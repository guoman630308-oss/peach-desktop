"""按番号抓取最高可得官方封套。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach.jav_cover_fetch import main

if __name__ == "__main__":
    raise SystemExit(main())
