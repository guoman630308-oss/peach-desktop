"""关注作者建档（ADR-0096）：建谁、等谁、并进谁，链接从哪来，认人与按批撤回。"""
import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from peach import follow_creators, web_follow
from peach.follow_sources import FollowCandidate, SourceFetch
from peach.follow_store import FollowStore
from peach.web_contract import WebContract, dispatch_api_get, dispatch_api_post
from peach.web_entity import q_entity
from peach.follow_identity import author_key
from scripts import revert_auto_landing
from support.ledger import fresh_ledger

MOMENT = datetime(2026, 9, 1, tzinfo=timezone.utc)


class FollowCreatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.db = fresh_ledger(self.root)
        self.contract = WebContract(
            self.db, follow_sources_root=self.root / "sources",
            follow_secrets_root=self.root / "secrets", follow_shared_root=self.root / "shared",
            candidate_root=self.root / "generated", follow_state_root=self.root / "state")

    def source(self, provider, ref, label, metadata=None, items=1):
        with self.contract.database.write_transaction() as connection:
            store = FollowStore(lambda: connection, sources_root=self.contract.follow_sources_root)
            source_id = store.register(provider=provider, ref=ref, label=label,
                                       url=f"https://{provider}.test/{ref}", metadata=metadata,
                                       moment=MOMENT)
            candidates = tuple(FollowCandidate(
                provider=provider, external_id=f"{source_id}-{at}", title=f"{label} {source_id}-{at}",
                url=f"https://{provider}.test/{ref}/{at}") for at in range(items))
            store.record(source_id, SourceFetch(
                provider=provider, ref=ref, request_url=f"https://{provider}.test/{ref}",
                semantics="work", candidates=candidates, raw_body=b"<html/>"), moment=MOMENT)
        return source_id

    def creator(self, name):
        with self.contract.database.write_transaction() as connection:
            return int(connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES('creator',?,?,?,?)", (name, name.casefold(), "2026", "2026")).lastrowid)

    def plan(self):
        with self.contract.database.read_connection() as connection:
            return {landing.key: landing for landing in follow_creators.plan(connection)}

    def run_followup(self):
        return follow_creators.run(self.contract, "follow-creator:all", None)

    def read(self, sql, *args):
        with self.contract.database.read_connection() as connection:
            return [tuple(row) for row in connection.execute(sql, args)]

    def jul3d(self):
        self.source("kemono", "fanbox/12345", "Jul3D", items=2)
        self.source("kemono", "patreon/777", "Jul3D")
        self.source("f95zone", "123", "Jul3D Collection", metadata={
            "author_key": "jul3d",
            "official_links": [
                {"service": "patreon", "handle": "jul3d", "url": "https://www.patreon.com/jul3d/posts"},
                {"service": "twitter", "handle": "Jul3D_", "url": "http://twitter.com/Jul3D_?s=20"},
                {"service": "discord", "handle": "abc", "url": "https://discord.gg/abc"},
            ]})

    def test_a_new_author_becomes_a_creator_with_its_channels_and_socials(self):
        self.jul3d()
        landing = self.plan()["name:jul3d"]
        self.assertEqual((landing.action, landing.name), ("create", "Jul3D"))
        self.assertEqual(sorted(link["url"] for link in landing.links), [
            "https://www.patreon.com/jul3d", "https://www.pixiv.net/users/12345", "https://x.com/Jul3D_"])

        result = self.run_followup()
        self.assertEqual((result["created"], result["bound"], result["links"]), (1, 3, 3))
        entity_id, metadata = self.read(
            "SELECT id,metadata_json FROM entity WHERE canonical_name='Jul3D'")[0]
        self.assertEqual(json.loads(metadata)["source"], follow_creators.SOURCE)
        self.assertEqual(sorted(self.read("SELECT link_kind,label FROM entity_link WHERE entity_id=?", entity_id)),
                         [("official", "Patreon"), ("social", "X"), ("social", "pixiv")])
        self.assertEqual({row[0] for row in self.read("SELECT entity_id FROM follow_source")}, {entity_id})
        # 重跑是一张空计划。
        self.assertEqual(self.run_followup()["created"], 0)

        authors = dispatch_api_get(self.contract, "/api/follow/authors", {})["items"]
        self.assertEqual([(row["k"], row["key"], row["entity_id"]) for row in authors],
                         [("Jul3D", f"entity:{entity_id}", entity_id)])
        # 资料页的读数与名册那一格数的是同一批更新。
        page = q_entity(self.contract, {"kind": "creator", "name": "Jul3D"})
        self.assertEqual((page["follow"]["key"], page["follow"]["n"], page["follow"]["held"]),
                         (f"entity:{entity_id}", authors[0]["n"], []))

    def test_a_later_source_under_the_same_name_key_joins_the_existing_creator(self):
        self.jul3d()
        self.run_followup()
        later = self.source("kemono", "fanbox/99999", "Jul3D")
        entity_id = self.read("SELECT id FROM entity WHERE canonical_name='Jul3D'")[0][0]
        landing = self.plan()["name:jul3d"]
        self.assertEqual((landing.action, landing.entity_id), ("join", entity_id))
        with self.contract.database.read_connection() as connection:
            self.assertEqual(follow_creators.declare(connection)[0]["task_key"], "follow-creator")
        self.run_followup()
        self.assertEqual(self.read("SELECT entity_id FROM follow_source WHERE id=?", later), [(entity_id,)])
        self.assertIn(("https://www.pixiv.net/users/99999",),
                      self.read("SELECT url FROM entity_link WHERE entity_id=?", entity_id))

    def test_a_same_named_creator_is_only_offered_until_the_user_confirms(self):
        held_id = self.creator("InitialA")
        source_id = self.source("kemono", "fanbox/4242", "InitialA", items=3)
        landing = self.plan()["name:initiala"]
        self.assertEqual((landing.action, landing.entity_id), ("hold", held_id))
        with self.contract.database.read_connection() as connection:
            self.assertEqual(follow_creators.declare(connection), [])
        self.run_followup()
        self.assertEqual(self.read("SELECT entity_id FROM follow_source"), [(None,)])

        authors = dispatch_api_get(self.contract, "/api/follow/authors", {})["items"]
        self.assertEqual([(row["key"], row["held_by"]) for row in authors], [("name:initiala", "InitialA")])
        follow = q_entity(self.contract, {"kind": "creator", "name": "InitialA"})["follow"]
        self.assertEqual(follow["key"], "")
        self.assertEqual([(group["key"], group["n"]) for group in follow["held"]], [("name:initiala", 3)])

        written = dispatch_api_post(self.contract, "/api/follow/creator",
                                    {"entity_id": held_id, "key": "name:initiala"})
        self.assertEqual((written["bound"], written["links"]), (1, 1))
        self.assertEqual(self.read("SELECT entity_id FROM follow_source WHERE id=?", source_id), [(held_id,)])
        link_metadata = json.loads(self.read("SELECT metadata_json FROM entity_link")[0][0])
        self.assertEqual(link_metadata["source"], follow_creators.CONFIRM_SOURCE)
        follow = q_entity(self.contract, {"kind": "creator", "name": "InitialA"})["follow"]
        self.assertEqual((follow["key"], follow["held"]), (f"entity:{held_id}", []))
        with self.assertRaises(ValueError):
            dispatch_api_post(self.contract, "/api/follow/creator", {"entity_id": held_id, "key": "name:initiala"})

    def test_revert_unbinds_the_batch_and_keeps_creators_people_rely_on(self):
        self.jul3d()
        held_id = self.creator("InitialA")
        self.source("kemono", "fanbox/4242", "InitialA")
        self.run_followup()
        dispatch_api_post(self.contract, "/api/follow/creator", {"entity_id": held_id, "key": "name:initiala"})
        with contextlib.redirect_stdout(io.StringIO()):
            revert_auto_landing.main(["--db", str(self.db), "--apply", "--backup", str(self.root / "backup.db"),
                                      "--source", follow_creators.SOURCE])
        self.assertEqual(self.read("SELECT canonical_name FROM entity WHERE kind='creator'"), [("InitialA",)])
        # 人认过的那一组不归自动撤回。
        self.assertEqual(sorted(row[0] or 0 for row in self.read("SELECT entity_id FROM follow_source")),
                         [0, 0, 0, held_id])
        self.assertEqual(len(self.read("SELECT id FROM entity_link")), 1)
        with contextlib.closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_the_follow_check_declares_the_followup_only_while_someone_is_waiting(self):
        self.jul3d()
        result = web_follow._run_follow_check(self.contract, {"sources": []})
        self.assertEqual([row["key"] for row in result["followups"]], ["follow-creator:all"])
        self.run_followup()
        self.assertEqual(web_follow._run_follow_check(self.contract, {"sources": []})["followups"], [])

    def test_author_key_follows_the_binding(self):
        self.jul3d()
        self.run_followup()
        with self.contract.database.read_connection() as connection:
            rows = FollowStore(lambda: connection).sources()
        self.assertEqual({author_key(row) for row in rows},
                         {f"entity:{rows[0]['entity_id']}"})


class SourceLinkTests(unittest.TestCase):
    def row(self, provider, ref, metadata=None, url=""):
        return {"provider": provider, "ref": ref, "url": url, "label": ref,
                "metadata_json": json.dumps(metadata or {})}

    def urls(self, row):
        return [link["url"] for link in follow_creators.source_links(row)]

    def test_archives_restore_only_the_account_shapes_that_were_checked(self):
        self.assertEqual(self.urls(self.row("kemono", "fanbox/123")), ["https://www.pixiv.net/users/123"])
        self.assertEqual(self.urls(self.row("coomer", "onlyfans/some.one_")), ["https://onlyfans.com/some.one_"])
        self.assertEqual(self.urls(self.row("kemono", "fanbox/name")), [])
        self.assertEqual(self.urls(self.row("kemono", "gumroad/123")), [])

    def test_an_official_source_is_its_own_channel(self):
        self.assertEqual(self.urls(self.row("subscribestar", "artist", url="https://subscribestar.adult/artist")),
                         ["https://subscribestar.adult/artist"])

    def test_booru_profile_links_count_only_when_the_handle_is_the_author(self):
        row = self.row("rule34video", "artist", {"official_links": [
            {"service": "twitter", "handle": "artist", "url": "https://x.com/artist"},
            {"service": "twitter", "handle": "collaborator", "url": "https://x.com/collaborator"}]})
        self.assertEqual(self.urls(row), ["https://x.com/artist"])

    def test_subscribestar_keeps_the_host_from_the_profile(self):
        row = self.row("f95zone", "1", {"official_links": [
            {"service": "subscribestar", "handle": "artist", "url": "https://www.subscribestar.com/artist?x=1"}]})
        self.assertEqual(self.urls(row), ["https://subscribestar.com/artist"])


if __name__ == "__main__":
    unittest.main()
