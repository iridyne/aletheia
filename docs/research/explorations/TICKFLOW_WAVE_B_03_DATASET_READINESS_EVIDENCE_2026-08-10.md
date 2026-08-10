# TickFlow Wave B-03 Dataset Readiness Evidence — 2026-08-10

## Scope

This investigation closes the contract decision for catalog items 27, 37, 46,
and 49 after the exact-OHLCV provider boundary was accepted. It is dated
evidence, not a runtime status report.

## Consumer-specific OHLCV fallback

No current production consumer requires coarse OHLC under an exact timeframe
identity:

- the dashboard OHLCV route requests an exact instrument/timeframe and already
  limits SQLite recovery to that identity;
- Market Context, Regime, and Universe outcome collection compute named
  timeframe features, so silently coarser candles would corrupt their meaning;
- History Downloader uses native CCXT for exact historical pages;
- Protective Barrier uses native CCXT and exact venue identity rather than the
  public spot fallback chain.

The existing OHLCV Provider Result Contract is therefore sufficient. A consumer
policy matrix or new coarse-price result type would be speculative. Add one only
after a named consumer demonstrates an availability requirement that outweighs
the loss of exact resolution and field semantics.

## Existing dataset facts

Aletheia already separates the two facts that the TickFlow readiness candidate
was meant to protect:

1. `collector_health_state` records whether each scheduled collector ran and
   how it finished.
2. `data_coverage.py` reads the persisted market-data tables and classifies each
   domain as `fresh`, `stale`, `empty`, `invalid`, or `idle` using an explicit
   age limit.

The coverage report already includes row count, distinct symbol count, source
counts, newest observation, age, criticality, and the configured freshness
limit. Overview displays Collector health and Data coverage separately. Adding
a second readiness registry, schema, or service would duplicate current facts.

The current behavior is now owned by the Market Data Coverage and Collector
Health Contract. No runtime code or API response changed in this slice.

## Readiness vocabulary decision

`/_infra/livez` and `/_infra/readyz` keep their current infrastructure meanings.
Market-data coverage does not change either HTTP result and does not become a
trading gate. A broad `alive/interface-ready/data-ready/operation-ready` aggregate
was not added because no current consumer needs it and one shallow aggregate
would hide the distinct causes already exposed by the two reports.

## Completeness decision

Retained row and symbol counts cannot prove requested-scope completeness. The
catalog's proposed requested/accepted/rejected coverage, publication ID, and
generation pointer require one named derived dataset with a real replacement or
publication boundary. None is selected in this slice, so those fields were not
invented and no generic completeness envelope was added.

When a concrete derived dataset needs atomic replacement, its proposal must name
the requested scope, accepted and rejected units, coverage threshold,
publication identity, old-generation retention rule, and recovery behavior.

## Result

- Item 27: deferred as unnecessary under the exact-OHLCV contract.
- Item 49: covered by the existing collector-health and data-coverage paths;
  their current behavior is now formalized as a Contract.
- Item 46: existing infrastructure probe meanings are preserved; no aggregate
  readiness endpoint is added without a consumer.
- Item 37: deferred until a named derived dataset has a publication boundary.

This outcome adopts the useful semantic separation from TickFlow without adding
another state owner or implying business readiness from shallow health facts.
