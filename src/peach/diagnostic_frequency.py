"""跨进程聚合已筛选的技术诊断；不读取日志或账本。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from filelock import FileLock

from .fsutil import atomic_write_text

INTERVAL = 3600
DAILY_LIMIT = 20
RETENTION_DAYS = 7


def _now():
    return datetime.now(timezone.utc)


def _path(config):
    return config.directory("state") / "remote-reporting-frequency.json"


def _load(path, revision, now):
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        state = {}
    if not isinstance(state, dict) or state.get("revision") != revision:
        state = {"revision": revision, "records": {}}
    day = now.date().isoformat()
    if state.get("day") != day:
        state.update(day=day, batches=0)
    cutoff = (now.date() - timedelta(days=RETENTION_DAYS - 1)).isoformat()
    records = state.get("records", {})
    if not isinstance(records, dict):
        records = {}
    state["records"] = {key: row for key, row in records.items()
        if isinstance(key, str) and len(key) == 75 and isinstance(row, dict)
        and isinstance(row.get("day"), str) and cutoff <= row["day"] <= day
        and isinstance(row.get("event"), dict)
        and type(row.get("total")) is int and type(row.get("submitted")) is int
        and 0 <= row["submitted"] <= row["total"]
        and type(row.get("last_sent")) in (int, float)}
    if type(state.get("batches")) is not int or state["batches"] < 0:
        state["batches"] = 0
    return state


def _emit(state, row, key, now, emit, *, force=False, event_id=None):
    pending = row["total"] - row["submitted"]
    if pending <= 0 or state["batches"] >= DAILY_LIMIT:
        return
    if not force and row["submitted"] and now.timestamp() - row["last_sent"] < INTERVAL:
        return
    event = {**row["event"], "tags": {**row["event"].get("tags", {}),
        "diagnostic_key": key.split(":", 1)[1], "occurrence_day": row["day"],
        "occurrence_count": str(pending)}}
    if event_id:
        event["event_id"] = event_id
    try:
        accepted = emit(event)
    except Exception:
        accepted = False
    if accepted:
        row.update(submitted=row["total"], last_sent=now.timestamp())
        state["batches"] += 1


def record(config, revision, event, emit):
    """记录一次筛选后的故障；首次立即提交，重复故障合并提交增量次数。"""
    path = _path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=.1):
        now = _now()
        state = _load(path, revision, now)
        sample = {key: value for key, value in event.items() if key != "event_id"}
        fingerprint = hashlib.sha256(json.dumps(sample, sort_keys=True).encode()).hexdigest()
        day = now.date().isoformat()
        key = day + ":" + fingerprint
        records = state["records"]
        if key not in records:
            if sum(row["day"] == day for row in records.values()) >= DAILY_LIMIT:
                return
            records[key] = {"day": day, "event": sample, "total": 0, "submitted": 0, "last_sent": 0}
        row = records[key]
        row["total"] += 1
        _emit(state, row, key, now, emit, event_id=event.get("event_id") if row["total"] == 1 else None)
        atomic_write_text(path, json.dumps(state))


def flush(config, revision, emit, *, force=False):
    """按发生日期提交未提交次数，退出时可提前提交，仍遵守每日批次上限。"""
    path = _path(config)
    if not path.exists():
        return
    with FileLock(str(path) + ".lock", timeout=.1):
        now = _now()
        state = _load(path, revision, now)
        for key, row in state["records"].items():
            _emit(state, row, key, now, emit, force=force)
        atomic_write_text(path, json.dumps(state))


def clear(config):
    path = _path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=.1):
        path.unlink(missing_ok=True)
