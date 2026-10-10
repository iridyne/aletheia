# Derivatives Economic Semantics Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `DE` · **Owner code:** `core/aletheia/data/derivatives_facts.py`

## Scope

This contract owns the unit, Funding annualization, source-proved projection and Open
Interest (OI) baseline semantics of every current Funding/OI writer and reader. On the
trading mainline, Universe feature collection reads the asset's own-venue Funding and OI
facts as Candidate inputs; collectors, repository reads, context and API reuse the same
primitives. Comparison sources (Hyperliquid, Coinalyze, configured comparison venues) are
observation-only. This contract grants no provider admission, Positioning promotion or
execution authority. Convergence narrative is in
[history](../history/derivatives_semantics_convergence_notes.md).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `extensions/data/collectors/derivatives.py::DerivativesCollector.collect` (`_parse_funding`, `_parse_oi`) | scheduler collector | `core/aletheia/agent_runtime/scheduler.py::_async_data_collectors` |
| `extensions/data/collectors/derivatives.py::DerivativesCollector.collect_comparison` | scheduler collector (observation-only) | `core/aletheia/agent_runtime/scheduler.py::_async_data_collector_context` |
| `extensions/data/ccxt_provider.py::CCXTProvider.get_funding_observation`, `get_open_interest_observation` | call | `core/aletheia/universe/features.py::_fetch_single_feature` |
| `core/aletheia/data/derivatives_facts.py::project_runtime_snapshot` | call (current carrier view) | `core/aletheia/storage/repositories_market_data.py::latest_funding_rate`, `latest_open_interest`; `core/aletheia/server/routes/data.py` |
| `core/aletheia/data/derivatives_facts.py::derivatives_readiness` | API diagnostic | `core/aletheia/server/routes/data.py` |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `funding_rate_snapshots` (`funding_rate` per settlement, nullable `funding_rate_annualized`, `derivatives_metadata_json` with `interval_seconds`) | `DerivativesCollector` | `latest_funding_rate` → Universe features, `core/aletheia/universe/candidate_funding.py::stored_funding_proof`, API | append-only; raw history never rewritten |
| `open_interest_snapshots` (`open_interest_usd`, `oi_change_1h_pct`, `derivatives_metadata_json` with `oi_unit`, `observation_only`) | `DerivativesCollector` | `latest_open_interest` → Universe features, API, OI baseline | append-only |
| `universe_feature_snapshots.open_interest`, `.oi_delta_1h` | `core/aletheia/universe/features.py::_fetch_single_feature` | Candidate selection | append-only (change-suppressed) |

## Requirements

### DE-C01 USD OI needs explicit proof

`The OI conversion shall yield a USD value only from explicit provider USD proof, or from a proved base or contracts quantity with a valid instrument (contract size for contracts) and a positive Mark price; otherwise it shall return no value with a reason.`

- WHEN a response has `openInterestAmount` and a Mark price but no unit proof THEN the value is `None` with `oi_unit_unproven`.
- WHEN a unified field name alone suggests USD THEN that is not proof.

Evidence: `core/aletheia/data/derivatives_facts.py::open_interest_usd`, `ccxt_metadata`.

### DE-C02 Unknown units and non-finite values are unavailable

`If a quantity unit is unknown or a result is non-finite or negative, then the OI fact shall be unavailable, and a CCXT amount shall never be relabelled as base coins.`

Evidence: `core/aletheia/data/derivatives_facts.py::open_interest_usd`; `extensions/data/collectors/derivatives.py::_parse_oi` (`oi_coin = None`).

### DE-C03 Funding stays per-settlement; annualization needs a proved interval

`The system shall keep observed Funding as the source's per-settlement rate and shall annualize it as rate × 365 × 86400 / interval_seconds only for a proved positive interval, returning None otherwise.`

- WHEN the interval is unknown (including predictions and history without proof) THEN `funding_rate_annualized` is `None`, never an assumed 8h value.
- WHEN the interval is 1h THEN annualization uses 3600 s.

Evidence: `core/aletheia/data/derivatives_facts.py::annualize_funding`, `funding_interval_seconds`.

### DE-C04 OI change needs a compatible 45–75 minute baseline

`When an OI change is computed, the system shall use only a baseline from the same provider, source scope, venue, instrument, unit, value kind and clock basis observed 45 to 75 minutes earlier (closest to 60), and shall report the change unavailable when no such baseline exists.`

- WHEN only a baseline 40 minutes old exists THEN the change is unavailable with `compatible_baseline_missing`.
- WHEN a stored legacy delta or a magnitude heuristic is available THEN it does not substitute for the baseline.

