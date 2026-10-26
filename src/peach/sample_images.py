"""按番号存的官方样张：读写、来源、失败记忆与本机缓存（ADR-0068）。

样张是片商给每部片配的剧照，女优页的照片档按番号把它们分组摆出来。这一层分四块：

1. **账本**：`code_sample_image` 一行一张，键是归一番号加序号。写入按 ADR-0052：官方站给的
   地址由代码判定，直接落，`source` 记 `auto:sample-images@<任务行 id>`，只填空，撤回就是删除。
2. **来源**：先读本机来源快照（`sources/library-metadata/<番号>-<站>.json`，处理链问过的站已经
   把样张地址带回来了，一次网络都不用），再按番号分档问官方站：有码问 DMM 的 GraphQL
   （`sources/dmm.py` 那一站），素人问 MGS 商品页。FC2、无码与其余档没有核实过的官方样张面，不问。
3. **失败记忆**：按站记「这一站说没有」，7 天内不再问同一站。冷却与预算用完不记：那是本趟
   没轮到，不是没有。
4. **缓存**：地址进账本，图不进。第一次有人看才下载到 `generated/sample-cache/<番号>/<n>.jpg`，
   同时缩一份 `<n>.thumb.jpg` 给图片墙；下载失败记一个 `<n>.miss`，一天内不再试，页面拿到 404
   显示占位。DMM 的图片主机在部分国内出口不可达，所以页面从不直连上游。
"""
from __future__ import annotations

import io
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote, urljoin, urlsplit

from PIL import Image, ImageOps

from .catalog_rules import normalise_code_key
from .fsutil import atomic_path
from .scraping_access import SOURCES, source_for

TABLE = "code_sample_image"

#: 这一层写下的一切都带这个归属串，批次号接在 `@` 后面。
SOURCE = "auto:sample-images"

#: 一部片最多存几张。DMM 常见 10～20 张，MGS 素人 8～16 张；再多的是来源把别的片也拼了进来。
MAX_PER_CODE = 30

#: 「这一站说没有」记多久。片商会补图，但不会天天补。
MISS_TTL = 7 * 24 * 3600

#: 下载失败的标记记多久。多半是出口不通或上游暂时抽风，隔一天再试。
DOWNLOAD_MISS_TTL = 24 * 3600

#: 图片墙那一档缩略图的宽度，与本地照片墙 `/photo-thumb` 同一档。
THUMB_WIDTH = 640

#: 一张样张最大收多少字节。DMM 原图实测 83 KB、MGS 约 150 KB，8 MiB 是余量。
MAX_IMAGE_BYTES = 8 * 1024 * 1024

#: 快照里只认发行方自己那几站给的样张：DMM、MGS 与 amane 桥那一档片商官网。综合索引（javdb、
#: JavBus、AVBase）与 FC2 存档站转载的图不算官方判据，不自动落。几站都给了时按这个顺序取。
SITE_ORDER = ("dmm", "mgstage", "prestige", "faleno", "dahlia", "makers", "r18dev", "1pondo", "fc2")

MGS_DETAIL = "https://www.mgstage.com/product/product_detail/{code}/"

_SAFE_KEY = re.compile(r"^[A-Za-z0-9_-]{2,40}$")
#: DMM 两种样张地址都是 120×90 的小图：GraphQL 给 `awsimgsrc…/pics_dig/digital/video/<cid>/<cid>-N.jpg`，
#: 旧页面给 `pics.dmm.co.jp/digital/video/<cid>/<cid>-N.jpg`。同目录的 `<cid>jp-N.jpg` 是原图
#: （2026-09-25 实测 SSIS-057：800×534、83 KB，JavBus 与 javdb 转载的都是这一张）。
_DMM_SMALL = re.compile(
    r"^https://(?:awsimgsrc\.dmm\.co\.jp/pics_dig|pics\.dmm\.co\.jp)/digital/video/"
    r"(?P<cid>[a-z0-9_]+)/(?P=cid)(?:jp)?-(?P<n>\d+)\.jpg(?:\?.*)?$")


def code_key(code: str | None) -> str:
    """账本与缓存目录共用的番号键；认不出或带路径字符的交空串。"""
    key = normalise_code_key(code)
    return key if _SAFE_KEY.match(key or "") else ""


def table_ready(connection: sqlite3.Connection) -> bool:
    """账本迁移到了没有。只读副本和旧测试夹具里可能还没有这张表，那时就当一张样张都没有。"""
    return connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (TABLE,)).fetchone() is not None


def site_label(site: str) -> str:
    """来源站给人看的名字，与采集设置里那一栏同名。"""
    return str(SOURCES.get(site, {}).get("label") or site)


# ── 地址 ─────────────────────────────────────────────────────────────


