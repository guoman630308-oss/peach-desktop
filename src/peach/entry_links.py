"""人物资料页的外部入口：几个站点的直达地址，以及其中两站可换的镜像域名。

入口只由后端拼：地址要用的东西都在服务端——`entity_external_ref` 里的站点 id、
`entity.canonical_name` 和 `entity_alias`。前端拿到的是一串带位置与标记的
`{site, label, slot, mark, url}`，没有模板也没有拼装规则，换一个站不必同时改两侧。

**没有 id 的站点不出现。** 退回搜索地址等于把「这个人在那边是谁」这件事交给站内检索
去猜，而同名的人正是最需要点进去核对的那一批。

**JavDB 与 MISSAV 只给 JAV 女优。** 账本里 `kind='performer'` 装着两种人：商业 AV 女优，
和 `145cm色白お嬢様` 这样的 FC2 个人摄创作者。后者不在这两个站的收录范围里，而 MISSAV
只按名字拼地址，不设门就会给每个创作者挂一条必然落空的链接。门槛是 `_JAV_DIRECTORIES`：
账本里有没有哪个 JAV 目录站给过她 id——那是别人已经收录过她的现成证据，比猜名字是不是
日文、比看标签都硬。`stash` 不算（那是本机 Stash 的 id，库里每个人都有），
`r18:performer_name` 也不算（那是名字映射，不是目录页上的 id）。

**MISSAV 采用名录中的实际 href。** 名录按当前艺名与规范名匹配，路径保留站点原文。
未取得对应名录条目时，候选入口使用 `/dm3/cn/actresses/` 与来源艺名；当前规范名
含假名且不是纯读音时优先使用，否则从别名里挑日文艺名。中文译名与旧艺名不能代替当前日文艺名：

页面语言不决定路径字形；站点 canonical 中的艺名与数字前缀由站点处理。

- 日文艺名可含假名或纯汉字。汉字现名采用同名字形或已有成对译名的来源证据；
  对应的中文名必须仍是当前规范名，不同姓氏与可信假名艺名的冲突保留给复核。
- 别名里的纯平假名读音不作为入口。`r18:performer` 每位都带一条（`しゃくありす`），
  选择艺名时需要区分它与人物的正式写法（`釈アリス`）。可信艺名来源标明的纯平假名正式艺名照用。
- 同样形态的有多条时，优先 `avdb-actor-mapping`、`javdb`、`wiki` 这几类艺名来源。
- 一条都挑不出时才用规范名：拼错的地址和没有地址相比，前者更难发现。

**同一个站点可以有多枚入口。** 一位女优在 javdb 常有两个演员页，两边挂的作品不同，
所以每个 id 各出一枚，第二枚起在标签后缀一个序号。

**两种位置。** minnano-av 是一份资料页，和事务所官网、社媒是同一类东西，所以它排进上面
那排链接里。JavDB 与 MISSAV 是「去看片」的入口，另起一行，用两站自己的标识。

**配置页只有两个域名框。** JavDB 与 MISSAV 各有一排可换的镜像域名，主域名连不上时得跟着
换，所以这两站各留一个框，留空就是上面那个默认域名；路径与占位符由这里定死。整条模板不
交给使用者填：占位符写错换来的是一排看着正常、点开全是 404 的入口，而域名对不对点一次
就知道。みんなのAV 只此一家，没有可换的东西，它不出现在配置页上。

**没有开关。** 入口本来就按账本里有没有 id 决定出不出现，三枚按钮自己就在资料页上；
再给每站一个开关，是让人到设置里关掉一枚他在页面上根本没见到过的入口。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from filelock import FileLock

from .fsutil import atomic_write_text
from .kanji import JP_KANJI_TO_SIMPLIFIED, fold_glyphs

#: 入口设置落在 Peach 数据目录的 state 目录下。它是本机显示偏好，不是账本真相。
FILENAME = "entry-links.json"
DIRECTORY_FILENAME = "missav-actresses.json"
DIRECTORY_URL = "https://missav.ws/cn/actresses"
#: `entity_external_ref.external_kind`：人物那一档。
EXTERNAL_KIND = "performer"

#: 入口在页面上的两个位置。`pill` 混进上面那排外链，`mark` 自己一行、用站点标识。
SLOT_PILL = "pill"
SLOT_MARK = "mark"

#: JAV 目录站。实体在这些站里有 `performer` id，才算「这是一位 JAV 女优」。
#: 判据见模块开头：这是别处已经收录过她的现成证据。
#: 事务所自己的名册不算——`k-mib.com` 那类站收的是所属艺人，和她拍不拍 JAV 是两件事，
#: 只凭它出现在名册上推不出 JavDB 与 MISSAV 上有她的页面。
JAV_DIRECTORIES = frozenset({"javdb", "minnano-av", "r18dev", "dmm", "avbase"})


@dataclass(frozen=True)
class EntrySite:
    """一个入口站点。

    `provider` 空串表示这一站按名字拼，不需要账本里的 id。`label` 是页面上那枚入口的
    可读名字，`title` 是配置页和报错里的站点名。`mark` 是雪碧图里那枚标记的名字；
    MISSAV 没有可用的图形标识，它的 `mark` 是空串，由前端按站点自己的排版规则排字。
    `host` 是默认域名，`mirrored` 说这一站能不能在配置页换域名。
    """

    key: str
    label: str
    title: str
    placeholder: str
    provider: str
    host: str
    path: str
    slot: str
    mark: str
    jav_only: bool = False
    mirrored: bool = False


SITES: tuple[EntrySite, ...] = (
    # 页面上写站点自己的名字「みんなのAV」，取自它官网的 title；`minnano-av` 是域名和
    # 账本里的 provider，留给设置项的 key 与 `entity_external_ref` 用。
    EntrySite("minnano-av", "みんなのAV", "みんなのAV", "minnano_id", "minnano-av",
              "www.minnano-av.com", "/actress{minnano_id}.html",
              SLOT_PILL, "brand-minnano"),
    EntrySite("javdb", "JavDB", "JavDB", "javdb_id", "javdb",
              "javdb.com", "/actors/{javdb_id}",
              SLOT_MARK, "mark-javdb", jav_only=True, mirrored=True),
    EntrySite("missav", "MISSAV", "MISSAV", "name", "",
              "missav.ws", "/dm3/cn/actresses/{name}",
              SLOT_MARK, "", jav_only=True, mirrored=True),
)
_BY_KEY = {site.key: site for site in SITES}
#: 域名长度上限与形状。只认域名本身：字母数字、连字符和点，至少一个点。
_HOST_LIMIT = 100
_HOST_SHAPE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?"
                         r"(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+")
#: 第二枚起的序号。用完退回括号数字，标签宁可长一点也不能两枚长得一模一样。
_ORDINALS = "②③④⑤⑥⑦⑧⑨"
#: 艺名类的别名来源。按子串认：来源串后面还挂着采集批次。
_STAGE_NAME_SOURCES = ("avdb-actor-mapping", "javdb", "wiki", "performer-localization")
_NAME_GLYPHS = {**JP_KANJI_TO_SIMPLIFIED, "條": "条"}


def defaults() -> dict:
    """一个域名都没换过：能换的那两站留空，读的时候各自退回自己的默认域名。"""
    return {site.key: {"host": ""} for site in SITES if site.mirrored}


def _clean(saved: object) -> dict:
    """把读到的内容收敛成已知站点。坏值退回默认，不让一个手改坏的文件废掉整行入口。"""
    result = defaults()
    if not isinstance(saved, dict):
        return result
    rows = saved.get("sites")
    for key, value in (rows.items() if isinstance(rows, dict) else ()):
        site = _BY_KEY.get(str(key))
        if site is None or not site.mirrored or not isinstance(value, dict):
            continue
        host = _tidy_host(value.get("host"))
        if _host_problem(site, host) == "":
            result[site.key]["host"] = host
    return result


def read(root: Path) -> dict:
    """当前设置。文件不在、读不出或内容坏掉都退回默认。"""
    try:
        return _clean(json.loads((Path(root) / FILENAME).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return defaults()


def snapshot(root: Path) -> dict:
    """配置页要的形状：只有能换域名的那几站，各带当前值和留空时用的默认域名。"""
    saved = read(root)
    return {"sites": [{
        "key": site.key,
        "label": site.title,
        "host": saved[site.key]["host"],
        "default_host": site.host,
    } for site in SITES if site.mirrored]}


def _tidy_host(written: object) -> str:
    """把填进来的东西收成域名。

    镜像地址多半是从别处整条复制过来的，带着 `https://` 和结尾的斜杠。这两样去掉就是
    合法的域名，为它们弹一条错误只是让人手工删一遍。再往后的路径不收：那是在改地址
    形状，不是换域名。
    """
    host = str(written or "").strip()
    for scheme in ("https://", "http://"):
        if host.lower().startswith(scheme):
            host = host[len(scheme):]
    return host.rstrip("/")


def _host_problem(site: EntrySite, host: str) -> str:
    """域名不能用时的那一句原因；能用时是空串。

    空串本身可用——它就是「没换过，走默认域名」。要回到默认值，清空这个框就行；逼人去
    手抄一遍默认域名的话，抄错了还得他自己认。
    """
    if not host:
        return ""
    if len(host) > _HOST_LIMIT:
        return f"{site.title} 的地址请控制在 {_HOST_LIMIT} 个字符以内"
    if not _HOST_SHAPE.fullmatch(host):
        return f"{site.title} 这里只写域名本身，像 {site.host}"
    return ""


def save(root: Path, body: dict) -> dict:
    """写回可换的那几站的域名。任一项不合规就整批不写。"""
    rows = body.get("sites")
    if not isinstance(rows, dict):
        raise ValueError("请提交每个站点的地址")
    result = defaults()
    for site in SITES:
        if not site.mirrored:
            continue
        row = rows.get(site.key)
        if not isinstance(row, dict):
            raise ValueError(f"缺少 {site.title} 的设置")
        if not isinstance(row.get("host"), str):
            raise ValueError(f"{site.title} 的地址必须是文字")
        host = _tidy_host(row["host"])
        problem = _host_problem(site, host)
        if problem:
            raise ValueError(problem)
        result[site.key]["host"] = host
    path = Path(root) / FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=5):
        atomic_write_text(path, json.dumps({"sites": result}, ensure_ascii=False))
    return snapshot(root)


def provider_ids(refs) -> dict[str, list[str]]:
    """`entity_external_ref` 的行 → 这个站点上的 id 们。只认人物那一档。"""
    found: dict[str, list[str]] = {}
    for ref in refs or ():
        if str(ref.get("external_kind") or "") != EXTERNAL_KIND:
            continue
        external_id = str(ref.get("external_id") or "").strip()
        provider = str(ref.get("provider") or "")
        ids = found.setdefault(provider, [])
        if external_id and external_id not in ids:
            ids.append(external_id)
    return found


def _has_kana(written: str) -> bool:
    return any(0x3040 <= ord(char) <= 0x30FF for char in written)


def _only_hiragana(written: str) -> bool:
    """写法里除了空白和标点就只有平假名。那是读音，不是站上挂着页面的艺名。"""
    return all(char.isspace() or not char.isalnum() or 0x3041 <= ord(char) <= 0x309F
               for char in written)


def _glyph_key(written: str) -> str:
    return fold_glyphs(written).translate(str.maketrans(_NAME_GLYPHS))


def _han_name(written: str) -> bool:
    return bool(re.fullmatch(r"[\u3400-\u9fff々\s]+", written))


def _glyph_equivalents(name: str, aliases) -> list[str]:
    """与当前艺名仅有汉字字形或叠字符号差异的写法。"""
    matches = []
    for row in aliases or ():
        written = str((row.get("alias") if isinstance(row, dict) else row) or "").strip()
        if written and _glyph_key(written) == _glyph_key(name):
            matches.append(written)
    return matches


def _published_kana_alias(aliases) -> bool:
    """别名中是否有区别于纯平假名读音的艺名。"""
    for row in aliases or ():
        written = str((row.get("alias") if isinstance(row, dict) else row) or "")
        if _has_kana(written) and not _only_hiragana(written):
            return True
    return False


def _paired_hiragana_name(jp: str, aliases) -> bool:
    """成对来源中的纯平假名艺名必须同时出现在可信艺名别名中。"""
    if not _only_hiragana(jp) or not _has_kana(jp):
        return False
    return any(isinstance(row, dict) and row.get("alias") == jp
               and any(mark in str(row.get("source") or "") for mark in _STAGE_NAME_SOURCES)
               for row in aliases or ())


def _localized_han_name(name: str, aliases, localization) -> str:
    """来源成对记录的当前汉字艺名；不同姓氏或可信假名艺名冲突时不采用。"""
    if not isinstance(localization, dict) or not _han_name(name):
        return ""
    jp = str(localization.get("jp") or "").strip()
    chinese = str(localization.get("zh_cn") or "").strip()
    source = str(localization.get("source") or "")
    trusted = any(mark in source for mark in (*_STAGE_NAME_SOURCES, "kanji-simplification"))
    if not trusted or _glyph_key(chinese) != _glyph_key(name):
        return ""
    if _paired_hiragana_name(jp, aliases):
        return jp
    if not _han_name(jp):
        return ""
    if _glyph_key(jp) != _glyph_key(name) and _glyph_key(jp)[:2] != _glyph_key(name)[:2]:
        return ""
    for row in aliases or ():
        if not isinstance(row, dict):
            continue
        written = str(row.get("alias") or "")
        alias_source = str(row.get("source") or "")
        if _has_kana(written) and not _only_hiragana(written) \
                and any(mark in alias_source for mark in _STAGE_NAME_SOURCES):
            return ""
    return jp


def japanese_name(canonical_name: str, aliases, localization=None) -> str:
    """这位的日文艺名。挑不出来时退回规范名，判据见模块开头。"""
    name = str(canonical_name or "").strip()
    localized = _localized_han_name(name, aliases, localization)
    current = localized or name
    equivalents = _glyph_equivalents(current, aliases)
    if localized or (equivalents and not _published_kana_alias(aliases)) \
            or (_has_kana(name) and not _only_hiragana(name)):
        return max([current, *equivalents],
                   key=lambda value: sum(char in _NAME_GLYPHS or char == "々" for char in value))
    chosen: tuple[tuple[int, int, int], str] | None = None
    for index, row in enumerate(aliases or ()):
        written = str((row.get("alias") if isinstance(row, dict) else row) or "").strip()
        source = str((row.get("source") if isinstance(row, dict) else "") or "")
        trusted = any(mark in source for mark in _STAGE_NAME_SOURCES)
        if not written or not _has_kana(written) or (_only_hiragana(written) and not trusted):
            continue
        rank = (1 if _only_hiragana(written) else 0,
                0 if trusted else 1,
                index)
        if chosen is None or rank < chosen[0]:
            chosen = (rank, written)
    return chosen[1] if chosen else name


def is_jav_performer(refs) -> bool:
    """这条实体在 JAV 目录站里有身份吗。判据与理由见模块开头。"""
    return any(provider in JAV_DIRECTORIES for provider in provider_ids(refs))


@dataclass(frozen=True)
class MissavDirectory:
    """目录实际 href 的只读索引；名称折叠只用于匹配，地址保持站点原文。"""

    paths: dict[str, tuple[str, ...]]
    complete: bool = False

    def path(self, japanese: str, canonical: str) -> str | None:
        for name in (japanese, canonical):
            key = directory_name_key(name)
            if key in self.paths:
                matches = self.paths[key]
                return matches[0] if len(matches) == 1 else ""
        return "" if self.complete else None


def directory_name_key(name: str) -> str:
    """名录匹配折叠字形及纯汉字、假名名称中的空白，路径保留来源原文。"""
    key = _glyph_key(name).casefold()
    if re.fullmatch(r"[\u3400-\u9fff\u3040-\u30ff\s]+", key):
        return "".join(key.split())
    return key


def _published_names(name: str) -> list[str]:
    names = [name.strip()]
    combined = re.fullmatch(r"([^()（）]+)[(（]([^()（）]+)[)）]", name)
    if combined:
        names.extend(part.strip() for part in combined.groups())
    return names


def directory_index(data: object) -> MissavDirectory | None:
    """目录快照的公开名字与实际 href；非法域名、空名与非人物路径不入索引。"""
    if not isinstance(data, dict) or data.get("source") != DIRECTORY_URL:
        return None
    entries = data.get("entries")
    if not isinstance(entries, list):
        return None
    paths: dict[str, dict[str, str]] = {}
    blocked: set[str] = set()
    for row in entries:
        if not isinstance(row, dict):
            continue
        try:
            target = urlsplit(str(row.get("url") or ""))
        except ValueError:
            continue
        if target.scheme != "https" or target.netloc != "missav.ws" \
                or not re.fullmatch(r"/(?:dm\d+/)?cn/actresses/[^/?#]+", target.path):
            continue
        slug = unquote(target.path.rsplit("/", 1)[-1])
        names = _published_names(slug) + _published_names(str(row.get("name") or ""))
        for name in names:
            if name:
                key = directory_name_key(name)
                bucket = paths.setdefault(key, {})
                if row.get("verified") is False:
                    blocked.add(key)
                else:
                    bucket.setdefault(slug, target.path)
    return MissavDirectory({key: () if key in blocked else tuple(sorted(values.values()))
                            for key, values in paths.items()},
                           complete=data.get("complete") is True)


@lru_cache(maxsize=4)
def _directory_file(path: str, modified: int, size: int) -> MissavDirectory | None:
    try:
        return directory_index(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def read_directory(root: Path) -> MissavDirectory | None:
    """按文件版本读取名录快照；不发网络请求，不写账本。"""
    path = Path(root) / DIRECTORY_FILENAME
    try:
        stat = path.stat()
    except OSError:
        return None
    return _directory_file(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def missav_name(japanese: str) -> str:
    """未取得名录条目时使用的简中候选艺名。"""
    return japanese.translate(str.maketrans(_NAME_GLYPHS))


def _ordinal(index: int) -> str:
    """同一个站点第二枚起的序号，第一枚没有。

    序号单独给一项而不是只拼进 `label`：标识那一行印的是站点自己的图形，没有位置放
    整句话，序号得能单独摆在标识旁边。用完那几个字符退回括号数字，宁可长一点也不能
    两枚长得一模一样。
    """
    if index == 0:
        return ""
    if index <= len(_ORDINALS):
        return _ORDINALS[index - 1]
    return f"({index + 1})"


def build(settings: dict, canonical_name: str, refs, aliases=(), localization=None,
          directory: MissavDirectory | None = None) -> list[dict]:
    """这条人物实体能直达的地址，按 SITES 的先后排好。缺 id 的站点不出现在结果里。"""
    ids = provider_ids(refs)
    jav = is_jav_performer(refs)
    written = japanese_name(canonical_name, aliases, localization)
    missav_path = directory.path(written, canonical_name) if directory else None
    out: list[dict] = []
    for site in SITES:
        row = settings.get(site.key) or {}
        if site.jav_only and not jav:
            continue
        if site.key == "missav" and missav_path == "":
            continue
        values = ids.get(site.provider, []) if site.provider else ([missav_name(written)] if written else [])
        host = _tidy_host(row.get("host")) if site.mirrored else site.host
        if not host or _host_problem(site, host):
            host = site.host
        for index, value in enumerate(values):
            ordinal = _ordinal(index)
            path = site.path.replace("{" + site.placeholder + "}", quote(value, safe=""))
            if site.key == "missav" and missav_path:
                path = missav_path
            out.append({"site": site.key,
                        "label": f"{site.label} {ordinal}".strip(),
                        "ordinal": ordinal, "slot": site.slot, "mark": site.mark,
                        "url": f"https://{host}{path}"})
    return out


def entry_links(root: Path, canonical_name: str, refs, aliases=(), localization=None) -> list[dict]:
    """读设置并拼出入口。资料页只调这一个。"""
    return build(read(root), canonical_name, refs, aliases, localization, read_directory(root))


def javdb_actor_pages(canonical_name: str, refs) -> list[str]:
    """这位在 JavDB 上的演员页，人物页「订阅新作」拉的就是这几页（ADR-0042）。

    和页面上那枚入口同一套判据，只是域名不跟配置页的镜像走：拉取按主机计间隔与冷却，
    预算登记在 `javdb.com` 名下，换了镜像就绕开了那份预算；镜像日后换掉，存下来的订阅
    地址也不必跟着改。
    """
    return [row["url"] for row in build(defaults(), canonical_name, refs)
            if row["site"] == "javdb"]
