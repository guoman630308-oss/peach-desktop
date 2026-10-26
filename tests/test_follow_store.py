"""追更订阅与候选落地的隔离测试。全部使用临时数据库，绝不碰真实 ledger。"""
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from peach.follow import FollowSourceError
from peach.follow_sources import FollowCandidate, SourceFetch
from peach.follow_store import (
    FollowStore, ReleaseGroup, author_display_text, f95_author_handles,
    f95_author_name, normalized_author_name,
)
from support.ledger import fresh_ledger


ROOT = Path(__file__).resolve().parents[1]
MOMENT = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def _candidate(external_id, title, **kwargs):
    kwargs.setdefault("provider", "rule34video")
    return FollowCandidate(external_id=external_id, title=title, **kwargs)


def _fetch(candidates, *, provider="rule34video", ref="lazyprocrastinator",
           semantics="work", raw=b"<html/>", **kwargs):
    return SourceFetch(
        provider=provider, ref=ref,
        request_url=f"https://{provider}.test/{ref}", semantics=semantics,
        candidates=tuple(candidates), raw_body=raw, **kwargs)


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.connection = sqlite3.connect(fresh_ledger(self.root))
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        self.store = FollowStore(lambda: self.connection,
                                 sources_root=self.root / "sources")

    def _entity(self, name="LazyProcrast", aliases=("LazyProcrastinator",)):
        cursor = self.connection.execute(
            "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES('creator',?,?,?,?)",
            (name, name.lower(), "2026-08-25T00:00:00Z", "2026-08-25T00:00:00Z"))
        entity_id = int(cursor.lastrowid)
        for alias in aliases:
            self.connection.execute(
                "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
                " VALUES(?,?,?,'test')", (entity_id, alias, alias.lower()))
        return entity_id

    def _source(self, provider="rule34video", ref="lazyprocrastinator", **kwargs):
        kwargs.setdefault("label", "LazyProcrastinator")
        kwargs.setdefault("url", f"https://{provider}.test/{ref}")
        return self.store.register(provider=provider, ref=ref, moment=MOMENT, **kwargs)


class RegistrationTests(_StoreCase):
    def test_register_is_idempotent_and_updates_the_label(self):
        first = self._source()
        second = self._source(label="Lazy P")
        self.assertEqual(first, second)
        rows = self.store.sources()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "Lazy P")

    def test_merging_metadata_keeps_the_keys_it_does_not_mention(self):
        """名片是登记之后才解析出来的，补它不能抹掉登记时写下的 author_key。"""
        source_id = self._source(metadata={"author_key": "mrstrauz"})
        self.store.merge_source_metadata(
            source_id, {"official_links": [{"service": "twitter",
                                            "handle": "Mr_Strauz"}]},
            moment=MOMENT)
        stored = json.loads(self.store.sources()[0]["metadata_json"])
        self.assertEqual(stored["author_key"], "mrstrauz")
        self.assertEqual([link["handle"] for link in stored["official_links"]],
                         ["Mr_Strauz"])

    def test_an_empty_profile_list_is_recorded_as_an_answer(self):
        # 「问过了，他没留主页」要留得下来，否则每次检查都要再问一遍。
        source_id = self._source()
        self.store.merge_source_metadata(source_id, {"official_links": []},
                                         moment=MOMENT)
        stored = json.loads(self.store.sources()[0]["metadata_json"])
        self.assertEqual(stored["official_links"], [])

    def test_rule34_case_variants_are_the_same_source(self):
        first = self._source(provider="rule34xxx", ref="LazyProcrastinator")
        second = self._source(provider="rule34xxx", ref="lazyprocrastinator")
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.sources()), 1)
        self.assertEqual(self.store.sources()[0]["ref"], "lazyprocrastinator")

    def test_register_keeps_an_existing_entity_when_the_update_omits_it(self):
        entity_id = self._entity()
        self._source(entity_id=entity_id)
        self._source(label="again")
        self.assertEqual(self.store.sources()[0]["entity_id"], entity_id)

    def test_enabled_only_filters_disabled_sources(self):
        source_id = self._source()
        self._source(provider="kemono", ref="fanbox/30917150")
        self.store.set_enabled(source_id, False, moment=MOMENT)
        self.assertEqual([row["provider"] for row in self.store.sources(enabled_only=True)],
                         ["kemono"])

    def test_bad_semantics_is_rejected(self):
        with self.assertRaises(FollowSourceError):
            self._source(semantics="whatever")

    def test_creator_aliases_include_canonical_name_and_aliases(self):
        entity_id = self._entity()
        self.assertEqual(self.store.creator_aliases(entity_id),
                         ("LazyProcrast", "LazyProcrastinator"))
        self.assertEqual(self.store.creator_aliases(None), ())


