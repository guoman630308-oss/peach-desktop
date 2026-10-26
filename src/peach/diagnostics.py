"""CLI 与 API 共用的分项诊断；不修复账本、配置、工具或挂载。"""
from __future__ import annotations

import ipaddress
import os
import platform
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
from contextlib import closing

from . import __version__, access, distribution, settings_file
from .ffmpeg import FFmpegResolver, binary_diagnostics
from .health import inspect_database
from .mount_reachability import MountReachability, PROBE_TIMEOUT


def item(label, status, reason, *, action="", target=None, details=None):
    return {"label": label, "status": status, "reason": reason, "action": action,
            "target": target, "details": details or {}}


def doctor_endpoint(config):
    """选择配置端口或具有 TLS 材料的源码托盘入口。"""
    configured = (config.server.host, config.server.port)
    if distribution.standalone():
        return configured
    tls = config.directory("secrets") / "tls"
    if not all((tls / name).is_file() for name in ("peach-local-ca.crt", "peach.crt", "peach.key")):
        return configured
    if sys.platform == "darwin":
        return "0.0.0.0", 8443
    from .mdns import lan_ipv4
    try:
        address = os.environ.get("PEACH_LAN_ADDRESS") or lan_ipv4()
    except (OSError, RuntimeError):
        address = "0.0.0.0"
    return address, 443


def configuration(config, configured, *, validate=True):
    try:
        if validate:
            settings_file.load_config(environ=dict(os.environ, PEACH_DATA_ROOT=str(config.data_root)))
        elif settings_file.error():
            raise settings_file.SettingsFileError("配置文件无法读取")
    except settings_file.SettingsFileError:
        return item("配置", "failed", "配置文件无法读取", action="在本机修复配置文件")
    if not configured:
        return item("配置", "failed", "尚未完成首次配置", action="完成首次配置", target="/settings/library")
    return item("配置", "ok", "配置可读取")


def database_checks(settings):
    state = inspect_database(settings.db_path)
    reasons = {"missing": "数据库不存在", "empty": "数据库尚未初始化", "unavailable": "数据库无法读取"}
    readable = state["database"] == "available" and state["readable"]
    database = item("数据库", "ok" if readable else "failed",
                    "数据库可读取" if readable else reasons.get(state["database"], "数据库结构无法读取"),
                    action="" if readable else "检查本机数据库与备份", details={"state": state["database"]})
    schema_reasons = {"current": "迁移校验通过", "pending": "存在待执行迁移", "empty": "数据库尚未初始化",
                      "mismatch": "迁移记录与当前程序不一致", "unavailable": "迁移校验未取得"}
    current = state["schema"] == "current"
    schema = item("数据库迁移", "ok" if current else "failed", schema_reasons[state["schema"]],
                  action="" if current else "在本机核对数据库迁移与备份",
                  details={key: state[key] for key in ("schema", "version", "pending", "mismatched", "unknown_versions")})
    return database, schema


def tools(settings, *, inspect=True):
    resolver = FFmpegResolver(settings.ffmpeg_root)
    found = {}
    for name, choice in (("ffmpeg", resolver.ffmpeg()), ("ffprobe", resolver.ffprobe())):
        found[name] = (binary_diagnostics(choice, decoders=name == 'ffmpeg') if inspect else
                       {'available': choice is not None, 'source': choice.source if choice else None,
                        'path': str(choice.path) if choice else None})
    openssl = shutil.which("openssl")
    found["openssl"] = {"available": openssl is not None, "source": "path" if openssl else None,
                        "path": openssl}
    missing = [name for name, value in found.items() if not value["available"]]
    unavailable = [name for name in ('ffmpeg', 'ffprobe') if found[name]['available'] and not found[name].get('usable', True)]
    incomplete = found['ffmpeg'].get('usable') and not found['ffmpeg'].get('av1_software')
    problems = (["未找到：" + "、".join(missing)] if missing else [])
    problems += (["无法执行：" + "、".join(unavailable)] if unavailable else [])
    problems += ['FFmpeg 缺少 AV1 软件解码器'] if incomplete else []
    return item("FFmpeg／OpenSSL", 'warning' if problems else 'ok',
                '；'.join(problems) or ('媒体工具可执行，AV1 软件解码器可用' if inspect else '工具路径已找到'),
                action='更新媒体工具' if problems else '', target='/settings/maintenance' if problems else None, details=found)


