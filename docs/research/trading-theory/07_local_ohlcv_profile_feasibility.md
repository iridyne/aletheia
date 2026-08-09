# Local OHLCV and Profile Feasibility

Date: 2026-08-09
Role: Research evidence

## Conclusion

The local candle store is sufficient for closed-bar technical indicators,
causal swings, approximate TPO, and explicitly estimated candle-based profile
experiments. It is not sufficient for an authoritative Volume Profile, POC,
volume delta, CVD, or order-flow reconstruction because it has no trades at
price, aggressor side, or order-book event sequence.

No compression or cold backend should be selected as part of this research.
The current technical contract deliberately leaves Zstandard, SQLite BLOB, and
external cold files unconfigured.

## Current local lifecycle

### Identity and schema

`market_ohlcv` is keyed by:

```text
exchange + canonical symbol + market_type + timeframe + bar-open timestamp
```

Each row stores OHLC, aggregate volume, and provider. It does not store trade
IDs, price-level volume, maker/taker side, liquidation identity, or a revision
history.

### Closed-bar rule

The CCXT provider marks a trailing bar as forming when its close time is still
in the future. Persistence explicitly filters to `is_closed=true`. A forming
bar therefore does not enter the local technical authority; it is inserted on
a later fetch only after closure.

### Revisions

Writes use `INSERT OR REPLACE` for the exact series and timestamp. A venue
revision can replace a previously closed row, but the database does not retain
the old version. Derived incremental state must either subtract the old row
before adding the replacement or rebuild a bounded affected window.

### Coverage and retention

The active bounded backfill currently prioritizes 1h and 4h series. Repository
insertion also applies a configured per-series bar cap. The separate retention
contract currently bounds `market_ohlcv` by timeframe:

| Timeframe | Retention |
| --- | ---: |
| 1m | 2 days |
| 5m | 7 days |
| 15m | 30 days |
| 1h | 90 days |
| 4h | 180 days |
| 1d | 730 days |

The effective history is the stricter result of time retention and the
per-series bar cap. A new profile consumer must prove that this history covers
its session/window contract rather than assume long-term availability.

### Hot/cold state

The tier manager can plan a hot tail and complete cold chunks and can verify a
supplied backend by durable receipt plus read-back equality. The repository
entry point is read-only. No production cold backend is configured and this
path does not delete SQLite rows.

## What can be calculated now

### Exact from candle inputs

- all existing closed-bar indicators in `TechnicalSnapshot`;
- causal pivots, structure transitions, breakout/failure events;
- candle range, return, volatility, and gap summaries;
- session high/low/open/close and time spent by candle presence.

These values are exact transformations of the stored candles, not necessarily
exact descriptions of the trades inside a candle.

### Approximate TPO

A TPO-like profile can count whether each sufficiently fine closed bar occupied
each configured price bin. It requires a frozen:

- venue and instrument;
- session timezone and boundary;
- source timeframe;
- tick/bin size;
- rule for distributing a bar across the bins between low and high;
- partial-session and missing-bar behavior.

This is a time-at-price estimate. Coarse candles lose the intrabar path and may
overstate bins that price crossed only briefly.

### Estimated candle Volume Profile

Aggregate candle volume can be assigned to the close, typical price, or spread
across the high-low range. Any resulting POC, VAH, VAL, HVN, or LVN is sensitive
to that arbitrary allocation. Such output must be named and tagged as, for
example:

```text
estimated_profile_from_ohlcv_v1
allocation = typical_price | uniform_range | other frozen rule
```

It must never be labeled as exchange-reported volume at price.

## What requires a new input

An authoritative Volume Profile and POC require sequenced trades containing at
least event time, price, size, stable identity/dedup key, venue, and instrument.
Aggressor-side features additionally need trustworthy side classification.
Order-flow imbalance from the book requires sequenced snapshots or deltas with
gap recovery. Liquidation profiles require venue-reported liquidation events;
they cannot be inferred from candles alone.

## Incremental computation shape

For a frozen profile contract, the minimum state is:

```text
(instrument identity, session/window identity, price bin)
  -> tpo count, estimated or observed volume, last update
```

Normal closed-bar append work is proportional to the bins touched by one bar.
Session rollover finalizes one immutable profile and starts the next. A revised
bar must remove its prior contribution before the corrected contribution is
added; if that prior contribution is unavailable, rebuild the bounded session.
Missing intervals degrade the profile and forming bars remain excluded.

## Storage and token implications

- Do not send raw candle windows to the LLM. Compute a compact profile snapshot
  once and pass the nearest levels, distances, session identity, and quality.
- Store raw observed events separately from derived bins so algorithms can be
  replayed after a rule change.
- Do not create a new compressed representation before measuring SQLite growth
  and query pressure under the actual trade feed.
- If cold storage becomes necessary, choose its physical format in a separate
  benchmark using write size, read range, recovery, checksum, and operational
  complexity. The existing format-neutral receipt boundary should remain.

## Recommendation

Do not implement a production `POC` from current OHLCV and present it as market
truth. The low-cost next research step is either:

1. an explicitly estimated OHLCV profile used only in offline evaluation; or
2. a bounded public trade capture for one venue and a true session Volume
   Profile computed from those trades.

Option 2 is the preferred evidence path if POC is expected to influence an LLM
decision. It resolves the largest uncertainty at the data layer rather than
tuning a candle allocation heuristic.
