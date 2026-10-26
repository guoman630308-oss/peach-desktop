"""让运行 Peach 的这台电脑弹系统自带的文件夹对话框，把选中的绝对路径交回页面。

浏览器自己拿不到本机绝对路径：`webkitdirectory` 只给相对名，File System Access API 只给
句柄。首启页和配置页要写的偏偏就是绝对路径。Peach 与浏览器在同一台机器上，所以由服务
进程代为弹窗：Windows 走 Shell 的 `IFileOpenDialog`（带地址栏、能直接粘路径的那种
选择框，`FOS_PICKFOLDERS`），macOS 走 AppleScript 的 `choose folder`。两者都是系统自带，
不为一个对话框引入 tkinter 或 GUI 框架。

调用方只在回环地址上开放这条路：否则局域网里任何人都能让这台电脑弹窗。

Windows 这里刻意用 `powershell.exe`（Windows PowerShell 5.1）：它在每台 Windows 上都有，
pwsh 7 是另装的。它自带的 `FolderBrowserDialog` 是 XP 时代的树形面板、没有地址栏，所以
用 `Add-Type` 编一段 COM 互操作直接调 `IFileOpenDialog`。脚本以 `-EncodedCommand` 传入，
避开引号与中文的转义。
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import threading

__all__ = ["PickerBusy", "PickerUnavailable", "command", "pick_folder", "pick_file"]


class PickerUnavailable(RuntimeError):
    """这台机器上没有可用的文件夹对话框，或者对话框没能打开。"""


class PickerBusy(RuntimeError):
    """已经有一个对话框开着：系统对话框是模态的，第二个只会藏在第一个后面。"""


#: 一个用户一次只会看一个对话框；第二个请求直接拒绝，不排队。
_LOCK = threading.Lock()

#: 用户在对话框里翻多久都行，但进程不能被一个忘了关的窗口永久占住。
DIALOG_TIMEOUT_SECONDS = 15 * 60

PROMPT = "选择媒体目录"

#: `IFileOpenDialog` 的最小互操作：vtable 顺序必须与 shobjidl 一致，一个都不能少。
#: FOS_NOCHANGEDIR 0x8 不改进程工作目录，FOS_PICKFOLDERS 0x20 选文件夹，
#: FOS_FORCEFILESYSTEM 0x40 只允许真实路径（库、此电脑这类虚拟节点不能当结果）。
_WINDOWS_INTEROP = r"""
using System;
using System.Runtime.InteropServices;
namespace PeachPick {
  [ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8"),
   InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IFileOpenDialog {
    [PreserveSig] int Show(IntPtr parent);
    void SetFileTypes(uint count, IntPtr filterSpec);
    void SetFileTypeIndex(uint index);
    void GetFileTypeIndex(out uint index);
    void Advise(IntPtr events, out uint cookie);
    void Unadvise(uint cookie);
    void SetOptions(uint options);
    void GetOptions(out uint options);
    void SetDefaultFolder(IShellItem item);
    void SetFolder(IShellItem item);
    void GetFolder(out IShellItem item);
    void GetCurrentSelection(out IShellItem item);
    void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
    void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string name);
    void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
    void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
    void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
    void GetResult(out IShellItem item);
    void AddPlace(IShellItem item, int placement);
    void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string extension);
    void Close(int hr);
    void SetClientGuid(ref Guid guid);
    void ClearClientData();
    void SetFilter(IntPtr filter);
    void GetResults(out IntPtr items);
    void GetSelectedItems(out IntPtr items);
  }
  [ComImport, Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"),
   InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IShellItem {
    void BindToHandler(IntPtr bindContext, ref Guid handlerId, ref Guid interfaceId, out IntPtr result);
    void GetParent(out IShellItem parent);
    void GetDisplayName(uint form, [MarshalAs(UnmanagedType.LPWStr)] out string name);
    void GetAttributes(uint mask, out uint attributes);
    void Compare(IShellItem other, uint hint, out int order);
  }
  [ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
  public class FileOpenDialogRCW { }
  public static class Picker {
    [DllImport("user32.dll")]
    static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern IShellItem SHCreateItemFromParsingName(string path, IntPtr bindContext, ref Guid interfaceId);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr FindWindow(string className, string title);
    [DllImport("user32.dll")]
    static extern bool SetWindowPos(IntPtr window, IntPtr insertAfter, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll")]
    static extern bool SetForegroundWindow(IntPtr window);
    // 服务进程在后台，Windows 不给它前台权：对话框会开在浏览器后面。等它的窗口
    // 出现后置顶（SetWindowPos 到 HWND_TOPMOST 不需要前台权），模态窗关掉即消失。
    static void Raise(string title) {
      for (int attempt = 0; attempt < 200; attempt++) {
        var window = FindWindow("#32770", title);
        if (window != IntPtr.Zero) {
          SetWindowPos(window, new IntPtr(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x40);
          SetForegroundWindow(window);
          return;
        }
        System.Threading.Thread.Sleep(50);
      }
    }
    public static string Pick(string title, string initial) {
      var previousDpi = SetThreadDpiAwarenessContext(new IntPtr(-4));
      try {
      var dialog = (IFileOpenDialog)new FileOpenDialogRCW();
      new System.Threading.Thread(() => Raise(title)).Start();
      dialog.SetOptions(0x8 | 0x20 | 0x40);
      dialog.SetTitle(title);
      if (!string.IsNullOrEmpty(initial) && System.IO.Directory.Exists(initial)) {
        var shellItem = typeof(IShellItem).GUID;
        try { dialog.SetFolder(SHCreateItemFromParsingName(initial, IntPtr.Zero, ref shellItem)); }
        catch (Exception) { }
      }
      if (dialog.Show(IntPtr.Zero) != 0) return null;
      IShellItem picked;
      dialog.GetResult(out picked);
      string path;
      picked.GetDisplayName(0x80058000, out path);
      return path;
      } finally {
        if (previousDpi != IntPtr.Zero) SetThreadDpiAwarenessContext(previousDpi);
      }
    }
  }
}
"""

#: 所有者窗口留空：Shell 自己会把新对话框放到前台；挂在一个透明置顶 Form 上的对话框
#: 反而跟着所有者一起不可见，进程就卡在 Show 里。
_WINDOWS_SCRIPT = """
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
Add-Type -TypeDefinition @'
@INTEROP@
'@
$picked = [PeachPick.Picker]::Pick('@PROMPT@', $env:PEACH_PICK_INITIAL)
if ($picked) { [Console]::Out.Write($picked) }
""".replace("@INTEROP@", _WINDOWS_INTEROP.strip()).replace("@PROMPT@", PROMPT)


def _applescript_string(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def command(initial: str | None, platform: str = sys.platform, *, file: bool = False) -> tuple[list[str], dict[str, str]]:
    """这个平台弹对话框的命令行，以及要附加的环境变量。"""
    if platform == "win32":
        script = _WINDOWS_SCRIPT
        if file:
            script = script.replace("0x8 | 0x20 | 0x40", "0x8 | 0x40 | 0x1000").replace(PROMPT, "选择视频文件")
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", encoded]
        return argv, {"PEACH_PICK_INITIAL": initial or ""}
    if platform == "darwin":
        script = ('choose file with prompt "选择视频文件"' if file
                  else f'choose folder with prompt "{_applescript_string(PROMPT)}"')
        if initial and os.path.isdir(initial):
            script += f' default location POSIX file "{_applescript_string(initial)}"'
        return ["osascript", "-e", f"POSIX path of ({script})"], {}
    raise PickerUnavailable("这个系统上没有可用的文件夹对话框")


def _pick(initial: str | None = None, *, platform: str = sys.platform,
          run=subprocess.run, file: bool = False) -> str | None:
    """弹对话框，返回选中的绝对路径；用户取消返回 None。"""
    argv, extra = command(initial, platform, file=file)
    if not _LOCK.acquire(blocking=False):
        raise PickerBusy("已经有一个文件选择窗口开着")
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if platform == "win32" else 0
        try:
            result = run(argv, capture_output=True, env={**os.environ, **extra},
                         timeout=DIALOG_TIMEOUT_SECONDS, creationflags=flags)
        except subprocess.TimeoutExpired:
            return None
    finally:
        _LOCK.release()
    stderr = (result.stderr or b"").decode("utf-8", "replace")
    if result.returncode != 0:
        # osascript 在用户取消时以 1 退出，stderr 是 "User canceled."；这不是失败。
        if "canceled" in stderr.lower():
            return None
        raise PickerUnavailable("没能打开文件选择窗口")
    text = (result.stdout or b"").decode("utf-8", "replace").strip()
    if platform == "darwin" and len(text) > 1:
        text = text.rstrip("/")
    return text or None


def pick_folder(initial: str | None = None, *, platform: str = sys.platform,
                run=subprocess.run) -> str | None:
    return _pick(initial, platform=platform, run=run)


def pick_file(initial: str | None = None, *, platform: str = sys.platform,
              run=subprocess.run) -> str | None:
    """选择一个现有文件；文件类型与媒体目录范围由入库入口检查。"""
    return _pick(initial, platform=platform, run=run, file=True)
