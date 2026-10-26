"""整理端点：预览、执行、回滚上一批（ADR-0039）。

预览只读，执行与回滚是真实 ledger 写入，所以它们不进 `READ_ONLY_POST_ROUTES`——
只读端那份账本是复制来的，改了路径就是一处合不回去的分叉。

执行走后台任务：几千行改名在 115 挂载上是分钟级的事，HTTP 等不住。互斥、进度和
「这一批什么时候跑的、动了多少」都落在任务中心那张表里。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from . import organize
from .migrations import sqlite_backup
from .media_sources import SCANNABLE_LOCATIONS
from .organize_templates import PLACEHOLDERS, TemplateError, validate_template
from .platform import location_roots
from .scripting import verify_after_write
from .web_settings import organize_templates

#: 界面上能整理的来源。`online` 是追更抓来的东西，不在任何一个媒体根下面。
ORGANIZE_LOCATIONS = SCANNABLE_LOCATIONS


def _templates(contract, body) -> tuple[str, str, str]:
    """取出这一次要用的来源与两份模板；模板缺省时用账本里存着的那一份。"""
    location = str(body.get("location") or "").strip()
    if location not in ORGANIZE_LOCATIONS:
        raise ValueError(f"不认识的来源：{location or '(空)'}")
    stored = organize_templates(contract).get(location, {})
    file_template = body.get("file_template")
    dir_template = body.get("dir_template")
    file_template = stored.get("file", "") if file_template is None else str(file_template)
    dir_template = stored.get("dir", "") if dir_template is None else str(dir_template)
    return (location, validate_template(file_template),
            validate_template(dir_template, directory=True))


def q_organize(contract, _args=None) -> dict:
    """整理这一块的当前状态：任务快照、可选来源、占位符说明与上一批。"""
    # 任务快照摊在顶层：进度组件（`followJobProgress`）认的就是这个形状，
    # 多包一层 `job` 就要在前端再写一遍拆包。
    state = contract.organize_job.snapshot() or {"status": "idle"}
    latest = organize.latest_batch(contract.candidate_root)
    declared = location_roots()
    return {
        **{key: value for key, value in state.items() if key not in ('plan', 'batch_stamp')},
        "status": state.get("status", "idle"),
        "locations": [{"location": location, "roots": list(declared.get(location, ()))}
                      for location in ORGANIZE_LOCATIONS if declared.get(location)],
        "templates": organize_templates(contract),
        "presets": [dict(preset) for preset in organize.PRESETS],
        "placeholders": [{"key": key, "label": label} for key, label in PLACEHOLDERS.items()],
        "last_batch": latest.name if latest else "",
    }


def w_organize_preview(contract, body) -> dict:
    """出计划：写一份 CSV，回给界面会变的行（上限 200）与跳过统计。"""
    location, file_template, dir_template = _templates(contract, body)
    with contract.read_connection() as connection:
        plan = organize.build_plan(connection, location=location,
                                   file_template=file_template, dir_template=dir_template)
    path = organize.plan_path(contract.candidate_root, location)
    organize.write_plan(path, plan["rows"])
    changes = [row for row in plan["rows"] if row["action"] != "skip"]
    return {"ok": True, "location": location, "counts": plan["counts"],
            "reasons": plan["reasons"], "rows": changes[:200],
            "truncated": len(changes) > 200, "plan_csv": str(path)}


def _run_write(contract, job_id: str, work) -> dict:
    """备份、开一条自己的连接干活、事后自检。

    连接不走 `contract.write_transaction()`：那把写锁在整批期间会一直被握着，而任务
    中心的进度也要拿同一把锁，同一个线程里就是死锁。逐条提交让每个事务都很短，
    并发的普通写入照常进行。
    """
    backup = Path(contract.candidate_root) / "organize-backups" / (
        f"ledger.pre-organize-{time.strftime('%Y%m%d-%H%M%S')}-{job_id}.db")
    backup.parent.mkdir(parents=True, exist_ok=True)
    sqlite_backup(Path(contract.db_path), backup)
    connection = sqlite3.connect(str(contract.db_path), timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        result = work(connection)
        integrity, violations = verify_after_write(connection)
    finally:
        connection.close()
    contract.cache_bust()
    if integrity != "ok" or violations:
        raise RuntimeError(
            f"写入后 ledger 校验失败：integrity={integrity} foreign_keys={violations}")
    return {**result, "backup": str(backup)}


def w_organize_apply(contract, body) -> dict:
    """按确认过的模板执行整理。重跑一次计划，不信任界面手上那份。"""
    if body.get("confirm") is not True:
        raise ValueError("整理需要确认")
    location, file_template, dir_template = _templates(contract, body)
    if not file_template and not dir_template:
        raise ValueError("至少要给一个模板")
    if organize.unverified_location(location):
        raise ValueError(f"{location} 挂载上的改名无法核验云端结果，界面只出预览，不执行")
    job = contract.organize_job

    def work(job_id: str) -> None:
        batch_stamp = (job.snapshot() or {}).get('batch_stamp') or f"{time.strftime('%Y%m%d-%H%M%S')}-{job_id}"
        plan = (job.snapshot() or {}).get('plan')
        if plan is None:
            with contract.read_connection() as connection:
                plan = organize.build_plan(connection, location=location,
                                           file_template=file_template,
                                           dir_template=dir_template)
            if not job.checkpoint(job_id, plan=plan, batch_stamp=batch_stamp):
                raise InterruptedError('整理已停止')
        job.update(job_id, total=plan["counts"]["change"], stage="正在整理")
        result = _run_write(contract, job_id, lambda connection: organize.apply_plan(
            connection, plan["rows"], generated_root=contract.candidate_root,
            stamp=batch_stamp,
            progress=lambda **fields: job.checkpoint(job_id, **fields)))
        job.update(job_id, **result, counts=plan["counts"], status="complete",
                   completed_at=time.time())

    started = job.start(work, restart=True,
                     initial={"stage": "正在核对计划", "location": location},
                     recovery={'confirm': True, 'location': location, 'file_template': file_template,
                               'dir_template': dir_template, 'action': 'apply'})
    return {key: value for key, value in started.items() if key not in ('plan', 'batch_stamp')}


def w_organize_rollback(contract, body) -> dict:
    """把上一批整理原样退回去。"""
    if body.get("confirm") is not True:
        raise ValueError("回滚需要确认")
    from .job_recovery import current_job_id, read
    saved = read(contract.db_path, 'organize') if current_job_id.get() else {}
    batch = (saved.get('checkpoint') or {}).get('rollback_batch')
    latest = (organize.batch_log_dir(contract.candidate_root) / batch if batch
              else organize.latest_batch(contract.candidate_root))
    if latest is None:
        raise ValueError("没有可以回滚的批次")
    job = contract.organize_job

    def work(job_id: str) -> None:
        if not job.checkpoint(job_id, rollback_batch=latest.name):
            raise InterruptedError('整理回滚已停止')
        if not latest.exists():
            job.update(job_id, status='complete', restored=0, failed=0)
            return
        result = _run_write(contract, job_id, lambda connection: organize.rollback_batch(
            connection, latest, progress=lambda **fields: job.checkpoint(job_id, **fields)))
        job.update(job_id, **result, status="complete", completed_at=time.time())

    return job.start(work, restart=True,
                     initial={"stage": "正在回滚上一批", "batch": latest.name},
                     recovery={'confirm': True, 'action': 'rollback'})


__all__ = ["ORGANIZE_LOCATIONS", "TemplateError", "q_organize", "w_organize_apply",
           "w_organize_preview", "w_organize_rollback"]
