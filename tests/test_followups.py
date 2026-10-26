"""任务后继（ADR-0040）：声明、去重、上限、串行、失败边界与重启续跑。"""
import json
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from peach import followups as followups_module
from peach import seed_followup, seed_pack, seed_validation
from peach import task_runs as task_runs_module
from peach.avatar_followup import TASK_KEY as AVATAR_TASK_KEY
from peach.avatar_followup import followup_key, parse_key, plan
from peach.followups import FollowupRunner, FollowupType, lanes
from peach.jav_cover_fetch import NotFound
from peach.jobs import BackgroundJob
from peach.library_processing import process_library
from peach.repository import LedgerDatabase
from peach.task_runs import MAX_FOLLOWUPS, TaskRunStore
from support.conditions import windows_ledger_roots
from support.ledger import fresh_ledger


@contextmanager
def registered(*types: FollowupType):
    """临时登记几种后继，出去时还原。登记表是模块级的，用例之间不能互相污染。"""
    previous = dict(followups_module.REGISTRY)
    followups_module.REGISTRY.clear()
    for followup_type in types:
        followups_module.REGISTRY[followup_type.task_key] = followup_type
    try:
        yield
    finally:
        followups_module.REGISTRY.clear()
        followups_module.REGISTRY.update(previous)


def echo_type(task_key: str, *, writes_ledger: bool = True, run=None) -> FollowupType:
    return FollowupType(task_key=task_key, label=task_key, writes_ledger=writes_ledger,
                        run=run or (lambda contract, key, handle: {"outcome": key}))


class LedgerTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.db = fresh_ledger(self.root)
        self.database = LedgerDatabase(self.db)
        self.store = TaskRunStore(self.database, host="test-host")

    def parent(self, task_key: str = "library-processing"):
        run = self.store.start(task_key, trigger="manual")
        self.assertIsNotNone(run)
        return run

    def wait_until_settled(self, parent_run_id: int, timeout: float = 10.0) -> None:
        """等这一批后继全部走到终态。

        `FollowupRunner.stop()` 不能当同步点用：它一置停止位，通道线程就不再认领下一条，
        队尾那几条会停在 `pending`，用例读到的是「还没轮到」而不是「串行跑完了」。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = self.store.children(parent_run_id)
            if rows and all(row.finished_at for row in rows):
                return
            time.sleep(0.02)
        self.fail(f"后继没有在 {timeout} 秒内跑完")


class EnqueueTests(LedgerTestCase):
    def test_followups_become_pending_rows_under_their_parent(self):
        parent = self.parent()
        result = self.store.enqueue_followups(
            parent.id, [("entity-avatar:performer:1", AVATAR_TASK_KEY, "补头像：甲")])
        self.assertEqual(len(result["queued"]), 1)
        child = self.store.get(result["queued"][0])
        self.assertEqual(child.status, "pending")
        self.assertEqual(child.parent_run_id, parent.id)
        self.assertEqual(child.root_run_id, parent.id)
        self.assertEqual(child.followup_depth, 1)
        self.assertEqual(child.followup_key, "entity-avatar:performer:1")
        # 触发方式继承父任务：这一条归根结底是那一次手动触发带出来的。
        self.assertEqual(child.trigger, "manual")
        self.assertEqual([row.id for row in self.store.children(parent.id)], [child.id])
        # 派出后继的那一轮自己就是这条链的根。
        self.assertEqual(self.store.get(parent.id).root_run_id, parent.id)

    def test_same_key_twice_in_one_declaration_only_queues_once(self):
        parent = self.parent()
        result = self.store.enqueue_followups(parent.id, [
            ("entity-avatar:performer:1", AVATAR_TASK_KEY, "甲"),
            ("entity-avatar:performer:1", AVATAR_TASK_KEY, "甲"),
            ("entity-avatar:performer:2", AVATAR_TASK_KEY, "乙"),
        ])
        self.assertEqual(len(result["queued"]), 2)
        self.assertEqual(result["duplicates"], 1)

    def test_a_key_already_queued_is_not_queued_again(self):
        first = self.parent()
        self.store.enqueue_followups(
            first.id, [("entity-avatar:performer:1", AVATAR_TASK_KEY, "甲")])
        self.store.finish(first.id, "succeeded")
        second = self.store.start("scrape-codes", trigger="manual")
        result = self.store.enqueue_followups(
            second.id, [("entity-avatar:performer:1", AVATAR_TASK_KEY, "甲")])
        self.assertEqual(result["queued"], [])
        self.assertEqual(result["duplicates"], 1)

    def test_a_key_whose_run_already_finished_can_be_queued_again(self):
        first = self.parent()
        queued = self.store.enqueue_followups(
            first.id, [("entity-avatar:performer:1", AVATAR_TASK_KEY, "甲")])["queued"]
        self.store.finish(queued[0], "succeeded")
        second = self.store.start("scrape-codes", trigger="manual")
        result = self.store.enqueue_followups(
            second.id, [("entity-avatar:performer:1", AVATAR_TASK_KEY, "甲")])
        self.assertEqual(len(result["queued"]), 1)

    def test_more_than_the_cap_is_truncated_and_counted(self):
        parent = self.parent()
        declared = [(f"entity-avatar:performer:{index}", AVATAR_TASK_KEY, "甲")
                    for index in range(MAX_FOLLOWUPS + 3)]
        result = self.store.enqueue_followups(parent.id, declared)
        self.assertEqual(len(result["queued"]), MAX_FOLLOWUPS)
        self.assertEqual(result["truncated"], 3)

    def test_depth_beyond_the_cap_queues_nothing(self):
        parent = self.parent()
        with mock.patch.object(task_runs_module, "MAX_FOLLOWUP_DEPTH", 1):
            child_id = self.store.enqueue_followups(
                parent.id, [("a:1", AVATAR_TASK_KEY, "甲")])["queued"][0]
            result = self.store.enqueue_followups(
                child_id, [("a:2", AVATAR_TASK_KEY, "乙")])
        self.assertEqual(result["queued"], [])
        self.assertEqual(result["depth_exceeded"], 1)


class RunnerTests(LedgerTestCase):
    def contract(self):
        return SimpleNamespace(task_runs=self.store)

    def test_drain_runs_queued_followups_and_settles_them(self):
        parent = self.parent()
        with registered(echo_type(AVATAR_TASK_KEY)):
            queued = self.store.enqueue_followups(
                parent.id, [("entity-avatar:performer:1", AVATAR_TASK_KEY, "甲")])["queued"]
            self.assertEqual(FollowupRunner(self.contract()).drain(), 1)
        done = self.store.get(queued[0])
        self.assertEqual(done.status, "succeeded")
        self.assertEqual(done.result_summary["outcome"], "entity-avatar:performer:1")

    def test_a_followup_that_says_continue_is_queued_again_in_place_until_it_stops(self):
        """存量要跑好几轮的后继：结算时说还没完，就在同一层、同一个父任务下再排一条，排在别的后继后面。"""
        parent = self.parent()
        left, order = [3], []

        def chunk(contract, key, handle):
            order.append(key)
            left[0] -= 1
            return {"outcome": key, "continue": left[0] > 0}

        def avatar(contract, key, handle):
            order.append(key)
            return {"outcome": key}

        with registered(echo_type("code-samples", run=chunk), echo_type(AVATAR_TASK_KEY, run=avatar)):
            self.store.enqueue_followups(parent.id, [
                ("code-samples:stock", "code-samples", "补番号样张"),
                ("entity-avatar:performer:1", AVATAR_TASK_KEY, "甲")])
            self.assertEqual(FollowupRunner(self.contract()).drain(), 4)
        self.assertEqual(order, ["code-samples:stock", "entity-avatar:performer:1",
                                 "code-samples:stock", "code-samples:stock"])
        rows = [row for row in self.store.children(parent.id) if row.task_key == "code-samples"]
        self.assertEqual([row.status for row in rows], ["succeeded"] * 3)
        self.assertEqual({(row.followup_depth, row.root_run_id, row.trigger) for row in rows},
                         {(1, parent.id, "manual")})
        self.assertEqual([row.result_summary["continue"] for row in rows], [True, True, False])

    def test_ledger_writers_share_one_lane_and_never_overlap(self):
        live, peak = [0], [0]
        guard = threading.Lock()

        def slow(_contract, key, _handle):
            with guard:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.05)
            with guard:
                live[0] -= 1
            return {"outcome": key}

        parent = self.parent()
        first, second = (echo_type("ledger-a", run=slow), echo_type("ledger-b", run=slow))
        with registered(first, second):
            self.assertEqual(list(lanes()), [followups_module.LEDGER_LANE])
            self.store.enqueue_followups(parent.id, [
                ("ledger-a:1", "ledger-a", "甲"), ("ledger-a:2", "ledger-a", "乙"),
                ("ledger-b:1", "ledger-b", "丙"), ("ledger-b:2", "ledger-b", "丁"),
            ])
            runner = FollowupRunner(self.contract())
            runner.wake()
            self.wait_until_settled(parent.id)
            runner.stop(timeout=10)
        self.assertEqual(peak[0], 1)
        self.assertEqual(
            {row.status for row in self.store.children(parent.id)}, {"succeeded"})

    def test_types_that_do_not_write_the_ledger_get_their_own_lane(self):
        with registered(echo_type("ledger-a"), echo_type("cheap", writes_ledger=False)):
            self.assertEqual(sorted(lanes()), ["cheap", followups_module.LEDGER_LANE])

    def test_a_failed_followup_does_not_change_its_parent(self):
        def explode(_contract, _key, _handle):
            raise RuntimeError("取不到这张图")

        parent = self.parent()
        with registered(echo_type(AVATAR_TASK_KEY, run=explode)):
            queued = self.store.enqueue_followups(
                parent.id, [("entity-avatar:performer:1", AVATAR_TASK_KEY, "甲")])["queued"]
            self.store.finish(parent.id, "succeeded", summary={"followups": 1})
            with self.assertLogs("peach.followups", level="WARNING") as captured:
                FollowupRunner(self.contract()).drain()
            self.assertIn("取不到这张图", "\n".join(captured.output))
        child = self.store.get(queued[0])
        self.assertEqual(child.status, "failed")
        self.assertIn("取不到这张图", child.error)
        self.assertEqual(self.store.get(parent.id).status, "succeeded")
        self.assertEqual(self.store.get(parent.id).error, "")

    def test_an_unregistered_followup_fails_instead_of_holding_its_key(self):
        """账本里可能排着一种登记表里没有的后继。它得有个结局，否则互斥键一直占着。"""
        parent = self.parent()
        with registered(echo_type("ledger-a")):
            queued = self.store.enqueue_followups(
                parent.id, [("ledger-a:1", "ledger-a", "甲")])["queued"]
        runner = FollowupRunner(self.contract())
        with registered():
            runner._execute(self.store.get(queued[0]))
        failed = self.store.get(queued[0])
        self.assertEqual(failed.status, "failed")
        self.assertIn("ledger-a", failed.error)


class RestartTests(LedgerTestCase):
    def test_a_followup_left_running_is_queued_again_not_interrupted(self):
        parent = self.parent()
        queued = self.store.enqueue_followups(
            parent.id, [("entity-avatar:performer:1", AVATAR_TASK_KEY, "甲")])["queued"]
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "UPDATE task_run SET status='running',started_at=? WHERE id=?",
                ("2026-09-22T00:00:00.000Z", queued[0]))
        # 先跑启动恢复：它必须放过后继，否则重排就再也没有机会。
        self.assertNotIn(queued[0], self.store.recover_interrupted(stale_after=0))
        self.assertEqual(self.store.requeue_followups(), queued)
        run = self.store.get(queued[0])
        self.assertEqual(run.status, "pending")
        self.assertEqual(run.started_at, '2026-09-22T00:00:00.000Z')

    def test_a_queued_followup_survives_and_still_blocks_its_key(self):
        parent = self.parent()
        self.store.enqueue_followups(
            parent.id, [("entity-avatar:performer:1", AVATAR_TASK_KEY, "甲")])
        self.store.recover_interrupted(stale_after=0)
        again = self.store.enqueue_followups(
            self.parent("scrape-codes").id,
            [("entity-avatar:performer:1", AVATAR_TASK_KEY, "甲")])
        self.assertEqual(again["duplicates"], 1)


class BackgroundJobDispatchTests(LedgerTestCase):
    def job(self, runner=None):
        return BackgroundJob("PeachTestJob", task_key="library-processing",
                             runs=self.store, followup_runner=runner)

    def declared(self):
        return [{"key": "entity-avatar:performer:1", "task_key": AVATAR_TASK_KEY,
                 "label": "补全资料图片：甲"}]

    def test_a_successful_run_dispatches_what_it_declared(self):
        job = self.job()
        job.start(lambda job_id: job.update(
            job_id, status="complete", followups=self.declared()))
        job.thread.join(5)
        run = self.store.query(task_key="library-processing", limit=1)[0]
        self.assertEqual(run.status, "succeeded")
        self.assertEqual(run.result_summary["followups"], 1)
        # 声明本身是明细，不进摘要——摘要是活动页上一眼看完的那一行。
        self.assertNotIn("followups_declared", run.result_summary)
        self.assertEqual(len(self.store.children(run.id)), 1)

    def test_a_failed_run_dispatches_nothing(self):
        job = self.job()

        def work(job_id):
            job.update(job_id, followups=self.declared())
            raise RuntimeError("采集中断")

        job.start(work)
        job.thread.join(5)
        run = self.store.query(task_key="library-processing", limit=1)[0]
        self.assertEqual(run.status, "failed")
        self.assertEqual(self.store.children(run.id), [])

    def test_a_run_that_settles_with_open_issues_still_dispatches(self):
        job = self.job()
        job.start(lambda job_id: job.update(
            job_id, status="failed", error="3 项需要处理", followups=self.declared()))
        job.thread.join(5)
        run = self.store.query(task_key="library-processing", limit=1)[0]
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.result_summary["followups"], 1)
        self.assertEqual(len(self.store.children(run.id)), 1)

    def test_the_runner_is_woken_once_the_followups_are_queued(self):
        runner = mock.Mock()
        job = self.job(runner)
        job.start(lambda job_id: job.update(
            job_id, status="complete", followups=self.declared()))
        job.thread.join(5)
        runner.wake.assert_called_once_with()


class AvatarFollowupTests(LedgerTestCase):
    STAMP = "2026-09-22T00:00:00.000Z"

    def entity(self, kind: str, name: str) -> int:
        with self.database.write_transaction(notify=False) as connection:
            cursor = connection.execute(
                "INSERT INTO entity(kind,canonical_name,normalized_name,"
                "created_at,updated_at) VALUES(?,?,?,?,?)",
                (kind, name, name.casefold(), self.STAMP, self.STAMP))
        return int(cursor.lastrowid)

    def test_the_key_round_trips(self):
        self.assertEqual(parse_key(followup_key("performer", 8022)), ("performer", 8022))
        with self.assertRaises(ValueError):
            parse_key("entity-avatar:series:1")

    def test_only_new_entities_without_an_avatar_are_planned(self):
        avatars = self.root / "avatars"
        avatars.mkdir()
        old = self.entity("performer", "旧人")
        fresh = self.entity("performer", "新人")
        studio = self.entity("studio", "新厂牌")
        dressed = self.entity("performer", "已有头像")
        (avatars / f"performer-{dressed}.img").write_bytes(b"jpeg")
        with self.database.read_connection() as connection:
            found = plan(connection, avatars, since_entity_id=old)
        # 厂牌的图是标识，归补厂牌那条后继。
        self.assertEqual([item.key for item in found], [followup_key("performer", fresh)])
        self.assertNotIn(str(studio), found[0].key)
        self.assertTrue(found[0].label.endswith("新人"))
        self.assertEqual({item.task_key for item in found}, {AVATAR_TASK_KEY})

    def test_an_entity_that_already_has_an_avatar_is_a_no_op(self):
        from peach.avatar_followup import run as avatar_run

        avatars = self.root / "avatars"
        avatars.mkdir()
        entity_id = self.entity("performer", "甲")
        (avatars / f"performer-{entity_id}.img").write_bytes(b"jpeg")
        contract = SimpleNamespace(
            avatar_root=avatars, candidate_root=self.root / "generated",
            database=self.database, task_runs=self.store, cache_bust=lambda: None)
        summary = avatar_run(contract, followup_key("performer", entity_id), None)
        self.assertEqual(summary, {"outcome": "已有头像"})

    def test_a_studio_key_is_not_an_avatar_followup(self):
        with self.assertRaises(ValueError):
            parse_key(f"{AVATAR_TASK_KEY}:studio:1")

    def test_a_vanished_entity_is_not_a_failure(self):
        from peach.avatar_followup import run as avatar_run

        avatars = self.root / "avatars"
        avatars.mkdir()
        contract = SimpleNamespace(
            avatar_root=avatars, candidate_root=self.root / "generated",
            database=self.database, task_runs=self.store, cache_bust=lambda: None)
        summary = avatar_run(contract, followup_key("performer", 999), None)
        self.assertEqual(summary, {"outcome": "档案已不存在"})


class CoverFaceFollowupTests(LedgerTestCase):
    """图库给不出那一张时，从她单人作品的封面上截脸。

    人脸模型要下 ONNX，测试不出网：探针按封面宽度查一张表给出脸框，检脸本身由头像域
    的用例覆盖。
    """

    STAMP = AvatarFollowupTests.STAMP
    entity = AvatarFollowupTests.entity

    #: 封面宽 → 脸框（相对宽度）。没列的宽度是一张检不出脸的封面，比如戴着面具的原图。
    #: 截出来的方图脸在正中；绿底的是卖家打了模糊的封面，截出来那块检不出脸。
    FACES = {600: 0.15, 276: 0.2, 1000: 0.3, 900: 0.5}
    BLURRED = (40, 200, 40)

    def setUp(self):
        super().setUp()
        self.avatars = self.root / "avatars"
        self.avatars.mkdir()
        self.covers = self.root / "covers"
        self.covers.mkdir()
        self.person = self.entity("performer", "梨奈")
        faces = self.FACES

        class Probe:
            unavailable = ""

            def on_bytes(self, body):
                import io

                from PIL import Image
                image = Image.open(io.BytesIO(body)).convert("RGB")
                width, height = image.size
                share = faces.get(width)
                if width == height:
                    share = None if image.getpixel((width // 2, height // 2))[1] > 150 else 0.4
                face = ({"cx": 0.5, "cy": 0.4, "w": share, "h": share, "score": 0.9}
                        if share else None)
                return {"ratio": width / height, "px": [width, height], "face": face}

        patcher = mock.patch("peach.avatar_face.FaceProbe", Probe)
        patcher.start()
        self.addCleanup(patcher.stop)
        # 装图那一步会顺手算人脸边车，同样不出网。
        sidecar = mock.patch("peach.avatar_provider.FaceProbe")
        sidecar.start().return_value.return_value = None
        self.addCleanup(sidecar.stop)

    def work(self, asset_id: int, code: str, size: tuple[int, int] | None,
             performers: tuple[int, ...] = (), colour="gray") -> None:
        from PIL import Image

        if size:
            Image.new("RGB", size, colour).save(self.covers / f"{code}.jpg", format="JPEG")
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,code,size) "
                "VALUES(?,'R:',?,?,'video',?,100)",
                (asset_id, f"R:\\media\\{code}.mp4", f"{code}.mp4", code))
            for entity_id in performers or (self.person,):
                connection.execute(
                    "INSERT INTO asset_entity(asset_id,entity_id,role,source) "
                    "VALUES(?,?,'performer','test')", (asset_id, entity_id))

    def contract(self):
        return SimpleNamespace(
            avatar_root=self.avatars, candidate_root=self.root / "generated",
            cover_root=self.covers, database=self.database, task_runs=self.store,
            cache_bust=lambda: None)

    def run_followup(self) -> dict:
        from peach.avatar_followup import run as avatar_run

        return avatar_run(self.contract(), followup_key("performer", self.person), mock.Mock())

    def provenance(self) -> dict:
        return json.loads((self.avatars / f"performer-{self.person}.img.provenance.json")
                          .read_text(encoding="utf-8"))

    def test_the_widest_face_wins_over_the_biggest_cover(self):
        """戴面具的大图检不出脸，对头像毫无用处；挑的是脸上有多少像素。"""
        self.work(1, "FC2-PPV-1", (1800, 1000))
        self.work(2, "FC2-PPV-2", (276, 154))
        self.work(3, "FC2-PPV-3", (600, 400))
        other = self.entity("performer", "别人")
        self.work(4, "FC2-PPV-4", (900, 600), performers=(self.person, other))
        summary = self.run_followup()
        self.assertEqual((summary["outcome"], summary["source"]),
                         ("已装上", "作品封面 FC2-PPV-3"))
        record = self.provenance()
        self.assertEqual((record["provider"], record["external_id"], record["face_px"]),
                         ("cover-face", "FC2-PPV-3", 90))
        self.assertFalse(record["identity_verified"])
        # 90 像素的脸放大 2.4 倍是 216 的方框，以脸心为中心。
        self.assertEqual(record["crop_box"], [192, 52, 408, 268])
        # 没装上的那张缩略图人脸也截好留作候选，挑图弹层里一点就换；双人作品照旧不碰。
        kept = [json.loads(path.read_text(encoding="utf-8"))
                for path in (self.root / "generated").rglob(f"evidence/performer-{self.person}-*.json")]
        self.assertEqual(sorted((one["provider"], one["external_id"]) for one in kept),
                         [("cover-face", "FC2-PPV-2"), ("cover-face", "FC2-PPV-3")])

    def test_stock_skips_her_once_tried_until_her_works_change(self):
        from peach.avatar_followup import stock
        from peach.followups import Attempts, attempts_root

        self.work(1, "FC2-PPV-1", (1800, 1000))
        chosen = self.entity("performer", "人挑过")
        self.work(6, "FC2-PPV-6", (600, 400), performers=(chosen,))
        (self.avatars / f"performer-{chosen}.img").write_bytes(b"jpeg")
        attempts = Attempts(attempts_root(self.root / "generated"))

        def planned():
            with self.database.read_connection() as connection:
                return [item.key for item in stock(connection, self.avatars, attempts,
                                                   limit=10)]

        self.assertEqual(planned(), [followup_key("performer", self.person)])
        self.run_followup()
        self.assertEqual(planned(), [])
        self.work(2, "FC2-PPV-2", (276, 154))
        self.assertEqual(planned(), [followup_key("performer", self.person)])

    def test_a_thumbnail_is_the_floor_not_nothing(self):
        self.work(1, "FC2-PPV-1", (1800, 1000))
        self.work(2, "FC2-PPV-2", (276, 154))
        self.assertEqual(self.run_followup()["source"], "作品封面 FC2-PPV-2")
        self.assertEqual(self.provenance()["face_px"], 55)

    def test_a_clearer_cover_later_replaces_the_crop_and_nothing_else_does(self):
        self.work(2, "FC2-PPV-2", (276, 154))
        self.run_followup()
        self.assertEqual(self.run_followup()["outcome"], "已是最清楚的封面人脸")
        self.work(5, "FC2-PPV-5", (1000, 600))
        self.assertEqual(self.run_followup()["source"], "作品封面 FC2-PPV-5")
        self.assertEqual(self.provenance()["face_px"], 300)

    def test_a_blurred_cover_is_passed_over_even_with_the_widest_face(self):
        """卖家打了模糊的商品图：整张检得出很宽的脸，截出来那块认不出人，挑下一张。"""
        self.work(1, "FC2-PPV-1", (900, 600), colour=self.BLURRED)
        self.work(2, "FC2-PPV-2", (276, 154))
        self.assertEqual(self.run_followup()["source"], "作品封面 FC2-PPV-2")

    def test_an_unreadable_crop_installed_earlier_gives_way_to_a_narrower_face(self):
        self.work(2, "FC2-PPV-2", (276, 154))
        (self.avatars / f"performer-{self.person}.img.provenance.json").write_text(
            json.dumps({"provider": "cover-face", "face_px": 450}), encoding="utf-8")
        from PIL import Image
        Image.new("RGB", (400, 400), self.BLURRED).save(
            self.avatars / f"performer-{self.person}.img", format="JPEG")
        self.assertEqual(self.run_followup()["source"], "作品封面 FC2-PPV-2")
        self.assertEqual(self.provenance()["face_px"], 55)

    def test_a_whole_cover_installed_earlier_gives_way_to_any_face(self):
        self.work(2, "FC2-PPV-2", (276, 154))
        (self.avatars / f"performer-{self.person}.img").write_bytes(b"jpeg")
        (self.avatars / f"performer-{self.person}.img.provenance.json").write_text(
            json.dumps({"provider": "cover-fallback"}), encoding="utf-8")
        self.assertEqual(self.run_followup()["source"], "作品封面 FC2-PPV-2")
        self.assertEqual(self.provenance()["provider"], "cover-face")

    def test_a_picture_someone_chose_is_never_replaced(self):
        self.work(5, "FC2-PPV-5", (1000, 600))
        (self.avatars / f"performer-{self.person}.img").write_bytes(b"jpeg")
        (self.avatars / f"performer-{self.person}.img.provenance.json").write_text(
            json.dumps({"provider": "picker"}), encoding="utf-8")
        self.assertEqual(self.run_followup(), {"outcome": "已有头像"})

    def test_no_face_on_any_cover_installs_nothing(self):
        self.work(1, "FC2-PPV-1", (1800, 1000))
        summary = self.run_followup()
        self.assertEqual(summary["outcome"], "图库里没有这个名字，封面上没有能截的脸")
        self.assertFalse((self.avatars / f"performer-{self.person}.img").exists())

    def test_a_new_cover_plans_her_again_only_while_her_picture_is_a_crop(self):
        self.work(2, "FC2-PPV-2", (276, 154))
        chosen = self.entity("performer", "人挑过")
        self.work(6, "FC2-PPV-6", (600, 400), performers=(chosen,))
        (self.avatars / f"performer-{chosen}.img").write_bytes(b"jpeg")
        self.run_followup()
        watermark = chosen
        with self.database.read_connection() as connection:
            found = plan(connection, self.avatars, since_entity_id=watermark,
                         covered_asset_ids=[2, 6])
        self.assertEqual([item.key for item in found],
                         [followup_key("performer", self.person)])


class FaceStubCase(LedgerTestCase):
    """按人脸认人那几组共用的桩：不出网，比对模型按图下半截的颜色给固定特征。

    红的是她、蓝的是别人、灰的检不出脸。图库候选预先放进候选缓存；封面的检脸沿用封面
    截脸那组的桩，每张图都在下半截正中检出一张脸。
    """

    STAMP = AvatarFollowupTests.STAMP
    entity = AvatarFollowupTests.entity
    HER, OTHER, BLANK = (200, 40, 40), (40, 40, 200), (128, 128, 128)
    #: 她的另一张照片（和 HER 余弦 0.8，过线又不到近重复）与一张截错了人的封面。
    HER_AGAIN, STRANGER = (250, 120, 0), (40, 200, 40)
    VECTORS = {HER: (1.0, 0.0, 0.0), OTHER: (0.0, 1.0, 0.0), BLANK: None,
               HER_AGAIN: (0.8, 0.0, 0.6), STRANGER: (0.0, 0.0, -1.0)}

    def setUp(self):
        super().setUp()
        self.avatars = self.root / "avatars"
        self.avatars.mkdir()
        self.covers = self.root / "covers"
        self.covers.mkdir()
        self.person = self.entity("performer", "梨奈")
        self.providers = self.root / "generated" / "provider-cache" / "performer-avatars"
        self.gallery: dict[str, dict[str, str]] = {}
        self.unavailable = ""
        vectors, test = self.VECTORS, self

        class Probe:
            unavailable = ""

            def on_bytes(self, body):
                from peach.images import measure_image_size
                width, height = measure_image_size(body)
                return {"ratio": width / height, "px": [width, height],
                        "face": {"cx": 0.5, "cy": 0.75, "w": 0.2, "h": 0.2, "score": 0.9}}

        class Matcher:
            @property
            def unavailable(self):
                return test.unavailable

            def embedding(self, body):
                import io

                from PIL import Image
                if test.unavailable:
                    return None
                image = Image.open(io.BytesIO(body)).convert("RGB")
                pixel = image.getpixel((image.width // 2, image.height * 3 // 4))
                nearest = min(vectors, key=lambda colour: sum(
                    (a - b) ** 2 for a, b in zip(colour, pixel)))
                return vectors[nearest]

        for target, value in (("peach.avatar_face.FaceProbe", Probe),
                              ("peach.face_match.FaceMatcher", Matcher)):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        sidecar = mock.patch("peach.avatar_provider.FaceProbe")
        sidecar.start().return_value.return_value = None
        self.addCleanup(sidecar.stop)
        # 候选都在缓存里：真要联网就是用例写错了。
        offline = mock.patch("peach.http.HttpxTransport",
                             return_value=mock.Mock(side_effect=AssertionError("不许出网")))
        offline.start()
        self.addCleanup(offline.stop)

    @staticmethod
    def picture(size: tuple[int, int], colour: tuple[int, int, int]) -> bytes:
        """上半截黑、下半截是这个人的颜色。整张一个颜色会被当成占位底色挡掉。"""
        import io

        from PIL import Image, ImageDraw
        buffer = io.BytesIO()
        image = Image.new("RGB", size, colour)
        ImageDraw.Draw(image).rectangle((0, 0, size[0], size[1] // 2), fill="black")
        image.save(buffer, format="JPEG", quality=95)
        return buffer.getvalue()

    def candidate(self, category: str, size: tuple[int, int], colour) -> str:
        """往图库索引里加一张 `梨奈` 名下的候选，图预先放进缓存。返回它的 ref。"""
        from peach import gfriends
        from peach.avatar_provider import AvatarCandidateCache, inspect_avatar

        filename = f"梨奈-{len(self.gallery)}.jpg"
        self.gallery.setdefault(category, {})["梨奈.jpg"] = filename
        index_dir = self.providers / "gfriends"
        index_dir.mkdir(parents=True, exist_ok=True)
        (index_dir / gfriends.INDEX_NAME).write_text(
            json.dumps({"Content": self.gallery}), encoding="utf-8")
        body = self.picture(size, colour)
        AvatarCandidateCache(index_dir).store(gfriends.image_url(category, filename), body,
                                              inspect_avatar(body))
        return f"gfriends:{category}/{filename}"

    def work(self, asset_id: int, code: str, colour) -> None:
        (self.covers / f"{code}.jpg").write_bytes(self.picture((800, 540), colour))
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,code,size) "
                "VALUES(?,'R:',?,?,'video',?,100)",
                (asset_id, f"R:\\media\\{code}.mp4", f"{code}.mp4", code))
            connection.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source) "
                "VALUES(?,?,'performer','test')", (asset_id, self.person))

    run_followup = CoverFaceFollowupTests.run_followup
    contract = CoverFaceFollowupTests.contract
    provenance = CoverFaceFollowupTests.provenance


class FaceMatchFollowupTests(FaceStubCase):
    """图库同名多张时，按封面人脸认出是她的那几张（ADR-0056）。"""

    def test_several_pictures_of_her_install_the_first_big_enough_in_gallery_order(self):
        """太小的那张认出来也不当胜者；其余照图库先后，第一张认定是她的就装。"""
        self.candidate("0-Hand-Storage", (200, 300), self.HER)
        first = self.candidate("1-S1", (400, 600), self.HER)
        self.candidate("y-Minnano", (800, 1000), self.HER)
        self.work(1, "IPX-001", self.HER)
        self.work(2, "SSIS-002", self.HER)
        summary = self.run_followup()
        self.assertEqual(summary["outcome"], "已装上")
        self.assertEqual((summary["matched"], summary["size"]), (3, "400×600"))
        record = self.provenance()
        self.assertEqual((record["provider"], record["source_kind"]),
                         ("gfriends", "gallery_face_matched"))
        self.assertEqual(f"gfriends:{record['external_id']}", first)
        match = record["face_match"]
        self.assertEqual((match["model"], match["threshold"], match["required"]),
                         ("sface_2021dec", 0.363, 2))
        self.assertEqual(sorted(cover["code"] for cover in match["covers"]),
                         ["IPX-001", "SSIS-002"])
        self.assertTrue(all(cover["score"] >= 0.363 for cover in match["covers"]))
        self.assertEqual((match["candidates"], match["compared"]), (3, 2))

    def test_only_her_pictures_are_eligible_when_someone_else_shares_the_name(self):
        """同名的另一个人那张更大、排得更前，照样落选；检不出脸的那张不算她。"""
        self.candidate("0-Hand-Storage", (900, 1200), self.OTHER)
        self.candidate("3-Prestige", (600, 800), self.BLANK)
        hers = self.candidate("7-S1", (400, 600), self.HER)
        self.work(1, "IPX-001", self.HER)
        self.work(2, "SSIS-002", self.HER)
        self.assertEqual(self.run_followup()["outcome"], "已装上")
        record = self.provenance()
        self.assertEqual(f"gfriends:{record['external_id']}", hers)
        self.assertEqual(record["face_match"]["compared"], 2)

    def test_one_cover_is_enough_when_it_is_all_she_has(self):
        self.candidate("1-S1", (400, 600), self.OTHER)
        hers = self.candidate("2-Ideapocket", (400, 600), self.HER)
        self.work(1, "IPX-001", self.HER)
        self.run_followup()
        record = self.provenance()
        self.assertEqual(f"gfriends:{record['external_id']}", hers)
        self.assertEqual(record["face_match"]["required"], 1)

    def test_two_gallery_pictures_and_one_cover_agreeing_are_enough(self):
        """一张参照截错了人：她在两家的两张不同照片对上另一张参照、彼此也对上，就是她（ADR-0057）。"""
        self.candidate("1-S1", (400, 600), self.OTHER)
        hers = self.candidate("2-Ideapocket", (400, 600), self.HER)
        again = self.candidate("3-Moodyz", (400, 600), self.HER_AGAIN)
        self.work(1, "IPX-001", self.HER)
        self.work(2, "SSIS-002", self.STRANGER)
        self.assertEqual(self.run_followup()["outcome"], "已装上")
        record = self.provenance()
        self.assertEqual(f"gfriends:{record['external_id']}", hers)
        self.assertEqual(record["face_match"]["required"], 2)
        self.assertEqual(record["face_match"]["corroborated_by"], {"ref": again, "score": 0.8})

    def test_one_gallery_picture_matching_one_of_two_covers_is_not_enough(self):
        self.candidate("1-S1", (400, 600), self.OTHER)
        self.candidate("2-Ideapocket", (400, 600), self.HER)
        self.work(1, "IPX-001", self.HER)
        self.work(2, "SSIS-002", self.STRANGER)
        self.run_followup()
        self.assertEqual(self.provenance()["provider"], "cover-face")

    def test_the_same_photo_stored_twice_is_one_piece_of_evidence(self):
        """同一张照片在两个目录各存一份（放大、重压过），不算两份互证。"""
        self.candidate("2-Ideapocket", (400, 600), self.HER)
        self.candidate("x-DAS", (800, 1200), self.HER)
        self.work(1, "IPX-001", self.HER)
        self.work(2, "SSIS-002", self.STRANGER)
        self.run_followup()
        self.assertEqual(self.provenance()["provider"], "cover-face")

    def test_two_directories_agreeing_stand_in_for_a_missing_cover(self):
        """没有单人封面：两家目录里她的两张不同照片彼此认得，就装图库先后靠前的那张（ADR-0062）。"""
        hers = self.candidate("1-S1", (400, 600), self.HER)
        again = self.candidate("3-Moodyz", (400, 600), self.HER_AGAIN)
        summary = self.run_followup()
        self.assertEqual((summary["outcome"], summary["source"]), ("已装上", "S1（按图库互证认定）"))
        record = self.provenance()
        self.assertEqual((record["provider"], record["source_kind"]),
                         ("gfriends", "gallery_face_matched"))
        self.assertEqual(f"gfriends:{record['external_id']}", hers)
        match = record["face_match"]
        self.assertEqual((match["covers"], match["required"], match["agreeing"], match["faces"]),
                         ([], 0, 2, 2))
        self.assertEqual(match["corroborated_by"], {"ref": again, "score": 0.8})

    def test_independent_gallery_majority_can_identify_her_with_an_unmatched_cover(self):
        hers = self.candidate("1-S1", (400, 600), self.HER)
        again = self.candidate("3-Moodyz", (400, 600), self.HER_AGAIN)
        self.work(1, "IPX-001", self.STRANGER)
        summary = self.run_followup()
        self.assertEqual(summary["source"], "S1（按图库互证认定）")
        record = self.provenance()
        self.assertEqual(f"gfriends:{record['external_id']}", hers)
        match = record["face_match"]
        self.assertEqual((match["covers"], match["required"], match["agreeing"], match["faces"]),
                         ([], 0, 2, 2))
        self.assertEqual(match["corroborated_by"], {"ref": again, "score": 0.8})
        rejected = match["unmatched_covers"]
        self.assertEqual(rejected[0]["code"], "IPX-001")
        self.assertEqual(set(rejected[0]["scores"]), {hers, again})
        self.assertTrue(all(score < 0.363 for score in rejected[0]["scores"].values()))

    def test_independent_gallery_minority_cannot_override_an_unmatched_cover(self):
        self.candidate("1-S1", (400, 600), self.HER)
        self.candidate("2-Ideapocket", (400, 600), self.HER_AGAIN)
        self.candidate("3-Moodyz", (400, 600), self.OTHER)
        self.candidate("4-Fitch", (400, 600), self.STRANGER)
        self.work(1, "IPX-001", self.BLANK)
        self.run_followup()
        self.assertNotEqual(self.provenance().get("source_kind"), "gallery_face_matched")

    def test_independent_profile_pictures_are_eligible_with_an_unmatched_cover(self):
        self.candidate("1-S1", (350, 350), self.HER)
        self.candidate("3-Moodyz", (350, 350), self.HER_AGAIN)
        self.work(1, "IPX-001", self.STRANGER)
        self.run_followup()
        record = self.provenance()
        self.assertEqual(record["source_kind"], "gallery_small")
        self.assertEqual(record["face_match"]["required"], 0)

    def test_the_same_photo_in_two_directories_does_not_stand_in_for_a_cover(self):
        self.candidate("1-S1", (400, 600), self.HER)
        self.candidate("2-Ideapocket", (400, 600), self.HER)
        summary = self.run_followup()
        self.assertEqual(summary["outcome"], "图库 2 张彼此认不出同一个人，封面上没有能截的脸")
        self.assertFalse((self.avatars / f"performer-{self.person}.img").exists())

    def test_a_pair_among_strangers_is_a_minority_and_installs_nothing(self):
        """单名命中的一堆人里偶然有某一位的两张：二对四不过半，不装。"""
        self.candidate("1-S1", (400, 600), self.HER)
        self.candidate("2-Ideapocket", (400, 600), self.OTHER)
        self.candidate("3-Moodyz", (400, 600), self.STRANGER)
        self.candidate("4-Fitch", (400, 600), self.HER_AGAIN)
        summary = self.run_followup()
        self.assertEqual(summary["outcome"], "图库 4 张彼此认不出同一个人，封面上没有能截的脸")
        self.assertFalse((self.avatars / f"performer-{self.person}.img").exists())

    def test_one_picture_alone_is_still_not_enough_without_a_cover(self):
        self.candidate("1-S1", (400, 600), self.HER)
        self.candidate("2-Ideapocket", (400, 600), self.BLANK)
        summary = self.run_followup()
        self.assertEqual(summary["outcome"], "图库 2 张认不准，封面上没有能截的脸")
        self.assertFalse((self.avatars / f"performer-{self.person}.img").exists())

    def test_no_match_falls_back_to_the_cover_face(self):
        """图库候选既未匹配封面、也不满足独立互证时，从封面截脸。"""
        self.candidate("1-S1", (400, 600), self.OTHER)
        self.candidate("2-Ideapocket", (400, 600), self.OTHER)
        self.work(1, "IPX-001", self.HER)
        summary = self.run_followup()
        self.assertEqual((summary["outcome"], summary["source"]),
                         ("已装上", "作品封面 IPX-001"))
        self.assertEqual(self.provenance()["provider"], "cover-face")

    def test_without_the_model_she_falls_back_and_is_tried_again_next_round(self):
        from peach.followups import Attempts, attempts_root

        self.unavailable = "缺少人脸比对模型"
        self.candidate("1-S1", (400, 600), self.HER)
        self.candidate("2-Ideapocket", (400, 600), self.HER)
        self.work(1, "IPX-001", self.HER)
        summary = self.run_followup()
        self.assertEqual(self.provenance()["provider"], "cover-face")
        self.assertTrue(summary["face_match_unavailable"])
        key = followup_key("performer", self.person)
        with self.database.read_connection() as connection:
            from peach.avatar_followup import fingerprint
            current = fingerprint(connection, self.person)
        self.assertFalse(Attempts(attempts_root(self.root / "generated")).settled(key, current))

    def test_a_new_alias_changes_the_fingerprint_and_queues_her_again(self):
        from peach.avatar_followup import fingerprint, stock
        from peach.followups import Attempts, attempts_root

        self.work(1, "IPX-001", self.BLANK)
        attempts = Attempts(attempts_root(self.root / "generated"))

        def planned():
            with self.database.read_connection() as connection:
                return [item.key for item in stock(connection, self.avatars, attempts,
                                                   limit=10)]

        with self.database.read_connection() as connection:
            self.assertEqual(fingerprint(connection, self.person), "1:0:0:r9")
        self.run_followup()
        self.assertEqual(planned(), [])
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "INSERT INTO entity_alias(entity_id,alias,normalized_alias,source)"
                " VALUES(?,?,?,'test')", (self.person, "りな", "りな"))
        with self.database.read_connection() as connection:
            self.assertEqual(fingerprint(connection, self.person), "1:1:0:r9")
        self.assertEqual(planned(), [followup_key("performer", self.person)])

    def test_a_small_picture_vouches_for_a_big_one_without_a_cover(self):
        """没有单人封面：大图只有自己一张，另一家目录里她的小图作证，装大图（ADR-0066）。"""
        big = self.candidate("0-Hand-Storage", (832, 1249), self.HER)
        small = self.candidate("y-Minnano", (399, 393), self.HER_AGAIN)
        summary = self.run_followup()
        self.assertEqual((summary["outcome"], summary["source"]),
                         ("已装上", "Hand-Storage（按图库互证认定）"))
        record = self.provenance()
        self.assertEqual(f"gfriends:{record['external_id']}", big)
        self.assertEqual(record["face_match"]["corroborated_by"], {"ref": small, "score": 0.8})

    def test_only_small_pictures_install_the_biggest_agreeing_one(self):
        """图库只有小图、封面截不出脸：两张彼此认得，装像素大的那张，标成小图兜底。"""
        self.candidate("y-AVDC", (199, 299), self.HER)
        bigger = self.candidate("y-Minnano", (470, 470), self.HER_AGAIN)
        summary = self.run_followup()
        self.assertEqual((summary["outcome"], summary["size"]), ("已装上", "470×470"))
        record = self.provenance()
        self.assertEqual(record["source_kind"], "gallery_small")
        self.assertEqual(f"gfriends:{record['external_id']}", bigger)
        self.assertTrue(self.run_followup()["outcome"].endswith("已装着认得准的小图"))

    def test_small_pictures_of_different_people_install_nothing(self):
        self.candidate("y-AVDC", (199, 299), self.HER)
        self.candidate("y-Minnano", (470, 470), self.OTHER)
        self.run_followup()
        self.assertFalse((self.avatars / f"performer-{self.person}.img").exists())

    def test_a_350_pixel_profile_is_preferred_to_a_cover_face(self):
        ref = self.candidate("y-Minnano", (350, 350), self.HER)
        self.work(1, "IPX-001", self.HER)
        self.assertEqual(self.run_followup()["size"], "350×350")
        self.assertEqual(f"gfriends:{self.provenance()['external_id']}", ref)
        self.assertEqual(self.run_followup()["outcome"], "已装着认得准的小图")

    def test_a_profile_can_replace_an_automatic_cover_crop(self):
        self.work(1, "IPX-001", self.HER)
        self.run_followup()
        self.assertEqual(self.provenance()["provider"], "cover-face")
        self.candidate("y-Minnano", (350, 350), self.HER)
        self.run_followup()
        self.assertEqual(self.provenance()["provider"], "gfriends")

    def test_profile_priority_requires_identity_matching_for_shared_names(self):
        ref = self.candidate("y-AVDC", (350, 350), self.HER)
        self.candidate("y-Minnano", (450, 450), self.OTHER)
        self.work(1, "IPX-001", self.HER)
        self.run_followup()
        self.assertEqual(f"gfriends:{self.provenance()['external_id']}", ref)
        self.assertEqual(self.provenance()["name_source"], "face-match")

    def test_a_manual_crop_is_preserved_with_a_profile_candidate(self):
        from peach import avatar_picker
        body = self.picture((204, 204), self.HER_AGAIN)
        avatar_picker.install(self.providers, self.avatars, "performer", self.person, body,
                              {"provider": "gfriends", "source_kind": "user_cropped"})
        self.candidate("y-Minnano", (350, 350), self.HER)
        self.assertEqual(self.run_followup()["outcome"], "已有头像")
        self.assertEqual((self.avatars / f"performer-{self.person}.img").read_bytes(), body)

    def test_an_automatic_official_thumbnail_can_use_a_verified_gallery_portrait(self):
        from peach import avatar_picker
        body = self.picture((125, 125), self.HER_AGAIN)
        avatar_picker.install(self.providers, self.avatars, "performer", self.person, body,
                              {"provider": "r18dev", "source_kind": "official_profile"})
        self.candidate("1-S1", (400, 600), self.HER)
        self.candidate("3-Moodyz", (400, 600), self.HER_AGAIN)
        self.assertEqual(self.run_followup()["size"], "400×600")
        self.assertEqual(self.provenance()["source_kind"], "gallery_face_matched")

    def test_an_official_thumbnail_without_a_verified_upgrade_is_preserved(self):
        from peach import avatar_picker
        body = self.picture((125, 125), self.HER_AGAIN)
        avatar_picker.install(self.providers, self.avatars, "performer", self.person, body,
                              {"provider": "r18dev", "source_kind": "official_profile"})
        self.candidate("1-S1", (400, 600), self.HER)
        self.candidate("3-Moodyz", (400, 600), self.OTHER)
        self.run_followup()
        self.assertEqual((self.avatars / f"performer-{self.person}.img").read_bytes(), body)

    def test_a_manually_selected_official_thumbnail_is_preserved(self):
        from peach import avatar_picker
        body = self.picture((125, 125), self.HER_AGAIN)
        avatar_picker.install(self.providers, self.avatars, "performer", self.person, body,
                              {"provider": "r18dev", "source_kind": "official_profile",
                               "user_selected": True})
        self.candidate("1-S1", (400, 600), self.HER)
        self.candidate("3-Moodyz", (400, 600), self.HER_AGAIN)
        self.assertEqual(self.run_followup()["outcome"], "已有头像")
        self.assertEqual((self.avatars / f"performer-{self.person}.img").read_bytes(), body)

    def test_a_manually_selected_cover_face_is_not_queued_or_replaced(self):
        from peach import avatar_picker
        from peach.avatar_followup import needs_avatar
        body = self.picture((125, 125), self.HER_AGAIN)
        avatar_picker.install(self.providers, self.avatars, "performer", self.person, body,
                              {"provider": "cover-face", "source_kind": "cover_face_crop",
                               "face_px": 50, "user_selected": True})
        self.candidate("1-S1", (400, 600), self.HER)
        self.candidate("3-Moodyz", (400, 600), self.HER_AGAIN)
        self.assertFalse(needs_avatar(self.avatars, "performer", self.person))
        self.assertEqual(self.run_followup()["outcome"], "已有头像")
        self.assertEqual((self.avatars / f"performer-{self.person}.img").read_bytes(), body)

    def test_a_profile_smaller_than_300_pixels_remains_a_cover_fallback(self):
        self.candidate("y-Minnano", (299, 350), self.HER)
        self.work(1, "IPX-001", self.HER)
        self.run_followup()
        self.assertEqual(self.provenance()["provider"], "cover-face")

    def test_a_cover_face_replaces_a_small_picture_later(self):
        from peach.avatar_followup import stock
        from peach.followups import Attempts, attempts_root

        self.candidate("y-AVDC", (199, 299), self.HER)
        self.run_followup()
        self.assertEqual(self.provenance()["source_kind"], "gallery_small")
        self.work(1, "IPX-001", self.HER)
        with self.database.read_connection() as connection:
            planned = stock(connection, self.avatars,
                            Attempts(attempts_root(self.root / "generated")), limit=10)
        self.assertEqual([item.key for item in planned], [followup_key("performer", self.person)])
        self.run_followup()
        self.assertEqual(self.provenance()["provider"], "cover-face")


class OffsiteCoverFaceTests(FaceStubCase):
    """馆里截不出她的脸时，从 avwikidb 列出的馆外单人作品封面上截，两部互证才装（ADR-0074）。"""

    ACTOR = "1025548"

    def setUp(self):
        super().setUp()
        self.works: list[dict] = []
        self.images: dict[str, bytes] = {}
        self.fetched: list[str] = []
        self.pages: list[str] = []
        self.blocked = False
        #: 女优页只列前几部；None 是全列。筛选页总是列全。
        self.actor_page_limit: int | None = None
        test = self

        class Pages:
            def get(self, url):
                from peach.performer_alias_followup import Blocked

                if test.blocked:
                    raise Blocked("avwikidb 在冷却")
                test.pages.append(url)
                singles = sum(len(work["actor"]) == 1 and
                              work["actor"][0]["fanzaAvActressId"] == test.ACTOR
                              for work in test.works)
                if url == f"https://avwikidb.com/actor/{test.ACTOR}/":
                    props = {"movies": test.works[:test.actor_page_limit], "singleCount": singles}
                else:
                    test.assertEqual(url, f"https://avwikidb.com/actor/{test.ACTOR}/works/?filter=single")
                    props = {"movies": test.works}
                data = {"props": {"pageProps": props}}
                return url, ('<script id="__NEXT_DATA__" type="application/json">'
                             f"{json.dumps(data)}</script>")

            def close(self):
                pass

        def transport(request, _timeout, _limit):
            from peach.http import HttpResponse

            test.fetched.append(request.url)
            body = test.images.get(request.url)
            return HttpResponse(200 if body else 404, {}, body or b"", request.url)

        for target, value in (("peach.performer_profile_followup.avwikidb_pages",
                               mock.Mock(return_value=Pages())),
                              ("peach.http.HttpxTransport", mock.Mock(return_value=transport))):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def bind(self) -> None:
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "INSERT INTO entity_external_ref(entity_id,provider,external_kind,external_id)"
                " VALUES(?,'avwikidb','performer',?)", (self.person, self.ACTOR))

    def compilation(self, asset_id: int, code: str) -> None:
        """馆里一部她和别人合演的作品：馆藏封面截脸那一档不取它。"""
        self.work(asset_id, code, self.OTHER)
        other = self.entity("performer", f"共演-{asset_id}")
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "INSERT INTO asset_entity(asset_id,entity_id,role,source) "
                "VALUES(?,?,'performer','test')", (asset_id, other))

    def listed(self, code: str, cid: str, colour, *cast: str) -> None:
        """avwikidb 上列一部作品，封面放在 DMM 的地址上；`cast` 缺省是她一个人。"""
        self.works.append({"adultVideoId": code, "floor": "videoa", "fanzaContentId": cid,
                           "actor": [{"name": "梨奈", "fanzaAvActressId": number}
                                     for number in (cast or (self.ACTOR,))]})
        if colour is not None:
            self.images[f"https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/{cid}/{cid}pl.jpg"] = (
                self.picture((800, 540), colour))

    def test_two_offsite_works_agreeing_install_the_widest_face(self):
        self.bind()
        self.listed("ABF-001", "118abf00001", self.HER)
        self.listed("ABF-002", "118abf00002", self.HER_AGAIN)
        summary = self.run_followup()
        self.assertEqual((summary["outcome"], summary["source"]),
                         ("已装上", "馆外单人作品封面 ABF-001（2 部互证）"))
        record = self.provenance()
        self.assertEqual((record["provider"], record["identity_verified"], record["offsite"]),
                         ("cover-face", False, True))
        self.assertTrue(record["cover_url"].endswith("/118abf00001pl.jpg"))
        self.assertEqual(sorted(record["face_match"]["codes"]), ["ABF-001", "ABF-002"])
        self.assertEqual(record["face_match"]["avwikidb"], self.ACTOR)
        self.assertEqual(self.pages, [f"https://avwikidb.com/actor/{self.ACTOR}/"])

    def test_a_truncated_actor_page_is_completed_from_the_single_works_page(self):
        """女优页只列得下一部、站方却数出两部：去筛选页补齐。"""
        self.bind()
        self.actor_page_limit = 1
        self.listed("ABF-001", "118abf00001", self.HER)
        self.listed("ABF-002", "118abf00002", self.HER_AGAIN)
        self.assertEqual(self.run_followup()["outcome"], "已装上")
        self.assertEqual(len(self.pages), 2)
        self.assertEqual(sorted(self.provenance()["face_match"]["codes"]), ["ABF-001", "ABF-002"])

    def test_one_offsite_face_is_kept_as_a_choice_but_not_installed(self):
        self.bind()
        self.listed("ABF-001", "118abf00001", self.HER)
        self.listed("ABF-002", "118abf00002", None)
        summary = self.run_followup()
        self.assertIn("凑不齐两部互证", summary["outcome"])
        self.assertEqual(summary["kept"], 1)
        self.assertFalse((self.avatars / f"performer-{self.person}.img").exists())

    def test_two_different_people_install_nothing(self):
        self.bind()
        self.listed("ABF-001", "118abf00001", self.HER)
        self.listed("ABF-002", "118abf00002", self.STRANGER)
        self.assertIn("凑不齐两部互证", self.run_followup()["outcome"])
        self.assertFalse((self.avatars / f"performer-{self.person}.img").exists())

    def test_the_same_photo_on_two_covers_is_one_piece_of_evidence(self):
        self.bind()
        self.listed("ABF-001", "118abf00001", self.HER)
        self.listed("ABF-002", "118abf00002", self.HER)
        self.assertIn("凑不齐两部互证", self.run_followup()["outcome"])
        self.assertFalse((self.avatars / f"performer-{self.person}.img").exists())

    def test_works_with_someone_else_and_works_in_the_library_are_not_fetched(self):
        self.bind()
        self.compilation(1, "ABF-001")
        self.listed("ABF-001", "118abf00001", self.HER)
        self.listed("ABF-003", "118abf00003", self.HER, self.ACTOR, "1040000")
        self.listed("ABF-002", "118abf00002", self.HER_AGAIN)
        self.run_followup()
        self.assertEqual([url.rsplit("/", 1)[-1] for url in self.fetched],
                         ["118abf00002pl.jpg"])

    def test_without_an_avwikidb_id_nothing_is_fetched(self):
        self.listed("ABF-001", "118abf00001", self.HER)
        summary = self.run_followup()
        self.assertEqual(summary["outcome"], "图库里没有这个名字，封面上没有能截的脸")
        self.assertEqual(self.fetched, [])

    def test_a_blocked_site_is_tried_again_next_round(self):
        from peach.avatar_followup import fingerprint
        from peach.followups import Attempts, attempts_root

        self.bind()
        self.blocked = True
        summary = self.run_followup()
        self.assertTrue(summary["source_unavailable"])
        self.assertIn("avwikidb 单人作品未取得", summary["outcome"])
        with self.database.read_connection() as connection:
            current = fingerprint(connection, self.person)
        key = followup_key("performer", self.person)
        self.assertFalse(Attempts(attempts_root(self.root / "generated")).settled(key, current))

    def test_binding_an_avwikidb_id_queues_her_again(self):
        from peach.avatar_followup import fingerprint, stock
        from peach.followups import Attempts, attempts_root

        attempts = Attempts(attempts_root(self.root / "generated"))
        key = followup_key("performer", self.person)

        def planned():
            # 合集里的共演者也缺头像，只看她这一条。
            with self.database.read_connection() as connection:
                return [item.key for item in stock(connection, self.avatars, attempts, limit=10)
                        if item.key == key]

        self.compilation(1, "DVAJ-495")
        self.run_followup()
        with self.database.read_connection() as connection:
            self.assertEqual(fingerprint(connection, self.person), "1:0:0:r9")
        self.assertEqual(planned(), [])
        self.bind()
        with self.database.read_connection() as connection:
            self.assertEqual(fingerprint(connection, self.person), "1:0:1:r9")
        self.assertEqual(planned(), [key])


class AccountFaceFollowupTests(FaceStubCase):
    """个人博主从自己独占作品的接触印相上截脸，两部作品互证才装（ADR-0074、ADR-0096）。"""

    def setUp(self):
        super().setUp()
        self.account = self.entity("creator", "博主")
        self.identify(self.account)

    def identify(self, entity_id: int) -> None:
        from peach.entity_classification import write_claim

        with self.database.write_transaction(notify=False) as connection:
            write_claim(connection, entity_id=entity_id, facet="identity", value="person",
                        source="user:identity-review", evidence="本人出镜", status="approved",
                        confidence=1)

    def sheet(self, asset_id: int, colour) -> Path:
        """九格同一个人的接触印相：每格上半截黑、下半截是这个人的颜色。"""
        import io

        from PIL import Image
        sheet = Image.new("RGB", (900, 600))
        cell = Image.open(io.BytesIO(self.picture((300, 200), colour)))
        for index in range(9):
            sheet.paste(cell, (index % 3 * 300, index // 3 * 200))
        path = self.root / "snapshots" / f"{asset_id}.jpg"
        path.parent.mkdir(exist_ok=True)
        sheet.save(path, format="JPEG", quality=95)
        return path

    def post(self, asset_id: int, colour, *, owners: tuple[int, ...] = (),
             snapshot: bool = True) -> None:
        """账号名下一部没有番号的作品；`colour` 为 None 时没有印相。"""
        path = self.sheet(asset_id, colour) if snapshot and colour is not None else None
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,size,snapshot_path) "
                "VALUES(?,'R:',?,?,'video',100,?)",
                (asset_id, f"R:\\media\\{asset_id}.mp4", f"{asset_id}.mp4",
                 str(path) if path else None))
            for entity_id in owners or (self.account,):
                connection.execute(
                    "INSERT INTO asset_entity(asset_id,entity_id,role,source) "
                    "VALUES(?,?,'creator','test')", (asset_id, entity_id))

    def contract(self):
        def snapshot_file(raw):
            path = Path(raw) if raw else None
            return path if path is not None and path.is_file() else None

        return SimpleNamespace(
            avatar_root=self.avatars, candidate_root=self.root / "generated",
            cover_root=self.covers, database=self.database, task_runs=self.store,
            cache_bust=lambda: None, snapshot_file=snapshot_file)

    def run_followup(self) -> dict:
        from peach.avatar_followup import run as avatar_run

        return avatar_run(self.contract(), followup_key("creator", self.account), mock.Mock())

    def provenance(self) -> dict:
        return json.loads((self.avatars / f"creator-{self.account}.img.provenance.json")
                          .read_text(encoding="utf-8"))

    def test_the_key_round_trips_for_an_account(self):
        self.assertEqual(parse_key(followup_key("creator", 9553)), ("creator", 9553))

    def test_two_works_agreeing_install_the_face(self):
        self.post(1, self.HER)
        self.post(2, self.HER_AGAIN)
        summary = self.run_followup()
        self.assertEqual((summary["outcome"], summary["source"]),
                         ("已装上", "作品画面 ASSET-ID-1（2 部互证）"))
        record = self.provenance()
        self.assertEqual((record["provider"], record["identity_verified"], record["sheet_cell"]),
                         ("cover-face", False, 0))
        self.assertEqual(sorted(record["face_match"]["codes"]), ["ASSET-ID-1", "ASSET-ID-2"])

    def test_two_different_people_install_nothing(self):
        """情侣号、多人号：两部作品截出的脸对不上同一个人。"""
        self.post(1, self.HER)
        self.post(2, self.STRANGER)
        summary = self.run_followup()
        self.assertIn("凑不齐两部互证", summary["outcome"])
        self.assertEqual(summary["kept"], 2)
        self.assertFalse((self.avatars / f"creator-{self.account}.img").exists())

    def test_nine_cells_of_one_work_are_one_piece_of_evidence(self):
        self.post(1, self.HER)
        self.assertIn("凑不齐两部互证", self.run_followup()["outcome"])
        self.assertFalse((self.avatars / f"creator-{self.account}.img").exists())

    def test_works_shared_with_another_account_are_not_read(self):
        other = self.entity("creator", "另一个号")
        self.post(1, self.HER)
        self.post(2, self.HER_AGAIN, owners=(self.account, other))
        self.assertIn("凑不齐两部互证", self.run_followup()["outcome"])

    def test_an_account_without_identity_is_neither_planned_nor_recorded(self):
        from peach.avatar_followup import NO_IDENTITY, fingerprint, stock
        from peach.followups import Attempts, attempts_root

        unknown = self.entity("creator", "未核实的号")
        self.post(1, self.HER, owners=(self.account, unknown))
        attempts = Attempts(attempts_root(self.root / "generated"))
        with self.database.read_connection() as connection:
            planned = [item.key for item in plan(connection, self.avatars, since_entity_id=0)]
            stocked = [item.key for item in stock(connection, self.avatars, attempts, limit=10)]
        for keys in (planned, stocked):
            self.assertIn(followup_key("creator", self.account), keys)
            self.assertNotIn(followup_key("creator", unknown), keys)
        from peach.avatar_followup import run as avatar_run

        key = followup_key("creator", unknown)
        self.assertEqual(avatar_run(self.contract(), key, mock.Mock())["outcome"], NO_IDENTITY)
        with self.database.read_connection() as connection:
            current = fingerprint(connection, unknown)
        self.assertFalse(attempts.settled(key, current))

    def test_a_new_contact_sheet_queues_the_account_again(self):
        from peach.avatar_followup import stock
        from peach.followups import Attempts, attempts_root

        attempts = Attempts(attempts_root(self.root / "generated"))
        key = followup_key("creator", self.account)

        def planned():
            with self.database.read_connection() as connection:
                return [item.key for item in stock(connection, self.avatars, attempts, limit=10)]

        self.post(1, self.HER)
        self.post(2, None)
        self.run_followup()
        self.assertEqual(planned(), [])
        path = self.sheet(2, self.HER_AGAIN)
        with self.database.write_transaction(notify=False) as connection:
            connection.execute("UPDATE asset SET snapshot_path=? WHERE id=2", (str(path),))
        self.assertEqual(planned(), [key])
        self.assertEqual(self.run_followup()["outcome"], "已装上")


class ProcessLibraryTests(LedgerTestCase):
    """一整条链走通：刮削登记新女优 → 声明后继 → 派出 → 真的跑完。

    这里不桩 `avatar_followup`：跑的就是补头像那条后继本身。图库索引在临时 Peach 数据目录下是空的，
    封面目录也是空的，于是它两档都给不出图并正常收尾——判不准不装图，本来就是这条后继的判据。
    实体种子换成临时目录里的一份小包（`seed()`）：仓库里的真包会给这位真人补上 avwikidb 编号，
    补头像那条就会真的去站上找单人作品，结论随当天有没有网而变。
    """

    def seed(self) -> Path:
        """一份只给 `涼森れむ` 补一条别名的种子包，替掉仓库里的真包。"""
        pack = self.root / 'seed.json'
        pack.write_text(seed_pack.dump({
            'format': 1, 'version': '2026-09-25', 'source': 'auto:seed', 'counts': {},
            'entities': [{'kind': 'performer', 'name': '涼森れむ', 'refs': [], 'links': [],
                          'aliases': [{'alias': '鈴森れむ', 'source': 'r18:performer'}]}],
        }), encoding='utf-8')
        patcher = mock.patch.object(seed_pack, 'DEFAULT_PACK', pack)
        patcher.start()
        self.addCleanup(patcher.stop)
        return pack

    def sample(self):
        from peach.settings_file import PeachConfig

        media = self.root / 'media'
        media.mkdir()
        (media / 'ABW-358.mp4').write_bytes(b'video')
        (media / 'ABW-358.nfo').write_text(
            '<movie><title>作品</title><sorttitle>ABW-358</sorttitle></movie>',
            encoding='utf-8')
        return media, PeachConfig(self.root, self.root / 'config.toml', present=True,
                                  locations={'local': (str(media),)})

    def provider(self):
        provider = mock.Mock()
        provider.amane.side_effect = NotFound('片商官网没有这个番号')
        provider.community.side_effect = NotFound('社区来源都没有这个番号')
        provider.cover.return_value = False
        provider.query.return_value = {
            'id': 'ABW-358',
            'actresses': [{'dmm_id': 1051912, 'japanese_name': '涼森れむ',
                           'profile_source': 'r18dev'}],
        }
        return provider

    @windows_ledger_roots
    def test_a_scrape_declares_dispatches_and_runs_its_avatar_followups(self):
        _media, config = self.sample()
        seed = seed_pack.load(self.seed())
        with mock.patch('peach.library_processing._generate_previews', return_value=0):
            result = process_library(config, self.db, self.root / 'generated',
                                     self.root / 'covers',
                                     provider_factory=mock.Mock(return_value=self.provider()),
                                     database=self.database)
        self.assertEqual(result['status'], 'complete')
        with self.database.read_connection() as connection:
            entity_id = connection.execute(
                "SELECT id FROM entity WHERE kind='performer'"
                " AND canonical_name='涼森れむ'").fetchone()[0]
        # 一、刮削自己声明了后继：随仓库走的实体种子一条排最前（ADR-0075），再是这位新女优的
        # 补头像、补别名、补女优资料各一条，实体身份就在 key 里。
        from peach import performer_alias_followup, performer_profile_followup
        self.assertEqual([item['key'] for item in result['followups']],
                         [seed_followup.followup_key(seed['version'], seed_validation.digest(seed)),
                          followup_key('performer', entity_id),
                          performer_alias_followup.followup_key(entity_id),
                          performer_profile_followup.followup_key(entity_id)])

        # 二、结算这一轮时派出去，一件事排成父任务下的一行。
        parent = self.parent()
        queued = self.store.enqueue_followups(
            parent.id, [(item['key'], item['task_key'], item['label'])
                        for item in result['followups']])['queued']
        self.assertEqual(len(queued), 4)

        # 三、真的被跑掉，收在终态。补别名、补女优资料的各站都换成取不到页的替身，不联网。
        class Offline:
            def get(self, _url):
                raise performer_alias_followup.Unavailable('测试里不联网')

            def close(self):
                pass

        contract = SimpleNamespace(
            task_runs=self.store, database=self.database,
            avatar_root=self.root / 'generated' / 'avatars',
            candidate_root=self.root / 'generated', cover_root=self.root / 'covers',
            cache_bust=lambda: None)
        offline = {performer_alias_followup.MINNANO: Offline(),
                   performer_alias_followup.AV_NEME: Offline()}
        profile_offline = {performer_profile_followup.MINNANO: Offline(),
                           performer_profile_followup.AVWIKIDB: Offline(),
                           performer_profile_followup.JAVDB: Offline()}
        with mock.patch.object(performer_alias_followup, 'open_sites', return_value=offline), \
                mock.patch.object(performer_profile_followup, 'open_sites',
                                  return_value=profile_offline):
            self.assertEqual(FollowupRunner(contract).drain(), 4)
        seeded = self.store.get(queued[0])
        self.assertEqual((seeded.status, seeded.result_summary['outcome']), ('succeeded', '别名 1'))
        self.assertTrue((self.root / 'generated' / seed_followup.REVIEW_FILE).exists(),
                        '种子导入跑完留下复核产物，哪怕这一轮没有可看的项')
        done = self.store.get(queued[1])
        self.assertEqual(done.status, 'succeeded')
        self.assertEqual(done.result_summary['outcome'], '图库里没有这个名字，封面上没有能截的脸')
        self.assertEqual(self.store.get(queued[2]).result_summary['outcome'], '未取得')
        self.assertEqual(self.store.get(queued[3]).status, 'succeeded')
        self.assertFalse((self.root / 'generated' / 'avatars'
                          / f'performer-{entity_id}.img').exists())


class TaskPayloadTests(LedgerTestCase):
    def test_the_detail_endpoint_gives_both_ends_of_the_chain(self):
        from peach.web_tasks import q_task

        parent = self.parent()
        queued = self.store.enqueue_followups(
            parent.id, [("entity-avatar:performer:1", AVATAR_TASK_KEY, "甲")])["queued"]
        contract = SimpleNamespace(task_runs=self.store)
        payload = q_task(contract, parent.id)
        self.assertIsNone(payload["parent"])
        self.assertEqual([row["id"] for row in payload["followups"]], queued)
        self.assertEqual(payload["followup_counts"], {"pending": 1})
        child = q_task(contract, queued[0])
        self.assertEqual(child["parent"]["id"], parent.id)
        self.assertEqual(child["run"]["followup_key"], "entity-avatar:performer:1")
        self.assertEqual(child["run"]["followup_depth"], 1)
        # 契约是 JSON，派生字段不能带出 Path、set 这类东西。
        json.dumps(payload)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
