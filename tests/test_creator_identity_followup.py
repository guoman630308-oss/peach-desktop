"""查创作者身份后继：资料站作品一览直接落 observed，没有页与没取到都不写，写下的能整批撤回。

全程临时账本、临时缓存；取页走真实的 `WikiSitePages`，传输换成按地址回页的替身，不联网。
"""
import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

from peach import creator_identity_followup as followup
from peach import entity_identity_research as research
from peach import performer_alias_followup as alias
from peach.entities import normalize_entity_name
from peach.entity_classification import write_claim
from peach.followups import Attempts, attempts_root
from peach.http import HttpResponse
from peach.repository import LedgerDatabase
from peach.scraping_access import paused_until
from peach.sources.seesaa import AV_NEME, SEESAA
from support.ledger import fresh_ledger

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-10-09T00:00:00.000Z"
CAST = ("八ッ橋さい子", "本多由奈", "北川ゆず")


def page_url(wiki: str, name: str) -> str:
    return research.WIKI_ROOTS[wiki] + "d/" + quote(name.encode("euc_jp"))


def work_table(*cast: str) -> bytes:
    rows = "".join(f'<tr><td>COSH-00{index}</td><td>こすっち00{index}</td>'
                   f'<td><a href="{page_url("sougouwiki", name)}">{name}</a></td></tr>'
                   for index, name in enumerate(cast, 1))
    return ('<meta charset="utf-8"><div id="page-body"><div class="user-area"><table>'
            '<tr><th>NO</th><th>TITLE</th><th>ACTRESS</th></tr>' + rows + '</table></div></div>').encode()


def load_revert():
    spec = importlib.util.spec_from_file_location(
        "revert_auto_landing_under_test", ROOT / "scripts" / "revert_auto_landing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Transport:
    """按地址回页面的传输替身；没登记的回 404。"""

    def __init__(self, pages: dict):
        self.pages, self.calls = pages, []

    def __call__(self, request, _timeout, _limit):
        self.calls.append(request.url)
        status, body = self.pages.get(request.url, (404, b""))
        return HttpResponse(status, {}, body, request.url)

    def close(self):
        pass


class NoWait:
    def wait(self, _url):
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
        self.transport = Transport({})
        self.next_asset = 1

    def entity(self, name: str, kind: str = "creator") -> int:
        with self.database.write_transaction(notify=False) as connection:
            cursor = connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
                " VALUES(?,?,?,?,?)", (kind, name, normalize_entity_name(name), STAMP, STAMP))
        return int(cursor.lastrowid)

    def work(self, creator: int, filename: str) -> None:
        asset_id, self.next_asset = self.next_asset, self.next_asset + 1
        with self.database.write_transaction(notify=False) as connection:
            connection.execute("INSERT INTO asset(id,location,path,name,medium) VALUES(?,'local',?,?,'video')",
                               (asset_id, f"R:\\media\\{asset_id}.mp4", filename))
            connection.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source)"
                               " VALUES(?,?,'creator','test')", (asset_id, creator))

    def sites(self) -> dict:
        cache = self.generated / "provider-cache" / "seesaa-pages"
        return {config.name: alias.WikiSitePages(cache, self.cooldown, self.transport, config=config)
                for config in (SEESAA, AV_NEME)}

    def run_followup(self, entity_id: int, run_id: int = 7) -> dict:
        handle = SimpleNamespace(run_id=run_id, progress=lambda **_kwargs: None)
        with mock.patch.object(followup, "open_sites", side_effect=lambda _contract: self.sites()), \
                mock.patch.object(alias, "_LIMITER", NoWait()):
            return followup.run(self.contract, followup.followup_key(entity_id), handle)

    def claims(self, entity_id: int) -> set[tuple[str, str, str]]:
        with self.database.read_connection() as connection:
            return {(row[0], row[1], row[2]) for row in connection.execute(
                "SELECT value,status,source FROM entity_classification WHERE entity_id=? AND facet='identity'",
                (entity_id,))}

    def series_creator(self) -> int:
        """名字查不到、文件名前缀查得到的创作者：三部三位不同出演者。"""
        for name in CAST:
            self.entity(name, "performer")
        creator = self.entity("Scotch Amateur")
        for index, name in enumerate(CAST, 1):
            self.work(creator, f"こすっち00{index} {name}")
        return creator


