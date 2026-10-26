#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""从 fc2cmadb 抓 FC2 作品元数据与评论区的人工标记。

FC2 没有 JAV 那样的番号体系，官方页面只有卖家自己填的标题，演员基本不写。
fc2cmadb 的价值不在正文而在**评论区**：那里有用户长期维护的两类标记。

演员标记，`<video_id>　<名前>`，全角空格分隔，一行可以有多个演员：

    2724256　未歩なな　皐月
    1934545　ゆう

等价标记，把同一段内容在不同 video_id 下的发布对应起来：

    3312576-4 = 2471432 = 3090722-1 = 4605413

等价关系是合集的判据，也是本脚本存在的主要理由。`FC2PPV-3312576` 是一个
21 段的合集，本地就按 `FC2PPV-3312576-1.mp4` 分片存着。**合集封面绝不能套给
每个分片**——那会让 21 个不同内容的视频显示同一张图。所以判定为合集时
`cover_url` 留空并在 note 里写明，让分片回落到自己的缩略图。

页面是 Laravel + Inertia，数据在 `<script type="application/json">` 里，
不用解析 HTML。请求带用户在采集设置里贴的 Cookie，按登录用户看到的那一页取。页面地址、props 与评论标记的解析
在站点解析器 `peach.sources.fc2cmadb`，本脚本只管汇总、收获表与复核候选。

评论是匿名用户写的，一律只作候选：产出 CSV 交人工复核，不碰真相字段。
同一个演员名被两条以上独立评论提到时置信度更高，写在 `performer_votes` 里。

**只抓库里有的作品页，但每页评论全量留存。** 一页评论往往给几十个 video_id
标了演员，本地只对上其中两三个；只留对得上的那几条，等于把评论区的价值丢掉。
所以产出三份：按本地资产的候选 CSV（复核页用）、按 video_id 汇总的全量收获
CSV、以及原始评论 JSONL。留原文是因为解析标记的那些正则一定会漏掉某种写法，
有原文就不必为此重爬一遍。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.catalog_rules import normalise_code_key
from peach.config import SECRETS_DIR
from peach.scraping_access import client_for
from peach.genre_decisions import load_genre_decisions
from peach.genre_taxonomy import map_genres
from peach.review_csv import read_rows, write_rows
from peach.scripting import open_readonly
from peach.config import DATABASE_PATH, GENERATED_DIR
from peach.sources.fc2 import canonical_code
from peach.sources.fc2cmadb import (VIDEO_ID, Fc2cmadbSource, collection_parts, inertia_props,
                                    page_comments, parse_equivalences, parse_performers)

FC2_COVER_WIDTH = 1200
#: 判定合集所需的最少分片映射数。一两条可能只是同一段内容的重复投稿。
COLLECTION_MIN_PARTS = 3

METADATA_POLICY_VERSION = "fc2cmadb-article-v1"
METADATA_FIELDS = (
    "item_key", "code", "query", "field", "field_label", "current_value",
    "candidates_json", "source_count", "source_profile", "policy_version",
    "status", "size_gb", "videos", "fetched_at",
)


def high_resolution_cover_url(url: str) -> str:
    """Use FC2's measured 1200px CDN rendition instead of the 276px listing thumb."""
    return re.sub(r"(/w)\d+(/)", rf"\g<1>{FC2_COVER_WIDTH}\2", str(url or ""), count=1)


def article_url(video_id: str) -> str:
    return Fc2cmadbSource().article_url(canonical_code(video_id))


def fetch_article(client: httpx.Client, video_id: str) -> dict:
    response = client.get(article_url(video_id), timeout=30)
    if response.status_code == 404:
        raise LookupError("站上无此作品")
    response.raise_for_status()
    props = inertia_props(response.text)
    if not props:
        raise LookupError("页面没有 Inertia 数据，可能被挡在登录或人机验证外")
    return props


