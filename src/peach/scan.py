"""扫描一个来源的目录，把文件元数据写进账本。

这是摄取入口，不是复核写入：它新增行、刷新 `size`／`mtime`／`last_seen`，不改任何
真相字段——本次没扫到的文件只会在 `last_seen` 上落后，由资源同步对账决定去留。

文件消失、带着个人记录的行由资源同步标 `disposal='vanished'`（ADR-0087）。登记时把它们接回：
同一路径的文件回来了，upsert 清掉那一行的 `disposal`；新登记的行由 `record_rehome.reattach`
按番号或「创作者 + 文件名主干 + 时长」找它的旧行，把记录搬过来、删掉旧行，记一个可撤回的批次。
接着由 `wants.reconcile` 按番号对一遍「想要」清单，命中的标「已入库」，同样记可撤回的批次。

两条不变量都在这里守：

- `asset.location` 是来源 ID，`asset.path` 一律是 Windows 形态的账本路径。扫描根必须
  落在该来源 `[media.locations]` 数据库路径前缀之内（`check_scan_target`），否则写进去的行既
  翻译不出本机路径、也通不过授权根，而且要等到有人点开那个资产才会发现。
- 目录在本机哪里，由 `[media.mounts]` 决定。Windows 上盘符本身就是挂载点，数据库路径前缀原样
  就是要遍历的目录；macOS 上遍历的是挂载点，写进账本的仍是 `数据库路径前缀\\相对路径`，这样
  读取侧的 `platform.translate_ledger_path` 才翻得回同一个文件。

字幕 sidecar 顺带在这里登记：遍历时每个目录的文件名已经在手上，配对判据只看同目录，
`subtitles.py` 负责判定，本模块只负责把结论落进 `asset_subtitle`。

刮削器与播放器写在正片旁边的附属文件不登记（`is_sidecar`）：NFO、Kodi 命名的海报与背景图、
`extrafanart/` 里的剧照、截图工具拼出的缩略图。它们描述一部片，本身不是作品，登记进来就会在
馆藏里冒出成百张「图片」，把真正的图集淹掉。

`peach init` 的首次扫描与 `peach scan` 调 `scan_location` 遍历整个根；推送发现拿到一条
路径时调 `ingest_path`，登记口径、不变量与 upsert 语句都是同一份。
数据库路径前缀和挂载表由调用方传入而不是读进程缓存：`init` 刚写完设置文件时缓存还是旧的。
"""
from __future__ import annotations

import os
import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from filelock import FileLock

from . import folder_rules, record_rehome, subtitles, wants
from .platform import is_windows_path, resolve_location, resolve_root
from .processing_control import checkpoint
from .fsutil import atomic_write_text

VIDEO = {".mp4", ".m4v", ".mkv", ".avi", ".wmv", ".mov", ".ts", ".flv", ".rmvb", ".mpg",
         ".m2ts", ".strm", ".iso"}
IMAGE = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
AUDIO = {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".opus"}
ARCHIVE = {".zip", ".rar", ".7z", ".tar", ".gz"}

#: 每攒够这么多行落一次盘，并报一行进度。
BATCH_SIZE = 2000

#: Kodi／Jellyfin 的影片图片名（单写或挂在片名后面，如 `ABC-123-fanart.jpg`）。
#: 只在同目录有视频时算附属：没有正片的目录里，`cover.jpg` 是图集自己的封面。
ARTWORK = frozenset({
    "poster", "fanart", "thumb", "folder", "cover", "banner", "clearart", "clearlogo",
    "landscape", "disc", "discart", "logo", "backdrop", "keyart",
})
#: 装一部片剧照的整个子目录，只在上一层有视频时算附属。
ARTWORK_DIRS = frozenset({"extrafanart", "extrathumbs"})
#: 截图工具的产物，看名字就能认出来，不必看同目录：`X_4x4_thumb.jpg`（拼版缩略图）、
#: `X.mp4_thumbs.jpg`（MPC-HC 存缩略图）、`X.png.thumb.png`（图片的缩略图）。
_DERIVED_THUMB = re.compile(
    r"(?:_\d+x\d+_thumbs?|\.(?:" + "|".join(sorted(ext[1:] for ext in VIDEO))
    + r")_thumbs?|\.(?:" + "|".join(sorted(ext[1:] for ext in IMAGE)) + r")\.thumb)$",
    re.IGNORECASE)

