"""实体事实种子包：导出只带公开事实且逐字节稳定，导入只给已有实体填空、只换种子自己写过的行，
整批可撤（ADR-0073、ADR-0075）。

全程两本临时账本：一本当导出方，一本当另一台机器。
"""
import contextlib
import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from peach import seed_pack
from peach.entities import normalize_entity_name
from peach.performer_profiles import read_profile, write_profile
from support.ledger import fresh_ledger

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-09-25T00:00:00Z"
VERSION = "2026-09-25"
BATCH = f"auto:seed@{VERSION}"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def entity(connection, kind: str, name: str) -> int:
    connection.execute(
        "INSERT INTO entity(kind,canonical_name,normalized_name,metadata_json,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?)",
        (kind, name, normalize_entity_name(name), json.dumps({"raw_snapshot": r"C:\somewhere\x.json"}),
         STAMP, STAMP))
    return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])


def alias(connection, entity_id: int, name: str, source: str) -> None:
    connection.execute("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
                       " VALUES(?,?,?,?,1.0)", (entity_id, name, normalize_entity_name(name), source))


def ref(connection, entity_id: int, provider: str, external_id: str, metadata: dict | None = None) -> None:
    connection.execute("INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id,"
                       "metadata_json,last_synced_at) VALUES(?,?,'performer',?,?,?)",
                       (entity_id, provider, external_id, json.dumps(metadata or {}), STAMP))


def link(connection, entity_id: int, kind: str, url: str, *, sensitive: int = 0, metadata=None) -> None:
    connection.execute("INSERT INTO entity_link(entity_id,link_kind,label,url,hostname,is_sensitive,"
                       "metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (entity_id, kind, "label", url, "x.com", sensitive, json.dumps(metadata or {}),
                        STAMP, STAMP))


def membership(connection, member_id: int, agency_id: int, source: str) -> None:
    connection.execute("INSERT INTO entity_membership(member_id,agency_id,source,confidence,checked_at)"
                       " VALUES(?,?,?,1.0,?)", (member_id, agency_id, source, STAMP))


def label_maker(connection, label_id: int, maker_id: int, source: str) -> None:
    connection.execute("INSERT INTO label_maker(label_id,maker_id,source,confidence,checked_at)"
                       " VALUES(?,?,?,1.0,?)", (label_id, maker_id, source, STAMP))


PROFILE = {"kana": "あまかわそら", "birth_date": "1998-10-10", "height_cm": 164, "cup": "G",
           "tags": ["巨乳", "美肌"], "raw": {"名前": "天川そら", "サイズ": "T164 / B87"}}


class SeedPackCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="peach-seed-"))
        self.source = connect(fresh_ledger(self.root, "source.db"))
        self.target = connect(fresh_ledger(self.root, "target.db"))
        self.addCleanup(self.source.close)
        self.addCleanup(self.target.close)

    def seeded_source(self) -> dict:
        """导出方账本：一位有全套事实的女优、她的事务所、一家带别名的厂牌、一个归属片商的 label、
        一位只有名字的女优。"""
        con = self.source
        with con:
            sora = entity(con, "performer", "天川そら")
            agency = entity(con, "agency", "STARTUP")
            studio = entity(con, "studio", "S1 NO.1 STYLE")
            maker = entity(con, "studio", "Prestige")
            label = entity(con, "studio", "PRESTIGE PREMIUM")
            entity(con, "performer", "只有名字")
            label_maker(con, label, maker, "review:label-maker-20260923.csv")
            alias(con, sora, "春山心愛", "r18:performer")
            alias(con, sora, "Amakawa Sora", "r18:performer")
            alias(con, sora, "そら", "javdb-actor-page@javdb-20260912")
            alias(con, sora, "二宮そら", "javdb-actor-page@javdb-20260912")
            alias(con, sora, "二宮そら", "kanji-simplification")
            alias(con, sora, "种子写的", BATCH)
            ref(con, sora, "minnano-av", "295275")
            ref(con, sora, "stash", "26")
            ref(con, sora, "javdb", "seedref", {"source": "auto:seed", "batch": "auto:seed@old"})
            link(con, sora, "social", "https://x.com/amakawa_sora_")
            link(con, sora, "official", "https://www.Startup0701.net/")
            link(con, sora, "social", "https://onlyfans.example/sora", sensitive=1)
            link(con, sora, "catalog", "https://www.minnano-av.com/actress295275.html")
            link(con, sora, "social", "https://x.com/seeded", metadata={"source": "auto:seed", "batch": "x"})
            write_profile(con, sora, dict(PROFILE), source="auto:performer-profile@7",
                          source_url="https://www.minnano-av.com/actress295275.html", fetched_at=STAMP)
            membership(con, sora, agency, "minnano-av:所属事務所")
            alias(con, studio, "S1", "javbus:studio-localization")
            link(con, studio, "official", "https://s1s1s1.com/")
        return seed_pack.export_pack(con, version=VERSION)


