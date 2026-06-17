# Futures Integration — Implementation Plan
> 版本：v1.0（2026-06-17）  基于 groundwork_analysis.md + deep research 结论
> 执行前必读：AGENTS.md / CLAUDE.md。change-controlled 模块改动须有明确理由。

---

## 总览

目标是把现有骨架（API 层、execution 层、risk engine 全部已支持合约）激活并补齐 4 个精准缺口，**不引入新模块、不改架构**。5 个 Phase 顺序推进，每个 Phase 自含测试验证。

```
Phase A  配置层显式化          ← 无风险，只加字段
Phase B  ctVal 张数换算        ← 影响下单正确性，最高优先级
Phase C  强平价格监控          ← 持仓安全守护
Phase D  资金费率计入 PnL      ← 合约长期持仓准确性
Phase E  Agent 感知 & 激活     ← 端到端联通，paper 跑通合约
```

---

## Phase A：配置层显式化

### 目标
把隐式的合约运行假设写进 `config/schema.py`，消除"隐式知识"风险。

### A-1：`ExchangeConfig` 加 `position_mode` 字段

**文件**：`aletheia/config/schema.py`

```python
class ExchangeConfig(BaseModel):
    # 已有字段 ...
    position_mode: str = "hedge"  # "hedge"（双向持仓）| "one_way"（单向）
    # 注：Bitget demo 账户默认 hedge mode，已通过实证（DECISIONS.md 2026-06-09）
```

**理由**：`bitget_broker.py` 的平仓逻辑依赖 `holdSide`（hedge mode 专属），若账户设置变更会静默出错。显式字段让代码可以防御性检查，也让 dashboard 能展示当前模式。

### A-2：`ExchangeConfig` 加合约标的列表

```python
class ExchangeConfig(BaseModel):
    # 已有字段 ...
    futures_symbols: list[str] = Field(
        default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    )  # 合约标的（canonical 格式）；对应现货 observed_symbols
```

**理由**：当前合约标的硬散在各处，统一到 config 让 agent 和 scheduler 有单一来源。

### A-3：`RiskConfig` 补合约专属风控参数

```python
class RiskConfig(BaseModel):
    # 已有字段（保留）：max_futures_leverage_live=2, max_futures_leverage_demo=5
    # 新增：
    futures_margin_warning_ratio: float = 0.80   # 保证金率 ≥80% 触发警告
    futures_margin_force_close_ratio: float = 0.95  # 保证金率 ≥95% 触发强制平仓
```

### A-4：`MarketDataConfig` 激活 perp instrument 观测

`observed_instruments` 字段已存在但默认空，**补充 perp 条目**到 `data/config.yaml`（运行态，不是代码）的默认示例中，并在 `instruments.py` 里增加 `futures_instruments_from_config()` helper。

**文件**：`aletheia/trading/instruments.py`

```python
def futures_instruments_from_config(cfg) -> list[InstrumentId]:
    """返回合约标的 InstrumentId 列表（perp market type）。"""
    exchange = cfg.market_data.ccxt_exchange or "binance"
    return [
        InstrumentId.from_parts(exchange, sym, MARKET_PERP)
        for sym in (cfg.exchange.futures_symbols or [])
    ]
```

### A 验证
```bash
uv run pytest tests/test_config.py -q        # schema 字段默认值正确
uv run python -c "from aletheia.config.schema import AletheiaConfig; c=AletheiaConfig(); print(c.exchange.position_mode, c.exchange.futures_symbols)"
```

---

## Phase B：ctVal 张数换算（最高优先级）

### 背景
Bitget USDT 永续合约下单 `size` 字段是**张数**，不是币数：
- BTC/USDT perp：1 张 = 0.001 BTC
- ETH/USDT perp：1 张 = 0.01  ETH
- SOL/USDT perp：1 张 = 1     SOL

换算公式：`张数 = floor((notional_usdt / price) / ctVal)`

NautilusTrader 设计参考：ctVal 常量定义在 **Instrument 层**，sizing 层只输出 notional，**adapter 边界**（execution 入口处）做最终换算。这是 aletheia 要采用的方案。

