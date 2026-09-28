# 09 · 下一轮分析目标（后端修复合并后）

> 日期：2026-09-28
> 基线：`origin/master` @ `6f1bb34b0`（#3624 OHLCV 缓存键、#3631 positions 投影 已合并）
> **重要：live Fedora runtime 仍是修复前构建**（实测 `limit=300` 仍 `provider_fetch` 2.7–3.3s，positions 仍 200 行 × 87 列 / 3041 KB）。修复已在 master，未部署。
> **性质：分析与汇报，不含代码改动。** 前端已冻结（即将重写），本域只看后端。

---

## 1. 已合并修复的源码核验

两条都已在 `origin/master`，实现正确：

| # | 核验 |
|---|---|
| #3624 | `data.py` docstring 重写为「every latest view goes through the shared TTL cache, keyed on (exchange, symbol, market_type, timeframe, limit)」，并写明旧行为「used to be consulted only for `limit == 100`, so the chart's own limit paid a full upstream provider round trip」。`before > 0` 仍 fetch fresh（正确保留）。 |
| #3631 | 新增 `PositionSummaryView`，对 6 列声明 `Field(default=None, exclude=True)`：`decision_reasoning`、`entry_market_summary`、`last_market_signal_evidence`、`reconciliation_evidence`、`reconciliation_identity`、`trailing_json`。 |

**#3631 的实现有两处值得记录的判断，未来同类修复应直接沿用**：

1. **为什么用 `exclude=True` 声明而不在路由里过滤** —— docstring 自述：让列表的 OpenAPI schema 与响应不可能漂移；且 `exclude=True` 能防止 Pydantic 把「声明但缺失」的字段回填成 `null`，否则读起来是「没有记录 rationale」而不是「不在这个读模型里」。**这是失败语义层面的考量，不只是性能。**
2. **`exit_reasoning` 被刻意保留**（未排除），理由写在 docstring：它体积小，且 causal-chain 视图要从列表行读它作为 order anchor。**即投影裁剪是逐列判断的，不是按类型一刀切。**

**部署后需复测**：`limit=300` 应返回 `memory_cache`（R20 同形状实测 65ms 是可达量级）；`/api/positions` 解码应从 3041 KB 降至 ~530 KB。

---

## 2. 本轮新发现（已立项）

| # | issue | 发现 | 我的独立核验 |
|---|---|---|---|
| 1 | **#3644** | universe 路径 `opportunity_id` 恒 `None` → 24/24 样本 `family=unknown` → 五个 alpha 层全 `not_measured` | ✅ 源码 + 运行时双向确认 |
| 2 | **#3642** | `/api/universe/planning-history?limit=200` 单响应 **20.4 MB**，`planner_input_json` 单列占 **92%** | ✅ 实测 |
| 3 | **#3645** | 同步 job 走 `result = fn()` 无墙钟上界，可永久占死单 worker 车道 | ✅ 源码确认机制；job 清单未核 |
| 4 | **#3643** | `candidate_planning_generations` 零 retention，约 1.2 MB/天无界增长 | ✅ grep 零命中 + 速率实测 |

### 2.1 最高优先级：#3644 归因链断裂

这是**当前唯一阻塞核心目标的一条**。

```
GET /api/analytics/alpha-attribution (window_days=30)
  universe / ranker / system_operator / learning / serenity
  → 全部 n=0、causal_grade=insufficient、measurement_status=not_measured

GET /api/analytics/trade-stats
  by_source: opportunity n=24 win=20.8 pnl=-5.2666
  by_family: unknown     n=24          ← 100%
```

根因在 `core/aletheia/execution/entry_plan_execution.py:360`：

```python
opportunity_id=plan.source_id if plan.source_kind == "serenity" else None,
```

而 `source_kind` 只有两个取值且默认就是 universe（`storage/models.py:1334`：`Literal["universe", "serenity"] = "universe"`）。**universe 是主交易路径，所以 `opportunity_id` 恒空**，消费侧 `analytics/family_scorecard.py:70-82` 的第一个 `if` 就断链，`scanner_family` 恒 `"unknown"`。

**为什么这条最重要**：06 域已确认 demo 亏损 57.1% 是止损滑价、43% 是方向。要判断那 43% 出在六族互斥假设的哪一族（momentum / mean_reversion / volatility / derivatives / event / short_bias，`continuous_scoring.py:449-1013`），**必须能按族归因**。现在恒 `unknown` ⇒ 「alpha 存在于哪一族」结构上无法回答。

