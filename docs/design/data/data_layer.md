# Data Layer Refactoring Plan

**Created:** 2026-06-08  
**Last updated:** 2026-06-23 (Phase 3 状态修正 + 实际 provider chain 补文档)  
**Status:** IMPLEMENTED — shipped 2026-06-08 (see AI_HANDOFF.md + DEVLOG 2026-06-08)  
**Scope:** Market data sources only. Execution/account/order paths untouched.

---

## 0. 参考系深读摘要（OpenAlice + OpenBB）

> 本节是代码级分析，不是 README 摘要。克隆仓库路径：`C:\Users\ollama\Projects\openalice-ref`

### 0.1 OpenBB 的核心数据架构（`packages/opentypebb`）

OpenBB 是一个 **Provider/Fetcher/OBBject 三层架构**，Aletheia 数据层完全可以移植这个模式（用 Python 实现，不引入 TS/Node）。

**Provider 层** — 登记数据源：
```
Provider(name, description, credentials, fetcherDict)
  fetcherDict: { "CryptoHistorical": YFinanceCryptoHistoricalFetcher, ... }
```
每个 provider 注册时声明需要哪些凭证（如 `fmp_api_key`），凭证名自动加上 provider 前缀。公开数据源（yfinance、cboe、federal_reserve 等）`requireCredentials = false`。

**Fetcher 层** — TET 三段管道（Transform-Extract-Transform）：
```python
# 对应 fetcher.py 的静态方法模式
class YFinanceCryptoHistoricalFetcher(Fetcher):
    @staticmethod
    def transform_query(params) -> QueryParams   # 1. 验证入参 + 补默认值
    @staticmethod
    async def extract_data(query, creds) -> raw  # 2. 调 HTTP/SDK 拿原始数据
    @staticmethod
    def transform_data(query, raw) -> [Data]     # 3. 标准化到 canonical model
```
关键设计：**方法全是静态的，Fetcher 永不实例化**。这使 mock 测试极其简单。

**OBBject 信封** — 统一响应容器：
```python
OBBject(results=[...], provider="yfinance", warnings=[], extra={})
```
携带 `provider` 字段，调用方永远知道数据来自哪里。与 Aletheia 现在的问题（返回结果无 source 标注）正好对立。

**Standard Models vs Provider Models 的分层**：
- `standard-models/crypto-historical.ts` — 定义跨 provider 的 canonical 字段（date/open/high/low/close/volume）
- `providers/yfinance/models/crypto-historical.ts` — 继承 standard model，加 yfinance 特有字段（interval 枚举）
- 两者通过 schema extension 实现，不是继承对象

**Registry + QueryExecutor**：
```
QueryExecutor.execute("yfinance", "CryptoHistorical", params, credentials)
  → registry.get("yfinance")
  → provider.fetcherDict["CryptoHistorical"]
  → filteredCredentials
  → fetcher.fetchData(params, creds)
```
调用方不关心 provider 实现细节，只需要 `(provider_name, model_name, params)` 三元组。

**yFinance 的 symbol 转换**（代码实测）：
```typescript
// providers/yfinance/models/crypto-historical.ts:42
// BTCUSD → BTC-USD (Yahoo Finance format)
const yahooTickers = tickers.map(t => {
  if (!t.includes('-') && t.length > 3) {
    return t.slice(0, -3) + '-' + t.slice(-3)  // "BTCUSDT" → "BTC-USDT"
  }
  return t
})
```
注意：`yfinance` 对加密的 symbol 格式是 `BTC-USD`，不是 `BTC/USDT`（CCXT）也不是 `BTCUSDT`（Bitget）。

**CBOE 等无凭证 provider** 如何处理降级：`requireCredentials = false`，QueryExecutor 会跳过凭证校验，不报错。

### 0.2 OpenAlice 的 CCXT 架构（`services/uta/src/domain/trading/brokers/ccxt/`）

