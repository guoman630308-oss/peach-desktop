from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.windows_update import replace_with_retry


def process_alive(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except OSError:
        return False
    return True


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")


def install_and_restart(
    *,
    wait_pid: int,
    staged: Path,
    target: Path,
    backup: Path,
    pending: Path,
    log: Path,
    alive: Callable[[int], bool] = process_alive,
    start: Callable[..., subprocess.Popen] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    for path in (staged, target, backup):
        if not path.is_file():
            append_log(log, f"replacement refused: missing file {path}")
            return 2
    if target.name.lower() != "peach.exe" or backup.parent != target.parent:
        append_log(log, "replacement refused: unexpected target or backup path")
        return 2

    deadline = time.monotonic() + 45.0
    while alive(wait_pid) and time.monotonic() < deadline:
        sleep(0.25)

    try:
        replace_with_retry(staged, target, sleep=sleep)
    except OSError as exc:
        append_log(log, f"replacement failed: {exc}")
        if not alive(wait_pid):
            start([str(target)], cwd=str(target.parent), shell=False)
        return 1

    environment = os.environ.copy()
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    launched = start(
        [str(target)], cwd=str(target.parent), shell=False, env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    sleep(3.0)
    if launched.poll() is None:
        pending.unlink(missing_ok=True)
        append_log(log, f"replacement succeeded: {target}")
        return 0

    append_log(log, f"new tray exited with code {launched.returncode}; rolling back")
    restore = target.with_name(".Peach.rollback.exe")
    shutil.copy2(backup, restore)
    try:
        replace_with_retry(restore, target, sleep=sleep)
    except OSError as exc:
        append_log(log, f"rollback failed: {exc}")
        return 1
    start(
        [str(target)], cwd=str(target.parent), shell=False, env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    append_log(log, f"rollback restored: {backup}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-pid", type=int, required=True)
    parser.add_argument("--staged", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--pending", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args(argv)
    return install_and_restart(
        wait_pid=args.wait_pid,
        staged=args.staged.resolve(),
        target=args.target.resolve(),
        backup=args.backup.resolve(),
        pending=args.pending.resolve(),
        log=args.log.resolve(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
