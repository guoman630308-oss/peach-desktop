"""补女优别名后继：两站证据确定的艺名直接登记，不确定的不写，写下的能整批撤回（ADR-0055）。

全程临时账本；两站的取页层是真的（缓存、冷却、撞墙判定都走一遍），只把最底下的传输换成
按地址回页面的替身，不联网。页面是 minnano-av 699633 与 av_neme「雲母そら」「有本紗世」的
真实页面裁剪而成，只留解析会读到的那几块：夹具内联在这里而不是另开目录，新目录映射不到
任何测试域，会让每次改动都跑全量。
"""
import contextlib
import importlib.util
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

from peach import minnano_av
from peach import performer_alias_followup as alias
from peach.entities import normalize_entity_name
from peach.followups import Attempts, attempts_root
from peach.http import HttpResponse
from peach.repository import LedgerDatabase
from peach.review_csv import read_rows
from peach.scraping_access import paused_until
from peach.sources.base import Page
from peach.sources.seesaa import person_profile, renamed_to, search_results, split_names
from support.ledger import fresh_ledger

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-09-24T00:00:00.000Z"
PROFILE_URL = f"{minnano_av.SITE}actress699633.html"

MINNANO_PROFILE = """<!doctype html><html lang="ja"><head>
<link rel="canonical" href="https://www.minnano-av.com/actress699633.html">
</head><body>
<h1>雲母そら<span>きららそら / Kirara Sora</span></h1>
<div class="act-profile">
<table width="100%" cellspacing="0" cellpadding="0" border="0">
<tr><td><h2>雲母そら （きららそら / Kirara Sora）</h2></td></tr>
<tr><td><span>別名</span><p>未来ちゃん(FC2) （みらいちゃん / ）</p></td></tr>
<tr><td><span>別名</span><p>いちかちゃん(FC2) （いちかちゃん / ）</p></td></tr>
<tr><td><span>別名</span><p>雫つむぎ(FC2) （しずくつむぎ / SizukuTsumugi）</p></td></tr>
<tr><td><span>別名</span><p>神山ももか （かみやまももか / KamiyamaMomoka）</p></td></tr>
<tr><td><span>別名</span><p>朝霧いのり(着エロ) （あさぎりいのり / Asagiri Inori）</p></td></tr>
<tr><td><span>別名</span><p>神山ももか(天然むすめ) （かみやまももか / Kamiyama Momoka）</p></td></tr>
<tr><td><span>別名</span><p>雲母そら （きららそら / Kirara Sora）</p></td></tr>
<tr><td><span>別名</span><p>美雲そら【旧名】 （みくもそら / Mikumo Sora）</p></td></tr>
<tr><td><span>所属事務所</span><p><a href="actress_list.php?production=282">Bambi Promotion</a></p></td></tr>
</table></div>
<div class="comment"><table><tr><td><span>別名</span><p>コメント欄の誰か</p></td></tr></table></div>
</body></html>"""

MINNANO_SEARCH = """<!doctype html><html lang="ja"><body>
<table width="100%" cellspacing="0" cellpadding="0" border="0" class="tbllist actress">
<tr><th class="t9"></th><th class="t9" align="left">名前</th></tr>
<tr><td><a href="actress699633.html"><img alt="神山ももか"></a></td>
<td class="details"><h2 class="ttl"><a href="actress699633.html">神山ももか</a></h2>
<p class="furi">（雲母そら）</p></td></tr>
<tr><td><a href="actress699633.html"><img alt="神山ももか(天然むすめ)"></a></td>
<td class="details"><h2 class="ttl"><a href="actress699633.html">神山ももか(天然むすめ)</a></h2>
<p class="furi">（雲母そら）</p></td></tr>
</table>
<div class="recommend"><a href="actress111111.html">おすすめの誰か</a></div>
</body></html>"""

MINNANO_EMPTY = """<!doctype html><html lang="ja"><body>
<table class="tbllist actress"><tr><th>名前</th></tr></table></body></html>"""


def wiki_url(title: str) -> str:
    return alias.AV_NEME_ROOT + "d/" + quote(title.encode("euc_jp"))


def wiki_search_url(key: str) -> str:
    return alias.AV_NEME_ROOT + "search?keywords=" + quote(key.encode("euc_jp"))


def wiki_page(section: str, rest: str = "") -> str:
    return ('<html><body><div id="page-body"><div class="user-area">'
            f'{section}{rest}</div></div></body></html>')


def wiki_section(title: str, body: str, level: int = 1) -> str:
    return (f'<div class="wiki-section-{level}"><div class="title-{level}"><h3>{title}</h3></div>'
            f'<div class="wiki-section-body-{level}">{body}</div></div>')


AV_NEME_KIRARA = wiki_page(
    wiki_section("プロフィール", """<pre class="BOX">
名前(女優名)：雲母そら（きららそら）
旧名義&amp;別名：美雲そら（みくもそら）・朝霧いのり（あさぎりいのり）・雫つむぎ・神山ももか
生年月日：
</pre>"""),
    # 作品小节里的名义是那一部片给她起的，不是她的艺名，不读。
    wiki_section("smuc170| 素人ムクムク-夢中-",
                 "別名：みく 18歳 本屋の店員<br />仮名：そらちゃん<br />", level=3))

