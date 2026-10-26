#!/usr/bin/env python3
"""按番号批量取多来源资料，产出逐字段的 Peach 复核候选。

走的是采集任务那条正式链（ADR-0044）：一个番号问谁、什么顺序、何时停由
`peach.metadata_routes` 决定，来源解析器与凭据、限流、冷却都复用
`peach.library_processing.LibraryMetadataProvider`。这里只做批量：按账本番号排队、
逐来源落原始快照、统计来源健康、把取值排成字段候选 CSV。
CSV 是复核队列；本命令没有写 ledger 的模式。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from contextlib import ExitStack
import json
import os
import random
import re
import sqlite3
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach import __version__ as PEACH_VERSION
from peach import metadata_routes
from peach.catalog_rules import (RELEASE_EVIDENCE_KINDS, code_query_variants, is_jav_code,
                                 normalise_code_key, same_release_code)
from peach.scripting import HostLimiter, open_readonly
from peach.config import DATABASE_PATH, GENERATED_DIR, LOG_DIR, SECRETS_DIR, SOURCES_DIR, TOOLS_DIR
from peach.genre_decisions import load_genre_decisions
from peach.genre_taxonomy import CONTENT_GENRES, map_genres
from peach.jobs import DiskGuard, JobPolicyError
from peach.task_runs import cli_run
from peach.metadata import (
    CATALOG_EVIDENCE_FIELDS,
    MetadataProviderError,
    auth_error,
    auth_wall_reason,
    extract_catalog_evidence,
    extract_peach_fields,
    identifies_code,
)
from peach.metadata_policy import (PEACH_FIELDS, POLICY_VERSION, SOURCE_SPECS, field_rank,
                                   preferred_tag_source, sort_candidates)
from peach.platform import system_volume
from peach.review_csv import ENCODING, read_rows, write_rows
from peach.fsutil import atomic_write_text
from peach.sources import Session
from peach.sources.seesaa import SEESAA, WIKI_SOURCES as WIKI_SITES, WikiPages


_logf = None
FIELD_LABELS = {
    "title": "标题", "original_title": "原标题",
    "performers": "演员", "studio": "厂牌", "series": "系列",
    "release_date": "发行日期", "tags": "内容标签",
}
FIELDS = [
    "item_key", "code", "query", "field", "field_label", "current_value",
    "candidates_json", "source_count", "source_profile", "policy_version",
    "status", "size_gb", "videos", "fetched_at",
]
ERROR_FIELDS = ["code", "query", "source", "kind", "status_code", "retryable", "message"]
UNMAPPED_FIELDS = ["genre", "source", "occurrences", "sample_code"]
HEALTH_FIELDS = [
    "source", "profile", "attempted", "snapshot_reused", "fetched", "succeeded",
    "empty", "errors", "retryable_errors", "cooldown_skips", "auth_skips",
    "blocked", "elapsed_ms",
    *dict.fromkeys((*PEACH_FIELDS, *CATALOG_EVIDENCE_FIELDS)),
    "last_error_kind", "last_error_status", "last_error_message",
]

#: Seesaa 作品表那一档，`--profile seesaa` 只问它。
WIKI_SOURCE = SEESAA.name
#: Seesaa 上的几个 Wiki（`sources.seesaa.WIKI_SOURCES`）。它们不在任何内容类型的链上
#: （`metadata_routes.ROUTES`），只由 `--profile seesaa` 或 `--sources` 点名，各自一档，经契约问。
WIKI_SOURCES = tuple(WIKI_SITES)
#: `--sources` 能点名的来源：链上每一档的成员，加 Seesaa 的几个 Wiki。别的名字在 `SOURCE_SPECS` 里
#: 只是历史来源身份（`metadata_policy.HISTORICAL_SOURCES`），当前没有解析器可问。
CHAIN_SOURCES = tuple(dict.fromkeys((
    *(source for chain in metadata_routes.ROUTES.values() for source in chain),
    *metadata_routes.AMANE_OFFICIAL_STAGE, *metadata_routes.AMANE_STAGE,
)))
QUERYABLE_SOURCES = (*CHAIN_SOURCES, *WIKI_SOURCES)
#: 三种取来源的方式，写进 CSV 的 `source_profile`：按番号内容类型走链、只问 Seesaa、
#: `--sources` 点名。
CHAIN_PROFILE, WIKI_PROFILE, CUSTOM_PROFILE = "chain", "seesaa", "custom"


def configure_log(log_dir: str | Path) -> None:
    global _logf
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    _logf = (path / time.strftime("metadata-candidates-%Y%m%d-%H%M%S.log")).open(
        "w", encoding="utf-8", buffering=1,
    )


def log(message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    if _logf is not None:
        _logf.write(line + "\n")


def close_log() -> None:
    global _logf
    if _logf is not None:
        _logf.close()
        _logf = None


def _is_explicit_code(code: str, *, release_evidence: bool = False) -> bool:
    r"""番号是否明确到可以直接查来源。

    原始写法本身就是番号形态的直接放行。缺分隔符的写法（`PBD390`、`IPVR00296`）要规范化
    之后才像番号，而账本里同样长相的还有建库前导入时写进 `code` 的目录名：论坛合集包
    `WX17`、创作者账号 `RAIKUN325`、`BANBI_555`、创作者自编号 `DTW003`。这一类只有在
    厂牌、发行日或出演者／片商／系列实体里有一样时才算番号，口径与 `is_jav_asset` 相同。
    2026-09-25 只读盘点：这种写法 31 个，全部没有发行证据，全是目录名。

    `WX17` 被规范成 `WX-017` 发给 javbus，取回的是另一部片 `WXSD-017`，片名和标签在
    09-02 随整批复核落到了 266 个网红视频上。

    形态判定交给 `is_jav_code`，这里不再抄一份正则。抄出来的那份和它逐字相同，于是
    `HHD800`、`HJD2048` 这些转载站水印域名在 `catalog_rules` 侧被排除之后，这里还会
    继续把它们发给 provider 查——同一个「什么算番号」有两个答案。
    """
    if is_jav_code(code):
        return True
    return release_evidence and is_jav_code(normalise_code_key(code))


def translate_failure(source: str, error: Exception) -> MetadataProviderError:
    """把正式链抛的异常翻成本脚本的错误分档。

    链那一侧分四种：来源明确说没有（`NotFound`，或契约 `not_found` 那一档的 `SourceFailure`）可冻成
    定论，`SourcePaused` 是限流或冷却（可重试、临时），`DeadlineExceeded` 是预算用尽，其余
    `Unavailable`、别档的 `SourceFailure` 与传输错误是这次没问到。401/403 单列成 `auth`：
    `SourceTransport` 撞上 403 会自己把来源停下并抛 `SourcePaused`，措辞里带着状态码，
    这里认出来后本批不再问它——限流会过去，凭据不会。
    """
    from peach.jav_cover_fetch import DeadlineExceeded
    from peach.library_processing import describe_failure, is_missing
    from peach.scraping_access import SourcePaused
    if isinstance(error, MetadataProviderError):
        return error
    text = describe_failure(error)
    status = re.search(r"\b(40[13]|429|503)\b", text)
    code = int(status.group(1)) if status else 0
    reason = auth_wall_reason(status_code=code) if code in {401, 403} else ""
    if reason:
        return auth_error(source, reason, status_code=code)
    if is_missing(error):
        return MetadataProviderError(text, kind="not_found", status_code=404)
    if isinstance(error, SourcePaused):
        return MetadataProviderError(text, kind="rate_limited", status_code=code or 429,
                                     retryable=True, temporary=True)
    if isinstance(error, DeadlineExceeded):
        return MetadataProviderError(text, kind="timeout", retryable=True, temporary=True)
    return MetadataProviderError(text, kind="unavailable", status_code=code,
                                 retryable=True, temporary=True)


class ChainAdapter:
    """把 `LibraryMetadataProvider` 的按档接口摊成 `fetch(code, stage, members)`。

    返回 `(取到的 {来源: 资料}, 失败的 {来源: 错误})`。一档一次问完是正式链的形状：FC2
    三处在同一次 `fc2()` 里先后问，amane 那几站一次子进程并发问；综合索引那一档这里逐家
    直接经契约问 `SITE_SOURCES` 里的站，不经 `community()` 的按番号缓存——本脚本要的
    是每家各自的结果与失败，用来记快照和健康，而那份缓存只记整档的结论。

    provider 按需才建：`--profile seesaa` 一次都不会碰到它。Seesaa 的每个 Wiki 一档，`wikis` 是
    `{来源名: (站, 会话)}`，会话的传输是带页缓存与请求限额的 `WikiPages`，整批共用。
    """

    def __init__(self, factory, wikis=None):
        self._factory = factory
        self._inner = None
        self.wikis = dict(wikis or {})

    @property
    def inner(self):
        if self._inner is None:
            self._inner = self._factory()
        return self._inner

    def fetch(self, code: str, stage: str, members: tuple[str, ...]):
        found: dict[str, dict] = {}
        if stage == "community":
            return self._community(code, members)
        try:
            if stage in ("r18dev", "dmm"):
                pairs = [(stage, self.inner.query(code, stage))]
            elif stage == "1pondo":
                pairs = self.inner.one_pondo(code)
            elif stage == "fc2":
                # `covers=True` 让链上点到的每一处都问，不在第一处答上时停：这里要的是
                # 每来源各自的证据，不是「这一档有没有答上」。
                pairs = self.inner.fc2(code, route=members, covers=True)
            elif stage in ("amane", "amane_official"):
                pairs = self.inner.amane(code, route=members)
            elif stage in self.wikis:
                wiki, session = self.wikis[stage]
                pairs = [(stage, wiki.query(code, session=session).payload())]
            else:
                raise ValueError(f"链上没有这一档：{stage}")
        except Exception as error:  # noqa: BLE001 - 每种失败都翻成本脚本的分档
            translated = translate_failure(stage, error)
            return found, {member: translated for member in members}
        for name, payload in pairs:
            if name in members:
                found.setdefault(name, payload)
        return found, {}

    def _community(self, code: str, members: tuple[str, ...]):
        from peach.community_catalog import community_sources_for
        found, errors = {}, {}
        for source in community_sources_for(code, route=members):
            try:
                found[source] = self.inner.site(source, code)
            except Exception as error:  # noqa: BLE001 - 同上
                errors[source] = translate_failure(source, error)
        return found, errors

    def close(self) -> None:
        if self._inner is not None:
            self._inner.close()
        for _, session in self.wikis.values():
            session.transport.close()


class PerSourceAdapter:
    """给只会 `query(code, source)` 的 provider（测试桩）套上同一个 `fetch` 形状。"""

    def __init__(self, provider):
        self.provider = provider

    def fetch(self, code: str, stage: str, members: tuple[str, ...]):
        found, errors = {}, {}
        for source in members:
            try:
                found[source] = self.provider.query(code, source)
            except MetadataProviderError as error:
                errors[source] = error
        return found, errors

    def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if close:
            close()


def _stage_results(adapter, *, query: str, stage: str, members: tuple[str, ...],
                   raw_dir: Path, refresh: bool, health: dict) -> dict:
    """一档内每个来源：优先复用快照，剩下的一次问完，失败也落盘。

    返回 `{来源: (资料或 None, 错误或 None, 是否复用快照)}`。一个来源既没资料也没
    错误（链答了别家、没答这家）按 `empty` 记：那不是「没有」的定论，下一轮照问。
    """
    results: dict[str, tuple] = {}
    pending: list[str] = []
    for source in members:
        snapshot = raw_dir / query / f"{source}.json"
        payload = None if refresh else _read_snapshot(snapshot, query)
        settled = None if refresh else _read_settled_error(snapshot)
        if payload is not None or settled is not None:
            health[source]["snapshot_reused"] += 1
            results[source] = (payload, settled, True)
        else:
            pending.append(source)
    if pending:
        found, errors = adapter.fetch(query, stage, tuple(pending))
        for source in pending:
            health[source]["fetched"] += 1
            payload, error = found.get(source), errors.get(source)
            if payload is None and error is None:
                error = MetadataProviderError("no result", kind="empty")
            _write_snapshot(raw_dir / query / f"{source}.json", code=query, source=source,
                            result=payload, error=error)
            results[source] = (payload, error, False)
    return results


def _identity_mismatch(query: str, payload: dict) -> MetadataProviderError | None:
    """来源返回的作品不是查的那一部时，拒收而不是当命中。

    javbus 一侧是拿番号做关键词搜索并取首个命中，所以搜不到原番号一定会返回别的
    东西：`CHU-101 -> CHUC-101`、`SA-104 -> AVSA-104`、`AR-301 -> STAR-3016`。
    2026-09-02 实测 `B:/MVP/MIB/` 下 68 次匹配有 58 次返回的番号根本不是查询的
    番号，整目录的厂牌、系列、标题因此全错。

    比对走 `same_release_code`，只接受已核验的前缀别名、补零和重制尾字母；
    来源没给 id 的不拦——那是缺证据，不是证据相反。
    """
    returned = str(payload.get("id") or payload.get("content_id") or "").strip()
    # MGStage 的完整商品编号可证明来源省略数字前缀的展示 id。
    # 仅读取商品详情路径，不把搜索参数、封面地址或相似标题当身份依据。
    url = urlsplit(str(payload.get("source_url") or ""))
    product = re.fullmatch(r"/product/product_detail/([^/]+)/?", url.path)
    if url.hostname in {"mgstage.com", "www.mgstage.com"} and product:
        product_code = product.group(1)
        if same_release_code(query, product_code) and (
            not returned or same_release_code(product_code, returned)
            or normalise_code_key(returned) in code_query_variants(product_code)
        ):
            return None
        return MetadataProviderError(
            f"MGStage 商品编号 {product_code} 与查询 {query} 或返回番号 {returned} 不一致",
            kind="identity_mismatch",
        )
    # DMM content_id 的厂牌段属于该来源的编码；只用于没有展示 id 的裸番号查询。
    if (not payload.get("id") and payload.get("content_id")
            and re.fullmatch(r"[A-Z]{2,8}-\d{2,5}", query)
            and url.hostname in {"r18.dev", "www.dmm.co.jp", "dmm.co.jp", "www.dmm.com", "dmm.com"}
            and identifies_code(query, payload)):
        return None
    if not returned or same_release_code(query, returned):
        return None
    return MetadataProviderError(
        f"来源返回的番号 {returned} 不是查询的 {query}",
        kind="identity_mismatch",
    )


def _candidate_key(code: str, field: str, source: str, value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{code}\0{field}\0{source}\0{canonical}".encode()).hexdigest()[:20]
    return f"{code}:{field}:{source}:{digest}"


def _current_values(connection: sqlite3.Connection, code: str, field: str) -> list[str]:
    title_columns = {"title": "catalog_title", "original_title": "original_title"}
    column = title_columns.get(field, field)
    if field in {"title", "original_title", "studio", "series", "release_date"}:
        if column not in {
            str(row[1]) for row in connection.execute("PRAGMA table_info(asset)")
        }:
            return []
        rows = connection.execute(
            f"SELECT DISTINCT trim({column}) FROM asset WHERE medium='video' "
            f"AND upper(trim(code))=upper(?) AND {column} IS NOT NULL AND trim({column})<>''",
            (code,),
        )
    elif field == "performers":
        rows = connection.execute(
            "SELECT DISTINCT e.canonical_name FROM asset a "
            "JOIN asset_entity ae ON ae.asset_id=a.id AND ae.role='performer' "
            "JOIN entity e ON e.id=ae.entity_id AND e.kind='performer' "
            "WHERE a.medium='video' AND upper(trim(a.code))=upper(?)",
            (code,),
        )
    else:
        allowed = sorted(set(CONTENT_GENRES.values()))
        marks = ",".join("?" * len(allowed))
        rows = connection.execute(
            "SELECT DISTINCT t.tag FROM asset a JOIN asset_tag t ON t.asset_id=a.id "
            f"WHERE a.medium='video' AND upper(trim(a.code))=upper(?) AND t.tag IN ({marks})",
            (code, *allowed),
        )
    return sorted({str(row[0]).strip() for row in rows if str(row[0] or "").strip()})


def _read_snapshot(path: Path, code: str) -> dict | None:
    """复用上一轮的成功记录，但先确认它和这个番号对得上。

    快照是在 `identifies_code` 之前落的盘，里面就有 dl.getchu 拿不相干同人商品
    当结果的记录。只看「有没有 result」而不看身份，等于把当初那次错配一路复用
    下去——封面域 2026-09-01 的跨片封套正是这么带到今天的。对不上就当没有快照，
    重新联网问一次，闸在 provider 那一侧会把它变成 not_found。
    """
    try:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
        result = wrapper.get("result")
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(result, dict) or not identifies_code(code, result):
        return None
    return result


#: 只有来源明确答「没有这部片」才算定论。
SETTLED_ERROR_KINDS = frozenset({"not_found"})

#: 鉴权失败这一档。它比冷却更早也更硬：冷却是「先歇 300 秒再说」，这里是「本批
#: 不再向这个来源发请求」。判据不同——限流会自己过去，凭据不会。2026-09-01 的补抓
#: 里，一把过期 Cookie 让同一个来源在 122 个番号上各撞一次墙，全部写成同一条错误。
AUTH_ERROR_KIND = "auth"

#: 连续多少次可重试失败才让来源进冷却，以及冷却多久（秒）。
#: 2026-09-01 的官方 tag 补抓实测：mgstage 在中途超时一次，旧逻辑当场把它
#: 「本批后续全部跳过」，剩下 122 个番号再也没被问过，dmm 同样丢了 150 个。
#: 一次超时是抖动不是封禁；真被限流会连续失败，那时再退让也来得及。冷却也
#: 必须会过期——长批次里一次抖动不该决定后面几百个番号的命运。
COOLDOWN_AFTER_FAILURES = 3
COOLDOWN_SECONDS = 300.0

#: 限流、封禁与站方过载的状态码。撞上它们换个写法只是再撞一次墙，也算进冷却计数。
BLOCKING_STATUS_CODES = frozenset({403, 429, 503})
#: Seesaa 的 Wiki 搜索范围内没找到（`sources.seesaa`）。可重试，但不算进冷却计数。
SEARCH_MISS_KIND = "incomplete_search"


def _read_settled_error(path: Path) -> MetadataProviderError | None:
    """复用确定失败，只让临时/可重试/结论不明的错误重新联网。"""
    try:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
        error = wrapper.get("error")
        if not isinstance(error, dict):
            return None
        if bool(error.get("retryable")) or bool(error.get("temporary")):
            return None
        if str(error.get("kind") or "") not in SETTLED_ERROR_KINDS:
            return None
        return MetadataProviderError(
            str(error.get("message") or "metadata source error"),
            kind=str(error.get("kind") or "unknown"),
            status_code=int(error.get("status_code") or 0),
            retryable=False,
            temporary=False,
        )
    except (OSError, ValueError, TypeError):
        return None


def _provider_name(source: str) -> str:
    """快照与候选里记的解析器名，和采集任务写的同一份（`library_processing.PROVIDER_NAMES`）。"""
    from peach.library_processing import PROVIDER_NAMES
    return PROVIDER_NAMES.get(source, source)


def _write_snapshot(path: Path, *, code: str, source: str, result: dict | None = None,
                    error: MetadataProviderError | None = None) -> None:
    """原始快照：`result` 的形状与采集任务的证据文件一致，`jav_cover_fetch` 离线复用它的
    `cover_url` 与 `content_id`。`provider` 记解析器名，`provider_version` 记 Peach 版本——
    事后判断「这条证据出自哪一版解析器」靠的是这两项。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    wrapper: dict[str, object] = {
        "provider": _provider_name(source), "provider_version": PEACH_VERSION,
        "code": code, "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    if result is not None:
        wrapper["result"] = result
    if error is not None:
        wrapper["error"] = {
            "kind": error.kind, "message": str(error), "status_code": error.status_code,
            "retryable": error.retryable, "temporary": error.temporary,
        }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(wrapper, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _default_output() -> Path:
    return GENERATED_DIR / time.strftime("metadata-field-candidates-%Y%m%d-%H%M%S.csv")


def parse_sources(raw: str) -> tuple[str, ...]:
    """`--sources` 的来源名：去重、保序，必须是链上能问的来源。"""
    sources = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    if not sources:
        raise ValueError("至少指定一个来源")
    unknown = [source for source in sources if source not in QUERYABLE_SOURCES]
    if unknown:
        historical = [source for source in unknown if source in SOURCE_SPECS]
        if historical:
            raise ValueError("这些来源只是历史来源身份，当前没有解析器可问：" + ", ".join(historical))
        raise ValueError("未知来源：" + ", ".join(unknown))
    return sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="fetch field-level metadata review candidates")
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--errors", type=Path, default=None)
    parser.add_argument("--raw-dir", type=Path, default=SOURCES_DIR / "metadata" / "javinizer-go",
                        help="原始快照目录；默认沿用历史快照所在的目录，离线复用与封面获取都读它")
    parser.add_argument("--log-dir", type=Path, default=LOG_DIR)
    parser.add_argument("--secrets-root", type=Path, default=SECRETS_DIR,
                        help="凭据根（采集设置里贴的 Cookie 与冷却记录都在这里）")
    parser.add_argument("--tools-root", type=Path, default=TOOLS_DIR,
                        help="工具区（amane 桥的 venv 在这里）")
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--profile", choices=(CHAIN_PROFILE, WIKI_PROFILE), default=CHAIN_PROFILE,
        help="chain：按番号内容类型走正式来源链（默认）；seesaa：只问 Seesaa 作品表")
    source_group.add_argument("--sources", help="逗号分隔的来源名，按给的顺序全部问，不短路")
    parser.add_argument("--health", type=Path, default=None)
    parser.add_argument("--unmapped", type=Path, default=None,
                        help="未收录 genre 清单；不写这个文件等于把来源给过的值悄悄丢掉")
    parser.add_argument(
        "--codes-file", type=Path,
        help="只处理文件中列出的番号；每行一个，空行和 # 注释忽略",
    )
    parser.add_argument(
        "--english-title-only", action="store_true",
        help="只处理已有非空英文标题、但没有日文标题的番号",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=1.2)
    parser.add_argument("--min-free", type=float, default=40.0,
                        help="系统盘最低可用 GiB；运行中每隔一段时间复查")
    parser.add_argument("--disk-check-secs", type=float, default=20.0)
    parser.add_argument("--refresh", action="store_true", help="ignore reusable raw snapshots")
    parser.add_argument('--resume', action=argparse.BooleanOptionalAction, default=True,
                        help='复用同一批次已保存的成功结果；--no-resume 开始新批次')
    parser.add_argument("--wiki-pages-file", type=Path, help="预取 Wiki 作品目录页，每行一个 URL")
    parser.add_argument("--wiki-max-requests", type=int, default=80,
                        help="每个 Wiki 本批 HTTP 请求上限；每个请求最多 4 MiB，同主机几个 Wiki 合计间隔至少 2 秒")
    return parser