### B-1：定义 ctVal 常量表

**文件**：`aletheia/trading/instruments.py`（追加）

```python
import math

# Bitget USDT-FUTURES 合约面值（ctVal）：1张合约代表的基础资产数量
# 来源：Bitget API /api/v2/mix/market/contracts
FUTURES_CT_VAL: dict[str, float] = {
    "BTC/USDT": 0.001,
    "ETH/USDT": 0.01,
    "SOL/USDT": 1.0,
}
_DEFAULT_CT_VAL = 1.0  # 未知标的安全回退（1张=1单位，等同现货逻辑）


def get_ct_val(symbol: str) -> float:
    """返回 canonical symbol 对应的合约张数面值（ctVal）。"""
    canon = to_canonical(symbol)
    return FUTURES_CT_VAL.get(canon, _DEFAULT_CT_VAL)


def notional_to_contracts(notional_usdt: float, price: float, symbol: str) -> float:
    """将 USDT notional 转为 Bitget 合约张数（向下取整到整数张）。

    params:
        notional_usdt: 目标持仓名义价值（USDT）
        price:         当前市价（USDT）
        symbol:        canonical symbol（BTC/USDT）

    returns:
        合约张数（float，整数值，最小为 1）
    """
    if price <= 0 or notional_usdt <= 0:
        return 0.0
    ct_val = get_ct_val(symbol)
    coins = notional_usdt / price
    contracts = math.floor(coins / ct_val)
    return max(float(contracts), 1.0)  # 最少 1 张，避免 0 张下单


def contracts_to_notional(contracts: float, price: float, symbol: str) -> float:
    """反向换算：合约张数 → USDT notional（用于 PnL、风控计算）。"""
    ct_val = get_ct_val(symbol)
    return contracts * ct_val * price
```

### B-2：`execution/bitget_broker.py` 在 execution 边界做换算

**文件**：`aletheia/execution/bitget_broker.py`

在 `execute_live_order()` 里，当 `market_type == "usdt_futures"` 时，在调用 `client.place_order()` 前把 `quantity`（notional 或币数）转为张数：

```python
from aletheia.trading.instruments import notional_to_contracts, contracts_to_notional

# 在 execute_live_order() 内，place_order 调用前：
if market_type == "usdt_futures":
    exchange_quantity = notional_to_contracts(quantity, price, symbol)
    # 反算实际 notional 用于本地 Fill/Position 记录（保持内部一致性）
    actual_notional = contracts_to_notional(exchange_quantity, price, symbol)
else:
    exchange_quantity = quantity
    actual_notional = quantity * price
```

**注意**：本地 DB 的 `positions.quantity` 继续存**张数**（这是交易所返回的单位），`pnl.py` 计算时统一用 `contracts_to_notional()` 还原 notional。这与 `get_positions()` 返回的 `Position.quantity` 字段语义保持一致（已是张数）。

### B-3：`trading/sizing.py` 不变，文档注释说明

`compute_position_size()` 继续输出 USDT notional（职责不变），调用方（paper_trader / execution gateway）负责在 execution 边界做换算。在 `sizing.py` 顶部注释中明确说明这一边界。

### B 验证
```bash
uv run pytest tests/test_instruments.py -q    # 新增 ctVal 换算单元测试
# 手动验证：BTC notional=30, price=60000 → 张数=floor(30/60000/0.001)=0→max=1
uv run python -c "
from aletheia.trading.instruments import notional_to_contracts
print(notional_to_contracts(30, 60000, 'BTC/USDT'))   # 期望: 1.0
print(notional_to_contracts(120, 60000, 'BTC/USDT'))  # 期望: 2.0
print(notional_to_contracts(50, 2000, 'ETH/USDT'))    # 期望: 2.0
"
```

---

## Phase C：强平价格 & Margin Ratio 监控

### 背景
全仓模式（crossed margin）下，Bitget 的强平机制是账户整体保证金不足触发，但对单个仓位近似估算：

```
多头强平价 ≈ entry_price × (1 - 1/leverage + mmr)
空头强平价 ≈ entry_price × (1 + 1/leverage - mmr)
# mmr（维持保证金率）BTC/ETH ≈ 0.004（0.4%），SOL ≈ 0.005
```

