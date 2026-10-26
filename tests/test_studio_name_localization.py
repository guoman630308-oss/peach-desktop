"""厂牌名去罗马音：判据、番号挑选、javbus 解析与整体产物。"""
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
    path = ROOT / "scripts" / "localize_studio_names.py"
    spec = importlib.util.spec_from_file_location("localize_studio_names", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_script()


def javbus_page(maker: str) -> str:
    return (
        "<html><head><title>CEAD-726 - JavBus</title></head><body>"
        '<p><span class="header">製作商:</span>'
        f'<a href="/studio/1x">{maker}</a></p>'
        '<p><span class="header">發行商:</span><a href="/label/9y">別のレーベル</a></p>'
        "</body></html>"
    )


AGE_GATE = "<html><head><title>Age Verification JavBus</title></head><body>你是否已經成年</body></html>"


class FakeSite:
    def __init__(self, pages, error=RuntimeError):
        self.pages = pages
        self.error = error
        self.asked = []
        self.fetched = self.cached = 0

    def get(self, url, refresh=False):
        self.asked.append(url)
        page = self.pages.get(url)
        if page is None:
            raise self.error(404)
        self.fetched += 1
        return page

    def close(self):
        pass


class ShapeTests(unittest.TestCase):
    def test_kanji_or_hiragana_counts_as_japanese(self):
        for name in ("セレブの友", "痴女ヘブン", "アロマ企画", "本中", "桃太郎映像出版"):
            self.assertEqual(MODULE.shape(name), "汉字假名", name)

    def test_pure_katakana_is_its_own_class(self):
        for name in ("ムーディーズ", "プレステージ", "アタッカーズ", "グローリークエスト"):
            self.assertEqual(MODULE.shape(name), "片假名", name)

    def test_latin_names_include_marks_and_digits(self):
        for name in ("MOODYZ", "kira☆kira", "S1 NO.1 STYLE", "Celeb no Tomo", "E-BODY"):
            self.assertEqual(MODULE.shape(name), "拉丁", name)


class DecideTests(unittest.TestCase):
    def test_japanese_maker_replaces_the_romaji_name(self):
        verdict, proposed, evidence = MODULE.decide("Celeb no Tomo", ["セレブの友", "セレブの友"])
        self.assertEqual((verdict, proposed), (MODULE.RENAME, "セレブの友"))
        self.assertIn("2 个番号一致", evidence)

    def test_katakana_maker_keeps_the_english_original(self):
        verdict, proposed, evidence = MODULE.decide("MOODYZ", ["ムーディーズ"])
        self.assertEqual(verdict, MODULE.KEEP_KATAKANA)
        self.assertEqual(proposed, "")
        self.assertIn("保留英文原名", evidence)

    def test_single_code_is_reported_as_weaker_evidence(self):
        _, _, evidence = MODULE.decide("Chijo Heaven", ["痴女ヘブン"])
        self.assertIn("只有 1 个番号可查", evidence)

    def test_disagreeing_makers_are_never_auto_renamed(self):
        verdict, proposed, evidence = MODULE.decide("Das", ["ダスッ！", "本中"])
        self.assertEqual((verdict, proposed), (MODULE.SPLIT, ""))
        self.assertIn("ダスッ！／本中", evidence)

    def test_missing_maker_is_unknown_not_a_guess(self):
        self.assertEqual(MODULE.decide("Hyoko", [])[0], MODULE.UNKNOWN)

    def test_already_japanese_name_is_left_alone(self):
        self.assertEqual(MODULE.decide("本中", ["本中"])[0], MODULE.KEEP_JP)

    def test_maker_equal_to_current_name_is_not_a_rename(self):
        self.assertEqual(MODULE.decide("FALENO", ["FALENO"])[0], MODULE.KEEP_JP)

    def test_latin_maker_differing_only_in_spacing_is_not_a_rename(self):
        verdict, proposed, evidence = MODULE.decide("V＆R PRODUCE", ["V＆RPRODUCE"])
        self.assertEqual((verdict, proposed), (MODULE.KEEP_LATIN, ""))
        self.assertIn("也是拉丁写法", evidence)

    def test_latin_maker_is_kept_even_when_wholly_different(self):
        # 来源自己也写拉丁，就说明日文原名没查到；换成另一串拉丁不是「去罗马音」。
        self.assertEqual(MODULE.decide("Fetish Box", ["FetishBox"])[0], MODULE.KEEP_LATIN)


class ParseTests(unittest.TestCase):
    def test_maker_is_read_from_the_maker_field_not_the_label_field(self):
        self.assertEqual(MODULE.maker_of(javbus_page("セレブの友")), "セレブの友")

    def test_age_gate_page_yields_no_maker(self):
        self.assertEqual(MODULE.maker_of(AGE_GATE), "")


class CodePickTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            "CREATE TABLE entity (id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT);"
            "CREATE TABLE asset (id INTEGER PRIMARY KEY, studio TEXT, code TEXT);"
        )

    def add(self, studio, codes, kind="studio"):
        self.connection.execute(
            "INSERT INTO entity (kind, canonical_name) VALUES (?,?)", (kind, studio))
        for code in codes:
            self.connection.execute(
                "INSERT INTO asset (studio, code) VALUES (?,?)", (studio, code))

    def test_one_group_per_prefix_and_most_common_first(self):
        self.add("Celeb no Tomo", ["CEMD-801", "CEMD-802", "CEMD-802", "CEAD-726", "CEAD-726"])
        groups = MODULE.code_groups(self.connection, "Celeb no Tomo", 3)
        self.assertEqual(sorted(group[0] for group in groups), ["CEAD-726", "CEMD-802"])

    def test_siblings_of_the_same_prefix_stay_as_fallbacks(self):
        # 同前缀不参与印证，但番号页 404 时要有下一个可试。
        self.add("Kichu", ["CHU-101", "CHU-102", "CHU-103", "CHU-201"])
        groups = MODULE.code_groups(self.connection, "Kichu", 2, depth=3)
        by_prefix = {group[0].split("-")[0]: group for group in groups}
        self.assertEqual(by_prefix["CHU"], ["CHU-101", "CHU-102", "CHU-103"])

    def test_depth_caps_the_fallbacks_per_prefix(self):
        self.add("Kichu", ["CHU-101", "CHU-102", "CHU-103"])
        self.assertEqual(MODULE.code_groups(self.connection, "Kichu", 2, depth=2),
                         [["CHU-101", "CHU-102"]])

    def test_non_jav_codes_are_skipped(self):
        self.add("HEYZO", ["heyzo_hd_1234", "", "HEY-022"])
        self.assertEqual(MODULE.code_groups(self.connection, "HEYZO", 2), [["HEY-022"]])

    def test_wanted_caps_the_number_of_prefixes(self):
        self.add("Prestige", ["ABF-246", "ABW-100", "ABP-340", "DIC-001"])
        self.assertEqual(len(MODULE.code_groups(self.connection, "Prestige", 2)), 2)

    def test_studios_are_ordered_by_asset_count_and_respect_the_minimum(self):
        self.add("Big", ["AAA-001", "AAA-002"])
        self.add("Small", ["BBB-001"])
        self.add("Empty", [])
        names = [row["canonical_name"] for row in MODULE.studios(self.connection, 1)]
        self.assertEqual(names, ["Big", "Small"])

    def test_performer_entities_are_not_treated_as_studios(self):
        self.add("Somebody", ["CCC-001"], kind="performer")
        self.assertEqual(MODULE.studios(self.connection, 0), [])

    def tearDown(self):
        self.connection.close()


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.database = self.tmp / "ledger.db"
        connection = sqlite3.connect(self.database)
        connection.executescript(
            "CREATE TABLE entity (id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT);"
            "CREATE TABLE asset (id INTEGER PRIMARY KEY, studio TEXT, code TEXT);"
        )
        for studio, codes in (("Celeb no Tomo", ["CEAD-726", "CEMD-801"]),
                              ("MOODYZ", ["MIAA-092", "MIAA-093"]),
                              ("本中", ["HMN-001"]),
                              ("Hyoko", ["HYK-001"]),
                              ("Fetish Box", ["FBX-001"]),
                              ("Blacked", ["blacked-2024-01-01", ""])):
            connection.execute(
                "INSERT INTO entity (kind, canonical_name) VALUES ('studio',?)", (studio,))
            for code in codes:
                connection.execute(
                    "INSERT INTO asset (studio, code) VALUES (?,?)", (studio, code))
        connection.commit()
        connection.close()
        self.site = FakeSite({
            "https://www.javbus.com/CEAD-726": javbus_page("セレブの友"),
            "https://www.javbus.com/CEMD-801": javbus_page("セレブの友"),
            # MIAA-092 故意缺页：同前缀的下一个番号要能顶上。
            "https://www.javbus.com/MIAA-093": javbus_page("ムーディーズ"),
            "https://www.javbus.com/HYK-001": AGE_GATE,
            "https://www.javbus.com/FBX-001": javbus_page("FetishBox"),
        }, error=MODULE.HttpStatusError)

    def rows(self):
        output = self.tmp / "studio-names.csv"
        args = argparse.Namespace(database=self.database, output=output, min_assets=1,
                                  codes=2, depth=3, limit=0, interval=0, timeout=5,
                                  refresh=False,
                                  cache_dir=self.tmp / "cache")
        self.assertEqual(MODULE.run(args, site=self.site), 0)
        return {row["studio"]: row for row in read_rows(output)}

    def test_each_verdict_lands_on_the_expected_studio(self):
        rows = self.rows()
        self.assertEqual(rows["Celeb no Tomo"]["verdict"], MODULE.RENAME)
        self.assertEqual(rows["Celeb no Tomo"]["proposed"], "セレブの友")
        self.assertEqual(rows["MOODYZ"]["verdict"], MODULE.KEEP_KATAKANA)
        self.assertEqual(rows["MOODYZ"]["proposed"], "")
        # 头一个番号 404 不算「这家查不到」，同前缀的下一个顶上后仍有结论。
        self.assertEqual(rows["MOODYZ"]["source_url"], "https://www.javbus.com/MIAA-093")
        self.assertIn("https://www.javbus.com/MIAA-092", self.site.asked)
        self.assertEqual(rows["Hyoko"]["verdict"], MODULE.UNKNOWN)
        self.assertEqual(rows["Fetish Box"]["verdict"], MODULE.KEEP_LATIN)

    def test_studios_without_jav_codes_are_out_of_scope_not_forensic_failures(self):
        rows = self.rows()
        self.assertEqual(rows["Blacked"]["verdict"], MODULE.SKIP)
        self.assertEqual(rows["Blacked"]["codes"], "")
        self.assertNotEqual(rows["Blacked"]["verdict"], MODULE.UNKNOWN)
        self.assertEqual([url for url in self.site.asked if "blacked" in url.lower()], [])

    def test_japanese_named_studios_are_not_fetched_at_all(self):
        rows = self.rows()
        self.assertNotIn("本中", rows)
        self.assertNotIn("https://www.javbus.com/HMN-001", self.site.asked)

    def test_evidence_carries_the_source_urls(self):
        rows = self.rows()
        self.assertEqual(rows["Celeb no Tomo"]["source_url"],
                         "https://www.javbus.com/CEAD-726 https://www.javbus.com/CEMD-801")

    def test_renames_sort_before_keeps(self):
        output = self.tmp / "studio-names.csv"
        args = argparse.Namespace(database=self.database, output=output, min_assets=1,
                                  codes=2, depth=3, limit=0, interval=0, timeout=5,
                                  refresh=False,
                                  cache_dir=self.tmp / "cache")
        MODULE.run(args, site=self.site)
        verdicts = [row["verdict"] for row in read_rows(output)]
        self.assertEqual(verdicts[0], MODULE.RENAME)
        # 需要人做决定的排前面，不适用的沉到最后，别让它挤在保留项之间。
        self.assertEqual(verdicts[-1], MODULE.SKIP)


if __name__ == "__main__":
    unittest.main()
