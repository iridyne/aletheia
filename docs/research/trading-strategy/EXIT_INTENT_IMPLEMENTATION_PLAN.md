# 声明式退出意图（Declarative Exit Intent）实施计划 · 初稿

> 状态：**初稿 / DRAFT**，供后期打磨。
> 配套阅读：`docs/research/audits/EXIT_AUTHORITY_ANALYSIS.md`（现状与三层裁决框架）。
> 核心设计原则（本方案的一句话）：
>
> **AI 授权声明式意图（stop / target / horizon / trailing / thesis 条件）→ 执行器以 tick 级忠实执行 → AI 在自己的节律上修订。规则引擎从「自主策略家」降级为「AI 意图的高速执行器 + 独立硬安全网」。**

---

## 0. 阅读须知 & 一个关键的现实修正

调研阶段的心智模型说「本地执行器 tick 级执行」，落地时必须修正一个事实：

- **本地 `position_manager.evaluate_open_positions()` 每 5 分钟才跑一次**（`scheduler.py:685`，`interval, minutes=5`）。它**不是** tick 级。
- 真正的 tick 级执行发生在**交易所侧的 bracket order**：开仓时 `_try_place_tpsl()` 把 SL/TP 作为 TPSL 委托挂到交易所（`bitget_broker.py:434`，需 `exchange_tpsl_enabled` 且 bitget_demo/live），交易所以 tick 速度成交；`replace_exchange_stop()`（`bitget_broker.py:492`）负责改单；`exchange_tpsl_order_id` 列记录委托 id。本地的 `price_feed`（30s）、`tpsl_fill_check`（90s，`scheduler.py:694`）负责检测成交并回填。

**因此本方案里「tick 级执行器」= 交易所原生 bracket 委托（真实模式）+ 本地 5 分钟循环（paper 模式的执行器、以及所有模式的兜底与 trailing 重算引擎）。** 这对方案影响很大：

> AI 授权的 stop/target，落地路径是「翻译成交易所 bracket 委托」，而不是「让本地循环去比价平仓」。本地循环只在 paper_internal、以及交易所 bracket 失效时兜底。

这也意味着：**AI 自主管理止损的能力，天然依赖 `exchange_tpsl_enabled` 与 broker 的 TPSL 支持**。Bitget 已具备；OKX/Binance 的 TPSL/对账路径缺失（见 `EXIT_AUTHORITY_ANALYSIS.md` §5、BACKEND_ISSUES §16.2），是本方案的前置风险项。

---

## 1. 目标与非目标

### 目标
1. 引入一个**每仓一份的声明式对象 `ExitPlan`**，承载 AI 的全部退出意图。
2. AI 在**入场时授权**初始 ExitPlan（stop / target / horizon / trailing / thesis 条件）。后续 System Operator 扫一轮时有两类动作：**(A) 修订被动 bracket**（`adjust_position`，等触发）；**(B) 主动提前出场**——发现趋势即直接向交易所挂新单（市价立即平 / 限价耐心平）抢在被动 TPSL 之前平仓或减仓。
3. 确定性执行器把 ExitPlan **翻译为交易所 bracket 委托**（tick 级）+ 本地兜底执行；规则不再自主决定策略性出场。
4. 把当前「规则自主」的 trailing / breakeven / max-hold / 默认 TP / market-aware / thesis 逐步改造为「AI 授权 + 执行器执行」或「唤醒 AI 决策」。
5. 补齐 Layer 0 硬安全边界（catastrophic stop + 运行时爆仓监控），作为把普通止损交给 AI 的安全前提。

### 非目标（明确不做）
- **不做**「让 AI 实时决定每一次平仓」——违背节律现实，不可行。
- **不动** Layer 0 已有的硬控制语义（kill switch / 账户日损 / 对账冻结）——只做补齐，不做重构。
- **不在**真实资金模式下先行开启 M4/M5，除非 M6（安全边界）已上线（见 §6 依赖顺序）。

---

## 2. 现状 → 目标 对照

| 维度 | 现状 | 目标 |
|---|---|---|
| 退出意图载体 | 分散在 `positions.stop_price` / `take_profit_price` / `trail_high_water` + config 默认 | 统一的 `ExitPlan`（每仓一份，带 provenance/version） |
| stop/target 来源 | AI 可提，缺省静默回落 config，**永不复议** | AI 授权；缺省标记 `provenance=default` 并**主动回灌给 AI 复议** |
| horizon | 无字段，用全局 `max_position_hold_hours`(72h)/`max_hold_days`(90d) | AI 给 `expected_horizon`，全局 max-hold 降为**远端硬兜底** |
| 中途改止损 | **仅规则**（trailing/breakeven 自主移动）；AI 无此动作 | 新增 AI 动作 `adjust_position`；trailing 变为 AI 授权的声明式规则 |
| market-aware 反向退出 | 规则自动平仓/收紧（默认关） | 反向信号 → **唤醒事件 + context**，由 AI 决策 |
| thesis 失效 | 规则读 `opportunity.status` 代理自动平仓 | 状态变化 → **优先唤醒** AI 复议；代理平仓仅慢速兜底 |
| tick 级执行 | 交易所 bracket（已存在，Bitget） | 复用并强化：ExitPlan 的唯一 tick 级出口 |
| catastrophic stop | ❌ 无 | ✅ Layer 0 硬线，force-close 不受 AI 意图约束 |
| 运行时爆仓监控 | ❌ 仅 pre-trade | ✅ 持仓期实时监控 |

---

## 3. 核心抽象：`ExitPlan`

每个开仓持有一份 ExitPlan，是 AI 意图的唯一真源。建议数据形态（最终以 §5.1 迁移决策为准）：

