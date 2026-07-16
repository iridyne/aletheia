# 部分平仓 / 减仓 (Reduce / Scale-Out) 记账模型研究报告

> 目的：为 Aletheia 增加"部分平仓/减仓"功能选定**记账模型**。核心难点是记账：已实现 PnL 的分摊、剩余仓 quantity / entry_fee 的更新、以及部分实现的 PnL 如何正确喂进日损断路器 (daily-loss) 与分析/学习管线。
>
> 研究方法：本项目代码逐行阅读（含 file:line 证据，已当场复核关键行）+ 三家交易所官方 API 文档 + CCXT master 源码 + 5 个开源交易机器人源码/文档。
>
> 日期：2026-07-14 · 分支：master · 工作目录：`.claude/worktrees/exit-system-analysis`
>
> 结论先行：**采用「原地按均价减 quantity + 累加已实现 PnL」(reduce-in-place) 模型**，这是全部 5 个主流开源机器人（在 netting 语义下）的一致做法，也与本项目现有 netting 单仓模型、加仓 (scale-in) 的加权均价记账天然对称。**但当前日损断路器与 paper 现金账本会在部分平仓下漏账/硬失败**，必须先改造它们的读取来源，否则减仓会打穿风控。

---

## 产品裁决 (2026-07) — 已定案并实施

在本报告基础上做出的产品边界决策（owner 拍板）：

**`paper_internal`(本地模拟器)保持"功能受限"定位——只做整仓平仓、离线、基础出场;所有部分/高级出场(partial reduce、真·resting reduce-only 限价、原生 OCO)只在交易所 demo 模式(`bitget_demo` / `okx_demo` / `binance_testnet`)上提供。**

理由：
- **默认 paper 模式本来就是 `bitget_demo`**(`config/schema.py:87`),`paper_internal` 是离线次要模式;高级出场走交易所 demo 不是绕路,是回主路径。
- **paper 现金账本的 `UNIQUE(action, position_id)` 难题只存在于 `paper_internal`**(`_paper_ledger_snapshot` 仅对该模式生效);`bitget_demo` 的现金/余额来自真实 demo 账户,部分平仓无本地账本问题。
- 在本地模拟器里重写"部分成交 / reduce-only / OCO / 保证金"= 重造交易所,保真度低、易漂移、且需危险的现金账本重建迁移;同样能力在交易所 demo 上是真实沙箱行为。

**由此确定的实施状态**:
- ✅ **R1 台账 `position_close_events` + 日损断路器 Stage-2 增量项**保留并已实现 —— 它们不是 paper_internal 专用,而是让 **`bitget_demo` 等交易所 demo 的部分平仓对日损风控安全**的地基(交易所 demo 走非-paper 路径:closed 求和 + partial 增量项)。
- ✅ **`reduce_position` 原语**已实现(本地记账半:实现 PnL + 写台账 + 减量 + prorate 费),交易所 broker 确认 reduce 成交后调用它。
- ✅ **`reduce_position` 对 `paper_internal` 显式拒绝**(`pnl.py`,返回 `partial_reduce_unsupported_on_paper_internal` + 指引用 demo 模式)—— honest degradation,绝不静默近似。
- ❌ **paper 现金账本重建迁移 → 取消,不做**(本决策省掉的最大风险/工作量)。
- ✅ **CR-EX-01 已实现(docs/CCXT + mock 测,待一次 demo 采样确认响应形状)**:`OKXClient/BinanceClient.get_open_orders()`(CCXT `fetch_open_orders`)+ `reconciliation._reconcile_ccxt_broker` 把 `okx_live_limited`/`binance_live_limited` 接进 `reconcile()`(镜像 Bitget 路径,复用纯函数 `resolve_orders_missing_from_exchange`;fetch 失败则告警残余、绝不臆断成交)。5 个 mock 测覆盖。**只读、低风险**;真实响应字段/是否需 symbol 参数留一次 demo 采样确认(最好录成 fixture)。
- 🟡 **部分 reduce-only 下单(docs/CCXT + mock 测,待 demo 采样真实语义)**:`position_manager.reduce_position_routed`(路由+护栏:paper_internal 拒绝、整仓退化为 close、okx→reduce_okx_position、bitget/binance→pending demo)+ `okx_broker.reduce_okx_position`(reduce-only 部分单 → 成交后调 reduce_position 记账)作**参考实现**,mock 测(验 reduce_only+部分量+记账)。**待 demo**:reduce-only 部分单的真实语义(是否恰好减 reduce_qty、部分成交)、**减仓后 TP/SL bracket 需要 size 重同步**(否则超量/孤儿)、以及 **bitget 对冲模式的部分平**(默认模式,`tradeSide:close` 非 reduceOnly)+ binance 同模式。
- ⏳ **待 demo 验证(高风险,动仓位)**:**原生 OCO cancel-on-fill**(`execution/oco.py` 纯逻辑已就位,交易所挂/撤适配器是标注的 needs-demo 桩)。
- `close_limit` 的**合成本地看盘**在 paper_internal 保留作粗近似(诚实标注为"到价市价平",非 maker 挂单)。

下面的原始研究(记账模型、交易所 API、CCXT、竞品、落地要点)仍然有效,是交易所-demo 侧实现 reduce-in-place 的依据。

---