class RecordTests(_StoreCase):
    def test_booru_source_only_becomes_a_profile_when_the_handle_matches(self):
        source_id = self._source(
            provider="rule34xxx", ref="auxtasy", label="Auxtasy",
            metadata={"author_key": "auxtasy", "official_links": [
                {"service": "twitter", "handle": "OldWrongName",
                 "url": "https://x.com/OldWrongName"},
            ]},
        )
        self.store.record(source_id, _fetch([
            _candidate("1", "one", provider="rule34xxx",
                       extra={"source": "https://x.com/Auxtasy/status/1"}),
            _candidate("2", "two", provider="rule34xxx",
                       extra={"source": "https://www.patreon.com/futavr/posts/2"}),
        ], provider="rule34xxx", ref="auxtasy"), moment=MOMENT)

        metadata = json.loads(self.store.sources()[0]["metadata_json"])
        self.assertEqual(
            [(link["service"], link["handle"]) for link in metadata["official_links"]],
            [("twitter", "Auxtasy")],
        )

    def test_booru_source_accepts_a_confirmed_alias_but_not_a_collaborator(self):
        self.store.upsert_author_alias(
            "LazyProcrastinator", "lazyprocrast", source="manual", moment=MOMENT)
        source_id = self._source(
            provider="rule34xxx", ref="lazyprocrastinator",
            metadata={"author_key": "lazyprocrastinator"},
        )
        self.store.record(source_id, _fetch([
            _candidate("1", "one", provider="rule34xxx",
                       extra={"source": "https://lazyprocrast.fanbox.cc/posts/1"}),
            _candidate("2", "two", provider="rule34xxx",
                       extra={"source": "https://x.com/unrelated/status/2"}),
        ], provider="rule34xxx", ref="lazyprocrastinator"), moment=MOMENT)

        metadata = json.loads(self.store.sources()[0]["metadata_json"])
        self.assertEqual([link["handle"] for link in metadata["official_links"]],
                         ["lazyprocrast"])

    def test_backfill_pages_add_to_the_profile_links_the_first_page_found(self):
        """往回抓只并集追加出处：更早那几页看不到近期作品链出去的那些站。"""
        source_id = self._source(
            provider="rule34xxx", ref="auxtasy", label="Auxtasy",
            metadata={"author_key": "auxtasy"},
        )
        self.store.record(source_id, _fetch([
            _candidate("1", "one", provider="rule34xxx",
                       extra={"source": "https://x.com/Auxtasy/status/1"}),
        ], provider="rule34xxx", ref="auxtasy"), moment=MOMENT)
        self.store.record(source_id, _fetch([
            _candidate("9", "nine", provider="rule34xxx",
                       extra={"source": "https://auxtasy.fanbox.cc/posts/9"}),
        ], provider="rule34xxx", ref="auxtasy"), moment=MOMENT, page=2)

        metadata = json.loads(self.store.sources()[0]["metadata_json"])
        self.assertEqual([link["handle"] for link in metadata["official_links"]],
                         ["Auxtasy", "auxtasy"])

    def test_first_fetch_adds_items_and_classifies_variants(self):
        source_id = self._source(entity_id=self._entity())
        outcome = self.store.record(source_id, _fetch([
            _candidate("4542713", "Fiona - Paizuri"),
            _candidate("4542721", "Fiona - Paizuri (Nude)"),
            _candidate("4542899", "Mitsuru [WIP]"),
        ]), creator_aliases=self.store.creator_aliases(1), moment=MOMENT)
        self.assertEqual((outcome.added, outcome.updated, outcome.discovered), (3, 0, 3))
        kinds = {item.external_id: item.variant_kind for item in self.store.items()}
        self.assertEqual(kinds, {"4542713": "main", "4542721": "alt", "4542899": "wip"})

    def test_second_fetch_updates_without_resetting_user_state(self):
        source_id = self._source()
        self.store.record(source_id, _fetch([_candidate("1", "Fiona - Paizuri")]),
                          moment=MOMENT)
        item = self.store.items()[0]
        self.store.set_status(item.id, "ignored")
        later = MOMENT + timedelta(hours=1)
        outcome = self.store.record(
            source_id, _fetch([_candidate("1", "Fiona - Paizuri", url="https://x.test/1")],
                              raw=b"<html>2</html>"), moment=later)
        self.assertEqual((outcome.added, outcome.updated), (0, 1))
        refreshed = self.store.items()[0]
        self.assertEqual(refreshed.status, "ignored")
        self.assertEqual(refreshed.url, "https://x.test/1")
        self.assertEqual(refreshed.first_seen_at, item.first_seen_at)
        self.assertNotEqual(refreshed.last_seen_at, item.last_seen_at)

    def test_a_failed_detail_never_erases_a_known_upload_time(self):
        """paheal 每个候选都要单独打一次详情页，被限流时 `_detail` 返回 {}。

        此前 upsert 无条件覆盖，于是上一轮已经取到的上传时间和时长被抹成 NULL，
        而 `COALESCE(published_at, first_seen_at)` 会让界面改显示抓取时刻——
        看着是条完整记录，时间却是错的。实测 168 条 paheal 里 7 条正是这样。
        """
        source_id = self._source()
        self.store.record(source_id, _fetch([_candidate(
            "1", "A", published_at="2026-08-18T09:03:30Z", duration=44.7)]), moment=MOMENT)
        first = self.store.items()[0]
        self.assertEqual(first.published_at, "2026-08-18T09:03:30Z")

        later = MOMENT + timedelta(hours=1)
        self.store.record(source_id, _fetch([_candidate("1", "A")], raw=b"<html>2</html>"),
                          moment=later)
        again = self.store.items()[0]
        self.assertEqual(again.published_at, "2026-08-18T09:03:30Z")
        self.assertEqual(again.duration, 44.7)
        self.assertEqual(again.published_precision, "exact")

    def test_a_later_fetch_with_a_real_time_still_wins(self):
        """保守只针对「取不到」；来源真的给了新时间就要覆盖，不能变成只写一次。"""
        source_id = self._source()
        self.store.record(source_id, _fetch([_candidate("1", "A")]), moment=MOMENT)
        self.assertIsNone(self.store.items()[0].published_at)
        self.store.record(source_id, _fetch([_candidate(
            "1", "A", published_at="2026-08-18T09:03:30Z")], raw=b"<html>2</html>"),
            moment=MOMENT + timedelta(hours=1))
        item = self.store.items()[0]
        self.assertEqual(item.published_at, "2026-08-18T09:03:30Z")
        self.assertEqual(item.published_precision, "exact")

    def test_not_modified_records_the_check_without_touching_items(self):
        source_id = self._source()
        self.store.record(source_id, _fetch([_candidate("1", "A")]), moment=MOMENT)
        outcome = self.store.record(
            source_id, _fetch([], raw=None, not_modified=True), moment=MOMENT)
        self.assertTrue(outcome.not_modified)
        self.assertEqual(len(self.store.items()), 1)
        self.assertEqual(self.store.sources()[0]["last_status"], "not_modified")

    def test_conditional_cursor_is_stored_on_the_source_row(self):
        source_id = self._source()
        self.store.record(source_id, _fetch([_candidate("1", "A")], etag='"v1"',
                                            last_modified="Mon, 25 Aug 2026 09:00:00 GMT"),
                          moment=MOMENT)
        row = self.store.sources()[0]
        self.assertEqual(row["etag"], '"v1"')
        self.assertEqual(row["last_modified"], "Mon, 25 Aug 2026 09:00:00 GMT")

    def test_raw_evidence_is_written_once_and_immutably(self):
        source_id = self._source()
        outcome = self.store.record(source_id, _fetch([_candidate("1", "A")]),
                                    moment=MOMENT)
        # evidence_path 是相对 `sources/` 的路径，分隔符随平台不同——不能写死 `/`。
        evidence = self.root / "sources" / Path(outcome.evidence_path)
        self.assertTrue(evidence.is_file())
        sidecar = json.loads(evidence.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar["candidates"], 1)
        self.assertEqual(sidecar["request_url"], "https://rule34video.test/lazyprocrastinator")

    def test_an_unusable_evidence_root_does_not_lose_the_candidates(self):
        """Mac 上 `peach-data/sources` 指向外置盘。盘不在时它是一条断链，
        `mkdir(exist_ok=True)` 会抛 `FileExistsError`（链接在、目标不在）。
        发现跟归档盘无关，不该让整次检查连同已抓到的候选一起炸掉。

        这里用「路径被普通文件占住」构造同一个 `FileExistsError`：断链要 symlink，
        而非管理员的 Windows 建不了 symlink，那样测试就只在一个平台成立。
        """
        (self.root / "evidence").write_text("not a directory", encoding="utf-8")
        store = FollowStore(lambda: self.connection,
                            sources_root=self.root / "evidence")
        source_id = store.register(provider="rule34video", ref="x", label="X",
                                   url="https://x.test/", moment=MOMENT)
        outcome = store.record(source_id, _fetch([_candidate("1", "Fiona - Paizuri")]),
                               moment=MOMENT)
        self.assertEqual(outcome.added, 1)
        self.assertIsNone(outcome.evidence_path)
        self.assertIn("证据未取得", outcome.evidence_error)
        item = store.items()[0]
        self.assertEqual(item.title, "Fiona - Paizuri")
        self.assertIsNone(item.metadata.get("evidence_path"))

    def test_a_readable_evidence_root_reports_no_error(self):
        source_id = self._source()
        outcome = self.store.record(source_id, _fetch([_candidate("1", "A")]),
                                    moment=MOMENT)
        self.assertIsNotNone(outcome.evidence_path)
        self.assertIsNone(outcome.evidence_error)

    def test_approximate_precision_is_carried_through_from_the_connector(self):
        source_id = self._source()
        self.store.record(source_id, _fetch([
            _candidate("1", "A", published_at="2026-08-18T00:00:00Z",
                       extra={"published_precision": "approximate"}),
            _candidate("2", "B"),
        ]), moment=MOMENT)
        precision = {i.external_id: i.published_precision for i in self.store.items()}
        self.assertEqual(precision, {"1": "approximate", "2": "unknown"})

    def test_record_error_keeps_the_message_and_status(self):
        source_id = self._source()
        self.store.record_error(source_id, "HTTP 403", moment=MOMENT,
                                status="unauthorized")
        row = self.store.sources()[0]
        self.assertEqual(row["last_status"], "unauthorized")
        self.assertEqual(row["last_error"], "HTTP 403")

    def test_items_can_be_filtered_by_status(self):
        source_id = self._source()
        self.store.record(source_id, _fetch([_candidate("1", "A"), _candidate("2", "B")]),
                          moment=MOMENT)
        self.store.set_status(self.store.items()[0].id, "seen")
        self.assertEqual(len(self.store.items(statuses=("new",))), 1)

    def test_an_ignored_item_can_be_restored_to_new(self):
        source_id = self._source()
        self.store.record(source_id, _fetch([_candidate("1", "A")]), moment=MOMENT)
        item = self.store.items()[0]
        self.store.set_status(item.id, "ignored")
        self.store.set_status(item.id, "new")
        self.assertEqual(self.store.items()[0].status, "new")

    def test_saved_status_cannot_be_set_directly(self):
        source_id = self._source()
        self.store.record(source_id, _fetch([_candidate("1", "A")]), moment=MOMENT)
        with self.assertRaises(FollowSourceError):
            self.store.set_status(self.store.items()[0].id, "saved")
        with self.assertRaises(FollowSourceError):
            self.store.set_status(self.store.items()[0].id, "nonsense")


