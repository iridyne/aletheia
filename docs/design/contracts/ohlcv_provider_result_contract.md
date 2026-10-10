# OHLCV Provider Result Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `OP` · **Owner code:** `core/aletheia/data/providers.py::parse_ohlcv_candle`, `extensions/data/ccxt_provider.py::CCXTProvider`

## Scope

This contract owns the result semantics of `MarketDataProvider.get_ohlcv()` and of the
primary-venue observation read, plus the validity of `Ticker` and `MarketSnapshot` values. A
non-empty OHLCV result is exact candles for the requested canonical timeframe with observed
open, high, low, close and volume; a provider that cannot meet that is unsupported and never
relabels coarse OHLC or synthesizes volume. On the trading mainline, Universe feature
collection reads USDT-perpetual 15m/1h/4h candles from the active venue through CCXT and
persists the closed bars that the Entry Planner later reads from SQLite. Spot-only providers
(`BinancePublicProvider`, `CoinGeckoProvider`) remain assembled in the default fallback chain
but are residual for the futures-only product: they can only serve spot requests. This
contract does not create a provider descriptor registry, persistent health snapshot,
consumer-specific coarse fallback, response `observed_at`, storage schema or Candle type.

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `extensions/data/ccxt_provider.py::CCXTProvider.get_ohlcv_observation` | call (primary venue, no fallback, no write) | `core/aletheia/universe/features.py::_fetch_single_feature` (`ohlcv_reader`) |
| `core/aletheia/data/providers.py::parse_ohlcv_candle` | call (shared row validation) | CCXT reads, `BinancePublicProvider`, `core/aletheia/universe/features.py::_fetch_single_feature` |
| `core/aletheia/data/providers.py::get_market_data_provider` | call (factory; `exact_venue=True` → native CCXT, fallback = primary) | `core/aletheia/data/history_downloader.py`, `core/aletheia/universe/outcome_recorder.py` |
| `extensions/data/ccxt_provider.py::get_market_data_provider` | call (default assembly: `FallbackChainProvider([CCXT, CoinGecko, BinancePublic])` when `market_data.enable_crypto_fallback`) | `core/aletheia/server/routes/data.py::get_market_ohlcv` (`GET /api/market/ohlcv`), `extensions/regime/collector.py::_fetch_btc_daily_candles` |
| `extensions/data/sources/chain.py::FallbackChainProvider.get_ohlcv` | call (ordered fallback) | default assembly above |
| `extensions/data/sources/coingecko.py::CoinGeckoProvider.get_ohlcv` | call (always unsupported) | `FallbackChainProvider` |
| `extensions/data/sources/binance_public.py::BinancePublicProvider.get_ohlcv` | call (spot only) | `FallbackChainProvider` |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `market_ohlcv` (`exchange + symbol + market_type + timeframe` identity) | `CCXTProvider._persist` (confirmed bars of `get_ohlcv`), `core/aletheia/universe/features.py::_fetch_single_feature` (closed observation rows) | Entry Planner windows, Fast Trigger prewarm, outcome recorder, chart route | upsert per bar |
| in-process OHLCV cache (`aletheia.data.cache`) | CCXT reads | CCXT reads | replaceable |

`OHLCVResult` is request-scoped: requested count, accepted/rejected counts, provider attempts
and `complete`/`partial`/`empty` outcome. Returned `Candle` rows keep provider and exchange identity.

## Requirements

### OHLCV results

#### OP-C01 Exact timeframe and real fields

`The provider shall return a non-empty OHLCV result only as oldest-to-newest candles of exactly the requested timeframe whose OHLC values are finite and geometrically valid, whose volume is a real non-negative source value, and whose provider and exchange identity is non-empty and homogeneous.`

- WHEN a source omits volume THEN the row is not converted to zero volume.
- `complete` describes accepted row count and quality; it never overrides the exact-timeframe or real-field rule.

Evidence: `core/aletheia/data/providers.py::OHLCVResult`, `classify_ohlcv_outcome`.

#### OP-C02 One row-validation boundary

