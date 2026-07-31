---
id: learning-evolution
status: accepted
related_contracts:
  - ../contracts/learning_contract.md
supersedes: []
---

# Learning Evolution Proposal

> Proposal status: accepted design, not a product implementation claim.
> Current fact: the shared measured lifecycle is implemented for
> `universe_family_calibration` and `operator_learning_bundle`; other declared
> treatment hooks remain static and measurement-unsupported. The bounded
> owner-facing `lco-v1` summary/default tab is partially implemented as recorded
> in the separate observability Proposal. Production positive contribution is
> still an empirical result, not a support claim.
> Scope: every Learning-adjacent producer, store, consumer, outcome source, scheduler,
> report, learned ranker/filter, prompt channel, guard, advisory skill, sizing policy,
> causal-attribution path, and automatic rollback.
> Current authority: [`learning_contract.md`](../contracts/learning_contract.md).
> This proposal describes a future measured lifecycle. It does not become a
> current contract as individual claims turn green.

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
| Universe allocation | bounded paper/shadow opportunity-score multiplier after evidence gates |
| Ranker | score adjustment or candidate block in paper/shadow modes after evidence gates |
| System Operator prompt | advisory context with explicit source ids and confidence |
| Trading guard | paper-entry block after dry-run and prospective shadow validation |
| Sizing policy | one bounded parameter at a time, inside immutable Risk Engine caps |
| Skill | advisory prompt/ranking policy only; forbidden layers remain unreachable |

### 2.1 Full-loop boundary

The Learning boundary is defined by behavioral influence, not by package name. A module is
Learning-adjacent when it derives a reusable judgment from past outcomes, injects that judgment
into a future decision, blocks or reorders a candidate from historical performance, or measures
whether such influence helped.

The complete governed loop therefore includes:

| Stage | Current modules/data | Required target treatment |
| --- | --- | --- |
| Outcome truth | `trade_attribution`, `trade_path`, `shadow_trades`, `filter_candidate_outcomes`, `universe_forward_outcomes`, `outcome_semantics`, `outcome_economics`, close-reason and clean-window rules | one versioned outcome contract and source precedence |
| Structured producers | `trade_case_builder`, universe outcome guard producer, `dreaming_agent`, `lesson_reviewer`, guard candidate/promotion, persona sizing | hypothesis/source identity before authority |
| Narrative/analytic producers | `post_trade_reviewer`, `risk_critic`, `insight_synthesizer`, `reasoning_feedback`, `confidence_calibration`, `reflection`, `exit_policy_advisor`, `factor_feedback`, filter replay/controller/shadow experiments | declared evidence class; narrative/diagnostic output is never a quantitative vote |
| Stores | knowledge items/FTS, `ps_lessons`, policy versions, skills, filter/shadow evidence, PI decisions, positions | durable causal ids, versions, retention and no hard deletion |
| Upstream consumers | outcome-calibrated universe family multiplier; market-observer advisory retrieval and trading memo | separate universe and observer-stage experiments/outcomes |
| Candidate consumers | factor hard block/preference, case-study score, memo score, learned guards | named before/after components and safe static fallback |
| Advisory trust guards | `trust/guard_chain.py` | observability only; never conflate its `block` label with Risk Engine or learned candidate-block authority |
| Decision consumers | reflection, memo, reasoning feedback, calibration feedback, structured lessons | exact prompt manifest and bundle/component experiments |
| Proposal consumers | persona/sizing policy and existing risk-lesson checks | baseline/treatment persistence; immutable risk envelope |
| Operations | scheduler, catch-up, enable switch, doctor, learning funnel, alpha/guard/memo reports, API/UI | durable health, honest contribution status and refreeze |
| Concurrent controls | operator-loop risk/confidence/stop tightening, strategy lifecycle, debate and other non-Learning policy versions | record as safety/control changes and experiment confounders; never withhold or credit to Learning |

Modules that only propose diagnostics remain inside the inventory so a later consumer cannot
silently turn them into authority. In particular, `exit_policy_advisor` remains proposal-only;
changing stops, take profit, holding time, or position management from its output is outside this
contract until a separately approved experiment and trust-boundary review exist.

