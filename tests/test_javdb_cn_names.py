"""javdb 资料页取中文名：现名与旧名必须分开，中文写法只认一个。

配错一位就是把另一个人的名字写进真相字段。这里守的是「什么才算证据」。
"""
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from peach import javdb
from peach.entities import normalize_entity_name

REPO = Path(__file__).resolve().parents[1]

#: 资料页顶部的真实结构（2026-09-04 实测）。现名一栏中日并列，紧随的是旧艺名。
PAGE = """
<div class="column section-title">
  <h2 class="title is-4">
    <span class="actor-section-name">{current}</span>
    <br />
    <span class="section-meta">{former}</span>
    <br>
    <span class="section-meta">324 部影片</span>
  </h2>
</div>
<a id="button-collect-actor" href="/actors/{actor}/collect"></a>
"""


def page(current: str, former: str = "", actor: str = "RJM8") -> str:
    return PAGE.format(current=current, former=former, actor=actor)


def load_script(name: str):
    """按文件路径加载脚本。

    要先登记进 `sys.modules` 再执行：`localize_performer_names` 里有 dataclass，
    而 `dataclasses` 会去 `sys.modules[cls.__module__]` 取模块字典，没登记就是
    `'NoneType' object has no attribute '__dict__'`——报错指向 dataclass，
    真正的原因在这一行。
    """
    sys.path.insert(0, str(REPO / "src"))
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_module():
    return load_script("harvest_javdb_cn_names")


