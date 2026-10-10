# Candidate Selection Utility Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `CS` · **Owner code:** `core/aletheia/universe/{candidate_eligibility,opportunity_evidence,economic_utility,candidate_selection_utility,candidate_selection_shadow,candidate_opportunity}.py`

## Scope

Candidate Selection Utility (CSU) is the first stage of the trading mainline: it decides
which exact instruments receive a scarce Candidate slot on each Universe refresh, and
hands each selected Candidate a typed opportunity context for the Entry Planner. Admission
is `measured` when the economic estimator has support, otherwise `startup` (ordering by
conservative signal strength); both feed the same Planner path. CSU does not choose Entry
geometry, size, leverage, order type, Risk exceptions, credentials, Gateway behavior or any
exchange action. Historical six-family and `total_score` records are immutable audit
evidence, not a runtime fallback; the six-family scorer survives only as the research
baseline in [Universe Baseline Integrity](universe-baseline-integrity.md).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/universe/registry.py::sync_registry` | call | Universe refresh (registry sync) |
| `core/aletheia/universe/service.py::UniverseService.refresh` | scheduler job `universe_refresh` (every 10 minutes) | `core/aletheia/agent_runtime/scheduler.py` |
| `core/aletheia/universe/candidate_selection_shadow.py::build_candidate_selection_shadow_bundle` | call | `service.py::_build_candidate_selection_bundle` |
| `core/aletheia/universe/candidate_eligibility.py::evaluate_hard_gate` | call | `build_candidate_selection_shadow_bundle` |
| `core/aletheia/universe/opportunity_evidence.py::build_opportunity_evidence`, `::annotate_within_run_percentiles` | call | `build_candidate_selection_shadow_bundle` |
| `core/aletheia/universe/economic_utility.py::estimate_economic_utility` | call | `build_candidate_selection_shadow_bundle` |
| `core/aletheia/universe/candidate_selection_utility.py::build_candidate_selection_utility`, `::select_admitted_candidates` | call | `build_candidate_selection_shadow_bundle` |
| `core/aletheia/cli/evidence_cmd.py` (`aletheia evidence candidate-selection status`) | CLI, read-only historical audit | operator |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `universe_registry` (incl. blacklist flag and `blacklist_reason`) | `registry.py::sync_registry` → `storage/repositories_universe.py::sync_universe_assets` | Universe refresh, Workbench | replaceable per sync |
| `universe_twin_shadow_batches`, `universe_twin_shadow_records` | `storage/repositories_universe.py::insert_universe_twin_shadow_bundle` | estimator training, alpha attribution, CLI audit | append-only; one batch per `scanner_run_id` |
| `universe_members.candidate_opportunity_context_json` (+ `_version`, `_hash`) | `storage/repositories.py::publish_candidate_snapshot` | Entry Planner, Workbench | immutable per member |
| `filter_candidate_outcomes` (rows with `scanner_family=candidate_selection_utility`) | same bundle write | estimator, settler | see [Opportunity Outcome contract](opportunity_outcome_contract.md) |

The Twin table names are inherited from the retired Twin shadow experiment
([history](../history/universe_twin_shadow_contract.md)); CSU is their sole current writer.

## Requirements

### Instrument scope (registry)

The product trades USDT-margined crypto perpetuals only
([Capability Ground Truth](../../product/CAPABILITY_GROUND_TRUTH.md)); the registry
(`universe/registry.py::sync_registry`, background from
`data/symbols.py::build_instrument_background`) enforces that boundary.

#### CS-B1 Perpetuals only

`When the registry ingests venue market facts, it shall skip every instrument whose market_type is not perp, and every instrument when exchange.enabled_markets["usdt_futures"] is false.`

- Spot never becomes a universe asset, Candidate, Entry Plan or Proposal.

#### CS-B2 RWA background

`When a Bitget perpetual carries isRwa=YES, the registry shall set its instrument background asset_class=real_world_asset; isRwa=NO is crypto; an absent flag falls through to the other venue signals and otherwise stays instrument_background_unresolved.`

#### CS-B3 Visible blacklist

`If an instrument's background is not a supported crypto context, then the registry shall store it with blacklisted=true and the rejection reason as blacklist_reason (for example unsupported_analysis_context:tradifi_perpetual).`

