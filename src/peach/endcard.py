"""Pure OCR classification for video opening/end-card evidence."""
from __future__ import annotations

import re
from dataclasses import dataclass


POLICY_VERSION = "video-endcard-policy-v1"
FULL_VERSION = re.compile(
    r"\bfull\s+version\s+(?:is\s+)?available(?:\s+on)?\b",
    re.IGNORECASE,
)
DOMAIN = re.compile(
    r"\b(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9-]{1,62}\.(?:com|net|org|tv|cc|me))"
    r"(?:[/\s]+([a-z0-9_.-]{2,64}))?",
    re.IGNORECASE,
)
HANDLE = re.compile(r"(?<![\w@])@([a-z0-9_]{3,32})\b", re.IGNORECASE)
CREATOR_PLATFORMS = {
    "fansly.com", "onlyfans.com", "fanvue.com", "manyvids.com", "patreon.com",
}


@dataclass(frozen=True)
class EndcardDetection:
    verdict: str
    full_version: bool
    urls: tuple[str, ...]
    handles: tuple[str, ...]
    confidence: float
    reason: str


def normalize_ocr(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def detect_endcard(text: str) -> EndcardDetection:
    normalized = normalize_ocr(text)
    full_version = bool(FULL_VERSION.search(normalized))
    urls = []
    for match in DOMAIN.finditer(normalized):
        domain = match.group(1).lower()
        suffix = (match.group(2) or "").strip("./")
        value = domain + (f"/{suffix}" if suffix else "")
        if value not in urls:
            urls.append(value)
    handles = tuple(dict.fromkeys(match.group(1) for match in HANDLE.finditer(normalized)))
    creator_source = any(url.split("/", 1)[0] in CREATOR_PLATFORMS for url in urls)
    if full_version:
        confidence = 0.98 if creator_source else 0.9
        reason = "片尾明确写有 Full version available"
        if urls:
            reason += "；来源 " + "、".join(urls)
        return EndcardDetection(
            "incomplete_candidate", True, tuple(urls), handles, confidence, reason,
        )
    if urls or handles:
        evidence = [*urls, *(f"@{handle}" for handle in handles)]
        return EndcardDetection(
            "source_evidence", False, tuple(urls), handles, 0.72,
            "画面出现来源/水印：" + "、".join(evidence),
        )
    return EndcardDetection("none", False, (), (), 0.0, "")
