# PI Operator 设计决策书

> 2026-06-21 | 基于 codebase 约束 + 竞品分析 + 控制论原则

---

## 问题 1：Operator 与底层 Scheduled Jobs 的关系

### 现状

系统有 **28 个 scheduled job**，分布在 5 个 executor pool（每个 pool 1-2 worker thread）：

| Pool | Workers | 典型 job | 性质 |
|------|---------|----------|------|
| `fast_io` | 1 | price_feed (30s) | 不可中断的基础设施 |
| `io` | 1 | exchange_sync, position_manager, data_collectors | 数据刷新 |
| `observer` | 1 | market_observer (15min) | 信号生成 |
| `agents` | 1 | pi_decision, paper_trader, strategy_designer, post_trade_reviewer, risk_critic | LLM 推理 |
| `default` | 2 | portfolio_snapshot | 计算聚合 |

**关键约束**：
- `agents` pool = 1 worker → 所有 LLM job 串行排队
- SQLite single-writer → 任何 DB 写操作实质串行
- `coalesce=True, max_instances=1` → 跑慢了自动跳过下一次

### 设计决策：Operator 是 Scheduler 的 **Supervisor**，不是 Peer

```
                    ┌─── PI Operator ───┐
                    │  (觉察+决策+指挥)  │
                    └───────┬───────────┘
                            │ 观察产出 / 发出指令
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
    ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
    │ market_obs   │ │ paper_trader │ │ strat_design │ ... (底层 job)
    │ (自主循环)   │ │ (自主循环)   │ │ (自主循环)   │
    └──────────────┘ └──────────────┘ └──────────────┘
```

**原则**：

1. **底层 job 永远自主运行** — Operator 不"启停"它们，不控制它们的执行频率
2. **Operator 消费底层产出** — 读 DB 中的 opportunities、trades、strategies、forward_outcomes
3. **Operator 通过 Config 间接控制行为** — 调整参数（如 `min_confidence_to_trade`），下一轮 job 执行时自然拿到新值
4. **Operator 通过 Agent Delegation 触发一次性任务** — 如"立即触发一次 strategy_designer"或"对 X 策略跑 Optuna"

**类比**：Operator 是工厂经理。生产线（scheduled jobs）24h 自动跑。经理不手动转动每个齿轮，而是：
- 看报表（消费产出）
- 调生产参数（config patch）
- 安排特别任务（delegation）
- 在异常时拉闸（kill_switch — 但不能自己解除）

### 不做的事

- ❌ Operator 不替代 scheduler 调度底层 job
- ❌ Operator 不能停掉某个 scheduled job
- ❌ Operator 不直接调用 `run_backtest()` / `place_order()` 等底层函数
- ❌ Operator 不能绕过 risk engine

---

## 问题 2：什么时候做出实时调整？

### 两种节奏并存

| 层 | 触发方式 | 延迟容忍 | 产出 |
|----|----------|----------|------|
| **Trading Decision** (现有) | 每 15min tick | 秒级不重要 | pass/open/scale_in |
| **Operator Assessment** (新增) | 每 4h + 事件触发 | 分钟级 | config patch / delegation / alert |

### Operator 的触发时机

**定时唤醒（4h cadence）**：
- 08:00 CST — 亚盘开场：审视隔夜表现、策略池状态、data_health
- 12:00 CST — 午间：检查晨间 strategy_designer 产出是否有新策略值得 promote
- 16:00 CST — 美盘前：evaluation of position exposure before US session volatility
- 20:00 CST — 美盘中期：mid-session check + risk tightening if needed
- 00:00 CST — 日切：daily summary review + calibration stats consumption
- 04:00 CST — 深夜：backtest orchestrator 跑完后消费结果 + strategy lifecycle 决策

**事件触发（可选，Stage 2+）**：
- Kill switch 被 trip → Operator 记录分析 + 通知
- Forward outcomes 批量 settle → 重新评估 family multipliers
- Drawdown 超过 50% daily limit → 临时收紧参数

### 为什么不更频繁（如每 15min）？

1. `agents` pool 只有 1 worker；Operator 与 pi_trading_decision 共享此 queue
2. LLM 调用 ≈ 15-30s/次；4h 一轮 = 每天 ~6 次 LLM 调用 ≈ 3min total
3. 系统变化速度：策略表现需要天级数据才有统计显著性；15min 调参是 overfitting
4. 竞品参考：virattt 的 Portfolio Manager 也是按"一批机会成熟后"触发，非实时

### 为什么不更低频（如每天 1 次）？

1. Crypto 24/7 运行，日内 regime shift 常见
2. Kill switch trip 后需要尽快分析原因（不能等到明天）
3. 新策略产出后应及时 review（strategy_designer 每天跑 3 次）

---

## 问题 3：给 Operator 多大的自我调整空间？

