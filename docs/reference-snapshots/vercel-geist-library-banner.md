# 首页处理进度 Banner

- 日期：2026-09-06；URL：<https://vercel.com/geist/banner>。
- Chrome 读取实际 DOM、getComputedStyle；渲染 HTML SHA-256：`06785ff700e2649130de056264061a22c0389045514cf9ae3c63b44a3ec6cbb3`。
- 桌面示例容器：`flex items-center justify-center gap-3 p-4`，内边距 16px，间距 12px，正文 16px；跳转按钮 `--height:32px`、`rounded-full`、14px 字体，锚点无下划线。
- 小屏上游收为带右箭头的整枚胶囊；Peach 保留阶段全文、独立跳转按钮和换行，方便显示真实进度。背景复用 Peach 中性色，无渐变宣传装饰。
- 执行环境中的 Chrome 两次返回 `ERR_NETWORK_ACCESS_DENIED`，插件入口超时；允许联网的执行环境重试成功。先前“外观未取得”记录已由本次实测补齐。
- 人工取证笔记，不在 reference-sources.json 登记；它不是可由上游文本覆盖的快照。
