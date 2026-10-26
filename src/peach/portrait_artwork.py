"""从单人作品的本地封面与已有高清帧挑人脸最清楚的一张。"""
from __future__ import annotations

import re
from pathlib import Path

from .avatar_face import face_px_width
from .avatar_provider import AvatarCandidateCache, inspect_avatar


def _passes_size_bar(width: int, height: int) -> bool:
    return ((width == height and min(width, height) >= 400)
            or (max(width, height) >= 500 and min(width, height) >= 300))


def _measure_faces(candidates: list[dict], probe) -> None:
    seen: dict[str, dict | None] = {}
    for candidate in candidates:
        digest = candidate["sha256"]
        if digest not in seen:
            seen[digest] = probe(candidate["object_path"])
        record = seen[digest]
        candidate["face_record"] = record
        candidate["face_width"] = face_px_width(record)


def _quality_key(candidate: dict) -> tuple[int, int, int, int, int]:
    return (int(candidate.get("face_width") or 0),
            min(candidate["width"], candidate["height"]),
            max(candidate["width"], candidate["height"]),
            candidate["width"] * candidate["height"],
            1 if candidate["provider"] == "cover-fallback" else 0)


def cover_fallback(connection, record: dict, cache: AvatarCandidateCache,
                   cover_root: Path, probe=None,
                   poster_root: Path | None = None) -> dict | None:
    """只看非回收站的单人作品；完整封面与已有高清帧共用一把质量尺。"""
    if record["kind"] != "performer":
        return None
    candidates = []
    rows = connection.execute(
        "SELECT DISTINCT a.id,a.code FROM asset a JOIN asset_entity ae ON ae.asset_id=a.id "
        "WHERE ae.entity_id=? AND ae.role='performer' "
        "AND a.disposal IS NULL "
        "AND NOT EXISTS(SELECT 1 FROM asset_entity other WHERE other.asset_id=a.id "
        "AND other.role='performer' AND other.entity_id<>ae.entity_id)",
        (record["entity_id"],),
    )
    for asset_id, code in rows:
        code_text = str(code or "")
        paths: list[tuple[Path, str, str]] = []
        if re.fullmatch(r"[A-Za-z0-9-]+", code_text):
            paths.append((cover_root / f"{code_text}.jpg", "cover-fallback",
                          "single_performer_cover"))
        if poster_root is not None:
            paths.extend((path, "poster-fallback", "single_performer_frame")
                         for path in sorted(poster_root.glob(f"{asset_id}_*.jpg")))
        for path, provider, source_kind in paths:
            if not path.is_file():
                continue
            data = path.read_bytes()
            inspected = inspect_avatar(data)
            if inspected is None or not _passes_size_bar(inspected.width, inspected.height):
                continue
            source = path.resolve().as_uri()
            stored = cache.store(source, data, inspected)
            label = "完整封面" if provider == "cover-fallback" else f"高清帧 {path.stem}"
            candidates.append({
                "provider": provider,
                "source_kind": source_kind,
                "source_url": source,
                "external_id": code_text or str(asset_id),
                "width": inspected.width,
                "height": inspected.height,
                "mime_type": inspected.mime_type,
                "sha256": inspected.sha256,
                "object_path": stored,
                "matched": record["canonical"],
                "name_source": "ledger-performer",
                "identity_verified": False,
                "evidence": f"单人作品 {code_text or asset_id} {label}",
            })
    if not candidates:
        return None
    if probe is not None:
        _measure_faces(candidates, probe)
    return max(candidates, key=_quality_key)