```jsonc
ExitPlan {
  // —— 硬价位（翻译为交易所 bracket 委托）——
  "stop_price":   number | null,      // 硬止损；映射到交易所 SL 委托
  "target_price": number | null,      // 止盈；映射到交易所 TP 委托

  // —— 时间意图 ——
  "expected_horizon_hours": number | null,  // AI 预期持有时长；到点=唤醒 AI，非自动平仓

  // —— 声明式 trailing（由本地循环重算并 replace_exchange_stop）——
  "trailing": {
    "mode": "atr" | "pct" | "none",
    "distance": number,               // atr_mult 或 pct
    "activation_profit_pct": number | null,  // 达到该利润后才启动（含 breakeven 语义）
    "floor_at_breakeven": bool
  } | null,

  // —— 声明式 thesis 失效条件（命中→唤醒 AI）——
  "thesis_invalidation": {
    "kind": "opportunity_status" | "counter_signal_score" | "price_level" | "custom",
    "params": { /* 例如 counter_signal_score: {score_gte: 70} */ },
    "on_trigger": "wake" | "close"    // 默认 wake；close 仅用于 AI 明确授权的自动平仓
  } | null,

  // —— 溯源与治理 ——
  "provenance": "operator" | "default",  // AI 授权 or config 兜底
  "version": integer,                    // 每次 AI 修订 +1
  "authored_at": iso8601,
  "authored_by": "system_operator" | "system",
  "rationale": { /* 复用 parameter_rationale 语义，逐项理由 */ }
}
```

**读写语义**
- `provenance=operator`：AI 明确给了该项 → 执行器忠实执行。
- `provenance=default`：config 兜底占位 → 执行器照常执行，但**标记为待 AI 复议**，下次 System Operator context 中高亮「这些仓在用默认风控」。
- 每次 `adjust_position` 生成新 version，写审计事件，并同步交易所 bracket。

---

## 4. 三层落地映射（概念 → 组件）

```
Layer 0  硬安全边界（AI 不可改写；每个执行周期最先跑）
  ├─ kill switch / 账户日损 / 对账冻结        [已存在，risk/engine.py + kill_switch.py]
  ├─ catastrophic stop（新增）               [新增 executor 检查，Layer0 优先级最高]
  ├─ 运行时爆仓/保证金监控（新增）           [新增周期任务，复用 futures_margin_* 阈值]
  └─ 交易所异常/数据陈旧减险（补齐）         [risk/engine.py 已有 pre-trade，扩为持仓期]

Layer 1  ExitPlan 被动执行（fire-and-forget，机器速度）
  ├─ stop/target  → 开仓时挂交易所 bracket 委托，之后交易所自己 tick 级盯盘成交，我们不管
  │                 [bitget_broker._try_place_tpsl；改单 replace_exchange_stop]
  ├─ trailing     → 本地循环重算 → replace_exchange_stop  [重构 _maybe_update_trailing_stop 读 ExitPlan.trailing]
  ├─ horizon      → 到点唤醒 AI（全局 max_hold 兜底）      [重构 _decide_exit 的 max_hold 分支]
  └─ paper_internal → 5 分钟本地循环即执行器                [evaluate_open_positions 现有比价逻辑]

Layer 2  AI 主动裁量（System Operator 扫一轮 + 事件唤醒）
  ├─ open              → 授权初始 ExitPlan                        [trading_decision open 流程]
  ├─ adjust_position   → 修订被动 bracket（收紧/放宽 stop、移 target、改 trailing）——仍是被动等触发
  ├─ 主动提前平仓/减仓  → 发现趋势/迹象，直接向交易所挂新单抢在被动 TPSL 之前出场：
  │     • close_market  → 市价单，立即平（已存在；平后撤 bracket 已实现）
  │     • close_limit   → 限价单，在更优价位耐心平（**新能力**，需与 bracket 做 OCO 互撤）
  │     • reduce（market/limit）→ 部分平
  └─ 唤醒触发：stop 逼近 / target 逼近 / horizon 到点 / thesis 命中 / 反向信号  [pi_daemon 事件]
```

**Layer 1 vs Layer 2 的本质区别（用户核心观点）**：
- **Layer 1 是被动、fire-and-forget**：TPSL 括号单一次性挂到交易所，交易所以 tick 速度替我们盯盘、成交，我们不需要持续干预。`adjust_position` 只是把这张被动单改到新价位——**仍然是等价格触发**。
- **Layer 2 的主动提前出场是真正下单动手**：System Operator 扫一轮时若读出趋势/迹象，可以**主动挂一张新单（市价立即平 / 限价耐心平）抢在被动 TPSL 之前把仓位平掉或减掉**，而不是被动等 stop 被打。这是「收紧止损等它触发」与「直接动手平」的关键差异——后者才是 discretionary trader 的核心动作。

---

## 5. 组件级改造清单

> 每项标注：**改哪里 · 改什么 · 怎么改 · 兼容性**。行号基于当前 worktree（committed 状态），实现时以实际为准。

### 5.1 数据模型与迁移
- **文件**：`aletheia/storage/migrations.py`（positions 列定义在 `~155`；增量列 tuple 列表在 `~1648–1886`）
- **改什么**：为 positions 增加承载 ExitPlan 的列。
- **决策点 D1（待拍板）**：
  - 方案 A：**单 JSON 列** `exit_plan_json TEXT` + `exit_plan_version INTEGER` + `exit_plan_provenance TEXT`。灵活、演进快，但不便 SQL 查询。
  - 方案 B：**平铺列** `expected_horizon_hours REAL`、`trailing_json TEXT`、`thesis_invalidation_json TEXT`、`stop_source TEXT`、`exit_plan_version INTEGER`。复用现有 `stop_price/take_profit_price` 作硬价位，query 友好。
  - **初稿倾向 B**（与现有 `stop_price/take_profit_price/trail_high_water` 列一致，迁移风险小；trailing/thesis 用小 JSON）。
