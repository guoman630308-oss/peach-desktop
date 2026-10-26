"""番号样张：账本只填空、按批次撤回，后继先读快照再问官方站，缓存首访才下载（ADR-0068）。

全程临时账本与临时 `generated`；官方站的取页换成替身，不联网。MGS 页面是商品页裁剪下来的
样张那一块，只留解析会读到的标签。
"""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from peach import sample_followup, sample_images
from peach.http import HttpResponse
from peach.jav_cover_fetch import NotFound
from peach.repository import LedgerDatabase
from peach.scraping_access import SourcePaused
from support.ledger import fresh_ledger

ROOT = Path(__file__).resolve().parents[1]


def dmm(cid: str, n: int, *, small: bool = False) -> str:
    return (f"https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/{cid}/{cid}-{n}.jpg" if small
            else f"https://pics.dmm.co.jp/digital/video/{cid}/{cid}jp-{n}.jpg")


def jpeg(width: int = 800, height: int = 534) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 80, 60)).save(buffer, "JPEG")
    return buffer.getvalue()


MGS_PAGE = """<html><body>
<a class="link_magnify" id="EnlargeImage" href="https://image.mgstage.com/images/x/300mium/1000/pb_e_300mium-1000.jpg">封面</a>
<dl id="sample-photo"><dd><ul>
<li><a class="sample_image" href="https://image.mgstage.com/images/x/300mium/1000/cap_e_0_300mium-1000.jpg">1</a></li>
<li><a class="sample_image" href="https://image.mgstage.com/images/x/300mium/1000/cap_e_1_300mium-1000.jpg">2</a></li>
</ul></dd></dl></body></html>"""


class PageTransport:
    def __init__(self, pages: dict[str, tuple[int, bytes]]):
        self.pages, self.asked = pages, []

    def __call__(self, request, _timeout, _limit):
        self.asked.append(request.url)
        status, body = self.pages.get(request.url, (404, b""))
        return HttpResponse(status, {}, body, request.url)

    def close(self):
        pass


class UrlTests(unittest.TestCase):
    def test_dmm_small_samples_are_rewritten_to_the_original_beside_them(self):
        self.assertEqual(sample_images.original_url(dmm("ssis00057", 3, small=True)), dmm("ssis00057", 3))
        self.assertEqual(sample_images.original_url(
            "https://pics.dmm.co.jp/digital/video/ssis00057/ssis00057-3.jpg"), dmm("ssis00057", 3))
        other = "https://image.mgstage.com/images/x/cap_e_0_x.jpg"
        self.assertEqual(sample_images.original_url(other), other)

    def test_only_https_addresses_on_registered_sources_are_kept(self):
        kept = sample_images.usable([
            dmm("abc00001", 1, small=True), dmm("abc00001", 1), "http://pics.dmm.co.jp/a.jpg",
            "https://evil.example/a.jpg", "", dmm("abc00001", 2)])
        self.assertEqual(kept, [dmm("abc00001", 1), dmm("abc00001", 2)])
        many = sample_images.usable(dmm("abc00001", n) for n in range(1, 50))
        self.assertEqual(len(many), sample_images.MAX_PER_CODE)

    def test_the_code_key_refuses_path_characters(self):
        self.assertEqual(sample_images.code_key("ssis-057"), "SSIS-057")
        self.assertEqual(sample_images.code_key("../x"), "")


class LedgerCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.db = fresh_ledger(self.root)
        self.database = LedgerDatabase(self.db)
        self.generated = self.root / "generated"
        self.sources = self.root / "sources"
        self.covers = self.root / "covers"
        self.covers.mkdir()
        self.busted = []
        self.contract = SimpleNamespace(
            database=self.database, candidate_root=self.generated, follow_sources_root=self.sources,
            follow_secrets_root=self.root / "secrets", cache_bust=lambda: self.busted.append(1),
            has_cover=lambda key: (self.covers / f"{key}.jpg").is_file())

    def work(self, asset_id: int, code: str, *, cover: bool = True, trash: bool = False) -> None:
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,code,disposal) VALUES(?,'local',?,?,'video',?,?)",
                (asset_id, f"R:\\media\\{asset_id}.mp4", f"{asset_id}.mp4", code, "trash" if trash else None))
        if cover:
            (self.covers / f"{sample_images.code_key(code)}.jpg").write_bytes(b"cover")

    def snapshot(self, code: str, site: str, payload: dict) -> None:
        folder = self.sources / "library-metadata"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{code}-{site}.json").write_text(json.dumps(payload), encoding="utf-8")

    def rows(self) -> list[tuple]:
        with self.database.read_connection() as connection:
            return [tuple(row) for row in connection.execute(
                "SELECT code,position,url,site,source FROM code_sample_image ORDER BY code,position")]

    def run_followup(self, fetchers: dict, run_id: int = 7) -> dict:
        handle = SimpleNamespace(run_id=run_id, progress=lambda **_kwargs: None,
                                 saved={}, checkpoint=lambda **_kwargs: None)
        with mock.patch.dict(sample_images.FETCHERS, fetchers, clear=True):
            return sample_followup.run(self.contract, sample_followup.FOLLOWUP_KEY, handle,
                                       transport=object())


class LandingTests(LedgerCase):
    def test_landing_only_fills_a_code_that_has_no_samples_yet(self):
        with self.database.write_transaction(notify=False) as connection:
            first = sample_images.land(connection, "ssis-057", "dmm",
                                       [dmm("ssis00057", 1), dmm("ssis00057", 2)], source="auto:sample-images@1")
            second = sample_images.land(connection, "SSIS-057", "dmm", [dmm("ssis00057", 9)],
                                        source="auto:sample-images@2")
        self.assertEqual((first, second), (2, 0))
        self.assertEqual([row[:2] for row in self.rows()], [("SSIS-057", 1), ("SSIS-057", 2)])
        with self.database.read_connection() as connection:
            self.assertEqual(sample_images.counts(connection, ["ssis-057", "ABC-001"]), {"SSIS-057": (2, "dmm")})
            self.assertEqual(sample_images.sample_url(connection, "ssis-057", 2), dmm("ssis00057", 2))
            self.assertIsNone(sample_images.sample_url(connection, "SSIS-057", 3))

    def test_revert_removes_one_batch_or_the_whole_source(self):
        with self.database.write_transaction(notify=False) as connection:
            sample_images.land(connection, "AAA-001", "dmm", [dmm("aaa00001", 1)], source="auto:sample-images@1")
            sample_images.land(connection, "BBB-002", "dmm", [dmm("bbb00002", 1)], source="auto:sample-images@2")
            sample_images.land(connection, "CCC-003", "dmm", [dmm("ccc00003", 1)], source="manual")
            self.assertEqual(sample_images.revert(connection, sample_images.SOURCE, "auto:sample-images@1"), 1)
            self.assertEqual(sample_images.revert(connection, sample_images.SOURCE), 1)
        self.assertEqual([row[0] for row in self.rows()], ["CCC-003"])


