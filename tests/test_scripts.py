import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest import mock

from datetime import datetime, timezone

from peach import follow_image_dims, scripting
from peach.follow_sources import FollowCandidate, SourceFetch
from peach.follow_store import FollowStore
from peach.http import HttpResponse
from peach.migrations import upgrade
from peach.scan import medium_of
from support.ledger import fresh_ledger
from peach.classification import is_probable_mainstream_release, is_structural_creator


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"


def load_script(name: str):
    """按路径加载 `scripts/<name>.py`。

    执行前先登记进 `sys.modules`：`@dataclass` 处理注解时要按 `cls.__module__` 回查
    模块，没登记就拿到 `None`，报出来的是 `'NoneType' object has no attribute
    '__dict__'`——和脚本本身毫无关系。前缀不用 `test_`，否则加载 `agent_worktree`
    会把真正的 tests/test_agent_worktree.py 从 `sys.modules` 里顶掉。
    """
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"peach_script_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


class ProjectBoardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = load_script("project_board")

    def issue(self, **changes):
        return {"__typename": "Issue", "state": "OPEN", "labels": {"nodes": []},
                "repository": {"nameWithOwner": "peach-mitao/peach"}, **changes}

    def test_merge_and_publication_keep_open_issue_waiting_for_acceptance(self):
        release = {"tag_name": "v1.2.0", "html_url": "https://example.test/release"}
        result = self.script.decision(self.issue(), {"Status": "进行中"}, [{"merged": True}], release)
        self.assertEqual(result["Status"], "待验证")
        self.assertEqual(result["交付进度"], "已发布")
        self.assertEqual(result["发布版本"], "v1.2.0")

    def test_closed_not_planned_is_cancelled_and_reopened_returns_to_queue(self):
        result = self.script.decision(self.issue(state="CLOSED", stateReason="NOT_PLANNED"), {}, [], None)
        self.assertEqual(result["Status"], "已取消")
        result = self.script.decision(self.issue(), {"Status": "已取消"}, [], None)
        self.assertEqual(result["Status"], "待安排")

    def test_manual_priority_and_queue_are_not_replaced_by_unrelated_activity(self):
        result = self.script.decision(self.issue(), {"Status": "待安排", "Priority": "P1"}, [], None)
        self.assertEqual(result["Status"], "待安排")
        self.assertNotIn("Priority", result)
        waiting = self.issue(labels={"nodes": [{"name": "status:needs-info"}]})
        self.assertEqual(self.script.decision(waiting, {}, [{"merged": True}], None)["Status"], "待评估")

    def test_reference_lines_exclude_other_repositories_and_casual_mentions(self):
        body = "参考 #64 的讨论\nRefs #65, peach-mitao/peach#66, other/repo#67\nCloses #68"
        self.assertEqual(self.script.references(body, "peach-mitao/peach"),
                         {f"peach-mitao/peach#{number}" for number in (65, 66, 68)})

    def test_migrated_references_deduplicate_without_cross_repository_number_collision(self):
        main, planning = "peach-mitao/peach", "peach-mitao/peach-roadmap"
        config = {"repository": main, "issue_repositories": [main, planning],
                  "issue_aliases": {f"{main}#70": f"{planning}#1"}}
        pr = {"state": "MERGED", "merged": True, "mergeCommit": {"oid": "a" * 40}, "url": "https://example.test/pr",
              "body": f"Refs #70, {planning}#1, other/repo#1\nRefs https://github.com/{planning}/issues/1",
              "closingIssuesReferences": {"nodes": [self.issue(number=1, repository={"nameWithOwner": planning}),
                                                       self.issue(number=1, repository={"nameWithOwner": "other/repo"})]}}
        linked = self.script.issue_links([pr], config)
        self.assertEqual(linked, {f"{planning}#1": [pr]})
        config["implementation_commits"] = {f"{main}#1": ["b" * 40]}
        with mock.patch.object(self.script, "ancestor") as ancestry:
            untouched = self.script.delivery_fields(config, self.issue(number=1, repository={"nameWithOwner": planning}), {}, {}, [])
        self.assertEqual(untouched["交付进度"], "未关联实现")
        ancestry.assert_not_called()
        release = {"tag_name": "v1.0", "html_url": "https://example.test/v1.0"}
        with mock.patch.object(self.script, "ancestor", return_value=True) as ancestry:
            result = self.script.delivery_fields(config, self.issue(number=1, repository={"nameWithOwner": planning}), {}, linked, [release])
        self.assertEqual(result["发布版本"], "v1.0")
        self.assertEqual(result["实现关联"], pr["url"])
        self.assertTrue(all(call.args[0] == "a" * 40 for call in ancestry.call_args_list))
        with mock.patch.object(self.script, "ancestor") as ancestry:
            result = self.script.delivery_fields(config, self.issue(number=1), {}, linked, [])
        self.assertEqual(result["交付进度"], "已合入待发布")
        ancestry.assert_called_once_with("b" * 40, "origin/master")

    def test_planning_poll_includes_closed_issues_and_alias_cycles_fail(self):
        main, planning = "peach-mitao/peach", "peach-mitao/peach-roadmap"
        closed = self.issue(id="closed", state="CLOSED")
        with mock.patch.object(self.script, "connection", side_effect=[[], [closed]]) as read:
            result = self.script.incoming_issues({"repository": main, "planning_repository": planning,
                                                  "issue_repositories": [main, planning]})
        self.assertEqual(result, [closed])
        self.assertEqual(read.call_args_list[0].kwargs["states"], ["OPEN"])
        self.assertIsNone(read.call_args_list[1].kwargs["states"])
        with self.assertRaisesRegex(ValueError, "循环"):
            self.script.canonical("a", {"a": "b", "b": "a"})

    def test_pr_closure_is_distinct_from_merge(self):
        pr = {"__typename": "PullRequest", "state": "CLOSED", "merged": False}
        self.assertEqual(self.script.decision(pr, {}, [], None)["Status"], "已关闭未合入")
        pr["merged"] = True
        self.assertEqual(self.script.decision(pr, {}, [], None)["交付进度"], "已合入待发布")

    def test_reconcile_uses_published_assets_and_keeps_archived_items_untouched(self):
        commit = "a" * 40
        fields = [{"id": name, "name": name} for name in ("发布版本", "发布说明", "实现关联")]
        fields += [{"id": "status", "name": "Status", "options": [{"id": "verify", "name": "待验证"}]},
                   {"id": "delivery", "name": "交付进度", "options": [{"id": "published", "name": "已发布"}]},
                   {"id": "source", "name": "来源", "options": [{"id": "feedback", "name": "用户反馈"}, {"id": "plan", "name": "维护者待办"}]}]
        issue = self.issue(id="issue", number=65, title="历史导入", author={"login": "visitor"}, url="https://github.com/peach-mitao/peach/issues/65")
        item = {"id": "item", "content": issue, "isArchived": False, "fieldValues": {"nodes": []}}
        archived = {**item, "id": "archive", "isArchived": True, "content": {**issue, "id": "old", "number": 90}}
        draft = {"id": "draft", "isArchived": False, "content": {"__typename": "DraftIssue", "id": "draft-content"}}
        releases = [[{"tag_name": tag, "html_url": f"https://example.test/{tag}", "published_at": date,
                      "draft": is_draft, "assets": assets} for tag, date, is_draft, assets in [
            ("v0.42.0", "2026-10-16", False, []),
            ("v0.43.0", "2026-10-17", False, [{"name": "app.zip"}]),
            ("v0.44.0", "2026-10-18", True, [{"name": "app.zip"}]),
            ("v0.45.0", "2026-10-19", False, [{"name": "app.zip"}])]]]
        def api_result(query, **variables):
            if "fields(first:" in query:
                return {"node": {"fields": {"nodes": fields}}}
            if "archivedStates:[ARCHIVED,NOT_ARCHIVED]" in query:
                return {"node": {"items": {"nodes": [item, archived, draft], "pageInfo": {"hasNextPage": False}}}}
            if "issues(first:" in query:
                return {"repository": {"issues": {"nodes": [archived["content"]], "pageInfo": {"hasNextPage": False}}}}
            if "pullRequests(first:" in query:
                return {"repository": {"pullRequests": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}
            if "updateProjectV2ItemFieldValue(" in query:
                self.assertEqual(variables["item"], "item")
                return {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "item"}}}
            self.fail(f"不允许的看板调用：{query}")

        with mock.patch.object(self.script, "graphql", side_effect=api_result), \
                mock.patch.object(self.script, "gh", return_value=releases), \
                mock.patch.object(self.script, "ancestor", return_value=True):
            changes = self.script.reconcile({"project_id": "project", "repository": "peach-mitao/peach",
                                              "implementation_commits": {"peach-mitao/peach#65": [commit]}}, apply=True)
            maintained = self.script.reconcile({"project_id": "project", "repository": "peach-mitao/peach",
                                                "planning_repository": "peach-mitao/peach",
                                                "implementation_commits": {"peach-mitao/peach#65": [commit]}}, apply=False)
        self.assertEqual({change["number"] for change in changes}, {65})
        actual = {change["field"]: change["value"] for change in changes}
        self.assertEqual(actual["发布版本"], "v0.43.0")
        self.assertEqual(actual["Status"], "待验证")
        self.assertEqual(actual["来源"], "用户反馈")
        self.assertEqual(next(change["value"] for change in maintained if change["field"]=="来源"), "维护者待办")
        self.assertTrue(actual["实现关联"].endswith(commit))

    def test_ancestor_uses_actual_commit_graph_and_rejects_unmerged_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                                      text=True, encoding="utf-8").stdout.strip()
            git("init", "-b", "master")
            git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--allow-empty", "-m", "base")
            base = git("rev-parse", "HEAD")
            git("tag", "v1.0")
            git("checkout", "-b", "feature")
            git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--allow-empty", "-m", "feature")
            feature = git("rev-parse", "HEAD")
            with mock.patch.object(self.script, "ROOT", root):
                self.assertTrue(self.script.ancestor(base, "refs/tags/v1.0"))
                self.assertFalse(self.script.ancestor(feature, "refs/tags/v1.0"))
                self.assertFalse(self.script.ancestor("not-a-commit", "refs/tags/v1.0"))

    def test_public_roadmap_adds_issues_without_copying_internal_drafts_or_prs(self):
        issue = self.issue(id="public-issue", number=7)
        pr = {"__typename": "PullRequest", "id": "pr", "state": "OPEN"}
        internal = {"id": "private-card", "isArchived": False,
                    "content": {"id": "private-draft", "__typename": "DraftIssue"}}
        candidates = {"private-draft": internal}
        with mock.patch.object(self.script, "connection", return_value=[issue]), \
                mock.patch.object(self.script, "graphql", return_value={"node": pr}) as api:
            self.script.include_incoming({"repository": "peach-mitao/peach", "project_id": "public", "issues_only": True},
                                         candidates, [pr], {"repository": {"full_name": "peach-mitao/peach"},
                                                            "pull_request": {"node_id": "pr"}}, False)
        self.assertEqual(set(candidates), {"private-draft", "public-issue"})
        self.assertIs(candidates["private-draft"], internal)
        self.assertEqual(api.call_count, 1)


class UpdateTaskSmokeTests(unittest.TestCase):
    def test_dry_run_does_not_open_artifacts_or_create_installation_data(self):
        with mock.patch.dict(sys.modules, {'smoke_desktop': load_script('smoke_desktop')}):
            script = load_script('smoke_update_tasks')
        with mock.patch.object(sys, 'argv', ['smoke_update_tasks.py', 'missing/Peach.exe']), \
                mock.patch.object(script, 'smoke_directory') as directory, redirect_stdout(io.StringIO()):
            self.assertEqual(script.main(), 0)
        directory.assert_not_called()


class DesktopSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = load_script("smoke_desktop")

    def test_smoke_environment_disables_remote_reporting_before_startup(self):
        from peach import remote_reporting
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory).resolve() / 'data'
            environment = self.script.smoke_environment(data)
            self.assertEqual(environment['PEACH_DATA_ROOT'], str(data))
            config = SimpleNamespace(directory=lambda name: data / name)
            self.assertEqual(remote_reporting.consent(config), {
                'usage': False, 'errors': False, 'revision': 'desktop-smoke',
            })
            with mock.patch.dict(os.environ, {
                    'PEACH_SENTRY_DSN': 'https://public@o1.ingest.us.sentry.io/1',
                    }), mock.patch.object(remote_reporting, '_disabled', False), \
                    mock.patch.object(remote_reporting, '_new_client') as client:
                remote_reporting.usage(config)
                remote_reporting.send('errors', {'level': 'error'}, config)
            client.assert_not_called()

    def test_distribution_resources_accept_an_application_without_private_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            application = Path(directory).resolve()
            (application / '_internal/resources').mkdir(parents=True)
            self.script.assert_public_resources(application)

    def test_distribution_resources_reject_private_entity_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            application = Path(directory).resolve()
            seed = application / '_internal/resources/seed'
            seed.mkdir(parents=True)
            (seed / 'entities.json').write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(AssertionError, '私有实体导出'):
                self.script.assert_public_resources(application)

    def test_cleanup_waits_for_a_released_windows_directory(self):
        error = PermissionError('目录仍被占用')
        error.winerror = 32
        directory = mock.Mock()
        directory.cleanup.side_effect = [error, None]
        with mock.patch.object(self.script.time, 'sleep'):
            self.script.cleanup_smoke_directory(directory)
        self.assertEqual(directory.cleanup.call_count, 2)

    def test_cleanup_preserves_persistent_or_unrelated_failures(self):
        for winerror in (32, 5):
            with self.subTest(winerror=winerror):
                error = PermissionError('无法清理')
                error.winerror = winerror
                directory = mock.Mock()
                directory.cleanup.side_effect = error
                with mock.patch.object(self.script.time, 'monotonic', side_effect=[0, 16]), \
                        self.assertRaises(PermissionError):
                    self.script.cleanup_smoke_directory(directory)

    def test_failure_preserves_logs_without_database_or_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory).resolve() / "evidence"
            with self.assertRaisesRegex(RuntimeError, "startup failed"), redirect_stderr(io.StringIO()):
                with self.script.smoke_directory(evidence) as root:
                    (root / "data/logs").mkdir(parents=True)
                    (root / "data/logs/tray.log").write_text("启动堆栈", encoding="utf-8")
                    (root / "data/config.toml").write_text("private config", encoding="utf-8")
                    (root / "data/ledger.db").write_bytes(b"private database")
                    raise RuntimeError("startup failed")
            files = [path.relative_to(evidence).parts[1:] for path in evidence.rglob("*") if path.is_file()]
            self.assertCountEqual(files, [("data", "logs", "tray.log"), ("failure.txt",)])
            self.assertFalse(root.exists())

    def test_success_removes_temporary_data_without_creating_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory).resolve() / "evidence"
            with self.script.smoke_directory(evidence) as root:
                (root / "runtime.log").write_text("ok", encoding="utf-8")
            self.assertFalse(root.exists())
            self.assertFalse(evidence.exists())

    def test_lifecycle_reader_tolerates_a_record_being_written(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory).resolve()
            self.assertEqual(self.script.lifecycle_events(data), [])
            (data / "logs").mkdir()
            (data / "logs/tray-lifecycle.log").write_text(
                '{"event":"watchdog_ready","tray_pid":42}\n{"event":', encoding="utf-8")
            self.assertEqual(self.script.lifecycle_events(data), [{"event": "watchdog_ready", "tray_pid": 42}])

    def test_process_inventory_filters_by_exact_artifact_and_handles_single_pid(self):
        with mock.patch.dict(os.environ, {"SystemRoot": "C:/Windows"}), \
                mock.patch.object(self.script.subprocess, "run",
                                  return_value=SimpleNamespace(stdout="42")) as run:
            executable = Path("C:/isolated/Peach.exe")
            self.assertEqual(self.script.artifact_processes(executable), [42])
        self.assertEqual(run.call_args.kwargs["env"]["PEACH_SMOKE_EXECUTABLE"], str(executable))
        self.assertIn("-NonInteractive", run.call_args.args[0])

    def test_health_wait_accepts_a_detached_launcher(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true}'
        opener = mock.Mock()
        opener.open.return_value = response
        self.assertEqual(self.script.wait_health(opener, "http://127.0.0.1:1", None,
                                                Path("unused.log")), {"ok": True})

    def test_failed_normal_stop_disarms_watchdogs_and_reaps_only_artifact_pids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            watchdogs = root / "data/state/tray-watchdogs"
            watchdogs.mkdir(parents=True)
            (watchdogs / "session.json").write_text("{}", encoding="utf-8")
            (watchdogs / "session.ready").write_text("43", encoding="utf-8")
            executable = root / "Peach.exe"
            launcher = mock.Mock()
            with mock.patch.dict(os.environ, {"SystemRoot": "C:/Windows"}), \
                    mock.patch.object(self.script, "artifact_processes", side_effect=[[42, 43], []]), \
                    mock.patch.object(self.script, "wait_for", side_effect=lambda check, *_args, **_kwargs: check()), \
                    mock.patch.object(self.script.subprocess, "run", side_effect=[
                        SimpleNamespace(returncode=1), SimpleNamespace(returncode=0), SimpleNamespace(returncode=0)]) as run:
                with self.assertRaisesRegex(AssertionError, "托盘退出失败"):
                    self.script.stop_tray(executable, root, {}, io.BytesIO(), launcher)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands[0], [str(executable), "--installer-stop"])
            self.assertEqual([command[2] for command in commands[1:]], ["43", "42"])
            self.assertEqual((watchdogs / "session.stop").read_text(encoding="utf-8"), "smoke-cleanup")

    @unittest.skipUnless(os.name == "nt", "Windows 托盘验收")
    def test_setup_failure_still_stops_tray_and_checks_all_artifact_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            executable = root / "Peach.exe"
            process = mock.Mock()

            def wait_for(check, description, **_kwargs):
                if "全部退出" in description:
                    self.assertTrue(check())
                return True

            with mock.patch.object(self.script, "artifact_processes", return_value=[]) as inventory, \
                    mock.patch.object(self.script, "wait_for", side_effect=wait_for), \
                    mock.patch.object(self.script, "wait_health", return_value={"ok": True}), \
                    mock.patch.object(self.script, "complete_first_run", side_effect=RuntimeError("setup failed")), \
                    mock.patch.object(self.script.subprocess, "Popen", return_value=process) as popen, \
                    mock.patch.object(self.script.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as stop, \
                    mock.patch.object(self.script.socket, "socket"), redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "setup failed"):
                    self.script.smoke_tray(executable, root / "evidence")
            self.assertEqual(popen.call_args.args[0], [str(executable), "--silent"])
            self.assertEqual(stop.call_args.args[0], [str(executable), "--installer-stop"])
            self.assertEqual(inventory.call_count, 2)
            process.wait.assert_called_once_with(timeout=15)
            failure = next((root / "evidence").rglob("failure.txt")).read_text(encoding="utf-8")
            self.assertIn("setup failed", failure)


class Sha1SyncOptionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = load_script("sync_sha1_115")

    def test_import_does_not_create_files_or_connect_to_ledger(self):
        with mock.patch("pathlib.Path.mkdir") as mkdir, \
                mock.patch("pathlib.Path.open") as open_path, \
                mock.patch("sqlite3.connect") as connect, \
                mock.patch.dict(sys.modules, {"p115client": None}):
            load_script("sync_sha1_115")
        mkdir.assert_not_called()
        open_path.assert_not_called()
        connect.assert_not_called()

    def test_default_paths_follow_configured_data_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with mock.patch.dict("os.environ", {}, clear=True), \
                    mock.patch.object(self.script, "SECRETS_DIR", root / "secrets"), \
                    mock.patch.object(self.script, "REVIEW_DIR", root / "review"):
                args = self.script.parse_args([])
            self.assertEqual(args.cookie_file, root / "secrets" / "115-cookies.txt")
            self.assertEqual(args.dupes_out, root / "review" / "115-sha1重复.csv")
            self.assertEqual(args.deprioritize_dir, [])
            self.assertFalse((root / "secrets").exists())
            self.assertFalse((root / "review").exists())

    def test_explicit_paths_override_environment_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            environment = {"PEACH_115_COOKIE_FILE": str(root / "env-cookie.txt"),
                           "PEACH_115_DUPES_OUT": str(root / "env-report.csv")}
            with mock.patch.dict("os.environ", environment):
                defaults = self.script.parse_args([])
                explicit = self.script.parse_args([
                    "--cookie-file", str(root / "cli-cookie.txt"),
                    "--dupes", "--dupes-out", str(root / "cli-report.csv"),
                    "--deprioritize-dir", "incoming", "--deprioritize-dir", "temporary"])
            self.assertEqual(defaults.cookie_file, root / "env-cookie.txt")
            self.assertEqual(defaults.dupes_out, root / "env-report.csv")
            self.assertEqual(explicit.cookie_file, root / "cli-cookie.txt")
            self.assertEqual(explicit.dupes_out, root / "cli-report.csv")
            self.assertTrue(explicit.dupes)
            self.assertEqual(explicit.deprioritize_dir, ["incoming", "temporary"])

    def test_directory_preference_requires_exact_component_and_explicit_opt_in(self):
        score = self.script.duplicate_score
        self.assertEqual(score(r"B:\云下载\a.mp4"), score(r"B:\library\a.mp4"))
        self.assertLess(score(r"B:\Incoming\a.mp4", ["incoming"]),
                        score(r"B:\incoming-archive\a.mp4", ["incoming"]))
        self.assertEqual(score("B:/Incoming/a.mp4", ["incoming"]),
                         score(r"B:\Incoming\a.mp4", ["incoming"]))

    def test_report_uses_selected_path_and_preference_without_mutating_assets(self):
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(":memory:")) as conn:
            root = Path(directory).resolve()
            conn.execute("CREATE TABLE asset (id INTEGER, path TEXT, size INTEGER, hash TEXT, location TEXT)")
            assets = [(1, r"B:\incoming\a.mp4", 1024**3, "sha-a", "115"),
                      (2, r"B:\library\b.mp4", 1024**3, "sha-a", "115"),
                      (3, r"R:\a.mp4", 1024**3, "sha-a", "local"),
                      (4, r"B:\single.mp4", 1024, "sha-b", "115")]
            conn.executemany("INSERT INTO asset VALUES (?, ?, ?, ?, ?)", assets)
            output = root / "review" / "custom.csv"
            self.assertEqual(self.script.write_duplicate_report(conn, output, ["incoming"]),
                             (1, 1024**3))
            with output.open(encoding=self.script.ENCODING, newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([(row["动作"], row["路径"]) for row in rows],
                             [("保留", r"B:\library\b.mp4"), ("删除", r"B:\incoming\a.mp4")])
            self.assertEqual(conn.execute("SELECT * FROM asset ORDER BY id").fetchall(), assets)


@unittest.skipUnless(sys.platform == "win32" and shutil.which("pwsh"), "需要 Windows PowerShell 7")
class LocalTlsDirectoryTests(unittest.TestCase):
    def resolve_directory(self, root, override=""):
        harness = root / "resolve-tls.ps1"
        harness.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            "$TlsTokens = $null\n$TlsErrors = $null\n"
            "$TlsAst = [System.Management.Automation.Language.Parser]::ParseFile("
            "$env:PEACH_TLS_SCRIPT, [ref]$TlsTokens, [ref]$TlsErrors)\n"
            "$TlsFunction = $TlsAst.Find({ param($TlsNode) "
            "$TlsNode -is [System.Management.Automation.Language.FunctionDefinitionAst] "
            "-and $TlsNode.Name -eq 'Resolve-PeachTlsDirectory' }, $true)\n"
            ". ([ScriptBlock]::Create($TlsFunction.Extent.Text))\n"
            "Resolve-PeachTlsDirectory -RequestedDirectory $env:PEACH_TLS_OVERRIDE "
            "-ProjectRoot $env:PEACH_TLS_PROJECT\n",
            encoding="utf-8")
        environment = dict(os.environ, PEACH_DATA_ROOT=str(root / "data"),
                           PEACH_TLS_SCRIPT=str(ROOT / "scripts" / "setup_local_tls.ps1"),
                           PEACH_TLS_PROJECT=str(ROOT), PEACH_TLS_OVERRIDE=override)
        return subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(harness)],
                              env=environment, capture_output=True, text=True, encoding="utf-8",
                              check=False, timeout=30)

    def test_environment_data_root_resolves_without_creating_tls_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            result = self.resolve_directory(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()), root / "data" / "secrets" / "tls")
            self.assertFalse((root / "data").exists())

    def test_configured_secrets_directory_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "data").mkdir()
            (root / "data" / "config.toml").write_text('[directories]\nsecrets = "private"\n', encoding="utf-8")
            result = self.resolve_directory(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()), root / "data" / "private" / "tls")
            self.assertFalse((root / "data" / "private").exists())

    def test_explicit_directory_bypasses_config_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "data").mkdir()
            (root / "data" / "config.toml").write_text('invalid [', encoding="utf-8")
            output = root / "explicit" / "tls"
            result = self.resolve_directory(root, str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()), output)
            self.assertFalse(output.exists())

    def test_invalid_configuration_refuses_default_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "data").mkdir()
            (root / "data" / "config.toml").write_text('invalid [', encoding="utf-8")
            result = self.resolve_directory(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "data" / "secrets").exists())


class PosixTlsDirectoryTests(unittest.TestCase):
    def resolve_directory(self, root, override=""):
        shells = ("/bin/sh", r"C:\Program Files\Git\bin\sh.exe", shutil.which("sh"))
        shell = next((candidate for candidate in shells if candidate and Path(candidate).is_file()), None)
        if not shell:
            self.skipTest("需要 POSIX shell")
        project = root / "project"
        (project / "scripts").mkdir(parents=True)
        (project / ".venv" / "bin").mkdir(parents=True)
        interpreter = project / ".venv" / "bin" / "python"
        interpreter.write_text(
            '#!/bin/sh\nPYTHONPATH="$PEACH_TLS_TEST_SOURCE" exec "$PEACH_TLS_TEST_PYTHON" "$@"\n',
            encoding="utf-8")
        interpreter.chmod(0o755)
        shutil.copyfile(ROOT / "scripts" / "resolve_tls_directory.py",
                        project / "scripts" / "resolve_tls_directory.py")
        source = (ROOT / "scripts" / "setup_local_tls.sh").read_text(encoding="utf-8")
        # 只运行路径解析段，证书签发与私钥读取不进入夹具。
        harness = project / "scripts" / "resolve-tls.sh"
        harness.write_text(source.partition("CA_CRT=")[0] + 'printf \'%s\\n\' "$TLS_DIR"\n', encoding="utf-8")
        environment = dict(os.environ, PEACH_DATA_ROOT=str(root / "data"), PEACH_TLS_DIR=override,
                           PEACH_TLS_TEST_PYTHON=Path(sys.executable).resolve().as_posix(),
                           PEACH_TLS_TEST_SOURCE=str(ROOT / "src"))
        return subprocess.run([shell, harness.as_posix()], env=environment,
                              capture_output=True, text=True, encoding="utf-8", check=False, timeout=30)

    def test_shell_uses_the_shared_settings_helper(self):
        for contents, tail in ((None, "secrets"), ('[directories]\nsecrets = "private"\n', "private")):
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                if contents:
                    (root / "data").mkdir()
                    (root / "data" / "config.toml").write_text(contents, encoding="utf-8")
                result = self.resolve_directory(root)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(Path(result.stdout.strip()), root / "data" / tail / "tls")
                self.assertFalse((root / "data" / tail).exists())

    def test_explicit_directory_bypasses_invalid_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "data").mkdir()
            (root / "data" / "config.toml").write_text("invalid [", encoding="utf-8")
            output = root / "explicit" / "tls"
            result = self.resolve_directory(root, str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()), output)
            self.assertFalse(output.exists())

    def test_invalid_settings_stop_before_certificate_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "data").mkdir()
            (root / "data" / "config.toml").write_text("invalid [", encoding="utf-8")
            result = self.resolve_directory(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "data" / "secrets").exists())


class UnknownStudioDirectoryOptionsTests(unittest.TestCase):
    def test_ledger_directory_is_explicit_and_independent_of_physical_path(self):
        script = load_script("rehome_unknown_jav")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            arguments = ["--mappings", str(root / "mappings.csv"),
                         "--physical-unknown-root", str(root / "unassigned")]
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                script.build_parser().parse_args(arguments)
            self.assertEqual(caught.exception.code, 2)
            args = script.build_parser().parse_args([
                *arguments, "--ledger-unknown-root", r"F:\Library\unassigned"])
            self.assertEqual(args.ledger_unknown_root, r"F:\Library\unassigned")
            self.assertEqual(args.physical_unknown_root, root / "unassigned")


class EnglishNameSpacingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = load_script('space_media_names')
        cls.language = cls.script.model()

    def test_words_sequence_and_punctuation_are_preserved(self):
        self.assertEqual(self.script.spaced_name("01_NewYear'sgift.mp4", self.language),
                         "01_New Year's gift.mp4")
        self.assertEqual(self.script.spaced_name('02_Thisisanothermergedtitle.mp4', self.language),
                         '02_This is another merged title.mp4')

    def test_unrelated_extensions_and_unicode_are_preserved(self):
        for name in ('01_作品封面.jpg', 'FC2-PPV-123456.zip', '作品.mp4'):
            self.assertEqual(self.script.spaced_name(name, self.language), name)

    def test_titles_from_the_executed_batch_split_at_word_bounds(self):
        """2026-10-07 那批里切错过的真实片名。"""
        cases = {
            "29_Couldn'tresistherhotcowgirl,shegotacreampie.mp4":
                "29_Couldn't resist her hot cowgirl, she got a creampie.mp4",
            "56_HoldingmyhairsoIdon'tResist.mp4": "56_Holding my hair so I don't Resist.mp4",
            "54_Hisdickdoesn'tfitinmyMouth.mp4": "54_His dick doesn't fit in my Mouth.mp4",
            '48_oiledPOVfootjob.mp4': '48_oiled POV footjob.mp4',
            '40_PetitebrunettemakeshimCIMwithhotdeepthroat.mp4':
                '40_Petite brunette makes him CIM with hot deepthroat.mp4',
            '74_HUGELOADonherFaceandSwallowed.mp4': '74_HUGE LOAD on her Face and Swallowed.mp4',
            '36_Bitchwithplumpyredlips.mp4': '36_Bitch with plumpy red lips.mp4',
            '35_Hotbrunettetakesabigcockinherthroat.mp4': '35_Hot brunette takes a big cock in her throat.mp4',
            '52_Iinterruptedherfromaphotoshootwithahotdeepthroat.mp4':
                '52_I interrupted her from a photoshoot with a hot deepthroat.mp4',
            'stepsisstuckinwasher.mp4': 'stepsis stuck in washer.mp4',
            'SPANKPH6-SpankingherRoughly.mp4': 'SPANKPH6-Spanking her Roughly.mp4',
            'POVblowjobASMR4K.mp4': 'POV blowjob ASMR4K.mp4',
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.script.spaced_name(name, self.language), expected)

    def test_mixed_script_words_split_and_keep_their_characters(self):
        spaced = self.script.spaced_name('17_Filledwithсumallthethroat.mp4', self.language)
        self.assertEqual(spaced, '17_Filled with сum all the throat.mp4')
        self.assertIn('сum', spaced)

    def test_performer_names_stay_whole(self):
        for name, expected in (('IrinaSucksAndRides.mp4', 'Irina Sucks And Rides.mp4'),
                               ('SonaGetsFucked.mp4', 'Sona Gets Fucked.mp4')):
            with self.subTest(name=name):
                self.assertEqual(self.script.spaced_name(name, self.language), expected)

    def test_site_identifiers_are_left_whole(self):
        for name in ('ph5f8a9bcdef12345.mp4', 'xvideos.com_abcdefgh.mp4',
                     'viewkey=ph5f8a9bcdef12345.mp4'):
            with self.subTest(name=name):
                self.assertEqual(self.script.spaced_name(name, self.language), name)

    def test_recompute_batch_plans_only_mis_split_rows_and_apply_accepts_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            declared = PureWindowsPath('R:/media')
            canonical = lambda path: str(declared.joinpath(*path.relative_to(root).parts))
            translate = lambda path: root.joinpath(*PureWindowsPath(path).relative_to(declared).parts)
            db = fresh_ledger(root)
            wrong = root / "29_Could n't resist her.mp4"
            right = root / '02_Throat fucking.mp4'
            moved = root / '03_Sloppy throat.mp4'
            for path in (wrong, right):
                path.write_bytes(b'media')
            with closing(sqlite3.connect(db)) as connection:
                for asset_id, path in ((1, wrong), (2, right), (3, root / 'elsewhere' / moved.name)):
                    connection.execute('INSERT INTO asset(id,location,path,name,medium,size) VALUES(?,?,?,?,?,?)',
                                       (asset_id, 'local', canonical(path), path.name, 'video', 5))
                connection.commit()
            log = root / 'organize-batch.json'
            # 第 1 条在批次之后被目录整理从 New 搬到了 root：按 asset_id 找当前行。
            log.write_text(json.dumps({'entries': [
                {'asset_id': 1, 'old_path': canonical(root / 'New' / "29_Couldn'tresisther.mp4"),
                 'new_path': canonical(root / 'New' / wrong.name)},
                {'asset_id': 2, 'old_path': canonical(root / '02_Throatfucking.mp4'), 'new_path': canonical(right)},
                {'asset_id': 3, 'old_path': canonical(root / '03_Sloppythroatt.mp4'), 'new_path': canonical(moved)},
            ]}), encoding='utf-8')
            review = root / 'recompute.csv'
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(self.script.main(['--db', str(db), '--root', str(declared),
                                                   '--recompute-batch', str(log), '--out', str(review)]), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report['planned'], 1)
            self.assertEqual([item['asset_id'] for item in report['skipped']], [3])
            rows = self.script.read_rows(review)
            self.assertEqual([(row['asset_id'], PureWindowsPath(row['target_path']).name) for row in rows],
                             [('1', "29_Couldn't resist her.mp4")])
            self.assertTrue(wrong.exists())

            with mock.patch.object(self.script, 'location_roots', return_value={'local': [str(declared)]}), \
                    mock.patch.object(self.script, 'translate_ledger_path', side_effect=translate), \
                    mock.patch.object(self.script.organize, 'translate_ledger_path', side_effect=translate), \
                    mock.patch.object(self.script, 'GENERATED_DIR', root / 'generated'), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(self.script.main(['--db', str(db), '--root', str(declared), '--review-csv', str(review),
                                                   '--apply', '--backup', str(root / 'backup.db')]), 0)
            self.assertFalse(wrong.exists())
            self.assertTrue((root / "29_Couldn't resist her.mp4").exists())

    def test_apply_rejects_character_changes_before_opening_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            declared = PureWindowsPath('R:/media')
            canonical = lambda path: str(declared.joinpath(*path.relative_to(root).parts))
            translate = lambda path: root.joinpath(*PureWindowsPath(path).relative_to(declared).parts)
            db = fresh_ledger(root)
            source = root / 'original.mp4'
            source.write_bytes(b'media')
            with closing(sqlite3.connect(db)) as connection:
                connection.execute('INSERT INTO asset(id,location,path,name,medium,size) VALUES(1,?,?,?,?,?)',
                                   ('local', canonical(source), source.name, 'video', 5))
                connection.commit()
            review = root / 'names.csv'
            row = dict(asset_id=1, location='local', current_path=canonical(source), target_path=canonical(root / 'invented.mp4'),
                       action='rename', reason='', size=5, original_name=source.name, new_name='invented.mp4')
            self.script.write_rows(review, self.script.FIELDS, [row])
            with mock.patch.object(self.script, 'open_for_write') as writer, \
                    mock.patch.object(self.script, 'location_roots', return_value={'local': [str(declared)]}), \
                    mock.patch.object(self.script, 'translate_ledger_path', side_effect=translate):
                with self.assertRaisesRegex(ValueError, '改变了内容字符'):
                    self.script.main(['--db', str(db), '--root', str(declared), '--review-csv', str(review),
                                      '--apply', '--backup', str(root / 'backup.db')])
                writer.assert_not_called()
            self.assertEqual(source.read_bytes(), b'media')


class LibraryDirectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = load_script('organize_library_dirs')

    def test_directory_plan_includes_mounted_cloud_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            ledger = fresh_ledger(root)
            locations = ('guangya', 'onedrive', 'mounted')
            roots = {location: [rf'G:\{location}'] for location in locations}
            sources = {location: rf'G:\{location}\1pon_092415_001-fhd' for location in locations}
            with closing(sqlite3.connect(ledger)) as db:
                for asset_id, location in enumerate(locations, 1):
                    db.execute('INSERT INTO asset(id,location,path,name,medium,code,studio) VALUES(?,?,?,?,?,?,?)',
                        (asset_id, location, sources[location] + r'\film.mp4', 'film.mp4',
                         'video', '092415-001', '一本道'))
                db.commit()
            with mock.patch.object(self.script, 'DATABASE_PATH', ledger), \
                    mock.patch.object(self.script, 'OUT', root), \
                    mock.patch.object(self.script, 'location_roots', return_value=roots), \
                    mock.patch.object(self.script, 'root_online', return_value=True), redirect_stdout(io.StringIO()):
                self.script.build_plan('rehome', 'mounted')
            plan = json.loads((root / 'rehome-mounted-manifest.json').read_text(encoding='utf-8'))
            self.assertEqual({op['source'] for op in plan['operations']}, set(sources.values()))

    def test_template_cli_accepts_mounted_cloud_sources(self):
        script = load_script('organize_media')
        for location in ('guangya', 'onedrive', 'mounted'):
            with self.subTest(location=location):
                self.assertEqual(script.build_parser().parse_args(['--location', location]).location, location)

    def test_release_pack_with_unattributed_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            ledger = fresh_ledger(root)
            source = r'B:\MVP\1pon-092415_001-fhd'
            with closing(sqlite3.connect(ledger)) as db:
                for aid, tail, medium, studio in (
                        (1, '1pon-092415-001-fhd1_(new).mp4', 'video', '一本道'),
                        (2, '1pon-092415-001-fhd2_(new).mp4', 'video', '一本道'),
                        (3, r'img\001.jpg', 'image', None)):
                    db.execute('INSERT INTO asset(id,location,path,name,medium,code,studio) VALUES(?,?,?,?,?,?,?)',
                               (aid, '115', source + '\\' + tail, tail, medium, '092415-001', studio))
                db.commit()
            with mock.patch.object(self.script, 'DATABASE_PATH', ledger), \
                    mock.patch.object(self.script, 'OUT', root), \
                    mock.patch.object(self.script, 'location_roots', return_value={'115': ['B:\\']}), \
                    mock.patch.object(self.script, 'root_online', return_value=True), redirect_stdout(io.StringIO()):
                self.script.build_plan('rehome', 'example')
                plan = json.loads((root / 'rehome-example-manifest.json').read_text(encoding='utf-8'))
                self.assertEqual(len(plan['operations']), 1)
                op = plan['operations'][0]
                self.assertEqual(op['source'], source)
                self.assertEqual(op['target'], r'B:\日本\一本道\092415-001')
                self.assertEqual([row['id'] for row in op['rows']], [1, 2, 3])
                with closing(self.script.open_readonly(ledger)) as db:
                    self.assertEqual(op['entities'], self.script.entity_guard(db, source))
                with closing(sqlite3.connect(ledger)) as db:
                    db.execute('INSERT INTO asset(id,location,path,name,medium) VALUES(4,?,?,?,?)',
                               ('115', source + r'\unknown.mp4', 'unknown.mp4', 'video'))
                    db.commit()
                self.script.build_plan('rehome', 'mixed')
                mixed = json.loads((root / 'rehome-mixed-manifest.json').read_text(encoding='utf-8'))
                self.assertEqual(len(mixed['operations']), 1)
                self.assertEqual(mixed['operations'][0]['kind'], 'files')
                self.assertEqual([r['id'] for r in mixed['operations'][0]['rows']], [1, 2])

    def test_sidecar_identity_conflicts_and_date_publishers(self):
        script = self.script
        owner = ('release', '日本', '一本道', '092415-001')
        image = dict(id=2, medium='image', disposal=None, code='092415-001', studio=None,
                     creator=None, effective_region='jp')
        self.assertTrue(script.compatible_sidecar(image, owner, {}))
        for update in ({'code': '092416-001'}, {'studio': '别的厂牌'},
                       {'creator': '独立作者'}, {'effective_region': 'kr'}, {'medium': 'video'}):
            self.assertFalse(script.compatible_sidecar(dict(image, **update), owner, {}))
        self.assertFalse(script.compatible_sidecar(image, owner, {2: [(9, '独立作者')]}))
        self.assertTrue(script.release_directory_matches('1pon-092415_001-fhd', owner))
        self.assertFalse(script.release_directory_matches('1pon-092416_001-fhd', owner))
        self.assertFalse(script.release_directory_matches('092415_001', ('release', '日本', '加勒比', '092415-001')))
        fc2 = ('release', '日本', 'FC2', 'FC2-PPV-389339')
        self.assertTrue(script.release_directory_matches('FC-389339最高峰 市島亜美', fc2))
        self.assertFalse(script.release_directory_matches('FC-3893390', fc2))

    def test_creator_subtype_and_korean_publisher_destinations(self):
        script = self.script
        row = dict(id=1, disposal=None, medium='video', code=None, studio=None,
                   name='film.mp4', creator='account', effective_region='')
        self.assertEqual(script.label(row, {1: [(9, 'account')]}, {}, {9}),
                         ('creator', '创作者', 'account'))
        self.assertEqual(script.label(row, {1: [(9, 'account')]}, {9: ['网黄博主']}, set()),
                         ('creator', '网黄博主', 'account'))
        self.assertEqual(script.label(dict(row, studio='MIB', code='AR-101', effective_region='kr'), {}, {}, set()),
                         ('publisher', '韩国', 'MIB'))
        image = dict(row, medium='image', creator=None)
        self.assertTrue(script.compatible_sidecar(image, ('creator', '创作者', 'account'), {}))
        self.assertFalse(script.compatible_sidecar(dict(image, code='ABP-123'), ('creator', '创作者', 'account'), {}))

    def test_directory_execution_preserves_sidecars_and_recovers(self):
        with redirect_stdout(io.StringIO()):
            self.script.self_check()

    def test_pikpak_collapse_staging_preserves_complete_recovery(self):
        script = self.script
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, target = r'A:\outer\outer', r'A:\outer'
            def translate(path):
                return root.joinpath(*PureWindowsPath(path).parts[1:]) if PureWindowsPath(path).drive.casefold() == 'a:' else Path(path)
            translate(source).mkdir(parents=True)
            media = translate(source) / 'film.mp4'
            media.write_bytes(b'media')
            with mock.patch.object(script, 'translate_ledger_path', side_effect=translate):
                for steps in (0, 1, 2):
                    pairs, state = script.collapse_moves(source, target)
                    self.assertTrue(Path(state['temporary']).name.startswith('peach-organize-'))
                    entry = dict(moves=pairs, collapse=state)
                    for old, new in pairs[:steps]:
                        script.organize._rename(old, new)
                    self.assertEqual(script.restore_entry(entry), [])
                    self.assertEqual(media.read_bytes(), b'media')
                    self.assertFalse(Path(state['temporary']).exists())

    def test_provider_move_separates_same_parent_rename_and_cross_parent_move(self):
        script = self.script
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, target = r'A:\old\account', r'A:\new\collection'
            def translate(path):
                return root.joinpath(*PureWindowsPath(path).parts[1:])
            translate(source).mkdir(parents=True)
            translate(target).parent.mkdir()
            media = translate(source) / 'film.mp4'
            media.write_bytes(b'media')
            with mock.patch.object(script, 'translate_ledger_path', side_effect=translate):
                pairs = script.provider_moves([(source,target)], 'A:\\')
                self.assertEqual(pairs, [(source,r'A:\old\collection'),(r'A:\old\collection',target)])
                for steps in (0, 1, 2):
                    for old, new in pairs[:steps]:
                        translate(old).rename(translate(new))
                    with mock.patch.object(script.organize, '_rename', side_effect=lambda old,new:translate(old).rename(translate(new))):
                        self.assertEqual(script.restore(pairs), [])
                    self.assertEqual(media.read_bytes(), b'media')
                translate(r'A:\old\collection').mkdir()
                with self.assertRaises(FileExistsError):
                    script.provider_moves([(source,target)], 'A:\\')

    def test_fc_short_parts_require_matching_parent_and_publisher(self):
        row = dict(code=None, studio=None, name='@fc1780822_1.mp4',
                   path=r'B:\MVP\FC2 collection\FC-1780822耐久黑田4K\@fc1780822_1.mp4')
        self.assertEqual(self.script.release_code(row), 'FC2-PPV-1780822')
        self.assertEqual(self.script.release_code(dict(row,path=r'B:\unconfirmed\@fc1780822_1.mp4')), '')
        self.assertEqual(self.script.release_code(dict(row,path=r'B:\FC-1780823\@fc1780822_1.mp4')), '')
        self.assertEqual(self.script.release_code(dict(row,studio='Prestige')), '')
        self.assertIsNone(row['code'])

    def test_flatten_plan_generates_its_own_csv(self):
        script = self.script
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            ledger = fresh_ledger(root)
            source = r'B:\ABC-123\ABC-123'
            media = root / 'media'
            def translate(path):
                return media.joinpath(*PureWindowsPath(path).parts[1:])
            translate(source).mkdir(parents=True)
            translate(source + r'\film.mp4').write_bytes(b'film')
            with closing(sqlite3.connect(ledger)) as db:
                db.execute('INSERT INTO asset(id,location,path,name,medium) VALUES(1,?,?,?,?)',
                           ('115',source+r'\film.mp4','film.mp4','video'))
                db.commit()
            with mock.patch.object(script, 'DATABASE_PATH', ledger), mock.patch.object(script, 'OUT', root), \
                    mock.patch.object(script, 'location_roots', return_value={'115':['B:\\']}), \
                    mock.patch.object(script, 'translate_ledger_path', side_effect=translate), \
                    redirect_stdout(io.StringIO()):
                script.build_plan('flatten', 'example')
            manifest = json.loads((root/'flatten-example-manifest.json').read_text(encoding='utf-8'))
            self.assertEqual([(op['kind'],op['source'],op['target']) for op in manifest['operations']],
                             [('collapse',source,r'B:\ABC-123')])
            self.assertTrue((root/'flatten-example-plan.csv').is_file())

    def test_mixed_file_group_and_database_failure_restore(self):
        script = self.script
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            ledger = fresh_ledger(root)
            media = root / 'media'
            source = r'B:\MVP\collection'
            target = r'B:\日本\FC2\FC2-PPV-1234567'
            def translate(path):
                return media.joinpath(*PureWindowsPath(path).parts[1:])
            film = source + r'\FC2-PPV-1234567.mp4'
            translate(film).parent.mkdir(parents=True)
            translate(film).write_bytes(b'film')
            subtitle = source + r'\FC2-PPV-1234567.srt'
            translate(subtitle).write_bytes(b'subtitle')
            translate(source + r'\unknown.mp4').write_bytes(b'other')
            with closing(sqlite3.connect(ledger)) as db:
                db.row_factory = sqlite3.Row
                db.execute('INSERT INTO asset(id,location,path,name,medium,code) VALUES(1,?,?,?,?,?)',
                           ('115', film, 'FC2-PPV-1234567.mp4', 'video', 'FC2-PPV-1234567'))
                db.execute('INSERT INTO asset(id,location,path,name,medium) VALUES(2,?,?,?,?)',
                           ('115', source + r'\unknown.mp4', 'unknown.mp4', 'video'))
                db.execute("INSERT INTO asset_subtitle(id,asset_id,location,path,name,format,pairing,first_seen,last_seen) VALUES(1,1,?,?,?,'srt','exact','2026-10-07','2026-10-07')",
                           ('115', subtitle, PureWindowsPath(subtitle).name))
                db.execute('INSERT INTO asset(id,location,path,name,medium) VALUES(3,?,?,?,?)',
                           ('115', subtitle, PureWindowsPath(subtitle).name, 'other'))
                db.commit()
                op = script.operation('files', source, target, script.snapshots(db, source, [1, 3]), '已知作品')
                op['subtitles'] = script.subtitles(db, source)
                op['entities'] = script.entity_guard(db, source, [1, 3])
                db.execute("CREATE TRIGGER refuse_path BEFORE UPDATE OF path ON asset BEGIN SELECT RAISE(ABORT,'refuse'); END")
                db.commit()
            payload = dict(format=1, stage='rehome', roots={'115': ['B:\\']}, operations=[op])
            script.save(root / 'rehome-example-manifest.json', payload)
            with mock.patch.object(script, 'DATABASE_PATH', ledger), mock.patch.object(script, 'OUT', root), \
                    mock.patch.object(script, 'translate_ledger_path', side_effect=translate), \
                    mock.patch.object(script, 'root_online', return_value=True), \
                    mock.patch.object(script.organize, '_rename', side_effect=lambda old, new: (translate(new).parent.mkdir(parents=True, exist_ok=True), translate(old).rename(translate(new)))), \
                    redirect_stdout(io.StringIO()):
                script.run_apply('rehome', root / 'backup1.db', 10, 20, batch='example')
                receipt = json.loads((root / 'rehome-example-receipt.json').read_text(encoding='utf-8'))
                self.assertEqual(receipt['entries'][0]['status'], 'restored')
                self.assertEqual(translate(film).read_bytes(), b'film')
                self.assertEqual(translate(subtitle).read_bytes(), b'subtitle')
                self.assertFalse(translate(target + r'\FC2-PPV-1234567.mp4').exists())
                with closing(sqlite3.connect(ledger)) as db:
                    self.assertEqual(db.execute('SELECT path FROM asset WHERE id=1').fetchone()[0], film)
                    db.execute('DROP TRIGGER refuse_path')
                    db.commit()
                script.run_apply('rehome', root / 'backup2.db', 10, 20, retry_failed=True, batch='example')
                self.assertEqual(translate(target + r'\FC2-PPV-1234567.mp4').read_bytes(), b'film')
                self.assertEqual(translate(target + r'\FC2-PPV-1234567.srt').read_bytes(), b'subtitle')
                self.assertEqual(translate(source + r'\unknown.mp4').read_bytes(), b'other')
                with closing(sqlite3.connect(ledger)) as db:
                    self.assertEqual(db.execute('SELECT path FROM asset WHERE id=1').fetchone()[0], target + r'\FC2-PPV-1234567.mp4')
                    self.assertEqual(db.execute('SELECT path FROM asset_subtitle WHERE id=1').fetchone()[0], target + r'\FC2-PPV-1234567.srt')
                    self.assertEqual(db.execute('SELECT path FROM asset WHERE id=3').fetchone()[0], target + r'\FC2-PPV-1234567.srt')


    @staticmethod
    def _media(root):
        """`B:` 账本路径落到临时目录；整目录移动的回执记本机路径，原样使用。"""
        media = root / 'media'
        def translate(path):
            if PureWindowsPath(path).drive.casefold() == 'b:':
                return media.joinpath(*PureWindowsPath(path).parts[1:])
            return Path(PureWindowsPath(path).as_posix())
        def rename(old, new):
            translate(new).parent.mkdir(parents=True, exist_ok=True)
            translate(old).rename(translate(new))
        return translate, rename

    def _sidecar_batch(self, root, names, rows, stem):
        """`B:\\pack` 里放好 `names`；`rows` 是 (id, 名字, 是否随本组移动) 的账本行。"""
        script = self.script
        ledger = fresh_ledger(root)
        source, target = r'B:\pack', r'B:\日本\FC2\FC2-PPV-1234567'
        translate, rename = self._media(root)
        for name in names:
            translate(source + '\\' + name).parent.mkdir(parents=True, exist_ok=True)
            translate(source + '\\' + name).write_bytes(name.encode())
        with closing(sqlite3.connect(ledger)) as db:
            db.row_factory = sqlite3.Row
            for aid, name, _ in rows:
                db.execute('INSERT INTO asset(id,location,path,name,medium) VALUES(?,?,?,?,?)',
                           (aid, '115', source + '\\' + name, name, medium_of(name)))
            db.commit()
            ids = [aid for aid, _, moving in rows if moving]
            op = script.operation('files', source, target, script.snapshots(db, source, ids), '已知作品')
            op['subtitles'], op['entities'] = [], script.entity_guard(db, source, ids)
        script.save(root / f'rehome-{stem}-manifest.json',
                    dict(format=1, stage='rehome', roots={'115': ['B:\\']}, operations=[op]))
        with mock.patch.object(script, 'DATABASE_PATH', ledger), mock.patch.object(script, 'OUT', root), \
                mock.patch.object(script, 'translate_ledger_path', side_effect=translate), \
                mock.patch.object(script, 'root_online', return_value=True), \
                mock.patch.object(script.organize, '_rename', side_effect=rename), redirect_stdout(io.StringIO()):
            script.run_apply('rehome', root / 'backup.db', 10, 20, batch=stem)
        receipt = json.loads((root / f'rehome-{stem}-receipt.json').read_text(encoding='utf-8'))
        return receipt['entries'][0], (lambda name: translate(source + '\\' + name)), \
            (lambda name: translate(target + '\\' + name))

    def test_file_group_takes_its_own_sidecars_and_reports_what_stays(self):
        quarantine = 'peach-purge-' + '0' * 32 + '.peach-quarantine'
        names = ['FC2-PPV-1234567.mp4', 'FC2-PPV-1234567-poster.jpg', 'FC2-PPV-1234567.nfo',
                 'FC2-PPV-1234567-thumb.jpg', 'other.mp4', 'other-fanart.jpg', 'poster.jpg',
                 r'extrafanart\1.jpg', quarantine]
        rows = [(1, 'FC2-PPV-1234567.mp4', True), (2, 'other.mp4', False),
                (3, 'FC2-PPV-1234567-thumb.jpg', False)]
        with tempfile.TemporaryDirectory() as directory:
            entry, old, new = self._sidecar_batch(Path(directory).resolve(), names, rows, 'partial')
            self.assertEqual(entry['status'], 'committed')
            for name in ('FC2-PPV-1234567.mp4', 'FC2-PPV-1234567-poster.jpg', 'FC2-PPV-1234567.nfo'):
                self.assertTrue(new(name).is_file(), name)
                self.assertFalse(old(name).exists(), name)
            # 登记过的图、别的视频的附属、目录级附属与隔离文件都留在原处。
            for name in ('FC2-PPV-1234567-thumb.jpg', 'other.mp4', 'other-fanart.jpg', 'poster.jpg',
                         r'extrafanart\1.jpg', quarantine):
                self.assertTrue(old(name).is_file(), name)
            [cleanup] = entry['cleanup_errors']
            self.assertEqual(cleanup['path'], r'B:\pack')
            self.assertIn('poster.jpg', cleanup['remaining'])

    def test_file_group_that_takes_every_video_empties_the_directory(self):
        names = ['FC2-PPV-1234567.mp4', 'poster.jpg', 'movie.nfo', r'extrafanart\1.jpg',
                 'FC2-PPV-1234567.mp4_thumbs.jpg']
        with tempfile.TemporaryDirectory() as directory:
            entry, old, new = self._sidecar_batch(Path(directory).resolve(), names,
                                                  [(1, 'FC2-PPV-1234567.mp4', True)], 'whole')
            self.assertEqual(entry['status'], 'committed')
            for name in names:
                self.assertTrue(new(name).is_file(), name)
            self.assertFalse(old('').exists())
            self.assertNotIn('cleanup_errors', entry)

    def test_supported_moves_reset_the_source_error_circuit(self):
        script=self.script
        for outcomes,expected in [([False,True,False,True,False,True],6),([False,False,False,True],3)]:
            with self.subTest(outcomes=outcomes), tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve()
                ledger=fresh_ledger(root)
                operations=[script.operation('rename',rf'A:\source{i}',rf'A:\creators\source{i}',[],'已确认归属') for i in range(len(outcomes))]
                script.save(root/'rehome-circuit-manifest.json',dict(format=1,stage='rehome',roots={'pikpak':['A:\\']},operations=operations))
                calls=[]
                def backend(db,payload,op,stage,receipt,path,deadline,retry,*,calls=calls,outcomes=outcomes):
                    index=len(calls);calls.append(op['key'])
                    if outcomes[index]:
                        receipt['entries'].append(dict(key=op['key'],status='committed'))
                        return None
                    error=OSError('不支持该请求');error.winerror=50
                    receipt['failures'].append(dict(key=op['key'],source=op['source'],error=str(error)))
                    return error
                with mock.patch.object(script,'DATABASE_PATH',ledger),mock.patch.object(script,'OUT',root), \
                        mock.patch.object(script,'execute_operation',side_effect=backend),redirect_stdout(io.StringIO()), \
                        script.organize.verified_renames('pikpak', lambda source, target: None):
                    script.run_apply('rehome',root/'backup.db',100,20,location='pikpak',batch='circuit')
                self.assertEqual(len(calls),expected)

    def test_pikpak_operations_need_the_verified_channel(self):
        script = self.script
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            ledger = fresh_ledger(root)
            operations = [script.operation('rename', r'A:\source', r'A:\creators\source', [], '已确认归属'),
                          script.operation('rename', r'B:\source', r'B:\creators\source', [], '已确认归属')]
            script.save(root/'rehome-mixed-manifest.json',
                        dict(format=1, stage='rehome', roots={'pikpak': ['A:\\'], '115': ['B:\\']}, operations=operations))
            with mock.patch.object(script, 'DATABASE_PATH', ledger), mock.patch.object(script, 'OUT', root), \
                    mock.patch.object(script, 'execute_operation') as backend, redirect_stdout(io.StringIO()):
                for location in (None, 'pikpak'):
                    with self.subTest(location=location), self.assertRaisesRegex(ValueError, '--pikpak-webdav'):
                        script.run_apply('rehome', root/'backup.db', 10, 20, location=location, batch='mixed')
                backend.assert_not_called()
                self.assertFalse((root/'backup.db').exists())
                backend.return_value = None
                script.run_apply('rehome', root/'backup.db', 10, 20, location='115', batch='mixed')
                self.assertEqual([c.args[2]['source'] for c in backend.call_args_list], [r'B:\source'])

    def _provider_batch(self, root, outcome):
        """两条 115 目录操作；`outcome(source)` 决定第一条的改名结果。"""
        script = self.script
        ledger = fresh_ledger(root)
        translate, move = self._media(root)
        operations = []
        with closing(sqlite3.connect(ledger)) as db:
            db.row_factory = sqlite3.Row
            for aid in (1, 2):
                source = rf'B:\src{aid}'
                translate(source + r'\film.mp4').parent.mkdir(parents=True)
                translate(source + r'\film.mp4').write_bytes(b'film')
                db.execute('INSERT INTO asset(id,location,path,name,medium) VALUES(?,?,?,?,?)',
                           (aid, '115', source + r'\film.mp4', 'film.mp4', 'video'))
                db.commit()
                op = script.operation('rename', source, rf'B:\dst\src{aid}', script.snapshots(db, source), '已确认归属')
                op['subtitles'], op['entities'] = [], script.entity_guard(db, source)
                operations.append(op)
        script.save(root/'rehome-provider-manifest.json',
                    dict(format=1, stage='rehome', roots={'115': ['B:\\']}, operations=operations))
        calls = []
        def rename(old, new):
            calls.append(translate(old).name)
            if translate(old) == translate(r'B:\src1') and translate(new) == translate(r'B:\dst\src1'):
                outcome(old)
            move(old, new)
        patches = (mock.patch.object(script, 'DATABASE_PATH', ledger), mock.patch.object(script, 'OUT', root),
                   mock.patch.object(script, 'translate_ledger_path', side_effect=translate),
                   mock.patch.object(script, 'root_online', return_value=True),
                   mock.patch.object(script.organize, '_rename', side_effect=rename))
        return ledger, translate, calls, operations, patches

    def test_provider_rejection_is_recorded_and_skipped_on_resume(self):
        from peach.organize_clouddrive import MoveNotExecuted
        script = self.script
        def reject(source):
            raise MoveNotExecuted('官方接口拒绝')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            ledger, translate, calls, operations, patches = self._provider_batch(root, reject)
            with patches[0], patches[1], patches[2], patches[3], patches[4], redirect_stdout(io.StringIO()):
                script.run_apply('rehome', root/'backup1.db', 10, 20, batch='provider')
                script.run_apply('rehome', root/'backup2.db', 10, 20, batch='provider')
            receipt = json.loads((root/'rehome-provider-receipt.json').read_text(encoding='utf-8'))
            self.assertEqual([f['key'] for f in receipt['failures']], [operations[0]['key']])
            self.assertEqual([(e['key'], e['status']) for e in receipt['entries']],
                             [(operations[0]['key'], 'restored'), (operations[1]['key'], 'committed')])
            self.assertEqual(calls, ['src1', 'src2'])
            self.assertEqual(translate(r'B:\src1\film.mp4').read_bytes(), b'film')
            with closing(sqlite3.connect(ledger)) as db:
                self.assertEqual(db.execute('SELECT path FROM asset ORDER BY id').fetchall(),
                                 [(r'B:\src1\film.mp4',), (r'B:\dst\src2\film.mp4',)])

    def test_unknown_provider_state_is_recorded_and_stops_the_batch(self):
        script = self.script
        def unknown(source):
            raise script.UnconfirmedMove('移动状态未确认')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            ledger, translate, calls, operations, patches = self._provider_batch(root, unknown)
            with patches[0], patches[1], patches[2], patches[3], patches[4], redirect_stdout(io.StringIO()):
                with self.assertRaises(script.UnconfirmedMove):
                    script.run_apply('rehome', root/'backup1.db', 10, 20, batch='provider')
                receipt = json.loads((root/'rehome-provider-receipt.json').read_text(encoding='utf-8'))
                self.assertEqual(receipt['failures'][0]['key'], operations[0]['key'])
                self.assertTrue(receipt['failures'][0]['unconfirmed'])
                self.assertEqual([e['status'] for e in receipt['entries']], ['intent'])
                self.assertEqual(calls, ['src1'])
                script.run_apply('rehome', root/'backup2.db', 10, 20, batch='provider')
            receipt = json.loads((root/'rehome-provider-receipt.json').read_text(encoding='utf-8'))
            self.assertEqual([e['status'] for e in receipt['entries']], ['restored', 'committed'])
            self.assertEqual(calls, ['src1', 'src2'])
            with closing(sqlite3.connect(ledger)) as db:
                self.assertEqual(db.execute('SELECT path FROM asset WHERE id=1').fetchone()[0], r'B:\src1\film.mp4')

    def test_committed_batch_rolls_back_in_reverse_and_reports_edited_rows(self):
        script = self.script
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            ledger, translate, calls, operations, patches = self._provider_batch(root, lambda source: None)
            with patches[0], patches[1], patches[2], patches[3], patches[4], redirect_stdout(io.StringIO()):
                script.run_apply('rehome', root/'backup1.db', 10, 20, batch='provider')
                with closing(sqlite3.connect(ledger)) as db:
                    db.execute('UPDATE asset SET path=? WHERE id=2', (r'B:\elsewhere\film.mp4',))
                    db.commit()
                plan = script.run_rollback('rehome', 'provider')
                self.assertEqual([(p['source'], bool(p['changed'])) for p in plan],
                                 [(r'B:\dst\src2', True), (r'B:\dst\src1', False)])
                self.assertTrue(translate(r'B:\dst\src1\film.mp4').is_file())
                results = script.run_rollback('rehome', 'provider', root/'backup2.db', apply=True)
                self.assertEqual([r['status'] for r in results], ['changed', 'rolled_back'])
                script.run_apply('rehome', root/'backup3.db', 10, 20, batch='provider')
            self.assertEqual(translate(r'B:\src1\film.mp4').read_bytes(), b'film')
            self.assertTrue(translate(r'B:\dst\src2\film.mp4').is_file())
            receipt = json.loads((root/'rehome-provider-receipt.json').read_text(encoding='utf-8'))
            self.assertEqual([e['status'] for e in receipt['entries']], ['rolled_back', 'committed'])
            with closing(sqlite3.connect(ledger)) as db:
                self.assertEqual(db.execute('SELECT path FROM asset ORDER BY id').fetchall(),
                                 [(r'B:\src1\film.mp4',), (r'B:\elsewhere\film.mp4',)])

    def test_fc2_promotional_prefixes_and_platform_named_parts(self):
        row = dict(code=None, studio=None, name='www.98T.la@FC2-1314799-CD1.mp4',
                   path=r'B:\番号\FC2-PPV\www.98T.la@FC2-1314799-CD1.mp4')
        self.assertEqual(self.script.release_code(row), 'FC2-PPV-1314799')
        for name in ('3933828早期購入.mp4', '3933828本編.mp4'):
            part = dict(row, name=name, path=str(PureWindowsPath(r'B:\番号\FC2-PPV', name)))
            self.assertEqual(self.script.release_code(part), 'FC2-PPV-3933828')
            canonical = dict(part, path=str(PureWindowsPath(r'B:\日本\FC2\FC2-PPV-3933828', name)))
            self.assertEqual(self.script.release_code(canonical), 'FC2-PPV-3933828')
            self.assertEqual(self.script.release_code(dict(canonical, path=str(PureWindowsPath(r'B:\日本\FC2\FC2-PPV-3933829', name)))), '')
            self.assertEqual(self.script.release_code(dict(part, studio='Prestige')), '')
            self.assertEqual(self.script.release_code(dict(part, path=str(PureWindowsPath(r'B:\unknown', name)))), '')
        self.assertEqual(self.script.release_code(dict(row, name='20250105.mp4')), '')
        self.assertIsNone(row['code'])

    def test_mixed_creator_files_keep_unknown_media_and_original_structure(self):
        from peach.entity_classification import write_claim
        script = self.script
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            ledger = fresh_ledger(root)
            source = r'B:\MVP\shared collection'
            with closing(sqlite3.connect(ledger)) as db:
                for aid,name in [(1,'known.mp4'),(2,'unknown.mp4')]:
                    db.execute('INSERT INTO asset(id,location,path,name,medium) VALUES(?,?,?,?,?)',
                               (aid,'115',source+'\\'+name,name,'video'))
                db.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) VALUES(9,'creator','account','account','2026-10-07','2026-10-07')")
                db.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source) VALUES(1,9,'creator','test')")
                write_claim(db,entity_id=9,facet='identity',value='unknown',source='test',evidence='归属关系存在，身份待复核')
                db.commit()
            with mock.patch.object(script,'DATABASE_PATH',ledger), mock.patch.object(script,'OUT',root), \
                    mock.patch.object(script,'location_roots',return_value={'115':['B:\\']}), \
                    mock.patch.object(script,'root_online',return_value=True), redirect_stdout(io.StringIO()):
                script.build_plan('rehome','mixed-creators')
            plan=json.loads((root/'rehome-mixed-creators-manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(len(plan['operations']),1)
            op=plan['operations'][0]
            self.assertEqual((op['kind'],op['source'],op['target']),
                             ('files',source,r'B:\创作者\account\shared collection'))
            self.assertEqual([row['id'] for row in op['rows']],[1])
            self.assertEqual(plan['skipped'],[dict(source=source+r'\unknown.mp4',reason='归属未确认，保留原位置')])
            with closing(sqlite3.connect(ledger)) as db:
                self.assertIsNone(db.execute('SELECT creator FROM asset WHERE id=1').fetchone()[0])

    def _rehome_plan(self, root, rows, creators=()):
        """`rows` 是 (id, 路径, 番号, 厂牌)；`creators` 是 (资产 id, 创作者名)。返回冻结计划。"""
        script = self.script
        ledger = fresh_ledger(root)
        with closing(sqlite3.connect(ledger)) as db:
            for aid, path, code, studio in rows:
                name = PureWindowsPath(path).name
                db.execute('INSERT INTO asset(id,location,path,name,medium,code,studio) VALUES(?,?,?,?,?,?,?)',
                           (aid, '115', path, name, 'video', code, studio))
            entities = {name: eid for eid, name in enumerate(dict.fromkeys(name for _, name in creators), 100)}
            for name, eid in entities.items():
                db.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,updated_at) VALUES(?,'creator',?,?,'2026-10-09','2026-10-09')",
                           (eid, name, name.casefold()))
            for aid, name in creators:
                db.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source) VALUES(?,?,'creator','test')", (aid, entities[name]))
            db.commit()
        with mock.patch.object(script, 'DATABASE_PATH', ledger), mock.patch.object(script, 'OUT', root), \
                mock.patch.object(script, 'location_roots', return_value={'115': ['B:\\']}), \
                mock.patch.object(script, 'root_online', return_value=True), redirect_stdout(io.StringIO()):
            script.build_plan('rehome', 'example')
        return json.loads((root / 'rehome-example-manifest.json').read_text(encoding='utf-8'))

    def test_creator_targets_drop_containers_above_the_canonical_layer(self):
        rows = [(1, r'B:\Downloads\sundome05\a.mp4', None, None),
                (2, r'B:\Downloads\sundome05\2024\b.mp4', None, None),
                (3, r'B:\Pack From Shared\kuroki collection\c.mp4', None, None)]
        with tempfile.TemporaryDirectory() as directory:
            plan = self._rehome_plan(Path(directory).resolve(), rows,
                                     [(1, 'sundome05'), (2, 'sundome05'), (3, 'kuroki')])
        self.assertEqual(sorted((op['kind'], op['source'], op['target']) for op in plan['operations']),
                         [('rename', r'B:\Downloads\sundome05', r'B:\创作者\sundome05'),
                          ('rename', r'B:\Pack From Shared\kuroki collection', r'B:\创作者\kuroki\kuroki collection')])

    def test_dated_release_joins_the_existing_equivalent_directory(self):
        rows = [(1, r'B:\日本\一本道\092415_001\1pondo-092415_001-FHD.mp4', '092415_001', '一本道'),
                (2, r'B:\incoming\1pon-092415-001-fhd\1pon-092415-001-fhd1_(new).mp4', '092415-001', '一本道'),
                (3, r'B:\日本\一本道\092415-001\1pon-092415-001-fhd2_(new).mp4', '092415-001', '一本道')]
        with tempfile.TemporaryDirectory() as directory:
            plan = self._rehome_plan(Path(directory).resolve(), rows)
        self.assertEqual(sorted((op['source'], op['target']) for op in plan['operations']),
                         [(r'B:\incoming\1pon-092415-001-fhd', r'B:\日本\一本道\092415_001'),
                          (r'B:\日本\一本道\092415-001', r'B:\日本\一本道\092415_001')])

    def test_release_whose_file_names_another_code_stays_for_review(self):
        path = r'B:\incoming\122614-947\122614_001-1pon-whole1_hd.avi'
        with tempfile.TemporaryDirectory() as directory:
            plan = self._rehome_plan(Path(directory).resolve(), [(1, path, '122614-947', '一本道')])
        self.assertEqual(plan['operations'], [])
        self.assertEqual(plan['skipped'], [dict(source=path, reason='数据库番号与文件名编号不一致，待确认')])

    def test_rename_into_an_existing_directory_merges_and_removes_the_source(self):
        script = self.script
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            ledger = fresh_ledger(root)
            source, target = r'B:\日本\一本道\092415-001', r'B:\日本\一本道\092415_001'
            translate, rename = self._media(root)
            for path in (source + r'\fhd2.mp4', target + r'\fhd1.mp4'):
                translate(path).parent.mkdir(parents=True, exist_ok=True)
                translate(path).write_bytes(PureWindowsPath(path).name.encode())
            with closing(sqlite3.connect(ledger)) as db:
                db.row_factory = sqlite3.Row
                db.execute('INSERT INTO asset(id,location,path,name,medium) VALUES(1,?,?,?,?)',
                           ('115', source + r'\fhd2.mp4', 'fhd2.mp4', 'video'))
                db.commit()
                op = script.operation('rename', source, target, script.snapshots(db, source), '等价目录')
                op['subtitles'], op['entities'] = [], script.entity_guard(db, source)
            script.save(root / 'rehome-merge-manifest.json',
                        dict(format=1, stage='rehome', roots={'115': ['B:\\']}, operations=[op]))
            with mock.patch.object(script, 'DATABASE_PATH', ledger), mock.patch.object(script, 'OUT', root), \
                    mock.patch.object(script, 'translate_ledger_path', side_effect=translate), \
                    mock.patch.object(script, 'root_online', return_value=True), \
                    mock.patch.object(script.organize, '_rename', side_effect=rename), redirect_stdout(io.StringIO()):
                script.run_apply('rehome', root / 'backup.db', 10, 20, batch='merge')
            self.assertEqual(sorted(p.name for p in translate(target).iterdir()), ['fhd1.mp4', 'fhd2.mp4'])
            self.assertFalse(translate(source).exists())
            with closing(sqlite3.connect(ledger)) as db:
                self.assertEqual(db.execute('SELECT path FROM asset WHERE id=1').fetchone()[0], target + r'\fhd2.mp4')


