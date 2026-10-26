#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""从 javdb 资料页取女优的中文写法，产出可喂给 `localize_performer_names.py` 的映射。

账本里 95 位女优的规范名还卡在假名上（`深田えいみ`、`河合あすな`、`長谷川るい`），
按中文名一个都搜不到。`localize_performer_names.py` 靠的那份 avdb 映射 XML 不在这台
机器上，而且它对假名名字本来就只能等收录——逐字转写不成立，`飯岡かなこ` 换出来是
`饭冈かなこ`，半中半日比原样留着更糟。

**中文名是查出来的，不是转写出来的。** 这里查的是 javdb 的资料页：它是中文站，页面
顶部把现名的中文写法与日文写法并列在同一个 `actor-section-name` 里，两者**同页共现**，
所以对上的是这个人而不是同名的另一个人。判据见 `peach.javdb`。

    <span class="actor-section-name">深田詠美, 深田えいみ</span>
    <span class="section-meta">天海こころ</span>          ← 旧艺名，不参与配对

三条闸：

1. **账本的名字必须出现在「现名」那一栏。** 只出现在旧艺名里说明站上已经改了名，
   用哪个名字当规范名是人要决定的事（`京香じゅりあ` 那页现名是 `JULIA`），记 `旧名`。
2. **中文写法只认整名汉字、无假名无拉丁的那一个。** 有两个就是站上把别的名字也并进
   了同一栏，记 `多义`，不猜。
3. **搜索命中多页时全部记下来**，不取第一个——取第一个是默默替用户挑了一位。

现名栏里并排的两个值不一定是同一个名字的两种写法，也可能是两个艺名
（`一之瀨亞美莉, 美空あやか`）。姓对不上就不是一对，判 `不同名`；账本规范名压根不在
现名栏、只靠别名对上的，判 `改艺名`——换规范名是人要决定的事。

`?locale=zh-CN` 只切界面语言，女优名是数据不跟着变（2026-09-04 实测：简体界面下
`愛音麻里亞` 仍是繁体）。所以不带这个参数——带上只会让缓存键变一套、把已经取回的
页面再取一遍——繁转简由 opencc 做，它已经是本项目声明的依赖。

同一趟还顺带取**别名候选**（`--aliases`）：页面上这个人的中文写法里，账本还没有的
那几个。这两件事查的是同一批页面，分成两个脚本就要按 5 秒一页把站再走一遍。

别名是另一个问题，不是规范名的副产品。一个人在账本里只有一个中文名：刮削源给的是
日文与罗马字，译名那条路每人只给一个 `zh_cn`，而改统称时降为别名的是**日文原规范名**。
所以旧艺名的中译一个字也没进过账本——`橋本ありな` 在，`桥本有菜` 不在，按后者搜不到人。
资料页现名底下那一栏正是这个写法的出处（旧艺名与昵称混放，站上不标，这一步也不筛：
认不认得这个昵称是人的事），取候选要连它一起看，范围也要放开到已经有中文
规范名的人（`--scope all`）：她们才是缺第二个中文名的那一批。整批要走 300 多页、近一个
钟头；起点是某一位的资料页时用 `--only` 点名，两次请求就能出结论。

只产出复核 CSV，不写账本。规范名落库走 `localize_performer_names.py --mapping-csv`，
别名落库走 `apply_alias_candidates.py`，两边各有自己的冲突判定与备份门槛。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urljoin

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach import javdb   # noqa: E402
from peach.config import STATE_DIR   # noqa: E402
from peach.kanji import fold_glyphs   # noqa: E402
from peach.jobs import job_main   # noqa: E402
from peach.review_csv import write_rows   # noqa: E402
from peach.social_links import name_key   # noqa: E402

#: 发行元数据来源。账号型 performer 不是女优，不翻译。
RELEASE_SOURCES = ("r18:performer", "javbus:performer")
KANA = re.compile(r"[぀-ヿ]")
HAN = re.compile(r"[㐀-䶿一-鿿]")
LATIN = re.compile(r"[A-Za-z]")

OK, FORMER, AMBIGUOUS, SAME_SHAPE, LOGIN, MISSING, FAILED = (
    "ok", "旧名", "多义", "同形（站上只有日文名）", "要登录", "未取得", "取页失败")
#: 靠别名对上的页，现名栏写的是另一个艺名。换规范名是人要决定的事。
RENAMED = "改艺名（现名栏是另一个艺名）"
#: 现名栏并排的两个名字不是同一个名字的两种写法。
UNRELATED = "不同名（两栏姓氏对不上）"

