# ⚠️ SUPERSEDED — 2026-07-02

> This analysis (2026-06-18) proposed 7 incremental optimizations to the strategy
> rules engine. It has been **superseded** by Position B/C (2026-07-02):
> the entire strategy rules engine was deleted and replaced with a
> scanner-family + PI-queue architecture. These 7 recommendations assume the
> engine continues to exist and are **no longer applicable**.
>
> See: `docs/plans/trading-pipeline/strategy_module_position_b_refactor_2026-07-02.md`
> See: `docs/plans/trading-pipeline/strategy_engine_deletion_position_c_2026-07-02.md`

---

# Strategy 生命周期优化分析

> 2026-06-18 · 对比竞品/社区实践 + 可落地建议
> 性质：研究分析，非施工授权。动工前需显式确认。

---

## 一、现状总结

### 生命周期状态机

```
strategy_designer(每天01:00) → LLM产出 ExecutableStrategySpec
    ↓ 编译RuleSet
    ↓ 成功 → 可执行策略(Path A: ruleset_scanner评估OHLCV信号)
    ↓ 失败 → 降级为文字策略(Path B: 只看opportunity方向+信心)
    ↓
draft → research_note → strategy_hypothesis → paper_candidate → paper_running
    (全自动推进，无门控)
    ↓
paper_running (14天+30笔+正期望+回撤<5%+连亏<5+有review)
    ↓ 通过门控
paper_passed → live_candidate → live_limited
    ↓
回测KILL(回撤>8%/胜率<20%/期望<0/PF<0.8) → retired
```

### 关键指标（可观测）

- `performance_ledger.py` 追踪 compile_rate / kill_rate / survival_rate
- `feedback.py` 把 kill_reasons 和 compile_failures 注入下次 LLM prompt
- 回测每天 04:00 跑一轮（所有 paper_running 策略）

### 当前瓶颈

| 问题 | 影响 | 根因 |
|---|---|---|
| 每天只产 1 个策略 | 进化慢，样本少 | strategy_designer 只跑一次 |
| RuleSet 编译率不稳定 | 大量策略降级为 advisory-only | LLM 输出不规范 → _compile_ruleset 失败 |
| advisory 策略无法被回测验证 | 占用 paper 资源但无证据价值 | Path B 不执行策略规则本身 |
| 无策略间竞争/排名 | 好坏策略享受同等资源 | 无 tournament 机制 |
| 晋升门 14 天太久 | 市场条件可能已变 | 保守设计，但可针对性调整 |

---

## 二、竞品/社区对标

### A. Clustr100 社区实践（实战经验沉淀）

| 社区最佳实践 | Aletheia 现状 | 差距 |
|---|---|---|
| **蒸馏交易员推文/策略** → LLM 提炼成规则 | strategy_designer 从零设计 | 缺外部灵感输入 |
| **ICT/FVG 策略给 AI 用很稳** | DSL 支持 indicator 比较但无形态模式 | 可扩展 |
| **多周期框架**(15m入场/1h主结构/4h区间/日线位置) | market_observer 只看单时间框架 context | 策略绑定 timeframe 但 context 窄 |
| **分档止盈 + 移动止损**(TP1:40%仓→止损上移至成本) | 固定 stop_loss_pct / take_profit_pct | DSL 有 TakeProfit 但不支持分批/移动 |
| **选币用成交额排序前50** | Universe Generator 已有 | ✅ 已做 |
| **小金额实盘 > 回测**（回测过拟合是头号杀手） | paper-first + 回测双保险 | ✅ 理念对齐 |
| **核心规则硬编码，不全交AI** | Risk Engine 硬限 + 仓位固定 | ✅ 已做 |

### B. OpenAlice（成熟实现）

| OpenAlice 做法 | Aletheia 现状 | 可借鉴度 |
|---|---|---|
| Guard 责任链（独立文件/可插拔/可配置） | risk/engine.py 巨函数 | 🟡 Phase 2+ 重构方向 |
| Trading-as-Git (staging→commit→push) | proposal→execute 但无 hash 链 | 北极星方向 |
| Decimal-as-string 避免浮点误差 | Python Decimal 可直接用 | 低优 |

### C. Agent Framework 研报共识

| 共识 | Aletheia 对齐度 |
|---|---|
| 确定性执行，LLM 不直接下单 | ✅ 已做且更严 |
| DSL 策略层必须结构化 | 🟡 有 DSL 但编译率是瓶颈 |
| paper-first 验证 | ✅ |
| 经验闭环分层 (写→读→改参数→RL) | 🟡 在 Tier 1→2 过渡 |

### D. Nautilus-Practice（用户已有的回测框架）

- 成熟撮合内核 + 三模式（回测/沙盒/实盘）
- 用 agent **造策略** 而非 **做决策**
- 与 Aletheia 互补：Aletheia 是决策层，Nautilus 是执行验证层

---

## 三、优化建议（按 ROI 排序）

### 1. 提高策略产量：每天 3 个 → 达尔文式竞争

**问题**：每天 1 个策略，进化太慢。
**方案**：strategy_designer 改为每天跑 3 次（01:00 / 09:00 / 17:00），每次产 1 个。覆盖亚/欧/美三时段 regime。

**改动量**：scheduler.py 加 2 个 cron job（复用同一 agent 函数）。
**风险**：LLM 成本 ×3（每次约 $0.01-0.05），可接受。

