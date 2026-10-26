# -*- coding: utf-8 -*-
"""fc2cmadb 评论解析。样本取自 3312576 等页面上的真实评论。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach.sources.fc2cmadb import collection_parts, page_comments, parse_equivalences, parse_performers
from scripts.fetch_fc2_metadata import (
    backfill,
    harvest,
    harvest_rows,
    high_resolution_cover_url,
    metadata_candidate_rows,
    summarise,
    translated_tags,
)

#: 站上评论是 CRLF 换行，样本照原样保留。
CRLF = "\r\n"

# 演员标记：全角空格分隔，一行可有多人。
PERFORMER_COMMENT = """2664942　ひかり
2355314　真夏
2802445　真夏
1955351　みなみ"""

MULTI_PERFORMER_COMMENT = """1934545　ゆう
2589532　未歩なな
2724256　未歩なな　皐月
*
1909413　すず"""

# 等价标记：断言拆成两行写，`=` 起头的行属于上一行。
SPLIT_EQUIV_COMMENT = """3312576-1
= 3090722-3

3312576-4
= 2471432 = 3090722-1 = 4605413"""

# 另一种写法：单行、下划线分片，末尾用 `bad:` 列对不上的分片。
INLINE_EQUIV_COMMENT = """3090722=3312576_1++
2471432=3312576_4
1912367=1909413=1952605=3312576_10

bad:
#2
#3312576-3=9"""


class ParsePerformersTest(unittest.TestCase):
    def test_maps_video_id_to_names(self):
        found = parse_performers(PERFORMER_COMMENT)
        self.assertEqual(found["2355314"], ["真夏"])
        self.assertEqual(found["1955351"], ["みなみ"])

    def test_keeps_every_name_on_a_co_starred_line(self):
        found = parse_performers(MULTI_PERFORMER_COMMENT)
        self.assertEqual(found["2724256"], ["未歩なな", "皐月"])

    def test_ignores_decorations_and_equivalence_lines(self):
        found = parse_performers(MULTI_PERFORMER_COMMENT)
        self.assertNotIn("*", found)
        # 等价行里的数字不能被当成演员名读进来。
        self.assertEqual(parse_performers(INLINE_EQUIV_COMMENT), {})


class ParseEquivalencesTest(unittest.TestCase):
    def test_joins_a_continuation_line_onto_its_subject(self):
        groups = parse_equivalences(SPLIT_EQUIV_COMMENT)
        self.assertIn([("3312576", "1"), ("3090722", "3")], groups)

    def test_reads_underscore_parts_and_long_chains(self):
        groups = parse_equivalences(INLINE_EQUIV_COMMENT)
        self.assertIn([("2471432", ""), ("3312576", "4")], groups)
        self.assertIn(
            [("1912367", ""), ("1909413", ""), ("1952605", ""), ("3312576", "10")],
            groups,
        )

    def test_stops_at_the_bad_section(self):
        """`bad:` 之后是「对不上」的记录，读成等价会把错误关系写进候选。"""
        groups = parse_equivalences(INLINE_EQUIV_COMMENT)
        self.assertNotIn([("3312576", "3"), ("9", "")], groups)
        for group in groups:
            self.assertNotIn("9", [token[0] for token in group])


class CollectionTest(unittest.TestCase):
    def test_counts_distinct_parts_of_this_id(self):
        groups = parse_equivalences(SPLIT_EQUIV_COMMENT + "\n" + INLINE_EQUIV_COMMENT)
        parts = collection_parts("3312576", groups)
        self.assertEqual(parts["1"], "3090722")
        self.assertEqual(parts["4"], "2471432")
        self.assertGreaterEqual(len(parts), 3)

    def test_a_standalone_work_is_not_a_collection(self):
        groups = parse_equivalences("2471432 = 4605413")
        self.assertEqual(collection_parts("2471432", groups), {})