class OperationalScriptTests(unittest.TestCase):
    def test_scrape_codes_resumes_committed_codes_and_retains_candidate_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            db = self._chain_ledger(root, ['AAA-001', 'BBB-002'])
            output = root / 'candidates.csv'
            arguments = ['--db', str(db), '--out', str(output), '--raw-dir', str(root / 'raw'),
                         '--log-dir', str(root / 'logs'), '--sources', 'r18dev', '--delay', '0', '--min-free', '0']
            class Provider:
                calls = []
                interrupt = True
                def query(instance, code, source):
                    instance.calls.append(code)
                    if code == 'BBB-002' and instance.interrupt:
                        raise KeyboardInterrupt('模拟进程退出')
                    return OperationalScriptTests._full_payload(code, source)
            provider = Provider()
            with redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
                self.scrape_codes.main(arguments, provider=provider)
            saved = json.loads(output.with_suffix('.resume.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['completed'], ['AAA-001'])
            provider.calls.clear()
            provider.interrupt = False
            try:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(self.scrape_codes.main(arguments, provider=provider), 0)
            finally:
                self.scrape_codes.close_log()
            self.assertEqual(provider.calls, ['BBB-002'])
            with output.open(encoding='utf-8-sig', newline='') as file:
                rows = list(csv.DictReader(file))
            self.assertEqual({row['query'] for row in rows}, {'AAA-001', 'BBB-002'})
            self.assertEqual(len(rows), len({row['item_key'] for row in rows}))

    def setUp(self):
        policy = mock.patch.object(self.sheets, "metadata_only", return_value=False)
        policy.start()
        self.addCleanup(policy.stop)

    @classmethod
    def setUpClass(cls):
        cls.clean_names = load_script("clean_names")
        cls.scrape_codes = load_script("scrape_codes")
        cls.find_ads = load_script("find_ads")
        cls.probe = load_script("probe")
        cls.sheets = load_script("sheets")
        cls.traffic_watch = load_script("traffic_watch")
        cls.creator_boards = load_script("creator_boards")
        cls.creator_tags = load_script("creator_tags")
        cls.creator_attributions = load_script("audit_creator_attributions")
        cls.rehome_unknown = load_script("rehome_unknown_jav")

    def tmp_ledger(self) -> Path:
        """一份只含 rule34xxx 追更条目的临时账本：一条已有类型和时长、一条都还没有。

        临时目录先 `.resolve()`：CI runner 的临时目录都是别名（macOS `/var` 软链到
        `/private/var`，Windows `RUNNER~1` 展开成 `runneradmin`），不 resolve 的路径
        喂给被测代码只会在 CI 上红。
        """
        root = Path(tempfile.mkdtemp()).resolve()
        database = root / "ledger.db"
        connection = sqlite3.connect(database)
        connection.executescript(
            "CREATE TABLE follow_source(id INTEGER PRIMARY KEY, provider TEXT);"
            "CREATE TABLE follow_item(id INTEGER PRIMARY KEY, source_id INTEGER,"
            " external_id TEXT, url TEXT, media_url TEXT, published_at TEXT,"
            " published_precision TEXT, duration REAL, metadata_json TEXT);")
        connection.execute("INSERT INTO follow_source VALUES(1,'rule34xxx')")
        connection.executemany(
            "INSERT INTO follow_item VALUES(?,1,?,?,?,?,'exact',?,?)",
            [(1, "18622796", "https://rule34.xxx/index.php?id=18622796",
              "https://api-cdn-mp4.rule34.xxx/images/1/a.mp4", "2026-09-14T03:02:05Z", 20.0,
              json.dumps({"tag_types": {"nier": "copyright"}})),
             (2, "18622794", "https://rule34.xxx/index.php?id=18622794",
              "https://api-cdn-mp4.rule34.xxx/images/1/b.mp4", "2026-09-14T03:02:05Z", None,
              json.dumps({"tag_types": {}}))])
        connection.commit()
        connection.close()
        return database

    def test_import_has_no_filesystem_or_log_side_effect(self):
        self.assertIsNone(self.clean_names._logf)
        self.assertIsNone(self.scrape_codes._logf)

    def test_english_title_batch_excludes_codes_that_already_have_japanese(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE asset(medium TEXT,code TEXT,catalog_title TEXT,original_title TEXT)")
        connection.executemany("INSERT INTO asset VALUES('video',?,?,?)", [
            ("AAA-001", "English title", None),
            ("BBB-002", "English title", "日本語タイトル"),
            ("CCC-003", None, None),
        ])
        codes = [("AAA-001", 1.0, 1), ("BBB-002", 1.0, 1), ("CCC-003", 1.0, 1)]
        self.assertEqual(self.scrape_codes._select_english_title_codes(connection, codes),
                         [("AAA-001", 1.0, 1)])
        connection.close()

    def test_unmapped_genres_are_written_out_and_english_title_runs_keep_only_titles(self):
        """未收录 genre 必须落盘：这条链一断，来源给过的值就静默消失，官方 tag 的缺口
        下一轮仍然查不出成因。只补英文标题的那一批只写标题字段，别的字段不进候选表。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            sqlite3.connect(db).close(); upgrade(db, MIGRATIONS)
            with closing(sqlite3.connect(db)) as connection:
                connection.execute(
                    "INSERT INTO asset(id,location,path,name,medium,code,size,catalog_title) "
                    "VALUES(1,'local','1.mp4','1.mp4','video','AAA-001',1000,'English title')")
                connection.commit()

            class Source:
                def query(self, code, source):
                    return {"content_id": "aaa001", "title": "日本語タイトル",
                            "genres": ["中出し", "見たことのないジャンル"]}

            def run(*extra):
                out, unmapped = root / f"c{len(extra)}.csv", root / f"u{len(extra)}.csv"
                with redirect_stdout(io.StringIO()):
                    self.scrape_codes.main([
                        "--db", str(db), "--out", str(out), "--unmapped", str(unmapped),
                        "--health", str(root / "h.csv"), "--raw-dir", str(root / "raw"),
                        "--log-dir", str(root / "logs"), "--delay", "0",
                        "--min-free", "0", "--sources", "javbus", *extra,
                    ], provider=Source())
                with out.open(encoding="utf-8-sig", newline="") as handle:
                    fields = [row["field"] for row in csv.DictReader(handle)]
                with unmapped.open(encoding="utf-8-sig", newline="") as handle:
                    genres = [(row["genre"], row["source"], row["sample_code"])
                              for row in csv.DictReader(handle)]
                return fields, genres

            unmapped = [("見たことのないジャンル", "javbus", "AAA-001")]
            self.assertEqual(run(), (["title", "tags"], unmapped))
            self.assertEqual(run("--english-title-only"), (["title"], unmapped))

    def test_reused_snapshots_are_re_checked_against_the_queried_code(self):
        # 「复用上一轮成功记录」只看 result 在不在，就会把当初那次错配一路带下去。
        import json as _json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dlgetchu.json"
            path.write_text(_json.dumps({"result": {
                "id": "33938", "content_id": "33938",
                "source_url": "https://dl.getchu.com/i/item33938",
                "genres": ["コスプレ一般"],
            }}), encoding="utf-8")
            self.assertIsNone(self.scrape_codes._read_snapshot(path, "ABW-220"))
            path.write_text(_json.dumps({"result": {
                "content_id": "118abw220", "genres": ["中出し"],
            }}), encoding="utf-8")
            self.assertEqual(
                self.scrape_codes._read_snapshot(path, "ABW-220")["genres"], ["中出し"])

    def test_source_cools_down_only_after_repeated_failures_and_recovers(self):
        """一次抖动不能决定后面几百个番号的命运。

        2026-09-01 官方 tag 补抓实测：mgstage 中途超时一次，旧逻辑当场把它
        「本批后续全部跳过」，剩下 122 个番号再也没被问过，dmm 丢了 150 个。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            sqlite3.connect(db).close(); upgrade(db, MIGRATIONS)
            connection = sqlite3.connect(db)
            connection.executemany(
                "INSERT INTO asset(id,location,path,name,medium,code,size) "
                "VALUES(?,'local',?,?,'video',?,?)",
                [(i, f"{i}.mp4", f"{i}.mp4", f"AAA-{i:03d}", 1_000) for i in range(1, 7)],
            )
            connection.commit(); connection.close()

            error = self.scrape_codes.MetadataProviderError

            class Flaky:
                def __init__(self): self.calls = []
                def query(self, code, source):
                    self.calls.append(code)
                    raise error("timeout", kind="unavailable",
                                retryable=True, temporary=True)

            provider = Flaky()
            clock = [0.0]
            health = root / "health.csv"
            with mock.patch.object(self.scrape_codes.time, "monotonic",
                                   side_effect=lambda: clock[0]):
                with redirect_stdout(io.StringIO()):
                    self.scrape_codes.main([
                        "--db", str(db), "--out", str(root / "c.csv"),
                        "--health", str(health), "--raw-dir", str(root / "raw"),
                        "--log-dir", str(root / "logs"), "--delay", "0",
                        "--min-free", "0", "--sources", "javbus",
                    ], provider=provider)
            # 前三个番号照常尝试，第三次连败才进冷却；时钟不走，剩下三个被跳过。
            self.assertEqual(len(provider.calls), self.scrape_codes.COOLDOWN_AFTER_FAILURES)
            with health.open(encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["fetched"], "3")
            self.assertEqual(row["cooldown_skips"], "3")
            self.assertEqual(row["blocked"], "1")

            # 冷却会过期：时钟越过窗口后，剩下的番号重新被问。
            provider = Flaky()
            clock = [0.0]

            def advancing():
                clock[0] += self.scrape_codes.COOLDOWN_SECONDS
                return clock[0]

            with mock.patch.object(self.scrape_codes.time, "monotonic",
                                   side_effect=advancing):
                with redirect_stdout(io.StringIO()):
                    self.scrape_codes.main([
                        "--db", str(db), "--out", str(root / "c2.csv"),
                        "--health", str(root / "h2.csv"), "--raw-dir", str(root / "raw2"),
                        "--log-dir", str(root / "logs"), "--delay", "0",
                        "--min-free", "0", "--sources", "javbus",
                    ], provider=provider)
            self.assertEqual(len(provider.calls), 6, "冷却过期后必须继续问剩下的番号")

    def test_an_authentication_failure_stops_that_source_for_the_whole_run(self):
        """凭据不会自己恢复：撞上鉴权墙的来源本批不再问，别的来源照常跑完。

        冷却那一档是「先歇 300 秒再说」，对限流成立，对过期 Cookie 只是把同一次
        失败重复几百遍。摘要里单列鉴权失败的来源，因为下一步不同：补凭据重跑，
        而不是等。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            sqlite3.connect(db).close(); upgrade(db, MIGRATIONS)
            connection = sqlite3.connect(db)
            connection.executemany(
                "INSERT INTO asset(id,location,path,name,medium,code,size) "
                "VALUES(?,'local',?,?,'video',?,?)",
                [(i, f"{i}.mp4", f"{i}.mp4", f"AAA-{i:03d}", 1_000) for i in range(1, 6)],
            )
            connection.commit(); connection.close()

            from peach.metadata import auth_error
            error = self.scrape_codes.MetadataProviderError

            class Walled:
                def __init__(self): self.calls = []
                def query(self, code, source):
                    self.calls.append((source, code))
                    if source == "javbus":
                        raise auth_error("javbus", "来源返回 403", status_code=403)
                    raise error("no such code", kind="not_found")

            provider = Walled()
            health = root / "health.csv"
            with redirect_stdout(io.StringIO()) as printed:
                self.scrape_codes.main([
                    "--db", str(db), "--out", str(root / "c.csv"),
                    "--health", str(health), "--raw-dir", str(root / "raw"),
                    "--log-dir", str(root / "logs"), "--delay", "0",
                    "--min-free", "0", "--sources", "javbus,r18dev",
                ], provider=provider)
            asked = [source for source, _ in provider.calls]
            self.assertEqual(asked.count("javbus"), 1, "鉴权失败后不该再问这个来源")
            self.assertEqual(asked.count("r18dev"), 5, "别的来源不受牵连")
            rows = {}
            with health.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    rows[row["source"]] = row
            self.assertEqual(rows["javbus"]["last_error_kind"], "auth")
            self.assertEqual(rows["javbus"]["auth_skips"], "4")
            self.assertEqual(rows["javbus"]["blocked"], "1")
            self.assertEqual(rows["r18dev"]["auth_skips"], "0")
            self.assertIn("鉴权失败的来源", printed.getvalue())

    def test_only_not_found_counts_as_a_settled_source_verdict(self):
        # 本机配置问题（凭据没贴、桥没装）返回的是 unknown / unavailable 错误。把它当
        # 定论复用，会让配置问题被冻结成来源判决，续跑再也不问这个番号。
        self.assertEqual(self.scrape_codes.SETTLED_ERROR_KINDS, frozenset({"not_found"}))

    def test_rule34_detail_backfill_reuses_the_connector_and_is_resumable(self):
        """标签类型、上传时间在帖子页上，视频时长在原文件头里，存量行靠常规检查补不到。

        抓取判据不重写，直接复用连接器的 `_detail` 与 `_video_seconds`；备份先做、
        分批提交，续跑只跳过上一趟真正写入的条目。
        """
        backfill = load_script("backfill_rule34_details")
        # 连接与备份走共享的 `open_for_write`／`open_readonly`，WAL 正确性由
        # `scripting` 那条回归测试守住；这里只钉「用的是那一份」。断言源码里有没有
        # `reader.backup(writer)` 这串字符的话，脚本一改成调用共享实现就会红——
        # 而那次行为恰恰是变好了。
        self.assertIs(backfill.open_for_write, scripting.open_for_write)
        self.assertIs(backfill.open_readonly, scripting.open_readonly)
        self.assertEqual(backfill.BACKUP_REQUIRED, scripting.BACKUP_REQUIRED)

        # 缺 `--backup` 的 `--apply` 在读输入之前就停，返回 2 且一行都不写。
        database = self.tmp_ledger()
        args = backfill.build_parser().parse_args(
            ["--db", str(database), "--apply"])
        self.assertEqual(backfill.run(args), 2)

        # 上传时间错没错要问过才知道，所以每条都排队；续跑只扣掉已经写入的。
        connection = sqlite3.connect(database)
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        pending = backfill.pending_rows(connection, 0)
        self.assertEqual([row["external_id"] for row in pending], ["18622796", "18622794"])
        self.assertEqual([row["external_id"] for row in backfill.pending_rows(connection, 0, {1})],
                         ["18622794"])

        # 只补不抹：已有的类型与时长不覆盖，帖子页没给上传时间就不动它。
        typed, untyped = pending
        detail = {"tag_types": {"a": "artist"}, "published_at": "2026-05-01T16:32:21Z"}
        self.assertEqual(backfill.plan_update(typed, detail, 60.054),
                         {"published_at": "2026-05-01T16:32:21Z"})
        plan = backfill.plan_update(untyped, detail, 60.054)
        self.assertEqual((plan["published_at"], plan["duration"]),
                         ("2026-05-01T16:32:21Z", 60.054))
        self.assertEqual(json.loads(plan["metadata_json"]), {"tag_types": {"a": "artist"}})
        self.assertIsNone(backfill.plan_update(
            typed, {"tag_types": {"a": "artist"}, "published_at": None}, None))

        # 写入与续跑：写过的条目在 CSV 里记 `written=是`，下一趟 `--resume` 跳过它。
        writer = sqlite3.connect(database)
        self.addCleanup(writer.close)
        backfill._apply(writer, [(2, plan)])
        row = connection.execute(
            "SELECT published_at, duration, published_precision FROM follow_item WHERE id=2"
        ).fetchone()
        self.assertEqual(tuple(row), ("2026-05-01T16:32:21Z", 60.054, "exact"))
        out = database.parent / "log.csv"
        backfill.write_rows(out, backfill.FIELDS, [{"item_id": 2, "written": "是"},
                                          {"item_id": 1, "written": "否"}], fill_missing=True)
        self.assertEqual(backfill.written_ids(out), {2})

    def test_rule34_duration_only_backfill_touches_nothing_but_empty_durations(self):
        """授权只覆盖时长时：只挑缺时长的 mp4，不问帖子页，只写 `duration`。"""
        backfill = load_script("backfill_rule34_details")
        database = self.tmp_ledger()
        seeding = sqlite3.connect(database)
        seeding.execute(
            "INSERT INTO follow_item VALUES(3,1,'18622790','https://rule34.xxx/?id=3',"
            "'https://api-cdn.rule34.xxx/images/1/c.jpeg','2026-09-14T03:02:05Z',"
            "'exact',NULL,'{}')")
        seeding.commit()
        untouched = seeding.execute(
            "SELECT id, published_at, published_precision, metadata_json FROM follow_item"
            " ORDER BY id").fetchall()
        seeding.close()
        heads = []

        class Connector:
            def _detail(self, post_id):
                raise AssertionError("只补时长不该打帖子页")

            def _video_seconds(self, url):
                heads.append(url)
                return 60.054

        out = database.parent / "duration.csv"
        args = backfill.build_parser().parse_args(
            ["--db", str(database), "--apply", "--backup", str(database.parent / "b.db"),
             "--out", str(out), "--delay", "0", "--duration-only"])
        with mock.patch.object(backfill, "build_connector", return_value=Connector()), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(backfill.run(args), 0)
        self.assertEqual(heads, ["https://api-cdn-mp4.rule34.xxx/images/1/b.mp4"])
        after = sqlite3.connect(database)
        self.addCleanup(after.close)
        self.assertEqual(dict(after.execute("SELECT id, duration FROM follow_item")),
                         {1: 20.0, 2: 60.054, 3: None})
        self.assertEqual(after.execute(
            "SELECT id, published_at, published_precision, metadata_json FROM follow_item"
            " ORDER BY id").fetchall(), untouched)
        self.assertEqual(backfill.written_ids(out), {2})

    def test_follow_image_dims_backfill_reads_archives_first_and_probes_headers_for_the_rest(self):
        """图片墙的比例占位要每张图都有宽高；存量行里只有 fanbox 记过。

        rule34.xxx 的尺寸就在归档的 dapi 响应里，不发请求；归档站只问文件头。
        判据与落库都复用 `follow_image_dims` 与 `FollowStore.set_image_dims`，
        只补空缺，条数不变，第二遍无事可做。
        """
        backfill = load_script("backfill_follow_image_dims")
        self.assertIs(backfill.open_for_write, scripting.open_for_write)
        self.assertIs(backfill.probe_image_dims, follow_image_dims.probe_image_dims)

        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        database = fresh_ledger(root)
        self.assertEqual(backfill.run(backfill.build_parser().parse_args(
            ["--db", str(database), "--apply"])), 2)

        moment = datetime(2026, 9, 1, tzinfo=timezone.utc)
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        store = FollowStore(lambda: connection, sources_root=root / "sources")
        def seed(provider, ref, candidates):
            source_id = store.register(provider=provider, ref=ref, label=ref,
                                       url=f"https://{provider}.test/{ref}", semantics="work",
                                       moment=moment)
            store.record(source_id, SourceFetch(
                provider=provider, ref=ref, request_url=f"https://{provider}.test/{ref}",
                semantics="work", candidates=tuple(candidates), raw_body=None), moment=moment)
        seed("rule34xxx", "tag", [
            FollowCandidate(provider="rule34xxx", external_id="11", title="a",
                            url="https://rule34.xxx/index.php?id=11",
                            media_url="https://api-cdn.rule34.xxx/images/1/a.jpg",
                            thumb_url="https://api-cdn.rule34.xxx/samples/1/a.jpg"),
            # 视频不占图片墙，不问——带缩略图也一样。
            FollowCandidate(provider="rule34xxx", external_id="12", title="v",
                            url="https://rule34.xxx/index.php?id=12",
                            media_url="https://api-cdn-mp4.rule34.xxx/images/1/v.mp4",
                            thumb_url="https://api-cdn.rule34.xxx/samples/1/v.jpg"),
        ])
        seed("pawchive", "user", [
            FollowCandidate(provider="pawchive", external_id="21", title="b",
                            url="https://pawchive.pw/post/21",
                            media_url="https://file.pawchive.pw/data/ab/cd/abcd.png",
                            thumb_url="https://img.pawchive.pw/thumbnail/data/ab/cd/abcd.png"),
            # 多图帖只问卡面那一张，其余几张只在详情里翻。
            FollowCandidate(provider="pawchive", external_id="23", title="d",
                            url="https://pawchive.pw/post/23",
                            media_url="https://file.pawchive.pw/data/ij/kl/one.png",
                            thumb_url="https://img.pawchive.pw/thumbnail/data/ij/kl/one.png",
                            extra={"media_items": [
                                {"id": f"/ij/kl/{name}.png", "media_kind": "image",
                                 "url": f"https://file.pawchive.pw/data/ij/kl/{name}.png",
                                 "thumb_url": f"https://img.pawchive.pw/thumbnail/data/ij/kl/{name}.png",
                                 "resource_provider": "pawchive"}
                                for name in ("one", "two")]}),
        ])
        connection.commit()
        connection.close()
        archive = root / "follow" / "rule34xxx" / "k"
        archive.mkdir(parents=True)
        (archive / "20260901T000000Z-abc.raw").write_bytes(json.dumps(
            [{"id": 11, "width": 1280, "height": 720}, {"id": 12, "width": 1920, "height": 1080},
             {"id": 99, "width": 1, "height": 1}]
        ).encode())
        (archive / "20260901T000000Z-abc.json").write_bytes(b"{}")

        seen = []
        def transport(request, timeout, max_bytes):
            seen.append(request)
            header = (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
                      + (800).to_bytes(4, "big") + (600).to_bytes(4, "big"))
            return HttpResponse(206, {}, header)

        out = root / "dims.csv"
        args = backfill.build_parser().parse_args([
            "--db", str(database), "--archives", str(root / "follow"), "--out", str(out),
            "--apply", "--backup", str(root / "backup.db")])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(backfill.run(args, transport=transport), 0)

        self.assertEqual(len(seen), 2, "rule34.xxx 从归档取，只有归档站要探测")
        self.assertEqual(seen[0].headers["Range"], "bytes=0-65535")
        self.assertTrue(all("img.pawchive.pw/thumbnail/" in request.url for request in seen),
                        [request.url for request in seen])
        connection = sqlite3.connect(database)
        self.addCleanup(connection.close)
        dims = {row[0]: json.loads(row[1]) for row in connection.execute(
            "SELECT external_id, metadata_json FROM follow_item")}
        self.assertEqual((dims["11"]["width"], dims["11"]["height"]), (1280, 720))
        self.assertEqual((dims["21"]["width"], dims["21"]["height"]), (800, 600))
        first, second = dims["23"]["media_items"]
        self.assertEqual((first["width"], first["height"]), (800, 600))
        self.assertNotIn("width", second)
        self.assertNotIn("width", dims["12"])
        with out.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual({(row["external_id"], row["mode"], row["result"]) for row in rows},
                         {("11", "归档", "取得"), ("21", "探测", "取得"), ("23", "探测", "取得")})
        self.assertTrue((root / "backup.db").exists())

        # 第二遍：全部已有尺寸，没有待补，也不再发请求。
        connection.row_factory = sqlite3.Row
        items = FollowStore(lambda: connection).items()
        self.assertEqual(backfill.pending_targets(items, set()), [])

    def test_test_entrypoints_guard_the_worktree_source_and_are_the_only_documented_command(self):
        """两个入口用哪个 venv、给运行器什么环境与参数，由 `test_test_environment.py` 真跑入口验；
        这里守的是那条用例碰不到的：拒收别处源码的核对、主工作树定位和两边相同的测试域。"""
        windows = (ROOT / "scripts" / "test.ps1").read_text(encoding="utf-8")
        self.assertIn("rev-parse --git-common-dir", windows)
        self.assertIn("peach.__file__", windows)
        self.assertIn("ValidateSet('full', 'auto', 'follow'", windows)
        # 两个平台各有一个入口，契约必须相同——否则「两边都要绿」只是句口号。
        posix = (ROOT / "scripts" / "test.sh").read_text(encoding="utf-8")
        self.assertIn("rev-parse --git-common-dir", posix)
        self.assertIn("peach.__file__", posix)
        self.assertIn("full|auto|follow|catalog|media|sync|metadata|tooling|web|checks|core|packaging)", posix)
        # 文档里可以「提到」裸命令来说明它为什么不可信，但绝不能让它单独出现成为一条可照抄的指令。
        # 判据因此不是黑名单，而是：凡出现该命令的行，必须在同一行指向某个正式入口。
        for relative in ("AGENTS.md", "docs/TESTING.md"):
            instructions = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("scripts\\test.ps1", instructions)
            self.assertIn("scripts/test.sh", instructions)
            for number, line in enumerate(instructions.splitlines(), 1):
                if "unittest discover" not in line:
                    continue
                self.assertTrue(
                    "test.ps1" in line or "test.sh" in line,
                    f"{relative}:{number} 单独出现了裸命令，读者会照抄；必须同时点明正式入口",
                )

    def test_posix_entrypoint_passes_extra_args_through_on_bash_3_2(self):
        """不带额外参数直接跑 `./scripts/test.sh` 必须能跑起来，参数要原样透传。

        macOS 自带 bash 3.2.57，`set -u` 下空数组的 `"${EXTRA[@]}"` 被判成未绑定变量
        （bash 4.4 起才不报），入口于是在最后一行崩掉。CI 每次都附带
        `--fresh --base <sha> --shard-*`，数组从不为空，这条路径只有本机会走到——而
        AGENTS.md 规定 macOS 的唯一测试入口就是这个脚本，崩了等于没有测试门槛。

        文本判据钉住 `set -euo pipefail` 还在：把 `set +u` 当解法会让其余变量的拼写错误
        没人拦，而行为上看不出来。展开写法由下面的行为判据验：从真实脚本里截出参数处理
        那一段来跑，CI 的 macOS 行用的就是 bash 3.2，改回裸展开时这一段会崩。
        """
        source = (ROOT / "scripts" / "test.sh").read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail", source)

        # 真实脚本后半段要定位 venv 并跑整个测试套件，直接执行会递归。只取参数处理那一段，
        # 把 exec 的目标换成一个回显 argv 的桩，其余保持逐字一致。
        marker = 'EXTRA=("${@:2}")\n'
        prologue, separator, tail = source.partition(marker)
        self.assertEqual(separator, marker)
        exec_line = next(line for line in tail.splitlines() if line.startswith("exec "))
        stub_line = exec_line.replace('"$PYTHON" scripts/test_runner.py',
                                      '"$PEACH_PY" "$PEACH_STUB"', 1)
        self.assertNotEqual(stub_line, exec_line)

        directory = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, directory, True)
        stub = directory / "argv_stub.py"
        stub.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
        entrypoint = directory / "prologue.sh"
        # 两个路径都写成正斜杠：Windows 的 `C:\Users\…` 落进 shell 脚本后反斜杠会被当成转义符
        # 吃掉，exec 拿到的是一个粘在一起的名字。解释器也显式写出来，不靠 `#!`——Git Bash
        # 不按 shebang 找 Windows 上的 Python。
        entrypoint.write_text(
            f'{prologue}{marker}'
            f'PEACH_PY={Path(sys.executable).as_posix()}\n'
            f'PEACH_STUB={stub.as_posix()}\n'
            f'{stub_line}\n', encoding="utf-8")

        # `/bin/bash` 在 macOS 上就是那个 3.2；装了新版 bash 的机器两个都跑。
        # WSL 的 bash 不算：它按 Linux 规则解析路径，入口文件在 Windows 盘符下，
        # 被它执行只会得到「文件不存在」的假失败。Git for Windows 的 bash 在
        # PATH 里可能排在 WSL 后面，这里把标准安装位置补上，按内核名筛一遍。
        shells = []
        for path in ("/bin/bash", shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"):
            if not path or not Path(path).exists():
                continue
            probe = subprocess.run([path, "-c", "uname -s"],
                                   capture_output=True, text=True, encoding="utf-8", check=False)
            if probe.returncode == 0 and "linux" not in probe.stdout.casefold():
                shells.append(path)
        self.assertTrue(shells)
        for shell in dict.fromkeys(shells):
            # 没显式给 `--jobs` 就补 auto，本机默认并行；给了就原样透传，不再补。
            for argv, expected in (
                    ([], ["--scope", "auto", "--jobs", "auto"]),
                    (["auto"], ["--scope", "auto", "--jobs", "auto"]),
                    (["web", "--fresh", "--base", "a b"],
                     ["--scope", "web", "--jobs", "auto", "--fresh", "--base", "a b"]),
                    (["web", "--jobs", "1"], ["--scope", "web", "--jobs", "1"]),
            ):
                with self.subTest(shell=shell, argv=argv):
                    done = subprocess.run(
                        [shell, str(entrypoint), *argv],
                        capture_output=True, text=True, encoding="utf-8", check=False)
                    self.assertEqual(done.returncode, 0, done.stderr)
                    self.assertEqual(json.loads(done.stdout), expected)

    def test_python_floor_matches_runtime_and_ci(self):
        """正式运行版本、CI 与 README 共同覆盖声明的 Python 下限。"""
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        floor = project["requires-python"].removeprefix(">=")
        self.assertEqual(floor, "3.14")
        self.assertEqual((ROOT / ".python-version").read_text(encoding="utf-8").strip(), floor)
        self.assertIn(f"Programming Language :: Python :: {floor}", project["classifiers"])
        workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        self.assertIn("fromJSON(needs.plan.outputs.matrix)", workflow)
        from scripts.ci_plan import plan
        self.assertEqual({row["python"] for row in plan("workflow_dispatch", [])["matrix"]["include"]}, {floor})
        self.assertIn(f"Python {floor} 或更高", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn(f"Python {floor} or newer", (ROOT / "README.en.md").read_text(encoding="utf-8"))

    def test_functional_test_scopes_are_explicit(self):
        runner = load_script("test_runner")
        follow = {path.name for path in runner.selected_files("follow")}
        full = {path.name for path in runner.selected_files("full")}
        self.assertIn("test_follow_web.py", follow)
        self.assertIn("test_migrations.py", follow)
        self.assertNotIn("test_media.py", follow)
        self.assertGreater(len(full), len(follow))
        with redirect_stdout(io.StringIO()) as listed:
            self.assertEqual(runner.main(["--list-scopes"]), 0)
        self.assertEqual(listed.getvalue().split(), ["full", "auto", *runner.SCOPES])

    def test_every_test_file_is_registered_in_a_scope(self):
        """新测试文件必须登记进 `scripts/test_runner.py` 的域，否则只有 `full` 才跑到它。"""
        runner = load_script("test_runner")
        patterns = (*runner.COMMON_PATTERNS,
                    *(pattern for scopes in runner.SCOPES.values() for pattern in scopes))
        orphans = sorted(
            path.name for path in (ROOT / "tests").glob("test_*.py")
            if not any(path.match(pattern) for pattern in patterns))
        self.assertEqual(
            orphans, [],
            "这些测试文件不属于任何域，把它们登记进 scripts/test_runner.py 的 SCOPES"
            "（或 COMMON_PATTERNS），否则只有 full 才跑到它们：\n  " + "\n  ".join(orphans))

    def test_auto_scope_maps_changed_files_and_falls_back_to_full(self):
        """`auto` 的选域是纯函数：喂文件清单，不碰 git。"""
        runner = load_script("test_runner")
        pick = runner.scopes_for_changes
        # 前缀表：多个文件取并集，反斜杠路径也认。
        self.assertEqual(pick(["src/peach/follow_store.py", "web/app.js"])[0], ("follow", "web"))
        self.assertEqual(pick(["src\\peach\\tray.py"])[0], ("sync", "tooling"))
        self.assertEqual(pick(["scripts/probe.py", "pyproject.toml", ".github/workflows/test.yml"])[0],
                         ("full",))
        self.assertEqual(pick(["README.md", "docs/STATUS.md", ".claude/skills/x/SKILL.md"])[0],
                         ("checks", "tooling"))
        self.assertEqual(pick(["src/peach/web_entity.py", "src/peach/routes_pages.py"])[0],
                         ("catalog", "tooling", "web"))
        self.assertEqual(pick(["src/peach/web_follow.py"])[0], ("follow",))
        # 模块名 ↔ 测试文件名推断，登记在几个域就跑几个域。
        self.assertEqual(pick(["src/peach/media.py"])[0], ("media",))
        self.assertEqual(pick(["src/peach/jobs.py"])[0], ("media", "tooling"))
        # `test_metadata_library.py` 也登记在 web 域（它有一段读 `web/app.js`），
        # 名字又落在 `test_metadata_` 这一族里，于是这个模块把 web 域一起带上。
        # 宽一档是安全的那一侧，web 域全是源码文本断言，代价只有几秒。
        self.assertEqual(pick(["src/peach/metadata.py"])[0], ("metadata", "web"))
        # 测试文件按自己的文件名归域；公共门槛文件归 tooling。
        self.assertEqual(pick(["tests/test_certs.py"])[0], ("sync", "tooling"))
        self.assertEqual(pick(["tests/test_context_budget.py"])[0], ("tooling",))
        scopes, why = pick(["src/peach/follow.py", "web/app.js"])
        self.assertTrue(why.startswith("Peach auto scope: follow, web <- "), why)
        self.assertIn("web: web/app.js", why)
        self.assertEqual(pick([])[0], ("checks",))
        # full 覆盖公共设施和未知影响面。
        for paths, fragment in ((["migrations/0099_next.sql"], "必须 full"),
                                (["tests/support/ledger.py"], "必须 full"),
                                (["tests/conftest.py"], "必须 full"),
                                (["package-lock.json"], "必须 full"),
                                (["frontend/package.json"], "必须 full"),
                                (["LICENSE"], "映射不到"),
                                (["src/peach/kanji.py", "web/app.js"], "映射不到")):
            scopes, why = pick(paths)
            self.assertEqual(scopes, ("full",), paths)
            self.assertTrue(why.startswith("Peach auto scope: full <- "), why)
            self.assertIn(fragment, why)

    def test_the_full_runner_can_import_repository_scripts(self):
        runner = load_script("test_runner")
        suite = runner.build_suite("tooling")
        self.assertGreater(suite.countTestCases(), 0)

    def test_structural_creator_and_mainstream_release_guards(self):
        self.assertTrue(is_structural_creator("视频"))
        self.assertTrue(is_structural_creator("门槛"))
        self.assertFalse(is_structural_creator("Alice"))
        self.assertTrue(is_probable_mainstream_release(
            "The.Great.Escape.S04E09.1080p.WEB-DL.H264.AAC-AppleTor.mp4"
        ))
        self.assertFalse(is_probable_mainstream_release("S04E09-personal-video.mp4"))

    def test_creator_attribution_audit_distinguishes_evidence_and_folder_names(self):
        classify = self.creator_attributions.classify
        self.assertEqual(classify(
            r"B:\云下载\足交仙人\feet of Suzyq (1).mp4", "足交仙人"
        )[3:5], ("replace", "suzuq"))
        self.assertEqual(classify(
            r"B:\MVP\捅主任\TokyoDolls\32.mp4", "捅主任"
        )[3], "remove")
        self.assertEqual(classify(
            r"B:\创作者\捅主任\real.mp4", "捅主任"
        )[3], "review_folder_projection")
        self.assertEqual(classify(
            r"B:\MVP\TokyoDolls\32.mp4", "捅主任"
        )[3], "review_legacy_projection")

    def test_ad_candidate_scan_is_isolated_and_review_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE asset(id INTEGER,location TEXT,path TEXT,name TEXT,"
                "size INTEGER,duration REAL,medium TEXT)"
            )
            for asset_id in range(1, 4):
                connection.execute(
                    "INSERT INTO asset VALUES(?,?,?,?,?,?,?)",
                    (
                        asset_id,
                        "local",
                        str(root / "pack" / f"promo-{asset_id}.mp4"),
                        f"promo-{asset_id}.mp4",
                        10 * 1024**2,
                        37.4,
                        "video",
                    ),
                )
            connection.commit()
            connection.close()

            plan, scanned = self.find_ads.find_candidates(db, min_group=3)
            self.assertEqual(scanned, 3)
            self.assertEqual(len(plan), 3)
            self.assertTrue(all("等长重复x3" in row["hits"] for row in plan))
            self.assertFalse((root / "ad-candidates.csv").exists())

    def test_ledger_paths_are_split_with_windows_semantics_on_any_host(self):
        """账本路径是 Windows 口径；用 `os.path.dirname` 在 macOS 上会得到空目录，
        判据 A/E 直接失效，判据 B 的「同目录」分组还会退化成跨整个库比对。"""
        self.assertEqual(
            self.find_ads.ledger_dir(r"B:\云下载\bbsxv.xyz-DOCP-324\极道世界.mp4"),
            r"B:\云下载\bbsxv.xyz-DOCP-324",
        )
        self.assertNotEqual(
            self.find_ads.ledger_dir(r"B:\一\a.mp4"),
            self.find_ads.ledger_dir(r"B:\二\a.mp4"),
        )

    def test_promo_dirpack_flags_clean_named_ads_and_spares_watermark_dirs(self):
        """广告包把域名藏进目录名（bbsxv.xyz-DOCP-324），文件名干净、无等长重复也不得漏；
        转载水印目录（www.98T.la@账号）是来源标注，不能因带域名就进清单。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ledger.db"
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE asset(id INTEGER,location TEXT,path TEXT,name TEXT,"
                "size INTEGER,duration REAL,medium TEXT)"
            )
            connection.executemany(
                "INSERT INTO asset VALUES(?,?,?,?,?,?,?)",
                [
                    (1, "115", r"B:\云下载\bbsxv.xyz-DOCP-324\极道世界.mp4",
                     "极道世界.mp4", 75281679, 28.08, "video"),
                    (2, "115", r"B:\云下载\bbsxv.xyz-DOCP-324\最新情报.wmv",
                     "最新情报.wmv", 90867046, 90.92, "video"),
                    (3, "115", r"B:\创作者\luckydog22\www.98T.la@luckydog22\469.avi",
                     "469.avi", 16 * 1024**2, 92.0, "video"),
                ],
            )
            connection.commit(); connection.close()

            plan, scanned = self.find_ads.find_candidates(db, min_group=3)
            self.assertEqual(scanned, 3)
            by_id = {row["id"]: row for row in plan}
            self.assertIn("推广目录", by_id[1]["hits"])
            self.assertIn("推广目录", by_id[2]["hits"])
            self.assertEqual(by_id[1]["confidence"], "确认")
            self.assertNotIn(3, by_id)

    def test_filename_cleanup_is_conservative(self):
        propose = self.clean_names.propose
        self.assertEqual(propose("www.98T.la@sample.mp4"), "sample.mp4")
        self.assertEqual(propose("CJOD-158[fuckbe.com].mp4", "CJOD-158"),
                         "CJOD-158.mp4")
        self.assertEqual(propose("sample.mp4.mp4"), "sample.mp4")
        self.assertEqual(propose("sample.mp4.jpg"), "sample.mp4.jpg")
        self.assertEqual(propose("(3).mp4"), "(3).mp4")
        self.assertEqual(
            propose("Dakota Doll - [Beauty-Angels.com] - [2024] Scene.mp4"),
            "Dakota Doll - [2024] Scene.mp4",
        )
        self.assertEqual(
            propose("❤成人游戏-导航-【688GM.CC】.png"),
            "❤成人游戏-导航.png",
        )
        self.assertEqual(
            propose("QR CODE--扫一扫.png"),
            "QR CODE--扫一扫.png",
            "没有命中清洁规则的原始双横线不能被顺手改写",
        )

    def test_filename_cleanup_normalises_only_the_confirmed_ledger_code(self):
        propose = self.clean_names.propose
        self.assertEqual(propose("PBD00390.mp4", "PBD390", True), "PBD-390.mp4")
        self.assertEqual(propose("HD-abp-0758.mp4", "ABP-758"), "HD-ABP-758.mp4")
        self.assertEqual(
            propose("fc2 3098987 sample.mp4", "FC2PPV-3098987"),
            "FC2-PPV-3098987 sample.mp4",
        )
        self.assertEqual(
            propose("KUZU_250103-U_iris3.mp4", "KUZU-25010"),
            "KUZU_250103-U_iris3.mp4",
            "番号后紧接额外数字时不能把长编号截断改写",
        )
        self.assertEqual(
            propose("raikun325.mp4", "RAIKUN325"), "raikun325.mp4",
            "没有分隔符的账号名不能先补成番号再改文件名",
        )
        self.assertEqual(
            propose("1pondo 092415 001 FHD.mp4", "092415_001"),
            "1pondo 092415_001 FHD.mp4",
            "日期式番号缺分隔符的写法按 ledger 补齐",
        )
        self.assertEqual(
            propose("1pon-092415-001-fhd1.mp4", "092415_001"),
            "1pon-092415-001-fhd1.mp4",
            "日期式番号的另一种分隔符属于别的片商，不改写",
        )

    def test_filename_cleanup_keeps_collision_media_with_a_numbered_suffix(self):
        rows = [
            (1, "115", r"B:\番号\ABW-234\ABW-234.mp4", "ABW-234.mp4", "ABW-234"),
            (2, "115", r"B:\番号\ABW-234\hhd800.com@abw-0234.mp4",
             "hhd800.com@abw-0234.mp4", "ABW-234"),
            (3, "115", r"B:\番号\FC2\fc2 3098987.mp4",
             "fc2 3098987.mp4", "FC2PPV-3098987"),
        ]
        plan = self.clean_names.build_plan(rows)
        by_id = {row["id"]: row for row in plan}
        self.assertEqual(by_id[2]["new"], "ABW-234 (2).mp4")
        self.assertEqual(by_id[2]["status"], "ready-suffixed")
        self.assertEqual(by_id[3]["new"], "FC2-PPV-3098987.mp4")
        self.assertEqual(by_id[3]["new_code"], "FC2-PPV-3098987")

    def test_filename_cleanup_normalises_compact_code_only_with_release_evidence(self):
        plan = self.clean_names.build_plan([
            (1, "115", r"B:\番号\PBD390\PBD00390.mp4",
             "PBD00390.mp4", "PBD390", 1),
            (2, "115", r"B:\账号\RAIKUN325\raikun325.mp4",
             "raikun325.mp4", "RAIKUN325", 0),
        ])
        self.assertEqual([(row["id"], row["new"], row["new_code"]) for row in plan],
                         [(1, "PBD-390.mp4", "PBD-390")])

    def test_filename_cleanup_joins_paths_by_the_ledger_path_shape(self):
        self.assertEqual(
            self.clean_names._join(r"B:\番号\ABW-234", "ABW-234.mp4"),
            r"B:\番号\ABW-234\ABW-234.mp4",
        )
        self.assertEqual(
            self.clean_names._join("/tmp/peach", "ABW-234.mp4"),
            "/tmp/peach/ABW-234.mp4",
        )

    def test_filename_cleanup_supports_mounted_cloud_sources(self):
        for location in ('guangya', 'onedrive', 'mounted'):
            with self.subTest(location=location):
                plan = self.clean_names.build_plan([
                    (1, location, r'G:\media\abw-0234.mp4', 'abw-0234.mp4', 'ABW-234'),
                ])
                self.assertEqual([(row['location'], row['new']) for row in plan],
                                 [(location, 'ABW-234.mp4')])

    def test_filename_cleanup_apply_renames_files_updates_ledger_and_validates_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "ABW-234.mp4"
            advertised = root / "hhd800.com@abw-0234.mp4"
            lowercase = root / "mide-950-C.mp4"
            existing.write_bytes(b"first")
            advertised.write_bytes(b"second")
            lowercase.write_bytes(b"third")
            db = root / "ledger.db"
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE asset(id INTEGER PRIMARY KEY,location TEXT,path TEXT,"
                "name TEXT,code TEXT)"
            )
            connection.executemany(
                "INSERT INTO asset VALUES(?,?,?,?,?)",
                [
                    (1, "local", str(existing), existing.name, "ABW-234"),
                    (2, "local", str(advertised), advertised.name, "ABW-234"),
                    (3, "local", str(lowercase), lowercase.name, "MIDE-950"),
                ],
            )
            connection.commit(); connection.close()

            backup_path = root / "ledger.pre-clean-names.db"
            result = self.clean_names.main([
                "--db", str(db), "--out", str(root / "plan.csv"),
                "--log-dir", str(root / "logs"),
                "--apply", "--backup", str(backup_path),
            ])

            self.assertEqual(result, 0)
            self.assertEqual(existing.read_bytes(), b"first")
            self.assertEqual((root / "ABW-234 (2).mp4").read_bytes(), b"second")
            self.assertEqual((root / "MIDE-950-C.mp4").read_bytes(), b"third")
            connection = sqlite3.connect(db)
            rows = connection.execute(
                "SELECT id,name,code FROM asset ORDER BY id"
            ).fetchall()
            connection.close()
            self.assertEqual(rows, [
                (1, "ABW-234.mp4", "ABW-234"),
                (2, "ABW-234 (2).mp4", "ABW-234"),
                (3, "MIDE-950-C.mp4", "MIDE-950"),
            ])
            backup = sqlite3.connect(backup_path)
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(backup.execute("SELECT count(*) FROM asset").fetchone()[0], 3)
            backup.close()

    def test_rehome_unknown_jav_flattens_files_and_updates_confirmed_studio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unknown = root / "番号" / "_未知厂牌"
            nested = unknown / "259LUXU-1468" / "release title"
            nested.mkdir(parents=True)
            video = nested / "259LUXU-1468.mp4"
            video.write_bytes(b"video")
            sidecar = nested / "259LUXU-1468.jpg"
            sidecar.write_bytes(b"image")
            db = root / "ledger.db"
            sqlite3.connect(db).close(); upgrade(db, MIGRATIONS)
            connection = sqlite3.connect(db)
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,code) VALUES(?,?,?,?,?,?)",
                (1, "115", r"B:\番号\_未知厂牌\259LUXU-1468\release title\259LUXU-1468.mp4",
                 video.name, "video", "259LUXU-1468"),
            )
            connection.commit(); connection.close()
            mappings = root / "mappings.csv"
            mappings.write_text(
                "code,studio,source,confidence,evidence_url,note\n"
                "259LUXU-1468,ラグジュTV,user:studio-review,1.0,https://javdb.com/v/YJ148,user confirmed\n",
                encoding="utf-8-sig",
            )
            backup = root / "ledger.pre-rehome.db"
            plan = root / "plan.csv"

            result = self.rehome_unknown.main([
                "--db", str(db), "--mappings", str(mappings),
                "--physical-unknown-root", str(unknown),
                "--ledger-unknown-root", r"B:\番号\_未知厂牌",
                "--plan", str(plan), "--apply", "--backup", str(backup),
            ])

            self.assertEqual(result, 0)
            target = root / "番号" / "ラグジュTV" / "259LUXU-1468"
            self.assertEqual((target / video.name).read_bytes(), b"video")
            self.assertEqual((target / sidecar.name).read_bytes(), b"image")
            self.assertFalse((unknown / "259LUXU-1468").exists())
            connection = sqlite3.connect(db)
            self.assertEqual(connection.execute(
                "SELECT path,studio FROM asset WHERE id=1"
            ).fetchone(), (r"B:\番号\ラグジュTV\259LUXU-1468\259LUXU-1468.mp4",
                           "ラグジュTV"))
            self.assertEqual(connection.execute(
                "SELECT e.canonical_name,ae.source FROM asset_entity ae "
                "JOIN entity e ON e.id=ae.entity_id WHERE ae.asset_id=1 AND ae.role='studio'"
            ).fetchone(), ("ラグジュTV", "user:studio-review"))
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            connection.close()
            self.assertTrue(backup.is_file())
            self.assertIn(",done", plan.read_text(encoding="utf-8-sig"))

    def test_rehome_unknown_jav_refuses_flattening_name_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unknown = root / "番号" / "_未知厂牌"
            for folder in ("one", "two"):
                nested = unknown / "ABP-340" / folder
                nested.mkdir(parents=True)
                (nested / "ABP-340.mp4").write_bytes(folder.encode())
            db = root / "ledger.db"
            sqlite3.connect(db).close(); upgrade(db, MIGRATIONS)
            mappings = root / "mappings.csv"
            mappings.write_text(
                "code,studio,source,confidence\n"
                "ABP-340,Prestige,user:studio-review,1.0\n",
                encoding="utf-8-sig",
            )

            result = self.rehome_unknown.main([
                "--db", str(db), "--mappings", str(mappings),
                "--physical-unknown-root", str(unknown),
                "--ledger-unknown-root", r"B:\番号\_未知厂牌",
                "--plan", str(root / "plan.csv"),
            ])

            self.assertEqual(result, 1)
            self.assertTrue((unknown / "ABP-340" / "one" / "ABP-340.mp4").is_file())
            self.assertFalse((root / "番号" / "Prestige" / "ABP-340").exists())

    def test_rehome_unknown_jav_accepts_cloud_drive_removing_an_empty_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "code"
            vanished = root / "release title"
            vanished.mkdir(parents=True)
            original_iterdir = Path.iterdir

            def cloud_drive_iterdir(path):
                if path == vanished and path.exists():
                    path.rmdir()
                    raise FileNotFoundError(path)
                return original_iterdir(path)

            with mock.patch.object(Path, "iterdir", cloud_drive_iterdir):
                self.rehome_unknown._remove_empty_tree(root)
            self.assertFalse(root.exists())

    def test_rehome_unknown_jav_accepts_cloud_drive_removing_an_empty_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.mkdir()
            original_iterdir = Path.iterdir

            def cloud_drive_iterdir(path):
                if path == target and path.exists():
                    path.rmdir()
                    raise FileNotFoundError(path)
                return original_iterdir(path)

            with mock.patch.object(Path, "iterdir", cloud_drive_iterdir):
                self.assertEqual(self.rehome_unknown._direct_children(target), {})

    def test_media_batch_scripts_are_import_safe_and_keep_context_rules(self):
        self.assertEqual(self.probe.context_fields(1920, 1080, 180), ("速食", "横屏", "2K"))
        with tempfile.TemporaryDirectory() as tmp:
            output = self.sheets.output_path(Path(tmp), "local", "R:/media/one.mp4")
            self.assertFalse(output.exists())
            self.assertTrue(output.parent.is_dir())
        self.assertTrue(self.traffic_watch.is_direct({"chains": ["DIRECT"]}))
        self.assertFalse(self.traffic_watch.is_direct({"chains": ["Proxy", "Relay"]}))
        self.assertEqual(self.creator_boards.safe_name("A/B:C"), "A_B_C")

    def test_age_gate_is_crossed_by_the_affirmative_link_only(self):
        """AV 厂牌官网普遍先给年龄确认页，不穿过它只能拿到约 10 KB 的空壳。

        判据必须是锚文本：否定链接指向站外（实测 dasdas.jp / muku.tv 都指向 dmm.com），
        肯定链接指向站内，两者的 href 本身看不出区别。跟错就会离开厂牌域名，
        而离开域名抓到的社交账号就不再属于这个厂牌。
        """
        module = load_script("find_studio_socials")
        gate = (
            '<a href="https://dasdas.jp/top">はい（入室する）</a>'
            '<a href="https://www.dmm.com/">いいえ</a>'
        )
        self.assertEqual(
            module.affirmative_link(gate, "https://dasdas.jp/"), "https://dasdas.jp/top")
        # 只有否定链接时不得跟随，否则会走到站外。
        self.assertIsNone(module.affirmative_link(
            '<a href="https://www.dmm.com/">いいえ</a>', "https://dasdas.jp/"))
        # 肯定文案但跨域，同样不跟。
        self.assertIsNone(module.affirmative_link(
            '<a href="https://elsewhere.example/top">ENTER</a>', "https://dasdas.jp/"))
        self.assertIsNone(module.affirmative_link("<p>没有链接</p>", "https://dasdas.jp/"))
        for content in ('は　い', '<img alt="はい">', '<img alt="WEBサイトへ入場">'):
            self.assertEqual(module.affirmative_link(f'<a href="/top">{content}</a>',
                                                    "https://brand.test/"), "https://brand.test/top")
        self.assertIsNone(module.affirmative_link('<a href="/no"><img alt="いいえ"></a>',
                                                 "https://brand.test/"))
        self.assertIsNone(module.affirmative_link('<a href="/catalog"><img alt="EYES"></a>',
                                                 "https://brand.test/"))

    def test_platform_paths_are_not_mistaken_for_accounts(self):
        module = load_script("find_studio_socials")
        html = ('<a href="https://twitter.com/intent/tweet">分享</a>'
                '<a href="https://x.com/dahliaofficial0">官方</a>'
                '<a href="https://twitter.com/share">share</a>')
        self.assertEqual(module.handles_in(html), {"dahliaofficial0"})

    def test_studio_social_profiles_keep_multiple_accounts_and_anchor_evidence(self):
        module = load_script("find_studio_socials")
        html = ('<p><a href="https://x.com/SCute_av">S-Cute【公式】</a></p>'
                '<p><a href="//twitter.com/_scute">nanairo【公式】</a></p>'
                '<a href="https://x.com/scute_AV?lang=ja">duplicate</a>'
                '<a href="https://x.com/actor/status/123">投稿</a>'
                '<script>"https://x.com/not_a_link"</script>'
                '<a href="https://x.com/intent/tweet">share</a>')
        accounts = module.accounts_in(html)
        self.assertEqual({a["handle"] for a in accounts}, {"SCute_av", "_scute"})
        self.assertEqual(accounts[1]["anchor"], "nanairo【公式】")
        rows = module.review_rows({"entity_id": 3, "studio": "S-Cute", "known": {"scute_av"}},
                                  [{**a, "page": "https://www.s-cute.com/", "sha256": "abc"}
                                   for a in accounts])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://x.com/_scute")
        self.assertIn("nanairo【公式】", rows[0]["evidence"])
        self.assertEqual(rows[0]["review"], "待复核账号归属")

    def test_studio_social_scan_checks_response_and_completes_age_gate(self):
        from peach.http import HttpResponse
        module = load_script("find_studio_socials")
        gate = ('<a href="https://x.com/main_brand">X</a>'
                '<a href="/top">はい</a>').encode()
        inside = '<a href="https://x.com/other_brand">関連公式</a>'.encode()
        responses = iter([HttpResponse(200, {}, gate, "https://brand.test/"),
                          HttpResponse(200, {}, inside, "https://brand.test/top")])
        evidence = []
        found, final, _ = module.scan(lambda *args: next(responses), "https://brand.test/", 1,
                                     evidence=evidence)
        self.assertEqual(found, {"main_brand", "other_brand"})
        self.assertEqual(final, "https://brand.test/top")
        self.assertEqual(len(evidence), 2)
        for response in (HttpResponse(403, {}, inside, "https://brand.test/"),
                         HttpResponse(200, {}, inside, "https://elsewhere.test/")):
            found, _, note = module.scan(lambda *args, response=response: response, "https://brand.test/", 1)
            self.assertEqual(found, set())
            self.assertIn("未取得", note)

    def test_studio_social_ledger_input_includes_existing_socials_and_only_official_sites(self):
        module = load_script("find_studio_socials")
        with sqlite3.connect(":memory:") as connection:
            connection.executescript("""
                CREATE TABLE entity(id INTEGER,kind TEXT,canonical_name TEXT);
                CREATE TABLE entity_link(id INTEGER,entity_id INTEGER,link_kind TEXT,url TEXT);
                INSERT INTO entity VALUES(1,'studio','S-Cute'),(2,'performer','Other');
                INSERT INTO entity_link VALUES(1,1,'official','https://www.s-cute.com/'),
                  (2,1,'social','https://twitter.com/SCute_av'),
                  (3,1,'catalog','https://catalog.test/'),(4,2,'official','https://other.test/');
            """)
            rows = module.ledger_sites(connection)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["known"], {"scute_av"})
        self.assertEqual(rows[0]["site"], "https://www.s-cute.com/")

    def test_studio_social_cli_preserves_review_evidence_on_resume(self):
        module = load_script("find_studio_socials")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, output, review = (root / n for n in ("sites.csv", "scan.csv", "review.csv"))
            module.write_rows(source, ("studio", "site"), [{"studio": "S-Cute", "site": "https://www.s-cute.com/"}])
            argv = ["find_studio_socials.py", "--input", str(source), "--output", str(output),
                    "--review-output", str(review), "--interval", "0"]
            transport = mock.Mock(return_value=HttpResponse(200, {},
                b'<a href="https://x.com/SCute_av">official</a>'))
            with mock.patch.object(sys, "argv", argv), mock.patch.object(module, "HttpxTransport", return_value=transport), redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(), 0)
            saved = review.read_bytes()
            with mock.patch.object(sys, "argv", [*argv, "--resume"]), mock.patch.object(module, "HttpxTransport", return_value=transport), redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(), 0)
            self.assertEqual(transport.call_count, 1)
            self.assertEqual(review.read_bytes(), saved)
            self.assertEqual(module.read_rows(review)[0]["url"], "https://x.com/SCute_av")

    def test_powershell_scripts_with_chinese_carry_a_utf8_bom(self):
        """没有 BOM 的 .ps1，Windows PowerShell 5.1 会按 ANSI（简中系统即 GBK）读。

        `scripts/test.ps1` 的中文 throw 消息因此被解成乱码，引号配对错乱，
        整个脚本在解析期就失败并闪退——报错行还会落在纯 ASCII 的语句上，极难定位。
        pwsh 7 默认按 UTF-8 读无 BOM 文件，所以这个故障只在 5.1 上出现。
        """
        for path in sorted((ROOT / "scripts").glob("*.ps1")):
            raw = path.read_bytes()
            if all(byte < 128 for byte in raw):
                continue
            self.assertTrue(
                raw.startswith(b"\xef\xbb\xbf"),
                f"{path.name} 含非 ASCII 却没有 UTF-8 BOM，PowerShell 5.1 会解析失败",
            )

    def test_every_script_importing_peach_can_run_without_pythonpath(self):
        """脚本是给人直接敲的，不该要求先设 PYTHONPATH。

        2026-09-02 交给用户的 `flatten_release_dirs.py --apply` 第一行就
        ModuleNotFoundError：`from peach.catalog_rules import ...` 在裸 python 下
        找不到 `src`。仓库里 `job_status.py` 等脚本早就带着这段引导，只是没有门槛
        逼后来的脚本跟上。判据只看「导入 peach 之前有没有把 src 挂进 sys.path」，
        不规定写法。
        """
        missing = []
        for path in sorted((ROOT / "scripts").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            lines = source.splitlines()
            peach_import = next(
                (index for index, line in enumerate(lines)
                 if line.startswith(("from peach.", "import peach"))), -1)
            if peach_import < 0:
                continue
            head = "\n".join(lines[:peach_import])
            if "sys.path.insert" not in head and "sys.path.append" not in head:
                missing.append(path.name)
        self.assertEqual(missing, [],
                         "这些脚本导入 peach 前没挂 src，裸 python 跑会 ModuleNotFoundError")

    def test_logo_candidates_are_squared_by_padding_not_discarded(self):
        """界面按方框渲染。接近方的直接用，长条形补背景填方，只有太小的才丢。"""
        import io

        from PIL import Image

        from peach.images import PAD, REJECT, SQUARE, classify, pad_to_square

        self.assertEqual(classify(512, 512)[0], SQUARE)
        self.assertEqual(classify(600, 500)[0], SQUARE, "轻微非方图不必补")
        self.assertEqual(classify(1600, 900)[0], PAD, "16:9 补成方图，而不是丢掉")
        self.assertEqual(classify(64, 64)[0], REJECT, "短边过小，补白也救不回来")

        wide = Image.new("RGBA", (400, 100), (10, 20, 30, 255))
        buffer = io.BytesIO()
        wide.save(buffer, "PNG")
        squared = Image.open(io.BytesIO(pad_to_square(buffer.getvalue())))
        self.assertEqual(squared.size, (400, 400))
        # 补出来的边取原图四角底色，和 Logo 自身背景连成一片，不是凭空刷白。
        self.assertEqual(squared.getpixel((5, 5)), (10, 20, 30, 255))
        self.assertEqual(squared.getpixel((200, 200)), (10, 20, 30, 255))

        # 透明 Logo 的字样可能贴到四角。角上的蓝字不是底色，补边必须继续透明；
        # 否则 PREMIUM 会整张铺蓝，白字 Logo 也会因错误白底而消失。
        transparent = Image.new("RGBA", (400, 100), (0, 0, 0, 0))
        for x in range(80):
            transparent.putpixel((x, 0), (0, 174, 239, 255))
        buffer = io.BytesIO()
        transparent.save(buffer, "PNG")
        squared = Image.open(io.BytesIO(pad_to_square(buffer.getvalue())))
        self.assertEqual(squared.getpixel((5, 5)), (0, 0, 0, 0))
        self.assertEqual(squared.getpixel((200, 200)), (0, 0, 0, 0))
        self.assertEqual(squared.getpixel((20, 150)), (0, 174, 239, 255))

    def test_a_transparent_mark_is_baked_onto_a_white_plate(self):
        """带透明像素的独立图标：裁掉透明边，居中放到白色方底上。

        三处取图位都用 `object-fit: cover` 铺满方框，透明底在深色底上会露出下面
        那一层。边距烤进文件，页面就不必各自补 inset 和 padding。
        """
        import io

        from PIL import Image

        from peach.images import MARK, PLATE_CONTENT_RATIO, bake_square, classify_plate

        source = Image.new("RGBA", (400, 100), (0, 0, 0, 0))
        for x in range(10, 310):
            for y in range(20, 60):
                source.putpixel((x, y), (0, 174, 239, 255))
        buffer = io.BytesIO()
        source.save(buffer, "PNG")
        payload = buffer.getvalue()

        self.assertEqual(classify_plate(payload), MARK)
        baked = bake_square(payload)
        with Image.open(io.BytesIO(baked)) as plate:
            self.assertEqual(plate.size[0], plate.size[1], "烤出来必须是方的")
            self.assertNotIn("A", plate.getbands(), "装进去的文件必须不透明")
            side = plate.size[0]
            self.assertAlmostEqual(300 / side, PLATE_CONTENT_RATIO, places=2,
                                   msg="内容占边长约 76%，四周各留约 12%")
            self.assertEqual(plate.getpixel((2, 2)), (255, 255, 255), "四周是白底")
            self.assertEqual(plate.getpixel((side // 2, side // 2)), (0, 174, 239),
                             "主体居中，像素不缩放")

    def test_an_opaque_plate_keeps_its_own_background(self):
        """完全不透明的图自带底色，那块底是设计的一部分：方的原样返回，长条补方。"""
        import io

        from PIL import Image

        from peach.images import TILE, bake_square, classify_plate

        def opaque(size, color):
            buffer = io.BytesIO()
            Image.new("RGB", size, color).save(buffer, "PNG")
            return buffer.getvalue()

        tile = opaque((400, 400), (12, 12, 12))
        self.assertEqual(classify_plate(tile), TILE)
        self.assertEqual(bake_square(tile), tile, "已经是不透明方图，一个字节都不动")

        strip = opaque((400, 100), (196, 20, 24))
        self.assertEqual(classify_plate(strip), TILE)
        with Image.open(io.BytesIO(bake_square(strip))) as squared:
            self.assertEqual(squared.size, (400, 400))
            self.assertEqual(squared.convert("RGB").getpixel((5, 5)), (196, 20, 24),
                             "补出来的边取原图边缘主色，不刷白")

        self.assertIsNone(classify_plate(b"not an image"))
        self.assertIsNone(bake_square(b"not an image"))

    def test_the_plate_colour_follows_the_marks_own_brightness(self):
        """浅色笔画配深底，自带整块底的一律白底。

        笔画直接挨着底色的标识才会被底色吞掉：白笔画配白底等于把它抹掉。自带整
        块底的标识边界是自己画的，外面那圈只是画框，配深底反而让那块底浮在黑里。
        """
        import io

        from PIL import Image

        from peach.images import bake_square

        def png(image):
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            return buffer.getvalue()

        strokes = Image.new("RGBA", (200, 60), (0, 0, 0, 0))
        for x in list(range(20, 80)) + list(range(120, 180)):
            for y in range(10, 50):
                strokes.putpixel((x, y), (255, 255, 255, 255))
        with Image.open(io.BytesIO(bake_square(png(strokes)))) as plate:
            self.assertEqual(plate.convert("RGB").getpixel((2, 2)), (17, 17, 17),
                             "白笔画配深底才看得见")

        card = Image.new("RGBA", (200, 60), (0, 0, 0, 0))
        for x in range(20, 180):
            for y in range(10, 50):
                card.putpixel((x, y), (255, 255, 255, 255))
        for x in range(60, 140):
            for y in range(24, 36):
                card.putpixel((x, y), (196, 20, 24, 255))
        with Image.open(io.BytesIO(bake_square(png(card)))) as plate:
            self.assertEqual(plate.convert("RGB").getpixel((2, 2)), (255, 255, 255),
                             "自带整块白底的标识，外面那圈跟着它一起白")

    def test_a_square_plate_is_refit_so_the_round_slot_shows_the_whole_mark(self):
        """不透明方图重新摆位：内容太小裁掉留白，会被圆片切掉就补到外接圆。

        小圆片（`.brandpill .mk`）是 32 px 圆、`cover` 铺满，铺满的是整张画布不是
        内容。源站 favicon 常自带大留白，铺进去内容小得认不出；顶到边的实心方标
        四角落在圆外，那部分直接看不见。像素一律不缩放。
        """
        import io
        from math import ceil, hypot

        from PIL import Image, ImageDraw

        from peach.images import PLATE_CONTENT_RATIO, refit_plate

        def png(image):
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            return buffer.getvalue()

        def side(payload):
            with Image.open(io.BytesIO(payload)) as opened:
                self.assertEqual(opened.size[0], opened.size[1])
                return opened.size[0]

        roomy = Image.new("RGB", (400, 400), (255, 255, 255))
        ImageDraw.Draw(roomy).rectangle((160, 160, 239, 239), fill=(196, 20, 24))
        cropped = refit_plate(png(roomy))
        self.assertEqual(side(cropped), round(80 / PLATE_CONTENT_RATIO),
                         "裁到内容框，四周各留约 12%")
        with Image.open(io.BytesIO(cropped)) as plate:
            self.assertEqual(plate.getpixel((plate.width // 2, plate.height // 2)),
                             (196, 20, 24), "像素不缩放，只是换了画布")
        self.assertEqual(refit_plate(cropped), cropped, "摆好的不再动")

        speckled = roomy.copy()
        for spot in ((6, 6), (392, 8), (10, 390), (388, 394)):
            speckled.putpixel(spot, (250, 250, 250))
        self.assertEqual(side(refit_plate(png(speckled))), side(cropped),
                         "有损压缩留下的零星斑点不算内容，撑不开内容框")

        packed = Image.new("RGB", (400, 400), (255, 255, 255))
        ImageDraw.Draw(packed).rectangle((20, 20, 379, 379), fill=(196, 20, 24))
        padded = refit_plate(png(packed))
        self.assertEqual(side(padded), ceil(hypot(359, 359)),
                         "补到内容的外接圆，直径就是内容框的对角线，四角不再被圆片切掉")
        with Image.open(io.BytesIO(padded)) as plate:
            self.assertEqual(plate.getpixel((2, 2)), (255, 255, 255),
                             "补出来的边取原图底色")

        circular = Image.new("RGB", (400, 400), (255, 255, 255))
        ImageDraw.Draw(circular).ellipse((0, 0, 399, 399), fill=(196, 20, 24))
        payload = png(circular)
        self.assertEqual(refit_plate(payload), payload, "本来就是圆的图标一个字节不动")

        strip = png(Image.new("RGB", (400, 100), (196, 20, 24)))
        self.assertEqual(refit_plate(strip), strip, "条状字标的摆位归 pad_to_square 管")

        transparent = png(Image.new("RGBA", (200, 200), (0, 0, 0, 0)))
        self.assertEqual(refit_plate(transparent), transparent,
                         "还没配底的图没有底色可取，补出来的边会变成黑块")
        self.assertIsNone(refit_plate(b"not an image"))

    def test_a_plate_smaller_than_the_round_slot_grows_to_it_without_rescaling(self):
        """短边不够小圆片的实像素就用自己的底色补上去，笔画一个像素都不缩放。

        小圆片是 32 CSS px、2 倍屏 64 实像素。短边不够时浏览器只能放大整张图；
        补边换来的是笔画按原样出图，代价是标识相对圆片小一档。补到内容占宽的下限
        为止——再往外撑就成了另一条规则要裁掉的大留白，两条会来回拉锯。
        """
        import io

        from PIL import Image, ImageDraw

        from peach.images import PLATE_MIN_SIDE, PLATE_MIN_SPAN, refit_plate

        def png(image):
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            return buffer.getvalue()

        def opened(payload):
            with Image.open(io.BytesIO(payload)) as image:
                return image.convert("RGB"), image.size

        small = Image.new("RGB", (48, 48), (18, 140, 220))
        ImageDraw.Draw(small).rectangle((4, 4, 43, 43), fill=(255, 255, 255))
        grown = refit_plate(png(small))
        image, size = opened(grown)
        self.assertEqual(size, (PLATE_MIN_SIDE, PLATE_MIN_SIDE), "补到圆片要的实像素")
        self.assertEqual(image.getpixel((1, 1)), (18, 140, 220), "补出来的边取原图底色")
        self.assertEqual(image.getpixel((PLATE_MIN_SIDE // 2, PLATE_MIN_SIDE // 2)),
                         (255, 255, 255), "内容原样居中，像素不缩放")
        self.assertEqual(refit_plate(grown), grown, "补好的不再动")

        sparse = Image.new("RGB", (40, 40), (18, 140, 220))
        ImageDraw.Draw(sparse).rectangle((16, 16, 27, 27), fill=(255, 255, 255))
        capped = refit_plate(png(sparse))
        _, size = opened(capped)
        self.assertEqual(size, (int(12 / PLATE_MIN_SPAN),) * 2,
                         "内容小的补到占宽下限就停，不补到 64")
        self.assertEqual(refit_plate(capped), capped, "停在下限上，两条规则不再拉锯")

        # 先裁才不够用的那一半：Flower 是 180 的画布上一圈 43 px 的金环，裁掉留白
        # 落在 57，判据看的是这一趟的产物，所以一趟就补到 64，产物是不动点。
        padded = Image.new("RGB", (180, 180), (18, 140, 220))
        ImageDraw.Draw(padded).rectangle((69, 69, 111, 111), fill=(255, 255, 255))
        cropped = refit_plate(png(padded))
        _, size = opened(cropped)
        self.assertEqual(size, (PLATE_MIN_SIDE, PLATE_MIN_SIDE), "裁完不够用的同一趟补上")
        self.assertEqual(refit_plate(cropped), cropped, "一趟定完，重跑不再动")

        big = Image.new("RGB", (PLATE_MIN_SIDE, PLATE_MIN_SIDE), (18, 140, 220))
        ImageDraw.Draw(big).rectangle((8, 8, 55, 55), fill=(255, 255, 255))
        payload = png(big)
        self.assertEqual(refit_plate(payload), payload, "够实像素的一个字节不动")

    def test_a_vector_mark_gets_the_same_plate_without_rasterising(self):
        """矢量标识：白底和边距一样烤进文件，但是包一层外层 SVG，原文档不动。

        VirtualTaboo 装的就是一张 207×70 的透明底字标。栅格化会把「放多大都清晰」
        这个唯一优势丢掉，所以方底由外层 SVG 给，内容整个塞进嵌套 `<svg>`。
        """
        import xml.etree.ElementTree as ElementTree

        from peach.images import (PLATE_CONTENT_RATIO, SVG_NS, bake_square_vector,
                                  vector_image_size)

        def svg(body, box="0 0 207 70"):
            return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{box}">'
                    f'{body}</svg>').encode("utf-8")

        wordmark = ('<svg xmlns="http://www.w3.org/2000/svg" width="207" height="70" '
                    'viewBox="0 0 207 70"><path fill="#D14747" d="M105 49h9v3z"/>'
                    '</svg>').encode("utf-8")

        self.assertEqual(vector_image_size(wordmark), (207.0, 70.0))
        plated = bake_square_vector(wordmark)
        self.assertIn(b'd="M105 49h9v3z"', plated, "原文档照抄，不栅格化")

        root = ElementTree.fromstring(plated)
        side = float(root.get("width"))
        self.assertEqual(root.get("height"), root.get("width"), "包出来必须是方的")
        self.assertAlmostEqual(207 / side, PLATE_CONTENT_RATIO, places=6,
                               msg="长边占边长约 76%，四周各留约 12%")
        plate, inner = list(root)
        self.assertEqual(plate.tag, f"{{{SVG_NS}}}rect")
        self.assertEqual(plate.get("fill"), "#ffffff", "底是白的")
        self.assertEqual(plate.get("width"), root.get("width"), "白底铺满整个方框")
        self.assertEqual(inner.get("viewBox"), "0 0 207 70", "内容自己的坐标系不变")
        self.assertAlmostEqual(float(inner.get("x")), (side - 207) / 2, places=6)
        self.assertAlmostEqual(float(inner.get("y")), (side - 70) / 2, places=6)

        self.assertEqual(bake_square_vector(plated), plated,
                         "已经包过方底的原样返回，重复跑不会越套越多")

        # 白字标配白底等于把标识抹掉：DarkRoomVR 的「DARK ROOM」和
        # TeamSkeetXReislin 的「TEAM」都是白的，白底可见率 0.20 与 0.52。
        def strokes(color):
            return svg(f'<rect x="10" y="10" width="60" height="50" fill="{color}"/>'
                       f'<rect x="137" y="10" width="60" height="50" fill="{color}"/>')

        pale = bake_square_vector(strokes("#ffffff"))
        self.assertEqual(ElementTree.fromstring(pale)[0].get("fill"), "#111111",
                         "浅色内容改配深底")
        self.assertEqual(
            ElementTree.fromstring(bake_square_vector(strokes("#101820")))[0]
            .get("fill"), "#ffffff", "深色内容仍是白底，和位图的 mark 一条规则")
        self.assertEqual(
            ElementTree.fromstring(bake_square_vector(svg(
                '<rect x="10" y="10" width="187" height="50" fill="#ffffff"/>')))[0]
            .get("fill"), "#ffffff", "自带整块底的标识不判底色，外面那圈跟着它一起白")
        # 空壳没有 viewBox 也没有 width／height：比例无从算起，方框边长也就无从定。
        self.assertIsNone(vector_image_size(b'<svg xmlns="http://www.w3.org/2000/svg"/>'))
        self.assertIsNone(bake_square_vector(b'<svg xmlns="http://www.w3.org/2000/svg"/>'))
        self.assertIsNone(bake_square_vector(b"not an image"))
        self.assertIsNone(bake_square_vector(b"<html><body>404</body></html>"))

    def test_studio_avatar_candidates_never_guess_a_handle_by_default(self):
        """猜错 handle 会产出一个「看起来很官方」的错误 Logo，和它要取代的搜索猜测同一种失败。"""
        module = load_script("fetch_studio_avatar_candidates")
        self.assertEqual(module.guess_handle("PREMIUM"), "PREMIUM")
        self.assertEqual(module.guess_handle("S1 NO.1 STYLE"), "S1NO1STYLE")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.csv"
            source.write_text("studio\nBAZOOKA\n", encoding="utf-8-sig")
            output = Path(tmp) / "out.csv"
            with mock.patch.object(sys, "argv", [
                "fetch_studio_avatar_candidates", "--input", str(source), "--output", str(output),
            ]):
                module.main()
            written = list(csv.DictReader(output.open(encoding="utf-8-sig", newline="")))
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0]["confirmation"], "no-handle")
        self.assertEqual(written[0]["accepted"], "False")
        self.assertIn("未取得", written[0]["reason"])

    def test_installed_studio_logos_are_backed_up_then_made_opaque_squares(self):
        """整个目录归一成不透明方图；测试只能写临时目录。

        `*.img` 全在范围内，`<safe>.icon.img` 与 `<safe>.logo.img` 也算。带透明的
        烤白底，不透明的长条补方，矢量包一层白底外层 SVG，已经归一的一个字节都不动。
        """
        from PIL import Image

        module = load_script("normalize_studio_logos")

        def png(image):
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            return buffer.getvalue()

        mark = Image.new("RGBA", (200, 60), (0, 0, 0, 0))
        for x in range(20, 180):
            for y in range(10, 50):
                mark.putpixel((x, y), (0, 174, 239, 255))
        roomy = Image.new("RGB", (200, 200), (255, 255, 255))
        for x in range(80, 120):
            for y in range(80, 120):
                roomy.putpixel((x, y), (196, 20, 24))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "logos"
            backup = Path(tmp).resolve() / "backup"
            root.mkdir()
            originals = {
                "wide.img": png(Image.new("RGB", (400, 100), (196, 20, 24))),
                "flat.icon.img": png(Image.new("RGB", (256, 256), (12, 12, 12))),
                "spot.icon.img": png(roomy),
                "sign.logo.img": png(mark),
                "vector.img": b'<svg xmlns="http://www.w3.org/2000/svg"/>',
                "sign.icon.img": ('<svg xmlns="http://www.w3.org/2000/svg" '
                                  'viewBox="0 0 200 60"><path d="M1 1h2v2z"/>'
                                  '</svg>').encode("utf-8"),
            }
            vectors = {"vector.img", "sign.icon.img"}
            for name, payload in originals.items():
                (root / name).write_bytes(payload)
                Path(f"{root / name}.ct").write_text(
                    "image/svg+xml" if name in vectors else "image/png",
                    encoding="utf-8")

            dry = {str(row["file"]): row for row in module.normalize(root)}
            self.assertEqual(dry["wide.img"]["action"], "would-pad")
            self.assertEqual(dry["wide.img"]["kind"], "tile")
            self.assertEqual(dry["sign.logo.img"]["action"], "would-bake")
            self.assertEqual(dry["sign.logo.img"]["kind"], "mark")
            self.assertEqual(dry["spot.icon.img"]["action"], "would-refit",
                             "已经是方图、只是内容太小的记重新摆位，不冒充补方")
            self.assertEqual(dry["spot.icon.img"]["kind"], "tile")
            self.assertEqual(dry["sign.icon.img"]["action"], "would-plate")
            self.assertEqual(dry["sign.icon.img"]["kind"], "vector")
            self.assertEqual(dry["vector.img"]["action"], "vector",
                             "连内容框都没声明，方框边长无从算起，单列出来而不是记成坏文件")
            self.assertNotIn("flat.icon.img", dry, "已经是不透明方图，不进复核件")
            for name, payload in originals.items():
                self.assertEqual((root / name).read_bytes(), payload, "dry-run 不得改图")
            with self.assertRaises(ValueError):
                module.normalize(root, apply=True)

            applied = {str(row["file"]): row for row in
                       module.normalize(root, apply=True, backup_dir=backup)}
            self.assertEqual(applied["wide.img"]["action"], "padded")
            self.assertEqual(applied["sign.logo.img"]["action"], "baked")
            self.assertEqual(applied["spot.icon.img"]["action"], "refitted")
            self.assertEqual(applied["sign.icon.img"]["action"], "plated")
            self.assertEqual((backup / "sign.icon.img").read_bytes(),
                             originals["sign.icon.img"])
            self.assertIn(b'd="M1 1h2v2z"', (root / "sign.icon.img").read_bytes(),
                          "矢量原文档照抄进外层 SVG，没有被栅格化")
            self.assertEqual(Path(f'{root / "sign.icon.img"}.ct').read_text(
                encoding="utf-8"), "image/svg+xml", "包完还是 SVG，类型不能改成 png")
            self.assertEqual((backup / "wide.img").read_bytes(), originals["wide.img"])
            self.assertFalse((backup / "flat.icon.img").exists(), "没动的文件不备份")
            self.assertEqual((root / "flat.icon.img").read_bytes(),
                             originals["flat.icon.img"])
            self.assertEqual((root / "vector.img").read_bytes(), originals["vector.img"],
                             "矢量标识原样留着")
            self.assertFalse(Path(f'{root / "vector.img"}.normalization.json').exists())

            with Image.open(root / "wide.img") as squared:
                self.assertEqual(squared.size, (400, 400))
            with Image.open(root / "sign.logo.img") as plate:
                self.assertEqual(plate.size[0], plate.size[1])
                self.assertNotIn("A", plate.getbands(), "烤过的文件必须不透明")

            for name, action in (("wide.img", "pad-to-square"),
                                 ("spot.icon.img", "refit-plate"),
                                 ("sign.logo.img", "bake-white-plate"),
                                 ("sign.icon.img", "plate-vector")):
                sidecar = json.loads(
                    Path(f"{root / name}.normalization.json").read_text(encoding="utf-8"))
                self.assertEqual(sidecar["action"], action)
                self.assertEqual(sidecar["original_sha256"],
                                 hashlib.sha256(originals[name]).hexdigest())
                self.assertEqual(sidecar["normalized_sha256"],
                                 hashlib.sha256((root / name).read_bytes()).hexdigest())
                self.assertEqual(sidecar["backup"], str(backup / name))
                if name not in vectors:
                    self.assertEqual(
                        Path(f"{root / name}.ct").read_text(encoding="utf-8"), "image/png")

            # 重跑不再有动作：位图是不透明方图，矢量已经包过方底，归一是幂等的。
            # 量不出内容框的那一行照旧每次都在，它是「还没处理」的记录，不是待办完成。
            self.assertEqual([row["action"] for row in module.normalize(root)],
                             ["vector"])

    def test_normalising_again_takes_the_backup_original_as_its_input(self):
        """已归一的文件重跑时输入取边车记的备份原图。

        烤底会毁掉透明通道，配错的底色在产物上再也判不回来。从原图重来，算法的
        改进才能落到已经装好的文件上；边车继续指向原图，别把指针改指到这一轮备份
        的归一产物。备份不在本机时只能拿现装的文件当输入。
        """
        from PIL import Image

        module = load_script("normalize_studio_logos")

        def png(image):
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            return buffer.getvalue()

        strokes = Image.new("RGBA", (200, 60), (0, 0, 0, 0))
        for x in list(range(20, 80)) + list(range(120, 180)):
            for y in range(10, 50):
                strokes.putpixel((x, y), (255, 255, 255, 255))
        original = png(strokes)
        swallowed = Image.new("RGB", (211, 211), (255, 255, 255))
        swallowed.paste(strokes.convert("RGB"), (5, 75), strokes)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "logos"
            archive = Path(tmp).resolve() / "archive"
            backup = Path(tmp).resolve() / "backup"
            root.mkdir()
            archive.mkdir()
            (archive / "sign.img").write_bytes(original)
            installed = png(swallowed)
            (root / "sign.img").write_bytes(installed)
            Path(f'{root / "sign.img"}.ct').write_text("image/png", encoding="utf-8")
            Path(f'{root / "sign.img"}.normalization.json').write_text(json.dumps({
                "action": "bake-white-plate", "kind": "mark",
                "backup": str(archive / "sign.img"),
            }), encoding="utf-8")

            applied = {str(row["file"]): row for row in
                       module.normalize(root, apply=True, backup_dir=backup)}
            self.assertEqual(applied["sign.img"]["action"], "baked")
            self.assertEqual(applied["sign.img"]["backup"], str(archive / "sign.img"))
            self.assertEqual(applied["sign.img"]["before_sha256"],
                             hashlib.sha256(installed).hexdigest(),
                             "复核件上的 before 是被替换掉的那个文件")
            with Image.open(root / "sign.img") as plate:
                self.assertEqual(plate.convert("RGB").getpixel((2, 2)), (17, 17, 17),
                                 "从原图重来才判得出白笔画该配深底")
            sidecar = json.loads(Path(f'{root / "sign.img"}.normalization.json')
                                 .read_text(encoding="utf-8"))
            self.assertEqual(sidecar["backup"], str(archive / "sign.img"),
                             "边车继续指向原图")
            self.assertEqual(sidecar["original_sha256"],
                             hashlib.sha256(original).hexdigest())
            self.assertEqual((backup / "sign.img").read_bytes(), installed,
                             "这一轮换掉的文件也留一份")
            self.assertEqual([row["action"] for row in module.normalize(root)], [],
                             "原图再走一遍还是同一个产物，归一是幂等的")

    def test_frame_retry_is_reserved_for_bad_color_metadata(self):
        """坏色彩元数据才重试。无条件重试会让网盘超时的文件每帧白跑两次 45 秒。"""
        sheets = self.sheets
        for stderr, expected in (
            ("[swscale] Unsupported color primaries: reserved", [False, True]),
            ("color_trc reserved is invalid", [False, True]),
            ("ffmpeg timeout", [False]),
            ("Error opening input: Input/output error", [False]),
        ):
            calls: list[bool] = []

            def fake_capture(_ffmpeg, _path, _timestamp, _destination, color_override,
                             _stderr=stderr, _calls=calls):
                _calls.append(color_override)
                return False, _stderr

            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(sheets, "_capture_frame", fake_capture):
                    sheets.make_sheet("ffmpeg", "R:/media/one.mp4", 100.0,
                                      Path(tmp) / "sheet.jpg", frames=1)
            self.assertEqual(calls, expected, stderr)

    def test_failed_sheet_says_whether_the_source_or_the_duration_is_wrong(self):
        """asset 12510 与 18349 都只报「失败」，一个是片源头坏、一个是账本时长记错。

        分不出来就没法决定该修片源还是修账本，所以原因必须能区分。
        """
        sheets = self.sheets

        def capture_none(_ffmpeg, _path, _timestamp, _destination, color_override):
            return False, "missing mandatory atoms, broken header"

        def capture_first_only(_ffmpeg, _path, timestamp, destination, color_override):
            # 账本时长比真实文件长时，只有最早的采样点还落在文件里。
            if timestamp > 100.0:
                return False, ""
            destination.write_bytes(b"x" * 2048)
            return True, ""

        for capture, expected in (
            (capture_none, "broken_source"),
            (capture_first_only, "duration_mismatch"),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(sheets, "_capture_frame", capture):
                    ok, reason = sheets.make_sheet(
                        "ffmpeg", "R:/media/one.mp4", 752.24, Path(tmp) / "sheet.jpg", frames=9,
                    )
            self.assertFalse(ok)
            self.assertEqual(reason, expected)

    def test_sheet_failure_reason_reaches_the_log(self):
        """原因只写在返回值里等于没写；批次日志和汇总都要能看到。"""
        sheets = self.sheets
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE asset(id INTEGER PRIMARY KEY,location TEXT,path TEXT,"
                "medium TEXT,duration REAL,size INTEGER,snapshot_path TEXT,disposal TEXT)"
            )
            connection.execute(
                "INSERT INTO asset VALUES(18349,'local',?,'video',752.24,1000,NULL,NULL)",
                (str(root / "one.mp4"),),
            )
            connection.commit()
            connection.close()

            args = sheets.build_parser().parse_args([
                "--db", str(db), "--workers", "1", "--min-free", "0",
                "--output-root", str(root / "out"), "--log-dir", str(root / "log"),
            ])
            choice = type("C", (), {"path": "ffmpeg"})
            with mock.patch.object(
                sheets, "make_sheet",
                lambda *_args, **_kwargs: (False, "duration_mismatch"),
            ), mock.patch.object(sheets.FFmpegResolver, "ffmpeg", lambda _self: choice), \
                    redirect_stdout(io.StringIO()):
                sheets.run(args)

            written = "\n".join(p.read_text(encoding="utf-8")
                                for p in (root / "log").glob("sheets-*.log"))
        self.assertIn("18349", written)
        self.assertIn("duration_mismatch", written)
        self.assertIn("失败原因：duration_mismatch 1", written)

    def test_sheets_can_reshoot_one_named_asset_over_a_stale_product(self):
        """点名重抽是为了盖掉上一次的错结果，撞上已存在的产物就短路等于没修。"""
        sheets = self.sheets
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE asset(id INTEGER PRIMARY KEY,location TEXT,path TEXT,"
                "medium TEXT,duration REAL,size INTEGER,snapshot_path TEXT,disposal TEXT)"
            )
            # 18349 已有陈旧产物且已登记；1 是同来源的另一条待抽项，不该被顺带带走。
            connection.execute(
                "INSERT INTO asset VALUES(18349,'local',?,'video',110.87,2000,'stale.jpg',NULL)",
                (str(root / "one.mp4"),),
            )
            connection.execute(
                "INSERT INTO asset VALUES(1,'local',?,'video',600.0,1000,NULL,NULL)",
                (str(root / "two.mp4"),),
            )
            connection.commit()
            connection.close()

            output_root = root / "out"
            stale = sheets.output_path(output_root, "local", str(root / "one.mp4"))
            stale.write_bytes(b"x" * 8192)

            args = sheets.build_parser().parse_args([
                "--db", str(db), "--workers", "1", "--min-free", "0",
                "--asset", "18349",
                "--output-root", str(output_root), "--log-dir", str(root / "log"),
            ])

            shot: list[str] = []

            def fake_sheet(_ffmpeg, path, _duration, destination, _frames):
                shot.append(path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"y" * 9000)
                return True, ""

            choice = type("C", (), {"path": "ffmpeg"})
            with mock.patch.object(sheets, "make_sheet", fake_sheet),                  mock.patch.object(sheets.FFmpegResolver, "ffmpeg", lambda _self: choice),                  redirect_stdout(io.StringIO()):
                sheets.run(args)

            connection = sqlite3.connect(db)
            snapshots = dict(connection.execute("SELECT id,snapshot_path FROM asset").fetchall())
            connection.close()
            rewritten = stale.read_bytes()

        self.assertEqual(len(shot), 1, "点名只该抽这一条")
        self.assertIn("one.mp4", shot[0])
        self.assertEqual(rewritten[:1], b"y", "陈旧产物必须被真正覆盖")
        self.assertIsNone(snapshots[1], "没点名的待抽项不该被这一趟带走")

    def test_sheets_obeys_preview_mode_and_never_extracts_pointer_or_disc_frames(self):
        with mock.patch.object(self.sheets, "metadata_only", return_value=True), \
                mock.patch.object(self.sheets.FFmpegResolver, "ffmpeg") as resolver:
            with self.assertRaisesRegex(self.sheets.JobPolicyError, "视频预览生成已关闭"):
                self.sheets.run(self.sheets.build_parser().parse_args([]))
            resolver.assert_not_called()
        with mock.patch.object(self.sheets, "_capture_frame") as capture:
            for source in ("movie.strm", "movie.ISO"):
                self.assertEqual(self.sheets.make_sheet("ffmpeg", source, 60, Path("unused.jpg"), 9),
                                 (False, "unsupported_source"))
            capture.assert_not_called()

    def test_sheets_stops_extracting_frames_when_preview_mode_is_disabled(self):
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(self.sheets, "metadata_only", side_effect=[False, True]), \
                mock.patch.object(self.sheets, "_capture_frame", return_value=(False, "")) as capture:
            result = self.sheets.make_sheet("ffmpeg", "movie.mp4", 60, Path(temporary) / "sheet.jpg", 9)
        self.assertEqual(result, (False, "previews_disabled"))
        self.assertEqual(capture.call_count, 1)

    def test_sheets_leaves_the_recycle_bin_alone_unless_a_row_is_named(self):
        """回收站里的行不领，点名的除外。对它们抽帧每次都要向网盘发一次注定失败的读请求，那一轮
        115 抽帧的 73 条 `broken_source` 里 72 条是这种行。
        """
        sheets = self.sheets
        for named, expected in ((False, ["keep.mp4"]), (True, ["trashed.mp4"])):
            with self.subTest(named=named), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                db = root / "ledger.db"
                connection = sqlite3.connect(db)
                connection.execute(
                    "CREATE TABLE asset(id INTEGER PRIMARY KEY,location TEXT,path TEXT,"
                    "medium TEXT,duration REAL,size INTEGER,snapshot_path TEXT,disposal TEXT)"
                )
                connection.execute(
                    "INSERT INTO asset VALUES(1,'local',?,'video',600.0,2000,NULL,'trash')",
                    (str(root / "trashed.mp4"),),
                )
                connection.execute(
                    "INSERT INTO asset VALUES(2,'local',?,'video',600.0,1000,NULL,NULL)",
                    (str(root / "keep.mp4"),),
                )
                connection.commit()
                connection.close()

                argv = ["--db", str(db), "--workers", "1", "--min-free", "0",
                        "--output-root", str(root / "out"), "--log-dir", str(root / "log")]
                if named:
                    argv += ["--asset", "1"]
                args = sheets.build_parser().parse_args(argv)

                shot: list[str] = []

                def fake_sheet(_ffmpeg, path, _duration, destination, _frames, shot=shot):
                    shot.append(Path(path).name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(b"x" * 8192)
                    return True, ""

                choice = type("C", (), {"path": "ffmpeg"})
                with mock.patch.object(sheets, "make_sheet", fake_sheet), \
                        mock.patch.object(sheets.FFmpegResolver, "ffmpeg",
                                          lambda _self, choice=choice: choice), \
                        redirect_stdout(io.StringIO()):
                    sheets.run(args)
                self.assertEqual(shot, expected)

    def test_sheets_stops_mid_run_when_the_disk_gate_trips(self):
        """验证接线，不只是 DiskGuard 类本身：起跑通过、运行中触线要真的停并报非零码。"""
        sheets = self.sheets
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE asset(id INTEGER PRIMARY KEY,location TEXT,path TEXT,"
                "medium TEXT,duration REAL,size INTEGER,snapshot_path TEXT,disposal TEXT)"
            )
            for asset_id in range(1, 61):
                connection.execute(
                    "INSERT INTO asset VALUES(?,?,?,?,?,?,NULL,NULL)",
                    (asset_id, "local", str(root / f"{asset_id}.mp4"), "video", 600.0, 1000),
                )
            connection.commit()
            connection.close()

            args = sheets.build_parser().parse_args([
                "--db", str(db), "--workers", "1", "--min-free", "40",
                "--disk-check-secs", "0",
                "--output-root", str(root / "out"), "--log-dir", str(root / "log"),
            ])

            roomy = type("U", (), {"free": 500 * 1024**3})
            starved = type("U", (), {"free": 1 * 1024**3})
            calls = {"n": 0}

            def shrinking_disk(_path):
                # 起跑线检查看到充裕空间；运行几步之后盘被外部吃光。
                calls["n"] += 1
                return roomy if calls["n"] <= 2 else starved

            written = []

            def fake_sheet(_ffmpeg, path, _duration, destination, _frames):
                written.append(path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"x" * 8192)
                return True, ""

            choice = type("C", (), {"path": "ffmpeg"})
            with mock.patch.object(sheets, "make_sheet", fake_sheet), \
                 mock.patch.object(sheets.FFmpegResolver, "ffmpeg", lambda _self: choice), \
                 mock.patch("peach.jobs.shutil.disk_usage", shrinking_disk), \
                 redirect_stdout(io.StringIO()):
                code = sheets.run(args)

            self.assertEqual(code, 3, "磁盘闸门中止必须体现在退出码上")
            self.assertLess(len(written), 60, "触线后不能把剩余任务跑完")
            # 已完成的部分必须已经入库，否则续跑会重复下载。
            connection = sqlite3.connect(db)
            registered = connection.execute(
                "SELECT count(*) FROM asset WHERE snapshot_path IS NOT NULL").fetchone()[0]
            connection.close()
            self.assertEqual(registered, len(written))

    def test_traffic_ceiling_can_cover_direct_sources(self):
        """115 实测走 DIRECT。计费来源若也直连，只算代理等于没有闸门。"""
        accumulate = self.traffic_watch.accumulate
        previous = {"a": (100, True, "cdn.example"), "b": (100, False, "proxy.example")}
        current = {"a": (700, True, "cdn.example"), "b": (400, False, "proxy.example")}

        counted, uncounted, hosts = accumulate(previous, current, False)
        self.assertEqual((counted, uncounted), (300, 600))
        self.assertEqual(hosts, {"proxy.example": 300})

        counted, uncounted, hosts = accumulate(previous, current, True)
        self.assertEqual((counted, uncounted), (900, 0))
        self.assertEqual(hosts, {"cdn.example": 600, "proxy.example": 300})

        # 连接被回收后重新编号会让计数倒退；倒退不能反向抵扣已用预算。
        self.assertEqual(accumulate({"a": (900, True, "h")}, {"a": (5, True, "h")}, True)[0], 0)
        self.assertFalse(self.traffic_watch.build_parser().parse_args([]).count_direct)

    def test_probe_never_records_an_unknown_duration_as_zero(self):
        """0 会同时躲过 probe 的 `duration IS NULL` 和抽帧的 `duration>2`，永久卡住。"""
        from peach import media_probe as module

        class _Empty:
            returncode = 0
            stderr = b""
            stdout = b'{"format":{},"streams":[{"width":0,"height":0}]}'

        original = module.subprocess.run
        module.subprocess.run = lambda *args, **kwargs: _Empty()
        try:
            duration, width, height, codec, fps, audio = module.probe_file("ffprobe", "x.mp4")
        finally:
            module.subprocess.run = original
        self.assertEqual(duration, -1.0)
        self.assertEqual((width, height, codec), (0, 0, None))
        self.assertEqual(module.context_fields(width, height, duration), (None, None, None))

    def test_probe_redo_separates_unprobed_from_failed(self):
        selection = self.probe.duration_selection
        self.assertEqual(selection("none"), "duration IS NULL")
        self.assertEqual(selection("zero"), "(duration IS NULL OR duration=0)")
        self.assertEqual(selection("failed"), "(duration IS NULL OR duration<0)")
        self.assertEqual(selection("all"), "(duration IS NULL OR duration<=0)")
        self.assertEqual(self.probe.build_parser().parse_args([]).redo, "none")
        self.assertEqual(self.probe.build_parser().parse_args(["--redo", "zero"]).redo, "zero")

    def test_probe_can_target_one_asset_whose_recorded_duration_is_wrong(self):
        """asset 18349 账本记 752.24 秒、真实文件 110.87 秒，--redo 的 0/-1 判据够不着。

        点名重探时不套时长筛选，但计费来源边界必须照旧生效。
        """
        probe = self.probe
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE asset(id INTEGER PRIMARY KEY,location TEXT,path TEXT,"
                "medium TEXT,duration REAL,size INTEGER,width INTEGER,height INTEGER,"
                "vcodec TEXT,fps REAL,has_audio INTEGER,ctx_length TEXT,ctx_orient TEXT,"
                "ctx_quality TEXT,disposal TEXT)"
            )
            for asset_id, duration in ((18349, 752.24), (1, None)):
                connection.execute(
                    "INSERT INTO asset(id,location,path,medium,duration,size) "
                    "VALUES(?,'local',?,'video',?,1000)",
                    (asset_id, str(root / f"{asset_id}.mp4"), duration),
                )
            connection.commit()
            connection.close()

            args = probe.build_parser().parse_args([
                "--db", str(db), "--workers", "1", "--min-free", "0",
                "--asset", "18349", "--log-dir", str(root / "log"),
                "--lock", str(root / "probe.lock"),
            ])
            self.assertEqual(args.asset, [18349])

            probed: list[str] = []
            choice = type("C", (), {"path": "ffprobe"})

            def fake_probe(_ffprobe, path, _timeout):
                probed.append(path)
                return 110.866667, 1920, 1072, "h264", 30.0, None

            with mock.patch.object(probe, "probe_file", fake_probe),                  mock.patch.object(probe.FFmpegResolver, "ffprobe", lambda _self: choice),                  redirect_stdout(io.StringIO()):
                probe.run(args)

            connection = sqlite3.connect(db)
            rows = dict(connection.execute("SELECT id,duration FROM asset").fetchall())
            connection.close()

        self.assertEqual(len(probed), 1, "点名只该动这一条，不能顺带重探全库")
        self.assertAlmostEqual(rows[18349], 110.866667, places=5)
        self.assertIsNone(rows[1], "没点名的未探测条目不该被这一趟带走")

    def test_sheet_retries_reserved_color_metadata_frames(self):
        """prim:reserved 会被 swscale 拒绝；首次失败后用 bt709 声明兜底重试。

        符玄12.mp4 实测：`scale=480:-1` 报 Error -129，覆盖色彩声明后正常。
        """
        from PIL import Image

        sheets = self.sheets
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "sheet.jpg"
            calls = []

            def fake_run(command, **kwargs):
                calls.append(list(command))
                if "-filter_complex" in command:
                    Image.new("RGB", (1440, 810), "black").save(dest, "JPEG")
                    return type("R", (), {"returncode": 0})()
                target = Path(command[-1])
                if "bt709" in command:
                    Image.new("RGB", (480, 270), "black").save(target, "JPEG")
                    return type("R", (), {"returncode": 0, "stderr": b""})()
                return type("R", (), {
                    "returncode": 1,
                    "stderr": b"[swscale] Unsupported color primaries: reserved",
                })()

            capture = sheets.frame_capture
            original = capture.subprocess.run
            capture.subprocess.run = fake_run
            try:
                ok, reason = sheets.make_sheet("ffmpeg", "reserved.mp4", 600.0, dest, 9)
            finally:
                capture.subprocess.run = original
            self.assertTrue(ok, reason)
            self.assertEqual(
                sum(1 for c in calls if "bt709" in c), 9,
                "9 帧都应在首次失败后用色彩覆盖重试",
            )

    def test_a_separatorless_code_needs_release_evidence(self):
        """`PBD390` 与目录名 `WX17` 长得一样，只有厂牌、发行日或出演者分得开。"""
        explicit = self.scrape_codes._is_explicit_code
        for code in ("ABW-123", "fc2ppv-1234567", "259LUXU-1475", "n1042"):
            self.assertTrue(explicit(code), code)
        for code in ("WX17", "PBD390", "ipvr00296", "RAIKUN325", "BANBI_555"):
            self.assertFalse(explicit(code), code)
            self.assertTrue(explicit(code, release_evidence=True), code)
        for code in ("", "合集", "未知厂牌", "4K", "FC2-1234"):
            self.assertFalse(explicit(code, release_evidence=True), code)

    def test_a_directory_label_stored_as_code_is_never_queried(self):
        """合集包 `WX17` 被规范成 `WX-017` 问 javbus，取回的是另一部片 `WXSD-017`。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            sqlite3.connect(db).close()
            upgrade(db, MIGRATIONS)
            connection = sqlite3.connect(db)
            connection.executemany(
                "INSERT INTO asset(id,location,path,name,medium,code,size,studio) "
                "VALUES(?,'local',?,?,'video',?,?,?)",
                [(1, "pack.mp4", "EllieLeen (1).mp4", "WX17", 10_000, None),
                 (2, "pbd.mp4", "pbd.mp4", "PBD390", 1_000, "プレミアム")],
            )
            connection.commit()
            connection.close()

            class FakeProvider:
                def __init__(self):
                    self.calls = []

                def query(self, code, source):
                    self.calls.append((code, source))
                    return {"source": source, "series": "Series"}

            def scrape(*extra):
                provider = FakeProvider()
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
                    try:
                        result = self.scrape_codes.main([
                            "--db", str(db), "--out", str(root / "candidates.csv"),
                            "--raw-dir", str(root / "raw"), "--log-dir", str(root / "logs"),
                            "--delay", "0", "--min-free", "0", "--sources", "r18dev", *extra,
                        ], provider=provider)
                    except SystemExit as exit_:
                        result = exit_.code
                return result, provider.calls, errors.getvalue()

            result, calls, _ = scrape()
            self.assertEqual(result, 0)
            self.assertEqual(calls, [("PBD-390", "r18dev")])

            codes_file = root / "codes.txt"
            codes_file.write_text("WX17\n", encoding="utf-8")
            result, calls, errors = scrape("--codes-file", str(codes_file))
            self.assertNotEqual(result, 0)
            self.assertEqual(calls, [])
            self.assertIn("目录名", errors)
            self.assertNotIn("不存在", errors)

    def test_repost_site_watermarks_are_never_queued_for_a_provider(self):
        # `HHD800`、`HJD2048` 是转载站域名剥掉 TLD 后的样子，不是番号。查它们只会
        # 白跑一轮限流预算，而 provider 的空结果又会被当成「这个番号没元数据」。
        for code in ("HHD800", "hhd800.com", "HJD2048", "AAVV333", "KFA33", "BEI88"):
            self.assertFalse(self.scrape_codes._is_explicit_code(code), code)

    def test_code_normalization(self):
        # 归一化本体已收进 catalog_rules；脚本只是 import 它，这里验的是脚本用的
        # 确实是那一份，而不是自己又抄了一个同名函数。
        normalise = self.scrape_codes.normalise_code_key
        self.assertEqual(normalise("fc2ppv-1234567"), "FC2-PPV-1234567")
        self.assertEqual(normalise("abw123"), "ABW-123")
        self.assertEqual(normalise("ipvr00296"), "IPVR-296")

    def test_explicit_sources_write_field_candidates_and_raw_evidence_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            sqlite3.connect(db).close()
            upgrade(db, MIGRATIONS)
            connection = sqlite3.connect(db)
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,code) "
                "VALUES(1,'local','one.mp4','one.mp4','video','ABC-001')"
            )
            connection.commit()
            connection.close()

            output = root / "metadata-field-candidates-20260822.csv"
            raw = root / "sources"
            class FakeProvider:
                def __init__(self):
                    self.calls = []
                def query(self, code, source):
                    self.calls.append((code, source))
                    return {
                        "source": source,
                        "source_url": ("https://r18.dev/example" if source == "r18dev"
                                       else "https://www.javbus.com/ABC-001"),
                        # r18.dev 的标题与系列只认日文视图，顶层那份是它自己的英文译文。
                        "translations": ([{"language": "ja", "title": "カタログ題", "series": "シリーズA"}]
                                         if source == "r18dev" else []),
                        "id": "ABC-001", "content_id": "abc00001",
                        "title": "Catalog title", "original_title": "原标题",
                        "runtime": 121, "director": "Director A", "label": "Label A",
                        "poster_url": "https://img.example/poster.jpg",
                        "cover_url": "https://img.example/cover.jpg",
                        "screenshot_urls": ["https://img.example/1.jpg"],
                        "trailer_url": "https://video.example/trailer.m3u8",
                        "maker": "Studio A" if source == "r18dev" else "Studio B",
                        "series": "Series A", "release_date": "2020-09-13T00:00:00Z",
                        "actresses": [{"dmm_id": 7, "japanese_name": "木村さん 木村さん"}],
                        "genres": ["Foot Fetish", "Anal", "Unknown"],
                    }
            provider = FakeProvider()
            with redirect_stdout(io.StringIO()):
                result = self.scrape_codes.main([
                    "--db", str(db), "--out", str(output), "--raw-dir", str(raw),
                    "--log-dir", str(root / "logs"), "--delay", "0",
                    "--min-free", "0",
                    "--sources", "javbus,r18dev",
                ], provider=provider)
            self.assertEqual(result, 0)
            self.assertEqual(provider.calls, [("ABC-001", "javbus"), ("ABC-001", "r18dev")])

            with output.open(encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual({row["field"] for row in rows},
                             {"title", "original_title", "performers", "studio",
                              "series", "release_date", "tags"})
            performer = next(row for row in rows if row["field"] == "performers")
            candidates = __import__("json").loads(performer["candidates_json"])
            self.assertEqual({candidate["source"] for candidate in candidates}, {"r18dev", "javbus"})
            self.assertEqual(candidates[0]["display_value"], "木村さん")
            self.assertIn("已规范化", candidates[0]["warnings"][0])
            self.assertEqual(candidates[0]["provider_id"], "ABC-001")
            self.assertEqual(candidates[0]["content_id"], "abc00001")
            self.assertEqual(candidates[0]["catalog_evidence"]["label"]["value"], "Label A")
            self.assertEqual(
                candidates[0]["catalog_evidence"]["screenshot_urls"]["value"],
                ["https://img.example/1.jpg"],
            )
            release = next(row for row in rows if row["field"] == "release_date")
            self.assertEqual(__import__("json").loads(release["candidates_json"])[0]["value"],
                             "2020-09-13")
            tag_candidates = __import__("json").loads(
                next(row for row in rows if row["field"] == "tags")["candidates_json"])
            self.assertEqual([candidate["source"] for candidate in tag_candidates],
                             ["r18dev", "javbus"], "官方 tag 来源必须排在社区来源前")
            self.assertTrue(tag_candidates[0]["official"])
            self.assertEqual(tag_candidates[0]["profile"], "custom")
            self.assertEqual(tag_candidates[0]["policy_version"],
                             "metadata-source-policy-v6")
            # 位次跟着 `metadata_policy.FIELD_SOURCE_ORDER` 里 tags 那一行走：
            # 前面每插进一个来源，r18dev 就往后挪一格。
            self.assertEqual(tag_candidates[0]["field_rank"], 12)
            self.assertEqual(tag_candidates[0]["source_kind"], "official_mirror")
            self.assertTrue(all(row["source_profile"] == "custom" for row in rows))
            self.assertTrue((raw / "ABC-001" / "r18dev.json").is_file())
            self.assertTrue((raw / "ABC-001" / "javbus.json").is_file())

            health = output.with_name("metadata-source-health-20260822.csv")
            with health.open(encoding="utf-8-sig", newline="") as handle:
                health_rows = {row["source"]: row for row in csv.DictReader(handle)}
            self.assertEqual(set(health_rows), {"javbus", "r18dev"})
            self.assertEqual(health_rows["r18dev"]["profile"], "custom")
            self.assertEqual(health_rows["r18dev"]["attempted"], "1")
            self.assertEqual(health_rows["r18dev"]["fetched"], "1")
            self.assertEqual(health_rows["r18dev"]["succeeded"], "1")
            self.assertEqual(health_rows["r18dev"]["release_date"], "1")
            self.assertEqual(health_rows["r18dev"]["title"], "1")
            self.assertEqual(health_rows["r18dev"]["trailer_url"], "1")

            connection = sqlite3.connect(db)
            asset = connection.execute(
                "SELECT creator,studio,series FROM asset WHERE id=1"
            ).fetchone()
            relation_count = connection.execute("SELECT count(*) FROM asset_entity").fetchone()[0]
            connection.close()
            self.assertEqual(asset, (None, None, None))
            self.assertEqual(relation_count, 0)

    def test_scrape_codes_file_limits_batch_in_file_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            sqlite3.connect(db).close()
            upgrade(db, MIGRATIONS)
            connection = sqlite3.connect(db)
            connection.executemany(
                "INSERT INTO asset(id,location,path,name,medium,code,size) "
                "VALUES(?,'local',?,?,'video',?,?)",
                [(1, "large.mp4", "large.mp4", "AAA-001", 10_000),
                 (2, "small.mp4", "small.mp4", "BBB-002", 1_000)],
            )
            connection.commit()
            connection.close()
            codes_file = root / "codes.txt"
            codes_file.write_text("# exact batch\nBBB002\n", encoding="utf-8")

            class FakeProvider:
                def __init__(self):
                    self.calls = []

                def query(self, code, source):
                    self.calls.append((code, source))
                    return {"source": source, "series": "Series B"}

            provider = FakeProvider()
            output = root / "candidates.csv"
            with redirect_stdout(io.StringIO()):
                result = self.scrape_codes.main([
                    "--db", str(db), "--out", str(output),
                    "--raw-dir", str(root / "raw"), "--log-dir", str(root / "logs"),
                    "--delay", "0", "--min-free", "0", "--sources", "r18dev",
                    "--codes-file", str(codes_file),
                ], provider=provider)
            self.assertEqual(result, 0)
            self.assertEqual(provider.calls, [("BBB-002", "r18dev")])
            with output.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual({row["query"] for row in rows}, {"BBB-002"})

    def test_resume_throttles_only_real_network_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            sqlite3.connect(db).close(); upgrade(db, MIGRATIONS)
            connection = sqlite3.connect(db)
            connection.executemany(
                "INSERT INTO asset(id,location,path,name,medium,code,size) "
                "VALUES(?,'local',?,?,'video',?,?)",
                [(1, "cached.mp4", "cached.mp4", "AAA-001", 2_000),
                 (2, "missing.mp4", "missing.mp4", "BBB-002", 1_500),
                 (3, "fresh.mp4", "fresh.mp4", "CCC-003", 1_000)],
            )
            connection.commit(); connection.close()
            raw = root / "raw"
            snapshot = raw / "AAA-001" / "r18dev.json"
            snapshot.parent.mkdir(parents=True)
            # 真实快照一定带得出番号身份；不带的现在按「对不上」重新联网问。
            snapshot.write_text(__import__("json").dumps({
                "result": {"source": "r18dev", "id": "AAA-001", "maker": "Cached Studio"},
            }), encoding="utf-8")
            missing = raw / "BBB-002" / "r18dev.json"
            missing.parent.mkdir(parents=True)
            missing.write_text(__import__("json").dumps({
                "error": {"kind": "not_found", "message": "status 404",
                          "status_code": 404, "retryable": False, "temporary": False},
            }), encoding="utf-8")

            class Provider:
                def __init__(self): self.calls = []
                def query(self, code, source):
                    self.calls.append((code, source))
                    return {"source": source, "maker": "Fresh Studio"}

            provider = Provider()
            errors = root / "errors.csv"
            with mock.patch.object(self.scrape_codes.time, "sleep") as sleep:
                with redirect_stdout(io.StringIO()):
                    result = self.scrape_codes.main([
                        "--db", str(db), "--out", str(root / "candidates.csv"),
                        "--errors", str(errors),
                        "--raw-dir", str(raw), "--log-dir", str(root / "logs"),
                        "--delay", "2", "--min-free", "0", "--sources", "r18dev",
                    ], provider=provider)
            self.assertEqual(result, 0)
            self.assertEqual(provider.calls, [("CCC-003", "r18dev")])
            self.assertEqual(sleep.call_count, 1, "本地快照不能消耗来源限流等待")
            with errors.open(encoding="utf-8-sig", newline="") as handle:
                error_rows = list(csv.DictReader(handle))
            self.assertEqual([(row["code"], row["status_code"]) for row in error_rows],
                             [("BBB-002", "404")])

    def test_metadata_health_distinguishes_snapshot_empty_error_and_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            sqlite3.connect(db).close(); upgrade(db, MIGRATIONS)
            connection = sqlite3.connect(db)
            connection.executemany(
                "INSERT INTO asset(id,location,path,name,medium,code,size) "
                "VALUES(?,'local',?,?,'video',?,?)",
                [(1, "1.mp4", "1.mp4", "ABC-001", 3_000),
                 (2, "2.mp4", "2.mp4", "DEF-002", 2_000),
                 (3, "3.mp4", "3.mp4", "GHI-003", 1_000)],
            )
            connection.commit(); connection.close()
            raw = root / "raw"
            snapshot = raw / "ABC-001" / "r18dev.json"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text(__import__("json").dumps({
                "result": {"source": "r18dev", "id": "ABC-001", "maker": "Studio A"},
            }), encoding="utf-8")

            class HealthProvider:
                def __init__(self): self.calls = []
                def query(self, code, source):
                    self.calls.append((code, source))
                    if source == "javbus" and code == "DEF-002":
                        raise self_error(
                            "rate limited", kind="rate_limited", status_code=429,
                            retryable=True, temporary=True,
                        )
                    if source == "javbus":
                        return {"source": source}
                    return {"source": source, "maker": "Studio A"}

            self_error = self.scrape_codes.MetadataProviderError
            provider = HealthProvider()
            output = root / "metadata-field-candidates-health.csv"
            health = root / "health.csv"
            with redirect_stdout(io.StringIO()):
                result = self.scrape_codes.main([
                    "--db", str(db), "--out", str(output), "--health", str(health),
                    "--raw-dir", str(raw), "--log-dir", str(root / "logs"),
                    "--delay", "0", "--min-free", "0", "--sources", "r18dev,javbus",
                ], provider=provider)
            self.assertEqual(result, 0)
            with health.open(encoding="utf-8-sig", newline="") as handle:
                rows = {row["source"]: row for row in csv.DictReader(handle)}
            self.assertEqual(rows["r18dev"]["snapshot_reused"], "1")
            self.assertEqual(rows["r18dev"]["fetched"], "2")
            self.assertEqual(rows["javbus"]["attempted"], "3")
            # 限流那一条之后的番号照问，所以三条都联了网。
            self.assertEqual(rows["javbus"]["fetched"], "3")
            self.assertEqual(rows["javbus"]["succeeded"], "2")
            self.assertEqual(rows["javbus"]["empty"], "2")
            self.assertEqual(rows["javbus"]["errors"], "1")
            self.assertEqual(rows["javbus"]["retryable_errors"], "1")
            # 单次可重试失败不再让来源停摆：后面的番号照问。
            self.assertEqual(rows["javbus"]["cooldown_skips"], "0")
            self.assertEqual(rows["javbus"]["blocked"], "0")
            self.assertEqual(rows["javbus"]["last_error_status"], "429")

            connection = sqlite3.connect(db)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM asset_entity").fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM review_decision").fetchone()[0], 0)
            connection.close()

    def test_scrape_falls_back_to_the_other_writing_of_the_same_code(self):
        """`LUXU-1642` 与 `259LUXU-1642` 是同一部作品，来源各只索引其中一种。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            sqlite3.connect(db).close(); upgrade(db, MIGRATIONS)
            connection = sqlite3.connect(db)
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,code) "
                "VALUES(1,'local','one.mp4','one.mp4','video','LUXU-1642')")
            connection.commit(); connection.close()

            self_error = self.scrape_codes.MetadataProviderError

            class PrefixOnlyProvider:
                def __init__(self): self.calls = []
                def query(self, code, source):
                    self.calls.append((code, source))
                    if code == "LUXU-1642":
                        raise self_error("status 404", kind="not_found",
                                         status_code=404, retryable=False, temporary=False)
                    return {"source": source, "id": "259LUXU-1642", "maker": "Maan Sha"}

            provider = PrefixOnlyProvider()
            raw = root / "raw"
            output = root / "candidates.csv"
            errors = root / "errors.csv"
            with redirect_stdout(io.StringIO()):
                result = self.scrape_codes.main([
                    "--db", str(db), "--out", str(output), "--errors", str(errors),
                    "--raw-dir", str(raw), "--log-dir", str(root / "logs"),
                    "--delay", "0", "--min-free", "0", "--sources", "javbus",
                ], provider=provider)
            self.assertEqual(result, 0)
            self.assertEqual(provider.calls,
                             [("LUXU-1642", "javbus"), ("259LUXU-1642", "javbus")])
            # 两种写法各自留证据，回退命中的那次不覆盖原写法的失败快照。
            self.assertTrue((raw / "LUXU-1642" / "javbus.json").is_file())
            self.assertTrue((raw / "259LUXU-1642" / "javbus.json").is_file())
            with output.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            studio = next(row for row in rows if row["field"] == "studio")
            # 评审键仍按账本的规范写法，不随回退漂移。
            self.assertEqual((studio["query"], studio["item_key"]),
                             ("LUXU-1642", "LUXU-1642:studio"))
            with errors.open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(list(csv.DictReader(handle)), [], "回退命中不算失败")

    def test_scrape_rejects_a_source_result_for_a_different_release(self):
        """javbus 搜不到就返回首个近似命中：`259LUXU-164` 会取回 `259LUXU-1642`。

        韩国 MIB 那批番号由 `metadata_routes.classify` 拦在刮削入口，走不到这里；这道守卫管的是
        剩下那些形状相近的误配，它们没有前缀表可依，只能靠比对来源自报的番号认出来。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            sqlite3.connect(db).close(); upgrade(db, MIGRATIONS)
            connection = sqlite3.connect(db)
            connection.executemany(
                "INSERT INTO asset(id,location,path,name,medium,code) "
                "VALUES(?,'local',?,?,'video',?)",
                [(1, "1.mp4", "1.mp4", "259LUXU-164"), (2, "2.mp4", "2.mp4", "IQQQ-026"),
                 (3, "3.mp4", "3.mp4", "390JAC-040")])
            connection.commit(); connection.close()

            class LooseSearchProvider:
                def query(self, code, source):
                    if code in {"259LUXU-164", "LUXU-164"}:
                        return {"source": source, "id": "259LUXU-1642", "maker": "别人的厂牌"}
                    if code in {"390JAC-040", "JAC-040"}:
                        return {"source": source, "id": "JAC-040", "content_id": "118jac040",
                                "source_url": "https://r18.dev/videos/vod/movies/detail/-/combined=118jac040/json",
                                "maker": "Prestige"}
                    return {"source": source, "id": "IQQQ-26", "maker": "Attackers"}

            raw = root / "raw"
            output = root / "candidates.csv"
            errors = root / "errors.csv"
            with redirect_stdout(io.StringIO()):
                result = self.scrape_codes.main([
                    "--db", str(db), "--out", str(output), "--errors", str(errors),
                    "--raw-dir", str(raw), "--log-dir", str(root / "logs"),
                    "--delay", "0", "--min-free", "0", "--sources", "javbus",
                ], provider=LooseSearchProvider())
            self.assertEqual(result, 0)
            with errors.open(encoding="utf-8-sig", newline="") as handle:
                error_rows = list(csv.DictReader(handle))
            self.assertCountEqual([(row["code"], row["kind"]) for row in error_rows],
                             [("390JAC-040", "identity_mismatch"),
                              ("259LUXU-164", "identity_mismatch")])
            self.assertTrue(any("259LUXU-1642" in row["message"] for row in error_rows))
            with output.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            # 补零差异是良性的，`IQQQ-26` 必须照常收下。
            self.assertEqual({row["code"] for row in rows}, {"IQQQ-026"})
            # 原始响应仍然落盘：拒收的是候选，不是证据。
            self.assertTrue((raw / "259LUXU-164" / "javbus.json").is_file())

    def test_scrape_uses_official_product_identity_for_display_alias(self):
        check = self.scrape_codes._identity_mismatch
        mgs = "https://www.mgstage.com/product/product_detail/390JAC-040/"
        self.assertIsNone(check("390JAC-040", {"id": "JAC-040", "source_url": mgs}))
        dvd = {"content_id": "118jac040",
               "source_url": "https://www.dmm.co.jp/mono/dvd/-/detail/=/cid=118jac040/"}
        self.assertIsNone(check("JAC-040", dvd))
        self.assertEqual(check("390JAC-040", dvd).kind, "identity_mismatch")
        for payload in (
            {"id": "JAC-040", "source_url": "https://www.dmm.co.jp/mono/dvd/-/detail/=/cid=118jac040/"},
            {"id": "390JAC-040", "source_url": mgs.replace("040", "041")},
            {"id": "JAC-040", "source_url": mgs.replace("www.mgstage.com", "mgstage.com.example.org")},
        ):
            self.assertEqual(check("390JAC-040", payload).kind, "identity_mismatch")

    def _chain_ledger(self, root, codes):
        db = root / "ledger.db"
        sqlite3.connect(db).close(); upgrade(db, MIGRATIONS)
        connection = sqlite3.connect(db)
        connection.executemany(
            "INSERT INTO asset(id,location,path,name,medium,code,size) "
            "VALUES(?,'local',?,?,'video',?,?)",
            # 体积递减：批次按体积倒序排队，给定的顺序就是处理顺序。
            [(i, f"{i}.mp4", f"{i}.mp4", code, 10_000 - i) for i, code in enumerate(codes, 1)],
        )
        connection.commit(); connection.close()
        return db

    @staticmethod
    def _full_payload(code, source):
        return {"source": source, "id": code, "title": "Catalog title",
                "actresses": [{"japanese_name": "木村さん"}], "maker": "Studio A",
                "release_date": "2020-09-13T00:00:00Z"}

    def test_chain_profile_collects_javdb_tags_after_the_scalars_settle(self):
        """默认走正式链：标量齐全后只补 JavDB 类别，标量不足时问完整综合索引档。

        判据与采集任务同一份（`metadata_routes.settles`），停手的单位是「档」不是
        「家」：综合索引那一档的三家一起问，谁都不因为前一家答上而被跳过。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = self._chain_ledger(root, ["ABC-001", "DEF-002"])
            self_error = self.scrape_codes.MetadataProviderError

            class Staged:
                def __init__(self): self.calls = []
                def query(self, code, source):
                    self.calls.append((code, source))
                    # DEF-002 在 r18.dev 与它兜底的 DMM 都没有，链才走到综合索引那一档。
                    if source == "makers" or (code == "DEF-002" and source in ("r18dev", "dmm")):
                        raise self_error("status 404", kind="not_found", status_code=404)
                    if code == "DEF-002":
                        return {"source": source, "id": code, "maker": "Studio B"}
                    return OperationalScriptTests._full_payload(code, source)

            provider = Staged()
            output = root / "metadata-field-candidates-chain.csv"
            with redirect_stdout(io.StringIO()):
                result = self.scrape_codes.main([
                    "--db", str(db), "--out", str(output), "--raw-dir", str(root / "raw"),
                    "--log-dir", str(root / "logs"), "--delay", "0", "--min-free", "0",
                ], provider=provider)
            self.assertEqual(result, 0)
            self.assertEqual(provider.calls, [
                ("ABC-001", "makers"), ("ABC-001", "r18dev"), ("ABC-001", "javdb"),
                ("DEF-002", "makers"), ("DEF-002", "r18dev"), ("DEF-002", "dmm"), ("DEF-002", "avbase"),
                ("DEF-002", "javbus"), ("DEF-002", "javdb"), ("DEF-002", "javlibrary"),
            ])
            with output.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(rows)
            self.assertTrue(all(row["source_profile"] == "chain" for row in rows))
            studio = next(row for row in rows if row["code"] == "DEF-002" and row["field"] == "studio")
            candidates = json.loads(studio["candidates_json"])
            # 同一档各家的取值并排进候选，排序按字段来源表：javdb 在前，表里没有的
            # avbase 排到末尾之后。
            self.assertEqual([c["source"] for c in candidates], ["javdb", "javbus", "javlibrary", "avbase"])
            # 候选记的解析器名与采集任务的证据文件同一份（`PROVIDER_NAMES`）。
            from peach.library_processing import PROVIDER_NAMES
            self.assertEqual(candidates[0]["provider"], PROVIDER_NAMES["javdb"])
            health = output.with_name("metadata-source-health-chain.csv")
            with health.open(encoding="utf-8-sig", newline="") as handle:
                health_rows = {row["source"]: row for row in csv.DictReader(handle)}
            # 走链时健康表覆盖所有链的并集，没轮到的档计数为 0 而不是缺行。
            self.assertEqual(set(health_rows), set(self.scrape_codes.CHAIN_SOURCES))
            self.assertEqual(health_rows["r18dev"]["attempted"], "2")
            self.assertEqual(health_rows["javdb"]["attempted"], "2")
            self.assertEqual(health_rows["fc2"]["attempted"], "0")

    def test_censored_tags_can_be_recollected_and_rejudged_when_tags_are_already_present(self):
        """按番号文件重取候选不依赖标签为空；临时库的旧自动标签按 JavDB 策略重判。"""
        from peach.entities import upsert_asset_entity
        from peach.metadata_auto_apply import auto_apply_metadata
        from peach.repository import LedgerDatabase

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            db = self._chain_ledger(root, ["ABW-220"])
            with closing(sqlite3.connect(db)) as con, con:
                con.execute("UPDATE asset SET name='ABW-220.mp4',path='ABW-220.mp4',"
                            "catalog_title='Catalog title',studio='Studio A',release_date='2020-09-13'")
                con.execute("INSERT INTO asset_tag(asset_id,tag,confidence,source) "
                            "VALUES(1,'肛交',0.9,'javinizer:r18dev:tag')")
                upsert_asset_entity(con, kind="tag", name="肛交", asset_id=1, role="tag",
                                    source="javinizer:r18dev:tag")
                upsert_asset_entity(con, kind="performer", name="木村さん", asset_id=1,
                                    role="performer", source="javinizer:r18dev:performer")
                con.execute("INSERT INTO review_decision(category,item_key,status,note,updated_at) "
                            "VALUES('metadata_fields','ABW-220:tags','approved',?,'2026-01-01')",
                            (json.dumps({"auto_applied": True, "source": "r18dev",
                                         "candidate_key": "old-r18dev", "value": "肛交"}),))

            class Recollection:
                def __init__(self):
                    self.calls = []

                def query(self, code, source):
                    self.calls.append((code, source))
                    genres = ["高跟鞋", "絲襪"] if source == "javdb" else ["アナル"]
                    return {**OperationalScriptTests._full_payload(code, source), "genres": genres}

            provider = Recollection()
            code_file = root / "codes.txt"
            code_file.write_text("ABW-220\n", encoding="utf-8")
            candidate_root = root / "candidates"
            output = candidate_root / "metadata-field-candidates-javdb.csv"
            with redirect_stdout(io.StringIO()):
                exit_code = self.scrape_codes.main([
                    "--db", str(db), "--out", str(output), "--codes-file", str(code_file),
                    "--raw-dir", str(root / "raw"), "--log-dir", str(root / "logs"),
                    "--delay", "0", "--min-free", "0",
                ], provider=provider)
            self.assertEqual(exit_code, 0)
            self.assertIn(("ABW-220", "javdb"), provider.calls)
            with output.open(encoding="utf-8-sig", newline="") as handle:
                tags = next(row for row in csv.DictReader(handle) if row["field"] == "tags")
            self.assertEqual(tags["current_value"], "肛交")
            self.assertEqual(json.loads(tags["candidates_json"])[0]["source"], "javdb")
            self.assertGreaterEqual(auto_apply_metadata(LedgerDatabase(db), candidate_root)["applied"], 1)
            with closing(sqlite3.connect(db)) as con:
                self.assertEqual(set(con.execute("SELECT tag,source FROM asset_tag WHERE asset_id=1"
                                                 " AND tag NOT LIKE '演员:%'")),
                                 {("高跟", "javinizer:javdb:tag"), ("丝袜", "javinizer:javdb:tag")})

    def test_explicit_sources_ask_every_named_source_and_refuse_retired_names(self):
        """`--sources` 点名的每一家都问，官方答全了也不停；历史来源名当场拒绝。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = self._chain_ledger(root, ["ABC-001"])

            class Everything:
                def __init__(self): self.calls = []
                def query(self, code, source):
                    self.calls.append((code, source))
                    return OperationalScriptTests._full_payload(code, source)

            provider = Everything()
            with redirect_stdout(io.StringIO()):
                result = self.scrape_codes.main([
                    "--db", str(db), "--out", str(root / "c.csv"), "--raw-dir", str(root / "raw"),
                    "--log-dir", str(root / "logs"), "--delay", "0", "--min-free", "0",
                    "--sources", "r18dev,javbus,fc2club",
                ], provider=provider)
            self.assertEqual(result, 0)
            self.assertEqual(provider.calls, [
                ("ABC-001", "r18dev"), ("ABC-001", "javbus"), ("ABC-001", "fc2club")])
            for names, message in (("libredmm", "历史来源身份"), ("imaginary", "未知来源"),
                                   ("r18dev", "不能同时")):
                argv = ["--db", str(db), "--sources", names]
                if names == "r18dev":
                    argv += ["--profile", "seesaa"]
                with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit), \
                        mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    self.scrape_codes.main(argv)
                if message != "不能同时":
                    self.assertIn(message, stderr.getvalue())

    def test_chain_failures_are_translated_into_the_script_error_kinds(self):
        """正式链的四种异常各落一档：没有是定论，限流与预算是临时，401/403 是鉴权。"""
        from peach.jav_cover_fetch import DeadlineExceeded, NotFound, Unavailable
        from peach.scraping_access import SourcePaused
        translate = self.scrape_codes.translate_failure
        self.assertEqual((translate("javdb", NotFound("没有")).kind,
                          translate("javdb", NotFound("没有")).retryable), ("not_found", False))
        paused = translate("javdb", SourcePaused("来源正在冷却"))
        self.assertEqual((paused.kind, paused.status_code, paused.retryable), ("rate_limited", 429, True))
        walled = translate("javdb", SourcePaused("javdb 返回 403，已冷却"))
        self.assertEqual((walled.kind, walled.status_code, walled.retryable), ("auth", 403, False))
        self.assertEqual(translate("fc2", DeadlineExceeded("预算用尽")).kind, "timeout")
        flaky = translate("fc2", Unavailable("HTTP 503 upstream"))
        self.assertEqual((flaky.kind, flaky.status_code, flaky.temporary), ("unavailable", 503, True))
        # 已经是本脚本分档的错误原样返回，不再套一层。
        own = self.scrape_codes.MetadataProviderError("x", kind="identity_mismatch")
        self.assertIs(translate("javdb", own), own)

    def test_creator_tag_review_queue_requires_approval_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            sqlite3.connect(db).close()
            upgrade(db, MIGRATIONS)
            connection = sqlite3.connect(db)
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,creator) "
                "VALUES(1,'local','one.mp4','one.mp4','video','Alice')"
            )
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,creator) "
                "VALUES(2,'local','vocab.mp4','vocab.mp4','video','Vocabulary')"
            )
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium,creator) "
                "VALUES(3,'local','The.Show.S01E02.1080p.WEB-DL.mp4',"
                "'The.Show.S01E02.1080p.WEB-DL.mp4','video','Alice')"
            )
            connection.execute(
                "INSERT INTO asset_tag(asset_id,tag,confidence,source) "
                "VALUES(2,'素人',0.9,'vision')"
            )
            connection.commit()
            connection.close()
            boards = root / "boards"
            boards.mkdir()
            (boards / "01_Alice_1.jpg").write_bytes(b"review-only fixture")
            review = root / "review.csv"

            total, pending = self.creator_tags.export_review(db, boards, review)
            self.assertEqual((total, pending), (1, 1))
            with review.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            rows[0].update({"status": "approved", "tags": "素人", "reason": "reviewed"})
            with review.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.creator_tags.REVIEW_FIELDS)
                writer.writeheader()
                writer.writerows(rows)

            backup = root / "backup.db"
            assets, tag_rows = self.creator_tags.apply_review(db, review, backup)
            self.assertEqual((assets, tag_rows), (1, 1))
            self.assertTrue(backup.is_file())
            connection = sqlite3.connect(db)
            self.assertEqual(connection.execute(
                "SELECT source,confidence FROM asset_tag WHERE asset_id=1 AND tag='素人'"
            ).fetchone(), ("vision_creator", 0.6))
            self.assertEqual(connection.execute(
                "SELECT ae.source FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
                "WHERE ae.asset_id=1 AND e.kind='tag' AND e.canonical_name='素人'"
            ).fetchone()[0], "vision_creator")
            connection.close()


class FlattenReleaseDirTests(unittest.TestCase):
    """冗余发行物目录层与目录名广告标记。"""

    @classmethod
    def setUpClass(cls):
        cls.flatten = load_script("flatten_release_dirs")

    def _ledger(self, root: Path, paths: list[str]) -> sqlite3.Connection:
        db = root / "ledger.db"
        sqlite3.connect(db).close()
        upgrade(db, MIGRATIONS)
        connection = sqlite3.connect(db)
        for index, path in enumerate(paths, 1):
            connection.execute(
                "INSERT INTO asset(id,location,path,name,medium) VALUES(?,'115',?,?,'video')",
                (index, path, PureWindowsPath(path).name))
        connection.commit()
        return connection

    def _resolver(self, root: Path):
        return lambda ledger: root.joinpath(*PureWindowsPath(str(ledger)).parts[1:])

    def test_path_index_preserves_operation_order_and_component_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            connection = self._ledger(Path(tmp).resolve(), [
                r"B:\old\inner\a.mp4", r"B:\old-other\b.mp4", r"B:\OLD\c.mp4"])
            operations = [
                {"ledger_dir": r"B:\old\inner", "target_dir": r"B:\old\renamed"},
                {"ledger_dir": r"B:\old", "target_dir": r"B:\new"},
                {"ledger_dir": r"B:\new\renamed", "target_dir": r"B:\final"}]
            self.assertEqual(self.flatten.plan_paths(connection, operations), [
                {"id": "1", "old_path": r"B:\old\inner\a.mp4", "new_path": r"B:\final\a.mp4"},
                {"id": "3", "old_path": r"B:\OLD\c.mp4", "new_path": r"B:\new\c.mp4"}])
            connection.close()

    def test_directory_plan_excludes_files_and_includes_redundant_siblings(self):
        with tempfile.TemporaryDirectory() as tmp:
            connection = self._ledger(Path(tmp).resolve(), [
                r"B:\ABC-123\ABC-123.mp4",
                r"B:\ATID-353\ATID-353.mp4\ATID-353.mp4",
                r"B:\BAZX-123\[Thz.la]bazx-123\BAZX-123cd1.mp4",
                r"B:\BAZX-123\bazx-123\BAZX-123.mp4",
                r"B:\合集\XYZ-456\XYZ-456.mp4"])
            operations = self.flatten.plan_operations(connection)
            self.assertEqual({op["ledger_dir"] for op in operations}, {
                r"B:\ATID-353\ATID-353.mp4",
                r"B:\BAZX-123\[Thz.la]bazx-123",
                r"B:\BAZX-123\bazx-123"})
            self.assertTrue(all(op["kind"] == "collapse" and op["assets"] == "1"
                                for op in operations))
            self.assertEqual(len(self.flatten.plan_paths(connection, operations)), 3)
            connection.close()

    def test_shared_parent_keeps_sidecars_and_recovers_directory_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / "same" / "same"
            source.mkdir(parents=True)
            (source.parent / "poster.jpg").write_bytes(b"cover")
            (source / "a.mp4").write_bytes(b"media")
            (source / "b.srt").write_bytes(b"subtitle")
            operation = {"kind": "collapse", "ledger_dir": r"B:\same\same", "target_dir": r"B:\same"}
            done = self.flatten.apply_operation(operation, self._resolver(root))
            self.assertFalse(source.exists())
            self.assertEqual((source.parent / "poster.jpg").read_bytes(), b"cover")
            self.assertEqual((source.parent / "a.mp4").read_bytes(), b"media")
            self.flatten.rollback(done)
            rename, attempts = Path.rename, [0]
            def fail_second_move(path, target):
                attempts[0] += 1
                if attempts[0] == 2:
                    raise OSError("blocked file move")
                return rename(path, target)
            with mock.patch.object(Path, "rename", fail_second_move):
                with self.assertRaisesRegex(OSError, "blocked file move"):
                    self.flatten.apply_operation(operation, self._resolver(root))
            self.assertEqual((source / "a.mp4").read_bytes(), b"media")
            self.assertEqual((source / "b.srt").read_bytes(), b"subtitle")
            self.assertEqual((source.parent / "poster.jpg").read_bytes(), b"cover")

    def test_directory_plan_cleans_advertised_leaf_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            connection = self._ledger(Path(tmp).resolve(), [
                r"B:\[Thz.la]ABC-123\[Thz.la]ABC-123.mp4"])
            self.assertEqual(self.flatten.plan_operations(connection), [{
                "kind": "rename", "ledger_dir": r"B:\[Thz.la]ABC-123",
                "target_dir": r"B:\ABC-123", "assets": "1", "verified": ""}])
            connection.close()

    def test_shared_parent_collision_keeps_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / "same" / "same"
            source.mkdir(parents=True)
            (source.parent / "a.mp4").write_bytes(b"parent version")
            (source / "a.mp4").write_bytes(b"child version")
            operation = {"kind": "collapse", "ledger_dir": r"B:\same\same", "target_dir": r"B:\same"}
            with self.assertRaisesRegex(ValueError, "目标条目已存在"):
                self.flatten.apply_operation(operation, self._resolver(root))
            self.assertEqual((source.parent / "a.mp4").read_bytes(), b"parent version")
            self.assertEqual((source / "a.mp4").read_bytes(), b"child version")

    def test_media_wrapper_with_siblings_preserves_versions_and_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / "VGD-157" / "VGD-157.mp4"
            source.mkdir(parents=True)
            (source.parent / "VGD-157.mkv").write_bytes(b"other version")
            (source / "VGD-157.mp4").write_bytes(b"media")
            (source / "VGD-157.srt").write_bytes(b"subtitle")
            operation = {"kind": "collapse", "ledger_dir": r"B:\VGD-157\VGD-157.mp4",
                         "target_dir": r"B:\VGD-157"}
            resolver = self._resolver(root)
            done = self.flatten.apply_operation(operation, resolver)
            self.assertTrue(source.is_file())
            self.assertEqual(source.read_bytes(), b"media")
            self.assertEqual((source.parent / "VGD-157.mkv").read_bytes(), b"other version")
            self.flatten.rollback(done)
            rename = Path.rename
            for failing_step in (1, 2, 3):
                attempts = [0]
                def fail_move(path, target, attempts=attempts, failing_step=failing_step):
                    attempts[0] += 1
                    if attempts[0] == failing_step:
                        raise OSError("blocked wrapper move")
                    return rename(path, target)
                with mock.patch.object(Path, "rename", fail_move):
                    with self.assertRaisesRegex(OSError, "blocked wrapper move"):
                        self.flatten.apply_operation(operation, resolver)
                self.assertEqual((source / "VGD-157.mp4").read_bytes(), b"media")
                self.assertEqual((source / "VGD-157.srt").read_bytes(), b"subtitle")
                self.assertEqual((source.parent / "VGD-157.mkv").read_bytes(), b"other version")
                self.assertFalse(list(source.parent.glob(".peach-organize-*")))

    def test_partial_directory_failure_restores_already_moved_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / "same" / "same"
            source.mkdir(parents=True)
            (source / "a.mp4").write_bytes(b"media")
            (source / "b.srt").write_bytes(b"subtitle")
            rename = Path.rename
            attempts = [0]
            def fail_second_move(path, target):
                attempts[0] += 1
                if attempts[0] == 2:
                    raise OSError("blocked")
                return rename(path, target)
            operation = {"kind": "collapse", "ledger_dir": r"B:\same\same", "target_dir": r"B:\same"}
            with mock.patch.object(Path, "rename", fail_second_move):
                with self.assertRaisesRegex(OSError, "blocked"):
                    self.flatten.apply_operation(operation, self._resolver(root))
            self.assertEqual((source / "a.mp4").read_bytes(), b"media")
            self.assertEqual((source / "b.srt").read_bytes(), b"subtitle")
            self.assertFalse((source.parent / "a.mp4").exists())

    def test_apply_synchronises_registered_subtitles_and_carries_covers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            inner = root / "TRE-080" / "TRE-080"
            inner.mkdir(parents=True)
            for name in ("TRE-080.mp4", "TRE-080.srt", "poster.jpg"):
                (inner / name).write_bytes(name.encode())
            connection = self._ledger(root, [r"B:\TRE-080\TRE-080\TRE-080.mp4"])
            connection.execute("INSERT INTO asset_subtitle(asset_id,location,path,name,language,format,size,mtime,pairing,first_seen,last_seen) VALUES(1,'115',?,'TRE-080.srt','zh','srt',1,0,'exact','now','now')",
                               (r"B:\TRE-080\TRE-080\TRE-080.srt",))
            connection.commit(); connection.close()
            args = self.flatten.build_parser().parse_args([
                "--db", str(root / "ledger.db"), "--plan-csv", str(root / "plan.csv"),
                "--path-csv", str(root / "paths.csv"), "--apply", "--backup", str(root / "backup.db")])
            verify, apply = self.flatten.verify, self.flatten.apply_operation
            resolve = self._resolver(root)
            rename = Path.rename
            def directory_only(path, target):
                if not path.is_dir():
                    raise OSError("file moves unsupported")
                return rename(path, target)
            with mock.patch.object(self.flatten, "verify", side_effect=lambda op, *args: verify(op, resolve)), \
                    mock.patch.object(self.flatten, "apply_operation", side_effect=lambda op: apply(op, resolve)), \
                    mock.patch.object(Path, "rename", directory_only):
                self.assertEqual(self.flatten.run(args), 0)
            connection = sqlite3.connect(root / "ledger.db")
            self.assertEqual(connection.execute("SELECT path FROM asset_subtitle").fetchone()[0], r"B:\TRE-080\TRE-080.srt")
            self.assertEqual(connection.execute("SELECT path FROM asset").fetchone()[0], r"B:\TRE-080\TRE-080.mp4")
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            connection.close()
            self.assertTrue((inner.parent / "poster.jpg").is_file())
            self.assertTrue((root / "backup.db").is_file())

    def test_directory_backend_can_remove_empty_staging_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / "same" / "same"
            source.mkdir(parents=True)
            (source / "a.mp4").write_bytes(b"media")
            operation = {"kind": "collapse", "ledger_dir": r"B:\same\same", "target_dir": r"B:\same"}
            rename = Path.rename
            def remove_empty_parent(path, target):
                result = rename(path, target)
                if path.parent.name.startswith(".peach-organize-"):
                    path.parent.rmdir()
                return result
            with mock.patch.object(Path, "rename", remove_empty_parent):
                done = self.flatten.apply_operation(operation, self._resolver(root))
            self.assertEqual((source.parent / "a.mp4").read_bytes(), b"media")
            self.flatten.rollback(done)
            self.assertEqual((source / "a.mp4").read_bytes(), b"media")

    def test_staging_cleanup_failure_restores_the_original_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / "same" / "same"
            source.mkdir(parents=True)
            (source / "a.mp4").write_bytes(b"media")
            operation = {"kind": "collapse", "ledger_dir": r"B:\same\same", "target_dir": r"B:\same"}
            remove = Path.rmdir
            def fail_staging(path):
                if path.name.startswith(".peach-organize-"):
                    raise OSError("blocked cleanup")
                return remove(path)
            with mock.patch.object(Path, "rmdir", fail_staging):
                with self.assertRaisesRegex(OSError, "blocked cleanup"):
                    self.flatten.apply_operation(operation, self._resolver(root))
            self.assertEqual((source / "a.mp4").read_bytes(), b"media")
            self.assertEqual(list(root.iterdir()), [source.parent])

    def test_subtitle_database_failure_restores_media_and_all_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / "same" / "same"
            source.mkdir(parents=True)
            (source / "a.mp4").write_bytes(b"media")
            (source / "a.srt").write_bytes(b"subtitle")
            connection = self._ledger(root, [r"B:\same\same\a.mp4"])
            connection.execute("INSERT INTO asset_subtitle(asset_id,location,path,name,format,pairing,first_seen,last_seen) VALUES(1,'115',?,'a.srt','srt','exact','now','now')",
                               (r"B:\same\same\a.srt",))
            connection.execute("CREATE TRIGGER blocked_subtitle BEFORE UPDATE ON asset_subtitle BEGIN SELECT RAISE(ABORT,'blocked subtitle'); END")
            connection.commit()
            operation = {"kind": "collapse", "ledger_dir": r"B:\same\same", "target_dir": r"B:\same"}
            rows = self.flatten.plan_paths(connection, [operation])
            subs = self.flatten.plan_paths(connection, [operation], table="asset_subtitle")
            apply = self.flatten.apply_operation
            with mock.patch.object(self.flatten, "apply_operation", side_effect=lambda op: apply(op, self._resolver(root))):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "blocked subtitle"):
                    self.flatten.apply_plan(connection, [operation], rows, subs)
            self.assertEqual(connection.execute("SELECT path FROM asset").fetchone()[0], r"B:\same\same\a.mp4")
            self.assertEqual(connection.execute("SELECT path FROM asset_subtitle").fetchone()[0], r"B:\same\same\a.srt")
            self.assertEqual((source / "a.mp4").read_bytes(), b"media")
            self.assertEqual((source / "a.srt").read_bytes(), b"subtitle")
            connection.close()

    def test_collapse_needs_a_redundant_name_not_just_a_lone_child(self):
        """`古川结爱合集` 底下只有一个 `FC2-PPV-…` 也是有意义的一层，不能合。

        真实数据里「父目录只有一个子目录」有 747 处，绝大多数不是冗余层；只按
        「独子」判会把合集目录整片摊平。
        """
        with tempfile.TemporaryDirectory() as tmp:
            connection = self._ledger(Path(tmp), [
                r"B:\日本\Prestige\TRE-080\[44x.me]tre-080\TRE-080.mp4",
                r"B:\日本\Prestige\TRE-080\[44x.me]tre-080\TRE-080-2.mp4",
                r"B:\合集\古川结爱合集\FC2-PPV-1234567\one.mp4",
            ])
            plan = self.flatten.plan_operations(connection)
            connection.close()
            collapses = [row["ledger_dir"] for row in plan if row["kind"] == "collapse"]
            self.assertEqual(collapses, [r"B:\日本\Prestige\TRE-080\[44x.me]tre-080"])
            renames = {row["ledger_dir"] for row in plan if row["kind"] == "rename"}
            self.assertNotIn(r"B:\日本\Prestige\TRE-080\[44x.me]tre-080", renames,
                             "已经被合掉的目录不该再排一次改名")

    def test_collapse_preserves_unregistered_parent_cover(self):
        """父目录中未登记的封面随媒体合并保留原位。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inner = root / "日本" / "TRE-080" / "[44x.me]tre-080"
            inner.mkdir(parents=True)
            (inner / "TRE-080.mp4").write_text("x", encoding="utf-8")
            operation = {"kind": "collapse",
                         "ledger_dir": r"B:\日本\TRE-080\[44x.me]tre-080",
                         "target_dir": r"B:\日本\TRE-080"}
            resolve = self._resolver(root)
            self.assertEqual(self.flatten.verify(operation, resolve), "ok")
            (inner.parent / "cover.jpg").write_text("x", encoding="utf-8")
            self.assertEqual(self.flatten.verify(operation, resolve), "ok")
            done = self.flatten.apply_operation(operation, resolve)
            self.assertEqual((inner.parent / "cover.jpg").read_text(encoding="utf-8"), "x")
            self.assertEqual((inner.parent / "TRE-080.mp4").read_text(encoding="utf-8"), "x")
            self.flatten.rollback(done)
            self.assertTrue((inner / "TRE-080.mp4").is_file())

    def test_apply_moves_files_up_then_rewrites_the_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inner = root / "日本" / "TRE-080" / "[44x.me]tre-080"
            inner.mkdir(parents=True)
            (inner / "TRE-080.mp4").write_text("x", encoding="utf-8")
            connection = self._ledger(root, [r"B:\日本\TRE-080\[44x.me]tre-080\TRE-080.mp4"])
            operation = {"kind": "collapse",
                         "ledger_dir": r"B:\日本\TRE-080\[44x.me]tre-080",
                         "target_dir": r"B:\日本\TRE-080"}
            rows = self.flatten.plan_paths(connection, [operation])
            self.assertEqual(rows, [{"id": "1",
                                     "old_path": r"B:\日本\TRE-080\[44x.me]tre-080\TRE-080.mp4",
                                     "new_path": r"B:\日本\TRE-080\TRE-080.mp4"}])
            done = self.flatten.apply_operation(operation, self._resolver(root))
            self.assertTrue((root / "日本" / "TRE-080" / "TRE-080.mp4").is_file())
            self.assertFalse(inner.exists())
            self.flatten.rollback(done)
            self.assertTrue((inner / "TRE-080.mp4").is_file())
            connection.close()

    def test_ad_stripping_only_touches_the_head_and_tail(self):
        """欧美片名里的 `[Vixen.com]` 是厂牌，不是广告；删中间那段等于丢真信息。"""
        from peach.catalog_rules import strip_promo_markers
        self.assertEqual(strip_promo_markers("[44x.me]tre-080"), "tre-080")
        self.assertEqual(strip_promo_markers("MattieDoll - pornhub.com"), "MattieDoll")
        self.assertEqual(strip_promo_markers("[98t.tv][98t.tv]ABW-251"), "ABW-251",
                         "叠了两层广告要剥到不动为止")
        for keep in (
            "Hazel Moore - [FootFetishDaily.com] - Hardcore (18.10.19) -",
            # 整串都符合「标签+.com」，按通用形态删前缀会连番号一起吃掉、只剩 mp4
            "ABP-762-fuckbe.com.mp4",
            "(12P+5V_1.28G) [12P-5V-1.28GB]",
            "@9ririsuamano",
            "TRE-080",
        ):
            self.assertEqual(strip_promo_markers(keep), keep)

    def test_rename_only_strips_the_ad_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            connection = self._ledger(Path(tmp), [
                r"B:\日本\[44x.me]桃子作品集\one.mp4",
            ])
            plan = self.flatten.plan_operations(connection)
            connection.close()
            renames = [row for row in plan if row["kind"] == "rename"]
            self.assertEqual([(row["ledger_dir"], row["target_dir"]) for row in renames],
                             [(r"B:\日本\[44x.me]桃子作品集", r"B:\日本\桃子作品集")])


class ApplyMetadataTagsTests(unittest.TestCase):
    """直接写标签这条路必须走 `/review` 批准时的同一份写入映射。"""

    @classmethod
    def setUpClass(cls):
        cls.apply_tags = load_script("apply_metadata_tags")

    def test_only_the_requested_source_and_field_are_written(self):
        rows = [
            {"item_key": "AAA-001:tags", "field": "tags", "status": "candidate",
             "candidates_json": json.dumps([{"source": "javbus", "value": ["素人"]},
                                            {"source": "javdb", "value": ["人妻"]}])},
            {"item_key": "AAA-001:title", "field": "title", "status": "candidate",
             "candidates_json": json.dumps([{"source": "javbus", "value": "タイトル"}])},
            {"item_key": "BBB-002:tags", "field": "tags", "status": "applied",
             "candidates_json": json.dumps([{"source": "javbus", "value": ["白虎"]}])},
        ]
        selected = self.apply_tags.plan(rows, "javbus", "tags")
        self.assertEqual([(group["item_key"], candidate["value"]) for group, candidate in selected],
                         [("AAA-001:tags", ["素人"])])

    def test_skipped_codes_stay_in_the_csv_but_do_not_reach_the_ledger(self):
        """批量放行里总有几条明显不对，跳过它们，但不许从复核产物里抹掉。

        实例：javbus 在 `MY-*` 系列的标题栏放的是「演员名+序号」而不是标题。
        过滤 CSV 会让这几条从此没人看见；跳过则它们仍在 `/review` 里等人处理。
        """
        rows = [
            {"item_key": "MY-101:title", "code": "MY-101", "field": "title",
             "status": "candidate",
             "candidates_json": json.dumps([{"source": "javbus", "value": "最上彩奈1"}])},
            {"item_key": "TRE-080:title", "code": "TRE-080", "field": "title",
             "status": "candidate",
             "candidates_json": json.dumps([{"source": "javbus", "value": "なまなかだし"}])},
        ]
        selected = self.apply_tags.plan(rows, "javbus", "title", frozenset({"my-101"}))
        self.assertEqual([group["item_key"] for group, _ in selected], ["TRE-080:title"])

    def test_apply_writes_tags_and_entities_for_the_whole_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "ledger.db"
            sqlite3.connect(db).close()
            upgrade(db, MIGRATIONS)
            connection = sqlite3.connect(db)
            connection.executemany(
                "INSERT INTO asset(id,location,path,name,medium,code) VALUES(?,'115',?,?,'video','TRE-080')",
                [(1, r"B:\TRE-080.mp4", "TRE-080.mp4"),
                 (2, r"B:\TRE-080-2.mp4", "TRE-080-2.mp4")])
            connection.commit()
            connection.close()

            candidates = root / "metadata-field-candidates-test.csv"
            candidate = {
                "candidate_key": "TRE-080:tags:javbus:abc", "source": "javbus",
                "confidence": 0.75, "provider": "javinizer-go",
                "source_url": "https://www.javbus.com/ja/TRE-080",
                "provider_id": "TRE-080", "content_id": "TRE-080",
                "raw_snapshot": "javbus.json", "value": ["中出内射", "素人"],
            }
            with candidates.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "item_key", "code", "query", "field", "status", "candidates_json"])
                writer.writeheader()
                writer.writerow({
                    "item_key": "TRE-080:tags", "code": "TRE-080", "query": "TRE-080",
                    "field": "tags", "status": "candidate",
                    "candidates_json": json.dumps([candidate], ensure_ascii=False)})

            args = self.apply_tags.build_parser().parse_args(
                [str(candidates), "--db", str(db), "--apply", "--backup", str(root / "backup.db")])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(self.apply_tags.run(args), 0)
            self.assertTrue((root / "backup.db").is_file())

            connection = sqlite3.connect(db)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM asset_tag WHERE source='javinizer:javbus:tag'"
            ).fetchone()[0], 4, "两条资产各写两个标签")
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM asset_entity WHERE role='tag' "
                "AND source='javinizer:javbus:tag'").fetchone()[0], 4,
                "标签实体那一半不能漏")
            row = connection.execute(
                "SELECT status,note FROM review_decision "
                "WHERE category='metadata_fields' AND item_key='TRE-080:tags'").fetchone()
            connection.close()
            self.assertIsNotNone(row, "写完不登记，这一组会永远挂在 /review 里")
            self.assertEqual(row[0], "approved")
            self.assertEqual(json.loads(row[1])["candidate_key"],
                             "TRE-080:tags:javbus:abc",
                             "留痕必须带候选身份，metadata_decision_is_stale 靠它判过期")

    def test_dry_run_never_touches_the_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.csv"
            with candidates.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["item_key", "field", "status",
                                                            "candidates_json"])
                writer.writeheader()
                writer.writerow({"item_key": "A:tags", "field": "tags", "status": "candidate",
                                 "candidates_json": json.dumps([{"source": "javbus",
                                                                 "value": ["素人"]}])})
            args = self.apply_tags.build_parser().parse_args(
                [str(candidates), "--db", str(root / "missing.db")])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(self.apply_tags.run(args), 0)
            self.assertFalse((root / "missing.db").exists(), "空跑不该建库")


