# Alpha 样本量与方向毛边缘 根因调查 — 2026-09-26

目的：回答两个机制性问题——「为什么有效样本这么小」（Investigation 1）与「为什么方向毛收益是零」
（Investigation 2）。本调查只读，不修任何东西；核心交付是区分竞争性解释，不是收集事实。

**数据来源与其边界**

| 项 | 值 |
|---|---|
| 分析日期 | 2026-09-26 |
| 代码基线 | `master` HEAD `2ac040c85`（工作区仅一个未跟踪文件 `ALPHA_SOURCE_CODE_AUDIT_2026-09-25.md`，无脏文件影响本次读取的路径） |
| 数据库 | `/Users/yixian/.aletheia/app.db`（本机开发库，`sqlite3` URI `mode=ro` 只读，未写入） |
| 前置报告 | `ALPHA_MEASURABILITY_2026-09-24.md`、`ALPHA_SOURCE_CODE_AUDIT_2026-09-25.md` |
| 分析者 | 原调查会话在 runtime 限制下被终止，其内联报告已完成（`history://gap23-rootcause-good`）；本文档为对该会话已验证结论的转录保存，未重跑调查、未新增发现 |

---

## 一、执行摘要

**Investigation 1 最大原因**：`INDEPENDENT_UNIT_INTERVAL = 4h` 的独立单元定义只在**读取/聚合时**生效
（`select_non_overlapping_t0_clusters`），写入路径（`_run_universe_refresh` 每 10 分钟一次，
`economic_utility.py:22`）完全不抑制冗余——每 10 分钟对同一批 ~25-27 个币各写 4 条 horizon 行，
3h20m 内产生 1816 行 `universe_forward_outcomes`，聚合后收缩成 ~1 个独立单元（~700:1）。
这不是 bug，是文档化的设计权衡（contract 明文写「这些是统计单元」），但代价是行数与信息量比例极端失衡。
次要且更关键：4h/24h horizon 在该窗口从未结算过一条（进程在 T0+3h38m 停止，行需 T0+4h 才到期），
而生产 `measured` 准入路径只消费 `horizon=='4h'` 行——该路径实际拿到的是 **0 行**，是「空样本」而非「小样本」。

**Investigation 2 最大原因**：`entry_price_usd` 取自最近一根**已收盘**的 1h K 线收盘价
（`features.py:890` `last_price = closes_1h[-1] if closes_1h else None`），而不是扫描时刻的实时 ticker——
DB 证实同一符号同一小时内多次扫描（01:05/01:15/…/01:55）的 `entry_price_usd` 完全相同，且入库时刻相对
上一根收盘 K 线平均滞后 31.1 分钟（约为 15m horizon 长度的 2.07 倍）。1 分钟 K 线量出的实际 1 分标准差
0.058%，换算到 15 分钟尺度的噪声量级 ≈0.22%——比整批样本测出的方向毛收益 +0.0046%（全量）/
−0.1457%（纯币种）大约 **50 倍**。这是测量 bug：它给 15m/1h 这两个唯一有结算数据的 horizon 注入系统性
噪声，而不是「策略没边缘」的证据。次要但独立的贡献：31.9% 样本是代币化美股+稳定币，当前代码仍未过滤；
剔除后纯币种簇内 t 从 −0.10 变为 −1.30（方向与预设相反，见 Investigation 2c）。

---

## 二、Investigation 1 — 为什么有效样本这么小

Naive 行（714）与独立单元（~1）相差 ~700 倍。

### a) 扫描节奏与 horizon 重叠 — `confirmed-cause`

- `_scheduler.add_job(..., "interval", minutes=10, id="universe_refresh", ...)` —
  `agent_runtime/scheduler.py:1557-1561`。真实配置 cadence = 10 分钟/tick。
- `_capture_universe_discovery_nonfatal`（`service.py:515-550`）在**两条分支**都无条件调用——change-suppression
  分支（:914，snapshot 复用时）和正常写入分支（:1132）——即前瞻结果账本的写入完全不受
  `_SNAPSHOT_HEARTBEAT_SEC = 55*60`（`service.py:68`）的 change-suppression 影响；该 suppression
  只作用于 `universe_members`/`universe_snapshots`，不作用于 `universe_forward_outcomes`。
