"""给「只有宽幅字标」和「一张图都没有」的厂牌补标识，出 `icon` 与 `logo` 两个变体。

已安装的厂牌图里有一部分源图是宽条字标，`normalize_studio_logos.py` 的边车记着它们。
补方让这些字标在 160px 的厂牌页大位上好看，但塞进筛选片那种 28px 的小圆里只剩一条糊字，
所以小位要另找一枚方标。社媒头像早就分 icon / logo 两用，厂牌按同一条判断走。另有一批
厂牌连一张图都没有，它们不在那份名单里，可两个位置一样空着，
所以也纳进来。第三批是**取回来过、但取小了**的：已装方标的短边不够小圆片的实像素，
`cover` 铺满就是在放大它（见 `INSTALLED_SHORT_EDGE`）。

取哪一份先问 `site_icons`：官网首页声明的 apple-touch-icon / SVG / manifest 优先，
都没有才落到 `/favicon.ico`。**站点声明的不等于就该用**，还有两条后手（见下）。
合格与否**不能**直接用 `link_marks.render_mark`：
它是为 `/link-mark` 那种 128px 圆标写的，`MIN_DESIGNED_SIZE=96` 会把 32×32、64×64 的
favicon 一律退回。实测七个 JAV 厂牌站，六个的 favicon 内容比在 1.0～1.84 之间——
是方标，只是小。退给一个 28px 的筛选片用绰绰有余，按「不是标识」退掉是判错了。

所以这里只借 `link_marks` 里真正表达 icon / 字标之分的那一条：`content_aspect` 与
`MAX_CONTENT_ASPECT`。尺寸另设自己的下限，因为要顶的位置本来就只有 28～32px。
`MIN_DESIGNED_SIZE` 不动：那是另一个调用方的正确取值。

**声明那一枚可能根本不是标识。** ACT 的官网 `<link rel=icon>` 指着一张艺人照片
（2026-09-05 实测，YuNet 0.934 分检出一张脸）。它够大、也够方，两道闸门都过，装上去
事务所页顶着一个人的脸。所以自动发现的候选还要过一道人脸闸：检出脸就退回，判词
`人像不是标识`。用户指定的来源不过这道闸——指定就是结论。模型取不到时整轮不设这道闸，
统计里说明，不静默当成「都没有脸」。

**声明之外还有三个来源，按这个顺序。** 站点自己 header 里那张 `<img>`
（`site_logos.logo_images`）、人在登录态解出来的社媒头像（`named_avatars`）、
它挂在页面上的 X 账号头像（`site_logos.x_profiles` + `avatar_tiers`）。bambi.ne.jp 是
这条链的实测样本：它只声明一枚 16×16 的 `favicon.ico`；header 那张 `header_logo.png`
是 187×57 的字标；X 头像 119×119；而它 Instagram 的头像是 1000×1000。用户 2026-09-05
的口径：站点自己那几张都不清楚、头像反而清楚时，头像既做 icon 也做 logo。

**够用就停，不够用就比大小。** 排在前面的来源更正式，但正式不等于清楚。取到的短边够
`GOOD_ENOUGH_SHORT_EDGE` 就不再敲别人的门；到不了就把这条链接的其余来源问完，取明显
更大的那枚（`BETTER_BY` 倍以上才算「明显」——114 和 119 谁大不该决定用谁的标）。
**大不能压过形。** 顶替者还要不比被顶的那枚宽出 `ASPECT_SLACK` 倍：eltra.jp 的
800×536 是皇冠加字，短边大出 1.79 倍，可 28 px 的小位要的是它那枚 300×300 的纯皇冠。
两位各挑各的——大位另取这一趟最大的一枚，正是被小位嫌宽的那张。

两条实测逼出来的规矩：

**共享主机守卫。** 链接带非根路径时（`bangbros.com/websites/BangBus`），发现流程从
`origin(url)` 出发，路径一丢，同主机的几个频道全坍缩成一枚 Aylo 站点模板 favicon。
那一枚 64×64、内容比 1.00，两道闸门都过，却和任何频道无关。所以这条路径上主机级候选
一律不算数（`site_icons.HOST_SCOPE`），判词 `平台通用图标`。`/link-mark` 那个位置本来
就是按主机的，不受这条约束。

**字标可装。** 用户 2026-09-03 定的口径：找不到方形标识时，宽扁字标烤成方图装进 `icon`
位也比露出无图强。所以方标一个都没做成、却取回过够大的字标时，用 `images.bake_square`
烤成方图装上，判词 `字标补白`，内容比照记——复核时那个数就是「这枚其实是字标」的提示。
同一份方图再出一行 `logo`：不出这行大位会回落到母品牌的 `<safe>.img`（BangBus 页顶着
BANGBROS），是错图。两位装的都是方图：页面三处取图位都是 `object-fit: cover` 的方框，
宽条装上去只剩中间两个字母。用户指定的 logo 来源做成时优先。

装盘的字节一律过 `images.bake_square`，所以 logo 目录里的文件天然是不透明方图。
入口只有这一个，`normalize_studio_logos.py` 是同一条规则对历史文件的回溯。

两个入口共用这一份判据：`scripts/harvest_studio_icons.py` 默认只出复核 CSV 和候选 PNG，
`--install` 才写 `<safe>.icon.img` 与 `<safe>.logo.img`；处理任务的补厂牌后继
（`studio_followup`）对单个新厂牌走同一条链，可装的判词直接落盘（ADR-0052）。
`<safe>.img` 只在尚不存在时补写一份，已有的一个字节都不动。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import face_detect, images, link_marks, site_icons, site_logos
from .http import HttpRequest, HttpxTransport
from .scripting import RateLimiter
from .social_links import is_archive


FIELDS = ("entity_id", "studio", "safe", "variant", "installed", "original_size",
          "link_kind", "url", "verdict", "mark_size", "content_aspect", "sha256",
          "candidate", "evidence")

#: 和 `/link-mark` 用同一个 UA：站点按它决定给不给图标，两处不一致会取到不同的东西。
from peach.user_agent import USER_AGENT

OK, WORDMARK, TOOSMALL = "ok", "仍是字标", "只有小图标"
MISSING, SKIP = "未取得", "无官网链接"
#: 方标没有，装的是补白过的字标。可以装，但复核件上要能看出装的是哪一类。
PADDED = "字标补白"
#: 取到了、也够清楚，但那是整个主机共用的一枚，代表不了这个实体。
SHARED = "平台通用图标"
#: 取回来的是一张照片。站点自己声明它是图标也不算数——那一位要的是标识。
PORTRAIT = "人像不是标识"
#: 会被 `--install` 落盘的判词。`平台通用图标` 与 `仍是字标` 不在内。
INSTALLABLE = (OK, PADDED)

ICON, LOGO = "icon", "logo"

#: 小标要顶的位置是 28px 的筛选片和 32px 的圆。短边到不了这个数，缩下去只是一团糊，
#: 还不如继续用现在那张补白字标——至少它是清晰的。
MIN_SHORT_EDGE = 32
#: `logo` 位是厂牌页那个 160 px 大位，2x 屏要 320 px；96 是「还能看」的下限，
#: 低于它说明取到的是缩略图不是标识资产。
MIN_LOGO_SHORT_EDGE = 96
#: 已装方标的短边下限。小圆片（`.brandpill .mk`）是 32 CSS px，2 倍屏 64 实像素，
#: 短边到不了这个数，`cover` 铺满就是在放大它。站点上往往还挂着更大的一份：
#: dorcelclub.com 声明了 180×180 的 apple-touch-icon，装着的那枚是 57×57
#: （2026-09-08 实测）。这些厂牌要再问一趟，见 `small_installed_marks`。
INSTALLED_SHORT_EDGE = 64
#: 够用就停的线：公司格最宽 180 CSS px，2 倍屏要 360 实像素，再大在页面上没有分别。
#: 到不了这条线才把这条链接的其余来源问完——每多问一个来源就多敲一次别人的门。
GOOD_ENOUGH_SHORT_EDGE = 360
#: 后面的来源要大出这个倍数才顶掉前面的。声明的 114 对上头像的 119 谁大不该决定用谁的
#: 标：那点差别在页面上看不出来，而来源的正式程度有差。bambi 声明的 16×16 对上
#: 1000×1000 的头像才是该换的那种差距。
BETTER_BY = 1.5

#: 顶替者的内容比允许比被顶的那枚宽出多少。`MAX_CONTENT_ASPECT` 管的是「这还算不算
#: 一枚方标」，这一条管的是同一趟里两枚都合格时谁更适合 28 px 的方框——那个尺寸下
#: 一点点宽就是一行认不出的字母。
ASPECT_SLACK = 1.1

#: `logo` 位的指定来源，按 canonical_name。`icon` 位的覆盖在
#: `site_icons.HOST_OVERRIDES`，两张表不能合并：那一张是「按主机发现图标」的例外，
#: 这一张是「这个厂牌的大字标在哪」，键的含义和取用位置都不同。
#:
#: FC2-PPV：用户 2026-09-03 指定 seeklogo 的 429409 这一份，600×600 P 模式、独角兽 +
#: 「FC2」文字、sha256 `6911574c…f1b7`（8916 B，2026-09-03 实测）。同站还有一份
#: 2000×662 的横向字标，那是字标不装这里。FC2 站上自己的大资产只有 189×68、690×68 两条
#: 横向字标，缩到 160 px 认不出。
#:
#: 剩下 22 条来自 jae.tokyo（Japan Adult Expo 2014／2015／2017 的参展厂牌名录），
#: 用户 2026-09-04 指定的来源。名录每届各带一套片商 logo：2017 是 320×320，2015 是
#: 188×188，2014 是 270×180，同一家出现在多届时取像素最多的那一届。键一律写账本
#: canonical_name，名录上厂牌自称的写法只用来认人：名录写 `ムーディーズ`、账本写
#: `MOODYZ`，落盘名跟账本走。2016 那届只有图没有名字，认不出是谁家的，不取。
#: 这些厂牌在账本里一张图都没有，且绝大多数连一条 official／catalog 链接都没有——
#: favicon 那条路走不到它们，所以小位也从这一张烤（见 `icon_from_logo`）。
#: 展会名录只是权宜之计：厂牌自己的名录交的是同一枚标识的当期版本，两边都有的按
#: `WORDMARK_SOURCES` 走。`シロウトTV` 的大位在这里，小位另有指定（`ICON_SOURCES`）。
LOGO_SOURCES: dict[str, str] = {
    "FC2-PPV": "https://images.seeklogo.com/logo-png/42/1/fc2-logo-png_seeklogo-429409.png",
    "JET映像": "http://www.jae.tokyo/jae2017/images/maker/maker_image/042.png",
    "MOODYZ": "http://www.jae.tokyo/jae2017/images/maker/maker_image/054.png",
    "MARRION": "http://www.jae.tokyo/jae2017/images/maker/maker_image/050.png",
    "V&R PRODUCE": "http://www.jae.tokyo/jae2014/exhibitor/images/logo/vr_logo.jpg",
    "S1 NO.1 STYLE": "http://www.jae.tokyo/jae2017/images/maker/maker_image/053.png",
    "センタービレッジ": "http://www.jae.tokyo/jae2015/images/maker/08_center@vllage.png",
    "DOC": "http://www.jae.tokyo/jae2017/images/maker/maker_image/025.png",
    "MAXING": "http://www.jae.tokyo/jae2014/exhibitor/images/logo/MAXING_logo.jpg",
    "SOD Create": "http://www.jae.tokyo/jae2015/images/maker/11_sod.png",
    "Baltan": "http://www.jae.tokyo/jae2014/exhibitor/images/logo/baltan_logo.jpg",
    "BeFree": "http://www.jae.tokyo/jae2015/images/maker/17_befree.png",
    "Dogma": "http://www.jae.tokyo/jae2015/images/maker/34_dogma.png",
    "桃太郎映像出版": "http://www.jae.tokyo/jae2017/images/maker/maker_image/022.png",
    "乱丸": "http://www.jae.tokyo/jae2015/images/maker/21_ran.png",
    "TEPPAN": "http://www.jae.tokyo/jae2014/exhibitor/images/logo/teppan_logo.jpg",
    "kira*kira": "http://www.jae.tokyo/jae2014/exhibitor/images/logo/kirakira_logo.jpg",
    "ゲッツ！！ボンボン/妄想族": "http://www.jae.tokyo/jae2017/images/maker/maker_image/029.png",
    "シロウトTV": "http://www.jae.tokyo/jae2017/images/maker/maker_image/034.png",
    "PREMIUM": "http://www.jae.tokyo/jae2015/images/maker/27_premium.png",
    # 妄想族自家发行目录 `mousouzoku-av.com/maker/list/<50音>/`（用户 2026-09-04 指定），
    # 213 家一律 `contents/maker/id<N>/logo_l.jpg`、200×200 真方标——这一位要的正是方的，
    # 不必烤。目录里 173 家挂 妄想族、39 家挂 エマニエル，都是同人／独立厂牌，
    # 和账本对上 8 家：`ゲッツ！！ボンボン/妄想族` 已有 jae.tokyo 的图，其余七家在这里。
    # 名录写的日文名就是账本 canonical_name（`Asia/妄想族`），按整名对；后四家的
    # catalog 链接指向同一个 maker id（用户 2026-09-23 授权）。
    "ABC/妄想族": "https://www.mousouzoku-av.com/contents/maker/id001/logo_l.jpg",
    "Asia/妄想族": "https://www.mousouzoku-av.com/contents/maker/id003/logo_l.jpg",
    "ティーチャー/妄想族": "https://www.mousouzoku-av.com/contents/maker/id360/logo_l.jpg",
    "下半身タイガース/妄想族": "https://www.mousouzoku-av.com/contents/maker/id367/logo_l.jpg",
    "ゆりえっち/妄想族": "https://www.mousouzoku-av.com/contents/maker/id403/logo_l.jpg",
    "山と空/妄想族": "https://www.mousouzoku-av.com/contents/maker/id416/logo_l.jpg",
    # 直撇号是账本 canonical_name 的写法；弯撇号那一份是别名，过 `safe_name` 同样落到
    # `AVS_collector_s`，按哪一种写都取得到同一个文件。
    "AVS collector's": "https://www.mousouzoku-av.com/contents/maker/id358/logo_l.jpg",
    # 素人ホイホイ是妄想族与旗下 power、sweet！ 等配信品牌之间的那一级（ADR-0051），
    # 自己没有作品；名录里 `素人ホイホイ/妄想族` 那一格的方标（2026-09-24 实测 200×200）。
    "素人ホイホイ": "https://www.mousouzoku-av.com/contents/maker/id379/logo_l.jpg",
    # 官网 `ks-p.com` 上的 120×120 方标。同站另有一张 376×82 的横版字标 `ks_logo.jpg`，
    # 烤成方图后字只占中间一窄条；X 头像只有 96 px（2026-09-23 实测）。
    "OFFICE K'S": "https://ks-p.com/wp-content/uploads/2019/10/label001.jpg",
}


#: 指定的**字标**来源，按 canonical_name。和 `LOGO_SOURCES` 的区别是形状不是画质：
#: 这一张里的图一律宽扁，只能烤成方图再用，两个位置装的都是那张方图。
#:
#: 为什么不并进 `LOGO_SOURCES`：`logo_row` 把指定来源原样装进大位，而大位是
#: `.entityportrait` 那个 `aspect-ratio:1` + `object-fit:cover` 的 160 px 方框，
#: 宽扁图进去只剩正中间那几个字母。`MIN_LOGO_SHORT_EDGE` 拦下 406×86 拦得对——
#: 问题不在 96 这个数，在于宽扁字标根本不该走那条原样装的路。
#:
#: 来源是 `harvest_maker_directories.py` 那三个厂牌名录（用户 2026-09-04 与 09-22 指定），
#: 对账后人工确认。MGStage `/ppv/makers.php` 十一页 351 家，站上有两种规格
#: （2026-09-04 量过）：通用的 `pc/<slug>.gif` 是 180×54 白底纯字标，首页轮播位另有
#: `pc/top/<slug>.jpg` 400×80／406×86。取的一律是前者，理由见下面 Jackson 那条。
#: Prestige `/api/maker` 的 `api/media/maker/banner-<slug>.jpg` 一律 200×55；KMP `/label`
#: 的 `img2018/label/<slug>/logo.svg` 是矢量，栅格到多大都清晰。
#:
#: **收已有图的厂牌要逐张比过。** 名录版未必更大：jae.tokyo 那批展会图是 320×320 的
#: 真方标，换成 180×54 的字标是降级。用户 2026-09-22 逐张看过对比图后点名换的五家在
#: 下面各自带一条判词，其余已有图的厂牌不收。
#:
#: `きらきらワイフ` 与 `おっぱいちゃん` 不在表里：对账时它们只走到「前缀候选」，撞上的
#: `kira*kira` 和 `OPPAI` 是另外两家真实厂牌，而这两家都已经有图，收进来只有装错的风险。
WORDMARK_SOURCES: dict[str, str] = {
    "Flower": "https://static.mgstage.com/mgs/img/pc/flower.gif",
    "ヒビノ": "https://static.mgstage.com/mgs/img/pc/hibino.gif",
    "HMJM": "https://static.mgstage.com/mgs/img/pc/hmjm.gif",
    "Ienergy": "https://static.mgstage.com/mgs/img/pc/ienergy.gif",
    "いんすた": "https://static.mgstage.com/mgs/img/pc/insta.gif",
    # Jackson 是 Prestige 旗下 label，母公司名录那份 `banner-jackson.jpg` 是 1024×346、
    # 白底的纯字标，烤成方图落地 1024×1024（2026-09-22 实测）。MGStage 同一家有两份：
    # `pc/jackson.gif` 180×54，首页轮播位 `pc/top/jackson.jpg` 400×80 带洋红底，烤方后
    # 上下补出两大块洋红。三份里只有母公司这一份既是白底纯字标又够大。
    "Jackson": "https://www.prestige-av.com/api/media/maker/banner-jackson.jpg",
    "まんまんランド": "https://static.mgstage.com/mgs/img/pc/manmanland.gif",
    "Planet Plus": "https://static.mgstage.com/mgs/img/pc/planetplus.gif",
    # Prestige 旗下只在 MGStage 发行的 label（ADR-0049），母公司 `/api/maker` 那 11 家里没有它。
    "PRESTIGE PREMIUM": "https://static.mgstage.com/mgs/img/pc/prestigepremium.gif",
    "Radix": "https://static.mgstage.com/mgs/img/pc/radix.gif",
    "S-Cute": "https://static.mgstage.com/mgs/img/pc/scute.gif",
    "VIP": "https://static.mgstage.com/mgs/img/pc/vip.gif",
    "Waap Entertainment": "https://static.mgstage.com/mgs/img/pc/waap.gif",
    # 这一条不来自 MGStage 名录，是厂牌官网 `mko-labo.net` header 里那张 391×143 的
    # 字标（2026-09-22 实测）。指定它是为了挡住自动发现给小位挑的那一枚：页面上挂的
    # X 账号头像是一张项圈的照片，400×400、内容比 1.04，够大也够方，人像闸也过——
    # 里面没有脸——装上去厂牌页顶着一条项圈。照片不是标识，这一点闸门判不出来。
    "えむっ娘ラボ":
        "https://cdn.up-timely.com/image/16/site_design/base/logo_image"
        "/agm4a2ESultyKGcW2F3tWNjLS0VTjbjzXuueyhai.png",
    # ナンパTV 是 Prestige 旗下 label，自己没有站，账本里也一条链接都没有——自动发现
    # 那四条链都是从链接出发的，对它一条都启动不了。母公司名录 `/api/maker` 里有它
    # 200×55 的白底字标（2026-09-22 实测），番号 `200GANA` 与账本那 9 部对得上。
    "ナンパTV": "https://www.prestige-av.com/api/media/maker/banner-nanpa.jpg",
    # ラグジュTV 同属 Prestige。这一条是这五家里唯一的降分辨率：名录版 200×55 烤方后
    # 落地 200×200，展会那份是 413×413。用户 2026-09-22 看过两版后仍指定名录版——
    # 展会图是 2017 年那一届交的，名录版是厂牌现在挂在自己站上的那一枚。
    "ラグジュTV": "https://www.prestige-av.com/api/media/maker/banner-lux.jpg",
    # 下面五家是 KMP 旗下 label，`/label` 名录给的是 SVG。矢量没有固有像素，栅格到
    # `link_marks.VECTOR_RASTER_SIZE` 就是 512×192，烤方落地 512×512，比展会那批
    # 416～421 的位图大且是矢量源（2026-09-22 实测）。白字标由 `bake_square` 配深底。
    # 后两家此前没有图，账本 catalog 链接即名录的 `?works=sq`／`?works=real`。
    "million": "https://www.km-produce.com/img2018/label/million/logo.svg",
    "BAZOOKA": "https://www.km-produce.com/img2018/label/bazooka/logo.svg",
    "俺の素人": "https://www.km-produce.com/img2018/label/oreshiro/logo.svg",
    "S級素人": "https://www.km-produce.com/img2018/label/sq/logo.svg",
    "Real Works": "https://www.km-produce.com/img2018/label/real/logo.svg",
    # 母公司自己那一枚：官网 header 的矢量字标（viewBox 135×56，栅格 512×213，内容比 2.46）。
    # 原装的是 X 头像 400×400 位图；用户 2026-09-25 点名换官网 SVG。
    "K M Produce": "https://www.km-produce.com/wp-content/themes/km-produce-v4/img/logo.svg",
    # 官网 `to-satsu.com` header 与 og:image 同一张 240×120 透明底字标，内容比 2.03
    # （2026-09-25 实测）。官网 09-23 才登记，此前盘上一张图都没有；用户同日点名。
    "変態紳士倶楽部": "https://cdn.up-timely.com/image/22/site_design/base/logo_image"
                "/0dwpajZ71cySRqLuIcHX4o4BkKaNRzYSx50Yrk1n.png",
    # 下面六家是 2026-09-25 全库审计后用户按推荐换的官网 header 字标，能用矢量的用矢量。
    # Prestige 原装展会名录 413×413 位图、一本道原装红底方块，都是同一枚标识的位图版；
    # Deep's 原装的 X 头像是一张卡车照片。三家官网 header 都是 SVG。
    "Prestige": "https://www.prestige-av.com/_nuxt/img/logo.e232877.svg",
    "一本道": "https://www.1pondo.tv/img/common/logo-1pondo.svg",
    "Deep's": "https://deeps.net/img/common/logo.svg",
    # 这三家官网只有位图，同目录猜 `.svg` 都是 404（2026-09-25 实测）。アロマ企画 原装 X 头像
    # 200×200，官网同一枚 521×257；本中 原装 64×64 只有一个「本」字；E-BODY 原装 X 头像
    # 只有一个「E」图形。后两家的站在 up-timely 上，平台只收位图。
    "アロマ企画": "https://www.aroma-p.com/pc/images/common/logo.png",
    "本中": "https://cdn.up-timely.com/image/25/site_design/base/logo_image"
          "/MTbIeAhJDw6XIrhHyXhmQkVaVlTdS4xs1GVZ2ulz.png",
    "E-BODY": "https://cdn.up-timely.com/image/6/site_design/base/logo_image"
              "/zFSBseqj7KtWPX3CmRWkqGcY05jt3wRtPQimobuo.png",
    # 妄想族是片商实体，作品全挂在旗下 label 上（ADR-0049），没有展会图也没有名录那一格。
    # 自家站 header 的字标 332×187（2026-09-23 实测），就是它现在挂在门面上的那一枚。
    "妄想族": "https://www.mousouzoku-av.com/pc/images/pages/common/logo_mousouzoku.png",
    # 下面两家官网只剩 Wayback 快照，名录里也没有它们。取快照 header 的字标 `id_` 原件：
    # アキノリ 142×81、Janes 200×60（2026-09-24 实测，用户看过候选拼图后按推荐指定）。
    "アキノリ": "https://web.archive.org/web/20170426023131id_/"
               "http://www.aknr.com/wp/wp-content/images/post/1/logo.png",
    "Janes": "https://web.archive.org/web/20131222105818id_/http://www.jns-grp.net/images/jns-logo.gif",
}


#: 指定的**方标**来源，按 canonical_name。只管 `icon` 位，大位仍由 `LOGO_SOURCES`
#: 或发现流程出——这是它和另外两张表的全部区别：那两张都是一张图管两位。
#:
#: 存在的理由只有一个形态：厂牌交出来的那张图里，方标和字标是并排的两块，而两个位置
#: 要的是不同的那一块。`シロウトTV` 是实测样本——名录版 200×55 里左边是黑框「素」方标
#: （主体 41×49，内容比 1.20），右边是「シロウトTV」横排字标。`bake_square` 的
#: `refit_plate` 按内容裁一遍，落地 64×64 正好是那枚「素」（2026-09-22 实测），顶
#: 28 px 的筛选片和 32 px 的圆片够用；而展会那份 414×414 的横条字标缩到 28 px 只剩
#: 一团糊，它该待的位置是 160 px 的大位。
#:
#: 键可以和 `LOGO_SOURCES` 重合——两位各取各的正是这张表的用途；和
#: `WORDMARK_SOURCES` 不能重合，那一张已经把两位都占了。
ICON_SOURCES: dict[str, str] = {
    "シロウトTV": "https://www.prestige-av.com/api/media/maker/banner-shiroutotv.jpg",
}


#: 事务所的指定**字标**来源，按 canonical_name，用法同 `WORDMARK_SOURCES`。
#:
#: 单列一张而不并进厂牌那张：`--kind agency` 那一趟只查这一张，厂牌表一条都不看。
#: 两种实体共用 `logo_key` 落盘名，同名的事务所去查厂牌表，就会装上那家厂牌指好的图。
#:
#: `Diaz Group`：名录只给了招募站 `diaz-recruit.com`，那是 STUDIO 建站、JS 渲染的
#: 页面，静态 HTML 不链集团官网，站点图标是建站平台的「/S」。集团官网 `diaz-g.com`
#: header 里是一张 147×27 的矢量字标（用户 2026-09-23 指定）。
#:
#: `Luminous Promotion`：2022 年由 BELLTECH 改屋号，账本 official 里旧站
#: `belltech-pro.com` 排在前面，自动发现取到的是旧屋号字标。新站 header 是 1741×532
#: 的透明底字标（2026-09-23 实测）。
#:
#: `ONE'S DOUBLE`：现站 SSL 连不上，官网只剩 Wayback 快照。字标是快照页脚那张 216×30
#: 的黑色 SVG，取 `id_` 原件，不带存档站注入的工具栏（2026-09-24 实测）。
#:
#: `ONE'S DOUBLE` 之后那 11 家同样只剩快照（链接见复核表
#: `entity-archive-links-20260924.csv`）。每家在各自快照里取了一到三张候选，用户看过
#: 拼图后按推荐指定，尺寸是 2026-09-24 实测的原件。GRANZPRO、Office Old Crew、
#: Zerosum Production 不在表里：它们已装 X 头像，存档那张要么缺字、要么只有
#: 186×32，并排比过不如现装的。
AGENCY_WORDMARK_SOURCES: dict[str, str] = {
    "Diaz Group": "https://diaz-g.com/assets/img/diaz_logo.svg",
    "Luminous Promotion": "https://luminous-pro.tokyo/img/logo.png",
    "ONE'S DOUBLE": "https://web.archive.org/web/20220130000436id_/"
                    "https://ones-double.com/wp/assets/img/footer-logo.svg",
    # 450×450 站点图标
    "ALL PROMOTION": "https://web.archive.org/web/20221014110606id_/"
                     "https://all-p.jp/wp-content/themes/allpro/common/img/icon.png",
    # 300×300，站内上传的 X 头像
    "ARROWS": "https://web.archive.org/web/20211123045707id_/"
              "https://arrowsweb.net/wp-content/uploads/2020/08/HQ60jaia_400x400-300x300.jpg",
    # 180×180 apple-touch-icon
    "Wish": "https://web.archive.org/web/20220814134735id_/"
            "https://wish-promotion.jp/favicon/apple-touch-icon-180x180.png",
    # 883×568
    "Fourty Four Management":
        "https://web.archive.org/web/20220313105746id_/https://i0.wp.com/management.ffgh.jp"
        "/wp/wp-content/uploads/2017/01/e136d1b4d7acb7fa49e166d6012814e6.png?fit=883%2C568",
    # 154×50 字标
    "Five Promotion": "https://web.archive.org/web/20160117041833id_/"
                      "http://5promotion.com/img/menu-company2-logo_sp.png",
    # 216×60 字标
    "AINA": "https://web.archive.org/web/20130421093131id_/http://www.aina.co.jp/img/rogo-2.png",
    # 270×270 站点图标
    "AI Production": "https://web.archive.org/web/20180903004018id_/"
                     "http://ai-p.love/wp/wp-content/uploads/2018/03/cropped-ai_R-270x270.png",
    # 250×145 字标
    "Ys Promotion": "https://web.archive.org/web/20130423061648id_/"
                    "http://www.ys-promotion.jp/images/base/top.jpg",
    # 145×90
    "Sun Production": "https://web.archive.org/web/20150101194914id_/http://sunpro.tv/media/43.png",
    # 600×611
    "New Gate": "https://web.archive.org/web/20150416061609id_/"
                "http://newgate.ne.jp/images/newgate_logo2.jpg",
    # 274×112 字标
    "Z-Earth Entertainment": "https://web.archive.org/web/20160308205119id_/"
                             "http://z-earth2.crusegroup.net/images/logo.gif",
}


def safe_name(studio: str) -> str:
    """和 `PreviewService.logo` 同一套文件名规则，两边必须一致。"""
    return re.sub(r"[^\w-]", "_", studio, flags=re.UNICODE)[:60]


#: `LOGO_SOURCES` 按文件名归一后的同一张表：目标集是按 safe 归拢的，没有链接的厂牌
#: 拿不到 canonical_name，只能按这个键找。
LOGO_SOURCES_BY_SAFE = {safe_name(name): url for name, url in LOGO_SOURCES.items()}

WORDMARK_SOURCES_BY_SAFE = {safe_name(name): url
                            for name, url in WORDMARK_SOURCES.items()}

AGENCY_WORDMARK_SOURCES_BY_SAFE = {safe_name(name): url
                                   for name, url in AGENCY_WORDMARK_SOURCES.items()}

ICON_SOURCES_BY_SAFE = {safe_name(name): url for name, url in ICON_SOURCES.items()}

#: 三张表的反向索引：安全文件名 → canonical_name。没有链接的厂牌拿不到别的名字，
#: 复核件上的 `studio` 列只能从这里取；`safe.replace("_", " ")` 对日文名会还原成一排
#: 下划线，那一列就认不出是谁了。
LOGO_SOURCE_NAMES = {safe_name(name): name
                     for name in (*LOGO_SOURCES, *WORDMARK_SOURCES, *ICON_SOURCES)}


def padded_studios(logo_root: Path) -> dict[str, dict[str, object]]:
    """已安装图里哪些是补白过的字标。

    判据是 `normalize_studio_logos.py` 当时留下的边车，不是现在的像素比例——补白之后
    每一张都是方的，从成品上再也看不出源图是不是条状。
    """
    found: dict[str, dict[str, object]] = {}
    for sidecar in sorted(logo_root.glob("*.img.normalization.json")):
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("action") != "pad-to-square":
            continue
        safe = sidecar.name[: -len(".img.normalization.json")]
        found[safe] = {"width": data.get("original_width", ""),
                       "height": data.get("original_height", "")}
    return found


def small_installed_marks(logo_root: Path) -> dict[str, dict[str, str]]:
    """已装的方标里短边不够小圆片用的那些：safe → 量到的文件名与尺寸。

    判据是文件的短边，不是边车。这些图各来自不同的一趟（`DorcelClub.img` 是取图标那趟
    装的、`HEYZO.icon.img` 是补方标那趟装的），共同点只有短边不够 `INSTALLED_SHORT_EDGE`。
    小位取的是 `<safe>.icon.img`，没有那一份才回落到 `<safe>.img`，所以量的也是这个
    顺序，见 `previews.Previews.logo`；`<safe>.logo.img` 归大位，不参与。
    """
    marks: dict[str, dict[str, Path]] = {}
    for path in sorted(logo_root.glob("*.img")):
        safe, _, variant = path.name[: -len(".img")].partition(".")
        if variant in ("", ICON):
            marks.setdefault(safe, {})[variant or "base"] = path
    small: dict[str, dict[str, str]] = {}
    for safe, found in sorted(marks.items()):
        path = found.get(ICON) or found.get("base")
        if path is None:
            continue
        try:
            size = images.measure_image_size(path.read_bytes())
        except OSError:
            continue
        if size is None or min(size) >= INSTALLED_SHORT_EDGE:
            continue
        small[safe] = {"original_size": f"{size[0]}x{size[1]}", "installed": path.name}
    return small


def installed_source(logo_root: Path, safe: str, variant: str) -> str | None:
    """`<safe>.<variant>.img` 是从哪个地址装的；没有边车或边车没记就返回 None。"""
    sidecar = logo_root / f"{safe}.{variant}.img.provenance.json"
    try:
        record = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    url = record.get("source_url")
    return url if isinstance(url, str) and url else None


def restated_sources(logo_root: Path) -> dict[str, dict[str, str]]:
    """指定表改了地址、装着的还是先前那一张的厂牌。

    `harvest_targets` 按「有没有 `<safe>.img`」收人，改掉表里的 URL 单靠那一条收不到：
    文件在，人就进不来，复核件上连一行都不会出现。装图时写的 provenance 边车记着这一份
    是从哪个地址装的，和表里现在写的对不上就说明指定换过了，该再取一趟。装完边车就和
    表一致，下一轮它自己退出目标集——重跑不会反复敲同一扇门。

    没有边车的不收：那些文件不是这条路装的，来源无从比对，收进来等于拿指定表去盖掉
    另一条线的成果。
    """
    wanted: dict[str, dict[str, str]] = {}
    for table, variant in ((LOGO_SOURCES_BY_SAFE, LOGO),
                           (WORDMARK_SOURCES_BY_SAFE, LOGO),
                           (ICON_SOURCES_BY_SAFE, ICON)):
        for safe, url in table.items():
            if installed_source(logo_root, safe, variant) in (None, url):
                continue
            path = logo_root / f"{safe}.{variant}.img"
            try:
                size = images.measure_image_size(path.read_bytes())
            except OSError:
                size = None
            wanted[safe] = {
                "original_size": f"{size[0]}x{size[1]}" if size else "",
                "installed": path.name}
    return wanted


def harvest_targets(padded: dict[str, dict[str, object]],
                    links: dict[str, list[dict[str, str]]],
                    logo_root: Path) -> dict[str, dict[str, str]]:
    """要补标识的厂牌：补白过的 ∪ 装着的方标太小的 ∪ 一张图都没有、但有链接或有指定
    logo 来源的。

    后两半不在补白名单里——`normalize_studio_logos.py` 从来没处理过它们，因为没有可处理
    的文件。可它们在页面上占的位置和别人一样，两个变体都是空的。
    只看补白名单等于承认「没图的就一直没图」。

    装着的方标太小同样是入场理由：那一位的图取回来过，只是取小了，而站点上还挂着更大
    的一份，见 `small_installed_marks`。落盘另有一道 `_shorter_than_installed` 守卫，
    所以再问一趟只可能换上更大的，问不到更大的就在复核件上留一行判词。

    指定 logo 来源自己就是入场理由。jae.tokyo 那批在账本里没有任何链接，按「有链接」
    收目标一条都收不到，而这一位的图早就指好了在哪。`WORDMARK_SOURCES` 同理：那些厂牌
    连链接带图一样都没有，图的位置也已经指好了。

    指定表改了地址是第四条入场理由（`restated_sources`）：那些厂牌图都装着，前三条
    一条都收不到它们。
    """
    targets: dict[str, dict[str, str]] = {}
    for safe, original in padded.items():
        width, height = original.get("width"), original.get("height")
        targets[safe] = {
            "original_size": f"{width}x{height}" if width and height else "",
            "installed": f"{safe}.img"}
    for safe, measured in small_installed_marks(logo_root).items():
        targets.setdefault(safe, dict(measured))
    for safe, measured in restated_sources(logo_root).items():
        targets.setdefault(safe, dict(measured))
    for safe in list(links) + list(LOGO_SOURCES_BY_SAFE) + list(WORDMARK_SOURCES_BY_SAFE):
        if safe in targets or (logo_root / f"{safe}.img").exists():
            continue
        # `original_size` 留空：没有原图，写 `x` 或 `0x0` 会被当成量到的尺寸。
        targets[safe] = {"original_size": "", "installed": ""}
    return targets


#: 能拿来找图标的链接类型。`social` 不在内：那是另一条线的头像，混进来会把厂牌小标
#: 换成运营的自拍。`catalog` 在内，因为发行平台（FC2-PPV、myfans 这类）按
#: `docs/SOURCING.md` 的判据不登记 official——它们不是厂牌，没有厂牌官网——可它们照样
#: 占着筛选片和卡片徽标那几个位置，需要一枚图标。只认 official 的话，这些实体会在复核件上
#: 记成「无官网链接」，看起来像是漏采，其实是查都没查。
ICON_LINK_KINDS = ("official", "catalog")

#: 一条官网都没有时才走的兜底。刚从 LIGHT 拆出来的 EST 只有 `https://x.com/EST_prod`，
#: 官网还没建，按上面那条它会永远停在「无官网链接」——一家真实存在、账本里有 3 位女优
#: 的事务所，页面上却连一枚标识都没有。有 official／catalog 的一概不走这条：账本里
#: 453 条社媒绝大多数挂在艺人身上，混进厂牌小标就成了运营的自拍。
FALLBACK_LINK_KIND = "social"


def studio_links(connection: sqlite3.Connection,
                 kind: str = "studio") -> dict[str, list[dict[str, str]]]:
    """按 safe 文件名归拢这类实体的站点链接；社媒只在没有官网时顶上。

    `kind` 是这一趟的实体种类。事务所和厂牌在这里没有区别：都是有官网、要在页面上
    顶一枚标识的公司，标识落盘按名字（`logo_key`）而不按种类，取图那条链也是同一条。
    """
    kinds = (*ICON_LINK_KINDS, FALLBACK_LINK_KIND)
    placeholders = ",".join("?" * len(kinds))
    rows = connection.execute(
        "SELECT e.id, e.canonical_name, l.link_kind, l.url"
        " FROM entity e JOIN entity_link l ON l.entity_id = e.id"
        f" WHERE e.kind = ? AND l.link_kind IN ({placeholders})"
        " ORDER BY e.canonical_name, l.id", (kind, *kinds)).fetchall()
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        entries = grouped.setdefault(safe_name(row["canonical_name"]), [])
        # 存档快照上取到的是存档站自己的图标，这条链接不当图源；公司照样留在名单里，
        # 关门的公司官网只剩这一份，指定字标还要按名字去取。
        if is_archive(row["url"]):
            continue
        entries.append(
            {"entity_id": row["id"], "studio": row["canonical_name"],
             "link_kind": row["link_kind"], "url": row["url"]})
    return {safe: [entry for entry in entries if entry["link_kind"] in ICON_LINK_KINDS]
            or entries
            for safe, entries in grouped.items()}


class Fetcher:
    """注入给 `site_icons.discover` 的取数闭包，顺带限流，并记下取回了几份。

    `best_mark` 返回 None 有两种完全不同的原因：一份都没取回来（站点不可达），
    和取回来了但都被闸门退掉（确实只有字标）。前者是**未取得**，下一步是换个时间
    或换条链接再试；后者是结论，下一步是人工找图。只看返回值分不出来，所以在这里数。
    """

    def __init__(self, client: httpx.Client, timeout: float, interval: float,
                 retries: int = 2, backoff: float = 2.0):
        self.client = client
        self.transport = HttpxTransport(client)
        self.timeout = timeout
        self.interval = interval
        self.retries = retries
        self.backoff = backoff
        self.fetched = 0
        self.retried = 0
        self._limiter = RateLimiter(interval)
        #: 同一份首页这一趟会被要三次：`site_icons` 找声明、这边找 header 的 `<img>`、
        #: 再找页面上的 X 账号。缓存把它压回一次，省的是站点的带宽和这一轮的墙上时间。
        self._cache: dict[str, tuple[bytes, str] | None] = {}

    def __call__(self, target: str):
        if target in self._cache:
            cached = self._cache[target]
            if cached is not None:
                self.fetched += 1
            return cached
        got = self._get(target)
        self._cache[target] = got
        return got

    def _get(self, target: str):
        for attempt in range(self.retries + 1):
            self._limiter.wait()
            try:
                response = self.transport(HttpRequest("GET", target, {"User-Agent": USER_AGENT}),
                                          self.timeout, 16 * 1024 * 1024)
            except (OSError, httpx.HTTPError):
                # 和 `page_cache.Site` 同一条规矩：这些站的 TLS 大约三次断一次，重试即成。
                # 上一轮 Fitch、Idea Pocket、Wanz Factory 都取到了，这一轮四个全断，
                # 一次失败就写「未取得」会把纯抖动记成结论。
                if attempt >= self.retries:
                    return None
                self.retried += 1
                time.sleep(self.backoff * (attempt + 1))
                continue
            # 状态码不重试：404 重试三次还是 404。
            if response.status != 200 or not response.body or len(response.body) > 16 * 1024 * 1024:
                return None
            self.fetched += 1
            return response.body, response.headers.get("content-type", "")
        return None


def _as_png(image) -> bytes:
    """原样保留像素，只统一成 PNG。.ico 里可能有多帧，浏览器挑哪一帧不归我们管。"""
    buffer = io.BytesIO()
    image.convert("RGBA").save(buffer, format="PNG")
    return buffer.getvalue()


class SquareMark:
    """一份图标字节 → 一枚方形小标，同时留下每次退回的理由和退回的字标本体。

    `best_mark` 只把结果传回来，退回的原因就地丢失了。可这几种退回的下一步完全不同：
    「还是条字标」现在是可装的回落，「站上只有 16×16」是去找更大的资产。所以在这里记。

    原样保留像素，不放大：存 128 会把 32×32 插值成一团，而这份图最终只显示在 28px。
    """

    def __init__(self, faces=None):
        #: 判「这是不是一张照片」的闸。传 None 就是不设这道闸——用户指定的来源走那条路。
        self.faces = faces
        self.reasons: list[str] = []
        #: 因为检出人脸退回的候选。和 `reasons` 分开记：那几条说的是「不够格」，
        #: 这一条说的是「找错东西了」，下一步不一样。
        self.portraits: list[str] = []
        self.size = ""
        #: 通过的那一份的内容比。1.0 是正方的标识，越接近 2.2 越可能是一条字标
        #: 侥幸压线——复核时这个数比看文件名有用得多。
        self.aspect = ""
        #: 退回过的第一份「够大的宽扁字标」。方标一个都没做成时它就是回落，由调用方
        #: 补白成方图。留第一份而不是最大的一份：`best_mark` 的遍历顺序已经是
        #: 「覆盖表 → 声明 → 根路径猜测」，第一份就是优先级最高的那一份。
        self.wordmark: bytes | None = None
        self.wordmark_size = ""
        self.wordmark_aspect = ""
        #: 那份字标是从哪个地址取回来的。`site_icons.best_mark` 不说它试的是哪一个，
        #: 所以只有走 `first_mark` 那条路时填得上，复核件上空着就是「随官网那条链接」。
        self.wordmark_url = ""

    def __call__(self, data: bytes, size: int = 0, content_type: str = "") -> bytes | None:
        image = link_marks.decode(data, content_type)
        if image is None:
            self.reasons.append("解不开")
            return None
        aspect = link_marks.content_aspect(image)
        if aspect == 0.0 or aspect > link_marks.MAX_CONTENT_ASPECT:
            self.reasons.append(f"内容比 {aspect:.2f} 是字标")
            if aspect > 0.0 and min(image.size) >= MIN_SHORT_EDGE and self.wordmark is None:
                self.wordmark = _as_png(image)
                self.wordmark_size = f"{image.size[0]}x{image.size[1]}"
                self.wordmark_aspect = f"{aspect:.2f}"
            return None
        if min(image.size) < MIN_SHORT_EDGE:
            self.reasons.append(f"只有 {image.size[0]}x{image.size[1]}")
            return None
        payload = _as_png(image)
        face = self.faces(payload) if self.faces is not None else None
        if face is not None:
            self.portraits.append(
                f"{image.size[0]}x{image.size[1]} 里检出一张脸（{face.score}）")
            return None
        self.size = f"{image.size[0]}x{image.size[1]}"
        self.aspect = f"{aspect:.2f}"
        return payload


class FaceGate:
    """一枚候选里有没有人脸。有就不是标识，是照片。

    模型是按需构造的：这一趟一个候选都没走到闸门时不必去下 232 KB 的 ONNX。取不到
    模型也不让整轮停下——那会把「今天没网」变成「所有站点都没有标识」——但要把原因记进
    统计，让复核的人知道这一轮的照片没人拦。
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.unavailable = ""
        self.rejected = 0
        self._detector = None

    def __call__(self, payload: bytes):
        if not self.enabled or self.unavailable:
            return None
        if self._detector is None:
            try:
                self._detector = face_detect.FaceDetector()
            except (face_detect.FaceModelUnavailable, ImportError) as error:
                self.unavailable = str(error)
                return None
        face = face_detect.shows_a_face(payload, self._detector)
        if face is not None:
            self.rejected += 1
        return face


