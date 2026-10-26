"""统一处理任务的启动、只读状态查询与失败项重试。"""
import json
import os
from pathlib import Path, PureWindowsPath

from . import settings_file
from .library_processing import (ALL_STAGES, COLLECT_STAGE, SCAN_STAGE, STAGES,
                                METADATA_STAGE, PREVIEWS_STAGE, _asset_selection,
                                decorate, issues_path, process_library, snapshot)
from .processing_control import control_for, initial_estimate, valid_mode, mode_ratio

ACTIVE = ('running', 'pausing', 'paused')


def _import_path(config, raw):
    """将本机选中的文件映射到已配置的媒体根，拒绝越界与未完成的下载。"""
    from .push_discovery import IGNORED_NAMES, ignored
    from .scan import medium_of
    if (not isinstance(raw, str) or not raw.strip() or len(raw) > 32767
            or any(ord(char) < 32 for char in raw) or not Path(raw).is_absolute()):
        raise ValueError('请选择视频文件的完整路径')
    try:
        target = Path(raw).resolve(strict=True)
    except OSError as error:
        raise ValueError('视频文件不存在或无法读取') from error
    if not target.is_file() or medium_of(target.name) != 'video':
        raise ValueError('请选择一个视频文件')
    if ignored(target.name) or any(part.casefold() in IGNORED_NAMES | {'$recycle.bin', 'system volume information', '#recycle'}
                                   for part in target.parts):
        raise ValueError('请选择已下载完成且不在系统目录中的视频')
    matches = []
    for location, declared in config.locations.items():
        mounted = config.mounts.get(location, declared if os.name == 'nt' else ())
        for index, root in enumerate(mounted):
            if index >= len(declared):
                continue
            try:
                local_root = Path(root).resolve(strict=True)
            except OSError:
                continue
            if target.is_relative_to(local_root):
                path = str(PureWindowsPath(declared[index]).joinpath(*target.relative_to(local_root).parts))
                matches.append((len(local_root.parts), location, path))
    if not matches:
        raise ValueError('此文件不在媒体目录内，请先在 Peach 配置中添加它所在的文件夹')
    _, location, path = max(matches)
    return location, path


def w_library_import_file(contract, body):
    """登记所选视频，然后用同一处理任务只补全这一条资产。"""
    from .scan import ingest_path
    config = settings_file.active()
    _processing_request(contract, config, _current(contract), {'stage': COLLECT_STAGE})
    if _current(contract).get('status') in ACTIVE:
        raise ValueError('请等待当前扫描与采集任务完成，或先停止该任务')
    mode = valid_mode(body.get('speed_mode') or control_for(config).mode)
    location, path = _import_path(config, body.get('path'))
    result = ingest_path(contract.db_path, location, path,
                         declared_roots=config.locations, mounts=config.mounts)
    if not result.found or result.sidecar or result.asset_id is None:
        raise ValueError('视频文件不可访问，请检查文件后重试')
    contract.cache_bust()
    return w_library_processing(contract, {'stage': COLLECT_STAGE, 'asset_ids': [result.asset_id], 'speed_mode': mode})


def _current(contract):
    live = contract.library_processing_job.snapshot()
    return live if live and live.get('status') in ACTIVE else snapshot(settings_file.active())


def q_library_processing(contract, _args):
    state = decorate(_current(contract))
    control = control_for(settings_file.active())
    control.refresh(force=True)
    state['speed_mode'] = control.mode
    from .media_processing_policy import metadata_only
    state['metadata_only'] = metadata_only(settings_file.active().directory('state'))
    from .job_recovery import read
    recovery = read(contract.db_path, 'library-processing')
    if state.get('status') not in ACTIVE and recovery.get('status') == 'blocked':
        state.update(status='failed', error=f"任务未能自动恢复：{recovery.get('error') or '请重新发起任务'}")
    # 上一趟任务的状态文件里没有这个字段，而它那份完整清单还在磁盘上：按 job_id
    # 推出地址补回去，界面才有得可给。
    if state.get('issue_count') and state.get('job_id') and not state.get('issues_log'):
        path = issues_path(settings_file.active(), str(state['job_id']))
        if path.is_file():
            state['issues_log'] = str(path)
    return state