OpenAlice 的 CCXT 集成是 **执行层**（IBroker 实现），不是数据层——这与我们的设计方向一致。但其 CcxtBroker 里有几个数据相关的工程细节值得记录：

**CCXT 在 OpenAlice 里的 symbol 格式**：`BTC/USDT:USDT`（CCXT 统一格式），不是 Bitget 的 `BTCUSDT`。`contractToCcxt()` 函数做双向转换。

**Stablecoin 规范化**：
```typescript
// CcxtBroker.ts:52
const STABLECOIN_TO_USD = new Set(['USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'FDUSD', 'PYUSD', 'USDE', 'USDP'])
function normalizeQuoteCurrency(quote: string): string {
  return STABLECOIN_TO_USD.has(quote.toUpperCase()) ? 'USD' : quote
}
```
行情数据（quote ticker）统一到 `USD` 基准，避免 USDT/USDC 的微小价差污染 PnL 计算。

**Spot holding 合成为 Position**：`fetchSpotHoldings()` 把余额（BTC: 0.01）转换成持仓（BTC/USDT long @ last_price）。这解决了 Aletheia 当前 `exchange_sync` 的同一问题——它不是数据层问题，是账户层的计算。

**批量 ticker 拉取**：
```typescript
// 一次 fetchTickers(symbols[]) 代替 N 次 fetchTicker(symbol)
tickers = await this.exchange.fetchTickers(symbols)
```
Aletheia 现在每次 `build_market_context` 对每个 symbol/timeframe 独立 HTTP 调用，这是 rate limit 风险的根源。

**关键结论：OpenAlice 的 CCXT 只做执行层（broker），数据层用 OpenBB（yfinance/fmp/cboe）。两层严格分离。这正是我们的设计目标。**

---

## 1. 现状诊断

### 1.1 当前数据流（单一来源）

```
Bitget API (公开端点)
    │
    ├─ collect_market_snapshots() [每 5 min]
    │     BitgetClient.get_ticker() → SQLite market_snapshots + JSONL
    │
    ├─ build_market_context() [market_observer 触发，每 15 min]
    │     BitgetClient.get_ohlcv() × 3 timeframes × N symbols
    │     → indicators.py → markdown → LLM prompt
    │     ⚠ OHLCV 每次实时拉取，无缓存
    │
    └─ GET /api/market/ohlcv [前端图表按需]
          BitgetClient.get_ohlcv() → 直传前端
          ⚠ 直连 BitgetClient，无抽象层
```

### 1.2 当前数据消费者汇总

| 消费点 | 文件 | 当前数据来源 | 变更后 |
|---|---|---|---|
| `collect_market_snapshots` | `trading/market_data.py:23` | `BitgetClient.get_ticker` | CCXT provider |
| `build_market_context` | `trading/market_context.py:180` | `BitgetClient.get_ohlcv` | CCXT provider |
| `paper_trader` 价格查询 | `agents/paper_trader.py:82` | `latest_snapshot()` 读 SQLite | 无变化（读 SQLite） |
| 前端 K 线图 | `server/routes/data.py:220` | `BitgetClient.get_ohlcv` | CCXT provider |
| `exchange_sync` | `portfolio/exchange_sync.py:141` | `BitgetClient`（账户） | **保持 Bitget**（执行层） |
| `reconciliation` | `execution/reconciliation.py:47` | `BitgetClient`（订单） | **保持 Bitget**（执行层） |
| `bitget_broker` | `execution/bitget_broker.py:15` | `BitgetClient`（下单） | **保持 Bitget**（执行层） |

### 1.3 核心问题

1. **交易所强耦合**：行情数据与执行数据走同一 `BitgetClient`。Bitget 不可用时分析链全断。
2. **OHLCV 无缓存**：`build_market_context()` 每次触发拉取 3 timeframe × N symbol，agent 触发频繁时 rate limit 风险高。
3. **Symbol 格式硬编码**：`BTCUSDT` 是 Bitget 私有格式。`instruments.py` 内的 `BITGET_GRANULARITY` 也是交易所私有映射。
4. **Config 层职责混乱**：`ExchangeConfig.tradable_symbols` 既驱动执行（允许交易哪些品种）又驱动行情拉取（观察哪些品种），两个关注点耦合在同一字段。
5. **OHLCV 未持久化**：K 线只存活在内存里，历史回看能力为零。

