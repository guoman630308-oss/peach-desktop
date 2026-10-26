"""按本机选择发送技术诊断；不读取历史日志、账本或媒体。"""
from __future__ import annotations

import atexit
import errno
import hashlib
import json
import os
import platform
import re
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__

FIELDS = {
    "usage": "Peach 版本、操作系统类型；每天最多一次，不含安装标识。",
    "errors": "Peach 版本、系统类型、Python 版本、CPU 架构、故障环节、异常链、Peach 代码文件名/函数名/行号、结构化错误码、媒体工具故障类别、HTTP 方法/路由模板/关联错误编号、同类故障次数与统计日期；首次立即上传，重复次数每小时或退出时合并，每天最多 20 批。",
}
NOTICE = "发送至 Sentry。不上报错误原文、完整日志、诊断 ZIP、媒体名称、路径、网址、观看记录或凭据。接收服务会接触连接 IP；关闭后停止新发送，已发送的信息无法撤回。"
_lock = threading.RLock()
_client = None
_client_dsn = ""
_disabled = False
_frequency_timer = None
_frequency_configs = {}
MEDIA_STAGES = frozenset({"frame-capture", "frame-tile", "media-probe", "preview", "library-preview"})
LOCAL_EXCEPTION_TYPES = frozenset({
    ("asyncio.exceptions", "CancelledError"), ("concurrent.futures._base", "CancelledError"),
    ("builtins", "InterruptedError"), ("peach.jobs", "JobPolicyError"),
    ("peach.jav_cover_fetch", "Unavailable"), ("peach.jav_cover_fetch", "DeadlineExceeded"),
    ("peach.scraping_access", "SourcePaused"), ("peach.previews", "PreviewUnavailable"),
    ("peach.transcodes", "TranscodeCancelled"), ("peach.segments", "SegmentCancelled"),
    ("peach.browser_transport", "BrowserUnavailable"), ("peach.browser_transport", "ChallengeUnsolved"),
    ("peach.face_detect", "FaceModelUnavailable"), ("peach.avatar_watermark", "WatermarkModelUnavailable"),
    ("starlette.requests", "ClientDisconnect"),
})


def _config(config=None):
    if config is None:
        from .settings_file import active
        config = active()
    return config


def _read(path, *, missing=None):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return missing if missing is not None else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def _consent(path):
    value = _read(path, missing={"usage": True, "errors": True, "revision": "default"})
    enabled = all(value.get(key) is True for key in FIELDS)
    return {"usage": enabled, "errors": enabled,
            "revision": str(value.get("revision", ""))}


def consent(config=None):
    return _consent(_config(config).directory("state") / "remote-reporting.json")


def dsn(environment="PEACH_SENTRY_DSN", key="dsn"):
    value = os.environ.get(environment, "").strip()
    if not value:
        from .settings_file import PROJECT_ROOT
        value = _read(PROJECT_ROOT / "sentry.json").get(key, "")
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
        # 托管 Sentry 的公开 DSN；不接受任意网络目标或带私钥的旧 DSN。
        if (parsed.scheme == "https" and parsed.hostname and re.fullmatch(r"[a-z0-9-]+\.ingest(?:\.(?:us|de))?\.sentry\.io", parsed.hostname)
                and parsed.username and not parsed.password and not parsed.query and not parsed.fragment
                and parsed.path[1:].isdigit() and parsed.port in (None, 443)):
            return value
    except ValueError:
        pass
    return ""


def snapshot(config=None):
    return {**{key: value for key, value in consent(config).items() if key != "revision"},
            "available": bool(dsn()), "fields": FIELDS, "notice": NOTICE}


def parse_choices(body, *, partial=False):
    if not isinstance(body, dict):
        raise ValueError("遥测和自动错误诊断必须是布尔值")
    defaults = consent() if partial else {}
    choices = {key: body.get(key, defaults.get(key)) for key in FIELDS}
    if any(type(value) is not bool for value in choices.values()):
        raise ValueError("遥测和自动错误诊断必须是布尔值")
    if choices["usage"] != choices["errors"]:
        raise ValueError("版本统计与错误诊断须同时开启或关闭")
    return choices