- **怎么改**：按现有 `("col","positions","TYPE")` tuple 追加；写 `ExitPlan` 的 read/write helper（建议 `aletheia/portfolio/exit_plan.py`：`load_exit_plan(position) / save_exit_plan(position_id, plan) / effective_exit_plan(position, cfg)`——后者取代分散的 `_effective_stop/_effective_take_profit`）。
- **兼容性**：老仓无新列 → helper 回落为 `provenance=default`，行为等价现状。**M1 阶段纯管道，无行为变更。**

### 5.2 AI 决策 schema（授权意图）
- **文件**：`aletheia/agent_shell/trading_decision.py`（`TradingDecision` dataclass `:94`；`_DECISION_SCHEMA` `:164`；action enum `:167`；prompt 组装 `~1011/1413`）
- **改什么**：
  1. 新增字段：`expected_horizon_hours: float | None`、`trailing: dict | None`、`thesis_invalidation: dict | None`。
  2. action enum 增加 `"adjust_position"`。
  3. `_DECISION_SCHEMA.properties` 增加上述字段；prompt 要求 trade/scale_in/flip 给 horizon 与（可选）trailing 意图，并入 `parameter_rationale`。
- **怎么改**：字段全部 optional，缺省不破坏历史 caller（`parameter_rationale_required` 模式已提供先例）。
- **兼容性**：AI 不给 → `provenance=default`；schema 向后兼容。

### 5.3 开仓落库（授权初始 ExitPlan）
- **文件**：open→proposal→position 落库路径（`trading_decision.py:517–541` 的 stop/tp 计算；`submit_decision_as_proposal`；broker 建仓 `bitget_broker.py:226` 处 `_try_place_tpsl`）
- **改什么**：建仓时用 AI 值构建 ExitPlan（有值→operator，无值→default 兜底），持久化；`_try_place_tpsl` 用 ExitPlan.stop/target 挂交易所 bracket（已如此，仅改数据来源）。
- **兼容性**：paper_internal 无 bracket，靠本地循环，行为等价。

### 5.4 执行器：Layer 1 重构
- **文件**：`aletheia/portfolio/position_manager.py`
  - `evaluate_open_positions()` `:346`（主循环）
  - `_decide_exit()` `~258`（SL/TP/max_hold/thesis）
  - `_maybe_update_trailing_stop()` `:164`（trailing/breakeven）
  - `_effective_stop/_effective_take_profit` `:113/:126`
  - `adjust_stop()` `:441`（→ `replace_exchange_stop`）
- **改什么**：
  1. SL/TP：改为读 `ExitPlan.stop_price/target_price`（经 §5.1 helper），行为等价但来源统一。
  2. `max_hold`：改为读 `ExitPlan.expected_horizon_hours`——**到点不再自动平仓，而是发唤醒事件**；全局 `max_position_hold_hours/max_hold_days` 降级为「horizon×N 或硬上限」的**远端兜底**（到此才自动平）。**【D3 裁定】该唤醒必须产出明确答复（平/续），不允许停留 Unknown；此"必答唤醒"不可被 D6 限频丢弃。** 兜底=`min(当前 horizon × N, 学校上限)`，**N 建议=3**（见 D3，待审核）；AI 选"续持"须给新 horizon，兜底随之前移。
  3. trailing/breakeven：`_maybe_update_trailing_stop` 改为执行 `ExitPlan.trailing` 声明式规则；**【D2 裁定：A】保留 ATR trailing 作执行器默认（读全局 `trail_enabled`/`trail_pct`/`trail_atr_mult` 作缺省），允许 AI 每仓通过 ExitPlan.trailing 覆盖/关闭**；flag 保留通往"纯 AI 授权(b)"的路。
  4. `thesis_invalidated`：**【D5 裁定：②】默认保持直接平仓（等价现状的代理自动平），但增加 AI 审核路径**——thesis 失效同时发唤醒事件，AI 可复议/否决该自动平仓（而非仅慢速兜底）。
- **兼容性**：全程 feature flag（`exit_intent.enabled`），关闭时等价现状。

### 5.5 AI 对持仓的两类动作（枢纽）

System Operator 扫一轮时，对一个已有持仓有**两类**动作，实现时必须区分清楚：

**(A) `adjust_position` — 修订被动 bracket（等触发）**
- **文件**：决策 handler（`trading_decision.py` action 分发）；执行器原语 `position_manager.adjust_stop():441`（已能 `replace_exchange_stop` 同步交易所）
- **改什么**：解析 `adjust_position` → 校验（新 stop 不得放松到 catastrophic 之外、不得违反 Layer 0）→ ExitPlan version++ → `save_exit_plan` → `replace_exchange_stop` 改交易所被动单 → 审计。
- **性质**：仍是被动——改完等价格触发。适合「保护利润但让它继续跑」。

