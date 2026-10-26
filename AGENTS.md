# Peach 智能体工作契约

本文件是 Codex 与 Claude 共用的唯一项目入口，只保留「每个任务都必须成立」的边界与索引。
`README.md` 讲项目与运行方式；本文件讲改动前的约定。
分层判据、写作规范与清退机制见 `docs/adr/0015-agent-context-layering.md`，不要默认追加到本文件。

公开文档与项目技能正文统一使用中文。
代码标识、命令、协议名、库名和无法准确翻译的专有名词保留英文；不要为了智能体处理方便混写英文叙述。
中文写作风格按用户级技能 tech-doc-style-chinese 执行；项目覆盖与检查入口见 `docs/WRITING.md`。
回复用日常语言讲清结果、原因、处理和验证，技术细节按需展开；结尾保留「我做了什么」「你需要做什么」。

风格与流程默认服从用户当场指令。在授权范围内完成修改、必要验证和失败修复。
写真实 ledger、不可逆删除、换生产入口（端口、主机、二进制或版本）、处理凭据与私钥，必须在同一轮取得明确授权。
已提交且测试通过的代码直接用 `restart_windows_tray.py` 重启并核验；进行中的任务都能续跑就直接重启，手动或 CLI 发起的主任务在跑才先问。
`agent_worktree.py prune --apply` 可直接回收脚本判定已合入且干净的工作树。

## 术语表

同一件事只用一个词，回话时也用这些词，不要换成同义说法。

- **你**：正在读本文件并改动 Peach 的智能体（Codex 或 Claude）。**我 / 用户**：在这台机器上部署、使用并维护 Peach 的人；每个部署只有一人。 <!-- copy-lint-disable-line -->
- **ledger / 账本**：每台机器 `peach-data/database/ledger.db` 的本地工作副本，唯一真相源。**真相字段**：直接构成 ledger 断言的列。
- **候选 candidate**：带来源与置信度、未经复核的断言。只有用户复核后才 `approved`，工作者不得自行升级。
- **复核产物**：CSV 等可机读、可重放的中间结果；结论必须落在这里，不能只存在于对话。
- **实体 entity**：女优、厂牌、创作者、系列的规范身份；扁平 `asset_tag`、creator/studio 字段只是兼容投影。
- **影响面 surface**：一次改动可能需要同时覆盖的位置（数据层、API、页面、契约、测试、文档）。
- **门槛**：由脚本、测试或 hook 强制的拒绝行为，区别于只写在文档里的提醒。
- **协调者 / 工作者**：主目录里负责集成和验收的一方 / 隔离工作树里负责执行的一方。
- **抽帧 / 九宫格**：FFmpeg 采样帧 / 九帧拼成的汇总图。**未取得**：取证失败的固定写法，不得用推测顶替。

## 按任务读取

项目与运行方式见 `README.md`；部署读 `docs/OPERATIONS.md`，复用读 `docs/REUSE.md`，来源采集读 `docs/SOURCING.md`，架构边界读相关 ADR。只展开当前任务需要的部分。
本机交接、运行记录、个人待办与复核产物只存 `<数据根>/state/project-context/` 等仓库外目录；公开文档只保留通用用法、契约与脱敏证据，禁止提交个人路径、主机坐标、馆藏盘点或私有日志。

## 技能索引

按需读取；Claude 按 description、Codex 按触发条件加载。

