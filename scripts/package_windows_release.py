"""生成完整免安装包、应用更新包及按工具拆分的 ZIP。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from peach.update_packages import MANIFEST, TOOL_PATHS, digest, read_manifest


def archive(destination: Path, root: Path, files: list[Path], manifest: dict | None = None) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for path in files:
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("发行目录包含链接或越界文件")
            package.write(path, "Peach/" + path.relative_to(root).as_posix())
        if manifest is not None:
            package.writestr("Peach/" + MANIFEST, json.dumps(manifest, ensure_ascii=False, sort_keys=True))


def download_notes(version: str, *, lite_installer: bool = False) -> str:
    """发布页的下载入口与包类型说明。"""
    base = f"https://github.com/peach-mitao/peach/releases/download/v{version}/Peach-{version}-windows-x64"
    lite = (f"| [轻量安装包（lite-setup.exe）]({base}-lite-setup.exe) | 不含工具，安装时复用已有工具或按需联网下载 |\n"
            if lite_installer else "")
    return (
        "\n## 下载选择\n\n"
        "| 下载 | 用途 |\n| --- | --- |\n"
        f"| [安装包（setup.exe）]({base}-setup.exe) | 包含全部工具，首次安装推荐 |\n"
        + lite +
        f"| [完整免安装包（ZIP）]({base}.zip) | 包含全部工具，解压后运行 `Peach.exe` |\n"
        f"| [应用更新包（update.zip）]({base}-update.zip) | 不含工具，由 Peach 更新器安装，不能单独用于首次安装 |\n"
        f"| 工具包（tools-*.zip）：[FFmpeg]({base}-tools-ffmpeg.zip) / "
        f"[untrunc]({base}-tools-untrunc.zip) / [cloudflared]({base}-tools-cloudflared.zip) "
        "| 更新器按需下载，普通使用无需手动选择 |\n"
        "| `*-corresponding-source.zip` | 第三方工具源码，普通使用无需下载 |\n\n"
        "已有 Peach：在「配置 → 应用与维护」检查更新，下载完成后确认重启安装。"
        "手动更新可运行安装包，或使用完整免安装包。\n"
    )


def build_lite_installer(output: Path, version: str, compiler: Path | None = None) -> Path:
    """将应用更新包及其工具清单编入按需下载工具的安装器。"""
    from peach.standalone_update import extract
    output = output.resolve()
    if compiler is None:
        candidates = [os.environ.get('PEACH_ISCC'), shutil.which('ISCC.exe'),
                      str(Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Inno Setup 6/ISCC.exe'),
                      str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Inno Setup 6/ISCC.exe')]
        compiler = next((Path(item) for item in candidates if item and Path(item).is_file()), None)
    if compiler is None:
        raise ValueError('未取得 Inno Setup 编译器，请设置 PEACH_ISCC。')
    with tempfile.TemporaryDirectory(prefix='peach-lite-installer-') as folder:
        source = extract(output / f'Peach-{version}-windows-x64-update.zip', Path(folder).resolve(), lambda *_: None)
        read_manifest(source, version)
        subprocess.run([str(compiler), '/Q', f'/DAppVersion={version}',
                        f'/DSourceDir={source}', f'/DOutputDir={output}', '/DLiteInstaller=1',
                        str(Path(__file__).resolve().parent / 'installer/peach.iss')], check=True)
    target = output / f'Peach-{version}-windows-x64-lite-setup.exe'
    if not target.is_file():
        raise ValueError('轻量安装包未生成。')
    return target


def package(root: Path, output: Path, version: str) -> dict:
    import re
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("发行版本无效")
    root, output = root.resolve(), output.resolve()
    if output.is_relative_to(root):
        raise ValueError("发行输出目录须位于程序目录外")
    if not (root / "Peach.exe").is_file() or not (root / "_internal/standalone.txt").is_file():
        raise ValueError("发行目录不是独立 Windows 包")
    output.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if any(path.name == MANIFEST for path in files):
        raise ValueError("完整程序目录中包含更新清单")
    tools = {}
    tool_files: set[Path] = set()
    for tool, (prefix, required) in TOOL_PATHS.items():
        selected = [path for path in files if (path.is_relative_to(root / prefix) if prefix
                    else path.relative_to(root).as_posix() in required)]
        names = {path.relative_to(root).as_posix(): digest(path) for path in selected}
        expected = {f"{prefix}/{name}" if prefix else name for name in required}
        if not expected.issubset(names):
            raise ValueError(f"发行目录缺少 {tool} 工具与分发材料")
        name = f"Peach-{version}-windows-x64-tools-{tool}.zip"
        archive(output / name, root, selected)
        tools[tool] = {"files": names, "asset": {"name": name, "size": (output / name).stat().st_size,
                                              "sha256": digest(output / name)}}
        tool_files.update(selected)
    manifest = {"format": 1, "version": version, "tools": tools}
    # 用运行时的同一判据验证生成结果，不在完整包内留下更新专用文件。
    with tempfile.TemporaryDirectory() as temp:
        check = Path(temp).resolve()
        (check / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        read_manifest(check, version)
    archive(output / f"Peach-{version}-windows-x64.zip", root, files)
    archive(output / f"Peach-{version}-windows-x64-update.zip", root,
            [path for path in files if path not in tool_files], manifest)
    (output / "DOWNLOADS.md").write_text(
        download_notes(version), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--lite-installer", action="store_true", help="同时编译按需下载工具的轻量安装包")
    parser.add_argument("--iscc", type=Path, help="Inno Setup 编译器路径")
    args = parser.parse_args()
    manifest = package(args.source, args.output, args.version)
    if args.lite_installer:
        build_lite_installer(args.output, args.version, args.iscc)
        (args.output / 'DOWNLOADS.md').write_text(download_notes(args.version, lite_installer=True), encoding='utf-8')
    print(json.dumps({"version": manifest["version"], "tools": list(manifest["tools"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
