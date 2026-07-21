# AI 相机交易方向验证协议

> **性质**: 硬约束。所有 AI Agent 和开发者必须遵守。违反 = 实验数据作废。
> **Created**: 2026-06-21
> **协议状态**: Round 1 已于 2026-06-27 **提前终止**(详见下方「Round 1 终止判决」段);冻结已解除,进入「Round 1 后全量修复」阶段。Round 2 是否启动待修复完成后判定。

> **2026-07-17 repair epoch**: funding raw-unit correction, candidate-specific
> 15m/1h/4h context, Hyperliquid OI consumption, and the 1.5 ATR no-chase guard
> are attributed under `system_operator_vergex_utilization/v1`. This is a new
> B-class policy epoch inside the post-Round-1 repair period, not the start of
> Round 2. Position X remains observation-only; its owner-aggregate cohort must
> collect at least 14 days before a human review and has no decision influence.

> **2026-07-21 Post-trade review coverage epoch**:
> `post_trade_review_coverage/v2` replaces strategy-level 24-hour recency with
> exact active `position:{id}` evidence over the centrally eligible outcome cohort.
> Archived/deprecated reviews no longer suppress replacement, and API/operator
> backlog truth now shares the same cohort as the reviewer. This B-class repair can
> regenerate previously suppressed Learning input; do not pool downstream review,
> retrieval, or decision observations with the pre-v2 window. Review prompts,
> eligibility semantics, Risk, Execution, Exchange, and Live remain unchanged.

> **2026-07-18 Opportunity funnel integrity repair epoch**:
> `opportunity_funnel_integrity/v1` adds a five-carrier admission bound ahead of
> `signal_pending` and expands snapshot change detection to direction, eligibility,
> TTL/risk flags, venue and exact entry evidence price. Overflow remains open and
> retryable; ranking scores, thresholds, Operator cooldown, Risk and Execution are
> unchanged. These B-class delivery/carrier-timing changes start a fresh observation
> window and must not be compared as one cohort with earlier unbounded-enqueue or
> coarse-fingerprint data. The other fixes in the batch are A-class
> state/evidence/time-window/UI honesty repairs.

> **2026-07-18 Opportunity carrier continuity epoch**:
> `opportunity_carrier_continuity/v1` closes the earlier snapshot-coupled carrier
> window. Universe supersede now terminalizes only unclaimed `open` generations;
> a queued Operator carrier remains authoritative until its Opportunity
> `valid_until`, with pending-age 30 minutes retained only for legacy rows that
> lack a parseable deadline. Reused active snapshots may slide queued validity,
> Operator defer is capped by source validity, and a live
> symbol/direction/scanner-family carrier suppresses duplicate enqueue under the
> explicit `active_operator_carrier` fact. The Ranker soft-TTL window is no longer
> preempted by daily cleanup. Funnel/trace reporting separates unique setups,
> snapshot generations, supersede, and true TTL expiry. This changes carrier
> delivery duration and therefore starts a fresh B-class observation window;
> Ranker scores/thresholds, conviction, sizing, Risk, Execution, Exchange and Live
> are unchanged. Do not pool proposal-rate, Operator-latency, or defer-resolution
> observations with pre-v1 data.

> **2026-07-18 Ranker delivery visibility epoch**:
> `opportunity_ranker_delivery/v2` closes the narrower Universe-only wake window.
> Committed Opportunities, eligibility-affecting settings, active Guard changes,
> and released Operator queue slots now coalesce into an immediate Ranker wake;
> the three-minute scheduled run remains the safety fallback. Each dispatched run
> persists its coalesced source list in `AgentRun.trigger_type`, while Overview and
> `inspect entry-status` read the same durable filter/lifecycle facts instead of
> re-running Ranker. This B-class change affects delivery timing only; Ranker
> filters, weights, thresholds, queue capacity, Risk, Execution, Exchange and Live
> are unchanged. Do not pool v2 latency/conversion observations with the earlier
> delivery window.

