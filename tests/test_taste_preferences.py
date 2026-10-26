"""多维偏好、混合平台和本机浏览器配置的行为验证。"""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, nullcontext
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
from types import SimpleNamespace

from peach.taste_browsers import browser_sources, configure_browser, enabled_history_sources
from peach.taste_history import HistorySource, _url_candidates, import_history_exports, refresh_history
from peach.taste_preferences import preference_profile, preference_score
from peach.taste_sites import account_key, mixed_adult
from peach.related import rank_related


class TastePreferenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.store = self.root / "history.sqlite"
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript(
            "CREATE TABLE entity(id INTEGER PRIMARY KEY,kind TEXT,canonical_name TEXT,normalized_name TEXT);"
            "CREATE TABLE entity_alias(entity_id INTEGER,alias TEXT);"
            "CREATE TABLE entity_link(entity_id INTEGER,url TEXT);"
            "CREATE TABLE follow_source(entity_id INTEGER,url TEXT,enabled INTEGER);"
            "CREATE TABLE asset(id INTEGER PRIMARY KEY,creator TEXT,studio TEXT,duration REAL,release_date TEXT,"
            "play_count INTEGER,play_seconds REAL,o_count INTEGER,rating INTEGER,feedback TEXT,last_played REAL,disposal TEXT,medium TEXT);"
            "CREATE TABLE asset_preference(profile_id TEXT,asset_id INTEGER,liked INTEGER);"
            "CREATE TABLE asset_entity(asset_id INTEGER,entity_id INTEGER);"
        )
        self.db.executemany("INSERT INTO entity VALUES(?,?,?,?)", [
            (1, "performer", "演示女优", "演示女优"), (2, "tag", "恋足", "恋足"),
            (3, "studio", "演示厂牌", "演示厂牌"), (4, "series", "演示系列", "演示系列"),
            (5, "performer", "其他女优", "其他女优")])
        self.db.execute("INSERT INTO entity_alias VALUES(1,'demo_actor')")

    def asset(self, aid, *, played=0, feedback=None):
        self.db.execute("INSERT INTO asset VALUES(?,NULL,NULL,7200,'2020-01-01',?, ?,0,0,?,NULL,NULL,'video')",
                        (aid, played, played * 600, feedback))
        self.db.executemany("INSERT INTO asset_entity VALUES(?,?)", [(aid, eid) for eid in (1, 2, 3, 4)])

    def history(self, visits):
        path = self.root / "export.json"
        path.write_text(json.dumps([{"url": url, "metadata": {"title": title}, "dt": datetime.fromisoformat(f"2026-10-{day:02d}T00:00:00+00:00").timestamp()}
                                    for day, url, title in visits]), encoding="utf-8")
        import_history_exports([path], self.store, host="test")

    def test_readonly_profile_preserves_history_and_does_not_create_preference_caches(self):
        self.asset(1, played=2)
        self.history([(1, 'https://example.invalid/work', 'demo_actor')])
        before = self.store.read_bytes()
        with patch('peach.fsutil.atomic_write_text', side_effect=AssertionError('只读计算不能写缓存')):
            strengths, _, _ = preference_profile(self.db, self.store, cache=False)
        self.assertGreater(strengths['performer'][1], 0)
        self.assertEqual(self.store.read_bytes(), before)
        self.assertFalse((self.store.parent / 'preference-cache.json').exists())
        self.assertFalse((self.store.parent / 'browser-preference-cache.json').exists())

    def test_bookmarks_feed_preferences_without_creating_visits_and_removal_clears_evidence(self):
        from peach.taste_history import build_taste_dashboard, remove_history_source
        path = self.root / "bookmarks.html"
        path.write_text('<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><DT><A '
                        'HREF="https://rule34.xxx/?tags=feet">私有书签标题</A></DL>', encoding="utf-8")
        first = import_history_exports([path], self.store, host="test")
        second = import_history_exports([path], self.store, host="test")
        self.assertEqual((first[0]["added"], second[0]["added"]), (1, 0))
        strengths, _, evidence = preference_profile(self.db, self.store)
        self.assertGreater(strengths["tag"][2], 0)
        self.assertEqual(evidence["tag"][2], ["浏览器书签"])
        dashboard = build_taste_dashboard(self.store, self.db)
        self.assertEqual(dashboard["summary"]["history_visits"], 0)
        self.assertEqual(dashboard["summary"]["bookmarks"], 1)
        self.assertEqual(dashboard["activity"]["days"], [])
        self.assertEqual(dashboard["rankings"]["browser_tags"][0]["name"], "feet")
        self.assertEqual(dashboard["rankings"]["browser_tags"][0]["web_bookmarks"], 1)
        self.assertEqual(dashboard["rankings"]["browser_tags"][0]["evidence"], ["浏览器书签"])
        self.assertNotIn("私有书签标题", json.dumps(dashboard, ensure_ascii=False))
        self.assertNotIn("rule34.xxx/?tags", json.dumps(dashboard))
        self.assertEqual(build_taste_dashboard(self.store, self.db, since="2026-10-01")["summary"]["bookmarks"], 0)
        self.assertEqual(remove_history_source(self.store, dashboard["sources"][0]["source_key"]), 1)
        self.assertFalse(preference_profile(self.db, self.store)[0]["tag"])

    def test_local_bookmark_only_profile_supports_stop_resume_and_snapshot_deletion(self):
        from peach.taste_browsers import enabled_bookmark_sources
        from peach.taste_bookmarks import refresh_bookmarks
        path = self.root / "Chrome" / "Default" / "Bookmarks"
        path.parent.mkdir(parents=True)
        def save(children):
            path.write_text(json.dumps({"roots": {"bookmark_bar": {"children": children}}}), encoding="utf-8")
        save([{"type": "url", "url": "https://rule34.xxx/?tags=feet", "name": "演示书签", "date_added": "13344473600000000"}])
        with patch("peach.taste_history.discover_history_sources", return_value=[]):
            configure_browser(self.store, "add", {"name": "Chrome", "path": str(path.parent.parent)})
            self.assertEqual(enabled_history_sources(self.store), [])
            sources = enabled_bookmark_sources(self.store)
            self.assertEqual(len(sources), 1)
            self.assertEqual(refresh_bookmarks(sources, self.store)[0]["added"], 1)
            key = browser_sources(self.store)[0]["id"]
            self.assertEqual(browser_sources(self.store)[0]["profiles"], 1)
            configure_browser(self.store, "disable", {"id": key})
            self.assertEqual(enabled_bookmark_sources(self.store), [])
            self.assertGreater(preference_profile(self.db, self.store)[0]["tag"][2], 0)
            configure_browser(self.store, "enable", {"id": key})
            save([])
            refresh_bookmarks(enabled_bookmark_sources(self.store), self.store)
            self.assertFalse(preference_profile(self.db, self.store)[0]["tag"])

    def test_browser_deletion_clears_both_snapshots_and_can_be_added_again(self):
        from peach.taste_browsers import enabled_bookmark_sources
        from peach.taste_bookmarks import refresh_bookmarks
        path = self.root / "Chrome/Default/Bookmarks"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"roots": {"bar": {"children": [
            {"type": "url", "url": "https://rule34.xxx/?tags=feet", "name": "恋足"}]}}}), encoding="utf-8")
        history = path.with_name("History")
        with closing(sqlite3.connect(history)) as db:
            db.executescript("CREATE TABLE urls(id INTEGER PRIMARY KEY,url TEXT,title TEXT);"
                             "CREATE TABLE visits(id INTEGER PRIMARY KEY,url INTEGER,visit_time INTEGER,visit_duration INTEGER);"
                             "INSERT INTO urls VALUES(1,'https://javdb.com/v/demo','恋足');"
                             "INSERT INTO visits VALUES(1,1,13344473600000000,0);")
            db.commit()
        discovered = [HistorySource("chrome", "Default", history, root=path.parent.parent, engine="chrome")]
        with patch("peach.taste_history.discover_history_sources", return_value=discovered):
            key = browser_sources(self.store)[0]["id"]
            refresh_history(enabled_history_sources(self.store), self.store)
            refresh_bookmarks(enabled_bookmark_sources(self.store), self.store)
            self.assertEqual(len(browser_sources(self.store)[0]["source_keys"]), 2)
            stale_history = enabled_history_sources(self.store)
            stale_bookmarks = enabled_bookmark_sources(self.store)
            with patch("peach.taste_browsers.browser_snapshot_keys", return_value=[]):
                configure_browser(self.store, "delete_browser", {"id": key})
            refresh_history(stale_history, self.store)
            refresh_bookmarks(stale_bookmarks, self.store)
            self.assertEqual(browser_sources(self.store), [])
            self.assertFalse(preference_profile(self.db, self.store)[0]["tag"])
            with closing(sqlite3.connect(self.store)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM history_source").fetchone()[0], 0)
            self.assertTrue(history.is_file() and path.is_file())
            configure_browser(self.store, "add", {"name": "Chrome", "path": str(path.parent.parent)})
            self.assertEqual(len(enabled_history_sources(self.store)), 1)
            self.assertEqual(len(enabled_bookmark_sources(self.store)), 1)

    def test_collection_accumulates_weak_evidence_and_explicit_watches_are_stronger(self):
        for aid in range(1, 41):
            self.asset(aid)
        candidate = {"entities": {"performer": {1}, "tag": {2}, "studio": {3}, "series": {4}},
                     "release_date": "2021-01-01", "duration": 7200}
        collected = preference_profile(self.db, self.store)
        weak, reasons = preference_score(candidate, *collected)
        self.assertGreater(weak, .25)
        self.assertLess(weak, .5)
        self.assertTrue(any("馆藏积累" in reason for reason in reasons))
        self.db.execute("UPDATE asset SET play_count=8,play_seconds=6000,rating=100 WHERE id<=20")
        strong = preference_score(candidate, *preference_profile(self.db, self.store))[0]
        self.assertGreater(strong, .75)
        self.assertGreater(strong, weak)

    def test_unrelated_user_interests_do_not_dilute_a_matching_candidate(self):
        self.asset(1, played=12)
        candidate = {"entities": {"performer": {1}, "tag": {2}, "studio": {3}, "series": {4}}}
        score = preference_score(candidate, *preference_profile(self.db, self.store))[0]
        for eid in range(10, 100):
            self.db.execute("INSERT INTO entity VALUES(?,'tag',?,?)", (eid, f"其他标签{eid}", f"其他标签{eid}"))
            self.db.execute("INSERT INTO asset_entity VALUES(1,?)", (eid,))
        self.assertEqual(score, preference_score(candidate, *preference_profile(self.db, self.store))[0])

    def test_known_social_accounts_count_and_unrelated_accounts_do_not(self):
        self.db.execute("INSERT INTO entity_link VALUES(1,'https://twitter.com/demo_actor')")
        self.db.execute("INSERT INTO follow_source VALUES(1,'https://x.com/demo_actor',1)")
        self.history([(1, "https://x.com/demo_actor/status/123", "演示女优 的社媒标题"),
                      (2, "https://x.com/unrelated/status/321", "普通新闻"),
                      (3, "https://x.com/home", "演示女优")])
        strengths, names, evidence = preference_profile(self.db, self.store)
        self.assertEqual(set(strengths["performer"]), {1})
        self.assertEqual(evidence["performer"][1], ["浏览记录", "关注"])
        self.assertEqual(names["performer"][1], "演示女优")
        self.db.execute("UPDATE follow_source SET enabled=0")
        self.assertLess(preference_profile(self.db, self.store)[0]["performer"][1], strengths["performer"][1])

    def test_browser_preferences_read_daily_identity_visits_without_dashboard_aggregates(self):
        self.history([(1, 'https://javdb.com/v/demo', '演示女优'),
                      (2, 'https://javdb.com/v/demo', '演示女优')])
        from peach.taste_evidence import browser_entities
        with patch('peach.taste_history._read_history_rows', side_effect=AssertionError('口味看板全量统计')), \
                patch('peach.taste_evidence.browser_entities', wraps=browser_entities) as reads:
            strengths, names, evidence = preference_profile(self.db, self.store)
        self.assertEqual(reads.call_count, 1)
        self.assertGreater(strengths['performer'][1], 0)
        self.assertEqual(names['performer'][1], '演示女优')
        self.assertEqual(evidence['performer'][1], ['浏览记录'])

    def test_browser_identity_cache_survives_watch_changes_and_tracks_aliases_and_history(self):
        from peach.taste_evidence import browser_entities
        self.history([(1, 'https://javdb.com/v/demo', 'demo_actor')])
        with patch('peach.taste_evidence.browser_entities', wraps=browser_entities) as reads:
            first = preference_profile(self.db, self.store)
            self.asset(1, played=3)
            watched = preference_profile(self.db, self.store)
            self.assertEqual(reads.call_count, 1)
            self.assertGreater(watched[0]['performer'][1], first[0]['performer'][1])
            self.db.execute("UPDATE entity_alias SET alias='another_actor'")
            unmatched = preference_profile(self.db, self.store)
            self.assertEqual(reads.call_count, 2)
            self.assertNotIn('浏览记录', unmatched[2]['performer'][1])
            self.history([(2, 'https://javdb.com/v/demo2', 'another_actor')])
            imported = preference_profile(self.db, self.store)
            self.assertGreater(reads.call_count, 2)
            self.assertIn('浏览记录', imported[2]['performer'][1])

    def test_adult_product_titles_and_search_aliases_map_to_existing_entities(self):
        self.history([(1, "https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=demo001/", "演示女优 的作品"),
                      (2, "https://www.mgstage.com/search/cSearch.php?search_word=demo_actor", "作品搜索"),
                      (3, "https://rule34.xxx/index.php?page=post&s=list&tags=footjob", "标签搜索")])
        strengths, _, evidence = preference_profile(self.db, self.store)
        self.assertIn(1, strengths["performer"])
        self.assertEqual(evidence["performer"][1], ["浏览记录"])

    def test_titles_and_urls_share_content_and_identity_evidence(self):
        from peach.taste_history import build_taste_dashboard
        self.history([(1, "https://javdb.com/v/abc123", "演示女优 演示厂牌 演示系列 足フェチ"),
                      (2, "https://rule34.xxx/?tags=feet+foot_fetish", "Foot Fetish 恋足")])
        bookmark = self.root / "bookmarks.html"
        bookmark.write_text('<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><DT><A '
                            'HREF="https://javbus.com/ABC-123">演示女优 demo_actor 演示厂牌 演示系列 足フェチ 制服</A></DL>', encoding="utf-8")
        import_history_exports([bookmark], self.store)
        strengths, _, evidence = preference_profile(self.db, self.store)
        for kind, eid in (("performer", 1), ("tag", 2), ("studio", 3), ("series", 4)):
            self.assertGreater(strengths[kind][eid], 0)
            self.assertEqual(evidence[kind][eid], ["浏览记录", "浏览器书签"])
        dashboard = build_taste_dashboard(self.store, self.db)
        self.assertEqual(dashboard["summary"]["history_visits"], 2)
        self.assertEqual(dashboard["summary"]["bookmarks"], 1)
        self.assertEqual(sum(row["web_visits"] for row in dashboard["rankings"]["browser_tags"]), 4)
        row = dashboard["rankings"]["performers"][0]
        self.assertEqual((row["web_visits"], row["web_bookmarks"]), (2, 1))

    def test_title_genres_use_boundaries_context_and_longest_terms(self):
        adult = "https://javdb.com/v/abc123"
        self.assertEqual(_url_candidates(adult, "生放送会場 Metro Platform Oral History")[1], set())
        self.assertEqual(_url_candidates(adult, "ANAL SEX 足フェチ 中出し 制服")[1],
                         {"肛交", "恋足", "中出内射", "制服"})
        self.assertEqual(_url_candidates(adult, "ＡＳＭＲ 深喉")[1], {"ASMR", "深喉"})
        self.assertEqual(_url_candidates("https://unknown.example/1", "Foot Fetish")[1], set())
        self.assertEqual(_url_candidates("https://x.com/demo/status/1", "Foot Fetish")[1], set())
        self.assertEqual(_url_candidates("https://x.com/demo/status/1", "NSFW Foot Fetish")[1], {"恋足"})
        self.assertEqual(_url_candidates("https://rule34.xxx/?tags=feet+foot_fetish", "恋足 Foot Fetish")[1], {"feet"})
        self.assertEqual(_url_candidates("https://rule34.xxx/?tags=feet+-cosplay", "feet -cosplay")[1], {"feet"})

    def test_unreadable_private_history_does_not_supply_browser_preferences(self):
        self.store.write_bytes(b"not a SQLite database")
        self.assertFalse(any(preference_profile(self.db, self.store)[0].values()))

    def test_disliked_assets_do_not_provide_collection_or_watch_evidence(self):
        self.asset(1, played=12, feedback="dislike")
        self.assertFalse(any(preference_profile(self.db, self.store)[0].values()))

    def test_known_male_performers_are_recorded_without_preference_evidence(self):
        self.db.execute('ALTER TABLE entity ADD COLUMN metadata_json TEXT')
        self.db.execute("UPDATE entity SET metadata_json=? WHERE id=1", (json.dumps({'gender': 'male'}),))
        self.asset(1, played=12)
        self.db.execute("INSERT INTO entity_link VALUES(1,'https://x.com/demo_actor')")
        self.db.execute("INSERT INTO follow_source VALUES(1,'https://x.com/demo_actor',1)")
        self.history([(1, 'https://x.com/demo_actor/status/123', '演示女优 的作品')])
        strengths, _, evidence = preference_profile(self.db, self.store)
        self.assertNotIn(1, strengths['performer'])
        self.assertNotIn(1, evidence['performer'])
        self.assertEqual(json.loads(self.db.execute('SELECT metadata_json FROM entity WHERE id=1').fetchone()[0]), {'gender': 'male'})

    def test_mmr_accepts_preference_relevance_without_changing_movie_similarity(self):
        pool = [{"id": 1, "entities": {"tag": {10}}, "taste": .9},
                {"id": 2, "entities": {"tag": {10}}, "taste": .89},
                {"id": 3, "entities": {"tag": {20}}, "taste": .8}]
        picked = rank_related({"id": 0}, pool, 3, relevance_for=lambda row: row["taste"])
        self.assertEqual([row["id"] for row in picked], [1, 3, 2])
        self.assertEqual([row["related_score"] for row in picked], [.9, .8, .89])
        self.assertEqual(rank_related({"id": 0}, pool, 3), [])

    def test_discovery_displays_the_same_preference_score_in_descending_and_ascending_order(self):
        from peach import web_jav_discovery
        self.asset(1, played=12)
        candidates = [{"id": aid, "code": f"DEMO-{aid}", "entities": {"performer": {1}, "tag": tags,
                       "studio": {3}, "series": {4}}} for aid, tags in ((10, {2}), (11, {2}), (12, set()))]
        contract = SimpleNamespace(read_connection=lambda: nullcontext(self.db), taste_history_store=self.store,
                                   has_cover=lambda _: False, cover_frame=lambda _: None,
                                   cover_version=lambda _: None, poster_box=lambda _: None)
        with patch.object(web_jav_discovery, "_external", return_value=candidates), \
             patch.object(web_jav_discovery, "_metadata_response", return_value={}), \
             patch.object(web_jav_discovery.wants, "wanted_keys", return_value=set()), \
             patch.object(web_jav_discovery.web_feeds, "_discovery_performers", return_value={10: [], 11: [], 12: []}):
            descending = web_jav_discovery.q_jav_discoveries(contract, {})["items"]
            ascending = web_jav_discovery.q_jav_discoveries(contract, {"direction": "asc"})["items"]
        scores = [row["taste_score"] for row in descending]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual([row["taste_score"] for row in ascending], sorted(scores))
        self.assertEqual({row["id"]: row["taste_score"] for row in descending},
                         {row["id"]: row["taste_score"] for row in ascending})

    def test_mixed_platforms_require_a_relevant_partition_or_account(self):
        self.assertFalse(mixed_adult("https://www.dmm.com/"))
        self.assertTrue(mixed_adult("https://www.dmm.com/fanza/"))
        self.assertFalse(mixed_adult("https://www.dlsite.com/home/work/"))
        self.assertTrue(mixed_adult("https://www.dlsite.com/maniax/work/"))
        self.assertEqual(account_key("https://twitter.com/demo_actor/status/123"), account_key("https://x.com/demo_actor"))
        self.assertIsNone(account_key("https://x.com/i/flow/login"))
        self.assertIsNone(account_key("https://javdb.com/actors/a"))
        self.assertEqual(account_key("https://example.fanbox.cc/posts/123"), ("fanbox.cc", "example"))
        self.assertIsNone(account_key("https://pixiv.net/artworks/123"))
        self.assertEqual(account_key("https://pixiv.net/member.php?id=123"), account_key("https://pixiv.net/users/123"))
        self.assertEqual(account_key("https://patreon.com/c/demo_actor/about"), account_key("https://patreon.com/demo_actor"))
        for url in ("https://www.dmm.co.jp/search/=/searchstr=制服/", "https://mgstage.com/search?search_word=制服"):
            self.assertIn("制服", _url_candidates(url)[1])
        self.assertEqual(_url_candidates("https://x.com/unrelated?search=feet")[1:], (set(), set()))
        _, tags, creators = _url_candidates("https://rule34.xxx/?tags=feet+sort:score+rating:explicit+artist:demo_artist+-cosplay")
        self.assertEqual(tags, {"feet"})
        self.assertEqual(creators, {"demo_artist"})
        self.assertEqual(_url_candidates("https://x.com/demo_artist", "NSFW creator")[2], {"demo_artist"})
        self.assertEqual(_url_candidates("https://[invalid"), (None, set(), set()))
        self.assertEqual(_url_candidates("https://unknown.example/search?q=feet", "普通页面")[1], set())
        self.assertEqual(_url_candidates("https://unknown.example/search?q=feet", "NSFW art")[1], {"feet"})

    def test_platform_root_links_do_not_bind_unrelated_accounts(self):
        from peach.taste_sites import identity_context, linked_identities
        self.db.execute("INSERT INTO entity_link VALUES(1,'https://x.com/')")
        self.db.execute("INSERT INTO entity_link VALUES(5,'https://pixiv.net/artworks/123')")
        accounts, _ = identity_context(self.db)
        self.assertEqual(linked_identities("https://x.com/", accounts), set())
        self.assertEqual(linked_identities("https://x.com/unrelated", accounts), set())
        self.assertEqual(linked_identities("https://pixiv.net/artworks/456", accounts), set())

    def test_browser_registration_stop_resume_and_idempotent_sync(self):
        path = self.root / "Aside" / "Default" / "History"
        path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(path)) as db:
            db.executescript("CREATE TABLE urls(id INTEGER PRIMARY KEY,url TEXT,title TEXT);"
                             "CREATE TABLE visits(id INTEGER PRIMARY KEY,url INTEGER,visit_time INTEGER,visit_duration INTEGER);")
            db.execute("INSERT INTO urls VALUES(1,'https://rule34.xxx/?tags=feet','私有标题')")
            db.execute("INSERT INTO visits VALUES(1,1,13344473600000000,0)")
            db.commit()
        with patch("peach.taste_history.discover_history_sources", return_value=[]):
            configure_browser(self.store, "add", {"name": "Aside", "path": str(path.parent.parent)})
            sources = enabled_history_sources(self.store)
            self.assertEqual([(source.browser, source.profile) for source in sources], [("aside", "Default")])
            first = refresh_history(sources, self.store, host="test")
            second = refresh_history(sources, self.store, host="test")
            self.assertEqual(first[0]["added"], 1)
            self.assertEqual(second[0]["added"], 0)
            key = browser_sources(self.store)[0]["id"]
            configure_browser(self.store, "disable", {"id": key})
            self.assertEqual(enabled_history_sources(self.store), [])
            with closing(sqlite3.connect(self.store)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM history_visit").fetchone()[0], 1)
            configure_browser(self.store, "enable", {"id": key})
            self.assertEqual(len(enabled_history_sources(self.store)), 1)
            with self.assertRaisesRegex(ValueError, "未找到"):
                configure_browser(self.store, "add", {"name": "缺失", "path": str(self.root / "missing")})

    def test_preference_cache_survives_process_cache_loss_and_refreshes_after_evidence_changes(self):
        from peach import taste_preferences
        self.asset(1, played=4)
        self.db.commit()
        path = self.root / "ledger.sqlite"
        with closing(sqlite3.connect(path)) as db:
            self.db.backup(db)
            db.row_factory = sqlite3.Row
            first = preference_profile(db, self.store)
            taste_preferences._PROFILES.clear()
            with patch.object(taste_preferences, "preference_evidence", side_effect=AssertionError("不应重新汇总")):
                second = preference_profile(db, self.store)
            self.assertEqual(first[0], second[0])
            self.assertTrue(all(isinstance(eid, int) for eid in second[0]["performer"]))
            db.execute("INSERT INTO asset_preference VALUES('local-default',1,1)")
            db.commit()
            refreshed = preference_profile(db, self.store)
            self.assertGreater(refreshed[0]["performer"][1], first[0]["performer"][1])

    def test_custom_history_filename_uses_its_schema_instead_of_the_browser_label(self):
        path = self.root / "private-backup.sqlite"
        with closing(sqlite3.connect(path)) as db:
            db.executescript("CREATE TABLE moz_places(id INTEGER PRIMARY KEY,url TEXT,title TEXT,description TEXT,preview_image_url TEXT);"
                             "CREATE TABLE moz_historyvisits(id INTEGER PRIMARY KEY,place_id INTEGER,visit_date INTEGER);")
            db.execute("INSERT INTO moz_places VALUES(1,'https://rule34.xxx/?tags=feet','私有标题',NULL,NULL)")
            db.execute("INSERT INTO moz_historyvisits VALUES(1,1,1700000000000000)")
            db.commit()
        with patch("peach.taste_history.discover_history_sources", return_value=[]):
            configure_browser(self.store, 'add', {'name': '自定义浏览器', 'path': str(path)})
            sources = enabled_history_sources(self.store)
            self.assertEqual(sources[0].engine, 'firefox')
            self.assertEqual(refresh_history(sources, self.store)[0]['added'], 1)

    def test_task_heartbeats_preserve_preferences_and_asset_updates_refresh_them(self):
        from peach import taste_preferences
        self.asset(1, played=1)
        self.db.commit()
        with closing(sqlite3.connect(self.root / 'ledger-revisions.sqlite')) as db:
            self.db.backup(db)
            db.row_factory = sqlite3.Row
            db.executescript("CREATE TABLE ledger_revision(tbl TEXT PRIMARY KEY,n INTEGER);"
                             "INSERT INTO ledger_revision VALUES('asset',1),('task_run',1);")
            first = preference_profile(db, self.store)
            db.execute("UPDATE ledger_revision SET n=n+1 WHERE tbl='task_run'")
            db.commit()
            with patch.object(taste_preferences, 'preference_evidence', side_effect=AssertionError('任务心跳')):
                self.assertEqual(preference_profile(db, self.store)[0], first[0])
            db.execute('UPDATE asset SET play_count=10,play_seconds=6000')
            db.execute("UPDATE ledger_revision SET n=n+1 WHERE tbl='asset'")
            db.commit()
            self.assertGreater(preference_profile(db, self.store)[0]['performer'][1], first[0]['performer'][1])

    def test_a_locked_browser_does_not_prevent_other_profiles_from_importing(self):
        from peach import taste_history
        path = self.root / "History"
        with closing(sqlite3.connect(path)) as db:
            db.executescript("CREATE TABLE urls(id INTEGER PRIMARY KEY,url TEXT,title TEXT);"
                             "CREATE TABLE visits(id INTEGER PRIMARY KEY,url INTEGER,visit_time INTEGER,visit_duration INTEGER);")
            db.execute("INSERT INTO urls VALUES(1,'https://rule34.xxx/?tags=feet','私有标题')")
            db.execute("INSERT INTO visits VALUES(1,1,13344473600000000,0)")
            db.commit()
        original = taste_history._consistent_copy
        locked = self.root / "locked"
        def copy(source, destination):
            if source == locked:
                raise sqlite3.OperationalError("database is locked")
            return original(source, destination)
        with patch.object(taste_history, "_consistent_copy", side_effect=copy):
            result = refresh_history([HistorySource('aside', 'busy', locked, engine='chrome'),
                                      HistorySource('chrome', 'ready', path)], self.store)
        self.assertEqual(result[0]["status"], "failed")
        self.assertIn("退出该浏览器", result[0]["error"])
        self.assertEqual(result[1]["added"], 1)