def save(body, config=None):
    payload = parse_choices(body)
    from .fsutil import atomic_write_text
    config = _config(config)
    payload["revision"] = uuid.uuid4().hex
    atomic_write_text(config.directory("state") / "remote-reporting.json", json.dumps(payload))
    from . import diagnostic_frequency
    diagnostic_frequency.clear(config)
    return snapshot(config)


def _reserve(config, kind, event):
    """跨托盘与子服务共享每日上限；额度文件只存指纹。"""
    from filelock import FileLock
    from .fsutil import atomic_write_text
    path = config.directory("state") / "remote-reporting-budget.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=.1):
        day = datetime.now(timezone.utc).date().isoformat()
        value = _read(path)
        value = value if value.get("day") == day else {"day": day}
        seen = value.get(kind, [])
        if not isinstance(seen, list):
            return False
        fingerprint = hashlib.sha256(json.dumps({key: value for key, value in event.items()
                                                if key != "event_id"}, sort_keys=True).encode()).hexdigest()
        if fingerprint in seen or len(seen) >= (1 if kind == "usage" else 20):
            return False
        value[kind] = [*seen, fingerprint]
        atomic_write_text(path, json.dumps(value))
        return True


def _new_client(target, *, envelope_gate=None):
    from sentry_sdk import Client
    from sentry_sdk.transport import HttpTransport

    class ConsentTransport(HttpTransport):
        TIMEOUT = 5

        def _send_envelope(self, envelope):
            if _disabled:
                return
            if envelope_gate is not None:
                if envelope_gate(envelope):
                    super()._send_envelope(envelope)
                return
            # SDK 后台队列真正发送前复查同意版本，跨进程关闭也生效。
            for item in envelope.items:
                event = item.get_event()
                if event is None:
                    return
                permit = event.pop("extra", {})
                try:
                    current = _consent(Path(permit["consent"]))
                    if not all(current.get(key) is True for key in FIELDS) or current["revision"] != permit["revision"]:
                        return
                except Exception:
                    return
            super()._send_envelope(envelope)

    def final_event(event, _hint):
        # SDK 的服务器名、上下文、SDK 包清单和全局 scope 均不进入发送载荷。
        return {key: value for key, value in event.items() if key in {
            "event_id", "timestamp", "platform", "release", "level", "message", "tags", "exception", "extra",
        }}

    return Client(dsn=target, transport=ConsentTransport, default_integrations=False,
                  auto_enabling_integrations=False, auto_session_tracking=False,
                  send_default_pii=False, include_local_variables=False, include_source_context=False,
                  send_client_reports=False, enable_backpressure_handling=False,
                  enable_logs=False, enable_metrics=False, traces_sample_rate=0,
                  profiles_sample_rate=0, transport_queue_size=10, shutdown_timeout=0,
                  before_send=final_event)


def _submit(kind, event, config, current, target):
    global _client, _client_dsn
    with _lock:
        if _client is None or _client_dsn != target:
            if _client is not None:
                _client.close(timeout=0)
            _client, _client_dsn = _new_client(target), target
        return _client.capture_event({**event, "platform": "python",
            "extra": {"consent": str(config.directory("state") / "remote-reporting.json"),
                      "kind": kind, "revision": current["revision"]}})


def _schedule_frequency(config):
    global _frequency_timer
    with _lock:
        _frequency_configs[str(config.directory("state"))] = config
        if _frequency_timer is None:
            _frequency_timer = threading.Timer(60, _frequency_tick)
            _frequency_timer.daemon = True
            _frequency_timer.start()


def _frequency_tick():
    global _frequency_timer
    with _lock:
        _frequency_timer = None
        configs = list(_frequency_configs.values())
    for config in configs:
        flush_counts(config)
    with _lock:
        if _frequency_configs and not _disabled:
            _schedule_frequency(next(iter(_frequency_configs.values())))


def flush_counts(config=None, *, force=False):
    try:
        from . import diagnostic_frequency
        config = _config(config)
        current, target = consent(config), dsn()
        if _disabled or not current["errors"] or not target:
            return
        diagnostic_frequency.flush(config, current["revision"],
            lambda event: _submit("errors", event, config, current, target), force=force)
    except Exception:
        pass


