#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""韩国 MIB 官网（k-mib.com）采集：作品元数据、封面、演员头像、厂牌标识与官网链接。

MIB 的番号问 JAV 目录站必错（`catalog_rules.KOREAN_MIB_PREFIXES`），官网是这批番号
唯一可信的来源。流程分三段：

    --fetch   联网补本机快照：列表页、详情页、封面、演员头像与站点 og 图，已有的跳过。
              每主机请求间隔 --interval 秒；403 / 429 视为机器人拦截，立即停止本批。
    （缺省）  只读：解析快照，写三份复核产物——
                kmib-catalog.csv           全站作品（含合作厂牌）与账本对照，官网没有的
                                           账本番号记「未取得」；
                kmib-performers.csv        全部演员页资料与账本实体匹配；
                kmib-metadata-field-candidates.csv
                                           账本已有番号的字段候选，进 metadata_fields 复核队列。
    --apply   写 ledger 与生成物，写前备份 ledger：
                1. 补空番号：文件名按 MIB 规则解析出、且官网目录里有的番号；
                2. 重写字段候选，按 ADR-0018 窄例外自动批准（只补空，番号须在文件名里）；
                3. 缺失的封面装进 generated/covers，附 .scraping.json 来源；
                4. 已关联到 MIB 作品的演员：官网资料页链接、没有头像的装官网头像；
                5. MIB 厂牌：官网链接，没有 logo 的装官网 og 图。
              头像与 logo 走复核候选 CSV 再批准落地，与复核页同一条装载路径。

装完封面后另跑 `scripts/detect_cover_faces.py` 补取景 sidecar。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image  # noqa: E402

from peach.avatar_provider import (  # noqa: E402
    AvatarCandidateCache, acceptable_avatar, inspect_avatar,
    provenance_now as avatar_provenance,
)
from peach.catalog_rules import (  # noqa: E402
    is_korean_mib_code, normalise_code_key, release_code_from_filename,
)
from peach.fsutil import atomic_write_bytes  # noqa: E402
from peach.config import DATABASE_PATH, GENERATED_DIR, SOURCES_DIR  # noqa: E402
from peach.entities import normalize_entity_name  # noqa: E402
from peach.genre_decisions import load_genre_decisions  # noqa: E402
from peach.genre_taxonomy import map_genres  # noqa: E402
from peach.http import HttpRequest, HttpxTransport  # noqa: E402
from peach.images import REJECT, bake_square, classify  # noqa: E402
from peach.logo_provider import (  # noqa: E402
    LogoCandidateCache, inspect_logo, provenance_now as logo_provenance,
)
from peach.metadata import extract_catalog_evidence, extract_peach_fields  # noqa: E402
from peach.metadata_kmib import (  # noqa: E402
    PROVIDER, ROOT, SOURCE, STAR_LIST_URL, STAR_URL, STUDIO, VIDEO_LIST_URL,
    VIDEO_URL, parse_list, parse_star, parse_video,
)
from peach.previews import entity_image_key, logo_key  # noqa: E402
from peach.review_csv import write_rows  # noqa: E402
from peach.scripting import USER_AGENT, HostLimiter, open_readonly  # noqa: E402


POLICY_VERSION = "kmib-official-v1"
SNAPSHOT_DIR = SOURCES_DIR / "k-mib"
CANDIDATE_FILE = "kmib-metadata-field-candidates.csv"
CONFIDENCE = 0.9
#: 演员头像门槛，与 `audit_performer_portraits.py` 的缺省一致。
AVATAR_MIN_LONG, AVATAR_MIN_SHORT = 500, 300
#: 站点 og 图：官方黑猫加 MIB 字标，800×400。页面里读不到时用这个地址。
DEFAULT_LOGO_URL = ROOT + "/uploads/image/config/24120615374902.jpg"
_OG_IMAGE = re.compile(r'<meta\s+property="og:image"\s+content="([^"]+)"', re.I)

