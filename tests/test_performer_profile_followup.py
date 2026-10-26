"""补女优资料后继：minnano-av 资料表整张落库、avwikidb 编号从作品出演表定、冲突只记不改（ADR-0067）。

全程临时账本；取页层是真的（缓存、冷却、撞墙判定都走一遍），只把最底下的传输换成按地址回
页面的替身，不联网。页面按 2026-09-25 实测的 minnano-av 387589、avwikidb 女优页 1046723 与
作品页 MKMP-761 裁剪而成，只留解析会读到的那几块；夹具内联在这里，新目录映射不到测试域。
"""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

from peach import avwikidb, javdb, minnano_av
from peach import performer_alias_followup as alias
from peach import performer_profile_followup as followup
from peach.entities import merge_entity, normalize_entity_name
from peach.followups import Attempts, attempts_root
from peach.http import HttpResponse
from peach.performer_profiles import read_profile, write_profile
from peach.repository import LedgerDatabase
from peach.review_csv import read_rows
from peach.scraping_access import paused_until
from support.ledger import fresh_ledger

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-09-25T00:00:00.000Z"
MINNANO_URL = f"{minnano_av.SITE}actress387589.html"
ACTOR_URL = avwikidb.ACTOR_PAGE.format(id="1046723")
WORK_URL = avwikidb.WORK_PAGE.format(code="MKMP-761")

MINNANO_PROFILE = """<!doctype html><html lang="ja"><head>
<link rel="canonical" href="https://www.minnano-av.com/actress387589.html">
</head><body>
<h1>美ノ嶋めぐり<span>みのしまめぐり / Minoshima Meguri</span></h1>
<div class="act-profile">
	<table width="100%" cellspacing="0" cellpadding="0" border="0">
		<tr><td><h2>美ノ嶋めぐり （みのしまめぐり / Minoshima Meguri）</h2></td></tr>
		<tr><td><span>別名</span><p>中村めぐり （なかむらめぐり / Nakamura Meguri）</p></td></tr>
		<tr><td><span>生年月日</span><p>2001年12月08日
			（現在 <a href="actress_list.php?birthday=2001-12-08">24歳</a>）いて座</td>		</p></td></tr>
		<tr><td><span>サイズ</span><p>T156 / B86(<a href="actress_list.php?cup=E">Eカップ</a>) / W58 / H85 / S</p></td></tr>
		<tr><td><span>血液型</span><p><a href="actress_list.php?blood_type=B">B型</a></p></td></tr>
		<tr><td><span>出身地</span><p><a href="actress_list.php?place=29">奈良県</a></p></td></tr>
		<tr><td><span>所属事務所</span>
			<p>
				<a href="actress_list.php?production=831">LINX</a>
			</p>
		</td></tr>
		<tr><td><span>趣味・特技</span><p>ゲーム</p></td></tr>
		<tr><td><span>AV出演期間</span><p>2021年 -</p></td></tr>
		<tr><td><span>デビュー作品</span><p>新人 プレステージ専属デビュー（2021年05月 21日）</p></td></tr>
		<tr><td><span>ブログ</span><p><a href="https://twitter.com/minoshimameguri" target="_blank">http://twitter.com/minoshimameguri</a></p>
			</td></tr>
		<tr><td><span>公式サイト</span><p><a href="http://pub.linx.live/contents/model/5081/" target="_blank">http://pub.linx.live/contents/model/5081/</a></p></td></tr>
		<tr valign="top"><td><span>タグ</span>
			<div class="tagarea">
				<a href="actress_list.php?tag_a_id=28">美乳</a>
				<a href="actress_list.php?tag_a_id=61">美人</a>
		</td></tr>
	</table></div>
<div class="comment"><table><tr><td><span>生年月日</span><p>1990年01月01日</p></td></tr></table></div>
</body></html>"""

# 资料没填的一位：没有生年月日、血液型、出身地那几行，尺寸是横线，出道作品没写日期。
MINNANO_SPARSE = """<!doctype html><html lang="ja"><head>
<link rel="canonical" href="https://www.minnano-av.com/actress12345.html">
</head><body>
<h1>白石まり<span>しらいしまり / </span></h1>
<div class="act-profile"><table>
<tr><td><span>サイズ</span><p>T- / B-(-カップ) / W- / H-</p></td></tr>
<tr><td><span>AV出演期間</span><p>2015年 - 2019年</p></td></tr>
<tr><td><span>デビュー作品</span><p>はじめての撮影</p></td></tr>
</table></div></body></html>"""

MINNANO_SEARCH = """<!doctype html><html lang="ja"><body>
<table class="tbllist actress">
<tr><td class="details"><h2 class="ttl"><a href="actress387589.html">美ノ嶋めぐり</a></h2></td></tr>
</table></body></html>"""

MINNANO_EMPTY = """<!doctype html><html lang="ja"><body>
<table class="tbllist actress"><tr><th>名前</th></tr></table></body></html>"""

