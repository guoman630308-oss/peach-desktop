---
name: peach-shell-commands
description: 在写 PowerShell 或 Bash 命令、交给用户在终端跑命令、拼多行内容、用 rg 或 Python CLI、在测试里造临时目录，或命令报了看不懂的语法与编码错误时使用。
---

# 命令与路径的形态

最后复核：2026-10-13
证据来源：`tests/test_agent_worktree.py` 与 `tests/test_scripts.py` 里临时目录 `.resolve()`
和正斜杠路径的注释、`scripts/test.ps1` 与 `scripts/test.sh` 的编码约定、Git 历史里被
heredoc 转义损坏的那几次补丁、`scripts/test_runner.py` 的外部工具预检，以及 2026-07 至 09
Claude 与 Codex 会话记录里交给用户、被贴回报错截图的 39 条命令。

## 交给用户跑的命令

用户终端在 Windows 上是 PowerShell 7，在 Mac 上是 zsh，都不是智能体自己工具里的那个 shell。
通用的错法与写法见用户级技能 `user-terminal-commands`（`~/.agents/skills/`）。Peach 专属的几条：

- 终端不一定在 peach-app，出现过停在 `.claude\worktrees\<会话>` 里的情况。所以 venv 与脚本写绝对路径：
  `& <用户目录>\Desktop\peach\peach-app\.venv\Scripts\python.exe -X utf8 <同上>\scripts\x.py`，
  写出时把占位换成实际路径。也可以在命令开头加 `Set-Location <peach-app 绝对路径>;`。
- 不能凭记忆写的参数：`agent_worktree.py integrate --branch <分支>`；迁移要写
  `peach.exe migrate upgrade --yes`，只写 `migrate` 只会打印状态。
- `--apply --backup` 的备份文件名带 `$(Get-Date -Format yyyyMMdd-HHmmss)`：上一次失败前备份已经写出，
  同名重跑会报 `FileExistsError`。
- 交出前确认前提成立：`restart_windows_tray.py` 需要托盘已经在运行；`uv sync` 碰到运行中的
  `peach.exe` 会报拒绝访问；不要让用户删本会话终端的启动目录。

## 何时使用

在这台机器上写任何 shell 命令之前。这些不是风格偏好，每一条都对应真实重犯过的失败；
违反后多数当场报错，少数（多行内容的转义）是静默损坏，那一类的核心判据留在 `AGENTS.md`。

## 按当前 shell 写

- PowerShell 的 `cat`、`ls`、`where` 是别名，语义与 Unix 同名命令不同；Bash 没有
  `Get-ChildItem`。写命令前先确定自己在哪个 shell 里。
- PowerShell 只用 `pwsh` 7.x。从 Bash 调用时加 `-NoProfile`。
- 默认单引号，需要展开才用双引号，有歧义时写 `${name}`。
- `rg` 的路径参数不含 `*`，要筛文件用 `-g`；退出码 1 表示无匹配，不是失败。
- Python CLI 加 `-X utf8`；PowerShell 读 UTF-8 日志显式加 `-Encoding utf8`。编码要在输出端
  固定，只给读取端指定编码挡不住乱码。

## Windows Codex 沙箱

- `windows.sandbox = "elevated"` 是隔离模式，不是管理员权限。项目测试、npm／PyInstaller 构建、
  Git common directory 写入需要正常 PowerShell 权限时，从第一次调用就用受控提权和窄命令前缀；
  不先在沙箱里制造一次 `WinError 5`。
- `_winapi.CreateProcess` 或 `.git/**/index.lock` 返回拒绝访问时，原样重跑统一入口；不拆成单测、
  不改断言、不跳过用例。`scripts/test.ps1` 会在分片前报告无法启动的外部工具。
- `danger-full-access` 是取消隔离，不是「提权沙箱」。除非用户明确要求承担全局风险，否则保留沙箱，
  只给当前项目入口所需的命令授权。
- 测试、构建与媒体任务直接使用项目入口，不外套 CPU、内存、进程数或重任务槽位守卫；任务范围与取消按本机 `resource-budget.md`，进程清理只按本任务 PID。

## 多行内容一律先写成文件

多行内容用写入工具或脚本写成文件，再让命令读那个文件，不要用 heredoc。反斜杠会被吃掉一层，
换成带引号的定界符也挡不住所有情形，而损坏是静默的：命令照常退出 0，写进去的内容已经变形。
提交消息同理，写进临时文件再 `git commit -F`。

Windows 与 Git Bash 之间传路径时统一写正斜杠（`Path.as_posix()`）。`C:\Users\...` 落进
shell 脚本后反斜杠会被当成转义符吃掉，`exec` 拿到的是一个粘在一起的名字。Git Bash 也不按
shebang 找 Windows 上的 Python，解释器要显式写出来。

## PowerShell 变量与管道

- 变量必须用任务专属名称。禁止声明 `$HOME`、`$home`、`$CODEX_HOME` 等系统变量的任何
  大小写变体。
- `foreach {}` 的结果先存进任务专属数组，再单独接管道格式化。禁止在闭合花括号后直接写管道。

## 测试里的临时目录

一律先 `.resolve()` 再喂给被测代码和断言。CI runner 的临时目录都是别名：macOS 的 `/var`
软链到 `/private/var`，Windows 的 `RUNNER~1` 短名展开成 `runneradmin`。开发机没有这层别名，
拿未 resolve 的路径断言只会在 CI 上红，本机怎么跑都是绿的。