FIELD_LABELS = {
    "title": "标题", "performers": "演员", "studio": "厂牌",
    "release_date": "发行日期", "tags": "内容标签",
}
METADATA_FIELDS = (
    "item_key", "code", "query", "field", "field_label", "current_value",
    "candidates_json", "source_count", "source_profile", "policy_version",
    "status", "size_gb", "videos", "fetched_at",
)
CATALOG_FIELDS = (
    "code", "result", "maker", "partner", "title", "release_date", "runtime_minutes",
    "performers", "genres", "tags", "unmapped_genres", "ledger_assets", "cover_url",
    "source_url",
)
PERFORMER_FIELDS = (
    "idx", "name", "entity_id", "match", "age", "height", "weight", "bwh", "tags",
    "introduction", "image_url", "source_url",
)
AVATAR_FIELDS = (
    "entity_id", "current_name", "matched_name", "name_source", "provider",
    "source_kind", "source_url", "external_id", "gfriends_category", "gfriends_file",
    "width", "height", "mime_type", "sha256", "cache_path", "provenance_path",
    "policy_version", "verdict", "avatar_url", "evidence",
)
LOGO_FIELDS = (
    "studio", "handle", "platform", "resolver_url", "resolved_url", "width",
    "height", "aspect", "verdict", "saved", "accepted", "confirmation",
    "content_state", "duplicate_of", "sha256", "mime_type", "cache_key",
    "perceptual_hash", "visual_distance", "provenance_key", "policy_version", "reason",
)


# —— 快照 ——

def image_path(snapshot: Path, url: str) -> Path:
    suffix = Path(urlsplit(url).path).suffix.lower() or ".img"
    return snapshot / "images" / (hashlib.sha256(url.encode("utf-8")).hexdigest()[:32] + suffix)


def cached_image(snapshot: Path, url: str) -> bytes | None:
    path = image_path(snapshot, url) if url else None
    return path.read_bytes() if path is not None and path.is_file() else None


def load_releases(snapshot: Path) -> list[dict]:
    releases = []
    for path in sorted((snapshot / "video").glob("*.html"), key=lambda p: int(p.stem)):
        release = parse_video(path.read_bytes(), path.stem)
        if release is not None:
            release["raw_snapshot"] = str(path)
            releases.append(release)
    return releases


def load_stars(snapshot: Path) -> list[dict]:
    stars = []
    for path in sorted((snapshot / "star").glob("*.html"), key=lambda p: int(p.stem)):
        star = parse_star(path.read_bytes(), path.stem)
        if star is not None:
            stars.append(star)
    return stars


def logo_url(snapshot: Path) -> str:
    for path in sorted(snapshot.glob("v*.html"))[:1]:
        found = _OG_IMAGE.search(path.read_text(encoding="utf-8", errors="replace"))
        if found:
            return found.group(1)
    return DEFAULT_LOGO_URL


class Fetcher:
    """带主机限速的 GET；机器人拦截时抛 `Blocked`，调用方停下本批。"""

    class Blocked(RuntimeError):
        pass

    def __init__(self, interval: float, timeout: float = 30.0):
        self.http = HttpxTransport()
        self.limiter = HostLimiter({"k-mib.com": interval})
        self.timeout = timeout
        self.requests = 0

    def get(self, url: str, max_bytes: int = 16 << 20) -> bytes | None:
        self.limiter.wait(url)
        self.requests += 1
        response = self.http(HttpRequest("GET", url, {"User-Agent": USER_AGENT}),
                             self.timeout, max_bytes)
        if response.status in {403, 429}:
            raise self.Blocked(f"{response.status} {url}")
        return response.body if response.status == 200 else None