---

## 2. 目标架构

```
                    ┌──────────────────────────────────┐
                    │        MarketDataProvider         │  ← 抽象层 (Protocol)
                    │  get_ticker(symbol) → Ticker      │
                    │  get_ohlcv(symbol, tf, n) → [Bar] │
                    └──────┬───────────┬────────────────┘
                           │           │
              ┌────────────▼──┐   ┌────▼────────────────┐
              │ CCXTProvider  │   │ yFinanceProvider     │
              │ Binance (主力) │   │ SPY/VIX/DXY (宏观)  │
              │ Bybit  (备用) │   └─────────────────────┘
              └───────────────┘
                    ↑ 仅行情
                    
              ┌─────────────────────────────────────────┐
              │           BitgetClient                   │
              │  (不变) — 仅用于:                        │
              │  place_order / get_balances /            │
              │  get_positions / get_fills /             │
              │  get_account / cancel_order              │
              │  + 当连接 Bitget 时的实时价格验证         │
              └─────────────────────────────────────────┘
```

**核心原则：**
- 行情数据（价格、K 线、指标）→ CCXT/yFinance
- 执行数据（余额、持仓、订单、成交）→ 对应交易所 client（当前为 BitgetClient）
- Bitget 的 `get_ticker` / `get_ohlcv` 仅在"执行上下文确认使用 Bitget"时作为补充校验

---

## 3. 变更清单（按阶段）

### Phase 0：基础抽象层（无行为变化）

**目标：** 建立接口定义 + Symbol 规范化，不改变任何现有逻辑。

| # | 变更 | 文件 | 类型 |
|---|---|---|---|
| 0.1 | 新建 `MarketDataProvider` Protocol | `aletheia/data/providers.py` | **新建** |
| 0.2 | 新建 `SymbolMapper` — canonical ↔ per-provider 格式转换 | `aletheia/data/symbols.py` | **新建** |
| 0.3 | `instruments.py` 提取 canonical timeframe 列表，保留 `BITGET_GRANULARITY` 但标注为 deprecated | `aletheia/trading/instruments.py` | 小改 |

**`providers.py` 核心接口：**
```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class MarketDataProvider(Protocol):
    async def get_ticker(self, symbol: str) -> Ticker: ...
    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list[Candle]: ...
```

**`symbols.py` 规范：**
- Canonical format: `BTC/USDT`（CCXT 通用格式）
- `to_bitget(sym)`: `BTC/USDT` → `BTCUSDT`
- `to_ccxt(sym)`: `BTCUSDT` → `BTC/USDT`
- `to_yfinance(sym)`: `BTC/USDT` → `BTC-USD`

---

### Phase 1：CCXT Provider 实现

**目标：** 实现 CCXT Binance 数据源，行情获取路径切换到 CCXT。

| # | 变更 | 文件 | 类型 |
|---|---|---|---|
| 1.1 | 新建 `CCXTProvider` — 封装 `ccxt.binance` (spot, 无需 API key) | `aletheia/data/ccxt_provider.py` | **新建** |
| 1.2 | 新建 `OHLCVCache` — 简单内存 TTL 缓存（10 min），避免重复拉取 | `aletheia/data/cache.py` | **新建** |
| 1.3 | `market_data.py` — `collect_market_snapshots` 改用 CCXT provider | `aletheia/trading/market_data.py` | 修改 |
| 1.4 | `market_context.py` — `build_market_context` 的 `client` 参数改为 `provider` | `aletheia/trading/market_context.py` | 修改 |
| 1.5 | `routes/data.py` — `/api/market/ohlcv` 改用 CCXT provider | `aletheia/server/routes/data.py` | 修改 |
| 1.6 | `config/schema.py` — 新增 `MarketDataConfig`（provider、exchange、fallback） | `aletheia/config/schema.py` | 修改 |

