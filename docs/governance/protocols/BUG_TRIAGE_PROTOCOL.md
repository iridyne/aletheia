# Bug Triage Protocol

> **性质**: 硬约束。AI Agent 和开发者发现问题后必须先分类再行动。
> **Created**: 2026-07-12

发现 bug 或设计问题时，**分类先于动手**。不同类别的处理方式不同：混用会污染实验数据（B 类误作 A 类），或浪费精力（C 类占用核心窗口）。

---

## A 类 — 立刻修，不触发实验重置

以下场景**必须修**，且修复不改变行为版本，不需要关闭实验窗口：

| 场景 | 说明 |
|------|------|
| 崩溃 | 任何导致进程退出/挂死的 bug |
| 数据库锁死 | WAL 挂起、事务泄漏、deadlock |
| 网络无限挂起 | 无超时的 HTTP/WebSocket 调用 |
| 数据写错表 | 写入了错误的表或字段 |
| Identity 丢失 | causal ID 断链、entity ID 混淆 |
| Accounting/Outcome 造假 | PnL 计算错误、fill 未记录、outcome 伪造 |
| Exchange adapter 明确错误 | API 返回错误码被静默忽略 |
| 安全边界绕过 | 任何绕过 trust boundary / permission 的路径 |

**处理流程**：直接修 → 测试 → 合并。无需关闭实验。

---

## B 类 — 可以修，但必须开新实验窗口

以下场景**可以修**，但修复本身改变了 AI 的行为，必须诚实对待：

| 场景 | 说明 |
|------|------|
| 排名公式变化 | 排序权重、score 计算逻辑 |
| Prompt / Context 变化 | AI 看到的信息内容或格式改变 |
| 阈值变化 | 任何决策门槛（置信度、最小仓位等） |
| Guard 行为变化 | Guard 规则、shadow 模式、惩罚分逻辑 |
| Sizing 变化 | 仓位大小计算逻辑 |
| Family quota 变化 | 资金分配比例 |
| Debate 权重变化 | 多 agent 投票逻辑 |

**处理流程**：
1. 关闭当前 experiment（或确认当前无活跃 experiment）
2. 提交修复 → 开新 policy epoch
3. 新 observation window 从零开始计数

**B 类结果不能与旧 epoch 数据混用。**

---

## C 类 — 暂缓

以下内容**不是 bug**，是功能扩展。等活动核心稳定后再动：

- 新 Agent
- 新资产（新交易对、新数据源）
- 新 UI 页面
- 新自进化模式
- 新策略功能
- 新复杂抽象（新 base class、新框架层）

不是永远不做——是**等 A/B 类问题清零、至少一个完整实验窗口跑通之后**再动。

---

## 裁决原则

不确定某个问题属于哪类？问自己：

> **"这个修复会不会让同一市场状况下，AI 做出不同的交易决策？"**

- 不会 → A 类
- 会或可能会 → B 类
- 不是 bug 而是新功能 → C 类

---

## 与实验协议的关系

本协议是 [`EXPERIMENT_PROTOCOL.md`](../../product/experiments/EXPERIMENT_PROTOCOL.md) 中"允许修改范围"的补充细化：

- EXPERIMENT_PROTOCOL 的"允许修改"列表 ≈ 本协议的 A 类
- EXPERIMENT_PROTOCOL 的"冻结区" ≈ 本协议的 B 类（修了必须开新窗口）
- C 类是本协议新增的延迟队列概念

`AGENTS.md` 中的 Change Discipline 节有精简版速查表。
