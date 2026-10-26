# 浏览器历史导入

「口味」读取网页访问记录。自动发现覆盖默认资料目录；便携版、自定义安装和自定义 Firefox 资料可在「浏览器」中添加资料目录或历史文件。文件格式按 SQLite 表结构识别，不由浏览器名称或文件名决定。

## 目录范围

| 平台 | 自动发现 |
| --- | --- |
| Windows | Chrome、Chromium、Edge、Firefox、Zen、Aside、Brave、Vivaldi、Opera、Opera GX、115、QQ、360 安全、360 极速、360 极速 X、搜狗新版、2345、Maxthon |
| macOS | Chrome、Edge、Firefox、Zen、Aside、Brave、Vivaldi、Opera、Opera GX、Safari |
| Linux | Chrome、Chromium、Edge、Firefox、Brave、Vivaldi、Opera；另含 Chrome Flatpak、Firefox Flatpak 与 Snap 默认目录 |

Windows 使用当前设备的 `APPDATA`、`LOCALAPPDATA`，Linux 使用 `XDG_CONFIG_HOME`。资料目录可直接包含历史文件，也可包含一层资料子目录。浏览器配置可以停止和恢复读取，已导入记录单独管理。

## 115 与国产浏览器

- **115**：默认资料目录为 `%LOCALAPPDATA%\115Chrome\User Data`，网页历史可存于 `HistoryEx`。解析器检查 `urls`、`visits` 等网页访问表，下载表本身不计入口味。[115 官方下载故障说明](https://115.com/115115/T344887.html)也引用此文件，但不能据此将整个文件视为仅有下载记录。
- **QQ、360 极速、360 极速 X、搜狗新版**：按 Chromium 网页历史读取。默认目录依据维护项目 [HackBrowserData 的 Windows 浏览器目录](https://github.com/moonD4rk/HackBrowserData/blob/master/browser/browser_windows.go)；360 安全目录另有[官方社区说明](https://bbs.360.cn/archiver/tid-15857311.html)。自定义安装路径需手动添加。
- **搜狗旧版**：`HistoryUrl3.db` 的专有格式未支持，不能按 Chromium 格式读取。[取证研究](https://www.huorong.cn/document/tech/vir_report/805)记录的表结构与标准网页访问表不同；完整时间字段语义未取得。
- **2345**：`%LOCALAPPDATA%\2345Explorer\User Data`，依据[官方帮助](https://ie.2345.cc/help/v7/help_scj.htm)确认资料目录；历史文件仍需满足受支持的 SQLite 表结构。
- **Maxthon**：同时查找 `%LOCALAPPDATA%\Maxthon\User Data` 与 `%LOCALAPPDATA%\Maxthon\Application\User Data`。前者见 [Winapp2 维护源码](https://github.com/MoscaDotTo/Winapp2/blob/master/Non-CCleaner/Winapp2.ini)，后者见[官方社区实际目录](https://forum.maxthon.com/d/6744-aimax--cant-turn-off/5)；[官方答复](https://forum.maxthon.com/d/3314-restore-last-visit-page-after-update-)确认 `History` 为资料中的历史文件。旧版专有 `.dat` 不在标准 SQLite 解析范围内。
- **Opera**：具体资料目录可从 `opera:about/` 查看，见[官方帮助](https://help.opera.com/en/latest/crashes-and-issues/)。

Chrome、Chromium 的资料路径以[官方说明](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/user_data_dir.md)和 `chrome://version` 的 `Profile Path` 为准；`--user-data-dir` 指定的任意位置需手动添加。

Firefox 自定义资料可以从 `about:profiles` 找到根目录后手动添加。官方源码中，[`profiles.ini`](https://github.com/mozilla-firefox/firefox/blob/main/toolkit/profile/nsToolkitProfileService.cpp) 的相对路径基于 Firefox 根目录；新版 [Profile Groups](https://github.com/mozilla-firefox/firefox/blob/main/toolkit/profile/ProfilesDatastoreService.sys.mjs) 记录在独立 SQLite 配置里。Peach 自动查找默认资料位置，不解析这两类配置中的外置位置。UC 的 [Autopsy 目录规则](https://github.com/sleuthkit/autopsy/blob/develop/RecentActivity/src/org/sleuthkit/autopsy/recentactivity/Chromium.java)包含 `UCBrowser/User Data%`，存在资料目录与文件名后缀差异，未列入自动发现承诺；可手动选择历史文件，按实际格式校验。

## 验证与限制

浏览器数据库先经 SQLite backup 取得一致快照，再由固定的 `browserexport==0.4.4` 转换访问时间与记录。Chrome 的可选停留时长、Firefox 的可选描述与预览图缺失时仍可读取；一份资料损坏不阻断其他资料。原始 URL 与标题只保存在私有历史库，不进入 ledger。

临时 SQLite 回归覆盖目录发现、手动添加、停止与恢复、增量去重、可选列、错误格式、特殊路径字符及单份来源失败隔离。真实浏览器文件的验收记录保存在仓库外；目录支持不表示已对每种浏览器的当前版本完成实机验收。

桌面与手机页面沿用现有浏览器来源管理，本次没有布局或交互改动。
