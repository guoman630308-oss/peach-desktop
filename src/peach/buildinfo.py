"""打包时埋进 EXE 的构建身份。

冻结的托盘运行的是打包那一刻的代码副本，检出却会继续往前走。没有这份身份，托盘无从
知道自己比源码旧了多少：`git rev-parse HEAD` 读的是检出，永远等于「最新」。所以由
`scripts/build_windows.ps1` 在调用 PyInstaller 之前把提交、版本和构建时间写进包根。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

#: 打进包根（`sys._MEIPASS`）的文件名，由 `scripts/build_windows.ps1` 生成。
BUILD_INFO_NAME = "build-info.json"


@dataclass(frozen=True)
class BuildInfo:
    #: 构建所用检出的完整 sha。构建机没有可用的 git 时为 None。
    commit: str | None
    version: str
    built_at: str


def read_build_info(root: Path) -> BuildInfo | None:
    """读包根里的构建身份。读不到或者格式坏了一律返回 None。

    这份文件是打包产物，不是配置：缺了只说明这个包比机制旧，调用方按「身份未取得」
    处理即可，没有任何理由让托盘起不来。
    """
    try:
        payload = json.loads((root / BUILD_INFO_NAME).read_text(encoding="utf-8"))
        version = str(payload["version"])
        built_at = str(payload["built_at"])
        commit = payload["commit"]
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None
    if commit is not None and not isinstance(commit, str):
        return None
    return BuildInfo(commit or None, version, built_at)


def frozen_build() -> BuildInfo | None:
    """当前进程的构建身份。源码运行时没有第二个版本，返回 None。"""
    if not getattr(sys, "frozen", False):
        return None
    root = getattr(sys, "_MEIPASS", "")
    return read_build_info(Path(root)) if root else None