AV_NEME_MOVED = wiki_page(wiki_section(
    "女優名(名前)変更", "女優名が【神山ももか】から【美雲そら】へ変更になりました。"))

AV_NEME_ARIMOTO = wiki_page(wiki_section("プロフィール", (
    "<table><tbody><tr><th><b><div>ナンバー</div></b></th><td>347</td></tr>"
    "<tr><th><b><div>名前(別名)</div></b></th>"
    "<td>有本紗世（ありもとさよ）／有本沙耶／有本紗也／元木小夜（もときさよ）</td></tr>"
    "</tbody></table>")))


def wiki_search(*hits) -> str:
    """检索结果页。每条是页名，或 (页名, 摘要)。"""
    blocks = []
    for hit in hits:
        title, text = hit if isinstance(hit, tuple) else (hit, "…")
        blocks.append(f'<div class="body"><h3 class="keyword"><a href="{wiki_url(title)}">{title}'
                      f'</a></h3><p class="text">{text}</p></div>')
    return f'<html><body><div class="result-box">{"".join(blocks)}</div></body></html>'


#: `叶芽ゆきな` 在 av_neme 的真实检索结果：五页厂牌页与月份页排在她的人物页 `桜美ゆきな` 前面。
LISTING_TEXT = "&amp;size(18){''名前(女優名)''：[[ 叶芽ゆきな ]]} 仮名：[[ゆきな]]"
YUKINA_SEARCH = (
    ("ガチ素人", LISTING_TEXT), ("Girl’s Blue", LISTING_TEXT), ("2024年11月", LISTING_TEXT),
    ("ぐちょぐちょ素人娘", LISTING_TEXT), ("2020年2月", "完璧な男 叶芽ゆきな"),
    ("桜美ゆきな", "ゆきな（さくらみゆきな） 旧名義&amp;別名：叶芽ゆきな（かなめゆきな）・夢原まみ・尾上さら"
                  " 生年月日：1999年1月8日"),
    ("叶芽ゆきな", "女優名が【叶芽ゆきな】から【桜美ゆきな】へ変更になりました。"),
)
AV_NEME_SAKURAMI = wiki_page(wiki_section("プロフィール", """<pre class="BOX">
名前(女優名)：桜美ゆきな（さくらみゆきな）
旧名義&amp;別名：叶芽ゆきな（かなめゆきな）・夢原まみ・尾上さら
生年月日：1999年1月8日
</pre>"""))
AV_NEME_LISTING = wiki_page(wiki_section("gsiro018| ガチ素人", "名前(女優名)：叶芽ゆきな<br />", level=3))


class Transport:
    """按地址回页面的传输替身；没登记的检索回一张空结果页，其余回 404。"""

    def __init__(self, pages: dict, empty_search: bytes):
        self.pages, self.empty_search, self.calls = pages, empty_search, []

    def __call__(self, request, _timeout, _limit):
        self.calls.append(request.url)
        if request.url in self.pages:
            status, body = self.pages[request.url]
        elif "search" in request.url:
            status, body = 200, self.empty_search
        else:
            status, body = 404, b""
        return HttpResponse(status, {}, body, request.url)

    def close(self):
        pass


class NoWait:
    def wait(self, _url):
        pass


CMADB_ARTICLE = ('<html><body><script type="application/json">{"component": "Articles/Show",'
                 ' "version": "v1", "props": {"article": {"video_id": "%s"}}}</script></body></html>')


class Fc2cmadbTransport:
    """fc2cmadb 替身：同一个地址，不带 `X-Inertia` 回作品页，带了回女优栏。"""

    def __init__(self, listed: dict[str, list[dict]], status: int = 200):
        self.listed, self.status, self.calls = listed, status, []

    def __call__(self, request, _timeout, _limit):
        self.calls.append((request.url, bool(request.headers.get("X-Inertia"))))
        video = request.url.rsplit("/", 1)[-1]
        if self.status != 200:
            return HttpResponse(self.status, {}, b"", request.url)
        if video not in self.listed:
            return HttpResponse(404, {}, b"", request.url)
        if not request.headers.get("X-Inertia"):
            return HttpResponse(200, {}, (CMADB_ARTICLE % video).encode("utf-8"), request.url)
        body = json.dumps({"component": "Articles/Show", "props": {"actresses": self.listed[video]}},
                          ensure_ascii=False)
        return HttpResponse(200, {}, body.encode("utf-8"), request.url)

    def close(self):
        pass


