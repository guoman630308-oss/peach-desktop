"""出演者栏的写法：剪出艺名，认出企划名义，再用本机已有的证据把它换回主艺名。

素人系与企划系的官方页把年龄、职业甚至整句宣传语写进出演者栏
（`本庄美奈子 30歳 元カフェ店員`、`超バドミントン部あかりちゃん 23歳 潮吹き部長`）。
照抄会把整句变成实体名，所以要先剪；剪完仍认不出边界的那些是**企划名义**——这部片
给她起的称呼，不是这个人的主艺名。

企划名义此前一律交人工（ADR-0025/0034）。ADR-0038 给它加了一条本机就能走通的出路：
同一个番号在 Seesaa 素人系総合 Wiki 与 AV女優名鑑 上常常直接写着正式艺名
（`200GANA-2245` 的出演者栏是「めぐみ 28歳 パパ活女子」，Wiki 那一行写的是
`目黒めぐみ`），而这些页面的快照早就落在本机 `sources/metadata/javinizer-go/<番号>/`
下。账本自己的 `entity_alias` 是第二条路：`Sarina Momonaga` 已经登记在 `藤木真央`
名下，`みほちゃん` 登记在 `未步奈奈` 名下。

两条路都只读本机已有的东西，不新增站点、不发请求。解不出就照旧交人工——这一层
不从宣传语里猜人名，ADR-0025 否决过那件事。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .entities import normalize_entity_name, resolve_entity

#: 艺名到年龄标记为止，后面是介绍；`はな/19歳/…` 用斜杠分段，同理。
_PERFORMER_INTRO = re.compile(r"[\s　]*[（(]?\d+\s*歳.*$")
_PERFORMER_SEPARATOR = re.compile(r"[/／]")
#: 斜杠分段里单独成段的年龄。MGS 的 `300NTK` 把介绍写在最前：`…オフパコ！！/はな/20歳`，
#: 艺名是年龄前面那一段，不是第一段。
_AGE_SEGMENT = re.compile(r"\s*\d+\s*歳\s*")
#: 剪完仍带空白、分隔符或敬称的不是艺名，是企划文案（`超バドミントン部あかりちゃん`、
#: `まゆみさん`）。剪到哪儿才对，本身就是个判断。
_NOT_A_STAGE_NAME = re.compile(r"[\s　/／]|ちゃん$|さん$")
#: 描述性称呼：身高、人数、外貌、身份与宣传语，不是谁的艺名。fc2cmadb 与 javdb 的
#: 素人条目常把标题里的称呼填进出演者栏（`145cm色白お嬢様`、`人気焼肉店の看板娘`、
#: `美女6名`），照抄就是一个永远找不到头像的女优实体。2026-09-24 拿它扫账本全部 939
#: 位女优，命中 22 位，全是这一类；`OL` 区分大小写，免得撞上 `Ssol`、`Cola酱`。
#: fc2ppvdb 给匿名素人开的女优页也拿称呼当页名（`犬好きFカップの音大生`、`究極のGカップ`、
#: 中文的 `牛逼`）；2026-09-29 扫全部 987 位女优，第二段词只命中这一类的 8 位。
_DESCRIPTIVE = re.compile(
    r"\d+\s*(?:cm|CM|㎝|センチ|名|人)|[!！]"
    r"|身長|長身|スレンダー|爆乳|巨乳|美乳|美脚|美尻|色白|パイパン|くびれ|クビレ"
    r"|女子|下級生|上級生|女優|グラドル|現役|看板娘|先生|少女|お嬢様|嬢$|美女|美人|ギャル|OL"
    r"|人妻|熟女|素人|店員|主婦|新人|の様な|のような|みたいな|働く|平凡|人気|伝説|抜群"
    r"|きれいな|綺麗な|可愛い|かわいい"
    r"|[A-Za-zＡ-Ｚ]カップ|変態|従事者|事務所|活躍|後輩|先輩|地元|大生|声優|究極|顔の|メンヘラ"
    r"|天真爛漫|^超美$|^牛逼$")


def is_descriptive(name: str) -> bool:
    """这个写法是描述性称呼，不是艺名。"""
    return bool(_DESCRIPTIVE.search(str(name or "")))


def stage_name(name: str) -> str | None:
    """出演者栏里的艺名；认不出艺名边界、或剪出来的是描述性称呼时返回 None。"""
    segments = _PERFORMER_SEPARATOR.split(str(name or ""))
    ages = [index for index, part in enumerate(segments) if _AGE_SEGMENT.fullmatch(part)]
    named = segments[ages[0] - 1] if ages and ages[0] > 0 else segments[0]
    trimmed = _PERFORMER_INTRO.sub("", named).strip()
    if not trimmed or _NOT_A_STAGE_NAME.search(trimmed) or is_descriptive(trimmed):
        return None
    return trimmed


def is_planning_alias(name: str) -> bool:
    """这个写法是不是企划名义——这部片给她起的称呼，不是这个人的主艺名。

    两种形态。一种是把年龄职业写在名字后面（`桜井奈々 28歳 某企業広報担当`）：介绍
    这一段只在这部片里成立，带着它的那个名字同样只在这部片里成立。另一种是剪完仍
    认不出艺名边界的（`佐倉井さん`、`超バドミントン部あかりちゃん`）。

    判的是写法不是人：同一个人在别处用主艺名登记，这里的一次性称呼跟那条记录对不上，
    却不说明账本存错了。
    """
    return bool(_PERFORMER_INTRO.search(name)) or stage_name(name) is None


#: 按番号写着正式出演者名的本机快照，按可信度排列。两家都是把作品与女优对起来的
#: 名鑑，不是商品页——商品页那一栏正是企划名义的来源，拿它解它自己没有意义。
WORK_NAME_SOURCES = ("sougouwiki", "avnamewiki")

#: 企划名义登记进 `entity_alias` 时记的来源。回溯「这个别名是谁写的」时它是唯一
#: 入口，两处各写一份字面量就等于埋着一次静默的分叉。
PLANNING_ALIAS_SOURCE = "javinizer:planning-alias"


def cached_work_names(metadata_root: Path | None, codes,
                      sources=WORK_NAME_SOURCES) -> list[tuple[str, str]]:
    """本机快照里这部片写着的出演者名：`[(来源, 名字)]`，按 `sources` 的顺序。

    只读已经落盘的那一份，不联网。快照里的失败记录（`error` 而不是 `result`）当
    没有——「这次没问出结果」不是「这部片没有出演者」。
    """
    if metadata_root is None:
        return []
    found: list[tuple[str, str]] = []
    for source in sources:
        for code in dict.fromkeys(str(value or "").strip() for value in codes):
            path = Path(metadata_root) / code / f"{source}.json"
            if not code or not path.is_file():
                continue
            try:
                wrapper = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            result = wrapper.get("result")
            if not isinstance(result, dict):
                continue
            for person in result.get("actresses") or []:
                name = str((person or {}).get("japanese_name") or "").strip()
                if name:
                    found.append((source, name))
    return found


def _settled_stage_name(pairs) -> tuple[str, str] | None:
    """这一批写法解出的唯一主艺名和给出它的来源；不止一个人时返回 None。

    不止一个名字就是这部片有多位出演者（或两家快照各说各的），落哪一个都是替用户
    做取舍，交回人工。
    """
    resolved: dict[str, str] = {}
    for source, name in pairs:
        if not is_planning_alias(name) and stage_name(name):
            resolved.setdefault(name, source)
    if len(resolved) != 1:
        return None
    name, source = next(iter(resolved.items()))
    return name, source


def resolve_planning_alias(connection, alias: str, *, metadata_root: Path | None = None,
                           codes=()) -> tuple[str, str] | None:
    """把一个企划名义换成主艺名，返回 `(艺名, 证据来源)`；解不出返回 None。

    先看本机快照里这部片写着的名字，再看账本已经登记的别名。顺序是有意的：快照
    讲的是「这部片的出演者是谁」，别名讲的是「这个写法指谁」，前者对这一行更直接。
    实测 `ナミちゃん` 两条路都有命中，而别名那条指向的实体规范名就是 `ナミちゃん`
    本身——企划名义被当成人建过一条实体。所以别名这条路要再判一次形态：换来的还是
    企划名义就不算解出来，那只是把同一个称呼换了个地方说。
    """
    settled = _settled_stage_name(cached_work_names(metadata_root, codes))
    if settled:
        return settled
    entity = resolve_entity(connection, "performer", alias)
    if entity is None:
        return None
    canonical = str(entity["canonical_name"]).strip()
    if canonical == alias.strip() or is_planning_alias(canonical):
        return None
    return canonical, "entity-alias"


def register_planning_alias(connection, entity_id: int, alias: str) -> None:
    """把企划名义登记成这条实体的别名，来源标明它是企划名义。

    落库写的是主艺名，而用户在页面上搜的很可能是封面上印的那个称呼。不登记的话，
    这次解析出的对应关系只存在于 `review_decision` 的一行 note 里，搜索用不上它。
    """
    name = str(alias or "").strip()
    if not name:
        return
    connection.execute(
        "INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,confidence)"
        " VALUES(?,?,?,?,0.9)",
        (int(entity_id), name, normalize_entity_name(name), PLANNING_ALIAS_SOURCE),
    )
