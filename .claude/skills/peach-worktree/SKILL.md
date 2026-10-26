---
name: peach-worktree
description: 在用户说并行、工作树、暂存、提交、ready、集成、生产重启，或任何要写 Peach 代码的任务开始时使用。
---

# 并行 worktree 与提交边界

最后复核：2026-10-19
证据来源：[开发约定](../../../AGENTS.md)、`README.md`、ADR-0015、ADR-0017、本机工作树与沙箱工具记录。

## 是否委派与何时使用
单一功能域、局部改动或预计 30 分钟内完成的任务，由协调者在自己的隔离工作树直接完成；只有任务可独立并行、需要隔离环境，或长跑过程能明确缩短总耗时才委派。
默认最多一个工作者；多个工作者只用于彼此无依赖的任务。委派只传目标、相关文件、边界和验收命令，不转交完整对话。
协调者只在完成、阻塞或至少五分钟的检查点读取状态，不做分钟级轮询，也不重复工作者完成的搜索、浏览器检查和测试。
跨对话消息仅用于对方需要处理的具体依赖；发送前核对状态与职责。授权不等于同步义务，不唤醒已结束对话只为报进度或收确认；交付以 Git 和共享证据为准。
任何会写入当前机器 `peach-app` 的代码任务都使用隔离工作树。主目录只做集成，不做并行编辑；worktree 本机重建，不跨机器复制目录。

## 流程
1. 协调者在主目录创建隔离工作树：`& .\.venv\Scripts\python.exe -X utf8 scripts\agent_worktree.py create --agent claude --task <task>`。它建在 `peach-worktrees/`，Codex 和 Claude 共用这一个目录。**不要用 Claude Code 内置的工作树机制（`.claude/worktrees/`）**：它在分支被集成后会被回收，目录却留在原地。
   Windows Codex 对 `create`／`ready`／`integrate`／`prune --apply`、`git add` 与 `git commit`
   从第一次调用就按 `peach-shell-commands` 的沙箱规则使用受控提权；它们会写主检出的
   Git common directory，工作树目录可写不代表 `.git/worktrees/**` 可写。

2. 工作者只在自己的工作树内编辑。`create` 自动锁定工作树，成功集成后解锁；不复制 `.venv`；用 `uv sync --locked --all-extras` 创建自己的测试环境。
3. 测试在当前工作树根目录运行：Windows `& .\scripts\test.ps1`，macOS/Linux `./scripts/test.sh`。默认 `auto` 按改动文件取影响域并集；文档及无改动检查 `checks`，未知影响面、共享测试设施和实际依赖选 `full`。域清单唯一真相在 `scripts/test_runner.py`；局部调试可显式选域。
   两者契约相同：优先当前树 venv，缺失时从 Git common directory 定位主项目 venv，强制 `PYTHONPATH=<当前工作树>/src`，
   核对 `peach.__file__` 后运行标准库 `unittest`。禁止手工拼接 venv 路径或调用 pytest。
   测试入口固定 `PYTHONIOENCODING=utf-8`，覆盖标准输出、错误输出及子进程；其他 Python CLI 使用 `-X utf8`。
   安装依赖和构建完成后再验证；测试期间不改变环境。主检出与隔离工作树均默认自动选测。
   PowerShell 读 UTF-8 日志显式加 `-Encoding utf8`；编码在输出端固定，不能只给读取端指定编码。
   记录绑定完整代码内容、依赖环境和范围，24 小时有效；失败撤销对应范围，运行中改动只保留未受影响的完整批次文件结果。
   用 `-Focus` / `--focus` 先验证相关 Python 或浏览器用例，调试不签记录；冻结改动后只运行一次 `auto`，不另写逐域循环。失败先定向修复，再让 `auto` 复用未受影响的记录，详见 `docs/TESTING.md`。
   同环境基线按有效内容差异补测；纯注释选 checks。完整批次补齐缺少文件后恢复全量，局部批次不能拼成全量；共享逻辑或未知文件仍跑全量。
   差异补测不延长全量基线有效期；版本号行单独变化归 tooling，包内其他逻辑变化仍按源码判断。
   相同状态复用记录；Windows `-Fresh`、POSIX 第二参数 `--fresh` 强制重跑；显式 `full` 总是实际执行。
