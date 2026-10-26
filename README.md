<p align="center">
  <img src="resources/peach-logo.png" alt="Peach" width="96">
</p>

<h1 align="center">Peach</h1>

<p align="center">给自己用的私人影片馆藏：本地硬盘、网盘和关注的创作者，放在一个地方看。</p>

<p align="center">
  <a href="https://peach.video">官网</a> ·
  <a href="https://github.com/peach-mitao/peach/releases">下载安装包</a> ·
  <a href="https://demo.peach.video">在线演示</a> ·
  <a href="https://github.com/peach-mitao/peach/releases/tag/intro-video">介绍视频</a> ·
  <a href="https://github.com/peach-mitao/peach/issues">问题反馈</a> ·
  <a href="https://github.com/orgs/peach-mitao/projects/7/views/1">需求看板</a> ·
  <a href="https://github.com/orgs/peach-mitao/projects/7/views/3">路线图</a> ·
  <a href="#文档">文档</a> ·
  <a href="README.en.md">English</a>
</p>

<p align="center">
  <a href="https://github.com/peach-mitao/peach/releases"><img src="https://img.shields.io/github/v/release/peach-mitao/peach?include_prereleases&amp;filter=v*&amp;sort=semver&amp;display_name=tag" alt="release"></a>
  <a href="https://github.com/peach-mitao/peach/actions/workflows/test.yml"><img src="https://img.shields.io/github/actions/workflow/status/peach-mitao/peach/test.yml?branch=master&amp;label=tests&amp;logo=githubactions&amp;logoColor=white" alt="CI 状态"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0--or--later-blue?logo=gnu&amp;logoColor=white" alt="license"></a>
  <a href="docs/TESTING_DESKTOP.md"><img src="https://img.shields.io/badge/Windows-x64-0078D4" alt="Windows x64"></a>
  <a href="docs/OPERATIONS.md#macos-独立测试包"><img src="https://img.shields.io/badge/macOS-DMG-555555?logo=apple&amp;logoColor=white" alt="macOS 独立应用构建与安装"></a>
  <img src="https://img.shields.io/badge/18%2B-adult%20content-critical" alt="18+ 成人内容">
</p>

https://github.com/user-attachments/assets/a5610874-e611-41bb-be22-7d99f0d64b62

> **18+** 仅供成年人管理自己的成人内容馆藏。仓库不含媒体或站点数据；连接外部站点需有相应账号和访问权。

## 能做什么

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

1. 从 [Releases](https://github.com/peach-mitao/peach/releases) 下载完整安装包 `Peach-<版本>-windows-x64-setup.exe`，双击安装，无需管理员权限；也可下载完整免安装包，解压后运行 `Peach.exe`。
2. 浏览器打开首次设置页后，选择媒体目录和访问范围。
3. 开始扫描，随后在浏览器里浏览和播放。

完整安装包携带媒体工具，可离线安装；轻量安装包体积更小，安装时按需下载工具。

测试版尚未签名。若 Windows 提示「已保护你的电脑」，确认来自本项目的 Release，再点「更多信息 → 仍要运行」。安装、更新和故障排查见 [Windows 使用说明](docs/TESTING_DESKTOP.md)。

Windows 的 WebView2 独立窗口测试包可通过 `scripts/build_windows.ps1 -EmbeddedWindow -Installer` 构建，使用独立资料与安装标识，保留原版业务界面。使用与运行组件要求见[独立窗口说明](docs/NATIVE_WINDOW.md)。

独立窗口默认使用 PotPlayer 等本机外部播放器，播放区保留「内置播放」。默认方式与程序路径可在 Peach 配置中选择，详见[外部播放器](docs/EXTERNAL_PLAYERS.md)。

### macOS 本机构建

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
- **数据存在哪？** 本机的 `peach-data` 文件夹；Windows 版默认位于 `%LOCALAPPDATA%\Peach\peach-data`。

## 问题反馈

先查看[用户反馈](https://github.com/peach-mitao/peach/issues)，或[提交问题](https://github.com/peach-mitao/peach/issues/new/choose)，附上版本、操作步骤、预期与实际结果。截图请遮掉凭据、局域网地址和完整路径，不要上传数据库或媒体；安全问题按[安全政策](SECURITY.md)私下报告。

## 文档

[运行与配置](docs/OPERATIONS.md) · [来源采集](docs/SOURCING.md) · [外部播放器](docs/EXTERNAL_PLAYERS.md) · [变更日志](CHANGELOG.md)

参与开发：[开发约定](AGENTS.md) · [测试与依赖](docs/TESTING.md)

## 相关项目

感谢 [amane](https://github.com/sqzw-x/amane)、[Gfriends](https://github.com/gfriends/gfriends)、[CloudDrive2](https://www.clouddrive2.com/) 等项目，完整致谢与复用说明见[复用清单](docs/REUSE.md)。

## Star 历史

<a href="https://www.star-history.com/?repos=peach-mitao%2Fpeach&amp;type=date&amp;logscale=&amp;releases=&amp;legend=top-left">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=peach-mitao/peach&amp;type=date&amp;theme=dark&amp;logscale&amp;legend=top-left">
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=peach-mitao/peach&amp;type=date&amp;logscale&amp;legend=top-left">
    <img alt="Peach Star 历史" src="https://api.star-history.com/svg?repos=peach-mitao/peach&amp;type=date&amp;logscale&amp;legend=top-left">
  </picture>
</a>

## 许可证

[AGPL-3.0-or-later](LICENSE) · Copyright (C) 2026 longmeidao。第三方组件保留各自的许可证。
