import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts import agent_worktree, release_tag
from scripts.agent_worktree import (
    WorkspaceError, _git, _lines, create, integrate, processes_under, prune, ready,
)
from scripts.version_bump import read_version
from support.gitrepo import seed_repository

#: 仓库里版本化的 git hook，测试原样装进临时仓库。
HOOKS = Path(__file__).resolve().parents[1] / agent_worktree.HOOKS_PATH
AUTHOR = "Co-Authored-By: Codex (GPT-6) <noreply@openai.com>"


def install_commit_hooks(repo: Path) -> None:
    hooks = repo / agent_worktree.HOOKS_PATH
    hooks.mkdir(parents=True)
    sources = list(HOOKS.iterdir())
    sources += [HOOKS.parent / name for name in
                ("check_commit_message.py", "check_readme_impact.py", "co_author.py")
                if (HOOKS.parent / name).is_file()]
    for source in sources:
        relative = source.relative_to(HOOKS.parent.parent)
        target = repo / relative
        shutil.copyfile(source, target)
        target.chmod(0o755)
        _git(repo, "add", relative.as_posix())
    _git(repo, "commit", "-m", "hooks")

#: 版本号的唯一来源，`src/peach/__init__.py` 在测试仓库里的最小复刻。
VERSION_SEED = b'"""Peach application package."""\n\n__version__ = "0.7.14"\n'


class StuckRemoval:
    """复刻 2026-09-01 Windows 上的实测：目录句柄被别的进程占着，
    `git worktree remove` 报 Permission denied。

    `residue`：git 已经把文件删光、注册也摘掉了，只剩一个空目录删不掉。
    `stuck`：注册还在，工作树整个没摘掉。
    """

    def __init__(self, real, modes: dict[Path, str]):
        self.real = real
        self.modes = {path.resolve(): mode for path, mode in modes.items()}

    def __call__(self, repo, *args, **kwargs):
        mode = None
        if args[:2] == ("worktree", "remove"):
            mode = self.modes.get(Path(args[-1]).resolve())
        if mode is None:
            return self.real(repo, *args, **kwargs)
        path = Path(args[-1])
        if mode == "residue":
            self.real(repo, *args, check=False)
            path.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(
            list(args), 1, "", f"fatal: failed to delete '{path}': Permission denied")


def commit(repo: Path, message: str) -> None:
    _git(repo, "add", "tracked.txt", "worker.txt")
    _git(repo, "commit", "-m", message)


class _WorktreeCase(unittest.TestCase):
    def setUp(self):
        self.verification = mock.patch.object(agent_worktree, "require_verified")
        self.verification.start()
        self.addCleanup(self.verification.stop)
        self.tmp = tempfile.TemporaryDirectory()
        # 先 resolve：prune 报告的是 git worktree list 给出的真实路径，而 CI runner 的
        # 临时目录是别名（macOS /var 软链到 /private/var，Windows 的 RUNNER~1 短名展开
        # 成 runneradmin）。拿未 resolve 的路径去比对，本机全绿、CI 全红。
        self.root = Path(self.tmp.name).resolve()
        self.repo = seed_repository(self.root / "repo", {
            ".gitignore": "build/\n",
            "tracked.txt": "base\n",
            "worker.txt": "base\n",
            "src/peach/__init__.py": VERSION_SEED,
        }, "base")

    def tearDown(self):
        self.tmp.cleanup()