**`MarketDataConfig` 草案（加入 `AletheiaConfig`）：**
```yaml
market_data:
  provider: "ccxt"           # ccxt | bitget | auto
  ccxt_exchange: "binance"   # binance | bybit | okx
  ccxt_fallback: "bybit"
  enable_yfinance_macro: false
  ohlcv_cache_ttl_min: 10
  observed_symbols:          # 行情观察列表（独立于交易列表）
    - "BTC/USDT"
    - "ETH/USDT"
    - "SOL/USDT"
```

**Symbol 迁移策略：**
- SQLite `market_snapshots` 已有 `BTCUSDT` 格式的历史数据 → 新写入改为 `BTC/USDT`
- `latest_snapshot()` 需兼容两种格式（查询时尝试 canonical 后 fallback Bitget 格式）
- `paper_trader` 的 `latest_snapshot(symbol)` 调用：symbol 来自 `tradable_symbols_from_config`，该函数需统一返回 canonical 格式

**`CCXTProvider` 关键设计：**
- 使用 `ccxt.binance` 的公开 REST 端点（无需任何 API key）
- 入参/出参复用现有 `Ticker` / `Candle` dataclass，避免下游改动
- timeframe 映射：`1h` → `1h`, `4h` → `4h`, `1day` → `1d`（CCXT 标准格式）

---

### Phase 2：yFinance 宏观数据层

**目标：** 在 `build_market_context()` 的 LLM prompt 中追加宏观背景块，不影响加密行情逻辑。

| # | 变更 | 文件 | 类型 |
|---|---|---|---|
| 2.1 | 新建 `yFinanceProvider` — 拉取 SPY/VIX/DXY/Gold 日线数据 | `aletheia/data/yfinance_provider.py` | **新建** |
| 2.2 | `market_context.py` — 可选追加宏观块（`enable_yfinance_macro` flag 控制） | `aletheia/trading/market_context.py` | 小改 |

**宏观块内容（渲染为 markdown）：**
```
### Macro context (daily, yFinance)
| Asset | Close | 5d % | RSI14 |
|-------|------:|-----:|------:|
| SPY   | 523.4 | +1.2%|  58.3 |
| VIX   |  14.2 | -8.5%|  32.1 |
| DXY   | 104.8 | -0.3%|  44.7 |
| Gold  | 2340  | +0.8%|  52.0 |
```

**注意：** yFinance 加密数据（`BTC-USD`）质量低于 CCXT，不用于主链行情；仅宏观品种。

---

### Phase 3：收尾与迁移清理

**目标：** 清理遗留的交易所耦合，确保数据一致性。

| # | 变更 | 文件 | 状态 |
|---|---|---|---|
| 3.1 | `BITGET_GRANULARITY` 仅保留为 deprecated execution-context 常量；新数据路径使用 canonical timeframe | `aletheia/trading/instruments.py` | ✅ 当前无生产消费者，不作为能力缺口 |
| 3.2 | `config/schema.py` — `ExchangeConfig.tradable_symbols` 改为仅控制交易白名单；行情观察改由 `MarketDataConfig.observed_symbols` 管控 | `aletheia/config/schema.py` | |
| 3.3 | `RiskConfig.allowed_symbols` 改为 canonical 格式 | `aletheia/config/schema.py` | 需同步更新 risk engine 消费点 |
| 3.4 | `BitgetClient` 删除 `get_ticker` / `get_ohlcv` 的公开行情路径的外部调用，注释标明"执行上下文内使用" | `aletheia/exchanges/bitget/client.py` | **change-controlled** |
| 3.5 | 数据库迁移：`market_snapshots` 新增 `provider` 列，允许多源数据共存 | `aletheia/storage/migrations.py` | |
| 3.6 | 更新 `instruments.py` 中 `tradable_symbols_from_config()` 返回 canonical 格式 | `aletheia/trading/instruments.py` | ✅ 已实现 |

