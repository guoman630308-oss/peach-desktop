"""关注卡叠层里哪几张其实是同一个画面。

一张关注卡合并了一组成员：同站的几份版本（alt、WIP）、跨站的同作品上传、帖子自带的
多张媒体。卡片上有两件事要知道「哪几张是同一个」：

- **翻卡**：悬停时逐张翻成员的缩略图。画面近乎相同的几张翻过去还是那张图，看着像卡住
  了，只翻彼此不同的；剔完只剩一张就不翻。
- **计数**：封面角标报这张卡合并了几个不同的媒体。同一个视频在两个站各传一份算一个
  媒体、两个来源；同一个站里只有能证明是同一个文件的才并成一个。

两件事都只作用于显示层：不合并条目、不改 ledger。判错的代价是少翻一帧，或者两个
不同的视频被写成「2 个来源」，点开以后两条都还在。

判据按先后：

1. 文件内容哈希相同，就是同一个文件。哈希取自已存的原始地址（kemono 系的 SHA-256、
   rule34.xxx 与 paheal 的 MD5，判据在各站连接器的 `content_hash`），不发任何请求，
   没有缩略图的归档站视频也判得出。哈希不同不等于画面不同，继续往下判。
2. 缩略图地址相同，就是同一张。
3. 否则两道都要过：dHash（`community_catalog.fingerprint`，与封面比对同一份实现）的
   汉明距离，和 8×8 色块逐格的最大 RGB 差。dHash 只看明暗走向，同一个姿势的穿衣版与
   nude 版常常只差 1～4 位；色块把换了颜色的那一块抓出来。
   两边时长都已知时，时长相差不超过 `DURATION_TOLERANCE`，且两道分别不超过
   `FACE_DISTANCE`、`COLOR_DISTANCE`；时长都已知却差得多，是两段不同的内容。
   有一边时长未知（图片、归档站不报时长的视频），改用更严的 `STRICT_FACE_DISTANCE`、
   `STRICT_COLOR_DISTANCE`。
4. 签名还没取得的只按第 1、2 条判。

计数比翻卡严：同站的两份只按第 1 条合并，第 2、3 条只用于跨站。同站按画面找不到安全
间隔，2026-09-24 在 11745 组、66522 对可比的同站成对上量过：

- 同一个文件（哈希相同）的 255 对，dHash 与色块差全是 0。
- 哈希不同、dHash ≤2 且色块差 <2 的 147 对里混着三种东西：同一张图的 4K 与 8K 两个
  文件（色块差 0.33–0.67）、同一帖子里重复上传的同一张（0–0.7），和只改了局部的差分
  图。「Tifa」一帖里的两对差分，色块差只有 1.67 与 2.0，放到 32×32 网格上最大格差却有
  26–27。8×8 签名看不见局部改动，跨站同一文件各自重压缩后的色块差也有 0.7–2.7，
  差分落在真重复的区间里，没有阈值能把两群隔开。
- 版本标签（alt、WIP）不挡哈希：标签是各站按标题判的，同一个文件挂在 alt 帖下也还是
  那一个文件（实测 3 对，都是 remake 帖子附了原图）。

签名在服务端按缩略图算，缓存在 `peach-data/generated/posters/follow-faces/`，不进
ledger。`/api/follow` 只查缓存，缺的交给后台线程逐张补，下一次打开就用得上；
浏览器不为这件事下载任何图片。每个地址只取一次：视频首帧读 `/follow-cover` 的磁盘
缓存，不联网；直连图床的缩略图浏览器自己取过，服务端为算签名再取一次，并且经来源的
按站冷却闸门，站点限流时先停下，不跟浏览抢额度。
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import threading
import time
import urllib.parse
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .fsutil import atomic_write_bytes
from .http import HttpRequest, HttpTransport, public_https_url, resolves_publicly
from .user_agent import USER_AGENT

LOGGER = logging.getLogger(__name__)

#: 时长都已知时，dHash 相差不超过这么多位、色块最大格差不超过这么多，算同一张。
#: 依据见 `docs/REUSE.md`「关注卡翻卡去重」的真实样本分布：同一段视频的两份格差在 1.3
#: 以内，同站时长相同的不同版本最小 18.7，不同的两个帖子最小 12.7。
FACE_DISTANCE = 6
COLOR_DISTANCE = 4.0
#: 有一边时长未知时只认几乎一致的画面：同一文件的两份格差几乎全是 0，
#: 同一帖子在两个归档站的两份不超过 2.7，已知的最近一对不同版本是 6.3。
STRICT_FACE_DISTANCE = 2
STRICT_COLOR_DISTANCE = 3.0
#: 色块边长。8×8 足够分出换了颜色的那一块，缓存每张只多 192 字节。
GRID_SIZE = 8
#: 两段时长相差不超过这么多秒，才可能是同一段。
DURATION_TOLERANCE = 1.0
#: 卡片上会出现的媒体类型。
MEDIA_KINDS = frozenset({"image", "video"})
#: 每张卡下发几张彼此不同的翻卡画面。界面最多翻 9 张，多给一些是给图片视图筛图片留余量。
MAX_FACES = 24

_CACHE_VERSION = "face-v1"
#: 取不到的缩略图隔多久再问一次。被来源限流挡回来的不算取不到，冷却过后就重排。
RETRY_SECONDS = 24 * 3600
#: 一张缩略图最多读多少字节。缩略图一般几十 KB，超过这个数多半是原图，不值得为一枚哈希读。
MAX_IMAGE_BYTES = 4 << 20
FETCH_TIMEOUT = 15.0
#: 同一主机两次请求的最小间隔。只有一个后台线程，这条就是它对每个站的全部压力。
HOST_INTERVAL = 0.5
#: 待补队列的上限。满了就丢，下一次打开页面会重新排上。
MAX_PENDING = 20000
#: 补完多少张落一次盘。
FLUSH_EVERY = 25

_COVER_ROUTE = "/follow-cover"


def _host(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").casefold()


@dataclass(frozen=True)
class Signature:
    """一张缩略图的画面签名：dHash 与 8×8 RGB 色块。"""
    fingerprint: int
    grid: bytes

    def encode(self) -> str:
        return f"{self.fingerprint:016x}{self.grid.hex()}"

    @classmethod
    def decode(cls, text: str) -> "Signature | None":
        try:
            grid = bytes.fromhex(text[16:])
            fingerprint = int(text[:16], 16)
        except ValueError:
            return None
        return cls(fingerprint, grid) if len(grid) == GRID_SIZE * GRID_SIZE * 3 else None

    @classmethod
    def of(cls, image: Image.Image) -> "Signature":
        # 与封面比对同一份 dHash。延迟导入：那个模块连着整套社区来源，只有算签名时才用得上。
        from .community_catalog import fingerprint
        grid = image.convert("RGB").resize((GRID_SIZE, GRID_SIZE), Image.Resampling.BOX)
        return cls(fingerprint(image), grid.tobytes())

    def distances(self, other: "Signature") -> tuple[int, float]:
        """（dHash 汉明距离，色块逐格 RGB 平均差的最大值）。"""
        cells = max((sum(abs(self.grid[cell + channel] - other.grid[cell + channel])
                         for channel in range(3)) / 3
                     for cell in range(0, len(self.grid), 3)), default=0.0)
        return (self.fingerprint ^ other.fingerprint).bit_count(), cells


@dataclass(frozen=True)
class Face:
    """一份媒体的比对依据。`content` 是文件内容哈希（`算法:十六进制`），取不到为 None。"""
    provider: str
    url: str
    duration: float | None
    signature: Signature | None
    content: str | None = None
    origin: tuple[str, int | None] | None = None


def _duration(value) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def same_file(one: Face, other: Face) -> bool:
    """两份的文件内容哈希都已知且相同。"""
    return one.content is not None and one.content == other.content


def same_upload(one: Face, other: Face) -> bool:
    """同站的同一帖子及同一媒体序号，独立于收录它的关注来源。"""
    if one.content is not None and other.content is not None and one.content != other.content:
        return False
    return one.origin is not None and one.provider == other.provider and one.origin == other.origin


def _upload_origin(member: dict, slot: int | None) -> tuple[str, int | None] | None:
    """站内全局编号与归档站的服务/作者范围分别标识一篇帖子。"""
    external_id = str(member.get("external_id") or "")
    if not external_id:
        return None
    if member.get("provider") in {"rule34video", "rule34xxx", "rule34paheal", "fanbox", "f95zone"}:
        return external_id, slot
    page = str(member.get("url") or "")
    return (f"{page}\0{external_id}", slot) if page else None


def same_face(one: Face, other: Face) -> bool:
    if same_file(one, other) or same_upload(one, other):
        return True
    if one.url and one.url == other.url:
        return True
    if one.signature is None or other.signature is None:
        return False
    distance, color = one.signature.distances(other.signature)
    if one.duration is not None and other.duration is not None:
        return (abs(one.duration - other.duration) <= DURATION_TOLERANCE
                and distance <= FACE_DISTANCE and color <= COLOR_DISTANCE)
    return distance <= STRICT_FACE_DISTANCE and color <= STRICT_COLOR_DISTANCE


def _exact(one: Face, other: Face) -> bool:
    return same_file(one, other) or same_upload(one, other) or bool(one.url and one.url == other.url)


def face_clusters(faces: list[Face]) -> list[int]:
    """每张缩略图归到哪一簇，否则自成一簇。

    同一文件、同一缩略图地址与簇里任何一份相同都进那一簇；画面相近只和簇首比，免得相近的
    一张接一张串下去。帖子自身那份的哈希取的是视频，缩略图却是帖里那张图：别的站上同一张图
    只能凭哈希认到帖里那一份，只比簇首就会把同一张图翻两次。
    """
    clusters: list[list[Face]] = []
    assigned: list[int] = []
    for face in faces:
        cluster = next((index for index, members in enumerate(clusters)
                        if same_face(members[0], face)
                        or any(_exact(member, face) for member in members[1:])), None)
        if cluster is None:
            cluster = len(clusters)
            clusters.append([])
        clusters[cluster].append(face)
        assigned.append(cluster)
    return assigned


def _joins_by_file(members: list[Face], face: Face) -> bool:
    """按内容哈希并进这一簇：簇里有同一个文件。"""
    return any(same_file(member, face) or same_upload(member, face) for member in members)


def _joins_by_face(members: list[Face], face: Face) -> bool:
    """按画面并进这一簇：只认跨站，且簇里还没有这个站的任何一份，只和簇首比。"""
    return (all(member.provider != face.provider for member in members)
            and same_face(members[0], face))


def media_clusters(faces: list[Face]) -> list[int]:
    """合并的媒体归成几个不同的媒体。

    同站同一帖子同一媒体序号、或同一文件（内容哈希相同）都并成一个。按画面只并别的站上的同一张，
    一簇里每个站至多一份：同站画面相近的可能是差分或另一个分辨率的文件，借着跨站那一份
    把同站的两条串成一簇，计数就把它们吞掉了。
    """
    clusters: list[list[Face]] = []
    assigned: list[int] = []
    for face in faces:
        cluster = next((index for index, members in enumerate(clusters)
                        if _joins_by_file(members, face) or _joins_by_face(members, face)), None)
        if cluster is None:
            cluster = len(clusters)
            clusters.append([])
        clusters[cluster].append(face)
        assigned.append(cluster)
    return assigned


def _members(group: dict) -> list[dict]:
    seen: set = set()
    members = []
    for member in (group.get("primary"), *(group.get("variants") or ()),
                   *(group.get("duplicates") or ())):
        if not member or member.get("id") in seen:
            continue
        seen.add(member.get("id"))
        members.append(member)
    return members


def _signatures(index: "FollowFaceIndex | None", faces: list[Face]) -> dict[str, Signature]:
    """查这些缩略图的已知签名；带上各自的来源，缺的按来源的冷却闸门去补。"""
    if index is None:
        return {}
    return index.lookup([face.url for face in faces],
                        {face.url: face.provider for face in faces})


def annotate_group(group: dict, index: "FollowFaceIndex | None" = None,
                   hashes: dict[tuple[int, int | None], str] | None = None) -> dict:
    """给一组 `/api/follow` 载荷写上翻卡与计数要用的那几个字段，原地改并返回。

    - 每个带缩略图的成员与媒体加 `face`：组内编号，编号相同就是同一个画面。
    - 组上加 `stack`：`media` 是去重后不同媒体的个数，`copies` 是合并了几份，
      `kind` 是 `video`／`image`／`mixed`，`faces` 是彼此不同的翻卡画面。
      合并的媒体不到两份时 `stack` 为 None。

    合并的媒体：成员带媒体清单的数清单里每一张，没有清单的数成员自己。
    `hashes` 是各份的文件内容哈希，键是（成员 id，媒体序号；成员本身为 None），
    由调用方从原始地址解析；原始地址不在载荷里。
    """
    hashes = hashes or {}
    thumbs: list[tuple[dict, Face]] = []
    merged: list[tuple[dict, Face, str]] = []
    for member in _members(group):
        provider = str(member.get("provider") or "")
        ident = member.get("id")
        own = Face(provider, str(member.get("thumb_url") or ""), _duration(member.get("duration")),
                   None, hashes.get((ident, None)), _upload_origin(member, None))
        if own.url:
            thumbs.append((member, own))
        media = [entry for entry in member.get("media_items") or ()
                 if entry.get("media_kind") in MEDIA_KINDS]
        for entry in media:
            face = Face(provider, str(entry.get("thumb_url") or ""), None, None,
                        hashes.get((ident, entry.get("index"))),
                        _upload_origin(member, entry["index"]) if "index" in entry else None)
            if face.url:
                thumbs.append((entry, face))
            merged.append((entry, face, str(entry["media_kind"])))
        if not media and member.get("media_kind") in MEDIA_KINDS:
            merged.append((member, own, str(member["media_kind"])))
    if len(merged) < 2:
        group["stack"] = None
        return group
    signatures = _signatures(index, [face for _, face in thumbs])

    def signed(face: Face) -> Face:
        signature = signatures.get(face.url) if face.url else None
        return replace(face, signature=signature) if signature is not None else face

    faces = [signed(face) for _, face in thumbs]
    for (payload, _), cluster in zip(thumbs, face_clusters(faces)):
        payload["face"] = cluster
    media_faces = [signed(face) for _, face, _ in merged]
    kinds = {kind for _, _, kind in merged}
    distinct: list[dict] = []
    seen_faces: set[int] = set()
    for payload, _, kind in merged:
        face = payload.get("face")
        if face is None or face in seen_faces or len(distinct) >= MAX_FACES:
            continue
        seen_faces.add(face)
        distinct.append({"thumb_url": payload["thumb_url"], "face": face, "media_kind": kind})
    group["stack"] = {
        "media": len(set(media_clusters(media_faces))),
        "copies": len(merged),
        "kind": kinds.pop() if len(kinds) == 1 else "mixed",
        "faces": distinct,
    }
    return group


class FollowFaceIndex:
    """缩略图地址到画面签名的本机缓存。查不到的排进队列，由一个后台线程慢慢补。

    缓存是一份 JSON：键是地址的 SHA-256，值是（十六进制签名或空串，取得时刻）。空串表示
    这一次没取到，`RETRY_SECONDS` 之后再问。
    """

    def __init__(self, root: Path, transport: HttpTransport, *,
                 cover_root: Path | None = None, gate=None, clock=time.time,
                 monotonic=time.monotonic, sleeper=time.sleep, background: bool = True):
        self.root = Path(root)
        self.path = self.root / f"{_CACHE_VERSION}.json"
        self.transport = transport
        self.cover_root = Path(cover_root) if cover_root is not None else None
        #: 按站冷却的闸门（`FollowMediaResolver`，浏览、播放与检查共用那一份）。知道来源的
        #: 缩略图经它去取：来源在冷却就先不取，被拒就让整站冷却，这一张留到下次再排。
        self.gate = gate
        self._providers: dict[str, str] = {}
        self._clock = clock
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._background = background
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._pending: deque[str] = deque()
        self._queued: set[str] = set()
        self._next_at: dict[str, float] = {}
        self._dirty = 0
        self._worker: threading.Thread | None = None
        self._entries = self._load()

    @staticmethod
    def _key(url: str) -> str:
        # 帖子里点名其他视频的首帧（`/follow-cover?id=N&media=M`）键上加 `slot:` 前缀：
        # 同一地址不带前缀的那一条签名取自第一个视频的首帧，不是这一段的画面。
        if url.startswith(_COVER_ROUTE + "?") and "media=" in url:
            url = f"slot:{url}"
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _load(self) -> dict[str, list]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        entries = payload.get("entries") if isinstance(payload, dict) else None
        return entries if isinstance(entries, dict) else {}

    def _save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            data = json.dumps({"version": _CACHE_VERSION, "entries": self._entries},
                              separators=(",", ":")).encode("utf-8")
            self._dirty = 0
        try:
            atomic_write_bytes(self.path, data)
        except OSError as exc:
            LOGGER.warning("follow face cache write failed: %s", exc)

    def fetchable(self, url: str) -> bool:
        if url.startswith(_COVER_ROUTE + "?"):
            return self.cover_root is not None
        return public_https_url(url)

    def lookup(self, urls: Iterable[str],
               providers: Mapping[str, str] | None = None) -> dict[str, Signature]:
        """已知签名按地址返回；未知且能取的排进队列，这一次不等它。

        `providers` 给出各地址属于哪个来源，取图时按它过来源的冷却闸门。
        """
        providers = providers or {}
        known: dict[str, Signature] = {}
        now = self._clock()
        with self._lock:
            for url in urls:
                if not url or url in known:
                    continue
                entry = self._entries.get(self._key(url))
                signature = Signature.decode(entry[0]) if entry and entry[0] else None
                if signature is not None:
                    known[url] = signature
                    continue
                if entry and now - float(entry[1]) < RETRY_SECONDS:
                    continue
                if (url not in self._queued and len(self._pending) < MAX_PENDING
                        and self.fetchable(url)):
                    self._queued.add(url)
                    self._pending.append(url)
                    if providers.get(url):
                        self._providers[url] = providers[url]
        if self._pending and self._background:
            self._ensure_worker()
            self._wake.set()
        return known

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._run, name="PeachFollowFaces",
                                            daemon=True)
            self._worker.start()

    def _run(self) -> None:
        while True:
            self._wake.wait(30)
            self._wake.clear()
            self.drain()

    def drain(self, limit: int | None = None) -> int:
        """把队列里的补完（最多 `limit` 张），返回这一趟处理了几张。"""
        done = 0
        while limit is None or done < limit:
            with self._lock:
                if not self._pending:
                    break
                url = self._pending.popleft()
                self._queued.discard(url)
                provider = self._providers.pop(url, "")
            if self._cooling(provider):
                # 来源在冷却：不取也不记失败，下次打开页面会重新排上。
                continue
            signature = self._signature(url, provider)
            if signature is None and self._cooling(provider):
                # 这一张被拒、整站进了冷却：不是「取不到」，不该等一天才重试。
                continue
            with self._lock:
                self._entries[self._key(url)] = [
                    signature.encode() if signature is not None else "", int(self._clock())]
                self._dirty += 1
                flush = self._dirty >= FLUSH_EVERY
            done += 1
            if flush:
                self._save()
        self._save()
        return done

    def _cooling(self, provider: str) -> bool:
        return bool(self.gate is not None and provider and self.gate.cooling(provider))

    def _read(self, url: str, provider: str = "") -> bytes | None:
        if url.startswith(_COVER_ROUTE + "?"):
            query = urllib.parse.parse_qs(url.split("?", 1)[1])
            ident = query.get("id", [""])[0]
            media = query.get("media", [""])[0]
            if (not ident.isdigit() or (media and not media.isdigit())
                    or self.cover_root is None):
                return None
            # 视频首帧由 `/follow-cover` 生成，卡片显示过就在缓存里；还没生成的下次再说。
            # 文件名是 `<id>-<指纹>.jpg`，点名的其他视频是 `<id>-m<序号>-<指纹>.jpg`
            # （`follow_covers`）；指纹是十六进制，不会以 m 开头。写到一半的临时文件
            # （`*.tmp.jpg`）同样匹配这个模式，要跳过。
            prefix = f"{ident}-m{media}-" if media else f"{ident}-"
            cached = [path for path in self.cover_root.glob(f"{prefix}*.jpg")
                      if not path.name.endswith(".tmp.jpg")
                      and (media or not path.name[len(prefix):].startswith("m"))]
            return max(cached, key=lambda path: path.stat().st_mtime).read_bytes() if cached else None
        host = _host(url)
        if not resolves_publicly(host):
            return None
        wait = self._next_at.get(host, 0.0) - self._monotonic()
        if wait > 0:
            self._sleeper(wait)
        self._next_at[host] = self._monotonic() + HOST_INTERVAL
        request = HttpRequest("GET", url, {"User-Agent": USER_AGENT, "Accept": "image/*"})
        response = (self.gate.fetch(provider, request, FETCH_TIMEOUT, MAX_IMAGE_BYTES)
                    if self.gate is not None and provider
                    else self.transport(request, FETCH_TIMEOUT, MAX_IMAGE_BYTES))
        return response.body if response.status == 200 else None

    def _signature(self, url: str, provider: str = "") -> Signature | None:
        try:
            data = self._read(url, provider)
            if not data:
                return None
            with Image.open(io.BytesIO(data)) as image:
                image.seek(0)
                image.load()
                return Signature.of(image)
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            LOGGER.debug("follow face signature unavailable for %s: %s", _host(url), exc)
            return None
        except Exception as exc:  # noqa: BLE001 - 传输层的各种网络异常都只意味着这一次没取到
            LOGGER.debug("follow face fetch failed for %s: %s", _host(url), exc)
            return None
