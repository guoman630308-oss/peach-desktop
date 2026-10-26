import importlib.util
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_checker():
    path = ROOT / "scripts" / "check_reference_updates.py"
    spec = importlib.util.spec_from_file_location("check_reference_updates", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ReleaseReviewTests(unittest.TestCase):
    def test_network_failure_keeps_later_checks_and_records_missing_evidence(self):
        from scripts import release_review as review
        registry = {"pins": [], "sources": [
            {"id": "first", "cadence": "weekly"},
            {"id": "second", "cadence": "quarterly"},
            {"id": "manual", "cadence": "manual"},
        ]}

        def run(args, root, **kwargs):
            if args[0] == "git":
                return "head"
            if args[0] == "gh":
                return '[[{"number": 3, "title": "更新", "html_url": "url", "user": {"login": "dependabot[bot]"}}]]'
            if args[0] == "npm":
                self.assertEqual(kwargs["allowed"], (0, 1))
                return "{}"
            return "locked tree"

        with mock.patch.object(review.references, "load_registry", return_value=registry), \
                mock.patch.object(review.references, "validate_registry", return_value=[]), \
                mock.patch.object(review, "run", side_effect=run), \
                mock.patch.object(review.references, "inspect_source", side_effect=[
                    OSError("network unavailable"), {"changed": True, "live": b"evidence"}
                ]) as inspect:
            report = review.collect(ROOT, "owner/repo")
        self.assertFalse(report["complete"])
        self.assertEqual(inspect.call_count, 2)
        by_id = {item["id"]: item for item in report["checks"]}
        self.assertEqual(by_id["first"]["status"], "未取得")
        self.assertIn("network unavailable", by_id["first"]["error"])
        self.assertEqual(by_id["second"]["result"], {"changed": True})
        self.assertNotIn("manual", by_id)
        self.assertEqual(by_id["dependabot"]["result"][0]["number"], 3)

    def test_report_is_saved_before_incomplete_review_is_rejected(self):
        import json
        from scripts import release_review as review
        report = {"complete": False, "checks": [{"id": "example", "status": "未取得"}]}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with mock.patch.object(review, "collect", return_value=report), \
                    self.assertRaisesRegex(ValueError, "example"):
                review.review(root, "owner/repo")
            self.assertEqual(json.loads((root / "build/release-review.json").read_text(
                encoding="utf-8")), report)


class ReferenceUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load_checker()

    def test_registered_snapshots_match_their_locks(self):
        registry = self.checker.load_registry(ROOT / "docs" / "reference-sources.json")
        self.assertEqual(self.checker.validate_registry(ROOT, registry), [])
        sources = {source["id"]: source for source in registry["sources"]}
        self.assertEqual(
            set(sources),
             {"open-claude-ui-design-manual", "open-claude-ui-rich-composer",
             "open-claude-ui-scroll-fade", "open-claude-ui-lightbox", "open-claude-ui-license",
             "folia-wall-blocks", "folia-wall-style", "folia-wall-reflows", "folia-wall-motion", "readme-immich", "readme-bruno", "readme-hoppscotch",
             "vercel-report-design", "vercel-web-interface-guidelines",
             "fiu758-studio-logo-discovery", "rule34-follow-tags-and-collections",
             "f95-masked-gofile-media", "follow-fanbox-gofile-paheal",
             "fanbox-browser-transport", "beeg-profile-layout",
             "vercel-geist-table-ranking",
             "vercel-geist-fieldset-scroller-empty-state",
             "vercel-geist-command-search-loading",
             "vercel-notifications-note",
             "youtube-player-controls-user-screenshot",
             "openaver-related-ranking",
             "vercel-geist-tabs-secondary",
             "vercel-geist-switch-segmented",
             "boardui-input",
             "boardui-theme",
             "boardui-auth-card",
             "fluid-springs", "fluid-tabs",
             "amane-content-routes",
             "readme-yingku",
             "readme-openaver", "readme-amane",
             "readme-stash", "readme-metatube", "readme-mdcx",
             "readme-sakuramediabe", "readme-sakuramedia", "readme-neoavdc",
             "readme-javboss", "readme-javdex", "readme-javm", "readme-mdcz",
             "readme-javinizer-go", "readme-ammds-doc", "readme-ammds-docker",
             "readme-javranking-extension", "readme-cuelume",
             "readme-jav-moviemanager", "readme-nassav", "readme-garage",
             "readme-atlas", "readme-javinfo-cli", "readme-javinfo-mcp",
             "readme-javinfo-legacy",
             "evilcharts-registry",
             "youtube-stats-buffer-20260829"},
        )
        self.assertNotEqual(
            sources["vercel-report-design"]["url"],
            sources["vercel-web-interface-guidelines"]["url"],
        )

    def test_every_snapshot_file_is_registered_or_says_why_it_is_not(self):
        """快照目录里不许有身份不明的文件。

        登记表的契约是「快照文件 + 可重抓 URL」。React 渲染的规格页、用户截图和「哪个主机
        还能取到图」这类实测给不出可哈希的上游快照，本来就不该登记。但目录里「不该登记」和
        「忘了登记」长得一模一样，所以未登记的必须在正文里自己说明理由。
        """
        registry = self.checker.load_registry(ROOT / "docs" / "reference-sources.json")
        registered = {source["snapshot"] for source in registry["sources"]}
        undeclared = []
        for path in sorted((ROOT / "docs" / "reference-snapshots").glob("*.md")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in registered:
                continue
            if "reference-sources.json" not in path.read_text(encoding="utf-8"):
                undeclared.append(rel)
        self.assertEqual(
            undeclared, [],
            "这些快照既没登记进 docs/reference-sources.json，也没写明为什么不登记",
        )

    def test_changed_markdown_is_reported_without_mutating_the_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = root / "snapshot.md"
            snapshot.write_text("old\n", encoding="utf-8")
            source = {
                "id": "example",
                "url": "https://example.invalid/reference.md",
                "snapshot": "snapshot.md",
                "sha256": self.checker.sha256_bytes(b"old\n"),
            }
            result = self.checker.inspect_source(
                root,
                source,
                fetcher=lambda _url: b"new\n",
                revision_resolver=lambda _source: None,
            )
            self.assertTrue(result["changed"])
            self.assertIn("-old", result["diff"])
            self.assertIn("+new", result["diff"])
            self.assertEqual(snapshot.read_text(encoding="utf-8"), "old\n")

    def test_registry_rejects_a_snapshot_changed_without_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "snapshot.md").write_text("changed\n", encoding="utf-8")
            registry = {
                "sources": [
                    {
                        "id": "example",
                        "snapshot": "snapshot.md",
                        "sha256": self.checker.sha256_bytes(b"locked\n"),
                    }
                ]
            }
            problems = self.checker.validate_registry(root, registry)
            self.assertTrue(any("快照校验失败" in problem for problem in problems))

    def _git_source(self, root: Path, **extra) -> dict:
        (root / "snapshot.md").write_text("same\n", encoding="utf-8")
        return {
            "id": "example",
            "url": "https://example.invalid/reference.md",
            "snapshot": "snapshot.md",
            "sha256": self.checker.sha256_bytes(b"same\n"),
            "git": {"repository": "https://example.invalid/r.git", "ref": "refs/heads/main",
                    "revision": "a" * 40},
            **extra,
        }

    def test_a_moved_revision_with_identical_content_is_not_an_update(self):
        with tempfile.TemporaryDirectory() as temp:
            source = self._git_source(Path(temp))
            result = self.checker.inspect_source(
                Path(temp), source, fetcher=lambda _url: b"same\n",
                revision_resolver=lambda _source: "b" * 40,
            )
            self.assertFalse(result["changed"])
            self.assertTrue(result["revision_moved"])

    def test_a_volatile_page_is_judged_by_its_revision_alone(self):
        with tempfile.TemporaryDirectory() as temp:
            source = self._git_source(Path(temp), volatile=True)
            same_revision = self.checker.inspect_source(
                Path(temp), source, fetcher=lambda _url: b"session token 123\n",
                revision_resolver=lambda _source: "a" * 40,
            )
            moved = self.checker.inspect_source(
                Path(temp), source, fetcher=lambda _url: b"session token 456\n",
                revision_resolver=lambda _source: "b" * 40,
            )
            self.assertFalse(same_revision["changed"])
            self.assertTrue(moved["changed"])

    def test_every_source_has_a_cadence_and_volatile_pages_without_git_are_manual(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._git_source(root)
            del source["git"]
            registry = {"sources": [dict(source), dict(source, id="volatile", volatile=True,
                                                         cadence="weekly")]}
            problems = self.checker.validate_registry(root, registry)
            self.assertTrue(any("example 的 cadence" in problem for problem in problems))
            self.assertTrue(any("volatile 每次取回都不同" in problem for problem in problems))

    def test_manual_sources_only_run_when_named(self):
        registry = {"sources": [{"id": "weekly", "cadence": "weekly"},
                                {"id": "page", "cadence": "manual"}]}
        default = self.checker.selected_sources(registry, None)
        named = self.checker.selected_sources(registry, "page")
        weekly = self.checker.selected_sources(registry, None, "weekly")
        self.assertEqual([source["id"] for source in default], ["weekly"])
        self.assertEqual([source["id"] for source in named], ["page"])
        self.assertEqual([source["id"] for source in weekly], ["weekly"])

    def test_a_pinned_dependency_reports_when_upstream_moves_past_the_pin(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pinned = "c" * 40
            (root / "pyproject.toml").write_text(
                f'dependencies = ["dep @ git+https://github.com/o/dep@{pinned}"]\n',
                encoding="utf-8",
            )
            pin = {"id": "dep", "cadence": "weekly", "manifest": "pyproject.toml",
                   "repository": "https://github.com/o/dep.git", "ref": "refs/heads/main"}
            current = self.checker.inspect_pin(root, pin, revision_resolver=lambda _pin: pinned)
            behind = self.checker.inspect_pin(root, pin, revision_resolver=lambda _pin: "d" * 40)
            self.assertFalse(current["changed"])
            self.assertTrue(behind["changed"])
            self.assertEqual(behind["compare"],
                             f"https://github.com/o/dep/compare/{pinned}...{'d' * 40}")

    def test_a_pin_must_name_exactly_one_sha_in_its_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pyproject.toml").write_text('dependencies = ["dep==1.0"]\n', encoding="utf-8")
            registry = {"pins": [{"id": "dep", "cadence": "weekly", "manifest": "pyproject.toml",
                                  "repository": "https://github.com/o/dep.git",
                                  "ref": "refs/heads/main"}]}
            problems = self.checker.validate_registry(root, registry)
            self.assertTrue(any("恰好钉住一个 sha" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()