Evidence: `core/aletheia/data/derivatives_facts.py::_open_interest_change`.

### DE-C05 Current readers expose only proved economics

`The current repository, context and API reads shall project stored rows through project_runtime_snapshot so unproved historical USD OI or annualization is not exposed as valid economics, while raw history stays readable without rewriting values, identifiers, timestamps or provenance.`

- WHEN a legacy row has no provenance metadata THEN its fact carries `legacy_provenance_unknown` and the current OI view is `None`.

Evidence: `core/aletheia/data/derivatives_facts.py::project_runtime_snapshot`, `project_snapshot`.

### DE-C06 Corrected OI gets a new Feature snapshot

`When the open_interest or oi_delta_1h value of a Feature differs from the previous snapshot, Universe feature collection shall write a new Feature snapshot instead of reusing the previous identity under the numeric suppression interval.`

Evidence: `core/aletheia/universe/features.py::_fetch_single_feature` (`derivatives_changed`).

### DE-C07 Comparison observations stay observation-only

`The primary/native Funding and OI selectors shall exclude rows from hyperliquid, hyperliquid_predicted and coinalyze and rows marked observation_only, and Universe features shall read derivatives only from the asset's own venue.`

Evidence: `core/aletheia/storage/repositories.py::_latest_deriv_where` (`_CROSS_VENUE_TAGS`, `LEGACY_DERIVATIVES_WHERE`), `core/aletheia/universe/features.py::_load_fresh_enrichment_rows`, `core/aletheia/data/derivatives_facts.py::derivatives_readiness` (`mode="observe_only"`).

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| OI unit unproved | no OI snapshot / `None` value | `oi_unit_unproven` | unavailable, never `0` |
| Funding interval unknown | per-settlement rate kept; annualized `None` | `funding_rate_annualized IS NULL` | `None`, never 8h-assumed |
| No compatible OI baseline | change unavailable | `compatible_baseline_missing` | `None`, never `0%` |
| Legacy row without provenance | projected unavailable | `legacy_provenance_unknown` | raw row unchanged |

## Authority

Produces Funding/OI facts only; may never change Candidate Utility, Entry, Risk, Gateway or
Live parameters.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| DE-C01, DE-C02 | `tests/test_derivatives_semantics.py::test_contract_quantity_and_nonfinite_values_use_one_canonical_primitive` |
| DE-C03 | `tests/test_derivatives_semantics.py::test_current_funding_annualization_and_history_read` |
| DE-C05 | `tests/test_derivatives_semantics.py::test_unproved_history_remains_readable_without_current_economic_authority`, `::test_nullable_migration_preserves_historical_values_rowids_and_indexes`, `::test_context_and_microstructure_do_not_display_unproved_annualization` |
| DE-C04, DE-C07 | `tests/test_derivatives_semantics.py::test_current_oi_baseline_does_not_promote_comparison_observations` |
| DE-C06 | `tests/test_universe_features.py` |

### Known conformance gaps

- USD proof for OI exists only for `okx` (`oiUsd`) and `binance` (`sumOpenInterestValue`,
  `sumOpenInterestUsd`) in `core/aletheia/data/derivatives_facts.py::ccxt_metadata`, and every
  collector passes only `value_unit`, never `amount_unit`
  (`extensions/data/collectors/derivatives.py::_parse_oi`,
  `extensions/data/ccxt_provider.py::CCXTProvider.get_open_interest_observation`). On `bitget`
  every OI observation is therefore `oi_unit_unproven`: no primary OI snapshot is written and
  Candidate OI and OI change are always unavailable on the product venue. This is fail-closed and
  conforms to DE-C01, but the base/contracts proof path is unused. Runtime corroboration: 24h of Fedora data showed 0 Bitget `open_interest_snapshots` rows
  ([capability tracking 2026-10-09](../../research/audits/OPPORTUNITY_LIFECYCLE_CAPABILITY_TRACKING_2026-10-09.md)).
- Long/short and top-trader ratios on `open_interest_snapshots` are fetched from Binance USD-M and
  projected with `provider="binance_public"` (`project_snapshot`), a frozen venue.

## Decision references

- Owner-approved convergence: #3067 (with #3065); the #2890 golden-equivalence boundary is
  historical ([history](../history/derivatives_semantics_convergence_notes.md)).
- Schema v82 made `funding_rate_annualized` nullable, preserving values and rowids
  (`core/aletheia/storage/migrations.py::_apply_nullable_funding_annualization_v82`).