## 目录
1. [本项目现状](#1-本项目现状)
2. [交易所侧（官方 API）](#2-交易所侧官方-api)
3. [CCXT 兼容度](#3-ccxt-兼容度)
4. [竞品做法](#4-竞品做法开源交易机器人)
5. [给 Aletheia 的推荐模型 + 落地要点](#5-给-aletheia-的推荐模型--落地要点)
6. [未决问题](#6-未决问题)

---

## 1. 本项目现状

**总纲：系统当前是严格的「整仓平仓 (full-close only)」。** `close_position()` 永远实现整个 `row["quantity"]`、把 `status` 置为 `"closed"`、写恰好一条 `position_closed` 事件。代码里**没有任何部分平仓 / 减仓 / scale-out 路径**。唯一的字面命中 `"reduce_position_with_confirmation"`（`aletheia/gateway/state_machine.py:41`）只是冻结期白名单里的一个裸字符串，没有任何函数实现它（grep 零命中）。

> 说明：worktree cwd 下的 `aletheia/storage/schema.sql` 是 **0 字节空文件**（git status 显示为未跟踪的空文件）；真实 DDL 全部在 `aletheia/storage/migrations.py`。下文行号以主仓 `/Users/yixian/Projects/aletheia/aletheia/...` 为准。

### 1.1 `pnl.close_position()` 如何算 PnL / 写 fills / 更新 positions / 写审计

文件：`aletheia/portfolio/pnl.py`

**签名（无 quantity 参数）** —— `pnl.py:191`：
```python
def close_position(position_id: str, exit_price: float, reason: str = "manual") -> dict:
```
只接受 `position_id` 与 `exit_price`，**没有 `quantity` / `fraction` 入参**，因此从 API 形态上就无法表达"平掉一部分"。

**状态守卫（阻止对同一行二次平仓）** —— `pnl.py:207-208`：
```python
if row.get("status") != "open":
    raise ValueError(f"position is not open: {position_id}")
```

**实现 PnL 公式** —— `_realized_pnl`（`pnl.py:98-101`），在 `pnl.py:210` 以**整仓 quantity** 调用：
```python
def _realized_pnl(direction, entry, exit_price, qty):
    if direction in ("long", "buy"):
        return (exit_price - entry) * qty
    return (entry - exit_price) * qty   # short/sell
# 调用点（pnl.py:210）：
gross_pnl = _realized_pnl(row["direction"], row["entry_price"], exit_price, row["quantity"])
```

**手续费处理** —— `pnl.py:214-232`：
- 退出费率按 `execution_mode` 查表 `_EXIT_FEE_BY_MODE`（`pnl.py:214-222`，与各 broker 开仓常量一致：paper/bitget=0.0006, okx=0.0005, binance=0.0004）。
- `entry_fee` 优先用**已存储的累积值**，仅当 `is None`（真正 NULL）时才按费率重算（`pnl.py:228-230`，Sprint B 修复：`0.0` 是合法值，旧的 `or 0` 会在合法零上误重算并虚增 PnL）。
- `exit_fee` 恒按**整仓** `row["quantity"]` 计算（`pnl.py:231`）。
- `realized = gross_pnl - entry_fee - exit_fee`（`pnl.py:232`）。

**是否写 fills 表？—— close_position 本身不写。** `pnl.py` 仅 import `list_positions, insert_portfolio_snapshot, update_position, list_market_snapshots`（`pnl.py:10-12`），无 fill 写入。fills 由 **broker 层**在调用 `close_position` 之前写（见 §1.4）。

**positions 表更新（不删行、不清零 quantity）** —— `pnl.py:259-272`：只翻转 `status='closed'` 并盖上经济学字段（`realized_pnl`、`unrealized_pnl=0`、`close_reason`、`closed_at`、`entry_fee`、`exit_fee`、`slippage_cost`、`exit_reasoning`），**原始 `quantity` 原封不动**。"已平"完全由 `status='closed'` 标志表达，**没有残余数量概念**。

**paper 现金账本结算** —— `pnl.py:274-276`：paper_internal 模式额外写一条 `position_closed` 现金账本事件 `settle_position_closed(position_id, realized)`。

**`position_closed` 审计事件** —— `pnl.py:277-286`：经 `write_audit(...)` 写 JSONL 审计（**不是 DB 表**），`event_type="position_closed"`，`before/after` 记录 status 与 realized_pnl。

**判定：整仓平仓专用。** gross PnL（:210）、exit_fee（:231）、entry_fee 消费（:228）都硬编码整仓 `row["quantity"]`。

### 1.2 日损断路器从哪里读实现 PnL

文件：`aletheia/portfolio/account_loss.py` · `authoritative_account_realized_loss(execution_mode)`（`account_loss.py:26`）

**读取来源 = `positions` 表（`status='closed'`），求和 `positions.realized_pnl`**（已当场复核）：
```python
# account_loss.py:35-38 —— 通过 raw_account_events 读取
rows = raw_account_events(execution_mode=execution_mode, closed_after=today_start)
# account_loss.py:50 —— 求和 positions.realized_pnl
realized = round(sum(float(row["realized_pnl"]) for row in rows), 8)
```
`raw_account_events`（`aletheia/analytics/outcome_integrity.py:174-203`，已当场复核）查询 `positions` 表并**强制过滤 `status='closed'`**：
```python
conditions = ["status = 'closed'"]                    # outcome_integrity.py:186
...
rows = get_db().execute(
    "SELECT * FROM positions WHERE " + " AND ".join(conditions)
    + " ORDER BY datetime(closed_at) ASC, id ASC LIMIT ?", params).fetchall()  # :198-202
```
即：断路器**只读 `positions` 表**，从不读 fills / raw_account_events 表（这两者作为独立 DB 表**并不存在** —— `raw_account_events` 是函数名而非表名；不存在 `account_events` 表；现金账本表叫 `paper_account_events`）。

**paper 模式的额外对账** —— `_paper_ledger_snapshot`（`account_loss.py:61`）读 `paper_account_events` 现金账本，并**要求每个 position 恰好一条 `position_closed` 账本行**（已当场复核 `account_loss.py:65-86`）：
```python
if set(actual) != set(expected):
    return _unavailable("paper_ledger_mismatch", ...)          # :81-82
for position_id, amount in expected.items():
    ledger_amounts = actual.get(position_id, [])
    if len(ledger_amounts) != 1 or abs(ledger_amounts[0] - amount) > 1e-8:   # :85
        return _unavailable("paper_ledger_mismatch", ...)       # :86
```

**⚠️ 部分平仓对断路器的致命影响：**
- 断路器完全以 `positions.status='closed'` + `positions.realized_pnl` 为锚。**一次不把仓整平的部分平仓（position 仍 `open`）对 `raw_account_events` 零贡献 —— 它实现的亏损会被日损断路器完全漏掉，直到最终整平那一刻。** 这是减仓功能最大的风险。
- 即便把 `realized_pnl` 覆写到 open 行，`status='closed'` 过滤（outcome_integrity.py:186）仍会排除它。
- paper 模式下，若一个 position 产生多于一条 `position_closed` 账本行，`len(ledger_amounts) != 1`（:85）会**硬失败**返回 `available=False`；且账本表本身有 `UNIQUE(action, position_id)` 约束（migrations.py:2401），第二条 `position_closed` 插入会直接违反约束。

### 1.3 outcome-integrity / 分析层如何认定"一次平仓"

文件：`aletheia/analytics/outcome_integrity.py`（docstring 明确"不 import 任何 Execution/exchange 模块"，把一行 position 当作一个原子 outcome）

**1:1 假设** —— `assess_position_closure`（`outcome_integrity.py:135`）以 **position id** 为唯一 outcome 身份（假设每个 position 只平一次）：
```python
key = row.get("position_id") or row.get("id")           # :143
pnl = _to_float(row.get("realized_pnl"))                # :146
entry = _to_float(row.get("entry_price"))               # :147
quantity = _to_float(row.get("quantity"))               # :148
...
return _assessment_from_closure(closure,
    outcome_key=f"position:{key}",                      # :155  每仓一个 outcome_key
    horizon="realized_close",
    net_return_pct=pnl / (entry * quantity) * 100.0,    # :157  单一 (entry,qty,pnl) 元组
)
```
`outcome_key = f"position:{key}"`（:155）假定**每个 position 行一个 outcome**；`net_return_pct`（:157）用单一 `entry_price`/`quantity`/`realized_pnl` 计算，预设这三者描述一次完整往返。此处**不 join fills 表**，全部 eligibility 从 positions 行的列派生（`_STRUCTURED_KEYS` :48-55；`classify_closure` :84-132）。

**多次平仓会破坏其假设？—— 会：**
- 数据模型无处附着"open 仓上的第二次平仓"：`raw_account_events` 只见 `status='closed'` 行，且 `outcome_key`（`position:{id}`）在多次部分平仓下会**碰撞**（不唯一）。
- `net_return_pct`（:157）用加权均价 `entry_price` 与当前存储的 `quantity`；若部分平仓减了 quantity 却留 open，或最终平仓的 `realized_pnl` 只反映残余腿，报告的收益率会错。
- 减仓需要（a）每仓多个 `outcome_key`（新身份方案）或（b）聚合部分 fills —— 二者均不支持。

### 1.4 加仓 (scale-in / averaging-in) 现在如何记账 —— 减仓的逆操作

**四个 broker 的加权均价更新完全一致。** 参考 `aletheia/execution/bitget_broker.py:172-197`：
```python
existing = _find_open_position(symbol, market_type, direction, execution_mode, strategy_id)   # :172
if existing:
    new_qty = existing["quantity"] + fill_qty                                                  # :174 数量相加
    new_entry = (existing["entry_price"] * existing["quantity"] + fill_price * fill_qty) / new_qty  # :175 名义加权均价
    ...
    new_entry_fee_leg = round(fill_price * fill_qty * fee_rate, 8)                              # :186
    existing_entry_fee = existing.get("entry_fee")
    update_kwargs["entry_fee"] = round((existing_entry_fee or 0.0) + new_entry_fee_leg, 8)      # :188 entry_fee 累加
    ...   # liquidation_price 按新均价重算；stop_price 只收紧不放松
    update_position(existing["id"], **update_kwargs)                                            # :197
```
加一腿的记账规则（减仓路径必须对称遵守其逆）：
- **quantity**：`new_qty = quantity + fill_qty`（相加）。
- **entry_price**：名义加权均价 `(old_entry*old_qty + fill_price*fill_qty)/new_qty`。
- **entry_fee**：**跨腿累加** `(existing_entry_fee or 0.0) + fill_price*fill_qty*fee_rate`（"#140 fix"）。**这是减仓最关键的对称性隐患**：减仓时被平部分对应的 entry_fee 必须**按比例移除**，否则残余仓成本基础被高估。
- **liquidation_price**：按 `new_entry` 重算（仅展示/LLM 用）。
- **stop_price**：合并，只收紧不放松。

逐字一致的其它 broker（已核）：
- Paper：`aletheia/execution/paper_broker.py:77-101`（额外累加 `slippage_cost` :100，交易所 broker 无此项）。
- OKX：`aletheia/execution/okx_broker.py:207-214`。
- Binance：`aletheia/execution/binance_broker.py:127-133`。

**关键：没有任何路径会 *减少* quantity 或 *降低* entry_fee。** 加权均价在减仓时应保持不变（实现 PnL = `(exit-entry)*reduced_qty`），但 `entry_fee`/`slippage_cost` 是累积总额，减仓需按比例分摊 —— 而当前 `close_position` 一次性消费整个 `entry_fee`（pnl.py:228），仅因平仓永远整仓才成立。

**交易所平仓单在 venue 层已支持 reduce，只是没接到部分本地记账：**
- Bitget 对冲平仓：`tradeSide:"close"` + `holdSide`，用 `quantity = float(row["quantity"])`（`bitget_broker.py:330, 406, 411`）。
- OKX/Binance 平仓用 `reduce_only=True`（`okx_broker.py:468`、`binance_broker.py:280`），但仍传整仓 `quantity = float(row["quantity"])` 后调用 `close_position(..., exit_price=fill_price)`（okx_broker.py:515、binance_broker.py:327）。`reduceOnly` 仅用于保证 venue 平仓而非反手，**不用于平部分 size**。

### 1.5 相关表结构（`aletheia/storage/migrations.py`）

**`positions`**（DDL `migrations.py:140-170`；额外列经 ALTER `migrations.py:1758-1886`）关键列：`quantity`(:145, open 期间从不减)、`entry_price`(:146, scale-in 加权)、`realized_pnl`(:149, 断路器求和对象)、`status`(:154, 平仓信号)、`close_reason`/`exit_reasoning`(:157-158)、`close_category`/`exit_price_source`/`exit_fill_verified`/`outcome_eligible`/`outcome_semantics_version`(:159-166, outcome-integrity)、`closed_at`(:169, 断路器过滤)、`entry_fee`/`exit_fee`/`slippage_cost`(:1852-1854, 累积成本基础)、`pending_close_order_id/_status/_requested_at`(:1837-1839, 平仓单未确认时保持 open)。**无 `closed_quantity`/残余量/部分成交联结列。** Pydantic 镜像 `aletheia/storage/models.py:343-419`。

**`fills`**（DDL `migrations.py:129-138`）：`id, order_id(→orders), fill_price, fill_quantity, fee, fee_currency, fee_source, filled_at`。**fill 上无 `position_id`** —— 只经 `orders → positions.order_id`（开仓单）间接关联。开/平两腿都写 fill（平腿如 `bitget_broker.py:407`），但平腿 fill 只系在平仓 Order 上。技术上 fills 已能表达多个部分腿，但**无任何逻辑聚合它们算 PnL** —— PnL 完全由 positions 行驱动。模型 `models.py:328-340`；`insert_fill` `repositories.py:559-560`。

**`paper_account_events`**（现金账本，DDL `migrations.py:2387-2404`）：`action` CHECK 枚举含 `position_opened/position_closed`（:2389-2391），带 `amount_usdt`、`cash_balance_*`、`position_id`，并有 **`UNIQUE(action, position_id)`**（:2401）—— **结构性禁止每仓多于一条 `position_closed`**。写入者 `paper_account.settle_position_closed`（`aletheia/portfolio/paper_account.py:102-109`），由 `has_paper_account_position_event(...)` + `_account_lock` 守卫。

**`position_closed` 审计**：无专用 DB 表；每次平仓产两条独立记录 —— (1) JSONL 审计 `write_audit(event_type="position_closed")`（pnl.py:277-286）；(2) paper 现金账本行 `action='position_closed'`（仅 paper 模式）。

---

## 2. 交易所侧（官方 API）

**统一结论：三家都用「下一张 size 小于持仓的反向 reduce-only 单」实现部分平仓；三家的专用"平仓"端点都是整仓专用（无 size 参数）。reduce-only 均仅限单向 (one-way) 模式，对冲模式改用 side/position-side 参数。**

### 2.1 Bitget（USDT-FUTURES）

1. **部分平仓**：走 `POST /api/v2/mix/order/place-order`，指定 `size` < 持仓。
   - 单向模式：`reduceOnly=YES`。文档明确溢出处理：新+现存 reduce-only 单超过持仓量时，"系统按创建顺序取消现存 reduce-only 单直到总量合适"。
   - 对冲模式：`tradeSide=close` + 对应 `side`。部分平仓文档原话："市价单只会平掉总持仓量减去限价单占用量之后的剩余量"。
   - 专用 **Flash Close** `POST /api/v2/mix/order/close-positions` 是**整仓专用**（无 `size` 参数，市价平，`holdSide` 控方向）。
2. **reduce-only**：参数 `reduceOnly`，值 `YES`/`NO`（默认 `NO`）。文档原话："仅适用于单向持仓模式"。对冲模式用 `tradeSide`。MARKET/LIMIT 均支持（`orderType`）。
3. **实现 PnL 字段**：
   - 持仓端点 `/api/v2/mix/position/single-position`：`achievedProfits` = "Realized PnL（不含资金费和手续费）"。
   - 成交历史 `GET /api/v2/mix/order/fill-history`：每笔 `profit` = "Realized PnL"；另有 `baseVolume`、`feeDetail`（嵌套）、`tradeScope`、`posMode`。
4. **TP/SL 与部分平仓**：`POST /api/v2/mix/order/place-tpsl-order`，`planType`：`profit_plan`/`loss_plan`/`moving_plan`（这三者**需要 `size`**，固定量）vs `pos_profit`/`pos_loss`（**不需要 `size`**，跟踪全仓量）。另有 `place-pos-tpsl-order`（同时 TP+SL）。**固定 size 的 TP/SL 在部分减仓后是否自动调整/取消 —— 文档未说明**（已明确标记为未确认）。

### 2.2 OKX（USDT-margined SWAP）

1. **部分平仓**：走 `POST /api/v5/trade/order`，指定部分 `sz`（净模式加 `reduceOnly`，或长短模式用对应 `posSide`）。专用 `POST /api/v5/trade/close-position`（官方 Python SDK 确认参数 `instId, mgnMode, posSide, ccy, autoCxl, clOrdId, tag`，**无 `sz`**）→ 整仓市价平。
2. **reduce-only**：参数 `reduceOnly`（官方 SDK `place_order` 签名确认为 bool）。**是否仅限净模式 —— 无法从 okx.com 逐字确认**（文档站 SPA 截断），普遍理解为净/单向模式限定，报告中不作断言。
3. **实现 PnL 字段**（均从 okx.com Account 段逐字确认）：
   - `GET /api/v5/account/positions-history`：`realizedPnl` = `pnl + fee + fundingFee + liqPenalty + settledPnl`；`pnl` = "盈亏（不含手续费）"；`pnlRatio`。**其 `type` 枚举直接标注部分：`1`="Close position partially"，`2`="Close all"。**
   - `GET /api/v5/account/bills`：`pnl`。
   - `GET /api/v5/trade/fills`：通常返回 `fillPnl`（每笔实现 PnL）—— **无法从 okx.com 逐字确认**（fills 在截断的 Trade 段内），标记为未确认。
4. **OCO / bracket**：下单附带 `attachAlgoOrds`（每个附带自己的 `sz`、`tpTriggerPx`/`slTriggerPx`）；独立算法单 `POST /api/v5/trade/order-algo`（`ordType` 含 `conditional`/`trigger`/`oco`）；`POST /api/v5/trade/amend-algos` 可用 `newSz` 改单量。**全仓跟踪 TP/SL** 用 `closeFraction`（=1 平全仓，挂在持仓 `closeOrderAlgo` 下，okx.com 逐字确认 `closeFraction`="触发时平仓的仓位比例"）。**固定 sz 算法单在部分减仓后行为 —— 未从官方文档确认**。

### 2.3 Binance（USDⓈ-M Futures）

1. **部分平仓**：走 `POST /fapi/v1/order`，`quantity` < 持仓，加 `reduceOnly=true`（单向）或 `positionSide`（对冲）。**无专用部分平仓端点**；`closePosition=true` 只在 algo 单上且是整仓 close-all。
2. **reduce-only**：参数 `reduceOnly`，枚举 `true`/`false`（默认 `false`），MARKET/LIMIT 均适用。原话："不能在对冲模式下发送"。对冲模式改设 `positionSide`（LONG/SHORT，"对冲模式必须发送"）。
3. **实现 PnL 字段**：
   - `GET /fapi/v1/income`：`incomeType` = `REALIZED_PNL`，金额在 `income` 字段。
   - `GET /fapi/v1/userTrades`：每笔 `realizedPnl` = "Realized Pnl"；另有 `commission`、`positionSide`。
4. **OCO / bracket**：USDT-M **无专用 OCO/bracket 端点**；TP/SL 是 `POST /fapi/v1/algoOrder`（`algoType=CONDITIONAL`）下的独立条件单（`STOP_MARKET`/`TAKE_PROFIT_MARKET`/`TRAILING_STOP_MARKET`）。**全仓跟踪 TP/SL** 用 `closePosition=true`（触发平全部剩余量，天然跟踪缩减仓）。**固定 `quantity` 条件单不自动调整** —— 部分减仓后有效变成超量/孤儿单，除非用 `closePosition=true`。

### 2.4 跨交易所对比

| 维度 | Bitget (USDT-FUTURES) | OKX (SWAP) | Binance (USDⓈ-M) |
|---|---|---|---|
| **部分平仓法** | `place-order` 部分 `size` | `trade/order` 部分 `sz` | `fapi/v1/order` 部分 `quantity` |
| **专用平仓端点** | `close-positions`（**整仓，无 size**） | `close-position`（**整仓，无 sz**） | 无（仅 algo `closePosition=true` 全平） |
| **reduce-only 参数** | `reduceOnly`=`YES`/`NO` | `reduceOnly`(bool) | `reduceOnly`=`true`/`false` |
| **reduce-only 模式限制** | "仅单向模式"；对冲用 `tradeSide=close` | 净模式限定*（未逐字确认）*；对冲用 `posSide` | "对冲模式不可发送"；对冲用 `positionSide` |
| **实现 PnL（每笔）** | `profit`（fill-history） | `fillPnl`（fills）*（未确认）* | `realizedPnl`（userTrades） |
| **实现 PnL（聚合/持仓）** | `achievedProfits`（single-position） | `realizedPnl`/`pnl`（positions-history） | `REALIZED_PNL` in `income` |
| **原生 OCO** | `place-tpsl-order`(+`place-pos-tpsl`) | `attachAlgoOrds`+`order-algo`(含`oco`) | 无 OCO；条件单 |
| **全仓跟踪 TP/SL** | `pos_profit`/`pos_loss`（无 size） | `closeFraction`（=1 全仓） | `closePosition=true` |
| **固定 size TP/SL 部分减仓后自动调整** | 未文档化 | 未文档化（`amend-algos`+`newSz` 手动） | 未文档化（无自动重算；用 `closePosition=true`） |

**关键要点：**
- 三家的部分平仓**都靠下缩量反向单**，专用平仓端点全是整仓（无 size）。
- reduce-only 三家**都仅限单向模式**；对冲/长短模式用 side/position-side 参数标记平仓。
- **在部分减仓下能存活的 TP/SL 通用安全模式 = 各家的"全仓跟踪"变体**（Bitget `pos_profit`/`pos_loss`、OKX `closeFraction=1`、Binance `closePosition=true`）；三家均**未文档化固定 size 的 TP/SL 会随部分减仓自动缩量**，故固定 size TP/SL 在部分平仓后有超量/孤儿风险。

---

## 3. CCXT 兼容度

> 本项目**目前未依赖 CCXT**（各 broker 自实现 REST，见 §1.4 的 bitget/okx/binance broker 文件）。以下是若未来引入 CCXT 的兼容度评估。证据基于 CCXT master (v4.x) TypeScript 源码 (`ts/src/{bitget,okx,binance}.ts`) 与 Manual (`wiki/Manual.md`)。

### 3.1 统一 reduce-only 支持

`reduceOnly` 经 `createOrder(symbol, type, side, amount, price=undefined, params={})` 的 **`params` 字典**传入（非位置参数）：`params={'reduceOnly': True}`（Manual.md:6154/6169），统一 order 结构也回读 `'reduceOnly': false`（:5422）。它是 CCXT 在衍生品上的**约定**，每家在 `createOrderRequest` 读 `params['reduceOnly']`：
- **binance**（`createOrderRequest` ~binance.ts:6650）：`safeBool(params,'reduceOnly',false)`。USDT-M 单向模式直传原生 `reduceOnly`；对冲模式**整体丢弃**该 flag（见 §3.4）。
- **okx**（~okx.ts:3189）：`isReduceOnly = safeValue(params,'reduceOnly',false) || (closeFraction!==undefined)`。净模式直传；对冲模式经 `posSide` **模拟**并省略 flag（okx.ts:3216-3225，明确注释"reduceOnly is emulated and not natively supported"）。
- **bitget**（~bitget.ts:5334）：单向非触发单设原生 `reduceOnly='yes'/'YES'`；对冲/触发单译为 `posSide`（bitget.ts:5339-5340）。

**结论**：三家 USDT-M 都支持 `params={'reduceOnly': True}`；binance/okx/bitget 单向模式**原生**，okx 对冲**模拟**，binance 对冲**根本不传**（改 side-flip + `positionSide`）。

### 3.2 部分平仓 / closePosition

有统一 `closePosition(symbol, side=undefined, params={})` 与 `closeAllPositions(params={})`（Manual.md:8067-8102），但它是**整仓市价 flash-close，非部分减仓**。

| 交易所 | `closePosition` | `closeAllPositions` | 底层端点 |
|---|---|---|---|
| bitget | `true` | `true` | `privateMixPostV2MixOrderClosePositions`（整仓） |
| okx | `true` | `false` | `privatePostTradeClosePosition`（整仓） |
| binance | `false` | `false` | 未作为 CCXT 统一方法实现（binance.ts:44 注释：其原生 `closePosition` 语义与 CCXT 不同） |

**部分减仓的真正做法（三家统一）：无统一部分平仓助手**，下一张 `amount < 持仓` 的反向 `createOrder` + `params={'reduceOnly': True}`：
```python
# long 5 张，平 2 张：
exchange.create_order('BTC/USDT:USDT', 'market', 'sell', 2, None, {'reduceOnly': True})
```
- OKX 额外有原生 `closeFraction`（okx.ts:3164，如 `'0.5'` 平 50%），但是**交易所专用 params**，只能配条件/OCO 单，且 OKX 报错 `51328`（closeFraction 仅限 reduceOnly 单）。非统一字段。

`fetchPositions` 统一结构（Manual.md:8028-8056）：`contracts, contractSize, side, entryPrice, markPrice, notional, leverage, unrealizedPnl, marginMode, hedged, percentage` —— **含 `unrealizedPnl`，不含 `realizedPnl`**。

### 3.3 实现 PnL 查询

**统一规范未定义 `realizedPnl` 字段**（Position 结构只有 `unrealizedPnl` :8051；Trade 结构 :6580-6607 无 realizedPnl；Ledger 结构 :6709-6732 无 realizedPnl，实现 PnL 若有则作为 ledger `amount` 落在 `info`）。实际覆盖：
- **binance USDT-M**：`parseTrade` **不透出** `realizedPnl`（虽然 fapi payload 里有）→ 只能 `trade['info']['realizedPnl']`。规范来源 = `fetchLedger`（`fapiPrivateGetIncome`，`params={'incomeType':'REALIZED_PNL'}`）作为 ledger `amount`；`parsePosition` **无 realizedPnl**。
- **okx USDT-M**：`parsePosition` 挂了非规范 `'realizedPnl': safeNumber(position,'realizedPnl')`（okx.ts:6370，累积/持仓级）；`fetchLedger`（`privateGetAccountBills`）每条带 `pnl`（in `info`）；`fetchMyTrades` 的 `fillPnl`/`pnl` 仅在 `info`。
- **bitget USDT-M**：`parsePosition` 挂非规范 `'realizedPnl': safeNumberN(['pnl','curRealisedPnl','cumRealisedPnl'])`（bitget.ts:8432）；`fetchLedger` 支持，明细在 `info`。

**小结**：实现 PnL **不属于 CCXT 统一字段**（trades/ledger 完全不统一，positions 部分且非规范）。**每次平仓的实现 PnL 一般需读原始 `info`**（binance fills `realizedPnl`、okx/bitget `pnl`/`fillPnl`），或经 `fetchLedger` 对账（binance `incomeType=REALIZED_PNL`、okx `AccountBills.pnl`）。

### 3.4 已知坑

1. **对冲 vs 单向改变 reduceOnly 语义，且各家不同**：binance 对冲**静默丢弃** reduceOnly、改 side-flip + `positionSide`（binance.ts:6922-6929）；okx 对冲经 `posSide` **模拟**（3216-3225）；bitget 对冲/触发译为 `posSide`（5339）。binance 相关错误码：`-4062`（无效 reduceOnly）、`-2026`（该单型不支持 reduceOnly）、`-4137`（closePosition=true 时 quantity 须为 0）、`-4138`（closePosition=true 时 reduceOnly 须 true）。
2. **`positionSide`/`hedged` 命名未完全统一**：统一参数是 `params['hedged']=true`（Manual.md:6311-6313），但 okx 也收 `positionSide`、binance 用 `positionSide`(LONG/SHORT/BOTH)、bitget 用 `posSide`/`holdSide`。Manual 自承此区"仍在统一中，是 WIP"（:6140, :5442, :5449）。
3. **`amount` 多数合约市场以「张数 (contracts)」计，非基础货币**：Manual.md:5518 警告 `must be greater than minimum amount precision of 1`。换算 `contracts = base / market['contractSize']`。binance USDT-M linear 常为 1，但 inverse/部分市场不同 —— **务必除以 `market['contractSize']`**。直接影响部分平仓的量计算。
4. **`closePosition()` 在 bitget/okx 是整仓专用，binance 不可用**（`has['closePosition']=false`）；部分减仓必须自己下缩量 reduceOnly 单。`closeAllPositions` 仅 bitget=`true`。
5. **实现 PnL 非统一字段**（见 §3.3）：勿依赖 `fetchPositions()[i]['realizedPnl']` 写跨所代码 —— okx/bitget 有（非规范扩展、累积级），binance 无。
6. **部分成交语义靠统一 order status**（Manual.md:5428-5435）：`status='open'` 覆盖未成交/部分成交，`'closed'` 才是全成交；用 `filled`/`remaining` 判部分成交。reduceOnly 单部分成交后需**重查 `fetchPositions` 确认残余 `contracts`**。

---

## 4. 竞品做法（开源交易机器人）

**核心问题：部分平仓时，(A) 拆成独立结算的子仓，还是 (B) 保留单一持仓、按均价原地减 quantity 并累加实现 PnL？**

**一句话结论：全部 5 个机器人在 netting 语义下都用 (B) reduce-in-place。** 没有一个把活仓拆成独立结算子仓。Freqtrade/jesse/OctoBot 各保留一个对象、减量、减仓时**冻结均价 entry**、累加 `realized_profit`/`realised_pnl`。Nautilus 在 NETTING OMS 下同理，仅在 opt-in 的 HEDGING OMS 下才建独立仓。Hummingbot 的连接器级 `Position` 镜像交易所（单一 netted 仓），策略 v2 executor 层按 executor 算实现 PnL。

| 机器人 | 功能名 | 模型 | 单对象减量? | 减仓冻结 entry 均价? | 实现 PnL 字段/去处 |
|---|---|---|---|---|---|
| **Freqtrade** | `adjust_trade_position` / partial exits | **B** | `Trade`(`amount`,`stake_amount`) | 是 —— *"Exits will not influence the average entry rate"* | `Trade.realized_profit`(累加), `close_profit` |
| **Nautilus** | NETTING(B) vs HEDGING(A) OMS | **B** 默认 / A opt-in | NETTING 下 `Position` | 是 —— 减仓只动 `avg_px_close` | `Position.realized_pnl`(累加) |
| **Jesse** | 多点 `take_profit`, `reduce_position` | **B** | `Position`(`qty`,`entry_price`) | 是 —— 减仓不动 `entry_price` | exchange `add_realized_pnl()`（Position 只存未实现 `pnl`） |
| **Hummingbot** | position mode + `PositionExecutor` | **B** | 连接器 `Position`；executor 跟 `amount_to_close` | 是（breakeven 从开仓算） | `PositionSummary.realized_pnl_quote`, `global_pnl_quote` |
| **OctoBot** | `update_from_order` 减量, partial TP | **B** | `Position`(`size`,`entry_price`) | 是 —— `update_average_entry_price` 仅加仓时 | `Position.realised_pnl`(累加) + realised-PnL 交易 |

### 4.1 Freqtrade —— `adjust_trade_position` / Partial exits
- 单 `Trade` 持久化，`amount`/`stake_amount` 每次加仓/部分平仓更新（*"trade.stake_amount will always be updated on every additional entry and partial exit"*）；每张子单挂在同一 trade（内存随之增长）。
- **均价冻结**：*"Entry rates are calculated using weighted averages."* / *"Exits will not influence the average entry rate."* / *"Partial exit relative profit is relative to the average entry price at this point."*
- 实现 PnL（`freqtrade/persistence/trade_model.py::recalc_trade_from_orders`）：遍历 `self.orders`，`current_amount += tmp_amount*side`（exit side=-1）减量；每次 exit `prof = calculate_profit(exit_rate, exit_amount, avg_price)`；`close_profit_abs += prof.profit_abs`；`self.realized_profit = close_profit_abs`（累加），`close_profit = (close_profit_abs/total_stake)*leverage`。

### 4.2 Nautilus Trader —— NETTING vs HEDGING（显式 A/B 开关）
- **NETTING**（多数加密所默认）：*"One position per instrument ID"*，`{instrument_id}-{strategy_id}` 确定性仓 ID → 部分减仓保留单 `Position`（B）。
- **HEDGING**（opt-in）：*"Multiple positions per instrument ID… No automatic netting"* → 子仓（A）。
- Position 字段：`signed_qty`, `quantity`, `peak_qty`, `avg_px_open`, `avg_px_close`, `realized_pnl`, `realized_return`。减仓：`avg_px_close=_calculate_avg_px_close_px(...)`；`realized_pnl += _calculate_pnl(avg_px_open, last_px, last_qty)`（对着 `avg_px_open` 算，量 `fmin(quantity, |signed_qty|)` 封顶）。**减仓分支从不动 `avg_px_open`**。

### 4.3 Jesse —— 多点止盈 / `reduce_position`
- 单 `Position`（`entry_price`, `qty`, `pnl`），**open Position 无 `realized_pnl` 字段**，实现 PnL 记到**交易所余额**。
- `_mutating_reduce`：`estimated_profit = estimate_PNL(qty, position.entry_price, price, type)`；futures `exchange.add_realized_pnl(estimated_profit)`；`_update_qty(...,'subtract')`。**entry_price 不改**。`_mutating_increase` 才 `entry_price = estimate_average_price(...)`（加权）。

### 4.4 Hummingbot —— position mode + strategy-v2 executor
- 连接器 `Position`（`_entry_price, _amount, _unrealized_pnl`）镜像交易所仓（ONEWAY 下单一 netted），只跟 unrealized，无本地实现 PnL 累加器。
- 策略 v2 `PositionExecutor`：`amount_to_close = open_filled_amount - close_filled_amount`；部分平仓下 `amount_to_close` 的平仓单；单 executor 跟累积 filled（reduce-in-place）。`PositionSummary`：`realized_pnl_quote`、`global_pnl_quote = unrealized + realized - cum_fees`。

### 4.5 OctoBot —— `realised_pnl` on Position
- 单 `Position`（`entry_price, exit_price, size, already_reduced_size, realised_pnl`）。
- 减仓 `_update_realized_pnl_from_size_update`：`realised_pnl_update = -size_update/self.size * self.unrealized_pnl`；`self.realised_pnl += realised_pnl_update`；发 `create_realised_pnl_transaction(average_entry_price=self.entry_price, closed_quantity=size_update, ...)`。**`update_average_entry_price` 仅在开/加仓调用**（`self.size==ZERO` 或增加分支守卫）；减仓走 `_update_exit_data` 从不改 `entry_price`。

### 4.6 综合 —— (B) reduce-in-place 的四条黄金规则
1. **保留一个持仓对象**，减量，不生子仓（Nautilus 是唯一提供 A 的，且门禁在 opt-in HEDGING OMS，专为 FX/原生分开长短仓的 venue）。
2. **减仓时冻结加权均价 entry**（entry 只在加/开仓变，全部机器人明确如此）。
3. **部分实现 PnL = (exit_price − avg_entry_price) × closed_qty × multiplier**，对着冻结均价算，**累加进 running `realized_pnl`**（而非从头重算）。Nautilus 把可平量 `fmin(qty, |signed_qty|)` 封顶、OctoBot 把 closed PnL 量按 entry 量封顶 —— **值得抄的护栏**。
4. **单独跟一个平均*出场*价**供报告（Nautilus `avg_px_close`、OctoBot `exit_price`/`already_reduced_size`），不扰动 entry 基础。

**(B) 的好处**（机器人隐含押注的）：一个规范持仓/量与 venue 自身 netted 仓对齐、实现/未实现拆分更简单、单一累加器代替对账众多子账本。**明确的代价**：freqtrade 警告子单全挂单 trade 增内存。**A 仅在 venue 本身持独立长短仓时才合理**（FX ECN hedging）—— 这正是 Nautilus 把 HEDGING 设为刻意 opt-in 的原因。

---

## 5. 给 Aletheia 的推荐模型 + 落地要点

### 5.1 推荐：原地部分平仓 (reduce-in-place)，而非拆分建仓子仓

**理由（三重收敛）：**
1. **与竞品一致**：全部 5 个主流开源机器人在 netting 语义下都用 (B)（§4）。
2. **与本项目现有模型天然对称**：Aletheia 已是 netting 单仓（每 symbol/direction/strategy 一个 open 仓，`_find_open_position`），加仓已用加权均价 + entry_fee 累加（§1.4）。减仓是它的精确逆运算，无需引入新的子仓身份体系。
3. **与交易所一致**：三家 venue 都是下缩量 reduce-only 单、返回 netted 仓（§2）；跨 paper_internal/bitget/okx/binance 都能用同一本地记账映射。

**拒绝拆分子仓 (A) 的原因**：会打爆 §1.3 的 `outcome_key=f"position:{id}"` 唯一性假设、§1.2 的 `paper_account_events` UNIQUE 约束、以及需要 fills↔position 的多对一联结（当前 fills 无 `position_id`，§1.5）。改造面远大于 (B)。

### 5.2 记账数学（减仓 = §1.4 加仓的逆）

设减仓量 `r`（`0 < r < quantity`），出场价 `exit_price`，原仓 `q0=quantity, e=entry_price, F=entry_fee(累积), S=slippage_cost(累积)`：
- **实现 PnL（毛）** = `(exit_price - e) * r`（long）/ `(e - exit_price) * r`（short）—— **对着冻结的加权均价 `e`**。
- **entry_fee 按比例分摊到被平部分** = `F * r / q0`；**exit_fee** = `exit_price * r * fee_rate`。
- **本次实现 PnL（净）** = 毛 − `F*r/q0` − `exit_fee`。
- **残余仓更新**：`quantity = q0 - r`；`entry_price = e`（**不变**）；`entry_fee = F * (q0-r)/q0`（按比例保留）；`slippage_cost = S * (q0-r)/q0`；`realized_pnl += 本次净`（累加）；`status` 保持 `'open'`；`liquidation_price` 按不变的 `e` 无需重算。
- **护栏**（抄 Nautilus/OctoBot）：`r = min(r, q0)`；当 `r == q0` 时退化为现有整仓 `close_position`（`status='closed'`）。

> 注意：这套分摊使得**最终把整仓分多次平完的净 PnL 之和 = 一次性整平的净 PnL**（entry_fee 完全用尽、exit_fee 按各段 exit_price 计），保持记账闭合。

### 5.3 关键：如何正确喂进日损断路器 + 分析（这是必须先解的阻塞项）

§1.2 已证明：断路器**只读 `positions WHERE status='closed'` 的 `realized_pnl`**。若减仓保持 `open`，实现的亏损会被**完全漏掉** —— 这会打穿风控。**必须改造读取来源，二选一：**

- **方案 R1（推荐）：新增 per-close 台账表** `position_close_events`（`position_id, closed_qty, exit_price, realized_pnl, entry_fee_alloc, exit_fee, closed_at, execution_mode, close_reason`），每次减仓/整平写一行。
  - 让 `raw_account_events` / `authoritative_account_realized_loss` 改为**从该台账按 `closed_at` 求和 `realized_pnl`**（而非从 positions 行），这样部分平仓与整平被统一、无遗漏地计入日损。
  - 分析层 `outcome_integrity` 的 `outcome_key` 从 `position:{id}` 改为 `close:{close_event_id}`（每段一个），`net_return_pct` 用该段的 `(exit, e, closed_qty, realized_pnl)`；outcome eligibility 分类下沉到台账行。彻底解决 §1.3 的碰撞与收益率错算。
  - paper 现金账本：新增 `action='position_reduced'` 或改 `paper_account_events` 的 `UNIQUE(action, position_id)` 为 `UNIQUE(close_event_id)`；`_paper_ledger_snapshot` 的 `len(ledger_amounts) != 1` 硬校验（§1.2）改为**按台账逐段对账**。
- **方案 R2（更小改动、但语义更弱）**：减仓时把本次净 PnL **累加进 open 行的 `realized_pnl`**，并放宽 `raw_account_events` 的 `status='closed'` 过滤为"含 open 行的当日增量" —— 但这会破坏 `realized_pnl` 在整平时的"最终值"语义、且难以按 `closed_at` 归到正确交易日（open 行只有一个时间戳）。**不推荐**，仅作对比。

### 5.4 具体落地要点（要改哪些函数）

1. **`aletheia/portfolio/pnl.py:191` `close_position`**：加 `quantity: float | None = None`（None=整仓，保持向后兼容）。按 §5.2 实现分摊：gross（:210）、exit_fee（:231）、entry_fee 消费（:228）全部改为按 `r` 而非 `row["quantity"]`；`r < q0` 时 `update_position(quantity=q0-r, entry_fee=F*(q0-r)/q0, slippage_cost=S*(q0-r)/q0, realized_pnl=旧+本次净, status='open')`（不置 closed）；`r == q0` 走现有整仓分支。
2. **新台账写入**：`close_position` 内每次都写一行 `position_close_events`（方案 R1），并把 `write_audit` 的 `event_type` 对部分平仓改为 `position_reduced`（整平仍 `position_closed`）。
3. **`aletheia/analytics/outcome_integrity.py:174 raw_account_events` + `:135 assess_position_closure`**：改为从 `position_close_events` 读取、`outcome_key=close:{id}`、`net_return_pct` 用该段经济学。
4. **`aletheia/portfolio/account_loss.py:26/50` + `_paper_ledger_snapshot:61`**：改为按台账求和；paper 对账改为逐段（去掉 `len==1` 硬校验，§1.2:85）。
5. **`aletheia/portfolio/paper_account.py:102`**：加 `settle_position_reduced`，或调整 `paper_account_events` 的 UNIQUE 约束（migrations.py:2401）与 CHECK 枚举（:2389-2391）。
6. **各 broker 平仓路径**（`bitget_broker.py:330/406/411`, `okx_broker.py:468/515`, `binance_broker.py:280/327`）：把传给 venue 的 `quantity` 与传给 `close_position` 的量从整仓 `float(row["quantity"])` 改为**请求的减仓量 `r`**；OKX/Binance 单向已用 `reduce_only=True`（保留），Bitget 对冲用 `tradeSide=close` 缩量（§2.1）。**对冲/单向模式的 reduce-only 语义分歧**（§2.4）按各所处理。
7. **TP/SL 联动（跨所一致性）**：部分减仓后固定 size TP/SL 会超量/孤儿（§2.4），落地时应改用**全仓跟踪 TP/SL**（Bitget `pos_profit`/`pos_loss`、OKX `closeFraction`、Binance `closePosition=true`），或减仓后主动改单量（OKX `amend-algos`+`newSz`）。这属于执行层，可作为后续迭代。
8. **schema/migrations**：新增 `position_close_events` 表 + `positions` 无需新列（quantity/entry_fee/realized_pnl 复用）。

### 5.5 跨执行模式一致性
- 记账数学（§5.2）与台账（§5.3 R1）与 execution_mode 无关，paper_internal/bitget/okx/binance 共用。
- 差异仅在 venue 下单（§2 各所 reduce-only/对冲参数）与实现 PnL 校验来源（本地记账为准，venue 返回值 `profit`/`realizedPnl`/`achievedProfits` 作为对账参考，§2.3）—— 与现有"本地 positions 行为 PnL 权威、venue 值仅对账"的架构一致。
- 若未来引入 CCXT，实现 PnL 需从 `info` 或 `fetchLedger` 读（§3.3），但推荐仍以**本地按均价计算**为权威，venue 值仅做偏差告警。

---

## 6. 未决问题

1. **日损归日 (calendar-day) 归属**：部分平仓分多天发生时，每段 PnL 按各自 `closed_at` 归入对应交易日（方案 R1 台账天然支持）。但**残余仓的未实现 PnL 是否也应在减仓日参与日损评估**？现行断路器只看已实现，需确认产品语义。
2. **entry_fee 分摊 vs 交易所实际计费**：§5.2 按 `F*r/q0` 线性分摊 entry_fee，但交易所对开仓费是一次性收取、不随减仓退还。本地分摊只影响"每段净 PnL 的账面拆分"，整仓闭合后总额一致；但**单段的 net_return_pct 会含分摊费** —— 是否可接受，需产品确认（竞品多数在"含费/不含费"上有明确口径，如 Bitget `achievedProfits` 不含费、OKX `realizedPnl` 含费）。
3. **对冲模式**：本项目目前是单向还是对冲？§2 表明 reduce-only 仅单向、对冲用 side/position-side。若涉及对冲模式，减仓路径需分叉（Bitget `tradeSide=close`）。需确认 `_find_open_position` 的模式假设。
4. **OKX `fillPnl` / `reduceOnly` 净模式限制**未能从 okx.com 官方文档逐字确认（SPA 截断，§2.2）；若接 OKX，需用真实沙盒验证 `fillPnl` 字段名与 reduce-only 是否被净模式拒绝。
5. **固定 size TP/SL 在部分减仓后的自动调整**：三家均未文档化（§2.4）。若 Aletheia 减仓时挂着固定 size TP/SL，需实机验证是否变超量/孤儿单，或统一改用全仓跟踪变体（§5.4 落地要点 7）。
6. **`position_close_events` 与现有 `fills` 的关系**：是否让平腿 fill 挂 `close_event_id`（当前 fill 无 position_id，§1.5）以支持成交级审计？还是台账独立于 fills？需定审计粒度。
7. **部分平仓的最小量 / 精度**：venue 有最小下单量与 `contractSize`（CCXT 坑 §3.4.3）；减仓量 `r` 需按各所精度取整，残余仓量也须 ≥ 最小量，否则应强制整平。需定"减仓后残余量过小自动整平"的阈值策略。
8. **学习/分析管线的向后兼容**：改 `outcome_key` 为 `close:{id}` 会否影响历史 outcome 的 `outcome_semantics_version="oi-v1"`（migrations.py:166）？可能需 `oi-v2` 语义版本迁移，历史整仓平仓映射为单段。
