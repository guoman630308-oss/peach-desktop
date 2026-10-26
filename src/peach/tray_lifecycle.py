"""Windows 托盘退出取证与进程恢复；监视器只加载标准库。"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

from .fsutil import atomic_write_text
from .tray_detached import launch_detached

WM_STOP = 0x0400 + 10
WM_ENDSESSION = 0x0016
HISTORY_ENV = "PEACH_TRAY_RECOVERIES"
ACK_ENV = "PEACH_TRAY_RECOVERY_ACK"
RECOVERY_WINDOW = 300
RECOVERY_DELAYS = (2, 5, 10)


def remove_records(paths) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logging.getLogger(__name__).warning("托盘监视记录清理失败：%s", path, exc_info=True)


def audit(log: Path, event: str, session: str, **fields) -> None:
    record = dict(at=datetime.now(timezone.utc).isoformat(), event=event,
                  session=session, observer_pid=os.getpid(), **fields)
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logging.getLogger(__name__).exception("托盘退出记录写入失败")


def recovery_history(value: str, now: float) -> list[float]:
    try:
        values = json.loads(value)
        if not isinstance(values, list):
            return []
        return [float(stamp) for stamp in values[-10:]
                if type(stamp) in (float, int) and 0 <= now - stamp < RECOVERY_WINDOW]
    except (TypeError, ValueError):
        return []


class ProcessHandle:
    """持有内核进程对象，避免轮询 PID 时认到复用该号码的其他进程。"""

    def __init__(self, pid: int):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.OpenProcess(0x100000 | 0x1000, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        times = [wintypes.FILETIME() for _ in range(4)]
        if not self.kernel.GetProcessTimes(self.handle, *(ctypes.byref(t) for t in times)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())
        self.created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    def wait(self, milliseconds: int) -> int | None:
        status = self.kernel.WaitForSingleObject(self.handle, milliseconds)
        if status == 258:
            return None
        if status != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def watch(path: Path, *, open_process=ProcessHandle, popen=None,
          sleep=time.sleep, now=time.time) -> int:
    """单个实例的监视器；有退出意图就交还控制权，异常退出最多恢复三次。"""
    record = json.loads(path.read_text(encoding="utf-8"))
    log, session = Path(record["log"]), record["session"]
    ready, stop = path.with_suffix(".ready"), path.with_suffix(".stop")
    process = None
    try:
        process = open_process(record["pid"])
        if process.created != record["created"]:
            raise RuntimeError("托盘进程身份不一致")
        audit(log, "watchdog_ready", session, tray_pid=record["pid"])
        atomic_write_text(ready, str(os.getpid()))
        while True:
            if stop.exists():
                audit(log, "watchdog_disarmed", session, tray_pid=record["pid"])
                return 0
            code = process.wait(250)
            if code is not None:
                break
        if stop.exists():
            return 0
        audit(log, "unexpected_exit", session, tray_pid=record["pid"], exit_code=code,
              actor="unknown")
        history = recovery_history(record.get("history", "[]"), now())
        ack = path.with_suffix(".recovered")
        while len(history) < len(RECOVERY_DELAYS):
            current = Path(record["current"])
            if current.read_text(encoding="utf-8") != session:
                audit(log, "recovery_superseded", session)
                return 0
            delay = RECOVERY_DELAYS[len(history)]
            audit(log, "recovery_scheduled", session, delay_seconds=delay, attempt=len(history) + 1)
            sleep(delay)
            if current.read_text(encoding="utf-8") != session:
                audit(log, "recovery_superseded", session)
                return 0
            history.append(now())
            environment = os.environ.copy()
            environment["PEACH_DATA_ROOT"] = record["data_root"]
            environment[HISTORY_ENV] = json.dumps(history)
            environment[ACK_ENV] = str(ack)
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            try:
                if popen is None:
                    child = launch_detached(record["argv"], cwd=record["cwd"], env=environment)
                else:
                    child = popen(record["argv"], cwd=record["cwd"], env=environment,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                                  | getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as exc:
                audit(log, "recovery_start_failed", session, error=str(exc))
                continue
            audit(log, "recovery_started", session, launcher_pid=child.pid)
            # 新托盘自己的监视器就绪才交接；导入失败也进入有限重试。
            for _ in range(240):
                if ack.exists():
                    audit(log, "recovery_handoff", session, tray_pid=int(ack.read_text()))
                    return 0
                code = child.poll()
                if code is not None:
                    audit(log, "recovery_launcher_exit", session, exit_code=code)
                    if code == 0:
                        return 0  # 单实例锁可能已经由用户手动启动的托盘持有。
                    break
                sleep(0.25)
            else:
                audit(log, "recovery_handoff_timeout", session, launcher_pid=child.pid)
                return 1
        audit(log, "recovery_limit", session, attempts=len(history), window_seconds=RECOVERY_WINDOW)
        return 1
    except Exception as exc:
        audit(log, "watchdog_error", session, error=str(exc))
        return 1
    finally:
        if process is not None:
            process.close()
        remove_records((ready, stop, path.with_suffix(".recovered"), path))


class Lifecycle:
    """托盘内的退出意图与监视器握手。仅由取得单实例锁的托盘创建。"""

    def __init__(self, state: Path, log: Path, data_root: Path, root: Path):
        self.session = uuid.uuid4().hex
        self.path = state / "tray-watchdogs" / (self.session + ".json")
        self.log = log / "tray-lifecycle.log"
        self.data_root, self.root = data_root, root
        self.reason: str | None = None
        self.guard = None

    def start(self) -> None:
        process = ProcessHandle(os.getpid())
        try:
            frozen = getattr(sys, "frozen", False)
            if frozen:
                argv = [sys.executable, "--silent"]
                helper = [sys.executable, "--tray-watchdog", str(self.path)]
                self.root = Path(sys.executable).parent
            else:
                # 基础解释器只加载标准库，依赖同步期间不占项目 venv 的启动器或扩展。
                pythonw = str(Path(sys.executable).with_name("pythonw.exe"))
                base = str(Path(sys._base_executable).with_name("pythonw.exe"))
                argv = [pythonw, "-m", "peach.tray", "--silent"]
                helper = [base, "-m", "peach.tray_lifecycle", str(self.path)]
            record = dict(session=self.session, pid=os.getpid(), created=process.created,
                          log=str(self.log), data_root=str(self.data_root), cwd=str(self.root),
                          argv=argv, history=os.environ.get(HISTORY_ENV, "[]"))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            current = self.path.parent / "current"
            record["current"] = str(current)
            atomic_write_text(current, self.session)
            atomic_write_text(self.path, json.dumps(record))
            audit(self.log, "started", self.session, tray_pid=os.getpid(), parent_pid=os.getppid(),
                  executable=sys.executable, project=str(self.root))
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(self.root / "src")
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            self.guard = launch_detached(helper, cwd=self.root, env=environment)
            deadline = time.monotonic() + 10
            while not self.path.with_suffix(".ready").exists():
                if self.guard.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("托盘监视器未能就绪")
                time.sleep(0.05)
            ack = os.environ.pop(ACK_ENV, None)
            if ack:
                atomic_write_text(Path(ack), str(os.getpid()))
        finally:
            process.close()

    def stop(self, reason: str, actor_pid: int | None = None) -> None:
        if self.reason is not None:
            return
        self.reason = reason
        try:
            audit(self.log, "stop_requested", self.session, tray_pid=os.getpid(),
                  reason=reason, actor_pid=actor_pid)
            atomic_write_text(self.path.with_suffix(".stop"), reason)
        except OSError:
            logging.getLogger(__name__).exception("托盘退出意图写入失败，正在停止本实例监视器")
        finally:
            if self.guard is not None:
                self._disarm()

    def _disarm(self) -> None:
        if self.guard is not None:
            try:
                self.guard.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # 这是本实例创建的监视器；它不能越过明确退出意图继续恢复。
                self.guard.terminate()
                self.guard.wait(timeout=5)
        remove_records((self.path.with_suffix(".ready"), self.path.with_suffix(".stop"), self.path))

    def exited(self, code: int) -> None:
        audit(self.log, "exited", self.session, tray_pid=os.getpid(), exit_code=code,
              reason=self.reason or "unexpected")

    def attach(self, icon) -> None:
        handlers = icon._message_handlers
        original = handlers[WM_STOP]

        def stopped(sender, reason):
            self.stop("external-stop" if sender else "window-stop", int(sender) or None)
            return original(sender, reason)

        def end_session(ending, flags):
            if ending:
                self.stop("session-end")
            return 0

        handlers[WM_STOP] = stopped
        handlers[WM_ENDSESSION] = end_session


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="监视一个 Windows Peach 托盘实例")
    parser.add_argument("record", type=Path)
    return watch(parser.parse_args(argv).record)


if __name__ == "__main__":
    raise SystemExit(main())
