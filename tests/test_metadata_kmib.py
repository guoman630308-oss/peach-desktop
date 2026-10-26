"""K-MIB 官网解析与采集脚本的隔离测试。"""
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

from peach import web_contract as rm_web
from peach.catalog_rules import is_korean_mib_code, release_code_from_filename
from peach.metadata_auto_apply import auto_apply_metadata
from peach.genre_taxonomy import map_genres
from peach.metadata_kmib import parse_list, parse_star, parse_video

from support.ledger import fresh_ledger

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "harvest_kmib.py"
_spec = importlib.util.spec_from_file_location("harvest_kmib", SCRIPT)
harvest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harvest)


def video_page(code="YUJ-103", actor="Ain", date="2024-11-20", runtime="00:31:03",
               categories=("Blowjob", "Sexy", "Kiss"),
               cover="/uploads/image/streaming/a.jpg"):
    tags = ", ".join(f"<p onclick=\"viewTag('1', 'T');\">{c}</p>" for c in categories)
    boxes = (("Title", "Do you wanna be my slave"), ("Introduction", "intro"),
             ("Release date", date), ("Playing time", runtime),
             ("Actor", actor), ("Code", code), ("Category", tags))
    body = "".join(f'<div class="info-box"><div class="head">{head}</div>'
                   f'<div class="conts">{value}</div></div>' for head, value in boxes)
    return (f'<div class="video-wrap"><div class="control"><img src="{cover}" alt="">'
            f'</div></div>{body}')


STAR_PAGE = """
<div class="profile-img"><span><img src="/uploads/image/actor/ain.jpg"></span></div>
<div class="main-conts"><em>Ain</em><div class="txt">adult movie actor</div></div>
<div class="profile-detail"><ul>
<li><div class="head">Name</div><div class="conts">Ain</div></li>
<li><div class="head">Age</div><div class="conts">24</div></li>
<li><div class="head">B-W-H</div><div class="conts">33-25-36</div></li>
<li><div class="head">Tag</div><div class="conts"><p>Big Tits</p>, <p>Slender</p></div></li>
</ul></div>
"""


class KmibPageTests(unittest.TestCase):
    def test_video_page_yields_catalog_fields(self):
        release = parse_video(video_page(), 7)
        self.assertEqual(release["id"], "YUJ-103")
        self.assertEqual(release["provider_id"], "YUJ-103")
        self.assertEqual(release["source_url"],
                         "https://www.k-mib.com/video/video-view.php?idx=7")
        self.assertEqual(release["release_date"], "2024-11-20")
        self.assertEqual(release["runtime"], 31.05)
        self.assertEqual(release["actresses"], [{"japanese_name": "Ain"}])
        self.assertEqual((release["maker"], release["partner"]), ("MIB", False))
        self.assertEqual(release["genres"], ["Blowjob", "Sexy", "Kiss"])
        self.assertEqual(release["cover_url"],
                         "https://www.k-mib.com/uploads/image/streaming/a.jpg")

    def test_placeholder_page_is_not_a_release(self):
        """没上架的 idx 各栏都空着，发行日期是 Unix 纪元。"""
        self.assertIsNone(parse_video(video_page(code="", date="1970-01-01"), 5))

    def test_partner_label_in_actor_column_becomes_the_maker(self):
        release = parse_video(video_page(code="JS-012", actor="JS MEDIA"), 9)
        self.assertEqual((release["maker"], release["partner"]), ("JS MEDIA", True))
        self.assertEqual(release["actresses"], [])

    def test_partner_prefix_keeps_its_real_cast(self):
        """`MMP-001` 的 Actor 栏写的是真人，厂牌只能从番号前缀认。"""
        release = parse_video(video_page(code="MMP-001", actor="Noah, Seoyeon"), 10)
        self.assertEqual(release["maker"], "MMP")
        self.assertEqual([a["japanese_name"] for a in release["actresses"]],
                         ["Noah", "Seoyeon"])

    def test_underscore_code_is_the_ledger_writing(self):
        self.assertEqual(parse_video(video_page(code="SOY_101"), 11)["id"], "SOY-101")

    def test_star_page_yields_name_portrait_and_profile(self):
        star = parse_star(STAR_PAGE, 3)
        self.assertEqual(star["name"], "Ain")
        self.assertEqual(star["image_url"], "https://www.k-mib.com/uploads/image/actor/ain.jpg")
        self.assertEqual(star["source_url"], "https://www.k-mib.com/star/star-view.php?idx=3")
        self.assertEqual((star["age"], star["bwh"]), ("24", "33-25-36"))
        self.assertEqual(star["tags"], ["Big Tits", "Slender"])

    def test_list_page_separates_videos_and_stars(self):
        html = ("<a onclick=\"view('432','/video/video-view.php')\"></a>"
                "<a onclick=\"view('30','star-view.php')\"></a>"
                "<a onclick=\"view('432','/video/video-view.php')\"></a>")
        self.assertEqual(parse_list(html), {"video": ["432"], "star": ["30"]})


