from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from .ffmpeg import FFmpegResolver
from .hardware_transcode import HardwareTranscoder
from .mp4index import composition_offsets_present


BROWSER_NATIVE_SUFFIXES = frozenset({".mp4", ".m4v", ".webm", ".ogv", ".ogg"})
DIRECT_MP4_PIXEL_FORMATS = frozenset({"yuv420p", "yuvj420p"})
TRANSCODE_TIMEOUT_SECONDS = 3600
PROBE_TIMEOUT_SECONDS = 20
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _MediaProfile:
    video_codec: str
    pixel_format: str
    audio_codec: str
    #: 容器报的总时长（秒）；探测不到时为 0，切片计划据此决定能不能按时间切。
    duration: float = 0.0
    #: 有 B 帧却没有 ctts：容器声明的显示时刻其实是解码顺序，直接播会掉帧。
    decode_order_timestamps: bool = False


@dataclass(frozen=True)
class _TranscodeAttempt:
    name: str
    command: tuple[str, ...]


class TranscodeUnavailable(RuntimeError):
    pass


class TranscodeCancelled(TranscodeUnavailable):
    pass


class TranscodeService:
    """Create immutable browser-playable MP4 cache entries without touching sources."""

    def __init__(
        self,
        resolver: FFmpegResolver,
        cache_root: Path,
        *,
        max_concurrent: int = 2,
        prefer_hardware: bool | None = None,
    ):
        self.resolver = resolver
        self.cache_root = cache_root
        self.prefer_hardware = prefer_hardware is not False
        self.hardware = HardwareTranscoder(enabled=self.prefer_hardware)
        self._locks: dict[int, list] = {}
        self._locks_guard = threading.Lock()
        self._slots = threading.Semaphore(max(1, max_concurrent))
        self._native_profiles: dict[tuple[str, int, int], _MediaProfile] = {}

    @staticmethod
    def needs_transcode(source: Path) -> bool:
        return source.suffix.lower() not in BROWSER_NATIVE_SUFFIXES

    def requires_conversion(self, source: Path, *, session: str = "", registry=None) -> bool:
        """按容器和内部编码选择浏览器兼容路径，不生成整片缓存。"""
        if self.needs_transcode(source):
            return True
        if self.resolver.ffprobe() is None:
            return False
        profile = self._profile_for(source, session, registry)
        return profile is None or not self._browser_compatible(source, profile)

    def decode_order_timestamps(self, source: Path, *, session: str = "", registry=None) -> bool:
        """是不是只有时间戳错乱的那一类：有 B 帧却没有 ctts，码流本身浏览器解得出来。

        这类片源不必重编码，重建一份头就够了；其余不兼容的原因（编码、像素格式、容器）
        换不掉，只能转码。
        """
        if self.needs_transcode(source) or self.resolver.ffprobe() is None:
            return False
        profile = self._profile_for(source, session, registry)
        return bool(profile and profile.decode_order_timestamps
                    and self._decodes_in_browser(source, profile))

    def media_duration(self, source: Path, *, session: str = "", registry=None) -> float:
        """ffprobe 报的总时长（秒）；账本没记时长的片源靠它切片。探测不到返回 0。"""
        if self.resolver.ffprobe() is None:
            return 0.0
        try:
            profile = self._profile_for(source, session, registry)
        except OSError:
            return 0.0
        return profile.duration if profile is not None else 0.0

    def _profile_for(self, source: Path, session: str, registry) -> _MediaProfile | None:
        """按 (路径, 大小, mtime) 缓存探测结果；同一个文件只问 ffprobe 一次。"""
        stat = source.stat()
        key = (str(source), stat.st_size, stat.st_mtime_ns)
        with self._locks_guard:
            profile = self._native_profiles.get(key)
        if profile is None:
            profile = self._probe(source, session, registry,
                                  time.monotonic() + PROBE_TIMEOUT_SECONDS)
            if profile is not None:
                with self._locks_guard:
                    if len(self._native_profiles) >= 512:
                        self._native_profiles.pop(next(iter(self._native_profiles)))
                    self._native_profiles[key] = profile
        return profile

    def browser_path(
        self, asset_id: int, source: Path, *, session: str = "", registry=None,
    ) -> tuple[Path, bool]:
        profile = None
        if not self.needs_transcode(source):
            if self.resolver.ffprobe() is None:
                return source, False
            try:
                profile = self._profile_for(source, session, registry)
            except OSError as exc:
                raise TranscodeUnavailable("source unavailable") from exc
            if profile is not None and self._decodes_in_browser(source, profile):
                return source, False

        choice = self.resolver.ffmpeg()
        if choice is None:
            raise TranscodeUnavailable("ffmpeg unavailable")

        try:
            stat = source.stat()
        except OSError as exc:
            raise TranscodeUnavailable("source unavailable") from exc

        target = self.cache_root / f"{asset_id}-{stat.st_size}-{stat.st_mtime_ns}.mp4"
        if target.is_file() and target.stat().st_size:
            return target, True

        with self._lock_for(asset_id):
            if target.is_file() and target.stat().st_size:
                return target, True
            self.cache_root.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f"{target.stem}.{uuid.uuid4().hex}.tmp.mp4")
            deadline = time.monotonic() + TRANSCODE_TIMEOUT_SECONDS
            last_detail = ""
            completed = False
            try:
                with self._slots:
                    self._raise_if_cancelled(session, registry)
                    if profile is None:
                        profile = self._probe(source, session, registry, deadline)
                    for attempt in self._attempts(
                        choice.path, source, temporary, profile,
                    ):
                        temporary.unlink(missing_ok=True)
                        try:
                            returncode, _stdout, stderr = self._execute(
                                attempt.command,
                                session=session,
                                registry=registry,
                                deadline=deadline,
                                label=attempt.name,
                            )
                        except TranscodeCancelled:
                            raise
                        except (OSError, TranscodeUnavailable) as exc:
                            last_detail = f"{attempt.name}: {exc}"
                            if time.monotonic() >= deadline:
                                break
                            continue
                        if (returncode == 0 and temporary.is_file()
                                and temporary.stat().st_size):
                            temporary.replace(target)
                            logger.info("整片转码完成：%s", attempt.name)
                            completed = True
                            break
                        detail = (stderr or b"").decode("utf-8", "replace")[-1000:]
                        last_detail = f"{attempt.name}: {detail or 'ffmpeg failed'}"
                if not completed:
                    raise TranscodeUnavailable(last_detail or "ffmpeg failed")
            finally:
                temporary.unlink(missing_ok=True)

            for stale in self.cache_root.glob(f"{asset_id}-*.mp4"):
                if stale != target:
                    stale.unlink(missing_ok=True)
            return target, True

    @staticmethod
    def _browser_compatible(source: Path, profile: _MediaProfile) -> bool:
        if source.suffix.lower() in {".mp4", ".m4v"}:
            return (profile.video_codec == "h264"
                    and profile.pixel_format in DIRECT_MP4_PIXEL_FORMATS
                    and profile.audio_codec in {"", "aac", "mp3"}
                    and not profile.decode_order_timestamps)
        if source.suffix.lower() == ".webm":
            return (profile.video_codec in {"vp8", "vp9", "av1"}
                    and profile.audio_codec in {"", "opus", "vorbis"})
        return (profile.video_codec == "theora"
                and profile.audio_codec in {"", "vorbis", "opus"})

    def _probe(
        self, source: Path, session: str, registry, deadline: float,
    ) -> _MediaProfile | None:
        choice = self.resolver.ffprobe()
        if choice is None:
            return None
        command = (
            str(choice.path), "-v", "error", "-show_entries",
            "stream=codec_type,codec_name,pix_fmt,has_b_frames:format=duration",
            "-of", "json", str(source),
        )
        try:
            returncode, stdout, _stderr = self._execute(
                command,
                session=session,
                registry=registry,
                deadline=min(deadline, time.monotonic() + PROBE_TIMEOUT_SECONDS),
                label="ffprobe",
                capture_stdout=True,
            )
        except TranscodeCancelled:
            raise
        except (OSError, TranscodeUnavailable):
            return None
        if returncode:
            return None
        try:
            report = json.loads(stdout.decode("utf-8", "replace"))
            streams = report.get("streams", ())
        except (AttributeError, json.JSONDecodeError):
            return None
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
        codec = str(video.get("codec_name") or "").lower()
        if not codec:
            return None
        try:
            duration = float((report.get("format") or {}).get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        return _MediaProfile(
            video_codec=codec,
            pixel_format=str(video.get("pix_fmt") or "").lower(),
            audio_codec=str(audio.get("codec_name") or "").lower(),
            duration=max(0.0, duration),
            decode_order_timestamps=self._decode_order_timestamps(source, video),
        )

    @staticmethod
    def _decodes_in_browser(source: Path, profile: _MediaProfile) -> bool:
        """浏览器能不能解出画面。时间戳错乱不算解不出来。

        整片转码要占满一个编码位、写一份和原片同量级的缓存，只有编码本身不被支持才值得。
        缺 `ctts` 的片源画面解得出来，错的只是容器写的显示顺序，交给 HLS 转码分片按需修
        就够。卡片悬停预览直接打 `/stream`，鼠标划过一张卡片不能启动一次整片转码。
        """
        return TranscodeService._browser_compatible(
            source, replace(profile, decode_order_timestamps=False),
        )

    @staticmethod
    def _decode_order_timestamps(source: Path, video: dict) -> bool:
        """有 B 帧却没有 `ctts` 的 MP4，容器声明的显示时刻就是解码顺序。

        浏览器信容器的时间戳，凡是比已显示帧更早的一律丢掉，于是整片掉两成帧、看着
        持续卡顿；FFmpeg 和本地播放器按解码器输出顺序排，同一个文件反而正常。这类片子
        原样送给浏览器修不好，播放要走转码分片，让编码器重新写一份对的时间轴。

        判据放在探测里做一次，结论跟 profile 一起缓存：`moov` 动辄几 MB，每个分片请求
        都读一遍等于把挂载网盘反复拉一遍。
        """
        if source.suffix.lower() not in {".mp4", ".m4v"}:
            return False
        try:
            reorder_depth = int(video.get("has_b_frames") or 0)
        except (TypeError, ValueError):
            return False
        if reorder_depth <= 0:
            return False
        return composition_offsets_present(source) is False

    def _attempts(
        self,
        ffmpeg: Path,
        source: Path,
        temporary: Path,
        profile: _MediaProfile | None,
    ):
        prefix = (
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        )
        mapping = ("-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn")
        finish = ("-movflags", "+faststart", str(temporary))

        if (profile is not None and profile.video_codec == "h264"
                and profile.pixel_format in DIRECT_MP4_PIXEL_FORMATS):
            audio = (
                ("-c:a", "copy")
                if profile.audio_codec in {"", "aac"}
                else ("-c:a", "aac", "-b:a", "160k")
            )
            yield _TranscodeAttempt(
                "video-copy",
                prefix + ("-i", str(source)) + mapping + ("-c:v", "copy") + audio + finish,
            )

        fast_audio = (
            ("-c:a", "copy")
            if profile is not None and profile.audio_codec in {"", "aac"}
            else ("-c:a", "aac", "-b:a", "160k")
        )
        for encoder in self.hardware.encoders(ffmpeg):
            for attempt in encoder.attempts(profile.video_codec if profile else ""):
                yield _TranscodeAttempt(
                    attempt.name, prefix + attempt.before_input + ("-i", str(source))
                    + mapping + attempt.output + fast_audio + finish,
                )

        yield _TranscodeAttempt(
            "libx264",
            prefix + ("-i", str(source)) + mapping + (
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            ) + finish,
        )

    def _execute(
        self,
        command: tuple[str, ...],
        *,
        session: str,
        registry,
        deadline: float,
        label: str,
        capture_stdout: bool = False,
    ) -> tuple[int, bytes, bytes]:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        registered = False
        try:
            if registry is not None and session:
                registered = registry.register_process(session, process)
                if not registered:
                    process.kill()
                    process.communicate()
                    raise TranscodeCancelled(session)
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if registry is not None and session and registry.is_cancelled(session):
                        process.kill()
                        process.communicate()
                        raise TranscodeCancelled(session)
                    if time.monotonic() >= deadline:
                        process.kill()
                        process.communicate()
                        raise TranscodeUnavailable(f"{label} timed out")
            self._raise_if_cancelled(session, registry)
            return (
                process.returncode if process.returncode is not None else -1,
                stdout or b"",
                stderr or b"",
            )
        finally:
            if registered:
                registry.unregister_process(session, process)

    @staticmethod
    def _raise_if_cancelled(session: str, registry) -> None:
        if registry is not None and session and registry.is_cancelled(session):
            raise TranscodeCancelled(session)

    @contextmanager
    def _lock_for(self, asset_id: int):
        """同一 asset 的转码互斥；引用归零的条目随手清掉，长跑不再只增不减。

        清理之所以安全：任何等锁线程都先在 guard 里加过引用，持有者退出临界区、
        释放锁之后再减引用。减到零的那一刻不可能还有持有者或等待者，此时删除
        才不会让后来的线程拿到第二把锁绕过互斥。
        """
        with self._locks_guard:
            entry = self._locks.get(asset_id)
            if entry is None:
                entry = [threading.Lock(), 0]
                self._locks[asset_id] = entry
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._locks_guard:
                entry[1] -= 1
                if entry[1] <= 0:
                    self._locks.pop(asset_id, None)
