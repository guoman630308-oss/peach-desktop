import json
import pathlib
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest import mock

from peach import catalog_rules, jav_poster_crop, web_batch, web_catalog, web_entity, web_playlists, web_stats
from peach import web_contract as rm_web
from peach.previews import entity_image_key, logo_key
from peach.web_catalog import q_editions, q_items, q_parts
from support.ledger import fresh_ledger


# 调用点继续走 `rm_web`（`web_contract` 现在只是再导出层），但 **patch 必须打在真的
# 那个模块上**：`web_stats.q_stats` 读的是 `web_stats` 自己模块里的 `system_volume`，
# 打在再导出层上不会有任何效果，而且不会报错——测试会带着假 mock 一路绿。
# 同一个 `LOCATION_ROOT_DECLARATIONS` 在存储卷一节属于 `web_stats`、在空目录清理
# 一节属于 `web_batch`，两个都得分别打。


class WebDataTests(unittest.TestCase):
    def test_recycle_bin_reasons_round_trip_and_restore_clears_them(self):
        for reason in ("manual", "junk", "duplicate", "missing"):
            rm_web.w_batch(self.contract, {"ids": [1], "operation": "dispose", "disposal_reason": reason})
            self.assertEqual(rm_web.q_items(self.contract, {"state": "trash"})["items"][0]["disposal_reason"], reason)
            self.assertEqual(rm_web.q_item(self.contract, 1)["disposal_reason"], reason)
            restored = rm_web.w_batch(self.contract, {"ids": [1], "operation": "restore"})
            self.assertEqual(restored["restored_reasons"], {"1": reason})
            self.assertIsNone(self.row()["disposal_reason"])
        with self.assertRaisesRegex(ValueError, "disposal reason"):
            rm_web.w_batch(self.contract, {"ids": [1], "operation": "dispose", "disposal_reason": "guessed"})
        self.assertIsNone(self.row()["disposal"])
        self.assertEqual(rm_web.w_feedback(self.contract, {"id": 1, "kind": "dispose"})["disposal_reason"], "manual")
        self.assertIsNone(rm_web.w_feedback(self.contract, {"id": 1, "kind": "dispose"})["disposal_reason"])

    def test_recycle_bin_legacy_reason_is_unknown(self):
        with self.contract.write_transaction() as connection:
            connection.execute("UPDATE asset SET disposal='trash' WHERE id=1")
        self.assertIsNone(rm_web.q_items(self.contract, {"state": "trash"})["items"][0]["disposal_reason"])
        restored = rm_web.w_batch(self.contract, {"ids": [1], "operation": "restore"})
        self.assertEqual(restored["restored_reasons"], {"1": None})
        rm_web.w_batch(self.contract, {"ids": [1], "operation": "dispose", "disposal_reason": None})
        self.assertIsNone(self.row()["disposal_reason"])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(fresh_ledger(self.tmp.name))
        con = sqlite3.connect(self.db_path)
        con.executemany(
            """INSERT INTO asset(
                 id,location,path,name,medium,size,creator,studio,code,duration,
                 width,height,ctx_length,ctx_orient,ctx_quality,first_seen)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (1, "local", r"R:\\Media\\one.mp4", "one.mp4", "video", 100,
                 "Alice", "Studio A", "ABC-001", 100, 1920, 1080, "速食", "横屏", "2K", "2026-08-14"),
                (2, "115", r"B:\\two.mp4", "two.mp4", "video", 200,
                 "Bob", None, None, 200, 1080, 1920, "速食", "竖屏", "1080P", "2026-08-13"),
                (3, "local", r"R:\\Media\\cover.jpg", "cover.jpg", "image", 10,
                 "Alice", None, None, None, None, None, None, None, None, "2026-08-12"),
            ],
        )
        con.executemany(
            "INSERT INTO asset_tag(asset_id,tag,source) VALUES(?,?,?)",
            [(1, "足交", "name"), (1, "演员:Alice", "performer"),
             (1, "Canonical Alice", "vision"), (2, "竖屏", "probe")],
        )
        con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(?,?,?,?,'2026-01-01','2026-01-01')",
            [(10, "tag", "足交", "足交"),
             (11, "performer", "Canonical Alice", "canonical alice"),
             (12, "creator", "Canonical Creator", "canonical creator"),
             (13, "studio", "Canonical Studio", "canonical studio"),
             (90, "tag", "Canonical Alice", "canonical alice")],
        )
        con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(?,?,?,?,?)",
            [(1, 10, "tag", "test", 1.0),
             (1, 11, "performer", "test", 1.0),
             (1, 12, "creator", "test", 1.0),
             (2, 12, "creator", "test", 1.0),
             (1, 13, "studio", "test", 1.0),
             (2, 13, "studio", "test", 1.0),
             (1, 90, "tag", "test", 1.0)],
        )
        con.commit()
        con.close()
        # 标识目录显式落在临时目录：默认值是本机真实的 generated 树，「这个厂牌有没有
        # 图」会随本机装了什么而变，测试跟着本机状态摇。
        self.logos = Path(self.tmp.name).resolve() / "logos"
        self.logos.mkdir()
        # 实体图和已裁好的头像同处 `avatar_root`，同样得落在临时目录里。
        self.avatars = Path(self.tmp.name).resolve() / "avatars"
        self.avatars.mkdir()
        self.contract = rm_web.WebContract(
            Path(self.db_path), avatar_root=self.avatars, logo_root=self.logos)

    def tearDown(self):
        self.tmp.cleanup()

    def row(self, aid=1):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM asset WHERE id=?", (aid,)).fetchone()
        con.close()
        return row

    def test_creator_card_and_detail_share_available_entity_image(self):
        (self.avatars / f"{entity_image_key('creator', 12)}.img").write_bytes(b"image")
        row = rm_web.q_items(self.contract, {"limit": "10"})["items"][0]
        ref = {"id": 12, "name": "Canonical Creator", "has_image": True,
               "image_version": self.contract.entity_image_version("creator", 12),
               "avatar_focus": None}
        self.assertTrue(ref["image_version"])
        self.assertEqual(row["creator_entity"], ref)
        self.assertEqual(row["creator"], ref["name"])
        self.assertEqual(rm_web.q_item(self.contract, row["id"])["entity_refs"]["creator"][0], ref)
        cards = [{"id": 2, "creator": "Bob"}, {"id": 3, "creator": "Alice"}]
        rm_web.attach_card_performers(self.contract, cards)
        self.assertEqual(cards[0]["creator_entity"], ref)
        self.assertIsNone(cards[1]["creator_entity"])
        self.assertEqual(cards[1]["creator"], "Alice")
        (self.avatars / f"{entity_image_key('creator', 12)}.img").unlink()
        fresh = rm_web.WebContract(Path(self.db_path), avatar_root=self.avatars, logo_root=self.logos)
        rm_web.attach_card_performers(fresh, cards)
        self.assertFalse(cards[0]["creator_entity"]["has_image"])

    def test_default_database_connection_is_readonly(self):
        con = self.contract.database.connect()
        with self.assertRaises(sqlite3.OperationalError):
            con.execute("UPDATE asset SET name='must-not-write' WHERE id=1")
        con.close()

    def test_people_index_subscription_uses_enabled_sources_bound_to_entity(self):
        from peach import feeds
        with closing(sqlite3.connect(self.db_path)) as con:
            active = feeds.add_source(con, url='https://javdb.com/actors/Alice', entity_id=11)
            paused = feeds.add_source(con, url='https://javdb.com/actors/AliceOld', entity_id=11)
            feeds.set_enabled(con, paused, False)
            feeds.add_source(con, url='https://javdb.com/actors/Other', name='Canonical Creator')
            con.commit()
        people = rm_web.q_index(self.contract, 'performers')['items']
        self.assertTrue(next(row for row in people if row['entity_id'] == 11)['following'])
        self.assertFalse(next(row for row in people if row['entity_id'] == 12)['following'])
        filtered = rm_web.q_index(self.contract, 'performers', q='Alice', limit=1)['items']
        self.assertEqual(len(filtered), 1)
        self.assertTrue(filtered[0]['following'])
        self.assertTrue(rm_web.q_index(self.contract, 'performers', limit=1, offset=1)['items'][0]['following'])
        with closing(sqlite3.connect(self.db_path)) as con:
            feeds.set_enabled(con, active, False)
            con.commit()
        self.assertFalse(rm_web.q_index(self.contract, 'performers', q='Alice')['items'][0]['following'])

    def test_people_index_marks_enabled_follow_sources_by_confirmed_identity(self):
        from peach.entity_classification import write_claim
        with closing(sqlite3.connect(self.db_path)) as con:
            con.execute("INSERT INTO follow_source(entity_id,provider,ref,label,url,created_at,updated_at) "
                        "VALUES(12,'patreon','test','Canonical Creator','https://www.patreon.com/test',"
                        "'2026-01-01','2026-01-01')")
            con.commit()
        rows = rm_web.q_index(self.contract, 'performers', q='Creator', limit=1)['items']
        self.assertTrue(rows[0]['following'])
        with closing(sqlite3.connect(self.db_path)) as con:
            write_claim(con, entity_id=12, facet='account_role', value='seller',
                        source='source:fc2', source_url='https://adult.contents.fc2.com/article/42/',
                        evidence='seller account', status='observed', confidence=1)
            con.commit()
        rows = rm_web.q_index(self.contract, 'creators', q='Creator', limit=1)['items']
        self.assertTrue(rows[0]['following'])
        with closing(sqlite3.connect(self.db_path)) as con:
            con.execute("UPDATE follow_source SET enabled=0")
            con.commit()
        self.assertFalse(rm_web.q_index(self.contract, 'creators')['items'][0]['following'])
        with closing(sqlite3.connect(self.db_path)) as con:
            con.execute("UPDATE follow_source SET enabled=1,entity_id=NULL")
            con.commit()
        self.assertFalse(rm_web.q_index(self.contract, 'creators')['items'][0]['following'])

    def test_seller_references_share_profile_work_portrait_and_trusted_role(self):
        from peach.entity_classification import write_claim
        with closing(sqlite3.connect(self.db_path)) as con:
            write_claim(con, entity_id=12, facet='account_role', value='seller',
                        source='source:fc2', source_url='https://adult.contents.fc2.com/article/42/',
                        evidence='seller account', status='observed', confidence=1)
            con.execute("UPDATE asset SET snapshot_path='seller.jpg' WHERE id=2")
            con.commit()
        (self.avatars / '2.jpg').write_bytes(b'portrait')
        profile = web_entity.q_entity(self.contract, {'kind':'creator', 'name':'Canonical Creator'})
        detail = rm_web.q_item(self.contract, 2)
        cards = rm_web.q_items(self.contract, {'limit':'10'})['items']
        attached = [{'id':2}]
        rm_web.attach_card_performers(self.contract, attached)
        for ref in [detail['entity_refs']['creator'][0], attached[0]['creator_entity'],
                    next(card for card in cards if card['id'] == 2)['creator_entity']]:
            self.assertEqual(ref['rep'], profile['representative_asset_id'])
            self.assertTrue(ref['has_avatar'])
            self.assertTrue(ref['avatar_stand_in'])
            self.assertEqual(ref['identity_categories'], ['seller'])
            self.assertFalse(ref['has_image'])
        self.assertEqual(detail['performers'], [])
        with closing(sqlite3.connect(self.db_path)) as con:
            con.execute("UPDATE entity_classification SET status='candidate' WHERE entity_id=12")
            con.commit()
        pending = rm_web.q_item(self.contract, 2)['entity_refs']['creator'][0]
        self.assertNotIn('seller', pending.get('identity_categories', []))

    def test_items_are_filtered_and_do_not_expose_paths(self):
        result = rm_web.q_items(
            self.contract, {"loc": "local", "sort": "new", "limit": "10"},
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["id"], 1)
        self.assertEqual(result["items"][0]["performers"], ["Canonical Alice"])
        self.assertEqual(result["items"][0]["performer_entities"], [
            {"id": 11, "name": "Canonical Alice", "has_image": False},
        ])
        self.assertEqual(result["items"][0]["tags"], ["足交"])
        self.assertIn("play_seconds", result["items"][0])
        self.assertIn("duration", result["items"][0])
        self.assertNotIn("Canonical Alice", [tag["k"] for tag in rm_web.q_item(self.contract, 1)["tags"]])
        self.assertNotIn("Canonical Alice", [tag["k"] for tag in rm_web.q_facets(self.contract)["tags"]])
        self.assertNotIn("Canonical Alice", [tag["k"] for tag in rm_web.q_index(self.contract, "tags")["items"]])
        self.assertNotIn("Canonical Alice", [tag["k"] for tag in rm_web.q_stats(self.contract)["top_tags"]])
        self.assertNotIn("path", result["items"][0])
        self.assertNotIn("snapshot_path", result["items"][0])

    def test_item_detail_marks_javinizer_and_fc2_official_tags(self):
        con = sqlite3.connect(self.db_path)
        con.executemany(
            "INSERT INTO asset_tag(asset_id,tag,confidence,source) "
            "VALUES(1,?,?,?)",
            [("官方标签", 0.9, "javinizer:r18dev:tag"),
             ("FC2 官方标签", 0.8, "javinizer:fc2:tag")],
        )
        con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(?,'tag',?,?,'2026-01-01','2026-01-01')",
            [(991, "官方标签", "官方标签"), (992, "FC2 官方标签", "fc2 官方标签")],
        )
        con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(1,?,'tag',?,?)",
            [(991, "javinizer:r18dev:tag", 0.9),
             (992, "javinizer:fc2:tag", 0.8)],
        )
        con.commit(); con.close()
        tags = {tag["k"]: tag for tag in rm_web.q_item(self.contract, 1)["tags"]}
        self.assertTrue(tags["官方标签"]["official"])
        self.assertTrue(tags["FC2 官方标签"]["official"])

    def test_multiple_tags_support_all_and_broad_any_matching(self):
        strict = rm_web.q_items(
            self.contract, {"tag": "足交,竖屏", "sort": "new", "limit": "10"},
        )
        broad = rm_web.q_items(
            self.contract,
            {"tag": "足交,竖屏", "tag_match": "any", "sort": "new", "limit": "10"},
        )
        self.assertEqual(strict["items"], [])
        self.assertEqual({item["id"] for item in broad["items"]}, {1, 2})

    def test_canonical_creator_beats_same_named_legacy_performer_tag(self):
        """1065 类数据不能被旧 `演员:` 投影改成艺人。"""
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO asset_tag(asset_id,tag,source) "
            "VALUES(2,'演员:Canonical Creator','performer')"
        )
        con.commit(); con.close()

        item = rm_web.q_item(self.contract, 2)
        self.assertEqual(item["creator"], "Canonical Creator")
        self.assertEqual(item["entities"]["creator"], ["Canonical Creator"])
        self.assertEqual(item["entity_refs"]["creator"], [
            {"id": 12, "name": "Canonical Creator", "has_image": False},
        ])
        self.assertEqual(item["performers"], [])
        self.assertEqual(item["entity_refs"]["performer"], [])

    def test_reviewed_creator_tags_are_included_in_tag_coverage(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO asset_tag(asset_id,tag,source) VALUES(2,'审核标签','vision_creator_review')"
        )
        con.commit(); con.close()
        stats = rm_web.q_stats(self.contract)
        self.assertEqual(stats["tag_cov"], 2)
        self.assertIn("审核标签", {row["k"] for row in stats["top_tags"]})

    def test_stats_band_videos_by_length_and_quality_in_probe_order(self):
        stats = rm_web.q_stats(self.contract)
        self.assertEqual(stats["by_length"], [
            {"k": "速食", "n": 2}, {"k": "短", "n": 0}, {"k": "中", "n": 0}, {"k": "长", "n": 0}])
        self.assertEqual(stats["by_quality"], [
            {"k": "4K", "n": 0}, {"k": "2K", "n": 1}, {"k": "1080P", "n": 1},
            {"k": "720P", "n": 0}, {"k": "低画质", "n": 0}])

    def test_stats_play_activity_and_replays_count_each_work_once(self):
        """在线追更那条关联的馆藏视频自己播过，就只按馆藏那一次算；没关联的照常算。"""
        late_monday = datetime(2026, 9, 21, 23, 30).timestamp()
        early_tuesday = datetime(2026, 9, 22, 0, 10).timestamp()
        sunday = datetime(2026, 9, 20, 8, 5).timestamp()
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("UPDATE asset SET play_count=3,last_played=? WHERE id=1", (str(late_monday),))
            connection.execute("UPDATE asset SET play_count=1,last_played=? WHERE id=2", (str(early_tuesday),))
            connection.execute(
                "INSERT INTO follow_source(id,provider,ref,label,url,created_at,updated_at) "
                "VALUES(1,'x','ref','Source','https://example.test','2026-01-01','2026-01-01')")
            connection.executemany(
                "INSERT INTO follow_item(id,source_id,external_id,title,release_key,asset_id,"
                "first_seen_at,last_seen_at) VALUES(?,1,?,?,?,?,'2026-01-01','2026-01-01')",
                [(1, "saved", "Saved", "saved", 1), (2, "online", "Online", "online", None)])
            connection.executemany(
                "INSERT INTO follow_playback(follow_item_id,play_count,last_played) VALUES(?,?,?)",
                [(1, 5, sunday), (2, 1, sunday)])
            connection.executemany(
                "INSERT INTO asset_preference(profile_id,asset_id,liked,reason,updated_at) "
                "VALUES('local-default',?,?,?,'2026-09-22')",
                [(1, 0, "只写了理由"), (2, 1, "")])
            connection.commit()

        stats = rm_web.q_stats(self.contract)

        self.assertEqual(stats["consumption"]["played"], 3)
        self.assertEqual(stats["consumption"]["liked"], 1)
        self.assertEqual(stats["replays"], [{"k": 1, "n": 2}, {"k": 3, "n": 1}])
        activity = stats["play_activity"]
        self.assertRegex(activity["timezone"], r"^UTC[+-]\d{2}:\d{2}$")
        self.assertEqual(activity["days"], [
            {"date": "2026-09-20", "count": 1}, {"date": "2026-09-21", "count": 1},
            {"date": "2026-09-22", "count": 1}])
        self.assertEqual(activity["hours"], [
            {"weekday": 0, "hour": 23, "count": 1}, {"weekday": 1, "hour": 0, "count": 1},
            {"weekday": 6, "hour": 8, "count": 1}])

    def test_library_counts_follow_named_roots_and_exclude_images(self):
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("UPDATE asset SET path=? WHERE id=1", (r"R:\Media\one.mp4",))
            connection.execute("UPDATE asset SET path=? WHERE id=3", (r"R:\Media\cover.jpg",))
            connection.commit()
        # 改真配置的这几个字段，不另捏一个只带它们的对象：`active()` 是全模块共用的，
        # 存储卷那一段顺着 `translate_ledger_path` 也要读 `mounts`，缺字段就是
        # AttributeError。Windows 上账本路径不必翻译、走不到那一步，只有 macOS 会红。
        config = replace(
            web_stats.settings_file.load_config(environ={}, strict=False),
            locations={"local": (r"R:\Media", r"R:\Media-more")},
            library_names={}, library_icons={})
        with mock.patch.object(web_stats.settings_file, "active", return_value=config):
            rows = rm_web.q_stats(self.contract)["by_library"]
        self.assertEqual([(row["name"], row["videos"], row["bytes"]) for row in rows],
                         [("Media", 1, 100), ("Media-more", 0, 0)])

    def test_stats_use_the_platform_system_volume_and_keep_the_old_alias(self):
        usage = type("Usage", (), {"free": 20, "total": 100})
        with mock.patch.object(web_stats, "system_volume", return_value=Path("X:/")), mock.patch(
            "shutil.disk_usage", return_value=usage,
        ):
            stats = rm_web.q_stats(self.contract)
        # 断言的是「用了 platform 给的系统卷」，不是盘符的字符串写法：
        # `Path("X:/")` 在 Windows 上渲染成 `X:\`、在 POSIX 上渲染成 `X:/`，
        # 写死反斜杠会让这条只在 Windows 通过。
        self.assertEqual(
            stats["system_disk"],
            {"root": str(Path("X:/")), "free": 20, "total": 100},
        )
        self.assertIs(stats["disk_c"], stats["system_disk"])

    def test_stats_report_system_resource_and_cloud_volumes(self):
        usage = type("Usage", (), {"free": 20, "total": 100})
        declarations = {"local": ("R:/media", "S:/more"), "115": ("B:/",), "pikpak": ("A:/",)}
        with mock.patch.object(web_stats, "LOCATION_ROOT_DECLARATIONS", declarations), \
                mock.patch.object(web_stats, "system_volume", return_value=Path("X:/")), \
                mock.patch.object(web_stats, "translate_ledger_path",
                                  side_effect=lambda value: Path(value)), \
                mock.patch.object(web_stats, "root_online", return_value=True), \
                mock.patch("shutil.disk_usage", return_value=usage):
            stats = rm_web.q_stats(self.contract)

        # 同一来源的几个根各占一行，用序号区分；单根来源不带序号。
        self.assertEqual(
            [(row["kind"], row["label"]) for row in stats["storage_volumes"]],
            [("system", "系统盘"), ("local", "资源盘 1"), ("local", "资源盘 2"),
             ("115", "115 网盘"), ("pikpak", "PikPak 网盘")],
        )
        self.assertEqual(stats["storage_summary"], {
            "volumes": 5, "online": 5, "measured": 5,
            "free": 100, "used": 400, "total": 500,
        })

    def test_items_support_duration_range(self):
        result = rm_web.q_items(
            self.contract, {"dur_min": "90", "dur_max": "110", "limit": "10"},
        )
        self.assertEqual([item["id"] for item in result["items"]], [1])

    def test_explicit_ab_parts_share_one_derived_group_and_ordered_queue(self):
        connection = sqlite3.connect(self.db_path)
        connection.executemany(
            "INSERT INTO asset(id,location,path,name,medium,size,studio,code,duration,"
            "width,height,first_seen) VALUES(?,'local',?,?,'video',?,'S1','OJIE-325',?,"
            "1920,1080,'2026-08-28')",
            [
                (4, r"R:\OJIE-325-B.mp4", "OJIE-325-B.mp4", 10_000, 14349),
                (5, r"R:\OJIE-325-A.mp4", "OJIE-325-A.mp4", 20_000, 14281),
            ],
        )
        connection.commit(); connection.close()

        listed = rm_web.q_items(
            self.contract, {"q": "OJIE-325", "sort": "new", "limit": "10"},
        )["items"]
        self.assertEqual(len(listed), 2, "API 仍保留两个真实资产")
        self.assertEqual({row["part_group"]["key"] for row in listed}, {"OJIE-325"})
        self.assertEqual(listed[0]["part_group"]["count"], 2)
        self.assertEqual(listed[0]["part_group"]["seed_id"], 5)
        self.assertEqual(listed[0]["part_group"]["total_size"], 30_000)

        parts = rm_web.q_parts(self.contract, {"id": "4"})
        self.assertEqual(parts["title"], "OJIE-325")
        self.assertEqual([item["name"] for item in parts["items"]],
                         ["OJIE-325-A.mp4", "OJIE-325-B.mp4"])
        self.assertEqual([item["part_label"] for item in parts["items"]], ["A", "B"])

    def test_facets_are_scoped_to_the_current_entity_or_item(self):
        creator = rm_web.q_facets(
            self.contract, scope_kind="creator", scope_name="Canonical Creator",
        )
        self.assertEqual({row["k"] for row in creator["locations"]}, {"local", "115"})
        self.assertEqual({row["k"] for row in creator["orientations"]}, {"横屏", "竖屏"})
        self.assertEqual(creator["stats"]["duration"], 2)

        item = rm_web.q_facets(self.contract, asset_id=1)
        self.assertEqual(item["locations"], [{"k": "local", "n": 1, "played": 0}])
        self.assertEqual(item["orientations"], [{"k": "横屏", "n": 1}])
        self.assertEqual([row["k"] for row in item["creators"]], ["Canonical Creator"])
        self.assertEqual([row["k"] for row in item["tags"]], ["足交"])
        self.assertEqual(item["stats"]["duration"], 1)

        tag = rm_web.q_facets(self.contract, scope_kind="tag", scope_name="足交")
        self.assertEqual(tag["locations"], item["locations"])
        self.assertEqual(tag["stats"]["duration"], 1)
        self.assertEqual([row["k"] for row in tag["tags"]], ["足交"])

    def test_studio_spellings_share_videos_pagination_and_facets(self):
        alias = 'CANONICAL STUDIO（カノニカル）'
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("INSERT INTO entity_alias VALUES(13,?,?,'merge:test',1)",
                               (alias, alias.casefold()))
        expected = rm_web.q_items(self.contract, {'studio': 'Canonical Studio', 'sort': 'new'})
        facets = rm_web.q_facets(self.contract, scope_kind='studio', scope_name='Canonical Studio')
        for name in ('CANONICAL STUDIO', alias):
            with self.subTest(name=name):
                items = rm_web.q_items(self.contract, {'studio': name, 'sort': 'new'})
                self.assertEqual(items['total'], 2)
                self.assertEqual([row['id'] for row in items['items']],
                                 [row['id'] for row in expected['items']])
                first = rm_web.q_items(self.contract, {'studio': name, 'sort': 'new', 'limit': '1'})
                next_page = rm_web.q_items(self.contract, {'studio': name, 'sort': 'new',
                                                         'limit': '1', 'offset': '1', 'count': '0'})
                self.assertEqual([row['id'] for row in first['items'] + next_page['items']],
                                 [row['id'] for row in expected['items']])
                self.assertEqual(rm_web.q_facets(self.contract, scope_kind='studio', scope_name=name), facets)

    def test_profile_kinds_share_registered_alias_scopes(self):
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.executemany("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
                "VALUES(?,?,?,?, 't','t')", [(14,'series','Series','series'),(15,'agency','Agency','agency')])
            connection.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) VALUES(2,14,'series','test',1)")
            connection.execute("INSERT INTO entity_membership(member_id,agency_id,source,checked_at) "
                               "VALUES(11,15,'test','t')")
            connection.executemany("INSERT INTO entity_alias VALUES(?,?,?,'test',1)",
                [(11,'Performer Alias','performer alias'),(12,'Creator Alias','creator alias'),
                 (14,'Series Alias','series alias'),(15,'Agency Alias','agency alias')])
        for kind, canonical, alias in [('performer','Canonical Alice','Performer Alias'),
                                     ('creator','Canonical Creator','Creator Alias'),
                                     ('series','Series','Series Alias'), ('agency','Agency','Agency Alias')]:
            with self.subTest(kind=kind):
                actual = rm_web.q_items(self.contract, {kind: alias})
                expected = rm_web.q_items(self.contract, {kind: canonical})
                self.assertGreater(actual['total'], 0)
                self.assertEqual([row['id'] for row in actual['items']], [row['id'] for row in expected['items']])
                self.assertEqual(rm_web.q_facets(self.contract, scope_kind=kind, scope_name=alias),
                                 rm_web.q_facets(self.contract, scope_kind=kind, scope_name=canonical))

    def test_unknown_or_ambiguous_studio_scopes_have_no_videos(self):
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
                               "VALUES(14,'studio','Other','other','t','t')")
            connection.executemany("INSERT INTO entity_alias VALUES(?,'Shared','shared','test',1)", [(13,),(14,)])
        for name in ('Missing', 'Shared'):
            self.assertEqual(rm_web.q_items(self.contract, {'studio': name})['total'], 0)
            self.assertEqual(rm_web.q_facets(self.contract, scope_kind='studio', scope_name=name)['locations'], [])

    def test_studio_facets_include_descendant_label_videos(self):
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
                               "VALUES(14,'studio','Label','label','t','t')")
            connection.execute('DELETE FROM asset_entity WHERE asset_id=2 AND entity_id=13')
            connection.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) VALUES(2,14,'studio','test',1)")
            connection.execute("INSERT INTO label_maker(label_id,maker_id,source,checked_at) "
                               "VALUES(14,13,'test','t')")
        videos = rm_web.q_items(self.contract, {'studio': 'Canonical Studio'})
        facets = rm_web.q_facets(self.contract, scope_kind='studio', scope_name='Canonical Studio')
        self.assertEqual(videos['total'], 2)
        self.assertEqual(sum(row['n'] for row in facets['locations']), 2)

    def test_facets_and_tops_narrow_to_the_state_the_page_is_showing(self):
        """「已标记」页的顶部三层和筛选面板曾走全库口径。

        结果是上面列着一排头像和一排标签，它们在本页一个作品都没有，
        点进去就是空列表。筛选项必须来自和作品列表相同的集合。
        """
        rm_web.w_preference(self.contract, {"id": 2, "liked": True, "reason": ""})
        self.assertEqual(
            [item["id"] for item in rm_web.q_items(
                self.contract, {"state": "flagged", "limit": "10"})["items"]],
            [2],
        )

        # 不带 state 仍然是全库口径，首页不受影响。
        everything = rm_web.q_facets(self.contract)
        self.assertEqual({row["k"] for row in everything["locations"]}, {"local", "115"})

        flagged = rm_web.q_facets(self.contract, state="flagged")
        self.assertEqual({row["k"] for row in flagged["locations"]}, {"115"})
        self.assertEqual({row["k"] for row in flagged["orientations"]}, {"竖屏"})
        local = rm_web.q_facets(self.contract, filters={"loc": "local"})
        remote = rm_web.q_facets(self.contract, filters={"loc": "115"})
        self.assertEqual(local['stats']['total'], 1)
        self.assertEqual(remote['stats']['total'], 1)
        self.assertEqual({row['k'] for row in local['locations']}, {'local', '115'})
        self.assertNotEqual(local['orientations'], remote['orientations'])
        empty = rm_web.q_facets(self.contract, filters={"q": "no-matching-demo"})
        self.assertEqual(empty['tags'], [])
        self.assertEqual(empty['creators'], [])
        self.assertEqual(empty['stats']['total'], 0)
        self.assertNotIn("足交", [row["k"] for row in flagged["tags"]])

        # 顶部三层同一口径：Alice 只在 1 号上，应该整个消失；
        # 厂牌两边都有，但计数要收窄到 1。
        tops = rm_web.q_tops(self.contract, 30, state="flagged")
        self.assertEqual([row["k"] for row in tops["performers"]], [])
        self.assertEqual([(row["k"], row["n"]) for row in tops["studios"]],
                         [("Canonical Studio", 1)])
        self.assertEqual(
            [(row["k"], row["n"]) for row in rm_web.q_tops(self.contract, 30)["studios"]],
            [("Canonical Studio", 2)])

    def test_studio_marks_say_up_front_whether_a_logo_is_installed(self):
        """三处厂牌取图位都要随资料下发「装了没有」。

        缺这个标志，页面只能无条件出 `<img>`、等 `/logo` 回 404 再换成首字母：顶栏
        一排 30 个厂牌里实测 21 个没有图，而 404 那条响应不可缓存，每次重绘再打一
        整轮。判据是目录索引，不查库。
        """
        def flags():
            item = rm_web.q_item(self.contract, 1)
            page = rm_web.q_entity(
                self.contract, {"kind": "studio", "name": "Canonical Studio"})
            return {
                "pill": [row["has_logo"] for row in
                         rm_web.q_tops(self.contract, 30)["studios"]],
                "ref": [ref["has_logo"] for ref in item["entity_refs"]["studio"]],
                # 非规范厂牌只有扁平 `studio` 字段，它的可用性单独一格；漏了这格，
                # 那条路径会从「本来能取到图」退化成永远只显示首字母。
                "flat": item["has_studio_logo"],
                "hero": page["has_logo"],
            }

        self.assertEqual(
            flags(), {"pill": [False], "ref": [False], "flat": False, "hero": False},
            "一张图都没装时三处都必须说没有")

        (self.logos / f"{logo_key('Canonical Studio')}.img").write_bytes(b"x")
        (self.logos / f"{logo_key('Studio A')}.icon.img").write_bytes(b"x")
        self.contract.cache_bust()
        self.assertEqual(
            flags(), {"pill": [True], "ref": [True], "flat": True, "hero": True},
            "装上之后三处都必须说有")

    def test_face_slots_say_up_front_whether_an_image_can_be_fetched(self):
        """四个取图位都要随资料下发「取不取得到」。

        缺这个标志，页面只能无条件出 `<img>`、等 404 再换成首字母：一个作品详情页
        实测 9 个这样的 404（1 个厂牌实体图、4 个人物实体图、4 个头像），首页手机
        视口 2 个，而 `/entity-image` 与 `/avatar` 的 404 都不带缓存头，每次重绘再打
        一整轮。

        两级图的判据不一样，所以分成两个标志：实体图有就是有；头像是按需生成的，
        「还没裁过但印相还在」仍然取得到，把它一起判成没有就等于把点一下就有的那条
        路关掉。
        """
        def flags():
            item = rm_web.q_item(self.contract, 1)
            page = rm_web.q_entity(
                self.contract, {"kind": "performer", "name": "Canonical Alice"})
            tops = rm_web.q_tops(self.contract, 30)["performers"]
            cards = rm_web.q_items(self.contract, {"limit": "5", "offset": "0"})["items"]
            return {
                # 顶栏圆头像：实体图与代表作头像各一格。
                "top_image": [row["has_image"] for row in tops],
                "top_avatar": [row["has_avatar"] for row in tops],
                # 卡片署名圈的身份引用（`/api/items`）。
                "card": [ref["has_image"] for row in cards
                         for ref in row["performer_entities"]],
                # 详情页身份格（`/api/item`）。
                "cell": [ref["has_image"] for ref in item["entity_refs"]["performer"]],
                # 资料页大位（`/api/entity`）：同样是两级。
                "hero_image": page["has_image"],
                "hero_avatar": page["has_avatar"],
            }

        self.assertEqual(
            flags(),
            {"top_image": [False], "top_avatar": [False], "card": [False],
             "cell": [False], "hero_image": False, "hero_avatar": False},
            "一张图都没有时四处都必须说没有")

        (self.avatars / f"{entity_image_key('performer', 11)}.img").write_bytes(b"x")
        snapshot = Path(self.tmp.name).resolve() / "one.jpg"
        snapshot.write_bytes(b"x")
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE asset SET snapshot_path=? WHERE id=1", (str(snapshot),))
        con.commit()
        con.close()
        self.contract.cache_bust()
        self.assertEqual(
            flags(),
            {"top_image": [True], "top_avatar": [True], "card": [True],
             "cell": [True], "hero_image": True, "hero_avatar": True},
            "装上实体图、印相也在盘上之后，四处都必须说有")

    def test_the_identity_reference_carries_the_face_focus(self):
        """取景跟着身份引用走，理由和 `has_image` 一样：读它的位置拿不到第二份数据。

        卡片署名、详情页身份格、顶栏署名圈、播放列表卡的脸都只拿得到这一份引用。
        缺了取景它们只能几何居中，而人脸落在画面顶上的那批（`focus.pct` 为 0）正好
        被裁掉脑袋——出镜者那一排实测五个人有四个是这样。
        """
        def focuses():
            item = rm_web.q_item(self.contract, 1)
            cards = rm_web.q_items(self.contract, {"limit": "5", "offset": "0"})["items"]
            return {
                "card": [ref.get("avatar_focus") for row in cards
                         for ref in row["performer_entities"]],
                "cell": [ref.get("avatar_focus")
                         for ref in item["entity_refs"]["performer"]],
            }

        key = entity_image_key("performer", 11)
        (self.avatars / f"{key}.img").write_bytes(b"x")
        self.contract.cache_bust()
        self.assertEqual(focuses(), {"card": [None], "cell": [None]},
                         "装了图却没算过取景时，键在、值是 None，页面据此维持几何居中")

        (self.avatars / f"{key}.face.json").write_text(
            '{"ratio":0.667,"face":{"cx":0.5,"cy":0.08},'
            '"focus":{"axis":"y","pct":0}}', encoding="utf-8")
        self.contract.cache_bust()
        self.assertEqual(focuses(),
                         {"card": [{"axis": "y", "pct": 0}],
                          "cell": [{"axis": "y", "pct": 0}]},
                         "脸贴在图最顶上时两处都要拿到 pct 0，几何居中会把头裁掉")

    def test_a_representative_without_its_snapshot_is_not_an_avatar(self):
        """账本记着印相路径、盘上却没有那个文件时，`/avatar` 一样取不到。

        `rep` 的选取只看 `snapshot_path IS NOT NULL`，那是账本里的一行字符串；
        真正决定取不取得到的是文件在不在，判据和卡片角上的 `has_thumb` 同一个。
        """
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE asset SET snapshot_path=? WHERE id=1",
                    (str(Path(self.tmp.name).resolve() / "gone.jpg"),))
        con.commit()
        con.close()
        self.contract.cache_bust()
        tops = rm_web.q_tops(self.contract, 30)["performers"]
        self.assertEqual([row["rep"] for row in tops], [1], "代表作仍会被选出来")
        self.assertEqual([row["has_avatar"] for row in tops], [False])

    def test_a_work_with_several_performers_is_nobodys_representative(self):
        """代表作头像是从接触印相里裁脸：多人作品的每个出演者都会拿到同一张脸。"""
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE asset SET snapshot_path='one.jpg' WHERE id IN (1,2)")
        con.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
                    "VALUES(14,'performer','Carol','carol','2026-01-01','2026-01-01')")
        con.executemany("INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
                        "VALUES(?,?,'performer','test',1.0)", [(1, 14), (2, 11)])
        con.commit()
        con.close()
        self.contract.cache_bust()
        tops = {row["k"]: row["rep"] for row in rm_web.q_tops(self.contract, 30)["performers"]}
        self.assertEqual(tops, {"Canonical Alice": 2, "Carol": None})
        pages = {name: rm_web.q_entity(self.contract, {"kind": "performer", "name": name})
                 for name in ("Carol", "Canonical Alice")}
        self.assertEqual({name: page["representative_asset_id"] for name, page in pages.items()},
                         {"Carol": None, "Canonical Alice": 2})
        studios = {row["k"]: row["rep"] for row in rm_web.q_tops(self.contract, 30)["studios"]}
        self.assertIsNotNone(studios["Canonical Studio"], "厂牌不看出演人数")

    def _install_snapshot(self, asset_id=1):
        """给一条作品配一份真的印相文件，让它的代表作头像变成取得到。"""
        snapshot = Path(self.tmp.name).resolve() / f"snapshot-{asset_id}.jpg"
        snapshot.write_bytes(b"x")
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE asset SET snapshot_path=? WHERE id=?", (str(snapshot), asset_id))
        con.commit()
        con.close()
        self.contract.cache_bust()

    def test_index_pages_say_up_front_whether_each_face_can_be_fetched(self):
        """索引页一屏几十个圆头像，同样得先问过再出图。

        `/performers` 桌面视口滚三屏实测 77 个取图请求里 5 个是 `/entity-image` 的
        404，而这条响应不带缓存头，每次重绘再打一整轮。
        """
        def flags(kind):
            return [(row["k"], row["has_image"], row["has_avatar"], row.get("avatar_stand_in", False))
                    for row in rm_web.q_index(self.contract, 'performers', limit=10)["items"]
                    if row['entity_kind'] == {'performers':'performer','creators':'creator'}[kind]]

        self.assertEqual(flags("performers"), [("Canonical Alice", False, False, False)])
        self.assertEqual(flags("creators"), [("Canonical Creator", False, False, False)])

        (self.avatars / f"{entity_image_key('performer', 11)}.img").write_bytes(b"x")
        self._install_snapshot()
        # kind 是落盘名的一部分：装的是女优那张图，创作者那格照样没有实体图可取，
        # 回落到同一条作品的代表作头像。账号没有本人身份依据，那张画面标成代表作画面。
        self.assertEqual(flags("performers"), [("Canonical Alice", True, True, False)])
        self.assertEqual(flags("creators"), [("Canonical Creator", False, True, True)])

        (self.avatars / f"{entity_image_key('creator', 12)}.img").write_bytes(b"x")
        self.contract.cache_bust()
        self.assertEqual(flags("creators"), [("Canonical Creator", True, True, True)])
        # 标签索引页没有脸，不该凭空多出两个标志。
        self.assertNotIn(
            "has_image", rm_web.q_index(self.contract, "tags", limit=10)["items"][0])

    def test_taste_rankings_say_up_front_whether_each_face_can_be_fetched(self):
        """口味榜的创作者和女优两排走同一条两级链，标志也得随榜下发。

        榜行的形状和别处的身份引用不一样（`entity_id`、`representative_asset_id`
        直接长在行上），但判据是同一对函数，不能因为形状不同就退回无条件出图。
        """
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE asset SET play_count=2,play_seconds=600,last_played=? WHERE id=1",
                    (1_700_000_200,))
        con.commit()
        con.close()
        # 浏览历史那一侧显式指向不存在的临时路径：默认值是本机真实的采集库，
        # 榜单会跟着本机装了什么摇。
        never = Path(self.tmp.name).resolve() / "no-taste-history"
        contract = rm_web.WebContract(
            Path(self.db_path), avatar_root=self.avatars, logo_root=self.logos,
            taste_history_store=never / "history.sqlite",
            taste_history_import_root=never / "imports",
            taste_history_manifest=never / "manifest.json")

        def flags():
            rankings = rm_web.q_taste(contract, {"window": "all"})["rankings"]
            return {key: [(row["name"], row["has_image"], row["has_avatar"])
                          for row in rankings[key]]
                    for key in ("creators", "performers", "peach_creators",
                                "peach_performers")}

        self.assertEqual(flags(), {
            "creators": [("Canonical Creator", False, False)],
            "performers": [("Canonical Alice", False, False)],
            "peach_creators": [("Canonical Creator", False, False)],
            "peach_performers": [("Canonical Alice", False, False)],
        })

        (self.avatars / f"{entity_image_key('performer', 11)}.img").write_bytes(b"x")
        self._install_snapshot()
        contract.cache_bust()
        # 同一批 dict 同时出现在总榜和分源榜里，两处必须给同一个答案。账号没有本人身份
        # 依据，代表作头像照出，另标代表作画面。
        self.assertEqual(flags(), {
            "creators": [("Canonical Creator", False, True)],
            "performers": [("Canonical Alice", True, True)],
            "peach_creators": [("Canonical Creator", False, True)],
            "peach_performers": [("Canonical Alice", True, True)],
        })
        rankings = rm_web.q_taste(contract, {"window": "all"})["rankings"]
        self.assertTrue(rankings["creators"][0]["avatar_stand_in"])
        self.assertNotIn("avatar_stand_in", rankings["performers"][0])
        # 标签榜不出脸，不该被顺手挂上两个用不到的标志。
        tags = rm_web.q_taste(contract, {"window": "all"})["rankings"]["tags"]
        self.assertNotIn("has_image", tags[0])

    def test_items_can_skip_repeated_total_count_on_later_pages(self):
        result = rm_web.q_items(
            self.contract, {"limit": "1", "offset": "0", "count": "0"},
        )
        self.assertIsNone(result["total"])
        self.assertIsNone(result["bytes"], "不算总数时也别去扫体积")
        self.assertEqual(len(result["items"]), 1)
        self.assertTrue(result["has_more"])

    def test_item_totals_carry_occupied_bytes_for_the_trash_card(self):
        """回收站卡片要说的是「清空能腾出多少」，体积跟条数同一次聚合出来。

        为它单独发一次请求只是把同一条 WHERE 再跑一遍；口径也必须跟着筛选走，
        不能变成整库体积。
        """
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE asset SET disposal='trash' WHERE id IN (2,3)")
        con.commit()
        con.close()
        trash = rm_web.q_items(self.contract, {"state": "trash", "limit": "10"})
        self.assertEqual(trash["total"], 2)
        self.assertEqual(trash["bytes"], 210, "回收站里是 200 和 10 两条")
        live = rm_web.q_items(self.contract, {"limit": "10"})
        self.assertEqual(live["total"], 1)
        self.assertEqual(live["bytes"], 100, "体积口径必须跟着同一条筛选走")

    def test_vanished_rows_stay_out_of_lists_search_stats_and_feed_landing(self):
        """标「已消失」的行文件不在盘上：列表、搜索、补全、统计与 Feed 落地都不认它。"""
        from peach import feeds

        def seen():
            stats = rm_web.q_stats(self.contract)
            groups = rm_web.q_suggest(self.contract, "two")["groups"]
            with self.contract.read_connection() as connection:
                landed = feeds.in_library(connection, "XYZ-002")
            return {
                "items": sorted(row["id"] for row in rm_web.q_items(self.contract, {"limit": "10"})["items"]),
                "search": [row["id"] for row in rm_web.q_items(self.contract, {"q": "two", "limit": "10"})["items"]],
                "suggest": [item["id"] for group in groups if group["kind"] == "asset" for item in group["items"]],
                "videos": stats["attribution"]["videos"],
                "by_loc": sorted(row["k"] for row in stats["by_loc"]),
                "facet_creators": [row["n"] for row in rm_web.q_facets(self.contract)["creators"]],
                "landed": landed,
            }

        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("UPDATE asset SET code='XYZ-002' WHERE id=2")
            connection.commit()
        before = seen()
        self.assertEqual((before["items"], before["search"], before["suggest"]), ([1, 2], [2], [2]))
        self.assertEqual((before["videos"], before["by_loc"], before["landed"]), (2, ["115", "local"], True))
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("UPDATE asset SET disposal='vanished' WHERE id=2")
            connection.commit()
        self.contract.cache_bust()
        after = seen()
        self.assertEqual((after["items"], after["search"], after["suggest"]), ([1], [], []))
        self.assertEqual((after["videos"], after["by_loc"], after["landed"]), (1, ["local"], False))
        self.assertEqual(sum(after["facet_creators"]), sum(before["facet_creators"]) - 1)

    def test_legacy_length_tags_are_hidden_in_favor_of_numeric_minutes(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(14,'tag','短片-2分内','短片-2分内','2026-01-01','2026-01-01')"
        )
        con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(1,14,'tag','test',1.0)"
        )
        con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(15,'tag','测试分页标签','测试分页标签','2026-01-01','2026-01-01')"
        )
        con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(1,15,'tag','test',1.0)"
        )
        con.commit(); con.close()
        self.assertNotIn("短片-2分内", [tag["k"] for tag in rm_web.q_item(self.contract, 1)["tags"]])
        facets = rm_web.q_facets(self.contract)
        visible = {row["k"] for row in facets["tags"] + facets["tech"]}
        self.assertNotIn("短片-2分内", visible)
        index = rm_web.q_index(self.contract, "tags")
        self.assertNotIn("短片-2分内", {row["k"] for row in index["items"]})
        self.assertIn("超长片-30分上", rm_web.LENGTH_TAGS)
        first_page = rm_web.q_index(self.contract, "tags", limit=1)
        second_page = rm_web.q_index(self.contract, "tags", limit=1, offset=1)
        self.assertTrue(first_page["has_more"])
        self.assertNotEqual(first_page["items"][0]["k"], second_page["items"][0]["k"])

    def test_every_technical_tag_uses_the_same_facets_classification(self):
        connection = sqlite3.connect(self.db_path)
        for entity_id, tag in enumerate(("2K", "有码", "无码"), start=201):
            connection.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
                "VALUES(?, 'tag', ?, ?,'2026-01-01','2026-01-01')",
                (entity_id, tag, tag.lower()),
            )
            connection.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
                "VALUES(1,?,'tag','test',1.0)",
                (entity_id,),
            )
        connection.commit()
        connection.close()

        facets = rm_web.q_facets(self.contract)
        tech = {row["k"] for row in facets["tech"]}
        content = {row["k"] for row in facets["tags"]}
        self.assertTrue({"2K", "有码", "无码"} <= tech)
        self.assertTrue({"2K", "有码", "无码"}.isdisjoint(content))

    def test_tag_index_uses_populated_hanime_style_categories(self):
        connection = sqlite3.connect(self.db_path)
        tagged = (
            (301, "中文字幕", "影片属性"),
            (302, "近亲", "人物关系"),
            (303, "护士", "角色设定"),
            (304, "巨乳", "外貌身材"),
            (305, "浴室", "情境场所"),
            (306, "出轨", "故事剧情"),
            (308, "原创内容", "其他内容"),
            (309, "原神", "其他内容"),
        )
        for entity_id, tag, _label in tagged:
            connection.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
                "VALUES(?, 'tag', ?, ?,'2026-01-01','2026-01-01')",
                (entity_id, tag, tag.lower()),
            )
            connection.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
                "VALUES(1,?,'tag','test',1.0)",
                (entity_id,),
            )
        connection.commit()
        connection.close()

        expected = {
            "中文字幕": "meta",
            "近亲": "relationship",
            "护士": "role",
            "巨乳": "appearance",
            "浴室": "scene",
            "出轨": "story",
            "足交": "position",
            "原创内容": "general",
            "原神": "general",
        }
        index = rm_web.q_index(self.contract, "tags")
        actual = {row["k"]: row["cat"] for row in index["items"]}
        self.assertEqual({tag: actual[tag] for tag in expected}, expected)
        self.assertNotIn("copyright", index["categories"])
        for category in expected.values():
            self.assertGreater(index["categories"][category], 0)
            filtered = rm_web.q_index(self.contract, "tags", category=category)
            self.assertTrue(filtered["items"])
            self.assertTrue(all(row["cat"] == category for row in filtered["items"]))

    def test_activity_accumulates_real_play_time_and_max_position(self):
        first = rm_web.w_activity(self.contract, {
            "id": 1, "position": 50, "duration": 100, "delta": 12, "seeks": 2,
        })
        second = rm_web.w_activity(self.contract, {
            "id": 1, "position": 20, "duration": 100, "delta": 3, "seeks": 1,
        })
        self.assertEqual(first["max_reached"], 0.5)
        self.assertEqual(second["play_seconds"], 15)
        self.assertEqual(second["max_reached"], 0.5)
        self.assertEqual(second["seek_count"], 3)

    def test_feedback_and_disposal_toggle_independently(self):
        self.assertEqual(rm_web.w_feedback(
            self.contract, {"id": 1, "kind": "dislike"},
        )["feedback"], "dislike")
        self.assertEqual(rm_web.w_feedback(
            self.contract, {"id": 1, "kind": "dispose"},
        )["disposal"], "trash")
        self.assertEqual(self.row()["feedback"], "dislike")
        self.assertIsNone(rm_web.w_feedback(
            self.contract, {"id": 1, "kind": "dislike"},
        )["feedback"])
        self.assertEqual(self.row()["disposal"], "trash")

    def test_a_vanished_asset_refuses_the_recycle_bin_toggle_and_stays_vanished(self):
        """已消失的行切进回收站再切出来就成了在库：单条切换与批量移入回收站都拒绝。"""
        with self.contract.write_transaction() as connection:
            connection.execute("UPDATE asset SET disposal='vanished' WHERE id=1")
        with self.assertRaisesRegex(ValueError, "vanished"):
            rm_web.w_feedback(self.contract, {"id": 1, "kind": "dispose"})
        self.assertEqual(self.row()["disposal"], "vanished")
        with self.assertRaisesRegex(ValueError, "vanished"):
            rm_web.w_batch(self.contract, {"ids": [1, 2], "operation": "dispose"})
        self.assertEqual((self.row()["disposal"], self.row(2)["disposal"]), ("vanished", None))

    def test_a_star_writes_twenty_points_and_taking_it_back_writes_null(self):
        """评分这一列是 0–100，五颗星走 20 的倍数；撤销回 NULL，不是 0。

        量纲不是自选的：这一列是 Stash 的 rating100 原样导进来的，
        `taste_history` 也按 rating/20 折算成 0–5 分。撤销必须写 NULL，因为排序是
        `a.rating {d} NULLS LAST`——写 0 会把「没评过」和「评了一星」并成一档，
        七万多条没评过的于是排在评了一星的前面。
        """
        self.assertEqual(rm_web.w_feedback(
            self.contract, {"id": 1, "kind": "rate", "value": 60},
        )["rating"], 60)
        self.assertEqual(self.row()["rating"], 60)
        self.assertIsNone(rm_web.w_feedback(
            self.contract, {"id": 1, "kind": "rate", "value": 0},
        )["rating"])
        self.assertIsNone(self.row()["rating"])

    def test_a_rating_outside_the_scale_is_clamped_instead_of_stored(self):
        """越界的分数夹回 0–100，不落库。前端只送 20 的倍数，接口本身仍是公开的。"""
        self.assertEqual(rm_web.w_feedback(
            self.contract, {"id": 1, "kind": "rate", "value": 4000},
        )["rating"], 100)
        self.assertIsNone(rm_web.w_feedback(
            self.contract, {"id": 1, "kind": "rate", "value": -7},
        )["rating"])

    def test_the_rating_sort_finally_orders_by_something_other_than_o_count(self):
        """评过分的排在前面，没评过的按 NULLS LAST 留在后面。

        补上写入路径之前，这一列全库为空，`rating` 排序静默退化成第二列
        `a.o_count`，界面上「评分」和「高潮计数」是同一个排序。
        """
        rm_web.w_feedback(self.contract, {"id": 2, "kind": "rate", "value": 100})
        rm_web.w_feedback(self.contract, {"id": 1, "kind": "rate", "value": 40})
        ordered = [item["id"] for item in rm_web.q_items(
            self.contract, {"sort": "rating", "limit": "10"})["items"]]
        self.assertEqual(ordered[:2], [2, 1])

    def test_orgasm_receipt_can_be_undone_without_crossing_zero(self):
        added = rm_web.w_feedback(self.contract, {"id": 1, "kind": "o"})
        self.assertEqual(added["o_count"], 1)
        undone = rm_web.w_feedback(self.contract, {"id": 1, "kind": "o-undo"})
        self.assertEqual(undone["o_count"], 0)
        self.assertEqual(
            rm_web.w_feedback(self.contract, {"id": 1, "kind": "o-undo"})["o_count"],
            0,
        )

    def test_entity_collection_states_keep_identity_and_clear_independently(self):
        def ids(state):
            return {item['id'] for item in rm_web.q_items(self.contract,
                {'creator': 'Canonical Creator', 'state': state, 'limit': '10'})['items']}

        self.assertEqual(ids(''), {1, 2})
        self.assertEqual(ids('fresh'), {1, 2})
        rm_web.w_watch_later(self.contract, {'id': 2})
        rm_web.w_preference(self.contract, {'id': 1, 'liked': True, 'reason': ''})
        self.assertEqual(ids('later'), {2})
        self.assertEqual(ids('flagged'), {1})
        self.assertEqual(ids(''), {1, 2})
        rm_web.w_watch_later(self.contract, {'id': 2})
        self.assertEqual(ids('later'), set())
        self.assertEqual(ids('flagged'), {1})

    def test_flagged_means_positive_marks_not_disposal_or_negative_feedback(self):
        rm_web.w_feedback(self.contract, {"id": 1, "kind": "dislike"})
        rm_web.w_feedback(self.contract, {"id": 1, "kind": "dispose"})
        rm_web.w_preference(self.contract, {"id": 2, "liked": True, "reason": ""})
        self.assertEqual(
            [item["id"] for item in rm_web.q_items(self.contract, {"state": "flagged", "limit": "10"})["items"]],
            [2],
        )
        self.assertEqual(rm_web.q_facets(self.contract)["stats"]["flagged"], 1)

        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE asset SET o_count=2 WHERE id=1")
        con.commit(); con.close()
        flagged = lambda: {item["id"] for item in rm_web.q_items(
            self.contract, {"state": "flagged", "limit": "10"})["items"]}
        # o_count 让 1 变成正向标记，但它还在回收站里，所以不该出现在任何普通列表。
        self.assertEqual(flagged(), {2})
        self.assertEqual(rm_web.q_facets(self.contract)["stats"]["flagged"], 1)

        # 还原后立刻重新计入：证明上面漏掉 1 的原因是回收站，而不是“已标记”的定义变了。
        rm_web.w_batch(self.contract, {"ids": [1], "operation": "restore"})
        self.assertEqual(flagged(), {1, 2})
        self.assertEqual(rm_web.q_facets(self.contract)["stats"]["flagged"], 2)

    def test_like_and_reason_are_independent_profile_preferences(self):
        saved = rm_web.w_preference(self.contract, {
            "id": 1, "liked": True, "reason": "喜欢自然的节奏和镜头",
        })
        self.assertTrue(saved["liked"])
        self.assertEqual(saved["like_reason"], "喜欢自然的节奏和镜头")
        item = rm_web.q_item(self.contract, 1)
        self.assertTrue(item["liked"])
        self.assertEqual(item["like_reason"], "喜欢自然的节奏和镜头")
        self.assertEqual(item["entity_refs"]["performer"], [
            {"id": 11, "name": "Canonical Alice", "has_image": False},
        ])
        self.assertIsNone(self.row()["feedback"])

        cleared = rm_web.w_preference(self.contract, {
            "id": 1, "liked": False, "reason": "",
        })
        self.assertFalse(cleared["liked"])
        self.assertEqual(cleared["like_reason"], "")

    def test_application_contract_instances_do_not_share_cache(self):
        other = rm_web.WebContract(Path(self.tmp.name) / "other.db")
        self.assertEqual(self.contract.cached("same", lambda: "first"), "first")
        self.assertEqual(other.cached("same", lambda: "second"), "second")

    def test_a_bust_during_a_slow_computation_discards_the_stale_result(self):
        """算到一半时缓存失效，算完不能把失效前的快照写回去。

        复核页就是这个场景：`q_review` 要读候选 CSV 又要查库，用户在这期间批准了
        一条候选，`w_review_decision` 调 `cache_bust`。写回一旦发生，用户批准完刷新
        看到的还是批准前的列表，而且要等满一个 TTL 才会消失。
        """
        entered, may_finish = threading.Event(), threading.Event()

        def slow():
            entered.set()
            self.assertTrue(may_finish.wait(5), "测试自身超时")
            return "批准前的快照"

        result = []
        worker = threading.Thread(
            target=lambda: result.append(self.contract.cached("review", slow)),
            daemon=True,
        )
        worker.start()
        self.assertTrue(entered.wait(5), "慢计算没有开始")
        self.contract.cache_bust()
        may_finish.set()
        worker.join(5)

        self.assertEqual(result, ["批准前的快照"], "本次调用仍应拿到自己算出的值")
        self.assertEqual(
            self.contract.cached("review", lambda: "批准后的列表"), "批准后的列表",
            "失效期间算出的值不得写回缓存",
        )

    def test_a_bust_before_the_computation_starts_still_caches_normally(self):
        """代次机制不能把正常的缓存也挡掉——失效发生在计算开始之前就不算竞态。"""
        self.contract.cache_bust()
        self.assertEqual(self.contract.cached("k", lambda: "算一次"), "算一次")
        self.assertEqual(
            self.contract.cached("k", lambda: "不该被调用"), "算一次",
            "第二次必须命中缓存",
        )

    def test_contract_handler_registries_are_complete_and_unknown_routes_fail(self):
        self.assertEqual(set(rm_web.GET_HANDLERS), {
            "/api/tasks",
            "/api/library-processing",
            "/api/library-processing/issues",
            "/api/media-processing",
            "/api/thumbnail-jobs", "/api/timeline", "/api/media-repair",
            "/api/scraping", "/api/scraping/cover", "/api/scraping/amane-bridge",
            "/api/items", "/api/item", "/api/entity", "/api/entity/shapes", "/api/photos", "/api/photo-set",
            "/api/index", "/api/parts", "/api/editions", "/api/duplicates", "/api/quality-goals",
            "/api/stats", "/api/tops", "/api/ads", "/api/related", "/api/facets",
            "/api/suggest",
            "/api/search-history", "/api/review", "/api/playlists", "/api/playlist",
            "/api/post-setup-tutorial",
            "/api/library-shortcuts", "/api/folders",
            "/api/custom-metadata",
            "/api/follow", "/api/follow/credentials", "/api/follow/schedule",
            "/api/follow/check", "/api/follow/resolve", "/api/follow/suggest",
            "/api/taste/refresh",
            "/api/links/prune", "/api/resource-sync/apply",
            "/api/follow/tags", "/api/follow/authors",
            "/api/taste", "/api/settings", "/api/links", "/api/organize", "/api/orphan-records",
            "/api/feeds", "/api/feeds/check", "/api/feeds/discoveries", "/api/feeds/lookup",
            "/api/downloads", "/api/resources/search",
            "/api/wants", "/api/wants/magnets",
            "/api/jav/detail", "/api/jav/reviews", "/api/jav/image", "/api/jav/resources", "/api/jav/discoveries",
        })
        self.assertEqual(set(rm_web.POST_HANDLERS), {
            "/api/downloads", "/api/downloads/cancel", "/api/downloads/postprocess",
            "/api/library-processing", "/api/thumbnail-jobs", "/api/media-repair",
            "/api/library-processing/import-file",
            "/api/media-processing",
            "/api/scraping/settings", "/api/scraping/check", "/api/scraping/cover",
            "/api/scraping/amane-bridge/check", "/api/scraping/amane-bridge/rebuild",
            "/api/activity", "/api/play", "/api/feedback", "/api/watch-later",
            "/api/playlist",
            "/api/library-shortcuts", "/api/folders/preview", "/api/folders/rule",
            "/api/custom-metadata",
            "/api/preference", "/api/quality-goal", "/api/item-tag", "/api/batch",
            "/api/search-history", "/api/trash/empty",
            "/api/review/decision", "/api/review/genre",
            "/api/purge-missing", "/api/orphan-records/attach",
            "/api/links/check", "/api/links/prune",
            "/api/resource-sync/scan", "/api/resource-sync/apply",
            "/api/follow/check", "/api/follow/status", "/api/follow/media/hide",
            "/api/follow/image-dims", "/api/follow/save",
            "/api/follow/play", "/api/follow/activity",
            "/api/follow/source", "/api/follow/resolve", "/api/follow/credential",
            "/api/follow/author-alias", "/api/follow/creator", "/api/follow/schedule",
            "/api/taste/refresh", "/api/taste/source", "/api/settings",
            "/api/entity-name", "/api/entity-alias",
            "/api/organize/preview", "/api/organize/apply", "/api/organize/rollback",
            "/api/feeds/check", "/api/feeds/source", "/api/feeds/discovery",
            "/api/wants",
        })
        with self.assertRaises(rm_web.ContractRouteNotFound):
            rm_web.dispatch_api_get(self.contract, "/api/typo", {})

    def test_read_only_post_routes_are_declared_and_all_exist(self):
        # 只读 POST 要绕开写入端闸门；名字写错就会静默失去豁免。
        self.assertTrue(rm_web.READ_ONLY_POST_ROUTES)
        self.assertTrue(rm_web.READ_ONLY_POST_ROUTES <= set(rm_web.POST_HANDLERS))

    def test_write_transaction_rolls_back_and_closes_on_failure(self):
        opened = []
        real_connect = self.contract.database.connect

        def capture(*, write=False):
            connection = real_connect(write=write)
            opened.append(connection)
            return connection

        with mock.patch.object(self.contract.database, "connect", side_effect=capture):
            with self.assertRaisesRegex(RuntimeError, "abort"):
                with self.contract.write_transaction() as connection:
                    connection.execute("UPDATE asset SET name='changed' WHERE id=1")
                    raise RuntimeError("abort")

        self.assertEqual(self.row(1)["name"], "one.mp4")
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")

    def test_item_tag_add_and_remove_preserve_source_evidence(self):
        added = rm_web.w_item_tag(self.contract, {
            "id": 1, "operation": "add", "tag": "新标签",
        })
        self.assertIn("新标签", added["tags"])
        removed = rm_web.w_item_tag(self.contract, {
            "id": 1, "operation": "remove", "tag": "足交",
        })
        self.assertNotIn("足交", removed["tags"])
        con = sqlite3.connect(self.db_path)
        self.assertEqual(
            con.execute("SELECT source FROM asset_tag WHERE asset_id=1 AND tag='足交'").fetchone()[0],
            "name",
        )
        self.assertEqual(
            con.execute("SELECT hidden FROM asset_tag_preference WHERE asset_id=1").fetchone()[0],
            1,
        )
        con.close()

    def test_item_tag_alias_uses_the_canonical_name_and_can_be_hidden(self):
        first = rm_web.w_item_tag(self.contract, {
            "id": 1, "operation": "add", "tag": "秘书OL",
        })
        repeated = rm_web.w_item_tag(self.contract, {
            "id": 1, "operation": "add", "tag": "OL",
        })
        self.assertEqual(repeated["tag"], "秘书OL")
        self.assertEqual(repeated["tags"], first["tags"])
        with closing(sqlite3.connect(self.db_path)) as con:
            self.assertEqual(con.execute(
                "SELECT tag FROM asset_tag WHERE asset_id=1 AND tag IN ('OL','秘书OL')"
            ).fetchall(), [("秘书OL",)])
        removed = rm_web.w_item_tag(self.contract, {
            "id": 1, "operation": "remove", "tag": repeated["tag"],
        })
        self.assertNotIn("秘书OL", removed["tags"])

    def _seed_editions(self, rows):
        con = sqlite3.connect(self.db_path)
        for asset_id, name, code in rows:
            con.execute(
                "INSERT INTO asset(id,location,path,name,medium,code,size) "
                "VALUES(?,'local',?,?,'video',?,1)",
                (asset_id, f"/x/{name}", name, code),
            )
        con.commit(); con.close()

    def test_a_censored_and_an_uncensored_release_share_one_card(self):
        """同一番号的有码与无码是两个版次，不该占两个格子。

        多个版次可能使用 `-C`、`CH`、`.Uncen` 等文件名后缀。

        版次判据只用 `jav_display_metadata`，和卡片上已经显示的徽章同一份——自己按
        文件名再写一套「像不像无码」，会出现角标写着无码、分组却当它是正片。
        """
        # 版次来自标签而不是文件名：实测 `-UN`／`-C`／`.Uncen` 这些后缀单独都认不出来，
        # 真实那条 ABF-234-UN 能认出是因为它打了 `无码` 标签。所以这里也必须造标签。
        self._seed_editions([(9001, "ABF-234.mp4", "ABF-234"),
                             (9002, "ABF-234-UN.mp4", "ABF-234")])
        con = sqlite3.connect(self.db_path)
        con.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
                    "VALUES(9100,'tag','无码','无码','2026-01-01','2026-01-01')")
        con.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
                    "VALUES(9002,9100,'tag','test',1.0)")
        con.commit(); con.close()
        groups = rm_web._edition_groups(self.contract, ["ABF-234"])
        self.assertEqual(list(groups), ["ABF-234"])
        self.assertEqual([item["edition"] for item in groups["ABF-234"]], ["有码", "无码"])
        # 正片在前只是要一个稳定的锚点：次序一变，卡片标题就跟着换。
        self.assertEqual(groups["ABF-234"][0]["id"], 9001)

    def test_two_copies_of_the_same_edition_are_not_versions(self):
        """同番号多文件不等于多版本。

        实测 158 个同番号多文件的番号里，84 个是分卷，还有一批是同名重复
        （`ABP-442.avi` 出现两次、`.MP4` 与 `.mp4` 各一份）。把它们当版本，等于把
        「该去重复文件页处理的东西」伪装成「可选的版本」。
        """
        self._seed_editions([(9011, "ABP-442.avi", "ABP-442"),
                             (9012, "ABP-442.mp4", "ABP-442")])
        self.assertEqual(rm_web._edition_groups(self.contract, ["ABP-442"]), {})

    def test_a_multipart_release_is_left_to_the_multipart_grouping(self):
        self._seed_editions([(9021, "SSIS-100-cd1.mp4", "SSIS-100"),
                             (9022, "SSIS-100-cd2.mp4", "SSIS-100")])
        self.assertEqual(rm_web._edition_groups(self.contract, ["SSIS-100"]), {})

    def test_the_editions_endpoint_is_registered(self):
        self.assertIn("/api/editions", rm_web.GET_HANDLERS)

    def test_query_side_normalisation_matches_the_write_side(self):
        """查询侧和写入侧必须用同一份归一化。

        `asset_tag_preference.normalized_tag` 写入时走 Python 的 `strip().casefold()`，
        而查询侧一度用 SQLite 的 `lower(trim())`。SQLite 的 lower() 只认 ASCII：西里尔
        字母和罗马数字 Ⅱ 原样放过，两边算出的键就对不上，`NOT EXISTS` 永远成立——
        用户点了「隐藏」而标签照常显示，没有任何报错。按今天的数据它们都落在
        走实体名比对的那条路径上，所以线上没有真的出错；这条测试守的是那个前提别再变。
        """
        cyrillic = "УбийцаАкаме"
        roman = "レキシントンⅡ"
        with self.contract.database.read_connection() as c:
            for tag in (cyrillic, roman):
                with self.subTest(tag=tag):
                    stored = rm_web.normalize_entity_name(tag)
                    self.assertEqual(
                        c.execute("SELECT peach_normalize(?)", (tag,)).fetchone()[0], stored,
                        "SQL 侧的 peach_normalize 必须和写入侧的 normalize_entity_name 一致")
                    self.assertNotEqual(
                        c.execute("SELECT lower(trim(?))", (tag,)).fetchone()[0], stored,
                        "这两个例子的意义就在于 SQLite 的 lower() 算不出同一个键；"
                        "若它们哪天一致了，请换成仍会分歧的例子，别让这条测试变成空转")

    def test_the_hidden_tag_rule_has_one_implementation(self):
        """「标签没被隐藏」的判据只许有一份。

        它此前在五处各写了一份裸 SQL。join 列因查询语境不同是正常的，规则本体不是：
        漏抄一处，被隐藏的标签就会从那个表面漏回来，而这属于语义契约。

        计数扫的是整个 web 层而不是单个文件：实现现在住在 `web_catalog`，再抄一份最可能
        抄到隔壁的域模块里去，只盯一个文件的话那种抄写正好检不出来。
        """
        web_layer = sorted(pathlib.Path(rm_web.__file__).parent.glob("web_*.py"))
        self.assertGreaterEqual(len(web_layer), 6, "没找到 web 层模块，glob 口径变了")
        source = "\n".join(path.read_text(encoding="utf-8") for path in web_layer)
        self.assertEqual(source.count("FROM asset_tag_preference p "), 1,
                         "隐藏判据又被抄了一份，请改调 tag_not_hidden()")
        self.assertEqual(source.count("WHERE performer.kind='performer' "), 1,
                         "「标签其实是女优名」的判据又被抄了一份，"
                         "请改调 tag_is_not_a_performer_name()")

    def test_item_tag_rolls_back_and_closes_when_the_entity_write_fails(self):
        """加标签是一个事务：实体写入失败时回滚 asset_tag，并在所有分支关闭连接。"""
        opened = []
        real_connect = self.contract.database.connect

        def capture(*, write=False):
            connection = real_connect(write=write)
            opened.append(connection)
            return connection

        with mock.patch.object(self.contract.database, "connect", side_effect=capture):
            with mock.patch.object(web_catalog, "upsert_asset_entity",
                                   side_effect=RuntimeError("entity write failed")):
                with self.assertRaisesRegex(RuntimeError, "entity write failed"):
                    rm_web.w_item_tag(self.contract, {
                        "id": 1, "operation": "add", "tag": "回滚标签",
                    })

        con = sqlite3.connect(self.db_path)
        self.assertIsNone(
            con.execute(
                "SELECT 1 FROM asset_tag WHERE asset_id=1 AND tag='回滚标签'").fetchone(),
            "事务中途失败后不能把标签行留在数据库里",
        )
        con.close()
        self.assertTrue(opened, "没有取到写连接，测试本身失效")
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[-1].execute("SELECT 1")

    def test_item_tag_releases_the_write_lock_between_calls(self):
        """写事务只能取一次 database.write_lock：那是把不可重入锁。

        谁把外层的手动取锁加回来（或在 write_transaction 里再取一次），第二次写入
        就会永远等自己已经持有的锁。这个断言就是那个陷阱的守卫。"""
        rm_web.w_item_tag(self.contract, {"id": 1, "operation": "add", "tag": "第一次"})
        finished = threading.Event()

        def second_write():
            rm_web.w_item_tag(self.contract, {"id": 1, "operation": "add", "tag": "第二次"})
            finished.set()

        worker = threading.Thread(target=second_write, daemon=True)
        worker.start()
        worker.join(timeout=10)
        self.assertTrue(finished.is_set(), "第二次写入没能在 10 秒内拿到写锁，写锁没被释放")

    def stage_media(self, aid, name="clip.mp4"):
        """把某条资产指向真实的临时文件，物理删除才有东西可删。"""
        path = Path(self.tmp.name) / name
        path.write_bytes(b"media")
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE asset SET path=?,snapshot_path=NULL WHERE id=?", (str(path), aid))
        con.commit(); con.close()
        return path

    def test_recycle_bin_delete_removes_the_media_and_every_ledger_reference(self):
        path = self.stage_media(1)
        playlist = rm_web.w_playlist(self.contract, {
            "action": "create", "name": "待清理", "asset_ids": [1],
            "source_kind": "mix", "source_seed_asset_id": 1,
        })["playlist"]
        rm_web.w_feedback(self.contract, {"id": 1, "kind": "dispose"})
        result = rm_web.w_batch(self.contract, {"ids": [1], "operation": "delete"})

        self.assertEqual((result["purged"], result["blocked"]), (1, []))
        self.assertFalse(path.exists(), "回收站的彻底删除必须真的删掉文件")
        self.assertIsNone(self.row(1), "数据库行也要一起消失")
        con = sqlite3.connect(self.db_path)
        for table in rm_web.ASSET_REFERENCE_TABLES:
            left = con.execute(f"SELECT count(*) FROM {table} WHERE asset_id=1").fetchone()[0]
            self.assertEqual(left, 0, f"{table} 残留了已删资产的引用")
        self.assertIsNone(con.execute(
            "SELECT current_asset_id FROM playlist WHERE id=?", (playlist["id"],),
        ).fetchone()[0])
        self.assertIsNone(con.execute(
            "SELECT source_seed_asset_id FROM playlist WHERE id=?", (playlist["id"],),
        ).fetchone()[0])
        con.close()

    def test_permanent_delete_removes_newly_empty_parents_but_keeps_the_source_root(self):
        source_root = Path(self.tmp.name) / "source"
        media = source_root / "creator" / "release" / "junk.url"
        media.parent.mkdir(parents=True)
        media.write_text("junk", encoding="utf-8")
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE asset SET path=?,snapshot_path=NULL WHERE id=1", (str(media),))
        con.commit(); con.close()
        rm_web.w_feedback(self.contract, {"id": 1, "kind": "dispose"})

        with mock.patch.object(
                web_batch, "LOCATION_ROOT_DECLARATIONS", {"local": (str(source_root),)}):
            result = rm_web.w_batch(self.contract, {"ids": [1], "operation": "delete"})

        self.assertEqual(result["empty_dirs_removed"], 2)
        self.assertTrue(source_root.is_dir(), "声明的来源根绝不能随空目录一起删除")
        self.assertFalse((source_root / "creator").exists())

    def test_failed_database_commit_restores_quarantined_media(self):
        path = self.stage_media(1, "commit-failure.mp4")
        rm_web.w_feedback(self.contract, {"id": 1, "kind": "dispose"})

        @contextmanager
        def fail_after_body():
            connection = self.contract.database.connect(write=True)
            try:
                yield connection
                connection.rollback()
                raise sqlite3.OperationalError("simulated commit failure")
            finally:
                connection.close()

        with mock.patch.object(self.contract, "write_transaction", fail_after_body):
            with self.assertRaisesRegex(sqlite3.OperationalError, "commit failure"):
                rm_web.w_batch(self.contract, {"ids": [1], "operation": "delete"})

        self.assertTrue(path.is_file(), "数据库失败后媒体必须恢复原名")
        self.assertEqual(self.row(1)["disposal"], "trash")
        self.assertEqual(list(path.parent.glob(".*.peach-purge-*.tmp")), [])

    def test_delete_and_restore_refuse_assets_outside_the_recycle_bin(self):
        """彻底删除只能作用于回收站，否则一次误选就能删掉在库作品。"""
        for operation in ("delete", "restore"):
            with self.assertRaises(ValueError):
                rm_web.w_batch(self.contract, {"ids": [1], "operation": operation})
        self.assertIsNotNone(self.row(1))

    def test_restore_returns_an_asset_to_normal_listings(self):
        rm_web.w_feedback(self.contract, {"id": 1, "kind": "dispose"})
        listed = {item["id"] for item in rm_web.q_items(self.contract, {"limit": "10"})["items"]}
        self.assertNotIn(1, listed, "回收站条目不该出现在普通列表")

        rm_web.w_batch(self.contract, {"ids": [1], "operation": "restore"})
        self.assertIsNone(self.row(1)["disposal"])
        listed = {item["id"] for item in rm_web.q_items(self.contract, {"limit": "10"})["items"]}
        self.assertIn(1, listed)

    def test_empty_trash_purges_only_the_bin(self):
        binned = self.stage_media(1, "binned.mp4")
        kept = self.stage_media(2, "kept.mp4")
        rm_web.w_feedback(self.contract, {"id": 1, "kind": "dispose"})

        result = rm_web.w_empty_trash(self.contract)

        self.assertEqual(result["purged"], 1)
        self.assertFalse(binned.exists())
        self.assertIsNone(self.row(1))
        self.assertTrue(kept.exists(), "不在回收站的媒体一个都不能碰")
        self.assertIsNotNone(self.row(2))

    def test_undeletable_media_keeps_its_row_in_the_bin_instead_of_orphaning_the_file(self):
        """文件删不掉时保留账本行：留一条能重试的回收站条目，好过留一个没人认领的文件。"""
        blocked_path = Path(self.tmp.name) / "locked"
        blocked_path.mkdir()          # 目录删不掉，等价于文件被占用/网盘离线
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE asset SET path=? WHERE id=1", (str(blocked_path),))
        con.commit(); con.close()
        rm_web.w_feedback(self.contract, {"id": 1, "kind": "dispose"})

        result = rm_web.w_empty_trash(self.contract)

        self.assertEqual(result["purged"], 0)
        self.assertEqual([item["id"] for item in result["blocked"]], [1])
        self.assertTrue(blocked_path.exists())
        self.assertEqual(self.row(1)["disposal"], "trash", "删不掉就该留在回收站里等重试")

    def test_batch_markers_are_bounded_and_preserve_like_reason(self):
        rm_web.w_preference(self.contract, {"id": 1, "liked": False, "reason": "保留原文"})
        result = rm_web.w_batch(self.contract, {"ids": [1, 2, 2], "operation": "like"})
        self.assertEqual(result["changed"], 2)
        self.assertEqual(rm_web.q_item(self.contract, 1)["like_reason"], "保留原文")

    def test_better_version_goal_is_independent_and_reversible(self):
        saved = rm_web.w_quality_goal(self.contract, {
            "id": 1, "wanted": True, "reason": "有水印，寻找高清无水印版",
        })
        self.assertTrue(saved["better_version"])
        item = rm_web.q_item(self.contract, 1)
        self.assertTrue(item["better_version"])
        self.assertEqual(item["better_version_reason"], "有水印，寻找高清无水印版")
        cleared = rm_web.w_quality_goal(self.contract, {"id": 1, "wanted": False})
        self.assertFalse(cleared["better_version"])
        self.assertFalse(rm_web.q_item(self.contract, 1)["better_version"])

    def test_better_version_targets_have_a_management_read_surface(self):
        rm_web.w_quality_goal(self.contract, {"id": 1, "wanted": True})
        queue = rm_web.q_quality_goals(self.contract, {"limit": "20"})
        self.assertEqual(queue["total"], 1)
        self.assertEqual(queue["items"][0]["id"], 1)
        self.assertNotIn("path", queue["items"][0])
        self.assertFalse(queue["has_more"])
        rm_web.w_batch(self.contract, {"ids": [1, 2], "operation": "dispose"})
        self.assertEqual(self.row(1)["disposal"], "trash")
        with self.assertRaises(ValueError):
            rm_web.w_batch(self.contract, {"ids": list(range(201)), "operation": "seen"})

    def test_top_lists_and_related_items_use_canonical_entities(self):
        tops = rm_web.q_tops(self.contract, 10)
        self.assertEqual(tops["performers"][0]["k"], "Canonical Alice")
        self.assertEqual(tops["performers"][0]["n"], 1)
        self.assertEqual(tops["studios"][0]["k"], "Canonical Studio")

        related = rm_web.q_related(self.contract, 1, 10)
        self.assertEqual(related["items"][0]["id"], 2)
        self.assertEqual(related["items"][0]["why"], "同创作者 · 同厂牌")
        self.assertEqual(related["items"][0]["performer_entities"], [])

        reverse_related = rm_web.q_related(self.contract, 2, 10)
        self.assertEqual(reverse_related["items"][0]["performer_entities"], [
            {"id": 11, "name": "Canonical Alice", "has_image": False},
        ])

    def _add_performers(self, asset_id, names, start_id=200):
        con = sqlite3.connect(self.db_path)
        for offset, name in enumerate(names):
            entity_id = start_id + offset
            con.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
                "VALUES(?,'performer',?,?,'2026-01-01','2026-01-01')", (entity_id, name, name.casefold()))
            con.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
                "VALUES(?,?,'performer','test',1.0)", (asset_id, entity_id))
        con.commit()
        con.close()

    def test_co_starred_work_carries_every_performer_not_just_the_first(self):
        self._add_performers(1, ["Canonical Bea", "Canonical Cleo"])
        item = rm_web.q_items(self.contract, {"limit": "10"})["items"]
        row = next(row for row in item if row["id"] == 1)
        self.assertEqual(row["performers"],
                         ["Canonical Alice", "Canonical Bea", "Canonical Cleo"])
        self.assertEqual([ref["name"] for ref in row["performer_entities"]],
                         ["Canonical Alice", "Canonical Bea", "Canonical Cleo"])
        self.assertEqual(row["performer_total"], 3)
        # 出镜者名不得再作为内容标签重复出现在同一张卡片上。
        self.assertNotIn("Canonical Bea", row["tags"])

    def test_card_performers_are_capped_but_the_total_is_still_reported(self):
        extra = [f"Cast {index:02d}" for index in range(rm_web.CARD_PERFORMERS + 3)]
        self._add_performers(2, extra)
        row = next(row for row in rm_web.q_items(self.contract, {"limit": "10"})["items"]
                   if row["id"] == 2)
        self.assertEqual(len(row["performer_entities"]), rm_web.CARD_PERFORMERS)
        self.assertEqual(row["performer_total"], len(extra))

    def test_detail_returns_the_full_cast_without_the_card_cap(self):
        extra = [f"Cast {index:02d}" for index in range(rm_web.CARD_PERFORMERS + 3)]
        self._add_performers(1, extra)
        detail = rm_web.q_item(self.contract, 1)
        self.assertEqual(len(detail["entity_refs"]["performer"]), len(extra) + 1)
        self.assertEqual(len(detail["performers"]), len(extra) + 1)

    def test_related_cards_report_the_same_performer_shape_as_the_home_grid(self):
        self._add_performers(2, ["Canonical Bea"])
        related = rm_web.q_related(self.contract, 1, 10)
        row = related["items"][0]
        self.assertEqual(row["performer_total"], len(row["performer_entities"]))

    def test_related_shows_a_multipart_release_once_and_never_the_source_s_own_parts(self):
        con = sqlite3.connect(self.db_path)
        con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,size,code,duration,width,height,first_seen) "
            "VALUES(?,'local',?,?,'video',100,'FC2-PPV-2543627',3600,1920,1080,'2026-09-01')",
            [(6, r"R:\Media\FC2-2543627-1.mp4", "FC2-2543627-1.mp4"),
             (7, r"R:\Media\FC2-2543627-2.mp4", "FC2-2543627-2.mp4")])
        con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(?,11,'performer','test',1.0)", [(6,), (7,)])
        con.commit()
        con.close()

        shown = [row for row in rm_web.q_related(self.contract, 1, 10)["items"] if row["id"] in (6, 7)]
        self.assertEqual([(row["id"], row["part_group"]["count"]) for row in shown], [(6, 2)])
        self.assertNotIn(6, [row["id"] for row in rm_web.q_related(self.contract, 7, 10)["items"]])

    def _add_unowned_and_performer_only_assets(self):
        con = sqlite3.connect(self.db_path)
        con.executemany(
            """INSERT INTO asset(
                 id,location,path,name,medium,size,creator,studio,code,duration,
                 width,height,ctx_length,ctx_orient,ctx_quality,first_seen)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (4, "local", r"R:\Media\four.mp4", "four.mp4", "video", 400,
                 None, "ラグジュTV", "259LUXU-1509", 400, 1280, 720, "速食", "横屏", "720P", "2026-08-11"),
                (5, "local", r"R:\Media\five.mp4", "five.mp4", "video", 500,
                 None, None, None, 500, 1920, 1080, "速食", "横屏", "2K", "2026-08-10"),
            ],
        )
        con.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
                    "VALUES(5,11,'performer','test',1.0)")
        con.commit()
        con.close()

    def test_unowned_is_a_collection_not_a_line_of_text_on_the_detail_panel(self):
        """卡片上写着「未归属」的每一条，点进这个集合都必须在里面。

        判据和 `cardIdentity` 同源：没有出镜者、没有创作者。4 号有厂牌也有番号，
        却一个人都没署名，它正是这一类；5 号有出镜者，不是。
        """
        self._add_unowned_and_performer_only_assets()
        listed = rm_web.q_items(self.contract, {"owner": "none", "limit": "10"})
        self.assertEqual([item["id"] for item in listed["items"]], [4])
        self.assertEqual(listed["total"], 1)

    def test_a_studio_is_not_ownership(self):
        """「ラグジュTV 出品」不回答「这是谁」。把有厂牌的排除掉，会正好漏掉最该补人的那批。"""
        self._add_unowned_and_performer_only_assets()
        row = next(item for item in rm_web.q_items(
            self.contract, {"owner": "none", "limit": "10"})["items"] if item["id"] == 4)
        self.assertEqual(row["studio"], "ラグジュTV")

    def test_an_unknown_owner_value_does_not_filter_anything(self):
        """筛选只认 `none` 这一个取值，别的写法不能悄悄变成一条空条件。"""
        self._add_unowned_and_performer_only_assets()
        everything = rm_web.q_items(self.contract, {"limit": "10"})["total"]
        self.assertEqual(rm_web.q_items(
            self.contract, {"owner": "", "limit": "10"})["total"], everything)
        self.assertEqual(rm_web.q_items(
            self.contract, {"owner": "someone", "limit": "10"})["total"], everything)

    def test_creator_filters_indexes_and_stats_use_canonical_entities(self):
        by_creator = rm_web.q_items(self.contract, {
            "creator": "Canonical Creator", "limit": "10",
        })
        by_studio = rm_web.q_items(self.contract, {
            "studio": "Canonical Studio", "limit": "10",
        })
        search = rm_web.q_items(self.contract, {
            "q": "Canonical Creator", "limit": "10",
        })
        self.assertEqual(by_creator["total"], 2)
        self.assertEqual(by_studio["total"], 2)
        self.assertEqual(search["total"], 2)
        self.assertEqual(rm_web.q_items(
            self.contract, {"creator": "Alice", "limit": "10"},
        )["total"], 0)

        creators = rm_web.q_index(self.contract, "creators", limit=10)
        self.assertEqual(creators["items"], [])
        performers = rm_web.q_index(self.contract, "performers", limit=10)
        artists = {row["k"]: row for row in performers["items"]}
        self.assertEqual(set(artists), {"Canonical Creator", "Canonical Alice"})
        self.assertEqual(artists["Canonical Creator"]["n"], 2)
        self.assertEqual(artists["Canonical Creator"]["entity_kind"], "creator")
        self.assertEqual(artists["Canonical Alice"]["n"], 1)
        self.assertEqual(artists["Canonical Alice"]["entity_kind"], "performer")
        stats = rm_web.q_stats(self.contract)
        self.assertEqual(stats["attribution"]["creator"], 2)
        self.assertEqual(stats["attribution"]["studio"], 2)
        facets = rm_web.q_facets(self.contract)
        self.assertEqual(facets["creators"][0]["k"], "Canonical Creator")

    def test_a_retired_performers_dead_link_is_listed_without_its_address(self):
        """资料页照旧列出那一枚，只写是哪家；地址不下发，悬停用的隐退年份一起给。"""
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO performer_profile(entity_id,active_until,source,source_url,fetched_at) "
            "VALUES(11,2021,'minnano-av','https://www.minnano-av.com/','2026-01-01')")
        con.execute(
            "INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,metadata_json,"
            "created_at,updated_at) VALUES(11,'official','Cruse Group',"
            "'https://crusegroup.net/model/316','crusegroup.net',"
            "'{\"gone\":{\"at\":\"2026-09-26\",\"note\":\"HTTP 404\"}}','2026-01-01','2026-01-01')")
        con.commit(); con.close()
        page = rm_web.q_entity(self.contract, {"kind": "performer", "name": "Canonical Alice"})
        [link] = page["links"]
        self.assertEqual((link["gone"], link["clickable"], link["url"], link["retired_year"]),
                         (True, False, None, 2021))
        self.assertEqual(link["label"], "Cruse Group")

    def test_performer_entity_page_and_watch_queue(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO entity_alias VALUES(11,'Alice','alice','test',1.0)"
        )
        con.execute(
            "INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,is_sensitive,"
            "created_at,updated_at) "
            "VALUES(11,'official','Official','https://example.com/alice','example.com',0,"
            "'2026-01-01','2026-01-01'),"
            "(11,'source_reference','Private source','https://source.invalid/a',"
            "'source.invalid',1,'2026-01-01','2026-01-01')"
        )
        con.execute(
            "INSERT INTO entity_search_term(entity_id,term,purpose,source,created_at) "
            "VALUES(11,'Alice code','source_lookup','user','2026-01-01')"
        )
        con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(15,'performer','Related Bob','related bob','2026-01-01','2026-01-01')"
        )
        con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(1,15,'performer','test',1.0)"
        )
        con.commit(); con.close()
        page = rm_web.q_entity(self.contract, {"kind": "performer", "name": "Alice"})
        self.assertEqual(page["canonical_name"], "Canonical Alice")
        self.assertEqual(page["asset_count"], 1)
        self.assertEqual(page["tags"], [{"id": 10, "k": "足交", "n": 1}])
        self.assertEqual(page["related_performers"][0]["k"], "Related Bob")
        self.assertEqual(page["related_performers"][0]["n"], 1)
        # 那排共演者是同一条两级取图链，取景也走同一份 sidecar。键必须在：没算过时
        # 是 None，页面据此维持几何居中，而键缺席会让页面读到 undefined。
        self.assertIn("avatar_focus", page["related_performers"][0])
        # 骨架照这份名单给卡底留出同台艺人那一条，名单与资料页用同一条判据。
        shapes = {entry["id"]: entry for entry in web_entity.q_entity_shapes(self.contract, {})["entities"]}
        self.assertEqual(shapes[11], {"id": 11, "kind": "performer",
                                      "names": ["Canonical Alice", "Alice"], "parts": ["costars", "jav"]})
        self.assertEqual(shapes[15]["parts"], ["costars", "jav"])
        self.assertTrue(page["links"][0]["clickable"])
        self.assertFalse(page["links"][1]["clickable"])
        self.assertIsNone(page["links"][1]["url"])
        # 实体契约里没有散文简介字段：资料页只展示可核对的身份、计数与链接。
        # 自由文本没有来源和置信度，也没有复核入口，不属于真相字段。
        self.assertNotIn("summary", page)
        self.assertEqual(rm_web.q_items(
            self.contract, {"performer": "Canonical Alice", "limit": "10"},
        )["total"], 1)
        self.assertTrue(rm_web.w_watch_later(
            self.contract, {"id": 1},
        )["watch_later"])
        self.assertTrue(rm_web.q_item(self.contract, 1)["watch_later"])
        self.assertFalse(rm_web.w_watch_later(
            self.contract, {"id": 1},
        )["watch_later"])

    def test_entity_name_prefers_canonical_and_only_displays_local_aliases(self):
        con = sqlite3.connect(self.db_path)
        con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(?,'performer',?,?,'2026-01-01','2026-01-01')",
            [(16, "飯岡かなこ", "飯岡かなこ"),
             (17, "森泽佳奈", "森泽佳奈"),
             (18, "释爱丽丝", "释爱丽丝")],
        )
        con.executemany(
            "INSERT INTO entity_alias VALUES(?,?,?,?,1.0)",
            [(17, "飯岡かなこ", "飯岡かなこ", "legacy"),
             (16, "共同旧名", "共同旧名", "legacy"),
             (17, "共同旧名", "共同旧名", "legacy"),
             (18, "Alice Shaku", "alice shaku", "mapping"),
             (18, "しゃくありす", "しゃくありす", "mapping"),
             (18, "釈アリス", "釈アリス", "mapping"),
             (18, "释爱丽丝", "释爱丽丝", "legacy")],
        )
        con.commit(); con.close()

        exact = rm_web.q_entity(
            self.contract, {"kind": "performer", "name": "飯岡かなこ"})
        self.assertEqual(exact["id"], 16, "规范名不能被另一实体的同名别名抢占")
        self.assertEqual(rm_web.q_entity(
            self.contract, {"kind": "performer", "name": "共同旧名"}),
            {"error": "not found"}, "撞名别名不能任意指向其中一位")

        localized = rm_web.q_entity(
            self.contract, {"kind": "performer", "name": "释爱丽丝"})
        self.assertIn("Alice Shaku", localized["aliases"], "罗马字仍须可用于身份检索")
        self.assertEqual(localized["display_aliases"], ["しゃくありす", "釈アリス"])

    def test_one_name_recorded_by_two_sources_is_listed_once(self):
        """`entity_alias` 的主键含 source，合并会再写一条 `merge:*`：同一个写法两行。

        留痕属于账本，资料页不该把同一个名字并排列两遍。取置信度最高的那一条。
        """
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(19,'performer','白石亚子','白石亚子','2026-01-01','2026-01-01')")
        con.executemany(
            "INSERT INTO entity_alias VALUES(?,?,?,?,?)",
            [(19, "Ako Shiraishi", "ako shiraishi", "r18:performer", 0.9),
             (19, "Ako Shiraishi", "ako shiraishi", "merge:duplicate-identity", 1.0),
             (19, "平沢すず", "平沢すず", "avdb-actor-mapping", 1.0)])
        con.commit(); con.close()

        entity = rm_web.q_entity(self.contract, {"kind": "performer", "name": "白石亚子"})
        self.assertEqual(entity["aliases"].count("Ako Shiraishi"), 1)
        self.assertIn("平沢すず", entity["aliases"])

    def _performer_with_two_names(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(20,'performer','飯岡かなこ','飯岡かなこ','2026-01-01','2026-01-01')")
        con.execute("INSERT INTO entity_alias VALUES(20,'森泽佳奈','森泽佳奈','javdb',1.0)")
        con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(1,20,'performer','test',1.0)")
        con.execute(
            "INSERT INTO asset_tag(asset_id,tag,source) VALUES(1,'演员:飯岡かなこ','performer')")
        con.commit(); con.close()

    def _names_of(self, entity_id=20):
        con = sqlite3.connect(self.db_path)
        canonical = con.execute(
            "SELECT canonical_name FROM entity WHERE id=?", (entity_id,)).fetchone()[0]
        aliases = {row[0]: row[1] for row in con.execute(
            "SELECT alias,source FROM entity_alias WHERE entity_id=?", (entity_id,))}
        # 只看这两个写法：夹具本来就给 asset 1 挂着另一位女优的扁平标签。
        tags = [row[0] for row in con.execute(
            "SELECT tag FROM asset_tag WHERE tag IN ('演员:飯岡かなこ','演员:森泽佳奈')"
            " ORDER BY tag")]
        con.close()
        return canonical, aliases, tags

    def test_preferred_name_promotes_an_existing_alias_and_demotes_the_old_one(self):
        """统称是用户的偏好，不是新证据：换的是哪个写法顶在标题上。

        被换下的旧规范名必须留成别名——它是这个人真用过的名字，也是换回去的入口。
        """
        self._performer_with_two_names()
        result = rm_web.w_entity_name(self.contract, {
            "kind": "performer", "name": "飯岡かなこ", "canonical": "森泽佳奈"})
        self.assertEqual(result["canonical_name"], "森泽佳奈")
        self.assertEqual(result["previous_name"], "飯岡かなこ")
        self.assertTrue(result["changed"])

        canonical, aliases, tags = self._names_of()
        self.assertEqual(canonical, "森泽佳奈")
        self.assertEqual(aliases.get("飯岡かなこ"), "user:preferred-name")
        # 扁平投影跟着走，否则卡片上还写着旧名、按旧名也照样查得到。
        self.assertEqual(tags, ["演员:森泽佳奈"])
        self.assertEqual(result["flat_rewritten"], 1)

    def test_preferred_name_can_be_chosen_back(self):
        self._performer_with_two_names()
        rm_web.w_entity_name(self.contract, {
            "kind": "performer", "name": "飯岡かなこ", "canonical": "森泽佳奈"})
        back = rm_web.w_entity_name(self.contract, {
            "kind": "performer", "name": "森泽佳奈", "canonical": "飯岡かなこ"})
        self.assertEqual(back["canonical_name"], "飯岡かなこ")
        canonical, aliases, tags = self._names_of()
        self.assertEqual(canonical, "飯岡かなこ")
        self.assertIn("森泽佳奈", aliases)
        self.assertEqual(tags, ["演员:飯岡かなこ"])

    def test_preferred_name_is_a_no_op_when_it_is_already_the_canonical_one(self):
        self._performer_with_two_names()
        result = rm_web.w_entity_name(self.contract, {
            "kind": "performer", "name": "飯岡かなこ", "canonical": "飯岡かなこ"})
        self.assertFalse(result["changed"])

    def test_preferred_name_refuses_free_text(self):
        """自由文本是改名，要有来源和证据，不是一次点击该干的事。"""
        self._performer_with_two_names()
        with self.assertRaises(ValueError):
            rm_web.w_entity_name(self.contract, {
                "kind": "performer", "name": "飯岡かなこ", "canonical": "另一个人"})
        self.assertEqual(self._names_of()[0], "飯岡かなこ")

    def test_preferred_name_reports_a_clash_with_another_entity(self):
        """规范名唯一。撞上另一条实体时只报冲突：那是该合并还是同名不同人，得人来判。"""
        self._performer_with_two_names()
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(21,'performer','森泽佳奈','森泽佳奈','2026-01-01','2026-01-01')")
        con.commit(); con.close()
        with self.assertRaises(ValueError):
            rm_web.w_entity_name(self.contract, {
                "kind": "performer", "name": "飯岡かなこ", "canonical": "森泽佳奈"})
        self.assertEqual(self._names_of()[0], "飯岡かなこ")

    def test_preferred_name_rewrites_the_flat_column_for_non_performers(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(22,'series','NightSafari','nightsafari','2026-01-01','2026-01-01')")
        con.execute("INSERT INTO entity_alias VALUES(22,'Night Safari','night safari','javdb',1.0)")
        con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(1,22,'series','test',1.0)")
        con.execute("UPDATE asset SET series='NightSafari' WHERE id=1")
        con.commit(); con.close()

        result = rm_web.w_entity_name(self.contract, {
            "kind": "series", "name": "NightSafari", "canonical": "Night Safari"})
        self.assertEqual(result["flat_rewritten"], 1)
        con = sqlite3.connect(self.db_path)
        self.assertEqual(
            con.execute("SELECT series FROM asset WHERE id=1").fetchone()[0], "Night Safari")
        con.close()

    def test_an_alias_typed_in_by_hand_becomes_one_of_this_entitys_names(self):
        """手敲的别名是身份补充：这个人还用过这个写法，账本此前不知道。

        它跟刮削来的别名进同一张表，所以头像图库按名字找图时立刻算数；随后要把它顶到
        标题上，走的仍是在已有名字里挑统称那一步。
        """
        self._performer_with_two_names()
        result = rm_web.w_entity_alias(self.contract, {
            "kind": "performer", "name": "飯岡かなこ", "alias": "桥本有菜"})
        self.assertTrue(result["added"])

        entity = rm_web.q_entity(self.contract, {"kind": "performer", "name": "飯岡かなこ"})
        self.assertIn("桥本有菜", entity["aliases"])
        # 界面只在自己添的这几个上给撤销，所以它们单独报一遍。
        self.assertEqual(entity["user_aliases"], ["桥本有菜"])
        self.assertEqual(self._names_of()[1]["桥本有菜"], "user:alias")

        promoted = rm_web.w_entity_name(self.contract, {
            "kind": "performer", "name": "飯岡かなこ", "canonical": "桥本有菜"})
        self.assertEqual(promoted["canonical_name"], "桥本有菜")

    def test_adding_a_name_the_entity_already_has_writes_nothing(self):
        self._performer_with_two_names()
        again = rm_web.w_entity_alias(self.contract, {
            "kind": "performer", "name": "飯岡かなこ", "alias": "森泽佳奈"})
        self.assertFalse(again["added"])
        itself = rm_web.w_entity_alias(self.contract, {
            "kind": "performer", "name": "飯岡かなこ", "alias": "飯岡かなこ"})
        self.assertFalse(itself["added"])
        self.assertEqual(sorted(self._names_of()[1]), ["森泽佳奈"])

    def test_only_the_aliases_added_by_hand_can_be_taken_back(self):
        """刮削和合并留下的别名是这条实体当初被认成这个人的依据，不给一次点击删掉。"""
        self._performer_with_two_names()
        rm_web.w_entity_alias(self.contract, {
            "kind": "performer", "name": "飯岡かなこ", "alias": "桥本有菜"})
        gone = rm_web.w_entity_alias(self.contract, {
            "kind": "performer", "name": "飯岡かなこ", "alias": "桥本有菜", "remove": True})
        self.assertTrue(gone["removed"])
        self.assertEqual(sorted(self._names_of()[1]), ["森泽佳奈"])

        with self.assertRaises(ValueError):
            rm_web.w_entity_alias(self.contract, {
                "kind": "performer", "name": "飯岡かなこ", "alias": "森泽佳奈", "remove": True})
        self.assertIn("森泽佳奈", self._names_of()[1])

    def test_a_name_that_already_belongs_to_another_entity_is_refused(self):
        """撞上另一条实体的统称时只报冲突：那是该合并还是同名不同人，得人来判。"""
        self._performer_with_two_names()
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(23,'performer','三上悠亜','三上悠亜','2026-01-01','2026-01-01')")
        con.commit(); con.close()
        with self.assertRaises(ValueError):
            rm_web.w_entity_alias(self.contract, {
                "kind": "performer", "name": "飯岡かなこ", "alias": "三上悠亜"})
        self.assertEqual(sorted(self._names_of()[1]), ["森泽佳奈"])

    def test_an_empty_alias_is_refused(self):
        self._performer_with_two_names()
        with self.assertRaises(ValueError):
            rm_web.w_entity_alias(self.contract, {
                "kind": "performer", "name": "飯岡かなこ", "alias": "   "})

    def test_entity_filter_and_video_sort_compose_in_one_items_query(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(2,11,'performer','test',1.0)")
        con.commit(); con.close()

        newest = rm_web.q_items(self.contract, {
            "performer": "Canonical Alice", "sort": "new", "limit": "10",
        })
        biggest = rm_web.q_items(self.contract, {
            "performer": "Canonical Alice", "sort": "big", "limit": "10",
        })
        self.assertEqual([item["id"] for item in newest["items"]], [1, 2])
        self.assertEqual([item["id"] for item in biggest["items"]], [2, 1])

    def test_items_pagination_is_bounded_and_seed_collisions_have_a_unique_order(self):
        with self.contract.write_transaction() as connection:
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium) VALUES(99992,'local','collision.mp4','collision.mp4','video')")
        args = {"seed": "1", "limit": "1"}
        first = rm_web.q_items(self.contract, args)
        second = rm_web.q_items(self.contract, {**args, "offset": "1"})
        self.assertEqual([first["items"][0]["id"], second["items"][0]["id"]], [1, 99992])
        bounded = rm_web.q_items(self.contract, {"limit": "-1", "offset": "-2", "seed": "1"})
        self.assertEqual([item["id"] for item in bounded["items"]], [1])

    def test_named_items_sort_over_the_complete_filtered_result_with_stable_pages(self):
        with self.contract.write_transaction() as connection:
            connection.executemany(
                "INSERT INTO asset(id,location,path,name,medium,duration) VALUES(?,'local',?,?,'video',10)",
                [(400 + index, f"sort-{index}.mp4", name)
                 for index, name in enumerate(["zebra.mp4", "Alpha.mp4", "alpha.mp4", "middle.mp4"])],
            )
        args = {"q": "mp4", "loc": "local", "sort": "name", "dir": "asc", "limit": "1"}
        complete = rm_web.q_items(self.contract, {**args, "limit": "100"})
        pages = [rm_web.q_items(self.contract, {**args, "offset": str(index)})
                 for index in range(complete["total"])]
        self.assertEqual([page["items"][0]["id"] for page in pages],
                         [item["id"] for item in complete["items"]])
        self.assertEqual([item["id"] for item in complete["items"] if item["id"] >= 400],
                         [401, 402, 403, 400])
        reverse = rm_web.q_items(self.contract, {**args, "limit": "100", "dir": "desc"})
        self.assertEqual([item["id"] for item in reverse["items"]],
                         list(reversed([item["id"] for item in complete["items"]])))
        tied = rm_web.q_items(self.contract, {"q": "Alpha", "sort": "dur", "dir": "asc", "limit": "1"})
        next_tied = rm_web.q_items(self.contract, {"q": "Alpha", "sort": "dur", "dir": "asc", "limit": "1", "offset": "1"})
        self.assertEqual([tied["items"][0]["id"], next_tied["items"][0]["id"]], [401, 402])

    def test_sort_takes_a_direction_and_puts_missing_values_last_either_way(self):
        """方向是独立参数，升序也把空值排在最后。

        每个可排序列在账本里都可为空。SQLite 的 `ASC` 默认把 NULL 排在最前，
        照默认写就等于让「时长未取得」的条目霸占「最短的视频」这一屏。
        """
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO asset(id,location,path,name,medium,size,duration,first_seen) "
            "VALUES(4,'local',?,'no-duration.mp4','video',50,NULL,'2026-08-11')",
            (r"R:\Media\no-duration.mp4",),
        )
        con.commit(); con.close()

        def ids(**args):
            return [item["id"] for item in rm_web.q_items(
                self.contract, {"sort": "dur", "limit": "10", **args})["items"]]

        self.assertEqual(ids(dir="desc"), [2, 1, 4])
        self.assertEqual(ids(dir="asc"), [1, 2, 4])
        # 不给方向时按列的默认方向走，与显式 desc 同解。
        self.assertEqual(ids(), [2, 1, 4])

    def test_a_vanished_playlist_entry_stays_listed_but_is_not_the_resume_point(self):
        """文件已不在盘上的条目照旧列出，续播位置落在它上面时退到第一条能播的。"""
        created = rm_web.w_playlist(self.contract, {
            "action": "create", "name": "周末", "asset_ids": [1, 2],
        })["playlist"]
        self.assertEqual(created["current_asset_id"], 1)

        def vanish(asset_id):
            with closing(sqlite3.connect(self.db_path)) as con, con:
                con.execute("UPDATE asset SET disposal='vanished' WHERE id=?", (asset_id,))

        vanish(1)
        page = web_playlists.q_playlist(self.contract, {"id": created["id"]})
        self.assertEqual([(item["id"], item["disposal"]) for item in page["items"]],
                         [(1, "vanished"), (2, None)])
        self.assertEqual(page["current_asset_id"], 2)
        vanish(2)
        self.assertIsNone(web_playlists.q_playlist(self.contract, {"id": created["id"]})["current_asset_id"])

    def test_persistent_playlist_can_save_mix_reorder_resume_and_edit(self):
        created = rm_web.w_playlist(self.contract, {
            "action": "create", "name": "  Alice Mix  ", "asset_ids": [1, 2, 1],
            "source_kind": "mix", "source_seed_asset_id": 1,
        })["playlist"]
        self.assertEqual(created["name"], "Alice Mix")
        self.assertEqual([item["id"] for item in created["items"]], [1, 2])
        self.assertEqual(created["current_asset_id"], 1)

        playlist_id = created["id"]
        renamed = rm_web.w_playlist(self.contract, {
            "action": "rename", "id": playlist_id, "name": "周末",
        })["playlist"]
        self.assertEqual(renamed["name"], "周末")
        reordered = rm_web.w_playlist(self.contract, {
            "action": "reorder", "id": playlist_id, "asset_ids": [2, 1],
        })["playlist"]
        self.assertEqual([item["id"] for item in reordered["items"]], [2, 1])
        resumed = rm_web.w_playlist(self.contract, {
            "action": "progress", "id": playlist_id, "asset_id": 2,
        })["playlist"]
        self.assertEqual(resumed["current_asset_id"], 2)
        edited = rm_web.w_playlist(self.contract, {
            "action": "remove", "id": playlist_id, "asset_id": 2,
        })["playlist"]
        self.assertEqual([item["id"] for item in edited["items"]], [1])
        self.assertEqual(edited["current_asset_id"], 1)
        self.assertEqual(rm_web.q_playlists(self.contract)["items"][0]["item_count"], 1)

        deleted = rm_web.w_playlist(self.contract, {
            "action": "delete", "id": playlist_id,
        })
        self.assertEqual(deleted["deleted"], playlist_id)
        self.assertEqual(rm_web.q_playlists(self.contract)["items"], [])
        con = sqlite3.connect(self.db_path)
        self.assertEqual(con.execute(
            "SELECT count(*) FROM playlist_item WHERE playlist_id=?", (playlist_id,),
        ).fetchone()[0], 0)
        con.close()

    def test_post_setup_tutorial_only_carries_progress_counts(self):
        """安装教程的聚合只带六个读数，不带馆藏明细、口味分析、关注列表与候选全文。

        首页右下角那张卡每次切路由都取一次；六个页面的完整载荷拼在一起，真实馆藏上要二十多秒、
        三百多 KB。这里钉住每一段的键集合，多出一个键就是明细搬进来了。
        """
        from peach import taste_history
        root = Path(self.tmp.name).resolve()
        (root / "candidates").mkdir()
        (root / "secrets").mkdir()
        store = root / "history.db"
        with closing(sqlite3.connect(store)) as history:
            taste_history._prepare_store(history)
            history.execute(
                "INSERT INTO history_source VALUES('k1','chrome','Default','pc','h','2026-01-01','2026-01-02')")
            history.commit()
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO follow_source(provider,ref,label,url,enabled,created_at,updated_at) "
            "VALUES('kemono','a','A','https://kemono.su/a',1,'2026-01-01','2026-01-01'),"
            "('rule34','b','B','https://rule34.xxx/b',0,'2026-01-01','2026-01-01')")
        con.commit()
        con.close()
        contract = rm_web.WebContract(
            Path(self.db_path), avatar_root=self.avatars, logo_root=self.logos,
            candidate_root=root / "candidates", follow_secrets_root=root / "secrets",
            taste_history_store=store)
        payload = rm_web.dispatch_api_get(contract, "/api/post-setup-tutorial", {})
        self.assertEqual(set(payload), {"library", "scraping", "taste", "follow", "credentials", "review"})
        # 馆藏读数与首页列表同一条 WHERE：图片与回收站不算。
        self.assertEqual(payload["library"], {"total": 2})
        self.assertEqual(payload["taste"], {"history_sources": 1})
        self.assertEqual(payload["follow"], {"sources": [
            {"provider": "kemono", "enabled": True}, {"provider": "rule34", "enabled": False}]})
        self.assertTrue(payload["scraping"]["sources"])
        for source in payload["scraping"]["sources"]:
            self.assertEqual(set(source), {"source", "accepts_cookie", "cookie_saved"})
        self.assertTrue(payload["credentials"]["providers"])
        for provider in payload["credentials"]["providers"]:
            self.assertEqual(set(provider), {"provider", "requirement", "present", "missing"})
        self.assertEqual(set(payload["review"]), {"counts"})
        self.assertTrue(all(isinstance(value, int) for value in payload["review"]["counts"].values()))
        self.assertLess(len(json.dumps(payload, ensure_ascii=False)), 8_000)





