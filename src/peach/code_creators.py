r"""被投影成创作者的番号目录名：判定与清理。

旧导入器把发行目录名直接当创作者写进 ledger。当目录名本身就是番号时，创作者索引里
就会多出一个假身份。实测样例：

    B:\云下载\HD-abp-758\HD-abp-758.mp4      creator=HD-abp-758  code=NULL
    B:\云下载\pppd-937ch\PPPD-937CH.mp4      creator=pppd-937ch  code=PPPD-937
    B:\云下载\Jav.li_MIAD573_HD\MIAD573_01.wmv  creator=Jav.li_MIAD573_HD  code=MIAD-573

`HD-` 前缀是画质标记，不是番号的一部分；`-CH`/`C` 是中文字幕版后缀。

同一形态里混着真实上传者账号，绝不能一起删：

    A:\Pack From Shared\pen\banbi_555\18歳Eカップ...mp4       banbi_555 是账号
    https://www.pixiv.net/users/93812377                      AH18 是 pixiv 画师

因此判定不看名字形态，只看文件级证据：目录内的媒体文件名必须解析出同一个番号。
账号目录里的文件是作品标题，解析不出番号，天然不会命中；pixiv 行的 path 是 URL，
直接排除。证据成立的那批由 `scripts/audit_code_creators.py --apply` 带备份清理，
证据不成立的留人工。

`collect` 的输出就是复核页那一类的队列：这一类现算，不读候选文件。判据的输入只有账本
本身，没有需要留存的外部证据，而按 CSV 快照摆队列会摆出早已不存在的实体——实测一份
44 行的快照里 27 行属于此类，点进去无事可做。
"""
from __future__ import annotations

import re
import sqlite3

from .catalog_rules import (
    compact_label,
    is_jav_code,
    normalise_code_key,
    release_code_from_filename as code_from_filename,
    release_code_from_text as canonical_code,
)
from .field_owners import write_owned_fields

#: fantia 之类的站点作品号既不是番号也不是创作者，单独归类。
_SITE_POST = re.compile(r"^(fantia)[-_](\d{6,10})$", re.I)
_EXTENSION = re.compile(r"\.(?:mp4|mkv|avi|wmv|ts|mov|m4v|jpg|jpeg|png|webp)$", re.I)

VERDICT_CODE = "番号"
VERDICT_SITE = "站点作品号"
VERDICT_UNCLEAR = "存疑"
VERDICT_KEEP = "保留"
#: 有文件级证据、可以直接清理的判定。
SETTLED_VERDICTS = (VERDICT_CODE, VERDICT_SITE)

FIELDS = ("entity_id", "creator", "verdict", "identity", "assets", "sample_path",
          "code_action", "reason")


def site_post_id(value: str) -> str | None:
    match = _SITE_POST.match(_EXTENSION.sub("", (value or "").strip()))
    return f"{match.group(1).lower()}-{match.group(2)}" if match else None


def is_filesystem_path(path: str) -> bool:
    """pixiv 等在线身份的 path 是 URL，不参与目录投影判定。"""
    return bool(path) and not re.match(r"^[a-z][a-z0-9+.-]*://", path, re.I)


def embedded_code(name: str, assets: list) -> str:
    """目录名里嵌着的发行番号，没有就是空串。

    `canonical_code` 认的是「整个名字就是番号」。下载站在番号前后贴上站名、画质和
    分享标记后它就认不出了，而这些目录同样不是创作者：`Jav.li_MIAD573_HD`、
    `[98t.tv][98t.tv]ABW-251`、`nes@第一会所@ATID-479`、`kpxvs-300MIUM-698`。

    以资产那一侧的番号为准去名字里找，名字形态不参与判断——反过来从名字里抠番号，
    会把 `banbi_555` 这类上传者账号抠成 `BANBI-555`。番号要么账本已记、要么由文件名
    解析得出，两头对上才算数；`is_jav_code` 把目录名冒充的番号挡在外面。
    """
    compact = compact_label(name)
    for row in assets:
        code = (str(row["code"] or "").strip()
                or code_from_filename(str(row["name"] or "")) or "")
        if code and is_jav_code(code) and compact_label(code) in compact:
            return normalise_code_key(code)
    return ""


