# Learning Subsystem Contract

> Contract level: L2. This file is normative for the Learning subsystem.
> Last aligned: 2026-07-31 measured-path and owner-observability reconciliation.
> Reference intent: [Product Direction - evidence-to-learning loop](../../product/definition/DIRECTION.md#2-complete-the-evidence-to-learning-loop).
> Future designs:
> [`learning_evolution.md`](../proposals/learning_evolution.md) and
> [`learning_observability.md`](../proposals/learning_observability.md).
> They are Proposals, not current guarantees.

Learning is not a dashboard-only feature. Its product role is to turn market
observations, trade decisions, closed trades, reviews, and later corrections into
machine-readable knowledge that can be reused by future market evaluation,
ranking, System Operator reasoning, and guarded trade approval.

Learning output must improve decision context without bypassing trust boundaries:
it must not edit credentials, unlock live trading, submit orders directly, or
change hard risk limits without explicit change control.

---

## 1. Current Contract

The current implementation has four durable artifact layers:

1. **Knowledge corpus**: `knowledge_items` + `knowledge_fts`.
   High-confidence active Markdown-backed knowledge is searchable through FTS.
2. **Structured lessons**: `ps_lessons` via `LessonStore`.
   Lessons carry structured evidence and review state.
3. **Policy versions**: `ps_policy_versions` via `PolicyStore`.
   Accepted lessons are folded into a traceable policy version.
4. **Draft skills**: `ps_skills` via `SkillStore`.
   Applied lessons can create draft long-term skill candidates. They are not
   activated automatically.

Those stores are only part of the real behavioral loop. The current loop is:

```text
realized closes + fixed-horizon candidate/shadow outcomes
  -> attribution/path/economic interpretation
  -> cases + reviews + memos + factor/reasoning/calibration/reflection + guard candidates
  -> knowledge/lesson/policy/skill/filter/shadow stores
  -> market observer + paper ranker/filters/guards + System Operator + sizing/risk checks
  -> candidate/decision/proposal/order/position or blocked/skipped counterfactual
  -> later outcomes and effectiveness reports
```

### 1.1 Current full-loop inventory

The following inventory is normative for present behavior. A path belongs here if historical
results can influence a later trading judgment, even when it lives outside `aletheia/learning/`.

| Role | Current implementation | Current authority |
| --- | --- | --- |
| Outcome joins | `analytics/trade_attribution.py`, `trade_path.py` | read-only realized-trade joins |
| Candidate counterfactuals | `counterfactual/trades.py`, `filter_evidence.py`, `universe/filter_outcome_recorder.py` | `filter_candidate_outcomes` is the canonical full-arm fixed-horizon source; `universe_forward_outcomes` is a selected-member legacy mirror/fallback, and economic disagreement fails closed |
| Outcome economics | `learning/outcomes.py`, `universe/outcome_economics.py`, `analytics/outcome_semantics.py`, `close_reasons.py`, `learning_window.py` | shared fail-closed cost/quality contract governs factor, universe, reviewer, ranker and sizing exposure authority; observer, Operator and automatic Risk-lesson boolean treatments use the same immutable exposure ledger and only link to real fixed-horizon candidate outcomes |
| Universe family calibration | `universe/allocator.py::get_family_multipliers`, `learning/experiments.py`, `universe/service.py` | 50-sample retrospective cohorts may propose a bounded 0.9/1.1 shadow treatment; actual paper influence requires stable prospective arms, linked net outcomes, minimum time, and a positive verdict; fallback is 1.0 |
| Structured cases | `analytics/trade_case_builder.py` | deterministic high-confidence cases and guard candidates |
| Universe guard producer | `universe/outcome_recorder.py` | derives candidate guards from settled rejected/pass outcomes |
| Narrative review | `agents/post_trade_reviewer.py` | medium-confidence prose; also triggers deterministic case building |
| Risk critique and lesson constraint | `agents/risk_critic.py`, `risk/engine.py`, `learning/experiments.py` | `risk_critic` writes a medium-confidence advisory research note only. Manual/owner/human Risk lessons remain static safety rules. The automatic-lesson experiment hook is measurement-unsupported, so automatic lessons currently remain advisory and cannot acquire blocking authority. |
| Lesson extraction/review | `agents/dreaming_agent.py`, `agents/lesson_reviewer.py` | promotion/overturn requires clean cost-aware outcomes matching every populated symbol/direction/scanner dimension; global history is context only |
| Memo synthesis | `analytics/insight_synthesizer.py`, `memo_effectiveness.py` | memo reaches observer prompt, ranker score, and Operator prompt |
| Factor feedback | `analytics/factor_feedback.py`, `learning/experiments.py` | exact clean history is diagnostic. The `ranker_factor_filter` hook is measurement-unsupported, creates no formal exposure, and returns the static watch/neutral behavior. |
| Reasoning feedback | `analytics/reasoning_feedback.py` | Operator prompt advice |
| Confidence calibration | `analytics/confidence_calibration.py` | Operator prompt advice |
| Last-decision reflection | `agent_shell/reflection.py` | Operator prompt advice; only exact proposal→position or opportunity→fixed-horizon/shadow outcomes are rendered |
| Operator Learning bundle | `learning/prompt_bundles.py`, `agent_shell/trading_decision.py` | one of two measurement-supported paths: exact single-carrier treatment/control exposure, Opportunity 4h outcome, verdict and shared paper lifecycle; authority remains default-off |
| Learned guards | `knowledge/guards.py`, `analytics/guard_promotion.py` | seed/manual guards retain their static safety class. Learned guards remain observe-only because `ranker_learned_guard` is measurement-unsupported; retrospective matches alone cannot grant block/penalty authority. |
| Trust guard chain | `trust/guard_chain.py` | pre-risk observability only; even a `block` verdict does not block execution |
| Case/memo score | `agents/paper_trader_ranker.py`, `learning/experiments.py` | retrospective case/memo values are diagnostics only. Both ranker paths are measurement-unsupported, create no formal exposure, and return static zero deltas. |
| Advisory retrieval | `knowledge/router.py`, `knowledge/retrieval.py` | ordinary advisory context remains available; the separate `observer_advisory_bundle` treatment hook is measurement-unsupported and injects no experimental bundle |
| Persona learning | `trading/persona.py`, `persona_rollout.py`, `sizing_policy.py`, `trading/sizing.py` | clean realized history can calculate a bounded proposal, but `persona_sizing` is measurement-unsupported and actual notional remains the static baseline; downstream Risk caps remain unchanged |
| Exit diagnostics | `analytics/exit_policy_advisor.py` | read-only advice only; no automatic mutation |
| Filter diagnostics | `analytics/filter_registry.py`, `filter_replay.py`, `filter_controller.py`, `filter_shadow_experiments.py` | shadow recommendations/observe-only experiments; no production mutation |
| Contribution/health | measured Learning ledger/evaluator plus `lco-v1`, alpha, memo, guard impact/effectiveness, learning-funnel analytics, doctor and scheduler | bounded cached `lco-v1` summary and multi-path measured-contribution selection are shipped with timing/responsive accessibility proof; detail/pagination and the broader five-tab workbench remain future slices |
| Supporting audits | trade insights/stats, family scorecard, opportunity flow, debate impact, thesis lineage, go-live scorecard | read models/gates; some are outcome sources or concurrent controls, not Learning treatments |
| Operator control loop | `agent_shell/operator_loop.py` | may tighten confidence, size/risk cap, stop or scanner settings under its own safety contract; loosening requires higher authority |

### 1.2 Measured-path maturity

`consumer_wired` means a call site exists. It does not mean the path creates an exposure, has an
outcome adapter, can be measured, or can receive authority. Current measured maturity is:

| Path/capability | Consumer | Exposure + outcome | Measurement/authority | Current conclusion |
| --- | --- | --- | --- | --- |
| `universe_family_calibration` | wired | full-arm canonical outcomes plus mirror/fallback | supported; owner-gated paper lifecycle | code-level causal loop complete; production contribution is collecting, not proven |
| `operator_learning_bundle` | wired | exact single-Opportunity 4h outcome | supported; owner-gated paper lifecycle | code-level measured loop exists; UI and production maturity are behind Universe calibration |
| `observer_advisory_bundle` | wired | unsupported | unsupported | static observer context; no experimental bundle or exposure |
| `ranker_case_score`, `ranker_memo_score`, `ranker_factor_filter` | wired | unsupported | unsupported | static score/filter baseline |
| `ranker_learned_guard` | wired | unsupported | unsupported | learned guards observe only; seed/manual guards are a separate static safety class |
| `risk_lesson_constraint` | wired | unsupported | unsupported | automatic lessons advisory-only; manual/owner lessons are non-experimental static constraints |
| `persona_sizing` | wired | unsupported | unsupported | learned proposal may be displayed; applied notional remains static |
| Lesson → policy → draft skill | wired artifact loop | reviewed realized evidence, not measured-ledger exposure | artifact lifecycle only | implemented; draft skills have no automatic activation or authority |
| `advisory_skill` | unwired | unsupported | unsupported | declared placeholder with no production consumer |
| `filter_parameter_tune` | unwired skeleton | unsupported | unsupported | deferred design; no override table, producer or authority |
| `lco-v1` owner projection | read-only consumer | reads existing ledger | no mutation authority | summary, multi-path UI and current-route performance contract implemented; drill-down/full workbench incomplete |

Only `universe_family_calibration` and `operator_learning_bundle` are currently
`measurement_supported`. Adding another path requires its own causal unit, outcome adapter,
behavior contract, failure semantics, focused tests and a new from-zero observation window; changing
only a registry flag is forbidden.

### 1.3 Current-path caveats

Existing tests prove individual behaviors and safety tripwires; they do not prove that the full
loop contributes positively. Current behavior has these contract-relevant limitations:

- factor, ranker, learned-guard, automatic Risk-lesson, Observer-bundle and persona hooks remain
  behaviorally static because they are measurement-unsupported; they create no formal exposure;
- factor identity is standardized as `factor_key` through producer, ranker and Operator context,
  but its unsupported experiment hook emits no before/after exposure;
- the only scheduled contribution evaluator measures the two supported paths through their
  path-specific cohort adapters. The Universe adapter uses
  `filter_candidate_outcomes` as the canonical full-arm source and
  `universe_forward_outcomes` only as a selected-member legacy mirror/fallback; conflicting
  economic copies invalidate the observation instead of choosing one;
- universe assignment is stable-randomized at the persisted `assignment_unit_key` cluster, not
  independently per surviving member. One complete cluster contributes one observation using
  intent-to-treat allocation utility: selected members contribute net return and dropped members
  contribute zero, with worst selected-member MAE. The immutable `allocation_selected` fact is
  required. Incomplete clusters contribute no survivor-only sample, and legacy exposures without a
  stored assignment unit remain auditable but excluded. Production candidate identity includes
  scanner-run plus feature-snapshot identity so change-suppressed features cannot merge distinct
  entry-price windows;
- contribution metric version
  `universe_family_calibration_cluster_allocation_utility_v3` applies only to new verdicts. Earlier
  metric verdict rows remain immutable history. A verdict's `created_at` is evaluation time, while
  `window_start` and `window_end` remain observation bounds;
- harm is determinate only after both arms reach the experiment's 50-assignment-unit gate. Before
  that point the persisted state is `indeterminate`: it blocks authority but does not claim a
  breach. Determinate loss-rate harm is an absolute increase above `0.15` (15 percentage points),
  and MAE p95 worsening is an absolute percentage-point difference above `0.25pp`; MAE fields use
  the `_pp` suffix rather than the ambiguous `_pct` suffix;
- lifecycle, authority, and owner-facing progress consume only the exact current hypothesis, stage,
  policy epoch, horizon, outcome contract, metric version, and deterministic behavior-contract
  cohort. `code_commit` remains provenance and does not fragment behaviorally identical evidence.
  Context-stale negative/invalid/determinate-harm history for the same experiment and hypothesis
  may conservatively refreeze or invalidate, but can never grant authority. Stage/hypothesis
  mismatch or malformed identity remains wrong-subject evidence and cannot mutate the current
  experiment;
- experiment registration is not an observation start. The first durable exposure atomically and
  idempotently changes an open experiment from `registered` to `active`, and `started_at` is always
  repaired to the earliest durable exposure;
- the evaluator persists positive/neutral/negative/insufficient/invalid measurement verdicts and
  never mutates authority itself. A protected owner admission plus the separate lifecycle/authority
  gates are required before a paper treatment can apply;
- automatic lesson `block_*` suggestions are rewritten as non-blocking `shadow-only` guard
  candidates, never coerced into Risk Engine category `risk`, and excluded from historical
  auto-promotion until prospective evaluation or explicit owner action;
- memo guidance reaches ordinary advisory consumers. Only the Operator bundle currently creates a
  measured exposure; Observer and ranker treatment hooks remain measurement-unsupported;
- reflection now fails closed when proposal/opportunity causal ids or eligible outcomes are absent;
- narrative review, memo, reasoning, calibration, lesson, and guard artifacts use different evidence
  units and confidence conventions;
- position-only historical guard replays cannot see successful blocks; Phase 0 now augments guard
  effectiveness with cost-aware `filter_candidate_outcomes` for actually blocked candidates;
- applied artifact counts and retrieval visibility show activity/influence, not contribution;
- overlapping Operator prompt channels now persist one bundle manifest; ordinary observer memory is
  a separate upstream manifest, not a measured exposure, so the two stages cannot masquerade as one
  influence unit.
- learned trading guards and the advisory trust guard chain have different authority and must not
  be merged or reported as the same mechanism.
- operator-loop safety/config changes can alter the same candidates/outcomes during a Learning
  evaluation; current Universe and Operator-bundle exposures rotate durable policy epochs. Any
  future measured path must do the same. These B-class behavior changes require a
  new policy epoch and a from-zero observation window before owner enablement; old-window evidence
  cannot authorize the new behavior. Safety tightening must never be withheld merely to make a
  cleaner experiment.
- every measurement-supported path declares a versioned behavior contract. The secret-free
  `behavior_contract_hash` combines that version with policy epoch, horizon, outcome contract and
  metric/statistical-unit version. A behavior version change starts a new from-zero cohort even on
  the same commit; an unrelated commit does not. Historical rows without a behavior hash remain
  auditable but cannot pool with or authorize a current cohort.
- historical experiments/exposures created before measurement-support enforcement remain immutable
  and visible for audit, but cannot produce a current verdict or authority.
- the Phase 3 harm contract is behavior version v2 for both measurement-supported paths. Its metric
  and path-version change rotates the policy epoch and starts a fresh from-zero window.
- canonical/mirror disagreement remains whole-cohort `invalid`; no conflicting sample is silently
  excluded. Verdict metadata preserves every conflict fingerprint and up to 20 exact provenance
  records, each bounded to 20 rows, containing source table, row id, timestamps, cost/source
  identity and differing economic field values. Complete conflict/row counts and explicit
  truncation flags remain visible.
- commit provenance for a behavior cohort is a sorted source set plus an explicit unknown-source
  count/completeness flag. The scalar verdict `code_commit` is populated only when every exposure
  has the same known commit; mixed known/NULL provenance must remain visibly incomplete.
- policy epoch ids remain explicit observation-window boundaries. The behavior hash uses the
  epoch's deterministic effective-values hash for reproducibility, while a later return to the
  same effective policy still starts a fresh epoch and does not pool across the intervening
  B-class policy window.
- Operator prompt-bundle exposure is created only after single-candidate scoping. It uses a stable
  candidate identity and the exact Opportunity 4h outcome key; a mixed queue returns the static
  prompt without creating an unlinked exposure. Candidate-context contract changes are included in
  the Operator Learning policy epoch so pre-change and post-change contribution evidence cannot pool.
- the scheduled lifecycle controller cannot admit a shadow experiment to `paper_10`; it can only
  recommend an owner admission. The public `admit_shadow_trial` API remains fail
  closed even when passed the string `owner`; the separately scoped protected
  settings route requires explicit confirmation, server-derived current epoch
  identity and every default-closed paper gate. Lifecycle automation and paper
  influence remain off by default.
- the bounded `lco-v1` owner projection exposes current measurement, cohort
  integrity and sanitized job health on the default Learning tab. Legacy
  artifact/funnel counts remain activity-oriented and cannot establish
  contribution.

The shared measured lifecycle and static fallback are current for the two measurement-supported
paths. Broader path expansion, overlap arbitration, artifact demotion and skill/filter evolution
remain proposed in [`learning_evolution.md`](../proposals/learning_evolution.md); they are not
normative current behavior.

### 1.4 Phase 0 measurement-integrity baseline

The first target slice is implemented without changing trading behavior:

- `learning/registry.py` declares current Learning and adjacent paths, authority ceilings,
  overlap groups, outcome sources, and static fallbacks;
- `trust/behavior_authority.py` is the final, small consumer-side inventory for
  paper-candidate blocking; declared Learning paths still have no effect unless that
  behavior gate admits the exact source and prospective path authority;
- `learning/evidence.py` gives narrative and diagnostic artifacts zero quantitative sample weight;
- `learning/outcomes.py` provides a shared fail-closed adapter for genuine realized positions and
  settled cost-aware candidate outcomes; consumer migration is intentionally partial;
- `learning/contribution.py` deduplicates repeated outcome identity, flags overlapping channel
  credit as not identifiable, and requests refreeze when eligible evidence coverage is lost;
- Learning alpha reports raw artifact events separately from unique cohorts/outcomes, so duplicate
  lessons no longer manufacture sample confidence;
- guard effectiveness includes successfully blocked candidates from fixed-horizon net outcomes;
- `learning/policy_epochs.py` defines and persists deterministic, secret-free concurrent-policy
  windows for the current measured paths;
- alpha read models distinguish directional evidence from measured prospective contribution.

This historical Phase 0 slice improved measurement honesty only. Later slices completed measured
Universe and Operator-bundle exposures. Ranker, sizing, Observer-bundle, learned-guard and automatic
Risk-lesson experiment hooks still return their static fallback because they are not measurement
supported. No Learning path grants Execution, exchange or live authority.

---

## 2. LessonStore

Source: `aletheia/learning/lessons.py`.
Table: `ps_lessons`.

### 2.1 Fields

| Field | Meaning |
| --- | --- |
| `lesson_id` | Stable lesson id |
| `source_type` / `source_id` | Producer and source artifact |
| `category` | `general`, `risk`, `entry`, `exit`, etc. |
| `severity` | `info`, `warning`, `critical` |
| `summary` | Human-readable lesson summary |
| `evidence_json` | Structured evidence, preferably quantitative |
| `recommendation_json` | Suggested action, such as `block_symbol` |
| `status` | Lesson lifecycle state |
| `applied_policy_version` | Policy version this lesson influenced |
| `activated_at` | Time accepted by reviewer/manual action |
| `dry_run_until` | 24h advisory-only safety window |
| `expires_at` | 30d TTL; expired lessons are ignored by consumers |

### 2.2 Lifecycle

```text
proposed --accept()--> accepted --mark_applied(version)--> applied
    |                    |
    |                    +--reject()--> rejected
    +--reject()--> rejected
    +--archive()--> archived
```

Important semantics:

- `proposed` is raw candidate output.
- `accepted` is a short-lived reviewed state.
- `applied` is the stable consumed state.
- `lesson_reviewer` normally calls `accept()` and then `mark_applied()` in the
  same run, so consumers must read `status="applied"`, not `accepted`.
- `archive()` is a soft lifecycle action. Hard deletion is not part of the
  Learning contract.

### 2.3 Producers

Current producers:

| Producer | Output |
| --- | --- |
| `dreaming_agent` | Proposed `ps_lessons` from `trade_case_study` evidence |
| Manual/API route | Human/operator lesson lifecycle actions |

### 2.4 Consumers

Current consumers:

| Consumer | Reads |
| --- | --- |
| `agent_shell/trading_context.py` Layer 3 | `LessonStore().list_all(status="applied", limit=5)` for System Operator prompt context |
| `risk/engine.py::_check_lesson_constraints` | `status="applied"`, non-expired lessons after dry-run. `manual`/`owner`/`human` sources remain static constraints; automatic sources propose an advisory block and require exact `risk_lesson_constraint` treatment authority linked to `proposal:<id>:4h`. Adapter errors fail closed as `lesson_check_error:internal_failure`. |
| `lesson_reviewer` | `proposed` for promotion and `accepted` for overturn checks |
| `doctor` / analytics | Counts and status visibility |

`reduce_size` recommendations remain advisory-only inside risk checks. Changing
them into hard sizing constraints touches a trust boundary and requires explicit
owner approval.

---

## 3. SkillStore

Source: `aletheia/learning/skills.py`.
Table: `ps_skills`.

### 3.1 Lifecycle

```text
draft --activate()--> active --deprecate()--> deprecated
```

### 3.2 Draft Producer

`lesson_reviewer` calls `SkillStore.create_from_lesson()` after a lesson is
marked `applied`.

The producer is intentionally conservative:

- It creates `status="draft"` only.
- It is idempotent by `lesson_id` (`name="lesson:<lesson_id>"`).
- It uses `scope="learning_advisory"`.
- It can output advisory prompt/ranking context.
- It does not call `activate()`.

### 3.3 Forbidden Layers

`SkillStore.create()` rejects any allowed layer that intersects:

```python
FORBIDDEN_LAYERS = {
    "risk_limits",
    "execution_gateway",
    "credentials",
    "live_mode",
    "order_submission",
}
```

This is a trust invariant. A skill candidate cannot grant itself permission to
change risk limits, touch execution, access secrets, unlock live mode, or submit
orders.

`Skill -> PolicyVersion` is still reserved. Draft skill activation and skill-led
policy versioning require a future human-reviewed workflow.

---

## 4. Knowledge Corpus And FTS Path

Learning also uses the Knowledge subsystem. See
`docs/design/contracts/knowledge_contract.md` for the complete item-type contract.

Relevant Learning item types:

| Type | Role |
| --- | --- |
| `trade_case_study` | Structured closed-trade case, used by FTS retrieval and System Operator context |
| `post_trade_review` | Post-trade reflection, lower-level review material |
| `trading_memo` | Recent tactical memo from Insight Synthesizer |
| `reasoning_feedback` | Prediction/reasoning quality feedback |
| `guard_candidate` | Candidate trading guard awaiting evidence/manual review |
| `trading_guard` | Active learned guard |
| `calibration_report` | Confidence calibration output |

High-confidence active knowledge is indexed into FTS and can be retrieved through
`retrieve_lessons()`. This path is separate from `ps_lessons`.

---

## 5. Two Artifact-Retrieval Paths

Knowledge/lesson retrieval has two intentionally distinct paths. They are not the whole
Learning subsystem; factor feedback, memo scoring, prompt feedback, learned guards, sizing,
reflection, shadow outcomes, and contribution analytics are additional paths listed in §1.1.

### Path A: FTS Advisory Retrieval

```text
knowledge_items high_confidence + active
  -> knowledge_fts
  -> retrieve_lessons()
  -> advisory prompt/ranker context
```

Used by:

- `paper_trader_ranker`
- `market_observer`
- `knowledge/router.py` advisory context

Path A answers: "Which past knowledge is relevant to this decision?"

### Path B: Structured LessonStore

```text
ps_lessons proposed
  -> lesson_reviewer
  -> ps_lessons applied
  -> System Operator prompt + risk lesson constraints
```

Path B answers: "Which reviewed structured lessons are active constraints or
operator guidance?"

Do not merge these paths casually. FTS results do not carry guard timestamps,
review status, or structured recommendation fields. `ps_lessons` rows do.

---

## 6. Memo Evolution And Time Decay

Insight Synthesizer writes `trading_memo` items. The latest memo can currently influence three
different stages: market-observer context, paper-ranker score, and System Operator context.

Current safeguards:

- `evaluate_prior_memo()` can overturn/downweight prior memo judgments after
  later trades contradict them.
- `memo_half_life_weight()` applies a 7-day half-life to memo guidance.
- `render_memo_for_observer()` displays `Memo time weight` and time-weighted
  regime playbook win-rate edges.

This implements the discussion requirement that newer evidence can repair or
reduce the influence of older rules instead of letting old memories remain
equally authoritative forever.

Time decay and contradiction handling limit stale influence; they do not
establish causal positive contribution. Separate observer/ranker/operator
exposures are proposed in
[`learning_evolution.md`](../proposals/learning_evolution.md); they are not
current Contract behavior.

---

## 7. Guard Candidates And Promotion

`guard_candidate` entries are candidate defensive rules. They may become
`trading_guard` entries through owner review:

1. **Manual review**: dashboard/API promotion through
   `POST /knowledge/guard-candidates/{guard_id}/promote`. A shadow learned candidate
   becomes active observation; this storage transition does not grant block or
   score-penalty authority.

`config.guard.auto_promote_enabled` is a legacy setting. The scheduled job cannot
promote or unshadow normal auto-generated candidates, and manual/seed guards are
excluded from that job. Historical match counts alone never grant authority.

Promotion must preserve auditability:

- The active guard is created as `trading_guard`.
- The source candidate is deprecated, not hard-deleted.
- A `TRADING_GUARD_PROMOTED` event is written.

The current registry marks `ranker_learned_guard` measurement-unsupported, so
learned guards remain observe-only and create no formal experiment authority.
Prospective blocked-candidate outcomes, supply-harm checks, continual holdout, and
automatic demotion remain part of the
[`learning_evolution.md`](../proposals/learning_evolution.md) design, not current
Contract behavior.

Ranker enforcement keeps two authority classes separate:

- shipped seed guards and owner-authored manual guards follow `guard.enabled` and
  `guard.enforce_in_paper_ranker`; disabling autonomous Learning or paper Learning influence does
  not disable these static safety rules;
- learned guards are stored observe-only. Automatic creation preserves their explicit proposed
  block and score penalty in separate metadata instead of erasing it;
- matching Opportunities call the registered `ranker_learned_guard` adapter with baseline `0`,
  but its current measurement-unsupported registry state returns the static baseline before any
  hypothesis, experiment, exposure, or assignment is created;
- a future path-specific program would need complete prospective measurement and explicit owner
  admission before an exact treatment could restore the preserved proposed block/penalty. Such a
  treatment could never grant Risk, Execution, exchange, or live authority.

---

## 8. UI And API Surface

The Learning UI is expected to surface real backend state, not invented healthy
states.

### 8.1 Current shipped surface

Current Learning UI surfaces:

- Knowledge base item types
- Post-trade lessons with `accept` / `reject` / `archive`
- Guard candidates with manual promote
- Active guards
- Learning funnel counts
- Conservative contradiction count requiring human review
- Registry-backed paper-influence path checkboxes. Only paths with a real consumer and prospective
  authority adapter are selectable; declared-but-unwired paths remain visible and disabled.
  `advisory_skill` is currently `consumer_unwired` and cannot be saved through Settings.

API endpoints are indexed in `docs/design/api/api_reference.md`.

The current surface does **not** expose the measured A1 cluster, provenance, metric-history,
current-cohort identity, evaluator recovery, catch-up, or lifecycle-recommendation facts. Portfolio
PnL, knowledge counts and `/analytics/learning-funnel` are activity/context only. A request failure
must not be rendered as a genuine empty list or zero.

### 8.2 Target owner projection

The proposed target is
[`learning_observability.md`](../proposals/learning_observability.md), feature id
`learning_contribution_observability`, projection schema `lco-v1`. It defines:

- current versus historical measurement identity;
- complete-cluster membership, selected/dropped members, exclusions and invalid reasons;
- canonical/fallback/mirror/disagreement provenance;
- current and durable-last-failure evaluator state plus measurement catch-up;
- read-only lifecycle recommendations with `mutation_available=false`;
- bounded cached GET endpoints and an honest responsive Learning workbench.

That Proposal adds no admission mutation, paper influence, lifecycle automation, schema, config,
scheduler job, Risk, Execution, exchange or live authority. Its `LCO-*` claims remain `missing`
until implementation tests turn them GREEN.

---

## 9. Trust Invariants

Learning must not:

- unlock live trading;
- submit orders directly;
- edit credentials;
- change hard risk limits;
- bypass the Execution Gateway;
- hard-delete knowledge or causal records to make reports look clean.

Learning may:

- create research, case studies, memos, lessons, guard candidates, and draft
  skills;
- feed advisory context into prompts;
- adjust ranking/guarding through reviewed or evidence-gated artifacts;
- propose changes that a human or controlled workflow later reviews.

---

## 10. Spec Coverage

Primary executable claims:

- `tests/specs/spec_learning_pipeline.py`
- `tests/specs/spec_lesson_guards.py`
- `tests/specs/spec_insight_feedback_loop.py`
- `tests/specs/spec_knowledge_fts.py`
- `tests/specs/spec_factor_feedback.py`
- `tests/specs/spec_filter_evidence.py`
- `tests/specs/spec_opportunity_outcomes.py`
- `tests/specs/spec_persona_sizing.py`
- `tests/specs/spec_pi_decision_chain.py`
- `tests/specs/spec_learning_registry.py`
- `tests/specs/spec_learning_attribution.py`
- `tests/specs/spec_learning_ledger.py`
- `tests/specs/spec_learning_contribution.py`
- `tests/specs/spec_learning_authority.py`
- `tests/specs/spec_learning_lifecycle.py`
- `tests/specs/spec_learning_continuity.py`
- `tests/specs/spec_learning_verdict_runtime.py`
- `tests/specs/spec_learning_final_consumers.py`
- `tests/specs/spec_learning_observability.py` (`LCO-*`, target/missing)
- `tests/e2e/test_pipeline_feedback_loop.py`
- `tests/e2e/test_pipeline_guard_promotion.py`
- `tests/e2e/test_learning_paper_lifecycle.py`

The isolated Paper Learning lifecycle E2E uses the real Universe score consumer, canonical full-arm
registration seam, cost-aware settler, contribution evaluator, protected owner admission route,
unfiltered 10% paper assignment stream, v3 cluster aggregation with selected and dropped members,
harm evaluator, lifecycle refreeze and static fallback. A shared deterministic test clock advances
event time without sleeping; only external market
observation I/O is replaced. The test proves production timestamps remain causally ordered across a
real seven-day evidence window and that no proposal, risk decision, order, fill, position or paper
account event is created. The adjacent Universe pipeline spec locks the service-to-registration
wiring with the full scored arm and selected subset. This is separate from and does not modify the
frozen active-mainline Golden Path. It proves causal plumbing and safety fallback, not positive
contribution, profitability, Live readiness or admission eligibility for the production
qualification cohort.

Important tripwires:

- `LP-DUAL-01`: System Operator structured lesson prompt must read from
  `LessonStore`, not `retrieve_lessons()`.
- `LP-P3-01`: lesson reviewer may create draft skills but must not activate them
  or grant forbidden layers.
- `KL-B01` / `IFL-B06`: trading memo half-life time decay must exist and render
  visibly into prompt context.
- `KL-S02`: risk engine must not grow a learning override interface without
  change-control review.

These current claims remain valid regression coverage. They must not be represented as proof of
the target `LE-*` positive-contribution claims, the missing `LCO-*` owner projection, or
profitability.

## 11. Outcome Integrity Handoff

`aletheia/learning/outcomes.py` now composes the dependency-neutral
`aletheia/analytics/outcome_integrity.py` assessment and then applies Learning's clean-window rules.
The global classifier/read model, behavior-consumer migration, owner-facing dual reporting and
versioned clean-window gate are delivered. Risk account loss remains deliberately separate from
strategy performance.

The future design is `../proposals/learning_evolution.md §14`; current executable evidence includes
`tests/specs/spec_outcome_integrity.py`. OI-C01 is now GREEN: an active persisted incident is read by
the scheduler gate, experiment authority, Operator prompt bundle, market-observer memory, learned
ranker guards, persona sizing/unlock and persona rollout. Read-only settlement and shadow outcome
measurement remain available. OI-C02 and OI-C05 are now GREEN, so:

- old or wrong-window calibration/reports are rejected and can be archived by the dry-run-first
  remediation tool;
- “Learning is healthy/effective” cannot be inferred from raw closed-position statistics;
- existing consumers protected by the central model must retain that protection.

This handoff does not authorize Learning to import Risk, Execution, exchange clients, or live routes.
Risk account loss remains a separate controlled contract, not a Learning outcome.

The incident gate does not disable owner/manual/human Risk lessons, because those are static safety
rules rather than Learning treatments. An automatic lesson has no blocking effect during a global
refreeze unless the exact current prospective path is independently authorized; unavailable or
errored authority remains advisory/fail-closed. Risk account-loss semantics remain separately
controlled by OI-C03.