class Case(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.db = fresh_ledger(self.root)
        self.database = LedgerDatabase(self.db)
        self.generated = self.root / "generated"
        self.cooldown = self.root / "secrets"
        self.busted = []
        self.contract = SimpleNamespace(
            database=self.database, candidate_root=self.generated,
            follow_secrets_root=self.cooldown, cache_bust=lambda: self.busted.append(1))
        self.minnano = Transport({
            minnano_av.search_url("神山ももか"): (200, MINNANO_SEARCH.encode("utf-8")),
            PROFILE_URL: (200, MINNANO_PROFILE.encode("utf-8")),
        }, MINNANO_EMPTY.encode("utf-8"))
        self.av_neme = Transport({
            wiki_search_url("神山ももか"): (200, wiki_search("雲母そら", "神山ももか").encode("euc_jp")),
            wiki_url("雲母そら"): (200, AV_NEME_KIRARA.encode("euc_jp")),
            wiki_url("神山ももか"): (200, AV_NEME_MOVED.encode("euc_jp")),
        }, wiki_search().encode("euc_jp"))
        self.fc2cmadb = Fc2cmadbTransport({"1234567": [
            {"id": 9186, "name": "神山ももか",
             "alias_name": "たぬき顔サラサラ黒髪ロング ちっぱいリクルーター"}]})

    def entity(self, name: str, *aliases: str, kind: str = "performer") -> int:
        with self.database.write_transaction(notify=False) as connection:
            cursor = connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES(?,?,?,?,?)", (kind, name, normalize_entity_name(name), STAMP, STAMP))
            entity_id = int(cursor.lastrowid)
            for written in aliases:
                connection.execute(
                    "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
                    " VALUES(?,?,?,'manual')", (entity_id, written, normalize_entity_name(written)))
        return entity_id

    def work(self, asset_id: int, *performers: int, code: str | None = None) -> None:
        with self.database.write_transaction(notify=False) as connection:
            connection.execute("INSERT INTO asset(id,location,path,name,medium,code)"
                               " VALUES(?,'local',?,?,'video',?)",
                               (asset_id, f"R:\\media\\{asset_id}.mp4", f"{asset_id}.mp4", code))
            for entity_id in performers:
                connection.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source)"
                                   " VALUES(?,?,'performer','test')", (asset_id, entity_id))

    def aliases(self, entity_id: int) -> dict[str, str]:
        with self.database.read_connection() as connection:
            return {str(row[0]): str(row[1]) for row in connection.execute(
                "SELECT alias,source FROM entity_alias WHERE entity_id=?", (entity_id,))}

    def sites(self) -> dict:
        cache = self.generated / "provider-cache"
        return {alias.MINNANO: alias.MinnanoPages(cache / "minnano", self.cooldown, self.minnano,
                                                  limiter=NoWait()),
                alias.AV_NEME: alias.WikiSitePages(cache / "seesaa", self.cooldown, self.av_neme),
                alias.FC2CMADB: alias.Fc2cmadbPages(cache / "fc2cmadb", self.cooldown, self.fc2cmadb,
                                                    limiter=NoWait())}

    def run_followup(self, entity_id: int, run_id: int = 7) -> dict:
        handle = SimpleNamespace(run_id=run_id, progress=lambda **_kwargs: None)
        with mock.patch.object(alias, "open_sites", side_effect=lambda _contract: self.sites()), \
                mock.patch.object(alias, "_LIMITER", NoWait()):
            return alias.run(self.contract, alias.followup_key(entity_id), handle)

    def review(self) -> list[dict]:
        return read_rows(self.generated / alias.REVIEW_FILE)


class ParsingTests(unittest.TestCase):
    def test_minnano_profile_reads_only_the_alias_rows_of_her_own_table(self):
        main, names = minnano_av.profile_names(MINNANO_PROFILE)
        self.assertEqual(main, "雲母そら")
        self.assertEqual(len(names), 8)
        self.assertNotIn("コメント欄の誰か", names)
        self.assertEqual(minnano_av.profile_names(MINNANO_SEARCH), ("", []))

    def test_minnano_search_counts_people_not_rows(self):
        hits = minnano_av.search_hits(MINNANO_SEARCH)
        self.assertEqual({found for found, _shown in hits}, {"699633"})
        self.assertEqual(len(hits), 2)

    def test_av_neme_reads_the_name_fields_in_both_layouts(self):
        self.assertEqual(person_profile(Page(wiki_url("雲母そら"), AV_NEME_KIRARA.encode("euc_jp"))),
                         ("雲母そら", ["雲母そら", "美雲そら", "朝霧いのり", "雫つむぎ", "神山ももか"]))
        self.assertEqual(person_profile(Page(wiki_url("有本紗世"), AV_NEME_ARIMOTO.encode("euc_jp"))),
                         ("有本紗世", ["有本紗世", "有本沙耶", "有本紗也", "元木小夜"]))

    def test_av_neme_a_page_not_named_after_her_is_not_her_page(self):
        body = AV_NEME_KIRARA.encode("euc_jp")
        self.assertEqual(person_profile(Page(wiki_url("2023年03月"), body)), ("", []))
        self.assertEqual(person_profile(Page(wiki_url("神山ももか"),
                                             AV_NEME_MOVED.encode("euc_jp"))), ("", []))

    def test_a_renamed_page_names_her_current_page(self):
        self.assertEqual(renamed_to(Page(wiki_url("神山ももか"), AV_NEME_MOVED.encode("euc_jp"))),
                         "美雲そら")
        self.assertEqual(renamed_to(Page(wiki_url("雲母そら"), AV_NEME_KIRARA.encode("euc_jp"))), "")

    def test_av_neme_search_lists_hits_in_order(self):
        body = wiki_search("雲母そら", ("神山ももか", "旧名義：雲母そら")).encode("euc_jp")
        self.assertEqual([(title, text) for _url, title, text in search_results(body)],
                         [("雲母そら", "…"), ("神山ももか", "旧名義：雲母そら")])

    def test_profile_pages_are_read_before_label_and_month_pages(self):
        hits = search_results(wiki_search(*YUKINA_SEARCH).encode("euc_jp"))
        self.assertEqual(alias.search_order(hits),
                         [wiki_url("桜美ゆきな"), wiki_url("叶芽ゆきな"), wiki_url("ガチ素人"),
                          wiki_url("Girl’s Blue"), wiki_url("ぐちょぐちょ素人娘")])

    def test_a_middle_dot_between_katakana_stays_inside_one_name(self):
        self.assertEqual(split_names("キラ・クィーン・美雲そら"), ["キラ・クィーン", "美雲そら"])

    def test_a_reading_and_an_ampersand_both_end_a_name(self):
        """篠田ゆう那页：读音后面没写分隔符，`&` 连着同一渠道的两个艺名。"""
        self.assertEqual(
            split_names("篠崎ゆう子（しのざきゆうこ）橋本真紀&桧山彩音（舞ワイフ名義）・高木早希"
                        "（ラグジュTV名義）・城田優子"),
            ["篠崎ゆう子", "橋本真紀", "桧山彩音", "高木早希", "城田優子"])

    def test_names_that_would_hit_someone_else_are_rejected(self):
        for name in ("そら", "みく", "Kirara Sora", "いちかちゃん", "未来ちゃん", "名前不明", "145cm色白お嬢様",
                     "舞香", "茜", "舞 香"):
            self.assertTrue(alias.rejection(alias.clean(name)), name)
        for name in ("雫つむぎ(FC2)", "美雲そら【旧名】", "有本紗世（ありもとさよ）", "キラ・クィーン",
                     "月島舞香", "伊吹彩"):
            self.assertEqual(alias.rejection(alias.clean(name)), "", name)