4. `ready` 前将当前 `master` 纳入工作树并运行 `auto`。相同内容可复用记录；目标变化重算影响域。`ready` / `integrate` 拒绝缺记录、范围不足或目标分支未纳入的提交。
5. 协调者统一运行 `integrate`：锁内复查、合并固定提交。取得成功退出状态并核对主线提交后才重启；锁忙交给入口等待，禁止直接 merge；提示带持锁方的 pid、开始时间与范围，不反复数进程。运行记录写到仓库外的私有上下文；工作者按文件分工，共享文件安排顺序。
   版本号不在这里动，`integrate` 只报当前值：推进与打标签都走 `scripts/release_tag.py`，
   它是唯一入口。`--bump auto --apply` 推版本、定版变更日志并把那一节带在输出里给人确认，
   落盘前自动检查上游、依赖与非 manual 参考来源；审阅 `build/release-review.json`，采用范围见 `docs/TESTING.md`。
   确认后 `--ship --apply` 一次做完提交、推送、等 Test 转绿、打标签（ADR-0012 修订）。
   人只判断措辞，不记流程；停在半路就再跑一次同一条命令。
   「该发一版了」由 `release_due.py` 的 Stop 钩子一家说，收尾那段不要跟着说第二遍：
   `integrate` 输出里的 `release`（`changelog.due()`）与它同一个判据，而钩子按 master 的 sha
   闩住、集成后的干净主检出正好满足它那四道闸，两边都说就是一屏里两条一样的话。判据是
   每周一次、攒够提前、破坏性变化与安全修复不等周期，只数使用者看得见的条目。
   CI 分片、系统覆盖与发布复用见 `docs/TESTING.md`；合并完成不机械追加全量。耗时和慢测试记录在主目录 `build/agent-verification/`。
   主检出 master 上的直接提交与手工 merge 由 `scripts/githooks/` 拒收，`core.hooksPath`
   由 `create` / `integrate` 自动指过去；只有 `integrate` 与 `release_tag.py` 带放行标记。
   记录、锁和 hook 约束统一入口，不是权限隔离；`--no-verify`、快进合并或篡改记录仍可绕过，
   不能宣称绝对防绕过。

## 暂存与提交
- 分支上每个提交的主题都写成 `type(scope): 中文描述`，如 `fix(web): 补齐图标声明与兜底路径`。
  类型的封闭清单在 `scripts/commit_subject.py` 的 `TYPES`；`feat` / `fix` / `perf` 会进变更日志，
  scope 被 `scripts/changelog.py` 换成区域标签，所以写歪的后果是日志静默漏条，不是报错。
  `ready` / `integrate` 拒收形状不对、类型不在清单的主题；从 master 合进来的 merge 不算。

- 提交前核对 README 影响；交付分支最后提交加 `README-Impact: updated; 说明` 或 `README-Impact: none; 原因`。
  只改一份 README 时另加 `README-Reviewed: 未改文件名; 无需修改的原因`，保留双语核对。
  它与 `Co-Authored-By` 等 trailer 连续写在消息末尾同一块里，中间隔一个空行就只算正文，解析不到。
  `commit-msg` 检查署名与已有声明格式；`ready/integrate` 核对交付差异，细节见 `docs/README_MAINTENANCE.md`。

- 分支上每个提交都要署名，形态是 `Co-Authored-By: 工具 (模型 版本) <厂商 noreply>`：
  `Claude Code (Opus 5) <noreply@anthropic.com>`、`Codex (GPT-5.5) <noreply@openai.com>`。
  括号里那一段是重点：事后翻这一行是要知道哪个模型写的，同一个工具换代模型，写出来的
  代码差别比换工具本身还大。工具与地址的名单在 `scripts/co_author.py` 的 `VENDORS`，
  `ready` / `integrate` 逐个提交判，拒收缺失、形态不对、工具未登记和地址与工具不配四种；
  合进来的 merge 与已在 `origin/master` 上的提交（网页上直接改的那种，签不了名）不算。
  智能体接力写成时并列署名；人类共同作者使用「姓名 <邮箱>」，与智能体署名同块保留。