### 2.2 Declared-path registry

Every reusable Learning path must have one registry entry containing:

- stable path id, producer, source tables/item types, consumer, and maximum authority;
- exact scope dimensions, clean-data floor, outcome source and precedence, target horizon;
- treatment/control unit, overlap group, promotion/harm gates, TTL, and static fallback;
- explicit `consumer_wired` and Settings-support state, so a declared design path cannot be
  presented as operator-selectable before its real consumer and authority adapter exist;
- expected scheduler job, owner-facing health state, and required specs/E2E scenarios.

An undeclared producer may write an inactive diagnostic artifact. An undeclared consumer must
not change a score, block, prompt, size, risk decision, or exit policy. Architecture tests enforce
registry coverage at behavior-changing call sites.

Safety/operations feedback is registered as a concurrent control rather than a Learning treatment.
Automatic tightening that is already authorized by the operator/risk contract is never withheld for
an experiment and never counted as Learning profit. A change to confidence gates, risk caps, stop
policy, strategy state, universe/filter version, model/prompt version, or execution conditions starts
a new attribution epoch or invalidates/stratifies the affected experiment.

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
| `path_id` / `overlap_group` | Registry identity and attribution group for interacting channels |
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
| Universe family calibration | 50 per arm | 7 market days | clean family/direction/market/horizon candidate outcomes |
| Ranker score | 50 per arm | 7 market days | prospective treatment/control outcomes |
| Factor block/preference | 50 per arm | 7 market days | exact-scope clean candidate outcomes; historical minimum alone is insufficient |
| Guard paper block | 30 matched shadows | 7 market days | blocked candidates continue outcome settlement |
| Market-observer bundle | 50 opportunities per arm | 7 market days | observer manifest + fixed-horizon opportunity outcomes |
| Operator-prompt bundle | 50 decisions per arm | 7 market days | prompt manifest + decision/shadow outcomes |
| Sizing bucket | 30 genuine closes for proposal; 50 prospective candidates per arm for authority | 14 market days | attributed regime source cohort plus cost-aware fixed-horizon outcomes and before/after notional |
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

Assignment must be stable under retries. It uses a hash of experiment id and a canonical
assignment-unit key, never wall-clock randomness. Safety gates apply identically to both arms.
For `universe_family_calibration`, the randomization unit is the persisted
`assignment_unit_key` cluster (the scoring cohort plus family, direction, and market type), so all
members of that cluster receive the same arm. The candidate key still identifies each exact member
outcome; it is not the statistical assignment unit. Its production identity contains both the
scanner-run id and feature-snapshot id. Feature snapshots may be intentionally reused during
change suppression, but a later scanner run is a new entry-price/outcome window and must never
upsert into or share mirrors with the earlier candidate outcome.

The current universe evaluator requires every persisted member of a cluster to resolve cleanly.
One complete cluster is one intent-to-treat sample: selected members contribute net return, dropped
members contribute zero utility, and its MAE is the worst selected-member MAE. If any expected
member is missing or ineligible, the entire cluster is excluded so selected survivors cannot become
a biased sample. Legacy exposures with no persisted `assignment_unit_key` remain queryable for audit
but are excluded and are never inferred from time, symbol, candidate adjacency, or selected Universe
membership.

Controls by path:

| Path | Treatment | Control |
| --- | --- | --- |
| Universe family calibration | bounded learned family multiplier | neutral multiplier `1.0` |
| Ranker | learned score delta applied | same candidate scored without delta |
| Factor filter | historical factor block/preference applied | same candidate evaluated without that learned filter |
| Guard | candidate blocked in paper trial | matched shadow keeps hypothetical unblocked result |
| Market observer | advisory memory bundle rendered | same market input without Learning memory; compare emitted opportunities |
| Operator prompt | one declared Learning bundle rendered | bundle withheld, all market/risk/candidate input identical |
| Sizing | bounded learned notional | static/persona baseline notional |

Live trading is never used to create an untreated control group. Initial experiments are
paper/shadow only.

