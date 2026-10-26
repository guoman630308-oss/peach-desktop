# ADR-0017：双机本机运行与分通道同步

- 状态：已接受并实施（显式 writer/reader、图片 artifact 与复核只读镜像已落地；其余 artifact 拆分待续）
- 日期：2026-08-21

## 背景

Windows 与 macOS 各自持有代码、运行数据、虚拟环境和 worktree，媒体盘只承担资源存储。移除媒体盘不应同时移除代码或运行账本。

外置盘现有 `peach-data` 也不能整体搬进 GitHub或普通双向同步：`database/` 含活跃 WAL 账本和
大量历史备份，`generated/` 同时混有可重建缓存、图片资产和人工复核产物，另外还有凭据、日志、
机器锁、工具与临时文件。同步整个目录会扩大冲突面，并可能传播凭据或损坏 SQLite。

## 决策

### 每台机器独立持有运行环境

- 两个平台都使用 `<项目父目录>/{peach-app,peach-data,peach-worktrees,peach-sync}`，各机独立持有运行文件。
- 媒体挂载路径由配置声明。拔盘只让 `local` 来源进入脱盘模式，不影响代码、账本、115/PikPak
  或应用启动。
- `.venv`、打包产物、凭据、日志、机器锁、临时文件和可重建缓存均为本机状态，不跨机器复制。

### 不同步 worktree 目录

- Git 仓库只同步被跟踪的代码、公开文档、迁移、可分发资源和分支。
- 主分支和仍需跨机器继续的任务分支必须先 commit/push；另一台 fetch 后按分支重建 worktree。
- `peach-worktrees` 目录及其 `.git` 指针带绝对本机路径，不进入 GitHub，也不通过文件同步工具复制。

### `peach-data` 按性质分通道

- `database/ledger.db`：两台机器各持本地 SQLite 工作副本，通过 Peach 自己的单写者复制同步，
  不进入 GitHub，也不交给通用文件同步。复制继续使用 SQLite backup API。
- 第一阶段的共享账本副本放在 Windows 内置盘的专用同步目录，由 SMB 暴露给 macOS；它是同步
  传输点，不是任一 Peach 实例直接运行的数据库。Windows 关机时 macOS 仍用本地副本运行，恢复
  后再同步。
- 两台服务可以同时在线，但账本仍是单写者：marker 的 `device` 是唯一写入端，另一台由 API
  强制只读。服务启动、浏览和退出都不复制；同步与写入端切换只由托盘显式动作执行。
- 需要跨机器保存的图片资产、原始证据和复核产物从 `generated/`、`sources/`、`review/` 中拆成
  独立、稳定命名的 artifact 集；拆分后可用 Syncthing 一类文件同步工具复制。活账本、`-wal`、
  `-shm`、同步标记、凭据、日志、锁、临时文件、转码与流分片必须排除。
- 复核候选在完成 artifact 拆分前仍由 writer 本机生成和解释。reader 不复制原始 CSV，而是通过
  本地 CA 严格校验的 HTTPS 读取 writer 的归一化 `GET /api/review` 契约，并只保留一个原子替换的
  最近成功 JSON 缓存。macOS 会把文件 CA 与系统/登录钥匙串中受信任的同名 Peach CA 合并为
  校验集合，以兼容两台机器各自签发 CA 的现状；只读取公钥证书，不导出私钥。读取前必须确认
  目标实例仍是 writer。launchd 的 Python 主体若被 macOS 本地网络权限拦截，则由系统
  `/usr/bin/curl` 经回环 Stash 代理发起同一严格请求：协议限 HTTPS、响应限 8 MiB，令牌经
  stdin 传入，非回环代理拒绝。
  reader 页面不提供任何复核决定写入。

### 两个局域网入口

- macOS 固定使用 `peach.local`，Windows 固定使用 `peach-writer.local`，两台可以同时广播，避免
  同名 mDNS 记录互相覆盖。

## 放弃方案

- 把 `peach-data`、worktree 或整个 Peach 父目录提交到 GitHub：会传播真实数据库、凭据和大量
  派生文件；worktree 的本机路径在另一台也无效。
- 用 Syncthing、Dropbox 等直接双向同步整个 `peach-data`：文件冲突副本不能合并 SQLite 事务，
  也不能保证 WAL 与主库是一致快照。
- 继续让外置盘保存代码、运行数据和共享账本：任一机器没插盘就无法独立运行，违背本次目标。
- 改成 PostgreSQL 或新增远程数据库服务：当前单用户规模不需要，且会把本地优先应用变成网络
  服务依赖；除非以后明确需要双主写入，否则不引入。

## 后果

- GitHub 同步范围保持小而可审查；worktree、venv 和运行态问题不再跨机器污染。
- Windows 内置盘克隆、venv、运行数据播种、默认路径、共享同步点和托盘入口已切换完成。
- 数据根与共享根由设置文件提供（`settings_file`，`config.py` 只读它，没有内置盘符）；
  `peach.sync` 已实现显式 writer/reader 角色、手动同步、显式接管和 SMB immutable 快照拉取。
- artifact 拆分前不启用 `peach-data` 整体同步；外置盘上的旧目录保留为只读迁移来源和备份，
  直到两端独立运行验收完成后再单独决定清退。

## 实施结果

1. 已完成：Windows 内置盘 checkout、`.venv`、`peach-data`、worktree、账本播种、
   内置盘 SMB 同步点、默认路径和指向内置盘的 Startup 托盘入口。
2. 已完成：Windows 固定发布 `peach-writer.local`；外置盘只保留 `R:\media` 媒体职责。
3. 已完成：五类图片 artifact 单向同步；Mac reader 可实时或从最近缓存浏览 writer 复核队列，
   不复制候选 CSV，也没有第二条写入路径。
4. 待续：其余 durable artifact 拆分，以及拔盘后的双机完整验收。
