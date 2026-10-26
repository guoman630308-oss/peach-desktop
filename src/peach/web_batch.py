"""数据清理：广告残留判定、批量操作、物理清除与回收站。

放在同一个模块是因为它们是同一条链上的四段：`q_ads` 判定哪些文件是广告残留，
`w_batch` 把用户的批量决定落库，`purge_assets` 真正动磁盘，`w_empty_trash` 收尾。
判定与执行分家的话，「命中推广词」和「可以删」之间那道界线就没人守了——
剥掉推广词后还剩内容的文件不是广告，这条只有把判据和删除放在一起看才成立。

物理删除要清的引用表只写在 `personal_records.ASSET_REFERENCE_TABLES` 一处；文件隔离加数据库失败回滚
（`_restore_staged_media`）是这个模块最不能出错的部分：删错的文件找不回来。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid

from functools import cache
from pathlib import Path, PureWindowsPath
from typing import Sequence

from .catalog_rules import duration_clusters, is_jav_code, normalise_code_key
from .config import GENERATED_DIR, LOCATION_ROOT_DECLARATIONS
from .field_owners import USER_MANUAL, write_owned_fields
from .media_sources import SCANNABLE_LOCATIONS
from .personal_records import ASSET_REFERENCE_TABLES, VANISHED
from .platform import is_unmapped, root_online, translate_ledger_path, within_root
from .regions import normalize_region
from .settings_file import PROJECT_ROOT
from .task_runs import TaskRunHandle
from .web_activity import DEFAULT_PROFILE_ID
from .web_catalog import attach_card_performers
from .media_sources import cost_for
from .web_resource_sync import clean_resource_orphans
from .web_state import WebContract

#: 批量操作在活动页上的名字。表里存 `operation`，人看的是这一列。
BATCH_LABELS = {
    "like": "批量标记喜欢", "seen": "批量标记看过", "later": "批量加入稍后看",
    "dispose": "批量移入回收站", "restore": "批量还原", "delete": "批量永久删除",
    "dismiss-junk": "批量确认不是垃圾", "reconsider-junk": "批量重新判定垃圾",
    "region": "批量判定产地",
}


# 只认联系方式与站点形态的推广套话。「微信」「成人游戏」这类词单独出现不算：
# 实测正片标题里就有（「还要微信跟老公汇报战果」是剧情，不是联系方式）。
PROMO_PHRASE = re.compile(
    r"(扫码|扫一扫|掃一掃|扫描|掃描|QR ?CODE|二维码|二維碼|加微信|加微|威信\d|微信号|"
    r"微信\s*[:：]|免费看|免费玩|福利群|最新地址|"
    r"永久(?:域名|地址|发布)|地址发布器|防屏蔽|资源获取|点击(?:观看|下载|进入)|下载APP|下载|签到|代币|领取|"
    r"强力推荐|国产大片|在线视频|線上影片每天火熱更新|手机看片|手機影城|"
    r"大饱眼福|房间火爆|澳门|赌场|博彩|棋牌|加我|"
    r"GAMES?\d*|APP)", re.I)
# 只降低残留、自己不构成命中的套话。
#
# 手游插页整批绕过了上面的判据：`爱姬远征-免费18禁手游-扫码安装.jpg` 命中「扫码」，
# 可剥完还剩「爱姬远征免费禁手游安装」12 个字，落在 6~14 的中间档只拿 30 分，够不上
# 40 的门槛；`三国志侵略版-免费18禁手游-扫码安装.png` 多两字残留就到 14，连 30 分都没有。
# 残留越多分越低，而这些残留里一个内容字都没有——广告名写得越长反而越安全。
# 游戏名（爱姬远征、天下布魔）永远进不了词表，能剥的只有它旁边那圈固定套话。
# 这些词单独出现说明不了什么（真片名里也有「免费」），所以只参与残留计算，不作命中依据。
PROMO_FILLER = re.compile(
    r"(免费\d*禁|免费|免費|手游|手遊|成人遊?戲|成人游戏|下载安装|安装|安裝|访问|訪問)",
    re.I)
# 结尾不能用 \b：`uuc82.com_2` 里 `m` 和 `_` 都是词字符，构不成边界，域名会漏掉。
PROMO_DOMAIN = re.compile(
    r"(?:https?://)?(?:www\.)?[\w-]{2,}\.(?:com|net|me|la|xyz|cc|tv|top|vip|club|"
    r"info|org|pw|cn|app|site|online|shop)(?![a-z0-9])", re.I)
# 真番号带厂牌前缀和连字号（ABW-153、259LUXU-1141）。RAIKUN325 这类没有连字号的
# 是被误填进 code 的创作者账号名，不能拿来做「同番号有完整版」的比较，
# 判据与 `is_jav_code` 同源：分隔符正是番号与账号名的唯一线索。
REAL_CODE = re.compile(r"^(?:\d{2,3})?[A-Za-z]{2,8}-\d{2,5}$|^FC2", re.I)
PART_MARK = re.compile(r"(CD\d|part\d|分卷|-\d{1,2}$|\(\d+\)$)", re.I)
# 推广站目录的两种形态，与 `scripts/find_ads.py` 的判据 D/E 同源：
# 创作者位是旧导入器的目录名投影，`bbsxv.xyz-DOCP-324` 这类广告包会直接落在那里；
# 裸域名目录（98T.la@账号、huachishe.com@系列）是转载水印，不是广告，不能进判据。
AD_DOMAIN = re.compile(
    r"\b[0-9a-z][-0-9a-z]{1,20}\.(?:cc|xyz|xxx|com|net|la|me|top|vip|club|app|cn|pw|tv|gg)\b", re.I)
AD_DIRPACK = re.compile(
    r"[0-9a-z][-0-9a-z]{1,20}\.[a-z]{2,10}[ \-_]+\[?[A-Za-z]{2,6}-?\d{2,5}", re.I)
#: 同一目录里有这么多资产，它就是成套下载的资源包，不是塞进别人目录的插页。
#:
#: `jitumi.pw(77).gif` 与 `【91狼友之家91home.cc】19.jpg` 剥完只剩域名，是最硬的
#: 「整名推广语」信号，可它们分别躺在 `森萝财团 X-019 肉丝换白丝 [103P1V-1.39GB]`
#: （同目录 107 项）和 `赠送-稀缺整合【91home.cc】\海角\新建文件夹`（同目录 24 项）里：
#: 域名在这里是打包渠道给整包起的名，几十上百张成套的图本身就是要留的资源。
#: 广告插页反过来，是一两张挤在别人的番号目录里——WAAA-415 那个目录只有 13 项。
BUNDLE_DIR_ASSETS = 20
#: 同一目录里有这么多同类推广名，它们就是成群塞进来的插页。
#:
#: 与 `BUNDLE_DIR_ASSETS` 正好对称：资源是成套的正片与图集，插页是成群的同一段套话。
#: `三国志侵略版-免费18禁手游-扫码安装.png` 剥完仍剩 6 个字的游戏名，单看名字只够
#: 中间档；但同目录另外 10 个文件挂着一模一样的推广尾巴，谁都不是正片的一部分。
PROMO_CLUSTER_FILES = 3
#: 缩略图与封面相对正片的固定尾巴（`MIAD573_02.wmv` ↔ `MIAD573_02_s.jpg`，
#: `LXVS006-BD.iso` ↔ `lxvs006pl.jpg`）。`pl`／`ps` 是 JAV 大小海报的通用写法。
#: 不剥数字：剥了 `MIAD573_02_s` 会退到 `MIAD573`，那已经不是同一条正片。
SHOT_TAIL = re.compile(r"[-_. ]?(?:s|t|pl|ps|thumb|poster|cover|fanart|big|small)$", re.I)
#: 正片自己带的画质与载体尾巴，只在配对时剥，用来让海报对上原盘。
DISC_TAIL = re.compile(r"[-_. ]?(?:bd|bdmv|bdiso|bdrip|dvd|dvdiso|iso|uhd|fhd|hd)$", re.I)
#: 超过这个体积的文件自己就是内容，不必再看目录名。
#:
#: `javme.me_LXVS-006-BD\LXVS006-BD.iso` 是 19.8 GB 的蓝光原盘，却因为住在
#: 「域名+番号」的目录里被判 45 分。广告不会有大文件：整包插页加起来都不到 10 MB，
#: 一个上 GB 的文件是资源站给自己的资源改了目录名，不是资源站塞进来的推广。
CONTENT_BYTES = 1024 ** 3
INTERNET_SHORTCUT_SUFFIXES = frozenset({".url"})
#: 保存下来的网页。`.mhtml` 是 Edge 的整页存档，媒体目录里的这份没有内容价值；
#: 它和 `.url` 一样是「导航页」而不是媒体，不给体积兜底留机会。
PAGE_ARCHIVE_SUFFIXES = frozenset({".mhtml", ".mht", ".htm", ".html"})
#: 推广品牌词。它们单独出现在视频名里不构成证据——`麻豆传媒`、`外围`、`直播` 都可能是
#: 真片名的一部分——只有叠加推广形态、装饰符或推广目录上下文时才计分。
#: 名单来自 2026-09 用户实例 `B:\xxr\0208 (23)` 里自曝身份的推广站与推广 APP。
AD_BRAND = re.compile(
    r"(91(?:短视频|视频|国产|AV|約炮|约炮)|成人抖音|快手直播|杏吧|草榴|色中色|成人头条|"
    r"泡芙短视频|含羞草|她趣|台湾UU|全国外围|外围楼凤|楼凤|麻豆传媒映画|蜜桃影像|"
    r"成人游戏|169BBS|SEX169)", re.I)
#: 与品牌词同现才算推广名的形态词。`视频`、`直播` 这类过常见的词不进这张表：
#: `麻豆传媒映画APP-限时免费体验` 是推广包，`麻豆传媒 某作品` 是资源。
AD_BRAND_FORM = re.compile(
    r"(APP|一键|导航|聊天室|免费体验|限时免费|扫码|资源获取|网址|发布页)", re.I)
#: 品牌推广图的体积上限。`蜜桃影像传媒.png` 18 KB、`東方秋白…169BBS…` 75 KB，
#: 而任何一张内容图都在 MB 量级；品牌名 + 这种体积才是广告卡，不是作品套图。
BRAND_TINY_BYTES = 300 * 1024
#: 装饰符名（`❤91短视频❤.jpg`）：两侧都挂或挂多个爱心的小图，名字里没有实质描述。
DECOR_MARKS = "❤♥❥♡💕💗💖💘💝"
#: 装饰图体积上限。`❤草榴视频❤.jpg` 705 KB 是广告卡；同名配套封面（2 MB 级）不算，
#: 它走 `has_sibling_original` 的正片配对豁免。
DECORATED_MAX_BYTES = 1536 * 1024
#: 目录名自曝是推广包的形态：`-APP`、`一键约炮`、`論壇文宣`、聊天室等。
#: 只匹配目录分量，不看文件名；命中后目录里不构成内容的文件都进复核。
AD_DIR_FORM = re.compile(
    r"(論壇文宣|论坛文宣|文宣|宣傳|一键约炮|全国外围|外围楼凤|楼凤|聊天室|"
    r"免费体验|限时免费)", re.I)
#: `APP` 按词匹配：`Happy` 里也藏着 `app`，裸子串会把正常目录卷进来；
#: `.app` 是域名后缀，`bkm9.app` 那类推广目录靠目录里其他证据，不靠这一条。
AD_DIR_APP = re.compile(r"(?:^|[^A-Za-z.])APP(?:[^A-Za-z.]|$)", re.I)
#: 品牌名只在目录里还带媒体平台词时才算推广目录；`草榴视频` 是推广 APP，
#: `麻豆传媒` 单独出现可能是这个厂牌的资源目录。
AD_DIR_BRAND_MEDIA = re.compile(r"(?:视频|視頻|影视|影視|传媒|傳媒|直播|短视频|短視頻)", re.I)
JUNK_KINDS = frozenset({"video", "image", "audio", "archive", "url", "other"})
#: 达到这个体积的视频不再当广告残留。这个值与原有「小于 120 MB」证据同源；
#: 较大的同番号短版本属于重复清理问题，由 ``q_duplicates`` 承接。
JUNK_VIDEO_MAX_BYTES = 120 * 1024**2
MEDIA_SIDECAR_SUFFIXES = frozenset({".nfo", ".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx"})
INSTALLER_SUFFIXES = frozenset({".apk", ".exe", ".msi"})
#: 按固定命名识别的推广附件。名字和体积只能说明「像」，内容要人看过才算数。
PROMO_NAMES_FILE = PROJECT_ROOT / "resources" / "junk" / "promo_attachment_names.json"
#: 进入垃圾复核队列的分数线。
JUNK_REVIEW_SCORE = 40
#: `scripts/trash_junk.py --apply` 默认的自动移入回收站分数线。只能复核的证据
#: （固定命名、未探测时长视频的目录与体积证据）单独凑不到这一档。
AUTO_TRASH_SCORE = 60
PAGE_COMPONENT_SUFFIXES = frozenset({".js", ".css", ".aspx"})
PAGE_CHROME_IMAGE = re.compile(r"^(?:banner(?:_[bs])?|1000x90(?:yunding)?|count\d*|logo|lan\d*|you|zuo|zuoxia)$", re.I)
PAGE_PROMO_CLAIM = re.compile(r"(?:记住|記住).{0,12}(?:网址|網址)|(?:网址|網址).{0,8}(?:发布|發布)")


def promo_residue(name: str) -> int:
    """剥掉域名和推广套话后，还剩多少实质描述字符。

    这是区分「广告」与「正片被打了站点水印」的关键：
    `点击观看 房间火爆` 剥完什么都不剩；
    `236953.xyz 推特新晋4年绿帽美腿淫妻网黄「一个ren」…` 剥完仍有大段内容描述。

    剥掉的词替换成空串而不是空格：空格自己也在计数字符集里，换成空格等于每剥一个
    词就补回一个残留。`爱姬远征-免费18禁手游-扫码安装` 剥掉四段套话后只剩四个字，
    却因为补进来的四个空格数成 8，正好卡在门槛外——剥得越干净残留越高。
    """
    text = PROMO_DOMAIN.sub("", name or "")
    text = PROMO_PHRASE.sub("", text)
    text = PROMO_FILLER.sub("", text)
    # 只数中日韩文字与字母，忽略编号、扩展名和标点。
    return len(re.findall(r"[一-鿿぀-ヿ가-힯 A-Za-z]", text))


def _folder_index(connection) -> tuple[dict[str, int], dict[str, set[str]]]:
    """每个目录里有多少资产，以及其中正片的文件名主体。

    两条判据都要看候选的邻居而不只是它自己：成套资源看目录规模，封面与截图看
    同目录有没有同名正片。`rsplit` 而不是 `PureWindowsPath`：ledger 路径统统是
    Windows 形态，这里要跑全表七万多行，只取目录名不值得为每行造一个路径对象。

    正片不限于 `medium='video'`：蓝光原盘在账本里是 `other`，它同样是海报要配的那份
    内容。用体积兜住这一类，不去猜扩展名——`.iso`、`.mds`、`BDMV` 之外还有什么，
    库里下一份原盘才知道。
    """
    counts: dict[str, int] = {}
    videos: dict[str, set[str]] = {}
    for path, medium, size in connection.execute(
            "SELECT path,medium,size FROM asset WHERE path IS NOT NULL AND path<>''"):
        folder, _, filename = str(path).rpartition("\\")
        counts[folder] = counts.get(folder, 0) + 1
        if medium == "video" or (size or 0) >= CONTENT_BYTES:
            stem = filename.rsplit(".", 1)[0] if "." in filename else filename
            key = stem.casefold()
            videos.setdefault(folder, set()).add(key)
            trimmed = DISC_TAIL.sub("", key)
            if trimmed:
                videos[folder].add(trimmed)
    return counts, videos


def has_sibling_original(stem: str, sibling_videos: set[str]) -> bool:
    """True 表示同目录里有一条与它同名的正片，它是那条正片的封面或截图。"""
    key = str(stem or "").casefold()
    if not key:
        return False
    return key in sibling_videos or SHOT_TAIL.sub("", key) in sibling_videos


def is_decorated_name(stem: str) -> bool:
    """名字挂着两个以上装饰符，没有任何实质描述。

    样本是 `B:\\xxr\\0208 (23)` 里那批 7~20 KB 的 `❤91短视频❤.png`。
    只挂一个爱心不算：Telegram 导出与正常标题里单个爱心常见，两个以上才是装饰性命名。
    """
    return sum(str(stem or "").count(mark) for mark in DECOR_MARKS) >= 2


def _promo_directory(parts) -> bool:
    """路径里有任意一层目录自曝是推广包。

    三种形态：`APP` 词形（`_含羞草APP`）、促销目录名（`一键约炮`、`論壇文宣`）、
    品牌名叠加媒体词（`_草榴视频`）。只看目录分量，文件名自己是不是推广由调用方判。
    """
    for part in parts:
        text = str(part)
        if AD_DIR_APP.search(text) or AD_DIR_FORM.search(text):
            return True
        if AD_BRAND.search(text) and AD_DIR_BRAND_MEDIA.search(text):
            return True
    return False


def _junk_rows(connection):
    """读取能进入垃圾复核的物理文件；字幕、NFO 与大视频在最外层排除。"""
    marks = ",".join("?" for _ in SCANNABLE_LOCATIONS)
    rows = connection.execute(
        "SELECT id,location,name,medium,creator,code,size,duration,width,height,snapshot_path,"
        "feedback,disposal,play_count,leave_ratio,o_count,studio,ctx_orient,path "
        f"FROM asset WHERE location IN ({marks}) AND disposal IS NULL "
        "AND (COALESCE(medium,'other')<>'video' OR (size < ? "
        "AND (duration IS NULL OR duration <= 1200)))",
        (*SCANNABLE_LOCATIONS, JUNK_VIDEO_MAX_BYTES)).fetchall()
    # NFO 是媒体资料边车，不是等待清理的物理内容；即使名字或所在目录带推广词，
    # 也应由资料读取报告解析问题，不能进入会把文件移入回收站的垃圾队列。
    return [row for row in rows if PureWindowsPath(
        row["name"] or row["path"] or "").suffix.casefold() not in MEDIA_SIDECAR_SUFFIXES]


def q_ads(contract: WebContract, limit=200, offset=0, kind="", status="pending"):
    """疑似垃圾复核队列 —— **不自动删**，只排队让人看证据确认。

    没有可靠的单一判据（试过「同番号短版」会误伤 CD2/part1 分卷，
    试过「同名扩散」会误伤 001.mp4 这类通用名的真文件）。
    所以给的是**嫌疑分**，按分排序，人工看图定夺，确认的走既定 CSV 删除流程。

    2026-08-15 按用户标记的 21 条真广告重新标定：命中推广词本身不算证据，
    要看**剥掉推广词后还剩不剩内容**。三类实测误判据此排除：剧情里的「微信」、
    开头是盗版站域名但正文是真实描述、以及把创作者账号当成番号去比时长。

    2026-09-03 按用户复核的三类结果再调，判据从「只看文件名」扩到**看它的邻居**：
    手游插页靠 `PROMO_FILLER` 与不再补空格的残留计算入队，剩下几个残留仍高的靠
    `PROMO_CLUSTER_FILES`（同目录成群的同类推广名）补齐；同目录资产满
    `BUNDLE_DIR_ASSETS` 的成套资源包不再因整名是域名而入队；文件自己是真番号、
    自己超过 `CONTENT_BYTES`、或是同目录正片的封面／截图时，`AD_DIRPACK` 的目录
    证据不成立——蓝光原盘和它的海报都属于这一类。

    物理资源的类型不能成为免检条件。视频保留时长、体积和同番号长版证据；图片、
    音频、压缩包和其它文件走共用的推广名／推广目录证据；Windows ``.url`` 是网址
    快捷方式，在媒体目录中直接进入人工复核。在线资产不是待清理的物理文件，排除。

    广告包还有一类没有推广词的样本：目录名自曝（`一键约炮`、`-APP`、`論壇文宣`），
    整包只有一条正片加一张配套封面，其余是装饰符小图、品牌推广卡与网页存档。
    这几种形态各自独立计分，配套封面走正片配对豁免，推广目录里的长视频只加 30 分、
    要再叠一条时长或体积证据才到门槛。

    词库 `PROMO_NAMES_FILE` 里的固定命名，以及未探测时长视频的目录与体积证据，只够进队列：
    这部分分数单独凑够 `AUTO_TRASH_SCORE` 时压到线下，须有别的证据或人看过内容才进回收站。"""
    kind = str(kind or "").strip().casefold()
    status = str(status or "pending").strip().casefold()
    if kind and kind not in JUNK_KINDS:
        raise ValueError("invalid junk kind")
    if status not in {"pending", "dismissed"}:
        raise ValueError("invalid junk status")
    out, dismissed_ids = contract.cached_until_changed(
        "junk-scored", lambda: _scored_junk(contract))
    pending = [item for item in out if item["id"] not in dismissed_ids]
    dismissed = [item for item in out if item["id"] in dismissed_ids]
    pool = dismissed if status == "dismissed" else pending
    counts = {junk_kind: 0 for junk_kind in JUNK_KINDS}
    for item in pool:
        counts[item["junk_kind"]] += 1
    filtered = [item for item in pool if not kind or item["junk_kind"] == kind]
    # 缓存里那份是共享的，这一页的条目复制出来再挂演员，不改到下一次请求。
    items = [dict(item) for item in filtered[offset:offset + limit]]
    attach_card_performers(
        contract, [item for item in items if item.get("medium") == "video"])
    return {
        "total": len(filtered),
        "all_total": len(pool),
        "pending_total": len(pending),
        "dismissed_total": len(dismissed),
        "counts": counts,
        "kind": kind,
        "status": status,
        "items": items,
    }


def _promo_neighbour_counts(rows) -> dict[str, int]:
    """统计同目录中缺少内容描述的推广名。"""
    counts: dict[str, int] = {}
    for row in rows:
        name = row["name"] or PureWindowsPath(row["path"] or "").name
        stem = PureWindowsPath(name).stem
        if not (PROMO_PHRASE.search(stem) or PROMO_DOMAIN.search(stem)):
            continue
        if promo_residue(stem) >= 14:
            continue
        folder = str(row["path"] or name).rpartition("\\")[0]
        counts[folder] = counts.get(folder, 0) + 1
    return counts


@cache
def promo_name_rules() -> tuple[dict, ...]:
    """读取 `PROMO_NAMES_FILE` 的推广附件命名规则；文件缺失或格式不对直接报错。"""
    data = json.loads(PROMO_NAMES_FILE.read_text(encoding="utf-8"))
    rules = []
    for entry in data["rules"]:
        names = frozenset(re.sub(r"\s", "", name).casefold() for name in entry.get("names", ()))
        patterns = tuple(re.compile(pattern, re.I) for pattern in entry.get("patterns", ()))
        if not entry["reason"] or not (names or patterns) or int(entry["max_bytes"]) <= 0:
            raise ValueError(f"推广附件命名规则不完整：{entry}")
        rules.append({
            "reason": str(entry["reason"]),
            "suffixes": frozenset(str(suffix).casefold() for suffix in entry["suffixes"]),
            "max_bytes": int(entry["max_bytes"]),
            "parent_is_name": bool(entry.get("parent_is_name")),
            "names": names,
            "patterns": patterns,
        })
    return tuple(rules)


def _promo_name_reason(suffix: str, path: str, size: int) -> str:
    """命中词库里推广附件固定命名的理由；只够进复核队列，见 `AUTO_TRASH_SCORE`。"""
    item = PureWindowsPath(path)
    stem = item.stem
    for rule in promo_name_rules():
        if suffix not in rule["suffixes"] or not 0 < size <= rule["max_bytes"]:
            continue
        if rule["parent_is_name"] and item.parent.name != stem:
            continue
        if (re.sub(r"\s", "", stem).casefold() in rule["names"]
                or any(pattern.fullmatch(stem) for pattern in rule["patterns"])):
            return rule["reason"]
    return ""


def _attachment_junk_reason(suffix: str, path: str, size: int) -> str:
    """区分安装附件与网页存档组件。"""
    if suffix in INSTALLER_SUFFIXES and size < 64 * 1024**2:
        return "媒体目录中的安装附件"
    if suffix in PAGE_COMPONENT_SUFFIXES and any(
            part.casefold().endswith("_files") for part in PureWindowsPath(path).parent.parts):
        return "网页存档的脚本或样式附件"
    parent = PureWindowsPath(path).parent.name
    if (parent.casefold().endswith('_files') and PAGE_PROMO_CLAIM.search(parent)
            and AD_DOMAIN.search(parent[:-len('_files')]) and suffix in {'.gif', '.jpg', '.png'}
            and size < 2 * 1024**2
            and (PAGE_CHROME_IMAGE.fullmatch(PureWindowsPath(path).stem)
                 or (PureWindowsPath(path).name.casefold() == '1.gif' and size <= 4096))):
        return "推广网页存档的横幅、标志或导航图片"
    return ""


def _video_evidence(row, stem: str, longer: dict, promo_dir: bool,
                    directory_points: int) -> tuple[int, int, list[str]]:
    """视频自己的证据：同番号完整版、时长、体积与推广目录。

    返回（分数，其中只够复核的分数，理由）。时长没探测到的视频可能就是正片：
    推广创作者位或「域名+番号」目录（`directory_points`）、推广目录与体积只说明它住在哪、
    有多大，说明不了它是广告，这些分数只够进复核队列。
    """
    score, why = 0, []
    code = (row["code"] or "").strip()
    duration = row["duration"]
    longest = longer.get(code)
    if longest and REAL_CODE.match(code) and duration is not None and 0 < duration < longest * 0.2 \
            and not PART_MARK.search(stem):
        # 分卷已排除，真番号下不到两成时长基本就是片段/预告，单独即可入队复核。
        # 用户标记的 `反抗不如享受.mp4`（ABW-220，244 秒）正好卡在旧的 35 分门外。
        score += 40; why.append(f"同番号有 {longest/60:.0f} 分完整版")
    if duration is not None and 0 < duration < 240:
        score += 15; why.append("不足 4 分钟")
    context = directory_points
    if (row["size"] or 0) < JUNK_VIDEO_MAX_BYTES:
        score += 10; context += 10; why.append("小于 120 MB")
    if promo_dir:
        # 30 分单独不构成删片理由：正片也可能躺在别人起错名的目录里，
        # 要再叠一条时长或体积证据才到门槛。
        score += 30; context += 30; why.append("住在推广目录")
    probed = duration is not None and duration > 0
    return score, 0 if probed else context, why


def _scored_junk(contract: WebContract) -> tuple[list[dict], frozenset[int]]:
    """全部候选按嫌疑分从高到低，连同用户已确认不是垃圾的 id。

    与请求的类型、状态和分页无关：整份算一次（真实账本上两三秒），`q_ads` 按参数切。
    """
    with contract.read_connection() as c:
        rows = _junk_rows(c)
        # 同番号是否存在明显更长的版本；只在 code 是真番号时才有意义。
        longer = {r[0]: r[1] for r in c.execute(
            "SELECT code, max(duration) FROM asset WHERE medium='video' AND code IS NOT NULL "
            "AND code<>'' AND duration IS NOT NULL GROUP BY code")}
        dismissed_keys = [str(row[0]) for row in c.execute(
            "SELECT item_key FROM review_decision "
            "WHERE category='junk_file' AND status='rejected'"
        )]
        dismissed_ids = {int(key) for key in dismissed_keys if key.isdigit()}
        folder_assets, folder_videos = _folder_index(c)
    # 每个目录里挂着推广名的候选有多少个；插页判据要看它有没有同伙。
    promo_neighbours = _promo_neighbour_counts(rows)
    out = []
    for r in rows:
        d = dict(r)
        s, why = 0, []
        name = d.get("name") or PureWindowsPath(d.get("path") or "").name
        name_path = PureWindowsPath(name)
        nm = name_path.stem
        suffix = name_path.suffix.casefold()
        d["junk_kind"] = (
            "url" if suffix in INTERNET_SHORTCUT_SUFFIXES
            else (d.get("medium") if d.get("medium") in JUNK_KINDS else "other")
        )
        residue = promo_residue(nm)
        promo = bool(PROMO_PHRASE.search(nm) or PROMO_DOMAIN.search(nm))
        page_archive = suffix in PAGE_ARCHIVE_SUFFIXES
        if suffix in INTERNET_SHORTCUT_SUFFIXES:
            s += 60; why.append("网址快捷方式")
        elif page_archive:
            s += 60; why.append("网页存档")
        attachment_reason = _attachment_junk_reason(suffix, d.get("path") or name, d.get("size") or 0)
        if attachment_reason:
            s += 60; why.append(attachment_reason)
        # 只够进复核队列的分数：固定命名，和未探测时长视频的目录与体积证据。
        review_only = 0
        name_reason = _promo_name_reason(suffix, d.get("path") or name, d.get("size") or 0)
        if name_reason:
            s += JUNK_REVIEW_SCORE; review_only += JUNK_REVIEW_SCORE; why.append(name_reason)
        # 目录维度的证据：广告包的文件名往往干净（`极道世界.mp4`），唯一线索在旧导入器
        # 从目录名投影出来的创作者位或路径里。creator 位本身是推广站域名时，它就不再是
        # 「有归属所以是正片」的证据，下面两处对 creator 的信任都必须先排除这种情况。
        owner = d.get("creator") or ""
        owner_is_promo = bool(AD_DOMAIN.search(owner))
        real_owner = bool(owner) and not owner_is_promo
        # ledger 路径在两个平台都是 Windows 形态。`rpartition` 没有平台依赖，
        # 键也与 `_folder_index` 完全同源；`os.path.dirname` 在 macOS 会把整条
        # 反斜杠路径当成文件名。
        folder = str(d.get("path") or name).rpartition("\\")[0]
        # 成套下载的资源包里，域名是打包渠道给整包起的名，不是插页的自我暴露。
        bundled = folder_assets.get(folder, 0) >= BUNDLE_DIR_ASSETS
        # 目录名带推广站域名只说明「从哪个站下的」，说明不了这个文件是广告。
        # 文件自己是真番号、自己就有内容级的体积、或者是同目录正片的封面／截图时，
        # 目录证据就不成立。
        #
        # 只放非视频：广告包里的视频本来就叫 `极道世界.mp4`，它的 code 是旧导入器从
        # `bbsxv.xyz-DOCP-324` 目录名投影出来的真番号形状，而它自己就在同目录的正片
        # 名单里——两条豁免对视频都会自动成立，`AD_DIRPACK` 这条判据就没了。
        self_evident = d.get("medium") != "video" and (
            is_jav_code(d.get("code"))
            or (d.get("size") or 0) >= CONTENT_BYTES
            or has_sibling_original(nm, folder_videos.get(folder, frozenset()))
        )
        decorated = (d.get("medium") != "video" and is_decorated_name(nm)
                     and (d.get("size") or 0) <= DECORATED_MAX_BYTES)
        brand_hit = bool(AD_BRAND.search(nm)) and bool(
            AD_BRAND_FORM.search(nm)
            or (d.get("medium") == "image"
                and (d.get("size") or 0) <= BRAND_TINY_BYTES))
        promo_dir = _promo_directory(PureWindowsPath(d.get("path") or name).parent.parts)
        if promo and residue < 6 and not bundled:
            # 名字剥完只剩广告本身，这是最硬的信号。
            s += 60; why.append("整个名字都是推广语")
        elif promo and residue < 14 and not real_owner and not bundled:
            s += 30; why.append("推广语占了名字主体")
            neighbours = promo_neighbours.get(folder, 0)
            if neighbours >= PROMO_CLUSTER_FILES:
                s += 30
                why.append(f"同目录另有 {neighbours - 1} 个同类推广名")
        directory_points = 0
        if owner_is_promo:
            directory_points = 50; why.append("创作者位是推广站域名")
        elif AD_DIRPACK.search(folder) and not self_evident:
            directory_points = 45; why.append("目录是「域名+番号」的推广打包")
        s += directory_points
        # 非视频的推广形态：装饰符小图、品牌推广名、住在推广目录。`self_evident`
        # （真番号、内容级体积、正片配套图）与网页存档、网址快捷方式已单独计分，
        # 这里不重复叠加。
        if (d.get("medium") != "video" and not self_evident and not page_archive
                and suffix not in INTERNET_SHORTCUT_SUFFIXES):
            if decorated:
                s += 60; why.append("装饰符小图")
            elif brand_hit:
                s += 60; why.append("推广品牌名")
            elif promo_dir:
                s += 50; why.append("住在推广目录")
        if d.get("medium") == "video":
            points, weak, reasons = _video_evidence(d, nm, longer, promo_dir, directory_points)
            s += points; review_only += weak; why.extend(reasons)
        # 有真实创作者归属、且名字剥完仍有实质描述的，是被打了水印的正片，不是广告。
        if real_owner and residue >= 14 and not (attachment_reason or name_reason):
            s -= 45
        if review_only and s - review_only < AUTO_TRASH_SCORE:
            s = min(s, AUTO_TRASH_SCORE - 1)
        if s >= JUNK_REVIEW_SCORE:
            d["score"] = s; d["why"] = " · ".join(why)
            d["cost"] = cost_for(d['location'], d.get('path'))
            d["has_thumb"] = contract.has_snapshot(d["snapshot_path"])
            d.pop("snapshot_path", None)
            d.pop("path", None)
            out.append(d)
    out.sort(key=lambda x: (-x["score"], -(x["size"] or 0)))
    return out, frozenset(dismissed_ids)


def automatic_junk_candidates(contract: WebContract) -> list[dict]:
    """返回达到自动隔离线且未被用户驳回的候选，供组合根接入下载后处理。"""
    scored, dismissed = contract.cached_until_changed(
        "junk-scored", lambda: _scored_junk(contract))
    return [row for row in scored
            if int(row["id"]) not in dismissed and int(row["score"]) >= AUTO_TRASH_SCORE]


def _restore_staged_media(staged):
    """数据库失败时恢复隔离文件；本地备份带原路径与内容摘要。"""
    for original, quarantine in reversed(staged):
        if quarantine.exists() and not original.exists():
            manifest = quarantine.parent / 'restore.json'
            if quarantine.name == 'content' and manifest.is_file():
                record = json.loads(manifest.read_text(encoding='utf-8'))
                if record['original'] != str(original):
                    raise ValueError('隔离恢复路径不符')
                shutil.copyfile(quarantine, original)
                if hashlib.sha256(original.read_bytes()).hexdigest() != record['sha256']:
                    raise ValueError('隔离恢复内容不符')
                quarantine.unlink()
                manifest.unlink()
                quarantine.parent.rmdir()
            else:
                os.rename(quarantine, original)


def _quarantine_media(original: Path, *, location: str | None = None) -> Path:
    """同目录改名不支持时，小文件先保留本地完整备份再删除原文件。"""
    key = uuid.uuid4().hex
    name = (f'peach-purge-{key}.peach-quarantine'
            if location == 'pikpak' or PureWindowsPath(str(original)).drive.casefold() == 'a:'
            else f'.{original.name}.peach-purge-{key}.tmp')
    quarantine = original.with_name(name)
    try:
        os.rename(original, quarantine)
        return quarantine
    except OSError as error:
        if getattr(error, 'winerror', None) != 50 or original.is_symlink():
            raise
        stat = original.stat()
        if stat.st_size > 1024 * 1024:
            raise
    staging = GENERATED_DIR / 'purge-staging'
    staging.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='purge-', dir=staging))
    quarantine = folder / 'content'
    manifest = folder / 'restore.json'
    try:
        shutil.copyfile(original, quarantine)
        body = quarantine.read_bytes()
        current = original.stat()
        if len(body) != stat.st_size or (current.st_size, current.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            raise OSError('文件在隔离备份期间发生变化')
        record = dict(original=str(original), size=len(body),
                      sha256=hashlib.sha256(body).hexdigest())
        manifest.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        original.unlink()
        return quarantine
    except BaseException:
        if original.exists():
            quarantine.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
            folder.rmdir()
        raise


def _online_source_roots() -> dict[str, tuple[Path, ...]]:
    """Return only declared physical roots that can be enumerated now, per source."""
    roots: dict[str, tuple[Path, ...]] = {}
    for location, declarations in LOCATION_ROOT_DECLARATIONS.items():
        online = []
        for declaration in declarations:
            root = translate_ledger_path(declaration)
            if is_unmapped(root) or not root.is_dir() or not root_online(root):
                continue
            online.append(root)
        if online:
            roots[location] = tuple(online)
    return roots


def _remove_empty_ancestors(parent: Path, source_roots: Sequence[Path]) -> list[Path]:
    """Remove empty parents up to, but never including, a declared source root."""
    source_root = next(
        (root for root in sorted(source_roots, key=lambda item: len(item.parts), reverse=True)
         if within_root(parent, root)),
        None,
    )
    if source_root is None:
        return []
    removed: list[Path] = []
    current = parent
    while current != source_root and within_root(current, source_root):
        if current.is_symlink():
            break
        next_parent = current.parent
        try:
            current.rmdir()
        except FileNotFoundError:
            # CloudDrive may collapse an empty layer as soon as its last file vanishes.
            current = next_parent
            continue
        except OSError:
            # Non-empty, offline, or protected directories are a normal stop boundary.
            break
        removed.append(current)
        current = next_parent
    return removed


def purge_vanished_rows(contract: WebContract, rows) -> dict:
    """永久删除文件已不在盘上的这些行，连同引用与派生产物；返回 `_finish_purge` 的回执。

    带个人记录的在库行到不了这里：资源同步先把它们标「已消失」（ADR-0087）。

    `missing_only`：到了删的这一刻文件又在了（复核之后网盘才同步回来），这一行整条
    跳过进 `blocked`，媒体文件一个字节都不碰。这一批要删的只是账本行。
    """
    contract.cache_bust()
    outcome = None
    try:
        with contract.write_transaction() as connection:
            outcome = purge_assets(connection, rows, missing_only=True)
    except BaseException:
        if outcome is not None:
            _restore_staged_media(outcome["_staged"])
        raise
    return _finish_purge(outcome)


def _finish_purge(outcome):
    """Delete committed quarantine files; report any residue for explicit cleanup."""
    cleanup_pending = []
    for _original, quarantine in outcome.pop("_staged"):
        try:
            quarantine.unlink(missing_ok=True)
            manifest = quarantine.parent / 'restore.json'
            if quarantine.name == 'content' and manifest.is_file():
                manifest.unlink()
                quarantine.parent.rmdir()
        except OSError as error:
            cleanup_pending.append({
                "path": str(quarantine), "reason": error.strerror or str(error),
            })
    for snapshot in outcome.pop("_snapshots"):
        try:
            snapshot.unlink(missing_ok=True)
        except OSError:
            pass
    source_roots = tuple(root for roots in _online_source_roots().values() for root in roots)
    removed_directories: set[Path] = set()
    for parent in outcome.pop("_parents"):
        removed_directories.update(_remove_empty_ancestors(parent, source_roots))
    outcome["cleanup_pending"] = cleanup_pending
    outcome["empty_dirs_removed"] = len(removed_directories)
    return outcome


def purge_assets(connection, rows, *, missing_only: bool = False):
    """Quarantine media, delete ledger rows, and leave final removal to the caller.

    Renaming beside the source is reversible and stays on the same filesystem. The
    caller restores the quarantined names if commit fails, then permanently removes
    them only after the SQLite transaction has committed.

    ``missing_only`` 删的只是账本行：文件还在（或答不上在不在）的行进 ``blocked``。
    """
    purged, blocked, staged, snapshots, parents = [], [], [], [], []
    for row in rows:
        media = row["path"]
        if media and missing_only:
            try:
                present = translate_ledger_path(media).exists()
                reason = "文件仍在盘上"
            except OSError as error:
                present, reason = True, error.strerror or str(error)
            if present:
                blocked.append({"id": row["id"], "path": media, "reason": reason})
                continue
        elif media:
            original = Path(media)
            try:
                if original.exists() and not original.is_file():
                    raise OSError("not a regular file")
                if original.is_file():
                    quarantine = _quarantine_media(original, location=row['location'] if 'location' in row.keys() else None)
                    staged.append((original, quarantine))
            except OSError as error:
                blocked.append({"id": row["id"], "path": media,
                                "reason": error.strerror or str(error)})
                continue
        snapshot = row["snapshot_path"]
        if snapshot:
            snapshots.append(Path(snapshot))
        if media:
            parents.append(Path(media).parent)
        purged.append(row["id"])
    try:
        if purged:
            marks = ",".join("?" * len(purged))
            connection.execute(
                f"UPDATE playlist SET current_asset_id=NULL WHERE current_asset_id IN ({marks})",
                purged,
            )
            connection.execute(
                f"UPDATE playlist SET source_seed_asset_id=NULL WHERE source_seed_asset_id IN ({marks})",
                purged,
            )
            for table in ASSET_REFERENCE_TABLES:
                connection.execute(f"DELETE FROM {table} WHERE asset_id IN ({marks})", purged)
            connection.execute(f"DELETE FROM asset WHERE id IN ({marks})", purged)
    except BaseException:
        _restore_staged_media(staged)
        raise
    return {
        "purged": len(purged), "blocked": blocked,
        "_staged": staged, "_snapshots": snapshots, "_parents": parents,
    }


def w_empty_trash(contract: WebContract):
    """永久清空回收站：只处理 disposal='trash' 的资产，其余一律不碰。"""
    contract.cache_bust()
    outcome = None
    try:
        with contract.write_transaction() as connection:
            rows = connection.execute(
                "SELECT id,location,path,snapshot_path FROM asset WHERE disposal='trash'",
            ).fetchall()
            outcome = purge_assets(connection, rows)
    except BaseException:
        if outcome is not None:
            _restore_staged_media(outcome["_staged"])
        raise
    result = {"ok": True, "operation": "empty-trash", **_finish_purge(outcome)}
    result.update(clean_resource_orphans(contract))
    return result


def _batch_region_value(body) -> str | None:
    """批量判定产地要写进真相字段的值；`None` 表示退回未判定。

    `none` 是「撤回这个判定」。认不出的写法一律拒绝，而不是按 `normalize_region`
    的习惯当成未判定——那会把一次拼错的批量操作变成一次静默的清空。
    """
    requested = str(body.get("region") or "").strip().lower()
    value = normalize_region(requested)
    if not value and requested != "none":
        raise ValueError("unsupported region")
    return value or None


#: 还原与永久删除只对这两档成立：回收站里的，和文件已不在盘上、带着个人记录的
#: （`vanished`，ADR-0087）。还原是清掉 `disposal`，永久删除走 `purge_assets`。
DISPOSED = frozenset({"trash", VANISHED})


def _reject_ineligible_targets(operation: str, rows) -> None:
    """选中集合与操作对不上就拒绝整批，不做部分生效。"""
    if operation in {"restore", "delete"} and any(row["disposal"] not in DISPOSED for row in rows):
        raise ValueError("restore/delete is only allowed for recycle-bin or vanished assets")
    # 已消失的行文件不在盘上：进回收站再还原就成了在库，只能在孤儿记录里接回或彻底删除。
    if operation == "dispose" and any(row["disposal"] == VANISHED for row in rows):
        raise ValueError("vanished assets cannot be moved to the recycle bin")
    if operation in {"dismiss-junk", "reconsider-junk"} and any(
            row["location"] not in SCANNABLE_LOCATIONS
            or row["disposal"] is not None for row in rows):
        raise ValueError("junk decisions are only allowed for active physical assets")


def _batch_disposal_reason(body):
    """移入回收站的原因；显式空值用于撤销未记录原因的条目。"""
    if body.get("operation") != "dispose":
        return None
    reason = body.get("disposal_reason", "manual")
    if reason not in (None, "manual", "junk", "duplicate", "missing"):
        raise ValueError("unsupported disposal reason")
    return reason


def _write_batch_state(connection, rows, operation, now, disposal_reason):
    """同一事务写入反馈或回收站状态，并保留还原操作的撤销信息。"""
    ids = [row["id"] for row in rows]
    marks = ",".join("?" * len(ids))
    if operation == "restore":
        connection.execute(
            f"UPDATE asset SET disposal=NULL,disposal_reason=NULL,feedback_at=? WHERE id IN ({marks})",
            [now, *ids],
        )
        return {"restored_reasons": {str(row["id"]): row["disposal_reason"] for row in rows}}
    if operation == "dispose":
        connection.execute(
            f"UPDATE asset SET disposal='trash',disposal_reason=?,feedback_at=? WHERE id IN ({marks})",
            [disposal_reason, now, *ids],
        )
    else:
        connection.execute(f"UPDATE asset SET feedback='seen',feedback_at=? WHERE id IN ({marks})", [now, *ids])
    return {}


def w_batch(contract: WebContract, body):
    """Apply one explicit, reversible marker to a bounded selected set."""
    raw_ids = body.get("ids")
    if not isinstance(raw_ids, list):
        raise TypeError("ids must be a list")
    ids = list(dict.fromkeys(int(item) for item in raw_ids))
    if not ids or len(ids) > 200:
        raise ValueError("batch requires 1 to 200 assets")
    operation = body.get("operation")
    if operation not in {
        "like", "seen", "later", "dispose", "restore", "delete",
        "dismiss-junk", "reconsider-junk", "region",
    }:
        raise ValueError("unsupported batch operation")
    # 产地的取值在这里就要认出来，别等到写库那一步才发现拼错了。
    region_value = _batch_region_value(body) if operation == "region" else None
    disposal_reason = _batch_disposal_reason(body)
    marks = ",".join("?" * len(ids))
    contract.cache_bust()
    purge_outcome = None
    state_receipt = {}
    # 批量是一次写、几秒就完的事，但删除那一支会真的动磁盘：事后「刚才那一批到底
    # 删了多少、有没有半路失败」只有这条记录答得出。不声明互斥——两批不同的资产
    # 各改各的，互斥只会把正常的连续操作挡住。
    run = contract.task_runs.start("batch", trigger="manual", total=len(ids),
                                   label=BATCH_LABELS.get(operation, operation))
    handle = TaskRunHandle(contract.task_runs, run.id if run else None)
    try:
        with contract.write_transaction() as connection:
            found = connection.execute(
                f"SELECT id,path,snapshot_path,disposal,disposal_reason,location FROM asset WHERE id IN ({marks})", ids,
            ).fetchall()
            valid_ids = [row["id"] for row in found]
            if not valid_ids:
                raise ValueError("assets not found")
            _reject_ineligible_targets(operation, found)
            now = time.time()
            if operation in {"restore", "seen", "dispose"}:
                state_receipt = _write_batch_state(connection, found, operation, now, disposal_reason)
            elif operation == "delete":
                # 已消失的行文件本来就不在了，只删账本行；文件又回到盘上的那几条留着，
                # 下一轮扫描会把它们接回在库。选中里有一条已消失的，整批都按这条口径删。
                purge_outcome = purge_assets(
                    connection, found, missing_only=any(row["disposal"] == VANISHED for row in found))
            elif operation == "dismiss-junk":
                connection.executemany(
                    "INSERT INTO review_decision(category,item_key,status,note,updated_at) "
                    "VALUES('junk_file',?,'rejected','用户确认不是垃圾',?) "
                    "ON CONFLICT(category,item_key) DO UPDATE SET "
                    "status='rejected',note=excluded.note,updated_at=excluded.updated_at",
                    [(str(asset_id), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)))
                     for asset_id in valid_ids],
                )
            elif operation == "reconsider-junk":
                connection.executemany(
                    "DELETE FROM review_decision WHERE category='junk_file' AND item_key=?",
                    [(str(asset_id),) for asset_id in valid_ids],
                )
            elif operation == "region":
                # 走 `write_owned_fields` 而不是直接 UPDATE：产地是真相字段，用户这一
                # 笔要压得住后面每一轮刮削与推断，归属列是它唯一的凭据。
                write_owned_fields(connection, valid_ids,
                                   {"region": region_value}, USER_MANUAL)
            elif operation == "later":
                connection.executemany(
                    "INSERT OR IGNORE INTO watch_queue(profile_id,asset_id,added_at,source) "
                    f"VALUES('{DEFAULT_PROFILE_ID}',?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),'web-batch')",
                    [(asset_id,) for asset_id in valid_ids],
                )
            else:
                connection.executemany(
                    "INSERT INTO asset_preference(profile_id,asset_id,liked,reason,source,updated_at) "
                    f"VALUES('{DEFAULT_PROFILE_ID}',?,1,'','web-batch',strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
                    "ON CONFLICT(profile_id,asset_id) DO UPDATE SET liked=1,source='web-batch',"
                    "updated_at=excluded.updated_at",
                    [(asset_id,) for asset_id in valid_ids],
                )
    except BaseException as error:
        if purge_outcome is not None:
            _restore_staged_media(purge_outcome["_staged"])
        handle.finish("failed", error=f"{type(error).__name__}: {error}")
        raise
    if purge_outcome is not None:
        result = {"ok": True, "operation": operation, **_finish_purge(purge_outcome)}
        result.update(clean_resource_orphans(contract))
        handle.finish("succeeded", summary={"operation": operation,
                                            "changed": len(valid_ids)})
        return result
    handle.finish("succeeded", summary={"operation": operation,
                                        "changed": len(valid_ids)})
    return {"ok": True, "operation": operation, "changed": len(valid_ids), **state_receipt}


def _add_large_short_copy_cluster(items: list[dict], clusters: list[list[dict]]):
    """把同番号的大体积短版本并到完整版所在组，返回这个组。"""
    claimed = {item["id"] for cluster in clusters for item in cluster}
    full = max(items, key=lambda item: item.get("duration") or 0)
    full_duration = float(full.get("duration") or 0)
    short_copies = [item for item in items
                    if item["id"] not in claimed and item["id"] != full["id"]
                    and (item.get("size") or 0) >= JUNK_VIDEO_MAX_BYTES
                    and 0 < float(item.get("duration") or 0) < full_duration * 0.2
                    and not PART_MARK.search(PureWindowsPath(
                        str(item.get("name") or "")).stem)]
    if not short_copies:
        return None
    cluster = next((candidate for candidate in clusters if any(
        item["id"] == full["id"] for item in candidate)), None)
    if cluster is None:
        cluster = [full]
        clusters.append(cluster)
    cluster.extend(short_copies)
    return cluster


def _duplicate_groups(grouped: dict[str, list[dict]]):
    """把已按番号归组的资产整理成重复项 API 结果。"""
    groups = []
    for code, items in grouped.items():
        if len(items) < 2:
            continue
        clusters = [cluster for cluster in duration_clusters(items) if len(cluster) >= 2]
        short_copy_cluster = _add_large_short_copy_cluster(items, clusters)
        for cluster in clusters:
            largest = max(cluster, key=lambda x: x.get("size") or 0)
            longest = max(cluster, key=lambda x: x.get("duration") or 0)
            hashes_present = [x["hash"] for x in cluster if x["hash"]]
            hashes = set(hashes_present)
            for item in cluster:
                item["is_largest"] = item["id"] == largest["id"]
                item["is_longest"] = item["id"] == longest["id"]
                item.pop("hash", None)
                item.pop("disposal", None)
            groups.append({
                "code": code,
                "files": sorted(cluster, key=lambda x: -(x.get("size") or 0)),
                "count": len(cluster),
                "evidence": "same_code_short_copy" if cluster is short_copy_cluster
                else "duration",
                # 必须每个文件都有 sha1 且完全相同才算确证字节一致。缺一个哈希
                # 就只是「时长相近」的推断，不能对外宣称已确证。
                "identical": len(hashes) == 1 and len(hashes_present) == len(cluster),
                "drives": sorted({x["drive"] for x in cluster}),
                "cross_drive": len({x["drive"] for x in cluster}) > 1,
                "reclaimable": sum(x.get("size") or 0 for x in cluster)
                - (largest.get("size") or 0),
            })
    return groups


def q_duplicates(contract: WebContract, args):
    """按番号 + 时长找真重复，也收同番号的大体积短版本。"""
    limit = min(max(int(args.get("limit", "60")), 1), 300)
    offset = max(int(args.get("offset", "0")), 0)
    with contract.read_connection() as connection:
        rows = connection.execute(
            "SELECT id,code,location,path,name,size,duration,hash,disposal "
            "FROM asset WHERE medium='video' AND code IS NOT NULL AND code<>'' "
            "AND disposal IS NULL"
        ).fetchall()

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        if not is_jav_code(row["code"]):
            continue
        item = dict(row)
        # 盘符只用于判定跨盘；重复项页面还要显示完整路径，不能在契约层丢掉。
        item["drive"] = str(item.get("path") or "")[:2].upper()
        grouped.setdefault(normalise_code_key(row["code"]), []).append(item)

    groups = _duplicate_groups(grouped)
    groups.sort(key=lambda g: -g["reclaimable"])
    window = groups[offset:offset + limit]
    return {
        "total": len(groups),
        "files": sum(g["count"] for g in groups),
        "reclaimable": sum(g["reclaimable"] for g in groups),
        "groups": window,
        "has_more": offset + limit < len(groups),
    }
