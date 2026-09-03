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
2. `universe-opportunity-evidence/v2` projects the existing six families into
   Continuation, Reversal, Positioning/Forced Flow, and Information/Catalyst,
   with volatility/regime/shock as context. Mechanism strengths are ordinal,
   same-run percentiles require 20 comparable rows, and raw evidence is never
   duplicated or summed across mechanisms. Continuation uses Momentum as its
   typed primary source; Short Bias is diagnostic evidence rather than a
   correlated best-of authority.
3. `universe-economic-utility/v1` compares mechanisms only within the same
   exact venue, market type, and direction in 4h cost-after percentage points.
   It uses selected and dropped canonical outcomes, one
   economic observation per instrument/direction/non-overlapping 4h UTC block,
   `K=50` parent shrinkage, one independent-unit SE uncertainty
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
duplicated economics. The same record freezes the numeric cost assumptions used
at settlement, so a later Settings change cannot restate a pending outcome.

When estimator support first becomes available, the path freezes its training
cutoff, complete eligible training-dataset fingerprint, and selection settings
including the exact Fast Monitoring mode, venue, and paper execution route
plus the configured execution fee and slippage assumptions and the legacy
control's explore ratio, family quotas, regime quotas, scanner weights, and
complete Regime detector configuration/behavior version into a new
behavior/policy identity. The frozen training cutoff is looked up only
within that exact identity; changing those inputs independently earns support
and freezes a new cutoff instead of reusing incompatible training rows.
Per-cell source fingerprints remain in
each estimate for audit but do not make the epoch depend on the current
Candidate composition. Later outcomes cannot change that estimator.
Qualification consumes only post-cutoff clusters from the latest frozen policy
epoch that existed at the requested as-of instant and uses the first
outcome-independent fixed look whose earliest T0-ordered exposure prefix contains
at least 50 clusters and seven consecutive UTC dates. That prefix must then have
at least 50 complete clusters, 80% full-arm coverage, positive
paired capacity-normalized net Utility, a positive one-sided 95% lower bound,
and preregistered loss-rate, MAE p95, CVaR5, cost, concentration, and catastrophic
tail gates. A later cluster cannot replace an incomplete cluster in that frozen
prefix. The look's coverage and exclusions freeze at that same stopping
cluster, so later batches are not loaded or allowed to rewrite its verdict. A selected-arm
outcome without finite MAE or fee, spread/slippage, and funding cost evidence
leaves its cluster incomplete. Negative,
neutral, insufficient, unavailable, and invalid are valid
terminal/reporting states and grant no authority.

Historical outcomes are indexed once per refresh and then looked up per
Candidate/mechanism, making computation `O(history + candidates)` rather than
re-scanning history per mechanism. Non-authoritative Shadow first checks the
existing 55-minute persistence cadence, so a skipped capture does not load or
aggregate historical outcomes. Record v2 stores compact row-local evidence and
keeps shared policy/training fingerprints in the batch. For the 20-Candidate
regression fixture this is 77,016 payload bytes/run, 27 runs/day, and 1.983 MiB/day;
the rolling 30-day Twin window projects to 59.493 MiB. For an admitted epoch,
the immutable evidence prefix through its first admission audit and the global
frozen training denominator remain protected. Later operational telemetry uses
the ordinary 30-day window, so an active authority does not grow storage without
bound. A protected epoch with no admission audit fails closed and retains all
evidence.

The admitted epoch's evidence prefix, frozen training denominator, and linked
canonical outcomes are retained for fixed-look replay rather than entering the
generic 30-day Twin/outcome prune. Post-admission rows and their outcomes enter
that prune after 30 days. Runtime Session attestations referenced by retained
outcomes are retained with them. If configured policy state is unreadable,
Candidate evidence pruning fails closed.
At a frozen cutoff, a row settled later remains a pending denominator row with
no post-cutoff settlement fields, while a post-cutoff exposure is excluded. The
training cohort cannot disappear, expand, or borrow future facts.

