"""独立测试包的资源标记与用户数据目录。"""
from __future__ import annotations

import sys
from pathlib import Path


def embedded_window() -> bool:
    return bool(getattr(sys, 'frozen', False) and
                (Path(getattr(sys, '_MEIPASS', '')) / 'native-window.txt').is_file())


def standalone() -> bool:
    return bool(getattr(sys, "frozen", False) and
                (Path(getattr(sys, "_MEIPASS", "")) / "standalone.txt").is_file())


def user_data_root(environ: dict[str, str]) -> Path:
    if sys.platform == "win32":
        base = Path(environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path.home() / "Library" / "Application Support"
    return base / ("PeachDesktop" if embedded_window() else "Peach") / "peach-data"
