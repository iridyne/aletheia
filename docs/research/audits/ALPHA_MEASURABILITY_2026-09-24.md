# Alpha 可度量性核查 — 2026-09-24

目的：在继续修 bug 之前，回答「我们现在到底有没有能力度量 Alpha」。
框架本身不产生收益，收益来自理论上存在的 Alpha。本文只问一件事：现有代码和数据能不能
判断 Alpha 是否存在。

**数据来源与其边界（先说清楚，避免结论被误用）**

| 项 | 值 |
|---|---|
| 数据库 | `/Users/yixian/.aletheia/app.db`（本机开发库，45 MB，mtime 2026-09-23） |
| 访问方式 | `sqlite3` URI `mode=ro` 只读，未写入 |
| **这不是 Fedora 生产库** | macOS 是开发/控制机。Fedora 的数字可能不同 |
| 样本时间跨度 | 2026-08-20 01:05 → 04:25 UTC，共 **3 小时 20 分**，16 个扫描批次，56 个币 |
| 真实成交 | `positions` / `orders` / `fills` **均为 0 行** |

最后一行很重要：**本机从未走完过一次真实交易链条**。所以下面所有数字都不来自真实成交，
而来自「前瞻结果账本」——一条不需要交易所的平行度量路径。

---

## 一、结论速览

| 问题 | 答案 |
|---|---|
| 1. 有多少笔成本完整的 Outcome？ | **714 + 526 = 1240 笔**，但全部来自前瞻账本，非真实成交；且集中在 3.3 小时内 |
| 2. 净收益（扣手续费/资金费/滑点）是正还是负？ | **明确为负**：均值 −0.297%/笔，按币聚类 t = −2.59 |
| 3. 归因能否分开「策略没边缘」与「成本吃掉边缘」？ | **能，且答案是前者**：方向毛收益 +0.0046%，聚类 t = −0.10，与 0 不可区分 |

一句话：**度量能力是有的，而且已经在出结论；结论是当前没有可观测的毛边缘。**
但样本只有 3.3 小时，所以这个结论只能证明「机器能转」，不能证明「策略长期无效」。

---

## 二、度量链路长什么样（这是好消息）

Alpha 度量**不走真实成交**，走两张前瞻账本表：

| 表 | 行数 | 语义 |
|---|---|---|
| `universe_forward_outcomes` | 1816（714 已结算） | 每个进入 universe 的候选，按固定 horizon 记录「如果当时按这个方向开，到期会怎样」 |
| `filter_candidate_outcomes` | 2763（1100 已结算） | 每个通过过滤器的候选，同上，另带 RSI / 基准队列等特征 |

两张表都做**成本完整归因**，字段直接落库：

```
core/aletheia/universe/outcome_economics.py:99
net = directional - fee_pct - spread_slippage_pct - funding_pct
```

- `directional_return_pct` — 方向调整后的毛收益（真正的边缘指标）
- `estimated_fee_pct` — 双边手续费
- `estimated_spread_slippage_pct` — `(spread_bps + 2×slippage_bps) / 100`
- `estimated_funding_pct` — 按持仓时长折算的资金费，多头付空头收
- `net_return_pct` — 以上四项的差

**关键设计优点**：`net` 在方向未知、永续无点对点资金费率、或结算价质量不合格时
**返回 None 而不是 0**（`outcome_economics.py:42` 明确写了「未知的经济输入不得变成乐观的零」）。
这意味着能算出 `net` 的行，其成本一定是完整的——没有偷偷把未知当免费。

**这条链路完全不依赖 Bitget。** 记忆里「Fedora 从未接通 Bitget、847 单全是 paper_internal」
所以无法度量 Alpha 的推论，在前瞻账本这条路径上**不成立**。前瞻账本只需要行情，不需要成交。

---

## 三、问题 1：有多少笔成本完整的 Outcome

| 表 | 总行 | 已结算 | 有完整 `net_return_pct` | 产出率 |
|---|---|---|---|---|
| `universe_forward_outcomes` | 1816 | 714 | **714** | 结算后 100% |
| `filter_candidate_outcomes` | 2763 | 1100 | **526** | 结算后 47.8% |

两条不同的漏损：

**(a) `universe_forward_outcomes`：1102 行未结算（61%）——不是 bug**
未结算的按 horizon 分布是 15m×48 / 1h×146 / 4h×454 / 24h×454，`due_at` 分别落在
04:48、05:43、08:43、次日 04:43。最后一条入库时间是 04:25。就是进程在 04:43 停了，
长 horizon 还没到期。本机是开发机，停了很正常。**Fedora 上如果常驻运行，这部分会自然收敛。**

