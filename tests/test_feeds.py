"""番号发现源：解析、两层去重、已读与忽略、空壳与真实资产的边界（ADR-0042）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach import feeds  # noqa: E402
from peach.migrations import upgrade  # noqa: E402
from peach.repository import LedgerDatabase  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

JAVDB_ACTOR = """
<div class="movie-list h cols-4 vcols-8">
  <div class="item">
    <a href="/v/5nr8mp" class="box" title="ignored">
      <div class="cover"><img src="x.jpg" /></div>
      <div class="video-title"><strong>PBD-528</strong> 気高きお姉さん達</div>
      <div class="score"><span class="value">4.0分</span></div>
      <div class="meta">
        2026-10-20
      </div>
    </a>
  </div>
  <div class="item">
    <a href="/v/RkPb5z" class="box" title="ignored">
      <div class="cover"><img src="y.jpg" /></div>
      <div class="video-title"><strong>BBSS-106</strong> 別の作品</div>
      <div class="meta">
        2026-10-13
      </div>
    </a>
  </div>
  <div class="item">
    <a href="/v/Zx9Qa1" class="box" title="ignored">
      <div class="cover"><img src="z.jpg" /></div>
      <div class="video-title"><strong></strong> 合集 30 部打包</div>
      <div class="meta">
        2026-10-01
      </div>
    </a>
  </div>
</div>
"""

#: 旧艺名那一页：`/actors/MmbDR`（白石アイリ）2026-09-28 抓回来的标题栏与空列表。
JAVDB_EMPTY_ACTOR = """
<div class="columns is-desktop section-columns">
  <div class="column section-title">
    <h2 class="title is-4 has-text-justified">
      <span class="actor-section-name">白石アイリ</span>
      <br>
      <span class="section-meta">0 部影片</span>
    </h2>
  </div>
</div>
  <div class="empty-message">暂无内容</div>
"""

#: 本名那一页（`/actors/bkxd`）的标题栏：别名一行在前，作品数一行在后。
JAVDB_ACTOR_HEADER = """
    <h2 class="title is-4 has-text-justified">
      <span class="actor-section-name">森日向子</span>
        <br>
        <span class="section-meta">朝比奈えみり, ひー, ひなこちゃん, 雛子ちゃん, 日向結衣</span>
      <br>
      <span class="section-meta">1060 部影片</span>
    </h2>
