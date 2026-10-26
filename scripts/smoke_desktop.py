"""仅用标准库在临时目录验证桌面制品，不读取真实馆藏。"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import http.cookiejar
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import urllib.parse
import uuid


# 合成 64×64 testsrc2 的两帧 AV1；不依赖发行工具包含 AV1 编码器。
_AV1_SAMPLE = (
    'REtJRgAAIABBVjAxQABAAAIAAAABAAAAAgAAAAAAAAAQAgAAAAAAAAAAAAASAAoKAAAAAq//m18gCDL/AxQAMoAY'
    'YQeKAgrbccEajCcFwrWZWZAcjk7HsQkwz6bl6C/RIMrhL7TGShgVqQJwYehp8Dk3NGsPnZpd9H/kMuUNCln4V/8Q'
    'sNsk8IXFEYPBxm3hH5FrNUGl0JHoXb5So71sC0w8QV8tnCFJTJAa+GuYX8XXKJuJpyg/HBFtHqiEEkLALQ/XUqV1'
    'LrdrAMGJ49bvAgoNjl5FDPy+4wDlFJYlV4y1RiocKER6pIiZK2g+N/lGJorkKg0vsmoI3olvguuTNs0+xIxZfToS'
    '7Y0UK7UCr7w5NzJEkZ0kpLbVot5PtEax3tOtvGE6FBU1kZfmKMQcB8OYl18JooM7zn1mYiNVVmYSOVeICjSlAXFj'
    'M4M8NDeILhqcheLaAJ1q53QozK7pAYwIKBS4AF8DjvvAVnxT9VLa2a+5RGqL2XT1aJ2Pfyh6kvsGhib/5pYSn4Xk'
    'OrP/XNtfE0Pdli4ram0vyYdaMWWmkqgxDMMLEqnWXdWn/+2RVPAdGZV//gWLaIS/BVQ92TcmFE0jZfAu6lFZa9kr'
    'ejynNQpdnX6BtyCsXsCXFuFjWviueEYWX9kN3KxKcmYIqae/8rioTK/koM1WIkuN7wIr0/+YE4v9eqfwkRqGKG7u'
    'GF3ydM6SqhvPoE2YvyUjw7lkOwzOzma5AOOux53IQlzAEigiskKcks4nv4AOAQAAAQAAAAAAAAASADKJAjIB4EAA'
    'AANoADDEExRddQDdOU0PJApEvWVmn/OPmFU6GzRhHf19xENKaaOLsjU3y6xIX+RghuEbLRK9wN2yvwikcZaOrZTr'
    'eEt5qhzR3F4YxcfTJakEEe4U6a9FIaWXxEAwrn4RU+3r7JnNsIifqcHcrsC4HVxAe8KcHtW+fmENmZgnhfwLdXp+'
    'LhgOZVki5u57z8TbVswnUJYINlRS2IMU9Tua7xL8EFwM+rxDyvaP0X3Rht73L1z0NvyYzeUaigPsLRrLvEwWp+BE'
    'UijJPuANY7UCckwZAo2GKlk3pY+wW5Zgw6aLcZpBzsDk8oZjBW13QxHQHremUttJSFHH4bQZ+D8cmQFv8BA='
)


def assert_av1_decoder(ffmpeg: Path, root: Path) -> None:
    """在 CPU 上验证随包 AV1 解码与缩图，不依赖显卡。"""
    source, image = root / 'av1.ivf', root / 'av1-frame.jpg'
    source.write_bytes(base64.b64decode(_AV1_SAMPLE))
    result = subprocess.run([str(ffmpeg), '-v', 'error', '-c:v', 'libdav1d', '-i', str(source),
                             '-vf', 'scale=480:-1', '-frames:v', '1', str(image)],
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')[-4000:]
    assert image.is_file() and image.stat().st_size > 0, 'AV1 抽帧未取得'


def assert_public_resources(application: Path) -> None:
    """发行资源不包含本机实体导出。"""
    assert not (application / "_internal/resources/seed").exists(), "发行包包含私有实体导出"


def assert_bundled_media_tools(application: Path) -> None:
    """在没有系统媒体工具的机器上验证随包转码、探测和抽帧。"""
    tools = application / "tools" / "ffmpeg"
    manifest = json.loads((tools / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["license"] == "GPL-3.0-or-later"
    for name, expected in manifest["files"].items():
        path = tools / name
        assert path.resolve().is_relative_to(tools.resolve()), name
        with path.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == expected, name
    untrunc_root = application / "tools/untrunc"
    repair_manifest = json.loads((untrunc_root / "manifest.json").read_text(encoding="utf-8"))
    assert repair_manifest["license"] == "GPL-2.0-or-later"
    for name, expected in repair_manifest["files"].items():
        path = untrunc_root / name
        assert path.resolve().is_relative_to(untrunc_root.resolve()), name
        with path.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == expected, name
    assert_public_resources(application)
    ffmpeg, ffprobe = tools / "bin/ffmpeg.exe", tools / "bin/ffprobe.exe"
    with tempfile.TemporaryDirectory(prefix="peach-media-smoke-") as directory:
        root = Path(directory).resolve()
        video, image = root / "sample.mp4", root / "frame.jpg"
        for command in (
            [str(ffmpeg), "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=64x64:r=5",
             "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
            [str(ffmpeg), "-v", "error", "-i", str(video), "-frames:v", "1", str(image)],
        ):
            result = subprocess.run(command, capture_output=True, timeout=30)
            assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
        result = subprocess.run([str(ffprobe), "-v", "error", "-show_streams", "-of", "json", str(video)],
                                capture_output=True, text=True, encoding="utf-8", timeout=15, check=True)
        assert json.loads(result.stdout)["streams"][0]["codec_name"] == "h264"
        assert image.stat().st_size > 0
        assert_av1_decoder(ffmpeg, root)
        # 合成片的 moov 位于 mdat 之后；移掉索引后用同编码参数的完整片重建。
        payload = video.read_bytes()
        offset = 0
        while offset + 8 <= len(payload):
            size = int.from_bytes(payload[offset:offset + 4], "big")
            assert size >= 8, "合成 MP4 盒子长度无效"
            if payload[offset + 4:offset + 8] == b"moov":
                break
            offset += size
        assert offset < len(payload), "合成 MP4 缺少 moov"
        broken, repaired = root / "broken.mp4", root / "repaired.mp4"
        broken.write_bytes(payload[:offset])
        result = subprocess.run([str(untrunc_root / "untrunc.exe"), "-n", "-s", "-dst", str(repaired),
                                 str(video), str(broken)], capture_output=True, timeout=30)
        assert repaired.is_file(), result.stderr.decode("utf-8", errors="replace")
        result = subprocess.run([str(ffprobe), "-v", "error", "-show_streams", "-of", "json", str(repaired)],
                                capture_output=True, text=True, encoding="utf-8", timeout=15, check=True)
        assert json.loads(result.stdout)["streams"][0]["codec_name"] == "h264"


@contextmanager
def smoke_directory(evidence_dir: Path):
    """失败时只保留本次临时运行的日志，不打包账本与配置。"""
    directory = tempfile.TemporaryDirectory(prefix="peach-desktop-")
    try:
        root = Path(directory.name).resolve()
        try:
            yield root
        except BaseException:
            destination = evidence_dir / uuid.uuid4().hex
            destination.mkdir(parents=True)
            for path in root.rglob("*.log"):
                target = destination / path.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            (destination / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
            print(f"制品验收失败，日志：{destination}", file=sys.stderr, flush=True)
            raise
    finally:
        cleanup_smoke_directory(directory)


def cleanup_smoke_directory(directory):
    """等待 Windows 释放原生缓存目录，持续占用仍判验收失败。"""
    deadline = time.monotonic() + 15
    while True:
        try:
            directory.cleanup()
            return
        except OSError as error:
            if getattr(error, 'winerror', None) not in (32, 145) or time.monotonic() >= deadline:
                raise
            time.sleep(.25)


def artifact_processes(executable: Path) -> list[int]:
    """仅核对本次制品的精确映像路径，包含脱离启动器的托盘与监视器。"""
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    environment = dict(os.environ, PEACH_SMOKE_EXECUTABLE=str(executable))
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command",
        "@(Get-Process | Where-Object { $_.Path -eq $env:PEACH_SMOKE_EXECUTABLE } | "
        "ForEach-Object { $_.Id }) | ConvertTo-Json -Compress"],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=15, check=True)
    value = json.loads(result.stdout.strip() or "[]")
    return value if isinstance(value, list) else [value]


def tray_window_ready(process_ids: list[int]) -> bool:
    """验到 pystray 的真实 Windows 消息窗口，且消息循环可以应答。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
        wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    found = []

    @callback_type
    def visit(window, _extra):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
        if process_id.value in process_ids:
            name = ctypes.create_unicode_buffer(512)
            user32.GetClassNameW(window, name, len(name))
            if name.value.startswith("Peach") and name.value.endswith("SystemTrayIcon"):
                result = ctypes.c_size_t()
                if user32.SendMessageTimeoutW(window, 0, 0, 0, 2, 1000, ctypes.byref(result)):
                    found.append(int(window))
        return True

    if not user32.EnumWindows(visit, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(found)


def lifecycle_events(data: Path) -> list[dict]:
    path = data / "logs/tray-lifecycle.log"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # 写入中的末行留给下一轮读取。
    return rows


def wait_for(check, description: str, *, timeout: float = 60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.25)
    raise RuntimeError(f"等待超时：{description}")


def smoke_environment(data: Path) -> dict[str, str]:
    state = data / 'state'
    state.mkdir(parents=True, exist_ok=True)
    (state / 'remote-reporting.json').write_text(json.dumps({
        'usage': False, 'errors': False, 'revision': 'desktop-smoke',
    }), encoding='utf-8')
    environment = dict(os.environ, PEACH_DATA_ROOT=str(data), PYTHONIOENCODING="utf-8")
    environment["LOCALAPPDATA"] = str(data.parent / "local-app-data")
    environment.pop("PYTHONPATH", None)
    environment["PATH"] = os.environ.get("SystemRoot", "/usr") + ("/System32" if os.name == "nt" else "/bin")
    return environment


def stop_tray(executable: Path, root: Path, environment: dict, log, launcher) -> None:
    try:
        stopped = subprocess.run([str(executable), "--installer-stop"], cwd=root,
            env=environment, stdout=log, stderr=log, timeout=90)
        assert stopped.returncode == 0, f"托盘退出失败：{stopped.returncode}"
        launcher.wait(timeout=15)
        wait_for(lambda: not artifact_processes(executable), "托盘、服务与监视器全部退出", timeout=20)
    except BaseException:
        # 正常退出契约失败也要回收本次实例；强制回收会保留失败结果和日志。
        watchdogs = root / "data/state/tray-watchdogs"
        for path in watchdogs.glob("*.json"):
            path.with_suffix(".stop").write_text("smoke-cleanup", encoding="utf-8")
        process_ids = artifact_processes(executable)
        guards = {int(path.read_text(encoding="utf-8")) for path in watchdogs.glob("*.ready")}
        taskkill = Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"
        for pid in sorted(process_ids, key=lambda pid: pid not in guards):
            subprocess.run([str(taskkill), "/PID", str(pid), "/T", "/F"],
                           stdout=log, stderr=log, timeout=15, check=False)
        launcher.wait(timeout=15)
        wait_for(lambda: not artifact_processes(executable), "失败实例全部退出", timeout=20)
        raise


def smoke_tray(executable: Path, evidence_dir: Path) -> None:
    """真实首启托盘、完成设置、接管服务并通过安装器契约正常退出。"""
    assert os.name == "nt", "托盘制品验收需要 Windows"
    assert not artifact_processes(executable), "此制品已有进程运行，不能接管它"
    with smoke_directory(evidence_dir) as root:
        data, media = root / "data", root / "media"
        media.mkdir()
        # 全新 Peach 数据目录的默认端口必须空闲，避免把其他部署当作首启服务。
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 8900))
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        environment = smoke_environment(data)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with (root / "runtime.log").open("wb") as log:
            launcher = subprocess.Popen([str(executable), "--silent"], cwd=root,
                env=environment, stdout=log, stderr=log)
            try:
                def tray_started():
                    rows = lifecycle_events(data)
                    pids = [row["tray_pid"] for row in rows if row["event"] == "watchdog_ready"]
                    return pids and tray_window_ready(pids)
                wait_for(tray_started, "托盘窗口、消息循环与监视器就绪")
                wait_health(opener, "http://127.0.0.1:8900", None, root / "runtime.log")
                selected = root / "selected-data"
                complete_first_run(opener, "http://127.0.0.1:8900", media, port,
                                   "standalone-tray-smoke-password", data_root=selected)
                health = wait_health(opener, f"http://127.0.0.1:{port}", None, root / "runtime.log")
                assert health["ok"]
                assert (selected / "database/ledger.db").is_file()
                assert (data / "state/data-root.txt").read_text(encoding="utf-8") == str(selected)
                wait_for(lambda: not (selected / "state/configuration-reload.request").exists(),
                         "托盘消费设置重载请求")
                assert_requires_login(opener, f"http://127.0.0.1:{port}")
            finally:
                stop_tray(executable, root, environment, log, launcher)
            rows = lifecycle_events(data)
            assert any(row["event"] == "stop_requested" and row.get("reason") == "external-stop"
                       for row in rows), "未取得托盘正常退出意图"
            assert any(row["event"] == "exited" and row.get("exit_code") == 0 for row in rows), "未取得托盘正常退出记录"
            assert not any(row["event"] in {"unexpected_exit", "watchdog_error", "recovery_started"}
                           for row in rows), "托盘发生异常退出或恢复"
            assert not list((data / "state/tray-watchdogs").glob("*.json")), "监视器记录仍在"
            assert not list((data / "state/tray-watchdogs").glob("*.ready")), "监视器握手仍在"
            # 原配置保存在自选目录、启动落点没有位置记录：恢复后再次普通启动也应进入馆藏。
            original = (selected / "config.toml").read_bytes()
            (data / "state/data-root.txt").unlink()
            launcher = subprocess.Popen([str(executable), "--silent"], cwd=root,
                env=environment, stdout=log, stderr=log)
            try:
                setup = "http://127.0.0.1:8900"
                assert wait_health(opener, setup, None, root / "runtime.log")["configured"] is False
                request = urllib.request.Request(setup + "/api/setup/restore",
                    data=json.dumps({"data_root": str(selected)}).encode(),
                    headers={"Origin": setup, "Content-Type": "application/json"})
                with opener.open(request, timeout=10) as response:
                    assert json.load(response)["restored"]
                assert wait_health(opener, f"http://127.0.0.1:{port}", None, root / "runtime.log")["configured"]
                assert (selected / "config.toml").read_bytes() == original
            finally:
                stop_tray(executable, root, environment, log, launcher)
            launcher = subprocess.Popen([str(executable), "--silent"], cwd=root,
                env=environment, stdout=log, stderr=log)
            try:
                assert wait_health(opener, f"http://127.0.0.1:{port}", None, root / "runtime.log")["configured"]
                assert_requires_login(opener, f"http://127.0.0.1:{port}")
            finally:
                stop_tray(executable, root, environment, log, launcher)
            print(json.dumps({"ok": True, "checks": ["tray-first-run", "tray-window",
                "tray-watchdog", "tray-service-handoff", "tray-graceful-stop", "tray-no-residue",
                "existing-data-recovery", "selected-data-relaunch"]}))