class PendingTests(LedgerCase):
    def test_only_covered_unsampled_askable_codes_are_pending_newest_first(self):
        self.work(1, "SSIS-057")
        self.work(2, "ABP-100", cover=False)
        self.work(3, "300MIUM-1000")
        self.work(4, "FC2-PPV-1234567")
        self.work(5, "IPX-001", trash=True)
        self.work(6, "MIDE-002")
        with self.database.write_transaction(notify=False) as connection:
            sample_images.land(connection, "MIDE-002", "dmm", [dmm("mide00002", 1)], source="manual")
        misses = sample_images.Misses(self.root / "misses.json")
        with self.database.read_connection() as connection:
            found = sample_images.pending(connection, self.contract.has_cover, misses, {}, limit=10)
        self.assertEqual(found, ["300MIUM-1000", "SSIS-057"])

    def test_a_recent_miss_skips_the_code_until_it_expires(self):
        self.work(1, "SSIS-057")
        now = [1_000_000.0]
        misses = sample_images.Misses(self.root / "misses.json", clock=lambda: now[0])
        misses.record("dmm", "SSIS-057")
        misses.save()
        reloaded = sample_images.Misses(self.root / "misses.json", clock=lambda: now[0])
        with self.database.read_connection() as connection:
            self.assertEqual(sample_images.pending(connection, self.contract.has_cover, reloaded, {}, limit=5), [])
            now[0] += sample_images.MISS_TTL + 1
            self.assertEqual(sample_images.pending(connection, self.contract.has_cover, reloaded, {}, limit=5),
                             ["SSIS-057"])

    def test_plan_declares_one_followup_only_while_something_is_pending(self):
        config = SimpleNamespace(directory=lambda name: self.generated if name == "generated" else self.sources)
        self.assertEqual(sample_followup.plan(self.database, config, self.covers), [])
        self.work(1, "SSIS-057")
        self.assertEqual(sample_followup.plan(self.database, config, self.covers),
                         [{"key": sample_followup.FOLLOWUP_KEY, "task_key": sample_followup.TASK_KEY,
                           "label": sample_followup.TASK_LABEL}])