def original_url(url: str) -> str:
    """同一张图的原图地址。只改写核实过的 DMM 小图形态，别的原样交回。"""
    matched = _DMM_SMALL.match(str(url or "").strip())
    if not matched:
        return str(url or "").strip()
    cid, n = matched.group("cid"), matched.group("n")
    return f"https://pics.dmm.co.jp/digital/video/{cid}/{cid}jp-{n}.jpg"


def usable(urls: Iterable[str]) -> list[str]:
    """能进账本的那几条：HTTPS、主机属于登记过的采集来源、按原图去重，最多 `MAX_PER_CODE` 张。

    主机要登记过是因为下载发生在出图那一刻：账本里的地址会被服务端去取，只收采集设置里有
    连接方式与冷却记录的那几家，不给「任意地址抓取」留口子。
    """
    found: list[str] = []
    for raw in urls or ():
        url = original_url(str(raw or ""))
        if urlsplit(url).scheme != "https" or source_for(url) is None or url in found:
            continue
        found.append(url)
        if len(found) >= MAX_PER_CODE:
            break
    return found


def urls_from_payload(payload: dict) -> list[str]:
    """来源快照里的样张列：DMM 那一站叫 `sample_images`，amane 桥交的叫 `screenshot_urls`。"""
    for key in ("sample_images", "screenshot_urls"):
        values = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(values, list) and values:
            return [str(value) for value in values if value]
    return []


# ── 账本 ─────────────────────────────────────────────────────────────


def batch_for(run_id) -> str:
    return f"{SOURCE}@{run_id if run_id is not None else time.strftime('%Y%m%dT%H%M%S')}"


def land(connection: sqlite3.Connection, code: str, site: str, urls: Iterable[str], *,
         source: str, now: str | None = None) -> int:
    """把一部片的样张写进账本，返回写了几张。已有样张的番号整组跳过：只填空（ADR-0052 第三条）。"""
    key = code_key(code)
    kept = usable(urls)
    if not key or not kept:
        return 0
    if connection.execute(f"SELECT 1 FROM {TABLE} WHERE code=? LIMIT 1", (key,)).fetchone():
        return 0
    moment = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    connection.executemany(
        f"INSERT INTO {TABLE}(code,position,url,site,source,fetched_at) VALUES(?,?,?,?,?,?)",
        [(key, index, url, str(site), str(source), moment) for index, url in enumerate(kept, 1)])
    return len(kept)


def sample_url(connection: sqlite3.Connection, code: str, position: int) -> str | None:
    key = code_key(code)
    if not key or position < 1 or not table_ready(connection):
        return None
    row = connection.execute(f"SELECT url FROM {TABLE} WHERE code=? AND position=?",
                             (key, int(position))).fetchone()
    return str(row[0]) if row else None


def detail_refs(connection: sqlite3.Connection, code: str | None) -> list[dict[str, int]]:
    """详情可浏览的本机样张序号，按顺序限量返回，不读取来源地址或出网。"""
    key = code_key(code)
    if not key or not table_ready(connection):
        return []
    return [{"n": int(row[0])} for row in connection.execute(
        f"SELECT position FROM {TABLE} WHERE code=? AND position>0 ORDER BY position LIMIT ?",
        (key, MAX_PER_CODE))]


