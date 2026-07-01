# 进/出场不对称 —— reconciler 假孤儿 thrash 根因分析（2026-07-01）

> 性质：L?（运行态信号驱动的代码根因分析）。触发：观察 Phase 2A+.10 做空链路时,监控捕获
> 6 个空仓在 2 秒内全平（`reconciled_gone`,均未碰 stop/tp）、5 分钟后又以**完全相同的入场价**
> 全部重开。追溯到代码核心,确认这不是"做空退出",是一个**自维持的 thrash 循环**在污染整个观察窗。

---

## 0. 一句话根因

**在非 Bitget 账户（当前 OKX demo）上,`position_reconciler.classify_positions` 在统一缓存(unified UTA
cache)短暂过期(≥600s)时,回退去读 legacy 的 Bitget-only "demo" 缓存。该缓存是新鲜但空的
(0 持仓,status="ok"),于是把所有 OKX 持仓误判为"孤儿"(本地开、交易所无),触发 reconcile
平仓。而 demo 孤儿平仓只是本地记账、不动交易所,持仓仍在 OKX 上 → 下个周期被 auto-adopt 重新
认领 → 无限 thrash。**

这既解释了 `reconciled_gone`(假孤儿平),也解释了 `exchange_tpsl_order_id=None`(重开的仓是
adopted,认领不下保护单),还解释了"进/出场不对称"这个报告标题的本意。

---

## 1. 症状（监控实测,2026-07-01）

- 13:44:00–02（2 秒内）：6 个 open short（BNB/TRX/ETH/BTC/DOGE/NEAR）**全部平仓**,
  `close_reason=reconciled_gone`,净 PnL≈+$1.17。
- **无一到达 stop 或 tp**：如 BNB 平于 544.5,夹在 tp 540 与 stop 574 之间；BTC 58772 同样夹在中间。
- 全部 `exchange_tpsl_order_id=None`（交易所侧无保护单）。
- 13:49:00–02：同 6 个标的**以完全相同的入场价重开**（BNB 562.7333333333333,不是市价 544.5）,
  `source=adopted`,`exchange_tpsl_order_id=None`。

相同入场价 + `source=adopted` = 不是新市价开仓,是**从交易所缓存里重新认领**。

---

## 2. 代码追溯

### 2.1 假孤儿的产生（触发点）

`aletheia/portfolio/position_reconciler.py:classify_positions()`（约 140–151 行）：

```python
unified = exchange_sync.get_unified_cached_state()
if unified is not None:
    age = unified.get("age_seconds")
    if age is not None and age < 600:        # ← 只有 <600s 才用 unified
        cached = {...unified positions...}
if cached is None:
    cached = exchange_sync.get_cached_state(mode)   # mode="demo" 显式 → legacy Bitget-only 缓存
```

- 统一缓存**只在 age<600s 时**被采用；否则 `cached` 仍为 None,回退到 `get_cached_state("demo")`。
- `mode="demo"` 是显式传入（`run_reconcile` 传 `account_mode=cfg.exchange.mode="demo"`）,
  所以 `get_cached_state` 走 `explicit_mode=True` 分支,直接读 `_read_raw("demo")` = **Bitget 专属**
  "demo" 缓存。当前账户是 OKX,这个 Bitget 缓存**新鲜但空**（实测 age=9s、0 持仓、status="ok"）。

### 2.2 为什么诚实性守卫没挡住

同函数约 163 行：

```python
if cached is None or sync_status not in ("ok", "stale"):
    # 报全 managed 并 bail —— "无可用交易所视图" 不臆测孤儿
    ...
```

守卫只在缓存**不存在**或状态**非 ok/stale** 时兜底。但 Bitget "demo" 缓存是合法的 **"ok"**（9s 新鲜,
只是里面没有 OKX 持仓）——守卫不触发,函数继续拿 OKX 本地持仓去和 Bitget 的 0 持仓做 join →
每个 OKX 持仓 key 都不在交易所集合里 → **全部归类 orphaned**（211–214 行)。

### 2.3 为什么平了还能"复活"（thrash 自维持）

`reconcile_close_orphan()`（463–504 行)文档明写 "close ... via **local bookkeeping**"：
只调 `pnl.close_position`（本地写 `status=closed`）,**不发交易所平仓单**。所以持仓在 OKX 上仍然在。
下一个 reconcile 周期若 unified 已刷新（<600s）,该持仓变成 **external**（交易所有、本地无）→
`auto_adopt_enabled=True` → `adopt_position()` 以交易所入场价重建本地仓,`source=adopted`,
不下保护单 → 回到起点,循环。

