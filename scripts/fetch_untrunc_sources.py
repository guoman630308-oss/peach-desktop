"""校验 untrunc 固定源码，整理 Windows 发行目录与完整对应源码包。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import zipfile

from fetch_ffmpeg_sources import digest, prepare

SCRIPT_DIR = Path(__file__).resolve().parent
LOCK_PATH = SCRIPT_DIR / 'untrunc-sources.json'


def load_sources(path: Path = LOCK_PATH) -> dict:
    lock = json.loads(path.read_text(encoding='utf-8'))
    if {source['name'] for source in lock['sources']} != {'untrunc', 'ffmpeg'}:
        raise ValueError('源码清单必须包含 untrunc 与 FFmpeg')
    for source in lock['sources']:
        if len(source['sha256']) != 64 or any(c not in '0123456789abcdef' for c in source['sha256']):
            raise ValueError(f"无效源码 SHA256: {source['name']}")
        if not source['url'].startswith('https://') or Path(source['archive']).name != source['archive']:
            raise ValueError('源码必须使用 HTTPS 与单个归档文件名')
    return lock


def finish(output: Path, lock: dict) -> dict:
    bundle = output / 'untrunc'
    work = output / 'work'
    shutil.copyfile(work / 'untrunc/COPYING', bundle / 'LICENSE.txt')
    shutil.copyfile(work / 'ffmpeg/COPYING.LGPLv2.1', bundle / 'LICENSE-FFmpeg-LGPLv2.1.txt')
    shutil.copyfile(output / 'buildconf.txt', bundle / 'buildconf.txt')
    (bundle / 'NOTICE.md').write_text(
        '# untrunc Windows x64\n\n'
        'untrunc 由 anthwlock 与贡献者维护，按 GPL-2.0-or-later 分发。\n'
        '静态链接 FFmpeg 8.1.3 的 LGPL-2.1-or-later 内置库；完整许可证见本目录 LICENSE 文件。\n'
        'FFmpeg 内部结构与 untrunc 绑定，构建使用固定 8.1 维护分支。\n'
        '不包含 GUI，不使用外部编解码库；Windows 系统库由操作系统提供。\n'
        '静态 C++ 与 MinGW-w64 运行时的版权和运行时例外见 LICENSE-GCC-runtime.txt 与 LICENSE-MinGW-w64.txt。\n'
        '完整对应源码、构建脚本和说明见同版本 untrunc-corresponding-source.zip。\n',
        encoding='utf-8',
    )
    archive = output / 'untrunc-corresponding-source.zip'
    instructions = (
        '# untrunc Windows x64 对应源码\n\n'
        '包含未经修改的 untrunc、FFmpeg 固定源码归档、构建脚本与环境记录。\n'
        'Ubuntu 安装：sudo apt-get install build-essential gcc-mingw-w64-x86-64-posix '
        'g++-mingw-w64-x86-64-posix mingw-w64-x86-64-dev nasm pkg-config python3 ca-certificates xz-utils\n\n'
        '执行：bash scripts/build_untrunc_windows.sh "$PWD/rebuilt"\n'
        '离线重建：先建立 rebuilt/sources，将 sources/ 中两份归档复制进去，再执行构建命令。\n'
        '产物为 rebuilt/untrunc 与 rebuilt/untrunc-corresponding-source.zip。\n'
        '工具链由系统提供，版本见 build-environment.txt；FFmpeg 配置见 buildconf.txt。\n'
    )
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for source in lock['sources']:
            path = output / 'sources' / source['archive']
            if digest(path) != source['sha256']:
                raise ValueError(f"源码 SHA256 不匹配: {source['name']}")
            package.write(path, f'sources/{path.name}')
        for name in ('build_untrunc_windows.sh', 'fetch_untrunc_sources.py',
                     'fetch_ffmpeg_sources.py', 'untrunc-sources.json'):
            package.write(SCRIPT_DIR / name, f'scripts/{name}')
        package.writestr('README.md', instructions)
        package.write(output / 'buildconf.txt', 'buildconf.txt')
        package.write(output / 'build-environment.txt', 'build-environment.txt')
    manifest = {
        'version': lock['version'], 'license': lock['license'],
        'files': {path.relative_to(bundle).as_posix(): digest(path) for path in sorted(bundle.rglob('*'))
                  if path.is_file() and path.name != 'manifest.json'},
        'sources': lock['sources'],
        'source_archive': {'name': archive.name, 'sha256': digest(archive)},
    }
    (bundle / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'finish'))
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    lock = load_sources()
    if args.action == 'prepare':
        prepare(args.output.resolve(), lock)
    else:
        finish(args.output.resolve(), lock)


if __name__ == '__main__':
    main()
