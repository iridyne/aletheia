# Candidate Selection Utility Contract

Authority: GitHub Issue #2729 and the accepted
`docs/design/proposals/universe_continuous_scoring_safety.md` proposal.

Status: Demo runtime uses the existing Utility policy family
`candidate_selection_utility_v1` with decision v2 and record v4. Measured
economic admission has priority; genuine sample scarcity may fill remaining
capacity through explicit Startup signal admission. Positive fixed-look
qualification and an owner admission record are not runtime prerequisites.

## Boundary

Candidate Selection Utility answers only which exact instruments deserve a
scarce Candidate slot. It does not choose Entry geometry, size, leverage, order
type, Risk exceptions, credentials, Gateway behavior, or exchange actions.
Historical six-family and `total_score` records remain immutable audit evidence,
not a runtime fallback or rollback allocator. Risk, Gateway, credentials and
Live-unlock authority are unchanged.

Historical Twin evaluation still verifies persisted records by replaying the
old allocation and whole-candidate continuous-scoring rules. Those replay
functions remain alongside the shared current execution decoration and
mechanism components; they do not select current Candidates. Relative-Rank
builders remain for historical fixture reconstruction and reader verification,
with no refresh/scheduler caller. Their table readers and immutable fixtures
retain the original meaning. The retired total-score/calibration computation
and its exposure writer have no such reader dependency and are removed.

Current Candidates are projected from exact raw/typed feature identity and the
current selection decision. Legacy Scanner/Filter output is not a required
carrier or fallback. The old two-pass filters and six-family scanner remain
only at the separate #2556 research capture seam after successful publication.
Their failure cannot revoke or prevent current Candidate publication.

The current chain is:

1. `universe-hard-gate/v1` records exact physical/data eligibility as
   `eligible`, `ineligible`, or `unavailable`, separately from actionability.
   Missing volume, spread, depth, freshness, price, provenance, or identity is
   unavailable. Volatility is never a Hard Gate. An operator instrument bound is
   part of this gate: a configured `universe.allowlist` admits only its members
   and is answered before `universe.blacklist`, so a symbol absent from the
   allowlist never needs naming in the blacklist. An empty allowlist is
   unrestricted. Both lists are hashed into the policy fingerprint, so narrowing
   the universe rotates the cohort instead of mixing pre- and post-narrowing
   evidence. Neither list is ever widened automatically: a venue that keeps
   listing new instrument families would reopen the universe faster than evidence
   can close it.
2. `universe-opportunity-evidence/v2` projects the existing six families into
   Continuation, Reversal, Positioning/Forced Flow, and Information/Catalyst,
   with volatility/regime/shock as context. Mechanism strengths are ordinal,
   same-run percentiles require 20 comparable rows, and raw evidence is never
   duplicated or summed across mechanisms. Continuation uses Momentum as its
   typed primary source; Short Bias is diagnostic evidence rather than a
   correlated best-of authority.
3. `universe-economic-utility/v2` compares mechanisms only within the same
   exact venue, market type, and direction in 4h cost-after percentage points.
   It uses selected and dropped canonical outcomes, deduplicates exact outcome
   identity, averages same-T0 symbols inside each mechanism/direction/venue/
   market cohort, then greedily retains the earliest T0 clusters separated by
   at least four hours. Those cross-sectional non-overlapping clusters are the
   statistical units used by `K=50` parent shrinkage, one independent-unit SE
   uncertainty penalty, explicit minimum support, and fail-closed tail status.
4. `candidate-selection-utility/v2` chooses the highest available mechanism,
   never sums utilities, rejects unresolved Long/Short conflicts and applies
   `min_cost_covering_edge_pct` (the cost-covering floor carried on
   `EconomicUtilityEstimateV2`, equal to full measured fee + spread/slippage +
   funding plus the estimation-error margin) to measured estimates. When
   `min_cost_covering_edge_pct` is unavailable, the estimate falls back to a
   `0.05` floor rather than admitting unconditionally. The legacy
   `max(0.05, min_tradable_edge_pct)` formula is retired for this consumer;
   `min_tradable_edge_pct` itself is unchanged and keeps only its separate
   counterfactual-classification consumer per `opportunity_outcome_contract.md`;
   "cleared its edge" statistics now read `min_cost_covering_edge_pct` there too.
   Parent support remains 50 independent clusters, 10 dates, 5 symbols and
   80% coverage; the child
   requires 20 clusters. These thresholds are not lowered for Startup.
