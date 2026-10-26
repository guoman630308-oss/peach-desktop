"""来源分级与字段结算策略，由 Peach 自己持有。

查询侧（一个番号问谁、什么顺序、何时停）在 `metadata_routes`；这里只管取回来的值
怎么分级、怎么排序、分歧听谁的（ADR-0038）。两套刮削栈的归属见 ADR-0044 与 ADR-0048：
每个站只有一个归属，Peach 自写的站只问自写解析器，其余经 amane 桥；来源名在这里登记一次，
链、候选、复核与账本的 provenance 都用同一个名字。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


POLICY_VERSION = "metadata-source-policy-v6"
PEACH_FIELDS = (
    "title", "original_title", "performers", "studio", "series", "release_date", "tags",
)


@dataclass(frozen=True)
class SourceSpec:
    name: str
    kind: str

    @property
    def official(self) -> bool:
        return self.kind in {"official", "official_mirror"}


#: 历史来源身份。这几家当年经 Javinizer-Go 取回过资料，账本里仍有
#: `javinizer:<站>:<字段>` 的 provenance（libredmm 的厂牌一百多行），旧快照也还在
#: `sources/metadata/javinizer-go/` 下。当前链不再向它们发请求（ADR-0044），名字留着是
#: 让结算与复核页仍认得这些行的级别；删掉名字的表现是已落库的官方值在复核页变成
#: 「未登记来源」。mgstage 同样有近 800 行历史标签，它现在经 amane 桥在素人链上（ADR-0048）；
#: dmm 当年也在这批里，现在由 Peach 自写的 GraphQL 解析器在有码链上回答（ADR-0059）。
HISTORICAL_SOURCES = (
    "libredmm", "tokyohot", "aventertainment", "caribbeancom",
    "dlgetchu", "jav321", "javstash",
)

SOURCE_SPECS = {
    name: SourceSpec(name, kind) for name, kind in {
        # r18.dev 是 DMM 数字版目录的镜像，采集链有码与素人排在片商站之后的那一档
        # （`library_processing.LibraryMetadataProvider.query`）。
        "r18dev": "official_mirror",
        # DMM 自己的 GraphQL 目录（`peach.sources.dmm`），有码链上 r18.dev 之后的兜底；
        # 历史上 Javinizer-Go 那一路的 `javinizer:dmm:*` provenance 也归这个名字。
        "dmm": "official",
        # FC2 发行方自己的商品页（`peach.sources.fc2`）。
        "fc2": "official",
        # 历史来源身份，见 `HISTORICAL_SOURCES`。
        "libredmm": "official_mirror",
        "tokyohot": "official",
        "aventertainment": "official", "caribbeancom": "official",
        "dlgetchu": "official",
        "jav321": "community", "javstash": "community",
        # Peach 自写解析器的社区站（`peach.sources.javdb`、`peach.sources.javbus`）。
        "javdb": "community", "javbus": "community", "javlibrary": "community",
        # Seesaa 作品表（`peach.sources.seesaa`，`scrape_codes --profile seesaa`）。
        "sougouwiki": "community",
        # Seesaa 上的两个认人 Wiki（同一模块，`scrape_codes --sources av_neme,av_name`）。
        "av_neme": "community", "av_name": "community",
        # AVBase 汇总各店铺的商品条目，采集任务在官方渠道落空时直接请求它
        # （`peach.sources.avbase`）。
        "avbase": "community",
        # 韩国 MIB 的官网（scripts/harvest_kmib.py）。登记在这里是为了复核与自动批准
        # 按官方来源对待它。
        "kmib": "official",
        # FC2 下架作品的镜像站。它转载的是发行方那一页，但标题和标签由站方用户维护，
        # 所以按社区来源对待：取值进复核，不当官方证据。
        "fc2cmadb": "community",
        # FC2 商品的元数据库（`peach.sources.fc2ppvdb`）与转载站 JAVten（`peach.sources.javten`），
        # 女优、卖家、标签由站方用户维护，都按社区来源对待（ADR-0060）。
        "fc2ppvdb": "community",
        "javten": "community",
        # 几个存档站也没有的下架 FC2 的最后一档（`peach.sources.javarchive`）。
        # 转载站，标题由发布者写，封面是转存件，按社区来源对待。登记在这里还有一层作用：
        # 「没有」的记忆按站记、记着每站的接入时刻（`library_processing._MissCache`）：接上这一站，
        # FC2 链上此前压着「封面没有」的番号下一轮就会重问一遍，不必等 TTL 走完。
        "javarchive": "community",
        # 一本道的官网作品 JSON（`peach.sources.onepondo`）。发行方自己那一份。
        "1pondo": "official",
        # 经 amane 桥问到的官方站（`peach.metadata_amane.OFFICIAL_SITES`，ADR-0048）：
        # makers 是按番号前缀路由到的片商官网，其余是各家发行方自己的站，mgstage 是
        # Prestige 系的配信店，素人番号的发行方那一页就在它上面。
        "makers": "official", "prestige": "official", "faleno": "official",
        "dahlia": "official", "mgstage": "official",
        # 经 amane 桥问到的社区站（`peach.metadata_amane.COMMUNITY_SITES`，ADR-0043）。都是
        # 转载或索引站，按社区来源对待。
        "fc2club": "community",
        "freejavbt": "community",
        "airav": "community",
        "avsox": "community",
    }.items()
}

#: 社区来源之间不一致时听这一家的。用户 2026-09-16 逐条核对过：javdb 比 javbus 准
#: （ADR-0034）。实测 `n0780` 的片长 javbus 报 36 分、javdb 报 96 分，盘里那条是 98 分。
PREFERRED_COMMUNITY_SOURCE = "javdb"

#: 兜底来源：同一个字段上还有别家可用时，它的取值不当证据（ADR-0035）。用户
#: 2026-09-16 逐条核对：javbus 的取值常常来自另一部片，`259LUXU-891` 它答的是
#: `259LUXU-1891`（ラグジュTV 1879，2026-07-29），而官方 mgstage 那页写的是
#: ラグジュTV 853、2017-11-26。只有一家都没有时才轮到它。
FALLBACK_SOURCES = ("javbus",)

#: 视频旁边那份 NFO。它不是联网来源，所以不进 `SOURCE_SPECS`，但字段优先级链
#: 要给它排一个位置（ADR-0029 起它就是补空证据），名字只能有一份。
LOCAL_NFO_SOURCE = "local_nfo"

#: 字段优先级链的层级，数字越小越先被采信（ADR-0038）。用户自己整理的 NFO 在最前，
#: 其次是发行方自己那页，再次是逐条核对过的 javdb，然后是其余社区站，最后是兜底。
#: 未登记的来源排在所有已知来源之后：它不构成证据，但也不该把整条链判成无解。
CHAIN_LOCAL_NFO, CHAIN_OFFICIAL, CHAIN_PREFERRED = 0, 1, 2
CHAIN_COMMUNITY, CHAIN_FALLBACK, CHAIN_UNKNOWN = 3, 4, 5

#: 稀疏例外：某个字段上把几家来源提到链首，顺序就是这里写的顺序（对应 amane 的
#: `field_priority`）。整条链已经按「谁更接近发行方」排好，这张表只用来记录逐条
#: 核对后发现的反例，不是第二份来源顺序表——每加一行都要能说出是哪个番号上看出来的。
#:
#: 演员栏的 fc2cmadb：只有 FC2 番号会问到它，它的女优栏按片中人逐条整理，javdb 那一侧
#: 常是转载站起的称呼。2026-09-23 逐条对照：`FC2-PPV-1449453` 与 `FC2-PPV-1464245`
#: javdb 写 `Chisa`、fc2cmadb 写 `大村阿美香`；`FC2-PPV-2629971` javdb 写中文 `安娜`、
#: fc2cmadb 写日文原名 `あんな`。FC2PPV-DB 的女优栏同样按片中人整理、写日文原名
#: （2026-09-24 `FC2-PPV-4898837` 写 `川北すずね`），排在 fc2cmadb 之后、javdb 之前。
FIELD_SOURCE_PRIORITY: dict[str, tuple[str, ...]] = {
    "performers": ("fc2cmadb", "fc2ppvdb"),
}

#: 字段级黑名单（对应 amane 的 `field_blacklist`），优先于一切：列进来的来源在这个
#: 字段上连候选都不算，不参与取值比对，也不会因为「只剩它一家」而被采信。它和兜底
#: 来源不是一回事——兜底是「有别家就退开」，黑名单是「这个字段上它说什么都不听」。
FIELD_SOURCE_BLACKLIST: dict[str, frozenset[str]] = {}


def source_tier(source: str) -> int:
    """这家来源在字段优先级链上的层级。"""
    name = str(source or "").strip()
    if name == LOCAL_NFO_SOURCE:
        return CHAIN_LOCAL_NFO
    if name in FALLBACK_SOURCES:
        return CHAIN_FALLBACK
    spec = SOURCE_SPECS.get(name)
    if spec is None:
        return CHAIN_UNKNOWN
    if spec.official:
        return CHAIN_OFFICIAL
    if name == PREFERRED_COMMUNITY_SOURCE:
        return CHAIN_PREFERRED
    return CHAIN_COMMUNITY


def preferred_tag_source(code: str | None) -> str | None:
    """有码作品的内容标签采用 JavDB 社区类别，其余类型沿用来源链。"""
    from .metadata_routes import classify

    return PREFERRED_COMMUNITY_SOURCE if code and classify(code) == "censored" else None


def chain_rank(field: str, source: str, *, code: str | None = None) -> tuple[int, int, str]:
    """字段优先级链上的排序键，越小越先采信。

    层内用 `FIELD_SOURCE_ORDER` 再排一次：官方来源之间谁更接近这部片的发行方，
    那张表早就按字段排过，不必再写第二份。表里没有的来源排在同层末尾，名字兜底
    保证同分时顺序是确定的——不确定的顺序会让同一批候选在两次运行里落不同的值。
    """
    name = str(source or "").strip()
    if name == LOCAL_NFO_SOURCE:
        return (-2, 0, name)
    if field == "tags" and preferred_tag_source(code):
        if name == preferred_tag_source(code):
            return (-1, 0, name)
    priority = FIELD_SOURCE_PRIORITY.get(field, ())
    if name in priority:
        return (-1, priority.index(name), name)
    order = FIELD_SOURCE_ORDER.get(field, ())
    within = order.index(name) if name in order else len(order)
    return (source_tier(name), within, name)


def blacklisted(field: str, source: str) -> bool:
    """这个字段上这家来源是不是被判了不听。"""
    return str(source or "").strip() in FIELD_SOURCE_BLACKLIST.get(field, frozenset())

#: 片商自己的站（`makers` 与三家单列的发行方）排在每个字段的最前：它们就是发行方那一页，
#: 镜像与配信店都从这里转来。一个番号至多问到其中一家（`metadata_routes.route_for_code`），
#: 四者之间的先后不会真的比出高下。
MAKER_SOURCES = ("makers", "prestige", "faleno", "dahlia")

FIELD_SOURCE_ORDER = {
    "title": (
        *MAKER_SOURCES, "dmm", "libredmm", "r18dev", "mgstage", "aventertainment",
        "caribbeancom", "1pondo", "tokyohot", "fc2", "javdb", "javlibrary",
        "javbus", "javstash", "jav321", "dlgetchu",
    ),
    "original_title": (
        *MAKER_SOURCES, "dmm", "libredmm", "r18dev", "mgstage", "aventertainment",
        "caribbeancom", "1pondo", "tokyohot", "fc2", "javdb", "javlibrary",
        "javbus", "javstash", "jav321", "dlgetchu",
    ),
    "performers": (
        *MAKER_SOURCES, "dmm", "libredmm", "r18dev", "mgstage", "aventertainment",
        "caribbeancom", "1pondo", "tokyohot", "fc2", "javdb", "javbus",
        "javlibrary", "javstash", "jav321", "dlgetchu",
    ),
    "studio": (
        *MAKER_SOURCES, "dmm", "libredmm", "r18dev", "mgstage", "aventertainment",
        "caribbeancom", "1pondo", "tokyohot", "fc2", "javdb", "javbus",
        "javlibrary", "javstash", "jav321", "dlgetchu",
    ),
    "series": (
        *MAKER_SOURCES, "dmm", "libredmm", "r18dev", "mgstage", "aventertainment",
        "caribbeancom", "1pondo", "tokyohot", "fc2", "javdb", "javlibrary",
        "javbus", "javstash", "jav321", "dlgetchu",
    ),
    # aventertainment 是面向海外的转售商，不是发行方，它给的是自己的上架日期：
    # `071213-625` 它答 2017-12-28，而这个番号本身就是发行日 2013-07-12
    # （javbus 与番号一致）；`092415-001` 同样差了 9 个月。发行方站点排在
    # 转售商前面，两个字段都要改——只改 tags 会留下一个照样写错日期的路径。
    # prestige 不在这张表上：它给的是配信开始日，只记在资料的 `extra['delivery_date']`，
    # 不当发行日候选（`metadata_amane.DELIVERY_DATE_SITES`）。
    "release_date": (
        "makers", "faleno", "dahlia", "dmm", "libredmm", "mgstage", "tokyohot", "caribbeancom", "1pondo",
        "aventertainment", "dlgetchu", "fc2", "r18dev", "javdb",
        "javlibrary", "javbus", "jav321", "javstash",
    ),
    # tag 单独把 mgstage 提到 dmm 之前。ABW-220 实测：mgstage 商品页给
    # 「性教育・中出し・巨乳・スレンダー」等 8 项，dmm 走的 mono/dvd 页和
    # libredmm 都只给「AV女優・単体作品・サンプル動画」3 项泛化类别，r18dev
    # 同样只有 3 项。厂牌、系列、日期这些字段仍以 dmm 为准，不跟着改。
    # 片商站里只有 makers 与 prestige 给标签，FALENO、DAHLIA 的作品页没有。
    "tags": (
        "mgstage", "makers", "prestige", "dmm", "libredmm", "tokyohot", "caribbeancom", "1pondo",
        "aventertainment", "dlgetchu", "fc2", "r18dev", "javstash",
        "javdb", "javlibrary", "javbus", "jav321",
    ),
}


def field_rank(field: str, source: str, *, code: str | None = None) -> int:
    """这家来源在这个字段的顺序表里排第几，从 1 起；表里没有的排在末尾之后。"""
    order = FIELD_SOURCE_ORDER[field]
    if field == "tags" and preferred_tag_source(code) and source == LOCAL_NFO_SOURCE:
        return -1
    if field == "tags" and source == preferred_tag_source(code):
        return 0
    try:
        return order.index(source) + 1
    except ValueError:
        return len(order) + 1


def sort_candidates(field: str, candidates: Iterable[Mapping[str, object]], *,
                    code: str | None = None) -> list[dict]:
    """同一字段的候选按来源顺序表排，同位次按置信度，再按来源名兜底保证确定。"""
    if field not in PEACH_FIELDS:
        raise ValueError("未知 Peach 元数据字段：" + field)
    rows = [dict(candidate) for candidate in candidates]
    for row in rows:
        row["field_rank"] = field_rank(field, str(row.get("source") or ""), code=code)
    rows.sort(key=lambda row: (
        int(row["field_rank"]),
        -float(row.get("confidence") or 0),
        str(row.get("source") or ""),
    ))
    return rows
