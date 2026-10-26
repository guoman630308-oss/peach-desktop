"""媒体来源表单与配置映射；来源 ID 直接沿用扫描及流量策略。"""
from __future__ import annotations

from pathlib import Path, PurePath, PureWindowsPath, PurePosixPath
import hashlib
import shutil
import sys
from typing import Any

from . import platform
from .media_sources import SOURCE_OPTIONS, cost_for


def _windows_installed_names() -> list[str]:
    """从 Windows 安装清单读取软件名称，不读取命令、账户或凭据。"""
    if sys.platform != "win32":
        return []
    import winreg
    names = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                                    0, winreg.KEY_READ | view) as key:
                    for index in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            with winreg.OpenKey(key, winreg.EnumKey(key, index)) as child:
                                names.append(str(winreg.QueryValueEx(child, "DisplayName")[0]).lower())
                        except OSError:
                            continue
            except OSError:
                continue
    return names


def mount_dependencies(*, system: str | None = None) -> list[dict[str, Any]]:
    """安装证据与挂载在线状态分别检测；只返回当前操作系统需要的依赖。"""
    system = system or sys.platform
    if system == "win32":
        names = _windows_installed_names()
        cloud = any(name.startswith("clouddrive") for name in names)
        driver, installed, url = "WinFsp", any(name.startswith("winfsp") for name in names), "https://winfsp.dev/rel/"
    elif system == "darwin":
        cloud = any((base / app).is_dir() for base in (Path("/Applications"), Path.home() / "Applications")
                    for app in ("CloudDrive.app", "CloudDrive2.app"))
        driver, installed, url = "macFUSE", Path("/Library/Filesystems/macfuse.fs").is_dir(), "https://macfuse.github.io/"
    else:
        return []
    cloud = cloud or any(shutil.which(name) for name in ("clouddrive", "clouddrive2", "CloudDrive"))
    return [{"name": "CloudDrive", "available": bool(cloud), "download_url": "https://www.clouddrive2.com/download.html"},
            {"name": driver, "available": installed, "download_url": url}]


def rows(config, *, windows: bool, probe: bool = False) -> list[dict[str, Any]]:
    result = []
    for location, roots in config.locations.items():
        mounts = config.mounts.get(location, ())
        for index, root in enumerate(roots):
            mount = root if windows else (mounts[index] if index < len(mounts) else "")
            result.append({"location": location, "root": root, "path": mount,
                           "library": config.library_names.get(root) or PureWindowsPath(root).name or root,
                           "library_icon": config.library_icons.get(root, ""),
                           "cost": cost_for(location, root, config),
                           "online": platform.root_online(Path(mount)) if probe and mount else False})
    return result


def _overlaps(first: PureWindowsPath, second: PureWindowsPath) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _automatic_root(location: str, path: str, used: list[PureWindowsPath]) -> str:
    # 媒体来源和目录决定内部路径；已有路径占用时改用其他盘符，避免不同目录共用前缀。
    token = hashlib.sha256(f"{location}\0{PurePosixPath(path)}".encode()).hexdigest()[:32]
    drives = dict.fromkeys(["R:", *(f"{chr(code)}:" for code in range(65, 91))])
    for drive in drives:
        candidate = PureWindowsPath(drive + "\\", "peach", location, token)
        if not any(_overlaps(candidate, other) for other in used):
            return str(candidate)
    return ""


def _prepare_source(row: dict, existing: list[dict], used: list[PureWindowsPath]) -> None:
    location = row.get("location")
    if not isinstance(location, str) or location not in dict(SOURCE_OPTIONS):
        return
    path = str(row.get("path", "")).strip()
    matches = [previous for previous in existing
               if previous["location"] == location and previous["path"]
               and PurePosixPath(previous["path"]) == PurePosixPath(path)]
    row["root"] = matches[0]["root"] if len(matches) == 1 else _automatic_root(location, path, used)
    if not row.get("library"):
        row["library"] = (matches[0]["library"] if len(matches) == 1 and "library" not in row
                          else PurePosixPath(path).name or dict(SOURCE_OPTIONS)[location])
    if row["root"]:
        used.append(PureWindowsPath(row["root"]))