`uv run aletheia evidence candidate-selection status --json` is read-only. A
positive report sets `qualification_eligible=true` and
`promotion_eligible=true`, but `owner_admission_required` remains true: evidence
does not mutate authority. The generic Universe configuration endpoint cannot
change the protected policy fields. The owner admission endpoint accepts only
the exact positive epoch, refuses an overlapping Paper Universe-calibration
authority, and persists an audit event under the same writer permit as the
configuration transition. Because SQLite and the atomically replaced config
file cannot share one physical transaction, a durable transition journal is
written before the config replacement. Recovery holds the SQLite writer permit
and restores the before-state when the audit did not commit, or confirms the
after-state when it did, before deleting the journal. Target refresh publication revalidates that exact
configured epoch under the shared permit. Every Settings writer for a frozen
selection input uses that same permit, so no identity mutation can commit
between revalidation and snapshot publication. A failed/compensated admission
cannot publish a stale target snapshot. The same exclusion applies in reverse:
Universe-family Learning admission and later Paper progression refuse to run
while target Candidate authority is configured. Admission and rollback
configure the policy for the next successful Universe refresh; they do not
relabel the active snapshot or its Planning Generations. The runtime re-evaluates
that same qualification before every target publication. Missing, stale,
negative, neutral, insufficient, or invalid evidence fails closed and preserves
the previous active snapshot.

Qualification requires independently attested capture and settlement Runtime
Sessions. Commit SHA and process-wide behavior fingerprints remain provenance
but are not Candidate economic identity: unrelated deployments may coexist in
one fixed look. Economic comparability is frozen by the explicit semantic
manifest (Hard Gate, Opportunity Evidence, scorer, estimator, outcome, cost,
and Candidate-selection contract versions) plus policy/cost inputs. A semantic
contract change rotates the epoch even if the Git commit string does not.
Missing, contradictory, or unattested provenance is invalid evidence, never a
local-checkout inference.

An active target snapshot may intentionally contain no members when every row
is below-floor or unavailable. An admitted scan with no currently available
estimate retains the admitted epoch and frozen training cutoff, so it can renew
that intentionally empty snapshot. Reads preserve the empty Candidate set and
do not replace it with legacy static fallback rows.

Within a mechanism, usable Long and Short witnesses are an unresolved conflict
and make that mechanism unavailable. Target allocation keeps the full
instrument denominator but admits at most one preferred-market instrument per
symbol thesis. On a change-suppressed target refresh, TTL renewal commits in the
same publication transaction as required target evidence.

An authoritative refresh writes the target selection evidence, canonical 4h
outcomes, Candidate rows, Planning Generation handoffs, and scanner completion
in one SQLite publication transaction. Candidate rows expose the target Utility
separately and retain `total_score` only as named legacy compatibility evidence.
They also persist typed `opportunity_mechanism`, mechanism version, source
component, source cutoff, and evidence hash fields; downstream code does not
recover authority from reason strings. This is the minimum carrier repair only;
mechanism-aware Planning identity/context/compiler work remains owned by #2791.
An explicit audited rollback restores `legacy_total_score_v1` without deleting
or relabeling historical evidence.

## Typed Candidate Opportunity handoff

Every target-authority Candidate carries `candidate-opportunity-context/v1`
on the existing `universe_members` row, with a queryable primary mechanism and
canonical JSON/hash. The payload freezes Candidate T0, the selection source
cutoff, policy/epoch, exact target record hash, selected mechanism/direction,
typed `source_component_id`, all bounded mechanism summaries, capability gaps,
and volatility/regime/shock context. No downstream owner may recover authority
from `inclusion_reason`, reason codes, or display prose.

Legacy projection is limited to Momentum/Short Bias → Continuation, Mean
Reversion → Reversal, and Derivatives → Positioning proxy with liquidation,
Basis, and cross-venue gaps. Volatility/Event do not receive an invented
mechanism. Information/Catalyst remains unavailable without exact external
claim identity and provenance. A mechanism is a predictive hypothesis, not
proved causal attribution.

## Isolation

Issue #2556 owns a separate frozen Observation Lab generation. This path never
reads, relabels, backfills, or mutates its benchmark records, identities, cost
model, stopping rule, or result package. Code/tests prove contract behavior and
replayability. This implementation does not itself prove a positive prospective
qualification, enable production authority, demonstrate profitability, or prove
the exact code is deployed on Fedora.
