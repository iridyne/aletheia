# Learning Subsystem Contract

> Contract level: L2. This file is normative for the Learning subsystem.
> Last aligned: 2026-07-12 Learning authority hardening.
> Reference intent: root `discussion.md` Learning section.
> Target extension: `learning_evolution_contract.md` defines the measured
> self-improvement lifecycle and tracks each implemented/remaining claim.

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
| Candidate counterfactuals | `analytics/shadow_trades.py`, `filter_evidence.py`, `universe/filter_outcome_recorder.py` | settle skipped/blocked candidate outcomes |
| Outcome economics | `learning/outcomes.py`, `universe/outcome_economics.py`, `analytics/outcome_semantics.py`, `close_reasons.py`, `learning_window.py` | shared fail-closed cost/quality contract governs factor, universe, reviewer, ranker and sizing exposure authority; prompt-bundle treatment settlement remains incomplete |
| Universe family calibration | `universe/allocator.py::get_family_multipliers`, `learning/experiments.py`, `universe/service.py` | 50-sample retrospective cohorts may propose a bounded 0.9/1.1 shadow treatment; actual paper influence requires stable prospective arms, linked net outcomes, minimum time, and a positive verdict; fallback is 1.0 |
| Structured cases | `analytics/trade_case_builder.py` | deterministic high-confidence cases and guard candidates |
| Universe guard producer | `universe/outcome_recorder.py` | derives candidate guards from settled rejected/pass outcomes |
| Narrative review | `agents/post_trade_reviewer.py` | medium-confidence prose; also triggers deterministic case building |
| Risk critique | `agents/risk_critic.py` | advisory research note from proposal/risk-decision history |
| Lesson extraction/review | `agents/dreaming_agent.py`, `agents/lesson_reviewer.py` | promotion/overturn requires clean cost-aware outcomes matching every populated symbol/direction/scanner dimension; global history is context only |
| Memo synthesis | `analytics/insight_synthesizer.py`, `memo_effectiveness.py` | memo reaches observer prompt, ranker score, and Operator prompt |
| Factor feedback | `analytics/factor_feedback.py`, `learning/experiments.py` | exact clean history is diagnostic and proposes shadow treatments; ranker score/filter authority requires a prospective positive verdict and otherwise stays neutral |
| Reasoning feedback | `analytics/reasoning_feedback.py` | Operator prompt advice |
| Confidence calibration | `analytics/confidence_calibration.py` | Operator prompt advice |
| Last-decision reflection | `agent_shell/reflection.py` | Operator prompt advice; only exact proposal→position or opportunity→fixed-horizon/shadow outcomes are rendered |
| Learned guards | `knowledge/guards.py`, `analytics/guard_promotion.py` | active paper-entry candidate blocks |
| Trust guard chain | `trust/guard_chain.py` | pre-risk observability only; even a `block` verdict does not block execution |
| Case/memo score | `agents/paper_trader_ranker.py`, `learning/experiments.py` | retrospective case/memo values only propose bounded score deltas; static zero is used until a prospective paper verdict is authorized, and each candidate carries the two separate exposure traces |
| Advisory retrieval | `knowledge/router.py`, `knowledge/retrieval.py` | market-observer and decision context |
| Persona learning | `trading/persona.py`, `persona_rollout.py`, `sizing_policy.py`, `trading/sizing.py` | clean realized history proposes a bounded notional; static, learned and actual values are recorded and actual stays static without prospective authority; risk caps remain downstream and unchanged |
| Exit diagnostics | `analytics/exit_policy_advisor.py` | read-only advice only; no automatic mutation |
| Filter diagnostics | `analytics/filter_registry.py`, `filter_replay.py`, `filter_controller.py`, `filter_shadow_experiments.py` | shadow recommendations/observe-only experiments; no production mutation |
| Contribution/health | alpha, memo, guard impact/effectiveness, learning-funnel analytics, doctor, scheduler | reporting and operational control with different evidence coverage |
| Supporting audits | trade insights/stats, family scorecard, opportunity flow, debate impact, thesis lineage, go-live scorecard | read models/gates; some are outcome sources or concurrent controls, not Learning treatments |
| Operator control loop | `agent_shell/operator_loop.py` | may tighten confidence, size/risk cap, stop or scanner settings under its own safety contract; loosening requires higher authority |

### 1.2 Current-path caveats

Existing tests prove individual behaviors and safety tripwires; they do not prove that the full
loop contributes positively. Current behavior has these contract-relevant limitations:

- factor and universe authority are now refrozen on existing retrospective data; they will remain
  behaviorally neutral until sufficient prospective treatment/control outcomes exist;
- factor identity is standardized as `factor_key` through producer, ranker, Operator context and
  prompt rendering, with immutable before/after exposure records;
- positive/negative experiment verdict calculation is still a separate evaluator responsibility;
  the authority adapter independently verifies real linked samples and fails closed if a verdict
  is absent, stale, under-sampled, or contradicted by a newer refreeze verdict;
- automatic lesson `block_*` suggestions are rewritten as non-blocking `shadow-only` guard
  candidates, never coerced into Risk Engine category `risk`, and excluded from historical
  auto-promotion until prospective evaluation or explicit owner action;
- memo guidance reaches three stages; observer and Operator persist separate bundle manifests,
  while the ranker now uses its own `ranker_memo_score` treatment/exposure instead of inheriting
  observer credit;
