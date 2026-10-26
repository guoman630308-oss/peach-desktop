"""无番号视频识别清单与候选合并的隔离回归。"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.resource_identification import (
    CANDIDATE_FILENAME,
    build_worklist,
    ingest_results,
    performer_guess,
    query_variants,
)
from peach.review_csv import read_rows, write_rows
from support.ledger import fresh_ledger


class QueryVariantTests(unittest.TestCase):
    def test_chinese_performer_prefix_stays_verbatim_and_guessable(self):
        name = "梓怡-背著老公和合租室友的狂歡 長相乖巧，被大肉棒瘋狂抽插.mp4"
        variants = query_variants(name)
        self.assertEqual(variants[0], "梓怡-背著老公和合租室友的狂歡 長相乖巧，被大肉棒瘋狂抽插")
        self.assertIn("梓怡 背著老公和合租室友的狂歡 長相乖巧，被大肉棒瘋狂抽插", variants)
        self.assertEqual(performer_guess(name), "梓怡")

    def test_unspaced_english_filename_gains_word_bounds(self):
        variants = query_variants("YouGetFootjobStandingFromStarttoFinish9.mp4")
        self.assertIn("You Get Footjob Standing From Startto Finish 9", variants)
        self.assertEqual(performer_guess("YouGetFootjobStandingFromStarttoFinish9.mp4"), "")

    def test_watermarks_and_quality_tails_are_stripped_in_variants(self):
        variants = query_variants("www.98t.la@MIAD-573-uncensored-HD.mp4")
        self.assertEqual(variants[0], "www.98t.la@MIAD-573-uncensored-HD")
        self.assertIn("MIAD-573-uncensored-HD", variants)
        self.assertIn("MIAD 573", variants)

    def test_jav_code_stems_are_not_performer_guesses(self):
        self.assertEqual(performer_guess("259LUXU-1055.mp4"), "")
        self.assertEqual(performer_guess("ABW-153.mp4"), "")


class ResourceWorklistTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.db_path = fresh_ledger(self.root)

    def add(self, asset_id, location, path, medium, size=1000, duration=None,
            code=None, disposal=None):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,size,duration,code,disposal) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (asset_id, location, path, path.rsplit("\\", 1)[-1], medium,
                 size, duration, code, disposal),
            )
            connection.commit()
        finally:
            connection.close()

    def test_worklist_pairs_a_cover_and_skips_code_assets(self):
        base = r"B:\xxr\0208 (23)"
        self.add(1, "115", rf"{base}\梓怡-背著老公.mp4", "video",
                 985 * 1024**2, 1647)
        self.add(2, "115", rf"{base}\梓怡-背著老公.png", "image", 2 * 1024**2)
        self.add(3, "115", rf"{base}\ABW-153.mp4", "video",
                 500 * 1024**2, 3600, code="ABW-153")
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            rows = build_worklist(connection, locations=("115",), prefix=base)
        finally:
            connection.close()

        self.assertEqual([row["asset_id"] for row in rows], [1])
        self.assertEqual(rows[0]["cover_path"], rf"{base}\梓怡-背著老公.png")
        self.assertEqual(rows[0]["performer_guess"], "梓怡")
        self.assertIn("梓怡", rows[0]["query_variants"])

    def test_paired_only_keeps_videos_with_a_sibling_image(self):
        self.add(1, "115", r"B:\xxr\a\有图.mp4", "video", 100 * 1024**2, 600)
        self.add(2, "115", r"B:\xxr\a\有图.jpg", "image", 1024)
        self.add(3, "115", r"B:\xxr\b\无图.mp4", "video", 100 * 1024**2, 600)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            rows = build_worklist(connection, locations=("115",), prefix=r"B:\xxr",
                                  paired_only=True)
        finally:
            connection.close()
        self.assertEqual([row["asset_id"] for row in rows], [1])

    def test_sparse_only_keeps_assets_without_any_metadata(self):
        self.add(1, "115", r"B:\xxr\a\已有标题.mp4", "video", 100 * 1024**2, 600)
        self.add(2, "115", r"B:\xxr\b\没有元信息.mp4", "video", 100 * 1024**2, 600)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                connection.execute(
                    "UPDATE asset SET catalog_title='已有标题' WHERE id=1")
            rows = build_worklist(connection, locations=("115",), prefix=r"B:\xxr",
                                  sparse_only=True)
            all_rows = build_worklist(connection, locations=("115",), prefix=r"B:\xxr")
        finally:
            connection.close()

        self.assertEqual([row["asset_id"] for row in rows], [2])
        self.assertEqual(rows[0]["metadata_hits"], 0)
        self.assertEqual([row["asset_id"] for row in all_rows], [2, 1])
        self.assertEqual(all_rows[1]["metadata_hits"], 1)


class ResourceIngestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.db_path = fresh_ledger(self.root)
        self.candidates = self.root / "generated" / CANDIDATE_FILENAME
        self.candidates.parent.mkdir(parents=True)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,size,duration) "
                "VALUES(1,'115',?,?,'video',?,1647)",
                (r"B:\xxr\0208 (23)\梓怡-背著老公.mp4", "梓怡-背著老公.mp4", 985 * 1024**2),
            )
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,size,duration,code) "
                "VALUES(2,'115',?,?,'video',?,3600,'ABW-153')",
                (r"B:\xxr\0208 (23)\ABW-153.mp4", "ABW-153.mp4", 500 * 1024**2),
            )
            connection.commit()
        finally:
            connection.close()

    def write_existing_local_candidate(self):
        write_rows(self.candidates, (
            "item_key", "code", "query", "asset_id", "asset_path", "field", "field_label",
            "current_value", "candidates_json", "source_count", "source_profile",
            "policy_version", "status", "size_gb", "videos", "fetched_at",
        ), [{
            "item_key": "asset:1:title", "code": "", "query": "梓怡-背著老公.mp4",
            "asset_id": 1, "asset_path": r"B:\xxr\0208 (23)\梓怡-背著老公.mp4",
            "field": "title", "field_label": "标题", "current_value": "",
            "candidates_json": json.dumps([{
                "candidate_key": "local_nfo:abc", "source": "local_nfo",
                "value": "旧标题", "display_value": "旧标题", "confidence": 0.9,
            }], ensure_ascii=False),
            "source_count": 1, "source_profile": "library", "policy_version": "library-v1",
            "status": "candidate", "size_gb": 0.96, "videos": 1, "fetched_at": "",
        }], atomic=True)

    RESULTS = [
        {"asset_id": "1", "field": "title",
         "value": "背着老公和合租室友的狂欢", "source_url": "https://av911.tv/video/113896",
         "confidence": "0.7", "note": "麻豆传媒作品页"},
        {"asset_id": "1", "field": "performers", "value": "梓怡",
         "source_url": "https://av911.tv/video/113896", "confidence": "0.7", "note": ""},
        {"asset_id": "1", "field": "studio", "value": "麻豆传媒",
         "source_url": "https://av911.tv/video/113896", "confidence": "0.6", "note": ""},
        {"asset_id": "2", "field": "title", "value": "不会写入",
         "source_url": "", "confidence": "0.5", "note": ""},
    ]

    def test_results_merge_with_existing_sources_and_stay_idempotent(self):
        self.write_existing_local_candidate()
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            stats = ingest_results(connection, self.candidates, self.RESULTS)
        finally:
            connection.close()
        self.assertEqual(stats["applied"], 3)
        self.assertEqual(stats["skipped"], 1)

        groups = {row["item_key"]: row for row in read_rows(self.candidates)}
        title_choices = json.loads(groups["asset:1:title"]["candidates_json"])
        self.assertEqual({choice["source"] for choice in title_choices},
                         {"local_nfo", "websearch"})
        performer = json.loads(groups["asset:1:performers"]["candidates_json"])[0]
        self.assertEqual(performer["value"], [{"name": "梓怡"}])
        self.assertEqual(performer["display_value"], "梓怡")
        self.assertEqual(groups["asset:1:performers"]["asset_path"],
                         r"B:\xxr\0208 (23)\梓怡-背著老公.mp4")

        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            ingest_results(connection, self.candidates, self.RESULTS)
        finally:
            connection.close()
        groups = {row["item_key"]: row for row in read_rows(self.candidates)}
        self.assertEqual(len(json.loads(groups["asset:1:title"]["candidates_json"])), 2)

    def test_invalid_values_are_skipped(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            stats = ingest_results(connection, self.candidates, [
                {"asset_id": "1", "field": "release_date", "value": "2026/01/01"},
                {"asset_id": "1", "field": "tags", "value": "有码"},
                {"asset_id": "1", "field": "title", "value": "   "},
                {"asset_id": "404", "field": "title", "value": "不存在"},
            ])
        finally:
            connection.close()
        self.assertEqual(stats["applied"], 0)
        self.assertEqual(stats["skipped"], 4)


if __name__ == "__main__":
    unittest.main()
