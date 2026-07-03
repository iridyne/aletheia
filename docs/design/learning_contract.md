# Learning Subsystem Contract

> Contract level: L2. This file is normative for the Learning subsystem.
> Last aligned: 2026-07-03.
> Reference intent: root `discussion.md` Learning section.

Learning is not a dashboard-only feature. Its product role is to turn market
observations, trade decisions, closed trades, reviews, and later corrections into
machine-readable knowledge that can be reused by future market evaluation,
ranking, System Operator reasoning, and guarded trade approval.

Learning output must improve decision context without bypassing trust boundaries:
it must not edit credentials, unlock live trading, submit orders directly, or
change hard risk limits without explicit change control.

---

## 1. Current Contract

The current implementation has four connected layers:

1. **Knowledge corpus**: `knowledge_items` + `knowledge_fts`.
   High-confidence active Markdown-backed knowledge is searchable through FTS.
2. **Structured lessons**: `ps_lessons` via `LessonStore`.
   Lessons carry structured evidence and review state.
3. **Policy versions**: `ps_policy_versions` via `PolicyStore`.
   Accepted lessons are folded into a traceable policy version.
4. **Draft skills**: `ps_skills` via `SkillStore`.
   Applied lessons can create draft long-term skill candidates. They are not
   activated automatically.

The stable Learning chain is:

```text
closed trade / market evidence
  -> trade_case_study / trading_memo / reasoning_feedback / guard_candidate
  -> dreaming_agent extracts ps_lessons
  -> lesson_reviewer accepts/rejects proposed lessons
  -> accepted lessons are marked applied with a policy version
  -> applied lessons feed System Operator prompt and risk lesson checks
  -> applied lessons also create draft skill candidates
```

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

## 5. Dual Learning Paths

The subsystem intentionally has two paths.

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

Insight Synthesizer writes `trading_memo` items and renders them into System
Operator context.

Current safeguards:

- `evaluate_prior_memo()` can overturn/downweight prior memo judgments after
  later trades contradict them.
- `memo_half_life_weight()` applies a 7-day half-life to memo guidance.
- `render_memo_for_observer()` displays `Memo time weight` and time-weighted
  regime playbook win-rate edges.

This implements the discussion requirement that newer evidence can repair or
reduce the influence of older rules instead of letting old memories remain
equally authoritative forever.

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

Important tripwires:

- `LP-DUAL-01`: System Operator structured lesson prompt must read from
  `LessonStore`, not `retrieve_lessons()`.
- `LP-P3-01`: lesson reviewer may create draft skills but must not activate them
  or grant forbidden layers.
- `KL-B01` / `IFL-B06`: trading memo half-life time decay must exist and render
  visibly into prompt context.
- `KL-S02`: risk engine must not grow a learning override interface without
  change-control review.