> 修 #3618/#3619 能让亏损变小；**只有修 #3644 才能知道该改哪个 mechanism。**

### 2.2 #3642 是 #3631 的同类，规模 10 倍

| | #3631（已修） | #3642（新） |
|---|---|---|
| 端点 | `/api/positions` | `/api/universe/planning-history?limit=200` |
| 单响应解码 | 3,041 KB | **20,420 KB** |
| 罪魁列 | `decision_reasoning` 1.8 MB / 60% | **`planner_input_json` 19.0 MB / 92%** |

**修法已有范式**（#3631 的 `*SummaryView` + `Field(exclude=True)`），直接复用即可，属低风险高收益。

### 2.3 #3645 与资金安全的组合风险

车道隔离本身是**有意且正确**的设计（`scheduler.py:655-672` 注释解释了避免 SQLite 并行写者级联）。问题是组合：

- 每条关键车道只有 **1 个 worker**（`protective`、`maintenance_io`、`io`、`audit` 等）
- 同步路径 `scheduler.py:718` 的 `result = fn()` **无墙钟上界**（异步路径有，`:800`/`:868-896` 的 detach，即 #3432 遗留加固）
- 代码自述局限（`:197-199`）：「APScheduler's `max_instances=1` prevents overlap, but it cannot recover a job ...」

⇒ **一个卡住的同步 job 可永久占死整条车道。** `protective` 车道承载保护性生命周期检查，而 #3619 已确认 demo 止损**完全依赖本地轮询**（0/30 有交易所原生挂单）。**「止损靠轮询」+「轮询车道可被占死」+「进程级无 watchdog（#3612）」= 三层都没有兜底。**

---

## 3. 经核验被否证 / 降级的候选

诚实记录，避免这些数字被后续引用：

