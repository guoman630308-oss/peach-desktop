#!/usr/bin/env python3
"""全库关键帧九宫格：可续跑、显式计费授权、产物落地后登记。"""
from __future__ import annotations

import argparse
import hashlib
import queue
import shutil
import sqlite3
import tempfile
import threading
import time
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach import frame_capture
from peach.config import DATABASE_PATH, FFMPEG_DIR, GENERATED_DIR, LOG_DIR, STATE_DIR
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
from peach.media_processing_policy import metadata_only
from peach.strm import background_rows, skip_background_read


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成视频关键帧九宫格")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--location")
    parser.add_argument("--frames", type=int, default=9)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-free", type=float, default=40.0)
    parser.add_argument(
        "--disk-check-secs",
        type=float,
        default=20.0,
        help="运行期复查磁盘余量的间隔；0 表示每条都查",
    )
    parser.add_argument(
        "--asset",
        type=int,
        action="append",
        help="按 id 只抽指定资产，绕过来源与批量筛选。用于修复个别失败条目，"
             "避免为一条重跑整个来源的待抽队列",
    )
    parser.add_argument("--allow-metered", action="store_true")
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--output-root", type=Path, default=GENERATED_DIR / "snapshots" / "cloud")
    parser.add_argument("--log-dir", type=Path, default=LOG_DIR)
    parser.add_argument("--lock", type=Path, default=STATE_DIR / ".sheets.lock")
    return parser


def output_path(output_root: Path, location: str, path: str) -> Path:
    digest = hashlib.sha1(path.encode("utf-8", "ignore")).hexdigest()[:16]
    directory = output_root / location / digest[:2]
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{digest}.jpg"


COLOR_OVERRIDE = frame_capture.COLOR_OVERRIDE
COLOR_METADATA_ERROR = frame_capture.COLOR_METADATA_ERROR


def _capture_frame(ffmpeg: str, path: str, timestamp: float, destination: Path,
                   color_override: bool) -> tuple[bool, str]:
    """抽一帧，返回（是否成功, stderr）。stderr 用于判断值不值得重试。

    命令本身在 `peach.frame_capture`，时间轴预览走的是同一条。色彩元数据那条重试判据
    只写一处：分成两份之后，坏元数据的那批片子会只在其中一份里被认出来，而失败形态是
    「这些片子没有图」，看不出是哪一份少了一条。
    """
    return frame_capture.capture_frame(ffmpeg, path, timestamp, destination,
                                       width=480, color_override=color_override)


def make_sheet(ffmpeg: str, path: str, duration: float, destination: Path,
               frames: int) -> tuple[bool, str]:
    """输入端 seek 抽帧后调用 FFmpeg tile；不线性解码整片。

    失败时返回可区分的原因，不能只返回「失败」：asset 12510 与 18349 都失败了一整天，
    一个是片源头损坏、一个是账本时长记错，光看计数分不出来，也就没法决定该修哪一边。
    """
    if skip_background_read(path):
        return False, "unsupported_source"
    if not duration or duration < 2:
        return False, "no_duration"
    temporary = Path(tempfile.mkdtemp(prefix="sheet_"))
    try:
        captured: list[Path] = []
        for index in range(frames):
            if metadata_only():
                return False, "previews_disabled"
            timestamp = duration * (0.03 + 0.94 * (index + 0.5) / frames)
            frame = temporary / f"{index:02d}.jpg"
            ok, stderr = _capture_frame(ffmpeg, path, timestamp, frame, color_override=False)
            if not ok and COLOR_METADATA_ERROR.search(stderr):
                ok, _ = _capture_frame(ffmpeg, path, timestamp, frame, color_override=True)
            if ok:
                captured.append(frame)
        if len(captured) < 2:
            # 一帧都解不出来是片源问题；解得出一部分说明片源可用，是账本时长比真实文件长，
            # 采样点落到了文件末尾之后。两者要修的地方不同。
            return False, "broken_source" if not captured else "duration_mismatch"
        for index, frame in enumerate(captured):
            target = temporary / f"s{index:02d}.jpg"
            if frame != target:
                frame.replace(target)
        rows = (len(captured) + 2) // 3
        if frame_capture.tile_frames(ffmpeg, str(temporary / "s%02d.jpg"), 3, rows,
                                     destination):
            return True, ""
        return False, "tile_failed"
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _require_previews_enabled() -> None:
    if metadata_only():
        raise JobPolicyError("视频预览生成已关闭，请先在媒体库设置中开启")


def _preview_active(stop, reasons) -> bool:
    if stop.is_set():
        return False
    if metadata_only():
        reasons.append("视频预览生成已关闭")
        stop.set()
        return False
    return True


