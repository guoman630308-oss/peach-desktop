"""导入实体种子后继：扫描结算后把私有种子包补进账本（ADR-0075、ADR-0111）。

种子包位于 Peach 数据目录的 `state/seed/entities.json`（`seed_pack.DEFAULT_PACK`），导入只给本机已有实体填空、换掉种子
自己写过的旧行（`seed_pack.land`）。这条后继由扫描与采集在结算时声明（ADR-0040 第一条），
派的条件是两个里有一个：这一轮登记了新的女优、厂牌或事务所，或这个版本的包还没有成功导入过。
同一版本因此会跑多次；`land` 幂等，多跑一次只是没有东西可补。

与本机不一致的事实、对上本机两位实体的包项写进 `candidate_root` 下的 `seed-landing.csv`，
活动页只给条数。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import seed_pack, seed_corrections, seed_validation
from .followups import Followup, FollowupType, register

TASK_KEY = "seed-import"
TASK_LABEL = "导入初始资料"
REVIEW_FILE = "seed-landing.csv"
FIELDS = ("kind", "entity", "field", "ours", "source", "theirs", "detail", "batch", "withdrawal_id", "evidence")
#: 活动页摘要里的计数键与它们的中文名，按这个顺序拼结论。
SUMMARY_LABELS = (("aliases", "别名"), ("refs", "编号"), ("links", "链接"), ("profiles", "资料"),
                  ("memberships", "归属"), ("makers", "片商"), ("refreshed", "换新"), ("withdrawn", "撤销"))


def followup_key(version: str, content_hash: str = "") -> str:
    return f"{TASK_KEY}:{version}" + (f":{content_hash}" if content_hash else "")


def pack_version(path: Path | None = None) -> str | None:
    """私有种子包版本；缺包时返回 None，包损坏时报告校验错误。"""
    path = Path(path) if path is not None else seed_pack.DEFAULT_PACK
    if not path.is_file():
        return None
    return str(seed_pack.load(path)["version"])


def landed(connection: sqlite3.Connection, key: str) -> bool:
    """这个版本有没有成功导入过：看任务记录里同 key 的后继有没有走到成功。"""
    return connection.execute(
        "SELECT 1 FROM task_run WHERE followup_key=? AND status='succeeded' LIMIT 1", (key,)).fetchone() is not None


def _new_entities(connection: sqlite3.Connection, since_entity_id: int) -> bool:
    marks = ",".join("?" * len(seed_pack.KINDS))
    return connection.execute(
        f"SELECT 1 FROM entity WHERE id>? AND kind IN ({marks}) LIMIT 1",
        (int(since_entity_id), *seed_pack.KINDS)).fetchone() is not None


def plan(connection: sqlite3.Connection, *, since_entity_id: int, pack_path: Path | None = None) -> list[Followup]:
    """这一轮该不该派：包的版本没成功导过，或这一轮登记了包里那三类的新实体。"""
    path = Path(pack_path) if pack_path is not None else seed_pack.DEFAULT_PACK
    if not path.is_file():
        return []
    pack = seed_pack.load(path)
    seed_corrections.check_history(connection, pack)
    version = pack["version"]
    key = followup_key(version, seed_validation.digest(pack))
    if landed(connection, key) and not _new_entities(connection, since_entity_id):
        return []
    return [Followup(key=key, task_key=TASK_KEY, label=f"{TASK_LABEL}：{version}")]


def _rows(report: dict) -> list[dict]:
    rows = [{"kind": item["kind"], "entity": item["entity"], "field": item["field"], "ours": item["ours"],
             "source": item["source"], "theirs": item["theirs"], "batch": report["batch"],
             "detail": "包里说的与本机不一致；本机这条不是种子写的，种子不动它"}
            for item in report["conflicts"]]
    rows += [{"kind": item["kind"], "entity": "、".join(item["entities"]), "field": "identity", "ours": "",
              "source": "", "theirs": item["name"], "batch": report["batch"],
              "detail": "包里一条对上本机两位，可能是重复身份；合并不可逆，由人决定"}
             for item in report["duplicates"]]
    rows += [{"kind": item["kind"], "entity": item["entity"], "field": item["field"], "source": item["source"],
              "detail": item["detail"] + "；" + item["reason"], "withdrawal_id": item["id"],
              "evidence": item["evidence"], "batch": report["batch"]}
             for item in report.get("withdrawal_conflicts", [])]
    return rows


def record_review(root: Path, report: dict) -> Path:
    """复核产物整份换成这一轮的：种子导入每次都是全量，上一轮留下的项这一轮要么还在、要么已解决。"""
    from .review_csv import write_rows
    path = Path(root) / REVIEW_FILE
    write_rows(path, FIELDS, _rows(report), atomic=True, fill_missing=True)
    return path


def outcome(report: dict) -> str:
    parts = [f"{label} {report[key]}" for key, label in SUMMARY_LABELS if report.get(key)]
    if report["conflicts"]:
        parts.append(f"不一致 {len(report['conflicts'])}")
    if report["duplicates"]:
        parts.append(f"重复身份 {len(report['duplicates'])}")
    if report.get("withdrawal_conflicts"):
        parts.append(f"撤销待复核 {len(report['withdrawal_conflicts'])}")
    return "、".join(parts) if parts else f"没有可补的（对上 {report['matched']} 位）"


def run(contract, key: str, handle) -> dict:
    """将私有种子包补进账本；测试使用临时种子包。"""
    pack = seed_pack.load(seed_pack.DEFAULT_PACK)
    if key != followup_key(pack["version"], seed_validation.digest(pack)):
        raise ValueError("种子内容与排队任务的摘要不一致，请重新派发")
    if handle is not None:
        handle.progress(label=f"{TASK_LABEL}：{pack['version']}", throttle=0)
    with contract.database.write_transaction() as connection:
        report = seed_pack.land(connection, pack)
    if seed_pack.written(report):
        contract.cache_bust()
    record_review(contract.candidate_root, report)
    return {"outcome": outcome(report), "version": report["version"], "matched": report["matched"],
            "unmatched": report["unmatched"], **{key: report.get(key, 0) for key, _label in SUMMARY_LABELS},
            "conflicts": len(report["conflicts"]), "duplicates": len(report["duplicates"]),
            "withdrawal_conflicts": len(report.get("withdrawal_conflicts", []))}


#: 写账本：别名、编号、链接、资料、归属与片商都进账本，走写账本那一条串行通道（ADR-0040 第三条）。
TYPE = register(FollowupType(task_key=TASK_KEY, label=TASK_LABEL, writes_ledger=True, run=run))
