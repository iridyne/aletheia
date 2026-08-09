# Discussion Remaining Plan 2026-07-03

来源: 2026-07-03 owner 口述（现见 [Product Direction](../../../../../product/definition/DIRECTION.md)）、`docs/_ai/discussions/2026-07-03/DISCUSSION_ACTION_PLAN.md`, 以及已验证的
`integration/discussion-2026-07-03` 集成分支。

> **Historical plan.** Its branch names and status labels are retained as
> provenance only. New work must be selected through `workflow bootstrap` and
> reconciled with the current L2 contract and issue ledger.

结论: 当时 owner direction 中的问题尚未全部修复。上一轮已经完成并集成验证了 12 个可核验
落地点, 但还有一批属于「部分完成」「尚未实测」「需要 owner 决策」的问题。本文作为
下一轮目标和执行计划。

## 1. 总体状态

| 编号 | 当时的方向问题 | 当前状态 | 原因 |
|---|---|---|---|
| D01 | 顶栏 BTC/ETH/SOL 价格横线 | 已修 | 改为每个 major 各取最新 1 条, 不再受全市场最近 20 条窗口挤出 |
| D02 | 页面切换缺少加载反馈 | 部分完成 | 已有全局 fetch progress bar + stale cache; 尚未做端到端页面切换性能采样 |
| D03 | Universe momentum 偏斜 | 部分完成 | 已修 quota/explore、regime member、spot/perp、derivatives context、Coinalyze persist; event family 和 post-refresh 生产分布还未复测 |
| D04 | Learning 全链路自学习/自进化 | 部分完成 | SDD 已同步, direction NULL 修复; 仍需验证 trade ETL -> lesson -> KB -> System Operator 消费全链路 |
| D05 | Strategy 剔除、Serenity 归位、Evolve 只留 Learning | 部分完成 | Serenity 已移到 Observe, Strategy 状态需再核对; 完整侧边栏 IA 还未按 owner 最终结构重排 |
| D06 | Trades 布局、Analytics/Insights 合并 | 已修 | subtab 3 -> 2, Blotter 并排显示 Closed Positions |
| D07 | 限价单 + entry/SL/TP/R:R 四字段 | 已修 | native limit 入场缺任一字段拒单, 前端审批展示字段 |
| D08 | System Operator 命名彻底化 | 部分完成 | runtime/display wording 已改; 技术标识符、DB 表、历史归档未改, 需要迁移策略或保留原则 |
| D09 | System Operator 消费 Learning 与 Trades analytics | 未完成 | 需要新增/验证上下文注入路径, 防止只在 UI 里分析而不进入决策 |
| D10 | System Operator doctor 自诊断 | 未完成 | 需要 audit 现有 doctor JSON 与 operator loop, 只能处理 runtime issue, 不能自动改代码或解锁 live |
| D11 | Serenity Lens 24h 产业链扫描能力 | 未完全审计 | 目前只处理了导航归位; 需要确认采集、证据、promote、learning 消费闭环 |
| D12 | TypeScript 后端迁移 | 不建议迁移 | 已评估迁移不是解决延迟根因; 替代方案是 schema/OpenAPI -> TS 类型生成 |
| D13 | pytest 慢测试 | 部分完成 | 已建立 1699 tests / 约 110s 基线和 slow marker; 还未做 fixture/分层进一步优化 |
| D14 | 低信号 lesson 与 app.db 膨胀 | 未完成/需决策 | 涉及 retention/archive/vacuum, 不能硬删知识或历史记录 |
| D15 | `reduce_size` 是否变硬风控 | 未完成/需决策 | 会改变 Risk Engine 行为, 必须 owner 明确批准并补 RISK spec |

## 2. 新目标

下一阶段目标不是继续扩大功能, 而是把剩余的「闭环质量问题」逐条证明并收口:

1. 证明 Learning 产生的知识能进入 System Operator 决策上下文。
2. 证明 Universe 修复后新样本分布真的改善, 不只是在合成测试中改善。
3. 证明前端页面切换和顶栏数据在运行态有可量化延迟指标。
4. 证明 System Operator 的运行时自诊断只处理 runtime issue, 不碰代码、live、凭证和交易权限。
5. 明确需要 owner 决策的风险/数据保留/IA 事项, 不把它们伪装成已修 bug。

## 3. 下一批分支

| 顺序 | 分支 | 边界 | 交付物 | 量化指标 |
|---|---|---|---|---|
| P1-1 | `audit/learning-to-operator-context` | 只审计/测试 Learning -> KB -> System Operator prompt/context; 不改风险/执行 | SDD claim + 回归测试或缺口报告 | 至少 1 条 synthetic lesson 能被决策上下文读取 |
| P1-2 | `audit/universe-post-fix-distribution` | 用 test DB 或只读生产库采样; 不直接改 live data | post-fix distribution 脚本/报告 | momentum、market_type、regime_tag、derivatives field 覆盖率四项指标 |
| P1-3 | `fix/operator-trade-analytics-context` | 只把已平仓交易 analytics 摘要接入 System Operator 上下文; 不改变下单规则 | focused test + prompt/context snapshot | analytics summary 从 0 个字段提升到明确字段集合 |
| P2-1 | `audit/frontend-runtime-latency` | 只测量页面切换/API 延迟; 不先改 UI | Playwright/API timing worksheet | 每个核心页面 TTFB/settled time, 找出 top slow endpoints |
| P2-2 | `fix/frontend-latency-top-offenders` | 只修 P2-1 定位出的慢路径 | targeted UI/API fix | 页面 settled time 或请求数下降百分比 |
| P2-3 | `fix/operator-doctor-runtime-supervision` | 只允许 doctor 读状态和生成诊断; 不允许改代码/解锁 live/下单 | spec + safety guard | doctor JSON issue -> advisory runtime note 1 条 |
| P3-1 | `docs/sidebar-ia-decision-record` | 只记录建议 IA, 等 owner 确认后再改 UI | IA 草图 + decision record | 明确 Evolve/Observe/Execute/Learn 的归属 |
| P3-2 | `fix/ts-type-generation` | 不迁移后端; 只生成/校验前端类型来源 | schema/type generation script 或 doc | 手写 API 类型减少项数 |
| P3-3 | `audit/test-fixture-speed` | 不大改测试框架; 只定位 fixture 慢点 | durations + fixture report | top 20 durations 和可优化点 |

## 4. 不能静默处理的 owner 决策

以下项目在没有 owner 明确决策前不应直接改代码:

1. `reduce_size` 从 advisory-only 升级为 hard block。
2. app.db retention/archive/vacuum 策略。
3. 完整侧边栏 IA, 尤其是 Learn/Evolve/Observe 的最终命名和层级。
4. 是否迁移技术标识符 `pi_daemon`, `pi_decisions`, `PI-S*`。
5. 是否允许 System Operator 对 runtime issue 做自动动作, 以及动作白名单。

## 5. 下一步执行顺序

先做 `audit/learning-to-operator-context`。原因:

1. 它是 discussion 中最核心的自学习闭环。
2. 它不需要 owner 先做产品 IA 决策。
3. 它能直接判断 D04、D09 是否是真闭环, 还是只有文档/局部测试闭环。
4. 若发现断点, 后续可以拆成 focused fix 分支, 每个断点单独测试。

完成标准:

```text
给出一条可复现证据:
closed trade / lesson / KB item / analytics summary -> System Operator context
至少一个路径 GREEN; 若 RED, 必须形成具体 bug 分支和失败测试。
```
