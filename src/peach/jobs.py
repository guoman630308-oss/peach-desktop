"""Peach 批处理任务共享的安全与成本策略，以及服务内后台任务的状态机。"""
from __future__ import annotations

import copy
import shutil
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

from .media_sources import SOURCE_COSTS, cost_for, cost_sql

DEFAULT_METERED_LOCATIONS = frozenset(location for location, cost in SOURCE_COSTS.items()
                                    if cost == 'metered') | {'online'}


class JobPolicyError(RuntimeError):
    exit_code = 1


class MeteredSourceDenied(JobPolicyError):
    exit_code = 2


class DiskSpaceDenied(JobPolicyError):
    exit_code = 3


class JobPaused(JobPolicyError):
    """安装期间在任务检查点暂停，原参数保留用于恢复。"""
    exit_code = 3


class JobAlreadyRunning(JobPolicyError):
    exit_code = 0


class TaskRunConflict(JobAlreadyRunning):
    """手动触发撞上了已经占着同一把互斥键的那一轮（`task_runs` 里的记录）。

    住在这里而不是 `task_runs`：它与 `JobAlreadyRunning` 是同一件事的两种载体
    （pid 锁文件与账本里的一行），退出码语义也一样——已有实例在跑不是失败。
    `task_runs` 在低一层被 `jobs` 反向依赖，异常放那边会成环。

    `blocking_run_id` 必须带出去：只说「已有任务在跑」的话，用户既不知道是哪一轮，
    也没有可以点进去看的东西。API 把它翻成 409。
    """

    def __init__(self, task_key: str, blocking_run_id: int | None):
        super().__init__(f"{task_key} 已有一轮在进行")
        self.task_key = task_key
        self.blocking_run_id = blocking_run_id


#: 长跑批处理只领没被处置的资产。对它们抽帧、探时长既产生不了任何
#: 用户看得见的结果，又要为每一条向网盘发一次注定失败的读请求——那一轮 115 抽帧报的 73 条
#: `broken_source` 里，72 条是这种行，而且每次重跑都会再失败一遍。
#: 点名重抽（`--asset`）不套这一条：那是用户指着某一行说「就抽它」。
ACTIVE_ASSET_SQL = "disposal IS NULL"


@dataclass(frozen=True)
class SourceAccessPolicy:
    metered_locations: frozenset[str] = DEFAULT_METERED_LOCATIONS

    def requires_budget(self, location: str) -> bool:
        """有正常读取目录的来源可筛选执行，全部受保护时需明确授权。"""
        if self.metered_locations != DEFAULT_METERED_LOCATIONS:
            return location in self.metered_locations
        from .settings_file import active
        config = active()
        roots = config.locations.get(location, ())
        return (all(cost_for(location, root, config) == 'metered' for root in roots)
                if roots else cost_for(location, config=config) == 'metered')

    def sql_filter(
        self,
        location: str | None,
        allow_metered: bool,
        column: str = "location",
    ) -> tuple[str, tuple[str, ...]]:
        """返回参数化 location 条件；显式计费授权是唯一放行入口。"""
        if location and not allow_metered and self.requires_budget(location):
            raise MeteredSourceDenied(
                f"{location} 的目录已开启代理流量保护；确认预算后显式加 --allow-metered"
            )

        clauses: list[str] = []
        parameters: list[str] = []
        if location:
            clauses.append(f"{column}=?")
            parameters.append(location)
        if not allow_metered:
            if self.metered_locations == DEFAULT_METERED_LOCATIONS:
                path_column = column.rsplit('.', 1)[0] + '.path' if '.' in column else 'path'
                expression, values = cost_sql(column, path_column)
                clauses.append(expression + "='free'")
                parameters.extend(values)
            else:
                metered = tuple(sorted(self.metered_locations))
                placeholders = ",".join("?" for _ in metered)
                clauses.append(f"{column} NOT IN ({placeholders})")
                parameters.extend(metered)
        if not clauses:
            return "", ()
        return " AND " + " AND ".join(clauses), tuple(parameters)