class GroupingTests(_StoreCase):
    def test_repeated_collection_of_the_main_upload_preserves_its_variants(self):
        for ref in ("hydrafxx", "zmsfm"):
            source = self._source(ref=ref)
            self.store.record(source, _fetch([
                _candidate("main", "Evening Movie"),
                _candidate("alt", "Evening Movie (4K60fps)"),
            ], ref=ref), moment=MOMENT)
        self.assertEqual(len(self.store.group(self.store.items())), 1)
        for source in self.store.sources():
            self.assertEqual(len(self.store.group(self.store.items(source_id=source["id"]))), 1)

    def test_stored_combined_video_specs_group_without_rewriting_rows(self):
        source = self._source()
        self.store.record(source, _fetch([
            _candidate("one", "Evening Movie (4K60fps)(NO WM)"),
            _candidate("two", "Evening Movie (60fps)(NO WM)"),
        ]), moment=MOMENT)
        self.connection.execute("UPDATE follow_item SET release_key=? WHERE external_id='one'",
                                ("evening movie 4k60fps",))
        self.connection.commit()
        groups = self.store.group(self.store.items())
        self.assertEqual(len(groups), 1)
        self.assertEqual({row.external_id for row in (groups[0].primary, *groups[0].variants)},
                         {"one", "two"})
        stored = self.connection.execute("SELECT release_key FROM follow_item WHERE external_id='one'").fetchone()[0]
        self.assertEqual(stored, "evening movie 4k60fps")

    def _populate(self):
        video = self._source()
        booru = self._source(provider="rule34xxx", ref="lazyprocrastinator")
        self.store.record(video, _fetch([
            _candidate("4542713", "Fiona - Paizuri"),
            _candidate("4542721", "Fiona - Paizuri (Nude)"),
            _candidate("4542705", "Fiona - Missionary"),
        ]), moment=MOMENT)
        self.store.record(booru, _fetch([
            FollowCandidate(provider="rule34xxx", external_id="9988770",
                            title="fiona paizuri",
                            group_hint="rule34xxx:post:9988770"),
            FollowCandidate(provider="rule34xxx", external_id="9988776",
                            title="totally different filename",
                            title_is_name=False,
                            group_hint="rule34xxx:post:9988770"),
        ], provider="rule34xxx"), moment=MOMENT)

    def test_alt_folds_under_its_main_and_other_works_stay_apart(self):
        self._populate()
        groups = {g.primary.title: g for g in self.store.group(self.store.items())}
        self.assertIn("Fiona - Paizuri", groups)
        self.assertIn("Fiona - Missionary", groups)
        paizuri = groups["Fiona - Paizuri"]
        self.assertIn("Fiona - Paizuri (Nude)", [v.title for v in paizuri.variants])
        self.assertEqual(groups["Fiona - Missionary"].variants, ())

    def test_cross_site_duplicate_is_reported_separately_from_same_site_variants(self):
        self._populate()
        paizuri = next(g for g in self.store.group(self.store.items())
                       if g.primary.title == "Fiona - Paizuri")
        self.assertEqual(sorted(paizuri.providers), ["rule34video", "rule34xxx"])
        self.assertTrue(all(d.provider == "rule34xxx" for d in paizuri.duplicates))

    def test_group_hint_beats_an_unrelated_title(self):
        # booru 子帖的「标题」是标签拼的，和父帖毫无关系，只有来源声明的键能连起来。
        self._populate()
        groups = self.store.group(self.store.items())
        stray = [g for g in groups if g.primary.title == "totally different filename"]
        self.assertEqual(stray, [])

    def test_a_shared_origin_key_merges_two_different_sites(self):
        # 这是跨站去重真正靠得住的那条路：rule34.xxx 从 source 归一出的键，
        # 和 kemono 上同一帖子的键完全相同，标题再不一样也能精确合并。
        booru = self._source(provider="rule34xxx", ref="lazyprocrastinator")
        kemono = self._source(provider="kemono", ref="fanbox/30917150")
        self.store.record(booru, _fetch([
            FollowCandidate(provider="rule34xxx", external_id="18534395",
                            title="fiona · blush", title_is_name=False,
                            group_hint="fanbox:12304831"),
        ], provider="rule34xxx", ref="lazyprocrastinator"), moment=MOMENT)
        self.store.record(kemono, _fetch([
            FollowCandidate(provider="kemono", external_id="12304831",
                            title="Fiona - Paizuri", group_hint="fanbox:12304831"),
        ], provider="kemono", ref="fanbox/30917150"), moment=MOMENT)
        groups = self.store.group(self.store.items())
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(groups[0].providers), ["kemono", "rule34xxx"])

    def test_paheal_and_existing_sources_deduplicate_on_the_subscribestar_post(self):
        paheal = self._source(provider="rule34paheal", ref="initiala")
        mirror = self._source(provider="rule34xxx", ref="initiala")
        self.store.record(paheal, _fetch([
            FollowCandidate(provider="rule34paheal", external_id="7428820",
                            title="Amina · Tifa", title_is_name=False,
                            group_hint="subscribestar:2639932"),
        ], provider="rule34paheal", ref="initiala"), moment=MOMENT)
        self.store.record(mirror, _fetch([
            FollowCandidate(provider="rule34xxx", external_id="18540000",
                            title="Different tag label", title_is_name=False,
                            group_hint="subscribestar:2639932"),
        ], provider="rule34xxx", ref="initiala"), moment=MOMENT)
        groups = self.store.group(self.store.items())
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(groups[0].providers), ["rule34paheal", "rule34xxx"])

    def test_a_tag_derived_title_never_merges_two_works_on_its_own(self):
        # 标签拼出来的「标题」不是名字：同一作者标签相似的两条不能因此被并掉。
        source_id = self._source(provider="rule34xxx", ref="lazyprocrastinator")
        self.store.record(source_id, _fetch([
            FollowCandidate(provider="rule34xxx", external_id="1",
                            title="fiona · blush", title_is_name=False),
            FollowCandidate(provider="rule34xxx", external_id="2",
                            title="fiona · blush", title_is_name=False),
        ], provider="rule34xxx", ref="lazyprocrastinator"), moment=MOMENT)
        self.assertEqual(len(self.store.group(self.store.items())), 2)

    def _booru_post(self, external_id, minutes, *, character="angel_(kof)",
                    general=("cowgirl_position", "blush", "nude", "pov"), **extra):
        tag_types = {"king_of_fighters": "copyright", character: "character",
                     "you": "character", "lazyprocrastinator": "artist",
                     **{tag: "general" for tag in general}}
        return FollowCandidate(
            provider="rule34xxx", external_id=external_id,
            title=f"{character} · {general[0]}", title_is_name=False,
            published_at=(MOMENT + timedelta(minutes=minutes)).isoformat(),
            group_hint=f"rule34xxx:post:{external_id}",
            extra={"title_from": "tags", "tag_types": tag_types, **extra})

    def _record_booru(self, *posts):
        source_id = self._source(provider="rule34xxx", ref="lazyprocrastinator")
        self.store.record(source_id, _fetch(posts, provider="rule34xxx"), moment=MOMENT)
        return self.store.group(self.store.items())

    def test_posts_uploaded_in_one_burst_fold_into_one_work(self):
        # 同一段动画按横屏、竖屏和机位拆成几帖连着发：角色相同、标签几乎一样。
        groups = self._record_booru(
            self._booru_post("1", 0), self._booru_post("2", 1),
            self._booru_post("3", 2, general=("cowgirl_position", "blush", "nude", "boobjob")),
            self._booru_post("4", 150))
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].primary.external_id, "1")
        self.assertEqual(len(groups[0].variants), 3)

    def test_a_burst_needs_the_same_characters(self):
        groups = self._record_booru(
            self._booru_post("1", 0), self._booru_post("2", 1, character="lavinia_voda"))
        self.assertEqual(len(groups), 2)

    def test_a_long_pause_starts_a_new_work(self):
        groups = self._record_booru(self._booru_post("1", 0), self._booru_post("2", 60 * 4))
        self.assertEqual(len(groups), 2)

    def test_posts_with_little_tag_overlap_stay_apart(self):
        groups = self._record_booru(
            self._booru_post("1", 0),
            self._booru_post("2", 1, general=("standing_sex", "against_wall", "hat", "nude")))
        self.assertEqual(len(groups), 2)

    def test_a_post_without_an_origin_joins_the_declared_work_it_was_uploaded_with(self):
        # LazyProcrastinator 的 Aerith 一批 6 帖，5 帖写了出处，夹在中间的一帖没写。
        origin = "https://www.fanbox.cc/@lazy/posts/12304831"
        groups = self._record_booru(
            self._booru_post("15101361", 0, source=origin),
            self._booru_post("15101385", 3),
            self._booru_post("15101390", 4, source=origin))
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].variants), 2)

    def test_two_declared_works_are_not_joined_through_a_post_between_them(self):
        # 来源把两帖分成了两个作品，中间那帖挂哪一组都是猜，三帖各自成卡。
        groups = self._record_booru(
            self._booru_post("1", 0, source="https://www.fanbox.cc/@lazy/posts/12304831"),
            self._booru_post("2", 1),
            self._booru_post("3", 2, source="https://www.fanbox.cc/@lazy/posts/12304832"))
        self.assertEqual(len(groups), 3)

    def test_posts_retagged_together_are_not_a_burst(self):
        # Rekin3D 的两帖分别在 05-01 与 05-20 上传，09-14 一起被改了标签，落库时间都是
        # 改标签那一分钟。帖子 id 相差近 20 万，不是连着传的。
        groups = self._record_booru(
            self._booru_post("17361475", 1), self._booru_post("17559518", 0))
        self.assertEqual(len(groups), 2)

    def test_an_untagged_booru_post_joins_its_batch_by_the_whole_tag_set(self):
        # rule34.paheal 不给标签分类。InitialA 一口气传的 Lily 动画里，8 帖写着同一个
        # subscribestar 出处，最后一帖没写；整组标签相同，归进那一组。
        source_id = self._source(provider="rule34paheal", ref="initiala", label="initiala")
        tags = {tag: "general" for tag in
                ("InitialA", "Lily_Artemis_II", "Stellar_Blade", "animated", "blender")}

        def post(external_id, seconds, **extra):
            return FollowCandidate(
                provider="rule34paheal", external_id=external_id,
                title="lily artemis ii · stellar blade", title_is_name=False,
                published_at=(MOMENT + timedelta(seconds=seconds)).isoformat(),
                group_hint="subscribestar:2685233" if "source" in extra
                else f"rule34paheal:post:{external_id}",
                extra={"title_from": "tags", "tag_types": tags, **extra})

        origin = "https://subscribestar.adult/posts/2685233"
        self.store.record(source_id, _fetch([
            post("7452299", 0, source=origin), post("7452300", 1, source=origin),
            post("7452301", 25),
            post("7452302", 30, tag_types={**tags, "Eve": "general"}),
        ], provider="rule34paheal", ref="initiala"), moment=MOMENT)
        groups = sorted(sorted(m.external_id for m in (g.primary, *g.variants, *g.duplicates))
                        for g in self.store.group(self.store.items()))
        self.assertEqual(groups, [["7452299", "7452300", "7452301"], ["7452302"]])

    def test_a_stored_hint_is_recomputed_from_the_recorded_source(self):
        # 落库时缺协议头的出处没认出来，键退回了帖子自己的 id；读时按出处重算后并组。
        stale = self._booru_post("2", 1, character="ahri",
                                 source="x.com/vileclipse/status/2101310844021928089")
        groups = self._record_booru(
            self._booru_post("1", 0, source="https://x.com/vileclipse/status/2101310844021928089"),
            replace(stale, group_hint="rule34xxx:post:2"))
        self.assertEqual(len(groups), 1)

    def _upload(self, external_id, hours, *, duration=60.054, character="grace_ashcroft",
                general=("athletic_female", "blender", "cowgirl_position", "nude"), **extra):
        return replace(self._booru_post(external_id, hours * 60, character=character,
                                        general=general, **extra), duration=duration)

    def _members(self, groups):
        return sorted(sorted(m.external_id for m in (g.primary, *g.variants, *g.duplicates))
                      for g in groups)

    def test_the_same_clip_uploaded_again_days_later_joins_the_first_upload(self):
        # Memz 的 Grace 动画：07-18 一帖带出处，07-23 又传两帖。重新编码过，只有时长逐毫秒
        # 相同；后两帖多挂一个作品标签，一般标签也不完全一样。
        later = {"resident_evil_9:_requiem": "copyright", "grace_ashcroft": "character",
                 "memz": "artist", "athletic_female": "general", "blender": "general",
                 "cowgirl_position": "general", "nude": "general", "3d_animation": "general"}
        groups = self._record_booru(
            self._upload("18148361", 0, source="https://danbooru.donmai.us/posts/11817280"),
            self._upload("18193355", 104, tag_types=later),
            self._upload("18193376", 104, tag_types=later))
        self.assertEqual(self._members(groups), [["18148361", "18193355", "18193376"]])

    def test_an_export_length_shared_by_other_characters_is_not_evidence(self):
        # LazyProcrastinator 的 20.02 秒用在上百种角色组合上：同一角色隔几天的两帖是两部。
        groups = self._record_booru(
            self._upload("1", 0, duration=20.02, character="reika_(doa)"),
            self._upload("2", 24 * 100, duration=20.02, character="reika_(doa)"),
            self._upload("3", 24 * 50, duration=20.02, character="jote_(ffxvi)"))
        self.assertEqual(len(groups), 3)

    def test_a_filtered_page_still_knows_an_export_length(self):
        # 只看未读时，别的角色那几帖可能都不在这一页；认固定长度要按筛选前的全部条目。
        self._record_booru(
            self._upload("1", 0, duration=20.02, character="reika_(doa)"),
            self._upload("2", 24 * 100, duration=20.02, character="reika_(doa)"),
            self._upload("3", 24 * 50, duration=20.02, character="jote_(ffxvi)"))
        everything = self.store.items()
        page = tuple(item for item in everything if item.external_id != "3")
        self.assertEqual(len(self.store.group(page, population=everything)), 2)

    def test_uploads_within_one_session_are_left_to_the_burst_rule(self):
        # 同一小时里时长相同的几帖是同一场景的不同体位，连发判据按相邻帖的标签切开了：
        # 1 与 3 标签相近，中间隔着体位不同的 2，这里不拿时长把 1 和 3 重新串起来。
        handjob = ("beach", "handjob", "duo", "pov")
        groups = self._record_booru(
            self._upload("1", 0, general=handjob),
            self._upload("2", 0.25, general=("beach", "ass_focus", "doggy", "wall")),
            self._upload("3", 0.5, general=(*handjob[:3], "kneeling")))
        self.assertEqual(len(groups), 3)

    def test_reuploads_need_overlapping_tags_and_a_millisecond_length(self):
        groups = self._record_booru(
            self._upload("1", 0), self._upload("2", 48, general=("standing", "hat", "wall", "nude")),
            self._upload("3", 0, duration=45.0, character="nyotengu"),
            self._upload("4", 48, duration=45.0, character="nyotengu"))
        self.assertEqual(len(groups), 4)

    def test_two_declared_releases_are_not_joined_as_a_reupload(self):
        # Pantsushi3D 发过两条推：来源自己说是两次发布，缩略图再像也不并。
        groups = self._record_booru(
            self._upload("4704518", 0, source="https://twitter.com/Pantsushi3D/status/1"),
            self._upload("6178800", 24 * 392, source="https://twitter.com/Pantsushi3D/status/2"))
        self.assertEqual(len(groups), 2)

    def _clip(self, video_id, title, *, duration=20.0, character="angel (kof)",
              work="King of Fighters", **extra_tags):
        tag_types = {"beach": "general", character: "general", work: "copyright",
                     "LazyProcrastinator": "artist", **extra_tags}
        return FollowCandidate(provider="rule34video", external_id=str(video_id),
                               title=title, duration=duration,
                               extra={"tag_types": tag_types})

    def _record_clips(self, *clips):
        source_id = self._source(provider="rule34video", ref="lazyprocrastinator")
        self.store.record(source_id, _fetch(clips), moment=MOMENT)
        return sorted(sorted(m.title for m in (g.primary, *g.variants, *g.duplicates))
                      for g in self.store.group(self.store.items()))

    def test_a_batch_of_clips_folds_per_character(self):
        # 同一段 KOF 动画导出成几条 20 秒短片连着传；Angel 与 Mai 是两个角色，分成两组。
        mai = "mai shiranui (dead or alive)"
        groups = self._record_clips(
            self._clip(4608755, "Angel-riding hugging"), self._clip(4608759, "Angel-missionary"),
            self._clip(4608779, "Angel-handjob"), self._clip(4608787, "Mai-blowjob", character=mai),
            self._clip(4608795, "Mai-doggy", character=mai))
        self.assertEqual(groups, [["Angel-handjob", "Angel-missionary", "Angel-riding hugging"],
                                  ["Mai-blowjob", "Mai-doggy"]])

    def test_a_clip_a_few_dozen_uploads_earlier_joins_its_pack(self):
        # Barney's Mom 一包 8 条 20 秒短片，Riding 比下一条早 64 个 id，中间夹着别人的上传。
        freyja = {"freyja (final fantasy)": "general"}
        groups = self._record_clips(
            self._clip(4608531, "Barney's Mom - Riding", work="Final Fantasy", **freyja),
            self._clip(4608595, "Barney's Mom - Doggy 3", work="Final Fantasy", **freyja),
            self._clip(4608599, "Barney's Mom - Sitting Blowjob", work="Final Fantasy",
                       **freyja))
        self.assertEqual(len(groups), 1)

    def test_the_same_character_uploaded_the_next_day_stays_apart(self):
        # LazyProcrastinator 的 Lavinia 两条隔天上传，id 相差 300，是两部。
        groups = self._record_clips(
            self._clip(4600000, "Lavinia-cowgirl", character="lavinia (zzz)"),
            self._clip(4600300, "Lavinia-doggystyle", character="lavinia (zzz)"))
        self.assertEqual(len(groups), 2)

    def test_clips_far_apart_or_of_another_length_stay_apart(self):
        groups = self._record_clips(
            self._clip(4608755, "Angel-riding hugging"), self._clip(4609755, "Angel-missionary"),
            self._clip(4609759, "Angel-handjob", duration=45.0))
        self.assertEqual(len(groups), 3)

    def test_a_series_title_does_not_fold_different_characters(self):
        # 标题开头是系列名，角色在后半截；带括号的角色标签对不上就拆开。配音演员
        # `Chloeangelva (VA)` 两边都有，但它归 artist，不算角色。
        groups = self._record_clips(
            self._clip(4454645, "Summer Fantasy – Cindy & Lunafreya", duration=97.0,
                       character="cindy aurum (final fantasy)", work="Final Fantasy",
                       **{"Chloeangelva (VA)": "artist"}),
            self._clip(4454649, "Summer Fantasy – Tifa & Aerith", duration=97.0,
                       character="tifa lockhart (final fantasy)", work="Final Fantasy",
                       **{"Chloeangelva (VA)": "artist"}))
        self.assertEqual(len(groups), 2)

    def test_wip_is_surfaced_on_the_group(self):
        source_id = self._source()
        self.store.record(source_id, _fetch([
            _candidate("1", "Mitsuru School Movie"),
            _candidate("2", "Mitsuru School Movie [WIP]"),
        ]), moment=MOMENT)
        group = self.store.group(self.store.items())[0]
        self.assertTrue(group.has_wip)
        self.assertEqual(group.primary.variant_kind, "main")

    def test_versions_posted_together_fold_by_title_family(self):
        # 同一作品按清晰度分帖发：尾巴上的 `Ultra HD` 不在变体词表里，靠「键接一小段
        # 尾巴、自己带变体标记、同期发布」归进同一组。
        source_id = self._source()
        self.store.record(source_id, _fetch([
            _candidate("1", "2B Love at Sunset - 720p", published_at="2026-08-31T23:57:42Z"),
            _candidate("2", "2B Love at Sunset - 1080p", published_at="2026-08-31T23:59:24Z"),
            _candidate("3", "2B Love at Sunset - 4K Ultra HD Unwatermarked",
                       published_at="2026-09-01T00:19:38Z"),
            # 续作编号、隔了几个月的相似标题、尾巴上没有变体标记的，各自成组。
            _candidate("4", "Sayuri - Cowgirl [4K]", published_at="2026-09-01T00:00:00Z"),
            _candidate("5", "Sayuri - Cowgirl Part 2 [4K]", published_at="2026-09-01T00:00:00Z"),
            _candidate("6", "Tifa Beach [4K]", published_at="2026-05-01T00:00:00Z"),
            _candidate("7", "Tifa Beach Night [4K]", published_at="2026-09-01T00:00:00Z"),
            _candidate("8", "Aerith Garden", published_at="2026-09-01T00:00:00Z"),
            _candidate("9", "Aerith Garden Party", published_at="2026-09-01T00:00:00Z"),
        ]), moment=MOMENT)
        groups = self.store.group(self.store.items())
        sunset = [group for group in groups
                  if group.primary.title.startswith("2B Love at Sunset")]
        self.assertEqual(len(sunset), 1)
        self.assertEqual(sorted(item.external_id for item in sunset[0].variants), ["2", "3"])
        self.assertEqual(len(groups), 7)

    def test_the_same_author_on_other_sites_folds_once_names_are_stripped(self):
        # 来源没绑实体时，标题里夹着的作者名入库剥不掉。给了作者映射，分组前剥掉，
        # 相似标题的版本也跨这位作者的来源去对；一个词的标题底下不接别的作品。
        fanbox = self._source(provider="kemono", ref="fanbox/30917150", label="Pantsushi · fanbox")
        video = self._source(ref="pantsushi", label="pantsushi")
        self.store.record(fanbox, _fetch([
            _candidate("f1", "2B Love at Sunset - 1080p", provider="kemono",
                       published_at="2026-08-31T23:59:24Z"),
            _candidate("f2", "Kasumi Training - Alt Black 4K Ultra HD", provider="kemono",
                       published_at="2026-06-24T00:00:00Z"),
            _candidate("f3", "Tifa Lifeguard [4K]", provider="kemono",
                       published_at="2026-06-20T00:00:00Z"),
        ], provider="kemono", ref="fanbox/30917150"), moment=MOMENT)
        self.store.record(video, _fetch([
            _candidate("v1", "2B Love at Sunset [pantsushi] 4K", published_at="2026-09-02T00:00:00Z"),
            _candidate("v2", "Kasumi Training - Alt Black 4K", published_at="2026-06-25T00:00:00Z"),
            _candidate("v3", "Tifa [pantsushi]", published_at="2026-06-23T00:00:00Z"),
            # 剥完等于别的作者的标题，而这位作者自己没有这部：不借剥名去撞别人的键。
            _candidate("v4", "Kyrie Canaan [pantsushi]", published_at="2026-06-01T00:00:00Z"),
        ], ref="pantsushi"), moment=MOMENT)
        other = self._source(ref="billyhhyb", label="billyhhyb")
        self.store.record(other, _fetch([
            _candidate("o1", "Kyrie Canaan", published_at="2026-06-01T00:00:00Z"),
        ], ref="billyhhyb"), moment=MOMENT)
        items = self.store.items()
        pantsushi = ("name:pantsushi", frozenset({"pantsushi"}))
        authors = {fanbox: pantsushi, video: pantsushi,
                   other: ("name:billyhhyb", frozenset({"billyhhyb"}))}
        folded = {frozenset(item.external_id for item in (g.primary, *g.variants, *g.duplicates))
                  for g in self.store.group(items, authors)}
        self.assertEqual(folded, {frozenset({"f1", "v1"}), frozenset({"f2", "v2"}),
                                  frozenset({"f3"}), frozenset({"v3"}),
                                  frozenset({"v4"}), frozenset({"o1"})})
        self.assertEqual(len(self.store.group(items)), 8)

    def test_groups_are_ordered_newest_first(self):
        source_id = self._source()
        self.store.record(source_id, _fetch([
            _candidate("1", "Older", published_at="2026-08-01T00:00:00Z"),
            _candidate("2", "Newer", published_at="2026-08-20T00:00:00Z"),
        ]), moment=MOMENT)
        self.assertEqual([g.primary.title for g in self.store.group(self.store.items())],
                         ["Newer", "Older"])

    def test_empty_input_groups_to_nothing(self):
        self.assertEqual(self.store.group(()), ())

    def test_same_provider_title_collision_is_not_merged_under_work_semantics(self):
        # 实测踩到的：kemono 上「February Poll Animations」和「February Poll + Animations」
        # 归一化后完全相同，却是两个帖子。同站两个 main 撞车只说明标题判据到头了。
        source_id = self._source(provider="kemono", ref="fanbox/30917150")
        self.store.record(source_id, _fetch([
            FollowCandidate(provider="kemono", external_id="10",
                            title="February Poll + Animations",
                            published_at="2026-02-15T21:27:50Z"),
            FollowCandidate(provider="kemono", external_id="19",
                            title="February Poll Animations",
                            published_at="2026-01-31T22:10:13Z"),
        ], provider="kemono", ref="fanbox/30917150"), moment=MOMENT)
        groups = self.store.group(self.store.items())
        self.assertEqual(len(groups), 2)
        self.assertEqual([g.variants for g in groups], [(), ()])

    def test_an_alt_still_folds_when_its_provider_has_only_one_main(self):
        source_id = self._source()
        self.store.record(source_id, _fetch([
            _candidate("1", "Fiona - Paizuri"),
            _candidate("2", "Fiona - Paizuri (Nude)"),
        ]), moment=MOMENT)
        groups = self.store.group(self.store.items())
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].variants), 1)

    def test_f95_resource_replies_are_independent_release_groups(self):
        source_id = self._source(provider="f95zone", ref="50685", semantics="release")
        self.store.record(source_id, _fetch([
            FollowCandidate(provider="f95zone", external_id="21383374",
                            title="Lazy Procrastinator Collection [2026-06-28]",
                            published_at="2026-08-21T04:14:09Z"),
            FollowCandidate(provider="f95zone", external_id="21394555",
                            title="Lazy Procrastinator Collection [2026-06-28]",
                            published_at="2026-08-22T18:09:23Z"),
        ], provider="f95zone", ref="50685", semantics="release"), moment=MOMENT)
        groups = self.store.group(self.store.items())
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(group.is_release for group in groups))
        self.assertTrue(all(group.variants == () for group in groups))
        # 最新楼层排在前面，但不会把较早的资源楼层吞成「动态」。
        self.assertEqual(groups[0].primary.external_id, "21394555")
        # 标题里的日期是批次标签，不是版本：`[2026-06-28]` 不进版本位。
        self.assertIsNone(groups[0].primary.version)


