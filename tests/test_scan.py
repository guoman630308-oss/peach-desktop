"""`peach.scan`：扫描写进账本的行必须能被读取侧翻译回同一个文件。

两种设置文件写法各一条：Windows 上 `[media.locations] local = <真实目录>`、挂载表为空；
macOS 上数据库路径前缀使用 `R:\\media`，`[media.mounts] local = <真实目录>`。两条都在临时目录里跑。

POSIX 形态那条平台无关，哪台机器上都跑。Windows 形态那条只在 Windows 上跑：它的前提是
配置中的路径前缀本身就是一个真实存在的盘符目录，而 POSIX 机器上造不出这种目录——拿 `/var/...` 配
`windows=True` 不是注入平台，是喂一个账本里不可能出现的形态，写入侧门槛会当场拒收。
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

from peach import scan
from peach.migrations import upgrade

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"


class _ScanCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.db = self.root / "ledger.db"
        upgrade(self.db, MIGRATIONS)
        self.media = self.root / "media"
        (self.media / "创作者" / "sub").mkdir(parents=True)
        (self.media / "创作者" / "sub" / "a.mp4").write_bytes(b"0" * 10)
        (self.media / "cover.jpg").write_bytes(b"1" * 3)
        (self.media / "notes.txt").write_bytes(b"2")

    def _paths(self):
        connection = sqlite3.connect(self.db)
        try:
            return dict(connection.execute(
                "SELECT path,medium FROM asset WHERE location='local'").fetchall())
        finally:
            connection.close()

    def _scan(self, root, **kwargs):
        output = io.StringIO()
        with redirect_stdout(output):
            result = scan.scan_location(self.db, "local", root, **kwargs)
        return result, output.getvalue()


@unittest.skipUnless(os.name == "nt", "本用例需要真实存在的 Windows 盘符目录")
class WindowsShapeTests(_ScanCase):
    """Windows：配置的路径前缀就是真实目录，路径原样保存到数据库。"""

    def test_rows_are_written_under_the_declared_root(self):
        declared = {"local": (str(self.media),)}
        result, output = self._scan(str(self.media), declared_roots=declared, windows=True)
        self.assertEqual(result.files, 3)
        self.assertEqual(result.total_bytes, 14)
        self.assertEqual(result.gone, 0)
        self.assertIn("✓ local: 3 文件", output)
        rows = self._paths()
        self.assertEqual(rows[str(self.media / "创作者" / "sub" / "a.mp4")], "video")
        self.assertEqual(rows[str(self.media / "cover.jpg")], "image")
        self.assertEqual(rows[str(self.media / "notes.txt")], "other")
        # 读取侧：Windows 上账本路径就是本机路径，每一行都指向真实存在的文件。
        for path in rows:
            self.assertTrue(Path(path).is_file(), path)

    def test_scanned_video_is_visible_without_a_thumbnail_filter(self):
        from support.web import route_client
        from peach.api import create_app
        from peach.config import PeachSettings
        self._scan(str(self.media), declared_roots={"local": (str(self.media),)}, windows=True)
        with route_client(create_app(PeachSettings(db_path=self.db, configured=True, token=""))) as client:
            self.assertEqual(client.get('/api/items').json()['total'], 1)
            self.assertEqual(client.get('/api/items?thumb=0').json()['total'], 1)
            self.assertEqual(client.get('/api/items?thumb=1').json()['total'], 0)

    def test_a_subdirectory_of_the_declared_root_is_accepted(self):
        declared = {"local": (str(self.media),)}
        result, _ = self._scan(str(self.media / "创作者"), declared_roots=declared, windows=True)
        self.assertEqual(result.files, 1)

    def test_rescanning_refreshes_instead_of_duplicating_and_counts_the_gone(self):
        declared = {"local": (str(self.media),)}
        self._scan(str(self.media), declared_roots=declared, windows=True)
        (self.media / "notes.txt").unlink()
        (self.media / "cover.jpg").write_bytes(b"1" * 8)
        result, _ = self._scan(str(self.media), declared_roots=declared, windows=True)
        self.assertEqual(result.files, 2)
        self.assertEqual(len(self._paths()), 3, "没扫到的行留着，由资源同步对账处理")
        connection = sqlite3.connect(self.db)
        size = connection.execute("SELECT size FROM asset WHERE path=?",
                                  (str(self.media / "cover.jpg"),)).fetchone()[0]
        connection.close()
        self.assertEqual(size, 8)


class PosixShapeTests(_ScanCase):
    """macOS：账本里写 `R:\\media\\...`，遍历的是挂载点，读取侧按挂载表翻回去。"""

    def test_resume_skips_committed_directories_and_retains_subtitles_and_new_ids(self):
        (self.media / '创作者/sub/a.srt').write_text('subtitle', encoding='utf-8')
        saved = {}
        def save(state):
            saved.clear()
            saved.update(json.loads(json.dumps(state)))
            if state.get('files') == 2:
                raise InterruptedError('模拟服务退出')
        options = dict(declared_roots={'local': ('R:\\media',)},
                       mounts={'local': (self.media,)}, windows=False)
        with self.assertRaises(InterruptedError):
            self._scan('R:\\media', save_checkpoint=save, **options)
        visited = []
        scandir = os.scandir
        def walk(path):
            visited.append(Path(path))
            return scandir(path)
        with patch.object(scan.os, 'scandir', side_effect=walk):
            result, _ = self._scan('R:\\media', resume_state=saved, **options)
        self.assertNotIn(self.media, visited)
        self.assertEqual((result.files, result.total_bytes, result.subtitles), (4, 22, 1))
        self.assertEqual(len(result.new_ids), 4)
        self.assertEqual(len(self._paths()), 4)

    def test_finished_root_checkpoint_does_not_enumerate_the_tree(self):
        saved = []
        options = dict(declared_roots={'local': ('R:\\media',)}, mounts={'local': (self.media,)}, windows=False)
        first, _ = self._scan('R:\\media', save_checkpoint=lambda value: saved.append(value), **options)
        with patch.object(scan.os, 'scandir', side_effect=AssertionError('已完成的根不应再次枚举')):
            second, _ = self._scan('R:\\media', resume_state=json.loads(json.dumps(saved[-1])), **options)
        self.assertEqual(second, first)

    def test_file_batch_cursor_resumes_inside_a_directory(self):
        saved = {}
        def save(state):
            saved.update(json.loads(json.dumps(state)))
            if state.get('cursor'):
                raise InterruptedError('模拟文件批次提交后退出')
        options = dict(declared_roots={'local': ('R:\\media',)}, mounts={'local': (self.media,)}, windows=False)
        with patch.object(scan, 'BATCH_SIZE', 1), self.assertRaises(InterruptedError):
            self._scan('R:\\media', save_checkpoint=save, **options)
        self.assertEqual(saved['files'], 1)
        (self.media / saved['cursor']).write_bytes(b'changed after checkpoint')
        progress = []
        result, _ = self._scan('R:\\media', resume_state=saved,
                               progress=lambda count, size: progress.append((count, size)), **options)
        self.assertEqual((result.files, result.total_bytes), (3, 14))
        self.assertTrue(all(count >= 1 for count, _ in progress))
        self.assertEqual(len(self._paths()), 3)

    def test_structured_progress_reports_discovered_files_and_bytes(self):
        progress = []
        result, _ = self._scan('R:\\media', declared_roots={'local': ('R:\\media',)},
                               mounts={'local': (str(self.media),)}, windows=False,
                               progress=lambda files, size: progress.append((files, size)))
        self.assertEqual(progress[0][0], 1)
        self.assertEqual(progress[-1], (3, 14))
        self.assertEqual(progress[-1], (result.files, result.total_bytes))

    DECLARED = {"local": (r"R:\media",)}

    def test_rows_keep_the_ledger_shape_and_translate_back_to_real_files(self):
        mounts = {"local": (self.media,)}
        result, _ = self._scan(r"R:\media", declared_roots=self.DECLARED, mounts=mounts,
                               windows=False)
        self.assertEqual(result.files, 3)
        rows = self._paths()
        self.assertEqual(set(rows), {r"R:\media\创作者\sub\a.mp4", r"R:\media\cover.jpg",
                                     r"R:\media\notes.txt"})
        # 与 `platform.translate_ledger_path` 一致：路径前缀后的层级拼到本机媒体目录。
        for path in rows:
            tail = PureWindowsPath(path).relative_to(PureWindowsPath(r"R:\media")).parts
            self.assertTrue(self.media.joinpath(*tail).is_file(), path)

    def test_a_local_directory_is_converted_to_the_ledger_shape(self):
        mounts = {"local": (str(self.media),)}
        root = scan.ledger_root_for("local", self.media / "创作者",
                                    declared_roots=self.DECLARED, mounts=mounts, windows=False)
        self.assertEqual(root, r"R:\media\创作者")
        result, _ = self._scan(root, declared_roots=self.DECLARED, mounts=mounts, windows=False)
        self.assertEqual(set(self._paths()), {r"R:\media\创作者\sub\a.mp4"})

    def test_a_source_without_a_mount_is_refused_before_touching_the_ledger(self):
        with self.assertRaises(scan.ScanTargetError) as caught:
            self._scan(r"R:\media", declared_roots=self.DECLARED, mounts={}, windows=False)
        self.assertIn("media.mounts", str(caught.exception))
        self.assertEqual(self._paths(), {})

    def test_a_second_root_maps_to_its_own_mount(self):
        """两个目录都是 `local`：第二个挂载目录对应第二个数据库路径前缀。"""
        second = self.root / "more"
        (second / "deep").mkdir(parents=True)
        (second / "deep" / "c.mp4").write_bytes(b"2")
        declared = {"local": (r"R:\media", r"R:\media2")}
        mounts = {"local": (str(self.media), str(second))}
        root = scan.ledger_root_for("local", second / "deep", declared_roots=declared,
                                    mounts=mounts, windows=False)
        self.assertEqual(root, r"R:\media2\deep")
        result, _ = self._scan(root, declared_roots=declared, mounts=mounts, windows=False)
        self.assertEqual(result.files, 1)
        self.assertEqual(set(self._paths()), {r"R:\media2\deep\c.mp4"})
        # 第二个路径前缀没有对应的挂载目录时拒绝扫描，不能读取第一个目录。
        with self.assertRaises(scan.ScanTargetError) as caught:
            scan.walk_root_for("local", r"R:\media2", declared_roots=declared,
                               mounts={"local": (str(self.media),)}, windows=False)
        self.assertIn("第 2 个数据库路径前缀", str(caught.exception))

    def test_a_directory_outside_the_mount_is_refused(self):
        with self.assertRaises(scan.ScanTargetError) as caught:
            scan.ledger_root_for("local", "/somewhere/else", declared_roots=self.DECLARED,
                                 mounts={"local": (str(self.media),)}, windows=False)
        self.assertIn("挂载点", str(caught.exception))


class SidecarTests(_ScanCase):
    """刮削器与截图工具写在正片旁边的附属文件不登记，图集里同名的图照常登记。"""

    DECLARED = {"local": (r"R:\media",)}

    def _layout(self, files):
        for name in files:
            path = self.media.joinpath(*name.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"0")

    def test_a_kodi_movie_folder_registers_only_the_video(self):
        self._layout(["FC2-1/FC2-1-CD1.mp4", "FC2-1/FC2-1-CD1.nfo", "FC2-1/poster.jpg",
                      "FC2-1/fanart.jpg", "FC2-1/thumb.jpg", "FC2-1/extrafanart/1.jpg",
                      "FC2-1/FC2-1-CD1-landscape.jpg",
                      "thumbs_new/detailed/FC2-1_4x4_thumb.jpg", "ABC-1.mp4_thumbs.jpg"])
        result, output = self._scan(r"R:\media", declared_roots=self.DECLARED,
                                    mounts={"local": (self.media,)}, windows=False)
        self.assertEqual(result.sidecars, 8)
        self.assertIn("附属文件未登记 8 个", output)
        self.assertEqual(set(self._paths()), {
            r"R:\media\FC2-1\FC2-1-CD1.mp4", r"R:\media\创作者\sub\a.mp4",
            r"R:\media\cover.jpg", r"R:\media\notes.txt"})

    def test_artwork_names_without_a_video_beside_them_are_photos(self):
        self._layout(["写真/cover.jpg", "写真/extrafanart/1.jpg", "写真/poster-1.jpg"])
        self._scan(r"R:\media\写真", declared_roots=self.DECLARED,
                   mounts={"local": (self.media,)}, windows=False)
        self.assertEqual(set(self._paths()), {
            r"R:\media\写真\cover.jpg", r"R:\media\写真\extrafanart\1.jpg",
            r"R:\media\写真\poster-1.jpg"})

    def test_the_rule_reads_names_only(self):
        videos = scan.video_stems(["ABC-1.mp4", "notes.txt"])
        self.assertEqual(videos, frozenset({"abc-1"}))
        sidecar = {name: scan.is_sidecar(name, videos) for name in (
            "x.NFO", "Poster.JPG", "ABC-1-fanart.jpg", "ABC-1.jpg", "OTHER-fanart.jpg",
            "a.png.thumb.png", "ABC-1_3x3_thumbs.jpg", "ABC-1.srt", "001.jpg")}
        self.assertEqual({name for name, hit in sidecar.items() if hit}, {
            "x.NFO", "Poster.JPG", "ABC-1-fanart.jpg", "a.png.thumb.png",
            "ABC-1_3x3_thumbs.jpg"})


class ScanTargetTests(unittest.TestCase):
    """写入侧门槛：`location` 与扫描根必须对得上（ADR-0023 第 2 阶段），口径与脚本时期一致。"""

    DECLARED = {"local": (r"R:\media",), "115": ("B:/",)}

    def test_the_declared_root_and_its_subdirectories_are_accepted(self):
        for location, root in (("local", r"R:\media"), ("local", r"R:\media\创作者"),
                               ("115", "B:/x")):
            self.assertIsNone(scan.check_scan_target(location, root,
                                                     declared_roots=self.DECLARED))

    def test_a_root_belonging_to_another_source_is_refused(self):
        with self.assertRaises(scan.ScanTargetError) as caught:
            scan.check_scan_target("115", r"R:\media\创作者", declared_roots=self.DECLARED)
        self.assertIn("115", str(caught.exception))
        self.assertIn("local", str(caught.exception))

    def test_a_root_outside_every_declared_root_is_refused(self):
        with self.assertRaises(scan.ScanTargetError) as caught:
            scan.check_scan_target("local", r"R:\Resources\Intake", declared_roots=self.DECLARED)
        self.assertIn(r"R:\media", str(caught.exception))

    def test_an_undeclared_source_is_refused_and_lists_the_known_ones(self):
        with self.assertRaises(scan.ScanTargetError) as caught:
            scan.check_scan_target("nas", "N:/", declared_roots=self.DECLARED)
        self.assertIn("nas", str(caught.exception))
        self.assertIn("local", str(caught.exception))

    def test_medium_is_decided_by_extension(self):
        self.assertEqual([scan.medium_of(name) for name in
                          ("a.MP4", "b.webp", "c.flac", "d.7z", "e.nfo")],
                         ["video", "image", "audio", "archive", "other"])


class NasDirectoryTests(unittest.TestCase):
    def test_system_directories_are_skipped_and_similarly_named_files_are_kept(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in ("#recycle", "#RECYCLE", "@eaDir", "@snapshot", "normal"):
                folder = root / name
                folder.mkdir(exist_ok=True)
                (folder / "sample.mp4").write_bytes(b"x")
            for name in ("@video.mp4", "#recycle.mp4"):
                (root / name).write_bytes(b"x")
            files = [Path(entry.path).relative_to(root).as_posix()
                     for _, entries in scan._walk(root) for entry in entries]
        self.assertEqual(set(files), {"normal/sample.mp4", "@video.mp4", "#recycle.mp4"})


if __name__ == "__main__":
    unittest.main()