def site_assets(url: str, fetch) -> tuple[list[str], list[str]]:
    """站点首页上自己挂着的标识资产：(header 里的 `<img>`, 页面上的 X 账号)。

    两样从同一份 HTML 里读，因为它们本来就在同一页上，分两次取只是多敲人家一次门。
    """
    got = fetch(url)
    if got is None:
        return [], []
    html = got[0].decode("utf-8", "replace")
    return site_logos.logo_images(html, url), site_logos.x_profiles(html, url)


def avatar_urls(profile: str, fetch) -> list[str]:
    """X 账号主页 → 头像候选，从上传原图退到缩略图。"""
    got = fetch(profile)
    if got is None:
        return []
    return site_logos.avatar_tiers(got[0].decode("utf-8", "replace"))


def first_mark(urls: list[str], fetch, policy: "SquareMark") -> tuple[bytes, str] | None:
    """按顺序取这几个地址，返回第一枚过闸的方标和它的来源地址。

    过不了闸的照样留在 `policy` 里：宽扁字标进 `wordmark`，人像进 `portraits`，
    复核件上要看得见每一份被退回的理由。
    """
    for url in urls:
        got = fetch(url)
        if got is None:
            continue
        held = policy.wordmark
        made = policy(got[0], content_type=got[1])
        if made:
            return made, url
        if policy.wordmark is not None and held is None:
            policy.wordmark_url = url
    return None


