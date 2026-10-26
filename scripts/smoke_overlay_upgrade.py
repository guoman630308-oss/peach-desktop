"""用已发布 Windows 制品验证 ZIP 与安装包原位覆盖升级。"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import urllib.parse
import urllib.request
import zipfile

from smoke_desktop import (artifact_processes, assert_requires_login, complete_first_run,
                           smoke_directory, smoke_environment, stop_tray, wait_for, wait_health)


APP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{02D9748A-78F6-4866-AC75-918177194DAB}_is1"


def installation() -> dict | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_KEY) as key:
            return {name: winreg.QueryValueEx(key, name)[0]
                    for name in ("DisplayVersion", "Inno Setup: App Path")}
    except FileNotFoundError:
        return None


def menu_path() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Peach.lnk"


def download(opener, root: Path, version: str, kind: str) -> Path:
    request = urllib.request.Request(
        f"https://api.github.com/repos/peach-mitao/peach/releases/tags/v{version}")
    if token := os.environ.get("GH_TOKEN"):
        request.add_header("Authorization", f"Bearer {token}")
    with opener.open(request, timeout=30) as response:
        release = json.load(response)
    suffix = ".zip" if kind == "zip" else "-setup.exe"
    name = f"Peach-{version}-windows-x64{suffix}"
    asset = next(asset for asset in release["assets"] if asset["name"] == name)
    target = root / name
    with opener.open(asset["browser_download_url"], timeout=60) as response, target.open("wb") as output:
        while chunk := response.read(256 * 1024):
            output.write(chunk)
    with target.open("rb") as source:
        digest = "sha256:" + hashlib.file_digest(source, "sha256").hexdigest()
    assert digest == asset["digest"], f"制品 SHA-256 不符：{name}"
    print(json.dumps({"phase": "download", "asset": name, "digest": digest}), flush=True)
    return target


def install(package: Path, destination: Path, environment: dict, log: Path, *, reuse: bool = False):
    command = [str(package), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log}"]
    if not reuse:
        command.append(f"/DIR={destination}")
    subprocess.run(command, env=environment, timeout=300, check=True)


def verify(opener, base: str, data: Path, media: Path, original: dict, password: str,
           version: str, commit: str, log: Path):
    health = wait_health(opener, base, None, log)
    assert health["configured"] and health["version"] == version, health
    if commit:
        assert health["build_commit"] == commit, health
    for relative, content in original.items():
        assert (data / relative).read_bytes() == content, f"文件未保留：{relative}"
    with closing(sqlite3.connect(data / "database/ledger.db")) as connection:
        assert connection.execute("SELECT value FROM overlay_upgrade_fixture").fetchall() == [("retained",)]
    assert (media / "retained.txt").read_text(encoding="utf-8") == "保留媒体目录"
    assert_requires_login(opener, base)
    login = urllib.request.Request(base + "/login",
        data=urllib.parse.urlencode({"token": password, "days": "0"}).encode(), headers={"Origin": base})
    with opener.open(login, timeout=10) as response:
        assert response.status == 200
    with opener.open(base + "/api/items", timeout=10) as response:
        assert response.status == 200
    with opener.open(base + "/healthz?ready=1", timeout=10) as response:
        assert json.load(response)["ready"]
    return health


def uninstall_and_verify(application: Path, data: Path, media: Path, original: dict, environment: dict):
    """卸载覆盖后的程序，核对程序登记清除且用户数据仍可读取。"""
    subprocess.run([str(application / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
        env=environment, timeout=120, check=True)
    wait_for(lambda: not application.exists(), "卸载程序目录", timeout=120)
    assert installation() is None and not menu_path().exists()
    for relative, content in original.items():
        assert (data / relative).read_bytes() == content
    with closing(sqlite3.connect(data / "database/ledger.db")) as connection:
        assert connection.execute("SELECT value FROM overlay_upgrade_fixture").fetchone() == ("retained",)
    assert (media / "retained.txt").is_file()
    print(json.dumps({"ok": True, "phase": "uninstall-keeps-data"}), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--to-version", required=True)
    parser.add_argument("--to-commit", required=True)
    parser.add_argument("--kind", choices=("zip", "installer"), required=True)
    parser.add_argument("--data-location", choices=("default", "explicit"), default="default")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--evidence-dir", type=Path, default=Path("smoke-evidence"))
    args = parser.parse_args()
    if not args.apply:
        print(json.dumps({"from": args.from_version, "to": args.to_version, "kind": args.kind, "apply": False}))
        return 0
    assert os.name == "nt", "覆盖升级验收需要 Windows"
    if args.kind == "installer":
        assert os.environ.get("GITHUB_ACTIONS") == "true", "安装包验收只允许在干净的 Actions runner 上运行"
        assert installation() is None and not menu_path().exists(), "已有 Peach 安装登记或快捷方式，停止验收"
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(cookies))
    with smoke_directory(args.evidence_dir.resolve()) as root:
        old = download(opener, root, args.from_version, args.kind)
        new = download(opener, root, args.to_version, args.kind)
        application = root / "中文 空格程序目录" / "Peach"
        executable = application / "Peach.exe"
        data = (root / "local-app-data/Peach/peach-data" if args.data_location == "default"
                else root / "selected-data")
        media = root / "media"
        media.mkdir()
        (media / "retained.txt").write_text("保留媒体目录", encoding="utf-8")
        environment = smoke_environment(data)
        environment["LOCALAPPDATA"] = str(root / "local-app-data")
        installer_environment = dict(environment)
        if args.data_location == "default":
            environment.pop("PEACH_DATA_ROOT")
        if args.kind == "zip":
            with zipfile.ZipFile(old) as archive:
                archive.extractall(application.parent)
        else:
            install(old, application, installer_environment, root / "install-old.log")
            assert installation()["DisplayVersion"] == args.from_version
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        base, password = f"http://127.0.0.1:{port}", "overlay-smoke-password"
        log_path = root / "runtime.log"
        with log_path.open("wb") as log:
            setup = subprocess.Popen([str(executable), "serve", "--setup", "--host", "127.0.0.1",
                "--port", str(port), "--no-mdns", "--no-ledger-sync"], cwd=root, env=environment,
                stdout=log, stderr=log)
            try:
                assert wait_health(opener, base, setup, log_path)["version"] == args.from_version
                complete_first_run(opener, base, media, port, password)
            finally:
                setup.terminate()
                setup.wait(timeout=15)
            with closing(sqlite3.connect(data / "database/ledger.db")) as connection, connection:
                connection.execute("CREATE TABLE overlay_upgrade_fixture(value TEXT)")
                connection.execute("INSERT INTO overlay_upgrade_fixture VALUES ('retained')")
            original = {relative: (data / relative).read_bytes()
                        for relative in ("config.toml", "secrets/access.json")}
            launcher = subprocess.Popen([str(executable), "--silent"], cwd=root, env=environment,
                stdout=log, stderr=log)
            try:
                verify(opener, base, data, media, original, password, args.from_version, "", log_path)
                if args.kind == "installer":
                    # 让安装器停止正在运行的旧版，并通过登记记住的目录原位安装。
                    install(new, application, installer_environment, root / "install-new.log", reuse=True)
                    launcher.wait(timeout=15)
                    wait_for(lambda: not artifact_processes(executable), "安装器停止旧版全部进程", timeout=20)
                    registered = installation()
                    assert registered["DisplayVersion"] == args.to_version, registered
                    assert Path(registered["Inno Setup: App Path"]).resolve() == application, registered
                    assert menu_path().is_file()
                else:
                    stop_tray(executable, root, environment, log, launcher)
                    # 模拟用户将完整 ZIP 解压到原程序目录，保留旧目录中的额外文件。
                    with zipfile.ZipFile(new) as archive:
                        archive.extractall(application.parent)
                cookies.clear()
                launcher = subprocess.Popen([str(executable), "--silent"], cwd=root, env=environment,
                    stdout=log, stderr=log)
                health = verify(opener, base, data, media, original, password,
                    args.to_version, args.to_commit, log_path)
                print(json.dumps({"ok": True, "phase": "overlay", "kind": args.kind,
                    "data_location": args.data_location,
                    "version": health["version"], "build_commit": health["build_commit"],
                    "checks": ["config", "password", "ledger", "media", "data-root", "login", "ready", "items"]}), flush=True)
            finally:
                stop_tray(executable, root, environment, log, launcher)
            if args.kind == "installer":
                uninstall_and_verify(application, data, media, original, installer_environment)
    print(json.dumps({"ok": True, "phase": "complete", "kind": args.kind,
                      "data_location": args.data_location}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
