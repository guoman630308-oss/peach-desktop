"""一次「检查更新」的完整流程：取回 → 落库 → 顺带学一条作者别名。

Web 与命令行共用这一份。各写一遍必然分岔：其中一份漏掉往回翻页、漏掉凭据到位
之后的强制重取、不学官方渠道的作者别名，于是同一句「检查更新」在网页上做的事比
在终端里多，而没有任何地方说明这一点。

网络与落库都在这里，已确认条目各自交给调用方传入的 `writer` 提交；整页完成后
才推进来源游标。展示不在这里，`CheckResult` 交回给调用方。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Mapping

from . import follow_providers
from .follow import FollowHistoryEnd, FollowSourceError, FollowSourceRateLimited
from .follow_secrets import CredentialError
from .follow_sources import SourceFetch, enrichment_mark, history_window, published_stamp
from .follow_store import RecordOutcome

#: 新来源首次采集至少收下多少条。历史范围只按天数算时，更新稀疏的作者一条都
#: 进不来；按条数又会让高产作者一下子塞满未看。取 30：大约一屏，kemono 一页 50
#: 条不必翻页，rule34video（一页 24）、fanbox（一页 10）翻一两页就够。
INITIAL_HISTORY_FLOOR = 30
#: 为补足下限最多往前翻几页。每页都是真实请求，下限不值得拿无界的翻页去换。
MAX_FLOOR_PAGES = 5
_BACKFILL_PROVIDERS = follow_providers.backfill_providers()


@dataclass(frozen=True)
class CheckResult:
    """一条来源检查完之后发生了什么。

    失败也是返回值而不是异常：逐条独立成败，一个来源缺凭据或被机器人验证挡住，
    不该让其余来源的更新一起消失。
    """

    source_id: int
    provider: str
    ref: str
    label: str
    page: int = 0
    older: bool = False
    ok: bool = True
    status: str = "ok"
    error: str = ""
    message: str = ""
    exhausted: bool = False
    fetch: SourceFetch | None = None
    outcome: RecordOutcome | None = None
    author_alias_learned: dict | None = None
    history_skipped: int = 0


def plan_check(store, credentials, *, source_id: int | None = None,
               older: bool = False,
               backfill_providers: frozenset[str] = frozenset()) -> list[dict]:
    """要检查哪些来源，以及每条要不要绕过条件请求游标。只读，不联网。

    `force_media_reparse` 单独在这里算好：凭据已经存在、但旧候选还标着
    `media_needs_credential` 时，条件请求的 304 会让旧解析结果永久不变——显式检查
    得无条件重取一次，凭据才真正生效。往回翻页时不算这个，那一页本来就没游标。
    """
    rows = [dict(row) for row in store.sources(enabled_only=True)
            if (source_id is None or row["id"] == source_id)
            and (not older or row["provider"] in backfill_providers)]
    for row in rows:
        row["force_media_reparse"] = (
            not older and credentials.load(row["provider"]) is not None
            and store.source_needs_media_reparse(row["id"])
        )
        # 第二阶段跳过谁：细节已经补齐的条目。重取媒体时不跳过任何一条——
        # 那正是把上一轮没取到的细节补回来的时机。
        mark = enrichment_mark(row["provider"])
        row["enrich_skip"] = (
            frozenset() if row["force_media_reparse"] or not mark
            else store.enriched_external_ids(row["id"], mark))
    return rows


def build_connector_for(provider: str, credentials, connector_factory, *,
                        enrich_skip: frozenset[str] = frozenset()):
    """按凭据仓库里现有的凭据造一个连接器。

    `connector_factory` 由调用方传进来（各自模块里的 `build_connector`），不在这里
    直接引用：那样调用方替换它才有效，测试也才能只替自己那一处。
    """
    kwargs = {"credential": credentials.load(provider)}
    gofile_credential = credentials.load("gofile")
    if gofile_credential is not None:
        kwargs["gofile_credential"] = gofile_credential
    if enrich_skip:
        kwargs["enrich_skip"] = enrich_skip
    return connector_factory(provider, **kwargs)


def run_check(row: Mapping, *, credentials, writer, connector_factory,
              older: bool = False, moment: datetime | None = None,
              progress=None, initial_days: int = 0, cooldown=None) -> CheckResult:
    """检查一条来源。

    `row` 是 `plan_check` 给出的那种字典。`writer` 是零参可调用对象，返回一个产出
    `FollowStore` 的上下文管理器；每次写都单独取一次，好让调用方决定提交粒度。

    `cooldown` 是按站计的限流冷却（`FollowMediaResolver` 那一份，浏览与检查共用）：
    站点在冷却就不发请求、也不把来源记成失败；这一条被限流就让整站进入冷却，
    同一站排在后面的来源随之跳过。
    """
    moment = moment or datetime.now(timezone.utc)
    source_id = int(row["id"])
    provider, ref = str(row["provider"]), str(row["ref"])
    page = (int(row["backfill_page"] or 0) + 1) if older else 0
    metadata = json.loads(row.get("metadata_json") or '{}')
    replay_first = older and bool(metadata.get('initial_history_first_page_pending'))
    if replay_first:
        page = 0
    base = {"source_id": source_id, "provider": provider, "ref": ref,
            "label": str(row["label"] or ""), "page": page, "older": older}
    remaining = cooldown.cooling(provider) if cooldown is not None else 0
    if remaining:
        return CheckResult(**base, ok=False, status="error",
                           error=f"{provider} 限流冷却中，{int(remaining) + 1} 秒后再试，本次跳过")
    force = bool(row.get("force_media_reparse")) or replay_first
    cutoff, floor = _history_limits(row, metadata, writer, moment,
                                    older=older, initial_days=initial_days)
    recorder = _PageRecorder(row, writer, moment, cutoff)
    try:
        connector = build_connector_for(
            provider, credentials, connector_factory,
            enrich_skip=frozenset(row.get("enrich_skip") or ()))
        connector.history_after = cutoff
        connector.history_skipped = 0
        connector.history_floor = floor
        connector.candidate_ready = recorder.publish
        if progress is not None:
            connector.progress = progress
        fetch = connector.fetch(
            ref,
            etag=None if force else row["etag"],
            last_modified=None if force else row["last_modified"],
            page=page,
        )
    except FollowHistoryEnd:
        with writer() as store:
            store.record_history_end(source_id, moment)
        return CheckResult(**base, exhausted=True, message="没有更多历史内容")
    except CredentialError as error:
        return _record_failure(writer, base, error, moment, "unauthorized")
    except FollowSourceError as error:
        _pause_if_rate_limited(cooldown, provider, error)
        return _record_failure(writer, base, error, moment, "error")
    candidates = history_window(fetch.candidates, cutoff, floor)
    history_skipped = getattr(connector, 'history_skipped', 0) + len(fetch.candidates) - len(candidates)
    fetch = replace(fetch, candidates=candidates)
    with writer() as store:
        if not fetch.not_modified:
            if replay_first:
                store.merge_source_metadata(source_id, {'initial_history_first_page_pending': False}, moment)
            elif history_skipped and page == 0 and 'initial_history_first_page_pending' not in metadata:
                store.merge_source_metadata(source_id, {'initial_history_first_page_pending': True}, moment)
        outcome = recorder.record_in(store, fetch, page)
    if floor and not fetch.not_modified:
        recorder.cutoff = None
        fetch, outcome = _fill_floor(row, connector, writer, fetch, outcome, cutoff, floor, moment,
                                     recorder=recorder)
    with writer() as store:
        learned = store.learn_official_author_alias(provider, ref, fetch.candidates)
    return CheckResult(**base, fetch=fetch, outcome=outcome,
                       author_alias_learned=learned, history_skipped=history_skipped)


def _pause_if_rate_limited(cooldown, provider: str, error: FollowSourceError) -> None:
    """来源说被限流了，就让整站进入冷却；同一站排在后面的来源随之跳过。"""
    if cooldown is not None and isinstance(error, FollowSourceRateLimited):
        cooldown.pause(provider, error.retry_after)


def _history_limits(row, metadata: dict, writer, moment: datetime, *,
                    older: bool, initial_days: int) -> tuple[datetime | None, int]:
    """这次检查的历史边界与条数下限；新来源首次检查时把两者固定进 metadata。

    重试和自动更新沿用固定下来的值，不按当时的设置重算。往回翻页不设边界。
    """
    history_after = metadata.get('initial_history_after')
    floor = int(metadata.get('initial_history_floor') or 0)
    if 'initial_history_after' not in metadata and not row.get('last_checked_at'):
        history_after = ((moment - timedelta(days=initial_days)).isoformat()
                         if initial_days and not older else '')
        floor = INITIAL_HISTORY_FLOOR if history_after else 0
        with writer() as store:
            store.merge_source_metadata(int(row["id"]), {'initial_history_after': history_after,
                                                         'initial_history_floor': floor}, moment)
    if not history_after or older:
        return None, 0
    return datetime.fromisoformat(history_after), floor


def _fill_floor(row, connector, writer, fetch, outcome, cutoff, floor, moment, *, recorder):
    """首次采集不足下限时往前翻页补足，再把边界挪到实际收下的最早一条。

    往前翻的页整页收下，回填游标因此和「已经抓到第几页」一致，之后手动加载更早
    接着往前走，不会漏掉半页。边界跟着挪，是为了之后的常规检查仍把这些条目
    算在范围内，不在每次检查里重复报「跳过」。
    """
    source_id, provider, ref = int(row["id"]), str(row["provider"]), str(row["ref"])
    kept = list(fetch.candidates)
    totals = {"discovered": outcome.discovered, "added": outcome.added, "updated": outcome.updated}
    counts = {"skipped": fetch.skipped, "skipped_compilations": fetch.skipped_compilations,
              "probed": fetch.probed}
    page = 0
    connector.history_after = None
    while len(kept) < floor and provider in _BACKFILL_PROVIDERS and page < MAX_FLOOR_PAGES:
        page += 1
        try:
            more = connector.fetch(ref, etag=None, last_modified=None, page=page)
        except FollowHistoryEnd:
            break
        except (CredentialError, FollowSourceError):
            # 首页已经落库，补页失败只是这次补得少，之后手动加载更早还能接着翻。
            break
        extra = recorder.record(more, page)
        kept.extend(more.candidates)
        for key in totals:
            totals[key] += getattr(extra, key)
        for key in counts:
            counts[key] += getattr(more, key)
    stamps = [stamp for stamp in map(published_stamp, kept) if stamp is not None]
    patch = {'initial_history_floor': 0}
    if stamps and min(stamps) < cutoff:
        patch['initial_history_after'] = min(stamps).isoformat()
    with writer() as store:
        store.merge_source_metadata(source_id, patch, moment)
    return (replace(fetch, candidates=tuple(kept), **counts),
            replace(outcome, **totals))


def _record_failure(writer, base: dict, error: Exception, moment: datetime,
                    status: str) -> CheckResult:
    with writer() as store:
        store.record_error(base["source_id"], str(error), moment, status=status)
    return CheckResult(**base, ok=False, status=status, error=str(error))


class _PageRecorder:
    """逐项提交可展示的结果，整页完成后才保存来源游标与完成状态。"""

    def __init__(self, row, writer, moment, cutoff):
        self.row, self.writer, self.moment, self.cutoff = row, writer, moment, cutoff
        self.added_ids: set[str] = set()

    def publish(self, candidate):
        # 有历史边界时，详情可能给出更早的日期；列表行由整页最终筛选接纳。
        if self.cutoff is not None and candidate.partial:
            return
        if not history_window((candidate,), self.cutoff):
            return
        fetch = SourceFetch(provider=str(self.row['provider']), ref=str(self.row['ref']),
                            request_url=str(self.row['url']), semantics=str(self.row['semantics']),
                            candidates=(candidate,))
        with self.writer() as store:
            outcome = store.record(int(self.row['id']), fetch,
                                   creator_aliases=store.creator_aliases(self.row['entity_id']),
                                   moment=self.moment, complete=False)
        if outcome.added:
            self.added_ids.add(str(candidate.external_id))

    def record(self, fetch, page):
        with self.writer() as store:
            return self.record_in(store, fetch, page)

    def record_in(self, store, fetch, page):
        outcome = store.record(int(self.row['id']), fetch,
                               creator_aliases=store.creator_aliases(self.row['entity_id']),
                               moment=self.moment, page=page)
        added = len(self.added_ids.intersection(str(c.external_id) for c in fetch.candidates))
        self.added_ids.clear()
        return replace(outcome, added=outcome.added + added, updated=outcome.updated - added)