def run(args: argparse.Namespace) -> int:
    _require_previews_enabled()
    if args.workers < 1 or args.frames < 2 or args.limit < 0:
        raise SystemExit("workers 必须大于 0，frames 至少为 2，limit 不能为负数")
    resolver = FFmpegResolver(FFMPEG_DIR)
    ffmpeg = resolver.ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg unavailable")
    require_free_space(system_volume(), args.min_free)
    source_sql, source_parameters = SourceAccessPolicy().sql_filter(
        args.location, args.allow_metered
    )
    args.output_root.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.log_dir / f"sheets-{time.strftime('%Y%m%d-%H%M%S')}.log"
    lock = threading.RLock()

    with log_path.open("w", encoding="utf-8", buffering=1) as log_file:
        def log(message: str) -> None:
            with lock:
                line = f"[{time.strftime('%H:%M:%S')}] {message}"
                print(line, flush=True)
                log_file.write(line + "\n")

        if args.allow_metered:
            log("已显式允许计费来源")
        connection = sqlite3.connect(args.db)
        if args.asset:
            # 点名重抽时不套 snapshot_path IS NULL：修完时长要重抽的那条，可能已经带着
            # 上一次的错误结果。计费来源和 online 的边界照旧生效。
            placeholders = ",".join("?" for _ in args.asset)
            sql = (
                "SELECT id,location,path,duration FROM asset WHERE medium='video' "
                f"AND id IN ({placeholders}) AND location != 'online' AND duration > 2"
                + source_sql + " ORDER BY size DESC"
            )
            parameters: tuple[object, ...] = tuple(args.asset) + source_parameters
        else:
            sql = (
                "SELECT id,location,path,duration FROM asset WHERE medium='video' "
                "AND snapshot_path IS NULL AND location != 'online' AND duration > 2"
                f" AND {ACTIVE_ASSET_SQL}"
                + source_sql + " ORDER BY size DESC"
            )
            parameters = source_parameters
        if args.limit:
            sql += " LIMIT ?"
            parameters += (args.limit,)
        tasks = background_rows(connection.execute(sql, parameters).fetchall(), 2)
        connection.close()
        total = len(tasks)
        log(f"待处理 {total:,} 个视频 workers={args.workers} frames={args.frames} 日志={log_path}")
        if not total:
            log("没有待处理项；可能需要先运行 probe")
            return 0

        pending: queue.Queue = queue.Queue()
        results: queue.Queue = queue.Queue()
        for task in tasks:
            pending.put(task)
        counters = {"done": 0, "failed": 0, "existing": 0}
        failure_reasons: Counter[str] = Counter()
        started = time.time()
        # 起跑线检查拦不住运行期把盘吃光的第三方缓存；这里边跑边看。
        guard = DiskGuard(system_volume(), args.min_free, args.disk_check_secs)
        stop = threading.Event()
        stop_reason: list[str] = []

        def worker() -> None:
            while _preview_active(stop, stop_reason):
                try:
                    asset_id, location, path, duration = pending.get_nowait()
                except queue.Empty:
                    return
                destination = output_path(args.output_root, location, path)
                try:
                    if (not args.asset and destination.is_file()
                            and destination.stat().st_size > 4096):
                        results.put((str(destination), asset_id))
                        with lock:
                            counters["existing"] += 1
                    else:
                        ok, reason = make_sheet(
                            str(ffmpeg.path), resolve_case_insensitive(path), duration,
                            destination, args.frames,
                        )
                        if ok:
                            results.put((str(destination), asset_id))
                        else:
                            with lock:
                                counters["failed"] += 1
                                failure_reasons[reason] += 1
                            log(f"[fail] asset {asset_id} {reason} {path}")
                except Exception as exc:
                    with lock:
                        counters["failed"] += 1
                        failure_reasons["exception"] += 1
                    log(f"[fail] asset {asset_id} exception {type(exc).__name__} {path}")
                finally:
                    with lock:
                        counters["done"] += 1
                        if counters["done"] % 100 == 0:
                            elapsed = time.time() - started
                            rate = counters["done"] / elapsed if elapsed else 0
                            eta = (total - counters["done"]) / rate / 3600 if rate else 0
                            log(f"{counters['done']:,}/{total:,} 失败 {counters['failed']} 已存在 {counters['existing']} 剩余 {eta:.1f} 小时")
                        try:
                            guard.check()
                        except JobPolicyError as exc:
                            if not stop.is_set():
                                stop_reason.append(str(exc))
                                log(f"[stop] {exc}")
                            stop.set()

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
        for thread in threads:
            thread.start()

        connection = sqlite3.connect(args.db, timeout=60)
        buffer = []
        while any(thread.is_alive() for thread in threads) or not results.empty():
            # 等待要短：循环条件看到线程还活着、下一刻线程就退出的话，这一次 get 要等满
            # 超时才回来。等 2 秒的话每次收尾都白站 2 秒，一次一张的重拍尤其明显。
            try:
                buffer.append(results.get(timeout=0.25))
            except queue.Empty:
                pass
            if len(buffer) >= 50:
                connection.executemany("UPDATE asset SET snapshot_path=? WHERE id=?", buffer)
                connection.commit()
                buffer.clear()
        if buffer:
            connection.executemany("UPDATE asset SET snapshot_path=? WHERE id=?", buffer)
            connection.commit()
        elapsed = time.time() - started
        log(f"完成 {counters['done']:,}，失败 {counters['failed']}，已存在 {counters['existing']}，耗时 {elapsed/3600:.2f} 小时")
        if failure_reasons:
            breakdown = "，".join(f"{reason} {count}" for reason, count in failure_reasons.most_common())
            log(f"失败原因：{breakdown}")
        connection.close()
        if stop_reason:
            # 已完成的部分都已入库；磁盘闸门中止不能报成正常完成，否则续跑决策会被误导。
            log(f"任务中止：{stop_reason[0]}")
            return DiskSpaceDenied.exit_code
    return 0


def main(argv: list[str] | None = None) -> int:
    return job_main(build_parser, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