实时 margin ratio（保证金使用率）从 `get_mix_account()` 返回的 `available + unrealizedPL` 可以推算。

### C-1：`storage/models.py` 的 `Position` 加字段

```python
class Position(BaseModel):
    # 已有字段 ...
    leverage: int | None = None              # 开仓杠杆（合约专用）
    liquidation_price: float | None = None   # 估算强平价
    margin_ratio: float | None = None        # 当前保证金率（0~1）
```

同步更新 `storage/migrations.py`（加 `ALTER TABLE` migration）和 `storage/repositories.py`（`update_position` 支持新字段）。

### C-2：`trading/instruments.py` 加强平价格计算

```python
# 各标的维持保证金率（mmr）
FUTURES_MMR: dict[str, float] = {
    "BTC/USDT": 0.004,
    "ETH/USDT": 0.004,
    "SOL/USDT": 0.005,
}

def calc_liquidation_price(
    entry_price: float,
    direction: str,   # "long" | "short"
    leverage: int,
    symbol: str,
) -> float:
    """全仓模式下单仓位的估算强平价（近似，用于预警，非精确值）。"""
    mmr = FUTURES_MMR.get(to_canonical(symbol), 0.005)
    if direction == "long":
        return entry_price * (1 - 1 / leverage + mmr)
    else:
        return entry_price * (1 + 1 / leverage - mmr)
```

### C-3：`portfolio/pnl.py` 的 `mark_positions_to_market()` 同步更新强平价

在 `persist=True` 路径中，对 `market_type == "usdt_futures"` 的持仓，计算并写入 `liquidation_price`：

```python
from aletheia.trading.instruments import calc_liquidation_price

# mark_positions_to_market() 内 persist=True 路径：
if persist and p.get("market_type") == "usdt_futures" and p.get("leverage"):
    liq_price = calc_liquidation_price(
        p["entry_price"], p["direction"], p["leverage"], p["symbol"]
    )
    margin_ratio = _calc_margin_ratio(price, p["entry_price"], p["direction"], p["leverage"])
    update_position(p["id"], current_price=price, unrealized_pnl=upnl,
                    liquidation_price=liq_price, margin_ratio=margin_ratio)
```

### C-4：`risk/engine.py` 加持仓中强平预警检查

**新增独立函数**（不影响 `evaluate_proposal()` 主流程）：

```python
def check_liquidation_risk() -> list[dict]:
    """扫描所有开仓合约持仓，返回接近强平的预警列表。由 scheduler 定期调用。"""
    from aletheia.config.loader import load_config
    cfg = load_config()
    warnings = []
    for p in list_positions(status="open"):
        if p.get("market_type") != "usdt_futures":
            continue
        mr = p.get("margin_ratio")
        if mr is None:
            continue
        warn_ratio = cfg.risk.futures_margin_warning_ratio
        force_ratio = cfg.risk.futures_margin_force_close_ratio
        if mr >= force_ratio:
            warnings.append({"position_id": p["id"], "symbol": p["symbol"],
                              "margin_ratio": mr, "level": "critical"})
        elif mr >= warn_ratio:
            warnings.append({"position_id": p["id"], "symbol": p["symbol"],
                              "margin_ratio": mr, "level": "warning"})
    return warnings
```

调用点：在 `server/routes/` 的 portfolio endpoint 返回预警，并在 scheduler 的 `portfolio_snapshot` job 后追加一次 `check_liquidation_risk()` 调用（若有 critical 级别则写 audit log）。

### C 验证
```bash
uv run pytest tests/test_risk_engine.py tests/test_position_manager.py -q
uv run python -c "
from aletheia.trading.instruments import calc_liquidation_price
# BTC long 3x entry=60000 → liq ≈ 60000*(1-1/3+0.004)=40240
print(calc_liquidation_price(60000, 'long', 3, 'BTC/USDT'))
"
```

---

## Phase D：资金费率计入已实现 PnL

