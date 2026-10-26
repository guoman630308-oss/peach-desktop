#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把历史证据里的 javdb 演员 id 与 minnano-av 女优 id 落成 `entity_external_ref`。

人物资料页的外部入口按 `entity_external_ref` 里的站点 id 拼地址，没有 id 的站点不出现。
采集作品页时 id 跟着名字一起回来（`sources.javdb.actresses`）；账本里已有的
几百位女优，她们的 id 散在两处**已经取回来的**证据里：

- **javdb 的页面缓存**（`peach.page_cache.Site` 落盘的整页 HTML）。资料页里有
  `href="/actors/<id>/collect"`，解析走 `peach.javdb`，和取中文名那条路同一份判据。
  搜索结果页的演员卡也带 id：资料页没取回（限流、要登录）的那些人，id 就在卡上。
  两种页都只按收得下的写法认人（`performer_alias_followup.rejection`，`葵` 这种短单名不算），
  对上的几张卡还得是同一个人（`javdb.one_person`：有碼、無碼各至多一张），否则记「站上同名」。
- **复核 CSV**。`harvest_agency_rosters.py` 的名册件每行都带 `entity_id` 与
  `actress_id`，那就是 minnano-av 的女优 id；`harvest_javdb_cn_names.py` 的件带
  `actor_id`。

两处都是磁盘上现成的东西，这个脚本一次网都不出。默认 dry-run，只产出复核 CSV；
`--apply` 才写库，并且必须同时给 `--backup`。

名字对不上就不登记：javdb 页那一侧按整条名字链（规范名加别名）匹配，全半角、大小写与
日本字形折掉再比（`performer_alias_followup.match_key`）：账本写 `泽村玲子`、站上写
`澤村玲子` 是同一个人。

判定按「这一条引用能不能落库」逐条给，可以重复跑：

- `ok`：账本里没有这一行，可以写。同一位在一个站上对上几个 id 就出几行——javdb 上
  同一位女优有两个演员页是常事，两边挂的作品不同，都要登记（0032 放宽了唯一约束）。
- `已登记`：这一行账本里已经有了，跳过。
- `id 已归他人`：这个 id 在账本里挂在另一位实体名下。站上的一个 id 只能属于一位，
  写下去会被主键挡掉，所以点名占有者等人判，不靠 `INSERT OR IGNORE` 无声吞掉。
- `冲突`：这一批证据里有两位实体认领同一个 id，同样不挑一个。
- `站上同名`：搜索卡片上对得上她的不止一个人，id 列在证据里等人判。她已经登记过
  javdb id 的不出这一行。
- `只有 FC2 作品`：她没有 JAV 目录站 id，带番号的作品又全是 FC2。这是 FC2 个人摄创作者，
  javdb 不按人收录，名字对上的演员页是同名的另一个人，不写。
