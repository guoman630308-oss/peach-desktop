#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 macOS 菜单栏项的 .app 外壳。

裸控制台进程拿不稳菜单栏项：`peach-tray` 直接后台启动时会拉起服务、然后自己安静
退出——AppKit 的运行循环在没有应用上下文时立刻返回，退出码 0、没有任何输出。
实测就是这样：服务进程还在，托盘父进程没了。

macOS 的标准做法是打成 bundle 并在 `Info.plist` 里声明 `LSUIElement`：只在菜单栏
出现，不占 Dock、不进 ⌘Tab。外壳本身只有一个 plist 和一个 exec 到项目 venv 的脚本
——这是源码开发入口，服务进程仍由 venv 承担。独立发行包走 build_macos_standalone.py。
"""
from __future__ import annotations

import argparse
import plistlib
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from peach import __version__

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.appid import MACOS_BUNDLE_ID, MACOS_LAUNCH_AGENT_LABEL
from peach.config import PROJECT_ROOT


BUNDLE_ID = MACOS_BUNDLE_ID
#: LaunchAgent 的 label，双击 .app 时踢它。
LABEL = MACOS_LAUNCH_AGENT_LABEL
# 这个外壳**不能**自己 exec 到解释器。macOS 26 上，主可执行文件是 exec 跳板时状态项
# 注册不上：进程活着、NSStatusItem 也建出来了，但按钮窗口永远是 (0,0,34,0)，菜单栏上
# 什么都不出现（Apple FB21015611）。同一份代码由 launchd 直接拉起时实测
# (858, 949, 34, 33)，正常落在菜单栏内。
#
# 所以双击 .app 只负责踢一脚 LaunchAgent，真正的菜单栏进程是 launchd 的直接子进程。
LAUNCHER = """#!/bin/sh
# 由 scripts/build_macos_app.py 生成，勿手改。
exec /bin/launchctl kickstart -k "gui/$(id -u)/{label}"
"""


def build(destination: Path, tray: Path) -> Path:
    app = destination / "Peach.app"
    macos, resources = app / "Contents" / "MacOS", app / "Contents" / "Resources"
    if app.exists():
        shutil.rmtree(app)
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    info = {
        "CFBundleName": "Peach",
        "CFBundleDisplayName": "Peach 蜜桃",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleVersion": __version__,
        "CFBundleShortVersionString": __version__,
        "CFBundlePackageType": "APPL",
        "CFBundleExecutable": "Peach",
        # 只在菜单栏出现：不占 Dock、不进 ⌘Tab。菜单栏项的标准声明。
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    }
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))

    launcher = macos / "Peach"
    launcher.write_text(LAUNCHER.format(label=LABEL), encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # 访达和 Dock 里的图标。方形原图直接当图标会比周围大一圈、四角还是直的，
    # 所以用 scripts/make_macos_icon.py 生成的圆角版本。
    icon = PROJECT_ROOT / "resources" / "peach.icns"
    if icon.is_file():
        shutil.copy2(icon, resources / "peach.icns")
        info["CFBundleIconFile"] = "peach"
        (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    return app


def build_frontend_bundles() -> None:
    """重建 `web/dist/` 里的主界面与独立页面产物（ADR-0031）。

    这个 .app 外壳本身不打包 `web/`：它只踢 LaunchAgent，托盘从仓库检出里取页面。
    但那份检出就是 macOS 用户真正跑的东西，所以出包前仍要把产物重建一遍——运行时
    没有 Node，产物只能在这一步生成。npm 不在就直接失败：宁可停在这里，也不要拿
    工作区里恰好存在的旧 bundle 冒充这一次的构建结果。
    """
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("找不到 npm：前端产物（frontend/）出包前必须重建，请先装 Node 24+")
    frontend = PROJECT_ROOT / "frontend"
    subprocess.run([npm, "--prefix", str(frontend), "ci"], check=True)
    subprocess.run([npm, "--prefix", str(frontend), "run", "build"], check=True)
    for name in ("peach-app.js", "peach-app.css", "peach-pages.js", "peach-pages.css"):
        bundle = PROJECT_ROOT / "web" / "dist" / name
        if not bundle.is_file():
            raise SystemExit(f"构建之后仍然没有 {bundle}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成 macOS 菜单栏项的 .app 外壳")
    parser.add_argument("--destination", type=Path, default=PROJECT_ROOT / "dist" / "macos-source.noindex")
    parser.add_argument("--tray", type=Path, default=PROJECT_ROOT / ".venv" / "bin" / "peach-tray")
    return parser


def run(args: argparse.Namespace) -> int:
    if not args.tray.is_file():
        raise SystemExit(f"找不到 peach-tray：{args.tray}（先 pip install -e .）")
    build_frontend_bundles()
    args.destination.mkdir(parents=True, exist_ok=True)
    app = build(args.destination, args.tray)
    print(f"已生成 {app}")
    print("  双击它只是踢一脚 LaunchAgent；先注册：")
    print("    python scripts/install_macos_agent.py install")
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
