"""口令闸门与登录页。

401 有三种形态，按路由类分组保留（不许统一成一种）：页面路由跳登录页，页面资产
返回 PlainText 提示，API 与媒体路由返回 JSON。三个 `require_*` 是唯一的判定入口，
路由里不要自己比对口令；对应的响应形态由 `api.py` 的异常处理器给出。

口令从 `request.app.state.settings` 取，不是闭包捕获：路由挂在模块级的 `APIRouter`
上，import 期就定型了，那时还没有 settings。
"""
from __future__ import annotations

import hmac
import ipaddress
from collections import OrderedDict
import threading
import time
from urllib.parse import urlsplit
from . import access, tunnel
from .web_entry import page_shell
from starlette.concurrency import run_in_threadpool
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

router = APIRouter()
_ATTEMPTS: OrderedDict[str, list[float]] = OrderedDict()
_ATTEMPTS_LOCK = threading.Lock()


def _attempt_key(request: Request) -> str:
    """限速按真实来源分桶。

    隧道在跑时所有请求的连接源都是 cloudflared 的回环地址，按它计数等于把整个公网
    合成一个桶：外面一台机器试满十次，本机浏览器也跟着被锁一分钟。这时改用边缘给出
    的客户端地址，隧道没在跑就仍按连接源，避免任何人自带一个头就换一个桶。
    """
    if tunnel.running(getattr(request.app.state, "tunnel", None)):
        forwarded = (request.headers.get("cf-connecting-ip") or "").strip()
        if forwarded:
            return f"cf:{forwarded}"
    return request.client.host if request.client else "unknown"


def _login_attempt(request: Request) -> None:
    """按连接来源限制密码尝试，保留有界的短期计数。"""
    peer = _attempt_key(request)
    now = time.monotonic()
    with _ATTEMPTS_LOCK:
        attempts = [stamp for stamp in _ATTEMPTS.pop(peer, []) if now - stamp < 60]
        _ATTEMPTS[peer] = attempts
        if len(attempts) >= 10:
            raise HTTPException(429, "尝试次数较多，请一分钟后重试", headers={"Retry-After": "60"})
        attempts.append(now)
        while len(_ATTEMPTS) > 1024:
            _ATTEMPTS.popitem(last=False)


def _policy(request: Request) -> dict:
    policy = access.load(request.app.state.settings.access_path)
    if policy["mode"] == "legacy":
        policy["key"] = request.app.state.settings.token
    return policy


def _first_query_values(request: Request) -> dict[str, str]:
    """兼容 urllib.parse.parse_qs：取首值并忽略空值。"""
    result: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if key not in result and value != "":
            result[key] = value
    return result


def _authorized(request: Request, token: str, args: dict[str, str]) -> bool:
    if not token:
        return True
    supplied = args.get("t") or request.headers.get("X-Token")
    if supplied is not None and hmac.compare_digest(str(supplied).encode(), token.encode()):
        return True
    policy = _policy(request)
    if policy["mode"] == "open":
        return True
    if policy["mode"] == "legacy":
        if hmac.compare_digest(request.cookies.get("tok", "").encode(), token.encode()):
            return True
    return access.valid_session(policy, request.cookies.get(access.COOKIE, ""))


class PageLoginRequired(Exception):
    """页面路由未授权：401 形态是跳登录页，不是响应体。"""

    def __init__(self, next_path: str):
        self.next_path = next_path


class AssetLoginRequired(Exception):
    """页面脚本与样式未授权：401 形态是 PlainText 提示。"""


def _origin_key(value: str) -> tuple[str, str, int | None] | None:
    """把 Origin 头和隧道 URL 归一到可比较的三元组。"""
    try:
        parsed = urlsplit(value.strip().rstrip("/"))
    except ValueError:
        return None
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    return parsed.scheme.lower(), parsed.hostname.rstrip(".").lower(), port


def _tunnel_origin(request: Request) -> tuple[str, str, int | None] | None:
    """返回当前进程实际拿到的隧道 origin，而不是信任请求头。"""
    manager = getattr(request.app.state, "tunnel", None)
    if manager is None:
        return None
    try:
        url = manager.snapshot().url
    except (AttributeError, OSError, ValueError):
        return None
    return _origin_key(url) if url else None


def _named_origin(request: Request) -> tuple[str, str, int | None] | None:
    """命名隧道的公开主机名。

    它来自本次启动注入的运行设置，不是请求头，也不随隧道握手完成才出现：连接刚建立
    那几秒页面就已经能提交，这时 manager 的快照还没进入 running。
    """
    settings = getattr(request.app.state, "settings", None)
    if getattr(settings, "tunnel_mode", "") != tunnel.NAMED_MODE:
        return None
    hostname = (getattr(settings, "tunnel_hostname", "") or "").strip()
    return _origin_key(tunnel.public_url(hostname)) if hostname else None


def same_origin(request: Request) -> None:
    """浏览器写请求只接受 Peach 页面或当前隧道页面发来的 Origin。

    独立包的 origin 是回环 HTTP，cloudflared 转发时 Host 仍可能是本机地址，
    所以不能单纯把 ``Origin`` 和 ASGI 的 ``base_url`` 比较。临时链接的随机入口由当前
    TunnelManager 产生并保存在进程内，命名隧道的公开主机名来自本次启动的运行设置；
    外部 Origin 必须精确匹配这两者之一，不能由调用方通过 ``X-Forwarded-*`` 自己
    声明一个可信地址。
    """
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "请从 Peach 配置页提交")
    if origin:
        supplied = _origin_key(origin)
        local = _origin_key(str(request.base_url))
        allowed = {local, _tunnel_origin(request), _named_origin(request)}
        if supplied is None or supplied not in allowed:
            raise HTTPException(403, "请从 Peach 配置页提交")


