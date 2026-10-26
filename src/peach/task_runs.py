"""任务中心：一张 `task_run` 表的写入、互斥、租约与查询。

各域的执行细节仍归各域（`BackgroundJob` 的状态字典、`library_processing` 的动作预算、
追更调度器的退避计数）。这里只维护对外那一层：谁在跑、跑到哪、什么时候结束的、
为什么没跑。表的取舍写在 `migrations/0028_task_runs.sql`。

三条不变量，全部由这个模块和那张表一起守住：

1. **终态一次性**。`finish` 是一条带 `WHERE status IN (活跃态)` 的 UPDATE，从终态改回
   活跃态不可能发生，重复结算也只有第一次生效。租约回收和任务自己收尾撞在一起时，
   落地的是先到的那一个。
2. **互斥由 `INSERT` 判**。撞上部分唯一索引才算冲突，不先查一遍——先查再插之间那个
   时间窗正是「两轮同时开跑」的来源。
3. **跳过也是一条记录**。定时触发撞上在跑的那轮会写一条 `cancelled`，带上挡路那条的
   id。跳过只留在内存里的话，「刚才那轮为什么没跑」就永远答不出来。

后继（ADR-0040）也住在这张表里：一轮任务结束时声明「接下来该做什么」，`enqueue_followups`
把它们写成 `pending` 行，父子关系落在 `parent_run_id` / `root_run_id` 上。真正去跑它们的是
`followups.FollowupRunner`，这一层只负责入队、去重、领取和查询。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .jobs import TaskRunConflict, process_alive
from .repository import LedgerDatabase

__all__ = [
    "ACTIVE_STATUSES", "DEFAULT_KEEP", "LEASE_SECONDS", "MAX_FOLLOWUPS",
    "MAX_FOLLOWUP_DEPTH", "PROGRESS_INTERVAL", "TASK_LABELS", "TERMINAL_STATUSES",
    "TRIGGERS", "TaskRun", "TaskRunConflict", "TaskRunHandle", "TaskRunStore",
    "cli_run", "inert_handle", "stamp", "task_label",
]

LOGGER = logging.getLogger(__name__)

#: 未结束的两种状态。部分唯一索引和每一条 CAS 的 WHERE 都用这一份。
ACTIVE_STATUSES = ("pending", "running")
#: 终态。只有这四种能进 `finish`，也只有这四种会被 `prune` 回收。
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "interrupted")
TRIGGERS = ("manual", "scheduled", "startup", "cli")

#: 心跳超过这么久没续，且进程也不在了，就按被打断处理。
#: 取 5 分钟不是因为任务不会更久——单项外部动作的预算是 240 秒（`library_processing`
#: 的 `ACTION_BUDGETS`），比它短就会把正常的慢任务判成死掉的。
LEASE_SECONDS = 300.0

#: 进度与心跳的默认写入间隔。扫描一万个文件时每项都写一次库，等于给每一项加一次
#: 写事务；页面两秒轮询一次，比这更密的进度没有读者。
PROGRESS_INTERVAL = 2.0

#: 进度与心跳最多等写锁这么久。它们不是结果：等不到就跳过这一次、记一条 warning，
#: 下一次进度照常再写。按默认的 30 秒等，任务线程会为一行心跳停住半分钟，同进程排在
#: 后面的写入也跟着等。
PROGRESS_LOCK_TIMEOUT = 2.0

#: 结算撞上写锁时，每次重试前等多少秒。结算写不进去，这一行就停在 `running`、
#: 占住互斥键到下次服务重启，所以它要重试；每一次尝试本身还会等满 SQLite 的 30 秒。
FINISH_RETRY_DELAYS = (1.0, 5.0)

#: 每个 task_key 默认保留多少条终态记录。活动页只看最近几轮，更早的属于日志。
DEFAULT_KEEP = 20

#: 一轮任务最多能派出多少条后继（ADR-0040 第五条）。多出来的在入队时截断，截断了几条
#: 写进父任务的摘要——静默丢弃和无上限一样，都是事后查不出来的那一类。
#: 64 是「一部片的出演者加厂牌」那个量级的十几倍，正常扇出撞不到它。
MAX_FOLLOWUPS = 64

#: 链深度上限。根任务是 0，它派出的后继是 1。允许到 2 是给「后继再派一次后继」留一层，
#: 再往下就不是扇出而是递归了，而递归的规模在代码评审里看不出来。
MAX_FOLLOWUP_DEPTH = 2

#: 任务登记表：key → 给人看的名字。不在表里的 key 由界面原样显示，不猜。
TASK_LABELS = {
    "follow-check": "追更检查",
    "follow-resolve": "关注来源查找",
    "library-processing": "扫描与采集",
    "taste-refresh": "口味分析",
    "link-check": "链接检查",
    "link-prune": "失效链接清理",
    "resource-scan": "资源同步检查",
    "resource-apply": "资源同步清理",
    "scraping-cover": "封面采集",
    "media-repair": "媒体修复",
    "organize": "按模板整理",
    "batch": "批量操作",
    "scrape-codes": "番号资料刮削",
    "jav-covers": "封面批量抓取",
    "entity-avatar": "补全资料图片",
    "studio-mark": "补厂牌官网与标识",
    "creator-identity": "查创作者身份",
    "code-samples": "补番号样张",
    "feed-check": "订阅源拉取",
    "feed-scrape": "取新作资料",
    "want-scrape": "取想要的资料",
    "timeline-thumbnails": "视频缩略图采集",
    "download-postprocess": "云下载后处理",
    "seed-import": "导入初始资料",
}


#: 触发方式的中文名。跳过原因是给人看的一句话，里面不留英文枚举值。
TRIGGER_LABELS = {
    "manual": "手动", "scheduled": "定时", "startup": "启动", "cli": "命令行",
}


def task_label(task_key: str) -> str:
    return TASK_LABELS.get(task_key, task_key)


def trigger_label(trigger: str) -> str:
    return TRIGGER_LABELS.get(trigger, trigger)


def stamp(moment: datetime | None = None) -> str:
    """ISO-8601 UTC 文本，与 `entity`、`follow_schedule` 同一种写法。"""
    return (moment or datetime.now(timezone.utc)).astimezone(
        timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class TaskRun:
    id: int
    task_key: str
    trigger: str
    status: str
    mutex_key: str | None
    pid: int | None
    host: str
    started_at: str | None
    finished_at: str | None
    heartbeat_at: str | None
    progress_current: int | None
    progress_total: int | None
    progress_label: str
    result_summary: dict
    error: str
    #: 派出这一轮的父任务；不是后继时为 None。
    parent_run_id: int | None = None
    #: 整条链的根。根任务指向自己，裸任务为 None（与 `parent_run_id` 同时为空）。
    root_run_id: int | None = None
    #: 这条后继要做的那件事的全名，同时是它的互斥键。不是后继时为空。
    followup_key: str = ""
    followup_depth: int = 0

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def followup(self) -> bool:
        return bool(self.followup_key)

    def payload(self) -> dict:
        """API 与页面共用的投影。派生字段在这里算一次，不让每个读者各算一份。"""
        started, finished = _parse(self.started_at), _parse(self.finished_at)
        end = finished or (datetime.now(timezone.utc) if self.active else None)
        return {
            "id": self.id,
            "task_key": self.task_key,
            "task_label": task_label(self.task_key),
            "trigger": self.trigger,
            "status": self.status,
            "mutex_key": self.mutex_key,
            "pid": self.pid,
            "host": self.host,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "heartbeat_at": self.heartbeat_at,
            "elapsed_seconds": (
                round((end - started).total_seconds(), 1)
                if started and end else None),
            "progress_current": self.progress_current,
            "progress_total": self.progress_total,
            "progress_label": self.progress_label,
            "result_summary": self.result_summary,
            "error": self.error,
            "parent_run_id": self.parent_run_id,
            "root_run_id": self.root_run_id,
            "followup_key": self.followup_key,
            "followup_depth": self.followup_depth,
        }


_COLUMNS = ("id,task_key,trigger,status,mutex_key,pid,host,started_at,finished_at,"
            "heartbeat_at,progress_current,progress_total,progress_label,"
            "result_summary,error,parent_run_id,root_run_id,followup_key,followup_depth")


def _row(row: sqlite3.Row) -> TaskRun:
    try:
        summary = json.loads(row["result_summary"] or "{}")
    except ValueError:
        summary = {}
    return TaskRun(
        id=row["id"], task_key=row["task_key"], trigger=row["trigger"],
        status=row["status"], mutex_key=row["mutex_key"], pid=row["pid"],
        host=row["host"] or "", started_at=row["started_at"],
        finished_at=row["finished_at"], heartbeat_at=row["heartbeat_at"],
        progress_current=row["progress_current"], progress_total=row["progress_total"],
        progress_label=row["progress_label"] or "",
        result_summary=summary if isinstance(summary, dict) else {},
        error=row["error"] or "",
        parent_run_id=row["parent_run_id"], root_run_id=row["root_run_id"],
        followup_key=row["followup_key"] or "",
        followup_depth=int(row["followup_depth"] or 0),
    )


class TaskRunStore:
    """`task_run` 的唯一写入口。

    `enabled=False` 时所有写入是空操作而读取照常：只读端（macOS）的账本是写入端的
    副本，在那里写一行任务记录等于制造一处永远合不回去的分叉。读得到写入端在跑什么
    仍然有用，所以只关写。
    """

    def __init__(self, database: LedgerDatabase | Path | str, *,
                 enabled: bool = True, host: str | None = None):
        self.database = (database if isinstance(database, LedgerDatabase)
                         else LedgerDatabase(Path(database)))
        self.enabled = bool(enabled)
        self.host = host if host is not None else socket.gethostname()
        self._lock = threading.Lock()
        #: run_id → 上一次写进度的单调时钟。节流只看这一份，不查库。
        self._last_write: dict[int, float] = {}
        #: 进度正写不进去的那几轮。一段连续失败只记一条 warning，写进去一次就清掉。
        self._progress_failing: set[int] = set()
        self.finish_retry_delays = FINISH_RETRY_DELAYS

    def available(self) -> bool:
        """这本账本上有没有 `task_run` 表。

        迁移不是服务启动时自动跑的（`peach migrate --apply` 是单独一步），所以升级
        之后第一次启动完全可能撞上没有这张表的账本。那时候不能让每个按钮都炸成 500：
        调用方据此把整个任务中心关掉，各域的任务照常能跑，只是这一轮不留记录。
        """
        try:
            self.query(limit=1)
            return True
        except sqlite3.DatabaseError:
            return False

    # -- 写入 --------------------------------------------------------------

    def start(self, task_key: str, *, trigger: str, mutex_key: str | None = None,
              conflict: str = "skip", total: int | None = None,
              label: str = "", pid: int | None = None) -> TaskRun | None:
        from .update_tasks import admission
        with admission(self.database.db_path):
            from .job_recovery import current_run
            resumed = self._resume(current_run.get(), task_key)
            if resumed is not None:
                return resumed
            return self._start(task_key, trigger=trigger, mutex_key=mutex_key,
                               conflict=conflict, total=total, label=label, pid=pid)

    def suspend(self, run_id: int, *, label: str = '等待安装完成后继续') -> None:
        if not self.enabled:
            return
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "UPDATE task_run SET status='pending',pid=0,progress_label=?,result_summary=? "
                "WHERE id=? AND status='running'",
                (label, json.dumps({'paused_for_update': True}), run_id))

    def _resume(self, run_id, task_key):
        if run_id is None or not self.enabled:
            return None
        with self.database.write_transaction(notify=False) as connection:
            cursor = connection.execute(
                "UPDATE task_run SET status='running',pid=?,host=?,heartbeat_at=?,result_summary='{}' "
                "WHERE id=? AND task_key=? AND status='pending' "
                "AND json_extract(result_summary,'$.paused_for_update')=1",
                (os.getpid(), self.host, stamp(), run_id, task_key))
        return self.get(run_id) if cursor.rowcount else None

    def _start(self, task_key: str, *, trigger: str, mutex_key: str | None = None,
               conflict: str = "skip", total: int | None = None,
               label: str = "", pid: int | None = None) -> TaskRun | None:
        """开一轮。互斥冲突时按 `conflict` 处理，返回 None 或抛 `TaskRunConflict`。

        `conflict="skip"` 是定时触发的语义：本轮不跑，但要留下一条记录说明被谁挡了。
        `conflict="raise"` 是手动触发的语义：告诉用户是哪一轮挡着，让他自己决定。
        """
        if trigger not in TRIGGERS:
            raise ValueError(f"未知的触发方式：{trigger}")
        if conflict not in ("skip", "raise"):
            raise ValueError(f"未知的冲突处理方式：{conflict}")
        if not self.enabled:
            return None
        moment = stamp()
        try:
            with self.database.write_transaction(notify=False) as connection:
                cursor = connection.execute(
                    "INSERT INTO task_run(task_key,trigger,status,mutex_key,pid,host,"
                    "started_at,heartbeat_at,progress_current,progress_total,"
                    "progress_label,result_summary) "
                    "VALUES(?,?,'running',?,?,?,?,?,0,?,?,'{}')",
                    (task_key, trigger, mutex_key, pid if pid is not None else os.getpid(),
                     self.host, moment, moment, total, label))
                run_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            if mutex_key is None:
                # 没声明互斥还撞上约束，说明坏的是别的东西（CHECK、列约束），
                # 把它当成冲突就是把真故障藏起来。
                raise
            blocking = self.blocking(mutex_key)
            if conflict == "raise":
                raise TaskRunConflict(
                    task_key, blocking.id if blocking else None) from None
            self._record_skip(task_key, trigger, mutex_key, blocking)
            return None
        # 这里刻意不给节流打时间戳：开工那一刻还不知道要做多少件，第一次进度报上来的
        # 才是真正能看的那一行。把它一起节流掉，活动页开头两秒只有一句「进行中」。
        return self.get(run_id)

    def _record_skip(self, task_key: str, trigger: str, mutex_key: str,
                     blocking: TaskRun | None) -> None:
        """把一次静默跳过写成终态记录。它没有 `started_at`——这一轮从没开跑。"""
        reason = (f"与第 {blocking.id} 轮（{trigger_label(blocking.trigger)}触发）冲突，本次跳过"
                  if blocking else "已有一轮在进行，本次跳过")
        summary = json.dumps({"blocked_by": blocking.id if blocking else None},
                             ensure_ascii=False)
        with self.database.write_transaction(notify=False) as connection:
            connection.execute(
                "INSERT INTO task_run(task_key,trigger,status,mutex_key,pid,host,"
                "finished_at,result_summary,error) "
                "VALUES(?,?,'cancelled',?,?,?,?,?,?)",
                (task_key, trigger, mutex_key, os.getpid(), self.host,
                 stamp(), summary, reason))

    def progress(self, run_id: int | None, *, current: int | None = None,
                 total: int | None = None, label: str | None = None,
                 throttle: float = PROGRESS_INTERVAL) -> bool:
        """推进度并顺带续租。已经进终态的行不再接受进度。

        `throttle` 是墙钟节流：页面两秒轮询一次，比这更密的写入没有读者，只有代价。
        收尾前那一次要看到真实的最终计数，调用方传 `throttle=0`。

        写锁拿不到时返回 False、不抛：进度丢一次，页面上只是计数晚两秒；抛出去则会
        打断正在跑的任务本身。没写进去的那次照样占一个节流窗口，否则账本被长时间占住
        期间，每一次进度都要为写锁等满 `PROGRESS_LOCK_TIMEOUT`。
        """
        if not self.enabled or run_id is None or not self._due(run_id, throttle):
            return False
        assignments = ["heartbeat_at=?"]
        values: list[object] = [stamp()]
        for column, value in (("progress_current", current),
                              ("progress_total", total),
                              ("progress_label", label)):
            if value is not None:
                assignments.append(f"{column}=?")
                values.append(value)
        return self._update_progress(run_id, assignments, values)

    def heartbeat(self, run_id: int | None, *,
                  throttle: float = PROGRESS_INTERVAL) -> bool:
        """只续租。没有计数可报、但确实还活着的阶段用它。写锁拿不到时同 `progress`。"""
        if not self.enabled or run_id is None or not self._due(run_id, throttle):
            return False
        return self._update_progress(run_id, ["heartbeat_at=?"], [stamp()])

    def _update_progress(self, run_id: int, assignments: list[str],
                         values: list[object]) -> bool:
        try:
            done = self._update_active(run_id, assignments, values,
                                       timeout=PROGRESS_LOCK_TIMEOUT)
        except sqlite3.OperationalError as error:
            with self._lock:
                first = run_id not in self._progress_failing
                self._progress_failing.add(run_id)
            if first:
                LOGGER.warning("第 %s 轮的进度没写进 task_run，下一次进度再写：%s",
                               run_id, error)
            return False
        with self._lock:
            self._progress_failing.discard(run_id)
        return done

    def finish(self, run_id: int | None, status: str, *, summary: dict | None = None,
               error: str = "") -> bool:
        """结算。只能从活跃态进终态，一条 UPDATE 带 WHERE 做 CAS。

        返回是否由本次调用落地。`False` 表示这一轮已经被别人结算过——租约回收和任务
        自己收尾撞在一起就是这种情形，后到的那个不许覆盖。

        写锁拿不到时按 `finish_retry_delays` 重试，全部用完仍写不进去才抛。
        """
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"{status} 不是终态")
        if not self.enabled or run_id is None:
            return False
        moment = stamp()
        assignments = ["status=?", "finished_at=?", "heartbeat_at=?", "result_summary=?",
                       "error=?"]
        values = [status, moment, moment,
                  json.dumps(summary or {}, ensure_ascii=False), error]
        delays = list(self.finish_retry_delays)
        while True:
            try:
                done = self._update_active(run_id, assignments, values)
                break
            except sqlite3.OperationalError as failure:
                if not delays:
                    raise
                delay = delays.pop(0)
                LOGGER.warning("第 %s 轮结算没写进 task_run，%s 秒后重试：%s",
                               run_id, delay, failure)
                time.sleep(delay)
        with self._lock:
            self._last_write.pop(run_id, None)
            self._progress_failing.discard(run_id)
        return done

    # -- 后继（ADR-0040）--------------------------------------------------

    def enqueue_followups(self, parent_run_id: int | None,
                          followups) -> dict:
        """把一轮任务声明的后继写成 `pending` 行，返回这次入队的结果。

        `followups` 的每一项是 `(key, task_key, label)`：`key` 是这件事的全名，同时是
        互斥键；`task_key` 决定谁来跑它、活动页上显示成什么。

        三种没入队的情况分开计数，都不静默：`duplicates` 是同一件事已经在排或在跑
        （同一次声明里重复的也算），`truncated` 是超出 `MAX_FOLLOWUPS` 被截掉的，
        `depth_exceeded` 是这条链已经到 `MAX_FOLLOWUP_DEPTH` 层。调用方把这三个数
        放进父任务的摘要，活动页因此看得见「派了几条、丢了几条」。
        """
        result = {"queued": [], "duplicates": 0, "truncated": 0, "depth_exceeded": 0}
        items = list(followups or [])
        if not self.enabled or parent_run_id is None or not items:
            return result
        parent = self.get(int(parent_run_id))
        if parent is None:
            return result
        depth = parent.followup_depth + 1
        if depth > MAX_FOLLOWUP_DEPTH:
            result["depth_exceeded"] = len(items)
            return result
        seen: set[str] = set()
        planned: list[tuple[str, str, str]] = []
        for key, task_key, label in items:
            key = str(key).strip()
            if not key or key in seen:
                result["duplicates"] += 1
                continue
            seen.add(key)
            planned.append((key, str(task_key), str(label or "")))
        if len(planned) > MAX_FOLLOWUPS:
            result["truncated"] = len(planned) - MAX_FOLLOWUPS
            planned = planned[:MAX_FOLLOWUPS]
        root = parent.root_run_id or parent.id
        moment = stamp()
        with self.database.write_transaction(notify=False) as connection:
            # 父任务是裸任务时它就是这条链的根。补在这里而不是 `start`：开工那一刻还
            # 不知道这一轮会不会派后继，给每一行都填一个指向自己的 root 是无意义的噪声。
            connection.execute(
                "UPDATE task_run SET root_run_id=id WHERE id=? AND root_run_id IS NULL",
                (parent.id,))
            for key, task_key, label in planned:
                try:
                    cursor = connection.execute(
                        "INSERT INTO task_run(task_key,trigger,status,mutex_key,pid,host,"
                        "heartbeat_at,progress_label,result_summary,parent_run_id,"
                        "root_run_id,followup_key,followup_depth) "
                        "VALUES(?,?,'pending',?,?,?,?,?,'{}',?,?,?,?)",
                        (task_key, parent.trigger, key, os.getpid(), self.host,
                         moment, label, parent.id, root, key, depth))
                except sqlite3.IntegrityError:
                    # 活跃互斥索引拦下的：同一件事已经在排队或在跑，这一条不必再排。
                    result["duplicates"] += 1
                    continue
                result["queued"].append(int(cursor.lastrowid))
        return result

    def continue_followup(self, run: TaskRun, label: str = "") -> int | None:
        """一条后继结算后说「还没做完」，在原地接一条同样的：同一个父任务、同一层、同一个键。

        不走 `enqueue_followups`：那是往下派一层，存量要跑几十轮的事会撞上 `MAX_FOLLOWUP_DEPTH`。
        接续不加深，会不会停由处理器自己保证——它只在这一轮确实推进了才说要接着跑（ADR-0084）。
        新行的 id 比已经在排的都大，同一通道里别的后继先跑。返回新行 id；同一件事已经在排就是 None。
        """
        if not self.enabled or not run.followup_key:
            return None
        with self.database.write_transaction(notify=False) as connection:
            try:
                cursor = connection.execute(
                    "INSERT INTO task_run(task_key,trigger,status,mutex_key,pid,host,"
                    "heartbeat_at,progress_label,result_summary,parent_run_id,"
                    "root_run_id,followup_key,followup_depth) "
                    "VALUES(?,?,'pending',?,?,?,?,?,'{}',?,?,?,?)",
                    (run.task_key, run.trigger, run.followup_key, os.getpid(), self.host,
                     stamp(), label, run.parent_run_id, run.root_run_id, run.followup_key,
                     run.followup_depth))
            except sqlite3.IntegrityError:
                return None
        return int(cursor.lastrowid)

    def claim_followup(self, task_keys) -> TaskRun | None:
        from .update_tasks import admission, UpdateBusy
        try:
            with admission(self.database.db_path):
                return self._claim_followup(task_keys)
        except UpdateBusy:
            return None

    def _claim_followup(self, task_keys) -> TaskRun | None:
        """领走这几类里最早的一条待跑后继并标 `running`；没有就返回 None。

        `task_keys` 是调用方这条通道负责的任务种类。按 id 升序领：后继之间没有优先级
        （ADR-0040 未决），先声明的先跑是唯一说得清的顺序。
        """
        keys = tuple(dict.fromkeys(str(key) for key in task_keys or ()))
        if not self.enabled or not keys:
            return None
        marks = ",".join("?" * len(keys))
        moment = stamp()
        with self.database.write_transaction(notify=False) as connection:
            row = connection.execute(
                f"SELECT id FROM task_run WHERE followup_key IS NOT NULL "
                f"AND status='pending' AND task_key IN ({marks}) ORDER BY id LIMIT 1",
                keys).fetchone()
            if row is None:
                return None
            run_id = int(row["id"])
            cursor = connection.execute(
                "UPDATE task_run SET status='running',pid=?,host=?,started_at=COALESCE(started_at,?),"
                "heartbeat_at=? WHERE id=? AND status='pending'",
                (os.getpid(), self.host, moment, moment, run_id))
        return self.get(run_id) if cursor.rowcount else None

    def requeue_followups(self) -> list[int]:
        """把停在 `running` 的后继改回 `pending`，服务启动时调用一次。

        普通任务在这种情形下被判 `interrupted`（`recover_interrupted`），后继不能——
        没跑完的后继冻在终态就再也没有重试机会，而续跑的判据是「只把成功判定当作已
        完成」（`peach-batch-jobs`）。代价是后继必须幂等，这条写在 ADR-0040 第六条。
        """
        if not self.enabled:
            return []
        with self.database.write_transaction(notify=False) as connection:
            rows = connection.execute(
                "SELECT id FROM task_run WHERE followup_key IS NOT NULL "
                "AND status='running'").fetchall()
            if not rows:
                return []
            connection.execute(
                "UPDATE task_run SET status='pending',heartbeat_at=? "
                "WHERE followup_key IS NOT NULL AND status='running'",
                (stamp(),))
        return [int(row["id"]) for row in rows]

    def children(self, parent_run_id: int) -> list[TaskRun]:
        """这一轮派出的后继，按入队顺序。"""
        with self.database.read_connection() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM task_run WHERE parent_run_id=? ORDER BY id",
                (int(parent_run_id),)).fetchall()
        return [_row(row) for row in rows]

    def _due(self, run_id: int, throttle: float) -> bool:
        """节流闸门。放行的那一次也记时间——不记的话下一次拿 0 当基准，节流形同虚设。"""
        now = time.monotonic()
        with self._lock:
            if throttle > 0 and now - self._last_write.get(run_id, 0.0) < throttle:
                return False
            self._last_write[run_id] = now
        return True

    def _update_active(self, run_id: int, assignments: list[str],
                       values: list[object], *, timeout: float | None = None) -> bool:
        marks = ",".join(f"'{name}'" for name in ACTIVE_STATUSES)
        with self.database.write_transaction(notify=False, timeout=timeout) as connection:
            cursor = connection.execute(
                f"UPDATE task_run SET {','.join(assignments)} "
                f"WHERE id=? AND status IN ({marks})",
                [*values, run_id])
        return cursor.rowcount > 0

    # -- 启动恢复与回收 ----------------------------------------------------

    def recover_interrupted(self, *, stale_after: float = LEASE_SECONDS) -> list[int]:
        """把上一个进程留下的活跃行改成 `interrupted`。服务启动时调用一次。

        判据分两种，本机与别的机器不能共用一条：

        - **本机写的行**：进程不在了就是被打断，不必等租约到期。PID 与我们自己相同
          也算——启动恢复跑在建任何一轮之前，那必然是一个被复用的 PID。
        - **别的机器写的行**：账本是复制过来的，那台机器上的进程存活与否这里查不到，
          只能按租约判。

        写成 `interrupted` 而不是 `failed`：任务没有失败，是没跑完；两者在「要不要
        去查为什么」上是相反的结论。

        后继不走这里，它们由 `requeue_followups` 重新排队（ADR-0040 第六条）。
        """
        if not self.enabled:
            return []
        deadline = datetime.now(timezone.utc) - timedelta(seconds=stale_after)
        recovered: list[int] = []
        for run in self.query(status="active", limit=1000):
            if run.followup:
                continue
            if run.status == 'pending' and run.result_summary.get('paused_for_update'):
                from .job_recovery import read
                recovery = read(self.database.db_path, run.task_key)
                if recovery.get('status') == 'active' and recovery.get('run_id') == run.id:
                    continue
            local = run.host == self.host
            alive = (local and run.pid is not None and run.pid != os.getpid()
                     and process_alive(run.pid))
            beat = _parse(run.heartbeat_at) or _parse(run.started_at)
            expired = beat is None or beat < deadline
            if alive or (not local and not expired):
                continue
            if local:
                from .job_recovery import read
                recovery = read(self.database.db_path, run.task_key)
                if recovery.get('status') == 'active' and recovery.get('run_id') == run.id:
                    self.suspend(run.id, label='等待服务启动后继续')
                    continue
            if self.finish(run.id, "interrupted",
                           error="服务重启，这一轮没有跑完"):
                recovered.append(run.id)
        return recovered

    def prune(self, task_key: str, keep: int = DEFAULT_KEEP) -> int:
        """每个 task_key 只留最近 `keep` 条终态记录，返回删掉多少条。

        活跃行一条都不动：它们不是历史。阈值用 `LIMIT 1 OFFSET keep` 取「第 keep+1 新
        的那条 id」，不用窗口函数——这张表上要的只是一个分界点。
        """
        if not self.enabled or keep < 0:
            return 0
        marks = ",".join(f"'{name}'" for name in TERMINAL_STATUSES)
        with self.database.write_transaction(notify=False) as connection:
            threshold = connection.execute(
                f"SELECT id FROM task_run WHERE task_key=? AND status IN ({marks}) "
                "ORDER BY id DESC LIMIT 1 OFFSET ?", (task_key, keep)).fetchone()
            if threshold is None:
                return 0
            cursor = connection.execute(
                f"DELETE FROM task_run WHERE task_key=? AND status IN ({marks}) "
                "AND id<=?", (task_key, threshold["id"]))
        return cursor.rowcount

    # -- 查询 --------------------------------------------------------------

    def get(self, run_id: int) -> TaskRun | None:
        with self.database.read_connection() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM task_run WHERE id=?", (run_id,)).fetchone()
        return _row(row) if row else None

    def blocking(self, mutex_key: str) -> TaskRun | None:
        """当前占着这把锁的那一轮。冲突时用它回答「是谁挡的」。"""
        marks = ",".join(f"'{name}'" for name in ACTIVE_STATUSES)
        with self.database.read_connection() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM task_run WHERE mutex_key=? "
                f"AND status IN ({marks}) ORDER BY id LIMIT 1", (mutex_key,)).fetchone()
        return _row(row) if row else None

    def query(self, *, status: str = "", task_key: str = "",
              limit: int = 50) -> list[TaskRun]:
        """按状态与任务筛最近的若干轮。`status` 另收 `active`／`finished` 两个合集。"""
        clauses: list[str] = []
        values: list[object] = []
        if status == "active":
            clauses.append("status IN (%s)" % ",".join(f"'{s}'" for s in ACTIVE_STATUSES))
        elif status == "finished":
            clauses.append("status IN (%s)" % ",".join(f"'{s}'" for s in TERMINAL_STATUSES))
        elif status:
            if status not in (*ACTIVE_STATUSES, *TERMINAL_STATUSES):
                raise ValueError(f"未知的状态：{status}")
            clauses.append("status=?")
            values.append(status)
        if task_key:
            clauses.append("task_key=?")
            values.append(task_key)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.database.read_connection() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM task_run{where} ORDER BY id DESC LIMIT ?",
                [*values, max(1, min(int(limit), 500))]).fetchall()
        return [_row(row) for row in rows]

    def finished_page(self, *, before: tuple[str, int] | None = None,
                      limit: int = 50) -> tuple[list[TaskRun], bool]:
        """终态行按结束时刻从新到旧取一页，返回这一页和它后面还有没有更早的。

        排序键是 `(finished_at, id)` 而不是 `id`：开跑早、结束晚的长任务按 id 会落进
        早已翻过的那几页，活动页「最近完成」上就再也看不到它。终态一次性，结束时刻落地
        后不再变，所以游标之后的行不会再挪到它前面去。`before` 是上一页最旧那一行的
        `(finished_at, id)`，这一页只取严格排在它后面的行。

        一页数的是顶层那几轮，它们已结束的后继跟着一起回来：一轮拉取派出二十条后继时，
        按行数翻页会让后继占满这一页、把派出它们的那一轮挤到下一页，页面上就只剩一串
        没有父卡片可挂的散行。父行已被 `prune` 掉的后继自己算顶层。
        """
        size = max(1, min(int(limit), 500))
        marks = ",".join(f"'{name}'" for name in TERMINAL_STATUSES)
        where = (f"t.status IN ({marks}) AND (t.parent_run_id IS NULL OR NOT EXISTS "
                 "(SELECT 1 FROM task_run p WHERE p.id=t.parent_run_id))")
        values: list[object] = []
        if before is not None:
            where += " AND (t.finished_at<? OR (t.finished_at=? AND t.id<?))"
            values = [before[0], before[0], int(before[1])]
        columns = ",".join(f"t.{name}" for name in _COLUMNS.split(","))
        with self.database.read_connection() as connection:
            rows = connection.execute(
                f"SELECT {columns} FROM task_run t WHERE {where} "
                "ORDER BY t.finished_at DESC, t.id DESC LIMIT ?", [*values, size + 1]).fetchall()
        page = [_row(row) for row in rows[:size]]
        return page + self.settled_followups([run.id for run in page]), len(rows) > size

    def settled_followups(self, root_ids) -> list[TaskRun]:
        """这几轮派出的、已经结束的后继，整条链上的都算。"""
        ids = [int(run_id) for run_id in root_ids]
        if not self.enabled or not ids:
            return []
        marks = ",".join(f"'{name}'" for name in TERMINAL_STATUSES)
        holes = ",".join("?" * len(ids))
        with self.database.read_connection() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM task_run WHERE root_run_id IN ({holes}) "
                f"AND id NOT IN ({holes}) AND status IN ({marks}) ORDER BY id",
                [*ids, *ids]).fetchall()
        return [_row(row) for row in rows]

    # -- 调用方的便利入口 --------------------------------------------------

    @contextmanager
    def track(self, task_key: str, *, trigger: str, mutex_key: str | None = None,
              conflict: str = "skip", total: int | None = None,
              label: str = "", keep: int = DEFAULT_KEEP) -> Iterator["TaskRunHandle"]:
        """同步代码用的一段式接线：进去开一轮，出来按有没有异常自动结算。

        被互斥挡住时产出一个不记录任何东西的 handle（`run_id is None`），调用方自己
        决定要不要继续干活——定时触发应当直接返回，命令行脚本通常也一样。
        """
        run = self.start(task_key, trigger=trigger, mutex_key=mutex_key,
                         conflict=conflict, total=total, label=label)
        handle = TaskRunHandle(self, run.id if run else None)
        try:
            yield handle
        except BaseException as error:
            handle.finish("failed", error=f"{type(error).__name__}: {error}")
            raise
        else:
            handle.finish("succeeded")
        finally:
            if run is not None:
                self.prune(task_key, keep)


def inert_handle() -> "TaskRunHandle":
    """一个什么都不登记的句柄。给「这一趟不进任务中心」和默认参数用。"""
    return TaskRunHandle(TaskRunStore(Path("."), enabled=False), None)


@contextmanager
def cli_run(task_key: str, db_path: Path | str | None, *, label: str = "",
            mutex_key: str | None = None) -> Iterator["TaskRunHandle"]:
    """命令行脚本的一段式接线：账本上记一轮，异常时记成失败。

    账本还没建、或者还没跑到 0028 时不记录，只在标准输出上说一句。批处理是无人值守
    跑的，因为「这一趟没法登记」就整个不跑，代价比不登记大得多；而静默跳过又会让人
    以为记上了，所以那一句必须打出来。

    脚本被强杀时这里什么也做不了，那一行会停在 `running`：服务下次启动的
    `recover_interrupted` 按 PID 把它收成 `interrupted`，两处判据是同一个。
    """
    store = None
    if db_path is not None and Path(db_path).is_file():
        candidate = TaskRunStore(Path(db_path))
        try:
            candidate.query(limit=1)
            store = candidate
        except sqlite3.OperationalError:
            print(f"[task-run] {db_path} 没有 task_run 表，本趟不登记进任务中心")
    if store is None:
        yield TaskRunHandle(TaskRunStore(Path(db_path or ":memory:"), enabled=False), None)
        return
    with store.track(task_key, trigger="cli", mutex_key=mutex_key,
                     conflict="skip", label=label) as handle:
        yield handle


def declared_rows(declared) -> list[tuple[str, str, str]]:
    """任务结果里声明的后继（`[{"key", "task_key", "label"}]`）换成 `enqueue_followups` 要的行。

    后台任务的结算与命令行入口共用这一份：任务结果是同一个函数给的，两边各拆一遍，
    命令行那边漏掉，就是一整轮新登记的女优全都没人去补头像和资料。
    """
    if not isinstance(declared, list):
        return []
    return [(item.get("key"), item.get("task_key"), item.get("label", ""))
            for item in declared if isinstance(item, dict) and item.get("key")]


@dataclass
class TaskRunHandle:
    """一轮任务的句柄。`run_id is None` 表示这一轮没有被记录，所有方法都是空操作。"""

    store: TaskRunStore
    run_id: int | None
    settled: bool = False

    @property
    def saved(self) -> dict:
        """同一条持久任务已提交的领域检查点。"""
        run = self.store.get(self.run_id) if self.run_id is not None else None
        return dict(run.result_summary.get('checkpoint') or {}) if run else {}

    def checkpoint(self, **fields) -> None:
        """领域结果提交后保存完成集合；后继重新领取时沿用。"""
        if self.run_id is None or not self.store.enabled:
            return
        with self.store.database.write_transaction(notify=False) as connection:
            row = connection.execute('SELECT result_summary FROM task_run WHERE id=?', (self.run_id,)).fetchone()
            if row is None:
                return
            summary = json.loads(row[0] or '{}')
            summary['checkpoint'] = {**summary.get('checkpoint', {}), **fields}
            connection.execute("UPDATE task_run SET result_summary=? WHERE id=? AND status='running'",
                               (json.dumps(summary, ensure_ascii=False), self.run_id))

    def progress(self, current: int | None = None, total: int | None = None,
                 label: str | None = None, *,
                 throttle: float = PROGRESS_INTERVAL) -> None:
        self.store.progress(self.run_id, current=current, total=total,
                            label=label, throttle=throttle)

    def heartbeat(self) -> None:
        self.store.heartbeat(self.run_id)

    def finish(self, status: str, *, summary: dict | None = None,
               error: str = "") -> bool:
        """结算一次。重复调用不再写入——终态一次性这条约束在句柄这一层也要成立。"""
        if self.settled:
            return False
        self.settled = True
        return self.store.finish(self.run_id, status, summary=summary, error=error)
