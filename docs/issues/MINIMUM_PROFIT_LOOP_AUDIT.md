# 最小赚钱循环（Minimum Profit Loop）审计报告

> 审计日期：2026-06-19
> 范围：master 分支全量代码，覆盖数据采集 → 信号生成 → 执行交易 → 复盘闭环四个阶段
> 目标：找出 (a) 数据/操作流断裂 (b) 数据未被完全利用 (c) 数据黑洞

---

## 严重程度图例

| 级别 | 含义 |
|------|------|
| **P0** | 核心循环断裂——直接阻止系统从数据中学习或盈利 |
| **P1** | 重要数据未被利用——系统功能受损但不致命 |
| **P2** | 数据黑洞——资源浪费，不直接影响交易决策 |

---

## P0 — 核心循环断裂（必须修复）

### 1. `trading_mode` 幽灵字段 — paper 历史数据从未参与排名

**位置**：`aletheia/agents/paper_trader_ranker.py:547-552`

```python
"FROM positions WHERE strategy_id = ? AND status = 'closed' "
"AND trading_mode = 'paper'"
```

**问题**：`Position` 模型实际字段名为 `execution_mode`，`trading_mode` 列不存在。SQLite 不报错，返回空结果。

**影响**：
- `paper_summary` 永远为 None，strategy evidence 层完全失效
- `paper_confirming` / `paper_underperforming` / `backtest_false_positive` 三种判定不可能触发
- guard 评估中 `paper_expectancy` 永远 None，基于 paper 表现的 guard 条件永不触发
- **Tier 1 自进化的反馈链断裂**：即使 guard_candidate 生成了，ranker 也看不到 paper 证据

**修复**：`trading_mode = 'paper'` 改为 `execution_mode = 'paper_internal'`（或 `LIKE 'paper%'`）

---

### 2. `key_observations` 结构性丧失 — LLM 产出无法被机器消费

**位置**：`aletheia/agents/market_observer.py:269-272`

```python
thesis_parts.append("Observations: " + " | ".join(observations))
```

**问题**：LLM 产出的结构化 `key_observations: list[str]` 被拼接为纯文本字符串后存入 `thesis`，`Opportunity` 模型无此字段，storage 层零命中。

**影响**：
- 所有下游（ranker、guards、shadow trades）无法按观测类型做统计/决策
- Tier 3 自进化（从 observations 提炼 scanner rule）的数据源不存在
- 命中率分析（哪些 observation pattern 与 win 关联）永久无法实现

**修复**：`Opportunity` 增加 `key_observations` JSON 字段，保留结构化列表

---

### 3. `oi_change_1h_pct` 永远 None — OI momentum 评分路径静默跳过

**位置**：`aletheia/data/collectors/derivatives.py:140-160`

**问题**：`_parse_oi()` 从未计算 OI 变化百分比（需要前后两个快照对比），字段永远写入 None。

**影响**：
- `features.py:207` 读出 None，`scanners.py:281,365` 的 `oi_delta_1h is None` 降级判断触发
- OI momentum 评分路径（derivatives scanner 核心信号之一）静默失效
- Universe 排名质量因此受损，系统对 OI 异动完全无感

**修复**：在 `_parse_oi()` 中查询前一条记录计算百分比变化

---

## P1 — 重要数据未被利用

### 4. guard_context 不传给 market_observer — LLM 与 guard 冲突

**位置**：`aletheia/agents/market_observer.py:362-368` + `aletheia/knowledge/router.py:52-55`

**问题**：`market_observer` 调用 `build_advisory_context` 时从不传 `guard_context`，导致 `_build_guard_advisory()` 永不执行。

**影响**：LLM 可能产出与已有 guard 冲突的建议，opportunity 生成后被 ranker 拦截，无效 token 消费且无日志说明冲突原因。

---

### 5. Family Scorecard 纯展示、不反馈 scanner 权重

**位置**：`aletheia/analytics/family_scorecard.py` + `server/routes/data.py:1754-1759`

**问题**：Scorecard 基于真实已平仓 PnL 计算 win_rate，但 allocator 只用 `calibration_stats()`（forward outcome 价格比较）。两套口径并行但互不共享。

**影响**：真实交易盈亏从未影响策略权重分配；calibration 可能与真实 PnL 矛盾时，系统选择了次优信号源。

---

### 6. `liquidation_events` 完整孤岛 — 采集即丢弃

**位置**：`aletheia/data/collectors/derivatives.py` + `repositories.py:1382`

