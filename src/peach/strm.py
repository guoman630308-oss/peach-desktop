"""STRM 文本指针：只读取授权目录中的小文件，播放时才解析目标。"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

MAX_BYTES = 64 * 1024
POINTER_SUFFIX = '.strm'
DISC_SUFFIXES = frozenset({'.iso'})


def is_pointer(path: str | Path) -> bool:
    return Path(path).suffix.lower() == POINTER_SUFFIX


def skip_background_read(path: str | Path) -> bool:
    return Path(path).suffix.lower() in DISC_SUFFIXES | {POINTER_SUFFIX}


def background_rows(rows, path_index: int):
    """批处理只领取可读取的视频文件，保留调用方的行形状。"""
    return [row for row in rows if not skip_background_read(row[path_index])]


def validate_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        valid = (parts.scheme.lower() in {'http', 'https'} and parts.hostname
                 and not parts.username and not parts.password and parts.port != 0)
    except ValueError:
        valid = False
    if not valid or any(ord(char) < 33 or ord(char) == 127 for char in url) or '\\' in url:
        raise ValueError('STRM 需要不含账号密码的 HTTP 或 HTTPS 播放地址')
    return url


def read_target(path: Path) -> str:
    try:
        with path.open('rb') as handle:
            content = handle.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError('STRM 文件超过 64 KiB')
        lines = [line.strip() for line in content.decode('utf-8-sig').splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
    except (OSError, UnicodeError) as error:
        raise ValueError('STRM 文件未取得或不是 UTF-8 文本') from error
    if len(lines) != 1:
        raise ValueError('STRM 文件需要且只能包含一个播放地址')
    return validate_url(lines[0])