class ExportTests(SeedPackCase):
    def test_pack_carries_public_facts_and_nothing_local(self):
        pack = self.seeded_source()
        self.assertEqual(pack["version"], VERSION)
        self.assertEqual(pack["counts"], {"entities": 3, "aliases": 3, "refs": 1, "links": 3,
                                          "profiles": 1, "memberships": 1, "makers": 1})
        by_name = {item["name"]: item for item in pack["entities"]}
        self.assertEqual(list(by_name), ["天川そら", "PRESTIGE PREMIUM", "S1 NO.1 STYLE"], "按种类与归一名排序")
        self.assertNotIn("STARTUP", by_name, "事务所自己没有可补的事实，只作为她的归属出现")
        self.assertNotIn("Prestige", by_name, "片商自己没有可补的事实，只作为 label 的归属出现")
        self.assertEqual(by_name["PRESTIGE PREMIUM"]["maker"],
                         {"name": "Prestige", "source": "review:label-maker-20260923.csv"})
        sora = by_name["天川そら"]
        self.assertEqual([(a["alias"], a["source"]) for a in sora["aliases"]],
                         [("二宮そら", "javdb-actor-page@javdb-20260912"), ("春山心愛", "r18:performer")],
                         "按归一写法排序，同一写法只留一个来源；罗马字、短单名与种子自己写的不导")
        self.assertEqual(sora["refs"], [{"provider": "minnano-av", "kind": "performer", "id": "295275"}],
                         "Stash 的行号与种子写的编号都不导")
        self.assertEqual([l["url"] for l in sora["links"]],
                         ["https://www.startup0701.net/", "https://x.com/amakawa_sora_"],
                         "敏感链接、目录页与种子写的链接都不导；主机按导入同一套规则归一，大小写不进包")
        self.assertEqual(sora["profile"]["fields"],
                         {"kana": "あまかわそら", "birth_date": "1998-10-10", "height_cm": 164, "cup": "G",
                          "tags": ["巨乳", "美肌"]}, "资料按列导，raw 残片不导")
        self.assertEqual(sora["profile"]["source"], "auto:performer-profile@7")
        self.assertEqual(sora["agency"], {"name": "STARTUP", "source": "minnano-av:所属事務所"})
        self.assertNotIn("只有名字", by_name, "光一个名字对别的数据库没有用处")
        text = seed_pack.dump(pack)
        self.assertNotIn("raw_snapshot", text)
        self.assertNotIn("somewhere", text)

    def test_two_exports_of_the_same_ledger_are_byte_identical(self):
        first = seed_pack.dump(self.seeded_source())
        second = seed_pack.dump(seed_pack.export_pack(self.source, version=VERSION))
        self.assertEqual(first, second)