# 同一路径上的文件回来了，标「已消失」的那一行就是它：清掉 `disposal`，不另起一行。
# 回收站里的行是用户丢的，文件在不在都不改。
_UPSERT = """INSERT INTO asset(location,path,name,medium,size,mtime,first_seen,last_seen)
             VALUES(?,?,?,?,?,?,?,?)
             ON CONFLICT(location,path) DO UPDATE SET
               medium=excluded.medium, size=excluded.size, mtime=excluded.mtime, last_seen=excluded.last_seen,
               disposal=CASE WHEN asset.disposal='vanished' THEN NULL ELSE asset.disposal END"""


class ScanTargetError(ValueError):
    """来源与扫描根对不上，或者来源在本机没有落点。消息可以直接给人看。"""


def medium_of(name: str) -> str:
    suffix = os.path.splitext(name)[1].lower()
    if suffix in VIDEO:
        return "video"
    if suffix in IMAGE:
        return "image"
    if suffix in AUDIO:
        return "audio"
    if suffix in ARCHIVE:
        return "archive"
    return "other"


def video_stems(names) -> frozenset[str]:
    """一个目录里视频文件的片名（casefold），`is_sidecar` 拿它判同目录有没有正片。"""
    return frozenset(os.path.splitext(name)[0].casefold() for name in names
                     if medium_of(name) == "video")


def is_sidecar(name: str, videos: frozenset[str], *, artwork_dir: bool = False) -> bool:
    """`name` 是否为不登记的附属文件或工具隔离文件。

    `videos` 是同目录的 `video_stems`；`artwork_dir` 表示这个目录叫 `extrafanart` 之类、
    且上一层有视频。判据只看名字，不读文件内容。
    """
    if re.fullmatch(r'peach-purge-[a-f0-9]{32}\.peach-quarantine', name, re.IGNORECASE):
        return True
    stem, suffix = os.path.splitext(name)
    if suffix.lower() == ".nfo":
        return True
    if medium_of(name) != "image":
        return False
    if artwork_dir or _DERIVED_THUMB.search(stem):
        return True
    if not videos:
        return False
    lowered = stem.casefold()
    if lowered in ARTWORK:
        return True
    head, dash, tail = lowered.rpartition("-")
    return bool(dash) and tail in ARTWORK and head in videos


def check_scan_target(
    location: str, root: str, *, declared_roots: Mapping[str, Sequence[str]],
) -> None:
    """扫描根必须落在这个来源的某个数据库路径前缀内，否则拒绝（ADR-0023 第 2 阶段的写入侧门槛）。"""
    declared = declared_roots.get(location)
    if declared is None:
        known = "、".join(sorted(declared_roots)) or "（设置文件里一个都没有）"
        raise ScanTargetError(f"✗ 未声明的来源 {location!r}；[media.locations] 里已知：{known}")
    actual = resolve_location(root, declared_roots)[0]
    if actual != location:
        raise ScanTargetError(
            f"✗ 扫描目录与媒体来源不一致：{location} 的数据库路径前缀是 {'、'.join(declared)}，"
            f"但要扫的是 {root}"
            + (f"（该目录属于 {actual}）" if actual else "（不在任何已配置的媒体目录下）")
        )


