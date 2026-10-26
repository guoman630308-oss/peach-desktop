# -*- coding: utf-8 -*-
"""同一位出演者的另一个艺名，从复核候选里认出来的判据。"""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import link_performer_name_variants as link  # noqa: E402

SCHEMA = (
    "CREATE TABLE asset(id INTEGER PRIMARY KEY, code TEXT)",
    "CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT,"
    " normalized_name TEXT)",
    "CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT, normalized_alias TEXT,"
    " source TEXT, confidence REAL DEFAULT 1.0,"
    " PRIMARY KEY(entity_id, normalized_alias, source))",
)


def row(code: str, *candidates: tuple[str, str]) -> dict:
    """一行出演者候选。每个 `(来源, 顿号分隔的名字)` 是一条候选。"""
    return {"field": "performers", "code": code, "query": code,
            "candidates_json": json.dumps(
                [{"source": source, "display_value": names} for source, names in candidates],
                ensure_ascii=False)}


class LinkPerformerNameVariantTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        for statement in SCHEMA:
            self.connection.execute(statement)
        self.connection.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name) VALUES(?,'performer',?,?)",
            [(1, "美空彩香", "美空彩香"), (2, "森泽佳奈", "森泽佳奈"), (3, "白咲碧", "白咲碧")])
        self.connection.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source) "
            "VALUES(1,'美空あやか','美空あやか','peach:canonicalization')")
        self.connection.executemany(
            "INSERT INTO asset(id,code) VALUES(?,?)",
            [(1, "n0762"), (2, "259LUXU-811"), (3, "LUXU-688")])
        self.addCleanup(self.connection.close)

    def collect(self, *rows):
        return link.collect(self.connection, list(rows))

    def test_one_anchored_name_makes_the_other_an_alias_of_that_person(self):
        """两家在同一部片上只差一个写法，其中一个有账本锚点，另一个就是同一位。"""
        found, contested = self.collect(
            row("n0762", ("javbus", "藤原遼子"), ("javdb", "森泽佳奈")))
        self.assertEqual(contested, [])
        self.assertEqual([(item["keep_id"], item["alias"]) for item in found],
                         [(2, "藤原遼子")])

    def test_the_anchor_may_be_a_registered_alias_rather_than_the_canonical_name(self):
        """锚点算的是身份不是规范名：`美空あやか` 是别名，同样指得到实体 1。"""
        found, _ = self.collect(row("n0646", ("javbus", "一ノ瀬アメリ"), ("javdb", "美空あやか")))
        self.assertEqual([(item["keep_id"], item["alias"], item["keep_name"]) for item in found],
                         [(1, "一ノ瀬アメリ", "美空彩香")])

    def test_a_shared_cast_member_leaves_the_single_differing_name_pairable(self):
        """两家各给两个人、其中一个逐字相同：剩下那一处才是同一位的另一个写法。"""
        found, _ = self.collect(
            row("n1032", ("javbus", "山本玲奈、本村紗枝"), ("javdb", "本村紗枝、白咲碧")))
        self.assertEqual([(item["keep_id"], item["alias"]) for item in found],
                         [(3, "山本玲奈")])

    def test_two_names_changing_at_once_is_a_real_disagreement(self):
        """整组换掉说明两家说的是两拨人，不是写法不同。"""
        found, _ = self.collect(
            row("n9999", ("javbus", "山本玲奈、佐藤花"), ("javdb", "白咲碧、鈴木葵")))
        self.assertEqual(found, [])

    def test_a_name_neither_side_of_the_ledger_knows_has_no_anchor(self):
        """两边都不认识就没有锚点，谁当规范名都是猜。"""
        found, _ = self.collect(row("122919-001", ("javbus", "新城由衣"), ("javdb", "吉澤ひかり")))
        self.assertEqual(found, [])

    def test_both_names_already_being_separate_people_is_left_to_merge_entity(self):
        """两侧都认识说明账本已经当成两个人，合不合并是另一件事。"""
        found, _ = self.collect(row("n8888", ("javbus", "白咲碧"), ("javdb", "森泽佳奈")))
        self.assertEqual(found, [])

    def test_the_amateur_planning_name_never_becomes_an_alias(self):
        """素人企划的商品页给的是这部片给她起的一次性称呼，不是她的艺名。"""
        found, _ = self.collect(row("259LUXU-811", ("avbase", "白咲碧"), ("javdb", "ひなた唯")))
        self.assertEqual(found, [])

    def test_the_same_planning_label_without_the_numeric_prefix_is_skipped_too(self):
        """同一个企划既有 `259LUXU-811` 也有 `LUXU-688`，字母段一样就整段算进去。"""
        self.assertIn("luxu", link.amateur_stems(self.connection))
        found, _ = self.collect(row("LUXU-688", ("avbase", "白咲碧"), ("javdb", "松永さな")))
        self.assertEqual(found, [])

    def test_promo_copy_is_not_a_name_so_the_whole_candidate_drops_out(self):
        """剪不出艺名边界的是企划文案，整条候选作废。"""
        found, _ = self.collect(row("n7777", ("mgstage", "佐倉井さん 28歳 某企業広報担当"),
                                    ("javdb", "白咲碧")))
        self.assertEqual(found, [])

    def test_one_spelling_claimed_by_two_people_is_written_for_neither(self):
        """同一个写法被两条实体同时认领时一条都不写：指错人比没指更难发现。"""
        found, contested = self.collect(
            row("n0762", ("javbus", "藤原遼子"), ("javdb", "森泽佳奈")),
            row("n0763", ("javbus", "藤原遼子"), ("javdb", "白咲碧")))
        self.assertEqual(found, [])
        self.assertEqual(sorted(item["keep_id"] for item in contested), [2, 3])

    def test_applying_writes_each_alias_once(self):
        found, _ = self.collect(row("n0762", ("javbus", "藤原遼子"), ("javdb", "森泽佳奈")))
        self.assertEqual(link.apply_rows(self.connection, found), 1)
        self.assertEqual(link.apply_rows(self.connection, found), 0)
        self.assertEqual(
            [tuple(item) for item in self.connection.execute(
                "SELECT entity_id,alias,source FROM entity_alias WHERE source=?",
                (link.ALIAS_SOURCE,))],
            [(2, "藤原遼子", link.ALIAS_SOURCE)])


if __name__ == "__main__":
    unittest.main()