class KmibCatalogRuleTests(unittest.TestCase):
    def test_mib_filenames_yield_their_codes(self):
        for name, code in (
            ("YUJ-103 Ain Do you wanna be my slave.mp4", "YUJ-103"),
            ("KJI-101 Ggulji Car sex with thick girl.mp4", "KJI-101"),
            ("[K-MIB]NOAH-101(NOAH)(1).mp4", "NOAH-101"),
        ):
            self.assertEqual(release_code_from_filename(name), code, name)

    def test_the_mib_filename_rule_stays_inside_mib_prefixes(self):
        self.assertIsNone(release_code_from_filename("ABC-123 some title.mp4"))
        self.assertIsNone(release_code_from_filename("MIB.mp4"))

    def test_official_catalogue_prefixes_are_korean_mib(self):
        for code in ("NOAH-101", "KJI-102", "MOSA-101", "SJSK-101"):
            self.assertTrue(is_korean_mib_code(code), code)
        # 合作厂牌不是 MIB 的命名体系。
        self.assertFalse(is_korean_mib_code("STRC-001"))

    def test_kmib_categories_project_onto_existing_tags(self):
        """k-mib 的类目在来源页上就是英文，没有日文原词可换，只能靠表里收录英文写法。

        投影仍然只落在既有词表上，门槛在 `test_genre_taxonomy`；`Sexy` 这类几乎每部
        都挂的氛围词按非内容排除，不占一格标签。
        """
        self.assertEqual(
            map_genres(["Doggy-Style", "Masterbation", "Pretty Girl", "Sexy", "Kiss"]),
            (["后入", "自慰", "高颜值", "接吻"], []))


class KmibHarvestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = fresh_ledger(self.root)
        con = sqlite3.connect(self.db)
        for aid, code, name in ((1, "YUJ-103", "YUJ-103 Ain Do you wanna be my slave.mp4"),
                                (2, None, "[K-MIB]NOAH-101(NOAH)(1).mp4"),
                                (3, None, "MIB.mp4"),
                                (4, None, "NOAH-999 not listed.mp4"),
                                (5, "AR-101", "AR-101 Ari.mp4")):
            con.execute("INSERT INTO asset(id,location,path,name,medium,code,size) "
                        "VALUES(?,'local',?,?,'video',?,1000)", (aid, f"/x/{name}", name, code))
        con.commit(); con.close()
        self.releases = [parse_video(video_page(), 7),
                         parse_video(video_page(code="NOAH-101", actor="Noah"), 8),
                         parse_video(video_page(code="JS-012", actor="JS MEDIA"), 9)]
        self.stars = [parse_star(STAR_PAGE, 3)]

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return harvest.metadata_candidate_rows(con, self.releases, self.stars,
                                                   fetched_at="2026-09-10T00:00:00Z")
        finally:
            con.close()

    def test_candidates_carry_their_own_item_keys_and_identity(self):
        """`<番号>:<字段>` 已被 JAV 错配候选的拒绝决定占着，官网候选要另起一个键。"""
        rows = self.rows()
        self.assertEqual({row["code"] for row in rows}, {"YUJ-103"})
        self.assertTrue(all(row["item_key"].endswith(":kmib") for row in rows))
        by_field = {row["field"]: harvest.json.loads(row["candidates_json"])[0]
                    for row in rows}
        self.assertEqual(set(by_field), {"title", "performers", "studio",
                                         "release_date", "tags"})
        self.assertEqual(by_field["studio"]["value"], "MIB")
        self.assertEqual(by_field["performers"]["provider_id"], "YUJ-103")
        self.assertEqual(by_field["performers"]["value"][0]["external_id"], "3")
        self.assertEqual(by_field["tags"]["value"], ["口交", "接吻"])

    def test_missing_codes_come_from_filenames_listed_on_the_official_site(self):
        con = sqlite3.connect(self.db)
        try:
            filled = harvest.fill_missing_codes(con, {"YUJ-103", "NOAH-101"})
            con.commit()
            codes = dict(con.execute("SELECT id,code FROM asset"))
        finally:
            con.close()
        self.assertEqual(filled, [(2, "NOAH-101")])
        self.assertEqual((codes[3], codes[4]), (None, None))

    def test_ledger_codes_missing_from_the_site_are_not_obtained(self):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            rows = harvest.catalog_rows(con, self.releases)
        finally:
            con.close()
        results = {row["code"]: row["result"] for row in rows}
        self.assertEqual(results["AR-101"], "未取得")
        self.assertEqual(results["JS-012"], "取得")

    def test_auto_apply_fills_empty_fields_and_links_the_performer(self):
        candidates = self.root / "generated"
        candidates.mkdir()
        harvest.write_rows(candidates / harvest.CANDIDATE_FILE, harvest.METADATA_FIELDS,
                           self.rows())
        contract = rm_web.WebContract(Path(self.db), candidate_root=candidates,
                                      logo_root=self.root / "logos",
                                      avatar_root=self.root / "avatars")
        outcome = auto_apply_metadata(contract.database, candidates)
        # 标签这一行的 genre 全部有去向，所以和另外四个字段一样走补空落库；
        # 只要还剩一个未收录的词，`_tags_are_fully_resolved` 就把它交回人工。
        self.assertEqual(outcome["applied"], 5)
        con = sqlite3.connect(self.db)
        try:
            asset = con.execute("SELECT catalog_title,release_date,studio FROM asset "
                                "WHERE id=1").fetchone()
            tags = [r[0] for r in con.execute(
                "SELECT tag FROM asset_tag WHERE asset_id=1 AND tag NOT LIKE '演员:%' "
                "ORDER BY tag")]
            ref = con.execute(
                "SELECT e.canonical_name FROM entity_external_ref x JOIN entity e "
                "ON e.id=x.entity_id WHERE x.provider='kmib' AND x.external_kind='performer' "
                "AND x.external_id='3'").fetchone()
        finally:
            con.close()
        self.assertEqual(asset, ("Do you wanna be my slave", "2024-11-20", "MIB"))
        self.assertEqual(tags, ["口交", "接吻"])
        self.assertEqual(ref, ("Ain",))


if __name__ == "__main__":
    unittest.main()
