"""内容产地：作品属于哪个发行体系。

为什么需要这一维：在这之前「是不是 JAV」完全由番号形态加发行证据推出来
（`catalog_rules.is_jav_asset`），而番号形态是日本厂牌的写法——韩国 MIB 的
`CA-103`、国产麻豆的 `MDX-0123` 和日本的 `MEYD-911` 在形状上没有任何区别。
`is_korean_mib_code` 那份前缀名单认得出 MIB，但它只接在刮削入口上，分类口径
从来没读过它，于是 MIB 的片照样落进 JAV 页。产地是这批判断共同缺的那个字段。

产地说的是**发行体系**，不是拍摄地，也不是演员国籍。JAV 里有欧美女优，欧美片里
有亚洲演员，按人判会把同一个厂牌的片拆到两个产地去。

## 分区取值

只有五个，都不细分到国家：

- `jp` 日本、`kr` 韩国、`cn` 国产（含港台华语）、`west` 欧美、`other` 其他。
- 空串是「未判定」，和 `other`（判过，就是不属于上面四类）不是一回事。混用会让
  「还有多少片没判产地」这个数永远算不出来。

为什么不存 ISO 国家码再派生分区：库里要区分的就是这几个发行体系，拆到国家一级
之后每一条推断规则都要回答「捷克拍的美国厂牌片算哪国」，而这个问题对使用者没有
意义。取值集合写在这里而不是 SQL `CHECK` 里，就是留给将来真要细分时用的——加一个
值只改本文件，不动账本。

## 判定来源

真相字段 `asset.region` 走 `field_owners`，和 `studio`、`release_date` 同级：
用户填的和用户批准的，自动写入者覆盖不掉。

`entity.region` 是厂牌与创作者这一层的产地。资产没有自己的 `region` 时按实体投影读。

`infer_region` 只认番号推得出的那部分，推不出返回空串交给人。它产出的是候选，
不自己坐实：厂牌名单再长也追不上新厂牌，而猜错的产地会把片从 JAV 页里藏掉。
"""
from __future__ import annotations

import re

from .catalog_rules import (
    is_korean_mib_code,
    normalise_code_key,
    western_release_identity,
)

#: 未判定。不是「其他」——那是判过之后的结论。
REGION_UNKNOWN = ""

JAPAN = "jp"
KOREA = "kr"
CHINA = "cn"
WEST = "west"
OTHER = "other"

#: 存得进账本的分区码，顺序就是界面上的排列顺序：按亚洲用户片库里的实际体量排，
#: 日本在最前。`other` 永远垫底。
REGIONS: tuple[str, ...] = (JAPAN, KOREA, CHINA, WEST, OTHER)

#: 给人看的写法。`cn` 叫「国产」不叫「中国」：中文资源圈里这个词就指这批片，
#: 换成「中国」反而要多解释一句它包不包括港台。
REGION_LABELS: dict[str, str] = {
    JAPAN: "日本",
    KOREA: "韩国",
    CHINA: "国产",
    WEST: "欧美",
    OTHER: "其他",
}

#: 国产厂牌的番号前缀。这批片的番号写法完全照搬 JAV，只有名单能把它们分开。
#: 取的是麻豆、天美、蜜桃、精东、星空无限、果冻这几家的常见前缀，以及台湾 SWAG。
#: 名单不追求全：推不出来的走人工，推错了才是要命的。
CHINA_CODE_PREFIXES: frozenset[str] = frozenset({
    "MD", "MDX", "MDS", "MDSJ", "MDBK", "MDCM", "MDJ", "MDL", "MDUS", "MDWP",
    "MKY", "MMZ", "MSD", "MTVQ",
    "TM", "TMW", "TMG", "TMP", "TMQ", "TMS", "TMTC",
    "PM", "PME", "PMC", "PMX",
    "JD", "JDBC", "JDYL", "JDSY",
    "XK", "XKG", "XKQP", "XSJ",
    "GDCM", "JELLY",
    "RAS", "ROC", "TZTV", "LAL", "LY", "XBH",
    "SWAG",
})

#: 韩国厂牌的番号前缀。MIB 自己那批走 `catalog_rules.is_korean_mib_code`——那份名单
#: 是演员名缩写，会随片源变长，两处各自维护。这里只放厂牌型前缀。
KOREA_CODE_PREFIXES: frozenset[str] = frozenset({
    "KBJ", "KOREA", "KNM",
})

#: `<字母>-<数字>` 这一种写法下的字母段。素人系的三位数字前缀（`300MIUM-1239`）
#: 不在此列：它们的发行体系本来就是日本的。
_PREFIX = re.compile(r"^([A-Z]+)-\d+$")


def is_region(value: str | None) -> bool:
    """这个取值存得进 `region` 列吗；未判定的空串也算。"""
    text = str(value or "").strip()
    return text == REGION_UNKNOWN or text in REGIONS


def normalize_region(value: str | None) -> str:
    """把外部来的产地取值收敛成账本写法；认不出的一律当未判定。

    认不出就退回未判定而不是报错：这个值会从查询参数、候选和实体投影三个方向进来，
    其中任何一个给了旧写法时，正确的结果是「这条还没判产地」，不是整页 500。
    """
    text = str(value or "").strip().lower()
    return text if text in REGIONS else REGION_UNKNOWN


def region_label(value: str | None) -> str:
    """给人看的产地写法；未判定返回空串。"""
    return REGION_LABELS.get(normalize_region(value), "")


def _code_prefix(code: str | None) -> str:
    shape = _PREFIX.match(normalise_code_key(code).upper())
    return shape.group(1) if shape else ""


def infer_region(code: str | None = None, name: str | None = None) -> str:
    """从番号和文件名推产地；推不出返回未判定。

    顺序是固定的，从最硬的证据往下走：韩国 MIB 的前缀名单有实测支撑（账本里这批
    前缀的片全在 `B:\\MVP\\MIB\\` 下、目录外零条），国产与韩国厂牌前缀次之，
    西片的「厂牌.发行日」形态最后——它不是番号，和前面几条撞不上。

    日本不在这里推。番号形态本身就是日本厂牌的写法，把「剩下的都算日本」写成规则
    会让每一个没收录的国产厂牌自动变成 JAV，正是这次要修的那个毛病。日本的产地
    由用户批准或按厂牌实体投影落下来。
    """
    if is_korean_mib_code(code):
        return KOREA
    prefix = _code_prefix(code)
    if prefix:
        if prefix in CHINA_CODE_PREFIXES:
            return CHINA
        if prefix in KOREA_CODE_PREFIXES:
            return KOREA
    if western_release_identity(name):
        return WEST
    return REGION_UNKNOWN
