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
