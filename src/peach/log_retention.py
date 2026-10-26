"""`<Peach 数据目录>/logs` 里的日志统一保留半年，大小不设限。

托盘子服务、首次扫描和源码同步都把 stdout 直接追加进各自的 `.log`，不经 `logging`，
所以 `RotatingFileHandler` 用不上；这里只看文件本身的修改时间：

- 半年没再写过的文件整份删掉——它里面没有一行是半年内的。
- 还在写的文件按自然月切段：托盘启动时发现上次写入落在更早的月份，就把它改名成
  `<名字>.until-<最后写入日期>.log`，下一次写入从空文件开始。切下来的段再等半年被第一条
  规则收走。

只用修改时间，不用创建时间：Windows 上追加写的文件 `st_ctime` 是创建时间，macOS 是
`st_birthtime`，Linux 常常两者都不可靠，而 `os.utime` 在三处都能改 mtime，测试才立得住。
必须在任何子进程打开日志之前跑：Windows 上被别的进程占着的文件改不了名也删不掉。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

__all__ = ["RETENTION", "sweep"]

#: 半年。
RETENTION = timedelta(days=183)

#: 切下来的段名里的标记；带它的文件不再二次切段。
_SEGMENT_MARK = ".until-"


def sweep(log_dir: Path, *, now: datetime | None = None) -> list[str]:
    """按上面两条规则整理一个日志目录，返回做过的事（供日志与测试）。"""
    if not log_dir.is_dir():
        return []
    moment = now or datetime.now()
    actions: list[str] = []
    for path in sorted(log_dir.glob("*.log")):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            continue
        if moment - modified > RETENTION:
            try:
                path.unlink()
            except OSError as error:
                actions.append(f"kept {path.name}: {error.strerror or error}")
                continue
            actions.append(f"deleted {path.name}")
            continue
        if _SEGMENT_MARK in path.name:
            continue
        if (modified.year, modified.month) < (moment.year, moment.month):
            segment = path.with_name(f"{path.stem}{_SEGMENT_MARK}{modified:%Y%m%d}{path.suffix}")
            if segment.exists():
                # 同一天切过两次只会发生在同一秒内重启两遍；后来的这份并进去比覆盖安全。
                try:
                    with segment.open("ab") as target, path.open("rb") as source:
                        target.write(source.read())
                    path.unlink()
                except OSError as error:
                    actions.append(f"kept {path.name}: {error.strerror or error}")
                    continue
            else:
                try:
                    path.replace(segment)
                except OSError as error:
                    actions.append(f"kept {path.name}: {error.strerror or error}")
                    continue
            actions.append(f"rotated {path.name} -> {segment.name}")
    return actions