**问题**：每 2 分钟采集清算数据入库，全库无任何读取此表的业务代码。7 天后被 cleanup 清除。

**影响**：极端清算事件对市场分析有高价值但完全浪费。

---

### 7. Shadow Trades 有入场无出场 — 反事实分析无法完成

**位置**：`aletheia/analytics/shadow_trades.py:50-65`

**问题**：`ShadowTrade` 只有 `entry_price`，无 exit 字段，无机制在平仓时回写。

**影响**：Edge Lab 反事实分析设计目的无法实现。

---

### 8. Data Collectors symbol 集合错位（limit=20 硬编码）

**位置**：`aletheia/agent_runtime/scheduler.py:1154`

**问题**：`_get_observed_symbols()` 硬编码 `limit=20`，而 universe 可能选出更多 candidates。

**影响**：中尾部 universe member 的评分数据缺失。

---

### 9. `failed` forward outcome 无告警

**位置**：`aletheia/universe/outcome_recorder.py:99-100`

**问题**：价格取不到时标 `failed` 后无报警。代币下架、映射失败等静默缩减 calibration 样本。

**影响**：`calibration_stats()` 可能高估某些 family 准确率。

---

## P2 — 数据黑洞（资源浪费）

### 10. `dreaming_log` / `daily_review` / `strategy_memory` — KB 三类型零消费

**位置**：`aletheia/knowledge/manager.py:90-183`

三种 knowledge_items 均为 `medium_confidence`，不进 FTS 索引，无代码读取。LLM 产出纯浪费。

### 11. `_latest_volume_ranking` 进程内存无消费

**位置**：`scheduler.py:79` + `:1133`

Phase B 每 10 分钟更新 volume ranking，`get_latest_volume_ranking()` 全库零调用。

### 12. `universe_feature_snapshots` 只写不读

**位置**：`aletheia/universe/features.py:295`

每次 universe 刷新都写历史快照，无查询函数读取。

### 13. Post-Trade Reviewer 无入场/出场市场上下文

**位置**：`aletheia/agents/post_trade_reviewer.py:53-58`

传给 LLM 的只有 fills/orders 数量，无 price/ATR/regime 数据，review 质量上限受限。

### 14. `taker_buy_ratio` 在 Bitget 环境下永远 None

**位置**：`aletheia/universe/features.py:229`

调用 Binance 专属 API，Bitget 下静默返回 None。

### 15. 未知 scanner family 静默丢弃

**位置**：`aletheia/universe/signal_emitter.py:73-80`

`_infer_direction` 只处理 4 个已知 family，新增 family 静默消失无 warning。

---

## 修复优先级排序

| 优先级 | 编号 | 一句话描述 | 预估工作量 |
|--------|------|------------|-----------|
| **P0-1** | #1 | `trading_mode` 改 `execution_mode` | 5 分钟 |
| **P0-2** | #2 | Opportunity 增加 key_observations 字段 | 1 小时 |
| **P0-3** | #3 | OI 变化百分比计算 | 30 分钟 |
| **P1-1** | #4 | guard_context 传入 market_observer | 15 分钟 |
| **P1-2** | #5 | scorecard 反馈 allocator 权重 | 2 小时（设计决策） |
| **P1-3** | #6 | liquidation_events 接入 context_builder | 1 小时 |
| **P1-4** | #7 | shadow trades 出场回写 | 1.5 小时 |
| **P1-5** | #8 | limit=20 改动态配置 | 10 分钟 |
| **P1-6** | #9 | failed outcome 加 warning 日志 | 10 分钟 |
| **P2** | #10-15 | 黑洞清理 / 接入 / 删除 | 各 15-60 分钟 |

---

## 循环完整性评估

```
数据采集 ──[4个黑洞]──→ 信号生成 ──[obs结构丢失]──→ 执行交易 ──[paper证据断]──→ 复盘闭环
    |                      |                          |                    |
    | OI=None             | guard不传                 | shadow无exit       | reviewer无context
    | liq无消费           | family静默丢              | mode字段错         | 3种KB类型黑洞
    | volume无消费        |                          |                    | scorecard不反馈
```

**结论**：循环骨架完整（数据能流完一圈），但 3 个 P0 问题导致核心反馈链实质断裂：
1. paper 历史数据无法参与决策（#1）
2. LLM 观测结果无法被机器消费（#2）
3. OI 信号完全失效（#3）

修复 #1 + #3 后系统的"从数据中学习"能力会显著恢复；修复 #2 是 Tier 3 自进化的前置依赖。