class SummariseTest(unittest.TestCase):
    def _props(self, comments, **article):
        base = {"title": "作品", "image_url": "https://example.invalid/c.png",
                "writer": {"name": "千華繚乱", "slug": "hyakka"}, "tags": []}
        base.update(article)
        return {"article": base, "comments": {"data": comments}}

    def test_withholds_the_cover_from_a_collection(self):
        """合集封面套给每个分片，会让 21 段不同内容显示同一张图。"""
        props = self._props([{"id": 1, "body": SPLIT_EQUIV_COMMENT},
                             {"id": 2, "body": INLINE_EQUIV_COMMENT}])
        row = summarise("3312576", props)
        self.assertEqual(row["is_collection"], "1")
        self.assertEqual(row["cover_url"], "")
        self.assertIn("封面不下发", row["note"])

    def test_keeps_the_cover_for_a_single_work(self):
        row = summarise("2355314", self._props([{"id": 1, "body": PERFORMER_COMMENT}]))
        self.assertEqual(row["is_collection"], "")
        self.assertEqual(row["cover_url"], "https://example.invalid/c.png")

    def test_upgrades_fc2_listing_thumbnail_to_the_measured_1200px_rendition(self):
        url = "https://contents-thumbnail2.fc2.com/w276/storage.example/cover.jpg"
        self.assertEqual(high_resolution_cover_url(url),
                         "https://contents-thumbnail2.fc2.com/w1200/storage.example/cover.jpg")

    def test_ranks_performers_by_how_many_comments_name_them(self):
        props = self._props([{"id": 1, "body": "2355314　真夏"},
                             {"id": 2, "body": "2355314　真夏"},
                             {"id": 3, "body": "2355314　ひかり"}])
        row = summarise("2355314", props)
        self.assertEqual(row["performers"], "真夏 ひかり")
        self.assertEqual(row["performer_votes"], "真夏:2 ひかり:1")


