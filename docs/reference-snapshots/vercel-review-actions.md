# 复核操作与折叠

人工取证笔记，React 页面运行后的 DOM 与计算样式不能作为可重抓的源字节，不登记进 `docs/reference-sources.json`。核验于 2026-09-06。

- 官方入口：https://vercel.com/geist/button 、https://vercel.com/geist/select 、https://vercel.com/geist/collapse 。
- 本机 Chrome 取得三页 DOM 与控件计算样式；原始资料归档于 `<私有验证记录>`。
- Button 示例高度 32／36／40px；默认主动作深底浅字，错误动作使用红色。Select 用于固定列表的单项选择，Collapse 用于展开正文。
- Peach 复用已有 Button、Select 和 `wireCollapse`。批量操作独立于分类 Tabs，统一来源只选择候选，通过按钮提交；拒绝使用危险动作色，跳过使用已有提示色。

- 本次补核官方 https://vercel.com/geist/badge ：Badge 表达静态状态信息；将「已选 N 项」放入中性 Badge 是 Peach 的布局选择，并非官方指定的计数控件。
- Button 页面 DOM SHA-256：`12350909671ae8d5c6f2a357b3bce2ae3494ba2901394cb6d25f04655e444793`。32px 按钮采用居中布局；主操作、警告和错误使用各自语义色，禁用态使用中性色。
- 本次 DOM、计算样式和隔离浏览器验证归档于 `<私有验证记录>`。

- 2026-09-06 复核 https://vercel.com/geist/fieldset ：卡片支持底部操作区。Peach 将复核操作组右对齐，保持原有按钮顺序；这是本地布局选择。来源图片与缺图占位使用等高 220px 区域、名称勾选间距 8px，均为 Peach 的资料核对布局，非官方图像尺寸规范。验证归档于 `<私有验证记录>`。

- 2026-09-06 复核 https://vercel.com/geist/menu ：官方提供 MenuSection 分区、点击打开、键盘导航和按视口定位。Peach 复用现有 Select 的 Listbox 协议，以分组方式和分类两个控件表达层次，字段选项置于具名分区；这是本地筛选设计，不声明复刻官方级联菜单。内置浏览器首轮返回 `js execution timed out`，备用路径返回 `net::ERR_NETWORK_ACCESS_DENIED`；允许联网的 Chrome 重试取得 DOM，SHA-256 为 `03c22ae1bc6f0be36a3e4ac11fc40d2adc4014ed324669fb5f3b26926b6cde06`。原始 HTML 和截图归档于 `<私有验证记录>`。
