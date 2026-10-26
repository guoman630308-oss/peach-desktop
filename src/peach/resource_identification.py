"""无番号视频的联网识别：查询清单与候选合并。

`scrape_codes` 只认番号，馆藏里大多数视频没有番号，进不了那条管线。这里按文件名
生成可搜索的查询（去推广头尾、拆无空格英文、取演出者前缀），并把联网核对后的
结果合并进 `library_processing` 读写的 `library-metadata-field-candidates.csv`。
产物仍是候选：`source='websearch'`、状态 `candidate`，写真相字段仍由 `/review`
批准；脚本本身不写账本。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path, PureWindowsPath

from .catalog_rules import is_jav_code, strip_promo_markers
from .entities import canonicalize_entity_name, collapse_repeated_entity_name
from .library_processing import FIELDS, LABELS
from .review_csv import read_rows, write_rows
from .media_sources import SCANNABLE_LOCATIONS

SOURCE = "websearch"
POLICY_VERSION = "websearch-v1"
SOURCE_PROFILE = "library"
CANDIDATE_FILENAME = "library-metadata-field-candidates.csv"

#: 联网识别能填的字段，与 `_apply_metadata_candidate` 的写入映射同源。
ALLOWED_FIELDS = ("title", "original_title", "performers", "studio", "series", "release_date")
_ENTITY_FIELDS = frozenset({"performers", "studio", "series"})

_SEPARATOR = re.compile(r"[._\-—+]+")
#: 无空格英文（`YouGetFootjobStandingFromStarttoFinish9`）按大小写与字母数字边界拆词。
_WORD_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")
#: 尾部画质与版本标签可以叠多层（`...-uncensored-HD`、`...MP4-P2P`），循环剥到不动。
_QUALITY_TAIL = re.compile(
    r"[-_.\s\[\]()【】]+(?:uncensored|無修正|无码|無碼|破解|中文字幕|中字|中文|完整版|全集|"
    r"原版|无水印|無水印|4k|2160p|1440p|1080p|720p|hd|fhd|sd|xxx|p2p|mp4|mov|avi|wmv)$",
    re.I)
_TRAILING_BRACKET = re.compile(r"[-_.\s]*(?:\[[^\[\]]{1,24}\]|【[^【】]{1,24}】|\([^()]{1,24}\))$")


def query_variants(name: str) -> list[str]:
    """按文件名给出一组搜索写法，原样名永远排在第一个。

    只做可逆的文本归一：摘掉头尾推广域名、分隔符换空格、按大小写拆无空格英文、
    剥尾部画质标签。不做翻译、不猜词、不删正文。
    """
    stem = PureWindowsPath(str(name or "")).stem.strip()
    if not stem:
        return []
    base = strip_promo_markers(stem).strip()
    spaced = re.sub(r"\s+", " ", _SEPARATOR.sub(" ", base)).strip()
    tagged = spaced
    while True:
        stripped = _QUALITY_TAIL.sub("", tagged)
        if stripped == tagged:
            break
        tagged = stripped
    tagged = _TRAILING_BRACKET.sub("", tagged.strip(" -_."))
    word_split = re.sub(r"\s+", " ", _WORD_BOUNDARY.sub(" ", tagged)).strip()
    variants: list[str] = []
    seen: set[str] = set()
    for value in (stem, base, spaced, tagged, word_split):
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            variants.append(value)
    return variants


def performer_guess(name: str) -> str:
    """文件名开头的演出者写法（`梓怡-背著老公…` 里的 `梓怡`），认不出返回空串。

    这是给识别的人看的线索，不是实体断言：番号形状的前缀、纯拉丁长串和没有
    分隔符的名字都不算。
    """
    stem = strip_promo_markers(PureWindowsPath(str(name or "")).stem).strip()
    if is_jav_code(stem):
        return ""
    parts = re.split(r"[-—_]", stem, maxsplit=1)
    if len(parts) < 2:
        return ""
    head = parts[0].strip()
    if not head:
        return ""
    if is_jav_code(head):
        return ""
    if len(head) > 12 or (re.fullmatch(r"[A-Za-z0-9 .&']+", head) and len(head) > 8):
        return ""
    return head


def _sibling_images(connection) -> dict[str, dict[str, str]]:
    """目录（小写）→ 文件名主体（小写）→ 图片路径。"""
    images: dict[str, dict[str, str]] = {}
    for row in connection.execute(
            "SELECT path FROM asset WHERE medium='image' AND disposal IS NULL "
            "AND path IS NOT NULL AND path<>''"):
        path = str(row["path"])
        parsed = PureWindowsPath(path)
        images.setdefault(str(parsed.parent).casefold(), {})[str(parsed.stem).casefold()] = path
    return images


def build_worklist(
    connection: sqlite3.Connection, *, locations: tuple[str, ...] = SCANNABLE_LOCATIONS,
    prefix: str | None = None, ids: tuple[int, ...] | None = None,
    paired_only: bool = False, sparse_only: bool = False, limit: int = 0,
) -> list[dict[str, object]]:
    """列出无番号视频与它们的检索材料；只读，不改账本。

    `sparse_only` 只留元信息最少的那些：创作者、标题、厂牌、系列、演员实体一个
    都没有。识别优先做这些——已经有元数据的条目不缺这一趟联网。
    """
    if not locations:
        return []
    marks = ",".join("?" * len(locations))
    where = [
        "medium='video'", "(code IS NULL OR trim(code)='')", "disposal IS NULL",
        f"location IN ({marks})",
    ]
    parameters: list[object] = list(locations)
    if prefix:
        where.append("path LIKE ?")
        parameters.append(prefix.rstrip("\\") + "\\%")
    if ids:
        id_marks = ",".join("?" * len(ids))
        where.append(f"id IN ({id_marks})")
        parameters.extend(int(value) for value in ids)
    rows = connection.execute(
        "SELECT id,location,name,path,size,duration,creator,catalog_title,original_title,"
        "studio,series FROM asset "
        f"WHERE {' AND '.join(where)} ORDER BY path",
        parameters,
    ).fetchall()
    entity_roles = {int(row[0]) for row in connection.execute(
        "SELECT DISTINCT asset_id FROM asset_entity "
        "WHERE role IN ('performer','studio','series')")}
    images = _sibling_images(connection)
    worklist: list[dict[str, object]] = []
    for row in rows:
        path = str(row["path"] or "")
        parsed = PureWindowsPath(path)
        cover = images.get(str(parsed.parent).casefold(), {}).get(str(parsed.stem).casefold(), "")
        if paired_only and not cover:
            continue
        metadata_hits = sum(
            1 for value in (row["creator"], row["catalog_title"], row["original_title"],
                            row["studio"], row["series"])
            if str(value or "").strip()
        ) + (1 if int(row["id"]) in entity_roles else 0)
        if sparse_only and metadata_hits:
            continue
        name = str(row["name"] or parsed.name)
        variants = query_variants(name) or [name]
        worklist.append({
            "asset_id": int(row["id"]),
            "location": row["location"],
            "path": path,
            "name": name,
            "size_gb": round((row["size"] or 0) / 1024 ** 3, 2),
            "duration": round(float(row["duration"]), 1) if row["duration"] else "",
            "metadata_hits": metadata_hits,
            "performer_guess": performer_guess(name),
            "cover_path": cover,
            "existing_creator": row["creator"] or "",
            "existing_title": row["catalog_title"] or row["original_title"] or "",
            "query_primary": variants[0],
            "query_variants": " | ".join(variants),
        })
        if limit and len(worklist) >= limit:
            break
    worklist.sort(key=lambda item: (int(item["metadata_hits"]), str(item["path"])))
    return worklist


def _current_value(connection: sqlite3.Connection, asset: sqlite3.Row, field: str) -> str:
    if field == "title":
        return str(asset["catalog_title"] or "")
    if field == "original_title":
        return str(asset["original_title"] or "")
    if field in {"studio", "series", "release_date"}:
        return str(asset[field] or "")
    if field == "performers":
        names = connection.execute(
            "SELECT DISTINCT e.canonical_name FROM asset_entity ae "
            "JOIN entity e ON e.id=ae.entity_id AND e.kind='performer' "
            "WHERE ae.asset_id=? AND ae.role='performer'",
            (int(asset["id"]),),
        ).fetchall()
        return "、".join(sorted({str(row[0]).strip() for row in names if str(row[0] or "").strip()}))
    return ""


def _candidate_key(asset_id: int, field: str, value: object) -> str:
    digest = hashlib.sha256(json.dumps(
        [asset_id, field, value], ensure_ascii=False, sort_keys=True,
    ).encode()).hexdigest()[:20]
    return f"{SOURCE}:{digest}"


def _performer_value(value: str) -> list[dict[str, str]]:
    performers = []
    for raw in re.split(r"[、,，|/]+", value):
        name = canonicalize_entity_name("performer", raw.strip())
        if name:
            performers.append({"name": name})
    return performers


def _clean_value(field: str, value: str) -> object | None:
    """把填写值整理成 `_apply_metadata_candidate` 能接受的形状；不合法返回 None。"""
    text = " ".join(str(value or "").split())
    if not text:
        return None
    if field == "release_date":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return None
        try:
            time.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
        return text
    if field == "performers":
        return _performer_value(text) or None
    if field in {"studio", "series"}:
        name = collapse_repeated_entity_name(canonicalize_entity_name(field, text))
        return name or None
    if len(text) > 1000 or any(ord(char) < 32 for char in text):
        return None
    return text


def ingest_results(
    connection: sqlite3.Connection, candidates_path: Path, results: list[dict[str, str]],
) -> dict[str, int]:
    """把联网核对结果合并成字段候选；已判定的行不动，只追加 `websearch` 来源。"""
    rows: dict[str, dict] = {
        str(row["item_key"]): dict(row)
        for row in read_rows(Path(candidates_path), missing_ok=True)
        if str(row.get("item_key") or "").strip()
    }
    applied = skipped = 0
    for item in results:
        asset_id = str(item.get("asset_id") or "").strip()
        field = str(item.get("field") or "").strip()
        if not asset_id.isdigit() or field not in ALLOWED_FIELDS:
            skipped += 1
            continue
        asset = connection.execute(
            "SELECT id,path,name,size,catalog_title,original_title,studio,series,release_date,code "
            "FROM asset WHERE id=? AND medium='video' AND disposal IS NULL",
            (int(asset_id),),
        ).fetchone()
        # 有番号的视频走 scrape_codes 的 JAV 来源，联网识别不覆盖它。
        if asset is None or str(asset["code"] or "").strip():
            skipped += 1
            continue
        value = _clean_value(field, str(item.get("value") or ""))
        if value is None:
            skipped += 1
            continue
        try:
            confidence = float(item.get("confidence") or 0.6)
        except (TypeError, ValueError):
            confidence = 0.6
        confidence = min(max(confidence, 0.0), 1.0)
        item_key = f"asset:{asset_id}:{field}"
        group = rows.get(item_key) or dict(
            item_key=item_key, code="", query=str(asset["name"] or ""),
            asset_id=int(asset_id), asset_path=str(asset["path"] or ""),
            field=field, field_label=LABELS.get(field, field),
            current_value=_current_value(connection, asset, field),
            candidates_json="[]", source_count=0, source_profile=SOURCE_PROFILE,
            policy_version=POLICY_VERSION, status="candidate",
            size_gb=round((asset["size"] or 0) / 1024 ** 3, 2), videos=1,
            fetched_at="",
        )
        try:
            choices = [
                choice for choice in json.loads(str(group.get("candidates_json") or "[]"))
                if isinstance(choice, dict) and choice.get("source") != SOURCE
            ]
        except (TypeError, ValueError):
            choices = []
        source_url = str(item.get("source_url") or "").strip()
        note = str(item.get("note") or "").strip()
        candidate_key = _candidate_key(int(asset_id), field, value)
        choices.append({
            "candidate_key": candidate_key,
            "source": SOURCE,
            "source_url": source_url,
            "confidence": confidence,
            "profile": SOURCE_PROFILE,
            "policy_version": POLICY_VERSION,
            "field_rank": 0,
            "source_kind": "community",
            "official": False,
            "provider_id": "",
            "content_id": "",
            "value": value,
            "display_value": "、".join(entry["name"] for entry in value)
            if field == "performers" else str(value),
            "warnings": ["联网识别候选，需核对来源页"],
            "catalog_evidence": {},
            "wiki_evidence": {},
            "raw_snapshot": note or source_url,
        })
        group.update(
            candidates_json=json.dumps(choices, ensure_ascii=False),
            source_count=len(choices),
            fetched_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        rows[item_key] = group
        applied += 1
    write_rows(Path(candidates_path), FIELDS,
               sorted(rows.values(), key=lambda row: str(row["item_key"])), atomic=True)
    return {"applied": applied, "skipped": skipped, "groups": len(rows)}