**(B) 主动提前出场 — operator 直接下新单动手平（用户核心补充）**
- **文件**：`execution/bitget_broker.py`——`close_demo_position` 现**写死 `order_type="market"`**（`:342/:359`）；平仓侧无限价能力（入场侧有 `order_type=="limit"` 分支 `:129-131`，可复用）。`close_position_routed`（`position_manager.py:29`）为统一出口。
- **改什么**：
  1. **`close_market`**（≈现有 close）：市价立即平。现路径已在成交后 `_try_cancel_tpsl` 撤 bracket（`bitget_broker.py:400-401`），保留。
  2. **`close_limit`（新能力）**：operator 指定限价，挂 reduce-only 限价单在更优价位耐心平。**新增**：`close_demo_position` 支持 `order_type/limit_price` 参数；挂 reduce-only 限价单；`tpsl_fill_check`/`price_feed` 侦测其成交并回填。**【D10 裁定：A】`order_type`(+`limit_price`) 由 AI 在决策里显式给出，并入 parameter_rationale**（不由系统自动选单型）。
  3. **`reduce`（部分平）**：`pnl.reduce_position`(reduce-in-place：均价冻结、减 quantity、prorate entry_fee、写 `position_close_events` is_partial=1 → 日损断路器经 Stage-2 增量项计入)。**【D11】仅交易所 demo/live 模式**（真实 reduce-only 单）；**paper_internal 显式拒绝**并指引用 demo 模式（honest degradation）。已实现本地记账半 + 拒绝护栏；交易所下单半待 demo 验证。
- **决策点 D9（OCO，关键）· 【裁定：优先 A 原生 OCO，不支持回落 B 软 OCO】**：`close_limit` 挂单后，交易所上**同时存在**「operator 的限价平仓单」与「原 TPSL 括号单」，需 OCO 互撤——任一成交则撤另一张，任一改动需级联。方案：
  - **优先 A**：用交易所原生 OCO（若 broker 支持）；
  - **回落 B**：由 `tpsl_fill_check`（90s）做软 OCO——侦测一侧成交/部分成交，立即撤另一侧并对账仓量。**必须处理部分成交与撤单竞态**（避免超卖或残留裸单）。
  - **broker 现实（见 D7）**：`okx_live_limited`/`binance_live_limited` 挂单无对账（CR-EX-01），resting `close_limit` 在其 live 模式暂不安全；这两个 broker 先只支持 `close_market`，或待补 CR-EX-01 后再开 `close_limit`。
- **性质**：主动——operator 基于趋势判断直接动手，不等被动 stop 被打。这是 discretionary trader 的核心动作。
- **兼容性**：`close_market` 等价现有 close；`close_limit`/OCO 为纯新增，feature flag 下灰度。**(A) M3 交付，(B) 建议 M3.5 紧随（先 close_market 复用，再 close_limit+OCO）。**

### 5.6 唤醒 / 事件（让 AI 及时复议）· 【D6 已研究，采纳下述设计】
- **文件**：`aletheia/agent_runtime/pi_daemon.py`（事件唤醒循环 `~104–156`；`notify_pi_daemon:42`）；`aletheia/agent_shell/commands.py:1522`（context 注入）；`config/schema.py` `PiDaemonConfig:846-849`；`server/app.py:457-465`
- **唤醒触发源**（新增，均附带 payload context）：
  - stop/target **逼近**（price 距 stop/target < 阈值）
  - `expected_horizon` **到点/临近**
  - `thesis_invalidation` 条件命中（opportunity.status 变化 / 反向信号 score ≥ 阈值）
  - Layer 0 catastrophic 触发后 → 强制平仓 + 事后复盘唤醒
- **现状与硬缺口（研究结论）**：pi_daemon 现为**全局单 `asyncio.Event` + 全局 cooldown，唤醒不带 payload**（`notify_pi_daemon(source:str)` 仅记日志），`evaluate_trading_opportunity({})` 忽略传入 context（`commands.py:1522`）。→ **必须先补两个缺口**：①`notify_pi_daemon` 升级为带 `WakeEvent{position_id, kind, priority, ts, payload}`；②`evaluate_trading_opportunity` 真正消费打包 context。否则限频/合流/context 打包均无法落地。
- **采纳设计（三者正交）**：
  1. **限频**：per-position **token bucket**（`capacity=15`，`refill=15/3600 tokens/s`）——保证任意 1h 窗口每仓 ≤15，且允许行情剧变短时突发。
  2. **合流**：per-position **固定窗口 coalescing**（从 first-seen 起算，默认 `3.0s`；P0=`0s`），窗口内同仓多事件并成**一次**唤醒并打包 context 给 LLM（省 token + 对齐注意力）。用固定窗口而非纯 debounce，避免急变行情下窗口被续期饿死。
  3. **优先级 + 超限三档**：P0 灾难（爆仓/穿透止损）→ 独立抢占通道，**绕过限频立即处理**（可打断 cooldown）；P1 紧急（`STOP_LOSS_NEAR`/`THESIS_INVALIDATED`）超限→**排队等 refill，不丢**；P3 低（`TAKE_PROFIT_NEAR`）超限→丢弃、靠 heartbeat 兜底。
  4. **【D3 覆盖】`HORIZON_DUE` 视为"必答"事件，归入"排队不丢弃"档（非研究默认的 P3 可丢弃）**，确保 horizon 到点一定被解析为"平/续"。
- **保留**：现有 `_eval_lock`（防自重叠）、`cooldown_sec=45`（二级护栏，P0 可打断）、`heartbeat_sec=60`（兜底捞被丢弃的低优先级事件）。
- **参数默认（初稿）**：`coalesce_window=3.0s`(P0=0)、`bucket_capacity=15`、`bucket_refill=15/h`、稳态最小间隔 ≈4min。状态存内存（daemon 单实例常驻），重启重置可接受。
- **兼容性**：旧 `source`-only 调用兜底映射为低优先级 portfolio 级事件，无需一次性改所有 caller；唤醒基础设施升级可独立灰度。
- **待定**：见 D6（去抖窗口细调、超限降级边界）；完整研究见对话记录（token bucket vs leaky bucket vs 窗口计数器对比）。