class LandingTests(Case):
    def test_her_other_stage_names_are_registered_from_both_sites(self):
        momoka = self.entity("神山ももか")
        summary = self.run_followup(momoka)
        self.assertEqual(summary["outcome"], "登记 4 个别名")
        batch = f"{alias.SOURCE}@7"
        self.assertEqual(self.aliases(momoka), {
            "雲母そら": batch, "雫つむぎ": batch, "朝霧いのり": batch, "美雲そら": batch})
        self.assertTrue(summary["sites"][alias.MINNANO].startswith("命中 " + PROFILE_URL))
        self.assertTrue(summary["sites"][alias.AV_NEME].startswith("命中 " + wiki_url("雲母そら")))
        self.assertEqual(self.busted, [1])
        actions = {(row["site"], row["alias"]): row["action"] for row in self.review()}
        self.assertEqual(actions[(alias.MINNANO, "神山ももか")], alias.HAVE)
        self.assertEqual(actions[(alias.AV_NEME, "雲母そら")], alias.HAVE)

    def test_amateur_labels_and_one_off_names_are_not_registered(self):
        momoka = self.entity("神山ももか")
        self.run_followup(momoka)
        names = self.aliases(momoka)
        for name in ("未来ちゃん", "いちかちゃん", "みく", "そらちゃん"):
            self.assertNotIn(name, names)
        skipped = {row["alias"]: row["detail"] for row in self.review()
                   if row["action"] == alias.SKIP}
        self.assertEqual(skipped, {"未来ちゃん": "一次性称呼", "いちかちゃん": "一次性称呼"})

    def test_a_name_another_entity_already_uses_merges_the_two(self):
        """两站名字栏把 `神山ももか` 与 `雫つむぎ` 列成同一个人：作品一样多、页上主名都不是她们，先登记的留下。"""
        momoka = self.entity("神山ももか")
        other = self.entity("雫つむぎ")
        summary = self.run_followup(momoka)
        self.assertEqual(summary["outcome"], "雫つむぎ 并入 神山ももか")
        self.assertEqual(summary["merged"]["into"], momoka)
        self.assertEqual(summary["merged"]["from"], other)
        self.assertNotIn("taken", summary)
        self.assertFalse(self.exists(other))
        names = self.aliases(momoka)
        self.assertEqual(names["雫つむぎ"], f"merge:{alias.SOURCE}@7")
        self.assertEqual(names["雲母そら"], f"{alias.SOURCE}@7")
        merged = [row for row in self.review() if row["action"] == alias.MERGE]
        self.assertEqual({row["alias"] for row in merged}, {"雫つむぎ"})
        self.assertEqual({row["site"] for row in merged}, {alias.MINNANO, alias.AV_NEME})
        self.assertIn(f"雫つむぎ（实体 {other}）并入 神山ももか（实体 {momoka}）", merged[0]["detail"])
        self.assertEqual(merged[0]["batch"], f"{alias.SOURCE}@7")
        self.assertEqual(len(self.merge_backups()), 1)
        self.assertTrue(self.merge_backups()[0].endswith(f"-{other}.db"))
        with self.database.read_connection() as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_an_unrelated_orphan_row_does_not_block_a_merge(self):
        """库里早有一条与实体无关的违规行时，合并照样落；那条行原样留着。"""
        momoka = self.entity("神山ももか")
        other = self.entity("雫つむぎ")
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "INSERT INTO follow_item(id,source_id,external_id,title,release_key,"
                "first_seen_at,last_seen_at) VALUES(1,999,'x','orphan','x',?,?)", (STAMP, STAMP))
        summary = self.run_followup(momoka)
        self.assertEqual(summary["merged"]["from"], other)
        self.assertFalse(self.exists(other))
        with self.database.read_connection() as connection:
            self.assertEqual([tuple(row)[:3] for row in connection.execute("PRAGMA foreign_key_check")],
                             [("follow_item", 1, "follow_source")])

    def exists(self, entity_id: int) -> bool:
        with self.database.read_connection() as connection:
            return connection.execute("SELECT 1 FROM entity WHERE id=?", (entity_id,)).fetchone() is not None

    def merge_backups(self) -> list[str]:
        return sorted(path.name for path in self.db.parent.glob("ledger.pre-alias-merge-*.db"))

    def test_the_side_with_more_works_survives_a_merge(self):
        momoka = self.entity("神山ももか")
        other = self.entity("雫つむぎ")
        self.work(1, momoka)
        self.work(2, other)
        self.work(3, other)
        summary = self.run_followup(momoka)
        self.assertEqual(summary["outcome"], "神山ももか 并入 雫つむぎ")
        self.assertFalse(self.exists(momoka))
        names = self.aliases(other)
        self.assertEqual(names["神山ももか"], f"merge:{alias.SOURCE}@7")
        # 这一轮先落在她名下的写法跟着并过去。
        self.assertEqual(names["雲母そら"], f"{alias.SOURCE}@7")
        with self.database.read_connection() as connection:
            self.assertEqual(alias._works(connection, other), 3)
        self.assertEqual(self.run_followup(momoka, run_id=8)["outcome"], "档案已不存在")

    def test_the_page_main_name_breaks_a_tie(self):
        momoka = self.entity("神山ももか")
        kirara = self.entity("雲母そら")
        summary = self.run_followup(momoka)
        self.assertEqual(summary["outcome"], "神山ももか 并入 雲母そら")
        self.assertFalse(self.exists(momoka))
        self.assertIn("神山ももか", self.aliases(kirara))

    def test_two_other_entities_on_one_page_are_left_for_review(self):
        momoka = self.entity("神山ももか")
        first = self.entity("雫つむぎ")
        second = self.entity("美雲そら")
        summary = self.run_followup(momoka)
        self.assertEqual(summary["taken"], 4)
        self.assertNotIn("merged", summary)
        self.assertTrue(self.exists(first) and self.exists(second))
        taken = [row for row in self.review() if row["action"] == alias.TAKEN]
        self.assertEqual({row["alias"] for row in taken}, {"雫つむぎ", "美雲そら"})
        self.assertIn(f"实体 {first}", next(row["detail"] for row in taken if row["alias"] == "雫つむぎ"))
        self.assertEqual(self.merge_backups(), [])

    def test_a_short_single_name_is_neither_searched_nor_registered(self):
        sora = self.entity("そら")
        with self.database.read_connection() as connection:
            self.assertFalse(alias.has_entry(connection, sora))
            self.assertEqual(alias.plan(connection, since_entity_id=0), [])
        momoka = self.entity("神山ももか")
        with self.database.write_transaction(notify=False) as connection:
            rows = alias.land(connection, momoka, "神山ももか", alias.AV_NEME, "page",
                              ["そら", "Kirara Sora", "美雲そら"], "batch@1")
        self.assertEqual([row["action"] for row in rows], [alias.SKIP, alias.SKIP, alias.WRITE])
        self.assertEqual(self.aliases(momoka), {"美雲そら": "batch@1"})

    def test_the_page_named_after_her_is_read_before_any_search(self):
        """站上人物页就叫她的名字，直取一次；改名页跟一跳到现页。两种都不检索。"""
        momoka = self.entity("神山ももか")
        self.assertEqual(self.run_followup(momoka)["outcome"], "登记 4 个别名")
        # 改名页指向的 `美雲そら` 站上没有这一页：回到检索，站上没有的页记下，下一轮不再问。
        self.assertEqual(self.av_neme.calls[:3], [wiki_url("神山ももか"), wiki_url("美雲そら"),
                                                  wiki_search_url("神山ももか")])
        asked = len(self.av_neme.calls)
        self.assertEqual(self.run_followup(momoka, run_id=8)["outcome"], "没有新写法")
        self.assertEqual(len(self.av_neme.calls), asked)
        minami = self.entity("初川みなみ")
        self.av_neme.pages[wiki_url("初川みなみ")] = (200, wiki_page(wiki_section(
            "プロフィール", '<pre class="BOX">\n名前(女優名)：初川みなみ（はつかわみなみ）\n生年月日：\n</pre>'
        )).encode("euc_jp"))
        summary = self.run_followup(minami)
        self.assertEqual(summary["outcome"], "没有新写法")
        self.assertTrue(summary["sites"][alias.AV_NEME].startswith("命中 " + wiki_url("初川みなみ")))
        self.assertNotIn(wiki_search_url("初川みなみ"), self.av_neme.calls)

    def test_her_profile_page_is_found_behind_five_archive_pages(self):
        """站上没有叫她名字的页；检索结果前五页是厂牌页与月份页，只读前三页就永远读不到她那一页。"""
        yukina = self.entity("叶芽ゆきな")
        self.av_neme.pages[wiki_search_url("叶芽ゆきな")] = (
            200, wiki_search(*YUKINA_SEARCH[:6]).encode("euc_jp"))
        self.av_neme.pages[wiki_url("桜美ゆきな")] = (200, AV_NEME_SAKURAMI.encode("euc_jp"))
        for title, _text in YUKINA_SEARCH[:5]:
            self.av_neme.pages[wiki_url(title)] = (200, AV_NEME_LISTING.encode("euc_jp"))
        summary = self.run_followup(yukina)
        self.assertEqual(summary["outcome"], "登记 3 个别名")
        self.assertEqual(set(self.aliases(yukina)), {"桜美ゆきな", "夢原まみ", "尾上さら"})
        self.assertIn(wiki_url("叶芽ゆきな"), self.av_neme.calls)
        self.assertNotIn(wiki_url("2020年2月"), self.av_neme.calls)

    def test_a_page_that_does_not_list_her_is_not_used(self):
        stranger = self.entity("佐々木ゆうか")
        self.minnano.pages[minnano_av.search_url("佐々木ゆうか")] = (
            200, MINNANO_PROFILE.encode("utf-8"))
        summary = self.run_followup(stranger)
        self.assertEqual(summary["outcome"], "两站都没对上她")
        self.assertEqual(self.aliases(stranger), {})

    def test_a_known_minnano_id_is_read_without_searching(self):
        momoka = self.entity("神山ももか")
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id)"
                " VALUES(?,?,'performer','699633')", (momoka, alias.MINNANO))
        self.run_followup(momoka)
        self.assertNotIn(minnano_av.search_url("神山ももか"), self.minnano.calls)
        self.assertIn("雲母そら", self.aliases(momoka))

    def test_a_renamed_or_missing_entity_is_not_written(self):
        momoka = self.entity("神山ももか")
        with self.database.write_transaction(notify=False) as connection:
            stale = alias.land(connection, momoka, "別人", alias.MINNANO, "page", ["雲母そら"], "b")
            gone = alias.land(connection, 999, "神山ももか", alias.MINNANO, "page", ["雲母そら"], "b")
        self.assertEqual([row["action"] for row in stale + gone], [alias.STALE, alias.GONE])
        self.assertEqual(self.aliases(momoka), {})