class FollowupTests(LedgerCase):
    def test_a_snapshot_is_landed_without_asking_any_site(self):
        self.work(1, "SSIS-057")
        self.snapshot("SSIS-057", "javdb", {"sample_images": ["https://c0.jdbstatic.com/samples/x.jpg"]})
        self.snapshot("SSIS-057", "dmm", {"sample_images": [dmm("ssis00057", 1, small=True),
                                                            dmm("ssis00057", 2, small=True)]})
        summary = self.run_followup({"dmm": mock.Mock(side_effect=AssertionError("不该联网"))})
        self.assertEqual(summary["landed"], 1)
        self.assertEqual(self.rows(), [
            ("SSIS-057", 1, dmm("ssis00057", 1), "dmm", "auto:sample-images@7"),
            ("SSIS-057", 2, dmm("ssis00057", 2), "dmm", "auto:sample-images@7")])
        self.assertEqual(self.busted, [1])

    def test_codes_without_a_snapshot_ask_the_site_of_their_tier(self):
        self.work(1, "SSIS-057")
        self.work(2, "300MIUM-1000")
        asked = []

        def fetcher(site, urls):
            def fetch(_transport, code, *, deadline=None):
                asked.append((site, code))
                return urls
            return fetch

        mgs = ["https://image.mgstage.com/images/x/cap_e_0_300mium-1000.jpg"]
        summary = self.run_followup({"dmm": fetcher("dmm", [dmm("ssis00057", 1, small=True)]),
                                     "mgstage": fetcher("mgstage", mgs)})
        self.assertEqual(sorted(asked), [("dmm", "SSIS-057"), ("mgstage", "300MIUM-1000")])
        self.assertEqual(summary["images"], 2)
        self.assertEqual({row[0]: row[3] for row in self.rows()}, {"SSIS-057": "dmm", "300MIUM-1000": "mgstage"})

    def test_a_site_that_says_no_is_remembered_and_a_cooldown_is_not(self):
        self.work(1, "SSIS-057")
        self.work(2, "ABP-100")
        self.work(3, "300MIUM-1000")

        def dmm_fetch(_transport, code, *, deadline=None):
            raise NotFound("HTTP 404")

        def mgs_fetch(_transport, code, *, deadline=None):
            raise SourcePaused("mgstage", 0.0, "冷却中")

        summary = self.run_followup({"dmm": dmm_fetch, "mgstage": mgs_fetch})
        self.assertEqual((summary["landed"], summary["absent"]), (0, 2))
        self.assertEqual(summary["paused"], ["mgstage"])
        self.assertEqual(summary["skipped"]["paused"], 1)
        self.assertEqual(summary["outcome"], "这一轮没有补上样张；跳过 1 部因来源冷却；还有 1 部待补，接着排下一轮")
        misses = sample_images.Misses(sample_images.misses_path(self.generated))
        self.assertTrue(misses.fresh("dmm", "SSIS-057"))
        self.assertFalse(misses.fresh("mgstage", "300MIUM-1000"), "冷却不是没有")
        with self.database.read_connection() as connection:
            self.assertEqual(sample_images.pending(connection, self.contract.has_cover, misses,
                                                   sample_images.snapshot_index(self.sources), limit=5),
                             ["300MIUM-1000"])

    def test_snapshots_all_land_in_one_round_and_only_network_asks_are_capped(self):
        """快照里现成的地址不占联网名额；名额用完的那部留到下一轮，这一轮推进了就说要接着跑。"""
        for index, code in enumerate(["AAA-001", "AAA-002", "AAA-003"], start=1):
            self.work(index, code)
            self.snapshot(code, "dmm", {"sample_images": [dmm(code.lower().replace("-", "00"), 1, small=True)]})
        self.work(4, "SSIS-057")
        self.work(5, "SSIS-058")
        asked = []

        def fetch(_transport, code, *, deadline=None):
            asked.append(code)
            return [dmm(code.lower().replace("-", "00"), 1, small=True)]

        with mock.patch.object(sample_followup, "BATCH", 1):
            summary = self.run_followup({"dmm": fetch})
        self.assertEqual(asked, ["SSIS-058"])
        self.assertEqual((summary["landed"], summary["deferred"], summary["remaining"]), (4, 1, 1))
        self.assertTrue(summary["continue"])
        self.assertEqual(summary["outcome"], "补上 4 部的样张（4 张）；跳过 1 部联网名额用完留到下一轮；还有 1 部待补，接着排下一轮")
        with mock.patch.object(sample_followup, "BATCH", 1):
            last = self.run_followup({"dmm": fetch}, run_id=8)
        self.assertEqual((last["landed"], last["remaining"], last["continue"]), (1, 0, False))
        self.assertEqual(last["outcome"], "补上 1 部的样张（1 张）")

    def test_a_round_held_up_only_by_a_cooldown_waits_for_the_next_processing(self):
        self.work(1, "300MIUM-1000")

        def mgs_fetch(_transport, code, *, deadline=None):
            raise SourcePaused("mgstage", 0.0, "冷却中")

        summary = self.run_followup({"mgstage": mgs_fetch})
        self.assertEqual((summary["landed"], summary["remaining"], summary["continue"]), (0, 1, False))
        self.assertEqual(summary["outcome"], "这一轮没有补上样张；跳过 1 部因来源冷却；还有 1 部待补，等下一次处理")

    def test_a_snapshot_seen_without_samples_counts_as_progress_only_once(self):
        """快照看过没样张只在第一次算推进：那一站一直冷却时，第二轮不能再说接着跑，否则接续空转不停。"""
        self.work(1, "SSIS-057")
        self.snapshot("SSIS-057", "r18dev", {"title": "x"})

        def dmm_fetch(_transport, code, *, deadline=None):
            raise SourcePaused("dmm", 0.0, "冷却中")

        first = self.run_followup({"dmm": dmm_fetch}, run_id=7)
        self.assertEqual((first["remaining"], first["continue"]), (1, True))
        second = self.run_followup({"dmm": dmm_fetch}, run_id=8)
        self.assertEqual((second["remaining"], second["continue"]), (1, False))
        self.assertEqual(second["outcome"], "这一轮没有补上样张；跳过 1 部因来源冷却；还有 1 部待补，等下一次处理")

    def test_codes_nobody_can_be_asked_about_are_counted_in_the_summary(self):
        """快照里没样张、又没有官方样张站的番号（FC2）不联网也不算「没有」，摘要说清跳过了几部、为什么。"""
        self.work(1, "FC2-PPV-1234567")
        self.work(2, "SSIS-057")
        self.snapshot("FC2-PPV-1234567", "dmm", {"title": "x"})
        summary = self.run_followup({"dmm": lambda _t, _code, *, deadline=None: [dmm("ssis00057", 1, small=True)]})
        self.assertEqual((summary["landed"], summary["deferred"]), (1, 0))
        self.assertEqual(summary["skipped"], {"no_site": 1, "recent_miss": 0, "paused": 0})
        self.assertEqual(summary["outcome"], "补上 1 部的样张（1 张）；跳过 1 部没有可问的官方站")

    def test_a_landed_batch_is_reverted_by_the_script_and_not_landed_twice(self):
        self.work(1, "SSIS-057")
        self.snapshot("SSIS-057", "dmm", {"sample_images": [dmm("ssis00057", 1)]})
        self.run_followup({}, run_id=7)
        self.assertEqual(self.run_followup({}, run_id=8)["codes"], 0, "已有样张的番号不再选")
        spec = importlib.util.spec_from_file_location(
            "revert_auto_landing_under_test", ROOT / "scripts" / "revert_auto_landing.py")
        revert = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(revert)
        base = ["--db", str(self.db), "--logo-root", str(self.root / "logos"),
                "--source", sample_images.SOURCE]
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(revert.main(base), 0)
        self.assertIn("'样张': 1", printed.getvalue())
        self.assertEqual(len(self.rows()), 1, "只列计划不删")
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(revert.main([*base, "--apply", "--backup", str(self.root / "backup.db")]), 0)
        self.assertIn("'删除样张': 1", printed.getvalue())
        self.assertEqual(self.rows(), [])