#: 名字开头那一串汉字。日本艺名的姓写汉字、名写假名，中文写法保留同一个姓，
#: 所以这一串是中日两栏能不能配成一对的判据。
KANJI_HEAD = re.compile(r"[㐀-䶿一-鿿]+")

#: `adopt` 是这一行建议采用的规范名。`ok` 行就是查到的中文写法；站上已经改了名的
#: （`改艺名`、`旧名`）填站上的现名——有中文写法用中文写法，没有就用现名本身。
#: 采不采用是落库那一步按 `--accept` 决定的，这里只把候选摆出来。
FIELDS = ("entity_id", "current_name", "assets", "actor_id", "url",
          "jp", "zh_cn", "zh_tw", "adopt", "keywords", "verdict", "evidence")

#: 别名那一份。`alias` 是简体，`alias_zh_tw` 是站上原样——复核时要看得出转了什么。
#: `origin` 记这个写法出自哪一栏：别名栏那些正是账本缺的一批，也是最需要人认一眼的
#: 一批，两栏分开记，落库时才按得住各自的把握。
ALIAS_FIELDS = ("entity_id", "current_name", "assets", "actor_id", "url",
                "alias", "alias_zh_tw", "origin", "verdict", "evidence")

#: 现名底下那一栏站上没给标签，放的是这个人的其他叫法：旧艺名、昵称都在里面
#: （`RJM8` 是 `橋本ありな、上乃木まな、岩谷志季、橋本有菜`，`KxPb` 只有一个爱称 `傻梦`）。
#: 站上不给这一栏任何标签，解析层照样不给：判它是不是艺名要靠人认得这个人。
CURRENT_ROW, OTHER_ROW = "现名栏", "别名栏"
#: 搜到两页同名的人。规范名那一份把两页都记下来让人挑，别名这一份一个都不产：
#: 别名进的是身份，配错了人比缺一个写法更难查回来。
CROWDED = "多页（同名两位，分不清是哪一页）"
#: 这个写法已经挂在另一条实体名下。那是两条实体该不该合并的问题，不是补别名。
TAKEN = "占用（另一条实体已有这个名字）"
#: 页面上的中文写法账本都有了。这一行留着是为了证明这个人查过。
NOTHING = "无新写法"


@lru_cache(maxsize=1)
def _simplify():
    """繁转简。opencc 是 `naming` 这个可选依赖，缺了就明说，不静默产出繁体。"""
    try:
        import opencc
    except ImportError as exc:   # pragma: no cover - 取决于安装方式
        raise SystemExit("需要 opencc：pip install -e .[naming]") from exc
    return opencc.OpenCC("t2s").convert


def fold(text: str) -> str:
    """比名字用的折叠形：日本字形与繁体各折一道，假名不动。

    两道缺一不可。opencc 只管繁简，`永瀬` 转不成 `永濑`、`姫川` 转不成 `姬川`；
    `peach.kanji` 只收人名字形，站上的繁体写法它不全收。两边都折完才比得出
    `宫本さくら` 与 `宮本さくら` 是一个人。
    """
    return _simplify()(fold_glyphs(text or ""))


def key(name: str) -> str:
    """比名字的统一入口：先折叠字形，再按账本的归一规则取键。"""
    return name_key(fold(name))


def same_person(zh: str, jp: str) -> bool:
    """中日两种写法是不是同一个名字。

    javdb 的现名栏可能并排放着两个不同的艺名（`一之瀨亞美莉, 美空あやか`，别名栏里
    还另有 `一ノ瀬アメリ`），不是同一个名字的两种写法。判据是姓：日文写法开头那一串
    汉字必须原样出现在中文写法里。`美空` 不在 `一之濑亚美莉` 里，那就是配到了别人。
    共用单个字不算数——`美空` 与 `亞美莉` 都有 `美`，按字取交集会把这一对放过去。

    日文写法全是假名（`アンナ`、`あべみかこ`）时无从比，放行。
    """
    head = KANJI_HEAD.search(fold(jp))
    return not head or head.group(0) in fold(zh)


def name_owners(connection: sqlite3.Connection) -> dict[str, tuple[int, str]]:
    """全库 performer 的名字索引：折叠键 → （实体，规范名）。

    别名候选撞上别人的名字时不能当新写法加进去。要么是同一个人在账本里裂成了两条，
    要么是真有两位重名——两种都得人来判，而两种都不是「给这条实体补一个写法」。
    """
    connection.row_factory = sqlite3.Row
    out: dict[str, tuple[int, str]] = {}
    for row in connection.execute(
            "SELECT e.id,e.canonical_name,a.alias FROM entity e"
            " LEFT JOIN entity_alias a ON a.entity_id=e.id"
            " WHERE e.kind='performer' ORDER BY e.id"):
        owner = (int(row["id"]), str(row["canonical_name"]))
        for name in (row["canonical_name"], row["alias"]):
            if name:
                out.setdefault(key(str(name)), owner)
    return out


