<p align="center">
  <img src="resources/peach-logo.png" alt="Peach" width="96">
</p>

<h1 align="center">PeachDesktop</h1>

<p align="center">基于 Peach 的 Windows 独立窗口定制版：管理本地影片，使用 PotPlayer 或内置播放器播放。</p>

> **来源与修改声明 · 2026-10-26**：本项目基于 [peach-mitao/peach](https://github.com/peach-mitao/peach)，原作者 Copyright (C) 2026 longmeidao，由 [guoman630308-oss](https://github.com/guoman630308-oss) 独立维护修改版。应用代码继续采用 [AGPL-3.0-or-later](LICENSE)，第三方组件保留各自许可。源码、修改范围和许可说明见 [Fork 说明](docs/FORK.md) 与 [NOTICE](NOTICE.md)。

<p align="center">
  <a href="https://peach.video">原项目官网</a> ·
  <a href="https://github.com/guoman630308-oss/peach-desktop/releases">本 Fork 发行页</a> ·
  <a href="https://demo.peach.video">原项目演示</a> ·
  <a href="https://github.com/peach-mitao/peach/releases/tag/intro-video">原项目介绍视频</a> ·
  <a href="https://github.com/guoman630308-oss/peach-desktop/issues">本 Fork 问题反馈</a> ·
  <a href="https://github.com/orgs/peach-mitao/projects/7/views/1">需求看板</a> ·
  <a href="https://github.com/orgs/peach-mitao/projects/7/views/3">路线图</a> ·
  <a href="#文档">文档</a> ·
  <a href="README.en.md">English</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0--or--later-blue?logo=gnu&amp;logoColor=white" alt="license"></a>
  <a href="docs/TESTING_DESKTOP.md"><img src="https://img.shields.io/badge/Windows-x64-0078D4" alt="Windows x64"></a>
  <a href="docs/OPERATIONS.md#macos-独立测试包"><img src="https://img.shields.io/badge/macOS-DMG-555555?logo=apple&amp;logoColor=white" alt="macOS 独立应用构建与安装"></a>
  <img src="https://img.shields.io/badge/18%2B-adult%20content-critical" alt="18+ 成人内容">
</p>

https://github.com/user-attachments/assets/a5610874-e611-41bb-be22-7d99f0d64b62

> **18+** 仅供成年人管理自己的成人内容馆藏。仓库不含媒体或站点数据；连接外部站点需有相应账号和访问权。

## 本 Fork 的定制与优化

- **Windows 独立窗口**：WebView2 承载 Peach 界面，窗口、托盘和本机服务一起工作。主界面无需完整浏览器，网站登录和人工验证使用系统浏览器。
- **PotPlayer 默认播放**：自动发现 PotPlayer，也可指定播放器程序；本地文件交给外部播放器，播放区保留「内置播放」。外部播放不回传观看进度。
- **按播放方式控制预览**：外部播放模式下，本地视频卡片不启动悬停视频预览，减少浏览列表时的转码和缓存。主动选择内置播放时沿用原版播放流程。
- **单个视频入库**：在已配置目录中选择一个视频，只对该文件入库、读取属性并采集资料。
- **JavLibrary 来源**：按番号查询作品资料与封面，复用人工验证会话；来源切换沿用原版来源链、冷却和身份校验。
- **页面返回与侧栏**：名册返回保留筛选、已加载页和滚动位置；返回图标无常态边框，灰色高亮固定在当前选中的菜单。

本地扫描、人物与番号关联、标签、收藏、关注、云下载和内置播放器来自原项目。完整模块对照、资源使用策略及已知限制见 [Fork 说明](docs/FORK.md)。

本 Fork 重点验证 Windows x64 独立窗口。通用电影、剧集、季集的数据模型仍属后续规划；当前影片资料继续使用 Peach 的影片、番号与人物体系。

## 能做什么

以下主要能力沿用原项目；联网来源、订阅与下载按设置启用。

- **本地、网盘一起看**：硬盘与已挂载的 115、PikPak、OneDrive 等网盘汇成一个馆藏。
- **播放墙挑片**：拖动全屏封面墙，点开原位播放；同一番号的不同版本与分卷集中切换。
- **播放器字幕**：加载本地字幕，按番号搜索 SubtitleCat 简繁中文字幕并选择加载，调整字体、字号、颜色与背景。在线搜索无需 API Key，来源可能包含机翻。
- **接手已有收藏**：直接读取 NFO 和海报，缺少的标题、女优、厂牌与封面可从多个来源补齐，保留手动修改。
- **跨站关注**：同一创作者在 FANBOX、Patreon、Kemono 等站点的更新汇到一起，同一作品合成一张卡。
- **追新作、找想看的**：按女优订阅新作，按口味发现作品，用「想要」清单留住待找的片子。
- **云下载**：按番号查找资源或粘贴磁力，提交到 115 或 PikPak，落地后自动整理入库。
- **浏览器添加 JAV**：在 JavDB、MissAV、Jable 等站点识别或选中番号，一键交给 Peach 云下载入库。[用户脚本](https://github.com/peach-mitao/peach-userscript)独立维护，见[安装与配对](docs/JAV_BROWSER.md)。
- **口味**：结合观看、反馈、馆藏、浏览历史、书签与关注关系分析偏好。一次读取本机浏览器的历史和书签，也可导入 HTML 书签或 Chromium Bookmarks JSON；来源可停止读取或删除。见[口味数据来源](docs/TASTE_SOURCES.md)。

观看记录、收藏和设置保存在本机。扫描不动原文件；手动整理先预览，可撤回上一批。

扫描、关注更新和新作资料采集会保存进度，应用重启后继续处理未完成的项目。恢复范围见[断点续跑](docs/OPERATIONS.md#长时任务断点续跑)。

「发送版本统计与错误诊断（推荐）」默认勾选，可在安装、首次设置或设置页取消。两类技术事件由同一开关控制，已保存的关闭选择会保留。启用后向 Sentry 发送页面列出的技术字段，不自动上传完整日志、诊断 ZIP、媒体信息或观看记录。见[远程诊断说明](docs/DIAGNOSTICS.md#可选远程诊断)。

馆藏与口味明细上传在安装器和设置页另设独立选项，默认关闭。设置页先展示完整字段与本机预览，经明确同意后，每周上传作品、标签、评分、观看汇总及全部偏好维度的明细；不含账号、设备或安装标识。明细仍有跨次关联与再识别风险，接收服务可见连接 IP。见[馆藏与口味群体研究](docs/DIAGNOSTICS.md#馆藏与口味群体研究)。

## 截图

以下视频和截图来自原项目演示，展示沿用的馆藏界面；独立窗口行为见 [Windows 独立窗口](docs/NATIVE_WINDOW.md)。

[在线演示](https://demo.peach.video) 可直接体验搜索、筛选和资料页，操作不会保存。点击截图查看原图。

<table>
  <tr>
    <td width="50%"><a href="https://peach.video/assets/screenshots/home-20261014.png" target="_blank" rel="noopener"><img src="https://peach.video/assets/screenshots/home-20261014.webp" alt="馆藏：首页筛选与作品卡片" width="600"></a></td>
    <td width="50%"><a href="https://peach.video/assets/screenshots/profile-20261014.png" target="_blank" rel="noopener"><img src="https://peach.video/assets/screenshots/profile-20261014.webp" alt="女优页：人像、资料与全部作品" width="600"></a></td>
  </tr>
  <tr>
    <td align="center">馆藏：按人物、来源和标签找片</td>
    <td align="center">女优页：资料、别名和全部作品</td>
  </tr>
  <tr>
    <td><a href="https://peach.video/assets/screenshots/follow-20261014.png" target="_blank" rel="noopener"><img src="https://peach.video/assets/screenshots/follow-20261014.webp" alt="关注：创作者导航与图片更新" width="600"></a></td>
    <td><a href="https://peach.video/assets/screenshots/stats-20261014.png" target="_blank" rel="noopener"><img src="https://peach.video/assets/screenshots/stats-20261014.webp" alt="统计：馆藏、存储与内容分布" width="600"></a></td>
  </tr>
  <tr>
    <td align="center">关注：创作者的跨站更新</td>
    <td align="center">统计：片子存在哪、看了多少</td>
  </tr>
  <tr>
    <td colspan="2"><a href="https://peach.video/assets/screenshots/wall-20261014.png" target="_blank" rel="noopener"><img src="https://peach.video/assets/screenshots/wall-20261014-readme.webp" alt="播放墙：全屏作品封面与浏览控件" width="1200"></a></td>
  </tr>
  <tr>
    <td colspan="2" align="center">播放墙：拖动挑片，原位播放</td>
  </tr>
</table>

## 下载与使用

### Windows

1. 从[本 Fork 发行页](https://github.com/guoman630308-oss/peach-desktop/releases)取得实际发布的 `PeachDesktop-<版本>-windows-x64-setup.exe`，双击安装；免安装包解压后运行 `Peach.exe`。发行页未提供的制品请按源码构建。
2. 系统需有 Microsoft Edge WebView2 Runtime。首次设置在独立窗口中选择媒体目录与访问范围。
3. 扫描后浏览馆藏，点击视频使用已配置的外部播放器，或在播放区选择「内置播放」。

完整窗口包携带媒体工具。来源凭据、人工验证和可选的 amane 采集桥按需配置；具体要求见[独立窗口说明](docs/NATIVE_WINDOW.md)。

窗口测试包未代码签名。安装前核对发行来源与校验值；安装、更新和故障排查见 [Windows 使用说明](docs/TESTING_DESKTOP.md)。

Windows 的 WebView2 独立窗口测试包可通过 `scripts/build_windows.ps1 -EmbeddedWindow -Installer` 构建，使用独立资料与安装标识，保留原版业务界面。使用与运行组件要求见[独立窗口说明](docs/NATIVE_WINDOW.md)。

构建需要 Windows x64、Git、uv、Python 3.14、Node.js 24 和 Inno Setup 6。同步依赖并构建前端后生成窗口安装包：

```powershell
uv sync --locked --all-extras
npm --prefix frontend ci
npm --prefix frontend run build
& .\scripts\build_windows.ps1 -EmbeddedWindow -Installer
```

独立窗口默认使用 PotPlayer 等本机外部播放器，播放区保留「内置播放」。默认方式与程序路径可在 Peach 配置中选择，详见[外部播放器](docs/EXTERNAL_PLAYERS.md)。

### macOS 本机构建

以下 macOS 与 Docker 入口来自上游；本 Fork 的平台验收范围为 Windows x64。

macOS 可在本机构建独立 `.app` 与 DMG，运行不需要 Python、源码或另一台电脑。构建与安装见 [macOS 独立测试包](docs/OPERATIONS.md#macos-独立测试包)。

发版流程分别生成 Apple Silicon（`macos-arm64.dmg`）与 Intel（`macos-x86_64.dmg`）测试包；可下载版本以 [Releases](https://github.com/peach-mitao/peach/releases) 的实际资产为准。在「关于本机」确认芯片，打开对应镜像并将 Peach 拖入「应用程序」。测试包未完成 Apple 公证，媒体工具需另行准备。

### 从源码运行（Windows、macOS）

需要 Git、uv 和 Python 3.14 或更高，步骤见[源码运行](docs/OPERATIONS.md#从源码运行windowsmacos)。

### Docker（VPS / NAS）

部署步骤与支持范围见 [Docker 部署](docs/DOCKER.md)。

## 常见问题

- **如何只添加一个视频？** 打开「管理 → 扫描与采集 → 添加单个视频」，选择已配置媒体目录中的文件，再点「添加并采集」。只处理选中的视频。

- **会改我的文件吗？** 扫描、补资料不碰原文件。手动整理会改名或移动；清空回收站会删除文件，资源同步中的清理会删除空目录，均需确认。
- **支持哪些网盘？** 通过 CloudDrive2 等工具挂载为本地目录的网盘，详见[网盘挂载](docs/CLOUDDRIVE.md)；云下载支持 115 和 PikPak，登录令牌保存在本机。
- **手机上怎么看？** 首次设置时允许同一局域网的设备访问，手机连同一网络，打开设置页给出的地址，并设置访问密码。
- **数据存在哪？** 独立窗口默认位于 `%LOCALAPPDATA%\PeachDesktop\peach-data`；首次设置可选择其他资料目录。媒体库、设置、海报和生成缓存跟随资料目录，原视频仍在原来的媒体目录。关闭程序后再迁移或备份资料。
- **外部播放还有缓存吗？** 外部播放器直接读取本地文件，Peach 不为这次外部播放转码。扫描生成的缩略图、采集图片及手动内置播放仍可能产生各自缓存。
- **如何配置播放器与来源？** 播放器位于「Peach 配置 → 媒体库与入库 → 播放器」；JavLibrary 使用内建来源配置，网站普通页面地址不能当作自定义 JSON 接口。详见[来源采集](docs/SOURCING.md)。

## 问题反馈

本 Fork 的问题请提交到[本仓库 Issues](https://github.com/guoman630308-oss/peach-desktop/issues)，附上版本、操作步骤、预期与实际结果。截图请遮掉凭据、局域网地址和完整路径，不要上传数据库或媒体；安全问题按[安全政策](SECURITY.md)私下报告。原项目的问题反馈入口保留在[上游仓库](https://github.com/peach-mitao/peach/issues)。

## 文档

[Fork 说明与模块对照](docs/FORK.md) · [独立窗口](docs/NATIVE_WINDOW.md) · [运行与配置](docs/OPERATIONS.md) · [来源采集](docs/SOURCING.md) · [外部播放器](docs/EXTERNAL_PLAYERS.md) · [变更日志](CHANGELOG.md)

参与开发：[开发约定](AGENTS.md) · [测试与依赖](docs/TESTING.md)

## 相关项目

感谢 [amane](https://github.com/sqzw-x/amane)、[Gfriends](https://github.com/gfriends/gfriends)、[CloudDrive2](https://www.clouddrive2.com/) 等项目，完整致谢与复用说明见[复用清单](docs/REUSE.md)。

## Star 历史

以下统计属于上游 Peach 项目。

<a href="https://www.star-history.com/?repos=peach-mitao%2Fpeach&amp;type=date&amp;logscale=&amp;releases=&amp;legend=top-left">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=peach-mitao/peach&amp;type=date&amp;theme=dark&amp;logscale&amp;legend=top-left">
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=peach-mitao/peach&amp;type=date&amp;logscale&amp;legend=top-left">
    <img alt="Peach Star 历史" src="https://api.star-history.com/svg?repos=peach-mitao/peach&amp;type=date&amp;logscale&amp;legend=top-left">
  </picture>
</a>

## 许可证

[AGPL-3.0-or-later](LICENSE) · Copyright (C) 2026 longmeidao。第三方组件保留各自的许可证。

本 Fork 的修改由 guoman630308-oss 维护，修改声明日期为 2026-10-26。源码分发、对应源码与远程访问要求见 [Fork 许可说明](docs/FORK.md#许可与再分发)。
