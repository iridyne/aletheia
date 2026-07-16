# Round 1 判决预览 + 路径推演

> **本文件作用**:把 Round 1 (2026-06-22 → 2026-07-06) 进行中的判决相关数据
> 固化为决策依据,**预承诺判决思路**,防止数据继续累积后判决被"我们已经投入
> 太多了"的沉默成本扭曲。
>
> **创建时间**:2026-06-24 (T+2.6 天,剩 11.4 天)
> **协议依据**:`docs/product/experiments/EXPERIMENT_PROTOCOL.md` (3 轮 × 14 天 + G1-G6 + K1-K4)
> **关联文档**:`frozen_optimization_backlog.md` §E2 (本轮 = Kill 路径推演的具体方案)
> **本文修订**:2026-06-24 第二次写入 — 第一版数据基础错误,见 §6 勘误。

---

## 1. 当前数据快照(T+2.6 天,亲核)

### 1A. 原始口径(所有 close_reason)

| 指标 | 实测值 | Go 阈值 | Kill 阈值 | 状态 |
|---|---|---|---|---|
| **样本量** (G6) | **17** closed trades | ≥ 60 | — | **未达** (~28%) |
| **全品种 PF** (G1/K1) | **0.323** | ≥ 1.2 | < 0.85 | ✗ 触 K1(但**含污染数据**) |
| **WR** | **41.2%** (7/17) | — | — | 偏弱 |
| **平均 win** | **+1.264** USDT | — | — | — |
| **平均 loss** | **-2.742** USDT | — | — | **被 -23.26 单笔拉偏** |
| **期望值** | **-1.09** USDT/trade | > 0 | — | 触 K-equivalent |
| **回撤** (K4) | max_dd_abs=$27.14, 占 capital=**0.031%** | — | > 25% | 远未触 K4 |
| **ECE** (G4/K3) | **无 calibration report** | < 0.15 | > 0.25 | 不可判 |

### 1B. ⚠️ 严格口径(剔除 `units_bug_cleanup` 与 `reconciled_gone`)

| 指标 | 严格值 | Go 阈值 | Kill 阈值 | 状态 |
|---|---|---|---|---|
| **样本量** | **1** closed (stop_loss only) | ≥ 60 | — | **极度不足** |
| **全品种 PF** | **0** (1 笔亏损 GRAM short -0.51) | ≥ 1.2 | < 0.85 | **不可判** |
| **WR** | 0% (0/1) | — | — | **不可判** |

### 1C. 半严格口径(只剔除 `units_bug_cleanup` 历史清账,保留 `reconciled_gone`)

| 指标 | 半严格值 | 说明 |
|---|---|---|
| **样本量** | **14** | 13 笔 reconciled_gone + 1 笔 stop_loss |
| **全品种 PF** | **2.19** | GP=8.63 / GL=3.94 — **远超 G2=1.5** |
| **WR** | **42.9%** (6/14) | — |
| **总净利** | **+4.69** USDT | — |

### 1D. 数据完整性诊断 — 三个口径差异的根因

| close_reason | 笔数 | 净 PnL | 含义 |
|---|---|---|---|
| `units_bug_cleanup` | 3 | **-23.26** | 6/23 清 OKX contractSize bug 的历史持仓 — **与 Round 1 AI 决策无关**,但发生在窗口内 |
| `reconciled_gone` | 13 | **+5.20** | 本地有持仓但交易所端没了,reconciler 强平。12/13 用了真实 current_price(非 fallback),**可能**是 TPSL 触发或 reconciler 周期 catch 的真实平仓,**但代码里看不出是哪种**(`position_reconciler.py:435` 试图从交易所拿 tpsl_price,拿不到才 fallback) |
| `stop_loss` | 1 | -0.51 | **唯一可确认为 AI 触发的真实平仓**(GRAM short) |

**关键不确定性**:13 笔 `reconciled_gone` 是 (a) TPSL 真实触发(算 AI 决策结果)还是 (b) reconciler 在轮询时发现 orphan 后批量强平(不算 AI 决策结果)。**24h 内有 3 波 batch close**(15:32×3、04:14-19×5、12:07×5),**同秒批量** = 强模式 → 倾向 (b) 但无 100% 确认证据。

**速率推算**(11.4 天剩余,基于 1A 原始口径):
- 当前已 closed 17 / 2.6d = **6.5 trades/day**
- 推算 14 天总样本 ≈ **91 笔**(超 G6=60 笔门槛 51%)
- → 原始口径**样本量到期会满足 G6**;但**严格/半严格口径都不会**(stop_loss 速率 ~0.4/天)

---