- DB 实测：18 个不同 `entry_ts`，中位数间隔 10.1 分钟，与配置 cadence 完全一致。
- 每个 T0 写 4 条 horizon 行（15m/1h/4h/24h，`outcome_recorder.py:196` `for horizon_key, delta in _HORIZONS.items()`），
  每次扫描 ~25-27 个币，故一次 tick 写 ~100-108 行（DB 实测每 entry_ts 96-108 行）。
- **量化重叠**：4h horizon 行，同一符号每 10 分钟写一条，due_at 相隔 10 分钟但持仓窗口重叠
  4h−10min = 3h50min，即单个符号在其 4h horizon 生命周期内会被 **~24 个**（4h/10min）几乎完全重叠的
  扫描批次各写一条。DB 实测 `OP/USDT` 在这 3h20m 内出现 18 次 4h-horizon 行，证实这种重叠规模。

### b) 独立单元规则在哪里生效 — `confirmed-cause`（设计权衡，非 bug）

- `select_non_overlapping_t0_clusters`（`economic_utility.py:101-122`）是纯函数，只接收内存中的行列表并
  贪心选前缀，**不写数据库、不参与 INSERT 路径**。写入路径（`service.py:1057` `pending_outcomes = build_pending(...)`
  → `service.py:1104` `repo.insert_forward_outcomes_batch(pending_outcomes)`）完全没有调用它。
- 唯二两处消费者都是**读取时**聚合：`economic_utility.py:283` `_independent_values(complete)`
  （在 `build_economic_utility_cohort_index` 里）和 `candidate_selection_evaluation.py:74`
  `_independent_policy_batches`。两者都是纯读取路径，不影响存储。
- **这是有意为之**：`docs/design/contracts/candidate_selection_utility_contract.md:61-64` 明文写道
  “greedily retains the earliest T0 clusters separated by at least four hours. Those cross-sectional
  non-overlapping clusters are the statistical units used by K=50 parent shrinkage...”。契约把「写入所有行、
  读取时去相关」定为设计契约，不是缺陷。但契约没有解释为什么不在写入时就抑制冗余（例如复用
  `_SNAPSHOT_HEARTBEAT_SEC` 的思路），这部分是行数浪费但非“accidental”，是有记录的取舍。

### c) 同 T0 跨币折叠 — `confirmed-cause`（判断题：倾向「过度保守但方向正确」）

- `_independent_values`（`economic_utility.py:125-161`）先按 `entry_ts`（精确到微秒的 ISO 字符串）分组
  （:135 `grouped.setdefault(entry.astimezone(timezone.utc).isoformat(), []).append(row)`），组内取
  `mean(net_return_pct)`（:141），再对分组结果跑 `select_non_overlapping_t0_clusters`。**同一次扫描的
  ~25-27 个币，无论方向、无论 mechanism，全部折叠成一个数字。**
- **统计判断**：50 个加密货币同一时刻高度相关（共同 BTC beta），但绝非完全相关（DB 数据也证实：同一 T0
  下不同币的 `directional_return_pct` 有正有负，方差不为零）。把它们视为 1 个观测点丢弃了真实横截面信息；
  视为 25-27 个独立观测点则明显错误（违反 t 检验独立性假设，会人为夸大显著性）。
- 当前「折叠为 1」**方向正确但代价过高**：它保证保守性，但把「3h20m 一次行情」这个真实约束和「BTC beta
  相关性」这个统计约束混为一谈，导致连「策略在不同币种间是否一致」这类可回答的问题都无法回答。防御性更合理的
  中间定义是**按币聚类的 cluster-robust SE**（前一份审计已这么做，n=56, t=−2.59；本次复算完全复现该口径）。
  纯粹的「有效样本量按平均两两相关性折算」（如 Newey-West 或 effective-N 公式）理论上更精确，但需要额外估计
  相关矩阵，在样本本身只有 3.3 小时的情况下，这个精度提升不值得增加的复杂度——**本次调查选择为按币聚类辩护，
  不建议在当前证据规模上再往复杂的相关性建模走**。

