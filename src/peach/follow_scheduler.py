"""APScheduler-backed automatic follow checks and their persisted preference."""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler
from .update_tasks import UpdateBusy


LOGGER = logging.getLogger(__name__)
JOB_ID = "peach-follow-update"
MIN_INTERVAL_MINUTES = 15
MAX_INTERVAL_MINUTES = 7 * 24 * 60
#: 连续失败时下一次最多推迟到原间隔的几倍。失败最常见的原因是上游临时挡回来或者
#: 断网；按原间隔继续敲只会把同一个错误重复几十遍，还白耗流量额度。第一次失败不
#: 退避（可能只是抖动），从第二次起翻倍，到这个倍数为止。
MAX_BACKOFF_FACTOR = 8


def _stamp(moment: datetime | None = None) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z")


#: 订阅源拉取（ADR-0042）借用同一个实现，只是换一个 job id 与一份自己的状态文件。
#: 间隔、退避、跳过与状态上报只有一份：第二套定时会长出第二种「上次什么时候跑的」。
FEED_JOB_ID = "peach-feed-update"


@dataclass(frozen=True)
class FollowScheduleConfig:
    enabled: bool = True
    interval_minutes: int = 60


class FollowScheduleStore:
    """Small replace-on-write state file; scheduling preferences are not ledger truth."""

    def __init__(self, state_root: Path, filename: str = "follow-schedule.json",
                 default: FollowScheduleConfig | None = None):
        self.path = Path(state_root) / filename
        self.default = default or FollowScheduleConfig()

    @staticmethod
    def validate(enabled: object, interval_minutes: object) -> FollowScheduleConfig:
        if not isinstance(enabled, bool) or isinstance(interval_minutes, bool):
            raise ValueError("enabled must be a boolean and interval_minutes an integer")
        interval = int(interval_minutes)
        if not MIN_INTERVAL_MINUTES <= interval <= MAX_INTERVAL_MINUTES:
            raise ValueError(
                f"interval_minutes must be between {MIN_INTERVAL_MINUTES} and "
                f"{MAX_INTERVAL_MINUTES}"
            )
        return FollowScheduleConfig(bool(enabled), interval)

    def load(self) -> FollowScheduleConfig:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return self.validate(payload.get("enabled", self.default.enabled),
                                 payload.get("interval_minutes",
                                             self.default.interval_minutes))
        except FileNotFoundError:
            return self.default
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            LOGGER.warning("invalid follow schedule preference at %s; using defaults", self.path)
            return self.default

    def save(self, config: FollowScheduleConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, self.path)