### 5.7 Layer 0 安全补齐（M6，真实资金前置）
- **catastrophic stop**（D4=B，先固定价格 %）：`aletheia/risk/` 新增或在执行器最前置一段硬检查——`force-close 当 price 自入场越过 catastrophic_stop_pct`，不受 ExitPlan 约束。**建议（待审核）：school-aware `short_term=-10%` / `serenity=-15%`**（见 D4；短期正常止损 2%、serenity 8%，故 catastrophic 明显更宽只在止损失效时触发）。config 增 `risk.catastrophic_stop_pct`（按学校）。低杠杆（≤5×）下此价格档先于 margin force-close(0.85) 触发，与运行时爆仓监控互补。v2（flag→A）：杠杆感知 margin-% 或 `AI_stop × k`。
- **运行时爆仓监控**：新增周期任务（或并入 `connectivity_monitor`/`position_manager` 前置），复用 `futures_margin_warning_ratio(0.70)/force_close_ratio(0.85)`（`config/schema.py:299`）与 `calc_liquidation_price`（`instruments.py:226`），逼近强平→减仓/平仓 + 唤醒。
- **交易所异常减险**：把 `risk/engine.py` 的 pre-trade 数据陈旧检查扩展为持仓期——数据陈旧/断连时冻结新的 AI 出场改单，保持交易所 bracket 生效（bracket 是断连时的最后防线）。

### 5.8 配置
- **文件**：`aletheia/config/schema.py`
- **新增**：`exit_intent { enabled, wake_stop_proximity_pct≈0.4, wake_horizon_lead_hours, horizon_hard_backstop_mult=3 }`、`risk.catastrophic_stop_pct { short_term:-10, serenity:-15 }`、运行时爆仓监控开关；`PiDaemonConfig` 增 `coalesce_window_sec=3.0`、`wake_bucket_capacity=15`、`wake_bucket_refill_per_hour=15`（D6）。上述数值均为**建议默认，待审核**。
- **保留**：`trail_enabled/trail_pct/trail_atr_mult` 作为 trailing 默认值来源（D2 方案 a）；`market_aware_exits`（M5 后语义从「自动平仓」转为「唤醒输入」）。

---

## 6. 分阶段实施（里程碑 + 验收 + 依赖）

> 原则：**先管道、后行为；先度量、后切换；安全边界先于交出自主权。** 每个里程碑独立可上/可回滚（feature flag）。
> **【D8 裁定：M6 严格先行，全串行、一步一步完成】** 顺序：M0 → **M6** → M1 → M1.5 → M2 → M3 → M3.5 → M4 → M5 → M7。

| 里程碑 | 内容 | 验收标准 | 依赖 |
|---|---|---|---|
| **M0 度量埋点** | 出场归因统计（各 reason 占比，仿 RuleSet 0/208 做法）；确认 `exchange_tpsl_enabled`/`trail_enabled`/`market_aware_exits.enabled` 线上真实状态 | 出一张「谁在驱动出场」实证分布；无行为变更 | — |
| **M6 硬安全边界（先行）** | catastrophic stop（D4：先固定 `-X%`）+ 运行时爆仓监控 + 交易所异常减险 | 强平线上线；越线 force-close 且不被 AI 意图绕过；spec 锁定 | M0 |
| **M1 ExitPlan 数据层** | 迁移列（D1：平铺）+ `exit_plan.py` helper + 老仓回填 default | 单测：helper 读写/回落等价现状；执行器仍走旧路径 | M6 |
| **M1.5 唤醒基础设施** | `notify_pi_daemon` 带 `WakeEvent` payload + `evaluate_trading_opportunity` 消费 context + per-position token bucket(15/15h) + 固定窗口 coalescing(3s) + 优先级抢占（D6） | 多事件合流成一次唤醒并打包 context；每仓每小时 ≤15；P0 绕过、P1/必答排队、P3 丢弃 | M1 |
| **M2 授权入场意图** | 决策 schema 加 `expected_horizon`(+trailing/thesis)；open 落库 ExitPlan；horizon 驱动 per-仓时间停（到点必答唤醒，D3），全局 max_hold 降为远端兜底 | 新仓带 operator ExitPlan；AI 缺省→default 且回灌复议；horizon 到点必答；spec 锁定 | M1.5 |
| **M3 adjust_position（枢纽 A）** | 新增 AI 动作 + handler + 审计 + bracket 同步 | AI 可在不平仓下改 stop/target；改单落到交易所；审计完整 | M2 |
| **M3.5 主动出场（枢纽 B）** | `close_market` 复用现有 + `close_limit`（reduce-only 限价，AI 显式给单型 D10）+ OCO（D9：优先原生，回落软 OCO） | AI 可主动挂市价/限价单提前平仓；限价单与 TPSL 括号 OCO 正确互撤；部分成交/竞态有处理 | M3 |
| **M4 trailing→AI 授权** | `_maybe_update_trailing_stop` 执行 ExitPlan.trailing；ATR 默认可覆盖（D2：A） | flag 开：trailing 可由每仓意图覆盖；flag 关：等价现状 | M3.5 |
| **M5 market-aware→唤醒 & thesis 审核路径** | 反向信号/opportunity.status → 唤醒事件 + context；**market-aware 不再自动平仓**；**thesis 默认仍自动平仓（D5：②）但增加 AI 审核/否决路径** | AI 收到反向信号 context 并可决策；thesis 自动平前 AI 可介入 | M4 |
| **M7 shadow 评估** | 并行记录「规则会怎么出 vs AI 怎么出」，比 PnL/回撤/持有时长 | 验证「AI 出场优于规则」假设；不成立的类别保留在 Layer 1 纯执行 | M5 |