def _requested_codes(path: Path) -> list[str]:
    requested: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding=ENCODING).splitlines():
        value = raw.split("#", 1)[0].strip()
        if not value:
            continue
        query = normalise_code_key(value)
        if query not in seen:
            seen.add(query)
            requested.append(query)
    return requested


def _select_requested_codes(codes: list[tuple], path: Path, *,
                            labels: set[str] = frozenset()) -> list[tuple]:
    """按番号文件挑出要问的番号；`labels` 是账本里有、但只是目录名的那几个规范键。"""
    requested = _requested_codes(path)
    withheld = [query for query in requested if query in labels]
    if withheld:
        preview = "、".join(withheld[:10])
        raise ValueError(f"番号文件含账本里当 code 存着的目录名，不是发行番号：{preview}"
                         "（缺分隔符，且没有厂牌、发行日或出演者证据）")
    available: dict[str, tuple] = {}
    for row in codes:
        query = normalise_code_key(row[0])
        previous = available.get(query)
        if previous is None:
            available[query] = row
        else:
            available[query] = (previous[0], previous[1] + row[1], previous[2] + row[2], *previous[3:])
    missing = [query for query in requested if query not in available]
    if missing:
        preview = "、".join(missing[:10])
        suffix = f" 等 {len(missing)} 个" if len(missing) > 10 else ""
        raise ValueError(f"番号文件含 ledger 中不存在的番号：{preview}{suffix}")
    return [available[query] for query in requested]