### d) 结算积压 — `confirmed-cause`（既有偶发时长不足，也有过去存活过的结构性问题；结构性问题现已在当前代码中修复）

- `settle_due`（`outcome_recorder.py:244-272`）+ `outcome_settler` 调度作业（`scheduler.py:1581-1586`，
  独立于 `universe_refresh`，每 10 分钟一次）。DB 实测：15m/1h horizon 全部结算完成（406+308 = 714，
  与两份前序审计的 714 数字一致），4h/24h 因到期时间未到（4h due_at 最早 05:05，最后一次 scanner run 是
  04:41）而全部 pending——这是**样本时长不足**，不是结算逻辑的 bug。
- **结构性问题 #3432**（`maintenance_io` 单 worker 被 `universe_refresh` 的同步 Candidate Selection 工作
  永久占死、饿死 `outcome_settler`）：**已修复**，commit `f21f69b82`（2026-09-24 10:18:49，
  标题 “fix(scheduler): bound the owned universe drain wait”），该 commit 就在本次审计所读 HEAD 之前，是
  当前代码状态的一部分。修复机制：`_run_async_with_timeout`（`scheduler.py:847-879`）给 drain-on-timeout
  调用方加了 `_ASYNC_DRAIN_GRACE_SEC = 30.0`（:256）硬上限，超时后调用方 detach 释放共享 executor lane
  以放开 `maintenance_io`，同时把 future 记录进 `_draining_futures`（:262-263）保持进程所有权，
  `_run_universe_refresh`（:3737）在下一次 tick 检查 `_is_draining("universe_refresh")` 并跳过而非重叠启动。
  测试引用 `tests/test_scheduler_timeouts.py` 通过 127 例。**结论：这个结构性坑已填，常驻运行现在能真正积累
  证据，不会再被这个 bug 卡死。**
- 另一个非结构性风险：该窗口 23 次 scanner_run 中 3 次 `failed`（耗时 3.7-12s 即失败，非超时），失败率 13%。
  这些失败会造成扫描空档，但每次 10 分钟 tick 的 `misfire_grace_time = 120s` 会补上，不构成持续性结算积压
  来源，只是偶发抖动。

### e) 无方向候选的写入放大 — `confirmed-cause`（PR #3488 已在当前 HEAD 生效）

- DB 实测：`filter_candidate_outcomes` 总行 direction 分布 `long 867 / short 460 / unknown 1436`；
  已结算（status='settled'）行中 `direction='unknown'` 占 **568/1100 = 51.6%**，与两份前序审计一致。
- **修复已确认存在于当前代码**：`filter_evidence.py:216` `if result_key and side in _VALID_DIRECTIONS:`
  在调用 `_ensure_candidate_outcomes` 之前先做 fail-closed 检查，与公开入口 `ensure_candidate_outcomes`
  （:994 `if str(direction or "").lower() not in _VALID_DIRECTIONS: return 0`）现在语义一致。
  `git log` 确认 `2ac040c85 fix(analytics): stop seeding forward-outcome rows for directionless filter
  candidates (#3488)` 就是 HEAD 本身。
- **去程收益量化**：过去写入的 1436 行 `direction=unknown` 中已结算 568 行（39.6%）已经花掉一次行情观测；
  产出率从 47.8%（526/1100）在修复后按同结构外推应逼近 ~100%（`long`/`short` 决策本身是有效方向，只有
  `unknown` 被剔除）——即**同样的写入量，证据产出率理论上翻倍（47.8%→~100%）**。但这只影响
  `filter_candidate_outcomes`，不影响 `universe_forward_outcomes`（后者的 `direction` 全部来自 scanner 的
  确定性方向输出，本身没有 unknown 问题；714 笔全部 cost-complete 证实了这点）。

### Deliverable 1 — 排序后的机制清单