class Fc2cmadbTests(Case):
    NICKNAME = "たぬき顔サラサラ黒髪ロング"

    def nicknamed(self, *listed: dict) -> int:
        """账本里只有卖家那句称呼的一位，挂着一部 FC2 作品。"""
        her = self.entity(self.NICKNAME)
        self.work(1, her, code="FC2-PPV-1234567")
        if listed:
            self.fc2cmadb.listed["1234567"] = list(listed)
        return her

    def test_the_site_name_behind_a_nickname_is_registered_and_searched_the_same_round(self):
        her = self.nicknamed()
        with self.database.read_connection() as connection:
            self.assertTrue(alias.has_entry(connection, her))
        summary = self.run_followup(her)
        names = self.aliases(her)
        batch = f"{alias.SOURCE}@7"
        self.assertEqual(names["神山ももか"], batch)
        self.assertEqual(names["雲母そら"], batch)
        self.assertNotIn("ちっぱいリクルーター", names)
        self.assertEqual(summary["sites"][alias.FC2CMADB],
                         "命中 " + alias.FC2CMADB_ACTRESS.format(id=9186))
        self.assertTrue(summary["sites"][alias.MINNANO].startswith("命中 " + PROFILE_URL))
        self.assertEqual([partial for _url, partial in self.fc2cmadb.calls], [False, True])

    def test_a_cast_list_of_someone_else_writes_nothing(self):
        her = self.nicknamed({"id": 77, "name": "佐々木ゆうか", "alias_name": "別の人"})
        summary = self.run_followup(her)
        self.assertEqual(self.aliases(her), {})
        self.assertEqual(summary["outcome"], "三站都没对上她")
        self.assertIn("FC2-PPV-1234567 的女优栏是 佐々木ゆうか", summary["sites"][alias.FC2CMADB])

    def test_two_people_on_the_cast_list_claiming_her_is_ambiguous(self):
        her = self.nicknamed({"id": 1, "name": "神山ももか", "alias_name": self.NICKNAME},
                             {"id": 2, "name": "雲母そら", "alias_name": self.NICKNAME})
        summary = self.run_followup(her)
        self.assertEqual(self.aliases(her), {})
        self.assertIn("不止一位", summary["sites"][alias.FC2CMADB])

    def test_a_site_name_another_entity_uses_is_left_for_review(self):
        other = self.entity("神山ももか")
        her = self.nicknamed()
        self.run_followup(her)
        self.assertNotIn("神山ももか", self.aliases(her))
        taken = [row for row in self.review() if row["site"] == alias.FC2CMADB]
        self.assertEqual([row["action"] for row in taken], [alias.TAKEN])
        self.assertIn(f"实体 {other}", taken[0]["detail"])

    def test_a_refusal_pauses_the_site(self):
        her = self.nicknamed()
        self.fc2cmadb.status = 403
        summary = self.run_followup(her)
        self.assertEqual(summary["outcome"], "未取得")
        self.assertTrue(paused_until(self.cooldown, alias.FC2CMADB))
        asked = len(self.fc2cmadb.calls)
        self.run_followup(her, run_id=8)
        self.assertEqual(len(self.fc2cmadb.calls), asked)

    def test_a_performer_without_fc2_works_is_not_asked(self):
        momoka = self.entity("神山ももか")
        self.work(1, momoka, code="ABP-968")
        summary = self.run_followup(momoka)
        self.assertEqual(self.fc2cmadb.calls, [])
        self.assertNotIn(alias.FC2CMADB, summary["sites"])