def require_free_space(path: Path | str, minimum_gb: float) -> float:
    """返回可用 GiB；无法读取或低于阈值都拒绝启动，避免静默失去磁盘闸门。"""
    try:
        free_gb = shutil.disk_usage(path).free / 1024**3
    except OSError as exc:
        raise DiskSpaceDenied(f"无法读取 {path} 的磁盘余量") from exc
    if free_gb < minimum_gb:
        raise DiskSpaceDenied(
            f"{path} 仅剩 {free_gb:.1f} GiB（阈值 {minimum_gb:.1f} GiB）"
        )
    return free_gb


class DiskGuard:
    """运行期磁盘闸门：长任务必须在跑的过程中反复检查，不能只查起跑线。

    2026-08-15 的实际事故：抽帧启动时 C: 还有几百 GB，`require_free_space` 放行，
    随后 CloudDrive 把下载块缓存到系统盘直到 0 字节可用，而任务全程没有再看一眼。
    消耗方是第三方软件、落点也不是本任务的产物目录，所以"只盯自己写的文件"同样拦不住。
    这里按墙钟节流地复查真实可用空间，触线就让调用方停下来。
    """

    def __init__(self, path: Path | str, minimum_gb: float, interval_secs: float = 20.0):
        self.path = Path(path)
        self.minimum_gb = float(minimum_gb)
        self.interval_secs = float(interval_secs)
        self._next_check = 0.0
        from .update_tasks import current_db
        self._pause_db = current_db.get()

    def free_gb(self) -> float:
        return shutil.disk_usage(self.path).free / 1024**3

    def check(self, force: bool = False) -> float | None:
        """到期就复查；返回本次读到的可用 GiB，未到期返回 None。触线抛 DiskSpaceDenied。"""
        from .update_tasks import check_pause
        check_pause(self._pause_db)
        now = time.monotonic()
        if not force and now < self._next_check:
            return None
        self._next_check = now + self.interval_secs
        try:
            free_gb = self.free_gb()
        except OSError as exc:
            raise DiskSpaceDenied(f"无法读取 {self.path} 的磁盘余量") from exc
        if free_gb < self.minimum_gb:
            raise DiskSpaceDenied(
                f"运行中 {self.path} 仅剩 {free_gb:.1f} GiB（阈值 {self.minimum_gb:.1f} GiB）；"
                "已停止任务。检查第三方缓存目录，本任务的产物目录未必是消耗方"
            )
        return free_gb


def process_alive(pid: int) -> bool:
    """这个 PID 现在还是一个活进程吗。

    pid 锁靠它判断锁文件里那个进程还在不在，`task_runs.recover_interrupted` 靠它判断
    停在 `running` 的那一行是不是上一个进程留下的。两处问的是同一件事，而它在
    Windows 上的正确写法有一个会当场打断自己的陷阱（见 `_process_alive_windows`），
    散成两份只会有一份是对的。
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _process_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        # 锁文件可能是 Windows 那台写的，PID 会超出 POSIX 的 pid_t 范围。
        # 超界的 PID 一定不是本机活进程，按「已死」清理，而不是让整轮任务崩掉。
        return False
    return True


def _process_alive_windows(pid: int) -> bool:
    """用 OpenProcess 查存活，绝不能用 `os.kill(pid, 0)`。

        Windows 上 `signal.CTRL_C_EVENT == 0`，所以 Unix 那个探测存活的经典写法
        `os.kill(pid, 0)` 实际会调用 `GenerateConsoleCtrlEvent(CTRL_C_EVENT, ...)`，
        把 Ctrl+C 发给整个控制台进程组——包括调用者自己。锁里写的又是自己的 PID，
        于是「检查锁是否还活着」会当场把自己打断：在真实控制台里跑批处理或测试时
        表现为毫无征兆的 KeyboardInterrupt；重定向、无控制台的环境反而不复现，
    因为控制台事件无处投递。
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_ACCESS_DENIED = 5
    STILL_ACTIVE = 259

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # 拒绝访问说明进程存在但不归我们管；其余错误一律视为已消失。
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


