"""编目规则：什么是番号、什么是分卷、标签属于哪一类、两条记录算不算重复。

不碰数据库、不碰 HTTP、不依赖任何 Peach 模块，是最底下那层纯策略。

文件名必须指着内容说话：这里没有一行是 web 的，而依赖它的四个模块里有三个不在
web 层——`repository`（数据层）取 `is_jav_code`，`taste_history` 取 `LENGTH_TAGS`，
`fc2_similarity` 取重复判据。数据层 import 一个叫 web 的模块，读代码的人会以为
分层反了。
"""
from __future__ import annotations

import re
from pathlib import PureWindowsPath


LENGTH_TAGS = {"短片-2分内", "中片-10分内", "长片-30分内", "超长片-30分上"}
TECH_TAGS = {
    "1080P", "720P", "4K", "2K", "2160P", "480P", "低画质", "高帧率",
    "横屏", "竖屏", "真人", "合集", "身份待确认", "R-18", "有码", "无码",
    "薄码",
}

# Hanime1 的筛选把可见标签按语义分组。Peach 只收录当前馆藏里确实存在的
# 分组；未命中的标签仍归「其他内容」，不凭名字臆造作品/角色实体。
ATTRIBUTE_TAGS = {
    "中文字幕", "内嵌字幕", "外挂字幕", "AI修复", "AI去码", "淫语", "ASMR",
    "日系同人", "游戏同人", "动漫同人", "3D动画", "VR", "60fps",
    "女性向", "原作改编",
}
RELATIONSHIP_TAGS = {
    "母子设定", "近亲", "姐弟", "师生", "同事上司", "女友", "青梅竹马",
    "情侣", "友情", "伴侣", "母亲的朋友",
}
ROLE_TAGS = {
    "素人", "网红主播", "萝莉", "痴女", "人妻", "御姐", "学生", "秘书OL",
    "女仆", "熟女", "护士", "空姐", "家庭教师", "教师",
    "探花", "男主频道", "辣妹", "千金小姐", "偶像艺人", "教练",
    # 「处男」说男方、「处女设定」说女方，来源用 `童貞` 和 `処女` 两个格子分开写，
    # 合成一个中文词就再也分不开。女方那一格叫「设定」：AV 里的処女是出道卖点或剧情，
    # 核实不了，出道之后对她本人也不再成立。
    "处男", "处女设定", "孕妇", "黑人",
    "美熟女", "微熟女", "美魔女", "变态", "跨性别",
    # 「伪娘」是男方扮女装，「跨性别」是身体本身；「正太」说男方年幼的设定，和「萝莉」对称。
    "伪娘", "正太", "外国人", "赛车女郎", "陪酒女郎", "女搜查官",
    "风俗女郎", "扶她", "母亲角色", "妹妹角色", "寡妇角色", "女医生",
    "模特", "格斗选手", "展会女郎", "讲师", "服务员", "女乘务员",
    "女儿角色", "公主角色", "宅女宅男", "老年女性角色", "老年男性角色",
    "啦啦队员", "养女角色", "女战士", "女忍者", "短裙女警", "修女",
    "播音员", "白人出演",
}
APPEARANCE_TAGS = {
    "丝袜", "制服", "OL制服", "JK制服", "美臀", "露脸", "情趣内衣", "美腿", "高跟",
    "眼镜", "洛丽塔", "苗条", "高颜值", "巨乳", "大腿", "白丝", "黑丝",
    "泳装", "美乳", "肉丝", "旗袍汉服", "恋足",
    "白虎", "双马尾", "婚纱", "裸足", "贫乳", "皮衣皮裙", "体操服",
    "兽耳兽装", "口罩遮脸", "和服浴衣", "瑜伽裤", "兔女郎", "腋", "丰满",
    "阿黑颜", "娇小", "高个", "美肌",
    # 发型三个标签一组：来源给的 `金髪・ブロンド`、`Short Hair` 和 `ツインテール`
    # 是同一档的格子。`汗湿` 和 `阿黑颜` 一样说的是身体当下的状态。
    "金发", "短发", "黑发", "汗湿", "美穴", "巨根",
    # 乳型一种一个标签，不往一个「乳系」里并：来源说到哪一级就取哪一级。大小只有
    # 「巨乳」「贫乳」两档，好看与否是「美乳」另一格。
    "瘦巨乳", "火箭乳", "尖乳", "下垂乳", "长垂乳",
    "色气乳房", "美乳晕", "大乳晕", "凸乳晕", "色气乳头", "大乳头", "美乳头",
    "凹陷乳头", "勃起乳头", "敏感乳头", "深色乳头", "长乳头", "隆胸",
    "巨臀", "美巨臀",
    # 「高颜值」说好看，「清纯」「可爱」各说一种气质，三个分开。
    "清纯", "可爱",
    "多毛", "白皙", "细腰", "火辣身材", "色气身材", "微胖", "身体柔软", "混血",
    "纹身", "大阴唇", "深色阴部", "名器", "动漫音", "娃娃音", "腹肌", "敏感体质",
    # 「明星脸」说长得像某位名人，和「高颜值」不是一回事。
    "迷你裙", "明星脸",
    "晒黑肤色", "纯欲", "湿身", "运动短裤", "制服外套", "裸体围裙",
    "猫耳", "短裙", "紧身衣", "泡泡袜", "靴子", "肥胖体型", "肌肉体型",
    "无内裤", "无胸罩", "翻白眼·失神", "蒙面", "卷发", "运动服",
}

#: 不再使用、也没有接替者的标签。`乳系`、`足系` 是文件名规则和视觉模型早期给的宽泛
#: 品味标签，来源说到的是具体乳型、腿脚或行为，并进一个粗桶等于把来源的话说粗了。
#: 账本里的存量按 `scripts/rename_retired_tags.py` 删掉。
DROPPED_TAGS = frozenset({"乳系", "足系"})
SCENE_TAGS = {
    "酒店", "浴室", "车震", "办公室", "户外露出", "线下约拍", "探花约炮",
    "教室学校", "厨房客厅", "户外", "车内", "按摩", "油压",
    # 「风俗店」比「按摩」宽：外送、店内和泡泡浴都算，来源也用 `風俗` 一个词盖住。
    # 「温泉」说的是旅程和露天池，不是家里那间浴室。
    "风俗店", "温泉", "医院", "酒会联谊",
    "公共场所", "热带度假", "旅行",
}
STORY_TAGS = {
    "角色扮演", "反差", "绿帽NTR", "调教", "泄密流出",
    "偷拍偷窥", "出轨", "强制剧情", "剧情", "捆绑", "性教育",
    "定制", "百合", "慢热前戏", "榨精",
    "女性主导", "走光", "初次无码", "初拍", "逆搭讪",
    "痴汉", "运动", "醉酒", "药物", "纪录片", "写真映像",
    # 「羞耻」是让她难为情的玩法，没到「调教」那一步；「妄想剧情」是时间停止、隐身一类现实里做不到的设定。
    "羞耻", "妄想剧情",
    "恋腿", "恋乳", "正太控", "舞蹈", "搭讪", "恶作剧", "性骚扰剧情",
    "残虐表现", "触手", "性转·女体化", "男同", "经历讲述", "傲娇",
    "萝莉角色扮演", "约会", "瑜伽·健身", "催眠剧情", "换妻换夫",
    "综艺", "历史剧", "恐怖剧情", "科幻剧情", "恶搞", "冒险剧情",
    "悬疑剧情", "心理惊悚", "复古风格", "怀旧风格",
}
POSITION_TAGS = {
    "口交", "打扫口交", "主观视角", "骑乘", "自慰", "后入", "多人", "中出内射", "足交",
    "手交", "潮吹", "打桩", "深喉", "射精", "乳交", "多女出镜",
    "颜射", "肛交", "射精特写", "3P多人", "吞精",
    "素股隔丝", "马眼", "屁眼", "直接进入", "舔阴",
    "龟头责", "口爆", "舔脚", "毒龙", "传教士", "火车便当", "双洞齐插",
    "接吻", "性玩具", "69", "颜面骑乘",
    "放尿", "局部特写", "母乳", "排泄", "拳交",
    "高潮", "饮尿", "挠痒", "脱衣", "宫颈", "导尿", "口球", "自慰辅助",
    "鼻钩", "双人性爱", "M字开腿", "站立性爱", "呻吟",
}

#: 同一件事的两个名字：左边是账本里已经存在、但不该再用的写法，右边是规范名。
#: 绝大多数来自已经关停的 Stash 导入（ADR-0021）——那套词表和 Peach 的并行长了一段
#: 时间，页面上于是同时摆着「主观视角 1797」和「POV第一视角 8」。产地关掉了，改一次
#: 名就不会再长出来；`偷窥`、`高跟鞋`、`老师` 是 genre 投影表自己产的，同批已改到
#: 右侧这几个名字上。
#:
#: 恋足与足交分开：前者是题材（`足フェチ`），后者是行为，合成一个标签就再也分不开。
#: 而「足底足指」是拼出来的复合词，「足控」是圈内说法，两个都归恋足。
#:
#: 规范名取馆藏里通行的那个写法，不取词表里先写下的那个：`合集` 有 3699 条、全部来自
#: 文件名，`混合集` 只有 313 条、全部来自 Stash，其中 270 条的资产两个名字都挂着。
RETIRED_TAGS = {
    "OL": "秘书OL",
    "POV第一视角": "主观视角",
    "女上位": "骑乘",
    "内射": "中出内射",
    "马眼尿道": "马眼",
    "NTR绿帽": "绿帽NTR",
    "有剧情": "剧情",
    "剧情演绎": "剧情",
    "偷窥": "偷拍偷窥",
    "内衣情趣": "情趣内衣",
    "高跟鞋": "高跟",
    "臀部": "美臀",
    "足控": "恋足",
    "足底足指": "恋足",
    "足部射精": "足交",
    "老师": "家庭教师",
    "混合集": "合集",
    "淫语ASMR": "淫语",
    "处女": "处女设定",
    # 胸的大小只留「巨乳」一档：来源之间 F 杯与 H 杯的边界各说各的，同一部片被一家写
    # `Big Tits`、另一家写 `Huge Tits`，两个标签在 70 部片上同时出现。「美巨乳」这类
    # 组合词读着拗口，账本里只挂了两部，一并归到大小那一档。
    "爆乳": "巨乳",
    "美巨乳": "巨乳",
    "美爆乳": "巨乳",
    "美贫乳": "贫乳",
}