def _control_action(contract, config, body):
    action = body['action']
    if action not in ('pause', 'resume', 'set_speed'):
        raise ValueError('处理动作无效')
    previous = _current(contract)
    job_id = body.get('job_id')
    if action != 'set_speed' and not job_id:
        raise ValueError('请选择正在处理的任务')
    if job_id and str(job_id) != str(previous.get('job_id') or ''):
        raise ValueError('任务已变化，请刷新页面后重试')
    if action != 'set_speed' and previous.get('status') not in ACTIVE:
        raise ValueError('当前没有可暂停或继续的任务')
    control = control_for(config)
    control.refresh(force=True)
    if job_id:
        control.select_job(str(job_id))
    if action == 'set_speed':
        mode = valid_mode(body.get('speed_mode'))
        control.request(action, mode=mode)
        if previous.get('status') in ACTIVE:
            ratio = mode_ratio(mode, previous.get('speed_mode') or 'standard')
            fields = dict(speed_mode=mode, eta_initial=True)
            if previous.get('speed_mode') != mode:
                for key in ('eta_seconds_min', 'eta_seconds_max'):
                    if isinstance(previous.get(key), (int, float)):
                        fields[key] = max(0, int(previous[key] * ratio))
            contract.library_processing_job.update(previous['job_id'], **fields)
        return q_library_processing(contract, {})
    status = ('paused' if previous.get('status') == 'paused' else 'pausing') if action == 'pause' else 'running'
    _announce_control(contract.library_processing_job, str(job_id), status)
    control.request(action, str(job_id))
    live = contract.library_processing_job.snapshot()
    thread = getattr(contract.library_processing_job, 'thread', None)
    if action == 'resume' and not (live and thread is not None and thread.is_alive()) and not _writer_active(config):
        from .job_recovery import read, current_job_id, current_run
        recovery = read(contract.db_path, 'library-processing')
        identity = current_job_id.set(str(job_id))
        run = current_run.set(recovery.get('run_id'))
        try:
            return w_library_processing(contract, {
                'stage': previous.get('requested_stage') or ALL_STAGES,
                **({'asset_ids': previous['asset_ids']} if previous.get('asset_ids') else {}),
                'speed_mode': control.mode}, resume=True)
        finally:
            current_job_id.reset(identity)
            current_run.reset(run)
    result = q_library_processing(contract, {})
    if not live and result.get('status') in ACTIVE:
        result['status'] = status
    return result


def _announce_control(job, job_id, status):
    with job.editing(job_id) as state:
        if state is not None and state.get('status') in ACTIVE:
            state['status'] = status


def _writer_active(config):
    from filelock import FileLock, Timeout
    from .library_processing import state_path
    try:
        with FileLock(str(state_path(config)) + '.lock', timeout=0):
            return False
    except Timeout:
        return True


def q_library_processing_issues(contract, args):
    state = _current(contract)
    job_id = str(args.get('job_id') or '')
    if not job_id or job_id != str(state.get('job_id') or ''):
        raise ValueError('任务已变化，请刷新页面后重试')
    rows = []
    path = issues_path(settings_file.active(), job_id)
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    offset = max(0, int(args.get('offset') or 0))
    limit = max(1, min(200, int(args.get('limit') or 50)))
    return {'job_id': job_id, 'total': len(rows), 'offset': offset,
            'rows': rows[offset:offset + limit]}