def security(settings):
    policy = access.load(settings.access_path)
    try:
        loopback = settings.host == "localhost" or ipaddress.ip_address(settings.host).is_loopback
    except ValueError:
        loopback = False
    protected = policy["mode"] == "password" or (policy["mode"] == "legacy" and bool(settings.token))
    exposed = not loopback or settings.tunnel_enabled
    if policy["mode"] == "locked":
        return item("访问安全", "failed", "访问密码配置无法读取", action="在本机检查访问密码配置")
    if exposed and not protected:
        return item("访问安全", "warning", "局域网或公网入口未开启访问密码",
                    action="在配置中开启访问密码", target="/settings/network", details={"exposed": True, "protected": False})
    return item("访问安全", "ok", "已开启访问密码" if protected else "仅监听本机",
                details={"exposed": exposed, "protected": protected})


def media_mounts(snapshot):
    if not snapshot["sources"]:
        return item("媒体挂载", "disabled", "没有已配置的本地来源")
    states = {"ok": "ok", "degraded": "warning", "checking": "unknown"}
    reason = "来源可读取" if snapshot["state"] == "ok" else "；".join(snapshot["warnings"]) or "挂载检测尚未完成"
    return item("媒体挂载", states[snapshot["state"]], reason,
                action="检查媒体目录与挂载状态" if snapshot["state"] != "ok" else "", target="/settings/library",
                details=snapshot)


def component_checks(settings, snapshot, *, config=None, validate_config=True, inspect_tools=True):
    config = config or settings_file.active()
    database, schema = database_checks(settings)
    checks = {"database": database, "schema": schema, "configured": configuration(config, settings.configured, validate=validate_config),
              "media_mounts": media_mounts(snapshot), "security": security(settings)}
    checks["ffmpeg"] = tools(settings, inspect=inspect_tools)
    return checks


def writable_root(root):
    try:
        with tempfile.TemporaryFile(dir=root, prefix=".peach-doctor-") as probe:
            probe.write(b"peach")
            probe.flush()
    except PermissionError:
        return item("数据目录", "failed", "没有权限写入数据目录", action="调整数据目录的写入权限")
    except OSError:
        return item("数据目录", "failed", "数据目录无法写入", action="检查数据目录与可用空间")
    return item("数据目录", "ok", "临时写入与清理通过")


def port_status(host, port):
    host = "127.0.0.1" if host == "localhost" else host
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return item("服务端口", "unknown", "监听地址不是 IP，端口状态未取得", details={"port": port})
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.bind((host, port))
    except OSError as error:
        import errno
        if error.errno == errno.EADDRINUSE or getattr(error, "winerror", None) == 10048:
            return item("服务端口", "ok", "端口正在使用", details={"port": port, "state": "in_use"})
        return item("服务端口", "unknown", "端口状态未取得", details={"port": port})
    return item("服务端口", "ok", "端口当前可用", details={"port": port, "state": "available"})


def recent_failure(path):
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute("SELECT id,finished_at FROM task_run WHERE status='failed' "
                                     "ORDER BY finished_at DESC,id DESC LIMIT 1").fetchone()
    except (sqlite3.Error, OSError):
        return item("后台任务", "unknown", "任务失败记录未取得")
    if row is None:
        return item("后台任务", "ok", "没有已记录的任务失败")
    # 不公开任务 error、参数或结果原文，它们可能包含凭据和媒体路径。
    moment = str(row[1] or "")
    safe_time = moment if len(moment) <= 32 and all(c in "0123456789-:+.TZ " for c in moment) else None
    return item("后台任务", "warning", "存在已记录的任务失败", action="在活动页查看失败任务",
                target="/tasks", details={"id": row[0], "finished_at": safe_time})


def probe_mounts(roots):
    monitor = MountReachability(roots)
    deadline = time.monotonic() + PROBE_TIMEOUT + 1.5
    try:
        while True:
            monitor.tick()
            snapshot = monitor.summary()
            if all(row["state"] != "checking" for row in snapshot["sources"]) or time.monotonic() >= deadline:
                return snapshot
            time.sleep(0.05)
    finally:
        monitor.stop()


def report(settings, snapshot, *, config=None):
    from . import library_diagnostics
    config = config or settings_file.active()
    checks = component_checks(settings, snapshot, config=config)
    checks.update(data_root=writable_root(config.data_root), port=port_status(settings.host, settings.port),
                  tasks=recent_failure(settings.db_path),
                  version=item("版本", "ok", f"Peach {__version__} · Python {platform.python_version()}",
                               details={"peach": __version__, "python": platform.python_version()}))
    statuses = {check["status"] for check in checks.values()}
    state = "failed" if "failed" in statuses else "warning" if statuses & {"warning", "unknown"} else "ok"
    return {"version": __version__, "status": state, "checks": checks,
            "library": library_diagnostics.library(settings, config),
            "sources": library_diagnostics.sources(settings, config)}


def health_components(settings, snapshot):
    checks = component_checks(settings, snapshot, validate_config=False, inspect_tools=False)
    # 公开健康检查只保留分项原因；工具路径与详细修法由本机诊断提供。
    return {key: {name: value[name] for name in ("status", "reason")} for key, value in checks.items()}
