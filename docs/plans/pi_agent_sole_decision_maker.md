# 架构审计报告：PI Agent 唯一决策者重构

> 日期：2026-06-21
> 触发：策略错配 bug 修复 → 发现 paper_trader 双路径完全绕过 PI Agent → 全框架审计

---

## 核心设计原则

**PI Agent 是唯一交易决策者。** 所有其他组件——RuleSet Scanner、Universe Signal Emitter、Paper Trader Ranker、Factor Feedback——必须是**信号生成器**或**预筛选器**，将结果输入 PI Agent 做最终判断。当前架构存在两套并行且互不感知的执行系统。

---

## 当前数据流（问题全景）

```
APScheduler (15min, "agents" executor, pool=1)
     |
     +── paper_trader (t=0)                       ← 独立决策，不看 PI Agent
     |     |
     |     +── Path A: scan_active_rulesets()
     |     |     → 信号命中 → _handle_ruleset_entry() → create_paper_proposal() ← 绕过 LLM
     |     |     → 退出信号 → _handle_ruleset_exit() → close_position()         ← 绕过 LLM
     |     |
     |     +── Path B: build_paper_candidates() → select_best_candidate()
     |           → create_paper_proposal()                                       ← 绕过 LLM
     |
     +── pi_trading_decision (t=+7min)            ← 真正的 LLM 决策路径
           |
           +── build_enriched_trading_context() → scan_active_rulesets()
           +── LLM evaluate → Bull/Bear debate → conviction scoring
           +── submit_decision_as_proposal() → Risk Engine → order
           |
           ⚠️ 但 PI Agent 不看 opportunities 表、不知道 paper_trader 在做什么

position_manager (5min, "io" executor)
     +── evaluate_open_positions()
           → stop_loss / take_profit / max_hold / trailing_stop → close_position()
           ⚠️ 不通知 PI Agent（下次轮询才发现）
```

**关键断裂：**
1. `paper_trader` 和 `pi_trading_decision` 完全独立——共享 SQLite 但无直接通信
2. PI Agent 不读 `opportunities` 表（看不到 market_observer 产出）
3. PI Agent 的 conviction/decision 不写入任何其他组件可读的表
4. Position manager 自主平仓，PI Agent 轮询间隔内完全不知
5. `scale_in` / `flip` 决策在 `submit_decision_as_proposal()` 被静默丢弃

---

## 违反原则的完整清单

### CRITICAL — 直接下单/平仓，无 LLM 评估

| # | 位置 | 行为 | 影响 |
|---|------|------|------|
| 1 | `paper_trader.py:251` `_handle_ruleset_entry()` | RuleSet 入场信号 → 直接 `create_paper_proposal()` | 开仓无 LLM |
| 2 | `paper_trader.py:209` `_handle_ruleset_exit()` | RuleSet 退出信号 → 直接 `close_position()` | 平仓无 LLM |
| 3 | `paper_trader.py:179` Path B | 最高排名候选 → 直接 `create_paper_proposal()` | 开仓无 LLM |
| 4 | `position_manager.py:265-283` | 止损/止盈/最大持仓时间/RuleSet 退出 → `close_position()` | 平仓无 LLM |
| 5 | `scheduler.py:924` `_run_tpsl_fill_check()` | 交易所 TPSL 触发 → `close_position()` | 平仓无 LLM |

### HIGH — 策略生命周期变更 / 持仓管理无 LLM

| # | 位置 | 行为 | 影响 |
|---|------|------|------|
| 6 | `operator_loop.py:303-321` | expectancy 阈值 → 自动 retire/demote 策略 | 改变信号池 |
| 7 | `strategy_designer.py:606-621` | 设计后自动 promote 到 paper_running | 新策略直接进入执行 |
| 8 | `position_reconciler.py:466-473` | 交易所仓位 → auto adopt → 纳入自动管理 | 外部仓位被接管 |
| 9 | `position_reconciler.py:476-479` | 孤儿仓位 → 自动标记关闭 | 实现 PnL 无审核 |

### MEDIUM — 静默过滤/权重影响，PI Agent 完全不可见

| # | 位置 | 行为 | 影响 |
|---|------|------|------|
| 10 | `operator_loop.py:393-471` | 自动改 config（confidence/sizing/stop） | 改 PI Agent 运行参数 |
| 11 | `trading_decision.py:163` | `scale_in`/`flip` 决策被静默丢弃 | LLM 决策未执行 |
| 12 | `paper_trader_ranker.py:266-273` | `blocked_candidate` → 静默 skip | AI 永远看不到这些机会 |
| 13 | `signal_emitter.py:215-296` | 82 行硬编码方向推断 | 替代 AI 做方向判断 |
| 14 | `paper_trader_ranker.py:325-401` | 硬编码评分权重决定排名 | 替代 AI 做优先级 |
| 15 | `factor_feedback.py:179-196` | win_rate<40% → blocked | 统计阈值替代 AI 判断 |
| 16 | `signal_emitter.py:27-37` | `_MIN_SCORE=45` 过滤机会 | AI 看不到低分机会 |
| 17 | `paper_trader_ranker.py:280-288` | Guard blocked → 静默 skip | Guard 替代 AI 做决策 |

