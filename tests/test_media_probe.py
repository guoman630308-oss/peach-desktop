"""入库时顺手探时长：探哪些行、哪些来源不碰、探不出来记成什么。"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest import mock

from peach import media_probe
from peach import push_discovery as push
from support.ledger import fresh_ledger

ROOT = r"R:\media"


class ProbeUnmeasuredTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = fresh_ledger(Path(self.tmp.name))
        self.probed: list[str] = []

    def add(self, path, *, location="local", medium="video", duration=None, disposal=None) -> int:
        connection = sqlite3.connect(self.db)
        try:
            cursor = connection.execute(
                "INSERT INTO asset(location,path,name,medium,duration,disposal) VALUES(?,?,?,?,?,?)",
                (location, path, PureWindowsPath(path).name, medium, duration, disposal))
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    def row(self, asset_id):
        connection = sqlite3.connect(self.db)
        try:
            return connection.execute(
                "SELECT duration,width,height,ctx_quality FROM asset WHERE id=?", (asset_id,)).fetchone()
        finally:
            connection.close()

    def fake_probe(self, _ffprobe, path, _timeout):
        self.probed.append(PureWindowsPath(path).name)
        return 1800.0, 1920, 1080, "h264", 30.0, None

    def probe(self, location="local", root=ROOT, ffprobe="ffprobe"):
        with mock.patch.object(media_probe, "probe_file", self.fake_probe), \
                mock.patch.object(media_probe, "resolve_case_insensitive", str):
            return media_probe.probe_unmeasured(self.db, ffprobe, location, root)

    def test_probe_uses_each_mount_root_choice_including_pikpak(self):
        self.add(r'A:\Media\normal.mp4', location='pikpak')
        self.add(r'A:\Media\Proxy\protected.mp4', location='pikpak')
        config = SimpleNamespace(locations={'pikpak': (r'A:\Media', r'A:\Media\Proxy')},
                                 media_costs={r'A:\Media': 'free', r'A:\Media\Proxy': 'metered'})
        with mock.patch.object(media_probe, 'probe_file', self.fake_probe), \
                mock.patch.object(media_probe, 'resolve_case_insensitive', str):
            count = media_probe.probe_unmeasured(self.db, 'ffprobe', 'pikpak', r'A:\Media', config=config)
        self.assertEqual(count, 1)
        self.assertEqual(self.probed, ['normal.mp4'])

    def test_only_unmeasured_active_videos_under_the_scanned_root_are_probed(self):
        fresh = self.add(ROOT + r"\新片\a.mp4")
        measured = self.add(ROOT + r"\b.mp4", duration=10.0)
        self.add(ROOT + r"\c.jpg", medium="image")
        self.add(ROOT + r"\d.mp4", disposal="trash")
        self.add(r"R:\mediax\e.mp4")
        self.add(r"R:\other\f.mp4")

        self.assertEqual(self.probe(), 1)
        self.assertEqual(self.probed, ["a.mp4"], "同前缀的兄弟目录 R:\\mediax 不在这个根下")
        self.assertEqual(self.row(fresh), (1800.0, 1920, 1080, "2K"))
        self.assertEqual(self.row(measured)[0], 10.0)

    def test_a_metered_source_is_left_for_the_explicit_batch(self):
        pending = self.add(r"A:\创作者\a.mp4", location="pikpak")
        self.assertEqual(self.probe("pikpak", "A:\\"), 0)
        self.assertEqual(self.probed, [])
        self.assertIsNone(self.row(pending)[0])

    def test_without_ffprobe_the_scan_still_finishes_and_leaves_rows_pending(self):
        pending = self.add(ROOT + r"\a.mp4")
        self.assertEqual(self.probe(ffprobe=None), 0)
        self.assertIsNone(self.row(pending)[0])

    def test_an_unreadable_file_is_marked_failed_so_the_next_scan_skips_it(self):
        broken = self.add(ROOT + r"\坏.mp4")

        def timeout(*_args):
            raise subprocess.TimeoutExpired("ffprobe", 20)

        with mock.patch.object(media_probe, "probe_file", timeout), \
                mock.patch.object(media_probe, "resolve_case_insensitive", str):
            media_probe.probe_unmeasured(self.db, "ffprobe", "local", ROOT)
        self.assertEqual(self.row(broken), (-1, 0, 0, None))
        self.assertEqual(self.probe(), 0, "-1 已经不是待办")

    def test_a_pushed_file_is_probed_on_its_own(self):
        pushed = self.add(ROOT + r"\a.mp4")
        other = self.add(ROOT + r"\b.mp4")
        with mock.patch.object(media_probe, "probe_file", self.fake_probe), \
                mock.patch.object(media_probe, "resolve_case_insensitive", str):
            self.assertEqual(media_probe.probe_path(self.db, "ffprobe", "local", ROOT + r"\a.mp4"), 1)
        self.assertEqual(self.row(pushed)[0], 1800.0)
        self.assertIsNone(self.row(other)[0])

    def test_controlled_probe_uses_parallel_readers_and_serial_progress(self):
        from peach.processing_control import control_for
        from peach.settings_file import PeachConfig
        root = Path(self.tmp.name).resolve()
        config = PeachConfig(root, root / 'config.toml', present=True, locations={'local': (ROOT,)})
        control = control_for(config)
        for mode, workers in (('low', 1), ('standard', 2), ('high', 4)):
            with self.subTest(mode=mode):
                selected = [self.add(ROOT + f'\\{mode}-{index}.mp4') for index in range(workers)]
                barrier = threading.Barrier(workers, timeout=3)
                readers, writers = set(), set()
                def probe(*_args, readers=readers, barrier=barrier):
                    readers.add(threading.get_ident())
                    barrier.wait()
                    return 1800.0, 1920, 1080, 'h264', 30.0, None
                with control.bind(mode, publish=lambda **_: None, active=lambda: True, db_path=self.db, mode=mode), \
                        mock.patch.object(media_probe, 'probe_file', probe), \
                        mock.patch.object(media_probe, 'resolve_case_insensitive', str):
                    count = media_probe.probe_unmeasured(self.db, 'ffprobe', 'local', ROOT, config=config,
                        asset_ids=selected, report=lambda *_, writers=writers: writers.add(threading.get_ident()))
                self.assertEqual(count, workers)
                self.assertEqual(len(readers), workers)
                self.assertEqual(writers, {threading.get_ident()})


class ProbeProcessTests(unittest.TestCase):
    def test_probe_reads_metadata_with_a_hidden_windows_process(self):
        payload = b'{"format":{"duration":"12.5"},"streams":[{"width":320,"height":240,' \
                  b'"codec_name":"h264","avg_frame_rate":"25/1"}]}'
        with mock.patch.object(media_probe.subprocess, "run",
                               return_value=SimpleNamespace(stdout=payload, returncode=0, stderr=b'')) as run:
            result = media_probe.probe_file("ffprobe", "sample.mp4", timeout=3)
        self.assertEqual(result, (12.5, 320, 240, "h264", 25.0, None))
        options = run.call_args.kwargs
        self.assertEqual(options["creationflags"],
                         subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.assertTrue(options["capture_output"])
        self.assertEqual(options["timeout"], 3)


class PushDiscoveryProbeTests(unittest.TestCase):
    def test_a_pushed_video_lands_with_its_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            db = fresh_ledger(root)
            media = root / "media"
            media.mkdir()
            (media / "a.mp4").write_bytes(b"0" * 8)
            if os.name == "nt":
                declared, mounts = {"local": (str(media),)}, {}
            else:
                declared, mounts = {"local": (ROOT,)}, {"local": (str(media),)}
            service = push.PushDiscoveryService(
                state_root=root / "state", secrets_root=root / "secrets", db_path=db,
                declared_roots=declared, mounts=mounts,
                ffprobe=lambda: SimpleNamespace(path="ffprobe"))
            path = str(PureWindowsPath(declared["local"][0]).joinpath("a.mp4"))
            with mock.patch.object(media_probe, "probe_file",
                                   lambda *_args: (95.0, 1280, 720, "h264", 25.0, None)):
                self.assertTrue(service.queue.ingest("local", path))
            connection = sqlite3.connect(db)
            try:
                duration, height = connection.execute("SELECT duration,height FROM asset").fetchone()
            finally:
                connection.close()
        self.assertEqual((duration, height), (95.0, 720))


if __name__ == "__main__":
    unittest.main()
