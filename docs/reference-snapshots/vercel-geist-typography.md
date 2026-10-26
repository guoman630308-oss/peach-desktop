# Vercel Geist Typography 实测

> 本文是 Peach 的人工取证笔记，不在 `reference-sources.json` 里登记：
> 该页由 React 渲染，数值只能从计算样式读出，给不出可哈希的上游快照。

- 取证日期：2026-09-04
- 取证入口：应用内浏览器打开 `https://vercel.com/geist/text`，对示例元素跑 `getComputedStyle`
- 复核方式：重开该页，取任一 `[class*="text-heading-"]` 元素读 `fontSize/lineHeight/letterSpacing/fontWeight`
- 未取得：上游 Markdown 源（该站对 `text/markdown` 返回 400），Figma 数值只能从渲染结果反推

## 通道

Geist 把排版发成 Tailwind 类，分四组：`text-heading-*`（引导页面与区块）、`text-button-*`
（只用在按钮里）、`text-label-*`（单行，配图标）、`text-copy-*`（多行正文，行高比 label 高）。
类名里的数字就是字号。`<strong>` 嵌在这些类下面即取得 Subtle / Strong 修饰。

## Heading 实测值

| 类 | 字号 | 行高 | 字距 | 字重 |
| --- | --- | --- | --- | --- |
| `text-heading-72` | 72px | 72px | -4.32px | 600 |
| `text-heading-64` | 64px | 64px | -3.84px | 600 |
| `text-heading-56` | 56px | 56px | -3.36px | 600 |
| `text-heading-48` | 48px | 56px | -2.88px | 600 |
| `text-heading-40` | 40px | 48px | -2.40px | 600 |
| `text-heading-32` | 32px | 40px | -1.28px | 600 |
| `text-heading-20` | 20px | 26px | -0.40px | 600 |
| `text-heading-16` | 16px | 24px | -0.32px | 600 |
| `text-heading-14` | 14px | 20px | -0.28px | 600 |

`text-heading-24` 那一行在页面上和相邻示例共用容器，选择器取到的是 40px 那一枚，
所以 24 这一档的行高与字距**未取得**；档位本身在表里明确存在。字距一律是 -0.04em
（72px 档是 -0.06em），行高在 32px 及以上收敛到 1.0–1.25。

## Label 与 Copy 的分工

`text-label-14` 被标为「最常用的文字样式，用在大量菜单里」，`text-copy-14` 被标为
「最常用的正文样式」。区别只在行高：label 20px，copy 20px，但 copy 在 16px 以上
明显更松（`text-copy-18` 是 18/28，`text-label-18` 是 18/20）。数字用 `text-label-13`
并开 tabular。

## Peach 的采用与差异

- 页面标题（`.pagetitle`/`.listtitle`/`.managetitle`/`.index .ihead h2`/`.playlistpage h2`）
  取 Heading 32：32px、600、行高 1.25（= 40px）。
- 窄屏两级往下收：`max-width:760px` 走 Heading 24 那一档（`--fs-2xl`），
  `max-width:640px` 才到 Heading 20（`--fs-xl`）。
- **字距不照抄**：Geist 的 -0.04em 是给拉丁字母调的，压在中文标题上会把字挤成一团，
  Peach 保留 -0.01em。这是本仓库对该规范唯一的显式偏离。
- 标题外边距 Geist 没有规定，Peach 自己定为下 20px（索引页与关注页记在
  `.ihead`/`.followhead` 上，三处同值）。

最后复核：2026-09-04
