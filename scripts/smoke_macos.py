#!/usr/bin/env python3
"""在源码树外、无开发工具的 PATH 下验证 macOS 应用。"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from smoke_desktop import assert_requires_login, complete_first_run, smoke_environment, wait_health


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    args = parser.parse_args()
    app = args.app.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="peach-macos-smoke-") as directory:
        root = Path(directory).resolve()
        shutil.copytree(app, root / "Peach.app", symlinks=True)
        executable = root / "Peach.app" / "Contents" / "MacOS" / "Peach"
        data, media = root / "data", root / "media"
        media.mkdir()
        environment = smoke_environment(data)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        command = [str(executable), "serve", "--host", "127.0.0.1", "--port", str(port),
                   "--no-ledger-sync", "--no-mdns"]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        base = f"http://127.0.0.1:{port}"
        process = None
        log_path = root / "runtime.log"
        with log_path.open("wb") as log:
            try:
                process = subprocess.Popen([*command, "--setup"], cwd=root, env=environment,
                                           stdout=log, stderr=log)
                wait_health(opener, base, process, log_path)
                password = "macos-smoke-password"
                complete_first_run(opener, base, media, port, password, media_root="B:/")
                process.terminate()
                process.wait(timeout=20)
                process = subprocess.Popen(command, cwd=root, env=environment, stdout=log, stderr=log)
                health = wait_health(opener, base, process, log_path)
                assert health["ledger_sync"] == "disabled", health
                assert_requires_login(opener, base)
                request = urllib.request.Request(base + "/login",
                    data=urllib.parse.urlencode({"token": password, "days": "0"}).encode(),
                    headers={"Origin": base})
                with opener.open(request, timeout=10) as response:
                    assert response.status == 200
                for path in ("/", "/dist/peach-app.js", "/dist/peach-app.css", "/api/items",
                             "/healthz?ready=1"):
                    with opener.open(base + path, timeout=30) as response:
                        assert response.status == 200, path
                with opener.open(base + "/api/configuration", timeout=15) as response:
                    configuration = json.load(response)
                second = root / "second-media"
                second.mkdir()
                request = urllib.request.Request(base + "/api/configuration",
                    data=json.dumps({"media_sources": configuration["media_sources"] +
                                    [{"location": "pikpak", "root": "A:/", "path": str(second)}],
                                    "port": port, "revision": configuration["revision"]}).encode(),
                    headers={"Origin": base, "Content-Type": "application/json"})
                with opener.open(request, timeout=15) as response:
                    assert json.load(response)["saved"]
                assert (data / "config.previous.toml").is_file()
                print(json.dumps({"ok": True, "version": health.get("version"),
                    "checks": ["relocated-app", "no-development-tools", "first-run", "migrations",
                               "standalone-ledger", "login", "frontend", "configuration-write"]}))
            except BaseException as error:
                if isinstance(error, urllib.error.HTTPError):
                    print(error.read().decode("utf-8", "replace"))
                print(log_path.read_text(errors="replace"))
                raise
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
