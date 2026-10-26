"""云下载的 Web 一侧：装配服务、任务列表与提交取消、设置页的保存与检查。

任务列表与提交挂在 `web_router` 的 `/api/downloads*` 下；设置页那几条（含凭据）在
`routes_configuration`，只放行本机连接。凭据值不出现在任何响应里，只报存没存过。
"""
from __future__ import annotations

from pathlib import Path

from .downloads import (
    CLOUDDRIVE_CREDENTIAL, MAX_WAIT_HOURS, PIKPAK_CREDENTIAL, PROVIDER_LABELS, DownloadError,
    DownloadService, Landing, OfflineProvider, clean_address, normalise_target,
    submit_offline_download,
)
from .follow_secrets import CredentialError, CredentialStore
from .repository import LedgerDatabase


def _values(credentials: CredentialStore, provider: str) -> dict:
    try:
        credential = credentials.load(provider)
    except CredentialError as error:
        raise DownloadError("config", f"本机凭据文件读不出来，请在设置页重新填写：{error}") from None
    return dict(credential.values) if credential else {}


def build_download_service(*, database: LedgerDatabase, state_root: Path,
                           credentials: CredentialStore, push_discovery,
                           available: bool, after_ingest=None) -> DownloadService:
    """按 provider 键现取客户端，地址与凭据在设置页改了立刻生效。

    PikPak 浏览器登录挂在 `service.pikpak_browser` 上：profile 放在凭据根下 `browser-pikpak/`，
    登录完成后叫醒对账线程。
    """
    from .downloads_clouddrive import CloudDriveProvider
    from .downloads_pikpak import PikPakProvider
    from .downloads_pikpak_browser import BrowserLogin, profile_dir

    holder: dict[str, DownloadService] = {}

    def providers(key: str) -> OfflineProvider:
        if key == "115":
            token = _values(credentials, CLOUDDRIVE_CREDENTIAL).get("token", "")
            return CloudDriveProvider(holder["service"].config.clouddrive_address, token)
        if key == "pikpak":
            return PikPakProvider(lambda: _values(credentials, PIKPAK_CREDENTIAL),
                                  persist=lambda values: credentials.save(PIKPAK_CREDENTIAL, values))
        raise DownloadError("config", f"不认识的云下载渠道：{key}")

    service = DownloadService(database=database, state_root=state_root, credentials=credentials,
                              providers=providers, landing=Landing(push_discovery),
                              available=available, after_ingest=after_ingest)
    holder["service"] = service
    service.pikpak_browser = BrowserLogin(
        profile_dir(Path(credentials.root).parent),
        persist=lambda values: credentials.save(PIKPAK_CREDENTIAL, values), on_done=service.wake)
    return service


def _service(contract) -> DownloadService:
    service = getattr(contract, "downloads", None)
    if service is None:
        raise ValueError("云下载没有启用")
    return service


# ---------------------------------------------------------------- 任务列表与提交


def q_downloads(contract, args) -> dict:
    try:
        limit = int(args.get("limit") or 100)
    except (TypeError, ValueError):
        limit = 100
    return _service(contract).snapshot(limit)


def w_download_submit(contract, body) -> dict:
    if not isinstance(body, dict):
        raise ValueError("请求体必须是一个对象")
    return submit_offline_download(
        _service(contract), magnet=str(body.get("magnet") or ""),
        provider=str(body.get("provider") or ""), target=body.get("target") or None,
        code=body.get("code"), title=body.get("title"), origin=body.get("origin") or "paste")


def w_download_cancel(contract, body) -> dict:
    if not isinstance(body, dict) or not str(body.get("id") or "").isdigit():
        raise ValueError("缺少任务编号")
    task = _service(contract).cancel(int(body["id"]))
    return {"ok": True, "task": task.payload()}


def w_download_postprocess(contract, body) -> dict:
    if not isinstance(body, dict) or not str(body.get("id") or "").isdigit():
        raise ValueError("缺少任务编号")
    task = _service(contract).retry_postprocess(int(body["id"]))
    return {"ok": True, "task": task.payload()}


# ---------------------------------------------------------------- 设置页


def settings_payload(service: DownloadService, declared_roots) -> dict:
    """设置页那一块的形状。读接口和每条写接口都回这一份，页面整块替换本地状态。"""
    from .downloads_pikpak import account
    credentials = service.credentials
    try:
        pikpak = account(_values(credentials, PIKPAK_CREDENTIAL))
    except DownloadError:
        pikpak = account(None)
    login = _browser_login(service)
    return {
        "available": service.available,
        "config": service.config.payload(),
        "token_set": "token" in credentials.describe(CLOUDDRIVE_CREDENTIAL)["fields"],
        "pikpak": pikpak,
        "pikpak_browser": {"available": login is not None and login.available(),
                           **(login.status() if login is not None else {"state": "idle", "message": ""})},
        "pikpak_roots": list(declared_roots.get("pikpak", ())),
        "providers": [{"key": key, "label": label} for key, label in PROVIDER_LABELS.items()],
        "max_wait_hours": MAX_WAIT_HOURS,
    }