## 2. 判决路径推演(基于当前数据,口径敏感)

### 2A. 三种口径下的判决倾向矩阵

| 口径 | PF | 判决倾向 | 论证 |
|---|---|---|---|
| 1A 原始(含 units_bug) | 0.32 | **倾向 Kill** | -23.26 USDT 那笔历史 bug 清账把分母 GL 拉到 27.42;但**不属于 Round 1 AI 决策** |
| 1B 严格(只 stop_loss) | n/a | **不可判** | n=1 远不到任何 G/K 阈值的样本量要求 |
| 1C 半严格(剔除 units_bug) | **2.19** | **倾向 Go (G2)** | GP=8.63/GL=3.94;若 14 天剩余按当前 closing 速率续产,样本量可能够 G6 |

**关键洞察**:
- **第一版判决预览(本会话上午)的 "PF 永远回不到 0.85" 推演 = 基于 1A 数据的算术错误**:
  把 -23.26 的历史 bug 清账算进 Round 1 → 触 K1 倾向。
  剔除后真实分母 GL=3.94,PF 跳到 2.19,**反向触 G2**。
- **真正的判决悬念在 reconciled_gone 这 13 笔到底算不算**(见 §1D):
  - 算 → 1C 口径 → 倾向 Go
  - 不算 → 1B 口径 → 样本不足,Grey 进 Round 2

### 2B. 7/6 前应优先解答的事

**判决前最重要的事**不是预测 PF 走向,而是**搞清 reconciled_gone 的本质**:

1. **核 `position_reconciler.py:435 _try_get_tpsl_fill_price()`** 的成功率
   — 若大部分 reconciled_gone 真拿到 TPSL 价(算真实平仓),1C 口径成立
   — 若大部分用 fallback(本会话实测 12/13 用 current_price 非 entry,但 current_price 是 reconciler 跑那一刻的 mark,不等于真实成交)→ 数据严重失真

2. **检查 reconciler 跑的频率与 trigger 条件** — 同秒批量 close 是 reconciler 周期到了正常清,还是某个 race condition 触发
3. 这些都**不动 reconciler 代码**(冻结边界附近),只是读源码 + 看日志查清楚机制

### 2C. Round 1 结束后判决预期

**修订后**:不再单方向预测 "Kill"。Round 1 结束当天必须用 §3 的"双口径 Checklist"判决,**判决倾向取决于 7/6 时 reconciled_gone 在样本中的占比 + 它们的本质**。