def summarise(video_id: str, props: dict) -> dict:
    article = props.get("article") or {}
    comments = page_comments(props)

    votes: dict[str, int] = {}
    equivalences: list[list[tuple[str, str]]] = []
    for comment in comments:
        body = str(comment.get("body") or "")
        for name in parse_performers(body).get(video_id, []):
            votes[name] = votes.get(name, 0) + 1
        equivalences.extend(parse_equivalences(body, subject=video_id))

    parts = collection_parts(video_id, equivalences)
    is_collection = len(parts) >= COLLECTION_MIN_PARTS
    # 同一段内容在别的 video_id 下的发布，供跨号去重与演员回填使用。
    aliases = sorted({tok[0] for group in equivalences for tok in group
                      if any(t[0] == video_id for t in group) and tok[0] != video_id})

    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
    writer = article.get("writer") or {}
    return {
        "title": article.get("title") or "",
        "release_date": article.get("release_date") or "",
        "duration": article.get("duration") or "",
        "censored": article.get("censored") or "",
        "writer": writer.get("name") or "",
        "writer_slug": writer.get("slug") or "",
        "tags": " ".join(str(t.get("name") or t) for t in (article.get("tags") or [])),
        "performers": " ".join(name for name, _ in ranked),
        "performer_votes": " ".join(f"{name}:{n}" for name, n in ranked),
        "is_collection": "1" if is_collection else "",
        "collection_parts": str(len(parts)) if is_collection else "",
        "equivalents": " ".join(aliases),
        # 合集封面套给每个分片会让 21 个不同内容显示同一张图，所以留空。
        "cover_url": "" if is_collection else high_resolution_cover_url(
            article.get("image_url") or ""
        ),
        "note": (f"合集，{len(parts)} 个分片各自独立，封面不下发" if is_collection
                 else ("FC2 官方页已下架" if article.get("not_found") else "")),
    }


HARVEST_FIELDS = ("video_id", "owned", "performers", "performer_votes",
                  "equivalents", "seen_on")


def harvest(video_id: str, props: dict, into: dict) -> None:
    """把一页评论里**所有** video_id 的标记并进全局收获表。

    本地只对得上其中两三个，其余的先存着：库随时会加片子，重爬一遍页面要几小时。
    """
    for comment in page_comments(props):
        body = str(comment.get("body") or "")
        for other_id, names in parse_performers(body).items():
            slot = into.setdefault(other_id, {"votes": {}, "equiv": set(), "seen": set()})
            for name in names:
                slot["votes"][name] = slot["votes"].get(name, 0) + 1
            slot["seen"].add(video_id)
        for group in parse_equivalences(body, subject=video_id):
            members = {token[0] for token in group}
            for other_id in members:
                slot = into.setdefault(other_id, {"votes": {}, "equiv": set(), "seen": set()})
                slot["equiv"].update(members - {other_id})
                slot["seen"].add(video_id)


def harvest_rows(collected: dict, owned: set[str]) -> list[dict]:
    rows = []
    for video_id in sorted(collected):
        slot = collected[video_id]
        ranked = sorted(slot["votes"].items(), key=lambda kv: (-kv[1], kv[0]))
        rows.append({
            "video_id": video_id,
            "owned": "1" if video_id in owned else "",
            "performers": " ".join(name for name, _ in ranked),
            "performer_votes": " ".join(f"{name}:{n}" for name, n in ranked),
            "equivalents": " ".join(sorted(slot["equiv"])),
            "seen_on": " ".join(sorted(slot["seen"])),
        })
    return rows


def backfill(rows: list[dict], collected: dict) -> list[dict]:
    """用全站收获补候选行的演员与等价关系。

    一个作品的演员常常标在**别的作品页**的评论里：`4176112` 那条「剛毛マキちゃん +
    四条链接」同时认领了 `3701252` 和 `4078398`。只看本页评论，这两条候选就会写着
    「无演员标记」，而收获表里明明有。
    """
    for row in rows:
        slot = collected.get(row.get("video_id"))
        if not slot:
            continue
        ranked = sorted(slot["votes"].items(), key=lambda kv: (-kv[1], kv[0]))
        if ranked:
            row["performers"] = " ".join(name for name, _ in ranked)
            row["performer_votes"] = " ".join(f"{name}:{n}" for name, n in ranked)
        merged = set(slot["equiv"]) | set((row.get("equivalents") or "").split())
        if merged:
            row["equivalents"] = " ".join(sorted(merged))
    return rows


FIELDS = ("code", "video_id", "result", "title", "release_date", "duration",
          "censored", "writer", "writer_slug", "tags", "performers",
          "performer_votes", "is_collection", "collection_parts",
          "equivalents", "cover_url", "note")