### 2.4 触发窗口的节拍（为什么反复发生）

- `position_reconcile` job：每 **5 min**（`scheduler.py:424`)。
- `exchange_sync` job（`sync_all_exchanges` → 刷 Bitget 缓存 + unified 缓存）：每 **5 min**（`:390`)。
  但 `_run_exchange_sync` 用 `_run_sync_with_timeout` 包裹,**OKX/UTA 查询超时/失败则 unified 不刷新**
  （Bitget 那一步在前、单独 try,即便 unified 失败 Bitget 缓存照样新鲜）。
- unified 每 5min=300s 刷一次；**连续 2 次 OKX 同步失败/超时 → unified age>600s** → 此时任一
  reconcile 周期就会读到空 Bitget 缓存 → 假孤儿全平。OKX API 抖动有前科（见
  `market_observer 线程泄漏`、`交易所断连韧性` 两份记忆/报告)。

实测 13:44 平、13:49 重开,正好一个 5min reconcile 间隔:一个周期 unified 过期→平,下一个周期
unified 刷新→重认领。

---

## 3. 规模（不是偶发,是主导）

对 `positions` 表全量统计（2026-07-01）：

| 指标 | 值 | 含义 |
|---|---|---|
| 总仓位行 | 206 | |
| `close_reason=reconciled_gone` | **85（41%）** | 四成"平仓"是孤儿清扫,非 AI 退出 |
| `source=adopted` | **116（56%）** | 过半仓位是被重新认领出来的,非策略开仓 |
| BNB/USDT short 生命周期行数 | **10** | 单标的被开/平循环 ~10 次(thrash churn) |
| reconciled_gone 按小时 | 13h=7 / 10h=8 / 08h=7 … | 全天多个小时成簇复发 |

**结论**：doctor 早报的 `loop.reconciled_ratio 70%`、`content.proposal_bias 95% short` 之所以
可疑,根子在这:**大部分"交易活动"是 reconciler thrash,不是真实策略行为**。据此算出的
win-rate/PF/归因全部被污染 —— owner 想攒的"干净窗"实际被 thrash 灌满了噪音。

---

## 4. 进/出场不对称（报告标题本意）

| | 进场（正常路径） | 出场（thrash 后） |
|---|---|---|
| 来源 | 策略/PI 决策,带 strategy_id、direction、stop/tp | 孤儿清扫 mark-to-last-price |
| 交易所保护单 | okx_broker 支持下 TP/SL（`okx_broker.py:111/212/225`) | 无（adopted 仓不下保护单） |
| 退出触发 | stop/tp/position_manager | 只能等下一次孤儿清扫 |
| PnL 真实性 | 真实成交 | 用最后已知价记账,非真实成交 |

一旦经历一次假孤儿,仓位就从"进场态"(富信息)退化为"adopted 态"(无策略、无保护单),
退出只能靠孤儿清扫兜底 —— 这就是不对称。**2A+.10 观察门「short→执行→管理退出」这一腿因此
未被真正验证**：开仓真,退出是 thrash 假象。

---

## 5. 根因修复方向（建议,未实施 —— 触仓位生命周期,需 owner 批 + SDD）

**P0（根因,最小改动）**：`classify_positions` 不得回退到"与当前活跃 broker 不匹配"的 broker
专属缓存。当活跃 broker 非 Bitget 且 unified 缓存不可用/过期时,应视为"无可用交易所视图" →
走既有安全分支(全报 managed、bail),**绝不**拿 Bitget 空缓存去判 OKX 孤儿。
- 具体：把 `get_cached_state(mode)` 回退**限定在活跃 broker 是 Bitget 时**；否则要么要求 unified、
  过期就 bail,要么放宽/去掉 600s 悬崖(改为"过期=未知"而非"过期=读别的 broker")。

**P1（不对称的另一半）**：`adopt_position`（`position_reconciler.py:226`+）**已写 DB 层默认 SL/TP**
（`stop_price=_adopted_stop / take_profit_price=_adopted_tp`,即 [[aletheia-adopted-position-missing-sltp]]
的修复生效 —— 实测重开的仓 DB 里确有 stop=573.988/tp=540.224）,但**不下交易所侧保护单**
（`exchange_tpsl_order_id` 保持 None）。后果：adopted 仓的保护完全依赖 position_manager 运行时监控 +
本地平仓;交易所侧无原生 TP/SL,一旦 gateway 挂/网络断,adopted 仓在交易所裸奔。上实盘前必须补
adopt 时下交易所保护单（demo 阶段先摸清路径合理）。