**说明（D8）**：M6 严格先行意味着 catastrophic 兜底在任何"把出场交给 AI"的里程碑（M3+）之前就已就位；真实资金启用仍应先经 paper/demo 灰度验证。M1.5 是所有"唤醒驱动"行为（M2 的 horizon 必答、M5 的信号/thesis 唤醒、M2 的 default 回灌）的公共前置。

---

## 7. 关键数据流（目标态）

**入场（authorize）**
```
System Operator LLM → TradingDecision{action:trade, stop, target, expected_horizon, trailing?}
  → build ExitPlan(provenance=operator|default)
  → submit_decision_as_proposal → risk gate（含 catastrophic 合法性校验）
  → broker 建仓 + _try_place_tpsl(stop,target) 挂交易所 bracket   ← tick 级出口就位
  → save_exit_plan(position)
```

**执行（execute, tick 级）**
```
交易所以 tick 速度监控 → 命中 SL/TP → 成交
  → tpsl_fill_check(90s)/_sync_exchange_closed 检测并回填 realized PnL
（paper_internal：evaluate_open_positions(5min) 比价平仓）
Layer 0：每周期最先跑 catastrophic/爆仓/账户日损，命中即 force-close（无视 ExitPlan）
```

**修订 / 主动出场（AI 节律，扫一轮或事件唤醒）**
```
唤醒事件（stop 逼近/horizon 到点/thesis 命中/反向信号）或周期扫描
  → System Operator 评估
  ├─(A 被动修订) action:adjust_position{new_stop/target/horizon/trailing}
  │     → 校验(不放松到 catastrophic 外) → ExitPlan.version++ → replace_exchange_stop → 审计
  │     → 交易所继续 tick 级盯这张被动单（等触发）
  └─(B 主动出场) 发现趋势 → 直接下新单抢在被动 TPSL 之前平：
        • close_market  → 市价立即平 → 成交后 _try_cancel_tpsl 撤 bracket（已实现）
        • close_limit   → 挂 reduce-only 限价单（更优价耐心平）
              → 与原 TPSL bracket 并存 → OCO：一侧成交即撤另一侧（D9）
              → tpsl_fill_check(90s) 侦测成交/部分成交 → 撤单 + 对账仓量 → 回填 PnL
```

---

## 8. 向后兼容与迁移
- **总开关** `exit_intent.enabled`：关闭时全系统等价现状（helper 回落 default，执行器走旧分支）。
- **老仓**：无 ExitPlan → 视为 `provenance=default`，行为不变；可选后台任务回灌。
- **spec 锁定**：仿 `PL-PM-08`（`tests/specs/spec_pipeline_position_manager.py:441`）为每个「规则自主出场→AI 授权」的迁移写结构性不变量，防止回归成「已布线未引爆的静默覆盖」。
- **broker 差异**：Bitget bracket 完整；OKX/Binance TPSL/对账缺失 → 这些 broker 下 AI 授权止损降级为「本地 5min 循环执行」，需在文档与告警中显式标注（不可静默）。

---

## 9. 测试策略
- **单元**：`exit_plan.py` helper（读写/回落/版本）；`adjust_position` 校验（拒绝放松到 catastrophic 外）；trailing 声明式重算。
- **Spec/不变量**：max_hold 到点不自动平仓（改唤醒）；market-aware 不再自动平仓（M5 后）；catastrophic 不可被 AI 绕过。
- **集成**：入场→bracket 挂单→改单→成交回填 全链路（demo）。
- **Shadow（M7）**：记录规则假想出场 vs AI 实际出场，离线对比。
- **回归门**：`exit_intent.enabled=false` 时全套现有 position_manager 测试必须绿。

---

## 10. 决策点与裁定（问题原文保留，裁定见每条 → 行）

> 裁定记录于 2026-07。**问题本身保留不删**，便于后期回看权衡与重新讨论。

- **D1** ExitPlan 存储：单 JSON 列 vs 平铺列（初稿倾向平铺 + 小 JSON）。
  - → **裁定：B（平铺列 + 小 JSON）**。按 §5.1 方案 B 实施。
- **D2** trailing 过渡：保留默认可覆盖 vs 彻底 AI 授权（初稿倾向前者起步）。
  - → **裁定：A（ATR trailing 作默认，AI 可每仓覆盖/关闭）**，用 flag 保留通往 B 的路。用户明确更倾向 A。
- **D3** horizon 到点行为：唤醒 AI（倾向）vs 直接平仓；全局硬兜底倍数 N=?
  - → **裁定：①唤醒 AI 复议，且该唤醒必须产出明确答复（平仓 或 继续持有），不允许停留在 Unknown/未决状态**。此类"必答唤醒"不可被限频丢弃（见 D6）。
  - → **建议 N（待审核）：`horizon_hard_backstop_mult = 3`**。远端硬兜底 = `min(当前 expected_horizon × 3, 学校绝对上限)`（short_term 72h / serenity 90d 仍是不可逾越的天花板）。**关键机制**：AI 在 horizon 必答唤醒时若选"继续持有"，须给出**新的 expected_horizon**（兜底随之前移）——于是兜底始终追踪"当前意图"，只在 AI **不响应**时作为失效保护触发，而非覆盖一个正常工作的 AI。理由：N=3 给 AI 的"续持"决定足够余量（可跑到预期的 3 倍才触底），又能清理真正被遗忘/卡住的仓；由学校上限封顶，绝不会把自动平推到现有 72h/90d 之外。
