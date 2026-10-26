"""新导入演员的资料页别名与官方头像补全。"""
from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from peach.http import HttpResponse
from peach.metadata_performer_profiles import enrich_performer_profiles
from peach.repository import LedgerDatabase
from support.ledger import fresh_ledger


def picture() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (125, 125), "#795548").save(output, format="JPEG")
    return output.getvalue()


class ImportedPerformerProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.db = fresh_ledger(self.root)
        self.database = LedgerDatabase(Path(self.db))
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,code) "
                "VALUES(1,'local','R:\\\\ABW-358.mp4','ABW-358.mp4','video','ABW-358')")
            connection.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
                "VALUES(7,'performer','涼森れむ','涼森れむ','t','t')")
            connection.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
                "VALUES(1,7,'performer','javinizer:local_nfo:performer',0.9)")
            connection.execute(
                "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id) "
                "VALUES(7,'r18dev','performer','1051912')")
            connection.execute(
                "INSERT INTO review_decision(category,item_key,status,note,updated_at) "
                "VALUES('metadata_fields','asset:1:performers','approved',?, 't')",
                (json.dumps({'candidate_key': 'profile'}),))

    @staticmethod
    def group(url="https://pics.dmm.co.jp/mono/actjpgs/suzumori_remu.jpg"):
        return {
            "asset_id": 1, "item_key": "asset:1:performers", "field": "performers",
            "candidates_json": json.dumps([{"candidate_key": "profile",
                "source": "local_nfo", "value": [{
                "name": "涼森れむ", "external_id": "1051912",
                "aliases": ["すずもりれむ", "Remu Suzumori"],
                "thumb_url": url, "profile_source": "r18dev",
            }]}], ensure_ascii=False),
        }

    def test_exact_profile_adds_aliases_and_installs_the_official_avatar(self):
        transport = Mock(return_value=HttpResponse(
            200, {"content-type": "image/jpeg"}, picture(),
            "https://pics.dmm.co.jp/mono/actjpgs/suzumori_remu.jpg"))
        with patch("peach.avatar_provider.FaceProbe") as face:
            face.return_value.return_value = None
            result = enrich_performer_profiles(
                self.database, [self.group()], self.root / "avatars", self.root / "providers",
                transport_factory=lambda: transport)
        self.assertEqual(result, {"aliases": 2, "avatars": 1, "conflicts": 0, "failed": 0})
        with closing(sqlite3.connect(self.db)) as connection:
            aliases = {row[0] for row in connection.execute(
                "SELECT alias FROM entity_alias WHERE entity_id=7")}
        self.assertEqual(aliases, {"すずもりれむ", "Remu Suzumori"})
        self.assertTrue((self.root / "avatars" / "performer-7.img").is_file())
        transport.assert_called_once()

    def test_refreshed_approval_selects_the_confirmed_profile_evidence(self):
        with self.database.write_transaction() as connection:
            connection.execute(
                "UPDATE review_decision SET note=? WHERE category='metadata_fields' AND item_key='asset:1:performers'",
                (json.dumps({'candidate_key': 'earlier-evidence', 'refreshed_candidate_key': 'profile'}),))
        transport = Mock()
        result = enrich_performer_profiles(
            self.database, [self.group(url='')], self.root / 'avatars', self.root / 'providers',
            transport_factory=lambda: transport)
        self.assertEqual(result['aliases'], 2)
        self.assertEqual(result['avatars'], 0)
        transport.assert_not_called()

    def test_a_javdb_actor_id_lands_as_a_performer_external_ref(self):
        """javdb 的演员 id 落进账本，人物页的 JavDB 入口就是靠它拼出来的。

        这一路不带头像，整段一次网都不出；名字没连着这条资产的人一个都不登记。
        """
        group = dict(self.group(), candidates_json=json.dumps([{
            "candidate_key": "profile", "source": "javdb",
            "value": [{"name": "涼森れむ", "external_id": "NPD3", "profile_source": "javdb"},
                      {"name": "別人", "external_id": "ZZZZ", "profile_source": "javdb"}],
        }], ensure_ascii=False))
        transport = Mock()
        result = enrich_performer_profiles(
            self.database, [group], self.root / "avatars", self.root / "providers",
            transport_factory=lambda: transport)
        self.assertEqual(result["avatars"], 0)
        transport.assert_not_called()
        with closing(sqlite3.connect(self.db)) as connection:
            refs = connection.execute(
                "SELECT entity_id,external_id FROM entity_external_ref "
                "WHERE provider='javdb' AND external_kind='performer'").fetchall()
        self.assertEqual(refs, [(7, "NPD3")])

    def test_tampered_avatar_url_is_never_requested_but_aliases_still_land(self):
        transport = Mock()
        result = enrich_performer_profiles(
            self.database, [self.group("http://127.0.0.1/admin")],
            self.root / "avatars", self.root / "providers",
            transport_factory=lambda: transport)
        self.assertEqual(result["aliases"], 2)
        self.assertEqual(result["avatars"], 0)
        transport.assert_not_called()

    def test_a_second_import_of_the_same_profile_adds_nothing(self):
        """别名已经在库里、头像已经在位时，重跑一遍不该再报新增。

        处理任务每跑完一次都走这一步，全库重扫是常事。第二次还报「补了 2 个别名」的话，
        那个数就不再是「这次真的补上了多少」，界面上也看不出什么时候该收手。
        """
        transport = Mock(return_value=HttpResponse(
            200, {"content-type": "image/jpeg"}, picture(),
            "https://pics.dmm.co.jp/mono/actjpgs/suzumori_remu.jpg"))
        with patch("peach.avatar_provider.FaceProbe") as face:
            face.return_value.return_value = None
            enrich_performer_profiles(
                self.database, [self.group()], self.root / "avatars", self.root / "providers",
                transport_factory=lambda: transport)
            again = enrich_performer_profiles(
                self.database, [self.group()], self.root / "avatars", self.root / "providers",
                transport_factory=lambda: transport)
        self.assertEqual(again, {"aliases": 0, "avatars": 0, "conflicts": 0, "failed": 0})
        transport.assert_called_once()

    def test_a_failed_download_is_counted_and_reported_with_its_cause(self):
        """取不到的那张要留下可查的原因，并且按可重试记进问题清单。"""
        def refuse(_request, _timeout, _max_bytes):
            raise OSError("connection reset")

        issues = []
        with self.assertLogs("peach.metadata_performer_profiles", level="WARNING") as captured:
            result = enrich_performer_profiles(
                self.database, [self.group()], self.root / "avatars", self.root / "providers",
                transport_factory=lambda: refuse,
                issue=lambda asset_id, message: issues.append((asset_id, message)))
        self.assertIn("connection reset", "\n".join(captured.output))
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["avatars"], 0)
        self.assertEqual([asset_id for asset_id, _message in issues], [1])
        self.assertIn("头像未取得", issues[0][1])
        self.assertFalse((self.root / "avatars" / "performer-7.img").exists())

    def test_an_external_id_held_by_another_entity_counts_as_a_conflict(self):
        """同一个 r18 编号挂在别的实体上时，这一条交回人工，不两边都认。"""
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
                "VALUES(8,'performer','別人','別人','t','t')")
            connection.execute(
                "UPDATE entity_external_ref SET entity_id=8 WHERE external_id='1051912'")
        transport = Mock()
        result = enrich_performer_profiles(
            self.database, [self.group()], self.root / "avatars", self.root / "providers",
            transport_factory=lambda: transport)
        self.assertEqual(result, {"aliases": 0, "avatars": 0, "conflicts": 1, "failed": 0})
        transport.assert_not_called()

    def test_a_stopped_job_neither_writes_aliases_nor_fetches_avatars(self):
        """停止之后一条都不许再落库、一张都不许再取。"""
        transport = Mock()
        result = enrich_performer_profiles(
            self.database, [self.group()], self.root / "avatars", self.root / "providers",
            transport_factory=lambda: transport, active=lambda: False)
        self.assertEqual(result, {"aliases": 0, "avatars": 0, "conflicts": 0, "failed": 0})
        transport.assert_not_called()
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM entity_alias").fetchone()[0], 0)

    def test_stopping_between_avatars_leaves_the_rest_alone(self):
        """停在第二张之前：第一张已经装上，剩下的既不取也不写。"""
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) "
                "VALUES(9,'performer','八乃つばさ','八乃つばさ','t','t')")
            connection.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence) "
                "VALUES(1,9,'performer','javinizer:local_nfo:performer',0.9)")
        group = json.loads(self.group()["candidates_json"])
        group[0]["value"].append({
            "name": "八乃つばさ", "external_id": "1041234", "aliases": [],
            "thumb_url": "https://pics.dmm.co.jp/mono/actjpgs/hachino_tsubasa.jpg",
            "profile_source": "r18dev"})
        rows = [{"asset_id": 1, "item_key": "asset:1:performers", "field": "performers",
                 "candidates_json": json.dumps(group, ensure_ascii=False)}]
        calls = []

        def once(request, _timeout, _max_bytes):
            calls.append(request)
            return HttpResponse(200, {"content-type": "image/jpeg"}, picture(),
                                "https://pics.dmm.co.jp/mono/actjpgs/suzumori_remu.jpg")

        with patch("peach.avatar_provider.FaceProbe") as face:
            face.return_value.return_value = None
            result = enrich_performer_profiles(
                self.database, rows, self.root / "avatars", self.root / "providers",
                transport_factory=lambda: once,
                active=lambda: len(calls) < 1)
        self.assertEqual(result["avatars"], 1)
        self.assertEqual(len(calls), 1)

    def test_candidate_still_waiting_for_review_cannot_enrich_the_entity(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "DELETE FROM review_decision WHERE item_key='asset:1:performers'")
        transport = Mock()
        result = enrich_performer_profiles(
            self.database, [self.group()], self.root / "avatars", self.root / "providers",
            transport_factory=lambda: transport)
        self.assertEqual(result, {"aliases": 0, "avatars": 0, "conflicts": 0, "failed": 0})
        transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