_CODE_STUDIO = re.compile(r"^[A-Z]{2,8}-\d{2,5}$")
_CODE_AMATEUR = re.compile(r"^\d{3}[A-Z]{2,8}-\d{2,5}$")
#: 素人系无码番号 `MMDDYY-NNN` / `MMDDYY_NNN`。**分隔符是片商标识，属于身份的一部分**：
#: 一本道、パコパコママ、カリビアンコムPR 用 `_`，カリビアンコム 用 `-`，同一天同一序号
#: 是两部不同影片。JavDB 自己也分开保存——搜 `092415-001` 首位返回的是一本道的
#: `092415_001`，`092416-001`、`092015-001` 才是加勒比那几部。
#: 所以归一化只统一大小写，绝不把 `_` 转写成 `-`，查询也不生成另一种分隔符的变体：
#: 用错分隔符搜到的是别的片。
#: 日期式番号的六位是 `MMDDYY`，月日必须成立。
_MMDDYY = r"(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{2}"
_CODE_DATE = re.compile(rf"^{_MMDDYY}[-_]\d{{2,4}}$")
#: ムゲンエンターテインメント 的无码系列 KIRARI：序号前带一个 `S`，`MKD-S` 是 DVD 版、
#: `MKBD-S` 是 Blu-ray 版。javbus 上 `MKBD-S118`／`MKD-S118`／`MKBD-S89`／`MKD-S89`
#: 四页都认这个写法、都给封面（2026-09-25 实测）。通用的「字母-S数字」不放开：
#: 没有实证的形态放进来，素材编号和文件计数都会被当成番号。
_KIRARI_CODE = re.compile(r"^MKB?D-S\d{2,4}$")

#: Tokyo-Hot 的编号没有厂牌字母段：`n1234`／`k1234` 是本编，`red-123` 是 Red Hot 支线。
#: 规范写法取小写，不跟着别的番号大写——javbus 的作品页地址就是 `/n1234`，NeoAVDC
#: （MIT，`src/main/number/parseNumber.ts` 的
#: `TOKYOHOT_NUM_RE`）同样输出小写 `n####`。
_TOKYO_HOT_BODY = re.compile(r"^(?:([nk])(\d{3,5})|red[-_]?(\d{2,4}))$", re.I)
#: Tokyo-Hot 站名。编号本身没有字母段，只靠形态会把 `no0037_01`、`part 18` 一起认走，
#: 所以站名出现时才允许在名字中段搜编号。
_TOKYO_HOT_SITE_TOKEN = r"tokyo[-_. ]?hot"
_TOKYO_HOT_SITE = re.compile(rf"(?i)(?<![a-z0-9]){_TOKYO_HOT_SITE_TOKEN}(?![a-z0-9])")
#: 站名写在编号前面的番号写法（`TOKYO-HOT-N0762`），账本里按目录名落的 `code` 是这个形状。
_TOKYO_HOT_SITE_PREFIX = re.compile(rf"(?i)^{_TOKYO_HOT_SITE_TOKEN}[-_. ]*")
#: 名字开头就是编号的写法（`n1025_rena_yamamoto_ss_n_fhd.wmv`）。本编固定四位，
#: 放宽到三位就会把 `no0037`、`k12` 这类一起收进来。
_TOKYO_HOT_HEAD = re.compile(r"^(?:[nk]\d{4}|red[-_]\d{3})(?![0-9])", re.I)
_TOKYO_HOT_INLINE = re.compile(
    r"(?<![a-z0-9])((?:[nk]\d{3,5}|red[-_]?\d{2,4}))(?![a-z0-9])", re.I)

#: 字母段 → 搜索扩展前缀。这里只生成查询词，不断言两种编号属于同一发行。
#:
#: 这张表按账本里实测出现过的字母段登记，不凭记忆扩展。补前缀会改变发给来源的查询，
#: 猜错就是拿别人的番号去查——这正是 2026-09-02 那批误匹配的成因。新增一行之前先在
#: 账本里确认该字母段只属于这一个发行方（`\d{3}[A-Z]+` 分组后看数字前缀是否唯一）。
MAKER_NUMBER_PREFIX = {
    "ARA": "261", "ENE": "550", "FKOS": "762", "GANA": "200", "GYAN": "278",
    "HHH": "451", "INST": "413", "JAC": "390", "JNT": "390", "KAI": "308",
    "KBI": "336", "LUXU": "259", "MAAN": "300", "MFC": "435", "MIUM": "300",
    "MLA": "476", "NTK": "300", "NTR": "348", "ORETD": "230", "OTIM": "393",
    "SIMM": "345", "SUKE": "428",
}
#: 日期式番号被抹掉分隔符后剩下的纯数字串，片商无从判断。月日与 `_CODE_DATE` 同一把尺。
_DATED_COMPACT = re.compile(rf"^{_MMDDYY}\d{{2,4}}$")
_CODE_MAKER_PREFIXED = re.compile(r"^(\d{3})([A-Z]{2,8})-(\d{2,5})$")
#: 数字前缀单独保留；`h_` 是 DMM content_id 的 label 标记。
_RELEASE_ID = re.compile(r"^(\d{1,4})?([A-Z]{2,8})0*(\d{1,5})([A-Z]?)$")
# 仅登记已核验的写法关系；查询扩展表不是身份等价表。
#
# MGStage 前缀那 9 行 2026-09-25 逐组核过：MGStage 商品页与 r18dev 快照的标题、时长、
# 出演一致，封面同图，avbase 把两家店的条目归为同一作品，账本里每个字母段只挂一个厂牌。
# 同一轮判出的反例 `348NTR`／`451HHH`／`550ENE` 与 DMM 裸编号是不同厂牌的不同作品，不登记。
_RELEASE_PREFIX_ALIASES = {
    ("259", "LUXU"), ("7", "BAZX"), ("49", "HA"),
    ("230", "ORETD"), ("278", "GYAN"), ("336", "KBI"), ("345", "SIMM"), ("413", "INST"),
    ("428", "SUKE"), ("435", "MFC"), ("476", "MLA"), ("762", "FKOS"),
}
_DMM_LABEL_PREFIX = re.compile(r"^H_(?=\d)")
_MEDIA_EXTENSION = re.compile(
    r"\.(?:mp4|mkv|avi|wmv|mov|m4v|webm|ts|m2ts|mts|mpg|mpeg|flv|rm|rmvb|iso)$",
    re.I,
)
_PROMO_DOMAIN = re.compile(
    r"(?:www\.)?[a-z0-9][a-z0-9-]{1,30}\."
    r"(?:com|net|la|xyz|cc|me|top|vip|club|info|org|tv|app|co|pw|gg|cn)",
    re.I,
)
_BRACKETED_PROMO_DOMAIN = re.compile(
    r"[\[【(（]\s*" + _PROMO_DOMAIN.pattern + r"\s*[\]】)）]", re.I,
)
_EDITION_TAIL = re.compile(
    r"(?:[-_.\s]+(?:c|ch|sub|uc|u|uncen(?:sored)?|uncensored|中字|中文字幕|无码|无码破解|破解))+$",
    re.I,
)

#: 转载站与搬运渠道的域名标签（不含 TLD）。
#:
#: 这份名单拦的是一次具体误判，不是识别广告。域名剥掉 `.com` 之后就是「字母+数字」，
#: 和番号同形，于是 `normalise_code_key("HHD800")` 会补出连字符变成 `HHD-800`，
#: 一个水印域名在作品页、在 `JAV_ASSET_PREDICATE`、在 `clean_names` 的重命名提案里
#: 全都成了番号。实例 asset 31048：
#:
#:     B:\番号\_未知厂牌\HHD800\hhd800.com@ABW-132.mp4\ABW-132.mp4
#:
#: 真番号 ABW-132 就在文件名里，`code` 却是 `HHD800`。
#:
#: 形态本身分不开——真番号 `IPX219C`、`MEYD911`、`476MLA-179` 同样是字母紧贴数字，
#: 所以只能靠名单。这里每一条都有本机 ledger 的路径实证（`<label>.<tld>` 或
#: `<label>@` 水印链），`bei88` 是唯一例外：它只以 `bei88@sis001@…` 的搬运链出现，
#: 形态与 `www.98t.la@` 同类，但路径里没有 TLD。新增条目前先用
#: `scripts/audit_domain_codes.py` 在真实 ledger 上取路径证据，并确认它不撞真番号。
REPOST_SITE_LABELS = frozenset({
    "18my", "22sht", "7mmtv", "7sht", "91home", "98t", "aavv333", "bbsxv",
    "bei88", "big2048", "fuckbe", "gc2048", "hhd800", "hjd2048", "huachishe",
    "javday", "javme", "jitumi", "kfa11", "kfa33", "madoubt", "mtfdz",
    "nyap2p", "ses23", "supjav", "thz", "thzu", "u3c3", "yy2048",
})

