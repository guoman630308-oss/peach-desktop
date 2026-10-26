"""Pure FC2 cross-number similarity evidence; never mutates release identity."""
from __future__ import annotations

import re
from collections import defaultdict
from itertools import combinations
from typing import Iterable, Mapping

from .catalog_rules import DUPLICATE_FLOOR_SECONDS, DUPLICATE_TOLERANCE, part_marker


POLICY_VERSION = "fc2-similarity-policy-v1"
VIDEO_ID = re.compile(r"\d{6,8}")


def fc2_video_id(value: object) -> str:
    match = VIDEO_ID.search(str(value or ""))
    return match.group(0) if match else ""


def stable_pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))


def performer_names(row: Mapping[str, object] | None) -> set[str]:
    return {name for name in str((row or {}).get("performers") or "").split() if name}


def comment_pairs(harvest: Mapping[str, Mapping[str, object]]) -> dict[tuple[str, str], dict]:
    pairs: dict[tuple[str, str], dict] = {}
    for video_id, row in harvest.items():
        for equivalent in str(row.get("equivalents") or "").split():
            other = fc2_video_id(equivalent)
            if not other or other == video_id:
                continue
            pair = stable_pair(video_id, other)
            slot = pairs.setdefault(pair, {"seen_on": set(), "assertions": set()})
            slot["assertions"].add(video_id)
            slot["seen_on"].update(str(row.get("seen_on") or "").split())
    return pairs


