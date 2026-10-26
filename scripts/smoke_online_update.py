"""在临时馆藏中用已发布 Windows 包验证真实在线更新与回滚。"""
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

from smoke_desktop import (artifact_processes, complete_first_run, smoke_directory,
                           smoke_environment, stop_tray, wait_for, wait_health)


def download_release(opener, root: Path, version: str) -> Path:
    with opener.open(f"https://api.github.com/repos/peach-mitao/peach/releases/tags/v{version}", timeout=30) as response:
        release = json.load(response)
    asset = next(asset for asset in release["assets"] if asset["name"] == f"Peach-{version}-windows-x64.zip")
    archive = root / "old.zip"
    with opener.open(asset["browser_download_url"], timeout=60) as response, archive.open("wb") as output:
        while chunk := response.read(256 * 1024):
            output.write(chunk)
    with archive.open("rb") as source:
        assert "sha256:" + hashlib.file_digest(source, "sha256").hexdigest() == asset["digest"]
    with zipfile.ZipFile(archive) as package:
        package.extractall(root / "app")
    return root / "app/Peach/Peach.exe"


def configuration_request(opener, base: str, path: str, body=None):
    request = urllib.request.Request(base + "/api/configuration/" + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Origin": base, "Content-Type": "application/json"})
    with opener.open(request, timeout=20) as response:
        return json.load(response)


def exercise_update(opener, base: str, data: Path, log_path: Path, version: str, *, rollback: bool):
    original = (data / "config.toml").read_bytes()
    access = (data / "secrets/access.json").read_bytes()
    configuration_request(opener, base, "update", {})

    def prepared():
        state = configuration_request(opener, base, "update-status")
        assert state["state"] != "error", state
        return state if state["state"] == "ready" else None

    wait_for(prepared, "下载、SHA-256 校验与解压", timeout=300)
    manifest = data / "state/standalone-update.json"
    transaction = json.loads(manifest.read_text(encoding="utf-8"))
    if rollback:
        (Path(transaction["stage"]) / "Peach.exe").write_bytes(b"invalid-executable-smoke-fixture")
    configuration_request(opener, base, "update-restart", {})

    def installed():
        state = json.loads(manifest.read_text(encoding="utf-8"))
        if rollback:
            return state if state["state"] == "error" else None
        assert state["state"] != "error", state
        return state if state["state"] == "complete" else None

    state = wait_for(installed, "回滚到旧版" if rollback else "重启安装新版", timeout=240)
    health = wait_health(opener, base, None, log_path)
    assert health["configured"] and health["version"] == version, health
    if rollback:
        assert "已恢复旧版本" in state["message"], state
    assert (data / "config.toml").read_bytes() == original
    assert (data / "secrets/access.json").read_bytes() == access
    with closing(sqlite3.connect(data / "database/ledger.db")) as connection:
        assert connection.execute("SELECT value FROM online_update_fixture").fetchone() == ("retained",)
    with opener.open(base + "/healthz?ready=1", timeout=10) as response:
        assert json.load(response)["ready"]
    with opener.open(base + "/api/items", timeout=10) as response:
        assert response.status == 200
    helper = Path(transaction["helper"]) / "Peach.exe"
    wait_for(lambda: not artifact_processes(helper), "更新助手退出", timeout=20)
    print(json.dumps({"ok": True, "phase": "rollback" if rollback else "installed",
        "version": health["version"], "build_commit": health.get("build_commit"), "state": state["state"]}), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--to-version", required=True)
    parser.add_argument("--exercise-rollback", action="store_true")
    parser.add_argument("--apply", action="store_true", help="运行临时实例并安装更新；缺省只输出验收计划")
    parser.add_argument("--evidence-dir", type=Path, default=Path("smoke-evidence"))
    args = parser.parse_args()
    if not args.apply:
        print(json.dumps({"from_version": args.from_version, "to_version": args.to_version,
                          "rollback": args.exercise_rollback, "apply": False}))
        return 0
    assert os.name == "nt", "在线更新制品验收需要 Windows"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with smoke_directory(args.evidence_dir.resolve()) as root:
        executable = download_release(opener, root, args.from_version)
        data, media = root / "selected-data", root / "media"
        media.mkdir()
        environment = smoke_environment(data)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        password = "online-update-smoke-password"
        log_path = root / "runtime.log"
        with log_path.open("wb") as log:
            setup = subprocess.Popen([str(executable), "serve", "--setup", "--host", "127.0.0.1", "--port", str(port),
                "--no-mdns", "--no-ledger-sync"], cwd=root, env=environment, stdout=log, stderr=log)
            try:
                assert wait_health(opener, base, setup, log_path)["version"] == args.from_version
                complete_first_run(opener, base, media, port, password)
            finally:
                setup.terminate()
                setup.wait(timeout=15)
            database = data / "database/ledger.db"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE online_update_fixture(value TEXT)")
                connection.execute("INSERT INTO online_update_fixture VALUES ('retained')")
            launcher = subprocess.Popen([str(executable), "--silent"], cwd=root,
                env=environment, stdout=log, stderr=log)
            try:
                wait_health(opener, base, None, log_path)
                request = urllib.request.Request(base + "/login",
                    data=urllib.parse.urlencode({"token": password, "days": "0"}).encode(), headers={"Origin": base})
                with opener.open(request, timeout=10) as response:
                    assert response.status == 200

                release = configuration_request(opener, base, "updates")
                assert release["state"] == "available" and release["latest_version"] == args.to_version, release
                print(json.dumps({"phase": "checked", "release": release}), flush=True)
                for rollback in ([True, False] if args.exercise_rollback else [False]):
                    exercise_update(opener, base, data, log_path,
                        args.from_version if rollback else args.to_version, rollback=rollback)
                locator = Path(environment["LOCALAPPDATA"]) / "Peach/peach-data/state/data-root.txt"
                assert locator.read_text(encoding="utf-8") == str(data)
                stop_tray(executable, root, environment, log, launcher)
                environment.pop("PEACH_DATA_ROOT")
                launcher = subprocess.Popen([str(executable), "--silent"], cwd=root,
                    env=environment, stdout=log, stderr=log)
                health = wait_health(opener, base, None, log_path)
                assert health["configured"] and health["version"] == args.to_version, health
                print(json.dumps({"ok": True, "phase": "default-launch", "version": health["version"]}), flush=True)
            finally:
                stop_tray(executable, root, environment, log, launcher)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
