import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from peach.ffmpeg import BinaryChoice
from peach.hardware_transcode import HardwareEncoder
from peach.transcodes import TranscodeCancelled, TranscodeService, TranscodeUnavailable
from support.mp4 import minimal_mp4


class _Resolver:
    def __init__(self, binary: Path | None, probe: Path | None = None):
        self.binary = binary
        self.probe = probe

    def ffmpeg(self):
        return BinaryChoice(self.binary, "test") if self.binary else None

    def ffprobe(self):
        return BinaryChoice(self.probe, "test") if self.probe else None


class _FakeTranscode:
    """让 Popen 立即产出缓存文件的最小替身。"""

    returncode = 0

    def __init__(self, command, **_kwargs):
        self.command = command

    def communicate(self, timeout=None):
        Path(self.command[-1]).write_bytes(b"mp4")
        return b"", b""


def _media_process(commands, profile, fail=None, duration=None):
    class Process:
        def __init__(self, command, **_kwargs):
            self.command = list(command)
            self.returncode = 0
            commands.append(self.command)

        def communicate(self, timeout=None):
            if Path(self.command[0]).stem.lower() == "ffprobe":
                report = {"streams": profile}
                if duration is not None:
                    report["format"] = {"duration": duration}
                return json.dumps(report).encode(), b""
            if fail is not None and fail(self.command):
                self.returncode = 1
                return b"", b"hardware unavailable"
            Path(self.command[-1]).write_bytes(b"mp4")
            return b"", b""

    return Process