class RepeatTests(Case):
    def test_a_second_run_writes_nothing_and_asks_nobody(self):
        momoka = self.entity("神山ももか")
        self.run_followup(momoka, run_id=7)
        first = dict(self.aliases(momoka))
        requests = len(self.minnano.calls) + len(self.av_neme.calls)
        summary = self.run_followup(momoka, run_id=8)
        self.assertEqual(summary["outcome"], "没有新写法")
        self.assertEqual(self.aliases(momoka), first)
        self.assertEqual(len(self.minnano.calls) + len(self.av_neme.calls), requests)

    def test_stock_does_not_dispatch_her_again_until_her_names_change(self):
        momoka = self.entity("神山ももか")
        self.work(1, momoka)
        attempts = Attempts(attempts_root(self.generated))

        def planned():
            with self.database.read_connection() as connection:
                return [item.key for item in alias.stock(connection, attempts, limit=5)]

        self.assertEqual(planned(), [alias.followup_key(momoka)])
        self.run_followup(momoka)
        self.assertEqual(planned(), [])
        with self.database.write_transaction(notify=False) as connection:
            connection.execute("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
                               " VALUES(?,'新しい名前','新しい名前','manual')", (momoka,))
        self.assertEqual(planned(), [alias.followup_key(momoka)])

    def test_stock_puts_performers_without_an_avatar_first(self):
        """作品多的那位已经有头像，缺头像的那位作品少也排在前面：别名补进来，
        补头像后继才多一个名字去图库里找。
        """
        busy = self.entity("神山ももか")
        quiet = self.entity("伊吹彩")
        self.work(1, busy)
        self.work(2, busy)
        self.work(3, quiet)
        avatars = self.generated / "avatars"
        avatars.mkdir(parents=True, exist_ok=True)
        (avatars / f"performer-{busy}.img").write_bytes(b"installed")
        attempts = Attempts(attempts_root(self.generated))
        with self.database.read_connection() as connection:
            ordered = [item.key for item in alias.stock(connection, attempts, limit=5,
                                                        avatar_root=avatars)]
            plain = [item.key for item in alias.stock(connection, attempts, limit=5)]
        self.assertEqual(ordered, [alias.followup_key(quiet), alias.followup_key(busy)])
        self.assertEqual(plain, [alias.followup_key(busy), alias.followup_key(quiet)])

    def test_a_new_entry_rule_dispatches_everyone_tried_again(self):
        """入口判据换了版本，名字链没变的也再问一次：新入口能到的页，旧入口没读到。"""
        momoka = self.entity("神山ももか")
        self.work(1, momoka)
        attempts = Attempts(attempts_root(self.generated))

        def planned():
            with self.database.read_connection() as connection:
                return [item.key for item in alias.stock(connection, attempts, limit=5)]

        self.run_followup(momoka)
        self.assertEqual(planned(), [])
        with mock.patch.object(alias, "ENTRY_RULE", alias.ENTRY_RULE + 1):
            self.assertEqual(planned(), [alias.followup_key(momoka)])

    def test_a_rate_limited_site_is_paused_and_not_asked_again(self):
        momoka = self.entity("神山ももか")
        self.minnano.pages[minnano_av.search_url("神山ももか")] = (429, b"")
        self.av_neme.pages[wiki_search_url("神山ももか")] = (429, b"")
        summary = self.run_followup(momoka)
        self.assertEqual(summary["outcome"], "未取得")
        self.assertIn("429", summary["sites"][alias.MINNANO])
        self.assertIn("429", summary["sites"][alias.AV_NEME])
        self.assertTrue(paused_until(self.cooldown, alias.MINNANO))
        self.assertTrue(paused_until(self.cooldown, alias.AV_NEME))
        requests = len(self.minnano.calls) + len(self.av_neme.calls)
        self.assertEqual(self.run_followup(momoka, run_id=8)["outcome"], "未取得")
        self.assertEqual(len(self.minnano.calls) + len(self.av_neme.calls), requests)
        self.assertEqual(self.aliases(momoka), {})

    def test_an_unfetched_conclusion_is_dispatched_again_after_a_day(self):
        """站在冷却时的「未取得」说的是那一天，不是她；名字链没变也要再试，其余结论照旧不再派。"""
        momoka = self.entity("神山ももか")
        self.work(1, momoka)
        settled = self.entity("有本紗世")
        self.work(2, settled)
        self.assertEqual(self.run_followup(settled)["outcome"], "两站都没对上她")
        self.minnano.pages[minnano_av.search_url("神山ももか")] = (429, b"")
        self.av_neme.pages[wiki_url("神山ももか")] = (429, b"")
        self.assertEqual(self.run_followup(momoka)["outcome"], "未取得")
        now = time.time()

        def planned(clock):
            attempts = Attempts(attempts_root(self.generated), clock=lambda: clock)
            with self.database.read_connection() as connection:
                return [item.key for item in alias.stock(connection, attempts, limit=5)]

        self.assertEqual(planned(now), [])
        self.assertEqual(planned(now + alias.RETRY_UNFETCHED + 1), [alias.followup_key(momoka)])
        self.assertEqual(planned(now + 365 * 86400), [alias.followup_key(momoka)])

    def test_a_challenge_page_is_not_cached_as_her_profile(self):
        momoka = self.entity("神山ももか")
        self.minnano.pages[minnano_av.search_url("神山ももか")] = (
            200, b"<title>Just a moment...</title>")
        summary = self.run_followup(momoka)
        self.assertIn("未取得", summary["sites"][alias.MINNANO])
        self.assertFalse(list((self.generated / "provider-cache" / "minnano").glob("*.json")))

    def test_the_cloudflare_beacon_on_a_normal_page_is_not_a_challenge(self):
        """Cloudflare 往正常页末尾插一段 `challenge-platform` 检测脚本；页照常取，来源不进冷却。"""
        beacon = (b"<script>(function(){var a=document.createElement('script');"
                  b"a.src='/cdn-cgi/challenge-platform/scripts/jsd/main.js';"
                  b"document.getElementsByTagName('head')[0].appendChild(a);})();</script>")
        for url, (status, body) in list(self.minnano.pages.items()):
            self.minnano.pages[url] = (status, body + beacon)
        momoka = self.entity("神山ももか")
        summary = self.run_followup(momoka)
        self.assertTrue(summary["sites"][alias.MINNANO].startswith("命中 " + PROFILE_URL))
        self.assertFalse(list(self.cooldown.glob("scraping-minnano-av.cooldown.json")))

    def test_a_refused_page_is_fetched_in_the_local_browser_and_the_site_stays_there(self):
        """默认传输是 `SourceTransport`：HTTP 客户端被拦时同一页由本机浏览器取，取到就缓存、不冷却，
        这一站之后固定走浏览器；补女优资料后继与链接、名册脚本走的是同一个入口。"""
        from peach import browser_transport
        from peach.scraping_access import fixed_to_browser

        class Browser(browser_transport.BrowserTransport):
            def __init__(self):
                self.calls = []

            def __call__(self, request, timeout, max_bytes):
                self.calls.append(request.url)
                return HttpResponse(200, {}, b"<title>profile</title>", PROFILE_URL)

        url = minnano_av.search_url("神山ももか")
        browser = Browser()
        pages = alias.MinnanoPages(self.generated / "provider-cache" / "minnano", self.cooldown, limiter=NoWait())
        pages.transport.transports[alias.MINNANO] = lambda request, *_args: HttpResponse(403, {}, b"", request.url)
        with mock.patch("peach.browser_transport.shared", return_value=browser):
            self.assertEqual(pages.get(url), (PROFILE_URL, "<title>profile</title>"))
        pages.close()
        self.assertEqual(browser.calls, [url])
        self.assertTrue(fixed_to_browser(self.cooldown, alias.MINNANO))
        self.assertFalse(paused_until(self.cooldown, alias.MINNANO))


