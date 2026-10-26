"""口径变了之后已经过期的资料候选。"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from peach.library_processing import FIELDS
from peach.review_csv import read_rows, write_rows
from peach.stale_candidates import stale_genre_rows, without

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "drop_stale_genre_candidates.py"
_spec = importlib.util.spec_from_file_location("drop_stale_genre_candidates", SCRIPT)
_script = importlib.util.module_from_spec(_spec)
sys.modules["drop_stale_genre_candidates"] = _script
_spec.loader.exec_module(_script)


def _row(item_key, field, candidates, asset_id=1):
    return {"item_key": item_key, "code": "ABW-358", "query": "ABW-358", "asset_id": asset_id,
            "asset_path": r"R:\media\x.mp4", "field": field, "field_label": "内容标签",
            "current_value": "", "candidates_json": json.dumps(candidates, ensure_ascii=False),
            "source_count": len(candidates), "source_profile": "library",
            "policy_version": "library-v1", "status": "candidate", "size_gb": "1.0",
            "videos": "1", "fetched_at": "2026-09-15 00:00:00"}


class StaleGenreTests(unittest.TestCase):
    def test_an_english_word_from_r18_is_what_marks_a_row_stale(self):
        rows = [_row("asset:1:tags", "tags",
                     [{"source": "r18dev", "value": ["熟女"], "unmapped_genres": ["Sex Toy"]}])]
        self.assertEqual([row["item_key"] for row in stale_genre_rows(rows)], ["asset:1:tags"])

    def test_a_japanese_word_still_waiting_for_a_decision_is_not_stale(self):
        """日文原词已经是这一趟要的东西，重抓一遍只会拿回同一个词。"""
        rows = [_row("asset:2:tags", "tags",
                     [{"source": "r18dev", "value": [], "unmapped_genres": ["シャワー"]}])]
        self.assertEqual(stale_genre_rows(rows), [])

    def test_a_genre_that_is_written_the_same_in_both_languages_is_not_stale(self):
        """`69` 两边写法一样，按它摘行就是每跑一次摘一次、永远收敛不了。"""
        rows = [_row("asset:9:tags", "tags",
                     [{"source": "r18dev", "value": ["巨乳"], "unmapped_genres": ["69"]}])]
        self.assertEqual(stale_genre_rows(rows), [])

    def test_other_sources_and_other_fields_are_left_alone(self):
        """英文那一层只有 r18 有；别的来源本来就返回日文。"""
        rows = [
            _row("asset:3:tags", "tags",
                 [{"source": "javbus", "value": [], "unmapped_genres": ["Sex Toy"]}]),
            _row("asset:4:title", "title", [{"source": "r18dev", "value": "Remu Style"}]),
        ]
        self.assertEqual(stale_genre_rows(rows), [])

    def test_dropping_keeps_every_other_row(self):
        rows = [_row("asset:5:tags", "tags",
                     [{"source": "r18dev", "value": [], "unmapped_genres": ["Ahegao"]}]),
                _row("asset:5:title", "title", [{"source": "r18dev", "value": "x"}])]
        self.assertEqual([row["item_key"] for row in without(rows, stale_genre_rows(rows))],
                         ["asset:5:title"])

    def test_a_preview_run_leaves_the_file_alone_and_applying_backs_it_up(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "candidates.csv"
        rows = [_row("asset:6:tags", "tags",
                     [{"source": "r18dev", "value": [], "unmapped_genres": ["Oil"]}]),
                _row("asset:6:title", "title", [{"source": "r18dev", "value": "x"}])]
        write_rows(path, FIELDS, rows)

        self.assertEqual(_script.run(_script.build_parser().parse_args(
            ["--candidates", str(path)])), 0)
        self.assertEqual(len(read_rows(path)), 2, "预览不改文件")

        self.assertEqual(_script.run(_script.build_parser().parse_args(
            ["--candidates", str(path), "--apply"])), 0)
        self.assertEqual([row["item_key"] for row in read_rows(path)], ["asset:6:title"])
        backup = path.with_suffix(".pre-japanese-genres.csv")
        self.assertEqual(len(read_rows(backup)), 2, "备份是摘掉之前的那份")