AVWIKIDB_ACTOR = """<html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebSite","name":"AVWikiDB"}</script>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Person","name":"皆月ひかる",
"url":"https://avwikidb.com/actor/1046723/",
"image":"https://pics.dmm.co.jp/mono/actjpgs/minazuki_hikaru.jpg","jobTitle":"AV女優",
"alternateName":["みなづき ひかる","Hikaru Minazuki","高橋未来","たかはし みらい","島修也"],
"height":{"@type":"QuantitativeValue","value":148,"unitCode":"CMT"},"birthDate":"2000-01-01"}</script>
</head><body><div class="mt-2"><p class="text-muted-foreground text-xs">身長・スリーサイズ</p>
<p class="text-sm">T148 B83(B) W55 H85<button type="button" aria-label="出典 1 件"></button></p></div>
</body></html>"""

AVWIKIDB_WORK = """<html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Movie","name":"MKMP-761 …",
"actor":[{"@type":"Person","name":"皆月ひかる","alternateName":"Hikaru Minazuki",
"url":"https://avwikidb.com/actor/1046723/"},
{"@type":"Person","name":"南日菜乃","alternateName":"Hinano Minami","url":"https://avwikidb.com/actor/1097822/"},
{"@type":"Person","name":"冬愛ことね","alternateName":"Kotone Toua","url":"https://avwikidb.com/actor/1051459/"}]}</script>
</head><body><a href="/actor/1046723/">皆月ひかる</a></body></html>"""


def javdb_search(*cards: tuple[str, str, str]) -> str:
    """javdb 演员搜索结果：每张卡 (id, 标题一栏, 记录类型)。按 2026-09-12 缓存的搜索页裁剪。"""
    boxes = "".join(
        f'<div class="box actor-box"><a href="/actors/{actor}" title="{title}">'
        f'<figure class="image"><img class="avatar" src="https://c0.jdbstatic.com/x.jpg" />'
        + (f'<span class="info">{record}</span>' if record else "")
        + f"</figure><strong>{title.split(',')[0]}</strong></a></div>"
        for actor, title, record in cards)
    return f'<html><body><div id="actors" class="actors">{boxes}</div></body></html>'


def avwikidb_works(movies: list[dict]) -> str:
    """单人作品筛选页：作品表只在 `__NEXT_DATA__` 里，卡片上的 `alt` 会把人名截成「ほか」。"""
    data = {"props": {"pageProps": {"movies": movies}}}
    return ('<html><body><img alt="DVAJ-495の女優は 星野あかり、麻美ゆまほか">'
            f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
            "</body></html>")


def avwikidb_movie(code: str, floor: str, cid: str, *cast: tuple[str, int],
                   unknown: bool = False) -> dict:
    return {"adultVideoId": code, "floor": floor, "fanzaContentId": cid, "mgsImageUrl": None,
            "actorUnknown": unknown,
            "actor": [{"name": name, "fanzaAvActressId": number} for name, number in cast]}


AVWIKIDB_SINGLE = avwikidb_works([
    avwikidb_movie("DVAJ-495", "videoa", "dvaj00495", ("星野あかり", 11823), ("杉野綾子", 1025548)),
    avwikidb_movie("EEA-266", "videoc", "eesthe266", ("杉野綾子", 1025548), unknown=True),
    avwikidb_movie("TD003KIRA0015C", "videoa", "td003kira00015c", ("杉野綾子", 1025548)),
    avwikidb_movie("IPZZ-937", "videoa", "ipzz00937", ("桜空もも", 1040000))])


class Transport:
    """按地址回页面的传输替身；没登记的检索回一张空结果页，其余回 404。"""

    def __init__(self, pages: dict, empty_search: str = MINNANO_EMPTY):
        self.pages, self.empty_search, self.calls = pages, empty_search.encode("utf-8"), []

    def __call__(self, request, _timeout, _limit):
        self.calls.append(request.url)
        if request.url in self.pages:
            status, body = self.pages[request.url]
            return HttpResponse(status, {}, body.encode("utf-8"), request.url)
        if "search" in request.url:
            return HttpResponse(200, {}, self.empty_search, request.url)
        return HttpResponse(404, {}, b"", request.url)

    def close(self):
        pass


class NoWait:
    def wait(self, _url):
        pass


