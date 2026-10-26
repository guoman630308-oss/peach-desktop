"""资源对账域的隔离测试。

随 `web_resource_sync` 从 `web_contract` 一同拆出：测试巨石镜像代码巨石没有意义。
patch 目标必须指向真正执行的模块——这批用例大量 patch `source_is_online` 与
`translate_ledger_path`，拆分时若仍打在 `web_contract` 上，patch 会静默失效，
测试照样「通过」到断言才炸。
"""
import contextlib
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from peach import web_batch
from peach import web_contract as rm_web
from peach import web_resource_sync as rm_sync

from support.ledger import fresh_ledger


def finish_scan(contract) -> dict:
    """跑完一轮后台检查并交回它的公开结果；执行只认这样一轮的 `scan_id`。"""
    rm_sync.w_resource_sync_scan(contract, {"background": True, "restart": True})
    contract.resource_scan.thread.join(10)
    return rm_sync.w_resource_sync_scan(contract, {"background": True, "status_only": True})


class PurgeMissingTests(unittest.TestCase):
    """对账：磁盘上已删掉的文件，账本行要么进回收站（按目录），要么直接删（整库）；
    带个人记录的两条路径都只标已消失。

    整库那条不可恢复，所以测试重点全在「什么时候**不该**删」。
    """

    LEDGER_DIR = r"B:\creator\P"
    OTHER_DIR = r"B:\creator\V"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.db_path = str(fresh_ledger(self.root))
        con = sqlite3.connect(self.db_path)
        sep = chr(92)
        rows = [
            (1, "115", self.LEDGER_DIR + sep + "001.jpg", "001.jpg"),
            (2, "115", self.LEDGER_DIR + sep + "002.jpg", "002.jpg"),
            (3, "115", self.LEDGER_DIR + sep + "003.jpg", "003.jpg"),
            # 同来源但另一个目录，任何情况下都不该被这次对账碰到。
            (4, "115", self.OTHER_DIR + sep + "a.mp4", "a.mp4"),
        ]
        con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,size) "
            "VALUES(?,?,?,?,'image',10)", rows)
        con.executemany("INSERT INTO asset_tag(asset_id,tag,source) VALUES(?,?,'t')",
                        [(2, "标签"), (4, "标签")])
        con.executemany(
            "INSERT INTO asset_preference(profile_id,asset_id,liked,reason,updated_at) "
            "VALUES('default',?,1,'','2026-01-01')", [(2,), (4,)])
        con.commit()
        con.close()
        self.contract = rm_web.WebContract(Path(self.db_path))
        # 只有 001 和 a.mp4 还在盘上；002、003 当作已被手动删除。
        for name in ("001.jpg", "a.mp4"):
            (self.root / name).write_bytes(b"x")
        # 检查会遍历在线来源的目录来找空文件夹；测试目录必须固定在临时目录中，否则会遍历
        # 这台机器上真实的 `R:\media` 与网盘挂载。
        self.mount = self.root / "mount"
        self.mount.mkdir()
        for module in (rm_sync, web_batch):
            declared = mock.patch.object(module, "LOCATION_ROOT_DECLARATIONS",
                                         {"115": (str(self.mount),)})
            declared.start()
            self.addCleanup(declared.stop)

    def tearDown(self):
        # 状态到达 complete 后，后台线程还要结算 task_run。先等线程真正退出，避免它在
        # TemporaryDirectory 清理期间继续打开 ledger 或写入目录。
        self.contract.resource_scan.stop()
        self.contract.resource_apply_job.stop()
        self.tmp.cleanup()

    def _translate(self, raw):
        """把账本的 Windows 路径映射到临时目录里的同名文件。"""
        return self.root / str(raw).rsplit(chr(92), 1)[-1]

    def _synced(self, online=lambda location: location == "115"):
        """账本路径映射到临时目录；删除那一侧（`web_batch`）要跟着映射，否则它去问真实的 B:。"""
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(rm_sync, "translate_ledger_path", self._translate))
        stack.enter_context(mock.patch.object(rm_sync, "source_is_online", online))
        stack.enter_context(mock.patch.object(web_batch, "translate_ledger_path", self._translate))
        return stack

    def _run(self, online=True):
        patch_translate = mock.patch.object(
            rm_sync, "translate_ledger_path", self._translate)
        patch_online = mock.patch.object(
            rm_sync, "source_is_online", lambda _loc: online)
        with patch_translate, patch_online:
            return rm_sync.w_purge_missing(self.contract, {"id": 1})

    def ids(self):
        con = sqlite3.connect(self.db_path)
        try:
            return [r[0] for r in con.execute("SELECT id FROM asset ORDER BY id")]
        finally:
            con.close()

    def test_offline_source_refuses_instead_of_deleting_everything(self):
        """盘没挂上时，整目录都会 stat 失败——那不是「文件被删」。

        R: 实测掉线过；若不拦，2,552 条本地资产会一次全部消失。
        """
        result = self._run(online=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "source offline")
        self.assertEqual(self.ids(), [1, 2, 3, 4])

    def disposals(self):
        con = sqlite3.connect(self.db_path)
        try:
            return dict(con.execute("SELECT id,disposal FROM asset ORDER BY id").fetchall())
        finally:
            con.close()

    def test_missing_files_with_records_vanish_and_the_rest_move_to_trash(self):
        result = self._run()
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 3)
        self.assertEqual((result["removed"], result["trashed"], result["vanished"]), (2, 1, 1))
        self.assertEqual({x["id"]: x["disposal"] for x in result["items"]},
                         {2: "vanished", 3: "trash"})
        # 另一个目录的 4 必须原样留下；002 带喜欢记录，标已消失；003 没有记录，进回收站。
        self.assertEqual(self.disposals(), {1: None, 2: "vanished", 3: "trash", 4: None})
        con = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(con.execute("SELECT disposal_reason FROM asset WHERE id=3").fetchone()[0], "missing")
            # 元数据等到清空回收站才随账本行一起删。
            self.assertEqual(
                [r[0] for r in con.execute("SELECT asset_id FROM asset_tag")], [2, 4])
            self.assertEqual(
                [r[0] for r in con.execute("SELECT asset_id FROM asset_preference")], [2, 4])
        finally:
            con.close()

    def test_a_row_only_rated_counts_as_carrying_records(self):
        """行上的评分也是个人记录：只打过分、六张表里什么都没有的行同样标已消失。"""
        with self.contract.write_transaction() as connection:
            connection.execute("UPDATE asset SET rating=3 WHERE id=3")
        result = self._run()
        self.assertEqual((result["trashed"], result["vanished"]), (0, 2))
        self.assertEqual(self.disposals(), {1: None, 2: "vanished", 3: "vanished", 4: None})

    def test_sync_delete_reattaches_a_vanished_row_to_the_one_other_version(self):
        """新版本先入库、旧文件后消失：标已消失的同时在库里找，唯一命中就当场接回。"""
        sep = chr(92)
        with self.contract.write_transaction() as connection:
            connection.executemany(
                "INSERT INTO asset(id,location,path,name,medium,size,code) "
                "VALUES(?,'115',?,?,'video',10,'ABC-123')",
                [(5, self.LEDGER_DIR + sep + "ABC-123.mp4", "ABC-123.mp4"),
                 (6, self.OTHER_DIR + sep + "ABC-123.mkv", "ABC-123.mkv")])
            connection.execute("INSERT INTO asset_preference(profile_id,asset_id,liked,reason,updated_at) "
                               "VALUES('default',5,1,'','2026-01-01')")
        result = self._run()
        self.assertEqual((result["trashed"], result["vanished"], result["reattached"]), (1, 1, 1))
        self.assertEqual({x["id"]: x["disposal"] for x in result["items"]},
                         {2: "vanished", 3: "trash", 5: "reattached"})
        self.assertEqual(self.disposals(), {1: None, 2: "vanished", 3: "trash", 4: None, 6: None})
        with self.contract.read_connection() as connection:
            self.assertEqual([r[0] for r in connection.execute(
                "SELECT asset_id FROM asset_preference ORDER BY asset_id")], [2, 4, 6])
            self.assertEqual([tuple(r) for r in connection.execute(
                "SELECT source,old_asset_id,new_asset_id FROM record_rehome")],
                [("auto:vanished-reattach", 5, 6)])

    def test_purge_undo_restores_both_tiers_and_leaves_earlier_disposals_alone(self):
        """回执的撤销走 batch restore：两档都还原；这一趟之前就进回收站的行不在名单里。"""
        with self.contract.write_transaction() as connection:
            connection.execute("INSERT INTO asset(id,location,path,name,medium,size,disposal) "
                               "VALUES(5,'115',?,'005.jpg','image',10,'trash')",
                               (self.LEDGER_DIR + chr(92) + "005.jpg",))
        result = self._run()
        self.assertEqual(sorted(x["id"] for x in result["items"]), [2, 3])
        web_batch.w_batch(self.contract, {"operation": "restore", "ids": [x["id"] for x in result["items"]]})
        self.assertEqual(self.disposals(), {1: None, 2: None, 3: None, 4: None, 5: "trash"})

    def test_full_sync_scans_all_online_assets_and_cleans_only_rebuildable_caches(self):
        roots = {}
        for name in ("snapshots", "posters", "photo-thumbs", "transcodes",
                     "stream-segments", "timeline", "avatars", "covers"):
            roots[name] = self.root / name
            roots[name].mkdir()
        cache_files = [
            roots["snapshots"] / "2.jpg",
            roots["posters"] / "2_4.jpg",
            roots["photo-thumbs"] / "2.jpg",
            roots["transcodes"] / "2-10-20.mp4",
            roots["stream-segments"] / "2" / "10-20-6" / "0.ts",
            # 时间轴预览按 `<id 末两位>/<id>/` 分桶。一部三小时的片子按 10 秒一帧是
            # 1080 张，片子没了还留着的话，是本机产物里单部占得最多的一类。
            roots["timeline"] / "02" / "2" / "000.jpg",
            roots["timeline"] / "02" / "2" / "meta.json",
            roots["avatars"] / "2.jpg",
            roots["covers"] / "hey-002.jpg",
        ]
        for path in cache_files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"cache")
        kept = roots["timeline"] / "04" / "4" / "000.jpg"
        kept.parent.mkdir(parents=True)
        kept.write_bytes(b"cache")
        evidence = self.root / "candidate.csv"
        evidence.write_text("review evidence", encoding="utf-8")
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE asset SET code='HEY-002',snapshot_path=? WHERE id=2",
                    (str(cache_files[0]),))
        # 这一条只看删除那一档：002 不带个人记录。
        con.execute("DELETE FROM asset_preference WHERE asset_id=2")
        con.commit();con.close()
        self.contract.snapshot_root = roots["snapshots"]
        self.contract.poster_root = roots["posters"]
        self.contract.photo_root = roots["photo-thumbs"]
        self.contract.transcode_root = roots["transcodes"]
        self.contract.stream_root = roots["stream-segments"]
        self.contract.timeline_root = roots["timeline"]
        self.contract.avatar_root = roots["avatars"]
        self.contract.cover_root = roots["covers"]
        self.contract.resource_cleanup_enabled = True

        with self._synced():
            preview = rm_sync.w_resource_sync_scan(self.contract)
            self.assertEqual(preview["missing"], 2)
            self.assertEqual(preview["cache"]["files"], len(cache_files))
            scan = finish_scan(self.contract)
            result = rm_sync.w_resource_sync_apply(
                self.contract, {"confirm": True, "clean_cache": True, "scan_id": scan["scan_id"]})

        self.assertEqual(result["purged"], 2)
        # 快照随账本行一起删（`_finish_purge`），剩下的才轮到孤儿缓存那一步。
        self.assertEqual(result["cache_removed"], len(cache_files) - 1)
        self.assertTrue(all(not path.exists() for path in cache_files))
        self.assertTrue(kept.is_file(), "数据库里还在的片子，它那套时间轴图不算孤儿")
        self.assertTrue(evidence.is_file(), "候选证据不属于可删除缓存")
        self.assertEqual(self.ids(), [1, 4])

    def test_full_sync_marks_record_holders_vanished_and_keeps_their_snapshot_and_cover(self):
        roots = {name: self.root / name for name in ("snapshots", "covers", "transcodes")}
        for root in roots.values():
            root.mkdir()
        snapshot, cover = roots["snapshots"] / "2.jpg", roots["covers"] / "hey-002.jpg"
        transcode = roots["transcodes"] / "2-10-20.mp4"
        for path in (snapshot, cover, transcode):
            path.write_bytes(b"cache")
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE asset SET code='HEY-002',snapshot_path=? WHERE id=2", (str(snapshot),))
        con.commit();con.close()
        self.contract.snapshot_root = roots["snapshots"]
        self.contract.cover_root = roots["covers"]
        self.contract.transcode_root = roots["transcodes"]
        self.contract.resource_cleanup_enabled = True

        with self._synced():
            scan = finish_scan(self.contract)
            self.assertEqual((scan["missing"], scan["purge"], scan["vanish"]), (2, 1, 1))
            source = next(row for row in scan["sources"] if row["location"] == "115")
            self.assertEqual((source["missing"], source["vanish"]), (2, 1))
            self.assertEqual(scan["cache"]["files"], 1)
            result = rm_sync.w_resource_sync_apply(
                self.contract, {"confirm": True, "clean_cache": True, "scan_id": scan["scan_id"]})
            again = rm_sync.w_resource_sync_scan(self.contract)

        self.assertEqual((result["purged"], result["vanished"]), (1, 1))
        self.assertEqual(self.disposals(), {1: None, 2: "vanished", 4: None})
        # 快照与封面留给孤儿记录列表认片；按 id 生成的转码对读不到的文件没有用处。
        self.assertTrue(snapshot.is_file())
        self.assertTrue(cover.is_file())
        self.assertFalse(transcode.exists())
        # 已消失的行不再进下一轮的候选，也不计入来源总数。
        self.assertEqual((again["missing"], again["vanish"]), (0, 0))
        self.assertEqual(next(row for row in again["sources"] if row["location"] == "115")["total"], 2)

    def test_full_sync_lists_each_directory_once_instead_of_stating_every_file(self):
        real_scandir = os.scandir
        with mock.patch.object(rm_sync, "translate_ledger_path", self._translate), \
             mock.patch.object(rm_sync, "source_is_online", lambda loc: loc == "115"), \
             mock.patch.object(rm_sync.os, "scandir", wraps=real_scandir) as scandir:
            preview = rm_sync.w_resource_sync_scan(self.contract)

        self.assertEqual(preview["missing"], 2)
        # 账本那几行同住一个目录，列一次；来源根为找空文件夹再列一次。
        self.assertEqual(scandir.call_count, 2)
        source = next(row for row in preview["sources"] if row["location"] == "115")
        self.assertEqual(source["total"], 4)
        self.assertEqual(source["unreadable"], 0)

    def test_sync_checks_local_and_cloud_files_in_mixed_library(self):
        with self.contract.write_transaction() as connection:
            connection.execute("INSERT INTO asset(id,location,path,name,medium,size) "
                               "VALUES(5,'local','R:\\missing.mp4','missing.mp4','video',10)")
        local = self.root / "local"
        local.mkdir()
        declared = {'local': (str(local),), '115': (str(self.mount),)}
        with mock.patch.object(rm_sync, 'LOCATION_ROOT_DECLARATIONS', declared), \
             mock.patch.object(web_batch, 'LOCATION_ROOT_DECLARATIONS', declared), \
             self._synced(online=lambda _location: True):
            preview = rm_sync.w_resource_sync_scan(self.contract)
            self.assertEqual([source['location'] for source in preview['sources']], ['local', '115'])
            self.assertEqual(preview['missing'], 3)
            scan = finish_scan(self.contract)
            result = rm_sync.w_resource_sync_apply(
                self.contract, {'confirm': True, 'clean_cache': False, 'scan_id': scan['scan_id']})
        self.assertEqual((result['purged'], result['vanished']), (2, 1))
        self.assertEqual(self.ids(), [1, 2, 4])

    def test_apply_resumes_after_a_committed_chunk_with_cumulative_receipt(self):
        saved = {}
        def remember(**fields):
            saved.update(fields)
            if 'apply_checkpoint' in fields:
                raise InterruptedError('停止于检查点')
            return True
        with self._synced():
            scan = finish_scan(self.contract)
            body = {'confirm': True, 'clean_cache': False, 'scan_id': scan['scan_id']}
            with self.assertRaises(InterruptedError):
                rm_sync.w_resource_sync_apply(self.contract, body, checkpoint=remember)
            self.assertEqual(self.ids(), [1, 2, 4])
            with mock.patch.object(rm_sync, '_recheck_vanished', wraps=rm_sync._recheck_vanished) as recheck:
                result = rm_sync.w_resource_sync_apply(self.contract, body, resume_state=saved)
        recheck.assert_not_called()
        self.assertEqual((result['purged'], result['vanished']), (1, 1))

    def test_local_only_configuration_has_resource_check(self):
        with mock.patch.object(rm_sync, 'LOCATION_ROOT_DECLARATIONS', {'local': ('R:\\',)}):
            self.assertEqual(rm_sync.configured_resource_locations(), ('local',))
            state = rm_sync.w_resource_sync_scan(self.contract, {'background': True, 'status_only': True})
            self.assertEqual(state['total_sources'], 1)
            with mock.patch.object(rm_sync, 'source_is_online', return_value=False):
                preview = rm_sync.w_resource_sync_scan(self.contract)
                self.assertEqual(preview['sources'][0]['location'], 'local')
                self.assertFalse(preview['sources'][0]['online'])
                self.assertEqual(preview['missing'], 0)

    def test_no_configured_source_requires_media_folder(self):
        with mock.patch.object(rm_sync, 'LOCATION_ROOT_DECLARATIONS', {}):
            for body in ({}, {'background': True}):
                with self.assertRaisesRegex(ValueError, '添加媒体目录'):
                    rm_sync.w_resource_sync_scan(self.contract, body)
            with self.assertRaisesRegex(ValueError, '添加媒体目录'):
                rm_sync.w_resource_sync_apply(self.contract, {'confirm': True})

    def test_full_sync_skips_an_unreadable_directory_instead_of_trashing_it(self):
        with mock.patch.object(rm_sync, "translate_ledger_path", self._translate), \
             mock.patch.object(rm_sync, "source_is_online", lambda loc: loc == "115"), \
             mock.patch.object(rm_sync.os, "scandir", side_effect=PermissionError("offline")):
            preview = rm_sync.w_resource_sync_scan(self.contract)

        self.assertEqual(preview["missing"], 0)
        source = next(row for row in preview["sources"] if row["location"] == "115")
        # 账本那几行所在的目录一个，来源根一个：同一个目录只数一次。
        self.assertEqual(source["unreadable"], 2)
        self.assertEqual(preview["empty"], 0)

    def test_background_sync_polls_then_rechecks_only_missing_candidates(self):
        idle = rm_sync.w_resource_sync_scan(
            self.contract, {"background": True, "status_only": True})
        self.assertEqual(idle["status"], "idle")
        with self._synced():
            started = rm_sync.w_resource_sync_scan(
                self.contract, {"background": True, "restart": True})
            self.assertIn(started["status"], {"running", "complete"})
            status = started
            deadline = time.monotonic() + 2
            while status["status"] == "running":
                status = rm_sync.w_resource_sync_scan(
                    self.contract, {"background": True})
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            self.assertEqual(status["status"], "complete")
            self.assertEqual(status["missing"], 2)
            resumed = rm_sync.w_resource_sync_scan(
                self.contract, {"background": True, "status_only": True})
            self.assertEqual(resumed["scan_id"], status["scan_id"])
            result = rm_sync.w_resource_sync_apply(self.contract, {
                "confirm": True, "clean_cache": False, "scan_id": status["scan_id"],
            })

        self.assertEqual((result["purged"], result["vanished"]), (1, 1))
        self.assertEqual(self.ids(), [1, 2, 4])

    def test_full_sync_keeps_a_cover_shared_by_an_active_asset(self):
        covers = self.root / "covers"
        covers.mkdir()
        shared = covers / "hey-002.jpg"
        shared.write_bytes(b"cover")
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE asset SET code='HEY-002' WHERE id IN (1,2)")
        con.commit();con.close()
        self.contract.cover_root = covers
        self.contract.avatar_root = self.root / "avatars"
        self.contract.poster_root = self.root / "posters"
        self.contract.photo_root = self.root / "photo-thumbs"
        self.contract.transcode_root = self.root / "transcodes"
        self.contract.stream_root = self.root / "stream-segments"
        self.contract.resource_cleanup_enabled = True
        with mock.patch.object(rm_sync, "translate_ledger_path", self._translate), \
             mock.patch.object(rm_sync, "source_is_online", lambda loc: loc == "115"):
            preview = rm_sync.w_resource_sync_scan(self.contract)
        self.assertEqual(preview["missing"], 2)
        self.assertTrue(shared.is_file())
        self.assertNotIn("covers", preview["cache"]["by_kind"])

    def test_cache_cleanup_never_crosses_the_database_data_root(self):
        """临时数据库漏配缓存根时，宁可不清理，也不能越界碰真实 generated。"""
        with tempfile.TemporaryDirectory() as outside_tmp:
            outside = Path(outside_tmp)
            cover = outside / "covers" / "orphan-001.jpg"
            segment = outside / "stream-segments" / "999" / "0.ts"
            cover.parent.mkdir()
            segment.parent.mkdir(parents=True)
            cover.write_bytes(b"cover")
            segment.write_bytes(b"segment")
            self.contract.cover_root = cover.parent
            self.contract.stream_root = outside / "stream-segments"
            self.contract.resource_cleanup_enabled = True

            result = rm_sync.clean_resource_orphans(self.contract)

            self.assertEqual(result["cache_removed"], 0)
            self.assertTrue(cover.is_file())
            self.assertTrue(segment.is_file())

    def test_intact_directory_reports_no_change(self):
        for name in ("002.jpg", "003.jpg"):
            (self.root / name).write_bytes(b"x")
        result = self._run()
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["checked"], 3)
        self.assertEqual(self.ids(), [1, 2, 3, 4])

    def test_purge_lists_the_directory_once_instead_of_statting_every_file(self):
        """逐条 is_file() 在云挂载上每条都是一次往返；已删路径没有负缓存，最贵。"""
        real_scandir = os.scandir
        with mock.patch.object(rm_sync, "translate_ledger_path", self._translate), \
             mock.patch.object(rm_sync, "source_is_online", lambda loc: loc == "115"), \
             mock.patch.object(rm_sync.os, "scandir", wraps=real_scandir) as scandir:
            result = rm_sync.w_purge_missing(self.contract, {"id": 1})
        self.assertEqual(result["removed"], 2)
        self.assertEqual(scandir.call_count, 1)

    def test_purge_keeps_rows_when_the_directory_cannot_be_read(self):
        """目录暂时读不了不是「文件被删」：与全量扫描同款语义。

        改动前这里逐条 `is_file()`，读不了会被当成全部缺失、整目录误入回收站。
        """
        with mock.patch.object(rm_sync, "translate_ledger_path", self._translate), \
             mock.patch.object(rm_sync, "source_is_online", lambda loc: loc == "115"), \
             mock.patch.object(rm_sync.os, "scandir", side_effect=PermissionError("busy")):
            result = rm_sync.w_purge_missing(self.contract, {"id": 1})
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["unreadable"], 3)
        self.assertEqual(self.ids(), [1, 2, 3, 4])

    def test_a_directory_listing_that_silently_skips_a_file_does_not_condemn_its_row(self):
        """网盘的目录枚举会成功返回、却少给几个名字。少给名字的那一趟不抛错，
        `unreadable` 数不到它，所以清理那一侧照名单删就会删掉几百行文件还在的资产。
        """
        for name in ("002.jpg", "003.jpg"):
            (self.root / name).write_bytes(b"x")
        with mock.patch.object(rm_sync, "translate_ledger_path", self._translate), \
             mock.patch.object(rm_sync, "_missing_resource_ids",
                               return_value=([1, 2, 3], 0)):
            rows = rm_sync.vanished_asset_rows(self.contract, "115")
        self.assertEqual(rows, [], "逐条问过一遍三个文件都在，一条都不该判失效")

    def test_a_file_that_is_really_gone_survives_the_second_look(self):
        with mock.patch.object(rm_sync, "translate_ledger_path", self._translate):
            rows = rm_sync.vanished_asset_rows(self.contract, "115")
        self.assertEqual([int(row["id"]) for row in rows], [2, 3])