def wait_health(opener, base: str, process: subprocess.Popen | None, log_path: Path) -> dict:
    deadline = time.monotonic() + 40
    while True:
        try:
            with opener.open(base + "/healthz", timeout=1) as response:
                return json.load(response)
        except (OSError, urllib.error.URLError):
            if (process is not None and process.poll() is not None) or time.monotonic() > deadline:
                raise RuntimeError(f"服务未就绪：{base}/healthz\n" + log_path.read_text(encoding="utf-8", errors="replace"))
            time.sleep(.25)


def lan_base(port: int) -> str:
    routed = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        routed.connect(("192.0.2.1", 80))
        return f"http://{routed.getsockname()[0]}:{port}"
    finally:
        routed.close()


def assert_requires_login(opener, base: str) -> None:
    """登录之后 `/api/items` 返回 200，只说明它能返回 200。

    密码这道门是不是真的关着，得在还没登录的时候敲一次。
    """
    try:
        with opener.open(base + "/api/items", timeout=10) as response:
            raise AssertionError(f"未登录拿到了 /api/items：{response.status}")
    except urllib.error.HTTPError as error:
        assert error.code == 401, f"未登录访问 /api/items 应为 401，实际 {error.code}"


def assert_standalone_tunnel(configuration: dict) -> None:
    tunnel = configuration['tunnel']
    assert tunnel['enabled'] is False
    assert tunnel['state'] == 'stopped'
    assert tunnel['available'] is True