| 候选 | 收到的估算 | 我的实测 | 处置 |
|---|---|---|---|
| outcome 大规模排除 | 「94.6% 被 `unknown_semantics_version` 吞掉，421/445，raw −58.77 vs eligible −5.27」 | `strategy-kpis` 与 `cost-summary` 在 window_days=30/90/365 **一致返回 raw=26 / eligible=24 / excluded=2 / reasons={'unknown_semantics_version': 2}`，raw_pnl −6.136 / eligible −5.267 | **未复现，不立项。** 排除率 2/26 = 7.7%，不是 94.6% |
| planning 表增长速率 | 「215 行/天、19.7 MB/天、DB 超预算 140%」 | 200 行跨 397.9h ⇒ **约 12 行/天、1.2 MB/天**（低约 16 倍）。DB 总量**无法核实**：`/api/data/capacity` 等只读端点均返回 `Not Found` | **降级为 medium**（#3643），速率按实测记录 |
| 裸跑 job 数量 | 「约 15 个」 | 只确认 `:718` 这条路径存在且无上界，**未逐 job 分类** | 立项（#3645）但在 issue 里标注该数字未核 |
| 前瞻账本结算积压 | `candidate_outcome_backlog` 曾出现在 health issues | 当次实测 `/api/health` 为 `healthy`、`issues: []` | **已排除**（瞬态，阈值仅 900s 极易触发） |
| 测试反馈环 | 09-24 实测 `tier_unit` 8495 用例 300s 未跑完 | 据报 09-28 已落地 late-import 优化，`tier_unit` 约 1959 用例 / ~49.5s（**我未重跑验证**） | **不建议专项**，除非再次实测变慢 |
| `/api/universe/workbench` | — | 45.7 KB / 76ms，`current_candidates` 44KB 占 97% 但总量健康 | **健康，不查** |
| `operator/*` 三端点 | — | `tasks` 44KB/13列/30ms · `sessions` 26KB/14列/79ms · `status` 1KB/26列 | **健康，无可剥离后端工作**（故 #3628 是纯前端） |

---

## 4. 建议优先级

| 序 | issue | 理由 | 成本 |
|---|---|---|---|
| **1** | **#3644** 归因链 | **唯一阻塞核心目标**（alpha 是否存在、在哪一族）。若替代键可复用则成本很低 | 低–中（取决于 `source_id` 语义） |
| **2** | **#3642** planning-history 投影 | 20.4 MB 单响应；**修法范式已存在**（#3631），低风险 | 低 |
| **3** | **#3645** 同步 job 无上界 | 与 #3619 + #3612 构成资金安全三层缺兜底；但无实际发生证据 | 中（需先列全 job 分类） |
| **4** | **#3643** retention | 确定无上界，但实测仅 1.2 MB/天，非紧急；与 #3642 同表，一起做收益最大 | 低 |

**建议 1 与 2 并行**：#3644 在 attribution/execution 层，#3642 在 API 投影层，无重叠。

**部署优先于这一切**：#3624/#3631 已在 master 但 runtime 未更新，收益尚未兑现。

---

## 5. 本域未验证

- **部署后的实际收益**。#3624/#3631 的效果只在源码层核验，runtime 仍是旧构建。
- universe 路径 `plan.source_id` 的确切语义（是否等于 opportunity id）—— 决定 #3644 的修复成本。
- 历史 24 笔样本能否回填 `opportunity_id`。
- `outcome_integrity.py` 附近是否真有可复用的替代关联键（只有线索，未读实现）。
- 走同步裸路径的 job 完整清单（#3645 的前置）。
- 数据库总量与预算余量（只读容量端点在 runtime 不可用）。
- `tier_unit` 当前真实耗时（未重跑）。
- 其余未系统扫描的端点：`/api/trades`、`/api/opportunities`、`/api/knowledge*`、`/api/agent-runs` 是否存在第三个 blob 列投影泄露。

---

## 6. 第二轮补查（部署前继续分析）

### 6.1 端点扫描收敛：blob 列投影只剩 #3642

把剩余列表端点全部按字节量了一遍（`curl --compressed` + 逐列 JSON 字节统计）：

| 端点 | 解码 | 行 | 列 | 最重列 | 判定 |
|---|---:|---:|---:|---|---|
| **`/api/universe/planning-history?limit=200`** | **20,420 KB** | 200 | — | **`planner_input_json` 92%** | **#3642** |
| `/api/opportunities` | 205.6 KB | 25 | 38 | `metadata` 71% · `generation_json` 21% | 有界，不立项 |
| `/api/trades` | 171.2 KB | 100 | 46 | `uta_account_identity_json` 49% | 可接受 |
| `/api/analytics/trade-paths` | 76.6 KB | 25 | **98** | 无单列 >4% | 健康 |
| `/api/market/snapshots` | 53.8 KB | 200 | 11 | 无 blob | 健康 |
| `/api/agent-runs` | 21.9 KB | 50 | 14 | 无 blob | 健康 |
| `/api/knowledge` | 14.8 KB | 50 | 10 | 无 blob | 健康 |
| `/api/analytics/operator-decisions` | 2.2 KB | 2 | 15 | — | 健康 |

**结论：该缺陷类在 #3631（已修）+ #3642 之后收敛完毕，不需再扫。**

两条边界值得记录：

1. `/api/opportunities` 的 `metadata` 占 71%，但 **`limit` 参数无效**（传 25/100/200 恒返回 25 行 / 205.6 KB）。当前有界，**但若将来放开行数上限会立刻变成第三个同类缺陷**。
2. `/api/analytics/trade-paths` 有 **98 列**却只有 76.6 KB —— **列多 ≠ payload 重**，判定必须按字节。这反向印证 #3631 裁 6 个具体 blob 列（而非按列数一刀切）是对的。

### 6.2 #3644 修复成本已探明：族数据已经存在

原 issue 把 `plan.source_id` 语义列为未验证。查清后结论对实施者直接有用：

- **`candidate_family` 是 EntryPlan 一等字段**（`trading/entry_plan.py:182`）且是表上 **NOT NULL 列**（`storage/migrations.py:3425,4049,4220`）。
- live 实测 `GET /api/universe/planning-history?limit=50` → `candidate_family` 分布 **`{'mean_reversion': 28, 'momentum': 22}`**。**族归属被正确记录着**，不是没产生。
- 断链在**连接键缺失**：position 行只有 `proposal_id` / `opportunity_id`，**没有** `entry_plan_id` / `candidate_id` / `planning_generation_id`；`TradeProposal` 也只有 `opportunity_id`（`storage/models.py:383`）。**持有 `candidate_family` 的 EntryPlan 根本不在归因链上。**
- `EntryPlan._contract_is_coherent`（`trading/entry_plan.py:290-296`）要求 `(source_kind, source_id, trade_school)` 三者同时存在，故 **universe 路径的 `source_id` 必然非空** —— `:360` 不是"没值可用"，是刻意只填 serenity 分支。

**建议路线 B（而非放宽 `:360` 的分支）**：在 proposal/position 上引入指回 EntryPlan 的键，让归因直读已有的 `candidate_family`，**不再绕 `opportunities` 表**。理由：`candidate_family` 是更近的真相源；绕开 `opportunities` 也绕开该表的 retention 风险。放宽 `:360` 的风险是——若 universe 的 `source_id` 不是 `opportunities.id`，则 `family_scorecard.py:74-80` 的 `SELECT ... FROM opportunities WHERE id = ?` 仍查不中，**问题从"恒 None"变成"恒查不中"，被埋得更深**。

### 6.3 发现更大的样本口径：446 笔而非 24 笔

`GET /api/analytics/round1-progress` 暴露了此前未用的口径：

```
Round1 起点 2026-06-22 · 首笔平仓 2026-08-07 · 末笔 2026-09-28T05:34
closed_trades 446 · win_rate 35.2% (157W/289L) · profit_factor 0.7215 · total_realized −59.0218
```

**比此前全程引用的 24 笔 / −5.27 USDT 大 18.6 倍。** 按平仓原因：

| close_reason | n | 占比 | net PnL | 胜率 |
|---|---:|---:|---:|---:|
| **`stop_loss`** | **232** | **52%** | **−135.09** | **18.1%** |
| `pi_agent_portfolio_review` | 152 | 34% | +1.77 | 40% |
| `take_profit` | 51 | 11% | **+77.10** | **100%** |
| 其余 4 类 | 11 | 2% | −2.80 | — |

**`stop_loss` 单项 −135.09 = 总亏损的 229%。若止损不亏，Round1 是 +76.07 盈利。** `take_profit` 51 笔全胜说明**赢的一侧正常**，问题集中在止损侧 —— 与 #3618/#3619/#3620 的执行链诊断同向且更强。

按 24 笔实测的 +0.822%/笔 外推到 232 笔止损（均名义 ~20.9 USDT）≈ **40 USDT**，即 59 USDT 总亏损中可能 **~2/3 住在止损执行侧**。**这是外推不是实测** `[推断]` —— 446 笔逐笔滑价受 `/api/positions` 200 行上限限制未能重算。但结论方向不依赖它：止损占 229% 净亏损 + 0/30 有交易所挂单，两个事实各自独立成立。

**优先级影响**：#3619（交易所原生止损）应是执行链里最高优先级，#3618 先做或同做（否则无法度量回收）。

### 6.4 排除一个误判：Observation Lab 关闭是有意默认

`filter-controller/persistent` 返回 `empty_reason: "controller_disabled"`、`schedule.enabled: false`，`observer_pre_filter_diagnostics` 为 `row_count 0 / cohort_count 0`，且该 filter 版本 `decision_count: 1023` 但 `last_seen_at` 停在 2026-08-27。

**这不是缺陷**：`core/aletheia/config/schema.py:2052` 是 `shadow_enabled: bool = False`，注释明写 *"Opt-in switch for a **diagnostic** observe job with **no trading consumer**"*，docstring 强调 *"It NEVER writes trading config."* 属刻意 opt-in 默认关闭。

**且它不是 #3644 的成因**——`family=unknown` 源于写侧 `opportunity_id` 恒 `None`，与该开关无关。前瞻账本本身仍在产出（446 笔、末笔在今天）。

### 6.5 第二轮后的优先级修订

| 序 | issue | 变化 |
|---|---|---|
| **1** | **#3619** 交易所原生止损 | **升到首位**：446 笔样本显示止损占 229% 净亏损、胜率 18.1%，而它完全无交易所挂单 |
| **2** | **#3618** 滑点记账 | 与 1 同做或先做（否则无法度量回收） |
| **3** | **#3644** 归因链 | 保持高位，但**成本已探明更低**（族数据已存在，见 6.2） |
| **4** | **#3642** planning 投影 | 不变，低风险高收益 |
| 5 | #3645 job 无上界 | 不变 |
| 6 | #3643 retention | 不变 |

**与第一轮的差异**：第一轮把 #3644 排第一（因为它阻塞"alpha 在哪一族"）。446 笔样本出现后，**执行链的确定性收益更大且更直接** —— 止损侧是唯一亏损来源，且修法明确。#3644 仍重要，但它回答的是"方向侧哪一族有问题"，而当前数据显示**方向侧不是主要失血点**。