### 6.1 Overlap and credit assignment

When multiple Learning paths touch one candidate, the registry must either assign only one
treatment in that overlap group or use a predeclared factorial design with enough samples. The
same candidate outcome may describe several exposures, but it cannot be counted as an independent
success for every exposure. Initial rollout permits one Learning treatment bundle per stage;
component-level credit is `not_identifiable` until isolated evidence exists.

Memo, reasoning feedback, calibration feedback, reflection, and structured lessons currently
share the System Operator prompt. They are one operator-prompt bundle for the first experiment.
The same memo also reaches market observation and ranker scoring; those upstream/candidate effects
must be assigned and evaluated separately rather than crediting all three paths from one close.

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

Current runtime scope: the contribution evaluator is measurement-only and accepts only
prospective `universe_family_calibration` exposures. It isolates experiment, policy epoch,
horizon, and outcome-contract version and resolves outcomes with bounded, batched exact-key reads.
`filter_candidate_outcomes` is the canonical full-arm source because it includes selected and
dropped candidates plus the immutable allocator-selection result. One complete assignment cluster
uses intent-to-treat allocation utility: selected members contribute their net return and dropped
members contribute zero. `universe_forward_outcomes` is the selected-member legacy mirror/fallback:
an identical mirror is not counted twice, absence of a canonical row may use the legacy selected
row, and economic disagreement fails closed. Ambiguous allocation identity excludes only that
sample and remains explicit. Proven economic conflict metadata includes source row ids and exact
differing fields; it does not authorize sample-level conflict exclusion. The evaluator persists
deterministic confidence, harm, source provenance, and exclusion details. Its `positive` result is not an authority promotion
signal, so it cannot enable a treatment or alter a multiplier.

The evaluator uses metric version
`universe_family_calibration_cluster_allocation_utility_v3`. Existing verdicts under older metric
versions remain append-only history and cannot satisfy a current metric-identity gate. Verdict
`created_at` records evaluation time; `window_start` and `window_end` continue to bound the
observations used.

The current harm contract uses the experiment's 50 assignment units per arm as the minimum
determinate sample. A smaller cohort persists `harm_state=indeterminate`, blocks authority, and
does not assert a breach. After the gate, treatment loss-rate increase above `0.15` and MAE p95
absolute worsening above `0.25` percentage points breach. MAE keys use `_pp` because the comparison
is a subtraction in percentage points, not a proportional ratio. A determinate breach drives
refreeze even when the stored verdict is otherwise labelled positive.

The evaluator also reads `authority_applied` from each exposure manifest. A treatment assignment
that stayed on its baseline is a shadow observation, not an intervention. When every treatment
sample is still shadow, the cohort may prove that collection and settlement work, but it cannot
produce a causal `positive` or `negative`; after the ordinary sample/time gates are met it returns
`insufficient_data` with reason `no_actual_intervention`. Persisted verdicts carry the policy epoch,
stage, target horizon, outcome-contract version, metric version, and producing code commit.

### 7.1 Current paper lifecycle

The lifecycle controller is a separate, default-closed consumer of that measurement. It never
rewrites a measurement verdict into `promote` or any other authority word. For the supported
`universe_family_calibration` path, the mapping is fixed:

| Measurement result | Lifecycle response |
| --- | --- |
| `positive` in a paper stage | recommend one-stage promotion |
| `neutral` / `insufficient_data` | hold the current stage |
| `negative` | refreeze the current experiment |
| `invalid` | mark the current experiment invalid and refreeze its hypothesis |

Stages are isolated experiments, not a mutable allocation counter:
`shadow` → `paper_10` → `paper_25` → `paper_50` → `paper_active`.
The automatic lifecycle controller never moves `shadow` to `paper_10`. It may only report
`recommended_lifecycle_action=admit_trial`. The protected owner mutation is
`POST /api/settings/learning/experiments/{id}/admit-trial`. It requires an
explicit `confirm_admission=true`, verdict id, expected policy epoch and bounded
reason. When a tunnel is enabled it requires the tunnel owner token; local-only
operation uses Aletheia's existing trusted-local-owner boundary. The server
derives the current policy epoch itself and treats the submitted epoch only as
an optimistic concurrency check. A stale epoch, mismatched verdict/build/stage,
paused runtime, integrity incident, disabled contribution evaluation, disabled
lifecycle automation, disabled paper influence or absent path allowlist fails
closed without writing a successor.

