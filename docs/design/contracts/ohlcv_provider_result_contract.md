# OHLCV Provider Result Contract

## Purpose and current scope

This contract owns the result semantics of
`MarketDataProvider.get_ohlcv()`. A non-empty result represents exact candles
for the requested canonical timeframe with observed open, high, low, close, and
volume values. A provider that cannot satisfy that boundary is unsupported; it
does not relabel coarse OHLC or synthesize missing volume.

The contract covers CCXTProvider, BinancePublicProvider, and their composition
through FallbackChainProvider. CoinGecko remains a ticker provider but is not an
OHLCV provider because its public OHLC endpoint has range-dependent intervals
and no volume field.

## Entry points

- `aletheia.data.providers.MarketDataProvider.get_ohlcv()` is the structural
  interface.
- `CCXTProvider.get_ohlcv()` is the primary and exact-venue fallback path.
- `BinancePublicProvider.get_ohlcv()` is the supported spot fallback path.
- `FallbackChainProvider.get_ohlcv()` owns ordered provider fallback.
- `CoinGeckoProvider.get_ohlcv()` reports OHLCV as unsupported without network
  I/O; its ticker method is unchanged.

## Durable state and consumers

`OHLCVResult` is request-scoped and not durable. It retains requested count,
accepted and rejected counts, provider attempts, and complete/partial/empty
outcome. Returned `Candle` rows retain provider and exchange identity.

CCXT persists confirmed bars to the exact SQLite
`exchange + symbol + market_type + timeframe` identity. Market Context, Regime,
Universe outcome collection, and the dashboard chart consume provider results.
History Downloader and Protective Barrier use native CCXT with exact venue
identity and do not enter the spot-only CoinGecko/Binance fallback path.

## Observable result

A successful non-empty call returns oldest-to-newest candles for exactly the
requested timeframe. All rows contain finite, geometrically valid OHLC values
and a real non-negative volume value supplied by the source. Provider and
exchange provenance is non-empty and homogeneous for a single result.

CCXT and Binance Public provide exact OHLCV. Binance Public accepts only the
explicit `_TF_MAP` timeframes. CoinGecko OHLCV and unknown Binance timeframes
raise `NotImplementedError`, allowing FallbackChainProvider to continue to the
next eligible provider.

## Invariants and trust boundaries

- Requested timeframe and returned candle interval never silently differ.
- Missing volume is not converted to zero.
- `complete` describes accepted row count and quality; it does not override
  exact-timeframe or real-field requirements.
- Empty, unsupported, and failed attempts remain distinct.
- A fallback result retains the provider and venue that actually supplied it.
- Derivative requests never fall back to spot-only providers.
- Forming bars remain `is_closed=false`; only confirmed CCXT bars are persisted.
- SQLite authority, Risk approval, Execution Gateway ownership, and Live
  permissions are unchanged.

## Failure and recovery semantics

Static capability mismatch raises `NotImplementedError` before remote I/O.
FallbackChainProvider records the unsupported attempt and tries the next
eligible provider. Empty provider results also continue fallback; network or
parse errors remain failed attempts. If no provider returns exact OHLCV, the
caller receives the existing empty/error behavior and may use an exact SQLite
fallback where that consumer already owns one.

No consumer may recover by treating coarse OHLC or synthesized volume as exact
OHLCV. A future coarse-price consumer requires a separate, explicitly approved
result contract.

## Executable evidence

- `tests/test_crypto_fallback.py` proves CoinGecko OHLCV and unknown Binance
  timeframes fail before network I/O, while supported Binance OHLCV and generic
  unsupported-provider fallback remain functional.
- `tests/test_ohlcv_provider_boundary.py` proves empty fallback and attempt facts.
- `tests/test_ccxt_provider_async_persistence.py` proves exact observed-venue
  cache identity and confirmed-bar persistence.
- `aletheia.data.providers.parse_ohlcv_candle()` is the shared finite/geometry/
  volume validation boundary.

## Current scope boundary

This contract does not create a provider descriptor registry, persistent health
snapshot, consumer-specific coarse fallback, response `observed_at`, new storage
schema, or new Candle type. Those additions require concrete consumer evidence
and separate approval.

The accepted design history is preserved in
`docs/design/proposals/ohlcv_provider_observation_result.md`.