class ParsingTests(unittest.TestCase):
    def test_multiple_career_periods_use_the_first_debut_and_latest_end(self):
        for text, until in (("2017年 - 2018年、2021年 -", None),
                            ("2017年 - 2018年、2021年 - 2024年", 2024)):
            with self.subTest(periods=text):
                page = MINNANO_SPARSE.replace("2015年 - 2019年", text)
                found = minnano_av.profile(page)
                self.assertEqual((found['debut_year'], found['active_until']), (2017, until))

    def test_a_full_profile_table_is_normalised_column_by_column(self):
        found = minnano_av.profile(MINNANO_PROFILE)
        self.assertEqual({column: found[column] for column in minnano_av.PROFILE_COLUMNS}, {
            "kana": "みのしまめぐり", "romaji": "Minoshima Meguri", "birth_date": "2001-12-08",
            "height_cm": 156, "bust_cm": 86, "cup": "E", "waist_cm": 58, "hip_cm": 85,
            "blood_type": "B", "birthplace": "奈良県", "hobbies": "ゲーム", "debut_year": 2021,
            "active_until": None, "debut_title": "新人 プレステージ専属デビュー",
            "debut_date": "2021-05-21", "blog_url": "https://twitter.com/minoshimameguri",
            "site_url": "http://pub.linx.live/contents/model/5081/"})
        self.assertEqual(found["tags"], ["美乳", "美人"])

    def test_the_raw_cells_keep_every_label_and_aliases_as_a_list(self):
        """规整规则改了不必重新取页：每一格原文都在，事务所也在（它写在实体上，这里只留原文）。"""
        raw = minnano_av.profile(MINNANO_PROFILE)["raw"]
        self.assertEqual(raw["別名"], ["中村めぐり （なかむらめぐり / Nakamura Meguri）"])
        self.assertEqual(raw["サイズ"], "T156 / B86( Eカップ ) / W58 / H85 / S")
        self.assertEqual(raw["所属事務所"], "LINX")

    def test_the_comment_section_does_not_override_the_profile_table(self):
        self.assertEqual(minnano_av.profile(MINNANO_PROFILE)["birth_date"], "2001-12-08")

    def test_missing_rows_and_dashes_stay_empty_instead_of_guessed(self):
        found = minnano_av.profile(MINNANO_SPARSE)
        for column in ("birth_date", "height_cm", "bust_cm", "cup", "waist_cm", "hip_cm",
                       "blood_type", "birthplace", "hobbies", "debut_date", "romaji",
                       "blog_url", "site_url"):
            self.assertIsNone(found[column], column)
        self.assertEqual((found["kana"], found["debut_year"], found["active_until"],
                          found["debut_title"]), ("しらいしまり", 2015, 2019, "はじめての撮影"))
        self.assertEqual(found["tags"], [])

    def test_an_impossible_date_is_dropped(self):
        page = MINNANO_SPARSE.replace("<tr><td><span>サイズ</span>",
                                      "<tr><td><span>生年月日</span><p>1999年02月30日</p></td></tr>"
                                      "<tr><td><span>サイズ</span>")
        self.assertIsNone(minnano_av.profile(page)["birth_date"])

    def test_a_page_that_is_not_a_profile_yields_none(self):
        self.assertIsNone(minnano_av.profile(MINNANO_SEARCH))

    def test_the_work_cast_comes_from_the_structured_data(self):
        self.assertEqual(avwikidb.work_cast(AVWIKIDB_WORK), [
            {"id": "1046723", "name": "皆月ひかる", "romaji": "Hikaru Minazuki"},
            {"id": "1097822", "name": "南日菜乃", "romaji": "Hinano Minami"},
            {"id": "1051459", "name": "冬愛ことね", "romaji": "Kotone Toua"}])

    def test_the_actor_page_reads_only_the_first_two_alternate_names(self):
        """`alternateName` 后面混着别的名义，其中有男优名；只有前两项是读音与罗马字。"""
        self.assertEqual(avwikidb.actor_profile(AVWIKIDB_ACTOR), {
            "id": "1046723", "name": "皆月ひかる", "kana": "みなづき ひかる",
            "romaji": "Hikaru Minazuki", "birth_date": "2000-01-01", "height_cm": 148,
            "bust_cm": 83, "cup": "B", "waist_cm": 55, "hip_cm": 85,
            "image": "https://pics.dmm.co.jp/mono/actjpgs/minazuki_hikaru.jpg"})

    def test_single_works_are_the_ones_whose_cast_is_her_alone(self):
        """合集与别人的单人作品都不算，按出演表里的站上编号判，不按名字。"""
        self.assertEqual(avwikidb.single_works(AVWIKIDB_SINGLE, "1025548"), [
            {"code": "EEA-266", "floor": "videoc", "content_id": "eesthe266", "mgs_image": "",
             "identified": True},
            {"code": "TD003KIRA0015C", "floor": "videoa", "content_id": "td003kira00015c",
             "mgs_image": "", "identified": False}])

    def test_a_page_without_structured_data_lists_no_single_works(self):
        self.assertEqual(avwikidb.single_works(AVWIKIDB_ACTOR, "1046723"), [])
        self.assertEqual(avwikidb.single_count(AVWIKIDB_ACTOR), 0)

    def test_the_actor_page_says_how_many_single_works_she_has(self):
        page = AVWIKIDB_SINGLE.replace('"movies":', '"singleCount": 2, "movies":')
        self.assertEqual(avwikidb.single_count(page), 2)

    def test_romaji_compares_word_order_free(self):
        self.assertEqual(avwikidb.romaji_key("Hikaru Minazuki"), avwikidb.romaji_key("Minazuki Hikaru"))
        self.assertNotEqual(avwikidb.romaji_key("Hikaru Minazuki"), avwikidb.romaji_key("Hikari Minazuki"))


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
            minnano_av.search_url("美ノ嶋めぐり"): (200, MINNANO_SEARCH),
            MINNANO_URL: (200, MINNANO_PROFILE)})
        self.avwikidb = Transport({WORK_URL: (200, AVWIKIDB_WORK), ACTOR_URL: (200, AVWIKIDB_ACTOR)})
        self.javdb = Transport({}, empty_search=javdb_search())

    def entity(self, name: str, *aliases: str) -> int:
        with self.database.write_transaction(notify=False) as connection:
            cursor = connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('performer',?,?,?,?)", (name, normalize_entity_name(name), STAMP, STAMP))
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

    def ref(self, entity_id: int, provider: str, external_id: str, metadata: dict | None = None):
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id,"
                "metadata_json) VALUES(?,?,'performer',?,?)",
                (entity_id, provider, external_id, json.dumps(metadata or {})))

    def refs(self, entity_id: int) -> dict[str, tuple[str, dict]]:
        with self.database.read_connection() as connection:
            return {str(row[0]): (str(row[1]), json.loads(row[2] or "{}")) for row in connection.execute(
                "SELECT provider,external_id,metadata_json FROM entity_external_ref WHERE entity_id=?",
                (entity_id,))}

    def profile(self, entity_id: int) -> dict | None:
        with self.database.read_connection() as connection:
            return read_profile(connection, entity_id)

    def sites(self) -> dict:
        cache = self.generated / "provider-cache"
        return {followup.MINNANO: alias.MinnanoPages(
                    cache / "minnano", self.cooldown, self.minnano, limiter=NoWait(),
                    max_age=followup.REFRESH),
                followup.AVWIKIDB: alias.MinnanoPages(
                    cache / "avwikidb", self.cooldown, self.avwikidb, limiter=NoWait(),
                    source=followup.AVWIKIDB, max_age=followup.REFRESH),
                followup.JAVDB: alias.MinnanoPages(
                    cache / "javdb", self.cooldown, self.javdb, limiter=NoWait(),
                    max_requests=alias.MAX_KEYS, source=followup.JAVDB, max_age=followup.REFRESH)}

    def run_followup(self, entity_id: int, run_id: int = 7) -> dict:
        handle = SimpleNamespace(run_id=run_id, progress=lambda **_kwargs: None)
        with mock.patch.object(followup, "open_sites", side_effect=lambda _contract: self.sites()):
            return followup.run(self.contract, followup.followup_key(entity_id), handle)

    def review(self) -> list[dict]:
        return read_rows(self.generated / followup.REVIEW_FILE)