class TranscodeServiceTests(unittest.TestCase):
    def setUp(self):
        candidates = patch("peach.hardware_transcode.HardwareTranscoder._candidates",
                           return_value=(HardwareEncoder("nvenc"),))
        probe = patch("peach.hardware_transcode.HardwareTranscoder._probe", return_value=True)
        candidates.start()
        probe.start()
        self.addCleanup(candidates.stop)
        self.addCleanup(probe.stop)

    def test_native_containers_are_checked_against_their_streams(self):
        cases = (
            (".mp4", "mpeg4", "yuv420p", "aac", True),
            (".mp4", "hevc", "yuv420p10le", "aac", True),
            (".mp4", "h264", "yuv420p10le", "aac", True),
            (".mp4", "h264", "yuv420p", "ac3", True),
            (".mp4", "h264", "yuv420p", "aac", False),
            (".webm", "vp9", "yuv420p", "opus", False),
        )
        for suffix, codec, pixel_format, audio, expected in cases:
            with self.subTest(codec=codec, pixel_format=pixel_format, audio=audio):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp).resolve()
                    source = root / ("movie" + suffix)
                    source.write_bytes(b"source")
                    service = TranscodeService(
                        _Resolver(root / "ffmpeg.exe", root / "ffprobe.exe"),
                        root / "cache", prefer_hardware=False,
                    )
                    commands = []
                    streams = [
                        {"codec_type": "video", "codec_name": codec, "pix_fmt": pixel_format},
                        {"codec_type": "audio", "codec_name": audio},
                    ]
                    with patch("peach.transcodes.subprocess.Popen",
                               side_effect=_media_process(commands, streams)):
                        result = service.browser_path(29999, source)
                        self.assertEqual(service.browser_path(29999, source), result)
                    self.assertEqual(result[1], expected)
                    self.assertEqual(source.read_bytes(), b"source")
                    self.assertEqual(sum("ffprobe" in c[0] for c in commands), 1)
                    if expected:
                        self.assertEqual(result[0].read_bytes(), b"mp4")

    def test_b_frames_without_composition_offsets_are_sent_to_transcode(self):
        """有 B 帧却没有 ctts 的 MP4，容器声明的显示时刻是解码顺序。

        浏览器照单全收，凡是比已显示帧更早的一律丢掉，整片掉两成帧；本地播放器按解码器
        输出顺序排，同一个文件看着正常。没有 B 帧的片源本来就不需要这张表，缺了不算毛病。
        """
        cases = (
            (2, False, True),
            (2, True, False),
            (0, False, False),
        )
        for reorder_depth, composition_offsets, expected in cases:
            with self.subTest(has_b_frames=reorder_depth, ctts=composition_offsets):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp).resolve()
                    source = root / "movie.mp4"
                    source.write_bytes(minimal_mp4(
                        timescale=1000, sample_delta=40, samples=100, keyframe_every=25,
                        composition_offsets=composition_offsets))
                    service = TranscodeService(
                        _Resolver(root / "ffmpeg.exe", root / "ffprobe.exe"),
                        root / "cache", prefer_hardware=False,
                    )
                    commands = []
                    streams = [
                        {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p",
                         "has_b_frames": reorder_depth},
                        {"codec_type": "audio", "codec_name": "aac"},
                    ]
                    with patch("peach.transcodes.subprocess.Popen",
                               side_effect=_media_process(commands, streams)):
                        self.assertEqual(service.requires_conversion(source), expected)
                    entries = commands[0][commands[0].index("-show_entries") + 1]
                    self.assertIn("has_b_frames", entries)

    def test_a_broken_timestamp_table_never_starts_a_whole_file_transcode(self):
        """缺 ctts 的片源照原样发，播放由 HLS 转码分片修。

        整片转码写一份和原片同量级的缓存，只有编码本身不被支持才值得。卡片悬停预览
        直接打 `/stream`，鼠标划过一张卡片不能启动一次整片转码。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / "movie.mp4"
            source.write_bytes(minimal_mp4(
                timescale=1000, sample_delta=40, samples=100, keyframe_every=25))
            service = TranscodeService(
                _Resolver(root / "ffmpeg.exe", root / "ffprobe.exe"),
                root / "cache", prefer_hardware=False,
            )
            commands = []
            streams = [
                {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p",
                 "has_b_frames": 2},
                {"codec_type": "audio", "codec_name": "aac"},
            ]
            with patch("peach.transcodes.subprocess.Popen",
                       side_effect=_media_process(commands, streams)):
                self.assertTrue(service.requires_conversion(source))
                self.assertEqual(service.browser_path(7, source), (source, False))
            self.assertEqual(
                [c for c in commands if Path(c[0]).stem.lower() == "ffmpeg"], [])

    def test_native_mp4_is_returned_without_ffmpeg(self):
        source = Path("movie.mp4")
        service = TranscodeService(_Resolver(None), Path("cache"))
        self.assertEqual(service.browser_path(1, source), (source, False))

    def test_unsupported_container_requires_ffmpeg(self):
        service = TranscodeService(_Resolver(None), Path("cache"))
        with self.assertRaises(TranscodeUnavailable):
            service.browser_path(1, Path("movie.avi"))

    def test_probe_reports_the_container_duration_for_slicing(self):
        """账本没记时长的片源按 ffprobe 报的时长切片；同一个文件只探测一次，探测不到给 0。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / "movie.mp4"
            source.write_bytes(b"source")
            service = TranscodeService(
                _Resolver(root / "ffmpeg.exe", root / "ffprobe.exe"),
                root / "cache", prefer_hardware=False,
            )
            commands = []
            streams = [
                {"codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p"},
                {"codec_type": "audio", "codec_name": "mp3"},
            ]
            with patch("peach.transcodes.subprocess.Popen",
                       side_effect=_media_process(commands, streams, duration="2543.416667")):
                self.assertTrue(service.requires_conversion(source))
                self.assertAlmostEqual(service.media_duration(source), 2543.417, places=3)
            self.assertEqual(sum("ffprobe" in c[0] for c in commands), 1)
            self.assertIn("format=duration", commands[0][commands[0].index("-show_entries") + 1])
            self.assertEqual(service.media_duration(root / "missing.mp4"), 0.0)
            self.assertEqual(TranscodeService(_Resolver(None), root / "cache").media_duration(source), 0.0)

    def test_avi_is_transcoded_once_and_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "movie.avi"
            source.write_bytes(b"avi")
            service = TranscodeService(
                _Resolver(root / "ffmpeg.exe"), root / "cache", prefer_hardware=False,
            )

            class Process:
                returncode = 0

                def __init__(self, command, **_kwargs):
                    self.command = command

                def communicate(self, timeout=None):
                    Path(self.command[-1]).write_bytes(b"mp4")
                    return b"", b""

            with patch("peach.transcodes.subprocess.Popen", side_effect=Process) as execute:
                first, transcoded = service.browser_path(9, source)
                second, cached = service.browser_path(9, source)

            self.assertTrue(transcoded and cached)
            self.assertEqual(first, second)
            self.assertEqual(first.read_bytes(), b"mp4")
            self.assertEqual(execute.call_count, 1)
            self.assertIn("libx264", execute.call_args.args[0])

    def test_h264_aac_mkv_is_remuxed_without_video_or_audio_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "movie.mkv"
            source.write_bytes(b"mkv")
            service = TranscodeService(
                _Resolver(root / "ffmpeg.exe", root / "ffprobe.exe"),
                root / "cache",
                prefer_hardware=True,
            )
            commands = []
            profile = [
                {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"},
                {"codec_type": "audio", "codec_name": "aac"},
            ]

            with patch(
                "peach.transcodes.subprocess.Popen",
                side_effect=_media_process(commands, profile),
            ):
                path, transcoded = service.browser_path(9, source)

            self.assertTrue(transcoded and path.is_file())
            transcodes = [command for command in commands if "ffprobe" not in command[0]]
            self.assertEqual(len(transcodes), 1)
            self.assertEqual(transcodes[0][transcodes[0].index("-c:v") + 1], "copy")
            self.assertEqual(transcodes[0][transcodes[0].index("-c:a") + 1], "copy")
            self.assertNotIn("h264_nvenc", transcodes[0])
            self.assertNotIn("libx264", transcodes[0])

    def test_h264_with_incompatible_audio_copies_video_and_encodes_aac(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "movie.mkv"
            source.write_bytes(b"mkv")
            service = TranscodeService(
                _Resolver(root / "ffmpeg.exe", root / "ffprobe.exe"),
                root / "cache",
                prefer_hardware=True,
            )
            commands = []
            profile = [
                {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"},
                {"codec_type": "audio", "codec_name": "ac3"},
            ]

            with patch(
                "peach.transcodes.subprocess.Popen",
                side_effect=_media_process(commands, profile),
            ):
                service.browser_path(9, source)

            command = [item for item in commands if "ffprobe" not in item[0]][0]
            self.assertEqual(command[command.index("-c:v") + 1], "copy")
            self.assertEqual(command[command.index("-c:a") + 1], "aac")

    def test_hevc_prefers_cuda_decode_and_nvenc_encode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "movie.mkv"
            source.write_bytes(b"mkv")
            service = TranscodeService(
                _Resolver(root / "ffmpeg.exe", root / "ffprobe.exe"),
                root / "cache",
                prefer_hardware=True,
            )
            commands = []
            profile = [
                {"codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le"},
                {"codec_type": "audio", "codec_name": "aac"},
            ]

            with patch(
                "peach.transcodes.subprocess.Popen",
                side_effect=_media_process(commands, profile),
            ):
                service.browser_path(9, source)

            command = [item for item in commands if "ffprobe" not in item[0]][0]
            self.assertEqual(command[command.index("-hwaccel") + 1], "cuda")
            self.assertIn("scale_cuda=format=nv12", command)
            self.assertIn("h264_nvenc", command)
            self.assertIn("p1", command)
            self.assertNotIn("libx264", command)

    def test_nvenc_failure_falls_back_to_previous_libx264_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "movie.mkv"
            source.write_bytes(b"mkv")
            service = TranscodeService(
                _Resolver(root / "ffmpeg.exe", root / "ffprobe.exe"),
                root / "cache",
                prefer_hardware=True,
            )
            commands = []
            profile = [
                {"codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p"},
                {"codec_type": "audio", "codec_name": "aac"},
            ]

            with patch(
                "peach.transcodes.subprocess.Popen",
                side_effect=_media_process(
                    commands, profile, fail=lambda command: "h264_nvenc" in command,
                ),
            ):
                path, transcoded = service.browser_path(9, source)

            self.assertTrue(transcoded and path.is_file())
            transcodes = [command for command in commands if "ffprobe" not in command[0]]
            self.assertEqual(len(transcodes), 3)
            self.assertIn("-hwaccel", transcodes[0])
            self.assertIn("h264_nvenc", transcodes[1])
            self.assertIn("libx264", transcodes[2])

    def test_lock_entries_do_not_accumulate_after_transcodes(self):
        """每把 per-asset 锁在没人持有、没人等待时必须从字典里消失。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "movie.avi"
            source.write_bytes(b"avi")
            service = TranscodeService(
                _Resolver(root / "ffmpeg.exe"), root / "cache", prefer_hardware=False,
            )

            with patch("peach.transcodes.subprocess.Popen", side_effect=_FakeTranscode):
                service.browser_path(9, source)
            self.assertEqual(service._locks, {})

    def test_concurrent_transcodes_of_one_asset_stay_serialised(self):
        """清理之后同 asset 并发仍互斥：两个线程同时转码，FFmpeg 只真跑一次。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "movie.avi"
            source.write_bytes(b"avi")
            service = TranscodeService(
                _Resolver(root / "ffmpeg.exe"), root / "cache", prefer_hardware=False,
            )
            start = threading.Barrier(2)
            outcomes: list[bool] = []

            def worker():
                start.wait()
                _, transcoded = service.browser_path(9, source)
                outcomes.append(transcoded)

            with patch("peach.transcodes.subprocess.Popen",
                       side_effect=_FakeTranscode) as execute:
                threads = [threading.Thread(target=worker) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
            self.assertEqual(sorted(outcomes), [True, True])
            self.assertEqual(execute.call_count, 1,
                             "第二个线程应命中缓存或等锁，不得重复转码")
            self.assertEqual(service._locks, {})

    def test_different_assets_do_not_queue_behind_each_other(self):
        """一个资产占着锁转码时，另一个资产不得在锁上排队。

        照 test_previews 的教训，不靠调度器去证明"两个线程真的并行"：
        A 进入 ffmpeg 阻塞是事件通知的，那一刻 A 必然持有 asset 101 的锁，
        此后 B 才开始。若 per-asset 锁退化成一把全局锁，B 永远完不成，
        断言按超时确定性变红。max_concurrent 显式给 2：本条只管锁粒度，
        不给全局并发上限留变化空间。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_a = root / "a.avi"
            source_a.write_bytes(b"avi")
            source_b = root / "b.avi"
            source_b.write_bytes(b"avi")
            service = TranscodeService(
                _Resolver(root / "ffmpeg.exe"), root / "cache", prefer_hardware=False,
                max_concurrent=2,
            )
            inside_a = threading.Event()
            release_a = threading.Event()

            class _BlockingTranscode:
                returncode = 0

                def __init__(self, command, **_kwargs):
                    self.command = command

                def communicate(self, timeout=None):
                    if Path(self.command[-1]).stem.startswith("101-"):
                        inside_a.set()
                        release_a.wait(8)
                    Path(self.command[-1]).write_bytes(b"mp4")
                    return b"", b""

            outcome_b: list[tuple[Path, bool]] = []
            error_b: list[BaseException] = []

            def worker_b():
                try:
                    outcome_b.append(service.browser_path(202, source_b))
                except BaseException as exc:
                    error_b.append(exc)

            with patch("peach.transcodes.subprocess.Popen",
                       side_effect=_BlockingTranscode):
                thread_a = threading.Thread(
                    target=lambda: service.browser_path(101, source_a))
                thread_a.start()
                self.assertTrue(inside_a.wait(8), "A 应先进入转码并持有自己的锁")
                thread_b = threading.Thread(target=worker_b)
                thread_b.start()
                thread_b.join(8)
                release_a.set()
                thread_a.join(8)

            self.assertEqual(error_b, [])
            self.assertTrue(outcome_b and outcome_b[0][1],
                            "A 持锁期间 B 应当能完成自己的转码")
            self.assertFalse(thread_b.is_alive(),
                             "B 不得等 A 的锁——不同资产不应互相排队")
            self.assertEqual(service._locks, {})

    def test_session_cancellation_kills_an_active_transcode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "movie.avi"
            source.write_bytes(b"avi")
            service = TranscodeService(
                _Resolver(root / "ffmpeg.exe"), root / "cache", prefer_hardware=False,
            )

            class Process:
                returncode = None
                killed = False
                calls = 0

                def __init__(self, _command, **_kwargs):
                    pass

                def communicate(self, timeout=None):
                    self.calls += 1
                    if self.calls == 1:
                        raise subprocess.TimeoutExpired("ffmpeg", timeout)
                    return b"", b""

                def kill(self):
                    self.killed = True
                    self.returncode = -9

            process = Process([])

            class Registry:
                def register_process(self, session, registered):
                    self.registered = (session, registered)
                    return True

                def is_cancelled(self, _session):
                    return process.calls > 0

                def unregister_process(self, session, registered):
                    self.unregistered = (session, registered)

            registry = Registry()
            with patch("peach.transcodes.subprocess.Popen", return_value=process):
                with self.assertRaises(TranscodeCancelled):
                    service.browser_path(9, source, session="s1", registry=registry)
            self.assertTrue(process.killed)
            self.assertEqual(registry.registered, ("s1", process))
            self.assertEqual(registry.unregistered, ("s1", process))