#: 画质标记落在番号前面，剥掉才露出番号主体（`HD-abp-758` → `abp-758`）。
_QUALITY_HEAD = re.compile(r"^(?:hd|fhd|sd|uhd|4k|2160p?|1080p?|720p?)[-_. ]+", re.I)
#: 版本标记：`-C`/`-CH` 是中文字幕版，`-UC` 是无码流出，画质词也可能落在词尾。
#: token 单独列成一份，字幕 sidecar 配对要剥掉同一批后缀（`subtitles.py`）；各写一份
#: 的话，哪天多认一个版次标记，字幕就配不上那一批文件。
VERSION_TAIL_TOKENS = ("ch", "sub", "uc", "fhd", "4k", "hd", "c", "u")
VERSION_TAIL = re.compile(r"[-_. ]?(?:" + "|".join(VERSION_TAIL_TOKENS) + r")$", re.I)
#: 一本道、加勒比是「日期+序号」体系，没有字母番号主体。分隔符进捕获组，原样带出去。
_DATE_CODE = re.compile(rf"(?<!\d)({_MMDDYY})([-_])(\d{{3}})(?!\d)")
#: UUID 首段长得像番号（`DCE7230C-730E-…` 会被拆成 `DCE`+`7230`），按整串形态排除。
_UUID_LIKE = re.compile(
    r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}", re.I)
#: 目录名和文件名都可能带扩展名，图片也算：缩略图 `BNST033(2).jpg` 与正片同番号。
_ASSET_EXTENSION = re.compile(
    r"\.(?:mp4|mkv|avi|wmv|ts|mov|m4v|jpg|jpeg|png|webp)$", re.I)
#: 文件名尾部的分卷、画质和重复计数；剥掉才能和目录名对齐。
_FILE_NOISE = re.compile(
    r"(?:[-_. ]?(?:1080p?|720p?|2160p?|4k|fhd|hd|uc|sub|ch|c|u)"
    r"|[-_. ]\d{1,2}|\(\d{1,2}\)|[a-z])+$", re.I)
#: 剥掉 `_FILE_NOISE` 之后的主干还像一个完整番号：有分隔符，或序号至少三位。
_STANDS_ALONE = re.compile(r"[-_ ]|\d{3}")
#: 番号主体：可选的三位素人前缀 + 字母厂牌 + 序号。
_CODE_BODY = re.compile(r"^(?:\d{3})?[A-Za-z]{2,8}[-_. ]?\d{2,5}$")

#: DMM 自己交付的文件名：四位发行日（`0112` 是 1 月 12 日）顶在番号前面，编码写在后面。
#: 四位是判据的关键——素人系的前缀恰好三位（`259LUXU-1007`），放宽到「若干位数字」
#: 就会把它的厂牌段吃掉。编码词也逐个列出，不写成「任意后缀」：那等于允许番号后面
#: 跟任何东西，`ABP-123-鈴村あいり` 这种把演员名写在后面的名字会被当成同一形状。
_DMM_DELIVERY = re.compile(
    r"^\d{4}([A-Za-z]{2,8}\d{2,5})[-_](?:h264|h265|hhb|mhb|dmb|dm|hq|sd|hd|mhbw|dmbw)$", re.I)
#: HEYZO 官方文件名把站名写在最前面，番号散在中间：`heyzo_hd_1031_full`。中间那截是
#: 画质档（`hd`／`lt`／`sd`），不是番号的一部分。站名必须出现——只按「下划线里夹一串
#: 数字」认的话，创作者目录里的 `Banbi_20歳の…_1080` 同样满足。
_HEYZO_FILENAME = re.compile(r"(?i)(?<![A-Z0-9])heyzo[-_ ]*(?:hd|lt|sd|fhd)?[-_ ]*(\d{3,5})(?![0-9])")

#: 只说明「这是什么文件」的词，形态与番号主体一模一样，但没有一个是厂牌代号。
#
#: 画质词和转载站标识不重复登记在这里：前者归 `_QUALITY_HEAD`，后者归
#: `REPOST_SITE_LABELS`，三份名单各管一类噪声，`release_code_from_text` 依次过一遍。
#: 新增条目需核对误判证据；创作者昵称也可能撞上这个形态，不逐个加入停用名单。
CODE_BODY_STOPWORDS = frozenset({"IMG", "NO", "PART", "VID", "VIDEO"})
#: 停用词落在名字开头时，后面跟的数字串同样不是番号：`VID_20241015_071039_730.mp4` 里的
#: `071039_730` 月日都成立，光靠日期形状拦不住，只有「这是一段手机录像」能。
#: 后视断言挡住 `NOAH-101`、`VIDEOMAN-12` 这类以停用词起头的真厂牌代号。
#: 排序固定成「长的在前、同长按字母」：`frozenset` 的遍历顺序随进程变，
#: 编出来的正则每次都不一样，比对与排错就没有稳定的东西可看。
_STOPWORD_HEAD = re.compile(
    r"^(?:" + "|".join(sorted(CODE_BODY_STOPWORDS, key=lambda word: (-len(word), word)))
    + r")(?![A-Za-z])", re.I)

#: 西片按「厂牌／系列 + 发行日」命名，没有番号：`DorcelClub.24.12.02.Christy.White.XXX.1080p`、
#: `LIFESELECOTR.20.10.03.Shinaryen.My.Hot.Girlfriend`。年份两位四位都有，月日必须成立。
#:
#: 只在 token 开头认，不在 token 中间搜：账本里的
#: `E078. Redhead.Sucking.Big.Cock.And.Hard.Sex.2019.10.15` 中间搜会得到系列名 `Sex`，
#: 而这一批的真系列写在方括号里。NeoAVDC（MIT，同上）的 `WESTERN_RE` 在 token 内任意
#: 位置匹配且只认两位年份，这里收紧成 token 开头并同时认四位年份。
_WESTERN_DATE = re.compile(
    r"^([A-Za-z][A-Za-z0-9]{1,23})\.(\d{4}|\d{2})\."
    r"(0[1-9]|1[0-2])\.(0[1-9]|[12]\d|3[01])(?![0-9])")
#: 分词只按空白与各类括号切，句点留给日期本身。
_WESTERN_TOKEN_SEP = re.compile(r"[\s_@\[\]【】()（）]+")
#: FC2 商品号。`FC-2052614` 是合集包常见的短写法（`B:\MVP\FC2-Selected collection-2`
#: 下 97 个文件是这种写法），`FC` 与数字之间必须有分隔符：连写的 `FC2052614`
#: 分不清是哪一种，仍按 `FC2` 连写读。
_FC2_ID = re.compile(r"^(?:FC2(?:[-_. ]?PPV)?[-_. ]?|FC[-_])(\d{5,})$", re.I)

#: 能证明「这是一次公开发行」的实体类型。`tag` 不在其中：口味标签谁都能挂，
#: 挂上了不代表这条记录对应某个发行物。
RELEASE_EVIDENCE_KINDS = frozenset({"performer", "studio", "series"})

#: 韩国 MIB 的番号前缀（用户 2026-09-04 指定）。这些是韩国内容，不是 JAV，拿它们去查
#: javbus／javdatabase 这类 JAV 目录站，取回的一定是**别的作品**。
#:
#: 形状上它们和厂牌番号毫无区别，所以 `is_jav_code` 认得它们，`is_jav_asset` 也只要求
#: 「有发行证据」——而那份证据恰恰是刮削自己写进去的，跑一轮就自我坐实了。2026-09-02 实测
#: `B:\MVP\MIB\` 下 68 次匹配有 58 次返回的番号根本不是查询的番号，整目录的厂牌、系列、
#: 标题与厂牌可能因此被误判，不能作为已确认的发行证据。
#: `_identity_mismatch` 只拦得住来源自报了番号的那部分，剩下的照样落进候选队列。
#:
#: 所以判据前移到刮削入口：这些番号一开始就不该被拿去问 JAV 来源。
#:
#: 前缀是 MIB 演员名的缩写，不是厂牌代号——`AR` 是 Ari、`JH` 是 Juhee、`SA` 是 Suah，
#: 一个演员一个前缀，所以表会随片源增加而变长，不要指望它收敛。2026-09-04 实测账本里
#: 两字母前缀番号共 24 种 75 条，其中 21 种全部位于 `B:\MVP\MIB\` 下、目录外零条。
#:
#: **不要退化成「两字母前缀就不是 JAV」这条形状判据。** 同一批实测里的三个例外正是反例：
#: `BF-366` 在 `B:\番号\BeFree\` 下，BeFree 是真实 JAV 厂牌；`TZ-105` 的 `TZ` 来自转载站
#: 水印 `[ThZu.Cc]`，压根不是番号（`is_repost_site_label` 那条路管它）；`FC-437689` 是 FC2
#: 的变体写法。按形状一刀切会把 BeFree 的真作品一起拦掉。
#: 三字母前缀同属这套命名。2026-09-09 复查 `B:\MVP\MIB\` 下 120 条资产，这 15 种
#: 三字母前缀共 40 条，目录外同样零条。
#:
#: 其余前缀取自官网 k-mib.com 的自有作品目录（2026-09-10 快照 368 部，合作厂牌的
#: JS、STRC、SFL 等不在此列）。它们在账本里 MIB 目录外同样零条。
KOREAN_MIB_PREFIXES = frozenset({
    "WX", "AR", "JH", "CA", "IY", "JA", "MY", "SH", "HA", "MH", "DB", "JI",
    "ES", "SR", "SY", "CD", "YH", "UY", "NN", "SA", "JE", "YR",
    "CHU", "ENS", "ERI", "HAL", "HNL", "MIJ", "MIN", "SAR", "SEY", "SIA",
    "SOY", "SRN", "SUY", "SYN", "YUJ",
    "CY", "DK", "JM", "JW", "RA", "RI", "SJ", "UB", "YL",
    "AIN", "GUL", "HRI", "JIE", "KJI", "MGL", "MOI", "MOR", "SEI", "SEO",
    "XIA", "YEL", "YJI", "YNS", "YWN",
    "MOSA", "MOSY", "NOAH", "SJSK",
})
_KOREAN_MIB_CODE = re.compile(r"^([A-Z]{2,4})-\d+$")
#: MIB 文件名是「番号 + 空格 + 演员名与片名」（`YUJ-103 Ain Do you wanna be my slave`），
#: 或带站名方括号与括号注记（`[K-MIB]NOAH-101(NOAH)(1)`），整段不是纯番号，
#: `_CODE_BODY` 认不出。只对 MIB 前缀开这条路，前缀之外的仍走通用规则。
_KOREAN_MIB_FILENAME = re.compile(
    r"^(?:\[K-?MIB\]\s*)?([A-Za-z]{2,4})[-_](\d{3,4})(?=$|[\s(\[._-])", re.I)