**(b) `filter_candidate_outcomes`：568/1100 已结算行永久不可用（51.6%）——这是真问题**

```
settled = 1100
  缺 directional_return_pct = 568   ← direction 字段是 'unknown'
  有 directional 但缺 funding  =   4
  可用 net                    = 526
```

568 行全部是 `direction='unknown'`（491 perp + 77 spot）。没有方向就算不出方向收益，
算不出方向收益就没有 `net`。这些行**永远不可能**产出证据，但每一行都已经：
- 占了一行存储
- 花掉一次结算时的行情观测（1436 个 unknown 行里 568 个已经取到了 `exit_price_usd`）

---

## 四、这 52% 无方向行的根因（已定位，且不是我最初以为的那个）

先说我查错的一步，免得后人重走：我一开始以为是 upsert 覆盖 bug——
`insert_filter_candidate_outcomes_batch` 的 `ON CONFLICT` 只更新 `allocation_selected`，
不更新 `direction`（`repositories.py:9843`），所以怀疑先写入的无方向阶段把方向锁死了。
**证伪了**：查 `filter_decisions`，这 1436 行对应的 359 个 key 里，能找到 long/short 记录的是
**0 个**。方向不是被覆盖丢掉的，是从来没有过。

真正的来源：

```
filter_decisions 的 direction 分布:  long 660 / None 360 / short 41
None 的 360 条全部是:  stage='signal_emitter', filter_name='opportunity_emission'
```

360 条无方向决策 × 4 个 horizon = 1440 ≈ 观测到的 1436 行。

而无方向本身是**故意的 fail-closed 语义**，不是缺陷：

```python
# core/aletheia/universe/scanners.py:246
# Fail closed: any qualifying factor opposing the chosen direction → directionless.
if direction is None or opposing:
    return 0.0, [], None
```

```python
# core/aletheia/universe/scanners.py:676
# ...the emitted candidate is directionless and cannot be chased.
```

扫描器发现有因子对冲掉了方向，或者 15m K 线已经在提议方向上走了 >1.5 ATR（追高纪律），
就**故意**不给方向。这是对的。

**缺陷在于：明知无方向，还是给它开了一张前瞻结果行。**

同一个文件里的两个入口对这件事的策略是相反的：

```python
# core/aletheia/analytics/filter_evidence.py:991  —— 公开入口，正确
def ensure_candidate_outcomes(...):
    """Public registration seam for non-filter prospective control cohorts."""
    if str(direction or "").lower() not in _VALID_DIRECTIONS:
        return 0          # ← 无方向就不建行
```

```python
# core/aletheia/analytics/filter_evidence.py:220  —— 过滤器证据路径，制造了这 1436 行
_ensure_candidate_outcomes(
    ...
    direction=side if side in _VALID_DIRECTIONS else "unknown",   # ← 无方向就盖 "unknown" 继续建
)
```

公开入口拒绝制造不可度量的证据行；过滤器证据路径把它造出来，占了这张表的 52%。

并且没有任何消费者需要这些行——grep 全库，没有一处查询 `direction='unknown'`，
而多处查询显式排除它（如 `agent_shell/trading_context.py:59` 的
`AND direction IN ('long','short')`）。

---

## 五、问题 2：净收益是正还是负

`universe_forward_outcomes`，714 笔成本完整样本：

```
方向毛收益 directional = +0.0046%
− 手续费 fee           =  0.1200%
− 点差+滑点 slippage   =  0.1824%
± 资金费 funding       = −0.0006%
─────────────────────────────────
= 净收益 net           = −0.2972%      胜率 29.7%
```

`filter_candidate_outcomes`，526 笔：

```
directional = +0.0478%   fee 0.1200%   slip 0.1632%   funding −0.0005%
net = −0.2348%           胜率 35.7%
```

成本参数对得上配置（`config/schema.py:1566`）：
`execution_fee_pct` 默认 0.06%/边 → 双边 0.12%（Bitget taker 实价），
`slippage_bps` 默认 5.0 → 双边 10 bps，加上点差得到 0.1824%。参数是合理的，不是被拍高的。

**显著性**：不能按 714 独立样本算。整批只有 3h20m、16 个批次、56 个币，而且同一个币
在相邻 10 分钟内被反复记录 1h horizon（窗口大量重叠）。代码本身已经意识到这个问题——
`universe/economic_utility.py:23` 定义了
`INDEPENDENT_UNIT_VERSION = "scanner-t0-cross-section-nonoverlap-4h/v1"`，
即「4 小时不重叠的 T0 横截面」才算一个独立单元。按这个口径，**这整批只构成约 1 个独立单元**。