def require_auth(request: Request) -> dict[str, str]:
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        same_origin(request)
    args = _first_query_values(request)
    if not _authorized(request, request.app.state.settings.token, args):
        raise HTTPException(status_code=401, detail="unauthorized")
    return args


def require_loopback_client(request: Request) -> None:
    """启动本机程序只接受回环地址，拒绝代理转发与外部主机名。"""
    try:
        local = bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
    except ValueError:
        local = False
    forwarded = any(key.startswith("x-forwarded-") or key in {"forwarded", "cf-connecting-ip"}
                    for key in request.headers)
    if not local or forwarded or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(403, "请在运行 Peach 的电脑上操作")


def require_page_auth(request: Request) -> dict[str, str]:
    args = _first_query_values(request)
    if not _authorized(request, request.app.state.settings.token, args):
        raise PageLoginRequired(request.url.path or "/")
    return args


def require_authenticated_client(request: Request) -> None:
    """错误上报须有有效登录凭据，开放访问模式不提供匿名上报。"""
    same_origin(request)
    policy = _policy(request)
    if policy['mode'] not in {'password', 'legacy'} or not policy.get('key'):
        raise HTTPException(401, 'unauthorized')
    authenticated = access.valid_session(policy, request.cookies.get(access.COOKIE, ''))
    if policy['mode'] == 'legacy':
        authenticated = _authorized(request, policy['key'], _first_query_values(request))
    if not authenticated:
        raise HTTPException(401, 'unauthorized')


def require_asset_auth(request: Request) -> dict[str, str]:
    args = _first_query_values(request)
    if not _authorized(request, request.app.state.settings.token, args):
        raise AssetLoginRequired()
    return args


def _query_token_matches(request: Request) -> bool:
    token = request.app.state.settings.token
    supplied = _first_query_values(request).get("t", "")
    return bool(token) and hmac.compare_digest(supplied.encode(), token.encode())


def query_auth_redirect(request: Request) -> str | None:
    """为已授权页面清除 URL 口令，保留原始编码、重复参数与播放时间。"""
    if not _query_token_matches(request):
        return None
    token = request.app.state.settings.token
    pairs = request.scope.get("query_string", b"").decode("latin-1").split("&")
    retained = [pair for pair in pairs if parse_qs(pair).get("t") != [token]]
    query = "&".join(retained)
    return (request.url.path or "/") + (f"?{query}" if query else "")


def set_auth_cookie(response: Response, request: Request, *, days: int = 30, login: bool = False) -> None:
    token = request.app.state.settings.token
    policy = _policy(request)
    response.headers["Cache-Control"] = "no-store"
    if token and policy["mode"] in {"password", "legacy"} and (login or _query_token_matches(request)):
        value, max_age = access.issue(policy, days)
        response.set_cookie(access.COOKIE, value, max_age=max_age, path="/", httponly=True,
                            samesite="lax", secure=request.url.scheme == "https")
        response.delete_cookie("tok", path="/")


def login_html(next_path: str, *, invalid: bool = False, error: str = "") -> str:
    """登录页的薄壳：表单由页面包画，服务端只给净化过的 `next`、口令是否错误与限流或参数错误的原因。"""
    data = {"next": next_path}
    if invalid:
        data["invalid"] = "true"
    if error:
        data["error"] = error
    return page_shell("登录 Peach", "login", data)


def _safe_next(value: str) -> str:
    """登录后去哪：只认站内路径，`//` 开头的协议相对地址会跳出本站。"""
    return value if value.startswith("/") and not value.startswith("//") else "/"


def _wants_page(request: Request) -> bool:
    """浏览器表单提交的 `Accept` 里有 `text/html`，出错时回登录页并原位报错；其余调用方回 JSON。"""
    return "text/html" in request.headers.get("accept", "")


@router.get("/login", response_class=HTMLResponse)
def login(request: Request, next: str = "/"):
    next_path = _safe_next(next)
    if _authorized(request, request.app.state.settings.token,
                   _first_query_values(request)):
        return RedirectResponse(next_path, status_code=303)
    return HTMLResponse(login_html(next_path))


@router.post("/login")
async def login_submit(request: Request):
    same_origin(request)
    form = parse_qs(
        (await request.body()).decode("utf-8", "replace"), keep_blank_values=True,
    )
    next_path = _safe_next((form.get("next") or ["/"])[0])
    try:
        _login_attempt(request)
    except HTTPException as exc:
        if not _wants_page(request):
            raise
        return HTMLResponse(login_html(next_path, error=str(exc.detail)), status_code=exc.status_code,
                            headers=exc.headers)
    token = request.app.state.settings.token
    supplied = (form.get("token") or [""])[0]
    policy = access.load(request.app.state.settings.access_path)
    if token and policy["mode"] != "open" and not await run_in_threadpool(access.verify, policy, supplied, token):
        return HTMLResponse(login_html(next_path, invalid=True), status_code=401)
    try:
        days = int((form.get("days") or ["0"])[0])
        if not 0 <= days <= 365:
            raise ValueError
    except ValueError:
        message = "请选择有效的保持登录时间"
        if _wants_page(request):
            return HTMLResponse(login_html(next_path, error=message), status_code=400)
        raise HTTPException(400, message) from None
    response = RedirectResponse(next_path, status_code=303)
    set_auth_cookie(response, request, days=days, login=True)
    return response
