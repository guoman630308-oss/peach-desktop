"""采集设置与有界来源连接检查；凭据只写本机。"""
import io
import hashlib
import json
import time
import httpx

from PIL import Image

from .http import HttpRequest
from .scraping_access import SOURCES, SourcePaused, SourceTransport, describe, save, record_source_check

#: 连不上 DMM 图片主机时给的那句去处：2026-09 实测中国移动宽带直连大多在握手后被断开，
#: 电信、联通直连正常，所以不是站点挂了，换条线路就好。
MOBILE_BROADBAND_HINT = "中国移动宽带直连 DMM 图片主机常在握手后被断开，请把 DMM / FANZA 的连接方式设成 Peach 代理。"


def _kept_cover_size(target, incoming, incoming_size, installed, previous):
    """本机封面不比来源这张差时回它的尺寸；该换、或本机还没有封面时回 `None`。

    本机那张的图片角色按装它时的来源认：边车记着来源地址就按它，否则按采集日志里
    上一次成功的那条。比较口径与批处理脚本同一个 `candidate_improves`。
    """
    from .jav_cover_fetch import candidate_for, candidate_improves, candidate_quality
    if not target.is_file():
        return None
    if isinstance(installed, dict) and installed.get("source_url"):
        current = candidate_for(str(installed["source_url"]))
    else:
        current = previous[0] if previous is not None else None
    with Image.open(target) as image:
        current_size = image.size
    if candidate_improves(
            incoming, incoming_size[0] * incoming_size[1],
            candidate_quality(current), current_size[0] * current_size[1]):
        return None
    return current_size


def w_scraping_cover(contract, body):
    """仅处理用户指定且馆藏命中的番号；完整解码后才允许升级封面。"""
    from .catalog_rules import is_korean_mib_code
    from .jav_cover_fetch import MIB_NOT_JAV
    from .metadata import validate_provider_code
    code = validate_provider_code(str(body.get("code", "")))
    if is_korean_mib_code(code):
        raise ValueError(MIB_NOT_JAV)
    with contract.database.read_connection() as connection:
        if not connection.execute("SELECT 1 FROM asset WHERE code=? LIMIT 1", (code,)).fetchone():
            raise ValueError("馆藏未找到这个番号")
    return contract.scraping_cover_job.start_result(lambda: _fetch_cover(contract, code), recovery={'code': code})


def _unavailable_reason(exc, network_failed: bool, statuses: set[int]) -> tuple[str, str]:
    """一次封面比较没取到图时，页面上那一格的原因键与说法：先看线路，再看来源回了什么。"""
    from .jav_cover_fetch import (CoverConnectError, HOSTS_UNREACHABLE, NO_USABLE_OFFICIAL,
                                 OFFICIAL_PLACEHOLDER_ONLY)
    if isinstance(exc, CoverConnectError):
        return "network", f"{HOSTS_UNREACHABLE}，未能完成封面比较；{MOBILE_BROADBAND_HINT}"
    if network_failed:
        return "network", "来源连接失败，未能完成封面比较；请检查来源连接设置。"
    if statuses & {401, 403}:
        codes = "/".join(str(s) for s in sorted(statuses & {401, 403}))
        return "access_denied", f"来源拒绝访问（HTTP {codes}），请检查登录或来源验证状态。"
    if any(s >= 500 for s in statuses):
        return "source_error", "来源服务异常（HTTP 5xx），请稍后重试。"
    if str(exc) == "所有渠道都没有候选":
        return "no_candidate", "来源没有返回这个番号的封面候选，无法判断是否有高清版。"
    return {
        OFFICIAL_PLACEHOLDER_ONLY: ("placeholder_only", f"{OFFICIAL_PLACEHOLDER_ONLY}。"),
        **dict.fromkeys(NO_USABLE_OFFICIAL, (
            "unusable_candidate", "候选封面未通过检查：图片未取得、无法解码或宽度不足 700px。")),
    }.get(str(exc), ("download_failed", "候选封面完整下载或图片校验失败，未取得可保存的图片。"))


