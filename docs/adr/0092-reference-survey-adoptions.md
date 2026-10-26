# ADR-0092：2026-10-01 参考项目调研的采纳与不采纳

- 状态：Accepted
- 日期：2026-10-02
- 相关：ADR-0018、ADR-0025、ADR-0065、ADR-0087、ADR-0088、ADR-0089、ADR-0090

## 背景

2026-10-01 对 SakuraMedia、OpenAver、JavBoss、Javdex、javm、mdcz、Javinizer-Go、AMMDS、NeoAVDC、
javranking、Cuelume 等 16 个项目的最新版做了一轮调研，最新代码浅克隆在仓库外 `attic/tools/20261001-参考项目/`。
调研给出的每条做法都要有一个去处：已经做了、进了待办、并进了现有条目、记成来源候选，或者不做。
不做的要留理由，下一轮调研碰到同一个做法时，先查这里。

## 决策

下表里的「待办 N」指 [公开路线图](https://github.com/orgs/peach-mitao/projects/7)「尚未实现」的编号。

### 已落地

| 做法 | 来源项目 | 落点 |
| --- | --- | --- |
| minnano-av 的 HTTP 客户端被拦时改由本机浏览器取页 | OpenAver | 提交 `32ef0f83`，ADR-0065 的浏览器通道 |
| 文件消失时保留个人记录，新版本入库时接回 | SakuraMedia | ADR-0087 |
| 实体合并留转址（墓碑） | SakuraMedia | ADR-0088 |
| 115／PikPak 离线下载 | JavBoss | ADR-0089 |
| 「想要」清单，入库按番号自动对账 | SakuraMedia、OpenAver | ADR-0090 |
| 隐私模式 | Javdex | 设置页的 SFW 模式覆盖图片与视频遮挡，并停止悬停预览；`settings-panel.tsx`、`web/app.js` 与 `card-art/hover.ts` |

### 进待办

| 做法 | 来源项目 | 待办 |
| --- | --- | --- |
| 本地 BT 与直链下载 | SakuraMedia | 42 |
| Torznab 资源搜索与候选筛选 | SakuraMedia、garage、Atlas | 43 |
| DMM cid 前缀表随版本发布、失败分类 | Javinizer-Go、AMMDS、mdcz | 44 |
| 来源测试录制回放 | mdcz | 45 |
| 挂载可达性探测 | OpenAver | 46 |
| 时刻、合集与片段导出 | SakuraMedia | 47 |
| 上榜标记 | javranking | 48 |
| 播放器画面条 | SakuraMedia | 49 |
| 女优身份冲突的四个动作 | Javinizer-Go | 50 |
| 女优体型筛选 | JAV_MovieManager | 53 |
| 无码官方站 | Javinizer-Go、mdcz | 54 |
| 字段策略两条 | mdcz | 55 |
| 头像裁剪记源图指纹 | Javinizer-Go | 56 |
| 长下载续传与停滞看门狗 | Javinizer-Go | 57 |
| 来源缺陷台账 | JavBoss | 58 |
| 番号清洗语料 | NeoAVDC | 59 |
| 结果提示音 | Cuelume | 60 |
| r18.dev dump 本地镜像 | Javinizer-Go、AMMDS | 61 |
| 来源开关旁写明来源性质 | 本轮综合 | 64 |
| 站点互联、漫画与同人本（调研不采纳，待办保留为观察与低优先级） | AMMDS | 67、68 |
| PWA、原生客户端（远期） | SakuraMedia | 69、70 |
| 系统诊断页 | SakuraMedia、javm、OpenAver | 26 |
| 开放 API 与按文件哈希查找（osHash 指纹） | AMMDS、OpenAver、SakuraMedia | 27 |
| 「今天看什么」推荐分 | SakuraMedia | 28 |
| JavDB 官方 App 私有 API 作可选来源 | OpenAver、JavBoss | 29 |
| 局域网配对码 | Javdex | 19 |
| 「已拥有」标记的浏览器扩展（低） | JavBoss | 71 |

### 并入现有条目

| 做法 | 来源项目 | 落点 |
| --- | --- | --- |
| 女优「发行时年龄」字段与筛选 | OpenAver | 待办 53 的子项 |
| 按演员归档的目录模板变量 | NeoAVDC | 待办 9 的子项 |
| 推荐时刻每片至多 3 条 | SakuraMedia | 待办 47 与 28 的子项 |
| 四种 NFO 样例核对读取覆盖面 | Javdex | 补进 `library_nfo` 的测试，不另开号 |
| 广告片识别作对照 | javm | `scripts/find_ads.py` 已登记在 [docs/SOURCING.md](../SOURCING.md) |

### 记为来源候选

xcity → 维基 → graphis 的女优资料备援（OpenAver），以及 sokmil、kingdom.vc、km-produce、fantia、jav321、
javlibrary 六站，写在 [docs/SOURCING.md](../SOURCING.md) 的 minnano 一节与来源链一节，接入前逐站取证。

### 调研建议不采纳，用户 2026-10-01 定为改造后接受

| 做法 | 来源项目 | 调研的理由 | 用户定的条件 |
| --- | --- | --- | --- |
| javinfo.dev 作可选来源 | javinfo | 付费、闭源、限速 | 待办 65：用户自带 API key，接入前对一组已复核番号对照字段准确率与速度 |
| 画面向量 | SakuraMedia | 单机单人用不着向量库的运维 | 待办 51：用 SQLite 扩展 `sqlite-vec`，不多起服务 |
| 遥测 | SakuraMedia、javm | 默认开启、上报过多 | 默认关闭、字段明示；技术诊断与明确同意的群体研究见 [ADR-0109](0109-opt-in-diagnostics-and-population-research.md) |
| 小文件打包 | SakuraMedia | 图片打包的迁移不可回退 | 待办 66：能解包回原样，和待办 32 一起设计 |
| 自定义 SQL | JAV_MovieManager | 绕过 ledger 读写边界 | 待办 62：只读连接，写不进去 |

### 不采纳

| 做法 | 来源项目 | 理由 |
| --- | --- | --- |
| 外部来源值与本地覆盖值分两列 | SakuraMedia | Peach 已有 `field_owners` 的字段归属与覆盖规则（ADR-0018、ADR-0025） |
| 媒体「复活」 | SakuraMedia | ADR-0087 的接回已覆盖 |
| 书签满 14 天、30 天提醒 | OpenAver | 想要清单在页签里看，不推提醒；出现需求再议 |
| 外部网页清单导入与消歧 | Javdex | 没有批量导入需求 |
| 片库分析联动 | OpenAver | 口味页已覆盖 |
| 自动下载闭环 | SakuraMedia、NASSAV | 默认不动用户文件；可控的形态只有用户自己给磁力的网盘离线（ADR-0089） |
| 截流媒体地址 | javm | 在 WebView 里注入脚本取流媒体地址，不适合 |
| garage、Atlas 的解析器 | garage、Atlas | 覆盖面不及 Peach 现有 |
| NASSAV 整体 | NASSAV | 数据源全是未授权转载站，Memo 有会员墙 |

## 理由

- 调研结论只活在仓库外的草稿里，下一轮调研会把同一批做法再评一遍；一张去向表让每条只评一次。
- 被用户推翻的调研结论要单独写出来：草稿里写的是「不采纳」，待办里却有这一条，没有这张表就看不出哪边是定论。

## 后果

- 调研原文不进仓库，事实以各待办条目与本表为准。
- 新一轮调研碰到已在「不采纳」里的做法，先看理由是否还成立，再决定要不要重开。
