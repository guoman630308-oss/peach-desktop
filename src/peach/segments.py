"""按关键帧切 HLS 片段，避免挂载网盘承担整段 MP4 Range。"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import subprocess
import uuid
from collections import OrderedDict
from pathlib import Path

from .ffmpeg import FFmpegResolver
from .hardware_transcode import HardwareTranscoder
from .mp4index import keyframe_seconds, segment_plan
from .streaming import StreamSessionRegistry

HLS_SEGMENT_SECONDS = 6
# 片段缓存上限。超了按最后访问时间淘汰；片段可再生，丢了只是重跑一次 FFmpeg。
DEFAULT_CACHE_BYTES = 2 << 30
PLAN_CACHE_LIMIT = 256
CODEC_CACHE_LIMIT = 256
PROBE_TIMEOUT_SECONDS = 20
logger = logging.getLogger(__name__)


def _probe_video_codec(ffprobe: Path, source: Path) -> str:
    """问 ffprobe 要第一条视频流的编码名。问不出来返回空串，调用方按软件解码处理。"""
    command = (str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=codec_name", "-of", "default=nw=1:nk=1", str(source))
    try:
        result = subprocess.run(command, capture_output=True, timeout=PROBE_TIMEOUT_SECONDS,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.decode("utf-8", "replace").strip().lower() if result.returncode == 0 else ""


class SegmentUnavailable(RuntimeError):
    pass


class SegmentCancelled(RuntimeError):
    pass


def build_hls_playlist(plan: list[tuple[float, float]], segment_url) -> str:
    """按真实分片计划生成 VOD 播放列表。

    时长必须写实际值。按 6 秒等分声明的话，`-c copy` 只能切在关键帧上，
    遇到关键帧间隔 8.33 秒的片源，目录说的和实际内容就完全对不上。
    """
    target = max(1, round(max(length for _, length in plan)))
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{target}",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-MEDIA-SEQUENCE:0",
    ]
    for index, (_, length) in enumerate(plan):
        lines.extend((f"#EXTINF:{length:.3f},", str(segment_url(index))))
    lines.extend(("#EXT-X-ENDLIST", ""))
    return "\n".join(lines)


class HlsSegmentService:
    """按关键帧边界 remux 单个时间段，输出可缓存的 MPEG-TS。"""

    def __init__(
        self,
        resolver: FFmpegResolver,
        work_root: Path,
        segment_seconds: int = HLS_SEGMENT_SECONDS,
        max_concurrent: int | None = None,
        cache_bytes: int = DEFAULT_CACHE_BYTES,
        prefer_hardware: bool = False,
        hardware: HardwareTranscoder | None = None,
    ):
        self.resolver = resolver
        self.work_root = work_root
        self.segment_seconds = segment_seconds
        self.cache_bytes = cache_bytes
        self.prefer_hardware = prefer_hardware
        self.hardware = hardware if hardware is not None else HardwareTranscoder(enabled=prefer_hardware)
        # 每个分片请求都会起一个 FFmpeg；播放器本身就并发预取，多设备同看能把机器打满。
        self._limit = max_concurrent or max(1, (os.cpu_count() or 4) // 2)
        self._semaphore: asyncio.Semaphore | None = None
        self._plans: OrderedDict[tuple, list[tuple[float, float]]] = OrderedDict()
        self._codecs: OrderedDict[tuple, str] = OrderedDict()
        # 同一分片常被并发请求：播放器预取、重连、多设备同看都会撞上同一个目标文件。
        # 同目标只放一个生成者，后到者等它完事后直接用缓存，不再各起一个 FFmpeg 重读网盘。
        self._generation_locks: dict[Path, asyncio.Lock] = {}

    def _gate(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._limit)
        return self._semaphore

    def fingerprint(self, source: Path) -> tuple:
        try:
            stat = source.stat()
        except OSError:
            return (str(source), 0, 0)
        return (str(source), stat.st_size, int(stat.st_mtime))

    async def video_codec(self, source: Path) -> str:
        """片源的视频编码名；探测不到返回空串。按 (路径, 大小, mtime) 缓存。

        只有重编码路径问这一句，用来决定敢不敢把解码交给显卡。探测本身在线程里跑：
        片源在挂载网盘上时 ffprobe 要几百毫秒，占着事件循环会卡住同时在放的其它片。
        """
        key = self.fingerprint(source)
        cached = self._codecs.get(key)
        if cached is not None:
            self._codecs.move_to_end(key)
            return cached
        choice = self.resolver.ffprobe()
        codec = await asyncio.to_thread(_probe_video_codec, choice.path, source) if choice else ""
        while len(self._codecs) >= CODEC_CACHE_LIMIT:
            self._codecs.popitem(last=False)
        self._codecs[key] = codec
        return codec

    def plan(self, source: Path, duration: float) -> list[tuple[float, float]] | None:
        """返回分片计划；读不到关键帧就返回 None，由调用方回退标准 Range。"""
        key = (*self.fingerprint(source), self.segment_seconds, round(duration, 3))
        cached = self._plans.get(key)
        if cached is not None:
            self._plans.move_to_end(key)
            return cached or None
        keyframes = keyframe_seconds(source)
        result = segment_plan(keyframes, duration, self.segment_seconds) if keyframes else []
        while len(self._plans) >= PLAN_CACHE_LIMIT:
            self._plans.popitem(last=False)
        self._plans[key] = result
        return result or None

    def cached_path(self, source: Path, asset_id: int, index: int, *, transcode: bool = False) -> Path:
        _, size, mtime = self.fingerprint(source)
        # 版本号跟着转码命令走：命令一变，磁盘上按另一套命令转出来的片段就得失效，
        # 否则换了解码器，播放器拿到的仍是缓存里那些绿帧。
        flavor = '-h264-v2' if transcode else ''
        return self.work_root / str(asset_id) / f"{size}-{mtime}-{self.segment_seconds}{flavor}" / f"{index}.ts"

    def conversion_plan(self, duration: float) -> list[tuple[float, float]]:
        """编码片段各自以关键帧开始，覆盖完整的播放时间轴。"""
        return [(float(start), min(float(self.segment_seconds), duration - start))
                for start in range(0, math.ceil(duration), self.segment_seconds)]

    def _evict(self) -> None:
        files = [item for item in self.work_root.rglob("*.ts") if item.is_file()]
        total = 0
        stats = []
        for item in files:
            try:
                stat = item.stat()
            except OSError:
                continue
            total += stat.st_size
            stats.append((stat.st_mtime, stat.st_size, item))
        if total <= self.cache_bytes:
            return
        for _, size, item in sorted(stats):
            try:
                item.unlink()
            except OSError:
                continue
            total -= size
            if total <= self.cache_bytes:
                return

    async def generate(
        self,
        source: Path,
        start: float,
        duration: float,
        *,
        asset_id: int,
        index: int,
        session: str,
        registry: StreamSessionRegistry,
        transcode: bool = False,
    ) -> Path:
        target = self.cached_path(source, asset_id, index, transcode=transcode)
        if target.is_file() and target.stat().st_size:
            os.utime(target, None)      # 命中即续期，淘汰按最后访问时间
            return target
        lock = self._generation_locks.setdefault(target, asyncio.Lock())
        try:
            async with lock:
                # 等到锁的可能是排在生成者后面的请求，先看缓存再决定要不要自己跑。
                if target.is_file() and target.stat().st_size:
                    os.utime(target, None)
                    return target
                return await self._generate_uncached(
                    source, start, duration, target,
                    session=session, registry=registry, transcode=transcode,
                )
        finally:
            if not lock.locked():
                self._generation_locks.pop(target, None)

    async def _generate_uncached(
        self, source: Path, start: float, duration: float, target: Path, *,
        session: str, registry: StreamSessionRegistry, transcode: bool,
    ) -> Path:
        choice = self.resolver.ffmpeg()
        if choice is None:
            raise SegmentUnavailable("ffmpeg unavailable")
        if registry.is_cancelled(session):
            raise SegmentCancelled(session)
        target.parent.mkdir(parents=True, exist_ok=True)
        codec = await self.video_codec(source) if transcode and self.hardware.enabled else ""
        detail = ""
        async with self._gate():
            attempts = await asyncio.to_thread(
                self._attempts, choice.path, source, start, duration, transcode, codec,
            )
            for name, build in attempts:
                temporary = target.with_name(f"{uuid.uuid4().hex}.tmp.ts")
                try:
                    returncode, stderr = await self._run(build(temporary), session, registry)
                    if returncode == 0 and temporary.is_file() and temporary.stat().st_size:
                        temporary.replace(target)
                        logger.info("HLS 分片完成：%s", name)
                        self._evict()
                        return target
                    if registry.is_cancelled(session):
                        raise SegmentCancelled(session)
                    detail = stderr.decode("utf-8", "replace")[-1000:]
                    if not detail:
                        # FFmpeg 可以退出码 0、stderr 全空却写出 0 字节（时间窗取错就是
                        # 这样）。此时光报 "ffmpeg failed" 等于没报，把能观测到的都说出来。
                        size = temporary.stat().st_size if temporary.is_file() else None
                        detail = (
                            f"ffmpeg wrote no data: returncode={returncode} "
                            f"bytes={'缺文件' if size is None else size} "
                            f"ss={start:.3f} to={start + duration:.3f}"
                        )
                    detail = f"{name}: {detail}"
                finally:
                    temporary.unlink(missing_ok=True)
        raise SegmentUnavailable(detail)

    def _attempts(self, ffmpeg: Path, source: Path, start: float, duration: float,
                  transcode: bool, codec: str = ""):
        """依次尝试的命令，每项是 (名字, 接收临时文件路径的构造函数)。

        封装复制只有一种。重编码与整片转码共享可用硬件后端，最后回退 libx264。

        NVDEC 解得出来的编码才走 CUDA 解码，`codec` 空或不在名单里就从软件解码起。
        判据要在开跑前定，因为坏结果不会自报：实测 `MIAD573_02.wmv`（vc1、1080p）经
        `-hwaccel cuda` 出来的首个分片，开头 21 帧在黑场与纯绿之间交替（色度均值在
        128 与 0 之间跳），而 FFmpeg 退出码 0、stderr 全空，绿帧就这么写进缓存，
        下次命中还是它。同一段去掉 CUDA 解码后色度全程 128，一帧绿都没有。
        """
        prefix = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        mapping = ["-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]
        mux = ["-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts"]
        if not transcode:
            # 终点必须写成绝对时间戳。-copyts 保留原始时间轴后，-t 会被当成绝对结束时刻
            # 而不是片段时长，于是除了开头那一两段，每段的 -t 都早已过期，ffmpeg 以
            # 退出码 0、空 stderr 写出 0 字节，服务端只能报一句没有内容的 ffmpeg failed。
            # 保留原始时间戳，让每段接着上一段走。用 -avoid_negative_ts make_zero
            # 把每段归零的话，每段都自称从 0 秒开始，拖动进度条时容易跳错位置。
            window = ["-ss", f"{start:.3f}", "-i", str(source), "-to", f"{start + duration:.3f}"]
            return [("copy", lambda out: prefix + window + mapping + ["-c", "copy", "-copyts"] + mux + [str(out)])]
        window = ["-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}"]
        audio = ["-c:a", "aac", "-b:a", "160k", "-output_ts_offset", f"{start:.3f}"]
        attempts = []
        for encoder in self.hardware.encoders(ffmpeg):
            for attempt in encoder.attempts(codec):
                attempts.append((attempt.name, lambda out, attempt=attempt:
                                 prefix + list(attempt.before_input) + window + mapping
                                 + list(attempt.output) + audio + mux + [str(out)]))
        attempts.append(("libx264", lambda out: prefix + window + mapping + [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-threads", "2",
        ] + audio + mux + [str(out)]))
        return attempts

    async def _run(self, command: list[str], session: str, registry: StreamSessionRegistry) -> tuple[int, bytes]:
        """跑一次 FFmpeg 并把进程登记到会话，取消时能被杀掉。返回 (退出码, stderr)。"""
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise SegmentUnavailable("ffmpeg failed to start") from exc
        if not registry.register_process(session, process):
            _kill_process(process)
            await process.communicate()
            raise SegmentCancelled(session)
        try:
            _, stderr = await process.communicate()
        except asyncio.CancelledError:
            _kill_process(process)
            await process.communicate()
            raise
        finally:
            registry.unregister_process(session, process)
        return process.returncode, stderr or b""


def _kill_process(process) -> None:
    try:
        if process.returncode is None:
            process.kill()
    except (OSError, ProcessLookupError):
        pass