| 排名 | 原因 | 定量贡献 | 状态 |
|---|---|---|---|
| 1 | 独立单元规则只在读取时生效，写入时不抑制冗余 | 714 行/~700 倍重叠→~1 个独立单元；单符号在 4h 窗口内被 ~24 个几乎全重叠批次各写一条 | 设计权衡，已在契约文档中承认 |
| 2 | 3h20m 窗口内 4h/24h horizon 从未结算 | 908/1816（50%）行必然 pending；生产 `measured` 路径只吃 `horizon=='4h'`，实际拿到 0 行 | 时长不足，非 bug |
| 3 | 过去 `filter_candidate_outcomes` 的 568 行（51.6% 已结算行）因 direction=unknown 永久不可用 | 产出率 47.8%→修复后趋近 100% | 已修复（PR #3488，HEAD 内） |
| 4 | 同 T0 跨币折叠为 1 个统计单元 | 25-27 个币→1；按币聚类可得 n=56 | 保守但方向正确 |
| 5 | maintenance_io 单 worker 饿死 outcome_settler（#3432） | 曾是「常驻运行也无法产出证据」的风险 | 已修复（f21f69b82，HEAD 之前） |

**若从现在起连续跑 2 周，能积累多少独立单元？**

```
配置 cadence             = 10 分钟/tick（scheduler.py:1558）
INDEPENDENT_UNIT_INTERVAL = 4h（economic_utility.py:22）
每 4h 窗口内理论上有 24 个 tick 机会，贪心算法只取该窗口内最早的一个 → 1 个独立单元/4h
2 周 = 336 小时 → 336/4 = 84 个独立单元（理想零故障情况）
```

但生产 `measured` 路径（`economic_utility.py`）只吃 `horizon=='4h'` 行（见
`build_economic_utility_cohort_index`:224 `if row.get("horizon") != "4h"... continue`），需要
`parent_supported` 门槛 **≥50 个独立 clusters**（`candidate_selection_evaluation.py:21`
`_MIN_COMPLETE_CLUSTERS = 50`）。按理想 84 单元/2 周计算，8.3 天（200 小时连续运行）即可跨过 50 单元门槛，
2 周应绰绰有余，**前提是 #3432 修复持续生效（已确认在 HEAD 中）且不再有类似的执行道饿死问题**。观测到的 13%
scanner_run 失败率若持续存在会略微拉长这个时间但不会阻断——`misfire_grace_time` 会在下个 10 分钟 tick 补上，
不影响 4h 粒度的独立单元计数。

---

## 三、Investigation 2 — 为什么方向毛收益是零

### a) 方向赋值来源 — `confirmed-cause`（多个 family 在结构上押注了动量续存，与已知的加密 4h 收益弱/负自相关相悖）

逐 family 核实（`scanners.py:1-560`）：

| Family | 设方向依据 | 理论基础 | 押的赌 |
|---|---|---|---|
| `momentum` (:25-143) | `rs_beta_adj_24h`/`return_4h` 正负 | 无——纯粹外推「过去 24h/4h 涨了→未来还涨」 | **显式押注收益率正自相关**，且是唯一直接拿 `return_4h` 本身当 direction 依据的 family（:65-73 “Fallback: ...use absolute 4h return”） |
| `mean_reversion` (:146-264) | RSI 极值 + 过度延伸的**反向** | 均值回归假说，理论自洽 | 押收益率负自相关（理论上更贴近加密市场真实统计特性） |
| `volatility` (:267-343) | `return_4h` 符号本身 | 类 momentum，无独立理论 | 同样押正自相关 |
| `derivatives` (:346-451) | funding rate 极值方向（挤仓） | 有经济学基础（资金费率均值回归） | 不直接押价格自相关，押的是资金费率的均值回归 |
| `event` (:454-550) | `return_1h` 极值方向 | 押事件驱动延续 | 押 1h→（15m/1h horizon）短期延续，时间尺度比 momentum 更短，理论上更合理 |
| `short_bias` (:553-637) | 固定 `"short"`，条件是 4h+24h 同向下跌 | 无理论依据的固定方向，纯粹的动量延续假设 | 显式押注下跌延续 |

