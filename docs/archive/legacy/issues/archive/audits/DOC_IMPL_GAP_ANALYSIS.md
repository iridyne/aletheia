> **⚠️ 已归档(2026-06-26)** — 本报告是 2026-06-19→06-23 的点对点快照。4-agent 对 HEAD 逐条核实:列举项绝大多数已修或已被 `FIX_PLAN_2026-06-26.md` / `frozen_optimization_backlog.md` 捕获;仍开放且未被别处记录的残余项已迁入 `../BACKEND_ISSUES.md` §19。当前开放 backlog 以 FIX_PLAN 为准。

# 文档 vs 实现缺口分析

> 审计日期：2026-06-23
> 范围：设计契约文档 vs 实际代码全面对比
> 结论：核心信任链路缺口为零；缺口集中在"智能进化"和"运维韧性"两层

---

## 已完整兑现的承诺（无缺口）

| 设计承诺 | 实现质量 |
|----------|----------|
| Risk Engine 16关串行审批 | 超额兑现：实际 17 个检查点，0 stub，含 drawdown scaling |
| `_eval_lock` 防 TOCTOU | 完整 |
| 每次评估持久化 RiskDecision | 完整 |
| Kill switch 落盘 + 只允许 human 恢复 | 完整 |
| FORBIDDEN_INTENTS 定义 + 4处强制执行 | 完整 |
| 因果链不可断（proposal→order→fill→position） | 完整 |
| Execution Gateway 唯一下单路径 | 完整 |
| 策略晋级门（paper_passed→live_candidate→live_limited） | 完整，7阈值+3系统级检查 |
| PI Agent 作为唯一 LLM 交易决策层 | 完整，含辩论机制+conviction scoring |
| Self-Evolution Tier 1（4h guard_candidate 生成） | 完整，真实运行中 |
| SQLite WAL + 每线程独立连接 + was_in_tx | 完整 |
| 实验协议冻结区定义 | 完整（EXPERIMENT_FREEZE 已生效） |

---

## 有实质实现但偏离文档设计的（架构偏差）

| 文档承诺 | 实际实现 | 偏差程度 |
|----------|----------|----------|
| Operator Layer 双进程（Python ↔ Node/TS） | 单进程内异步双层（scheduler线程 → asyncio PI daemon） | 中 — 功能等价但架构简化 |
| Operator 8种运行模式状态机 | 仅 permission_mode 二值判断（Paper/Full） | 中 — 缺 Investigating/Repairing/Dreaming 等精细模式 |
| PI Daemon Heartbeat 完整字段 | 仅3个字段（running/event_set/lock_held） | 低 — 可观测性不足但不影响正确性 |
| Level 1 actions 自动执行 | 实际降级为"推荐+等PI确认" | 低 — 比文档更保守，安全侧偏差 |

---

## 纯空壳/零代码的承诺（真正的缺口）

| 文档承诺 | 来源 | 缺口性质 |
|----------|------|----------|
| Self-Evolution Tier 2（LLM 周期归纳 guard→rule） | self_evolution_plan.md | 零代码，计划 2026-07-01 |
| Self-Evolution Tier 3（确定性 scanner rule 提炼） | self_evolution_plan.md | 零代码，计划 2026-08-17 |
| SkillStore 写入路径 | learning/skills.py | 表+CRUD存在，但无任何调用方写入技能 |
| Session 日常压缩/崩溃恢复 | PI Agent 计划 §12 | 零代码 |
| Watchdog 守护进程（检测卡死→自动重启→degraded） | PI Agent 计划 §5.2 | 零代码 |
| Insight Feedback Loop | plans/insight_feedback_loop.md | 未审计，大概率零代码 |
| 期货集成 | plans/futures_integration_plan.md | 基础设施存在（okx_broker），完整流程未闭环 |

---

## 量化评估

| 层级 | 兑现度 | 说明 |
|------|--------|------|
| 核心交易/风控路径 | ~95% | 安全关键路径完全兑现，甚至超额 |
| Operator/Agent Shell 层 | ~60% | 核心有，精细化缺（模式/heartbeat/自动执行） |
| 自演化/学习层 | ~30% | 只有 Tier 1 在跑 |
| 运维韧性层 | ~20% | 基本靠外部 Task Scheduler 看门狗兜底 |

**总体评估**：文档写得比代码超前约 1-2 个迭代周期。但考虑到实验协议当前处于冻结期（禁止改决策链），这些缺口暂时不构成阻塞 — 它们是"下一阶段"的工作，不是"当前阶段的欠债"。

---

## 当前已知的管道断裂（已修/待修）

| 断裂点 | 状态 | 影响 |
|--------|------|------|
| `trading_mode` vs `execution_mode` 字段名 (P0-1) | ⚠️ 冻结区，Round 2 修 | paper evidence 全失效 |
| `key_observations` 无下游消费 (P0-2) | ⚠️ 冻结区，Round 2 修 | LLM 产出被丢弃 |
| `oi_change_1h_pct` 永远 None (P0-3) | ⚠️ 冻结区，Round 2 修 | OI momentum 信号静默失效 |
| PnL 全链路（snapshot vs equity-curve） | ✅ 已修 (本会话) | — |
| OKX proxy 不通 | ✅ 已修 (本会话) | — |
| yfinance 1.4.x 兼容性 | ✅ 已修 (本会话) | — |
| Binance liquidation 401 | ✅ 已修 (本会话) | — |
| Position 无市场感知退出 | ✅ 已实现 (enabled=False) | Round 1 后开启 |
| 同 symbol 无累积加仓上限 | ⚠️ 冻结区，Round 2 修 | SOL 31笔累积到 790 USDT |

---

## e2e 管道测试覆盖现状

| 管道 | 测试数 | 状态 |
|------|--------|------|
| opportunity → ranker → risk → position | 6 | ✅ |
| position → price → SL/TP/max_hold → close | 5 | ✅ |
| close → realized_pnl → equity_curve | 6 | ✅ |
| market signal → exit_rules → tighten/close | 5 | ✅ |
| close → knowledge → ranker feedback | 3 | ✅ |
| **总计** | **25** | **0.75s 全绿** |

---

## 下一步优先级

1. **Round 1 观察期** (现在 → 7/6) — 不动代码，观察数据
2. **Round 2** — 从 EXPERIMENT_BACKLOG.md 选一个改动实施（P5 校准层或 P1 价格结构）
3. **Round 2 同期** — 修 P0-1/P0-2/P0-3 管道断裂 + 加累积加仓 guard
4. **后续** — 运维韧性层（Watchdog、Session 恢复）按需实施