def ledger_root_for(
    location: str, root: str | os.PathLike[str], *,
    declared_roots: Mapping[str, Sequence[str]],
    mounts: Mapping[str, Sequence[str | Path]], windows: bool | None = None,
) -> str:
    """将媒体目录转换为数据库使用的 Windows 格式路径。

    Windows 上任何绝对路径本来就是账本形态，相对路径先按当前目录补全。其他平台上
    给的是本机目录（某个挂载点下的某一层），换成对应那个 `数据库路径前缀\\相对路径`；没有
    挂载点或不在任何挂载点下都拒绝——那种目录扫出来的行谁也翻译不回去。
    """
    windows = os.name == "nt" if windows is None else windows
    text = os.fspath(root)
    if windows:
        return text if is_windows_path(text) else str(Path(text).resolve())
    declared = declared_roots.get(location, ())
    location_mounts = tuple(mounts.get(location, ()))
    for declared_root, mount in zip(declared, location_mounts):
        try:
            tail = Path(text).resolve().relative_to(Path(mount).resolve()).parts
        except ValueError:
            continue
        return str(PureWindowsPath(declared_root, *tail))
    if is_windows_path(text):
        return text
    if not location_mounts:
        raise ScanTargetError(
            f"✗ 媒体来源 {location!r} 未配置本机目录，无法转换 {text} 的路径；"
            f"请先在 [media.mounts] 中配置媒体目录")
    shown = "、".join(os.fspath(mount) for mount in location_mounts)
    raise ScanTargetError(f"✗ {text} 不在来源 {location!r} 的挂载点 {shown} 之下")


def walk_root_for(
    location: str, root: str, *, declared_roots: Mapping[str, Sequence[str]],
    mounts: Mapping[str, Sequence[str | Path]], windows: bool | None = None,
) -> Path:
    """取得数据库路径在本机对应的扫描目录。调用前先过 `check_scan_target`。"""
    windows = os.name == "nt" if windows is None else windows
    if windows:
        return Path(root)
    _location, index, tail = resolve_root(root, declared_roots)
    location_mounts = tuple(mounts.get(location, ()))
    if index >= len(location_mounts):
        raise ScanTargetError(
            f"✗ 媒体来源 {location!r} 的第 {index + 1} 个数据库路径前缀没有对应的本机目录；"
            f"请先在 [media.mounts] 中按相同顺序配置媒体目录")
    return Path(location_mounts[index]).joinpath(*tail)


@dataclass(frozen=True)
class IngestResult:
    """单条路径的登记结果。`found` 为假表示那个文件此刻不在磁盘上，什么也没写。"""
    location: str
    path: str
    found: bool
    size: int = 0
    subtitles: int = 0
    #: 文件在，但认作附属文件（`is_sidecar`），什么也没写。
    sidecar: bool = False
    #: 登记的那一行；`new` 表示这一次才新建。
    asset_id: int | None = None
    new: bool = False
    #: 新行接回了几条已消失作品的个人记录（`record_rehome`）。
    reattached: int = 0
    #: 新行让几条「想要」标了已入库（`wants.reconcile`）。
    acquired: int = 0


def _directory_stats(directory: Path) -> dict[str, tuple[int, str]]:
    """一个目录里能 stat 到的文件名 → `(size, mtime)`，读不了的条目跳过。"""
    stats: dict[str, tuple[int, str]] = {}
    try:
        with os.scandir(directory) as scanner:
            entries = list(scanner)
    except OSError:
        return stats
    for entry in entries:
        try:
            if entry.is_dir():
                continue
            found = entry.stat()
        except OSError:
            continue
        stats[entry.name] = (
            found.st_size, time.strftime("%Y-%m-%d", time.localtime(found.st_mtime)))
    return stats


def _sidecar_on_disk(name: str, directory: Path) -> bool:
    """单条登记时的 `is_sidecar`：只在名字本身判不完时才列目录。"""
    if is_sidecar(name, frozenset()):
        return True
    if medium_of(name) != "image":
        return False
    if directory.name.casefold() in ARTWORK_DIRS and video_stems(
            _directory_stats(directory.parent)):
        return True
    stem = os.path.splitext(name)[0].casefold()
    if stem not in ARTWORK and stem.rpartition("-")[2] not in ARTWORK:
        return False
    return is_sidecar(name, video_stems(_directory_stats(directory)))