### 背景
- `funding_rate_snapshots` 表**已存在**，`insert_funding_rate_snapshot()` / `latest_funding_rate()` 已有
- 缺少**按时间范围查询**的接口，以及从持仓生命周期角度累加资金费率的逻辑

### D-1：`storage/repositories.py` 加按时间段查询

```python
def list_funding_rate_snapshots(
    symbol: str,
    since: str | None = None,   # ISO timestamp
    until: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """按时间段查询资金费率快照，用于 PnL 核算。"""
    where_parts = ["symbol = ?"]
    params: list = [symbol]
    if since:
        where_parts.append("observed_at >= ?")
        params.append(since)
    if until:
        where_parts.append("observed_at <= ?")
        params.append(until)
    return _select_all(
        "funding_rate_snapshots",
        where=" AND ".join(where_parts),
        params=tuple(params),
        limit=limit,
        order_by="observed_at ASC",
    )
```

### D-2：`portfolio/pnl.py` 的 `close_position()` 扣除资金费率

```python
def _calc_funding_cost(
    symbol: str,
    direction: str,
    quantity_contracts: float,
    opened_at: str,
    closed_at: str,
    price: float,
) -> float:
    """计算持仓期间资金费率总成本（正值=支出，负值=收入）。
    
    多头支付正资金费率，空头收取正资金费率（Bitget 标准行为）。
    """
    from aletheia.storage.repositories import list_funding_rate_snapshots
    from aletheia.trading.instruments import contracts_to_notional

    snapshots = list_funding_rate_snapshots(symbol, since=opened_at, until=closed_at)
    if not snapshots:
        return 0.0

    total_cost = 0.0
    notional = contracts_to_notional(quantity_contracts, price, symbol)
    for snap in snapshots:
        rate = float(snap.get("funding_rate") or 0)
        # 多头：rate>0 付出，rate<0 收入；空头反向
        cost = notional * rate
        if direction in ("short", "sell"):
            cost = -cost
        total_cost += cost
    return total_cost
```

在 `close_position()` 中，仅对 `market_type == "usdt_futures"` 的持仓调用：

```python
# close_position() 内，紧接 gross_pnl 计算之后：
funding_cost = 0.0
if row.get("market_type") == "usdt_futures":
    funding_cost = _calc_funding_cost(
        row["symbol"], row["direction"], row["quantity"],
        row.get("opened_at", ""), closed_at_iso, exit_price,
    )
realized = gross_pnl - entry_fee - exit_fee - funding_cost
```

### D-3：`storage/models.py` `Position` 加 `funding_cost_accrued` 字段

```python
class Position(BaseModel):
    # 已有字段 + Phase C 新增 ...
    funding_cost_accrued: float = 0.0   # 累计已结算资金费率（USDT）
```

scheduler 的 `portfolio_snapshot` job 可以定期更新此字段（非阻塞，不影响 close 逻辑）。

### D 验证
```bash
uv run pytest tests/test_portfolio.py -q
# 检查：对一个已有 funding_rate_snapshots 记录的 BTC perp 持仓平仓后
# realized_pnl 包含资金费率扣减
```

---

## Phase E：端到端激活与 Agent 感知

### 目标
让 paper_trader 能够开合约仓，agent 能够感知合约特有的上下文（资金费率、杠杆、强平价）。

### E-1：`agents/paper_trader.py` 支持合约提案

当前 `paper_trader` 生成 `TradeProposal` 时 `market_type` 固定为 `"spot"`（需要确认）。改为从 opportunity 的 `market` 字段派生：

```python
# TradeProposal 构造时：
market_type = opp.get("market", "spot")  # opportunity.market 已有此字段
leverage = cfg.risk.max_futures_leverage_demo if market_type == "usdt_futures" else None
proposal = TradeProposal(
    ...
    market_type=market_type,
    leverage=leverage,
)
```

### E-2：`data/context_builder.py` 加合约上下文

agent 的市场上下文 prompt 中加入：
- 当前资金费率（年化）
- 持仓的强平价格（若已有合约仓）
- 合约账户权益 vs 现货账户权益

