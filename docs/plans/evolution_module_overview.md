# Evolution (EVO) Module — Design Overview

> L3 计划层 — 描述 Strategy / Serenity Lens / Learning 三模块如何协作构成 evolution 系统。
> 本文档是设计概览,非 L2 契约。子系统级契约见:
> - docs/design/promotion_gates_contract.md (Strategy lifecycle — ⚠️ LEGACY Position C)
> - docs/design/learning_contract.md (Learning subsystem — 🟡 新建 2026-07-02)
> - Serenity specs: 	ests/specs/spec_serenity_*.py (9 个) — 统一 L2 契约见 docs/design/serenity_contract.md (🟡 新建 2026-07-02)
> Created: 2026-07-02

---

## 1. 什么是 EVO 模块

EVO 是 Aletheia Trading OS 的"进化"子系统,由三个协作模块组成:

`
   ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
   │  Strategy        │     │  Serenity Lens   │     │  Learning        │
   │  (strategy_       │     │  (serenity_       │     │  (learning/       │
   │   lifecycle.py)   │     │   evidence.py +   │     │   lessons.py +    │
   │                   │     │   serenity_        │     │   skills.py)      │
   │  策略生命周期状态机 │     │   scoring.py)      │     │  经验存储 + 权限   │
   │  晋级门            │     │  证据质量评估      │     │  技能学习          │
   └────────┬──────────┘     └────────┬──────────┘     └────────┬──────────┘
            │                         │                         │
            └─────────────────────────┴─────────────────────────┘
                                      │
                           ┌──────────▼──────────┐
                           │  PI Agent            │
                           │  (决策中枢)           │
                           │  Trading Decision    │
                           └─────────────────────┘
`

## 2. 模块职责

### 2.1 Strategy (letheia/trading/strategy_lifecycle.py)

- **策略生命周期**: draft → ... → live_limited 前向状态机
- **晋级门**: 7 个定量阈值 (14天/30笔/正期望/回撤/连亏/风险/复盘)
- **PI 审批**: paper_running 晋级需 System Operator 推荐
- **状态**: ⚠️ LEGACY (Position C 2026-07-02) — 规则引擎删除后无活晋级路径

### 2.2 Serenity Lens (letheia/trading/serenity_evidence.py + serenity_scoring.py)

- **D2 生成者/评估者分离**: LLM 提案 → 确定性评估器独立判定
- **来源天花板**: 方法论/LLM/预测市场 → speculative 硬顶
- **研究能力分级**: A/B/C 三级,C 级封顶 40 分
- **评分卡**: 证据(28) + 阶段(26) + 均值(12) + 置信(20) - 风险罚分
- **加密-only 符号过滤**: 防止 LLM  hallucination 进入 PI 交易上下文

### 2.3 Learning (letheia/learning/lessons.py + skills.py)

- **LessonStore**: 经验教训 CRUD + 状态机 + 守卫时间戳
- **SkillStore**: 技能学习 CRUD + FORBIDDEN_LAYERS 权限强制
- **消费路径**: Layer3 wisdom → PI prompt (ADVISORY only)
- **状态**: LessonStore 活,SkillStore 预留空壳

## 3. 数据流

`
Market Data → Market Observer → Opportunities
                                      │
                                      ▼
                        Serenity Lens (4-lens research)
                                      │
                                      ▼
                        SerenityOpportunity (assessed_tier)
                                      │
                                      ▼
                        PI Agent Trading Decision
                              ┌───────┴───────┐
                              │               │
                        Strategy           Learning
                        (lifecycle)        (Layer3 wisdom)
                              │               │
                              └───────┬───────┘
                                      │
                                      ▼
                        Risk Engine → Execution Gateway → Bitget
                                      │
                                      ▼
                        Position → Fill → PnL
                                      │
                                      ▼
                        post_trade_reviewer → trade_case_study
                                      │
                                      ↓
                        dreaming_agent → LessonStore (ps_lessons)
`

## 4. 信任不变量

1. **所有订单必须通过 Execution Gateway** — 无 agent→exchange 直连
2. **Learning 不能修改风险/执行** — FORBIDDEN_LAYERS 硬编码
3. **Serenity 不能自评** — D2 独立评估器,生成者 self-grade 仅作 advisory
4. **策略晋级必须人工** — live_limited 需 first_live_confirmation + permission
5. **知识不能硬删除** — delete_item() 永远 raise PermissionError

## 5. 已知限制 (Position C 后果)

- 策略生命周期是 LEGACY — 无活创建/晋级路径
- SkillStore 是空壳 — 无生产者
- DEFAULT_PROMOTION_GATES 硬编码 — 不可配置
- Serenity 评分权重硬编码 — 不可调

## 6. 修复历史 (2026-07-02)

| 修复 | 类型 | 状态 |
|------|------|------|
| PG-B06 失效 spec claim | P0 | ✅ 已修 (REMOVED marker) |
| max_drawdown or 1.0 fallback | P0 | ✅ 已修 (改用 starting_capital_usdt) |
| Learning L2 契约 | P1 | ✅ 已建 (learning_contract.md) |
| DM-S19/S20/S21 data_model spec | P1 | ✅ 已补 (3 claims) |
| LessonStore/SkillStore CRUD 测试 | P1 | ✅ 已补 (17 tests) |
| ps_lessons DDL 统一 | P2 | ✅ 已修 (guard 列移入 CREATE TABLE) |
