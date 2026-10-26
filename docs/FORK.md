---
kind: reference
---

# PeachDesktop 修改版说明

本项目是面向 Windows x64 的 Peach 独立窗口定制版，由 [guoman630308-oss](https://github.com/guoman630308-oss) 维护。修改声明日期为 2026-10-26。

原项目为 [peach-mitao/peach](https://github.com/peach-mitao/peach)，原版权为 Copyright (C) 2026 longmeidao。应用代码保留 [AGPL-3.0-or-later](../LICENSE)，来源声明见 [NOTICE](../NOTICE.md)。本 Fork 独立维护，问题入口为[本 Fork Issues](https://github.com/guoman630308-oss/peach-desktop/issues)。

## 用途与边界

PeachDesktop 在本机管理影片、人物、番号、标签和观看相关资料，支持已挂载为本地目录的媒体。Windows 窗口由 WebView2 承载，托盘持有本机服务，日常浏览无需完整浏览器窗口。原版的联网来源、关注与云下载入口保留，由使用者配置启用。

普通本地视频可入库和播放；自动匹配依赖文件身份与来源覆盖。当前资料体系以 Peach 的影片、番号与人物关系为基础。通用电影、剧集、季号、集号与合并集的数据模型属于后续规划。

## 源码对照基线

上游版本入口为 [v0.46.1](https://github.com/peach-mitao/peach/tree/v0.46.1)。本说明的定制模块对照还核对了上游主干快照 [de22796c04919aaa057d77930c33c81313765569](https://github.com/peach-mitao/peach/commit/de22796c04919aaa057d77930c33c81313765569)。上游自带能力及上游后续演进不计作本 Fork 独立新增功能。

## 定制模块

| 部位 | 本 Fork 的实现 | 主要源码与说明 |
| --- | --- | --- |
| Windows 外壳 | WebView2 窗口、隐藏到托盘、重新打开、退出及本机服务所有权；外部网站交给系统浏览器 | `src/peach/native_shell.py`、`src/peach/tray.py`、[窗口说明](NATIVE_WINDOW.md) |
| 窗口分发 | 独立程序标识、窗口启动入口、安装包及窗口包更新资产校验 | `scripts/build_windows.ps1`、`scripts/installer/native-window.iss`、`src/peach/release_updates.py` |
| 本机外部播放器 | 发现 PotPlayer、选择程序与默认播放方式；本地路径作为独立参数传入，失败可重试或切换内置播放 | `src/peach/local_player.py`、`frontend/src/react/settings/local-player-settings.tsx`、`frontend/src/react/stage/local-player.tsx` |
| 外部播放预览策略 | 外部模式下不为本地卡片启动悬停视频预览；偏好尚未取得时保持保守的预览策略 | `frontend/src/local-player.ts`、`frontend/src/card-art/hover.ts` |
| 单文件入库 | 从已配置媒体目录选择单个视频，仅对该文件登记、探测与采集，重复导入复用原记录 | `frontend/src/react/library-processing/single-file-import-dialog.tsx`、`src/peach/web_library_processing.py` |
| JavLibrary 资料 | 搜索与详情解析、完整番号校验、标题/人物等字段映射、封面与来源记录；验证由使用者在浏览器完成 | `src/peach/sources/javlibrary.py`、`src/peach/metadata_routes.py`、[复用依据](REUSE.md#javlibrary-浏览器来源) |
| 来源与采集边界 | JavLibrary 进入既有社区来源链；人工验证等待计入可豁免时间；已改变文件路径的旧候选不阻止重新采集 | `src/peach/community_catalog.py`、`src/peach/library_processing.py`、`src/peach/scraping_access.py` |
| 资料目录与 Windows 选取器 | 窗口资料落点和重定向复用原版目录机制，文件选取适配 Windows 路径 | `src/peach/settings_file.py`、`src/peach/folder_picker.py` |
| 返回与阅读位置 | 返回按钮、按历史条目保存名册页数与滚动位置、返回时恢复筛选和已加载内容 | `frontend/src/history/index.ts`、`frontend/src/react/index/index-data.ts`、`frontend/src/react/index/index-page.tsx` |
| 侧栏外观与高亮 | 返回图标无常态边框，颜色复用侧栏开合按钮；灰色高亮标记当前选中项，悬停及离开保持落点 | `frontend/src/application/chrome.js`、`frontend/src/react/sidebar/sidebar-island.tsx`、`frontend/src/react/sidebar/sidebar.css` |

## 资源使用与性能

- 外部播放把解码交给外部播放器，本地文件不经过 Peach 播放转码。外部模式下的悬停策略减少浏览列表时的转码缓存、磁盘写入和后台解码。
- 单文件入库将处理范围限定为一个已选文件，适合向既有媒体目录新增少量视频。
- 返回名册复用有效页面缓存、已加载页数与筛选，保持阅读位置；缓存失效时按已加载范围重新取得内容。
- 资料采集复用原版来源路由、冷却、快照与增量处理机制。人工验证等待不挤占后续来源查询的有效预算；路径变化后的候选重新按当前文件身份处理。

原版已有的扫描队列、受限并发、媒体属性缓存、FFmpeg 处理与断点续跑仍归上游架构。本 Fork 没有统一硬件与馆藏的性能基准，以上说明实现机制，不给出扫描速度倍数。

## 数据、播放与联网

安装程序、资料目录与原始媒体目录分别管理。窗口默认资料目录为 `%LOCALAPPDATA%/PeachDesktop/peach-data`，首次设置可选择其他目录；卸载保留资料的行为见[窗口说明](NATIVE_WINDOW.md)。原始视频不因更换资料目录自动搬移。

外部播放不向 Peach 回传进度、次数与观看时长，PotPlayer 自行维护播放状态。手动选择内置播放及专用沉浸模式沿用原版播放器。扫描预览、封面、浏览器站点存储和内置转码分别产生各自缓存，不能把外部播放策略理解为整个程序没有缓存。

JavLibrary、其他资料站与网盘服务需要有效网络和相应访问权。来源链会按作品身份、错误和冷却状态尝试适合的来源；每个站点的收录范围不同。地区限制、登录、人机验证和站点停服仍可能阻止采集。使用者手动完成验证，程序不代替人机验证。

## 已知边界

- 当前重点验证 Windows x64；macOS 与 Docker 代码入口来自上游，需针对修改版自行验收。
- 外部播放器不回传观看状态，无法用播放器关闭事件推断已看完。
- 返回历史再次进入视频详情时可能重新触发外部播放；侧栏外观与名册位置恢复使用当前导航机制。
- 窗口更新只接受配套 `PeachDesktop-<版本>-windows-x64.zip` 资产；没有窗口版资产时使用窗口安装包。发行页实际制品是下载依据。
- Windows 安装包未代码签名；WebView2、可选采集桥及来源凭据要求见[窗口说明](NATIVE_WINDOW.md)。

## 许可与再分发

原项目的 [LICENSE](../LICENSE) 为 AGPL-3.0-or-later。本 Fork 保留原版权与许可全文，修改声明和相关日期位于 README、NOTICE 与本文件；新增修改同样按该许可证提供。

分发修改源码时保留版权、许可及无担保声明，清楚标注修改版。分发可执行制品时，同时提供对应版本的可修改源码、构建与安装脚本，并在下载处给出明确的免费源码入口。若让他人通过网络与修改版交互，应向访问者显著提供对应版本的免费源码获取方式。上述要求依据许可证第 4、5、6、13 节；完整条件以许可证正文为准。

第三方库、字体、图标、商标和工具保留各自的权利与许可，不因本 Fork 的 AGPL 声明变更授权。相关清单和来源见 [REUSE](REUSE.md)、[窗口第三方许可](NATIVE_WINDOW.md#第三方许可) 及各目录的 `ORIGIN.md`、`LICENSE`；FFmpeg 等工具的二进制再分发还须随附各自要求的许可和对应源码。

仓库只提供程序源码与必要的项目资源。个人媒体、海报缓存、数据库、观看记录、Cookie、令牌、私钥、本机配置、运行日志和构建缓存不进入公开提交。公开说明与测试样例使用合成数据或中性路径。