def targets(connection: sqlite3.Connection, scope: str = "kana",
            only: Sequence[str] = ()) -> list[dict]:
    """有发行来源的 performer，连它的名字链。

    `kana` 只要规范名还带假名的那些——那是「中文名缺席」的判据。`all` 覆盖全部，
    补别名要找的恰恰是已经有中文规范名的人：她们的第二个中文写法一个都没进过账本。

    `only` 按实体 id 或名字链上任一写法点名几位，两道筛选都让位给它。整批要走 300
    多页、近一个钟头，而真正的起点常常是「我看着这一位的资料页，觉得少个名字」。
    点名的人被范围挡在外面就没法查，那正是最需要查的一位。
    """
    connection.row_factory = sqlite3.Row
    named = {str(value).strip() for value in only if str(value).strip()}
    named_keys = {key(value) for value in named}
    aliases: dict[int, list[str]] = {}
    for row in connection.execute(
            "SELECT entity_id,alias FROM entity_alias ORDER BY entity_id,alias"):
        aliases.setdefault(int(row["entity_id"]), []).append(str(row["alias"]))
    out = []
    for row in connection.execute(
        "SELECT e.id,e.canonical_name,"
        " (SELECT count(DISTINCT ae.asset_id) FROM asset_entity ae WHERE ae.entity_id=e.id) n,"
        " (SELECT group_concat(DISTINCT ae.source) FROM asset_entity ae"
        "   WHERE ae.entity_id=e.id) sources"
        " FROM entity e WHERE e.kind='performer' ORDER BY e.id"
    ):
        name = str(row["canonical_name"])
        sources = set(filter(None, str(row["sources"] or "").split(",")))
        chain = list(dict.fromkeys([name, *aliases.get(int(row["id"]), [])]))
        if named:
            if (str(row["id"]) not in named
                    and not named_keys.intersection(key(value) for value in chain)):
                continue
        elif scope == "kana" and not KANA.search(name):
            continue
        elif not sources.intersection(RELEASE_SOURCES):
            continue
        out.append({"entity_id": int(row["id"]), "name": name,
                    "assets": int(row["n"]), "chain": chain})
    return out


def chinese_writings(names: list[str]) -> list[str]:
    """一栏名字里的中文写法：整名汉字、无假名无拉丁的那些。

    掺一个假名就是日文写法（`きみかわ結衣`），掺一个拉丁字母就是艺名本体（`JULIA`）。
    """
    return [value for value in names
            if HAN.search(value) and not KANA.search(value) and not LATIN.search(value)]


def chinese_name(current: list[str]) -> tuple[str, str]:
    """现名那一栏里的中文写法与判据。只认唯一那一个。"""
    han = chinese_writings(current)
    if len(han) == 1:
        return han[0], ""
    if not han:
        return "", SAME_SHAPE
    return "", AMBIGUOUS


def adopted_name(current: list[str]) -> str:
    """站上现名里该当规范名的那一个：有中文写法就用中文写法，否则用第一个写法。"""
    zh, _ = chinese_name(current)
    return zh or (current[0] if current else "")


def judge(record: dict, html: str, url: str) -> dict:
    """一页资料页对一位账本女优的判定。"""
    current = javdb.current_names(html)
    wanted = {key(value) for value in record["chain"]}
    row = {"entity_id": record["entity_id"], "current_name": record["name"],
           "assets": record["assets"], "actor_id": javdb.actor_id(html), "url": url,
           "jp": "", "zh_cn": "", "zh_tw": "", "adopt": "",
           "keywords": "|".join(current),
           "verdict": "", "evidence": f"现名 {'、'.join(current) or '未取得'}"}
    if not wanted & {key(value) for value in current}:
        row.update(verdict=FORMER, jp=record["name"], adopt=adopted_name(current),
                   evidence=f"账本名只出现在旧艺名里；站上现名 {'、'.join(current) or '未取得'}")
        return row
    if key(record["name"]) not in {key(value) for value in current}:
        row.update(verdict=RENAMED, jp=record["name"], adopt=adopted_name(current),
                   evidence=(f"靠别名对上的页；账本规范名 {record['name']} 不在现名栏，"
                             f"站上现名 {'、'.join(current)}"))
        return row
    zh_tw, why = chinese_name(current)
    if not zh_tw:
        row["verdict"] = why
        return row
    jp = next((value for value in current if KANA.search(value)), record["name"])
    if not same_person(zh_tw, jp):
        row["verdict"] = UNRELATED
        row["evidence"] = f"现名栏并排的 {zh_tw} 与 {jp} 姓氏对不上，不是同一个名字"
        return row
    row.update(jp=jp, zh_tw=zh_tw, adopt=zh_tw, verdict=OK,
               evidence=f"资料页现名一栏同时写着 {zh_tw} 与 {jp}")
    return row


