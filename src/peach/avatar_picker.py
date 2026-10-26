"""换头像：列出这个人可选的图，取来其中一张，装上去。

自动挑选按来源优先级走（`gfriends.quality_key`），而那个顺序回答的是「先试哪一张」，
不是「哪一张适合当头像」。实测两类偏差都真实存在：葵つかさ排第一的是一张压着书名的
写真封面，而她的经纪事务所那张正脸原图排第六；横宫七海更直接——她的头像是作品封面
兜底装上的，gfriends 里那 9 张人像因为「文件已存在」从来没被看过一眼。

所以这里的立场是：自动挑一张先用着，人随时能换成别的。可换的来源有四种——图库里
同名的其他候选、这个人自己作品里的画面、本机的图片文件、一个 https 地址。

四路都可以先框出一块再装（`crop` 那个参数），框按源图像素给，服务端切出新字节。
作品那一路必须框：封面是横版封套，九宫格是十六比九的画面，里面常常还不止一个人，
整张装进圆框只会得到一块背景。其余三路多是人像，图库里却常是全身竖图，脸只占
一小块，所以也先进框选（默认框落在检出的脸上，`framed`），整张装照样成立。

**换过的图都留着。** 每一张取到的图都按内容哈希进候选缓存，换回去只是再装一次，
不重新下载；被顶下来的那张也在里面，不会因为换了一次就永远找不回来。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from . import gfriends, images, jav_poster_crop
from .avatar_cover_face import WHOLE_COVER_PROVIDERS, face_square
from .avatar_face import read_sidecar
from .catalog_rules import normalise_code_key
from .entities import is_short_single_name
from .entity_classification import work_portrait_predicate
from .western_artwork import artwork_cast_size, artwork_key
from .kanji import fold_glyphs
from .avatar_provider import (
    AvatarCandidateCache, InspectedAvatar, POLICY_VERSION, inspect_avatar,
    install_entity_avatar, provenance_now,
)
from .http import HttpRequest, HttpTransport, public_https_url, resolves_publicly

#: 一次最多给页面列这么多张。同名候选最多的人有十几张，再多就不是选图而是翻图册了。
MAX_CHOICES = 40
#: 候选缓存按来源分目录（`gfriends/`、`social/`、`babepedia/`……），各有自己的
#: `objects/`、`requests/` 与 `evidence/`。取过的图要跨目录找：同一个人的几张图
#: 常常来自不同来源。图库索引和图库对象同处 `gfriends/`。
GFRIENDS_CACHE = "gfriends"
#: 下载一张头像的上限。图库里最大的一张 3 MB 上下，留足余量即可；这个数同时是
#: 「别人给的地址指向一个 4 GB 文件」时我们停下来的地方。
MAX_IMAGE_BYTES = 16 * 1024 * 1024
FETCH_TIMEOUT = 30
#: 证据里的 `provider` 是采集路线的代号，摆到格子底下要换成来源本身的名字：
#: `jae:actress.html#joyu117` 这种串回答的是「批处理怎么再找到它」，不是「这张图哪来的」。
#: 表里没有的按代号原样显示——新来源接进来时先露一个代号，好过盖成一个含混的
#: 「其他」：看到代号的人才知道该来这里补一行。
SOURCE_NAMES = {
    "gfriends": "图库",
    "jae": "展会名录",
    "social-web": "社交主页",
    "follow-content": "在线作品",
    "cover": "作品封面",
    "cover-fallback": "作品封面",
    "cover-face": "封面人脸",
    "babepedia": "Babepedia",
    "kmib": "官网",
    "picker": "自己挑的",
    "history": "取过的图",
    "asset": "作品画面",
    "code-cover": "番号封面",
    "url": "地址",
    "upload": "本机",
}
#: 按番号取来的封面放在这个来源目录里。它不属于任何人，所以只存对象、不写证据：
#: 证据按 `performer-<id>-*` 存，写了就会冒充成某个人「取过的图」。
CODE_COVER_CACHE = "code-cover"
#: 作品那一组一次最多列这么多部。这一组是拿来找一张能框出脸的画面的，不是作品列表；
#: 一个人的作品动辄上百部，全列出来就把图库候选挤到看不见的地方去了。
MAX_ASSET_CHOICES = 12
#: 九宫格的格数。底图可以在这九格加封面之间换，框选在换底图之后重来。
SHEET_CELLS = 9
#: 手填地址取来的图放在这个来源目录里，按地址存。框选、确认都从这里读，不再出网；
#: 装上去时来源记录的 `provider` 也是它，整张装的那一份和取来的落在同一条记录上。
ADDRESS_CACHE = "url"
#: 发布账号没有本人身份依据时，作品画面格子上的说明。
STAND_IN_NOTE = "代表作画面，非本人"


class PickerError(RuntimeError):
    """这一次换不成，原因可以直接给用户看。"""


@dataclass(frozen=True)
class Choice:
    """一个可选项。`ref` 是前端唯一回递的东西。"""

    ref: str
    source: str
    label: str
    width: int = 0
    height: int = 0
    detail: str = ""
    #: 这一格是按哪个名字从图库里找到的。只有一个名字命中时留空。
    found_by: str = ""
    current: bool = False
    #: 这一格必须先框一块再装。作品画面是横图，整张装进圆框只剩一块背景。
    crop: bool = False
    #: 框选时可以换的底图，按 `ref` 给。空表示这一格只有它自己那一张。
    bases: tuple[str, ...] = ()
    #: 封面上该取景的那一块，源图像素、右下开区间（`cover_focus`）。格子围着它取景，
    #: 框选围着它落默认框；None 时两边都居中。
    focus: tuple[int, int, int, int] | None = None
    #: 这部作品有几位演员（`cast_sizes`）。多于一位时封面上那张脸多半是领衔的另一位，
    #: `focus` 不围着它取景；0 是不知道（不是她馆藏里的作品）。
    cast: int = 0
    #: 封面那张底图的版本（`cover_version`）。封面会被补高清、重探原地替换，地址不变；
    #: 预览地址带上它才不会显示旧图，交框时回递它，服务端才认得出框是在哪一张上画的。
    #: 图库与历史按内容寻址，九宫格从视频抽帧，都不会原地变，是空串。
    version: str = ""

    def as_dict(self) -> dict:
        focus = (dict(zip(("x0", "y0", "x1", "y1"), self.focus))
                 if self.focus else None)
        return {"ref": self.ref, "source": self.source, "label": self.label,
                "width": self.width, "height": self.height,
                "detail": self.detail, "found_by": self.found_by,
                "current": self.current, "crop": self.crop,
                "bases": list(self.bases), "focus": focus, "cast": self.cast,
                "version": self.version}


def cover_version(cover: Path | None) -> str:
    """一张封面文件此刻的版本：修改时间的十六进制纳秒。文件不在就是空串。"""
    if cover is None:
        return ""
    try:
        return f"{cover.stat().st_mtime_ns:x}"
    except OSError:
        return ""


def _check_version(cover: Path | None, version: str | None) -> None:
    """框是在 `version` 那一张封面上画的；封面此后被换过，同一组坐标就落在别的地方。"""
    if version is not None and cover_version(cover) != version:
        raise PickerError("这张封面刚换过，返回备选图片重新框一次")


def name_chain(connection: sqlite3.Connection, entity_id: int) -> list[str]:
    """查图库用的名字，按匹配次序：规范名在前，别名在后。

    别名不是锦上添花：大陆简体与日文字体在图库里是两个不同的键（`横宫七海` 与
    `横宮七海`），只拿规范名去查，汉字简化过的那些人一个也找不到。

    只有名、没有姓的短别名不进链（`entities.is_short_single_name`）：`茜`、`舞香` 在图库
    里各是另外几个人，图库只命中一张时补头像不比脸就装。规范名本身短的照查，别名只是它
    换了字形的（`美优` 的 `美優`）也照查，两者在图库里本来就是同一个人的两个键。
    """
    names: list[str] = []
    row = connection.execute("SELECT canonical_name FROM entity WHERE id=?",
                             (int(entity_id),)).fetchone()
    canonical = str(row[0]) if row and row[0] else ""
    if canonical:
        names.append(canonical)
    same_as_canonical = fold_glyphs(gfriends.normalized(canonical))
    names += [str(alias) for (alias,) in connection.execute(
        "SELECT alias FROM entity_alias WHERE entity_id=?", (int(entity_id),))
        if alias and (not is_short_single_name(str(alias))
                      or fold_glyphs(gfriends.normalized(str(alias))) == same_as_canonical)]
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        key = gfriends.normalized(name)
        if key and key not in seen:
            seen.add(key)
            ordered.append(name)
    return ordered


def installed_digest(avatar_root: Path, kind: str, entity_id: int) -> str:
    """当前装着那张图的哈希，用来在候选里把它标出来。读不到就是空。"""
    from .previews import entity_image_key

    path = avatar_root / f"{entity_image_key(kind, int(entity_id))}.img"
    try:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError:
        return ""


def cast_sizes(connection: sqlite3.Connection, entity_id: int) -> dict[str, int]:
    """她名下每个番号的作品有几位演员，番号按 `normalise_code_key`。

    与 `avatar_cover_face.single_works` 同一个口径：只数 `performer` 角色。同一个
    番号有几条 asset 行时取人数最多的那一行。
    """
    sizes: dict[str, int] = {}
    for code, cast in connection.execute(
            "SELECT a.code,(SELECT count(DISTINCT o.entity_id) FROM asset_entity o"
            " WHERE o.asset_id=a.id AND o.role='performer')"
            " FROM asset a JOIN asset_entity ae ON ae.asset_id=a.id"
            " WHERE ae.entity_id=? AND ae.role='performer' AND coalesce(a.code,'')<>''",
            (int(entity_id),)):
        key = normalise_code_key(code)
        if key:
            sizes[key] = max(sizes.get(key, 0), int(cast or 0))
    return sizes


def _focus_face(face: dict | None, cast: int) -> dict | None:
    """合演作品的封面不按边车里那张脸取景：DVAJ-495 那张是 15 人里领衔的葵つかさ。"""
    return face if cast <= 1 else None


def _history(providers_root: Path, entity_id: int, current: str,
             cover_root: Path | None = None,
             casts: dict[str, int] | None = None) -> list[Choice]:
    """这个人取过的图。证据文件按 `performer-<id>-<sha>.json` 存，天然是一份历史。

    这里给的是「换回去不用重下」的那一批：装过又被顶掉的、批处理下过但没装的，
    都在候选缓存里按内容寻址躺着。跨来源目录找——取过的图未必都来自图库。

    两种记录不列出来：对象已经不在缓存里的（列了也只能点出一句「不在本机缓存里」），
    和整张只有一个颜色的（`images.is_flat`）。后者是来源取不到人像时给的占位底色，
    尺寸格式都合规，摆进候选里就是一块白格子。

    批处理整张存下的作品封面（`WHOLE_COVER_PROVIDERS`）和作品画面同形：横版封套，
    整张装进圆框只剩一块背景。它们同样先框再装，按证据里的番号在封面边车上取景。
    """
    out: list[Choice] = []
    seen: set[str] = set()
    for path in sorted(providers_root.glob(
            f"*/evidence/performer-{int(entity_id)}-*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        digest = str(record.get("sha256") or "")
        # 同一张图可能在几个来源目录里各留了一份证据——摆出来是两个一模一样的格子。
        if not digest or digest in seen:
            continue
        seen.add(digest)
        body = _object_bytes(providers_root, digest)
        if body is None or images.is_flat(body):
            continue
        provider = str(record.get("provider") or "")
        label = SOURCE_NAMES.get(provider, provider or "取过的图")
        width, height = int(record.get("width") or 0), int(record.get("height") or 0)
        whole = provider in WHOLE_COVER_PROVIDERS
        focus, cast = None, 0
        if whole:
            key = normalise_code_key(record.get("external_id"))
            label = f"{label} {key}".strip()
            cast = (casts or {}).get(key, 0)
            cover = Path(cover_root) / f"{key}.jpg" if cover_root is not None and key else None
            if width and height:
                focus = cover_focus(key, cover,
                                    _focus_face(read_sidecar(cover) if cover else None, cast),
                                    width, height)
        out.append(Choice(
            ref=f"sha256:{digest}", source="history", label=label,
            width=width, height=height,
            detail=str(record.get("upstream_url") or ""),
            current=digest == current, crop=whole, focus=focus, cast=cast))
    return out


def asset_artwork(connection: sqlite3.Connection, cover_root: Path,
                  entity_id: int) -> list[Choice]:
    """这个人的作品里能拿来框头像的那些画面。

    一部作品只占一格，格上那张是它的封面（没有就是九宫格正中那一格）；点开之后
    底图可以在封面和九宫格九格之间换。列的是作品而不是每一张图：一个人几十部作品
    乘以十张图，摆出来是几百个格子，而人要找的是「哪一部里有一张正脸」。

    没有封面也没铺过九宫格的作品不列：那种格子点开是一片空白。

    同一个番号分了几段、或在两个目录各有一份时，账本里是几条 asset 行，封面却是同一
    张：只留文件最大的那一份，其余几行不再各占一格。

    **封面像素多的排前面**，只有九宫格的排在所有封面之后。同一个人的封面有 3360×1890
    的官方原图，也有 276×154 的缩略图；框出来的头像清不清楚只看底图有多少像素。格上的
    宽高就是封面的，页面照常把它标出来。

    **合演作品标出人数，不按封面上那张脸取景**（`_focus_face`）：封面人脸边车记的是最大那
    张脸，合集里那多半是领衔的另一位。格子照列，她自己的脸常常在九宫格里。

    发布账号没有本人身份依据（不过 `work_portrait_predicate`）时格子照列，说明里写明是
    代表作画面、非本人：挑不挑由人决定（ADR-0099）。
    """
    stand_in = connection.execute('SELECT 1 FROM entity e WHERE e.id=? AND ' +
                                  work_portrait_predicate(connection),
                                  (int(entity_id),)).fetchone() is None
    casts = cast_sizes(connection, entity_id)
    rows = connection.execute(
        "SELECT a.id,a.code,COALESCE(NULLIF(a.catalog_title,''),a.name),a.snapshot_path "
        "FROM asset_entity ae JOIN asset a ON a.id=ae.asset_id "
        "WHERE ae.entity_id=? AND a.medium='video' "
        "ORDER BY a.size DESC",
        (int(entity_id),),
    ).fetchall()
    found: list[tuple[int, int, Choice]] = []
    seen_codes: set[str] = set()
    for order, (asset_id, code, title, snapshot) in enumerate(rows):
        key = artwork_key(asset_id, code)
        if key:
            if key in seen_codes:
                continue
            seen_codes.add(key)
        cover = Path(cover_root) / f"{key}.jpg"
        size = images.measure_image_file(cover) if key else None
        has_sheet = bool(snapshot)
        if size is None and not has_sheet:
            continue
        bases = ([f"asset:{int(asset_id)}:cover"] if size else [])
        if has_sheet:
            bases += [f"asset:{int(asset_id)}:cell{cell}" for cell in range(SHEET_CELLS)]
        width, height = size or (0, 0)
        cast = casts.get(key, 0) if code else connection.execute(
            "SELECT count(DISTINCT entity_id) FROM asset_entity WHERE asset_id=? AND role='performer'",
            (int(asset_id),)).fetchone()[0]
        cast = artwork_cast_size(cover, cast)
        found.append((width * height, order, Choice(
            ref=bases[0], source="asset",
            label=str(code or title or f"作品 {asset_id}"),
            width=width, height=height,
            detail=" · ".join(filter(None, (STAND_IN_NOTE if stand_in else "", str(title or "")))),
            crop=True, bases=tuple(bases),
            focus=cover_focus(key, cover, _focus_face(read_sidecar(cover), cast), width, height)
            if size else None, cast=cast, version=cover_version(cover) if size else "")))
    found.sort(key=lambda item: (-item[0], item[1]))
    return [choice for _area, _order, choice in found[:MAX_ASSET_CHOICES]]


def cover_focus(key: str, cover: Path | None, face: dict | None,
                width: int, height: int) -> tuple[int, int, int, int] | None:
    """一张封面上该围着取景的那一块：检出脸就是脸周围的方图，没检出脸的横版封套是
    正封，别的是 None。

    脸那一块和批处理从封面截头像是同一块（`avatar_cover_face.face_square`），弹层里
    看到的就是批处理真会装上去的样子。正封交给 `jav_poster_crop`：封套版式的判据只有
    那一份，边车里有算过（或人框过）的框就用它，没有就按正封比例从右缘量回去。
    """
    square = face_square(face, width, height)
    if square or not jav_poster_crop.crops_to_portrait(key):
        return square
    sidecar = jav_poster_crop.read_sidecar(cover) if cover is not None else None
    box = ((jav_poster_crop.projection(sidecar)
            if jav_poster_crop.is_current(sidecar, width, height) else None)
           or jav_poster_crop.front_panel_box(width, height, code=key))
    if box.get("method") == jav_poster_crop.NONE:
        return None
    return int(box["x0"]), int(box["y0"]), int(box["x1"]), int(box["y1"])


def _code_cover_url(key: str) -> str:
    return f"peach:code-cover/{key}"


def _code_cover_bytes(key: str, cover_root: Path | None,
                      providers_root: Path) -> tuple[bytes | None, Path | None]:
    """这个番号的封面在本机哪儿有：馆藏封面目录优先，其次是按番号取过的那一份。

    馆藏里那张连同它的路径一起给，它旁边的人脸与正封边车省一次检测；取来的那份没有。
    """
    if cover_root is not None:
        path = Path(cover_root) / f"{key}.jpg"
        try:
            return path.read_bytes(), path
        except OSError:
            pass
    return AvatarCandidateCache(providers_root / CODE_COVER_CACHE).lookup(_code_cover_url(key)), None


def code_cover(code: str, cover_root: Path | None, providers_root: Path,
               fetch: Callable[[str], bytes],
               probe: Callable[[bytes], dict | None]) -> Choice:
    """按番号拿一张封面来框头像，番号不必在馆藏里。

    同一个人的作品未必都在本机：图库和本机作品都给不出正脸时，人常常记得她哪一部的
    封面拍得好。本机封面目录里有就用它；没有就由 `fetch` 去官方渠道取最大的那张，
    取到的按番号进候选缓存，框选、确认时都从缓存读，不再出网。
    """
    key = normalise_code_key(code)
    if not key:
        raise PickerError("认不出这个番号")
    body, cover = _code_cover_bytes(key, cover_root, providers_root)
    if body is None:
        body = fetch(key)
        AvatarCandidateCache(providers_root / CODE_COVER_CACHE).store(
            _code_cover_url(key), body, accept_image(body))
    size = images.measure_image_size(body)
    if size is None:
        raise PickerError("这个番号的封面不是一张能识别的图片")
    face = read_sidecar(cover) if cover is not None else None
    if face_square(face, *size) is None:
        face = probe(body)
    focus = cover_focus(key, cover, face, *size)
    ref = f"cover:{key}"
    return Choice(ref=ref, source="code", label=key, width=size[0], height=size[1],
                  crop=True, bases=(ref,), focus=focus, version=cover_version(cover))


def framed(ref: str, body: bytes, probe: Callable[[bytes], dict | None], *,
           source: str, label: str) -> Choice:
    """一张人像进框选之前要知道的：转正后的尺寸，和脸周围那块方图。

    图库人像多是全身竖图，整张装进圆框脸只占一小块，所以和作品画面一样先框再装。
    默认框和批处理从封面截头像是同一块（`face_square`）；检不出脸时 `focus` 是 None，
    页面居中落框。`crop` 保持 False：这几路的图整张装也成立，框选是可选的那一步。
    """
    accept_image(body)
    body = images.upright(body)
    size = images.measure_image_size(body)
    if size is None:
        raise PickerError("这不是一张能识别的图片")
    return Choice(ref=ref, source=source, label=label, width=size[0], height=size[1],
                  bases=(ref,) if ref else (), focus=face_square(probe(body), *size))


def typed_address(url: str, providers_root: Path, fetch: Callable[[str], bytes],
                  probe: Callable[[bytes], dict | None]) -> Choice:
    """手填的地址取一次图，留进候选缓存，交回一格可以框的候选。

    地址必须已经过 `allowed_source`。取到的字节按地址存：框选时页面看的预览、探针检的
    脸和确认时切的都是这一份，不会因为两次取图之间站点换了图而对不上。
    """
    body = fetch(url)
    AvatarCandidateCache(providers_root / ADDRESS_CACHE).store(url, body, accept_image(body))
    host = urllib.parse.urlsplit(url).hostname or url
    return framed(f"url:{url}", body, probe, source="url", label=host)


def choices(connection: sqlite3.Connection, providers_root: Path,
            avatar_root: Path, kind: str, entity_id: int,
            cover_root: Path | None = None, *, deduplicate: bool = True) -> dict:
    """页面要展示的一切：图库同名候选、取过的历史、当前装着的是哪一张。

    索引只读本地缓存。联网补索引是批处理的事——为一次点击同步拉 6 MB，页面会卡在
    那里，而卡住的理由用户完全看不见。
    """
    index_dir = providers_root / GFRIENDS_CACHE
    names = name_chain(connection, entity_id)
    index = gfriends.load_index(index_dir)
    match = gfriends.candidates(index, names)
    current = installed_digest(avatar_root, kind, entity_id)
    cache = AvatarCandidateCache(index_dir)
    #: 图库候选里已经取过的那些，按内容哈希记下来。取过的图会同时以「图库某个分类」
    #: 和「这个人取过的图」两种身份出现，摆在一起就是两个一模一样的格子。留图库那
    #: 一边：`S1`、`GRAPHIS` 说得出是谁家的图，「图库」只说得出它从哪个路子来。
    taken: set[str] = set()
    items: list[Choice] = []
    # 好几个名字都命中时，每一格得说得出自己是按哪个名字找来的：找错人是这一屏唯一
    # 会出的大错，而一屏里混着两个人的图时，名字是唯一能看出来的线索。只有一个名字
    # 命中就不必说——那句话对每一格都一样，等于没说。
    tell_finder = len(match.names) > 1
    for category, filename in match.items:
        shot = cache.describe(gfriends.image_url(category, filename)) or {}
        digest = str(shot.get("sha256") or "")
        if digest:
            taken.add(digest)
        items.append(Choice(
            ref=f"gfriends:{category}/{filename}", source="gfriends",
            label=gfriends.category_label(category), detail=filename,
            found_by=match.finder.get((category, filename), "") if tell_finder else "",
            width=int(shot.get("width") or 0), height=int(shot.get("height") or 0),
            current=bool(digest) and digest == current))
    for choice in _history(providers_root, entity_id, current, cover_root,
                           cast_sizes(connection, entity_id)):
        if choice.ref.split(":", 1)[1] not in taken:
            items.append(choice)
    # 在用的那张排第一。它是这一屏唯一的参照物——别的候选好不好，是跟它比出来的；
    # 排在第十二个就得先把它找出来才能开始比。排序是稳定的，其余顺序不动。
    items.sort(key=lambda choice: not choice.current)
    # 作品画面接在人像候选后面，而且不跟它们抢 `MAX_CHOICES` 那个名额：这一组要回答
    # 的是「图库和历史里都没有合用的时候去哪找」，被截在名额外面就等于这条路不存在。
    listed = items[:MAX_CHOICES]
    if cover_root is not None:
        listed += asset_artwork(connection, cover_root, entity_id)
    if deduplicate:
        listed = _distinct_choices(listed, providers_root, cover_root)
    age = gfriends.index_age(index_dir)
    return {
        "kind": kind, "entity_id": int(entity_id),
        "names": names, "matched_names": list(match.names),
        "choices": [choice.as_dict() for choice in listed],
        "index_age_hours": round(age / 3600, 1) if age is not None else None,
        "index_stale": age is None or age > gfriends.INDEX_MAX_AGE_SECONDS,
    }


def _distinct_choices(items: list[Choice], providers_root: Path,
                      cover_root: Path | None) -> list[Choice]:
    """同一张本机图片及其默认裁片共用一格，作品底图仍可进入框选。

    只比缓存字节与同一裁切函数的输出哈希，不联网、不做相似人脸猜测。
    图库的整图框选会重编 JPEG，所以它与来源原图的字节哈希可以不同。
    """
    groups: list[tuple[Choice, set[str]]] = []
    rank = {"asset": 0, "gfriends": 1, "history": 2}
    for choice in items:
        body = None
        if choice.source == "gfriends":
            category, _, filename = choice.ref.partition(":")[2].partition("/")
            body = AvatarCandidateCache(providers_root / GFRIENDS_CACHE).lookup(
                gfriends.image_url(category, filename))
        elif choice.source == "history":
            body = _object_bytes(providers_root, choice.ref.partition(":")[2])
        elif choice.source == "asset" and cover_root is not None and choice.ref.endswith(":cover"):
            # 作品组已核对归属；文件名取列表中真实番号，不从客户端 ref 推断路径。
            key = artwork_key(int(choice.ref.split(":")[1]), choice.label)
            path = Path(cover_root) / f"{key}.jpg"
            try:
                body = path.read_bytes()
            except OSError:
                pass
        keys = {f"ref:{choice.ref}"}
        if body is not None:
            upright = images.upright(body)
            box = choice.focus
            if box is None:
                keys.add(hashlib.sha256(body).hexdigest())
                size = images.measure_image_size(upright)
                box = (0, 0, *size) if size else None
            cut = images.crop_to_box(upright, box) if box else None
            if cut is not None:
                keys.add(hashlib.sha256(cut).hexdigest())
        overlapping = [index for index, (_, existing) in enumerate(groups) if keys & existing]
        if not overlapping:
            groups.append((choice, keys))
            continue
        first = overlapping[0]
        merged = [groups[index][0] for index in overlapping] + [choice]
        winner = min(merged, key=lambda item: rank.get(item.source, 3))
        bases = tuple(dict.fromkeys(ref for item in merged for ref in item.bases))
        details = list(dict.fromkeys(text for item in merged for text in (item.detail, item.label)
                                    if text and text != winner.label))
        winner = replace(winner, current=any(item.current for item in merged),
                         bases=bases, detail=" · ".join(details))
        for index in overlapping:
            keys.update(groups[index][1])
        groups[first] = (winner, keys)
        for index in reversed(overlapping[1:]):
            groups.pop(index)
    return sorted((choice for choice, _ in groups), key=lambda choice: not choice.current)


def _object_bytes(providers_root: Path, digest: str) -> bytes | None:
    """按内容哈希在各来源目录里找那张图的字节；找不到或读不出就是 None。

    列举候选和真正装上去都要这一步，差别只在读不到时怎么办：列举跳过，安装报错。
    """
    for path in providers_root.glob(f"*/objects/{digest}.*"):
        try:
            body = path.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(body).hexdigest() == digest:
            return body
    return None


def _cached_object(providers_root: Path, digest: str) -> bytes:
    """按内容哈希取那张图。路径可能过期，内容不会。"""
    body = _object_bytes(providers_root, digest)
    if body is None:
        raise PickerError("这张图不在本机缓存里了")
    return body


def fetch_image(transport: HttpTransport, url: str) -> bytes:
    """取一张图。`url` 必须已经过 `allowed_source` 或由我们自己拼出来。"""
    try:
        response = transport(
            HttpRequest("GET", url, {"Accept": "image/*"}),
            FETCH_TIMEOUT, MAX_IMAGE_BYTES)
    except Exception as error:  # noqa: BLE001 — 网络层什么都可能抛
        raise PickerError(f"取不到这张图：{error}") from error
    if response is None or response.status != 200:
        status = "无响应" if response is None else f"HTTP {response.status}"
        raise PickerError(f"取不到这张图：{status}")
    return response.body


def allowed_source(url: str) -> bool:
    """用户手填的地址能不能让 Peach 去取。

    Peach 跑在用户自己的机器上，它能访问路由器后台、NAS、局域网里别的服务和本机
    各个端口。「你给地址我去下」如果不设边界，就是一个替人发请求的跳板：填
    `http://127.0.0.1:8080/admin` 进来，Peach 会替人去访问，再把结果当图片存下。
    判据与追更代理共用一份（`http.public_https_url` 加 `http.resolves_publicly`）：
    必须 https、必须是公网域名、不能是 IP 字面量、解析出来的每一个地址都得是公网的。
    """
    if not public_https_url(url):
        return False
    return resolves_publicly(urllib.parse.urlsplit(url).hostname or "")


def accept_image(body: bytes) -> InspectedAvatar:
    """确认这堆字节真是一张能用的图。格式由解码结果定，不看扩展名也不信响应头。"""
    if len(body) > MAX_IMAGE_BYTES:
        raise PickerError("图太大了")
    inspected = inspect_avatar(body)
    if inspected is None:
        raise PickerError("这不是一张能识别的 JPEG 或 PNG 图片")
    return inspected


@dataclass(frozen=True)
class ArtworkSource:
    """作品画面从哪来。端点把这两样拼好递进来，这一层不认识预览服务。"""

    cover_root: Path
    #: `(asset_id, cell) -> 那一格的路径或 None`。九宫格没铺过时由它现抽一张。
    frame: Callable[[int, int], Path | None]


def _asset_image(ref: str, connection: sqlite3.Connection, entity_id: int,
                 artwork: ArtworkSource | None,
                 version: str | None = None) -> tuple[bytes, dict]:
    """`asset:<id>:cover` / `asset:<id>:cell<n>` → 那张图的字节和来源记录。

    作品必须真的挂在这个人身上才给。页面只会递自己刚列出来的那些，但这一层不能
    依赖那一点：`asset:1:cover` 是个人都拼得出来，凭它就能把任意一部作品的封面
    读出来，而资料页本来看不到那部作品。
    """
    if artwork is None:
        raise PickerError("这一次取不到作品画面")
    _, _, rest = ref.partition(":")
    raw_id, _, what = rest.partition(":")
    try:
        asset_id = int(raw_id)
    except ValueError as error:
        raise PickerError("无法识别这张备选图片") from error
    row = connection.execute(
        "SELECT a.code FROM asset_entity ae JOIN asset a ON a.id=ae.asset_id "
        "WHERE ae.entity_id=? AND ae.asset_id=?",
        (int(entity_id), asset_id)).fetchone()
    if row is None:
        raise PickerError("这部作品不在这个人名下")
    if what == "cover":
        key = artwork_key(asset_id, row[0])
        path = (Path(artwork.cover_root) / f"{key}.jpg") if key else None
        label = "封面"
        _check_version(path, version)
    elif what.startswith("cell"):
        try:
            cell = int(what[len("cell"):])
        except ValueError as error:
            raise PickerError("无法识别这张备选图片") from error
        if not 0 <= cell < SHEET_CELLS:
            raise PickerError("无法识别这张备选图片")
        path = artwork.frame(asset_id, cell)
        label = f"第 {cell + 1} 格"
    else:
        raise PickerError("无法识别这张备选图片")
    if path is None or not Path(path).is_file():
        raise PickerError(f"这部作品的{label}还没有落在本机")
    try:
        body = Path(path).read_bytes()
    except OSError as error:
        raise PickerError(f"读不出这部作品的{label}") from error
    return body, {"source": "avatar picker", "provider": "asset",
                  "external_id": f"{asset_id}:{what}",
                  "asset_id": asset_id, "asset_code": str(row[0] or ""),
                  "identity_verified": False}


def resolve(ref: str, connection: sqlite3.Connection, providers_root: Path,
            entity_id: int,
            transport: HttpTransport | None,
            artwork: ArtworkSource | None = None,
            version: str | None = None,
            online: Callable[[str], tuple[bytes, dict]] | None = None) -> tuple[bytes, dict]:
    """把页面回递的 `ref` 换成图片字节和一份来源记录。

    `ref` 只认这里自己刚枚举出来的那些：图库候选要在索引里真的存在，历史候选要在
    缓存里真的有对象。页面递不进任意地址——手填地址是另一条路，它有自己的边界。

    `version` 是交框时页面回递的封面版本（`Choice.version`）。给了就核对，封面在列出
    候选之后被换过就拒收；只取预览图时不给。
    """
    if ref.startswith(("follow:", "follow-avatar:", "follow-link:")):
        if online is None:
            raise PickerError("未取得在线备选头像")
        return online(ref)
    if ref.startswith("sha256:"):
        digest = ref.split(":", 1)[1].strip().lower()
        body = _cached_object(providers_root, digest)
        return body, {"source": "avatar picker", "provider": "history",
                      "external_id": digest[:12]}
    if ref.startswith("asset:"):
        return _asset_image(ref, connection, entity_id, artwork, version)
    if ref.startswith("cover:"):
        # 只读本机：出网那一步在 `code_cover` 里，由人输入番号时显式触发。来源记录里
        # 不写 `upstream_url`——`keep` 拿它当缓存键，写了会让框出来的那一块顶掉整张封面。
        key = normalise_code_key(ref.split(":", 1)[1])
        body, path = _code_cover_bytes(
            key, artwork.cover_root if artwork else None, providers_root) if key else (None, None)
        if body is None:
            raise PickerError("这个番号的封面还没有取过")
        _check_version(path, version)
        return body, {"source": "avatar picker", "provider": "code-cover",
                      "external_id": key, "asset_code": key}
    if ref.startswith("url:"):
        # 同样只读本机：取图那一步在 `typed_address` 里，地址在那里过公网判据。
        url = ref.split(":", 1)[1]
        body = AvatarCandidateCache(providers_root / ADDRESS_CACHE).lookup(url)
        if body is None:
            raise PickerError("这个地址的图还没有取过")
        return body, {"source": "avatar picker", "provider": ADDRESS_CACHE,
                      "external_id": "", "upstream_url": url}
    if not ref.startswith("gfriends:"):
        raise PickerError("无法识别这张备选图片")
    category, _, filename = ref.split(":", 1)[1].partition("/")
    index = gfriends.load_index(providers_root / GFRIENDS_CACHE)
    match = gfriends.candidates(index, name_chain(connection, entity_id))
    if (category, filename) not in match.finder:
        raise PickerError("当前索引中没有这张备选图片")
    # 证据里记的是找到这一张的那个名字，不是整条链：事后要答的是「这张图凭什么算他」。
    matched = match.finder[(category, filename)]
    url = gfriends.image_url(category, filename)
    cache = AvatarCandidateCache(providers_root / GFRIENDS_CACHE)
    body = cache.lookup(url)
    if body is None:
        if transport is None:
            raise PickerError("这张图还没下载过，而这一次不允许联网")
        body = fetch_image(transport, url)
    return body, {"source": "avatar picker", "provider": "gfriends",
                  "gfriends_category": category, "gfriends_file": filename,
                  "matched_name": matched, "name_source": "picker",
                  "external_id": f"{category}/{filename}", "upstream_url": url}


def crop(body: bytes, box: object) -> tuple[bytes, dict]:
    """按源图像素框切出头像那一块，连同一份记着框的来源补充。

    原图不动：切出来的是新字节，被切的那张（作品封面、九宫格的一格、图库人像）还在
    原处。装上去之后这份新字节自己进候选缓存，所以同一个框换回来不必再切一次。
    框是在浏览器里转正之后的图上画的，所以先按 EXIF 方向转正再量、再切。
    """
    body = images.upright(body)
    size = images.measure_image_size(body)
    if size is None:
        raise PickerError("这不是一张能识别的图片")
    edges = images.clamp_box(box, size[0], size[1])
    if edges is None:
        raise PickerError("框选的区域不成立")
    cropped = images.crop_to_box(
        body, (edges["x0"], edges["y0"], edges["x1"], edges["y1"]))
    if cropped is None:
        raise PickerError("这一块裁不出来")
    return cropped, {"source_kind": "user_cropped",
                     "crop_box": [edges["x0"], edges["y0"], edges["x1"], edges["y1"]],
                     "crop_source_px": [size[0], size[1]]}


def keep(providers_root: Path, entity_id: int, body: bytes, origin: dict) -> InspectedAvatar:
    """把这张图连同证据留进候选缓存，不装。挑图弹层「取过的图」那一组列的就是这些。"""
    inspected = accept_image(body)
    cache = AvatarCandidateCache(
        providers_root / str(origin.get("provider") or "picker"))
    url = str(origin.get("upstream_url") or f"peach:picker/{inspected.sha256}")
    # 地址是缓存键：按它查回来的必须是那个地址上的整张图。框出来的一块记在自己的
    # 内容地址下，否则图库那一格、手填的那个地址从此取回来的都是这一块。
    key = (f"peach:picker/{inspected.sha256}"
           if origin.get("source_kind") == "user_cropped" else url)
    cache.store(key, body, inspected)
    cache.store_provenance(provenance_now(
        entity_id=int(entity_id), provider=str(origin.get("provider") or "picker"),
        source_kind=str(origin.get("source_kind") or "user_selected"),
        matched_name=str(origin.get("matched_name") or ""),
        name_source=str(origin.get("name_source") or "picker"),
        external_id=str(origin.get("external_id") or ""), upstream_url=url,
        width=inspected.width, height=inspected.height,
        mime_type=inspected.mime_type, sha256=inspected.sha256,
        cache_path=f"objects/{inspected.sha256}{inspected.extension}"))
    return inspected


def install(providers_root: Path, avatar_root: Path, kind: str,
            entity_id: int, body: bytes, origin: dict) -> dict:
    """装上去，同时把这张图连同证据留在候选缓存里——换回来时就不必再取一次。"""
    inspected = keep(providers_root, entity_id, body, origin)
    install_entity_avatar(avatar_root, kind, int(entity_id), body,
                          inspected.mime_type,
                          {**origin, "sha256": inspected.sha256,
                           "width": inspected.width, "height": inspected.height,
                           "policy_version": POLICY_VERSION})
    return {"sha256": inspected.sha256, "width": inspected.width,
            "height": inspected.height, "mime_type": inspected.mime_type}
