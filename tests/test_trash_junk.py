"""高置信广告批量入回收站的隔离回归。"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from unittest import mock
from pathlib import Path

from support.ledger import fresh_ledger

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"peach_script_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


class TrashJunkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trash_junk = load_script("trash_junk")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.db_path = fresh_ledger(self.root)
        self.out = self.root / "junk.csv"
        self.backup = self.root / "ledger.backup.db"

    def add(self, asset_id, location, path, medium, size=1000, duration=None):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,size,duration) "
                "VALUES(?,?,?,?,?,?,?)",
                (asset_id, location, path, path.rsplit("\\", 1)[-1], medium,
                 size, duration),
            )
            connection.commit()
        finally:
            connection.close()

    def disposal(self, asset_id):
        connection = sqlite3.connect(self.db_path)
        try:
            return connection.execute(
                "SELECT disposal FROM asset WHERE id=?", (asset_id,)).fetchone()[0]
        finally:
            connection.close()

    def test_dry_run_lists_without_writing(self):
        self.add(1, "115", r"B:\广告\tuu26.com.mp4", "video", 10 * 1024**2, 60)

        code = self.trash_junk.main(["--db", str(self.db_path), "--out", str(self.out)])

        self.assertEqual(code, 0)
        self.assertIsNone(self.disposal(1))
        self.assertTrue(self.out.is_file())

    def test_apply_trashes_only_candidates_at_or_above_the_bar(self):
        self.add(1, "115", r"B:\广告\tuu26.com.mp4", "video", 10 * 1024**2, 60)
        self.add(2, "115", r"B:\番号\正片.mp4", "video", 400 * 1024**2, 900)

        code = self.trash_junk.main([
            "--db", str(self.db_path), "--out", str(self.out),
            "--apply", "--backup", str(self.backup),
        ])

        self.assertEqual(code, 0)
        self.assertEqual(self.disposal(1), "trash")
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT disposal_reason FROM asset WHERE id=1").fetchone()[0], "junk")
        self.assertIsNone(self.disposal(2))
        self.assertTrue(self.backup.is_file())

    def test_mounted_sources_can_be_selected_and_trashed(self):
        locations = ('guangya', 'onedrive', 'mounted')
        for asset_id, location in enumerate((*locations, 'online'), 1):
            self.add(asset_id, location, rf'G:\{location}\tuu26.com.mp4', 'video', 10 * 1024**2, 60)
        candidates = self.trash_junk.select_candidates(self.db_path, min_score=40)
        self.assertEqual({row['id'] for row in candidates}, {1, 2, 3})
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(self.trash_junk.trash_assets(connection, [1, 2, 3, 4]), 3)
        self.assertEqual([self.disposal(aid) for aid in (1, 2, 3, 4)], ['trash', 'trash', 'trash', None])

    def test_apply_without_backup_is_rejected(self):
        self.add(1, "115", r"B:\广告\tuu26.com.mp4", "video", 10 * 1024**2, 60)

        with self.assertRaises(SystemExit):
            self.trash_junk.main([
                "--db", str(self.db_path), "--out", str(self.out), "--apply",
            ])

        self.assertIsNone(self.disposal(1))

    def test_story_titles_are_not_promotion_evidence(self):
        self.add(1, "115", r"B:\作品\花式暴操包养的学妹.mp4", "video", 4000000, 20)
        self.add(2, "115", r"B:\作品\约炮应届毕业女大，太骚了.mp4", "video", 14000000, 207)
        self.add(3, "115", r"B:\作品\線上影片每天火熱更新中.avi", "video", 6000000, 40)
        candidates = self.trash_junk.select_candidates(self.db_path, min_score=40)
        self.assertEqual([row["id"] for row in candidates], [3])

    def reviewed_file(self, asset_id=1, suffix=".txt"):
        path = self.root / f"广告{asset_id}{suffix}"
        path.write_bytes(b"promotion")
        self.add(asset_id, "local", str(path), "other", path.stat().st_size)
        return path, {"id": str(asset_id), "path": str(path), "size": str(path.stat().st_size),
                      "decision": "delete", "confidence": "1", "why": "文件内容为地址发布广告"}

    def test_installer_web_components_and_unprobed_promos_enter_review(self):
        self.add(1, "115", r"B:\作品\1024_1.apk", "other")
        self.add(2, "115", r"B:\作品\资源站_files\style.css", "other")
        self.add(3, "115", r"B:\作品\下载APP.mp4", "video", 4000000)
        self.add(4, "115", r"B:\作品\下载APP.srt", "other")
        self.add(5, "115", r"B:\作品\作品.torrent", "other")
        self.add(6, "115", r"B:\作品\正片.mp4", "video", 4000000, 3)
        self.add(7, "115", r"B:\作品\下载APP失败探测.mp4", "video", 4000000, -1)
        candidates = self.trash_junk.select_candidates(self.db_path, min_score=40)
        self.assertEqual({row["id"] for row in candidates}, {1, 2, 3, 7})

    def test_review_manifest_requires_unique_ids_and_confirmed_evidence(self):
        _, review = self.reviewed_file()
        fields = list(review)
        for reviews in ([review, review], [{**review, "why": ""}],
                        [{**review, "confidence": ".8"}], [{**review, "confidence": "nan"}],
                        [{**review, "size": "-1"}]):
            self.trash_junk.write_rows(self.out, fields, reviews)
            with self.assertRaises(ValueError):
                self.trash_junk.load_review(self.out)
        self.trash_junk.write_rows(self.out, fields, [review, {**review, "decision": "retain"}])
        self.assertEqual(len(self.trash_junk.load_review(self.out)), 1)

    def purge(self, rows, candidates=None):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        if candidates is None:
            candidates = {int(row["id"]) for row in rows}
        with mock.patch.object(self.trash_junk, "LOCATION_ROOT_DECLARATIONS", {"local": (str(self.root),)}):
            return self.trash_junk.purge_reviewed(connection, rows, candidates)

    def execute(self, sql, *params):
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(sql, params)
            connection.commit()

    def exists(self, asset_id):
        with closing(sqlite3.connect(self.db_path)) as connection:
            return connection.execute("SELECT 1 FROM asset WHERE id=?", (asset_id,)).fetchone() is not None

    def test_purge_skips_rows_with_personal_records_and_reports_them(self):
        played, played_review = self.reviewed_file(1)
        queued, queued_review = self.reviewed_file(2)
        clean, clean_review = self.reviewed_file(3)
        self.execute("UPDATE asset SET play_count=1 WHERE id=1")
        self.execute("INSERT INTO watch_queue(profile_id,asset_id,added_at) VALUES('default',2,'2026-01-01')")
        result = self.purge([played_review, queued_review, clean_review])
        self.assertEqual(result["purged"], 1)
        self.assertEqual({item["id"]: item["reason"] for item in result["skipped"]},
                         {1: "带个人记录", 2: "带个人记录"})
        self.assertTrue(played.exists() and queued.exists())
        self.assertTrue(self.exists(1) and self.exists(2))
        self.assertFalse(clean.exists())
        self.assertFalse(self.exists(3))

    def test_purge_skips_rows_the_user_kept_or_that_left_the_queue(self):
        kept, kept_review = self.reviewed_file(1)
        stale, stale_review = self.reviewed_file(2)
        self.execute("INSERT INTO review_decision(category,item_key,status,note,updated_at) "
                     "VALUES('junk_file','1','rejected','用户确认不是垃圾','2026-01-01')")
        result = self.purge([kept_review, stale_review], candidates={1})
        self.assertEqual(result["purged"], 0)
        self.assertEqual({item["id"]: item["reason"] for item in result["skipped"]},
                         {1: "用户已标为不是垃圾", 2: "已不在垃圾复核队列里"})
        self.assertTrue(kept.exists() and stale.exists())
        self.assertTrue(self.exists(1) and self.exists(2))

    def test_purge_skips_videos_with_subtitles_instead_of_orphaning_them(self):
        video, review = self.reviewed_file(1, ".mp4")
        subtitle = video.with_name(video.stem + ".zh.srt")
        subtitle.write_text("1\n", encoding="utf-8")
        result = self.purge([review])
        self.assertEqual(result["purged"], 0)
        self.assertIn(subtitle.name, result["skipped"][0]["reason"])
        self.assertTrue(video.exists() and subtitle.exists())

        subtitle.unlink()
        self.execute("INSERT INTO asset_subtitle(asset_id,location,path,name,format,size,pairing,"
                     "first_seen,last_seen) VALUES(1,'local',?,?,'srt',2,'exact','2026-01-01','2026-01-01')",
                     str(subtitle), subtitle.name)
        result = self.purge([review])
        self.assertEqual(result["skipped"][0]["reason"], "登记了字幕")
        self.assertTrue(video.exists() and self.exists(1))

    def test_purge_candidates_come_from_the_junk_review_queue(self):
        promotion = self.root / "tuu26.com.mp4"
        promotion.write_bytes(b"promotion")
        self.add(1, "local", str(promotion), "video", promotion.stat().st_size, 60)
        self.reviewed_file(2)
        candidates = self.trash_junk.candidate_ids(self.db_path)
        self.assertIn(1, candidates)
        self.assertNotIn(2, candidates)

    def test_reviewed_purge_deletes_only_confirmed_file_and_ledger_row(self):
        path, review = self.reviewed_file()
        retained, _ = self.reviewed_file(2)
        result = self.purge([review])
        self.assertEqual(result["purged"], 1)
        self.assertFalse(path.exists())
        self.assertTrue(retained.exists())
        self.assertIsNone(self.disposal(2))

    def test_stale_review_rejects_whole_batch_before_any_file_is_removed(self):
        path, review = self.reviewed_file()
        changed, stale = self.reviewed_file(2)
        changed.write_bytes(b"changed content")
        with self.assertRaisesRegex(ValueError, "失效"):
            self.purge([review, stale])
        self.assertTrue(path.exists())
        self.assertTrue(changed.exists())
        self.assertIsNone(self.disposal(1))

    def test_purge_protects_sidecars_and_unmounted_roots(self):
        path, review = self.reviewed_file(suffix=".nfo")
        with self.assertRaisesRegex(ValueError, "资料与字幕"):
            self.purge([review])
        self.assertTrue(path.exists())
        with mock.patch.object(self.trash_junk, "root_online", return_value=False):
            with self.assertRaisesRegex(ValueError, "已挂载"):
                self.purge([review])

    def test_database_error_restores_quarantined_file(self):
        path, review = self.reviewed_file()
        with mock.patch.object(self.trash_junk, "verify_after_write", return_value=("broken", 0)):
            with self.assertRaisesRegex(RuntimeError, "校验失败"):
                self.purge([review])
        self.assertTrue(path.exists())
        self.assertIsNone(self.disposal(1))

    def test_purge_and_rollback_support_mounts_without_replace(self):
        path, review = self.reviewed_file()
        with mock.patch.object(os, "replace", side_effect=OSError("replacement unsupported")):
            with mock.patch.object(self.trash_junk, "verify_after_write", return_value=("broken", 0)):
                with self.assertRaisesRegex(RuntimeError, "校验失败"):
                    self.purge([review])
            self.assertTrue(path.exists())
            self.assertIsNone(self.disposal(1))
            self.assertEqual(self.purge([review])["purged"], 1)
            self.assertFalse(path.exists())

    def test_small_file_delete_with_local_backup_restores_on_database_error(self):
        from peach import web_batch
        path, review = self.reviewed_file()
        unsupported = OSError('rename unsupported')
        unsupported.winerror = 50
        with mock.patch.object(web_batch, 'GENERATED_DIR', self.root / 'generated'), mock.patch.object(os, 'rename', side_effect=unsupported):
            with mock.patch.object(self.trash_junk, 'verify_after_write', return_value=('broken', 0)):
                with self.assertRaisesRegex(RuntimeError, '校验失败'):
                    self.purge([review])
            self.assertEqual(path.read_bytes(), b'promotion')
            self.assertIsNone(self.disposal(1))
            self.assertEqual(self.purge([review])['purged'], 1)
            self.assertFalse(path.exists())
            self.assertEqual(list((self.root / 'generated' / 'purge-staging').iterdir()), [])

    def test_large_file_without_rename_is_preserved(self):
        from peach import web_batch
        path, review = self.reviewed_file()
        path.write_bytes(b'x' * (1024 * 1024 + 1))
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute('UPDATE asset SET size=? WHERE id=1', (path.stat().st_size,))
            connection.commit()
        review.update(size=str(path.stat().st_size))
        unsupported = OSError('rename unsupported')
        unsupported.winerror = 50
        with mock.patch.object(web_batch, 'GENERATED_DIR', self.root / 'generated'), mock.patch.object(os, 'rename', side_effect=unsupported):
            result = self.purge([review])
        self.assertEqual(result['purged'], 0)
        self.assertEqual(result['blocked'][0]['id'], 1)
        self.assertTrue(path.is_file())
        self.assertIsNone(self.disposal(1))

    def test_pikpak_isolation_preserves_bytes_and_restores_the_original_path(self):
        from peach import web_batch
        from peach import scan
        path=self.root/'作品附件.png'
        path.write_bytes(b'confirmed promotion')
        quarantine=web_batch._quarantine_media(path,location='pikpak')
        self.assertFalse(path.exists())
        self.assertFalse(quarantine.name.startswith('.'))
        self.assertEqual(quarantine.suffix,'.peach-quarantine')
        self.assertEqual(quarantine.read_bytes(),b'confirmed promotion')
        self.assertTrue(scan.is_sidecar(quarantine.name,frozenset()))
        web_batch._restore_staged_media([(path,quarantine)])
        self.assertEqual(path.read_bytes(),b'confirmed promotion')
        self.assertFalse(quarantine.exists())

    def test_a_drive_isolation_uses_a_bounded_normal_component(self):
        from pathlib import PureWindowsPath
        from peach import web_batch
        original=PureWindowsPath('a:\\作品\\'+'长'*200+'.jpg')
        with mock.patch.object(web_batch.os,'rename') as rename:
            quarantine=web_batch._quarantine_media(original)
        self.assertEqual(quarantine.parent,original.parent)
        self.assertLess(len(quarantine.name),80)
        self.assertFalse(quarantine.name.startswith('.'))
        self.assertEqual(quarantine.suffix,'.peach-quarantine')
        rename.assert_called_once_with(original,quarantine)

    def test_only_exact_tool_quarantine_names_are_skipped(self):
        from peach import scan
        for name in ('peach-purge-film.mp4','peach-purge-'+('a'*32)+'.mp4','作品.peach-quarantine'):
            self.assertFalse(scan.is_sidecar(name,frozenset()))

    def test_download_site_navigation_images_keep_content_attachments(self):
        from peach.web_batch import _promo_name_reason
        self.assertIn('须核验视频内容', _promo_name_reason('.mp4', 'A:\\作品\\社 區 最 新 情 報.mp4', 15089802))
        self.assertEqual(_promo_name_reason('.mp4', 'A:\\作品\\社区最新情报合集.mp4', 15089802), '')
        self.assertEqual(_promo_name_reason('.mp4', 'A:\\作品\\社區最新情報.mp4', 32 * 1024**2), '')
        for name in ('如何使用谷歌DNS让您更快进入下载网页步骤01.jpg',
                     '~Free Adult Movie, Fastest & Newest Porn Movie Site.jpg',
                     'hav.so_最新成人高清店長推薦強片天天更新.gif',
                     '__ HiHSP.pw 國產精品 高速下載 在線點播.png'):
            self.assertIn('须核验图片内容', _promo_name_reason(Path(name).suffix, 'B:\\作品\\' + name, 50000))
            self.assertEqual(_promo_name_reason(Path(name).suffix, 'B:\\作品\\' + name, 2 * 1024**2), '')
        for name in ('images.rar', '作品封面.jpg', '作品字幕.srt', 'HiHSP.com-作品截图.jpg'):
            self.assertEqual(_promo_name_reason(Path(name).suffix, 'B:\\作品\\' + name, 50000), '')

    def test_tiny_promotion_cards_require_exact_names_and_content_review(self):
        from peach.web_batch import _promo_name_reason
        for name in ('51风流', '代开实习证明', '扫码约炮', '探花社区'):
            self.assertIn('须核验图片内容', _promo_name_reason('.png', 'A:\\作品\\'+name+'.png', 1400))
            self.assertFalse(_promo_name_reason('.png', 'A:\\作品\\'+name+'.png', 4097))
            self.assertFalse(_promo_name_reason('.png', 'A:\\作品\\'+name+'作品.png', 1400))
            self.assertFalse(_promo_name_reason('.mp4', 'A:\\作品\\'+name+'.mp4', 1400))

    def test_site_cards_require_matching_parent_and_small_png_content_review(self):
        from peach.web_batch import _promo_name_reason
        name='｜91porn｜真实国产原创亚洲最火成人网站，强势回归｜'
        source='A:\\待确认\\My Pack\\213\\'+name+'\\'+name+'.png'
        self.assertIn('须核验图片内容', _promo_name_reason('.png', source, 49130))
        for suffix,path,size in (
                ('.mp4', source.removesuffix('.png')+'.mp4', 49130),
                ('.png', source, 128*1024+1),
                ('.png', 'A:\\作品\\'+name+'.png', 49130),
                ('.png', source.replace(name+'.png','作品截图.png'), 49130)):
            self.assertFalse(_promo_name_reason(suffix,path,size))

    def test_information_wmv_requires_exact_name_size_and_content_review(self):
        from peach.web_batch import JUNK_VIDEO_MAX_BYTES, _promo_name_reason
        for name in ('最新情報', '最 新 情 报'):
            source = 'B:\\云下载\\DOCP-324\\' + name + '.wmv'
            self.assertIn('须核验视频内容', _promo_name_reason('.wmv', source, 90867046))
        for suffix, name, size in (
                ('.wmv', '最新情報', 0),
                ('.wmv', '最新情報', JUNK_VIDEO_MAX_BYTES),
                ('.mp4', '最新情報', 90867046),
                ('.wmv', '作品最新情報完整版', 90867046)):
            self.assertFalse(_promo_name_reason(suffix, 'B:\\作品\\' + name + suffix, size))

    def test_apply_leaves_name_only_promotion_attachments_in_review(self):
        """固定命名只够进复核队列；同一次 --apply 里真正够格的条目照常移入回收站。"""
        site = '｜91porn｜真实国产原创亚洲最火成人网站，强势回归｜'
        named = {
            1: (r'B:\作品\51风流.png', 'image', 1400, None),
            2: (r'B:\作品\代开实习证明.png', 'image', 1400, None),
            3: (r'B:\作品\探花社区.png', 'image', 1400, None),
            4: ('B:\\待确认\\213\\' + site + '\\' + site + '.png', 'image', 49130, None),
            5: (r'B:\作品\最新情報.wmv', 'video', 90867046, None),
            6: (r'B:\作品\社區最新情報.mp4', 'video', 15089802, 60),
            7: (r'B:\作品\如何使用谷歌DNS让您更快进入下载网页步骤01.jpg', 'image', 50000, None),
            8: (r'B:\作品\1024核工厂.rar', 'archive', 4111, None),
        }
        for asset_id, (path, medium, size, duration) in named.items():
            self.add(asset_id, '115', path, medium, size, duration)
        self.add(9, '115', r'B:\广告\tuu26.com.mp4', 'video', 10 * 1024**2, 60)

        queued = self.trash_junk.select_candidates(self.db_path, min_score=40)
        self.assertEqual({row['id'] for row in queued}, {*named, 9})
        code = self.trash_junk.main([
            '--db', str(self.db_path), '--out', str(self.out),
            '--apply', '--backup', str(self.backup),
        ])

        self.assertEqual(code, 0)
        self.assertEqual({asset_id: self.disposal(asset_id) for asset_id in named},
                         dict.fromkeys(named))
        self.assertEqual(self.disposal(9), 'trash')

    def test_unprobed_videos_need_more_than_directory_and_size_evidence(self):
        """没探测时长的视频凭推广目录、推广创作者位与体积只进复核队列。"""
        self.add(1, '115', r'B:\_含羞草APP\极道世界.mp4', 'video', 10 * 1024**2)
        self.add(2, '115', r'B:\作品\极道世界.mp4', 'video', 10 * 1024**2)
        self.add(3, '115', r'B:\bbsxv.xyz-DOCP-324\_含羞草APP\极道世界.mp4', 'video', 10 * 1024**2)
        self.add(4, '115', r'B:\bbsxv.xyz-DOCP-325\_含羞草APP\极道世界.mp4', 'video', 10 * 1024**2, 60)
        self.add(5, '115', r'B:\广告\tuu26.com.mp4', 'video', 10 * 1024**2)
        self.execute("UPDATE asset SET creator='bbsxv.xyz' WHERE id=2")

        queued = {row['id']: row['score'] for row in
                  self.trash_junk.select_candidates(self.db_path, min_score=40)}
        trashable = {row['id'] for row in self.trash_junk.select_candidates(self.db_path, min_score=60)}

        self.assertEqual(set(queued), {1, 2, 3, 4, 5})
        self.assertEqual(queued[1], 40)
        self.assertLess(max(queued[1], queued[2], queued[3]), 60)
        self.assertEqual(trashable, {4, 5})

    def test_promotion_names_come_from_the_lexicon_file(self):
        from peach import web_batch
        lexicon = self.root / 'promo_attachment_names.json'
        lexicon.write_text('{"rules": [{"reason": "测试推广卡，须核验图片内容", "suffixes": [".png"], '
                           '"max_bytes": 4096, "names": ["测试推广卡"]}]}', encoding='utf-8')
        web_batch.promo_name_rules.cache_clear()
        self.addCleanup(web_batch.promo_name_rules.cache_clear)
        with mock.patch.object(web_batch, 'PROMO_NAMES_FILE', lexicon):
            self.assertEqual(web_batch._promo_name_reason('.png', r'B:\作品\测试推广卡.png', 1400),
                             '测试推广卡，须核验图片内容')
            self.assertEqual(web_batch._promo_name_reason('.png', r'B:\作品\51风流.png', 1400), '')
        web_batch.promo_name_rules.cache_clear()
        with mock.patch.object(web_batch, 'PROMO_NAMES_FILE', self.root / 'missing.json'):
            with self.assertRaises(FileNotFoundError):
                web_batch.promo_name_rules()


if __name__ == "__main__":
    unittest.main()