class ParseTests(unittest.TestCase):
    def test_the_current_name_and_the_former_names_are_two_different_lists(self):
        """`JULIA` 那页的旧名里有 `京香じゅりあ`。混成一串就分不出这是不是她的中文名。"""
        html = page("JULIA", "京香じゅりあ")
        self.assertEqual(javdb.current_names(html), ["JULIA"])
        self.assertEqual(javdb.former_names(html), ["京香じゅりあ"])

    def test_the_film_count_is_not_a_name(self):
        self.assertNotIn("324 部影片", javdb.former_names(page("深田詠美, 深田えいみ")))

    def test_both_writings_of_the_current_name_come_back_in_order(self):
        self.assertEqual(javdb.current_names(page("深田詠美, 深田えいみ")),
                         ["深田詠美", "深田えいみ"])

    def test_the_record_kind_suffix_is_not_part_of_the_name(self):
        """无码那条记录的现名挂着 `(無碼)`。留着会让同一个人变成两个人。"""
        self.assertEqual(javdb.current_names(page("木下ひまり(無碼)")), ["木下ひまり"])

    def test_the_actor_id_is_the_page_identity(self):
        self.assertEqual(javdb.actor_id(page("深田詠美, 深田えいみ", actor="Ng03")), "Ng03")

    def test_a_search_result_that_names_nobody_we_want_is_not_a_hit(self):
        html = ('<div class="box actor-box"><a href="/actors/AAA" title="別人, べつじん">'
                '</a></div><div class="box actor-box">'
                '<a href="/actors/BBB" title="深田詠美, 深田えいみ"></a></div>')
        self.assertEqual(javdb.search_hits(html, {"深田えいみ"}), ["/actors/BBB"])

    def test_a_wider_key_can_be_supplied_for_matching(self):
        """账本写 `宫`、站上写 `宮`，字形差别不该让人搜不到自己。"""
        html = ('<div class="box actor-box"><a href="/actors/AAA" title="宮本櫻, 宮本さくら">'
                '</a></div>')
        self.assertEqual(javdb.search_hits(html, {"宫本さくら"}), [])
        self.assertEqual(
            javdb.search_hits(html, {"宫本さくら"}, lambda name: name.replace("宮", "宫")),
            ["/actors/AAA"])

    def test_two_pages_for_one_name_both_come_back(self):
        """同一个名字在站上真有两位时，取第一个是默默替用户挑了一位。"""
        html = ('<div class="box actor-box"><a href="/actors/AAA" title="あおい"></a></div>'
                '<div class="box actor-box"><a href="/actors/BBB" title="あおい"></a></div>')
        self.assertEqual(javdb.search_hits(html, {"あおい"}), ["/actors/AAA", "/actors/BBB"])


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.record = {"entity_id": 1, "name": "深田えいみ", "assets": 6,
                       "chain": ["深田えいみ", "天海こころ"]}

    def judge(self, current, former=""):
        return self.module.judge(self.record, page(current, former), "https://javdb.com/x")

    def test_a_name_written_both_ways_on_one_page_is_the_evidence(self):
        row = self.judge("深田詠美, 深田えいみ")
        self.assertEqual(row["verdict"], "ok")
        self.assertEqual(row["zh_tw"], "深田詠美")
        self.assertEqual(row["jp"], "深田えいみ")

    def test_a_ledger_name_found_only_among_former_names_is_left_to_a_human(self):
        """站上已经改了名。用哪个当规范名是人要决定的事，不是这一步该定的。"""
        row = self.judge("JULIA", "深田えいみ")
        self.assertEqual(row["verdict"], "旧名")
        self.assertIn("JULIA", row["evidence"])

    def test_a_page_with_only_a_japanese_name_yields_nothing(self):
        """`長谷川るい` 那页只有日文写法。没有就是没有，不转写。"""
        self.record.update(name="長谷川るい", chain=["長谷川るい"])
        row = self.judge("長谷川るい")
        self.assertEqual(row["verdict"], "同形（站上只有日文名）")
        self.assertEqual(row["zh_tw"], "")

    def test_two_chinese_writings_in_one_field_are_ambiguous(self):
        self.record.update(name="田中レモン", chain=["田中レモン", "楓カレン"])
        row = self.judge("田中檸檬, 田中レモン, 楓花戀")
        self.assertEqual(row["verdict"], "多义")

    def test_two_different_stage_names_in_the_current_field_are_not_a_pair(self):
        """`一之瀨亞美莉, 美空あやか` 是两个艺名。姓对不上，中文那个不是这位的名字。"""
        self.record.update(name="美空あやか", chain=["美空あやか"])
        row = self.judge("一之瀨亞美莉, 美空あやか", "一ノ瀬アメリ")
        self.assertEqual(row["verdict"], "不同名（两栏姓氏对不上）")
        self.assertEqual(row["zh_cn" if "zh_cn" in row else "zh_tw"], "")

    def test_a_shared_single_character_is_not_a_shared_surname(self):
        """`美空` 与 `亞美莉` 都有 `美`。按字取交集会把配错的那一对放过去。"""
        self.assertFalse(self.module.same_person("一之瀨亞美莉", "美空あやか"))
        self.assertTrue(self.module.same_person("深田詠美", "深田えいみ"))

    def test_a_kana_only_japanese_name_has_no_surname_to_compare(self):
        """`あべみかこ` 没有汉字可比。无从判不等于判否。"""
        self.assertTrue(self.module.same_person("安部未華子", "あべみかこ"))

    def test_a_glyph_difference_is_not_a_name_difference(self):
        """`宫本さくら` 与 `宮本さくら` 是一个人，`宮` 与 `宫` 只是字形。"""
        self.record.update(name="宫本さくら", chain=["宫本さくら"])
        self.assertEqual(self.judge("宮本櫻, 宮本さくら")["verdict"], "ok")

    def test_a_page_matched_only_through_an_alias_is_left_to_a_human(self):
        """账本规范名不在现名栏：站上写的是另一个艺名，换不换规范名是人的决定。"""
        self.record.update(name="おっぱい隊長", chain=["おっぱい隊長", "夢見るぅ"])
        row = self.judge("夢見露, 夢見るぅ")
        self.assertEqual(row["verdict"], "改艺名（现名栏是另一个艺名）")
        self.assertIn("おっぱい隊長", row["evidence"])

    def test_a_latin_stage_name_is_not_a_chinese_name(self):
        self.record["name"] = "京香じゅりあ"
        self.record["chain"] = ["京香じゅりあ", "JULIA"]
        row = self.judge("JULIA, 京香じゅりあ")
        self.assertEqual(row["verdict"], "同形（站上只有日文名）")


class SimplifyTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.record = {"entity_id": 1, "name": "深田えいみ", "assets": 6,
                       "chain": ["深田えいみ"]}

    def test_the_traditional_writing_is_kept_next_to_the_simplified_one(self):
        """复核件要看得出转了什么：只留简体的话，转错了没人能发现。"""
        rows = self.module.localize(
            [{"verdict": "ok", "zh_tw": "永瀨未萌", "adopt": "永瀨未萌"},
             {"verdict": "旧名", "zh_tw": "", "adopt": "JULIA"}])
        self.assertEqual(rows[0]["zh_cn"], "永濑未萌")
        self.assertEqual(rows[0]["zh_tw"], "永瀨未萌")
        self.assertEqual(rows[1]["zh_cn"], "")

    def test_a_japanese_current_name_is_not_half_converted(self):
        """`きみかわ結衣` 转成 `きみかわ结衣` 是半假名半简体，比原样留着更糟。"""
        rows = self.module.localize([{"verdict": "旧名", "zh_tw": "", "adopt": "きみかわ結衣"},
                                     {"verdict": "旧名", "zh_tw": "", "adopt": "美月麗花"}])
        self.assertEqual(rows[0]["adopt"], "きみかわ結衣")
        self.assertEqual(rows[1]["adopt"], "美月丽花")

    def test_a_site_renamed_row_carries_the_new_name_as_the_candidate(self):
        """改了名的那些也要有候选，不然复核件里只写着「站上不是这个名字了」。"""
        self.record.update(name="京香じゅりあ", chain=["京香じゅりあ", "JULIA"])
        row = self.module.judge(self.record, page("JULIA", "京香じゅりあ"), "https://javdb.com/x")
        self.assertEqual(row["verdict"], "改艺名（现名栏是另一个艺名）")
        self.assertEqual(row["adopt"], "JULIA")
        self.assertEqual(row["jp"], "京香じゅりあ")


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.con = sqlite3.connect(Path(self.tmp.name).resolve() / "ledger.db")
        self.con.executescript(
            "CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT);"
            "CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT);"
            "CREATE TABLE asset_entity(asset_id INTEGER, entity_id INTEGER, source TEXT);")
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name) VALUES(?,'performer',?)",
            [(1, "深田えいみ"), (2, "三上悠亚"), (3, "MattieDoll"), (4, "河合あすな")])
        self.con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,source) VALUES(?,?,?)",
            [(10, 1, "r18:performer"), (11, 2, "r18:performer"),
             (12, 3, "legacy:asset"), (13, 4, "javbus:performer")])
        self.con.execute("INSERT INTO entity_alias VALUES(1,'天海こころ')")
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_only_kana_names_backed_by_release_metadata_are_targets(self):
        """已经是中文的不用查；账号型 performer 不是女优，不翻译。"""
        found = {row["name"] for row in self.module.targets(self.con)}
        self.assertEqual(found, {"深田えいみ", "河合あすな"})

    def test_the_aliases_ride_along_as_search_terms(self):
        """按规范名搜不到时，旧名往往搜得到——名字链是入口，不是装饰。"""
        chain = next(row["chain"] for row in self.module.targets(self.con)
                     if row["name"] == "深田えいみ")
        self.assertEqual(chain, ["深田えいみ", "天海こころ"])

    def test_a_chinese_name_is_a_target_when_the_scope_is_all(self):
        """补别名要找的恰恰是已经有中文规范名的人：她们的第二个写法一个都没进过账本。"""
        found = {row["name"] for row in self.module.targets(self.con, "all")}
        self.assertEqual(found, {"深田えいみ", "三上悠亚", "河合あすな"})

    def test_a_named_performer_is_looked_up_whatever_the_scope_says(self):
        """起点常常是「我看着这一位，觉得少个名字」。被范围挡下的正是最该查的一位。"""
        for named in ("三上悠亚", "2", "天海こころ"):
            found = [row["name"] for row in self.module.targets(self.con, "kana", [named])]
            self.assertEqual(len(found), 1, named)

    def test_naming_nobody_leaves_the_scope_in_charge(self):
        self.assertEqual({row["name"] for row in self.module.targets(self.con, "kana", [])},
                         {"深田えいみ", "河合あすな"})

    def test_a_performer_without_release_metadata_is_out_of_scope_either_way(self):
        """账号型 performer 不是女优，放开范围也不查。"""
        for scope in ("kana", "all"):
            names = {row["name"] for row in self.module.targets(self.con, scope)}
            self.assertNotIn("MattieDoll", names)


