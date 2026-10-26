# ADR-0016：远端 MP4 默认标准 Range，HLS 改为按需模式

- 状态：已接受
- 日期：2026-08-18

## 背景

- 2026-08-18 实测 asset 22716（115 的 HEVC 重制 MP4）在 `/item/22716` 黑屏：HLS 分片全部 200
  返回、111 MB 进入缓冲、时间轴推进，但 `videoWidth=0`、解码帧数为 0、无任何 error 事件。
  ffprobe 确认源与分片都是 `hevc`；同浏览器实测
  `MediaSource.isTypeSupported('video/mp2t; codecs="hvc1…"')` 为 `false`：Chromium 的 MSE
  管线不支持 MPEG-TS 里的 HEVC，`-c copy` 分片把 HEVC 原样装进 TS，数据能进缓冲却一帧都解
  不出来，因此是静默黑屏而不是报错。文件名含 `HEVC` 的 115 视频有 248 条（PikPak 2 条），
  全部受同一缺陷影响。
- 速度实测：挂载文件 16 MB 头部读取 0.02 秒、中段 8 MB 0.01 秒、单个缓存热分片生成 0.12 秒；
  同浏览器直接 Range 播放该 HEVC 文件可正常出帧（113 帧、1920×1080、无错误）。用户确认
  CloudDrive 与资源管理器里的本地播放器加载明显快于 Peach。HLS 路径的固定开销是每次进入
  新区域都要起一个 FFmpeg 进程做 seek/remux，再经 VHS transmux 解码；Range 路径浏览器直接
  读挂载文件，行为等同本地播放器。
- ADR-0014 引入 HLS 的动机是「大跨度 seek 只读取目标片段附近」，但每个分片仍会触发
  CloudDrive 约 100 MiB 固定块预取，HLS 并不能消除来源层成本，只限制响应字节；在默认场景下
  它换来的是首帧更慢和上述 HEVC 黑屏，不再划算。

## 决策

- 远端（115/PikPak）的 `.mp4`/`.m4v` 默认返回标准 Range 计划（`protocol=range`），浏览器直接
  通过 `/stream` 播放挂载文件，与本地播放器同路径；不再默认走 6 秒 HLS。
- HLS 保留为按需模式：`/api/stream-plan?mode=hls` 且片源时长已知时才返回 HLS 计划，供流量
  优先场景（PikPak 计费、代理预算紧张）显式启用。播放列表、TS 分片、关键帧对齐、`-copyts`
  和分片缓存端点与实现全部保留，不改写。
- 不引入服务端编解码探测：`stream-plan` 不逐文件 ffprobe（每次约 25 MB 来源读取，且服务端
  知道编码也推不出客户端的解码能力）。Range + MP4 是浏览器标准行为，能解码的容器自然可播；
  不支持 HEVC 的浏览器会得到明确的媒体错误，而不是静默黑屏。
- 不为 HEVC 引入代码级转码回退。后续确有客户端无法解码 HEVC 时，再按代码级（H.264）转码
  单独决策，本 ADR 不做。

## 放弃方案

- 继续默认 HLS：HEVC 黑屏没有修复路径，且每次进入新区块都有 FFmpeg 启动与 remux 开销。
- 服务端 ffprobe 选路：把「能不能播」的判定放错层级；浏览器的能力才是判决，探测既慢又不可靠。
- 让 HLS 分片转码成 H.264：每段都要完整解码再编码，CPU 成本与分片粒度冲突，暂不需要。

## 后果

- 首帧路径缩短为一次标准 Range 请求；CloudDrive 的块预取仍在来源层，本 ADR 不宣称消除它。
- `stream-plan` 的默认返回值改变，前端已有「非 HLS 计划直接走 `/stream`」的回退路径，无需改动。
- HLS 相关端点、缓存与测试全部保留，改为显式 `mode=hls` 后继续生效；默认计划、按需计划和
  播放列表三级都有测试。生产重启后需用真实 HEVC 资产验证出帧，不能用静态测试代替。

## 落地与验证（2026-08-18）

`MediaEngine.stream_plan` 新增 `mode` 参数，默认 `auto` 返回 Range，只有 `mode="hls"` 才走
分片；`/api/stream-plan` 透传该参数，`_hls_plan` 显式以 `mode="hls"` 取计划，所以播放列表和
分片端点不受默认值影响。

生产重启后用 asset 22716（`hevc`、`hev1` tag、1920×1080、746.965 秒）复验：

- 默认计划返回 `protocol=range`、`src=/stream?id=22716`；`mode=hls` 仍返回 76 个分片的 HLS
  计划；`/stream/hls/22716/index.m3u8` 返回 200。三级契约都成立。
- 详情页取到的是 `/stream?id=22716`，`readyState=4`、`videoWidth=1920`、`videoHeight=1080`。
- 把当前帧画到 canvas 实测：平均亮度 147.1、非黑像素 99.3%、峰值 253.4，不是黑屏。
- 同一浏览器独立复核了本 ADR 的前提：`MediaSource.isTypeSupported('video/mp2t; codecs="hvc1…"')`
  为 `false`，而 `video/mp4; codecs="hvc1…"` 为 `true`；直接 Range 播放该文件解码 997 帧、
  丢 8 帧、无 error，拖动到 30 秒后继续正常出帧。前提与结论都成立。
