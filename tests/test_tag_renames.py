"""退役标签名改成规范名：账本侧的判定与写入。"""
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from peach import tag_renames
from peach.catalog_rules import RETIRED_TAGS

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rename_retired_tags.py"
_spec = importlib.util.spec_from_file_location("rename_retired_tags", SCRIPT)
_script = importlib.util.module_from_spec(_spec)
sys.modules["rename_retired_tags"] = _script
_spec.loader.exec_module(_script)

SCHEMA = """
CREATE TABLE asset(id INTEGER PRIMARY KEY, location TEXT, path TEXT, name TEXT);
CREATE TABLE profile(id INTEGER PRIMARY KEY, settings_json TEXT DEFAULT '{}', updated_at TEXT);
CREATE TABLE asset_tag(
  asset_id INTEGER, tag TEXT, confidence REAL DEFAULT 1.0, source TEXT,
  UNIQUE(asset_id,tag));
CREATE TABLE entity(
  id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT, normalized_name TEXT,
  metadata_json TEXT DEFAULT '{}', created_at TEXT, updated_at TEXT,
  UNIQUE(kind,normalized_name));
CREATE TABLE entity_alias(
  entity_id INTEGER, alias TEXT, normalized_alias TEXT, source TEXT,
  confidence REAL DEFAULT 1.0, PRIMARY KEY(entity_id,normalized_alias,source));
CREATE TABLE asset_entity(
  asset_id INTEGER, entity_id INTEGER, role TEXT, source TEXT, confidence REAL DEFAULT 1.0,
  metadata_json TEXT DEFAULT '{}', first_seen_at TEXT, last_seen_at TEXT,
  UNIQUE(asset_id,entity_id,role,source));
CREATE TABLE entity_external_ref(
  entity_id INTEGER, provider TEXT, external_kind TEXT, external_id TEXT,
  metadata_json TEXT DEFAULT '{}', last_synced_at TEXT,
  PRIMARY KEY(provider,external_kind,external_id),
  UNIQUE(entity_id,provider,external_kind));
CREATE TABLE entity_link(
  id INTEGER PRIMARY KEY, entity_id INTEGER, link_kind TEXT, label TEXT, url TEXT,
  hostname TEXT, is_sensitive INTEGER DEFAULT 0, metadata_json TEXT DEFAULT '{}',
  created_at TEXT, updated_at TEXT, UNIQUE(entity_id,url));
CREATE TABLE entity_search_term(
  entity_id INTEGER, term TEXT, purpose TEXT, source TEXT, created_at TEXT,
  PRIMARY KEY(entity_id,term,purpose));
CREATE TABLE entity_membership(
  member_id INTEGER PRIMARY KEY, agency_id INTEGER, source TEXT,
  confidence REAL DEFAULT 1.0, checked_at TEXT);
CREATE TABLE label_maker(
  label_id INTEGER PRIMARY KEY, maker_id INTEGER, source TEXT,
  confidence REAL DEFAULT 1.0, checked_at TEXT);
CREATE TABLE performer_profile(entity_id INTEGER PRIMARY KEY, source TEXT);
CREATE TABLE follow_source(id INTEGER PRIMARY KEY, entity_id INTEGER);
CREATE TABLE feed_source(id INTEGER PRIMARY KEY, entity_id INTEGER);
CREATE TABLE feed_discovery_entity(
  discovery_id INTEGER, entity_id INTEGER, PRIMARY KEY(discovery_id,entity_id));
CREATE TABLE entity_redirect(
  old_id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL, source TEXT NOT NULL,
  merged_at TEXT NOT NULL);
"""


class RenameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "ledger.db"
        self.connection = sqlite3.connect(self.db)
        self.addCleanup(self.connection.close)
        self.connection.executescript(SCHEMA)

    def _tag(self, asset_id, tag, source="stash"):
        self.connection.execute("INSERT OR IGNORE INTO asset(id,location,path) VALUES(?,'local',?)",
                                (asset_id, f"R:\\media\\{asset_id}.mp4"))
        self.connection.execute("INSERT INTO asset_tag(asset_id,tag,source) VALUES(?,?,?)",
                                (asset_id, tag, source))

    def _entity(self, entity_id, name):
        self.connection.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name) VALUES(?,'tag',?,?)",
            (entity_id, name, name.casefold()))

    def _tags_of(self, asset_id):
        return sorted(row[0] for row in self.connection.execute(
            "SELECT tag FROM asset_tag WHERE asset_id=?", (asset_id,)))

    def test_every_retired_name_is_a_name_the_ledger_could_actually_hold(self):
        """名单里出现的两个名字都得是标签，改名脚本才是可重复执行的。"""
        self.assertNotEqual(RETIRED_TAGS, {})
        self.assertEqual(sorted(set(RETIRED_TAGS) & set(RETIRED_TAGS.values())), [])

    def test_a_retired_name_becomes_the_canonical_one(self):
        self._tag(1, "POV第一视角")
        rows = tag_renames.collect(self.connection)
        counts = tag_renames.apply_rows(self.connection, rows)
        self.assertEqual(self._tags_of(1), ["主观视角"])
        self.assertEqual(counts["tags"], 1)
        self.assertEqual(counts["dropped"], 0)

    def test_an_asset_that_already_has_both_names_keeps_one_row(self):
        """`UNIQUE(asset_id, tag)` 挡住这次改写，旧的那条就只能删。"""
        self._tag(2, "POV第一视角")
        self._tag(2, "主观视角", source="name")
        counts = tag_renames.apply_rows(self.connection, tag_renames.collect(self.connection))
        self.assertEqual(self._tags_of(2), ["主观视角"])
        self.assertEqual(counts["dropped"], 1)

    def test_what_the_source_itself_said_is_left_alone(self):
        """`pixiv_tag` 记的是作者打了什么词，改它等于事后修改来源的原话。"""
        self._tag(3, "足控", source="pixiv_tag")
        rows = {row["old"]: row for row in tag_renames.collect(self.connection)}
        self.assertEqual(rows["足控"]["raw_kept"], 1)
        self.assertEqual(rows["足控"]["assets"], 0)
        tag_renames.apply_rows(self.connection, list(rows.values()))
        self.assertEqual(self._tags_of(3), ["足控"])

    def test_the_tag_entity_follows_the_name(self):
        self._entity(10, "足底足指")
        tag_renames.apply_rows(self.connection, tag_renames.collect(self.connection))
        self.assertEqual(
            self.connection.execute("SELECT canonical_name FROM entity WHERE id=10").fetchone()[0],
            "恋足")

    def test_two_entities_for_one_tag_merge_and_the_old_name_stays_searchable(self):
        self._entity(11, "足控")
        self._entity(12, "恋足")
        self.connection.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source) VALUES(1,11,'tag','stash')")
        counts = tag_renames.apply_rows(self.connection, tag_renames.collect(self.connection))
        self.assertEqual(counts["entities"], 1)
        self.assertIsNone(self.connection.execute("SELECT id FROM entity WHERE id=11").fetchone())
        self.assertEqual(
            self.connection.execute(
                "SELECT entity_id FROM asset_entity WHERE asset_id=1").fetchone()[0], 12)
        self.assertIn("足控", [row[0] for row in self.connection.execute(
            "SELECT alias FROM entity_alias WHERE entity_id=12")])

    def test_the_second_name_landing_on_one_tag_is_reported_as_a_merge(self):
        """`足控` 与 `足底足指` 都归 `恋足`：先改的造出实体，后改的只能合并。"""
        self._entity(13, "足控")
        self._entity(14, "足底足指")
        rows = tag_renames.collect(self.connection)
        verdicts = [row["entity"] for row in rows if row["new"] == "恋足"]
        self.assertEqual(verdicts, ["改名", "合并"])
        tag_renames.apply_rows(self.connection, rows)
        self.assertEqual(
            [row[0] for row in self.connection.execute(
                "SELECT canonical_name FROM entity WHERE kind='tag'")], ["恋足"])

    def test_a_dropped_bucket_leaves_the_ledger_with_its_entity(self):
        self._tag(6, "乳系", source="vision_creator")
        self._tag(6, "美乳", source="vision_creator")
        self._entity(20, "乳系")
        self.connection.execute(
            "INSERT INTO asset_entity(asset_id,entity_id,role,source) VALUES(6,20,'tag','vision_creator')")
        self.connection.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source) VALUES(20,'乳','乳','stash')")
        rows = {row["old"]: row for row in tag_renames.collect(self.connection)}
        self.assertEqual((rows["乳系"]["new"], rows["乳系"]["assets"], rows["乳系"]["entity"]),
                         ("", 1, "删除"))
        counts = tag_renames.apply_rows(self.connection, list(rows.values()))
        self.assertEqual(self._tags_of(6), ["美乳"])
        self.assertEqual((counts["dropped"], counts["entities"]), (1, 1))
        for table in ("entity", "asset_entity", "entity_alias"):
            column = "id" if table == "entity" else "entity_id"
            self.assertIsNone(self.connection.execute(
                f"SELECT 1 FROM {table} WHERE {column}=20").fetchone(), table)

    def test_a_dropped_bucket_the_source_itself_said_is_left_alone(self):
        self._tag(7, "足系", source="pixiv_tag")
        tag_renames.apply_rows(self.connection, tag_renames.collect(self.connection))
        self.assertEqual(self._tags_of(7), ["足系"])

    def test_a_followed_bucket_entity_is_kept_for_a_person_to_look_at(self):
        self._entity(21, "足系")
        self.connection.execute("INSERT INTO follow_source(entity_id) VALUES(21)")
        counts = tag_renames.apply_rows(self.connection, tag_renames.collect(self.connection))
        self.assertEqual(counts["entities"], 0)
        self.assertIsNotNone(self.connection.execute("SELECT 1 FROM entity WHERE id=21").fetchone())

    def test_a_preview_run_writes_the_csv_and_leaves_the_ledger_alone(self):
        self._tag(4, "足控")
        self.connection.commit()
        csv = Path(self.tmp.name) / "rename.csv"
        args = _script.build_parser().parse_args(
            ["--db", str(self.db), "--review-csv", str(csv)])
        self.assertEqual(_script.run(args), 0)
        self.assertIn("足控", csv.read_text(encoding="utf-8"))
        self.assertEqual(self._tags_of(4), ["足控"])

    def test_applying_backs_up_first_and_then_writes(self):
        self._tag(5, "足控")
        self.connection.commit()
        backup = Path(self.tmp.name) / "before.db"
        args = _script.build_parser().parse_args(
            ["--db", str(self.db), "--apply", "--backup", str(backup),
             "--review-csv", str(Path(self.tmp.name) / "rename.csv")])
        self.assertEqual(_script.run(args), 0)
        self.assertEqual(self._tags_of(5), ["恋足"])
        with closing(sqlite3.connect(backup)) as saved:
            self.assertEqual(
                saved.execute("SELECT tag FROM asset_tag WHERE asset_id=5").fetchone()[0],
                "足控", "备份必须是写入之前的状态")

    def test_writing_the_ledger_without_a_backup_is_refused(self):
        args = _script.build_parser().parse_args(["--db", str(self.db), "--apply"])
        with self.assertRaisesRegex(SystemExit, "--backup"):
            _script.run(args)