**Phase 3 当前结论：**

- **3.1 不再作为产品 backlog**：`BITGET_GRANULARITY` 仍存在，但当前没有生产消费者；为了删除常量而删除不会改善数据链或支持面。未来若触碰该模块可做有界清理。

- **3.6 已完成**：`tradable_symbols_from_config()` 在源头对每个配置值调用 `to_canonical()`，消费者得到 `"BTC/USDT"` 形式。

---

## 3a. 实际实现差异

本节记录已实现代码与上述 Phase 1–3 计划之间的已知差异，作为契约更新的规范来源。

### 3a.1 Provider Chain：3-tier FallbackChainProvider（已实现增强）

**计划描述（§2 目标架构）**：CCXTProvider (Binance 主) + CCXTProvider (Bybit 备)，两者均通过 `CCXTProvider` 内部 primary/fallback 机制处理。

**实际实现**：`get_market_data_provider()` (`aletheia/data/ccxt_provider.py:218`) 在 `enable_crypto_fallback=True`（默认）时返回：

```
FallbackChainProvider([
    CCXTProvider(primary=ccxt_exchange, fallback=ccxt_fallback),  # tier 1
    CoinGeckoProvider(),                                            # tier 2
    BinancePublicProvider(),                                        # tier 3
])
```

各 tier 职责：

| Tier | Provider | 文件 | 覆盖范围 | 限制 |
|------|----------|------|----------|------|
| 1 | `CCXTProvider` | `aletheia/data/ccxt_provider.py:56` | 全部加密品种，OHLCV + ticker | 需 CCXT 支持的交易所 |
| 2 | `CoinGeckoProvider` | `aletheia/data/sources/coingecko.py:43` | 主流加密 ticker；OHLCV 仅 "4h"/"1d" | 免费限速 1 req/6s；"1h" 降级为 "4h"；"5m"/"15m" 抛 `NotImplementedError` |
| 3 | `BinancePublicProvider` | `aletheia/data/sources/binance_public.py:30` | Binance 公开端点备用 | 无 API key 需求 |

**降级规则**（由 `FallbackChainProvider` 实现，`aletheia/data/sources/chain.py:52`）：
- `NotImplementedError` → 静默跳过当前 provider（如 CoinGecko 不支持 5m）
- 其他异常 → 记录 debug 日志并尝试下一 provider
- 全部失败 → 抛出最后一个异常
- Tier 1 失败且 Tier 2/3 成功 → 发出 operator 通知（`price_fallback` 事件，每 symbol 限频 5 min）

**锁定为契约**：此 3-tier 链为当前生产行为，下述不变量必须保持：
1. `get_market_data_provider()` 在默认配置下返回 `FallbackChainProvider` 实例（非裸 `CCXTProvider`）
2. 链顺序：CCXTProvider 始终为 tier 1
3. `enable_crypto_fallback=False` 时退化为裸 `CCXTProvider`（向后兼容开关）

### 3a.2 xStocks ON-series 直连 Bitget 覆写

ON-series xStocks（如 `AAPLON/USDT`、`TSLAON/USDT`）是 Bitget 独有的代币化美股，只在 Bitget 上市，通用 Binance/Bybit 路径无法拉取。

**实现位置**（`aletheia/trading/market_data.py:118–130`）：

```python
# market_data.py:122-123
if xstocks:
    xprovider = CCXTProvider(exchange="bitget", fallback_exchange="bitget")
```

此处**刻意绕过** `get_market_data_provider()`，直接构造 `CCXTProvider(exchange="bitget", fallback_exchange="bitget")`，原因：

- 两个 exchange 均为 `"bitget"`，确保 primary 失败时 fallback 不切换到其他交易所
- xStocks 不在 FallbackChainProvider 的 CoinGecko/Binance 链中存在，多级降级会静默失败

