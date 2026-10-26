"""当前用户的 Peach 登录启动项：Windows 快捷方式与 macOS LaunchAgent。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

from . import distribution, settings_file
from .appid import MACOS_LAUNCH_AGENT_LABEL
from .fsutil import atomic_write_text

#: 结论只走 stdout 的一行 JSON，`ok` 决定成败。退出码和 stderr 都不作判据：
#: Windows PowerShell 5.1 把进度流序列化成 `#< CLIXML` 写到 stderr，成功那一次也写，
#: 于是「取 stderr 第一行当原因」拿到的是「正在准备首次使用模块。」而不是错误。
_SHORTCUT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
  $peachInput = [Console]::In.ReadToEnd() | ConvertFrom-Json
  $peachLinkPath = [IO.Path]::GetFullPath($peachInput.path)
  if ([IO.Path]::GetExtension($peachLinkPath) -ne '.lnk') { throw 'Expected shortcut' }
  $peachExists = Test-Path -LiteralPath $peachLinkPath -PathType Leaf
  $peachShell = New-Object -ComObject WScript.Shell
  if ($peachInput.action -eq 'read' -and -not $peachExists) {
    @{ok=$true; enabled=$false; target=''; arguments=''} | ConvertTo-Json -Compress
  } else {
    $peachLink = $peachShell.CreateShortcut($peachLinkPath)
    if ($peachInput.action -ne 'read' -and $peachExists -and $peachLink.TargetPath -ne $peachInput.expected) {
      throw 'Startup shortcut belongs to another installation'
    }
    if ($peachInput.action -eq 'write') {
      $peachParent = Split-Path -Parent $peachLinkPath
      [IO.Directory]::CreateDirectory($peachParent) | Out-Null
      $peachLink.TargetPath = $peachInput.target
      $peachLink.Arguments = $peachInput.arguments
      $peachLink.WorkingDirectory = $peachInput.directory
      $peachLink.Description = 'Peach'
      $peachLink.WindowStyle = 7
      $peachLink.Save()
      if (-not (Test-Path -LiteralPath $peachLinkPath -PathType Leaf)) {
        throw "Save reported success but $peachLinkPath is absent"
      }
      $peachExists = $true
    }
    if ($peachInput.action -eq 'remove' -and $peachExists) {
      Remove-Item -LiteralPath $peachLinkPath
      $peachExists = $false
    }
    @{ok=$true; enabled=$peachExists; target=$peachLink.TargetPath; arguments=$peachLink.Arguments} |
      ConvertTo-Json -Compress
  }
} catch {
  @{ok=$false; error=$_.Exception.Message; kind=$_.Exception.GetType().FullName;
    line=$_.InvocationInfo.ScriptLineNumber} | ConvertTo-Json -Compress
}
"""


#: 读到的快捷方式状态，键是路径，值是 `(指纹, 结论)`。指纹见 `_fingerprint`。
_READ_CACHE: dict[str, tuple[tuple, dict]] = {}

#: PowerShell 冷启动与快捷方式操作的总等待时限。
_SHORTCUT_TIMEOUT = 60


def _fingerprint(path: Path) -> tuple:
    """这个 `.lnk` 的当前身份。文件不在就是 `()`，内容变了指纹一定跟着变。"""
    try:
        stat = path.stat()
    except OSError:
        return ()
    return (stat.st_mtime_ns, stat.st_size)


