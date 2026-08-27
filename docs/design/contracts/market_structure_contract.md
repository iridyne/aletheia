# Market Structure Derived Data Contract

## Purpose and current scope

Aletheia derives neutral, causal Swing, structure-state, BOS, CHOCH, and
optionally FVG facts from exact-instrument closed OHLCV. This domain is a
sibling of technical analysis and has observation authority only. It does not
authorize Candidate admission, Trigger thresholds, Risk, execution, or
Learning behavior.

## Entry points

- `MarketStructureProvider.get_snapshot()` performs one bounded latest or
  historical read through an injected `MarketDataProvider`.
- `derive_market_structure()` is the pure bounded closed-bar geometry kernel.
- `derive_fvg_zones()` is the optional pure-Python FVG V1 enrichment.
- `universe.market_structure.evaluate_market_structure()` preserves the Fast
  Trigger candle-dict API as a `market-structure/v2` projection over that same
  kernel.

## Durable state and consumers

The provider snapshot is query-time derived data and adds no table, cache,
scheduler, or durable event ledger. Exact `InstrumentId`, timeframe, admitted
bar window, provider/exchange identity, source outcome, history quality,
algorithm/parameter identity, and full source/geometry fingerprints are carried
in `StructureProvenance`.

Fast Trigger remains the current production consumer. It calculates from its
in-memory closed 1m bars without provider, database, or network I/O and persists
the existing `check_type="market_structure"`, `authority="shadow"` assessment.
Historical `market-structure/v1` rows are not rewritten; new compatibility
facts use `market-structure/v2` over core `causal-market-structure/v1`.

## Observable result

For one exact instrument, timeframe, and optional knowledge cutoff, an
available `MarketStructureSnapshot` reports the final neutral state, ATR14 and
break tolerance, bounded confirmed Swings, named BOS/CHOCH events, and optional
FVG zones. Thirty through 119 continuous bars are partial history; 120 bars are
complete history. A valid empty event or FVG tuple is available, not missing.

Swings become known at the close of their second right-side confirmation bar.
BOS/CHOCH becomes known at the breaking bar close. FVG becomes known at its
third bar close, and mitigation records the first eligible later bar close
without changing zone identity. `known_at_ms` is causal market-data time, not
proof of network receipt or freshness.

## Invariants and trust boundaries

- No fact is observable before its canonical `known_at_ms`; `computed_at_ms`
  is provenance only and never enters fact identity.
- Closed bars are strictly increasing, unique, finite, economically valid, and
  exactly continuous at the canonical `timeframe_ms()` duration. The component
  never interpolates, reorders, deduplicates, or substitutes another venue.
- Swing high/low uses independent strict symmetric `K=2` tests. State uses only
  confirmed Swings. The newest same-kind Swing replaces the active break level,
  and each active level is consumed on its first qualifying cross.
- ATR14 is the arithmetic mean of the latest 14 True Range values; it is not
  the shared Wilder ATR. Break equality at the `0.05 * ATR14` boundary is not a
  cross.
- Fact IDs and fingerprints use full lowercase SHA-256 over canonical JSON.
  Source identity changes the source fingerprint; provider wrapping and
  calculation time do not change geometry or fact identity.
- FVG is query-requested only, adds no pandas/numba/SMC runtime dependency, and
  never enters the Fast Trigger hot path.
- `HARD_GATE_ALLOWLIST` remains empty. Market structure alone cannot block
  Opportunity materialization, Risk approval, or execution.

## Failure and recovery semantics

Caller contract misuse raises one stable `ValueError`. Provider/network,
empty, insufficient, invalid, discontinuous, historical-tail, or unproven-venue
conditions return an explicit unavailable snapshot with provenance; they never
become an empty success. Cancellation propagates and the injected provider
remains caller-owned.

If requested FVG calculation fails after valid core derivation, the core
snapshot remains available while FVG is explicitly unavailable. Repeating the
same historical cutoff over an unchanged source prefix returns the same
semantic facts even after future bars arrive. Because the read window is
bounded to 120 bars, this is not a durable historical event ledger.

## Executable evidence

- `tests/test_market_structure.py` proves causal Swing/event/FVG time,
  state-machine behavior, exact provider reads, replay stability, fingerprints,
  identities, failure semantics, and provider ownership.
- `tests/test_universe_fast_trigger.py` proves the v2 compatibility projection,
  no repeated final-bar event, empty hard allowlist, and Shadow-only
  non-authority.
- `tests/specs/spec_market_structure.py` proves structural convergence and the
  current cross-boundary invariants.

## Known gaps

There is no current behavior-authority, durable event ledger, incremental
service, multi-timeframe aggregation, Order Block/Liquidity model, or Alpha
claim. Those require separate approved work with a concrete consumer.
