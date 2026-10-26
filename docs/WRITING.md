# 文档与界面文案

Peach 使用 [seiso](https://github.com/scarletkc/seiso) 检查 Markdown 的职责、链接和规则例外。中文表达遵循 tech-doc-style-chinese；界面文字还需对照实际操作、错误状态和可用的恢复方式复核。

## 文档职责

文件分类由 [seiso.toml](../seiso.toml) 维护。新增文档须选择一种职责；已有路径映射不符合内容时，在 frontmatter 声明 `kind`。

| 内容 | 归属 | kind |
| --- | --- | --- |
| 产品用途、安装入口、文档导航 | [README](../README.md)，英文版同步维护 | `readme` |
| 安装、配置、测试与维护步骤 | [运行与配置](OPERATIONS.md)、[测试与依赖](TESTING.md) 等操作文档 | `howto` |
| 架构、术语、接口约束、复用依据 | [总体架构](ARCHITECTURE.md)、[开发约定](../AGENTS.md)、[复用清单](REUSE.md) | `reference` |
| 决策背景、选择与代价 | `adr/` 下按编号命名的决策文件 | `adr` |
| 尚未完成的工作 | [公开路线图](https://github.com/orgs/peach-mitao/projects/7) | `plan` |
| 面向使用者的发布记录 | [变更日志](../CHANGELOG.md) | `changelog` |

同一事实只维护一处，其他页面链接到文件或章节。版本、文件数量、部署状态优先指向清单、代码或查询入口；保留观察值时写明核验日期。代码版本不能用来推断服务已更新。

README 保留开始使用所需的信息。字段定义放参考文档，操作步骤放 howto，设计理由放 ADR。保留现有章节锚点；移动内容时同步修复引用。

## 公开与私有记录

- 本机交接、运行状态、个人待办、会话复盘、馆藏盘点和逐条复核记录保存到 `<Peach 数据目录>/state/project-context/`、`review/` 或 `archive/`，不进入 Git。
- 公开文档使用中性账号、`<项目目录>` 等占位路径与 RFC 5737 文档地址。主机名、网络布局、媒体文件名与馆藏数量不从真实部署复制。
- 公开参考证据保留来源、许可和复现方法；本机样本清单、原始日志与账号信息留在私有记录中。

## 界面文字

- 按钮写动作和对象，如「保存配置」「删除播放列表」。导航、按钮与提示中的同一概念使用同一名称。
- 空状态区分尚未创建、筛选无结果、没有权限与读取失败。说明下一步时，只写页面实际提供的操作。
- 错误标题指出对象；正文保留原因，并说明可行的恢复方式。请求超时不能说明后台任务已经停止，先查看任务状态再决定是否重试。
- 删除、清空、卸载等确认文字说明对象、保留内容和可撤销范围；保留现有安全条件。
- 界面面向使用者说明行为。数据库列、内部状态码、实现细节放开发文档；配置所需的路径、域名和字段保持准确。
- 中文使用中文标点，数字与单位、中文与英文间按语义留空格。代码标识、API 字段、URL、站点原名、引用及第三方许可原文不作文字替换。

共享请求错误由 [requestErrorMessage](../frontend/src/core/index.ts) 提供，React 的 [API 封装](../frontend/src/api.ts) 复用它；页面在错误标题中补充操作对象。文案编辑不改变字段值、路由、任务状态或数据写入条件。

配置页外部入口说明由 [configuration-copy.ts](../frontend/src/configuration-copy.ts) 维护，首屏骨架和正式表单共用。修改说明时，两处的换行和高度也须一致，由浏览器几何用例验证。

## 检查与复核

先按 [测试与依赖](TESTING.md#环境与锁文件) 安装锁定的开发环境。以下命令在仓库根目录执行，检查全部项目 Markdown：

```text
uv run --locked --extra dev seiso check
```

查看实验规则提示，逐条核对事实和上下文：

```text
uv run --locked --extra dev seiso check --preview --output-format concise
```

查看实际分类、排除范围及启用的规则：

```text
uv run --locked --extra dev seiso policy
```

正式测试入口的文案检查执行 seiso 稳定规则，并保留 [最终状态检查器](../scripts/check_copy_final_state.py)。预览规则仅供人工复核，不作为 CI 门槛；语言风格、内容真伪和界面适配也不能由 seiso 的通过结果证明。

最终状态检查共用 `scripts/copy-rules.json`。明确叙述实现变更、测试名称描述变更过程时拒绝通过；可能属于条件说明的词语只提示人工核对。合法条件说明不需要添加豁免标记。明确需要保留的事故原文仍用 `copy-lint-disable-line` 逐行说明例外。

Python 检查读取 AST 字符串、f-string 静态片段及注释；HTML 读取正文、注释和可见属性。JS/TS 由 `frontend/scripts/copy-check.mjs` 使用 Oxc 解析注释、字符串、模板及 JSX，忽略变量名、静态属性键和模块路径。前端文案检查随正式入口的前端 lint 用例执行，CI 由 `web-bundle` 执行；纯 Python 文档检查不要求安装 Node。

提交模板不是成稿；`docs/reference-snapshots/upstream/` 保存第三方原文，相对链接属于上游仓库。这两类在配置中排除。Peach 自己撰写的取证摘要、ADR、技能和依赖来源说明仍接受检查；构建目录、依赖安装目录不属于项目文档。

必要例外按 [seiso 的例外语法](https://github.com/scarletkc/seiso/blob/main/docs/guides/checking.md#explain-an-exception) 写明完整规则编号和原因。规则提示需先判断是否成立，不能通过扩大排除范围掩盖项目问题。
