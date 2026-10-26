"""在可终止的子进程中读取配套图片，主任务只消费本机临时产物。"""
from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image

TIMEOUT_SECONDS = 20.0
MAX_BYTES = 32 * 1024 * 1024


def read_jpeg(source: Path, *, isolated=True) -> tuple[bytes, tuple[int, int]]:
    if not isolated:
        return _decode(source)
    from .images import measure_image_size

    command = ([sys.executable, 'local-image'] if getattr(sys, 'frozen', False)
               else [sys.executable, '-m', 'peach.local_poster'])
    with tempfile.TemporaryDirectory(prefix='peach-local-poster-') as temporary:
        output = Path(temporary).resolve() / 'poster.jpg'
        result = subprocess.run([*command, str(source), str(output)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT_SECONDS,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode:
            raise ValueError('配套图片无法读取')
        data = output.read_bytes()
    size = measure_image_size(data)
    if size is None:
        raise ValueError('配套图片无法读取')
    return data, size


def _decode(source):
    try:
        if source.stat().st_size > MAX_BYTES:
            raise ValueError('配套图片体积超出范围')
        with Image.open(source) as image:
            image.load()
            output = io.BytesIO()
            image.convert('RGB').save(output, format='JPEG', quality=95)
            return output.getvalue(), image.size
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise ValueError('配套图片无法读取') from error


def main(argv=None):
    parser = argparse.ArgumentParser(description='读取单张配套图片')
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args(argv)
    try:
        data, _ = _decode(args.source)
        args.output.write_bytes(data)
        return 0
    except (OSError, ValueError, Image.DecompressionBombError):
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
