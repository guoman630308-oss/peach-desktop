"""缺 `moov` 的 MP4：借同来源一部完好的片子当参照，用 untrunc 从 `mdat` 里重建索引。

下载或录制没写完的 MP4 只有 `ftyp` 和 `mdat`，每一帧在哪、多大、什么时刻都记在
`moov` 里，它缺了就没有播放器打得开。码流本身还在，untrunc 拿参照片的编码参数把
`mdat` 逐帧切开，再写出一份带索引的新文件。

参照必须和坏片出自同一台编码器、同一套参数，否则切出来的帧解不开。所以候选按目录
与文件名远近排：同目录里名字挨着的最先试（同一批下载、同一天发布），实测命中率最高。
修出来的文件整片解码一遍才算数，解码报错多于几行、音画时长对不上的一律不收。

修好的文件换到原路径，坏原件改名成同目录的隐藏文件留着。和 `ctts` 那类只存边车不同：
缺索引的片子在哪都打不开，时长、缩略图也都生成不了，只修 Peach 里的播放不够。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .mp4repair import RepairUnavailable, _top_level

UNTRUNC_ENV = "PEACH_UNTRUNC"
#: 一部坏片最多试几个参照。每试一个都要把坏片完整读一遍，网盘上是实打实的流量。
REFERENCE_LIMIT = 4
#: 整片解码允许的报错行数。截断处最后一帧常常不完整，实测修好的片子恰好报 1 行。
DECODE_ERROR_LIMIT = 3
#: 音频与视频时长差出这个比例，就是参照的采样率或帧率对不上，切出来的时间轴是错的。
DURATION_DRIFT = 0.05
#: 恢复出的数据到这个比例就不必再换参照了。
GOOD_ENOUGH = 0.99
RUN_TIMEOUT_SECONDS = 4 * 3600
ORIGINAL_SUFFIX = ".peach-original"


@dataclass(frozen=True)
class Recovery:
    path: Path
    reference: Path
    duration: float
    #: 修好的片子里的媒体数据占坏片 `mdat` 的比例；片尾接了一段参数不同的内容时会明显小于 1。
    coverage: float


def untrunc_path(tools_root: Path) -> Path | None:
    """依次查环境变量、随包工具、Peach 数据目录托管目录与 PATH。"""
    configured = os.environ.get(UNTRUNC_ENV)
    if configured and Path(configured).expanduser().is_file():
        return Path(configured).expanduser().resolve()
    name = f"untrunc{'.exe' if os.name == 'nt' else ''}"
    if getattr(sys, 'frozen', False):
        bundled = Path(sys.executable).resolve().parent / 'tools' / 'untrunc' / name
        if bundled.is_file():
            return bundled.resolve()
    managed = Path(tools_root) / "untrunc" / name
    if managed.is_file():
        return managed.resolve()
    found = shutil.which("untrunc")
    return Path(found).resolve() if found else None


def _payload_size(path: Path) -> int:
    with open(path, "rb") as handle:
        return sum(end - body for kind, _start, body, end in _top_level(handle) if kind == b"mdat")


def missing_index(source: Path) -> bool:
    """顶层有 `mdat` 却没有 `moov`。只读每个顶层盒子的头，不碰码流。"""
    with open(source, "rb") as handle:
        kinds = {kind for kind, *_ in _top_level(handle)}
    return b"mdat" in kinds and b"moov" not in kinds


def pick_references(broken: str, candidates: list[str], limit: int = REFERENCE_LIMIT) -> list[str]:
    """候选参照按远近排：同目录优先，其次按文件名排序后离坏片有多远。

    路径是账本口径（Windows 形态）。坏片自己不在候选里。
    """
    target = PureWindowsPath(broken)
    ordered = sorted({*candidates, broken}, key=lambda raw: str(PureWindowsPath(raw)).casefold())
    rank = {raw: index for index, raw in enumerate(ordered)}
    others = [raw for raw in candidates if raw != broken]
    others.sort(key=lambda raw: (PureWindowsPath(raw).parent != target.parent,
                                 abs(rank[raw] - rank[broken])))
    return others[:limit]


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def _stream_durations(ffprobe: Path, path: Path) -> dict[str, float]:
    completed = _run([str(ffprobe), "-v", "error", "-show_entries", "stream=codec_type,duration",
                      "-of", "csv=p=0", str(path)], 120)
    durations: dict[str, float] = {}
    for line in completed.stdout.decode("utf-8", "replace").splitlines():
        kind, _, value = line.strip().partition(",")
        try:
            durations.setdefault(kind, float(value))
        except ValueError:
            continue
    return durations


def _decode_errors(ffmpeg: Path, path: Path) -> int:
    completed = _run([str(ffmpeg), "-v", "error", "-threads", "0", "-i", str(path), "-f", "null", "-"],
                     RUN_TIMEOUT_SECONDS)
    lines = completed.stderr.decode("utf-8", "replace").splitlines()
    return sum(1 for line in lines if "error while decoding" in line or "corrupt" in line)


def acceptable(durations: dict[str, float], errors: int) -> bool:
    """修出来的片子能不能收：有视频、解码几乎不报错、音画时长对得上。"""
    video = durations.get("video", 0.0)
    if video < 1 or errors > DECODE_ERROR_LIMIT:
        return False
    audio = durations.get("audio")
    return audio is None or abs(audio - video) <= DURATION_DRIFT * video


def recover(source: Path, references: list[Path], *, untrunc: Path, ffmpeg: Path, ffprobe: Path,
            work: Path) -> Recovery:
    """逐个参照试，收恢复得最多的那一份。一份都收不了就抛 `RepairUnavailable`。"""
    work.mkdir(parents=True, exist_ok=True)
    total = _payload_size(source)
    best: Recovery | None = None
    for index, reference in enumerate(references):
        output = work / f"recovered-{index}.mp4"
        output.unlink(missing_ok=True)
        try:
            # `-s` 跳过认不出的数据段接着往下切，不在第一处对不上的地方就收手。
            _run([str(untrunc), "-n", "-s", "-dst", str(output), str(reference), str(source)],
                 RUN_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            output.unlink(missing_ok=True)
            continue
        if not output.is_file():
            continue
        durations = _stream_durations(ffprobe, output)
        if not acceptable(durations, _decode_errors(ffmpeg, output)):
            output.unlink()
            continue
        found = Recovery(output, reference, durations["video"],
                         _payload_size(output) / total if total else 0.0)
        if best is None or found.coverage > best.coverage:
            if best is not None:
                best.path.unlink(missing_ok=True)
            best = found
        else:
            output.unlink()
        if best.coverage >= GOOD_ENOUGH:
            break
    if best is None:
        raise RepairUnavailable("没有一个参照切得出能解码的片子")
    return best


def replace_original(source: Path, repaired: Path) -> Path:
    """修好的文件换到原路径，坏原件改名成同目录的隐藏文件；返回留档的路径。

    先把新文件完整写成同目录的临时名，再做两次同目录改名：网盘上改名是服务端操作，
    中途失败时原件还在原路径或留档名上，不会两头落空。
    """
    kept = source.with_name(f".{source.name}{ORIGINAL_SUFFIX}")
    if kept.exists():
        raise RepairUnavailable(f"同目录已有一份留档：{kept.name}")
    staged = source.with_name(f".{source.name}.peach-repair.tmp")
    try:
        shutil.copyfile(repaired, staged)
        if staged.stat().st_size != repaired.stat().st_size:
            raise OSError("写到原目录的副本大小不对")
        os.replace(source, kept)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    try:
        os.replace(staged, source)
    except BaseException:
        os.replace(kept, source)
        staged.unlink(missing_ok=True)
        raise
    repaired.unlink(missing_ok=True)
    return kept