def _candidate_key(code: str, field: str, value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{code}\0{field}\0fc2\0{canonical}".encode()).hexdigest()[:20]
    return f"{code}:{field}:fc2:{digest}"


def translated_tags(raw: str, genre_decisions=None) -> list[str]:
    """Conservatively project archived FC2 labels into Peach's reviewed taxonomy.

    词表和 JAV 官方来源共用 `peach.genre_taxonomy`：同一个日文标签在 FC2 和
    dmm/mgstage 上必须投影到同一个 Peach 标签，两份表迟早会漂移。用户在复核页
    收录下来的那批也共用，不然收录一次只对其中一条抓取链生效。
    """
    return map_genres(str(raw or "").split(), genre_decisions)[0]


def metadata_candidate_rows(rows: list[dict], database: Path, *, raw_snapshot: Path,
                            fetched_at: str) -> list[dict]:
    """Turn archived article facts into the normal review queue without writing ledger."""
    connection = open_readonly(database)
    output = []
    try:
        # 用户在复核页收录过的 genre 这一批就当已知词，不再作为未收录回来问一遍。
        genre_decisions = load_genre_decisions(connection)
        for row in rows:
            if row.get("result") != "取得":
                continue
            code = normalise_code_key(str(row.get("code") or ""))
            assets = connection.execute(
                "SELECT id,size,catalog_title FROM asset WHERE medium='video' "
                "AND upper(trim(code))=upper(?) AND (disposal IS NULL OR disposal<>'trash')",
                (code,),
            ).fetchall()
            if not assets:
                continue
            current_tags = sorted({str(item[0]).strip() for item in connection.execute(
                "SELECT DISTINCT t.tag FROM asset a JOIN asset_tag t ON t.asset_id=a.id "
                "WHERE a.medium='video' AND upper(trim(a.code))=upper(?) "
                "AND t.tag IS NOT NULL AND trim(t.tag)<>''",
                (code,),
            )})
            source_url = article_url(str(row.get("video_id") or ""))
            evidence = {}
            for field, value in (
                ("runtime", row.get("duration")),
                ("cover_url", high_resolution_cover_url(row.get("cover_url") or "")),
            ):
                if value:
                    evidence[field] = {"value": value, "display_value": str(value), "warnings": []}
            warnings = ["FC2CMADB 归档镜像，需人工复核"]
            values = []
            title = " ".join(str(row.get("title") or "").split())
            if title:
                values.append(("title", "标题", title, title,
                               next((str(item["catalog_title"] or "").strip()
                                     for item in assets if item["catalog_title"]), "")))
            tags = translated_tags(str(row.get("tags") or ""), genre_decisions)
            if tags:
                values.append(("tags", "内容标签", tags, "、".join(tags),
                               "、".join(current_tags)))
            for field, label, value, display_value, current in values:
                candidate = {
                    "candidate_key": _candidate_key(code, field, value),
                    "provider": "fc2cmadb", "source": "fc2", "source_url": source_url,
                    "confidence": 0.8, "profile": "fc2", "policy_version": METADATA_POLICY_VERSION,
                    "field_rank": 1, "source_kind": "official_mirror", "official": True,
                    "provider_id": str(row.get("video_id") or ""),
                    "content_id": str(row.get("video_id") or ""),
                    "value": value, "display_value": display_value, "warnings": warnings,
                    "catalog_evidence": evidence, "raw_snapshot": str(raw_snapshot),
                }
                output.append({
                    "item_key": f"{code}:{field}", "code": code, "query": code,
                    "field": field, "field_label": label, "current_value": current,
                    "candidates_json": json.dumps([candidate], ensure_ascii=False,
                                                   separators=(",", ":")),
                    "source_count": 1, "source_profile": "fc2",
                    "policy_version": METADATA_POLICY_VERSION, "status": "candidate",
                    "size_gb": round(sum(int(item["size"] or 0) for item in assets) / 1e9, 2),
                    "videos": len(assets), "fetched_at": fetched_at,
                })
    finally:
        connection.close()
    return output


def pending(database: Path, limit: int) -> list[tuple[str, str]]:
    """库里的 FC2 资产，按 (code, video_id) 去重。"""
    connection = open_readonly(database)
    try:
        rows = connection.execute(
            "SELECT DISTINCT code FROM asset "
            "WHERE code LIKE 'FC2%' AND code IS NOT NULL AND code<>'' ORDER BY code"
        ).fetchall()
    finally:
        connection.close()
    seen: dict[str, str] = {}
    for (code,) in rows:
        match = VIDEO_ID.search(code or "")
        if match:
            seen.setdefault(match.group(0), code)
    ordered = [(code, video_id) for video_id, code in seen.items()]
    return ordered[:limit] if limit else ordered


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--log", type=Path,
                        default=GENERATED_DIR / "fc2-candidate-log.csv")
    parser.add_argument("--harvest", type=Path,
                        default=GENERATED_DIR / "fc2-comment-harvest.csv",
                        help="评论里出现过的所有 video_id，不限于库里有的")
    parser.add_argument("--raw", type=Path,
                        default=GENERATED_DIR / "fc2-comments-raw.jsonl",
                        help="原始评论存档；解析规则以后改了不必重爬")
    parser.add_argument("--metadata-log", type=Path,
                        default=GENERATED_DIR / "fc2-metadata-field-candidates.csv",
                        help="标题与标签的统一元数据复核候选")
    parser.add_argument("--rebuild-metadata-only", action="store_true",
                        help="不联网，只用既有 --log 重建元数据候选")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=2.0)
    return parser


