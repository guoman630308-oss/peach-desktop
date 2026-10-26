# ADR-0007：在线追更首先复用 RSS/Atom 协议

- 状态：Superseded by ADR-0019（边界原则仍有效）
- 日期：2026-08-14
- 取代说明（2026-08-29 补记）：本 ADR 的**实现选择**已被推翻，ADR-0019 记录了七个实际
  来源实测无一提供 feed，站点连接器取代了 `FeedAdapter`。同日删除 `FeedAdapter`、
  `FeedSnapshotStore` 及其专属 DTO 与 `feedparser` 依赖；`follow.py` 只剩连接器和存储层
  在用的错误类型与工具函数。下面这些**边界原则仍然有效，由连接器继承**：只在显式调用时
  联网、条件请求与有界读取、URL 不得内嵌凭据、原始证据只写一次、候选带出处且不直接写
  真相字段。读正文时把「`FeedAdapter` 负责」理解为「连接器负责」。

## 背景

Peach 把在线追更列为正式能力，但尚无 connector。直接从站点 HTML、私有接口或浏览器状态开始，会把站点易变结构、认证、抓取和 ledger 写入耦合在一起。

## 决策

- 第一类追更 connector 复用 RSS 2.0/Atom 公开协议，由 `FeedAdapter` 统一输出稳定候选 DTO。
- 网络请求只能由显式调用触发；服务启动、health 和普通浏览均不自动联网。
- adapter 支持 ETag/Last-Modified 条件请求、15 秒超时、5 MiB 有界读取，以及 RSS enclosure/Atom enclosure 媒体候选。
- URL 只允许无内嵌凭据的绝对 HTTP(S) 地址；凭据不得写入 URL、日志或 ledger。
- adapter 只发现候选，不自动写 ledger 真相。`FeedSnapshotStore` 把不可变原始响应与规范化旁车存入 `peach-data/sources/follow`，把条件请求游标存入 `peach-data/state/follow`；后续通过带 source/provenance/review 的导入边界写入。
- 无 feed 的站点以后实现独立 connector；不得把站点专用 HTML 规则塞进通用 feed adapter。

## 后果

Peach 获得可测试、低耦合的首个在线追更边界，并复用成熟协议；具体 feed 配置、调度和 reviewed import 仍需下一阶段实现。当前没有对真实站点发起追更请求，也没有修改 ledger。

## 拒绝方案

- 服务启动时自动轮询所有来源。
- 用浏览器 Cookie 或 URL 内嵌账号密码抓取。
- 解析结果直接创建/覆盖资产。
- 为首个 connector 引入独立抓取服务或消息队列。