按 `frozen_optimization_backlog.md` 已固化的 Round 2 修复清单 (L-Bug-A / A' / B / S-Bug-C),
按 `frozen_optimization_backlog.md` 已固化的 Round 2 修复清单 (L-Bug-A / A' / B / S-Bug-C),
**最高杠杆的改动 = L-Bug-A + L-Bug-A' 一起**(把 PI 从"失忆"变"有记忆"):

| Round 2 改动候选 | 杠杆推断 |
|---|---|
| L-Bug-A + L-Bug-A' (lessons + guards 接入) | **高**(改 PI 输入) |
| L-Bug-B (ps_lessons 接入) | 中(补充信号) |
| S-Bug-C (strategy KPIs JOIN) | 低(让 PI 看见数字,但不一定改决策) |
| OKX 51155 compliance 处理 | 中(若 demo≠live,影响判决有效性) |

**协议第 110 行**:Round N→N+1 允许"恰好一个"改动 → 把 A + A' 视为同一函数同一次提交,
**捆绑作为 Round 2 的单一改动**。这样不会消耗 Round 3 的改动配额。

---

## 3. 预承诺的判决思路(防事后扭曲)

> **写在 7/6 数据来之前。Round 1 结束当天,按此清单照表判决,不重新论证。**

### 判决 Checklist(7/6 23:59 UTC 执行 — 双口径并跑)

**核心原则**:**两个口径都跑一遍**(1A 原始 + 1C 半严格,剔除 `units_bug_cleanup`),
对比看判决是否在边界。如果两个口径判决相同 → 直接照判;若不同 → 用 §3-edge 规则裁决。

```sql
-- 1A 原始
SELECT COUNT(*), SUM(realized_pnl), ... FROM positions
WHERE status='closed' AND closed_at >= '2026-06-22';

-- 1C 半严格(剔除历史 bug 清账,保留 reconciler 行为)
SELECT COUNT(*), SUM(realized_pnl), ... FROM positions
WHERE status='closed' AND closed_at >= '2026-06-22'
  AND close_reason != 'units_bug_cleanup';

-- 1B 严格(只算 AI 主动平仓)— 仅参考,不作判决
SELECT COUNT(*), ... FROM positions
WHERE status='closed' AND closed_at >= '2026-06-22'
  AND close_reason IN ('stop_loss', 'take_profit', 'pi_close', 'manual_close');
```

**对每个口径执行**:

1. **核样本量 G6** — n >= 60 ?
   - 若 1A < 60 但 1C >= 60 → 用 1C 判(units_bug 不应阻止判决)
   - 若 1C < 60 → 触 Grey 自动进 Round 2(不延期)
2. **核 K1** — 全品种 PF < 0.85 ?(分口径)
3. **核 K2** — 最优子集(symbol × direction, n>=30)PF < 1.0 ?
4. **核 K3** — calibration_report.ece > 0.25 ?
5. **核 K4** — max_dd / starting_capital > 25% ?
6. **核 G1/G2/G3** — 全品种 PF >= 1.2 / 最优子集 PF >= 1.5 / 期望值 > 0
7. **集成裁决**:
   - 若 K 触发但 G 也满足 → 协议没明说,**预承诺裁:Kill 优先**(保守)
   - 若都不触发 → Grey 进 Round 2

### §3-edge 规则(双口径判决冲突时)

如果 1A 触 K 而 1C 触 G(本会话即此情况)→ **以 1C 为准**,理由:
units_bug_cleanup 是冻结期之前的 bug 清算,**不是 AI 决策表现**,
保留它在原始口径只是为了完整性记录,**不应在裁决时左右方向**。

如果 1C 触 K 而 1A 触 G → 不可能(剔除负数会让 PF 更差,只会更倾 Kill,
不会从 Kill 翻 Go)。

如果两口径都 Grey → 直接 Grey,进 Round 2。

### 不可做的事(违反预承诺)

1. **不允许在 7/6 当天看到数据后,把"差一点点"判 Grey 拉成 Go**(协议第 105 行)
2. **不允许"再观察几天"延长 Round 1**(那是移动球门,实验破产)
3. **不允许 Round 2 同时改 2+ 个东西**(协议第 110 行严格"恰好一个")
4. **冻结期判决前不允许改 G1-G6 / K1-K4 阈值**(协议第 105 行)

### 终极 Kill 距离

- 当前是 Round 1。最坏情况(连续 3 轮 = Kill 或 Grey)= 整个 AI 相机交易方向证伪
- 距离终极 Kill 还有 2 轮(R2 + R3)缓冲
- → 不必为 R1 的 Kill 倾向焦虑;每轮一次改动,共 2 次实质性翻盘机会

---

## 4. 现在可做的事(不污染冻结 + 不移动球门)

### A. 监测自动化(已落地于 master 318053f 之前的 307584a)

- `loop.round1` doctor probe — 已实测有效,会自动报"BEHIND G6"或"PF below break-even"
- `/api/analytics/round1-progress` endpoint — 同上数据源
- 已建议每天 `aletheia doctor` 扫一眼

### B. 7/6 判决前应每天监测的指标(给 watchdog 加)

watchdog 现在已有,可以让它在每次巡检顺便打印 round1 状态。但不要把这当成
判决依据——预承诺 Checklist 才是。每日监测只是早期预警(若数据走向已经清楚
偏向 Kill,Round 2 准备工作可以提前启动)。

### C. Round 2 准备工作(可现在做,完全 safe)

- `frozen_optimization_backlog.md` 已固化所有冻结期不可做的 fix
- `round2_week1_decision_chain_fixes.md` 已固化具体修复路径 + RED spec 测试
- 7/6 判决一旦 Kill,**当天即可启动 Round 2 第一周修复**,无需重新分析

---

## 5. 反思 — 为什么写这份文件

(1) **Opus 4.7 初次分析的错误**:我把 elapsed days 从 2.6 误读为 7,推断"快到窗口结束、要提前 Kill"。亲核
   datetime 后真相是才过 2.6 天,还剩 11.4 天。**判决备忘的措辞从"立即 Kill"改为"路径推演 + 预承诺"**。

(2) **预承诺机制的本质**:Round 1 真实数据(PF=0.32, WR=41%, 期望值 -$1.09/trade)说服力极强,**判决导向 Kill 的概率很高**。但**不能现在就 Kill**——这违反协议"3 轮 × 14 天"的固定窗口设计。把判决思路、Checklist、阈值在数据来之前固化,**Round 1 结束当天照表施工**,杜绝"再观察几天"或"差一点点判 Go"的扭曲。

(3) **不是给 Kill 找理由**:写这份不等于已经判 Kill。完全有可能剩余 11 天市场反转、WR 跳到 55%+
   翻盘。但**预承诺让 Kill 的合理执行不被沉默成本绑架**——若 7/6 数据触 K1 而上下文是"已投入 3 周",
   人会本能想"再多看看"。预承诺破除这个本能。

(4) **关联**:本文件 §3 的预承诺与 `EXPERIMENT_PROTOCOL.md` 第 102 行"这是预承诺。写在数据来之前。
   不允许事后修改阈值"完全对齐——只是把判决执行流程具体化到 Checklist 形式。

---

## 6. 勘误(2026-06-24 第二次修订)

第一版(commit 4c324d9)有**严重数据基础错误**,本次修订前必须列出来,
让读这份文件的人(或未来的我)能识别第一版叙事的污染。

### 6.1 第一版错误

| 区域 | 第一版叙事 | 实际亲核(修订版) |
|---|---|---|
| §1 PF | "PF=0.323 → 触 K1" 单口径 | 1A 0.32 触 K1 / 1C 2.19 触 G2 / 1B 不可判 — 三口径分歧极大 |
| §1 平均 loss | "-2.742 USDT" | 含 -23.26 单笔 units_bug_cleanup;真实 AI 决策亏损平均 ≈ -0.36 |
| §2A 翻盘推演 | "PF 永远回不到 0.85" | 剔除 units_bug 后 PF=2.19,已**超过 G2 (1.5)**,推演反向 |
| §3 Checklist | 单口径 SELECT | 必须双口径并跑(1A + 1C),设 edge 规则裁决冲突 |
| Round 2 改动建议 | 建议捆绑 L-Bug-A + L-Bug-A' | **暂搁** — 真实根因还没搞清,可能不是 Learning 而是 reconciler/对账层 |

### 6.2 第一版错误的根因

(a) 没核 `close_reason`,把 17 笔 closed 当成 17 笔有效 AI 决策来算 PF。
(b) 没识别 6/23 凌晨那 3 笔 -23.26 USDT 是 contractSize bug 修复前的清账,
    不是 Round 1 范围内 AI 操作的结果。
(c) 推 "PF 永远回不到 0.85" 用了**未剔除 GL** 的分母,数学正确但前提错。

### 6.3 教训(写进 memory 候选)

任何"用 PF / WR / ECE 做判决"的文档,**先看 close_reason 分布**,
然后**至少分原始 / 半严格 / 严格三口径**。单一数字判决 = 容易被
"对账失败 / bug 清账 / 未平仓持仓被强平" 这类系统性事件污染。
本会话 code-review 自查发现,纠正 commit 在 §6 之后。

### 6.4 修订后再次实测(T+2.7 天,数据已乐观反转)

本会话写 §6 期间又过了一小时,期间又新增了 8 笔 closed,数据画像发生**显著反转**。
当前 (T+2.7d) 25 笔 closed 的 close_reason 分布:

| close_reason | 笔数 | 净 PnL | WR | 含义 |
|---|---|---|---|---|
| `reconciled_gone` | 13 | +5.20 | 6/13 = 46% | 对账强平 |
| **`take_profit`** | **7** | **+38.55** | **7/7 = 100%** | **AI 真实止盈** |
| `units_bug_cleanup` | 3 | -23.26 | n/a | 历史 bug 清账 |
| `stop_loss` | 2 | -0.97 | 0/2 | AI 止损 |

**这下三口径都倾向 Go**:
- **1A 原始**: n=25, PF≈1.7, WR=56% → **触 G1+G2**(且 raw 不再触 K1)
- **1C 半严格**(剔除 units_bug): n=22, PF=10.7, WR≈64% → **强烈触 G2**
- **1B 严格**(只 AI 主动平仓): n=9, PF=40.2, WR=78% → **强烈触 G1+G2**

这是 §6.3 教训的现场印证:同一个判决文档在 T+2.6d 写时倾向 Kill,
T+2.7d 已经倾向 Go。**预承诺机制(§3 Checklist)的价值正在于此**:
不靠中间快照拍板,等 7/6 数据来再照表执行。

**实时数据见** `/api/analytics/round1-progress` 端点(2026-06-24 本会话扩展,
返回 `cohorts.raw / half_strict / strict_ai_driven` 三口径)。

### 6.5 第二次修订相关 commits(参考)

- doctor 加 `loop.reconciled_ratio` + `loop.concentration` 探针(本会话同一 commit)
- `/api/analytics/round1-progress` 加 `by_close_reason` 分组 + 三口径 cohorts(同上)
- `frozen_optimization_backlog.md` 新增 D15 (reconciler 模式) / D16 (OKX universe) / D17 (IOCP follow-ups)
