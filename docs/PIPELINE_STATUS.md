# Pipeline Status — 全自动交易闭环实现现状

> 最后更新：2026-06-08 · 基于代码 grep 逐项核实

## 预期完整流程

```
全市场扫描 → 自动分析 → 自动研究策略 → 自动开仓 → 自动平仓 → 自动评估 → 自动学习 → 自动总结
    ↑                                                                              |
    └──────────────────────── 经验反哺 ─────────────────────────────────────────────┘
```

---

## 逐环节现状

### 1. 全市场扫描 — 80%

| 资产 | 数据源 | 频率 | 实现 |
|---|---|---|---|
| Crypto (BTC/ETH/SOL/XRP/DOGE) | Binance via CCXT + Bybit fallback | 每 5 min | ✅ `scheduler.py:36` |
| xStocks (AAPL/TSLA/NVDA/SPY) | Bitget 直连 | 每 5 min | ✅ `market_data.py` xStocks loop |
| US Equity (SPY/QQQ/NVDA/AAPL) | yFinance | 工作日 14:30-21:00 UTC / 30min | ✅ `stock_observer.py` |
| Prediction Markets | Polymarket Gamma API | 每 30 min | ✅ `prediction_observer.py` |

**缺口**：无跨交易所套利扫描；无 funding rate / open interest 深度数据。

### 2. 自动分析 — 85%

**实现**：`market_observer.py` 每 15 分钟对每个 symbol 独立 LLM 调用。

- 输入：`build_market_context()` 产出多时间框架技术指标（SMA/RSI/ATR/Bollinger/momentum）
- LLM 产出：结构化 `MarketRead`（direction_bias / confidence / confidence_score / key_observations）
- 输出：每 symbol 一行 `Opportunity` 写入 DB
- 经验注入：✅ `retrieve_lessons()` 已接入（advisory block 注入 prompt）

**缺口**：
- 无基本面 / 链上 / 情绪分析维度
- 无多 agent 交叉验证（如 risk_critic 反馈不进入 observer）

### 3. 自动研究策略 — 60%

**实现**：`strategy_designer.py` 每天 01:00 跑一次。

- 输入：tradable symbols + retrieve_lessons + load_playbooks
- LLM 产出：`StrategySpec`（name / timeframe / direction / entry_rule / exit_rule）
- 自动推进：draft → research_note → strategy_hypothesis → paper_candidate → paper_running
- 参数持久化：✅ `parameters` JSON 快照可审计

**缺口**：
- entry/exit rule 是**自然语言描述**，paper_trader **不解析也不执行**这些规则
- 策略不回测——靠 paper trading 累积证据（promotion gate: 14 天 / 30 笔 / 正期望）
- 每天只产一个策略，无优化/变异机制
- 策略之间无比较、无 A/B test

### 4. 自动开仓 — 90%

**实现**：`paper_trader.py` 每 15 分钟消费最新 open opportunity。

- Gate A：`autonomous_paper_trading` 配置开关
- Gate B：方向必须是 long/short（非 neutral）
- Gate C：confidence ≥ `min_confidence_to_trade`（默认 medium）
- 仓位：固定 25 USDT notional（非 LLM 决策）
- 止损：配置 `default_stop_loss_pct` 自动计算 stop_price
- 策略绑定：opportunity 的 `linked_strategy_id` 传递到 proposal

**缺口**：
- 每轮只消费**一个** opportunity（`break` 在第一个匹配处）
- 纯规则引擎，不参考策略定义的 entry 条件
- 不支持加仓 / 分批建仓

### 5. 自动平仓 — 90%

**实现**：`position_manager.py` 每 5 分钟扫描。

- 止损：价格触及 `stop_price`（配置 % 或 proposal 显式设置）
- 止盈：价格触及 `take_profit_price`（配置 % 或 proposal 显式设置）
- 超时：持仓超 `max_position_hold_hours` → 市价平
- 安全：live 持仓**永不自动平**（paper/demo only）

**缺口**：
- 无 trailing stop
- 无动态止盈（如按波动率调整）
- 无部分平仓 / 分批出场

### 6. 自动评估 — 40%

**实现**：`post_trade_reviewer.py` 存在但**未排入 scheduler**。

- 逻辑：按 strategy_id 分组 fills → LLM 写文字复盘 → `create_post_trade_review()`
- 策略晋升门：`strategy_lifecycle.py` 检查 paper_trades / expectancy / drawdown / reviews

**缺口**：
- `post_trade_reviewer` 不自动跑（需手动 `aletheia agent run post_trade_reviewer`）
- `risk_critic` 同样不在 scheduler（从未被定时触发）
- 评估只产出文字，无量化指标表（Sharpe / win rate / avg hold time）
- 无策略间对比排名

### 7. 自动学习 — 35%

**实现**：`knowledge/retrieval.py` FTS5 检索已完成。