```python
# context_builder.py 追加 _build_futures_context()
def _build_futures_context(symbols: list[str]) -> str:
    from aletheia.storage.repositories import latest_funding_rate
    lines = []
    for sym in symbols:
        fr = latest_funding_rate(sym)
        if fr:
            rate_pct = float(fr.get("funding_rate_annualized", 0)) * 100
            lines.append(f"{sym} funding rate: {rate_pct:.1f}% annualized")
    return "\n".join(lines) if lines else ""
```

### E-3：`config/data.yaml` 激活合约标的观测

在运行态配置中，把 BTC/ETH/SOL 的 perp instrument 加入 `observed_instruments`：
```yaml
market_data:
  observed_instruments:
    - "binance:BTC/USDT:spot"
    - "binance:ETH/USDT:spot"
    - "binance:SOL/USDT:spot"
    - "binance:BTC/USDT:perp"
    - "binance:ETH/USDT:perp"
    - "binance:SOL/USDT:perp"
```

**注**：这是运行态变更，通过 dashboard UI 的 settings 页面修改（CLAUDE.md 规则：config 变更走 UI，不直接改 yaml）。

### E-4：Dashboard 新增合约状态展示

在 portfolio 页面加入：
- 合约持仓卡片显示：杠杆、强平价、资金费率
- 保证金率进度条（颜色：绿/黄/红 对应 <80%/80-95%/≥95%）

**文件**：`frontend/src/` 相关组件（Phase E 最后做，不阻塞前面 Phase）

### E 验证
```bash
# 全量测试
uv run pytest tests/ -q --tb=line
# 手动验证：创建一个 BTC/USDT usdt_futures 的 opportunity
# 确认 paper_trader 生成合约 TradeProposal → risk engine 通过 → 执行 → 有正确张数的订单
uv run aletheia gateway restart --no-browser
# 观察 data/logs/ 中的 paper_trader 日志
```

---

## 变更范围汇总

| Phase | 文件 | 变更类型 |
|-------|------|---------|
| A | `config/schema.py` | 加字段（3个） |
| A | `trading/instruments.py` | 加 helper 函数 |
| B | `trading/instruments.py` | 加 ctVal 常量 + 2个换算函数 |
| B | `execution/bitget_broker.py` | 改 execute_live_order()（**change-controlled**） |
| C | `storage/models.py` | Position 加 3 个字段 |
| C | `storage/migrations.py` | 加 ALTER TABLE migration |
| C | `storage/repositories.py` | update_position 支持新字段 |
| C | `trading/instruments.py` | 加强平价格计算函数 |
| C | `portfolio/pnl.py` | mark_positions_to_market() 写强平价 |
| C | `risk/engine.py` | 加 check_liquidation_risk()（**change-controlled**） |
| D | `storage/repositories.py` | 加 list_funding_rate_snapshots() |
| D | `portfolio/pnl.py` | close_position() 扣资金费率 |
| D | `storage/models.py` | Position 加 funding_cost_accrued |
| E | `agents/paper_trader.py` | 支持 usdt_futures market_type |
| E | `data/context_builder.py` | 加资金费率上下文 |
| E | `frontend/src/` | 合约状态展示组件 |

**change-controlled 模块**（`risk/`、`execution/`）改动均为**只增加**，不改现有检查逻辑。

---

## 不动的部分（明确排除）

- `exchanges/bitget/client.py`：API 层完整，不动
- `risk/engine.py` 的 15 个 proposal 检查：架构不变，只加 `check_liquidation_risk()` 函数
- `data/symbols.py`：symbol 抽象完整，不动
- Hedge mode 下单/平仓逻辑：已实证，不动
- `storage/migrations.py` 的现有迁移：只追加新 migration，不改旧的

---

## 执行顺序 & 依赖关系

```
A（独立，可立即开始）
  ↓
B（依赖 A 的 futures_symbols 字段）
  ↓
C（依赖 B 的 contracts_to_notional，以及 A 的 margin 配置）
  ↓
D（依赖 C 的 Position 字段扩展）
  ↓
E（依赖 A-D 全部完成）
```

每个 Phase 完成后独立运行相关测试，不等全量。全量测试放 E 完成后运行一次。
