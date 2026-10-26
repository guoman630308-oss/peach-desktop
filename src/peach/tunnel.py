"""Cloudflare Tunnel 的受控进程边界。

Peach 不实现 Cloudflare 协议，只负责三件事：为两种部署选正确的本机 origin，
把访问密码作为启动前置条件，以及在自己的服务生命周期内管理 ``cloudflared``。
Quick Tunnel 的随机地址只写进 Peach 数据目录的状态文件，不进入设置、账本或日志文档。

命名隧道（Named Tunnel）多一条固定入口：地址是用户在 Cloudflare 后台绑定的公开主机名，
不从 cloudflared 的输出里捕获，就绪判据只剩 pidfile。它要一份隧道令牌，只存在设置文件里，
不进账本、日志与健康检查；本模块把它从子进程输出中抹掉，也不打印任何启动计划。
"""
from __future__ import annotations

import ctypes
import json
import ipaddress
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO

from . import access
from .process_job import assign_to_job, close_job, create_kill_on_close_job


STATE_FILENAME = "cloudflare-tunnel.json"
LOG_FILENAME = "cloudflare-tunnel.log"
PID_FILENAME = "cloudflared.pid"
QUICK_URL_RE = re.compile(
    r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com",
    re.IGNORECASE,
)
#: cloudflared 转发时一定会带上的请求头；只要隧道在跑，带这些头的连接就来自公网。
EDGE_HEADERS = ("cf-connecting-ip", "cf-ray")
QUICK_MODE = "quick"
NAMED_MODE = "named"
MODES = (QUICK_MODE, NAMED_MODE)
STANDALONE_NAMED_ERROR = "独立包只支持临时链接"
#: 一段主机名标签：字母数字开头结尾，中间可带连字符；整串至少两段且不超过 253 字符。
HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


class TunnelError(RuntimeError):
    """Tunnel 不能安全启动或已经异常退出。"""


@dataclass(frozen=True)
class TunnelOrigin:
    url: str
    origin_server_name: str = ""
    ca_path: Path | None = None


@dataclass(frozen=True)
class TunnelPlan:
    binary: Path
    origin: TunnelOrigin
    command: tuple[str, ...]
    mode: str = QUICK_MODE
    #: 命名隧道的公开入口。非空表示地址已经定下来，不必也不该从子进程输出里捕获。
    url: str = ""
    #: 命令里带着的凭据。管理器只用它做一件事：把它从子进程日志里抹掉。
    secret: str = ""


@dataclass(frozen=True)
class TunnelSnapshot:
    state: str = "stopped"
    url: str = ""
    error: str = ""
    pid: int | None = None
    started_at: str = ""


def state_path(state_dir: Path) -> Path:
    return Path(state_dir) / STATE_FILENAME


def log_path(log_dir: Path) -> Path:
    return Path(log_dir) / LOG_FILENAME


def extract_quick_url(line: str) -> str | None:
    """从 cloudflared 的普通/警告输出中提取随机入口。"""
    match = QUICK_URL_RE.search(line or "")
    return match.group(0).rstrip(".,);]") if match else None