class ChineseSearchTermTests(unittest.TestCase):
    """短查询必须能通过别名和检索词命中日文汉字身份。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = str(fresh_ledger(self.tmp.name))
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO asset(id,location,path,name,medium,duration,first_seen) "
            "VALUES(1,'local',?,'ABW-232.mp4','video',100,'2026-08-18')",
            (r"R:\Media\ABW-232.mp4",))
        con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(11,'performer','涼森れむ','涼森れむ','2026-01-01','2026-01-01')")
        con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(1,11,'performer','test',1.0)")
        con.commit(); con.close()
        self.contract = rm_web.WebContract(Path(self.db_path))

    def found(self, query):
        return [row["id"] for row in
                rm_web.q_items(self.contract, {"q": query, "limit": "10"})["items"]]

    def _add_term(self, term):
        # purpose 只有 'discovery' 和 'source_lookup' 两个合法值（0002 的 CHECK）。
        # 这里此前写的是 'search'，真实账本里根本存不下这一行。
        con = sqlite3.connect(self.db_path)
        con.execute("INSERT INTO entity_search_term(entity_id,term,purpose,source,"
                    "created_at) "
                    "VALUES(11,?,'discovery','hanzi-simplified','2026-01-01')", (term,))
        con.commit(); con.close()

    def test_simplified_query_misses_before_a_term_exists(self):
        self.assertEqual(self.found("凉森"), [])

    def test_simplified_query_hits_through_the_search_term(self):
        # 「凉森」只有两字，trigram 用不上，永远走 LIKE 分支。
        self._add_term("凉森れむ")
        self.assertEqual(self.found("凉森"), [1])

    def test_original_japanese_spelling_still_works(self):
        self._add_term("凉森れむ")
        self.assertEqual(self.found("涼森"), [1])

    def test_alias_is_matched_by_short_queries_too(self):
        con = sqlite3.connect(self.db_path)
        con.execute("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
                    " VALUES(11,'Remu Suzumori','remu suzumori','r18',1.0)")
        con.commit(); con.close()
        self.assertEqual(self.found("Suzumori"), [1])

    def test_unrelated_query_still_finds_nothing(self):
        self._add_term("凉森れむ")
        self.assertEqual(self.found("凉宫"), [])


class JavModeAndCoverTests(unittest.TestCase):
    """JAV 模式的边界与封面查找。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.covers = root / "covers"
        self.covers.mkdir()
        self.db_path = str(fresh_ledger(root))
        con = sqlite3.connect(self.db_path)
        # 前四条是真番号的四种形态；JI-103 虽像番号，但只有 creator、没有发行证据。
        con.executemany(
            "INSERT INTO asset(id,location,path,name,medium,code,creator,studio,duration,first_seen) "
            "VALUES(?,'local',?,?,'video',?,?,?,100,'2026-08-18')",
            [(1, r"R:\a.mp4", "a.mp4", "ABW-232", None, "Studio A"),
             (2, r"R:\b.mp4", "b.mp4", "259LUXU-1468", None, "Studio B"),
             (3, r"R:\c.mp4", "c.mp4", "FC2-PPV-1234567", None, None),
             (4, r"R:\d.mp4", "d.mp4", "040221-001", None, "Studio D"),
             (5, r"R:\e.mp4", "e.mp4", "RAIKUN325", None, None),
             (6, r"R:\f.mp4", "f.mp4", "HHD800", None, None),
             (7, r"R:\g.mp4", "g.mp4", None, None, None),
             (8, r"R:\h.mp4", "JI-103 Jioh.mp4", "JI-103", "MIB", None)],
        )
        con.executemany(
            "UPDATE asset SET release_date=? WHERE id=?",
            [("2022-05-13", 1), ("2024-02-01", 2), (None, 3), ("2023-09-08", 4)],
        )
        con.commit(); con.close()
        self.contract = rm_web.WebContract(Path(self.db_path), cover_root=self.covers)

    def ids(self, args):
        return sorted(row["id"] for row in
                      rm_web.q_items(self.contract, {**args, "limit": "20"})["items"])

    def test_jav_mode_keeps_only_real_code_shapes(self):
        self.assertEqual(self.ids({"jav": "1"}), [1, 2, 3, 4])

    def test_jav_dto_keeps_the_raw_filename_for_file_operations(self):
        row = rm_web.q_item(self.contract, 1)
        self.assertTrue(row["is_jav"])
        self.assertEqual((row["code"], row["name"]), ("ABW-232", "a.mp4"))

    def test_jav_dto_exposes_catalog_titles(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "UPDATE asset SET catalog_title='正式作品标题',original_title='原标题' WHERE id=1"
        )
        con.commit(); con.close()
        row = rm_web.q_item(self.contract, 1)
        self.assertEqual(row["catalog_title"], "正式作品标题")
        self.assertEqual(row["original_title"], "原标题")

    def test_compact_code_with_release_evidence_gets_canonical_display_fields(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO asset(id,location,path,name,medium,code,studio,duration,first_seen) "
            "VALUES(9,'local',?,'PBD00390.mp4','video','PBD390','PREMIUM',100,'2026-08-18')",
            (r"R:\PBD00390.mp4",),
        )
        con.commit(); con.close()
        row = rm_web.q_item(self.contract, 9)
        self.assertTrue(row["is_jav"])
        self.assertEqual(row["display_code"], "PBD-390")
        self.assertEqual(row["display_title"], "")
        self.assertEqual(row["edition_badges"], [])
        self.assertEqual((row["code"], row["name"]), ("PBD390", "PBD00390.mp4"))
        self.assertIn(9, self.ids({"jav": "1"}))

    def test_filename_suffixes_become_edition_badges_not_title_text(self):
        subtitled = rm_web.jav_display_metadata("CJOD-175-C.mp4", "CJOD-175")
        advertised = rm_web.jav_display_metadata(
            "CJOD-158 CJOD-158[fuckbe.com].mp4", "CJOD-158",
        )
        uncensored = rm_web.jav_display_metadata(
            "ABP614.Hinata.Mio.Uncen.mp4", "ABP614",
        )
        cracked = rm_web.jav_display_metadata(
            "MIDE-950.mp4", "MIDE-950", ["AI去码"],
        )
        self.assertEqual(subtitled, {
            "display_code": "CJOD-175", "display_title": "",
            "edition_badges": ["中字"],
        })
        self.assertEqual(advertised["display_title"], "")
        self.assertEqual(uncensored["display_title"], "Hinata Mio")
        self.assertEqual(uncensored["edition_badges"], ["无码"])
        self.assertEqual(cracked["edition_badges"], ["无码破解"])

    def test_uncensored_release_names_are_not_shown_as_titles(self):
        """无码片的文件名由「发行站 + 番号 + 画质/分卷」拼成，一个标题词都没有。

        修之前界面把剥掉番号后的残渣当标题显示：`040221-001 carib-1080p`、
        `071213-625 1pon-whole1 hd`、`heyzo hd 1380 full`。
        """
        for name, code in (
            ("040221-001-carib-1080p.mp4", "040221-001"),
            ("051421-001-carib-720p.mp4", "051421-001"),
            ("071213-625-1pon-whole1_hd.avi", "071213-625"),
            ("heyzo_hd_1380_full.mp4", "HEYZO-1380"),
            ("259LUXU-971_1080p.mp4", "259LUXU-971"),
            ("259LUXU-934.HD.mp4", "259LUXU-934"),
        ):
            self.assertEqual(
                rm_web.jav_display_metadata(name, code)["display_title"], "", name)

    def test_code_is_stripped_even_when_the_release_site_comes_first(self):
        # `1pon-092415-001-fhd1_(new).mp4`：番号在中间，只认前缀就会整个显示出来。
        self.assertEqual(
            rm_web.jav_display_metadata(
                "1pon-092415-001-fhd1_(new).mp4", "092415-001")["display_title"], "")
        self.assertEqual(
            rm_web.jav_display_metadata(
                "122614_001-1pon-whole1_hd.avi", "122614-947")["display_title"], "")

    def test_real_titles_survive_the_release_noise_filter(self):
        # 判空判据是「没有中日文、也没有长度 ≥4 的字母词」，真标题不受影响。
        self.assertEqual(
            rm_web.jav_display_metadata(
                "MIN-101 Minah My new companion hard fuck racing girl.mp4",
                "MIN-101")["display_title"],
            "Minah My new companion hard fuck racing girl")
        self.assertEqual(
            rm_web.jav_display_metadata(
                "ABP614.Hinata.Mio.Uncen.mp4", "ABP614")["display_title"], "Hinata Mio")
        self.assertEqual(
            rm_web.jav_display_metadata(
                "MIDE-925 痴女に犯される.mp4", "MIDE-925")["display_title"],
            "痴女に犯される")

    def test_uncensored_studios_get_the_badge_without_a_filename_marker(self):
        """无码片商的片本身就是无码，不需要文件名里另有 `-U`／`Uncen`。

        番号形状（`040221-001`、`HEYZO-1380`）和文件名里的发行站都是本机可核验
        的证据，不依赖抓取——这些番号在 r18.dev 永远 404，等元数据到齐再判，
        徽章就永远不会出现。
        """
        for name, code in (
            ("040221-001-carib-1080p.mp4", "040221-001"),
            ("1pon-092415-001-fhd1_(new).mp4", "092415-001"),
            ("heyzo_hd_1380_full.mp4", "HEYZO-1380"),
            ("122614_001-1pon-whole1_hd.avi", "122614-947"),
        ):
            self.assertEqual(
                rm_web.jav_display_metadata(name, code)["edition_badges"], ["无码"], name)
        # 有码番号不能因为这条判据凭空拿到徽章。
        for name, code in (("ABW-220.mp4", "ABW-220"), ("MIDE-925-4K.mp4", "MIDE-925")):
            self.assertEqual(
                rm_web.jav_display_metadata(name, code)["edition_badges"], [], name)

    def test_edition_markers_glued_to_the_code_still_count(self):
        """`PPPD-937CH.mp4` 中间没有分隔符，此前既没徽章、番号还被当标题显示。"""
        for name, code in (
            ("PPPD-937CH.mp4", "PPPD-937"), ("MIDV-751CH.mp4", "MIDV-751"),
            ("MIRD-204CH.mp4", "MIRD-204"),
        ):
            row = rm_web.jav_display_metadata(name, code)
            self.assertEqual(row["display_title"], "", name)
            self.assertEqual(row["edition_badges"], ["中字"], name)
        # `-UN` 和 `-U`／`-UC` 是同一个意思；标题判空后不认它就等于丢掉这条信息。
        self.assertEqual(
            rm_web.jav_display_metadata("ABF-158-UN.mp4", "ABF-158")["edition_badges"],
            ["无码"])
        # 相邻字母不能被当成版次标记吞掉。
        self.assertEqual(
            rm_web.jav_display_metadata("ABC-123CHAPTER.mp4", "ABC-123")["edition_badges"], [])

    def test_release_sort_orders_official_dates_descending_and_missing_last(self):
        rows = rm_web.q_items(
            self.contract, {"jav": "1", "sort": "release", "limit": "20"},
        )["items"]
        self.assertEqual([row["id"] for row in rows], [2, 4, 1, 3])

    def test_release_sort_ascending_keeps_missing_dates_last(self):
        """翻成升序时缺发行日的条目仍在最后，不是抢占「最早」那一屏。"""
        rows = rm_web.q_items(
            self.contract, {"jav": "1", "sort": "release", "dir": "asc", "limit": "20"},
        )["items"]
        self.assertEqual([row["id"] for row in rows], [1, 4, 2, 3])

    def test_uploader_handles_in_the_code_column_are_excluded(self):
        # `RAIKUN325` 是 myfans 账号名、`HHD800` 是站点水印，都不是番号。
        self.assertNotIn(5, self.ids({"jav": "1"}))
        self.assertNotIn(6, self.ids({"jav": "1"}))

    def test_code_shaped_creator_clip_without_release_evidence_is_excluded(self):
        self.assertNotIn(8, self.ids({"jav": "1"}))
        self.assertFalse(rm_web.q_item(self.contract, 8)["is_jav"])

    def test_without_the_flag_nothing_is_filtered(self):
        self.assertEqual(self.ids({}), [1, 2, 3, 4, 5, 6, 7, 8])

    def test_shape_predicate_matches_the_documented_forms(self):
        for good in ("ABW-232", "259LUXU-1468", "FC2-PPV-1234567", "040221-001"):
            self.assertTrue(rm_web.is_jav_code(good), good)
        for bad in ("RAIKUN325", "HHD800", "WX17", "BANBI_555", "", None):
            self.assertFalse(rm_web.is_jav_code(bad), repr(bad))

    def test_cover_key_normalises_the_same_way_as_the_fetcher(self):
        self.assertEqual(rm_web.normalise_code_key("abw232"), "ABW-232")
        self.assertEqual(rm_web.normalise_code_key("ABW-0232"), "ABW-232")
        self.assertEqual(rm_web.normalise_code_key("278gyan17"), "278GYAN-017")

    def test_maker_prefixed_shape_covers_longer_letter_segments(self):
        # `\d{3}[A-Z]{2,6}` 漏掉 5 字母以上的字母段；`230ORETD-001` 真实存在于账本。
        for good in ("259LUXU-1642", "230ORETD-001", "300MIUM-1024", "762FKOS-001"):
            self.assertTrue(catalog_rules.is_jav_code(good), good)

    def test_query_variants_pair_the_two_writings_of_one_release(self):
        # 第一个永远是账本的规范写法：评审键不能随回退漂移。
        self.assertEqual(catalog_rules.code_query_variants("259LUXU-1642"),
                         ("259LUXU-1642", "LUXU-1642"))
        self.assertEqual(catalog_rules.code_query_variants("luxu1642"),
                         ("LUXU-1642", "259LUXU-1642"))

    def test_query_variants_never_invent_a_prefix_for_unregistered_letters(self):
        # 补前缀要凭空填三位数字，猜错就是拿别人的番号去查。
        self.assertEqual(catalog_rules.code_query_variants("ABW-232"), ("ABW-232",))
        self.assertEqual(catalog_rules.code_query_variants("FC2-PPV-1234567"),
                         ("FC2-PPV-1234567",))
        self.assertEqual(catalog_rules.code_query_variants(""), ())
        self.assertEqual(catalog_rules.code_query_variants(None), ())

    def test_release_identity_tolerates_the_benign_writing_differences(self):
        for left, right in (
            ("259LUXU-1642", "LUXU-1642"),      # 片商数字前缀
            ("IQQQ-026", "IQQQ-26"),            # 来源不补零
            ("SY-101", "h_113sy00101"),         # DMM label 标记 + content_id
            ("BAZX-123", "7BAZX-123"),          # 发行方数字
            ("HA-102", "49ha102r"),             # 重制尾字母
            ("FC2-PPV-1812235", "1812235"),     # FC2 来源只给数字
            ("040221-001", "040221001"),        # 素人日期号
        ):
            self.assertTrue(catalog_rules.same_release_code(left, right), f"{left} vs {right}")

    def test_release_identity_rejects_the_wrong_release(self):
        # 这些正是 2026-09-02 那批把 MIB 整目录刮错的返回值。
        for left, right in (
            ("SA-104", "AVSA-104"), ("CHU-101", "CHUC-101"), ("AR-301", "STAR-3016"),
            ("SR-101", "SSR-101"), ("259LUXU-164", "259LUXU-1642"),
            ("390JAC-040", "JAC-040"), ("390JNT-002", "JNT-002"),
            ("390JNT-006", "JNT-006"), ("390JNT-015", "JNT-015"),
            ("123ABC-007", "ABC-007"), ("123ABC-007", "456ABC-007"),
            ("ABW-232", ""), ("", "ABW-232"), ("ABW-232", None),
        ):
            self.assertFalse(catalog_rules.same_release_code(left, right), f"{left} vs {right}")

    def test_cards_report_whether_a_cover_is_on_disk(self):
        (self.covers / "ABW-232.jpg").write_bytes(b"x")
        rows = {row["id"]: row for row in
                rm_web.q_items(self.contract, {"jav": "1", "limit": "20"})["items"]}
        self.assertTrue(rows[1]["has_cover"])
        self.assertFalse(rows[2]["has_cover"])

    def test_item_detail_also_reports_whether_a_cover_is_on_disk(self):
        """详情开场也要能照「JAV 默认封面」设置选海报位。

        单条详情漏发这一格，播放器就只能永远落在预览图那一档：同一部作品在卡片
        上是封套、进详情却换了一张，两处各自一份判据迟早分叉。
        """
        (self.covers / "ABW-232.jpg").write_bytes(b"x")
        self.assertTrue(rm_web.q_item(self.contract, 1)["has_cover"])
        self.contract.cache_bust()
        (self.covers / "ABW-232.jpg").unlink()
        self.assertFalse(rm_web.q_item(self.contract, 1)["has_cover"])

    def test_missing_cover_resolves_to_none_not_a_broken_path(self):
        self.assertIsNone(self.contract.cover_path("ABW-232"))
        self.assertIsNone(self.contract.cover_path(None))

    def test_cover_frame_reads_both_axes_out_of_the_face_sidecar(self):
        """哪个轴生效由容器和图片的宽高比决定，数据层两个都得给出去。

        16:9 官方剧照在大图容器里只会横向裁，横向锚点丢在这一层的话，页面就只能
        退回写死的 50%，偏在一侧的人物整个落到可见窗口外面。
        """
        (self.covers / "ABW-232.jpg").write_bytes(b"x")
        (self.covers / "ABW-232.face.json").write_text(
            '{"ratio":1.49,"face":{"cx":0.82,"cy":0.19}}', encoding="utf-8")
        self.assertEqual(self.contract.cover_frame("ABW-232"),
                         {"cx": 0.82, "cy": 0.19})

    def test_a_sidecar_with_only_one_axis_keeps_just_that_axis(self):
        """缺的那个轴不带键，页面各自回落；不能拿另一个轴的值顶替。"""
        (self.covers / "ABW-232.jpg").write_bytes(b"x")
        (self.covers / "ABW-232.face.json").write_text(
            '{"ratio":1.78,"face":{"cx":0.13}}', encoding="utf-8")
        self.assertEqual(self.contract.cover_frame("ABW-232"), {"cx": 0.13})

    def test_a_cover_without_a_sidecar_falls_back_silently(self):
        (self.covers / "ABW-232.jpg").write_bytes(b"x")
        self.assertIsNone(self.contract.cover_frame("ABW-232"))

    def test_a_sidecar_reporting_no_face_is_not_a_frame(self):
        # 954 张实测检出 885 张，没检出是少数但确实存在——必须安静回落。
        (self.covers / "ABW-232.jpg").write_bytes(b"x")
        (self.covers / "ABW-232.face.json").write_text(
            '{"ratio":1.49,"face":null}', encoding="utf-8")
        self.assertIsNone(self.contract.cover_frame("ABW-232"))

    def test_a_corrupt_sidecar_never_breaks_the_card(self):
        (self.covers / "ABW-232.jpg").write_bytes(b"x")
        (self.covers / "ABW-232.face.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.contract.cover_frame("ABW-232"))

    def install_poster_sidecar(self, method: str = "fold") -> None:
        (self.covers / "ABW-232.jpg").write_bytes(b"x")
        (self.covers / "ABW-232.poster.json").write_text(json.dumps({
            "version": jav_poster_crop.ALGORITHM_VERSION, "px": [800, 540],
            "box": {"x0": 419, "y0": 0, "x1": 800, "y1": 540, "method": method},
        }), encoding="utf-8")
        self.contract.cache_bust()

    def test_the_card_carries_the_front_panel_box_when_it_has_been_computed(self):
        """正封位置要的是一个框，不是锚点：折痕在哪由离线脚本算好写在边车里。"""
        self.install_poster_sidecar()
        self.assertEqual(self.contract.poster_box("ABW-232"),
                         {"x0": 419, "y0": 0, "x1": 800, "y1": 540,
                          "method": "fold", "px": [800, 540]})
        rows = {row["id"]: row for row in
                rm_web.q_items(self.contract, {"jav": "1", "limit": "20"})["items"]}
        self.assertEqual(rows[1]["poster_box"]["method"], "fold")

    def test_a_cover_that_should_not_be_cropped_reports_no_box(self):
        """不该裁的那一档也会落边车，但它不是一个可用的框，字段给 null。"""
        self.install_poster_sidecar(method="none")
        self.assertIsNone(self.contract.poster_box("ABW-232"))
        rows = {row["id"]: row for row in
                rm_web.q_items(self.contract, {"jav": "1", "limit": "20"})["items"]}
        self.assertIsNone(rows[1]["poster_box"])

    def test_a_stale_or_corrupt_poster_sidecar_is_ignored(self):
        (self.covers / "ABW-232.jpg").write_bytes(b"x")
        (self.covers / "ABW-232.poster.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.contract.poster_box("ABW-232"))
        (self.covers / "ABW-232.poster.json").write_text(json.dumps({
            "version": "poster-crop-v0", "px": [800, 540],
            "box": {"x0": 419, "y0": 0, "x1": 800, "y1": 540, "method": "fold"},
        }), encoding="utf-8")
        self.contract.cache_bust()
        self.assertIsNone(self.contract.poster_box("ABW-232"))

    def test_the_two_cover_sidecars_do_not_shadow_each_other(self):
        """脸和正封框各描述一件事，同一次目录扫描一起收齐，缺一份不影响另一份。"""
        self.install_poster_sidecar()
        (self.covers / "ABW-232.face.json").write_text(
            '{"ratio":1.49,"face":{"cx":0.82,"cy":0.19}}', encoding="utf-8")
        self.contract.cache_bust()
        self.assertEqual(self.contract.cover_frame("ABW-232"),
                         {"cx": 0.82, "cy": 0.19})
        self.assertEqual(self.contract.poster_box("ABW-232")["x0"], 419)
        self.assertTrue(self.contract.has_cover("ABW-232"))




class FaceFocusMathTests(unittest.TestCase):
    """人脸中心 → 圆框 object-position 的换算。"""

    def test_a_portrait_centers_the_face_vertically(self):
        # 640x960（ratio 2/3）、脸心在 cy=0.45：窗口应从余量的 35% 处开始。
        self.assertEqual(rm_web.face_focus(0.667, 0.5, 0.45),
                         {"axis": "y", "pct": 35})

    def test_the_centered_face_keeps_the_default_position(self):
        self.assertEqual(rm_web.face_focus(2 / 3, 0.5, 0.5),
                         {"axis": "y", "pct": 50})

    def test_faces_too_close_to_an_edge_clamp_instead_of_overflowing(self):
        self.assertEqual(rm_web.face_focus(0.667, 0.5, 0.0),
                         {"axis": "y", "pct": 0})
        self.assertEqual(rm_web.face_focus(0.667, 0.5, 1.0),
                         {"axis": "y", "pct": 100})

    def test_a_landscape_image_takes_its_margin_horizontally(self):
        # 1500x1000、脸心在 cx=0.6：窗口应从余量的 80% 处开始。
        self.assertEqual(rm_web.face_focus(1.5, 0.6, 0.4),
                         {"axis": "x", "pct": 80})
        # 脸太靠右时夹取到边缘，不越界。
        self.assertEqual(rm_web.face_focus(1.5, 0.9, 0.4),
                         {"axis": "x", "pct": 100})

    def test_near_square_images_have_no_margin_to_reframe(self):
        self.assertIsNone(rm_web.face_focus(1.0, 0.5, 0.3))
        self.assertIsNone(rm_web.face_focus(1.03, 0.5, 0.3))

    def test_unreadable_numbers_return_none_not_an_exception(self):
        self.assertIsNone(rm_web.face_focus(None, 0.5, 0.3))
        self.assertIsNone(rm_web.face_focus("x", 0.5, 0.3))
        self.assertIsNone(rm_web.face_focus(-1, 0.5, 0.3))


class AvatarFocusTests(unittest.TestCase):
    """资料页实体图的人脸取景 sidecar。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.avatars = root / "avatars"
        self.avatars.mkdir()
        self.contract = rm_web.WebContract(
            Path(str(root / "ledger.db")), avatar_root=self.avatars)

    def test_the_sidecar_focus_is_passed_through(self):
        (self.avatars / "performer-7900.img").write_bytes(b"x")
        (self.avatars / "performer-7900.face.json").write_text(
            '{"ratio":0.667,"face":{"cx":0.5,"cy":0.45},'
            '"focus":{"axis":"y","pct":35}}', encoding="utf-8")
        self.assertEqual(self.contract.avatar_focus("performer", 7900),
                         {"axis": "y", "pct": 35})

    def test_no_sidecar_falls_back_silently(self):
        (self.avatars / "performer-7900.img").write_bytes(b"x")
        self.assertIsNone(self.contract.avatar_focus("performer", 7900))

    def test_a_sidecar_without_a_detection_is_not_a_focus(self):
        # Haar 对侧脸、低头会漏检，没检出是常态而不是错误——必须安静回落。
        (self.avatars / "performer-7900.img").write_bytes(b"x")
        (self.avatars / "performer-7900.face.json").write_text(
            '{"ratio":0.667,"face":null}', encoding="utf-8")
        self.assertIsNone(self.contract.avatar_focus("performer", 7900))

    def test_a_corrupt_sidecar_never_breaks_the_page(self):
        (self.avatars / "performer-7900.img").write_bytes(b"x")
        (self.avatars / "performer-7900.face.json").write_text(
            "{not json", encoding="utf-8")
        self.assertIsNone(self.contract.avatar_focus("performer", 7900))

    def test_a_malformed_focus_is_rejected(self):
        (self.avatars / "performer-7900.face.json").write_text(
            '{"focus":{"axis":"z","pct":50}}', encoding="utf-8")
        (self.avatars / "studio-12.face.json").write_text(
            '{"focus":{"axis":"y","pct":"high"}}', encoding="utf-8")
        (self.avatars / "creator-13.face.json").write_text(
            '{"focus":{"axis":"x","pct":-20}}', encoding="utf-8")
        (self.avatars / "series-14.face.json").write_text(
            '{"focus":{"axis":"y","pct":true}}', encoding="utf-8")
        self.assertIsNone(self.contract.avatar_focus("performer", 7900))
        self.assertIsNone(self.contract.avatar_focus("studio", 12))
        self.assertIsNone(self.contract.avatar_focus("creator", 13))
        self.assertIsNone(self.contract.avatar_focus("series", 14))

    def test_the_face_box_is_handed_over_in_source_pixels(self):
        # 放大到几倍还清楚，问的是「这张脸有多少像素」；归一化值答不了，所以脸框
        # 在这里就换算成源图像素。`performer-8711` 的真实数据：640×960 的全身照，
        # 脸框归一化 0.105 宽，还原成 67 px。
        (self.avatars / "performer-7900.face.json").write_text(
            '{"ratio":0.667,"px":[640,960],'
            '"face":{"cx":0.439,"cy":0.224,"w":0.105,"h":0.095,"score":0.934},'
            '"focus":{"axis":"y","pct":0}}', encoding="utf-8")
        self.assertEqual(
            self.contract.avatar_focus("performer", 7900),
            {"axis": "y", "pct": 0,
             "box": {"cx": 0.439, "cy": 0.224, "faceW": 67,
                     "imgW": 640, "imgH": 960}})

    def test_a_square_image_gets_a_box_even_without_an_axis(self):
        # 方图算不出 object-position——没有被裁的方向可挪——但脸小一样该放大。
        # 两半各自缺失：只有 focus 那一半的旧 sidecar 也不能因此丢掉。
        (self.avatars / "performer-7900.face.json").write_text(
            '{"ratio":1.0,"px":[1000,1000],'
            '"face":{"cx":0.5,"cy":0.3,"w":0.15,"h":0.15,"score":0.9}}',
            encoding="utf-8")
        self.assertEqual(
            self.contract.avatar_focus("performer", 7900),
            {"box": {"cx": 0.5, "cy": 0.3, "faceW": 150,
                     "imgW": 1000, "imgH": 1000}})

    def test_a_sidecar_written_before_the_pixels_existed_still_pans(self):
        # 补字段之前算的 512 张只有脸心。那些照旧只挪不放大，不是整条取景一起作废。
        (self.avatars / "performer-7900.face.json").write_text(
            '{"ratio":0.667,"face":{"cx":0.5,"cy":0.45},'
            '"focus":{"axis":"y","pct":35}}', encoding="utf-8")
        self.assertEqual(self.contract.avatar_focus("performer", 7900),
                         {"axis": "y", "pct": 35})

    def test_a_malformed_box_is_rejected_without_taking_the_axis_down(self):
        # 坏的那一半丢掉，好的那一半留着。源图像素是 0 或负数时脸框像素还原不出来，
        # 按 0 处理会让页面算出一个无穷大的倍数。
        bad = [
            '{"px":[0,960],"face":{"cx":0.4,"cy":0.2,"w":0.1}}',
            '{"px":[640],"face":{"cx":0.4,"cy":0.2,"w":0.1}}',
            '{"px":[640,960],"face":{"cx":0.4,"cy":0.2,"w":0}}',
            '{"px":[640,960],"face":{"cx":1.4,"cy":0.2,"w":0.1}}',
            '{"px":[640,960],"face":{"cx":0.4,"cy":0.2,"w":"wide"}}',
            '{"px":[640,960],"face":{"cx":true,"cy":0.2,"w":0.1}}',
            '{"px":"640x960","face":{"cx":0.4,"cy":0.2,"w":0.1}}',
        ]
        for index, payload in enumerate(bad):
            with self.subTest(payload=payload):
                path = self.avatars / f"performer-{8000 + index}.face.json"
                path.write_text(payload[:-1] + ',"focus":{"axis":"y","pct":30}}',
                                encoding="utf-8")
                self.assertEqual(self.contract.avatar_focus("performer", 8000 + index),
                                 {"axis": "y", "pct": 30})


class PeopleIndexFocusTests(unittest.TestCase):
    """索引页大图版式的取景：与资料页大图同一份 sidecar，只是按行取。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.db_path = str(fresh_ledger(str(root)))
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO asset(id,location,path,name,medium,size,snapshot_path,first_seen)"
            " VALUES(1,'local',?,'one.mp4','video',100,?,'2026-08-14')",
            (r"R:\Media\one.mp4", r"R:\snap\1.jpg"),
        )
        con.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,?,?,?,'2026-01-01','2026-01-01')",
            [(10, "tag", "足交", "足交"),
             (11, "performer", "Canonical Alice", "canonical alice"),
             (12, "creator", "Canonical Creator", "canonical creator")],
        )
        con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
            " VALUES(?,?,?,?,1.0)",
            [(1, 10, "tag", "test"),
             (1, 11, "performer", "test"),
             (1, 12, "creator", "test")],
        )
        con.commit()
        con.close()
        self.avatars = root / "avatars"
        self.avatars.mkdir()
        self.contract = rm_web.WebContract(Path(self.db_path), avatar_root=self.avatars)

    def test_each_row_carries_the_focus_the_entity_page_would_use(self):
        (self.avatars / "performer-11.face.json").write_text(
            '{"focus":{"axis":"y","pct":30}}', encoding="utf-8")
        row = rm_web.q_index(self.contract, "performers")["items"][0]
        self.assertEqual(row["k"], "Canonical Alice")
        # 同一个实体，索引页大图和资料页大图必须取到同一个位置。
        self.assertEqual(row["avatar_focus"], {"axis": "y", "pct": 30})
        self.assertEqual(row["avatar_focus"], self.contract.avatar_focus("performer", 11))

    def test_a_row_without_a_sidecar_says_so_instead_of_dropping_the_key(self):
        # 没算过是常态：键仍然在，页面据此维持几何居中。
        row = rm_web.q_index(self.contract, "performers")["items"][0]
        self.assertIsNone(row["avatar_focus"])

    def test_creators_read_their_own_kind_not_the_performer_sidecar(self):
        (self.avatars / "performer-12.face.json").write_text(
            '{"focus":{"axis":"x","pct":80}}', encoding="utf-8")
        (self.avatars / "creator-12.face.json").write_text(
            '{"focus":{"axis":"y","pct":20}}', encoding="utf-8")
        row = next(row for row in rm_web.q_index(self.contract, "performers")["items"]
                   if row["k"] == "Canonical Creator")
        self.assertEqual(row["k"], "Canonical Creator")
        self.assertEqual(row["entity_kind"], "creator")
        self.assertEqual(row["avatar_focus"], {"axis": "y", "pct": 20})

    def test_the_tag_index_has_no_face_to_frame(self):
        tags = rm_web.q_index(self.contract, "tags")["items"]
        self.assertEqual([row["k"] for row in tags], ["足交"])
        self.assertNotIn("avatar_focus", tags[0])


