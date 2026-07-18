# Learning Positive-Contribution Implementation Plan

> Status: implementation in progress; integrity ledger, scheduler continuity, factor/universe
> authority, prompt manifests, causal reflection, and automatic block routing are implemented.
> Normative target: `docs/design/contracts/learning_evolution_contract.md`.
> Existing behavior contract: `docs/design/contracts/learning_contract.md`.
> Supersedes: `docs/plans/learning/LEARNING_MODULE_REPAIR_PLAN.md` for future work.
> Full-loop re-audit: 2026-07-12; includes universe calibration, observer memory,
> factor feedback, prompt-channel overlap, causal reflection, filter diagnostics,
> concurrent operator controls, shared outcome truth, and non-authoritative analytics.
> Product stage: MVP trust hardening; no new exchange, database service, vector store,
> reinforcement-learning framework, or live authority.
> Phase 3 update (2026-07-12): retrospective factor/family findings now create neutral
> shadow experiments. Paper influence requires stable treatment/control assignment,
> minimum time, unique linked cost-aware outcomes, and a positive verdict; negative or
> invalid evidence restores the static baseline immediately.
> Lifecycle update (2026-07-12): the supported universe calibration path now has a durable,
> restart-safe paper lifecycle. Measurement remains separate from lifecycle action; every stage
> promotion creates a fresh experiment and salt, while paper influence and lifecycle automation
> remain default-off. This does not add live authority.
> Attribution update (2026-07-12): Operator Learning channels persist as one bundle
> manifest, observer advisory memory has its own Opportunity-linked manifest, reflection
> is causal-id-only, and automatic block suggestions route to non-blocking guard shadow
> candidates instead of pretending to be Risk Engine lessons.
> A1 truth-repair delivery (2026-07-18): current universe contribution measurement is now
> full-arm, exact-key, and cluster-randomized. Canonical filter outcomes, selected-member legacy
> fallback/mirror checks, complete-cluster aggregation, experiment activation timestamps, verdict
> clocks/versioning, default-closed authenticated-owner admission boundary, single evaluator scheduling, durable failure
> history, and active-exposure retention anchors are aligned in code and contract. This is a
> measurement-integrity delivery, not evidence of empirical positive contribution.
> Remaining A2: owner-facing observability for cluster membership/exclusions, source fallback or
> disagreement, metric-version history, evaluator catch-up/last-failure state, and explicit trial
> admission recommendations. A2 remains read-only and adds no admission control. UI completion is
> not claimed; paper influence and lifecycle automation remain off by default.

## 1. Outcome And Non-Negotiable Standard

This plan turns Learning from an artifact-production pipeline into a measured control loop:

```text
attributable outcome
  -> unique scoped hypothesis
  -> prospective shadow/control evaluation
  -> bounded paper influence
  -> cost-aware contribution verdict
  -> promote / hold / demote / refreeze
  -> later evidence can overturn the result
```

Success is not “more lessons”. Success is:

- an influence can be traced to the exact later candidate/decision/size;
- its counterfactual is measured at the same horizon;
- duplicates never inflate evidence;
- positive contribution is required before influence expands;
- harmful or unmeasurable learning automatically returns to static behavior.

No implementation may claim guaranteed profit. Markets are non-stationary. The guarantee
is procedural: Learning must prove value continuously and lose authority when it cannot.

## 2. Root Cause To Design Response

| Root cause | Required design response | Proof artifact |
| --- | --- | --- |
| Repeated summaries from one source multiply evidence | canonical hypothesis fingerprint + source/cohort uniqueness | LE-B01/02 |
| Global evidence floor approves narrow claims | per-scope sample/time gates | LE-B04/C01 |
| “Applied” does not mean consumer changed behavior | per-consumer LearningExposure | LE-B06/07/10 |
| Ranker lesson/memo deltas are not persisted | before/after score components | ranker exposure E2E |
| Prompt lessons have no decision manifest | persisted lesson ids/versions and assignment | prompt E2E |
| Guard success disappears because blocked candidates never become positions | settle blocked candidate shadows and use them in guard analytics | LE-B08/09 |
| Contribution report counts artifact rows, not unique trades | cohort-aware distinct outcome aggregation | LE-B02 |
| Reviewer can stop while producers keep writing | durable job schedule/watermark/backlog health + catch-up | LE-B11/12 |
| `autonomous_learning` UI control is not a universal execution gate | single effective Learning enable gate | LE-S03 |
| Sizing learner is enabled but inactive on sparse regime tags | attribution coverage gate + shadow baseline + explicit inactivity reason | LE-B10/C03 |
| Draft skills exist without measurable use | advisory-only activation tied to proven source hypothesis | skill E2E |
| Current reports can label activity as effectiveness | contribution verdict API and honest UI states | LE-C06 |
| Factor feedback can hard-block paper candidates from small retrospective groups | register as a learned filter; shared clean window, shadow/control, promotion and demotion | LE-B13/C08 |
| Memo affects observer, ranker, and Operator but has one artifact identity | stage-specific exposures and overlap groups; never triple-credit one close | LE-B14/C09/C10 |
| Reflection can infer outcomes by symbol or recent movement | causal decision→proposal/opportunity→position/outcome resolver; heuristic text is ineligible | LE-B15 |
| Outcome quality/economics differ by analytics path | one versioned outcome adapter and explicit source precedence | LE-B16/C11 |
| LLM reviews and explanations can look like evidence | narrative evidence class cannot add samples or promotion weight | LE-B17 |
| Exit advisor emits actionable-sounding changes | permanent proposal-only registry authority; no config/position-manager consumer | LE-B18 |
| Prompt channels overlap (memo, reasoning, calibration, reflection, lessons) | bundle experiment first; isolated/factorial component tests only after enough data | LE-C10 |
| Automatic lessons emit non-`risk` categories while Risk Engine only honors `risk` hard actions | keep automatic recommendation advisory; route typed `block_*` into learned-guard shadow path, never relabel to gain Risk authority | LE-B19/C05 |
| Universe family multiplier changes upstream scores ±40% after 10 retrospective samples | register as a first-stage Learning treatment with neutral `1.0` control and holdout rollback | LE-B20/C12 |
| Operator loop can concurrently tighten confidence/risk/stop/scanner config | persist policy epochs; stratify/pause/invalidate experiments; never withhold safety tightening or credit it to Learning | LE-B21/C13 |

### 2.1 Audited path register

Implementation starts from this code-backed inventory; it is not permitted to scope Learning to
`aletheia/learning/` alone.