def _note(record: dict, url: str, verdict: str, evidence: str) -> dict:
    """两份 CSV 共用的那几列。取页失败、要登录、搜不到都只有这几列可填。"""
    return {"entity_id": record["entity_id"], "current_name": record["name"],
            "assets": record["assets"], "url": url,
            "verdict": verdict, "evidence": evidence}


def alias_candidates(record: dict, html: str, url: str,
                     owners: dict[str, tuple[int, str]]) -> list[dict]:
    """这一页上账本还没有的中文写法。

    现名栏与别名栏都算。同一页上的名字都是这个人的，站方自己这么归的——这也正是
    别名栏能用在这里、却不能用来定规范名的原因：作为**别名**它只需要「确实是她用过
    的写法」，而定规范名还要判「现在该叫哪个」，后者是人的事。别名栏里混着昵称
    （`傻梦`），这一步也不筛：站上没标，判它是不是艺名要靠人认得这个人。

    一个新写法都没有时也回一行。「查过，没查出东西」和「还没轮到她」在复核件里必须
    分得开，否则下一趟不知道该从哪儿接着跑。
    """
    convert = _simplify()
    have = {key(value) for value in record["chain"]}
    actor = javdb.actor_id(html)
    rows: list[dict] = []
    seen: set[str] = set()
    for names, origin in ((javdb.current_names(html), CURRENT_ROW),
                          (javdb.former_names(html), OTHER_ROW)):
        for written in chinese_writings(names):
            folded = key(written)
            if folded in have or folded in seen:
                continue
            seen.add(folded)
            row = {"entity_id": record["entity_id"], "current_name": record["name"],
                   "assets": record["assets"], "actor_id": actor, "url": url,
                   "alias": convert(written), "alias_zh_tw": written, "origin": origin,
                   "verdict": OK, "evidence": f"资料页{origin}写着 {written}"}
            owner = owners.get(folded)
            if owner and owner[0] != record["entity_id"]:
                row.update(verdict=TAKEN,
                           evidence=f"{owner[1]}（实体 {owner[0]}）已经用着这个写法")
            rows.append(row)
    if not rows:
        row = _note(record, url, NOTHING,
                    f"页面上的写法 {'、'.join(javdb.all_names(html)) or '未取得'}，"
                    "没有一个是账本以外的中文写法")
        row["actor_id"] = actor
        rows.append(row)
    return rows


def harvest(connection: sqlite3.Connection, site, limit: int,
            scope: str = "kana", only: Sequence[str] = ()) -> tuple[list[dict], list[dict]]:
    """走一趟站，两份候选一起出：规范名的与别名的。

    分成两个脚本就要按 5 秒一页把同一批资料页再走一遍。规范名那一份始终只收规范名
    带假名的人，`--scope all` 放开的只是别名那一份的范围。
    """
    owners = name_owners(connection)
    records = targets(connection, scope, only)
    rows: list[dict] = []
    alias_rows: list[dict] = []

    def note(record: dict, url: str, verdict: str, evidence: str) -> None:
        row = _note(record, url, verdict, evidence)
        if KANA.search(record["name"]):
            rows.append(row)
        alias_rows.append(dict(row))

    for record in records[:limit] if limit else records:
        wanted = {key(value) for value in record["chain"]}
        hits: list[str] = []
        search = ""
        error = None
        for name in record["chain"]:
            search = javdb.SEARCH.format(quote(name))
            try:
                hits = javdb.search_hits(site.get(search), wanted, key)
            except Exception as exc:   # noqa: BLE001 - 取页失败要落进 CSV，不能中断整批
                error = f"{type(exc).__name__}: {exc}"
                break
            if hits:
                break
        if error:
            note(record, search, FAILED, error)
            continue
        if not hits:
            note(record, search, MISSING,
                 f"搜过名字链的 {len(record['chain'])} 个写法，站上没有这个人")
            continue
        crowded = len(hits) > 1
        if crowded and not KANA.search(record["name"]):
            # 这一位只为别名候选而来，而别名候选在多页时一个都不产。那就别把这几页
            # 取回来了——每页 5 秒，取回来也只是再写一遍同一句「分不清是哪一页」。
            alias_rows.append(_note(record, search, CROWDED,
                                    f"搜到 {len(hits)} 页同名的人"))
            continue
        for path in hits:
            url = urljoin(javdb.BASE, path)
            try:
                html = site.get(url)
            except Exception as exc:   # noqa: BLE001
                note(record, url, FAILED, f"{type(exc).__name__}: {exc}")
                continue
            if javdb.LOGIN.search(html):
                note(record, url, LOGIN, "这一页要登录才给，不注册账号")
                continue
            if KANA.search(record["name"]):
                rows.append(judge(record, html, url))
            if crowded:
                alias_rows.append(_note(record, url, CROWDED,
                                        f"搜到 {len(hits)} 页同名的人"))
                continue
            alias_rows.extend(alias_candidates(record, html, url, owners))
    return rows, alias_rows


