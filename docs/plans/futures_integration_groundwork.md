# Futures Integration — Groundwork Analysis
> 状态：研究完成（2026-06-17）。Deep research workflow 已完成，最佳实践对照已补充（§六）。
> 这份文档是写完整实现计划之前的基础分析，不是实施指令。

---

## 一、现有合约能力盘点（已可用，无需新建）

### 1. API 层 — `exchanges/bitget/client.py`
完整支持合约，**已通过 demo 账户实证**（DECISIONS.md 2026-06-09 有记录）：

| 能力 | 实现状态 |
|------|---------|
| 合约行情 ticker / OHLCV | ✅ mix market 路径 |
| 合约下单（开仓）| ✅ `tradeSide:"open"` |
| 合约下单（平仓）| ✅ hedge-mode `tradeSide:"close"` + `holdSide` |
| 查询持仓 `get_positions()` | ✅ `/api/v2/mix/position/all-position` |
| 查询合约账户权益 `get_mix_account()` | ✅ `/api/v2/mix/account/accounts` |
| 查询挂单 / 撤单 / 成交 | ✅ mix 路径全覆盖 |
| 实时成交价回查 `_get_order_detail()` | ✅ 市价单同步成交验证 |
| Position dataclass（含 leverage、mark_price）| ✅ |

**关键细节**：账户跑的是 **hedge mode（双向持仓）**，这是 Bitget demo 账户的默认设置。平仓机制已实证：`side` 与开仓方向相同 + `holdSide` 指定持仓方向，**不用 `reduceOnly`**。

### 2. Execution 层 — `execution/bitget_broker.py`
- `execute_live_order(market_type="usdt_futures")` → 走合约路径
- `close_demo_position()` → 完整 hedge-mode 平仓实现
- 先写本地 DB 再调交易所（防止交易所成交但 DB 丢失）
- 使用交易所确认的成交价，不伪造

### 3. Risk Engine — `risk/engine.py`
已有合约专属检查：
- Check 2：`enabled_markets.usdt_futures` 开关
- Check 10：`max_futures_leverage_live=2` / `max_futures_leverage_demo=5`（硬编码上限，agent 不可修改）
- Check 7：`allow_short_demo` / `allow_short_live` 控制空头许可

### 4. 配置层 — `config/schema.py`
```python
# ExchangeConfig
enabled_markets: {"spot": True, "usdt_futures": True}

# RiskConfig
max_futures_leverage_live: int = 2
max_futures_leverage_demo: int = 5
allow_short_demo: bool = True
allow_short_live: bool = True

# MarketDataConfig
observed_instruments: list[str] = []   # "exchange:BASE/QUOTE:market_type" 格式
```

### 5. Symbol 抽象 — `data/symbols.py` + `trading/instruments.py`
- `InstrumentId(exchange, symbol, market_type)` 三元组体系完整
- `MARKET_SPOT / MARKET_PERP / MARKET_FUTURES` 常量已定义
- `to_ccxt_perp()` / `to_bitget()` 转换函数完整
- `observed_instruments_from_config()` 已支持多市场类型

### 6. 衍生品数据 — `data/collectors/derivatives.py`
- 资金费率（FundingRateSnapshot）✅
- 持仓量（OpenInterestSnapshot）✅
- 爆仓数据（LiquidationEvent）✅，含 Binance fallback

---

## 二、精准缺口（4 个，需要填补）

### 缺口 1：合约张数换算（ctVal）— **最高优先级，影响下单正确性**

**问题**：`sizing.py` 输出 USDT notional（如 $30），但 Bitget 合约 `size` 字段要求"张数"。
两者换算缺失，当前如果下合约单，`quantity` 会被当成币数传入，导致严重尺寸偏差。

**Bitget USDT 永续合约 ctVal（每张合约面值）**：
```
BTC/USDT perp: 1 张 = 0.001 BTC
ETH/USDT perp: 1 张 = 0.01  ETH
SOL/USDT perp: 1 张 = 1     SOL
```

**换算公式**：
```
币数 = notional_usdt / price
张数 = 币数 / ctVal = (notional_usdt / price) / ctVal
```

**影响文件**：`trading/sizing.py` 或 execution adapter 的入口处