class LandingTests(Case):
    def test_a_work_list_under_the_filename_prefix_lands_as_observed_release(self):
        creator = self.series_creator()
        self.transport.pages[page_url("sougouwiki", "こすっち")] = (200, work_table(*CAST))
        summary = self.run_followup(creator)
        self.assertEqual(summary["outcome"], "是厂牌或系列")
        self.assertEqual(self.claims(creator), {("release", "observed", f"{followup.SOURCE}@7")})
        self.assertEqual(self.busted, [1])
        self.assertIn(page_url("sougouwiki", "Scotch Amateur"), self.transport.calls)
        # 有了可信身份，存量补派与再跑都不再问站。
        asked = len(self.transport.calls)
        self.assertEqual(self.run_followup(creator)["outcome"], "已有身份")
        self.assertEqual(len(self.transport.calls), asked)
        with self.database.read_connection() as connection:
            self.assertEqual(followup.stock(connection, Attempts(self.root / "attempts"), limit=5), [])

    def test_missing_pages_write_nothing_and_are_not_asked_again(self):
        creator = self.entity("lucky")
        summary = self.run_followup(creator)
        self.assertEqual(summary["outcome"], "两站都没有作品一览")
        self.assertIn("sougouwiki「lucky」（页不存在）", summary["asked"])
        self.assertEqual(self.claims(creator), set())
        asked = len(self.transport.calls)
        self.assertEqual(asked, 2)
        self.run_followup(creator)
        self.assertEqual(len(self.transport.calls), asked)
        with self.database.read_connection() as connection:
            self.assertEqual(followup.stock(connection, Attempts(attempts_root(self.generated)), limit=5), [])

    def test_a_refused_site_is_unfetched_cools_down_and_retries_later(self):
        creator = self.entity("lucky")
        self.transport.pages[page_url("sougouwiki", "lucky")] = (403, b"")
        self.assertEqual(self.run_followup(creator)["outcome"], "未取得")
        self.assertEqual(self.claims(creator), set())
        self.assertTrue(paused_until(self.cooldown, SEESAA.name))
        self.assertFalse(paused_until(self.cooldown, AV_NEME.name))
        attempts = Attempts(attempts_root(self.generated), clock=lambda: 10 ** 10)
        with self.database.read_connection() as connection:
            self.assertEqual([item.key for item in followup.stock(connection, attempts, limit=5)],
                             [followup.followup_key(creator)])

    def test_reviewed_identities_and_structural_names_are_never_queried(self):
        reviewed = self.entity("Known Person")
        with self.database.write_transaction(notify=False) as connection:
            write_claim(connection, entity_id=reviewed, facet="identity", value="person",
                        source="user:identity-review", evidence="用户复核", status="approved")
        self.entity("1080p")
        fresh = self.entity("lucky")
        with self.database.read_connection() as connection:
            self.assertEqual([item.key for item in followup.plan(connection, since_entity_id=0)],
                             [followup.followup_key(fresh)])
        self.assertEqual(self.run_followup(reviewed)["outcome"], "已有身份")
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.run_followup(999), {"outcome": "档案已不存在"})


class RevertTests(Case):
    def test_a_batch_of_claims_is_reverted_without_touching_other_sources(self):
        creator = self.series_creator()
        self.transport.pages[page_url("sougouwiki", "こすっち")] = (200, work_table(*CAST))
        self.run_followup(creator)
        with self.database.write_transaction(notify=False) as connection:
            write_claim(connection, entity_id=creator, facet="identity", value="unknown",
                        source=research.SOURCE, evidence="公开身份来源未取得")
        revert = load_revert()
        base = ["--db", str(self.db), "--source", followup.SOURCE]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(revert.main([*base, "--apply", "--backup", str(self.root / "backup.db")]), 0)
        self.assertEqual(self.claims(creator), {("unknown", "candidate", research.SOURCE)})


class AllocationTests(Case):
    def test_new_and_stock_creators_get_identity_followups(self):
        from peach import library_processing, performer_profile_followup, task_runs

        old = [self.entity(f"account{index:02d}") for index in range(5)]
        for entity_id in old:
            self.work(entity_id, f"{entity_id}.mp4")
        new = self.entity("newcomer")
        config = SimpleNamespace(directory=lambda _name: self.generated)
        # 一轮 8 条，补别名、补资料各留 1 条，创作者身份的存量取自己那 2 条。
        with mock.patch.object(task_runs, "MAX_FOLLOWUPS", 8), \
                mock.patch.object(alias, "STOCK_SHARE", 1), \
                mock.patch.object(performer_profile_followup, "STOCK_SHARE", 1), \
                mock.patch.object(followup, "STOCK_SHARE", 2):
            found = library_processing._entity_followups(self.database, config, old[-1])
        keys = [item["key"] for item in found if item["task_key"] == followup.TASK_KEY]
        self.assertEqual(keys[0], followup.followup_key(new))
        self.assertEqual(len(keys), 3)


if __name__ == "__main__":
    unittest.main()
