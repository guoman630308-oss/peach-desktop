"""通过 Windows 桌面父身份启动常驻托盘，独立于启动器的私有 Job。"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

_CREATE_SUSPENDED = 0x00000004
_HANDOFF_ENV = "PEACH_TRAY_DESKTOP_HANDOFF"


class _JobBasicLimits(ctypes.Structure):
    _fields_ = [("times", ctypes.c_longlong * 2), ("flags", wintypes.DWORD),
                ("working_set", ctypes.c_size_t * 2), ("active_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        *[(name, wintypes.DWORD) for name in (
            "dwX", "dwY", "dwXSize", "dwYSize", "dwXCountChars", "dwYCountChars",
            "dwFillAttribute", "dwFlags")],
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", wintypes.LPVOID)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "OpenProcess": (wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
        "CloseHandle": (wintypes.BOOL, [wintypes.HANDLE]),
        "IsProcessInJob": (wintypes.BOOL, [wintypes.HANDLE, wintypes.HANDLE,
                                         ctypes.POINTER(wintypes.BOOL)]),
        "InitializeProcThreadAttributeList": (wintypes.BOOL, [wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]),
        "UpdateProcThreadAttribute": (wintypes.BOOL, [wintypes.LPVOID, wintypes.DWORD,
            ctypes.c_size_t, wintypes.LPVOID, ctypes.c_size_t, wintypes.LPVOID, wintypes.LPVOID]),
        "DeleteProcThreadAttributeList": (None, [wintypes.LPVOID]),
        "CreateProcessW": (wintypes.BOOL, [wintypes.LPCWSTR, wintypes.LPWSTR,
            wintypes.LPVOID, wintypes.LPVOID, wintypes.BOOL, wintypes.DWORD,
            wintypes.LPVOID, wintypes.LPCWSTR, ctypes.POINTER(_StartupInfoEx),
            ctypes.POINTER(_ProcessInfo)]),
        "ResumeThread": (wintypes.DWORD, [wintypes.HANDLE]),
        "QueryInformationJobObject": (wintypes.BOOL, [wintypes.HANDLE, ctypes.c_int,
            wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID]),
    }
    for name, (result, arguments) in signatures.items():
        function = getattr(kernel, name)
        function.restype, function.argtypes = result, arguments
    return kernel


def _in_job(kernel, handle, job=None) -> bool:
    result = wintypes.BOOL()
    if not kernel.IsProcessInJob(handle, job, ctypes.byref(result)):
        raise _windows_error("IsProcessInJob")
    return bool(result.value)


def _windows_error(operation: str) -> OSError:
    error = ctypes.WinError(ctypes.get_last_error())
    error.peach_operation = operation
    return error


def in_job() -> bool:
    if os.name != "nt":
        return False
    return _in_job(_kernel(), wintypes.HANDLE(-1))


def handoff_tray(arguments, root, *, module="peach.tray") -> bool:
    """托盘取得单实例锁前完成独立启动；保留参数、Peach 数据目录与源码路径。"""
    handed_off = os.environ.pop(_HANDOFF_ENV, None) == "1"
    if handed_off or not in_job():
        return False
    environment = os.environ.copy()
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if getattr(sys, "frozen", False):
        command = [sys.executable, *arguments]
    else:
        environment["PYTHONPATH"] = os.fspath(root / "src")
        command = [sys.executable, "-m", module, *arguments]
    child = launch_detached(command, cwd=root, env=environment)
    child.close()
    return True


class DetachedProcess:
    """持有真实进程句柄，提供启动验收所需的 poll/wait。"""

    def __init__(self, handle, pid):
        import _winapi
        self._api = _winapi
        self._handle, self.pid, self.returncode = handle, pid, None

    def poll(self):
        if self.returncode is None and self._handle is not None:
            if self._api.WaitForSingleObject(self._handle, 0) == self._api.WAIT_OBJECT_0:
                self.returncode = self._api.GetExitCodeProcess(self._handle)
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            milliseconds = self._api.INFINITE if timeout is None else max(0, int(timeout * 1000))
            if self._api.WaitForSingleObject(self._handle, milliseconds) == self._api.WAIT_TIMEOUT:
                raise subprocess.TimeoutExpired(str(self.pid), timeout)
            self.returncode = self._api.GetExitCodeProcess(self._handle)
        return self.returncode

    def terminate(self):
        self._api.TerminateProcess(self._handle, 1)

    def close(self):
        if self._handle is not None:
            self._api.CloseHandle(self._handle)
            self._handle = None

    def __del__(self):
        self.close()


def _job_facts(kernel, handle, *, current=False):
    """记录已知进程的 Job 归属；限制查询只适用于调用进程的直接 Job。"""
    facts = {}
    try:
        member = _in_job(kernel, handle)
        facts = {"in_job": member}
        if current and member:
            limits = _JobBasicLimits()
            if not kernel.QueryInformationJobObject(None, 2, ctypes.byref(limits), ctypes.sizeof(limits), None):
                raise _windows_error("QueryInformationJobObject(current)")
            facts["immediate_limit_flags"] = hex(limits.flags)
            facts["immediate_limit_names"] = [name for flag, name in (
                (0x0400, "DIE_ON_UNHANDLED_EXCEPTION"), (0x0800, "BREAKAWAY_OK"),
                (0x1000, "SILENT_BREAKAWAY_OK"), (0x2000, "KILL_ON_JOB_CLOSE"),
                (0x4000, "SUBSET_AFFINITY")) if limits.flags & flag]
        return facts
    except OSError as error:
        return {**facts, "status": "unavailable", "winerror": getattr(error, "winerror", None),
                "operation": getattr(error, "peach_operation", None)}


def _create_child(kernel, executable, argv, cwd, block, startup, flags, context):
    """按桌面父身份挂起创建，记录归属后恢复主线程。"""
    child = _ProcessInfo()
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline([os.fspath(a) for a in argv]))
    if not kernel.CreateProcessW(str(executable), command, None, None, False,
            flags | _CREATE_SUSPENDED, block, os.fspath(cwd),
            ctypes.byref(startup), ctypes.byref(child)):
        operation = "CreateProcessW(breakaway)" if flags & subprocess.CREATE_BREAKAWAY_FROM_JOB else "CreateProcessW"
        raise _windows_error(operation)
    process = DetachedProcess(child.hProcess, child.dwProcessId)
    verified = False
    try:
        context["child"] = {"pid": process.pid, **_job_facts(kernel, child.hProcess)}
        if kernel.ResumeThread(child.hThread) == 0xFFFFFFFF:
            raise _windows_error("ResumeThread")
        verified = True
        return process
    finally:
        kernel.CloseHandle(child.hThread)
        if not verified:
            try:
                process.terminate()
                process.wait(timeout=5)
            finally:
                process.close()


def launch_detached(argv, *, cwd, env=None) -> DetachedProcess:
    """由当前会话桌面进程承接父身份；失败明确报错，不回退到启动器 Job。"""
    if os.name != "nt":
        raise OSError("独立托盘启动仅适用于 Windows")
    environment = os.environ.copy() if env is None else dict(env)
    executable = Path(argv[0])
    config = executable.parent.parent / "pyvenv.cfg"
    if executable.name.lower() in ("python.exe", "pythonw.exe") and config.is_file():
        # CPython venv 启动器会把实际解释器放进自己的 kill-on-close Job。
        # 复用它的 __PYVENV_LAUNCHER__ 协议直接启动基础解释器，保留 venv 身份。
        values = dict(line.partition("=")[::2] for line in config.read_text(encoding="utf-8").splitlines()
                      if "=" in line)
        home = next((value.strip() for key, value in values.items() if key.strip() == "home"), None)
        if home is None:
            raise OSError(f"虚拟环境缺少基础解释器目录：{config}")
        environment["__PYVENV_LAUNCHER__"] = str(executable.resolve())
        executable = Path(home) / executable.name
    kernel = _kernel()
    context = {"caller": {"pid": os.getpid(), **_job_facts(kernel, wintypes.HANDLE(-1), current=True)},
               "parent_source": "Windows desktop",
               "job_chain": {"status": "unavailable", "reason": "未取得各层 Job 句柄与归属；仅记录已知进程和直接 Job"}}
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetShellWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    shell = user.GetShellWindow()
    pid = wintypes.DWORD()
    if not shell or not user.GetWindowThreadProcessId(shell, ctypes.byref(pid)):
        error = OSError("未取得当前会话的 Windows 桌面进程，请确认已登录 Windows 桌面")
        error.peach_context = context
        raise error
    context["desktop"] = {"pid": pid.value}
    parent = kernel.OpenProcess(0x0080 | 0x1000, False, pid.value)
    if not parent:
        error = _windows_error("OpenProcess(desktop)")
        error.peach_context = context
        raise error
    context["desktop"].update(_job_facts(kernel, parent))
    # 标记由桌面父身份创建的这一跳；托盘入口消费后不向普通子服务传播。
    # PyInstaller onefile 的引导进程会把环境传给实际应用进程。
    environment[_HANDOFF_ENV] = "1"
    initialized = False
    try:
        size = ctypes.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        if not kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)):
            raise _windows_error("InitializeProcThreadAttributeList")
        initialized = True
        parent_value = wintypes.HANDLE(parent)
        if not kernel.UpdateProcThreadAttribute(attributes, 0, 0x00020000,
                ctypes.byref(parent_value), ctypes.sizeof(parent_value), None, None):
            raise _windows_error("UpdateProcThreadAttribute(parent)")
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.lpDesktop = "winsta0\\default"
        startup.lpAttributeList = ctypes.cast(attributes, wintypes.LPVOID)
        block = ctypes.create_unicode_buffer("\0".join(
            f"{key}={value}" for key, value in sorted(environment.items(), key=lambda row: row[0].upper())
        ) + "\0\0")
        flags = 0x00080000 | 0x00000400 | subprocess.DETACHED_PROCESS
        try:
            process = _create_child(kernel, executable, argv, cwd, block, startup, flags, context)
        except OSError as error:
            if getattr(error, "winerror", None) != 5 or getattr(error, "peach_operation", "") != "CreateProcessW":
                raise
            context["retry"] = "CREATE_BREAKAWAY_FROM_JOB"
            process = _create_child(kernel, executable, argv, cwd, block, startup,
                                    flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, context)
        try:
            from .error_reporting import event
            event("tray-detached", context=context)
        except Exception:
            pass
        return process
    except OSError as error:
        error.peach_context = context
        raise
    finally:
        if initialized:
            kernel.DeleteProcThreadAttributeList(attributes)
        kernel.CloseHandle(parent)