class AgentWorktreeTests(_WorktreeCase):
    def test_create_ready_and_integrate_non_overlapping_branch(self):
        result = create(self.repo, "Claude", "metadata batch", self.root / "worktrees")
        worker = Path(result["path"])
        (worker / "worker.txt").write_text("worker\n", encoding="utf-8")
        commit(worker, "worker change")
        report = ready(worker)
        self.assertEqual(report["same_file_changes"], [])
        merged = integrate(self.repo, str(result["branch"]))
        self.assertIn("worker.txt", merged["files"])
        self.assertEqual((self.repo / "worker.txt").read_text(encoding="utf-8"), "worker\n")

    def test_ready_rejects_dirty_worker(self):
        result = create(self.repo, "Codex", "dirty task", self.root / "worktrees")
        worker = Path(result["path"])
        (worker / "worker.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceError, "dirty"):
            ready(worker)

    def test_integrate_refuses_same_file_changed_on_both_branches(self):
        result = create(self.repo, "Claude", "overlap", self.root / "worktrees")
        worker = Path(result["path"])
        (worker / "tracked.txt").write_text("worker\n", encoding="utf-8")
        commit(worker, "worker overlap")
        (self.repo / "tracked.txt").write_text("main\n", encoding="utf-8")
        commit(self.repo, "main overlap")
        with self.assertRaisesRegex(WorkspaceError, "same-file"):
            integrate(self.repo, str(result["branch"]))


class MasterCommitGuardTests(_WorktreeCase):
    """主检出的 master 只收 `integrate` 与 `release_tag.py` 的提交。

    2026-09 进 master 的五个红提交，四个是在主检出上直接提交，一个是手工 merge，
    都没经过 ready / integrate 的检查。这里用仓库里那份 hook 原样装进测试仓库。
    """

    def setUp(self):
        super().setUp()
        install_commit_hooks(self.repo)
        environment = mock.patch.dict(os.environ, {"PATH": str(Path(sys.executable).parent)
                                                  + os.pathsep + os.environ.get("PATH", "")})
        environment.start()
        self.addCleanup(environment.stop)
        result = create(self.repo, "Claude", "guarded", self.root / "worktrees")
        self.worker, self.branch = Path(result["path"]), str(result["branch"])
        (self.worker / "worker.txt").write_text("worker\n", encoding="utf-8")
        commit(self.worker, "test: worker change\n\n" + AUTHOR)

    def test_create_points_the_repository_at_the_versioned_hooks(self):
        self.assertEqual(_git(self.repo, "config", "core.hooksPath").stdout.strip(),
                         agent_worktree.HOOKS_PATH)

    def test_a_direct_commit_on_master_in_the_main_checkout_is_refused(self):
        before = _git(self.repo, "rev-parse", "HEAD").stdout
        (self.repo / "tracked.txt").write_text("main\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceError, "主检出只做集成"):
            commit(self.repo, "direct")
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD").stdout, before)

    def test_a_hand_made_merge_into_master_is_refused(self):
        before = _git(self.repo, "rev-parse", "HEAD").stdout
        with self.assertRaisesRegex(WorkspaceError, "主检出只做集成"):
            _git(self.repo, "merge", "--no-ff", "-m", "merge by hand", self.branch)
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD").stdout, before)

    def test_integrate_still_merges_the_worker_branch(self):
        self.assertEqual(_git(self.worker, "status", "--porcelain").stdout, "")
        integrate(self.repo, self.branch)
        self.assertEqual((self.repo / "worker.txt").read_text(encoding="utf-8"), "worker\n")

    def test_the_release_commit_carries_the_pass(self):
        """`release_tag.py` 发出的实际参数由 `test_scripts.ShipTests` 钉住。"""
        (self.repo / "tracked.txt").write_text("release\n", encoding="utf-8")
        _git(self.repo, "add", "tracked.txt")
        _git(self.repo, "-c", f"{release_tag.MASTER_WRITER}=release", "commit", "-m", "release")
        self.assertEqual(_git(self.repo, "log", "-1", "--format=%s").stdout.strip(), "release")


class CommitMessageGuardTests(_WorktreeCase):
    def setUp(self):
        super().setUp()
        install_commit_hooks(self.repo)
        environment = mock.patch.dict(os.environ, {"PATH": str(Path(sys.executable).parent)
                                                  + os.pathsep + os.environ.get("PATH", "")})
        environment.start()
        self.addCleanup(environment.stop)
        result = create(self.repo, "codex", "message", self.root / "worktrees with spaces")
        self.worker = Path(result["path"])
        (self.worker / "worker.txt").write_text("worker\n", encoding="utf-8")
        _git(self.worker, "add", "worker.txt")

    def attempt(self, message):
        path = self.worker / "message.txt"
        path.write_text(message, encoding="utf-8")
        return _git(self.worker, "commit", "-F", str(path), check=False)

    def test_a_missing_or_invalid_author_refuses_the_commit_and_keeps_the_index(self):
        before = _git(self.worker, "rev-parse", "HEAD").stdout
        for trailer in ("", "Co-Authored-By: Codex <noreply@openai.com>",
                        "Co-Authored-By: Codex (GPT-6) <wrong@example.com>"):
            with self.subTest(trailer=trailer):
                result = self.attempt("test: check\n\n" + trailer)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("署名" if trailer else "Co-Authored-By", result.stderr)
                self.assertEqual(_git(self.worker, "rev-parse", "HEAD").stdout, before)
                self.assertEqual(_git(self.worker, "diff", "--cached", "--name-only").stdout.strip(),
                                 "worker.txt")

    def test_separated_or_malformed_readme_declarations_refuse_the_commit(self):
        for declaration in ("README-Impact: none; 内部测试\n\n", "README-Impact: none\n",
                            "README-Impact: none; 内部测试\nREADME-Impact: updated; 文档\n"):
            with self.subTest(declaration=declaration):
                result = self.attempt("test: check\n\n" + declaration + AUTHOR)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("README-Impact", result.stderr)

    def test_counterpart_review_trailer_requires_a_file_reason_and_contiguous_block(self):
        for declaration in ("README-Reviewed: README.en.md; 已核对\n\n",
                            "README-Reviewed: README.en.md;\n",
                            "README-Reviewed: unknown.md; 已核对\n"):
            result = self.attempt("test: 核对声明\n\n" + declaration + AUTHOR)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("README-Reviewed", result.stderr)
        result = self.attempt("test: 核对声明\n\nREADME-Reviewed: README.en.md; 英文正确\n" + AUTHOR)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_valid_trailers_and_amend_preserve_the_message(self):
        message = ("test: 中文提交\n\nREADME-Impact: updated; 两种语言\n" + AUTHOR
                   + "\nCo-Authored-By: Claude Code (Opus 5) <noreply@anthropic.com>\n")
        result = self.attempt(message)
        self.assertEqual(result.returncode, 0, result.stderr)
        amended = _git(self.worker, "commit", "--amend", "--no-edit", check=False)
        self.assertEqual(amended.returncode, 0, amended.stderr)
        self.assertEqual(_git(self.worker, "show", "-s", "--format=%B").stdout.strip(), message.strip())

    def test_human_and_agent_trailers_are_preserved_in_the_commit(self):
        message = ("test: 共同作者署名\n\n" + AUTHOR
                   + "\nCo-Authored-By: Contributor <contributor@example.invalid>\n")
        result = self.attempt(message)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_git(self.worker, "show", "-s", "--format=%B").stdout.strip(), message.strip())

    def test_intermediate_commit_can_leave_readme_scope_for_delivery_check(self):
        result = self.attempt("test: intermediate\n\n" + AUTHOR)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_author_lines_separated_into_different_blocks_are_rejected(self):
        result = self.attempt("test: authors\n\n" + AUTHOR + "\n\n"
                              "Co-Authored-By: Claude Code (Opus 5) <noreply@anthropic.com>")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Co-Authored-By", result.stderr)

    def test_git_comment_lines_do_not_count_as_declarations(self):
        result = self.attempt("test: comments\n\n" + AUTHOR + "\n\n# README-Impact: example\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_merge_messages_are_exempt_from_authorship_trailers(self):
        result = self.attempt("test: worker\n\n" + AUTHOR)
        self.assertEqual(result.returncode, 0, result.stderr)
        _git(self.repo, "switch", "-c", "parallel")
        (self.repo / "tracked.txt").write_text("parallel\n", encoding="utf-8")
        _git(self.repo, "add", "tracked.txt")
        _git(self.repo, "commit", "-m", "test: parallel")
        merged = _git(self.worker, "merge", "--no-ff", "-m", "Merge parallel", "parallel", check=False)
        self.assertEqual(merged.returncode, 0, merged.stderr)


if __name__ == "__main__":
    unittest.main()


class PruneTests(_WorktreeCase):
    """回收已经并入 master 的隔离工作树。

    `create` 一直有人用，回收却从来没有入口——2026-08-29 清到 3 个，两天后长回 74 个、
    占 868 MB。一次性清理解决不了这个问题，所以把回收做成命令，并在这里锁住它的边界。
    """

    def _integrated(self, name: str) -> Path:
        result = create(self.repo, "Codex", name, self.root / "worktrees")
        worker = Path(result["path"])
        (worker / "worker.txt").write_text(name + " done\n", encoding="utf-8")
        commit(worker, f"{name} change")
        integrate(self.repo, str(result["branch"]))
        return worker

    def test_reporting_does_not_remove_anything(self):
        """默认只报告。回收不可逆，不该是运行一下就顺手做了的事。"""
        worker = self._integrated("reportable")
        report = prune(self.repo)
        self.assertIn(str(worker), report["reclaimed"])
        self.assertFalse(report["applied"])
        self.assertTrue(worker.is_dir(), "没给 --apply 就不该动手")

    def test_apply_reclaims_the_worktree_and_its_branch(self):
        worker = self._integrated("reclaimable")
        branch = _git(self.repo, "branch", "--list", "agent/codex/reclaimable").stdout
        self.assertIn("reclaimable", branch)
        prune(self.repo, apply=True)
        self.assertFalse(worker.exists(), "工作树应当被回收")
        self.assertNotIn("reclaimable",
                         _git(self.repo, "branch", "--list", "agent/codex/reclaimable").stdout,
                         "已并入的分支也应一并删掉")

    def test_a_dirty_worktree_is_refused_even_when_its_branch_is_merged(self):
        """分支已合入不等于工作区里没东西。

        实测就有这样的工作树：分支早已并入 master，里面却躺着一份成形的未提交改动
        （按钮等待态改用 aria-busy，还配了对应的测试）。所以脏的一律拒收并单独列出，
        交给人看，不给 --force 这个口子。
        """
        worker = self._integrated("has-wip")
        (worker / "worker.txt").write_text("未提交的后续改动\n", encoding="utf-8")
        report = prune(self.repo, apply=True)
        self.assertTrue(worker.is_dir(), "脏工作树不能被回收")
        self.assertEqual([row["path"] for row in report["dirty"]], [str(worker)])
        self.assertEqual(report["reclaimed"], [])

    def test_an_unmerged_branch_is_kept_with_a_reason(self):
        result = create(self.repo, "Codex", "still-open", self.root / "worktrees")
        worker = Path(result["path"])
        (worker / "worker.txt").write_text("still open wip\n", encoding="utf-8")
        commit(worker, "still open")
        report = prune(self.repo, apply=True)
        self.assertTrue(worker.is_dir())
        self.assertEqual([row["branch"] for row in report["kept"]], ["agent/codex/still-open"])

    def test_the_main_worktree_is_never_a_candidate(self):
        self._integrated("bystander")
        report = prune(self.repo)
        listed = report["reclaimed"] + [row["path"] for row in report["dirty"] + report["kept"]]
        self.assertNotIn(str(self.repo), listed, "主检出不能出现在回收清单里")

    def test_a_directory_git_could_not_delete_is_deleted_by_the_script(self):
        """git 摘了注册却删不掉目录时，脚本自己把目录删掉，不留给人。

        2026-09-01 实测：连跑 5 次 prune --apply 才把 5 个工作树摘完，5 条分支一条没删，
        最后是手工 `git branch -d` 收的尾——因为 remove 一失败就抛错中止了整轮。分支的
        那半边当时补上了，目录这半边还写着「待人工清理」，于是残留目录照样留在原地：
        长得和真工作树一样，在里面跑 git 全作用于主检出的 master。
        """
        worker = self._integrated("stuck-handle")
        with mock.patch.object(agent_worktree, "_git",
                               StuckRemoval(agent_worktree._git, {worker: "residue"})):
            report = prune(self.repo, apply=True)
        self.assertTrue(report["ok"], "一个目录删不掉不该把整轮判成失败")
        self.assertFalse(worker.exists(), "git 删不掉的空目录由脚本删掉")
        self.assertEqual(report["residue"], [], "删掉了就不是残留")
        self.assertEqual(report["reclaimed"], [str(worker)])
        self.assertNotIn("stuck-handle",
                         _git(self.repo, "branch", "--list", "agent/codex/stuck-handle").stdout,
                         "注册已经摘掉了，分支就该跟着删")

    def test_a_directory_nobody_can_delete_is_reported_with_the_reason(self):
        """脚本也删不掉才算残留：报出原因交给人，分支照删。"""
        worker = self._integrated("locked-handle")
        with mock.patch.object(agent_worktree, "_git",
                               StuckRemoval(agent_worktree._git, {worker: "residue"})), \
                mock.patch.object(agent_worktree, "_delete_tree",
                                  return_value="PermissionError: 目录被别的进程占着"):
            report = prune(self.repo, apply=True)
        self.assertTrue(worker.is_dir(), "删不掉就还在原地")
        self.assertEqual([row["path"] for row in report["residue"]], [str(worker)])
        self.assertIn("目录被别的进程占着", report["residue"][0]["why"])
        self.assertEqual(report["reclaimed"], [])
        self.assertNotIn("locked-handle",
                         _git(self.repo, "branch", "--list", "agent/codex/locked-handle").stdout)

    def test_one_stuck_worktree_does_not_block_the_others(self):
        """回收失败只波及它自己，剩下的工作树照常处理，不用人反复重跑。"""
        stuck = self._integrated("stuck-first")
        other = self._integrated("second-in-line")
        with mock.patch.object(agent_worktree, "_git",
                               StuckRemoval(agent_worktree._git, {stuck: "stuck"})):
            report = prune(self.repo, apply=True)
        self.assertEqual(report["reclaimed"], [str(other)])
        self.assertFalse(other.exists())
        self.assertNotIn("second-in-line",
                         _git(self.repo, "branch", "--list", "agent/codex/second-in-line").stdout)
        self.assertEqual([row["branch"] for row in report["failed"]], ["agent/codex/stuck-first"])
        self.assertTrue(stuck.is_dir())
        self.assertIn("stuck-first",
                      _git(self.repo, "branch", "--list", "agent/codex/stuck-first").stdout,
                      "注册还在就是没回收成，分支不能删")


class UnregisteredLeftoverTests(_WorktreeCase):
    """清掉两个工作树落点里已经不登记的残留目录。

    `create` 的落点和 Claude Code 内置的落点都会留下这种目录：它不在
    `git worktree list` 里，所以按登记项遍历的那轮回收永远碰不到它；人却会走进去当
    工作树用，而在里面跑 git 全部作用于主检出的 master。两处同一条规则，只盖其中
    一处的话，另一处的残留就只能靠人想起来。
    """

    def roots(self) -> tuple[Path, ...]:
        """`create` 建在主检出旁边，Claude Code 建在主检出里。"""
        return (self.repo.parent / agent_worktree.WORKTREE_ROOT,
                self.repo / agent_worktree.BUILTIN_WORKTREES)

    def leftover(self, root: Path, name: str) -> Path:
        path = root / name
        path.mkdir(parents=True)
        return path

    def test_an_unregistered_empty_leftover_is_swept(self):
        for root in self.roots():
            with self.subTest(root=root.name):
                empty = self.leftover(root, "distracted-lamarr")
                report = prune(self.repo, apply=True)
                self.assertEqual(report["swept"], [str(empty)])
                self.assertFalse(empty.exists())

    def test_reporting_lists_the_leftover_without_deleting_it(self):
        for root in self.roots():
            with self.subTest(root=root.name):
                empty = self.leftover(root, "reportable-leftover")
                report = prune(self.repo)
                self.assertEqual(report["swept"], [str(empty)])
                self.assertTrue(empty.is_dir(), "没给 --apply 就不该动手")
                shutil.rmtree(empty)

    def test_a_leftover_that_still_holds_files_is_left_alone(self):
        """空目录才扫。里面还有文件就可能是别人正开着的检出或没提交的东西。"""
        for root in self.roots():
            with self.subTest(root=root.name):
                used = self.leftover(root, "sweet-newton")
                (used / "worker.txt").write_text("别人正在用\n", encoding="utf-8")
                report = prune(self.repo, apply=True)
                self.assertEqual(report["swept"], [])
                self.assertTrue(used.is_dir())
                self.assertEqual([row["path"] for row in report["kept"]], [str(used)])
                shutil.rmtree(used)

    def test_a_registered_worktree_under_either_root_is_not_swept(self):
        """登记着的工作树归上面那轮按分支状态处理，扫残留这步不许碰。"""
        for index, root in enumerate(self.roots()):
            with self.subTest(root=root.name):
                task = f"in-place-{index}"
                result = create(self.repo, "Claude", task, root)
                worker = Path(result["path"])
                (worker / "worker.txt").write_text("还在做\n", encoding="utf-8")
                commit(worker, f"{task} wip")
                report = prune(self.repo, apply=True)
                self.assertEqual(report["swept"], [])
                self.assertTrue(worker.is_dir())
                self.assertIn(f"agent/claude/{task}",
                              [row["branch"] for row in report["kept"]])


class LeftoverProcessTests(_WorktreeCase):
    """找出工作树落点下还活着的进程：谁占着删不掉的目录、机器变卡时谁是自己的残留。"""

    def sleeper(self, cwd: Path) -> subprocess.Popen:
        cwd.mkdir(parents=True, exist_ok=True)
        # 持有工作目录的是实际解释器，进程探测夹具直接跟踪它的 PID。
        interpreter = getattr(sys, '_base_executable', sys.executable)
        child = subprocess.Popen([interpreter, "-c", "import sys; sys.stdin.read()"],
                                 cwd=cwd, stdin=subprocess.PIPE)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rows = processes_under([cwd])
            if any(row["pid"] == child.pid for row in rows):
                break
            time.sleep(0.1)
        return child

    def stop(self, child: subprocess.Popen) -> None:
        # EOF 让解释器自己退出；Windows venv 启动器也会等其子进程结束。
        child.communicate(timeout=10)

    def test_a_process_working_in_a_worktree_is_listed_with_its_details(self):
        child = self.sleeper(self.repo.parent / agent_worktree.WORKTREE_ROOT / "busy")
        try:
            report = agent_worktree.processes(self.repo)
        finally:
            self.stop(child)
        row = next(row for row in report["processes"] if row["pid"] == child.pid)
        self.assertFalse(row["orphan"], "父进程（测试本身）还在")
        self.assertFalse(row["caller"])
        self.assertGreaterEqual(row["age_minutes"], 0)
        self.assertIn("cpu_percent", row)
        self.assertIn("memory_percent", report["machine"])

    def test_the_caller_and_processes_elsewhere_are_told_apart(self):
        child = self.sleeper(self.root / "elsewhere")
        try:
            rows = processes_under([self.root / "elsewhere", Path(os.getcwd())])
        finally:
            self.stop(child)
        self.assertIn(child.pid, [row["pid"] for row in rows])
        self.assertNotIn(child.pid, [row["pid"] for row in processes_under([self.repo])])
        mine = [row for row in rows if row["pid"] == os.getpid()]
        self.assertEqual([row["caller"] for row in mine], [True], "调用者自己这条链要标出来")

    def test_a_directory_nobody_can_delete_names_the_process_holding_it(self):
        held = self.repo / agent_worktree.BUILTIN_WORKTREES / "held-open"
        child = self.sleeper(held)
        try:
            with mock.patch.object(agent_worktree, "_delete_tree",
                                   return_value="PermissionError: 目录被别的进程占着"):
                report = prune(self.repo, apply=True)
        finally:
            self.stop(child)
        self.assertEqual([row["path"] for row in report["residue"]], [str(held)])
        self.assertIn(child.pid, [row["pid"] for row in report["residue"][0]["holders"]])

    def test_without_psutil_the_scan_says_it_was_not_obtained(self):
        with mock.patch.dict(sys.modules, {"psutil": None}):
            self.assertTrue(processes_under([self.repo]).startswith("未取得"))
            report = create(self.repo, "Codex", "no-psutil", self.root / "worktrees")
        self.assertTrue(report["orphan_processes"].startswith("未取得"))


class IntegrationVersionTests(_WorktreeCase):
    """集成报告版本号，但把推进它的权力留给发布入口。

    一天几十次集成共用一个版本号：跑着的是哪一份由 commit 认定，`build-info.json`
    随包走，托盘按它算自己落后多少个提交。版本号一次发布推一格，于是每个 `X.Y.Z`
    都对应一份能下载到的制品（ADR-0012）。
    """

    def worker_touching(self, name: str, path: str, text: str = "改了\n", *,
                        subject: str | None = None) -> str:
        result = create(self.repo, "Claude", name, self.root / "worktrees")
        worker = Path(result["path"])
        target = worker / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        _git(worker, "add", path)
        _git(worker, "commit", "-m", subject or f"fix: {name}")
        return str(result["branch"])

    def test_a_runtime_fix_merges_without_touching_the_version(self):
        branch = self.worker_touching("runtime-change", "src/peach/module.py")
        report = integrate(self.repo, branch)
        self.assertEqual(report["version"], "0.7.14")
        self.assertEqual(read_version(self.repo), "0.7.14")
        self.assertEqual(_git(self.repo, "log", "-1", "--format=%s").stdout.strip(),
                         f"Merge branch '{branch}'")
        touched = _lines(_git(self.repo, "show", "--name-only", "--format=", "HEAD"))
        self.assertNotIn("src/peach/__init__.py", touched)

    def test_a_feature_commit_stays_on_the_same_version_too(self):
        """`feat` 决定的是发布时推哪一位，不是集成时推不推。"""
        branch = self.worker_touching("feature-work", "src/peach/module.py", subject="feat: 新功能")
        report = integrate(self.repo, branch)
        self.assertEqual(report["version"], "0.7.14")
        self.assertEqual(read_version(self.repo), "0.7.14")

    def test_a_new_migration_stays_on_the_same_version_too(self):
        branch = self.worker_touching("schema", "migrations/0099_new.sql", subject="fix: 补一列")
        self.assertEqual(integrate(self.repo, branch)["version"], "0.7.14")
        self.assertEqual(read_version(self.repo), "0.7.14")

    def test_integration_never_creates_a_version_tag_itself(self):
        """打标签只有 `scripts/release_tag.py` 一个入口，它遇到同名本地标签就拒绝。

        集成这一步再造一个本地标签，等于把唯一的发布入口挡在门外。
        """
        branch = self.worker_touching("taggable", "src/peach/module.py")
        report = integrate(self.repo, branch)
        self.assertEqual(report["release_tag_entry"], "scripts/release_tag.py")
        self.assertEqual(_lines(_git(self.repo, "tag", "--list")), [])

    def test_every_integration_reports_whether_a_release_is_due(self):
        """判断挂在集成的输出里，不靠谁记着去查——「记着」正是它要替掉的东西。"""
        branch = self.worker_touching("user-visible", "src/peach/module.py",
                                      subject="fix(web): 修一处使用者看得见的")
        report = integrate(self.repo, branch)
        self.assertEqual(report["release"]["entries"], 1)
        self.assertEqual(report["release"]["groups"], {"修复": 1})
        self.assertIn("due", report["release"])

    def test_development_only_work_never_asks_for_a_release(self):
        branch = self.worker_touching("inside-only", "src/peach/module.py",
                                      subject="refactor(web): 只动开发过程")
        report = integrate(self.repo, branch)
        self.assertEqual((report["release"]["entries"], report["release"]["due"]), (0, False))


from scripts.check_readme_impact import check, git


class ReadmeImpactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        git(self.repo, "init", "-b", "master")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        self.save("README.md", "中文")
        self.save("README.en.md", "English")
        git(self.repo, "commit", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD").strip()
        git(self.repo, "checkout", "-b", "worker")

    def save(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        git(self.repo, "add", "--", name)

    def commit(self, trailer=""):
        git(self.repo, "commit", "-m", "测试", "-m", trailer)
        return check(self.repo, self.base)

    def test_runtime_change_requires_review(self):
        self.save("src/peach/example.py", "x = 1")
        self.assertTrue(self.commit())

    def test_internal_change_accepts_explained_none(self):
        self.save("src/peach/example.py", "x = 1")
        self.assertEqual(self.commit("README-Impact: none; 内部缓存实现，使用方式不变"), [])

    def test_single_language_update_requires_counterpart_review(self):
        self.save("README.md", "新内容")
        self.assertTrue(self.commit("README-Impact: updated; 功能说明"))

    def test_single_language_typo_accepts_explicit_counterpart_review(self):
        self.save("README.md", "修正错字")
        self.assertEqual(self.commit("README-Impact: updated; 中文错字\n"
                                     "README-Reviewed: README.en.md; 英文表述正确，无需修改"), [])

    def test_counterpart_review_requires_correct_file_and_reason(self):
        self.save("README.md", "修正错字")
        for trailer in ("README-Reviewed: README.md; 已核对", "README-Reviewed: README.en.md;",
                        "README-Reviewed: README.en.md; 已核对\nREADME-Reviewed: README.en.md; 重复"):
            self.assertTrue(self.commit("README-Impact: updated; 中文错字\n" + trailer))
            git(self.repo, "reset", "--soft", "HEAD^")

    def test_bilingual_update_accepts_review(self):
        self.save("README.md", "新内容")
        self.save("README.en.md", "New content")
        self.assertEqual(self.commit("README-Impact: updated; 功能说明"), [])

    def test_none_cannot_describe_document_changes(self):
        self.save("README.md", "新内容")
        self.save("README.en.md", "New content")
        self.assertTrue(self.commit("README-Impact: none; 内部改动"))

    def test_test_only_change_needs_no_declaration(self):
        self.save("tests/example.py", "x = 1")
        self.assertEqual(self.commit(), [])

    def test_uncommitted_readme_does_not_satisfy_review(self):
        self.save("frontend/package.json", "{}")
        self.commit("README-Impact: updated; 工具要求")
        self.save("README.md", "新内容")
        self.save("README.en.md", "New content")
        self.assertTrue(check(self.repo, self.base))

    def test_missing_reason_and_duplicate_trailers_are_rejected(self):
        self.save("web/app.js", "x = 1")
        for trailer in ("README-Impact: none;",
                        "README-Impact: none; 原因\nREADME-Impact: updated; 原因"):
            git(self.repo, "commit", "--allow-empty", "-m", "测试", "-m", trailer)
            self.assertTrue(check(self.repo, self.base))

    def test_deletion_and_rename_trigger_review(self):
        self.save("src/peach/example.py", "x = 1")
        self.commit("README-Impact: none; 内部实现")
        self.base = git(self.repo, "rev-parse", "HEAD").strip()
        git(self.repo, "mv", "src/peach/example.py", "example.txt")
        self.assertTrue(self.commit())

    def test_gate_rejects_before_accepting_verification(self):
        self.save("web/app.js", "x = 1")
        self.commit()
        with mock.patch.object(agent_worktree.test_evidence, "key") as key:
            with self.assertRaisesRegex(agent_worktree.WorkspaceError, "README"):
                agent_worktree.require_verified(self.repo, "master", {"web/app.js"})
            key.assert_not_called()


from scripts import co_author


class CoAuthorTests(unittest.TestCase):
    """交付提交的署名。追责要落到具体的模型，所以工具名单独一个词不够。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        git(self.repo, "init", "-b", "master")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "commit", "--allow-empty", "-m", "base")
        git(self.repo, "checkout", "-b", "worker")

    def signed(self, *trailers):
        git(self.repo, "commit", "--allow-empty", "-m", "测试", "-m", "\n".join(trailers))
        return co_author.check(self.repo, "master")

    def test_an_unsigned_commit_cannot_be_traced_back_to_a_model(self):
        self.assertTrue(self.signed())

    def test_the_tool_alone_does_not_say_which_model_wrote_it(self):
        self.assertTrue(self.signed("Co-Authored-By: Claude Code <noreply@anthropic.com>"))

    def test_each_registered_tool_passes_with_its_own_vendor(self):
        self.assertEqual(
            self.signed("Co-Authored-By: Claude Code (Opus 5) <noreply@anthropic.com>"), [])
        self.assertEqual(
            self.signed("Co-Authored-By: Codex (GPT-5.5) <noreply@openai.com>"), [])
        self.assertEqual(
            self.signed("Co-Authored-By: opencode (GLM 5.3 Flash) <noreply@opencode.ai>"), [])

    def test_an_address_that_does_not_belong_to_the_tool_is_rejected(self):
        self.assertTrue(self.signed("Co-Authored-By: Codex (GPT-5.5) <noreply@anthropic.com>"))

    def test_an_unregistered_tool_waits_until_someone_registers_it(self):
        """名单在 `co_author.VENDORS`：换用别的智能体是要动一行代码的决定，不是随手写。"""
        self.assertTrue(self.signed("Co-Authored-By: Gemini CLI (3.5) <noreply@google.com>"))

    def test_two_agents_writing_one_commit_may_both_sign(self):
        self.assertEqual(
            self.signed("Co-Authored-By: Claude Code (Opus 5) <noreply@anthropic.com>",
                        "Co-Authored-By: Codex (GPT-5.5) <noreply@openai.com>"), [])

    def test_human_and_agent_coauthors_share_one_commit(self):
        human = "Co-Authored-By: Contributor <contributor@example.invalid>"
        agent = "Co-Authored-By: Codex (GPT-6) <noreply@openai.com>"
        for signatures in ((human, agent), (agent, human)):
            with self.subTest(signatures=signatures):
                self.assertEqual(self.signed(*signatures), [])

    def test_human_coauthor_requires_an_agent_signature(self):
        self.assertTrue(self.signed("Co-Authored-By: Contributor <contributor@example.invalid>"))

    def test_human_coauthor_requires_a_name_and_email(self):
        agent = "Co-Authored-By: Codex (GPT-6) <noreply@openai.com>"
        for human in ("Contributor", "Contributor <invalid>", "<contributor@example.invalid>"):
            with self.subTest(human=human):
                self.assertTrue(co_author.problems("测试", [agent, human]))

    def test_registered_agent_and_vendor_addresses_require_model_metadata(self):
        agent = "Codex (GPT-6) <noreply@openai.com>"
        for signature in ("Codex <someone@example.invalid>",
                          "Contributor <noreply@openai.com>"):
            with self.subTest(signature=signature):
                self.assertTrue(co_author.problems("测试", [agent, signature]))

    def test_every_commit_on_the_branch_is_checked_not_only_head(self):
        """一次交付常常是好几个提交。只判尖端等于只判收尾那一次签得对不对，
        而要追的恰恰是写出某一行的那个提交，所以问题里要报出它的短 hash 和主题。
        """
        self.signed("Co-Authored-By: Gemini CLI (3.5) <noreply@google.com>")
        problems = self.signed(
            "Co-Authored-By: Claude Code (Opus 5) <noreply@anthropic.com>")
        self.assertEqual(len(problems), 1)
        self.assertIn(git(self.repo, "log", "-1", "--format=%h", "HEAD~1").strip(),
                      problems[0])
        self.assertIn("Gemini CLI", problems[0])

    def test_merges_from_the_target_branch_are_not_the_worker_s_commits(self):
        """目标分支上的提交由它自己那次交付负责，合进来不等于这个分支重写了一遍。"""
        git(self.repo, "checkout", "master")
        git(self.repo, "commit", "--allow-empty", "-m", "上游的提交")
        git(self.repo, "checkout", "worker")
        self.signed("Co-Authored-By: Claude Code (Opus 5) <noreply@anthropic.com>")
        git(self.repo, "merge", "--no-ff", "-m", "Merge branch 'master' into worker",
            "master")
        self.assertEqual(co_author.check(self.repo, "master"), [])

    def test_a_commit_already_on_the_remote_master_cannot_be_signed_any_more(self):
        """网页上直接改的那条已经在 `origin/master` 上，分支并回它时改写 hash 只能强推；
        没有远端引用时什么都不豁免。"""
        git(self.repo, "commit", "--allow-empty", "-m", "Remove section from README")
        published = git(self.repo, "rev-parse", "HEAD").strip()
        self.signed("Co-Authored-By: Claude Code (Opus 5) <noreply@anthropic.com>")
        self.assertEqual(len(co_author.check(self.repo, "master")), 1)
        git(self.repo, "update-ref", "refs/remotes/origin/master", published)
        self.assertEqual(co_author.check(self.repo, "master"), [])

    def test_the_signature_gate_runs_before_the_test_evidence_is_read(self):
        """署名和 README 影响面在同一处拒收，都不必等到去读测试记录。"""
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "example.py").write_text("x = 1\n", encoding="utf-8")
        git(self.repo, "add", "--", "tests/example.py")
        git(self.repo, "commit", "-m", "测试")
        with mock.patch.object(agent_worktree.test_evidence, "key") as key:
            with self.assertRaisesRegex(agent_worktree.WorkspaceError, "Co-Authored-By"):
                agent_worktree.require_verified(self.repo, "master", {"tests/example.py"})
            key.assert_not_called()


from scripts import commit_subject


class CommitSubjectTests(unittest.TestCase):
    """提交主题的形状。变更日志按它分组，写歪的提交不报错，只是静默漏掉。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        git(self.repo, "init", "-b", "master")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "commit", "--allow-empty", "-m", "base")
        git(self.repo, "checkout", "-b", "worker")

    def titled(self, *subjects):
        for subject in subjects:
            git(self.repo, "commit", "--allow-empty", "-m", subject)
        return commit_subject.check(self.repo, "master")

    def test_a_conventional_subject_passes_with_or_without_scope(self):
        self.assertEqual(self.titled("fix(web): 补齐图标声明", "docs: 补记四个版本",
                                     "refactor(media)!: 删掉旧快照根"), [])

    def test_a_bare_sentence_is_not_a_subject(self):
        self.assertTrue(self.titled("修复关注页按手柄查不到"))

    def test_the_space_after_the_colon_is_part_of_the_shape(self):
        self.assertTrue(self.titled("fix(web):保留必需凭据"))

    def test_an_unlisted_type_waits_until_someone_lists_it(self):
        """清单在 `commit_subject.TYPES`：加类型要先想清它在变更日志里归哪一组。"""
        problems = self.titled("release(app): 版本 0.30.0")
        self.assertEqual(len(problems), 1)
        self.assertIn("release", problems[0])

    def test_every_commit_on_the_branch_is_checked_not_only_head(self):
        problems = self.titled("补齐 JAV 元数据", "fix(web): 后面这条是对的")
        self.assertEqual(len(problems), 1)
        self.assertIn("补齐 JAV 元数据", problems[0])

    def test_merges_from_the_target_branch_are_not_the_worker_s_subjects(self):
        git(self.repo, "checkout", "master")
        git(self.repo, "commit", "--allow-empty", "-m", "chore(deps-dev): bump vitest")
        git(self.repo, "checkout", "worker")
        git(self.repo, "commit", "--allow-empty", "-m", "fix(web): 自己的提交")
        git(self.repo, "merge", "--no-ff", "-m", "Merge branch 'master' into worker", "master")
        self.assertEqual(commit_subject.check(self.repo, "master"), [])

    def test_a_subject_already_on_the_remote_master_is_not_the_worker_s_to_fix(self):
        problems = self.titled("Remove section from README", "docs(readme): 并回网页上的改动")
        self.assertEqual(len(problems), 1)
        git(self.repo, "update-ref", "refs/remotes/origin/master",
            git(self.repo, "rev-parse", "HEAD~1").strip())
        self.assertEqual(commit_subject.check(self.repo, "master"), [])

    def test_the_subject_gate_runs_before_the_test_evidence_is_read(self):
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "example.py").write_text("x = 1\n", encoding="utf-8")
        git(self.repo, "add", "--", "tests/example.py")
        git(self.repo, "commit", "-m", "加个测试", "-m",
            "Co-Authored-By: Claude Code (Opus 5) <noreply@anthropic.com>")
        with mock.patch.object(agent_worktree.test_evidence, "key") as key:
            with self.assertRaisesRegex(agent_worktree.WorkspaceError, "提交主题"):
                agent_worktree.require_verified(self.repo, "master", {"tests/example.py"})
            key.assert_not_called()
