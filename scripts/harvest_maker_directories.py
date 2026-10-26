"""从发行商与发行平台自己的厂牌名录取官方字标，产出可复核的 `LOGO_SOURCES` 候选。

`LOGO_SOURCES` 里的 JAE 那批是人工从展会名录扒出来的，26 家、逐张看过。名录这几份动辄
几十上百家——手抄不现实，但也不能因为量大就跳过确认，所以这里只出候选和证据，写进
`LOGO_SOURCES` 仍是人看过之后的事。

**名录给的是日文名，账本常记英文品牌名**（`ムーディーズ` 对 `MOODYZ`），两边唯一稳定的
桥是文件名里的罗马字 slug（如 `aroma.gif`）。所以匹配分四路，每一路都把判据写进复核件的
`how` 列：对不上的那几百家不是失败，是账本里还没有的厂牌，它们的字标现在有出处了。
**复核件默认把没对上的也写出来**，这是这个脚本最主要的产物：别人的账本和这台机器上的
不是同一批厂牌，名录整份留下来，换个库一样能对。

存的是名录入口而不是几百条图片地址。KMP 那份的文件名带时间戳
（`file/label_1727404339.png`），厂牌换一次标识地址就变；入口重跑一次总是当下的那份。

这个脚本不判图。尺寸、内容比、方标还是字标那一套判据在 `harvest_studio_icons.py` 里已经
有一份，第二份抄本只会两边打架。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Callable, NamedTuple
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach.config import STATE_DIR   # noqa: E402
from peach.jobs import job_main   # noqa: E402
from peach.page_cache import Site   # noqa: E402
from peach.review_csv import write_rows   # noqa: E402
from peach.scripting import open_readonly   # noqa: E402

BASE = "https://www.mgstage.com/ppv/makers.php"
#: 50 音导航的每一页。`osusume` 是站方自己的推荐位，和音节页有重合，靠 slug 去重。
PAGES = ("osusume", "a", "ka", "sa", "ta", "na", "ha", "ma", "ya", "ra", "wa")
STATIC = "https://static.mgstage.com/mgs/img/pc/"
ENTRY = re.compile(
    r'<img[^>]+src="' + re.escape(STATIC) + r'([^"]+?)\.gif"[^>]*alt="([^"]*)"', re.I)
#: 50 音导航条自己也是 gif，和厂牌字标混在同一批 `<img>` 里，只能按文件名排掉。
NAV = re.compile(r"^(?:maker_\d+|maker_osusume|MGS|50kensaku|sankaku\w*)$", re.I)
#: 「【独占】」是 MGStage 的销售身份，不是厂牌名的一部分。
EXCLUSIVE = re.compile(r"^【[^】]*】")
#: 年龄门。没有它整站只回一张确认页，那不是内容页。
COOKIES = {"adc": "1"}
FIELDS = ("directory", "slug", "maker_ja", "entity_id", "studio", "assets",
          "how", "logo_url", "state")

PRESTIGE_API = "https://www.prestige-av.com/api/maker"
PRESTIGE_MEDIA = "https://www.prestige-av.com/api/media/"
KMP_LABELS = "https://www.km-produce.com/label"
#: KMP 的 label 图和站头、页脚的自家标识混在同一批 `<img>` 里。厂牌图一律住在
#: `/img2018/label/<slug>/` 或 `/file/label_<时间戳>.<后缀>`，站头那两张走
#: `/wp-content/themes/`，按路径分得开。
KMP_LABEL_IMAGE = re.compile(r"/img2018/label/|/file/label_\d+\.", re.I)
KMP_SLUG = re.compile(r"/img2018/label/([^/]+)/", re.I)
IMG_TAG = re.compile(r"<img[^>]+>", re.I)
IMG_SRC = re.compile(r"""src=["']([^"']+)["']""", re.I)
IMG_ALT = re.compile(r"""alt=["']([^"']*)["']""", re.I)


def fold_roman(name: str) -> str:
    """归一到只剩 ASCII 字母数字，用来和罗马字 slug 比。

    纯日文名折出来是空串，那是「这个名字没有可比的罗马字形」，不是「和谁都相等」。
    调用处必须把空串当不可比：不排掉的话 129 家里每个日文名都撞进同一个空键，
    351 家会全部对成同一家，复核件看着满满当当、一条都不能用。
    """
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", name).casefold())


#: 日文名之间比对用的归一：NFKC 之后去掉空白与常见标点，日文字符原样留着。
PUNCT = re.compile(r"[\s・.,'\"()（）\[\]/&+*!?！？：:－-]")


def fold_text(name: str) -> str:
    return PUNCT.sub("", unicodedata.normalize("NFKC", name).casefold())


def makers(site: Site) -> dict[str, tuple[str, str]]:
    """MGStage：十一页合起来的 slug →（日文名, 字标地址），按 slug 去重。"""
    found: dict[str, tuple[str, str]] = {}
    for page in PAGES:
        html = site.get(f"{BASE}?id={page}")
        for slug, alt in ENTRY.findall(html):
            if NAV.match(slug):
                continue
            name = EXCLUSIVE.sub("", alt).strip()
            if name:
                found.setdefault(slug, (name, f"{STATIC}{slug}.gif"))
    return found


def prestige_makers(site: Site) -> dict[str, tuple[str, str]]:
    """Prestige：`/api/maker` 一次给全，`codeName` 就是现成的罗马字 slug。

    这一份是 JSON 而不是 HTML：`/maker` 那个页面是客户端渲染的空壳，`/maker/<slug>`
    连标题都和列表页一样，从 HTML 里认不出谁是谁。
    """
    payload = json.loads(site.get(PRESTIGE_API))
    found: dict[str, tuple[str, str]] = {}
    for maker in payload.get("list") or []:
        slug = (maker.get("codeName") or "").strip()
        name = (maker.get("name") or "").strip()
        path = ((maker.get("media") or {}).get("path") or "").strip()
        if slug and name and path:
            found.setdefault(slug, (name, PRESTIGE_MEDIA + path))
    return found


def kmp_makers(site: Site) -> dict[str, tuple[str, str]]:
    """KMP：`/label` 一页列完旗下 label，名字在 `alt` 上。

    大半是 SVG。矢量在哪个尺寸都清楚，`images.bake_square_vector` 认得它们，所以
    不按后缀筛。没有 slug 的那些（`file/label_<时间戳>` 这一路）拿日文名当键——
    它们只能靠日文名对账，slug 那一路本来就走不到。
    """
    html = site.get(KMP_LABELS)
    found: dict[str, tuple[str, str]] = {}
    for tag in IMG_TAG.findall(html):
        src, alt = IMG_SRC.search(tag), IMG_ALT.search(tag)
        if not src or not alt:
            continue
        url = urljoin(KMP_LABELS, src.group(1))
        name = alt.group(1).strip()
        if not name or not KMP_LABEL_IMAGE.search(url):
            continue
        slug = KMP_SLUG.search(url)
        found.setdefault(slug.group(1) if slug else name, (name, url))
    return found


class MakerDirectory(NamedTuple):
    """一个名录的入口。`entries` 拿 `Site` 取页，返回 slug →（日文名, 字标地址）。

    用 `NamedTuple` 而不是 `dataclass`：测试按文件路径 `importlib` 装载这个脚本，
    装出来的模块不进 `sys.modules`，而 `dataclass` 要回查模块命名空间才能解开
    `from __future__ import annotations` 留下的字符串注解，查不到就 AttributeError。
    """

    key: str
    label: str
    entries: Callable[[Site], dict[str, tuple[str, str]]]
    cookies: dict[str, str]


#: 已验证的名录。新增一个只要写一条取条目函数，对账、复核件和图位判定都是共用的。
DIRECTORIES: tuple[MakerDirectory, ...] = (
    MakerDirectory("mgstage", "MGStage /ppv/makers.php", makers, COOKIES),
    MakerDirectory("prestige", "Prestige /api/maker", prestige_makers, {}),
    MakerDirectory("kmp", "KMP /label", kmp_makers, {}),
)


def ledger_studios(connection: sqlite3.Connection) -> tuple[dict, dict, dict]:
    by_roman, by_text, counts = {}, {}, {}
    for entity_id, name in connection.execute(
            "SELECT id,canonical_name FROM entity WHERE kind='studio'"):
        if fold_roman(name):
            by_roman.setdefault(fold_roman(name), (entity_id, name))
        by_text.setdefault(fold_text(name), (entity_id, name))
    for alias, entity_id, name in connection.execute(
            "SELECT a.alias,e.id,e.canonical_name FROM entity_alias a "
            "JOIN entity e ON e.id=a.entity_id WHERE e.kind='studio'"):
        if fold_roman(alias):
            by_roman.setdefault(fold_roman(alias), (entity_id, name))
        by_text.setdefault(fold_text(alias), (entity_id, name))
    for entity_id, total in connection.execute(
            "SELECT ae.entity_id,count(*) FROM asset_entity ae JOIN entity e "
            "ON e.id=ae.entity_id WHERE e.kind='studio' GROUP BY 1"):
        counts[entity_id] = total
    return by_roman, by_text, counts


def match(slug: str, name_ja: str, by_roman: dict, by_text: dict):
    """返回（entity_id, canonical_name, 判据）；对不上返回 None。"""
    roman, text = fold_roman(slug), fold_text(name_ja)
    if roman and roman in by_roman:
        return (*by_roman[roman], "slug 归一相等")
    if text and text in by_text:
        return (*by_text[text], "日文名相等")
    if roman and roman in by_text:
        return (*by_text[roman], "罗马字对上别名")
    if roman and len(roman) >= 4:
        # slug 是缩写时（`aknr` 对 `Akinori`）只能靠前缀，唯一命中才当候选。
        hits = {value for key, value in by_roman.items()
                if len(key) >= 4 and (key.startswith(roman) or roman.startswith(key))}
        if len(hits) == 1:
            return (*hits.pop(), "前缀候选")
    return None


def logo_state(safe: str, logo_root: Path) -> str:
    """这个厂牌现在两个位置上各有什么，决定这一枚是补空还是替换。"""
    if not logo_root.is_dir():
        return "无图"
    has = {"icon": (logo_root / f"{safe}.icon.img").is_file(),
           "logo": (logo_root / f"{safe}.logo.img").is_file(),
           "主位": (logo_root / f"{safe}.img").is_file()}
    marks = [key for key, ok in has.items() if ok]
    return "+".join(marks) if marks else "无图"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="账本路径")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--logo-root", type=Path,
                        help="给了就在复核件上标出每个厂牌现在两个位置各有什么")
    parser.add_argument("--cache-dir", type=Path, default=STATE_DIR / "cache" / "makers")
    parser.add_argument("--directory", nargs="*", default=[],
                        choices=[entry.key for entry in DIRECTORIES],
                        help="只跑这几个名录；不给就全部")
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--matched-only", action="store_true",
                        help="只输出对上账本的那些；默认连没对上的一起出，它们是入库时的现成来源")
    # `job_main` 直接读 args.lock，没有这一行会在拿锁时才 AttributeError。
    parser.add_argument("--lock", type=Path, default=STATE_DIR / ".maker-directories.lock")
    return parser


def run(args) -> int:
    wanted = set(args.directory or [entry.key for entry in DIRECTORIES])
    connection = open_readonly(args.db)
    try:
        by_roman, by_text, counts = ledger_studios(connection)
    finally:
        connection.close()

    from peach.previews import logo_key

    rows, matched, counted, fetched, cached = [], 0, {}, 0, 0
    for directory in DIRECTORIES:
        if directory.key not in wanted:
            continue
        # 每个名录一个缓存目录与自己的 cookie：年龄门是 MGStage 一家的要求，
        # 把它带到别家去是替对方声明它也有年龄门。
        site = Site(args.cache_dir / directory.key, args.interval, args.timeout,
                    refresh=args.refresh, via_proxy=True, cookies=directory.cookies)
        listing = directory.entries(site)
        counted[directory.key] = len(listing)
        fetched, cached = fetched + site.fetched, cached + site.cached
        for slug, (name_ja, logo_url) in sorted(listing.items()):
            hit = match(slug, name_ja, by_roman, by_text)
            if hit is None and args.matched_only:
                continue
            entity_id, studio, how = hit if hit else ("", "", "")
            if hit:
                matched += 1
            rows.append({
                "directory": directory.key,
                "slug": slug, "maker_ja": name_ja,
                "entity_id": entity_id, "studio": studio,
                "assets": counts.get(entity_id, "") if entity_id else "",
                "how": how, "logo_url": logo_url,
                "state": (logo_state(logo_key(studio), args.logo_root)
                          if studio and args.logo_root else ""),
            })
    write_rows(args.out, FIELDS, rows)
    print({"名录": counted, "合计": sum(counted.values()), "对上账本": matched,
           "取页": fetched, "命中缓存": cached, "output": str(args.out)})
    return 0


if __name__ == "__main__":
    # 进度与汇总里有日文厂牌名，`アリーナ･エンターテインメント` 的 `･` 在 GBK 控制台上
    # 编不出来，一个 print 就能把整批跑掀掉。证据在 CSV（UTF-8）里，控制台糊掉无所谓。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(job_main(build_parser, run))