def classify(name: str, assets: list) -> tuple[str, str, str]:
    """返回 (判定, 归一标识, 理由)。只有文件级证据成立才判为可清理。"""
    post = site_post_id(name)
    code = canonical_code(name)
    if not all(is_filesystem_path(str(row["path"] or "")) for row in assets):
        return VERDICT_KEEP, "", "存在 URL 身份（如 pixiv 画师），不是发行目录"
    if not post and not code:
        embedded = embedded_code(name, assets)
        if not embedded:
            return VERDICT_KEEP, "", "名字不是番号形态"
        return (VERDICT_CODE, embedded,
                f"目录名里嵌着番号 {embedded}，与目录内文件对得上")

    identity = post or code or ""
    hit = any(
        (site_post_id(str(row["name"] or "")) or code_from_filename(str(row["name"] or "")))
        == identity
        or str(row["code"] or "").upper() == identity
        for row in assets
    )
    if not hit:
        return (VERDICT_UNCLEAR, identity,
                "名字像番号，但目录内没有同番号文件；可能是真实账号名")
    if post:
        return VERDICT_SITE, identity, f"目录与文件同为站点作品号 {identity}"
    return VERDICT_CODE, identity, f"目录与文件同为番号 {identity}"


#: 来源片长与本地时长的容差。片源前后常挂一两分钟广告（用户 2026-09-16），来源本身
#: 只报整分钟，再放宽 30 秒。
DURATION_TOLERANCE_SECONDS = 150


def main_video_seconds(assets: list) -> float | None:
    """目录里最长那条视频的时长，一条都量不到就是 None。

    目录里常混着预告、论坛文宣和封面图（`Tokyo-Hot n0780-HD` 里就有两个），拿它们和
    正片的片长比必然对不上。最长的那条才是正片。
    """
    durations = [float(row["duration"]) for row in assets
                 if str(row["medium"] or "") == "video" and row["duration"] is not None
                 and float(row["duration"]) > 0]
    return max(durations) if durations else None


def duration_confirms_code(local_seconds: float | None,
                           runtime_minutes: float | None) -> tuple[bool, str]:
    """来源报的片长认不认这个目录就是这个番号，附一句可读的理由。

    目录名像番号、里面的文件却不带番号时，名字本身答不了「这是发行目录还是上传者账号」
    （`banbi_555` 与 `Tokyo-Hot n0780-HD` 同一形态）。片长是本机可核验的第二条证据：
    对得上就是这部片，对不上（`bbsxv.xyz-DOCP-324` 目录里只有一条 90 秒的广告）留人工。
    """
    if local_seconds is None:
        return False, "目录里没有量到时长的视频"
    if not runtime_minutes:
        return False, "来源没有给片长"
    delta = abs(local_seconds - float(runtime_minutes) * 60)
    verdict = delta <= DURATION_TOLERANCE_SECONDS
    return verdict, (f"本地 {local_seconds / 60:.1f} 分、来源 {float(runtime_minutes):.0f} 分，"
                     f"差 {delta:.0f} 秒{'，在容差内' if verdict else '，超出容差'}")


def collect(connection: sqlite3.Connection) -> list[dict[str, object]]:
    # 用独立 cursor 取具名列，不改调用方连接的 row_factory。
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    rows: list[dict[str, object]] = []
    creators = cursor.execute(
        "SELECT id,canonical_name FROM entity WHERE kind='creator' ORDER BY canonical_name"
    ).fetchall()
    for creator in creators:
        assets = cursor.execute(
            """SELECT a.id,a.name,a.path,a.code,a.medium FROM asset_entity ae
               JOIN asset a ON a.id=ae.asset_id
               WHERE ae.entity_id=? AND ae.role='creator' ORDER BY a.id""",
            (creator["id"],),
        ).fetchall()
        if not assets:
            continue
        verdict, identity, reason = classify(str(creator["canonical_name"]), assets)
        if verdict == VERDICT_KEEP:
            continue
        missing = sum(1 for row in assets if not str(row["code"] or "").strip())
        rows.append({
            "entity_id": int(creator["id"]),
            "creator": str(creator["canonical_name"]),
            "verdict": verdict,
            "identity": identity,
            "assets": len(assets),
            "sample_path": str(assets[0]["path"] or ""),
            "code_action": (f"补 code {identity}（{missing} 条为空）"
                            if verdict == VERDICT_CODE and missing else ""),
            "reason": reason,
        })
    return rows