class MgsTests(unittest.TestCase):
    def test_mgs_samples_are_the_sample_image_links_not_the_cover(self):
        url = sample_images.MGS_DETAIL.format(code="300MIUM-1000")
        transport = PageTransport({url: (200, MGS_PAGE.encode("utf-8"))})
        found = sample_images.fetch_mgs(transport, "300mium-1000")
        self.assertEqual(found, [
            "https://image.mgstage.com/images/x/300mium/1000/cap_e_0_300mium-1000.jpg",
            "https://image.mgstage.com/images/x/300mium/1000/cap_e_1_300mium-1000.jpg"])
        self.assertEqual(transport.asked, [url])


class CacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.now = [1_000_000.0]
        self.cache = sample_images.SampleCache(self.root, clock=lambda: self.now[0])

    def test_a_cooldown_leaves_no_failure_mark_and_bad_bytes_do(self):
        self.assertIsNone(self.cache.fetch("SSIS-057", 1, dmm("ssis00057", 1), lambda _url: None, thumb=True))
        self.assertFalse(self.cache.failed_recently("SSIS-057", 1))
        self.assertIsNone(self.cache.fetch("SSIS-057", 1, dmm("ssis00057", 1), lambda _url: b"<html>", thumb=True))
        self.assertTrue(self.cache.failed_recently("SSIS-057", 1))

    def test_a_failure_mark_expires_and_the_next_view_downloads_again(self):
        (self.root / "SSIS-057").mkdir()
        (self.root / "SSIS-057" / "1.miss").write_bytes(b"")
        self.now[0] = (self.root / "SSIS-057" / "1.miss").stat().st_mtime + sample_images.DOWNLOAD_MISS_TTL + 1
        path = self.cache.fetch("SSIS-057", 1, dmm("ssis00057", 1), lambda _url: jpeg(), thumb=False)
        self.assertEqual(path, self.root / "SSIS-057" / "1.jpg")
        self.assertFalse((self.root / "SSIS-057" / "1.miss").exists())

    def test_the_cache_root_follows_the_photo_thumbnail_root(self):
        self.assertEqual(sample_images.cache_root(self.root / "generated" / "photo-thumbs"),
                         self.root / "generated" / "sample-cache")


if __name__ == "__main__":
    unittest.main()