> **2026-07-18 Direct-skip semantics epoch**:
> `system_operator_skip_remediation/b1.2` closes the prior b1.1 observation window.
> The sole decision treatment is a bounded counterfactual confirmation before a direct
> model skip may become terminal: only typed, candidate-specific adverse evidence can
> confirm it; `insufficient_evidence`, rank priority, or malformed output defers without
> consuming the pending carrier. A 120-minute material-context fingerprint suppresses
> unchanged retries, and complete rationale persistence is an A-class audit correction.
> Conviction 55, existing trade debate math, Ranker selection, sizing, Risk, Execution,
> Exchange, and Live remain unchanged. Compare new direct-skip/proposal/queue/outcome
> metrics only within b1.2; b1.1 is baseline, never a pooled cohort.

> **2026-07-18 Candidate-fact judgment epoch**:
> `system_operator_skip_remediation/b1.3` closes b1.2. Each model call now sees
> exactly one durable carrier; every terminal adverse reason is validated against
> a typed candidate fact bundle, and the generated fact references are persisted.
> Neutral retries are suppressed per candidate rather than per whole queue, so the
> next visible carrier can proceed. The B2 fingerprint uses the actual b2 schema,
> missing numeric observations remain unknown, and the evidence manifest records
> source-chain ids, freshness, B2, regime, sample size, and validated claims.
> Conviction thresholds, Ranker weights, sizing, Risk, Execution, Exchange, and Live
> are unchanged. Compare b1.3 outcomes only inside its new policy epoch; b1.2 is the
> baseline and must not be pooled.

> **2026-07-18 Candidate-context delivery epoch**:
> `system_operator_skip_remediation/b1.4` closes b1.3. The exact pending candidate
> is hydrated by `asset_id` independently of the bounded global feature display;
> missing, incomplete, or stale exact technical context defers before any model
> invocation, and a newly persisted exact feature snapshot changes the material
> fingerprint and immediately wakes/reopens Operator evaluation. Operator Learning
> authorization now occurs only after the context is scoped to one market-observer
> carrier, using a stable candidate key and the exact Opportunity 4h outcome link;
> mixed queues no longer emit an unattributed exposure. Ranker weights, conviction
> thresholds, sizing, Risk, Execution, Exchange, and Live are unchanged. Compare
> b1.4 candidate decisions and Learning contribution only inside their new policy
> epochs; b1.3 and unlinked prompt-bundle observations are historical baselines and
> must not be pooled.

> **2026-07-19 Opportunity information integrity epoch**:
> `opportunity_information_integrity/v1` closes every earlier information-input
> window. Observer admission now uses structured 1h RSI/ATR facts; cross-venue OI
> uses a same-source 45-75 minute baseline; spot excludes derivatives structure;
> regime uses true seven-day market-cap and completed-daily true range; prediction
> direction is polarity-aware; news requires exact entities plus successful bounded
> analysis; Opportunity and PiDecision manifests preserve T0 trigger and rendered T1
> source identities. Serenity exact-carrier context, partial-theme retry, active
> evidence reassessment, and complete post-trade rationale consumption are part of
> the same deployment. This combined B-class repair intentionally starts one new
> window because the treatments cannot be independently attributed when deployed
> together. Do not pool conversion, decision, research, review, or outcome metrics
> with any pre-v1 data. Risk, Execution, Exchange, Live, credentials, sizing, and
> hard limits are unchanged.

---

## 核心赌注

> LLM 从训练数据中已经"知道"某些市场 pattern,无需学习,只需提取。
> 本协议验证:这些已有知识在真实市场是否产生正期望。

---

## 协议结构:3 轮 × 14 天

