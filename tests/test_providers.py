import unittest
import json

from peach.providers import (
    OpenCodeGoClient, ProviderCapabilities, ProviderKind, ProviderRegistry,
    ProviderStatus, ProviderUnavailable, default_registry,
)


class ProviderRegistryTests(unittest.TestCase):
    def test_default_health_is_passive_and_deterministic(self):
        registry = default_registry(lambda name: f"C:/tools/{name}.exe" if name == "codex" else None)
        health = registry.health()
        by_id = {item["id"]: item for item in health["providers"]}

        self.assertEqual(set(by_id), {
            "opencode-go", "codex-local", "claude-code-local-personal",
        })
        self.assertTrue(by_id["opencode-go"]["available"])
        self.assertTrue(by_id["codex-local"]["available"])
        self.assertFalse(by_id["claude-code-local-personal"]["available"])
        self.assertTrue(by_id["claude-code-local-personal"]["experimental"])
        for item in health["providers"]:
            self.assertEqual(set(item), {
                "id", "kind", "provider", "auth_mode", "available", "configured",
                "experimental", "capabilities", "note",
            })
            self.assertNotIn("secret_ref", item)
            self.assertNotIn("executable", item)

    def test_duplicate_provider_id_is_rejected(self):
        registry = ProviderRegistry()
        status = ProviderStatus(
            id="one", kind=ProviderKind.INFERENCE, provider="test",
            auth_mode="none", available=True, configured=True, experimental=False,
            capabilities=ProviderCapabilities(),
        )
        registry.register(status)
        with self.assertRaisesRegex(ValueError, "duplicate provider id"):
            registry.register(status)

    def test_opencode_model_discovery_is_normalized_and_cached(self):
        calls = []

        def transport(request, timeout, max_bytes):
            calls.append((request.url, request.headers.get("Authorization"), timeout, max_bytes))
            from peach.http import HttpResponse
            return HttpResponse(200, {}, json.dumps({"object": "list", "data": [
                {"id": "kimi-k3", "object": "model", "owned_by": "opencode", "extra": "drop"},
                {"missing": "id"},
            ]}).encode())

        client = OpenCodeGoClient(
            "test-secret", transport=transport, cache_ttl=300, timeout=3,
        )
        first = client.list_models()
        second = client.list_models()

        self.assertTrue(client.configured)
        self.assertEqual(first, [{
            "id": "kimi-k3", "object": "model", "owned_by": "opencode",
        }])
        self.assertEqual(second, first)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], (
            "https://opencode.ai/zen/go/v1/models", "Bearer test-secret", 3, 1024 * 1024,
        ))
        self.assertNotIn("test-secret", repr(first))

    def test_opencode_invalid_payload_is_provider_error(self):
        from peach.http import HttpResponse
        client = OpenCodeGoClient(
            transport=lambda *_: HttpResponse(200, {}, b'{"unexpected":true}')
        )
        with self.assertRaises(ProviderUnavailable):
            client.list_models()


if __name__ == "__main__":
    unittest.main()