class PidFileLock:
    """进程级独占锁；只清理确认已经不存在的旧 PID。"""

    #: 存活判据与任务中心共用一份（`process_alive`）。这里保留这个名字，是因为
    #: `windows_restart` 与托盘测试按它做判断和打桩。
    _running = staticmethod(process_alive)

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._acquired = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                old_pid = int(self.path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                old_pid = 0
            if old_pid and self._running(old_pid):
                raise JobAlreadyRunning(f"已有实例在跑（PID {old_pid}）")
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(str(os.getpid()))
        self._acquired = True

    def release(self) -> None:
        if self._acquired:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self._acquired = False

    def __enter__(self) -> "PidFileLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class BackgroundJob:
    """服务里一次「点一下、后台跑、前端轮询」的任务：锁、状态和线程都在这里。

    死链检查和资源对账各写了一份逐字相同的这个形状：一把锁、一个带 uuid 的状态字典、
    `status_only` 与 `restart` 两个开关、一个 daemon 线程，以及一个把异常翻成
    `status="failed"` 的 except。共用的不只是形状，还有三条容易漏的约定：

    1. **状态里必须带任务 id，而且每次改状态前都要核对**。轮询期间用户可以按
       `restart` 顶掉在跑的那一轮；被顶掉的线程如果还继续写状态，前端看到的就是
       新一轮的 id 配旧一轮的进度。
    2. **后台异常必须变成可轮询的状态**。只写日志的话，界面会永远停在「进行中」。
    3. **`thread.start()` 不能在锁里**。

    `snapshot()` 返回深拷贝：域模块的公开投影（挑字段、`[dict(x) for x in ...]`）
    因此可以在锁外安全地算，不必让每个域都自己记得「投影要在锁里做」。状态只装
    JSON 形态的数据，拷贝很便宜。
    """

    #: 结算时不往 `result_summary` 里带的键：它们要么是明细（一整份结果列表、问题
    #: 预览），要么是每一轮都会变的内部游标。摘要是给活动页一眼看完的一行。
    BULKY_STATE_KEYS = frozenset({
        "results", "rows", "issues", "issue_preview", "retryable_asset_ids",
        "current", "progress_seq", "last_progress_at", "issues_log", "followups",
    })

    def __init__(self, name: str, *, id_key: str = "job_id",
                 task_key: str = "", runs=None, mutex_key: str | None = None,
                 followup_runner=None):
        #: 线程名，出现在崩溃栈和进程视图里，所以取和端点一致的名字。
        self.name = name
        #: 状态字典里存任务 id 的键名。域模块的公开投影直接下发它，所以沿用各域原有的
        #: 名字（`check_id`／`scan_id`）而不是统一改名——那是前端契约。
        self.id_key = id_key
        #: 这一类任务在 `task_run` 表里的名字，和活动页上显示的名字一一对应。
        self.task_key = task_key or name
        #: `task_runs.TaskRunStore`；为 None 时这个任务不进任务中心（测试里的裸实例）。
        self.runs = runs
        #: 默认按任务自己互斥：同一个 BackgroundJob 本来就只跑一轮，写进表里之后，
        #: 命令行和调度器也能看见这把锁。
        self.mutex_key = mutex_key if mutex_key is not None else self.task_key
        #: `followups.FollowupRunner`；为 None 时这个任务声明的后继照样入队，只是没有
        #: 人当场去跑它们——服务启动时的 `resume()` 会把它们捡起来。
        self.followup_runner = followup_runner
        self.lock = threading.Lock()
        self.state: dict | None = None
        self.thread: threading.Thread | None = None
        #: job_id → task_run id。结算读这一份而不是读状态：状态可能已经被下一轮顶掉，
        #: 而被顶掉的那一轮同样要有人给它收尾。
        self._run_ids: dict[str, int] = {}
        self._activity_files: dict[str, Path] = {}
        self._recoverable: set[str] = set()
        self._paused_stops: set[str] = set()
        #: 摘 run id 与把那一行写成结束是同一件事：只摘不写完就放手的话，另一个线程
        #: 看见 id 没了以为已经收过尾，紧接着开的下一轮会撞上那一行还没释放的互斥键。
        self._close_lock = threading.Lock()
        if (db := self._database_path()) is not None:
            from .job_recovery import read
            previous = read(db, self.task_key)
            saved = previous.get('checkpoint') or {}
            if previous.get('status') in ('complete', 'failed') and saved.get(self.id_key):
                self.state = copy.deepcopy(saved)

    def snapshot(self) -> dict | None:
        """当前状态的深拷贝；一次都没跑过返回 None。"""
        with self.lock:
            return copy.deepcopy(self.state) if self.state is not None else None

    def _database_path(self) -> Path | None:
        value = getattr(getattr(self.runs, 'database', None), 'db_path', None)
        return Path(value) if isinstance(value, (str, Path)) else None

    def update(self, job_id: str, **fields: object) -> bool:
        """状态仍属于 `job_id` 时改字段；已经被顶掉就什么都不做并返回 False。"""
        with self.editing(job_id) as state:
            if state is None:
                return False
            state.update(fields)
            return True

    def checkpoint(self, job_id: str, **fields: object) -> bool:
        """领域结果已保存后，原子登记可跨进程恢复的完成集合与计数。"""
        if not self.update(job_id, **fields):
            return False
        if job_id in self._recoverable and (db := self._database_path()) is not None:
            from .job_recovery import checkpoint
            checkpoint(db, self.task_key, job_id, fields)
        return True

    @contextmanager
    def editing(self, job_id: str) -> Iterator[dict | None]:
        """拿住锁产出仍属于 `job_id` 的状态；被顶掉则产出 None。

        进度是往列表里追加、给计数加一，不都是整字段替换，所以除了 `update` 还要
        有这个原地改的入口。

        退出时把进度同步进 `task_run`，写库在锁外：那一步要拿数据库的写锁，套在本类
        的锁里面就多了一组取锁顺序，而进度本来不需要和状态原子。节流由 store 做。
        """
        if job_id in self._recoverable:
            from .update_tasks import check_pause
            check_pause(self._database_path())
        with self.lock:
            state = self.state
            current = state if state is not None and state[self.id_key] == job_id else None
            yield current
            observed = copy.copy(current) if current is not None else None
        if observed is not None:
            self._report_progress(job_id, observed)

    def _report_progress(self, job_id: str, state: dict) -> None:
        """把域自己的计数投影成 `task_run` 的进度。

        写不进去（已终态，或写锁一时拿不到）就算了：store 不抛、只记 warning，进度
        上报失败不能打断这一轮任务本身。
        """
        if self.runs is None:
            return
        run_id = self._run_ids.get(job_id)
        if run_id is None:
            return
        current = state.get("checked")
        total = state.get("total")
        counts = (int(current) if isinstance(current, (int, float)) else None,
                  int(total) if isinstance(total, (int, float)) else None)
        label = self._progress_label(state) or None
        if counts == (None, None) and label is None:
            # 这一次改的是别的字段（`run_id` 本身就是一例），没有进度可报。不往下走：
            # 空写一次会白占掉节流窗口，真正的第一份计数要等两秒才进得去表。
            return
        self.runs.progress(run_id, current=counts[0], total=counts[1], label=label)

    @staticmethod
    def _progress_label(state: dict) -> str:
        """一行「此刻在做什么」。域各有各的字段名，这里按可读性从具体到笼统取第一个。"""
        if state.get('status') in ('paused', 'pausing'):
            return ('已暂停' if state['status'] == 'paused' else '正在暂停') + ' · ' + str(state.get('stage') or '')
        current = state.get("current")
        if isinstance(current, dict):
            for key in ("label", "provider"):
                if current.get(key):
                    return str(current[key])[:120]
        for key in ("stage", "message", "step"):
            if state.get(key):
                return str(state[key])[:120]
        return ""

    def _summary(self, state: dict) -> dict:
        """结算摘要：只留标量，明细留在各域自己的状态与日志里。"""
        summary: dict[str, object] = {}
        for key, value in state.items():
            if key in self.BULKY_STATE_KEYS or key == self.id_key:
                continue
            if isinstance(value, bool) or isinstance(value, (int, float)):
                summary[key] = value
            elif isinstance(value, str) and 0 < len(value) <= 200:
                summary[key] = value
        return summary

    def start(self, fn: Callable[[str], None], *, initial: dict | None = None,
              restart: bool = False, trigger: str = "manual", recovery: dict | None = None) -> dict:
        from .update_tasks import admission, UpdateBusy
        db = self._database_path()
        try:
            with admission(db) if db is not None else nullcontext():
                return self._start(fn, initial=initial, restart=restart, trigger=trigger, recovery=recovery)
        except UpdateBusy:
            if db is None or recovery is None:
                raise
            return self._queue(recovery, initial)

    def _queue(self, recovery, initial):
        from .job_recovery import save, read
        from .update_tasks import _mutex, state_for
        db = self._database_path()
        with _mutex(state_for(db)), self.lock:
            if self.state is not None and self.state.get('status') == 'running':
                return copy.deepcopy(self.state)
            existing = read(db, self.task_key)
            job_id = existing.get('job_id') if existing.get('status') == 'active' else uuid.uuid4().hex
            if existing.get('status') != 'active':
                save(db, self.task_key, job_id, recovery, waiting=True)
            self.state = {self.id_key: job_id, 'status': 'running', 'run_id': None,
                          **(initial or {}), 'waiting_for_update': True,
                          'stage': '等待安装完成后继续', 'error': '',
                          'message': '任务将在安装完成后自动继续。'}
            return copy.deepcopy(self.state)

    def _start(self, fn: Callable[[str], None], *, initial: dict | None = None,
               restart: bool = False, trigger: str = "manual", recovery: dict | None = None) -> dict:
        """跑一轮，返回启动后（或原有）状态的深拷贝。

        已经在跑，或者已经有结果而调用方没要求 `restart`，都原样返回现状——重复点击
        不该把一轮跑到一半的检查丢掉。`fn` 收到本轮的任务 id，自己负责把 `status`
        推到 `complete`：完成时要落什么结果字段是域的事。

        `trigger` 进 `task_run`。手动触发撞上表里还占着同一把互斥键的那一轮时抛
        `TaskRunConflict`（API 翻成 409）：那一轮不是这个进程开的——命令行脚本，或者
        上一个进程留下的——所以本类自己的锁看不见它，只有表看得见。
        """
        thread = None
        job_id = ""
        with self.lock:
            state = self.state
            if state is not None and state.get('waiting_for_update'):
                state = None
            previous = state
            if state is None or (restart and state["status"] != "running"):
                from .job_recovery import current_job_id
                job_id = current_job_id.get() or uuid.uuid4().hex
                state = {self.id_key: job_id, "status": "running", "run_id": None,
                         "started_at": time.time(), "error": "", **(initial or {})}
                state.update(self._resuming_fields(job_id, initial))
                self.state = state
                thread = threading.Thread(target=self._run, args=(fn, job_id),
                                          daemon=True, name=self.name)
                self.thread = thread
            snapshot = copy.deepcopy(state)
        if thread is None:
            # 没开新一轮。对定时触发来说这就是一次跳过，要留下记录——「刚才那轮为什么
            # 没跑」只有这一条答案。手动触发拿回的是在跑的那一轮本身，不是跳过。
            if (self.runs is not None and trigger != "manual"
                    and snapshot.get("status") == "running"):
                self.runs.start(self.task_key, trigger=trigger,
                                mutex_key=self.mutex_key, conflict="skip")
            return snapshot
        # 开 task_run 与起线程都在锁外：前者要拿数据库的写锁，后者是这个类的老约定。
        # 先给被顶掉的那一轮收尾再开新的：它占着同一把互斥键，不收就是新的一轮开不了，
        # 而它自己的线程要等到 `fn` 返回才走到结算，那可能是几分钟以后。
        if previous is not None and previous[self.id_key] != job_id:
            self._settle_from(previous[self.id_key], previous, superseded=True)
        try:
            self._open_run(job_id, trigger, snapshot)
            if (db := self._database_path()) is not None:
                from .update_tasks import register
                self._activity_files[job_id] = register(db, self.task_key)
                if recovery is not None:
                    from .job_recovery import save
                    save(db, self.task_key, job_id, recovery, run_id=self._run_ids.get(job_id))
                    self._recoverable.add(job_id)
                    from .job_recovery import checkpoint
                    checkpoint(db, self.task_key, job_id, self.snapshot() or snapshot)
        except Exception:
            self._close_run(job_id, 'failed', error='任务未能启动')
            activity = self._activity_files.pop(job_id, None)
            if activity is not None:
                activity.unlink(missing_ok=True)
            # 线程还没起，这一轮什么都没做过，撤干净再把冲突交出去。
            with self.lock:
                if self.state is not None and self.state[self.id_key] == job_id:
                    self.state = None
                    self.thread = None
            raise
        thread.start()
        return self.snapshot() or snapshot

    def _resuming_fields(self, job_id, initial):
        """恢复原任务的持久进度，当前请求仍使用自己的同步回执标识。"""
        from .job_recovery import current_job_id, read
        db = self._database_path()
        if not current_job_id.get() or db is None:
            return {}
        saved = read(db, self.task_key)
        if saved.get('job_id') != job_id:
            return {}
        fields = {**(saved.get('checkpoint') or {}), 'status': 'running', 'error': ''}
        if initial and 'request_id' in initial:
            fields['request_id'] = initial['request_id']
        return fields

    def _open_run(self, job_id: str, trigger: str, state: dict) -> None:
        if self.runs is None:
            return
        total = state.get("total")
        run = self.runs.start(
            self.task_key, trigger=trigger, mutex_key=self.mutex_key,
            conflict="raise" if trigger == "manual" else "skip",
            total=int(total) if isinstance(total, (int, float)) else None,
            label=self._progress_label(state))
        if run is None:
            return
        self._run_ids[job_id] = run.id
        self.update(job_id, run_id=run.id)

    def _close_run(self, job_id: str, status: str, *, error: str = "",
                   summary: dict | None = None) -> None:
        with self._close_lock:
            run_id = self._run_ids.pop(job_id, None)
            if self.runs is None or run_id is None:
                return
            self.runs.finish(run_id, status, summary=summary, error=error)
        self.runs.prune(self.task_key)

    def stop(self, timeout: float | None = 2.0, *, resume: bool = True) -> None:
        """丢掉状态并等线程收工，用于服务关停。

        线程是 daemon，本来也挡不住进程退出；清状态的意义是让在途的 worker 下一次
        拿锁时发现自己已被顶掉，从而安静返回，而不是在解释器拆卸期间继续查库、
        往一个没人读的状态里写进度。等不到就不等——一次外部 HTTP 探测可以是十几秒，
        关停不该被它拖住。

        `resume=True` 保留可恢复任务与原运行记录，线程退出后交回恢复入口。
        `resume=False` 取消任务并删除恢复记录。
        """
        from .update_tasks import _mutex, state_for
        db = self._database_path()
        with _mutex(state_for(db)) if db is not None else nullcontext():
            if not resume and db is not None:
                from .job_recovery import cancel
                cancel(db, self.task_key)
            with self.lock:
                state = self.state
                job_id = state[self.id_key] if state is not None else ""
                self.state = None
                thread = self.thread
                self.thread = None
            if job_id and state is not None and state["status"] in ('running', 'pausing', 'paused'):
                self._stop_run(job_id, resume=resume)
        if thread is not None and thread.is_alive():
            thread.join(timeout)

    def _stop_run(self, job_id, *, resume):
        if resume and job_id in self._recoverable:
            self._paused_stops.add(job_id)
            run_id = self._run_ids.pop(job_id, None)
            if self.runs is not None and run_id is not None:
                self._suspend_run(job_id, run_id)
            return
        self._close_run(job_id, 'cancelled', error='服务关停，这一轮没有跑完')

    def _suspend_run(self, job_id, run_id):
        from .processing_control import user_paused
        if self.task_key == 'library-processing' and user_paused(self._database_path(), job_id):
            self.runs.suspend(run_id, label='已暂停，等待继续')
        else:
            self.runs.suspend(run_id)

    def start_result(self, fn: Callable[[], dict], *, recovery: dict | None = None) -> dict:
        """执行一次操作并保存终态回执；重复提交只返回进行中的同一任务。"""
        def work(job_id):
            result = fn()
            if not result.get('ok', True):
                from .error_reporting import event
                event('background-result', job_id=job_id, task_key=self.task_key,
                      message=result.get('error') or result.get('message') or '任务失败')
            self.update(job_id, **result, status=(
                "complete" if result.get("ok", True) else "failed"),
                completed_at=time.time())
        return self.start(work, restart=True, recovery=recovery)

    def _run(self, fn: Callable[[str], None], job_id: str) -> None:
        from .update_tasks import current_db
        from .job_recovery import current_run, current_job_id
        parent_run = current_run.set(None)
        parent_job = current_job_id.set(None)
        context = current_db.set(self._database_path() if job_id in self._recoverable else None)
        try:
            from .update_tasks import check_pause
            check_pause(current_db.get())
            fn(job_id)
        except JobPaused:
            with self.lock:
                if self.state is not None and self.state.get(self.id_key) == job_id:
                    self.state.update(waiting_for_update=True, stage='等待安装完成后继续', error='',
                                      message='任务已暂停，安装完成后自动继续。')
        except Exception as error:   # 后台失败必须变成可轮询的状态，不能只留在日志里
            from .error_reporting import event
            event('background-job', error, job_id=job_id, task_key=self.task_key,
                  run_id=self._run_ids.get(job_id))
            # 抛出来就是没跑完：途中声明过的后继作废，结算时一条都不派。
            with self.lock:
                if self.state is not None and self.state.get(self.id_key) == job_id:
                    self.state.update(status='failed', error=f'{type(error).__name__}: {error}',
                                      followups=[], completed_at=time.time())
        finally:
            try:
                self._finish_execution(job_id)
            finally:
                current_db.reset(context)
                current_run.reset(parent_run)
                current_job_id.reset(parent_job)

    def _finish_execution(self, job_id):
        from .update_tasks import _mutex, state_for
        db = self._database_path()
        with _mutex(state_for(db)) if db is not None else nullcontext():
            try:
                self._settle(job_id)
                if job_id in self._paused_stops:
                    from .job_recovery import waiting
                    waiting(db, self.task_key, job_id)
                state = self.snapshot() or {}
                if (db is not None and job_id in self._recoverable
                        and state.get(self.id_key) == job_id and not state.get('waiting_for_update')):
                    from .job_recovery import checkpoint, finish
                    checkpoint(db, self.task_key, job_id, state)
                    finish(db, self.task_key, job_id,
                           'failed' if state.get('status') == 'failed' else 'complete')
            finally:
                activity = self._activity_files.pop(job_id, None)
                if activity is not None:
                    activity.unlink(missing_ok=True)
                self._recoverable.discard(job_id)
                self._paused_stops.discard(job_id)

    def _settle(self, job_id: str) -> None:
        """按域自己落下的终态结算 `task_run`。

        读的是这一轮自己的状态：被下一轮顶掉之后 `snapshot()` 讲的是别人的事，而
        被顶掉的这一轮同样要有人给它收尾，否则它会在表里一直挂着 `running`，
        把互斥键一起堵死到下次服务重启。
        """
        state = self.snapshot() or {}
        if state.get(self.id_key) == job_id and state.get('waiting_for_update'):
            from .job_recovery import waiting
            run_id = self._run_ids.pop(job_id, None)
            if self.runs is not None and run_id is not None:
                self._suspend_run(job_id, run_id)
            waiting(self._database_path(), self.task_key, job_id)
            return
        if state.get(self.id_key) != job_id:
            # 已经被 `start` 在顶替的那一刻收过尾了，这里再收一次是空操作。
            self._close_run(job_id, "cancelled", error="这一轮已被新的任务顶替")
            return
        self._settle_from(job_id, state)

    def _settle_from(self, job_id: str, state: dict, *,
                     superseded: bool = False) -> None:
        """按给定的那份状态结算。`superseded` 是「被下一轮顶替」的那条路。

        顶替发生在 `start` 里，那时旧状态已经被换掉，`snapshot()` 讲的是新一轮的事，
        所以这里收的是调用方手上那一份，不重新读。
        """
        status = state.get("status")
        if status == "failed":
            # 声明了后继就是结算到了底，只是留下了要人处理的项（ADR-0053）：后继照派。
            # 中途抛异常的那一轮走不到声明那一步，一条都不派。
            self._close_run(job_id, "failed", error=str(state.get("error") or "任务失败"),
                            summary={**self._summary(state),
                                     **self._dispatch_followups(job_id, state)})
            return
        if superseded and status == "running":
            # 还在跑就被顶掉：它的结果没人要了，记成取消而不是成功。
            self._close_run(job_id, "cancelled", error="这一轮已被新的任务顶替")
            return
        # `fn` 正常返回就是跑完了。域没有把状态推到 `complete` 只说明它不靠状态报结果
        # （`w_links` 这类直接返回 payload），不代表这一轮失败。
        self._close_run(job_id, "succeeded",
                        summary={**self._summary(state),
                                 **self._dispatch_followups(job_id, state)})

    def _dispatch_followups(self, job_id: str, state: dict) -> dict:
        """把这一轮声明的后继交给任务中心，返回要并进摘要的那几个数。

        成功收尾、以及结算到底但留下待处理项的那一轮都走到这里（ADR-0053）；派不派
        看这一轮有没有声明，中途中断的轮次声明不到。
        没入队的三种情况各占摘要里的一行——静默丢弃和没有上限一样，事后都查不出来。
        """
        from .task_runs import declared_rows

        run_id = self._run_ids.get(job_id)
        if self.runs is None or run_id is None:
            return {}
        rows = declared_rows(state.get("followups"))
        if not rows:
            return {}
        result = self.runs.enqueue_followups(run_id, rows)
        if self.followup_runner is not None:
            self.followup_runner.wake()
        summary: dict[str, object] = {"followups": len(result["queued"])}
        for field, label in (("duplicates", "followups_duplicate"),
                             ("truncated", "followups_truncated"),
                             ("depth_exceeded", "followups_depth_exceeded")):
            if result[field]:
                summary[label] = result[field]
        return summary


def job_main(build_parser, run, argv: list[str] | None = None) -> int:
    """长跑批处理脚本的统一入口收尾。

    解析参数、拿住 pid 锁、跑 `run(args)`；被磁盘或来源策略拦下时打印一行 `[stop]`
    并交出策略自己的退出码，而不是抛栈——批处理是无人值守跑的，退出码才是被读的那个。

    这段此前在 creator_boards、probe、sheets 三个脚本里逐字相同地各写了一份。它们
    共用的不只是形状，还有「pid 锁必须包住整个 run」和「策略异常不算崩溃」这两条
    约定；散成三份，下一个脚本照抄时漏掉锁或吞掉退出码都不会有人发现。
    """
    args = build_parser().parse_args(argv)
    from .update_tasks import UpdateBusy
    try:
        db = Path(getattr(args, 'db', Path(args.lock).resolve().parent / 'ledger.db'))
        with PidFileLock(args.lock):
            return _run_batch_with_updates(db, Path(args.lock).stem, run, args)
    except JobPolicyError as exc:
        print(f"[stop] {exc}")
        return exc.exit_code
    except UpdateBusy as exc:
        print(f"[stop] {exc}")
        return 3


def _run_batch_with_updates(db, label, run, args):
    from .update_tasks import activity, is_pausing, wait_for_installation, UpdateBusy
    while True:
        try:
            with activity(db, label):
                result = run(args)
            if result == 0 or not is_pausing(db):
                return result
        except (JobPaused, UpdateBusy):
            pass
        print('[pause] 任务进度已保留，安装完成后自动继续。')
        wait_for_installation(db)
