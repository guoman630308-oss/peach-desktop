"""外部入口的形状判据与修复计划：目录页、检索页要认得出，直达页不许被误判。"""
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

from peach.link_repair import (   # noqa: E402
    bare_root, confirms, directory_reason, empty_path_segment, html_entity_leak,
    index_candidates, is_search_page, page_title, same_document_path, upgraded_scheme,
    without_entities,
)
from support.ledger import fresh_ledger   # noqa: E402


def load_script():
    spec = importlib.util.spec_from_file_location(
        "repair_entity_links", REPO / "scripts" / "repair_entity_links.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DirectoryShapeTests(unittest.TestCase):
    def test_a_site_root_is_not_a_person_page(self):
        self.assertEqual(directory_reason("http://lightpro.jp/"), "站点根")
        self.assertEqual(directory_reason("http://www.julia-official.com"), "站点根")

    def test_a_directory_word_at_the_end_is_a_listing(self):
        self.assertEqual(directory_reason("http://ai-p.love/model/"), "路径末段是目录词")
        self.assertEqual(directory_reason("http://cmore.jp/official/model.html"),
                         "路径末段是目录词")

    def test_an_index_filename_is_stripped_before_judging(self):
        """`/models/kobatomugi/index.html` 是小鸠むぎ本人的页面。

        `index` 是「这一层的首页」，不是一个对象；不先摘掉它，目录词表就只看到 `index`
        而根本没看它上面那一段，于是一批本人页被判成目录页。
        """
        self.assertEqual(directory_reason("http://www.8man.jp/models/kobatomugi/index.html"), "")
        self.assertEqual(directory_reason("http://www.allurepro.com/models/index.html"),
                         "路径末段是目录词")
        self.assertEqual(directory_reason("http://www.l-promotion.com/index.html"), "站点根")

    def test_a_link_that_others_hang_below_is_a_listing(self):
        """`/model/` 下面挂着 `/model/12110`，那 `/model/` 讲的是「有哪些人」。"""
        self.assertEqual(
            directory_reason("http://official.nax-pro.com/model/", ["/model/12110", "/model/"]),
            "同站另有更深的链接挂在它下面")

    def test_a_named_page_under_a_directory_is_left_alone(self):
        self.assertEqual(directory_reason("https://so-agent.jp/model/nagi_hikaru/"), "")
        self.assertEqual(
            directory_reason("http://bambi.ne.jp/official/model.php?alias=aiga_mizuki&id=176"), "")

    def test_a_query_only_address_is_still_the_site_root(self):
        """`bambi.ne.jp` 把 `/official/model.php?alias=…` 整段重定向到 `/?alias=…`。

        两头标题完全一致，只比标题就会把一条个人页链接换成站点首页，而「新旧标题相同」
        看起来还像是很强的证据。
        """
        self.assertTrue(bare_root("https://bambi.ne.jp/?alias=hoshina_ai&id=138"))
        self.assertTrue(bare_root("https://www.l-promotion.com/index.html"))
        self.assertFalse(bare_root("https://bambi.ne.jp/official/model.php?alias=x&id=1"))

    def test_a_redirect_to_another_path_is_a_different_document(self):
        """换协议时跳到另一条路径，两头标题一致证明的是「都落在兜底页上」。

        `t-powers.co.jp` 把拼错的 `/telent/…` 跳到 `/release/`，`l-promotion.com` 把
        `/naiyou.html` 跳到 `/blog/`；扩展名和末尾斜杠的整理才是同一篇文档。
        """
        self.assertTrue(same_document_path(
            "https://www.prestige-av.com/special/nagisa_konomi",
            "https://www.prestige-av.com/special/nagisa_konomi.php"))
        self.assertTrue(same_document_path("https://lightpro.jp/talent/kirarin.html",
                                           "https://lightpro.jp/talent/kirarin.html"))
        self.assertFalse(same_document_path("https://www.t-powers.co.jp/release/",
                                            "https://www.t-powers.co.jp/telent/ひなた"))
        self.assertFalse(same_document_path("https://www.l-promotion.com/blog/",
                                            "https://www.l-promotion.com/naiyou.html"))

    def test_an_empty_path_segment_is_visible(self):
        self.assertTrue(empty_path_segment("https://www.t-powers.co.jp/talent//"))
        self.assertFalse(empty_path_segment("https://www.t-powers.co.jp/talent/"))


class SearchShapeTests(unittest.TestCase):
    def test_the_three_search_forms_on_the_ledger_are_caught(self):
        for url in ("https://video.dmm.co.jp/av/list/?key=釈アリス",
                    "https://www.mgstage.com/search/cSearch.php?actor[]=釈アリス&type=top",
                    "https://www.av-event.jp/search/?q=釈アリス"):
            self.assertTrue(is_search_page(url), url)

    def test_an_id_bearing_page_is_not_a_search(self):
        """直达页的查询里装的是站内编号或别名，不是检索词。

        把 `actress=`、`actress_id=`、`name=` 一起收进检索词表，就会把本来正确的
        直达地址判成问题，然后拿它去「修」。
        """
        for url in ("https://video.dmm.co.jp/av/list/?actress=1019076",
                    "https://www.prestige-av.com/actress/actress_detail.php?name=kiduki&actress_id=4822",
                    "https://senzai.tv/m_detail.asp?idx=10138",
                    "https://prime-recruit.com/girlInfo.php?id=120"):
            self.assertFalse(is_search_page(url), url)


class UrlTextTests(unittest.TestCase):
    def test_an_unescaped_entity_breaks_the_query(self):
        url = "http://bambi.ne.jp/official/model.php?alias=aiga_mizuki&amp;id=176"
        self.assertTrue(html_entity_leak(url))
        self.assertEqual(without_entities(url),
                         "http://bambi.ne.jp/official/model.php?alias=aiga_mizuki&id=176")

    def test_a_clean_url_is_left_alone(self):
        self.assertFalse(html_entity_leak("https://x.com/alice_710_"))
        self.assertEqual(upgraded_scheme("https://x.com/alice_710_"), "")
        self.assertEqual(upgraded_scheme("http://official.nax-pro.com/model/"),
                         "https://official.nax-pro.com/model/")

    def test_the_link_itself_comes_first_when_it_is_the_index(self):
        """目录页的索引就是它自己；照死链那样先砍掉末段，人名全在被砍掉的那一页上。"""
        self.assertEqual(index_candidates("http://official.nax-pro.com/model/",
                                          include_self=True)[0],
                         "http://official.nax-pro.com/model/")

    def test_a_listing_title_does_not_confirm_an_individual(self):
        self.assertEqual(confirms("<title>モデル | NAX</title>", ["釈アリス"]), "")
        self.assertIn("釈アリス", confirms("<title>釈アリス | NAX公式</title>", ["釈アリス"]))
        self.assertEqual(page_title("<title> 釈アリス\n | NAX </title>"), "釈アリス | NAX")


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script()
        self.directory = Path(tempfile.mkdtemp())
        self.db = fresh_ledger(self.directory)
        self.connection = sqlite3.connect(self.db)
        self.connection.executemany(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(?,?,?,?,'2026-09-22T00:00:00Z','2026-09-22T00:00:00Z')",
            [(1, "performer", "释爱丽丝", "释爱丽丝"), (2, "performer", "岬樱", "岬樱")])
        self.connection.execute(
            "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
            " VALUES(1,'釈アリス','釈アリス','test',1.0)")
        self.connection.commit()

    def tearDown(self):
        self.connection.close()

    def add(self, link_id, entity_id, link_kind, url, metadata="{}"):
        self.connection.execute(
            "INSERT INTO entity_link(id,entity_id,link_kind,label,url,hostname,metadata_json,"
            "created_at,updated_at) VALUES(?,?,?,'标签',?,'host',?,"
            "'2026-09-22T00:00:00Z','2026-09-22T00:00:00Z')",
            (link_id, entity_id, link_kind, url, metadata))
        self.connection.commit()

    def findings(self):
        return {row["link_id"]: row
                for row in self.module.review(self.module.load_links(self.connection))}

    def test_each_shape_gets_its_own_problem_and_fix(self):
        self.add(1, 1, "official", "http://official.nax-pro.com/model/")
        self.add(2, 2, "official", "https://official.nax-pro.com/model/14717")
        self.add(3, 1, "catalog", "https://video.dmm.co.jp/av/list/?key=釈アリス")
        self.add(4, 2, "official",
                 "http://bambi.ne.jp/official/model.php?alias=aiga_mizuki&amp;id=176")
        found = self.findings()
        self.assertEqual([name for name, _, _ in found[1]["problems"]],
                         [self.module.DIRECTORY, self.module.PLAIN_HTTP])
        self.assertNotIn(2, found)
        self.assertEqual(found[3]["problems"][0][:2],
                         (self.module.SEARCH, self.module.FIX_KEEP_SEARCH))
        self.assertEqual([name for name, _, _ in found[4]["problems"]],
                         [self.module.ENTITY_LEAK, self.module.PLAIN_HTTP])

    def test_a_search_page_becomes_a_direct_page_only_with_a_stored_id(self):
        """没有 id 就标 `保留检索页`。按名字拼一个直达地址，打开是别人才麻烦。"""
        self.add(1, 1, "catalog", "https://video.dmm.co.jp/av/list/?key=釈アリス")
        self.assertEqual(self.findings()[1]["problems"][0][1], self.module.FIX_KEEP_SEARCH)
        self.connection.execute(
            "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id)"
            " VALUES(1,'dmm','performer','1019076')")
        self.connection.commit()
        self.assertEqual(self.findings()[1]["problems"][0][1], self.module.FIX_DIRECT_ID)

    def test_two_links_on_one_site_are_reported_and_not_touched(self):
        """两个账号可能都是她的，也可能有一条是别人的。删哪一条都是替用户做决定。"""
        self.add(1, 1, "social", "https://x.com/alice_710_")
        self.add(2, 1, "social", "https://x.com/shaku_alice")
        found = self.findings()
        self.assertEqual([name for name, _, _ in found[1]["problems"]],
                         [self.module.DUPLICATE])
        self.assertIsNone(self.module.primary(found[1]))

    def test_a_latin_agency_name_is_still_usable_against_a_title(self):
        """事务所和厂牌的规范名常常就是罗马字，它们的官网首页要靠这个名字确认。

        日文站检索把罗马字排除在外是对的；标题比对不受那条规矩管，照搬会让这些实体
        一个名字都拿不到。
        """
        self.assertEqual(self.module.title_names("T-POWERS", []), ["T-POWERS"])
        self.assertEqual(self.module.title_names("释爱丽丝", ["釈アリス", "Alice Shaku"]),
                         ["釈アリス", "释爱丽丝"])
        self.assertEqual(self.module.title_names("AI", []), [])

    def test_the_deepest_fix_wins_when_a_link_breaks_several_rules(self):
        """重探出来的地址本来就是站点现在给的形态，协议问题跟着一起没了。"""
        self.add(1, 1, "official", "http://official.nax-pro.com/model/")
        self.assertEqual(self.module.primary(self.findings()[1])[1],
                         self.module.FIX_REDISCOVER)


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script()
        self.directory = Path(tempfile.mkdtemp())
        self.db = fresh_ledger(self.directory)
        self.connection = sqlite3.connect(self.db)
        self.connection.execute(
            "INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES(1,'performer','释爱丽丝','释爱丽丝','2026-09-22T00:00:00Z',"
            "'2026-09-22T00:00:00Z')")
        self.connection.executemany(
            "INSERT INTO entity_link(id,entity_id,link_kind,label,url,hostname,metadata_json,"
            "created_at,updated_at) VALUES(?,1,'official','NAX',?,'official.nax-pro.com',"
            "'{\"source\": \"minnano-av\"}','2026-09-22T00:00:00Z','2026-09-22T00:00:00Z')",
            [(1, "http://official.nax-pro.com/model/"),
             (2, "https://official.nax-pro.com/model/617")])
        self.connection.commit()

    def tearDown(self):
        self.connection.close()

    def plan(self, link_id, new_url, verdict="ok"):
        return {"link_id": str(link_id), "entity_id": "1", "problem": "目录页",
                "url": "http://official.nax-pro.com/model/", "new_url": new_url,
                "verdict": verdict, "check": "标题自述该人"}

    def test_a_repair_keeps_the_original_provenance_and_records_where_it_came_from(self):
        plans = [self.plan(1, "https://official.nax-pro.com/model/14717")]
        tally = self.module.write_plan(self.connection, plans, "2026-09-22T10:00:00Z")
        self.assertEqual(tally["updated"], 1)
        url, host, metadata, updated = self.connection.execute(
            "SELECT url, hostname, metadata_json, updated_at FROM entity_link WHERE id=1"
        ).fetchone()
        self.assertEqual(url, "https://official.nax-pro.com/model/14717")
        self.assertEqual(host, "official.nax-pro.com")
        self.assertEqual(updated, "2026-09-22T10:00:00Z")
        stored = json.loads(metadata)
        self.assertEqual(stored["source"], "minnano-av")
        self.assertEqual(stored["repaired_from"]["url"], "http://official.nax-pro.com/model/")

    def test_a_clash_with_the_unique_key_skips_the_whole_row(self):
        """同一实体已经有一条指向那个地址时，这一行整条跳过并报出来。

        照写会撞 `UNIQUE(entity_id,url)`，整个事务回滚——那时已经改过的行也一起没了，
        而日志上只有一句约束错误。
        """
        plans = [self.plan(1, "https://official.nax-pro.com/model/617")]
        tally = self.module.write_plan(self.connection, plans, "2026-09-22T10:00:00Z")
        self.assertEqual(tally, {"updated": 0, "skipped_unique": 1})
        self.assertEqual(plans[0]["verdict"], "撞上唯一约束，整行跳过")
        self.assertEqual(
            self.connection.execute("SELECT url FROM entity_link WHERE id=1").fetchone()[0],
            "http://official.nax-pro.com/model/")

    def test_only_confirmed_rows_are_written(self):
        plans = [self.plan(1, "", verdict="未取得"),
                 self.plan(2, "https://official.nax-pro.com/model/617", verdict="已是目标")]
        self.assertEqual(self.module.write_plan(self.connection, plans, "2026-09-22T10:00:00Z"),
                         {"updated": 0, "skipped_unique": 0})


if __name__ == "__main__":
    unittest.main()
