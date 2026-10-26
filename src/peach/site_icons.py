"""站点图标发现：把一个站能给出的图标候选找齐，再挑真正适合做小圆标的那一枚。

`/link-mark` 只取 `/favicon.ico`、取到就收工的结果普遍糊（用户指出）：站点自己
备好了更清晰的资产，不去问就拿不到。实测四个站，四种形态：

    threads.com   <link rel=icon type=image/svg+xml>  → 512 viewBox 的成品 app 图标
    mgstage.com   五个 apple-touch-icon，最大 180×180 → 位图但足够大
    javdatabase   apple-touch-icon 180 + favicon 32   → 常见 WordPress 套件形态
    av-event.jp   只有 favicon.ico                    → 站点确实没有更好的

所以顺序是「按声明找候选 → 实测像素 → 取最高清」，不是「favicon 取到就不找了」。

**但「最高清」不等于「最合适」。** FANZA 是这条规则的反例，也是本模块存在的理由：

    p-smith.com/apple-touch-icon/fanza.png   200×200，内容是整条「FANZA」字标
    p-smith.com/pinned/favicon_r18.ico        48×48，内容是单个「F」

按分辨率排，200×200 的字标赢；可它塞进 32 px 圆里就是一条糊掉的红杠，48×48 的「F」
反而清楚。区别不在画布大小，在**内容的外接框比例**：字标的内容框接近 4:1，字形的接近
1:1。所以排序前先过一道内容比例闸门，宽扁的整条字标不参加小圆标的竞选——它属于厂牌页
那个大 logo 位（`/logo`），那里 `fanza_r18.svg` 才是对的。同一个品牌在两个位置用两份
资产，这不是不一致，是两个位置本来就要两种东西。

`rel="mask-icon"`（Safari 固定标签页）单独对待：它按规范一定是纯黑单色剪影，当成品图标
用会得到一枚全黑方块，但拿它当**字形**去做品牌色圆底恰恰是最好的输入——矢量、无背景、
边缘干净。所以它不排进成品图标，只排进字形候选。
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlsplit

from .http import body_text

#: 声明里没有尺寸时给的保守估计。favicon.ico 历史上就是 16，别按它去赢过 apple-touch-icon。
DEFAULT_SIZE = 16
#: SVG 没有像素尺寸。给一个高于任何现实位图的分数，让它无条件排在最前。
VECTOR_SIZE = 4096
#: 小于这个边长的位图不值得当成品图标用，只能当字形。32 px 的容器在 3x 屏上要 96 px。
MIN_DESIGNED_SIZE = 96

_LINK_TAG = re.compile(r"<link\b[^>]*>", re.I)
_META_TAG = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR = re.compile(r"""([a-zA-Z][\w:-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
_SIZE = re.compile(r"(\d+)\s*[x×]\s*(\d+)", re.I)


def _attrs(tag: str) -> dict[str, str]:
    return {m.group(1).lower(): (m.group(2) or m.group(3) or m.group(4) or "")
            for m in _ATTR.finditer(tag)}


def _declared_size(value: str) -> int:
    """`sizes="180x180 152x152"` → 180；`sizes="any"` → 0。"""
    return max((int(m.group(1)) for m in _SIZE.finditer(value or "")), default=0)


def _is_svg(url: str, mime: str = "") -> bool:
    return "svg" in (mime or "").lower() or urlsplit(url).path.lower().endswith(".svg")


#: 候选能代表谁。发现流程从 `origin(url)` 出发，所以它找到的一切只能代表**主机**：
#: 首页声明的 `<link rel=icon>`、manifest、`/favicon.ico` 都是整站共用的那一份。
#: 只有覆盖表里手工写下的那几行是冲着某个实体去的，才算 `entity`。
#:
#: 这个区分不是洁癖：`bangbros.com/websites/{BangBus,BangBros18,MonstersOfCock}` 三个频道
#: 共用一个主机，主机级发现让三个厂牌拿到同一枚 Aylo 站点模板 favicon（蓝色六边形「1」，
#: sha256 前缀 `a61e1e88`），两道闸门都过，和任何一个频道都无关。
ENTITY_SCOPE, HOST_SCOPE = "entity", "host"


class Candidate:
    """一个图标候选。`size` 是排序用的分数，不是承诺的真实像素。"""

    __slots__ = ("url", "size", "role", "vector", "scope")

    def __init__(self, url: str, size: int, role: str, vector: bool = False):
        self.url = url
        self.size = size
        self.role = role
        self.vector = vector
        self.scope = ENTITY_SCOPE if role == "override" else HOST_SCOPE

    def __repr__(self) -> str:   # pragma: no cover - 只为断言失败时好读
        return f"Candidate({self.url!r}, size={self.size}, role={self.role!r})"

    def __eq__(self, other) -> bool:
        return (isinstance(other, Candidate) and self.url == other.url
                and self.size == other.size and self.role == other.role)

    def __hash__(self) -> int:
        return hash((self.url, self.size, self.role))


def _rel_role(rel: str) -> str:
    """`rel` → 我们关心的角色；不关心的返回空串。

    `rel` 可以是空格分隔的多值（`shortcut icon`），所以按词判断而不是整串相等。
    """
    words = {w for w in re.split(r"\s+", (rel or "").strip().lower()) if w}
    if "mask-icon" in words:
        return "mask"
    if {"apple-touch-icon", "apple-touch-icon-precomposed"} & words:
        return "apple-touch"
    if "icon" in words:
        return "icon"
    return ""


def link_candidates(html: str, base_url: str) -> list[Candidate]:
    """HTML 的 `<link rel=...icon...>` 与 msapplication 磁贴 → 候选列表。"""
    found: list[Candidate] = []
    for tag in _LINK_TAG.findall(html or ""):
        attributes = _attrs(tag)
        role = _rel_role(attributes.get("rel", ""))
        href = (attributes.get("href") or "").strip()
        if not role or not href:
            continue
        vector = _is_svg(href, attributes.get("type", ""))
        size = VECTOR_SIZE if vector else (
            _declared_size(attributes.get("sizes", "")) or DEFAULT_SIZE)
        found.append(Candidate(urljoin(base_url, href), size, role, vector))
    for tag in _META_TAG.findall(html or ""):
        attributes = _attrs(tag)
        if attributes.get("name", "").lower() != "msapplication-tileimage":
            continue
        href = (attributes.get("content") or "").strip()
        if href:
            # 磁贴图按规范是 144 起步，没有 sizes 属性可读，给一个够格的分数。
            found.append(Candidate(urljoin(base_url, href), 144, "apple-touch"))
    return found


def manifest_url(html: str, base_url: str) -> str:
    """`<link rel="manifest">` 的绝对地址；没有就返回空串。"""
    for tag in _LINK_TAG.findall(html or ""):
        attributes = _attrs(tag)
        words = set(re.split(r"\s+", attributes.get("rel", "").strip().lower()))
        href = (attributes.get("href") or "").strip()
        if "manifest" in words and href:
            return urljoin(base_url, href)
    return ""


def manifest_candidates(payload: str | bytes, base_url: str) -> list[Candidate]:
    """web app manifest 的 `icons[]` → 候选列表。

    PWA 站点常把最大的一份（512 甚至 1024）只放在 manifest 里，HTML 的 `<link>` 只留
    apple-touch-icon。不读 manifest 就会白白少掉最清晰的那一枚。
    """
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return []
    icons = data.get("icons") if isinstance(data, dict) else None
    if not isinstance(icons, list):
        return []
    found: list[Candidate] = []
    for entry in icons:
        if not isinstance(entry, dict):
            continue
        src = str(entry.get("src") or "").strip()
        if not src:
            continue
        vector = _is_svg(src, str(entry.get("type") or ""))
        size = VECTOR_SIZE if vector else (
            _declared_size(str(entry.get("sizes") or "")) or DEFAULT_SIZE)
        found.append(Candidate(urljoin(base_url, src), size, "manifest", vector))
    return found


def conventional_candidates(base_url: str) -> list[Candidate]:
    """站点什么都没声明时的老规矩位置。

    `apple-touch-icon.png` 在根目录是 iOS 的约定回落，很多站有文件却不写 `<link>`；
    `favicon.ico` 是最后一道。两个都可能 404，由取的那一方负责丢弃。

    角色单列成 `conventional`，排在所有**声明过**的候选之后。T-POWERS 是这条的证据：
    根目录 `/apple-touch-icon.png` 是一条带「T-POWERS」文字的横向锁定图（内容比 1.83），
    而它在 `<link>` 里声明的 `assets/images/common/apple-touch-icon.png` 是紧凑标识
    （1.25）。两个都是 180，按大小并列时字标会因为路径短而排前面。站点自己指了哪一个，
    就该听它的；根路径只是我们的猜测。
    """
    return [
        Candidate(urljoin(base_url, "/apple-touch-icon.png"), 180, "conventional"),
        Candidate(urljoin(base_url, "/favicon.ico"), DEFAULT_SIZE, "conventional"),
    ]


def host_key(url: str) -> str:
    """按主机归一，去掉 `www.`——同一个站的每条链接共用一枚图标。"""
    return (urlsplit(url).hostname or "").casefold().removeprefix("www.")


#: 发现流程找不到、或找到的不适合时，逐主机指定来源。**这是例外，不是主路径。**
#:
#: 键可以是纯主机（`fc2.com`，连子域一起管），也可以是「主机 + 路径前缀」
#: （`bangbros.com/websites/BangBus`，只管挂在这条路径下的那个实体）。后者是给
#: 「一个主机装着好几个品牌」的站用的：发现流程从 `origin(url)` 出发，路径一丢，
#: 同主机的几个实体就坍缩成同一份结果。
#:
#: 每加一行都要写清为什么发现流程不够，否则这张表会长成一份手工 favicon 清单，
#: 而站点改版时没有人会来更新它。
HOST_OVERRIDES: dict[str, tuple[str, ...]] = {
    # 首页受验证页保护；用户确认的 180×180 成品图标位于 /img/，根路径约定无法发现。
    "missav.com": ("https://missav.ws/img/favicon.png",),
    "missav.ws": ("https://missav.ws/img/favicon.png",),
    # 首页只声明了 favicon.ico（16×16 的相机图标，缩到 32 px 认不出）。这张吉祥物图
    # 只出现在年龄确认页的正文里，`<link>` 里没有，发现流程按设计不会去正文里翻图。
    "av-event.jp": (
        "https://www.av-event.jp/pc/images/pages/age_check/title_logo_icon.jpg",
    ),
    # FANZA 的资产托在 p-smith.com，video.dmm.co.jp 自己的 `<link>` 指不到那里。
    # 顺序就是取用顺序：先要那个方形的「F」，字标（apple-touch-icon/fanza.png）
    # 会被内容比例闸门挡下，留给 `/logo` 的大位。
    "video.dmm.co.jp": ("https://p-smith.com/pinned/favicon_r18.ico",),
    "dmm.co.jp": ("https://p-smith.com/pinned/favicon_r18.ico",),
    # FC2 全站声明的图标只有 `static.fc2.com/share/image/favicon.ico`（16×16，独角兽头
    # 是真正的标识），页面里引用的更大资产全是横向字标（189×68、690×68），那属于
    # `/logo` 的大位；`blog`／`live`／`static` 上的 `apple-touch-icon`、`favicon-192`、
    # `icon.png` 全 404。所以这里不是「发现流程没找对」，是站上确实没有够大的那一份。
    #
    # 用户 2026-09-03 直接指定这一枚补 `icon` 位：400×400 RGBA、内容比 1.07，纯红色
    # 独角兽、没有文字，sha256 `ddaa3216…9462`（40901 B，2026-09-03 实测）。服务端回的
    # content-type 是 `application/octet-stream`，靠 `link_marks.decode` 里 PIL 的嗅探解开。
    # 曾用 App Store 的「FC2動画」商店图标（512×512），已被用户否决：背景多了胶片图案。
    # 也不用 `id.fc2.com/apple-touch-icon.png`：114×114，是「独角兽 + FC2 文字」的纵向
    # 锁定图，缩到 28 px 文字糊成一团。600×600 那份方形锁定图装 `logo` 位，见
    # `scripts/harvest_studio_icons.py` 的 `LOGO_SOURCES`。
    "fc2.com": (
        "https://storage.googleapis.com/datanyze-data//technologies/"
        "8ef39cbce34aece41d279b6e8e7dbb77aea3086e.png",
    ),
    # BangBros 的三个频道都是 `bangbros.com/websites/<频道>`，发现流程不够有三层原因：
    # `discover()` 先 `origin(url)` 把路径丢了，三个频道坍缩成同一个主机；频道页自己只
    # 声明一枚 16×16 favicon（`MIN_SHORT_EDGE` 退回）；于是轮到 `bangbros.com/favicon.ico`
    # ——64×64、内容比 1.00，两道闸门都过，可它是 Aylo 站点模板的通用图标（蓝色六边形
    # 「1」，`www.bangbus.com`、`www.monstersofcock.com` 回同一份），和频道无关。
    # 这两枚是 `bangbros.com/websites` 页面配置里的 `*_LOGO` 资产（298×50，宽扁字标，
    # 由 `harvest_studio_icons.py` 补白成方图）。MonstersOfCock 那一页没有 logo 资产，
    # 记「未取得」，不在这张表里编一个。
    # sha256：BangBus `ab652f50…d145`、BangBros18 `a59dd7ae…16d2`（2026-09-03 实测）。
    "bangbros.com/websites/BangBus": (
        "https://images-assets-ht.project1content.com/BangBros/MA/Subsites/"
        "6480a83863bd03.19615490.png",
    ),
    "bangbros.com/websites/BangBros18": (
        "https://images-assets-ht.project1content.com/BangBros/MA/Subsites/"
        "6480a83860e9c0.34870333.png",
    ),
}


def overrides_for(url: str) -> list[Candidate]:
    """覆盖表里的候选；没有就返回空列表。

    先按「主机 + 路径前缀」取**最长**匹配，再退到纯主机（含子域）。顺序不能倒：
    `bangbros.com` 将来若要加一条主机级条目，三个频道各自那条必须仍然赢过它，
    否则又回到「同主机坍缩成同一枚图标」。
    """
    host = host_key(url)
    if not host:
        return []
    target = (host + urlsplit(url).path.rstrip("/")).casefold()
    scoped = [key for key in HOST_OVERRIDES
              if "/" in key and (target == key.casefold()
                                 or target.startswith(key.casefold() + "/"))]
    if scoped:
        entries: tuple[str, ...] | None = HOST_OVERRIDES[max(scoped, key=len)]
    else:
        entries = HOST_OVERRIDES.get(host)
    if entries is None:
        # `p-smith.com` 这类子域也要能命中父域条目。带路径的键不参与子域匹配：
        # 那种键管的是一条路径，不是一族主机。
        entries = next((v for k, v in HOST_OVERRIDES.items()
                        if "/" not in k and host.endswith("." + k)), None)
    if not entries:
        return []
    return [Candidate(u, VECTOR_SIZE if _is_svg(u) else 512, "override", _is_svg(u))
            for u in entries]


#: 排序分层。矢量优先于任何位图，与 `rel` 无关——threads 把 512 viewBox 的 SVG 声明成
#: `rel="icon"`，而根目录还躺着一个约定俗成的 `apple-touch-icon.png`。按 `rel` 分层会让
#: 那个位图赢过 SVG，正是用户指出的「抓到 favicon 就不找别的了」的同一种错。
_OVERRIDE, _VECTOR, _RASTER, _CONVENTIONAL, _MASK = 0, 1, 2, 3, 4


def _tier(candidate: "Candidate") -> int:
    if candidate.role == "override":
        return _OVERRIDE
    if candidate.role == "mask":
        # 单色剪影。当成品图标用会得到一枚全黑方块，所以永远排最后，
        # 轮到它时它走的是字形通道。
        return _MASK
    if candidate.role == "conventional":
        return _CONVENTIONAL
    return _VECTOR if candidate.vector else _RASTER


def rank(candidates: list[Candidate]) -> list[Candidate]:
    """按「先覆盖表、再矢量、再大小」排序，同一地址只留分数最高的一条。"""
    best: dict[str, Candidate] = {}
    for candidate in candidates:
        current = best.get(candidate.url)
        if current is None or candidate.size > current.size:
            best[candidate.url] = candidate
    return sorted(best.values(), key=lambda c: (_tier(c), -c.size, c.url))


def origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


#: 一次发现最多真的去下载几个候选。排序已经把最可能的放前面，试到第三个还不成，
#: 多半是这个站没有能用的图标，继续试只是白打人家的服务器。
MAX_FETCH = 4


def discover(url: str, fetch) -> list[Candidate]:
    """一个外链地址 → 按取用顺序排好的图标候选。

    `fetch(url) -> (bytes, content_type) | None` 由调用方注入，测试因此不必联网。
    首页取不到不算失败：老规矩位置（`/favicon.ico`、`/apple-touch-icon.png`）照样试。
    """
    base = origin(url)
    if not base:
        return []
    candidates = list(overrides_for(url))
    page = fetch(base)
    if page is not None:
        # `fetch` 连内容类型一起给了，声明里的 charset 就在手边：日站首页仍有相当
        # 一部分是 Shift_JIS，按 utf-8 硬解会把 `<title>` 与 `rel` 文本变成替换字符。
        html = body_text(page[0], {"content-type": page[1]})
        candidates += link_candidates(html, base)
        manifest = manifest_url(html, base)
        if manifest:
            got = fetch(manifest)
            if got is not None:
                candidates += manifest_candidates(got[0], manifest)
    candidates += conventional_candidates(base)
    return rank(candidates)


def best_mark(url: str, fetch, render, fallback=None, accept=None) -> bytes | None:
    """按顺序试候选，第一个能做出圆标的就是它。

    `render(data, content_type=...) -> bytes | None` 自己决定合格与否——宽扁字标、
    解不开的文件和太小的位图都由它退回 None，这里只负责换下一个。

    `accept(candidate, data, content_type) -> bool` 是取回之后、渲染之前的一道否决，
    默认没有。调用方用它拒绝「取到了、也够清楚，但只能代表主机而不是这个实体」的候选，
    并就地记下拒绝理由——被否决的那一份连 `fallback` 都不许用，否则回落会把整站通用的
    favicon 装成这个实体的标识。`/link-mark` 那个位置本来就是按主机的，不传这个参数。

    一个都没做成时，`fallback` 拿排在最前的那份**已经取回来的**字节再试一次（原样缩图
    也比露出地球图标强）。回落用的是循环里存下的那一份，不是重新发现一遍：重新发现要把
    首页和 manifest 再取一次，为一张回落图打两轮请求不值得。
    """
    tried = 0
    first: tuple[bytes, str] | None = None
    for candidate in discover(url, fetch):
        if tried >= MAX_FETCH:
            break
        got = fetch(candidate.url)
        if got is None:
            continue
        tried += 1
        if accept is not None and not accept(candidate, got[0], got[1]):
            continue
        if first is None:
            first = got
        made = render(got[0], content_type=got[1])
        if made:
            return made
    if fallback is not None and first is not None:
        return fallback(first[0], content_type=first[1])
    return None