"""


def _database(root: Path) -> LedgerDatabase:
    path = root / "ledger.db"
    upgrade(path, MIGRATIONS)
    return LedgerDatabase(path)


class FeedParsingTest(unittest.TestCase):
    def test_code_is_found_inside_a_title_full_of_prose(self):
        # 整段丢给 `release_code_from_text` 认不出来，词元扫描才认得出。
        title = "HMN-071 新人 帶來超稀有妹子 戶川步[有碼高清中文字幕]"
        self.assertEqual(feeds.scan_code(title), "HMN-071")
        self.assertEqual(feeds.scan_code("[H265 1080p] DSOD-114 花宮京子"), "DSOD-114")
        self.assertIsNone(feeds.scan_code("合集 30 部打包"))

    def test_javdb_actor_page_reads_code_and_release_date(self):
        parsed = feeds.parse_javdb_actor(JAVDB_ACTOR, "https://javdb.com/actors/pRMq")
        self.assertEqual([entry.item_key for entry in parsed.entries],
                         ["/v/5nr8mp", "/v/RkPb5z", "/v/Zx9Qa1"])
        self.assertEqual(parsed.entries[0].code, "PBD-528")
        self.assertEqual(parsed.entries[0].link, "https://javdb.com/v/5nr8mp")
        self.assertEqual(parsed.entries[0].published_at, "2026-10-20T00:00:00.000Z")
        self.assertIsNone(parsed.entries[2].code)

    def test_an_unknown_kind_is_a_parse_failure(self):
        # 账本里若留着别的类型，拉取把原因写在那一行上，不拿演员页的解析器硬读。
        self.assertIsNone(feeds.parse("rss", JAVDB_ACTOR.encode("utf-8"),
                                      "https://example.test/rss"))

    def test_a_page_without_any_work_is_a_failure_not_an_empty_poll(self):
        # 带查询串的演员页会回一份不含作品的页面。当成「这次没有新作」会让一个坏掉的
        # 订阅永远不报错。
        self.assertIsNone(feeds.parse_javdb_actor("<html>nothing</html>",
                                                  "https://javdb.com/actors/pRMq"))

    def test_a_page_that_says_it_has_no_work_is_a_quiet_source(self):
        # 旧艺名页常年是空的；页面自报 0 部，它就是一个没有作品的真实源。
        parsed = feeds.parse_javdb_actor(JAVDB_EMPTY_ACTOR, "https://javdb.com/actors/MmbDR")
        self.assertEqual((parsed.title, parsed.entries), ("白石アイリ", ()))

    def test_a_page_that_claims_works_but_lists_none_is_still_a_failure(self):
        self.assertIsNone(feeds.parse_javdb_actor(
            JAVDB_ACTOR_HEADER + '<div class="empty-message">暂无内容</div>',
            "https://javdb.com/actors/bkxd"))

    def test_the_page_name_is_read_from_the_title_bar(self):
        parsed = feeds.parse_javdb_actor(JAVDB_ACTOR_HEADER + JAVDB_ACTOR,
                                         "https://javdb.com/actors/bkxd")
        self.assertEqual(parsed.title, "森日向子")
        self.assertEqual(len(parsed.entries), 3)


class CompilationTest(unittest.TestCase):
    """标题与名单取自一份订阅实际抓回的壳。"""

    def test_marked_titles_and_long_cast_lists_are_group_compilations(self):
        cast = "、".join(f"女優{index}" for index in range(8))
        for title, performers in (
                ("ひくひくアナル丸見え！デカ尻バック激ピストンBEST", "奏音かのん、丘えりな"),
                ("乱交ベスト", ""),
                ("主観手コキ50連発", ""),
                ("絶対忠実秘書 BEST 8時間 Vol.01", "八掛うみ、野々浦暖"),
                ("季刊性年KMP 花火 ノーカット 1397分", ""),
                ("レス界隈奥さん他人棒で淫乱覚醒する巨乳人妻", cast)):
            with self.subTest(title=title):
                self.assertEqual(feeds.compilation_kind(title, performers), feeds.GROUP_COMPILATION)

    def test_one_listed_performer_with_a_marked_title_is_a_solo_compilation(self):
        for title in ("涼森れむ 8時間 BEST PRESTIGE PREMIUM RESTRICTED vol.12",
                      "涼森れむ 480分 総集編"):
            with self.subTest(title=title):
                self.assertEqual(feeds.compilation_kind(title, "涼森れむ"), feeds.SOLO_COMPILATION)

    def test_a_few_co_stars_and_ordinary_titles_are_not_compilations(self):
        for title, performers in (
                ("初めてのセックスが大優勝 ～神女優で童貞卒業～", "鈴村あいり、涼森れむ、八掛うみ"),
                ("月刊ハメ撮り 本能剥き出し3本番 涼森れむ", "涼森れむ"),
                ("BESTIE と過ごす2時間", ""),
                (None, None)):
            with self.subTest(title=title):
                self.assertIsNone(feeds.compilation_kind(title, performers))

    def test_the_highlight_studio_is_an_excerpt_whatever_the_title_says(self):
        self.assertEqual(feeds.compilation_kind("愛する妻が…", "篠田ゆう", "ハイライト"),
                         feeds.EXCERPT)
        self.assertEqual(feeds.compilation_kind("涼森れむ 480分 総集編", "涼森れむ", "ハイライト"),
                         feeds.EXCERPT)
        self.assertIsNone(feeds.compilation_kind("愛する妻が…", "篠田ゆう", "マドンナ"))


class FeedStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.database = _database(self.root)
        self.addCleanup(self._tmp.cleanup)

    def _source(self, **kwargs) -> int:
        with self.database.write_transaction() as connection:
            return feeds.add_source(connection,
                                    url=kwargs.pop("url", "https://javdb.com/actors/pRMq"),
                                    **kwargs)

    def _poll(self, source_id: int) -> dict:
        parsed = feeds.parse_javdb_actor(JAVDB_ACTOR, "https://javdb.com/actors/pRMq")
        with self.database.write_transaction() as connection:
            source = connection.execute("SELECT * FROM feed_source WHERE id=?",
                                        (source_id,)).fetchone()
            return feeds.poll(connection, source, parsed)

    def test_https_only(self):
        with self.assertRaises(ValueError):
            feeds.normalize_url("http://example.test/rss")

    def test_same_url_twice_is_refused(self):
        self._source()
        with self.assertRaises(ValueError):
            self._source()

    def test_first_poll_records_every_entry_and_shells_the_coded_ones(self):
        source_id = self._source()
        outcome = self._poll(source_id)
        self.assertEqual(outcome["seen"], 3)
        self.assertEqual(outcome["fresh"], 3)
        # 解不出番号的条目照样记一行，只是不建壳。
        self.assertEqual(outcome["without_code"], 1)
        self.assertEqual(sorted(code for _id, code in outcome["created"]),
                         ["BBSS-106", "PBD-528"])
        with self.database.read_connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM feed_item").fetchone()[0], 3)

    def test_second_poll_of_the_same_document_creates_nothing(self):
        source_id = self._source()
        self._poll(source_id)
        outcome = self._poll(source_id)
        self.assertEqual(outcome["fresh"], 0)
        self.assertEqual(outcome["created"], [])

    def test_two_sources_reporting_one_code_share_a_single_shell(self):
        first = self._source(url="https://javdb.com/actors/pRMq")
        second = self._source(url="https://javdb.com/actors/d45k9")
        self._poll(first)
        outcome = self._poll(second)
        # 源内那一层是各自的，所以第二个源仍然把三条都记成新条目……
        self.assertEqual(outcome["fresh"], 3)
        # ……但全局那一层只认番号，壳一个都不再建。
        self.assertEqual(outcome["created"], [])
        with self.database.read_connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM feed_discovery").fetchone()[0], 2)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM feed_item").fetchone()[0], 6)

    def test_a_code_already_in_the_library_gets_no_shell(self):
        with self.database.write_transaction() as connection:
            connection.execute(
                "INSERT INTO asset(location,path,name,medium,code) "
                "VALUES('R','R:\\\\media\\\\pbd528.mp4','pbd528.mp4','video','pbd00528')")
        source_id = self._source()
        outcome = self._poll(source_id)
        # 归一化之后 `pbd00528` 与 `PBD-528` 是同一部片。
        self.assertEqual([code for _id, code in outcome["created"]], ["BBSS-106"])

    def test_read_and_ignore_are_independent_and_do_not_touch_dedupe(self):
        source_id = self._source()
        created = self._poll(source_id)["created"]
        discovery_id = created[0][0]
        with self.database.write_transaction() as connection:
            connection.execute("UPDATE feed_discovery SET read_at=? WHERE id=?",
                               (feeds.stamp(), discovery_id))
            connection.execute("UPDATE feed_discovery SET ignored_at=? WHERE id=?",
                               (feeds.stamp(), discovery_id))
            row = connection.execute(
                "SELECT read_at,ignored_at FROM feed_discovery WHERE id=?",
                (discovery_id,)).fetchone()
        self.assertIsNotNone(row["read_at"])
        self.assertIsNotNone(row["ignored_at"])
        # 忽略之后再拉一轮，这条既不重建也不变回未忽略。
        outcome = self._poll(source_id)
        self.assertEqual(outcome["created"], [])
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT ignored_at FROM feed_discovery WHERE id=?",
                (discovery_id,)).fetchone()
        self.assertIsNotNone(row["ignored_at"])

    def test_settle_writes_the_next_time_on_every_path(self):
        source_id = self._source()
        with self.database.write_transaction() as connection:
            feeds.settle(connection, source_id, error="来源回了 HTTP 503",
                         interval_minutes=60)
        with self.database.read_connection() as connection:
            row = connection.execute("SELECT * FROM feed_source WHERE id=?",
                                     (source_id,)).fetchone()
        self.assertEqual(row["last_error"], "来源回了 HTTP 503")
        self.assertIsNotNone(row["next_fetch_at"])
        self.assertGreater(row["next_fetch_at"], row["last_fetched_at"])

    def test_disabled_sources_are_never_due(self):
        source_id = self._source()
        with self.database.write_transaction() as connection:
            feeds.set_enabled(connection, source_id, False)
        with self.database.read_connection() as connection:
            self.assertEqual(feeds.due_sources(connection), [])

    def test_performer_names_match_existing_entities_only(self):
        with self.database.write_transaction() as connection:
            connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('performer','深田えいみ',peach_normalize('深田えいみ'),?,?)",
                (feeds.stamp(), feeds.stamp()))
        with self.database.read_connection() as connection:
            found = feeds.match_performers(connection, ["深田えいみ", "查无此人"])
        self.assertEqual(len(found), 1)


class ShellBoundaryTest(unittest.TestCase):
    """壳不进 asset，所以整理、抽帧与馆藏统计看不到它（ADR-0042 第二条）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.database = _database(Path(self._tmp.name))
        self.addCleanup(self._tmp.cleanup)

    def _poll_once(self, connection) -> int:
        source_id = feeds.add_source(connection, url="https://javdb.com/actors/pRMq")
        feeds.poll(connection, connection.execute(
            "SELECT * FROM feed_source WHERE id=?", (source_id,)).fetchone(),
            feeds.parse_javdb_actor(JAVDB_ACTOR, "https://javdb.com/actors/pRMq"))
        return source_id

    def test_a_shell_adds_no_asset_row(self):
        with self.database.write_transaction() as connection:
            self._poll_once(connection)
        with self.database.read_connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM asset").fetchone()[0], 0)
            self.assertGreater(connection.execute(
                "SELECT count(*) FROM feed_discovery").fetchone()[0], 0)

    def test_the_shell_table_is_not_an_asset_reference(self):
        # 物理删除资产时要清的引用名单里没有它：壳不引用 asset，两者只按番号对上。
        from peach.web_batch import ASSET_REFERENCE_TABLES
        self.assertNotIn("feed_discovery", ASSET_REFERENCE_TABLES)
        self.assertNotIn("feed_item", ASSET_REFERENCE_TABLES)

    def test_deleting_a_source_takes_its_items_but_leaves_the_shell(self):
        # 壳是「这个番号还没入库」，它不该随着某个订阅被删而消失。连接和服务一样不开
        # 外键：子表靠 `remove_source` 自己处理，不靠建表时的 CASCADE 声明。
        with self.database.write_transaction() as connection:
            feeds.remove_source(connection, self._poll_once(connection))
        with self.database.read_connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM feed_item").fetchone()[0], 0)
            self.assertEqual(tuple(connection.execute(
                "SELECT count(*), count(source_id) FROM feed_discovery").fetchone()), (2, 0))
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