xStocks 标识通过 `aletheia/data/symbols.py:111` 的 `_XSTOCK_MAP` 维护，映射关系为 `{"AAPLON": "AAPL", "TSLAON": "TSLA", ...}`。

**契约锁定**：xStocks 数据路径必须满足：
- 调用 `CCXTProvider(exchange="bitget", fallback_exchange="bitget")` — 不得路由到 `get_market_data_provider()`
- `InstrumentId.from_parts("bitget", symbol, MARKET_SPOT)` 构造（`aletheia/trading/instruments.py:64`）

### 3a.3 数据源审计收尾：删假源 + 接免费源（datasrc overhaul, 2026-06-28）

审计(`docs/research/audits/DATA_SOURCE_AUDIT_AND_GITHUB_SCAN_2026-06-28.md`)确认四处空转/死源，已清除并以零-key 免费源填补。

**删除**：CoinGlass collector（三路径全空 + 从未注册，纯门面）+ 其 spec/doctor/settings/前端引用；
derivatives 死清算 REST fallback；Glassnode 确认从不存在。

**新增 collector（契约锁定）**：
- `HyperliquidCollector`（`collectors/hyperliquid.py`，claims **HL-***）：公开 `api.hyperliquid.xyz/info`，
  零 key，per-call、proxy-aware（`aletheia.net.httpx_client`）。写 `funding_rate_snapshots` /
  `open_interest_snapshots`，tagged `exchange="hyperliquid"` / `"hyperliquid_predicted"`。
  **跨-venue 不变量**：这些 tag 被 `latest_funding_rate` / `latest_open_interest` 的默认读取**排除**
  （`_CROSS_VENUE_TAGS`），主交易所 per-symbol 视图不被污染；显式 `exchange=` 可查。
- `DefiLlamaCollector`（`collectors/defillama.py`，claims **DFL-***）：公开 `*.llama.fi`，零 key，
  三路独立（stablecoin_mcap / chain_tvl / dex_volume，一路失败不影响其他）。写新 `onchain_snapshots` 表，
  折进 `render_enriched_context_md` 的 macro 块供 LLM 消费（填 Glassnode 链上缺口）。

两源经 `_async_data_collectors` Phase B（~10min）调度，各 `enable_hyperliquid` / `enable_defillama` 门控。
Coinalyze（跨所聚合）已在 `docs/archive/legacy/plans/market-data/free_data_sources_implementation_plan.md` 设计，待免费 key，**不实现 stub**。

---

## 4. 不变更的范围

以下文件/模块**不在本次数据层重构范围内**，保持不变：

- `aletheia/execution/` — 执行网关、下单逻辑
- `aletheia/risk/` — 风险引擎
- `aletheia/exchanges/bitget/auth.py` — 签名逻辑
- `aletheia/portfolio/exchange_sync.py` — 交易所账户同步（本身就是 exchange-specific）
- `aletheia/agents/` 中除 `market_observer.py` 外的所有 agent
- 所有测试文件（重构后新增覆盖，不改现有测试）

---

## 5. 依赖关系与实施顺序

```
Phase 0 (接口定义 + Symbol 规范)
    ↓
Phase 1 (CCXT Provider + 行情路径切换)   ← 最高优先级，核心价值
    ↓
Phase 2 (yFinance 宏观) — 可选，独立，可后做
    ↓
Phase 3 (清理) — Phase 1 稳定后再做
```

Phase 1 可以再细分为两步：
1. **Phase 1a**：实现 CCXTProvider，`build_market_context()` 先接入（agent 分析链）
2. **Phase 1b**：`collect_market_snapshots()` 切换（持久化层），需要 symbol 迁移处理

---

## 6. 新增依赖

| 库 | 用途 | 安装 | 风险 |
|---|---|---|---|
| `ccxt` | CCXT REST client | `uv add ccxt` | 低。纯 HTTP，无 C 扩展。已在业界广泛使用。 |
| `yfinance` | 宏观数据 | `uv add yfinance` | 低。仅 Phase 2 引入，且有 `enable_yfinance_macro` 开关默认关闭。 |

