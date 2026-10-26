# Geist Tabs 的两个变体与分段器的分界（实测）

不登记进 `docs/reference-sources.json`：这里是浏览器实测的 DOM 计算值，给不出可重抓的上游字节。
对应的上游正文已登记，见 `docs/reference-snapshots/upstream/vercel-geist-tabs.md`
（来源 id `vercel-geist-tabs-secondary`）与 `docs/reference-snapshots/upstream/vercel-geist-switch.md`
（来源 id `vercel-geist-switch-segmented`）。

取证方式：2026-09-03 在浏览器打开 `https://vercel.com/geist/tabs` 与 `https://vercel.com/geist/switch`，
对 `[role="tablist"]` 及其 `[role="tab"]` 逐个读 `getComputedStyle` 与 `getBoundingClientRect`。
页面是深色主题，下面的 RGB 都是深色下的实测值。

## Tabs：primary（默认）

| 部位 | 实测 |
| --- | --- |
| tablist | `display:flex`、`gap:24px`、高 50px、`box-shadow:0 -1px 0 rgb(51,51,51) inset`（整条底线） |
| tab | `padding:14px 2px`、`font-size:14px`、`font-weight:400`、无圆角、无底色 |
| 选中 | 文字 `rgb(237,237,237)`，`border-bottom:2px solid rgb(237,237,237)` |
| 未选中 | 文字 `rgb(161,161,161)`，`border-bottom:2px solid transparent` |

## Tabs：secondary（`variant="secondary"`）

| 部位 | 实测 |
| --- | --- |
| tablist | `display:flex`、`gap:normal`（即 0）、高 32px、`!shadow-none`（不画底线） |
| tab | 高 32px、`padding:0 12px`、`border-radius:6px`、`font-size:13px`、`font-weight:400`、无边框 |
| 选中 | 底 `rgb(31,31,31)`（`--accents-2` 那一级），文字 `rgb(237,237,237)` |
| 未选中 | 底透明，文字 `rgb(161,161,161)` |

## Switch（分段器）

上游 Best Practices 原文见已登记的 upstream 文件。判据只有三条：分段器用于 2–3 个互斥选项、
两三个字的短标签、同一表面的不同视图；超过 3 项或标签变长就改用 Tabs 或 Select；布尔开关用 Toggle。
容器与选中块的实测数值早前已记在 `docs/reference-snapshots/vercel-geist-note-progress-switch-analytics.md`。

## 上游对「什么该是 Tabs」的两条判据

原文在已登记的 `upstream/vercel-geist-tabs.md`，与本项目的落点直接相关：

- 数量上限：桌面一排 5–7 项、移动 3–4 项，超过就合并或把次要视图收进 `Menu`。
- 语义前提：Tabs 表示这些视图共享 scope、URL parent 和 **data model**；页面之间的导航用子菜单，不用 Tabs。

按这两条量 Peach：垃圾文件那 7 项是同一批候选按 type 收窄，数据模型完全相同、当前项已落在
URL 上，是标准的 Tabs 用法，且刚好压在 7 项上限。复核那 10 项各自换掉整个面板的数据模型，
既越过 5–7 上限、也不满足「共享 data model」，按规范应当收敛项数或把低频分类收进 Menu。
这一条尚未处理，记在这里而不是当作已对齐。

## Peach 的落点与保留的差异

- 人工复核分类 10 项、垃圾文件分类 7 项，都越过分段器的 2–3 项上限，所以这两条是 Tabs 不是 Switch。
  两条现有的 pill 外观本来就对应 secondary 变体，不改成下划线式，只把数值对齐 secondary。
- 有意加回 1px `--border-15` 描边，相邻两枚给 5px 间距（不给间距两条 1px 边会粘成一条 2px 的）。
  上游 secondary 不描边，正文里也没有「secondary 必须放在容器内」这类条款——**未取得**，
  没有实测能证明上游是靠容器边界替这排控件划范围。Peach 侧的事实是可核对的：这两条直接坐在
  页面顶部，下面就是全宽网格，四周没有任何容器边线，不描边时只有选中那一枚有底色，其余
  六到九项没有可视边界。2026-09-03 用户复核界面后要求加回描边，判断依据是「更好看」加
  「我们不在框内」；描边取值与排序条 `.count .sorts button` 同族，同一页面族里认得出是同类控件。
- 浅色主题里选中底色取 `--hover`：深色实测的 `rgb(31,31,31)` 是「比页面底色高一级的中性面」，
  Peach 浅色下同一语义的变量是 `--hover`，不是墨色反相。原先两条都用 `--ink-2` 反相 + 600 字重，
  比规范重了一整级。
- 计数按规范当徽标处理，为 0 时整枚去掉，不留 `0` 占位。
- 垃圾文件那条仍是 `<a href>` 导航而不是 `role="tab"`：规范要求当前项反映在 URL 上可深链，
  Peach 这里就是靠 `?type=` 做的；同一条里的「已排除」是另一根轴（视图切换），
  用分隔符隔开，也不进 tablist。复核分类没有独立 URL，升级成了真正的 `role="tablist"`
  加左右方向键漫游焦点。
- Peach 保留每项前的图标（上游 `With icons` 示例支持 icon slot）与 40em 以下 44px 触摸高度。