### 应保持自治的组件 (CORRECT as-is)

| 组件 | 理由 |
|------|------|
| `position_manager` 止损/止盈执行 | 执行层安全机制，价格移动时不能等 LLM |
| `position_manager` trailing stop 调整 | 纯机械式保护，非交易决策 |
| `position_reconciler` 交易所对账同步 | 数据一致性，非决策 |
| `tpsl_fill_check` 交易所成交同步 | 事后记录，非决策 |
| `strategy_lifecycle` promotion gates | 明确量化安全门槛，需人工确认 |
| Guard engine 本身 | 数据驱动谓词引擎，架构正确 |

---

## 目标架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    PI Agent (唯一决策者)                          │
│  输入：enriched context + signals + guards + factors + opps     │
│  输出：trade / skip / watch / scale_in / flip                   │
│  ↓ (conviction ≥ threshold)                                     │
│  submit_decision_as_proposal() → Risk Engine → Execution        │
└─────────────────────────────────────────────────────────────────┘
         ▲ 信号输入                              ▲ 信号输入
         │                                       │
┌────────┴────────┐                    ┌─────────┴────────┐
│ Signal Layer    │                    │ Context Layer     │
│                 │                    │                   │
│ • RuleSet Scan  │                    │ • Trading Context │
│ • Signal Emitter│                    │ • Guard Advisory  │
│ • Market Obs    │                    │ • Factor Stats    │
│ • Ranker Score  │                    │ • Strategy Evid.  │
│ • Serenity Opp  │                    │ • Lessons/Memory  │
└─────────────────┘                    └───────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│           Execution Layer (自治，非决策)                          │
│ • Stop Loss / Take Profit (price-triggered safety)              │
│ • Trailing Stop (mechanical protection)                         │
│ • Exchange Reconciliation (sync, not decide)                    │
│ • TPSL Fill Check (record, not decide)                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 实施计划 (分阶段)

### Phase 1: 修复 PI Agent 信息盲区 (LOW RISK)

**目标：PI Agent 能看到所有信号——不改执行路径，只加输入。**

| 改动 | 文件 | 说明 |
|------|------|------|
| 1a | `trading_context.py` | Layer 1 追加 `opportunities` — 读 `list_opportunities(status="open")` top 10 |
| 1b | `trading_context.py` | Layer 1 追加 `factor_feedback` — 所有 scanner_family 的分类状态 |
| 1c | `trading_context.py` | Layer 2 追加 `ranker_candidates` — 上一轮 ranker 的 top 5 候选+分数 |
| 1d | `trading_decision.py` | Prompt 追加 opportunities/factor/ranker section，让 LLM 可见 |
| 1e | `trading_decision.py` | 修复 `submit_decision_as_proposal()` — 支持 `scale_in`/`flip` |

**验证：** PI Agent 日志中能看到 opportunities、factor status、ranker candidates。

### Phase 2: PI Agent 决策持久化 (LOW RISK)

**目标：PI Agent 的决策写入 DB 表，其他组件可查询。**

| 改动 | 文件 | 说明 |
|------|------|------|
| 2a | `models.py` | 新增 `PiDecision` 模型: symbol, direction, action, conviction, reasoning, ts |
| 2b | `repositories.py` | `insert_pi_decision()`, `latest_pi_decision(symbol)` |
| 2c | `trading_decision.py` | `record_decision()` 同时写 DB（当前只写 json 文件） |
| 2d | `migrations.py` | 新表 `pi_decisions` additive migration |

**验证：** `SELECT * FROM pi_decisions ORDER BY created_at DESC LIMIT 5` 有数据。

### Phase 3: Paper Trader 尊重 PI Agent (MEDIUM RISK)

**目标：paper_trader 在执行前查询 PI Agent 最近决策，作为 veto/confirmation。**

| 改动 | 文件 | 说明 |
|------|------|------|
| 3a | `paper_trader.py` Path B | 在 `create_paper_proposal()` 前查 `latest_pi_decision(symbol)`: 如果 PI 最近 30min 内对该 symbol 说了 `skip`/`watch`，降级为仅记录，不提交 |
| 3b | `paper_trader.py` Path A entry | RuleSet entry 信号不再直接执行，改为写入 `pi_decisions` 表作为 `signal_pending`，等待下次 PI Agent 轮询消费 |
| 3c | `paper_trader.py` Path A exit | RuleSet exit 信号继续自治执行（退出是安全操作） |
| 3d | `paper_trader_ranker.py` | `blocked_candidate` / guard blocked 不再静默 skip，改为 `penalized_candidate`（-50 分），仍进入候选池供 PI Agent 可见 |

