"""正常重启 Windows 托盘：发停止消息、等它自己退出、再启动并核对子服务。

绝不强杀。托盘退出时要自己关掉两个 `peach serve` 子进程，被强杀的话那两个进程会
变成没人管的孤儿，继续占着 80 和 443，下一份托盘于是永远起不来服务。
`swap_from` 让它在旧托盘退出后、新托盘启动前顺手换掉二进制，那是整条链路上唯一一个
目标文件没有进程持有的窗口。
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol

from .windows_update import replace_with_retry, swap_tray_binary
from .tray_detached import launch_detached


WM_STOP = 0x0400 + 10  # pystray._util.win32.WM_STOP

#: 停与起各一份的期限。正常一轮十来秒；机器忙着跑测试时新托盘光启动就可能过半分钟，
#: 而成功时一就绪就返回，期限放宽只拖慢真失败的那一次。
DEFAULT_TIMEOUT = 60.0


@dataclass(frozen=True)
class TrayWindow:
    process_id: int
    handle: int


@dataclass(frozen=True)
class RestartResult:
    ok: bool
    message: str
    old_tray_pid: int | None = None
    new_tray_pid: int | None = None
    service_pids: tuple[int, ...] = ()
    swapped_from: str | None = None
    backup: str | None = None
    #: 旧托盘停着的那段时间里做的准备（依赖同步）给人看的一句话；没做就是 None。
    preparation: str | None = None


def _normal_path(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def process_alive(process_id: int) -> bool:
    from .jobs import PidFileLock
    return PidFileLock._running(process_id)


def parse_windows_command_line(script: str | None) -> list[str]:
    """按 CommandLineToArgvW 的规则拆开一条命令行。

    托盘的自定义 bootstrap 里既有引号又有字面引号内的单引号，`shlex` 的
    posix 反斜杠规则在 Windows 上不成立，所以照 Win32 的三条规则自己劈：
    2n 个反斜杠后跟引号 → n 个反斜杠加一个定界引号；2n+1 个 → n 个反斜杠加字面
    引号；引号里再遇到一个引号，后面紧跟的还是引号时出一个字面引号，否则收界。
    分隔空白不带引号才算分隔，别把路径里的盘符斜杠拆成碎块。

    不直接调 shell32 的 CommandLineToArgvW，是为了让这一段在 macOS 上也跑得了
    测试：整个模块只在 Windows 上工作，但拆命令行是纯字符串的事，没有理由把它
    的判据锁死在一个平台上。
    """
    if not script:
        return []
    argv: list[str] = []
    current: list[str] = []
    quoted = False
    in_quotes = False
    index = 0
    while index < len(script):
        char = script[index]
        if char == "\\":
            count = 0
            while index < len(script) and script[index] == "\\":
                count += 1
                index += 1
            if index < len(script) and script[index] == '"':
                current.append("\\" * (count // 2))
                if count % 2:
                    # 奇数个反斜杠把这个引号转义成字面量，它不再收界。
                    current.append('"')
                    index += 1
            else:
                current.append("\\" * count)
            continue
        if char == '"':
            index += 1
            if in_quotes and index < len(script) and script[index] == '"':
                # 引号里的 "" 是一个字面引号，仍然在引号中。
                current.append('"')
                index += 1
                continue
            in_quotes = not in_quotes
            quoted = True
            continue
        if not in_quotes and char.isspace():
            if current or quoted:
                # `a "" b` 中间那个是真的空参数，不能因为没攒到字符就丢掉。
                argv.append("".join(current))
                current = []
                quoted = False
            index += 1
            continue
        current.append(char)
        index += 1
    if current or quoted:
        argv.append("".join(current))
    return argv


def _command_lines(process_ids: Iterable[int] = ()) -> dict[int, str]:
    """按 pid 读 Win32 进程的命令行，读不到就返回空。

    找源码托盘要认的就是这条命令行里的 `peach.tray`；失败不抛——这条
    路径宁可安静地确认「没有源码托盘」，也不误伤别的窗口。

    给了 pid 就只问这几个：这个函数起一个 PowerShell，而重启要在等待循环里
    反复问，整机枚举一轮几百个进程的代价会乘上轮数。
    """
    wanted = sorted({int(pid) for pid in process_ids})
    query = "Get-CimInstance Win32_Process"
    if wanted:
        clause = " or ".join(f"ProcessId={pid}" for pid in wanted)
        query = f"{query} -Filter '{clause}'"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         f"{query} | ForEach-Object {{ \"$($_.ProcessId)|$($_.CommandLine)\" }}"],
        capture_output=True, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        encoding="utf-8", errors="replace")
    lines: dict[int, str] = {}
    for raw in (result.stdout or "").splitlines():
        pid_text, _, command = raw.partition("|")
        try:
            lines[int(pid_text)] = command
        except ValueError:
            continue
    return lines


def find_source_tray_windows() -> tuple[TrayWindow, ...]:
    """源码形态的托盘：`pythonw -c …peach.tray…` 拉起的托盘窗口。

    `find_tray_windows` 只认打包入口（窗口类名加 dist 可执行路径）；源码部署
    的托盘是 venv 的 pythonw 启动的，桌面启动器和 `--show` 参数都在命令行里，
    所以按同一窗口类名加「命令行含 peach.tray 找」。返回的仍是一道窗口，跟
    restart_tray 用的是同一套唯一性检验。

    两步走：类名和映像路径都是纯 ctypes 的便宜判据，先用它们筛出候选，只有候选
    非空时才去问命令行。重启要在等待循环里反复调这个函数，而问命令行要起一个
    PowerShell——没有候选的那些轮次不该付这份钱。
    """
    if os.name != "nt":
        return ()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    paths, _parents = _process_paths_and_parents()
    candidates: list[TrayWindow] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

    @callback_type
    def visit(window, _extra):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
        process_id = int(process_id.value)
        image = paths.get(process_id, "")
        buffer = ctypes.create_unicode_buffer(512)
        user32.GetClassNameW(window, buffer, len(buffer))
        class_name = buffer.value
        if (class_name.startswith("Peach") and class_name.endswith("SystemTrayIcon")
                and image.lower().endswith("pythonw.exe")):
            candidates.append(TrayWindow(process_id, int(window)))
        return True

    ctypes.set_last_error(0)
    if not user32.EnumWindows(visit, 0):
        error = ctypes.get_last_error()
        if error:
            raise ctypes.WinError(error)
    if not candidates:
        return ()
    commands = _command_lines({window.process_id for window in candidates})
    return tuple(window for window in candidates
                 if "peach.tray" in commands.get(window.process_id, ""))


def _process_paths_and_parents() -> tuple[dict[int, str], dict[int, int]]:
    if os.name != "nt":
        raise OSError("Windows tray control is only available on Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    paths: dict[int, str] = {}
    parents: dict[int, int] = {}
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        more = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
        while more:
            process_id = int(entry.th32ProcessID)
            parents[process_id] = int(entry.th32ParentProcessID)
            handle = kernel32.OpenProcess(0x1000, False, process_id)
            if handle:
                try:
                    buffer = ctypes.create_unicode_buffer(32768)
                    length = wintypes.DWORD(len(buffer))
                    if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                        paths[process_id] = buffer.value
                finally:
                    kernel32.CloseHandle(handle)
            more = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)
    return paths, parents


def find_tray_windows(target: Path) -> tuple[TrayWindow, ...]:
    if os.name != "nt":
        return ()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    paths, _parents = _process_paths_and_parents()
    target_key = _normal_path(target)
    found: list[TrayWindow] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

    @callback_type
    def visit(window, _extra):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
        buffer = ctypes.create_unicode_buffer(512)
        user32.GetClassNameW(window, buffer, len(buffer))
        class_name = buffer.value
        if (class_name.startswith("Peach") and class_name.endswith("SystemTrayIcon")
                and _normal_path(paths.get(int(process_id.value), "")) == target_key):
            found.append(TrayWindow(int(process_id.value), int(window)))
        return True

    ctypes.set_last_error(0)
    if not user32.EnumWindows(visit, 0):
        error = ctypes.get_last_error()
        if error:
            raise ctypes.WinError(error)
    return tuple(found)


def post_stop(window_handle: int) -> bool:
    if os.name != "nt":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    return bool(user32.PostMessageW(window_handle, WM_STOP, os.getpid(), 0))


def _descends_from(process_id: int, ancestor: int, parents: dict[int, int]) -> bool:
    seen: set[int] = set()
    parent = parents.get(process_id, 0)
    while parent and parent not in seen:
        if parent == ancestor:
            return True
        seen.add(parent)
        parent = parents.get(parent, 0)
    return False


def owned_service_pids(tray_process_id: int, service_executable: Path) -> tuple[int, ...]:
    """这个托盘名下的服务入口进程。

    父进程号在父进程死后仍记在子进程身上，所以托盘退出后再问一次，拿到的就是它
    没收干净、还活着的那几个。
    """
    if os.name != "nt":
        return ()
    paths, parents = _process_paths_and_parents()
    service_key = _normal_path(service_executable)
    return tuple(sorted(
        process_id for process_id, path in paths.items()
        if _normal_path(path) == service_key
        and _descends_from(process_id, tray_process_id, parents)
    ))


def venv_holders(
    venv: Path,
    trees: Iterable[int],
    *,
    own_pid: int | None = None,
    snapshot: Callable[[], tuple[dict[int, str], dict[int, int]]] | None = None,
) -> tuple[int, ...]:
    """映像在项目 venv 里、又不归给定进程树管的进程。

    给定的树（旧托盘）连同祖先与子孙都不算：托盘退出时它们一起走。本进程和它的祖先也
    不算：重启脚本自己就是 venv 的 `python.exe` 启动器拉起来的，从托盘菜单发起时托盘
    还是它的祖先。venv 启动器拉起的基础解释器映像不在 venv 里，列出启动器就够认人了。
    """
    if snapshot is None:
        if os.name != "nt":
            return ()
        snapshot = _process_paths_and_parents
    paths, parents = snapshot()
    prefix = _normal_path(venv).rstrip("\\/") + os.sep
    roots = tuple(int(pid) for pid in trees)
    exempt: set[int] = set()
    for root in (os.getpid() if own_pid is None else own_pid, *roots):
        current, seen = root, set()
        while current and current not in seen:
            exempt.add(current)
            seen.add(current)
            current = parents.get(current, 0)
    exempt.update(pid for pid in paths
                  if any(_descends_from(pid, root, parents) for root in roots))
    return tuple(sorted(
        pid for pid, path in paths.items()
        if pid not in exempt and _normal_path(path).startswith(prefix)
    ))


def stray_service_pids(tray_process_id: int, service_executable: Path) -> tuple[int, ...]:
    """不在这个托盘名下的 `peach serve`：被强杀的托盘留下的孤儿就在这里面。

    只认命令行第一个参数是 `serve` 的：同一个 venv 里跑着的批处理 CLI 不占端口。
    """
    if os.name != "nt":
        return ()
    paths, parents = _process_paths_and_parents()
    service_key = _normal_path(service_executable)
    candidates = [
        process_id for process_id, path in paths.items()
        if _normal_path(path) == service_key
        and not _descends_from(process_id, tray_process_id, parents)
    ]
    if not candidates:
        return ()
    commands = _command_lines(candidates)
    return tuple(sorted(
        process_id for process_id in candidates
        if parse_windows_command_line(commands.get(process_id))[1:2] == ["serve"]
    ))


def _stop_old_tray(
    windows: tuple[TrayWindow, ...],
    old_process_id: int,
    service_executable: Path,
    *,
    timeout: float,
    stop_window: Callable[[int], bool],
    alive: Callable[[int], bool],
    services: Callable[[int, Path], tuple[int, ...]],
    strays: Callable[[int, Path], tuple[int, ...]],
    sleep: Callable[[float], None],
) -> RestartResult | None:
    """发停止消息，等旧托盘退出，再等它名下的子服务退净；哪一步不成立就返回失败。

    发消息之前先看端口是不是旧托盘的。它名下凑不齐两个子服务、旁边却有别的 `serve`
    在跑，就是有托盘被强杀过、那几个服务成了孤儿：这时重启托盘换不掉代码，新托盘见
    端口健康就不拉自己的，只会一直等到超时。

    旧托盘退出时自己会停子服务；这里等它们真的消失，新托盘才不会把正在退出的旧服务
    当成现成的。
    """
    owned = services(old_process_id, service_executable)
    if len(owned) < 2:
        foreign = strays(old_process_id, service_executable)
        if foreign:
            listed = "、".join(str(pid) for pid in foreign)
            return RestartResult(
                False, f"拒绝重启：旧托盘名下只有 {len(owned)} 个子服务，端口由不归它管的"
                       f" serve 进程占着（PID {listed}），重启托盘换不掉它们",
                old_tray_pid=old_process_id, service_pids=foreign)
    if not all(stop_window(window.handle) for window in windows):
        return RestartResult(False, "托盘停止消息发送失败", old_tray_pid=old_process_id)

    deadline = time.monotonic() + timeout
    while alive(old_process_id) and time.monotonic() < deadline:
        sleep(0.1)
    if alive(old_process_id):
        return RestartResult(False, "托盘未在期限内正常退出；未强杀、未另启",
                             old_tray_pid=old_process_id)
    while any(alive(pid) for pid in owned) and time.monotonic() < deadline:
        sleep(0.1)
    leftover = tuple(pid for pid in owned if alive(pid))
    if leftover:
        return RestartResult(False, "旧托盘已退出，但它的子服务未在期限内退出；未另启",
                             old_tray_pid=old_process_id, service_pids=leftover)
    return None


@dataclass(frozen=True)
class _Launch:
    """新托盘启动之后看到的样子。"""

    exit_code: int | None = None
    tray_pid: int | None = None
    service_pids: tuple[int, ...] = ()

    @property
    def ready(self) -> bool:
        return self.exit_code is None and len(self.service_pids) >= 2

    def failure(self) -> str:
        if self.exit_code is not None:
            return f"新托盘退出，代码 {self.exit_code}"
        if self.tray_pid is None:
            return "新托盘窗口未在期限内出现"
        return (f"新托盘（PID {self.tray_pid}）已起，但名下只有 {len(self.service_pids)} 个"
                "子服务：端口可能被不归它管的进程占着")


def _await_new_tray(
    launched: subprocess.Popen,
    find_windows: Callable[[], tuple[TrayWindow, ...]],
    service_executable: Path,
    *,
    timeout: float,
    services: Callable[[int, Path], tuple[int, ...]],
    sleep: Callable[[float], None],
) -> _Launch:
    """等新托盘建出唯一窗口并拥有两个子服务；期限到了返回最后看到的样子。"""
    deadline = time.monotonic() + timeout
    seen = _Launch()
    while time.monotonic() < deadline:
        if launched.poll() is not None:
            return _Launch(exit_code=launched.returncode)
        process_ids = {window.process_id for window in find_windows()}
        if len(process_ids) == 1:
            tray_pid = next(iter(process_ids))
            seen = _Launch(tray_pid=tray_pid,
                           service_pids=services(tray_pid, service_executable))
            if seen.ready:
                return seen
        sleep(0.2)
    return seen


def start_tray(target: Path) -> subprocess.Popen:
    environment = os.environ.copy()
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return launch_detached([str(target)], cwd=target.parent, env=environment)


def _rolled_back(
    backup: Path,
    target: Path,
    *,
    start: Callable[[Path], subprocess.Popen],
    sleep: Callable[[float], None],
    message: str,
    old_tray_pid: int | None,
    swapped_from: str | None,
) -> RestartResult:
    """新二进制起不来就把备份换回去再开一次，别把机器留在没有托盘的状态。

    备份先复制成临时名再 `os.replace`：直接把备份本身换回去的话，回滚成功了
    `dist/Peach/` 里那份可回退的副本也没了，下一次失败就无处可退。
    """
    restore = target.with_name(".Peach.rollback.exe")
    try:
        shutil.copy2(backup, restore)
        replace_with_retry(restore, target, sleep=sleep)
    except OSError as exc:
        return RestartResult(False, f"{message}；回滚失败，生产入口停在新二进制：{exc}",
                             old_tray_pid=old_tray_pid, swapped_from=swapped_from,
                             backup=str(backup))
    started = start(target)
    if started.poll() is not None:
        return RestartResult(False, f"{message}；已回滚到备份，但旧托盘也没起来",
                             old_tray_pid=old_tray_pid, swapped_from=swapped_from,
                             backup=str(backup))
    return RestartResult(False, f"{message}；已回滚到备份并重开旧托盘",
                         old_tray_pid=old_tray_pid, swapped_from=swapped_from,
                         backup=str(backup))


class Preparation(Protocol):
    """旧托盘停下前后各调一次的准备步骤（`runtime_prepare.RestartPreparation`）。

    `before_stop` 返回拒绝理由就什么都不动；`while_stopped` 在旧托盘与它的子服务都退净、
    新托盘还没起的窗口里执行，返回 (是否备齐, 给人看的一句话)。没备齐也照样起新托盘：
    别把机器留在没有托盘的状态，失败写进结果。
    """

    def before_stop(self, tray_pid: int) -> str | None: ...

    def while_stopped(self) -> tuple[bool, str | None]: ...


def _finished(ok: bool, message: str, prepared: tuple[bool, str | None],
              **fields) -> RestartResult:
    ready, note = prepared
    if ok and not ready:
        ok, message = False, f"{message}，但运行环境没有备齐：{note}"
    return RestartResult(ok, message, preparation=note, **fields)


def restart_tray(
    target: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    swap_from: Path | None = None,
    prepare: Preparation | None = None,
    find_windows: Callable[[Path], tuple[TrayWindow, ...]] = find_tray_windows,
    stop_window: Callable[[int], bool] = post_stop,
    alive: Callable[[int], bool] = process_alive,
    start: Callable[[Path], subprocess.Popen] = start_tray,
    services: Callable[[int, Path], tuple[int, ...]] = owned_service_pids,
    strays: Callable[[int, Path], tuple[int, ...]] = stray_service_pids,
    swap: Callable[[Path, Path], Path] = swap_tray_binary,
    sleep: Callable[[float], None] = time.sleep,
) -> RestartResult:
    target = target.resolve()
    service_executable = target.parents[2] / ".venv" / "Scripts" / "peach.exe"
    if not target.is_file() or target.name.lower() != "peach.exe":
        return RestartResult(False, f"拒绝重启：入口不存在或不是 Peach.exe：{target}")
    if not service_executable.is_file():
        return RestartResult(False, f"拒绝重启：服务入口不存在：{service_executable}")

    windows = find_windows(target)
    process_ids = {window.process_id for window in windows}
    if len(process_ids) != 1 or not windows:
        return RestartResult(False, "拒绝重启：没有找到唯一且路径匹配的 Peach 托盘窗口")
    old_process_id = next(iter(process_ids))
    refusal = prepare.before_stop(old_process_id) if prepare is not None else None
    if refusal:
        return RestartResult(False, refusal, old_tray_pid=old_process_id)
    stopped = _stop_old_tray(
        windows, old_process_id, service_executable, timeout=timeout,
        stop_window=stop_window, alive=alive, services=services, strays=strays, sleep=sleep)
    if stopped is not None:
        return stopped

    prepared = prepare.while_stopped() if prepare is not None else (True, None)
    backup: Path | None = None
    if swap_from is not None:
        try:
            backup = swap(swap_from.resolve(), target)
        except (OSError, ValueError) as exc:
            started = start(target)
            message = f"换二进制失败，已按原样重开旧托盘：{exc}"
            if started.poll() is not None:
                message = f"换二进制失败，且旧托盘未能重开：{exc}"
            return RestartResult(False, message, old_tray_pid=old_process_id,
                                 swapped_from=str(swap_from))

    swapped_from = str(swap_from) if swap_from is not None else None
    backup_path = str(backup) if backup is not None else None
    # 停和起各拿一份完整预算：换一份 40 MB 的 EXE 要几秒，那几秒不该从新托盘的
    # 启动窗口里扣掉，否则带 --swap-from 的这条路比不带的更容易假超时。
    launch = _await_new_tray(
        start(target), lambda: find_windows(target), service_executable,
        timeout=timeout, services=services, sleep=sleep)
    if launch.ready:
        return _finished(
            True, "托盘已正常重启并重新拥有 HTTP/HTTPS 子服务", prepared,
            old_tray_pid=old_process_id, new_tray_pid=launch.tray_pid,
            service_pids=launch.service_pids, swapped_from=swapped_from,
            backup=backup_path,
        )
    if backup is not None:
        return _rolled_back(
            backup, target, start=start, sleep=sleep, message=launch.failure(),
            old_tray_pid=old_process_id, swapped_from=swapped_from)
    return _finished(False, launch.failure(), prepared, old_tray_pid=old_process_id,
                     new_tray_pid=launch.tray_pid, service_pids=launch.service_pids)


def restart_source_tray(
    *,
    timeout: float = DEFAULT_TIMEOUT,
    find_windows: Callable[[], tuple[TrayWindow, ...]] = find_source_tray_windows,
    stop_window: Callable[[int], bool] = post_stop,
    alive: Callable[[int], bool] = process_alive,
    command_lines: Callable[[], dict[int, str]] = _command_lines,
    start: Callable[[list[str]], subprocess.Popen] | None = None,
    services: Callable[[int, Path], tuple[int, ...]] = owned_service_pids,
    strays: Callable[[int, Path], tuple[int, ...]] = stray_service_pids,
    sleep: Callable[[float], None] = time.sleep,
    prepare: Preparation | None = None,
) -> RestartResult:
    """源码部署托盘的正常重启：按同一命令行自起新托盘。

    依据现有托盘自己的命令行重建 argv：`--show`／`--silent` 和 Peach 数据目录都在
    那一串里，照抄它们就不会把静默启动重启成开浏览器，或者反过来。入口
    是项目 `.venv` 的 `pythonw.exe`，服务入口由它旁推出
    `.venv\\Scripts\\peach.exe`。没有换二进制这一步，也不涉及备份回滚。
    """
    def start_argv(argv: list[str]) -> subprocess.Popen:
        if start is None:
            # `<项目根>\.venv\Scripts\pythonw.exe` 往上两级就是项目根。逐级取而不是
            # parents[2]：层级不够时 `.parent` 停在盘符，索引却会直接抛。
            working_directory = Path(argv[0]).parent.parent.parent
            return launch_detached(argv, cwd=working_directory)
        return start(argv)

    windows = find_windows()
    process_ids = {window.process_id for window in windows}
    if len(process_ids) != 1:
        return RestartResult(False, "拒绝重启：没有找到唯一且命令行匹配的源码托盘窗口")
    old_process_id = next(iter(process_ids))
    argv = parse_windows_command_line(command_lines().get(old_process_id))
    if len(argv) < 2 or "peach.tray" not in " ".join(argv):
        return RestartResult(False, "拒绝重启：托盘窗口的命令行里没有 peach.tray，"
                                    "照原样重启会起错东西",
                             old_tray_pid=old_process_id)
    service_executable = Path(argv[0]).parent / "peach.exe"
    if not service_executable.is_file():
        return RestartResult(False, f"拒绝重启：源码托盘旁没有服务入口 {service_executable}",
                             old_tray_pid=old_process_id)
    refusal = prepare.before_stop(old_process_id) if prepare is not None else None
    if refusal:
        return RestartResult(False, refusal, old_tray_pid=old_process_id)
    stopped = _stop_old_tray(
        windows, old_process_id, service_executable, timeout=timeout,
        stop_window=stop_window, alive=alive, services=services, strays=strays, sleep=sleep)
    if stopped is not None:
        return stopped

    prepared = prepare.while_stopped() if prepare is not None else (True, None)
    launch = _await_new_tray(
        start_argv(argv), find_windows, service_executable,
        timeout=timeout, services=services, sleep=sleep)
    if launch.ready:
        return _finished(True, "源码托盘已正常重启并重新拥有 HTTP/HTTPS 子服务", prepared,
                         old_tray_pid=old_process_id, new_tray_pid=launch.tray_pid,
                         service_pids=launch.service_pids)
    return _finished(False, launch.failure(), prepared, old_tray_pid=old_process_id,
                     new_tray_pid=launch.tray_pid, service_pids=launch.service_pids)