| Path id | Producer → consumer | Present influence | Initial experiment unit / fallback |
| --- | --- | --- | --- |
| `universe_family_calibration` | forward calibration stats → universe allocator | family/direction/market score multiplier 0.6–1.4 | scored asset → neutral multiplier `1.0` |
| `observer_advisory_bundle` | router cases/lessons/guards + memo → market observer | changes LLM opportunity output | observer run/symbol → no-Learning memory block |
| `ranker_case_score` | retrieved trade cases → ranker | negative soft score | candidate → static score |
| `ranker_memo_score` | latest memo → ranker | ± score | candidate → static score |
| `ranker_factor_filter` | factor feedback → ranker | hard block plus status context | candidate → factor filter disabled |
| `ranker_learned_guard` | trading guards → ranker | hard paper-entry block | candidate → shadow/unblocked counterfactual |
| `operator_learning_bundle` | reflection + memo + reasoning + calibration + lessons → Operator | changes trade/skip/direction/conviction | decision opportunity → same prompt without bundle |
| `risk_lesson_constraint` | applied structured lesson → Risk Engine | selected block actions after dry run | proposal → existing static risk behavior; change-controlled migration |
| `persona_sizing` | regime/outcome learner → sizing | bounded parameter selection | proposal → static/persona baseline |
| `advisory_skill` | proven hypothesis → future prompt/ranker adapter | currently draft/no consumer | inherits source exposure → no advisory |
| `exit_policy_advice` | trade path diagnosis → analytics API | read-only | no experiment/authority in this program |
| `filter_ttl_shadow` | historical filter replay → prospective observe-only evaluator | review-ready proposal only | first exposure → production filter unchanged |
| `operator_safety_control` | system assessment → operator config actions | authorized tightening/recommendations under operator contract | not a Learning treatment; policy epoch/confounder |

Supporting truth/health paths—trade attribution, trade path, shadow outcomes, filter/universe
forward outcomes, economics/semantics, close reasons, clean window, scheduler, doctor, alpha/memo/
guard reports—must also be registered even though they do not directly alter a candidate.

The `risk_lesson_constraint` row documents current behavior, not the intended automatic path.
Dreaming-produced `block_*` recommendations use categories such as `entry_timing`, `risk_sizing`,
or `symbol_selection`; they must not be relabelled `risk` merely to make the Risk Engine consume
them. Phase 3 converts them into scoped learned-guard hypotheses and prospective shadow evidence.

## 3. Target Code Architecture

### 3.1 New Learning Core

```text
aletheia/learning/
  models.py              Typed hypothesis/exposure/experiment/verdict models
  registry.py            Declared producer→consumer paths, authority and safe fallback
  hypotheses.py          Fingerprint, dedup, lifecycle transitions
  evidence.py            Clean/scope-complete evidence eligibility
  outcomes.py            Versioned outcome adapter, economics and source precedence
  experiments.py         Stable assignment and treatment/control manifests
  overlap.py             Mutually exclusive bundles/factorial credit rules
  policy_epochs.py       Concurrent config/model/safety changes and experiment validity
  exposures.py           Consumer influence ledger
  contribution.py        Unique-cohort metrics, bootstrap, harm gates
  promotion.py           Promote/demote/refreeze state machine
  scheduler_health.py    Expected runs, watermarks, backlog/refreeze signals
  lessons.py             Existing LessonStore compatibility adapter
  skills.py              Existing safe advisory skill store
```

The core has no imports from exchange clients, execution brokers, live routes, or secrets.
It may read normalized storage and emit typed advisory decisions. Consumers retain ownership
of their behavior.

### 3.2 Integration Adapters

| Consumer | Adapter change | Boundary |
| --- | --- | --- |
| Universe allocator | baseline/learned family multiplier, exposure and neutral fallback | upstream candidate supply |
| Paper ranker | calculate baseline and learned score; persist components/assignment | `agents/paper_trader_ranker.py` |
| Factor feedback | clean exact-scope eligibility; shadow/control before block/preference | analytics + paper ranker |
| Filter evidence | register treatment/control outcomes for all candidates | `analytics/filter_evidence.py` |
| Market observer | stage-specific Learning manifest and opportunity outcome link | high-sensitivity decision chain |
| System Operator | build one initial Learning-bundle manifest and persist it with decision | high-sensitivity decision chain |
| Reflection | replace eligible symbol heuristic with causal outcome resolver | high-sensitivity decision chain |
| Guards | link active guard to hypothesis/experiment and settle blocked shadows | knowledge/ranker path |
| Sizing | persist baseline and learned notional; one-param trial | trading sizing path |
| Scheduler | durable expected-run/catch-up and universal enable gate | runtime scheduler |
| Alpha audit | consume exposures/outcomes, distinct by cohort/candidate | read-only analytics |
| Diagnostics | mark exit advice/narrative review as non-authoritative evidence classes | read-only analytics |
| Operator safety loop | emit concurrent policy epochs; pause/stratify experiments | existing operator authority, not Learning authority |
| API/UI | contribution, lifecycle, experiment, and health read models | cached read endpoints |

### 3.3 Dependency Direction

```text
storage/data outcomes
       ↓
learning core (pure policy/evidence decisions)
       ↓
consumer adapters (universe / observer / ranker / prompt / guard / sizing)
       ↓
existing risk and execution gates
```

Risk and execution do not call a generic Learning override. The current lesson hard-block
path is migrated to the evidence-gated trading-guard path; removing or changing that path
is a separate change-controlled batch with focused Risk Engine tests.

`registry.py` is the architecture root for this subsystem. A behavior-changing adapter cannot
be added merely by importing an analytics helper: it needs a declared path, consumer stage,
authority ceiling, experiment unit, outcome source, overlap group, health job, and fallback.

### 3.4 Concrete Existing-Code Touch Map

This map is the implementation checklist. A phase task narrows it further and declares its own
boundary before editing; no phase should edit all files at once.

