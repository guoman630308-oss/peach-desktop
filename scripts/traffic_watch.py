#!/usr/bin/env python3
r"""代理下载流量哨兵：按连接增量计量，只停止 Peach 所属任务树。"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import LOG_DIR


DEFAULT_PIPE = r"\\.\pipe\verge-mihomo"
KILL_PATTERNS = (
    "scripts\\probe.py",
    "scripts\\sheets.py",
    "scripts\\creator_boards.py",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Peach 代理流量停机哨兵")
    parser.add_argument("--limit", type=float, default=50.0)
    parser.add_argument("--warn", type=float)
    parser.add_argument(
        "--count-direct",
        action="store_true",
        help="把直连下载也计入停机预算；计费来源直连时不加这个开关等于没有闸门",
    )
    parser.add_argument("--interval", type=int, default=2)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--pipe", default=DEFAULT_PIPE)
    parser.add_argument("--secret", default=os.environ.get("PEACH_CLASH_SECRET", "set-your-secret"))
    parser.add_argument("--log-dir", type=Path, default=LOG_DIR)
    parser.add_argument("--alert", type=Path, default=LOG_DIR / "流量警报.txt")
    return parser


def clash(pipe: str, secret: str, path: str = "/connections") -> dict:
    request = (
        f"GET {path} HTTP/1.1\r\nHost: localhost\r\n"
        f"Authorization: Bearer {secret}\r\nAccept: application/json\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    with open(pipe, "r+b", buffering=0) as handle:
        handle.write(request)
        buffer = b""
        content_length = None
        header_end = -1
        chunked = False
        while True:
            try:
                chunk = handle.read(65536)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            if header_end < 0:
                header_end = buffer.find(b"\r\n\r\n")
                if header_end >= 0:
                    header = buffer[:header_end].lower()
                    match = re.search(rb"content-length:\s*(\d+)", header)
                    if match:
                        content_length = int(match.group(1))
                    chunked = b"transfer-encoding: chunked" in header
            if header_end >= 0:
                body_length = len(buffer) - header_end - 4
                if content_length is not None and body_length >= content_length:
                    break
                if content_length is None and buffer.endswith(b"\r\n0\r\n\r\n"):
                    break
    header, _, body = buffer.partition(b"\r\n\r\n")
    if chunked or b"chunked" in header.lower():
        decoded = b""
        remaining = body
        while remaining:
            line, _, remaining = remaining.partition(b"\r\n")
            try:
                size = int(line.strip().split(b";")[0], 16)
            except ValueError:
                break
            if size == 0:
                break
            decoded += remaining[:size]
            remaining = remaining[size + 2:]
        body = decoded
    elif content_length is not None:
        body = body[:content_length]
    return json.loads(body)


def is_direct(connection: dict) -> bool:
    chains = connection.get("chains") or []
    return len(chains) == 1 and chains[0] == "DIRECT"


def accumulate(
    previous: dict, current: dict, count_direct: bool
) -> tuple[float, float, dict[str, float]]:
    """把两次快照的差算成 (计入预算, 未计入, 各 host 计入)。

    连接会被回收，看不到的连接不能倒扣，所以只累加非负增量——这是下界不是精确值。
    计费来源如果走直连，`count_direct=False` 会让闸门永远不触发；口径要跟着来源走。
    """
    counted = uncounted = 0.0
    hosts: dict[str, float] = {}
    for connection_id, (downloaded, direct, host) in current.items():
        old = previous.get(connection_id, (0, direct, host))[0]
        delta = max(downloaded - old, 0)
        if direct and not count_direct:
            uncounted += delta
        else:
            counted += delta
            hosts[host] = hosts.get(host, 0) + delta
    return counted, uncounted, hosts


def snapshot(pipe: str, secret: str) -> tuple[int, dict]:
    payload = clash(pipe, secret)
    per_connection = {}
    for connection in payload.get("connections") or []:
        metadata = connection.get("metadata") or {}
        per_connection[connection.get("id")] = (
            connection.get("download", 0),
            is_direct(connection),
            metadata.get("host") or metadata.get("destinationIP") or "?",
        )
    return payload.get("downloadTotal", 0), per_connection


def owned_python_jobs() -> list[tuple[str, str]]:
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    jobs = []
    for line in result.stdout.splitlines():
        pid, separator, command = line.partition("|")
        normalized = command.lower().replace("/", "\\")
        if separator and pid.strip().isdigit() and any(pattern in normalized for pattern in KILL_PATTERNS):
            jobs.append((pid.strip(), command.strip()))
    return jobs


def stop_owned_jobs(log) -> list[str]:
    stopped = []
    try:
        for pid, command in owned_python_jobs():
            result = subprocess.run(
                ["taskkill", "/PID", pid, "/T", "/F"],
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0:
                stopped.append(f"{pid} {command[:80]}")
    except Exception as exc:
        log(f"停止任务失败: {exc}")
    return stopped


def run(args: argparse.Namespace, log) -> int:
    if args.limit <= 0 or args.interval < 1:
        raise SystemExit("limit 必须大于 0，interval 至少为 1 秒")
    warn_gb = args.warn if args.warn is not None else args.limit * 0.6
    try:
        total_start, previous = snapshot(args.pipe, args.secret)
    except Exception as exc:
        print("连不上 Clash 管道:", exc)
        return 1

    counted_bytes = 0.0
    uncounted_bytes = 0.0
    counted_hosts: dict[str, float] = {}
    log(f"哨兵启动。mihomo 累计（含直连）{total_start/1024**3:,.1f} GB，活跃连接 {len(previous)}")
    if args.count_direct:
        log("计量口径：代理 + 直连下载增量都计入；这是轮询下界")
    else:
        log("计量口径：只累计非 DIRECT 连接下载增量；这是轮询下界")
    log(f"阈值 {args.limit:.0f} GB，预警 {warn_gb:.0f} GB，每 {args.interval}s 检查")

    if args.once:
        aggregate: dict[str, int] = {}
        for downloaded, direct, host in previous.values():
            if not direct:
                aggregate[host] = aggregate.get(host, 0) + downloaded
        for host, value in sorted(aggregate.items(), key=lambda item: -item[1])[:12]:
            print(f"{value/1024**3:>8.3f} GB  {host}")
        return 0

    warned = False
    while True:
        time.sleep(args.interval)
        try:
            _total, current = snapshot(args.pipe, args.secret)
        except Exception as exc:
            log(f"读取失败（{exc}），跳过本轮")
            continue
        counted_delta, uncounted_delta, host_delta = accumulate(
            previous, current, args.count_direct
        )
        counted_bytes += counted_delta
        uncounted_bytes += uncounted_delta
        for host, value in host_delta.items():
            counted_hosts[host] = counted_hosts.get(host, 0) + value
        previous = current
        used_gb = counted_bytes / 1024**3
        if used_gb >= args.limit:
            log(f"增量 {used_gb:.1f} GB 超过阈值 {args.limit:.0f} GB——停止所属任务")
            stopped = stop_owned_jobs(log)
            top = sorted(counted_hosts.items(), key=lambda item: -item[1])[:5]
            scope = "代理+直连" if args.count_direct else "代理"
            lines = [
                f"流量警报 {time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"{scope}下载增量 {used_gb:.1f} GB（阈值 {args.limit:.0f} GB）",
                f"同期未计入 {uncounted_bytes/1024**3:.1f} GB",
                "", "计入目标 Top:",
                *[f"  {value/1024**3:>8.3f} GB  {host}" for host, value in top],
                "", "已停止的 Peach 任务:",
                *([f"  {item}" for item in stopped] or ["  （无匹配任务）"]),
            ]
            args.alert.parent.mkdir(parents=True, exist_ok=True)
            args.alert.write_text("\n".join(lines) + "\n", encoding="utf-8")
            for line in lines:
                log(line)
            return 10
        if used_gb >= warn_gb and not warned:
            warned = True
            log(f"增量已达 {used_gb:.1f} GB，接近停机线")
        scope = "代理+直连" if args.count_direct else "代理"
        log(f"{scope} {used_gb:>7.2f}/{args.limit:.0f} GB"
            f"（未计入 {uncounted_bytes/1024**3:>7.2f} GB）连接 {len(current)}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.log_dir / f"trafficwatch-{time.strftime('%Y%m%d-%H%M%S')}.log"
    with log_path.open("a", encoding="utf-8", buffering=1) as log_file:
        def log(message: str) -> None:
            line = f"[{time.strftime('%H:%M:%S')}] {message}"
            print(line, flush=True)
            log_file.write(line + "\n")

        return run(args, log)


if __name__ == "__main__":
    raise SystemExit(main())