def _fetch_cover(contract, code):
    from .cover_artwork import install_cover
    from .jav_cover_fetch import (best_cover, HostLimitedTransport, Unavailable,
                                 fc2_cover_candidates, logged_success_evidence)
    from .review_csv import read_rows
    target = contract.cover_root / (code + ".jpg")
    sidecar = target.with_suffix(".scraping.json")
    try:
        installed = json.loads(sidecar.read_text(encoding="utf-8"))
        if (time.time() - float(installed["checked_at"]) < 86400 and target.is_file()
                and hashlib.sha256(target.read_bytes()).hexdigest() == installed["raw_sha256"]):
            return {"ok": True, "code": code, "result": f"复用 24 小时内核验过的本机封面（{installed['width']} × {installed['height']}），没有再向来源请求。", **{
                key: installed[key] for key in ("width", "height", "raw_sha256")}}
    except (OSError, ValueError, KeyError, TypeError):
        installed = None
    raw = SourceTransport(contract.follow_secrets_root, max_requests=80,
                          max_bytes=32 * 1024 * 1024, max_seconds=180)
    transport = HostLimitedTransport(raw, 1.5)
    statuses = set()
    diagnostics = {}
    network_failed = False

    def observed(request, timeout, limit):
        nonlocal network_failed
        try:
            response = transport(request, timeout, limit)
        except httpx.TransportError:
            network_failed = True
            raise
        statuses.add(response.status)
        return response

    try:
        fc2 = fc2_cover_candidates(contract.candidate_root / "fc2-candidate-log.csv").get(code)
        log = contract.candidate_root / "cover-fetch-log.csv"
        previous = logged_success_evidence(read_rows(log), code) if log.is_file() else None
        prior = tuple(item for item in (fc2, previous[0] if previous else None) if item)
        candidate, size, data = best_cover(observed, code, 0, diagnostics=diagnostics,
                                          prior_candidates=prior,
                                          metadata_root=contract.follow_sources_root / "metadata" / "javinizer-go")
        kept_size = _kept_cover_size(target, candidate, size, installed, previous)
        if kept_size is not None:
            suffix = "部分来源连接失败，未能完成全部来源比较。" if network_failed or any(s >= 400 and s != 404 for s in statuses) else "没有找到更大或更合适的封面。"
            return {"ok": True, "code": code, "reason": "kept_existing",
                    "result": f"本机封面 {kept_size[0]} × {kept_size[1]}，来源可用封面 {size[0]} × {size[1]}；保留本机封面。{suffix}"}
        evidence = {"code": code, "width": size[0], "height": size[1],
                    "source": candidate.source, "source_url": candidate.url,
                    "raw_sha256": hashlib.sha256(data).hexdigest(),
                    "installed_sha256": hashlib.sha256(data).hexdigest(),
                    "checked_at": time.time(), "resolver": "peach-jav-cover-v1"}
        install_cover(target, code, data, size, evidence=evidence)
        return {"ok": True, "code": code, "result": "高清封面已保存", "width": size[0],
                "height": size[1], "requests": raw.requests, "bytes": raw.bytes}
    except Unavailable as exc:
        reason, message = _unavailable_reason(exc, network_failed, statuses)
        labels = {"placeholder": "「准备中」占位图", "probe_failed": "图片头请求失败", "invalid_image": "图片无法解码",
                  "too_small": "图片宽度不足 700px", "download_failed": "完整下载失败",
                  "dimension_mismatch": "完整图片尺寸与探测结果不一致"}
        details = "；".join(f"{label} {diagnostics[key]} 张" for key, label in labels.items() if diagnostics.get(key))
        if details:
            message = ("候选封面未通过检查。" if reason == "unusable_candidate" else message) + details + "。"
        return {"ok": False, "code": code, "reason": reason, "error": message + "已有图片保留。"}
    except SourcePaused as exc:
        return {"ok": False, "code": code, "reason": "paused", "error": str(exc)}
    except httpx.TransportError:
        return {"ok": False, "code": code, "reason": "network", "error": "来源连接失败，请检查来源连接设置；已有图片保留。"}
    except OSError:
        return {"ok": False, "code": code, "reason": "local_file", "error": "本机封面读取或保存失败，请检查封面目录权限与磁盘空间。"}
    except Exception as exc:
        return {"ok": False, "code": code, "reason": "processing_error", "error": f"封面处理失败（{type(exc).__name__}），未能完成比较；请查看服务日志。",
                "error_type": type(exc).__name__}
    finally:
        transport.close()


def q_scraping(contract, _args):
    return {"sources": [describe(contract.follow_secrets_root, source) for source in SOURCES
                        if source != "instagram"],
            "instagram_status": "Instagram 自动头像定位尚待独立登录会话验证"}


def w_scraping_settings(contract, body):
    return {"ok": True, "saved": save(contract.follow_secrets_root, str(body.get("source", "")), body)}


def _connection_failure(exc, label):
    """来源连接异常对应可操作的诊断说明。"""
    if isinstance(exc, SourcePaused):
        detail, message = "cooldown", "来源正在冷却，请稍后重试。"
    elif isinstance(exc, httpx.ProxyError):
        detail, message = "proxy", "代理连接失败，请检查 Peach 代理。"
    elif isinstance(exc, httpx.TimeoutException):
        detail, message = "timeout", "来源连接超时，请稍后重试或检查连接方式。"
    else:
        detail = "network"
        message = (f"连接未取得；{MOBILE_BROADBAND_HINT}" if label == "高清图片 CDN"
                   else "连接未取得；此来源可能需要代理，请检查来源连接方式。")
    return {"ok": False, "kind": "unavailable", "reason": detail,
            "message": message, "error_type": type(exc).__name__}


