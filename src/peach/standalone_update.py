"""独立 Windows 包的持久更新事务：下载准备、用户重启、目录切换与回滚。"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import zipfile

import httpx
from filelock import FileLock, Timeout

from . import __version__, desktop_installer, distribution, release_updates, settings_file, update_packages
from .fsutil import atomic_write_text
from .windows_update import replace_with_retry
from . import update_tasks

ACTIVE = {"downloading", "verifying", "extracting", "preparing", "restarting", "installing"}
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024


def state_path() -> Path:
    return settings_file.load_config().directory("state") / "standalone-update.json"


def remember_installation_data_root(config) -> bool:
    """在线安装启动时，将已知的数据目录记入空的默认启动落点。"""
    if not distribution.standalone() or not config.present:
        return False
    data = read()
    target = data.get("target")
    if (data.get("state") != "installing" or data.get("version") != __version__
            or not isinstance(target, str) or not Path(target).is_absolute()
            or Path(target).resolve() != Path(sys.executable).resolve().parent):
        return False
    bootstrap = distribution.user_data_root(os.environ)
    if (bootstrap.resolve() == config.data_root.resolve()
            or any((bootstrap / name).exists() for name in (
                settings_file.SETTINGS_FILENAME, "state/data-root.txt", "database/ledger.db"))):
        return False
    settings_file.remember_data_root(settings_file.load_config(
        environ=dict(os.environ, PEACH_DATA_ROOT=str(bootstrap))), config)
    return True


def read() -> dict:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "idle", "progress": 0}


def write(data: dict, **changes) -> dict:
    data.update(changes, updated_at=time.time())
    atomic_write_text(state_path(), json.dumps(data, ensure_ascii=False))
    return data


def public() -> dict:
    data = read()
    if data.get("state") in ACTIVE and time.time() - data.get("updated_at", 0) > 180:
        data = dict(data, state="error", message="更新任务已中断，请重试。")
    return {key: value for key, value in data.items() if key in {
        "state", "progress", "message", "downloaded", "total", "version", "updated_at"}}


def extract(archive: Path, destination: Path, progress, *, tool_files: set[str] | None = None) -> Path:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if sum(item.file_size for item in entries) > MAX_EXPANDED or len(entries) > 20000:
            raise ValueError("更新包展开体积超限")
        seen = set()
        for index, item in enumerate(entries):
            name = item.filename
            parts = PurePosixPath(name).parts
            if (not parts or parts[0] != "Peach" or "\\" in name or ":" in name
                    or ".." in parts or name.startswith("/") or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("更新包含无效路径")
            if tool_files is not None and (item.is_dir() or "/".join(parts[1:]) not in tool_files):
                raise ValueError("工具包包含清单外的文件")
            target = destination.joinpath(*parts).resolve()
            key = str(target).casefold()
            if not target.is_relative_to(destination) or key in seen:
                raise ValueError("更新包路径重复或越界")
            seen.add(key)
            package.extract(item, destination)
            progress(index + 1, len(entries))
    root = destination / "Peach"
    if tool_files is None and (not (root / "Peach.exe").is_file() or not (root / "_internal" / "standalone.txt").is_file()):
        raise ValueError("更新包不是独立 Windows 版本")
    return root


def download(url: str, archive: Path, size: int, digest: str, progress) -> None:
    checksum = hashlib.sha256()
    received = 0
    with httpx.Client(follow_redirects=True, timeout=30) as client, client.stream("GET", url) as response:
        response.raise_for_status()
        with archive.open("wb") as output:
            for chunk in response.iter_bytes(256 * 1024):
                received += len(chunk)
                if received > size:
                    raise ValueError("下载体积与发布记录不符")
                output.write(chunk)
                checksum.update(chunk)
                progress(received)
    if received != size or checksum.hexdigest() != digest:
        raise ValueError("更新包校验失败，请重新下载")


def prepare_tools(stage: Path, target: Path, transaction: Path, release: dict, data: dict,
                  *, report=None, fetch=None) -> None:
    report, fetch = report or write, fetch or download
    manifest = update_packages.read_manifest(stage, release["latest_version"])
    missing = []
    for tool, bundle in manifest["tools"].items():
        if update_packages.matches(stage, bundle["files"]):
            continue
        if update_packages.matches(target, bundle["files"]):
            for name in bundle["files"]:
                destination = update_packages.checked_path(stage, name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(update_packages.checked_path(target, name), destination)
        else:
            missing.append((tool, bundle))
    total = data["total"] + sum(bundle["asset"]["size"] for _, bundle in missing)
    report(data, total=total)
    for index, (tool, bundle) in enumerate(missing):
        asset = bundle["asset"]
        archive = transaction / f"tools-{tool}.zip"
        received = data["downloaded"]
        report(data, state="downloading", message=f"正在下载 {tool} 工具包")
        fetch(release["asset_url"].rsplit("/", 1)[0] + "/" + asset["name"], archive,
                 asset["size"], asset["sha256"],
                 lambda count, start=received, step=index, size=asset["size"]: report(
                     data, downloaded=start + count, progress=75 + round((step + count / size) / len(missing) * 13)))
        report(data, state="verifying", message=f"正在校验 {tool} 工具包")
        extracted = extract(archive, transaction / f"tool-{tool}", lambda *_: None, tool_files=set(bundle["files"]))
        if not update_packages.matches(extracted, bundle["files"]):
            raise ValueError(f"{tool} 工具包文件校验失败")
        for name in bundle["files"]:
            destination = update_packages.checked_path(stage, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(update_packages.checked_path(extracted, name), destination)
    for tool, bundle in manifest["tools"].items():
        if not update_packages.matches(stage, bundle["files"]):
            raise ValueError(f"{tool} 工具复用校验失败")


def prepare(data: dict, lock: FileLock) -> None:
    try:
        release = release_updates.check()
        if release["state"] != "available":
            raise ValueError(release["message"])
        digest = release.get("asset_digest") or ""
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise ValueError("更新包缺少 SHA-256 校验信息")
        total = release.get("asset_size", 0)
        if not isinstance(total, int) or not 0 < total <= MAX_ARCHIVE:
            raise ValueError("更新包大小无效")
        target = Path(sys.executable).resolve().parent
        if state_path().resolve().is_relative_to(target):
            raise ValueError("数据目录位于程序目录内，不能自动替换")
        transaction = target.parent / f".{target.name}-update-{data['id']}"
        transaction.mkdir()
        write(data, transaction=str(transaction), target=str(target), version=release["latest_version"],
              total=total, downloaded=0, message="正在下载", state="downloading")
        archive = transaction / "package.zip"
        download(release["asset_url"], archive, total, digest[7:],
                 lambda count: write(data, downloaded=count, progress=round(count / total * 65, 1)))
        write(data, state="verifying", progress=66, message="正在校验")
        write(data, state="extracting", message="正在解压")
        stage = extract(archive, transaction / "stage", lambda done, count: write(data, progress=67 + round(done / count * 8)))
        from .buildinfo import read_build_info
        info = read_build_info(stage / "_internal")
        if not info or info.version != release["latest_version"]:
            raise ValueError("包内版本与发布版本不符")
        if release.get("package_kind") == "update":
            write(data, state="preparing", message="正在复用和校验已安装工具")
            prepare_tools(stage, target, transaction, release, data)
        write(data, state="preparing", progress=90, message="正在准备安装")
        desktop_installer.carry_uninstaller(target, stage)
        # 助手从自己的目录运行，正式目录与暂存目录都不会被它的 DLL 句柄占用。
        helper = transaction / "helper"
        shutil.copytree(target, helper, ignore=shutil.ignore_patterns("*.log"))
        write(data, stage=str(stage), helper=str(helper), state="ready", progress=100,
              message="更新已准备好，重启后安装。")
    except Exception as exc:
        write(data, state="error", message=str(exc), progress=0)
    finally:
        lock.release()


def start() -> dict:
    if not distribution.standalone() or sys.platform != "win32":
        raise ValueError("此安装方式不支持独立包更新")
    lock = FileLock(str(state_path()) + ".lock", thread_local=False)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return public()
    if public().get("state") in ACTIVE | {"ready"}:
        lock.release()
        return public()
    data = write({"id": uuid.uuid4().hex}, state="downloading", progress=0, message="正在连接 GitHub")
    threading.Thread(target=prepare, args=(data, lock), daemon=True, name="PeachPackageUpdate").start()
    return public()


def request_restart() -> dict:
    if not distribution.standalone() or sys.platform != "win32":
        raise ValueError("此安装方式不支持独立包更新")
    with FileLock(str(state_path()) + ".lock", timeout=0):
        data = read()
        if data.get("state") != "ready":
            raise ValueError("更新尚未准备完成")
        db = settings_file.load_config().directory("database") / "ledger.db"
        token = update_tasks.reserve(db)
        try:
            write(data, state="restarting", task_token=token, progress=0, message="正在保存任务进度，随后重启安装")
        except Exception:
            update_tasks.release(db, token)
            raise
    return public()


def poll(tray) -> None:
    if not distribution.standalone() or read().get("state") != "restarting":
        return
    if not tray._action_lock.acquire(blocking=False):
        return
    try:
        data = read()
        db = settings_file.load_config().directory("database") / "ledger.db"
        token = data.get("task_token") or update_tasks.reserve(db)
        if not data.get("task_token"):
            write(data, task_token=token)
        update_tasks.wait_quiescent(db)
        helper = Path(data["helper"]) / "Peach.exe"
        environment = os.environ.copy()
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        environment[settings_file.DATA_ROOT_ENV] = str(settings_file.load_config().data_root.resolve())
        process = subprocess.Popen([str(helper), "--apply-standalone-update", str(state_path()), str(os.getpid())],
                         cwd=str(helper.parent), env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
        update_tasks.transfer(db, token, process.pid)
        tray.exit(reason="standalone-update")
    except Exception as exc:
        if 'token' in locals():
            update_tasks.release(db, token)
        write(read(), state="error", message=f"未能重启：{exc}")
    finally:
        tray._action_lock.release()


def migrate_database(stage: Path, data: dict) -> Path | None:
    from .migrations import plan, sqlite_backup
    config = settings_file.load_config()
    database = config.directory("database") / "ledger.db"
    if not database.is_file():
        return None
    _, pending = plan(database, stage / "_internal" / "migrations")
    if not pending:
        return None
    backup = config.directory("state") / "update-backups" / data["id"] / "ledger.db"
    sqlite_backup(database, backup)
    write(data, database_backup=str(backup), state="installing", progress=10, message="正在更新本地数据库")
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1", PEACH_DATA_ROOT=str(config.data_root.resolve()))
    with (backup.parent / "migration.log").open("wb") as log:
        result = subprocess.run([str(stage / "Peach.exe"), "migrate", "upgrade", "--yes", "--db", str(database)],
                                cwd=str(stage), env=environment, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                timeout=180, check=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("数据库更新失败")
    return backup


def restore_database(data: dict) -> None:
    config = settings_file.load_config()
    database = config.directory("database") / "ledger.db"
    backup = config.directory("state") / "update-backups" / data["id"] / "ledger.db"
    if not backup.is_file():
        return
    with closing(sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(database)) as destination:
        source.backup(destination)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("数据库恢复检查失败")


def _transaction_paths(manifest: Path):
    """只接受本机状态文件与同卷暂存目录，安装目标须为完整独立包。"""
    if manifest.resolve() != state_path().resolve():
        raise ValueError('更新状态文件不匹配')
    data = read()
    target = Path(data["target"]).resolve()
    transaction = Path(data["transaction"]).resolve()
    stage = Path(data["stage"]).resolve()
    if (transaction.parent != target.parent or transaction.name != f".{target.name}-update-{data['id']}"
            or stage != transaction / "stage" / "Peach" or state_path().resolve().is_relative_to(target)
            or not (target / "_internal" / "standalone.txt").is_file()):
        raise ValueError('更新目录无效')
    return data, target, transaction, stage


def apply(manifest: Path, wait_pid: int) -> int:
    """复制出来的助手等待原托盘正常退出，再在同一卷上切换两个目录。"""
    from .windows_restart import process_alive, start_tray, find_tray_windows, post_stop
    from .tray import configured_service_specs
    from .settings_file import load_config
    from . import job_recovery
    try:
        data, target, transaction, stage = _transaction_paths(manifest)
    except ValueError:
        return 2
    backup = transaction / "backup"
    moved = False
    installed = False
    db = load_config().directory("database") / "ledger.db"
    token = data.get("task_token")
    previous = job_recovery.legacy_library_request(db)
    try:
        if token:
            update_tasks.transfer(db, token, os.getpid())
        else:
            token = update_tasks.reserve(db)
    except Exception as exc:
        write(data, state="error", progress=0, message=f"安装预检未通过，程序与数据库未更改：{exc}")
        return 1
    try:
        deadline = time.monotonic() + 90
        while process_alive(wait_pid):
            if time.monotonic() > deadline:
                raise TimeoutError("托盘尚未退出，安装已取消")
            time.sleep(.25)
        job_recovery.import_legacy_library(db, previous)
        migrate_database(stage, data)
        write(data, state="installing", progress=20, message="正在保留旧版本")
        replace_with_retry(target, backup)
        moved = True
        write(data, progress=50, message="正在安装新版本")
        replace_with_retry(stage, target)
        installed = True
        write(data, progress=80, message="正在启动新版本")
        update_tasks.release(db, token)
        start_tray(target / "Peach.exe")
        deadline = time.monotonic() + 90
        specs = configured_service_specs(load_config())
        spec = next((s for s in specs if s.name == "https"), specs[0])
        while time.monotonic() < deadline:
            try:
                health = httpx.get(spec.health_url, verify=spec.verify, trust_env=False, timeout=3)
                ready = httpx.get(spec.health_url + "?ready=1", verify=spec.verify, trust_env=False, timeout=3)
                if (health.status_code == ready.status_code == 200 and health.json().get("version") == data["version"]
                        and ready.json().get("ready") and find_tray_windows(target / "Peach.exe")):
                    if desktop_installer.installed(target):
                        try:
                            desktop_installer.record_version(data["version"])
                        except OSError:
                            pass
                    write(data, state="complete", progress=100, message=f"已更新至 {data['version']}")
                    return 0
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(1)
        raise TimeoutError("新版本未能就绪")
    except Exception as exc:
        if moved:
            from .error_reporting import event
            event('standalone-update-install', exc)
            try:
                if installed:
                    for window in find_tray_windows(target / "Peach.exe"):
                        post_stop(window.handle)
                    replace_with_retry(target, transaction / "failed")
                replace_with_retry(backup, target)
                restore_database(data)
                update_tasks.release(db, token)
                start_tray(target / "Peach.exe")
                message = f"更新失败，已恢复旧版本：{exc}"
            except Exception as rollback:
                event('standalone-update-rollback', rollback)
                message = f"更新失败，回滚未完成：{rollback}"
        else:
            from .error_reporting import event
            event('standalone-update-install', exc)
            message = f"更新未安装：{exc}"
            try:
                restore_database(data)
                if token:
                    update_tasks.release(db, token)
                if not process_alive(wait_pid):
                    start_tray(target / "Peach.exe")
            except Exception as rollback:
                event('standalone-update-rollback', rollback)
                message = f"更新失败，数据库恢复未完成：{rollback}"
        write(data, state="error", progress=0, message=message)
        return 1
    finally:
        if token:
            update_tasks.release(db, token)
