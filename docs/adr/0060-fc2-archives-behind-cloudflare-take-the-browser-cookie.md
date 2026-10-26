# ADR-0060：FC2 链接上 FC2PPV-DB 与 JAVten，Cloudflare 后面的站用浏览器的 Cookie 进

- 状态：Accepted
- 日期：2026-09-24
- 修订：ADR-0043「移除 fc2ppvdb」那一节（旧域 fc2ppvdb.com 仍判已关；新域 fc2ppv-db.com 由 Peach 自写解析器接回）

## 背景

FC2 链是发行方商品页 → fc2cmadb → JavArchive。下架作品在 fc2cmadb 也常常没有，JavArchive 只给标题与转存封面，
演员一栏整条链只有 fc2cmadb 一处。用户给了两站：fc2ppv-db.com（旧 fc2ppvdb.com 关站后新起的 FC2 元数据库）与
javten.com（fc2hub.com 的后继，OpenAver issue #186 的 FC2 刮削经验点名它），要求「研究下怎么抓取，需要 cookie
的话就在来源里加 cookie」。同时评估的 bigboobs.pink 不按 FC2 号索引、只按女优聚合文章，本轮不接。

## 实测（2026-09-24）

| 项 | fc2ppv-db.com | javten.com |
| --- | --- | --- |
| 门 | Cloudflare JS 验证：httpx 与 curl_cffi `impersonate=chrome` 都回 403、标题 `Just a moment...` | 同 |
| 进法 | 浏览器过验证后的 `cf_clearance`；它绑着解题浏览器的 UA 与出口 IP，UA 不一致照样 403 | 同 |
| 作品页 | `/ja/videos/<id>`，Next.js 服务端渲染，无 `__NEXT_DATA__`，无作品 JSON 接口 | `/video/<站内号>/id<id>/<标题>`，站内号拼不出来，先 `/search?kw=<id>`；单命中站方直接跳作品页 |
| 给什么 | 女优（链到站内女优页）、卖家（slug 与发行方用户页同名）、販売日、タグ、時長、「流出あり／なし」 | 日文标题、标签、卖家名、時長、販売日（`videos:published_time`）、FC2 存储原件封面地址两处 |
| 封面 | 360×360 的 CloudFront 缩略图（`og:image:width` 写 800，实测 360），不合封面最低宽度 | `storage*.contents.fc2.com` 原件，与发行方商品页同一文件，下架后可能已删 |
| 没有 | 回 200 的 404 页，`<h1>` 写 `404` | 搜索结果里没有 `id<号>` 那一条 |
| 跳转 | 无 | 搜索单命中 302 的 Location 写成 `http://`，且带着用户会话时落到用户上次选的语言版（`/tw/`） |
| 语言 | 日文原页 `/ja/` | 有 `/tw/`、`/en/`、`/ko/` 译文版，中文是机器翻译（用户判定） |
| 演员 | `FC2-PPV-4898837` 写 `川北すずね`，日文原名 | 不给演员 |

## 决策

**一、两站各自自写解析器，套站点契约。** `sources/fc2ppvdb.py`（来源名 `fc2ppvdb`，解析器 `fc2ppvdb-page`）与
`sources/javten.py`（`javten`，`javten-page`），按社区来源登记（`SOURCE_SPECS`）：女优、标签、卖家由站方用户维护。
Cloudflare 验证页由 `sources.base.challenge_page` 认，归 `cloudflare_challenge`，措辞指向采集设置。

**二、链位置 fc2 → fc2cmadb → fc2ppvdb → javten → javarchive → fc2club → javdb。** fc2cmadb 给下架作品的原图与
女优栏，仍排最前；FC2PPV-DB 给女优与流出标记、不给封面；JAVten 给日文标题、标签与存储原件地址；JavArchive
只有转存封面，仍在最后。`OFFICIAL_STAGE` 与 `FC2_STAGE` 同加。缺演员时接着问的例外从「只问 fc2cmadb」扩成
`FC2_CAST_SITES = ("fc2cmadb", "fc2ppvdb")`；演员栏来源优先级 `FIELD_SOURCE_PRIORITY["performers"]` 把 fc2ppvdb
排在 fc2cmadb 之后、javdb 之前。

**三、JAVten 只收日文原页。** `fetch` 只取不带语言前缀的地址，落到译文页再取原页；`parse` 见 `og:url` 带语言前缀
归 `parse_error`，不把机翻写进标题。搜索那一跳的 Location 是 `http://`：`HttpxTransport` 跟跳时把同主机的
`http://` 升回 `https://`（浏览器靠 HSTS 做同一件事），否则 Secure 的 `cf_clearance` 不随明文地址发出，
Cookie 对了也回验证页。只升不降，换了主机的明文地址仍按跨源丢凭据。

**四、FC2PPV-DB 不交封面。** 缩略图连最低宽度都过不了，交下去只是白花一次请求；封面留给链上前后指着存储原件的几档。
「流出」标记进 `extra['leaked']`，只作证据，不投影到账本字段。

**五、UA 走整站那一份，不按来源另收。** `cf_clearance` 绑着解题那台浏览器的 UA，但 Peach 是单人自托管应用，
浏览器就是用户这一台 Chrome：`peach.user_agent.USER_AGENT` 按它的 `chrome://version` 定（2026-09-24 为
Chrome 153），Chrome 大版本升了就改那一处，全部对外请求一起换。用户的判据是「不要我单独输 UA」。
`scraping_access.SOURCES` 里这两站收 `cookie`、`session`，`blocked_pause` 6 小时；保存新 Cookie 时清掉该来源的
冷却：冷却记的是旧 Cookie 撞出来的账。收 Cookie 的来源 403 时的暂停措辞指向「更新它的 Cookie」。

**六、bigboobs.pink 不接。** 可直接抓（WordPress，无 Cloudflare），但只有按女优聚合的文章，没有 FC2 号入口，
作女优别名与作品聚合的参考，不进链。

## 后果

- FC2 链多两档，只在前面几档落空或缺演员时发请求；没配 Cookie 时各自 403 一次后整站冷却 15 分钟起、翻倍到 6 小时，
  链照常往下走，不影响别的档。
- `cf_clearance` 有效期短（站方一般数小时到一天），过期就要用户重新贴。这是这两站的固有成本，不是 Peach 的缺陷；
  自动过验证（无头浏览器、第三方解题服务）不做。
- Cookie 必须来自 UA 所指的那台 Chrome，连接方式必须选与它同一个出口；Chrome 升了大版本而 `USER_AGENT` 没跟上，
  表现是 Cookie 对了仍 403。
- 演员栏多一处日文原名来源；两站的女优名不一致时按现有的候选与复核流程处理。