def _retry_ids(previous, body):
    """请求带 `retry` 时按上一任务的失败集合重试；普通启动仍处理整个馆藏。

    返回 `None` 表示全量任务；返回列表表示只处理这些资产。空列表由调用方
    按「没有可重试项」处理，不允许滑回全库重跑。
    """
    if not body or 'retry' not in body:
        return None
    job_id = str(body.get('job_id') or '')
    if not job_id or job_id != str(previous.get('job_id') or ''):
        raise ValueError('原任务已变化，请刷新页面后重试')
    requested = body.get('retry')
    if not isinstance(requested, list):
        raise ValueError('重试项目格式无效')
    allowed = list(previous.get('retryable_asset_ids') or [])
    if not requested:
        return allowed
    chosen = []
    for value in requested:
        if not isinstance(value, int) or value not in allowed:
            raise ValueError('重试项目不属于原任务的失败集合')
        chosen.append(value)
    return chosen


def _processing_request(contract, config, previous, body):
    """校验馆藏、阶段与互斥的处理范围。"""
    if not config.configured or not config.locations:
        raise ValueError('请先添加媒体目录')
    if contract.db_path.resolve() != (config.directory('database') / 'ledger.db').resolve():
        raise ValueError('当前馆藏与媒体配置不一致')
    retry_ids = _retry_ids(previous, body)
    if 'asset_ids' in (body or {}) and body['asset_ids'] is None:
        raise ValueError('请选择有效的视频项目')
    asset_ids = _asset_selection(contract.db_path, (body or {}).get('asset_ids'))
    if asset_ids is not None and retry_ids is not None:
        raise ValueError('选择项目与失败重试不能同时使用')
    stage = str((body or {}).get('stage') or
                (previous.get('requested_stage') if retry_ids is not None else None) or ALL_STAGES)
    if stage not in STAGES:
        raise ValueError('处理阶段无效')
    from .media_processing_policy import metadata_only
    if stage == PREVIEWS_STAGE and metadata_only(config.directory('state')):
        raise ValueError('视频预览生成已关闭，请先在媒体库设置中开启')
    return retry_ids, asset_ids, stage


def w_library_processing(contract, body, *, resume=False):
    config = settings_file.active()
    if (body or {}).get('action'):
        return _control_action(contract, config, body)
    previous = _current(contract)
    if previous.get('status') in ACTIVE and not resume:
        return previous
    retry_ids, asset_ids, stage = _processing_request(contract, config, previous, body)
    if retry_ids is not None and not retry_ids:
        return previous
    mode = valid_mode(control_for(config).mode if resume else
                      (body or {}).get('speed_mode') or control_for(config).mode)
    control_for(config).request('set_speed', mode=mode)
    def work(job_id):
        try:
            result = process_library(config, contract.db_path, contract.candidate_root, contract.cover_root,
                job_id=job_id, retry_ids=retry_ids, stage=stage, asset_ids=asset_ids,
                # 自动落库与页面写的是同一个 `LedgerDatabase`：进程内写锁在实例上，
                # 另建一个就是在同一个进程里开第二条写入路径。
                database=contract.database,
                resume_state=previous if resume else None,
                speed_mode=control_for(config).mode,
                active=lambda: (contract.library_processing_job.snapshot() or {}).get('job_id') == job_id,
                report=lambda state: contract.library_processing_job.update(job_id, **{
                    key: value for key, value in state.items() if key != 'job_id'}))
            contract.library_processing_job.update(job_id, **{key: value for key, value in result.items() if key != 'job_id'})
        finally:
            contract.cache_bust()
    # 起步那行字按这一趟真正要做的事写：点了「只扫描」却看到「读取本地资料」，
    # 人会以为点错了。
    labels = {ALL_STAGES: '准备处理', SCAN_STAGE: '准备扫描文件', COLLECT_STAGE: '准备采集资料',
              METADATA_STAGE: '准备采集资料', PREVIEWS_STAGE: '准备生成预览图'}
    return contract.library_processing_job.start(
        work, restart=True, initial={'stage': labels[stage], 'requested_stage': stage,
                                    **initial_estimate(config, contract.db_path, stage, asset_ids or retry_ids, mode)},
        recovery={'stage': stage, 'speed_mode': mode, **({'asset_ids': asset_ids or retry_ids}
                                    if asset_ids is not None or retry_ids is not None else {})})