| Area/file | Required implementation | Must not change |
| --- | --- | --- |
| `universe/allocator.py`, `universe/service.py` | neutral/learned multiplier components, registry assignment, exposure before scoring, holdout and `1.0` fallback | scanner logic, quotas and execution eligibility |
| `analytics/trade_attribution.py`, `trade_path.py` | expose resolver-quality/causal completeness; avoid silent eligible defaults | causal ids and existing read-only behavior |
| `analytics/close_reasons.py`, `learning_window.py`, `outcome_semantics.py`; `universe/outcome_economics.py` | compose behind `learning/outcomes.py`, version the decision and exclusion reason | cost-aware conservative unknown semantics |
| `analytics/factor_feedback.py` | consume clean eligible rows; separate diagnostic status from authorized treatment; exact-scope hypothesis id; standardize `factor_key` (current Operator adapter asks for nonexistent `key`) | no direct DB/LLM/exchange mutation |
| `agents/paper_trader_ranker.py` | named baseline/case/memo/factor/guard components; registry lookup; exposure before influence; static fallback | venue, execution-eligibility and non-Learning hard filters |
| `analytics/filter_evidence.py`, `universe/filter_outcome_recorder.py`, `shadow_trades.py` | link experiments/exposures and settle every declared horizon idempotently | existing observe-only and price-quality rules |
| `analytics/filter_shadow_experiments.py` | optionally nominate inactive hypothesis; retain manual-review/production-mutation prohibition | must never apply TTL to production |
| `analytics/trade_case_builder.py`, `universe/outcome_recorder.py` | attach unique source outcome/cohort/hypothesis provenance to cases/guard candidates | deterministic case/outcome construction |
| `agents/post_trade_reviewer.py` | attach evidence class/source ids; keep prose non-quantitative | no confidence auto-upgrade or direct promotion |
| `agents/risk_critic.py` | register advisory narrative source and causal proposal/risk-decision ids | cannot change hard limits or create an automatic risk override |
| `agents/dreaming_agent.py`, `agents/lesson_reviewer.py` | fingerprint before write/review; exact-scope gate; durable run/watermark; shadow-only result | LLM cannot waive data gates |
| `learning/lessons.py`, `skills.py`; knowledge manager/retrieval/router | compatibility links, versioned manifest items and safe fallback | archive-not-delete and forbidden skill layers |
| `analytics/insight_synthesizer.py`, `memo_effectiveness.py` | memo hypothesis/version/source cohort; stage-specific use tracking | time decay and contradiction correction |
| `analytics/reasoning_feedback.py`, `confidence_calibration.py` | shared clean outcome adapter and bundle component ids | advisory-only prompt authority |
| `agent_shell/reflection.py` | causal resolver and explicit verified/unverified result | JSON/DB audit trail and no invented settled outcome |
| `agents/market_observer.py` | stable observer assignment, exact manifest, emitted-opportunity link, empty-bundle fallback | market provider and canonical-symbol rules |
| `agent_shell/trading_context.py`, `trading_decision.py` | one Operator bundle assignment/manifest; persist what was rendered | market/risk inputs identical across arms |
| `knowledge/guards.py`, `analytics/guard_promotion.py`, `guard_impact.py`, `guard_effectiveness.py` | hypothesis links, prospective blocked outcomes, supply harm, holdout/demotion | seed/safety guard classification and archive history |
| `trust/guard_chain.py` | registry as advisory observability and preserve separate reporting identity | never wire its `block` label into execution or merge it with learned ranker guards |
| `trading/persona.py`, `persona_rollout.py`, `sizing_policy.py` | baseline/learned notional exposure and resolver-based bucket evidence | Risk Engine caps, leverage/stops/pyramiding freeze |
| `analytics/exit_policy_advisor.py` | registry entry and structural no-consumer test only | read-only/no config or position mutation |
| `analytics/filter_registry.py`, `filter_replay.py`, `filter_controller.py`, `filter_shadow_experiments.py` | registry entries, source provenance and observe-only hypothesis nomination | no direct production filter/config mutation |
| `agent_runtime/scheduler.py` | shared enable gate, durable expected-run/catch-up/health for every mutating Learning job | worker cap and SQLite concurrency rules |
| `agent_shell/operator_loop.py` | publish policy/config epoch events and invalidate/stratify affected experiments | existing safety authority, human gate for loosening, risk caps |
| trade insights/stats, family scorecard, opportunity flow, debate impact, thesis lineage, go-live scorecard | register as source/read-model/concurrent-control inputs with shared outcome quality where eligible | no promotion authority inferred from diagnostic verdicts |
| storage migrations/models/repositories | additive tables/columns/indexes and transaction-safe idempotency | one-connection-per-thread and pre-write transaction invariant |
| diagnostics/analytics/server/frontend | cached read models, honest status/coverage/overlap/fallback | no hot async disk/SQLite scans; no count-based optimism |
| `risk/engine.py` | only a separately scoped migration from lesson hard blocks if authorized | hard limits, required approval and gateway invariants |

Every row that changes behavior receives a focused current-behavior regression first, then the
new target claim. Risk/execution/live modules are not bulk-edited as part of Learning.

## 4. Data Model And Migration

### 4.1 New Tables

#### `learning_hypotheses`

```sql
hypothesis_id TEXT PRIMARY KEY
fingerprint TEXT NOT NULL UNIQUE
cohort_id TEXT NOT NULL
source_type TEXT NOT NULL
source_artifact_ids_json TEXT NOT NULL
path_id TEXT NOT NULL
overlap_group TEXT NOT NULL
scope_json TEXT NOT NULL
action_json TEXT NOT NULL
consumer TEXT NOT NULL
target_horizon TEXT NOT NULL
status TEXT NOT NULL
version INTEGER NOT NULL
effect_threshold REAL NOT NULL
harm_budget_json TEXT NOT NULL
evidence_summary_json TEXT NOT NULL
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
expires_at TEXT
```

#### `learning_experiments`

```sql
experiment_id TEXT PRIMARY KEY
hypothesis_id TEXT NOT NULL
stage TEXT NOT NULL
assignment_salt TEXT NOT NULL
allocation_pct REAL NOT NULL
min_samples_per_arm INTEGER NOT NULL
min_market_days INTEGER NOT NULL
primary_metric TEXT NOT NULL
promotion_gate_json TEXT NOT NULL
harm_gate_json TEXT NOT NULL
status TEXT NOT NULL
started_at TEXT
ended_at TEXT
created_at TEXT NOT NULL
```

Current activation semantics: ordinary hypothesis experiment registration leaves the experiment
`registered` with no evidence start. The first durable exposure atomically/idempotently changes an
open registered experiment to `active`, and `started_at` is maintained as the earliest persisted
exposure time. Unexposed registrations are not inferred to have observation windows.

#### `learning_exposures`

```sql
exposure_id TEXT PRIMARY KEY
dedup_key TEXT NOT NULL UNIQUE
experiment_id TEXT NOT NULL
hypothesis_id TEXT NOT NULL
assignment TEXT NOT NULL
consumer TEXT NOT NULL
consumer_stage TEXT NOT NULL
path_id TEXT NOT NULL
overlap_group TEXT NOT NULL
assignment_unit_key TEXT
candidate_key TEXT
opportunity_id TEXT
proposal_id TEXT
decision_id TEXT
observer_run_id TEXT
position_id TEXT
before_json TEXT NOT NULL
after_json TEXT NOT NULL
manifest_json TEXT NOT NULL
outcome_candidate_key TEXT
outcome_source TEXT
outcome_contract_version TEXT NOT NULL
code_commit TEXT
policy_version TEXT
concurrent_policy_epoch TEXT NOT NULL
exposed_at TEXT NOT NULL
```

#### `learning_contribution_verdicts`

```sql
verdict_id TEXT PRIMARY KEY
experiment_id TEXT NOT NULL
hypothesis_id TEXT NOT NULL
window_start TEXT NOT NULL
window_end TEXT NOT NULL
unique_treatment_n INTEGER NOT NULL
unique_control_n INTEGER NOT NULL
baseline REAL
observed REAL
delta REAL
confidence_low REAL
confidence_high REAL
harm_metrics_json TEXT NOT NULL
verdict TEXT NOT NULL
reason TEXT NOT NULL
created_at TEXT NOT NULL
```

For `universe_family_calibration`, `assignment_unit_key` is the persisted randomization cluster.
Member candidate/outcome keys remain exact causal identities, while one complete cluster is one
intent-to-treat allocation-utility sample: selected members contribute net return, dropped members
contribute zero, and worst selected-member MAE is retained. The allocator selection fact is
persisted before evaluation. Incomplete clusters and auditable legacy exposures lacking this field
are excluded rather than inferred or reduced to survivors.

#### `learning_job_health`

```sql
job_id TEXT PRIMARY KEY
schedule_key TEXT NOT NULL
last_expected_at TEXT
last_started_at TEXT
last_succeeded_at TEXT
next_due_at TEXT
input_watermark TEXT
backlog_count INTEGER NOT NULL DEFAULT 0
oldest_backlog_at TEXT
consecutive_failures INTEGER NOT NULL DEFAULT 0
status TEXT NOT NULL
last_error TEXT
last_failed_at TEXT
last_failure_reason TEXT
updated_at TEXT NOT NULL
```

Recovery clears current `last_error` and resets consecutive failures, but preserves
`last_failed_at` and `last_failure_reason` as the most recent durable failure.

