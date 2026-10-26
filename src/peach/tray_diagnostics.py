"""诊断报告的 Windows 原生结果窗口。"""
from __future__ import annotations

import ctypes
import os
import struct
from ctypes import wintypes
from pathlib import Path


OPEN_DIRECTORY = 1001
OPEN_FILE = 1002
CLOSE = 2  # IDCANCEL：Esc 与标题栏关闭具有同一个结果。


def _template(path: Path) -> bytes:
    """按 winuser.h 的 DLGTEMPLATE / DLGITEMTEMPLATE 编码，控件按 DWORD 对齐。"""
    def text(value: str) -> bytes:
        return (value + '\0').encode('utf-16-le')

    # WS_POPUP | WS_CAPTION | WS_SYSMENU | DS_MODALFRAME | DS_CENTER | DS_SETFONT
    data = bytearray(struct.pack('<IIHhhhh', 0x80C808C0, 0, 5, 0, 0, 360, 136))
    data.extend(b'\0\0\0\0' + text('Peach 诊断报告') + struct.pack('<H', 9) + text('Segoe UI'))

    def control(identifier, class_id, label, x, y, width, height, style):
        data.extend(b'\0' * (-len(data) % 4))
        data.extend(struct.pack('<IIhhhhH', 0x50000000 | style, 0,
                                x, y, width, height, identifier))
        data.extend(struct.pack('<HH', 0xFFFF, class_id) + text(label) + b'\0\0')

    control(0xFFFF, 0x82, '诊断报告已保存，可打开目录或文件。', 12, 12, 336, 18, 0)
    # 只读多行 EDIT 支持选择、复制和垂直滚动，长路径不依赖标签折行。
    control(1003, 0x81, str(path), 12, 36, 336, 54, 0x00A10844)
    control(OPEN_DIRECTORY, 0x80, '打开目录', 108, 106, 76, 18, 0x10000)
    control(OPEN_FILE, 0x80, '打开文件', 190, 106, 76, 18, 0x10000)
    control(CLOSE, 0x80, '关闭', 272, 106, 76, 18, 0x10001)
    return bytes(data)


def _windows_choice(path: Path) -> int:
    # DLGPROC 返回 INT_PTR，WPARAM / LPARAM 与 HWND 均使用指针宽度。
    dialog_proc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM)
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.DialogBoxIndirectParamW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p,
                                           wintypes.HWND, dialog_proc, wintypes.LPARAM]
    user.DialogBoxIndirectParamW.restype = ctypes.c_ssize_t
    user.EndDialog.argtypes = [wintypes.HWND, ctypes.c_ssize_t]
    user.EndDialog.restype = wintypes.BOOL
    user.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetDlgItem.restype = wintypes.HWND
    user.SetFocus.argtypes = [wintypes.HWND]
    user.SetFocus.restype = wintypes.HWND

    @dialog_proc
    def procedure(hwnd, message, wparam, _lparam):
        if message == 0x110:  # WM_INITDIALOG
            user.SetFocus(user.GetDlgItem(hwnd, CLOSE))
            return 0
        if message == 0x10:  # WM_CLOSE
            user.EndDialog(hwnd, CLOSE)
            return 1
        if message == 0x111 and (wparam & 0xFFFF) in (OPEN_DIRECTORY, OPEN_FILE, CLOSE):
            user.EndDialog(hwnd, wparam & 0xFFFF)
            return 1
        return 0

    template = ctypes.create_string_buffer(_template(path))
    # Windows 在调用线程内运行此窗口的消息循环，并在 EndDialog 后销毁窗口。
    choice = user.DialogBoxIndirectParamW(None, template, None, procedure, 0)
    if choice == -1:
        raise ctypes.WinError(ctypes.get_last_error())
    return choice


def show_report(path: Path) -> None:
    """用户选择动作后关闭窗口，再交给系统默认应用打开路径。"""
    if os.name != 'nt':
        print(f'Peach 诊断报告：诊断报告已保存：{path}')
        return
    choice = _windows_choice(path)
    if choice == OPEN_DIRECTORY:
        os.startfile(path.parent)
    elif choice == OPEN_FILE:
        os.startfile(path)
