# 参与 Peach

Issues and pull requests are welcome. Small fixes (bugs, copy, documentation, tests) can go straight to a PR; large changes — new pages, new connectors, contract or data-layer changes, anything an ADR covers — should be discussed in an issue first. Read [`AGENTS.md`](AGENTS.md) before opening a PR: it is the single entry point for development rules, and reviews follow it. Each platform has exactly one test entry point: `& .\scripts\test.ps1` on Windows and `./scripts/test.sh` on macOS/Linux. By submitting a contribution you agree to license it under AGPL-3.0-or-later. The rest of this document is in Chinese.

Peach 是单人自托管应用，由一个人维护。欢迎提 Issue 和 PR。

- 小修（修缺陷、改文案、补文档、补测试）可以直接提 PR。
- 大改动（新页面、新连接器、改接口约定或数据层、涉及 ADR 的方向）请先开 issue 讨论，免得做完才发现和既定设计冲突。

提交贡献即表示同意以 AGPL-3.0-or-later 授权。

## 开 Issue

- **报缺陷**：写清 Peach 版本或构建、系统版本及架构、安装方式、复现步骤、期望结果和实际结果，附上完整报错。无法稳定复现时说明发生条件，不要求自行判断根因。Python 版本仅用于源码部署，可选；`/healthz` 返回也可选，服务无法启动时写「无法访问」即可。贴日志或诊断报告前先遮盖本机路径、网络地址、账号及凭据。
- **提需求**：写清问题和使用场景。开放需求见 [公开路线图](https://github.com/orgs/peach-mitao/projects/7)，设计边界见 [总体架构](docs/ARCHITECTURE.md#明确不做)。涉及多用户、云托管、微服务或 PostgreSQL 的提案，先讨论是否需要新增 ADR。
- **安全问题**：不要开公开 issue，按 [`SECURITY.md`](SECURITY.md) 私下报告。

## Issue 处理规范

维护者和协助维护的智能体按本节处理 issue。回复跟随报告者的语言；提交模板及标签说明提供中英文。未经明确授权，不修改他人 issue 的标题、正文或评论；需要归纳时另写维护者摘要。已有 issue 的分类、关联、负责人和开关操作按当前任务授权执行，不因整理规范批量修改。

### 回复、提及与格式

- 首次回复简短确认问题，说明已确认的事实或尚待验证的判断，再给出下一步。需要补充信息时一次列出必要项目，不要求用户完成与问题无关的环境自查。
- 修复回复说明原因、修复内容、可下载的版本链接及验证范围。代码通过测试与原用户环境复测分别说明，不把推测写成已确认原因。
- 只有需要指定的人补资料、复测或处理时才 `@mention`，并说明具体动作；普通回复不重复提及，避免无关人员和团队通知。
- 标题简短描述现象，不堆叠类型、状态或版本前缀。正文用短段落和必要列表；日志用代码块，长日志用 `<details>` 折叠。截图、日志和诊断报告都先检查隐私信息。
- 链接使用明确文字，例如「0.40.1 下载」「修复 PR #123」；关联其他 issue 时说明关系。

### 右侧字段

| 字段 | 约定 |
| --- | --- |
| Type | `Bug`：异常行为；`Feature`：功能建议；`Task`：明确的维护任务。缺陷和需求模板分别设置 `Bug`、`Feature`，维护者创建的维护任务使用 `Task`。 |
| Labels | 按需补充 `platform:*` 平台、`area:*` 模块及 `status:*` 处理条件。多个受影响平台或模块可并列；不要猜测未确认的影响面。标签定义见 [标签清单](.github/labels.json)。 |
| Assignees | 指定实际认领并处理的人；未认领时留空，不把报告者当作负责人。 |
| Milestone | 只关联已确定的目标版本或阶段；没有发布承诺时留空。修复已发布版本同时在回复中给出链接。 |
| Projects / Priority | 仅在实际使用项目排期时维护；状态和优先级各选一个记录位置，不同时用项目字段和 label 重复表达。 |
| Relationships | 仅记录明确的父子任务和阻塞关系，不能根据相似标题推断依赖。 |
| Development | 关联实际修复该问题的 PR，不关联仅供参考或比较的 PR。 |

`status:needs-info` 表示缺少定位所需的信息，回复中应列明具体缺口；`status:needs-verification` 表示修复已有可下载版本，等待目标环境验证。两者互斥，条件消失时移除。未处理、处理中、已关闭不另设状态标签。没有明确影响的平台或模块可以留空。

`bug`、`enhancement` 保留给 PR 和未设置 Type 的 issue；使用 Type 的新 issue 不重复添加同义标签。`dependencies`、`javascript`、`github_actions`、`python:uv` 用于对应依赖更新，`documentation`、`accessibility` 等按实际内容使用。标签名称保持稳定，颜色和双语说明以清单为准；修改清单时同步 GitHub 并回读核对，不批量删除或重命名已有标签。

### 关闭、重开与关联

- 修复已发布且有相应验证依据后，回复解决版本和验证结果，再以 Completed 关闭。原用户环境仍待确认时保持 Open，并使用 `status:needs-verification`；仅合并 PR 不作为已解决的依据。
- 同一问题在修复版本仍可复现时重开，补充版本与复现证据；不同问题另开 issue 并相互引用。
- 重复问题先链接到继续跟踪的 issue，说明重复依据，再以 Not planned 关闭；明确不在项目范围或不计划处理时说明原因，也以 Not planned 关闭，不把这些情况标成已修复。
- 等待信息时保持 Open，不因暂时没有回复自动关闭；不设置自动过期或批量关单流程。
- 修复 PR 和提交用普通编号引用，例如 `Refs #123`。本项目按发布与验证结果手动关闭 issue，不使用会在合入默认分支时触发自动关闭的 `Fixes #123`、`Closes #123` 或 `Resolves #123`。

GitHub 的字段与模板机制见 [Issue types](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/managing-issue-types-in-an-organization)、[Issue 模板配置](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/configuring-issue-templates-for-your-repository) 和 [PR 与 issue 关联](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)。

## 提 PR 之前

先读 [`AGENTS.md`](AGENTS.md)，它是开发规则的唯一入口，PR 评审也按它执行。分支、暂存与提交的细则见 [`.claude/skills/peach-worktree/SKILL.md`](.claude/skills/peach-worktree/SKILL.md)。最容易踩到的三条：

1. 不用 `git add .`、`git add -A`、目录路径或通配符，只暂存这次改动的文件。
2. 实现和它的测试放在同一个提交里。
3. 一个 PR 只做一件事，并在 PR 模板的「影响面」表里逐项说明（数据层、API、页面、接口约定、测试、文档），不适用的也写出来。

## 测试

每个平台只有一个测试入口：

```powershell
& .\scripts\test.ps1            # Windows
```

```bash
./scripts/test.sh               # macOS / Linux
```

不带参数时按改动的文件自动选测试范围，判断不了就跑全量。也可以手动指定范围，例如 `-Scope follow`（macOS/Linux 写 `./scripts/test.sh follow`），可选 `follow`、`catalog`、`media`、`sync`、`metadata`、`web`、`tooling` 等。跨多个范围、改数据库迁移、改共享测试设施、改依赖或大面积改动时，请用 `full` 跑全量。

脚本会自己找到虚拟环境并核对导入路径，不要手动拼 venv 路径或另写测试命令。测试框架是标准库 `unittest`。

## 前端改动

`frontend/` 需要 Node 24 或更高版本。

运行 Peach 时不需要 Node，Python 服务和安装包直接读取构建好的 `web/dist/`，所以它要提交进 Git。改了 `frontend/src` 后，在同一个 PR 里运行 `npm --prefix frontend run build` 并提交 `web/dist/`；CI 的 `web-bundle` 任务会检查构建结果和源码是否一致。目录结构与样式约定见 [`docs/FRONTEND.md`](docs/FRONTEND.md)。

## 提交说明与变更日志

- 提交主题使用 `type(scope): 中文描述`，例如 `fix(web): 修正列表滚动`；类型与范围按项目提交检查器核对。
- 破坏性变化在主题里加 `!`，或写 `BREAKING CHANGE:` 脚注。
- PR 不用改 [`CHANGELOG.md`](CHANGELOG.md)。版本、变更日志与发布统一由 `scripts/release_tag.py` 处理，流程见 [运行与配置](docs/OPERATIONS.md)。

## 文案

- 文档职责、事实归属和界面提示遵循 [文档与界面文案](docs/WRITING.md)。正式测试入口执行 seiso 稳定规则；预览规则供人工复核。
- 文档、注释、界面文字和测试名一律用中文；代码标识、命令、协议名和库名保留英文。
- 只写现在的状态，不写「改动前后」的对比，也不保留被否掉的做法。`tests/test_copy_final_state.py` 会检查这一条；确实需要记录的事故原文，逐行加 `copy-lint-disable-line` 放行。
- 不要提交媒体、数据库、凭据、日志、构建缓存和个人信息（局域网地址、主机名、账号名、个人目录）。