**注意：** `ccxt` 包体积较大（~2MB）。如仅使用 Binance public REST，也可考虑轻量替代：直接调 `https://api.binance.com/api/v3/` via `httpx`（仿照现有 `BitgetClient` 风格）。需用户决策。

---

## 7. 测试策略

每个 Phase 完成后必须通过：

```bash
uv run pytest tests/ -q          # 全量基线，352 passed
```

新增测试（各 Phase 附带）：
- `tests/test_ccxt_provider.py` — mock httpx，验证 symbol 转换、降级逻辑
- `tests/test_symbol_mapper.py` — 各格式互转的纯函数测试
- `tests/test_market_data_provider.py` — provider 切换后 `collect_market_snapshots` 行为
- `tests/test_ohlcv_cache.py` — TTL 缓存命中/过期逻辑

change-controlled 文件（如 `BitgetClient` Phase 3 改动）需附专项回归测试。

---

## 8. 风险与缓解

| 风险 | 概率 | 缓解 |
|---|---|---|
| CCXT Binance 公开 API 被封/限速 | 低 | `ccxt_fallback` 指向 Bybit，降级机制与现有 Bitget fallback 相同 |
| Symbol 格式迁移导致 `paper_trader` 查不到价格 | 中 | `latest_snapshot()` 加双格式查询，Phase 1 期间两种格式并存 |
| yFinance 数据质量/可用性 | 低 | 默认关闭，纯 advisory，prompt 中标注"宏观参考，不作为方向信号" |
| Phase 3 修改 `BitgetClient` 引入 regression | 中 | change-controlled 文件，附专项测试，Phase 3 可延后至系统稳定后 |

---

## 9. 决策记录（已锁定）

1. **CCXT 而非自写 httpx**：使用 `ccxt` 库，`ccxt.pro` 不引入，仅用 REST。理由：rate limit 处理、symbol 规范化、多 exchange fallback 开箱即用；维护成本远低于自维护 HTTP 层。Exchange 选 Binance 为主，Bybit 为备用（均为公开端点，无需 API key）。

2. **Phase 1 + Phase 2 合并推进**：CCXT（加密行情）+ yFinance（宏观）一起做，`enable_yfinance_macro` 默认关闭，Phase 1a/1b 稳定后再打开。

3. **OHLCV 本地持久化——滚动窗口策略**：
   - 参照 OpenBB 的设计：provider 层无状态（纯 fetch+transform），持久化在应用层
   - `market_ohlcv` SQLite 表按 `(exchange, symbol, market_type, timeframe, ts)` 唯一存储；现货、永续和不同 venue 不互相覆盖。无法恢复真实 venue 的旧 `exchange='ccxt'` 行迁移为 `legacy_unknown`，不得冒充当前活动标的覆盖。
   - 滚动窗口：每个 `(symbol, timeframe)` 对保留最近 **500 条**（1h 约 20天，4h 约 83天，1day 约 1.4年）
   - 写入时 `INSERT OR REPLACE`，定期 `DELETE WHERE rowid NOT IN (SELECT rowid FROM ... ORDER BY ts DESC LIMIT 500)`
   - 内存 TTL 缓存（10 min）仍是热路径；SQLite 是冷启动恢复 + 历史回看的持久层

4. **`BitgetClient` 行情方法保留但隔离**：
   - `get_ohlcv`：仅 `exchange_sync.py`（执行上下文）允许调用；`market_context.py` 和 `routes/data.py` 迁移到 CCXT 后，`get_ohlcv` 加文档注释标注"限执行上下文"，不删除
   - `get_ticker`：`exchange_sync.py` 用它做持仓估值（合法的执行层用途），保留，不隔离
   - Phase 3 不删除这两个方法，改为在方法 docstring 里明确标注 `# execution-context only`
