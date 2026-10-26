"""真相字段的字段级归属与乐观并发。

这一组用例盯的是覆盖规则本身：谁能改谁写下的值，以及一次写入里有字段写不得时
剩下的字段还写不写得进去。判据必须在 SQL 那一层成立——调用方自觉检查的版本，
换一个调用点就失效了。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.field_owners import (
    OWNED_FIELDS,
    USER_MANUAL,
    RevisionConflict,
    auto_owner,
    check_revision,
    owner_label,
    owner_of,
    parse_owners,
    review_owner,
    script_owner,
    write_owned_fields,
)
from peach.migrations import upgrade

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class FieldOwnerWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        database = Path(self.tmp.name).resolve() / "ledger.db"
        sqlite3.connect(database).close()
        upgrade(database, MIGRATIONS)
        self.connection = sqlite3.connect(database)
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        for asset_id in (1, 2):
            self.connection.execute(
                "INSERT INTO asset(id,location,path,name,medium) "
                "VALUES(?,'local',?,?,'video')",
                (asset_id, f"R:\\media\\{asset_id}.mp4", f"{asset_id}.mp4"))

    def _row(self, asset_id: int) -> sqlite3.Row:
        return self.connection.execute(
            "SELECT * FROM asset WHERE id=?", (asset_id,)).fetchone()

    def test_a_write_records_the_owner_and_bumps_the_revision(self):
        result = write_owned_fields(
            self.connection, [1], {"release_date": "2026-09-11"}, auto_owner("r18dev"))
        self.assertEqual((result.assets, result.written, result.refused),
                         (1, ("release_date",), ()))
        row = self._row(1)
        self.assertEqual(row["release_date"], "2026-09-11")
        self.assertEqual(parse_owners(row["field_owners"]),
                         {"release_date": "auto:r18dev"})
        self.assertEqual(row["mutation_revision"], 1)

    def test_automatic_writers_cannot_touch_what_the_user_decided(self):
        write_owned_fields(self.connection, [1], {"studio": "用户写的"}, USER_MANUAL)
        result = write_owned_fields(
            self.connection, [1], {"studio": "刮削给的"}, auto_owner("javbus"))
        self.assertEqual(result.refused, ("studio",))
        self.assertEqual(result.written, ())
        row = self._row(1)
        self.assertEqual(row["studio"], "用户写的")
        self.assertEqual(owner_of(row["field_owners"], "studio"), USER_MANUAL)

    def test_a_blocked_field_does_not_take_the_rest_of_the_write_with_it(self):
        """按字段判而不是按行判：一个字段被接管不该让同批其它无主字段也写不进去。"""
        write_owned_fields(self.connection, [1], {"studio": "用户写的"}, USER_MANUAL)
        result = write_owned_fields(
            self.connection, [1],
            {"studio": "刮削给的", "series": "某系列", "release_date": "2026-01-02"},
            auto_owner("javbus"))
        self.assertEqual(result.refused, ("studio",))
        self.assertEqual(sorted(result.written), ["release_date", "series"])
        row = self._row(1)
        self.assertEqual((row["studio"], row["series"], row["release_date"]),
                         ("用户写的", "某系列", "2026-01-02"))
        self.assertEqual(parse_owners(row["field_owners"]), {
            "studio": USER_MANUAL, "series": "auto:javbus",
            "release_date": "auto:javbus"})

    def test_the_user_overrides_any_automatic_owner(self):
        write_owned_fields(self.connection, [1], {"code": "ABC-001"},
                           script_owner("apply_metadata_tags"))
        result = write_owned_fields(self.connection, [1], {"code": "ABC-002"}, USER_MANUAL)
        self.assertEqual(result.refused, ())
        self.assertEqual(self._row(1)["code"], "ABC-002")
        self.assertEqual(owner_of(self._row(1)["field_owners"], "code"), USER_MANUAL)

    def test_approval_overrides_what_an_earlier_approval_wrote(self):
        write_owned_fields(self.connection, [1], {"series": "旧系列"},
                           review_owner("r18dev"))
        write_owned_fields(self.connection, [1], {"series": "新系列"},
                           review_owner("javbus"))
        row = self._row(1)
        self.assertEqual(row["series"], "新系列")
        self.assertEqual(owner_of(row["field_owners"], "series"), "review:javbus")
        self.assertEqual(row["mutation_revision"], 2)

    def test_require_empty_refuses_a_field_that_already_has_a_value(self):
        write_owned_fields(self.connection, [1], {"code": "ABC-001"},
                           auto_owner("r18dev"))
        result = write_owned_fields(self.connection, [1], {"code": "ZZZ-999"},
                                    "scan:filename", require_empty=True)
        self.assertEqual(result.refused, ("code",))
        self.assertEqual(self._row(1)["code"], "ABC-001")

    def test_the_revision_only_counts_truth_field_changes(self):
        write_owned_fields(self.connection, [1], {"studio": "同一个值"},
                           auto_owner("r18dev"))
        write_owned_fields(self.connection, [1], {"studio": "同一个值"},
                           auto_owner("javbus"))
        row = self._row(1)
        self.assertEqual(row["mutation_revision"], 1)
        # 取值没变也要把归属换成最后一个写入者：回答的是「现在这个值算谁的」。
        self.assertEqual(owner_of(row["field_owners"], "studio"), "auto:javbus")

    def test_a_stale_revision_is_refused_and_reports_the_current_one(self):
        write_owned_fields(self.connection, [1], {"studio": "甲"}, USER_MANUAL)
        with self.assertRaises(RevisionConflict) as caught:
            write_owned_fields(self.connection, [1], {"studio": "乙"}, USER_MANUAL,
                               expected_revision=0)
        self.assertEqual(caught.exception.expected, 0)
        self.assertEqual(caught.exception.revisions, {1: 1})
        self.assertEqual(self._row(1)["studio"], "甲")

    def test_a_matching_revision_lets_the_write_through(self):
        write_owned_fields(self.connection, [1], {"studio": "甲"}, USER_MANUAL,
                           expected_revision=0)
        self.assertEqual(self._row(1)["studio"], "甲")
        write_owned_fields(self.connection, [1], {"studio": "乙"}, USER_MANUAL,
                           expected_revision=1)
        self.assertEqual(self._row(1)["studio"], "乙")

    def test_check_revision_covers_paths_that_write_other_tables(self):
        write_owned_fields(self.connection, [1], {"studio": "甲"}, USER_MANUAL)
        check_revision(self.connection, [2], 0)
        with self.assertRaises(RevisionConflict):
            check_revision(self.connection, [1, 2], 0)

    def test_one_statement_covers_every_targeted_asset(self):
        result = write_owned_fields(
            self.connection, [1, 2], {"catalog_title": "同番号两卷"}, review_owner("mgstage"))
        self.assertEqual(result.assets, 2)
        self.assertEqual(result.revisions, {1: 1, 2: 1})
        for asset_id in (1, 2):
            self.assertEqual(self._row(asset_id)["catalog_title"], "同番号两卷")

    def test_a_null_owner_map_never_wipes_the_existing_owners(self):
        """`json_patch` 碰上 NULL 会把整个结果变成 NULL，基值与跳过分支都得给 `'{}'`。"""
        write_owned_fields(self.connection, [1], {"studio": "用户写的"}, USER_MANUAL)
        write_owned_fields(self.connection, [1],
                           {"studio": "刮削给的", "series": "某系列"},
                           auto_owner("javbus"))
        stored = self._row(1)["field_owners"]
        self.assertIsNotNone(stored)
        self.assertEqual(json.loads(stored),
                         {"studio": USER_MANUAL, "series": "auto:javbus"})


class FieldOwnerVocabularyTests(unittest.TestCase):
    def test_only_the_declared_truth_fields_can_be_written(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        with self.assertRaises(ValueError):
            write_owned_fields(connection, [1], {"play_count": 3}, USER_MANUAL)

    def test_an_unknown_owner_category_is_refused(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        with self.assertRaises(ValueError):
            write_owned_fields(connection, [1], {"studio": "甲"}, "plugin:whatever")
        with self.assertRaises(ValueError):
            write_owned_fields(connection, [1], {"studio": "甲"}, "user")

    def test_owner_identifiers_are_restricted_to_a_safe_shape(self):
        self.assertEqual(review_owner("r18dev"), "review:r18dev")
        self.assertEqual(auto_owner("aventertainment"), "auto:aventertainment")
        self.assertEqual(script_owner("apply_metadata_tags"),
                         "script:apply_metadata_tags")
        for bad in ("", "R18Dev", "has space", 'quote"'):
            with self.assertRaises(ValueError):
                review_owner(bad)

    def test_a_broken_owner_map_reads_as_unowned(self):
        self.assertEqual(parse_owners("not json"), {})
        self.assertEqual(parse_owners(None), {})
        self.assertEqual(parse_owners('{"play_count":"user:manual"}'), {})
        self.assertEqual(owner_of('{"studio":"user:manual"}', "studio"), USER_MANUAL)
        self.assertEqual(owner_of('{"studio":"user:manual"}', None), "")

    def test_every_owned_field_is_a_real_asset_column(self):
        database = Path(tempfile.mkdtemp()) / "ledger.db"
        sqlite3.connect(database).close()
        upgrade(database, MIGRATIONS)
        connection = sqlite3.connect(database)
        self.addCleanup(connection.close)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(asset)")}
        self.assertTrue(set(OWNED_FIELDS) <= columns)

    def test_owner_labels_say_who_wrote_it(self):
        self.assertEqual(owner_label(USER_MANUAL), "手动填写")
        self.assertEqual(owner_label("review:javbus"), "复核批准 javbus")
        self.assertEqual(owner_label("auto:r18dev"), "r18dev 免复核落库")
        self.assertEqual(owner_label("scan:filename"), "文件名推导")
        self.assertEqual(owner_label("script:clean_names"), "维护脚本 clean_names")
        self.assertEqual(owner_label(""), "")


if __name__ == "__main__":
    unittest.main()