- Ordinary crypto exclusions (leveraged tokens, test or dated contracts) are omitted altogether; a configured blacklist entry is stored as `config_blacklist`.

#### CS-B4 No partial registry

`If venue market facts are unavailable or identity-invalid, then the registry shall write nothing.`

### Selection chain

#### CS-C01 Hard Gate separates physical truth from actionability

`The Hard Gate (universe-hard-gate/v1) shall return ineligible when any physical check fails, unavailable when any physical input (registry status, identity, price, provenance, volume, spread, depth, freshness) is missing, eligible observe_only when only the action route is unavailable, and eligible actionable otherwise.`

- Volatility is never a Hard Gate input.
- WHEN depth is missing THEN the status is `unavailable`, not `ineligible`.

#### CS-C02 Operator instrument bounds

`Where universe.allowlist is non-empty, the Hard Gate shall admit only its members and answer it before universe.blacklist, and both lists shall be part of the policy fingerprint.`

- An empty allowlist is unrestricted. Neither list is ever widened automatically; narrowing the universe rotates the policy cohort.

#### CS-C03 Typed opportunity evidence

`The evidence builder (universe-opportunity-evidence/v2) shall project the six scanner families into Continuation, Reversal, Positioning/Forced Flow and Information/Catalyst mechanisms with volatility/regime/shock as context, and shall never sum raw evidence across mechanisms.`

- Continuation uses Momentum as its primary source; Short Bias is diagnostic, not a best-of authority.
- WHEN fewer than 20 Hard-Gate-passing rows share mechanism, direction and market_type in a run THEN no within-run percentile is invented and raw `pessimistic_strength` is kept.

#### CS-C04 Clustered, shrunk economic utility

`The estimator (universe-economic-utility/v2) shall compare mechanisms only within the same venue, market_type and direction on 4h cost-after net outcomes, deduplicated by outcome identity and reduced to non-overlapping 4h T0 clusters, and shall report utility = shrunk mean − one standard error with parent shrinkage K = 50.`

- Parent support requires 50 independent clusters, 10 dates, 5 symbols and 80% coverage; a child strength quintile requires 20 clusters. These thresholds are not lowered for Startup.
- WHEN the child tail shows a settled net below the tail-harm floor THEN the tail is `harm` and no utility is produced.

#### CS-C05 Incomplete history lowers coverage

`If a history row has any unknown cost or risk field (net return, MAE, fee, spread/slippage, funding, tradable-edge margin, cost assumptions or cost-model version), then the estimator shall exclude it from the sample but keep it in the coverage denominator.`

- A systematic gap therefore refuses the cohort through `parent_sample_insufficient`; `history_economics_incomplete` is reported only when complete rows disagree on the tradable-edge margin.
- A settled row below the tail-harm floor still flags the cohort even if another field is unknown.

#### CS-C06 Measured admission

`When at least one actionable mechanism has an available estimate with clear tail, the selector (candidate-selection-utility/v2) shall take the highest-utility mechanism and admit it as measured if its utility is at or above its min_tradable_edge_pct, otherwise below_floor.`

- Utilities are never summed; utility is already net of fee, spread/slippage and funding, so the floor (the estimation-error margin) never deducts cost again and is never negative.
- WHEN both directions clear their margins within `max(margins)` of each other THEN the result is `direction_conflict`.

#### CS-C07 Startup admission

`When no measured estimate is available and every actionable route's reason is one of no_history, parent_sample_insufficient, child_sample_insufficient, history_economics_incomplete or settlement_overdue, the selector shall admit as startup the actionable mechanism with the highest finite pessimistic_strength in (0, 100] on an eligible, actionable instrument.`

- Each listed reason means "not measured yet". `tail_harm`, `percentile_unavailable`, `history_time_invalid` and `history_identity_invalid` can never be bypassed through Startup.
- WHEN usable Startup routes point in more than one direction THEN the result is `direction_conflict`.

#### CS-C08 Capacity filling

`When admitted decisions exceed universe.max_candidates, the selector shall fill slots measured-first by utility, then Startup by pessimistic_strength, breaking ties by candidate_key, and shall keep one Candidate per symbol by the same order.`

- Below-floor rows never fill capacity; a legitimate empty set stays empty; an all-cold run may use all slots.
- A negative cap raises instead of widening admission.

