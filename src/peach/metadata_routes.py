"""查询侧的来源路由：一个番号属于哪种内容，因此问哪几家、什么顺序、何时停。

分工先说清楚。本模块只管**问**：番号的内容类型、该类型的有序来源链、以及一档取到
必填标量字段之后不再问下一档。取回来的值怎么排序、怎么结算分歧、哪个字段听谁的，
在 `metadata_policy`（`FIELD_SOURCE_ORDER`、`PREFERRED_COMMUNITY_SOURCE`、
`FALLBACK_SOURCES`）与落库那一侧，两条轴不要混：javbus 在结算上是兜底
（ADR-0035），在查询上却排在 javdb 前面——javdb 按出口 IP 计配额、主机间隔 3 秒
（`library_processing.SOURCE_INTERVALS`），先问便宜的那两家不影响结算，只影响谁先撞上限流。

链的取舍参考 amane 的 `docs/dev/content-routes.md`（证据登记在
`docs/reference-sources.json` 的 `amane-content-routes`），原则照搬三条：类型专属源
靠前、综合索引垫后；对不匹配番号仍发 HTTP 的站不进默认链；会把无关番号拼成
「看起来像详情页」的站一概不进。成员只取 Peach 已经接好的那几家（自写解析器或经 amane 桥），
每个站只有一个归属（ADR-0048）；上游表里 kin8、giga、theporndb、getchu 这些没接的不写进来占位。
对不匹配番号仍发 HTTP 的片商站（Prestige、FALENO、DAHLIA）由 `route_for_code` 按本机证据裁掉。

国产、欧美这些 Peach 一家对应来源都没接的体系合成一类 `other`，链是空的：按本机证据认得出
它不是日本发行物，就一家都不问，也不问封面。番号形状像有码、本机证据却对不上它的写法时归
`unsure`，只问综合索引那一档一遍，答复照常记进「没有」的记忆（`classify` 写着判据）。
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping, Sequence

from .catalog_rules import is_amateur_code, is_korean_mib_code, is_uncensored_code
from .metadata_policy import SOURCE_SPECS


#: 内容类型。`unknown` 是「番号认不出来」，不是一种内容；`other` 是认得出、但 Peach 没接
#: 来源的体系（国产、欧美）；`unsure` 是形状像有码、本机证据对不上的番号。
CONTENT_TYPES = ("censored", "amateur", "uncensored", "fc2", "kmib", "other", "unsure", "unknown")

#: MGStage 素人系的字母前缀。三位数字前缀那一批由 `catalog_rules.is_amateur_code`
#: 认（`300MIUM-1239`、`259LUXU-1475`），这三种没有数字前缀，形态上和厂牌番号一样，
#: 只能按前缀列出来。它们和三位数字前缀一起写在 `catalog_rules._RELEASE_SYSTEM_SHAPE`
#: 里，那条判据已经把它们当作「发行体系自己的写法」，这里只是把同一批前缀用在路由上。
#: **`ABP` / `ABF` / `ABW` 不在此列**：那是 Prestige 的有码番号，mgstage 首页同时挂
#: 有码号与素人号，按站点归类会把整个 Prestige 判成素人。
MGS_AMATEUR_PREFIXES = ("SIRO", "STP", "STN")
_MGS_AMATEUR = re.compile(r"^(?:" + "|".join(MGS_AMATEUR_PREFIXES) + r")-?\d+$", re.I)
#: 入库前没归一成 `FC2-PPV-…` 的短写法：`FC-43768`（账本实测，文件名是 `FC-437689-C.mp4`）。
#: 与 `catalog_rules._FC2_ID` 的短写法同一把尺。
_FC2_SHORT = re.compile(r"^FC-\d{5,}$", re.I)
#: 番号拆成字母段与数字段，用来在本机证据里找它的写法。
_CODE_PARTS = re.compile(r"^([A-Z]+)-?0*(\d+)$")
#: 路径里出现就说明这是 Peach 没接来源的体系。每一个都在本机账本的路径里实测出现过
#: （2026-09-25 只读统计）：`兔子先生TZ-105`、`网红…合集\MXGD3721.MP4` 这类文件的番号
#: 形状和有码一模一样，按有码链问下去是片商官网、r18.dev、DMM、三家综合索引各白问一遍。
OTHER_SYSTEM_MARKS = ("国产", "國產", "网红", "網紅", "兔子先生", "麻豆")
#: 欧美片站的文件名：番号前面挂着至少两个用连字符或下划线连起来的拉丁词
#: （`wankzvr-elena-koshka-GFE-180_180x180_3dh_LR.mp4`，站名加演员名）。日本片的前缀是
#: 站点水印（`hjd2048.com-`、`[FHD]`、`HD_`），不会是两个以上纯字母词连写。
_WESTERN_SLUG = r"(?<![a-z])(?:[a-z]{2,}[-_]){2,}"

#: 每种内容类型问哪几家，从左到右。每条链后面的注释回答两件事：为什么是这个顺序，
#: 以及为什么某一家**不**在这条链上——后者才是省请求的地方。
ROUTES: dict[str, tuple[str, ...]] = {
    # 有码：片商官网先问，经 amane 桥一次子进程（ADR-0048）。片商自己那一页是发行方口径，
    # 比官方镜像 r18.dev 更近一层：2026-09-23 实测 S1、MOODYZ 官网的发売日与账本一致，
    # DMM 数字版给的是配信开始日。四家里一个番号只问一家：Prestige、FALENO、DAHLIA 按本机
    # 证据认自家番号（`MAKER_EVIDENCE`），都不认的交给 `makers`——amane 按系列前缀路由到
    # 二十九家片商官网，前缀不在它的表里就不发请求。官网没有或出错再问 r18.dev，一次请求
    # 给标题、厂牌、系列、发行日与演员；r18.dev 也没有再问 DMM 自己的 GraphQL 目录（ADR-0059）：
    # 它是 r18.dev 镜像的源头，2026-09-24 实测当月新片与 FKOS、FNS、SUKE 这些小厂 r18.dev 都是
    # 404 而 DMM 有，一部片一到三次请求、不需要日本出口；再落空才到综合索引。AVBase 与
    # JavBus 在前，javdb 与需要人工验证的 JavLibrary 垫后，浏览器目录间隔为 3 秒。
    # 1pondo 与 fc2 的发行体系不同，不进这条链。Prestige 系由 route_for_code 补入 MGStage
    # 备用，官网冷却时仍有发行店资料可问；标题等字段继续按片商优先级结算。
    "censored": ("prestige", "faleno", "dahlia", "makers", "r18dev", "dmm", "avbase", "javbus", "javdb", "javlibrary"),
    # 素人：MGStage 先问。MGS 素人系（`259LUXU`、`300MIUM`、`SIRO`……）由 MGS 自己发行，
    # mgstage 那一页就是发行方口径；amane 同样把 MGS 放素人第一源、不让它进有码默认表
    # （否则 MIDV 也会去问 MGS），Peach 的等价做法是把素人单列成一种类型。
    # r18.dev 垫在最后：2026-09-25 清点本机来源快照，素人号在 r18.dev 上一份都没有，排在
    # 综合索引前面等于每部片先白等一次主机间隔。仍留在链上，mgstage 与综合索引都落空时它是
    # 最后一处可能有官方值的地方。
    # dmm 不进：2026-09-24 实测 DMM 的搜索对 `MIUM 1239`、`LUXU 1475` 零结果，MGS 素人号
    # 不在它的目录上，多问一家只多两次白请求。
    "amateur": ("mgstage", "avbase", "javbus", "javdb", "r18dev"),
    # 无码：发行方自己那份作品 JSON 先问，但只在本机证据指着一本道时才问
    # （见 `route_for_code`）——日期式番号不带片商，カリビアンコム 与一本道同形，
    # 问错一家答回来的是同一天发行的另一部片。
    # **不含 r18dev**：无码番号在 r18.dev 上没有（`docs/SOURCING.md`「一本道作品
    # 资料与封面」、`catalog_rules.is_uncensored_release` 的实测注释、
    # 与 `sources/onepondo.py` 的模块说明）。留着它等于每个
    # 无码番号白等一次主机间隔，再把「问了都没有」读成「上游没有」。
    # avsox 经 amane 桥（ADR-0043）垫在最后：它专收无码，但是转载索引，且要经 Cloudflare，
    # 三家综合索引都落空才轮到它。
    "uncensored": ("1pondo", "avbase", "javbus", "javdb", "avsox"),
    # FC2：发行方商品页 → 下架作品的镜像站 → FC2PPV-DB → JAVten → JavArchive → javdb。
    # fc2cmadb 给下架作品的原图与女优栏，排在前；FC2PPV-DB 给女优、卖家、販売日与流出标记，
    # 不给封面；JAVten 给日文标题、标签与 FC2 存储原件的地址（ADR-0060）。这两站都在 Cloudflare
    # 验证后面，由本机浏览器过验证（ADR-0065）；验证没过、或没有浏览器时 Cookie 失效回 403，各自
    # 整站冷却，链照常往下走。JavArchive 只给标题和一张
    # 转存封面，比官方原图差一档，所以排在几个存档站之后。
    # 不含 fc2club：它经 amane 桥问（ADR-0043），2026-09-25 清点本机来源快照，它在 FC2 那一档
    # 一份资料都没交过，还回 429 把自己送进冷却。站点代码留着，用户整条覆盖时仍可点名。
    # 不含 r18dev（实测 85 条全空）、不含 AVBase 与 JavBus（本机 1213 份来源证据里
    # 这两家对 FC2 番号一份都没给过，javdb 给了 166 份）。判据原文在
    # `community_catalog.community_sources_for` 与 `docs/SOURCING.md`。
    "fc2": ("fc2", "fc2cmadb", "fc2ppvdb", "javten", "javarchive", "javdb"),
    # 韩国 MIB 一家都不问：番号和日本番号同形，JAV 目录站按它去查返回的是别的作品，
    # 那份错值只能靠人一条条认出来。官网走 `scripts/harvest_kmib.py`，不在这条路上。
    "kmib": (),
    # 国产、欧美：Peach 一家对应来源都没接，问日本目录站只会查空或撞上同号的日本片。
    "other": (),
    # 形状像有码、本机证据对不上写法的番号：只问综合索引一遍，不起片商表、不问 r18.dev 与 DMM。
    "unsure": ("avbase", "javbus", "javdb"),
    "unknown": (),
}

#: 片商官网与发行方自营店那一档，经 amane 桥（`metadata_amane.OFFICIAL_SITES`，ADR-0048）。一次
#: 子进程并发问链上属于这一档的几站，所以合成一档 `amane_official`；按 `route_for_code` 裁过之后，
#: 有码番号在这一档只剩一家片商，Prestige 系另有 MGStage 备用；素人番号只剩 mgstage。
AMANE_OFFICIAL_STAGE = ("prestige", "faleno", "dahlia", "makers", "mgstage")
#: 发行方与专站那一档。链上排在综合索引前面，取到必填标量字段就短路。FC2 的四个存档站
#: 按来源分级是 community，但在链上属于这一档：它们只收 FC2，不是综合索引。
OFFICIAL_STAGE = (*AMANE_OFFICIAL_STAGE, "r18dev", "dmm", "1pondo", "fc2", "fc2cmadb", "fc2ppvdb", "javten",
                  "javarchive")
#: FC2 那五处是同一次 `LibraryMetadataProvider.fc2()` 里先后问的，合成一档 `fc2`。
FC2_STAGE = ("fc2", "fc2cmadb", "fc2ppvdb", "javten", "javarchive")
#: FC2 链上有女优栏的几站。发行方商品页没有演员栏，这一行还缺演员时前面几档答上了也接着问它们；
#: JAVten 与 JavArchive 不给演员，不为演员去问。
FC2_CAST_SITES = ("fc2cmadb", "fc2ppvdb")
#: 缺标签的行在这几档之间多问一家，见 `settles`。dmm 不在这里：它与 r18.dev 是同一份目录，
#: r18.dev 答了标量却没给标签时再问 DMM 拿回的还是那一套 genre；它只在 r18.dev 落空时被问。
OFFICIAL_STAGE_NAMES = ("amane_official", "r18dev", "1pondo", "fc2")

#: 对任何番号都发请求的三家片商站各认哪些番号：（字母前缀，指着这家的写法）。前缀取自本机账本
#: 2026-09-23 的只读统计——厂牌列写着这一家、同一前缀至少两行；FALENO 另加 amane 标注的同站
#: 厂牌 maryGOLD 与 JimmyScandal。写法在账本厂牌、路径与文件名里找，不分大小写。前缀或写法命中
#: 一条才问，别的番号一个请求都不发；这三家都不认的番号交给 `makers`，由 amane 的片商表判。
MAKER_EVIDENCE: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "prestige": (("ABF", "ABP", "ABS", "ABW", "AKA", "DIC", "DOCP", "FIR", "FIV", "HRV", "JBS", "KBI",
                  "LXVS", "ONEZ", "PPT", "PXH", "SGA", "TRE", "YRH", "YRZ"), ("prestige", "プレステージ")),
    "faleno": (("FSDSS", "FCDSS", "FNS", "MGOLD", "JIMMY"), ("faleno",)),
    "dahlia": (("DLDSS",), ("dahlia",)),
}
_LETTER_PREFIX = re.compile(r"^([A-Z]+)-?\d")
#: 综合索引那一档。这一档**不**逐家短路：免复核要两家取值一致（ADR-0030、
#: ADR-0034），封面互证要两个不同图源（ADR-0032），问到第一家就停等于把这两条
#: 判据的样本降到一家。
COMMUNITY_STAGE = ("avbase", "javbus", "javdb", "javlibrary")
#: 经 amane 桥问的转载站那一档（`metadata_amane.COMMUNITY_SITES`，ADR-0043）。一次子进程并发问链上属于
#: 这一档的几站，所以合成一档 `amane`；分级上都是社区来源，但不占 `COMMUNITY_STAGE`
#: 的 `LIST_FIELD_DEPTH` 名额——综合索引的互证样本不该被转载站挤掉。链上放在综合索引之前
#: 还是之后由各类型的链自己定：默认链上只有无码的 avsox，垫在最后；其余三站只由整条覆盖点名。
AMANE_STAGE = ("fc2club", "freejavbt", "airav", "avsox")

#: 必填标量字段。一档把这几项（在这一行还缺的范围内）都给全了就不问下一档。
SCALAR_FIELDS = ("title", "performers", "studio", "release_date")
#: 列表字段：一档给了也不算「够了」，因为多一家就多一批标签与一个图源。
LIST_FIELDS = ("tags", "cover_url")
#: 列表字段在综合索引那一档最多问到第几家。3 是下限不是上限：两家一致才免复核、
#: 两个图源才算互证，砍到 2 家就等于任何一家缺席都退回人工复核。
LIST_FIELD_DEPTH = 4


def classify(code: str | None, *hints: str | None) -> str:
    """这个番号属于哪种内容。`hints` 是本机证据（路径、文件名、账本厂牌）。

    只看番号形状与本机证据，不依赖任何抓取结果：一旦改成「先拿到元数据再判断」，
    没有元数据的番号就永远轮不到该问的那个来源。

    「文件名被读成番号的创作者作品」不是一种内容类型：那道门在更前面，由
    `catalog_rules.scrapes_as_jav` 拦（`docs/SOURCING.md`「哪些行不该进 JAV 刮削」），
    过不了那道门的行根本不会走到路由这一步。

    有码是兜底那一类，所以本机证据在这里再分三种（例子都是账本实测）：

    - 证据里写着带三位数字前缀的同一个号（`LUXU-688` 的文件是 `259LUXU-688.mp4`）是素人；
    - 路径带国产标记（`OTHER_SYSTEM_MARKS`）或文件名是欧美片站的写法（`_WESTERN_SLUG`）
      归 `other`，一家都不问；
    - 证据里有这个号的字母段、却找不到它的完整写法，是文件名被读错了号
      （`UWFr85dczsVeysGg.mp4` 读成 `UWFR-085`，`KUZU_250103-U_iris3.mp4` 读成
      `KUZU-25010`），归 `unsure`。证据里压根没有字母段的（号来自 NFO 或账本）不算读错。
    """
    value = str(code or "").strip()
    if not value:
        return "unknown"
    if is_korean_mib_code(value):
        return "kmib"
    # `FC-437689` 这类变体由 `catalog_rules.normalise_code_key` 在入库前统一成
    # `FC2-PPV-…`；没归一的短写法按 `_FC2_SHORT` 认。
    if value.upper().startswith("FC2") or _FC2_SHORT.match(value):
        return "fc2"
    if is_uncensored_code(value):
        return "uncensored"
    if is_amateur_code(value) or _MGS_AMATEUR.match(value.upper()):
        return "amateur"
    return _censored_or_other(value.upper(), [str(hint) for hint in hints if hint])


def _censored_or_other(value: str, hints: list[str]) -> str:
    """有码形状的番号按本机证据再分一次，判据见 `classify`。"""
    parts = _CODE_PARTS.match(value)
    if not parts or not hints:
        return "censored"
    letters, number = parts.group(1), str(int(parts.group(2)))
    text = "\n".join(hints)
    # 连字符是判据的一半：DMM 的 content_id 也是三位数字打头（`118lxvs00039`），那是 Prestige
    # 的有码号；MGS 素人号的写法总带着连字符。
    if re.search(rf"(?<![A-Z0-9])\d{{3}}{letters}-0*{number}(?!\d)", text, re.I):
        return "amateur"
    if any(mark in text for mark in OTHER_SYSTEM_MARKS):
        return "other"
    written = rf"(?<![A-Z]){letters}[-_ ]?0*{number}(?!\d)(?:[A-Z]{{0,3}})(?![A-Z])"
    if re.search(_WESTERN_SLUG + written, text, re.I):
        return "other"
    if re.search(written, text, re.I):
        return "censored"
    return "unsure" if re.search(rf"(?<![A-Z]){letters}", text, re.I) else "censored"


def parse_route_overrides(raw: str | Mapping[str, Sequence[str] | str] | None):
    """用户给的每类型覆盖。文本写法 `censored=r18dev,javdb;fc2=fc2`。

    形状与用途跟 `scrape_codes --sources` 同一套：来源名必须在
    `SOURCE_SPECS` 里登记过，类型名必须是 `CONTENT_TYPES` 之一，不认识的直接报错
    而不是静默忽略——静默忽略的表现是「设置改了没生效」，比报错难查得多。
    空链合法，意思是这类内容一家都不问。
    """
    if raw is None:
        return {}
    items: Iterable[tuple[str, Sequence[str] | str]]
    if isinstance(raw, str):
        items = [tuple(part.split("=", 1)) for part in raw.split(";") if part.strip()]  # type: ignore[misc]
    else:
        items = list(raw.items())
    overrides: dict[str, tuple[str, ...]] = {}
    for entry in items:
        if len(entry) != 2:
            raise ValueError("来源链覆盖要写成 `类型=来源,来源`")
        name, value = entry
        content = str(name).strip()
        if content not in CONTENT_TYPES:
            raise ValueError("未知内容类型：" + content)
        parts = value.split(",") if isinstance(value, str) else list(value)
        sources = tuple(dict.fromkeys(
            str(part).strip() for part in parts if str(part).strip()))
        unknown = [source for source in sources if source not in SOURCE_SPECS]
        if unknown:
            raise ValueError("未知来源：" + "、".join(unknown))
        overrides[content] = sources
    return overrides


def route(content_type: str, *, overrides: Mapping[str, Sequence[str]] | None = None):
    """这种内容的有序来源链。用户覆盖整条替换，不做逐项合并。

    逐项合并的表现是「删不掉一家」：用户想摘掉 javdb 就得先知道内建表里有它。
    整条替换让设置里那一行自己就是答案。
    """
    if content_type not in ROUTES:
        raise ValueError("未知内容类型：" + str(content_type))
    resolved = parse_route_overrides(overrides)
    if content_type in resolved:
        return resolved[content_type]
    return ROUTES[content_type]


def route_for_code(code: str | None, *hints: str | None,
                   overrides: Mapping[str, Sequence[str]] | None = None):
    """这个番号的来源链，已按本机证据裁过。

    无码那条链上的 `1pondo` 要证据：日期式番号本身不带片商，只有路径、文件名或账本
    厂牌指着一本道时才问它，问不着的照旧落到综合索引（`sources/onepondo.py`
    的模块说明）。

    有码链上的片商站同理：Prestige、FALENO、DAHLIA 只在本机证据认得是自家番号时才问
    （`maker_sites`），一个番号只属于一家片商，所以三家有一家认了就不再问 `makers`。
    """
    content_type = classify(code, *hints)
    overrides = parse_route_overrides(overrides)
    chain = route(content_type, overrides=overrides)
    if "1pondo" in chain:
        from .sources.onepondo import movie_id, names_this_studio
        if not (movie_id(str(code or "")) and names_this_studio(*hints)):
            chain = tuple(source for source in chain if source != "1pondo")
    if any(source in MAKER_EVIDENCE for source in chain):
        claimed = maker_sites(code, *hints)
        chain = tuple(source for source in chain
                      if (source not in MAKER_EVIDENCE or source in claimed)
                      and not (source == "makers" and claimed))
        if content_type == 'censored' and content_type not in overrides and 'prestige' in chain:
            position = chain.index('prestige') + 1
            chain = (*chain[:position], 'mgstage', *chain[position:])
    return chain


def maker_sites(code: str | None, *hints: str | None) -> tuple[str, ...]:
    """`MAKER_EVIDENCE` 里认这个番号的片商站：字母前缀在表里，或账本厂牌、路径、文件名指着这家。"""
    matched = _LETTER_PREFIX.match(str(code or "").strip().upper())
    prefix = matched.group(1) if matched else ""
    text = " ".join(str(hint or "") for hint in hints).casefold()
    return tuple(site for site, (prefixes, names) in MAKER_EVIDENCE.items()
                 if prefix in prefixes or any(name.casefold() in text for name in names))


def community_route(code: str | None, *hints: str | None,
                    overrides: Mapping[str, Sequence[str]] | None = None):
    """这个番号在综合索引那一档问哪几家，顺序同链，最多 `LIST_FIELD_DEPTH` 家。"""
    chain = route_for_code(code, *hints, overrides=overrides)
    return tuple(source for source in chain
                 if source in COMMUNITY_STAGE)[:LIST_FIELD_DEPTH]


def stages_for_code(code: str | None, *hints: str | None,
                    overrides: Mapping[str, Sequence[str]] | None = None):
    """链按「档」摊给采集任务：官方那几家逐个是一档，综合索引合成一档 `community`。

    综合索引合成一档是因为那一档不逐家短路（见 `COMMUNITY_STAGE`）；官方那几家
    逐个成档，取到必填标量就不问下一档。FC2 那三家是同一次
    `LibraryMetadataProvider.fc2()` 里先后问的三处，合成一档 `fc2`；经 amane 桥的
    片商站与转载站各是一次子进程，分别合成 `amane_official` 与 `amane`。
    """
    return stages_for_chain(route_for_code(code, *hints, overrides=overrides))


def stage_name(source: str) -> str:
    """这个来源在采集任务里属于哪一档。合档的四组见 `FC2_STAGE`、`COMMUNITY_STAGE`、
    `AMANE_OFFICIAL_STAGE`、`AMANE_STAGE`。"""
    if source in FC2_STAGE:
        return "fc2"
    if source in COMMUNITY_STAGE:
        return "community"
    if source in AMANE_OFFICIAL_STAGE:
        return "amane_official"
    if source in AMANE_STAGE:
        return "amane"
    return source


def stages_for_chain(chain: Sequence[str]):
    """任意一条有序来源链按档摊开，顺序同链。`scrape_codes --sources` 点名的链也走这里。"""
    stages: list[str] = []
    for source in chain:
        name = stage_name(source)
        if name not in stages:
            stages.append(name)
    return tuple(stages)


def stage_members(stage: str, chain: Sequence[str]):
    """这一档在链上对应哪几个来源名。缓存与证据文件都按来源名存，不按档名。"""
    return tuple(source for source in chain if stage_name(source) == stage)


def official_route(code: str | None, *hints: str | None,
                   overrides: Mapping[str, Sequence[str]] | None = None):
    """链上属于官方／发行方那一档的成员，顺序同链。"""
    chain = route_for_code(code, *hints, overrides=overrides)
    return tuple(source for source in chain if source in OFFICIAL_STAGE)


def required_scalars(missing: Iterable[str]):
    """这一行还缺、且属于必填标量的那些字段。空集合表示没有可短路的目标。"""
    wanted = set(str(field) for field in missing)
    return tuple(field for field in SCALAR_FIELDS if field in wanted)


def settles(required: Sequence[str], provided: Iterable[str], *,
            wants_tags: bool = False, then: str = "") -> bool:
    """这一档给的字段够不够停手。

    `required` 空时任何一档只要给出资料就停——这一行要的本来就不是标量字段，
    再问下一档只会拿回一堆和账本现值相同的候选（ADR-0033）。

    `wants_tags` 是这一行还缺标签，`then` 是链上下一档的档名。FALENO 与 DAHLIA 官网的
    作品资料没有类别，标量给全了也一个标签都没有；下一档仍是官方档（有码链上片商官网之后
    的 r18.dev）时再问它一次换一批标签。下一档是综合索引或转载站就照样停：javdb 按出口 IP
    计配额，不为标签多问。
    """
    given = set(str(field) for field in provided)
    if wants_tags and "tags" not in given and then in OFFICIAL_STAGE_NAMES:
        return False
    if not required:
        return True
    return all(field in given for field in required)
