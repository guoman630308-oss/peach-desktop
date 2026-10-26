"""随句柄关闭一并终止成员的 Windows Job Object。

父进程被强杀时不会走自己的收尾代码，子进程于是成了孤儿，继续占着端口或留在公网上转发。
把子进程挂进一个 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的 Job，句柄随父进程退出由内核关闭，
成员随之一并终止，不管父进程是怎么死的。托盘的 `peach serve` 子服务与服务里的
cloudflared 都用这一份。非 Windows 上三个函数都是空操作。
"""
from __future__ import annotations

import ctypes
import os

__all__ = ["assign_to_job", "close_job", "create_kill_on_close_job"]

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
#: JobObjectExtendedLimitInformation
_JOB_EXTENDED_LIMIT_CLASS = 9


def create_kill_on_close_job():
    """建一个随句柄关闭一并终止成员的 Job Object；非 Windows 或建不成时返回 None。

    句柄不可继承：子进程要是继承了它，Job 就要等到最后一个子进程退出才关，
    父进程死了成员也不会被收走。
    """
    if os.name != "nt":
        return None
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        wintypes.HANDLE(job), _JOB_EXTENDED_LIMIT_CLASS,
        ctypes.byref(limits), ctypes.sizeof(limits),
    ):
        kernel32.CloseHandle(wintypes.HANDLE(job))
        return None
    return job


def assign_to_job(job, process) -> None:
    """把一个 `subprocess.Popen` 挂进 Job；挂不上照样放行。

    内核拒绝嵌套 Job 时子进程仍然在跑，调用方自己的停止路径照旧收得掉它，
    只是少了强杀时的那层兜底。
    """
    if job is None or os.name != "nt":
        return
    handle = getattr(process, "_handle", None)
    if handle is None:
        return
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(handle)))
    except (OSError, ValueError, TypeError):
        pass


def close_job(job) -> None:
    """关掉 Job 句柄；这一下就会终止里面还活着的成员。"""
    if job is None or os.name != "nt":
        return
    from ctypes import wintypes

    try:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(wintypes.HANDLE(job))
    except (OSError, ValueError):
        pass
