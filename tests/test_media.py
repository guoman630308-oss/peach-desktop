import os
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import peach.media as media_module
from peach.ffmpeg import BinaryChoice, FFmpegResolver, binary_diagnostics
from peach.media import (
    FilesystemBackend, MediaEngine, MediaUnavailable, normalized_path,
    resolve_case_insensitive,
)
from peach.repository import MediaAsset


class MediaEngineTests(unittest.TestCase):
    def test_tool_diagnostics_distinguishes_software_av1_and_hardware_only(self):
        for decoder, available in (('av1', False), ('libdav1d', True)):
            results = [subprocess.CompletedProcess([], 0, b'ffmpeg version test\n', b''),
                       subprocess.CompletedProcess([], 0, f' V..... {decoder} AV1 decoder\n'.encode(), b'')]
            with patch('peach.ffmpeg.subprocess.run', side_effect=results):
                result = binary_diagnostics(BinaryChoice(Path('ffmpeg'), 'test'), decoders=True)
            self.assertTrue(result['usable'])
            self.assertEqual(result['av1_software'], available)

    def test_tool_diagnostics_reports_execution_failure(self):
        with patch('peach.ffmpeg.subprocess.run', side_effect=subprocess.TimeoutExpired('ffmpeg', 5)):
            result = binary_diagnostics(BinaryChoice(Path('ffmpeg'), 'test'), decoders=True)
        self.assertTrue(result['available'])
        self.assertFalse(result['usable'])
        self.assertEqual(result['error'], 'TimeoutExpired')

    # 临时目录一律先 resolve 再喂给被测代码。媒体解析本身就以 resolve 后的真实路径
    # 判断是否越过根目录，而 CI runner 的临时目录都是别名：macOS 的 /var 是
    # /private/var 的软链，Windows runner 的 %TEMP% 是 C:\Users\RUNNER~1 这种 8.3
    # 短名，展开后是 runneradmin。拿未 resolve 的路径做断言，本机全绿、CI 全红。
    def test_filesystem_backend_enforces_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            inside = root / "media" / "one.mp4"
            outside = root.parent / "outside.mp4"
            inside.parent.mkdir()
            inside.write_bytes(b"test")
            backend = FilesystemBackend([root], root / "snapshots")
            self.assertEqual(backend.file_for(MediaAsset(1, str(inside), None)), inside)
            with self.assertRaises(MediaUnavailable):
                backend.file_for(MediaAsset(2, str(outside), None))

    def test_engine_resolves_through_the_filesystem_only(self):
        """媒体解析没有第二条来源。

        2026-09-01 关掉 Stash adapter 之前，这里还有一层 backend 契约。它在生产里
        一次都没有生效过：`media_binding` 表是空的，`external_id("stash")` 永远返回
        None，而 `stream_candidates` 本身没有任何调用方。见 ADR-0021。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            media = root / "one.mp4"
            media.write_bytes(b"test")
            repository = unittest.mock.Mock()
            repository.media_asset.return_value = MediaAsset(1, str(media), None)
            engine = MediaEngine(repository, FilesystemBackend([root], root / "snapshots"))
            self.assertEqual(engine.file_for(1), media)

    def test_engine_raises_for_unknown_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = unittest.mock.Mock()
            repository.media_asset.return_value = None
            engine = MediaEngine(
                repository,
                FilesystemBackend([root], root / "snapshots"),
            )
            with self.assertRaises(Exception) as raised:
                engine.file_for(404)
        self.assertEqual(type(raised.exception).__name__, "MediaNotFound")

    def test_ffmpeg_resolver_prefers_explicit_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "ffmpeg.exe"
            binary.write_bytes(b"test")
            resolver = FFmpegResolver(root)
            with patch.dict("os.environ", {"PEACH_FFMPEG": str(binary)}, clear=False):
                choice = resolver.ffmpeg()
        self.assertEqual(choice.source, "environment")

    def test_ffmpeg_resolver_uses_managed_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            binary.parent.mkdir()
            binary.write_bytes(b"test")
            with patch.dict("os.environ", {"PEACH_FFMPEG": ""}, clear=False), \
                    patch("peach.ffmpeg.shutil.which", return_value=None):
                choice = FFmpegResolver(root).ffmpeg()
        self.assertEqual(choice.source, "peach-managed")

    def test_frozen_application_uses_both_bundled_tools_without_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            bundle = root / "app" / "tools" / "ffmpeg" / "bin"
            bundle.mkdir(parents=True)
            suffix = ".exe" if os.name == "nt" else ""
            for name in ("ffmpeg", "ffprobe"):
                (bundle / (name + suffix)).write_bytes(b"tool")
            with patch.object(sys, "frozen", True, create=True), \
                    patch.object(sys, "executable", str(root / "app" / "Peach.exe")), \
                    patch.dict(os.environ, {"PEACH_FFMPEG": "", "PEACH_FFPROBE": ""}), \
                    patch("peach.ffmpeg.shutil.which", return_value=None):
                resolver = FFmpegResolver(root / "data")
                for choice in (resolver.ffmpeg(), resolver.ffprobe()):
                    self.assertEqual(choice.source, "peach-bundled")
                    self.assertEqual(choice.path.parent, bundle)

    def test_source_environment_does_not_use_tools_beside_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            tool = root / "tools" / "ffmpeg" / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            tool.parent.mkdir(parents=True)
            tool.write_bytes(b"tool")
            with patch.object(sys, "frozen", False, create=True), \
                    patch.object(sys, "executable", str(root / "python.exe")), \
                    patch.dict(os.environ, {"PEACH_FFMPEG": ""}), \
                    patch("peach.ffmpeg.shutil.which", return_value=None):
                self.assertIsNone(FFmpegResolver(root / "data").ffmpeg())

    def test_offline_windows_root_does_not_block_startup(self):
        with patch.object(Path, "resolve", side_effect=OSError("offline")):
            path = normalized_path(Path("B:/"))
        self.assertTrue(path.is_absolute())

    @unittest.skipUnless(os.name == "nt", "只有 Windows 的盘符路径跳过 resolve")
    def test_windows_ledger_paths_are_not_resolved_through_the_redirector(self):
        """PikPak 的 A: 是 WinFsp 网络驱动器：`resolve()` 一条文件路径要 7 秒，还换成 UNC 形态，
        之后每次 stat 再花 7 到 14 秒；盘符路径本身就是绝对路径，只需要折掉 `..`。"""
        with patch.object(Path, "resolve", side_effect=AssertionError("resolve() 走了网络重定向器")):
            path = normalized_path(r"A:\番号\..\番号\clip.mp4")
        self.assertEqual(path, Path(r"A:\番号\clip.mp4"))

    def test_filesystem_backend_matches_case_insensitively_on_sensitive_mounts(self):
        """CloudDrive 大小写敏感：账本 `abw-118.mp4` 对磁盘 `ABW-118.mp4` 必须救回。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "ABW-118.MP4"
            real.write_bytes(b"test")
            backend = FilesystemBackend([root], root / "snapshots")
            opened = backend.file_for(MediaAsset(1, str(root / "abw-118.mp4"), None))
            self.assertTrue(opened.is_file())
            self.assertEqual(opened.name.casefold(), "abw-118.mp4")
            # 完全缺失的名字仍应拒绝
            with self.assertRaises(MediaUnavailable):
                backend.file_for(MediaAsset(2, str(root / "nope.mp4"), None))

    def test_batch_path_resolution_translates_ledger_drive_before_case_matching(self):
        """sheets/probe 不经过 FilesystemBackend，也必须先翻译账本盘符。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "ABW-118.MP4"
            real.write_bytes(b"test")
            with patch(
                "peach.media.translate_ledger_path",
                return_value=root / "abw-118.mp4",
            ):
                opened = Path(resolve_case_insensitive(r"A:\\ABW-118.mp4"))
            self.assertTrue(opened.is_file())
            self.assertTrue(opened.parent.samefile(root))

    def test_case_insensitive_miss_expires_after_the_short_ttl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("peach.media.monotonic", return_value=0):
                self.assertIsNone(
                    media_module._case_insensitive_match(str(root), "abw-118.mp4")
                )
            real = root / "ABW-118.MP4"
            real.write_bytes(b"test")
            with patch("peach.media.monotonic", return_value=6):
                self.assertEqual(
                    media_module._case_insensitive_match(str(root), "abw-118.mp4"),
                    real.name,
                )

    def test_remote_mp4_defaults_to_range_and_only_segments_on_request(self):
        """默认 HLS 会让 HEVC 静默黑屏：`-c copy` 把 HEVC 装进 MPEG-TS，而 Chromium 的
        MSE 不支持 TS 里的 HEVC（实测 `video/mp2t; codecs="hvc1…"` 为 false），数据进得了
        缓冲却一帧都解不出，也没有 error 事件。同一浏览器直接 Range 播同一文件能出帧。
        见 ADR-0016。
        """
        repository = unittest.mock.Mock()
        repository.media_asset.return_value = MediaAsset(
            1, r"B:\video\one.mp4", None, "115", "one.mp4", 31.5, 100,
        )
        engine = MediaEngine(
            repository, FilesystemBackend([Path("B:/")], Path("snapshots")),
        )
        self.assertEqual(engine.stream_plan(1).protocol, "range")
        self.assertEqual(engine.stream_plan(1, mode="auto").protocol, "range")
        plan = engine.stream_plan(1, mode="hls")
        self.assertEqual(plan.protocol, "hls")
        self.assertEqual(plan.segment_seconds, 6)

    def test_hls_stays_unavailable_where_it_never_applied(self):
        """按需模式不是万能开关：本地来源和时长未知的片源仍然只能走 Range。"""
        repository = unittest.mock.Mock()
        repository.media_asset.return_value = MediaAsset(
            1, r"R:\video\one.mp4", None, "local", "one.mp4", 31.5, 100,
        )
        engine = MediaEngine(repository, FilesystemBackend([Path("R:/")], Path("snapshots")))
        self.assertEqual(engine.stream_plan(1, mode="hls").protocol, "range")

        repository.media_asset.return_value = MediaAsset(
            1, r"B:\video\one.mp4", None, "115", "one.mp4", None, 100,
        )
        engine = MediaEngine(repository, FilesystemBackend([Path("B:/")], Path("snapshots")))
        self.assertEqual(engine.stream_plan(1, mode="hls").protocol, "range")

    def test_local_or_unknown_duration_keeps_range_plan(self):
        repository = unittest.mock.Mock()
        repository.media_asset.return_value = MediaAsset(
            1, r"R:\video\one.mp4", None, "local", "one.mp4", None, 100,
        )
        engine = MediaEngine(
            repository, FilesystemBackend([Path("R:/")], Path("snapshots")),
        )
        self.assertEqual(engine.stream_plan(1).protocol, "range")


if __name__ == "__main__":
    unittest.main()