---

### 2. 编译成功率提升：失败重试 + prompt 强化

**问题**：LLM 输出不规范 → RuleSet 编译失败 → 降级为无用的 advisory 策略。
**方案**：
- 编译失败后，把失败原因 + 具体错误回传 LLM，给 1 次重试机会
- prompt 中明确列出 `_VALID_INDICATORS` 和 `_VALID_COMPARATORS` 作为 enum 约束
- `feedback.get_compile_failures()` 已经有这个能力，只需确保注入够强

**改动量**：strategy_designer.py 加 retry loop（~20 行）。
**预期**：编译率从 ~50-60% → 80%+。

---

### 3. advisory 策略自动淘汰：编译失败 = 不进入 paper_running

**问题**：无 RuleSet 的策略占 paper 资源但不能被回测验证。
**方案**：编译失败的策略停在 `paper_candidate` 而非 `paper_running`；标记 `paper_trading_paused=True, paper_pause_reason="no_ruleset"`。保留 research 价值但不浪费执行资源。

**改动量**：strategy_designer.py `_ACTIVATION_PATH` 逻辑加一个分支（~10 行）。

---

### 4. 策略竞争排名：Elo 式 score 决定资源分配

**问题**：所有 paper_running 策略享受同等机会（Path B 选最优 opportunity 但不选最优策略）。
**方案**：
- 每个策略累计一个 `elo_score`（初始 1000）
- 每笔平仓：赢 +K、亏 -K（K 按对手市场难度调整）
- `paper_trader_ranker` 的 evidence_bonus 加入 elo_score 权重
- 低于 900 分的策略自动 pause → 连续低分自动 retire

**改动量**：strategy_lifecycle.py 或新建 `strategy_ranking.py`（~80 行）。
**哲学**：达尔文式自然选择——让好策略获得更多 paper 执行机会。

---

### 5. 滑点模型升级：从固定到波动率自适应

**问题**：当前回测和 paper 用固定滑点，乐观时段不准。
**方案**（直接搬 Clustr100 社区验证方案）：
```
slippage_bps = base_spread + 0.15 * ATR_pct + signal_adverse_factor
```
- 平静期 3-5 bps，正常 5-10 bps，剧烈波动 15-30 bps
- 比固定 5bps 在关键亏损时段更真实

**改动量**：backtest.py 的 fill 模拟 + paper_trader 的 paper_internal 模式（~30 行）。

---

### 6. 分批止盈机制（社区验证最有效的单一改进）

**问题**：固定止盈 = 要么全吃到要么全吃不到。
**方案**（搬 ICT 三段止盈模型，简化版）：
- TP1：40% 仓位平仓，止损上移至成本价（锁保本）
- TP2：60% 仓位平仓（或全部），更高目标

**改动量**：position_manager.py 的平仓逻辑（~50 行）。DSL `TakeProfit` 扩展 `stages` 字段。
**价值**：群内公认"触 TP1 止损上移成本"是最实用的风控改进。

---

### 7. [可选/远期] 外部策略灵感注入

**问题**：strategy_designer 从零设计，无外部信息来源。
**方案**：加一个 `strategy_seeds/` 文件夹存人工整理的策略灵感（如 ICT/FVG pattern、funding rate 策略模板），strategy_designer 随机抽取一个作为 context 注入。

**改动量**：文件夹 + strategy_designer prompt 加载（~15 行）。
**风险**：低。只是给 LLM 多一个灵感来源，不改变决策权。

---

## 四、不做什么（红线确认）

| 社区/竞品做法 | 为什么不做 |
|---|---|
| 让 AI 生成可执行 Python 代码 | ROADMAP 红线：agent 不产执行代码 |
| LangGraph/DAG 编排引擎 | CLAUDE.md + ROADMAP 红线 |
| Vector DB 替代 FTS5 | 记忆检索当前够用，Phase 7+ |
| 全量 L2 订单簿滑点模型 | K 线级策略不需要（Clustr100 共识） |
| 自动修改 Risk Engine 参数 | Risk Engine 是硬限，change-controlled |
| RL / 强化学习 | 数据不足，增加不透明度 |

---

## 五、建议实施优先级

```
立刻可做（<1天）:
  #2 编译重试 + prompt 强化
  #3 advisory 策略不进 paper_running

本周（2-3天）:
  #1 策略产量 ×3
  #5 波动率自适应滑点

下周（需设计）:
  #4 Elo 排名
  #6 分批止盈

远期（积累数据后）:
  #7 外部策略灵感
```

---

## 六、关键问题：能赚钱吗？

**诚实回答**：当前无法确定。原因：

1. **RuleSet 策略**还没有足够的 paper 交易数据证明期望为正
2. **advisory 策略**（Path B）验证的是 market_observer 的方向判断，不是策略本身
3. 社区经验表明：**AI + 技术指标策略** 在主流币上胜率约 54%（memeki 数据），配合严格止损可盈利

**最小赚钱闭环需要**：
- 编译率 > 80%（让大部分策略可被精确验证）
- 至少 10 个策略并行 paper running（统计显著性）
- 1 个策略通过 14 天门控（证明正期望）
- 然后才有讨论 live 的基础

**上面 7 个优化建议的核心目标就是**：加速走到"至少 1 个策略通过门控"这个里程碑。
