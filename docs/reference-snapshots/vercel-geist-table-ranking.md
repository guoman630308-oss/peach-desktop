# Vercel Geist Table 与首页布局证据（2026-09-04 复测）

> 本文是 Peach 的人工取证笔记，不在 `reference-sources.json` 里登记；同 id 来源的上游 Markdown 原文锁定在 `upstream/vercel-geist-table.md`，`accept` 只覆盖那个文件。

## 请求与取得通道

- 目标：`https://vercel.com/geist/table`、`https://vercel.com/home`。
- 2026-08-30 那一轮：登录态内置浏览器两次读取 Table 均在 30 秒超时并重置控制连接，
  直接读取又因该入口声明的 `text/markdown` 内容类型不受读取通道支持而返回 HTTP 400，
  DOM、计算样式与正文都记为未取得，只从官方索引拿到几条使用原则。
- 2026-09-04 用内置浏览器重取成功。正文、六个示例的元素属性、`<tbody>` 上的变体类名与
  `:root` token 计算值都已取得；下面的数值全部出自 `getComputedStyle` 和元素属性，
  没有截图测量，也没有推断。

## Table 的三个变体是正交开关，基态什么都不加

变体不写在 `<tr>` 上，而是 `<tbody>` 的类名；行本体只有 `transition-colors`：

| 变体 | `<tbody>` 类名 | 效果 |
| --- | --- | --- |
| 基础 | `[&_td:first-child]:rounded-l-sm [&_td:last-child]:rounded-r-sm` | 行既无填充也无分隔线 |
| 隔栏异色 | `[&_tr:where(:nth-child(odd))]:bg-background-200` | 奇数行填 `--ds-background-200` |
| 分隔线 | `[&_tr:not(:last-child)]:border-b` 加 `:border-gray-400` | 除末行外每行一条发丝线 |
| 可交互 | `[&_tr:hover]:bg-gray-100` | 悬停整行填 `--ds-gray-100` |

- 隔栏异色与分隔线不同时出现：Striped 示例没有行线，Bordered 示例没有行填充。
  Full featured 与 Virtualized 用的是隔栏异色加可交互，仍然没有行线。
- 隔栏色比页面**更暗**：`--ds-background-200` 是 `hsla(0,0%,0%,1)`，页面底
  `--ds-background-100` 是 `hsla(0,0%,4%,1)`。悬停色 `--ds-gray-100` 是 `hsla(0,0%,10%,1)`，
  比两者都亮，所以隔栏与悬停不会互相盖住：一个向下压，一个向上抬。
- 行填充左右两端收圆角（`td:first-child` 圆左、`td:last-child` 圆右），不通到容器边。

## 表头、表脚与度量

- `<thead>` 恒有一条下边线：`[&_tr]:border-b` 配 `border-gray-400`（`hsla(0,0%,18%,1)`）。
  这条线与变体无关，基础表也有。
- 表头与首行之间插一个 `aria-hidden` 的空 `<tbody class="h-3 block">`，即 12px 间隙，
  不靠 padding 或 margin。
- `<th>`：高 `--ds-size-medium` 即 36px、`padding:0 8px`、`font-weight:500`、
  色 `--ds-gray-900`（`hsla(0,0%,63%,1)`）、左对齐、末列右对齐。
- `<td>`：`padding:10px 8px`、行高 40px、色同为 63% 灰、`white-space:nowrap`、末列右对齐。
- `<tfoot>`：`border-t` 配 gray-400 加 `font-medium`，用于合计行（Full featured 的 Subtotal）。
- 外层 `[data-slot=table-root]` 是 `relative w-full overflow-x-auto`，横向溢出在表自己身上滚。

## 正文给出的判据

- 用 `Table` 的条件：各行同形，且至少一列可排序或可跨行比较。一行描述性内容配单个动作用
  `Entity`；详情页的键值元数据块用 `Description`，不做成两列表格。
- 列表为空时把 Empty State 渲染在表格**外面**，不留一个空的 `<Table.Body>`。
- 单元格里值未知或不适用写 `—`，不用 `N/A`、`null` 或空串。
- 可排序表头是 button；可见标签保持 Title Case；**方向箭头是装饰性的，按钮向辅助技术播报的是
  下一个排序状态**，不是当前状态。
- 数字列加 `tabular-nums`（或 Geist Mono），让各行数位对齐以便比较。
- 列头是 Title Case 的名词或名词短语（`Last Used`、`Requests (7d)`），不写句子。
- 单元格里的相对时间用短形式（`2m ago`、`5h ago`），超过 7 天换成 `Mar 14, 2026`。
- 分页按钮文案是 `Previous` 与 `Next`；页码文案写 `Page 2 of 7` 或 `21–40 of 142`，
  区间里用 en-dash。
- 长列表用虚拟化表格，底部一枚 `Show More` 按钮；定长的短表格没有这枚按钮。

## Peach 采用与差异

- 使用空间的四个位置属于同形可比较数据，使用原生 `table`（`.insightdatatable`），列为位置、
  已用、可用与使用率；数字列 tabular numerals。容量拿不到时单元格写 `未取得`／`离线` 而不是
  `—`：Peach 的取证规则要求区分「不适用」与「取证失败」，这一处主动偏离 Geist。
- 三张表（`.insightdatatable`、`.insighttable`、`.linktable`）都采用分隔线变体，
  不叠加隔栏异色：Geist 的这两个变体互斥，叠起来等于同时用两套分行手段。
- 配置页的 CloudDrive 起步配置按缓存所在硬盘分三档，每档一项列表，项内用 `dl` 列出缓存上限、
  读取长度与同时处理视频三项读数。它用列表而不是表格：设置卡片在 390px 下放不下四列，
  列表不需要横向滚动。
- 悬停填充只给可点的行（`.insighttablerow:is(button)`、`.insightrankrow`）。不可点的行不加悬停，
  那等于给一个不存在的动作画反馈。
- 内容标签是固定 Top 30 排行并可直接筛选，不提供虚假的列排序；桌面以两栏有序列表提高扫描
  密度，DOM 顺序保持排名顺序，390px 手机回到单栏。
- 最近看过固定 12 行、由服务端一次给全，没有下一页可取，因此不配 `Show More`。
- 排序控件不在表头上，而是横排互斥的排序条（`.count .sorts`）：Peach 的主列表是卡片网格，
  没有表头可挂。方向的分工照 Geist 执行——箭头装饰、`aria-pressed` 表达当前项、
  无障碍名称播报下一步动作。
- 表格与排行都复用 Peach 既有 `--line-soft`、字号和 1100px 阅读列，不复制 Geist 的
  36px／40px 行高与 8px 内边距。

## 复核入口

- https://vercel.com/geist/table
- https://vercel.com/home