class SaveAssetTests(_StoreCase):
    def _one_item(self, **candidate_kwargs):
        entity_id = self._entity()
        source_id = self._source(entity_id=entity_id)
        candidate_kwargs.setdefault("url", "https://rule34video.com/video/4542713/x/")
        candidate_kwargs.setdefault("duration", 20.0)
        self.store.record(
            source_id,
            _fetch([_candidate("4542713", "Fiona - Paizuri", **candidate_kwargs)]),
            moment=MOMENT)
        return entity_id, self.store.items()[0]

    def test_saving_requires_explicit_confirmation(self):
        _, item = self._one_item()
        with self.assertRaises(FollowSourceError):
            self.store.save_asset(item.id)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM asset").fetchone()[0], 0)

    def test_confirmed_save_creates_one_online_asset_and_links_the_creator(self):
        entity_id, item = self._one_item(published_at="2026-08-18T06:23:33Z")
        asset_id = self.store.save_asset(item.id, confirm=True, moment=MOMENT)
        row = self.connection.execute(
            "SELECT location,path,name,medium,creator,duration,release_date FROM asset"
            " WHERE id=?", (asset_id,)).fetchone()
        self.assertEqual(row["location"], "online")
        self.assertEqual(row["path"], "https://rule34video.com/video/4542713/x/")
        self.assertEqual(row["medium"], "video")
        self.assertEqual(row["creator"], "LazyProcrast")
        self.assertEqual(row["release_date"], "2026-08-18")
        relation = self.connection.execute(
            "SELECT role,source FROM asset_entity WHERE asset_id=? AND entity_id=?",
            (asset_id, entity_id)).fetchone()
        self.assertEqual((relation["role"], relation["source"]),
                         ("creator", "follow:rule34video"))
        self.assertEqual(self.store.items()[0].status, "saved")

    def test_saving_twice_is_idempotent(self):
        _, item = self._one_item()
        first = self.store.save_asset(item.id, confirm=True, moment=MOMENT)
        second = self.store.save_asset(item.id, confirm=True, moment=MOMENT)
        self.assertEqual(first, second)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM asset").fetchone()[0], 1)

    def test_an_existing_online_asset_is_reused_not_duplicated(self):
        _, item = self._one_item()
        self.connection.execute(
            "INSERT INTO asset(location,path,name) VALUES('online',?,'existing')",
            (item.url,))
        asset_id = self.store.save_asset(item.id, confirm=True, moment=MOMENT)
        self.assertEqual(self.connection.execute(
            "SELECT name FROM asset WHERE id=?", (asset_id,)).fetchone()[0], "existing")

    def test_kemono_posts_without_duration_are_saved_as_illustrations(self):
        source_id = self._source(provider="kemono", ref="fanbox/30917150")
        self.store.record(source_id, _fetch(
            [FollowCandidate(provider="kemono", external_id="11406814", title="VVD 7",
                             url="https://kemono.cr/fanbox/user/30917150/post/11406814")],
            provider="kemono", ref="fanbox/30917150"), moment=MOMENT)
        asset_id = self.store.save_asset(self.store.items()[0].id, confirm=True,
                                         moment=MOMENT)
        self.assertEqual(self.connection.execute(
            "SELECT medium FROM asset WHERE id=?", (asset_id,)).fetchone()[0],
            "illustration")

    def test_a_candidate_without_a_page_url_cannot_be_saved(self):
        source_id = self._source()
        self.store.record(source_id, _fetch([_candidate("1", "No URL")]), moment=MOMENT)
        with self.assertRaises(FollowSourceError):
            self.store.save_asset(self.store.items()[0].id, confirm=True)

    def test_missing_item_is_an_error(self):
        with self.assertRaises(FollowSourceError):
            self.store.save_asset(9999, confirm=True)