def shortcut(action: str, path: Path, *, target: str = "", arguments: str = "", directory: str = "", expected: str = "") -> dict:
    """对一个 `.lnk` 读、写或删，结论由 PowerShell 的一行 JSON 给出。

    读是配置页首屏的同步路径上唯一一件慢事：起一个 `powershell.exe` 要 0.8–1.7 秒，而
    这一页最多读三个快捷方式。所以读走两道近路，写和删一律照直执行——它们要落盘。

    文件不在就不起进程：脚本对 `read` 的这一支本来也只回一句「没有」。剩下真存在的那几个
    按 `(mtime, size)` 记住结论，`.lnk` 一被改写指纹就变，缓存自然作废。
    """
    mark = ()
    if action == "read":
        mark = _fingerprint(path)
        if not mark:
            return {"ok": True, "enabled": False, "target": "", "arguments": ""}
        hit = _READ_CACHE.get(str(path))
        if hit and hit[0] == mark:
            return hit[1]
    encoded = base64.b64encode(_SHORTCUT_SCRIPT.encode("utf-16-le")).decode("ascii")
    # 和 folder_picker 相同：发行包使用 Windows 自带的 PowerShell，不要求用户安装 pwsh。
    shell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        result = subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                input=json.dumps(dict(action=action, path=str(path), target=target, arguments=arguments,
                                                      directory=directory, expected=expected)),
                                capture_output=True, text=True, encoding="utf-8", timeout=_SHORTCUT_TIMEOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    except subprocess.TimeoutExpired as exc:
        raise OSError(f"Windows 快捷方式操作超时（等待 {_SHORTCUT_TIMEOUT} 秒），请稍后重试。") from exc
    try:
        payload = json.loads(result.stdout.lstrip("﻿"))
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or not payload.get("ok"):
        # PowerShell 说的原因不能吞掉。只留「请检查权限」这一句时，Windows runner 上
        # 这一步失败了三次，而权限、`WScript.Shell` COM 不可用、路径没落地和扩展名
        # 校验不通过在消息里长得一模一样，谁都没法往下查。
        raise OSError("启动项未能保存，请检查当前用户的启动文件夹权限"
                      f"（PowerShell：{_shortcut_reason(payload, result)}）")
    if action == "read":
        # 记的是读之前那次指纹。这一趟里文件正好被换掉的话，它与之后 stat 出来的对不上，
        # 下次就重读一遍；记读完那次则相反——旧结论会配上新指纹，从此一直命中错的。
        _READ_CACHE[str(path)] = (mark, payload)
    else:
        # 写和删都改了这个路径上的东西，旧结论当场作废，不等下次 stat 去发现。
        _READ_CACHE.pop(str(path), None)
    return payload


def _shortcut_reason(payload, result) -> str:
    """脚本自报的原因优先；只有 PowerShell 连 JSON 都没吐出来才退回它的输出。

    退回时逐行滤掉 CLIXML：5.1 把进度流序列化成 `#< CLIXML` 加一整段 XML 写进 stderr，
    成功那一次也写，整段抄进消息只会把真正的原因顶掉。
    """
    if isinstance(payload, dict) and payload.get("error"):
        kind = str(payload.get("kind", "")).rsplit(".", 1)[-1]
        line = payload.get("line")
        located = f"{kind} 第 {line} 行" if kind and line else kind
        return f"{payload['error']}（{located}）" if located else str(payload["error"])
    noise = next((line.strip() for line in (result.stderr or "").splitlines()
                  if line.strip() and not line.lstrip().startswith(("#<", "<"))), "")
    return noise or f"退出码 {result.returncode}，输出为空"


def target(config, *, executable: Path | None = None) -> tuple[Path, list[str], Path]:
    if executable is not None or distribution.standalone():
        program = (executable or Path(sys.executable)).resolve()
        return program, ["--data-root", str(config.data_root.resolve())], program.parent
    if sys.platform == "win32":
        program = Path(sys.executable).with_name("pythonw.exe").resolve()
        bootstrap = ("import os,runpy;os.environ['PEACH_DATA_ROOT']=" + repr(str(config.data_root.resolve()))
                     + ";runpy.run_module('peach.tray',run_name='__main__')")
        return program, ["-c", bootstrap], settings_file.PROJECT_ROOT
    return settings_file.PROJECT_ROOT / ".venv/bin/peach-tray", [], settings_file.PROJECT_ROOT


def startup_directory() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"


#: 桌面的 known folder ID（`FOLDERID_Desktop`）。
_DESKTOP_FOLDER_ID = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"