The public `admit_shadow_trial` function remains fail-closed with
`authenticated_owner_admission_unavailable`, so a caller-provided `"owner"`
string grants no authority. A successful protected admission atomically closes
the shadow predecessor, creates one `paper_10` successor with a fresh assignment
salt and writes one deduplicated lifecycle event; a retry returns that same
successor. Its JSONL audit names `human`/`dashboard`, while scheduled transitions
retain `learning_lifecycle_controller`/`learning_lifecycle`. Later paper-stage
promotions also close the predecessor, create one successor, and require fresh
evidence before another promotion. Paper treatment is capped at 90%; the
final-stage default is 80%, leaving a permanent control holdout. Lifecycle
automation and paper influence remain disabled by default.

Before any paper influence is used, the authority check binds the requested path and successor to
the same hypothesis and its closed predecessor, then requires the activation verdict to belong to
that predecessor and hypothesis. Both activation and current-stage measurements must also carry the hypothesis's
declared horizon, current policy epoch, expected stage, outcome-contract version, metric version,
and a determinate, unbreached harm result. Missing, foreign, malformed, or indeterminate harm
identity always returns the static baseline.

Every applied transition has one durable, deduplicated lifecycle event with the source verdict,
stage change, reason, effective policy epoch, and successor identity. The event and experiment
state change commit together; the JSONL audit is appended after that durable commit. Repeating a
job after restart therefore returns the existing transition instead of creating a second successor.

An experiment may exist as `registered` before it has observations. Inserting the first durable
exposure and activating the open experiment commit atomically and idempotently. `started_at` is the
minimum persisted `exposed_at` for that experiment, including when an earlier exposure is replayed
or a legacy partial insert is repaired; an unexposed registration does not start an evidence window.

Measurement remains schedulable even while autonomous behavior is off or paused. The separate
lifecycle controller is allowed to change paper state only when reconciliation integrity is clear,
autonomous Learning is enabled, runtime is not paused, lifecycle automation is enabled, paper
influence is enabled, and the path is explicitly allowlisted. No setting here can grant live,
execution, risk, or credential authority.

The Settings API accepts only registry entries marked consumer-wired and supported. It rejects
unknown/unwired values both on direct allowlist updates and when the master influence toggle would
reactivate an unsupported value left by an older free-text configuration. The UI renders the same
registry as checkboxes; unsupported entries are visible but disabled.

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

For the current paper lifecycle, automatic refreeze is enabled by default but still respects the
same controller safety gate above. Independently, the authority resolver returns the static
baseline immediately when it sees a current `negative` or `invalid` measurement, even before a
later controller run persists the lifecycle state. A context-stale negative, invalid, or determinate
harm breach for the same experiment/hypothesis can persist the same conservative withdrawal; stale
evidence never grants authority, and wrong-stage/wrong-hypothesis evidence remains non-mutating.

## 9. Consumer Contracts

### 9.1 Ranker

- Score deltas are typed, bounded, and separately persisted.
- The candidate trace records each hypothesis id and delta.
- A guard block still registers all configured counterfactual outcome horizons.
- Ranker learning failures return the static score, not a partially modified score.
- Historical factor feedback is a learned filter: `hard_blocked`, `preferred`, and every future
  equivalent require clean-window evidence, an exposure, a prospective control, and a fallback.
- A threshold such as ten historical trades is an eligibility hint, not proof of contribution.

### 9.2 Universe Allocation

- Outcome-derived scanner-family multipliers are Learning influence, even though their code lives
  in `universe/allocator.py` rather than `learning/`.
- Family, direction, market type, horizon, score before/after, multiplier and policy version are
  persisted before universe selection.
- Historical calibration can nominate a shadow treatment but cannot grant the current ±40% score
  authority from a ten-sample threshold.
