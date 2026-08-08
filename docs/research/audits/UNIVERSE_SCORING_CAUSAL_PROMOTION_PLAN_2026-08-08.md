# Universe Scoring Causal Promotion Plan

**Status:** review required; no trading authority is granted by this document
**Date:** 2026-08-08
**Scope:** Local Paper Universe scoring and ranking only

## Decision

Do not replace the authoritative Universe score, change a rank cutoff, or alter
Fedora configuration from the current evidence. The next permitted step is a
read-only, prospective comparison of the existing authority against one frozen
continuous-scoring treatment. Any behavioral rollout requires a separate owner
approval after the evidence contract below is implemented and satisfied.

Risk, Execution, position sizing, Live, exchange routing, credentials, and hard
portfolio limits remain unchanged.

## Why this is the current bottleneck

The Fedora Paper instance at commit `6b62441e` supplied the following checkpoint
on 2026-08-08:

- 51 verified strategy closes, `-10.02524844 USDT` net realized PnL, 25.5% win
  rate, and profit factor 0.36;
- 33 stop-loss closes contributed `-15.146 USDT`, while seven take-profit closes
  contributed `+5.1201 USDT`;
- trades closed within four hours were the weakest holding cohorts;
- actual rank 21+ trades were 3/22 winners and contributed `-4.2731 USDT`;
- the first post-deployment exact-bar stop (SKYAI short, Universe rank 25,
  total score 23.25) had bounded MFE upper bound 0%, MAE -5.8735%, and closed
  after 17 minutes. The exact 1m protection path worked; this was a bad entry,
  not observed profit giveback;
- the entry-lock exit shadow did not trigger for that position and truthfully
  recorded zero incremental effect;
- the current UFC cohort remained shadow-only, with zero behavior-changing
  interventions.

This does not justify a rank cutoff. In the same running epoch, cost-complete
15m/1h forward outcomes did not show monotonic improvement at higher authority
ranks. Two complete scanner batches showed the pessimistic continuous twin with
less-negative selected-set mean returns than authority, but both treatments
remained negative and two batches are not an inference sample.

## Confirmed producer and Ranker seam

The entry surface is not one homogeneous Universe-score path:

- the deterministic `universe_scanner` emitter requires `total_score >= 45`
  and copies `round(total_score)` into `Opportunity.confidence_score`;
- `market_observer` iterates the full current Universe up to
  `max_candidates`, may emit an Opportunity below that threshold, and supplies
  its own model-authored confidence score;
- the Ranker then adds `0.50 * confidence_score`, `0.25 * total_score`, and
  `0.10 * tradability_score`, before subtracting the full late-entry and
  crowding penalties;
- `total_score` already contains `0.25 * tradability_score` and the same
  late-entry and crowding penalties. Direct scanner opportunities therefore
  reuse the composite score itself, and both producers reuse some composite
  components with different effective weights.

The first exact-bar stop illustrates the distinction. SKYAI was emitted by
`market_observer` with model confidence 65 while its linked Universe member had
`total_score=23.25`, `tradability_score=75`, and `late_entry_penalty=13`. Its
persisted Ranker score of 32.81 is exactly:

```text
0.50 * 65 + 0.25 * 23.25 + 0.10 * 75 - 13 = 32.8125
```

At the same Fedora checkpoint, all 51 verified closes linked to a Universe
member split by the stored `source_agent` field into 42 `market_observer` rows
(8 winners, `-9.464945 USDT`) and nine `universe_scanner` rows (five winners,
`-0.560303 USDT`). Only eight final `confidence_score` values were within one
point of the linked `total_score`. Twenty-eight entries had Universe scores
below 45; all 28 stored `source_agent=market_observer`.

The stored source is not sufficient producer or score provenance. Observer
enrichment can reuse an open deterministic Opportunity, replace its confidence
with `max(old_score, model_score)`, change its direction, and leave
`source_agent=universe_scanner`. A subsequent SLX observation demonstrated the
exact mutation: the scanner emitted short with `total_score=49.105`; the first
Ranker pass scored it 46.28. Observer enrichment changed the same row to long
with confidence 65; the next Ranker pass scored it 54.28 while its stored source
remained `universe_scanner`. No append-only score-revision row identifies the
before/after inputs.

These are descriptive, non-randomized observations spanning multiple producer
commits. They identify the authority and provenance seam but do not prove that
disabling the Observer, adding a hard rank cutoff, or changing weights would
improve net returns.

Three behavior hypotheses must remain separate:

1. **Producer eligibility:** whether Observer-created or Observer-enriched
   opportunities should inspect the full Universe or a prospectively frozen
   eligible subset.
2. **Ranker semantics and provenance:** whether model confidence and decomposed
   Universe facts should each contribute once, with immutable input revisions,
   without substituting a composite score for a missing model confidence or
   recounting its components.
3. **Universe scorer:** whether the pessimistic continuous twin selects a better
   fixed-capacity set than the current authoritative scorer.

No treatment may combine these hypotheses. Otherwise a better result cannot be
attributed to the producer boundary, score semantics, or continuous scorer.

## Causal question

For the same prospective scanner batch and the same pre-outcome candidate facts,
does the frozen pessimistic continuous score select a fixed-capacity Paper
portfolio with higher cost-complete net utility than the current authority?

The estimand is the paired batch delta:

```text
delta_batch = utility(pessimistic_twin_selected_set)
              - utility(authority_selected_set)
```

Candidate rows are not independent observations. One complete scanner batch is
one assignment/inference unit.

## Frozen treatment and control

- **Control:** current authoritative scanner score, filters, quotas, allocator,
  and deterministic allocation lineage.
