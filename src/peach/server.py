"""无桌面服务入口：维护 Web 进程，消费配置重载与资料处理请求。"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading

from . import onboarding, settings_file


def launch(*arguments: str) -> subprocess.Popen:
    environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PEACH_HEADLESS_MANAGED="1")
    return subprocess.Popen(
        [sys.executable, "-m", "peach", *arguments],
        stdin=subprocess.DEVNULL, env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def initialize() -> None:
    config = settings_file.load_config()
    if config.present:
        return
    if config.directory("database").joinpath("ledger.db").exists():
        raise RuntimeError("已有数据库但缺少配置文件，请恢复 config.toml 后启动")
    process = launch("init", "--no-input")
    try:
        code = process.wait(timeout=120)
        if code:
            raise RuntimeError(f"初始化失败，退出码 {code}")
    finally:
        stop_process(process)


def supervise(host: str, port: int, stopping: threading.Event) -> int:
    service = scanner = None
    try:
        while not stopping.is_set():
            config = settings_file.load_config()
            reload_path = config.directory("state") / onboarding.RELOAD_NAME
            if service is None or reload_path.exists():
                stop_process(service)
                service = launch("serve", "--host", host, "--port", str(port),
                                 "--no-mdns", "--no-ledger-sync")
                reload_path.unlink(missing_ok=True)
            code = service.poll()
            if code is not None:
                return code or 1
            if scanner is not None and scanner.poll() is not None:
                if scanner.returncode:
                    print(f"资料处理失败，退出码 {scanner.returncode}", file=sys.stderr, flush=True)
                scanner = None
            if scanner is None:
                location = onboarding.take_first_scan_request(config)
                if location is not None:
                    scanner = launch("process", location)
            stopping.wait(1)
        return 0
    finally:
        stop_process(scanner)
        stop_process(service)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行通过浏览器管理的 Peach 服务")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8900)
    args = parser.parse_args(argv)
    stopping = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopping.set())
    initialize()
    return supervise(args.host, args.port, stopping)


if __name__ == "__main__":
    raise SystemExit(main())