- Negative/invalid holdout restores the exact neutral multiplier `1.0`; allocation failures also
  fall back to neutral rather than a partial map.

### 9.3 Market Observer

- Retrieved cases, lessons, guards, and memos form an explicit observer-stage manifest.
- Treatment/control keeps market data and model settings identical and varies only that manifest.
- Contribution is first measured on emitted opportunity quality and fixed-horizon candidate
  outcomes; later trade PnL is secondary because downstream ranker/operator treatments intervene.
- Missing retrieval, stale memo, or Learning refreeze produces the declared no-Learning baseline.

### 9.4 System Operator Prompt

- Prompt context contains lesson ids, versions, scope, confidence, age, and evidence count.
- The persisted decision records the exact prompt learning manifest.
- Treatment/control changes only Learning context, not market/risk inputs.
- Treatment/control authorization occurs only after the active context is scoped to one exact
  market-observer pending carrier. Its candidate key excludes timestamps and unrelated queue churn,
  and its exposure links to `opportunity:<id>:4h`; mixed or Serenity-only queues fail closed without
  an unattributed exposure.
- The Operator may disagree; disagreement is recorded and becomes evidence, not an error.
- Reflection must resolve by causal decision/opportunity/proposal/position ids and time bounds.
  Symbol-only position matching or latest-price movement is explanatory text, not eligible evidence.
- Reasoning, calibration, memo, reflection, and lesson channels are attributed first as one bundle;
  no individual channel may claim credit until isolated or factorial evidence exists.

### 9.5 Guards

- `guard_candidate` is not an active guard.
- Promotion requires matched shadow outcomes at the intended horizon.
- Active guard effectiveness reads blocked-candidate outcomes, not only executed positions.
- Broad global/direction blocks require a separate explicit safety review; ordinary automatic
  guards remain high-dimensional (symbol/scanner/direction/regime where supported).

### 9.6 Sizing

- Learning adjusts one parameter at a time.
- Risk Engine caps remain the outer bound.
- Unknown regime, thin samples, distress, or attribution gaps return the static baseline.
- Learned size and baseline size are both stored for outcome attribution.

### 9.7 Skills

- A skill is a versioned reusable advisory policy, not executable code generation.
- Automatic activation is limited to prompt/ranking advisory layers after the source
  hypothesis passes its contribution gate.
- `FORBIDDEN_LAYERS` remains immutable and structurally tested.
- Skill activation, use, and demotion create durable exposures/events.

### 9.8 Narrative and diagnostic producers

- Post-trade reviews and LLM lesson explanations may describe evidence but never add samples,
  upgrade confidence, or promote an action on their own.
- Deterministic trade cases retain the original causal chain and settled outcome quality fields.
- Confidence calibration and reasoning feedback use the same clean-window, synthetic-close, and
  attribution rules as the shared outcome contract.
- Exit-policy advice remains read-only/proposal-only. No consumer may mutate stop, take-profit,
  maximum hold, trailing behavior, or position manager settings from it under this contract.

### 9.9 Outcome truth

- Every path uses a versioned shared adapter for close eligibility, direction, fees, spread,
  slippage, funding, horizon quality, and source precedence.
- Realized closed-position outcomes are used only for decisions that actually executed.
  Fixed-horizon candidate outcomes are used for blocked/skipped/non-executed decisions.
- Legacy gross-only outcomes may explain market movement but cannot prove tradable positive alpha.
- Unresolved, ambiguous, symbol-only, contaminated, or economically incomplete rows remain
  unavailable; they must never be converted to optimistic zero cost or a win.

## 10. Scheduler And Operational Continuity

Every Learning job has a durable expected-run record, idempotency key, last success,
next due time, input watermark, output counts, and failure reason.

- Restart catch-up runs at most once for the missing schedule window.
- Contribution measurement has one recurring scheduler job,
  `learning_contribution_evaluator`. `learning_measurement_startup_catchup` runs before its first
  regular interval and invokes that same evaluator only when the current window is missing; there
  is no second scheduled verdict runner. Historical `learning_verdict_runner` health rows remain
  retained operational history and do not imply a registered job.
