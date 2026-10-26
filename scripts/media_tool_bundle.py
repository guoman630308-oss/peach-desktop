"""校验并复制 Windows 媒体工具及其对应源码制品。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil


REQUIRED_FILES = {"bin/ffmpeg.exe", "bin/ffprobe.exe", "LICENSE.txt", "NOTICE.md", "buildconf.txt"}
POLICIES = {
    "ffmpeg": (REQUIRED_FILES, {"ffmpeg", "x264", "nv-codec-headers", "dav1d"}, "GPL-3.0-or-later"),
    "untrunc": ({"untrunc.exe", "LICENSE.txt", "NOTICE.md", "buildconf.txt"},
                {"untrunc", "ffmpeg"}, "GPL-2.0-or-later"),
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_file(root: Path, name: str, expected: str) -> Path:
    relative = PurePosixPath(name)
    if not name or "\\" in name or relative.is_absolute() or ".." in relative.parts or ":" in name:
        raise ValueError(f"FFmpeg 制品路径无效：{name}")
    target = root.joinpath(*relative.parts)
    if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink() or not target.is_file():
        raise ValueError(f"FFmpeg 制品文件缺失或越界：{name}")
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or digest(target) != expected:
        raise ValueError(f"FFmpeg SHA-256 mismatch: {name}")
    return target


def validate(root: Path, tool: str = "ffmpeg") -> tuple[dict, Path]:
    """构建和制品验收共用；哈希不匹配时不复制任何文件。"""
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    required, source_names, license_name = POLICIES[tool]
    if manifest.get("license") != license_name or not manifest.get("version"):
        raise ValueError("FFmpeg 清单缺少版本或 GPL 许可证声明")
    files = manifest.get("files", {})
    if not required.issubset(files):
        raise ValueError("FFmpeg 清单缺少工具或分发材料")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != set(files) | {"manifest.json"}:
        raise ValueError("FFmpeg 目录与清单文件不一致")
    for name, expected in files.items():
        checked_file(root, name, expected)
    sources = manifest.get("sources", [])
    if {source.get("name") for source in sources} != source_names:
        raise ValueError("FFmpeg 清单缺少完整源码来源")
    for source in sources:
        if (not source.get("revision") or not source.get("url", "").startswith("https://")
                or not re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", ""))):
            raise ValueError("FFmpeg 源码来源缺少固定版本或哈希")
    archive = manifest["source_archive"]
    if archive["name"] != f"{tool}-corresponding-source.zip":
        raise ValueError("媒体工具对应源码归档名称不符")
    source_path = checked_file(root.parent, archive["name"], archive["sha256"])
    return manifest, source_path


def package(root: Path, destination: Path, tool: str = "ffmpeg") -> dict:
    manifest, source_path = validate(root, tool)
    if destination.exists():
        raise ValueError(f"FFmpeg 目标目录必须不存在：{destination}")
    shutil.copytree(root, destination)
    output = destination.parents[2] / source_path.name
    if output.resolve() != source_path.resolve():
        shutil.copyfile(source_path, output)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--tool", choices=tuple(POLICIES), default="ffmpeg")
    args = parser.parse_args()
    if args.validate_only:
        manifest, _source = validate(args.source.resolve(), args.tool)
        print(json.dumps({"version": manifest["version"], "verified": True}))
        return 0
    if args.destination is None:
        parser.error("--destination is required unless --validate-only is set")
    manifest = package(args.source.resolve(), args.destination.resolve(), args.tool)
    print(json.dumps({"version": manifest["version"], "destination": str(args.destination)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