### 三级权限模型

```
┌─────────────────────────────────────────────────────────────┐
│ Level 3 (FORBIDDEN — 永不自动执行)                           │
│ • 解除 kill_switch                                          │
│ • 切换 exchange.mode = live                                 │
│ • 修改 live.state                                           │
│ • 调高 max_daily_loss_pct                                   │
│ • 调高 max_futures_leverage_live                            │
│ • 修改 credentials                                          │
│ • 删除 knowledge / 审计日志                                  │
├─────────────────────────────────────────────────────────────┤
│ Level 2 (PROPOSAL — 写入建议队列，等人批准)                   │
│ • 调高 max_live_position_notional_usdt                      │
│ • 调高 max_open_positions_live                              │
│ • 关闭 correlation_guard_enabled                            │
│ • Promote 策略到 live_candidate                             │
│ • 调低 min_confidence_to_trade                              │
│ • 放宽 kill_switch_enabled_demo = false                     │
├─────────────────────────────────────────────────────────────┤
│ Level 1 (AUTO — 立即自动执行 + 审计记录)                     │
│ • 调高 min_confidence_to_trade (收紧门槛)                    │
│ • 调低 max_demo_position_notional_usdt (缩小仓位)            │
│ • 调高 default_stop_loss_pct (收窄止损)                     │
│ • Retire/demote 表现差的策略                                 │
│ • 触发 strategy_designer 生成新策略                          │
│ • 触发 Optuna sweep                                        │
│ • 调整 scanner family multiplier (±0.2 以内)                │
│ • 请求 OHLCV backfill                                      │
│ • 发通知告警                                                │
└─────────────────────────────────────────────────────────────┘
```

### 核心原则："只能收紧，不能放松"

- **自动收紧** = 安全的（减少风险暴露）→ Level 1
- **自动放松** = 危险的（增加风险暴露）→ Level 2/3
- **不可逆操作** = 最危险 → Level 3

### 数值约束（防 overshoot）

Level 1 自动调整有 **per-cycle cap**：
- `min_confidence_to_trade`: 分类阶梯，单次移动 1 步，梯级为 `["speculative", "medium", "high"]`，中性值 `"medium"`
- `max_demo_position_notional_usdt`: 单次调整 ≤ ±30% 当前值
- `default_stop_loss_pct`: 单次调整 ≤ ±1.0 (范围 1.0-5.0)
- `scanner family multiplier`: 单次调整 ≤ ±0.2 (范围 0.6-1.4)
- 每 24h 最多执行 **3 次 Level 1 config patch**（防止 LLM 幻觉连续触发）

### Cooldown 机制

- 同一参数被调整后，**2h 内不再触动**（防振荡）
- 连续 3 次调整同一方向 → 转为 Level 2 proposal（需人确认趋势是否对）

---

## 问题 4：Operator 日常负责什么工作？

### 六大日常职责（按优先级排序）

#### 1. System Health Monitor (持续)
- 消费 `data_health` 状态 → 触发 backfill / notify
- 检查 scheduled job 是否正常产出（stale detection）
- 检查 LLM 调用成功率（error_count in agent_runs）
- **已有基础**：今天实现的 self-heal layer

#### 2. Strategy Lifecycle Management (每 4h)
- 审视所有 `paper_running` 策略的 7d/14d 指标
- 对达标者 promote（到 paper_passed → live_candidate 需 Level 2）
- 对表现差者 demote/retire（Level 1）
- 检查 uncovered symbols → delegate strategy_designer

#### 3. Performance Assessment (每 4h)
- 汇总：日 PnL、win_rate 滑动 7d、per-strategy Sharpe
- 对比 forward_outcomes calibration_stats → 判断当前 regime 是否偏移
- 写入 `knowledge_item type=operator_assessment`

#### 4. Parameter Tuning (每 4h，有 delta 时)
- 当 signal_win_rate < 40% 持续 > 24h → 收紧 min_confidence
- 当 drawdown 接近 daily limit 50% → 缩小 sizing
- 当有 RuleSet 策略且 OHLCV 充足 → 触发 Optuna sweep

#### 5. Universe/Scanner Governance (每日 1-2 次)
- 消费 calibration_stats → 调整 family_multiplier
- 识别 consistently-failing family → 降权或暂停
- 检查 universe size 是否合理（太少 = 机会不足，太多 = 质量稀释）

#### 6. Reporting & Notification (事件驱动)
- Kill switch trip → 立即分析 + 通知
- 策略 retired/promoted → 记录 reasoning
- 日 summary 增强（与 daily_summary job 互补，加入 operator 视角的 commentary）

### 不负责的事