def load_revert():
    spec = importlib.util.spec_from_file_location(
        "revert_auto_landing_under_test", ROOT / "scripts" / "revert_auto_landing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RevertTests(Case):
    def test_a_batch_is_reverted_and_not_landed_again(self):
        momoka = self.entity("神山ももか", "かみやまももか本人")
        self.work(1, momoka)
        self.run_followup(momoka, run_id=7)
        self.assertEqual(len(self.aliases(momoka)), 5)
        revert = load_revert()
        base = ["--db", str(self.db), "--logo-root", str(self.root / "logos"),
                "--source", alias.SOURCE]
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(revert.main([*base, "--batch", f"{alias.SOURCE}@8"]), 0)
        self.assertIn("'别名': 0", printed.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(revert.main(base), 0)
        self.assertIn("'别名': 4", printed.getvalue())
        self.assertEqual(len(self.aliases(momoka)), 5)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(revert.main(
                [*base, "--apply", "--backup", str(self.root / "backup.db")]), 0)
        self.assertEqual(self.aliases(momoka), {"かみやまももか本人": "manual"})
        # 撤回之后她的名字链与撤回前、这一批写入前一样，存量补派不会原样再落一遍。
        with self.database.read_connection() as connection:
            self.assertEqual(alias.stock(connection, Attempts(attempts_root(self.generated)),
                                         limit=5), [])


class AllocationTests(Case):
    def test_stock_aliases_get_their_share_of_the_round(self):
        from peach import library_processing, task_runs

        people = [self.entity(f"白石まり{index:02d}") for index in range(10)]
        for index, entity_id in enumerate(people, start=1):
            self.work(index, entity_id)
        config = SimpleNamespace(directory=lambda _name: self.generated)
        with mock.patch.object(task_runs, "MAX_FOLLOWUPS", 8), \
                mock.patch.object(alias, "STOCK_SHARE", 3):
            found = library_processing._entity_followups(self.database, config, max(people))
        kinds = [item["task_key"] for item in found]
        self.assertEqual(len(kinds), 8)
        self.assertEqual(kinds.count(alias.TASK_KEY), 3)

    def test_new_performers_get_an_alias_followup_ahead_of_stock(self):
        from peach import library_processing

        old = self.entity("古川ゆうな")
        self.work(1, old)
        new = self.entity("新田あおい")
        config = SimpleNamespace(directory=lambda _name: self.generated)
        found = library_processing._entity_followups(self.database, config, old)
        keys = [item["key"] for item in found if item["task_key"] == alias.TASK_KEY]
        self.assertEqual(keys, [alias.followup_key(new), alias.followup_key(old)])


if __name__ == "__main__":
    unittest.main()