- Current status may recover to healthy and clear `last_error`, but `last_failed_at` and
  `last_failure_reason` retain the most recent failure across recovery.
- A direct scheduled extractor is recorded even when it bypasses the agent orchestrator.
- Backlog age and count are health signals; a healthy producer with a stalled reviewer is unhealthy.
- `autonomous_learning=false` prevents all autonomous Learning producers/reviewers/promoters,
  and returns every Learning consumer (including universe family calibration) to its declared
  baseline, while manual read-only audit remains available.
- Operator/risk safety tightening is not disabled by the Learning switch; it is a concurrent
  safety control, not a Learning treatment.
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

The complete owner-facing contract is
[`learning_observability.md`](learning_observability.md). Its canonical name is
**Learning Contribution Observability**, feature id `learning_contribution_observability`; bare
"A2" is only a historical sequencing alias. The projection keeps activity, measurement,
operations and authority as four independent dimensions and exposes no mutation route.

## 12. Proposed Executable Claims

These claims are target SDD. Each implementation batch must first add its claims with
`status="missing"`, produce RED behavior tests, implement, then make them GREEN.

| Claim | Level | Requirement | Status |
| --- | --- | --- | --- |
| LE-S01 | L2a | hypothesis/exposure/experiment stores exist with stable ids | implemented |
| LE-S02 | L2a | Learning imports no exchange auth/client or live unlock route | implemented |
| LE-S03 | L2a | autonomous Learning jobs share one effective enable gate | implemented |
| LE-S04 | L2a | every behavior-changing Learning producer/consumer is declared in the path registry | implemented |
| LE-S05 | L2a | Settings renders registry-backed supported/unsupported path controls without a free-text authority editor | implemented |
| LE-B01 | L2b | repeated source/action/scope creates one hypothesis fingerprint | implemented |
| LE-B02 | L2b | duplicate artifacts do not multiply contribution sample size | implemented |
| LE-B03 | L2b | incomplete/synthetic evidence is ineligible | implemented |
| LE-B04 | L2b | reviewer defers when the exact scope misses its sample gate | implemented |
| LE-B05 | L2b | stable assignment survives retries/restarts | implemented |
| LE-B06 | L2b | every applied ranker delta stores before/after score and ids | implemented |
| LE-B07 | L2b | every prompt decision stores its Learning manifest | implemented |
| LE-B08 | L2b | blocked guard candidates continue shadow settlement | missing |
| LE-B09 | L2b | guard effectiveness consumes blocked shadow outcomes | implemented |
| LE-B10 | L2b | sizing stores learned and baseline notional | implemented |
| LE-B11 | L2b | missed scheduled windows catch up once and only once | implemented |
| LE-B12 | L2b | stalled reviewer/backlog refreezes promotion | implemented |
| LE-B13 | L2b | factor feedback uses clean exact-scope evidence and prospective exposure before influence | implemented |
| LE-B14 | L2b | observer and operator prompt bundles persist exact manifests and causal outcome links | implemented |
| LE-B15 | L2b | reflection cannot infer eligible outcomes from symbol-only or unbounded time matching | implemented |
| LE-B16 | L2b | all paths share versioned cost-aware outcome eligibility and source precedence | implemented — behavior consumers use the central eligible cohort and stale derived reports fail closed |
| LE-B17 | L2b | narrative/LLM artifacts cannot independently increase samples or promotion confidence | implemented |
| LE-B18 | L2b | exit-policy advisor remains read-only with no settings or position-management consumer | implemented |
| LE-B19 | L2b | automatic `block_*` recommendations enter guard shadow evaluation, not Risk Engine via category coercion | implemented |
| LE-B20 | L2b | universe family calibration records clean prospective neutral/treatment exposures before score influence | implemented |
| LE-B21 | L2b | concurrent policy/config changes version, stratify, pause, or invalidate affected experiments | partial — deterministic policy epochs persist and stratify current Universe and Operator-bundle exposures; unsupported paths create no exposures, and other prompt-content policy changes remain |
| LE-B22 | L2b | factor producer, Operator context, ranker trace, and exposure use the same stable `factor_key` identity | implemented |
| LE-B23 | L2b | retrying one exposure dedup key stores one immutable influence record | implemented |
| LE-B24 | L2b | a shadow cohort with no treatment applied is measurable but cannot produce a causal authority signal | implemented |
| LE-B25 | L2b | a cohort with treatment actually applied may become causally eligible after ordinary gates | implemented |
| LE-B26 | L2b | full-arm universe outcomes persist exact-key allocator selection and complete persisted assignment clusters, not selected survivors, are the statistical samples | implemented |
| LE-B27 | L2b | policy epoch ignores code-only rebuilds, rotates on effective behavior change, and exposures retain running-build identity | implemented |
| LE-B28 | L2b | conflicting settled values for one outcome identity invalidate/refreeze rather than selecting one copy | implemented |
| LE-V01 | L2b | the Universe runtime verdict adapter reads canonical full-arm filter outcomes by exact key, calculates cluster intent-to-treat allocation utility, uses selected-member Universe rows only as mirror/fallback, and fails closed on disagreement | implemented — measurement-only evaluator; Operator bundle uses its separate exact Opportunity 4h adapter; no empirical positive-contribution claim |
| LE-V02 | L2c | deterministic verdict storage cannot alter Learning authority or experiment lifecycle | implemented — evaluator writes only its idempotent measurement record |
| LE-V03 | L2c | shadow assignments with no actually applied treatment cannot produce a causal positive/negative verdict | implemented — exposure manifests feed cohort samples and return `insufficient_data/no_actual_intervention` |
| LE-L01 | L2b | measurement verdicts map to distinct lifecycle actions without changing their vocabulary | implemented |
| LE-L02 | L2c | stage transitions are transactional, idempotent, isolated, and default-closed for paper influence | implemented |
| LE-L03 | L2c | measurement and lifecycle control use separate restart-safe single-instance schedules | implemented |
| LE-L04 | L2c | shadow cannot auto-promote; the scheduled controller only recommends admission and the protected explicit-admission route requires confirmation, server-derived epoch identity and every paper gate | implemented; the separate legacy master Learning toggle is also tunnel-token protected |
| LE-L05 | L2c | paper authority binds approved evidence to its same-hypothesis closed predecessor and declared horizon | implemented |
| LE-C01 | L2c | no promotion without unique samples, time span, and outcome links | implemented |
| LE-C02 | L2c | negative/unsafe holdout automatically demotes or refreezes | implemented |
| LE-C03 | L2c | refreeze restores static baseline without deleting evidence | implemented |
| LE-C04 | L2c | control arms retain all pre-existing safety checks | missing |
| LE-C05 | L2c | Learning cannot change hard risk limits or execution authority | implemented |
| LE-C06 | L2c | UI/API contribution status is derived from measured verdict, not counts | partial — alpha diagnostics separate raw events from unique cohorts and the default Contribution tab uses backend measurement facts; the complete owner workbench remains incomplete |
| LE-C07 | L2c | full close→learn→expose→settle→promote→demote chain is causal | missing |
| LE-C08 | L2c | factor hard block/preference demotes to static behavior after negative holdout | missing |
| LE-C09 | L2c | market-observer Learning influence is separated from downstream ranker/operator influence | implemented |
| LE-C10 | L2c | overlapping Learning channels cannot double-count one outcome as independent credit | partial — Observer and Operator persist separate bundle manifests and the common aggregator dedupes outcomes; controlled prompt exposure migration remains |
| LE-C11 | L2c | data-quality or causal-link loss makes contribution unavailable and refreezes authority | partial — shared aggregator requests refreeze; consumer authority adapters remain |
| LE-C12 | L2c | negative/invalid family-calibration holdout restores neutral multiplier `1.0` | implemented |
| LE-C13 | L2c | safety/operations tightening is never withheld or credited as Learning contribution | partial — epoch core enforces semantics; operator integration remains |
| LE-C14 | L2c | disabling autonomous Learning restores declared prompt-consumer baselines without removing market/safety context | implemented |
| LE-C15 | L2c | stale policy-epoch verdicts cannot authorize a new cohort | implemented |
| LE-C16 | L2c | Observer and Operator bundles require exact prospective paper authority; control keeps the static prompt baseline | implemented |
| LE-C17 | L2c | active Operator diagnostic Learning context is curated, current, advisory and manifest-bearing without granting treatment authority | implemented |

