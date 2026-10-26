import contextlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from peach import buildinfo
from peach.buildinfo import BuildInfo
from peach.versioning import (
    UNKNOWN_BUILD_AGE, VersionManager, VersionSnapshot, discover_repository_root,
)
from peach.windows_update import windows_tray_rebuild_required


def git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={path.as_posix()}", "-C", str(path), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )


def initialize_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "master", str(path)], capture_output=True, check=True)
    git(path, "config", "user.name", "Peach Tests")
    git(path, "config", "user.email", "tests@peach.local")
    (path / "version.txt").write_text("one\n", encoding="utf-8")
    git(path, "add", "version.txt")
    git(path, "commit", "-m", "initial")


class VersionManagerTests(unittest.TestCase):
    @contextlib.contextmanager
    def tracking_clone(self):
        """一个跟踪 origin/master 的检出，外加一份用来代表「另一台机器」的克隆。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, repo, other = root / "remote.git", root / "repo", root / "other"
            config = root / "gitconfig"
            config.write_text(
                "[safe]\n"
                + "".join(f"\tdirectory = {path.as_posix()}\n"
                          for path in (repo, remote, other)),
                encoding="utf-8",
            )
            git_env = {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"}
            with mock.patch.dict(os.environ, git_env):
                subprocess.run(["git", "init", "--bare", str(remote)],
                               capture_output=True, check=True)
                initialize_repo(repo)
                git(repo, "remote", "add", "origin", str(remote))
                git(repo, "push", "-u", "origin", "master")
                subprocess.run(["git", "clone", str(remote), str(other)],
                               capture_output=True, check=True)
                git(other, "config", "user.name", "Peach Tests")
                git(other, "config", "user.email", "tests@peach.local")
            yield repo, other, git_env

    def test_repository_discovery_prefers_source_resource_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            initialize_repo(repo)
            executable = root / "elsewhere" / "Peach.exe"

            self.assertEqual(
                discover_repository_root(repo, executable=executable),
                repo.resolve(),
            )

    def test_repository_discovery_finds_repo_above_packaged_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            initialize_repo(repo)
            bundle_root = root / "pyinstaller" / "_MEI12345"
            executable = repo / "dist" / "Peach" / "Peach.exe"

            self.assertEqual(
                discover_repository_root(bundle_root, executable=executable),
                repo.resolve(),
            )

    def test_repository_discovery_does_not_invent_remote_for_portable_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle_root = root / "pyinstaller" / "_MEI12345"
            executable = root / "portable" / "Peach.exe"

            self.assertEqual(
                discover_repository_root(bundle_root, executable=executable),
                bundle_root.resolve(),
            )

    def test_local_development_version_is_readable_without_remote(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            initialize_repo(repo)
            manager = VersionManager(repo)
            snapshot = manager.inspect()
            self.assertEqual(snapshot.branch, "master")
            self.assertNotEqual(snapshot.commit, "unknown")
            self.assertFalse(snapshot.remote_configured)
            result = manager.check()
            self.assertEqual(result.state, "unconfigured")
            self.assertIn("本地开发版", result.message)

    def test_check_fetches_and_reports_remote_commits_without_applying(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = root / "remote.git"
            repo = root / "repo"
            other = root / "other"
            test_config = root / "gitconfig"
            test_config.write_text(
                "[safe]\n"
                f"\tdirectory = {repo.as_posix()}\n"
                f"\tdirectory = {remote.as_posix()}\n"
                f"\tdirectory = {other.as_posix()}\n",
                encoding="utf-8",
            )
            safe_directories = {
                "GIT_CONFIG_GLOBAL": str(test_config),
                "GIT_CONFIG_NOSYSTEM": "1",
            }
            with mock.patch.dict(os.environ, safe_directories):
                subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
                initialize_repo(repo)
                git(repo, "remote", "add", "origin", str(remote))
                git(repo, "push", "-u", "origin", "master")
                subprocess.run(["git", "clone", str(remote), str(other)], capture_output=True, check=True)
                git(other, "config", "user.name", "Peach Tests")
                git(other, "config", "user.email", "tests@peach.local")
                (other / "version.txt").write_text("two\n", encoding="utf-8")
                git(other, "add", "version.txt")
                git(other, "commit", "-m", "remote update")
                git(other, "push", "origin", "master")

                before = git(repo, "rev-parse", "HEAD").stdout.strip()
                result = VersionManager(repo).check()
                after = git(repo, "rev-parse", "HEAD").stdout.strip()
                self.assertEqual(result.state, "available")
                self.assertEqual(result.behind, 1)
                self.assertEqual(before, after)

    def test_update_fast_forwards_and_reports_what_changed(self):
        with self.tracking_clone() as (repo, other, git_env):
            with mock.patch.dict(os.environ, git_env):
                (other / "feature.txt").write_text("new\n", encoding="utf-8")
                git(other, "add", "feature.txt")
                git(other, "commit", "-m", "remote feature")
                git(other, "push", "origin", "master")

                result = VersionManager(repo).update()

                self.assertEqual(result.state, "updated")
                self.assertEqual(result.behind, 1)
                self.assertEqual(result.changed_paths, ("feature.txt",))
                self.assertTrue((repo / "feature.txt").is_file())

    def test_update_is_a_no_op_when_already_current(self):
        with self.tracking_clone() as (repo, _other, git_env):
            with mock.patch.dict(os.environ, git_env):
                before = git(repo, "rev-parse", "HEAD").stdout.strip()
                result = VersionManager(repo).update()
                self.assertEqual(result.state, "current")
                self.assertEqual(result.changed_paths, ())
                self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), before)

    def test_update_refuses_to_touch_a_dirty_checkout(self):
        """有未提交修改就停手。stash 会把别人正在改的东西卷进来，而托盘没人看着。"""
        with self.tracking_clone() as (repo, other, git_env):
            with mock.patch.dict(os.environ, git_env):
                (other / "feature.txt").write_text("new\n", encoding="utf-8")
                git(other, "add", "feature.txt")
                git(other, "commit", "-m", "remote feature")
                git(other, "push", "origin", "master")
                (repo / "version.txt").write_text("dirty\n", encoding="utf-8")

                before = git(repo, "rev-parse", "HEAD").stdout.strip()
                result = VersionManager(repo).update()

                self.assertEqual(result.state, "blocked")
                self.assertIn("未提交修改", result.message)
                self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), before)
                self.assertEqual((repo / "version.txt").read_text(encoding="utf-8"), "dirty\n")

    def test_update_never_rewrites_history_when_the_branches_diverged(self):
        """两边分叉时只报，不 rebase 也不 reset：并行工作树共用同一个对象库。"""
        with self.tracking_clone() as (repo, other, git_env):
            with mock.patch.dict(os.environ, git_env):
                (other / "feature.txt").write_text("new\n", encoding="utf-8")
                git(other, "add", "feature.txt")
                git(other, "commit", "-m", "remote feature")
                git(other, "push", "origin", "master")
                (repo / "local.txt").write_text("mine\n", encoding="utf-8")
                git(repo, "add", "local.txt")
                git(repo, "commit", "-m", "local work")

                before = git(repo, "rev-parse", "HEAD").stdout.strip()
                result = VersionManager(repo).update()

                self.assertEqual(result.state, "diverged")
                self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), before)
                self.assertFalse((repo / "feature.txt").exists())


class PackagedBuildAgeTests(unittest.TestCase):
    """打包托盘跑的是构建那一刻的代码副本；检出往前走了，它不会跟着变。

    这台机器既是开发机又是生产机，提交先落本地再推 GitHub，所以「落后远端」永远不成立，
    而托盘停在两周前的代码上却完全可能——判据必须是构建提交与检出的差，不是远端。
    """

    @contextlib.contextmanager
    def packaged(self, commit: str | None):
        info = None if commit is None else BuildInfo(commit, "0.7.14", "2026-09-05T10:00:00+08:00")
        with mock.patch.object(buildinfo.sys, "frozen", True, create=True), \
                mock.patch.object(buildinfo, "frozen_build", return_value=info):
            yield

    def repo_with_two_commits(self, root: Path) -> tuple[Path, str]:
        repo = root / "repo"
        initialize_repo(repo)
        built_at = git(repo, "rev-parse", "HEAD").stdout.strip()
        (repo / "src" / "peach").mkdir(parents=True)
        (repo / "src" / "peach" / "tray.py").write_text("tray\n", encoding="utf-8")
        git(repo, "add", "src/peach/tray.py")
        git(repo, "commit", "-m", "tray change")
        return repo, built_at

    def test_a_packaged_tray_measures_itself_against_the_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, built_at = self.repo_with_two_commits(Path(directory).resolve())
            with self.packaged(built_at):
                snapshot = VersionManager(repo).inspect()
            self.assertEqual(snapshot.build_commit, built_at)
            self.assertEqual(snapshot.build_behind, 1)
            self.assertTrue(snapshot.build_stale)
            self.assertIn(f"托盘构建 {built_at[:8]}，落后 1 个提交", snapshot.build_label)

    def test_a_tray_built_from_the_current_head_is_not_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, _built_at = self.repo_with_two_commits(Path(directory).resolve())
            head = git(repo, "rev-parse", "HEAD").stdout.strip()
            with self.packaged(head):
                snapshot = VersionManager(repo).inspect()
            self.assertEqual(snapshot.build_behind, 0)
            self.assertFalse(snapshot.build_stale)
            self.assertEqual(snapshot.build_label, f"master@{snapshot.commit}")

    def test_a_source_checkout_has_no_second_version_to_lag_behind(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, _built_at = self.repo_with_two_commits(Path(directory).resolve())
            snapshot = VersionManager(repo).inspect()
            self.assertIsNone(snapshot.build_commit)
            self.assertIsNone(snapshot.build_behind)
            self.assertFalse(snapshot.build_stale)

    def test_a_build_identity_nobody_can_read_counts_as_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, _built_at = self.repo_with_two_commits(Path(directory).resolve())
            with self.packaged(None):
                snapshot = VersionManager(repo).inspect()
            self.assertIsNone(snapshot.build_commit)
            self.assertEqual(snapshot.build_behind, UNKNOWN_BUILD_AGE)
            self.assertTrue(snapshot.build_stale)
            self.assertIn("托盘构建身份未取得", snapshot.build_label)

    def test_a_build_commit_outside_this_repository_counts_as_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, _built_at = self.repo_with_two_commits(Path(directory).resolve())
            stranger = "0" * 40
            with self.packaged(stranger):
                snapshot = VersionManager(repo).inspect()
            self.assertEqual(snapshot.build_behind, UNKNOWN_BUILD_AGE)
            self.assertIn("不在本检出历史里", snapshot.build_label)

    def test_stale_paths_name_what_changed_since_the_build(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, built_at = self.repo_with_two_commits(Path(directory).resolve())
            paths = VersionManager(repo).stale_build_paths(built_at)
            self.assertEqual(paths, ("src/peach/tray.py",))
            self.assertTrue(windows_tray_rebuild_required(paths))

    def test_an_unmeasurable_build_falls_back_to_a_value_that_forces_a_rebuild(self):
        """数不出来不等于没落后：托盘旧着不动才是真正会骗人的那个结果。"""
        with tempfile.TemporaryDirectory() as directory:
            repo, _built_at = self.repo_with_two_commits(Path(directory).resolve())
            manager = VersionManager(repo)
            for unmeasurable in (None, "", "0" * 40):
                paths = manager.stale_build_paths(unmeasurable)
                self.assertEqual(paths, ("src/peach/",))
                self.assertTrue(windows_tray_rebuild_required(paths))

    def test_a_standalone_package_never_reports_a_stale_build(self):
        """独立测试包没有源码检出可比，更新方式是下载新版完整替换程序目录。"""
        with tempfile.TemporaryDirectory() as directory:
            repo, built_at = self.repo_with_two_commits(Path(directory).resolve())
            with self.packaged(built_at), \
                    mock.patch("peach.distribution.standalone", return_value=True):
                snapshot = VersionManager(repo).inspect()
            self.assertIsNone(snapshot.build_behind)
            self.assertFalse(snapshot.build_stale)

    def test_the_head_probe_reads_the_checkout_without_touching_the_network(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, _built_at = self.repo_with_two_commits(Path(directory).resolve())
            calls = []
            manager = VersionManager(repo)
            real = manager._execute

            def record(command, **kwargs):
                calls.append(command)
                return real(command, **kwargs)

            manager._execute = record
            self.assertEqual(manager.head_commit(),
                             git(repo, "rev-parse", "HEAD").stdout.strip())
            self.assertNotIn("fetch", [argument for call in calls for argument in call])


class BuildLabelTests(unittest.TestCase):
    def test_uncommitted_changes_and_a_stale_build_are_both_named(self):
        snapshot = VersionSnapshot(
            "0.7.14", "master", "1a2b3c4d", True, True, "origin/master",
            build_commit="9f8e7d6c5b4a39281706f5e4d3c2b1a098765432", build_behind=4,
        )
        self.assertEqual(
            snapshot.build_label,
            "master@1a2b3c4d · 有未提交修改 · 托盘构建 9f8e7d6c，落后 4 个提交",
        )


if __name__ == "__main__":
    unittest.main()
