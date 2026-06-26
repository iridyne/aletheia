# docs/issues/ — 问题与修复登记

> 本目录记录代码层的结构性问题、UI 缺口、运行时行为异常,以及修复计划。
> **意图/事实分离**:这里只写问题与意图,不写运行态事实(进程/DB/凭证)。
> 2026-06-26 做过一次整理,把历史已修区、监测流水、超越的快照报告移入 `archive/`。

## 当前唯一可信的开放 backlog

**[`FIX_PLAN_2026-06-26.md`](FIX_PLAN_2026-06-26.md)** —— 已对 HEAD 逐条核实的开放项,按四类分:
- **A** 已修(2026-06-26 本批 free-scope:numeric input / Exposure 对齐 / smoke / telegram / 计数)
- **B** 待授权(信任边界:三 broker fee 诚实性 · risk_critic 类型洗白)
- **C** 安全不变量缺口(手动触发 agent-run 绕过 paused/kill_switch — 建议优先)
- **D** 暂缓(cosmetic / 无消费方 / 大改)

> ⚠️ 各 domain 文件里的"已修复"自评**不完全可信**——2026-06-26 6-finder 复核发现某轮 overlay 约 62% 误报。引用任何"已修/未修"前先 grep HEAD 核实(见 memory `aletheia-backend-issues-archive-unreliable`)。

## 活跃 domain 登记(详细 file:line + 修复建议)

| 文件 | 内容 | 状态 |
|---|---|---|
| [`BACKEND_ISSUES.md`](BACKEND_ISSUES.md) | 后端结构性问题(§1🔴 信任不变量 / §2🟠 数据失真 / §3🟡 dead code / §6 数据层 / §13–§18 多轮审计 backlog) | 活跃,详细参考 |
| [`FRONTEND_ISSUES.md`](FRONTEND_ISSUES.md) | 前端 UI honesty / 字段缺失 / 工程基础(§1–§3 / §4 视觉微Bug / §6 设计系统对照) | 活跃,详细参考 |
| [`RUNTIME_BEHAVIOR_ISSUES.md`](RUNTIME_BEHAVIOR_ISSUES.md) | 运行时 agent 行为观察(§0–§9:学习闭环 / 知识质量 / agent 产出 / 转化率) | 活跃 |
| [`HUMAN_ISSUES.md`](HUMAN_ISSUES.md) | 用户亲报的 UI 问题(B1-x/B2-x)+ live 实测状态 | 活跃 |
| [`P3_UI_GAPS.md`](P3_UI_GAPS.md) | 小的 P3 UI 增量(timer / 可视化 / WebSocket) | 活跃,低优先 |
| [`LOW_VALUE_TESTS.md`](LOW_VALUE_TESTS.md) | 低价值 mock 测试清理清单(分 Tier) | 活跃,工程债 |

## archive/ — 历史留痕(已修 / 已超越,保审计链)

| 文件 | 内容 |
|---|---|
| `archive/RUNTIME_MONITORING_AND_RESILIENCE_2026-06.md` | RUNTIME §监测日志(06-17→06-26 巡检流水)+ §23/§24 frontdoor/backend 韧性修复写法 |
| `archive/BACKEND_FIXED_HISTORY.md` | BACKEND §4/§10/§12/§5-OAuth 已修历史 |
| `archive/FRONTEND_FIXED_HISTORY.md` | FRONTEND §10/§5-Markets/§4-OAuth 已修历史 |
| `archive/MINIMUM_PROFIT_LOOP_AUDIT.md` | 命脉循环审计快照(2026-06-19);9 项全修,残余已入 BACKEND §19 |
| `archive/SYSTEM_WIDE_DATA_FLOW_AUDIT.md` | 全系统数据流审计快照;15 项全修/已捕获 |
| `archive/PIPELINE_GAPS.md` | 断管道登记快照(A–J 级);10 个未捕获项已迁入 BACKEND §19 |
| `archive/DOC_IMPL_GAP_ANALYSIS.md` | 文档 vs 实现缺口快照;CUM-ADD 风险项已迁入 BACKEND §19 + FIX_PLAN C |
| `archive/issue_register_2026-06-06.md` | 最早的 issue 登记册 |

> 4 份快照报告(MINIMUM_PROFIT_LOOP / SYSTEM_WIDE / PIPELINE_GAPS / DOC_IMPL_GAP)经 4-agent 对 HEAD 逐条核实后归档;开放项已迁入活跃文件,归档前加了 banner 说明。

## 关联

- 信任边界与受控模块:`docs/governance/trust_and_change_control.md`
- 冻结期优化 backlog:`frozen_optimization_backlog.md`(实验冻结期不动的优化项)
- 方向地图(长期参考系):`docs/references/north_star.md`
- SDD spec(可执行契约):`tests/specs/spec_*.py`