Owner-facing contribution observability is governed by the separate `LCO-S01..02`, `LCO-B01..05`
and `LCO-C01..06` claims in `learning_observability.md`. The bounded summary, multi-path
Contribution selector, side-effect-free cached reads, current-surface responsive accessibility
and direct Preview API performance evidence are implemented. Detail/pagination and the full
workbench remain future slices.

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

## 14. Position Outcome Integrity And Incident Refreeze（OI-* target）

The Learning shared outcome adapter is only a transitional protection. Global closure semantics
belong in a dependency-neutral analytics layer so Operator, calibration, alpha, Risk separation,
reporting, and Learning cannot drift into different definitions of a “trade.” Learning composes that
central assessment with its own clean-window, attribution, sample-floor, and experiment gates.

### 14.1 Mandatory behavior-consumer migration

The following are part of the Learning/full-profit loop even when their code is outside
`aletheia/learning/`; they are completion gates, not optional analytics cleanup:

| Consumer | Required outcome semantics | OI claim |
|---|---|---|
| trade statistics, family scorecard, strategy KPI | eligible genuine outcomes only | OI-C02 |
| Operator 7d win rate / PF / streak / strategy performance | eligible; insufficient never falls back to raw | OI-C02 |
| conviction calibration and active PI calibration prompt | eligible + current semantics version + fresh report | OI-C02/C05 |
| System Operator alpha observed leg | eligible realized outcome | OI-C02 |
| trading context performance | eligible; exposes excluded count where shown | OI-C02/C04 |
| Risk daily loss | separate authoritative account-loss source, never Learning/performance PnL | OI-C03 |
| CLI/API/UI performance | raw and genuine cohorts explicitly labelled | OI-C04 |

