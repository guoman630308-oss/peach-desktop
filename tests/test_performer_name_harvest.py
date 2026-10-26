"""javdatabase 名字候选：页面解析、番号入口、匹配门槛与整体产物。"""
from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from peach.review_csv import read_rows   # noqa: E402


def load_script():
    path = ROOT / "scripts" / "harvest_javdatabase_names.py"
    spec = importlib.util.spec_from_file_location("harvest_javdatabase_names", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_script()


def movie_page(*slugs: str) -> str:
    links = "".join(
        f'<a href="https://www.javdatabase.com/idols/{slug}/">idol</a>' for slug in slugs)
    return f"<html><body><h1>CEMD-517</h1>{links}</body></html>"


def idol_page(romaji: str, japanese: str = "", alternate: str = "") -> str:
    """照抄真实 idol 页的资料行写法，标签名和名字之间隔着 `</b>`。

    固定件必须是抓回来的那份 HTML，不是照着记忆重画的：按 `JP: 名字` 写的固定件
    能让测试全绿，而线上一个日文名都采不到。
    """
    facts = ""
    if japanese:
        facts += f"\n\t\t\t\t<b>JP:</b> {japanese} "
        facts += f" - <b>Alt:</b> {alternate}<br>" if alternate else "<br>"
    elif alternate:
        facts += f"\n\t\t\t\t<b>Alt:</b> {alternate}<br>"
    return (f"<html><head><title>{romaji} - JAV Database</title></head><body>"
            f'<h1 class="h3">{romaji} - JAV Profile</h1><b>Age:</b> 27{facts}'
            "</body></html>")


class FakeSite:
    def __init__(self, pages, error=RuntimeError):
        self.pages = pages
        self.error = error
        self.asked = []
        self.fetched = self.cached = self.retried = 0

    def get(self, url, refresh=False):
        self.asked.append(url)
        page = self.pages.get(url)
        if page is None:
            raise self.error(404)
        self.fetched += 1
        return page

    def close(self):
        pass


class ParseTests(unittest.TestCase):
    def test_the_profile_line_yields_japanese_and_alternate_names(self):
        page = idol_page("Rin Oka", "凰華りん", "Rin Natsuki")
        self.assertEqual(MODULE.idol_names(page), [
            ("romaji", "Rin Oka"), ("jp", "凰華りん"), ("alt", "Rin Natsuki"),
        ])

    def test_the_column_name_is_not_taken_as_part_of_the_name(self):
        """标题写成 `Rin Oka - JAV Profile`，后半截是栏目名。"""
        names = MODULE.idol_names(idol_page("Rin Oka"))
        self.assertEqual(names, [("romaji", "Rin Oka")])

    def test_several_alternate_names_are_split(self):
        names = MODULE.idol_names(idol_page("Yua Mikami", "三上悠亜", "Momona Kito, Moa Hoshizora"))
        self.assertEqual([value for field, value in names if field == "alt"],
                         ["Momona Kito", "Moa Hoshizora"])

    def test_a_romaji_name_with_a_space_is_not_split(self):
        self.assertIn(("alt", "Rin Natsuki"),
                      MODULE.idol_names(idol_page("Rin Oka", "凰華りん", "Rin Natsuki")))

    def test_the_captured_markup_parses(self):
        """2026-09-02 从 `/idols/arina-arata/` 抓下来的原文，逐字保留。"""
        captured = ('<h1 class="h3">Arina Arata - JAV Profile</h1>'
                    'class="btn btn-primary btn-sm">Suggest Tags</a></br>\n\t\t\t\t'
                    "<b>JP:</b> 新ありな  - <b>Alt:</b> Iwatani Shiki, Arina Hashimoto,"
                    " Mana Kaminogi<br>\n\t\t\t\t"
                    '<a href="https://twitter.com/Arina_aa9" target="_blank"></a>')
        self.assertEqual(MODULE.idol_names(captured), [
            ("romaji", "Arina Arata"), ("jp", "新ありな"), ("alt", "Iwatani Shiki"),
            ("alt", "Arina Hashimoto"), ("alt", "Mana Kaminogi"),
        ])

    def test_the_separator_between_the_two_fields_is_not_part_of_the_name(self):
        names = dict(MODULE.idol_names(idol_page("Rin Oka", "凰華りん", "Rin Natsuki")))
        self.assertEqual(names["jp"], "凰華りん")

    def test_idol_links_come_out_deduplicated_and_in_page_order(self):
        page = movie_page("rin-oka", "yua-mikami", "rin-oka")
        self.assertEqual(MODULE.idol_links(page), [
            "https://www.javdatabase.com/idols/rin-oka/",
            "https://www.javdatabase.com/idols/yua-mikami/",
        ])


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(
            "CREATE TABLE entity (id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT);"
            "CREATE TABLE entity_alias (entity_id INTEGER, alias TEXT, confidence REAL);"
            "CREATE TABLE asset (id INTEGER PRIMARY KEY, code TEXT, medium TEXT);"
            "CREATE TABLE asset_entity (asset_id INTEGER, entity_id INTEGER, source TEXT);"
        )

    def add(self, name, codes, aliases=()):
        cursor = self.connection.execute(
            "INSERT INTO entity (kind, canonical_name) VALUES ('performer',?)", (name,))
        entity_id = cursor.lastrowid
        for alias in aliases:
            self.connection.execute(
                "INSERT INTO entity_alias (entity_id, alias, confidence) VALUES (?,?,1.0)",
                (entity_id, alias))
        for code in codes:
            asset = self.connection.execute(
                "INSERT INTO asset (code, medium) VALUES (?, 'video')", (code,)).lastrowid
            self.connection.execute(
                "INSERT INTO asset_entity (asset_id, entity_id, source) VALUES (?,?,'r18:performer')",
                (asset, entity_id))
        return entity_id

    def test_romaji_aliases_stay_in_the_held_set(self):
        """`name_chain` 按设计剔掉罗马字；判断「账本有没有」必须连罗马字一起看。"""
        entity_id = self.add("夏木铃", ["FSDSS-249"], ["Rin Natsuki", "凰華りん"])
        self.assertEqual(
            set(MODULE.held_names(self.connection)[entity_id]),
            {"夏木铃", "Rin Natsuki", "凰華りん"},
        )

    def test_one_code_per_movie_no_matter_how_many_files(self):
        entity_id = self.add("夏木铃", ["FSDSS-249", "FSDSS-249", "PPT-123"])
        self.assertEqual(MODULE.codes_of(self.connection, entity_id, 2),
                         ["FSDSS-249", "PPT-123"])

    def test_non_jav_codes_never_become_an_entry_point(self):
        entity_id = self.add("皮皮娘", ["", "2024-01-01-vlog"])
        self.assertEqual(MODULE.codes_of(self.connection, entity_id, 2), [])

    def test_a_name_shared_by_two_performers_is_indexed_to_both(self):
        first = self.add("なぎさ", ["ABC-001"])
        second = self.add("Nagisa", ["DEF-002"], ["なぎさ"])
        index = MODULE.index_names(MODULE.held_names(self.connection))
        self.assertEqual(index["なぎさ"], {first, second})

    def tearDown(self):
        self.connection.close()


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.database = self.tmp / "ledger.db"
        connection = sqlite3.connect(self.database)
        connection.executescript(
            "CREATE TABLE entity (id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT);"
            "CREATE TABLE entity_alias (entity_id INTEGER, alias TEXT, confidence REAL);"
            "CREATE TABLE asset (id INTEGER PRIMARY KEY, code TEXT, medium TEXT);"
            "CREATE TABLE asset_entity (asset_id INTEGER, entity_id INTEGER, source TEXT);"
        )
        self.ids = {}
        for name, codes, aliases in (
            ("夏木铃", ["CEMD-517"], ["Rin Natsuki", "凰華りん"]),
            ("ナミちゃん", ["300MIUM-1198"], []),
            ("木村さん", ["MIUM-900"], []),
            ("なぎさ", ["ABC-001"], []),
            ("Nagisa", ["ABC-001"], ["なぎさ"]),
        ):
            entity_id = connection.execute(
                "INSERT INTO entity (kind, canonical_name) VALUES ('performer',?)",
                (name,)).lastrowid
            self.ids[name] = entity_id
            for alias in aliases:
                connection.execute(
                    "INSERT INTO entity_alias (entity_id, alias, confidence) VALUES (?,?,1.0)",
                    (entity_id, alias))
            for code in codes:
                asset = connection.execute(
                    "INSERT INTO asset (code, medium) VALUES (?, 'video')", (code,)).lastrowid
                connection.execute(
                    "INSERT INTO asset_entity (asset_id, entity_id, source)"
                    " VALUES (?,?,'r18:performer')", (asset, entity_id))
        connection.commit()
        connection.close()
        self.site = FakeSite({
            "https://www.javdatabase.com/movies/cemd-517/": movie_page("rin-oka", "someone-else"),
            "https://www.javdatabase.com/idols/rin-oka/":
                idol_page("Rin Oka", "凰華りん", "Rin Natsuki"),
            # 同一部片挂着的另一位女优，名字对不上账本这个人。
            "https://www.javdatabase.com/idols/someone-else/": idol_page("Someone Else", "誰か"),
            "https://www.javdatabase.com/movies/mium-900/": movie_page("kimura-san"),
            "https://www.javdatabase.com/idols/kimura-san/": idol_page("Kimura San", "木村さん"),
            "https://www.javdatabase.com/movies/abc-001/": movie_page("nagisa"),
            "https://www.javdatabase.com/idols/nagisa/": idol_page("Nagisa", "なぎさ"),
        }, error=MODULE.HttpStatusError)

    def rows(self, **overrides):
        output = self.tmp / "javdatabase-names.csv"
        args = argparse.Namespace(database=self.database, output=output, min_assets=1,
                                  codes=2, limit=0, only=None, interval=0, timeout=5,
                                  refresh=False, cache_dir=self.tmp / "cache")
        for key, value in overrides.items():
            setattr(args, key, value)
        self.assertEqual(MODULE.run(args, site=self.site), 0)
        return list(read_rows(output))

    def test_a_name_the_ledger_lacks_is_reported_as_a_candidate(self):
        rows = [row for row in self.rows() if row["entity_id"] == str(self.ids["夏木铃"])]
        candidates = {row["candidate"]: row["verdict"] for row in rows}
        self.assertEqual(candidates["Rin Oka"], MODULE.OK)
        self.assertEqual(candidates["凰華りん"], MODULE.HELD)
        self.assertEqual(candidates["Rin Natsuki"], MODULE.HELD)

    def test_the_other_idol_on_the_same_movie_is_not_attributed(self):
        """一部片挂好几位女优，番号对上不等于整页名字都属于这个人。"""
        candidates = {row["candidate"] for row in self.rows()}
        self.assertNotIn("Someone Else", candidates)
        self.assertNotIn("誰か", candidates)

    def test_a_code_javdatabase_does_not_index_is_a_forensic_failure(self):
        row = next(row for row in self.rows()
                   if row["entity_id"] == str(self.ids["ナミちゃん"]))
        self.assertEqual(row["verdict"], MODULE.MISSING)
        self.assertIn("HTTP 404", row["evidence"])
        self.assertEqual(row["candidate"], "")

    def test_a_page_matching_two_performers_is_left_to_a_human(self):
        rows = [row for row in self.rows() if row["verdict"] == MODULE.AMBIGUOUS]
        self.assertEqual({row["entity_id"] for row in rows},
                         {str(self.ids["なぎさ"]), str(self.ids["Nagisa"])})
        self.assertTrue(all(row["candidate"] == "" for row in rows))

    def test_a_matched_page_is_only_fetched_once_across_performers(self):
        self.rows()
        asked = [url for url in self.site.asked if url.endswith("/idols/nagisa/")]
        self.assertEqual(len(asked), 1)

    def test_candidates_sort_before_the_names_we_already_hold(self):
        verdicts = [row["verdict"] for row in self.rows()]
        self.assertEqual(verdicts[0], MODULE.OK)
        self.assertEqual(verdicts[-1], MODULE.HELD)

    def test_only_narrows_the_batch_without_touching_other_performers(self):
        rows = self.rows(only=str(self.ids["木村さん"]))
        self.assertEqual({row["entity_id"] for row in rows}, {str(self.ids["木村さん"])})
        self.assertEqual([url for url in self.site.asked if "cemd-517" in url], [])


if __name__ == "__main__":
    unittest.main()