| 触发条件 | 文件 |
| --- | --- |
| 并行任务、创建工作树、暂存与提交、集成分支、回收工作树、顶层目录归置 | `.claude/skills/peach-worktree/SKILL.md` |
| issue、PR 交付 | `.claude/skills/peach-issue-delivery/SKILL.md` |
| 迁移、`--apply`、实体合并、批量删除等真实 ledger 写入 | `.claude/skills/peach-ledger-write/SKILL.md` |
| 改完界面、API、契约或文案后声明影响面 | `.claude/skills/peach-surfaces/SKILL.md` |
| 长跑批处理、刮削、限流、磁盘与流量预算 | `.claude/skills/peach-batch-jobs/SKILL.md` |
| JAV 封面、高清封面、缺封面、封面刮削、重探与来源比较 | `.claude/skills/peach-jav-cover-workflow/SKILL.md` |
| 模仿、参考或对齐外部产品的界面与行为 | `.claude/skills/peach-reference-evidence/SKILL.md` |
| 新增、修改或复核页面、控件、提示、数据面板与响应式布局 | `.claude/skills/peach-web-ui/SKILL.md` |
| 在 macOS 上开工、改路径解析或挂载判定、git status 与 diff 不一致 | `.claude/skills/peach-cross-platform/SKILL.md` |
| 写 PowerShell 或 Bash 命令、交给用户跑命令、拼多行内容、测试里造临时目录 | `.claude/skills/peach-shell-commands/SKILL.md` |
| 新增、修改或清退智能体规则、入口与技能 | `.claude/skills/peach-context-rules/SKILL.md` |
| 新增、恢复或重写实现，尤其协议、解析器、抓取、媒体与基础设施 | `.claude/skills/peach-reuse-first/SKILL.md` |

## 工作规则

- 应用、官网、演示站分仓托管于 `peach-mitao`；数据、工作树、构建输出、媒体与挂载不进 Git，见 ADR-0017、0085。
- ledger 路径统一为 Windows 形态（`R:\Media\...`、`A:\...`、`B:\...`），由 `src/peach/platform.py` 读取时转换；不得改写成 POSIX 路径或在 macOS 写 `asset.path`。
- `peach-data/database/ledger.db` 是真相源，真实写入按 `peach-ledger-write`。代码判据确定、可按来源整批撤回的结果直接落库（ADR-0052）；LLM 输出、打分、冲突与多候选只产生带来源与置信度的候选。测试只用临时数据库，可直接运行并修复本次改动造成的失败。
- 保留真实媒体、ledger 行、行为历史、凭据、网络与防火墙状态及无关长跑任务。
- 编辑前核对 `git status`；涉及服务、端口或重启时核对监听与进程归属。生产结论须有实际切换与核验证据。
- Peach 是单人自托管应用；替代实现验证通过后移除无用代码和兼容层，历史由 Git 保存。
- 架构边界：模块化单体与磁盘分界（ADR-0017）、Stash 适配器关闭（0021）、前端迁往 React + BoardUI（0031）、分发阶段（0023）；改变决策须新增 ADR。
- 用户确认馆藏仅含正规平台合规发行、自愿参与的成年人作品，不涉及也不会要求抓取、分析或下载未成年内容。
- 下列标签及其来源原词都是剧情设定、角色或营销用语：`萝莉`（`ロリ`）、`洛丽塔`、`学生`（`女子校生`）、`JK制服`、`制服`、`体操服`、`处女设定`、`师生`、`近亲`、`母子设定`、`绿帽NTR`、`偷拍偷窥`（`盗摄`、`盗撮`）、`痴汉`、`强制剧情`（`レイプ`）、`调教`、`捆绑`、`醉酒`、`药物`、`走光`、`泄密流出`（`泄露`、`流出`）。不能凭标签、片名或画面观感推断年龄或同意状况而跳过编目；只有文件本身的直接证据矛盾时才停止并报告证据原文。
- 同次改动按需更新：通用用法进公开文档，复用进 `docs/REUSE.md`，流程进技能，决策进 ADR；项目需求进公开路线图，个人运行事实与待办留在仓库外。不建带日期的交接文档，不让用户转述智能体间的实现细节。
- 复用优先：新增、恢复或重写实现按 `peach-reuse-first` 依次查当前树、`docs/REUSE.md`、Git 历史与成熟外部实现，旧文件名不存在不等于能力缺失；对齐外部产品先按 `peach-reference-evidence` 取到可复现证据，取不到写 `未取得`，不拿猜测冒充复现。