class MetadataCandidateTest(unittest.TestCase):
    def test_translates_only_reviewed_fc2_tags_and_deduplicates_synonyms(self):
        self.assertEqual(
            translated_tags("素人 フェラ フェラチオ 口内発射 未知"),
            ["素人", "口交", "口爆"],
        )

    def test_article_title_tags_and_cover_evidence_enter_the_unified_review_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "ledger.db"
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE asset(
                  id INTEGER PRIMARY KEY,medium TEXT,code TEXT,size INTEGER,
                  catalog_title TEXT,disposal TEXT
                );
                CREATE TABLE asset_tag(asset_id INTEGER,tag TEXT);
                CREATE TABLE genre_decision(
                  source_genre TEXT PRIMARY KEY,raw_genre TEXT NOT NULL,
                  peach_tag TEXT,decided_at TEXT NOT NULL
                );
                INSERT INTO asset VALUES(1,'video','FC2-PPV-3701252',1500000000,NULL,NULL);
            """)
            connection.commit(); connection.close()
            source = root / "fc2-candidate-log.csv"
            rows = metadata_candidate_rows([{
                "code": "FC2 3701252", "video_id": "3701252", "result": "取得",
                "title": "作品标题", "tags": "素人 フェラ 口内発射",
                "duration": "46:44", "cover_url": "https://example.invalid/cover.jpg",
            }], database, raw_snapshot=source, fetched_at="2026-08-19T00:00:00Z")
            self.assertEqual({row["field"] for row in rows}, {"title", "tags"})
            title = next(row for row in rows if row["field"] == "title")
            candidate = json.loads(title["candidates_json"])[0]
            self.assertEqual(candidate["provider"], "fc2cmadb")
            self.assertEqual(candidate["source"], "fc2")
            self.assertTrue(candidate["official"])
            self.assertEqual(candidate["catalog_evidence"]["cover_url"]["value"],
                             "https://example.invalid/cover.jpg")
            tag_row = next(row for row in rows if row["field"] == "tags")
            tags = json.loads(tag_row["candidates_json"])[0]
            self.assertEqual(tags["value"], ["素人", "口交", "口爆"])


class HarvestTest(unittest.TestCase):
    """评论是用户多年攒的人工经验，本地对不上的标记同样要留。"""

    def _props(self, comments):
        return {"article": {"title": "合集", "tags": []},
                "comments": {"data": comments}}

    def test_keeps_markings_for_video_ids_we_do_not_own(self):
        collected = {}
        harvest("3312576", self._props([{"id": 1, "body": MULTI_PERFORMER_COMMENT}]),
                collected)
        rows = {row["video_id"]: row for row in harvest_rows(collected, owned={"3312576"})}
        # 库里没有 2724256，但它的演员标记必须留下来。
        self.assertEqual(rows["2724256"]["performers"], "未歩なな 皐月")
        self.assertEqual(rows["2724256"]["owned"], "")
        self.assertEqual(rows["2724256"]["seen_on"], "3312576")

    def test_records_which_page_a_marking_came_from(self):
        collected = {}
        harvest("3312576", self._props([{"id": 1, "body": PERFORMER_COMMENT}]), collected)
        harvest("2355314", self._props([{"id": 2, "body": "2664942　ひかり"}]), collected)
        rows = {row["video_id"]: row for row in harvest_rows(collected, owned={"2355314"})}
        self.assertEqual(rows["2664942"]["seen_on"], "2355314 3312576")
        self.assertEqual(rows["2664942"]["performer_votes"], "ひかり:2")

    def test_equivalence_members_are_kept_both_ways(self):
        collected = {}
        harvest("3312576", self._props([{"id": 1, "body": SPLIT_EQUIV_COMMENT}]), collected)
        rows = {row["video_id"]: row for row in harvest_rows(collected, owned=set())}
        self.assertIn("3312576", rows["3090722"]["equivalents"].split())
        self.assertIn("4605413", rows["2471432"]["equivalents"].split())


class RealWorldShapesTest(unittest.TestCase):
    """线上抓到的真实写法，照着记忆重画的正则读不出它们。"""

    def test_reads_a_full_width_equals_sign(self):
        """日文输入法打出的是 `＝`，只认半角等号会把这类断言整条丢掉。"""
        groups = parse_equivalences("＝2407240", subject="4143616")
        self.assertEqual(groups, [[("4143616", ""), ("2407240", "")]])

    def test_an_equals_line_without_a_subject_is_dropped(self):
        self.assertEqual(parse_equivalences("＝2407240"), [])

    def test_reads_a_name_followed_by_work_links(self):
        """一行名字 + 若干作品链接，语义是同一个人的作品集。"""
        body = CRLF.join(["剛毛マキちゃん",
                          "https://fc2ppvdb.com/articles/3701252",
                          "https://fc2ppvdb.com/articles/4078398"])
        found = parse_performers(body)
        self.assertEqual(found["3701252"], ["剛毛マキちゃん"])
        self.assertEqual(found["4078398"], ["剛毛マキちゃん"])

    def test_link_listing_without_a_single_name_is_not_guessed(self):
        """说明文字超过一行就说不准哪行是名字，宁可不认。"""
        body = CRLF.join(["同一人？", "たぶん",
                          "https://fc2ppvdb.com/articles/3701252"])
        self.assertEqual(parse_performers(body), {})


class BackfillTest(unittest.TestCase):
    def test_takes_performers_marked_on_another_works_page(self):
        """`4176112` 页上那条链接列表同时认领了 `3701252`，候选行得吃到。"""
        collected = {}
        body = CRLF.join(["剛毛マキちゃん",
                          "https://fc2ppvdb.com/articles/3701252"])
        harvest("4176112", {"comments": {"data": [{"id": 1, "body": body}]}}, collected)
        rows = backfill([{"video_id": "3701252", "performers": "",
                          "performer_votes": "", "equivalents": ""}], collected)
        self.assertEqual(rows[0]["performers"], "剛毛マキちゃん")

    def test_keeps_equivalents_found_on_the_works_own_page(self):
        collected = {}
        harvest("4143616", {"comments": {"data": [{"id": 1, "body": "＝2407240"}]}},
                collected)
        rows = backfill([{"video_id": "4143616", "performers": "",
                          "performer_votes": "", "equivalents": "9999999"}], collected)
        self.assertEqual(rows[0]["equivalents"], "2407240 9999999")


class PageCommentsTest(unittest.TestCase):
    def test_deduplicates_the_two_overlapping_comment_lists(self):
        """`comments.data` 与 `article.comments` 重叠，相加会让一条评论投两票。"""
        props = {"comments": {"data": [{"id": 7, "body": "2355314　真夏"}]},
                 "article": {"comments": [{"id": 7, "body": "2355314　真夏"},
                                          {"id": 8, "body": "2355314　ひかり"}]}}
        self.assertEqual(len(page_comments(props)), 2)
        self.assertEqual(summarise("2355314", props)["performer_votes"],
                         "ひかり:1 真夏:1")


if __name__ == "__main__":
    unittest.main()