class AliasCandidateTests(unittest.TestCase):
    """页面上账本还没有的中文写法。

    `桥本有菜` 这种名字在账本里一个字都没有：刮削源给日文与罗马字，译名那条路每人只
    给一个中文名，改统称时降为别名的又是日文原规范名。旧艺名的中译正是从这一栏来的。
    """

    def setUp(self):
        self.module = load_module()
        self.record = {"entity_id": 1, "name": "新有菜", "assets": 15,
                       "chain": ["新有菜", "新ありな"]}

    def candidates(self, current, former="", owners=None):
        return self.module.alias_candidates(
            self.record, page(current, former), "https://javdb.com/actors/RJM8",
            owners or {})

    def test_a_chinese_writing_in_the_former_name_field_is_a_candidate(self):
        rows = self.candidates("新有菜, 新ありな", "橋本有菜, 橋本ありな")
        self.assertEqual([(row["alias"], row["origin"], row["verdict"]) for row in rows],
                         [("桥本有菜", "别名栏", "ok")])

    def test_a_nickname_in_that_field_is_offered_like_any_other_writing(self):
        """站上不标这一栏放的是什么，`傻梦` 是爱称。认不认得归复核的人，解析层不猜。"""
        self.record.update(entity_id=7746, name="凉森玲梦", chain=["凉森玲梦", "涼森れむ"])
        rows = self.candidates("涼森玲夢, 涼森れむ", "傻梦")
        self.assertEqual([(row["alias"], row["origin"]) for row in rows], [("傻梦", "别名栏")])

    def test_the_writing_on_the_page_is_kept_next_to_the_converted_one(self):
        """复核件要看得出转了什么，也要指得回是哪一页。"""
        row = self.candidates("新有菜, 新ありな", "橋本有菜")[0]
        self.assertEqual(row["alias_zh_tw"], "橋本有菜")
        self.assertEqual(row["actor_id"], "RJM8")

    def test_a_writing_the_ledger_already_has_is_not_a_candidate(self):
        """查过、没查出东西，和还没轮到她是两件事。"""
        self.assertEqual([row["verdict"] for row in self.candidates("新有菜, 新ありな")],
                         ["无新写法"])

    def test_the_same_name_in_traditional_characters_is_not_a_new_writing(self):
        self.record["chain"] = ["新有菜", "桥本有菜"]
        self.assertEqual(
            [row["verdict"] for row in self.candidates("新有菜, 新ありな", "橋本有菜")],
            ["无新写法"])

    def test_a_japanese_or_latin_writing_is_not_a_chinese_alias(self):
        """`JULIA` 是艺名本体，`きみかわ結衣` 掺着假名。两个都不是中文写法。"""
        self.assertEqual(
            [row["verdict"] for row in self.candidates("JULIA, 京香じゅりあ", "きみかわ結衣")],
            ["无新写法"])

    def test_one_writing_listed_in_both_fields_is_offered_once(self):
        rows = self.candidates("新有菜, 橋本有菜", "橋本有菜")
        self.assertEqual([(row["alias"], row["origin"]) for row in rows],
                         [("桥本有菜", "现名栏")])

    def test_a_writing_that_belongs_to_someone_else_is_flagged_not_offered(self):
        """要么两条该合并、要么真有两位重名，都得人判。"""
        owners = {self.module.key("橋本有菜"): (99, "桥本有菜")}
        row = self.candidates("新有菜, 新ありな", "橋本有菜", owners)[0]
        self.assertEqual(row["verdict"], "占用（另一条实体已有这个名字）")
        self.assertIn("99", row["evidence"])


def box(path: str, title: str) -> str:
    return f'<div class="box actor-box"><a href="{path}" title="{title}"></a></div>'


class FakeSite:
    """按地址发页的假站。没登记的地址抛错：取页失败要落进 CSV，不是静默的空页。"""

    def __init__(self, pages: dict):
        self.pages = dict(pages)
        self.asked: list[str] = []

    def get(self, url: str) -> str:
        self.asked.append(url)
        if url not in self.pages:
            raise LookupError(f"没有这一页 {url}")
        return self.pages[url]