DUPLICATE_TOLERANCE = 0.005
DUPLICATE_FLOOR_SECONDS = 15.0
#: 卷号写成 `01`、`02` 的和写成 `1`、`2` 的一样多：`MIAD573_01.wmv` 与 `_02` 是同一部
#: 片的两卷，不认前导零就各占一张卡、互相成了「同创作者的另一部作品」。`00` 不是卷号。
_PART_MARKER = re.compile(
    r"(?:^|[^a-z0-9]|(?<=\d)(?=[a-h]\.|(?:part|pt|cd|disc|disk|dvd|vol)))"
    r"(?:part|pt|cd|disc|disk|dvd|vol|fhd)?[-_ ]?(0|0?[1-9]|[1-9]\d|[a-h])"
    r"(?=\.[a-z0-9]{2,4}$)",
    re.I,
)
_CIRCLED_PART = re.compile(r"[-_ ]([①-⑳])[^.]*\.[a-z0-9]{2,4}$", re.I)
_FC2_COLLECTION_NAME = re.compile(
    r"^FC(?:2)?[-_ ]?(?:PPV[-_ ]?)?(\d{5,})(?:【[^】]*】)?(.*?)\.[a-z0-9]{2,4}$", re.I)
_BONUS_PART = re.compile(r"(?:[-_ ](?:sp|gift)[-_ ]?(\d{1,2})?|[（(]特典[）)])$", re.I)


def current_tags(names) -> list[str]:
    """按现在的词表读一串标签：退役名换成规范名，撤掉的粗桶丢掉，去重保序。"""
    return [tag for tag in dict.fromkeys(RETIRED_TAGS.get(name, name) for name in names)
            if tag not in DROPPED_TAGS]


def compact_label(value: str | None) -> str:
    """把一串标识压成「无分隔符、序号不补零」的比较形。

    `BEI88`、`BEI-088`、`bei88` 说的是同一个东西，但 `normalise_code_key` 会把前两个
    都写成 `BEI-088`。不抹掉补零，名单就只拦得住其中一种写法——而界面显示的、
    SQL 谓词里比的、脚本重命名用的恰好是补过零的那一种。
    """
    text = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    shape = re.fullmatch(r"([a-z]+)(\d+)", text)
    return f"{shape.group(1)}{int(shape.group(2))}" if shape else text


def is_repost_site_label(value: str | None) -> bool:
    """True 表示这串字符是转载站／搬运渠道的水印标识，不是番号。

    两条判据：整串就是一个域名（`hhd800.com` 原样落进 `code` 的情况），或者压成
    比较形后命中 `REPOST_SITE_LABELS`（域名被剥掉 TLD、只剩标签的情况）。
    """
    text = str(value or "").strip()
    if not text:
        return False
    if _PROMO_DOMAIN.fullmatch(text):
        return True
    return compact_label(text) in REPOST_SITE_LABELS


def tokyo_hot_code(value: str | None) -> str:
    """Tokyo-Hot 编号的规范写法；不是这一形态返回空串。

    补零到发行方自己的位数（本编四位、Red Hot 三位），大小写一律折成小写。
    形态判据与小写这一点的出处见 `_TOKYO_HOT_BODY`。

    站名写在编号前面的一并认（`TOKYO-HOT-N0762`）：账本里按目录名落的 `code` 就是这个
    写法，站名不剥掉的话它既不成番号形态，也进不了封面缓存键。
    """
    shape = _TOKYO_HOT_BODY.match(_TOKYO_HOT_SITE_PREFIX.sub("", str(value or "").strip()))
    if not shape:
        return ""
    if shape.group(3):
        return f"red-{int(shape.group(3)):03d}"
    return f"{shape.group(1).lower()}{int(shape.group(2)):04d}"


def is_code_body_stopword(value: str | None) -> bool:
    """True 表示这段文字的字母只是文件类型标记，不能当番号主体（见 `CODE_BODY_STOPWORDS`）。"""
    return re.sub(r"[^A-Za-z]", "", str(value or "")).upper() in CODE_BODY_STOPWORDS


def western_release_identity(value: str | None) -> str:
    """西片的「厂牌／系列 + 发行日」身份；不是这一形态返回空串。

    返回 `DORCELCLUB.2024-12-02` 这样的写法。它不是番号，也不会和番号撞上——番号里
    没有句点，也没有四位年份。两位年份按 70 分界展开（`69` → 2069，`70` → 1970）：
    这个语料里的西片全是 2010 年之后的，分界点取在哪都不影响现有条目，写死一个是为了
    同一个文件名永远给出同一个身份。

    同一场景在不同发布组手里会写成 `Vixen.26.05.07` 和 `Vixen.2026.05.07`，收敛成一个
    身份才能判重；这也是它进 `release_identity` 的理由。
    """
    stem = _ASSET_EXTENSION.sub("", str(value or "").strip())
    for token in _WESTERN_TOKEN_SEP.split(stem):
        shape = _WESTERN_DATE.match(token)
        if not shape:
            continue
        series, year, month, day = shape.groups()
        if len(year) == 2:
            year = f"20{year}" if int(year) < 70 else f"19{year}"
        return f"{series.upper()}.{year}-{month}-{day}"
    return ""


def normalise_code_key(code: str | None) -> str:
    """Normalize a release code into the stable cover-cache key.

    日期式番号（`_CODE_DATE`）例外：分隔符是片商标识，归一化原样保留，
    `092415_001` 与 `092415-001` 各算一个键、各存一张封面。
    Tokyo-Hot 同样例外，它的规范写法是小写（见 `_TOKYO_HOT_BODY`）。
    """
    raw = (code or "").upper().strip()
    tokyo = tokyo_hot_code(raw)
    if tokyo:
        return tokyo
    if _CODE_DATE.match(raw):
        return raw
    value = raw.replace("_", "-").replace(" ", "-").strip()
    if not value:
        return ""
    if is_repost_site_label(value):
        # 水印域名原样返回：给它补出连字符就等于凭空造了一个番号，
        # 而作品页显示的 `display_code` 正是这个返回值。
        return value
    if value.startswith("FC2"):
        digits = re.search(r"(\d{5,})", value)
        return f"FC2-PPV-{digits.group(1)}" if digits else value
    short_fc2 = _FC2_ID.match(value)
    if short_fc2:
        return f"FC2-PPV-{short_fc2.group(1)}"
    shape = re.match(r"^(\d{3})?([A-Z]+)-?(\d+)$", value)
    if not shape:
        return value
    return f"{shape.group(1) or ''}{shape.group(2)}-{int(shape.group(3)):03d}"


def is_jav_code(code: str | None) -> bool:
    """Recognize only code shapes whose original value keeps its separator.

    停用词在这里也要拦。提取器不再产出 `VIDEO-2022` 这样的值，但账本里存着 58 条
    （2026-09-12 只读盘点：`VIDEO-2022` 34 条、`VIDEO-2025` 3 条、`IMG-nnnn` 21 条），
    而 `JAV_ASSET_PREDICATE` 是通过 SQLite 自定义函数直接调本函数判的。
    """
    value = (code or "").upper().strip()
    if not value or is_repost_site_label(value) or is_code_body_stopword(value):
        return False
    if value.startswith("FC2"):
        return bool(re.search(r"\d{5,}", value))
    return bool(
        _CODE_STUDIO.match(value)
        or _CODE_AMATEUR.match(value)
        or _CODE_DATE.match(value)
        or _KIRARI_CODE.match(value)
        or tokyo_hot_code(value)
    )


def is_korean_mib_code(code: str | None) -> bool:
    """True 表示这个番号属于韩国 MIB，不适用 JAV 规则。

    只认 `<字母>-<数字>` 这一种写法下的前缀。素人系的三位数字前缀（`300MIUM-1239`）
    和 FC2 都不在此列：它们的字母段虽然可能撞上，但发行体系本来就不是 MIB。
    """
    value = normalise_code_key(code)
    shape = _KOREAN_MIB_CODE.match(value)
    return bool(shape and shape.group(1) in KOREAN_MIB_PREFIXES)


def code_query_variants(code: str | None) -> tuple[str, ...]:
    """按可用程度排序的查询写法，第一个永远是账本的规范写法。

    同一发行方在账本里两种写法都有——`259LUXU-1004` 和 `LUXU-688`、`336KBI-010`
    和 `KBI-019`，共 6 个字母段 13 个裸写法。来源站点通常只索引一种，拿另一种查会
    落空。所以先查规范写法，落空再换另一种写法。

    去前缀仅生成搜索词；补前缀只对 `MAKER_NUMBER_PREFIX` 登记过的字母段做。
    消费者必须按原始编号另行核验返回作品，不能用扩展查询词代替发行身份。

    日期式番号只回它自己：换一种分隔符搜到的是别的片商的另一部片，不是同一发行的
    另一种写法（见 `_CODE_DATE`）。
    """
    primary = normalise_code_key(code)
    if not primary:
        return ()
    if primary.startswith("FC2"):
        return (primary,)
    prefixed = _CODE_MAKER_PREFIXED.fullmatch(primary)
    if prefixed:
        return (primary, f"{prefixed.group(2)}-{prefixed.group(3)}")
    bare = _CODE_STUDIO.match(primary)
    if bare:
        letters = primary.split("-", 1)[0]
        prefix = MAKER_NUMBER_PREFIX.get(letters)
        if prefix:
            return (primary, f"{prefix}{primary}")
    return (primary,)


