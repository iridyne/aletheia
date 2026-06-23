# AI 相机交易方向验证协议

> **性质**: 硬约束。所有 AI Agent 和开发者必须遵守。违反 = 实验数据作废。
> **Created**: 2026-06-21
> **协议状态**: ACTIVE — Round 1 进行中

---

## 核心赌注

> LLM 从训练数据中已经"知道"某些市场 pattern,无需学习,只需提取。
> 本协议验证:这些已有知识在真实市场是否产生正期望。

---

## 协议结构:3 轮 × 14 天

| 轮次 | 起止日期 | 系统版本 | 状态 |
|------|---------|---------|------|
| Round 1 | 2026-06-22 → 2026-07-06 | v5 (6/21 最佳版本) | **进行中** |
| Round 2 | (仅当 R1 未 Go 时启动) | v6 (一个高杠杆改动) | 待定 |
| Round 3 | (仅当 R2 未 Go 时启动) | v7 (最后一次机会) | 待定 |

**每轮之间**: 允许做**恰好一个**针对性改动(不是 10 个)。改动必须是信息管道/prompt/context 级别,不是 plumbing。改动候选排序见 [EXPERIMENT_BACKLOG.md](EXPERIMENT_BACKLOG.md)。

---

## 冻结区(14 天观察期内禁止修改)

以下路径在观察期内 **禁止任何修改**:

```
aletheia/agent_shell/trading_context.py
aletheia/agent_shell/trading_decision.py
aletheia/agents/market_observer.py
aletheia/agents/paper_trader.py
aletheia/agents/paper_trader_ranker.py
aletheia/agents/pi_agent*.py
aletheia/strategy/
aletheia/learning/
aletheia/config/prompts/         (如存在)
```

**冻结的本质**: 任何会改变 "AI 看到什么信息" 或 "AI 怎么做决策" 的代码。

### 允许修改的范围

- UI / 前端 / 可视化
- 监控 / 通知 / 日志
- 文档
- 风控参数微调(仓位大小,不影响方向判断)
- Bug fix — **仅限会导致系统崩溃/挂起的 bug**,不包括"我觉得逻辑可以更好"
- 基础设施(数据库、调度器、网络层)— 前提是不改变 AI 输入数据的内容/格式

### 灰色地带裁决

不确定某个改动是否违反冻结?问自己:**"这个改动会不会让同一个市场状况下,AI 做出不同的交易决策?"** 如果答案是"会"或"可能",则禁止。

---

## 判决标准

### Go(方向验证 — 进入小仓实盘准备)

单轮内**全部满足**:

| # | 条件 | 阈值 |
|---|------|------|
| G1 | Profit Factor(全品种) | ≥ 1.2 |
| G2 | **或** 最优子集 PF(≥30 笔) | ≥ 1.5 |
| G3 | 期望值(WR × avg_win - (1-WR) × avg_loss) | > 0 |
| G4 | ECE(信心校准) | < 0.15 |
| G5 | 最大回撤(模拟资金) | < 15% |
| G6 | 样本量 | ≥ 60 closed trades |

**G1 或 G2 满足其一即可** — 允许窄域 edge(只在某币种/方向赚钱也算)。

### Kill(单轮 Kill)

单轮内满足**任一条**:

| # | 条件 | 阈值 |
|---|------|------|
| K1 | 全品种 PF | < 0.85 |
| K2 | 最优子集 PF(最赚钱的方向) | < 1.0 |
| K3 | ECE | > 0.25 |
| K4 | 最大回撤 | > 25% |

### Grey(不判决)

不触发 Go 也不触发 Kill = 灰区。进入下一轮,允许做一个改动。

---

## 终极 Kill — 方向证伪

> **如果连续 3 轮都未达到 Go(无论是 Kill 还是 Grey),整个"AI 相机交易"方向判定为证伪。**

证伪后:
- 停止在此方向上的所有开发投入
- 不再"优化一下再试"
- 转向备选方向(传统因子量化、套利、做市、或退出交易赛道)

**这是预承诺。写在数据来之前。不允许事后修改阈值。**

---

## 轮间改动规则

Round N 结束、未达 Go 时,允许做**恰好一个**改动进入 Round N+1:

**合法改动(信息管道/prompt 级)**:
- 给 AI 新的数据维度(如加入 funding rate、orderbook depth)
- 改 prompt / system message / context 框架
- 改信号过滤逻辑(如只允许高 conviction 信号触发)
- 收窄部署范围(只做被证明有信号的币种/方向)

**非法改动(plumbing 级,不重置计数器)**:
- 修 UI、通知、日志
- 修网络/数据库/调度器 bug
- 加监控

**判断标准同冻结区**: "这个改动会不会让同一个市场状况下 AI 做出不同决策?" → 是 = 计为实质改动,消耗本轮唯一改动配额。

---

## 数据采集要求

- 执行模式: **bitget_demo**(真实撮合,非 paper_internal)
- 记录: 每笔 trade 必须关联 pi_decisions(含 conviction score)
- ECE: 每轮结束时跑一次 confidence_calibration
- Scorecard: 每轮结束时跑一次 go_live_scorecard
- 基线 tag: 每轮开始时打 git tag `experiment/round-N-start`

---

## 心理契约

这份协议存在的意义:

1. **消灭"再优化一下"的循环** — 改动次数有硬上限
2. **消灭"数据还不够"的借口** — 14 天 × 当前节奏 ≈ 60-80 笔,够了
3. **消灭"regime 不对"的逃生口** — 3 轮跨 ~6 周,足以覆盖多种行情
4. **给焦虑一个终点** — 最多 2026-08-03,答案必然到来

**如果你发现自己想修改这份协议的阈值或规则 — 停下来。那是恐惧在说话,不是理性。**

---

## 基线数据(Round 1 前,调试期产物,仅供参考)

> 以下 62 笔来自 108 次 commit 的混合版本(6/9-6/21),不可用作方向判决,仅作对比基线。

```
期间: 2026-06-09 → 2026-06-21 (10 天,系统高频变动中)
Closed trades: 62
Win rate: 40.3% (25/62)
Total PnL: -6.58 USDT
Profit Factor: 0.803
Gross profit: 26.83 USDT | Gross loss: 33.41 USDT
Avg win: +1.07 USDT | Avg loss: -0.90 USDT
Best win: +1.57 | Worst loss: -7.51
Expectancy: -0.107 USDT/trade
Max single loss: -7.51 (AVAX/USDT short 或 SOL)

By symbol+direction:
  ETH/USDT short:  39 trades, 21 wins, +21.93  ← 唯一盈利方向
  BTC/USDT long:    2 trades,  1 win,  +0.06
  BTC/USDT short:   2 trades,  0 wins, -0.64
  ETH/USDT long:    7 trades,  0 wins, -2.73
  AVAX/USDT short:  2 trades,  1 win,  -5.00
  SOL/USDT short:   3 trades,  0 wins, -9.29
  SOL/USDT long:    7 trades,  2 wins, -10.91

Execution: 61 bitget_demo + 1 paper_internal
ECE: 不可计算 — 62笔全部来自旧管道(opportunity→paper_trader→proposal),
     不经过 trading_decision.py 的 conviction 评估。
     无一笔记录数字 conviction score,只有文字 conf=high/medium。
     pi_decisions 表的 signal_pending 行 conviction=0(ranker写入,非评估结果)。
     Round 1 将是新决策引擎(PI Agent structured decision + debate)的首次真实数据。
```

---

## 版本记录

| 日期 | 变更 |
|------|------|
| 2026-06-22 | 初版。协议生效,Round 1 开始。基线数据记录 |