`When a provider row is parsed, parse_ohlcv_candle shall reject it if any value is non-finite, the timestamp is not a positive integer, any price is non-positive, volume is negative, or high/low do not bound open and close, and the reader shall count it as rejected.`

- WHEN one of 100 rows has `high < close` THEN 99 candles are accepted, `rejected == 1`, and a warning names the venue.

Evidence: `core/aletheia/data/providers.py::parse_ohlcv_candle`.

#### OP-C03 Forming bars are never confirmed

`When a fetched trailing bar's close time is still in the future, the CCXT reader shall mark it is_closed=false, and only closed bars shall be persisted.`

Evidence: `extensions/data/ccxt_provider.py::CCXTProvider.get_ohlcv`, `get_ohlcv_observation`, `_persist`; `core/aletheia/universe/features.py::_fetch_single_feature` (`_closed_rows`).

#### OP-C04 Mainline observation reads use the primary venue only

`When Universe feature collection reads OHLCV, CCXTProvider.get_ohlcv_observation shall query only the primary market-data venue, perform no fallback and no durable write, and fail with a recorded attempt when that venue is cooling down.`

- WHEN the primary venue is cooling down THEN `VenueCoolingDownError` is raised with one `failed` attempt and no other venue is tried.

Evidence: `extensions/data/ccxt_provider.py::CCXTProvider.get_ohlcv_observation`.

#### OP-C05 Exact-venue reads never switch venue

`Where a caller requests exact_venue, the factory shall return a native CCXT provider whose fallback venue equals the requested venue.`

- WHEN the History Downloader backfills a series THEN every persisted bar is labelled with the target exchange.

Evidence: `extensions/data/ccxt_provider.py::create_market_data_provider`, `core/aletheia/data/history_downloader.py`.

#### OP-C06 A fallback result keeps its real venue

`When CCXTProvider.get_ohlcv falls back to market_data.ccxt_fallback, the returned and persisted candles shall carry the fallback venue's exchange identity, never the primary's.`

- WHEN the primary fails and `ccxt_fallback` serves the bars THEN `market_ohlcv.exchange` is the fallback venue, so a consumer that requires the asset's venue (Entry Planner windows) does not admit them.

Evidence: `extensions/data/ccxt_provider.py::CCXTProvider.get_ohlcv` (`parse_ohlcv_candle(..., exchange=name)`, `_persist(..., name)`).

#### OP-C07 Derivative requests never reach spot-only providers

`If an InstrumentId request is not spot, then FallbackChainProvider shall consider only its first (CCXT) provider.`

Evidence: `extensions/data/sources/chain.py::FallbackChainProvider._eligible_providers`.

#### OP-C08 Static unsupported capability fails before I/O

`If a provider cannot supply exact OHLCV for the request (CoinGecko for any request; Binance Public for a non-spot InstrumentId or a timeframe outside _TF_MAP), then it shall raise NotImplementedError before any network I/O.`

- WHEN CoinGecko is asked for OHLCV THEN it raises, because its OHLC endpoint has range-dependent intervals and no volume.

Evidence: `extensions/data/sources/coingecko.py::CoinGeckoProvider.get_ohlcv`, `extensions/data/sources/binance_public.py::_spot_canonical`, `BinancePublicProvider.get_ohlcv`.

#### OP-C09 Empty, unsupported and failed attempts stay distinct

`When FallbackChainProvider tries providers in order, it shall record each attempt as unsupported, failed or by its outcome, continue past unsupported and empty results, return the first non-empty result with all attempts, and otherwise raise the last failure with its attempts, return an empty result when an attempt was empty, or raise "no provider supports" when every attempt was unsupported.`

Evidence: `extensions/data/sources/chain.py::FallbackChainProvider.get_ohlcv`.

#### OP-C10 No coarse recovery

`The system shall not recover from a missing exact OHLCV result by treating coarse OHLC or synthesized volume as exact OHLCV; a caller may use only an exact SQLite fallback it already owns.`

Evidence: `extensions/data/sources/coingecko.py::CoinGeckoProvider.get_ohlcv` (no coarse path exists).