**验证：** paper_trader 日志出现 "deferred to PI Agent" / "PI veto respected"。

### Phase 4: 统一决策循环 (HIGH RISK — 最终目标)

**目标：废除 paper_trader 独立执行路径，全部通过 PI Agent。**

| 改动 | 文件 | 说明 |
|------|------|------|
| 4a | `paper_trader.py` | 重构为纯信号收集器：scan RuleSet + build candidates → 写入 `pending_signals` 表 |
| 4b | `commands.py` | `evaluate_trading_opportunity()` 同时读 `pending_signals` 作为输入 |
| 4c | `scheduler.py` | `paper_trader` 运行频率调高到 5min（纯收集），`pi_trading_decision` 保持 15min |
| 4d | `operator_loop.py` | retire/demote 改为 `recommend_action` → 写入队列 → PI Agent 下次轮询确认 |
| 4e | `strategy_designer.py` | 自动 promote 停在 `paper_candidate`，promote 到 `paper_running` 需 PI Agent 确认 |
| 4f | 移除 | `paper_trader.py` 的 `create_paper_proposal()` 直接调用全部移除 |

**验证：** 全系统只有 `pi_trading_decision` 路径能产出新 proposal。

---

## 应保持不变的组件

| 组件 | 理由 |
|------|------|
| `position_manager` 止损/止盈/trailing | 执行层安全——价格快速移动时 LLM 延迟不可接受 |
| `position_reconciler` 对账同步 | 数据一致性维护，非交易决策 |
| Risk Engine (`evaluate_proposal`) | 最后一道硬件级安全门，即使 PI Agent 犯错也能拦截 |
| Guard engine 谓词计算 | 架构正确——数据驱动；只需改消费端从 "block" 变 "advisory" |
| `strategy_lifecycle` promotion gates | 明确的量化安全门槛，promotion 到 live 必须人工确认 |

---

## 预期收益

1. **一致性**：所有交易决策经过同一个 LLM 评估——可追溯、可解释、可 review
2. **修复策略错配类 bug**：当 PI Agent 是唯一决策者时，不存在 "哪个策略绑哪个机会" 的问题——PI Agent 自己选择
3. **市场适应性**：LLM 能根据上下文调整——硬编码阈值在变化的市场中固化（RSI 30/70 在趋势市失效）
4. **反馈闭环**：PI Agent 的每个决策都有 conviction score + reasoning → post_trade_reviewer 能学习
5. **消除静默过滤**：当前 `blocked_candidate` / `factor_blocked` / `guard_blocked` 让系统无法学习为什么某些机会值得尝试

---

## 风险与对策

| 风险 | 对策 |
|------|------|
| LLM 延迟导致错过入场 | 15min 周期已足够宽裕；RuleSet 信号可标记 urgency 让 PI Agent 加权 |
| LLM 错误导致频繁交易 | Risk Engine 保持不变——单日亏损/仓位限制/频率限制仍然生效 |
| PI Agent 上下文过载 | 分层注入：Layer 1 必需（positions/features），Layer 2 按需（signals/candidates），Layer 3 辅助（lessons/guards） |
| 回归风险——Phase 3/4 改动大 | Phase 1-2 先上线观察；Phase 3 用 feature flag 逐步切换 |
| 当前 paper_trader 已有正收益策略 | Phase 3 的 PI veto 是 opt-in——只在 PI 明确说 skip 时才拦截 |

---

## 实施优先级

```
Phase 1 (信息盲区) ← 可立即实施，纯加法，无破坏性
    ↓ 观察 1-2 周
Phase 2 (决策持久化) ← 加 DB 表，纯加法
    ↓ 验证 PI Agent 产出合理
Phase 3 (Paper Trader 尊重 PI) ← 渐进式，feature flag 控制
    ↓ 验证无回归
Phase 4 (统一决策循环) ← 终极目标，最高风险
```

**总耗时估算：** Phase 1-2 可在 1-2 个 session 内完成；Phase 3 需 1 session + 观察期；Phase 4 视 Phase 3 效果决定时机。

---

## 附：即时修复（已完成）

策略错配 bug 修复（本次会话）：
- `market_observer.py`: symbol→strategy 映射替代盲取第一条
- `paper_trader_ranker.py`: 验证 linked strategy 实际 target 该 symbol

这是 Phase 3 的前置修复——确保在 paper_trader 仍独立执行期间至少不会用错策略。