**P2（诚实性）**：`reconcile_close_orphan` 的 `reconciled_gone` 平仓,若源于"缓存 gap"而非真实
交易所消失,应可区分 —— 至少在孤儿平仓前二次确认(强制刷新 unified / 直查交易所),避免"缓存抖动"
被当成"仓位真没了"。

**边界提示**：`position_reconciler.py` 不在 CLAUDE.md 列的 trust boundary 清单内,但属决策链敏感
(会开/平仓)。修改须走 SDD:先在 `spec_*.py` 声明 claim（如"非 Bitget 账户 unified 过期时不产生
孤儿")→ RED → 改 → GREEN。

---

## 6. 与既有记忆/报告的关系

- [[aletheia-round1-cleanwindow-eval]]：Round1 干净窗 PF 0.06 时"平仓靠 reconciler 非 AI" —— 同一根子,
  当时归因到方向集中,**真正的机制是本报告的 thrash**。
- [[aletheia-profit-loop-broker-balance-trap]]：`get_cached_state` 默认路径读错 broker 喂 PI 幻觉 —— 
  **同一个"legacy Bitget 缓存 vs 多 broker"错配**的另一个受害者(那次是 equity,这次是 positions)。
  提示：`exchange_sync` 的 Bitget-only legacy 缓存在多 broker 时代是**系统性错配源**,值得一次性清算。
- [[aletheia-adopted-position-missing-sltp]]：adopted 仓缺 SL/TP 已修过一次 —— 需复核是否覆盖交易所侧。

---

## 7. 修复实现（2026-07-01,SDD 完成）

穷尽排查确认:缓存错配**只坑 reconciler 一处**(其它消费者都走 `get_cached_state(mode=None)`
默认路径,已被 profit-loop 修复保护,非 Bitget 自动返回 unified)。下游 thrash 污染只有
go_live_scorecard 未过滤(round1-progress 有 strict-cohort、trade_case_builder 已过滤)。

**P0 — 掐断 thrash 源头(`position_reconciler.classify_positions`)**
`cached is None`(unified 过期/缺失)时的回退改为 **broker 感知**:仅当活跃 broker 是 Bitget
(或 live)才回退读 legacy per-mode 缓存;非 Bitget 账户则保持 `cached=None` → 既有诚实性守卫
报全 managed 并 bail,**绝不**拿空的 Bitget 缓存误判孤儿。thrash 从源头消失。
- spec **PL-ADOPT-10/11/12**(L2b×2 + L2c 端到端):非 Bitget + 无新鲜 unified → 零孤儿 /
  新鲜 unified 见仓 → managed / run_reconcile 端到端零 orphan-close。反向验证:改前三条全红
  (1 orphaned、thrash close reconciled_gone、WR 被污染),改后全绿。

**P1 — Scorecard 去污(`go_live_scorecard`)**
新增 `_genuine_closed_pnls()`,四项交易指标(volume/PF/WR/回撤)统一排除 synthetic 平仓;
NULL close_reason 保留(未知≠合成)。新建 `analytics/close_reasons.py` 为单一真源
(`SYNTHETIC_CLOSE_REASONS` = reconciled_gone/demo_validation_cleanup/units_bug_cleanup),
`trade_case_builder` 改为复用它(顺带补齐 units_bug_cleanup 排除)。
- spec **AN-S03**(L2b):插 3 真实平仓 + 10 合成亏损,断言 scorecard 只算真实 cohort。

**验证**:conformance 496→500 claims 全绿;目标子系统 90 tests 全绿;full suite 回归 + doctor
`loop.reconciled_ratio` 观察 + gateway restart + 监控确认 thrash 停止。

**P2 — 仍未做(需 owner 批,触 execution 边界)**：①adopted 仓不下交易所保护单(demo 无碍/
上实盘裸奔)②adoption 抹 strategy_id(P0 修好后 adoption 变罕见,影响自然降)③factor_feedback
是否过滤 thrash。三项列入 backlog,owner 定夺。

**历史污染数据**：不追溯清洗(reconciled_gone 净 PnL≈-0.72,对 PnL 数字无实质影响;真正的
归因损失已成历史)。**修复后应视为新干净窗起点**——旧 adopted 碎片/thrash 数据不计入策略评估
(符合 EXPERIMENT_PROTOCOL 污染期排除原则)。