class FollowUpdateScheduler:
    """Own one interval job and expose its state to the settings surface."""

    def __init__(self, state_root: Path, run_check: Callable[[], dict], *, available: bool,
                 job_id: str = JOB_ID, filename: str = "follow-schedule.json",
                 default: FollowScheduleConfig | None = None,
                 unavailable_message: str =
                 "automatic follow updates are available only on the ledger writer"):
        self.job_id = job_id
        self.unavailable_message = unavailable_message
        self.store = FollowScheduleStore(state_root, filename, default)
        self.run_check = run_check
        self.available = bool(available)
        self.config = self.store.load()
        self.scheduler = BackgroundScheduler(timezone=timezone.utc, daemon=True)
        self._started = False
        self._state_lock = threading.Lock()
        self._running = False
        self._last_started_at: str | None = None
        self._last_finished_at: str | None = None
        self._last_skipped_at: str | None = None
        self._last_error: str | None = None
        self._last_checked = 0
        self._last_added = 0
        self._consecutive_failures = 0

    def start(self) -> None:
        if self._started or not self.available:
            return
        self.scheduler.start()
        self._started = True
        self._apply_job()

    def stop(self) -> None:
        if not self._started:
            return
        self.scheduler.shutdown(wait=False)
        self._started = False

    def update(self, *, enabled: object, interval_minutes: object) -> dict:
        if not self.available:
            raise ValueError(self.unavailable_message)
        self.config = self.store.validate(enabled, interval_minutes)
        self.store.save(self.config)
        if self._started:
            self._apply_job()
        return self.status()

    def _apply_job(self) -> None:
        if not self.config.enabled:
            if self.scheduler.get_job(self.job_id) is not None:
                self.scheduler.remove_job(self.job_id)
            return
        interval = self.config.interval_minutes
        self.scheduler.add_job(
            self._run,
            "interval",
            id=self.job_id,
            minutes=interval,
            next_run_time=datetime.now(timezone.utc) + timedelta(minutes=interval),
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=min(interval * 60, 300),
        )

    def backoff_minutes(self, failures: int) -> int:
        """连续失败 `failures` 次之后，下一次应当推到多少分钟以后。

        第一次失败照原间隔重试——大多数失败只是一次抖动。从第二次起翻倍，
        到 `MAX_BACKOFF_FACTOR` 倍为止，且不超过允许的最大间隔。
        """
        if failures <= 1:
            return self.config.interval_minutes
        factor = min(2 ** (failures - 1), MAX_BACKOFF_FACTOR)
        return min(self.config.interval_minutes * factor, MAX_INTERVAL_MINUTES)

    def _run(self) -> None:
        with self._state_lock:
            started_before = self._last_started_at
            self._running = True
            self._last_started_at = _stamp()
            self._last_error = None
        skipped = False
        try:
            # 这一轮在 `task_run` 里的记录由 `run_check` 那条路上的 BackgroundJob 落下
            # （`w_follow_check` 按 `automatic` 传 `trigger="scheduled"`），开始、进度、
            # 结束三样都在里面。这里不再记第二条：轮询和手动检查是同一件事的两种发起
            # 方式，各记一条在活动页上就成了两轮。
            try:
                result = self.run_check()
            except UpdateBusy:
                result = {'busy': True}
            if result.get("busy"):
                # 锁被另一次检查占着是互斥的正常结果，不是故障：手动检查正在跑的
                # 时候自动这一轮什么也没做。这里不抛异常：抛了界面就把它显示成
                # 「上次失败」，还连带触发退避——真正的失败反而被这种噪声盖住。
                # 这一轮既没检查也没完成，所以不留「上次运行」的时间戳，
                # 只记一次跳过；下一次照原间隔来。
                skipped = True
                with self._state_lock:
                    self._last_skipped_at = _stamp()
                    self._last_started_at = started_before
                return
            rows = result.get("results") or []
            failures = [row for row in rows if not row.get("ok")]
            with self._state_lock:
                self._last_checked = int(result.get("checked") or 0)
                self._last_added = sum(int(row.get("added") or 0) for row in rows)
                if failures:
                    self._last_error = f"{len(failures)} 个来源检查失败"
            self._settle(failed=bool(failures))
        except Exception as error:  # the background job must never terminate the scheduler
            LOGGER.exception("automatic follow update failed")
            with self._state_lock:
                self._last_error = str(error)
            self._settle(failed=True)
        finally:
            with self._state_lock:
                self._running = False
                if not skipped:
                    self._last_finished_at = _stamp()

    def _settle(self, *, failed: bool) -> None:
        """结算这一轮的连续失败计数，必要时把下一次推后。

        `modify_job` 不在 `_state_lock` 里调用：那会把本类的锁套在 APScheduler
        自己的锁外面，而 `status()` 的取锁顺序正相反，两条路径一起跑就会死锁。
        """
        with self._state_lock:
            self._consecutive_failures = (
                self._consecutive_failures + 1 if failed else 0)
            failures = self._consecutive_failures
        if not failed or not self._started:
            return
        delay = self.backoff_minutes(failures)
        if delay <= self.config.interval_minutes:
            return
        try:
            self.scheduler.modify_job(
                self.job_id,
                next_run_time=datetime.now(timezone.utc) + timedelta(minutes=delay))
        except JobLookupError:
            # 任务在这一轮跑的过程中被关掉了。没什么要退避的。
            pass

    def status(self) -> dict:
        with self._state_lock:
            # 任务与本类的状态必须是同一张快照：分开读会出现「已经不在跑了，
            # 下次运行时间却还是上一轮的」这种自相矛盾的回答。
            job = self.scheduler.get_job(self.job_id) if self._started else None
            next_run = job.next_run_time if job is not None else None
            return {
                "ok": True,
                "available": self.available,
                "enabled": self.config.enabled,
                "interval_minutes": self.config.interval_minutes,
                "running": self._running,
                "next_run_at": _stamp(next_run) if next_run else None,
                "last_started_at": self._last_started_at,
                "last_finished_at": self._last_finished_at,
                # 上一次因为锁被占着而跳过的时刻。跳过不是失败，所以它不进
                # `last_error`，但也不能一点痕迹都不留。
                "last_skipped_at": self._last_skipped_at,
                "last_error": self._last_error,
                "last_checked": self._last_checked,
                "last_added": self._last_added,
                "consecutive_failures": self._consecutive_failures,
            }