- reflection now fails closed when proposal/opportunity causal ids or eligible outcomes are absent;
- narrative review, memo, reasoning, calibration, lesson, and guard artifacts use different evidence
  units and confidence conventions;
- position-only historical guard replays cannot see successful blocks; Phase 0 now augments guard
  effectiveness with cost-aware `filter_candidate_outcomes` for actually blocked candidates;
- applied artifact counts and retrieval visibility show activity/influence, not contribution;
- overlapping Operator prompt channels now persist one bundle manifest; observer memory is a
  separate manifest on the causal Opportunity, so the two stages cannot masquerade as one exposure.
- learned trading guards and the advisory trust guard chain have different authority and must not
  be merged or reported as the same mechanism.
- operator-loop safety/config changes can alter the same candidates/outcomes during a Learning
  evaluation; factor, universe, ranker, and sizing exposures rotate durable policy epochs, while
  prompt-bundle treatment exposure still needs migration. Safety tightening must never be
  withheld merely to make a cleaner experiment.

The target lifecycle, registry, prospective experiments, overlap handling, and automatic fallback
are normative in `learning_evolution_contract.md`. Phase 0 measurement claims are tracked below;
behavior-changing claims remain unavailable until their executable status turns GREEN.

### 1.3 Phase 0 measurement-integrity baseline

The first target slice is implemented without changing trading behavior:

- `learning/registry.py` declares current Learning and adjacent paths, authority ceilings,
  overlap groups, outcome sources, and static fallbacks;
- `learning/evidence.py` gives narrative and diagnostic artifacts zero quantitative sample weight;
- `learning/outcomes.py` provides a shared fail-closed adapter for genuine realized positions and
  settled cost-aware candidate outcomes; consumer migration is intentionally partial;
- `learning/contribution.py` deduplicates repeated outcome identity, flags overlapping channel
  credit as not identifiable, and requests refreeze when eligible evidence coverage is lost;
- Learning alpha reports raw artifact events separately from unique cohorts/outcomes, so duplicate
  lessons no longer manufacture sample confidence;
- guard effectiveness includes successfully blocked candidates from fixed-horizon net outcomes;
- `learning/policy_epochs.py` defines deterministic, secret-free concurrent-policy semantics;
  persistence and operator/consumer integration remain future work;
- alpha read models distinguish directional evidence from measured prospective contribution.

This historical Phase 0 slice improved measurement honesty only. Later implemented slices now
create refrozen paper exposures for ranker and sizing proposals, but still do not modify any
risk/execution/live authority without the separate prospective gates described above.

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
| `risk/engine.py::_check_lesson_constraints` | `status="applied"` lessons; enforces non-expired block actions after dry-run |
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
`docs/design/knowledge_contract.md` for the complete item-type contract.

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

Time decay and contradiction handling limit stale influence; they do not establish causal
positive contribution. The target contract requires separate observer/ranker/operator exposures
and prevents the same later trade from being counted as independent proof for all three.

---

## 7. Guard Candidates And Promotion

`guard_candidate` entries are candidate defensive rules. They may become
`trading_guard` entries through two paths:

1. **Manual review**: dashboard/API promotion through
   `POST /knowledge/guard-candidates/{guard_id}/promote`.
2. **Configured auto-promotion**: scheduled `guard_promotion` job, gated by
   `config.guard.auto_promote_enabled`, promotes candidates only after evidence
   thresholds are met.

Promotion must preserve auditability:

- The active guard is created as `trading_guard`.
- The source candidate is deprecated, not hard-deleted.
- A `TRADING_GUARD_PROMOTED` event is written.

The present promotion gate is evidence-gated but retrospective. Under the target contract,
historical evidence may nominate a shadow experiment only; active authority requires prospective
blocked-candidate outcomes, supply-harm checks, continual holdout, and automatic demotion.

---

## 8. UI And API Surface

The Learning UI is expected to surface real backend state, not invented healthy
states.

Current Learning UI surfaces:

- Knowledge base item types
- Post-trade lessons with `accept` / `reject` / `archive`
- Guard candidates with manual promote
- Active guards
- Learning funnel counts
- Conservative contradiction count requiring human review

API endpoints are indexed in `docs/design/api_reference.md`.

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
- `tests/e2e/test_pipeline_feedback_loop.py`
- `tests/e2e/test_pipeline_guard_promotion.py`

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
the target `LE-*` positive-contribution claims or of profitability.

## 11. Outcome Integrity Handoff

`aletheia/learning/outcomes.py` now composes the dependency-neutral
`aletheia/analytics/outcome_integrity.py` assessment and then applies Learning's clean-window rules.
The global classifier/read model is delivered, but consumer migration remains incomplete: several
Operator, calibration, alpha, Risk and performance paths still read closed positions independently.

The target contract is `learning_evolution_contract.md §14` plus
`tests/specs/spec_outcome_integrity.py`. Until OI-C01/OI-C02/OI-C05 are GREEN:

- a reconciler-thrash incident is not proven to refreeze every Learning consumer;
- an old calibration/report may still be active after its underlying outcome semantics became stale;
- “Learning is healthy/effective” cannot be inferred from raw closed-position statistics;
- existing consumers already protected by `assess_realized_position()` must retain that protection,
  but their local adapter must eventually compose the neutral central outcome model.

This handoff does not authorize Learning to import Risk, Execution, exchange clients, or live routes.
Risk account loss remains a separate controlled contract, not a Learning outcome.
