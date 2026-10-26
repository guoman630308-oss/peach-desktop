"""子服务起来之前备齐运行环境：项目 venv 与 `uv.lock` 一致，账本迁移到当前结构。

两步都只在子服务停着时做（ADR-0091）。

**依赖。** 判据与动作是同一串参数 `UV_SYNC`，前者多一个 `--check`（只读）：

- `--no-install-project` 把 editable 的 peach 本身排除在外。它的 `cache-keys` 含
  `README.md` 与 `src/peach/__init__.py`，uv 按修改时间判，几乎每次合入都会判成要重装，
  而重装要改写 `Scripts\\peach.exe`；源码本来就按 editable 直接读，不需要重装。
- `--inexact` 不删锁文件之外的包：主检出的 venv 由 `python -m venv` 建，自带 pip。
- Windows 上被进程加载的 `.pyd` 不能替换，托盘自己就加载着 PIL 与 pystray。所以托盘
  进程里只做只读检查；真同步由 `scripts/restart_windows_tray.py` 在旧托盘退出、新托盘
  启动之间执行，那时候用这个 venv 的只剩脚本自己，而脚本只加载标准库与本包的纯 Python
  模块。本模块因此也只能 import 标准库。

**迁移。** 只有本机是账本写入端才迁，判据与 `library_processing._require_writer` 相同：
复制关掉时按独立写者；开着时 `device-id` 必须等于 `sync.writer_device`。用子进程跑 venv 的
`peach migrate`，拿到的一定是检出里的新代码；备份由 `peach migrate upgrade` 自带
（`ledger.pre-migrate-<时间>.db`），清退归 `peach.ledger_backups`。

**重启闸门。** 重启前看本机还活着的任务：后继由 `requeue_followups` 重排、定时检查到点
再跑，这两类能续上；其余重启后只会被 `recover_interrupted` 标成 interrupted。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .fsutil import atomic_write_text


#: 判据与动作共用的参数；判据只多一个 `--check`。
UV_SYNC = ("sync", "--locked", "--all-extras", "--inexact", "--no-install-project")
#: 托盘启动后把这次准备的结果落在 `<Peach 数据目录>/state/` 下，重启脚本据此报告新托盘的状况。
STATE_FILE = "runtime-prepare.json"
#: 子进程输出只留尾巴进通知与日志：uv 与 migrate 出错时有用的都在最后几行。
OUTPUT_TAIL = 600
#: 能续跑的触发方式。后继另按 `followup_key` 判，不在这里。
RESUMABLE_TRIGGERS = ("scheduled",)

Run = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class Step:
    """一步准备的结果。`state` 取 skipped / current / stale / synced / applied / failed。"""

    name: str
    state: str
    message: str

    @property
    def failed(self) -> bool:
        return self.state == "failed"

    @property
    def problem(self) -> bool:
        """要让人知道的结果：失败，或依赖还没跟上锁文件。"""
        return self.state in ("failed", "stale")


def _tail(completed: subprocess.CompletedProcess) -> str:
    parts = [str(part).strip() for part in (completed.stdout, completed.stderr) if part]
    text = "\n".join(part for part in parts if part)
    return text[-OUTPUT_TAIL:] if text else f"退出码 {completed.returncode}，没有输出"


def _quiet() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def find_uv(*, which: Callable[[str], str | None] = shutil.which,
            environ: Mapping[str, str] | None = None) -> Path | None:
    """先找 PATH，再找 winget 的安装目录：工具 shell 与开机自启的托盘常缺 WinGet Links。"""
    found = which("uv")
    if found:
        return Path(found)
    local = (os.environ if environ is None else environ).get("LOCALAPPDATA")
    if local:
        packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        for candidate in sorted(packages.glob("astral-sh.uv*/uv.exe")):
            if candidate.is_file():
                return candidate
    return None


class Dependencies:
    """项目 venv 与 `uv.lock` 的一致性。`check` 只读，`sync` 真装。"""

    def __init__(self, root: Path, *, run: Run = subprocess.run,
                 which: Callable[[str], str | None] = shutil.which,
                 environ: Mapping[str, str] | None = None, frozen: bool | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.root = Path(root)
        self._run = run
        self._which = which
        self._environ = dict(os.environ if environ is None else environ)
        self._frozen = getattr(sys, "frozen", False) if frozen is None else frozen
        self._clock = clock

    def _skip(self) -> tuple[str | None, Path | None]:
        if self._frozen:
            return "打包进程不加载项目 venv，依赖由重启脚本核对", None
        if not (self.root / "uv.lock").is_file():
            return f"没有 uv.lock：{self.root}", None
        if not (self.root / ".venv" / "pyvenv.cfg").is_file():
            return f"没有项目 venv：{self.root / '.venv'}", None
        uv = find_uv(which=self._which, environ=self._environ)
        if uv is None:
            return "未取得 uv：PATH 与 winget 安装目录里都没有", None
        return None, uv

    def _uv(self, uv: Path, *extra: str) -> subprocess.CompletedProcess:
        environment = dict(self._environ)
        # 调用方自己的 venv 不能顶替项目的 `.venv`。
        for key in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
            environment.pop(key, None)
        environment.update(NO_COLOR="1", UV_NO_PROGRESS="1")
        return self._run(
            [str(uv), *UV_SYNC, *extra, "--project", str(self.root)],
            cwd=str(self.root), capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=environment, check=False, **_quiet())

    def check(self) -> Step:
        reason, uv = self._skip()
        if uv is None:
            return Step("dependencies", "skipped", reason or "")
        try:
            completed = self._uv(uv, "--check")
        except OSError as exc:
            return Step("dependencies", "failed", f"uv 没能运行：{exc}")
        if completed.returncode == 0:
            return Step("dependencies", "current", "项目 venv 与 uv.lock 一致")
        output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
        if "outdated" not in output:
            return Step("dependencies", "failed", f"核对依赖失败：{_tail(completed)}")
        wanted = [line.strip()[2:] for line in output.splitlines()
                  if line.strip().startswith("+ ")]
        listed = "、".join(wanted[:6]) + ("等" if len(wanted) > 6 else "")
        return Step("dependencies", "stale",
                    f"项目 venv 与 uv.lock 不一致，要装 {len(wanted)} 个包" + (f"：{listed}" if listed else ""))

    def sync(self) -> Step:
        checked = self.check()
        if checked.state != "stale":
            return checked
        _reason, uv = self._skip()
        started = self._clock()
        try:
            completed = self._uv(uv)
        except OSError as exc:
            return Step("dependencies", "failed", f"uv 没能运行：{exc}")
        if completed.returncode != 0:
            return Step("dependencies", "failed", f"uv sync 失败：{_tail(completed)}")
        return Step("dependencies", "synced",
                    f"已按 uv.lock 同步依赖（{checked.message}，用时 {self._clock() - started:.1f} 秒）")


def ledger_writer(*, replication_enabled: bool, db: Path, shared_db: Path,
                  state_dir: Path) -> tuple[bool, str]:
    """本机是不是账本写入端。只读：不像 `sync.device_id` 那样在缺文件时写一个新标识。"""
    if not replication_enabled:
        return True, "replication.enabled = false，按独立写者"
    path = Path(state_dir) / "device-id"
    try:
        device = path.read_text(encoding="utf-8").strip()
    except OSError:
        device = ""
    if not device:
        return False, "本机没有 device-id，判不出是不是写入端"
    from .sync import writer_device
    owner = writer_device(Path(db), Path(shared_db))
    if owner is None:
        return False, "没有可验证的写入端"
    if owner != device:
        return False, f"写入端是 {owner}，本机是只读端"
    return True, "本机是写入端"


class Migrations:
    """用 venv 的 `peach migrate` 子进程把账本迁到当前结构。"""

    def __init__(self, db: Path, *, peach: Callable[[], Path],
                 writer: Callable[[], tuple[bool, str]], run: Run = subprocess.run,
                 environ: Callable[[], Mapping[str, str]] | None = None) -> None:
        self.db = Path(db)
        self._peach = peach
        self._writer = writer
        self._run = run
        self._environ = environ or (lambda: os.environ)

    def _migrate(self, executable: Path, *action: str) -> subprocess.CompletedProcess:
        environment = dict(self._environ())
        # 子进程的 stdout 被管道捕获时默认走 ANSI 代码页，中文会变成替换字符。
        environment["PYTHONIOENCODING"] = "utf-8"
        return self._run(
            [str(executable), "migrate", *action, "--db", str(self.db)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=environment, check=False, **_quiet())

    def apply(self) -> Step:
        if not self.db.is_file():
            return Step("migrations", "skipped", f"数据库不存在：{self.db}")
        writer, why = self._writer()
        if not writer:
            return Step("migrations", "skipped", f"不迁移：{why}")
        try:
            executable = self._peach()
            status = self._migrate(executable, "status")
        except OSError as exc:
            return Step("migrations", "failed", f"迁移没能启动：{exc}")
        if status.returncode != 0:
            return Step("migrations", "failed", f"migrate status 失败：{_tail(status)}")
        # `_migrate` 打的是 `migrations: <总数>, pending: <待迁移数>`。
        found = re.search(r"pending:\s*(\d+)", status.stdout or "")
        if found is None:
            return Step("migrations", "failed", f"migrate status 没有报待迁移数：{_tail(status)}")
        if int(found.group(1)) == 0:
            return Step("migrations", "current", "数据库结构已是最新")
        try:
            upgrade = self._migrate(executable, "upgrade", "--yes")
        except OSError as exc:
            return Step("migrations", "failed", f"迁移没能启动：{exc}")
        if upgrade.returncode != 0:
            return Step("migrations", "failed",
                        f"执行 {found.group(1)} 个待迁移时失败，数据库目录里有迁移前备份："
                        f"{_tail(upgrade)}")
        applied = re.search(r"^applied:\s*(.+)$", upgrade.stdout or "", re.MULTILINE)
        backup = re.search(r"^backup:\s*(.+)$", upgrade.stdout or "", re.MULTILINE)
        return Step("migrations", "applied",
                    f"已执行迁移 {applied.group(1).strip() if applied else found.group(1) + ' 个'}"
                    f"，迁移前备份 {backup.group(1).strip() if backup else '未取得'}")


def record(state_dir: Path, steps: Iterable[Step], *, pid: int | None = None) -> dict:
    """把托盘这次启动的准备结果写到 `<Peach 数据目录>/state/`，重启脚本读它来报告新托盘。"""
    items = list(steps)
    payload = {
        "pid": os.getpid() if pid is None else pid,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ok": not any(step.problem for step in items),
        "steps": [asdict(step) for step in items],
    }
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_text(directory / STATE_FILE, json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def read_record(state_dir: Path, *, pid: int) -> dict | None:
    """指定那个托盘写下的记录；没有、读不了或是别的进程写的都返回 None。"""
    try:
        payload = json.loads((Path(state_dir) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) and payload.get("pid") == pid else None


@dataclass(frozen=True)
class ActiveRun:
    id: int
    task_key: str
    trigger: str
    status: str
    pid: int | None
    followup_key: str | None

    def describe(self) -> str:
        return f"#{self.id} {self.task_key}（{self.trigger}，{self.status}，PID {self.pid}）"


@dataclass(frozen=True)
class TaskGate:
    """重启前本机还活着的任务，分成拦得住重启的与拦不住的。"""

    blocking: tuple[ActiveRun, ...] = ()
    resumable: tuple[ActiveRun, ...] = ()
    #: 行还停在活跃状态、进程却已不在：重启伤不到它，下次服务启动会把它标成 interrupted。
    stale: tuple[ActiveRun, ...] = ()
    error: str | None = None
    note: str = ""

    @property
    def refused(self) -> bool:
        return bool(self.blocking) or self.error is not None

    def refusal(self) -> str:
        if self.error is not None:
            return f"拒绝重启：读不了任务表（{self.error}）；确认没有任务在跑后加 --force"
        listed = "、".join(run.describe() for run in self.blocking)
        return f"拒绝重启：这些任务重启后续跑不了：{listed}；等它们结束，或加 --force"

    def as_dict(self) -> dict:
        return {
            "blocking": [asdict(run) for run in self.blocking],
            "resumable": [asdict(run) for run in self.resumable],
            "stale": [asdict(run) for run in self.stale],
            "error": self.error,
            "note": self.note,
        }


def task_gate(db: Path, *, host: str, alive: Callable[[int], bool]) -> TaskGate:
    """只读查 `task_run`，把本机的活跃行分类。"""
    db = Path(db)
    if not db.is_file():
        return TaskGate(note=f"数据库不存在：{db}")
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=2)) as connection:
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute(
                "SELECT id, task_key, trigger, status, pid, followup_key FROM task_run "
                "WHERE status IN ('pending','running') AND host=? ORDER BY id",
                (host,)).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc) or "no such column" in str(exc):
            return TaskGate(note=f"数据库里还没有可判的任务表：{exc}")
        return TaskGate(error=str(exc))
    except sqlite3.Error as exc:
        return TaskGate(error=str(exc))
    blocking: list[ActiveRun] = []
    resumable: list[ActiveRun] = []
    stale: list[ActiveRun] = []
    for row in rows:
        run = ActiveRun(int(row[0]), str(row[1]), str(row[2]), str(row[3]),
                        None if row[4] is None else int(row[4]), row[5])
        from .job_recovery import read
        recovery = read(db, run.task_key)
        can_resume = (recovery.get('status') == 'active' and recovery.get('run_id') == run.id
                      and isinstance(recovery.get('request'), dict))
        if run.followup_key or run.trigger in RESUMABLE_TRIGGERS or can_resume:
            resumable.append(run)
        elif run.pid is not None and not alive(run.pid):
            stale.append(run)
        else:
            blocking.append(run)
    return TaskGate(tuple(blocking), tuple(resumable), tuple(stale))


@dataclass
class RestartPreparation:
    """`windows_restart` 在旧托盘停下前后各调一次的准备步骤，只管依赖。

    迁移不在这里：新托盘启动时自己会跑，它的 Peach 数据目录与环境变量才是服务真正用的那一份。
    `holders(tray_pid)` 返回旧托盘那棵进程树以外、还在用项目 venv 的进程；要同步依赖时
    有这种进程就拒绝重启，因为它们加载着的 `.pyd` 换不掉，uv 会在半路失败。
    """

    dependencies: Dependencies
    holders: Callable[[int], tuple[int, ...]]
    checked: Step | None = None
    synced: Step | None = None

    def before_stop(self, tray_pid: int) -> str | None:
        self.checked = self.dependencies.check()
        if self.checked.state != "stale":
            return None
        holding = self.holders(tray_pid)
        if holding:
            listed = "、".join(str(pid) for pid in holding)
            return (f"拒绝重启：{self.checked.message}，但这些进程还在用项目 venv："
                    f"PID {listed}；先停掉它们再重启")
        return None

    def while_stopped(self) -> tuple[bool, str | None]:
        if self.checked is None or self.checked.state != "stale":
            return (self.checked is None or not self.checked.failed,
                    self.checked.message if self.checked else None)
        self.synced = self.dependencies.sync()
        return not self.synced.failed, self.synced.message
