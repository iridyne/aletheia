# Technical Analysis Contract: retired and unwired passages

> **History (moved 2026-10-10).** Moved verbatim from
> `docs/design/contracts/technical_analysis_contract.md` (then titled "Technical Analysis and
> OHLCV Tiering Contract") when it was rewritten into numbered requirements. The passages
> describe OHLCV hot/cold tiering, which has no production caller
> (`core/aletheia/storage/repositories.py::plan_market_ohlcv_tiering` is never called; only
> `core/aletheia/analytics/position_replay.py` imports two helpers from
> `core/aletheia/storage/market_ohlcv_tiering.py`), and the `technical-context-v1` renderer that
> served the retired Market Observer (`core/aletheia/trading/market_context.py::build_market_context`
> has no production caller). Nothing here is current behavior; the current rules are the `TA-C*`
> requirements.

## Purpose (tiering paragraph)

The current storage lifecycle keeps SQLite as the indexed hot authority and can
plan immutable cold chunks without choosing a cold physical format. Zstandard,
SQLite BLOB cold storage, and external cold files are not configured.

## Entry points (tiering)

- `repositories.plan_market_ohlcv_tiering()` creates a read-only plan for one
  exact `exchange + symbol + market_type + timeframe` series. It has no production
  caller and no cold-store implementation; tiering is not a current runtime behavior.
- `OHLCVTierManager.copy_to_cold()` coordinates a format-neutral cold backend
  when one is explicitly supplied.

## Durable state and consumers (Market Observer renderer and Operator)

`trading/market_context.py` can render the shared snapshot as one bounded
`technical-context-v1` block; that renderer served the retired Market Observer and
has no production caller now. The block format is retained: The block preserves exact instrument identity,
algorithm version and a bundle input fingerprint in its linked Harness artifact;
model-visible rows contain normalized trend, momentum, volatility, location and
participation facts. Insufficient-warmup or degraded timeframes expose only an
unavailable quality state, not partial indicator values. Bundle quality is
measured against every configured timeframe, and the venue comes from returned
candles; inconsistent venues fail closed. Universe scanners use
projected RSI/ATR/Bollinger/volume facts. System Operator consumes the persisted
feature row and includes technical version/input identity in its material
decision fingerprint.

## Observable result (block paragraph)

The block does not carry recent raw close arrays. Correlated raw
SMA/EMA levels are compressed into one EMA trend state and spread; ATR, VWAP and
Donchian location are normalized so the model does not need to calculate them.

## Invariants (Operator fingerprint and tiering)

- Algorithm-version changes and 1h input-fingerprint changes alter the Operator
  material decision fingerprint.
- Cold planning never deletes SQLite rows.
- A cold delete cutoff is unavailable until every complete chunk has a durable
  matching receipt and passes read-back equality.
- No compression or cold storage format is implied by the tiering contract.

## Failure and recovery semantics (tiering)

A cold backend receipt mismatch or read-back mismatch raises and fails closed.
Deferred rows that do not fill a complete cold chunk remain in the hot authority.

## Executable evidence (tiering and renderer)

- `tests/test_market_ohlcv_tiering.py`
- `tests/test_market_context.py`

## Known gaps (tiering)

The owner intentionally deferred selection of SQLite BLOB versus external cold
files. Until that decision is made and a production `ColdOHLCVBackend` is
configured, tiering remains read-only planning and verified coordination; the
SQLite hot authority is never pruned through this path.
