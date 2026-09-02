# Candidate Selection Utility Contract

Authority: GitHub Issue #2729 and the accepted
`docs/design/proposals/universe_continuous_scoring_safety.md` proposal.

Status: implemented as non-authoritative Shadow. The current
`universe-step-v3` / `total_score` selector remains production Candidate
authority until this contract's prospective qualification is positive and an
authenticated owner explicitly admits the exact frozen policy epoch.

## Boundary

Candidate Selection Utility answers only which exact instruments deserve a
scarce Candidate slot. It does not choose Entry geometry, size, leverage, order
type, Risk exceptions, credentials, Gateway behavior, or exchange actions.
Historical six-family and `total_score` records remain immutable compatibility
evidence and the deterministic rollback control.

The V1 chain is:

1. `universe-hard-gate/v1` records exact physical/data eligibility as
   `eligible`, `ineligible`, or `unavailable`, separately from actionability.
   Missing volume, spread, depth, freshness, price, provenance, or identity is
   unavailable. Volatility is never a Hard Gate.
2. `universe-opportunity-evidence/v1` projects the existing six families into
   Continuation, Reversal, Positioning/Forced Flow, and Information/Catalyst,
   with volatility/regime/shock as context. Mechanism strengths are ordinal,
   same-run percentiles require 20 comparable rows, and raw evidence is never
   duplicated or summed across mechanisms.
3. `universe-economic-utility/v1` compares mechanisms only in 4h cost-after
   percentage points. It uses selected and dropped canonical outcomes, one
   economic observation per instrument/direction occurrence, scanner-run
   cluster inference, `K=50` parent shrinkage, one cluster-SE uncertainty
   penalty, explicit minimum support, and fail-closed tail status.
4. `candidate-selection-utility/v1` chooses the highest available mechanism,
   never sums utilities, rejects unresolved Long/Short conflicts, applies
   `max(0.05, min_tradable_edge_pct)`, sorts admitted rows by Utility, and uses
   existing `max_candidates` only as a ceiling. It never fills with below-floor
   rows.

## Persistence and prospective evidence

The target path reuses `universe_twin_shadow_batches`,
`universe_twin_shadow_records`, and `filter_candidate_outcomes`; it creates no
second Candidate, Outcome, experiment, scheduler, or database platform. Each
successful sampled scanner run persists the complete same-T0 population plus
one canonical 4h outcome per observed direction. The compact record maps all
mechanism cohort labels to that shared outcome, so several labels cannot become
duplicated economics.

When estimator support first becomes available, the path freezes its training
cutoff and source fingerprints into a new behavior/policy identity. Later
outcomes cannot change that estimator. Qualification consumes only post-cutoff
clusters and uses the first outcome-independent fixed look with at least 50
complete clusters, seven consecutive UTC dates, 80% full-arm coverage, positive
paired capacity-normalized net Utility, a positive one-sided 95% lower bound,
and preregistered loss-rate, MAE p95, CVaR5, cost, concentration, and catastrophic
tail gates. Negative, neutral, insufficient, unavailable, and invalid are valid
terminal/reporting states and grant no authority.

`uv run aletheia evidence candidate-selection status --json` is read-only. A
positive report sets only `qualification_eligible=true`; it deliberately keeps
`promotion_eligible=false` and `owner_admission_required=true`. Until the owner
admission boundary is implemented and exercised for the exact policy epoch,
the legacy selector remains the only authority and rollback requires no history
rewrite.

## Isolation

Issue #2556 owns a separate frozen Observation Lab generation. This path never
reads, relabels, backfills, or mutates its benchmark records, identities, cost
model, stopping rule, or result package. Code/tests prove contract behavior and
replayability, not profitability, deployment, qualification, or production
authority.
