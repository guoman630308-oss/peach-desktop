"""时间轴预览：每隔固定秒数一帧，按 10×10 拼成接触印相，供悬停扫画面用。

悬停扫的九宫格取的是全片九个等分点：一部两小时的片子，格与格之间隔着十几分钟，
想看某一段里有什么，扫过去的九张都对不上。这里换成按时间取——每 10 秒或 30 秒一帧，
帧在图上的位置就是它在片子里的位置。

两个口径都固定，前端据此换算格子位置，不必为每部片子多取一份索引：一张图 10 列
10 行共 100 帧，帧宽 320。间隔由用户在设置里选，和图一起写进 `meta.json`。

只对本机磁盘上的片子预生成。网盘上的每抽一帧都要拉一次远端读取，实测 115 单文件抽
九帧约 285 MB；两万部按每 10 秒一帧算，流量按 TB 计，与流量策略冲突。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from .frame_capture import capture_with_retry, tile_frames
from .fsutil import atomic_path
from .jobs import DiskSpaceDenied
from .media import resolve_case_insensitive

#: 两档密度，值就是秒数。名字进接口也进设置页，改这里就是改契约。
INTERVALS = {"precise": 10, "coarse": 30}
OFF = "off"
MODES = (OFF, *INTERVALS)

#: 一张图的格子数与帧宽。换算在前端也有一份，这三个数是两边的共同约定。
COLUMNS = 10
ROWS = 10
FRAMES_PER_SHEET = COLUMNS * ROWS
FRAME_WIDTH = 320

#: 头尾各让开一点：片头的黑场和片尾的字幕板扫过去什么都看不出来。
EDGE_MARGIN = 0.01


class _TileFailed(RuntimeError):
    """拼图这一步没出成图。只在本模块内部用来跳出 `atomic_path`。"""


def _fill_gaps(directory: Path, count: int) -> int:
    """把抽失败的格子用邻近那一帧补上，返回补完后连续的帧数。

    两件事都要这么做才成立。一是 `image2` 按连续序号读，中间缺一号它就在那里停下；
    二是这套图的全部意义在于「第 n 格就是第 n 个采样点」，缺一帧让后面全体前移一格，
    图还是满的，位置却整体错开——而错开多少取决于哪几帧解不出来，事后看不出来。
    补进去的是旁边那一帧，画面重复一格，位置不动。
    """
    slots = [directory / f"s{index:03d}.jpg" for index in range(count)]
    present = [index for index, path in enumerate(slots) if path.is_file()]
    if not present:
        return 0
    for index, path in enumerate(slots):
        if path.is_file():
            continue
        nearest = min(present, key=lambda good: (abs(good - index), good))
        shutil.copyfile(slots[nearest], path)
    return count


def sheet_dir(root: Path, asset_id: int) -> Path:
    """按 id 末两位分桶。一层平铺的话，本机这三千部就是几千个文件挤在一个目录里，
    Windows 上列目录会变慢，而这个目录正好是清理产物时要逐个看的那个。"""
    return Path(root) / f"{int(asset_id) % 100:02d}" / str(int(asset_id))


def meta_path(root: Path, asset_id: int) -> Path:
    return sheet_dir(root, asset_id) / "meta.json"


def sheet_path(root: Path, asset_id: int, index: int) -> Path:
    return sheet_dir(root, asset_id) / f"{int(index):03d}.jpg"


def frame_count(duration: float, interval: int) -> int:
    """这部片子按这个间隔有多少帧。至少两帧才谈得上扫。"""
    if not duration or duration <= 0 or interval <= 0:
        return 0
    return max(0, min(FRAMES_PER_SHEET * 999, int(duration // interval)))


def read_meta(root: Path, asset_id: int) -> dict | None:
    """已经生成过什么。读不出来当作没有：坏掉的 meta 重生成一次就好，
    比让整条链在一个残缺的 JSON 上崩掉划算。"""
    path = meta_path(root, asset_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("frames") else None


def is_current(root: Path, asset_id: int, interval: int) -> bool:
    """这部片子已经按这个间隔做好了，且每一张图都还在。

    只看 meta 不看图的话，用户手工清过产物目录之后，任务会把它们全部当成做好的跳过，
    页面上则是一片取不到的图。
    """
    meta = read_meta(root, asset_id)
    if not meta or int(meta.get("interval") or 0) != int(interval):
        return False
    return all(sheet_path(root, asset_id, index).is_file()
               for index in range(int(meta.get("sheets") or 0)))


def build(ffmpeg: str, source: str, duration: float, root: Path, asset_id: int,
          interval: int, *, active=lambda: True) -> dict:
    """给一部片子生成整套时间轴图，返回写进 `meta.json` 的那份。

    `active` 每帧问一次：一部三小时的片子有上千帧，中途按停就该停在这里，而不是
    等这一部跑完。半路停下不写 meta——没有 meta 就是没做过，下一趟从头来，不会
    留下一份「说有 700 帧、实际只有 300 帧」的索引。
    """
    total = frame_count(duration, interval)
    if total < 2:
        return {}
    sheets = (total + FRAMES_PER_SHEET - 1) // FRAMES_PER_SHEET
    directory = sheet_dir(root, asset_id)
    # 整套重做，所以先清干净。换档位时新的一套通常比旧的少几张，只覆盖不清理的话，
    # 多出来的那几张留在盘上，`is_current` 又不看它们，于是永远没人删。
    discard(root, asset_id)
    directory.mkdir(parents=True, exist_ok=True)
    made = 0
    produced = 0
    for index in range(sheets):
        first = index * FRAMES_PER_SHEET
        count = min(FRAMES_PER_SHEET, total - first)
        temporary = Path(tempfile.mkdtemp(prefix="timeline_"))
        try:
            slots = 0
            for offset in range(count):
                if not active():
                    return {}
                # 帧号乘间隔就是它在片子里的秒数，前端按同一条式子反算。头尾各让开
                # 一点，靠 EDGE_MARGIN 把采样点往里推，不改这条对应关系的斜率。
                timestamp = (first + offset) * interval + duration * EDGE_MARGIN
                if timestamp >= duration:
                    break
                slots = offset + 1
                capture_with_retry(ffmpeg, source, timestamp,
                                   temporary / f"s{offset:03d}.jpg",
                                   width=FRAME_WIDTH, timeout=20)
            captured = _fill_gaps(temporary, slots)
            if captured < 2:
                # 末张可能一格都排不进（`EDGE_MARGIN` 把最后一个采样点推过了片尾）。
                # 前面已经出了图就到此为止，别把整套连同已成的几十张一起作废。
                if produced:
                    break
                return {}
            rows = (captured + COLUMNS - 1) // COLUMNS
            try:
                with atomic_path(sheet_path(root, asset_id, index)) as destination:
                    if not tile_frames(ffmpeg, str(temporary / "s%03d.jpg"),
                                       COLUMNS, rows, destination):
                        # 拼不出来就让 `atomic_path` 按异常路径收尾：正常退出它会去
                        # 替换目标，而这时那个临时文件要么不存在要么是半张。
                        raise _TileFailed
            except _TileFailed:
                return {}
            made += captured
            produced += 1
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
    # `frames` 是连续的格子数，末张的行数由它反算（`ceil(余数 / columns)`）：那一张
    # 通常不满 10 行，按 `rows` 去算格子位置会在图外面取到空白。
    meta = {"interval": int(interval), "frames": made, "sheets": produced,
            "columns": COLUMNS, "rows": ROWS, "frame_width": FRAME_WIDTH,
            "made_at": time.time()}
    with atomic_path(meta_path(root, asset_id)) as destination:
        destination.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


#: 本机磁盘上的片子才预生成。这个清单是判据也是文案依据，改它就是改功能范围。
LOCAL_LOCATIONS = ("local",)

#: 用户按停时写进 `stopped` 的那句话。调用方按它区分「人停的」和「机器停的」：
#: 前者不是故障，后者要把原因摆到页面上。
STOPPED_BY_USER = "已按停"


def pending(db_path: Path | str, interval: int, *, limit: int = 0) -> list[tuple]:
    """待办队列：本机磁盘上、时长够两帧的视频，最近看过的排前面。

    排序不是装饰。这条链随时可能被磁盘闸门或用户按停，跑到哪算哪，所以先做的必须是
    最可能被悬停扫到的那些。
    """
    placeholders = ",".join("?" for _ in LOCAL_LOCATIONS)
    # 回收站里的不做：那一批等着被删，为它们各抽一百帧是白花的时间和磁盘。
    sql = ("SELECT id, path, duration FROM asset WHERE medium='video' "
           f"AND location IN ({placeholders}) AND duration >= ? "
           "AND disposal IS NULL "
           "ORDER BY (last_played IS NULL), last_played DESC, id")
    parameters: tuple = (*LOCAL_LOCATIONS, float(interval) * 2)
    if limit:
        sql += " LIMIT ?"
        parameters += (int(limit),)
    connection = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        return connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()


def generate_library(db_path: Path | str, root: Path, interval: int, *, ffmpeg: str,
                     report=lambda **fields: None, active=lambda: True,
                     guard=None, limit: int = 0, resume_state=None, checkpoint=lambda **fields: None) -> dict:
    """按队列逐部生成，返回这一趟的计数。

    失败不中断：一部片子解不出来（源坏了、时长记错、编码器不认）是它自己的事，
    队列后面还有几千部。磁盘触线是另一回事——那说明整台机器写不下了，立刻停手并
    把原因带出去，不能继续跑成「完成」。
    """
    tasks = pending(db_path, interval, limit=limit)
    state = {"total": len(tasks), "done": 0, "made": 0, "skipped": 0,
             "failed": 0, "stopped": ""}
    completed = set((resume_state or {}).get('completed_assets') or [])
    for key in ('done', 'made', 'skipped', 'failed'):
        state[key] = (resume_state or {}).get(key, 0)
    def save(asset_id):
        completed.add(asset_id)
        checkpoint(completed_assets=sorted(completed), **state)
    report(**state)
    for asset_id, path, duration in tasks:
        if asset_id in completed and is_current(root, asset_id, interval):
            continue
        from .strm import skip_background_read
        if skip_background_read(path):
            state['done'] += 1
            state['skipped'] += 1
            report(**state)
            continue
        if not active():
            state["stopped"] = STOPPED_BY_USER
            break
        if guard is not None:
            try:
                guard.check()
            except DiskSpaceDenied as exc:
                state["stopped"] = str(exc)
                break
        state["done"] += 1
        if is_current(root, asset_id, interval):
            state["skipped"] += 1
            save(asset_id)
            report(**state)
            continue
        # 账本路径是 Windows 形态，而挂载层可能是大小写敏感的：这一步同时管翻译和
        # 大小写救回，和抽帧脚本走的是同一份判据。
        source = Path(resolve_case_insensitive(path))
        if not source.is_file():
            state["failed"] += 1
            report(**state)
            continue
        if build(ffmpeg, str(source), float(duration or 0), root, int(asset_id),
                 interval, active=active):
            state["made"] += 1
            save(asset_id)
        else:
            state["failed"] += 1
        report(**state)
    return state


def discard(root: Path, asset_id: int) -> None:
    """删掉一部片子的整套图。关掉采集时按它清退，产物不留在盘上。"""
    shutil.rmtree(sheet_dir(root, asset_id), ignore_errors=True)