def release_identity(code: str | None) -> str:
    """把番号或来源返回的 id 收敛成可比对的作品身份。

    数字前缀保留作品身份，只有登记过的等价写法和显式 DMM h_ 标记可归一。
    搜索变体不证明发行相同：390JAC-040 配信与 JAC-040 DVD 合集分别保留。

    认不出形态的值原样返回，只和自己相等：`20211103_JENNIFERMENDEZ` 这种关键词
    搜索结果不该和任何番号算成同一部。
    """
    raw = _DMM_LABEL_PREFIX.sub("", str(code or "").upper().strip())
    tokyo = tokyo_hot_code(raw)
    if tokyo:
        return tokyo
    if _CODE_DATE.match(raw):
        # 日期式番号带着分隔符进身份：抹掉它就等于断言一本道的 `092415_001` 和
        # 加勒比的 `092415-001` 是同一部（见 `_CODE_DATE`）。
        return raw
    western = western_release_identity(raw)
    if western:
        # 西片不走番号体系，但同一场景的两种年份写法要收敛成一个身份。
        return western
    value = re.sub(r"[\s._\-]+", "", raw)
    if not value:
        return ""
    if value.startswith("FC2"):
        # 分隔符已经抹掉，取数字要从 `FC2` 之后开始：否则 `FC2-1812235` 会被读成
        # 一段 `21812235`，和 `FC2-PPV-1812235` 算成两部不同的作品。
        digits = re.search(r"(\d{5,})", value[3:])
        return f"FC2-{int(digits.group(1))}" if digits else value
    if _DATED_COMPACT.fullmatch(value):
        # 来源只给一串数字时分隔符无从得知，原样留着数字，别替它挑一个片商。
        # 与带分隔符那一侧的比对交给 `same_release_code`。
        return value
    shape = _RELEASE_ID.fullmatch(value)
    if shape:
        prefix, letters, number, suffix = shape.groups()
        prefix = prefix or ""
        if (prefix, letters) in _RELEASE_PREFIX_ALIASES or str(code or "").upper().strip().startswith("H_"):
            prefix = ""
        return f"{prefix}{letters}-{int(number)}"
    return value


def same_release_code(left: str | None, right: str | None) -> bool:
    """两个写法是否指同一部作品。空值不算相等——没有证据不是证据。"""
    first, second = release_identity(left), release_identity(right)
    if not first or not second:
        return False
    if first == second:
        return True
    # 日期式番号的紧凑写法（`040221001`）没带分隔符，认不出片商，两种分隔符都算命中：
    # 缺一个字符不构成「这是另一部片」的证据，拒掉只会把对的元数据一起丢了。
    compact = next((value for value in (first, second)
                    if _DATED_COMPACT.fullmatch(value)), "")
    if compact:
        dated = first if second == compact else second
        if (_CODE_DATE.match(dated)
                and re.sub(r"[-_]", "", dated) == compact):
            return True
    # FC2 的来源 id 是裸数字（`FC2-PPV-1812235` → `1812235`）。裸数字自己不是番号
    # 形态，所以这条不放进 `release_identity`：只有另一侧确实是 FC2 才按数字比。
    fc2 = next((value for value in (first, second) if value.startswith("FC2-")), "")
    other = first if second == fc2 else second
    return bool(fc2) and other.isdigit() and fc2 == f"FC2-{int(other)}"
def is_amateur_code(code: str | None) -> bool:
    """三位数字前缀的素人系番号：`259LUXU-1475`、`300MIUM-1239`。

    这类番号由 MGS 发行，不进 DMM 数字版目录，也查不到 r18.dev 与 Prestige
    官方 API。判定只看形状：一旦改成「先拿到元数据再判断」，没有元数据的番号
    就永远轮不到该问的那个来源。
    """
    return bool(_CODE_AMATEUR.match((code or "").upper().strip()))


def code_letter_stem(code: str | None) -> str:
    """番号的字母段，用来和 DMM `content_id` 对照：`ABW-232` -> `abw`。"""
    value = normalise_code_key(code)
    if not value or value.startswith("FC2"):
        return ""
    return re.sub(r"[^A-Z]", "", value.split("-", 1)[0]).lower()


#: 日本的产地码。取值集合与标签在 `regions.py`，这里只认这一个：本函数要回答的是
#: 「是不是日本发行物」，而 `regions` 反过来要用本模块的番号判据，写成 import 就成环。
JAPAN_REGION = "jp"


def is_jav_asset(code: str | None, studio: str | None = None,
                 release_date: str | None = None,
                 entity_kinds: tuple[str, ...] | list[str] = (),
                 region: str | None = None) -> bool:
    """Require release evidence in addition to a code-shaped string.

    Creator clips such as ``JI-103`` can look exactly like a studio code. They
    stay in ordinary browsing until a studio, performer, series, or release
    date ties them to a published JAV release. FC2 IDs are an explicit release
    system and do not need those projections.

    `region` 是已经判定的产地，判过且不是日本就一概不是 JAV——韩国 MIB 与国产厂牌的
    番号和日本番号同形，发行证据那几条它们全都满足，只有产地分得开。未判定（空串）
    维持原有口径：这一列绝大多数行还是空的，拿「没判过」当「不是日本」会把整个 JAV
    页清空。
    """
    if str(region or "").strip() not in ("", JAPAN_REGION):
        return False
    # 历史 ledger 里有 PBD390、IPVR00296 这类缺连字符但带片商／出演者证据的真发行物。
    # 裸 `RAIKUN325` 仍不能单凭形态升级；只有规范化后像番号且同时有发行证据才接受。
    normalized = normalise_code_key(code)
    if not is_jav_code(code) and not is_jav_code(normalized):
        return False
    value = normalized.upper().strip()
    if value.startswith("FC2"):
        return True
    return bool(
        str(studio or "").strip()
        or str(release_date or "").strip()
        or RELEASE_EVIDENCE_KINDS.intersection(entity_kinds)
    )


#: 发行体系自己的写法，形态本身就是发行证据：FC2 商品号（`FC-437689` 这种变体先经
#: `normalise_code_key` 归成 `FC2-PPV-…`）、
#: 素人平台的三位数字前缀（`300MIUM-698`）、MGStage 素人系（`STP-26232`、`SIRO-3508`）、
#: 日期式番号（`071213-625`）。创作者的文件名撞不出这几种形状，所以它们一概放行。
_RELEASE_SYSTEM_SHAPE = re.compile(r"^(FC2|\d{3}[A-Z]+-|ST[PN]-|SIRO-|\d{6}[-_])")


def scrapes_as_jav(code: str | None, studio: str | None = None,
                   creator: str | None = None, release_date: str | None = None,
                   entity_kinds: tuple[str, ...] | list[str] = (),
                   region: str | None = None) -> bool:
    """这个番号该不该拿去问 JAV 来源。

    和 `is_jav_asset` 的分工：那个判「这条记录算不算一部 JAV 发行物」，要求发行证据
    已经落在账本里，用来决定浏览时归到哪一类。刮削入口不能用它——那份证据本来就是
    刮回来的，没刮过就没证据，拿它当门会把每一部还没刮过的新片一起关在外面。

    这里只拦一种：**创作者作品的文件名被读成了番号**。`sumwall95 masturbation_1.mp4`
    出 `SUMWALL-095`、`aerith 2412a.mp4` 出 `AERITH-2412`（角色名加年月），形态上和厂牌
    番号毫无区别。判据是「归在某个创作者名下、没有厂牌也没有其他发行证据、写法又不属于
    任何发行体系」。2026-09-22 只读盘点：账本 2851 部有番号的资产里这样的 126 部，
    105 部是 `oscarkim123` 的 cosplay、19 部是 `sunwall` 的剪辑，每轮采集都拿去问一遍
    javdb；同一盘点里被放行的真番号零误伤。
    """
    if str(region or "").strip() not in ("", JAPAN_REGION):
        return False
    if not str(creator or "").strip():
        return True
    if (str(studio or "").strip() or str(release_date or "").strip()
            or RELEASE_EVIDENCE_KINDS.intersection(entity_kinds)):
        return True
    return bool(_RELEASE_SYSTEM_SHAPE.match(normalise_code_key(code).upper()))


def release_code_from_text(value: str | None) -> str | None:
    """从一段文字（目录名或文件名主干）里解析出规范番号；解析不出返回 None。

    和 `normalise_code_key` 的分工：那个只归一化「已经确认是番号」的字符串，这个负责
    判断一段文字里到底有没有番号。归一化仍然交给它，不在这里重写一遍——番号既是身份
    判定也是封面缓存键，两份实现漂移会让同一部片解析出两个键。

    `HHD800` 这类转载站标签一律返回 None：它形态上完全符合「字母+数字」，只有名单能
    把它和 `MEYD911` 分开。

    噪声分三层，顺序固定：先按名单认出整串就是水印的情况，再摘掉头尾的推广域名
    （`www.98t.la@ABW-358-U`、`[xxx.cc]ABC-123`、`ABP-762-fuckbe.com`），最后剥画质
    前缀与版次后缀。域名这一层交给 `strip_promo_markers`，番号提取和目录判重共用它，
    否则同一个 `[98t.tv][98t.tv]` 会在两处各剥一半。

    西片是「厂牌／系列 + 发行日」，不产出番号：它没有番号可给，而剥掉尾部噪声后日期会
    散架，从剩下的半截里一定能抠出一个不存在的番号来。
    """
    text = str(value or "").strip()
    if not text or _UUID_LIKE.search(text):
        return None
    text = _ASSET_EXTENSION.sub("", text)
    if is_repost_site_label(text) or western_release_identity(text):
        return None
    text = strip_promo_markers(text)
    fc2 = _FC2_ID.match(text)
    if fc2:
        return normalise_code_key(f"FC2-PPV-{fc2.group(1)}")
    tokyo = _tokyo_hot_from_text(text)
    if tokyo:
        return tokyo
    if _STOPWORD_HEAD.match(text):
        return None
    date = _DATE_CODE.search(text)
    if date:
        return f"{date.group(1)}{date.group(2)}{date.group(3)}"
    body = VERSION_TAIL.sub("", _QUALITY_HEAD.sub("", text))
    if not _CODE_BODY.match(body) or is_code_body_stopword(body):
        return None
    canonical = normalise_code_key(body)
    return canonical if is_jav_code(canonical) else None


