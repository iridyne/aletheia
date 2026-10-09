# Retired knowledge consumers: Ranker weighting and Operator entry prompt

> 2026-10-09 迁入 history：原为 `docs/design/contracts/knowledge_contract.md` 的 §8.1、§8.2。
> 两者的消费者（`paper_trader_ranker`、`trading_context` / `trading_decision` 入场 prompt）
> 已随 #2945 与 #3134 退役；本页只保留当时的测量语义与 LP-S14 历史枚举。文中行号是当时的
> 源码位置。当前 knowledge 消费者见 [knowledge_contract §7](../contracts/knowledge_contract.md)。

## 8.1 历史 Ranker 权重调整（消费者已退役）

以下为历史测量语义；Ranker 不再运行，这些实验 ID 或历史 exposure 不会自动接入当前 Planner。

`paper_trader_ranker.py` 只检索可信的 `trade_case_study`，并另行读取最新的
`trading_memo`。`ranker_case_score` 现在有独立的 candidate-level 4h
`filter_candidate_outcomes` 因果合同，可通过统一 adapter 创建 Shadow
hypothesis/experiment/exposure；它仍是 Shadow-only，实际 Ranker delta 固定为 0。
`ranker_memo_score` 仍为 measurement-unsupported，不创建 formal exposure。
- Ranker Case 的 prospective outcome 只允许 exact candidate identity、4h horizon、
  policy/provenance 与成本完整的 settled outcome；
- Ranker trace 可记录原始建议与实际零增量，但不得伪造实验/exposure id，也不得让两个来源互相冒领贡献；
- **边界**：Ranker 调整只影响机会排序，不影响 risk_engine 关卡，不影响 sizing。

## 8.2 历史 System Operator 入场 prompt 片段（已退役）

以下三路 trading_context/trading_decision 注入及其旧 LP-S14 枚举属于历史契约，不再是当前入场路径；不得恢复旧 prompt 或据此为 Planner 新增消费权。当前 Operator 诊断 lesson 消费见 §7.2。

System Operator 的 system prompt 分为两层：
- **基础层**（宪法约束，人类专有）：角色定义、FORBIDDEN_INTENTS 提示、风格约定
- **追加片段层**（学习可写入）：以下三条路径

**学习内容注入 System Operator prompt 的三条合法路径**（完整枚举，由 LP-S14 spec claim 锁定）：

| 路径 | 数据来源 | 代码锚点 |
|------|---------|---------|
| 1. 近期战术总结 | `trading_memo` item_type（Insight Synthesizer 写入） | `trading_context.py:406-422` → `trading_decision.py:716-719` |
| 2. 推理质量反馈 | `reasoning_feedback` item_type（Reasoning feedback tracker 写入） | `trading_context.py:425-430` → `trading_decision.py:724-727` |
| 3. 建议性事后教训 | `LessonStore` 的 applied 行（dreaming_agent 写入、lesson_reviewer 审核并应用） | `trading_context.py` Layer3 → `trading_decision.py` prompt |

学习不能直接修改 prompt 基础层文件。上述三条仅为历史学习→入场推理注入路径；新增当前消费者须另行证明并更新对应契约，不继承旧 LP-S14 权威。

> **历史修正**：本节原文写"这是'学习影响 System Operator 推理'的唯一合法路径"（指 `trading_memo`），
> 实际代码同时存在 `reasoning_feedback` 和 `ps_lessons` 两条并行路径
>（`trading_context.py:427` 的 `layer['reasoning_feedback']` 和 `:597` 的 `layer['ps_lessons']`），
> 均通过 `trading_decision.py` 渲染进 System Operator prompt。
> 已于 2026-06-30 修正为三路完整枚举，并补 LP-S14 spec claim 锁住该枚举不变量。
