r"""存量修正：撤掉被当成番号的写法，以及拿它刮回来的一切。

建库前的导入把 115 盘上的目录名整批写进了 `asset.code`。论坛合集包 `WX17` 下是 18 位网红的
266 个视频，`WX17` 被规范成 `WX-017` 问了 javbus，取回的是另一部片 `WXSD-017`；09-02 那次
整批复核把它的片名和标签落到了这 266 条上。新判据已在刮削入口拦住
（`scrape_codes._is_explicit_code`），这里把已经写进账本的改回去：

- `review_item` 以 `<规范键>:` 开头的 javinizer 归属，连同同源同名的 `asset_tag` 行删掉；
- 这个键下批准过的字段候选写进过的列（`METADATA_FIELD_COLUMNS`）以本脚本的归属清空，
  用户手改过的不动；清空的列不受保护，别的来源给得出一致的值时照常自动落库；
- `code` 以 `user:manual` 清空，扫描器此后不会再按文件名把它写回来；
- 这个键下批准过的复核决定改成 `rejected`，原批准记录留在 note 里。

两种入口：

- `--code`：账本里存着的原始写法。本身是番号形态的、或这批资产里有厂牌／发行日／出演者
  证据的，一律拒绝。
- `--asset-id`：由 `scan:filename` 写下、而 `release_code_from_filename` 对这个文件名已给出
  别的结果的番号（`dao01(1).mp4` → `DAO-001`）。规范写法像番号，按写法判不出来，只能按
  资产逐条点名；资产上有发行证据的同样拒绝。
- `--source-mismatch <番号>:<来源>`：番号是对的，是来源去前缀查时交回了另一部作品
  （`348NTR-007` 查到 DMM `1ntr00007` 涼川絢音）。只撤这个来源的归属、它批准写进的列和
  它的批准决定，`code` 和别的来源给的归属不动；来源交回的编号仍认得出这个番号时拒绝。
- `--release-owners`：按 `--source-mismatch` 留下的驳回决定找回它清空过的列，把仍为空、
  仍签着 `user:manual` 的改签成本脚本。09-25 那次撤回用 `user:manual` 清空字段，
  「不要这个来源」就成了「不要任何来源」：mgstage 与 avbase 给得一致的片名、厂牌、
  发行日期停在人工队列（6 条资产 19 行）。只改签归属，不写取值。

所有目标先全部过一遍计划，有一个被拒就整批不写。一次运行只备份一次、只开一个事务。
默认只列计划；`--apply` 必须同时给 `--backup`。

    revert_misread_code.py --code WX17 RAIKUN325
    revert_misread_code.py --asset-id 23975 34792 --apply --backup <备份路径>
    revert_misread_code.py --source-mismatch 348NTR-007:r18dev 451HHH-022:r18dev
    revert_misread_code.py --release-owners --apply --backup <备份路径>
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach.catalog_rules import (  # noqa: E402
    RELEASE_EVIDENCE_KINDS, is_jav_code, normalise_code_key, release_code_from_filename)
from peach.field_owners import (  # noqa: E402
    is_protected, owner_of, script_owner, write_owned_fields)
from peach.metadata import identifies_code  # noqa: E402
from peach.metadata_auto_apply import METADATA_FIELD_COLUMNS  # noqa: E402
from peach.scripting import (  # noqa: E402
    add_ledger_write_args, counts_of, open_for_write, verify_after_write)

#: 清空 `code` 用用户归属：扫描器按文件名会把目录名再写回来，只有受保护的归属拦得住。
OWNER = "user:manual"
#: 清空取值字段用脚本归属：要挡的只是那个交错作品的来源，它的决定已经驳回；别的来源
#: 给得出一致的值时，这一格应当照常自动落库。
CLEARED = script_owner("revert_misread_code")
_MISMATCH_REASON = "%去前缀查%交回的是另一部作品"
EXTRA_COUNTS = {
    "asset_tag": "SELECT count(*) FROM asset_tag",
    "有 code": "SELECT count(*) FROM asset WHERE trim(COALESCE(code,''))<>''",
    "approved 决定": "SELECT count(*) FROM review_decision WHERE status='approved'",
}
_COLUMNS = ",".join(sorted(set(METADATA_FIELD_COLUMNS.values())))


class NotADirectoryLabel(ValueError):
    """这个 code 可能是真番号，脚本不替人判。"""


def _release_evidence(connection, ids: list[int]) -> int:
    marks = ",".join("?" * len(ids))
    kinds = ",".join("?" * len(RELEASE_EVIDENCE_KINDS))
    return connection.execute(
        f"SELECT count(*) FROM asset a WHERE a.id IN ({marks}) AND ("
        "trim(COALESCE(a.studio,''))<>'' OR trim(COALESCE(a.release_date,''))<>'' OR EXISTS("
        "SELECT 1 FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
        f"WHERE ae.asset_id=a.id AND e.kind IN ({kinds}) AND ae.source NOT LIKE 'javinizer:%'))",
        (*ids, *sorted(RELEASE_EVIDENCE_KINDS))).fetchone()[0]


def _derived(connection, code: str, assets: list[dict], reason: str) -> dict:
    """这个键刮回来、批准过的一切：归属行、复核决定、要清的列。"""
    key = normalise_code_key(code)
    ids = [row["id"] for row in assets]
    marks = ",".join("?" * len(ids))
    # 复核决定按规范键记，不按资产记：别的资产也挂着这个番号时，驳回会连它的一起驳掉。
    others = connection.execute(
        f"SELECT count(*) FROM asset WHERE code IN (?,?) AND id NOT IN ({marks})",
        (code, key, *ids)).fetchone()[0]
    if others:
        raise NotADirectoryLabel(f"另有 {others} 条资产的 code 也是 {code} 或 {key}，复核决定分不开")
    links = [dict(row) for row in connection.execute(
        f"SELECT ae.asset_id,ae.entity_id,ae.role,ae.source,e.canonical_name FROM asset_entity ae "
        f"JOIN entity e ON e.id=ae.entity_id WHERE ae.asset_id IN ({marks}) "
        "AND ae.source LIKE 'javinizer:%' "
        "AND json_extract(ae.metadata_json,'$.review_item') LIKE ?", (*ids, f"{key}:%"))]
    decisions = [dict(row) for row in connection.execute(
        "SELECT category,item_key,status,note FROM review_decision "
        "WHERE category='metadata_fields' AND item_key LIKE ? AND status='approved'",
        (f"{key}:%",))]
    columns = sorted({METADATA_FIELD_COLUMNS[field] for row in decisions
                      if (field := row["item_key"].split(":", 1)[1]) in METADATA_FIELD_COLUMNS})
    fields = {column: [row["id"] for row in assets
                       if str(row[column] or "").strip()
                       and not is_protected(owner_of(row["field_owners"], column))]
              for column in columns}
    return {"code": code, "key": key, "assets": ids, "links": links,
            "decisions": decisions, "fields": fields, "reason": reason}


def plan(connection, code: str) -> dict:
    """一个目录名写法要改的资产、归属行、字段与复核决定；不写库。"""
    if is_jav_code(code):
        raise NotADirectoryLabel(f"{code} 的原始写法本身就是番号形态，不按目录名处理")
    assets = [dict(row) for row in connection.execute(
        f"SELECT id,field_owners,{_COLUMNS} FROM asset WHERE code=? ORDER BY id", (code,))]
    if not assets:
        raise NotADirectoryLabel(f"账本里没有 code={code} 的资产")
    evidence = _release_evidence(connection, [row["id"] for row in assets])
    if evidence:
        raise NotADirectoryLabel(f"{code} 下有 {evidence} 条资产带本机发行证据，不按目录名处理")
    key = normalise_code_key(code)
    return _derived(connection, code, assets, f"{code} 是目录名，不是番号 {key}")


def plan_asset(connection, asset_id: int) -> dict:
    """一条按文件名误识别的番号；只收 `scan:filename` 写下、文件名已解析不出它的。"""
    row = connection.execute(
        f"SELECT id,code,path,field_owners,{_COLUMNS} FROM asset WHERE id=?", (asset_id,)).fetchone()
    if row is None or not str(row["code"] or "").strip():
        raise NotADirectoryLabel(f"资产 {asset_id} 不存在或没有 code")
    code, name = row["code"], PureWindowsPath(row["path"]).name
    owner = owner_of(row["field_owners"], "code")
    if owner != "scan:filename":
        raise NotADirectoryLabel(f"资产 {asset_id} 的 code={code} 归属是 {owner or '无主'}，"
                                 "不是文件名扫描写下的")
    parsed = release_code_from_filename(name)
    if parsed and normalise_code_key(parsed) == normalise_code_key(code):
        raise NotADirectoryLabel(f"资产 {asset_id} 的文件名 {name} 仍解析出 {code}")
    if _release_evidence(connection, [asset_id]):
        raise NotADirectoryLabel(f"资产 {asset_id} 带本机发行证据，不按误识别处理")
    return _derived(connection, code, [dict(row)], f"{code} 是从文件名 {name} 误识别的，不是番号")


def _source_owns(owner: str, source: str) -> bool:
    """这一列现在的值还是不是这个来源给的：无主的是批准写入留下的，别的来源或用户写过就不是。"""
    return not is_protected(owner) and (not owner or source in owner)


def plan_source(connection, target: str) -> dict:
    """番号是对的、来源交回了另一部作品：只撤这个来源的一切；不写库。"""
    code, _, source = target.partition(":")
    if not source:
        raise NotADirectoryLabel(f"{target} 要写成 <番号>:<来源>，如 348NTR-007:r18dev")
    assets = [dict(row) for row in connection.execute(
        f"SELECT id,field_owners,{_COLUMNS} FROM asset WHERE code=? ORDER BY id", (code,))]
    if not assets:
        raise NotADirectoryLabel(f"账本里没有 code={code} 的资产")
    ids = [row["id"] for row in assets]
    marks = ",".join("?" * len(ids))
    rows = [dict(row) for row in connection.execute(
        f"SELECT ae.asset_id,ae.entity_id,ae.role,ae.source,ae.metadata_json,e.canonical_name "
        f"FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
        f"WHERE ae.asset_id IN ({marks}) AND ae.source LIKE ?", (*ids, f"javinizer:{source}:%"))]
    payloads = [{"id": meta.get("provider_id"), "content_id": meta.get("content_id"),
                 "source_url": meta.get("source_url")}
                for meta in (json.loads(row.pop("metadata_json") or "{}") for row in rows)]
    payloads = [payload for payload in payloads if any(payload.values())]
    if not payloads:
        raise NotADirectoryLabel(f"{code} 上没有带来源编号的 {source} 归属，判不了是不是另一部")
    if any(identifies_code(code, payload) for payload in payloads):
        raise NotADirectoryLabel(f"{source} 交回的编号认得出 {code}，不按另一部作品处理")
    decisions = [dict(row) for row in connection.execute(
        "SELECT category,item_key,status,note FROM review_decision "
        "WHERE category='metadata_fields' AND item_key LIKE ? AND status='approved' "
        "AND json_extract(note,'$.source')=?", (f"{normalise_code_key(code)}:%", source))]
    columns = sorted({METADATA_FIELD_COLUMNS[field] for row in decisions
                      if (field := row["item_key"].split(":", 1)[1]) in METADATA_FIELD_COLUMNS})
    fields = {column: [row["id"] for row in assets if str(row[column] or "").strip()
                       and _source_owns(owner_of(row["field_owners"], column), source)]
              for column in columns}
    return {"code": code, "key": normalise_code_key(code), "assets": ids, "links": rows,
            "decisions": decisions, "fields": fields, "keep_code": True,
            "reason": f"{source} 去前缀查 {code} 交回的是另一部作品"}


def plan_release(connection) -> dict:
    """`--source-mismatch` 清空过、仍空着且签着 `user:manual` 的列；不写库。

    目标按驳回决定找，不按归属形态找：同样空着、签着 `user:manual` 的格子也可能是用户
    自己清掉的，那种不能动。
    """
    columns_by_key: dict[str, set[str]] = {}
    for (item_key,) in connection.execute(
            "SELECT item_key FROM review_decision WHERE category='metadata_fields' "
            "AND status='rejected' "
            # 人手写的驳回理由是纯文本 note，`json_extract` 碰上就整条查询报错。
            "AND CASE WHEN json_valid(note) THEN json_extract(note,'$.reason') END LIKE ?",
            (_MISMATCH_REASON,)):
        key, _, field = item_key.partition(":")
        if field in METADATA_FIELD_COLUMNS:
            columns_by_key.setdefault(key, set()).add(METADATA_FIELD_COLUMNS[field])
    release: list[tuple[int, str]] = []
    for key, columns in sorted(columns_by_key.items()):
        for row in connection.execute(
                f"SELECT id,code,field_owners,{_COLUMNS} FROM asset "
                "WHERE upper(code) LIKE '%'||upper(?)||'%' ORDER BY id", (key,)):
            if normalise_code_key(str(row["code"] or "")) != key:
                continue
            release += [(row["id"], column) for column in sorted(columns)
                        if not str(row[column] or "").strip()
                        and owner_of(row["field_owners"], column) == OWNER]
    return {"code": "--release-owners", "key": f"{len(columns_by_key)} 个番号",
            "assets": sorted({asset_id for asset_id, _column in release}), "links": [],
            "decisions": [], "fields": {}, "keep_code": True, "release": release,
            "reason": "撤回时以用户归属清空的字段改签成脚本归属"}


def apply(connection, work: dict, stamp: str) -> dict[str, int]:
    done = {"归属行": 0, "标签行": 0, "清空字段": 0, "清空番号": 0, "驳回决定": 0, "改签归属": 0}
    # 只动归属、不动取值：`write_owned_fields` 不让脚本归属覆盖用户归属，这正是它该守的
    # 规矩，而这里要撤的恰恰是本脚本自己签错的那一笔，取值仍为空、`mutation_revision` 不变。
    for asset_id, column in work.get("release", ()):
        connection.execute(
            f"UPDATE asset SET field_owners=json_set(field_owners,'$.\"{column}\"',?) "
            f"WHERE id=? AND trim(COALESCE({column},''))='' "
            f"AND json_extract(field_owners,'$.\"{column}\"')=?", (CLEARED, asset_id, OWNER))
        done["改签归属"] += connection.execute("SELECT changes()").fetchone()[0]
    for link in work["links"]:
        connection.execute(
            "DELETE FROM asset_entity WHERE asset_id=? AND entity_id=? AND role=? AND source=?",
            (link["asset_id"], link["entity_id"], link["role"], link["source"]))
        done["归属行"] += connection.execute("SELECT changes()").fetchone()[0]
        if link["role"] == "tag":
            connection.execute("DELETE FROM asset_tag WHERE asset_id=? AND tag=? AND source=?",
                               (link["asset_id"], link["canonical_name"], link["source"]))
            done["标签行"] += connection.execute("SELECT changes()").fetchone()[0]
    for column, ids in work["fields"].items():
        if ids:
            done["清空字段"] += write_owned_fields(connection, ids, {column: None}, CLEARED).assets
    if not work.get("keep_code"):
        done["清空番号"] = write_owned_fields(connection, work["assets"], {"code": None}, OWNER).assets
    for decision in work["decisions"]:
        note = json.dumps({"reverted_approval": decision["note"], "reason": work["reason"]},
                          ensure_ascii=False)
        connection.execute(
            "UPDATE review_decision SET status='rejected',note=?,updated_at=? "
            "WHERE category=? AND item_key=? AND status='approved'",
            (note, stamp, decision["category"], decision["item_key"]))
        done["驳回决定"] += connection.execute("SELECT changes()").fetchone()[0]
    return done


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="撤掉被当成番号的写法及其刮削结果；默认只读")
    add_ledger_write_args(parser)
    parser.add_argument("--code", nargs="+", default=[], help="账本里存着的原始写法，如 WX17")
    parser.add_argument("--asset-id", nargs="+", type=int, default=[],
                        help="按文件名误识别番号的资产 id")
    parser.add_argument("--source-mismatch", nargs="+", default=[],
                        help="<番号>:<来源>，来源交回了另一部作品，如 348NTR-007:r18dev")
    parser.add_argument("--release-owners", action="store_true",
                        help="把 --source-mismatch 以用户归属清空、仍空着的字段改签成脚本归属")
    return parser


def _plan_all(connection, args) -> tuple[list[dict], list[str]]:
    works, refused = [], []
    targets = ([(plan, code) for code in args.code] + [(plan_asset, i) for i in args.asset_id]
               + [(plan_source, target) for target in args.source_mismatch])
    for planner, target in targets:
        try:
            works.append(planner(connection, target))
        except NotADirectoryLabel as error:
            refused.append(str(error))
    if args.release_owners:
        works.append(plan_release(connection))
    return works, refused


def _write(connection, works: list[dict]) -> int:
    before = counts_of(connection, EXTRA_COUNTS)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    total: dict[str, int] = {}
    connection.execute("BEGIN IMMEDIATE")
    try:
        for work in works:
            for key, value in apply(connection, work, stamp).items():
                total[key] = total.get(key, 0) + value
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    after = counts_of(connection, EXTRA_COUNTS)
    integrity, violations = verify_after_write(connection)
    print("写入：" + "，".join(f"{key} {value}" for key, value in total.items()))
    for key in before:
        print(f"  {key}: {before[key]} -> {after[key]}")
    print(f"integrity_check={integrity} foreign_key_check={violations}")
    return 0 if integrity == "ok" and violations == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not (args.code or args.asset_id or args.source_mismatch or args.release_owners):
        parser.error("至少给一个 --code、--asset-id、--source-mismatch 或 --release-owners")
    connection = open_for_write(args)
    try:
        works, refused = _plan_all(connection, args)
        for line in refused:
            print(f"拒绝：{line}", file=sys.stderr)
        if refused:
            return 2
        for work in works:
            if "release" in work:
                print(f"改签归属（{work['key']}）：资产 {len(work['assets'])} 条，字段 "
                      f"{len(work['release'])} 格 {work['release']}")
                continue
            print(f"{work['code']}（被当成 {work['key']}）：资产 {len(work['assets'])} 条，"
                  f"javinizer 归属 {len(work['links'])} 行，批准决定 "
                  f"{[row['item_key'] for row in work['decisions']]}，"
                  f"清空字段 { {column: len(ids) for column, ids in work['fields'].items()} }")
        if not args.apply:
            print("dry-run：未写 ledger；加 --apply --backup <路径> 才真的写。")
            return 0
        return _write(connection, works)
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
