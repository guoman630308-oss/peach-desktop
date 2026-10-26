"""补厂牌官网与标识后继：证据确定的直接落，不确定的不写，写下的能整批撤回（ADR-0052）。

全程临时账本、临时标识目录；试地址与取图都桩掉，不联网。
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from peach import studio_followup, studio_icons
from peach.repository import LedgerDatabase
from support.ledger import fresh_ledger

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-09-24T00:00:00.000Z"


def load_revert():
    spec = importlib.util.spec_from_file_location(
        "revert_auto_landing_under_test", ROOT / "scripts" / "revert_auto_landing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def square_png(size: int = 256) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (size, size), (200, 40, 90)).save(buffer, format="PNG")
    return buffer.getvalue()


def site_row(verdict: str, url: str = "https://newlabel.jp/") -> dict:
    return {"verdict": verdict, "final_url": url, "candidate_url": url,
            "note": "标题自述厂牌名", "title": "NEWLABEL 公式サイト", "sha256": "abc"}


class Faces:
    """人像闸替身：`found` 为真时每张都判出人脸，`unavailable` 非空时闸不生效。"""

    def __init__(self, found: bool = False, unavailable: str = ""):
        self.found, self.unavailable, self.rejected = found, unavailable, 0

    def __call__(self, _payload):
        return None if self.unavailable or not self.found else (0, 0, 10, 10)


class StudioFollowupCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.db = fresh_ledger(self.root)
        self.database = LedgerDatabase(self.db)
        self.logos = self.root / "generated" / "logos"
        self.logos.mkdir(parents=True)
        self.busted = []
        self.contract = SimpleNamespace(
            database=self.database, logo_root=self.logos,
            candidate_root=self.root / "generated",
            cache_bust=lambda: self.busted.append(1))

    def entity(self, kind: str, name: str) -> int:
        with self.database.write_transaction(notify=False) as connection:
            cursor = connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES(?,?,?,?,?)", (kind, name, name.casefold(), STAMP, STAMP))
        return int(cursor.lastrowid)

    def links(self, entity_id: int) -> list[dict]:
        with self.database.read_connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT link_kind,label,url,metadata_json FROM entity_link WHERE entity_id=?",
                (entity_id,)).fetchall()]

    def run_followup(self, entity_id: int, handle=None) -> dict:
        return studio_followup.run(self.contract, studio_followup.followup_key(entity_id),
                                   handle or SimpleNamespace(run_id=77, progress=lambda **_: None))

    def harvested(self, candidate: Path) -> list[dict]:
        payload = candidate.read_bytes()
        return [{"safe": "NEWLABEL", "variant": studio_icons.ICON, "verdict": studio_icons.OK,
                 "candidate": str(candidate), "sha256": hashlib.sha256(payload).hexdigest(),
                 "url": "https://newlabel.jp/favicon.png", "link_kind": "official",
                 "evidence": "官网声明的图标"}]

    def land(self, faces: Faces) -> dict:
        candidate = self.root / "candidate.png"
        candidate.write_bytes(square_png())
        entries = [{"entity_id": "1", "studio": "NEWLABEL", "link_kind": "official",
                    "url": "https://newlabel.jp/"}]
        with mock.patch.object(studio_icons, "harvest",
                               return_value=self.harvested(candidate)):
            return studio_icons.land_one("NEWLABEL", entries, self.logos, self.root / "cache",
                                         fetch=None, faces=faces,
                                         source=studio_followup.SOURCE, batch="b1")


class PlanTests(StudioFollowupCase):
    def test_the_key_round_trips(self):
        self.assertEqual(studio_followup.parse_key(studio_followup.followup_key(12)), 12)
        with self.assertRaises(ValueError):
            studio_followup.parse_key("entity-avatar:studio:12")

    def test_only_new_studios_without_any_mark_are_planned(self):
        old = self.entity("studio", "旧厂牌")
        fresh = self.entity("studio", "NEWLABEL")
        self.entity("performer", "新人")
        dressed = self.entity("studio", "已有标识")
        (self.logos / f"{studio_icons.safe_name('已有标识')}.icon.img").write_bytes(b"png")
        with self.database.read_connection() as connection:
            found = studio_followup.plan(connection, self.logos, since_entity_id=old)
        self.assertEqual([item.key for item in found], [studio_followup.followup_key(fresh)])
        self.assertNotIn(str(dressed), found[0].key)
        self.assertEqual(found[0].task_key, studio_followup.TASK_KEY)
        self.assertTrue(found[0].label.endswith("NEWLABEL"))

    def test_stock_skips_a_studio_once_tried_until_it_gains_a_link(self):
        from peach.followups import Attempts, attempts_root

        studio = self.entity("studio", "OLDLABEL")
        with self.database.write_transaction(notify=False) as connection:
            connection.execute("INSERT INTO asset(id,location,path,name,medium,code,size)"
                               " VALUES(1,'R:','R:\\media\\a.mp4','a.mp4','video','OLD-1',1)")
            connection.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source)"
                               " VALUES(1,?,'studio','test')", (studio,))
        attempts = Attempts(attempts_root(self.contract.candidate_root))

        def planned():
            with self.database.read_connection() as connection:
                return [item.key for item in
                        studio_followup.stock(connection, self.logos, attempts, limit=10)]

        key = studio_followup.followup_key(studio)
        self.assertEqual(planned(), [key])
        with mock.patch.object(studio_followup.studio_sites, "discover",
                               return_value=(site_row("weak"), 0.0)):
            self.run_followup(studio)
        self.assertEqual(planned(), [])
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,is_sensitive,"
                "metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,0,'{}',?,?)",
                (studio, "official", "官方网站", "https://old.jp/", "old.jp", STAMP, STAMP))
        self.assertEqual(planned(), [key])

    def test_stock_studios_are_not_crowded_out_by_avatarless_performers(self):
        """没头像的女优再多，存量厂牌也有自己那一份名额。"""
        from peach import (avatar_followup, library_processing, performer_alias_followup,
                           performer_profile_followup, task_runs)

        studio = self.entity("studio", "OLDLABEL")
        people = [self.entity("performer", f"白石まり{index:02d}") for index in range(12)]
        with self.database.write_transaction(notify=False) as connection:
            for index, entity_id in enumerate([studio, *people], start=1):
                connection.execute(
                    "INSERT INTO asset(id,location,path,name,medium,code,size)"
                    " VALUES(?,'R:',?,?,'video',?,1)",
                    (index, f"R:\\media\\{index}.mp4", f"{index}.mp4", f"OLD-{index}"))
                connection.execute(
                    "INSERT INTO asset_entity(asset_id,entity_id,role,source) VALUES(?,?,?,'test')",
                    (index, entity_id, "studio" if entity_id == studio else "performer"))
        config = SimpleNamespace(directory=lambda _name: self.contract.candidate_root)
        # 一轮 6 条，补别名、补资料各留 1 条，余下 4 条是厂牌和头像争的那一段。
        with mock.patch.object(task_runs, "MAX_FOLLOWUPS", 6), \
                mock.patch.object(performer_alias_followup, "STOCK_SHARE", 1), \
                mock.patch.object(performer_profile_followup, "STOCK_SHARE", 1):
            found = library_processing._entity_followups(self.database, config, max(people))
        tasks = [item["task_key"] for item in found]
        self.assertIn(studio_followup.followup_key(studio), [item["key"] for item in found])
        self.assertIn(avatar_followup.TASK_KEY, tasks)
        self.assertLessEqual(len(found), 6)


class SiteTests(StudioFollowupCase):
    def test_an_ok_site_is_linked_with_its_source_and_batch(self):
        entity_id = self.entity("studio", "NEWLABEL")
        with mock.patch.object(studio_followup.studio_sites, "discover",
                               return_value=(site_row("ok"), 0.0)), \
                mock.patch.object(studio_followup.studio_icons, "land_one",
                                  return_value={"outcome": "未取得"}) as land:
            summary = self.run_followup(entity_id)
        self.assertEqual(summary["site"]["outcome"], "已登记")
        [link] = self.links(entity_id)
        self.assertEqual((link["link_kind"], link["label"], link["url"]),
                         ("official", "官方网站", "https://newlabel.jp/"))
        metadata = json.loads(link["metadata_json"])
        self.assertEqual(metadata["source"], studio_followup.SOURCE)
        self.assertEqual(metadata["batch"], f"{studio_followup.SOURCE}@77")
        self.assertEqual(metadata["verdict"], "ok")
        # 登记下的官网就是取图那一趟的起点。
        entries = land.call_args.args[1]
        self.assertEqual([entry["url"] for entry in entries], ["https://newlabel.jp/"])

    def test_a_weak_site_writes_nothing_and_fetches_no_mark(self):
        entity_id = self.entity("studio", "NEWLABEL")
        with mock.patch.object(studio_followup.studio_sites, "discover",
                               return_value=(site_row("weak"), 0.0)), \
                mock.patch.object(studio_followup.studio_icons, "land_one") as land:
            summary = self.run_followup(entity_id)
        self.assertEqual(summary["outcome"], "官网待人确认")
        self.assertEqual(self.links(entity_id), [])
        land.assert_not_called()

    def test_a_studio_that_already_has_a_link_skips_discovery(self):
        entity_id = self.entity("studio", "NEWLABEL")
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,is_sensitive,"
                "metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,0,'{}',?,?)",
                (entity_id, "official", "官方网站", "https://newlabel.jp/", "newlabel.jp",
                 STAMP, STAMP))
        with mock.patch.object(studio_followup.studio_sites, "discover") as discover, \
                mock.patch.object(studio_followup.studio_icons, "land_one",
                                  return_value={"outcome": "未取得"}):
            self.run_followup(entity_id)
        discover.assert_not_called()
        self.assertEqual(len(self.links(entity_id)), 1)

    def test_a_studio_with_a_mark_on_disk_is_a_no_op(self):
        entity_id = self.entity("studio", "NEWLABEL")
        (self.logos / "NEWLABEL.img").write_bytes(b"png")
        with mock.patch.object(studio_followup.studio_sites, "discover") as discover:
            summary = self.run_followup(entity_id)
        self.assertEqual(summary["outcome"], "已有标识")
        discover.assert_not_called()

    def test_a_vanished_studio_is_not_a_failure(self):
        self.assertEqual(self.run_followup(999), {"outcome": "档案已不存在"})


class MarkTests(StudioFollowupCase):
    def test_a_clean_square_mark_is_installed_with_its_provenance(self):
        landed = self.land(Faces())
        self.assertEqual(landed["outcome"], "已装上")
        self.assertIn("NEWLABEL.icon.img", landed["installed"])
        record = json.loads((self.logos / "NEWLABEL.icon.img.provenance.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual((record["source"], record["batch"]), (studio_followup.SOURCE, "b1"))

    def test_a_mark_with_a_face_is_not_installed(self):
        landed = self.land(Faces(found=True))
        self.assertEqual(landed["verdicts"], [studio_icons.PORTRAIT])
        self.assertEqual(list(self.logos.iterdir()), [])

    def test_nothing_is_installed_while_the_face_gate_is_down(self):
        landed = self.land(Faces(unavailable="取不到人脸模型"))
        self.assertIn("人像闸未生效", landed["outcome"])
        self.assertEqual(list(self.logos.iterdir()), [])


class RevertTests(StudioFollowupCase):
    def test_a_batch_is_reverted_links_and_files_together(self):
        entity_id = self.entity("studio", "NEWLABEL")
        with mock.patch.object(studio_followup.studio_sites, "discover",
                               return_value=(site_row("ok"), 0.0)), \
                mock.patch.object(studio_followup.studio_icons, "land_one",
                                  return_value={"outcome": "未取得"}):
            self.run_followup(entity_id)
        self.land(Faces())
        # 人装的那一张来源不同，撤回不碰它。
        (self.logos / "OTHER.img").write_bytes(b"png")
        (self.logos / "OTHER.img.provenance.json").write_text(
            json.dumps({"source": "studio icon harvest"}), encoding="utf-8")
        revert = load_revert()
        base = ["--db", str(self.db), "--logo-root", str(self.logos)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(revert.main(base), 0)
        self.assertEqual(len(self.links(entity_id)), 1)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(revert.main(
                [*base, "--apply", "--backup", str(self.root / "backup.db")]), 0)
        self.assertEqual(self.links(entity_id), [])
        self.assertEqual(sorted(path.name for path in self.logos.iterdir()),
                         ["OTHER.img", "OTHER.img.provenance.json"])
        self.assertTrue((self.root / "backup.db").exists())


if __name__ == "__main__":
    unittest.main()
