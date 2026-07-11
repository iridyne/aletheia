# Learning Evolution Contract

> Contract level: L2 target contract.
> Status: proposed; implementation claims are intentionally marked `missing` until delivered.
> Scope: closed-trade learning, ranker feedback, prompt guidance, learned guards,
> advisory skills, sizing policy learning, causal attribution, and automatic rollback.
> This contract extends `learning_contract.md`; the existing contract remains the
> authority for currently implemented behavior until each claim below becomes GREEN.

## 1. Product Outcome

Learning exists to improve the trading loop, not to maximize the number of memories,
lessons, policies, or skills. A Learning path is useful only when all of the following
are true:

1. its input is attributable, settled, cost-aware, and free of administrative closes;
2. its recommendation is scoped to the evidence that produced it;
3. its actual influence on a candidate, decision, or size is recorded;
4. a contemporaneous control or shadow outcome exists;
5. the treated cohort improves net outcome without unacceptable tail-risk regression;
6. later contradictory evidence can reduce, demote, or disable the influence.

Artifact production, prompt visibility, retrieval hits, and guard counts are activity
metrics. They are never evidence of positive contribution by themselves.

## 2. Immutable Boundaries

Learning must never:

- unlock live trading, submit an order, access credentials, or bypass the Execution Gateway;
- increase or rewrite hard risk limits;
- suppress kill-switch, live-state, permission, exchange, or causal-chain checks;
- learn from synthetic/administrative closes, unresolved outcomes, missing attribution,
  or pre-declared contaminated windows;
- promote an artifact because the same source was repeated many times;
- treat an LLM judgment as quantitative evidence;
- remove a losing control observation or hard-delete an embarrassing artifact;
- run an experiment that withholds an existing safety control.

Learning may influence only these bounded surfaces:

| Surface | Maximum automatic authority |
| --- | --- |
| Ranker | score adjustment or candidate block in paper/shadow modes after evidence gates |
| System Operator prompt | advisory context with explicit source ids and confidence |
| Trading guard | paper-entry block after dry-run and prospective shadow validation |
| Sizing policy | one bounded parameter at a time, inside immutable Risk Engine caps |
| Skill | advisory prompt/ranking policy only; forbidden layers remain unreachable |

Any proposal to change Risk Engine limits, execution semantics, credentials, or live
authority is outside Learning and requires a separate owner-approved, change-controlled task.

## 3. Closed Lifecycle

Every learnable hypothesis follows one state machine:

```text
observed
  -> proposed
  -> deduplicated
  -> evidence_ready
  -> shadow
  -> paper_trial
  -> promoted
  -> active
  -> {demoted | refrozen | expired | archived}
```

State meanings:

| State | Meaning |
| --- | --- |
| `observed` | One attributable outcome or pattern was recorded; no recommendation yet |
| `proposed` | A scoped recommendation exists, but it has no authority |
| `deduplicated` | The canonical fingerprint is unique; duplicates point to this artifact |
| `evidence_ready` | The exact scope meets minimum unique-sample and time-span gates |
| `shadow` | Treatment and control are assigned prospectively; actual behavior is unchanged |
| `paper_trial` | The treatment affects paper decisions only; control outcomes continue |
| `promoted` | Predeclared contribution and harm gates passed |
| `active` | The artifact currently influences its permitted consumer |
| `demoted` | New evidence contradicted the artifact; influence is reduced to advisory/shadow |
| `refrozen` | Data quality, distress, scheduler health, or harm gate forces static fallback |
| `expired` | TTL elapsed without enough confirming evidence |
| `archived` | Retained for audit but excluded from active consumption |

No direct transition from `proposed` to `active` is allowed. Manual approval may advance
review state but cannot waive evidence, shadow, risk, or attribution gates.

## 4. Canonical Learning Hypothesis

The durable unit is a `LearningHypothesis`, not a Markdown paragraph.

Required fields:

| Field | Contract |
| --- | --- |
| `hypothesis_id` | Stable id used by every exposure and outcome |
| `fingerprint` | SHA-256 of normalized scope, action, horizon, source cohort, and version |
| `source_artifact_ids` | Unique causal sources; duplicates do not increase sample count |
| `scope_json` | Explicit symbol/direction/scanner/regime/market type/confidence dimensions |
| `action_json` | Typed action and bounded parameters |
| `consumer` | `ranker`, `operator_prompt`, `guard`, `sizing`, or `skill` |
| `target_horizon` | Outcome horizon used for promotion and later monitoring |
| `evidence_window` | Start/end, clean-data floor, unique sample count, market-day count |
| `status` | Lifecycle state from §3 |
| `effect_threshold` | Predeclared minimum net improvement |
| `harm_budget` | Maximum permitted loss-rate, MAE, drawdown, or concentration regression |
| `version` | Immutable behavior version; changes create a new hypothesis version |
| `created_at` / `expires_at` | Audit and TTL anchors |

Scope cannot be broader than its evidence. A BTC-short observation cannot create a global
short block; a momentum-scanner observation cannot create an all-scanner rule.

