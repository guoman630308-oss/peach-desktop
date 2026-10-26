"""同一数据目录内，任务起跑与安装准备共用进程间闸门。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import socket
import uuid
import threading
import time
from weakref import WeakValueDictionary

from filelock import FileLock, Timeout

from .fsutil import atomic_write_text


class UpdateBusy(ValueError):
    """任务或安装事务占用数据目录，调用方应保留当前程序。"""


def state_for(db: Path) -> Path:
    parent = Path(db).resolve().parent
    data = parent.parent if parent.name == "database" else parent
    return data / "state" / "update-tasks"


_locks = WeakValueDictionary()
_locks_guard = threading.Lock()
current_db = ContextVar('peach_task_database', default=None)


def _mutex(root: Path) -> FileLock:
    root.mkdir(parents=True, exist_ok=True)
    with _locks_guard:
        lock = _locks.get(root)
        if lock is None:
            lock = FileLock(root / "admission.lock", timeout=10)
            _locks[root] = lock
        return lock


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise UpdateBusy("任务保护状态无法读取，请检查数据目录后重试。") from exc
    if not isinstance(value, dict):
        raise UpdateBusy("任务保护状态无效，请检查数据目录后重试。")
    return value


def _alive(data: dict) -> bool:
    from .jobs import process_alive
    pid = data.get("pid")
    return isinstance(pid, int) and process_alive(pid)


def _maintenance(root: Path) -> dict:
    path = root / "install.json"
    data = _read(path)
    if data and not _alive(data):
        path.unlink(missing_ok=True)
        return {}
    return data


@contextmanager
def admission(db: Path):
    """持锁完成任务登记；安装取得预约后不接受新任务。"""
    root = state_for(db)
    with _mutex(root):
        if _maintenance(root):
            raise UpdateBusy("正在准备安装更新，暂时不能启动新任务。请等待安装完成。")
        yield


def register(db: Path, label: str) -> Path:
    """登记线程任务或 CLI；文件在执行结束时撤下，死进程由预检回收。"""
    with admission(db):
        path = state_for(db) / "active" / (uuid.uuid4().hex + ".json")
        atomic_write_text(path, json.dumps({"pid": os.getpid(), "label": label}, ensure_ascii=False))
        return path


@contextmanager
def activity(db: Path, label: str):
    path = register(db, label)
    context = current_db.set(Path(db))
    try:
        yield
    finally:
        current_db.reset(context)
        with _mutex(path.parent.parent):
            path.unlink(missing_ok=True)


def reserve(db: Path, *, owner: int | None = None) -> str:
    """接受安装预约并通知所有服务保存进度、暂停任务。"""
    root = state_for(db)
    with _mutex(root):
        if _maintenance(root):
            raise UpdateBusy("已有更新安装正在进行，请等待完成。")
        token = uuid.uuid4().hex
        atomic_write_text(root / "install.json", json.dumps({
            "pid": owner if owner is not None else os.getpid(), "token": token}))
        from .job_recovery import request_pause
        request_pause(db)
        return token


def is_pausing(db: Path) -> bool:
    root = state_for(db)
    with _mutex(root):
        return bool(_maintenance(root))


def check_pause(db: Path | None) -> None:
    if db is not None and is_pausing(db):
        from .jobs import JobPaused
        raise JobPaused('任务已暂停，安装完成后自动继续。')


def _running(db: Path) -> list[str]:
    from .jobs import process_alive
    from .runtime_prepare import task_gate
    root = state_for(db)
    with _mutex(root):
        labels = []
        for path in (root / "active").glob("*.json"):
            data = _read(path)
            if _alive(data):
                labels.append(str(data.get("label") or "后台任务"))
            else:
                path.unlink(missing_ok=True)
        gate = task_gate(db, host=socket.gethostname(), alive=process_alive)
        if gate.error:
            labels.append('任务状态未取得')
        labels.extend(run.describe() for run in (*gate.blocking, *gate.resumable)
                      if run.status == "running" and (run.pid is None or process_alive(run.pid)))
        # 旧版 CLI 已有 PID 锁，预检同时兼容这些仍在运行的批次。
        legacy = [root.parent / name for name in ('.probe.lock', '.sheets.lock', '.creator-boards.lock')]
        for path in legacy:
            if not path.exists():
                continue
            try:
                pid = int(path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                raise UpdateBusy("批处理进程状态无法确认，安装已取消。") from None
            paused = _read(root / 'paused' / f'{pid}.json')
            if process_alive(pid) and not paused:
                labels.append(path.stem)
        library_lock = root.parent / 'library-processing.json.lock'
        try:
            with FileLock(library_lock, timeout=0):
                pass
        except Timeout:
            labels.append('馆藏资料与预览处理')
        return list(dict.fromkeys(labels))


def wait_quiescent(db: Path, timeout: float = 30) -> bool:
    """等在途步骤保存进度；期限到后由安装入口停止自己拥有的服务。"""
    deadline = time.monotonic() + timeout
    while _running(db):
        if time.monotonic() >= deadline:
            return False
        time.sleep(.1)
    return True


def wait_for_installation(db: Path) -> None:
    """源码批处理保留 PID 锁，在安装期间暂停，结束后由原进程接着执行。"""
    path = state_for(db) / 'paused' / f'{os.getpid()}.json'
    atomic_write_text(path, json.dumps({'pid': os.getpid()}))
    try:
        while is_pausing(db):
            time.sleep(.25)
    finally:
        path.unlink(missing_ok=True)


def transfer(db: Path, token: str, owner: int) -> None:
    """把安装预约交给更新助手或安装器，避免托盘退出产生空档。"""
    root = state_for(db)
    with _mutex(root):
        data = _read(root / "install.json")
        if not token or data.get("token") != token:
            raise UpdateBusy("更新安装预约已失效，请重新准备更新。")
        atomic_write_text(root / "install.json", json.dumps({"pid": owner, "token": token}))


def release(db: Path, token: str) -> None:
    root = state_for(db)
    with _mutex(root):
        if _read(root / "install.json").get("token") == token:
            (root / "install.json").unlink(missing_ok=True)


def release_owner(db: Path, owner: int) -> None:
    root = state_for(db)
    with _mutex(root):
        data = _read(root / 'install.json')
        if data.get('pid') == owner:
            release(db, data['token'])