def _tokyo_hot_from_text(text: str) -> str:
    """Tokyo-Hot 编号只在两个位置上认：名字开头，或站名已经写在名字里。"""
    head = _TOKYO_HOT_HEAD.match(text)
    if head:
        return tokyo_hot_code(head.group(0))
    if _TOKYO_HOT_SITE.search(text):
        inline = _TOKYO_HOT_INLINE.search(text)
        if inline:
            return tokyo_hot_code(inline.group(1))
    return ""


def release_code_from_filename(name: str | None) -> str | None:
    r"""文件名带分卷、画质和重复计数，剥掉噪声后再解析。

    剥完噪声剩下的主干要自己站得住：带分隔符，或序号至少三位。论坛合集包
    `[mtfdz.club]WX17.3` 摘掉域名、再把 `.3` 当分卷剥掉，只剩 `WX17`，补零才凑成
    `WX-017`；真番号的分卷（`abp762-1`、`ABW-358-2`）剥完仍是完整番号。
    """
    stem = _ASSET_EXTENSION.sub("", str(name or "").strip())
    if western_release_identity(stem):
        return None
    partless = _ASSET_EXTENSION.sub("", _PART_MARKER.sub("", str(name or "").strip()))
    stripped = _FILE_NOISE.sub("", strip_promo_markers(partless))
    code = release_code_from_text(stem) or (
        release_code_from_text(stripped) if _STANDS_ALONE.search(stripped) else None)
    if code:
        return code
    delivery = _DMM_DELIVERY.match(stem)
    if delivery:
        return normalise_code_key(delivery.group(1))
    heyzo = _HEYZO_FILENAME.search(stem)
    if heyzo:
        return normalise_code_key(f"HEYZO-{heyzo.group(1)}")
    mib = _KOREAN_MIB_FILENAME.match(stem)
    if mib and mib.group(1).upper() in KOREAN_MIB_PREFIXES:
        return normalise_code_key(f"{mib.group(1)}-{mib.group(2)}")
    return None


def _jav_code_pattern(code: str | None) -> str:
    """Return one regex fragment matching compact and separated forms of a canonical code."""
    canonical = normalise_code_key(code)
    fc2 = re.fullmatch(r"FC2-PPV-(\d+)", canonical)
    if fc2:
        return rf"FC2(?:[-_ ]?PPV)?[-_ ]*0*{re.escape(fc2.group(1))}"
    amateur = re.fullmatch(r"(\d{3})?([A-Z]+)-(\d+)", canonical)
    if amateur:
        prefix, letters, digits = amateur.groups()
        return (
            rf"{re.escape(prefix or '')}{re.escape(letters)}"
            rf"[-_ ]*0*{re.escape(str(int(digits)))}"
        )
    tokyo = _TOKYO_HOT_BODY.fullmatch(canonical)
    if tokyo:
        # 文件名里补零位数会漂移（`n646`／`n0646`），两种都放行。
        if tokyo.group(3):
            return rf"red[-_ ]*0*{int(tokyo.group(3))}"
        return rf"{tokyo.group(1)}0*{int(tokyo.group(2))}"
    dated = re.fullmatch(r"(\d{6})[-_](\d{2,4})", canonical)
    if dated:
        # 匹配文件名时两种分隔符都放行：身份认分隔符，野生文件名的写法却会漂移
        # （账本里同一部一本道既有 `1pon-092415-001-fhd1`，也有 `1pondo-092415_001-FHD`）。
        return rf"{re.escape(dated.group(1))}[-_ ]*{re.escape(dated.group(2))}"
    return ""


#: 无码片商自己的编号法：Caribbeancom／1Pondo／10musume／Pacopacomama 走日期式
#: （形状与分隔符含义见 `_CODE_DATE`，同一份实现），HEYZO 用 `HEYZO-1380`，
#: Tokyo-Hot 用 `n1234`（见 `_TOKYO_HOT_BODY`）。有码片商不用这三种形状。
UNCENSORED_CODE_SHAPES = (
    _CODE_DATE,
    re.compile(r"^HEYZO-\d{2,5}$", re.I),
    _TOKYO_HOT_BODY,
)
#: 文件名里的发行站标记。番号形状认不出来时，这是另一条本机就能核验的证据。
_UNCENSORED_SITE = re.compile(
    r"(?i)(?<![A-Z0-9])(?:"
    r"carib(?:bean(?:com)?(?:pr)?)?|1pon(?:do)?|10mu(?:sume)?|heyzo|"
    r"pacopacomama|paco|muramura|" + _TOKYO_HOT_SITE_TOKEN +
    r")(?![A-Z0-9])"
)
#: 版次标记有时和番号粘在一起，中间没有分隔符：`PPPD-937CH.mp4`、`MIDV-751CH.mp4`。
#: 只认带分隔符的写法，这些文件既拿不到「中字」徽章，番号本身还会被当标题显示。
_GLUED_EDITION = r"(?:CH|C|SUB|UC|U)"


def is_uncensored_code(code: str | None) -> bool:
    value = str(code or "").strip()
    return any(shape.fullmatch(value) for shape in UNCENSORED_CODE_SHAPES)


def code_release_date(code: str | None) -> str | None:
    """日期式番号自己写着发行日：`092415_001` 是 2015-09-24。别的形状返回 None。

    这几家片商按发行日编号，番号比任何转录站都可靠：javdb 给 `092415_001` 的是
    2016-06-16，那是转售商的上架日。
    """
    value = str(code or "").strip()
    if not _CODE_DATE.fullmatch(value):
        return None
    return f"20{value[4:6]}-{value[0:2]}-{value[2:4]}"


def is_uncensored_release(name: str | None, code: str | None) -> bool:
    """番号形状或文件名里的发行站，两者有一个成立就是无码片商的片。

    这两条都是本机可核验的证据，不依赖抓取结果——`040221-001` 这类番号在
    r18.dev 永远 404，等元数据到齐再判，徽章就永远不会出现。
    """
    return is_uncensored_code(code) or bool(_UNCENSORED_SITE.search(str(name or "")))


def jav_edition_badges(name: str | None, code: str | None,
                       tags: tuple[str, ...] | list[str] = ()) -> list[str]:
    """Project filename/tag evidence into compact edition badges beside the code."""
    text = _MEDIA_EXTENSION.sub("", str(name or ""))
    tag_set = {str(tag).strip().casefold() for tag in tags if str(tag).strip()}
    code_pattern = _jav_code_pattern(code)
    after_code = (
        re.search(
            rf"(?:^|[^A-Z0-9]){code_pattern}((?:{_GLUED_EDITION})?(?:[^A-Z0-9].*)?)$",
            text, re.I,
        )
        if code_pattern else None
    )
    suffix = after_code.group(1) if after_code and after_code.group(1) else ""
    cracked = (
        bool(re.search(r"无码\s*破解|無碼\s*破解|AI\s*去码|"
                       r"(?:^|[-_.\s\[])破解(?:$|[-_.\s\]])", text, re.I))
        or "ai去码" in tag_set or "无码破解" in tag_set
    )
    uncensored = (
        cracked
        or "无码" in tag_set
        # 无码片商的片本身就是无码，不需要文件名里另有 `-U`／`Uncen` 标记。
        or is_uncensored_release(name, code)
        # `un` 和 `u`／`uc` 是同一个意思。此前它没进这张表，`ABF-158-UN.mp4`
        # 既拿不到徽章，`UN` 又被当标题显示；现在标题判空了，不认它就等于把
        # 这条信息整个丢掉。
        or bool(re.search(r"(?:^|[-_.\s\[])"
                          r"(?:uc|un|u|uncen(?:sored)?|uncensored|无码|無碼)"
                          r"(?:$|[-_.\s\]])", suffix, re.I))
    )
    subtitled = (
        bool({"中文字幕", "内嵌字幕", "外挂字幕", "中字"}.intersection(tag_set))
        or bool(re.search(r"(?:^|[-_.\s\[])(?:c|ch|sub|中字|中文字幕)"
                          r"(?:$|[-_.\s\]])", suffix, re.I))
    )
    badges = []
    if subtitled:
        badges.append("中字")
    if cracked:
        badges.append("无码破解")
    elif uncensored:
        badges.append("无码")
    return badges


#: 头尾的裸域名不允许标签里带连字符：`ABP-762-fuckbe.com` 整串都符合「标签+.com」，
#: 按通用形态删前缀会把番号一起吃掉，只剩 `mp4`。带方括号那种由括号定界，不受此限。
_BARE_PROMO = r"(?:www\.)?[a-z0-9]{2,31}\.(?:com|net|la|xyz|cc|me|top|vip|club|info|org|tv|app|co|pw|gg|cn)"
_PROMO_PREFIX = re.compile(
    r"^(?:[\[【(（]\s*(?:" + _PROMO_DOMAIN.pattern + r")\s*[\]】)）]|(?:"
    + _BARE_PROMO + r"))[-_@.\s]*", re.I)
_PROMO_SUFFIX = re.compile(
    r"[-_@.\s]*(?:[\[【(（]\s*(?:" + _PROMO_DOMAIN.pattern + r")\s*[\]】)）]|(?:"
    + _BARE_PROMO + r"))$", re.I)