class MediaReparseTests(_StoreCase):
    """`source_needs_media_reparse` 的判定口径。

    这个判定交给 SQLite 的 `json_extract`，不把整个来源的 `metadata_json` 取回
    Python 再逐条 `json.loads`：回填过的来源单源上千行，为一个布尔值全解一遍。
    下推到 SQL 要守住同样的容忍度。
    """

    def _write_metadata(self, source_id: int, external_id: str, raw) -> None:
        self.connection.execute(
            "INSERT INTO follow_item(source_id,external_id,release_key,title,url,"
            "metadata_json,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?)",
            (source_id, external_id, external_id, external_id,
             f"https://x.test/{external_id}",
             raw, "2026-08-25T00:00:00Z", "2026-08-25T00:00:00Z"))

    def test_no_item_needs_a_reparse_on_an_empty_source(self):
        self.assertFalse(self.store.source_needs_media_reparse(self._source()))

    def test_one_flagged_item_is_enough(self):
        source_id = self._source()
        self._write_metadata(source_id, "a", json.dumps({"markers": []}))
        self._write_metadata(source_id, "b",
                             json.dumps({"media_needs_credential": True}))
        self.assertTrue(self.store.source_needs_media_reparse(source_id))

    def test_an_explicit_false_does_not_count(self):
        source_id = self._source()
        self._write_metadata(source_id, "a",
                             json.dumps({"media_needs_credential": False}))
        self.assertFalse(self.store.source_needs_media_reparse(source_id))

    def test_unparsable_or_odd_shaped_metadata_is_skipped_not_fatal(self):
        """非法 JSON 只是「这条不知道」，不该让整个检查崩掉。

        `json_extract` 遇到非法 JSON 是报错而不是返回 NULL，所以查询里那层
        `json_valid` 不能省：它顶的是 `except JSONDecodeError: continue` 那一级容忍。
        列上是 `NOT NULL DEFAULT '{}'`，所以「没有元数据」落盘的形状是 `{}` 和
        空字串，不是 NULL；空字串并不是合法 JSON，同样得被 `json_valid` 拦住。
        """
        source_id = self._source()
        self._write_metadata(source_id, "a", "not json at all")
        self._write_metadata(source_id, "b", "")
        self._write_metadata(source_id, "c", "[1,2,3]")
        self._write_metadata(source_id, "d", "{}")
        self.assertFalse(self.store.source_needs_media_reparse(source_id))
        self._write_metadata(source_id, "e",
                             json.dumps({"media_needs_credential": True}))
        self.assertTrue(self.store.source_needs_media_reparse(source_id))

    def test_the_flag_does_not_leak_across_sources(self):
        first = self._source()
        second = self._source(ref="someone-else")
        self._write_metadata(second, "a",
                             json.dumps({"media_needs_credential": True}))
        self.assertFalse(self.store.source_needs_media_reparse(first))
        self.assertTrue(self.store.source_needs_media_reparse(second))


