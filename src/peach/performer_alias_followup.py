"""同一位女优在别处用过的艺名，证据确定的登记成别名（ADR-0055）。

一位女优换过几次艺名、在不同发行渠道挂着不同名字，是常事：`神山ももか` 在 minnano-av 上
是 `雲母そら` 那一页的一个别名，同一页还列着 `美雲そら`、`朝霧いのり`、`雫つむぎ`。账本没登记
这些写法时，按名字链找头像、搜作品、判合并都只拿着一个名字在走。

判据是确定性的，所以按 ADR-0052 直接落库，不产生候选：

1. **入口**：minnano-av 走账本里已有的外部编号，或按账本名字检索唯一命中（结果表里对得上
   这个写法的行只指向一个编号）；av_neme 先按页名直取（改名页跟到现页），没有再站内搜索，
   检索结果按摘要把资料页排在归档页前面，读到的人物页里恰好一张列着她。
2. **双向核对**：账本里她的某个名字（规范名或已有别名，本身得是收得下的写法）必须出现在那一页的
   主名或别名栏里。
3. **只收名字栏**：minnano-av 资料表的「別名」行，av_neme 人物页的「名前(女優名)」「旧名義&別名」
   「名前(別名)」。读音与 `(FC2)`、`【旧名】` 这类注记剥掉。作品小节里的素人名义不读；
   `いちかちゃん` 这种带敬称的一次性称呼、`145cm色白お嬢様` 这种描述性称呼按
   `metadata_alias_resolve` 的判据不收，也不拿去检索。
4. **短单名不收**：两个字以内的写法（`舞香`、`茜`）与三个字以内的纯假名（`そら`、`みく`）
   只是名、不带姓，会命中别人（`entities.is_short_single_name`）；罗马字写法也不收，日文站和
   图库都用不上它。西方资料页收罗马字，单个词只在它就是页面主名时收（`latin_rejection`）。
5. **四种不写**，与 `scripts/apply_alias_candidates.py` 同一口径：已有、被另一条实体占用、
   统称已变、查无此人。
6. **两条实体合成一条**（ADR-0064）：minnano-av 或 av_neme 的名字栏把她和账本里另一条女优
   实体列成同一个人，且被占用的写法全指向那一条时，两条合并。保留作品多的一侧，一样多保留
   页上主名那一侧，再一样保留先登记的；被并入的名字与别名全留作别名。合并前先把账本备份到
   数据库目录（`ledger.pre-alias-merge-*`）。fc2cmadb 的曾用名栏混着卖家起的商品名，只记占用。

有 FC2 作品的女优先问第三站 fc2cmadb（ADR-0061）：从她自己作品页的女优栏进到站上那位
人物，那位人物的主名或曾用名栏里得有账本里她的名字，才收下站上的主名。曾用名栏那一串
混着卖家起的商品名，一个也不收；站上主名登记之后，本轮再拿它去搜另外两站，那两站的
名字栏照上面的判据补齐其余写法。

每条写入的 `entity_alias.source` 是这一轮的批次号 `auto:performer-alias@<任务行 id>`，
`scripts/revert_auto_landing.py --source auto:performer-alias` 按它整批撤回。每一轮的判词
（写了什么、为什么没写）逐条写进 `generated/performer-alias-landing.csv`。

外部请求走页面缓存与站间隔；撞上 429、403 或机器人验证就把这一站记进 `scraping_access`
的冷却记录，本轮与之后的后继在冷却期内都不再问它，结论写「未取得」。minnano-av 的 HTTP 客户端
被拦时先由本机浏览器取同一页验证，取到就固定走浏览器、不冷却（`MinnanoPages`）。这种结论的记号
只保 `RETRY_UNFETCHED`，过了再派一次；其余结论照 ADR-0053 记指纹，名字链不变不再派。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

from . import minnano_av
from .entities import (is_short_single_name, merge_entity, name_chain, name_rank,
                       normalize_entity_name)
from .followups import Attempts, Followup, FollowupType, attempts_root, register
from .kanji import fold_glyphs
from .metadata_alias_resolve import is_descriptive, is_planning_alias
from .migrations import sqlite_backup
from .scripting import HostLimiter
from .social_links import name_key
from .sources.base import challenge_page

#: 这类后继在任务中心的身份，也是活动页上那一行的名字来源。
TASK_KEY = "performer-alias"
TASK_LABEL = "补女优别名"

#: 这条后继写下的别名都以它开头，后面接 `@<任务行 id>`；撤回按它认。
SOURCE = "auto:performer-alias"

#: 每轮处理任务给存量的名额（`task_runs.MAX_FOLLOWUPS` 的一半）。一条要问两站、
#: 十来秒，走写账本那条串行通道；给多了会把厂牌那几条挤到很后面。九百多位女优按 16 条
#: 一轮要跑六十轮才轮遍，一半的名额把它压到三十轮以内。
STOCK_SHARE = 32
#: 结论是「未取得」（站在冷却、请求失败）时，记号只保这么久：那一轮的结论取决于站那天
#: 让不让进，不取决于她的名字。过了这段再派一次；不设期限的话，minnano-av 一次长冷却
#: 就让这一批女优永远停在「未取得」上。
RETRY_UNFETCHED = 24 * 3600

MINNANO, AV_NEME, FC2CMADB = "minnano-av", "av_neme", "fc2cmadb"
AV_NEME_ROOT = "https://seesaawiki.jp/av_neme/"
FC2CMADB_ACTRESS = "https://fc2cmadb.com/actresses/{id}"
#: 每站最多拿几个名字去检索、av_neme 一次搜索最多读几张人物页、一站一条后继最多几次请求。
MAX_KEYS, MAX_PERSON_PAGES, MAX_REQUESTS = 3, 3, 8
#: 入口判据的版本，进指纹（`fingerprint`）。换了入口就加一：跑过的女优按新入口各再问一次。
#: 2 是 ADR-0063 的 av_neme 按页名直取与摘要排序。
ENTRY_RULE = 2
#: fc2cmadb 最多翻她几部作品的女优栏：一部两次请求（作品页、女优栏），同一个人每部都一样。
MAX_WORKS = 3
#: minnano-av 的间隔与超时：与链接采集那一趟同一档，再放宽一点，这条后继不赶时间。
MINNANO_INTERVAL, TIMEOUT = 3.0, 25.0
#: 进程内共用：一轮里几十条后继一条接一条跑，每条各起一个限速器等于没有间隔。
#: avwikidb 与 minnano-av 同一档（补女优资料后继，ADR-0067）。
_LIMITER = HostLimiter({"minnano-av.com": MINNANO_INTERVAL, "seesaawiki.jp": 2.0,
                        "fc2cmadb.com": 2.0, "avwikidb.com": MINNANO_INTERVAL})

#: av_neme 搜索结果里明显不是人物页的页名：月份归档、番号页、厂牌与一览。
_NOT_A_PERSON = re.compile(r"\d{4}年|[A-Za-z]+-?\d{3,}|一覧|レーベル|メーカー")
#: 检索摘要里人物页与归档页各自的记号。人物页开头是资料栏（`旧名義&別名`、`生年月日`）；
#: 系列页、厂牌页与月份页的摘要是作品小节（`名前(女優名)：[[…]]`），一页几十部、搜谁都命中，
#: 常把她那一张人物页挤到第五、第六位之后。
_PROFILE_HINT = re.compile(r"プロフィール|旧名義|別名|生年月日|身長")
_LISTING_HINT = re.compile(r"名前[（(]女優名[）)]")
_BRACKETS = re.compile(r"[（(【\[][^（()）【】\[\]]*[）)】\]]")
_UNCERTAIN = re.compile(r"[?？▲�]|不明|未確認")

WRITE, HAVE, TAKEN, STALE, GONE, SKIP = "写入", "已有", "占用", "已变", "查无此人", "不收"
MERGE = "合并"
#: 名字栏能当合并证据的站：这两站的人物页由编辑维护，一页一个人。
MERGE_SITES = (MINNANO, AV_NEME)
REVIEW_FILE = "performer-alias-landing.csv"
FIELDS = ("entity_id", "canonical_name", "alias", "site", "page", "action", "detail", "batch")


class Blocked(RuntimeError):
    """这一站拒绝了请求或还在冷却：本轮不再问它。"""


class Unavailable(RuntimeError):
    """这一页没取到（网络、非 200、本条请求数用完）。"""


class Missing(Unavailable, LookupError):
    """站上没有这一页（404，或近期问过是 404）。"""


# -- 名字 --------------------------------------------------------------------


def clean(raw: str) -> str:
    """站上一格名字 → 写法本身：剥掉读音、`(FC2)`、`【旧名】` 这类括号注记。"""
    text = str(raw or "")
    while True:
        stripped = _BRACKETS.sub("", text)
        if stripped == text:
            break
        text = stripped
    return re.sub(r"\s+", " ", text).strip()


def rejection(name: str) -> str:
    """这个写法不收的原因；收得下返回空串。"""
    if not name:
        return "空名"
    if _UNCERTAIN.search(name):
        return "带不确定标记"
    if name_rank(name) >= 3:
        return "罗马字写法"
    if is_short_single_name(name):
        return "短单名，会命中别人"
    if is_planning_alias(name):
        return "一次性称呼"
    if is_descriptive(name):
        return "描述性称呼"
    return ""


def latin_rejection(name: str, primary: str) -> str:
    """允许罗马字的来源（西方资料页）里，这个写法不收的原因；收得下返回空串。

    `Lena`、`Anna`、`Mara` 这种单个词是名不是艺名，同名的人成百上千；只有它就是这一页的
    主名（页面把它当作这个人的完整艺名）时才收。企划名义的判据按日文的名字边界切分，
    罗马字的名与姓之间就是空格，不适用；描述性称呼照旧不收。
    """
    if len(name.split()) < 2 and match_key(name) != match_key(primary):
        return "罗马字单名，会命中别人"
    if len(match_key(name)) < 4:
        return "短单名，会命中别人"
    if is_descriptive(name):
        return "描述性称呼"
    return ""


def match_key(name: str) -> str:
    """比名字用的键：全半角、空白、大小写与日本字形都折掉（`涼森` 与 `凉森` 同键）。"""
    return fold_glyphs(name_key(name))


# -- 账本 --------------------------------------------------------------------


def followup_key(entity_id: int) -> str:
    return f"{TASK_KEY}:{int(entity_id)}"


def parse_key(key: str) -> int:
    """把后继 key 拆回女优 id。形状不对就抛——那说明排队的行不是这个版本写的。"""
    prefix, _, raw = str(key).partition(":")
    if prefix != TASK_KEY or not raw.isdigit():
        raise ValueError(f"认不出这条补别名后继：{key}")
    return int(raw)


def _names(connection: sqlite3.Connection, entity_id: int, *, own: bool = True, allow_creator: bool = False):
    """(规范名, [别名])；实体不在或不是女优返回 None。`own=False` 时不算这条后继自己写的。"""
    kinds = "('performer','creator')" if allow_creator else "('performer')"
    row = connection.execute("SELECT canonical_name FROM entity WHERE id=? AND kind IN " + kinds,
                             (int(entity_id),)).fetchone()
    if row is None:
        return None
    aliases = [str(alias) for alias, source in connection.execute(
        "SELECT alias,source FROM entity_alias WHERE entity_id=? ORDER BY alias", (int(entity_id),))
        if own or not _ours(str(source))]
    return str(row[0] or ""), aliases


def _ours(source: str) -> bool:
    return source == SOURCE or source.startswith(SOURCE + "@")


def _refs(connection: sqlite3.Connection, entity_id: int) -> list[str]:
    return [str(row[0]) for row in connection.execute(
        "SELECT external_id FROM entity_external_ref WHERE entity_id=? AND provider=?"
        " AND external_kind='performer' ORDER BY external_id", (int(entity_id), MINNANO))]


def fc2_codes(connection: sqlite3.Connection, entity_id: int) -> list[str]:
    """她出演的 FC2 作品番号，按番号排，最多 `MAX_WORKS` 部：fc2cmadb 那一站的入口。"""
    return [str(row[0]) for row in connection.execute(
        "SELECT DISTINCT a.code FROM asset a JOIN asset_entity ae ON ae.asset_id=a.id"
        " WHERE ae.entity_id=? AND ae.role='performer' AND a.code LIKE 'FC2%'"
        " ORDER BY a.code LIMIT ?", (int(entity_id), MAX_WORKS))]


def search_keys(canonical: str, aliases) -> list[str]:
    """拿去检索的写法：名字链里收得下的那些，日文写法在前。"""
    return [name for name in name_chain(canonical, list(aliases)) if not rejection(name)]


def has_entry(connection: sqlite3.Connection, entity_id: int) -> bool:
    """有没有路进得去：账本里有 minnano-av 编号、名字链里有一个能拿去检索的写法，或她有 FC2 作品。"""
    names = _names(connection, entity_id)
    return names is not None and bool(_refs(connection, entity_id) or search_keys(*names)
                                      or fc2_codes(connection, entity_id))


def fingerprint(connection: sqlite3.Connection, entity_id: int) -> str:
    """会让结论变的量：她的名字链与 minnano-av 编号，有 FC2 作品的再加上 fc2cmadb 那一站。
    这条后继自己写的别名不算在内——算进去的话，撤回一批之后指纹又变回来，下一轮就把刚撤掉的
    原样再写一遍。

    fc2cmadb 那一项只在她有 FC2 作品时进指纹：没有 FC2 作品的，指纹只有名字链与编号两项，
    存量不会因为多了一站整库重派；有的那些各重跑一次，把站上的主名补上。

    入口判据的版本（`ENTRY_RULE`）也在里面：入口换了，同一条名字链在站上能找到的页就不一样，
    跑过的都该按新入口再问一次。
    """
    names = _names(connection, entity_id, own=False) or ("", [])
    keys = sorted({match_key(name) for name in [names[0], *names[1]] if name})
    parts: list = [keys, _refs(connection, entity_id), f"r{ENTRY_RULE}"]
    if fc2_codes(connection, entity_id):
        parts.append(FC2CMADB)
    raw = json.dumps(parts, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _label(name: str) -> str:
    return f"{TASK_LABEL}：{name}" if name else TASK_LABEL


def plan(connection: sqlite3.Connection, *, since_entity_id: int) -> list[Followup]:
    """这一轮新登记、又有路进得去的女优，一人一条，作品多的在前。"""
    rows = connection.execute(
        "SELECT e.id,e.canonical_name,"
        " (SELECT count(DISTINCT ae.asset_id) FROM asset_entity ae WHERE ae.entity_id=e.id)"
        " FROM entity e WHERE e.id>? AND e.kind='performer' ORDER BY e.id",
        (int(since_entity_id),)).fetchall()
    found = sorted(((int(row[2] or 0), int(row[0]), str(row[1] or "")) for row in rows
                    if has_entry(connection, int(row[0]))), key=lambda item: (-item[0], item[1]))
    return [Followup(key=followup_key(entity_id), task_key=TASK_KEY, label=_label(name))
            for _assets, entity_id, name in found]


def stock(connection: sqlite3.Connection, attempts, *, limit: int, skip=(),
          avatar_root=None) -> list[Followup]:
    """库里早就登记的女优，最多 `limit` 条（ADR-0053）。

    给了 `avatar_root` 时缺头像的先排（ADR-0072），各自再按作品多的在前：别名进了账本，
    补头像后继才多一个名字去图库里找，`伊吹彩` 的 `月島舞香` 名下就有一张过门槛的大图。
    跑过一次、名字链与编号都没变的不再派（`attempts`）。
    """
    if limit <= 0:
        return []
    from .avatar_followup import needs_avatar

    rows = connection.execute(
        "SELECT e.id,e.canonical_name,count(DISTINCT ae.asset_id) AS assets"
        " FROM entity e JOIN asset_entity ae ON ae.entity_id=e.id"
        " WHERE e.kind='performer' GROUP BY e.id ORDER BY assets DESC, e.id").fetchall()
    if avatar_root is not None:
        lacking = [row for row in rows if needs_avatar(avatar_root, "performer", int(row[0]))]
        taken = {int(row[0]) for row in lacking}
        rows = lacking + [row for row in rows if int(row[0]) not in taken]
    skip, found = set(skip), []
    for row in rows:
        entity_id, key = int(row[0]), followup_key(int(row[0]))
        if key in skip or not has_entry(connection, entity_id):
            continue
        if attempts.settled(key, fingerprint(connection, entity_id)):
            continue
        found.append(Followup(key=key, task_key=TASK_KEY, label=_label(str(row[1] or ""))))
        if len(found) >= limit:
            break
    return found


# -- 取页 --------------------------------------------------------------------


class MinnanoPages:
    """minnano-av 的取页：成功页落盘缓存，一条后继至多 `MAX_REQUESTS` 次请求。

    缓存记着跳转后的最终地址：检索唯一命中会直接跳到资料页，判「这是谁的页」要看那个地址。
    撞上 429、403 或机器人验证就记冷却并抛 `Blocked`，不重试。

    默认传输是 `SourceTransport`：minnano-av 在 `scraping_access.SOURCES` 里登记了 `browser_fallback`，
    HTTP 客户端被拦时先由本机浏览器取同一页验证，取到就把这一站固定到浏览器，浏览器也没取到才冷却。
    补女优资料后继也拿它取 avwikidb 与 javdb 的页（`source` 换成那一站的冷却键）；`max_age` 给了
    就只认这么多秒以内的缓存，资料页过期重取靠它。
    """

    def __init__(self, cache_dir: Path, cooldown_root: Path, transport=None, *,
                 limiter=_LIMITER, max_requests: int = MAX_REQUESTS, source: str = MINNANO,
                 max_age: float | None = None):
        from .scraping_access import SourceTransport

        self.cache_dir, self.cooldown_root = Path(cache_dir), Path(cooldown_root)
        self.transport = transport or SourceTransport(self.cooldown_root)
        self.limiter, self.max_requests, self.requests = limiter, max_requests, 0
        self.source, self.max_age = source, max_age

    def _path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".json")

    def forget(self, url: str) -> None:
        """丢掉这一页的缓存：回 200 却不是要的那页（登入页）时，下一轮得重新问。"""
        self._path(url).unlink(missing_ok=True)

    def get(self, url: str) -> tuple[str, str]:
        from .http import HttpRequest
        from .scraping_access import SourcePaused, paused_until

        path = self._path(url)
        try:
            if self.max_age is None or time.time() - path.stat().st_mtime < self.max_age:
                cached = json.loads(path.read_text(encoding="utf-8"))
                return str(cached["final_url"]), str(cached["body"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if paused_until(self.cooldown_root, self.source):
            raise Blocked("来源正在冷却")
        if self.requests >= self.max_requests:
            raise Unavailable("这一条的请求数用完了")
        self.limiter.wait(url)
        self.requests += 1
        try:
            response = self.transport(HttpRequest("GET", url, {"Accept": "text/html"}),
                                      TIMEOUT, 4 << 20)
        except SourcePaused as error:
            raise Blocked(str(error)) from None
        except Exception as error:  # noqa: BLE001 - 网络层失败都是「这一页没取到」
            raise Unavailable(f"网络请求失败：{type(error).__name__}") from None
        body = response.body.decode("utf-8", "replace")
        if response.status == 429:
            self._hold(refused=False)
            raise Blocked("来源限流（429）")
        if response.status == 403 or challenge_page(body):
            self._hold(refused=True)
            raise Blocked("来源拒绝访问或要求机器人验证")
        if response.status != 200:
            raise Unavailable(f"HTTP {response.status}")
        final_url = response.url or url
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"url": url, "final_url": final_url, "body": body},
                                   ensure_ascii=False), encoding="utf-8")
        return final_url, body

    def _hold(self, *, refused: bool) -> None:
        """记这一站的冷却。`SourceTransport` 按它这次走的那条路（HTTP 客户端或浏览器）记，别的传输按 HTTP 客户端记。"""
        from .scraping_access import pause_source

        hold = getattr(self.transport, "hold", None)
        if hold is not None:
            hold(self.source, "blocked" if refused else "rate_limited")
        else:
            pause_source(self.cooldown_root, self.source, refused=refused)

    def close(self) -> None:
        close = getattr(self.transport, "close", None)
        if close:
            close()


class WikiSitePages:
    """Seesaa 上一个 Wiki 的取页：缓存、请求上限与撞墙判定都是 `sources.seesaa.WikiPages` 那一份，
    这里只把它撞墙的那一下按站名记进冷却，并在冷却期内一次都不问。缺省是 av_neme。

    站上没有的页（404）也记下（`missing.json`，保 `MISSING_TTL`）：按页名直取会问到不存在的页，
    同一个名字下一轮再跑不该为它再发一次请求。没有的页抛 `Missing`，与没取到分开。
    """

    MISSING_TTL = 30 * 86400

    def __init__(self, cache_dir: Path, cooldown_root: Path, transport=None, *, config=None,
                 max_requests: int = MAX_REQUESTS):
        from .sources.seesaa import AV_NEME as DEFAULT, WikiPages

        config = config or DEFAULT
        self.site = config.name
        self.pages = WikiPages(cache_dir, config=config, transport=transport, limiter=_LIMITER,
                               max_requests=max_requests)
        self.cooldown_root = Path(cooldown_root)
        self.missing_path = Path(cache_dir) / "missing.json"

    def _missing(self) -> dict[str, float]:
        try:
            found = json.loads(self.missing_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def get(self, url: str):
        from .metadata import MetadataProviderError
        from .scraping_access import pause_source, paused_until

        if paused_until(self.cooldown_root, self.site):
            raise Blocked("来源正在冷却")
        missing = self._missing()
        if time.time() - float(missing.get(url, 0)) < self.MISSING_TTL:
            raise Missing("站上没有这一页")
        try:
            return self.pages.get(url)
        except MetadataProviderError as error:
            if not self.pages.blocked:
                if error.status_code == 404:
                    missing[url] = time.time()
                    self.missing_path.parent.mkdir(parents=True, exist_ok=True)
                    self.missing_path.write_text(json.dumps(missing), encoding="utf-8")
                    raise Missing(str(error)) from None
                raise Unavailable(str(error)) from None
            limited = error.status_code == 429
            pause_source(self.cooldown_root, self.site, refused=not limited)
            raise Blocked("来源限流（429）" if limited else "来源拒绝访问或要求机器人验证") from None

    def close(self) -> None:
        self.pages.close()


class Fc2cmadbPages:
    """fc2cmadb 的取页：一部作品的女优栏（作品页一次、点名 `actresses` 再一次）。

    按作品号缓存解析结果，站上没有这部的也记下，下一轮不再问。Cookie、429 冷却走来源层的
    `SourceTransport`；403 与验证页它不记，这里补记，站在冷却期内一次都不问。
    """

    def __init__(self, cache_dir: Path, cooldown_root: Path, transport=None, *,
                 limiter=_LIMITER, max_requests: int = MAX_REQUESTS):
        from .scraping_access import SourceTransport

        self.cache_dir, self.cooldown_root = Path(cache_dir), Path(cooldown_root)
        self.transport = transport or SourceTransport(self.cooldown_root)
        self.limiter, self.max_requests, self.requests = limiter, max_requests, 0

    def actresses(self, video: str) -> list[dict]:
        from .sources.fc2cmadb import FC2CMADB as CONFIG, ARTICLE_PATH, parse_actresses, partial_headers

        path = self.cache_dir / f"{video}.json"
        try:
            return list(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            pass
        url = CONFIG.base_url + ARTICLE_PATH.format(video_id=video)
        page = self._get(url, {"Accept": "text/html"})
        headers = partial_headers(page) if page is not None else {}
        found = parse_actresses(self._get(url, {**headers, "Referer": url}) or b"") if headers else []
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
        return found

    def _get(self, url: str, headers: dict) -> bytes | None:
        """一页的正文；站上没有（404）回 None。"""
        from .http import HttpRequest
        from .scraping_access import SourcePaused, pause_source, paused_until

        if paused_until(self.cooldown_root, FC2CMADB):
            raise Blocked("来源正在冷却")
        if self.requests >= self.max_requests:
            raise Unavailable("这一条的请求数用完了")
        self.limiter.wait(url)
        self.requests += 1
        try:
            response = self.transport(HttpRequest("GET", url, headers), TIMEOUT, 4 << 20)
        except SourcePaused as error:
            raise Blocked(str(error)) from None
        except Exception as error:  # noqa: BLE001 - 网络层失败都是「这一页没取到」
            raise Unavailable(f"网络请求失败：{type(error).__name__}") from None
        text = response.body[:4096].decode("utf-8", "replace")
        if response.status == 429:
            pause_source(self.cooldown_root, FC2CMADB)
            raise Blocked("来源限流（429）")
        if response.status == 403 or challenge_page(text):
            pause_source(self.cooldown_root, FC2CMADB, refused=True)
            raise Blocked("来源拒绝访问或要求机器人验证")
        if response.status == 404:
            return None
        if response.status != 200:
            raise Unavailable(f"HTTP {response.status}")
        return response.body

    def close(self) -> None:
        close = getattr(self.transport, "close", None)
        if close:
            close()


def open_sites(contract) -> dict:
    """这一条后继用的三站取页器。测试把这一步整个换掉，网络就一次都不出。"""
    cache = Path(contract.candidate_root) / "provider-cache"
    cooldown = _cooldown_root(contract)
    return {MINNANO: MinnanoPages(cache / "minnano-av-pages", cooldown),
            AV_NEME: WikiSitePages(cache / "seesaa-pages", cooldown),
            FC2CMADB: Fc2cmadbPages(cache / "fc2cmadb-actresses", cooldown)}


def _cooldown_root(contract) -> Path:
    root = getattr(contract, "follow_secrets_root", None)
    return Path(root) if root is not None else Path(contract.candidate_root) / "provider-cache"


# -- 读页 --------------------------------------------------------------------


def _anchored(names: list[str], mine: set[str]) -> bool:
    """这一页列出的写法里，有没有一个是账本里她自己的、而且本身收得下的名字。"""
    return any(match_key(name) in mine for name in names if not rejection(name))


def minnano_page(pages: MinnanoPages, keys: list[str], refs: list[str]) -> tuple[list, str]:
    """([(页面地址, 页上全部写法)], 说明)。先按账本里的编号进，没有才检索。"""
    profiles, note = minnano_profile_pages(pages, keys, refs)
    found = []
    for url, html in profiles:
        main, aliases = minnano_av.profile_names(html)
        if main:
            found.append((url, [clean(main), *(clean(alias) for alias in aliases)]))
    return found, note


def minnano_profile_pages(pages: MinnanoPages, keys: list[str],
                          refs: list[str]) -> tuple[list[tuple[str, str]], str]:
    """([(页面地址, 资料页正文)], 说明)：`minnano_page` 的入口那一半，补女优资料后继也走它。"""
    notes = []
    profiles = [pages.get(f"{minnano_av.SITE}actress{ref}.html") for ref in refs]
    for key in [] if refs else keys[:MAX_KEYS]:
        final_url, html = pages.get(minnano_av.search_url(key))
        if minnano_av.page_actress_id(html):
            # 唯一命中时站点直接跳到资料页。
            profiles = [(final_url, html)]
            break
        hits = {found_id for found_id, shown in minnano_av.search_hits(html)
                if match_key(clean(shown)) == match_key(key)}
        if len(hits) == 1:
            profiles = [pages.get(f"{minnano_av.SITE}actress{hits.pop()}.html")]
            break
        notes.append(f"「{key}」{'对上多个人' if hits else '检索未命中'}")
    return profiles, "；".join(notes)


def search_order(hits: list[tuple[str, str, str]]) -> list[str]:
    """检索结果里该先读哪几页：摘要像资料栏的在前，像作品小节的在后，其余按站给的顺序。

    `叶芽ゆきな` 搜出八页，她的人物页 `桜美ゆきな` 排第六，前五页是 `ガチ素人`、`Girl's Blue`
    这些厂牌页与月份页。只读前 `MAX_PERSON_PAGES` 页的话，读到的全是归档页，
    结论就成了「没有人物页列着」。页名明显不是人物的（`_NOT_A_PERSON`）一开始就不要。
    """
    ranked = []
    for position, (url, title, text) in enumerate(hits):
        if not url.startswith(AV_NEME_ROOT + "d/") or _NOT_A_PERSON.search(title):
            continue
        if _PROFILE_HINT.search(text):
            tier = 0
        elif _LISTING_HINT.search(text):
            tier = 2
        else:
            tier = 1
        ranked.append((tier, position, url))
    return [url for _tier, _position, url in sorted(ranked)]


def _page_named(pages, key: str, mine: set[str]) -> tuple[str, list[str]] | None:
    """站上就叫她这个名字的那一页，是人物页且列着她就交回来。

    av_neme 的页面按页名寻址，人物页就叫她的名字：`初川みなみ` 的页在站内检索二十条结果里
    排不进去（每条都是提到她的别人的页和月份页），按页名直取一次就到。她改过艺名的话，
    旧名那一页是一句改名说明（`renamed_to`），跟一跳到现在那一页。页不存在、
    取不到就交 None，回到站内检索；站拒绝访问照样抛 `Blocked`。
    """
    from .sources.seesaa import person_profile, renamed_to

    title = key
    for _hop in range(2):
        try:
            page = pages.get(AV_NEME_ROOT + "d/" + quote(title.encode("euc_jp")))
        except (Unavailable, UnicodeEncodeError):
            return None
        names = [clean(name) for name in person_profile(page)[1]]
        if names and _anchored(names, mine):
            return page.url, names
        title = renamed_to(page)
        if not title:
            return None
    return None


def av_neme_page(pages, keys: list[str], mine: set[str]) -> tuple[list, str]:
    """([(页面地址, 页上全部写法)], 说明)。先按页名直取，没有再站内检索；
    读到的人物页里恰好一张列着她才算。"""
    from .sources.seesaa import person_profile, search_results

    notes = []
    for key in keys[:MAX_KEYS]:
        try:
            encoded = quote(key.encode("euc_jp"))
        except UnicodeEncodeError:
            notes.append(f"「{key}」写不成站上的编码")
            continue
        named = _page_named(pages, key, mine)
        if named is not None:
            return [named], ""
        listed = pages.get(AV_NEME_ROOT + "search?keywords=" + encoded)
        candidates = search_order(search_results(listed.body))
        matched = []
        for url in candidates[:MAX_PERSON_PAGES]:
            _main, names = person_profile(pages.get(url))
            names = [clean(name) for name in names]
            if names and _anchored(names, mine):
                matched.append((url, names))
        if len(matched) == 1:
            return matched, ""
        notes.append(f"「{key}」{'有 %d 张人物页都列着' % len(matched) if matched else '没有人物页列着'}")
        if matched:
            break
    return [], "；".join(notes)


def fc2cmadb_page(pages: Fc2cmadbPages, codes: list[str], mine: set[str]) -> tuple[list, str]:
    """([(人物页地址, [站上主名])], 说明)。她作品页女优栏里对得上她的那位，站上的主名。

    对得上：那位人物的主名或曾用名栏里有账本里她的名字。这里的「她的名字」不按
    `rejection` 筛——入口是她自己那部作品，账本里她叫 `たぬき顔サラサラ黒髪ロング`，站上
    那一格也叫这个，这就是同一位；筛掉了，站上替她记着的真名就永远接不上。

    交回去的只有站上主名。曾用名栏里混着卖家起的商品名（`ちっぱいリクルーター`、
    `製菓専門生`），现有判据挡不住，所以一个也不收；她的其余写法由另外两站的名字栏补。
    女优栏里不止一位列着她时判歧义，一个也不交。
    """
    from .sources.fc2 import video_id

    found: dict[str, tuple[str, str]] = {}
    notes = []
    for code in codes:
        video = video_id(code)
        if not video:
            continue
        listed = pages.actresses(video)
        matched = [one for one in listed if str(one.get("external_id") or "")
                   and any(match_key(name) in mine
                           for name in [one.get("japanese_name"), *(one.get("alias_names") or [])]
                           if name)]
        for one in matched:
            found[str(one["external_id"])] = (FC2CMADB_ACTRESS.format(id=one["external_id"]),
                                              clean(str(one["japanese_name"])))
        if matched:
            # 一部就够：同一个人在站上只有一页，再翻别的作品只是多花两次请求。
            break
        shown = "、".join(str(one.get("japanese_name") or "") for one in listed)
        notes.append(f"{code} 的女优栏{'是 ' + shown if shown else '是空的'}")
    if len(found) > 1:
        return [], "女优栏里不止一位列着她：" + "、".join(url for url, _name in found.values())
    return [(url, [name]) for url, name in found.values()], "；".join(notes)


# -- 落库 --------------------------------------------------------------------


def _owners(connection: sqlite3.Connection, *, allow_creator: bool = False) -> dict[str, set[int]]:
    """折叠键 → 用着这个写法的女优实体。"""
    owners: dict[str, set[int]] = {}
    kinds = "('performer','creator')" if allow_creator else "('performer')"
    for entity_id, written in connection.execute(
            "SELECT id,canonical_name FROM entity WHERE kind IN " + kinds +
            " UNION SELECT a.entity_id,a.alias FROM entity_alias a"
            " JOIN entity e ON e.id=a.entity_id WHERE e.kind IN " + kinds):
        if written:
            owners.setdefault(match_key(str(written)), set()).add(int(entity_id))
    return owners


def land(connection: sqlite3.Connection, entity_id: int, expected_name: str, site: str,
         url: str, names: list[str], batch: str, *, allow_latin: bool = False,
         allow_creator: bool = False) -> list[dict]:
    """把一页上的写法逐个判完，该写的写进 `entity_alias`。返回每个写法一行判词。

    `allow_latin` 给西方资料页用：`names[0]` 必须是页面主名，罗马字写法按
    `latin_rejection` 判。
    """
    base = {"entity_id": entity_id, "canonical_name": expected_name, "site": site,
            "page": url, "batch": ""}
    current = _names(connection, entity_id, allow_creator=allow_creator)
    if current is None:
        return [{**base, "alias": name, "action": GONE, "detail": "实体不在或不是女优"}
                for name in names]
    if normalize_entity_name(current[0]) != normalize_entity_name(expected_name):
        return [{**base, "alias": name, "action": STALE,
                 "detail": f"数据库里这条实体现在叫 {current[0]}"} for name in names]
    have = {normalize_entity_name(name) for name in [current[0], *current[1]]}
    owners, rows = _owners(connection, allow_creator=allow_creator), []
    for name in dict.fromkeys(names):
        reason = rejection(name)
        if allow_latin and reason == "罗马字写法":
            reason = latin_rejection(name, names[0])
        others = sorted(owners.get(match_key(name), set()) - {entity_id})
        if reason:
            rows.append({**base, "alias": name, "action": SKIP, "detail": reason})
        elif normalize_entity_name(name) in have:
            rows.append({**base, "alias": name, "action": HAVE, "detail": "已经有这个写法"})
        elif others:
            owner = connection.execute("SELECT canonical_name FROM entity WHERE id=?",
                                       (others[0],)).fetchone()
            # `owner` 与 `listed` 给合并判定看，不进复核产物：占用者恰好一条才算得清是哪一对，
            # `listed` 为 0 说明这个写法是页上的主名。
            rows.append({**base, "alias": name, "action": TAKEN,
                         "detail": f"{owner[0] if owner else ''}（实体 {others[0]}）已经用着这个写法",
                         "owner": others[0] if len(others) == 1 else 0,
                         "listed": names.index(name)})
        else:
            connection.execute(
                "INSERT OR IGNORE INTO entity_alias(entity_id,alias,normalized_alias,source,"
                "confidence) VALUES(?,?,?,?,1.0)",
                (entity_id, name, normalize_entity_name(name), batch))
            have.add(normalize_entity_name(name))
            owners.setdefault(match_key(name), set()).add(entity_id)
            rows.append({**base, "alias": name, "action": WRITE, "batch": batch,
                         "detail": f"{site} 页面列着数据库里她的名字"})
    return rows


def _record_review(root: Path, entity_id: int, rows: list[dict]) -> None:
    """复核产物里这位的那几行换成这一轮的判词；别人的行原样留着。"""
    from .review_csv import read_rows, write_rows

    path = Path(root) / REVIEW_FILE
    kept = [row for row in read_rows(path, missing_ok=True)
            if str(row.get("entity_id")) != str(entity_id)]
    write_rows(path, FIELDS, kept + rows, atomic=True, fill_missing=True)


def _works(connection: sqlite3.Connection, entity_id: int) -> int:
    return int(connection.execute("SELECT count(DISTINCT asset_id) FROM asset_entity"
                                  " WHERE entity_id=?", (int(entity_id),)).fetchone()[0])


def merge_one_person(contract, entity_id: int, rows: list[dict], batch: str) -> dict | None:
    """名字栏把她和另一条实体列成同一个人时，把两条合成一条（ADR-0064）；合不了返回 None。

    只在被占用的写法全指向同一条另外的实体时合：指向两条以上，页上的人比账本里多，
    那不是一对。保留作品多的一侧；一样多保留页上主名那一侧，再一样保留先登记的。
    合并不可逆，先按 `peach-ledger-write` 把账本备到数据库目录，托盘按备份保留规则清退；
    合并后外键校验多出违规就抛错让事务回滚。占用行改判成「合并」。

    校验比的是合并前后的差：合并之前库里就有的违规不是这次合并造成的。按全库绝对数判
    的话，别处一条孤儿行就让所有合并一起回滚——本机曾因一条已删关注源留下的条目，
    同一天六次合并全部失败。
    """
    taken = [row for row in rows if row["action"] == TAKEN and row["site"] in MERGE_SITES]
    owners = {int(row.get("owner") or 0) for row in taken} - {0}
    if len(owners) != 1 or any(not row.get("owner") for row in taken):
        return None
    other = owners.pop()
    with contract.database.read_connection() as connection:
        mine, theirs = _names(connection, entity_id), _names(connection, other)
        works = (_works(connection, entity_id), _works(connection, other)) if theirs else (0, 0)
    if mine is None or theirs is None:
        return None
    theirs_is_main = any(row["listed"] == 0 for row in taken if int(row["owner"]) == other)
    if works[1] > works[0] or (works[1] == works[0] and theirs_is_main):
        target_id, source_id, kept, absorbed = other, entity_id, theirs[0], mine[0]
    else:
        target_id, source_id, kept, absorbed = entity_id, other, mine[0], theirs[0]
    db_path = Path(contract.database.db_path)
    backup = db_path.with_name(
        f"ledger.pre-alias-merge-{time.strftime('%Y%m%d-%H%M%S')}-{source_id}.db")
    sqlite_backup(db_path, backup)
    with contract.database.write_transaction() as connection:
        if _names(connection, source_id) is None or _names(connection, target_id) is None:
            return None
        existing = {tuple(row) for row in connection.execute("PRAGMA foreign_key_check")}
        moved = merge_entity(connection, target_id=target_id, source_id=source_id,
                             source_name=absorbed, alias_source=f"merge:{batch}")
        broken = [row for row in connection.execute("PRAGMA foreign_key_check")
                  if tuple(row) not in existing]
        if broken:
            raise RuntimeError(f"合并实体 {source_id} 进 {target_id} 后外键校验有 {len(broken)} 条违规")
    contract.cache_bust()
    sites = "、".join(dict.fromkeys(row["site"] for row in taken))
    for row in taken:
        row.update(action=MERGE, batch=batch,
                   detail=f"{sites} 名字栏把两条实体列成同一个人：{absorbed}（实体 {source_id}）"
                          f"并入 {kept}（实体 {target_id}），迁 {moved['assets']} 部作品")
    return {"into": target_id, "from": source_id, "kept": kept, "absorbed": absorbed,
            "assets": moved["assets"], "backup": backup.name}


# -- 执行 --------------------------------------------------------------------


def run(contract, key: str, handle) -> dict:
    """跑一条补别名后继，再把她当时的指纹记进 `Attempts`，存量补派按它判。"""
    summary = _run(contract, key, handle)
    with contract.database.read_connection() as connection:
        current = fingerprint(connection, parse_key(key))
    outcome = str(summary.get("outcome", ""))
    Attempts(attempts_root(contract.candidate_root)).record(
        key, current, outcome, retry_after=RETRY_UNFETCHED if outcome == "未取得" else None)
    return summary


def _run(contract, key: str, handle) -> dict:
    entity_id = parse_key(key)
    batch = f"{SOURCE}@{getattr(handle, 'run_id', None) or time.strftime('%Y%m%dT%H%M%S')}"
    with contract.database.read_connection() as connection:
        names = _names(connection, entity_id)
        refs = _refs(connection, entity_id)
        codes = fc2_codes(connection, entity_id)
    if names is None:
        return {"outcome": "档案已不存在"}
    canonical = names[0]
    if handle is not None:
        handle.progress(label=_label(canonical), throttle=0)
    sites = open_sites(contract)
    try:
        reports: dict[str, str] = {}
        rows = _visit_fc2cmadb(contract, sites, entity_id, canonical, codes, batch, reports)
        more_reports, more_rows = _visit(contract, sites, entity_id, canonical, refs, batch)
        reports.update(more_reports)
        rows += more_rows
    finally:
        for pages in sites.values():
            pages.close()
    written = [row["alias"] for row in rows if row["action"] == WRITE]
    if written:
        contract.cache_bust()
    merged = merge_one_person(contract, entity_id, rows, batch)
    if rows:
        _record_review(contract.candidate_root, entity_id, rows)
    taken = sum(1 for row in rows if row["action"] == TAKEN)
    if merged:
        outcome = f"{merged['absorbed']} 并入 {merged['kept']}"
    elif written:
        outcome = f"登记 {len(written)} 个别名"
    elif rows:
        outcome = "没有新写法"
    elif any(report.startswith("未取得") for report in reports.values()):
        outcome = "未取得"
    else:
        outcome = f"{'三' if codes else '两'}站都没对上她"
    summary = {"name": canonical, "outcome": outcome, "sites": reports}
    if written:
        summary["aliases"] = written[:12]
    if taken:
        summary["taken"] = taken
    if merged:
        summary["merged"] = merged
    return summary


def _visit_fc2cmadb(contract, sites: dict, entity_id: int, canonical: str, codes: list[str],
                    batch: str, reports: dict[str, str]) -> list[dict]:
    """先问 fc2cmadb：站上主名登记之后，另外两站这一轮就能拿它去搜。没有 FC2 作品就不问。"""
    if not codes or FC2CMADB not in sites:
        return []
    with contract.database.read_connection() as connection:
        current = _names(connection, entity_id)
    if current is None:
        reports[FC2CMADB] = "未命中：档案已不存在"
        return []
    mine = {match_key(name) for name in [current[0], *current[1]] if name}
    try:
        pages, note = fc2cmadb_page(sites[FC2CMADB], codes, mine)
    except (Blocked, Unavailable) as error:
        reports[FC2CMADB] = f"未取得：{error}"
        return []
    if not pages:
        reports[FC2CMADB] = f"未命中：{note or '女优栏里没有她'}"
        return []
    rows: list[dict] = []
    for url, found in pages:
        with contract.database.write_transaction() as connection:
            rows.extend(land(connection, entity_id, canonical, FC2CMADB, url, found, batch))
    reports[FC2CMADB] = "命中 " + "、".join(url for url, _found in pages)
    return rows


def _visit(contract, sites: dict, entity_id: int, canonical: str, refs: list[str],
           batch: str) -> tuple[dict[str, str], list[dict]]:
    """两站各找她那一页，找到就当场落库。返回（每站一句结论, 每个写法一行判词）。

    走两圈：第一圈用账本里现有的名字；某一站没命中、而另一站登记了新写法时，第二圈拿新写法
    再搜那一站——`神山ももか` 在 av_neme 上搜得到 `雲母そら` 那一页，登记之后 minnano-av 才搜得到。
    """
    reports: dict[str, str] = {}
    rows: list[dict] = []
    visited: set[str] = set()
    tried: dict[str, set[str]] = {MINNANO: set(), AV_NEME: set()}
    for turn in range(2):
        wrote = False
        for site in (MINNANO, AV_NEME):
            if turn and not reports.get(site, "").startswith("未命中"):
                continue
            with contract.database.read_connection() as connection:
                current = _names(connection, entity_id)
            if current is None:
                reports[site] = "未命中：档案已不存在"
                continue
            mine = {match_key(name) for name in [current[0], *current[1]] if not rejection(name)}
            keys = [name for name in search_keys(*current) if name not in tried[site]][:MAX_KEYS]
            entry_refs = refs if site == MINNANO and not turn else []
            if not keys and not entry_refs:
                reports.setdefault(site, "未命中：没有能拿去检索的名字")
                continue
            if not entry_refs:
                tried[site].update(keys)
            try:
                if site == MINNANO:
                    pages, note = minnano_page(sites[site], keys, entry_refs)
                else:
                    pages, note = av_neme_page(sites[site], keys, mine)
            except (Blocked, Unavailable) as error:
                reports[site] = f"未取得：{error}"
                continue
            pages = [(url, found) for url, found in pages
                     if url not in visited and _anchored(found, mine)]
            if not pages:
                reports[site] = f"未命中：{note or '页面上没有数据库里她的名字'}"
                continue
            for url, found in pages:
                visited.add(url)
                with contract.database.write_transaction() as connection:
                    landed = land(connection, entity_id, canonical, site, url, found, batch)
                rows.extend(landed)
                wrote = wrote or any(row["action"] == WRITE for row in landed)
            reports[site] = "命中 " + "、".join(url for url, _found in pages)
        if not wrote:
            break
    return reports, rows


#: 写账本：别名进 `entity_alias`。取页在事务外做，写的那一下很短，
#: 但照样走写账本那一条串行通道（ADR-0040 第二条）。
TYPE = register(FollowupType(task_key=TASK_KEY, label=TASK_LABEL, writes_ledger=True, run=run))