**方案选择**（待深研结果后拍板）：
- 方案 A：在 `sizing.py` 里加 `notional_to_contracts(notional, price, symbol)` 工具函数
- 方案 B：在 `execution/bitget_broker.py` 里做，sizing 层只管 notional，adapter 边界做换算

NautilusTrader 和 Hummingbot 的处理层级待 deep research 结果确认。

---

### 缺口 2：资金费率未计入 PnL — **影响长期持仓准确性**

**问题**：`portfolio/pnl.py` 的 `close_position()` 只算价差 PnL，没有扣除/累加持仓期资金费率。
`derivatives.py` 已在采集资金费率数据，但没有流向 PnL。

**影响**：资金费率 8h 一次，年化约 ±5%~30%（极端市场更高）。对于持仓超过数小时的合约，PnL 失真。

**当前 `pnl.py` 逻辑**：
```python
gross_pnl = (exit - entry) × qty   # 纯价差，无资金费率
```

**需要增加**：
```python
# 伪代码
funding_paid = sum(rate × notional for rate in funding_snapshots_during_hold)
realized = gross_pnl - entry_fee - exit_fee - funding_paid  # long 支付资金费率
# short 方向：funding_paid 为负数时反而是收入
```

**依赖**：需要 storage 层能按时间段查询资金费率快照，目前 `FundingRateSnapshot` 模型已存在，待确认是否写入 DB。

---

### 缺口 3：强平价格 / margin ratio 监控 — **持仓安全守护**

**问题**：Risk engine 只在开仓前检查杠杆上限，持仓中不监控接近强平的风险。
合约持仓如果价格反向移动，没有任何预警机制。

**全仓模式（crossed margin）近似强平价格**：
```
多头强平价 ≈ entry × (1 - 1/leverage + 维持保证金率)
空头强平价 ≈ entry × (1 + 1/leverage - 维持保证金率)
# Bitget 维持保证金率 BTC: ~0.4%, ETH: ~0.4%
```

**需要增加**：
- `Position` 模型加 `liquidation_price` 字段
- scheduler 定期计算 margin ratio，接近阈值时触发 kill switch 或警告
- dashboard 展示强平价格

---

### 缺口 4：持仓模式（position_mode）未显式配置

**问题**：`client.py` 注释里提到账户跑的是 hedge mode，但这是隐式假设，不在 `config/schema.py` 里。
如果 Bitget 账户设置从 hedge mode 改成单向持仓（one-way），平仓逻辑会出错（`holdSide` 参数无效）。

**需要增加**：
```python
# ExchangeConfig
position_mode: str = "hedge"  # "hedge" | "one_way"
```
并在 `bitget_broker.py` 的平仓逻辑里根据此字段决定是否传 `holdSide`。

---

## 三、不需要动的部分

| 模块 | 理由 |
|------|------|
| `InstrumentId` 体系 | 已支持 perp/futures，直接用 |
| `symbols.py` 转换函数 | 完整，无需修改 |
| `risk/engine.py` 框架 | 15 个检查项、锁机制、kill switch 集成全部保留，只加字段 |
| hedge-mode 下单/平仓逻辑 | 已通过实证，不动 |
| `derivatives.py` 数据采集 | 已完整，只需打通到 PnL |

---

## 四、待 Deep Research 补充的内容

以下问题等 NautilusTrader / Hummingbot / Jesse 研究结果回来后补充：

1. **ctVal 换算在哪层做最合理**（instrument 层 vs execution adapter 层 vs sizing 层）？
2. **funding rate PnL** 业界标准计算方式，以及如何与 position lifecycle 绑定？
3. **margin ratio 监控** 触发阈值的业界惯例（如 >80% margin ratio 警告，>95% 强制平仓）？
4. **双向持仓数据模型**：position 表是否需要按 (symbol, hold_side) 做联合唯一键？

---

## 五、标的与杠杆参数建议（草案）

### 合约标的（与现货平行，不引入新标的）
```
BTC/USDT perp (BTCUSDT_UMCBL)
ETH/USDT perp (ETHUSDT_UMCBL)
SOL/USDT perp (SOLUSDT_UMCBL)
```

### 系统杠杆上限（RiskConfig，已存在，确认当前值合理）
```python
max_futures_leverage_demo: int = 5   # demo 阶段：最高 5x
max_futures_leverage_live: int = 2   # live 阶段：最高 2x（极保守）
```
Paper 阶段建议先在 1x 跑通逻辑，再逐步放开。