退一步按币聚类（56 个簇）：

| 指标 | 朴素（假装 714 独立） | 按币聚类（n=56） |
|---|---|---|
| net 均值 | −0.2972% | −0.3640% |
| net t 值 | −6.79 | **−2.59** |
| 为正的簇 | — | 14/56 |

净收益为负，即使在保守的聚类口径下仍然显著。

---

## 六、问题 3：归因能否分开「没边缘」和「成本吃掉边缘」

**能分开，而且答案很干净。**

这是两种完全不同的病，修法相反：
- 「成本吃掉边缘」= 毛收益显著为正但小于成本 → 该修执行（降滑点、改挂单、压手续费）
- 「策略没边缘」= 毛收益本身就是 0 → 改执行一点用没有，因为没有东西可保护

观测结果：

```
方向毛收益 directional，按币聚类:  均值 −0.0134%   sd 1.041   t = −0.10
成本地板:                          0.1200% + 0.1824% = 0.3024% / 往返
毛边缘 / 总成本 =                  0.015        （要盈利需 > 1）
```

**t = −0.10。毛边缘与 0 在统计上不可区分。** 这是「策略没边缘」，不是「成本吃掉边缘」。
毛边缘离成本地板差 **65 倍**（0.0046% vs 0.3024%）。

换句话说：即使把手续费和滑点砍到 0，这批样本也不赚钱——因为方向本身没有预测力。

### 按策略族拆开

| family | horizon | n | 方向毛% | 成本% | 净% | 胜率 |
|---|---|---|---|---|---|---|
| short_bias | 1h | 12 | +1.8948 | 0.3594 | **+1.5368** | 83.3% |
| short_bias | 15m | 16 | +0.7335 | 0.3279 | **+0.4059** | 62.5% |
| volatility | 1h | 21 | +0.4903 | 0.3548 | **+0.1341** | 61.9% |
| mean_reversion | 1h | 48 | +0.1336 | 0.2665 | −0.1301 | 29.2% |
| mean_reversion | 15m | 64 | +0.0954 | 0.2632 | −0.1671 | 39.1% |
| momentum | 1h | 144 | +0.0176 | 0.2887 | −0.2714 | 26.4% |
| derivatives | 15m | 111 | +0.0601 | 0.3341 | −0.2723 | 19.8% |
| momentum | 15m | 192 | −0.1063 | 0.2774 | −0.3838 | 25.0% |
| derivatives | 1h | 83 | −0.2491 | 0.3589 | −0.6014 | 28.9% |
| volatility | 15m | 23 | −0.9615 | 0.3478 | −1.3096 | 34.8% |

**不要拿 short_bias 那两行当好消息。** 拆开看 1h 那 12 笔：

```
PRL/USDT  01:05  +4.63%      ← 同一个币
PRL/USDT  01:15  +3.78%      ← 相隔 10 分钟的 1h horizon，测的是同一段行情
PRL/USDT  01:25  +3.47%
PRL/USDT  01:35  +3.63%
CFG/USDT  01:45  −0.15%
CFG/USDT  01:55  −0.30%
AKE/USDT  02:05 ~ 03:34  ×6
```

12 笔来自 **3 个币**，PRL 一次上涨被重复计入 4 次。这正是记忆里已经记过一次的陷阱
（`project_counterfactual_ope_findings_2026-07-31`：「naive +0.133% 是假象（选择偏差）」，
同一份记忆里 short_bias 也是唯一为正的族）。**同一个假象在两次独立度量中重现了。**

真实含义只是：这 3.3 小时里有 3 个小币下跌，short_bias 抓到了。不是边缘，是一次行情。

### 顺带排除一个假 bug

`min_tradable_edge_pct` 全表恒为 0.05%，而成本地板是 0.3024%，相差 6 倍——看起来像门槛设错。
查了消费方，全部是 `net_return_pct > min_tradable_edge_pct`（`repositories.py:10604`、
`10768`、`10862`、`trading_context.py:52`、`audit_cmd.py:111`）。比较的是**已扣完成本的 net**
和 0.05%，语义是「净利要超过 5bp 才算赢，避免把噪声记成胜」。**不是重复扣成本，不是 bug。**

---

## 七、这对后续方向意味着什么

### 已经确认可用的能力（不用再建）