class MinnanoLandingTests(Case):
    def test_a_bound_performer_gets_her_whole_profile_written_under_the_batch(self):
        meguri = self.entity("美ノ嶋めぐり")
        self.work(1, meguri)
        self.ref(meguri, alias.MINNANO, "387589")
        summary = self.run_followup(meguri, run_id=7)
        found = self.profile(meguri)
        self.assertEqual((found["birth_date"], found["height_cm"], found["cup"], found["debut_year"]),
                         ("2001-12-08", 156, "E", 2021))
        self.assertEqual((found["source"], found["source_url"]),
                         (f"{followup.SOURCE}@7", MINNANO_URL))
        self.assertEqual(found["tags"], ["美乳", "美人"])
        self.assertEqual(self.minnano.calls, [MINNANO_URL], "有编号就不检索")
        self.assertTrue(summary["landed"])
        self.assertTrue(self.busted)
        self.assertIn(followup.WRITE, [row["action"] for row in self.review()])

    def test_an_unbound_performer_is_found_by_search_and_the_number_is_bound(self):
        meguri = self.entity("美ノ嶋めぐり")
        self.work(1, meguri)
        self.run_followup(meguri, run_id=9)
        self.assertEqual(self.profile(meguri)["birth_date"], "2001-12-08")
        number, metadata = self.refs(meguri)[alias.MINNANO]
        self.assertEqual((number, metadata["source"], metadata["batch"]),
                         ("387589", followup.SOURCE, f"{followup.SOURCE}@9"))

    def test_an_alias_on_the_page_anchors_her_too(self):
        """账本里她叫旧名 `中村めぐり`，资料页別名栏列着它，就是同一位。"""
        meguri = self.entity("中村めぐり")
        self.work(1, meguri)
        self.ref(meguri, alias.MINNANO, "387589")
        self.run_followup(meguri)
        self.assertIsNotNone(self.profile(meguri))

    def test_a_page_that_does_not_list_her_name_is_not_written(self):
        other = self.entity("白石まり")
        self.work(1, other)
        self.ref(other, alias.MINNANO, "387589")
        summary = self.run_followup(other)
        self.assertIsNone(self.profile(other))
        self.assertTrue(summary["sites"][followup.MINNANO].startswith(followup.MISS))
        self.assertEqual(summary["outcome"], "哪一站都没对上她")

    def test_a_hand_written_row_is_left_alone(self):
        meguri = self.entity("美ノ嶋めぐり")
        self.work(1, meguri)
        self.ref(meguri, alias.MINNANO, "387589")
        with self.database.write_transaction(notify=False) as connection:
            write_profile(connection, meguri, {"birth_date": "2001-12-09"}, source="user:manual",
                          source_url="", fetched_at="2020-01-01T00:00:00Z")
        self.run_followup(meguri)
        found = self.profile(meguri)
        self.assertEqual((found["birth_date"], found["source"]), ("2001-12-09", "user:manual"))

    def test_a_fresh_profile_is_not_fetched_again_and_a_stale_one_is(self):
        meguri = self.entity("美ノ嶋めぐり")
        self.work(1, meguri)
        self.ref(meguri, alias.MINNANO, "387589")
        self.run_followup(meguri, run_id=7)
        self.run_followup(meguri, run_id=8)
        self.assertEqual(self.minnano.calls, [MINNANO_URL], "取回不到 30 天，第二轮一次都不问")
        self.assertEqual(self.profile(meguri)["source"], f"{followup.SOURCE}@7")
        old = "2026-01-01T00:00:00Z"
        with self.database.write_transaction(notify=False) as connection:
            connection.execute("UPDATE performer_profile SET fetched_at=?", (old,))
        stale = time.time() - followup.REFRESH - 60
        for cached in (self.generated / "provider-cache" / "minnano").glob("*.json"):
            os.utime(cached, (stale, stale))
        self.run_followup(meguri, run_id=11)
        self.assertEqual(self.minnano.calls, [MINNANO_URL, MINNANO_URL], "缓存也过期，重取资料页")
        self.assertEqual(self.profile(meguri)["source"], f"{followup.SOURCE}@11")

    def test_a_rate_limited_site_is_paused_and_the_mark_expires_in_a_day(self):
        meguri = self.entity("美ノ嶋めぐり")
        self.work(1, meguri)
        self.ref(meguri, alias.MINNANO, "387589")
        self.minnano.pages[MINNANO_URL] = (429, "")
        summary = self.run_followup(meguri)
        self.assertEqual(summary["outcome"], followup.UNFETCHED)
        self.assertTrue(paused_until(self.cooldown, alias.MINNANO))
        record = json.loads(next((attempts_root(self.generated)).glob("performer-profile-*.json"))
                            .read_text(encoding="utf-8"))
        self.assertLess(record["retry_until"] - time.time(), followup.RETRY_UNFETCHED + 5)