def fetch_snapshot(snapshot: Path, fetcher: Fetcher, *, refresh: bool,
                   ledger_codes: set[str]) -> dict:
    """列表页发现 idx → 补详情页 → 补图片。每一步都落盘，断了重跑接着来。"""
    stats = {"pages": 0, "details": 0, "images": 0, "failed": []}
    discovered: dict[str, list[str]] = {"video": [], "star": []}
    for kind, template, prefix in (("video", VIDEO_LIST_URL, "v"),
                                   ("star", STAR_LIST_URL, "s")):
        for page in range(1, 500):
            body = fetcher.get(template.format(page=page))
            if body is None:
                stats["failed"].append(template.format(page=page))
                break
            fresh = [idx for idx in parse_list(body)[kind] if idx not in discovered[kind]]
            if not fresh:
                break
            (snapshot / f"{prefix}{page}.html").write_bytes(body)
            discovered[kind].extend(fresh)
            stats["pages"] += 1
    for kind, template in (("video", VIDEO_URL), ("star", STAR_URL)):
        folder = snapshot / kind
        folder.mkdir(parents=True, exist_ok=True)
        for idx in discovered[kind]:
            target = folder / f"{idx}.html"
            if target.is_file() and not refresh:
                continue
            body = fetcher.get(template.format(idx=idx))
            if body is None:
                stats["failed"].append(template.format(idx=idx))
                continue
            target.write_bytes(body)
            stats["details"] += 1
    wanted = [release["cover_url"] for release in load_releases(snapshot)
              if release["id"] in ledger_codes and release["cover_url"]]
    wanted += [star["image_url"] for star in load_stars(snapshot) if star["image_url"]]
    wanted.append(logo_url(snapshot))
    for url in dict.fromkeys(wanted):
        target = image_path(snapshot, url)
        if target.is_file():
            continue
        body = fetcher.get(url)
        if body is None:
            stats["failed"].append(url)
            continue
        atomic_write_bytes(target, body)
        stats["images"] += 1
    return stats


# —— 复核产物 ——

def ledger_assets(connection, code: str) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT id,size,catalog_title,release_date,studio FROM asset WHERE medium='video' "
        "AND upper(trim(code))=upper(?) AND (disposal IS NULL OR disposal<>'trash')",
        (code,),
    ).fetchall()


def ledger_mib_codes(connection) -> set[str]:
    return {normalise_code_key(row[0]) for row in connection.execute(
        "SELECT DISTINCT code FROM asset WHERE medium='video' AND code IS NOT NULL "
        "AND (disposal IS NULL OR disposal<>'trash')") if is_korean_mib_code(row[0])}


def _current_values(connection, code: str, assets: list[sqlite3.Row]) -> dict[str, str]:
    def first(column: str) -> str:
        return next((str(row[column]).strip() for row in assets if row[column]), "")
    ids = [int(row["id"]) for row in assets]
    marks = ",".join("?" * len(ids))
    performers = [row[0] for row in connection.execute(
        f"SELECT DISTINCT e.canonical_name FROM asset_entity ae JOIN entity e "
        f"ON e.id=ae.entity_id WHERE ae.asset_id IN ({marks}) AND ae.role='performer' "
        "ORDER BY e.canonical_name", ids)]
    tags = sorted({str(row[0]).strip() for row in connection.execute(
        f"SELECT DISTINCT tag FROM asset_tag WHERE asset_id IN ({marks}) "
        "AND tag IS NOT NULL AND trim(tag)<>'' AND tag NOT LIKE '演员:%'", ids)})
    return {"title": first("catalog_title"), "release_date": first("release_date"),
            "studio": first("studio"), "performers": "、".join(performers),
            "tags": "、".join(tags)}