_JAPANESE_TEXT_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_LATIN_TEXT_RE = re.compile(r"[A-Za-z]")


def _select_english_title_codes(connection, codes: list[tuple]) -> list[tuple]:
    """Select codes whose recorded titles are Latin-only and have no Japanese alternative."""
    titles: dict[str, list[str]] = {}
    for code, catalog_title, original_title in connection.execute(
        "SELECT code,catalog_title,original_title FROM asset "
        "WHERE medium='video' AND code IS NOT NULL AND trim(code)<>''"
    ):
        key = normalise_code_key(str(code))
        titles.setdefault(key, []).extend(
            str(value).strip() for value in (catalog_title, original_title) if str(value or "").strip()
        )
    selected = []
    for row in codes:
        values = titles.get(normalise_code_key(row[0]), [])
        if (values and any(_LATIN_TEXT_RE.search(value) for value in values)
                and not any(_JAPANESE_TEXT_RE.search(value) for value in values)):
            selected.append(row)
    return selected


def _unmapped_output(output: Path) -> Path:
    name = output.name.replace("metadata-field-candidates-", "metadata-unmapped-genres-", 1)
    if name == output.name:
        name = output.stem + "-unmapped-genres.csv"
    return output.with_name(name)


def _write_unmapped(path: Path, seen: dict[tuple[str, str], list]) -> None:
    rows = [
        {"genre": genre, "source": source, "occurrences": count, "sample_code": sample}
        for (source, genre), (count, sample) in sorted(
            seen.items(), key=lambda item: (-item[1][0], item[0][0], item[0][1]),
        )
    ]
    write_rows(path, UNMAPPED_FIELDS, rows, atomic=True)


