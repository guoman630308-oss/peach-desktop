"""重启 Windows 托盘的命令行入口，实现在 `peach.windows_restart`。

    .venv\\Scripts\\python.exe scripts\\restart_windows_tray.py

重启前先过任务闸门：本机有重启后续跑不了的任务就拒绝，`--force` 越过。旧托盘退出后、
新托盘启动前按 `uv.lock` 同步项目 venv；迁移与就绪检查由新托盘启动时执行，结果从
`<Peach 数据目录>/state/runtime-prepare.json` 读回来一起报告（ADR-0091）。stdout 只打一行 JSON。

这个进程只能加载标准库与本包的纯 Python 模块：它要在活着的时候替换项目 venv 里的包，
而 Windows 上被加载的 `.pyd` 换不掉。

换生产二进制走 `scripts/deploy_windows_tray.py`，不要单独用这里的 `--swap-from`。
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import DATABASE_PATH, STATE_DIR
from peach.jobs import process_alive
from peach.runtime_prepare import Dependencies, RestartPreparation, read_record, task_gate
from peach.windows_restart import (
    DEFAULT_TIMEOUT, find_tray_windows, restart_source_tray, restart_tray, venv_holders,
)


def preparation() -> RestartPreparation:
    return RestartPreparation(
        Dependencies(PROJECT_ROOT, frozen=False),
        holders=lambda tray_pid: venv_holders(PROJECT_ROOT / ".venv", (tray_pid,)),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="静默无窗口地重启 Windows Peach 托盘及子服务")
    parser.add_argument("--target", type=Path,
                        default=PROJECT_ROOT / "dist" / "Peach" / "Peach.exe")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--source", action="store_true",
                        help="重启源码部署的托盘（pythonw -m peach.tray），不做换包")
    parser.add_argument("--swap-from", type=Path, default=None,
                        help="旧托盘退出后把这个暂存包换上生产入口，失败自动回退")
    parser.add_argument("--force", action="store_true",
                        help="有重启后续跑不了的任务也照样重启")
    args = parser.parse_args(argv)
    # 源码托盘没有生产入口可换，`--swap-from` 在这条路上无处可去。默默忽略它的话，
    # 命令照样退出 0、照样打印 ok，而那个包根本没换上——换包失败最不能是静默的。
    if args.source and args.swap_from:
        parser.error("--source 不能与 --swap-from 同用：源码托盘没有可换的生产入口")

    gate = task_gate(DATABASE_PATH, host=socket.gethostname(), alive=process_alive)
    if gate.refused and not args.force:
        print(json.dumps({"ok": False, "message": gate.refusal(), "tasks": gate.as_dict()},
                         ensure_ascii=False))
        return 1

    prepare = preparation()
    timeout = max(1.0, args.timeout)
    if not args.source and find_tray_windows(args.target):
        result = restart_tray(args.target, timeout=timeout, swap_from=args.swap_from,
                              prepare=prepare)
    else:
        result = restart_source_tray(timeout=timeout, prepare=prepare)
    payload = asdict(result)
    payload["tasks"] = gate.as_dict()
    if result.new_tray_pid is not None:
        startup = read_record(STATE_DIR, pid=result.new_tray_pid)
        payload["startup"] = startup
        if startup is None:
            payload["message"] += "；未取得新托盘的启动准备记录"
        elif not startup.get("ok"):
            failed = "；".join(step["message"] for step in startup.get("steps", ())
                              if step.get("state") in ("failed", "stale"))
            payload["ok"] = False
            payload["message"] += f"；新托盘启动准备失败：{failed}"
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