1. 成本完整的前瞻结果账本，`net` 拒绝把未知当零 —— 能用
2. 三项成本分离归因（fee / spread+slip / funding）—— 能用，且能回答问题 3
3. 独立单元定义（`nonoverlap-4h/v1`）—— 定义在，**但上面这批数据的聚合没走它**
4. 读取面：`aletheia inspect pnl`、`/api/analytics/*`、`audit_cmd` 校准统计 —— 在

**所以不需要为「度量 Alpha」再建框架。** 度量能力已经具备。

### 真正的约束是样本，不是框架

3 小时 20 分、1 个独立单元，决定不了任何事。要判断 Alpha 存在与否，需要的是
**让前瞻账本连续累积数周**，不是再写一层抽象。

这反过来确认了 #3432 确实在关键路径上：`universe_refresh` 和 `outcome_settler` 共用
`maintenance_io` 这条只有 1 个 worker 的执行道，一个卡死会饿死另一个——
而 `outcome_settler` 正是把 pending 行变成证据的那一步。修它不是「修一个 High 回归」，
是「保证证据能持续产出」。

### 按收益排序的下一步

| # | 动作 | 为什么值得做 | 成本 |
|---|---|---|---|
| 1 | 让 Fedora 的前瞻账本连续跑 ≥ 2 周，按 `nonoverlap-4h/v1` 口径聚合 | 这是唯一能回答「Alpha 是否存在」的办法。当前 1 个独立单元 → 目标 ≥ 80 个 | 只需常驻运行 + 一个聚合查询 |
| 2 | 无方向候选不再 seed 前瞻结果行 | 证据产出率 47.8% → ~95%，等于把可用样本翻倍；顺带省掉 568 次无意义行情观测 | `filter_evidence.py:220` 一个条件，照抄同文件 `:991` 已有的写法 |
| 3 | 聚合口径默认用独立单元，不用行数 | 朴素 t=−6.79 vs 聚类 t=−2.59，口径不同差 2.6 倍；错的口径会让人以为样本够了 | 改聚合查询 |
| 4 | 把「毛边缘 vs 成本地板」做成常驻报表 | 这是唯一能区分「改策略」还是「改执行」的指标。现在要手写 SQL 才看得到 | 一个视图 + 一个读取面 |

第 2 项是纯删减、有现成对照写法、影响面清晰，符合「删除优于新增」。
第 1 项不是代码工作，是运行纪律。

### 不该做的

- 不要因为「净收益为负」去调滑点/手续费参数。参数是对的（0.06%/边就是 Bitget taker 实价），
  而且毛边缘是 0，压成本救不回来。
- 不要拿 short_bias 去加仓或加权。它是 3 个币的重叠窗口，记忆里已经栽过一次。
- 不要在样本 = 1 个独立单元的前提下判定任何策略族「无效」并删掉它。现在的数据既不能证实
  也不能证伪，唯一诚实的说法是「还没有证据」。

---

## 八、本文没有验证的事

- **Fedora 生产库的实际数字**。本文全部来自本机开发库。Fedora 上的行数、时间跨度、
  `direction='unknown'` 占比都可能不同。结论里「度量链路可用」「归因能分开两种病」是代码性质，
  跨机器成立；具体数值必须在 Fedora 上重跑。
- **真实成交的 Outcome**。本机 `positions`/`orders`/`fills` 全 0。前瞻账本的 net 是模型估算的
  成本，不是真实成交的滑点。两者的差距（模型滑点 10bps vs 真实滑点）只有真跑起来才知道。
- **`filter_candidate_outcomes` 的 1663 行 pending** 未逐条核对到期状态，只核了
  `universe_forward_outcomes` 的 1102 行。
- **第 2 项改动的回归面**。已 grep 确认无消费者依赖 `direction='unknown'`，但未跑测试套件。

复现命令（只读）：

```bash
python3 - <<'EOF'
import sqlite3, statistics as st, math, collections
db = sqlite3.connect("file:/Users/yixian/.aletheia/app.db?mode=ro", uri=True)
rows = db.execute("""SELECT symbol, directional_return_pct, net_return_pct
                     FROM universe_forward_outcomes WHERE net_return_pct IS NOT NULL""").fetchall()
by = collections.defaultdict(list)
for s, d, n in rows: by[s].append((d, n))
dm = [st.mean([x[0] for x in v]) for v in by.values()]
nm = [st.mean([x[1] for x in v]) for v in by.values()]
for name, m in (("directional", dm), ("net", nm)):
    t = st.mean(m) / (st.stdev(m) / math.sqrt(len(m)))
    print(f"{name}: clusters={len(m)} mean={st.mean(m):+.4f}% t={t:.2f}")
EOF
```
