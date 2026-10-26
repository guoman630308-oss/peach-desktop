# ADR-0091：重启托盘时自动备齐依赖与账本结构

- 状态：Accepted
- 日期：2026-10-02
- 相关：ADR-0011、ADR-0017、ADR-0040

## 背景

2026-10-02 托盘「重启服务」上线后，HTTPS 子服务起不来：主检出 `.venv` 缺 grpcio，`create_app` 装配云下载
服务时 `import grpc` 失败。补装 grpcio 后，迁移 0040–0043 没有执行，`entity_redirect` 查询报 500。两步都是
人工发现、人工补上的。托盘的健康检查只看 `/healthz` 的 `ok`，账本结构落后时服务照样回 `ok`。

代码合入 master 不等于运行环境跟上：依赖由 `uv.lock` 决定，账本结构由 `migrations/` 决定，两者都要有人在
重启时落到这台机器上。

## 决策

**一、依赖按 `uv.lock` 同步，只在源码部署、只在托盘进程之外做。** 判据是
`uv sync --locked --all-extras --inexact --no-install-project`，加 `--check` 即只读核对。frozen 包、缺
`uv.lock`、缺 `.venv/pyvenv.cfg` 或找不到 uv（PATH 之外再查 WinGet 安装目录）时跳过。`restart_windows_tray.py`
在旧托盘与子服务退净之后、新托盘启动之前同步；同步前还有别的进程映像在项目 venv 里，就拒绝重启并报出 PID。
托盘进程里只核对：核对出不一致时，菜单「重启服务」分离地拉起 `restart_windows_tray.py --source --force`
整体重启托盘，脚本拒绝或没起来就通知原因并只重启子服务。

**二、迁移由托盘在子服务停着的间隙执行，只迁本机写者的账本。** 托盘启动时排在第一次 `start_missing` 之前，
「重启服务」排在停子服务与拉起之间。命令是子进程 `peach migrate status`，有待执行的再 `migrate upgrade --yes`，
都带 `--db`。账本不存在、首次设置未完成、本机不是写者时不迁移。写者判据与 `library_processing._require_writer`
一致：复制关闭时本机即写者，开启时 `<数据根>/state/device-id` 要等于共享副本记的写者。

**三、子服务起来后问 `/healthz?ready=1`。** 只有 API 服务回 `ready`；未就绪项写日志、进启动通知。依赖、迁移、
就绪三步写进 `<数据根>/state/runtime-prepare.json`，记录带托盘 PID，重启脚本只认新托盘那一份。

**四、重启前过任务闸门。** 只读查本机 `task_run` 中 pending／running 的行：`followup_key` 非空或
`trigger='scheduled'` 的重启后会续跑，放行；其余在跑就拒绝，`--force` 越过。进程已死的行只列出，不拦。
托盘菜单拉起的整托盘重启带 `--force`：点「重启服务」本来就要停子服务。

## 理由

- Windows 上被进程加载的 `.pyd` 换不掉。托盘自己加载着 venv 里的 pystray、httpx 等扩展，活着的托盘里同步
  会半途失败、留下半装的环境；重启脚本只加载标准库与本包纯 Python 模块（`peach.runtime_prepare` 的导入边界
  由测试守住），它停掉托盘之后的那段间隙是唯一安全的时机。
- `--no-install-project`：pyproject 的 `cache-keys` 按 mtime 判 editable 项目，改一次 README 就判成不一致，
  内容还原也一样。项目本身是 editable 安装，源码改动不需要重装。
- `--inexact`：venv 里手工装的调试工具不归 `uv.lock` 管，同步不删它们。
- 迁移放在托盘而不是重启脚本里：托盘冷启动（开机自启）也要迁，脚本只覆盖手动重启这一条路。
- 迁移走子进程而不是在托盘里 import：失败时只是一次退出码，托盘照样起来报告，不会带着半开的连接继续跑。

## 备份与清退

`migrate upgrade` 自己先把账本备份成 `ledger.pre-migrate-<时间>.db`，路径打印在输出的 `backup:` 行，失败时
通知里指向它。这些备份和其他 `ledger.pre-*.db` 一样由 `peach.ledger_backups` 的规则清退：托盘每次启动在迁移
之后按最近 5 份、24 小时内、比 `ledger.db` 更新的都留的规则执行。

## 后果

- reader 机器不迁移：它的账本副本来自写者，结构跟着写者走；reader 上结构落后由就绪检查报出。
- macOS 菜单栏不走 `PeachTray` 的这条准备链路，依赖与迁移仍按 `docs/OPERATIONS.md` 手工做。
- `deploy_windows_tray.py` 换包不同步依赖，新托盘启动时照样迁移；冻结托盘自身跳过依赖核对。
- 依赖不一致时，开着调试 serve（如 8099）会挡住整托盘重启，要先停掉它。
- `task_run.trigger='startup'` 目前没有任务使用，归入续跑不了的一类。