def _health_output(output: Path) -> Path:
    name = output.name.replace("metadata-field-candidates-", "metadata-source-health-", 1)
    if name == output.name:
        name = output.stem + "-health.csv"
    return output.with_name(name)


def _health_rows(sources, profile: str) -> dict[str, dict[str, object]]:
    return {source: {
        "source": source, "profile": profile, "attempted": 0,
        "snapshot_reused": 0, "fetched": 0, "succeeded": 0, "empty": 0,
        "errors": 0, "retryable_errors": 0, "cooldown_skips": 0, "auth_skips": 0,
        "blocked": 0, "elapsed_ms": 0,
        **{field: 0 for field in (*PEACH_FIELDS, *CATALOG_EVIDENCE_FIELDS)},
        "last_error_kind": "", "last_error_status": "", "last_error_message": "",
    } for source in sources}


def _write_health(path: Path, rows: dict[str, dict[str, object]]) -> None:
    write_rows(path, HEALTH_FIELDS, rows.values(), atomic=True)


def _load_codes(connection, args, parser) -> list[tuple]:
    """账本里要问的番号，每条 `(番号, 大小 GiB, 视频数, 路径, 文件名, 厂牌)`。

    后三项是路由要的本机证据（`metadata_routes.route_for_code`）：日期式番号只有路径、
    文件名或账本厂牌指着一本道时才问它。韩国 MIB 一律不问——JAV 目录站对这些番号只会
    返回别的作品，链上它是空的，`--sources` 点名也不放行。
    """
    kinds = ",".join("?" * len(RELEASE_EVIDENCE_KINDS))
    codes, labels = [], set()
    for row in connection.execute(
        "SELECT a.code,COALESCE(sum(a.size),0)/1073741824.0,count(*),max(a.path),max(a.name),"
        "max(a.studio),max(trim(COALESCE(a.studio,''))<>'' OR trim(COALESCE(a.release_date,''))<>'' "
        "OR EXISTS(SELECT 1 FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id "
        f"WHERE ae.asset_id=a.id AND e.kind IN ({kinds}))) "
        "FROM asset a WHERE a.medium='video' AND a.code IS NOT NULL AND trim(a.code)<>'' "
        "GROUP BY a.code ORDER BY 2 DESC", sorted(RELEASE_EVIDENCE_KINDS)
    ):
        code = str(row[0]).strip()
        if not _is_explicit_code(code, release_evidence=bool(row[6])):
            if is_jav_code(normalise_code_key(code)):
                labels.add(normalise_code_key(code))
            continue
        if metadata_routes.classify(normalise_code_key(code)) in ("kmib", "unknown"):
            continue
        codes.append((code, float(row[1]), int(row[2]), row[3], row[4], row[5]))
    if args.codes_file:
        try:
            codes = _select_requested_codes(codes, args.codes_file, labels=labels)
        except (OSError, UnicodeError, ValueError) as error:
            # `parser.error` 直接退出；日志和连接不先关，Windows 上这两个文件就删不掉。
            connection.close()
            close_log()
            parser.error(str(error))
    if args.english_title_only:
        codes = _select_english_title_codes(connection, codes)
    if args.limit:
        codes = codes[:max(args.limit, 0)]
    return codes


