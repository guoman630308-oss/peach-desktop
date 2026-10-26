"""媒体库按声明路径分组，来源与挂载仍由媒体配置管理。"""
from pathlib import PureWindowsPath

LIBRARY_ICONS = frozenset({"hard-drive", "database", "heart", "star", "tags", "115", "pikpak",
                         "heart-hand", "flame", "cherry", "gem", "crown", "shirt", "footprints",
                         "flower", "venetian-mask", "hand", "bed-double", "moon", "sparkles", "camera", "video",
                         "lollipop", "candy", "banana", "droplets", "venus", "mars", "venus-and-mars",
                         "ribbon", "graduation-cap", "stethoscope", "glasses", "rabbit", "paw-print", "dumbbell",
                         "bath", "key-round", "wine", "cigarette", "film", "image", "gamepad-2"})


def libraries(config):
    groups = {}
    for location, roots in config.locations.items():
        for root in roots:
            name = config.library_names.get(root) or PureWindowsPath(root).name or root
            groups.setdefault(name, []).append({"location": location, "root": root})
    icons = getattr(config, "library_icons", {})
    return [{"id": name, "name": name, "roots": roots,
             "icon": next((icons[item["root"]] for item in roots
                           if icons.get(item["root"]) in LIBRARY_ICONS),
                          roots[0]["location"] if len({item["location"] for item in roots}) == 1 else "database")}
            for name, roots in groups.items()]


def predicate(config, library):
    """路径边界精确匹配；未知库返回空集。"""
    roots = next((row["roots"] for row in libraries(config) if row["id"] == library), [])
    terms, params = [], []
    for item in roots:
        prefix = str(PureWindowsPath(item["root"])).rstrip("\\") + "\\"
        terms.append("(a.location=? AND substr(a.path,1,?)=? COLLATE NOCASE)")
        params.extend((item["location"], len(prefix), prefix))
    return "(" + " OR ".join(terms) + ")" if terms else "0", params