No module may keep a local synthetic-close string set. Adding a close category or changing semantics
updates the central classifier and its version once. Behavior-changing consumers must use
`eligible_strategy_outcomes`; raw readers are limited to account state, audit, reconciliation, and
explicitly labelled operator reporting.

### 14.2 Reconciliation incident response

An administrative close followed by any native or adopted reopen with the same scoped
broker/account/environment/instrument/direction identity is a data-integrity incident. It globally
refreezes every Learning authority path to its last static baseline across restart. Read-only
measurement and incident reporting remain available. Recovery requires explicit incident resolution
and a clean observation gate; one normal poll cannot automatically unfreeze Learning.

### 14.3 Stale derived artifacts

Calibration reports, Operator assessments, contribution reports, and derived knowledge carry
`outcome_semantics_version` and clean-window identity. A missing/old version is rejected before prompt
or policy consumption, even before historical remediation runs. Historical repair archives or
supersedes polluted artifacts and regenerates them from eligible evidence; it never hard-deletes the
originals.

Executable target claims live in `tests/specs/spec_outcome_integrity.py`:

| Claim | Requirement | Status |
|---|---|---|
| OI-B00 | retain current broker/account/local-only protections | implemented |
| OI-S01 | additive closure evidence and durable observation storage | implemented |
| OI-S02 | dependency-neutral central classifier and explicit read models | implemented |
| OI-B01 | durable scoped disappearance confirmation | implemented |
| OI-B02 | broker-neutral verified exit evidence | implemented |
| OI-B03 | verified/admin economic close split | implemented |
| OI-B04/C01 | any-reopen incident + global refreeze | implemented |
| OI-C02 | all behavior-changing consumers use current eligible outcomes | implemented |
| OI-C03 | authoritative Risk account loss separated from performance PnL | implemented |
| OI-C04 | operator-facing dual reporting | implemented |
| OI-B05/C05 | additive history remediation + versioned clean window | implemented |
| OI-C06 | cross-layer lifecycle E2E and final evidence inventory | implemented |