class AvwikidbTests(Case):
    def setUp(self):
        super().setUp()
        self.hikaru = self.entity("皆月ひかる")
        self.work(1, self.hikaru, code="MKMP-761")

    def test_the_number_comes_from_her_own_work_and_the_actor_page_fills_the_metadata(self):
        summary = self.run_followup(self.hikaru, run_id=5)
        number, metadata = self.refs(self.hikaru)[followup.AVWIKIDB]
        self.assertEqual(number, "1046723")
        self.assertEqual({key: metadata[key] for key in
                          ("image", "height", "birthDate", "kana", "romaji", "work", "batch")},
                         {"image": "https://pics.dmm.co.jp/mono/actjpgs/minazuki_hikaru.jpg",
                          "height": 148, "birthDate": "2000-01-01", "kana": "みなづき ひかる",
                          "romaji": "Hikaru Minazuki", "work": "MKMP-761",
                          "batch": f"{followup.SOURCE}@5"})
        self.assertNotIn("conflicts", metadata)
        self.assertEqual(self.avwikidb.calls, [WORK_URL, ACTOR_URL])
        self.assertTrue(summary["landed"])

    def test_a_disagreement_is_recorded_and_the_profile_is_not_changed(self):
        with self.database.write_transaction(notify=False) as connection:
            write_profile(connection, self.hikaru, {"birth_date": "2000-03-18", "height_cm": 148},
                          source=f"{followup.SOURCE}@1", source_url="",
                          fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        summary = self.run_followup(self.hikaru)
        _number, metadata = self.refs(self.hikaru)[followup.AVWIKIDB]
        self.assertEqual(metadata["conflicts"], [
            {"field": "birth_date", "minnano-av": "2000-03-18", "avwikidb": "2000-01-01"}])
        self.assertEqual(self.profile(self.hikaru)["birth_date"], "2000-03-18")
        self.assertEqual(summary["conflicts"], 1)
        self.assertIn(followup.CONFLICT, [row["action"] for row in self.review()])

    def test_romaji_from_her_minnano_profile_matches_a_differently_written_name(self):
        """账本里写成 `皆月光`，出演表上是 `皆月ひかる`；罗马字按词对得上就是她。"""
        other = self.entity("皆月光")
        self.work(2, other, code="MKMP-761")
        with self.database.write_transaction(notify=False) as connection:
            write_profile(connection, other, {"romaji": "Minazuki Hikaru"},
                          source=f"{followup.SOURCE}@1", source_url="",
                          fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        with self.database.write_transaction(notify=False) as connection:
            connection.execute("DELETE FROM asset_entity WHERE entity_id=?", (self.hikaru,))
        self.run_followup(other)
        self.assertEqual(self.refs(other)[followup.AVWIKIDB][0], "1046723")

    def test_two_cast_members_matching_is_ambiguous_and_nothing_is_bound(self):
        both = self.entity("南日菜乃", "冬愛ことね")
        self.work(3, both, code="MKMP-761")
        summary = self.run_followup(both)
        self.assertNotIn(followup.AVWIKIDB, self.refs(both))
        self.assertIn("不止一位", summary["sites"][followup.AVWIKIDB])

    def test_no_match_in_the_cast_reports_not_found_instead_of_guessing(self):
        stranger = self.entity("星宮一花")
        self.work(4, stranger, code="MKMP-761")
        summary = self.run_followup(stranger)
        self.assertNotIn(followup.AVWIKIDB, self.refs(stranger))
        self.assertTrue(summary["sites"][followup.AVWIKIDB].startswith(followup.MISS))

    def test_a_number_held_by_another_entity_is_only_recorded(self):
        holder = self.entity("皆月ひかる（別人）")
        self.ref(holder, followup.AVWIKIDB, "1046723")
        summary = self.run_followup(self.hikaru)
        self.assertNotIn(followup.AVWIKIDB, self.refs(self.hikaru))
        self.assertTrue(summary["sites"][followup.AVWIKIDB].startswith(followup.TAKEN))

    def test_a_blocked_site_is_paused_and_nothing_is_bound(self):
        self.avwikidb.pages[WORK_URL] = (403, "")
        summary = self.run_followup(self.hikaru)
        self.assertTrue(paused_until(self.cooldown, followup.AVWIKIDB))
        self.assertTrue(summary["sites"][followup.AVWIKIDB].startswith(followup.UNFETCHED))
        self.assertEqual(self.refs(self.hikaru), {})

    def test_a_failed_refresh_keeps_the_metadata_of_a_bound_number(self):
        self.run_followup(self.hikaru, run_id=5)
        with self.database.write_transaction(notify=False) as connection:
            connection.execute("UPDATE entity_external_ref SET last_synced_at='2026-01-01T00:00:00Z'"
                               " WHERE provider=?", (followup.AVWIKIDB,))
        stale = time.time() - followup.REFRESH - 60
        for cached in (self.generated / "provider-cache" / "avwikidb").glob("*.json"):
            os.utime(cached, (stale, stale))
        del self.avwikidb.pages[ACTOR_URL]
        summary = self.run_followup(self.hikaru, run_id=6)
        _number, metadata = self.refs(self.hikaru)[followup.AVWIKIDB]
        self.assertEqual((metadata["height"], metadata["batch"]), (148, f"{followup.SOURCE}@5"))
        self.assertTrue(summary["sites"][followup.AVWIKIDB].startswith(followup.UNFETCHED))

    def test_fc2_codes_are_not_used_as_an_entry(self):
        fc2 = self.entity("FC2の人")
        self.work(5, fc2, code="FC2-PPV-1234567")
        with self.database.read_connection() as connection:
            self.assertEqual(followup.work_codes(connection, fc2), [])


class JavdbTests(Case):
    def setUp(self):
        super().setUp()
        self.meguri = self.entity("美ノ嶋めぐり")
        self.work(1, self.meguri)
        self.ref(self.meguri, alias.MINNANO, "387589")

    def search(self, name: str, *cards: tuple[str, str, str], status: int = 200) -> None:
        self.javdb.pages[javdb.SEARCH.format(quote(name))] = (status, javdb_search(*cards))

    def javdb_ids(self, entity_id: int) -> list[tuple[str, dict]]:
        with self.database.read_connection() as connection:
            return [(str(row[0]), json.loads(row[1] or "{}")) for row in connection.execute(
                "SELECT external_id,metadata_json FROM entity_external_ref"
                " WHERE entity_id=? AND provider=? ORDER BY external_id",
                (entity_id, followup.JAVDB))]

    def test_her_censored_and_uncensored_records_are_both_bound(self):
        """有碼、無碼两条是同一个人；名字对不上的近似结果不算。"""
        self.search("美ノ嶋めぐり", ("Ab12", "美ノ嶋めぐり, 中村めぐり", ""),
                    ("Cd34", "美ノ嶋めぐり", "無碼"), ("Zz99", "美嶋めぐみ", ""))
        summary = self.run_followup(self.meguri, run_id=4)
        found = self.javdb_ids(self.meguri)
        self.assertEqual([number for number, _metadata in found], ["Ab12", "Cd34"])
        self.assertEqual({metadata["batch"] for _number, metadata in found}, {f"{followup.SOURCE}@4"})
        self.assertIn("javdb 绑定", summary["outcome"])

    def test_two_people_sharing_her_name_are_left_unbound(self):
        self.search("美ノ嶋めぐり", ("Ab12", "美ノ嶋めぐり", ""), ("Ef56", "美ノ嶋めぐり", ""))
        summary = self.run_followup(self.meguri)
        self.assertEqual(self.javdb_ids(self.meguri), [])
        self.assertTrue(summary["sites"][followup.JAVDB].startswith(followup.MISS))
        self.assertIn("Ab12、Ef56", summary["sites"][followup.JAVDB])

    def test_japanese_glyphs_on_the_card_match_her_simplified_name(self):
        reiko = self.entity("泽村玲子")
        self.work(2, reiko)
        self.ref(reiko, alias.MINNANO, "5555")
        self.search("泽村玲子", ("Gh78", "澤村玲子", ""))
        self.run_followup(reiko)
        self.assertEqual([number for number, _metadata in self.javdb_ids(reiko)], ["Gh78"])

    def test_a_performer_without_a_jav_directory_id_is_not_searched(self):
        creator = self.entity("145cm色白お嬢様")
        self.work(3, creator)
        summary = self.run_followup(creator)
        self.assertEqual(self.javdb.calls, [])
        self.assertEqual(summary["sites"][followup.JAVDB], "不查：JAV 目录站里没有她")

    def test_a_bound_id_is_not_searched_again(self):
        self.ref(self.meguri, followup.JAVDB, "Ab12")
        self.run_followup(self.meguri)
        self.assertEqual(self.javdb.calls, [])

    def test_a_login_page_is_unfetched_and_asked_again_next_round(self):
        """回 200 的登入页不是「站上没有她」：不结算，缓存也不留。"""
        url = javdb.SEARCH.format(quote("美ノ嶋めぐり"))
        self.javdb.pages[url] = (200, "<html><head><title>登入 | JavDB</title></head></html>")
        summary = self.run_followup(self.meguri)
        self.assertTrue(summary["sites"][followup.JAVDB].startswith(followup.UNFETCHED))
        self.search("美ノ嶋めぐり", ("Ab12", "美ノ嶋めぐり", ""))
        self.run_followup(self.meguri)
        self.assertEqual([number for number, _metadata in self.javdb_ids(self.meguri)], ["Ab12"])

    def test_searches_that_all_fail_are_unfetched_not_a_miss(self):
        self.search("美ノ嶋めぐり", status=500)
        summary = self.run_followup(self.meguri)
        self.assertTrue(summary["sites"][followup.JAVDB].startswith(followup.UNFETCHED))
        self.assertNotIn(followup.JAVDB, {row["site"] for row in self.review()})

    def test_a_refusal_pauses_javdb_and_binds_nothing(self):
        self.search("美ノ嶋めぐり", status=403)
        summary = self.run_followup(self.meguri)
        self.assertTrue(summary["sites"][followup.JAVDB].startswith(followup.UNFETCHED))
        self.assertTrue(paused_until(self.cooldown, followup.JAVDB))
        self.assertEqual(self.javdb_ids(self.meguri), [])


def load_revert():
    spec = importlib.util.spec_from_file_location(
        "revert_auto_landing_under_test", ROOT / "scripts" / "revert_auto_landing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RevertTests(Case):
    def test_a_batch_removes_the_profile_rows_and_the_numbers_it_bound(self):
        meguri = self.entity("美ノ嶋めぐり")
        self.work(1, meguri, code="MKMP-761")
        self.ref(meguri, "javdb", "abc12", {"source": "manual"})
        self.run_followup(meguri, run_id=7)
        self.assertEqual(set(self.refs(meguri)), {"javdb", alias.MINNANO})
        revert = load_revert()
        base = ["--db", str(self.db), "--logo-root", str(self.root / "logos"),
                "--source", followup.SOURCE]
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(revert.main(base), 0)
        self.assertIn("'资料': 1", printed.getvalue())
        self.assertIn("'编号': 1", printed.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(revert.main([*base, "--apply", "--backup", str(self.root / "b.db")]), 0)
        self.assertIsNone(self.profile(meguri))
        self.assertEqual(set(self.refs(meguri)), {"javdb"}, "别人登记的编号不动")


class MergeTests(Case):
    def test_a_merge_keeps_the_targets_profile_and_otherwise_takes_the_sources(self):
        target, source, bare = self.entity("甲"), self.entity("乙"), self.entity("丙")
        with self.database.write_transaction(notify=False) as connection:
            write_profile(connection, target, {"height_cm": 150}, source="auto:x", source_url="")
            write_profile(connection, source, {"height_cm": 160}, source="auto:x", source_url="")
            merge_entity(connection, target_id=target, source_id=source, source_name="乙",
                         alias_source="test")
            moved = merge_entity(connection, target_id=bare, source_id=target, source_name="甲",
                                 alias_source="test")
            left = connection.execute("SELECT count(*) FROM performer_profile").fetchone()[0]
        self.assertEqual(moved["profiles"], 1)
        self.assertEqual((self.profile(bare)["height_cm"], left), (150, 1))


class SchedulingTests(Case):
    def test_a_landed_profile_is_not_dispatched_again_until_it_is_due(self):
        meguri = self.entity("美ノ嶋めぐり")
        self.work(1, meguri)
        self.ref(meguri, alias.MINNANO, "387589")
        self.run_followup(meguri)
        root = attempts_root(self.generated)
        with self.database.read_connection() as connection:
            self.assertEqual(followup.stock(connection, Attempts(root), limit=5), [])
            later = Attempts(root, clock=lambda: time.time() + followup.REFRESH + 60)
            self.assertEqual([item.key for item in followup.stock(connection, later, limit=5)],
                             [followup.followup_key(meguri)])

    def test_stock_profiles_get_their_share_of_the_round(self):
        from peach import library_processing, task_runs

        people = [self.entity(f"白石まり{index:02d}") for index in range(12)]
        for index, entity_id in enumerate(people, start=1):
            self.work(index, entity_id)
        config = SimpleNamespace(directory=lambda _name: self.generated)
        with mock.patch.object(task_runs, "MAX_FOLLOWUPS", 10), \
                mock.patch.object(alias, "STOCK_SHARE", 3), \
                mock.patch.object(followup, "STOCK_SHARE", 2):
            found = library_processing._entity_followups(self.database, config, max(people))
        kinds = [item["task_key"] for item in found]
        self.assertEqual(len(kinds), 10)
        self.assertEqual((kinds.count(alias.TASK_KEY), kinds.count(followup.TASK_KEY)), (3, 2))

    def test_a_new_performer_gets_a_profile_followup(self):
        from peach import library_processing

        old = self.entity("古川ゆうな")
        self.work(1, old)
        new = self.entity("新田あおい")
        config = SimpleNamespace(directory=lambda _name: self.generated)
        found = library_processing._entity_followups(self.database, config, old)
        keys = [item["key"] for item in found if item["task_key"] == followup.TASK_KEY]
        self.assertEqual(keys, [followup.followup_key(new), followup.followup_key(old)])


def load_sweep():
    spec = importlib.util.spec_from_file_location(
        "run_performer_profiles_under_test", ROOT / "scripts" / "run_performer_profiles.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SweepTests(unittest.TestCase):
    """一次补完的脚本只挑人、记进度；每一位怎么补是后继自己的事，这里换成替身。"""

    def items(self, count):
        return [SimpleNamespace(key=followup.followup_key(index), label=f"女优{index}")
                for index in range(1, count + 1)]

    def test_consecutive_unfetched_stops_the_sweep_instead_of_stamping_everyone(self):
        sweep = load_sweep()
        with mock.patch.object(followup, "run", return_value={"outcome": followup.UNFETCHED}) as run:
            tally = sweep.sweep(None, self.items(20), "manual-test", out=io.StringIO(), stop_after=3)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(tally["停在"], 3)

    def test_one_failure_is_hers_alone_and_the_sweep_goes_on(self):
        sweep = load_sweep()
        outcomes = [RuntimeError("坏页"), {"outcome": "minnano-av 写入"}, {"outcome": "资料未过期"}]
        with mock.patch.object(followup, "run", side_effect=outcomes):
            tally = sweep.sweep(None, self.items(3), "manual-test", out=io.StringIO())
        self.assertEqual(tally, {"出错：RuntimeError: 坏页": 1, "minnano-av 写入": 1, "资料未过期": 1})

    def test_the_batch_carries_the_manual_run_id(self):
        sweep = load_sweep()
        seen = []
        with mock.patch.object(followup, "run",
                               side_effect=lambda _c, _k, handle: seen.append(handle.run_id) or {}):
            sweep.sweep(None, self.items(2), "manual-20260925T120000", out=io.StringIO())
        self.assertEqual(seen, ["manual-20260925T120000"] * 2)

    def test_apply_without_backup_is_refused_before_anything_runs(self):
        sweep = load_sweep()
        with mock.patch.object(sweep, "pending") as pending, \
                mock.patch.object(followup, "run") as run, \
                self.assertRaises(SystemExit) as refused:
            sweep.main(["--db", "unused.db", "--apply"])
        self.assertEqual(str(refused.exception), sweep.BACKUP_REQUIRED)
        pending.assert_not_called()
        run.assert_not_called()

    def test_a_name_outside_the_console_code_page_does_not_stop_the_sweep(self):
        """中文 Windows 控制台是 GBK，名字里一个 `䌷` 就能让打印抛错；输出按 UTF-8 写。"""
        sweep = load_sweep()
        console = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
        item = SimpleNamespace(key=followup.followup_key(1), label="䌷野あい")
        with mock.patch("sys.stdout", console), \
                mock.patch.object(sweep, "open_readonly", return_value=contextlib.nullcontext()), \
                mock.patch.object(sweep, "pending", return_value=[item]), \
                mock.patch("peach.web_state.WebContract"):
            self.assertEqual(sweep.main(["--db", "unused.db"]), 0)
        console.flush()
        self.assertIn("䌷野あい", console.buffer.getvalue().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