class ResourceSyncCleanupTests(unittest.TestCase):
    """一次检查、一次执行：失效记录、空文件夹与孤儿缓存一起清。失效记录带个人记录的标已消失，
    其余直接永久删除。

    路径全是临时目录里的真路径，不经 `_translate`：执行那一步真的删行、删目录，
    删除那一侧（`web_batch`）看到的路径必须和检查那一侧是同一个。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.db_path = fresh_ledger(self.root)
        self.mount = self.root / "mount"
        offline = self.root / "offline"  # 不存在：这个来源没挂上
        self.keep = self.mount / "keep"
        self.keep.mkdir(parents=True)
        (self.keep / "a.mp4").write_bytes(b"keep")
        (self.mount / "empty" / "nested").mkdir(parents=True)
        self.hollow = self.mount / "release" / "文宣"
        self.hollow.mkdir(parents=True)
        self.unreadable = self.mount / "unreadable"
        self.unreadable.mkdir()
        (self.unreadable / "e.mp4").write_bytes(b"keep")
        rows = [
            (1, "115", self.keep / "a.mp4", None),
            (2, "115", self.mount / "gone" / "b.mp4", None),
            (3, "115", self.keep / "c.mp4", "trash"),
            (4, "pikpak", offline / "d.mp4", None),
            # 所在目录的枚举会报找不到，逐条问一次它却在：不能按枚举判它没了。
            (5, "115", self.unreadable / "e.mp4", None),
        ]
        con = sqlite3.connect(self.db_path)
        con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,size,disposal) "
            "VALUES(?,?,?,?,'video',10,?)",
            [(asset_id, location, str(path), path.name, disposal)
             for asset_id, location, path, disposal in rows])
        con.commit()
        con.close()
        self.contract = rm_web.WebContract(Path(self.db_path))
        posters = self.root / "posters"
        posters.mkdir()
        self.orphan_poster = posters / "2_1.jpg"
        self.live_poster = posters / "1_1.jpg"
        for poster in (self.orphan_poster, self.live_poster):
            poster.write_bytes(b"poster")
        self.contract.poster_root = posters
        self.contract.resource_cleanup_enabled = True
        declared = {"115": (str(self.mount),), "pikpak": (str(offline),)}
        for module in (rm_sync, web_batch):
            patcher = mock.patch.object(module, "LOCATION_ROOT_DECLARATIONS", declared)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.contract.resource_scan.stop()
        self.contract.resource_apply_job.stop()
        self.tmp.cleanup()

    def ids(self):
        with self.contract.read_connection() as connection:
            return [row[0] for row in connection.execute("SELECT id FROM asset ORDER BY id")]

    def clouddrive(self):
        """CloudDrive 的两种目录：空的那个列不出 `.` 与 `..`；有东西的那个这一轮读不了。

        两个都让 `scandir` 报找不到；`listdir` 给空列表的才算空文件夹。
        """
        failing = {os.path.normcase(os.fspath(path)) for path in (self.hollow, self.unreadable)}
        real_scandir = os.scandir

        def mount_scandir(path="."):
            if os.path.normcase(os.fspath(path)) in failing:
                raise FileNotFoundError(2, "系统找不到指定的文件。", os.fspath(path))
            return real_scandir(path)
        return mock.patch.object(os, "scandir", mount_scandir)

    def apply(self, scan, **body):
        return rm_sync.w_resource_sync_apply(
            self.contract, {"confirm": True, "scan_id": scan["scan_id"], **body})

    def test_check_reports_each_source_and_changes_nothing(self):
        with self.clouddrive():
            scan = finish_scan(self.contract)

        self.assertEqual(scan["status"], "complete")
        sources = {source["location"]: source for source in scan["sources"]}
        cloud = sources["115"]
        # 空文件夹：empty、empty/nested、release/文宣，以及只装着它的 release。
        self.assertEqual((cloud["missing"], cloud["empty"], cloud["unreadable"]), (2, 4, 1))
        self.assertEqual((scan["missing"], scan["empty"], scan["unreadable"]), (2, 4, 1))
        self.assertFalse(sources["pikpak"]["online"])
        self.assertEqual((sources["pikpak"]["missing"], sources["pikpak"]["total"]), (0, 1))
        self.assertEqual(scan["cache"]["files"], 1)
        self.assertEqual(self.ids(), [1, 2, 3, 4, 5], "检查只报数不删")
        self.assertTrue(self.hollow.is_dir())
        self.assertTrue((self.mount / "empty" / "nested").is_dir())
        self.assertTrue(self.orphan_poster.is_file())
        self.assertNotIn("mount", json.dumps(scan, ensure_ascii=False), "物理路径不出服务端")

    def test_apply_purges_vanished_rows_empty_folders_and_orphan_caches(self):
        with self.clouddrive():
            scan = finish_scan(self.contract)
            rm_sync.w_resource_sync_apply(self.contract, {
                "confirm": True, "background": True, "scan_id": scan["scan_id"]})
            self.contract.resource_apply_job.thread.join(10)
        result = self.contract.resource_apply_job.snapshot()

        self.assertEqual(result["status"], "complete")
        self.assertEqual((result["purged"], result["blocked"]), (2, []))
        self.assertEqual((result["dirs_removed"], result["dir_errors"]), (4, 0))
        self.assertEqual((result["cache_removed"], result["unreadable"]), (1, 1))
        # 在库的 2 与回收站里的 3 都删；文件还在的 1、5 和离线来源上的 4 一条不碰。
        self.assertEqual(self.ids(), [1, 4, 5])
        self.assertEqual((self.keep / "a.mp4").read_bytes(), b"keep")
        self.assertEqual((self.unreadable / "e.mp4").read_bytes(), b"keep")
        self.assertFalse((self.mount / "empty").exists())
        self.assertFalse((self.mount / "release").exists())
        self.assertTrue(self.mount.is_dir(), "声明的来源根绝不删")
        self.assertFalse(self.orphan_poster.exists())
        self.assertTrue(self.live_poster.is_file())
        run = self.contract.task_runs.query(task_key="resource-apply")[0]
        self.assertEqual(run.status, "succeeded", "删数据库行这件事要在任务中心留下记录")
        self.assertEqual((run.result_summary["purged"], run.result_summary["dirs_removed"],
                          run.result_summary["cache_removed"]), (2, 4, 1))

    def test_recycle_bin_rows_are_purged_even_when_they_carry_records(self):
        """回收站是用户自己丢的：带不带个人记录，文件不在了都照旧永久删除。"""
        with self.contract.write_transaction() as connection:
            connection.execute(
                "INSERT INTO asset_preference(profile_id,asset_id,liked,reason,updated_at) "
                "VALUES('default',3,1,'','2026-01-01')")
        with self.clouddrive():
            scan = finish_scan(self.contract)
            self.assertEqual((scan["purge"], scan["vanish"]), (2, 0))
            result = self.apply(scan)
        self.assertEqual((result["purged"], result["vanished"]), (2, 0))
        self.assertEqual(self.ids(), [1, 4, 5])

    def test_a_vanished_row_with_one_other_version_in_the_library_is_reattached(self):
        """新版本先入库、旧文件后消失：执行清理标已消失的同时当场接回，记自动接回批次。"""
        with self.contract.write_transaction() as connection:
            connection.execute("UPDATE asset SET code='ABC-123' WHERE id IN (1,2)")
            connection.execute(
                "INSERT INTO watch_queue(profile_id,asset_id,added_at) VALUES('default',2,'2026-01-01')")
        with self.clouddrive():
            scan = finish_scan(self.contract)
            result = self.apply(scan)
        self.assertEqual((result["purged"], result["vanished"], result["reattached"]), (1, 0, 1))
        self.assertEqual(self.ids(), [1, 4, 5])
        with self.contract.read_connection() as connection:
            self.assertEqual([r[0] for r in connection.execute("SELECT asset_id FROM watch_queue")], [1])
            self.assertEqual([tuple(r) for r in connection.execute(
                "SELECT source,old_asset_id,new_asset_id FROM record_rehome")],
                [("auto:vanished-reattach", 2, 1)])

    def test_a_record_added_after_the_check_still_saves_its_row(self):
        """有没有记录在执行那一刻重新问：检查之后刚点的喜欢也算。"""
        with self.clouddrive():
            scan = finish_scan(self.contract)
            self.assertEqual(scan["vanish"], 0)
            with self.contract.write_transaction() as connection:
                connection.execute(
                    "INSERT INTO watch_queue(profile_id,asset_id,added_at) VALUES('default',2,'2026-01-01')")
            result = self.apply(scan)
        self.assertEqual((result["purged"], result["vanished"]), (1, 1))
        self.assertEqual(self.ids(), [1, 2, 4, 5])

    def test_a_file_back_on_disk_since_the_check_keeps_its_row(self):
        scan = finish_scan(self.contract)
        (self.mount / "gone").mkdir()
        (self.mount / "gone" / "b.mp4").write_bytes(b"back")

        result = self.apply(scan)

        self.assertEqual(result["purged"], 1)
        self.assertEqual(self.ids(), [1, 2, 4, 5])
        self.assertEqual((self.mount / "gone" / "b.mp4").read_bytes(), b"back")

    def test_purge_never_deletes_a_file_that_answers_present_at_the_last_moment(self):
        """复核之后、删除之前文件回来了：删除那一步自己再问一次，只进 `blocked`。"""
        scan = finish_scan(self.contract)
        (self.mount / "gone").mkdir()
        (self.mount / "gone" / "b.mp4").write_bytes(b"back")

        with mock.patch.object(rm_sync, "_confirm_vanished", side_effect=list):
            result = self.apply(scan)

        self.assertEqual(result["purged"], 1)
        self.assertEqual(result["blocked"], [{"id": 2, "name": "b.mp4", "reason": "文件仍在盘上"}])
        self.assertEqual((self.mount / "gone" / "b.mp4").read_bytes(), b"back")
        self.assertIn(2, self.ids())

    def test_a_source_that_went_offline_after_the_check_is_left_alone(self):
        scan = finish_scan(self.contract)
        with mock.patch.object(rm_sync, "source_is_online", return_value=False):
            result = self.apply(scan)

        self.assertEqual((result["purged"], result["dirs_removed"]), (0, 0))
        self.assertEqual(self.ids(), [1, 2, 3, 4, 5])
        self.assertTrue((self.mount / "empty" / "nested").is_dir())

    def test_apply_only_accepts_the_latest_completed_check(self):
        with self.assertRaisesRegex(ValueError, "expired"):
            rm_sync.w_resource_sync_apply(self.contract, {"confirm": True})
        old = finish_scan(self.contract)
        new = finish_scan(self.contract)
        self.assertNotEqual(old["scan_id"], new["scan_id"])
        for body in ({"confirm": True}, {"confirm": True, "scan_id": old["scan_id"]},
                     {"confirm": True, "scan_id": old["scan_id"], "background": True}):
            with self.assertRaisesRegex(ValueError, "expired"):
                rm_sync.w_resource_sync_apply(self.contract, body)
        with self.assertRaisesRegex(ValueError, "confirmation"):
            rm_sync.w_resource_sync_apply(self.contract, {"scan_id": new["scan_id"]})
        self.assertIsNone(self.contract.resource_apply_job.snapshot(), "过期的检查不开执行任务")
        self.assertEqual(self.ids(), [1, 2, 3, 4, 5])

    def test_a_check_that_was_applied_reports_applied_and_cannot_run_again(self):
        """清过的那一轮不再拿旧读数冒充现状：状态带 `applied`，同一个 `scan_id` 不能再执行。"""
        scan = finish_scan(self.contract)
        self.assertFalse(scan["applied"])
        self.apply(scan)

        status = rm_sync.w_resource_sync_scan(
            self.contract, {"background": True, "status_only": True})
        self.assertEqual((status["status"], status["scan_id"], status["applied"]),
                         ("complete", scan["scan_id"], True))
        with self.assertRaisesRegex(ValueError, "expired"):
            self.apply(scan)
        self.assertFalse(finish_scan(self.contract)["applied"], "重新检查是新的一轮")

    def test_apply_is_gated_as_a_ledger_write_and_check_stays_read_only(self):
        self.assertIs(rm_web.POST_HANDLERS["/api/resource-sync/apply"], rm_sync.w_resource_sync_apply)
        self.assertNotIn("/api/resource-sync/apply", rm_web.READ_ONLY_POST_ROUTES,
                         "它永久删除数据库行，只读端那份复制来的数据库不能被它清掉")
        self.assertIn("/api/resource-sync/scan", rm_web.READ_ONLY_POST_ROUTES)
        self.assertNotIn("/api/data-cleanup/empty-folders", rm_web.POST_HANDLERS)