- **D4** catastrophic stop 定义：`AI_stop × k`（k=?）还是固定 `-X%`，还是二者取更近。
  - → **裁定：先做 B（固定 `-X%`）**，用 flag 保留通往 A 的路径。
  - → **建议 X（待审核）：学校感知的价格 %——`short_term = -10%`，`serenity = -15%`**（自入场价的价格移动，与现有 school-aware 止损/max_hold 一致）。理由：必须明显宽于正常止损才只在"AI 止损失效"时触发——short_term 正常止损 2%，-10% 是其 5×；serenity 正常止损 8%，-15% 约 2×（长周期容忍更大回撤）。**杠杆交互**：本系统杠杆上限低（demo major 5× / live major 3× / alt live 1×），故价格 % catastrophic 可行且与 margin 监控互补——按 force-close 0.85 折算，-10%/-15% 价格档在允许杠杆下都**先于** margin 强平触发，形成"价格档失效保护 → margin 档强平邻近"的双层。v2（flag→A）：改为杠杆感知的 margin-loss %（如 50% 保证金）或 `AI_stop × k`。
- **D5** thesis_invalidation 默认 `on_trigger`：wake（倾向）vs close；哪些条件允许 AI 授权 auto-close。
  - → **裁定：先走②（默认直接平仓），但保留 AI 审核路径**（AI 可介入复议/否决）。
- **D6** 唤醒频率与 LLM 成本上限：事件唤醒的去抖/合流策略；每仓每小时最多唤醒几次。
  - → **裁定：每仓每小时最多唤醒 15 次**。去抖/合流策略**已研究并采纳**（详见 §5.6）：per-position token bucket(15/15h) + per-position 固定窗口 coalescing(3s，P0=0s) + P0 灾难独立抢占绕过限频；超限三档（绕过/排队/丢弃）；`HORIZON_DUE` 按 D3 上调为"排队不丢弃"。**先决**：须先补 `notify_pi_daemon` 带 payload、`evaluate_trading_opportunity` 消费 context 两个缺口。
  - → **建议细调（待审核）**：
    - **窗口/桶**：`coalesce_window=3.0s`（P0=0s）、`bucket_capacity=15`、`bucket_refill=15/3600 s`；保留 `cooldown_sec=45`（P0 可打断）、`heartbeat_sec=60`（兜底捞被丢弃的低优先级）。
    - **事件分层（复用现有 market_aware 分数阈值，避免另造一套）**：
      - **P0（绕过限频、抢占）**：catastrophic 越线 / margin 逼近 force-close(0.85) / 价格跳空穿透止损。
      - **P1（必答，超限只排队不丢弃）**：`STOP_LOSS_NEAR`、`THESIS_INVALIDATED`、`HORIZON_DUE`（D3 上调）、反向信号 `score ≥ urgent_exit_score(80)`。
      - **P2（正常消费令牌；超限降级为排队一次后丢弃）**：反向信号 `score ≥ tighten_stop_score(60)`。
      - **P3（可丢弃、靠 heartbeat）**：`TAKE_PROFIT_NEAR`、反向信号 `score < 60`。
    - **逼近阈值**：`STOP_LOSS_NEAR`/`TAKE_PROFIT_NEAR` 触发点建议 ATR 缩放（缺省价距 `wake_stop_proximity_pct≈0.4%`，有 ATR 时取 `0.5×ATR` 与之取大），避免高波动品种过早/过频唤醒。
- **D7** OKX/Binance：是先补 TPSL/对账再扩展 AI 自主止损，还是暂时限定 Bitget-only。
  - → 核实结论：三家都支持市价/限价/reduce-only 平仓单；OKX 有 TPSL 括号（`place_okx_protective_orders`），Binance 无；真正缺口是 `okx_live_limited`/`binance_live_limited` 的**挂单对账**（CR-EX-01，`reconciliation.py:43`）——resting 限价单在这两个 live broker 上无法可靠追踪。
  - → **建议方向（待审核）：A —— Bitget-first 起步，CR-EX-01 作 fast-follow（不作为架构前置）**。按 broker 能力精确分档，而非一刀切禁用：
    - **Bitget（demo/live）**：全功能——被动 bracket + `adjust_position` + `close_market` + `close_limit` + OCO。整个 ExitPlan→bracket→改单→OCO 闭环先在 Bitget 跑通验证。
    - **OKX**：有 TPSL 括号 → 被动 stop/target ✓；`close_market` ✓；`close_limit` 仅在补 CR-EX-01 对账后对 live 开放（demo 若有 `get_open_orders` 可先开）。
    - **Binance**：无 TPSL 括号 → 被动 stop 依赖本地 5min 循环（降级，**须显式告警**）；`close_market` ✓；`close_limit` 待 CR-EX-01。
    - 理由：exit-intent 架构是主线，CR-EX-01 是正交的 broker 对齐缺口，耦合会拖慢主线；Bitget 已是完整验证过的路径；OKX/Binance live 目前并非近期主战场，暂关其 `close_limit` 实际成本低。CR-EX-01 在主线于 Bitget 验证通过后、OKX/Binance live 上量前补齐。
- **D8** 里程碑排序：M6 与 M2/M3 并行 vs 串行（影响真实资金灰度时间点）。
  - → **裁定：M6 严格先行，一步一步完成（全串行）**。见 §6 已按此重排。
- **D9** 主动限价出场的 OCO 实现：交易所原生 OCO vs 本地软 OCO（`tpsl_fill_check` 侦测+互撤）；部分成交/撤单竞态的处理策略；limit 单挂多久不成交则升级市价。
  - → **裁定：优先 A（交易所原生 OCO），不支持则回落 B（本地软 OCO / 最基础挂单 + 本地互撤）**。