#### `learning_policy_epochs`

```sql
epoch_id TEXT PRIMARY KEY
epoch_kind TEXT NOT NULL
effective_values_hash TEXT NOT NULL
effective_values_json TEXT NOT NULL
affected_path_ids_json TEXT NOT NULL
source_event_id TEXT
safety_required INTEGER NOT NULL DEFAULT 0
started_at TEXT NOT NULL
ended_at TEXT
created_at TEXT NOT NULL
```

Epochs cover effective configuration, universe/filter policy, prompt/model version, strategy
lifecycle and execution-condition changes that can alter treatment/control outcomes. Secret values
are never stored; only non-secret effective fields and hashes are eligible.

### 4.2 Existing Table Extensions

- `ps_lessons`: add nullable `hypothesis_id`, `fingerprint`, `review_reason_json`.
- `ps_skills`: add nullable `hypothesis_id`, `activated_at`, `demoted_at`.
- `knowledge_items` guard/memo artifacts: store hypothesis/version in tags/metadata;
  no taxonomy change is required.
- System Operator decision persistence: add `learning_manifest_json` to the existing
  decision record that already owns the reasoning result. If no durable decision table
  can carry it without ambiguity, introduce `decision_learning_manifests` instead of
  hiding it in free-form rationale.
- Market-observer opportunity/run persistence: add a manifest link or normalized
  `decision_learning_manifests` owner row so observer memory influence is not lost before ranking.
- Factor/filter traces: persist hypothesis/experiment/exposure ids alongside the existing
  factor hard-block step and fixed-horizon outcome registration.

### 4.3 Migration Rules

1. Additive migrations only.
2. No historical artifact is hard-deleted.
3. Existing applied lessons are imported as `proposed` or `shadow`, never assumed positive.
4. Historical duplicates map to one fingerprint/cohort; duplicate rows remain archived/auditable.
5. Existing active guards enter `shadow_validation` until blocked-candidate outcome coverage
   proves or contradicts them; safety-approved static seed guards remain separately labelled.
6. Existing skill drafts remain drafts and receive hypothesis links only when provable.
7. Backfill is idempotent and emits counts, not runtime facts into Git documentation.
8. Historical factor statuses, prompt injections, and reflection text are imported as
   retrospective/unmeasured observations only; they cannot backfill treatment assignments.
9. Existing filter shadow experiments remain observe-only and can nominate a hypothesis, but
   `ready_for_review` does not become production or Learning promotion evidence by itself.
10. Existing operator-loop config/audit history seeds policy epochs only where exact effective
    values/timestamps are provable; ambiguous windows are invalid rather than guessed.

Schema addition requires updating `docs/design/data/data_model.md` and `spec_data_model.py` in the
same implementation batch.

## 5. Fingerprint And Scope Design

Canonical fingerprint input:

```json
{
  "consumer": "guard",
  "action": {"type": "block_candidate"},
  "scope": {
    "symbol": "AAVE/USDT",
    "direction": "long",
    "scanner_family": "momentum",
    "market_type": "usdt_futures",
    "regime": null
  },
  "target_horizon": "4h",
  "source_cohort": "closed_trade_case:v2",
  "policy_schema_version": 1
}
```

Normalization rules:

- canonical symbol format;
- lower-case enumerations;
- sorted lists and JSON keys;
- absent dimension is `null`, not wildcard unless explicitly declared;
- numeric values quantized to declared configuration precision;
- summaries, timestamps, LLM prose, and lesson ids are excluded from identity.

One extractor call may produce several genuinely distinct hypotheses, but repeated prose for
the same normalized scope/action/horizon produces one hypothesis with multiple descriptions.

## 6. Evidence And Reviewer Redesign

### 6.1 Two Gates, Not One

The reviewer first checks deterministic eligibility, then optional semantic consistency:

```text
deterministic exact-scope evidence gate
  -> contradiction/dedup checks
  -> optional LLM explanation/judgment
  -> shadow only
```

The LLM cannot override insufficient evidence. Its accepted output is a reason/explanation,
not a sample or promotion vote.

### 6.2 Exact-Scope Query

Evidence query includes every populated scope dimension. If a hypothesis says
`BTC/USDT + short + momentum`, global or BTC-long outcomes cannot satisfy its sample gate.

Store both:

- exact-scope statistics used for eligibility;
- broader context statistics used only for interpretation.

### 6.3 Reviewer Continuity

- scheduled extractor and reviewer use durable date-window idempotency keys;
- restart catch-up processes one missed window, not every historical missed tick;
- each run writes an `agent_runs` or equivalent durable record even with zero outputs;
- proposed backlog age can refreeze new promotions while existing static behavior continues;
- `autonomous_learning=false` disables extraction, review, promotion, experiments, and
  every Learning consumer including universe calibration, learned ranker/filter/guard influence,
  prompt bundles and learned sizing together; read-only reports remain enabled;
- the flag does not disable operator/risk safety tightening, which remains a concurrent control.

### 6.4 Shared Outcome Resolver

`learning/outcomes.py` normalizes every eligible result before any producer/reviewer/report uses
it. It wraps the existing close-reason, clean-window, outcome-semantics and outcome-economics
rules rather than duplicating them.

Source precedence:

1. an actually executed decision uses its causally linked settled position with realized costs;
2. a blocked/skipped/non-executed candidate uses the registered fixed-horizon candidate outcome;
3. a same-stage shadow may be used only at its declared horizon and cost-model version;
4. legacy gross-only movement is explanatory, never positive-contribution evidence;
5. symbol-only, unresolved, contaminated, missing-direction, stale-price, or incomplete derivative
   funding data returns `unavailable`, never zero-cost/neutral/win.

For the implemented universe contribution evaluator, this general precedence is concretized as:

1. batch-read exact keys from `filter_candidate_outcomes`, the canonical full-arm source;
2. treat `universe_forward_outcomes` as a selected-member legacy mirror/fallback only;
3. do not double-count agreeing copies and fail closed on economic disagreement;
4. never let an incomplete assignment cluster become a selected-survivor sample.

Every evidence record stores resolver version, source, horizon, quality, and exclusion reason.
`factor_feedback`, reasoning/calibration/memo builders, persona sizing, guards, and contribution
reports migrate to this adapter before authority can expand.

### 6.5 Evidence Classes

| Class | Examples | May count samples? | May change behavior directly? |
| --- | --- | --- | --- |
| settled quantitative | causal realized close; cost-aware candidate outcome | yes, once per experiment unit/horizon | only after experiment gate |
| deterministic derived | trade case, factor aggregate, calibration statistic | no additional sample beyond sources | proposal/shadow only until proven |
| narrative | post-trade review, LLM lesson explanation, memo prose, reflection text | no | advisory treatment only |
| diagnostic proposal | exit-policy advice, replay-ready filter threshold | no | no; owner/review workflow only |

This prevents a close, its case study, its review, its memo summary, and six duplicate lessons from
becoming ten independent observations.

## 7. Experiment Design By Learning Path

### 7.1 Universe Family Calibration

Current `get_family_multipliers()` can change opportunity scores by up to ±40% after ten settled
rows. That is a real upstream Learning treatment, not a harmless dashboard calibration.

Implementation:

1. replace direct retrospective authority with a scoped hypothesis keyed by scanner family,
   direction, market type, natural horizon and calibration version;