def counts(connection: sqlite3.Connection, codes: Iterable[str]) -> dict[str, tuple[int, str]]:
    """番号键 → （样张数，来源站）。没有样张的番号不在结果里。"""
    keys = sorted({key for key in (code_key(code) for code in codes) if key})
    if not keys or not table_ready(connection):
        return {}
    found: dict[str, tuple[int, str]] = {}
    for start in range(0, len(keys), 500):
        chunk = keys[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for row in connection.execute(
                f"SELECT code,count(*),min(site) FROM {TABLE} WHERE code IN ({marks}) GROUP BY code",
                chunk):
            found[str(row[0])] = (int(row[1]), str(row[2] or ""))
    return found


def sampled_codes(connection: sqlite3.Connection) -> set[str]:
    if not table_ready(connection):
        return set()
    return {str(row[0]) for row in connection.execute(f"SELECT DISTINCT code FROM {TABLE}")}


def planned_revert(connection: sqlite3.Connection, source: str, batch: str = "") -> list[dict]:
    """某个来源（或其中一批）写下的样张，按番号汇总。`source` 列存的是批次号，所以按「来源@」前缀认。"""
    if not table_ready(connection):
        return []
    if batch:
        clause, values = "source=?", (batch,)
    else:
        clause, values = "(source=? OR substr(source,1,?)=?)", (source, len(source) + 1, source + "@")
    return [{"code": str(row[0]), "count": int(row[1]), "source": str(row[2])}
            for row in connection.execute(
                f"SELECT code,count(*),source FROM {TABLE} WHERE {clause} GROUP BY code,source ORDER BY code",
                values)]


def revert(connection: sqlite3.Connection, source: str, batch: str = "") -> int:
    """删掉这一批写下的样张，返回删了几行。调用方负责事务与备份。"""
    if not table_ready(connection):
        return 0
    if batch:
        cursor = connection.execute(f"DELETE FROM {TABLE} WHERE source=?", (batch,))
    else:
        cursor = connection.execute(
            f"DELETE FROM {TABLE} WHERE source=? OR substr(source,1,?)=?",
            (source, len(source) + 1, source + "@"))
    return int(cursor.rowcount or 0)


# ── 来源 ─────────────────────────────────────────────────────────────


def site_for(code: str) -> str | None:
    """这个番号该问哪一站的样张：有码问 DMM，素人问 MGS，其余没有核实过的官方样张面。"""
    from .metadata_routes import classify
    kind = classify(code)
    return {"censored": "dmm", "amateur": "mgstage"}.get(kind)


def snapshot_index(sources_root: Path) -> dict[str, list[Path]]:
    """来源快照目录列一遍：番号键 → 官方站的那几份快照。文件名是 `<番号>-<站>.json`。"""
    folder = Path(sources_root) / "library-metadata"
    index: dict[str, list[Path]] = {}
    try:
        names = sorted(path for path in folder.iterdir() if path.suffix == ".json")
    except OSError:
        return index
    for path in names:
        head, _, site = path.stem.rpartition("-")
        key = code_key(head)
        if key and site in SITE_ORDER:
            index.setdefault(key, []).append(path)
    return index


def snapshot_samples(paths: Iterable[Path]) -> tuple[str, list[str]] | None:
    """几份快照里样张最可信的那一份：官方站在前，同档取张数多的。"""
    found: list[tuple[int, int, str, list[str]]] = []
    for path in paths:
        site = path.stem.rpartition("-")[2]
        if site not in SITE_ORDER:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        urls = usable(urls_from_payload(payload))
        if urls:
            found.append((SITE_ORDER.index(site), -len(urls), site, urls))
    if not found:
        return None
    _rank, _count, site, urls = min(found, key=lambda item: item[:3])
    return site, urls


class _MgsSamples(HTMLParser):
    """MGS 商品页的样张：`<a class="sample_image" href="…/cap_e_N_….jpg">`，与 EnlargeImage 封面分开。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = str(values.get("class") or "").split()
        if tag == "a" and "sample_image" in classes and values.get("href"):
            self.urls.append(urljoin("https://www.mgstage.com/", str(values["href"])))


def fetch_dmm(transport, code: str, *, deadline: float | None = None) -> list[str]:
    """DMM GraphQL 那一站给的样张。没有这部片抛 `SourceFailure(NOT_FOUND)`。"""
    from .sources import DmmSource, Session
    record = DmmSource().query(code, session=Session(transport, deadline))
    return [str(url) for url in record.extra.get("sample_images") or []]


def fetch_mgs(transport, code: str, *, deadline: float | None = None) -> list[str]:
    """MGS 商品页上的样张。年龄确认只带公开的 `adc=1`，与封面那一路相同。"""
    from .jav_cover_fetch import _fetch
    page = _fetch(transport, MGS_DETAIL.format(code=quote(code.upper())),
                  referer="https://www.mgstage.com/", limit=4 * 1024 * 1024,
                  extra_headers={"Cookie": "adc=1"}, deadline=deadline)
    parser = _MgsSamples()
    parser.feed(page.decode("utf-8", "ignore"))
    return parser.urls


FETCHERS: dict[str, Callable] = {"dmm": fetch_dmm, "mgstage": fetch_mgs}


class Misses:
    """按站的「没有」记忆：`{站: {番号: 时间戳}}`，一个 JSON 文件。"""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self.data: dict[str, dict[str, float]] = {
            str(site): {str(code): float(at) for code, at in rows.items()}
            for site, rows in (raw.items() if isinstance(raw, dict) else ()) if isinstance(rows, dict)}

    def fresh(self, site: str, code: str) -> bool:
        at = self.data.get(site, {}).get(code)
        return at is not None and self.clock() - at < MISS_TTL

    def record(self, site: str, code: str) -> None:
        self.data.setdefault(site, {})[code] = self.clock()

    def save(self) -> None:
        now = self.clock()
        kept = {site: {code: at for code, at in rows.items() if now - at < MISS_TTL}
                for site, rows in self.data.items()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_path(self.path) as temporary:
            Path(temporary).write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")


def misses_path(generated_root: Path) -> Path:
    return Path(generated_root) / "provider-cache" / "sample-images" / "misses.json"


def pending(connection: sqlite3.Connection, has_cover: Callable[[str], bool], misses: Misses,
            snapshots: dict[str, list[Path]], *, limit: int) -> list[str]:
    """还没有样张、已有封面、这一趟问得着的番号，新入库的在前，最多 `limit` 个。

    「问得着」是快照里有，或者按番号分档有一站可问、那一站最近没说过没有。封面在不在是
    「这部片的资料已经齐了」的记号：没封面的番号多半还没认准，先不为它问样张。
    """
    if limit <= 0 or not table_ready(connection):
        return []
    done = sampled_codes(connection)
    found: list[str] = []
    for row in connection.execute(
            "SELECT code,max(id) AS newest FROM asset WHERE code IS NOT NULL AND code<>''"
            " AND disposal IS NULL GROUP BY code ORDER BY newest DESC"):
        key = code_key(row[0])
        if not key or key in done or key in found or not has_cover(key):
            continue
        site = site_for(key)
        if key in snapshots and not misses.fresh("snapshot", key):
            found.append(key)
        elif site and not misses.fresh(site, key):
            found.append(key)
        if len(found) >= limit:
            break
    return found


# ── 缓存 ─────────────────────────────────────────────────────────────


def cache_root(photo_root: Path) -> Path:
    """样张缓存的目录：照片缩略图旁边那一个，跟着它进临时目录（理由同 `previews.entity_thumb_root`）。"""
    return Path(photo_root).parent / "sample-cache"


class SampleCache:
    """样张的本机缓存。原图 `<番号>/<n>.jpg`，墙上那一档 `<番号>/<n>.thumb.jpg`，失败标记 `<n>.miss`。"""

    def __init__(self, root: Path, clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.clock = clock

    def _paths(self, code: str, position: int) -> tuple[Path, Path, Path] | None:
        key = code_key(code)
        if not key or int(position) < 1:
            return None
        folder = self.root / key
        return folder / f"{position}.jpg", folder / f"{position}.thumb.jpg", folder / f"{position}.miss"

    def cached(self, code: str, position: int, *, thumb: bool) -> Path | None:
        paths = self._paths(code, position)
        if paths is None:
            return None
        target = paths[1] if thumb else paths[0]
        return target if target.is_file() else None

    def failed_recently(self, code: str, position: int) -> bool:
        paths = self._paths(code, position)
        if paths is None:
            return True
        try:
            return self.clock() - paths[2].stat().st_mtime < DOWNLOAD_MISS_TTL
        except OSError:
            return False

    def fetch(self, code: str, position: int, url: str, download: Callable[[str], bytes | None],
              *, thumb: bool) -> Path | None:
        """缓存里有就交缓存；没有就下载一次、验成图片、落盘，再按需缩一份。取不到交 None。

        `download` 返回 None 表示这一趟没轮到（冷却、预算），不记失败标记；抛异常或交回的不是
        图片才记。
        """
        paths = self._paths(code, position)
        if paths is None:
            return None
        original, small, miss = paths
        if not original.is_file():
            if self.failed_recently(code, position):
                return None
            try:
                data = download(url)
            except Exception:  # noqa: BLE001 - 上游的任何失败都只让这一张显示占位
                data = b""
            if data is None:
                return None
            try:
                with Image.open(io.BytesIO(data)) as probe:
                    probe.verify()
            except Exception:  # noqa: BLE001 - 不是图片、截断、解码炸弹
                original.parent.mkdir(parents=True, exist_ok=True)
                miss.write_bytes(b"")
                return None
            original.parent.mkdir(parents=True, exist_ok=True)
            with atomic_path(original) as temporary:
                Path(temporary).write_bytes(data)
            miss.unlink(missing_ok=True)
        if not thumb:
            return original
        if small.is_file():
            return small
        try:
            with atomic_path(small) as temporary, Image.open(original) as opened:
                image = ImageOps.exif_transpose(opened)
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 8), Image.LANCZOS)
                image.save(temporary, "JPEG", quality=82, optimize=True)
        except (OSError, ValueError, Image.DecompressionBombError):
            return None
        return small


def downloader(secrets_root: Path) -> Callable[[str], bytes | None]:
    """出图那一刻下载一张样张：走采集设置里这一站的连接方式与冷却，冷却中交 None。"""
    from .http import HttpRequest
    from .scraping_access import SourcePaused, SourceTransport
    from .scripting import USER_AGENT

    def download(url: str) -> bytes | None:
        transport = SourceTransport(Path(secrets_root), max_requests=3,
                                    max_bytes=MAX_IMAGE_BYTES, max_seconds=20)
        try:
            response = transport(HttpRequest("GET", url, {"User-Agent": USER_AGENT}, None),
                                 15.0, MAX_IMAGE_BYTES)
        except SourcePaused:
            return None
        finally:
            transport.close()
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}")
        return response.body

    return download