## 5. Deduplication And Evidence Eligibility

### 5.1 Identity

Deduplication happens before review and before contribution measurement.

- Multiple summaries from the same case batch may attach as descriptions to one hypothesis.
- Reprocessing the same source is idempotent.
- A source id counts once per target horizon.
- Related hypotheses with identical normalized action/scope share a cohort id.
- Contribution reports aggregate by unique candidate/outcome and cohort, never by artifact row.

### 5.2 Eligible Evidence

Evidence must be:

- settled and cost-aware (`net_return_pct` or realized PnL after fees/slippage);
- linked to candidate/opportunity/proposal/order/position where the stage requires it;
- after the clean-data floor and outside known incident windows;
- non-synthetic according to the central close-reason classifier;
- dimensionally complete for every field in the hypothesis scope;
- temporally separated enough to avoid one burst being mistaken for repeated confirmation.

Minimums are configuration values with conservative defaults. Initial defaults:

| Path | Unique samples | Minimum span | Required evidence |
| --- | ---: | ---: | --- |
| Ranker score | 50 per arm | 7 market days | prospective treatment/control outcomes |
| Guard paper block | 30 matched shadows | 7 market days | blocked candidates continue outcome settlement |
| Prompt lesson | 50 decisions per arm | 7 market days | prompt manifest + decision/shadow outcomes |
| Sizing bucket | 30 genuine closes | 14 market days | attributed regime and realized net return |
| Advisory skill | inherits source path | inherits | no independent promotion without exposures |

These defaults may be raised without broadening authority. Lowering them is a product/risk
decision and requires owner review plus updated specs.

## 6. Prospective Exposure And Controls

Every influence creates a `LearningExposure` before the consumer acts.

Required exposure fields:

- `exposure_id`, `hypothesis_id`, `artifact_version`;
- `candidate_key`, `opportunity_id`, `proposal_id`, or `decision_id` as applicable;
- deterministic `assignment` (`treatment`, `control`, or `shadow`);
- consumer stage and timestamp;
- value before and after influence (score, prompt manifest, block result, notional);
- exact reason and matched scope;
- code commit, policy version, and experiment id;
- eventual outcome key and settlement status.

Assignment must be stable under retries. It uses a hash of experiment id and canonical
candidate key, never wall-clock randomness. Safety gates apply identically to both arms.

Controls by path:

| Path | Treatment | Control |
| --- | --- | --- |
| Ranker | learned score delta applied | same candidate scored without delta |
| Guard | candidate blocked in paper trial | matched shadow keeps hypothetical unblocked result |
| Prompt | reviewed lesson ids rendered | lessons withheld, all other prompt context identical |
| Sizing | bounded learned notional | static/persona baseline notional |

Live trading is never used to create an untreated control group. Initial experiments are
paper/shadow only.

## 7. Contribution Decision

Promotion uses predeclared metrics. The primary metric is cost-aware net return at the
hypothesis target horizon. Secondary harm metrics include:

- loss rate and tradable-edge win rate;
- MAE and tail quantiles;
- realized drawdown and concentration;
- opportunity supply reduction and no-trade rate;
- data freshness and unresolved-outcome ratio.

An artifact can promote only when:

1. unique treatment/control samples and minimum time span are satisfied;
2. treatment-control net-return delta exceeds the configured practical threshold;
3. deterministic bootstrap confidence does not include a material negative effect;
4. no harm budget is breached;
5. source, exposure, and outcome chains are complete;
6. scheduler and data-quality health are green;
7. no contradictory active artifact has equal or stronger evidence.

`insufficient_data`, `neutral`, `positive`, and `negative` are distinct verdicts. Absence
of evidence is never positive. A positive verdict expires unless continuously renewed.

## 8. Automatic Demotion And Refreeze

Every active artifact is continuously evaluated on a rolling window and an immutable
post-promotion holdout.

Automatic response:

| Condition | Response |
| --- | --- |
| effect below neutral threshold | demote to `shadow` |
| harm budget breached | immediate `refrozen` |
| attribution coverage below minimum | `refrozen` |
| scheduler misses two expected review windows | `refrozen` |
| learning job fails three consecutive attempts | disable that path and alert |
| data incident or reconciler-thrash fingerprint | global learning refreeze |
| TTL elapsed without renewal | `expired` |

Refreeze restores the last known static baseline. It must not delete hypotheses,
exposures, outcomes, or prior policy versions.

## 9. Consumer Contracts

### 9.1 Ranker

- Score deltas are typed, bounded, and separately persisted.
- The candidate trace records each hypothesis id and delta.
- A guard block still registers all configured counterfactual outcome horizons.
- Ranker learning failures return the static score, not a partially modified score.

### 9.2 System Operator Prompt

- Prompt context contains lesson ids, versions, scope, confidence, age, and evidence count.
- The persisted decision records the exact prompt learning manifest.
- Treatment/control changes only Learning context, not market/risk inputs.
- The Operator may disagree; disagreement is recorded and becomes evidence, not an error.