def complete_first_run(opener, base: str, media: Path, port: int, password: str, *,
                       data_root: Path | None = None, media_root: str | None = None) -> None:
    """首启服务的薄壳与页面包都取得到，再按页面包的提交形态完成一次设置。"""
    with opener.open(base + "/", timeout=5) as response:
        assert 'data-page="setup"' in response.read().decode("utf-8")
    for path in ("/dist/peach-pages.js", "/dist/peach-pages.css", "/api/setup/questions"):
        with opener.open(base + path, timeout=5) as response:
            assert response.status == 200, path
    answers = {"media_dir": [{"path": str(media), "location": "115"}], "host": "2",
               "port": str(port), "mdns_name": "peach", "access_enabled": True,
               "access_password": password, "access_confirm": password,
               "remote_reporting": {"usage": False, "errors": False}}
    if media_root is not None:
        answers["media_dir"][0]["root"] = media_root
    if data_root is not None:
        answers["data_root"] = str(data_root)
    request = urllib.request.Request(base + "/api/setup", data=json.dumps(answers).encode(),
        headers={"Origin": base, "Content-Type": "application/json"})
    with opener.open(request, timeout=60) as response:
        assert json.load(response)["url"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--evidence-dir", type=Path, default=Path("smoke-evidence"))
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    assert_bundled_media_tools(executable.parent)
    evidence_dir = args.evidence_dir.resolve()
    smoke_tray(executable, evidence_dir)
    with smoke_directory(evidence_dir) as root:
        data = root / "data"
        media = root / "media"
        media.mkdir()
        # 制品不得借助用户安装的 Python、Git、Node、FFmpeg 或 OpenSSL。
        environment = smoke_environment(data)
        with (root / "runtime.log").open("wb") as log:
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            setup_command = [str(executable), "serve", "--host", "127.0.0.1", "--port", str(port),
                             "--no-ledger-sync", "--no-mdns", "--setup"]
            process = subprocess.Popen(setup_command, cwd=root, env=environment, stdout=log, stderr=log)
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                base = f"http://127.0.0.1:{port}"
                health = wait_health(opener, base, process, root / "runtime.log")
                password = "standalone-smoke-password"
                complete_first_run(opener, base, media, port, password)
                assert (data / "database" / "ledger.db").is_file()
                process.terminate()
                process.wait(timeout=15)
                command = [str(executable), "serve", "--host", "0.0.0.0", "--port", str(port),
                           "--no-ledger-sync"]
                process = subprocess.Popen(command, cwd=root, env=environment, stdout=log, stderr=log)
                health = wait_health(opener, base, process, root / "runtime.log")
                # mDNS 这两行是服务自报：这个脚本只用标准库，发不出组播查询，也就
                # 验不到「局域网里真的解析得到 peach.local」。反查要 zeroconf，归
                # `tests/test_desktop_settings.py` 那一侧。
                assert health["mdns_backend"]
                assert health["mdns_service_host"] == "peach.local"
                with opener.open(lan_base(port) + "/healthz", timeout=5) as response:
                    assert json.load(response)["ok"]
                assert_requires_login(opener, base)
                login = urllib.request.Request(base + "/login",
                    data=urllib.parse.urlencode({"token": password, "days": "0"}).encode(),
                    headers={"Origin": base})
                with opener.open(login, timeout=10) as response:
                    assert response.status == 200
                for path in ("/", "/app.css", "/dist/peach-app.js", "/dist/peach-app.css", "/api/items"):
                    with opener.open(base + path, timeout=10) as response:
                        assert response.status == 200, path
                with opener.open(base + "/api/configuration", timeout=5) as response:
                    configuration = json.load(response)
                assert not any(row["term"] in ("FFmpeg", "untrunc") for row in configuration["facts"]), configuration["facts"]
                assert configuration['media_sources'][0]['location'] == '115'
                assert_standalone_tunnel(configuration)
                second = root / 'pikpak'
                second.mkdir()
                sources = configuration['media_sources'] + [{'location': 'pikpak', 'path': str(second)}]
                request = urllib.request.Request(base + "/api/configuration",
                    data=json.dumps({'media_sources': sources, 'port': port,
                                     'revision': configuration['revision']}).encode(),
                    headers={"Origin": base, 'Content-Type': 'application/json'})
                with opener.open(request, timeout=10) as response:
                    assert json.load(response)['saved']
                with opener.open(base + '/api/configuration', timeout=5) as response:
                    saved = json.load(response)
                assert [row['location'] for row in saved['media_sources']] == ['115', 'pikpak']
                assert (data / "config.previous.toml").is_file()
                assert (data / "state" / "configuration-reload.request").is_file()
                print(json.dumps({"ok": True, "version": health.get("version"),
                                  "checks": ["oobe", "migrations", "lan-bind", "mdns", "password-login", "unauthenticated-api", "pages", "island", "items", "clouddrive-configuration", "cloudflared-sidecar", "bundled-media-tools", "public-resources"]}))
            finally:
                process.terminate()
                process.wait(timeout=15)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
