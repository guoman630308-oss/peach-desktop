# STRM 与挂载来源参考

核对日期：2026-10-17。本文件汇总多个来源与本机实测，无法对应单一可重抓快照，因此不登记进 `docs/reference-sources.json`。
源码链接固定 revision；产品文档仅作行为说明，没有逐字复制或引入运行依赖。

## 实测证据

| 来源 | 版本与实际读取位置 | 结论 |
| --- | --- | --- |
| [SmartStrm](https://github.com/Cp0204/SmartStrm/tree/01e9318d932187c65d485ecac5bcb46cbd2196bf) | `01e9318d932187c65d485ecac5bcb46cbd2196bf` 的仓库树、README、NOTICE；[FAQ](https://smartstrm.github.io/help/faq)、[代理说明](https://smartstrm.github.io/settings/proxy) | 核心闭源，实现源码未取得。文档说明稳定解析地址、OpenList 签名与播放时 302；不能据此声称复现其内部实现。 |
| [OpenList](https://github.com/OpenListTeam/OpenList/tree/3b28270a4066ea52943a69ecddd850d83f7e8bdc) | `drivers/strm/driver.go`、`drivers/strm/util.go`；AGPL-3.0 | 列表生成指针和附属文件，播放按需解析目标。复用稳定指针与解析时机的语义，不复制驱动代码。 |
| [Jellyfin](https://github.com/jellyfin/jellyfin/tree/dbb1f5dd5d0c73825a511a91aef051b254c637cb) | `MediaBrowser.Providers/MediaInfo/ProbeProvider.cs`、`Emby.Server.Implementations/Library/MediaSourceManager.cs`；GPL-2.0 | STRM 是文本入口，读取首个非空、非注释行；播放与媒体探测是独立阶段。Peach 主动拒绝多个目标，避免歧义。 |
| [Kodi STRM 文档](https://kodi.wiki/view/Internet_video_and_audio_streams) | Internet video and audio streams | 文本 URL、NFO 与海报可分别存在，指针不等于媒体本身。 |
| [CloudDrive2 支持列表](https://www.clouddrive2.com/en/features.html) | Storage supported today；光鸭支持的版本记录 `v1.0.11 / f58306f` | OneDrive 与光鸭可由外部工具挂载；光鸭版本记录注明只读。Peach 不引入网盘登录或上传 SDK。 |
| [OpenList OneDrive](https://doc.oplist.org/guide/drivers/onedrive) | OneDrive 驱动说明；光鸭驱动参考 `v4.2.5 / cc87e88` | 平台登录、分享链接与目录访问交给驱动；Peach 使用已经挂载的文件系统路径。 |

## Peach 的采用范围

STRM 仅接受一个 HTTP(S) 地址，支持 UTF-8 BOM、空行和注释。扫描只登记本地指针，播放才发起上游请求；签名参数保留原始编码。HTTP Range 与 HLS 使用既有 HTTPX 传输、播放器计划和 HLS 解析库，浏览器只取得 Peach 的鉴权地址。映射有容量与有效期，不持久缓存正片。

本地 OpenList、CloudDrive 地址可以作为指针初始来源；重定向和播放列表只能继续访问同一来源，或经公网 HTTPS 检查的目标。浏览器 Cookie 和共享 HTTP 客户端的认证信息不转发给上游。错误回执不包含签名地址。

STRM 与 ISO 不参与后台探测、九宫格或时间轴抽帧。ISO 只登记与管理，并提供外部播放器入口；未声称浏览器支持 ISO、BDMV 导航或多片段原盘。

「生成视频预览」控制九宫格与时间轴抽帧，保留普通媒体按来源策略执行的 ffprobe 探测。关闭后停止时间轴任务，重新开启总开关不自动开启时间轴采集。时间轴间隔仍由用户选择。

新增网盘目录由用户明确选择「正常读取」或「代理流量保护」，不按网盘品牌预选，PikPak 同样适用。既有配置保留当前读取策略，文件写入能力独立判断。通用「其他已挂载网盘」只承诺文件系统读取，不承诺每家平台的原生 API、上传、分享或回收站功能。

## 代理与成本证据

[光鸭官网](https://www.guangyapan.com/)面向大陆提供第三方应用直连能力，真实账号下载路径未取得。[微软网络说明](https://learn.microsoft.com/en-us/microsoft-365/enterprise/microsoft-365-networking-china?view=o365-worldwide)区分国际租户跨境拥塞与世纪互联环境；[OpenList 驱动](https://doc.oplist.org/guide/drivers/onedrive)按账号类型选择应用端点，并提供独立的下载反代设置。登录可达不等于文件下载直连；不能仅凭网盘名称推断代理费用。曾按品牌为新增网盘选择默认成本的判断不成立，最终使用用户明确选择的目录策略。

本机匿名 HEAD 核验中，显式绕过客户端代理的光鸭官网返回 200，`login.live.com` 返回 404，`graph.microsoft.com` 返回 405，OneDrive 个人网页版连接超时。404、405 只证明公共端点可达，不代表认证或业务成功。FlowLens 快照未观测到这些短连接，不能用它证明系统隧道路径、零代理流量或全大陆可用性。无网盘登录、真实媒体读取或路由配置改动。

## 验证边界

自动回归使用临时账本、合成文件、本机 HTTP 服务与模拟上游，验证登记不预读、真实 Range 206、签名重定向、HLS 子资源鉴权及预览开关。真实 OneDrive、光鸭账号及挂载未取得；真实云端端到端兼容性未验证。SmartStrm 核心源码未取得。