def ingest_path(
    db_path: str | os.PathLike[str], location: str, path: str, *,
    declared_roots: Mapping[str, Sequence[str]],
    mounts: Mapping[str, Sequence[str | Path]] | None = None,
    windows: bool | None = None,
) -> IngestResult:
    """按扫描的同一口径登记一个文件。

    `path` 是数据库使用的 Windows 格式绝对路径。两条不变量与 `scan_location` 共用同一段判定，upsert
    也是同一条语句：推送发现拿到一条路径之后走的就是这里，账本里不会出现第二种
    「新文件怎么变成一行」的写法。

    文件不在就直接返回：事件到达与文件落地之间总有时间差，那不是错误；定期全量扫描
    与资源同步对账各自会处理。字幕 sidecar 的配对按定义只看同一个目录，所以只有视频
    或字幕才多列一次那个目录，别的类型连一次 `scandir` 都不发。附属文件的判定同理：
    只有叫 `poster.jpg` 这类名字、或落在 `extrafanart/` 里的图片才去列目录看有没有正片。
    所有列目录与 stat 都在打开账本连接之前做完，写事务里只有 SQL。
    """
    windows = os.name == "nt" if windows is None else windows
    ledger_path = PureWindowsPath(path)
    ledger_dir = ledger_path.parent
    name = ledger_path.name
    check_scan_target(location, str(ledger_dir), declared_roots=declared_roots)
    directory = walk_root_for(
        location, str(ledger_dir), declared_roots=declared_roots,
        mounts=mounts or {}, windows=windows)
    try:
        stat = (directory / name).stat()
    except OSError:
        return IngestResult(location, str(ledger_path), False)
    if _sidecar_on_disk(name, directory):
        return IngestResult(location, str(ledger_path), True, stat.st_size, sidecar=True)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    mtime = time.strftime("%Y-%m-%d", time.localtime(stat.st_mtime))
    medium = medium_of(name)
    tracks = 0
    # 列目录在开连接之前做完：写事务从 upsert 开始一直持有写锁，网盘挂载卡住的那几十秒
    # 不能落在锁里，否则同一账本的其他写入者全部跟着超时。
    pairing = None
    if medium == "video" or subtitles.subtitle_format(name):
        here = _directory_stats(directory)
        pairing = subtitles.directory_sidecars(
            ledger_dir, here, [entry for entry in here if medium_of(entry) == "video"])
    # 忙等时长与 `LedgerDatabase.connect` 一致。
    connection = sqlite3.connect(db_path, timeout=30)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(_UPSERT, (location, str(ledger_path), name, medium,
                                     stat.st_size, mtime, now, now))
        if pairing is not None:
            tracks = subtitles.record(connection, location, pairing, now)[0]
        asset_id, first_seen = connection.execute(
            "SELECT id,first_seen FROM asset WHERE location=? AND path=?",
            (location, str(ledger_path))).fetchone()
        new = first_seen == now
        reattached = len(record_rehome.reattach(connection, [asset_id] if new else [], now=now))
        acquired = len(wants.reconcile(connection, [asset_id] if new else []))
        folder_rules.apply_new(connection, [asset_id] if new else [])
        connection.commit()
    finally:
        connection.close()
    return IngestResult(location, str(ledger_path), True, stat.st_size, tracks,
                        asset_id=asset_id, new=new, reattached=reattached, acquired=acquired)


@dataclass(frozen=True)
class ScanResult:
    location: str
    root: str
    files: int
    total_bytes: int
    seconds: float
    #: 该来源里本次没扫到的行数（`last_seen` 落后于本次）。
    gone: int
    #: 本次登记的字幕 sidecar 条数，其中配不到视频的那部分。
    subtitles: int = 0
    orphan_subtitles: int = 0
    #: 认作附属文件、没有登记的条数（`is_sidecar`）。
    sidecars: int = 0
    #: 本次新建的行；探完时长之后 `record_rehome.reattach_in` 拿它们再判一轮。
    new_ids: tuple[int, ...] = ()
    #: 本次接回了几条已消失作品的个人记录。
    reattached: int = 0
    #: 本次让几条「想要」标了已入库。
    acquired: int = 0

    def summary(self) -> str:
        return (f"✓ {self.location}: {self.files:,} 文件 / "
                f"{self.total_bytes / 1024 ** 4:.2f} TB / 耗时 {self.seconds:.0f}s；"
                f"附属文件未登记 {self.sidecars:,} 个；清单中已消失 {self.gone:,} 个；"
                f"字幕 {self.subtitles:,} 条（孤立 {self.orphan_subtitles:,} 条）")