class DuplicateDetectionTests(unittest.TestCase):
    """同番号不等于重复：合集、分卷和混入的广告都会共用一个 code。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = str(fresh_ledger(self.tmp.name))
        self.con = sqlite3.connect(self.db_path)
        # 后进先出：这条要在临时目录清理之后注册，才会先关连接。Windows 上没关
        # 的 SQLite 句柄会让 TemporaryDirectory 删不掉文件。
        self.addCleanup(self.con.close)
        self.next_id = 1
        self.contract = rm_web.WebContract(Path(self.db_path))

    def add(self, code, duration, size, drive="B", name=None, hash_=None):
        asset_id = self.next_id
        self.next_id += 1
        self.con.execute(
            "INSERT INTO asset(id,location,path,name,medium,code,size,duration,"
            "hash,first_seen) VALUES(?,'local',?,?,'video',?,?,?,?,'2026-08-19')",
            # 默认名不能以数字结尾：`ABW-232-1.mp4` 会被正确识别成分卷标记，
            # 于是每个夹具文件都成了「不同部分」。用 ripN 避开这个约定。
            (asset_id, rf"{drive}:\{asset_id}.mp4", name or f"{code} rip{asset_id}.mp4",
             code, size, duration, hash_))
        self.con.commit()
        return asset_id

    def groups(self):
        return rm_web.q_duplicates(self.contract, {"limit": "50"})

    def test_same_duration_rips_are_a_duplicate_cluster(self):
        self.add("ABW-232", 7200, 5_000_000_000)
        self.add("ABW-232", 7200, 3_000_000_000)
        result = self.groups()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["groups"][0]["count"], 2)

    def test_shared_filename_rips_with_missing_duration_are_candidates(self):
        """同名且已知时长在容差内的副本进入重复候选，缺测量的一份保留。"""
        kept = self.add("SSIS-001", 7200, 9_000_000_000, name="SSIS-001.mp4")
        drifted = self.add("SSIS-001", 7380, 4_000_000_000, drive="R", name="SSIS-001.mp4")
        unprobed = self.add("SSIS-001", None, 3_000_000_000, drive="A", name="SSIS-001.mp4")
        result = self.groups()
        self.assertEqual(result["total"], 1)
        self.assertEqual({item["id"] for item in result["groups"][0]["files"]},
                         {kept, drifted, unprobed})

    def test_shared_filename_full_and_half_length_files_stay_independent(self):
        self.add('YRH-110', 14663, 2_443_829_006, name='YRH-110.mp4')
        self.add('YRH-110', 7597, 5_886_476_309, name='YRH-110.mp4')
        self.add('YRH-110', None, 3_000_000_000, name='YRH-110.mp4')
        result = self.groups()
        self.assertEqual(result['total'], 0)

    def test_a_shared_name_does_not_drag_in_the_other_parts(self):
        """并的是同名那几份，别的簇不跟着合。"""
        self.add("PPT-019", 6000, 5_000_000_000, name="PPT-019-A.mp4")
        self.add("PPT-019", 6000, 4_000_000_000, drive="R", name="PPT-019-A.mp4")
        self.add("PPT-019", 9000, 5_000_000_000, name="PPT-019-B.mp4")
        self.add("PPT-019", 9000, 4_000_000_000, drive="R", name="PPT-019-B.mp4")
        result = self.groups()
        self.assertEqual(result["total"], 2)
        self.assertTrue(all(group["count"] == 2 for group in result["groups"]))

    def test_multi_part_releases_never_collapse_into_one_cluster(self):
        # PPT-018 实测：109.2/175.2/196.4 分各两份。按番号只留最大会删掉两个部分。
        for minutes in (109.2, 175.2, 196.4):
            self.add("PPT-018", minutes * 60, int(minutes * 5e7))
            self.add("PPT-018", minutes * 60, int(minutes * 4e7))
        result = self.groups()
        self.assertEqual(result["total"], 3, "三个部分必须各成一簇")
        self.assertTrue(all(g["count"] == 2 for g in result["groups"]))

    def test_a_collection_is_not_a_pile_of_duplicates(self):
        # FC2-PPV-3312576 一个番号 19 个文件，是 19 部不同作品。
        for index in range(6):
            self.add("FC2-PPV-3312576", 3000 + index * 600, 1_000_000_000)
        self.assertEqual(self.groups()["total"], 0)

    def test_advertisements_sharing_the_code_do_not_touch_the_real_work(self):
        real = self.add("BAZX-302", 11994, 9_000_000_000)
        self.add("BAZX-302", 78, 20_000_000, name="妹妹直播.mp4")
        self.add("BAZX-302", 80, 21_000_000, name="N房间的精彩直播.mp4")
        result = self.groups()
        flagged = {f["id"] for g in result["groups"] for f in g["files"]}
        self.assertNotIn(real, flagged, "199 分钟的正片不该被判成重复")

    def test_large_short_same_code_copy_is_a_duplicate_not_junk(self):
        full = self.add("FC2-PPV-1110408", 50 * 60, 4_000_000_000,
                        name="FC2-PPV-1110408.mp4")
        short = self.add("FC2-PPV-1110408", 3 * 60, 488 * 1024**2,
                         name="FC2-PPV-1110408-2-4K修复.mp4")

        result = self.groups()

        self.assertEqual(result["total"], 1)
        group = result["groups"][0]
        self.assertEqual({item["id"] for item in group["files"]}, {full, short})
        self.assertEqual(group["evidence"], "same_code_short_copy")
        self.assertEqual(group["reclaimable"], 488 * 1024**2)

    def test_largest_and_longest_are_marked_separately(self):
        # 时长差必须落在容差内才是同一簇；体积最大与时长最长可以是不同文件，
        # 实测 MEYD-692 里最长的那个反而是码率更低的 nyap2p 版本。
        big = self.add("MEYD-692", 7000, 9_000_000_000)
        long_ = self.add("MEYD-692", 7020, 8_000_000_000)
        files = {f["id"]: f for f in self.groups()["groups"][0]["files"]}
        self.assertTrue(files[big]["is_largest"])
        self.assertFalse(files[big]["is_longest"])
        self.assertTrue(files[long_]["is_longest"])

    def test_identical_needs_a_hash_on_every_file(self):
        self.add("DASD-839", 7200, 5_000_000_000, hash_="abc")
        self.add("DASD-839", 7200, 5_000_000_000, hash_="abc")
        self.assertTrue(self.groups()["groups"][0]["identical"])

    def test_a_missing_hash_downgrades_the_claim_to_inference(self):
        self.add("DASD-839", 7200, 5_000_000_000, hash_="abc")
        self.add("DASD-839", 7200, 5_000_000_000, hash_=None)
        self.assertFalse(self.groups()["groups"][0]["identical"])

    def test_cross_drive_duplicates_are_flagged(self):
        self.add("SSIS-057", 7200, 5_000_000_000, drive="A")
        self.add("SSIS-057", 7200, 4_000_000_000, drive="B")
        group = self.groups()["groups"][0]
        self.assertTrue(group["cross_drive"])
        self.assertEqual(group["drives"], ["A:", "B:"])

    def test_duplicate_rows_keep_the_full_path_for_review(self):
        self.add("SSIS-057", 7200, 5_000_000_000, drive="A")
        self.add("SSIS-057", 7200, 4_000_000_000, drive="B")
        files = {row["drive"]: row for row in self.groups()["groups"][0]["files"]}
        self.assertEqual(files["A:"]["path"], r"A:\1.mp4")
        self.assertEqual(files["B:"]["path"], r"B:\2.mp4")

    def test_reclaimable_keeps_the_largest_of_each_cluster(self):
        self.add("WAAA-415", 7200, 5_000_000_000)
        self.add("WAAA-415", 7200, 3_000_000_000)
        self.assertEqual(self.groups()["reclaimable"], 3_000_000_000)

    def test_unknown_duration_is_never_merged_into_a_cluster(self):
        self.add("HMN-145", 0, 5_000_000_000)
        self.add("HMN-145", 0, 5_000_000_000)
        self.assertEqual(self.groups()["total"], 0, "没有时长证据就不判重复")

    def test_recycle_bin_items_are_excluded(self):
        first = self.add("TRE-080", 7200, 5_000_000_000)
        self.add("TRE-080", 7200, 4_000_000_000)
        self.con.execute("UPDATE asset SET disposal='trash' WHERE id=?", (first,))
        self.con.commit()
        self.assertEqual(self.groups()["total"], 0)

    def test_long_films_do_not_merge_across_a_percentage_tolerance(self):
        # HRV-041 实测：237 分与 239 分是两个部分。3% 容差在 4 小时片子上等于
        # ±7 分钟，会把它们并成一簇，「留最大」就删掉了一个部分。
        self.add("HRV-041", 237 * 60, 10_000_000_000, name="HRV-041-1.mp4")
        self.add("HRV-041", 239 * 60, 10_100_000_000, name="HRV-041-2.mp4")
        self.add("HRV-041", 237 * 60, 4_400_000_000, name="HD_hrv-041-1.mp4")
        self.add("HRV-041", 239 * 60, 4_460_000_000, name="HD_hrv-041-2.mp4")
        result = self.groups()
        self.assertEqual(result["total"], 2, "两个部分必须各成一簇")
        for group in result["groups"]:
            durations = {round(f["duration"]) for f in group["files"]}
            self.assertEqual(len(durations), 1, "同簇内时长必须一致")

    def test_distinct_part_markers_never_share_a_cluster(self):
        # FCDSS-021 的 -1/-2/-3 时长只差 12 秒，却是三个部分。
        self.add("FCDSS-021", 14496, 10_140_000_000, name="FCDSS-021-1.mp4")
        self.add("FCDSS-021", 14500, 10_140_000_000, name="FCDSS-021-2.mp4")
        self.add("FCDSS-021", 14508, 10_130_000_000, name="FCDSS-021-3.mp4")
        self.assertEqual(self.groups()["total"], 0, "分卷标记不同不算重复")

    def test_unmarked_rips_of_the_same_part_still_cluster(self):
        # 没有分卷标记的同时长文件仍应判为重复：站点前缀不是分卷标记。
        self.add("MEYD-692", 9192, 6_580_000_000, name="hhd800.com@MEYD-692.mp4")
        self.add("MEYD-692", 9192, 6_570_000_000, name="MEYD-692.mp4")
        self.add("MEYD-692", 9192, 6_570_000_000, name="meyd-692.mp4")
        self.assertEqual(self.groups()["groups"][0]["count"], 3)

    def test_part_marker_extraction(self):
        self.assertEqual(rm_web.part_marker("HRV-041-1.mp4"), "1")
        self.assertEqual(rm_web.part_marker("HD_hrv-041-2.mp4"), "2")
        self.assertEqual(rm_web.part_marker("Movie-pt3.mkv"), "3")
        self.assertEqual(rm_web.part_marker("Movie-disk4.mp4"), "4")
        self.assertEqual(rm_web.part_marker("MEYD-692.mp4"), "")
        self.assertEqual(rm_web.part_marker("hhd800.com@MEYD-692.mp4"), "")

    def test_multipart_group_rejects_duplicate_or_gapped_markers(self):
        duplicate = [
            {"id": 1, "name": "OJIE-325-A.mp4"},
            {"id": 2, "name": "OJIE-325-A.mp4"},
            {"id": 3, "name": "OJIE-325-B.mp4"},
        ]
        gapped = [
            {"id": 1, "name": "FCDSS-021-1.mp4"},
            {"id": 3, "name": "FCDSS-021-3.mp4"},
        ]
        self.assertEqual(rm_web.ordered_multipart_items(duplicate), [])
        self.assertEqual(rm_web.ordered_multipart_items(gapped), [])

    def test_fc2_collection_with_missing_parts_keeps_explicit_numbers(self):
        numbers = [21, 1, 8, 10, 13, 15]
        items = [{"id": n, "code": "FC2-PPV-3312576",
                  "name": f"FC2-PPV-3312576-{n}.mp4"} for n in numbers]
        self.assertEqual([row["id"] for row in rm_web.ordered_multipart_items(items)],
                         sorted(numbers))
        for extra in (
            {"id": 31, "code": "FC2-PPV-3312576", "name": "FC2-PPV-3312576-01.mp4"},
            {"id": 32, "code": "FC2-PPV-3312576", "name": "FC2-PPV-3312576.mp4"},
            {"id": 33, "code": "FC2-PPV-3312576", "name": "FC2-PPV-1234567-3.mp4"},
        ):
            with self.subTest(extra=extra):
                self.assertEqual(rm_web.ordered_multipart_items([*items, extra]), [])

    def test_fc2_collection_api_keeps_all_available_parts_in_order(self):
        numbers = [n for n in range(1, 22) if n not in (9, 14)]
        for n in reversed(numbers):
            self.add("FC2-PPV-3312576", 1800 + n * 100, 1_000_000_000 + n,
                     name=f"FC2-PPV-3312576-{n}.mp4")
        listed = rm_web.q_items(self.contract, {"q": "FC2-PPV-3312576", "limit": "30"})["items"]
        self.assertEqual(len(listed), 19)
        self.assertEqual({row["part_group"]["count"] for row in listed}, {19})
        self.assertTrue(all("edition_group" not in row for row in listed))
        parts = rm_web.q_parts(self.contract, {"id": str(listed[0]["id"])})
        self.assertEqual([row["part_label"] for row in parts["items"]], list(map(str, numbers)))

    def trash(self, asset_id):
        self.con.execute("UPDATE asset SET disposal='trash' WHERE id=?", (asset_id,))
        self.con.commit()

    def test_volumes_that_fold_into_one_card_count_as_one_work(self):
        """作品计数与卡片折叠读同一份分卷判据，不限 FC2。"""
        self.add("DVDMS-243", 7200, 5_000_000_000, name="DVDMS-243cd1.mp4")
        self.add("DVDMS-243", 7000, 5_000_000_000, name="DVDMS-243cd2.mp4")
        result = rm_web.q_items(self.contract, {"q": "DVDMS-243", "limit": "10"})
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["work_total"], 1)
        self.assertEqual({row["part_group"]["count"] for row in result["items"]}, {2})

    def test_a_lone_live_volume_is_a_single_card_and_queue(self):
        """FC2 合集第 3 卷进了回收站，在库的只剩第 1 卷：照单个文件摆，队列不收回收站那卷。"""
        first = self.add("FC2-PPV-3312576", 1800, 1_000_000_000, name="FC2-PPV-3312576-1.mp4")
        third = self.add("FC2-PPV-3312576", 2000, 1_000_000_000, name="FC2-PPV-3312576-3.mp4")
        self.trash(third)
        result = rm_web.q_items(self.contract, {"q": "FC2-PPV-3312576", "limit": "10"})
        self.assertEqual([row["id"] for row in result["items"]], [first])
        self.assertEqual(result["work_total"], 1)
        self.assertNotIn("part_group", result["items"][0])
        self.assertEqual(rm_web.q_parts(self.contract, {"id": str(first)}),
                         {"error": "multipart release not found"})

    def test_a_duplicate_volume_in_the_recycle_bin_keeps_the_set_unfolded_everywhere(self):
        """回收站里还有一份重复的第 2 卷时卷号有歧义，计数和卡片都按两个文件算。"""
        self.add("HRV-041", 237 * 60, 10_000_000_000, name="HRV-041-1.mp4")
        self.add("HRV-041", 239 * 60, 10_100_000_000, name="HRV-041-2.mp4")
        self.trash(self.add("HRV-041", 239 * 60, 4_460_000_000, name="HD_hrv-041-2.mp4"))
        result = rm_web.q_items(self.contract, {"q": "HRV-041", "limit": "10"})
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["work_total"], 2)
        self.assertTrue(all("part_group" not in row for row in result["items"]))

    def test_attached_volume_markers_and_circled_titles_share_one_queue(self):
        cases = [
            ("DVDMS-243", ["DVDMS-243cd1.mp4", "DVDMS-243cd2.mp4"], ["1", "2"]),
            ("IPZ-561", ["IPZ-561A.1080p.mkv", "IPZ-561B.1080p.mkv"], ["A", "B"]),
            ("ABP-601", ["ABP-601A-C.mp4", "ABP-601B-C.mp4"], ["A", "B"]),
            ("FC2-PPV-1261799", ["FC-1261799A.mp4", "FC-1261799B.mp4"], ["A", "B"]),
            ("092415-001", ["1pon-092415-001-fhd1_(new).mp4",
                            "1pon-092415-001-fhd2_(new).mp4"], ["1", "2"]),
            ("FC2-PPV-3189414", ["FC2-PPV-3189414-0.mp4", "FC2-PPV-3189414-1.mp4"], ["0", "1"]),
            ("FC2-PPV-3075582", ["FC2-PPV-3075582-①本編.mp4",
                                 "FC2-PPV-3075582-③特典.mp4"], ["1", "3"]),
            ("FC2-PPV-4550808", ["fc4550808_2.mp4", "fc4550808_3.mp4",
                                 "fc4550808_sp1.mp4", "fc4550808_sp3.mp4"],
                                 ["2", "3", "特典 1", "特典 3"]),
            ("FC2-PPV-4592208", ["fc4592208.mp4", "fc4592208_sp.mp4"], ["本篇", "特典 1"]),
            ("FC2-PPV-2498047", ["FC2-PPV-2498047【标题】.mp4",
                                 "FC2-PPV-2498047【标题】(特典).mp4"], ["本篇", "特典 1"]),
        ]
        for code, names, labels in cases:
            with self.subTest(code=code):
                for name in reversed(names):
                    self.add(code, 1800, 1_000_000, name=name)
                listed = rm_web.q_items(self.contract, {"q": code, "limit": "30"})["items"]
                self.assertEqual({row["part_group"]["count"] for row in listed}, {len(names)})
                self.assertTrue(all("edition_group" not in row for row in listed))
                parts = rm_web.q_parts(self.contract, {"id": str(listed[0]["id"])})
                self.assertEqual([row["name"] for row in parts["items"]], names)
                self.assertEqual([row["part_label"] for row in parts["items"]], labels)

    def test_zero_number_requires_an_explicit_fc2_collection(self):
        for code in ("ABC-123", "FC2-PPV-3189414"):
            for names in (["ABC-123-0.mp4", "ABC-123-1.mp4"],
                          ["FC2-PPV-3189414-00.mp4", "FC2-PPV-3189414-1.mp4"],
                          ["FC2-PPV-3189414-0.mp4", "FC2-PPV-3189414.mp4"]):
                with self.subTest(code=code, names=names):
                    rows = [{"id": n, "code": code, "name": name} for n, name in enumerate(names)]
                    self.assertEqual(rm_web.ordered_multipart_items(rows), [])

    def test_bonus_collection_rejects_duplicate_and_mixed_complete_files(self):
        for tails in (["", "", "_sp"], ["", "_1", "_sp"], ["_sp1", "_gift1"],
                      ["", "_sp", "_1080p"], ["_sp", "_sp01"]):
            with self.subTest(tails=tails):
                rows = [{"id": n, "code": "FC2-PPV-4550808", "name": f"fc4550808{tail}.mp4"}
                        for n, tail in enumerate(tails)]
                self.assertEqual(rm_web.ordered_multipart_items(rows), [])

    def test_parts_group_when_the_edition_suffix_sits_after_the_volume_number(self):
        """卷号后面挂着版次或修复标记时，剥掉组内共有尾缀仍认得出这是一部片的几卷。

        卷号后可带 `_prob3`、`-4K修复`、`_8k` 等组内共有后缀。
        """
        uncensored = [
            {"id": 31442, "name": "PPT-018-1-uncensored.mp4", "duration": 11785.8},
            {"id": 31443, "name": "PPT-018-2-uncensored.mp4", "duration": 10511.7},
            {"id": 31444, "name": "PPT-018-3-uncensored.mp4", "duration": 6551.6},
        ]
        self.assertEqual(
            [item["id"] for item in rm_web.ordered_multipart_items(uncensored)],
            [31442, 31443, 31444])
        restored = [
            {"id": 2, "name": "FC2-PPV-1083921-2-4K修复.mp4"},
            {"id": 1, "name": "FC2-PPV-1083921-1-4K修复.mp4"},
        ]
        self.assertEqual(
            [item["id"] for item in rm_web.ordered_multipart_items(restored)], [1, 2])

    def test_volume_numbers_written_with_a_leading_zero_group(self):
        """`_01`、`_02` 是库里常见的卷号写法，和 `-1`、`-2` 一样要成组。

        实测 MIAD-573：两卷 `MIAD573_01.wmv`（4566 秒）与 `_02`（2577 秒）各占一张卡，
        详情页把另一卷摆进「接着看」当成同创作者的另一部作品。卷号连续这条判据照旧——
        同库的 FC2-PPV-4620098 有 01／02／04／05，缺 03，仍然不成组。
        """
        padded = [
            {"id": 28401, "name": "MIAD573_02.wmv", "duration": 2576.7},
            {"id": 28399, "name": "MIAD573_01.wmv", "duration": 4566.0},
        ]
        self.assertEqual(
            [item["id"] for item in rm_web.ordered_multipart_items(padded)], [28399, 28401])
        gapped = [
            {"id": 1, "name": "FC2-PPV-4620098-01.mp4", "duration": 4569.9},
            {"id": 2, "name": "FC2-PPV-4620098-02.mp4", "duration": 3988.2},
            {"id": 4, "name": "FC2-PPV-4620098-04.mp4", "duration": 299.9},
            {"id": 5, "name": "FC2-PPV-4620098-05.mp4", "duration": 177.7},
        ]
        self.assertEqual(rm_web.ordered_multipart_items(gapped), [])

    def test_two_resolutions_of_one_film_are_not_a_multipart_release(self):
        """共有尾部不从分隔符起头就剥不动，两个清晰度因此仍是两条独立记录。

        实测 040221-001（carib）：`-1080p` 与 `-720p` 的公共尾部只有 `0p`。按字符去剥
        会把 `1080p` 拆成 `10` 加 `80p`，同一段画面就成了「第 10 卷和第 8 卷」。
        `ABF-234` 与 `ABF-234-UN` 同理，它们归版次组。
        """
        resolutions = [
            {"id": 1, "name": "040221-001-carib-1080p.mp4"},
            {"id": 2, "name": "040221-001-carib-720p.mp4"},
        ]
        self.assertEqual(rm_web.ordered_multipart_items(resolutions), [])
        editions = [
            {"id": 1, "name": "ABF-234.mp4"},
            {"id": 2, "name": "ABF-234-UN.mp4"},
        ]
        self.assertEqual(rm_web.ordered_multipart_items(editions), [])

    def test_a_bare_first_part_joins_numbered_parts_when_durations_agree(self):
        # TRE-080 实测：首卷 `TRE-080.mp4` 没有标记，后两卷是 -2/-3，时长 9163/11255/8530 秒。
        items = [
            {"id": 3, "name": "TRE-080-3.mp4", "duration": 8530},
            {"id": 1, "name": "TRE-080.mp4", "duration": 9163},
            {"id": 2, "name": "TRE-080-2.mp4", "duration": 11255},
        ]
        self.assertEqual([item["id"] for item in rm_web.ordered_multipart_items(items)], [1, 2, 3])

    def test_a_bare_file_only_counts_as_part_one_when_it_cannot_be_the_whole_film(self):
        parts = [
            {"id": 2, "name": "TRE-080-2.mp4", "duration": 11255},
            {"id": 3, "name": "TRE-080-3.mp4", "duration": 8530},
        ]
        cases = {
            "完整版": [{"id": 1, "name": "TRE-080.mp4", "duration": 28900}, *parts],
            "广告片": [{"id": 1, "name": "TRE-080.mp4", "duration": 78}, *parts],
            "缺时长": [{"id": 1, "name": "TRE-080.mp4", "duration": None}, *parts],
            "两个裸名": [{"id": 1, "name": "TRE-080.mp4", "duration": 9163},
                         {"id": 4, "name": "tre-080.mp4", "duration": 9163}, *parts],
            "标记不从 2 起": [{"id": 1, "name": "TRE-080.mp4", "duration": 9163}, parts[1]],
            "字母卷缺 A": [{"id": 1, "name": "OJIE-325.mp4", "duration": 14300},
                           {"id": 2, "name": "OJIE-325-B.mp4", "duration": 14349},
                           {"id": 3, "name": "OJIE-325-C.mp4", "duration": 14281}],
        }
        for label, items in cases.items():
            with self.subTest(label):
                self.assertEqual(rm_web.ordered_multipart_items(items), [])

    def test_a_bare_first_part_gets_one_card_and_a_numbered_queue_not_an_edition_group(self):
        connection = sqlite3.connect(self.db_path)
        connection.executemany(
            "INSERT INTO asset(id,location,path,name,medium,size,studio,code,duration,"
            "width,height,first_seen) VALUES(?,'115',?,?,'video',?,'Prestige','TRE-080',?,"
            "1920,1080,'2026-08-13')",
            [
                (6, r"B:\TRE-080\TRE-080-2.mp4", "TRE-080-2.mp4", 8_432_234_092, 11255.5),
                (7, r"B:\TRE-080\TRE-080-3.mp4", "TRE-080-3.mp4", 6_403_519_569, 8530.4),
                (8, r"B:\TRE-080\TRE-080.mp4", "TRE-080.mp4", 7_105_839_805, 9163.0),
            ],
        )
        connection.commit(); connection.close()

        listed = rm_web.q_items(
            self.contract, {"q": "TRE-080", "sort": "new", "limit": "10"},
        )["items"]
        self.assertEqual(len(listed), 3, "API 仍保留三个真实资产")
        self.assertEqual({row["part_group"]["count"] for row in listed}, {3})
        self.assertEqual(listed[0]["part_group"]["seed_id"], 8, "裸名文件是第一卷")
        self.assertTrue(all("edition_group" not in row for row in listed),
                        "同一版次的分卷不是多版本")
        self.assertEqual(rm_web._edition_groups(self.contract, ["TRE-080"]), {})

        parts = rm_web.q_parts(self.contract, {"id": "6"})
        self.assertEqual([item["name"] for item in parts["items"]],
                         ["TRE-080.mp4", "TRE-080-2.mp4", "TRE-080-3.mp4"])
        self.assertEqual([item["part_label"] for item in parts["items"]], ["1", "2", "3"])

    def test_volumes_that_carry_an_edition_suffix_still_collapse_into_one_card(self):
        """PPT-018 的三卷把版次写在卷号后面，仍然折成一张卡和一条 1／2／3 的队列。

        实测三份 `PPT-018-N-uncensored.mp4`：11785／10511／6551 秒。它们此前各占一张卡，
        标题、女优、厂牌完全一样，在目录里只有时长能区分是哪一卷。
        """
        connection = sqlite3.connect(self.db_path)
        connection.executemany(
            "INSERT INTO asset(id,location,path,name,medium,size,studio,code,duration,"
            "width,height,first_seen) VALUES(?,'115',?,?,'video',?,'Prestige','PPT-018',?,"
            "1920,1080,'2026-08-13')",
            [
                (11, r"B:\PPT-018\PPT-018-2-uncensored.mp4", "PPT-018-2-uncensored.mp4",
                 8_913_529_270, 10511.7),
                (12, r"B:\PPT-018\PPT-018-1-uncensored.mp4", "PPT-018-1-uncensored.mp4",
                 10_015_751_441, 11785.8),
                (13, r"B:\PPT-018\PPT-018-3-uncensored.mp4", "PPT-018-3-uncensored.mp4",
                 5_560_132_404, 6551.6),
            ],
        )
        connection.commit(); connection.close()

        listed = rm_web.q_items(
            self.contract, {"q": "PPT-018", "sort": "new", "limit": "10"},
        )["items"]
        self.assertEqual({row["part_group"]["count"] for row in listed}, {3})
        self.assertEqual(listed[0]["part_group"]["seed_id"], 12, "卷号 1 那份是第一卷")
        self.assertTrue(all("edition_group" not in row for row in listed),
                        "同一版次的分卷不是多版本")

        parts = rm_web.q_parts(self.contract, {"id": "11"})
        self.assertEqual([item["name"] for item in parts["items"]],
                         ["PPT-018-1-uncensored.mp4", "PPT-018-2-uncensored.mp4",
                          "PPT-018-3-uncensored.mp4"])
        self.assertEqual([item["part_label"] for item in parts["items"]], ["1", "2", "3"])


class TopsRotationTests(unittest.TestCase):
    """顶部三层要跟着「换一批」真的换人，否则刷新后上面纹丝不动。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = str(fresh_ledger(self.tmp.name))
        con = sqlite3.connect(self.db_path)
        self.addCleanup(con.close)
        # 候选池要大于展示位，抽样才有意义（TOPS_POOL_FACTOR 倍）。
        for index in range(40):
            con.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
                        "VALUES(?,'performer',?,?,'2026-01-01','2026-01-01')",
                        (100 + index, f"P{index:02d}", f"p{index:02d}"))
            for copy in range(40 - index):          # 让数量各不相同，排序稳定
                asset_id = index * 100 + copy
                con.execute("INSERT INTO asset(id,location,path,name,medium,first_seen) "
                            "VALUES(?,'local',?,?,'video','2026-08-19')",
                            (asset_id, f"/x/{asset_id}.mp4", f"{asset_id}.mp4"))
                con.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
                            "VALUES(?,?,'performer','test',1.0)", (asset_id, 100 + index))
        con.commit()
        self.contract = rm_web.WebContract(Path(self.db_path))

    def names(self, **kwargs):
        return [row["k"] for row in rm_web.q_tops(self.contract, 8, **kwargs)["performers"]]

    def test_without_a_seed_it_stays_the_strict_top_list(self):
        self.assertEqual(self.names(), self.names())
        self.assertEqual(self.names()[0], "P00", "无种子时仍按数量取前 N")

    def test_a_seed_changes_who_appears(self):
        self.assertNotEqual(self.names(seed="111"), self.names(seed="222"))

    def test_the_same_seed_is_repeatable(self):
        # 翻页和重绘之间不能抖动，否则同一批里会看到两套人。
        self.assertEqual(self.names(seed="111"), self.names(seed="111"))

    def test_a_seeded_batch_is_still_full_and_ordered_by_count(self):
        rows = rm_web.q_tops(self.contract, 8, seed="111")["performers"]
        self.assertEqual(len(rows), 8)
        counts = [row["n"] for row in rows]
        self.assertEqual(counts, sorted(counts, reverse=True), "抽完仍按数量排序")

    def test_a_small_pool_degrades_to_everything_available(self):
        rows = rm_web.q_tops(self.contract, 100, seed="111")["performers"]
        self.assertEqual(len(rows), 40, "候选不足时不能抽空，也不能报错")

    def test_the_next_page_picks_up_where_the_first_batch_left_off(self):
        """横滚续接靠页号往后数：续上来的人不能跟第一页重，也不能跳过谁。

        第一页那几个是从数量前 `n * TOPS_POOL_FACTOR` 里抽的，抽剩下的那些不再露面：
        续页要是回头补它们，就得先知道抽走了谁，而那份名单只存在于上一次请求里。
        所以那一段整个归第一页，续页从它的末尾往后按数量数下去——代价是抽剩的几个这一
        轮不出现，换一批时它们照样有机会。
        """
        first = self.names(seed="111")
        second = self.names(seed="111", page=1)
        self.assertEqual(len(second), 8)
        self.assertEqual(set(first) & set(second), set(), "续页不能给出第一页已经有的人")
        # 第一页独占数量前 8*4 名，第二页就是第 33 名往后的 8 个。
        strict = [row["k"] for row in rm_web.q_tops(self.contract, 40)["performers"]]
        self.assertEqual(second, strict[8 * web_catalog.TOPS_POOL_FACTOR:][:8])

    def test_pages_run_out_instead_of_repeating_the_tail(self):
        # 数到库里没人了就回空数组：前端据此停下，回一份重复的会让那一排无限续。
        self.assertEqual(self.names(seed="111", page=9), [])

    def test_without_a_seed_the_next_page_follows_the_strict_list(self):
        # 无种子时第一页就是严格前 N，没有候选池那一段，续页紧接着第 N 名往后。
        strict = [row["k"] for row in rm_web.q_tops(self.contract, 40)["performers"]]
        self.assertEqual(self.names(page=1), strict[8:16])

    def test_the_page_number_is_part_of_the_cache_key(self):
        # 页号不进键的话，续页拿回的是第一页那份缓存，那一排会一直续出同一批人。
        def page(number):
            args = {"n": "8", "seed": "111", "page": str(number)}
            return [row["k"] for row
                    in rm_web.dispatch_api_get(self.contract, "/api/tops", args)["performers"]]

        self.assertEqual(page(0), page(0), "同一页仍走缓存")
        self.assertEqual(set(page(0)) & set(page(1)), set())