def run(args: argparse.Namespace) -> int:
    if args.rebuild_metadata_only:
        rows = read_rows(args.log)
        fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(args.log.stat().st_mtime))
        candidates = metadata_candidate_rows(
            rows, args.db, raw_snapshot=args.log, fetched_at=fetched_at,
        )
        write_rows(args.metadata_log, METADATA_FIELDS, candidates)
        print(f"完成：{len(candidates)} 个 FC2 元数据字段候选 -> {args.metadata_log}")
        return 0
    todo = pending(args.db, args.limit)
    owned = {video_id for _, video_id in todo}
    rows: list[dict] = []
    collected: dict = {}
    stats = {"hit": 0, "miss": 0}
    args.raw.parent.mkdir(parents=True, exist_ok=True)
    raw_log = args.raw.open("w", encoding="utf-8")
    print(f"待抓 {len(todo)} 个 FC2 作品", flush=True)
    with client_for(SECRETS_DIR, "fc2cmadb", session=True) as client:
        for index, (code, video_id) in enumerate(todo, 1):
            try:
                props = fetch_article(client, video_id)
                data = summarise(video_id, props)
            # 长跑任务不能因为一个作品的网络问题整体退出；按条记「未取得」继续。
            # Ctrl-C 是 BaseException，不受影响。
            except Exception as exc:
                stats["miss"] += 1
                reason = f"{type(exc).__name__}: {exc}"
                rows.append({"code": code, "video_id": video_id,
                             "result": "未取得", "note": reason[:120]})
                print(f"[{index}/{len(todo)}] 未取得 {code}：{reason[:70]}", flush=True)
            else:
                stats["hit"] += 1
                rows.append({"code": code, "video_id": video_id,
                             "result": "取得", **data})
                harvest(video_id, props, collected)
                # 原文照存。这些是用户多年攒下的标记，我的正则漏掉哪种写法都不该
                # 意味着要重爬一遍。
                raw_log.write(json.dumps({"video_id": video_id, "code": code,
                                          "comments": page_comments(props)},
                                         ensure_ascii=False) + "\n")
                raw_log.flush()
                mark = "合集" if data["is_collection"] else (data["performers"] or "无演员标记")
                print(f"[{index}/{len(todo)}] 取得 {code} {mark}", flush=True)
            # 每条都落盘：上次抓取死在半路时进度全丢，重来一遍是三小时。
            write_rows(args.log, FIELDS, backfill(rows, collected))
            write_rows(args.harvest, HARVEST_FIELDS, harvest_rows(collected, owned))
            write_rows(args.metadata_log, METADATA_FIELDS, metadata_candidate_rows(
                backfill(rows, collected), args.db, raw_snapshot=args.log,
                fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            ))
            time.sleep(args.delay)
    raw_log.close()
    extra = sum(1 for vid in collected if vid not in owned)
    print(f"完成：取得 {stats['hit']}，未取得 {stats['miss']} -> {args.log}")
    print(f"评论收获 {len(collected)} 个 video_id，其中 {extra} 个库里还没有 -> {args.harvest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