def strip_promo_markers(name: str | None) -> str:
    """摘掉名字**头尾**的推广域名标记，其余部分一个字都不动。

    只认头尾，不认名字中间。删任意位置的带括号域名会把
    `Hazel Moore - [FootFetishDaily.com] - Hardcore` 里的厂牌一起删掉——欧美片的
    `[Vixen.com]`、`[StraplessDildo.com]` 是厂牌名，不是广告，删掉是丢真信息。
    真正的广告标记全在头或尾：`[44x.me]tre-080`、`MattieDoll - pornhub.com`。

    同样刻意不做的事：不压缩多余空格、不合并空括号。做了的话，
    `(12P+5V_1.28G) [12P-5V-1.28GB]` 会变成 `(12P+5V_1.28G12P-5V-1.28GB]`，
    没有广告的 `狗链  兔尾` 也跟着被改。

    头尾各剥到不动为止，`[98t.tv][98t.tv]ABW-251` 这种叠了两层的才能剥干净。
    """
    original = str(name or "")
    text = original
    while True:
        stripped = _PROMO_SUFFIX.sub("", _PROMO_PREFIX.sub("", text, count=1), count=1)
        if stripped == text:
            break
        text = stripped
    if text == original:
        # 没摘掉任何广告就原样返回：末尾那次 strip 会把 `@9ririsuamano` 这种
        # 本来就带前缀符号的账号名改掉，而它不是广告。
        return original
    return text.strip(" ._-—@")


def promo_free_key(name: str | None) -> str:
    r"""摘广告后再抹掉大小写与分隔符，用来判断两个目录名是不是同一个名字。

    实测的冗余层是 `TRE-080\[44x.me]tre-080`：大小写不同、还挂着广告前缀，
    直接比字符串会漏掉。分隔符也一起抹掉，`TRE080` 与 `TRE-080` 才算同名。
    """
    return re.sub(r"[\s._\-—]+", "", strip_promo_markers(name)).casefold()


#: 无码片的文件名基本由「发行站 + 番号 + 画质/分卷」拼成，一个真标题词都没有：
#: `040221-001-carib-1080p.mp4`、`071213-625-1pon-whole1_hd.avi`、
#: `heyzo_hd_1380_full.mp4`。剥掉番号剩下的是发行残渣，不是标题——界面上却当
#: 标题显示成「040221-001 carib-1080p」。这些站名和画质标记是有限集合，日文
#: 标题里不会出现，可以按词剥。
_RELEASE_NOISE = re.compile(
    r"(?i)(?<![A-Z0-9])(?:"
    r"carib(?:bean(?:com)?(?:pr)?)?|1pon(?:do)?|10mu(?:sume)?|heyzo|"
    r"pacopacomama|paco|muramura|" + _TOKYO_HOT_SITE_TOKEN + r"|xxx[-_]?av|"
    r"\d{3,4}p|[0-9]?[fu]?hd\d*|sd|4k|2k|whole\d*|part\d*|full|lt|ch\d*"
    r")(?![A-Z0-9])"
)
#: 剥完之后判断剩下的还算不算标题：既没有中日文，也没有一个长度 ≥4 的字母词，
#: 那就是番号数字和零碎标记，不是名字。`Minah My new companion…` 留得住，
#: `1pon-092415-001-fhd2 (new)` 剥到只剩 `new` 就该判空。
_TITLE_CJK = re.compile(r"[぀-ヿ㐀-鿿]")
_TITLE_WORD = re.compile(r"[A-Za-z]{4,}")


def _is_release_residue(text: str) -> bool:
    return not (_TITLE_CJK.search(text) or _TITLE_WORD.search(text))


def jav_fallback_title(name: str | None, code: str | None) -> str:
    """Clean a filename-derived JAV title without changing the stored filename."""
    text = _MEDIA_EXTENSION.sub("", str(name or "").strip())
    text = _BRACKETED_PROMO_DOMAIN.sub(" ", text)
    text = _PROMO_DOMAIN.sub(" ", text)
    code_pattern = _jav_code_pattern(code)
    if code_pattern:
        repeated = re.compile(
            rf"^[\s._\-—]*(?:{code_pattern})(?:{_GLUED_EDITION})?(?=$|[\s._\-—\[])", re.I)
        while repeated.search(text):
            text = repeated.sub("", text, count=1)
        # 番号不总在开头：`1pon-092415-001-fhd1_(new).mp4` 把发行站放在了前面。
        # 只认前缀，整个番号就会留在「标题」里显示出来。
        text = re.sub(rf"(?<![A-Z0-9])(?:{code_pattern})(?:{_GLUED_EDITION})?(?![A-Z0-9])",
                      " ", text, flags=re.I)
    text = _EDITION_TAIL.sub("", text)
    text = _RELEASE_NOISE.sub(" ", text)
    text = re.sub(r"[\[\]【】()（）]+", " ", text)
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_—")
    return "" if _is_release_residue(text) else text


def jav_display_metadata(name: str | None, code: str | None,
                         tags: tuple[str, ...] | list[str] = ()) -> dict[str, object]:
    """Safe display projection; raw name/code remain untouched for file operations."""
    return {
        "display_code": normalise_code_key(code),
        "display_title": jav_fallback_title(name, code),
        "edition_badges": jav_edition_badges(name, code, tags),
    }


def face_focus(ratio: float, cx: float, cy: float) -> dict | None:
    """Convert a normalized face center into a circular-frame object position."""
    try:
        ratio = float(ratio)
        cx = float(cx)
        cy = float(cy)
    except (TypeError, ValueError):
        return None
    if ratio <= 0 or abs(1.0 - ratio) <= 0.05:
        return None
    if ratio < 1.0:
        pos = (cy - ratio / 2) / (1 - ratio)
    else:
        pos = (ratio * cx - 0.5) / (ratio - 1)
    pct = int(round(min(1.0, max(0.0, pos)) * 100))
    return {"axis": "y" if ratio < 1.0 else "x", "pct": pct}


def tag_cat(tag: str) -> str:
    """Classify one tag for the web surface."""
    if tag.startswith("演员:"):
        return "artist"
    if tag in LENGTH_TAGS or tag in TECH_TAGS or tag in ATTRIBUTE_TAGS:
        return "meta"
    if tag in RELATIONSHIP_TAGS:
        return "relationship"
    if tag in ROLE_TAGS:
        return "role"
    if tag in APPEARANCE_TAGS:
        return "appearance"
    if tag in SCENE_TAGS:
        return "scene"
    if tag in STORY_TAGS:
        return "story"
    if tag in POSITION_TAGS:
        return "position"
    return "general"


def part_marker(name: str) -> str:
    """Return a trailing multipart marker, if present."""
    match = _PART_MARKER.search(name or "")
    if match:
        return match.group(1).lower()
    circled = _CIRCLED_PART.search(name or "")
    return str(ord(circled.group(1)) - ord("①") + 1) if circled else ""


#: 首卷裸名（`TRE-080.mp4`）、后续卷带 `-2`/`-3` 时，裸名那份的时长必须落在其他卷的
#: 这个倍数之内。完整版至少是各卷之和，必然超出；几十秒的广告片又远低于下限。
PART_DURATION_SPREAD = 1.5


def _bare_first_part_plausible(bare: dict, parts: list[dict]) -> bool:
    durations = [float(item.get("duration") or 0) for item in [bare, *parts]]
    if any(value <= 0 for value in durations):
        return False                      # 没有时长证据就不替裸名下结论
    own, rest = durations[0], durations[1:]
    return min(rest) / PART_DURATION_SPREAD <= own <= max(rest) * PART_DURATION_SPREAD


def names_without_shared_part_tail(items: list[dict]) -> list[str]:
    """把这一组文件名共有的那段尾缀剥掉，卷号才回到扩展名前面。

    `PPT-018-1-uncensored.mp4`／`-2-`／`-3-` 是库里成批出现的写法：版次、修复标记和
    分辨率跟在卷号后面（实测还有 `_prob3`、`-4K修复`、`_8k`、`.1080p`），卷号不紧邻
    扩展名，三卷于是各占一张卡、标题还一模一样。

    判据是组内共有而不是词表：只剥每个文件名都带的那一段，且它必须从分隔符起头。
    `040221-001-carib-1080p` 与 `-720p` 的公共尾部是 `0p`，不从分隔符起头，剥不动——
    那两份是同一段画面的两个清晰度，本来就不该合成分卷。剥不出东西时返回空列表。
    """
    names = [str(item.get("name") or "") for item in items]
    stems = [name.rpartition(".")[0] for name in names]
    if len(stems) < 2 or not all(stems):
        return []
    common = stems[0]
    for stem in stems[1:]:
        while common and not stem.endswith(common):
            common = common[1:]
    separator = re.search(r"[-_. ]", common)
    tail = common[separator.start():] if separator else ""
    if not tail:
        return []
    stripped = []
    for name in names:
        stem, dot, suffix = name.rpartition(".")
        head = stem[: len(stem) - len(tail)]
        if not head:
            return []                     # 整个名字都是尾缀，剥完什么都不剩
        stripped.append(f"{head}{dot}{suffix}")
    return stripped


def ordered_multipart_items(items: list[dict]) -> list[dict]:
    """Return one unambiguous multipart release in playback order.

    Bare A/B and numeric suffixes are both common in the existing library.  A
    repeated marker means that one part has duplicate encodes, while mixed
    letter/number markers are ambiguous; neither case is safe to collapse into
    one browsing card automatically.

    盗版站常把第一卷留成裸名、后续卷才加 `-2`/`-3`（TRE-080 实测：9163/11255/8530 秒）。
    裸名也可能是整部完整版，所以只在数字标记、标记正好从 2 连续排起、且裸名时长与
    其他卷相差不大时，才把它当第 1 卷；字母卷缺 A 时无从判断裸名是不是 A，不猜。

    文件名原样认不出卷号时再按 `names_without_shared_part_tail` 剥一次共有尾缀重试。
    FC2 合集允许缺集，文件名前缀必须匹配登记番号，数字序号必须唯一且显式标出。
    """
    ordered = _ordered_by_markers(items, [str(item.get("name") or "") for item in items])
    if ordered:
        return ordered
    stripped = names_without_shared_part_tail(items)
    ordered = _ordered_by_markers(items, stripped) if stripped else []
    return ordered or _fc2_bonus_collection(items)