def _walk(top: Path, pending: list[str] | None = None):
    """自顶向下遍历，每个目录给出它里面的非目录条目（`os.DirEntry`）。

    与 `os.walk` 的差别只在一处：条目的大小与时间从 `DirEntry.stat()` 拿。Windows 的目录
    列表自带这两项，不必再对每个文件发一次 `os.stat`——网盘挂载上那一次就是一趟往返，
    几万个文件就是几万趟。读不了的目录跳过；符号链接指向的目录列出但不进入。
    """
    stack = pending if pending is not None else [os.fspath(top)]
    while stack:
        checkpoint('scan')
        directory = stack.pop()
        try:
            with os.scandir(directory) as scanner:
                entries = list(scanner)
        except OSError:
            continue
        files = []
        subdirectories = []
        for entry in entries:
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            if is_dir:
                try:
                    if not entry.is_symlink() and entry.name.casefold() != "#recycle" and not entry.name.startswith("@"):
                        subdirectories.append(entry.path)
                except OSError:
                    pass
            else:
                files.append(entry)
        # 倒着压栈，弹出来就是字典序，进度行读起来和目录里看到的一致。
        queued = set(stack)
        stack.extend(path for path in sorted(subdirectories, reverse=True) if path not in queued)
        yield directory, sorted(files, key=lambda entry: entry.name)


class _ScanProgress:
    """结构化扫描进度按时间节流，首项与最终汇总始终报告。"""
    def __init__(self, callback):
        self.callback = callback
        self.reported_at = 0.0

    def __call__(self, files, total_bytes, *, force=False):
        now = time.monotonic()
        if force or files == 1 or now - self.reported_at >= 0.5:
            self.callback(files, total_bytes)
            self.reported_at = now


def scan_location(db_path, location, root, **kwargs) -> ScanResult:
    """命令行按来源与根目录保存断点，组合任务可提供自己的持久检查点。"""
    if 'save_checkpoint' in kwargs or 'resume_state' in kwargs:
        return _scan_location(db_path, location, root, **kwargs)
    from .update_tasks import state_for
    key = hashlib.sha256(f'{location}:{root}'.encode()).hexdigest()
    path = state_for(Path(db_path)).parent / 'scan-checkpoints' / f'{key}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock', timeout=0):
        previous = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        result = _scan_location(db_path, location, root, **kwargs, resume_state=previous,
            save_checkpoint=lambda state: atomic_write_text(path, json.dumps(state, ensure_ascii=False)))
        path.unlink(missing_ok=True)
        return result


def _pending_directories(previous, walk_root):
    """恢复目录栈，并核对本机挂载仍属于当前扫描根。"""
    pending = list(previous.get('pending', [os.fspath(walk_root)]))
    if previous.get('current_directory'):
        pending.append(previous['current_directory'])
    if any(not Path(value).is_relative_to(walk_root) for value in pending):
        raise ScanTargetError('扫描目录与保存的断点不一致，请核对挂载设置')
    return pending


