#!/usr/bin/env python3
"""按创作者跨作品抽样并生成 3×3 风格板。"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as futures
import hashlib
import random
import re
import subprocess
import threading
import time
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import DATABASE_PATH, FFMPEG_DIR, GENERATED_DIR, STATE_DIR
from peach.scripting import open_readonly
from peach.classification import is_probable_mainstream_release, is_structural_creator
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
from peach.media import normalized_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成创作者跨作品视觉风格板")
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument("--per", type=int, default=9)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--from-video", action="store_true")
    parser.add_argument("--location")
    parser.add_argument("--allow-metered", action="store_true")
    parser.add_argument("--min-free", type=float, default=40.0)
    parser.add_argument(
        "--disk-check-secs",
        type=float,
        default=20.0,
        help="运行期复查磁盘余量的间隔；0 表示每条都查",
    )
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--output-dir", type=Path, default=GENERATED_DIR / "boards")
    parser.add_argument("--lock", type=Path, default=STATE_DIR / ".creator-boards.lock")
    return parser


def safe_name(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]", "_", name)[:40]


def cell_height(ffprobe: str, path: Path) -> int | None:
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "v",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        width, height = (int(value) for value in result.stdout.strip().split(","))
    except ValueError:
        return None
    if width != 1440:
        return None
    return height if height < 1000 else height // 3


def grab_frame(ffmpeg: str, cache: Path, job: tuple[str, float]) -> Path | None:
    path, duration = job
    destination = cache / (
        hashlib.sha1(path.encode("utf-8", "ignore")).hexdigest()[:16] + ".jpg"
    )
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    seek = int((duration or 60) * 0.3)
    try:
        subprocess.run(
            [
                ffmpeg, "-y", "-v", "error", "-ss", str(seek), "-i", path,
                "-frames:v", "1", "-vf", "scale=480:-1", str(destination),
            ],
            capture_output=True,
            timeout=240,
        )
    except subprocess.TimeoutExpired:
        return None
    return destination if destination.is_file() and destination.stat().st_size > 0 else None


def build_board(ffmpeg: str, ffprobe: str, paths: list[Path], destination: Path) -> bool:
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for source in paths[:9]:
        height = cell_height(ffprobe, source)
        index = len(labels)
        inputs.extend(("-i", str(source)))
        crop = f"crop=480:{height}:0:0," if height else ""
        filters.append(
            f"[{index}:v]{crop}scale=360:360:force_original_aspect_ratio=decrease,"
            f"pad=360:360:(ow-iw)/2:(oh-ih)/2:color=black[v{index}]"
        )
        labels.append(f"[v{index}]")
    if len(labels) < 4:
        return False
    while len(labels) < 9:
        filters.append(f"color=c=black:s=360x360:d=1[v{len(labels)}]")
        labels.append(f"[v{len(labels)}]")
    layout = "|".join(f"{column*360}_{row*360}" for row in range(3) for column in range(3))
    chain = ";".join(filters) + ";" + "".join(labels) + f"xstack=inputs=9:layout={layout}[out]"
    result = subprocess.run(
        [
            ffmpeg, "-y", "-v", "error", *inputs, "-filter_complex", chain,
            "-map", "[out]", "-frames:v", "1", "-q:v", "4", str(destination),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print("  ffmpeg:", result.stderr.strip()[:160])
        return False
    return destination.is_file()


def _existing_video_boards(output_dir: Path) -> set[str]:
    names = set()
    for path in output_dir.glob("v*_*.jpg"):
        match = re.match(r"^v\d+_(.+)_\d+\.jpg$", path.name)
        if match:
            names.add(match.group(1))
    return names


def build_from_videos(args: argparse.Namespace, ffmpeg: str, ffprobe: str) -> int:
    require_free_space(system_volume(), args.min_free)
    source_sql, source_parameters = SourceAccessPolicy().sql_filter(
        args.location, args.allow_metered
    )
    frame_cache = args.output_dir / "_frames"
    frame_cache.mkdir(parents=True, exist_ok=True)
    connection = open_readonly(args.db)
    rows = connection.execute(
        "SELECT creator,path,name,duration FROM asset WHERE medium='video' "
        "AND creator IS NOT NULL AND creator<>'' "
        f"AND {ACTIVE_ASSET_SQL} "
        "AND id NOT IN (SELECT asset_id FROM asset_tag)" + source_sql,
        source_parameters,
    ).fetchall()
    connection.close()

    by_creator: dict[str, list[tuple[str, float]]] = collections.defaultdict(list)
    for creator, path, name, duration in rows:
        if is_structural_creator(creator) or is_probable_mainstream_release(name, path):
            continue
        by_creator[creator].append((path, duration or 60))
    ranked = sorted(by_creator.items(), key=lambda item: -len(item[1]))[:args.top]
    existing = _existing_video_boards(args.output_dir)
    ranked = [(creator, items) for creator, items in ranked if safe_name(creator) not in existing]
    print(f"待出板 {len(ranked)} 位；并发 {args.workers}")

    jobs: list[tuple[str, float]] = []
    for creator, items in ranked:
        random.Random(creator).shuffle(items)
        jobs.extend(items[:args.per])
    captured: dict[tuple[str, float], Path] = {}
    started = time.time()
    # 抽帧触发的云盘块缓存落在系统盘，起跑线检查拦不住；边跑边看，触线让未开始的帧直接跳过。
    guard = DiskGuard(system_volume(), args.min_free, args.disk_check_secs)
    stop = threading.Event()
    stopped_reason = ""

    def grab(job: tuple[str, float]) -> Path | None:
        if stop.is_set():
            return None
        return grab_frame(ffmpeg, frame_cache, job)

    with futures.ThreadPoolExecutor(args.workers) as pool:
        outputs = pool.map(grab, jobs)
        for index, (job, output) in enumerate(zip(jobs, outputs), 1):
            if output:
                captured[job] = output
            if index % 25 == 0:
                rate = index / max(time.time() - started, 1) * 60
                print(f"  {index}/{len(jobs)} 帧 成功 {len(captured)} {rate:.1f} 帧/分", flush=True)
            if not stop.is_set():
                try:
                    guard.check()
                except JobPolicyError as exc:
                    stopped_reason = str(exc)
                    print(f"  [stop] {exc}", flush=True)
                    stop.set()

    if stopped_reason:
        # 已抽到的帧留在缓存里，续跑时不会重复下载；但板子不出，避免用残缺样本代表创作者。
        print(f"磁盘闸门中止：{stopped_reason}")
        print(f"已缓存 {len(captured)} 帧，未出板；恢复磁盘后重跑即可续上。")
        return DiskSpaceDenied.exit_code

    made = 0
    for rank, (creator, items) in enumerate(ranked, 1):
        frames = [captured[job] for job in items[:args.per] if job in captured]
        if len(frames) < 4:
            print(f"  {rank:>2}. {creator[:26]:<26} 可用帧仅 {len(frames)}，跳过")
            continue
        destination = args.output_dir / f"v{rank:02d}_{safe_name(creator)}_{len(items)}.jpg"
        if build_board(ffmpeg, ffprobe, frames, destination):
            made += 1
            print(f"  {rank:>2}. {creator[:26]:<26} 视频{len(items):>4} 帧{len(frames)} → {destination.name}")
    print(f"生成 {made} 张 → {args.output_dir}")
    return 0


def build_from_snapshots(args: argparse.Namespace, ffmpeg: str, ffprobe: str) -> int:
    connection = open_readonly(args.db)
    rows = connection.execute(
        "SELECT creator,name,path,snapshot_path FROM asset WHERE medium='video' "
        "AND creator IS NOT NULL AND creator<>'' AND snapshot_path IS NOT NULL "
        f"AND {ACTIVE_ASSET_SQL} "
        "AND id NOT IN (SELECT asset_id FROM asset_tag)"
    ).fetchall()
    count_rows = connection.execute(
            "SELECT creator,name,path FROM asset WHERE medium='video' AND creator IS NOT NULL "
            f"AND creator<>'' AND {ACTIVE_ASSET_SQL} "
            "AND id NOT IN (SELECT asset_id FROM asset_tag)"
        ).fetchall()
    counts = collections.Counter(
        creator for creator, name, path in count_rows
        if not is_structural_creator(creator)
        and not is_probable_mainstream_release(name, path)
    )
    connection.close()
    by_creator: dict[str, list[Path]] = collections.defaultdict(list)
    for creator, name, asset_path, snapshot in rows:
        if is_structural_creator(creator) or is_probable_mainstream_release(name, asset_path):
            continue
        path = normalized_path(snapshot)
        if path.is_file():
            by_creator[creator].append(path)
    ranked = [(creator, count) for creator, count in counts.most_common() if len(by_creator.get(creator, [])) >= 4][:args.top]
    print(f"可出板创作者 {len(ranked)} 位，覆盖无标签视频 {sum(count for _, count in ranked)} 条")
    made = 0
    for rank, (creator, count) in enumerate(ranked, 1):
        candidates = by_creator[creator]
        random.Random(creator).shuffle(candidates)
        destination = args.output_dir / f"{rank:02d}_{safe_name(creator)}_{count}.jpg"
        if build_board(ffmpeg, ffprobe, candidates[:args.per], destination):
            made += 1
            print(f"  {rank:>2}. {creator[:26]:<26} 视频{count:>4} 取样{min(len(candidates), args.per)} → {destination.name}")
    print(f"生成 {made} 张 → {args.output_dir}")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.top < 1 or args.per < 4 or args.workers < 1:
        raise SystemExit("top/workers 必须大于 0，per 至少为 4")
    resolver = FFmpegResolver(FFMPEG_DIR)
    ffmpeg = resolver.ffmpeg()
    ffprobe = resolver.ffprobe()
    if ffmpeg is None or ffprobe is None:
        raise RuntimeError("ffmpeg/ffprobe unavailable")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.from_video:
        return build_from_videos(args, str(ffmpeg.path), str(ffprobe.path))
    return build_from_snapshots(args, str(ffmpeg.path), str(ffprobe.path))


def main(argv: list[str] | None = None) -> int:
    return job_main(build_parser, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