### 9.3 Guards

- `guard_candidate` is not an active guard.
- Promotion requires matched shadow outcomes at the intended horizon.
- Active guard effectiveness reads blocked-candidate outcomes, not only executed positions.
- Broad global/direction blocks require a separate explicit safety review; ordinary automatic
  guards remain high-dimensional (symbol/scanner/direction/regime where supported).

### 9.4 Sizing

- Learning adjusts one parameter at a time.
- Risk Engine caps remain the outer bound.
- Unknown regime, thin samples, distress, or attribution gaps return the static baseline.
- Learned size and baseline size are both stored for outcome attribution.

### 9.5 Skills

- A skill is a versioned reusable advisory policy, not executable code generation.
- Automatic activation is limited to prompt/ranking advisory layers after the source
  hypothesis passes its contribution gate.
- `FORBIDDEN_LAYERS` remains immutable and structurally tested.
- Skill activation, use, and demotion create durable exposures/events.

## 10. Scheduler And Operational Continuity

Every Learning job has a durable expected-run record, idempotency key, last success,
next due time, input watermark, output counts, and failure reason.

- Restart catch-up runs at most once for the missing schedule window.
- A direct scheduled extractor is recorded even when it bypasses the agent orchestrator.
- Backlog age and count are health signals; a healthy producer with a stalled reviewer is unhealthy.
- `autonomous_learning=false` prevents all autonomous Learning producers/reviewers/promoters,
  while manual read-only audit remains available.
- Settings UI and backend behavior use the same effective flag.

## 11. UI And API Honesty

The Learning page and API must separate:

- artifacts produced;
- artifacts eligible for review;
- active treatment exposures;
- settled treatment/control outcomes;
- measured contribution by path;
- current promotion/demotion/refreeze state;
- unique sample counts versus raw event counts;
- last successful run and backlog health.

The UI must not say `improving`, `effective`, or `healthy` from counts alone. It shows
`not measured`, `insufficient data`, or `inactive` when appropriate.

Hot read endpoints use cached/pre-aggregated data and must not scan Markdown, invoke LLMs,
or perform unbounded SQLite work inside an async handler.

## 12. Proposed Executable Claims

These claims are target SDD. Each implementation batch must first add its claims with
`status="missing"`, produce RED behavior tests, implement, then make them GREEN.

| Claim | Level | Requirement | Initial status |
| --- | --- | --- | --- |
| LE-S01 | L2a | hypothesis/exposure/experiment stores exist with stable ids | missing |
| LE-S02 | L2a | Learning imports no exchange auth/client or live unlock route | missing |
| LE-S03 | L2a | autonomous Learning jobs share one effective enable gate | missing |
| LE-B01 | L2b | repeated source/action/scope creates one hypothesis fingerprint | missing |
| LE-B02 | L2b | duplicate artifacts do not multiply contribution sample size | missing |
| LE-B03 | L2b | incomplete/synthetic evidence is ineligible | missing |
| LE-B04 | L2b | reviewer defers when the exact scope misses its sample gate | missing |
| LE-B05 | L2b | stable assignment survives retries/restarts | missing |
| LE-B06 | L2b | every applied ranker delta stores before/after score and ids | missing |
| LE-B07 | L2b | every prompt decision stores its Learning manifest | missing |
| LE-B08 | L2b | blocked guard candidates continue shadow settlement | missing |
| LE-B09 | L2b | guard effectiveness consumes blocked shadow outcomes | missing |
| LE-B10 | L2b | sizing stores learned and baseline notional | missing |
| LE-B11 | L2b | missed scheduled windows catch up once and only once | missing |
| LE-B12 | L2b | stalled reviewer/backlog refreezes promotion | missing |
| LE-C01 | L2c | no promotion without unique samples, time span, and outcome links | missing |
| LE-C02 | L2c | negative/unsafe holdout automatically demotes or refreezes | missing |
| LE-C03 | L2c | refreeze restores static baseline without deleting evidence | missing |
| LE-C04 | L2c | control arms retain all pre-existing safety checks | missing |
| LE-C05 | L2c | Learning cannot change hard risk limits or execution authority | missing |
| LE-C06 | L2c | UI contribution status is derived from measured verdict, not counts | missing |
| LE-C07 | L2c | full close→learn→expose→settle→promote→demote chain is causal | missing |

## 13. Definition Of Positive Contribution

The subsystem is not declared complete because tests pass or because it learns forever.
It is complete when it can do all of the following in a reproducible paper environment:

1. discover a scoped hypothesis from attributable outcomes;
2. deduplicate it;
3. run a prospective treatment/control or treatment/shadow evaluation;
4. promote it only on positive, harm-safe evidence;
5. record exactly which future decisions it influenced;
6. detect a later regime/effect reversal;
7. demote/refreeze and restore baseline automatically;
8. report the entire chain without double counting.

Production positive contribution remains an empirical result, not a software guarantee.
The software guarantee is that unproven or harmful learning cannot silently expand its authority.