def resolve_binary(
    configured: str = "",
    *,
    executable: Path | None = None,
    environ: dict[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> Path | None:
    """按显式配置、打包旁路、环境变量和 PATH 的顺序找 cloudflared。

    Peach 数据目录不在搜索范围里：它是用户放媒体和账本的地方，任何能往那里写文件的人都
    不该因此获得一次本机可执行文件的启动机会。
    """
    environ = os.environ if environ is None else environ
    candidates: list[Path] = []

    explicit = (environ.get("PEACH_CLOUDFLARED") or configured).strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())

    package_dir = Path(executable).resolve().parent if executable else None
    if package_dir is None and getattr(sys, "frozen", False):
        package_dir = Path(sys.executable).resolve().parent
    if package_dir is not None:
        candidates.extend((
            package_dir / "cloudflared.exe",
            package_dir / "cloudflared",
            package_dir / "_internal" / "cloudflared.exe",
            package_dir / "_internal" / "cloudflared",
        ))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue

    for name in ("cloudflared.exe", "cloudflared"):
        found = which(name)
        if found:
            return Path(found).resolve()
    return None


def build_origin(
    *, standalone_mode: bool, port: int | None, mdns_name: str = "",
    lan_address: str | None = None, ca_path: Path | None = None,
) -> TunnelOrigin:
    """为源码托盘和独立包分别构造 origin。

    源码服务的 80 端口只是跳转口，不能拿来做 Tunnel origin；独立包则没有本机 TLS
    入口，使用回环 HTTP。Cloudflare edge 到浏览器仍然是 HTTPS。源码未显式传端口时
    取标准 HTTPS 端口。
    """
    if standalone_mode:
        if type(port) is not int or not 1 <= port <= 65535:
            raise TunnelError(f"独立包 Tunnel 的 HTTP 端口无效：{port}")
        return TunnelOrigin(f"http://127.0.0.1:{port}")
    if port is None:
        port = 443
    address = (lan_address or "").strip()
    if not address:
        raise TunnelError("无法确定源码服务的局域网地址")
    try:
        parsed_address = ipaddress.ip_address(address)
    except ValueError as exc:
        raise TunnelError(f"源码 Tunnel 的局域网地址不是 IP：{address}") from exc
    hostname = (mdns_name or "").strip().rstrip(".")
    if not hostname:
        raise TunnelError("源码服务没有配置 mDNS 名称")
    origin_name = hostname if hostname.endswith(".local") else f"{hostname}.local"
    if ca_path is None or not Path(ca_path).is_file():
        raise TunnelError(f"源码 Tunnel 需要项目 CA：{ca_path}")
    if type(port) is not int or not 1 <= port <= 65535:
        raise TunnelError(f"源码 Tunnel 的 HTTPS 端口无效：{port}")
    host = f"[{address}]" if parsed_address.version == 6 else address
    return TunnelOrigin(
        f"https://{host}:{port}", origin_server_name=origin_name, ca_path=Path(ca_path),
    )


def origin_for_config(
    config, *, standalone_mode: bool, lan_address: str | None = None,
    https_port: int | None = None,
) -> TunnelOrigin:
    """从设置文件取端口、mDNS 名和项目 CA，其余交给 `build_origin`。"""
    port = config.server.port if standalone_mode and https_port is None else https_port
    return build_origin(
        standalone_mode=standalone_mode, port=port,
        mdns_name=getattr(config.server, "mdns_name", ""),
        lan_address=lan_address,
        ca_path=config.directory("secrets") / "tls" / "peach-local-ca.crt",
    )


def _access_error(policy: dict, token: str) -> str | None:
    if policy.get("mode") == "locked":
        return "访问设置损坏，不能启动公网 Tunnel"
    if policy.get("mode") == "open":
        return "启动公网 Tunnel 前必须先设置访问密码"
    if policy.get("mode") not in {"password", "legacy"}:
        return "访问设置不是受支持的密码模式"
    if not token:
        return "缺少 Peach 内部访问令牌，不能启动公网 Tunnel"
    return None


def validate_access(access_path: Path | None, token: str) -> None:
    """公网入口只接受有第二道登录闸门的部署。"""
    policy = access.load(access_path)
    message = _access_error(policy, token)
    if message:
        raise TunnelError(message)


def normalize_mode(value: str) -> str:
    """把设置里的模式收敛成两个已知值之一；空串按临时链接处理。"""
    mode = (value or "").strip().lower() or QUICK_MODE
    if mode not in MODES:
        raise TunnelError(f"tunnel.mode 只能是 quick 或 named：{value}")
    return mode


def normalize_hostname(value: str) -> str:
    """校验公开主机名。

    用户多半从 Cloudflare 后台整条地址复制过来，所以容忍 `https://` 前缀和末尾的点；
    其余一律按域名判，路径、端口和空格都算填错——这个值同时用于展示和同源校验，
    放一个解析不出主机的串进去等于把写接口的门开在一个谁都对不上的名字上。
    """
    host = (value or "").strip().rstrip(".").lower()
    for prefix in ("https://", "http://"):
        if host.startswith(prefix):
            host = host[len(prefix):]
    host = host.rstrip("/").rstrip(".")
    if not host:
        raise TunnelError("命名隧道需要填写公开主机名")
    if not HOSTNAME_RE.match(host):
        raise TunnelError(f"公开主机名不是有效的域名：{value}")
    return host


def public_url(hostname: str) -> str:
    return f"https://{hostname}"


def build_command(binary: Path, origin: TunnelOrigin) -> tuple[str, ...]:
    """构造不带凭据的 Quick Tunnel 命令。"""
    command = [
        str(binary), "tunnel", "--url", origin.url,
        "--no-autoupdate", "--loglevel", "info", "--metrics", "127.0.0.1:0",
    ]
    if origin.origin_server_name:
        command.extend(("--origin-server-name", origin.origin_server_name))
    if origin.ca_path is not None:
        command.extend(("--origin-ca-pool", str(origin.ca_path)))
    return tuple(command)


def build_named_command(binary: Path, token: str) -> tuple[str, ...]:
    """命名隧道的入向配置在 Cloudflare 后台，这里不传 `--url` 也不传 origin 选项。

    `--no-autoupdate`、`--loglevel`、`--metrics` 和管理器补的 `--pidfile` 都是 `tunnel`
    这一级的选项，必须写在 `run` 前面；`run` 只认它自己的 `--token`。
    """
    return (
        str(binary), "tunnel",
        "--no-autoupdate", "--loglevel", "info", "--metrics", "127.0.0.1:0",
        "run", "--token", token,
    )


def named_plan(
    binary: Path, *, token: str, hostname: str, standalone_mode: bool,
) -> TunnelPlan:
    """命名隧道只在源码开发环境可用，且必须同时有令牌和公开主机名。"""
    if standalone_mode:
        raise TunnelError(STANDALONE_NAMED_ERROR)
    secret = (token or "").strip()
    if not secret:
        raise TunnelError("命名隧道需要填写 Cloudflare 隧道令牌")
    url = public_url(normalize_hostname(hostname))
    return TunnelPlan(
        binary=binary, origin=TunnelOrigin(url), command=build_named_command(binary, secret),
        mode=NAMED_MODE, url=url, secret=secret,
    )


def plan_for_config(
    config,
    *,
    access_path: Path | None,
    token: str,
    standalone_mode: bool,
    lan_address: str | None = None,
    https_port: int | None = None,
    tls_enabled: bool = True,
    executable: Path | None = None,
    environ: dict[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> TunnelPlan:
    """校验安全前置并返回可审阅的启动计划。"""
    validate_access(access_path, token)
    if not standalone_mode and not tls_enabled:
        raise TunnelError("源码 Tunnel 需要正在运行的 HTTPS 服务")
    tunnel_settings = getattr(config, "tunnel", None)
    binary = _require_binary(
        getattr(tunnel_settings, "binary", ""),
        executable=executable, environ=environ, which=which,
    )
    if normalize_mode(getattr(tunnel_settings, "mode", "")) == NAMED_MODE:
        return named_plan(
            binary,
            token=getattr(tunnel_settings, "token", ""),
            hostname=getattr(tunnel_settings, "hostname", ""),
            standalone_mode=standalone_mode,
        )
    origin = origin_for_config(
        config, standalone_mode=standalone_mode, lan_address=lan_address,
        https_port=https_port,
    )
    return TunnelPlan(binary=binary, origin=origin, command=build_command(binary, origin))


def _require_binary(
    configured: str,
    *,
    executable: Path | None = None,
    environ: dict[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> Path:
    binary = resolve_binary(
        configured, executable=executable, environ=environ, which=which,
    )
    if binary is None:
        raise TunnelError(
            "找不到 cloudflared：安装官方 cloudflared，"
            "或用设置文件的 tunnel.binary 与 PEACH_CLOUDFLARED 指定路径"
        )
    return binary


def plan_for_settings(
    settings,
    *,
    executable: Path | None = None,
    environ: dict[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> TunnelPlan:
    """只用本次启动注入的运行设置构造计划。

    服务进程不再回头读设置文件：公网入口的开关、端口和 origin 必须与这条服务实际
    监听的形态一致，而设置文件可以在服务运行期间被改成别的样子。
    """
    validate_access(settings.access_path, settings.token)
    if not settings.tunnel_standalone and not settings.tls_enabled:
        raise TunnelError("源码 Tunnel 需要正在运行的 HTTPS 服务")
    binary = _require_binary(
        settings.tunnel_binary, executable=executable, environ=environ, which=which,
    )
    if normalize_mode(getattr(settings, "tunnel_mode", "")) == NAMED_MODE:
        return named_plan(
            binary,
            token=getattr(settings, "tunnel_token", ""),
            hostname=getattr(settings, "tunnel_hostname", ""),
            standalone_mode=settings.tunnel_standalone,
        )
    origin = build_origin(
        standalone_mode=settings.tunnel_standalone,
        port=settings.tunnel_origin_port,
        mdns_name=settings.mdns_name,
        lan_address=settings.tunnel_lan_address,
        ca_path=settings.tunnel_ca_path,
    )
    return TunnelPlan(binary=binary, origin=origin, command=build_command(binary, origin))


def running(manager) -> bool:
    """只有 manager 自己报告 running 才算公网入口在服务中。"""
    if manager is None:
        return False
    try:
        return manager.snapshot().state == "running"
    except (AttributeError, OSError, ValueError):
        return False


def from_edge(manager, headers) -> bool:
    """隧道在跑时，带 Cloudflare 转发头的连接一律按公网来源处理。"""
    if not running(manager):
        return False
    return any(headers.get(name) for name in EDGE_HEADERS)


def _read_state(path: Path) -> TunnelSnapshot:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return TunnelSnapshot()
    except (OSError, ValueError, TypeError):
        return TunnelSnapshot(error="Tunnel 状态文件不可读")
    if not isinstance(data, dict):
        return TunnelSnapshot(error="Tunnel 状态文件格式错误")
    return TunnelSnapshot(
        state=str(data.get("state") or "stopped"),
        url=str(data.get("url") or ""),
        error=str(data.get("error") or ""),
        pid=int(data["pid"]) if isinstance(data.get("pid"), int) else None,
        started_at=str(data.get("started_at") or ""),
    )


def saved_snapshot(state_dir: Path) -> TunnelSnapshot:
    """读取状态文件；不把旧的 running 记录误报成当前进程仍在运行。"""
    saved = _read_state(state_path(state_dir))
    if saved.state in {"running", "starting"}:
        return TunnelSnapshot(
            state="stopped", error="上一次 Tunnel 没有正常收尾，运行结果未取得",
        )
    return saved


def _write_state(path: Path, snapshot: TunnelSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": snapshot.state,
        "url": snapshot.url,
        "error": snapshot.error,
        "pid": snapshot.pid,
        "started_at": snapshot.started_at,
    }
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=".cloudflare-tunnel-", suffix=".json",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001


def _windows_image_name(pid: int) -> str:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    )
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return Path(buffer.value).name
    finally:
        kernel32.CloseHandle(handle)


def _posix_image_name(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return Path(result.stdout.strip()).name if result.returncode == 0 else ""


def process_image_name(pid: int) -> str:
    """取这个 pid 当前的可执行名；取不到就返回空串，调用方按未取得处理。"""
    if pid <= 0:
        return ""
    try:
        return _windows_image_name(pid) if os.name == "nt" else _posix_image_name(pid)
    except (OSError, ValueError, AttributeError):
        return ""


def _terminate_windows_pid(pid: int) -> None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    handle = kernel32.OpenProcess(_PROCESS_TERMINATE | 0x00100000, False, pid)
    if not handle:
        return
    try:
        kernel32.TerminateProcess(handle, 1)
        kernel32.WaitForSingleObject(handle, 5000)
    finally:
        kernel32.CloseHandle(handle)


def _terminate_posix_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def terminate_pid(pid: int) -> None:
    """终止一个不是本进程子进程的 pid；调用方负责先确认它就是 cloudflared。"""
    if pid <= 0:
        return
    try:
        if os.name == "nt":
            _terminate_windows_pid(pid)
        else:
            _terminate_posix_pid(pid)
    except (OSError, ValueError, AttributeError):
        pass


class TunnelManager:
    """一个 Peach 服务进程拥有的一条 cloudflared 子进程。"""

    def __init__(
        self,
        state_dir: Path,
        log_dir: Path,
        *,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        wait_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        image_name: Callable[[int], str] = process_image_name,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.log_dir = Path(log_dir)
        self._popen = popen
        self._image_name = image_name
        self._job = None
        self._wait_timeout = wait_timeout
        self._clock = clock
        self._lock = threading.RLock()
        # Only one caller may perform the spawn-and-ready handshake at a time.
        # `snapshot()` and `stop()` remain available while Cloudflare is connecting.
        self._start_lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._url = ""
        # 命名隧道的地址由设置给定，子进程输出里出现的任何地址都不该盖掉它。
        self._fixed_url = False
        self._secret = ""
        self._error = ""
        self._started_at = ""
        self._ready = threading.Event()
        # A stopped child may still be draining its pipe while a new one starts.
        # The generation prevents that old reader from publishing a stale URL.
        self._generation = 0

    @property
    def state_file(self) -> Path:
        return state_path(self.state_dir)

    @property
    def log_file(self) -> Path:
        return log_path(self.log_dir)

    @property
    def pid_file(self) -> Path:
        return self.state_dir / PID_FILENAME

    @staticmethod
    def _process_pid(process: subprocess.Popen | None) -> int | None:
        value = getattr(process, "pid", None)
        return value if isinstance(value, int) and value > 0 else None

    @staticmethod
    def _process_alive(process: subprocess.Popen | None) -> bool:
        if process is None:
            return False
        try:
            return process.poll() is None
        except (AttributeError, OSError):
            return False

    def _pidfile_matches(self, process: subprocess.Popen | None) -> bool:
        """Accept only the pidfile written by the current cloudflared child."""
        try:
            value = self.pid_file.read_text(encoding="ascii").strip()
            pid = int(value)
        except (OSError, ValueError):
            return False
        process_pid = self._process_pid(process)
        return pid > 0 and (process_pid is None or pid == process_pid)

    @staticmethod
    def _terminate_process(process: subprocess.Popen | None) -> None:
        if process is None:
            return
        try:
            alive = process.poll() is None
        except (AttributeError, OSError):
            alive = False
        if not alive:
            return
        try:
            process.terminate()
            process.wait(timeout=8)
        except (AttributeError, OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=3)
            except (AttributeError, OSError, subprocess.TimeoutExpired):
                pass

    def reclaim_orphan(self) -> int | None:
        """收掉上一次服务留下的 cloudflared，并返回被收掉的 pid。

        判据是 pidfile 里的进程此刻还在、且可执行名就是 cloudflared；名字取不到或
        对不上就原样留着，绝不按 pid 猜身份去杀一个别人的进程。
        """
        try:
            pid = int(self.pid_file.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None
        if pid <= 0 or pid == os.getpid():
            return None
        name = self._image_name(pid)
        if "cloudflared" not in name.lower():
            return None
        terminate_pid(pid)
        return pid

    def _write_error(self, message: str) -> None:
        self._error = message
        # 启动失败后不留着命名隧道那个固定地址：它是计划里的值，不是一条在服务的入口。
        self._url = ""
        self._fixed_url = False
        self._secret = ""
        try:
            _write_state(self.state_file, TunnelSnapshot(state="error", error=message))
        except OSError:
            # The process must still be stopped when the state directory is not writable.
            pass

    def snapshot(self) -> TunnelSnapshot:
        with self._lock:
            process = self._process
            if self._process_alive(process):
                return TunnelSnapshot(
                    state="running" if self._url and self._pidfile_matches(process) else "starting",
                    url=self._url, error=self._error, pid=self._process_pid(process),
                    started_at=self._started_at,
                )
            if process is not None and not self._process_alive(process):
                error = self._error or "cloudflared 已退出"
                return TunnelSnapshot(
                    state="error", url=self._url, error=error,
                    pid=self._process_pid(process), started_at=self._started_at,
                )
        # 服务重启后不复用旧进程；状态文件只用于给页面一个明确的 stopped/error 结论。
        return saved_snapshot(self.state_dir)

    def _read_output(
        self, stream: TextIO, process: subprocess.Popen, handle: TextIO, generation: int,
    ) -> None:
        try:
            for raw in iter(stream.readline, ""):
                line = str(raw).rstrip("\r\n")
                secret = self._secret
                if secret:
                    line = line.replace(secret, "***")
                if line:
                    handle.write(line + "\n")
                    handle.flush()
                if self._fixed_url:
                    continue
                url = extract_quick_url(line)
                if url:
                    with self._lock:
                        if generation == self._generation:
                            self._url = url
                            self._ready.set()
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass
            try:
                handle.close()
            except OSError:
                pass
            if generation == self._generation:
                self._ready.set()
                if not self._process_alive(process):
                    with self._lock:
                        if not self._url and not self._error:
                            self._error = "cloudflared 在取得入口前退出"
                        snapshot = TunnelSnapshot(
                            state="error", url=self._url, error=self._error,
                            pid=self._process_pid(process), started_at=self._started_at,
                        )
                    try:
                        _write_state(self.state_file, snapshot)
                    except OSError:
                        pass

    def start(self, plan: TunnelPlan) -> TunnelSnapshot:
        with self._start_lock:
            return self._start_impl(plan)

    def _start_impl(self, plan: TunnelPlan) -> TunnelSnapshot:
        with self._lock:
            if self._process_alive(self._process):
                return self.snapshot()
            self._generation += 1
            generation = self._generation
            # 命名隧道开工前地址就是已知的，等的只是 cloudflared 把 pidfile 写出来。
            self._url = plan.url
            self._fixed_url = bool(plan.url)
            self._secret = plan.secret
            self._error = ""
            self._started_at = datetime.now(timezone.utc).isoformat()
            self._ready.clear()
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self.reclaim_orphan()
            self.pid_file.unlink(missing_ok=True)
            if self._job is None:
                self._job = create_kill_on_close_job()
            handle: TextIO | None = None
            process: subprocess.Popen | None = None
            try:
                # Open the log before spawning the child. If the data root is not
                # writable, no untracked cloudflared process may be left behind.
                handle = self.log_file.open("a", encoding="utf-8")
                process = self._popen(
                    # `--pidfile` 紧跟 `tunnel` 子命令：Quick 与 Named 两种命令里它都属于这一级。
                    [*plan.command[:2], "--pidfile", str(self.pid_file), *plan.command[2:]],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    shell=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                self._terminate_process(process)
                if handle is not None:
                    try:
                        handle.close()
                    except OSError:
                        pass
                message = f"cloudflared 启动失败：{exc}"
                self._generation += 1
                self._process = None
                self._reader = None
                self._ready.set()
                self._write_error(message)
                raise TunnelError(message) from exc
            assign_to_job(self._job, process)
            self._process = process
            stream = process.stdout
            if stream is None:
                message = "cloudflared 没有输出通道"
                self._terminate_process(process)
                self._generation += 1
                self._process = None
                self._reader = None
                self._ready.set()
                if handle is not None:
                    try:
                        handle.close()
                    except OSError:
                        pass
                self._write_error(message)
                raise TunnelError(message)
            self._reader = threading.Thread(
                target=self._read_output, args=(stream, process, handle, generation),
                name="PeachCloudflared", daemon=True,
            )
            try:
                self._reader.start()
                _write_state(self.state_file, TunnelSnapshot(
                    state="starting", pid=self._process_pid(process),
                    started_at=self._started_at,
                ))
            except (OSError, RuntimeError) as exc:
                message = f"Tunnel 启动状态失败：{exc}"
                self._generation += 1
                self._process = None
                self._reader = None
                self._ready.set()
                self._terminate_process(process)
                if handle is not None:
                    try:
                        handle.close()
                    except OSError:
                        pass
                self._write_error(message)
                raise TunnelError(message) from exc

        deadline = self._clock() + self._wait_timeout
        while self._clock() < deadline:
            self._ready.wait(0.1)
            with self._lock:
                if not self._process_alive(self._process):
                    break
                if self._url and self._pidfile_matches(self._process):
                    break
                self._ready.clear()
        with self._lock:
            process = self._process
            url = self._url
            error = self._error
            alive = self._process_alive(process)
            connected = self._pidfile_matches(process)
        if not url or not alive or not connected:
            if alive:
                self.stop()
            message = error or (
                "cloudflared 未在限定时间内取得入口"
                if not url else "cloudflared 未在限定时间内连接到 Cloudflare 边缘"
            )
            self._write_error(message)
            raise TunnelError(message)
        snapshot = TunnelSnapshot(
            state="running", url=url, pid=self._process_pid(process),
            started_at=self._started_at,
        )
        try:
            _write_state(self.state_file, snapshot)
        except OSError as exc:
            message = f"Tunnel 状态写入失败：{exc}"
            self.stop()
            self._write_error(message)
            raise TunnelError(message) from exc
        return snapshot

    def stop(self) -> TunnelSnapshot:
        with self._lock:
            process = self._process
            reader = self._reader
            job = self._job
            self._process = None
            self._reader = None
            self._job = None
            self._generation += 1
            cleanup_generation = self._generation
            self._url = ""
            self._fixed_url = False
            self._secret = ""
            self._error = ""
            self._ready.set()
        self._terminate_process(process)
        close_job(job)
        if reader is not None and reader.is_alive():
            reader.join(timeout=1)
        snapshot = TunnelSnapshot(state="stopped")
        with self._lock:
            if self._generation != cleanup_generation or self._process is not None:
                return snapshot
            try:
                self.state_file.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                try:
                    _write_state(self.state_file, snapshot)
                except OSError:
                    pass
            try:
                self.pid_file.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        return snapshot