class HarvestTests(unittest.TestCase):
    """一趟走完两份产物。分两个脚本就要按 5 秒一页把同一批资料页再走一遍。"""

    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.con = sqlite3.connect(Path(self.tmp.name).resolve() / "ledger.db")
        self.con.executescript(
            "CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT);"
            "CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT);"
            "CREATE TABLE asset_entity(asset_id INTEGER, entity_id INTEGER, source TEXT);")
        self.con.executemany(
            "INSERT INTO entity(id,kind,canonical_name) VALUES(?,'performer',?)",
            [(1, "深田えいみ"), (2, "三上悠亚")])
        self.con.executemany(
            "INSERT INTO asset_entity(asset_id,entity_id,source) VALUES(?,?,'r18:performer')",
            [(10, 1), (11, 2)])
        self.con.execute("INSERT INTO entity_alias VALUES(1,'天海こころ')")
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    @staticmethod
    def search(name: str) -> str:
        return javdb.SEARCH.format(quote(name))

    def test_one_page_feeds_both_sheets(self):
        site = FakeSite({
            self.search("深田えいみ"): box("/actors/BBB", "深田詠美, 深田えいみ"),
            "https://javdb.com/actors/BBB": page("深田詠美, 深田えいみ", "天海こころ")})
        names, aliases = self.module.harvest(self.con, site, 0)
        self.assertEqual([row["verdict"] for row in names], ["ok"])
        self.assertEqual([(row["alias"], row["origin"]) for row in aliases],
                         [("深田咏美", "现名栏")])

    def test_two_pages_for_one_name_yield_no_alias_candidate(self):
        """别名进的是身份。配错了人，比缺一个写法更难查回来。"""
        site = FakeSite({
            self.search("深田えいみ"): box("/actors/BBB", "深田詠美, 深田えいみ"),
            "https://javdb.com/actors/BBB": page("深田詠美, 深田えいみ"),
            self.search("三上悠亚"): (box("/actors/AAA", "三上悠亞, 三上悠亜")
                                     + box("/actors/CCC", "三上悠亞, 三上悠亜"))})
        names, aliases = self.module.harvest(self.con, site, 0, "all")
        crowded = [row for row in aliases if row["current_name"] == "三上悠亚"]
        self.assertEqual([row["verdict"] for row in crowded],
                         ["多页（同名两位，分不清是哪一页）"])
        self.assertNotIn("https://javdb.com/actors/AAA", site.asked)

    def test_the_name_sheet_keeps_to_the_performers_still_named_in_kana(self):
        """`--scope all` 放开的只是别名那一份。中文规范名不需要再定一次规范名。"""
        site = FakeSite({
            self.search("深田えいみ"): box("/actors/BBB", "深田詠美, 深田えいみ"),
            "https://javdb.com/actors/BBB": page("深田詠美, 深田えいみ"),
            self.search("三上悠亚"): box("/actors/AAA", "三上悠亞, 三上悠亜"),
            "https://javdb.com/actors/AAA": page("三上悠亞, 三上悠亜")})
        names, aliases = self.module.harvest(self.con, site, 0, "all")
        self.assertEqual({row["current_name"] for row in names}, {"深田えいみ"})
        self.assertEqual({row["current_name"] for row in aliases},
                         {"深田えいみ", "三上悠亚"})

    def test_a_performer_the_site_does_not_have_is_recorded_in_both_sheets(self):
        site = FakeSite({self.search("深田えいみ"): "", self.search("天海こころ"): ""})
        names, aliases = self.module.harvest(self.con, site, 1)
        self.assertEqual([row["verdict"] for row in names], ["未取得"])
        self.assertEqual([row["verdict"] for row in aliases], ["未取得"])

    def test_a_failed_fetch_lands_in_the_sheets_instead_of_stopping_the_batch(self):
        site = FakeSite({})
        names, aliases = self.module.harvest(self.con, site, 1)
        self.assertEqual([row["verdict"] for row in names], ["取页失败"])
        self.assertIn("LookupError", str(aliases[0]["evidence"]))