#### CS-C09 Pre-capacity actionability

`The selector shall freeze supported mechanism capability, regime.short_enabled and the exact Fast Monitoring route before capacity selection, and shall not require slow market_snapshots freshness for Candidate admission.`

- Unsupported or capability-gap mechanisms cannot become measured or Startup primaries.
- The Planner still requires a fresh exact orderbook and execution still requires fresh Fast Market streams ([Entry Plan contract](entry-plan-contract.md)); neither is relaxed.
- Detected regime is frozen once into features, Candidate evidence and outcomes and adds no score.

#### CS-C10 Admission record

`The selector shall store admission_basis=measured with numeric utility, or admission_basis=startup with the exact startup_reason and economic_utility_pct=null, and rejected or historical records may carry no basis.`

- Signal strength is an ordinal heuristic, not a calibrated return or probability; unknown is neither zero nor economic qualification.

### Persistence and historical evidence

#### CS-P1 Same-transaction bundle

`When a Universe refresh publishes (or reuses) a Candidate snapshot, universe/service.py shall write the selection bundle through insert_universe_twin_shadow_bundle(..., min_interval_sec=0) inside the same _atomic_universe_publication() transaction as the snapshot.`

- There is no separate 55-minute cadence.

#### CS-P2 Invalid bundle rolls back publication

`If the bundle fails validation (count, scanner-run, capture-time, direction or outcome-identity mismatch → ValueError), then the publication transaction shall roll back and re-raise.`

- A Candidate snapshot never exists without its selection evidence.

#### CS-P3 Idempotent batch identity

`When the same scanner_run_id batch is already stored with identical identity, the write shall be a no-op (returns 0), and a conflicting identity shall raise universe_twin_shadow_batch_identity_conflict.`

- Each sampled run persists the complete same-T0 population plus one canonical 4h outcome per observed direction; all mechanism labels map to that shared outcome.

#### CS-C11 Frozen cost assumptions

`The selection record shall freeze the numeric cost assumptions (execution_fee_pct, slippage_bps, fallback_spread_bps) used at settlement, and the settler shall use those values or defer the row when they cannot be read.`

- A later Settings change cannot restate a pending outcome.

#### CS-C12 Cutoff-safe training view

`When a refresh trains the estimator, it shall freeze history at its actual capture cutoff with a training-source fingerprint, treating rows settled after the cutoff as pending and failing closed on malformed history or missing canonical outcomes.`

- A not-yet-due exposure is waiting evidence; the next refresh may absorb newly settled outcomes.

#### CS-C13 Policy epoch identity

`The runtime policy epoch shall be the canonical fingerprint of candidate_selection_semantic_manifest() plus the selection configuration (Hard Gate thresholds, allow/blacklists, selection limits, short_enabled, preferred market, action route and cost inputs).`

- A meaningful config or semantic change produces a new epoch; commit/process fingerprints are provenance only.

#### CS-C14 Atomic publication

`When a refresh publishes, the service shall first run recover_candidate_selection_authority_transition(), then recheck the configured identity and runtime scope inside the writer transaction, and commit Candidate rows, Planning Generation handoffs and scanner completion atomically.`

- WHEN the epoch or identity changed THEN publication raises and the previous snapshot stays; no half-published generation or Legacy fallback exists.
- TTL renewal on a change-suppressed refresh stays inside the same transaction.

#### CS-C15 Historical qualification grants nothing

`The runtime selection record shall carry promotion_eligible=false, and the read-only aletheia evidence candidate-selection status audit shall grant no runtime authority.`

- Legacy policies and prior decision JSON/hashes remain readable; no Demo Candidate is selected by Legacy.

### Candidate measurement acquisition identity

#### CS-C16 Hand-maintained acquisition identity

`The training loader shall include a history record only when its feature_acquisition_version equals the literal CANDIDATE_FEATURE_ACQUISITION_FINGERPRINT in universe/features.py.`

- Missing and legacy identities are excluded and counted; no record is rewritten, backfilled or relabelled.
- The constant changes by hand, in the same commit, when and only when a change alters a computed Candidate feature value wherever that code lives; comments, logging and refactors leave it unchanged. Every Outcome keeps `producer_commit` / `producer_runtime_session_id`, so a missed bump can be excluded afterwards.
- Matching identities pool across producer builds and runtime sessions.

