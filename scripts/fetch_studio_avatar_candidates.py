"""把厂牌自己的社交账号头像取成 Logo 候选。

社交头像天然是正方形，而且由品牌本人发布，比搜索引擎的同名结果可信得多——
Wikimedia 搜 `BAZOOKA` 会返回 16:9 的泡泡糖品牌图，这类错误无法靠人工一眼分辨。

handle 默认必须由 `--handles` 显式提供。脚本不猜 handle：猜错会产出一个"看起来很像
官方"的错误 Logo，和它要取代的搜索引擎猜测是同一种失败。`--guess-handles` 只用于
生成待人工确认的候选，产出一律标记 `needs_confirmation`，不得直接采用。

解析链路：handle → unavatar.io 解析出 `pbs.twimg.com` 的真实地址 → 从该地址下载 →
实测尺寸。unavatar 只用来解析地址，图片本身来自平台自己的 CDN，provenance 两者都记。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach.http import HttpRequest, HttpxTransport      # noqa: E402
from peach.images import REJECT, bake_square, classify  # noqa: E402
from peach.logo_provider import (  # noqa: E402
    POLICY_VERSION,
    LogoCandidateCache,
    hash_distance,
    inspect_logo,
    installed_logo_hashes,
    provenance_now,
)
from peach.config import GENERATED_DIR  # noqa: E402
from peach.previews import logo_key  # noqa: E402
from peach.social_links import twimg_tiers  # noqa: E402
from peach.review_csv import read_rows, write_rows
from peach.scripting import USER_AGENT, RateLimiter

RESOLVER = "https://unavatar.io/{platform}/{handle}?json"
FIELDS = (
    "studio", "handle", "platform", "resolver_url", "resolved_url", "width",
    "height", "aspect", "verdict", "saved", "accepted", "confirmation",
    "content_state", "duplicate_of", "sha256", "mime_type", "cache_key",
    "perceptual_hash", "visual_distance", "provenance_key", "policy_version", "reason",
)
HEALTH_FIELDS = (
    "source", "policy_version", "attempted", "no_handle", "resolved",
    "snapshot_reused", "fetched", "succeeded", "unchanged", "changed", "new",
    "duplicates", "rejected", "errors", "bytes_fetched", "elapsed_ms",
    "last_error_kind", "last_error_message",
)


def safe_name(studio: str) -> str:
    """候选图的落盘名，与已装标识同一套规则（`logo_key`）。

    只留 ASCII 的话日文厂牌全折成下划线：`セレブの友` 与 `親父の個撮` 同为 `_____`，
    同一批里后取的那张盖掉先取的，复核页上两家挂着同一张图；已装标识按 `logo_key`
    落盘，拿 ASCII 名去比也永远比不上。
    """
    return logo_key(studio)


def guess_handle(studio: str) -> str:
    """把厂牌名压成一个可能的 handle。只作候选，绝不直接采用。"""
    return re.sub(r"[^A-Za-z0-9_]", "", studio)[:15]


def load_handles(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    return {
        (row.get("studio") or "").strip(): (row.get("handle") or "").strip()
        for row in read_rows(path)
        if (row.get("studio") or "").strip()
    }


def download(http, urls: list[str], timeout: float) -> tuple[bytes, str]:
    """按顺序试地址，第一个回 200 的就用；返回 (字节, 实际用的地址)。

    原图地址不是每个账号都在（旧头像有过只留缩略图的），取不到就退回带尺寸后缀的那一份，
    而不是让整个厂牌变成取图失败。
    """
    last = ""
    for candidate in dict.fromkeys(urls):
        response = http(HttpRequest("GET", candidate, {"User-Agent": USER_AGENT}),
                        timeout, 8 << 20)
        if response.status == 200 and response.body:
            return response.body, candidate
        last = f"HTTP {response.status}"
    raise RuntimeError(last or "没有可用的头像地址")


def resolve(http, platform: str, handle: str, timeout: float) -> str:
    response = http(HttpRequest(
        "GET", RESOLVER.format(platform=platform, handle=handle),
        {"User-Agent": USER_AGENT}), timeout, 1 << 20)
    return str(json.loads(response.body).get("url") or "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True,
                        help="缺 Logo 的厂牌清单，需含 studio 列")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--handles", type=Path,
                        help="studio,handle 映射 CSV；没有它就不解析")
    parser.add_argument("--platform", default="x")
    parser.add_argument("--guess-handles", action="store_true",
                        help="按厂牌名猜 handle，产出一律标记 needs_confirmation")
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--image-dir", type=Path,
                        help="落盘目录；默认放在 output 同级的 studio-logos/")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--cache-dir", type=Path,
        default=GENERATED_DIR / "provider-cache" / "studio-logos" / "social",
    )
    parser.add_argument("--installed-dir", type=Path, default=GENERATED_DIR / "logos")
    parser.add_argument("--health", type=Path)
    parser.add_argument("--refresh", action="store_true")
    return parser


def _atomic_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    write_rows(path, fields, rows, atomic=True, fill_missing=True)


def main(argv: list[str] | None = None, *, transport=None) -> int:
    args = build_parser().parse_args(argv)
    if args.image_dir is None:
        args.image_dir = args.output.parent / "studio-logos"
    if args.health is None:
        args.health = args.output.with_name(args.output.stem + "-health.csv")

    studios = [(row.get("studio") or "").strip()
               for row in read_rows(args.input) if (row.get("studio") or "").strip()]
    if args.limit:
        studios = studios[: args.limit]
    mapping = load_handles(args.handles)

    rows: list[dict[str, object]] = []
    health: dict[str, object] = {
        "source": "studio_social_avatar", "policy_version": POLICY_VERSION,
        "attempted": 0, "no_handle": 0, "resolved": 0, "snapshot_reused": 0,
        "fetched": 0, "succeeded": 0, "unchanged": 0, "changed": 0, "new": 0,
        "duplicates": 0, "rejected": 0, "errors": 0, "bytes_fetched": 0,
        "elapsed_ms": 0, "last_error_kind": "", "last_error_message": "",
    }
    started = time.perf_counter()
    cache = LogoCandidateCache(args.cache_dir)
    installed_by_key, installed_by_hash = installed_logo_hashes(args.installed_dir)
    batch_hashes: dict[str, str] = {}
    owned_http = transport is None
    http = transport or HttpxTransport()
    limiter = RateLimiter(args.interval)
    try:
        for studio in studios:
            health["attempted"] += 1
            handle = mapping.get(studio, "")
            confirmation = "confirmed-handle"
            if not handle and args.guess_handles:
                handle, confirmation = guess_handle(studio), "needs_confirmation"
            if not handle:
                rows.append({"studio": studio, "handle": "", "platform": args.platform,
                             "resolver_url": "", "resolved_url": "", "width": "",
                             "height": "", "aspect": "", "verdict": "", "saved": "",
                             "accepted": False, "policy_version": POLICY_VERSION,
                             "confirmation": "no-handle",
                             "content_state": "no_handle",
                             "reason": "未取得该厂牌的社交 handle"})
                health["no_handle"] += 1
                continue
            limiter.wait()
            resolver_url = RESOLVER.format(platform=args.platform, handle=handle)
            record = {"studio": studio, "handle": handle, "platform": args.platform,
                      "resolver_url": resolver_url, "resolved_url": "", "width": "",
                      "height": "", "aspect": "", "verdict": "", "saved": "",
                      "accepted": False, "confirmation": confirmation,
                      "content_state": "", "duplicate_of": "", "sha256": "",
                      "mime_type": "", "cache_key": "", "perceptual_hash": "",
                      "visual_distance": "", "provenance_key": "",
                      "policy_version": POLICY_VERSION, "reason": ""}
            try:
                url = resolve(http, args.platform, handle, args.timeout)
                if not url:
                    record["reason"] = "解析不到头像地址"
                    record["content_state"] = "empty"
                    rows.append(record)
                    continue
                tiers = twimg_tiers(url)
                url = tiers[0]
                record["resolved_url"] = url
                health["resolved"] += 1
                payload = None if args.refresh else cache.lookup(url)
                if payload is not None:
                    health["snapshot_reused"] += 1
                else:
                    payload, url = download(http, tiers, args.timeout)
                    record["resolved_url"] = url
                    health["fetched"] += 1
                    health["bytes_fetched"] += len(payload)
                source_raster = inspect_logo(payload)
            except Exception as exc:
                record["reason"] = f"取图失败：{type(exc).__name__}"
                record["content_state"] = "error"
                health["errors"] += 1
                health["last_error_kind"] = type(exc).__name__
                health["last_error_message"] = str(exc)[:500]
                rows.append(record)
                continue
            if source_raster is None:
                record["reason"] = "无法解析为图片"
                record["content_state"] = "rejected"
                health["rejected"] += 1
                rows.append(record)
                continue
            object_path = cache.store(url, payload, source_raster)
            width, height = source_raster.width, source_raster.height
            verdict, aspect, reason = classify(width, height)
            if verdict != REJECT:
                # 厂牌标识落盘前一律烤成不透明方图：页面三处取图位都是 cover 的方框。
                # 已经是不透明方图的原字节返回，长条补背景，带透明的配白底。
                baked = bake_square(payload)
                if baked is None:
                    verdict, reason = REJECT, "烤方图失败"
                else:
                    payload = baked
            final_raster = inspect_logo(payload) if verdict != REJECT else None
            saved = ""
            content_state = "rejected"
            duplicate_of = ""
            visual_distance: int | str = ""
            candidate_digest = final_raster.sha256 if final_raster else ""
            if verdict != REJECT:
                args.image_dir.mkdir(parents=True, exist_ok=True)
                suffix = final_raster.extension
                destination = args.image_dir / f"{safe_name(studio)}{suffix}"
                destination.write_bytes(payload)
                saved = destination.name
                installed_signature = installed_by_key.get(safe_name(studio))
                installed_digest = installed_signature[0] if installed_signature else ""
                installed_perceptual = installed_signature[1] if installed_signature else ""
                visual_distance = hash_distance(
                    installed_perceptual, final_raster.perceptual_hash,
                ) if installed_signature else ""
                if (installed_digest == candidate_digest
                        or installed_signature and visual_distance <= 4):
                    content_state = "unchanged"
                elif candidate_digest in installed_by_hash:
                    content_state = "duplicate"
                    duplicate_of = installed_by_hash[candidate_digest]
                elif candidate_digest in batch_hashes:
                    content_state = "duplicate"
                    duplicate_of = batch_hashes[candidate_digest]
                else:
                    content_state = "changed" if installed_digest else "new"
                    batch_hashes[candidate_digest] = studio
            if content_state in {"unchanged", "changed", "new", "rejected"}:
                health[content_state] += 1
            if content_state == "duplicate":
                health["duplicates"] += 1
            accepted = (
                verdict != REJECT and confirmation != "needs_confirmation"
                and content_state in {"new", "changed"}
            )
            provenance = provenance_now(
                studio=studio, handle=handle, platform=args.platform,
                resolver_url=resolver_url, source_url=url, width=width, height=height,
                mime_type=source_raster.mime_type, sha256=source_raster.sha256,
                perceptual_hash=source_raster.perceptual_hash,
                object_name=object_path.name,
            )
            provenance_path = cache.provenance(provenance)
            record.update({
                "width": width, "height": height, "aspect": round(aspect, 3),
                "verdict": verdict, "saved": saved, "accepted": accepted,
                "content_state": content_state, "duplicate_of": duplicate_of,
                "sha256": candidate_digest, "mime_type": final_raster.mime_type if final_raster else "",
                "cache_key": object_path.name,
                "perceptual_hash": final_raster.perceptual_hash if final_raster else "",
                "visual_distance": visual_distance,
                "provenance_key": provenance_path.name,
                "reason": (
                    "与已安装 Logo 内容一致" if content_state == "unchanged"
                    else f"与 {duplicate_of} 内容重复" if content_state == "duplicate"
                    else reason
                ),
            })
            health["succeeded"] += 1
            rows.append(record)
    finally:
        if owned_http:
            http.close()

    rows.sort(key=lambda item: (not item["accepted"], item["aspect"] or 99))
    health["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    _atomic_csv(args.output, FIELDS, rows)
    _atomic_csv(args.health, HEALTH_FIELDS, [health])
    accepted = sum(1 for row in rows if row["accepted"])
    print({"total": len(rows), "accepted": accepted, "output": str(args.output),
           "health": str(args.health)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