class ImportTests(SeedPackCase):
    def seeded_target(self) -> dict[str, int]:
        """另一台机器：账本里她叫「春山心愛」，事务所已登记，厂牌、label 与片商都只有名字；
        包里的另一位 `未登记` 本机没有。"""
        con = self.target
        with con:
            ids = {"her": entity(con, "performer", "春山心愛"), "agency": entity(con, "agency", "STARTUP"),
                   "studio": entity(con, "studio", "S1 NO.1 STYLE"), "other": entity(con, "performer", "别人"),
                   "label": entity(con, "studio", "PRESTIGE PREMIUM"), "maker": entity(con, "studio", "Prestige")}
        return ids

    def test_only_existing_entities_get_their_blanks_filled(self):
        pack = self.seeded_source()
        pack["entities"].append({"kind": "performer", "name": "未登记", "aliases": [], "refs": [],
                                 "links": [{"kind": "social", "label": "X", "url": "https://x.com/nobody"}]})
        ids = self.seeded_target()
        with self.target:
            report = seed_pack.land(self.target, pack)
        self.assertEqual(report, {"version": VERSION, "batch": BATCH, "matched": 3, "unmatched": 1,
                                  "aliases": 3, "refs": 1, "links": 3, "profiles": 1, "memberships": 1,
                                  "makers": 1, "refreshed": 0, "conflicts": [], "duplicates": []})
        her = ids["her"]
        rows = self.target.execute("SELECT alias,source FROM entity_alias WHERE entity_id=? ORDER BY alias",
                                   (her,)).fetchall()
        self.assertEqual([tuple(row) for row in rows], [("二宮そら", BATCH), ("天川そら", BATCH)],
                         "包里的规范名与别名都补成别名，本机规范名不动，来源是批次号")
        self.assertEqual(self.target.execute("SELECT canonical_name FROM entity WHERE id=?", (her,)).fetchone()[0],
                         "春山心愛")
        provider, external_id, metadata = self.target.execute(
            "SELECT provider,external_id,metadata_json FROM entity_external_ref WHERE entity_id=?", (her,)).fetchone()
        self.assertEqual((provider, external_id, json.loads(metadata)),
                         ("minnano-av", "295275", {"source": "auto:seed", "batch": BATCH}))
        links = self.target.execute("SELECT url,metadata_json FROM entity_link WHERE entity_id=? ORDER BY url",
                                    (her,)).fetchall()
        self.assertEqual([row[0] for row in links], ["https://www.startup0701.net/", "https://x.com/amakawa_sora_"])
        self.assertEqual(json.loads(links[0][1]), {"source": "auto:seed", "batch": BATCH})
        profile = read_profile(self.target, her)
        self.assertEqual((profile["kana"], profile["height_cm"], profile["tags"], profile["raw"], profile["source"]),
                         ("あまかわそら", 164, ["巨乳", "美肌"], {}, BATCH))
        self.assertEqual(profile["fetched_at"], STAMP, "取回时间照包里的记，30 天算旧的规则接着管它")
        member = self.target.execute("SELECT agency_id,source FROM entity_membership WHERE member_id=?",
                                     (her,)).fetchone()
        self.assertEqual(tuple(member), (ids["agency"], BATCH))
        studio_alias = self.target.execute("SELECT alias,source FROM entity_alias WHERE entity_id=?",
                                           (ids["studio"],)).fetchone()
        self.assertEqual(tuple(studio_alias), ("S1", BATCH))
        maker = self.target.execute("SELECT maker_id,source FROM label_maker WHERE label_id=?",
                                    (ids["label"],)).fetchone()
        self.assertEqual(tuple(maker), (ids["maker"], BATCH), "label 归哪家片商也补上，来源是批次号")
        self.assertEqual(self.target.execute("SELECT count(*) FROM entity").fetchone()[0], 6, "不造实体")

    def test_seed_never_overwrites_what_the_ledger_already_holds(self):
        pack = self.seeded_source()
        ids = self.seeded_target()
        with self.target:
            write_profile(self.target, ids["her"], {"kana": "本机刮的", "raw": {}},
                          source="auto:performer-profile@99", source_url="https://example/", fetched_at=STAMP)
            membership(self.target, ids["her"], ids["agency"], "review:user")
            link(self.target, ids["her"], "social", "https://X.com/amakawa_sora_")
            alias(self.target, ids["her"], "二宮そら", "user:manual")
            kmp = entity(self.target, "studio", "K M Produce")
            label_maker(self.target, ids["label"], kmp, "review:user")
        with self.target:
            report = seed_pack.land(self.target, pack)
        self.assertEqual((report["profiles"], report["refs"], report["memberships"], report["links"],
                          report["aliases"], report["makers"], report["refreshed"]), (0, 1, 0, 2, 2, 0, 0))
        self.assertEqual(report["conflicts"],
                         [{"kind": "studio", "entity": "PRESTIGE PREMIUM", "field": "maker", "ours": "K M Produce",
                           "source": "review:user", "theirs": "Prestige"}],
                         "人复核过的片商与包里不一致：不动，记进明细等人看")
        self.assertEqual(self.target.execute("SELECT maker_id FROM label_maker WHERE label_id=?",
                                             (ids["label"],)).fetchone()[0], kmp)
        self.assertEqual(self.target.execute("SELECT count(*) FROM entity_link WHERE entity_id=?",
                                             (ids["her"],)).fetchone()[0], 2,
                         "本机那条主机大写的同一地址不再补一条只差大小写的")
        self.assertEqual(read_profile(self.target, ids["her"])["source"], "auto:performer-profile@99",
                         "本机后继刚刮的资料比种子新，不被盖掉")
        self.assertEqual(self.target.execute("SELECT source FROM entity_membership WHERE member_id=?",
                                             (ids["her"],)).fetchone()[0], "review:user")
        rows = self.target.execute("SELECT alias,source FROM entity_alias WHERE entity_id=? ORDER BY alias",
                                   (ids["her"],)).fetchall()
        self.assertEqual([tuple(row) for row in rows], [("二宮そら", "user:manual"), ("天川そら", BATCH)],
                         "已有的写法保留原来源，只补缺的那个")

    def test_a_newer_seed_replaces_what_an_older_seed_wrote(self):
        pack = self.seeded_source()
        ids = self.seeded_target()
        with self.target:
            old_agency = entity(self.target, "agency", "旧事务所")
            old_maker = entity(self.target, "studio", "旧片商")
            membership(self.target, ids["her"], old_agency, "auto:seed@2026-01-01")
            label_maker(self.target, ids["label"], old_maker, "auto:seed@2026-01-01")
            write_profile(self.target, ids["her"], {"kana": "旧种子写的", "raw": {}}, source="auto:seed@2026-01-01",
                          source_url="https://example/", fetched_at="2026-01-01T00:00:00Z")
        with self.target:
            report = seed_pack.land(self.target, pack)
        self.assertEqual((report["memberships"], report["makers"], report["profiles"], report["refreshed"],
                          report["conflicts"]), (0, 0, 0, 3, []), "旧种子写的三行都换成新批次，不算新补也不算冲突")
        self.assertEqual(tuple(self.target.execute("SELECT agency_id,source FROM entity_membership WHERE member_id=?",
                                                   (ids["her"],)).fetchone()), (ids["agency"], BATCH))
        self.assertEqual(tuple(self.target.execute("SELECT maker_id,source FROM label_maker WHERE label_id=?",
                                                   (ids["label"],)).fetchone()), (ids["maker"], BATCH))
        profile = read_profile(self.target, ids["her"])
        self.assertEqual((profile["kana"], profile["source"]), ("あまかわそら", BATCH))
        with self.target:
            again = seed_pack.land(self.target, pack)
        self.assertEqual(seed_pack.written(again), 0, "同一包再跑一遍不再动任何一行")

    def test_a_name_on_one_entity_and_a_number_on_another_matches_nobody(self):
        pack = self.seeded_source()
        ids = self.seeded_target()
        with self.target:
            ref(self.target, ids["other"], "minnano-av", "295275")
        with self.target:
            report = seed_pack.land(self.target, pack)
        self.assertEqual((report["matched"], report["unmatched"], report["duplicates"]),
                         (2, 0, [{"kind": "performer", "name": "天川そら", "entities": ["别人", "春山心愛"]}]),
                         "两家厂牌对上了；她那条认出两位，记进重复身份明细")
        for entity_id in (ids["her"], ids["other"]):
            self.assertEqual(self.target.execute("SELECT count(*) FROM entity_alias WHERE entity_id=?",
                                                 (entity_id,)).fetchone()[0], 0, "名字与编号各指一位，谁都不动")
        owner = self.target.execute("SELECT entity_id FROM entity_external_ref WHERE external_id='295275'").fetchone()
        self.assertEqual(owner[0], ids["other"], "别人已登记的编号不改指")

    def test_two_local_matches_are_left_alone(self):
        pack = self.seeded_source()
        with self.target:
            first = entity(self.target, "performer", "春山心愛")
            second = entity(self.target, "performer", "二宮そら")
        with self.target:
            report = seed_pack.land(self.target, pack)
        self.assertEqual((report["matched"], report["unmatched"], report["duplicates"]),
                         (0, 2, [{"kind": "performer", "name": "天川そら", "entities": ["二宮そら", "春山心愛"]}]))
        for entity_id in (first, second):
            self.assertEqual(self.target.execute("SELECT count(*) FROM entity_alias WHERE entity_id=?",
                                                 (entity_id,)).fetchone()[0], 0, "两位都对得上就谁都不动")

    def test_plan_reports_the_same_counts_without_touching_the_ledger(self):
        pack = self.seeded_source()
        self.seeded_target()
        before = self.target.execute("SELECT count(*) FROM entity_alias").fetchone()[0]
        report = seed_pack.plan(self.target, pack)
        self.assertEqual((report["matched"], report["aliases"], report["profiles"]), (3, 3, 1))
        self.assertEqual(self.target.execute("SELECT count(*) FROM entity_alias").fetchone()[0], before)

    def test_a_seed_batch_is_reverted_across_all_six_tables(self):
        pack = self.seeded_source()
        ids = self.seeded_target()
        with self.target:
            alias(self.target, ids["her"], "人写的", "user:manual")
            seed_pack.land(self.target, pack)
        self.target.close()
        revert = load_script("revert_auto_landing")
        db = self.root / "target.db"
        base = ["--db", str(db), "--source", "auto:seed", "--logo-root", str(self.root / "no-logos")]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(revert.main(base), 0)
        self.assertIn("'归属': 1", out.getvalue())
        self.assertIn("'片商': 1", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(revert.main([*base, "--apply", "--backup", str(self.root / "backup.db")]), 0)
        con = connect(db)
        self.addCleanup(con.close)
        counts = [con.execute(sql).fetchone()[0] for sql in (
            "SELECT count(*) FROM entity_alias", "SELECT count(*) FROM entity_external_ref",
            "SELECT count(*) FROM entity_link", "SELECT count(*) FROM performer_profile",
            "SELECT count(*) FROM entity_membership", "SELECT count(*) FROM label_maker")]
        self.assertEqual(counts, [1, 0, 0, 0, 0, 0], "只剩人写的那条别名")


class ScriptTests(SeedPackCase):
    def test_default_pack_and_export_destination_stay_outside_the_repository(self):
        script = load_script("seed_pack")
        export = script.build_parser().parse_args(["export"])
        self.assertEqual(export.out, seed_pack.DEFAULT_PACK)
        self.assertNotIn(ROOT, export.out.resolve().parents)

    def test_validate_checks_package_without_opening_a_database(self):
        pack = self.root / "package.json"
        pack.write_text(seed_pack.dump(self.seeded_source()), encoding="utf-8")
        script = load_script("seed_pack")
        with mock.patch.object(script, "open_readonly", side_effect=AssertionError("database must remain closed")):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(script.main(["validate", "--pack", str(pack)]), 0)
            self.assertTrue(json.loads(output.getvalue())["valid"])
            pack.write_text('{"format":true}', encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as rejected:
                self.assertEqual(script.main(["validate", "--pack", str(pack)]), 2)
            self.assertFalse(json.loads(rejected.getvalue())["written"])

    def test_export_is_idempotent_and_import_is_dry_run_by_default(self):
        self.seeded_source()
        self.source.close()
        script = load_script("seed_pack")
        out = self.root / "seed" / "entities.json"
        with contextlib.redirect_stdout(io.StringIO()) as first:
            self.assertEqual(script.main(["export", "--db", str(self.root / "source.db"), "--version", VERSION,
                                          "--out", str(out)]), 0)
        with contextlib.redirect_stdout(io.StringIO()) as second:
            script.main(["export", "--db", str(self.root / "source.db"), "--version", VERSION, "--out", str(out)])
        self.assertEqual((json.loads(first.getvalue())["changed"], json.loads(second.getvalue())["changed"]),
                         (True, False))
        self.assertEqual(seed_pack.load(out)["counts"]["entities"], 3)
        with self.target:
            entity(self.target, "performer", "天川そら")
        self.target.close()
        target = self.root / "target.db"
        with contextlib.redirect_stdout(io.StringIO()) as dry:
            self.assertEqual(script.main(["import", "--db", str(target), "--pack", str(out)]), 0)
        self.assertIn("dry-run", dry.getvalue())
        con = connect(target)
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT count(*) FROM entity_alias").fetchone()[0], 0)
        with contextlib.redirect_stdout(io.StringIO()) as applied:
            self.assertEqual(script.main(["import", "--db", str(target), "--pack", str(out), "--apply",
                                          "--backup", str(self.root / "pre-seed.db")]), 0)
        report = json.loads(applied.getvalue())
        self.assertEqual((report["matched"], report["aliases"], report["integrity_check"]), (1, 2, "ok"))
        self.assertTrue((self.root / "pre-seed.db").exists())

    def test_a_changed_export_that_keeps_the_version_is_refused(self):
        """版本与内容绑定，导出内容变化须推进版本。"""
        self.seeded_source()
        script = load_script("seed_pack")
        out, db = self.root / "seed" / "entities.json", str(self.root / "source.db")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(script.main(["export", "--db", db, "--version", VERSION, "--out", str(out)]), 0)
        before = out.read_text(encoding="utf-8")
        sora = self.source.execute("SELECT id FROM entity WHERE canonical_name='天川そら'").fetchone()[0]
        with self.source:
            alias(self.source, sora, "改名后的艺名", "r18:performer")
        self.source.close()
        with contextlib.redirect_stdout(io.StringIO()) as refused:
            self.assertEqual(script.main(["export", "--db", db, "--version", VERSION, "--out", str(out)]), 2)
        report = json.loads(refused.getvalue())
        self.assertEqual((report["version"], report["changed"], report["written"]), (VERSION, True, False))
        self.assertIn("换一个新版本串", report["error"])
        self.assertEqual(out.read_text(encoding="utf-8"), before, "被拒的导出不动文件")
        with contextlib.redirect_stdout(io.StringIO()) as renewed:
            self.assertEqual(script.main(["export", "--db", db, "--version", f"{VERSION}.1", "--out", str(out)]), 0)
        self.assertTrue(json.loads(renewed.getvalue())["changed"])
        self.assertEqual((seed_pack.load(out)["version"], seed_pack.load(out)["counts"]["aliases"]),
                         (f"{VERSION}.1", 4))
        # 文件已是 `.1`，再改内容后用缺省那样的当天日期导出：比现有版本串旧，同样拒绝。
        source = connect(self.root / "source.db")
        with source:
            alias(source, sora, "再改一次", "r18:performer")
        source.close()
        with contextlib.redirect_stdout(io.StringIO()) as older:
            self.assertEqual(script.main(["export", "--db", db, "--version", VERSION, "--out", str(out)]), 2)
        self.assertIn("需要更新的 version 与 revision", json.loads(older.getvalue())["error"])
        self.assertEqual(seed_pack.load(out)["version"], f"{VERSION}.1")


if __name__ == "__main__":
    unittest.main()