def send(kind, event, config=None):
    """发送失败不影响应用；技术诊断按同意版本聚合，关闭时不初始化 SDK。"""
    if _disabled:
        return
    try:
        config = _config(config)
        current = consent(config)
        target = dsn()
        if kind not in FIELDS or not current[kind] or not target:
            return
        event = {**event, "release": __version__}
        if kind == "errors":
            from . import diagnostic_frequency
            diagnostic_frequency.record(config, current["revision"], event,
                lambda batch: _submit(kind, batch, config, current, target))
            _schedule_frequency(config)
        elif _reserve(config, kind, event):
            _submit(kind, event, config, current, target)
    except Exception:
        # 不能把遥测自身故障写回错误钩子而递归发送。
        pass


def usage(config=None):
    send("usage", {"message": "Peach started", "level": "info", "tags": {"os": platform.system()}}, config)
    flush_counts(config)
    try:
        if not _disabled and consent(config)["errors"] and dsn():
            _schedule_frequency(_config(config))
    except Exception:
        pass


def _http_status(error):
    status = getattr(error, "status_code", None)
    if type(status) is not int or not 100 <= status <= 599:
        status = getattr(getattr(error, "response", None), "status_code", None)
    if type(status) is not int and any(cls.__module__ == "urllib.error" and cls.__name__ == "HTTPError"
                                      for cls in type(error).__mro__):
        status = getattr(error, "code", None)
    return status if type(status) is int and 100 <= status <= 599 else None


def _exception_details(error):
    frames, codes, chain = [], {}, []
    current, visited = error, set()
    while current is not None and id(current) not in visited and len(chain) < 8:
        visited.add(id(current))
        chain.append(type(current).__name__)
        for key in ("errno", "winerror", "returncode"):
            value = getattr(current, key, None)
            if type(value) is int:
                codes[key] = value
        if (status := _http_status(current)) is not None:
            codes["status_code"] = status
        tb = current.__traceback__
        while tb is not None and len(frames) < 40:
            filename = Path(tb.tb_frame.f_code.co_filename)
            if filename.parent.name == "peach":
                frames.append({"filename": filename.name, "lineno": tb.tb_lineno,
                               "function": tb.tb_frame.f_code.co_name})
            tb = tb.tb_next
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    return frames, codes, chain


def _media_details(error, fields, codes):
    detail = getattr(error, "diagnostics", {}) if error is not None else {}
    detail = detail if isinstance(detail, dict) else {}
    if type(detail.get("returncode")) is int:
        codes["returncode"] = detail["returncode"]
    raw = str(fields.get("stderr", "")) + str(detail.get("stderr", ""))
    for marker, code in (("Your platform doesn't support hardware accelerated AV1 decoding", "av1_decoder_unavailable"),
                         ("Unknown decoder", "decoder_unavailable"),
                         ("Invalid data found", "invalid_media"), ("Permission denied", "permission_denied"),
                         ("No space left", "disk_full"), ("No such file or directory", "missing_media")):
        if marker in raw:
            return code
    return ""


def _expected_failure(stage, error, fields):
    if stage == "logging" and fields.get("level", "ERROR") not in {"ERROR", "CRITICAL"}:
        return True
    if stage == "browser" and fields.get("error_name") == "AbortError":
        return True
    kinds = {(cls.__module__, cls.__name__) for cls in type(error).__mro__}
    if kinds & LOCAL_EXCEPTION_TYPES:
        return True
    if ("peach.sources.base", "SourceFailure") in kinds:
        return str(error.reason) in {"cloudflare_challenge", "ip_banned", "geo_restricted", "auth_required",
            "not_found", "gone", "no_usable_metadata", "rate_limited", "timeout", "server_error", "network"}
    if ("peach.metadata", "MetadataProviderError") in kinds and error.detail != "parse_error":
        return error.kind in {"auth", "not_found"} or error.retryable is True or error.temporary is True
    if stage in MEDIA_STAGES:
        detail = getattr(error, "diagnostics", {})
        if isinstance(error, subprocess.TimeoutExpired) or isinstance(detail, dict) and detail.get("reason") == "timeout":
            return True
        if isinstance(error, OSError) and error.errno == errno.ENOSPC:
            return True
        if isinstance(error, (FileNotFoundError, PermissionError, IsADirectoryError, NotADirectoryError)):
            filename = getattr(error, "filename", None)
            if filename and Path(filename).name != Path(str(fields.get("tool", ""))).name:
                return True
    return False