### Perp funding evidence

#### CS-C17 T0 funding proof

`When a Candidate perpetual outcome is created, the bundle shall freeze candidate-perp-funding/v1 in funding_evidence_json with the exact instrument and venue, observed rate, positive proved settlement interval, source/acquisition times and interval provenance.`

- WHEN the interval or source proof is unknown THEN funding and net are unavailable, never an 8h assumption ([OO-C10](opportunity_outcome_contract.md)).
- Pre-contract perpetual outcomes are counted as `perp_funding_historical_incompatible` and excluded; current incomplete proofs are counted as `perp_funding_current_incomplete` and stay in the denominator.

### Typed Candidate Opportunity handoff

#### CS-C18 Typed context on the member row

`Every Candidate selected by Candidate Selection Utility shall carry candidate-opportunity-context/v1 on its universe_members row, with T0, selection cutoff, policy/epoch, target record hash, mechanism/direction, source_component_id, bounded mechanism summaries, capability gaps, context, admission_basis and startup_reason, hashed canonically.`

- For a `candidate_selection_utility` policy, only a valid Startup decision may have null utility. Historical JSON/hashes are not rewritten.
- No downstream owner may recover authority from `inclusion_reason`, reason codes or display prose.

#### CS-C19 Legacy mechanism projection

`The context builder shall project legacy families only as Momentum/Short Bias → Continuation, Mean Reversion → Reversal and Derivatives → Positioning proxy with liquidation, basis and cross-venue gaps, and shall leave Information/Catalyst unavailable without exact external claim provenance.`

- Volatility and Event receive no invented mechanism. A mechanism is a predictive hypothesis, not proved causal attribution.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| venue market facts unavailable | registry writes nothing | `market_instruments_unavailable` | no partial registry |
| Hard Gate input missing | `unavailable` | `HardGateCheck.reason_code` `*_unavailable` | never `ineligible` or `0` |
| estimator lacks support | `unavailable_reason` token | decision record | Startup only for evidence-absent reasons |
| bundle validation fails | publication rolls back, raises | refresh failure | previous snapshot kept |
| policy epoch changed before publish | raise `candidate_selection_authority_changed_before_publication` | refresh failure | previous snapshot kept |
| cost snapshot unreadable at settlement | row deferred | stays `pending` | never current-config substitution |
| funding proof missing | funding/net NULL | `perp_funding_current_incomplete` | unavailable, never 8h |

## Authority

