import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from peach.review_mirror import ReviewMirror


LOCAL_EMPTY = {
    "sections": {"creator_tags": []},
    "sources": {"creator_tags": None},
    "counts": {"creator_tags": 0},
}


class FakeResponse:
    def __init__(self, payload):
        self.raw = json.dumps(payload).encode()

    def read(self, _limit):
        return self.raw


class FakeOpener:
    def __init__(self, routes=None, error=None):
        self.routes = routes or {}
        self.error = error
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return FakeResponse(self.routes[request.full_url])


class ReviewMirrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ca = self.root / "ca.crt"
        self.ca.write_text("test ca", encoding="utf-8")
        self.cache = self.root / "review" / "writer-review.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_live_writer_payload_is_validated_rewritten_and_cached(self):
        remote = {
            "sections": {"creator_tags": [{
                "item_key": "one", "preview_url": "/endcard-frame?id=1",
                "comparison_assets": [{"preview_url": "/poster?id=1"}],
            }]},
            "sources": {"creator_tags": "creator-tags-candidate.csv"},
            "counts": {"creator_tags": 1},
        }
        opener = FakeOpener({
            "https://writer.test/healthz": {"ledger_sync": "writer"},
            "https://writer.test/api/review": remote,
        })
        mirror = ReviewMirror(
            "https://writer.test", self.ca, self.cache,
            token="secret", opener=opener, now=lambda: 1000.0,
        )

        result = mirror.resolve(LOCAL_EMPTY)

        self.assertEqual(result["mirror"]["state"], "live")
        row = result["sections"]["creator_tags"][0]
        self.assertEqual(row["preview_url"],
                         "https://writer.test/endcard-frame?id=1")
        self.assertEqual(row["comparison_assets"][0]["preview_url"],
                         "https://writer.test/poster?id=1")
        self.assertTrue(self.cache.is_file())
        self.assertEqual(opener.requests[0][0].get_header("X-token"), "secret")

    def test_cached_writer_payload_survives_a_temporary_network_failure(self):
        live = FakeOpener({
            "https://writer.test/healthz": {"ledger_sync": "writer"},
            "https://writer.test/api/review": {
                "sections": {"creator_tags": [{"item_key": "one"}]},
                "sources": {"creator_tags": "batch.csv"},
                "counts": {"creator_tags": 1},
            },
        })
        ReviewMirror(
            "https://writer.test", self.ca, self.cache,
            opener=live, now=lambda: 1000.0,
        ).resolve(LOCAL_EMPTY)

        failed = ReviewMirror(
            "https://writer.test", self.ca, self.cache,
            opener=FakeOpener(error=OSError("offline")), now=lambda: 2000.0,
        ).resolve(LOCAL_EMPTY)

        self.assertEqual(failed["mirror"]["state"], "cached")
        self.assertIn("offline", failed["mirror"]["error"])
        self.assertEqual(failed["counts"]["creator_tags"], 1)

    def test_a_non_writer_peer_never_replaces_the_local_queue(self):
        opener = FakeOpener({
            "https://writer.test/healthz": {"ledger_sync": "reader"},
        })
        result = ReviewMirror(
            "https://writer.test", self.ca, self.cache,
            opener=opener, now=lambda: 1000.0,
        ).resolve(LOCAL_EMPTY)

        self.assertEqual(result["mirror"]["state"], "unavailable")
        self.assertIn("不是 ledger writer", result["mirror"]["error"])
        self.assertEqual(result["counts"]["creator_tags"], 0)

    def test_a_cache_write_failure_does_not_hide_a_live_writer_response(self):
        blocked_parent = self.root / "not-a-directory"
        blocked_parent.write_text("occupied", encoding="utf-8")
        opener = FakeOpener({
            "https://writer.test/healthz": {"ledger_sync": "writer"},
            "https://writer.test/api/review": {
                "sections": {"creator_tags": [{"item_key": "one"}]},
                "sources": {"creator_tags": "batch.csv"},
                "counts": {"creator_tags": 1},
            },
        })

        result = ReviewMirror(
            "https://writer.test", self.ca, blocked_parent / "writer-review.json",
            opener=opener, now=lambda: 1000.0,
        ).resolve(LOCAL_EMPTY)

        self.assertEqual(result["mirror"]["state"], "live")
        self.assertEqual(result["counts"]["creator_tags"], 1)

    def test_plain_http_is_rejected_instead_of_downgrading_peer_privacy(self):
        result = ReviewMirror(
            "http://writer.test", self.ca, self.cache,
        ).resolve(LOCAL_EMPTY)

        self.assertEqual(result["mirror"]["state"], "unavailable")
        self.assertIn("严格校验", result["mirror"]["error"])

    def test_macos_keychain_ca_is_added_without_replacing_the_file_ca(self):
        keychain = self.root / "login.keychain-db"
        keychain.write_text("keychain", encoding="utf-8")
        exported = "-----BEGIN CERTIFICATE-----\nwriter-ca\n-----END CERTIFICATE-----\n"
        mirror = ReviewMirror(
            "https://writer.test", self.ca, self.cache,
            keychain_paths=(keychain,),
        )

        with patch("peach.review_mirror.subprocess.run", return_value=SimpleNamespace(
            returncode=0, stdout=exported,
        )) as run:
            bundle = mirror._trusted_ca_pem()

        self.assertIn("test ca", bundle)
        self.assertIn("writer-ca", bundle)
        self.assertEqual(run.call_args.args[0][-1], str(keychain))

    def test_macos_system_curl_fallback_keeps_the_token_off_process_arguments(self):
        calls = []

        def curl_runner(args, **kwargs):
            calls.append((args, kwargs))
            payload = ({"ledger_sync": "writer"} if args[-1].endswith("/healthz") else {
                "sections": {"creator_tags": [{"item_key": "one"}]},
                "sources": {"creator_tags": "batch.csv"},
                "counts": {"creator_tags": 1},
            })
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(payload).encode(), stderr=b"",
            )

        result = ReviewMirror(
            "https://writer.test", self.ca, self.cache,
            token="secret", opener=FakeOpener(error=OSError("denied")),
            keychain_paths=(), curl_fallback=True, curl_runner=curl_runner,
            proxy="http://127.0.0.1:7890",
            now=lambda: 1000.0,
        ).resolve(LOCAL_EMPTY)

        self.assertEqual(result["mirror"]["state"], "live")
        self.assertEqual(len(calls), 2)
        for args, kwargs in calls:
            self.assertIn("--cacert", args)
            self.assertIn("--noproxy", args)
            self.assertIn("--proxy", args)
            self.assertIn("http://127.0.0.1:7890", args)
            self.assertNotIn("secret", args)
            self.assertEqual(kwargs["input"], b"X-Token: secret\n")

    def test_only_a_loopback_proxy_can_receive_writer_requests(self):
        local = ReviewMirror(
            "https://writer.test", self.ca, self.cache,
            proxy="socks5h://localhost:7890",
        )
        remote = ReviewMirror(
            "https://writer.test", self.ca, self.cache,
            proxy="http://proxy.example:7890",
        )

        self.assertEqual(local.proxy, "socks5h://localhost:7890")
        self.assertEqual(remote.proxy, "")