class _Throttle:
    """来源级的冷却与鉴权阻断，整批共享。"""

    def __init__(self, health: dict) -> None:
        self.health = health
        self.cooldown_until: dict[str, float] = {}
        self.consecutive_failures: dict[str, int] = {}
        #: 本批已判定鉴权失败的来源 → 第一条说明。进了这张表就不再对它发请求。
        self.auth_blocked: dict[str, str] = {}

    def open_members(self, members) -> tuple[str, ...]:
        """这一档里本轮还能问的来源；被挡的记进健康表。"""
        allowed = []
        for source in members:
            self.health[source]["attempted"] += 1
            if source in self.auth_blocked:
                self.health[source]["auth_skips"] += 1
            elif time.monotonic() < self.cooldown_until.get(source, 0.0):
                self.health[source]["cooldown_skips"] += 1
            else:
                allowed.append(source)
        return tuple(allowed)

    def record_failure(self, source: str, error: MetadataProviderError) -> None:
        row = self.health[source]
        row["errors"] += 1
        row["retryable_errors"] += int(error.retryable)
        row["last_error_kind"] = error.kind
        row["last_error_status"] = error.status_code or ""
        row["last_error_message"] = str(error)[:500]
        if source in WIKI_SOURCES and (error.kind == "budget" or error.status_code in {403, 429}):
            self.cooldown_until[source] = float("inf")
            row["blocked"] += 1
            log(f"{source} 本批联网停止；已取得的候选已保留")
        if error.kind == AUTH_ERROR_KIND:
            self.auth_blocked[source] = str(error)
            self.consecutive_failures[source] = 0
            row["blocked"] += 1
            # 下一步由错误消息自己带着：判据明确就说换凭据，只剩状态码
            # 可看的 403 则不替用户断成因。这里再补一句通用建议会盖掉那份
            # 区分，把撞上 IP 封禁的人引去反复换 Cookie。
            log(f"{source} 鉴权失败，本批不再向它发请求：{error}")
        elif error.kind == SEARCH_MISS_KIND:
            # 站答了，只是搜索范围内没有这部片：不是限流信号。FC2 排在前面的一批里连着几个
            # 没收录，就会把整站冷却掉，后面收录了的番号一个都问不到。
            self.consecutive_failures[source] = 0
        elif error.retryable or error.status_code in BLOCKING_STATUS_CODES:
            self.consecutive_failures[source] = self.consecutive_failures.get(source, 0) + 1
            if self.consecutive_failures[source] >= COOLDOWN_AFTER_FAILURES:
                self.cooldown_until[source] = time.monotonic() + COOLDOWN_SECONDS
                self.consecutive_failures[source] = 0
                row["blocked"] += 1
                log(f"{source} 连续 {COOLDOWN_AFTER_FAILURES} 次可重试失败，"
                    f"冷却 {COOLDOWN_SECONDS:.0f} 秒后自动恢复：{error}")
        else:
            self.consecutive_failures[source] = 0

    def record_success(self, source: str) -> None:
        self.consecutive_failures[source] = 0


