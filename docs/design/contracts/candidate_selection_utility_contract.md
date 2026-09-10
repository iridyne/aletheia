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

Current Candidates are projected from exact raw/typed feature identity and the
current selection decision. Legacy Scanner/Filter output is not a required
carrier or fallback. The old two-pass filters and six-family scanner remain
only at the separate #2556 research capture seam after successful publication.
Their failure cannot revoke or prevent current Candidate publication.

The current chain is:

1. `universe-hard-gate/v1` records exact physical/data eligibility as
   `eligible`, `ineligible`, or `unavailable`, separately from actionability.
   Missing volume, spread, depth, freshness, price, provenance, or identity is
   unavailable. Volatility is never a Hard Gate.
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
   `max(0.05, min_tradable_edge_pct)` to measured estimates. Parent support remains
   50 independent clusters, 10 dates, 5 symbols and 80% coverage; the child
   requires 20 clusters. These thresholds are not lowered for Startup.
5. Only `no_history`, `parent_sample_insufficient` or
   `child_sample_insufficient` can support Startup. It requires an eligible,
   actionable instrument, explicit direction, available capabilities and finite
   positive `pessimistic_strength`. Invalid-only history, missing costs/MAE,
   invalid identities/times, overdue settlement, tail harm and known economic
   rejection cannot be bypassed through another sparse mechanism. A mature
   parent without a current percentile is unavailable, not Startup.
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
manifest includes `feature_acquisition` from the features-owner constant
`candidate-feature-acquisition/v2`; records store `feature_acquisition_version`.
Training requires equality to the explicitly supplied frozen policy version
before labels are built. Missing versions match only missing historical versions;
training does not require epoch equality or mutate frozen historical rows.

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

## Isolation

Issue #2556 owns a separate frozen Observation Lab generation. This path never
reads, relabels, backfills, or mutates its benchmark records, identities, cost
model, stopping rule, or result package. Code/tests prove contract behavior and
replayability. This implementation does not itself prove profitability, a
positive prospective qualification, live-trading authority, or deployment of
the exact code on Fedora.