class ReleaseGroupTests(unittest.TestCase):
    def test_group_is_immutable(self):
        group = ReleaseGroup("k", None, (), ())
        with self.assertRaises(Exception):
            group.release_key = "other"


class AuthorIdentityTests(_StoreCase):
    """作者别名表的写入口径。这些 SQL 归存储层，不写在 Web 处理函数里。"""

    def test_the_container_suffix_is_not_part_of_the_author_name(self):
        """F95 的线程标题说的是一个容器，不是另一个作者。

        真实数据是 `Lazy Procrastinator Collection`，而每一条作者来源都是
        `LazyProcrastinator`；留着这个通用后缀会凭空多出一个分组。
        """
        self.assertEqual(
            normalized_author_name("Lazy Procrastinator Collection",
                                   provider="f95zone"),
            normalized_author_name("LazyProcrastinator"))
        # 只对 F95 成立：别的站上 `Collection` 可能真是名字的一部分。
        self.assertNotEqual(
            normalized_author_name("Lazy Procrastinator Collection"),
            normalized_author_name("LazyProcrastinator"))

    def test_the_service_suffix_only_says_where_they_publish(self):
        self.assertEqual(normalized_author_name("LazyProcrastinator · fanbox"),
                         normalized_author_name("lazyprocrastinator"))
        self.assertEqual(author_display_text("Billyhhyb · patreon"), "Billyhhyb")

    def test_the_f95_author_sits_in_the_last_bracket(self):
        """站点的标题约定是 `作品名 [版本或日期] [作者]`。"""
        self.assertEqual(
            author_display_text("Strauzek Collection [2026-09-04] [Mr_Strauz]",
                                provider="f95zone"), "Mr_Strauz")
        self.assertEqual(
            author_display_text("Some Game [v1.2] [Final] [DevName]",
                                provider="f95zone"), "DevName")

    def test_a_title_without_an_author_bracket_falls_back_to_its_stem(self):
        # `[Collection Request]` 是版块标签，`Complete Collection` 是容器措辞，
        # 剥掉之后剩下的才是这个人。
        self.assertEqual(
            author_display_text(
                "[Collection Request] Suzutaru 3D - Complete Collection",
                provider="f95zone"), "Suzutaru 3D")
        self.assertEqual(
            author_display_text("Memz3D Models Collection", provider="f95zone"),
            "Memz3D")

    def test_both_handles_in_the_author_bracket_are_kept(self):
        # 一个人常在作者位上写两个手柄，第一个当显示名，另一个够格做别名候选。
        self.assertEqual(
            f95_author_handles(
                "Lazy Procrastinator Collection [2026-06-28]"
                " [LazyProcrastinator/LazyProcrast]"),
            ("LazyProcrastinator", "LazyProcrast"))
        self.assertEqual(
            f95_author_name(
                "Lazy Procrastinator Collection [2026-06-28]"
                " [LazyProcrastinator/LazyProcrast]"), "LazyProcrastinator")

    def test_the_bracket_rule_is_only_for_f95(self):
        # 别的站上方括号可能真是标签的一部分，不能拿 F95 的约定去套。
        self.assertEqual(
            author_display_text("Strauzek Collection [2026-09-04] [Mr_Strauz]"),
            "Strauzek Collection [2026-09-04] [Mr_Strauz]")

    def test_the_f95_author_key_matches_the_name_on_the_other_sites(self):
        self.assertEqual(
            normalized_author_name("Strauzek Collection [2026-09-04] [Mr_Strauz]",
                                   provider="f95zone"),
            normalized_author_name("mr_strauz"))

    def test_a_manual_alias_maps_both_names_to_one_canonical_key(self):
        self.store.upsert_author_alias("Initiala", "ffxivinitiala", source="manual",
                                       moment=MOMENT)
        mapping, groups = self.store.author_aliases()
        self.assertEqual(mapping["ffxivinitiala"], "initiala")
        self.assertEqual(mapping["initiala"], "initiala")
        self.assertEqual([group["canonical_name"] for group in groups], ["Initiala"])
        self.assertEqual([alias["name"] for alias in groups[0]["aliases"]],
                         ["ffxivinitiala"])

    def test_automatic_evidence_never_overwrites_a_decision(self):
        """人工确认可以有意重新分组；自动证据只填此前未知的手柄。"""
        self.store.upsert_author_alias("Initiala", "ffxivinitiala", source="manual",
                                       moment=MOMENT)
        self.assertIsNone(self.store.upsert_author_alias(
            "Someone Else", "ffxivinitiala", source="official:fanbox", moment=MOMENT))
        mapping, _groups = self.store.author_aliases()
        self.assertEqual(mapping["ffxivinitiala"], "initiala")

    def test_two_names_that_normalize_the_same_are_not_an_alias(self):
        for canonical, alias in (("Initiala", "initi-ala"), ("", "x"), ("x", "")):
            with self.assertRaises(ValueError):
                self.store.upsert_author_alias(canonical, alias, source="manual")

    def test_the_canonical_name_cannot_be_removed_as_an_alias(self):
        """删规范名会拆散整个组，剩下的别名指向一个不存在的键。"""
        self.store.upsert_author_alias("Initiala", "ffxivinitiala", source="manual",
                                       moment=MOMENT)
        with self.assertRaises(ValueError):
            self.store.remove_author_alias("Initiala")
        with self.assertRaises(ValueError):
            self.store.remove_author_alias("never-registered")
        with self.assertRaises(ValueError):
            self.store.remove_author_alias("")
        self.store.remove_author_alias("ffxivinitiala")
        self.assertEqual(self.store.author_aliases(), ({"initiala": "initiala"}, []))

    def test_an_official_handle_is_learned_only_from_one_unambiguous_author(self):
        learned = self.store.learn_official_author_alias(
            "fanbox", "ffxivinitiala",
            (_candidate("1", "a", provider="fanbox", author="Initiala"),))
        self.assertEqual(learned["alias"], "ffxivinitiala")
        self.assertEqual(learned["source"], "official:fanbox")
        # 归档站的 ref 是数字 id，不是名字。
        self.assertIsNone(self.store.learn_official_author_alias(
            "kemono", "fanbox/30917150",
            (_candidate("1", "a", provider="kemono", author="Initiala"),)))


class SourceRemovalTests(_StoreCase):
    def test_removing_a_source_takes_it_out_of_the_listing(self):
        source_id = self._source()
        other = self._source(provider="rule34xxx", ref="tag")
        self.store.remove_source(source_id)
        self.assertEqual([row["id"] for row in self.store.sources()], [other])

    def test_removing_a_source_takes_its_items_and_their_playback(self):
        source_id = self._source()
        self.store.record(source_id, _fetch([_candidate("1", "Fiona")]), moment=MOMENT)
        self.store.record_playback(self.store.items()[0].id, MOMENT)
        self.store.remove_source(source_id)
        for table in ("follow_item", "follow_playback"):
            self.assertEqual(self.connection.execute(
                f"SELECT count(*) FROM {table}").fetchone()[0], 0, table)
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])


class PlaybackTests(_StoreCase):
    def _item(self):
        source_id = self._source()
        self.store.record(source_id, _fetch([_candidate("1", "Fiona")]),
                          moment=MOMENT)
        return self.store.items()[0].id

    def test_playing_a_new_item_marks_it_seen_once(self):
        item_id = self._item()
        self.assertEqual(self.store.record_playback(item_id, MOMENT), "seen")
        # 已经播过的不再改状态：手动标成 ignored 之后再播不该被拉回 seen。
        self.store.set_status(item_id, "ignored")
        self.assertEqual(self.store.record_playback(item_id, MOMENT), "ignored")
        count = self.connection.execute(
            "SELECT play_count FROM follow_playback WHERE follow_item_id=?",
            (item_id,)).fetchone()[0]
        self.assertEqual(count, 2)

    def test_activity_accumulates_time_and_keeps_the_furthest_point(self):
        item_id = self._item()
        first = self.store.record_playback_activity(
            item_id, position=30, duration=100, delta=30, moment=MOMENT)
        self.assertEqual(first, {"play_seconds": 30.0, "max_reached": 0.3})
        # 往回拖再看一遍：时长累加，最远位置不倒退。
        second = self.store.record_playback_activity(
            item_id, position=10, duration=100, delta=10, moment=MOMENT)
        self.assertEqual(second, {"play_seconds": 40.0, "max_reached": 0.3})
        ended = self.store.record_playback_activity(
            item_id, position=99, duration=100, delta=1, ended=True, moment=MOMENT)
        self.assertEqual(ended["max_reached"], 1.0)

    def test_an_unknown_item_is_a_request_error_not_a_storage_failure(self):
        """调用方把 ValueError 映射成 400；这里不能变成 500。"""
        for call in (lambda: self.store.record_playback(9999),
                     lambda: self.store.record_playback_activity(9999)):
            with self.assertRaises(ValueError):
                call()

    def test_nonsense_numbers_do_not_reach_the_database(self):
        item_id = self._item()
        result = self.store.record_playback_activity(
            item_id, position=-5, duration=0, delta=-3, moment=MOMENT)
        self.assertEqual(result, {"play_seconds": 0.0, "max_reached": 0.0})
