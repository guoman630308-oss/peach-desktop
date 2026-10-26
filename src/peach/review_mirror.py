"""把 writer 的复核队列安全镜像给 reader。

候选 CSV 仍只由 writer 生成和解释；reader 只缓存 writer 已归一化的 JSON 契约，绝不据此
批准候选或写 ledger。这样 `/review` 能在 Mac 上浏览，同时不把 `generated`、SQLite/WAL 或
整个 `peach-data` 变成双向同步目录。
"""
from __future__ import annotations

import ipaddress
import json
import logging
import ssl
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from .fsutil import atomic_write_text


MAX_REVIEW_BYTES = 8 * 1024 * 1024
REVIEW_CACHE_SECONDS = 20.0
LOGGER = logging.getLogger(__name__)


class ReviewMirrorError(RuntimeError):
    """writer 镜像不可用或响应不符合复核契约。"""


class ReviewMirror:
    def __init__(
        self,
        origin: str,
        ca_path: Path,
        cache_path: Path,
        *,
        token: str = "",
        timeout: float = 5.0,
        now=time.time,
        opener=None,
        keychain_paths: tuple[Path, ...] | None = None,
        curl_fallback: bool | None = None,
        curl_runner=None,
        proxy: str = "",
    ):
        self.origin = origin.rstrip("/")
        self.ca_path = Path(ca_path)
        self.cache_path = Path(cache_path)
        self.token = token
        self.timeout = timeout
        self._now = now
        self._opener = opener
        if keychain_paths is None:
            keychain_paths = (
                Path("/Library/Keychains/System.keychain"),
                Path.home() / "Library/Keychains/login.keychain-db",
            ) if sys.platform == "darwin" else ()
        self.keychain_paths = tuple(Path(path) for path in keychain_paths)
        self.curl_fallback = sys.platform == "darwin" if curl_fallback is None else curl_fallback
        self._curl_runner = curl_runner or subprocess.run
        self.proxy = self._loopback_proxy(proxy)
        self._live_payload: dict | None = None
        self._live_at = 0.0

    @property
    def enabled(self) -> bool:
        parsed = urlparse(self.origin)
        return bool(parsed.scheme == "https" and parsed.netloc and self.ca_path.is_file())

    def resolve(self, local_payload: dict) -> dict:
        """优先返回 writer 实时队列；失败时退回上次安全缓存或本机队列。"""
        if not self.enabled:
            return self._annotate(local_payload, state="unavailable",
                                  error="未配置可严格校验的写入端 HTTPS")
        try:
            payload = self._fetch_cached_live()
            return self._annotate(payload, state="live")
        except ReviewMirrorError as error:
            cached = self._read_cache()
            if cached is not None:
                return self._annotate(cached["payload"], state="cached",
                                      fetched_at=cached["fetched_at"], error=str(error))
            return self._annotate(local_payload, state="unavailable", error=str(error))

    def _fetch_cached_live(self) -> dict:
        now = self._now()
        if self._live_payload is not None and now - self._live_at < REVIEW_CACHE_SECONDS:
            return deepcopy(self._live_payload)
        health = self._request_json("/healthz")
        if health.get("ledger_sync") != "writer":
            raise ReviewMirrorError("目标实例当前不是 ledger writer")
        payload = self._request_json("/api/review")
        self._validate(payload)
        rewritten = self._rewrite_preview_urls(payload)
        fetched_at = datetime.fromtimestamp(now, timezone.utc).isoformat()
        try:
            self._write_cache(rewritten, fetched_at)
        except OSError as error:
            # 缓存只是 writer 暂时离线时的降级，落盘失败不能把刚取得的实时队列也变成 500。
            LOGGER.warning("复核镜像缓存写入失败：%s", error)
        self._live_payload = rewritten
        self._live_at = now
        return deepcopy(rewritten)

    def _request_json(self, path: str) -> dict:
        request = Request(urljoin(self.origin + "/", path.lstrip("/")))
        if self.token:
            request.add_header("X-Token", self.token)
        try:
            response = self._client().open(request, timeout=self.timeout)
            raw = response.read(MAX_REVIEW_BYTES + 1)
        except Exception as error:
            if not self.curl_fallback:
                raise ReviewMirrorError(
                    f"无法读取写入端：{type(error).__name__}: {error}",
                ) from error
            try:
                raw = self._curl_bytes(request.full_url)
            except ReviewMirrorError as curl_error:
                raise ReviewMirrorError(
                    f"无法读取写入端：{type(error).__name__}: {error}；"
                    f"系统网络通道：{curl_error}",
                ) from error
        if len(raw) > MAX_REVIEW_BYTES:
            raise ReviewMirrorError("写入端复核响应超过 8 MiB 上限")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReviewMirrorError("写入端返回的不是有效 JSON") from error
        if not isinstance(payload, dict):
            raise ReviewMirrorError("写入端返回的不是 JSON 对象")
        return payload

    def _curl_bytes(self, url: str) -> bytes:
        """macOS LaunchAgent 无本地网络权限时，借系统 curl 的已授权主体发起同一严格请求。"""
        args = [
            "/usr/bin/curl", "--fail", "--silent", "--show-error",
            "--proto", "=https", "--tlsv1.2",
            "--cacert", str(self.ca_path),
            "--connect-timeout", str(self.timeout),
            "--max-time", str(self.timeout),
            "--max-filesize", str(MAX_REVIEW_BYTES),
        ]
        if self.proxy:
            args.extend(["--proxy", self.proxy, "--noproxy", ""])
        else:
            args.extend(["--noproxy", "*"])
        header = None
        if self.token:
            args.extend(["--header", "@-"])
            header = f"X-Token: {self.token}\n".encode("utf-8")
        args.append(url)
        try:
            result = self._curl_runner(
                args, input=header, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=self.timeout + 2, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ReviewMirrorError(f"curl 启动失败：{type(error).__name__}: {error}") from error
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise ReviewMirrorError(f"curl 退出 {result.returncode}：{detail or '未取得错误详情'}")
        return bytes(result.stdout)

    @staticmethod
    def _loopback_proxy(value: str) -> str:
        parsed = urlparse(value)
        if (parsed.scheme not in {"http", "https", "socks5", "socks5h"}
                or not parsed.hostname or parsed.username is not None):
            return ""
        try:
            port = parsed.port
        except ValueError:
            return ""
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname == "localhost"
        return value if loopback and port is not None else ""

    def _client(self):
        if self._opener is not None:
            return self._opener
        context = ssl.create_default_context(cadata=self._trusted_ca_pem())
        # `.local`/LAN 请求不能继承外网代理；Stash fake-IP 会把 peer 解析成 198.18.x。
        self._opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
        return self._opener

    def _trusted_ca_pem(self) -> str:
        """合并文件 CA 与 macOS 钥匙串中同名的受信 CA，不读取或导出任何私钥。"""
        bundle = self.ca_path.read_text(encoding="utf-8")
        for keychain in self.keychain_paths:
            if not keychain.is_file():
                continue
            try:
                result = subprocess.run(
                    [
                        "/usr/bin/security", "find-certificate", "-a", "-p",
                        "-c", "Peach Local CA", str(keychain),
                    ],
                    capture_output=True, text=True, encoding="utf-8",
                    timeout=3, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode == 0 and "-----BEGIN CERTIFICATE-----" in result.stdout:
                bundle += "\n" + result.stdout
        return bundle

    @staticmethod
    def _validate(payload: dict) -> None:
        for key in ("sections", "sources", "counts"):
            if not isinstance(payload.get(key), dict):
                raise ReviewMirrorError(f"写入端复核响应缺少 {key}")

    def _rewrite_preview_urls(self, payload: dict) -> dict:
        result = deepcopy(payload)

        def visit(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if (key in {"preview_url", "asset_preview_url"}
                            and isinstance(child, str) and child.startswith("/")):
                        value[key] = self.origin + child
                    else:
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(result)
        return result

    def _write_cache(self, payload: dict, fetched_at: str) -> None:
        # 只给自己读：这份缓存里是复核队列的完整内容，不该跟着数据目录的默认权限走。
        atomic_write_text(self.cache_path, json.dumps(
            {"fetched_at": fetched_at, "payload": payload}, ensure_ascii=False,
        ), mode=0o600)

    def _read_cache(self) -> dict | None:
        try:
            envelope = json.loads(self.cache_path.read_text(encoding="utf-8"))
            payload = envelope["payload"]
            fetched_at = str(envelope["fetched_at"])
            self._validate(payload)
        except (OSError, KeyError, TypeError, json.JSONDecodeError, ReviewMirrorError):
            return None
        return {"fetched_at": fetched_at, "payload": payload}

    def _annotate(
        self,
        payload: dict,
        *,
        state: str,
        fetched_at: str | None = None,
        error: str = "",
    ) -> dict:
        result = deepcopy(payload)
        result["mirror"] = {
            "state": state,
            "origin": self.origin or None,
            "fetched_at": fetched_at,
            "error": error or None,
            "read_only": True,
        }
        return result
