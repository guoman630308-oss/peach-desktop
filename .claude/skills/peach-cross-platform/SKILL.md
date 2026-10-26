---
name: peach-cross-platform
description: 在 macOS 上开工、改动路径解析或挂载判定、遇到 git status 与 git diff 结果不一致，或要把改动交回 Windows 那台时使用。
---

# 跨 Windows / macOS 双机开发

最后复核：2026-10-18
证据来源：`src/peach/platform.py`、ADR-0017、2026-08-19 的 macOS 迁移实测、
2026-08-21 的 Windows 现状核对，以及 2026-09-14 用 junction 复现的临时目录别名。

## 何时使用

- 当前工作目录在 macOS 上（`~/Desktop/<用户目录>/peach/peach-app`），或改动要交回 Windows 那台继续。
- 改动涉及 `asset.path` 解析、`allowed_media_roots`、挂载可达性或磁盘闸门；`git status` 显示大量文件被修改，但 `git diff` 是空的。

## 路径口径

账本里的 `asset.path` **一律是 Windows 形态**：`R:\Media\...`（本地硬盘）、
`B:\...`（115）、`A:\...`（PikPak）。

不要为了 macOS 把账本改写成 POSIX 路径。翻译只发生在读取期，收敛在
`src/peach/platform.py` 一处；写回账本的索引脚本仍只在 Windows 上跑，路径口径
因此保持单一。**禁止从 macOS 写 `asset.path`**。

来源映射按 `asset.location` 而不是盘符。声明根在设置文件的 `[media.locations]`，本机落点在 `[media.mounts]`；不要从其他部署复制挂载路径。

用 `PEACH_MEDIA_MOUNTS=local=/mnt/res,115=/mnt/115` 覆盖；数据目录用 `PEACH_DATA_ROOT`。
CloudDrive 在 Windows 是盘符、在 macOS 是 macFUSE 挂载点，这层不是可选优化。

运行目录不跟着媒体盘符走。代码、数据、venv 和 worktree 各机独立保存，媒体挂载点由配置声明。

## 三条只在 Windows 成立的假设

- **授权根比较不能只看字面**。账本写 `R:\Media`，macOS 侧授权根是
  `<mounts.local>`；pathlib 区分大小写而 exFAT/NTFS 不区分，字面比较会把
  全部本地资产判成越权。`within_root` 在字面不匹配时改用 inode 比较，交给文件系统
  判定。
- **挂载点「在不在」的判据是能否列出第一个条目**，不是 `is_dir()`。CloudDrive 掉线后
  挂载点目录仍然存在，读一个条目才会报 `Device not configured`。见 `root_online`。
- **磁盘闸门看系统盘**：Windows `C:`、macOS `/`，用 `system_volume()`。CloudDrive 的
  下载块缓存在两个平台都落在系统盘；写死 `C:/` 会让 macOS 侧直接抛 `FileNotFoundError`。

另外 `PidFileLock._running` 要吞掉 `OverflowError`：Windows 写下的 PID 可能超出 POSIX
的 `pid_t`，`os.kill` 会崩掉整轮任务，而超界的 PID 一定不是本机活进程。

## 脱盘模式

脱盘是**来源级**的，不是全局开关。外置盘拔掉只影响 `local`，115/PikPak 的可达性单独判断；
CloudDrive 掉线不影响仍可读取的本地来源。

判据只有 `GET /api/sources` 一份。媒体层把整盘不可达从 `MediaUnavailable` 里分出
`MediaOffline`，HTTP 回 503 加 `X-Peach-Offline: 1`，和「单个文件缺失」的 404 分开。
前端据此置灰脱盘来源的筛选（数量仍然显示：脱的是盘不是账本），详情页换成脱盘面板
而不是挂一个必然失败的播放器。

## 运行环境

macOS 的 FFmpeg 走 PATH（`brew install ffmpeg`）；Windows 的 FFmpeg bundle 留在 Windows
本机 `peach-data/tools`。菜单栏走 LaunchAgent，服务使用非特权端口 8900。

测试入口：Windows `scripts/test.ps1`，macOS/Linux `scripts/test.sh`。两者契约相同。
**两边都必须绿**；只在一台上通过的改动不算完成。Windows 托盘专属的 DPI 声明和单实例
锁在非 Windows 上按 `skipUnless` 跳过，不要改成在 macOS 上伪造通过。

