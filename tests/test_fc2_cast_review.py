# -*- coding: utf-8 -*-
"""FC2 演员复核清单：账本已有的演员与 fc2cmadb 对不上的番号列出来，只出清单不写库。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.jav_cover_fetch import NotFound
from peach.review_csv import read_candidates, read_rows
from scripts.fc2_cast_review import build_parser, run
from support.ledger import fresh_ledger


class _Mirror:
    """按番号回 fc2cmadb 那一页；没登记的番号是 404，登记成异常的原样抛。"""

    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def site(self, source, code):
        self.asked.append((source, code))
        page = self.pages.get(code)
        if page is None:
            raise NotFound("HTTP 404")
        if isinstance(page, Exception):
            raise page
        return page


def _page(code, *names):
    return {"id": code, "source_url": f"https://fc2cmadb.com/articles/{code.rsplit('-', 1)[-1]}",
            "actresses": list(names)}


class Fc2CastReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.db = fresh_ledger(self.root)
        self.snapshots = self.root / "library-metadata"
        self.snapshots.mkdir()
        self.output = self.root / "generated" / "fc2-cast-candidates.csv"
        con = sqlite3.connect(self.db)
        try:
            con.executemany(
                "INSERT INTO asset(id,location,path,name,medium,size,first_seen,code)"
                " VALUES(?,'local',?,?,'video',100,'2026-01-01',?)",
                [(1, r"R:\media\a.mp4", "a.mp4", "FC2-PPV-2629971"),
                 (2, r"R:\media\b.mp4", "b.mp4", "FC2-PPV-1449453"),
                 (3, r"R:\media\c.mp4", "c.mp4", "FC2-PPV-3518061"),
                 (4, r"R:\media\d.mp4", "d.mp4", "FC2-PPV-2851534")])
            con.executemany(
                "INSERT INTO entity(id,kind,canonical_name,normalized_name,metadata_json,"
                "created_at,updated_at) VALUES(?,'performer',?,?,'{}','t','t')",
                [(11, "安娜", "安娜"), (12, "Chisa", "chisa"), (13, "梨奈", "梨奈"), (14, "みお", "みお")])
            con.execute("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
                        " VALUES(13,'りな','りな','test')")
            con.executemany(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source,confidence)"
                " VALUES(?,?,'performer',?,1.0)",
                [(1, 11, "javinizer:javdb:performer"), (2, 12, "javinizer:javdb:performer"),
                 (3, 13, "javinizer:javdb:performer"), (4, 14, "user")])
            con.commit()
        finally:
            con.close()

    def _snapshot(self, code, *names):
        (self.snapshots / f"{code}-fc2cmadb.json").write_text(
            json.dumps(_page(code, *names), ensure_ascii=False), encoding="utf-8")

    def _run(self, *extra, provider=None):
        self.pauses = []
        args = build_parser().parse_args(
            ["--db", str(self.db), "--snapshots", str(self.snapshots),
             "--output", str(self.output), "--misses", str(self.root / "misses.json"), *extra])
        return run(args, provider=provider, sleep=self.pauses.append)

    def test_a_different_name_on_the_mirror_becomes_one_review_row(self):
        self._snapshot("FC2-PPV-2629971", "あんな")
        self._snapshot("FC2-PPV-3518061", "りな")
        result = self._run("--offline")
        rows = read_rows(self.output)
        self.assertEqual([row["item_key"] for row in rows], ["FC2-PPV-2629971:performers:fc2cmadb"])
        self.assertEqual(rows[0]["current_value"], "安娜")
        candidate = json.loads(rows[0]["candidates_json"])[0]
        self.assertEqual((candidate["source"], candidate["display_value"]), ("fc2cmadb", "あんな"))
        self.assertEqual(result["review"], 1)
        # 别名登记过的同一个人不算分歧；没存快照的离线时算「站上没有」。
        self.assertEqual((result["agreed_or_empty"], result["not_on_mirror"]), (1, 1))

    def test_a_code_with_a_cast_the_user_set_is_left_alone(self):
        self._snapshot("FC2-PPV-2851534", "梨奈")
        self._run("--offline")
        self.assertEqual(read_rows(self.output), [])

    def test_the_review_page_reads_the_list_as_metadata_fields(self):
        self._snapshot("FC2-PPV-1449453", "大村阿美香")
        self._run("--offline")
        rows, _label, skipped = read_candidates("metadata_fields", self.output.parent)
        self.assertEqual(skipped, 0)
        self.assertEqual([(row["item_key"], row["field"]) for row in rows],
                         [("FC2-PPV-1449453:performers:fc2cmadb", "performers")])

    def test_a_live_answer_is_kept_as_a_snapshot_and_not_asked_twice(self):
        mirror = _Mirror({"FC2-PPV-1449453": _page("FC2-PPV-1449453", "大村阿美香")})
        first = self._run(provider=mirror)
        self.assertEqual((first["review"], first["not_on_mirror"]), (1, 2))
        self.assertTrue((self.snapshots / "FC2-PPV-1449453-fc2cmadb.json").is_file())
        asked = len(mirror.asked)
        self._run(provider=mirror)
        self.assertNotIn(("fc2cmadb", "FC2-PPV-1449453"), mirror.asked[asked:])

    def test_a_code_the_mirror_does_not_have_is_not_asked_again(self):
        mirror = _Mirror({})
        self._run(provider=mirror)
        self.assertEqual(len(mirror.asked), 3)
        result = self._run(provider=mirror)
        self.assertEqual(len(mirror.asked), 3)
        self.assertEqual(result["not_on_mirror"], 3)

    def test_live_questions_are_spaced_but_snapshots_are_not(self):
        self._snapshot("FC2-PPV-1449453", "大村阿美香")
        self._run("--interval", "7", provider=_Mirror({}))
        # 三个番号里一个有快照，剩下两次联网之间停一次。
        self.assertEqual(self.pauses, [7.0])

    def test_a_snapshot_without_the_cast_column_is_asked_once_more(self):
        """女优栏要单独再问一跳，没问过那一跳的快照是空的（`2629971` 站上是 `あんな`）。"""
        self._snapshot("FC2-PPV-2629971")
        self._snapshot("FC2-PPV-3518061")
        mirror = _Mirror({"FC2-PPV-2629971": _page("FC2-PPV-2629971", "あんな"),
                          "FC2-PPV-3518061": _page("FC2-PPV-3518061")})
        self._run(provider=mirror)
        self.assertEqual([row["code"] for row in read_rows(self.output)], ["FC2-PPV-2629971"])
        asked = len(mirror.asked)
        self._run(provider=mirror)
        # 重问过仍空的一周内不再问；有了女优栏的读快照。
        self.assertNotIn("FC2-PPV-3518061", [code for _, code in mirror.asked[asked:]])
        self.assertNotIn("FC2-PPV-2629971", [code for _, code in mirror.asked[asked:]])

    def test_a_failure_other_than_absence_stops_and_keeps_what_was_found(self):
        mirror = _Mirror({"FC2-PPV-1449453": _page("FC2-PPV-1449453", "大村阿美香"),
                          "FC2-PPV-2629971": TimeoutError("read timed out")})
        result = self._run(provider=mirror)
        self.assertTrue(result["stopped"].startswith("FC2-PPV-2629971"))
        self.assertEqual([row["code"] for row in read_rows(self.output)], ["FC2-PPV-1449453"])


if __name__ == "__main__":
    unittest.main()