def _actionable(stage, error, fields):
    """仅允许技术故障入口；预期领域状态和 SDK 自身日志留在本机。"""
    if str(fields.get("logger", "")).startswith(("sentry_sdk", "urllib3")):
        return False
    if stage == "unhandled-asyncio":
        from .error_reporting import is_asyncio_connection_cleanup
        if is_asyncio_connection_cleanup(error, fields):
            return False
    stages = {"browser", "http-request", "unhandled-main", "unhandled-thread", "unraisable",
              "unhandled-asyncio", "cli", "packaged-cli", "desktop-entry", "tray-import",
              "background-job", "library-preview", "frame-capture", "frame-tile", "media-probe",
              "preview", "standalone-update-install", "standalone-update-rollback", "logging"}
    if stage not in stages or isinstance(error, (SystemExit, KeyboardInterrupt)):
        return False
    if _expected_failure(stage, error, fields):
        return False
    if stage == "http-request" and fields.get("status_code", 500) < 500:
        return False
    if (status := _http_status(error)) is not None and 400 <= status < 500:
        return False
    return True


def _http_details(stage, fields):
    tags, error_id = {}, ""
    if stage != "http-request":
        return tags, error_id
    if fields.get("method") in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
        tags["http_method"] = fields["method"]
    route = fields.get("route", "")
    if isinstance(route, str) and re.fullmatch(r"[a-zA-Z0-9_/{}/:-]{1,180}", route):
        tags["route_template"] = route
    candidate = fields.get("error_id", "")
    if isinstance(candidate, str) and re.fullmatch(r"[a-f0-9]{32}", candidate):
        error_id = candidate
    return tags, error_id


def _browser_frames(stack):
    frames = []
    pattern = r"(peach-(?:app|pages)\.js)(?:\?[^\s):]*)?:(\d+):(\d+)"
    for line in str(stack).splitlines()[:80]:
        if "chrome-extension://" in line or "moz-extension://" in line:
            continue
        for name, row, column in re.findall(pattern, line):
            frames.append({"filename": name, "lineno": int(row), "colno": int(column)})
    return frames[:20]


def capture(stage, error=None, **fields):
    """只投影代码位置和错误类别，不发送异常文本或日志文本。"""
    if not _actionable(stage, error, fields):
        return
    media_stage = stage in MEDIA_STAGES
    if error is None and stage != "browser" and not media_stage:
        return
    frames, codes, chain = _exception_details(error)
    if error is None and stage == "browser":
        frames = _browser_frames(fields.get("stack", ""))
        if not frames:
            return
    if stage == "logging" and not frames:
        return
    for key in ("returncode", "status_code"):
        if type(fields.get(key)) is int:
            codes[key] = fields[key]
    reason = ""
    if media_stage:
        reason = _media_details(error, fields, codes)
        if reason in {"invalid_media", "permission_denied", "disk_full", "missing_media"}:
            return
        if not reason and not codes and error is None:
            return
    category = type(error).__name__ if error is not None else ("BrowserError" if stage == "browser" else "LogError")
    http, error_id = _http_details(stage, fields)
    event = {"level": "error", "tags": {"os": platform.system(),
        "python": platform.python_version(), "architecture": platform.machine(), "stage": stage,
        **http, **{key: str(value) for key, value in codes.items()}, **({"media_failure": reason} if reason else {})},
        "exception": {"values": [{"type": category, "value": " / ".join(chain) or reason or category,
                                    "stacktrace": {"frames": frames}}]}}
    if error_id:
        event["event_id"] = error_id
    send("errors", event)


def disable_for_process():
    """本进程停止远程发送，保留本机选择。"""
    global _disabled
    _disabled = True
    close()


def close():
    global _client, _client_dsn, _frequency_timer
    with _lock:
        if _frequency_timer is not None:
            _frequency_timer.cancel()
        _frequency_timer = None
        _frequency_configs.clear()
        if _client is not None:
            _client.close(timeout=0)
        _client, _client_dsn = None, ""


def _shutdown():
    try:
        with _lock:
            configs = list(_frequency_configs.values())
        for config in configs:
            flush_counts(config, force=True)
        with _lock:
            if _client is not None:
                _client.flush(timeout=1)
            close()
    except Exception:
        pass


atexit.register(_shutdown)
