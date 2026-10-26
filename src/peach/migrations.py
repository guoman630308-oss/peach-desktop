from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migration(
  version TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path
    sql: str
    checksum: str


def discover(directory: Path) -> list[Migration]:
    # `Path.glob` 对不存在的目录返回空序列，于是「一个 migration 都没有」和「根本没找到
    # migration 目录」会长得一模一样：`upgrade` 报 0 个迁移、`status` 报 0 pending，
    # 而账本是个 0 字节的空文件。空账本被当成迁移到位的账本是数据完整性问题，所以这里
    # 直接拒绝。目录缺失的真实原因通常是 `pip install .`（非 editable）——wheel 只装
    # `src/` 下的包，仓库根的 `migrations/` 不在里面。
    if not directory.is_dir():
        raise FileNotFoundError(f"migration 目录不存在：{directory}")
    migrations: list[Migration] = []
    for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        version, name = path.stem.split("_", 1)
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        migrations.append(Migration(version, name, path, sql, checksum))
    versions = [m.version for m in migrations]
    if len(versions) != len(set(versions)):
        raise ValueError("migration 版本重复")
    return migrations


def applied(connection: sqlite3.Connection) -> dict[str, str]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='schema_migration'"
    ).fetchone()
    if not exists:
        return {}
    return {row[0]: row[1] for row in connection.execute(
        "SELECT version,checksum FROM schema_migration ORDER BY version"
    )}


def plan(db_path: Path, directory: Path) -> tuple[list[Migration], list[Migration]]:
    all_migrations = discover(directory)
    # status 对既有数据库使用 SQLite 只读 URI，保证不会创建 journal/WAL 或触发隐式写入。
    connection = (sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
                  if db_path.exists() else sqlite3.connect(db_path))
    try:
        done = applied(connection)
    finally:
        connection.close()
    for migration in all_migrations:
        if migration.version in done and done[migration.version] != migration.checksum:
            raise RuntimeError(f"已应用 migration {migration.version} 的 checksum 已变化")
    return all_migrations, [m for m in all_migrations if m.version not in done]


def sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


#: 迁移文件用这一行申明「我要重建一张被别的表引用的父表」。
#:
#: `PRAGMA foreign_keys` 在事务里是空操作，而 `upgrade` 把每个迁移都包进
#: `BEGIN IMMEDIATE`，所以迁移文件自己关不掉外键。这不是形式问题：外键开着时
#: `DROP TABLE <父表>` 会先做一次隐式 `DELETE FROM`，子表的 CASCADE 真的会执行。
#: SQLite 官方的改表十二步因此第一步就是关外键——`entity` 的 `kind` CHECK 要加一种
#: 实体，除了重建没有别的写法，而重建会连 `entity_alias`、`entity_link`、
#: `asset_entity` 一起删空。0024 把这件事记下来并推迟到「另一次改动」，就是这里。
#:
#: 关外键只对申明了的那个迁移生效，退出时必检 `PRAGMA foreign_key_check`：
#: 关掉的是执行期的强制，不是这次改动的正确性要求。
FOREIGN_KEYS_OFF_MARKER = "-- peach:foreign_keys=off"


def needs_foreign_keys_off(sql: str) -> bool:
    """这个迁移申明了要在外键关闭下运行吗。

    只认整行，且必须出现在文件开头的注释块里：写在中间等于没写，因为 pragma 要在
    整个文件开跑之前就决定，而它在事务里改不动。
    """
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped == FOREIGN_KEYS_OFF_MARKER:
            return True
        if stripped and not stripped.startswith("--"):
            return False
    return False


def upgrade(db_path: Path, directory: Path, backup_path: Path | None = None) -> list[Migration]:
    _, pending = plan(db_path, directory)
    if not pending:
        return []
    if backup_path is not None:
        sqlite_backup(db_path, backup_path)

    connection = sqlite3.connect(db_path)
    try:
        for migration in pending:
            stamp = datetime.now(timezone.utc).isoformat()
            reconcile = _legacy_reconcile_sql(connection) if migration.version == "0000" else ""
            keys_off = needs_foreign_keys_off(migration.sql)
            # pragma 只能在事务外改，所以逐个迁移前设一次，而不是循环外设一次。
            connection.execute("PRAGMA foreign_keys=" + ("OFF" if keys_off else "ON"))
            script = (
                "BEGIN IMMEDIATE;\n"
                + MIGRATION_TABLE_SQL
                + migration.sql
                + reconcile
                + "\nINSERT INTO schema_migration(version,name,checksum,applied_at) VALUES("
                + ",".join(_quote(x) for x in (migration.version, migration.name, migration.checksum, stamp))
                + ");\nCOMMIT;"
            )
            try:
                connection.executescript(script)
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
            finally:
                connection.execute("PRAGMA foreign_keys=ON")
            if keys_off:
                # 外键关着跑完，正确性就得自己交代。留下孤儿行的迁移在这里失败，
                # 而不是等某个运维脚本半年后撞上 `foreign_key_check`。
                broken = connection.execute("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise RuntimeError(
                        f"migration {migration.version} 关闭外键后留下 {len(broken)} 条"
                        f"违约行：{broken[:5]}")
        connection.execute("PRAGMA optimize")
    finally:
        connection.close()
    return pending


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _legacy_reconcile_sql(connection: sqlite3.Connection) -> str:
    """把 Claude 阶段即席列纳入 0000 正式迁移；新库由 0000 SQL 一次创建。"""
    exists = connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='asset'"
    ).fetchone()
    if not exists:
        return ""
    have = {row[1] for row in connection.execute("PRAGMA table_info(asset)")}
    expected = {
        "studio": "TEXT",
        "feedback": "TEXT",
        "disposal": "TEXT",
        "leave_ratio": "REAL",
        "play_seconds": "REAL",
        "feedback_at": "REAL",
        "seek_count": "INTEGER",
        "max_reached": "REAL",
    }
    return "".join(
        f"\nALTER TABLE asset ADD COLUMN {name} {declaration};"
        for name, declaration in expected.items() if name not in have
    )
