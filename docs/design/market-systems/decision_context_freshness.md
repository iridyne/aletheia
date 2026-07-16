# Decision Context Freshness

## Purpose

Persisting a market-data row does not make it safe to use forever. Decision
consumers must not treat an old orderbook, funding snapshot, open-interest row,
macro snapshot, or positioning map as a current observation merely because it is
the newest row still present in SQLite.

`aletheia/data/freshness.py` is the single policy for enriched data entering
Universe scoring or LLM market context. It does not alter values, interpolate
missing fields, retry providers, or change risk and execution behavior.

## Policy

| Domain | Maximum decision age | Normal collection cadence |
|---|---:|---:|
| Orderbook | 15 minutes | about 2 minutes |
| Primary funding | 15 minutes | about 2 minutes |
| Primary open interest and LSR | 15 minutes | about 2 minutes |
| Macro snapshot | 30 minutes | about 10 minutes |
| DefiLlama on-chain snapshot | 30 minutes | about 10 minutes |
| Cross-venue funding | 30 minutes | about 10 minutes |
| Perp positioning structure | 60 minutes | source-specific |

The limits intentionally allow multiple bounded collector attempts. They are
decision-use limits, not claims about the underlying economic series' update
frequency. For example, FRED values may be daily, but the macro row's
`observed_at` proves when this installation last verified and assembled them.

Freshness is fail-closed:

- Missing and malformed timestamps are unusable.
- Rows older than the domain limit are unusable.
- Timestamps more than five minutes in the future are unusable. This prevents a
  bad clock or corrupt row from remaining fresh indefinitely.
- A snapshot is accepted or rejected as a unit. A stale funding row cannot lose
  only `funding_rate` while retaining its annualized derivative; a stale OI row
  cannot retain its delta or long/short ratio.

Consumers represent unusable data as absent. They do not substitute zero,
"neutral", or a reassuring state. If no fresh derivative evidence remains, the
derivatives block is omitted rather than rendered as an all-`n/a` observation.

## Identity and source boundaries

Where an instrument venue is known, orderbook, funding, and OI reads use that
exact venue. A same-symbol row from another exchange is not a fallback.

Coinalyze is a cross-exchange aggregate source. It remains stored and available
to explicitly tagged research consumers, but it is excluded from the repository's
default "primary venue" derivative read together with Hyperliquid secondary rows.
This prevents aggregate OI from silently replacing the execution venue's OI.

## Runtime paths

- `build_market_context_from_db()` owns primary orderbook, funding, OI/LSR, recent
  liquidation, and macro assembly.
- `market_observer` delegates that assembly instead of duplicating raw DB reads;
  it separately freshness-gates on-chain, cross-venue, and perp-structure blocks.
- `universe/features.py` applies the same policy to DB fallbacks. A successful
  live funding fetch may replace the fallback; a failed fetch cannot revive a
  stale cached value.

## Non-goals

- No automatic collector restart or threshold widening.
- No cross-exchange value substitution.
- No change to scanner weights, opportunity filters, risk limits, or execution.
- No claim that a fresh observation is correct, only that it was observed within
  the bounded decision window.