2. use the shared clean outcome adapter and require prospective neutral/treatment arms;
3. persist raw opportunity score, neutral score, multiplier, treated score and allocation result;
4. keep quotas, tradability, execution eligibility and regime direction brakes identical;
5. measure fixed-horizon opportunity quality and supply/concentration harm before downstream stages;
6. promote paper influence only after the common gate and maintain continual neutral holdout;
7. negative, invalid, sparse or stale evidence restores multiplier exactly to `1.0`.

The existing ten-row threshold becomes nomination-only. It cannot preserve production-like score
authority merely because it already exists in code.

### 7.2 Ranker Cases, Memos, And Factor Feedback

Implementation:

1. split current score into named components;
2. calculate `baseline_score` without learned components;
3. calculate bounded `case_delta`, `memo_delta`, factor decision, and guard decision separately;
4. persist every component before candidate selection;
5. assign eligible candidates to treatment/control prospectively;
6. register all horizons through existing filter outcome settlement;
7. ranker selection continues to obey all non-Learning hard filters in both arms.
8. migrate factor feedback from retrospective `hard_blocked`/`preferred` classification to a
   registered hypothesis: historical data can nominate shadow, not immediately grant authority;
9. enforce the central clean-data floor and exact factor scope before factor eligibility;
10. continuously settle candidates that factor feedback would block, and auto-demote on reversal.

Initial bounds:

- per-hypothesis score delta: `[-10, +10]`;
- total learned advisory delta: `[-20, +20]`;
- a score bonus cannot make an execution-ineligible candidate eligible;
- negative contribution demotes the hypothesis to shadow immediately after gate evaluation.
- the existing ten-trade hard-block threshold is removed as an authority gate; it may remain only
  as an early diagnostic label until the prospective gate is satisfied.

### 7.3 Learned Guards

Implementation:

1. candidate generation creates a hypothesis, not an active guard;
2. deduplicate by exact high-dimensional scope;
3. settle matched candidate shadows at 15m/1h/4h/24h;
4. evaluate primarily at the predeclared horizon;
5. enter paper trial only after shadow contribution is positive;
6. maintain a small deterministic shadow/control allocation so active guards remain refutable;
7. guard analytics reads `filter_candidate_outcomes`, not only positions.

Promotion must consider opportunity-supply harm. A guard that avoids losses but removes an
excessive share of otherwise profitable supply fails the harm gate.

### 7.4 Market-Observer Learning Bundle

The market observer is an upstream Learning consumer and receives both advisory retrieval and the
latest memo. It needs its own experiment because it can change whether an opportunity exists before
the ranker or Operator sees it.

1. build an observer manifest of case/lesson/guard/memo ids and versions per symbol run;
2. assign the observer bundle prospectively while keeping market inputs/model settings identical;
3. persist the manifest on emitted opportunities or a normalized run-manifest link;
4. compare opportunity emission, direction, confidence, and fixed-horizon cost-aware outcomes;
5. do not use later executed PnL as isolated observer credit when ranker/operator Learning also ran;
6. stale/missing/refrozen Learning returns an empty observer bundle, not partially mixed context.

### 7.5 System Operator Learning Bundle

Implementation:

1. build a compact typed manifest before prompt rendering;
2. stable treatment/control assignment determines whether Learning context is rendered;
3. persist manifest, token budget, evidence ids, and assignment;
4. preserve identical market, account, risk, and opportunity inputs between arms;
5. connect decision to executed result or same-cycle shadow result;
6. measure trade rate, direction, conviction calibration, and cost-aware outcome.
7. treat reflection, memo, reasoning, calibration, and structured lessons as one initial overlap
   bundle; component credit remains `not_identifiable` until isolated/factorial experiments run;
8. replace reflection's eligible outcome lookup with decision/opportunity/proposal/position ids and
   bounded timestamps; retain heuristic symbol/move text only as visibly unverified context;
9. persist disagreement and which bundle component the Operator cited without treating citation as
   causal proof.

The experiment is paper-only. The control group withholds advisory Learning context, never
safety instructions or hard risk controls.

### 7.6 Persona/Sizing Learning

Implementation:

1. fix regime attribution coverage before activating any bucket;
2. compute baseline notional and learned notional for every eligible proposal;
3. remain shadow until each bucket meets unique clean-close/time gates;
4. unlock only base notional first; leverage, stops, and pyramiding remain frozen;
5. evaluate risk-adjusted net contribution, drawdown, and concentration;
6. auto-refreeze on distress, attribution degradation, negative holdout, or insufficient
   rolling samples.

Counterfactual PnL for sizing must use the same price path and cap at actually available
liquidity/exposure. It must not pretend unfillable size had the same execution quality.

### 7.7 Advisory Skills

Skills become reusable wrappers around proven hypotheses:

- automatically activatable only for `prompt_context` and `ranking_advisory`;
- no arbitrary generated Python/tool capability;
- activation requires the source experiment's positive verdict;
- every use creates an exposure;
- negative source/skill holdout demotes the skill automatically;
- human activation cannot add a forbidden layer.

### 7.8 Diagnostics And Concurrent Controls

- `post_trade_reviewer` prose stays medium-confidence narrative and cannot satisfy a sample gate;
- deterministic case studies carry the causal source ids used by hypotheses;
- `exit_policy_advisor` remains read-only and has no adapter to config, position manager, risk, or
  execution; future exit-policy automation requires a separate owner-approved program;
- existing filter TTL shadow experiments remain prospective observe-only proposals. A human review
  cannot silently turn `ready_for_review` into a production mutation.
- operator-loop safety tightening and approved config changes remain under their existing contract,
  are never withheld for a Learning control, and create a concurrent policy epoch. Affected
  experiments pause, stratify, or invalidate instead of attributing the change to Learning.
- debate, strategy lifecycle, live-readiness and execution-condition changes are likewise recorded
  as concurrent policy versions when they affect an experiment cohort.

## 8. Contribution Engine

### 8.1 Aggregation

All metrics use distinct `(experiment_id, candidate_key, horizon)` or the equivalent
decision identity. Artifact count is never a weight.

The contribution engine resolves overlap before aggregation:

- one candidate may have many exposure rows for audit, but only the predeclared experiment unit
  contributes one observation per horizon;
- mutually exclusive assignment is the default within `observer`, `ranker`, `operator`, and
  `sizing` overlap groups;
- multi-stage outcomes are reported as total bundle effect unless upstream/downstream treatments
  were isolated by design;
- memo component contribution is not inferred from a trade that also received memo influence in
  another stage;
- retrospective correlations remain labelled weak/unidentified and cannot promote.

Primary calculation:

```text
delta = mean(treatment net return) - mean(control net return)
```

For before/after compatibility reports, deduplicate hypotheses by cohort and report them
as weak evidence. Promotion uses prospective controls only.

### 8.2 Confidence

Use deterministic seeded bootstrap over unique observations. Store the seed, resample count,
confidence interval, and metric version. No new scientific dependency is required; NumPy is
already present.

Initial promotion example:

```text
unique treatment >= 50
unique control >= 50
span >= 7 market days
delta >= +0.10 percentage points net
80% bootstrap lower bound > 0
95th-percentile MAE not worse by > 0.25 points
no-trade/supply loss within configured budget
```