def fc2_collection_label(item: dict) -> str:
    """明确的 FC2 本篇、数字集数和特典标签；不根据时长猜序号。"""
    code = normalise_code_key(item.get("code"))
    match = _FC2_COLLECTION_NAME.match(str(item.get("name") or ""))
    if not code.startswith("FC2-PPV-") or not match or f"FC2-PPV-{match[1]}" != code:
        return ""
    tail = match[2]
    if not tail:
        return "本篇"
    bonus = _BONUS_PART.fullmatch(tail)
    if bonus:
        return "特典 " + str(int(bonus[1] or 1))
    number = re.fullmatch(r"[-_ ](\d{1,2})", tail)
    return str(int(number[1])) if number else ""


def _fc2_bonus_collection(items: list[dict]) -> list[dict]:
    """同番号、唯一标签且含显式特典的合集可按本篇与特典排序。"""
    labels = [fc2_collection_label(item) for item in items]
    if (len(items) < 2 or not all(labels) or len(set(labels)) != len(labels)
            or len({normalise_code_key(item.get("code")) for item in items}) != 1
            or not any(label.startswith("特典 ") for label in labels)):
        return []
    if "本篇" in labels and any(label.isdigit() for label in labels):
        return []  # 本篇与数字分卷并存，可能是完整版和切片。
    def key(pair):
        label = pair[0]
        return (1, int(label[3:])) if label.startswith("特典 ") else (0, int(label) if label.isdigit() else -1)
    return [item for _, item in sorted(zip(labels, items), key=key)]


def _ordered_by_markers(items: list[dict], names: list[str]) -> list[dict]:
    marked = [(item, part_marker(name)) for item, name in zip(items, names)]
    if len(marked) < 2:
        return []
    bare = [item for item, marker in marked if not marker]
    if len(bare) > 1:
        return []
    numbered = [(item, marker) for item, marker in marked if marker]
    markers = [marker for _, marker in numbered]
    if len(set(markers)) != len(markers):
        return []
    numeric = all(marker.isdigit() for marker in markers)
    alphabetic = all(len(marker) == 1 and marker.isalpha() for marker in markers)
    if not (numeric or alphabetic):
        return []
    positions = [int(marker) if numeric else ord(marker) - ord("a") + 1 for marker in markers]
    ordered = list(zip(positions, (item for item, _ in numbered)))
    if bare:
        if not numeric or sorted(positions) != list(range(2, len(positions) + 2)):
            return []
        if not _bare_first_part_plausible(bare[0], [item for item, _ in numbered]):
            return []
        ordered.append((1, bare[0]))
    elif sorted(positions) != list(range(1, len(positions) + 1)):
        # FC2 合集按明确的文件序号浏览，允许馆藏缺集；不推断裸名文件的序号。
        if not numeric or not _explicit_fc2_collection(items, names, positions):
            return []
    return [item for _, item in sorted(ordered, key=lambda pair: pair[0])]


def _explicit_fc2_collection(items: list[dict], names: list[str], positions: list[int]) -> bool:
    """缺集的 FC2 合集需要唯一数字序号与同一番号的文件名前缀。"""
    codes = {normalise_code_key(item.get("code")) for item in items}
    code = next(iter(codes)) if len(codes) == 1 else ""
    if not code.startswith("FC2-PPV-") or len(set(positions)) != len(positions):
        return False
    for name in names:
        match = _PART_MARKER.search(name) or _CIRCLED_PART.search(name)
        if not match or normalise_code_key(name[:match.start()]) != code:
            return False
    return True


def duration_clusters(items: list[dict]) -> list[list[dict]]:
    """Cluster same-code files by tight duration and multipart evidence."""
    clusters: list[list[dict]] = []
    known = sorted(
        (item for item in items if (item.get("duration") or 0) > 0),
        key=lambda item: item["duration"],
    )
    for item in known:
        marker = part_marker(str(item.get("name") or ""))
        for cluster in clusters:
            reference = cluster[0]["duration"]
            if abs(item["duration"] - reference) > max(
                DUPLICATE_FLOOR_SECONDS, reference * DUPLICATE_TOLERANCE,
            ):
                continue
            existing = {part_marker(str(row.get("name") or "")) for row in cluster}
            if marker and existing - {"", marker}:
                continue
            cluster.append(item)
            break
        else:
            clusters.append([item])
    clusters.extend([item] for item in items if not (item.get("duration") or 0) > 0)
    return _merged_by_filename(clusters)


def _merged_by_filename(clusters: list[list[dict]]) -> list[list[dict]]:
    """同番号同名副本可进复核簇；已知时长差超过 10% 的保持独立。"""
    home = list(range(len(clusters)))

    def root(index: int) -> int:
        while home[index] != index:
            home[index] = home[home[index]]
            index = home[index]
        return index

    first: dict[str, int] = {}
    named_durations: dict[str, list[float]] = {}
    for cluster in clusters:
        for item in cluster:
            stem = PureWindowsPath(str(item.get('name') or '')).stem.casefold()
            if float(item.get('duration') or 0) > 0:
                named_durations.setdefault(stem, []).append(float(item['duration']))
    for index, cluster in enumerate(clusters):
        for item in cluster:
            stem = PureWindowsPath(str(item.get("name") or "")).stem.casefold()
            if not stem:
                continue
            known = first.setdefault(stem, index)
            if root(known) != root(index):
                selected = {root(known), root(index)}
                known_times = named_durations.get(stem, [])
                if (known_times and max(known_times) - min(known_times) > max(15, max(known_times) * .10)
                        and not any(float(row.get('duration') or 0) > 0 for row in clusters[index])):
                    continue
                durations = [float(row['duration']) for cluster_index, cluster in enumerate(clusters)
                             if root(cluster_index) in selected for row in cluster
                             if float(row.get('duration') or 0) > 0]
                if durations and max(durations) - min(durations) > max(15, max(durations) * .10):
                    continue
                home[root(index)] = root(known)
    merged: dict[int, list[dict]] = {}
    for index, cluster in enumerate(clusters):
        merged.setdefault(root(index), []).extend(cluster)
    return [merged[key] for key in sorted(merged)]


def dir_expr(alias: str = "a.") -> str:
    """从 `path` 去掉 `name` 和分隔符，剩下的就是所在目录。

    表别名做成参数，是因为图集查询用 `a.`、按目录对账时直接查 `asset` 不带别名；
    靠对常量做字符串替换来凑另一种写法的话，改一次别名就会悄悄失配。
    """
    return (f"substr({alias}path,1,"
            f"length({alias}path)-length({alias}name)-1)")


#: 只写「这是图片」的通用目录名。它们做标题没有信息量，改用上一级目录名。
GENERIC_PHOTO_DIRS = frozenset({
    "p", "photo", "photos", "pic", "pics", "picture", "pictures",
    "image", "images", "img", "图片", "写真", "照片",
})


def photo_set_title(directory: str) -> str:
    """图集标题：叶子目录名；叶子只是 `P`、`图片` 这类通用名时用上一级。"""
    parts = [part for part in str(directory).replace("/", "\\").split("\\") if part]
    if not parts:
        return "未命名图集"
    leaf = parts[-1]
    if leaf.casefold() in GENERIC_PHOTO_DIRS and len(parts) > 1:
        return parts[-2]
    return leaf


def solo_performer_clause(asset: str, entity: str) -> str:
    """`asset` 这部作品的出演者只有 `entity` 这一位。

    代表作头像是从接触印相里裁脸：多人作品的那一帧不一定是她，而同一部作品的每个
    出演者都会拿到同一张脸——HOISW-002 的三位在首页顶排就是三个一样的头像。
    """
    return (f"NOT EXISTS(SELECT 1 FROM asset_entity solo WHERE solo.asset_id={asset} "
            f"AND solo.role='performer' AND solo.entity_id<>{entity})")


#: FC2 站方替敏感词打的码：一个星号顶一个字（`10代**生` 原文是 `10代現役生`）。
MASKED_TITLE_CHARS = "*＊"
#: javdb 往 FC2 标题里插的混淆串：两个星号后接一串小写字母与星号（`**pp*jonsoo`、
#: `**zs*yj*ospn`），卖家原文里没有这一段。卖家自己打的码后面接的是日文（`都立**超`、
#: `THE LAST**】`），不会被它带走。
SCRAPE_MARK = re.compile(r"\*\*[a-z*]{8,}[ 　]?")


def strip_scrape_mark(title: str) -> str:
    """去掉 javdb 插进标题的混淆串（`SCRAPE_MARK`）。"""
    return SCRAPE_MARK.sub("", str(title or "")).strip()


def fill_masked_title(masked: str, originals) -> str | None:
    """用别家的原文补上 `masked` 里的码，补不上返回 None。

    每个星号按一个字配，其余字逐字对齐；原文里只取配上的那一段，镜像站在标题前后加的
    促销语（`初撮影・顔出し！3日間限定1980pt！！`）不跟着进来。几份原文补出的结果不一致
    时不补：那说明对齐落到了不同的地方，没有一份能单独作数。
    """
    text = str(masked or "")
    if not any(char in text for char in MASKED_TITLE_CHARS):
        return None
    if not "".join(char for char in text if char not in MASKED_TITLE_CHARS).strip():
        return None
    pattern = re.compile("".join(
        "[^*＊]" if char in MASKED_TITLE_CHARS else re.escape(char) for char in text))
    filled = {found.group(0) for original in originals
              if (candidate := str(original or ""))
              and not any(char in candidate for char in MASKED_TITLE_CHARS)
              and (found := pattern.search(candidate))}
    return filled.pop() if len(filled) == 1 else None