- ❌ 逐笔交易决策（那是 Trading Decision 的活）
- ❌ 实时盯盘 / 手动平仓（position_manager 做）
- ❌ 数据采集（data_collectors / market_observer 做）
- ❌ 订单执行（paper_trader / execution gateway 做）

---

## 问题 5：运行频率

### 最终方案：4h 定时 + 事件 webhook

```python
# scheduler 注册
scheduler.add_job(
    _run_operator_loop,
    trigger="cron",
    hour="0,4,8,12,16,20",  # 每 4h CST
    minute=15,               # 避开整点与其他 job 冲撞
    id="pi_operator_loop",
    executor="agents",       # 与其他 LLM job 串行
    misfire_grace_time=600,  # 10min grace（如果 agents pool 忙）
)
```

### 频率论证

| 频率 | LLM 调用/天 | 优势 | 风险 |
|------|-------------|------|------|
| 1h | 24 | 反应快 | 过拟合；LLM 成本高；agents pool 拥堵 |
| **4h** | **6** | 策略有统计积累时间；成本可控 | 8h 无操作可能 |
| 8h | 3 | 最省资源 | 错过 regime shift |
| 24h | 1 | 最简 | 无法日内响应 |

**4h 是最佳平衡**：
- 6 次/天 × ~20s/次 = ~2min LLM 占用，不影响 15min 的 pi_decision
- 每 4h 有 ~16 个 pi_decision 产出作为输入数据点（足够统计意义）
- 与 post_trade_reviewer (4h) 错开 1h，消费其产出
- 与 backtest_orchestrator (04:00) 对齐，能消费当晚回测结果

### Stage 2+ 事件触发（额外）

```python
# 不注册 scheduler，而是由特定 job 的 post-hook 触发
def on_kill_switch_trip():
    _run_operator_loop(trigger="kill_switch_trip")

def on_outcomes_settled(batch_size: int):
    if batch_size >= 20:  # 只在大批量 settle 时触发
        _run_operator_loop(trigger="outcome_batch")
```

---

## 问题 6：能做什么 / 不能做什么

### ✅ Operator 能做（自动）

| 能力 | 实现方式 | 频率限制 |
|------|----------|----------|
| 写 config.yaml 的 "safe" 字段 | `save_config()` | ≤3 patch/24h |
| Retire/demote 策略 | `update_strategy(status="retired")` | 无限制 |
| 触发 strategy_designer | `run_agent("strategy_designer", ...)` | ≤3 次/天 |
| 触发 Optuna sweep | `run_parameter_sweep(...)` | ≤2 次/天 |
| 调整 family_multiplier | 写 config 或内存 override | ±0.2/次 |
| 写 knowledge_item (assessment/action) | DB insert | 无限制 |
| 发送通知 (Telegram/webhook) | 现有 notify 接口 | ≤10 次/天 |
| 请求 OHLCV backfill | 触发 nightly job 的立即执行 | ≤1 次/天 |

### ⚠️ Operator 能提议（需人批准）

| 提议 | 批准方式 |
|------|----------|
| Promote 策略到 live_candidate | 前端 proposal queue |
| 调高 position sizing | 前端 proposal queue |
| 调低 min_confidence (放松入场) | 前端 proposal queue |
| 新增 symbol 到 universe (非 scanner 自动发现的) | 前端 proposal queue |

### ❌ Operator 绝不能做

| 禁止操作 | 原因 |
|----------|------|
| 解除 kill_switch | 只有人类可以 |
| 切换到 live 模式 | trust boundary |
| 修改 risk 硬限制 (leverage, daily_loss) | 用户资金安全 |
| 直接下单 / 平仓 | execution path = paper_trader 的活 |
| 修改凭证 / secrets | 安全边界 |
| 删除审计日志 / knowledge | 可审计性 |
| 绕过 risk engine check | `FORBIDDEN_INTENTS` 硬编码 |
| 修改自己的权限 | 防提权 |

---

## 总结：设计原则

1. **Supervisor, not Executor** — Operator 看报表、调参数、派任务，不直接干活
2. **Only tighten autonomously** — 自动收紧安全，自动放松需人批
3. **4h heartbeat** — 够快能响应 regime shift，够慢不 overfit
4. **Rate-limited** — 每日 ≤3 config patch + cooldown 防振荡
5. **Auditable** — 每个 action 写 knowledge_item + 审计日志
6. **Cannot escalate** — 无法提升自身权限或绕过 risk engine
7. **Gracefully degradable** — Operator 挂了，底层 job 照跑（因为是 supervisor 不是 controller）

---

## 实施 Roadmap（不变）

- **Stage 1**: 骨架（assessment only, no execution）
- **Stage 2**: Strategy lifecycle + Optuna trigger（Level 1 actions）
- **Stage 3**: Capital allocation + scanner tuning（Level 1 + 2）
- **Stage 4**: Risk adaptation（Level 2, 需授权开放）