The exact defaults live in config and the normative contract/spec; UI changes accompany
config additions.

### 8.3 Verdict Semantics

- `positive`: promotion gates pass.
- `neutral`: effect is inside practical-neutral band; hold current authority, do not expand.
- `negative`: effect or harm gate fails; demote/refreeze.
- `insufficient_data`: no behavioral conclusion; remain shadow/static.
- `invalid`: attribution, contamination, or metric contract violated; refreeze and repair data.

## 9. API And UI Design

### 9.1 Read APIs

Proposed endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/analytics/learning-contribution` | verdict by path/hypothesis, unique samples, controls, harm metrics |
| `GET /api/learning/hypotheses` | lifecycle and scope, paginated |
| `GET /api/learning/experiments` | active/completed experiment state |
| `GET /api/learning/health` | expected jobs, last success, backlog, refreeze reason |
| `GET /api/learning/exposures/{entity_id}` | why a candidate/decision/size changed |
| `GET /api/learning/paths` | declared paths, authority ceiling, overlap group, fallback, health |

Endpoints use a 30–60 second cached read model. Aggregation runs in a sync worker or scheduled
materializer, never as unbounded SQLite/Markdown work in an async handler.

### 9.2 Mutation APIs

Only lifecycle review operations:

- approve/reject a proposal for shadow evaluation;
- manual refreeze/demote/archive;
- approve advisory skill activation;
- never bypass evidence gates or live/risk controls.

All mutations write audit events and use optimistic status transitions.

### 9.3 Learning UI

Replace count-first health with five panels:

1. Contribution by path: verdict, net delta, interval, unique treatment/control.
2. Active experiments: scope, horizon, stage, progress, harm status.
3. Hypothesis lifecycle: proposed/shadow/paper/active/demoted.
4. Operational health: last expected/successful run, backlog age, refreeze reason.
5. Decision trace: exact learned influences for a selected candidate/trade.
6. Data integrity: outcome contract version, causal coverage, exclusions, unresolved and overlap
   rates; component contribution is shown as `not identifiable` when appropriate.

Words such as “effective” or “improving” render only for a measured positive verdict.

## 10. Configuration

Proposed `LearningConfig` fields:

```yaml
learning:
  enabled: true
  experiments_enabled: false
  paper_influence_enabled: false
  min_samples_ranker_per_arm: 50
  min_samples_guard_shadow: 30
  min_samples_prompt_per_arm: 50
  min_samples_sizing_bucket: 30
  min_market_days: 7
  min_net_delta_pct: 0.10
  bootstrap_resamples: 2000
  max_total_ranker_delta: 20
  max_supply_reduction_pct: 30
  auto_refreeze: true