class PartialCandidateTests(_StoreCase):
    """`partial=True` 的候选（跳过了第二阶段）落库时不能抹掉已有的细节。

    upsert 不整块替换 metadata、media_url、thumb_url 和 group_hint：第二阶段一旦
    按「已补齐」跳过，同一条再落一次就会把上一轮取到的细节清成空——而清空之后
    「详情本来就没有」和「这次没去问」在数据里长得一模一样。
    """

    def _both(self):
        source_id = self._source(provider="rule34paheal", ref="initiala")
        full = FollowCandidate(
            provider="rule34paheal", external_id="7428820", title="Tifa",
            url="https://rule34.paheal.net/post/view/7428820",
            media_url="https://r34i.paheal-cdn.net/df/fb/video",
            thumb_url="https://r34t.paheal.net/df/fb/poster",
            published_at="2026-08-26T15:21:00Z", duration=28.4, author="VHSephi",
            group_hint="subscribestar:2639932",
            extra={"tag": "initiala", "source": "https://subscribestar.adult/posts/2639932",
                   "tag_types": {"tifa_lockhart": "general"}})
        partial = FollowCandidate(
            provider="rule34paheal", external_id="7428820", title="Tifa Lockhart",
            url="https://rule34.paheal.net/post/view/7428820",
            media_url="https://r34i.paheal-cdn.net/df/fb/listing",
            thumb_url="https://r34t.paheal.net/df/fb/thumb",
            group_hint="rule34paheal:post:7428820", partial=True,
            extra={"tag": "initiala", "media_kind": "video",
                   "tag_types": {"amina": "general"}})
        return source_id, full, partial

    def _item(self, source_id):
        return self.store.items(source_id=source_id)[0]

    def _record(self, source_id, candidate):
        self.store.record(source_id, _fetch([candidate], provider="rule34paheal",
                                            ref="initiala"), moment=MOMENT)

    def test_a_partial_row_keeps_every_detail_only_column(self):
        source_id, full, partial = self._both()
        self._record(source_id, full)
        self._record(source_id, partial)
        row = self._item(source_id)
        self.assertEqual(row.published_at, "2026-08-26T15:21:00Z")
        self.assertEqual(row.duration, 28.4)
        self.assertEqual(row.media_url, "https://r34i.paheal-cdn.net/df/fb/video")
        self.assertEqual(row.thumb_url, "https://r34t.paheal.net/df/fb/poster")
        self.assertEqual(row.group_hint, "subscribestar:2639932")
        # 列表本来就权威的那几列照常更新。
        self.assertEqual(row.title, "Tifa Lockhart")

    def test_a_partial_row_merges_metadata_instead_of_replacing_it(self):
        source_id, full, partial = self._both()
        self._record(source_id, full)
        self._record(source_id, partial)
        metadata = self._item(source_id).metadata
        self.assertEqual(metadata["source"], "https://subscribestar.adult/posts/2639932",
                         "详情给出的出处不在这次的补丁里，就该原样留着")
        self.assertEqual(metadata["media_kind"], "video")
        self.assertEqual(metadata["tag_types"],
                         {"tifa_lockhart": "general", "amina": "general"})

    def test_a_complete_row_still_replaces_what_it_carries(self):
        """第二阶段成功时是完整视图，该覆盖就覆盖——否则改正过的细节写不进去。"""
        source_id, full, partial = self._both()
        self._record(source_id, partial)
        self._record(source_id, full)
        row = self._item(source_id)
        self.assertEqual(row.thumb_url, "https://r34t.paheal.net/df/fb/poster")
        self.assertEqual(row.group_hint, "subscribestar:2639932")
        self.assertEqual(row.metadata["tag_types"], {"tifa_lockhart": "general"})


class ImageDimsTests(_StoreCase):
    """图片宽高是给图片墙占位用的比例，三处来源（接口、文件头、界面回写）同权。"""

    def _record(self, source_id, candidate):
        self.store.record(source_id, _fetch([candidate], provider="kemono", ref="a"),
                          moment=MOMENT)
        return self.store.items(source_id=source_id)[0]

    def _image(self, **extra):
        return FollowCandidate(
            provider="kemono", external_id="1", title="Sketch",
            url="https://kemono.cr/fanbox/user/1/post/1",
            media_url="https://kemono.cr/data/ab/cd/abcd.jpg",
            thumb_url="https://kemono.cr/thumbnail/data/ab/cd/abcd.jpg", extra=extra)

    def test_item_dims_fill_only_a_gap_and_say_whether_they_wrote(self):
        source_id = self._source(provider="kemono", ref="a")
        item = self._record(source_id, self._image())
        self.assertTrue(self.store.set_image_dims(item.id, 800, 600))
        self.assertFalse(self.store.set_image_dims(item.id, 1024, 768),
                         "已有尺寸不覆盖：三处给的都是同一张图的比例，没有谁更权威")
        metadata = self.store.item(item.id).metadata
        self.assertEqual((metadata["width"], metadata["height"]), (800, 600))
        self.assertFalse(self.store.set_image_dims(item.id + 99, 800, 600))
        with self.assertRaises(FollowSourceError):
            self.store.set_image_dims(item.id, 0, 600)

    def test_media_dims_land_on_that_one_media_only(self):
        source_id = self._source(provider="kemono", ref="a")
        item = self._record(source_id, self._image(media_items=[
            {"id": "a", "media_kind": "image", "url": "https://kemono.cr/data/a.png"},
            {"id": "b", "media_kind": "image", "url": "https://kemono.cr/data/b.png"},
        ]))
        self.assertTrue(self.store.set_image_dims(item.id, 1920, 1080, media_index=1))
        self.assertFalse(self.store.set_image_dims(item.id, 1, 1, media_index=5))
        media_items = self.store.item(item.id).metadata["media_items"]
        self.assertNotIn("width", media_items[0])
        self.assertEqual((media_items[1]["width"], media_items[1]["height"]), (1920, 1080))
        self.assertNotIn("width", self.store.item(item.id).metadata,
                         "媒体级尺寸不该顺手写成条目级")

    def test_partial_item_and_media_dimensions_accept_a_complete_pair(self):
        source_id = self._source(provider="kemono", ref="a")
        item = self._record(source_id, self._image(width=800, media_items=[
            {"id": "a", "media_kind": "image", "url": "https://kemono.cr/data/a.png", "width": 800},
            {"id": "b", "media_kind": "image", "url": "https://kemono.cr/data/b.png", "height": 600},
        ]))
        self.assertTrue(self.store.set_image_dims(item.id, 800, 600))
        for index in (0, 1):
            self.assertTrue(self.store.set_image_dims(item.id, 800, 600, media_index=index))
            self.assertFalse(self.store.set_image_dims(item.id, 1, 1, media_index=index))
        metadata = self.store.item(item.id).metadata
        self.assertEqual((metadata["width"], metadata["height"]), (800, 600))
        self.assertEqual([(media["width"], media["height"]) for media in metadata["media_items"]],
                         [(800, 600), (800, 600)])

    def test_a_full_refetch_keeps_learned_dims_until_the_source_reports_its_own(self):
        source_id = self._source(provider="kemono", ref="a")
        item = self._record(source_id, self._image(tags="a"))
        self.store.set_image_dims(item.id, 800, 600)
        # 下一轮检查更新是完整候选，走整块替换 metadata 的那条 SET。
        metadata = self._record(source_id, self._image(tags="a b")).metadata
        self.assertEqual(metadata["tags"], "a b")
        self.assertEqual((metadata["width"], metadata["height"]), (800, 600))
        # 来源自己开始报尺寸时以来源为准。
        metadata = self._record(source_id, self._image(width=1600, height=1200)).metadata
        self.assertEqual((metadata["width"], metadata["height"]), (1600, 1200))

    def test_a_full_refetch_keeps_learned_media_dims_by_media_identity(self):
        """清单里每张图学到的尺寸按媒体稳定键对回：作者在前面插了一张，尺寸不串位。"""
        source_id = self._source(provider="kemono", ref="a")
        item = self._record(source_id, self._image(media_items=[
            {"id": "a", "media_kind": "image", "url": "https://kemono.cr/data/a.png"},
            {"id": "b", "media_kind": "image", "url": "https://kemono.cr/data/b.png"},
        ]))
        self.store.set_image_dims(item.id, 1920, 1080, media_index=1)
        media_items = self._record(source_id, self._image(media_items=[
            {"id": "new", "media_kind": "image", "url": "https://kemono.cr/data/new.png"},
            {"id": "a", "media_kind": "image", "url": "https://kemono.cr/data/a.png"},
            {"id": "b", "media_kind": "image", "url": "https://kemono.cr/data/b.png",
             "width": 640, "height": 480},
            {"id": "c", "media_kind": "image", "url": "https://kemono.cr/data/c.png"},
        ])).metadata["media_items"]
        self.assertNotIn("width", media_items[0])
        self.assertNotIn("width", media_items[1])
        self.assertEqual((media_items[2]["width"], media_items[2]["height"]), (640, 480),
                         "来源自己报了尺寸时以来源为准")
        self.assertNotIn("width", media_items[3])
        media_items = self._record(source_id, self._image(media_items=[
            {"id": "b", "media_kind": "image", "url": "https://kemono.cr/data/b.png"},
        ])).metadata["media_items"]
        self.assertEqual((media_items[0]["width"], media_items[0]["height"]), (640, 480))

    def test_a_refetched_media_tuple_keeps_learned_dims(self):
        """f95 的清单是附件与 gofile 两段拼成的 tuple，学到的尺寸照样对回。"""
        source_id = self._source(provider="kemono", ref="a")
        attachment = {"id": "f95-attachment-1", "media_kind": "image",
                      "url": "https://attachments.f95zone.to/2026/10/1_a.png"}
        item = self._record(source_id, self._image(media_items=[attachment]))
        self.store.set_image_dims(item.id, 2000, 1125, media_index=0)
        media_items = self._record(source_id, self._image(media_items=(attachment,))).metadata["media_items"]
        self.assertEqual((media_items[0]["width"], media_items[0]["height"]), (2000, 1125))