def group_assets(assets: Iterable[Mapping[str, object]]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for raw in assets:
        row = dict(raw)
        video_id = fc2_video_id(row.get("code"))
        if video_id:
            grouped[video_id].append(row)
    return dict(grouped)


def _duration_close(left: float, right: float) -> bool:
    return abs(left - right) <= max(
        DUPLICATE_FLOOR_SECONDS,
        min(left, right) * DUPLICATE_TOLERANCE,
    )


def media_evidence(left_assets: list[dict], right_assets: list[dict]) -> dict:
    """Return the strongest local-media comparison for one cross-number pair."""
    best: dict = {}
    best_score = -1
    for left in left_assets:
        for right in right_assets:
            left_hash = str(left.get("hash") or "")
            right_hash = str(right.get("hash") or "")
            exact_hash = bool(left_hash and right_hash and left_hash == right_hash)
            left_duration = float(left.get("duration") or 0)
            right_duration = float(right.get("duration") or 0)
            duration_close = bool(
                left_duration > 0
                and right_duration > 0
                and _duration_close(left_duration, right_duration)
            )
            left_size = int(left.get("size") or 0)
            right_size = int(right.get("size") or 0)
            size_delta = (
                abs(left_size - right_size) / max(left_size, right_size)
                if left_size > 0 and right_size > 0 else None
            )
            size_close = size_delta is not None and size_delta <= 0.03
            dimensions_known = all(
                int(row.get(field) or 0) > 0
                for row in (left, right) for field in ("width", "height")
            )
            resolution_match = bool(
                dimensions_known
                and int(left["width"]) == int(right["width"])
                and int(left["height"]) == int(right["height"])
            )
            left_part = part_marker(str(left.get("name") or ""))
            right_part = part_marker(str(right.get("name") or ""))
            part_conflict = bool(left_part and right_part and left_part != right_part)
            score = (
                100 if exact_hash else 0
            ) + (10 if duration_close else 0) + (5 if size_close else 0) + (
                3 if resolution_match else 0
            ) - (50 if part_conflict else 0)
            if score <= best_score:
                continue
            best_score = score
            best = {
                "left_asset_id": left.get("id", ""),
                "right_asset_id": right.get("id", ""),
                "exact_hash": exact_hash,
                "duration_close": duration_close,
                "duration_delta_seconds": (
                    round(abs(left_duration - right_duration), 3)
                    if left_duration > 0 and right_duration > 0 else ""
                ),
                "size_close": size_close,
                "size_delta_percent": (
                    round(size_delta * 100, 3) if size_delta is not None else ""
                ),
                "resolution_match": resolution_match,
                "part_conflict": part_conflict,
            }
    return best


def _candidate(
    pair: tuple[str, str], assets: Mapping[str, list[dict]],
    harvest: Mapping[str, Mapping[str, object]], comment: Mapping[str, object] | None,
    collections: set[str], inferred: bool,
) -> dict:
    left, right = pair
    # Put a locally owned ID first for review context while keeping pair_key sorted.
    if not assets.get(left) and assets.get(right):
        left, right = right, left
    local_evidence = media_evidence(assets.get(left, []), assets.get(right, []))
    shared = sorted(performer_names(harvest.get(left)) & performer_names(harvest.get(right)))
    kinds = []
    if comment is not None:
        kinds.append("comment_equivalent")
    if local_evidence.get("exact_hash"):
        kinds.append("exact_hash")
    elif all(local_evidence.get(key) for key in (
        "duration_close", "size_close", "resolution_match",
    )):
        kinds.append("media_similarity")
    warnings = []
    if comment is not None:
        warnings.append("匿名评论等价标记只作候选")
    if left in collections or right in collections:
        warnings.append("涉及合集，只能复核分片关系，不能整体合并")
    if not assets.get(left) or not assets.get(right):
        warnings.append("其中一个番号尚不在本地 ledger")
    if local_evidence.get("part_conflict"):
        warnings.append("本地文件分片标记不同")
    confidence = 1.0 if local_evidence.get("exact_hash") else (
        0.9 if comment is not None and "media_similarity" in kinds else
        0.72 if comment is not None else 0.65
    )
    left_code = str((assets.get(left) or [{}])[0].get("code") or f"FC2-PPV-{left}")
    right_code = str((assets.get(right) or [{}])[0].get("code") or f"FC2-PPV-{right}")
    return {
        "pair_key": "|".join(pair),
        "code": left_code,
        "left_video_id": left,
        "right_video_id": right,
        "left_code": left_code,
        "right_code": right_code,
        "left_owned": "1" if assets.get(left) else "",
        "right_owned": "1" if assets.get(right) else "",
        "evidence_kinds": " ".join(kinds),
        "comment_seen_on": " ".join(sorted((comment or {}).get("seen_on", set()))),
        "comment_assertions": len((comment or {}).get("assertions", set())),
        "shared_performers": " ".join(shared),
        **local_evidence,
        "confidence": confidence,
        "warnings": "；".join(warnings),
        "reason": (
            f"{left_code} ↔ {right_code}；证据：" + "、".join(kinds)
        ),
        "policy_version": POLICY_VERSION,
        "status": "candidate",
        "inferred": "1" if inferred else "",
    }


def build_candidates(
    raw_assets: Iterable[Mapping[str, object]],
    harvest: Mapping[str, Mapping[str, object]],
    collections: set[str] | None = None,
) -> list[dict]:
    assets = group_assets(raw_assets)
    collections = collections or set()
    comments = comment_pairs(harvest)
    candidates: dict[tuple[str, str], dict] = {}
    # Comment evidence is retained when at least one side is locally owned.
    for pair, comment in comments.items():
        if assets.get(pair[0]) or assets.get(pair[1]):
            candidates[pair] = _candidate(
                pair, assets, harvest, comment, collections, inferred=False,
            )

    # Mechanical inference is intentionally narrow: exact hash, or close media facts plus
    # an independently harvested performer overlap. It never writes or merges anything.
    for left, right in combinations(sorted(assets), 2):
        pair = stable_pair(left, right)
        if pair in candidates:
            continue
        evidence = media_evidence(assets[left], assets[right])
        shared = performer_names(harvest.get(left)) & performer_names(harvest.get(right))
        strong_media = all(evidence.get(key) for key in (
            "duration_close", "size_close", "resolution_match",
        )) and not evidence.get("part_conflict")
        if evidence.get("exact_hash") or (strong_media and shared):
            candidates[pair] = _candidate(
                pair, assets, harvest, None, collections, inferred=True,
            )
    return sorted(candidates.values(), key=lambda row: (
        -float(row["confidence"]), row["pair_key"],
    ))
