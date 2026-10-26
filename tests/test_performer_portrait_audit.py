import contextlib
import csv
import os
import importlib.util
import io
import json
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def jpeg_bytes(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 140, 160)).save(buffer, "JPEG")
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.headers: dict[str, str] = {}
        self.body = body


class FakeTransport:
    """按 URL 子串返回预置响应；记录调用顺序以便断言续跑不再重复下载。"""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, request, timeout, max_bytes):
        self.calls.append(request.url)
        for fragment, response in self.routes.items():
            if fragment in request.url:
                if callable(response):
                    return response(len([c for c in self.calls if fragment in c]))
                return response
        return FakeResponse(404, b"")


class Exploding:
    def __call__(self, request, timeout, max_bytes):
        raise OSError("[SSL: UNEXPECTED_EOF_WHILE_READING]")


def payload(obj) -> FakeResponse:
    return FakeResponse(200, json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def make_ledger(path: Path, entities: list[dict], aliases: list[tuple[int, str]],
                asset_links: list[int] | None = None,
                codes: dict[int, str] | None = None):
    """`codes` 给某个 performer 的作品指定番号；不给就按 JAV 处理。

    真实账本里 performer 是通过 `asset` 认出「是不是 JAV」的，所以这个助手两张表
    都建。只建 `asset_entity` 的话，取像脚本一旦按作品判断来源，测试就整片报
    `no such table: asset`。这张表是让测试和真实 schema 对齐，不是为了迁就实现。
    """
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT,"
                       " canonical_name TEXT, normalized_name TEXT, metadata_json TEXT)")
    connection.execute("CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT,"
                       " normalized_alias TEXT, source TEXT)")
    counts: dict[int, int] = {}
    for link in asset_links or []:
        counts[link] = counts.get(link, 0) + 1
    connection.execute("CREATE TABLE asset_entity(asset_id INTEGER, entity_id INTEGER)")
    connection.execute("CREATE TABLE asset(id INTEGER PRIMARY KEY, medium TEXT, code TEXT)")
    for entity in entities:
        connection.execute(
            "INSERT INTO entity(id, kind, canonical_name, normalized_name, metadata_json)"
            " VALUES(?,?,?,?,?)",
            (entity["id"], "performer", entity["canonical"], entity["canonical"],
             json.dumps(entity.get("metadata", {}), ensure_ascii=False)))
    for entity_id, alias in aliases:
        connection.execute(
            "INSERT INTO entity_alias(entity_id, alias, normalized_alias, source)"
            " VALUES(?,?,?,?)",
            (entity_id, alias, alias.lower(), "test"))
    for entity_id, count in counts.items():
        for index in range(count):
            asset_id = entity_id * 100 + index
            connection.execute("INSERT INTO asset_entity VALUES(?,?)", (asset_id, entity_id))
            connection.execute("INSERT INTO asset(id, medium, code) VALUES(?,?,?)",
                               (asset_id, "video",
                                (codes or {}).get(entity_id, "ABW-001")))
    connection.commit()
    connection.close()


GFRIENDS_TREE = {"Content": {
    "z-DMM(骑)": {"立花美涼.jpg": "立花美涼.jpg?t=1"},
    "0-Hand-Storage": {"立花美涼.jpg": "AI-Fix-立花美涼.jpg?t=2"},
}}


class PerformerPortraitAuditTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script("audit_performer_portraits")
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.avatars = self.tmp / "avatars"
        self.avatars.mkdir()
        self.out = self.tmp / "portrait-audit.csv"
        self.candidates = self.tmp / "performer-avatar-candidate-test.csv"
        self.health = self.tmp / "performer-avatar-source-health-test.csv"
        self.cache = self.tmp / "provider-cache"

    def args(self, db: Path, *, resume: bool = False):
        argv = ["--db", str(db), "--avatars", str(self.avatars),
                "--out", str(self.out), "--candidates", str(self.candidates),
                "--health", str(self.health), "--cache-dir", str(self.cache),
                "--workers", "1"]
        if resume:
            argv.append("--resume")
        return self.module.build_parser().parse_args(argv)

    def rows(self) -> list[dict]:
        with self.out.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def write_orphan_avatar(self, old_id: int, provenance: dict,
                            body: bytes = jpeg_bytes(500, 600)):
        (self.avatars / f"performer-{old_id}.img").write_bytes(body)
        (self.avatars / f"performer-{old_id}.img.ct").write_text("image/jpeg\n")
        (self.avatars / f"performer-{old_id}.img.provenance.json").write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_regression_8022_relinks_from_8168_and_keeps_provenance(self):
        """交接单第 4.5 条的回归样本：8022 缺图，8168 已删但头像与 provenance 还在。

        必须同时证明：8022 经 name_localization.jp 找到 500×600 的 Gfriends 图；
        8168 的孤立文件按原 provenance 列为 orphan_relink 候选（不重新取源）；
        全程不落任何新头像文件。
        """
        module = self.module
        db = self.tmp / "ledger.db"
        make_ledger(db, [{
            "id": 8022, "canonical": "立花美凉",
            "metadata": {"name_localization": {"jp": "立花美涼", "zh_cn": "立花美凉"}},
        }], aliases=[(8022, "Misuzu Tachibana"), (8022, "たちばな みすず")])
        original = jpeg_bytes(500, 600)
        provenance = {
            "source": "gfriends media-library avatar repository",
            "gfriends_category": "8-Warashi",
            "gfriends_file": "AI-Fix-立花美涼.jpg",
            "matched_name": "立花美涼",
            "upstream_url": module.gfriends_url("8-Warashi", "AI-Fix-立花美涼.jpg"),
            "width": 500, "height": 600,
            "cached_at": "2026-08-15T00:00:00+00:00",
        }
        self.write_orphan_avatar(8168, provenance, original)

        transport = FakeTransport({
            "Filetree.json": payload(GFRIENDS_TREE),
            "AI-Fix": FakeResponse(200, jpeg_bytes(500, 600)),
        })
        exit_code = module.run(self.args(db), transport=transport)
        self.assertEqual(exit_code, 0)

        rows = self.rows()
        missing = [r for r in rows if r["section"] == "missing"]
        orphans = [r for r in rows if r["section"] == "orphan"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(len(orphans), 1)
        found = missing[0]
        self.assertEqual(found["entity_id"], "8022")
        self.assertEqual(found["verdict"], "ok")
        self.assertEqual(found["matched_name"], "立花美涼")
        self.assertEqual(found["name_source"], "localization_jp",
                         "canonical 与 alias 都未命中时必须落到本地化 jp 档")
        self.assertEqual((found["width"], found["height"]), ("500", "600"))
        self.assertEqual(found["gfriends_category"], "0-Hand-Storage")
        self.assertEqual(found["mime_type"], "image/jpeg")
        self.assertEqual(len(found["sha256"]), 64)
        self.assertTrue(Path(found["cache_path"]).is_file())
        provenance = json.loads(Path(found["provenance_path"]).read_text(encoding="utf-8"))
        self.assertEqual(provenance["provider"], "gfriends")
        self.assertEqual(provenance["matched_name"], "立花美涼")
        self.assertEqual(provenance["sha256"], found["sha256"])
        self.assertEqual(found["url"],
                         module.gfriends_url("0-Hand-Storage", "AI-Fix-立花美涼.jpg"))
        relink = orphans[0]
        self.assertEqual(relink["relink_old_id"], "8168")
        self.assertEqual(relink["relink_target_id"], "8022")
        self.assertEqual(relink["verdict"], "orphan_relink")
        self.assertEqual(relink["matched_name"], "立花美涼")
        # 孤立行保留原 provenance 口径，而不是本轮重查到的更优来源。
        self.assertEqual(relink["gfriends_category"], "8-Warashi")
        self.assertEqual(relink["url"],
                         module.gfriends_url("8-Warashi", "AI-Fix-立花美涼.jpg"))
        # dry-run 不写任何头像文件，旧文件一字节不动。
        self.assertFalse((self.avatars / "performer-8022.img").exists())
        self.assertEqual((self.avatars / "performer-8168.img").read_bytes(), original)
        with self.candidates.open(encoding="utf-8-sig", newline="") as handle:
            candidates = list(csv.DictReader(handle))
        self.assertEqual([row["entity_id"] for row in candidates], ["8022"])
        self.assertEqual(candidates[0]["provider"], "gfriends")
        self.assertEqual(Path(candidates[0]["cache_path"]).name,
                         candidates[0]["cache_path"])
        self.assertEqual(Path(candidates[0]["provenance_path"]).name,
                         candidates[0]["provenance_path"])
        with self.health.open(encoding="utf-8-sig", newline="") as handle:
            health = next(csv.DictReader(handle))
        self.assertEqual(health["attempted"], "1")
        self.assertEqual(health["fetched"], "1")
        self.assertEqual(health["succeeded"], "1")

    def test_lookup_order_is_canonical_then_alias_then_localization(self):
        module = self.module
        index = {"涼森れむ": [("7-S1", "涼森れむ.jpg")],
                 "Remu Suzumori": [("7-S1", "Remu-Suzumori.jpg")]}
        canonical_hit = module.audit_missing(
            {"entity_id": 1, "canonical": "涼森れむ", "aliases": ["Remu Suzumori"],
             "jp": "涼森れむ"}, index, None, self.args(self.tmp / "none.db"))
        self.assertEqual(canonical_hit["name_source"], "canonical")
        alias_hit = module.audit_missing(
            {"entity_id": 2, "canonical": "铃木一郎", "aliases": ["Remu Suzumori"],
             "jp": "涼森れむ"}, index, None, self.args(self.tmp / "none.db"))
        self.assertEqual(alias_hit["name_source"], "alias")
        jp_only = module.audit_missing(
            {"entity_id": 3, "canonical": "铃木一郎", "aliases": [],
             "jp": "涼森れむ"}, index, None, self.args(self.tmp / "none.db"))
        self.assertEqual(jp_only["name_source"], "localization_jp")

    def test_portrait_gate_uses_long_and_short_side(self):
        """竖构图头像不能套方图门槛：手工精选 334x501 必须放行。"""
        acceptable = self.module.acceptable
        self.assertTrue(acceptable((334, 501), 500, 300), "0-Hand-Storage 精选图")
        self.assertTrue(acceptable((360, 508), 500, 300), "8-GRAPHIS 写真")
        self.assertTrue(acceptable((1500, 2125), 500, 300))
        self.assertFalse(acceptable((125, 125), 500, 300), "DMM 官方小图")
        self.assertFalse(acceptable((640, 200), 500, 300), "细长横条不是人像")

    def test_gfriends_index_orders_sources_by_quality_prefix(self):
        module = self.module
        tree = {"Content": {
            "z-DMM(骑)": {"篠田ゆう.jpg": "篠田ゆう.jpg?t=1"},
            "0-Hand-Storage": {"篠田ゆう.jpg": "AI-Fix-篠田ゆう.jpg?t=2"},
            "7-S1": {"篠田ゆう.jpg": "篠田ゆう.jpg?t=3"},
        }}
        index = module.parse_gfriends(payload(tree).body)
        categories = [entry[0] for entry in index["篠田ゆう"]]
        self.assertEqual(categories, ["0-Hand-Storage", "7-S1", "z-DMM(骑)"])
        # 键是展示名、值才是真实文件名，二者可以不同。
        self.assertEqual(index["篠田ゆう"][0][1], "AI-Fix-篠田ゆう.jpg")

    def test_unknown_quality_prefix_sorts_after_known_sources(self):
        module = self.module
        tree = {"Content": {
            "?Future": {"Remu Suzumori.jpg": "future.jpg?t=1"},
            "7-S1": {"remu suzumori.jpg": "known.jpg?t=2"},
        }}
        index = module.parse_gfriends(payload(tree).body)
        self.assertEqual(index["remu suzumori"],
                         [("7-S1", "known.jpg"), ("?Future", "future.jpg")])

    def test_image_inspection_rejects_non_raster(self):
        module = self.module
        self.assertIsNone(module.inspect_image(b"<svg xmlns='http://www.w3.org/2000/svg'/>"))
        self.assertIsNone(module.inspect_image(b"not an image"))

    def test_fetch_downgrades_network_errors_instead_of_raising(self):
        """一次 TLS 抖动曾让 62 分钟的批量结果全部丢失，必须降级为 None。"""
        module = self.module
        self.assertIsNone(module.fetch(Exploding(), "https://x.example/a", "text/html"))

    def test_second_run_reuses_index_and_image_cache_without_network(self):
        module = self.module
        db = self.tmp / "ledger.db"
        make_ledger(db, [{"id": 1, "canonical": "立花美涼", "metadata": {}}], aliases=[])
        first = FakeTransport({
            "Filetree.json": payload(GFRIENDS_TREE),
            "AI-Fix": FakeResponse(200, jpeg_bytes(500, 600)),
        })
        self.assertEqual(module.run(self.args(db), transport=first), 0)

        offline = FakeTransport({})
        self.assertEqual(module.run(self.args(db), transport=offline), 0)
        self.assertEqual(offline.calls, [])
        with self.health.open(encoding="utf-8-sig", newline="") as handle:
            health = next(csv.DictReader(handle))
        self.assertEqual(health["index_cache_reused"], "1")
        self.assertEqual(health["snapshot_reused"], "1")
        self.assertEqual(health["fetched"], "0")
        self.assertEqual(health["succeeded"], "1")

    def _performers(self, db):
        module = self.module
        connection = module.open_readonly(db)
        try:
            return {record["entity_id"]: record
                    for record in module.load_performers(connection)}
        finally:
            connection.close()

    def test_a_performer_with_no_jav_release_is_not_sent_to_a_jav_library(self):
        """Gfriends 是 JAV 图库，拿非 JAV 的人去查必然落空。

        实测 567 位有作品的 performer 里 26 位不是 JAV，缺头像的 72 位里 19 位属于此类
        （中文素人创作者）。查它们不只是白费往返——更糟的是它们混进 no_match，把真正
        的 JAV 缺口盖住，让人以为图库覆盖比实际差。
        """
        db = self.tmp / "ledger.db"
        make_ledger(db,
                    [{"id": 1, "canonical": "立花美涼", "metadata": {}},
                     {"id": 2, "canonical": "Cola酱", "metadata": {}}],
                    aliases=[], asset_links=[1, 2],
                    codes={2: ""})          # 2 号的作品没有番号：非 JAV 的反面证据
        records = self._performers(db)
        self.assertTrue(records[1]["jav"])
        self.assertFalse(records[2]["jav"])
        targets = self.module.missing_targets(
            self.module.open_readonly(db), self.tmp / "avatars", 0)
        self.assertEqual([t["entity_id"] for t in targets], [1])

    def test_a_skipped_performer_still_appears_in_the_audit_table(self):
        """跳过不等于忽略。

        悄悄丢掉最糟：复核的人看不到这个人，只会以为她根本不缺头像。所以照样出一行，
        判定写成 skipped_not_jav，说明「需要另一个来源」而不是「查不到」。
        """
        db = self.tmp / "ledger.db"
        make_ledger(db, [{"id": 2, "canonical": "Cola酱", "metadata": {}}],
                    aliases=[], asset_links=[2], codes={2: ""})
        skipped = self.module.skipped_targets(
            self.module.open_readonly(db), self.tmp / "avatars")
        self.assertEqual([record["entity_id"] for record in skipped], [2])
        row = self.module.audit_skipped(skipped[0])
        self.assertEqual(row["verdict"], "skipped_not_jav")
        self.assertEqual(row["section"], "missing")
        self.assertIn("JAV", row["note"])

    def test_a_performer_with_no_visible_works_is_still_looked_up(self):
        """看不见作品是未知，不是反面证据。

        把未知也当成非 JAV，任何一次取数疏漏都会静默地把人整批排除掉——而那种失效
        不会报错，只会让缺口悄悄变小。
        """
        db = self.tmp / "ledger.db"
        make_ledger(db, [{"id": 3, "canonical": "查无作品", "metadata": {}}], aliases=[])
        records = self._performers(db)
        self.assertTrue(records[3]["jav"], "没有作品证据时应当照查，不该跳过")
        self.assertEqual(records[3]["known_works"], 0)

    def test_a_stale_index_cache_is_refetched_instead_of_reused_forever(self):
        """索引缓存要有保鲜期，否则「找不到」这个结论会被永久固化。

        Gfriends 是持续增补的图库，缓存只要文件在就一直复用的话，实测后果是：
        2026-08-25 的快照里没有「釈アリス」，之后 Gfriends 加了她（两份索引正好差这
        一条），但本地无论重跑多少次都还是 no_match——判定是对的，只是对的是一周前。

        这类失效最难发现：脚本没报错，健康报告一切正常，只是答案停在了过去。
        """
        module = self.module
        db = self.tmp / "ledger.db"
        make_ledger(db, [{"id": 1, "canonical": "立花美涼", "metadata": {}}], aliases=[])
        first = FakeTransport({
            "Filetree.json": payload(GFRIENDS_TREE),
            "AI-Fix": FakeResponse(200, jpeg_bytes(500, 600)),
        })
        self.assertEqual(module.run(self.args(db), transport=first), 0)

        cache = next(self.tmp.rglob("gfriends-filetree.json"))
        aged = time.time() - module.INDEX_MAX_AGE_SECONDS - 60
        os.utime(cache, (aged, aged))

        again = FakeTransport({
            "Filetree.json": payload(GFRIENDS_TREE),
            "AI-Fix": FakeResponse(200, jpeg_bytes(500, 600)),
        })
        self.assertEqual(module.run(self.args(db), transport=again), 0)
        self.assertTrue(any("Filetree.json" in str(call) for call in again.calls),
                        "缓存过期后必须重新取索引，而不是照抄旧答案")
        with self.health.open(encoding="utf-8-sig", newline="") as handle:
            health = next(csv.DictReader(handle))
        self.assertEqual(health.get("index_cache_stale"), "1",
                         "过期重取要和「本来就没缓存」在健康报告里分得开")

    def _run_on_stale_fallback(self, db: Path) -> str:
        """先跑一轮建好缓存，再把缓存放旧、让索引取不到，返回第二轮的输出。"""
        module = self.module
        first = FakeTransport({
            "Filetree.json": payload(GFRIENDS_TREE),
            "AI-Fix": FakeResponse(200, jpeg_bytes(500, 600)),
        })
        self.assertEqual(module.run(self.args(db), transport=first), 0)
        cache = next(self.tmp.rglob("gfriends-filetree.json"))
        aged = time.time() - module.INDEX_MAX_AGE_SECONDS - 60
        os.utime(cache, (aged, aged))

        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(module.run(self.args(db), transport=FakeTransport({})), 0)
        return printed.getvalue()

    def test_falling_back_to_a_stale_index_is_announced_and_counted(self):
        """取不到新索引而用旧缓存，必须当场说出来，健康报告也要与正常复用分开。

        两种情况产出的 CSV 长得一模一样：同一批人、同一批判定。不声明的话，一次
        GitHub 抖动就会让「这一轮的结论只代表快照那天」这件事无人知晓。
        """
        db = self.tmp / "ledger.db"
        make_ledger(db, [{"id": 1, "canonical": "立花美涼", "metadata": {}}], aliases=[])
        output = self._run_on_stale_fallback(db)
        self.assertIn("告警", output)
        self.assertIn("本地缓存", output)
        with self.health.open(encoding="utf-8-sig", newline="") as handle:
            health = next(csv.DictReader(handle))
        self.assertEqual(health["index_cache_fallback"], "1")
        self.assertEqual(health["index_cache_stale"], "1")
        # 已经缓存过的图不再出网，旧索引照样能把命中那位判成 ok。
        rows = {row["entity_id"]: row for row in self.rows() if row["section"] == "missing"}
        self.assertEqual(rows["1"]["verdict"], "ok")

    def test_a_stale_index_fallback_does_not_freeze_no_match(self):
        """旧索引里没有这个人，只说明快照那天没有，不能当成结论写进 CSV。

        no_match 是终态，`--resume` 见到就永远跳过。索引取不到时照写 no_match，
        等于把一次网络失败变成一个再也不会被重新审视的答案——釈アリス 那次就是
        这么固化的，区别只在于上一次的原因是缓存不过期，这一次是兜底缓存。
        """
        module = self.module
        db = self.tmp / "ledger.db"
        make_ledger(db, [
            {"id": 1, "canonical": "立花美涼", "metadata": {}},
            {"id": 2, "canonical": "釈アリス", "metadata": {}},
        ], aliases=[])
        self._run_on_stale_fallback(db)
        rows = {row["entity_id"]: row for row in self.rows() if row["section"] == "missing"}
        self.assertEqual(rows["2"]["verdict"], "error")
        self.assertIn("过期缓存", rows["2"]["note"])

        # 索引恢复后续跑：那一行必须被重新问一次，而不是被 --resume 跳过。
        grown = dict(GFRIENDS_TREE["Content"])
        grown["0-Hand-Storage"] = dict(grown["0-Hand-Storage"])
        grown["0-Hand-Storage"]["釈アリス.jpg"] = "AI-Fix-釈アリス.jpg?t=3"
        healthy = FakeTransport({
            "Filetree.json": payload({"Content": grown}),
            # 与 1 号那张不同的字节，否则会被同图去重判成 duplicate 而不是 ok。
            "AI-Fix": FakeResponse(200, jpeg_bytes(520, 620)),
        })
        self.assertEqual(module.run(self.args(db, resume=True), transport=healthy), 0)
        rows = {row["entity_id"]: row for row in self.rows() if row["section"] == "missing"}
        self.assertEqual(rows["2"]["verdict"], "ok")

    def test_exact_duplicate_image_is_evidence_but_not_a_review_candidate(self):
        module = self.module
        db = self.tmp / "ledger.db"
        make_ledger(db, [
            {"id": 1, "canonical": "甲", "metadata": {}},
            {"id": 2, "canonical": "乙", "metadata": {}},
        ], aliases=[])
        tree = {"Content": {"7-S1": {"甲.jpg": "a.jpg", "乙.jpg": "b.jpg"}}}
        transport = FakeTransport({
            "Filetree.json": payload(tree),
            "Content/": FakeResponse(200, jpeg_bytes(800, 600)),
        })
        self.assertEqual(module.run(self.args(db), transport=transport), 0)
        rows = {row["entity_id"]: row for row in self.rows() if row["section"] == "missing"}
        self.assertEqual(rows["1"]["verdict"], "ok")
        self.assertEqual(rows["2"]["verdict"], "duplicate")
        self.assertEqual(rows["2"]["duplicate_of_entity_id"], "1")
        self.assertTrue(Path(rows["2"]["provenance_path"]).is_file())
        with self.candidates.open(encoding="utf-8-sig", newline="") as handle:
            candidates = list(csv.DictReader(handle))
        self.assertEqual([row["entity_id"] for row in candidates], ["1"])
        with self.health.open(encoding="utf-8-sig", newline="") as handle:
            health = next(csv.DictReader(handle))
        self.assertEqual(health["duplicates"], "1")

    def test_host_limiter_throttles_each_site_independently(self):
        """各站各自排队：慢站的等待不该拖住别的站。"""
        module = self.module
        limiter = module.HostLimiter({"slow.example": 10.0, "fast.example": 0.0})
        started = time.monotonic()
        limiter.wait("https://fast.example/a")
        limiter.wait("https://fast.example/b")
        limiter.wait("https://other.example/c")   # 未登记的主机不限速
        self.assertLess(time.monotonic() - started, 1.0)
        limiter.wait("https://slow.example/a")
        self.assertGreater(limiter._next["slow.example"], time.monotonic())

    def test_resume_retries_error_rows_and_keeps_final_verdicts(self):
        """网络失败记 error 并在续跑时重试；已判定实体不得重复下载。"""
        module = self.module
        db = self.tmp / "ledger.db"
        make_ledger(db, [
            {"id": 1, "canonical": "立花美涼", "metadata": {}},
            {"id": 2, "canonical": "涼森れむ", "metadata": {}},
        ], aliases=[])
        tree = {"Content": {"7-S1": {
            "立花美涼.jpg": "a.jpg?t=1", "涼森れむ.jpg": "b.jpg?t=1"}}}

        def broken(call_count: int):
            raise OSError("boom")

        first = FakeTransport({"Filetree.json": payload(tree), "Content/": broken})
        module.run(self.args(db), transport=first)
        rows = {row["entity_id"]: row["verdict"] for row in self.rows()
                if row["section"] == "missing"}
        self.assertEqual(rows, {"1": "error", "2": "error"})

        good = FakeTransport({
            "Filetree.json": payload(tree),
            "a.jpg": FakeResponse(200, jpeg_bytes(800, 600)),
            "b.jpg": FakeResponse(200, jpeg_bytes(801, 600)),
        })
        module.run(self.args(db, resume=True), transport=good)
        rows = {row["entity_id"]: row["verdict"] for row in self.rows()
                if row["section"] == "missing"}
        self.assertEqual(rows, {"1": "ok", "2": "ok"})
        downloads_after_resume = [c for c in good.calls if "Content/" in c]
        self.assertEqual(len(downloads_after_resume), 2)

        third = FakeTransport({"Filetree.json": payload(tree)})
        module.run(self.args(db, resume=True), transport=third)
        self.assertEqual([c for c in third.calls if "Content/" in c], [],
                         "全部已判定后续跑不得再发任何图片请求")

    def test_resume_limit_advances_past_the_completed_first_batch(self):
        module = self.module
        db = self.tmp / "ledger.db"
        make_ledger(db, [
            {"id": 1, "canonical": "甲", "metadata": {}},
            {"id": 2, "canonical": "乙", "metadata": {}},
        ], aliases=[])
        tree = {"Content": {"7-S1": {
            "甲.jpg": "a.jpg?t=1", "乙.jpg": "b.jpg?t=1"}}}
        transport = FakeTransport({
            "Filetree.json": payload(tree),
            "Content/": FakeResponse(200, jpeg_bytes(800, 600)),
        })
        first = self.args(db)
        first.limit = 1
        module.run(first, transport=transport)
        self.assertEqual([row["entity_id"] for row in self.rows()
                          if row["section"] == "missing"], ["1"])

        second = self.args(db, resume=True)
        second.limit = 1
        module.run(second, transport=transport)
        self.assertEqual([row["entity_id"] for row in self.rows()
                          if row["section"] == "missing"], ["1", "2"])

    def test_csv_replace_failure_preserves_the_previous_resume_file(self):
        previous = "existing resume data\n"
        self.out.write_text(previous, encoding="utf-8")
        # 时钟跨过重试期限；真实写入与失败清理仍由 write_csv 执行。
        with mock.patch("peach.fsutil.os.replace", side_effect=OSError("locked")), \
                mock.patch("peach.fsutil.time.monotonic", side_effect=[0.0, 11.0]):
            with self.assertRaisesRegex(OSError, "locked"):
                self.module.write_csv(self.out, [{"section": "missing"}])
        self.assertEqual(self.out.read_text(encoding="utf-8"), previous)
        self.assertEqual(list(self.out.parent.glob(f".{self.out.name}.*.tmp")), [])

    def test_orphan_ambiguous_unresolved_and_target_exists(self):
        module = self.module
        db = self.tmp / "ledger.db"
        make_ledger(db, [
            {"id": 5, "canonical": "同名子", "metadata": {}},
            {"id": 6, "canonical": "同名子二号", "metadata": {}},
        ], aliases=[(5, "撞名"), (6, "撞名"), (5, "存在目标")])
        self.write_orphan_avatar(10, {"matched_name": "撞名"})
        self.write_orphan_avatar(11, {"matched_name": "查无此人"})
        self.write_orphan_avatar(12, {"matched_name": "存在目标"})
        (self.avatars / "performer-5.img").write_bytes(jpeg_bytes(600, 800))

        transport = FakeTransport({"Filetree.json": payload({"Content": {}})})
        module.run(self.args(db), transport=transport)
        verdicts = {row["relink_old_id"]: row["verdict"] for row in self.rows()
                    if row["section"] == "orphan"}
        self.assertEqual(verdicts["10"], "orphan_ambiguous")
        self.assertEqual(verdicts["11"], "orphan_no_provenance")
        self.assertEqual(verdicts["12"], "orphan_target_exists")

    def test_readonly_connection_refuses_writes(self):
        """只读 URI 是「绝不写库」的硬保证，不是约定。"""
        module = self.module
        db = self.tmp / "ledger.db"
        make_ledger(db, [{"id": 1, "canonical": "x", "metadata": {}}], aliases=[])
        connection = module.open_readonly(db)
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE mutation_attempt(a)")

    def test_missing_selection_skips_performers_that_already_have_files(self):
        module = self.module
        db = self.tmp / "ledger.db"
        make_ledger(db, [
            {"id": 1, "canonical": "有图的", "metadata": {}},
            {"id": 2, "canonical": "没图的", "metadata": {}},
        ], aliases=[], asset_links=[2])
        (self.avatars / "performer-1.img").write_bytes(jpeg_bytes(600, 800))
        targets = module.missing_targets(sqlite3.connect(db), self.avatars, 0)
        self.assertEqual([record["entity_id"] for record in targets], [2])


if __name__ == "__main__":
    unittest.main()