class EnrichedMarkTests(_StoreCase):
    """「这一行不必再打详情页」怎么从 ledger 里读出来。"""

    def test_the_paheal_mark_is_a_published_time_the_detail_page_gave(self):
        source_id = self._source(provider="rule34paheal", ref="initiala")
        self.store.record(source_id, _fetch([
            FollowCandidate(provider="rule34paheal", external_id="1", title="a",
                            published_at="2026-08-26T15:21:00Z"),
            FollowCandidate(provider="rule34paheal", external_id="2", title="b",
                            partial=True),
        ], provider="rule34paheal", ref="initiala"), moment=MOMENT)
        self.assertEqual(self.store.enriched_external_ids(source_id, "published_at"),
                         frozenset({"1"}))

    def test_the_rule34xxx_mark_is_the_taxonomy_only_the_detail_page_gives(self):
        source_id = self._source(provider="rule34xxx", ref="tag")
        self.store.record(source_id, _fetch([
            FollowCandidate(provider="rule34xxx", external_id="1", title="a",
                            published_at="2026-08-26T15:21:00Z",
                            extra={"tag_types": {"tifa": "general"}}),
            # 上传时间来自列表，详情页这次被挡回来了：这一条还没补齐。
            FollowCandidate(provider="rule34xxx", external_id="2", title="b",
                            published_at="2026-08-26T15:20:00Z", partial=True,
                            extra={"tag": "tag"}),
        ], provider="rule34xxx", ref="tag"), moment=MOMENT)
        self.assertEqual(self.store.enriched_external_ids(source_id, "tag_types_duration"),
                         frozenset({"1"}))

    def test_a_rule34xxx_video_without_duration_is_not_yet_enriched(self):
        """分类有了、mp4 的时长没读到，下次检查还要进第二阶段；图片不需要时长。"""
        source_id = self._source(provider="rule34xxx", ref="tag")
        types = {"tag_types": {"tifa": "general"}}
        self.store.record(source_id, _fetch([
            FollowCandidate(provider="rule34xxx", external_id="clip", title="a",
                            media_url="https://api-cdn-mp4.rule34.xxx/images/1/a.mp4",
                            extra=types),
            FollowCandidate(provider="rule34xxx", external_id="timed", title="b",
                            media_url="https://api-cdn-mp4.rule34.xxx/images/1/b.mp4",
                            duration=12.5, extra=types),
            FollowCandidate(provider="rule34xxx", external_id="still", title="c",
                            media_url="https://api-cdn.rule34.xxx/images/1/c.jpeg",
                            extra=types),
        ], provider="rule34xxx", ref="tag"), moment=MOMENT)
        self.assertEqual(self.store.enriched_external_ids(source_id, "tag_types_duration"),
                         frozenset({"timed", "still"}))

    def test_a_partial_candidate_fills_an_empty_duration_without_overwriting(self):
        """帖子页被挡回来时候选仍是 partial，文件头读到的时长只补空着的那一格。"""
        source_id = self._source(provider="rule34xxx", ref="tag")
        self.store.record(source_id, _fetch([
            FollowCandidate(provider="rule34xxx", external_id="empty", title="a"),
            FollowCandidate(provider="rule34xxx", external_id="known", title="b",
                            duration=30.0),
        ], provider="rule34xxx", ref="tag"), moment=MOMENT)
        self.store.record(source_id, _fetch([
            FollowCandidate(provider="rule34xxx", external_id="empty", title="a",
                            partial=True, duration=10.0),
            FollowCandidate(provider="rule34xxx", external_id="known", title="b",
                            partial=True, duration=99.0),
        ], provider="rule34xxx", ref="tag"), moment=MOMENT)
        durations = dict(self.connection.execute(
            "SELECT external_id, duration FROM follow_item WHERE source_id=?", (source_id,)))
        self.assertEqual(durations, {"empty": 10.0, "known": 30.0})

    def test_a_rule34video_row_stays_detailed_when_the_list_sees_it_again(self):
        """补齐过的 rule34video 行再被列表看到时不打详情页，详情给的值都要留住。"""
        source_id = self._source(provider="rule34video", ref="artist")
        self.store.record(source_id, _fetch([
            FollowCandidate(provider="rule34video", external_id="1", title="a",
                            media_url="https://rule34video.com/get_file/1_720p.mp4/",
                            published_at="2026-08-18T00:00:00Z",
                            extra={"media_kind": "video", "published_precision": "exact",
                                   "tag_types": {"3D": "metadata"}}),
            FollowCandidate(provider="rule34video", external_id="2", title="b",
                            media_url="https://rule34video.com/get_file/2_preview.mp4/",
                            partial=True, extra={"media_kind": "preview_clip"}),
        ], provider="rule34video", ref="artist"), moment=MOMENT)
        self.assertEqual(self.store.enriched_external_ids(source_id, "tag_types"),
                         frozenset({"1"}))
        self.store.record(source_id, _fetch([
            FollowCandidate(provider="rule34video", external_id="1", title="a",
                            media_url="https://rule34video.com/get_file/1_preview.mp4/",
                            published_at="2026-08-10T00:00:00Z", partial=True,
                            extra={"added_text": "2 months ago"}),
        ], provider="rule34video", ref="artist"), moment=MOMENT + timedelta(minutes=1))
        media_url, published_at, metadata = self.connection.execute(
            "SELECT media_url, published_at, metadata_json FROM follow_item"
            " WHERE source_id=? AND external_id='1'", (source_id,)).fetchone()
        self.assertEqual(media_url, "https://rule34video.com/get_file/1_720p.mp4/")
        self.assertEqual(published_at, "2026-08-18T00:00:00Z")
        self.assertEqual(json.loads(metadata)["media_kind"], "video")
        self.assertEqual(self.store.enriched_external_ids(source_id, "tag_types"),
                         frozenset({"1"}))

    def test_every_kemono_row_in_the_ledger_counts_as_judged(self):
        source_id = self._source(provider="kemono", ref="fanbox/1")
        self.store.record(source_id, _fetch([
            FollowCandidate(provider="kemono", external_id="10", title="a"),
            FollowCandidate(provider="kemono", external_id="11", title="b", partial=True),
        ], provider="kemono", ref="fanbox/1"), moment=MOMENT)
        self.assertEqual(self.store.enriched_external_ids(source_id, "kept"),
                         frozenset({"10", "11"}))

    def test_an_unregistered_mark_is_refused_not_silently_matched(self):
        source_id = self._source()
        with self.assertRaises(ValueError):
            self.store.enriched_external_ids(source_id, "whatever")


class StoredCompilationTests(_StoreCase):
    """判据收紧之前入库的跨作者打包，按同一判据清退。"""

    def _record(self, external_id, title, models):
        source_id = self._source()
        self.store.record(source_id, _fetch([
            _candidate(external_id, title, extra={"models": list(models),
                                                  "model_count": len(models)}),
        ]), moment=MOMENT)
        return self.store.items()[0]

    def test_voice_and_audio_credits_keep_a_single_creator_work_out_of_the_list(self):
        self._record("1", "Yunara Showing Ahri Some Discipline",
                     ["Iidssm", "Adaline (VA)", "GeminiStarsign1 (VA)",
                      "Huntress___ (Audio/SFX)", "HentAudio (Audio)"])
        self.assertEqual(self.store.collected_compilations(), ())

    def test_a_cross_artist_pack_is_listed_with_both_counts(self):
        self._record("2", "Fuck Track / Futa PMV", [f"M{n}" for n in range(13)])
        listed = self.store.collected_compilations()
        self.assertEqual(len(listed), 1)
        self.assertEqual((listed[0].credited, listed[0].visual), (13, 13))
        self.assertEqual(listed[0].external_id, "2")

    def test_an_item_already_saved_as_an_asset_is_left_alone(self):
        item = self._record("3", "ON AND ON | HMV / PMV", [f"M{n}" for n in range(17)])
        self.connection.execute("UPDATE follow_item SET url=? WHERE id=?",
                                ("https://rule34video.com/video/3/x/", item.id))
        self.store.save_asset(item.id, confirm=True, moment=MOMENT)
        self.assertEqual(self.store.collected_compilations(), ())

    def test_purging_requires_explicit_confirmation(self):
        self._record("4", "Resident Evil - The Fallen Saga",
                     [f"M{n}" for n in range(14)])
        listed = self.store.collected_compilations()
        with self.assertRaises(FollowSourceError):
            self.store.purge_compilations(listed)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM follow_item").fetchone()[0], 1)

    def test_a_confirmed_purge_takes_the_playback_rows_with_it(self):
        item = self._record("5", "triss blacked censored", [f"M{n}" for n in range(13)])
        self.store.record_playback(item.id)
        listed = self.store.collected_compilations()
        self.assertEqual(self.store.purge_compilations(listed, confirm=True), 1)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM follow_item").fetchone()[0], 0)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM follow_playback").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
