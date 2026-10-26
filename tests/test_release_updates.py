"""发行通道、故障状态和独立托盘检查的隔离回归。"""
import json
import unittest
from unittest.mock import Mock, patch

import httpx

from peach import release_updates
from peach.http import HttpResponse
from peach.versioning import VersionManager


def release(version, **extra):
    return dict(tag_name=f"v{version}", prerelease=True, draft=False,
                assets=[{"name": f"Peach-{version}-windows-x64.zip", "state": "uploaded"}], **extra)


class ReleaseUpdateTests(unittest.TestCase):
    def check(self, rows, status=200):
        transport = Mock(return_value=HttpResponse(status, {}, json.dumps(rows).encode()))
        with patch.object(release_updates, "__version__", "0.9.0"):
            return release_updates.check(transport=transport)

    def test_channel_includes_prereleases_and_orders_versions_numerically(self):
        result = self.check([release("0.9.0"), release("0.10.0")])
        self.assertEqual((result["state"], result["latest_version"]), ("available", "0.10.0"))
        self.assertEqual(result["release_url"], release_updates.RELEASES_URL + "/tag/v0.10.0")

    def test_unpublished_or_incomplete_packages_are_not_offered(self):
        draft = release("8.0.0"); draft["draft"] = True
        incomplete = release("9.0.0"); incomplete["assets"] = []
        result = self.check([draft, incomplete, {"tag_name": "invalid"}, release("0.9.0")])
        self.assertEqual(result["state"], "current")
        self.assertEqual(self.check([release("0.8.0")])["state"], "ahead")
        self.assertEqual(self.check([])["state"], "empty")

    def test_prefers_app_update_when_all_tool_assets_are_uploaded(self):
        row = release("1.0.0")
        row["assets"] += [
            {"name": "Peach-1.0.0-windows-x64-update.zip", "state": "uploaded", "size": 123, "digest": "sha256:" + "a" * 64},
            {"name": "Peach-1.0.0-windows-x64-setup.exe", "state": "uploaded"},
        ]
        self.assertEqual(self.check([row])["package_kind"], "full")
        row["assets"] += [{"name": f"Peach-1.0.0-windows-x64-tools-{tool}.zip", "state": "uploaded"}
                          for tool in ("ffmpeg", "untrunc", "cloudflared")]
        result = self.check([row])
        self.assertEqual(result["package_kind"], "update")
        self.assertTrue(result["asset_url"].endswith("-update.zip"))
        self.assertEqual(result["asset_size"], 123)
        row["assets"][1]["state"] = "new"
        self.assertEqual(self.check([row])["package_kind"], "full")
        row["assets"].pop(0)
        self.assertEqual(self.check([row])["state"], "empty")

    def test_failures_do_not_claim_current_or_keep_a_latest_version(self):
        self.assertEqual(self.check({}, status=429)["state"], "error")
        self.assertEqual(self.check({})["state"], "error")
        for cause in (httpx.ConnectError("offline"), httpx.ReadTimeout("timeout")):
            result = release_updates.check(transport=Mock(side_effect=cause))
            self.assertEqual(result["state"], "error")
            self.assertIsNone(result["latest_version"])

    def test_standalone_tray_checks_releases_without_git(self):
        execute = Mock(side_effect=AssertionError("Git must not run"))
        with patch("peach.distribution.standalone", return_value=True), patch.object(
                release_updates, "check", return_value=dict(state="available", latest_version="1.0.0", message="有新版本可下载。")) as check:
            result = VersionManager(execute=execute).check()
        check.assert_called_once()
        self.assertEqual(result.state, "available")
        self.assertIn("1.0.0", result.message)
        execute.assert_not_called()
