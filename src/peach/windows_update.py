from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .fsutil import atomic_write_text, replace_with_retry


log = logging.getLogger(__name__)

BACKUP_GLOB = "Peach.pre-source-sync-*.exe"
STAGING_DIRNAME = "source-sync-build"
KEEP_BACKUPS = 2

WINDOWS_TRAY_INPUTS = (
    "src/peach/",
    "scripts/build_app_entry.py",
    "scripts/build_windows.ps1",
    "scripts/replace_windows_tray.py",
    "web/",
    "migrations/",
    "resources/",
    "pyproject.toml",
)


def windows_tray_rebuild_required(changed_paths: tuple[str, ...]) -> bool:
    """The frozen Windows entry must carry every packaged runtime input."""
    return any(
        path == prefix or path.startswith(prefix)
        for path in changed_paths
        for prefix in WINDOWS_TRAY_INPUTS
    )


@dataclass(frozen=True)
class PendingWindowsUpdate:
    commit: str
    changed_paths: tuple[str, ...]


@dataclass(frozen=True)
class WindowsUpdatePreparation:
    state: str
    message: str


#: PyInstaller 单文件包解压目录的固定前缀，位于 `%TEMP%`。
ONEFILE_GLOB = "_MEI*"
#: 比这更新的解压目录可能属于正在启动的另一份进程，不碰。
ONEFILE_MIN_AGE = timedelta(days=1)


def sweep_onefile_extractions(
    bundle_root: str | None = getattr(sys, "_MEIPASS", None), *,
    now: datetime | None = None, min_age: timedelta = ONEFILE_MIN_AGE,
) -> tuple[Path, ...]:
    """删掉单文件包解压后没能自清的 `%TEMP%/_MEIxxxx` 目录，托盘每次启动调一次。

    PyInstaller 的单文件包每次启动都把自己解到临时目录，只在正常退出时删掉它；托盘被
    结束进程、断电或在替换过程中被接管时那份就一直留着，每份 40–80 MB，几周就以 GB 计。
    只认 `_MEIPASS` 所在目录里的同前缀兄弟目录：跳过自己，跳过修改时间不满 `min_age`
    的（可能是正在启动的另一份进程），删不掉的只记日志。源码运行没有 `_MEIPASS`，
    什么都不做。
    """
    if not bundle_root:
        return ()
    own = Path(bundle_root).resolve()
    moment = now or datetime.now()
    removed: list[Path] = []
    for entry in sorted(own.parent.glob(ONEFILE_GLOB)):
        if not entry.is_dir() or entry.resolve() == own:
            continue
        try:
            modified = datetime.fromtimestamp(entry.stat().st_mtime)
            if moment - modified < min_age:
                continue
            shutil.rmtree(entry)
        except OSError as exc:
            log.warning("单文件包解压残留未能删除 %s: %s", entry, exc)
            continue
        removed.append(entry)
    return tuple(removed)


