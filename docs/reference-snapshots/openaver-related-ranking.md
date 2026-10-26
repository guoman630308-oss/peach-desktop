# OpenAver 相似探索算法证据

- 来源：<https://github.com/slive777/OpenAver>
- 固定 revision：`8cc17e50453d9f69a81f5fee1a072df80f7aab73`（v0.16.13，2026-10-01 核对）
- 首次取证：2026-08-31，revision `dca4c0c368ea0c2db9cf15e48977de2fc75e7077`（0.15.6）
- 许可证：MIT

`core/similar/` 与 `core/cf_transport.py` 在这两个 revision 之间没有提交，下文结论对两者都成立；v0.16.13 的 README 仍写规则式相似排序（tag IDF 加系列、片商、女优）。

## 已取得

固定 revision 的 README 明确描述相似探索为本地规则式排序：Tag 使用 IDF 加权，并混合系列、片商、女优、年份和片长等共同点；不使用行为推荐模型，不需要 GPU 或下载模型。详情灯箱以可继续“钻入”的相似作品作为消费表面。

## Peach 采用

- 复用 IDF 抑制高频泛标签的算法方向；
- 混合规范实体中的创作者、出演者、系列、厂牌与 Tag，以及发行年、片长；
- 保留每条推荐的中文原因；
- 在候选集上增加 MMR 多样性约束，避免近重复作品占满队列；
- 使用固定 seed 的 SHA-256 破同分，替换旧的 SQL `random()`，保证同一请求可重放。

## 有意差异

Peach 的相关推荐由 ledger 规范实体和既有 DTO 驱动；负反馈与回收站边界继续由 Peach 负责。MMR 与稳定破同分是 Peach 为队列连续播放增加的约束。

## 验证边界

真实 ledger 只读抽样 20 个种子、每个 12 条时，结果两次运行完全一致；运行前后 ledger SHA-256 一致。该 POC 证明只读性、稳定性和结果可得，不等于用户已完成主观质量验收。