### Ticker results

#### OP-C11 Ticker values are finite and in domain

`The Ticker model shall reject a non-finite or non-positive last price, a non-finite or negative 24h volume, a non-finite 24h change, a non-positive optional 24h extreme, and a high_24h below low_24h.`

Evidence: `core/aletheia/data/providers.py::Ticker.__post_init__`, `_ticker_number`.

#### OP-C12 Batch volume ranking ignores impossible ticker facts

`When Universe feature collection pre-filters by 24h quote volume, it shall reject a ticker whose price or volume is non-finite or impossible with ticker_numeric_invalid instead of using it as ranking evidence.`

Evidence: `core/aletheia/universe/features.py::_evaluate_ticker_prefilters`, `_quote_volume_24h`, `_positive_float`, `_nonnegative_float`.

#### OP-C13 MarketSnapshot validates before persistence

`The MarketSnapshot model shall reject non-finite, boolean, non-positive price or extreme, negative volume, and unordered 24h extreme values before persistence, as defense in depth behind provider validation.`

Evidence: `core/aletheia/storage/models.py::MarketSnapshot`.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| Static capability mismatch | `NotImplementedError` before I/O; chain continues | `OHLCVAttempt.outcome="unsupported"` | not empty, not failed |
| Network/parse error | attempt `failed`; chain continues; last error re-raised with `ohlcv_attempts` | `OHLCVAttempt.reason` | failed, not empty |
| Provider returns no rows | attempt by outcome `empty`; chain continues | `OHLCVResult.outcome="empty"` | empty, never synthesized |
| Invalid row | dropped and counted | `OHLCVResult.rejected`, warning log | never zero-filled |
| Primary venue cooling down (observation read) | raise | `VenueCoolingDownError` | no fallback venue used |

## Authority

May fetch and persist market data only; may never change SQLite authority rules, Risk
approval, Execution Gateway ownership or Live permissions.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| OP-C01, OP-C02, OP-C09 | `tests/test_ohlcv_provider_boundary.py` |
| OP-C03, OP-C06 | `tests/test_ccxt_provider_async_persistence.py` |
| OP-C07, OP-C08, OP-C10 | `tests/test_crypto_fallback.py`; `tests/specs/spec_market_structure.py::MS-C03` (Binance Public non-spot guard) |
| OP-C04 | `tests/test_ohlcv_feature_boundary.py` |
| OP-C12 | `tests/test_universe_features.py`; `tests/test_instrument_aware_collectors.py`; `tests/test_market_data_venue_health.py` |
| OP-C05 | `tests/test_market_provider_port.py` |
| OP-C11 | `tests/test_crypto_fallback.py` |
| OP-C13 | `tests/test_universe_features.py` |

### Known conformance gaps

- `core/aletheia/config/schema.py` defaults `market_data.ccxt_fallback = "bybit"` (comment: "ccxt uses
  CCXT Binance/Bybit"), so a non-exact CCXT read can fetch from Bybit, a venue outside the
  bitget_demo-only product scope; OP-C06 keeps those bars out of Bitget series but does not stop the fetch.
- `market_data.enable_crypto_fallback` defaults to `True` (`core/aletheia/config/schema.py`), so the
  spot-only CoinGecko/Binance Public providers stay assembled. The only current spot OHLCV request is
  the Regime BTC/USDT daily read (`extensions/regime/collector.py::_fetch_btc_daily_candles`, a string
  symbol, therefore spot), which can be served by Binance Public — a frozen venue.
- OP-C07 applies only to `InstrumentId` requests; a string symbol is always treated as spot
  (`extensions/data/ccxt_provider.py::_resolve_ccxt_symbol`).
- `parse_ohlcv_candle(volume_index=None)` sets volume to `0.0`, contrary to OP-C01; it has no
  production caller.

## Decision references

- Accepted design history: [`ohlcv_provider_observation_result.md`](../proposals/ohlcv_provider_observation_result.md).
- The former "Protective Barrier uses native CCXT" consumer was removed with the local simulator
  ([history](../history/local_simulated_protection.md)).
