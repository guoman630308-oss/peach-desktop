"""本机外部播放器偏好与原文件启动；播放记录由播放器自己管理。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from sys import platform

from .fsutil import atomic_write_text
from . import distribution


def discover() -> str:
    if platform != "win32":
        return ""
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for name in ("PotPlayerMini64.exe", "PotPlayerMini.exe"):
            key = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}"
            try:
                with winreg.OpenKey(hive, key) as opened:
                    raw = str(winreg.QueryValue(opened, None)).strip().strip('"')
                if Path(raw).is_file():
                    return raw
            except OSError:
                continue
    return ""


def read(state_root: Path) -> dict:
    path = Path(state_root) / "local-player.json"
    if platform != "win32" or (not distribution.embedded_window() and not path.is_file()):
        return {"available": False, "mode": "internal", "executable": "", "label": "外部播放器"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {"mode": "external", "executable": discover()}
    except (OSError, ValueError) as exc:
        raise ValueError("播放器配置无法读取，请在播放器设置中重新保存") from exc
    if (not isinstance(data, dict) or not isinstance(data.get("mode"), str)
            or data["mode"] not in {"external", "internal"}):
        raise ValueError("播放器配置无效，请在播放器设置中重新保存")
    executable = data.get("executable", "")
    if not isinstance(executable, str):
        raise ValueError("播放器程序路径无效")
    label = "PotPlayer" if Path(executable).stem.lower().startswith("potplayer") else Path(executable).stem or "外部播放器"
    return {"available": True, "mode": data["mode"], "executable": executable, "label": label}


def executable_path(raw: str) -> Path:
    path = Path(raw.strip().strip('"'))
    if not path.is_absolute() or path.suffix.lower() != ".exe" or not path.is_file():
        raise ValueError("请选择已安装播放器的 .exe 程序")
    return path


def save(state_root: Path, mode: str, executable: str) -> dict:
    if platform != "win32":
        raise ValueError("本机播放器启动仅适用于 Windows")
    if not isinstance(mode, str) or mode not in {"external", "internal"} or not isinstance(executable, str):
        raise ValueError("播放器设置无效")
    executable = executable.strip().strip('"')
    if executable or mode == "external":
        executable = str(executable_path(executable))
    atomic_write_text(Path(state_root) / "local-player.json",
                      json.dumps({"mode": mode, "executable": executable}, ensure_ascii=False), mode=0o600)
    return read(state_root)


def launch(preference: dict, source: Path | str) -> None:
    if platform != "win32":
        raise ValueError("本机播放器启动仅适用于 Windows")
    executable = executable_path(preference["executable"])
    # 参数分别传递，片名里的空格、引号与符号不进入命令解释器。
    subprocess.Popen([str(executable), str(source)], cwd=str(executable.parent),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, creationflags=(getattr(subprocess, "DETACHED_PROCESS", 0)
                                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)))
