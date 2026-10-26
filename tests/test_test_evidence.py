from __future__ import annotations

import json
import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import agent_worktree as coordinator
from scripts import test_evidence as evidence
from scripts import test_runner as runner
from scripts import test_progress
from support.gitrepo import seed_repository




class ParallelShardTests(unittest.TestCase):
    def test_requested_parallelism_has_no_machine_slot_ceiling(self):
        live, peak = set(), [0]
        class Shard:
            def __init__(self, command):
                report = Path(command[command.index('--timings') + 1])
                report.write_text(json.dumps({'success': True, 'count': 1, 'timings': []}), encoding='utf-8')
                self.polls, self.returncode = 0, None
                live.add(self)
                peak[0] = max(peak[0], len(live))
            def poll(self):
                self.polls += 1
                if self.polls >= 2:
                    live.discard(self)
                    self.returncode = 0
                return self.returncode
        def spawn(command, **kwargs):
            return Shard(command)
        with redirect_stdout(io.StringIO()):
            passed, count, _ = runner.run_shards(('checks',), jobs=6, shard_count=12, spawn=spawn)
        self.assertTrue(passed)
        self.assertEqual(count, 12)
        self.assertEqual(peak[0], 6)


class VerificationTests(unittest.TestCase):
    def setUp(self):
        # 这组用例模拟本机验收，运行它的 CI job 身份不属于临时仓库。
        self.enterContext(mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = seed_repository(self.root / "repo", {
            ".gitignore": "build/\n",
            "README.md": "内容\n",
            "src/peach/__init__.py": '__version__ = "0.7.30"\n',
        }, "seed")

    def git(self, *args):
        return evidence.git(self.repo, *args)

    def worker(self):
        item = coordinator.create(self.repo, "codex", "verification", self.root / "worktrees")
        worker = Path(item["path"])
        (worker / "README.md").write_text("测试内容\n", encoding="utf-8")
        (worker / "README.en.md").write_text("Test content\n", encoding="utf-8")
        evidence.git(worker, "add", "README.md", "README.en.md")
        evidence.git(worker, "commit", "-m", "docs: test", "-m",
                     "README-Impact: updated; 测试文档\n"
                     "Co-Authored-By: Codex (GPT-5.5) <noreply@openai.com>")
        return worker, item["branch"]

    def certify(self, worker, scopes=("checks", "tooling"), success=True):
        state = evidence.key(worker)
        evidence.write(worker, state, scopes, success=success, elapsed=1, slowest=[], count=1)
        return state

    def test_gate_requires_matching_successful_coverage(self):
        worker, branch = self.worker()
        before = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(coordinator.WorkspaceError, "有效测试记录"):
            coordinator.integrate(self.repo, branch)
        self.certify(worker, ("web",))
        with self.assertRaisesRegex(coordinator.WorkspaceError, "有效测试记录"):
            coordinator.ready(worker)
        self.certify(worker, success=False)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.certify(worker)
        coordinator.ready(worker)
        result = coordinator.integrate(self.repo, branch)
        self.assertTrue(result["ok"])
        entries = coordinator._worktree_entries(self.repo)
        self.assertFalse(next(e for e in entries if e.get("branch") == branch).get("locked"))

    def test_target_advance_requires_combined_tree_verification(self):
        worker, branch = self.worker()
        self.certify(worker)
        (self.repo / "another.md").write_text("并行任务\n", encoding="utf-8")
        self.git("add", "another.md")
        self.git("commit", "-m", "docs: parallel")
        with self.assertRaisesRegex(coordinator.WorkspaceError, "已前进"):
            coordinator.integrate(self.repo, branch)

    def test_target_change_during_verification_prevents_merge(self):
        worker, branch = self.worker()
        self.certify(worker)
        verify = coordinator.require_verified
        def advance(*args):
            verify(*args)
            (self.repo / "parallel.md").write_text("并发改动\n", encoding="utf-8")
            self.git("add", "parallel.md")
            self.git("commit", "-m", "docs: concurrent")
        with mock.patch.object(coordinator, "require_verified", side_effect=advance):
            with self.assertRaisesRegex(coordinator.WorkspaceError, "集成工作树改变"):
                coordinator.integrate(self.repo, branch)
        self.assertEqual((self.repo / "README.md").read_text(encoding="utf-8"), "内容\n")

    def test_scope_refresh_preserves_expiry_of_other_scopes(self):
        state = evidence.key(self.repo)
        with mock.patch.object(evidence.time, "time", return_value=100):
            self.certify(self.repo, ("full",))
        with mock.patch.object(evidence.time, "time", return_value=86450):
            self.certify(self.repo, ("checks",))
        with mock.patch.object(evidence.time, "time", return_value=86550):
            record = evidence.read(self.repo, state)
            self.assertTrue(evidence.covers(record, ("checks",)))
            self.assertFalse(evidence.covers(record, ("web",)))

    def test_snapshot_tracks_untracked_deleted_and_modified_content(self):
        baseline = evidence.snapshot(self.repo)
        path = self.repo / "extra.md"
        path.write_text("未跟踪\n", encoding="utf-8")
        self.assertNotEqual(baseline, evidence.snapshot(self.repo))
        path.unlink()
        self.assertEqual(baseline, evidence.snapshot(self.repo))
        (self.repo / "README.md").unlink()
        self.assertNotEqual(baseline, evidence.snapshot(self.repo))

    def test_git_execute_mode_changes_require_full_even_without_content_changes(self):
        original = evidence.manifest(self.repo)
        self.git("update-index", "--chmod=+x", "README.md")
        current = evidence.manifest(self.repo)
        paths, kinds = runner.change_impact.delta(original, current)
        self.assertEqual(kinds["README.md"], "unsafe")
        self.assertEqual(runner.scopes_for_changes(paths, kinds=kinds)[0], ("full",))

    def test_known_file_addition_and_deletion_follow_their_domain(self):
        name = "src/peach/web_follow.py"
        path = self.repo / name
        path.write_text("value = 1\n", encoding="utf-8")
        self.git("add", "--", name)
        contents = runner.changed_contents(self.repo, "HEAD", [name])
        self.assertEqual(runner.scopes_for_changes([name], contents=contents)[0], ("follow",))
        self.git("commit", "-m", "test: fixture")
        path.unlink()
        contents = runner.changed_contents(self.repo, "HEAD", [name])
        self.assertEqual(runner.scopes_for_changes([name], contents=contents)[0], ("follow",))

    def test_known_python_file_execute_mode_requires_full(self):
        name = "src/peach/web_follow.py"
        (self.repo / name).write_text("value = 1\n", encoding="utf-8")
        self.git("add", "--", name)
        self.git("commit", "-m", "test: fixture")
        self.git("update-index", "--chmod=+x", name)
        contents = runner.changed_contents(self.repo, "HEAD", [name])
        self.assertEqual(runner.scopes_for_changes([name], contents=contents)[0], ("full",))

    def test_python_symlinks_require_full_for_each_change_kind(self):
        name = "src/peach/web_follow.py"
        for modes in (":000000 120000", ":120000 120000", ":120000 000000"):
            with self.subTest(modes=modes), mock.patch.object(evidence, "git", return_value=modes):
                contents = runner.changed_contents(self.repo, "HEAD", [name])
            self.assertEqual(runner.scopes_for_changes([name], contents=contents)[0], ("full",))

    def test_repeated_import_path_preserves_dependency_identity(self):
        site = self.root / "site"
        metadata = site / "peach_evidence_probe-1.0.dist-info" / "METADATA"
        metadata.parent.mkdir(parents=True)
        metadata.write_text("Metadata-Version: 2.1\nName: peach-evidence-probe\nVersion: 1.0\n")
        with mock.patch.object(evidence, "python_identity", return_value={
                "version": sys.version, "sites": [str(site), str(site)]}):
            baseline = evidence.environment(self.repo)
            with mock.patch.object(sys, "path", [str(site), *sys.path]):
                self.assertEqual(evidence.environment(self.repo), baseline)
            metadata.write_text("Metadata-Version: 2.1\nName: peach-evidence-probe\nVersion: 2.0\n")
            self.assertNotEqual(evidence.environment(self.repo), baseline)

    def test_the_same_tool_reached_by_another_path_keeps_one_identity(self):
        """工具身份是它自报的版本，不是 PATH 解析到的那条路径。

        记录因此不再绑在 shell 上。同一套 Git 安装，PowerShell 解析到 `Git\\cmd\\git.exe`、
        Git Bash 解析到 `Git\\mingw64\\bin\\git.exe`，两个前端字节不同、`git --version`
        相同。把路径和字节记进指纹的话，在一个 shell 里跑出的记录到另一个 shell 里就查
        不到：`integrate` 报「缺少有效测试记录」，回到工作树跑 `auto` 又说「复用记录」，
        两句查的是两个键，代价是白跑一遍全量。
        """
        here = Path(sys.executable)
        detour = here.parent / ".." / here.parent.name / here.name
        self.assertNotEqual(str(detour), str(here))
        probe = ("--version",)
        self.assertEqual(evidence.tool_identity(str(here), here.stat().st_mtime_ns, probe),
                         evidence.tool_identity(str(detour), 0, probe))
        with mock.patch.object(evidence, "tool_identity", return_value="v1") as probed:
            with mock.patch.object(evidence.shutil, "which", side_effect=lambda name: f"/a/{name}"):
                baseline = evidence.environment(self.repo)
            probed.reset_mock()
            with mock.patch.object(evidence.shutil, "which", side_effect=lambda name: f"/b/{name}"):
                self.assertEqual(evidence.environment(self.repo), baseline)
            self.assertEqual({call.args[0] for call in probed.call_args_list},
                             {f"/b/{name}" for name, _ in evidence.TOOL_PROBES},
                             "每个探针都要被问到，不能有工具悄悄不进指纹")
        with mock.patch.object(evidence.shutil, "which", side_effect=lambda name: f"/a/{name}"):
            with mock.patch.object(evidence, "tool_identity", return_value="v2"):
                self.assertNotEqual(evidence.environment(self.repo), baseline)
        with mock.patch.object(evidence.shutil, "which", return_value=None):
            self.assertNotEqual(evidence.environment(self.repo), baseline)

    def test_a_newer_uv_keeps_the_environment_fingerprint_stable(self):
        """uv 只负责建环境，它自己的版本不进指纹。

        winget 会自动升级 uv，而它装出来的解释器与包已经逐个进了指纹。把 uv 的版本也
        记进去的话，升级当天全部测试记录一起失效，被验证的那套环境却一个字节都没变，
        代价是每台机器重跑一遍全量。
        """
        def probe_as(uv: str, others: str):
            return mock.patch.object(
                evidence, "tool_identity",
                side_effect=lambda executable, stamp, probe:
                uv if executable.endswith("uv") else others)

        with mock.patch.object(evidence.shutil, "which", side_effect=lambda name: f"/a/{name}"):
            with probe_as("uv-1", "ok"):
                baseline = evidence.environment(self.repo)
            with probe_as("uv-2", "ok"):
                self.assertEqual(evidence.environment(self.repo), baseline)
            with probe_as("uv-1", "changed"):
                self.assertNotEqual(evidence.environment(self.repo), baseline)

    def test_an_unspawnable_tool_is_not_the_same_as_a_missing_one(self):
        """探针跑不起来和工具不在 PATH 上是两种环境，指纹要能分开。"""
        absent = self.root / "no-such-tool"
        self.assertEqual(evidence.tool_identity(str(absent), 0, ("--version",)), "unspawnable")
        with mock.patch.object(evidence.shutil, "which", side_effect=lambda name: str(absent)):
            broken = evidence.environment(self.repo)
        with mock.patch.object(evidence.shutil, "which", return_value=None):
            self.assertNotEqual(evidence.environment(self.repo), broken)

    def test_unspawnable_tools_names_every_resolved_tool_blocked_by_permissions(self):
        """入口需要工具名来给出一条明确结论，不能等测试分片各自抛 CreateProcess。"""
        with mock.patch.object(evidence.shutil, "which", side_effect=lambda name: f"/tools/{name}"), \
                mock.patch.object(evidence, "tool_identity",
                                  side_effect=lambda executable, stamp, probe:
                                  "unspawnable" if executable.endswith(("uv", "git")) else "ok"), \
                mock.patch.object(evidence.Path, "stat", return_value=mock.Mock(st_mtime_ns=1)):
            self.assertEqual(evidence.unspawnable_tools(), ("uv", "git"))

    def test_commit_metadata_preserves_content_record(self):
        worker, _ = self.worker()
        state = self.certify(worker)
        evidence.git(worker, "commit", "--amend", "-m", "docs: wording")
        self.assertEqual(evidence.key(worker), state)
        self.assertTrue(evidence.covers(evidence.read(worker, state), ("checks",)))
        (worker / "README.md").write_text("不同内容\n", encoding="utf-8")
        self.assertNotEqual(evidence.key(worker), state)

    def test_environment_change_expiry_and_failure_invalidate_records(self):
        state = self.certify(self.repo, ("full",))
        with mock.patch.object(evidence, "environment", return_value="different"):
            self.assertNotEqual(evidence.key(self.repo), state)
        with mock.patch.object(evidence.time, "time", return_value=10**12):
            self.assertFalse(evidence.covers(evidence.read(self.repo, state), ("web",)))
        self.certify(self.repo, ("web",), success=False)
        self.assertFalse(evidence.covers(evidence.read(self.repo, state), ("web",)))

    def test_cross_process_integration_lock_refuses_mutation(self):
        _, branch = self.worker()
        folder = evidence.evidence_dir(self.repo)
        folder.mkdir(parents=True)
        before = self.git("rev-parse", "HEAD")
        command = [sys.executable, str(Path(coordinator.__file__)), "--repo", str(self.repo),
                   "integrate", "--branch", branch]
        with evidence.held(folder / "integration.lock", branch="agent/x/other"):
            result = subprocess.run(command, capture_output=True, text=True,
                                    encoding="utf-8", timeout=15,
                                    env={**os.environ, "PYTHONIOENCODING": "gbk"})
        self.assertEqual(result.returncode, 2, result.stderr)
        error = json.loads(result.stdout)["error"]
        self.assertIn("另一任务正在集成", error)
        self.assertIn(f"pid {os.getpid()}", error, "被拒的一方要能看到在等谁")
        self.assertIn("branch agent/x/other", error)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)

    def test_a_held_lock_leaves_a_holder_note_only_while_it_is_held(self):
        """三个会话同时在等锁时，光看进程列表判断不出谁在测；记录要说清 pid、时间和范围。"""
        lock = evidence.evidence_dir(self.repo) / "full-suite.lock"
        with evidence.held(lock, scope="full"):
            note = json.loads(evidence.holder_path(lock).read_text(encoding="utf-8"))
            self.assertEqual(note["pid"], os.getpid())
            self.assertEqual(note["scope"], "full")
            self.assertIn("pid", evidence.describe_holder(lock))
            self.assertIn("scope full", evidence.describe_holder(lock))
        self.assertFalse(evidence.holder_path(lock).exists())
        self.assertIn("刚退出或没留记录", evidence.describe_holder(lock))

    def test_holder_note_cleanup_waits_for_a_reader_to_close_the_file(self):
        lock = evidence.evidence_dir(self.repo) / "full-suite.lock"
        note = evidence.holder_path(lock)
        owner_ready = threading.Event()
        reader_open = threading.Event()
        owner_close = threading.Event()
        owner_exiting = threading.Event()
        reader_close = threading.Event()
        original = Path.read_text

        def read_note(path, *args, **kwargs):
            if path != note:
                return original(path, *args, **kwargs)
            with path.open(encoding="utf-8") as handle:
                reader_open.set()
                if not reader_close.wait(5):
                    raise TimeoutError("测试读者没有收到关闭信号")
                return handle.read()

        def own():
            with evidence.held(lock, scope="full"):
                owner_ready.set()
                if not owner_close.wait(5):
                    raise TimeoutError("测试持有者没有收到退出信号")
                owner_exiting.set()

        with ThreadPoolExecutor(max_workers=2) as pool, mock.patch.object(Path, "read_text", new=read_note):
            owner = pool.submit(own)
            try:
                self.assertTrue(owner_ready.wait(5))
                reader = pool.submit(evidence.describe_holder, lock)
                self.assertTrue(reader_open.wait(5))
                owner_close.set()
                self.assertTrue(owner_exiting.wait(5))
                with self.assertRaises(TimeoutError):
                    owner.result(timeout=0.1)
                self.assertTrue(note.exists())
            finally:
                owner_close.set()
                reader_close.set()
            owner.result(timeout=5)
            self.assertIn("scope full", reader.result(timeout=5))
        self.assertFalse(note.exists())


    def test_the_waiting_runner_names_the_holder_of_the_full_suite_lock(self):
        lock = evidence.evidence_dir(self.repo) / "full-suite.lock"
        output = io.StringIO()
        with evidence.held(lock, scope="full", root="elsewhere"), \
                redirect_stderr(io.StringIO()), redirect_stdout(output), \
                mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "build_suite", side_effect=AssertionError("不该开跑")):
            self.assertEqual(runner.main(["--scope", "full", "--lock-timeout", "0"]), 2)
        self.assertIn("本仓库全量测试正在运行", output.getvalue())
        self.assertIn(f"pid {os.getpid()}", output.getvalue())
        self.assertIn("root elsewhere", output.getvalue())

    def test_full_verification_waits_and_snapshots_after_the_holder_releases(self):
        lock = evidence.evidence_dir(self.repo) / "full-suite.lock"
        waiting = threading.Event()
        describe = evidence.describe_holder

        def describe_wait(path):
            waiting.set()
            return describe(path)

        output, errors = io.StringIO(), io.StringIO()
        with redirect_stderr(errors), redirect_stdout(output), \
                mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(evidence, "describe_holder", side_effect=describe_wait), \
                mock.patch.object(runner, "environment_preflight"), \
                mock.patch.object(runner, "run_local_suite", return_value=(True, 1, [])) as run, \
                ThreadPoolExecutor(max_workers=1) as pool:
            with evidence.held(lock, scope="integrate"):
                future = pool.submit(runner.main, ["--scope", "full", "--lock-timeout", "5"])
                self.assertTrue(waiting.wait(5), "验证应等待持锁的集成")
                run.assert_not_called()
                (self.repo / "README.md").write_text("集成完成\n", encoding="utf-8")
            self.assertEqual(future.result(timeout=15), 0)
        self.assertIn("等待 full-suite.lock", errors.getvalue())
        self.assertNotIn("等待 full-suite.lock", output.getvalue())
        self.assertTrue(evidence.covers(evidence.read(self.repo, evidence.key(self.repo)), ("full",)))

    def test_full_verification_holds_the_lock_through_record_publication(self):
        before = self.git("rev-parse", "HEAD")
        publish = evidence.write

        def publish_locked(*args, **kwargs):
            with self.assertRaisesRegex(coordinator.WorkspaceError, "全量验证"):
                coordinator.integrate(self.repo, "unused", lock_timeout=0)
            return publish(*args, **kwargs)

        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()), \
                mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(evidence, "write", side_effect=publish_locked) as written, \
                mock.patch.object(runner, "run_local_suite", return_value=(True, 1, [])):
            self.assertEqual(runner.main(["--scope", "full"]), 0)
        written.assert_called_once()
        self.assertEqual(self.git("rev-parse", "HEAD"), before)

    def test_integration_excludes_full_verification(self):
        def merge(*_):
            self.assertEqual(runner.main(["--scope", "full", "--lock-timeout", "0"]), 2)
            return {"ok": True}

        with redirect_stdout(io.StringIO()), mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "run_local_suite") as run, \
                mock.patch.object(coordinator, "_integrate_locked", side_effect=merge):
            self.assertTrue(coordinator.integrate(self.repo, "unused")["ok"])
        run.assert_not_called()

    def test_integration_waits_for_full_verification_before_mutation(self):
        lock = evidence.evidence_dir(self.repo) / "full-suite.lock"
        waiting = threading.Event()
        describe = evidence.describe_holder

        def describe_wait(path):
            waiting.set()
            return describe(path)

        with redirect_stdout(io.StringIO()), \
                mock.patch.object(evidence, "describe_holder", side_effect=describe_wait), \
                mock.patch.object(coordinator, "_integrate_locked", return_value={"ok": True}) as merge, \
                ThreadPoolExecutor(max_workers=1) as pool:
            with evidence.held(lock, scope="full"):
                future = pool.submit(coordinator.integrate, self.repo, "unused", lock_timeout=5)
                self.assertTrue(waiting.wait(5), "集成应等待全量记录写入完成")
                merge.assert_not_called()
            self.assertTrue(future.result(timeout=15)["ok"])
        merge.assert_called_once()

    def test_empty_active_worktree_survives_prune(self):
        item = coordinator.create(self.repo, "codex", "active", self.root / "worktrees")
        report = coordinator.prune(self.repo, apply=True)
        self.assertTrue(Path(item["path"]).is_dir())
        self.assertTrue(any(row["path"] == item["path"] for row in report["kept"]))

    def test_auto_uses_scope_union_and_full_for_shared_inputs(self):
        self.assertEqual(runner.scopes_for_changes([])[0], ("checks",))
        self.assertEqual(runner.scopes_for_changes(["docs/HANDOFF.md"])[0], ("checks",))
        self.assertEqual(runner.scopes_for_changes(["web/app.js", "src/peach/follow.py"])[0],
                         ("follow", "web"))
        for path in ("pyproject.toml", "scripts/test_runner.py", "scripts/test_evidence.py",
                     "uv.lock", "src/peach/unknown.py"):
            self.assertEqual(runner.scopes_for_changes([path])[0], ("full",))

    def test_runner_reuses_success_and_fresh_failure_invalidates_it(self):
        def suite(*_):
            return unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()), \
                mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "build_suite", side_effect=suite) as build:
            self.assertEqual(runner.main(["--scope", "checks"]), 0)
            self.assertEqual(runner.main(["--scope", "checks"]), 0)
            self.assertEqual(build.call_count, 1)
            def failing(*_):
                return unittest.TestSuite([unittest.FunctionTestCase(lambda: self.fail("fixture"))])
            build.side_effect = failing
            self.assertEqual(runner.main(["--scope", "checks", "--fresh"]), 1)
            self.assertFalse(evidence.covers(evidence.read(self.repo, evidence.key(self.repo)), ("checks",)))

    def test_a_parallel_run_signs_one_record_from_the_merged_shards(self):
        """并行时父进程不自己跑用例，只按分片汇总签一份记录；一片红就没有记录。"""
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()), \
                mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "build_suite", side_effect=AssertionError("父进程不该自己跑")), \
                mock.patch.object(runner, "run_shards", return_value=(True, 3, [(0.5, "x"), (0.1, "y")])) as shards:
            self.assertEqual(runner.main(["--scope", "checks", "--jobs", "2"]), 0)
        shards.assert_called_once()
        self.assertEqual(shards.call_args.args, (("checks",),))
        self.assertEqual(shards.call_args.kwargs["jobs"], 2)
        self.assertGreater(shards.call_args.kwargs["shard_count"], 1)
        record = evidence.read(self.repo, evidence.key(self.repo))
        self.assertTrue(evidence.covers(record, ("checks",)))
        self.assertEqual(record["count"], 3)
        self.assertEqual(record["slowest"][0], [0.5, "x"])
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()), \
                mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "run_shards", return_value=(False, 3, [])):
            self.assertEqual(runner.main(["--scope", "checks", "--jobs", "2", "--fresh"]), 1)
        self.assertFalse(evidence.covers(evidence.read(self.repo, evidence.key(self.repo)), ("checks",)))

    def test_serial_full_run_releases_memory_between_shards(self):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()), \
                mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "build_suite", side_effect=AssertionError("父进程不加载全量用例")), \
                mock.patch.object(runner, "run_shards", return_value=(True, 7, [])) as shards:
            self.assertEqual(runner.main(["--scope", "full", "--jobs", "1"]), 0)
        shards.assert_called_once()
        self.assertEqual(shards.call_args.kwargs["jobs"], 1)
        self.assertEqual(shards.call_args.kwargs["shard_count"], 4)
        record = evidence.read(self.repo, evidence.key(self.repo))
        self.assertTrue(evidence.covers(record, ("full",)))
        self.assertEqual(record["count"], 7)

    def test_full_baseline_requires_only_new_scopes_without_extending_its_age(self):
        original = self.certify(self.repo, ("full",))
        stamp = evidence.read(self.repo, original)["validated"]["full"]
        (self.repo / "README.md").write_text("集成差异\n", encoding="utf-8")
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()), \
                mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "resolve_auto_scope", return_value=(("full",), "fixture")), \
                mock.patch.object(runner, "build_suite", return_value=unittest.TestSuite([
                    unittest.FunctionTestCase(lambda: None)])) as build:
            self.assertEqual(runner.main(["--scope", "auto"]), 0)
        build.assert_called_once_with("checks", "tooling")
        record = evidence.read(self.repo, evidence.key(self.repo))
        self.assertEqual(record["baseline"], original)
        self.assertEqual(record["validated"]["full"], stamp)
        self.assertTrue(evidence.covers(record, ("full",)))

    def test_full_baseline_cannot_cover_shared_or_unknown_changes(self):
        self.certify(self.repo, ("full",))
        (self.repo / "pyproject.toml").write_text("# shared\n")
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()), \
                mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "resolve_auto_scope", return_value=(("full",), "fixture")), \
                mock.patch.object(runner, "run_local_suite", return_value=(True, 1, [])) as run:
            self.assertEqual(runner.main(["--scope", "auto"]), 0)
        run.assert_called_once_with(("full",), 1)

    def test_baseline_does_not_expand_a_small_requested_scope(self):
        self.certify(self.repo, ("full",))
        (self.repo / "src/peach/__init__.py").write_text('__version__ = "0.7.31"\n')
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()), \
                mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "resolve_auto_scope", return_value=(("checks",), "fixture")), \
                mock.patch.object(runner, "build_suite", return_value=unittest.TestSuite([
                    unittest.FunctionTestCase(lambda: None)])) as build:
            self.assertEqual(runner.main(["--scope", "auto"]), 0)
        build.assert_called_once_with("checks")

    def test_baseline_distinguishes_version_assignment_and_package_logic(self):
        self.certify(self.repo, ("full",))
        version = self.repo / "src/peach/__init__.py"
        version.write_text('__version__ = "0.7.31"\n')
        candidates = list(evidence.baselines(self.repo, evidence.inputs(self.repo)))
        self.assertTrue(candidates[0][2])
        version.write_text('__version__ = "0.7.31"\nflag = True\n')
        candidates = list(evidence.baselines(self.repo, evidence.inputs(self.repo)))
        self.assertFalse(candidates[0][2])
        self.assertIn("src/peach/__init__.py", candidates[0][1])

    def test_holder_notes_never_crowd_the_full_baseline_out_of_the_scan(self):
        self.certify(self.repo, ("full",))
        folder = evidence.evidence_dir(self.repo)
        for index in range(40):
            (folder / f"{index:064x}.lock.holder.json").write_text("{}", encoding="utf-8")
        candidates = list(evidence.baselines(self.repo, evidence.inputs(self.repo)))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][1], [])

    def test_a_record_deleted_while_the_folder_is_scanned_is_skipped(self):
        self.certify(self.repo, ("full",))
        gone = evidence.evidence_dir(self.repo) / f"{'0' * 64}.json"
        gone.write_text("{}", encoding="utf-8")
        stat = Path.stat

        def vanished(path, *args, **kwargs):
            if path.name == gone.name:
                raise FileNotFoundError(path)
            return stat(path, *args, **kwargs)

        with mock.patch.object(Path, "stat", vanished):
            candidates = list(evidence.baselines(self.repo, evidence.inputs(self.repo)))
        self.assertEqual(len(candidates), 1)

    def test_runner_rejects_changes_during_verification(self):
        def mutate():
            (self.repo / "README.md").write_text("测试期间改动\n", encoding="utf-8")
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()), \
                mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "build_suite", return_value=unittest.TestSuite([
                    unittest.FunctionTestCase(mutate)])):
            self.assertEqual(runner.main(["--scope", "checks"]), 4)
        self.assertFalse(evidence.covers(evidence.read(self.repo, evidence.key(self.repo)), ("checks",)))

    def test_full_run_retains_unaffected_files_then_completes_with_auto(self):
        mapping = {"full": ("test_checks.py", "test_web.py"), "checks": ("test_checks.py",),
                   "web": ("test_web.py",)}
        select = lambda scope: tuple(Path(name) for name in mapping.get(scope, ()))

        def mutate(*args):
            (self.repo / "README.md").write_text("检查期间的文档修订\n", encoding="utf-8")
            return True, 2, []

        with redirect_stdout(io.StringIO()), mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "selected_files", side_effect=select), \
                mock.patch.object(runner, "environment_preflight"), \
                mock.patch.object(runner, "run_local_suite", side_effect=mutate):
            self.assertEqual(runner.main(["--scope", "full"]), 4)
        current = evidence.key(self.repo)
        record = evidence.read(self.repo, current)
        self.assertEqual(set(record["coverage"]), {"test_web.py"})
        self.assertFalse(evidence.covers(record, ("full",)))
        stamp = record["coverage"]["test_web.py"]
        with redirect_stdout(io.StringIO()), mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "selected_files", side_effect=select), \
                mock.patch.object(runner, "resolve_auto_scope", return_value=(("full",), "fixture")), \
                mock.patch.object(runner, "environment_preflight"), \
                mock.patch.object(runner, "run_local_suite", return_value=(True, 1, [])) as run:
            self.assertEqual(runner.main(["--scope", "auto"]), 0)
        run.assert_called_once_with(("checks",), 1)
        record = evidence.read(self.repo, current)
        self.assertTrue(evidence.covers(record, ("full",)))
        self.assertEqual(record["validated"]["full"], stamp)

    def test_shared_logic_and_environment_changes_discard_inflight_coverage(self):
        context = evidence.inputs(self.repo)
        source = {**context, "cohort": context["state"], "coverage": {"test_web.py": 100}}
        select = lambda scope: (Path("test_web.py"),)
        changed = self.repo / "tests/support/shared.py"
        changed.parent.mkdir(parents=True)
        changed.write_text("value = 1\n", encoding="utf-8")
        self.assertEqual(test_progress.retained(source, evidence.inputs(self.repo), runner.scopes_for_changes, select), {})
        changed.unlink()
        other = {**context, "environment": "different"}
        self.assertEqual(test_progress.retained(source, other, runner.scopes_for_changes, select), {})
        self.assertEqual(test_progress.retained({**source, "cohort": None}, context, runner.scopes_for_changes, select), {})

    def test_content_signatures_preserve_narrow_comment_and_metadata_deltas(self):
        path = self.repo / "tests/support/shared.py"
        path.parent.mkdir(parents=True)
        path.write_text("value = 1\n", encoding="utf-8")
        original = evidence.inputs(self.repo)
        path.write_text("# 说明\nvalue = 1\n", encoding="utf-8")
        _, kinds = runner.change_impact.delta(original["manifest"], evidence.inputs(self.repo)["manifest"])
        self.assertEqual(kinds["tests/support/shared.py"], "prose")
        self.assertEqual(runner.scopes_for_changes(kinds, kinds=kinds)[0], ("checks",))
        original = evidence.manifest(self.repo)
        (self.repo / "src/peach/__init__.py").write_text('__version__ = "0.7.31"\n', encoding="utf-8")
        paths, kinds = runner.change_impact.delta(original, evidence.manifest(self.repo))
        self.assertEqual(runner.scopes_for_changes(paths, kinds=kinds)[0], ("tooling",))

    def test_expired_file_results_cannot_complete_full_coverage(self):
        state = evidence.key(self.repo)
        with mock.patch.object(evidence.time, "time", return_value=100):
            evidence.write(self.repo, state, (), success=False, elapsed=0, slowest=[], count=1,
                           cohort=state, coverage={"test_web.py": 100})
        with mock.patch.object(evidence.time, "time", return_value=86501):
            self.assertEqual(evidence.read(self.repo, state)["coverage"], {})

    def test_pending_coverage_chooses_the_smallest_file_union(self):
        mapping = {"full": ("a", "b", "c", "d"), "catalog": ("a", "b", "d"),
                   "media": ("b", "c", "d"), "web": ("a", "b", "c")}
        select = lambda scope: tuple(Path(name) for name in mapping[scope])
        self.assertEqual(test_progress.pending({"d": 100}, select, ("catalog", "media", "web")), ("web",))

    def test_partial_baseline_preserves_unaffected_scopes_and_their_age(self):
        original = self.certify(self.repo, ("catalog", "metadata", "web"))
        stamps = evidence.read(self.repo, original)["validated"]
        changed = self.repo / "frontend/src/react/example.tsx"
        changed.parent.mkdir(parents=True)
        changed.write_text("export const value = 1;\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()), mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "environment_preflight"), \
                mock.patch.object(runner, "resolve_auto_scope", return_value=(("catalog", "metadata", "web"), "fixture")), \
                mock.patch.object(runner, "run_local_suite", return_value=(True, 1, [])) as run:
            self.assertEqual(runner.main(["--scope", "auto"]), 0)
        run.assert_called_once_with(("web",), 1)
        record = evidence.read(self.repo, evidence.key(self.repo))
        self.assertTrue(evidence.covers(record, ("catalog", "metadata", "web")))
        self.assertNotIn("full", record["validated"])
        self.assertEqual(record["validated"]["catalog"], stamps["catalog"])
        self.assertEqual(record["validated"]["metadata"], stamps["metadata"])

    def test_partial_baseline_cannot_cover_shared_changes_or_full_validation(self):
        self.certify(self.repo, ("catalog", "web"))
        changed = self.repo / "scripts/test_runner.py"
        changed.parent.mkdir(parents=True)
        changed.write_text("# fixture\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()), mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "environment_preflight"), \
                mock.patch.object(runner, "resolve_auto_scope", return_value=(("full",), "fixture")), \
                mock.patch.object(runner, "run_local_suite", return_value=(True, 1, [])) as run:
            self.assertEqual(runner.main(["--scope", "auto"]), 0)
        run.assert_called_once_with(("full",), 1)

    def test_same_state_only_runs_missing_scopes_and_failed_state_runs_all(self):
        self.certify(self.repo, ("catalog",))
        with redirect_stdout(io.StringIO()), mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "environment_preflight"), \
                mock.patch.object(runner, "resolve_auto_scope", return_value=(("catalog", "web"), "fixture")), \
                mock.patch.object(runner, "run_local_suite", return_value=(True, 1, [])) as run:
            self.assertEqual(runner.main(["--scope", "auto"]), 0)
            run.assert_called_once_with(("web",), 1)
            run.reset_mock()
            self.certify(self.repo, ("full",), success=False)
            self.assertEqual(runner.main(["--scope", "auto"]), 0)
            run.assert_called_once_with(("catalog", "web"), 1)

    def test_partial_baseline_requires_live_timestamps_and_matching_environment(self):
        original = self.certify(self.repo, ("catalog",))
        path = evidence.evidence_dir(self.repo) / f"{original}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        for changes in ({"validated": {"catalog": 0}}, {"environment": "another"}):
            path.write_text(json.dumps({**record, **changes}), encoding="utf-8")
            self.assertEqual(list(evidence.baselines(self.repo, evidence.inputs(self.repo), partial=True)), [])

    def test_failed_domain_preserves_other_domains_and_requires_its_own_revalidation(self):
        self.certify(self.repo, ("catalog", "metadata", "web"))
        state = evidence.key(self.repo)
        stamps = evidence.read(self.repo, state)["validated"]
        self.certify(self.repo, ("web",), success=False)
        failed = evidence.read(self.repo, state)
        self.assertTrue(evidence.covers(failed, ("catalog", "metadata")))
        self.assertFalse(evidence.covers(failed, ("web",)))
        with redirect_stdout(io.StringIO()), mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "environment_preflight"), \
                mock.patch.object(runner, "resolve_auto_scope", return_value=(("catalog", "metadata", "web"), "fixture")), \
                mock.patch.object(runner, "run_local_suite", return_value=(True, 1, [])) as run:
            self.assertEqual(runner.main(["--scope", "auto"]), 0)
        run.assert_called_once_with(("web",), 1)
        self.assertEqual(evidence.read(self.repo, state)["validated"]["catalog"], stamps["catalog"])

    def test_success_of_another_domain_cannot_restore_a_failed_domain_from_an_older_baseline(self):
        self.certify(self.repo, ("full",))
        (self.repo / "README.md").write_text("fixture difference\n", encoding="utf-8")
        self.certify(self.repo, ("catalog", "web"))
        self.certify(self.repo, ("web",), success=False)
        self.certify(self.repo, ("checks",))
        record = evidence.read(self.repo, evidence.key(self.repo))
        self.assertEqual(record["failed_scopes"], ["web"])
        with redirect_stdout(io.StringIO()), mock.patch.object(runner, "ROOT", self.repo), \
                mock.patch.object(runner, "environment_preflight"), \
                mock.patch.object(runner, "resolve_auto_scope", return_value=(("checks", "catalog", "web"), "fixture")), \
                mock.patch.object(runner, "run_local_suite", return_value=(True, 1, [])) as run:
            self.assertEqual(runner.main(["--scope", "auto"]), 0)
        run.assert_called_once_with(("web",), 1)

    def test_focus_never_publishes_evidence_and_formal_run_rejects_focus_environment(self):
        selector = self.repo / "tests/test_focus_sample.py"
        selector.parent.mkdir(parents=True)
        selector.write_text("import unittest\nclass Sample(unittest.TestCase):\n def test_value(self): self.assertEqual(1,1)\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), \
                mock.patch.object(runner, "ROOT", self.repo), mock.patch.object(evidence, "write") as write:
            self.assertEqual(runner.main(["--focus", "test_focus_sample"]), 0)
            write.assert_not_called()
            with mock.patch.dict(os.environ, {"PEACH_E2E_FILES": "[]"}):
                with self.assertRaises(SystemExit) as stopped:
                    runner.main(["--scope", "checks"])
            self.assertEqual(stopped.exception.code, 2)

    def test_focus_rejects_missing_files_and_path_escape(self):
        from scripts import test_focus
        for selector in ("test_missing", "frontend/e2e/../../other.test.ts", "scripts/test_runner.py"):
            with self.subTest(selector=selector), self.assertRaises(ValueError):
                test_focus.selection(self.repo, [selector])



if __name__ == "__main__":
    unittest.main()
