"""HLS 分片：关键帧对齐、时间戳连续、播放列表报真实时长。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from peach.mp4index import (
    composition_offsets_present, keyframe_seconds, movie_seconds, segment_plan,
)
from peach.hardware_transcode import HardwareEncoder
from peach.segments import HlsSegmentService, StreamSessionRegistry, build_hls_playlist
from support.mp4 import minimal_mp4


class Mp4IndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, payload: bytes, name="clip.mp4") -> Path:
        path = self.root / name
        path.write_bytes(payload)
        return path

    def test_keyframe_times_come_from_the_sync_sample_table(self):
        # 1000 刻度、每样本 40 刻度（25fps），每 25 个样本一个关键帧 = 每秒一个。
        path = self.write(minimal_mp4(timescale=1000, sample_delta=40, samples=250, keyframe_every=25))
        self.assertEqual(keyframe_seconds(path)[:5], [0.0, 1.0, 2.0, 3.0, 4.0])

    def test_moov_at_the_end_is_still_found(self):
        """非 faststart 的文件把 moov 放在结尾；顺序读到它可能要拉几个 GB。"""
        full = minimal_mp4(timescale=1000, sample_delta=40, samples=100, keyframe_every=25)
        moov_start = full.index(b"moov") - 4
        moov_size = int.from_bytes(full[moov_start:moov_start + 4], "big")
        moov = full[moov_start:moov_start + moov_size]
        rest = full[:moov_start] + full[moov_start + moov_size:]
        self.assertEqual(keyframe_seconds(self.write(rest + moov))[:3], [0.0, 1.0, 2.0])

    def test_unparsable_input_returns_none_so_callers_fall_back_to_range(self):
        self.assertIsNone(keyframe_seconds(self.write(b"not an mp4 at all")))
        self.assertIsNone(keyframe_seconds(self.root / "missing.mp4"))

    def test_composition_offsets_are_read_from_the_video_track(self):
        """有 B 帧却没有 ctts 的片源要改走转码，所以这张表在不在必须答得准。"""
        plain = self.write(minimal_mp4(
            timescale=1000, sample_delta=40, samples=100, keyframe_every=25))
        reordered = self.write(minimal_mp4(
            timescale=1000, sample_delta=40, samples=100, keyframe_every=25,
            composition_offsets=True), name="reordered.mp4")
        self.assertIs(composition_offsets_present(plain), False)
        self.assertIs(composition_offsets_present(reordered), True)

    def test_unreadable_moov_answers_none_instead_of_guessing(self):
        """读不出 moov 就不知道有没有这张表；猜「没有」会把好片源推去转码。"""
        self.assertIsNone(composition_offsets_present(self.write(b"not an mp4 at all")))
        self.assertIsNone(composition_offsets_present(self.root / "missing.mp4"))

    def test_movie_duration_is_read_from_a_truncated_file_head(self):
        """远端视频只取文件头：`moov` 被截在一半也读得出 `mvhd`，放在结尾的读不出。"""
        full = minimal_mp4(timescale=1000, sample_delta=40, samples=250, keyframe_every=25)
        head = full[:full.index(b"mvhd") + 120]
        self.assertEqual(movie_seconds(head), 10.0)
        moov_start = full.index(b"moov") - 4
        moov_size = int.from_bytes(full[moov_start:moov_start + 4], "big")
        tail_moov = full[:moov_start] + full[moov_start + moov_size:] + full[moov_start:][:moov_size]
        self.assertIsNone(movie_seconds(tail_moov[:len(head)]))
        self.assertIsNone(movie_seconds(b"<html>not a video</html>"))

    def test_plan_snaps_to_keyframes_and_covers_the_whole_duration(self):
        keyframes = [round(value * 8.33, 2) for value in range(12)]
        plan = segment_plan(keyframes, 100.0, 6)
        self.assertTrue(all(start in keyframes for start, _ in plan),
                        "每段起点都必须落在真实关键帧上，否则 -c copy 切不准")
        self.assertTrue(all(length >= 6 for _, length in plan[:-1]))
        self.assertAlmostEqual(plan[0][0], 0.0)
        self.assertAlmostEqual(plan[-1][0] + plan[-1][1], 100.0, places=6)

    def test_plan_starts_at_zero_even_when_the_first_keyframe_is_late(self):
        plan = segment_plan([3.0, 9.0, 15.0], 20.0, 6)
        self.assertAlmostEqual(plan[0][0], 0.0)
        self.assertAlmostEqual(plan[-1][0] + plan[-1][1], 20.0, places=6)

    def test_playlist_reports_real_segment_durations(self):
        """按 6 秒等分声明会和关键帧实际切点对不上，播放器进度条就错位。"""
        plan = [(0.0, 8.33), (8.33, 8.33), (16.66, 3.34)]
        playlist = build_hls_playlist(plan, lambda index: f"/s/{index}.ts")
        self.assertIn("#EXTINF:8.330,", playlist)
        self.assertIn("#EXTINF:3.340,", playlist)
        self.assertIn("#EXT-X-TARGETDURATION:8", playlist)
        self.assertNotIn("#EXTINF:6.000,", playlist)
        self.assertEqual(playlist.count("/s/"), 3)
        self.assertIn("#EXT-X-ENDLIST", playlist)


if __name__ == "__main__":
    unittest.main()


class SegmentCommandTests(unittest.TestCase):
    """片段的时间窗必须写成绝对终点，否则只有开头两段能产出。

    真实故障：`-copyts` 保留原始时间轴后，`-t` 被 FFmpeg 当成绝对结束时刻而非片段时长。
    每段的 `-t` 都约等于一个片段长，于是起点超过它的片段一律「已经过期」，FFmpeg 以
    退出码 0、空 stderr 写出 0 字节，服务端只报一句没有内容的 ffmpeg failed。
    实测 asset 6562：片段 0 与 1 返回 200，片段 2 和 300 全是 503，播到约 20 秒就断，
    也拖不动。缓存目录里三个资产都只留下 0.ts 和 1.ts，没有更靠后的片段。
    """

    def _command_for(self, index: int, transcode: bool = False) -> list[str]:
        captured: list[list[str]] = []

        class _Process:
            returncode = 0

            async def communicate(self):
                return b"", b""

        async def fake_exec(*command, **_kwargs):
            captured.append(list(command))
            return _Process()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mp4"
            source.write_bytes(bytes(1024))
            service = HlsSegmentService(
                resolver=mock.Mock(ffmpeg=lambda: type("C", (), {"path": "ffmpeg"})),
                work_root=root / "cache",
            )
            # 计划固定为等长片段，免得测试依赖真实关键帧表。
            service._plans[service.fingerprint(source)] = [
                (round(n * 9.993, 3), 9.993) for n in range(400)
            ]

            async def drive():
                with mock.patch("asyncio.create_subprocess_exec", fake_exec):
                    # 产物由假进程负责写，否则会走到「0 字节」的失败分支。
                    original = service.cached_path

                    def cached_path(*args, **kwargs):
                        target = original(*args, **kwargs)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        return target

                    with mock.patch.object(service, "cached_path", cached_path):
                        try:
                            await service.generate(
                                source, round(index * 9.993, 3), 9.993,
                                asset_id=6562, index=index, session="s",
                                registry=StreamSessionRegistry(),
                                transcode=transcode,
                            )
                        except Exception:
                            pass

            asyncio.run(drive())
        self.assertTrue(captured, "没有真的调用 FFmpeg")
        return captured[0]

    def test_conversion_slice_has_bounded_duration_and_absolute_output_timeline(self):
        command = self._command_for(100, transcode=True)
        self.assertEqual(command[command.index('-c:v')+1], 'libx264')
        self.assertEqual(command[command.index('-t')+1], '9.993')
        self.assertEqual(command[command.index('-output_ts_offset')+1], '999.300')
        self.assertNotIn('-copyts', command)

    def test_silent_ffmpeg_failure_still_says_what_was_observed(self):
        """FFmpeg 可以退出码 0、stderr 全空却写出 0 字节，只报 ffmpeg failed 等于没报。"""
        from peach.segments import SegmentUnavailable

        class _Process:
            returncode = 0

            async def communicate(self):
                return b"", b""

        async def fake_exec(*_command, **_kwargs):
            return _Process()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mp4"
            source.write_bytes(bytes(1024))
            service = HlsSegmentService(
                resolver=mock.Mock(ffmpeg=lambda: type("C", (), {"path": "ffmpeg"})),
                work_root=root / "cache",
            )

            async def drive():
                with mock.patch("asyncio.create_subprocess_exec", fake_exec):
                    await service.generate(
                        source, 2997.995, 9.993, asset_id=6562, index=300,
                        session="s", registry=StreamSessionRegistry(),
                    )

            with self.assertRaises(SegmentUnavailable) as caught:
                asyncio.run(drive())

        message = str(caught.exception)
        self.assertIn("returncode=0", message)
        self.assertIn("2997.995", message)
        self.assertIn("3007.988", message)

    def test_every_segment_asks_for_an_absolute_end_not_a_length(self):
        for index in (0, 1, 2, 300):
            command = self._command_for(index)
            self.assertIn("-copyts", command)
            self.assertNotIn("-t", command, f"片段 {index} 仍在用相对时长")
            start = float(command[command.index("-ss") + 1])
            end = float(command[command.index("-to") + 1])
            self.assertAlmostEqual(end - start, 9.993, places=2)
            # 决定性判据：终点随片段前移，而不是所有片段都停在第一段的末尾。
            self.assertGreater(end, start)
            self.assertAlmostEqual(end, round(index * 9.993, 3) + 9.993, places=2)

    def test_concurrent_requests_generate_one_copy_of_the_same_segment(self):
        """播放器预取与重连会同时要同一段：同目标只许一个 FFmpeg，其余等它写完直接用缓存。"""
        calls = 0
        release = None

        class _Process:
            returncode = 0

            async def communicate(self):
                await release.wait()
                return b"", b""

        async def fake_exec(*command, **_kwargs):
            nonlocal calls
            calls += 1
            Path(command[-1]).write_bytes(b"segment")
            return _Process()

        async def drive():
            nonlocal release
            release = asyncio.Event()
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "clip.mp4"
                source.write_bytes(bytes(1024))
                service = HlsSegmentService(
                    resolver=mock.Mock(ffmpeg=lambda: type("C", (), {"path": "ffmpeg"})),
                    work_root=root / "cache",
                )
                registry = StreamSessionRegistry()
                with mock.patch("asyncio.create_subprocess_exec", fake_exec):
                    tasks = [asyncio.create_task(service.generate(
                        source, 0, 6, asset_id=6562, index=0, session=f"s{n}", registry=registry,
                    )) for n in range(3)]
                    await asyncio.sleep(0)
                    release.set()
                    results = await asyncio.gather(*tasks)
                self.assertEqual(len({str(item) for item in results}), 1)
                self.assertEqual(results[0].read_bytes(), b"segment")
                self.assertEqual(service._generation_locks, {}, "锁在没人用时要从字典里消失")

        asyncio.run(drive())
        self.assertEqual(calls, 1)

    def test_hardware_encode_failure_falls_back_to_libx264_for_the_slice(self):
        """没有 NVENC 的机器分片不能缺席：CUDA 解码、软件解码两条 NVENC 路都失败后走 libx264。"""
        captured: list[list[str]] = self._transcode_commands("h264")
        self.assertEqual([c[c.index("-c:v") + 1] for c in captured], ["h264_nvenc", "h264_nvenc", "libx264"])
        self.assertIn("-hwaccel", captured[0])
        self.assertNotIn("-hwaccel", captured[1])
        for command in captured:
            self.assertEqual(command[command.index("-output_ts_offset") + 1], "12.000")
            self.assertEqual(command[command.index("-t") + 1], "6.000")
            self.assertEqual(command[command.index("-f") + 1], "mpegts")

    def test_a_codec_nvdec_garbles_is_decoded_in_software(self):
        """VC-1 交给 NVDEC 会解出绿帧，而 FFmpeg 退出码 0、stderr 全空，事后查不出来。

        实测 `MIAD573_02.wmv`（vc1、1080p）：`-hwaccel cuda` 出来的首个分片开头 21 帧
        在黑场与纯绿之间交替，同一段软件解码后色度全程正常。名单之外一律软件解码，
        编码探测不出来的也是——省下的 CPU 抵不上一段没人察觉的坏画面。
        """
        for codec in ("vc1", "wmv3", ""):
            with self.subTest(codec=codec):
                captured = self._transcode_commands(codec)
                self.assertEqual([c[c.index("-c:v") + 1] for c in captured], ["h264_nvenc", "libx264"])
                self.assertNotIn("-hwaccel", captured[0])

    def _transcode_commands(self, codec: str) -> list[list[str]]:
        """NVENC 全程失败时，这个编码的片源实际跑过的命令。"""
        captured: list[list[str]] = []

        class _Process:
            def __init__(self, returncode):
                self.returncode = returncode

            async def communicate(self):
                return b"", (b"nvenc unavailable" if self.returncode else b"")

        async def fake_exec(*command, **_kwargs):
            captured.append(list(command))
            if "h264_nvenc" in command:
                return _Process(1)
            Path(command[-1]).write_bytes(b"segment")
            return _Process(0)

        async def drive():
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "clip.mp4"
                source.write_bytes(bytes(1024))
                service = HlsSegmentService(
                    resolver=mock.Mock(ffmpeg=lambda: type("C", (), {"path": "ffmpeg"})),
                    work_root=root / "cache", prefer_hardware=True,
                )
                with mock.patch("asyncio.create_subprocess_exec", fake_exec), \
                        mock.patch("peach.hardware_transcode.HardwareTranscoder._candidates",
                                   return_value=(HardwareEncoder("nvenc"),)), \
                        mock.patch("peach.hardware_transcode.HardwareTranscoder._probe", return_value=True), \
                        mock.patch("peach.segments._probe_video_codec", return_value=codec):
                    target = await service.generate(
                        source, 12.0, 6.0, asset_id=6562, index=2, session="s",
                        registry=StreamSessionRegistry(), transcode=True,
                    )
                self.assertEqual(target.read_bytes(), b"segment")

        asyncio.run(drive())
        return captured


class PlanCacheTests(unittest.TestCase):
    """计划缓存一满就 clear() 等于没有缓存；改成 LRU 后热条目要能活下来。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = HlsSegmentService(
            resolver=mock.Mock(ffmpeg=lambda: None), work_root=self.root / "cache")

    def tearDown(self):
        self.tmp.cleanup()

    def _source(self, number: int) -> Path:
        path = self.root / f"clip{number}.mp4"
        path.write_bytes(b"x")
        return path

    def _plan(self, number: int) -> None:
        with mock.patch("peach.segments.keyframe_seconds", return_value=[0.0]):
            self.service.plan(self._source(number), 60.0)

    def _held_sources(self) -> set[str]:
        return {key[0] for key in self.service._plans}

    def test_cache_keeps_at_most_the_limit_and_evicts_the_oldest_access(self):
        from peach.segments import PLAN_CACHE_LIMIT

        for number in range(PLAN_CACHE_LIMIT):
            self._plan(number)
        self.assertEqual(len(self.service._plans), PLAN_CACHE_LIMIT)

        # 命中一次让 0 号变成最近访问，再插入新条目：被逐出的必须是 1 号。
        self._plan(0)
        self._plan(PLAN_CACHE_LIMIT)
        self.assertEqual(len(self.service._plans), PLAN_CACHE_LIMIT)
        self.assertIn(str(self._source(0)), self._held_sources())
        self.assertNotIn(str(self._source(1)), self._held_sources())
