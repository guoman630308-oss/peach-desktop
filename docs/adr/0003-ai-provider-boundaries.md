# ADR-0003：可插拔 AI Provider 与凭据边界

- 状态：Accepted（接口边界）；实现分阶段
- 日期：2026-08-14

## 背景

Codex/Claude Code 是带线程、工具和权限的 agent runtime；OpenCode Go 提供推理 HTTP API。把它们伪装成完全等价的 `LLMProvider` 会隐藏协议、认证、取消和条款差异。

## 决策

- 分成 `InferenceProvider`（models/complete/stream/health）与 `AgentProvider`（start/resume/stream/cancel/status）。
- OpenCode Go direct HTTP 是首个 Inference Provider 候选。
- Codex 通过正式 SDK/app-server 的本地 stdio adapter 接入，由 Codex 自管 ChatGPT 登录；不得抽取登录 token 作为通用 OpenAI API 凭据。
- Claude 正式发布默认使用 API key/云 provider。调用用户本机已登录 `claude` 的订阅 adapter 只标为 personal-local experimental；Peach 不实现 Claude.ai 登录、不读取或保存 OAuth 凭据。
- 第一阶段只提交接口、配置与无副作用 health 边界，不发送真实模型请求。
- 第一阶段 registry 与受认证的 `/api/providers` health 已实现；它只检查本机命令可用性并返回公开 capability，不联网、不读取凭据、不返回可执行文件路径。
- OpenCode Go 的首个真实 adapter 仅实现公开模型发现：显式请求 `/api/providers/opencode-go/models` 时读取官方 `/zen/go/v1/models`，结果缓存 5 分钟并只返回规范字段。它不调用推理端点、不读取 OpenCode CLI 的凭据文件。
- AI 只能生成候选、解释与补全；写入 ledger 真相字段必须经过 provenance/confidence/review。

## 凭据

`ledger.db` 只保存 `secret_ref` 和非敏感配置。API key 后续使用 Windows Credential Manager/DPAPI CurrentUser；Codex/Claude OAuth 由各自 CLI/SDK 保存。日志必须脱敏，子进程使用 argv 数组、`shell=False` 和最小环境。

## 被否决的方案

- 抓浏览器 Cookie 或复制 OAuth token。
- 把 Codex/Claude CLI stdout 当稳定的通用推理 API。
- 阶段一增加独立 AI gateway 或微服务。

## 协议依据

- OpenCode Go endpoints 与模型列表：<https://opencode.ai/docs/go/>
- Provider/API key 配置边界：<https://opencode.ai/docs/providers>
