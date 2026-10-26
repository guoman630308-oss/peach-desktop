"""amane 桥的 Peach 一侧：起子进程、翻 payload、失败分档、冷却动作，以及采集链上那一档。"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from peach import metadata_amane
from peach.jav_cover_fetch import NotFound, Unavailable
from peach.library_processing import LibraryMetadataProvider, PROVIDER_NAMES, SOURCE_LABELS
from peach.metadata import MetadataProviderError, extract_peach_fields
from peach.metadata_policy import SOURCE_SPECS
from peach.metadata_routes import AMANE_OFFICIAL_STAGE, AMANE_STAGE
from peach.scraping_access import SourcePaused, cooldown_state, paused_until, pause_source
from peach.sources import REASON_KINDS, FailureReason, SiteRecord

ROOT = Path(__file__).resolve().parents[1]


def completed(stdout, returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def report(**sites):
    return json.dumps({"number": "FC2-PPV-1234567", "language": "jp", "sites": sites,
                       "amane": {"version": "0.16.1"}}, ensure_ascii=False)


FOUND = {"status": "found", "metadata": {
    "number": "FC2-PPV-1234567", "title": "標題", "studio": "卖家", "publisher": "レーベル",
    "release": "2024-01-02", "runtime": 61, "tags": ["a", "b"], "series": "",
    "actors": [{"name": "女優", "gender": "female"}, {"name": "男", "gender": "male"}],
    "poster_urls": ["https://x/p.jpg"], "thumb_urls": ["https://x/t.jpg"],
    "trailer_urls": [], "extrafanart": ["https://x/1.jpg"], "directors": ["監督"],
    "source_url": "https://fc2club.top/html/FC2-PPV-1234567.html", "external_id": "https://fc2club.top/html/FC2-PPV-1234567.html",
}, "elapsed_s": 0.3}


class ManifestTests(unittest.TestCase):
    def test_the_manifest_pins_a_full_sha_and_the_lock_names_that_version(self):
        revision = metadata_amane.pinned_revision()
        self.assertRegex(revision, r"^[0-9a-f]{40}$")
        self.assertTrue(metadata_amane.locked_version())
        lock = (metadata_amane.BRIDGE_ROOT / "uv.lock").read_text(encoding="utf-8")
        self.assertIn(revision, lock)

    def test_every_opened_site_is_a_registered_source_of_its_tier_and_a_bridge_site(self):
        spec = importlib.util.spec_from_file_location("amane_bridge_sites", metadata_amane.BRIDGE_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        BRIDGE_SITES = module.SITES
        for sites, kind, stage in ((metadata_amane.COMMUNITY_SITES, "community", AMANE_STAGE),
                                   (metadata_amane.OFFICIAL_SITES, "official", AMANE_OFFICIAL_STAGE)):
            names = {name for name, _ in sites}
            self.assertEqual(names, set(stage))
            for site in names:
                with self.subTest(site=site):
                    self.assertEqual(SOURCE_SPECS[site].kind, kind)
                    self.assertIn(site, BRIDGE_SITES)
                    self.assertIn(site, SOURCE_LABELS)
                    self.assertIn(site, PROVIDER_NAMES)
        self.assertEqual(set(AMANE_STAGE) | set(AMANE_OFFICIAL_STAGE), set(metadata_amane.SITES))

    def test_each_site_has_one_owner(self):
        """自写解析器已经答的站不经桥再问一遍（ADR-0048）。"""
        from peach.sources import SITE_SOURCES
        for own in ("r18dev", "fc2", "javdb", "javbus", "avbase", "1pondo", "dmm"):
            with self.subTest(site=own):
                self.assertNotIn(own, metadata_amane.SITES)
        self.assertEqual(set(SITE_SOURCES) & set(metadata_amane.SITES), set())

    def test_every_upstream_failure_reason_maps_onto_one_contract_reason(self):
        reasons = set(__import__("re").findall(r'"([a-z_]+)",?\s', (
            metadata_amane.BRIDGE_SCRIPT).read_text(encoding="utf-8").split("FAILURE_REASONS = (", 1)[1]
            .split(")", 1)[0]))
        self.assertEqual(len(reasons), 17)
        self.assertEqual(reasons - set(metadata_amane.AMANE_REASONS), set())
        for upstream, reason in metadata_amane.AMANE_REASONS.items():
            with self.subTest(upstream=upstream):
                self.assertIsInstance(reason, FailureReason)
                self.assertIn(REASON_KINDS[reason], {"auth", "unavailable", "not_found"})

    def test_the_three_tier_classification_of_every_upstream_reason(self):
        """十七档各落到哪一档是冷却与重试的依据，逐条钉住；契约表改了这里要跟着改。"""
        expected = {
            "not_found": "not_found", "no_usable_metadata": "not_found",
            "rate_limited": "unavailable", "server_error": "unavailable", "timeout": "unavailable",
            "network": "unavailable", "http_error": "unavailable", "api_error": "unavailable", "empty_response": "unavailable",
            "unexpected": "unavailable", "crawler_unavailable": "unavailable", "parse_error": "unavailable",
            "cloudflare_challenge": "auth", "cloudflare_blocked": "auth", "ip_banned": "auth",
            "geo_restricted": "auth", "age_verification": "auth",
        }
        self.assertEqual({upstream: REASON_KINDS[reason] for upstream, reason in metadata_amane.AMANE_REASONS.items()},
                         expected)

    def test_a_bare_401_or_403_is_the_site_turning_this_exit_away(self):
        """Prestige 在非日本出口上回 CloudFront 403，正文里没有地区那句话，上游归 `http_error`。"""
        for status in (401, 403):
            with self.subTest(status=status):
                self.assertEqual(metadata_amane.contract_reason("http_error", status), FailureReason.AUTH_REQUIRED)
        self.assertEqual(metadata_amane.contract_reason("http_error", 500), FailureReason.SERVER_ERROR)
        self.assertEqual(metadata_amane.contract_reason("http_error"), FailureReason.SERVER_ERROR)
        self.assertEqual(metadata_amane.contract_reason("geo_restricted", 403), FailureReason.GEO_RESTRICTED)
        self.assertEqual(metadata_amane.contract_reason("something_new"), FailureReason.NETWORK)

    def test_bridge_sites_carry_the_same_config_shape_as_self_written_sites(self):
        official = {name for name, _ in metadata_amane.OFFICIAL_SITES}
        for site, config in metadata_amane.SITE_CONFIGS.items():
            with self.subTest(site=site):
                self.assertEqual((config.name, config.label, config.provider, config.stage),
                                 (site, SOURCE_LABELS[site], PROVIDER_NAMES[site],
                                  "official" if site in official else "amane"))
        self.assertEqual(metadata_amane.SITES, {site: config.label for site, config in metadata_amane.SITE_CONFIGS.items()})


class RebuildTests(unittest.TestCase):
    def test_the_rebuild_command_syncs_the_lock_into_the_tools_root(self):
        command, env = metadata_amane.rebuild_command(Path("T:/tools"), uv=Path("uv.exe"))
        self.assertEqual(command[:4], ["uv.exe", "sync", "--locked", "--no-dev"])
        self.assertIn("--python", command)
        self.assertEqual(command[command.index("--project") + 1], str(metadata_amane.BRIDGE_ROOT))
        self.assertEqual(Path(env["UV_PROJECT_ENVIRONMENT"]), Path("T:/tools") / "amane-bridge" / ".venv")

    def test_rebuild_reports_uv_failures_and_success_with_the_pinned_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = Path(tmp)
            with patch.object(metadata_amane, "find_uv", return_value=Path("uv.exe")):
                failed = metadata_amane.rebuild(tools, runner=lambda *a, **k: completed("", 2, "boom"))
                self.assertFalse(failed["ok"])
                self.assertIn("boom", failed["error"])
                python = metadata_amane.bridge_python(tools)

                def build(*args, **kwargs):
                    python.parent.mkdir(parents=True, exist_ok=True)
                    python.write_text("", encoding="utf-8")
                    return completed("")

                done = metadata_amane.rebuild(tools, runner=build)
            self.assertTrue(done["ok"])
            self.assertTrue(done["installed"])
            self.assertIn(metadata_amane.pinned_revision()[:12], done["result"])
            with patch.object(metadata_amane, "find_uv", return_value=None):
                self.assertIn("uv", metadata_amane.rebuild(tools, runner=lambda *a, **k: completed(""))["error"])

    def test_describe_lists_the_facts_the_settings_card_shows(self):
        with tempfile.TemporaryDirectory() as tmp:
            facts = metadata_amane.describe(Path(tmp))
        self.assertEqual(set(facts), {"repository", "license", "revision", "version", "installed", "installed_version", "python", "sites"})
        self.assertFalse(facts["installed"])
        self.assertEqual(facts["installed_version"], "")
        self.assertEqual([site["source"] for site in facts["sites"]], list(metadata_amane.SITES))

    def test_installed_version_reads_the_isolated_package_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = Path(tmp)
            package = metadata_amane.bridge_home(tools) / ".venv" / "Lib" / "site-packages" / "amane-0.17.0.dist-info"
            package.mkdir(parents=True)
            (package / "METADATA").write_text("Metadata-Version: 2.1\nName: amane\nVersion: 0.17.0\n", encoding="utf-8")
            self.assertEqual(metadata_amane.describe(tools)["installed_version"], "0.17.0")
    def test_the_upstream_tag_is_never_guessed(self):
        class Client:
            def __init__(self, status, body):
                self.status, self.body = status, body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get(self, url):
                import httpx
                return httpx.Response(self.status, json=self.body, request=httpx.Request("GET", url))

        with patch("httpx.Client", lambda **kw: Client(200, {"tag_name": "v0.17.0"})):
            self.assertEqual(metadata_amane.latest_upstream_tag({"trust_env": False}), "v0.17.0")
        with patch("httpx.Client", lambda **kw: Client(403, {"message": "rate limited"})):
            self.assertEqual(metadata_amane.latest_upstream_tag({"trust_env": False}), "未取得")

        def explode(**kw):
            raise OSError("no network")

        with patch("httpx.Client", explode):
            self.assertEqual(metadata_amane.latest_upstream_tag({"trust_env": False}), "未取得")


class ProxyArgumentTests(unittest.TestCase):
    def test_the_three_proxy_modes_map_onto_the_bridge_flag(self):
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://env:1", "NO_PROXY": "x"}, clear=False):
            args, env = metadata_amane.proxy_argument({"trust_env": False, "proxy": "http://peach:2"})
            self.assertEqual(args, ["--proxy", "http://peach:2"])
            args, env = metadata_amane.proxy_argument({"trust_env": False})
            self.assertEqual(args, [])
            self.assertNotIn("HTTPS_PROXY", env)
            self.assertNotIn("NO_PROXY", env)
            args, env = metadata_amane.proxy_argument({"trust_env": True})
            self.assertEqual(args, ["--proxy", "http://env:1"])
            self.assertNotIn("HTTPS_PROXY", env)


class BridgeQueryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.tools = Path(self.temporary.name)
        self.python = metadata_amane.bridge_python(self.tools)
        self.python.parent.mkdir(parents=True)
        self.python.write_text("", encoding="utf-8")

    def test_create_refuses_when_the_venv_is_missing(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(MetadataProviderError) as caught:
                metadata_amane.AmaneBridge.create(Path(empty))
        self.assertIn("amane 桥未安装", str(caught.exception))

    def test_query_runs_the_pinned_interpreter_and_reads_the_last_json_line(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return completed("noise from a stray print\n" + report(fc2club=FOUND) + "\n")

        bridge = metadata_amane.AmaneBridge.create(self.tools, runner=runner)
        result = bridge.query("fc2-ppv-1234567", ["fc2club"], proxy_options={"trust_env": False, "proxy": "http://p:1"},
                              timeout=42)
        self.assertEqual(result["sites"]["fc2club"]["status"], "found")
        command, kwargs = calls[0]
        self.assertEqual(command[0], str(self.python))
        self.assertEqual(command[command.index("--number") + 1], "FC2-PPV-1234567")
        self.assertEqual(command[command.index("--sites") + 1], "fc2club")
        self.assertEqual(command[command.index("--proxy") + 1], "http://p:1")
        self.assertEqual(kwargs["timeout"], 42)
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["env"]["PYTHONIOENCODING"], "utf-8")

    def test_query_rejects_sites_peach_did_not_open(self):
        bridge = metadata_amane.AmaneBridge.create(self.tools, runner=lambda *a, **k: completed(""))
        with self.assertRaises(ValueError):
            bridge.query("ABW-220", ["javdb"])

    def test_timeouts_and_garbage_output_become_provider_errors(self):
        def slow(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        bridge = metadata_amane.AmaneBridge.create(self.tools, runner=slow)
        with self.assertRaises(MetadataProviderError) as caught:
            bridge.query("ABW-220", ["avsox"])
        self.assertEqual(caught.exception.kind, "unavailable")
        bridge = metadata_amane.AmaneBridge.create(self.tools, runner=lambda *a, **k: completed("Traceback…", 1))
        with self.assertRaises(MetadataProviderError) as caught:
            bridge.query("ABW-220", ["avsox"])
        self.assertIn("非 JSON", str(caught.exception))
        bridge = metadata_amane.AmaneBridge.create(
            self.tools, runner=lambda *a, **k: completed(json.dumps({"error": "amane 加载失败：X"}), 3))
        with self.assertRaises(MetadataProviderError) as caught:
            bridge.query("ABW-220", ["avsox"])
        self.assertIn("amane 加载失败", str(caught.exception))


class PayloadTests(unittest.TestCase):
    def test_amane_metadata_lands_in_the_javinizer_shaped_payload(self):
        payload = metadata_amane.to_payload("fc2club", "FC2-PPV-1234567", FOUND["metadata"])
        self.assertEqual(payload["source"], "fc2club")
        self.assertEqual(payload["content_id"], "FC2-PPV-1234567")
        self.assertEqual(payload["id"], "FC2-PPV-1234567")
        self.assertEqual(payload["maker"], "卖家")
        self.assertEqual(payload["label"], "レーベル")
        self.assertEqual(payload["actresses"], [{"japanese_name": "女優"}])
        self.assertEqual(payload["cover_url"], "https://x/t.jpg")
        self.assertEqual(payload["poster_url"], "https://x/p.jpg")
        self.assertEqual(payload["director"], "監督")
        self.assertEqual(payload["genres"], ["a", "b"])
        fields = extract_peach_fields(payload)
        self.assertEqual(fields["title"]["value"], "標題")
        self.assertEqual([row["name"] for row in fields["performers"]["value"]], ["女優"])
        self.assertEqual(fields["studio"]["value"], "卖家")
        self.assertEqual(fields["release_date"]["value"], "2024-01-02")

    def test_the_bridge_payload_is_the_contract_record_projected_field_for_field(self):
        """桥的一站先套进 `SiteRecord`，再投影成快照那份 dict；每个键的值逐个钉住。

        `cover_urls` 收 amane 的整列 `thumb_urls`，`cover_url` 仍是第一张；其余键与自写站的
        `payload()` 同名同义，只有 amane 才给的几个键（`poster_url`、`raw`……）经 `extra` 原样带出。
        """
        record = metadata_amane.to_record("fc2club", FOUND["metadata"])
        self.assertIsInstance(record, SiteRecord)
        self.assertEqual((record.source, record.provenance, record.code), ("fc2club", "amane-fc2club", "FC2-PPV-1234567"))
        self.assertEqual(record.performers, ({"japanese_name": "女優"},))
        self.assertEqual(record.tags, ("a", "b"))
        payload = metadata_amane.to_payload("fc2club", "FC2-PPV-1234567", FOUND["metadata"])
        self.assertEqual(payload.pop("raw"), FOUND["metadata"])
        self.assertEqual(payload, {
            "id": "FC2-PPV-1234567", "content_id": "FC2-PPV-1234567", "source": "fc2club",
            "source_url": "https://fc2club.top/html/FC2-PPV-1234567.html", "title": "標題",
            "maker": "卖家", "label": "レーベル", "series": "", "release_date": "2024-01-02", "runtime": 61,
            "director": "監督", "cover_url": "https://x/t.jpg", "cover_urls": ["https://x/t.jpg"],
            "poster_url": "https://x/p.jpg", "screenshot_urls": ["https://x/1.jpg"], "trailer_url": "",
            "actresses": [{"japanese_name": "女優"}], "genres": ["a", "b"], "plot": "",
        })

    def test_split_report_keeps_hits_that_identify_the_code_and_files_the_rest(self):
        wrong = {"status": "found", "metadata": {**FOUND["metadata"], "number": "FC2-PPV-7654321", "source_url": "https://freejavbt.com/FC2-PPV-7654321"}}
        found, failures = metadata_amane.split_report("FC2-PPV-1234567", json.loads(report(
            fc2club=FOUND, freejavbt=wrong,
            avsox={"status": "failed", "reason": "rate_limited", "http_status": 429},
            airav={"status": "not_found", "reason": "not_found"})))
        self.assertEqual([site for site, _ in found], ["fc2club"])
        self.assertEqual(set(failures), {"freejavbt", "avsox", "airav"})
        self.assertEqual(failures["freejavbt"].kind, "not_found")
        self.assertEqual(failures["avsox"].kind, "unavailable")
        self.assertEqual(failures["avsox"].detail, "rate_limited")
        self.assertEqual(failures["avsox"].status_code, 429)
        self.assertEqual(failures["airav"].kind, "not_found")

    def test_prestige_release_is_a_delivery_date_not_a_release_date_candidate(self):
        """Prestige 回的是 MGS 配信开始日（ABW-032 回 2020-11-11，发行日 2020-12-11）。"""
        metadata = {**FOUND["metadata"], "number": "ABW-032", "release": "2020-11-11",
                    "source_url": "https://www.prestige-av.com/goods/x"}
        payload = metadata_amane.to_payload("prestige", "ABW-032", metadata)
        self.assertEqual((payload["release_date"], payload["delivery_date"]), ("", "2020-11-11"))
        self.assertNotIn("release_date", {field for field, row in extract_peach_fields(payload).items()
                                          if row["value"]})
        makers = metadata_amane.to_payload("makers", "ABW-032", metadata)
        self.assertEqual(makers["release_date"], "2020-11-11")
        self.assertNotIn("delivery_date", makers)

    def test_official_hits_keep_their_own_provenance(self):
        record = metadata_amane.to_record("makers", {**FOUND["metadata"], "number": "SSIS-057"})
        self.assertEqual((record.source, record.provenance), ("makers", "amane-makers"))


class FailureMappingTests(unittest.TestCase):
    def test_reasons_map_onto_the_three_existing_kinds_and_keep_the_detail(self):
        for reason, translated in metadata_amane.AMANE_REASONS.items():
            with self.subTest(reason=reason):
                failure = metadata_amane.site_failure("avsox", {"reason": reason, "http_status": 0})
                self.assertEqual((failure.reason, failure.detail), (translated, reason))
                error = metadata_amane.failure_error("avsox", {"reason": reason, "http_status": 0})
                self.assertEqual(error.kind, REASON_KINDS[translated])
                self.assertEqual(error.detail, reason)
                self.assertIn("AVSOX", str(error))
        unknown = metadata_amane.failure_error("avsox", {"reason": "something_new"})
        self.assertEqual((unknown.kind, unknown.detail, unknown.retryable), ("unavailable", "something_new", True))
        auth = metadata_amane.failure_error("airav", {"reason": "cloudflare_blocked", "http_status": 403})
        self.assertFalse(auth.retryable)
        self.assertTrue(auth.temporary)
        parse = metadata_amane.failure_error("airav", {"reason": "parse_error", "detail": "selector missing"})
        self.assertFalse(parse.retryable)
        self.assertIn("selector missing", str(parse))
        timeout = metadata_amane.failure_error("airav", {"reason": "timeout", "detail": "curl 28"})
        self.assertTrue(timeout.retryable)
        self.assertNotIn("curl 28", str(timeout))

    def test_only_blocks_and_rate_limits_pause_the_whole_site(self):
        self.assertEqual(metadata_amane.cooldown_action(
            metadata_amane.failure_error("avsox", {"reason": "ip_banned"})), "blocked")
        self.assertEqual(metadata_amane.cooldown_action(
            metadata_amane.failure_error("avsox", {"reason": "rate_limited"})), "rate_limited")
        for reason in ("not_found", "timeout", "server_error", "parse_error", "age_verification"):
            self.assertEqual(metadata_amane.cooldown_action(
                metadata_amane.failure_error("avsox", {"reason": reason})), "")
        self.assertEqual(metadata_amane.cooldown_action(MetadataProviderError("plain")), "")

    def test_a_region_lock_or_a_bare_403_pauses_the_site_but_is_not_a_miss(self):
        """被挡与没有要分开：地区限制和裸 403 让整站冷却，不冻进「没有」的记忆。"""
        for record in ({"reason": "geo_restricted"}, {"reason": "http_error", "http_status": 403}):
            with self.subTest(record=record):
                error = metadata_amane.failure_error("prestige", record)
                self.assertEqual(error.kind, "auth")
                self.assertEqual(metadata_amane.cooldown_action(error), "blocked")
        refused = metadata_amane.failure_error("prestige", {"reason": "http_error", "http_status": 403})
        self.assertIn("HTTP 403", str(refused))
        self.assertEqual(metadata_amane.cooldown_action(
            metadata_amane.failure_error("prestige", {"reason": "http_error", "http_status": 500})), "")
        missing = metadata_amane.failure_error("prestige", {"reason": "not_found", "http_status": 404})
        self.assertEqual((missing.kind, metadata_amane.cooldown_action(missing)), ("not_found", ""))


class CooldownHelperTests(unittest.TestCase):
    def test_pause_source_uses_the_existing_two_tiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(paused_until(root, "avsox"), 0.0)
            first = pause_source(root, "avsox", refused=True)
            self.assertAlmostEqual(first - time.time(), 900, delta=5)
            second = pause_source(root, "avsox", refused=True)
            self.assertAlmostEqual(second - time.time(), 1800, delta=5)
            self.assertEqual(cooldown_state(root, "avsox")[1], 2)
            self.assertGreater(paused_until(root, "avsox"), time.time())
            limited = pause_source(root, "airav", retry_after=30)
            self.assertAlmostEqual(limited - time.time(), 30, delta=5)
            self.assertEqual(cooldown_state(root, "airav")[1], 0)


class ProviderStageTests(unittest.TestCase):
    """`LibraryMetadataProvider.amane()`：跳过冷却中的站、按桥的报告分三种结局、把封禁写回冷却。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.secrets, self.tools = root / "secrets", root / "tools"
        self.secrets.mkdir()
        python = metadata_amane.bridge_python(self.tools)
        python.parent.mkdir(parents=True)
        python.write_text("", encoding="utf-8")
        self.calls = []

    def provider(self, stdout, returncode=0):
        def runner(command, **kwargs):
            self.calls.append(command)
            return completed(stdout, returncode)

        provider = LibraryMetadataProvider(self.secrets, tools_root=self.tools)
        self.addCleanup(provider.transport.close)
        patcher = patch.object(metadata_amane.AmaneBridge, "create",
                               classmethod(lambda cls, tools_root, **kw: metadata_amane.AmaneBridge(
                                   metadata_amane.bridge_python(tools_root), runner=runner)))
        patcher.start()
        self.addCleanup(patcher.stop)
        return provider

    def test_a_hit_comes_back_as_source_payload_pairs_and_is_cached(self):
        provider = self.provider(report(fc2club=FOUND, avsox={"status": "not_found", "reason": "not_found"}))
        found = provider.amane("FC2-PPV-1234567", route=("fc2club", "avsox"))
        self.assertEqual([site for site, _ in found], ["fc2club"])
        self.assertEqual(found[0][1]["title"], "標題")
        provider.amane("FC2-PPV-1234567", route=("fc2club", "avsox"))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][self.calls[0].index("--sites") + 1], "fc2club,avsox")

    def test_site_network_settings_reach_the_bridge_and_parsed_state(self):
        from peach.scraping_access import save, source_checks
        save(self.secrets, "fc2club", {"network": "direct"})
        provider = self.provider(report(fc2club=FOUND, avsox={"status": "not_found", "reason": "not_found"}))
        provider.amane("FC2-PPV-1234567", route=("fc2club", "avsox"))
        command = self.calls[0]
        self.assertEqual(command[command.index("--direct-sites") + 1], "fc2club")
        self.assertTrue(source_checks(self.secrets, "fc2club")["metadata"]["ok"])
        self.assertEqual(source_checks(self.secrets, "avsox")["metadata"]["reason"], "not_found")

    def test_all_misses_are_not_found_and_errors_are_unavailable_with_the_reason(self):
        provider = self.provider(report(fc2club={"status": "not_found", "reason": "not_found"},
                                        avsox={"status": "not_found", "reason": "not_found"}))
        with self.assertRaises(NotFound):
            provider.amane("FC2-PPV-1234567", route=("fc2club", "avsox"))
        provider = self.provider(report(fc2club={"status": "failed", "reason": "server_error", "http_status": 526},
                                        avsox={"status": "not_found", "reason": "not_found"}))
        with self.assertRaises(Unavailable) as caught:
            provider.amane("FC2-PPV-7654321", route=("fc2club", "avsox"))
        self.assertIn("FC2Club", str(caught.exception))
        self.assertIn("526", str(caught.exception))

    def test_malformed_reports_are_unavailable_and_diagnostics_preserve_hits(self):
        for value in (None, [], {}):
            with self.subTest(value=value):
                provider = self.provider(json.dumps({'sites': value}))
                with self.assertRaises(Unavailable):
                    provider.amane('FC2-PPV-1234567', route=('fc2club',))
        hit = {**FOUND, 'http_status': 'invalid', 'elapsed_s': 'invalid'}
        provider = self.provider(report(fc2club=hit, javdb=FOUND))
        found = provider.amane('FC2-PPV-1234567', route=('fc2club',))
        self.assertEqual([source for source, _ in found], ['fc2club'])

    def test_a_block_pauses_that_site_and_a_fully_paused_stage_is_reported_as_paused(self):
        provider = self.provider(report(avsox={"status": "failed", "reason": "cloudflare_blocked", "http_status": 403}))
        with self.assertRaises(SourcePaused):
            provider.amane("HEYZO-1380", route=("avsox",))
        self.assertGreater(paused_until(self.secrets, "avsox"), time.time())
        # 站已在冷却：下一部片连子进程都不起。
        self.calls.clear()
        provider = self.provider(report(avsox=FOUND))
        with self.assertRaises(SourcePaused):
            provider.amane("HEYZO-1381", route=("avsox",))
        self.assertEqual(self.calls, [])

    def test_a_missing_venv_is_an_unavailable_stage_not_a_crash(self):
        with tempfile.TemporaryDirectory() as empty:
            provider = LibraryMetadataProvider(self.secrets, tools_root=Path(empty))
            self.addCleanup(provider.transport.close)
            with self.assertRaises(Unavailable) as caught:
                provider.amane("HEYZO-1380", route=("avsox",))
        self.assertIn("amane 桥未安装", str(caught.exception))

    def test_a_route_without_bridge_sites_is_not_found_without_a_subprocess(self):
        provider = self.provider(report())
        with self.assertRaises(NotFound):
            provider.amane("ABW-220", route=("javdb",))
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
