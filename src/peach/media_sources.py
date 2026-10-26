"""挂载来源能力与按数据库路径前缀配置的代理流量保护。"""
from dataclasses import dataclass
import json
from pathlib import PureWindowsPath
import re

from .settings_file import PROJECT_ROOT


@dataclass(frozen=True)
class MediaSource:
    id: str
    name: str
    label: str
    default_cost: str
    cloud: bool
    icon: str


SOURCE_REGISTRY = tuple(MediaSource(**row) for row in json.loads(
    (PROJECT_ROOT / "resources" / "media-sources.json").read_text(encoding="utf-8")))
SOURCES = {source.id: source for source in SOURCE_REGISTRY}
SOURCE_OPTIONS = tuple((source.id, source.label) for source in SOURCE_REGISTRY)
SOURCE_COSTS = {source.id: source.default_cost for source in SOURCE_REGISTRY}
SCANNABLE_LOCATIONS = tuple(SOURCES)
CLOUD_LOCATIONS = frozenset(source.id for source in SOURCE_REGISTRY if source.cloud)


def _config(config):
    if config is None:
        from .settings_file import active
        return active()
    return config


def _root_key(path: str) -> str:
    # 与 SQLite COLLATE NOCASE 一致；路径只比较，不访问挂载。
    return str(PureWindowsPath(path)).rstrip("\\").translate(
        str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))


def _root_costs(config):
    overrides = {_root_key(root): cost for root, cost in getattr(config, "media_costs", {}).items()}
    return sorted(((location, str(PureWindowsPath(root)),
                    overrides.get(_root_key(root), SOURCE_COSTS.get(location, "metered")))
                   for location, roots in getattr(config, "locations", {}).items() for root in roots),
                  key=lambda row: len(_root_key(row[1])), reverse=True)


def cost_for(location: str, path: str | None = "", config=None) -> str:
    """实际路径按最长数据库路径前缀取保护设置；空路径聚合同来源的已配置根。"""
    roots = [row for row in _root_costs(_config(config)) if row[0] == location]
    if not path and roots:
        return "metered" if any(cost == "metered" for _, _, cost in roots) else "free"
    key = _root_key(path) if path else ""
    for _, root, cost in roots:
        declared = _root_key(root)
        if key == declared or key.startswith(declared + "\\"):
            return cost
    return SOURCE_COSTS.get(location, "metered")


def cost_sql(location_column: str = "location", path_column: str = "path", *, config=None) -> tuple[str, tuple]:
    """返回求值为 free/metered 的参数化 CASE；NULL/空路径使用来源聚合值。"""
    for column in (location_column, path_column):
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?", column, flags=re.ASCII):
            raise ValueError("成本查询列名不正确")
    config = _config(config)
    clauses, parameters = [], []
    for location in getattr(config, "locations", {}):
        clauses.append(f"WHEN {location_column}=? AND COALESCE({path_column},'')='' THEN ?")
        parameters.extend((location, cost_for(location, config=config)))
    for location, root, cost in _root_costs(config):
        root = root.rstrip("\\")
        prefix = root + "\\"
        clauses.append(f"WHEN {location_column}=? AND ({path_column}=? COLLATE NOCASE "
                       f"OR substr({path_column},1,?)=? COLLATE NOCASE) THEN ?")
        parameters.extend((location, root, len(prefix), prefix, cost))
    for location, cost in SOURCE_COSTS.items():
        clauses.append(f"WHEN {location_column}=? THEN ?")
        parameters.extend((location, cost))
    return "CASE " + " ".join(clauses) + " ELSE 'metered' END", tuple(parameters)