def prepare_sources(raws: object, *, windows: bool, config=None) -> object:
    """为非 Windows 表单补齐内部路径，保留已配置目录和显式填写的值；不读取或改写数据库。"""
    if windows or not isinstance(raws, list) or not 1 <= len(raws) <= 100:
        return raws
    result = [dict(row) if isinstance(row, dict) else row for row in raws]
    existing = rows(config, windows=False) if config is not None else []
    reserved = [row["root"] for row in existing] if config is not None and (config.present or config.mounts) else []
    reserved += [str(row.get("root", "")).strip() for row in result if isinstance(row, dict)]
    used = [PureWindowsPath(root) for root in reserved if platform.is_windows_path(root)]
    for row in result:
        if not isinstance(row, dict) or str(row.get("root", "")).strip():
            continue
        _prepare_source(row, existing, used)
    return result


def _validated_paths(row: dict, *, windows: bool) -> tuple[PureWindowsPath, PurePath]:
    """取得数据库路径前缀与本机实际目录，用于检查重复和包含关系。"""
    text = str(row.get("path", "")).strip()
    root = text if windows else str(row.get("root", "")).strip()
    if any(ord(char) < 32 for char in text + root):
        raise ValueError("路径不能包含控制字符")
    if not windows and not root:
        raise ValueError("无法自动配置媒体目录，请检查配置文件中的目录对应关系")
    if not platform.is_windows_path(root) or ".." in PureWindowsPath(root).parts:
        raise ValueError("媒体路径必须包含盘符，例如 B:\\；网络共享请先映射为 Windows 盘符" if windows
                         else "配置文件中 [media.locations] 的路径格式无效；请使用 Windows 格式，例如 R:\\media")
    path = PureWindowsPath(text) if windows else PurePosixPath(text)
    if not text or not (platform.is_windows_path(text) if windows else text.startswith("/")):
        raise ValueError("请填写媒体目录的完整路径，例如 D:\\Movies" if windows
                         else "请填写媒体目录的完整路径，例如 /media")
    if ".." in path.parts:
        raise ValueError("媒体目录路径不能包含上级目录 ..")
    return PureWindowsPath(root), path


def validate(raws: object, *, windows: bool, config=None) -> tuple[dict, dict, list[str]]:
    """离线来源允许保存；绝对路径、盘符根与来源间重叠在保存前校验。"""
    if not isinstance(raws, list) or not raws or len(raws) > 100:
        return {}, {}, ["请添加 1 到 100 个媒体目录"]
    raws = prepare_sources(raws, windows=windows, config=config)
    locations: dict[str, list[str]] = {}
    mounts: dict[str, list[str]] = {}
    errors = [""] * len(raws)
    seen: list[tuple[PureWindowsPath, PurePath]] = []
    for index, row in enumerate(raws):
        try:
            if not isinstance(row, dict):
                raise ValueError("媒体来源格式不正确")
            location = row.get("location")
            if not isinstance(location, str) or location not in dict(SOURCE_OPTIONS):
                raise ValueError("请选择列表中的媒体来源")
            if "cost" in row and row["cost"] not in ("free", "metered"):
                raise ValueError("请选择代理流量保护设置")
            declared, path = _validated_paths(row, windows=windows)
            for other_root, other_path in seen:
                if (declared == other_root or declared.is_relative_to(other_root)
                        or other_root.is_relative_to(declared)):
                    raise ValueError("媒体路径与另一文件夹重复或互相包含" if windows
                                     else "配置文件中的目录对应关系重复或互相包含")
                if path == other_path or path.is_relative_to(other_path) or other_path.is_relative_to(path):
                    raise ValueError("媒体目录与另一行重复或互相包含")
            seen.append((declared, path))
            locations.setdefault(location, []).append(str(declared))
            if not windows:
                mounts.setdefault(location, []).append(str(path))
        except ValueError as exc:
            errors[index] = str(exc)
    return ({key: tuple(value) for key, value in locations.items()},
            {key: tuple(value) for key, value in mounts.items()},
            errors if any(errors) else [])


def costs(raws: list[dict], config, *, windows: bool) -> dict[str, str]:
    """按数据库路径前缀保存已校验媒体行的读取策略。"""
    result = dict(getattr(config, "media_costs", {}))
    for row in raws:
        root = str(PureWindowsPath(row["path"] if windows else row["root"]))
        for previous in tuple(result):
            if PureWindowsPath(previous) == PureWindowsPath(root):
                del result[previous]
        result[root] = row.get("cost", cost_for(row["location"], root, config))
    return result
