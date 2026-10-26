"""时间轴预览的采集任务：选一档密度、后台跑、页面轮询。

档位存在服务端而不是浏览器里：跑的是这台机器上的一条长任务，从另一台设备打开设置
时要看到的是「这台机器正在按 10 秒一帧采集」，不是那台设备自己的上一次选择。
"""
import json
import time
from pathlib import Path

from . import timeline_sheets
from .config import FFMPEG_DIR, STATE_DIR
from .ffmpeg import FFmpegResolver
from .fsutil import atomic_path
from .jobs import DiskGuard
from .media_processing_policy import metadata_only, save as save_processing_policy
from .platform import system_volume

#: 采集期间系统盘至少留这么多 GiB。数字和抽帧脚本的默认值同一个：真正会把系统盘写满的
#: 是网盘缓存那一类第三方消耗方，与产物落在哪块盘无关。
MIN_FREE_GB = 40.0

MODE_PATH = STATE_DIR / 'timeline-thumbnails.json'


def _mode_file(path: Path | None = None) -> Path:
    return Path(path) if path is not None else MODE_PATH


def read_mode(path: Path | None = None) -> str:
    """当前档位。读不出来就是关着——这条链会写盘也会读片子，不能靠猜来开。"""
    try:
        data = json.loads(_mode_file(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return timeline_sheets.OFF
    mode = str((data or {}).get('mode') or timeline_sheets.OFF)
    return mode if mode in timeline_sheets.MODES else timeline_sheets.OFF


def write_mode(mode: str, path: Path | None = None) -> None:
    with atomic_path(_mode_file(path)) as destination:
        destination.write_text(json.dumps({'mode': mode, 'saved_at': time.time()}),
                               encoding='utf-8')


def q_media_processing(contract, _args):
    return {'metadata_only': metadata_only(contract.follow_state_root)}


def w_media_processing(contract, body):
    value = (body or {}).get('metadata_only')
    save_processing_policy(contract.follow_state_root, value)
    if value:
        write_mode('off', Path(contract.follow_state_root) / 'timeline-thumbnails.json')
        contract.thumbnail_job.stop(resume=False)
    return q_media_processing(contract, {})


def q_timeline(contract, args):
    """一部片子的时间轴索引。没铺到就回空对象，页面据此决定要不要画这一层。

    单独一条而不是随列表下发：一页六十张卡里真正被悬停的是个位数，为此每行多读一份
    `meta.json` 是把六十次文件读摊到每一次翻页上。
    """
    asset_id = int((args or {}).get('id') or 0)
    if asset_id <= 0:
        return {}
    return timeline_sheets.read_meta(contract.timeline_root, asset_id) or {}


def q_thumbnail_jobs(contract, _args):
    state = contract.thumbnail_job.snapshot() or {}
    return {'mode': read_mode(), 'modes': list(timeline_sheets.MODES),
            'intervals': dict(timeline_sheets.INTERVALS),
            'status': state.get('status') or 'idle',
            'stage': state.get('stage') or '',
            'message': state.get('message') or '',
            'waiting_for_update': bool(state.get('waiting_for_update')),
            'job_id': state.get('job_id') or '',
            'total': state.get('total') or 0, 'done': state.get('done') or 0,
            'made': state.get('made') or 0, 'skipped': state.get('skipped') or 0,
            'failed': state.get('failed') or 0, 'stopped': state.get('stopped') or ''}


def _with_progress(fields: dict) -> dict:
    """任务中心（活动页）按 `checked` / `total` 画进度条，这条链自己的计数叫 `done`。"""
    return {**fields, 'checked': fields['done']} if 'done' in fields else fields


def w_thumbnail_jobs(contract, body):
    """选档位。选 `off` 只是停手，已经生成的图留在盘上——几 GB 的产物删不删是另一件
    事，走数据管理页那条清理链，不在切换开关时顺手做掉。"""
    mode = str((body or {}).get('mode') or timeline_sheets.OFF)
    if mode not in timeline_sheets.MODES:
        raise ValueError('采集密度无效')
    if mode != timeline_sheets.OFF and metadata_only(contract.follow_state_root):
        raise ValueError('视频预览生成已关闭，请先在媒体库设置中开启')
    write_mode(mode)
    if mode == timeline_sheets.OFF:
        contract.thumbnail_job.stop(resume=False)
        return q_thumbnail_jobs(contract, {})
    choice = FFmpegResolver(FFMPEG_DIR).ffmpeg()
    if choice is None:
        raise ValueError('未找到 ffmpeg，无法采集缩略图')
    interval = timeline_sheets.INTERVALS[mode]
    root = contract.timeline_root

    def work(job_id):
        guard = DiskGuard(system_volume(), MIN_FREE_GB)
        result = timeline_sheets.generate_library(
            contract.db_path, root, interval, ffmpeg=str(choice.path),
            active=lambda: ((contract.thumbnail_job.snapshot() or {}).get('job_id') == job_id
                            and not metadata_only(contract.follow_state_root)),
            report=lambda **fields: contract.thumbnail_job.update(job_id, **_with_progress(fields)),
            guard=guard, resume_state=contract.thumbnail_job.snapshot(),
            checkpoint=lambda **fields: contract.thumbnail_job.checkpoint(job_id, **fields))
        # 磁盘触线停下来的那一轮不能记成完成：队列后面还剩几千部，而页面上「完成」
        # 和「跑完了」是同一个意思。按停是用户自己做的，不算故障。
        stopped = str(result.get('stopped') or '')
        failed = bool(stopped) and stopped != timeline_sheets.STOPPED_BY_USER
        contract.thumbnail_job.update(
            job_id, **_with_progress(result), completed_at=time.time(),
            status='failed' if failed else 'complete',
            error=stopped if failed else '')

    contract.thumbnail_job.start(work, restart=True, recovery={'mode': mode},
                                 initial={'mode': mode, 'interval': interval,
                                          'stage': '采集缩略图', 'checked': 0,
                                          'total': 0, 'done': 0, 'made': 0,
                                          'skipped': 0, 'failed': 0, 'stopped': ''})
    return q_thumbnail_jobs(contract, {})