def named_avatars(path: Path) -> dict[str, str]:
    """`{账本名: 头像地址}`；文件不在就是空的。

    Instagram 的头像地址只有登录态才拿得到：登出页的 `og:image` 是空的，
    `web_profile_info` 接口回 429（2026-09-05 实测）。所以这一份由人在登录的浏览器里
    解出来写进文件，脚本只管取字节、过闸、和别的来源比大小。地址带签名会过期，
    隔一段时间要重解一次；过期了就是取不回来，不会拿别的图顶替。

    键用账本里的规范名而不是 handle：判断「这个账号是不是这家公司的」需要人看一眼，
    krone 官号叫 `krone_official__`，而 `miyu_krone` 是艺人的号，形状上分不开。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found: dict[str, str] = {}
    for name, item in (data or {}).items():
        url = item.get("url") if isinstance(item, dict) else item
        if isinstance(url, str) and url.startswith("http"):
            found[str(name)] = url
    return found


def _enough(found: list[tuple[bytes, str, str, str, str]]) -> bool:
    return any(_short_edge(item[3]) >= GOOD_ENOUGH_SHORT_EDGE for item in found)


def _best(found: list[tuple[bytes, str, str, str, str]]
          ) -> tuple[bytes, str, str, str, str]:
    """这一趟收到的候选里小位用哪一枚：来源优先，除非后面有明显更大、又不更宽的。

    「更大」单看短边会挑错人。eltra.jp 声明的 `ELTRA_icon.png` 是 300×300 的皇冠，
    header 里那张 `ELTRA_logo.png` 是 800×536 的皇冠加 ELTRA 字样（内容比 1.50）。
    短边 536 比 300 大出 1.79 倍，可小位是 28 px 的方框，1.50 比的那张装进去
    只剩一行认不出的字母，而 300 那枚本来就是照着这个尺寸画的。所以宽出
    `ASPECT_SLACK` 倍以上的候选没有资格顶替，多大都不行。
    """
    limit = _aspect(found[0][4]) * ASPECT_SLACK
    fit = [item for item in found if not limit or _aspect(item[4]) <= limit]
    ranked = max(fit or found[:1], key=lambda item: _short_edge(item[3]))
    first = max(1, _short_edge(found[0][3]))
    return ranked if _short_edge(ranked[3]) >= first * BETTER_BY else found[0]


def _short_edge(size: str) -> int:
    """`"187x57"` → 57；量不到就是 0。"""
    parts = str(size).split("x")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return 0
    return min(int(part) for part in parts)


def _aspect(text: str) -> float:
    """`"1.50"` → 1.5；量不到就是 0，表示「这一枚没有理由被判宽」。"""
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


class EntityScope:
    """否决只能代表主机的候选，并记下它是哪个主机的哪一份。

    传给 `site_icons.best_mark(accept=...)`。只在链接带非根路径时才挂上：那种链接
    说明这个实体只是主机上的一条路径，主机级图标代表的是平台不是它。
    """

    def __init__(self):
        self.rejected: list[str] = []
        self._seen: set[str] = set()

    def __call__(self, candidate, data: bytes, content_type: str = "") -> bool:
        if candidate.scope == site_icons.ENTITY_SCOPE:
            return True
        digest = hashlib.sha256(data).hexdigest()
        if digest not in self._seen:
            self._seen.add(digest)
            self.rejected.append(
                f"{candidate.url} 是 {site_icons.host_key(candidate.url)} 的主机级图标"
                f"（sha256 {digest[:8]}）")
        return False


def shares_its_host(url: str) -> bool:
    """这条链接指的是主机上的一条路径，而不是整个主机吗。

    `bangbros.com/websites/BangBus` 是，`https://honnaka.jp/` 不是。是的那些不能用
    主机级候选：同主机的三个频道会拿到同一枚 sha256 完全相同的 favicon。
    """
    return bool(urlsplit(url).path.strip("/"))


def _row(safe: str, target: dict[str, str], entry: dict[str, str] | None,
         variant: str, verdict: str, **extra) -> dict[str, object]:
    row = {"entity_id": entry["entity_id"] if entry else "",
           "studio": (entry["studio"] if entry
                      else LOGO_SOURCE_NAMES.get(safe, safe.replace("_", " "))),
           "safe": safe, "variant": variant,
           "installed": target["installed"], "original_size": target["original_size"],
           "link_kind": entry["link_kind"] if entry else "",
           "url": entry["url"] if entry else "",
           "verdict": verdict, "mark_size": "", "content_aspect": "",
           "sha256": "", "candidate": "", "evidence": ""}
    row.update(extra)
    return row


def _store(candidate_dir: Path, name: str, payload: bytes) -> Path:
    candidate_dir.mkdir(parents=True, exist_ok=True)
    path = candidate_dir / name
    path.write_bytes(payload)
    return path


def _made_rows(safe: str, target: dict[str, str], entry: dict[str, str],
               policy: "SquareMark", found: list[tuple[bytes, str, str, str, str]],
               candidate_dir: Path
               ) -> tuple[dict[str, object], dict[str, object] | None]:
    """做成了方标：小位取 `_best`，大位从这一趟的收获里另挑，两位各按各的尺寸挑。

    尺寸和内容比是取到那一刻从 `policy` 记下来的，不在这里现读：大位还要接着用同一个
    `policy` 去翻站点的字标，翻完那两个字段说的就是别的图了。
    """
    chosen = _best(found)
    made, source, evidence, size, aspect = chosen
    path = _store(candidate_dir, f"{safe}.png", made)
    icon = _row(safe, target, entry, ICON, OK, url=source, mark_size=size,
                content_aspect=aspect, sha256=hashlib.sha256(made).hexdigest(),
                candidate=str(path), evidence=evidence)
    return icon, hunt_logo_row(safe, target, entry, icon, policy, chosen, found,
                               candidate_dir)


def hunt_logo_row(safe: str, target: dict[str, str], entry: dict[str, str],
                  icon: dict[str, object], policy: "SquareMark",
                  chosen: tuple[bytes, str, str, str, str],
                  found: list[tuple[bytes, str, str, str, str]],
                  candidate_dir: Path) -> dict[str, object] | None:
    """小位做成之后，大位从同一趟的收获里出，没有指定来源也不留空。

    大位要的是完整字标，所以站点自己那张字标优先——宽扁是它应有的形状，`logo` 位不过
    内容比闸门。可它常常小得没法用：bambi.ne.jp 的 `header_logo.png` 只有 187×57
    （2026-09-05 实测），短边 57 放进 160 px 的大位是一条糊字。这种时候按用户
    2026-09-05 的口径退到小位那张方标：站点自己那几张都不清楚、头像反而清楚时，
    清楚的那张两个位置都顶。两条都够不着 `MIN_LOGO_SHORT_EDGE` 就照记一行不装，
    大位会回落到 `<safe>.img`。

    站点的完整标识不一定宽到进 `policy.wordmark`：eltra.jp 那张 800×536 的皇冠加
    ELTRA 字样内容比只有 1.50，两道闸门都过，进的是 `found`。它太宽当不了小位的图标，
    却正是大位要的那一张，所以这一趟收到的最大一枚排在小位那枚前面。

    已经有 `<safe>.img` 的一律不出这一行。那张多半正是这个厂牌的完整字标（补白名单
    上的每一家都是），而 `.logo.img` 在解析顺序里排在它前面——出这一行等于拿一枚
    28 px 用的方标把大位上本来对的那张字标顶掉。空着才需要补，不空的别动。
    """
    if target["installed"]:
        return None
    made = chosen[0]
    biggest = max(found, key=lambda item: _short_edge(item[3]))
    candidates: list[tuple[bytes, str, str, str]] = []
    if policy.wordmark is not None:
        candidates.append((policy.wordmark, policy.wordmark_size, "站点自己的字标",
                           policy.wordmark_url or str(icon["url"])))
    if biggest[0] is not made:
        candidates.append((biggest[0], biggest[3], "这一趟取到的最大一枚", biggest[1]))
    candidates.append((made, str(icon["mark_size"]), "与 icon 位同一枚方标",
                       str(icon["url"])))
    for payload, size, origin, url in candidates:
        if _short_edge(size) < MIN_LOGO_SHORT_EDGE:
            continue
        plate = images.bake_square(payload)
        square = link_marks.decode(plate) if plate else None
        if square is None:
            continue
        baked = _as_png(square)
        return _row(safe, target, entry, LOGO, OK, url=url,
                    mark_size=f"{square.size[0]}x{square.size[1]}",
                    content_aspect=f"{link_marks.content_aspect(square):.2f}",
                    sha256=hashlib.sha256(baked).hexdigest(),
                    candidate=str(_store(candidate_dir, f"{safe}.logo.png", baked)),
                    evidence=f"{origin}（{size}）烤成方图")
    return _row(safe, target, entry, LOGO, TOOSMALL, url=str(icon["url"]),
                mark_size=str(icon["mark_size"]),
                evidence=f"这一趟取到的都不到 {MIN_LOGO_SHORT_EDGE}：" + "、".join(
                    f"{origin} {size}" for _, size, origin, _ in candidates))


def icon_row(safe: str, target: dict[str, str], entries: list[dict[str, str]],
             fetch, candidate_dir: Path, faces=None, avatar: str = ""
             ) -> tuple[dict[str, object], dict[str, object] | None]:
    """`icon` 位一行。一个厂牌可能挂多条链接，第一条做成就停。

    每条链接依次问四个来源：站点声明的图标、站点 header 里那张 `<img>`、人在登录态
    解出来的社媒头像（`named_avatars`）、站点挂着的 X 账号头像。四样都过同一道闸
    （方、够大、不是人像），排序即正式程度。

    **够用就停，不够用就比大小。** 声明那一枚够 `GOOD_ENOUGH_SHORT_EDGE` 就不再敲别人
    的门；到不了就把这条链接的其余来源问完，取明显更大的那枚（`_best`）。bambi 声明的
    是 119×119，而它 Instagram 的头像是 1000×1000——按「第一枚合格的就用」会把那张
    糊的装上去，页面上一眼就看得出来。

    没有官网、只登记得到社媒的公司走 `FALLBACK_LINK_KIND`：那条链接本身就是账号主页，
    直接取它的头像，不问声明的图标和 header——X 那两样对每个账号都一样。

    第二个返回值是同一趟带出来的 `logo` 行：走字标补白时是那张补白图，做成方标时由
    `hunt_logo_row` 决定，都没有就是 None。
    """
    if not entries:
        return _row(safe, target, None, ICON, SKIP,
                    evidence="数据库里这个厂牌没有能取图的链接（存档快照不算）"), None
    attempts: list[str] = []
    reachable = False
    policy = SquareMark(faces)
    scope = EntityScope()
    for entry in entries:
        before = getattr(fetch, "fetched", 0)
        found: list[tuple[bytes, str, str, str, str]] = []
        if entry["link_kind"] == FALLBACK_LINK_KIND:
            got = first_mark(avatar_urls(entry["url"], fetch), fetch, policy)
            if got:
                found.append((got[0], got[1], "数据库里登记的社媒头像",
                              policy.size, policy.aspect))
            if found:
                return _made_rows(safe, target, entry, policy, found, candidate_dir)
            reachable = reachable or getattr(fetch, "fetched", 0) > before
            attempts.append(entry["url"])
            continue
        made = site_icons.best_mark(
            entry["url"], fetch, policy,
            accept=scope if shares_its_host(entry["url"]) else None)
        if made:
            found.append((made, entry["url"], "官网声明的图标", policy.size, policy.aspect))
        # 声明那一枚够大、大位又已经有图时才免掉首页那一趟：SO MODEL AGENT 声明的
        # favicon 只有 114×114，而 header 里挂着 600×150 的完整字标，那才是大位要的。
        if not _enough(found) or not target["installed"]:
            marks, profiles = site_assets(entry["url"], fetch)
            own = first_mark(marks, fetch, policy)
            if own:
                found.append((own[0], own[1], "站点 header 里的 <img>",
                              policy.size, policy.aspect))
            if avatar and not _enough(found):
                got = first_mark([avatar], fetch, policy)
                if got:
                    found.append((got[0], got[1], "人指定的社媒头像",
                                  policy.size, policy.aspect))
            for profile in profiles:
                if _enough(found):
                    break
                got = first_mark(avatar_urls(profile, fetch), fetch, policy)
                if got:
                    found.append((got[0], got[1], f"{profile} 的头像",
                                  policy.size, policy.aspect))
        if found:
            return _made_rows(safe, target, entry, policy, found, candidate_dir)
        reachable = reachable or getattr(fetch, "fetched", 0) > before
        attempts.append(entry["url"])

    entry = entries[0]
    tried = "、".join(attempts)
    plate = images.bake_square(policy.wordmark) if policy.wordmark else None
    if plate:
        # 用户 2026-09-03 的口径：不是 icon 也可以装 icon，尽量不要落入无图。
        side = link_marks.decode(plate)
        path = _store(candidate_dir, f"{safe}.png", plate)
        icon = _row(safe, target, entry, ICON, PADDED,
                    mark_size=f"{side.size[0]}x{side.size[1]}" if side else "",
                    content_aspect=policy.wordmark_aspect,
                    sha256=hashlib.sha256(plate).hexdigest(), candidate=str(path),
                    evidence=(f"方形标识未取得，装的是字标：{policy.wordmark_size}"
                              f"／内容比 {policy.wordmark_aspect}，烤成方图"))
        # 不出这一行，`logo` 位会回落到 `<safe>.img`——BangBus 的那一份是母品牌
        # BANGBROS，小位对了大位却挂着别家的牌子。
        logo_path = _store(candidate_dir, f"{safe}.logo.png", plate)
        logo = _row(safe, target, entry, LOGO, OK,
                    mark_size=icon["mark_size"], content_aspect=policy.wordmark_aspect,
                    sha256=icon["sha256"], candidate=str(logo_path),
                    evidence=(f"与 icon 位同一份方图（原字标 {policy.wordmark_size}）"))
        return icon, logo
    if not reachable:
        verdict, evidence = MISSING, f"试过 {tried}，一份字节都没取回来"
    elif policy.portraits and not policy.reasons:
        # 站上那几张都是照片。下一步是人工指一张标识，不是换个时间再抓。
        verdict = PORTRAIT
        evidence = f"试过 {tried}：" + "、".join(policy.portraits)
    elif scope.rejected and not policy.reasons:
        # 取到了、也够清楚，但那是平台模板的通用图标，和这个实体无关。
        verdict = SHARED
        evidence = f"试过 {tried}：" + "；".join(scope.rejected)
    elif policy.reasons and all("只有 " in reason for reason in policy.reasons):
        # 是方标，只是站上没有够大的那一份。下一步是找更大的资产，不是放弃。
        verdict, evidence = TOOSMALL, f"试过 {tried}：" + "、".join(policy.reasons)
    else:
        verdict = WORDMARK
        evidence = f"试过 {tried}：" + "、".join(
            policy.reasons + policy.portraits + scope.rejected or ["没有候选"])
    return _row(safe, target, entry, ICON, verdict, evidence=evidence), None


def logo_row(safe: str, target: dict[str, str], entries: list[dict[str, str]],
             fetch, candidate_dir: Path) -> dict[str, object] | None:
    """`logo` 位一行；这个厂牌没有指定来源就返回 None。

    这一位不过内容比闸门——大位要的本来就是完整字标，宽扁是它应有的形状。
    只验「是图」和「够大」。
    """
    url = LOGO_SOURCES_BY_SAFE.get(safe)
    if not url:
        return None
    entry = dict(entries[0], link_kind="logo-source", url=url) if entries else None
    if entry is None:
        entry = {"entity_id": "", "studio": safe.replace("_", " "),
                 "link_kind": "logo-source", "url": url}
    got = fetch(url)
    if got is None:
        return _row(safe, target, entry, LOGO, MISSING,
                    evidence="指定的 logo 来源一份字节都没取回来")
    image = link_marks.decode(got[0], got[1])
    if image is None:
        return _row(safe, target, entry, LOGO, MISSING, evidence="指定的 logo 来源解不开")
    if min(image.size) < MIN_LOGO_SHORT_EDGE:
        return _row(safe, target, entry, LOGO, TOOSMALL,
                    mark_size=f"{image.size[0]}x{image.size[1]}",
                    evidence=f"短边 {min(image.size)} < {MIN_LOGO_SHORT_EDGE}")
    payload = _as_png(image)
    path = _store(candidate_dir, f"{safe}.logo.png", payload)
    return _row(safe, target, entry, LOGO, OK,
                mark_size=f"{image.size[0]}x{image.size[1]}",
                content_aspect=f"{link_marks.content_aspect(image):.2f}",
                sha256=hashlib.sha256(payload).hexdigest(), candidate=str(path),
                evidence="用户指定的 logo 来源")


def icon_from_logo(safe: str, target: dict[str, str], logo: dict[str, object],
                   candidate_dir: Path) -> dict[str, object]:
    """指定 logo 来源做成了、小位却还空着时，小位从同一张烤。

    jae.tokyo 那 26 家在账本里连一条链接都没有，`icon_row` 走不到发现流程，只会记一行
    「无官网链接」，然后筛选片和卡片徽标继续空着。可大位这张已经是名录里厂牌自己交的
    标识资产，比任何 favicon 都准。方的直接顶上，宽扁的补白成方图——和字标补白同一条
    口径，判词也照用 `字标补白`，区别只在源图来自指定来源而不是官网。
    """
    payload = images.bake_square(Path(str(logo["candidate"])).read_bytes())
    image = link_marks.decode(payload) if payload else None
    if payload is None or image is None:
        return _row(safe, target, None, ICON, MISSING,
                    url=str(logo["url"]), link_kind="logo-source",
                    evidence="指定的 logo 来源烤不成方图")
    # 判「补没补白」看源图自己的长宽，不看内容比：`images.MAX_ASPECT` 正是 `bake_square`
    # 把长条补成方图的那条线。方图也可能被重新摆位，但那不是补白。名录里的 jpg 是整幅
    # 不透明的，内容比对它一律回 0，拿那个数分不出方图和长条。
    width, height = (int(value) for value in str(logo["mark_size"]).split("x"))
    wide = max(width, height) / min(width, height) > images.MAX_ASPECT
    aspect = link_marks.content_aspect(image)
    path = _store(candidate_dir, f"{safe}.png", payload)
    return _row(safe, target, None, ICON, PADDED if wide else OK,
                url=str(logo["url"]), link_kind="logo-source",
                mark_size=f"{image.size[0]}x{image.size[1]}",
                content_aspect=f"{aspect:.2f}",
                sha256=hashlib.sha256(payload).hexdigest(), candidate=str(path),
                evidence=f'与 logo 位同一份指定来源（{logo["mark_size"]}）烤成方图')


def wordmark_source_rows(safe: str, target: dict[str, str], fetch, candidate_dir: Path,
                         table: dict[str, str] | None = None
                         ) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    """指定字标来源的 `icon` + `logo` 两行；这个厂牌没有指定字标来源就返回 `(None, None)`。

    宽扁字标只有烤成方图这一条用法，两个位置装的是同一张——和官网字标补白同一条口径
    （`icon_row` 的补白分支），区别只在源图来自名录而不是站点自己的 `<img>`。
    失败也出行：复核件不丢失败记录，`--install` 只认带候选文件的行。
    `table` 缺省是厂牌那张；事务所那一趟传 `AGENCY_WORDMARK_SOURCES_BY_SAFE`。
    """
    url = (WORDMARK_SOURCES_BY_SAFE if table is None else table).get(safe)
    if not url:
        return None, None
    kind = "wordmark-source"
    got = fetch(url)
    if got is None:
        return _row(safe, target, None, ICON, MISSING, url=url, link_kind=kind,
                    evidence="指定的字标来源一份字节都没取回来"), None
    source = link_marks.decode(got[0], got[1])
    if source is None:
        return _row(safe, target, None, ICON, MISSING, url=url, link_kind=kind,
                    evidence="指定的字标来源解不开"), None
    if min(source.size) < MIN_SHORT_EDGE:
        return _row(safe, target, None, ICON, TOOSMALL, url=url, link_kind=kind,
                    mark_size=f"{source.size[0]}x{source.size[1]}",
                    evidence=f"短边 {min(source.size)} < {MIN_SHORT_EDGE}"), None
    plate = images.bake_square(_as_png(source))
    square = link_marks.decode(plate) if plate else None
    if square is None:
        return _row(safe, target, None, ICON, MISSING, url=url, link_kind=kind,
                    mark_size=f"{source.size[0]}x{source.size[1]}",
                    evidence="指定的字标来源烤不成方图"), None
    aspect = f"{link_marks.content_aspect(square):.2f}"
    payload = _as_png(square)
    digest = hashlib.sha256(payload).hexdigest()
    size = f"{square.size[0]}x{square.size[1]}"
    origin = f"{source.size[0]}x{source.size[1]}"
    icon = _row(safe, target, None, ICON, PADDED, url=url, link_kind=kind,
                mark_size=size, content_aspect=aspect, sha256=digest,
                candidate=str(_store(candidate_dir, f"{safe}.png", payload)),
                evidence=f"指定的字标来源（{origin}）烤成方图")
    logo = _row(safe, target, None, LOGO, OK, url=url, link_kind=kind,
                mark_size=size, content_aspect=aspect, sha256=digest,
                candidate=str(_store(candidate_dir, f"{safe}.logo.png", payload)),
                evidence=f"与 icon 位同一份方图（原字标 {origin}）")
    return icon, logo


def icon_source_row(safe: str, target: dict[str, str], fetch, candidate_dir: Path
                    ) -> dict[str, object] | None:
    """指定方标来源的 `icon` 一行；这个厂牌没有指定方标来源就返回 None。

    和字标那条的区别只在出几行：这一张只管小位，大位仍由 `logo_row` 或发现流程出。
    源图里方标和字标并排时，`bake_square` 的 `refit_plate` 按内容裁一遍，落地的正是
    方标那一块——`シロウトTV` 的 200×55 落成 64×64 就是这么来的。
    """
    url = ICON_SOURCES_BY_SAFE.get(safe)
    if not url:
        return None
    kind = "icon-source"
    got = fetch(url)
    if got is None:
        return _row(safe, target, None, ICON, MISSING, url=url, link_kind=kind,
                    evidence="指定的方标来源一份字节都没取回来")
    source = link_marks.decode(got[0], got[1])
    if source is None:
        return _row(safe, target, None, ICON, MISSING, url=url, link_kind=kind,
                    evidence="指定的方标来源解不开")
    plate = images.bake_square(_as_png(source))
    square = link_marks.decode(plate) if plate else None
    if square is None:
        return _row(safe, target, None, ICON, MISSING, url=url, link_kind=kind,
                    mark_size=f"{source.size[0]}x{source.size[1]}",
                    evidence="指定的方标来源烤不成方图")
    if min(square.size) < MIN_SHORT_EDGE:
        return _row(safe, target, None, ICON, TOOSMALL, url=url, link_kind=kind,
                    mark_size=f"{square.size[0]}x{square.size[1]}",
                    evidence=f"裁出来的方标短边 {min(square.size)} < {MIN_SHORT_EDGE}")
    payload = _as_png(square)
    return _row(safe, target, None, ICON, OK, url=url, link_kind=kind,
                mark_size=f"{square.size[0]}x{square.size[1]}",
                content_aspect=f"{link_marks.content_aspect(square):.2f}",
                sha256=hashlib.sha256(payload).hexdigest(),
                candidate=str(_store(candidate_dir, f"{safe}.png", payload)),
                evidence=f"指定的方标来源（{source.size[0]}x{source.size[1]}）裁出方标")


def harvest(targets: dict[str, dict[str, str]],
            links: dict[str, list[dict[str, str]]],
            fetch, candidate_dir: Path, faces=None,
            avatars: dict[str, str] | None = None,
            wordmarks: dict[str, str] | None = None) -> list[dict[str, object]]:
    """每个目标厂牌出一行 `icon`；有指定 logo 来源或走了字标补白的再出 `logo` 行。

    有指定字标来源的厂牌只走那一条，两行都从同一张方图出（`wordmark_source_rows`）。

    有指定**方标**来源的厂牌，小位走那一条、大位照常（`icon_source_row`）：两个位置
    要的是源图里并排的不同那一块时，只有分开指才对得上。

    小位自己没做成、大位的指定来源做成了时，小位从大位那张烤（`icon_from_logo`）。

    指定来源做成时只有它那一行；它没做成（未取得／太小）那行照记，字标 logo 行
    跟在后面——复核表不丢失败记录，安装时只有带候选文件的行才落地。
    """
    rows: list[dict[str, object]] = []
    for safe in sorted(targets):
        target = targets[safe]
        entries = links.get(safe, [])
        marked, marked_logo = wordmark_source_rows(safe, target, fetch, candidate_dir,
                                                   wordmarks)
        if marked is not None:
            # 指定字标来源就是这个厂牌的答案，不再去链接上碰运气：这 12 家账本里
            # 本来就一条 official／catalog 链接都没有，走发现流程只会多记一行
            # 「无官网链接」，把已经指好的那张图挤掉。
            rows.append(marked)
            if marked_logo is not None:
                rows.append(marked_logo)
            continue
        name = (entries[0]["studio"] if entries
                else LOGO_SOURCE_NAMES.get(safe, safe.replace("_", " ")))
        wordmark_logo = None
        icon = icon_source_row(safe, target, fetch, candidate_dir)
        if icon is None:
            icon, wordmark_logo = icon_row(safe, target, entries, fetch, candidate_dir,
                                           faces, (avatars or {}).get(name, ""))
        logo = logo_row(safe, target, entries, fetch, candidate_dir)
        if (logo is not None and logo["verdict"] == OK
                and icon["verdict"] not in INSTALLABLE):
            icon = icon_from_logo(safe, target, logo, candidate_dir)
        rows.append(icon)
        if logo is not None:
            rows.append(logo)
        if wordmark_logo is not None and (logo is None or logo["verdict"] != OK):
            rows.append(wordmark_logo)
    return rows


#: 三张指定来源表在复核件上的 `link_kind`。这些行的图是人指的，不受「只认更大的」
#: 那道守卫约束，理由见 `_shorter_than_installed`。
PINNED_KINDS = ("logo-source", "wordmark-source", "icon-source")


def _shorter_than_installed(destination: Path, payload: bytes) -> bool:
    """已经装着的那一份更大，这一枚就别覆盖它。

    取数抖一下这一趟就只剩声明的小图标：krone-web.jp 在 2026-09-05 那一次一份字节都没
    回来，于是 968 的方标被 253 顶掉，而复核件上那一行写的是 `ok`——看不出刚刚发生的是
    一次网络故障。装图这一步只认更大的，重跑一趟就自己修好。

    这道守卫防的是自动发现那条链的抖动，所以指定来源不过它（`PINNED_KINDS`）：那些
    地址是人逐张看过之后写进表里的，换一张小的也是想要的结果。`シロウトTV` 的小位
    正是这种——展会那份 414×414 的横条字标在 28 px 的筛选片上只剩一团糊，换上的
    64×64 方标小得多，却是那个位置该有的东西。
    """
    if not destination.exists():
        return False
    now, before = link_marks.decode(payload), link_marks.decode(destination.read_bytes())
    if now is None or before is None:
        return False
    return min(before.size) > min(now.size)


#: 命令行那一趟写进边车的来源名。后继写的是 `auto:` 开头的归属串，撤回按它认。
HARVEST_SOURCE = "studio icon harvest"


def install(rows: list[dict[str, object]], logo_root: Path, *,
            source: str = HARVEST_SOURCE, batch: str = "") -> list[str]:
    """把可装的候选落成 `<safe>.icon.img` / `<safe>.logo.img`。

    这两个都是新文件名，不覆盖也不删除现有的 `<safe>.img`。只有尚**没有**
    `<safe>.img` 的厂牌才补写一份：不带 `variant` 和认不出的 `variant` 都回落到它，
    缺了那些位置仍然 404。

    落盘的字节过 `images.bake_square`，装进去的一律是不透明方图。哈希核对针对
    候选文件本身，`installed_sha256` 记的才是真正写进目录的那一份。

    `source` 与 `batch` 原样写进边车：`scripts/revert_auto_landing.py` 按它们整批撤回。
    """
    written: list[str] = []
    logo_root.mkdir(parents=True, exist_ok=True)
    for row in rows:
        if row["verdict"] not in INSTALLABLE or not row["candidate"]:
            continue
        candidate = Path(str(row["candidate"])).read_bytes()
        if hashlib.sha256(candidate).hexdigest() != row["sha256"]:
            raise ValueError(f'候选文件与复核记录哈希不一致，拒绝安装：{row["safe"]}')
        payload = images.bake_square(candidate)
        if payload is None:
            raise ValueError(f'无法生成可用的方形图片，未安装：{row["safe"]}')
        installed_sha256 = hashlib.sha256(payload).hexdigest()
        variant = str(row.get("variant") or ICON)
        targets = [logo_root / f'{row["safe"]}.{variant}.img']
        base = logo_root / f'{row["safe"]}.img'
        if not base.exists():
            targets.append(base)
        pinned = row.get("link_kind") in PINNED_KINDS
        for destination in targets:
            if not pinned and _shorter_than_installed(destination, payload):
                continue
            staging = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}.tmp")
            staging.write_bytes(payload)
            os.replace(staging, destination)
            Path(f"{destination}.ct").write_text("image/png", encoding="utf-8")
            Path(f"{destination}.provenance.json").write_text(json.dumps({
                "source": source,
                **({"batch": batch} if batch else {}),
                "source_url": row["url"],
                "sha256": row["sha256"],
                "installed_sha256": installed_sha256,
                "variant": variant,
                "verdict": row["verdict"],
                "installed_beside": f'{row["safe"]}.img',
                "imported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "purpose": "small-surface studio mark" if variant == ICON
                           else "studio hero wordmark",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(destination.name)
    return written


def entity_links(connection: sqlite3.Connection, entity_id: int, name: str
                 ) -> list[dict[str, str]]:
    """一个厂牌自己的取图链接，取舍与 `studio_links` 相同。"""
    return [entry for entry in studio_links(connection).get(safe_name(name), [])
            if int(entry["entity_id"]) == int(entity_id)]


def land_one(name: str, entries: list[dict[str, str]], logo_root: Path,
             candidate_dir: Path, fetch, faces: "FaceGate", *, source: str,
             batch: str) -> dict[str, object]:
    """给一个厂牌走一遍取图链，可装的直接落盘（ADR-0052）。

    判据与命令行那一趟逐字相同：目标、链接、闸门、`install` 全是同一批函数，只是范围收到
    这一个实体。人像闸没生效时一张都不装——命令行那一趟有人看复核件兜底，这里没人看，
    放过去就可能把照片当标识装上。`entries` 由 `entity_links` 先读好：取图要联网，
    不该一直占着账本连接。
    """
    safe = safe_name(name)
    links = {safe: entries} if entries else {}
    targets = {key: value for key, value in harvest_targets({}, links, logo_root).items()
               if key == safe}
    if not targets:
        return {"outcome": SKIP if not entries else "已有标识", "rows": []}
    rows = harvest(targets, links, fetch, candidate_dir, faces)
    # 大位那张来自站点页头的 `<img>`，命令行那一趟不过人像闸：页头横幅放一张艺人照，
    # 复核的人一眼就看得出来。这里没人看，所以每一张要落盘的都再过一次。
    for item in rows:
        if (item["verdict"] in INSTALLABLE and item["candidate"]
                and faces(Path(str(item["candidate"])).read_bytes()) is not None):
            item.update(verdict=PORTRAIT, evidence=f"{item['evidence']}；检出人脸")
    verdicts = [str(item["verdict"]) for item in rows]
    if faces.unavailable:
        return {"outcome": f"人像闸未生效，不自动装（{faces.unavailable}）",
                "verdicts": verdicts, "rows": rows}
    written = install(rows, logo_root, source=source, batch=batch)
    return {"outcome": "已装上" if written else next(iter(verdicts), MISSING),
            "verdicts": verdicts, "installed": written, "rows": rows}
