"""macOS 上的反向域名标识：bundle ID、launchd 标签与 pf anchor 名。

同一个名字要在四个地方同时成立：`.app` 的 `CFBundleIdentifier`、
`~/Library/LaunchAgents/<标签>.plist` 的文件名与 `Label`、`launchctl` 的服务路径
`gui/<uid>/<标签>`，以及 `/etc/pf.conf` 里的 anchor 与 `/etc/pf.anchors/<名字>`。
任何一处对不上都不报错：`launchctl kickstart` 去踢一个不存在的服务、pf 加载一个空
anchor，表现是菜单栏图标不出现、`peach.local` 不带端口打不开，看上去像代码坏了。

所以标识只在这里写一次。单独成模块而不是并进 `peach.config`：它不是设置层的投影，
用户改不了它；也不放进 `peach.tray`，那个模块 import 了 pystray 与 PIL，macOS 的
构建脚本不该为了取一个字符串把整个托盘拉起来。`scripts/setup_macos_port80.sh` 是
POSIX shell，import 不了 Python，它那份字面量由 `tests/test_tray.py` 钉住与这里一致。
"""
from __future__ import annotations

#: 反向域名前缀。取仓库的 GitHub 归属而不是维护者的私有域名：下载者装上的东西不该
#: 带着别人的域名（ADR-0023 第 4 阶段）。
BUNDLE_PREFIX = "io.github.longmeidao.peach"

#: 双击 `.app` 时的 `CFBundleIdentifier`。
MACOS_BUNDLE_ID = f"{BUNDLE_PREFIX}.app"
#: 菜单栏项 LaunchAgent 的 `Label`，同时是它 plist 的文件名主干。
MACOS_LAUNCH_AGENT_LABEL = f"{BUNDLE_PREFIX}.tray"
#: pf 的 anchor 名，同时是 `/etc/pf.anchors` 下的文件名与 80/443 转发那个
#: LaunchDaemon 的 label 主干。
MACOS_PF_ANCHOR = BUNDLE_PREFIX

#: 已部署机器上装着的遗留标签。安装脚本装新标签之前要按这张表把旧 agent bootout 并
#: 删掉它的 plist：`launchctl bootout` 只认给定的那一个 label，留着的旧 agent 会继续
#: 跑一个菜单栏进程、继续占着 80/443 的转发。找不到就静默跳过。
#:
#: 这是一次性迁移。所有部署都跑过一轮之后，这张表连同用到它的分支一起删。
LEGACY_MACOS_LAUNCH_AGENT_LABELS: tuple[str, ...] = ("gg.lmd.peach.tray",)
#: 同上，pf 侧的遗留 anchor 名。
LEGACY_MACOS_PF_ANCHORS: tuple[str, ...] = ("gg.lmd.peach",)