class ScriptingConventionTests(unittest.TestCase):
    """`peach.scripting` 收口的那几条约定，按行为而不是按源码字符串验收。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
        self.db = self.root / "ledger.db"
        connection = sqlite3.connect(self.db)
        connection.executescript(
            "CREATE TABLE asset(id INTEGER PRIMARY KEY);"
            "CREATE TABLE entity(id INTEGER PRIMARY KEY, kind TEXT);"
            "CREATE TABLE asset_entity(asset_id INTEGER, entity_id INTEGER);"
            "CREATE TABLE entity_alias(entity_id INTEGER, alias TEXT);"
            "CREATE TABLE asset_tag(asset_id INTEGER, tag TEXT);"
        )
        connection.execute("INSERT INTO asset(id) VALUES(1)")
        connection.executemany("INSERT INTO entity(id,kind) VALUES(?,?)",
                               [(1, "creator"), (2, "performer"), (3, "performer")])
        connection.commit()
        connection.close()

    def _args(self, **overrides):
        values = {"db": self.db, "apply": False, "backup": None}
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_readonly_connection_cannot_write_the_ledger(self):
        connection = scripting.open_readonly(self.db)
        self.addCleanup(connection.close)
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute("INSERT INTO asset(id) VALUES(2)")

    def test_readonly_uri_survives_a_hash_in_the_directory_name(self):
        awkward = self.root / "a#b"
        awkward.mkdir()
        target = awkward / "ledger.db"
        sqlite3.connect(target).close()
        connection = scripting.open_readonly(target)
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)

    def test_dry_run_gets_a_read_only_connection_and_writes_no_backup(self):
        backup = self.root / "unused.db"
        connection = scripting.open_for_write(self._args(backup=backup))
        self.addCleanup(connection.close)
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute("INSERT INTO asset(id) VALUES(2)")
        self.assertFalse(backup.exists(), "dry-run 不该产生备份")

    def test_apply_without_backup_is_refused_with_the_single_house_wording(self):
        with self.assertRaises(SystemExit) as caught:
            scripting.open_for_write(self._args(apply=True))
        self.assertEqual(str(caught.exception), "--apply 必须同时给 --backup")

    def test_apply_backup_keeps_transactions_that_are_still_only_in_the_wal(self):
        """WAL 里已提交未 checkpoint 的事务必须进备份；文件复制会把它们丢掉。"""
        writer = sqlite3.connect(self.db)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO asset(id) VALUES(99)")
        writer.commit()
        self.addCleanup(writer.close)

        backup = self.root / "backup.db"
        connection = scripting.open_for_write(self._args(apply=True, backup=backup))
        self.addCleanup(connection.close)
        connection.execute("INSERT INTO asset(id) VALUES(100)")
        connection.commit()

        saved = sqlite3.connect(backup)
        self.addCleanup(saved.close)
        ids = {row[0] for row in saved.execute("SELECT id FROM asset")}
        self.assertIn(99, ids, "WAL 中的已提交事务丢了")
        self.assertNotIn(100, ids, "备份必须是写入之前的状态")

    def test_counts_of_reports_the_shared_base_plus_the_callers_own_measures(self):
        connection = scripting.open_readonly(self.db)
        self.addCleanup(connection.close)
        counts = scripting.counts_of(connection, {
            "performer": "SELECT count(*) FROM entity WHERE kind='performer'",
            "asset_tag": "SELECT count(*) FROM asset_tag",
        })
        self.assertEqual(counts, {"asset": 1, "entity": 3, "asset_entity": 0,
                                  "entity_alias": 0, "performer": 2, "asset_tag": 0})

    def test_ledger_write_args_are_exactly_db_apply_backup(self):
        parser = scripting.add_ledger_write_args(argparse.ArgumentParser())
        parsed = parser.parse_args(["--db", str(self.db), "--apply",
                                    "--backup", str(self.root / "b.db")])
        self.assertEqual((parsed.db, parsed.apply, parsed.backup),
                         (self.db, True, self.root / "b.db"))
        self.assertFalse(parser.parse_args([]).apply)
        with self.assertRaises(SystemExit):
            parser.parse_args(["--database", str(self.db)])

    #: 会真写 ledger、已经收口到本模块的脚本。
    LEDGER_WRITERS = (
        "backfill_rule34_details",
        "clean_names",
        "install_entity_links",
        "localize_performer_names",
        "localize_series_names",
        "merge_duplicate_identities",
        "rename_retired_tags",
    )

    def test_every_ledger_writer_takes_the_same_three_write_arguments(self):
        """写入脚本的参数名只有一套。

        不收 `--database`（必填）、`--backup-dir`（目录）这类同义写法：名字不同的同义
        参数会让「上次那条命令」在另一个脚本上直接报错，而报错只说缺参数——不说该写什么。
        """
        for name in self.LEDGER_WRITERS:
            with self.subTest(script=name):
                parser = load_script(name).build_parser()
                dests = {action.dest for action in parser._actions}
                self.assertLessEqual({"db", "apply", "backup"}, dests)
                self.assertNotIn("database", dests)
                self.assertNotIn("backup_dir", dests)

    def test_every_ledger_writer_opens_the_ledger_through_this_module(self):
        """连接、备份、拒绝三件事只有一处实现。

        判据落在「用的是不是同一个函数」上，而不是源码里出现过哪个字符串：脚本各自
        `sqlite3.connect` 时，dry-run 拿到的是可写连接，「这一趟绝不写库」只是靠读代码
        维持的约定。
        """
        for name in self.LEDGER_WRITERS:
            with self.subTest(script=name):
                module = load_script(name)
                self.assertIs(module.open_for_write, scripting.open_for_write)

    def test_rate_limiter_waits_the_remainder_rather_than_the_full_interval(self):
        now = [100.0]
        slept: list[float] = []

        def sleeper(seconds):
            slept.append(seconds)
            now[0] += seconds

        limiter = scripting.RateLimiter(2.0, clock=lambda: now[0], sleeper=sleeper)
        limiter.wait()
        self.assertEqual(slept, [], "第一次不该等")
        now[0] += 1.5                        # 本地处理已经花了 1.5 秒
        limiter.wait()
        self.assertEqual(slept, [0.5], "只该补齐差额，不是又睡满一个间隔")
        now[0] += 5.0
        limiter.wait()
        self.assertEqual(slept, [0.5], "间隔已过就不再等")

    def test_zero_interval_rate_limiter_never_sleeps(self):
        slept: list[float] = []
        limiter = scripting.RateLimiter(0, sleeper=slept.append)
        limiter.wait()
        limiter.wait()
        self.assertEqual(slept, [])

    def test_host_limiter_matches_on_dot_boundaries_not_substrings(self):
        now = [0.0]
        slept: list[float] = []

        def sleeper(seconds):
            slept.append(seconds)
            now[0] += seconds

        limiter = scripting.HostLimiter({"x.com": 2.0}, clock=lambda: now[0],
                                        sleeper=sleeper)
        limiter.wait("https://netflix.com/a")
        limiter.wait("https://netflix.com/b")
        self.assertEqual(slept, [], "netflix.com 不是 x.com 的子域，不该被限速")
        limiter.wait("https://mobile.x.com/a")
        limiter.wait("https://www.x.com/b")
        self.assertEqual(slept, [2.0], "同一主机的第二次请求必须等一个间隔")

    def test_host_under_rejects_a_domain_that_merely_ends_with_the_key(self):
        self.assertTrue(scripting.host_under("x.com", ("x.com",)))
        self.assertTrue(scripting.host_under("mobile.X.com", ("x.com",)))
        self.assertFalse(scripting.host_under("notx.com", ("x.com",)))
        self.assertFalse(scripting.host_under("", ("x.com",)))


class ReleaseTagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release = load_script("release_tag")

    def test_latest_matching_master_run_must_finish_successfully(self):
        success = dict(id=1, head_sha="abc", head_branch="master", event="push",
                       status="completed", conclusion="success", html_url="test-url")
        self.assertEqual(self.release.require_success([success], "abc"), success)
        for changes in ({"head_sha": "other"}, {"head_branch": "feature"},
                        {"event": "pull_request"}, {"status": "in_progress"},
                        {"conclusion": "failure"}, {"conclusion": "cancelled"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.release.require_success([{**success, **changes}], "abc")
        with self.assertRaises(ValueError):
            self.release.require_success([success, {**success, "id": 2, "conclusion": "failure"}], "abc")

    def test_default_is_read_only_and_apply_stops_if_master_moves(self):
        planned = dict(sha="abc", tag="v1.2.3", repo="owner/repo")
        with mock.patch.object(self.release, "plan", return_value=planned), \
                mock.patch.object(self.release, "command") as command, redirect_stdout(io.StringIO()):
            self.assertEqual(self.release.main([]), 0)
            command.assert_not_called()
            with mock.patch.object(self.release, "api", return_value={"object": {"sha": "other"}}):
                self.assertEqual(self.release.main(["--apply"]), 1)
            command.assert_not_called()

    def test_apply_pushes_only_the_validated_tag(self):
        planned = dict(sha="abc", tag="v1.2.3", repo="owner/repo")
        with mock.patch.object(self.release, "plan", return_value=planned), \
                mock.patch.object(self.release, "api", return_value={"object": {"sha": "abc"}}), \
                mock.patch.object(self.release, "command") as command, redirect_stdout(io.StringIO()):
            self.assertEqual(self.release.main(["--repo", "owner/repo", "--apply"]), 0)
            self.assertEqual(command.call_args_list[-1], mock.call(
                "git", "push", "https://github.com/owner/repo.git", "refs/tags/v1.2.3"))
            self.assertNotIn("--force", str(command.call_args_list))

    def test_unmerged_commit_cannot_pass_release_verification(self):
        with mock.patch.object(self.release, "api", return_value={"status": "ahead"}), \
                self.assertRaises(ValueError):
            self.release.verify("owner/repo", "abc")

    #: 干净的 master 检出，HEAD 与远端一致。
    CLEAN = {("git", "status", "--porcelain"): "",
             ("git", "branch", "--show-current"): "master",
             ("git", "rev-parse", "HEAD"): "abc"}

    def _shell(self, changes=None):
        answers = {**self.CLEAN, **(changes or {})}
        return mock.patch.object(self.release, "command",
                                 side_effect=lambda *args, **_: answers.get(args, ""))

    def test_plan_refuses_dirty_checkout_wrong_branch_and_existing_tags(self):
        for changes, refs in (({("git", "status", "--porcelain"): " M file"}, []),
                              ({("git", "branch", "--show-current"): "feature"}, []),
                              ({}, [{"ref": "refs/tags/v0.7.14"}])):
            with self.subTest(changes=changes, refs=refs), self._shell(changes), \
                    mock.patch.object(self.release, "api", side_effect=lambda repo, path, refs=refs: {"object": {"sha": "abc"}} if path == "git/ref/heads/master" else refs), \
                    mock.patch.object(self.release.version_bump, "read_version", return_value="0.7.14"), \
                    mock.patch.object(Path, "read_text", return_value="## [0.7.14] - 2026-09-07\n"), \
                    self.assertRaises(ValueError):
                self.release.plan("owner/repo")

    def test_a_version_without_its_changelog_section_cannot_be_tagged(self):
        """使用者读到的说明只有变更日志那一节；缺了就等于发一个没有说明的版本。"""
        with self._shell(), \
                mock.patch.object(self.release, "api", return_value={"object": {"sha": "abc"}}), \
                mock.patch.object(self.release.version_bump, "read_version", return_value="0.7.14"), \
                mock.patch.object(Path, "read_text", return_value="# 变更日志\n\n## [未发布]\n"), \
                self.assertRaisesRegex(ValueError, "CHANGELOG.md 缺少 0.7.14"):
            self.release.plan("owner/repo")

    def _planned_bump(self):
        return {"range": "v0.7.14..HEAD", "bump": "minor", "current": "0.7.14",
                "version": "0.8.0", "commits": 3}

    def test_a_bump_plan_writes_nothing_until_apply(self):
        with self._shell(), mock.patch.object(self.release, "api", return_value=[]), \
                mock.patch.object(self.release.version_bump, "plan_bump",
                                  return_value=self._planned_bump()), \
                mock.patch.object(self.release.version_bump, "write_version") as write, \
                mock.patch.object(self.release.changelog, "release") as promote:
            result = self.release.prepare("owner/repo", "auto", apply=False)
        self.assertEqual((result["tag"], result["version"], result["bump"]),
                         ("v0.8.0", "0.8.0", "minor"))
        write.assert_not_called()
        promote.assert_not_called()

    def test_applying_a_bump_writes_the_version_and_the_changelog_but_commits_nothing(self):
        """措辞要人过一遍，所以脚本只落盘；提交与标签是后面两步。"""
        with self._shell() as command, mock.patch.object(self.release, "api", return_value=[]), \
                mock.patch("scripts.release_review.review", return_value={"complete": True}) as review, \
                mock.patch.object(self.release.version_bump, "plan_bump",
                                  return_value=self._planned_bump()), \
                mock.patch.object(self.release.version_bump, "write_version") as write, \
                mock.patch.object(self.release.changelog, "release") as promote:
            result = self.release.prepare("owner/repo", "auto", apply=True)
        write.assert_called_once_with(self.release.ROOT, "minor")
        review.assert_called_once_with(self.release.ROOT, "owner/repo")
        self.assertEqual(promote.call_args.args, (self.release.ROOT, "0.8.0"))
        self.assertEqual(promote.call_args.kwargs["spec"], "v0.7.14..HEAD")
        self.assertTrue(result["next"], "落盘之后必须告诉人下一步做什么")
        issued = [args for args, _ in command.call_args_list]
        self.assertFalse([args for args in issued
                          if {"commit", "tag", "push"} & set(args)], issued)

    def test_incomplete_upstream_review_keeps_version_files_untouched(self):
        with self._shell(), mock.patch.object(self.release, "api", return_value=[]), \
                mock.patch.object(self.release.version_bump, "plan_bump",
                                  return_value=self._planned_bump()), \
                mock.patch("scripts.release_review.review", side_effect=ValueError("未取得")), \
                mock.patch.object(self.release.version_bump, "write_version") as write, \
                mock.patch.object(self.release.changelog, "release") as promote, \
                self.assertRaisesRegex(ValueError, "未取得"):
            self.release.prepare("owner/repo", "auto", apply=True)
        write.assert_not_called()
        promote.assert_not_called()

    def test_a_bump_stops_when_the_target_tag_already_exists(self):
        with self._shell(), \
                mock.patch.object(self.release, "api", return_value=[{"ref": "refs/tags/v0.8.0"}]), \
                mock.patch.object(self.release.version_bump, "plan_bump",
                                  return_value=self._planned_bump()), \
                mock.patch.object(self.release.version_bump, "write_version") as write, \
                self.assertRaisesRegex(ValueError, "v0.8.0 已存在"):
            self.release.prepare("owner/repo", "auto", apply=True)
        write.assert_not_called()


class PinnedReleaseTests(unittest.TestCase):
    """标签、说明与 CI 都绑定已确认的提交，主线可以继续推进。"""

    SHA = "1" * 40
    NOTES = "## [0.37.0] - 2026-10-01\n\n### 修复\n\n- **界面**：统计页布局稳定。\n"

    @classmethod
    def setUpClass(cls):
        cls.release = load_script("release_tag")

    def _shell(self, changes=None):
        answers = {
            ("git", "status", "--porcelain"): "",
            ("git", "branch", "--show-current"): "master",
            ("git", "rev-parse", f"{self.SHA}^{{commit}}"): self.SHA,
            ("git", "show", f"{self.SHA}:src/peach/__init__.py"): '__version__ = "0.37.0"',
            ("git", "show", f"{self.SHA}:CHANGELOG.md"): self.NOTES,
            ("git", "tag", "--list", "v0.37.0"): "",
        }
        answers.update(changes or {})
        return mock.patch.object(self.release, "command",
                                 side_effect=lambda *args, **_: answers.get(args, ""))

    def _api(self, *, status="behind", conclusion="success", tags=()):
        def answer(_repo, endpoint):
            if endpoint.startswith("git/matching-refs/tags/"):
                return list(tags)
            if endpoint == f"compare/master...{self.SHA}":
                return {"status": status}
            if endpoint.startswith("actions/workflows/test.yml/runs"):
                return {"workflow_runs": [dict(
                    id=1, head_sha=self.SHA, head_branch="master", event="push",
                    status="completed", conclusion=conclusion, html_url="selected-test")]}
            raise AssertionError(endpoint)
        return mock.patch.object(self.release, "api", side_effect=answer)

    def test_plan_reads_version_notes_and_ci_from_the_selected_commit(self):
        with self._shell() as command, self._api(), \
                mock.patch.object(self.release.version_bump, "read_version") as local_version, \
                mock.patch.object(Path, "read_text") as local_notes:
            result = self.release.plan_commit("owner/repo", self.SHA)
        self.assertEqual((result["sha"], result["tag"], result["test"]),
                         (self.SHA, "v0.37.0", "selected-test"))
        self.assertIn("统计页布局稳定", result["section"])
        local_version.assert_not_called()
        local_notes.assert_not_called()
        self.assertEqual(ShipTests._writes(command), [])

    def test_moving_refs_and_abbreviated_shas_are_rejected(self):
        for sha in ("", "master", "HEAD", "v0.37.0", self.SHA[:8], "z" * 40):
            with self.subTest(sha=sha), self._shell(), self.assertRaisesRegex(ValueError, "完整"):
                self.release.plan_commit("owner/repo", sha)

    def test_an_explicit_empty_sha_does_not_select_the_current_head(self):
        with self._shell() as command, mock.patch.object(self.release, "plan") as current, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(self.release.main(["--release-sha", "", "--apply"]), 1)
        current.assert_not_called()
        self.assertEqual(ShipTests._writes(command), [])

    def test_dirty_checkout_invalid_commit_version_notes_and_local_tag_stop_before_writes(self):
        cases = [
            {("git", "status", "--porcelain"): " M file"},
            {("git", "branch", "--show-current"): "feature"},
            {("git", "rev-parse", f"{self.SHA}^{{commit}}"): "2" * 40},
            {("git", "show", f"{self.SHA}:src/peach/__init__.py"): ""},
            {("git", "show", f"{self.SHA}:CHANGELOG.md"): "## [未发布]\n"},
            {("git", "show", f"{self.SHA}:CHANGELOG.md"): self.NOTES.replace("**界面**：", "web：")},
            {("git", "tag", "--list", "v0.37.0"): "v0.37.0"},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self._shell(changes) as command, self._api(), \
                    self.assertRaises((ValueError, self.release.version_bump.VersionError)):
                self.release.plan_commit("owner/repo", self.SHA)
            self.assertEqual(ShipTests._writes(command), [])

    def test_unmerged_red_ci_and_remote_tag_cannot_be_released(self):
        for options in (dict(status="ahead"), dict(status="diverged"),
                        dict(conclusion="failure"), dict(tags=[{"ref": "refs/tags/v0.37.0"}])):
            with self.subTest(options=options), self._shell() as command, self._api(**options), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(self.release.main(["--release-sha", self.SHA, "--apply"]), 1)
            self.assertEqual(ShipTests._writes(command), [])

    def test_apply_rechecks_the_selected_commit_and_pushes_only_its_annotated_tag(self):
        with self._shell() as command, self._api(), \
                mock.patch.object(self.release, "plan_commit", wraps=self.release.plan_commit) as plan, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(self.release.main([
                "--repo", "owner/repo", "--release-sha", self.SHA, "--apply"]), 0)
        self.assertEqual(plan.call_args_list, [mock.call("owner/repo", self.SHA)] * 2)
        self.assertEqual(ShipTests._writes(command), [
            ("git", "tag", "-a", "v0.37.0", self.SHA, "-m", "Peach v0.37.0 Windows 测试版"),
            ("git", "push", "https://github.com/owner/repo.git", "refs/tags/v0.37.0"),
        ])

    def test_a_failed_recheck_cannot_create_a_tag(self):
        planned = dict(sha=self.SHA, tag="v0.37.0", repo="owner/repo")
        with mock.patch.object(self.release, "plan_commit", side_effect=[planned, ValueError("CI 已取消")]), \
                mock.patch.object(self.release, "command") as command, redirect_stdout(io.StringIO()):
            self.assertEqual(self.release.main(["--release-sha", self.SHA, "--apply"]), 1)
        command.assert_not_called()


class ShipTests(unittest.TestCase):
    """`--ship`：定好版之后一路到标签，中途停下再跑一次要接着走。"""

    @classmethod
    def setUpClass(cls):
        cls.release = load_script("release_tag")

    #: 一次干净的起点：在 master 上，工作区正好是那两份定版文件，本地没有这个标签。
    SHELL = {("git", "branch", "--show-current"): "master",
             ("git", "status", "--porcelain"): " M CHANGELOG.md\n M src/peach/__init__.py",
             ("git", "rev-parse", "HEAD"): "abc",
             ("git", "tag", "--list", "v0.8.0"): ""}

    SUCCESS = dict(id=1, head_sha="abc", head_branch="master", event="push",
                   status="completed", conclusion="success", html_url="test-url")

    def _shell(self, changes=None):
        answers = {**self.SHELL, **(changes or {})}
        return mock.patch.object(self.release, "command",
                                 side_effect=lambda *args, **_: answers.get(args, ""))

    def _api(self, *, tags=(), remote="old", runs=(SUCCESS,)):
        def answer(_repo, endpoint):
            if endpoint.startswith("git/matching-refs/tags/"):
                return list(tags)
            if endpoint == "git/ref/heads/master":
                return {"object": {"sha": remote}}
            if endpoint.startswith("actions/workflows/test.yml/runs"):
                return {"workflow_runs": list(runs)}
            raise AssertionError(f"没预料到的调用：{endpoint}")
        return mock.patch.object(self.release, "api", side_effect=answer)

    def _version(self):
        return mock.patch.object(self.release.version_bump, "read_version", return_value="0.8.0")

    def _document(self, text="## [0.8.0] - 2026-09-07\n\n### 新增\n\n- **界面**：那件事\n"):
        return mock.patch.object(Path, "read_text", return_value=text)

    @staticmethod
    def _writes(command):
        """真正动了仓库的那些调用；`git tag --list` 这种查询不算。
        发版提交带着 `-c peach.masterWriter=release` 过主检出的 pre-commit，排在 `commit` 前面。"""
        return [args for args, _ in command.call_args_list
                if args[:2] in {("git", "add"), ("git", "commit"), ("git", "push"), ("git", "-c")}
                or args[:3] == ("git", "tag", "-a")]

    def test_a_plan_without_apply_writes_nothing(self):
        with self._shell() as command, self._api(), self._version(), self._document():
            result = self.release.ship("owner/repo", apply=False)
        self.assertEqual((result["tag"], result["commit"], result["push"]),
                         ("v0.8.0", True, True))
        self.assertEqual(self._writes(command), [])

    def test_pending_files_keep_the_status_column_of_the_first_line(self):
        """`git status --porcelain` 首行以空格开头；按列取路径前不能对整段输出 strip。

        2026-09-08 定版时两份文件都在，发布却被拒：第一行被读成 `HANGELOG.md`。"""
        porcelain = " M CHANGELOG.md\n M src/peach/__init__.py\n"
        with mock.patch.object(self.release.subprocess, "run",
                               return_value=mock.Mock(stdout=porcelain)):
            self.assertEqual(self.release._pending_files(),
                             ["CHANGELOG.md", "src/peach/__init__.py"])

    def test_unrelated_changes_are_never_carried_into_the_release_commit(self):
        """标签指向发布提交，夹带什么就等于发出去什么。"""
        dirty = {("git", "status", "--porcelain"):
                 " M CHANGELOG.md\n M src/peach/api.py\n M src/peach/__init__.py"}
        with self._shell(dirty) as command, self._api(), self._version(), self._document(), \
                self.assertRaisesRegex(ValueError, "src/peach/api.py"):
            self.release.ship("owner/repo", apply=True)
        self.assertEqual(self._writes(command), [])

    def test_a_version_without_its_changelog_section_cannot_ship(self):
        with self._shell(), self._api(), self._version(), \
                self._document("# 变更日志\n\n## [未发布]\n"), \
                self.assertRaisesRegex(ValueError, "缺少 0.8.0"):
            self.release.ship("owner/repo", apply=True)

    def test_an_unclassified_release_note_cannot_be_committed(self):
        raw = "## [0.8.0] - 2026-09-07\n\n### 修复\n\n- web：那件事\n"
        with self._shell() as command, self._api(), self._version(), self._document(raw), \
                self.assertRaisesRegex(self.release.version_bump.VersionError, "未归类.*web"):
            self.release.ship("owner/repo", apply=True)
        self.assertEqual(self._writes(command), [])

    def test_an_occupied_tag_stops_the_whole_thing(self):
        for tags, local, message in (([{"ref": "refs/tags/v0.8.0"}], "", "已存在"),
                                     ([], "v0.8.0", "本地 v0.8.0 已存在")):
            with self.subTest(message=message), \
                    self._shell({("git", "tag", "--list", "v0.8.0"): local}), \
                    self._api(tags=tags), self._version(), self._document(), \
                    self.assertRaisesRegex(ValueError, message):
                self.release.ship("owner/repo", apply=True)

    def test_apply_commits_pushes_waits_then_tags_in_that_order(self):
        with self._shell() as command, self._api(), self._version(), self._document():
            result = self.release.ship("owner/repo", apply=True)
        self.assertEqual(self._writes(command), [
            ("git", "add", "src/peach/__init__.py", "CHANGELOG.md"),
            ("git", "-c", "peach.masterWriter=release", "commit", "-m", "chore(release): 版本 0.8.0"),
            ("git", "push", "https://github.com/owner/repo.git", "refs/heads/master"),
            ("git", "tag", "-a", "v0.8.0", "abc", "-m", "Peach v0.8.0 Windows 测试版"),
            ("git", "push", "https://github.com/owner/repo.git", "refs/tags/v0.8.0"),
        ])
        self.assertEqual(result["test"], "test-url")

    def test_a_red_test_run_never_becomes_a_tag(self):
        red = {**self.SUCCESS, "conclusion": "failure"}
        with self._shell() as command, self._api(runs=(red,)), self._version(), \
                self._document(), self.assertRaisesRegex(ValueError, "尚未通过"):
            self.release.ship("owner/repo", apply=True)
        issued = self._writes(command)
        self.assertIn(("git", "-c", "peach.masterWriter=release", "commit",
                       "-m", "chore(release): 版本 0.8.0"), issued)
        self.assertFalse([args for args in issued if "refs/tags/v0.8.0" in args], issued)

    def test_running_it_again_skips_what_is_already_done(self):
        """网络断在半路就再跑一次：已提交的不重提，已推送的不重推。"""
        with self._shell({("git", "status", "--porcelain"): ""}) as command, \
                self._api(remote="abc"), self._version(), self._document():
            result = self.release.ship("owner/repo", apply=True)
        self.assertEqual((result["commit"], result["push"]), (False, False))
        self.assertEqual(self._writes(command), [
            ("git", "tag", "-a", "v0.8.0", "abc", "-m", "Peach v0.8.0 Windows 测试版"),
            ("git", "push", "https://github.com/owner/repo.git", "refs/tags/v0.8.0"),
        ])


class AwaitTestTests(unittest.TestCase):
    """等 Test 出结果：绿了才回来，红了不等，超时说清楚接下来怎么办。"""

    @classmethod
    def setUpClass(cls):
        cls.release = load_script("release_tag")

    RUNNING = dict(id=1, head_sha="abc", head_branch="master", event="push",
                   status="in_progress", conclusion=None, html_url="test-url")

    def _runs(self, *rounds):
        answers = iter(rounds)
        return mock.patch.object(self.release, "test_runs",
                                 side_effect=lambda *_: list(next(answers)))

    def test_polling_continues_until_the_run_completes(self):
        done = {**self.RUNNING, "status": "completed", "conclusion": "success"}
        naps = []
        with self._runs((), (self.RUNNING,), (done,)):
            checked = self.release.await_test(
                "owner/repo", "abc", timeout=90, poll=30,
                sleep=naps.append, clock=lambda: len(naps) * 30)
        self.assertEqual(checked["html_url"], "test-url")
        self.assertEqual(naps, [30, 30])

    def test_a_finished_red_run_is_not_worth_waiting_out(self):
        red = {**self.RUNNING, "status": "completed", "conclusion": "failure"}
        naps = []
        with self._runs((red,)), self.assertRaisesRegex(ValueError, "尚未通过"):
            self.release.await_test("owner/repo", "abc", timeout=3600, poll=30,
                                    sleep=naps.append, clock=lambda: 0)
        self.assertEqual(naps, [], "红的等多久都不会变绿")

    def test_giving_up_says_the_commit_is_already_pushed(self):
        with self._runs((self.RUNNING,), (self.RUNNING,)), \
                self.assertRaisesRegex(ValueError, "--ship --apply"):
            self.release.await_test("owner/repo", "abc", timeout=30, poll=30,
                                    sleep=lambda _: None, clock=iter([0, 30, 60]).__next__)


class Fc2SellerAndDescriptiveNameRepairTests(unittest.TestCase):
    """存量修正：FC2 卖家厂牌改挂 `FC2-PPV`，描述性称呼女优删掉（ADR-0054）。"""

    STAMP = "2026-09-24T00:00:00Z"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.db = fresh_ledger(self.root)
        self.generated = self.root / "generated"
        (self.generated / "avatars").mkdir(parents=True)
        con = sqlite3.connect(self.db)
        entities = [(1, "studio", "FC2-PPV"), (2, "studio", "プライベートアーカイブ管理人"),
                    (3, "performer", "145cm色白お嬢様"), (4, "performer", "wink的美女"),
                    (5, "performer", "音あずさ")]
        for entity_id, kind, name in entities:
            con.execute("INSERT INTO entity(id,kind,canonical_name,normalized_name,created_at,"
                        "updated_at) VALUES(?,?,?,?,?,?)",
                        (entity_id, kind, name, name.casefold(), self.STAMP, self.STAMP))
        for asset_id, code, studio in [(10, "FC2-PPV-4927200", "プライベートアーカイブ管理人"),
                                       (11, "FC2-PPV-1785524", "FC2-PPV"),
                                       (12, "ABP-001", "プライベートアーカイブ管理人")]:
            con.execute("INSERT INTO asset(id,location,path,name,medium,code,studio,field_owners)"
                        " VALUES(?,'local',?,?,'video',?,?,?)",
                        (asset_id, f"/x/{code}.mp4", f"{code}.mp4", code, studio,
                         json.dumps({"studio": "auto:local_nfo"})))
        relations = [(10, 2, "studio", "javinizer:local_nfo:studio"),
                     (11, 1, "studio", "javinizer:fc2:studio"),
                     (12, 2, "studio", "javinizer:local_nfo:studio"),
                     (11, 3, "performer", "javinizer:javdb:performer"),
                     (11, 4, "performer", "stash"),
                     (11, 5, "performer", "javinizer:javdb:performer")]
        for asset_id, entity_id, role, source in relations:
            con.execute("INSERT INTO asset_entity(asset_id,entity_id,role,source) VALUES(?,?,?,?)",
                        (asset_id, entity_id, role, source))
        con.execute("INSERT INTO asset_tag(asset_id,tag,confidence,source) VALUES(11,?,0.6,?)",
                    ("演员:145cm色白お嬢様", "javinizer:javdb:performer"))
        con.execute("INSERT INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
                    " VALUES(3,'色白お嬢様','色白お嬢様','test',1.0)")
        con.execute("INSERT INTO review_decision(category,item_key,status,reviewer,note,updated_at)"
                    " VALUES('metadata_fields','asset:11:performers','approved','local-default',?,?)",
                    (json.dumps({"auto_applied": True, "value": "145cm色白お嬢様"},
                                ensure_ascii=False), self.STAMP))
        con.commit()
        con.close()
        for suffix in (".img", ".img.provenance.json"):
            (self.generated / "avatars" / f"performer-3{suffix}").write_bytes(b"x")
        (self.generated / "avatars" / "performer-5.img").write_bytes(b"x")

    def run_repair(self, *extra):
        output = io.StringIO()
        with redirect_stdout(output):
            code = load_script("repair_fc2_sellers_and_descriptive_names").main(
                ["--db", str(self.db), "--generated-root", str(self.generated), *extra])
        return code, output.getvalue()

    def query(self, sql, *params):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def test_a_dry_run_writes_nothing(self):
        code, output = self.run_repair()
        self.assertEqual(code, 0)
        self.assertIn("厂牌 1 部，女优 1 位，头像文件 2 个", output)
        self.assertEqual(self.query("SELECT studio FROM asset WHERE id=10"),
                         [("プライベートアーカイブ管理人",)])
        self.assertTrue((self.generated / "avatars" / "performer-3.img").exists())

    def test_apply_moves_sellers_under_fc2_ppv_and_drops_descriptive_performers(self):
        code, output = self.run_repair("--apply", "--backup", str(self.root / "backup.db"))
        self.assertEqual(code, 0, output)
        self.assertTrue((self.root / "backup.db").exists())
        self.assertEqual(self.query("SELECT id,studio FROM asset ORDER BY id"),
                         [(10, "FC2-PPV"), (11, "FC2-PPV"), (12, "プライベートアーカイブ管理人")])
        self.assertEqual(self.query(
            "SELECT asset_id,entity_id FROM asset_entity WHERE role='studio' ORDER BY asset_id"),
            [(10, 1), (11, 1), (12, 2)])
        # 非 FC2 作品还挂着它，卖家实体这回不删。
        self.assertEqual(self.query("SELECT count(*) FROM entity WHERE id=2"), [(1,)])
        self.assertEqual(self.query("SELECT id FROM entity WHERE kind='performer' ORDER BY id"),
                         [(4,), (5,)], "人工来源挂着的与真艺名都留下")
        self.assertEqual(self.query("SELECT count(*) FROM asset_tag"), [(0,)])
        self.assertEqual(self.query("SELECT count(*) FROM entity_alias"), [(0,)])
        self.assertEqual(self.query("SELECT count(*) FROM review_decision"), [(0,)])
        self.assertEqual(sorted(path.name for path in (self.generated / "avatars").iterdir()),
                         ["performer-5.img"])
        self.assertEqual(
            sorted(path.name for path in (self.generated / "avatars-superseded").iterdir()),
            ["performer-3.img", "performer-3.img.provenance.json"])
        self.assertIn("integrity_check=ok foreign_key_check=0", output)

    def test_a_seller_left_with_no_works_is_dropped(self):
        con = sqlite3.connect(self.db)
        con.execute("DELETE FROM asset_entity WHERE asset_id=12")
        con.execute("DELETE FROM asset WHERE id=12")
        con.commit()
        con.close()
        code, output = self.run_repair("--apply", "--backup", str(self.root / "backup.db"))
        self.assertEqual(code, 0, output)
        self.assertEqual(self.query("SELECT count(*) FROM entity WHERE id=2"), [(0,)])


if __name__ == "__main__":
    unittest.main()