class PhotoSetTests(unittest.TestCase):
    """图集就是目录：账本没有图集实体，同一目录下的图片本来就是一份图集。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = str(fresh_ledger(self.tmp.name))
        self.con = sqlite3.connect(self.db_path)
        self.addCleanup(self.con.close)
        self.con.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
            "VALUES(1,'creator','桃子','桃子','2026-01-01','2026-01-01')")
        self.con.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence) "
            "VALUES(1,'taozi','taozi','stash:performer',0.9)")
        self.next_id = 1
        self.contract = rm_web.WebContract(Path(self.db_path))

    def add(self, path, medium="image", location="pikpak", size=1000,
            linked=True, disposal=None):
        asset_id = self.next_id
        self.next_id += 1
        self.con.execute(
            "INSERT INTO asset(id,location,path,name,medium,size,disposal) VALUES(?,?,?,?,?,?,?)",
            (asset_id, location, path, path.rsplit("\\", 1)[-1], medium, size, disposal))
        if linked:
            self.con.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
                "VALUES(?,1,'creator','legacy:asset',1.0)", (asset_id,))
        self.con.commit()
        return asset_id

    def sets(self, name="桃子"):
        return rm_web.q_entity_photos(self.contract, {"kind": "creator", "name": name})

    def test_photos_group_by_directory_and_id_is_the_first_image(self):
        first = self.add(r"A:\创作者\桃子\夏日写真\P\001.jpg")
        self.add(r"A:\创作者\桃子\夏日写真\P\002.jpg")
        other = self.add(r"A:\创作者\桃子\冬日\001.jpg")
        self.add(r"A:\创作者\桃子\正片.mp4", medium="video")
        result = self.sets()
        self.assertEqual(result["total"], 3)
        self.assertEqual([(item["id"], item["n"]) for item in result["sets"]],
                         [(first, 2), (other, 1)], "按张数排序，id 取目录里第一张")

    def test_generic_leaf_directories_borrow_the_parent_name(self):
        self.add(r"A:\创作者\桃子\夏日写真\P\001.jpg")
        self.add(r"B:\创作者\桃子\冬日\图片\001.jpg", location="115")
        titles = {item["title"] for item in self.sets()["sets"]}
        self.assertEqual(titles, {"夏日写真", "冬日"}, "`P`、`图片` 这类目录名没有信息量")

    def test_alias_resolves_to_the_same_entity_and_trash_is_excluded(self):
        self.add(r"A:\创作者\桃子\夏日写真\001.jpg")
        self.add(r"A:\创作者\桃子\夏日写真\002.jpg", disposal="trash")
        self.assertEqual(self.sets("taozi")["total"], 1)

    def test_unlinked_images_do_not_appear_on_the_profile(self):
        self.add(r"A:\别人\写真\001.jpg", linked=False)
        self.assertEqual(self.sets()["sets"], [])

    def test_a_set_lists_its_own_directory_in_file_name_order(self):
        second = self.add(r"A:\创作者\桃子\夏日写真\002.jpg")
        first = self.add(r"A:\创作者\桃子\夏日写真\001.jpg")
        self.add(r"A:\创作者\桃子\冬日\001.jpg")
        result = rm_web.q_photo_set(self.contract, {"id": first, "limit": "1"})
        self.assertEqual(result["title"], "夏日写真")
        self.assertEqual(result["total"], 2)
        self.assertEqual([item["id"] for item in result["items"]], [first])
        self.assertTrue(result["has_more"])
        rest = rm_web.q_photo_set(self.contract, {"id": first, "limit": "1", "offset": "1"})
        self.assertEqual([item["id"] for item in rest["items"]], [second])
        self.assertFalse(rest["has_more"])

    def test_the_same_directory_on_two_sources_stays_two_sets(self):
        pikpak = self.add(r"A:\创作者\桃子\夏日写真\001.jpg")
        self.con.execute(
            "INSERT INTO asset(id,location,path,name,medium,size) "
            r"VALUES(99,'115','A:\创作者\桃子\夏日写真\001.jpg','001.jpg','image',10)")
        self.con.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
            "VALUES(99,1,'creator','legacy:asset',1.0)")
        self.con.commit()
        self.assertEqual(rm_web.q_photo_set(self.contract, {"id": pikpak})["total"], 1)
        self.assertEqual({item["location"] for item in self.sets()["sets"]}, {"pikpak", "115"})

    def test_a_video_id_is_not_a_photo_set(self):
        video = self.add(r"A:\创作者\桃子\正片.mp4", medium="video")
        self.assertEqual(rm_web.q_photo_set(self.contract, {"id": video}),
                         {"error": "not found"})
        self.assertEqual(rm_web.q_photo_set(self.contract, {"id": "x"}),
                         {"error": "invalid id"})

    def test_a_seed_reshuffles_the_photos_and_later_pages_keep_its_order(self):
        ids = [self.add(rf"A:\创作者\桃子\夏日写真\{n:03d}.jpg") for n in range(1, 9)]
        plain = self.sets()
        self.assertEqual([item["id"] for item in plain["items"]], ids, "不带种子按文件名排")
        self.assertEqual(plain["seed"], "")
        args = {"kind": "creator", "name": "桃子", "seed": "40000", "limit": "3"}
        pages = [rm_web.q_entity_photos(self.contract, {**args, "offset": str(offset)})
                 for offset in (0, 3, 6)]
        shuffled = [item["id"] for page in pages for item in page["items"]]
        self.assertEqual(sorted(shuffled), ids, "三页拼起来不重不漏")
        self.assertEqual(shuffled, sorted(ids, key=lambda i: ((i * 40000) % 99991, i)))
        self.assertNotEqual(shuffled, ids)
        self.assertEqual(pages[0]["seed"], "40000", "回话带回种子，翻页照它接着要")

    def test_a_set_takes_the_same_seed(self):
        ids = [self.add(rf"A:\创作者\桃子\夏日写真\{n:03d}.jpg") for n in range(1, 6)]
        result = rm_web.q_photo_set(self.contract, {"id": ids[0], "seed": "40000"})
        self.assertEqual([item["id"] for item in result["items"]],
                         sorted(ids, key=lambda i: ((i * 40000) % 99991, i)))
        self.assertEqual(result["seed"], "40000")
        self.assertEqual(rm_web.q_photo_set(self.contract, {"id": ids[0]})["seed"], "")




class CatalogVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = fresh_ledger(Path(self.tmp.name).resolve())
        self.contract = rm_web.WebContract(self.db)

    def add(self, aid, name, duration, height=1080, code='FC2-PPV-1110408'):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                'INSERT INTO asset(id,location,path,name,medium,code,size,duration,width,height,first_seen) '
                "VALUES(?,'115',?,?,'video',?,1000000,?,?,?,'2026-01-01')",
                (aid, f'B:\\{aid}\\{name}', name, code, duration, height * 16 // 9, height))

    def test_dmm_content_id_filename_has_release_identity(self):
        from peach.catalog_versions import filename_matches_code
        self.assertTrue(filename_matches_code({'code': 'LXVS-039', 'name': '118lxvs00039.mp4'}))
        self.assertFalse(filename_matches_code({'code': 'LXVS-039', 'name': '118lxvs00038.mp4'}))

    def test_scraped_generic_filename_uses_exact_number_directory(self):
        from peach.catalog_versions import filename_matches_code
        row = {'code': 'n0780', 'name': 'Tokyo-Hot.mp4', 'path': r'B:\东京热\n0780\Tokyo-Hot.mp4',
               'catalog_title': '发行标题', 'field_owners': '{"catalog_title":"auto:javdb"}'}
        self.assertTrue(filename_matches_code(row))
        self.assertFalse(filename_matches_code({**row, 'path': r'B:\东京热\n0781\Tokyo-Hot.mp4'}))
        self.assertFalse(filename_matches_code({**row, 'field_owners': None}))

    def test_explicit_filename_conflict_takes_priority_over_directory(self):
        from peach.catalog_versions import filename_matches_code
        row = {'code': '122614-947', 'name': '122614_001-1pon-whole1_hd.avi',
               'path': r'B:\一本道\122614-947\122614_001-1pon-whole1_hd.avi',
               'catalog_title': '发行标题', 'field_owners': '{"catalog_title":"auto:1pondo"}'}
        self.assertFalse(filename_matches_code(row))

    def test_catalog_query_keeps_scraped_identity_for_generic_video_names(self):
        self.add(1, 'Tokyo-Hot.mp4', 5896, 720, code='n0780')
        self.add(2, 'n0780.mkv', 5786, 1080, code='n0780')
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute('UPDATE asset SET path=?,catalog_title=?,field_owners=? WHERE id=1',
                               (r'B:\东京热\n0780\Tokyo-Hot.mp4', '发行标题', '{"catalog_title":"auto:javdb"}'))
        result = rm_web.q_editions(self.contract, {'id':1})
        self.assertEqual(result['count'], 2)
        self.assertEqual({row['id'] for row in result['items']}, {1,2})

    def test_related_keeps_one_version_card_when_a_later_part_ranks_first(self):
        self.add(1, 'FC-1110408.mp4', 3213)
        self.add(2, 'FC2-PPV-1110408-1-4K修复.mp4', 3013, 2160)
        self.add(3, 'FC2-PPV-1110408-2-4K修复.mp4', 200, 2160)
        rows = {row['id']: row for row in web_catalog._catalog_group_rows(self.contract)}
        candidates = [rows[3], rows[1], rows[2]]
        kept = web_catalog._one_card_per_release(self.contract, 'ABW-999', candidates)
        self.assertEqual([row['id'] for row in kept], [3])
        self.assertEqual(web_catalog._one_card_per_release(
            self.contract, 'FC2-PPV-1110408', candidates), [])

    def test_full_release_and_repaired_parts_share_two_versions(self):
        self.add(1, 'FC-1110408.mp4', 3213.802667)
        self.add(2, 'FC2-PPV-1110408-1-4K修复.mp4', 3013.577233, 2160)
        self.add(3, 'FC2-PPV-1110408-2-4K修复.mp4', 199.8997, 2160)
        result = q_items(self.contract, {'q': '1110408'})
        self.assertEqual(result['work_total'], 1)
        self.assertEqual({row['edition_group']['count'] for row in result['items']}, {2})
        versions = q_editions(self.contract, {'id': 3})
        self.assertEqual(versions['count'], 2)
        self.assertEqual([row['id'] for row in versions['items']], [1, 2, 3])
        self.assertEqual([row.get('part_label') for row in versions['items']], [None, '1', '2'])
        self.assertEqual([row['id'] for row in q_parts(self.contract, {'id': 3})['items']], [2, 3])

    def test_two_multipart_encodes_keep_independent_part_queues(self):
        for aid, name, duration, height in (
            (1, 'FC-1110408_1.mp4', 3013, 1080), (2, 'FC-1110408_2.mp4', 200, 1080),
            (3, 'FC2-PPV-1110408-1-4K修复.mp4', 3013, 2160),
            (4, 'FC2-PPV-1110408-2-4K修复.mp4', 200, 2160),
        ):
            self.add(aid, name, duration, height)
        result = q_items(self.contract, {'q': '1110408'})
        self.assertEqual(result['work_total'], 1)
        self.assertEqual({row['edition_group']['count'] for row in result['items']}, {2})
        self.assertEqual([row['id'] for row in q_parts(self.contract, {'id': 2})['items']], [1, 2])
        self.assertEqual([row['id'] for row in q_parts(self.contract, {'id': 4})['items']], [3, 4])

    def test_full_file_and_same_quality_parts_have_two_playable_forms(self):
        self.add(1, 'FC-1110408.mp4', 3213)
        self.add(2, 'FC2-PPV-1110408-CD1.mp4', 3013)
        self.add(3, 'FC2-PPV-1110408-CD2.mp4', 200)
        self.assertEqual(q_editions(self.contract, {'id': 1})['count'], 2)

    def test_resolution_versions_use_normalized_codes(self):
        self.add(1, 'ABW-251.mp4', 12044, 720, 'ABW-251')
        self.add(2, 'abw251.mp4', 12044, 1080, 'abw251')
        result = q_items(self.contract, {'q': '251'})
        self.assertEqual({row['edition_group']['count'] for row in result['items']}, {2})
        self.assertEqual(q_editions(self.contract, {'id': 1})['count'], 2)

    def test_clips_and_unknown_specifications_stay_unfolded(self):
        self.add(1, 'FC2-PPV-1110408.mp4', 3000, 1080)
        self.add(2, 'FC2-PPV-1110408.mp4', 200, 720)
        self.assertTrue(all('edition_group' not in row for row in q_items(self.contract, {})['items']))

    def test_matching_copies_are_not_presented_as_resolution_versions(self):
        self.add(1, 'FC2-PPV-1110408.mp4', 3000)
        self.add(2, 'FC2-PPV-1110408.MP4', 3000)
        self.assertEqual(q_editions(self.contract, {'id': 1}), {'error': 'edition group not found'})

    def test_duplicate_full_files_share_one_version_without_part_labels(self):
        self.add(1, 'FC2-PPV-1110408.mp4', 3213)
        self.add(2, 'FC2-PPV-1110408.MP4', 3213)
        self.add(3, 'FC2-PPV-1110408-CD1.mp4', 3013)
        self.add(4, 'FC2-PPV-1110408-CD2.mp4', 200)
        versions = q_editions(self.contract, {'id': 1})
        self.assertEqual(versions['count'], 2)
        self.assertEqual({row['version_id'] for row in versions['items']}, {1, 3})
        self.assertTrue(all('part_label' not in row for row in versions['items'][:2]))

    def test_missing_fc2_codes_are_projected_without_ledger_writes(self):
        self.add(1, 'FC2-PPV-1110408-1.mp4', 3000, code=None)
        self.add(2, 'FC2-PPV-1110408-2.mp4', 3000, code=None)
        result = q_items(self.contract, {'q': '1110408'})
        self.assertEqual(result['work_total'], 1)
        self.assertEqual({row['part_group']['count'] for row in result['items']}, {2})
        self.assertEqual([row['id'] for row in q_parts(self.contract, {'id': 2})['items']], [1, 2])
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute('SELECT code FROM asset ORDER BY id').fetchall(), [(None,), (None,)])


if __name__ == "__main__":
    unittest.main()