def _connection_message(detail, reason):
    """来源响应的失败原因决定用户下一步操作。"""
    from .metadata import reason_blames_credentials

    if detail == "cloudflare_challenge":
        return "来源返回人机验证页，请检查来源验证状态或更换出口。"
    if detail == "rate_limited":
        return "来源正在限流，请稍后重试。"
    if detail == "not_found":
        return "来源检查地址不存在，请核对站点地址。"
    if reason_blames_credentials(reason):
        return "来源要求登录或验证，请在官网完成登录后重试。"
    return f"{reason}。" if reason else "来源暂不可用，请稍后重试。"


def w_scraping_check(contract, body):
    from .metadata import auth_wall_reason
    source = str(body.get("source", ""))
    if source not in SOURCES:
        raise ValueError("未知采集来源")
    if not SOURCES[source]["login"]:
        return {"ok": True, "results": [{"label": "来源页面", "ok": False, "kind": "unavailable",
                "reason": "needs_code", "message": "片商官网按番号选择，请输入馆藏番号检查高清封面。"}],
                "session_verified": False}
    targets = [("来源页面", SOURCES[source]["login"])]
    if source == "dmm":
        targets.append(("高清图片 CDN", "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/gyan00017/gyan00017pl.jpg"))
    results = []
    transport = SourceTransport(contract.follow_secrets_root)
    try:
        for label, url in targets:
            result = {"label": label, "ok": False}
            started = time.monotonic()
            try:
                response = transport(HttpRequest("GET", url, {"Range": "bytes=0-65535"}), 10, 65536)
                # 鉴权失败与「站点挂了」是两件事，页面上要分得开：前者要人去官网登录
                # 或换一份 Cookie，后者只要等。判据和元数据来源共用一份；重定向终点
                # 只在真的跳转过时才算数，否则配置里本身就指向登录页的来源会自判失败。
                reason = auth_wall_reason(
                    status_code=response.status, body=response.body,
                    final_url=response.url if response.url and response.url != url else "")
                result.update(status=response.status, bytes=len(response.body),
                              ok=response.status in {200, 206} and not reason,
                              kind="ok" if response.status in {200, 206} and not reason
                              else "auth" if reason else "unavailable")
                from .browser_transport import CHALLENGE_MARKS
                challenge = any(mark.lower() in response.body.decode("utf-8", errors="replace").lower()
                                for mark in CHALLENGE_MARKS)
                detail = ("cloudflare_challenge" if challenge else "auth_required" if reason
                          else "ok" if result["ok"] else "rate_limited" if response.status == 429
                          else "server_error" if response.status >= 500
                          else "not_found" if response.status == 404 else "http_error")
                result["reason"] = detail
                if challenge:
                    result.update(ok=False, kind="auth")
                if label == "高清图片 CDN" and result["ok"]:
                    with Image.open(io.BytesIO(response.body)) as image:
                        result["width"], result["height"] = image.size
                if not result["ok"]:
                    # 成因锁死在凭据上才让人去登录。只剩状态码可看的 403 直接把
                    # `auth_wall_reason` 那句原样给用户：出口 IP 被封时登录是白做，
                    # 页面上再手写一份「请去登录」等于把人引去做错事。
                    result["message"] = _connection_message(detail, reason)
            except Exception as exc:
                result.update(_connection_failure(exc, label))
            result.update(checked_at=time.time(), elapsed_s=round(time.monotonic() - started, 2))
            if label == "来源页面":
                record_source_check(contract.follow_secrets_root, source, "connection", result)
            results.append(result)
    finally:
        transport.close()
    return {"ok": True, "results": results, "session_verified": False}


def q_scraping_amane_bridge(contract, args):
    """amane 桥那张卡：钉的 sha、锁里的版本、venv 建没建、开了哪几站，外加重建任务的快照。"""
    from . import metadata_amane
    return {**metadata_amane.describe(contract.tools_root),
            "job": contract.amane_bridge_job.snapshot() or {"status": "idle"}}


def w_scraping_amane_check(contract, body):
    """只读 GitHub API 问上游最新 release。取不到就是「未取得」；升不升级由人读 diff 决定。"""
    from . import metadata_amane, peach_proxy
    latest = metadata_amane.latest_upstream_tag(peach_proxy.client_options(contract.follow_secrets_root))
    return {"ok": True, "latest": latest, "checked_at": time.time()}


def w_scraping_amane_rebuild(contract, body):
    """按锁重建桥的 venv。首次要下载约 98 MB，放后台跑，页面轮询 `q_scraping_amane_bridge` 那条读接口。"""
    from . import metadata_amane
    return contract.amane_bridge_job.start_result(lambda: metadata_amane.rebuild(contract.tools_root), recovery={})