- **D10** operator 主动出场的单型选择由谁定：AI 在决策里显式给 `order_type`(+limit_price)，还是系统按「急/不急」自动选（初稿倾向 AI 显式给，并入 parameter_rationale）。
  - → **裁定：A（AI 在决策里显式给 `order_type`(+`limit_price`)，并入 parameter_rationale）**。
- **D11** paper_internal vs 交易所 demo 的功能边界（reduce / 原生 OCO / resting 限价 放哪里做）。
  - → **裁定：`paper_internal` 保持"功能受限"——整仓平仓 + 离线 + 基础出场;部分/高级出场(partial reduce、真·resting reduce-only 限价、原生 OCO)只在交易所 demo 模式(bitget_demo/okx_demo/binance_testnet,即默认 paper_execution_mode)上提供。** 理由:交易所 demo 是真实沙箱、保真更高;paper 现金账本的 `UNIQUE(action,position_id)` 难题只在 paper_internal,重写=重造交易所。**影响**:R1 台账 + Stage-2 断路器项保留(是交易所-demo 部分平仓对风控安全的地基);`reduce_position` 已对 paper_internal 显式拒绝(honest degradation);**paper 现金账本重建迁移取消不做**;交易所 broker 的 reduce-only + 原生 OCO 待 demo 验证。详见 `PARTIAL_REDUCE_RESEARCH.md §产品裁决`。

**状态**：D1–D11 主裁定已定；D3(N)、D4(X%)、D6(细调)、D7(方向) 均已给出**建议值，待用户审核**（见各条 → 建议行）。审核通过后即为定稿，再由各建议值回填 §5.x 正文。

---

## 11. 文件改动速查表

| 区域 | 文件 | 主要改动 | 里程碑 |
|---|---|---|---|
| 迁移 | `aletheia/storage/migrations.py` | positions 增列（ExitPlan） | M1 |
| 数据模型 | `aletheia/portfolio/exit_plan.py`（新建） | load/save/effective ExitPlan helper | M1 |
| AI schema | `aletheia/agent_shell/trading_decision.py` | 字段+enum `adjust_position`+prompt | M2/M3 |
| 开仓落库 | `trading_decision.py` + `execution/bitget_broker.py` | 用 ExitPlan 构建并挂 bracket | M2 |
| 执行器 | `aletheia/portfolio/position_manager.py` | SL/TP/horizon/trailing 读 ExitPlan；max_hold 转唤醒 | M2/M4 |
| AI 动作(A) | `trading_decision.py`(handler) + `position_manager.adjust_stop` | `adjust_position` 修订被动 bracket | M3 |
| AI 动作(B) | `execution/bitget_broker.py`(`close_demo_position`+新 limit 路径) + `position_manager.close_position_routed` | 主动 `close_market`/`close_limit`/reduce + 与 bracket 的 OCO 互撤 | M3.5 |
| 唤醒基础设施 | `aletheia/agent_runtime/pi_daemon.py`(`notify_pi_daemon:42`) + `agent_shell/commands.py:1522`(消费 context) + `config/schema.py`(`PiDaemonConfig:846`) | `WakeEvent` payload + token bucket(15/15h) + 固定窗口 coalescing(3s) + 优先级抢占 | M1.5 |
| 唤醒触发源 | `pi_daemon.py` + 执行器/信号侧 | stop/target 逼近、horizon 到点(必答)、thesis 命中、反向信号 → 发 WakeEvent | M2/M5 |
| 安全 | `aletheia/risk/`(+新周期任务) + `config/schema.py` | catastrophic stop(固定 -X%) + 运行时爆仓监控 + 异常减险 | M6 |
| market-aware | `aletheia/portfolio/exit_rules.py` | 从自动平仓改为唤醒输入；thesis 保留自动平 + AI 审核路径 | M5 |
| 配置 | `aletheia/config/schema.py` | `exit_intent` / `risk.catastrophic_stop_pct` | M2–M6 |
| spec | `tests/specs/spec_pipeline_position_manager.py` | 迁移不变量（仿 PL-PM-08） | 各里程碑 |

---

## 附录 — 关键锚点行号（当前 worktree，committed）
- 调度节律：`scheduler.py:685`(position_manager 5min)、`:675`(price_feed 30s)、`:694`(tpsl_fill_check 90s)、`:670`(pi_trading_decision 兜底)
- 执行器：`position_manager.py` `evaluate_open_positions:346` / `_decide_exit:~258` / `_maybe_update_trailing_stop:164` / `_effective_stop:113` / `_effective_take_profit:126` / `adjust_stop:441` / `close_position_routed:29`
- 交易所 bracket：`bitget_broker.py` `_try_place_tpsl:434` / `replace_exchange_stop:492` / `exchange_tpsl_order_id` 列
- AI 决策：`trading_decision.py` `TradingDecision:94` / `_DECISION_SCHEMA:164` / action enum `:167` / stop/tp `:185` / close·reduce 分发 `:1609` / evaluate 入口 `:232`
- 迁移列：`migrations.py` positions DDL `~155`、增量列 `1648/1653/1654/1829/1885`
- 安全：`risk/engine.py` 账户日损 `269` / 爆仓缓冲 `921`；`risk/kill_switch.py`；`config/schema.py` `MarketAwareExitsConfig:432` / 保证金阈值 `299`
- 唤醒：`pi_daemon.py` `notify_pi_daemon:42` / 事件循环 `~104–156` / 现有 debounce `126` / cooldown `153`；`commands.py:1522`(现忽略 params)；`config/schema.py` `PiDaemonConfig:846`；OKX/Binance broker：`okx_broker.py` `place_okx_protective_orders:282` / `close_okx_position:407`；`binance_broker.py` `close_testnet_position:215`；对账缺口 `reconciliation.py:43`(CR-EX-01)
