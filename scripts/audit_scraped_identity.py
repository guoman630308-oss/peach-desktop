r"""已落库的刮削归属，来源交回的作品认不认得出所查番号（只读，只出复核 CSV）。

`metadata.identifies_code` 在候选落库处核验来源身份；它上线前落下的归属没人回头查过。
实测样例：目录名 `WX17` 被规范成 `WX-017` 去问 javbus，来源交回 `WXSD-017`，片名和标签
落到了 266 个无关视频上。本脚本按归属里留下的 `provider_id`／`content_id`／`source_url`
用同一道闸重判，分三档：

- `错配`：来源交回的是另一部作品（`WX-017` → `WXSD-017`）。
- `前缀未证实`：去掉三位数字前缀就对得上（`476MLA-234` → `MLA-234`）。数字前缀属于作品
  身份（`390JAC-040` 配信与 `JAC-040` DVD 合集是两个商品，见 `docs/SOURCING.md`），这一档
  只说明认不出，不说明错；账本里的标题、日期往往就取自同一份快照，不能拿来互证。
- `来源无编号`：来源没给 id，只有 `source_url`，缺证据不是反证，只计数不进 CSV。

复核 CSV 每行一组（资产 code、复核番号、来源编号），写库另走 `revert_misread_code.py`
或 `peach-ledger-write` 的单独授权。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach.config import DATABASE_PATH, GENERATED_DIR  # noqa: E402
from peach.metadata import identifies_code  # noqa: E402
from peach.review_csv import write_rows  # noqa: E402
from peach.scripting import open_readonly  # noqa: E402

MISMATCH, PREFIX, NO_ID = "错配", "前缀未证实", "来源无编号"
FIELDS = ("tier", "asset_code", "review_code", "provider_id", "content_id", "source_url",
          "asset_count", "link_count", "sources", "asset_ids", "sample_path")
_MAKER_PREFIX = re.compile(r"^\d{3}(?=[A-Z])")


def classify(code: str, payload: dict) -> str | None:
    """认得出返回 None；认不出按档位返回。"""
    if identifies_code(code, payload):
        return None
    stripped = _MAKER_PREFIX.sub("", code.upper())
    if stripped != code.upper() and identifies_code(stripped, payload):
        return PREFIX
    if not (payload.get("id") or payload.get("content_id")):
        return NO_ID
    return MISMATCH


def collect(connection) -> tuple[list[dict], Counter]:
    groups: dict[tuple, dict] = {}
    tiers: Counter = Counter()
    for asset_id, code, path, source, meta in connection.execute(
            "SELECT a.id,a.code,a.path,ae.source,ae.metadata_json FROM asset a "
            "JOIN asset_entity ae ON ae.asset_id=a.id "
            "WHERE ae.source LIKE 'javinizer:%' AND trim(COALESCE(a.code,''))<>'' ORDER BY a.id"):
        data = json.loads(meta or "{}")
        payload = {"id": data.get("provider_id"), "content_id": data.get("content_id"),
                   "source_url": data.get("source_url")}
        if not any(payload.values()):
            continue
        review = str(data.get("review_item") or "").split(":", 1)[0]
        review = code if review in {"", "asset"} else review
        tier = classify(review, payload)
        if tier is None:
            continue
        tiers[tier] += 1
        group = groups.setdefault((tier, code, review), {
            "tier": tier, "asset_code": code, "review_code": review, "provider_id": "",
            "content_id": "", "source_url": "", "assets": set(), "links": 0,
            "sources": set(), "sample_path": path})
        for field, value in (("provider_id", payload["id"]), ("content_id", payload["content_id"]),
                             ("source_url", payload["source_url"])):
            group[field] = group[field] or value or ""
        group["assets"].add(asset_id)
        group["links"] += 1
        group["sources"].add(source)
    rows = [{**{k: v for k, v in group.items() if k not in {"assets", "links", "sources"}},
             "asset_count": len(group["assets"]), "link_count": group["links"],
             "sources": " ".join(sorted(group["sources"])),
             "asset_ids": " ".join(map(str, sorted(group["assets"])))}
            for group in groups.values() if group["tier"] != NO_ID]
    rows.sort(key=lambda row: (row["tier"] != MISMATCH, -row["asset_count"], row["asset_code"]))
    return rows, tiers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="重判已落库刮削归属的来源身份；只读，只出复核 CSV")
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--review-csv", type=Path,
                        default=GENERATED_DIR / "scraped-identity-review.csv")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    connection = open_readonly(args.db)
    try:
        rows, tiers = collect(connection)
    finally:
        connection.close()
    print("认不出所查番号的归属行：" + (
        "，".join(f"{tier} {tiers[tier]}" for tier in (MISMATCH, PREFIX, NO_ID)) or "无"))
    for row in rows:
        print(f"  {row['tier']} {row['asset_code']} 查 {row['review_code']} → "
              f"{row['provider_id'] or row['content_id']}：资产 {row['asset_count']} 条")
    write_rows(args.review_csv, FIELDS, rows)
    print(f"复核 CSV → {args.review_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
