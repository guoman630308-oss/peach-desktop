"""硬件后端探测与整片、HLS 转码的失败回退。"""
import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from peach.ffmpeg import BinaryChoice
from peach.hardware_transcode import HardwareEncoder, HardwareTranscoder
from peach.segments import HlsSegmentService
from peach.streaming import StreamSessionRegistry
from peach.transcodes import TranscodeService, _MediaProfile


class HardwareDetectionTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"PEACH_TRANSCODE_BACKEND": "auto", "PEACH_VAAPI_DEVICE": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def test_linux_render_nodes_are_tried_until_one_encodes(self):
        commands = []

        def run(command, **kwargs):
            commands.append(command)
            self.assertEqual(kwargs["timeout"], 10)
            device = command[command.index("-vaapi_device") + 1]
            return subprocess.CompletedProcess(command, int(device.endswith("128")), stderr=b"no display")

        hardware = HardwareTranscoder()
        with patch("peach.hardware_transcode.sys.platform", "linux"), \
                patch.object(Path, "exists", return_value=False), \
                patch.object(Path, "glob", return_value=[Path("/dev/dri/renderD128"), Path("/dev/dri/renderD129")]), \
                patch("peach.hardware_transcode.subprocess.run", side_effect=run):
            encoders = hardware.encoders(Path("ffmpeg"))
            self.assertEqual(encoders, (HardwareEncoder("vaapi", "/dev/dri/renderD129"),))
            self.assertEqual(hardware.encoders(Path("ffmpeg")), encoders)
        self.assertEqual(len(commands), 2)
        self.assertTrue(all("h264_vaapi" in command for command in commands))
        self.assertTrue(all("format=nv12,hwupload" in command for command in commands))
        self.assertTrue(all(command[-2:] == ("null", "-") for command in commands))

    def test_explicit_device_and_backend_are_probed_without_gpu_listing(self):
        with patch.dict(os.environ, {"PEACH_TRANSCODE_BACKEND": "vaapi", "PEACH_VAAPI_DEVICE": "/dev/dri/renderD130"}):
            hardware = HardwareTranscoder()
        with patch("peach.hardware_transcode.subprocess.run", return_value=Mock(returncode=0)) as run:
            self.assertEqual(hardware.encoders(Path("ffmpeg")), (HardwareEncoder("vaapi", "/dev/dri/renderD130"),))
        self.assertIn("/dev/dri/renderD130", run.call_args.args[0])
        self.assertNotIn("h264_nvenc", run.call_args.args[0])

    def test_probe_errors_and_timeouts_are_cached_as_unavailable(self):
        for failure in (PermissionError("denied"), subprocess.TimeoutExpired("ffmpeg", 10)):
            with self.subTest(failure=failure), patch.dict(os.environ, {"PEACH_TRANSCODE_BACKEND": "nvenc"}):
                hardware = HardwareTranscoder()
                with patch("peach.hardware_transcode.subprocess.run", side_effect=failure) as run:
                    self.assertEqual(hardware.encoders(Path("ffmpeg")), ())
                    self.assertEqual(hardware.encoders(Path("ffmpeg")), ())
                    self.assertEqual(run.call_count, 1)

    def test_software_mode_and_disabled_preference_do_not_probe(self):
        for enabled, backend in ((True, "software"), (False, "auto")):
            with self.subTest(enabled=enabled, backend=backend), \
                    patch.dict(os.environ, {"PEACH_TRANSCODE_BACKEND": backend}), \
                    patch("peach.hardware_transcode.subprocess.run") as run:
                self.assertEqual(HardwareTranscoder(enabled=enabled).encoders(Path("ffmpeg")), ())
                run.assert_not_called()

    def test_linux_without_devices_and_macos_have_no_automatic_candidates(self):
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform), patch("peach.hardware_transcode.sys.platform", platform), \
                    patch.object(Path, "exists", return_value=False), patch.object(Path, "glob", return_value=[]):
                self.assertEqual(HardwareTranscoder()._candidates(), ())

    def test_linux_nvidia_device_enables_nvenc_probe(self):
        with patch("peach.hardware_transcode.sys.platform", "linux"), \
                patch.object(Path, "exists", return_value=True), patch.object(Path, "glob", return_value=[]), \
                patch("peach.hardware_transcode.subprocess.run", return_value=Mock(returncode=0)) as run:
            self.assertEqual(HardwareTranscoder().encoders(Path("ffmpeg")), (HardwareEncoder("nvenc"),))
        self.assertIn("h264_nvenc", run.call_args.args[0])

    def test_invalid_backend_has_an_actionable_error(self):
        with patch.dict(os.environ, {"PEACH_TRANSCODE_BACKEND": "typo"}):
            with self.assertRaisesRegex(ValueError, "PEACH_TRANSCODE_BACKEND"):
                HardwareTranscoder()


class VaapiConsumerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / "clip.mkv"
        self.source.write_bytes(b"source")
        self.resolver = Mock(ffmpeg=lambda: BinaryChoice(Path("ffmpeg"), "test"), ffprobe=lambda: None)
        environment = patch.dict(os.environ, {"PEACH_TRANSCODE_BACKEND": "vaapi", "PEACH_VAAPI_DEVICE": "/dev/dri/renderD128"})
        environment.start()
        self.addCleanup(environment.stop)
        probe = patch.object(HardwareTranscoder, "_probe", return_value=True)
        probe.start()
        self.addCleanup(probe.stop)

    def test_full_file_tries_vaapi_then_hardware_encode_then_software(self):
        for failures, expected in ((0, ["vaapi"]), (1, ["vaapi", "vaapi-encode"]),
                                   (2, ["vaapi", "vaapi-encode", "libx264"])):
            with self.subTest(failures=failures):
                service = TranscodeService(self.resolver, self.root / f"cache{failures}")
                captured = []

                def execute(command, *, captured=captured, failures=failures, **kwargs):
                    captured.append((kwargs["label"], command))
                    if len(captured) <= failures:
                        return 1, b"", b"hardware failed"
                    Path(command[-1]).write_bytes(b"mp4")
                    return 0, b"", b""

                profile = _MediaProfile("hevc", "yuv420p10le", "aac")
                with patch.object(service, "_probe", return_value=profile), \
                        patch.object(service, "_execute", side_effect=execute):
                    target, converted = service.browser_path(95, self.source)
                self.assertTrue(converted)
                self.assertEqual(target.read_bytes(), b"mp4")
                self.assertEqual([name for name, _ in captured], expected)
                command = captured[0][1]
                self.assertLess(command.index("-hwaccel"), command.index("-i"))
                self.assertIn("scale_vaapi=format=nv12", command)
                self.assertIn("h264_vaapi", command)
                self.assertIn("+faststart", command)
                if failures:
                    self.assertNotIn("-hwaccel", captured[1][1])
                    self.assertIn("format=nv12,hwupload", captured[1][1])

    def test_video_copy_does_not_probe_hardware(self):
        service = TranscodeService(self.resolver, self.root / "cache")
        commands = service._attempts(Path("ffmpeg"), self.source, self.root / "out.mp4",
                                     _MediaProfile("h264", "yuv420p", "aac"))
        with patch.object(service.hardware, "encoders") as encoders:
            self.assertEqual(next(commands).name, "video-copy")
            encoders.assert_not_called()

    def test_hls_vaapi_failures_preserve_seek_timestamps_and_audio(self):
        async def drive(failures):
            service = HlsSegmentService(self.resolver, self.root / f"hls{failures}",
                                        hardware=HardwareTranscoder())
            captured = []

            async def run(command, session, registry):
                captured.append(command)
                if len(captured) <= failures:
                    return 1, b"hardware failed"
                Path(command[-1]).write_bytes(b"ts")
                return 0, b""

            with patch.object(service, "_run", side_effect=run), \
                    patch.object(service, "video_codec", return_value="hevc"):
                target = await service.generate(self.source, 12, 6, asset_id=95, index=2,
                                                session="test", registry=StreamSessionRegistry(), transcode=True)
            self.assertEqual(target.read_bytes(), b"ts")
            self.assertEqual(len(captured), failures + 1)
            for command in captured:
                self.assertEqual(command[command.index("-ss") + 1], "12.000")
                self.assertEqual(command[command.index("-t") + 1], "6.000")
                self.assertEqual(command[command.index("-output_ts_offset") + 1], "12.000")
                self.assertEqual(command[command.index("-c:a") + 1], "aac")
                self.assertEqual(command[command.index("-f") + 1], "mpegts")
            self.assertIn("scale_vaapi=format=nv12", captured[0])
            if failures:
                self.assertIn("format=nv12,hwupload", captured[1])
            if failures == 2:
                self.assertIn("libx264", captured[2])

        for failures in (0, 1, 2):
            with self.subTest(failures=failures):
                asyncio.run(drive(failures))

    def test_hls_remux_does_not_probe_hardware(self):
        service = HlsSegmentService(self.resolver, self.root / "hls", hardware=HardwareTranscoder())
        with patch.object(service.hardware, "encoders") as encoders:
            attempts = service._attempts(Path("ffmpeg"), self.source, 0, 6, False)
        encoders.assert_not_called()
        self.assertEqual([name for name, _ in attempts], ["copy"])