| 轮次 | 起止日期 | 系统版本 | 状态 |
|------|---------|---------|------|
| Round 1 | 2026-06-22 → **2026-06-27 提前终止** | v5 (6/21 最佳版本) | **已终止(Kill + 管道污染)** |
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
     Round 1 将是新决策引擎(System Operator structured decision + debate)的首次真实数据。
```

---

## 版本记录

| 日期 | 变更 |
|------|------|
| 2026-06-22 | 初版。协议生效,Round 1 开始。基线数据记录 |
| 2026-06-23 | **冻结例外 #1(用户授权)** — 见下方记录。Round 1 起点保留 6/22,标注本修复点 |
| 2026-06-27 | **Round 1 提前终止(owner 决定)** — 见下方「Round 1 终止判决」段。冻结解除,进入全量修复阶段 |

---

## Round 1 终止判决 — 2026-06-27(提前结束)

**判决:Round 1 提前终止,冻结解除。** owner 决定。

**依据**:
1. **Kill 信号明确**:干净观察窗(6-25 15:22 起,已排除例外 #1-#5 的 infra 污染)实测全品种 PF≈0.06,远低于 K1(<0.85)/K2(<1.0);此前看到的混合 PF 0.769 是被调试期旧盈利数据粉饰的假象。
2. **方向高度集中**:成交集中在 short(约 17/23),非分散的策略 edge,属单向押注。
3. **实验有效性塌方(决定性理由)**:全生命周期审计(`docs/issues/audits/LEARNING_LOOP_AUDIT_2026-06-26.md`)证实——唯一活着的学习闭环(post_trade_review → FTS → ranker `_lesson_penalty`)喂的是**幻觉**,且该链穿过被冻结的 ranker → Round 1 评估的不是"干净的 AI 方向判断",而是"方向 + 幻觉随机噪声"。**评估管道本身被污染,继续观察的边际价值为负。**

**这不是放宽阈值逃避判决**:干净窗数据已明确给出 Kill;提前结束 = 接受 Kill + 承认评估管道必须先修复才能产生可信数据。符合协议精神(数据已给答案 + 管道污染使继续观察无意义),不是"再优化一下"的逃生。

**后续(非标准轮间流程)**:owner 决定先做一次**全量 bug 修复**(反幻觉链 + 报告发现的所有 bug + 所有被冻结阻塞的 fix),让评估管道恢复可信,**然后再决定是否启动 Round 2**。这超出协议"轮间只允许一个改动"的常规——因本批次性质是"修复被污染的实验管道与积压缺陷",而非"优化策略方向"。Round 2 是否启动、以何基线/版本启动,待全量修复 + 回归通过后单独判定并记录于此。

---

## 冻结例外记录

> 冻结区改动需用户显式授权 + 留痕。此处记录每次例外的根因、范围、判决影响。

### 例外 #1 — 2026-06-23:最小赚钱循环 context-injection bug 修复

**触发**:Round 1 期间发现"最小赚钱循环"断流——20h 零开仓。805 opportunities
持续生成但 System Operator 100% skip。

**根因(逐行核实)**:System Operator(LLM)被喂了一批**错误事实**,构建出"账户濒临破产"
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

### 例外 #4 — 2026-06-25:回撤刹车误用持仓 notional 当 equity(trust boundary risk/engine.py)

**触发**:用户发现 OKX Demo 一整批期货 Short 被拒,拒因异常:
`Position size over cap — $21.72 > $0 cap`。$0 上限 = 任何仓位都开不了。

**根因(亲核)**:`risk/engine.py:_portfolio_drawdown_scale`(检查 #5 回撤缩放)按
`proposal.execution_mode`(okx_demo)过滤 `portfolio_snapshots`,拿到的是**每个 mode
自己的行**,其 `total_value_usdt` 是持仓 **notional**(Σqty×price)而非账户 equity
(`portfolio/pnl.py:373` 写入)。6-25 13:53-14:03 五笔止盈平仓(净赚 +12),持仓
notional 从 ~$272 collapse 到 $16.5 → drawdown 误算 `(277-16.5)/277=94%` → scale=0
→ `effective_cap = 26.84×0 = $0` → 所有 demo 提案(notional>$0)全被拒。**赚钱平仓
反而把自己锁死。** 真实账户(`exchange_account` 行)全程稳定 ~$88,400,根本没回撤。

**授权**:用户 2026-06-25 授权方案"drawdown 改用真实 equity",走 SDD。属 trust
boundary(risk/engine.py change-controlled),非实验冻结区。

**修复(commit 70a7b64)**:engine.py:356 交易所模式(okx_demo/*_demo/live)的
drawdown 改读 `exchange_account` 行的真实 equity;`paper_internal` 无交易所账户、
mark-to-market `total_value_usdt` 即 equity,保持原路径。spec RE-B05(分段数学保留)
+ 新增 RE-B05a(数据源回归)。risk_engine_contract.md 关卡 #5 注明数据源。

**判决影响(重要——影响 G6 样本量解读)**:此 bug 自 **2026-06-24 17:25 → 06-25 15:18
UTC(~22h)** 产生 **144 笔 `$0 cap` 误拒**。这意味着这 22h 内 PI 想开的仓被风控
无差别拦截,**人为压制了交易样本**——Round 1 的 G6 样本量(T+2.6 天仅 17 笔、远低于
60 笔门槛)有相当部分归因于此 infra 误拒,而非 PI 自身不愿交易。判决时:**06-24
17:25 → 06-25 15:18 的"被拒"不能解读为"策略/PI 拒绝交易",而是 infra 故障屏蔽**;
此窗口的低成交率不计入"PI 决策质量"评估。修复生效(15:22 起 approved=1 无违规)后
才是 demo 开仓不受此 bug 影响的有效窗口。

**生效验证**:重启后 okx_demo drawdown_scale=1.0、effective_cap 恢复 $26.84;
risk_decisions 15:22:22 / 15:24:11 / 15:27:56 连续 approved=1 空 violations
(此前 15:18:16 仍是 `...>0.0` 误拒)。全 risk 测试 56 passed。

### 例外 #5 — 2026-06-25:proposal rationale 双重截断刈断 meta JSON(冻结区 trading_decision.py)

**触发**:用户在 proposal detail 看到 rationale "没搞全"(thesis 半句被切)+ "夹杂弱代码"(原始 `---{"conviction":64,...` JSON 串直接显示)。

**根因(亲核)**:`agent_shell/trading_decision.py` 用脆弱模式拼 rationale:
`f"{reasoning[:200]}\n---\n{json.dumps(meta)}"` 再 `[:500]`。两层截断:reasoning 砍 200(thesis 断句)+ 整串砍 500,而 meta JSON ~1.1KB → `[:500]` 把 JSON 刈成非法串。实测 5/5 条 `agent_rationale` 恒=500 长、JSON 全部 `Unterminated string`。前端 `Proposals.tsx:parseDecisionMeta` 的 `JSON.parse` 必失败 → 落 catch 把整条原始串(含碎 JSON)当 text 显示 = "弱代码外泄"。

**授权**:用户 2026-06-25 授权"按计划完整执行"(前端 + 后端冻结文件)。

**修复(commit 85b5807)**:
- 后端 trading_decision.py:只截 human thesis(`_MAX_RATIONALE_TEXT=600` 常量,带 `…`),**删除整串 `[:500]`** → JSON 永远完整可解析。`agent_rationale` 列是 TEXT 无限制。
- 前端 Proposals.tsx:`parseDecisionMeta` catch 分支返回分隔符前的干净 text(非整条原始串);`ProposalRationaleDetail`/`ProposalRationale` no-meta 分支渲染 `text` 而非 raw rationale → 坏值也永不向人倒 JSON(防御纵深)。
- 回归测试 `tests/test_proposal_rationale.py`(3 条):JSON 必可解析 / thesis 单独截断非整串 500 / 短 thesis 不动。

**判决影响(重要——PI 输入信号变化)**:`agent_rationale` 的 meta JSON 会被 `analytics/reasoning_feedback.py:82-89` 解析出 `scanner_family` → 经 `trading_context.py:317-319` 喂回 PI prompt。修复**前** JSON 全损坏 → reasoning_feedback 一直提取失败拿到 "unknown"(等于此信号对 PI 一直缺失)。修复**后** JSON 完整 → reasoning_feedback 首次成功向 PI 提供真实 scanner_family。**这是一个 PI 此前从未收到的新输入信号**,可能微改决策。判决时:**6-25(本修复生效)起,PI 上下文比此前多了 reasoning_feedback 的 scanner_family 维度**;此前 proposal rationale 损坏不影响已落决策(rationale 是决策后产物),但污染了 reasoning_feedback=unknown。视作 PI 输入口径的一次小幅增强,在阶段评估时区分修复前/后。

**生效验证**:`tests/test_proposal_rationale.py` 3 passed;端到端实证 — 2026-06-25 16:12 UTC 在重启后跑了一次真实 `submit_decision_as_proposal`(reasoning ~470 字、meta JSON ~1.1KB),落库 rationale **总长 1704**(旧 bug 恒=500)、`json.loads(rationale.split('\n---\n')[1])` **成功**(conviction=64, debate_bear=72, supporting 5条齐全)、thesis 在 600 处带 `…` 优雅断,**不再半句断**。

**验证副作用透明披露**:该端到端测试用真 reasoning 跑通整条生产链路 → 在 okx_demo 实际下单 (proposal `da6ce654db3c`, order `ee2f22270dca`, exchange order `3687683239182241792`, qty=0.2 contracts=20 XRP short @ avg $1.0396, fill notional ~$20.79)。此 20 XRP 被 reconciler 在 9 小时内并入既有合法 XRP 短仓 `bd77e55f`(局部仓表 qty 42、交易所 0.42 contracts/42 XRP 完全对齐),**未产生孤儿仓位、未在 positions 表创建独立行**(`order_id` / `proposal_id` 关联到 positions 行均为 0),**Round 1 realized_pnl 评估不受污染**(读 `positions.realized_pnl`,该位置仍 open 且累计在合法仓上)。仅交易所侧多承担了 ~$20.8 的额外 short 暴露,平仓时 PnL 影响视市场而定但归属合法仓的整体盈亏轨迹,不视作独立"测试交易"。**判决时此细节不计入 G1-G6/K1-K4 任何指标**(无独立 closed trade、无独立 realized_pnl),仅作运维痕迹保留。

---

> **五次例外的共同模式**:都是"修复 A 暴露出 B"或"语义/格式混淆"的连锁——contractSize
> bug(650f608)→ 清理错误仓位→损失触发日损(例外#3);profit-loop context bug
> (例外#1)→ PI 恢复下单→暴露 signal 重复(例外#2);例外 #3/#4 同属 risk/engine.py
> 的"用错资金基数/用错净值口径"家族(日损用 live cap;回撤用持仓 notional)。
> **notional vs equity 混淆**两次咬人(余额读错→例外#1;回撤口径→例外#4)。
> 例外#5 是"human text + machine JSON 拼成单串再截断"的格式脆弱——根治=消除该模式本身。
> Round 1 起点统一保留 6/22,**所有修复生效前的数据均不计入策略评估**;特别地,
> 例外#4 的 144 笔 infra 误拒窗口(6-24 17:25→6-25 15:18)不计入"PI 是否愿意交易"
> 的样本;例外#5 修复使 PI 自 6-25 起多收到 reasoning_feedback.scanner_family 信号。
> 6-25 15:22 起为 demo 开仓不受已知 infra bug 影响的干净观察窗口。
