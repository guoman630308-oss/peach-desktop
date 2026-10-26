"""厂牌名录采集：对上账本的那些必须是真的对上了。

这份复核件的失败模式不是报错，是「看着满满当当、一条都不能用」——匹配放宽一点，
351 家会全部对上同一家，人翻到第十行才发现不对。所以这里守的主要是匹配的下限。
另一半是每个名录自己的取条目：三个名录的页面形态互不相同，认错了就是把站头的
自家标识当成某个厂牌的。
"""
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load_module():
    sys.path.insert(0, str(REPO / "src"))
    spec = importlib.util.spec_from_file_location(
        "harvest_maker_directories", REPO / "scripts" / "harvest_maker_directories.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entry(slug: str, alt: str) -> str:
    return (f'<img src="https://static.mgstage.com/mgs/img/pc/{slug}.gif" '
            f'width="180" height="54" alt="{alt}">')


class FakeSite:
    """按页返回预置 HTML；没预置的页返回空串，等同那一页没有厂牌。"""

    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.asked: list[str] = []

    def get(self, url: str) -> str:
        self.asked.append(url)
        return self.pages.get(url.rsplit("=", 1)[-1], "")


class FoldTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_a_purely_japanese_name_folds_to_an_empty_roman_form(self):
        """纯日文名没有可比的罗马字形，折出来就该是空串。

        这条本身不是缺陷，下一条测的才是：调用处必须把空串当不可比。
        """
        self.assertEqual(self.module.fold_roman("アロマ企画"), "")
        self.assertEqual(self.module.fold_roman("Aroma Planning"), "aromaplanning")

    def test_two_unrelated_japanese_names_never_match_through_the_empty_fold(self):
        """真实事故：`fold` 把每个日文名都折成空串，于是它们互相全等。

        首轮探测因此报出 332 个「匹配」，实际只有 29 个。空键必须是不可比，
        不是一个所有人共享的桶。
        """
        by_roman = {}
        by_text = {self.module.fold_text("ヒビノ"): (1, "ヒビノ")}
        self.assertIsNone(self.module.match("aroma", "アロマ企画", by_roman, by_text))

    def test_text_folding_keeps_japanese_and_drops_only_punctuation(self):
        """`アリーナ･エンターテインメント` 与 `アリーナエンターテインメント` 是同一家。

        中黑、全角括号这类分隔符在两边写法不一，日文字符本身一个都不能丢——
        丢了就等于又造一个空串桶。
        """
        self.assertEqual(self.module.fold_text("アリーナ･エンターテインメント"),
                         self.module.fold_text("アリーナエンターテインメント"))
        self.assertNotEqual(self.module.fold_text("ヒビノ"), self.module.fold_text("いんすた"))
        self.assertNotEqual(self.module.fold_text("ヒビノ"), "")


class MatchTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.by_roman = {
            "prestige": (5630, "Prestige"),
            "aromaplanning": (5566, "Aroma Planning"),
            "hmjm": (5597, "HMJM"),
            "waapentertainment": (8555, "Waap Entertainment"),
        }
        self.by_text = {
            self.module.fold_text("Prestige"): (5630, "Prestige"),
            self.module.fold_text("ヒビノ"): (8624, "ヒビノ"),
            self.module.fold_text("hmjm"): (5597, "HMJM"),
            self.module.fold_text("ハマジム"): (5597, "HMJM"),
        }

    def test_the_slug_matches_the_canonical_name_directly(self):
        self.assertEqual(self.module.match("prestige", "プレステージ", self.by_roman, self.by_text),
                         (5630, "Prestige", "slug 归一相等"))

    def test_the_japanese_listing_name_matches_a_japanese_canonical_name(self):
        """账本里少数厂牌规范名本身就是日文，那一路只有日文名对得上。"""
        self.assertEqual(self.module.match("hibino", "ヒビノ", self.by_roman, self.by_text),
                         (8624, "ヒビノ", "日文名相等"))

    def test_the_slug_matches_a_japanese_alias(self):
        """`hmjm.gif` 的日文名是 `ハマジム`，账本规范名是 `HMJM`。

        slug 一路先命中，这条守的是它命中的确实是同一家。
        """
        self.assertEqual(self.module.match("hmjm", "ハマジム", self.by_roman, self.by_text),
                         (5597, "HMJM", "slug 归一相等"))

    def test_an_abbreviated_slug_falls_back_to_a_unique_prefix_candidate(self):
        """`waap.gif` 对 `Waap Entertainment`：slug 是缩写，只能靠前缀。

        判据写进 `how` 列，让人知道这一条比前三路弱。
        """
        self.assertEqual(self.module.match("waap", "WAAP", self.by_roman, self.by_text),
                         (8555, "Waap Entertainment", "前缀候选"))

    def test_an_ambiguous_prefix_is_not_a_candidate(self):
        """前缀撞上两家就没有候选：给人一个五五开的猜测，不如告诉他账本里没有。"""
        by_roman = dict(self.by_roman, sodcreate=(1, "SOD Create"), sodstar=(2, "SOD Star"))
        self.assertIsNone(self.module.match("sod", "SOD", by_roman, {}))

    def test_a_short_slug_never_reaches_the_prefix_path(self):
        """三个字母以下的 slug 前缀命中太廉价，`vip` 会撞上一串无关厂牌。"""
        self.assertIsNone(self.module.match("doc", "DOC", {"documentary": (9, "Documentary")}, {}))

    def test_a_maker_absent_from_the_ledger_returns_no_match(self):
        """322 家对不上不是失败。它们照样进复核件，是入库时的现成来源。"""
        self.assertIsNone(self.module.match("kmp", "ケイ･エム･プロデュース", self.by_roman, self.by_text))


class ListingTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_navigation_images_are_not_makers(self):
        """50 音导航条和厂牌字标是同一批 `<img>`，只能按文件名排掉。"""
        site = FakeSite({"a": entry("maker_01", "あ行") + entry("50kensaku", "50音検索")
                         + entry("aroma", "アロマ企画")})
        self.assertEqual(self.module.makers(site), {
            "aroma": ("アロマ企画", "https://static.mgstage.com/mgs/img/pc/aroma.gif")})

    def test_the_exclusive_badge_is_stripped_from_the_maker_name(self):
        """`【独占】` 是 MGStage 的销售身份，不是厂牌名的一部分。"""
        site = FakeSite({"a": entry("luxutv", "【独占】ラグジュTV")})
        self.assertEqual(self.module.makers(site)["luxutv"][0], "ラグジュTV")

    def test_a_maker_listed_on_two_pages_appears_once(self):
        """`osusume` 是站方推荐位，和音节页整片重合，靠 slug 去重。"""
        site = FakeSite({"osusume": entry("prestige", "プレステージ"),
                         "ha": entry("prestige", "プレステージ")})
        self.assertEqual(list(self.module.makers(site)), ["prestige"])

    def test_every_syllabary_page_is_requested(self):
        site = FakeSite({})
        self.module.makers(site)
        self.assertEqual(len(site.asked), len(self.module.PAGES))


class OnePageSite:
    """只有一页的名录用的替身：不管问哪个地址都回同一份内容。"""

    def __init__(self, body: str):
        self.body = body
        self.asked: list[str] = []

    def get(self, url: str) -> str:
        self.asked.append(url)
        return self.body


class PrestigeListingTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def payload(self, *makers) -> str:
        return json.dumps({"list": list(makers), "count": len(makers)})

    def test_the_code_name_is_the_slug_and_the_media_path_is_the_logo(self):
        site = OnePageSite(self.payload(
            {"codeName": "nanpatv", "name": "ナンパTV",
             "media": {"path": "maker/banner-nanpa.jpg"}}))
        self.assertEqual(self.module.prestige_makers(site), {
            "nanpatv": ("ナンパTV",
                        "https://www.prestige-av.com/api/media/maker/banner-nanpa.jpg")})

    def test_a_maker_without_an_image_is_left_out(self):
        """没有 `media` 的那条给不出字标地址，出在复核件上只会是一行空证据。"""
        site = OnePageSite(self.payload(
            {"codeName": "kanbi", "name": "KANBi", "media": None},
            {"codeName": "magic", "name": "MAGIC", "media": {"path": "maker/m.jpg"}}))
        self.assertEqual(list(self.module.prestige_makers(site)), ["magic"])


class KmpListingTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_the_slug_comes_from_the_label_path(self):
        site = OnePageSite(
            '<img src="/img2018/label/bazooka/logo.svg" alt="BAZOOKA">')
        self.assertEqual(self.module.kmp_makers(site), {
            "bazooka": ("BAZOOKA",
                        "https://www.km-produce.com/img2018/label/bazooka/logo.svg")})

    def test_the_site_own_marks_are_not_labels(self):
        """站头和页脚那两张是 KMP 自己的标识，和 label 图混在同一批 `<img>` 里。"""
        site = OnePageSite(
            '<img src="/wp-content/themes/km-produce-v4/img/logo.svg" alt="KMP">'
            '<img src="/img2018/label/real/logo.svg" alt="REAL">')
        self.assertEqual(list(self.module.kmp_makers(site)), ["real"])

    def test_a_label_without_a_slug_is_keyed_by_its_japanese_name(self):
        """`file/label_<时间戳>` 这一路没有罗马字 slug，只能靠日文名对账。

        拿路径里那串时间戳当键的话，每次厂牌换图它就变成另一家。
        """
        site = OnePageSite('<img src="/file/label_1560389959.svg" alt="ヒメゴト">')
        self.assertEqual(list(self.module.kmp_makers(site)), ["ヒメゴト"])


class DirectoryRegistryTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_every_directory_has_its_own_key(self):
        keys = [entry.key for entry in self.module.DIRECTORIES]
        self.assertEqual(sorted(keys), sorted(set(keys)))

    def test_only_mgstage_carries_the_age_gate_cookie(self):
        """年龄门是 MGStage 一家的要求。把它的 cookie 带到别家去，等于替对方
        声明它也有一道门——而那家并没有。
        """
        gated = {entry.key for entry in self.module.DIRECTORIES if entry.cookies}
        self.assertEqual(gated, {"mgstage"})


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def ledger(self, path: Path):
        connection = sqlite3.connect(path)
        connection.executescript(
            "CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT);"
            "CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT);"
            "CREATE TABLE asset_entity(asset_id INTEGER, entity_id INTEGER);")
        return connection

    def test_only_studio_entities_are_reconciled(self):
        """演员和厂牌同住 `entity` 表。不按 kind 过滤，`Jackson` 会对上一个同名演员。"""
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw).resolve() / "ledger.db"
            connection = self.ledger(path)
            connection.executemany(
                "INSERT INTO entity(id,kind,canonical_name) VALUES(?,?,?)",
                [(1, "studio", "Jackson"), (2, "performer", "Prestige")])
            connection.commit()
            by_roman, _, _ = self.module.ledger_studios(connection)
            connection.close()
            self.assertEqual(by_roman.get("jackson"), (1, "Jackson"))
            self.assertNotIn("prestige", by_roman)

    def test_asset_counts_come_from_studio_links_only(self):
        """`assets` 列是复核时的优先级：挂着 425 部片的厂牌值得先看。"""
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw).resolve() / "ledger.db"
            connection = self.ledger(path)
            connection.executemany(
                "INSERT INTO entity(id,kind,canonical_name) VALUES(?,?,?)",
                [(1, "studio", "Prestige"), (2, "performer", "誰か")])
            connection.executemany("INSERT INTO asset_entity(asset_id,entity_id) VALUES(?,?)",
                                   [(10, 1), (11, 1), (10, 2)])
            connection.commit()
            _, _, counts = self.module.ledger_studios(connection)
            connection.close()
            self.assertEqual(counts, {1: 2})


class LogoStateTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_the_state_column_tells_a_gap_apart_from_a_replacement(self):
        """补空和替换是两种决定，复核件上必须分得开。"""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            (root / "Prestige.icon.img").write_bytes(b"x")
            (root / "Prestige.img").write_bytes(b"x")
            self.assertEqual(self.module.logo_state("Prestige", root), "icon+主位")
            self.assertEqual(self.module.logo_state("Flower", root), "无图")

    def test_a_missing_logo_root_reports_no_image_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as raw:
            self.assertEqual(self.module.logo_state("Prestige", Path(raw).resolve() / "nope"), "无图")


if __name__ == "__main__":
    unittest.main()