def desktop_directory() -> Path:
    """本机桌面目录。Windows 走 known folder，不拼 `%USERPROFILE%\\Desktop`。

    Windows 11 上启用 OneDrive 备份的机器把桌面重定向到
    `%USERPROFILE%\\OneDrive\\Desktop`，`~/Desktop` 那个目录要么不存在，要么是个
    没人看的空壳——快捷方式放进去，用户在自己桌面上看不到任何东西。
    """
    if sys.platform != "win32":
        return Path.home() / "Desktop"
    import ctypes
    buffer = ctypes.c_wchar_p()
    guid = ctypes.create_string_buffer(16)
    if ctypes.windll.ole32.CLSIDFromString(_DESKTOP_FOLDER_ID, guid) == 0 and \
            ctypes.windll.shell32.SHGetKnownFolderPath(
                guid, 0, None, ctypes.byref(buffer)) == 0:
        try:
            return Path(buffer.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(buffer)
    return Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"


def owned_target(target: Path, program: Path) -> bool:
    """`.lnk` 指向的目标算不算这份安装的入口。

    源码安装的判据放宽到整个项目目录：`dist\\Peach\\Peach.exe` 和
    `pythonw.exe -c "…runpy.run_module('peach.tray')"` 是同一份 Peach 的两个入口，而
    `target()` 只会给出后者。按字面比就把自己先前放的图标判成「另一份安装」，配置页里
    连关掉它都不行——本机的桌面图标正是这么被锁住的。
    """
    return target == program.resolve() or (not distribution.standalone()
                                           and target.is_relative_to(settings_file.PROJECT_ROOT.resolve()))


def desktop_entry(program: Path) -> tuple[Path, dict]:
    """桌面快捷方式的路径与当前状态；属于另一份安装时不认领。

    文件名是给人看的 `Peach.lnk`，不带启动项那串安装身份哈希：桌面上并排两个
    `Peach-3f2a…lnk` 谁也分不清哪个是哪个。代价是同一台机器上的第二份安装认不到这个
    图标，它会照实报「属于另一份 Peach 安装」，不覆盖。
    """
    path = desktop_directory() / "Peach.lnk"
    current = shortcut("read", path)
    if current["enabled"] and not owned_target(Path(current["target"]).resolve(), program):
        raise ValueError("桌面快捷方式属于另一份 Peach 安装")
    return path, current


def windows_entry(config, program: Path) -> tuple[Path, dict]:
    directory = startup_directory()
    legacy = directory / "Peach.lnk"
    current = shortcut("read", legacy)
    if current["enabled"] and owned_target(Path(current["target"]).resolve(), program):
        return legacy, current
    identity = hashlib.sha256(str(config.data_root.resolve()).casefold().encode()).hexdigest()[:12]
    path = directory / f"Peach-{identity}.lnk"
    current = shortcut("read", path)
    if current["enabled"] and Path(current["target"]).resolve() != program.resolve():
        raise ValueError("启动项属于另一份 Peach 安装")
    return path, current


#: 桌面快捷方式只有 Windows 有。macOS 的等价物是 Finder 别名，得靠 osascript 造，
#: 而 Dock 已经是那个平台放常用程序的地方。
_NO_DESKTOP = "此系统未提供桌面快捷方式"


def desktop_snapshot(config) -> dict:
    """桌面快捷方式那一格的状态，自己一个 try。

    不跟启动项共用异常出口：桌面上摆着另一份安装的 `Peach.lnk` 只该让这个开关关掉并
    说出原因，不该把整个「开机自启」面板打成「状态未取得」，把还好着的两个开关一起锁住。
    """
    if sys.platform != "win32":
        return {"desktop": False, "desktop_message": _NO_DESKTOP}
    try:
        program, _, _ = target(config)
        _, entry = desktop_entry(program)
        return {"desktop": entry["enabled"], "desktop_message": ""}
    except (OSError, ValueError) as exc:
        return {"desktop": False, "desktop_message": str(exc)}
    except (KeyError, subprocess.TimeoutExpired):
        return {"desktop": False, "desktop_message": "桌面快捷方式状态未取得"}


def snapshot(config=None) -> dict:
    config = config or settings_file.load_config()
    if sys.platform not in {"win32", "darwin"}:
        return {"available": False, "enabled": False, "silent": True,
                "message": "此系统未提供登录自启入口", **desktop_snapshot(config)}
    try:
        preference = json.loads((config.directory("state") / "desktop-startup.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        preference = {"silent": True}
    try:
        program, _, _ = target(config)
        if sys.platform == "win32":
            _, entry = windows_entry(config, program)
            enabled = entry["enabled"]
            silent = "--show" not in entry.get("arguments", "") if enabled else bool(preference.get("silent", True))
        else:
            path = Path.home() / "Library/LaunchAgents" / f"{MACOS_LAUNCH_AGENT_LABEL}.plist"
            entry = plistlib.loads(path.read_bytes()) if path.is_file() else {}
            if entry.get("ProgramArguments") and Path(entry["ProgramArguments"][0]).resolve() != program.resolve():
                raise ValueError("启动项属于另一份 Peach 安装")
            enabled = bool(entry.get("RunAtLoad", False))
            silent = "--show" not in entry.get("ProgramArguments", []) if enabled else bool(preference.get("silent", True))
        return {"available": program.is_file(), "enabled": enabled, "silent": silent,
                "message": "" if program.is_file() else "未找到本机托盘入口", **desktop_snapshot(config)}
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return {"available": False, "enabled": False, "silent": True,
                "message": "启动项状态未取得", **desktop_snapshot(config)}


def save(config, *, enabled: bool, silent: bool, desktop: bool, executable: Path | None = None) -> dict:
    if type(enabled) is not bool or type(silent) is not bool or type(desktop) is not bool:
        raise ValueError("启动选项必须为开或关")
    if desktop and sys.platform != "win32":
        raise ValueError(_NO_DESKTOP)
    program, args, directory = target(config, executable=executable)
    if (enabled or desktop) and not program.is_file():
        raise ValueError("未找到本机托盘入口")
    if sys.platform == "win32":
        # 桌面图标固定 `--show`，跟登录自启的静默选项无关：用户是特意去点它的。托盘已经
        # 在跑时 `main()` 拿不到单实例锁，`--show` 那一支正好打开网页，这跟点桌面图标该
        # 发生的事是同一件。
        try:
            icon, existing = desktop_entry(program)
        except ValueError:
            # 桌面上的 `Peach.lnk` 是另一份安装的。要求开启就照实拒绝；要求关闭时它本来
            # 就不是我们放的，跳过——不能因为它挡在那儿连「开机自启」这一格都存不下。
            if desktop:
                raise
        else:
            shortcut("write" if desktop else "remove", icon, target=str(program),
                     arguments=subprocess.list2cmdline([*args, "--show"]),
                     directory=str(directory), expected=existing.get("target", ""))
        args = [*args, "--silent" if silent else "--show"]
        path, existing = windows_entry(config, program)
        shortcut("write" if enabled else "remove", path, target=str(program), arguments=subprocess.list2cmdline(args),
                 directory=str(directory), expected=existing.get("target", ""))
    elif sys.platform == "darwin":
        args = [*args, "--silent" if silent else "--show"]
        path = Path.home() / "Library/LaunchAgents" / f"{MACOS_LAUNCH_AGENT_LABEL}.plist"
        entry = plistlib.loads(path.read_bytes()) if path.is_file() else {}
        if entry.get("ProgramArguments") and Path(entry["ProgramArguments"][0]).resolve() != program.resolve():
            raise ValueError("启动项属于另一份 Peach 安装")
        entry.update(Label=MACOS_LAUNCH_AGENT_LABEL, ProgramArguments=[str(program), *args], RunAtLoad=enabled,
                     KeepAlive=False, ProcessType="Interactive", WorkingDirectory=str(directory))
        entry["EnvironmentVariables"] = {**entry.get("EnvironmentVariables", {}), "PEACH_DATA_ROOT": str(config.data_root),
                                         "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"}
        atomic_write_text(path, plistlib.dumps(entry).decode("utf-8"))
    else:
        raise ValueError("此系统未提供登录自启入口")
    atomic_write_text(config.directory("state") / "desktop-startup.json", json.dumps({"silent": silent}))
    return {"available": True, "enabled": enabled, "silent": silent, "message": "",
            "desktop": desktop, "desktop_message": "" if sys.platform == "win32" else _NO_DESKTOP}