5. Only `no_history`, `parent_sample_insufficient` or
   `child_sample_insufficient`, `history_economics_incomplete` or
   `settlement_overdue` can support Startup: each means "not measured yet", not
   "measured and refused". It requires an eligible, actionable instrument,
   explicit direction, available capabilities and finite positive
   `pessimistic_strength`. Invalid-only history, invalid identities/times, tail
   harm and known economic rejection cannot be bypassed through another sparse
   mechanism. A mature parent without a current percentile is unavailable, not
   Startup.

   A history row with any unknown cost or risk field (net return, MAE, fee,
   spread/slippage, funding, cost-covering floor, cost assumptions or cost-model
   version) is excluded from the sample and stays in the coverage denominator.
   It no longer voids its cohort; a systematic gap lowers coverage below the 80%
   floor and refuses the cohort through `parent_sample_insufficient`. A cohort
   reports `history_economics_incomplete` only when complete rows disagree on the
   cost floor. A settled row below the tail-harm floor still flags the cohort
   even if another field of it is unknown. (#3786, 2026-10-08)
6. Measured candidates sort first by Utility; Startup fills remaining existing
   `max_candidates` slots by conservative signal strength. Stable identity breaks
   ties. Cross-market deduplication uses the same order, with preferred market
   only resolving equal economic/signal rank. All-cold runs may use all slots;
   mature candidates filling capacity leave no reserved Startup allocation.
   Below-floor rows never fill capacity. Legitimate empty sets remain empty.

Startup stores `admission_basis=startup`, the exact `startup_reason` and
`economic_utility_pct=null`. Measured admission stores `admission_basis=measured`.
Rejected/historical records may have no basis. Signal strength is an ordinal
heuristic, not calibrated expected return or success probability; unknown is
neither zero nor economic qualification. Groups below 20 retain raw signal
strength without an invented percentile.

## Persistence and historical evidence

The target path reuses `universe_twin_shadow_batches`,
`universe_twin_shadow_records`, and `filter_candidate_outcomes`; it creates no
second Candidate, Outcome, experiment, scheduler, or database platform. Each
successful sampled scanner run persists the complete same-T0 population plus
one canonical 4h outcome per observed direction. The compact record maps all
mechanism cohort labels to that shared outcome, so several labels cannot become
duplicated economics. The same record freezes the numeric cost assumptions used
at settlement, so a later Settings change cannot restate a pending outcome.

Each runtime refresh freezes the historical view at its actual capture cutoff
and records the training-source fingerprint. A later-settled row remains pending
at that cutoff, without post-cutoff settlement fields; a not-yet-due exposure is
waiting evidence, while a due exposure lacking settlement blocks Startup.
Malformed persisted history or missing canonical outcomes fails closed instead
of disappearing into clean zero history. The next refresh may absorb newly
settled outcomes: the first empty training view is never frozen permanently.

The runtime policy epoch is the canonical fingerprint of the semantic/config
identity, independent of whether economic support exists. It includes Hard Gate,
Opportunity, estimator, outcome, cost and acquisition semantics plus current
selection limits, physical-data thresholds and execution-route/cost inputs.
Legacy ranking, allocation and calibration inputs are not Demo runtime
dependencies. A meaningful config/semantic change produces a new identity.
Commit/process fingerprints are provenance, not a replacement for this identity.

History is indexed once per refresh and reused across Candidate lookups.
Production selection computes on every refresh; sampled full-denominator
outcomes retain the existing four-hour evidence cadence. Unselected opportunities
also accumulate market-and-cost-model outcomes. These are not actual Fill PnL
and do not require first placing an order. No exploration allocator is added.

Snapshot publication rechecks the configured identity and runtime scope under
the shared writer permit, then commits Candidate rows, Planning Generation
handoffs and scanner completion atomically. Failure preserves the previous
snapshot; no half-published generation or Legacy fallback is allowed. Existing
transition-journal recovery precedes config migration/publication. TTL renewal
on a change-suppressed refresh remains in the publication transaction.

Legacy policies and prior decision JSON/hashes remain readable but no new Demo
Candidate is selected by Legacy. Candidate rows expose Utility separately from
named historical `total_score` evidence and preserve typed mechanism, source
component, direction, cutoff and source-record hash. Downstream authority never
comes from reason strings. A supported Startup Candidate uses the same Context,
generation, Planner and Compiler path; Startup guarantees no Entry Plan or Fill.

### Historical fixed-look qualification

Read-only qualification v2 evaluates immutable record-v3 evidence only. Its
frozen training cutoff, first outcome-independent non-overlapping 50-unit/seven-
date prefix, complete-cluster/coverage requirements, paired Utility confidence
bound, tail gates and provenance checks retain their historical meaning.
Later batches cannot replace an incomplete prefix or rewrite its terminal result.
Historical admission evidence, protected prefixes, linked outcomes and Runtime
Session attestations retain their existing retention/audit protections.

The A-batch provenance boundary accepted in #3056 permits new records to omit
retired `legacy_control` diagnostics and consequently produce a new
`source_record_hash` and `CandidateOpportunityContext.context_hash`. Historical
JSON and hashes are never rewritten. For the same frozen inputs, selected
identity/order, Utility, mechanism/direction, all other Context semantics, and
Planning ownership/material identity must remain equal. Provenance hash equality
is not substituted for that semantic and Planning equivalence check.

`uv run aletheia evidence candidate-selection status --json` remains a historical
read-only audit. Its positive qualification flags do not grant current runtime
authority. Runtime record-v4 dynamic cutoffs are not fixed-window confirmatory
evidence and set `promotion_eligible=false`. Demo exposes no positive-admission
or Legacy-rollback runtime control. This does not relax separate Learning,
Risk, Gateway, Paper/Live scope or Live-unlock permissions.

## Typed Candidate Opportunity handoff

Every target-authority Candidate carries `candidate-opportunity-context/v1`
on the existing `universe_members` row, with a queryable primary mechanism and
canonical JSON/hash. The payload freezes Candidate T0, the selection source
cutoff, policy/epoch, exact target record hash, selected mechanism/direction,
typed `source_component_id`, all bounded mechanism summaries, capability gaps,
and volatility/regime/shock context. It also carries explicit admission basis
and Startup reason. Only a valid, supported Startup decision may have null
Utility; numeric Utility remains required for measured admission. Historical
JSON/hashes are not rewritten to add the new fields. No downstream owner may
recover authority from `inclusion_reason`, reason codes or display prose.

Legacy projection is limited to Momentum/Short Bias → Continuation, Mean
Reversion → Reversal, and Derivatives → Positioning proxy with liquidation,
Basis, and cross-venue gaps. Volatility/Event do not receive an invented
mechanism. Information/Catalyst remains unavailable without exact external
claim identity and provenance. A mechanism is a predictive hypothesis, not
proved causal attribution.

## Acquisition and publication amendment — 2026-09-05

Discovery acquires exact venue/symbol/physical-market books independently of
Candidate existence. Only durable observations enter evidence. Suppressed
calculations becoming evidence persist current payload and timestamp. The
manifest includes `feature_acquisition`, which replaced the manually bumped
`candidate-feature-acquisition/v2` identity (see the 2026-10-08 amendment below
for how it is maintained now). The existing serialized `feature_acquisition_version` field stores
the digest without changing the record decoding/schema version. Training requires
equality to the explicitly supplied frozen policy identity before labels are built.
Missing identities match only missing historical identities; current policies
exclude both missing and legacy manual identities. Historical readers can still
request their original frozen policy; this does not authorize current training.

This measurement cutover excludes every pre-digest record, including records
captured after #3492 but before this identity was deployed. No historical record,
price, hash or Outcome is deleted, backfilled, relabeled or retrospectively certified.
New natural captures open the clean acquisition window. Existing Outcome
`producer_commit` / `producer_runtime_session_id` attribute each exposure to its
exact build and process. The runtime `behavior_fingerprint` hashes configuration
only and cannot distinguish the #3492 source change; it is not this boundary.

Matching acquisition identities may pool across producer commits/sessions subject
to the existing outcome, funding, mechanism, cost and cutoff checks. No new experiment
framework, database migration, admission threshold or execution authority is added.

### Amendment — 2026-10-08: frozen, hand-maintained acquisition identity

The import-time digest of `features.py` bytes is retired. Comment-only and
logging-only edits each rotated it and discarded all accumulated training
history, while the code that computes feature values outside that file was never
covered (#3786). `feature_acquisition` is now the literal constant
`CANDIDATE_FEATURE_ACQUISITION_FINGERPRINT` in `features.py`, frozen at that
file's digest as deployed on 2026-09-27
(`5c0692756228107b3d549e4c07db170d0397bcc1c8f90c69fe89f47eca928774`), so evidence
captured since then keeps matching. Training still requires equality, and legacy
or missing identities stay excluded exactly as above.

The constant changes by hand, in the same commit, when and only when a change
alters a computed Candidate feature value, wherever that code lives. Comments,
logging and refactors leave it unchanged. Every Outcome keeps `producer_commit`,
so evidence from a build whose bump was missed can still be identified and
excluded afterwards. No record is rewritten.

Canonical planning generations own refresh priority without legacy wakes.
Scanner `candidate_path` metrics distinguish unavailable data, legitimate empty
selection, supported planning and observe-only selection. Total acquisition or
build failure preserves the prior snapshot. Metrics and completion commit with
publication. Sampled evidence retains its existing cadence and operational
pending/unavailable reporting. Historical terminal qualification is not a
runtime selection gate. Candidate reads contain durable members only; static
instruments remain explicit observation fallbacks. Read errors are unavailable,
not empty success. Hard gates and execution permissions remain unchanged.

Current funnel counts describe Hard Gate/actionability, admitted, selected and
Planning results. Historical `filtered`/`assets_passed` retains its original
legacy-filter meaning; it is not a current admission count. Current Candidate
API/UI/CLI projections expose Utility and typed admission evidence, not synthetic
legacy scores. Historical score values remain readable under their old policy;
the required physical `total_score` column is not an economic authority.

## Pre-capacity actionability amendment — 2026-09-10

As corrected by owner-approved #2917 (2026-09-11), current actionability freezes
supported mechanism capability and the exact Fast Monitoring route before capacity
selection. Exact T0 feature facts remain Hard Gate inputs. Slow `market_snapshots`
freshness is not Candidate admission authority: dynamic snapshot targets depend on
already-active Candidate/Planning work, so requiring that cache would prevent
empty-Universe discovery from recovering. Planner still requires a fresh exact
orderbook before model dispatch, and execution still requires fresh FastMarketStream
channels and source/receipt evidence before Order Intent. Neither gate is relaxed.
Post-selection decoration projects TTL/risk flags without rechecking eligibility.
Unsupported or capability-gap mechanisms cannot become measured or Startup
primaries. The explicit `regime.short_enabled` control applies before direction
comparison and is part of the policy identity. Detected regime is frozen once
into current feature context, Candidate evidence and outcomes; it does not add
an economic score or automatic directional bias.

The #2917 actionability manifest `supported-mechanism-and-fast-route-before-capacity/v2`
rotates the existing current policy epoch. The original B amendment rotated identity
through its actionability
contract material and short-control configuration. It does not rewrite A's
historical golden policy, Context/Planning identity or any persisted evidence.
Historical allocator replay retains its original late-decoration behavior.

## Observation isolation

Issue #2556 owns a separate frozen Observation Lab generation. This path never
reads, relabels, backfills, or mutates its benchmark records, identities, cost
model, stopping rule, or result package. Code/tests prove contract behavior and
replayability. This implementation does not itself prove profitability, a
positive prospective qualification, live-trading authority, or deployment of
the exact code on Fedora.

## Perp T0 Funding evidence — #3086

Canonical Candidate Perp outcomes freeze `candidate-perp-funding/v1` in
`funding_evidence_json`: exact physical instrument/venue, observed rate, positive
proved settlement interval, source/acquisition times and interval provenance.
Bitget reuses the same ticker observation and the already-loaded contracts
`fundInterval`; the exact primary stored fact is only a bounded fallback.
Unknown interval/source proof produces unavailable funding/net, never an 8h
assumption. The shared non-Candidate outcome paths keep their existing contract;
this amendment changes only canonical Candidate settlement.

The Candidate semantic manifest adds `perp_funding`, rotating the existing policy
epoch. Training explicitly counts pre-contract Perp outcomes as
`perp_funding_historical_incompatible`; they remain immutable/queryable and do
not enter current compatible cohorts. Feature acquisition identity and Spot
history are unchanged. New incomplete proofs remain counted as
`perp_funding_current_incomplete` and stay in the current outcome denominator,
where overdue/incomplete economics fail closed. `perp_funding_current_complete`
counts T0 proof completeness, not settlement, MAE completeness or profitability.
Counts use unique outcome rows before mechanism label expansion. Near-due path
and MAE semantics remain owned by #3073 and unchanged.