def _ask_stage(adapter, *, query: str, variants, stage: str, members, args, health) -> tuple[dict, bool]:
    """一档按写法逐轮问：来源明确说没有或答了别的片，就换下一种写法再问一次。

    `259LUXU-1642` 与 `LUXU-1642` 是同一部作品的两种写法，来源站各只索引其中一种；
    限流、封禁、鉴权和网络抖动与写法无关，换个写法只是再撞一次墙，所以那几种不换。
    返回 `({来源: (资料, 错误, 命中的写法)}, 是否发过网络请求)`。
    """
    collected: dict[str, tuple] = {}
    pending = list(members)
    used_network = False
    for attempt in variants:
        started = time.perf_counter()
        results = _stage_results(adapter, query=attempt, stage=stage, members=tuple(pending),
                                 raw_dir=args.raw_dir, refresh=args.refresh, health=health)
        elapsed = round((time.perf_counter() - started) * 1000)
        pending = []
        for source, (payload, error, reused) in results.items():
            health[source]["elapsed_ms"] += elapsed
            used_network = used_network or not reused
            if payload is not None:
                error = _identity_mismatch(query, payload)
                payload = None if error is not None else payload
            if payload is not None and attempt != query:
                log(f"{query} 在 {source} 改用 {attempt} 命中")
            if payload is None and error is not None and not (
                    error.retryable or error.kind == AUTH_ERROR_KIND
                    or error.status_code in BLOCKING_STATUS_CODES):
                pending.append(source)
            collected[source] = (payload, error, attempt)
        if not pending:
            break
    return collected, used_network


def _candidates_from(payload: dict, *, query: str, source: str, profile: str, snapshot: Path,
                     genre_decisions, health: dict, unmapped_genres: dict, code: str) -> dict[str, dict]:
    """一份来源资料摊成各字段的候选，顺带记健康计数与未收录 genre。"""
    extracted_fields = extract_peach_fields(payload, genre_decisions)
    catalog_evidence = extract_catalog_evidence(payload)
    for genre in map_genres(payload.get("genres") or [], genre_decisions)[1]:
        entry = unmapped_genres.setdefault((source, genre), [0, code])
        entry[0] += 1
    row = health[source]
    row["succeeded"] += 1
    if not extracted_fields and not catalog_evidence:
        row["empty"] += 1
    for field in set(catalog_evidence) | set(extracted_fields):
        row[field] += 1
    spec = SOURCE_SPECS[source]
    return {field: {
        "candidate_key": _candidate_key(query, field, source, extracted["value"]),
        "source": source,
        "provider": _provider_name(source),
        "source_url": str(payload.get("source_url") or ""),
        "confidence": 0.9 if source == "r18dev" else 0.75,
        "profile": profile,
        "policy_version": POLICY_VERSION,
        "field_rank": field_rank(field, source),
        "source_kind": spec.kind,
        "official": spec.official,
        "provider_id": str(payload.get("id") or ""),
        "content_id": str(payload.get("content_id") or ""),
        "value": extracted["value"],
        "display_value": extracted["display_value"],
        "warnings": [*extracted["warnings"], *payload.get("source_warnings", [])],
        "catalog_evidence": catalog_evidence,
        "wiki_evidence": payload.get("wiki_evidence", {}),
        "raw_snapshot": str(snapshot),
    } for field, extracted in extracted_fields.items()}


def _chain_for(row, *, profile: str, sources) -> tuple[str, ...]:
    """这个番号问哪几家、什么顺序。点名的照单全问；否则按内容类型与本机证据取链。"""
    if sources is not None:
        return tuple(sources)
    if profile == WIKI_PROFILE:
        return (WIKI_SOURCE,)
    return metadata_routes.route_for_code(normalise_code_key(row[0]), *row[3:6])