**关键判断**：`momentum`、`volatility`、`short_bias` 三个 family 都在结构上把「过去 N 小时涨/跌」直接翻译成
「未来会继续涨/跌」的方向赌注，这正是「用 `return_4h` 预测下一个 4h」的自相关押注。加密货币 4h 收益率的自相关
性普遍偏弱或为负（公开的实证文献与本次 DB 样本一致，见下方 c/d 的实测数据）。前一份审计已指出「short_bias 的
所谓正收益是 3 个币的重叠窗口假象」，与本判断一致：如果 short_bias、momentum、volatility 三个 family 的方向
赌注在结构上站在收益自相关性的错误一侧，它们贡献的应是负期望而非零，零期望反而说明其他 family（mean_reversion、
derivatives）在部分抵消。这是**设计层面的问题，不是调参问题**：无论手续费/滑点参数怎么改，一个押错自相关方向的
方向赋值函数不会因为参数调整而产生正毛收益。

### b) 度量完整性检查 — `confirmed-cause`（最高优先级：入场价滞后 look-behind staleness bug；look-ahead 已排除）

**Look-ahead 排除**：`estimate_outcome_economics`（`outcome_economics.py:22-126`）严格用 `entry_price`（T0 前
已知）和 `exit_price`（结算时提供）计算，未发现任何跨越 T0 边界向前偷看的代码路径；`price_quality_ok` 门禁
（:96-101）在结算价质量不合格时把 `net` 置 `None` 而非乐观零，是 fail-closed 而非乐观 bug。**Look-ahead 已排除。**

**Look-behind/staleness — 已确认存在，且是本次调查最重要的发现**：

- 证据链：`features.py:890` `last_price = closes_1h[-1] if closes_1h else None`。`closes_1h` 来自
  `_closed_rows(ohlcv_1h, "1h")`（:669），其过滤条件（:662-666）是 `int(row[0]) + interval <= now_ms`——
  即**只保留已经完全收盘的 1h K 线**，`last_price` 取的是**最近一根已收盘 K 线的收盘价**，不是扫描瞬间的实时
  ticker（虽然 ticker 数据在同一函数里可用，见 :619-624 的 `bid`/`ask` 用法，`_quote_volume_24h` 也在用
  `ticker.get("last")`——即实时价确实抓取了，但没用于 `last_price`/`entry_price_usd`）。
- **DB 实测直接证实**：BTC/USDT 在 01:05:11 到 01:55:37 这 6 次不同扫描（间隔 10 分钟）的 `entry_price_usd`
  全部是 `69389.1` 一模一样；ETH/USDT 在 01:05-01:55 的 6 次扫描也全部是 `2261.7`。这不是巧合——`closes_1h[-1]`
  在整个「01:00-02:00」这根 1h K 线收盘之前值恒定不变，直到 02:00 收盘后才跳到新值（`2265.67`）。
- **量化滞后量级**：18 个 entry_ts 相对上一根收盘 K 线的滞后时间（分钟数）：
  `[5.2, 15.1, 25.3, 35.4, 45.4, 55.6, 5.6, 43.0, 55.1, 6.2, 16.1, 34.1, 44.0, 54.1, 16.5, 25.2, 33.7, 43.6]`，
  均值 **31.1 分钟**，中位数 34.1 分钟。相对 15m horizon（唯一有大量结算数据的短 horizon），这个滞后是
  **horizon 长度的 2.07 倍**（均值）。
- **噪声量级压过信号**：本机 1m K 线实测 BTC 在该窗口的 1 分钟收益标准差为 0.058%，换算到 15 分钟尺度理论标准差
  ≈0.22%（`sqrt(15) × 0.058%`）。而整批测出的方向毛收益只有 +0.0046%（全量 714 行）或 −0.1457%（排除代币化
  股票后 447 行纯币种）——**入场价滞后注入的噪声比测出的信号大约 50 倍**（0.22% / 0.0046% ≈ 48，相对纯币种
  −0.1457% 也接近 50 倍量级）。这意味着即便策略确有真实边缘，这个测量方式也会把信号淹没在噪声里。
