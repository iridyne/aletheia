# Free Data Sources Implementation Plan

**Stage**: datasrc-overhaul worktree — Track 2
**Date**: 2026-06-28
**Scope**: Hyperliquid (衍生品聚合) + DefiLlama (链上/DeFi 指标)
**SDD-aligned**: spec-first, then implementation

---

## 1. 数据形状与归一化

### 1.1 Hyperliquid — POST https://api.hyperliquid.xyz/info

#### Request A: `{"type": "metaAndAssetCtxs"}`
响应是 `[universe_list, ctx_list]` 两个等长数组：

```
universe_list[i] = {
  "name": "BTC",          # 资产名（无 USDT 后缀）
  "szDecimals": 5,
  "maxLeverage": 40,
  "marginTableId": 56,
  "onlyIsolated": false   # 可选
}
ctx_list[i] = {
  "funding": "-0.0000090567",       # 当前8h资金费率（HyperLiquid 1h interval）
  "openInterest": "33476.0213",     # 单位: 合约基础资产（BTC等）
  "prevDayPx": "60429.0",
  "dayNtlVlm": "1016219245.29",    # 24h notional 成交量 USD
  "premium": "-0.0005156783",       # mark - oracle 溢价
  "oraclePx": "60115.0",
  "markPx": "60083.0",
  "midPx": "60083.5",
  "impactPxs": ["60083.0", "60084.0"],
  "dayBaseVlm": "16849.19644"       # 24h 基础资产成交量
}
```

**归一化规则**:
- `symbol`: `f"{name}/USDT"` → 内部 canonical BTC/USDT
- `funding_rate`: `float(ctx["funding"])` — 这是每次结算的利率，Hyperliquid 1h intervals → annualized × 24 × 365
- `open_interest_usd`: `float(ctx["openInterest"]) * float(ctx["markPx"])` — HL OI 是以合约单位，需要乘以 mark price 换算 USD
- `mark_price`: `float(ctx["markPx"])`
- `exchange`: `"hyperliquid"`
- 写入表: `funding_rate_snapshots` + `open_interest_snapshots`

#### Request B: `{"type": "predictedFundings"}`
响应是 `[asset_name, [[venue, {fundingRate, nextFundingTime, fundingIntervalHours}], ...]]` 列表。

```
[
  "0G",
  [
    ["BinPerp", {"fundingRate": "-0.00014783", "nextFundingTime": 1782648000000, "fundingIntervalHours": 4}],
    ["HlPerp",  {"fundingRate": "0.000009091", "nextFundingTime": 1782640800000, "fundingIntervalHours": 1}],
    ["BybitPerp",{"fundingRate": "-0.0000562",  "nextFundingTime": 1782648000000, "fundingIntervalHours": 4}]
  ]
]
```

用途: 提取 Hyperliquid 自身预测资金费率（HlPerp），作为补充信号存入 `funding_rate_snapshots`（`exchange="hyperliquid_predicted"`）。

### 1.2 DefiLlama

#### Request 1: GET https://stablecoins.llama.fi/stablecoins?includePrices=true
```
{
  "peggedAssets": [
    {
      "id": "1",
      "name": "Tether",
      "symbol": "USDT",
      "gecko_id": "tether",
      "pegType": "peggedUSD",
      "pegMechanism": "fiat-backed",
      "circulating": {"peggedUSD": 184904740487.97},
      "circulatingPrevDay": {"peggedUSD": 184904740489.60},
      "price": 1.0,
      ...
    }
  ]
}
```

**归一化**: 汇总 stablecoin 总市值 = sum of `circulating.peggedUSD`

#### Request 2: GET https://api.llama.fi/v2/chains
```
[
  {
    "gecko_id": "ethereum",
    "tvl": 52000000000.0,
    "tokenSymbol": "ETH",
    "cmcId": "1027",
    "name": "Ethereum",
    "chainId": 1
  },
  ...
]
```

**归一化**: Top-N 链 TVL 汇总，总 TVL = sum of all `tvl`

#### Request 3: GET https://api.llama.fi/overview/dexs
```
{
  "total24h": 12000000000,
  "total7d": 80000000000,
  "protocols": [...]
}
```

**归一化**: 取 `total24h`, `total7d` 作为全网 DEX 成交量指标

---

## 2. 存储设计

### 2.1 复用现有表

Hyperliquid 数据写入已有表：
- `funding_rate_snapshots`: symbol, exchange, funding_rate, funding_rate_annualized, next_funding_time, mark_price, observed_at
- `open_interest_snapshots`: symbol, exchange, open_interest_usd, open_interest_coin, oi_change_1h_pct, long_short_ratio, observed_at

使用已有 `insert_funding_rate_snapshot()` / `insert_open_interest_snapshot()`（`repositories.py:1547/1565`）。

### 2.2 新建表: onchain_snapshots

```sql
CREATE TABLE IF NOT EXISTS onchain_snapshots (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,                  -- 'defillama'
    metric_type TEXT NOT NULL,             -- 'stablecoin_mcap' | 'chain_tvl' | 'dex_volume'
    total_usd REAL,                        -- 汇总 USD 值
    change_24h_pct REAL,                   -- 24h 变化 %
    top_entries TEXT DEFAULT '[]',         -- JSON: top-N 链/协议列表
    raw_summary TEXT DEFAULT '{}',         -- JSON: key metrics
    observed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_onchain_source_ts
    ON onchain_snapshots (source, metric_type, observed_at DESC);
```

---

## 3. Spec Claim 清单

### Hyperliquid Claims (HL-*)