```

Implementation must either migrate `agent.autonomous_learning` to this effective config or
derive one canonical gate without breaking compatibility. Every new schema field receives a
Settings control in the same batch. Hard risk parameters are not Learning config.

## 11. Phased Implementation

Each phase is its own task/branch/PR. A phase cannot expand authority before the previous
phase's evidence and observability are GREEN.

### Phase 0 — Measurement Integrity And Baseline

Goal: make existing reports honest before changing behavior.

Delivery status: measurement-integrity batch A implemented. Path registry, evidence classes,
shared fail-closed outcome adapter, unique/overlap aggregation, Learning-alpha deduplication,
blocked-guard outcomes, and pure policy-epoch semantics are present. Consumer-wide outcome
migration, durable epoch persistence, and UI contribution rendering remain before Phase 0 is closed.

Changes:

- codify the audited path registry and an architecture tripwire for undeclared consumers;
- add the shared outcome resolver contract and report per-path eligibility/coverage gaps;
- identify concurrent policy/config epochs and invalidate ambiguous historical windows;
- deduplicate Learning alpha by cohort/candidate/horizon;
- label before/after evidence weak and remove repeated-artifact confidence inflation;
- add guard counterfactual outcomes to effectiveness analytics;
- split activity, influence, and contribution metrics;
- classify narrative/diagnostic artifacts as non-sample evidence;
- add `LE-S04`, `LE-B02/09/16/17/18`, `LE-C06/10/11` RED→GREEN tests;
- record a versioned baseline report in runtime analytics, not Git docs.

Authority change: none.

Exit gate: every current producer/consumer has an authority/fallback entry; eligible outcomes use
one contract; current behavior can be measured without duplicate or overlapping credit, and guard
blocks are visible through settled shadow outcomes.

### Phase 1 — Durable Hypothesis And Exposure Ledger

Goal: create causal identity before more learning is activated.

Changes:

- migrations and repositories for hypotheses/experiments/exposures/verdicts/job health;
- typed models and fingerprint logic;
- backfill existing lessons/guards/memos/factor statuses to non-active hypothesis states;
- update data model/retention/export/import documentation;
- add hypothesis/exposure read APIs.

Authority change: none.

Exit gate: repeated sources are idempotent and every future influence has a place to be recorded.

### Phase 2 — Producer Hygiene And Scheduler Continuity

Current status (2026-07-12): implemented for exact-scope reviewer gating, shared autonomous
job control, durable catch-up watermarks, backlog health, and promotion refreeze.

Goal: stop noisy production and stalled review.

Changes:

- source/cohort dedup at extraction time;
- exact-scope deterministic evidence gate before LLM review;
- durable scheduled-run/catch-up records;
- universal effective enable gate;
- backlog health and automatic promotion refreeze;
- maintenance migration for historical duplicates (soft archive/link only).

Authority change: new promotions remain disabled.

Exit gate: no duplicate hypothesis from retries/restarts; missed job catches up once; disabling
Learning actually disables autonomous mutation.

### Phase 3 — Universe, Ranker, And Guard Prospective Experiments

Current status (2026-07-12): universe/factor/ranker numeric consumers now create separate
candidate-linked prospective exposures and restore their static baselines without authority.
Automatic lesson blocks route to non-blocking guard shadow candidates. Full blocked-candidate
settlement and the aggregate promotion/demotion evaluator remain later work.

A1 integrity update (2026-07-18): universe contribution evaluation now covers selected and dropped
full arms from canonical `filter_candidate_outcomes`, uses bounded exact-key reads, checks the
selected-member `universe_forward_outcomes` mirror/fallback, and counts complete persisted
assignment clusters rather than members. Each cluster measures allocation utility, not the
exogenous mean return of every candidate: selected members contribute net return and dropped
members contribute zero. Metric version
`universe_family_calibration_cluster_allocation_utility_v2` starts a new append-only verdict history;
old verdicts remain audit history. This does not establish a positive empirical result or enable
paper.

Goal: prove the already-active low-risk feedback paths.

Changes:

- universe family-calibration neutral/treatment exposure, supply metrics and `1.0` rollback;
- ranker score component exposure logging;
- factor feedback clean-window/exact-scope migration and prospective experiment;
- automatic lesson `block_*` actions become learned-guard hypotheses; do not coerce category to
  `risk` or broaden Risk Engine authority;
- stable experiment assignment;
- blocked-guard shadow settlement;
- guard contribution and supply-harm gates;
- shadow→paper→active promotion/demotion state machine;
- active guard continual holdout.

Authority change: paper universe/ranker/guard only, behind config defaults OFF.

Exit gate: harmful universe/factor/guard treatments automatically demote, and beneficial treatments
promote only after unique prospective evidence; retrospective ten-trade labels cannot directly
change universe scores or block candidates.

### Phase 4 — Observer And System Operator Bundle Experiments

Goal: establish separately whether upstream memory improves opportunities and whether the complete
Operator Learning bundle improves decisions.

Changes:

- typed compact Learning manifest;
- observer-run manifest and opportunity outcome linkage;
- treatment/control prompt rendering;
- persisted decision manifest and disagreement reason;
- same-cycle shadow linkage;
- contribution by lesson/hypothesis and prompt bundle.
- causal reflection resolver; heuristic reflection text marked unverified and ineligible;
- bundle-first overlap assignment for memo/reasoning/calibration/reflection/lessons;
- isolated component tests remain disabled until bundle evidence and sample capacity permit them.

Authority change: paper System Operator context only.

Exit gate: observer and Operator effects are queryable separately; exact bundle influence and
control comparison exist for every opportunity/decision; no channel double-counts one close.

### Phase 5 — Sizing Shadow Learner

Current status (2026-07-12): the System Operator sizing path computes and records static,
learned-proposal, and actual notional; missing causal links, missing authority, no proposed delta,
or adapter failure returns the static value. Promotion evaluation and liquidity-aware sizing
counterfactual reporting remain later work.

Goal: activate one bounded learned parameter only with adequate attribution.

Changes:

- regime attribution coverage diagnostics and catch-up where provable;
- baseline/learned notional exposure;
- liquidity-aware counterfactual sizing result;
- per-bucket promotion and distress/negative-holdout refreeze;
- keep leverage/stops/pyramiding frozen.

Authority change: paper base notional only, config default OFF.

Exit gate: positive bucket promotes; negative or sparse bucket returns exactly to static baseline.

### Phase 6 — Advisory Skill Evolution

Goal: make skills reusable without granting arbitrary code or trading authority.

Changes:

- skill activation tied to proven hypothesis/version;
- prompt/ranker skill consumers with exposure logging;
- automatic advisory demotion;
- human UI review; forbidden-layer validation remains immutable.

Authority change: advisory only.

Exit gate: activated skill use is measurable and cannot name or reach forbidden layers.

### Phase 7 — Controlled Rollout And Continuous Audit

Goal: operate the complete loop safely.

Rollout ladder:

1. measurement only;
2. shadow experiments;
3. paper treatment allocation 10%;
4. paper 25%;
5. paper 50%;
6. paper active with continual holdout;
7. any live influence requires a separate owner decision and task.

At every rung, negative evidence refreezes the current paper experiment and invalid evidence also
marks it invalid; in both cases the consumer authority falls back to its static baseline immediately.

## 12. SDD Delivery Matrix

Target claim definitions live in `learning_evolution_contract.md §12`.

Implementation mapping:

| Batch | Spec files | Claims |
| --- | --- | --- |
| Phase 0 | `spec_learning_registry.py`, `spec_learning_contribution.py`, existing outcome/operator specs | LE-S04, LE-B02/09/16/17/18/21, LE-C06/10/11/13 |
| Phase 1 | `spec_learning_evolution.py`, `spec_data_model.py` | LE-S01/02, LE-B01, LE-C05 |
| Phase 2 | `spec_learning_evolution.py`, `spec_agent_contracts.py` | LE-S03, LE-B03/04/11/12, LE-C01 |
| Phase 3 | `spec_learning_experiments.py`, `spec_factor_feedback.py`, `spec_filter_evidence.py`, `spec_lesson_guards.py`, `spec_universe_pipeline.py` | LE-B05/06/08/09/13/19/20/22, LE-C02/03/04/05/08/12 |
| Phase 4 | `spec_pi_decision_chain.py`, `spec_insight_feedback_loop.py`, `spec_learning_experiments.py` | LE-B07/14/15, LE-C04/07/09/10 |
| Phase 5 | `spec_persona_sizing.py`, `spec_learning_experiments.py` | LE-B10, LE-C02/03/07 |
| Phase 6 | `spec_skill_store.py`, `spec_learning_evolution.py` | LE-C02/05/07 |
| Lifecycle runtime | `spec_learning_lifecycle.py`, `test_learning_lifecycle.py`, `test_learning_lifecycle_scheduler.py` | LE-L01/02/03/04 |
| A1 contribution integrity | `spec_learning_authority.py`, `spec_learning_verdict_runtime.py`, `spec_learning_ledger.py`, `spec_learning_continuity.py` | LE-B26, refined LE-V01, refined LE-L04 |

A2 observability remains: surface canonical-versus-fallback outcome use, cluster member and
exclusion counts, metric-version history, current and last-failure scheduler health, catch-up state,
and the read-only owner-admission recommendation without implying effectiveness. Admission mutation
remains separately scoped. No Learning UI completion is recorded by A1.

SDD sequence for every new claim:

1. add `SpecClaim(status="missing")`;
2. add RED L2a/L2b/L2c test;
3. implement minimal architecture slice;
4. make GREEN;
5. add negative and recovery cases;
6. update contract status and conformance matrix.

No claim becomes `implemented` from source-string presence alone when behavior is the requirement.

## 13. E2E Test Matrix

New test module family: `tests/e2e/test_learning_evolution_*.py`.

| E2E | Scenario | Required assertions |
| --- | --- | --- |
| E2E-LE-01 | close→case→hypothesis | full causal ids, clean outcome, one scoped proposed hypothesis |
| E2E-LE-02 | retry/restart duplicate | one fingerprint/cohort; duplicate source count does not increase unique samples |
| E2E-LE-03 | narrow claim with broad global data | exact scope below gate remains deferred |
| E2E-LE-04 | scheduler misses reviewer window | one catch-up run, durable success, no duplicate outputs |
| E2E-LE-05 | autonomous Learning disabled | no producer/reviewer/promoter writes; read audits still work |
| E2E-LE-06 | ranker treatment/control | stable assignment, before/after components, same safety filters, settled outcomes |
| E2E-LE-07 | beneficial guard | blocked shadows underperform control; promote after gate only |
| E2E-LE-08 | harmful guard reversal | holdout turns negative; guard demotes/refreezes; baseline candidates return |
| E2E-LE-09 | prompt lesson experiment | manifest ids persist; control omits only Learning section; decisions/outcomes link |
| E2E-LE-10 | prompt disagreement | Operator disagreement recorded; lesson not silently treated as applied behavior |
| E2E-LE-11 | sizing positive bucket | learned/base sizes stored; paper promotion remains inside Risk Engine cap |
| E2E-LE-12 | sizing distress/data loss | immediate static fallback and refreeze reason |
| E2E-LE-13 | advisory skill lifecycle | proven source→active advisory→exposure→negative holdout→demoted |
| E2E-LE-14 | forbidden skill/risk mutation | rejected structurally; no risk/execution/live state change |
| E2E-LE-15 | contribution report | distinct samples, correct delta/interval, honest insufficient/negative/positive states |
| E2E-LE-16 | crash between exposure and settlement | idempotent recovery links one outcome, no double count |
| E2E-LE-17 | historical backfill | duplicates soft-linked/archived; no hard deletion; second run no-op |
| E2E-LE-18 | API/UI honesty | inactive/refrozen/insufficient displayed from backend values, not optimistic labels |
| E2E-LE-19 | retrospective factor loser | stable `factor_key` reaches Operator/ranker/exposure; history may nominate shadow but cannot hard-block before prospective gate |
| E2E-LE-20 | factor promote then reverse | exact-scope clean treatment wins, promotes in paper, later holdout loss restores static factor behavior |
| E2E-LE-21 | observer bundle isolation | same market/model input; manifest persists on opportunity; outcome credits observer bundle only |
| E2E-LE-22 | overlapping Operator channels | memo/reasoning/calibration/reflection/lesson exposures remain one bundle observation, not five wins |
| E2E-LE-23 | reflection causal mismatch | same-symbol unrelated position/move is labelled unverified and cannot settle the decision exposure |
| E2E-LE-24 | shared outcome quality | synthetic close, dirty window, gross-only move, missing perp funding, wrong horizon all remain ineligible |
| E2E-LE-25 | narrative fan-out | one close→case→review→memo→duplicate lessons still contributes one unique source outcome |
| E2E-LE-26 | exit diagnostic boundary | advice can be read but cannot write config, risk, position-manager, order, or execution state |
| E2E-LE-27 | undeclared consumer | architecture/runtime guard rejects behavior-changing Learning use without registry/fallback/experiment metadata |
| E2E-LE-28 | automatic block recommendation | non-`risk` lesson cannot gain Risk authority; becomes scoped guard shadow and needs prospective evidence |
| E2E-LE-29 | universe calibration lifecycle | neutral/treatment family score persists; positive paper gate promotes; negative holdout restores `1.0` |
| E2E-LE-30 | concurrent config change | operator tightening starts new policy epoch and pauses/stratifies affected Learning verdict |
| E2E-LE-31 | safety control coexistence | Learning control never withholds kill/risk/operator safety tightening and never claims its benefit |

Test design rules:

- isolated SQLite database and frozen clock;
- no real exchange, network, secrets, or live mode;
- deterministic fake LLM responses only where semantic review is under test;
- treatment and control share identical market inputs;
- overlap-group fixtures prove mutually exclusive assignment or declared factorial cells;
- outcome fixtures include fees, spread/slippage, and adverse cases;
- assert persisted rows and causal links, not just returned dictionaries;
- test promotion and later demotion in the same lifecycle scenario;
- include property tests for stable fingerprint/assignment under ordering and retries.

## 14. Verification Commands

Per phase, narrow tests first. Final pre-merge evidence:

```bash
uv run pytest tests/specs/spec_learning_evolution.py \
  tests/specs/spec_learning_contribution.py \
  tests/specs/spec_learning_experiments.py -q

