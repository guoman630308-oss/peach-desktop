#!/usr/bin/env python3
"""全库 ffprobe：可续跑、单写者、显式计费授权。

新文件入库时已经探过一次（`peach.media_probe`），这里补的是历史空缺、失败重探和点名
重探，以及入库时不碰的计费来源。
"""
from __future__ import annotations

import argparse
import queue
import sqlite3
import threading
import time
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import DATABASE_PATH, FFMPEG_DIR, LOG_DIR, STATE_DIR
from peach.ffmpeg import FFmpegResolver
from peach.jobs import (
    ACTIVE_ASSET_SQL,
    DiskGuard,
    DiskSpaceDenied,
    JobPolicyError,
    SourceAccessPolicy,
    require_free_space,
    job_main,
)
from peach.platform import system_volume
from peach.media import resolve_case_insensitive
from peach.media_probe import UPDATE, context_fields, probe_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="补全视频事实层和情境层")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--location")
    parser.add_argument("--interval", type=float, default=0.15)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-free", type=float, default=40.0)
    parser.add_argument(
        "--disk-check-secs",
        type=float,
        default=20.0,
        help="运行期复查磁盘余量的间隔；0 表示每条都查",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--redo",
        choices=("none", "zero", "failed", "all"),
        default="none",
        help="额外重探已记录但无效的时长：zero=0（软失败）、failed=-1（硬失败）、all=两者",
    )
    parser.add_argument(
        "--asset",
        type=int,
        action="append",
        help="按 id 重探指定资产，绕过时长筛选。用于账本时长与真实文件不符的个案："
             "这类值看着正常，--redo 的 0/-1 判据够不着",
    )
    parser.add_argument("--allow-metered", action="store_true")
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--log-dir", type=Path, default=LOG_DIR)
    parser.add_argument("--lock", type=Path, default=STATE_DIR / ".probe.lock")
    return parser


def duration_selection(redo: str) -> str:
    """未探测和"探测过但没拿到时长"是两种状态；后者必须显式要求才重跑。

    软失败历史上被写成 duration=0，既不在 `duration IS NULL` 的待办里，也过不了
    抽帧的 duration>2 门槛，于是永远卡住。硬失败写成 -1。
    """
    return {
        "none": "duration IS NULL",
        "zero": "(duration IS NULL OR duration=0)",
        "failed": "(duration IS NULL OR duration<0)",
        "all": "(duration IS NULL OR duration<=0)",
    }[redo]


def run(args: argparse.Namespace) -> int:
    if args.workers < 1 or args.interval < 0 or args.limit < 0:
        raise SystemExit("workers 必须大于 0；interval/limit 不能为负数")
    if args.timeout <= 0:
        raise SystemExit("timeout 必须大于 0")
    choice = FFmpegResolver(FFMPEG_DIR).ffprobe()
    if choice is None:
        raise RuntimeError("ffprobe unavailable")
    require_free_space(system_volume(), args.min_free)
    source_sql, source_parameters = SourceAccessPolicy().sql_filter(
        args.location, args.allow_metered
    )

    args.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.log_dir / f"probe-{time.strftime('%Y%m%d-%H%M%S')}.log"
    lock = threading.RLock()
    with log_path.open("w", encoding="utf-8", buffering=1) as log_file:
        def log(message: str) -> None:
            with lock:
                line = f"[{time.strftime('%H:%M:%S')}] {message}"
                print(line, flush=True)
                log_file.write(line + "\n")

        if args.allow_metered:
            log("已显式允许计费来源")
        if args.redo != "none":
            log(f"重探已失败记录：--redo {args.redo}")
        connection = sqlite3.connect(args.db)
        if args.asset:
            # 显式点名就不再套时长筛选，但计费来源和 online 的边界照旧生效。
            placeholders = ",".join("?" for _ in args.asset)
            sql = (
                f"SELECT id,path FROM asset WHERE medium='video' AND id IN ({placeholders})"
                " AND location != 'online'" + source_sql + " ORDER BY size ASC"
            )
            parameters: tuple[object, ...] = tuple(args.asset) + source_parameters
        else:
            sql = (
                "SELECT id,path FROM asset WHERE medium='video' AND "
                + duration_selection(args.redo)
                + f" AND location != 'online' AND {ACTIVE_ASSET_SQL}"
                + source_sql + " ORDER BY size ASC"
            )
            parameters = source_parameters
        if args.limit:
            sql += " LIMIT ?"
            parameters += (args.limit,)
        from peach.strm import background_rows
        tasks = background_rows(connection.execute(sql, parameters).fetchall(), 1)
        connection.close()
        total = len(tasks)
        log(f"待处理 {total:,} 个视频 workers={args.workers} interval={args.interval}s 日志={log_path}")
        if not total:
            return 0

        pending: queue.Queue = queue.Queue()
        results: queue.Queue = queue.Queue()
        for task in tasks:
            pending.put(task)
        counters = {"done": 0, "failed": 0}
        started = time.time()
        # 探测本身产物很小，但它触发的云盘块缓存会落在系统盘；边跑边看，不只查起跑线。
        guard = DiskGuard(system_volume(), args.min_free, args.disk_check_secs)
        stop = threading.Event()
        stop_reason: list[str] = []

        def worker() -> None:
            while not stop.is_set():
                try:
                    asset_id, path = pending.get_nowait()
                except queue.Empty:
                    return
                try:
                    duration, width, height, codec, fps, audio = probe_file(
                        str(choice.path), resolve_case_insensitive(path), args.timeout
                    )
                    if duration <= 0:
                        with lock:
                            counters["failed"] += 1
                    length, orientation, quality = context_fields(width, height, duration)
                    results.put((duration, width, height, codec, fps, audio, length, orientation, quality, asset_id))
                except Exception:
                    results.put((-1, 0, 0, None, 0, 0, None, None, None, asset_id))
                    with lock:
                        counters["failed"] += 1
                finally:
                    with lock:
                        counters["done"] += 1
                        if counters["done"] % 50 == 0:
                            elapsed = time.time() - started
                            rate = counters["done"] / elapsed if elapsed else 0
                            eta = (total - counters["done"]) / rate / 60 if rate else 0
                            log(f"{counters['done']:,}/{total:,} 失败 {counters['failed']} {rate:.1f} 个/秒 剩余 {eta:.0f} 分钟")
                        try:
                            guard.check()
                        except JobPolicyError as exc:
                            if not stop.is_set():
                                stop_reason.append(str(exc))
                                log(f"[stop] {exc}")
                            stop.set()
                    time.sleep(args.interval)

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
        for thread in threads:
            thread.start()

        connection = sqlite3.connect(args.db, timeout=120)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=120000")
        update = UPDATE
        buffer = []
        while any(thread.is_alive() for thread in threads) or not results.empty():
            try:
                buffer.append(results.get(timeout=2))
            except queue.Empty:
                pass
            if len(buffer) >= 25:
                connection.executemany(update, buffer)
                connection.commit()
                buffer.clear()
        if buffer:
            connection.executemany(update, buffer)
            connection.commit()
        elapsed = time.time() - started
        log(f"完成 {counters['done']:,} 个，失败 {counters['failed']}，耗时 {elapsed/60:.1f} 分钟")
        connection.close()
        if stop_reason:
            # 已探测的部分都已入库；中止必须体现在退出码上，不能报成正常完成。
            log(f"磁盘闸门中止：{stop_reason[0]}")
            return DiskSpaceDenied.exit_code
    return 0


def main(argv: list[str] | None = None) -> int:
    return job_main(build_parser, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
