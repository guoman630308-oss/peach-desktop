"""仅用发行制品重建应用更新目录，并核对它与完整包逐文件一致。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import zipfile


def inventory(package: zipfile.ZipFile) -> dict[str, str]:
    result = {}
    for entry in package.infolist():
        if entry.is_dir():
            continue
        parts = PurePosixPath(entry.filename).parts
        if (not parts or parts[0] != "Peach" or ".." in parts or "\\" in entry.filename
                or ":" in entry.filename or entry.filename.startswith("/")
                or (entry.external_attr >> 16) & 0o170000 == 0o120000
                or entry.filename.casefold() in {name.casefold() for name in result}):
            raise ValueError("发行 ZIP 包含无效路径")
        with package.open(entry) as source:
            result[entry.filename] = hashlib.file_digest(source, "sha256").hexdigest()
    return result


def verify(root: Path, version: str, destination: Path, *, prepare_with_app: bool = False) -> dict:
    prefix = f"Peach-{version}-windows-x64"
    if destination.exists():
        raise ValueError("制品验收目录须不存在")
    with zipfile.ZipFile(root / f"{prefix}-update.zip") as thin, zipfile.ZipFile(root / f"{prefix}.zip") as full:
        expected = inventory(full)
        actual = inventory(thin)
        manifest = json.loads(thin.read("Peach/update-manifest.json"))
        del actual["Peach/update-manifest.json"]
        if manifest["version"] != version or manifest["format"] != 1:
            raise ValueError("发行更新清单版本无效")
        tools = []
        for tool, bundle in manifest["tools"].items():
            name = f"{prefix}-tools-{tool}.zip"
            if bundle["asset"]["name"] != name:
                raise ValueError("发行工具包名无效")
            path = root / name
            with path.open("rb") as source:
                if path.stat().st_size != bundle["asset"]["size"] or hashlib.file_digest(source, "sha256").hexdigest() != bundle["asset"]["sha256"]:
                    raise ValueError("发行工具包校验失败")
            with zipfile.ZipFile(path) as package:
                files = inventory(package)
            if files != {"Peach/" + name: value for name, value in bundle["files"].items()} or set(actual) & set(files):
                raise ValueError("发行工具文件清单不符或重复")
            actual.update(files)
            tools.append(path)
        if actual != expected:
            raise ValueError("应用更新包与工具包不能重建完整程序")
        thin.extractall(destination)
    if prepare_with_app:
        error = destination / 'tools-error.txt'
        process = subprocess.run([str(destination / 'Peach/Peach.exe'), '--data-root', str(destination / 'setup-data'), '--installer-tools',
                                  str(destination / 'empty-install'), str(root), str(error)],
                                 capture_output=True, timeout=120)
        if process.returncode:
            raise ValueError(error.read_text(encoding='utf-8') if error.exists() else '轻量安装工具准备失败')
        for name, checksum in expected.items():
            with (destination / name).open('rb') as source:
                if hashlib.file_digest(source, 'sha256').hexdigest() != checksum:
                    raise ValueError(f'轻量安装文件不符：{name}')
    else:
        for path in tools:
            with zipfile.ZipFile(path) as package:
                package.extractall(destination)
    return {"files": len(expected), "tools": len(tools), "application": str(destination / "Peach/Peach.exe")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--version", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--prepare-with-app", action="store_true", help="由实际应用准备轻量安装所需工具")
    args = parser.parse_args()
    print(json.dumps(verify(args.root.resolve(), args.version, args.destination.resolve(),
                            prepare_with_app=args.prepare_with_app), ensure_ascii=False))