def _build_adapter(args, sources, provider=None):
    """来源适配器：注入的 provider 优先（测试桩），否则按需建正式链的 provider 与 Wiki。"""
    if provider is not None:
        return provider if hasattr(provider, "fetch") else PerSourceAdapter(provider)
    wikis = {}
    names = [name for name in sources if name in WIKI_SITES]
    pages = ([line.strip() for line in args.wiki_pages_file.read_text(encoding=ENCODING).splitlines()
              if line.strip() and not line.lstrip().startswith("#")]
             if args.wiki_pages_file and names else [])
    stray = [url for url in pages if not any(url.startswith(WIKI_SITES[name].root) for name in names)]
    if stray:
        raise ValueError("预取页不属于本批点名的 Wiki：" + "、".join(stray[:3]))
    # 几个 Wiki 都在 seesaawiki.jp 上：共用一个主机间隔，合起来仍是每 2 秒至多一次请求。
    limiter = HostLimiter({}, default_interval=SEESAA.interval)
    for name in names:
        site = WIKI_SITES[name]
        wiki = site(pages=[url for url in pages if url.startswith(site.root)])
        wikis[name] = (wiki, Session(WikiPages(args.raw_dir / "seesaa-pages", config=site.DEFAULT, limiter=limiter,
                                               refresh=args.refresh, max_requests=max(0, args.wiki_max_requests))))

    def factory():
        from peach.library_processing import LibraryMetadataProvider
        return LibraryMetadataProvider(args.secrets_root, tools_root=args.tools_root)

    return ChainAdapter(factory, wikis)


def _collect_code_fields(*, code, query, variants, chain, profile, sources, adapter, args,
                         health, throttle, genre_decisions, unmapped_genres,
                         error_writer, error_handle):
    """一个番号逐档取证并汇总字段；标量齐全后只补优先标签来源，返回候选与失败次数。"""
    by_field: dict[str, list[dict]] = {}
    given: set[str] = set()
    errors = 0
    stages = metadata_routes.stages_for_chain(chain)
    tag_source = (preferred_tag_source(code)
                  if sources is None and not args.english_title_only else None)
    scalar_settled = False
    asked: set[str] = set()
    for stage, then in zip(stages, (*stages[1:], "")):
        members = throttle.open_members(metadata_routes.stage_members(stage, chain))
        if scalar_settled:
            members = tuple(name for name in members if name == tag_source)
        if not members:
            continue
        asked.update(members)
        results, used_network = _ask_stage(
            adapter, query=query, variants=variants, stage=stage, members=members,
            args=args, health=health)
        for source, (payload, error, attempt) in results.items():
            if payload is None:
                error = error or MetadataProviderError("no result", kind="empty")
                error_writer.writerow({
                    "code": code, "query": query, "source": source, "kind": error.kind,
                    "status_code": error.status_code, "retryable": int(error.retryable),
                    "message": str(error),
                })
                error_handle.flush()
                errors += 1
                throttle.record_failure(source, error)
                continue
            throttle.record_success(source)
            candidates = _candidates_from(
                payload, query=query, source=source, profile=profile,
                snapshot=args.raw_dir / attempt / f"{source}.json",
                genre_decisions=genre_decisions, health=health,
                unmapped_genres=unmapped_genres, code=code)
            for field, candidate in candidates.items():
                by_field.setdefault(field, []).append(candidate)
                if candidate["value"]:
                    given.add(field)
        # 本地快照不消耗网络限流窗口。
        if args.delay > 0 and used_network:
            time.sleep(args.delay + random.uniform(0, min(0.4, args.delay / 3)))
        if sources is None and metadata_routes.settles(
                metadata_routes.SCALAR_FIELDS, given, wants_tags=True, then=then):
            scalar_settled = True
            if tag_source not in chain or tag_source in asked:
                break
    return by_field, errors