May decide Candidate slot admission and write selection evidence; may never change Entry
geometry, Risk limits, Gateway behavior, credentials or Live unlock.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| CS-B1..B4 | `tests/test_registry_market_boundary.py::test_bitget_rwa_perpetual_is_not_crypto`, `::test_unavailable_market_facts_never_write_registry`, `::test_registry_retains_admission_blacklist_and_cleanup` |
| CS-C01 | `tests/specs/spec_candidate_selection_utility.py::CSU-S01`, `tests/test_candidate_selection_utility.py::test_hard_gate_is_fail_closed_and_action_route_is_observe_only` |
| CS-C02 | `tests/test_candidate_selection_utility.py::test_allowlist_bounds_admission_at_the_binding_gate` |
| CS-C03 | `tests/specs/spec_candidate_selection_utility.py::CSU-S02`, `tests/test_candidate_selection_utility.py::test_within_run_percentile_requires_twenty_and_average_ranks_ties` |
| CS-C04 | `tests/test_candidate_selection_utility.py::test_economic_utility_uses_exact_4h_clustered_shrunk_economics` |
| CS-C05 | `tests/test_candidate_selection_utility.py::test_one_incomplete_row_does_not_void_a_mature_cohort`, `::test_widespread_incomplete_rows_still_refuse_the_cohort`, `::test_an_incomplete_row_with_a_tail_loss_still_flags_the_cohort` |
| CS-C06 | `tests/test_candidate_selection_utility.py::test_candidate_selection_does_not_fill_and_direction_conflict_fails_closed`, `::test_admission_floor_is_the_estimation_margin_only` |
| CS-C07 | `tests/test_candidate_selection_utility.py::test_startup_requires_genuine_scarcity_and_cutoff_safe_settlement`, `::test_economic_utility_reason_classification_is_exhaustive`, `::test_overdue_settlement_cannot_hide_a_measured_tail_harm` |
| CS-C08 | `tests/test_candidate_selection_utility.py::test_target_selection_deduplicates_symbol_by_economic_rank`, `::test_startup_projection_measured_first_and_capacity` |
| CS-C09 | `tests/specs/spec_candidate_selection_utility.py::CSU-S05`, `tests/test_candidate_selection_utility.py::test_fast_route_actionability_does_not_require_slow_snapshots`, `::test_refresh_freezes_detected_regime_and_short_control_identity` |
| CS-C10 | `tests/test_candidate_opportunity_persistence.py::test_startup_persists_null_utility_and_can_own_planning_generation` |
| CS-P1..P3 | `tests/test_candidate_selection_utility.py::test_candidate_selection_shadow_persists_same_t0_full_denominator`, `tests/test_universe_twin_persistence.py::test_persistence_identity_conflict_fails_closed` |
| CS-C11 | no dedicated claim |
| CS-C12 | `tests/test_candidate_selection_utility.py::test_frozen_training_view_keeps_late_settlement_as_pending_denominator` |
| CS-C13 | `tests/specs/spec_candidate_selection_utility.py::CSU-S04`, `tests/test_candidate_selection_utility.py::test_candidate_selection_epoch_identity_includes_cost_and_route`, `::test_actionability_manifest_rotates_existing_policy_epoch` |
| CS-C14 | `tests/test_candidate_selection_utility.py::test_current_publication_survives_retired_scanner_failure` (publication path); no dedicated claim for the in-transaction recheck |
| CS-C15 | `tests/specs/spec_candidate_selection_utility.py::CSU-S04` |
| CS-C16 | `tests/specs/spec_candidate_selection_utility.py::CSU-S07`, `::CSU-S07b` |
| CS-C17 | `tests/specs/spec_candidate_selection_utility.py::CSU-S06`, `tests/test_candidate_funding.py::test_scoped_cutover_keeps_spot_and_current_incomplete_denominator` |
| CS-C18 | `tests/test_candidate_opportunity_persistence.py::test_startup_persists_null_utility_and_can_own_planning_generation` |
| CS-C19 | `tests/specs/spec_candidate_selection_utility.py::CSU-S02` |

### Known conformance gaps

- Residual spot code: `core/aletheia/config/schema.py` still defaults
  `exchange.enabled_markets` to `{"spot": True, "usdt_futures": True}`, that dict is part of
  the action-route policy identity, and `candidate_selection_shadow.py::build_candidate_selection_shadow_bundle`
  still ranks `spot`/`perp` by `preferred_market_type` when deduplicating a symbol. The
  registry admits only perpetuals (CS-B1), so these branches are unreachable for new
  Candidates; removal belongs to [spec 0003](../specs/0003-futures-only-convergence/spec.md).
- The pre-rewrite contract said both "`settlement_overdue` can support Startup" and "a due
  exposure lacking settlement blocks Startup". Code
  (`economic_utility.py::EVIDENCE_ABSENT_REASONS`) and its test treat `settlement_overdue` as
  evidence-absent, so CS-C07 keeps the first statement.

## Decision references

- #2729 and `docs/design/proposals/universe_continuous_scoring_safety.md`: introduced CSU.
- #2556: separate frozen Observation Lab benchmark; CSU never reads, relabels or mutates it.
- #3056 (A-batch provenance), #3492, #3786 (2026-10-08, hand-maintained acquisition identity
  frozen at the 2026-09-27 `features.py` digest), #2917 (owner-approved 2026-09-11,
  pre-capacity actionability; manifest now `supported-mechanism-and-fast-route-before-capacity/v3`),
  #3086 (Perp T0 funding), #3073 (near-due path and MAE), #3483 / #3486 with owner decision
  2026-10-09 (single "cleared its edge" comparison against `min_tradable_edge_pct`).
- Dated amendments (2026-09-05 acquisition and publication, 2026-09-10 pre-capacity
  actionability, 2026-10-08 acquisition identity, #3086 funding), historical fixed-look
  qualification, Twin/Relative-Rank replay and Observation isolation text are preserved
  verbatim in [history](../history/candidate_selection_utility_superseded_passages.md).
