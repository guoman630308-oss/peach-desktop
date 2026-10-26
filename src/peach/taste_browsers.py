"""本机浏览器读取配置，保存在私有历史库中，与已导入记录分开管理。"""
from __future__ import annotations

import hashlib
import socket
import sqlite3
from contextlib import closing
from pathlib import Path


def _key(path: Path) -> str:
    return hashlib.sha256(f"{socket.gethostname()}\0{str(path.resolve()).casefold()}".encode()).hexdigest()


def _paths(path: Path, *, browsing_only: bool = False, bookmarks=False) -> list[Path]:
    names = ("Bookmarks", "places.sqlite") if bookmarks else ("History", "HistoryEx", "places.sqlite", "History.db")
    if path.is_file():
        if bookmarks:
            return [path] if path.name in names else _paths(path.parent, bookmarks=True)
        return [] if path.name == "Bookmarks" else [path]
    if not path.is_dir():
        return []
    paths = sorted({item for name in names
                    for pattern in (name, f"*/{name}") for item in path.glob(pattern)
                    if item.is_file()})[:200]
    if not browsing_only:
        return paths
    found = []
    for item in paths:
        if item.name == "HistoryEx":
            try:
                _validate(item)
            except ValueError:
                continue
        found.append(item)
    return found


def history_engine(db: sqlite3.Connection) -> str:
    """根据访问记录的必需列识别格式，附加元数据由解析器处理。"""
    schemas = {
        "chrome": {"urls": {"id", "url", "title"}, "visits": {"url", "visit_time"}},
        "firefox": {"moz_places": {"id", "url", "title"},
                    "moz_historyvisits": {"place_id", "visit_date"}},
        "safari": {"history_items": {"id", "url"},
                   "history_visits": {"history_item", "visit_time", "title"}},
    }
    for engine, required in schemas.items():
        if all(columns <= {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
               for table, columns in required.items()):
            return engine
    raise ValueError("浏览器历史格式不受支持，请选择包含网页访问记录的历史文件")


def _validate(path: Path) -> str:
    from .taste_history import _readonly_uri
    if path.name == "Bookmarks":
        from .taste_bookmarks import read_bookmarks
        read_bookmarks(path)
        return "chrome"
    try:
        with closing(sqlite3.connect(_readonly_uri(path), uri=True)) as db:
            try:
                return history_engine(db)
            except ValueError as error:
                if path.name == "places.sqlite" and db.execute("SELECT 1 FROM sqlite_master WHERE name='moz_bookmarks'").fetchone():
                    return "firefox"
                if path.name == "HistoryEx":
                    raise ValueError("HistoryEx 未包含可识别的网页访问记录；115 下载记录不能用于浏览历史，请选择 History 文件") from error
                raise
    except sqlite3.DatabaseError as error:
        if "locked" in str(error).casefold():
            raise ValueError("历史文件正在使用，请退出该浏览器后添加") from error
        raise ValueError("浏览器历史文件未取得，请检查资料目录") from error


def _registered_browsers(store_path):
    """读取当前设备的配置，历史格式只在配置保存时迁移。"""
    if store_path.is_file():
        from .taste_history import _readonly_uri
        with closing(sqlite3.connect(_readonly_uri(store_path), uri=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='browser_sync'").fetchone():
                columns = {row[1] for row in db.execute("PRAGMA table_info(browser_sync)")}
                engine = "engine" if "engine" in columns else "'auto'"
                return list(db.execute(f"SELECT id,name,path,enabled,{engine} FROM browser_sync WHERE host=?", (socket.gethostname(),)))
    return []


def browser_sources(store_path: Path) -> list[dict]:
    from .taste_history import discover_history_sources
    entries = {}
    for source in discover_history_sources(include_bookmarks=True):
        root = source.root or source.path
        key = _key(root)
        entries[key] = {"id": key, "name": source.browser.title(), "path": str(root),
                        "enabled": True, "automatic": True, "engine": source.engine or "safari"}
    for key, name, path, enabled, engine in _registered_browsers(store_path):
        if enabled < 0:
            entries.pop(key, None)
            continue
        entries[key] = {"id": key, "name": name, "path": path, "enabled": bool(enabled),
                        "automatic": key in entries, "engine": engine}
    for entry in entries.values():
        paths = _paths(Path(entry["path"]), browsing_only=True) + _paths(Path(entry["path"]), bookmarks=True)
        entry["profiles"] = len({path.parent for path in paths})
        entry["available"] = bool(paths)
        entry["source_keys"] = browser_snapshot_keys(store_path, entry, paths)
    return sorted(entries.values(), key=lambda row: (row["name"].casefold(), row["id"]))


def configure_browser(store_path: Path, operation: str, body: dict) -> None:
    from .taste_history import _open_history_store
    entries = {row["id"]: row for row in browser_sources(store_path)}
    if operation == "add":
        name = str(body.get("name") or "").strip()
        raw_path = str(body.get("path") or "").strip()
        if not name or len(name) > 80 or not raw_path or len(raw_path) > 1024:
            raise ValueError("请填写浏览器名称和资料目录")
        path = Path(raw_path).expanduser().resolve()
        paths = sorted(set(_paths(path) + _paths(path, bookmarks=True)))
        if not paths:
            raise ValueError("目录中未找到浏览器历史或书签文件")
        # 同一资料目录可以同时包含网页历史与下载历史。
        browsing = sorted(set(_paths(path, browsing_only=True) + _paths(path, bookmarks=True)))
        engines = [_validate(history) for history in browsing or paths]
        entry = {"id": _key(path), "name": name, "path": str(path), "enabled": True,
                 "engine": engines[0] if len(set(engines)) == 1 else "auto"}
    elif operation in ("disable", "enable", "delete_browser"):
        entry = entries.get(str(body.get("id") or ""))
        if not entry:
            raise ValueError("浏览器来源不存在")
        entry["enabled"] = operation == "enable"
    else:
        raise ValueError("浏览器操作无效")
    with _open_history_store(store_path, None) as (db, _, _):
        db.execute("CREATE TABLE IF NOT EXISTS browser_sync("
                   "id TEXT PRIMARY KEY,host TEXT NOT NULL,name TEXT NOT NULL,path TEXT NOT NULL,enabled INTEGER NOT NULL,engine TEXT NOT NULL DEFAULT 'auto')")
        if "engine" not in {row[1] for row in db.execute("PRAGMA table_info(browser_sync)")}:
            db.execute("ALTER TABLE browser_sync ADD COLUMN engine TEXT NOT NULL DEFAULT 'auto'")
        db.execute("INSERT INTO browser_sync(id,host,name,path,enabled,engine) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                   "name=excluded.name,path=excluded.path,enabled=excluded.enabled,engine=excluded.engine",
                   (entry["id"], socket.gethostname(), entry["name"], entry["path"],
                    -1 if operation == "delete_browser" else int(entry["enabled"]), entry["engine"]))
        if operation == "delete_browser":
            keys = set(entry.get("source_keys", []))
            keys.update(row[0] for row in db.execute("SELECT source_key FROM browser_snapshot WHERE browser_id=?", (entry["id"],)))
            for key in keys:
                for table in ("history_visit", "history_bookmark", "browser_snapshot", "history_source"):
                    db.execute(f"DELETE FROM {table} WHERE source_key=?", (key,))
        db.commit()


def bind_browser_snapshot(db, source_key, source):
    """本机资料的历史和书签共享浏览器来源，文件导入保持独立。"""
    if source.root is not None:
        db.execute("INSERT OR REPLACE INTO browser_snapshot VALUES(?,?)", (source_key, _key(source.root)))


def browser_snapshot_allowed(db, source):
    """写快照时与来源删除串行，已取得的读取任务不恢复删除的数据。"""
    if source.root is None:
        return True
    db.execute("BEGIN IMMEDIATE")
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='browser_sync'").fetchone():
        row = db.execute("SELECT enabled FROM browser_sync WHERE id=?", (_key(source.root),)).fetchone()
        if row and row[0] < 0:
            db.rollback()
            return False
    return True


def browser_snapshot_keys(store_path, browser, paths):
    if not store_path.is_file():
        return []
    from .taste_history import _readonly_uri
    hashes = {hashlib.sha256(str(path.resolve()).encode()).hexdigest() for path in paths}
    with closing(sqlite3.connect(_readonly_uri(store_path), uri=True)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        bound = dict(db.execute("SELECT source_key,browser_id FROM browser_snapshot")) if "browser_snapshot" in tables else {}
        keys = {key for key, owner in bound.items() if owner == browser["id"]}
        if "history_source" in tables:
            keys.update(key for key, digest in db.execute("SELECT source_key,path_hash FROM history_source WHERE host=?", (socket.gethostname(),)) if key not in bound and digest in hashes)
    return sorted(keys)


def enabled_history_sources(store_path: Path):
    from .taste_history import HistorySource
    unique = {}
    for browser in browser_sources(store_path):
        if browser["enabled"]:
            for path in _paths(Path(browser["path"]), browsing_only=True):
                unique.setdefault(str(path.resolve()).casefold(), HistorySource(
                    browser["name"].casefold(), path.parent.name, path, root=Path(browser["path"]), engine=browser["engine"]))
    return list(unique.values())


def enabled_bookmark_sources(store_path: Path):
    from .taste_history import HistorySource
    unique = {}
    for browser in browser_sources(store_path):
        if browser["enabled"]:
            for path in _paths(Path(browser["path"]), bookmarks=True):
                unique.setdefault(str(path.resolve()).casefold(), HistorySource(
                    browser["name"].casefold(), path.parent.name, path, root=Path(browser["path"]),
                    engine="firefox" if path.name == "places.sqlite" else "chrome"))
    return list(unique.values())