"""
from __future__ import annotations

import argparse
import collections
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach import javdb   # noqa: E402
from peach.config import STATE_DIR   # noqa: E402
from peach.entry_links import EXTERNAL_KIND, JAV_DIRECTORIES   # noqa: E402
from peach.performer_alias_followup import match_key, rejection   # noqa: E402
from peach.review_csv import read_rows, write_rows   # noqa: E402
from peach.scripting import add_ledger_write_args, open_for_write, verify_after_write   # noqa: E402

JAVDB, MINNANO = "javdb", "minnano-av"
#: 复核 CSV 里这两列就是站点 id，列名由产出它们的脚本定下。
CSV_COLUMNS = {"actor_id": JAVDB, "actress_id": MINNANO}

OK, HAVE, CONFLICT = "ok", "已登记", "冲突"
TAKEN, NOT_PERFORMER, NAMESAKE = "id 已归他人", "不是女优实体", "站上同名"
FC2_ONLY = "只有 FC2 作品"

FIELDS = ("entity_id", "canonical_name", "provider", "external_id",
          "origin", "verdict", "evidence")


def name_owners(connection: sqlite3.Connection) -> dict[str, set[int]]:
    """折叠键 → 拥有这个写法的女优实体。一个键对上两条就是重名，不猜。

    只收本身收得下的写法：资料页和搜索卡片都只凭名字认人，`葵`、`蕾` 这种短单名会认到别人。
    """
    owners: dict[str, set[int]] = collections.defaultdict(set)
    for entity_id, written in connection.execute(
            "SELECT e.id,e.canonical_name FROM entity e WHERE e.kind='performer'"
            " UNION SELECT a.entity_id,a.alias FROM entity_alias a"
            " JOIN entity e ON e.id=a.entity_id WHERE e.kind='performer'"):
        if written and not rejection(str(written)):
            owners[match_key(str(written))].add(int(entity_id))
    return owners


def _cached_pages(cache_dir: Path):
    for path in sorted(Path(cache_dir).glob("*.html")):
        try:
            yield path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue


def from_page_cache(cache_dir: Path, owners: dict[str, set[int]]) -> dict:
    """缓存下来的 javdb 资料页 → 实体 → 演员 id 集合。"""
    found: dict[int, set[str]] = collections.defaultdict(set)
    for html in _cached_pages(cache_dir):
        actor = javdb.actor_id(html)
        if not actor:
            continue
        for written in javdb.all_names(html):
            for entity_id in owners.get(match_key(written), ()):
                found[entity_id].add(actor)
    return found


def from_search_cards(cache_dir: Path, owners: dict[str, set[int]]) -> tuple[dict, dict]:
    """缓存下来的 javdb 搜索页 → (实体 → 演员 id 集合, 实体 → 站上同名的那几个 id)。

    同一张卡会出现在几张搜索页里，按 id 去重之后再判是不是同一个人。
    """
    cards: dict[str, dict] = {}
    for html in _cached_pages(cache_dir):
        for card in javdb.search_cards(html):
            cards.setdefault(card["id"], card)
    matched: dict[int, list[dict]] = collections.defaultdict(list)
    for card in cards.values():
        claimants = set().union(*(owners.get(match_key(name), set()) for name in card["names"]))
        for entity_id in claimants:
            matched[entity_id].append(card)
    found: dict[int, set[str]] = {}
    namesakes: dict[int, list[str]] = {}
    for entity_id, hits in matched.items():
        ids = sorted(card["id"] for card in hits)
        if javdb.one_person(hits):
            found[entity_id] = set(ids)
        else:
            namesakes[entity_id] = ids
    return found, namesakes


def from_csv(paths, provider_of: dict[str, str]) -> dict:
    """复核 CSV → （provider, 实体）→ id 集合。列名决定这一列属于哪个站。"""
    found: dict[tuple[str, int], set[str]] = collections.defaultdict(set)
    for path in paths:
        try:
            rows = read_rows(path)
        except OSError:
            continue
        for row in rows:
            raw = str(row.get("entity_id") or "").strip()
            if not raw.isdigit():
                continue
            for column, provider in provider_of.items():
                value = str(row.get(column) or "").strip()
                if value:
                    found[(provider, int(raw))].add(value)
    return found


def _owner_of(connection: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """账本里 (站点, id) → 现在挂在哪位实体名下。主键保证一个 id 只有一位。"""
    return {(str(provider), str(external_id)): int(entity_id)
            for entity_id, provider, external_id in connection.execute(
                "SELECT entity_id,provider,external_id FROM entity_external_ref"
                " WHERE external_kind=?", (EXTERNAL_KIND,))}


def fc2_only(connection: sqlite3.Connection) -> dict[int, int]:
    """没有 JAV 目录站 id、带番号的作品又全是 FC2 的女优实体 → 作品数。

    这些是 FC2 个人摄创作者，javdb 不按人收录她们：名字对上的演员页是同名的另一个人。
    """
    jav = {int(entity_id) for (entity_id,) in connection.execute(
        "SELECT DISTINCT entity_id FROM entity_external_ref WHERE external_kind=?"
        f" AND provider IN ({','.join('?' * len(JAV_DIRECTORIES))})",
        (EXTERNAL_KIND, *sorted(JAV_DIRECTORIES)))}
    codes: dict[int, list[str]] = collections.defaultdict(list)
    for entity_id, code in connection.execute(
            "SELECT ae.entity_id,a.code FROM asset_entity ae JOIN asset a ON a.id=ae.asset_id"
            " JOIN entity e ON e.id=ae.entity_id WHERE e.kind='performer'"
            " AND COALESCE(a.code,'')<>''"):
        codes[int(entity_id)].append(str(code).upper())
    return {entity_id: len(held) for entity_id, held in codes.items()
            if entity_id not in jav and all(code.startswith("FC2") for code in held)}


def plan(connection: sqlite3.Connection, args: argparse.Namespace) -> list[dict]:
    """每条候选引用一行。已登记、冲突、实体不对的也留行：下一趟要看得出这一位查过。"""
    owners = name_owners(connection)
    candidates: dict[tuple[str, int], tuple[set[str], str]] = {}
    namesakes: dict[int, list[str]] = {}
    if args.javdb_cache and Path(args.javdb_cache).is_dir():
        cache = Path(args.javdb_cache)
        for entity_id, ids in from_page_cache(cache, owners).items():
            candidates[(JAVDB, entity_id)] = (ids, f"javdb 页面缓存 {cache}")
        carded, namesakes = from_search_cards(cache, owners)
        for entity_id, ids in carded.items():
            have, origin = candidates.get((JAVDB, entity_id), (set(), ""))
            candidates[(JAVDB, entity_id)] = (
                have | ids, origin or f"javdb 搜索页缓存 {cache}")
    for path in args.csv:
        for (provider, entity_id), ids in from_csv([path], CSV_COLUMNS).items():
            have, origin = candidates.get((provider, entity_id), (set(), ""))
            candidates[(provider, entity_id)] = (
                have | ids, f"{origin}；{Path(path).name}".strip("；"))
    names = {int(entity_id): str(written) for entity_id, written in connection.execute(
        "SELECT id,canonical_name FROM entity WHERE kind='performer'")}
    owner = _owner_of(connection)
    creators = fc2_only(connection)
    # 这一批证据里谁认领了哪个 id。两位实体认领同一个是重名没解开，不挑一个。
    claimants: dict[tuple[str, str], set[int]] = collections.defaultdict(set)
    for (provider, entity_id), (ids, _origin) in candidates.items():
        for external_id in ids:
            claimants[(provider, external_id)].add(entity_id)
    rows: list[dict] = []
    for (provider, entity_id), (ids, origin) in sorted(
            candidates.items(), key=lambda item: (item[0][0], item[0][1])):
        if entity_id not in names:
            rows.append({"entity_id": entity_id, "canonical_name": "", "provider": provider,
                         "external_id": "", "origin": origin, "verdict": NOT_PERFORMER,
                         "evidence": "账本里这条实体不是女优"})
            continue
        for external_id in sorted(ids):
            row = {"entity_id": entity_id, "canonical_name": names[entity_id],
                   "provider": provider, "external_id": external_id, "origin": origin,
                   "verdict": OK, "evidence": f"{origin} 给的 id"}
            held = owner.get((provider, external_id))
            others = claimants[(provider, external_id)] - {entity_id}
            if held == entity_id:
                row.update(verdict=HAVE, evidence="账本里已有这一行")
            elif held is not None:
                row.update(verdict=TAKEN,
                           evidence=f"账本里这个 id 挂在实体 {held} 名下")
            elif others:
                row.update(verdict=CONFLICT,
                           evidence="同一个 id 对上 "
                                    f"{len(others) + 1} 位实体："
                                    f"{'、'.join(str(x) for x in sorted(others | {entity_id}))}")
            elif provider == JAVDB and entity_id in creators:
                row.update(verdict=FC2_ONLY,
                           evidence=f"没有 JAV 目录站 id，带番号的 {creators[entity_id]} 部作品全是 FC2")
            rows.append(row)
    registered = {entity_id for (provider, _external_id), entity_id in owner.items()
                  if provider == JAVDB}
    for entity_id, ids in sorted(namesakes.items()):
        if entity_id in names and entity_id not in registered and (JAVDB, entity_id) not in candidates:
            rows.append({"entity_id": entity_id, "canonical_name": names[entity_id],
                         "provider": JAVDB, "external_id": "",
                         "origin": f"javdb 搜索页缓存 {args.javdb_cache}", "verdict": NAMESAKE,
                         "evidence": f"对得上的卡片不止一个人：{'、'.join(ids)}"})
    return rows


def apply_rows(connection: sqlite3.Connection, rows: list[dict]) -> tuple[int, int]:
    """写入 `ok` 的那些，返回（打算写的条数，真的写进去的条数）。

    两个数不相等就是判定和账本对不上——`OR IGNORE` 留着只为让这一趟跑完，不是让它
    安静地少写几条：数量报出来，差值由人去看。
    """
    planned = written = 0
    for row in rows:
        if row["verdict"] != OK or not row["external_id"]:
            continue
        planned += 1
        connection.execute(
            "INSERT OR IGNORE INTO entity_external_ref"
            "(entity_id,provider,external_kind,external_id,metadata_json)"
            " VALUES(?,?,?,?,?)",
            (row["entity_id"], row["provider"], EXTERNAL_KIND, row["external_id"],
             '{"source": "backfill_performer_entry_ids"}'))
        written += connection.execute("SELECT changes()").fetchone()[0]
    connection.commit()
    return planned, written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把 javdb 演员 id 与 minnano-av 女优 id 落成 entity_external_ref")
    add_ledger_write_args(parser)
    parser.add_argument("--out", type=Path, required=True, help="复核 CSV 的落点")
    parser.add_argument("--javdb-cache", type=Path,
                        default=STATE_DIR / "directory-links" / "javdb",
                        help="javdb 的页面缓存目录")
    parser.add_argument("--csv", type=Path, action="append", default=[],
                        help="带 entity_id 与 actor_id／actress_id 的复核 CSV，可重复给")
    return parser


def run(args: argparse.Namespace) -> int:
    connection = open_for_write(args)
    try:
        rows = plan(connection, args)
        write_rows(args.out, FIELDS, rows, fill_missing=True)
        counts = collections.Counter(str(row["verdict"]) for row in rows)
        by_site = collections.Counter(
            str(row["provider"]) for row in rows if row["verdict"] == OK)
        print(f"外部入口 id 候选 {len(rows)} 行，复核 CSV：{args.out}")
        print("  判定分布：", dict(counts))
        print("  可补的引用：", dict(by_site))
        if args.apply:
            planned, written = apply_rows(connection, rows)
            integrity, violations = verify_after_write(connection)
            print(f"打算写 {planned} 条、实际写入 {written} 条 entity_external_ref；"
                  f"integrity_check={integrity}、外键违规 {violations} 条")
            return 1 if violations or integrity != "ok" or planned != written else 0
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