uv run pytest tests/e2e/test_learning_evolution_*.py -q

uv run pytest tests/specs/spec_filter_evidence.py \
  tests/specs/spec_factor_feedback.py \
  tests/specs/spec_insight_feedback_loop.py \
  tests/specs/spec_learning_pipeline.py \
  tests/specs/spec_lesson_guards.py \
  tests/specs/spec_opportunity_outcomes.py \
  tests/specs/spec_operator_loop.py \
  tests/specs/spec_universe_pipeline.py \
  tests/specs/spec_pi_decision_chain.py \
  tests/specs/spec_persona_sizing.py \
  tests/specs/spec_skill_store.py \
  tests/specs/spec_risk_engine.py -q

uv run pytest tests/test_architecture_fitness.py tests/specs/spec_trust_boundary.py -q
uv run pytest tests/specs/ -q
uv run pytest tests/ -q --tb=line
uv run aletheia doctor
git diff --check  # then require the normal PR CI matrix
```

Frontend phases also run `cd frontend && npm run build`. New/changed API endpoints update
`api_reference.md`; schema fields update Settings in the same batch.

## 15. Rollback And Failure Handling

Code rollback is not the primary safety mechanism. Runtime behavior must fail safe:

- disable experiment/treatment flags → static current behavior;
- refreeze hypothesis/path → baseline score/prompt/size;
- demote guard → shadow evaluation only;
- demote skill → no advisory consumption;
- preserve all evidence and audit records;
- never rewrite outcomes to make contribution positive.

Database migrations are additive. Old binaries ignore new tables/nullable columns. A rollback
does not require destructive schema reversal.

## 16. Performance And Capacity

- candidate hot path: O(1) hypothesis lookup from in-memory/TTL cache;
- exposure inserts are bounded, indexed, and batched where safe;
- contribution aggregation is scheduled/materialized, not recomputed per UI poll;
- no Markdown reads in hot API or ranker loops;
- retention may compact raw exposure details only after preserving immutable aggregate and
  causal ids; hard deletion follows existing retention contract, never ad hoc Learning cleanup;
- scheduler worker limits and SQLite concurrency pillars remain unchanged.

## 17. Security And Trust Review Notes

Phases that touch `risk/`, `execution/`, live routes, permissions, or tool registry require
their machine-policy command evidence. This plan intentionally avoids execution changes.

The migration away from direct lesson hard blocks may touch `risk/engine.py`; that batch must:

- declare the protected invariant;
- add focused approval/rejection regressions first;
- preserve a RiskDecision for every evaluated proposal;
- prove Learning cannot create a generic risk override;
- run `spec_risk_engine` and `spec_kill_conditions` as required.

## 18. Completion Audit

The entire program is complete only when evidence proves every item:

- normative claims LE-S01 through LE-C11, including every intermediate LE-B claim, are GREEN;
- all 31 E2E scenarios pass against persisted state;
- universe calibration, observer, factor filter, ranker, prompt bundle, guard, sizing, and skill paths each have an
  observable baseline fallback;
- every audited Learning-adjacent path has a registry entry and undeclared behavioral use fails;
- all eligible evidence paths use one versioned outcome adapter and report exclusions honestly;
- overlapping channels cannot claim independent credit from one outcome;
- duplicate artifacts cannot change unique sample counts;
- scheduler catch-up and disable semantics work under restart;
- a beneficial fixture promotes and a later harmful fixture demotes/refreezes;
- UI/API never infer effectiveness from activity;
- full specs, architecture, trust-boundary checks, frontend build, and full tests pass;
- task packets/handoffs contain current-HEAD verification evidence;
- live authority and hard risk limits remain unchanged unless separately authorized.

Passing only the positive-promotion test is insufficient. Automatic reversal is part of the
definition of self-evolution.

## 19. Recommended Task DAG

```text
LE-00 path registry + outcome integrity + honest measurement
  -> LE-01 schema + hypothesis ledger
      -> LE-02 producer dedup + scheduler continuity
          -> LE-03 universe + ranker + factor exposures
          -> LE-04 guard shadow experiments
              -> LE-05 contribution/promotion engine
                  -> LE-06 observer bundle experiment
                  -> LE-07 Operator bundle + causal reflection experiment
                      -> LE-08 sizing shadow learner
                          -> LE-09 advisory skills
                              -> LE-10 UI + controlled rollout audit
```

`LE-03` and `LE-04` may proceed in parallel after the ledger exists. Concurrent operator/risk
policy epochs are a Phase-0 prerequisite and are never disabled for experiment cleanliness.
Observer and Operator bundle
experiments remain separate so upstream opportunity generation is not confused with downstream
decision changes. Prompt and sizing trials wait for the shared contribution engine so each path
does not invent a different definition of “positive”.