### 持仓模式
**结论**：保持现有 hedge mode（双向持仓），代码已实证，不切换。
后续在 config 里显式化（缺口 4），而不是改底层逻辑。

---

---

## 六、NautilusTrader 最佳实践对照（Deep Research 结论）

> 来源：对 NautilusTrader 源码（master/develop 分支）的多轮对抗性验证，4 条 high-confidence 发现。
> OpenAlice/Open ALS 未找到足够文档化的 instrument 抽象；Hummingbot/Jesse/freqtrade 本轮未产出通过验证的 claim。

### 发现 1：Instrument 类型层面显式分叉（不用运行时 if/else）

NautilusTrader 用独立类型区分：
- `CurrencyPair` — 现货
- `CryptoPerpetual` — 永续合约（无到期字段）
- `CryptoFuture` — 加密带到期日合约
- `FuturesContract` — 传统可交割期货（含 `expiration_ns` 字段）

**对 aletheia 的启示**：`InstrumentId` 的 `market_type` 字段（`spot` / `perp` / `futures`）已实现同等分叉，思路一致，无需改造。

---

### 发现 2：杠杆存在 Account 层，不嵌入 Instrument（关键架构决策）

NautilusTrader 的杠杆倍率存储在 `MarginAccount._leverages: dict[InstrumentId, Decimal]`，不是 Instrument 对象的字段。保证金计算统一公式：

```
margin = notional_value(quantity, price) × (1 / leverage)
现货等效：leverage = 1 → margin = notional（全额占用）
```

**对 aletheia 的直接意义**：
- `config/schema.py` 里的 `max_futures_leverage_demo/live` 是正确的存放位置（Account/Config 层，而非 Instrument 层）✅
- 合约 notional 计算：`notional = quantity_in_contracts × ctVal × price`，这一步在 **Instrument 层**完成（配合 ctVal 常量），杠杆在 **Account/Risk 层**应用
- 现货和合约可以共用同一套 risk engine 保证金路径，只是杠杆参数不同

---

### 发现 3：同一 MarginAccount 内支持逐仓 + 全仓（双模式并存）

```python
_margins: dict[InstrumentId, MarginBalance]       # 逐仓（isolated）
_account_margins: dict[Currency, MarginBalance]    # 全仓（cross-margin）
# 路由：margin.instrument_id is None → 全仓；否则 → 逐仓
```

**对 aletheia 的启示**：Bitget 目前合约用全仓（crossed）模式，单账户内不需要两套账户类。未来若需要支持逐仓，只需在同一账户对象里加 per-instrument 字典，不用重构账户层。

---

### 发现 4：现货与合约执行路由通过 client_id / account_type 在适配层隔离

NautilusTrader 每个 client 实例只处理一种 `account_type`（SPOT 或 FUTURES），同时交易两类需配置两个独立 client（`BINANCE_SPOT` + `BINANCE_FUTURES`），策略通过下单时的 `client_id` 路由。

**对 aletheia 的启示**：`bitget_broker.py` 当前通过 `market_type` 参数在同一 broker 内分路，这与 NautilusTrader 方案等效但更轻量，适合当前 MVP 规模。若未来需要分账户隔离，可参考此模式拆分。

---

### 未解决问题（本轮 research 未产出验证 claim）

这三个问题在本轮 deep research 中由于 rate limit 导致验证失败，属于**已知未知**，需要后续单独研究或实测：

1. **资金费率如何进入已实现 PnL**：NautilusTrader 是否有 FundingSettlement 事件，还是策略层手动处理？
2. **Hedge mode 数据模型**：NautilusTrader HEDGING OMS 是否通过 position ID 后缀区分多空？
3. **ctVal 张数换算在哪层做**：Instrument 层的 `notional_value()` 还是策略/sizing 层？

---

## 七、下一步

1. 基于本文档写 `futures_integration_plan.md`（完整实现计划，Phase A–E）
2. 先解决未解决问题 3（ctVal 换算层级），因为它直接影响 Phase C 的 sizing 设计
3. 按 Phase 分支实施：配置层 → 数据层 → Risk 扩展 → Execution 扩展 → Agent 感知
