"""群体研究的本机去重、用户信号与分组计数。"""
from collections import Counter, defaultdict
import math

from .catalog_rules import is_jav_code, normalise_code_key
from .entities import male_performer_ids, normalize_entity_name
from .error_reporting import redact
from .library_favorites import favorite_entities
from .taste_evidence import era, length_band

KINDS = ("performer", "creator", "studio", "series", "tag", "era", "length")
COUNTS = {"collected", "liked", "disliked", "watched"}
WORK_FIELDS = {"code", "rating", "liked", "feedback", "watch_later", "play_count", "watch_ratio", "manual_tags"}


def number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value > 0 else 0


def _text(value):
    return redact(str(value)).strip()


def project(connection):
    """只读当前馆藏；编号是跨部署统计作品的公共键。"""
    excluded = male_performer_ids(connection)
    works, coded_files, total_files = _collection(connection, _features(connection, excluded))
    library, groups = _summarize(works)
    for favorite in favorite_entities(connection):
        kind, name = favorite["kind"], _text(favorite["name"])
        if kind in groups and name and favorite["entity_id"] not in excluded:
            groups[kind].setdefault(name, {"name": name})["favorite"] = True
    return {"library": sorted(library, key=lambda row: row["code"]),
            "preferences": {kind: sorted(rows.values(), key=lambda row: (-row.get("works", {}).get("collected", 0), row["name"]))
                            for kind, rows in groups.items()},
            "coverage": {"active_files": total_files, "coded_files": coded_files,
                         "coded_works": len(library), "uncoded_items": total_files - coded_files}}


def _features(connection, excluded):
    """按资产读取规范实体与可见的手动标签。"""
    links = defaultdict(lambda: defaultdict(set))
    names = {}
    for aid, eid, kind, name in connection.execute(
            "SELECT ae.asset_id,e.id,e.kind,e.canonical_name FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id"):
        if kind in {"performer", "creator", "studio", "series"} and eid not in excluded:
            if eid not in names:
                names[eid] = _text(name)
            if names[eid]:
                links[aid][kind].add(names[eid])
    hidden = {(aid, tag) for aid, tag in connection.execute(
        "SELECT asset_id,normalized_tag FROM asset_tag_preference WHERE profile_id='local-default' AND hidden=1")}
    for aid, tag in connection.execute("SELECT asset_id,tag FROM asset_tag WHERE source='web-user'"):
        if (aid, normalize_entity_name(tag)) not in hidden and _text(tag):
            links[aid]["tag"].add(_text(tag))
    return links


def _collection(connection, links):
    """将资产按公共编号合并，保留无编号条目的独立本机分组。"""
    likes = {row[0] for row in connection.execute(
        "SELECT asset_id FROM asset_preference WHERE profile_id='local-default' AND liked=1")}
    later = {row[0] for row in connection.execute("SELECT asset_id FROM watch_queue WHERE profile_id='local-default'")}
    works = {}
    coded_files = total_files = 0
    for row in connection.execute(
            "SELECT id,code,rating,play_count,watch_ratio,max_reached,play_seconds,duration,feedback,feedback_at,"
            "release_date,creator,studio FROM asset WHERE disposal IS NULL ORDER BY id"):
        row = dict(row)
        aid = row["id"]
        code = normalise_code_key(row["code"]) if is_jav_code(row["code"]) else None
        total_files += 1
        coded_files += bool(code)
        key = ("code", code) if code else ("uncoded", aid)
        work = works.setdefault(key, {"code": code, "ratings": set(), "liked": False, "watch_later": False,
            "play_count": 0, "watch_ratio": 0, "feedback": None, "feedback_order": (-1, -1),
            "features": defaultdict(set), "duration": 0})
        _merge_asset(work, row, aid in likes, aid in later, links[aid])
    return works, coded_files, total_files


def _merge_asset(work, row, liked, later, features):
    """合并同一作品的反馈、播放记录与分组特征。"""
    rating = number(row["rating"])
    if 0 < rating <= 100:
        work["ratings"].add(rating)
    work["liked"] |= liked
    work["watch_later"] |= later
    work["play_count"] += int(number(row["play_count"]))
    duration = number(row["duration"])
    work["duration"] = max(work["duration"], duration)
    work["watch_ratio"] = max(work["watch_ratio"], min(1, max(number(row["watch_ratio"]),
        number(row["max_reached"]), number(row["play_seconds"]) / duration if duration else 0)))
    order = (number(row["feedback_at"]), row["id"])
    if row["feedback"] in {"seen", "dislike"} and order > work["feedback_order"]:
        work["feedback"], work["feedback_order"] = row["feedback"], order
    for kind, names in features.items():
        work["features"][kind].update(names)
    for kind in ("creator", "studio"):
        if not features[kind] and row[kind] and _text(row[kind]):
            work["features"][kind].add(_text(row[kind]))
    period = era(row["release_date"])
    if period and 1900 <= int(period) <= 2100:
        work["features"]["era"].add(period)


