# -*- mode: python ; coding: utf-8 -*-
"""macOS 独立应用：原生启动器、内置解释器与只读资源。"""
import json
import os
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / "src"))
from peach.appid import MACOS_BUNDLE_ID

identity = Path(os.environ["PEACH_MACOS_BUILD_INFO"])
version = json.loads(identity.read_text(encoding="utf-8"))["version"]
datas = [(str(root / name), name) for name in ("web", "migrations", "resources")]
datas += [(str(identity), "."), (str(root / "scripts" / "standalone.txt"), ".")]
datas += [(str(root / "LICENSE"), ".")]
datas += copy_metadata("sentry-sdk")
binaries = []
hiddenimports = [
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on", "peach.menubar",
]
for package in ("curl_cffi", "resvg_py"):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports

a = Analysis([str(root / "scripts" / "build_app_entry.py")],
             pathex=[str(root / "src")], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, excludes=["tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Peach",
          console=False, argv_emulation=False, strip=False, upx=False,
          target_arch=os.environ["PEACH_MACOS_TARGET_ARCH"])
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Peach")
app = BUNDLE(collection, name="Peach.app", icon=str(root / "resources" / "peach.icns"),
             bundle_identifier=MACOS_BUNDLE_ID,
             info_plist={"CFBundleName": "Peach", "CFBundleDisplayName": "Peach 蜜桃",
                         "CFBundleShortVersionString": version, "CFBundleVersion": version,
                         "LSUIElement": True, "NSHighResolutionCapable": True})