class ApplyAliasTests(unittest.TestCase):
    """候选写进 `entity_alias` 的那一步。四种不写，每种都要留下为什么。"""

    def setUp(self):
        self.module = load_script("apply_alias_candidates")
        self.tmp = tempfile.TemporaryDirectory()
        self.con = sqlite3.connect(Path(self.tmp.name).resolve() / "ledger.db")
        self.con.executescript(
            "CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT,"
            " canonical_name TEXT, normalized_name TEXT);"
            "CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT,"
            " normalized_alias TEXT, source TEXT, confidence REAL);")
        for entity_id, name in ((1, "桥本有菜"), (2, "三上悠亚")):
            self.con.execute(
                "INSERT INTO entity VALUES(?,'performer',?,?)",
                (entity_id, name, normalize_entity_name(name)))
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def candidate(self, **over):
        row = {"entity_id": 1, "current_name": "桥本有菜", "alias": "新有菜",
               "origin": "别名栏", "verdict": "ok", "url": "https://javdb.com/actors/RJM8"}
        row.update(over)
        return row

    def plan(self, **over):
        return self.module.plan(self.con, [self.candidate(**over)])[0]

    def aliases_of(self, entity_id: int):
        return [(row[0], row[1]) for row in self.con.execute(
            "SELECT alias,source FROM entity_alias WHERE entity_id=?", (entity_id,))]

    def test_a_new_writing_is_written_with_the_page_as_its_source(self):
        rows = self.module.plan(self.con, [self.candidate()])
        self.assertEqual(rows[0]["action"], "写入")
        self.assertEqual(self.module.apply_rows(self.con, rows, "javdb-20260912"), 1)
        self.assertEqual(self.aliases_of(1),
                         [("新有菜", f"{javdb.ALIAS_SOURCE}@javdb-20260912")])

    def test_the_batch_is_named_in_the_source_so_it_can_be_taken_back_whole(self):
        """解析判错时，认得出批次才能按 `source` 把那一趟整批撤回。"""
        self.module.apply_rows(self.con, self.module.plan(self.con, [self.candidate()]),
                               "javdb-20260912")
        self.module.apply_rows(self.con, self.module.plan(
            self.con, [self.candidate(alias="岩谷志季")]), "javdb-20261001")
        gone = self.con.execute("DELETE FROM entity_alias WHERE source=?",
                                (self.module.source_for("javdb-20261001"),)).rowcount
        self.assertEqual(gone, 1)
        self.assertEqual([alias for alias, _ in self.aliases_of(1)], ["新有菜"])

    def test_a_writing_the_entity_already_has_is_left_alone(self):
        self.con.execute("INSERT INTO entity_alias VALUES(1,'新有菜',?,'javdb-actor-page',1.0)",
                         (normalize_entity_name("新有菜"),))
        self.assertEqual(self.plan()["action"], "已有")

    def test_the_canonical_name_is_not_added_to_its_own_alias_list(self):
        self.assertEqual(self.plan(alias="桥本有菜")["action"], "已有")

    def test_a_writing_that_belongs_to_another_performer_is_refused(self):
        """静默写下去只会给后面的合并判定多送一个假信号。"""
        self.con.execute("INSERT INTO entity_alias VALUES(2,'新有菜',?,'javdb-actor-page',1.0)",
                         (normalize_entity_name("新有菜"),))
        row = self.plan()
        self.assertEqual(row["action"], "占用")
        self.assertIn("三上悠亚", row["detail"])

    def test_a_row_whose_entity_was_renamed_after_the_harvest_is_not_written(self):
        """CSV 是某一刻的快照。统称变过说明这个人后来被动过，该重抓一遍再看。"""
        row = self.plan(current_name="新ありな")
        self.assertEqual(row["action"], "已变")
        self.assertIn("桥本有菜", row["detail"])

    def test_a_row_pointing_at_nobody_is_not_written(self):
        self.assertEqual(self.plan(entity_id=404)["action"], "查无此人")

    def test_a_writing_from_another_site_records_that_site_as_its_source(self):
        rows = self.module.plan(self.con, [self.candidate()])
        self.module.apply_rows(self.con, rows, "namewikis-20260924", base="av_name")
        self.assertEqual(self.aliases_of(1), [("新有菜", "av_name@namewikis-20260924")])

    def test_a_writing_judged_wrong_is_taken_off_whatever_wrote_it(self):
        """javdb 与 Wiki 的別名栏都混有共演男优名，人看过判「删除」就摘掉，来源留在复核 CSV。"""
        self.con.execute("INSERT INTO entity_alias VALUES(1,'島修也',?,'javdb-actor-page@x',1.0)",
                         (normalize_entity_name("島修也"),))
        rows = self.module.plan(self.con, [self.candidate(alias="島修也", verdict="删除")])
        self.assertEqual(rows[0]["action"], "删除")
        self.assertIn("javdb-actor-page@x", rows[0]["detail"])
        self.assertEqual(self.module.apply_rows(self.con, rows, "r"), 0)
        self.assertEqual(self.module.remove_rows(self.con, rows), 1)
        self.assertEqual(self.aliases_of(1), [])

    def test_removing_a_writing_the_entity_does_not_have_changes_nothing(self):
        row = self.plan(alias="島修也", verdict="删除")
        self.assertEqual(row["action"], "名下没有")
        self.assertEqual(self.module.remove_rows(self.con, [row]), 0)

    def test_only_the_verdicts_named_on_the_command_line_are_taken(self):
        rows = [self.candidate(), self.candidate(verdict="占用（另一条实体已有这个名字）"),
                self.candidate(alias="")]
        self.assertEqual(len(self.module.accepted(rows, ["ok"])), 1)
        self.assertEqual(len(self.module.accepted(rows, ["ok", "占用（另一条实体已有这个名字）"])), 2)


class MappingCsvTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script("localize_performer_names")
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name).resolve() / "mapping.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, *rows):
        from peach.review_csv import write_rows
        write_rows(self.path, load_module().FIELDS, list(rows), fill_missing=True)

    def test_only_the_rows_judged_ok_become_mappings(self):
        self.write({"current_name": "深田えいみ", "adopt": "深田咏美", "verdict": "ok",
                    "actor_id": "A1"},
                   {"current_name": "長谷川るい", "adopt": "", "verdict": "同形（站上只有日文名）"})
        mappings = self.module.read_mapping_csv(self.path)
        self.assertEqual([m.jp for m in mappings], ["深田えいみ"])

    def test_a_renamed_row_is_only_taken_when_it_is_named_on_the_command_line(self):
        """站上改了名要不要跟着改是人的决定，不能因为查到了就默认采用。"""
        self.write({"current_name": "京香じゅりあ", "adopt": "JULIA", "verdict": "改艺名"})
        self.assertEqual(self.module.read_mapping_csv(self.path), [])
        taken = self.module.read_mapping_csv(self.path, ["ok", "改艺名"])
        self.assertEqual([(m.jp, m.zh_cn) for m in taken], [("京香じゅりあ", "JULIA")])

    def test_one_performer_matched_on_several_pages_yields_one_mapping(self):
        """`アンナ` 命中了三页。三条映射进去，落库时只会变成一次「没有唯一映射」。"""
        self.write({"current_name": "アンナ", "adopt": "Anna", "verdict": "改艺名"},
                   {"current_name": "アンナ", "adopt": "安娜", "verdict": "ok"},
                   {"current_name": "アンナ", "adopt": "ANNA", "verdict": "改艺名"})
        taken = self.module.read_mapping_csv(self.path, ["ok", "改艺名"])
        self.assertEqual([(m.jp, m.zh_cn) for m in taken], [("アンナ", "安娜")])

    def test_a_latin_current_name_is_the_stage_name_not_a_romanization(self):
        """XML 那份偶尔把罗马字填进 `zh_cn`；javdb 的现名栏写 `JULIA` 时那就是艺名。"""
        self.write({"current_name": "京香じゅりあ", "adopt": "JULIA", "verdict": "改艺名"})
        taken = self.module.read_mapping_csv(self.path, ["改艺名"])
        self.assertTrue(taken[0].latin_is_a_stage_name)

    def test_a_row_with_nothing_to_adopt_is_not_a_mapping(self):
        """判定收了但这一行没给出可用的名字，落库时就是把规范名改成空。"""
        self.write({"current_name": "長谷川るい", "adopt": "", "verdict": "ok"})
        self.assertEqual(self.module.read_mapping_csv(self.path), [])

    def test_the_page_id_becomes_the_provenance_key(self):
        """回溯「这个中文名是谁写的」时，要指得回具体那一页。"""
        self.write({"current_name": "深田えいみ", "adopt": "深田咏美", "verdict": "ok",
                    "actor_id": "A1"})
        self.assertEqual(self.module.read_mapping_csv(self.path)[0].key, "javdb:A1")

    def test_the_index_points_back_into_the_returned_list(self):
        """`collect` 拿 `index` 当下标回查。跳过的行也算数就会指到别人身上。"""
        self.write({"current_name": "跳过", "adopt": "", "verdict": "同形（站上只有日文名）"},
                   {"current_name": "深田えいみ", "adopt": "深田咏美", "verdict": "ok",
                    "actor_id": "A1"})
        mappings = self.module.read_mapping_csv(self.path)
        self.assertEqual(mappings[mappings[0].index].jp, "深田えいみ")

    def test_a_japanese_glyph_left_by_the_traditional_conversion_is_normalized(self):
        """opencc 转的是繁简，不管日本字形：`滝` 中文写 `泷`。字形不需要外部证据。"""
        self.write({"current_name": "滝田あゆ", "adopt": "滝田亚由", "verdict": "ok",
                    "actor_id": "A2"})
        self.assertEqual(self.module.read_mapping_csv(self.path)[0].zh_cn, "泷田亚由")


if __name__ == "__main__":
    unittest.main()
