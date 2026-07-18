# Multi-Settle Instrument Model

## Background

Derivatives exchanges increasingly list the same perpetual contract in multiple
settlement currencies. OKX, for example, offers both `BTC/USDT:USDT` and
`BTC/USDC:USDC` as separate listed markets. Both give the trader the same
directional BTC exposure; what differs is the currency in which P&L and margin
are denominated.

This document records the design intent for how Aletheia should handle
multi-settle instruments — both what the current model already gets right and
what needs to change when we extend coverage.

---

## Core principle

**Same underlying, different settlement instrument.**

`BTC/USDT` perp and `BTC/USDC` perp are the same underlying (BTC delta) but
distinct tradeable products. They must not be collapsed into a single canonical
symbol, because:

- Execution targets a specific listed market. You can only hold one position per
  exchange unless you open the other market separately.
- Funding rates can diverge between settle variants (different open-interest
  pools, different arbitrageur flows).
- Liquidity and spread differ across variants.

At the same time they must not be treated as fully independent assets for signal
analysis, because:

- Total-market BTC perp OI = sum of all settle variants. Seeing only the USDT
  pool gives a systematically low reading on venues where USDC has meaningful
  share (e.g. OKX).
- Crowding signals (funding rate, OI delta, long/short ratio) need the
  cross-variant view to reflect true market positioning.
- A futures basis or funding arbitrage strategy between USDT and USDC variants
  of the same underlying requires comparing them explicitly.

---

## Current state (as of 2026-07)

### What works correctly

`to_canonical()` strips the CCXT settle suffix before storing:
`BTC/USDT:USDT` → `BTC/USDT`, `BTC/USDC:USDC` → `BTC/USDC`.
The two are distinct at the canonical level throughout storage, risk, and P&L.

`to_ccxt_perp()` derives the settle suffix from the quote currency, so it
handles both correctly: `BTC/USDT` → `BTC/USDT:USDT`, `BTC/USDC` → `BTC/USDC:USDC`.

`_KNOWN_QUOTES` in `symbols.py` includes USDC, so separator-less venue symbols
like `BTCUSDC` parse correctly.

### Known gap

`_to_perp_symbol()` in `aletheia/data/collectors/derivatives.py` (line ~221)
hardcodes `:USDT` as the settle suffix when building CCXT target symbols. Any
observed instrument whose canonical quote is USDC silently produces a
non-existent CCXT symbol and is skipped. This was noted in the 2026-07-18 data
fetch audit as a low-risk issue for the current USDT-only scope.

---

## Design for USDC perp support

### 1. Fix `_to_perp_symbol` (data/collectors/derivatives.py)

Replace the hardcoded suffix with a quote-derived one — the same logic
`to_ccxt_perp()` already uses:

```python
# Current (wrong for USDC):
def _to_perp_symbol(canonical: str) -> str:
    return canonical.replace("/", "") + ":USDT"

# Fixed:
def _to_perp_symbol(canonical: str) -> str:
    _, _, quote = canonical.partition("/")
    settle = quote or "USDT"
    return f"{canonical}:{settle}"
```

This change alone is backward-compatible: USDT symbols produce identical output.

### 2. Data collection: collect variants separately, store with canonical symbol

The data layer should collect both `BTC/USDT` and `BTC/USDC` from any venue
that lists them, writing each row with its own canonical `symbol` column value.
No schema change is needed — `open_interest_snapshots`, `funding_rate_snapshots`,
and `orderbook_snapshots` all key by `(symbol, exchange)`.

### 3. Analysis layer: aggregate across settle variants

Add a helper in `repositories.py` (or `context_builder.py`) for
"base-asset total OI":

```python
def total_oi_for_base(base: str, exchange: str | None = None) -> float | None:
    """Sum open_interest_usd across all BTC/USDT, BTC/USDC, BTC/USD… rows."""
```

`compute_leverage_state()` in `normalizer.py` and the crowding-score path in
`context_builder.py` should use this aggregate view when the signal is intended
to reflect total-market positioning. Per-variant signals (e.g. comparing USDT
vs USDC funding to detect basis dislocation) should use the per-symbol rows
directly.

### 4. Universe and execution

Each settle variant is a separate `InstrumentId` and can be listed independently
in the active universe. The execution adapter (`to_bitget`, `to_binance`, etc.)
already operates on the full canonical symbol including the quote currency, so
no routing change is needed.

Risk netting: a long `BTC/USDT` perp position and a long `BTC/USDC` perp
position on the same exchange both contribute BTC delta. The risk engine must
net them under the same base-asset exposure bucket, not treat them as independent
assets. This is risk-engine scope and is tracked separately.

---

## What USDT and USDC are NOT

- **Not the same stablecoin.** USDT and USDC carry different credit risk and
  have historically depegged at different times. Do not collapse their quotes
  for price or value calculations. `BTC/USDT` and `BTC/USDC` spot are different
  liquidity pools.
- **Not interchangeable for execution.** You cannot substitute one market for
  the other at fill time; the execution adapter must target the exact market.
- **Not a conversion problem.** A USDC-settled P&L in USDT terms requires a
  USDC/USDT exchange rate. This is exchange-rate scope, not symbol-model scope.

The existing `to_yfinance()` mapping (`USDT/USDC → USD`) is a read-only
adapter convenience for fetching TradFi reference prices and does not imply
any equivalence in the Aletheia data model.

---

## Migration checklist (when implementing)

- [ ] Fix `_to_perp_symbol` in `derivatives.py` (backward-compatible one-liner)
- [ ] Confirm `_perp_target_set()` in `derivatives.py` includes USDC perps for
      batch venues (OKX `fetchOpenInterests` / `fetchFundingRates` returns both
      when no filter is applied — verify empirically)
- [ ] Add `total_oi_for_base()` aggregation helper in `repositories.py`
- [ ] Update `context_builder.py` crowding path to use the aggregate view
- [ ] Add `BTC/USDC` (and `ETH/USDC`) to default observed-instrument set for
      OKX deployments
- [ ] Risk engine: net USDT and USDC perp positions under the same base-asset
      exposure bucket
- [ ] Data coverage: decide whether the coverage report tracks `BTC/USDT` and
      `BTC/USDC` OI separately or as a single aggregated domain
- [ ] Verify `to_coinalyze_symbol()` handles USDC canonical input
      (`BTC/USDC` → `BTCUSDC_PERP.A` or exchange-specific form)