def main(argv: list[str] | None = None, *, provider=None) -> int:
    """入口只负责把这一趟登记进任务中心，正文在 `_scrape` 里。

    退出码非 0 是「提前收工」（磁盘触线、来源熔断），不是崩溃：记成 `cancelled`
    并把退出码写进摘要，活动页据此和真正的失败分开显示。
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    with cli_run("scrape-codes", args.db, label="番号资料刮削") as handle, ExitStack() as resources:
        resources.callback(close_log)
        code = _scrape(parser, args, handle, provider=provider, resources=resources)
        if code:
            handle.finish("cancelled", summary={"exit_code": code},
                          error=f"提前收工，退出码 {code}")
        return code


def _scrape_batch(parser, args):
    """按参数签名定位同一批次及其输出。"""
    sources = None
    if args.sources is not None:
        try:
            sources = parse_sources(args.sources)
        except ValueError as error:
            parser.error(str(error))
    profile = CUSTOM_PROFILE if sources is not None else args.profile
    scope = dict(db=str(args.db.resolve()), profile=profile, sources=sources,
                 english_title_only=args.english_title_only, refresh=args.refresh, limit=args.limit,
                 codes=_requested_codes(args.codes_file) if args.codes_file else None,
                 wiki_pages=args.wiki_pages_file.read_text(encoding=ENCODING) if args.wiki_pages_file else None)
    signature = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
    active_path = args.log_dir / 'scrape-codes-active.json'
    active = json.loads(active_path.read_text(encoding='utf-8')) if args.resume and active_path.is_file() else {}
    output = args.out or (Path(active['output']) if active.get('signature') == signature
                         and active.get('status') == 'active' else _default_output())
    checkpoint_path = output.with_suffix('.resume.json')
    saved = json.loads(checkpoint_path.read_text(encoding='utf-8')) if args.resume and checkpoint_path.is_file() else {}
    completed = set(saved.get('completed') or [])
    if completed and not output.is_file():
        parser.error('续跑候选文件未取得，请恢复原文件或使用 --no-resume')
    return sources, profile, signature, active_path, output, checkpoint_path, saved, completed


def _prepare_scrape_output(parser, args, connection, saved, signature, output, errors_path, completed):
    """固定原批次番号，并保留成功番号的候选行。"""
    codes = _load_codes(connection, args, parser)
    if saved and saved.get('signature') != signature:
        parser.error('续跑参数与原批次不一致，请使用原参数或 --no-resume')
    if saved.get('codes'):
        by_code = {normalise_code_key(row[0]): row for row in codes}
        codes = [by_code[code] for code in saved['codes'] if code in by_code]
    previous_rows = read_rows(output) if saved and output.is_file() else []
    previous_rows = [row for row in previous_rows if row['query'] in completed]
    write_rows(output, FIELDS, previous_rows, atomic=True)
    previous_errors = read_rows(errors_path) if saved and errors_path.is_file() else []
    write_rows(errors_path, ERROR_FIELDS, previous_errors, atomic=True)
    return codes, len(previous_rows), len(previous_errors)


def _scrape(parser, args, handle, *, provider=None, resources) -> int:
    sources, profile, signature, active_path, output, checkpoint_path, saved, completed = _scrape_batch(parser, args)
    error_name = output.name.replace("metadata-field-candidates-", "metadata-source-errors-", 1)
    if error_name == output.name:
        error_name = output.stem + "-errors.csv"
    errors_path = args.errors or output.with_name(error_name)
    health_path = args.health or _health_output(output)
    unmapped_path = args.unmapped or _unmapped_output(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    errors_path.parent.mkdir(parents=True, exist_ok=True)
    configure_log(args.log_dir)
    guard = DiskGuard(system_volume(), args.min_free, args.disk_check_secs)
    try:
        free_gb = guard.check(force=True)
    except JobPolicyError as error:
        log(f"[stop] {error}")
        close_log()
        return error.exit_code
    log(f"系统盘可用 {free_gb:.1f} GiB，运行期阈值 {args.min_free:.1f} GiB")
    # 健康表覆盖这一趟可能问到的全部来源：点名的就是那几家，走链的是所有链的并集。
    covered = sources or ((WIKI_SOURCE,) if profile == WIKI_PROFILE else CHAIN_SOURCES)
    health = _health_rows(covered, profile)
    health.update(saved.get('health') or {})
    unmapped_genres: dict[tuple[str, str], list] = {}
    unmapped_genres.update({tuple(key): value for key, value in saved.get('unmapped', [])})
    adapter = _build_adapter(args, covered, provider)
    resources.callback(adapter.close)

    connection = open_readonly(args.db)
    resources.callback(connection.close)
    # 用户在复核页收录过的 genre 这一批就当已知词，不再作为未收录回来问一遍。
    genre_decisions = load_genre_decisions(connection)
    codes, groups_written, errors_written = _prepare_scrape_output(
        parser, args, connection, saved, signature, output, errors_path, completed)
    def save_progress(status='active'):
        record = dict(signature=signature, output=str(output.resolve()), status=status,
                      completed=sorted(completed), codes=[normalise_code_key(row[0]) for row in codes],
                      health=health, unmapped=list(unmapped_genres.items()))
        atomic_write_text(checkpoint_path, json.dumps(record, ensure_ascii=False))
        atomic_write_text(active_path, json.dumps(record, ensure_ascii=False))
    save_progress()
    log(f"字段候选批次：profile {profile}，番号 {len(codes)}，来源 {','.join(covered)}；只读查询，不写 ledger")

    throttle = _Throttle(health)
    stopped: JobPolicyError | None = None
    # 流式写：两个文件同时开着，行在长循环里边跑边落盘，中途还有 guard.check()
    # 打断点。write_rows 收的是完整行集合，改用它等于「跑完才落盘」，被打断就全丢。
    with output.open("a", encoding='utf-8', newline="") as candidate_handle, \
            errors_path.open("a", encoding='utf-8', newline="") as error_handle:
        candidate_writer = csv.DictWriter(candidate_handle, fieldnames=FIELDS)
        error_writer = csv.DictWriter(error_handle, fieldnames=ERROR_FIELDS)
        for index, row in enumerate(codes, 1):
            code, size_gb, videos = row[0], row[1], row[2]
            if normalise_code_key(code) in completed:
                handle.progress(index, len(codes), normalise_code_key(code))
                continue
            try:
                guard.check()
            except JobPolicyError as error:
                stopped = error
                log(f"[stop] {error}")
                break
            query = normalise_code_key(code)
            # 第一个永远是账本的规范写法，评审键不随回退漂移。
            variants = code_query_variants(code) or (query,)
            chain = _chain_for(row, profile=profile, sources=sources)
            fetched_at = datetime.now(timezone.utc).isoformat()
            by_field, errors = _collect_code_fields(
                code=code, query=query, variants=variants, chain=chain, profile=profile,
                sources=sources, adapter=adapter, args=args, health=health, throttle=throttle,
                genre_decisions=genre_decisions, unmapped_genres=unmapped_genres,
                error_writer=error_writer, error_handle=error_handle)
            errors_written += errors
            for field, candidates in by_field.items():
                if args.english_title_only and field != "title":
                    continue
                candidates = sort_candidates(field, candidates, code=code)
                candidate_writer.writerow({
                    "item_key": f"{query}:{field}", "code": code, "query": query,
                    "field": field, "field_label": FIELD_LABELS[field],
                    "current_value": "、".join(_current_values(connection, code, field)),
                    "candidates_json": json.dumps(candidates, ensure_ascii=False, separators=(",", ":")),
                    "source_count": len(candidates), "source_profile": profile,
                    "policy_version": POLICY_VERSION, "status": "candidate",
                    "size_gb": round(size_gb, 2), "videos": videos, "fetched_at": fetched_at,
                })
                groups_written += 1
            candidate_handle.flush()
            error_handle.flush()
            if by_field and not errors:
                completed.add(query)
            save_progress()
            _write_health(health_path, health)
            _write_unmapped(unmapped_path, unmapped_genres)
            if index % 25 == 0:
                log(f"{index}/{len(codes)}：已落 {groups_written} 个字段组，错误 {errors_written}")
            handle.progress(index, len(codes), f"{query}")
    save_progress('complete' if len(completed) == len(codes) else 'active')
    _write_health(health_path, health)
    _write_unmapped(unmapped_path, unmapped_genres)
    log(f"完成：{groups_written} 个字段候选组 → {output}")
    log(f"来源健康 → {health_path}")
    log(f"未收录 genre {len(unmapped_genres)} 种 → {unmapped_path}")
    if errors_written:
        log(f"来源错误 {errors_written} 条 → {errors_path}")
    # 鉴权失败单列。混在错误总数里看不出「这一批有几家其实一条都没问到」，
    # 而它决定的是下一步做什么：补凭据重跑，而不是等限流过去。
    if throttle.auth_blocked:
        log(f"鉴权失败的来源 {len(throttle.auth_blocked)} 家，本批已停止请求；按各自的说明处理后重跑："
            + "；".join(f"{source}（{detail}）" for source, detail in throttle.auth_blocked.items()))
    close_log()
    return stopped.exit_code if stopped is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
