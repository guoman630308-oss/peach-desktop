import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from PIL import Image

from peach.avatar_provider import (
    AvatarCandidateCache,
    inspect_avatar,
    mark_duplicate_candidates,
    provenance_now,
)


def image_bytes(image_format: str = "JPEG", size: tuple[int, int] = (600, 800)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (60, 80, 100)).save(buffer, image_format)
    return buffer.getvalue()


class AvatarProviderTests(unittest.TestCase):
    def test_cache_is_content_addressed_and_provenance_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = AvatarCandidateCache(Path(tmp))
            body = image_bytes()
            avatar = inspect_avatar(body)
            self.assertIsNotNone(avatar)
            url = "https://example.invalid/a.jpg"
            object_path = cache.store(url, body, avatar)
            self.assertEqual(cache.lookup(url), body)
            self.assertIn(avatar.sha256, object_path.name)
            provenance = provenance_now(
                entity_id=7, provider="gfriends", source_kind="external_media_library",
                matched_name="甲", name_source="canonical", external_id="7-S1/a.jpg",
                upstream_url=url, width=avatar.width, height=avatar.height,
                mime_type=avatar.mime_type, sha256=avatar.sha256,
                cache_path=str(object_path),
            )
            evidence_path = cache.store_provenance(provenance)
            original = evidence_path.read_bytes()
            cache.store_provenance(replace(provenance, cached_at="2099-01-01T00:00:00Z"))
            self.assertEqual(evidence_path.read_bytes(), original)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(evidence["sha256"], avatar.sha256)
            self.assertEqual(evidence["policy_version"], "performer-avatar-provider-v1")

    def test_cache_hash_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = AvatarCandidateCache(Path(tmp))
            body = image_bytes()
            avatar = inspect_avatar(body)
            url = "https://example.invalid/a.jpg"
            object_path = cache.store(url, body, avatar)
            object_path.write_bytes(b"tampered")
            self.assertIsNone(cache.lookup(url))

    def test_duplicate_gate_keeps_evidence_out_of_review(self):
        digest = "a" * 64
        rows = [
            {"section": "missing", "entity_id": 2, "verdict": "ok", "sha256": digest},
            {"section": "missing", "entity_id": 3, "verdict": "ok", "sha256": digest},
        ]
        mark_duplicate_candidates(rows, {digest: 1})
        self.assertEqual([row["verdict"] for row in rows], ["duplicate", "duplicate"])
        self.assertEqual([row["duplicate_of_entity_id"] for row in rows], [1, 1])


if __name__ == "__main__":
    unittest.main()