def _signals(work):
    """输出存在的用户信号，每部作品贡献一份评分。"""
    signals = {}
    if work["ratings"]:
        signals["rating"] = round(sum(work["ratings"]) / len(work["ratings"]), 2)
    for key in ("liked", "watch_later", "play_count", "feedback"):
        if work[key]:
            signals[key] = work[key]
    ratio = round(work["watch_ratio"], 4)
    if ratio:
        signals["watch_ratio"] = ratio
    if work["features"]["tag"]:
        signals["manual_tags"] = sorted(work["features"]["tag"])
    return signals


def _summarize(works):
    """生成作品列表及有编号、无编号范围各自的分组计数。"""
    groups = {kind: {} for kind in KINDS}
    library = []
    for work in works.values():
        band = length_band(work["duration"])
        if band is not None:
            work["features"]["length"].add(band)
        signals = _signals(work)
        if work["code"]:
            library.append({"code": work["code"], **signals})
        for kind, names in work["features"].items():
            for name in names:
                group = groups[kind].setdefault(name, {"name": name})
                counts = group.setdefault("works" if work["code"] else "uncoded", _counts())
                counts["collected"] += 1
                counts["liked"] += bool(signals.get("liked"))
                counts["disliked"] += signals.get("feedback") == "dislike"
                counts["watched"] += bool(work["play_count"] or work["watch_ratio"] or work["feedback"] == "seen")
                if "rating" in signals:
                    counts["ratings"][str(signals["rating"])] += 1
    return library, groups


def _counts():
    return {"collected": 0, "liked": 0, "disliked": 0, "watched": 0, "ratings": Counter()}


def _count(value):
    return type(value) is int and value >= 0


def _finite(value, maximum):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= maximum


def _public(value):
    return isinstance(value, str) and bool(value.strip()) and redact(value) == value


def valid_projection(payload):
    """传输端使用同一白名单检查每份投影的值和内部计数关系。"""
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != {"active_files", "coded_files", "coded_works", "uncoded_items"}:
        return False
    if (not all(_count(value) for value in coverage.values())
            or coverage["active_files"] != coverage["coded_files"] + coverage["uncoded_items"]
            or not coverage["active_files"] >= coverage["coded_files"] >= coverage["coded_works"] == len(payload["library"])):
        return False
    codes = set()
    for work in payload["library"]:
        if (not isinstance(work, dict) or "code" not in work or not set(work) <= WORK_FIELDS
                or not isinstance(work["code"], str) or not is_jav_code(work["code"])
                or normalise_code_key(work["code"]) != work["code"] or work["code"] in codes):
            return False
        codes.add(work["code"])
        if not _valid_signals(work):
            return False
    preferences = payload["preferences"]
    if set(preferences) != set(KINDS):
        return False
    for kind, rows in preferences.items():
        if not isinstance(rows, list):
            return False
        seen = set()
        for row in rows:
            if not _valid_preference(row, kind, coverage) or row["name"] in seen:
                return False
            seen.add(row["name"])
    return True


def _valid_signals(work):
    """逐字段检查作品的稀疏用户信号。"""
    for key, value in work.items():
        if key in {"liked", "watch_later"} and value is not True:
            return False
        if key == "play_count" and (not _count(value) or value == 0):
            return False
        if key in {"rating", "watch_ratio"} and not _finite(value, 100 if key == "rating" else 1):
            return False
        if key == "feedback" and value not in {"seen", "dislike"}:
            return False
        if key == "manual_tags" and (not isinstance(value, list) or not all(_public(tag) for tag in value)):
            return False
    return True


def _valid_preference(row, kind, coverage):
    """检查分组名称、主动收藏标记及两种计数范围。"""
    if (not isinstance(row, dict) or not set(row) <= {"name", "works", "uncoded", "favorite"}
            or "name" not in row or not _public(row["name"]) or len(row) < 2):
        return False
    if kind == "length" and row["name"] not in {"0", "1", "2", "3"}:
        return False
    if kind == "era" and (not row["name"].isdigit() or not 1900 <= int(row["name"]) <= 2100 or int(row["name"]) % 5):
        return False
    if "favorite" in row and (row["favorite"] is not True or kind in {"era", "length"}):
        return False
    for scope, maximum in (("works", coverage["coded_works"]), ("uncoded", coverage["uncoded_items"])):
        if scope in row and not _valid_counts(row[scope], maximum):
            return False
    return True


def _valid_counts(row, maximum):
    if (not isinstance(row, dict) or set(row) != COUNTS | {"ratings"}
            or not all(_count(row[key]) for key in COUNTS)
            or not all(row[key] <= row["collected"] <= maximum for key in COUNTS)
            or not isinstance(row["ratings"], dict)):
        return False
    for score, count in row["ratings"].items():
        try:
            valid = isinstance(score, str) and _finite(float(score), 100) and _count(count) and count > 0
        except (ValueError, OverflowError):
            return False
        if not valid:
            return False
    return sum(row["ratings"].values()) <= row["collected"]