def save_settings(service: DownloadService, body: dict, declared_roots) -> dict:
    """保存地址、目录与等待上限。令牌留空表示不改，`clear_token` 才撤掉。

    存好之后有令牌就按已保存的配置检查一遍，报告放在 `report` 里随响应回去。115 目标目录
    留空时检查按推送发现推一个目录，CloudDrive2 里已经有它就填进设置再存一次；没有它就只
    在报告里给出建议值，其他字段照常保存。没有令牌时 `report` 为 None。
    """
    if not isinstance(body, dict):
        raise ValueError("设置必须是一个对象")
    token = str(body.get("token") or "").strip()
    config = service.save_settings(body, declared_roots)
    if token:
        service.credentials.save(CLOUDDRIVE_CREDENTIAL, {"token": token})
    elif body.get("clear_token"):
        service.credentials.clear(CLOUDDRIVE_CREDENTIAL)
    report = None
    if "token" in service.credentials.describe(CLOUDDRIVE_CREDENTIAL)["fields"]:
        from .downloads_clouddrive import empty_report
        try:
            report = check_clouddrive(service, {
                "clouddrive_address": config.clouddrive_address,
                "target": config.targets.get("115", ""), "pikpak_root": config.pikpak_root,
                "pikpak_account": body.get("pikpak_account") is True})
        except DownloadError as error:
            report = empty_report(config.clouddrive_address, [error.detail])
        suggested = report.get("suggested_target")
        if not config.targets.get("115") and suggested and suggested.get("exists"):
            filled = config.payload()
            filled["targets"] = {**config.targets, "115": suggested["path"]}
            service.save_settings(filled, declared_roots)
    return {**settings_payload(service, declared_roots), "report": report}


def _check_inputs(service: DownloadService, body) -> tuple[dict, str, str]:
    body = body if isinstance(body, dict) else {}
    address = clean_address(body["clouddrive_address"] if "clouddrive_address" in body
                            else service.config.clouddrive_address)
    token = str(body.get("token") or "").strip() or _values(
        service.credentials, CLOUDDRIVE_CREDENTIAL).get("token", "")
    return body, address, token


def _hints(service: DownloadService, body: dict):
    """使用推送发现的前缀表生成目录建议，与下载完成后的路径转换使用相同配置。

    PikPak 算在用：本机存着 PikPak 登录令牌，或页面说用户已在账号框里填了账号。
    """
    from .downloads_clouddrive import Hints
    landing = service.landing
    push = getattr(landing, "push_discovery", None)
    prefixes = tuple(getattr(getattr(push, "config", None), "prefixes", ()) or ())
    pikpak_root = str(body["pikpak_root"] if "pikpak_root" in body else service.config.pikpak_root)
    try:
        logged_in = "refresh_token" in service.credentials.describe(PIKPAK_CREDENTIAL)["fields"]
    except CredentialError:
        logged_in = False
    return Hints(prefixes=prefixes, declared_roots=dict(landing.declared_roots or {}),
                 pikpak_root=pikpak_root.strip(),
                 pikpak_account=body.get("pikpak_account") is True or logged_in)


def check_clouddrive(service: DownloadService, body: dict) -> dict:
    """「检查」按页面上此刻填的值查，没带的字段取已保存的；空对象就是按已保存的配置查。
    只读，不提交、不取消、不建目录。

    地址是空串时探测本机端口；目标目录或 PikPak 根是空串时按推送发现推建议值。
    报告里的 `address` 与 `suggested_*` 由页面填回表单，不在这里保存。
    """
    from .downloads_clouddrive import check, empty_report
    body, address, token = _check_inputs(service, body)
    raw_target = str(body.get("target") if "target" in body
                     else service.config.targets.get("115", "")).strip()
    target = normalise_target(raw_target) if raw_target else ""
    try:
        return check(address, token, target, hints=_hints(service, body))
    except DownloadError as error:
        return empty_report(address, [error.detail])


def create_clouddrive_folder(service: DownloadService, body: dict) -> dict:
    """「新建这个目录」：用户点了才在 CloudDrive2 里建，建好后回对这个目录的检查报告。"""
    from .downloads_clouddrive import create_folder
    body, address, token = _check_inputs(service, body)
    path = normalise_target(str(body.get("path") or ""))
    try:
        return create_folder(address, token, path, hints=_hints(service, body))
    except DownloadError as error:
        raise ValueError(error.detail) from None


def pikpak_login(service: DownloadService, body: dict, declared_roots, *, transport=None) -> dict:
    """登录成功回设置块；要人机验证时回验证页地址，`ok` 为假。"""
    from .downloads_pikpak import CaptchaRequired, login
    body = body if isinstance(body, dict) else {}
    credentials = service.credentials
    try:
        login(lambda: _values(credentials, PIKPAK_CREDENTIAL), str(body.get("username") or ""),
              str(body.get("password") or ""), remember=bool(body.get("remember")),
              persist=lambda values: credentials.save(PIKPAK_CREDENTIAL, values),
              transport=transport)
    except CaptchaRequired as error:
        return {"ok": False, "captcha_url": error.url, "message": error.detail,
                "settings": settings_payload(service, declared_roots)}
    except DownloadError as error:
        raise ValueError(error.detail) from None
    service.wake()
    return {"ok": True, "settings": settings_payload(service, declared_roots)}


def pikpak_logout(service: DownloadService, declared_roots) -> dict:
    service.credentials.clear(PIKPAK_CREDENTIAL)
    return settings_payload(service, declared_roots)


def _browser_login(service: DownloadService):
    return getattr(service, "pikpak_browser", None)


def pikpak_browser_start(service: DownloadService, declared_roots) -> dict:
    """拉起登录窗口，立刻回设置块；页面看 `pikpak_browser.state` 轮询，不在这里等。"""
    if not service.available:
        raise ValueError("云下载只在允许修改数据库的设备上可用")
    login = _browser_login(service)
    if login is None:
        raise ValueError("浏览器登录没有启用")
    login.start()
    return settings_payload(service, declared_roots)


def pikpak_browser_cancel(service: DownloadService, declared_roots) -> dict:
    login = _browser_login(service)
    if login is not None:
        login.cancel()
    return settings_payload(service, declared_roots)