| ID | 级别 | 断言 |
|----|------|------|
| HL-S01 | L2a | `hyperliquid.py` 文件存在，导出 `HyperliquidCollector` |
| HL-S02 | L2a | `_HL_INFO_URL` 模块级常量已定义 |
| HL-S03 | L2a | `collect()` 返回 `{"funding": [...], "open_interest": [...], "errors": [...]}` |
| HL-S04 | L2b | 喂真实 fixture → BTC/USDT OI > 0 且 funding_rate 非 None |
| HL-S05 | L2b | OI 值 = openInterest × markPx（USD换算正确） |
| HL-C01 | L2c | HTTP 错误不 raise，返回 errors 列表 + 空列表 |
| HL-C02 | L2c | 写入 funding + OI 表后，DB 里有对应行（用真实 fixture）|

### DefiLlama Claims (DL-*)

| ID | 级别 | 断言 |
|----|------|------|
| DFL-S01 | L2a | `defillama.py` 文件存在，导出 `DefiLlamaCollector` |
| DFL-S02 | L2a | `_DL_*_URL` 三个模块级常量已定义 |
| DFL-S03 | L2a | `collect()` 返回 `{"snapshots": [...], "errors": [...]}` |
| DFL-S04 | L2a | `migrations.py _DATA_COLLECTORS_DDL` 包含 `onchain_snapshots` 建表 DDL |
| DFL-S05 | L2a | `repositories.py` 导出 `insert_onchain_snapshot` 和 `latest_onchain_snapshots` |
| DFL-S06 | L2b | 喂真实 fixture → stablecoin 总市值 > 1e11（USDT 184B+，明显大） |
| DFL-S07 | L2b | 写入 onchain_snapshots 后 DB 中有对应行，`metric_type` 正确 |
| DFL-C01 | L2c | 三个 endpoint 每个独立 try/except，一个失败不影响其余 |

---

## 4. 集成 TODO（给主控）

主控负责修改以下共享文件，本 worktree **不触碰**：

### 4.1 config/schema.py — 新增字段

```python
# 在 MarketDataConfig 下追加:
enable_hyperliquid: bool = True          # 启用 Hyperliquid 衍生品数据
enable_defillama: bool = True            # 启用 DefiLlama 链上数据
hyperliquid_symbols: list[str] = []      # 空=跟随主 symbols 列表; 非空则覆盖
defillama_top_chains: int = 20           # 保存前 N 链 TVL
```

### 4.2 scheduler.py — 新增 job

```python
# Phase B data_collectors 函数中，与 CoinGeckoTopCollector 同级追加:
from aletheia.data.collectors.hyperliquid import HyperliquidCollector
from aletheia.data.collectors.defillama import DefiLlamaCollector

# Hyperliquid: 每 5 分钟（衍生品数据变化快）
# DefiLlama: 每 15 分钟（链上数据变化慢）

# 建议 job 名: "hyperliquid_derivatives"，频率 5min
# 建议 job 名: "defillama_onchain"，频率 15min
```

### 4.3 diagnostics/doctor.py — 新增探针

```python
# 建议探针逻辑（仅参考，具体实现由主控决定）:
from aletheia.storage.repositories import latest_open_interest, latest_onchain_snapshots
# Hyperliquid 探针: 检查 exchange='hyperliquid' 的 funding_rate_snapshots
# DefiLlama 探针: 检查 metric_type='stablecoin_mcap' 的 onchain_snapshots
# 两者都应在 30 分钟内有数据，否则 warn
```

### 4.4 frontend Settings 页面

在 Settings.tsx "Macro & Sentiment Signals" 区域或新建 "Chain & Derivatives" 区域，需要加入：

```
// 开关文案（英文）:
"Enable Hyperliquid derivatives data (OI / funding rate)"
"Enable DefiLlama on-chain data (stablecoin cap / TVL / DEX volume)"
// 对应 config 字段名:
enable_hyperliquid
enable_defillama
```

### 4.5 tests/test_spec_conformance.py — 追加 import

```python
# 在 _import_all_specs() 列表中追加:
from tests.specs import spec_hyperliquid
from tests.specs import spec_defillama
```

---

## 5. Coinalyze 设计（待 key，暂不实现）

**跨所聚合**需要付费 key，暂不实现，避免造新死 stub。

### 设计备注

- **API endpoint**: `https://api.coinalyze.net/v1/open-interest?symbols=BTCUSDT_PERP.A&api_key=KEY`
- **关键字段**: `open_interest`, `open_interest_usd`, `long_short_ratio`, `volume`
- **为何需要 key**: Coinalyze 无免费无认证 tier，所有 endpoint 均需 API key
- **未来接入路径**: config 加 `coinalyze_api_key`，collector 检查 key 存在才运行（仿 FRED 模式）；否则 skip 并记录 `errors=["no_key"]`，不抛异常
- **接入价值**: 可覆盖 Binance/OKX/Bybit/Bitget 汇总 OI，补充 Hyperliquid 只有自身 OI 的局限

---

## 6. 新表 Schema 摘要

```sql
onchain_snapshots:
  id TEXT PK
  source TEXT NOT NULL          -- 'defillama'
  metric_type TEXT NOT NULL     -- 'stablecoin_mcap' | 'chain_tvl' | 'dex_volume'
  total_usd REAL                -- 汇总 USD
  change_24h_pct REAL           -- 24h 变化 %
  top_entries TEXT DEFAULT '[]' -- JSON array: [{name, tvl/volume/mcap, ...}, ...]
  raw_summary TEXT DEFAULT '{}' -- JSON object: key scalar metrics
  observed_at TEXT NOT NULL
```
