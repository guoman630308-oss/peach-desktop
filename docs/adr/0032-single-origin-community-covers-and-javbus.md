# ADR-0032：社区封面只有一个图源时照样用，JavBus 作第三个社区来源

- 状态：Accepted
- 日期：2026-09-15

## 背景

ADR-0030 让社区来源的封面在两个图源上对得上才用。问题清单里 IPX-060 只有 javdb 有图，
MIDE-594 官方各版本只回「准备中」占位图，这类片一直没有封面，每轮采集都报成问题。
用户 2026-09-15 核对：MIDE-594 在 JavBus 上有封面。

同一天盘点 `cover-fetch-log.csv`：缺封面的 466 个番号里，82 个在 Javinizer-Go 缓存里有 JavBus
快照的封面地址。`cached_metadata` 不分来源把快照封面当官方候选，社区站的图因此绕过了图源印证；
而 JavBus 搜不到原番号时返回的是别的作品（`scripts/scrape_codes.py`）。

决定：实在只有一个图源，也比没有封面、卡在问题清单里强。

## 决策

**一、单一图源照用。** `verified_cover` 仍先找两个图源对得上的最大那张；可用的图全出自同一个
图源时，取像素最大的那张，返回的印证图源为空，`.scraping.json` 的 `verified_by` 记 `[]`，即未经印证。
两个图源各给了图却对不上，照旧不用；有官方小图就退回官方小图。

**二、JavBus 作第三个社区来源。** 按 AVBase、JavBus、javdb 的顺序问；JavBus 作品页 `/<番号>`
一次请求，图源记 `javbus`，番号页 404 或页上番号不一致记「没有」。

**三、Cookie 由用户提供。** JavBus 有年龄门，javdb 有登录墙。两家在采集设置里接受 Cookie，公开采集
随请求带上（`scraping_access.SOURCES` 的 `session`）。Peach 不读浏览器的 Cookie 库；JavBus 回的页上
没有「識別碼」时报「多半是年龄确认页」，让用户去贴 Cookie，不记成没有。

**四、社区站快照不当官方候选。** Javinizer-Go 的 JavBus、javdb 等社区站快照只借厂牌判断走哪条
官方渠道，封面地址与 content_id 不进 `best_cover` 的候选。

## 理由

- 封面是可重建产物，不写 ledger；`verified_by` 为空的封面可以按 sidecar 列出来复查。
- 两个图源给的图不一致是有证据的冲突，和「没有第二个图源」不是一回事，前者照旧不用。
- 社区站的图该走社区来源的规则，不能借快照的名义与官方地址同等对待。

## 被否决的方案

- **Peach 自带固定的年龄确认 Cookie。** 厂牌回查脚本 `localize_studio_names.py` 带过 `age=verified`；
  2026-09-15 实测番号页不带 Cookie 回答题式年龄验证页，固定值能否过门未取得实证，用户自己过门后的
  Cookie 更可靠。
- **继续坚持两个图源。** 只有一家收录的片会一直没有封面。

## 后果

- 采集每部官方落空的片多问 JavBus 一次；没贴 Cookie 时问题清单写明去贴 Cookie。
- `verified_by` 为空的封面未经印证，可能是挂错的图。
- `/scraping` 页多出 JavBus 一项，JavDB 一项可以贴 Cookie。
- 批量 `fetch_jav_covers.py` 只用官方快照的封面地址；只有社区站有图的片交给采集任务。
