---
name: peach-batch-jobs
description: 在用户说抽帧、九宫格、probe、sheets、creator_boards、刮削、批量、长任务、限流、续跑、流量预算或磁盘余量时使用。
---

# 长跑批处理与流量边界

最后复核：2026-10-18
证据来源：`scripts/probe.py`、`scripts/sheets.py`、`scripts/traffic_watch.py`、`src/peach/jobs.py`、相关单元测试与 ADR-0015。

## 失败值

测量失败不能写成下游可误认的正常值。`probe.py` 曾把「ffprobe 无时长」写成 `duration=0`，
数据因此同时退出 `duration IS NULL` 队列又进不了 `duration>2` 队列。失败一律写 `-1`，
并提供 `--redo zero|failed|all`。

两组状态数字看似矛盾时，先查是否存在两边查询都遗漏的状态，不要直接判定其中一组错误。
待办数量统一报告「可执行 / 被阻塞 / 合计」。

## 磁盘闸门

起跑前一次 `require_free_space` 不是运行期闸门。`DiskGuard` 默认每 20 秒复查系统盘实际余量；
`probe.py`、`sheets.py`、`creator_boards.py` 触线后停止领新任务、保留已完成结果并返回退出码 3，
不得伪报正常完成。

CloudDrive 的稀疏读取缓存可能占用系统盘，与产物所在盘不同。CloudDrive 退出可能重写配置，长跑前后都要复核缓存上限和淘汰策略，不能把
历史配置值当成当前事实。

## 限流

- 限流按主机管理。`HostLimiter` 为每个主机记录下次可发时刻，等待期间可处理其他来源。
- 限流参数按批次规模验证，不能用短测成功推断长跑稳定；测量记录留在仓库外。
- 被 Cloudflare 拦的站一律放弃，不绕过机器人检测。按出口 IP 计的速率配额是另一类：那道判的是
  「这个出口发得太快」而不是「你是不是机器人」，换节点可用，来源下限和撞 403 收工照守，不在
  封禁期里换着节点连打。通用来源边界见 `docs/SOURCING.md`。

## 续跑

长任务必须能续跑。一次未捕获的 TLS EOF 曾让 62 分钟结果归零：网络异常降级为空值而不是抛出，
结果定期写入文件，`--resume` 只把成功判定当作已完成：`no_name` 这类多半是限流假阴性，冻结进
复核文件就再无重试机会。

## 流量与进程

- 200 GB 守卫默认只统计代理流量，覆盖 PikPak，看不到直连 115；需要覆盖直连时显式
  `--count-direct`，且不要在同一计量窗口混跑不同来源。
- 流量估算使用当前来源、实际出口和采样方法；测量结果保存在私有批次记录中。
- `-probesize`/`-analyzeduration` 无法减少 CloudDrive 固定块预取；未知时长时创作者板可回退到
  60 秒 seek，不需要先做全量 PikPak probe。
- Windows 查询已消失 PID 可能抛 `OSError(winerror=87)` 而不是 `ProcessLookupError`，
  `PidFileLock` 必须识别为陈旧锁并安全清理。
- 长任务只停止自己拥有且命令行匹配的 Python/FFmpeg 进程树，禁止全机终止 FFmpeg。
- 导入运维脚本不得触发文件、网络或数据库副作用。

## 抽帧路径

`scripts/sheets.py`、`scripts/probe.py` 的 worker 直接用账本路径跑 FFmpeg，不走 MediaEngine。
修大小写敏感挂载层的失败必须先把 `peach.media.resolve_case_insensitive` 接入这两个 worker。
`sheets.py` 遇 `prim:reserved` 非法色彩元数据会带 bt709 声明重试，只在失败帧重试，不得无条件
给所有输入加色彩覆盖。

## 真实库批次的预检与验收

- 真实批次只在 ledger writer 上跑（`/healthz` 回 `ledger_sync=writer`、`db=available`）；reader 只做
  代码验证和副本抽样。起跑前逐项核对，缺一项就不接管写入端：工作区干净且已纳入 master、全量测试
  通过、`peach migrate status` 正常、目标网盘盘符能列目录、没有同类 `probe`／`sheets`／
  `traffic_watch` 进程、系统盘余量高于 `--min-free`，并按 `peach-ledger-write` 用 SQLite backup API
  备份账本。队列数字开跑前用 `scripts/job_status.py` 现算，不抄进文档：写死的基线只在生成那一刻成立。
- 先在独立窗口起流量守卫（`scripts/traffic_watch.py --limit 200 --warn 120`，PikPak 要加
  `--count-direct`），再按封面 → `probe` → `sheets` 的顺序跑；`probe` 非零退出就不进九宫格。
  同一晚只跑一个来源，期间不做 Ledger 同步、接管、托盘重启或别的真实库批处理。
- 全量缩略图先按当前队列与来源成本估算。触发流量或磁盘闸门是安全中止不算失败，
  下次用原命令续跑，不为完成率调高预算。
- 次日先 `scripts/job_status.py` 打印实时队列，读两类任务的最新日志，再跑 `PRAGMA integrity_check`
  与 `PRAGMA foreign_key_check`；任何一项不过就不同步共享副本。报告固定给：完成／仍失败／待处理、
  守卫累计下载、实际出口 chain、系统盘余量、备份路径、退出码、是否触发闸门；少一项就判不了下一晚
  该续跑还是先查问题。
