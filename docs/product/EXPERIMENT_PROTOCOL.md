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
| 2026-06-23 | **冻结例外 #1(用户授权)** — 见下方记录。Round 1 起点保留 6/22,标注本修复点 |

---

## 冻结例外记录

> 冻结区改动需用户显式授权 + 留痕。此处记录每次例外的根因、范围、判决影响。

### 例外 #1 — 2026-06-23:最小赚钱循环 context-injection bug 修复

**触发**:Round 1 期间发现"最小赚钱循环"断流——20h 零开仓。805 opportunities
持续生成但 PI Agent 100% skip。

**根因(逐行核实)**:PI Agent(LLM)被喂了一批**错误事实**,构建出"账户濒临破产"
的幻觉危机:
1. 余额读错:`get_cached_state()` 默认读 bitget 闲置缓存 $167,真实 okx_demo 是 $88k
   (此项在 `exchange_sync.py` 修,**非冻结**,先行修复)
2. 7d 统计被 `units_bug_cleanup` 一次性强平污染(-13.46/58笔 vs 真实 +1.94/50笔)
3. `cfg.risk.kill_switch_active` AttributeError → 整个 risk block 为 {},LLM 看不到限额
4. 策略过滤用 `status` 但 DB 字段是 `state` → 4 条活跃策略对 LLM 不可见
5. `win_rate 46.6%` 裸百分比被 LLM 误读为 "-45.8% drawdown"
6. `long_bias/short_bias` 标签读起来像方向否决而非概率权重
7. signal `win_rate` 与 7d trade `win_rate` 同名异义,可叠加成双重危机读法
8. debate 公式 `50+(bull-bear)` 丢弃 initial conviction,系统性拉回 50(<阈值55)

**授权范围**(用户 2026-06-23 授权全部修复,含 debate 公式):
- `agent_shell/trading_context.py`:kill_switch 属性路径 / state typo / cleanup 过滤(3 处)
- `agent_shell/trading_decision.py`:win_rate W/L 计数 / PnL 改名 / signal 改名 /
  bias→weight 标签 / debate 公式锚定 initial(5 处)
- `agent_shell/trading_decision.py`:ohlcv 过度保守阈值(非冻结渲染层,1 处)

**判决依据**:命中协议"Bug fix — 会导致系统挂起的 bug"例外(20h 零开仓=循环挂起),
且全部为"纠正错误事实",非"逻辑可以更好"。修复后 AI 基于**准确事实**自主决策。

**未纳入**:bear prompt 量纲(TD-4)——判断改 prompt 语气风险大于收益,debate
锚定公式已解决核心问题。

**判决影响(用户决定)**:Round 1 起点**保留 6/22**,不重置。判决时区分修复前/后数据
——6/22→6/23 的 skip/proposal 数据是在错误事实下产生,不计入策略表现评估;
6/23 修复生效后的数据才是 Round 1 的有效样本。

**生效验证**:修复后 PI 决策(@13:55)reasoning 引用 "23W/27L, +$1.94 over 50 trades"
(干净数据),不再出现 $167/破产/恢复模式。幻觉危机消除,LLM 转为基于真实 46% 胜率
的诚实谨慎(仍 skip,但理由真实可学习)。全量 1646 测试通过。

**监测固化**:doctor `loop.profit` 探针 + `spec_pipeline_profit_loop.py`(PL-PROFIT-01..04)
防止此类静默断流再次无声发生。

---

### 例外 #2 — 2026-06-24:PT-1 signal_pending 去重(冻结区 paper_trader.py)

**触发**:例外 #1 修复后 PI 短暂恢复下单,但很快又 100% skip。新理由:LLM 看到
`signal_pending` 里同一 (symbol,direction) 重复出现(ETH short 4 次),判定
"pipeline data integrity compromised / 数据损坏",拒绝决策。

**根因(逐行核实)**:`agents/paper_trader.py` Path B 写 `signal_pending` 后**不**标记
source opportunity 为 reviewed(line ~113 只对被永久跳过的 candidate 标 reviewed,
eligible→signal_pending 路径漏标)。同一批 open opportunity 每 5min 被 build_paper_candidates
重新入选、重复写 signal_pending → PI 看到重复 → skip。20min 内 ETH short 累积 4 条。

**授权**:用户 2026-06-24 授权"冻结例外修 PT-1(一行)"。

**修复**:paper_trader.py:153 写 signal_pending 成功后加
`update_opportunity(candidate["opportunity_id"], status="reviewed")`(与 line 113 同范式)。
spec `PL-PT-05`(spec_pipeline_paper_trade.py)status drift→implemented,xfail 移除。

**判决影响**:同例外 #1——Round 1 起点保留 6/22,修复前的重复-信号-致-skip 数据不计入
策略评估;修复生效后才是有效样本。

### 例外 #3 — 2026-06-24:CR-RE-03 demo 日损用 demo 资金基数(trust boundary risk/engine.py)

**触发**:例外 #1+#2 后 PI 真正下单,但全部被风控拒,拒因
`max_daily_loss_exceeded:-23.26<=-1.0`。

**根因**:risk/engine.py 检查 #8 日损门控对所有模式都用 `max_live_limited_cap_usdt`($100)
作 capital_base → demo 日损限 = $1。今早清理 contractSize bug 错误仓位的 -23.26 清账损失
触发了 demo 日损熔断,阻断当日 PI 全部交易。

**授权**:用户 2026-06-24 授权"现在修 CR-RE-03(SDD流程)"。属 trust boundary
(risk/engine.py change-controlled),非实验冻结区,但走完整 SDD:RE-B13 RED→改→GREEN。

**修复**:engine.py:154 按 `_is_live()` 分流——live 用 max_live_limited_cap_usdt,
demo 用 starting_capital_usdt(对齐检查 #13 范式)。config 同步 starting_capital
100→88000(运行态 API,日损限 $1→$880)。

**判决影响**:同上。注:这是 risk 风控参数修正,不改 AI 决策逻辑,但影响哪些 proposal
被拒,故一并记录。

---

> **三次例外的共同模式**:都是"修复 A 暴露出 B"的连锁——contractSize bug(650f608)→
> 清理错误仓位→损失触发日损(例外#3);profit-loop context bug(例外#1)→ PI 恢复下单→
> 暴露 signal 重复(例外#2)。Round 1 起点统一保留 6/22,**所有修复生效前的数据均不计入
> 策略评估**,6/24 起为干净观察窗口。
