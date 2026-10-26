# ADR-0078：booru 帖子按出处账号署名发布者

- 状态：Accepted
- 日期：2026-09-26

## 背景

rule34.xxx 把动画、模型、场景、配音的作者都标成 artist。按 artist 标签关注一位创作者时，
流里会混进别人用他的模型做的作品，卡片却署着被关注者的名字。例如 17070008 挂着
2hour2、billyhhyb、madruga3d 等五位 artist，出处是 `x.com/2hour2hour`，实际作者是
2hour2，billyhhyb 只提供了模型。

按出处把这类帖子隐藏的做法被否决：LazyProcrastinator 与 InitialA 的配音版同样由配音者
发布，会一起被藏掉。用户选择保留，只把署名改对。

## 决策

`web_follow._item_credit` 在读时计算，不写 ledger。rule34xxx、rule34paheal 的帖子同时满足
以下条件时，条目 payload 带 `credit: {poster, credited}`：

1. 出处第一个网址能认出账号：X、Twitter、Patreon、SubscribeStar、Pixiv、Ko-fi 取路径首段，
   Bluesky 取第二段，fanbox 取子域名。
2. 被关注者本人也在 artist 标签里。
3. 账号对上一个 artist 标签，而且对上的不是被关注者。只比字母数字；四个字符以上的前缀
   也算同一人（`2hour2hour` 对 `2hour2`）。同一人挂了几个写法时，取与账号完全一致的，
   其次取最长的。

卡片与详情的作者名换成 `poster`。也关注了这位就用他的来源名和头像，否则只显示首字母，
不借用被关注者的头像。下面多一行「署名含 `credited`」，说明这条为什么出现在这个流里。
认不出账号、对不上标签的帖子照常署被关注者，不猜。

## 依据

2026-09-26 在真实 ledger 上只读试算，4826 帖里 201 帖改署名。最多的是配音版：
LazyProcrastinator ← ecchiwaffle 38 帖、InitialA ← evilaudio 27 帖。17070008 算出
`{poster: 2hour2, credited: billyhhyb}`。

## 后果

- 筛选、分组、关注来源都不变：这些帖子仍在被关注者的流里，按作者筛照旧能筛到。
- 出处没写或写的是站内链接的帖子无法区分，多位 artist 的帖子里约一半属于这种情况。
- 配音者发布的版本同样署配音者，与动画作者本人发布的版本分别署名。