- FTS5 索引：`knowledge_fts` 虚拟表（title + body 全文索引）
- 评分：BM25 × recency_decay × importance（quality gate: 只检索 `high_confidence` + `active`）
- 调用点：`market_observer`（L223-231）和 `strategy_designer`（L57-64）在决策前注入 memory_block
- advisory 标记：明确标注"建议，不可覆盖硬限"

**缺口**：
- 只有 2/7 agent 接入检索（paper_trader / risk_critic / post_trade_reviewer / dreaming_agent 均不读）
- playbooks 有 `load_playbooks()` 但**只有 strategy_designer 读**
- 无"学到的规则自动变成硬约束"的路径（也不应有——但量化 signal 应该反馈）
- 无 embedding / 语义检索（目前只有关键词 FTS5，够用但粗糙）

### 8. 自动总结 — 75%

**实现**：`dreaming_agent.py` 每天 02:00 跑。

- 输入：24h agent_runs 活动摘要
- LLM 产出：dreaming_log（发生了什么 / 一个洞察 / 明日建议）
- Learning Ledger：activation_ratio / retire_ratio / review_coverage / lessons_consulted
- Git 快照：`commit_all()` 每晚版本化 knowledge/playbooks/reports
- 产出：dreaming_log + daily_review（去重一天只写一次）

**缺口**：
- 只看 agent_runs 摘要，不读具体 opportunity/trade 数据
- 不主动发起"策略该退休了"或"这个 pattern 反复亏损"的判断
- Learning Ledger 只统计，不依据统计做自动行动

---

## 结构性差距总结

### A. 经验闭环只闭了 ~30%

```
写入（勤）：6 个 agent 持续写 research_note / post_trade_review / dreaming_log / strategy_memory
读回（弱）：只有 market_observer + strategy_designer 读 lessons
执行（无）：读到的 lessons 不改变 paper_trader 的行为（它是纯规则）
```

### B. Agent 之间无协作流

```
当前：各 agent 独立 cron → 单向写 DB → 互不感知
期望：observer → designer → critic → trader 有方向性数据流
缺失：risk_critic 的输出不进入 observer/trader 的输入；observer 的 thesis 不进入 trader 的判断
```

### C. 策略是"文字"不是"可执行规则"

```
当前：strategy_designer 产出 "RSI < 30 and price > SMA50"（自然语言字符串）
实际执行：paper_trader 不读 strategy 的 entry_rule，只看 opportunity 的方向+信心
后果：promotion gate 验证的是"random signal + 固定仓位"的结果，不是策略规则本身的有效性
```

### D. 两个 agent 从未自动运行

```
post_trade_reviewer：代码完整，不在 scheduler
risk_critic：代码完整，不在 scheduler
```

---

## Scheduler 全景（13 个 job）

| Job | 频率 | 功能 | 状态 |
|---|---|---|---|
| market_data | 5 min | 拉快照 | ✅ 运行 |
| market_observer | 15 min | LLM 分析 → Opportunity | ✅ 运行 |
| paper_trader | 15 min | Opportunity → Paper trade | ✅ 运行（需 autonomous=true） |
| position_manager | 5 min | 止损/止盈/超时 | ✅ 运行 |
| exchange_sync | 5 min | 读取交易所账户 | ✅ 运行 |
| portfolio_snapshot | 10 min | PnL 快照 | ✅ 运行 |
| reconciliation | 2 min | live 模式对账 | ✅ 运行（paper 模式空跑） |
| health_check | 10 min | 健康诊断 | ✅ 运行 |
| strategy_designer | 每天 01:00 | LLM 产策略 | ✅ 运行 |
| dreaming_agent | 每天 02:00 | 每日整理 | ✅ 运行 |
| stock_observer | 工作日交易时段 / 30min | US equity 观察 | ✅ 运行 |
| prediction_observer | 30 min | Polymarket 观察 | ✅ 运行 |
| cleanup | 每天 03:17 | 数据修剪 | ✅ 运行 |
| **post_trade_reviewer** | — | 交易复盘 | ❌ **未排入** |
| **risk_critic** | — | 风险教训 | ❌ **未排入** |

---

## 北极星阶段与当前位置

```
[当前] 阶段 0：稳跑验证（7-14 天不崩/不乱单/不丢状态）
        └─ 尚未开始真正的 unattended run

  下一步  阶段 0.5：补全基础设施
        ├─ post_trade_reviewer + risk_critic 排入 scheduler
        ├─ 72h unattended paper trading run
        └─ 验证 Learning Ledger 指标非零

  之后    阶段 1：证明学习质量
        └─ activation_ratio / review_coverage / lessons_consulted 趋势向好

  远期    阶段 2：最小经验闭环（需显式授权）
        ├─ paper_trader 读 lessons（advisory gate）
        ├─ agent 间有方向性工作流
        ├─ risk_critic 输出进入决策
        └─ playbook 全面激活
```