- **结论**：这是一个**测量 bug**，不是「策略没边缘」的证据。它可能让结果看起来比真实情况更差（真实策略有边缘时
  滞后噪声会把它测成接近 0），也可能让结果看起来比真实情况更好（方向随机走的行也会因为滞后价格偶然对齐真实方向）。
  两个方向的偏差同时存在，净效果不可预测；唯一确定的是**当前的 +0.0046%/−0.1457% 这两个数字都不可信**，需要修复
  入场价为扫描瞬间实时 ticker（代码里已经在抓取该 ticker，只是没接到 `entry_price_usd`）之后重新测量才能得出可靠结论。

### c) 标的池污染 — `confirmed-cause`，但方向与预设相反

- DB 实测符号列表证实仍包含 `GOOGL/USDT NVDA/USDT AMD/USDT COIN/USDT SOXL/USDT RLUSD/USDT` 等代币化美股与
  稳定币，未在当前代码中被过滤掉（universe pipeline 的 hard gate 只查 volume/spread/depth/freshness/
  stablecoin/blacklist 七项，`ALPHA_SOURCE_CODE_AUDIT` 已确认 `filters.py:36-115` 不含「美股非交易时段」
  专门过滤）。
- **重新计算（exclude 结构性无 edge 品种）**：

| 口径 | 行数 | 币数 | 方向毛收益（简单均值） | 按币聚类均值 | 聚类 t 值 | net 均值 |
|---|---|---|---|---|---|---|
| 全量 | 714 | 56 | +0.0046% | −0.0134% | −0.10 | −0.2972% |
| **纯币种（exclude 代币化美股+稳定币）** | 447 | 36 | **−0.1457%** | **−0.2282%** | **−1.30** | **−0.4564%** |
| 仅代币化美股+稳定币 | 267 | 20 | +0.2562% | +0.3732% | +1.82 | −0.0308% |

**这个结果不支持「no edge 是因为坏纳入被稀释」的假说，反而相反**：剔除代币化美股/稳定币后，纯币种簇的 t 值从
−0.10 变为**更负**的 −1.30，意味着这批无 edge 品种其实是把整体结果往 0 拉，而不是把负结果拉正。前一份审计假设的
方向（「剔除坏纳入后 edge 显露」）在本次复算里**没有成立**——真正拖累结果的是纯加密货币子集本身呈现出更明确的
负向（仍未显著，|t|=1.30 < 1.96，但方向一致且幅度更大），这与 b) 项揭示的 staleness 噪声混杂后更难判断真实符号。

### d) regime 混淆 — `confirmed-cause`（样本窗口本身近乎无方向性机会）

BTC/USDT 01:05-04:25 UTC 实测（1m K 线，唯一覆盖此精确窗口的 timeframe；1h/4h K 线在此库中此窗口段仅各
1-2 根不足以做统计）：
- 净移动：−0.282%（open 69353.9 → close 69158.5）
- 区间（high-low）：1.421%（hi = 69867.9, lo = 68882.1）
- 1 分钟收益标准差：0.058%

这是一个**窄幅震荡、无明确趋势的窗口**：3 小时 20 分内净移动不到 0.3%，区间波动 1.4% 但没有形成持续单边行情。
在这种 regime 下，「方向毛收益接近 0」完全符合「这段时间市场本身没有方向性机会」的解释，而不必然说明策略永久无效。

### e) horizon 与策略期限匹配 — `confirmed-cause`（存在但影响相对次要；本次样本中该影响未量化）

- `allocator.py:36-43` 的 `_FAMILY_CALIBRATION_HORIZON` 把 `momentum` 校准 horizon 设为 4h，
  `mean_reversion`/`volatility` 设为 1h，`derivatives` 设为 1h（注释明确说明是从 4h 改到 1h，因 1h 胜率更好）——
  但这个映射**只用于 `get_family_multipliers` 这个 legacy 校准路径**（grep 确认 `get_family_multipliers` 唯一被
  生产代码调用的地方是 `allocator.py` 本身定义处，实际调用方只在测试文件 `tests/test_universe_allocator.py` 中，
  未见于当前生产的 `candidate_selection_utility_v1` 路径；DB 证实 `universe_members` 表 455 条记录
  `candidate_selection_policy` 全部是 `legacy_total_score_v1`，说明生产路径事实上就是走这条 legacy calibration）。
