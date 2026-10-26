"""安装包装出的程序目录与 Peach 自己的更新、卸载、快捷方式之间的约定。

安装包（`scripts/installer/peach.iss`，Inno Setup）只放程序文件、开始菜单项和「应用和功能」
里的那一条；自更新、应用内卸载、开机自启和桌面图标仍归 Peach 自己管。两边靠三件事对上：

- 程序目录里有 `unins000.exe` 就是安装包装的，免安装包没有这个文件；
- 注册表项名由 `APP_ID` 推出，`.iss` 用同一个值，`tests/test_desktop_installer.py` 钉住两处；
- 安装包在覆盖安装与卸载前调 `Peach.exe --installer-stop` / `--installer-uninstall`，
  Inno Setup 调不到 Python，只能经由程序自己的入口停托盘、撤快捷方式。
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from . import distribution

APP_ID = ("A74968D7-59A0-4DE7-B288-079EF3E03ED7" if distribution.embedded_window()
          else "02D9748A-78F6-4866-AC75-918177194DAB")
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{{{APP_ID}}}_is1"
#: 开始菜单项的文件名，与 `.iss` 的 `[Icons]` 一致。
MENU_SHORTCUT = "Peach 独立窗口测试版.lnk" if distribution.embedded_window() else "Peach.lnk"
_UNINSTALLER = re.compile(r"unins\d{3}\.(?:exe|dat|msg)", re.IGNORECASE)


def installed(program: Path) -> bool:
    return (program / "unins000.exe").is_file()


def menu_shortcut() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs" / MENU_SHORTCUT


def carry_uninstaller(source: Path, destination: Path) -> None:
    """自更新整目录切换前，把卸载程序带进新目录。

    更新包是免安装 zip，里面没有 `unins000.*`；不带过去的话，切换之后「应用和功能」
    那一条指向一个已经不存在的卸载程序。
    """
    for path in source.iterdir():
        if path.is_file() and _UNINSTALLER.fullmatch(path.name):
            shutil.copy2(path, destination / path.name)


def record_version(version: str) -> None:
    """自更新完成后改写「应用和功能」里显示的版本；没有安装包那一条就什么都不做。"""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version)
    except FileNotFoundError:
        return


def _running_from(program: Path) -> list[int]:
    """仍从程序目录运行的进程，不含自己和自己的祖先。

    卸载时调用链是 `{app}\\unins000.exe` → 临时目录里的第二阶段 → 这个进程，第一阶段
    就住在程序目录里；把它算进来的话，清场会连同整棵进程树把卸载程序和自己一起结束。
    """
    from .windows_restart import _normal_path, _process_paths_and_parents
    prefix = _normal_path(program) + os.sep
    paths, parents = _process_paths_and_parents()
    own = {os.getpid()}
    parent = parents.get(os.getpid(), 0)
    while parent and parent not in own:
        own.add(parent)
        parent = parents.get(parent, 0)
    return [pid for pid, path in paths.items()
            if pid not in own and _normal_path(path).startswith(prefix)]


def stop(program: Path, *, timeout: float = 60) -> bool:
    """让这个目录里的托盘正常退出，再结束它没收干净、仍从程序目录运行的进程。

    托盘退出时会停掉自己名下的服务；被强杀过的托盘留下的 `serve` 和转码进程不归任何
    托盘管，只能按镜像路径清场，否则覆盖安装写不进被占用的 DLL。
    """
    from .windows_restart import find_tray_windows, post_stop
    for window in find_tray_windows(program / "Peach.exe"):
        post_stop(window.handle)
    deadline = time.monotonic() + timeout
    while find_tray_windows(program / "Peach.exe"):
        if time.monotonic() > deadline:
            return False
        time.sleep(.25)
    while (strays := _running_from(program)) and time.monotonic() < deadline:
        time.sleep(.5)
    for pid in strays:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    return not _running_from(program)


def remove_shortcuts(program: Path) -> None:
    """撤掉这份安装自己放的开机自启与桌面图标；属于另一份安装的不动。"""
    from . import desktop_startup, settings_file
    try:
        desktop_startup.save(settings_file.load_config(), enabled=False, silent=True, desktop=False,
                             executable=program / "Peach.exe")
    except ValueError:
        return


def main(action: str) -> int:
    """`--installer-stop` 与 `--installer-uninstall` 的入口，退出码给 Inno Setup 判断。"""
    if sys.platform != "win32" or not distribution.standalone():
        return 2
    program = Path(sys.executable).resolve().parent
    if not stop(program):
        return 1
    if action == "--installer-uninstall":
        remove_shortcuts(program)
    return 0


def prepare(program: Path, owner: int, error_file: Path) -> int:
    """安装器使用随包携带的运行时预检，程序目录里的旧版本无需支持此协议。"""
    from . import settings_file, update_tasks, job_recovery
    from .fsutil import atomic_write_text
    config = settings_file.load_config(project_root=program)
    db = config.directory('database') / 'ledger.db'
    token = None
    try:
        token = update_tasks.reserve(db, owner=owner)
        previous = job_recovery.legacy_library_request(db)
        update_tasks.wait_quiescent(db)
        if not stop(program):
            raise update_tasks.UpdateBusy('Peach 尚未退出，安装未完成。请稍后重试。')
        job_recovery.import_legacy_library(db, previous)
        return 0
    except Exception as exc:
        if token:
            update_tasks.release(db, token)
        atomic_write_text(error_file, str(exc))
        return 1


def prepare_install_tools(program: Path, cache: Path, error_file: Path, *, stage: Path | None = None) -> int:
    """在安装器临时目录备齐工具，不读取馆藏设置或修改现有安装。"""
    from . import __version__, standalone_update, update_packages
    from .fsutil import atomic_write_text
    import tempfile
    stage = (stage or Path(sys.executable).resolve().parent).resolve()
    try:
        def fetch(url, archive, size, checksum, progress):
            cached = cache / url.rsplit('/', 1)[-1]
            if cached.is_file():
                if cached.stat().st_size != size or update_packages.digest(cached) != checksum:
                    raise ValueError(f'本地工具包校验失败：{cached.name}。请重新下载或移走此文件后重试。')
                shutil.copy2(cached, archive)
                progress(size)
            else:
                standalone_update.download(url, archive, size, checksum, progress)

        release = {'latest_version': __version__,
                   'asset_url': f'https://github.com/peach-mitao/peach/releases/download/v{__version__}/package.zip'}
        with tempfile.TemporaryDirectory(prefix='peach-installer-tools-', dir=stage.parent) as folder:
            standalone_update.prepare_tools(stage, program, Path(folder).resolve(), release, {'total': 0, 'downloaded': 0},
                                            report=lambda data, **changes: data.update(changes), fetch=fetch)
        return 0
    except Exception as exc:
        atomic_write_text(error_file, f'媒体工具准备失败：{exc}')
        return 1


def finish_install(program: Path, owner: int) -> int:
    """安装完成或取消后撤销属于这个安装器的预约。"""
    from . import settings_file, update_tasks
    db = settings_file.load_config(project_root=program).directory('database') / 'ledger.db'
    update_tasks.release_owner(db, owner)
    return 0


def migrate_install(program: Path) -> int:
    """按实际数据目录迁移已有账本，空白安装不创建数据库。"""
    from . import settings_file
    from .cli import main as cli_main
    db = settings_file.load_config(project_root=program).directory('database') / 'ledger.db'
    if not db.is_file():
        return 0
    return cli_main(['migrate', 'upgrade', '--yes', '--db', str(db)])
