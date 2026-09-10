---
id: derivatives-facts
status: accepted
github_issue: 2890
related_contracts:
  - ../contracts/market_data_coverage_contract.md
supersedes: []
---

# Minimal canonical Derivatives Facts

## Summary

Keep the existing collectors and Funding/OI/Liquidation tables. Introduce one
small typed normalization/read contract for linear USD-stable perpetual facts,
with explicit venue, instrument, source scope, clocks, units and value kind.
New cross-venue output is observation/readiness only. The implementation and
acceptance evidence belong to [#2890](https://github.com/iridyne/aletheia/issues/2890).

## Current fact

The existing [coverage contract](../contracts/market_data_coverage_contract.md)
separates collector health from persisted coverage. Primary CCXT Funding/OI,
native Hyperliquid current/predicted Funding/OI and Coinalyze aggregate-tagged
rows already share tables. Binance global-account and top-position ratios are
embedded in primary OI rows. Source tags currently encode provider, venue and
forecast semantics together. The current source inventory is recorded in #2890.

Primary funding annualization assumes eight hours. The shared OI extractor
accepts an amount multiplied by mark without contract-unit proof. Neither rule
is sufficient evidence for a new comparable fact. Hyperliquid canonical USDT
aliases do not prove its physical quote/settlement asset. Unknown historical
metadata must remain unknown.

## Goals and non-goals

Expose one source-independent read contract, preserve existing consumer outputs,
and reuse current scheduling, managed CCXT sessions, freshness and storage.
Do not modify Candidate economics, Entry, accounting, Risk, Gateway/UTA, Live,
Learning ownership or historical evidence. Positioning remains observe-only.
No generic fact store, plugin framework, permission certificate or new scheduler.

## Proposed design

### Fact meaning

A fixed typed fact contains the #2890 fields: fact type, provider, source scope,
exact venue (null for aggregate), physical instrument identity, source time,
acquisition time, availability, value kind, unit, finite value, optional source
reference and typed unavailable reason. Only linear stable-settled perpetuals
are eligible. Do not derive quote/settlement from a display alias.

Funding additionally retains a proved interval in seconds; missing interval
permits the observed rate but prohibits normalized comparison. Forecast and
observed rates remain separate. OI accepts proved USD notional, or converts a
proved base/contract quantity with exact contract metadata and compatible mark;
unknown units yield unavailable. OI change requires matching provider, scope,
venue, physical instrument and unit semantics within the existing baseline band.

Reuse `data/freshness.py` for both clocks and staleness. Source-time absence is
not acquisition time masquerading as source time. Reference kind (index/oracle)
and liquidation side/coverage are explicit family semantics. A zero-price
Coinalyze storage sentinel projects as absent event price, not economic zero.

### Ownership and physical storage

| Fact | Surviving acquisition owner | Durable owner | New read owner |
|---|---|---|---|
| Primary current Funding/OI | DerivativesCollector, managed exact-venue CCXT | existing snapshot repositories | data/derivatives_facts.py |
| Primary historical Funding/OI | same collector history path, historical semantics | same tables; existing duplicate checks | same projection |
| Hyperliquid current/forecast/OI | native HyperliquidCollector | existing scheduler batch writer | same projection, explicit value kind |
| Binance ratio populations | current Binance public requests | originating OI row plus population/source metadata | separate typed facts; never primary-venue ratios |
| Coinalyze OI/liquidation | existing optional collector | existing OI/liquidation tables | separate market-aggregate view only when provenance proved |
| Additional native venue facts | same CCXT collection path, excluding owned primary/native pairs | same repository writers | same projection |

The current scheduler first selects active executable Universe members and only
then falls back to configured observations. A new comparison workset must be
resolved independently of that Candidate-ranked path; it must not replace that
path or alter existing consumer inputs. There is no duplicate current table
writer on the audited Bitget/native-Hyperliquid configuration. A configured
Hyperliquid CCXT primary could overlap the native context path: native ownership
must win before such comparison fan-out is enabled, without deleting either
historical provenance or unrelated source observations.

Feature acquisition is an existing non-table-writing consumer path; it does not
become a canonical Facts writer. Native Hyperliquid retains authority when it is
also in the comparison set. Historical backfill and current snapshots are
separate observation meanings, not competing current writers. Comparison
selection uses existing observed instruments/active registry plus required
primary/open-position instruments; no score/outcome selection or tradability
mutation. Reuse existing request caps and bounded rotation where per-symbol
calls cannot cover the full set.

Add only nullable source-semantic columns to existing rows where required:
provider/scope, physical instrument metadata, value kind, interval or OI unit
basis, and population-specific source times for embedded ratios. These are
facts, not blanket fingerprint trees. Final column choices must correspond to
an actual normalization/dedup/read need. Historical rows are not backfilled.
Independent mark/reference observations cannot truthfully be stored as Funding
rows when no funding observation exists; a narrow mark/reference table is the
only permitted additional table if this case is exercised. No generic events.

Network and normalization complete before the writer transaction. Preserve
existing context-domain savepoint isolation. Additive provenance must participate
in meaningful dedup comparisons so an unchanged numeric value cannot suppress
newly known semantics. No Learning writer is called by canonical normalization
or projection; existing collector coupling remains outside this change.

### Consumer equivalence and honest unknowns

Canonical observations and existing consumer scalar outputs are two projections
of the same physical observation, never two collectors/stores/authorities.
Preserve historical and current consumer numbers, rounding, freshness behavior
and rendered context in P1. Canonical readers do not inherit unsupported claims
from legacy annualization, amount conversion or source tags. Any correctness
repair changing those consumers needs separately evidenced owner acceptance.

Coinalyze source-contract discrepancies must remain explicit: its published API
uses `value/update`, optional `convert_to_usd`, and nested history; existing code
expects USD-specific fields and flat liquidation records. A suffix alone does
not prove market aggregation. Do not reinterpret old rows or silently enable a
repair affecting existing context; use unavailable until the provider meaning
and desired correction boundary are resolved. See the
[official API contract](https://api.coinalyze.net/v1/doc/).

### Readiness and failure

One read-only projection returns configured/listed venues, usable facts and
unavailable/stale/unsupported/deferred/error outcomes. Aggregate sources remain
separate. Dispersion/covered subtotal requires two compatible exact venues;
never label the subtotal global. Premium requires compatible independently
identified mark/reference observations and a source-justified cadence bound,
not a universal arbitrary skew. No new setting is needed unless existing
observed-instrument configuration cannot represent comparison intent.

## Trust and complexity review

An unproved unit is an unavailable fact, not a fallback to a different venue.
Additional sources cannot change existing latest-row consumers through a new
implicit venue fallback. Existing consumers using omitted exchange must be
classified before enabling fan-out. Preserve primary and native writer ownership
and source-failure isolation. Fixed family functions and one read projection are
sufficient; no registry/plugin/second state machine is required.

Comparison Funding/OI rows carry `observation_only` provenance. Existing scalar,
context, fill-evidence, history and coverage readers exclude those new rows;
the canonical reader includes them. Current primary/native Funding/OI are not
recollected by comparison coverage. Independently acquired Mark/reference pairs
use `perp_price_snapshots`, retaining one response's clock and explicit reference
kind; the native Hyperliquid collector remains their Hyperliquid writer.

`market_data.derivatives_comparison_venues` is the single comparison-source
setting (Binance/OKX/Bitget; empty disables extra coverage), editable in existing
Settings. `observed_instruments` cannot represent this switch without also
changing its existing market-context consumers. The workset unions active
registry instruments with configured and open-position requirements; it does
not consume Candidate ranking. Latest attempt coverage reuses one bounded
`collector_health_state` row. Per-symbol endpoints rotate four instruments per
source attempt within a ten-second source budget; batch endpoints cover the
listed workset. Mark/reference retention reuses the configured OI retention.

## Verification contract

Frozen current outputs cover primary Funding/OI and both Binance populations,
Hyperliquid current/forecast, Coinalyze aggregate-tagged inputs, context rendering
and exact-venue repository reads. Contract RED probes cover unknown intervals,
ambiguous OI units, explicit instrument/value-kind/scope, nonfinite values,
missing source time and source independence. Later source fixtures prove actual
CCXT/native semantics; coverage tests prove non-duplication and partial failure.
Fedora acceptance requires exact-SHA public data, restart/idempotency, retention,
request bounds and old-consumer continuity, not natural trading.

## Rollout and rollback

The owner accepted this D2 boundary before production implementation. Canonicalize
existing rows before fan-out; add cross-venue readiness only after equivalence.
Disable only additional coverage on instability, preserve primary/native facts
and all stored history. Never restore a second writer as a compatibility path.

## Resolution

Accepted by owner as the implementation boundary for #2890. Existing consumers
remain golden-equivalent; changes to legacy economic semantics or Candidate
behavior require a separate owner decision. Acceptance does not claim implementation.