- 禁止 `git add .`、`git add -A`、目录路径或 glob。只暂存任务明确拥有的文件。
- 提交前用 `git diff --cached --name-status` 与任务边界逐条对照。干净的 `git status`
  不能证明归属正确。
- 实现与其测试必须原子提交。反例 `bba0b77`：测试被误判为本任务文件而进入提交，对应
  `probe.py` 未暂存，HEAD 出现「测试指向不存在实现」。

## 工作者禁止事项
自行 merge、执行 `--apply`、把候选标为 `approved`、修改迁移或 ADR、重启生产服务。
这些属于协调者。

## 已知陷阱

- **工作树会惄无声息地失效，目录却还在**。2026-08-26 实例：分支被集成后，
  `.claude/worktrees/<task>` 的注册消失，目录变成主检出里一份旧副本。提示符和文件列表看不出
  任何差别，但在里面跑的每一条 git 都作用于主检出的 master。后果不是报错而是假结论：
  那一轮里智能体一直报告「还没推」，实际上提交已经被另一个智能体的 push 顺带到了 origin。
  判据不看目录名，只看 `git rev-parse --show-toplevel`；等于 `peach-app` 就是在主检出里。
  实测入口：`git worktree list` 里没有你那一行，就是已经没了。
- Windows 源码改动生效时，禁止用 Computer Use 操作系统托盘。只用与托盘「重启服务」等价的项目命令；入口必须让现有托盘执行重启，或完整重启托盘并重新取得子服务所有权。直接强杀／另启 `.venv\Scripts\peach.exe` 会让 `_owned` 失真，不算等价；仓库缺少安全命令时先补入口，不能退回托盘 UI。
- 工作者报 `ready` 前必须 rebase 到当前 `master`。落后十天的分支不要指望协调者去 merge：
  共享文件上你那一侧是旧的，冲突解错就会把已上线的修复回退掉；只移植仍需要的独有改动。

## 回收与顶层归置

工作树用完要回收：`python scripts/agent_worktree.py prune` 列出分支已并入 master 且工作区
干净的工作树，加 `--apply` 才真的删。
脏的一律拒收并单独列出：分支已合入不等于工作区
里没东西，实测就有工作树的分支早已并入 master、里面却躺着一份成形的未提交改动。

一个工作树失败不影响其它工作树：`residue` 是连脚本也删不掉的目录，分支照样删，`holders` 列出占着它的进程（`orphan` 为真是父进程已退出的残留），
按 PID 结束自己起的再重跑；`processes` 子命令列出两处落点下的全部活进程。`failed` 是注册还没摘掉，分支保留。未登记的目录归 `swept`：`peach-worktrees/`
与 `.claude/worktrees/` 两处都扫，只删空的，有文件的进 `kept` 等人确认，因为那种目录和真
工作树长得一模一样，在里面跑 git 全作用于主检出的 master，门槛在 `tests/test_repo_hygiene.py`。

`prune` 保留锁定的活动工作树。不要手动解锁正在编辑的工作树；任务中止时先核对任务归属、
改动与提交，再决定是否解锁回收。

`Desktop\peach` 顶层只放 ADR-0017 定义的四个运行时目录加一个 `attic/`：

    peach-app  peach-data  peach-sync  peach-worktrees  attic

`peach-` 前缀专属那四个，不再新增；别的东西按性质进 `attic/` 的 `builds`／`evidence`／
`instances`／`tools`／`reviews`，目录名写成 `YYYYMMDD-主题`，顶层不放散落文件，
由 `test_repo_hygiene` 守住。这条规则只写在仓库外的 `../attic/README.md` 时，在 peach-app 里
干活的人根本看不到它，清完两天又堆回三个违规目录。 <!-- copy-lint-disable-line -->

`attic/` 不等于可以随便删：`instances/` 常带 100 MB 量级的 ledger 副本，`tools/` 的
`runtime.json` 会留 token。账本副本、复核产物和取证归档按 AGENTS.md 的保留清单对待，
删除要单独确认。
