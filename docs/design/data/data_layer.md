# Data Layer Contract — Public Market-Data Boundary

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `DLY` (claim namespace `data_layer_contract` also holds `DL-*`, `DFL-*`, `HL-*`, `MS-*`, `OD-*` claims) · **Owner code:** `core/aletheia/data/providers.py`, `extensions/data/ccxt_provider.py`

## Scope

Public market facts (ticker, OHLCV, orderbook, funding, open interest, listings) reach Core
through one provider abstraction; private account, order, fill and funding-ledger facts stay
behind UTA and the Execution Gateway. A provider or fallback change must not change instrument
identity, causal time, availability semantics, Risk authority or execution ownership. Ticker
and OHLCV result validity is detailed in the
[OHLCV Provider Result Contract](../contracts/ohlcv_provider_result_contract.md); which collector
feeds which consumer is registered in [Data Utilization](data_utilization.md); collector health
and coverage are in the [Coverage and Collector Health Contract](../contracts/market_data_coverage_contract.md).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/data/providers.py::get_market_data_provider` | call | `universe/features.py`, `agent_shell/entry_planner.py`, `data/price_feed.py`, `server/routes/data.py` and other public readers |
| `core/aletheia/data/providers.py::configure_market_data_factory` | composition | `main.py`, `server/app.py` (binds `extensions/data/ccxt_provider.py::create_market_data_provider`) |
| `extensions/data/collectors/*` | scheduler collectors | `agent_runtime/scheduler.py` (`data_collectors`, `data_collector_context`) |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `market_ohlcv` | `extensions/data/ccxt_provider.py` best-effort persistence of confirmed bars; `data/history_downloader.py` | OHLCV route fallback, Entry Planner windows, analytics | upsert by full instrument identity + `ts` |
| in-process OHLCV cache | `core/aletheia/data/cache.py` | provider callers | replaceable, TTL |

All other market-data tables and their writers: [data_model.md §3](data_model.md).

## Requirements

### DLY-C01 One provider entry

`When Core needs public market data, it shall obtain a fresh request-owned provider from data/providers.py::get_market_data_provider, which shall raise "market data provider unavailable" when composition bound no factory.`

- WHEN `exact_venue=True` is requested without a venue THEN `ValueError` is raised.
- WHEN a new direct `CCXTProvider(...)` construction appears THEN it needs a stated reason; the current ones are the factory, `extensions/data/collectors/derivatives.py` (history backfill) and `extensions/research/equity_observer.py` (residual, see below).

### DLY-C02 Loop-scoped provider lifecycle

`The data layer shall create async CCXT providers on the caller's event loop, leave closing to the caller, and shall not cache a provider across event loops.`

- WHEN `close()` is called twice THEN the second call is a no-op.

### DLY-C03 Exact-venue reads stay pinned

`When a consumer requests exact_venue, the provider shall read the named venue through native CCXT with that venue as its own fallback unless an explicit fallback_venue is passed, and optional observation capabilities shall use no cross-source fallback.`

- WHEN an exact-venue listing or orderbook read fails THEN the result is unavailable, never another venue's value.

### DLY-C04 No physical-market substitution

`If a request carries a non-spot InstrumentId, then the fallback chain shall use only the CCXT provider and never a spot-only source.`

- WHEN a perp ticker read fails on CCXT THEN CoinGecko and Binance public are not tried (`extensions/data/sources/chain.py::FallbackChainProvider._eligible_providers`).

### DLY-C05 Finite, valid market facts

`When a provider row is parsed, the data layer shall reject non-finite or physically impossible ticker and OHLCV values rather than return them as usable facts.`

Evidence: `data/providers.py::parse_ohlcv_candle`, `Ticker`.

### DLY-C06 OHLCV order and forming bar

`The provider shall return candles oldest to newest and mark the trailing still-forming bar is_closed=False, and missing rows or provider failure shall not become fabricated candles.`

- WHEN a causal consumer builds a window THEN it excludes bars with `is_closed=False`.

### DLY-C07 Persistence never changes fetch truth

`If best-effort OHLCV persistence fails, then the fetched result shall be returned unchanged.`

### DLY-C08 Cache identity

`The OHLCV cache shall key entries by exchange, symbol, market_type, timeframe and limit, and a cache hit shall not relax the caller's freshness or causal-time requirement.`

### DLY-C09 Canonical symbols

`Internal consumers shall use canonical symbols and InstrumentId, and provider-specific symbol formats shall be converted only at the adapter boundary.`

- WHEN `to_canonical("BTCUSDT")` runs THEN it returns `BTC/USDT`; WHEN `to_bitget("BTC/USDT")` runs THEN it returns `BTCUSDT`; the round trip is stable.

### DLY-C10 Public and private stay separate

`The data layer shall not route private account, order, fill or funding-ledger reads through MarketDataProvider, and public-market consumers shall not construct a private execution client.`

### DLY-C11 Collector credentials stay in the adapter

`Where a collector needs an API key (Coinalyze, FRED), the adapter shall resolve only its own secret reference and Core consumers shall receive facts, never keys.`

- WHEN no Coinalyze key is configured THEN the collector returns `errors=["no_key"]` and its health is `not_configured`.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| malformed / non-finite payload | rejected | attempt evidence on `OHLCVResult` | unavailable, never `0` |
| primary unavailable | fallback only where DLY-C03/C04 allow | source identity on the result | unavailable if none compatible |
| wrong venue or market identity | rejected | error | never coerced |
| forming or future bar | not confirmed history | `is_closed=False` | — |
| persistence or cache failure | logged | log | fetch result unchanged |
| close failure | bounded cleanup error | log | provider still not cached |

## Authority

May read public market data and persist confirmed candles; may never read credentials outside
its own adapters, change Risk, submit or route orders, or unlock Live.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| DLY-C01 | `tests/specs/spec_data_layer.py::DL-S01`, `DL-S02`; `tests/test_ccxt_provider_factory.py` |
| DLY-C02 | `tests/test_ccxt_provider_factory.py` |
| DLY-C03 | `tests/test_market_provider_port.py`; `tests/test_data_provider_and_async_fixes.py` (registry never substitutes the fallback venue) |
| DLY-C04 | `tests/specs/spec_data_layer.py::DL-S06`; `tests/test_crypto_fallback.py` |
| DLY-C05, DLY-C06 | `tests/specs/spec_data_layer.py::DL-B14`; `tests/test_ohlcv_provider_boundary.py`; `tests/test_crypto_fallback.py` (forming bar) |
| DLY-C07 | `tests/test_ccxt_provider_async_persistence.py` (persistence path; no failure-injection test found) |
| DLY-C08 | `tests/specs/spec_data_layer.py::DL-S08`, `DL-S09` |
| DLY-C09 | `tests/specs/spec_data_layer.py::DL-S03`, `DL-B04`, `DL-B05`, `DL-C01`, `DL-C02`, `DL-C03` |
| DLY-C10 | `tests/specs/spec_data_layer.py::DL-S07` |
| DLY-C11 | `tests/specs/spec_data_utilization.py::DU-S06` |

### Residual for closed scopes

The product is USDT-margined perpetuals on `bitget_demo`; these paths remain in code but are
not supported scope. Their removal is owned by
[spec 0003 FC-R03](../specs/0003-futures-only-convergence/spec.md).

- `enable_crypto_fallback` (default `True`) wraps generic reads in
  `FallbackChainProvider([CCXT, CoinGecko, BinancePublic])`; the two public sources are
  spot-only and are skipped for perp requests (DLY-C04).
- `market_data.ccxt_fallback` defaults to `bybit`, so a generic (non-exact) CCXT read may fall
  back to a venue outside the product.
- `MarketInstrument` listings still report spot markets; `read_market_listing(spot_only=...)`
  serves only the xStock listing check.
- `extensions/data/yfinance_provider.py::fetch_macro_context` is bound as the macro reader when
  `enable_yfinance_macro` is on (default off); its only consumer,
  `trading/market_context.py::build_market_context`, has no production caller. Stock-token
  deviation in `extensions/research/equity_observer.py` is equity scope.

### Known conformance gaps

- `extensions/data/collectors/derivatives.py::DerivativesCollector.collect` fetches long/short
  and top-trader ratios from Binance USD-M, a frozen venue
  (also listed in the [coverage contract](../contracts/market_data_coverage_contract.md#known-conformance-gaps)).

## Decision references

- The former staged refactoring plan, Mermaid diagram and non-goals list:
  [history snapshot](../history/data_layer_2026-10-10.md).
- Async CCXT sessions are loop-bound; a process-global provider leaked aiohttp sessions across
  scheduler loops (reason for DLY-C02).
