"""在仅安装 wheel 的解释器中，从源码树外验证资源、迁移和 HTTP 关键入口。"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory(prefix="peach-wheel-") as directory:
        root = Path(directory).resolve()
        os.environ["PEACH_DATA_ROOT"] = str(root / "data")
        environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        environment.pop("PYTHONPATH", None)
        command = [sys.executable, "-m", "peach"]
        result = subprocess.run(command + ["init", "--no-input", "--host", "127.0.0.1"],
                                cwd=root, env=environment, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        config = root / "data" / "config.toml"
        original = config.read_bytes()
        repeat = subprocess.run(command + ["init", "--no-input"], cwd=root, env=environment,
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert repeat.returncode == 3 and config.read_bytes() == original
        from fastapi.testclient import TestClient
        import peach
        from peach.api import create_app
        from peach.config import MIGRATIONS_DIR, PeachSettings
        from peach.migrations import upgrade
        assert "site-packages" in Path(peach.__file__).parts, peach.__file__
        assert "_resources" in MIGRATIONS_DIR.parts, MIGRATIONS_DIR
        database = root / "ledger.db"
        assert upgrade(database, MIGRATIONS_DIR)
        settings = PeachSettings(db_path=database, configured=True, token="wheel-smoke")
        client = TestClient(create_app(settings))
        try:
            assert client.get("/healthz").status_code == 200
            ready = client.get("/healthz?ready=1")
            assert ready.status_code == 200, ready.text
            assert client.get("/api/items").status_code == 401
            headers = {"X-Token": "wheel-smoke"}
            assert client.get("/api/items", headers=headers).status_code == 200
            assert client.get("/", headers=headers).status_code == 200
            assert settings.page_path.is_file()
            for name in ("peach-app.js", "peach-app.css", "peach-pages.js", "peach-pages.css"):
                assert (settings.page_path.parent / "dist" / name).is_file()
            assert any(settings.vendor_path.iterdir())
            print(json.dumps({"module": peach.__file__, "ready": ready.json(),
                              "checks": ["migrations", "web", "vendor", "frontend", "auth", "items"]}))
        finally:
            client.close()
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        import httpx
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command + ["serve", "--host", "127.0.0.1", "--port", str(port),
                                       "--db", str(database), "--token", "wheel-smoke",
                                       "--no-mdns", "--no-ledger-sync"], cwd=root, env=environment,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=2) as http:
                    deadline = time.monotonic() + 30
                    while True:
                        try:
                            assert http.get("/healthz?ready=1").status_code == 200
                            break
                        except httpx.TransportError:
                            if process.poll() is not None or time.monotonic() > deadline:
                                raise RuntimeError("wheel 服务未就绪")
                            time.sleep(0.1)
                    for path in ("/", "/api/items", "/dist/peach-app.js"):
                        response = http.get(path, headers={"X-Token": "wheel-smoke"})
                        assert response.status_code == 200, (path, response.status_code)
                    print(json.dumps({"tcp": "passed", "init_repeat": "existing_config_preserved"}))
            finally:
                process.terminate()
                process.wait(timeout=15)


if __name__ == "__main__":
    main()