def _candidate_key(code: str, field: str, value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{code}\0{field}\0{SOURCE}\0{canonical}".encode()).hexdigest()[:20]
    return f"{code}:{field}:{SOURCE}:{digest}"


def star_index(stars: list[dict]) -> dict[str, dict]:
    return {normalize_entity_name(star["name"]): star for star in stars}


def metadata_candidate_rows(connection, releases: list[dict], stars: list[dict], *,
                            fetched_at: str) -> list[dict]:
    """账本已有番号的字段候选。item_key 带来源后缀：这批番号的 `<番号>:<字段>` 早被
    JAV 错配候选的拒绝决定占着，沿用同一个键会让官网候选一出现就算「已判」。"""
    by_name = star_index(stars)
    # 用户在复核页收录过的 genre 这一批就当已知词，不再作为未收录回来问一遍。
    genre_decisions = load_genre_decisions(connection)
    output = []
    for release in releases:
        if release["partner"]:
            continue
        code = release["id"]
        assets = ledger_assets(connection, code)
        if not assets:
            continue
        current = _current_values(connection, code, assets)
        fields = extract_peach_fields(release, genre_decisions)
        evidence = extract_catalog_evidence(release)
        if "performers" in fields:
            for person in fields["performers"]["value"]:
                star = by_name.get(normalize_entity_name(person["name"]))
                if star is not None:
                    person["external_id"] = star["idx"]
                    person["thumb_url"] = star["image_url"]
        for field, label in FIELD_LABELS.items():
            if field not in fields:
                continue
            value = fields[field]
            candidate = {
                "candidate_key": _candidate_key(code, field, value["value"]),
                "provider": PROVIDER, "source": SOURCE,
                "source_url": release["source_url"], "confidence": CONFIDENCE,
                "profile": SOURCE, "policy_version": POLICY_VERSION, "field_rank": 1,
                "source_kind": "official", "official": True,
                "provider_id": code, "content_id": "",
                "value": value["value"], "display_value": value["display_value"],
                "warnings": value.get("warnings", []), "catalog_evidence": evidence,
                "raw_snapshot": release.get("raw_snapshot", ""),
            }
            output.append({
                "item_key": f"{code}:{field}:{SOURCE}", "code": code, "query": code,
                "field": field, "field_label": label, "current_value": current[field],
                "candidates_json": json.dumps([candidate], ensure_ascii=False,
                                              separators=(",", ":")),
                "source_count": 1, "source_profile": SOURCE,
                "policy_version": POLICY_VERSION, "status": "candidate",
                "size_gb": round(sum(int(row["size"] or 0) for row in assets) / 1e9, 2),
                "videos": len(assets), "fetched_at": fetched_at,
            })
    return output


def catalog_rows(connection, releases: list[dict]) -> list[dict]:
    rows = []
    listed = set()
    genre_decisions = load_genre_decisions(connection)
    for release in releases:
        listed.add(release["id"])
        tags, unmapped = map_genres(release["genres"], genre_decisions)
        rows.append({
            "code": release["id"], "result": "取得", "maker": release["maker"],
            "partner": release["partner"], "title": release["title"],
            "release_date": release["release_date"],
            "runtime_minutes": release["runtime"] or "",
            "performers": "、".join(a["japanese_name"] for a in release["actresses"]),
            "genres": "、".join(release["genres"]), "tags": "、".join(tags),
            "unmapped_genres": "、".join(unmapped),
            "ledger_assets": len(ledger_assets(connection, release["id"])),
            "cover_url": release["cover_url"], "source_url": release["source_url"],
        })
    for code in sorted(ledger_mib_codes(connection) - listed):
        rows.append({"code": code, "result": "未取得", "maker": STUDIO,
                     "ledger_assets": len(ledger_assets(connection, code))})
    return rows


def mib_performer_entities(connection) -> dict[str, int]:
    """已经凭官网候选挂到 MIB 作品上的演员实体，按规范化名字索引。"""
    return {normalize_entity_name(row[1]): int(row[0]) for row in connection.execute(
        "SELECT DISTINCT e.id,e.canonical_name FROM asset_entity ae JOIN entity e "
        "ON e.id=ae.entity_id WHERE ae.role='performer' AND e.kind='performer' "
        "AND ae.source=?", (f"javinizer:{SOURCE}:performer",))}


def performer_rows(connection, stars: list[dict]) -> list[dict]:
    linked = mib_performer_entities(connection)
    rows = []
    for star in stars:
        entity_id = linked.get(normalize_entity_name(star["name"]))
        rows.append({
            **{key: star.get(key, "") for key in PERFORMER_FIELDS},
            "tags": "、".join(star.get("tags") or []),
            "entity_id": entity_id or "",
            "match": "已关联 MIB 作品" if entity_id else "未关联",
        })
    return rows


# —— 写入 ——

def backup_ledger(database: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = database.with_name(f"ledger.pre-kmib-harvest-{stamp}.db")
    source = sqlite3.connect(str(database))
    try:
        destination = sqlite3.connect(str(target))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return target


def fill_missing_codes(connection, catalog_codes: set[str]) -> list[tuple[int, str]]:
    """文件名按 MIB 规则解析得出、官网目录又有的番号，补进空着的 `asset.code`。"""
    filled = []
    for row in connection.execute(
            "SELECT id,name FROM asset WHERE medium='video' AND (code IS NULL OR trim(code)='') "
            "AND (disposal IS NULL OR disposal<>'trash')").fetchall():
        code = release_code_from_filename(row[1])
        if code and is_korean_mib_code(code) and code in catalog_codes:
            connection.execute(
                "UPDATE asset SET code=? WHERE id=? AND (code IS NULL OR trim(code)='')",
                (code, row[0]))
            filled.append((int(row[0]), code))
    return filled


def install_cover(snapshot: Path, cover_root: Path, release: dict) -> str:
    target = cover_root / f"{release['id']}.jpg"
    if target.is_file():
        return "已有"
    data = cached_image(snapshot, release["cover_url"])
    if data is None:
        return "未取得"
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            size = image.size
            if image.format != "JPEG":
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, "JPEG", quality=95)
                data = buffer.getvalue()
    except Exception:
        return "无法解码"
    atomic_write_bytes(target, data)
    atomic_write_bytes(target.with_suffix(".scraping.json"), json.dumps({
        "source": SOURCE, "source_url": release["cover_url"], "width": size[0],
        "height": size[1], "raw_sha256": hashlib.sha256(data).hexdigest(),
        "checked_at": time.time(),
    }, ensure_ascii=False).encode("utf-8"))
    return "取得"


def add_link(connection, entity_id: int, kind: str, label: str, url: str, now: str) -> bool:
    cursor = connection.execute(
        "INSERT OR IGNORE INTO entity_link(entity_id,link_kind,label,url,hostname,"
        "is_sensitive,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,0,?,?,?)",
        (entity_id, kind, label, url, urlsplit(url).hostname or "",
         json.dumps({"source": "harvest_kmib", "installed_at": now}, ensure_ascii=False),
         now, now))
    return cursor.rowcount > 0


def avatar_candidates(snapshot: Path, candidate_root: Path, avatar_root: Path,
                      stars: list[dict], entities: dict[str, int]) -> list[dict]:
    cache = AvatarCandidateCache(candidate_root / "provider-cache" / "performer-avatars" / SOURCE)
    rows = []
    for star in stars:
        entity_id = entities.get(normalize_entity_name(star["name"]))
        if entity_id is None or (avatar_root / f"{entity_image_key('performer', entity_id)}.img").is_file():
            continue
        data = cached_image(snapshot, star["image_url"])
        avatar = inspect_avatar(data) if data else None
        if avatar is None:
            continue
        verdict = "ok" if acceptable_avatar(avatar, AVATAR_MIN_LONG, AVATAR_MIN_SHORT) else "rejected"
        object_path = cache.store(star["image_url"], data, avatar)
        provenance = cache.store_provenance(avatar_provenance(
            entity_id=entity_id, provider=SOURCE, source_kind="official",
            matched_name=star["name"], name_source="k-mib star page",
            external_id=star["idx"], upstream_url=star["image_url"],
            width=avatar.width, height=avatar.height, mime_type=avatar.mime_type,
            sha256=avatar.sha256, cache_path=object_path.name))
        rows.append({
            "entity_id": entity_id, "current_name": star["name"],
            "matched_name": star["name"], "name_source": "k-mib star page",
            "provider": SOURCE, "source_kind": "official", "source_url": star["source_url"],
            "external_id": star["idx"], "width": avatar.width, "height": avatar.height,
            "mime_type": avatar.mime_type, "sha256": avatar.sha256,
            "cache_path": object_path.name, "provenance_path": provenance.name,
            "policy_version": POLICY_VERSION, "verdict": verdict,
            "avatar_url": star["image_url"],
            "evidence": "MIB 官网演员页头像；实体已凭官网作品候选关联到 MIB 作品",
        })
    return rows


def logo_candidate(snapshot: Path, candidate_root: Path, logo_root: Path) -> dict | None:
    if (logo_root / f"{logo_key(STUDIO)}.img").is_file():
        return None
    url = logo_url(snapshot)
    data = cached_image(snapshot, url)
    raster = inspect_logo(data) if data else None
    if raster is None:
        return None
    cache = LogoCandidateCache(candidate_root / "provider-cache" / "studio-logos" / SOURCE)
    object_path = cache.store(url, data, raster)
    verdict, aspect, reason = classify(raster.width, raster.height)
    baked = bake_square(data) if verdict != REJECT else None
    final = inspect_logo(baked) if baked else None
    saved = ""
    if final is not None:
        folder = candidate_root / "studio-logos"
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"{STUDIO}{final.extension}"
        destination.write_bytes(baked)
        saved = destination.name
    provenance = cache.provenance(logo_provenance(
        studio=STUDIO, handle="", platform="official-site", resolver_url=ROOT,
        source_url=url, width=raster.width, height=raster.height,
        mime_type=raster.mime_type, sha256=raster.sha256,
        perceptual_hash=raster.perceptual_hash, object_name=object_path.name))
    return {
        "studio": STUDIO, "handle": "", "platform": "official-site", "resolver_url": ROOT,
        "resolved_url": url, "width": raster.width, "height": raster.height,
        "aspect": round(aspect, 3), "verdict": verdict, "saved": saved,
        "accepted": bool(saved), "confirmation": "official-site",
        "content_state": "new" if saved else "rejected", "duplicate_of": "",
        "sha256": final.sha256 if final else "", "mime_type": final.mime_type if final else "",
        "cache_key": object_path.name,
        "perceptual_hash": final.perceptual_hash if final else "", "visual_distance": "",
        "provenance_key": provenance.name, "policy_version": POLICY_VERSION,
        "reason": "MIB 官网 og 图（黑猫加 MIB 字标）" if saved else reason,
    }


def verify(database: Path) -> dict:
    connection = sqlite3.connect(str(database))
    try:
        return {"integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0],
                "foreign_key_violations": len(connection.execute(
                    "PRAGMA foreign_key_check").fetchall())}
    finally:
        connection.close()


def apply(args, releases: list[dict], stars: list[dict]) -> dict:
    from peach.metadata_auto_apply import auto_apply_metadata
    from peach.repository import LedgerDatabase
    from peach.web_review import w_review_decision
    from peach.web_state import WebContract

    report: dict = {"backup": str(backup_ledger(args.db))}
    by_code = {release["id"]: release for release in releases if not release["partner"]}
    connection = sqlite3.connect(str(args.db))
    try:
        with connection:
            report["codes_filled"] = fill_missing_codes(connection, set(by_code))
    finally:
        connection.close()

    reader = open_readonly(args.db)
    try:
        rows = metadata_candidate_rows(reader, releases, stars, fetched_at=args.fetched_at)
    finally:
        reader.close()
    write_rows(args.out / CANDIDATE_FILE, METADATA_FIELDS, rows)
    # 自动批准只读这一份：同一个目录里其它来源的候选不在本次授权范围内。
    with tempfile.TemporaryDirectory() as scratch:
        shutil.copy2(args.out / CANDIDATE_FILE, Path(scratch) / CANDIDATE_FILE)
        outcome = auto_apply_metadata(LedgerDatabase(args.db), Path(scratch))
    report["auto_applied"] = outcome["applied"]
    report["left_to_review"] = outcome["left_to_review"]

    contract = WebContract(args.db, candidate_root=args.out)
    reader = open_readonly(args.db)
    try:
        codes = {normalise_code_key(r[0]) for r in reader.execute(
            "SELECT DISTINCT code FROM asset WHERE code IS NOT NULL AND medium='video' "
            "AND (disposal IS NULL OR disposal<>'trash')")}
        entities = mib_performer_entities(reader)
        studio_row = reader.execute(
            "SELECT id FROM entity WHERE kind='studio' AND normalized_name=?",
            (normalize_entity_name(STUDIO),)).fetchone()
    finally:
        reader.close()
    covers: dict[str, int] = {}
    for code, release in by_code.items():
        if code in codes:
            state = install_cover(args.snapshot, contract.cover_root, release)
            covers[state] = covers.get(state, 0) + 1
    report["covers"] = covers

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    by_name = star_index(stars)
    links = 0
    connection = sqlite3.connect(str(args.db))
    try:
        with connection:
            for key, entity_id in entities.items():
                star = by_name.get(key)
                if star is not None:
                    links += add_link(connection, entity_id, "catalog", "K-MIB",
                                      star["source_url"], now)
            if studio_row is not None:
                links += add_link(connection, int(studio_row[0]), "official",
                                  "K-MIB 官网", ROOT + "/", now)
    finally:
        connection.close()
    report["links_added"] = links

    stamp = time.strftime("%Y%m%d-%H%M%S")
    avatars = avatar_candidates(args.snapshot, args.out, contract.avatar_root, stars, entities)
    if avatars:
        write_rows(args.out / f"performer-avatar-candidate-kmib-{stamp}.csv",
                   AVATAR_FIELDS, avatars, fill_missing=True)
    installed = 0
    for row in avatars:
        if row["verdict"] == "ok":
            w_review_decision(contract, {
                "category": "performer_avatars", "item_key": str(row["entity_id"]),
                "status": "approved", "note": "MIB 官网演员页头像"})
            installed += 1
    report["avatars"] = {"candidates": len(avatars), "installed": installed}

    logo = logo_candidate(args.snapshot, args.out, contract.logo_root)
    report["logo"] = "已有" if logo is None else logo["content_state"]
    if logo is not None:
        write_rows(args.out / f"studio-logo-candidate-kmib-{stamp}.csv", LOGO_FIELDS, [logo])
        if logo["saved"]:
            w_review_decision(contract, {"category": "studio_logos", "item_key": STUDIO,
                                         "status": "approved", "note": "MIB 官网 og 图"})
            report["logo"] = "已装载"
    report.update(verify(args.db))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_DIR)
    parser.add_argument("--out", type=Path, default=GENERATED_DIR)
    parser.add_argument("--fetch", action="store_true", help="联网补快照")
    parser.add_argument("--refresh", action="store_true", help="--fetch 时重取已有详情页")
    parser.add_argument("--interval", type=float, default=1.5, help="同主机请求间隔秒数")
    parser.add_argument("--apply", action="store_true", help="写 ledger 与生成物（先备份）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if args.fetch:
        reader = open_readonly(args.db)
        try:
            ledger_codes = ledger_mib_codes(reader)
        finally:
            reader.close()
        fetcher = Fetcher(args.interval)
        try:
            stats = fetch_snapshot(args.snapshot, fetcher, refresh=args.refresh,
                                   ledger_codes=ledger_codes)
        except Fetcher.Blocked as exc:
            print(f"停止：站点拒绝访问（{exc}），已落盘的快照保留", file=sys.stderr)
            return 2
        print(json.dumps({"fetch": stats, "requests": fetcher.requests}, ensure_ascii=False))
    releases = load_releases(args.snapshot)
    stars = load_stars(args.snapshot)
    reader = open_readonly(args.db)
    try:
        write_rows(args.out / "kmib-catalog.csv", CATALOG_FIELDS,
                   catalog_rows(reader, releases), fill_missing=True)
        if not args.apply:
            write_rows(args.out / CANDIDATE_FILE, METADATA_FIELDS, metadata_candidate_rows(
                reader, releases, stars, fetched_at=args.fetched_at))
    finally:
        reader.close()
    report = apply(args, releases, stars) if args.apply else {}
    reader = open_readonly(args.db)
    try:
        write_rows(args.out / "kmib-performers.csv", PERFORMER_FIELDS,
                   performer_rows(reader, stars))
    finally:
        reader.close()
    print(json.dumps({"releases": len(releases), "stars": len(stars), **report},
                     ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
