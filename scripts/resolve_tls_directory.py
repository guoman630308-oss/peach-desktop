#!/usr/bin/env python3
"""输出配置中的 TLS 目录，供维护脚本使用；不创建目录或读取证书。"""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.settings_file import load_config


def tls_directory() -> Path:
    return load_config().directory("secrets") / "tls"


if __name__ == "__main__":
    print(tls_directory())
