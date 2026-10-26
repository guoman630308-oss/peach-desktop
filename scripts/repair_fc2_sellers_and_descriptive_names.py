"""存量修正：FC2 卖家当成了厂牌、描述性称呼当成了女优（ADR-0054）。

两件事都是自动落库按旧判据写下的，新判据已在落库那一层拦住（`_fc2_seller_as_label`、
`metadata_alias_resolve.is_descriptive`），这里把已经写进账本的那些改到新判据下的样子：

- FC2 番号上厂牌不是 `FC2-PPV` 的作品，厂牌改成 `FC2-PPV`、关系改挂到 `FC2-PPV` 实体；
  改完不再有作品的卖家厂牌实体删掉。卖家名留在候选证据的 `label` 上，账本没有这一列。
- 规范名是描述性称呼、且全部关系都由自动落库写下的女优实体：连同 `演员:` 标签删掉，
  那几条自动批准的出演者决定一并撤掉，让这几部片按新判据重新排进人工队列；头像文件
  挪进 `avatars-superseded/`，不删。

默认只列计划；`--apply` 必须同时给 `--backup`。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach.config import GENERATED_DIR  # noqa: E402
from peach.field_owners import write_owned_fields  # noqa: E402
from peach.metadata_alias_resolve import is_descriptive  # noqa: E402
from peach.metadata_auto_apply import _fc2_seller_as_label  # noqa: E402
from peach.scripting import (  # noqa: E402
    add_ledger_write_args, counts_of, open_for_write, verify_after_write)
from peach.sources import fc2  # noqa: E402

OWNER = "script:repair_fc2_sellers_and_descriptive_names"
EXTRA_COUNTS = {
    "asset_tag": "SELECT count(*) FROM asset_tag",
    "review_decision": "SELECT count(*) FROM review_decision",
}


def seller_assets(connection) -> list[dict]:
    """FC2 番号上厂牌记成卖家的作品。"""
    rows = connection.execute(
        "SELECT id,code,studio FROM asset WHERE trim(COALESCE(studio,''))<>''").fetchall()
    return [dict(row) for row in rows
            if _fc2_seller_as_label("studio", str(row["code"] or ""),
                                    {"value": row["studio"]})["value"] != row["studio"]]


def descriptive_performers(connection) -> list[dict]:
    """规范名是描述性称呼、关系全是自动落库写下的女优实体。"""
    found = []
    for row in connection.execute(
            "SELECT id,canonical_name FROM entity WHERE kind='performer'").fetchall():
        if not is_descriptive(row["canonical_name"]):
            continue
        links = connection.execute(
            "SELECT asset_id,source FROM asset_entity WHERE entity_id=?", (row["id"],)).fetchall()
        if any(not str(link["source"]).startswith("javinizer:") for link in links):
            continue
        found.append({"id": int(row["id"]), "name": str(row["canonical_name"]),
                      "assets": sorted({int(link["asset_id"]) for link in links})})
    return found


def repair_sellers(connection, assets: list[dict], stamp: str) -> dict[str, int]:
    done = {"厂牌改写": 0, "关系改挂": 0, "卖家实体": 0}
    if not assets:
        return done
    ids = [row["id"] for row in assets]
    write = write_owned_fields(connection, ids, {"studio": fc2.STUDIO}, OWNER)
    done["厂牌改写"] = write.assets if "studio" in write.written else 0
    marks = ",".join("?" * len(ids))
    moved = [int(row[0]) for row in connection.execute(
        f"SELECT id FROM asset WHERE id IN ({marks}) AND studio=?", (*ids, fc2.STUDIO))]
    platform = connection.execute(
        "SELECT id FROM entity WHERE kind='studio' AND canonical_name=?", (fc2.STUDIO,)).fetchone()
    if platform is None:
        connection.execute(
            "INSERT INTO entity(kind,canonical_name,normalized_name,created_at,updated_at)"
            " VALUES('studio',?,?,?,?)", (fc2.STUDIO, fc2.STUDIO.casefold(), stamp, stamp))
        platform_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    else:
        platform_id = int(platform[0])
    sellers: set[int] = set()
    for asset_id in moved:
        sellers.update(int(row[0]) for row in connection.execute(
            "SELECT entity_id FROM asset_entity WHERE asset_id=? AND role='studio' AND entity_id<>?",
            (asset_id, platform_id)))
        connection.execute(
            "DELETE FROM asset_entity WHERE asset_id=? AND role='studio' AND entity_id<>?",
            (asset_id, platform_id))
        connection.execute(
            "INSERT OR IGNORE INTO asset_entity(asset_id,entity_id,role,source,confidence,"
            "metadata_json,first_seen_at,last_seen_at) VALUES(?,?,'studio',?,1.0,'{}',?,?)",
            (asset_id, platform_id, OWNER, stamp, stamp))
        done["关系改挂"] += 1
    for entity_id in sorted(sellers):
        connection.execute(
            "DELETE FROM entity WHERE id=? AND kind='studio' "
            "AND NOT EXISTS(SELECT 1 FROM asset_entity WHERE entity_id=?)", (entity_id, entity_id))
        done["卖家实体"] += connection.execute("SELECT changes()").fetchone()[0]
    return done


def _auto_decision_names(note: str) -> str:
    try:
        parsed = json.loads(note or "{}")
    except ValueError:
        return ""
    return str(parsed.get("value") or "") if parsed.get("auto_applied") else ""


def avatar_files(performers: list[dict], avatar_roots: tuple[Path, ...]) -> list[Path]:
    """这些女优名下的头像与缩略图（`performer-<id>.*`）。"""
    return [path for person in performers for root in avatar_roots
            for path in sorted(root.glob(f"performer-{person['id']}.*"))]


def move_aside(paths: list[Path], superseded: Path) -> int:
    """挪进 `avatars-superseded/`：实体 id 会被新实体复用，留在原处就会顶到别人头上。"""
    for path in paths:
        superseded.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(superseded / path.name))
    return len(paths)


def drop_performers(connection, performers: list[dict]) -> dict[str, int]:
    done = {"女优实体": 0, "演员标签": 0, "撤回决定": 0}
    for person in performers:
        for asset_id in person["assets"]:
            connection.execute(
                "DELETE FROM asset_tag WHERE asset_id=? AND tag=? AND source LIKE 'javinizer:%'",
                (asset_id, f"演员:{person['name']}"))
            done["演员标签"] += connection.execute("SELECT changes()").fetchone()[0]
            key = f"asset:{asset_id}:performers"
            decision = connection.execute(
                "SELECT note FROM review_decision WHERE category='metadata_fields' AND item_key=?"
                " AND status='approved'", (key,)).fetchone()
            if decision is not None and person["name"] in _auto_decision_names(decision["note"]):
                connection.execute(
                    "DELETE FROM review_decision WHERE category='metadata_fields' AND item_key=?",
                    (key,))
                done["撤回决定"] += 1
        connection.execute("DELETE FROM entity WHERE id=? AND kind='performer'", (person["id"],))
        done["女优实体"] += connection.execute("SELECT changes()").fetchone()[0]
    return done


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_ledger_write_args(parser)
    parser.add_argument("--generated-root", type=Path, default=GENERATED_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    avatar_roots = (args.generated_root / "avatars", args.generated_root / "avatar-thumbs")
    superseded = args.generated_root / "avatars-superseded"
    connection = open_for_write(args)
    try:
        assets = seller_assets(connection)
        performers = descriptive_performers(connection)
        for row in assets:
            print(f" - 厂牌 {row['code']:<20} {row['studio']} → {fc2.STUDIO}")
        for person in performers:
            print(f" - 女优 {person['id']:>6} {person['name'][:30]}（{len(person['assets'])} 部）")
        files = avatar_files(performers, avatar_roots)
        if not args.apply:
            print(f"dry-run：厂牌 {len(assets)} 部，女优 {len(performers)} 位，头像文件 "
                  f"{len(files)} 个；加 --apply --backup <路径> 才真的写。")
            return 0
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        before = counts_of(connection, EXTRA_COUNTS)
        # 外键默认关着；删实体时要它把别名、外部编号、链接一并级联删掉。PRAGMA 只能在事务外设。
        connection.commit()
        connection.execute("PRAGMA foreign_keys=ON")
        done = repair_sellers(connection, assets, stamp)
        done.update(drop_performers(connection, performers))
        connection.commit()
        done["头像文件"] = move_aside(files, superseded)
        after = counts_of(connection, EXTRA_COUNTS)
        integrity, violations = verify_after_write(connection)
        print("写入：" + "，".join(f"{key} {value}" for key, value in done.items()))
        for key in sorted(before):
            print(f"  {key}: {before[key]} -> {after[key]}")
        print(f"integrity_check={integrity} foreign_key_check={violations}")
        return 0 if integrity == "ok" and violations == 0 else 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
