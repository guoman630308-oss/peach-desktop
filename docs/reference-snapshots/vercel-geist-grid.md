# Geist Grid 取证记录

- 取证日期：2026-08-28（原记录在 [开发约定](../../AGENTS.md)，2026-09-02 移入）
- URL：<https://vercel.com/geist/grid>
- 取证方式：实时 DOM 读取（Geist 文档站是 React 渲染，视觉规格拿不到可哈希的快照；
  与 `vercel-geist-controls-measured.md` 同一策略，不在 `docs/reference-sources.json` 登记）
- 上游版本：未取得（页面无锁定资源版本记录；复核需重新抓取）

## 实测要点

- 实时 DOM 使用资源类 `grid-module__AMTIxG__grid`。
- 网格引导线由父级统一拥有，单元透明、0 圆角，示例按阅读顺序逐行排列。
- 官方明确要求各断点可预测重排、可点击单元独立显示 focus。

## Peach 采用与差异

- 复用父级共享边线与 3／2／1 列断点重排。
- Tag 仍保留 6px 小圆角，普通排行不复制演示页的大留白或装饰性引导线。