def swap_tray_binary(
    staged: Path,
    target: Path,
    *,
    timeout: float = 45.0,
    now: Callable[[], datetime] = datetime.now,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """换掉生产托盘 EXE，先在原地留一份可回滚的备份，返回备份路径。

    调用方必须先让旧托盘正常退出：这个函数不发停止消息，也不判断谁还活着。
    备份名沿用 `BACKUP_GLOB`，托盘下次启动的 `sweep_artifacts` 才认得出它、
    才会按 `KEEP_BACKUPS` 收口；换个名字就会在 `dist/Peach/` 里越堆越多。
    """
    if not staged.is_file():
        raise FileNotFoundError(f"暂存包不存在：{staged}")
    if target.name.lower() != "peach.exe" or not target.is_file():
        raise ValueError(f"生产入口不存在或不是 Peach.exe：{target}")
    if os.path.normcase(os.path.abspath(staged)) == os.path.normcase(os.path.abspath(target)):
        raise ValueError("暂存包与生产入口是同一个文件")

    backup = target.with_name(f"Peach.pre-source-sync-{now().strftime('%Y%m%d-%H%M%S')}.exe")
    shutil.copy2(target, backup)
    try:
        replace_with_retry(staged, target, timeout=timeout, sleep=sleep)
    except OSError:
        backup.unlink(missing_ok=True)
        raise
    return backup


class WindowsUpdateInstaller:
    """Test an updated checkout and stage a recoverable Windows tray replacement."""

    def __init__(
        self,
        root: Path,
        *,
        state_dir: Path,
        log_dir: Path,
        current_executable: Path | None = None,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        powershell: str | None = None,
        frozen: bool | None = None,
        process_id: int | None = None,
    ) -> None:
        self.root = root.resolve()
        self.state_dir = state_dir.resolve()
        self.log_dir = log_dir.resolve()
        self.current_executable = Path(current_executable or sys.executable).resolve()
        self._run = run
        self._popen = popen
        self._powershell = powershell
        self._frozen = (
            bool(getattr(sys, "frozen", False)) and os.name == "nt"
            if frozen is None else frozen
        )
        self._process_id = os.getpid() if process_id is None else process_id
        self.pending_path = self.state_dir / "windows-source-sync.json"
        self.log_path = self.log_dir / "windows-source-sync.log"

    def mark_pending(self, commit: str, changed_paths: tuple[str, ...]) -> None:
        atomic_write_text(self.pending_path, json.dumps(
            {"commit": commit, "changed_paths": list(changed_paths)},
            ensure_ascii=False, indent=2,
        ))

    def pending(self) -> PendingWindowsUpdate | None:
        try:
            payload = json.loads(self.pending_path.read_text(encoding="utf-8"))
            commit = str(payload["commit"])
            changed = tuple(str(path) for path in payload["changed_paths"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return PendingWindowsUpdate(commit, changed)

    def clear_pending(self) -> None:
        self.pending_path.unlink(missing_ok=True)

    def sweep_artifacts(self, *, keep_backups: int = KEEP_BACKUPS) -> tuple[Path, ...]:
        """删掉更新流程留下、已经没人会读的产物，托盘每次启动调一次。

        每次更新都会在 EXE 旁边留一份 `Peach.pre-source-sync-<时间>.exe` 备份、在
        `state/source-sync-build/<commit>/` 留一份暂存构建，两者各约 40 MB。备份只留最新
        `keep_backups` 份；暂存目录只留待应用记录指着的那个提交——替换助手要等新托盘
        活过 3 秒才清掉待应用记录，所以刚换上来的那份到下一次启动才会被清。只认更新器
        自己的命名，`Peach.exe` 本体和手工放进目录的任何文件一律不碰；删不掉的只记日志，
        不影响启动。
        """
        removed: list[Path] = []
        backups = sorted(self.current_executable.parent.glob(BACKUP_GLOB))
        stale_count = max(len(backups) - max(keep_backups, 0), 0)
        for stale in backups[:stale_count]:
            try:
                stale.unlink()
            except OSError as exc:
                log.warning("旧托盘备份未能删除 %s: %s", stale, exc)
                continue
            removed.append(stale)

        pending = self.pending()
        staging_root = self.state_dir / STAGING_DIRNAME
        if staging_root.is_dir():
            for entry in sorted(staging_root.iterdir()):
                if not entry.is_dir():
                    continue
                if pending is not None and entry.name == pending.commit:
                    continue
                try:
                    shutil.rmtree(entry)
                except OSError as exc:
                    log.warning("暂存构建未能删除 %s: %s", entry, exc)
                    continue
                removed.append(entry)
        return tuple(removed)

    def _powershell_executable(self) -> str | None:
        if self._powershell:
            return self._powershell
        for name in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
            found = shutil.which(name)
            if found:
                return found
        known = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")
        return str(known) if known.is_file() else None

    def _run_logged(self, command: list[str], *, append: bool = True) -> int:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with self.log_path.open(mode, encoding="utf-8", errors="replace") as log:
            log.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] {' '.join(command)}\n")
            log.flush()
            result = self._run(
                command,
                cwd=str(self.root),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        return result.returncode

    def build_staged_tray(self, commit: str, *, shell: str | None = None) -> Path | None:
        """把当前检出打成一份托盘 EXE，暂存到 `state/source-sync-build/<commit>/`。

        不能就地构建 `dist/Peach/`：那份 `Peach.exe` 正被运行中的托盘持有，PyInstaller
        清理输出目录时会撞上 `WinError 5`。暂存目录的命名是 `sweep_artifacts` 认得的
        那一种，换名字它就清不掉。构建失败返回 None，原因在 `log_path` 里。
        """
        shell = shell or self._powershell_executable()
        if shell is None:
            return None
        staging_dir = self.state_dir / STAGING_DIRNAME / commit
        staging_dir.mkdir(parents=True, exist_ok=True)
        staged = staging_dir / "Peach.exe"
        staged.unlink(missing_ok=True)
        build_script = self.root / "scripts" / "build_windows.ps1"
        build_exit = self._run_logged([
            shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(build_script),
            "-OutputDirectory", str(staging_dir),
        ])
        if build_exit != 0 or not staged.is_file():
            return None
        return staged

    def packaged_migrations_pass(self, staged: Path) -> bool:
        """在换上生产入口之前，先让暂存包自己跑一次 `migrate status`。

        PyInstaller 漏掉 `migrations/` 的话包能构建、能启动，直到第一次开库才炸；
        那时旧二进制已经被换走了。这一步用真实的包在真实 Peach 数据目录上验证一遍。
        """
        reset_environment = os.environ.copy()
        reset_environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8", errors="replace") as log:
            validation = self._run(
                [str(staged), "migrate", "status"],
                cwd=str(self.root), stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, check=False, env=reset_environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        return validation.returncode == 0

    def prepare(
        self, commit: str, changed_paths: tuple[str, ...],
    ) -> WindowsUpdatePreparation:
        shell = self._powershell_executable()
        if shell is None:
            return WindowsUpdatePreparation(
                "failed", "代码已快进，但找不到 PowerShell，未运行测试或重启服务。",
            )

        test_script = self.root / "scripts" / "test.ps1"
        for attempt in range(3):
            test_exit = self._run_logged(
                [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(test_script),
                 "-Scope", "full", "-Fresh"],
                append=attempt > 0,
            )
            # test_runner 的 4 表示验证期间输入变化；最多三次，每次取得完整有效记录。
            if test_exit != 4:
                break
        if test_exit == 4:
            return WindowsUpdatePreparation(
                "failed", "测试期间代码或依赖持续变化，三次验证均未取得有效记录；请稍后重试。",
            )
        if test_exit != 0:
            return WindowsUpdatePreparation(
                "failed", "代码已快进，但完整测试失败；服务与托盘均未重启，请查看日志。",
            )

        if not windows_tray_rebuild_required(changed_paths):
            return WindowsUpdatePreparation("services", "代码与完整测试已通过。")

        if not self._frozen:
            return WindowsUpdatePreparation(
                "services-manual-tray",
                "代码与完整测试已通过；当前不是打包托盘，托盘进程需手动重开。",
            )

        if not self.current_executable.is_file():
            return WindowsUpdatePreparation(
                "failed", "代码与测试已通过，但找不到当前托盘 EXE，未执行替换。",
            )

        staged = self.build_staged_tray(commit, shell=shell)
        if staged is None:
            return WindowsUpdatePreparation(
                "failed", "代码与测试已通过，但新托盘构建失败；旧托盘和服务保持运行。",
            )
        if not self.packaged_migrations_pass(staged):
            return WindowsUpdatePreparation(
                "failed", "新托盘未通过打包迁移资源检查；旧托盘和服务保持运行。",
            )

        backup = self.current_executable.with_name(
            f"Peach.pre-source-sync-{datetime.now().strftime('%Y%m%d-%H%M%S')}.exe"
        )
        shutil.copy2(self.current_executable, backup)
        helper_python = self.root / ".venv" / "Scripts" / "pythonw.exe"
        helper_script = self.root / "scripts" / "replace_windows_tray.py"
        if not helper_python.is_file() or not helper_script.is_file():
            return WindowsUpdatePreparation(
                "failed", "新托盘已构建，但替换助手不完整；旧托盘和服务保持运行。",
            )

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        try:
            self._popen(
                [
                    str(helper_python), str(helper_script),
                    "--wait-pid", str(self._process_id),
                    "--staged", str(staged),
                    "--target", str(self.current_executable),
                    "--backup", str(backup),
                    "--pending", str(self.pending_path),
                    "--log", str(self.log_path),
                ],
                cwd=str(self.root), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                shell=False, creationflags=creationflags,
            )
        except OSError:
            return WindowsUpdatePreparation(
                "failed", "新托盘已构建，但替换助手启动失败；旧托盘和服务保持运行。",
            )
        return WindowsUpdatePreparation(
            "replace", "代码、完整测试和新托盘检查均已通过；正在安全替换并重启。",
        )