def _scan_location(
    db_path: str | os.PathLike[str], location: str, root: str, *,
    declared_roots: Mapping[str, str], mounts: Mapping[str, str | Path] | None = None,
    report: Callable[[str], None] = print, windows: bool | None = None,
    progress: Callable[[int, int], None] = lambda files, total_bytes: None,
    resume_state: dict | None = None,
    save_checkpoint: Callable[[dict], None] = lambda state: None,
) -> ScanResult:
    """遍历 `root`（数据库中的路径格式）对应的本机目录，把文件元数据 upsert 进 `asset`。

    读不了的目录和文件直接跳过，不中断整轮：网盘挂载里零星几个条目读失败很常见，
    一个坏条目不该让几万个好条目白扫。
    """
    check_scan_target(location, root, declared_roots=declared_roots)
    walk_root = walk_root_for(
        location, root, declared_roots=declared_roots, mounts=mounts or {}, windows=windows)
    previous = resume_state or {}
    if previous.get('result'):
        return ScanResult(**{**previous['result'], 'new_ids': tuple(previous['result']['new_ids'])})
    ledger_root = PureWindowsPath(root)
    now = previous.get('scan_time') or time.strftime("%Y-%m-%d %H:%M:%S")
    started = time.time()
    progress = _ScanProgress(progress)
    files = previous.get('files', 0)
    total = previous.get('total_bytes', 0)
    skipped = previous.get('sidecars', 0)
    tracks = previous.get('subtitles', 0)
    orphans = previous.get('orphan_subtitles', 0)
    video_dirs = set(previous.get('video_dirs', []))
    pending = _pending_directories(previous, walk_root)
    batch: list[tuple] = []
    # 文件批次提交后保存文件名游标；同目录的配对资料随断点保留。
    def save_directory(directory=None, cursor=None, here=None):
        artwork_parents = {os.path.dirname(value) for value in [*pending, *([directory] if directory else [])]
                           if os.path.basename(value).casefold() in ARTWORK_DIRS}
        video_dirs.intersection_update(artwork_parents)
        save_checkpoint(dict(scan_time=now, pending=list(pending), files=files,
                             total_bytes=total, sidecars=skipped, subtitles=tracks,
                             orphan_subtitles=orphans, video_dirs=sorted(video_dirs),
                             current_directory=directory, cursor=cursor, here=dict(here or {})))
    # 写事务只在每批 upsert 到 commit 之间打开，遍历与 stat 都在两次 commit 之间，不持锁。
    # 忙等时长与 `LedgerDatabase.connect` 一致。
    connection = sqlite3.connect(db_path, timeout=30)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        for directory, entries in _walk(walk_root, pending):
            checkpoint('scan')
            relative = Path(directory).relative_to(walk_root).parts
            continuing = directory == previous.get('current_directory')
            here = dict(previous.get('here') or {}) if continuing else {}
            cursor = previous.get('cursor') if continuing else None
            videos = video_stems(entry.name for entry in entries)
            if videos:
                video_dirs.add(directory)
            # 自顶向下遍历，上一层总是先于这一层出来，这时已经知道它有没有视频。
            artwork_dir = (os.path.basename(directory).casefold() in ARTWORK_DIRS
                           and os.path.dirname(directory) in video_dirs)
            for entry in entries:
                checkpoint('scan')
                name = entry.name
                if cursor is not None and name <= cursor:
                    continue
                if is_sidecar(name, videos, artwork_dir=artwork_dir):
                    skipped += 1
                    continue
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                ledger_path = str(ledger_root.joinpath(*relative, name))
                mtime = time.strftime("%Y-%m-%d", time.localtime(stat.st_mtime))
                here[name] = (stat.st_size, mtime)
                batch.append((location, ledger_path, name, medium_of(name),
                              stat.st_size, mtime, now, now))
                files += 1
                total += stat.st_size
                progress(files, total)
                if len(batch) >= BATCH_SIZE:
                    connection.executemany(_UPSERT, batch)
                    connection.commit()
                    batch.clear()
                    save_directory(directory, name, here)
                    report(f"  {time.time() - started:5.0f}s  {files:,} 文件  "
                           f"{total / 1024 ** 4:.2f} TB")
            sidecars = subtitles.directory_sidecars(
                ledger_root.joinpath(*relative), here,
                [name for name in here if medium_of(name) == "video"])
            if batch:
                connection.executemany(_UPSERT, batch)
                batch.clear()
            directory_tracks, directory_orphans = subtitles.record(connection, location, sidecars, now)
            connection.commit()
            tracks += directory_tracks
            orphans += directory_orphans
            save_directory()
        if batch:
            connection.executemany(_UPSERT, batch)
        connection.commit()
        # 只有 INSERT 那一支写 `first_seen`，等于本次时刻的就是这一轮新建的行。
        new_ids = tuple(row[0] for row in connection.execute(
            "SELECT id FROM asset WHERE location=? AND first_seen=? ORDER BY id", (location, now)))
        reattached = len(record_rehome.reattach(connection, new_ids, now=now))
        acquired = len(wants.reconcile(connection, new_ids))
        folder_rules.apply_new(connection, new_ids)
        connection.commit()
        gone =connection.execute(
            "SELECT COUNT(*) FROM asset WHERE location=? AND last_seen<?",
            (location, now)).fetchone()[0]
    finally:
        connection.close()
    result = ScanResult(location, root, files, total, time.time() - started, gone,
                        tracks, orphans, skipped, new_ids, reattached, acquired)
    save_checkpoint({'result': asdict(result)})
    progress(files, total, force=True)
    report(result.summary())
    return result