def localize(rows: list[dict]) -> list[dict]:
    """给判成 `ok` 的行填简体写法。繁体原样留在 `zh_tw`，复核时要看得出转了什么。"""
    convert = _simplify()
    for row in rows:
        # 没判成 `ok` 的行也要有这一列且为空：复核件里空格与「这一格不存在」在人眼里
        # 一样，在按列取值的代码里不一样。
        row["zh_cn"] = convert(str(row["zh_tw"])) if row.get("verdict") == OK else ""
        # 站上的现名可能本来就是日文（`きみかわ結衣`）。繁转简会把它变成
        # `きみかわ结衣`——半假名半简体，比原样留着更糟。整名汉字的才转。
        adopt = str(row.get("adopt") or "")
        row["adopt"] = adopt if KANA.search(adopt) or LATIN.search(adopt) else convert(adopt)
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从 javdb 资料页取女优中文名，产出映射 CSV")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--aliases", type=Path,
                        help="别名候选 CSV：页面上账本还没有的中文写法")
    parser.add_argument("--scope", choices=("kana", "all"), default="kana",
                        help="kana 只查规范名还带假名的人；all 查全部有发行来源的人")
    parser.add_argument("--only", action="append", default=[],
                        help="只查点名的这几位：实体 id 或她名字链上的任一写法，"
                             "逗号分隔，可重复给；给了就不受 --scope 限制")
    parser.add_argument("--limit", type=int, default=0)
    # javdb 按出口 IP 自己封速率。5 秒是 `harvest_directory_links.SOURCE_INTERVAL`
    # 定下的那一档，两处必须一致：换个脚本就换个速度等于没有速度约束。
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--cache-dir", type=Path, default=STATE_DIR / "directory-links")
    parser.add_argument("--lock", type=Path, default=STATE_DIR / ".javdb-cn-names.lock")
    parser.add_argument("--refresh", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    from peach.page_cache import Site

    if args.scope == "all" and not args.aliases:
        raise SystemExit("--scope all 放开的是别名候选的范围，请一并给 --aliases <路径>")
    only = [part.strip() for value in args.only for part in str(value).split(",")
            if part.strip()]
    site = Site(args.cache_dir / "javdb", max(args.interval, 5.0), args.timeout,
                refresh=args.refresh, via_proxy=True)
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        rows, alias_rows = harvest(connection, site, args.limit, args.scope, only)
        rows = localize(rows)
    finally:
        connection.close()
    write_rows(args.out, FIELDS, rows, fill_missing=True)
    counts = Counter(str(row.get("verdict")) for row in rows)
    print(f"javdb 中文名 {len(rows)} 行，映射 CSV：{args.out}")
    print("  判定分布：", dict(counts))
    for row in rows:
        if row.get("adopt"):
            print(f"    [{row['verdict']}] {row['current_name']} -> {row['adopt']}"
                  f"  ({row['evidence']})")
    if args.aliases:
        write_rows(args.aliases, ALIAS_FIELDS, alias_rows, fill_missing=True)
        fresh = [row for row in alias_rows if row.get("verdict") == OK]
        print(f"javdb 别名候选 {len(alias_rows)} 行（新写法 {len(fresh)} 个），"
              f"CSV：{args.aliases}")
        print("  判定分布：", dict(Counter(str(row.get("verdict")) for row in alias_rows)))
        for row in fresh:
            print(f"    [{row['origin']}] {row['current_name']} + {row['alias']}"
                  f"  ({row['url']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(job_main(build_parser, run))
