"""只读就绪检查；不创建账本，不修复或迁移数据。"""
import sqlite3
import re
from contextlib import closing

from .config import MIGRATIONS_DIR, PeachSettings
from .migrations import applied, discover


def inspect_schema(installed, directory) -> dict:
    """比较已登记迁移与当前程序，保留可公开的版本编号。"""
    versions = sorted(value for value in installed if isinstance(value, str) and re.fullmatch(r"\d{4}", value))
    result = {"version": versions[-1] if versions else None, "pending": [],
              "mismatched": [], "unknown_versions": 0, "schema": "unavailable"}
    try:
        expected = discover(directory)
    except (OSError, ValueError):
        return result
    known = {migration.version for migration in expected}
    result["unknown_versions"] = len(set(installed) - known)
    result["pending"] = [migration.version for migration in expected if migration.version not in installed]
    result["mismatched"] = [migration.version for migration in expected
                             if migration.version in installed and installed[migration.version] != migration.checksum]
    if not installed:
        result["schema"] = "empty"
    elif result["mismatched"] or result["unknown_versions"] or not expected:
        result["schema"] = "mismatch"
    else:
        result["schema"] = "pending" if result["pending"] else "current"
    return result


def inspect_database(path, directory=None) -> dict:
    """读取账本与迁移校验结果；错误只输出状态，不携带路径或 SQL。"""
    result = {"database": "missing", "readable": False, "schema": "unavailable",
              "version": None, "pending": [], "mismatched": [], "unknown_versions": 0}
    directory = MIGRATIONS_DIR if directory is None else directory
    if not path.is_file():
        return result
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=1)) as connection:
            connection.execute("PRAGMA query_only=ON")
            installed = applied(connection)
            result["database"] = "available" if installed else "empty"
            result.update(inspect_schema(installed, directory))
            try:
                connection.execute("SELECT id FROM asset LIMIT 1").fetchone()
                result["readable"] = True
            except sqlite3.Error:
                pass
    except (sqlite3.Error, OSError):
        result.update(database="unavailable", readable=False, schema="unavailable")
    return result


def database_status(path) -> str:
    return inspect_database(path)["database"]


def readiness(settings: PeachSettings, *, background_ready: bool | None = None) -> dict:
    checks = {"configured": bool(settings.configured),
              "web": settings.page_path.is_file(), "database": False, "schema": False}
    inspected = inspect_database(settings.db_path)
    checks.update(database=inspected["readable"], schema=inspected["schema"] == "current")
    if background_ready is not None:
        checks["background"] = background_ready
    return {"ready": all(checks.values()), "checks": checks}