用例里的临时根一律先 `.resolve()` 再交给被测代码：被测那边多半自己也 `resolve()`，而 CI runner
的临时目录是别名（Windows 的短名与 junction、macOS 的 `/var` 软链），本机两边相等、runner 上
不等，包含关系与路径断言于是本机全绿、CI 全红。本机复现它：拿 `mklink /J` 做一个 junction，
把 `TEMP` / `TMP` 指过去再跑那几条用例。

## 账本复制

目标是两台机器各持本地工作副本，Windows 内置盘上的专用 SMB 目录提供共享传输副本。
**不是多主实时同步**，因为 SQLite 没有安全的自动三方合并。见 ADR-0017、`src/peach/sync.py`
与 README「数据属于自己」。

- 服务启动只观察世代和写入端，不自动拉取或推送，也没有定时同步；marker 的 `device` 是唯一写入端、另一台只读，复制与「接管 Ledger 写入」只能由托盘显式执行，两边都动过就转只读报冲突、由人选一边。
- 同步点不可达时本地仍可运行；媒体盘是否插入与账本同步无关。
- 复制走 SQLite backup API，**不要复制 `.db` 文件**：WAL 里已提交未 checkpoint 的
  事务会丢，还可能和目标残留的 `-wal` 拼成损坏的库。
- Windows 本地工作副本与内置盘共享副本已分离；线上 `/healthz` 用 `writer` / `reader` /
  `conflict` 报告角色。macOS 从 SMB 拉取时，共享 DB 只能作为 immutable 已关闭快照读取。
- 冲突不能靠合并解决，只能选一边：复制其一覆盖另一份，或删掉一侧 `.sync.json`
  重新播种。别写「已同步」这种含糊结论。

## 代码同步

代码在 `origin` 指向的那个仓库，两台机器手动 push/pull，**不自动推送**。
开工前先 `sh scripts/sync_status.sh`：落后远端就先 `git pull --rebase`，别在旧代码上接着
写；账本走另一套同步（`peach.sync`），同一个脚本会一并报告。

worktree 目录不跨机器复制。需要交接的任务分支先 commit/push，另一台 fetch 后重新创建本机
worktree；`.venv`、`peach-data`、构建产物和 worktree 的 `.git` 指针都不进 GitHub。

## macOS 的 mDNS

从终端起正常、改由 launchd 起就没人应答：「本地网络」隐私门按进程判，launchd 作业是另一个
主体且没有弹窗可点，自己发的多播被**静默**丢弃（设置里给 Python 放行没用，不是这个主体）。
macOS 因此用 `dns-sd -P` 请系统 mDNSResponder 代发，Windows 继续用 zeroconf。

**别让服务进程自证可达**：它发不出多播，探自己必然失败，会把好的判成不可达。验证用
`scripts/check_mdns.py`（从终端跑）。排查手法：同一条命令分别从 shell 和 launchd 起各查一次，
差异只在启动者时就是这个门。

主机名取本机 `server.mdns_name`，用 `/healthz` 与 `check_mdns.py` 核对广播名称及可达性；两台机器不得同名。

## Git 陷阱
- **换行**由 `.gitattributes` 的 `* text=auto eol=lf` 固定，不要依赖各自的
  `core.autocrlf`。2026-08 之前 Windows 侧把 105 个已跟踪文件整体改写成 CRLF，
  `git status` 长期显示 117 个文件被修改、16213 行增删，而真正有实质改动的只有 1 个，
  未提交的工作被假 diff 完全埋掉。
- **从 exFAT 复制过来的文件权限是 `rwx------`**（exFAT 没有 POSIX 权限位，macOS 挂载时
  统一合成）。这会让 git 的 stat 缓存长期对不上：`git status` 显示上百个文件被修改，
  `git diff` 却是空的，`git hash-object` 和 HEAD 逐字节相同。
  `git update-index --refresh` **只会报 `needs update`，修不好**；用 `git add` 重写
  stat 缓存才行。
- 判断真实差异一律看 `git diff HEAD --name-only`，**不要看 `git status`**。
