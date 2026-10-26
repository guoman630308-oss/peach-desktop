# ADR-0014：标准 Range、Video.js 与分片播放边界

- 状态：已接受（HLS 默认化部分已被 ADR-0016 撤销）
- 日期：2026-08-15
- 取代说明（2026-08-29 补记）：决策里「2026-08-17 起，已知时长的 115/PikPak 原生 MP4 由
  `MediaEngine.stream_plan` 选择 HLS VOD」一段已被 ADR-0016 撤销：远端 MP4 默认恢复标准
  Range `/stream`，HLS 改为 `/api/stream-plan?mode=hls` 显式按需模式。其余标准 Range 语义、
  Video.js 选型与 session 取消边界仍然有效。

## 背景

Peach 为避免 CloudDrive 对开放区间一路读取，曾把远端 `Range: bytes=0-` 的响应人工截成
32 MiB。总文件长度虽仍写入 `Content-Range`，响应却不再忠实满足客户端请求。Firefox 在
item 29297 上把这种响应表现为总时长随已下载内容增长；文件本身的 `moov/mvhd` 位于开头，
账本与容器都给出 28,639.916 秒，因此不是坏文件或尾部元数据问题。

## 决策

- `/stream` 对本地和远端统一使用 Starlette 标准 Range 语义；session 只负责取消同一次详情
  播放的活动请求和拒绝迟到请求，不改写客户端请求范围。
- 详情播放器复用本地固定版本 Video.js 8.23.9（Apache-2.0），不使用 CDN。控制栏的权威总
  时长来自 ledger；浏览器报告值只有在 ledger 缺失时才回退使用。
- 当前直接 MP4 统计显示播放位置、缓冲秒数、分辨率和丢帧。正式 HLS/DASH 落地后复用
  Video.js 内置 VHS 的带宽、请求和传输字节统计。
- CloudDrive 的约 100 MiB 固定块预取属于来源层成本。播放器皮肤不能消除它；后续由 Media
  Engine 提供真实短分片或直连来源 adapter，不能再次用非标准 Range 截断冒充分片。
- 2026-08-17 起，已知时长的 115/PikPak 原生 MP4 由 `MediaEngine.stream_plan` 选择 HLS VOD：
  `/api/stream-plan` 返回 6 秒清单，TS 片段按请求由 FFmpeg seek/remux 生成并在响应后清理；
  HLS 生成失败时前端回退标准 `/stream`。本实现暂不提供自适应码率或多路清单。

## 放弃方案

- 继续保留 32 MiB 人工截断：协议行为不一致，且已在 Firefox 产生错误总时长。
- 只换播放器外观：不能解决来源层预取，也无法保证 Range 正确。
- 自研完整播放器：重复实现可访问性、控制栏、移动端适配和 HLS/DASH 统计，维护成本过高。
- 当前直接引入 Shaka/hls.js：现阶段主要输入仍是直接 MP4；Video.js 同时覆盖当前 HTML5 和
  后续 VHS，避免两套控制层。

## 后果

直接 MP4 恢复标准客户端兼容性，关闭详情仍能由 session 主动终止服务端请求；HLS 片段也纳入
同一取消机制。HLS 把大跨度 seek 的读取范围限制在目标片段附近，但每次来源读取仍受
CloudDrive 固定块预取影响，不能宣称本 ADR 消除了来源层成本。
