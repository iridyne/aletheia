# Candidate Selection Utility Contract

Authority: GitHub Issue #2729 and the accepted
`docs/design/proposals/universe_continuous_scoring_safety.md` proposal.

Status: implemented with legacy authority by default. Candidate Selection
Utility records non-authoritative Shadow evidence until its prospective fixed
look is positive and an owner explicitly admits that exact frozen policy epoch
through the protected Settings boundary.

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
3. `universe-economic-utility/v1` compares mechanisms only within the same
   exact venue, market type, and direction in 4h cost-after percentage points.
   It uses selected and dropped canonical outcomes, one
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
cutoff, complete eligible training-dataset fingerprint, and selection settings
including the exact Fast Monitoring mode, venue, and paper execution route
into a new behavior/policy identity. Per-cell source fingerprints remain in
each estimate for audit but do not make the epoch depend on the current
Candidate composition. Later outcomes cannot change that estimator.
Qualification consumes only post-cutoff
clusters from the latest frozen policy epoch and uses the first
outcome-independent fixed look whose earliest T0-ordered exposure prefix contains
at least 50 clusters and seven consecutive UTC dates. That prefix must then have
at least 50 complete clusters, 80% full-arm coverage, positive
paired capacity-normalized net Utility, a positive one-sided 95% lower bound,
and preregistered loss-rate, MAE p95, CVaR5, cost, concentration, and catastrophic
tail gates. A later cluster cannot replace an incomplete cluster in that frozen
prefix. The look's coverage and exclusions freeze at that same stopping
cluster, so later pending outcomes cannot rewrite its verdict. A selected-arm
outcome without finite MAE or fee, spread/slippage, and funding cost evidence
leaves its cluster incomplete. Negative,
neutral, insufficient, unavailable, and invalid are valid
terminal/reporting states and grant no authority.

`uv run aletheia evidence candidate-selection status --json` is read-only. A
positive report sets `qualification_eligible=true` and
`promotion_eligible=true`, but `owner_admission_required` remains true: evidence
does not mutate authority. The generic Universe configuration endpoint cannot
change the protected policy fields. The owner admission endpoint accepts only
the exact positive epoch, refuses an overlapping Paper Universe-calibration
authority, and persists an audit event. Admission and rollback configure the
policy for the next successful Universe refresh; they do not relabel the active
snapshot or its Planning Generations. The runtime re-evaluates
that same qualification before every target publication. Missing, stale,
negative, neutral, insufficient, or invalid evidence fails closed and preserves
the previous active snapshot.

An authoritative refresh writes the target selection evidence, canonical 4h
outcomes, Candidate rows, Planning Generation handoffs, and scanner completion
in one SQLite publication transaction. Candidate rows expose the target Utility
separately and retain `total_score` only as named legacy compatibility evidence.
An explicit audited rollback restores `legacy_total_score_v1` without deleting
or relabeling historical evidence.

## Isolation

Issue #2556 owns a separate frozen Observation Lab generation. This path never
reads, relabels, backfills, or mutates its benchmark records, identities, cost
model, stopping rule, or result package. Code/tests prove contract behavior and
replayability, not profitability, deployment, qualification, or production
authority.