- **Primary treatment:** the existing pessimistic continuous twin, frozen by
  behavior version and policy hash before the first eligible batch.
- **Diagnostic only:** point-estimate continuous twin. It cannot be substituted
  for the primary treatment after outcomes are visible.
- **Capacity:** both selected sets use the same frozen `max_candidates` and quota
  contract. Unfilled slots contribute zero utility so a smaller set cannot win
  merely by dropping difficult slots.
- **Outcome horizon:** the predeclared natural family horizon already used by
  Universe candidate settlement. It cannot be selected after observing returns.
- **Producer stratum:** original emitter, every later enrichment revision, and
  the exact Ranker-consumed revision are reported separately. The mutable
  `source_agent` projection alone is not causal attribution. A producer-policy
  change is a different experiment, not an implicit part of the
  continuous-score treatment.
- **Ranker contract:** the current formula remains the control. A score-semantics
  treatment must persist each named input and its single contribution; it cannot
  run with the continuous-score treatment in the same allocation unit.

## Required evidence contract

Each eligible batch must persist or resolve all of the following without parsing
display strings:

1. scanner run, candidate index, feature snapshot, exact instrument, direction,
   family, and candidate outcome key;
2. authority score/rank/selection and pessimistic score/rank/selection;
3. frozen authority and treatment behavior versions, policy epoch, allocator
   random state, capacity, quota, and availability profile;
4. one cost-complete settled outcome for every candidate needed by either set;
5. fee, spread/slippage, funding, settlement source, lag, and cost model version;
6. immutable batch completeness status and a deterministic evidence fingerprint.

Missing candidates, duplicate mappings, identity conflicts, stale/mixed policy
epochs, incomplete costs, or incomplete selected sets make the entire batch
ineligible. A partially observed batch must never be treated as zero return.

The existing `universe_twin_shadow_records` and `filter_candidate_outcomes`
contain most facts, but the current batch contract is explicitly diagnostic:
all observed batches have `promotion_eligible=false`, and the stored blocking
reasons include missing assignment, availability, allocator lineage, propensity,
policy epoch, and fixed-horizon outcome linkage. Those reasons must not be
silently cleared by a read-time join.

## Utility and harm metrics

The primary batch utility is fixed-capacity, cost-complete net return across the
selected slots. Report alongside it:

- treatment and control gross return, fees, spread/slippage, and funding;
- selected-set downside tail and worst candidate;
- family/direction/market representation and empty-slot count;
- turnover and selection disagreement;
- current regime and candidate availability profile.

Promotion is forbidden when the average improves by hiding costs, increasing the
predeclared downside-tail harm metric, starving a required family solely because
of missing data, or relying on one symbol/batch.

## Fixed-look qualification

The confirmatory plan must be registered before eligible outcomes exist:

- at least seven consecutive eligible UTC market days;
- at least 50 complete scanner batches;
- exactly one confirmatory look at a frozen information cutoff;
- paired ITT analysis over all cutoff batches;
- positive practical delta and an owner-approved one-sided family alpha;
- no harm breach, identity conflict, coverage gap, or cost incompleteness;
- monitoring results before the cutoff are descriptive and cannot promote;
- a neutral or negative consumed look cannot later turn positive by waiting for
  more favorable batches.

The final alpha, practical-effect threshold, and downside budget must be approved
before implementation. They must not be inferred from the two currently observed
batches.

## Interaction with existing Learning

Universe Family Calibration and the twin scorer both alter Universe scoring.
They must not receive behavior authority in the same batch unless a combined
treatment was prospectively designed. The first Paper trial therefore permits
only one active behavior treatment in the Universe-scoring overlap group; the
other path remains shadow/control.

Historical UFC experiments and prior twin rows remain immutable evidence. A new
behavior version or policy epoch starts a fresh qualification window. Candidate
outcomes from an older version may be shown as history but cannot authorize the
new scorer.

## Rollout sequence

1. **Diagnostic linkage:** persist direct twin-to-outcome identity and complete
   batch settlement; expose paired batch rows and producer-specific Ranker input
   decompositions with immutable revision identity. No score, eligibility, or
   selection changes.
2. **Shadow validation:** prove deterministic replay, restart idempotency,
   coverage, costs, fixed-look readiness, and one-use score decomposition on
   Fedora. Replaying a proposed Ranker formula remains non-authoritative.
3. **Owner review:** review the frozen treatment, thresholds, sample plan, and
   observed shadow evidence.
4. **Paper 10%:** prospectively randomize complete batches or allocation units;
   retain a long-lived control. No retrospective backfill.
5. **Paper 25% / 50% / active:** each stage requires a fresh experiment and its
   own consumed confirmatory verdict. Harm or integrity failure immediately
   refreezes the treatment.

No stage changes Live, Risk approval, Execution Gateway routing, order semantics,
or hard loss limits.

## Focused verification matrix

- exact one-to-one candidate/outcome mapping; duplicates and missing rows fail;
- candidate order and batch fingerprints are stable across restart/replay;
- authority and twin receive the identical candidate availability set;
- fixed capacity and quota replay cannot use outcome-time information;
- incomplete costs or mixed epochs make the whole batch unavailable;
- one batch with many candidates counts as one inference unit;
- point and pessimistic variants cannot be switched after outcomes;
- cutoff freeze and verdict consumption are CAS/write-once;
- monitoring frequency cannot change the confirmatory decision;
- UFC and twin authority cannot overlap without a combined registered treatment;
- Paper treatment changes only the permitted score/selection surface;
- Risk, Execution, Live, credentials, and hard limits are unchanged.

## Review gate

Approval of this document authorizes implementation of diagnostic linkage and
shadow validation only. A second explicit approval is required before any Paper
score or selection treatment is enabled.