#: 这条清理路径写真相字段时的归属串。
CODE_CREATORS_OWNER = "script:code-creators"

#: 目录名这条证据只给正片背书。`Tokyo-Hot n0780-HD` 里还躺着 `論壇文宣\封殺001.jpg`：
#: 番号写到它头上，库里它就冒充这部片的文件，垃圾复核那边又因为「自己带真番号」判定
#: 目录证据不成立，把它挡在队列外——一次补空同时制造了两处错。番号是作品标识，作品是
#: 目录里那条视频。
RELEASE_FILE = "medium='video' AND COALESCE(code,'')=''"


def _ids_where(connection: sqlite3.Connection, asset_ids: list[int],
               condition: str, parameters: tuple = ()) -> list[int]:
    """这批资产里满足 `condition` 的那几条。"""
    if not asset_ids:
        return []
    marks = ",".join("?" * len(asset_ids))
    return [int(row[0]) for row in connection.execute(
        f"SELECT id FROM asset WHERE id IN ({marks}) AND {condition}",
        (*asset_ids, *parameters))]


def _write_owned(connection: sqlite3.Connection, asset_ids: list[int], field: str,
                 value: str | None, *, require_empty: bool = False) -> int:
    """按字段归属写一个真相字段，返回真的落下去几条。

    数的是写完之后真的等于目标值的条数：`write_owned_fields` 会挡下用户改过的格子，
    而它回报的 rowcount 数的是匹配到的行，把被挡的那几条也算在里面。
    """
    if not asset_ids:
        return 0
    write_owned_fields(connection, asset_ids, {field: value}, CODE_CREATORS_OWNER,
                       require_empty=require_empty)
    marks = ",".join("?" * len(asset_ids))
    return connection.execute(
        f"SELECT count(*) FROM asset WHERE id IN ({marks}) AND {field} IS ?",
        (*asset_ids, value)).fetchone()[0]


def apply_rows(connection: sqlite3.Connection, rows: list[dict[str, object]]) -> dict[str, int]:
    """只清理有文件级证据的行；存疑一律留给人工。"""
    counts = {"links": 0, "entities": 0, "codes": 0, "flat": 0}
    for row in rows:
        if row["verdict"] not in SETTLED_VERDICTS:
            continue
        entity_id = int(row["entity_id"])
        identity = str(row["identity"])
        asset_ids = [
            int(item[0]) for item in connection.execute(
                "SELECT asset_id FROM asset_entity WHERE entity_id=? AND role='creator'",
                (entity_id,))
        ]
        # 番号是作品标识，写进 code；站点作品号不是番号，写进去只会污染刮削队列。
        if row["verdict"] == VERDICT_CODE:
            counts["codes"] += _write_owned(
                connection, _ids_where(connection, asset_ids, RELEASE_FILE),
                "code", identity, require_empty=True)
        connection.execute(
            "DELETE FROM asset_entity WHERE entity_id=? AND role='creator'", (entity_id,))
        counts["links"] += connection.execute("SELECT changes()").fetchone()[0]
        counts["flat"] += _write_owned(
            connection, _ids_where(connection, asset_ids, "creator=?", (row["creator"],)),
            "creator", None)
        # 只删掉再无任何关系的实体，避免连带清掉别处仍在引用的身份。
        connection.execute(
            "DELETE FROM entity WHERE id=? AND kind='creator' "
            "AND NOT EXISTS(SELECT 1 FROM asset_entity WHERE entity_id=?)",
            (entity_id, entity_id))
        counts["entities"] += connection.execute("SELECT changes()").fetchone()[0]
    return counts
