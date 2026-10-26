"""按时间点抽单帧、把若干帧拼成一张接触印相。九宫格与时间轴预览共用这一份。"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from peach import frame_capture
from peach.ffmpeg import FFmpegResolver
from support.conditions import missing_prerequisite


class _Completed:
    def __init__(self, stderr: bytes = b""):
        self.stderr = stderr
        self.returncode = 0


class CaptureFrameTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="frame-capture-")).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.commands: list[list[str]] = []

    def _run(self, produce: bool = True, stderr: bytes = b""):
        def fake(command, **options):
            self.assertEqual(options["creationflags"],
                             subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.commands.append(list(command))
            if produce:
                Image.new('RGB', (480, 270), 'green').save(command[-1], format='JPEG')
            return _Completed(stderr)
        return patch.object(frame_capture.subprocess, "run", fake)

    def test_the_seek_happens_before_the_input_so_only_that_part_is_decoded(self):
        """`-ss` 放到 `-i` 后面就是从头解码到那个时间点，两小时的片子抽最后一帧要几分钟。"""
        with self._run():
            ok, _ = frame_capture.capture_frame("ffmpeg", "movie.mp4", 61.5,
                                                self.root / "one.jpg", width=320)
        self.assertTrue(ok)
        command = self.commands[0]
        self.assertLess(command.index("-ss"), command.index("-i"))
        self.assertIn("scale=320:-1", command)
        self.assertNotIn("bt709", command)

    def test_a_frame_too_small_to_be_a_picture_does_not_count_as_taken(self):
        """命令退出 0 不等于出了图。写了个几百字节的壳，页面上就是一块灰。"""
        def fake(command, **_kwargs):
            Path(command[-1]).write_bytes(b"x" * 64)
            return _Completed()
        with patch.object(frame_capture.subprocess, "run", fake):
            ok, _ = frame_capture.capture_frame("ffmpeg", "movie.mp4", 1.0,
                                                self.root / "small.jpg")
        self.assertFalse(ok)

    def test_a_timeout_is_reported_as_a_failure_not_an_exception(self):
        def fake(_command, **_kwargs):
            raise subprocess.TimeoutExpired("ffmpeg", 45)
        with patch.object(frame_capture.subprocess, "run", fake):
            ok, stderr = frame_capture.capture_frame("ffmpeg", "movie.mp4", 1.0,
                                                     self.root / "late.jpg")
        self.assertFalse(ok)
        self.assertEqual(stderr, "ffmpeg timeout")

    def test_small_decodable_frame_counts_as_a_picture(self):
        def fake(command, **_kwargs):
            Image.new('RGB', (32, 32), 'black').save(command[-1], format='JPEG')
            return _Completed()
        destination = self.root / 'small-valid.jpg'
        with patch.object(frame_capture.subprocess, 'run', fake):
            ok, _ = frame_capture.capture_frame('ffmpeg', 'movie.mp4', 1, destination)
        self.assertTrue(ok)
        self.assertLess(destination.stat().st_size, 1024)

    def test_nonzero_exit_rejects_and_removes_a_partial_picture(self):
        def fake(command, **_kwargs):
            Image.new('RGB', (480, 270)).save(command[-1], format='JPEG')
            result = _Completed(b'decoder failed')
            result.returncode = 1
            return result
        detail = {}
        destination = self.root / 'partial.jpg'
        with patch.object(frame_capture.subprocess, 'run', fake):
            self.assertFalse(frame_capture.capture_with_retry(
                'ffmpeg', 'movie.mp4', 5, destination, diagnostics=detail))
        self.assertFalse(destination.exists())
        self.assertEqual(detail['returncode'], 1)
        self.assertEqual(detail['stderr'], 'decoder failed')
        self.assertEqual(detail['timestamp_seconds'], 5)

    def test_timeout_preserves_stderr_and_cleans_the_output(self):
        destination = self.root / 'timed-out.jpg'
        def fake(*_args, **_kwargs):
            destination.write_bytes(b'partial')
            raise subprocess.TimeoutExpired('ffmpeg', 45, stderr=b'decode stalled')
        detail = {}
        with patch.object(frame_capture.subprocess, 'run', fake):
            self.assertFalse(frame_capture.capture_with_retry(
                'ffmpeg', 'movie.mp4', 8, destination, diagnostics=detail))
        self.assertFalse(destination.exists())
        self.assertEqual(detail['reason'], 'timeout')
        self.assertEqual(detail['stderr'], 'decode stalled')
        self.assertEqual(len(detail['attempts']), 1)

    def test_color_retry_keeps_both_attempts(self):
        detail = {}
        with self._run(produce=False, stderr=b'Unsupported color primaries: reserved'):
            self.assertFalse(frame_capture.capture_with_retry(
                'ffmpeg', 'movie.mp4', 1, self.root / 'color.jpg', diagnostics=detail))
        self.assertEqual([item['color_override'] for item in detail['attempts']], [False, True])

    def test_an_existing_frame_cannot_mask_an_empty_output(self):
        destination = self.root / 'stale.jpg'
        Image.new('RGB', (480, 270)).save(destination)
        detail = {}
        with self._run(produce=False):
            self.assertFalse(frame_capture.capture_with_retry(
                'ffmpeg', 'movie.mp4', 1, destination, diagnostics=detail))
        self.assertEqual(detail['reason'], 'no_frame')
        self.assertFalse(destination.exists())

    def test_only_bad_color_metadata_is_worth_a_second_try(self):
        """无条件重试会让网盘超时这类必然失败的文件每帧白跑第二次：单帧最坏耗时从
        45 秒翻到 90 秒，9 帧就是 13.5 分钟。"""
        for stderr, attempts in (
            (b"[swscale] Unsupported color primaries: reserved", 2),
            (b"color_trc reserved is invalid", 2),
            (b"Error opening input: Input/output error", 1),
            (b"", 1),
        ):
            self.commands = []
            with self._run(produce=False, stderr=stderr):
                ok = frame_capture.capture_with_retry("ffmpeg", "movie.mp4", 1.0,
                                                      self.root / "retry.jpg")
            self.assertFalse(ok)
            self.assertEqual(len(self.commands), attempts, stderr)
            if attempts == 2:
                self.assertIn("bt709", self.commands[1])


class TileFramesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="frame-tile-")).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_the_grid_size_reaches_the_filter(self):
        seen: list[list[str]] = []

        def fake(command, **options):
            self.assertEqual(options["creationflags"],
                             subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            seen.append(list(command))
            Image.new('RGB', (96, 96), 'black').save(command[-1], format='JPEG')
            return _Completed()

        with patch.object(frame_capture.subprocess, "run", fake):
            made = frame_capture.tile_frames("ffmpeg", str(self.root / "s%03d.jpg"), 10, 7,
                                             self.root / "out" / "000.jpg")
        self.assertTrue(made)
        self.assertIn("tile=10x7", seen[0])

    def test_a_tile_that_did_not_land_is_not_reported_as_made(self):
        with patch.object(frame_capture.subprocess, "run",
                          lambda command, **_kwargs: _Completed()):
            made = frame_capture.tile_frames("ffmpeg", str(self.root / "s%03d.jpg"), 3, 3,
                                             self.root / "out" / "000.jpg")
        self.assertFalse(made)


class FFmpegCaptureIntegrationTests(unittest.TestCase):
    def test_av1_software_decode_uses_the_desktop_release_gate(self):
        from scripts.smoke_desktop import assert_av1_decoder
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            choice = FFmpegResolver(root).ffmpeg()
            if choice is None:
                missing_prerequisite('AV1 解码回归需要 FFmpeg')
            assert_av1_decoder(choice.path, root)

    def test_real_small_jpegs_and_unicode_paths_produce_a_grid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            choice = FFmpegResolver(root).ffmpeg()
            if choice is None:
                missing_prerequisite('抽帧回归需要 FFmpeg')
            ffmpeg = str(choice.path)
            source = root / '中文 空格 (1).mp4'
            subprocess.run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=c=black:s=320x180:r=5:d=1', '-c:v', 'mpeg4', str(source)],
                           capture_output=True, check=True, timeout=20)
            for index in range(9):
                detail = {}
                self.assertTrue(frame_capture.capture_with_retry(
                    ffmpeg, str(source), .1, root / f's{index:02d}.jpg', diagnostics=detail), detail)
            self.assertLess((root / 's00.jpg').stat().st_size, 1024)
            target = root / 'grid.jpg'
            self.assertTrue(frame_capture.tile_frames(ffmpeg, str(root / 's%02d.jpg'), 3, 3, target))
            with Image.open(target) as image:
                self.assertEqual(image.size, (1440, 810))
            detail = {}
            self.assertFalse(frame_capture.capture_with_retry(
                ffmpeg, str(source), 10, root / 'beyond.jpg', diagnostics=detail))
            self.assertIn(detail['reason'], ('no_frame', 'ffmpeg_error'))
            self.assertIsInstance(detail['returncode'], int)


if __name__ == "__main__":
    unittest.main()