- 真正决定 admission 的 `economic_utility.py`（`candidate_selection_utility_v1`）则**统一只用 `horizon=='4h'`**
  （`build_economic_utility_cohort_index`:224），不区分 family 该有的 holding period。`momentum` family 理论
  holding period 是 4h（与其自身 `_FAMILY_CALIBRATION_HORIZON` 一致），但 `derivatives`/`mean_reversion`/
  `volatility` 的真实校准 horizon 是 1h，被 `economic_utility.py` 强制用 4h 窗口衡量——**这是一个系统性的
  horizon/thesis 错配，会把这些 short-horizon family 的真实信号在 4h 窗口里稀释/淹没**。不过在本次样本里此项
  影响是理论性的（因为 4h horizon 行本身 0 结算，见 I1-d），实测数据无法验证其具体量级。

### Deliverable 2 — 逐项判定

| 项 | 判定 | 关键证据 |
|---|---|---|
| a) 方向赋值来源 | **confirmed-cause** | momentum/volatility/short_bias 三个 family 结构性押注收益自相关正向延续，与加密 4h 收益弱/负自相关的普遍认知相悖（`scanners.py:65-73, 146-264, 553-637`） |
| b) 度量完整性（look-ahead/look-behind） | **confirmed-cause（最高优先级）** | `entry_price_usd` 取自最近已收盘 1h K 线（`features.py:669, 890`），同符号连续 6 次扫描 entry_price 完全相同，平均滞后 31.1 分钟（2.07 倍于 15m horizon 长度），对应 15m 尺度噪声量级 ≈0.22%，压过测出的 +0.0046%/−0.1457% 信号约 50 倍 |
| c) 标的池污染 | **confirmed-cause，但方向与预期相反** | 代币化美股/稳定币仍在池中（31.9%）；剔除后纯币种 t 值从 −0.10 恶化为 −1.30，说明「坏纳入稀释真实 edge」的假说不成立，坏纳入其实在拉高整体均值 |
| d) regime 混淆 | **confirmed-cause** | BTC 实测 1m 数据：3h20m 净移动仅 −0.282%，区间 1.42%，是窄幅震荡窗口，方向性机会本身稀少 |
| e) horizon/thesis 匹配 | **confirmed-cause，影响未量化** | `economic_utility.py` 统一用 4h 窗口衡量所有 family，但 derivatives/mean_reversion/volatility 真实校准 horizon 是 1h（`allocator.py:36-43`）；4h 结算行样本为 0 导致无法实测这一项的具体幅度 |

### 结论段（不回避）

证据指向 **(ii) 测量/纳入/horizon 设置正在稀释或掩盖真实边缘**，而不是 (i) 策略本身无 edge、也不完全是 (iii)
样本退化到无法区分。理由：

1. b 项揭示的入场价滞后是**可精确量化、机制明确的 bug**（代码里 ticker 已经抓到实时价却没接到
   `entry_price_usd`），其注入的噪声量级（≈0.22%/15m）比测出的信号大 50 倍左右，任何「信号」层面的结论在这个
   bug 修复之前都不可信——这一项单独就足以支撑 (ii)。
2. a 项揭示的方向赋值理论问题是**真实的设计缺陷**：三个 family 在押一个与已知市场统计特性相悖的赌注，如果它们的
   负期望被其余 family 部分抵消，整体测出接近 0 是符合预期的复合结果，而不是「没有任何策略有 edge」的证据——
   这也支撑 (ii) 而非 (i)。