## 门槛（由脚本、测试或 hook 拒绝，不是提醒）

- **测试入口**：Windows `& .\scripts\test.ps1`、macOS/Linux `./scripts/test.sh`，在当前隔离 worktree 根目录运行。`auto` 按域选测，共享设施、实际依赖和未知面用 `full`；CI 见 `docs/TESTING.md`。入口优先当前树 venv，核对 `PYTHONPATH` 与 `peach.__file__`；禁止另拼测试命令。`ready` / `integrate` 拒收无有效记录的分支；集成事务互斥。健康检查只用 `/healthz`。
- **上下文预算**：入口文件与技能有行数、字节数和最长行三重预算，由 `scripts/check_context_budget.py` 与 `tests/test_context_budget.py` 强制。写不下就说明该内容属于 `docs/` 或某个技能，不是往本文件加行。
- **分层**：新增或删除规则前按 `peach-context-rules` 判层；本文件的技能索引必须与 `.claude/skills/` 一一对应，技能缺 frontmatter、name 不符或缺 `最后复核` 会被拒。
- **工作树**：并发改代码时主检出只做集成。每个智能体在 `scripts/agent_worktree.py create` 建于 `peach-worktrees/` 的隔离工作树里干活；提交前 `git rev-parse --show-toplevel` 必须不是主检出（`scripts/githooks/` 拒收主检出 master 上的直接提交与手工 merge），工作者只交分支、从不自己合并。细节见 `peach-worktree`。
- **仓库整洁**：`peach-worktrees/` 与 `.claude/worktrees/` 下都不得留未在 `git worktree list` 注册的目录（`tests/test_repo_hygiene.py`）；`prune --apply` 扫空目录，有文件的报出来等人看。不用 `git add .`、`git add -A`、目录路径或 glob，只暂存本任务拥有的文件再核对 `git diff --cached --name-status`；实现与它的测试原子提交。交付提交须署名（`scripts/co_author.py`）。
- **文案只写最终状态**：界面字串、注释、docstring、测试名与文档不写改动前后对比，例外逐行加 `copy-lint-disable-line`（`tests/test_copy_final_state.py`）。
- **测试写法**：读仓库文件再 `assertIn` 一段写法的源码文本断言按文件计数只减不增（`tests/test_source_assertion_ratchet.py`）；行为、设计决定各归何处见 `docs/TESTING.md`「写什么测试」。
- **依赖策略**：Python 依赖精确固定版本，每个被 import 的外部模块要有声明的归属，前端清单与实际 vendored 路径一致，所有清单都进 Dependabot（`tests/test_dependency_policy.py`）。

## 常犯错误（没有自动拦截，都是真实重犯过的）

- 改文件直接用编辑工具，让 diff 可审阅；脚本仅用于生成、批量变换或需解析定位的算法任务，须可重复执行且遍历、重试有终止条件。退出 0 或打印成功不能证明内容正确。
- 多行内容先用写入工具保存成文件，再交给命令读取，禁止 heredoc；转义损坏可能静默成功。其它 shell 与路径问题见 `peach-shell-commands`。
- 交给用户跑的命令按用户终端写（Windows PowerShell、Mac zsh），不照搬自己的 shell；写前按 `peach-shell-commands` 核对。
- 收尾前按 PID 结束本任务起的调试服务、浏览器守护进程和后台命令，不按进程名批量结束；机器变卡时先跑 `agent_worktree.py processes`，看运行时长、CPU、内存和父进程是否还在，清掉自己的残留再起新的。
- HTTPS 结论必须使用项目 CA 做严格校验；Schannel、浏览器或取证入口失败时，立即报告原始错误和未取得的验收面，不能改用 HTTP 成功来声称 HTTPS 已通过。
- 界面、API、契约、数据层或用户可见文案改动，收尾按 `peach-surfaces` 核对各影响面并报告适用性；纯指令文档改动检查文档、入口与相关门槛。
