# ADR-0081：补女优资料后继按名字搜 javdb，绑定演员 id

- 状态：Accepted
- 日期：2026-09-27

## 背景

人物页的 JavDB 入口只按 `entity_external_ref(provider='javdb')` 拼地址，没有 id 就不出（`entry_links`）。
这个 id 原先只有两条来路：作品的演员字段恰好采用了 javdb 作品页（`sources.javdb.actresses`），或者
`scripts/backfill_performer_entry_ids.py` 读本机 javdb 页面缓存一次性回填。缓存无法保证持续更新，
新导入的女优走补女优资料后继（ADR-0067），只补 minnano-av 与 avwikidb。

javdb 的演员搜索 `search?f=actor&q=` 回的每张卡都带站内 id 和标题一栏（这个人在站上的全部写法）；
同一位女优可能有多条记录；無碼条目的头像上有 `<span class="info">無碼</span>` 标记。

## 决策

**一、javdb 进补女优资料后继，排在 minnano-av 与 avwikidb 之后。** 那两站可能刚给她绑上 minnano-av 编号，
排在后面才判得出她是不是 JAV 女优。

**二、只给 JAV 女优、只绑 id。** 她在 JAV 目录站里有身份（`entry_links.is_jav_performer`）又还没有 javdb id
才搜；FC2 个人摄创作者不在这个站的收录范围里，已有 id 的也不再问。资料页不进：入口只要 id，
而無碼那条的资料页常要登录。

**三、卡片判身份，同名不挑。** 名字链里收得下的写法（`performer_alias_followup.rejection`）至多搜三个，
搜到就停。卡片标题一栏里要有账本里她的名字，比较时全半角、大小写与日本字形都折掉（`match_key`）。
对得上的几张卡每种记录类型至多一张（`javdb.one_person`），就是同一个人，几个 id 都绑上；
同一种记录对上两张是站上同名，写「未命中」，id 列进判词。

**四、限速与冷却守来源下限。** 这一站单独 5 秒间隔，与目录采集的 `SOURCE_INTERVAL` 同一档；一条后继
至多三次请求。走 `SourceTransport`，撞上 403、429 或机器人验证就记进 `javdb` 那一份冷却，作品采集
与这条后继在冷却期里都不再问。搜索页缓存 30 天；回 200 的登入页不进缓存。登入页与网络失败都记「未取得」，
一个写法都没搜成就不算「站上没有她」。

**五、落库与撤回照 ADR-0067。** 判据确定，按 ADR-0052 直接落库；每条 id 带
`auto:performer-profile@<任务行 id>`，`revert_auto_landing.py --source auto:performer-profile` 整批撤回。
判据版本 `RULE` 加一，存量女优按新判据各再派一次。

**六、历史缺口由回填脚本补。** 回填脚本同样读搜索页缓存里的卡片，判据与第三条同一份；
名字比较也换成 `match_key`，账本写 `泽村玲子`、站上写 `澤村玲子` 能对上。资料页缓存和卡片用同一套
写法：`rejection` 不收的短单名、罗马字不作锚点（实测 `初美沙希` 只靠别名 `しずく` 对上了另一个人）。
后继只搜 JAV 目录站里有她的人，回填对应的门槛是：没有 JAV 目录站 id、带番号的作品又全是 FC2 的，
记「只有 FC2 作品」不写。

## 后果

- 存量按每轮 16 条的名额慢慢轮到；缺 javdb id 的那两百来位，每位多两三次 javdb 请求。
- 这一站未取得时，记号只保 24 小时，和另外两站一样。
- 判词写进 `generated/performer-profile-landing.csv`，`site` 列为 `javdb`。

## 未决

- 站上除 `無碼` 以外的记录类型标记未取得。出现新标记时，同一人的两条记录仍按每种类型至多一张判。
- 按作品番号从作品页定 id 更确定，但每部要两次请求；在 javdb 配额下没有采用。