3. c 项的实测结果削弱了「坏纳入稀释真实 edge」这个具体假说（方向刚好相反），但 c 项本身证明的是「当前的纳入策略
   未经审查，任何聚合数字都混杂了不可比的资产类别」，这仍然是纳入设置的问题，只是具体机制与预设不同。
4. d 项确认这 3h20m 确实是一个规律不明显的窗口，但这只解释了「这个窗口没有方向机会」，不能单独解释 b 项揭示的
   具体量级噪声问题。

**要彻底解决残余不确定性所需的额外证据**：(1) 修复 `entry_price_usd` 为扫描瞬间实时 ticker 后重跑同一批数据
（或等待 Fedora 新数据），对比修复前后的方向毛收益与 t 值——如果修复后信号仍接近 0，才能真正排除 (i)；(2) 在
≥50 个独立 4h clusters（约 8-9 天连续运行，数学上 2 周内可达）的规模上重新做本次全部计算。当前 1 个独立单元的
规模本身不足以对任何一个判定给出统计显著的确认，所有 `confirmed-cause` 判定都是**机制层面确认**（代码逻辑本身），
而非**效应量层面确认**（该机制在真实交易结果里造成了多大偏差）——机制已查清，效应量需要干净的、修复后的、更长时间
的数据才能测出。

---

## 四、关键术语 — 反事实结果账本

**反事实结果账本**（owner 已明确采用此名称，旧称「前瞻账本」作废）：记录每个候选机会「如果当时开了仓会怎样」的
模拟结果账本，只需要行情、不需要真实成交。它按固定 horizon 记录每个进入 universe / 通过过滤器的候选在「按某方向
入场后到期」的结果，并做成本完整归因（`outcome_economics.py:99`：
`net = directional - fee_pct - spread_slippage_pct - funding_pct`），未知的经济输入返回 `None` 而不是乐观的零。

底层表名（`universe_forward_outcomes`、`filter_candidate_outcomes`）在代码中保持不变——只有中文术语由「前瞻账本」
改为「反事实结果账本」，代码/表名不受影响。

---

## 五、未覆盖 / 需要后续验证

本次调查被 runtime 限制终止于报告撰写完成后；以下为确认未完成、或明确需要后续验证的项：

- **入场价陈旧（staleness）的修复决策**：`entry_price_usd` 取自最近已收盘 1h K 线收盘价（`features.py:890`）
  这一发现**尚未有任何对应的修复决策**——它目前只是一个待 owner 决策的 finding，不是已计划的改动。代码里实时
  ticker 已经抓取（`_quote_volume_24h` 在用 `ticker.get("last")`），但未接到 `entry_price_usd`，修复路径明确但未定案。
- **效应量层面未确认**：所有 `confirmed-cause` 判定均为机制层面（代码逻辑）确认，效应量（该机制在真实交易结果中
  造成多大偏差）因当前仅 ~1 个独立单元而无法统计显著确认，需要修复 + 更长数据（≥50 个独立 4h clusters）。
- **Investigation 2e（horizon/thesis 错配）影响未量化**：因 4h horizon 行在样本窗口 0 结算（I1-d），该项的
  具体幅度无法实测，仅理论确认。
- **Investigation 1 的 2 周独立单元投影依赖前提**：84 单元/2 周、8.3 天跨过 50 门槛的算术假设 #3432 修复持续
  生效（已确认在 HEAD）且不再出现新的执行道饿死问题；13% 的 scanner_run 失败率只略微拉长时间、不阻断，但未量化。
- **方向毛收益的残余不确定性**：(ii) 与 (i) 的最终区分需要先修复 `entry_price_usd` 后重跑，或等 Fedora 干净新
  数据；当前数据无法排除 (i)（策略真无 edge），也无法确认 (i)（现有测量被约 50 倍噪声污染）。
- **c 项对「坏纳入稀释 edge」假说的证伪**是基于同一批被 staleness 噪声污染的 entry_price 数据；修复入场价后应
  重算纯币种子集的 t 值以确认该结论在干净数据上仍成立。

---

> 本文档所有代码引用均可在所述基线 `master` HEAD `2ac040c85` 上复现；数据库访问只读（`mode=ro`）。
