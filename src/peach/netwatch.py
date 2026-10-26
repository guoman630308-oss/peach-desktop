"""macOS 网络变化事件订阅。

不轮询。系统在网络配置变化时会广播 Darwin 通知
`com.apple.system.config.network_change`（换 Wi-Fi、DHCP 换地址、插拔网线、VPN 起落
都会触发）。`notify_register_file_descriptor` 把它变成一个文件描述符，阻塞读就行——
没有定时器，也没有「30 秒才发现」的延迟。

只用 libSystem 的 notify(3)，不额外依赖 pyobjc-framework-SystemConfiguration。
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import select
import sys
import threading
from typing import Callable


LOGGER = logging.getLogger(__name__)

#: 系统在网络配置变化时广播的键。
NETWORK_CHANGE = b"com.apple.system.config.network_change"
_NOTIFY_STATUS_OK = 0


def _libsystem() -> ctypes.CDLL:
    path = ctypes.util.find_library("System") or "libSystem.dylib"
    library = ctypes.CDLL(path, use_errno=True)
    library.notify_register_file_descriptor.argtypes = [
        ctypes.c_char_p, ctypes.POINTER(ctypes.c_int),
        ctypes.c_int, ctypes.POINTER(ctypes.c_int),
    ]
    library.notify_register_file_descriptor.restype = ctypes.c_uint32
    library.notify_cancel.argtypes = [ctypes.c_int]
    library.notify_cancel.restype = ctypes.c_uint32
    return library


class NetworkChangeWatcher:
    """网络配置一变就回调一次。

    回调在自己的线程里跑，异常只记录不外抛——它挂掉不该带走整个托盘。
    非 macOS 上 `start()` 直接返回，`supported` 为 False。
    """

    def __init__(self, on_change: Callable[[], None], name: str = "PeachNetWatch"):
        self.on_change = on_change
        self.name = name
        self.supported = sys.platform == "darwin"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wake_read = -1
        self._wake_write = -1

    def start(self) -> bool:
        if not self.supported or self._thread is not None:
            return False
        # 自建一根管道用来叫醒 select，否则 stop() 要等到下一次网络事件才生效。
        self._wake_read, self._wake_write = os.pipe()
        self._thread = threading.Thread(target=self._loop, name=self.name, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._wake_write >= 0:
            try:
                os.write(self._wake_write, b"x")
            except OSError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)
        for descriptor in (self._wake_read, self._wake_write):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        self._wake_read = self._wake_write = -1

    def _loop(self) -> None:
        try:
            library = _libsystem()
        except OSError:
            LOGGER.exception("无法加载 libSystem，网络变化订阅不可用")
            return
        descriptor = ctypes.c_int(0)
        token = ctypes.c_int(0)
        status = library.notify_register_file_descriptor(
            NETWORK_CHANGE, ctypes.byref(descriptor), 0, ctypes.byref(token))
        if status != _NOTIFY_STATUS_OK:
            LOGGER.error("订阅网络变化失败，notify 状态 %s", status)
            return
        LOGGER.info("已订阅网络变化事件（%s）", NETWORK_CHANGE.decode())
        try:
            while not self._stop.is_set():
                readable, _, _ = select.select([descriptor.value, self._wake_read], [], [])
                if self._stop.is_set():
                    return
                if descriptor.value not in readable:
                    continue
                # 事件本体是一个 4 字节的 token，读掉即可；内容不需要解析。
                try:
                    os.read(descriptor.value, 4)
                except OSError:
                    return
                try:
                    self.on_change()
                except Exception:
                    LOGGER.exception("网络变化回调失败")
        finally:
            library.notify_cancel(ctypes.c_int(token.value))
