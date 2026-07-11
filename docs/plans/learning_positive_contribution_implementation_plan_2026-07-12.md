# Learning Positive-Contribution Implementation Plan

> Status: approved design target; implementation not started.
> Normative target: `docs/design/learning_evolution_contract.md`.
> Existing behavior contract: `docs/design/learning_contract.md`.
> Supersedes: `docs/plans/LEARNING_MODULE_REPAIR_PLAN.md` for future work.
> Product stage: MVP trust hardening; no new exchange, database service, vector store,
> reinforcement-learning framework, or live authority.

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

## 3. Target Code Architecture

### 3.1 New Learning Core

```text
aletheia/learning/
  models.py              Typed hypothesis/exposure/experiment/verdict models
  hypotheses.py          Fingerprint, dedup, lifecycle transitions
  evidence.py            Clean/scope-complete evidence eligibility
  experiments.py         Stable assignment and treatment/control manifests
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
| Paper ranker | calculate baseline and learned score; persist components/assignment | `agents/paper_trader_ranker.py` |
| Filter evidence | register treatment/control outcomes for all candidates | `analytics/filter_evidence.py` |
| System Operator | build Learning manifest and persist it with decision | high-sensitivity decision chain |
| Guards | link active guard to hypothesis/experiment and settle blocked shadows | knowledge/ranker path |
| Sizing | persist baseline and learned notional; one-param trial | trading sizing path |
| Scheduler | durable expected-run/catch-up and universal enable gate | runtime scheduler |
| Alpha audit | consume exposures/outcomes, distinct by cohort/candidate | read-only analytics |
| API/UI | contribution, lifecycle, experiment, and health read models | cached read endpoints |

### 3.3 Dependency Direction

```text
storage/data outcomes
       ↓
learning core (pure policy/evidence decisions)
       ↓
consumer adapters (ranker / prompt / guard / sizing)
       ↓
existing risk and execution gates
```

Risk and execution do not call a generic Learning override. The current lesson hard-block
path is migrated to the evidence-gated trading-guard path; removing or changing that path
is a separate change-controlled batch with focused Risk Engine tests.

## 4. Data Model And Migration

### 4.1 New Tables

#### `learning_hypotheses`

```sql
hypothesis_id TEXT PRIMARY KEY
fingerprint TEXT NOT NULL UNIQUE
cohort_id TEXT NOT NULL
source_type TEXT NOT NULL
source_artifact_ids_json TEXT NOT NULL
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

#### `learning_exposures`

```sql
exposure_id TEXT PRIMARY KEY
dedup_key TEXT NOT NULL UNIQUE
experiment_id TEXT NOT NULL
hypothesis_id TEXT NOT NULL
assignment TEXT NOT NULL
consumer TEXT NOT NULL
candidate_key TEXT
opportunity_id TEXT
proposal_id TEXT
decision_id TEXT
position_id TEXT
before_json TEXT NOT NULL
after_json TEXT NOT NULL
manifest_json TEXT NOT NULL
outcome_candidate_key TEXT
code_commit TEXT
policy_version TEXT
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
updated_at TEXT NOT NULL
```

### 4.2 Existing Table Extensions

- `ps_lessons`: add nullable `hypothesis_id`, `fingerprint`, `review_reason_json`.
- `ps_skills`: add nullable `hypothesis_id`, `activated_at`, `demoted_at`.
- `knowledge_items` guard/memo artifacts: store hypothesis/version in tags/metadata;
  no taxonomy change is required.
- System Operator decision persistence: add `learning_manifest_json` to the existing
  decision record that already owns the reasoning result. If no durable decision table
  can carry it without ambiguity, introduce `decision_learning_manifests` instead of
  hiding it in free-form rationale.

### 4.3 Migration Rules

1. Additive migrations only.
2. No historical artifact is hard-deleted.
3. Existing applied lessons are imported as `proposed` or `shadow`, never assumed positive.
4. Historical duplicates map to one fingerprint/cohort; duplicate rows remain archived/auditable.
5. Existing active guards enter `shadow_validation` until blocked-candidate outcome coverage
   proves or contradicts them; safety-approved static seed guards remain separately labelled.
6. Existing skill drafts remain drafts and receive hypothesis links only when provable.
7. Backfill is idempotent and emits counts, not runtime facts into Git documentation.

Schema addition requires updating `docs/design/data_model.md` and `spec_data_model.py` in the
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
  learned sizing together; read-only reports remain enabled.

## 7. Experiment Design By Learning Path

### 7.1 Ranker Lessons And Memos

Implementation:

1. split current score into named components;
2. calculate `baseline_score` without learned components;
3. calculate bounded `lesson_delta`, `memo_delta`, and `guard_delta` separately;
4. persist every component before candidate selection;
5. assign eligible candidates to treatment/control prospectively;
6. register all horizons through existing filter outcome settlement;
7. ranker selection continues to obey all non-Learning hard filters in both arms.

Initial bounds:

- per-hypothesis score delta: `[-10, +10]`;
- total learned advisory delta: `[-20, +20]`;
- a score bonus cannot make an execution-ineligible candidate eligible;
- negative contribution demotes the hypothesis to shadow immediately after gate evaluation.

### 7.2 Learned Guards

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

### 7.3 System Operator Lessons

Implementation:

1. build a compact typed manifest before prompt rendering;
2. stable treatment/control assignment determines whether Learning context is rendered;
3. persist manifest, token budget, evidence ids, and assignment;
4. preserve identical market, account, risk, and opportunity inputs between arms;
5. connect decision to executed result or same-cycle shadow result;
6. measure trade rate, direction, conviction calibration, and cost-aware outcome.

The experiment is paper-only. The control group withholds advisory Learning context, never
safety instructions or hard risk controls.

### 7.4 Persona/Sizing Learning

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

### 7.5 Advisory Skills

Skills become reusable wrappers around proven hypotheses:

- automatically activatable only for `prompt_context` and `ranking_advisory`;
- no arbitrary generated Python/tool capability;
- activation requires the source experiment's positive verdict;
- every use creates an exposure;
- negative source/skill holdout demotes the skill automatically;
- human activation cannot add a forbidden layer.

## 8. Contribution Engine

### 8.1 Aggregation

All metrics use distinct `(experiment_id, candidate_key, horizon)` or the equivalent
decision identity. Artifact count is never a weight.

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

Changes:

- deduplicate Learning alpha by cohort/candidate/horizon;
- label before/after evidence weak and remove repeated-artifact confidence inflation;
- add guard counterfactual outcomes to effectiveness analytics;
- split activity, influence, and contribution metrics;
- add `LE-B02`, `LE-B09`, `LE-C06` RED→GREEN tests;
- record a versioned baseline report in runtime analytics, not Git docs.

Authority change: none.

Exit gate: current behavior can be measured without duplicate samples and guard blocks are
visible through settled shadow outcomes.

### Phase 1 — Durable Hypothesis And Exposure Ledger

Goal: create causal identity before more learning is activated.

Changes:

- migrations and repositories for hypotheses/experiments/exposures/verdicts/job health;
- typed models and fingerprint logic;
- backfill existing lessons/guards/memos to non-active hypothesis states;
- update data model/retention/export/import documentation;
- add hypothesis/exposure read APIs.

Authority change: none.

Exit gate: repeated sources are idempotent and every future influence has a place to be recorded.

### Phase 2 — Producer Hygiene And Scheduler Continuity

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

### Phase 3 — Ranker And Guard Prospective Experiments

Goal: prove the already-active low-risk feedback paths.

Changes:

- ranker score component exposure logging;
- stable experiment assignment;
- blocked-guard shadow settlement;
- guard contribution and supply-harm gates;
- shadow→paper→active promotion/demotion state machine;
- active guard continual holdout.

Authority change: paper ranker/guard only, behind config defaults OFF.

Exit gate: an E2E harmful guard is automatically demoted and a beneficial guard promotes only
after unique prospective evidence.

### Phase 4 — System Operator Prompt Experiments

Goal: establish whether reviewed lessons change decisions profitably.

Changes:

- typed compact Learning manifest;
- treatment/control prompt rendering;
- persisted decision manifest and disagreement reason;
- same-cycle shadow linkage;
- contribution by lesson/hypothesis and prompt bundle.

Authority change: paper System Operator context only.

Exit gate: exact lesson influence and control comparison are queryable for every decision.

### Phase 5 — Sizing Shadow Learner

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

At every rung, negative/invalid verdict returns to the previous safe rung automatically.

## 12. SDD Delivery Matrix

Target claim definitions live in `learning_evolution_contract.md §12`.

Implementation mapping:

| Batch | Spec files | Claims |
| --- | --- | --- |
| Phase 0 | `spec_learning_contribution.py` | LE-B02, LE-B09, LE-C06 |
| Phase 1 | `spec_learning_evolution.py`, `spec_data_model.py` | LE-S01/02, LE-B01, LE-C05 |
| Phase 2 | `spec_learning_evolution.py`, `spec_agent_contracts.py` | LE-S03, LE-B03/04/11/12, LE-C01 |
| Phase 3 | `spec_learning_experiments.py`, `spec_filter_evidence.py` | LE-B05/06/08/09, LE-C02/03/04 |
| Phase 4 | `spec_pi_decision_chain.py`, `spec_learning_experiments.py` | LE-B07, LE-C04/07 |
| Phase 5 | `spec_persona_sizing.py`, `spec_learning_experiments.py` | LE-B10, LE-C02/03/07 |
| Phase 6 | `spec_skill_store.py`, `spec_learning_evolution.py` | LE-C02/05/07 |

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

Test design rules:

- isolated SQLite database and frozen clock;
- no real exchange, network, secrets, or live mode;
- deterministic fake LLM responses only where semantic review is under test;
- treatment and control share identical market inputs;
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
  tests/specs/spec_pi_decision_chain.py \
  tests/specs/spec_persona_sizing.py \
  tests/specs/spec_skill_store.py \
  tests/specs/spec_risk_engine.py -q

uv run pytest tests/test_architecture_fitness.py tests/specs/spec_trust_boundary.py -q
uv run pytest tests/specs/ -q
uv run pytest tests/ -q --tb=line
uv run aletheia doctor
uv run aletheia agent workflow check --strict
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

- normative claims LE-S01 through LE-C07 are GREEN;
- all 18 E2E scenarios pass against persisted state;
- ranker, prompt, guard, sizing, and skill paths each have an observable baseline fallback;
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
LE-00 measurement integrity
  -> LE-01 schema + hypothesis ledger
      -> LE-02 producer dedup + scheduler continuity
          -> LE-03 ranker exposures
          -> LE-04 guard shadow experiments
              -> LE-05 contribution/promotion engine
                  -> LE-06 prompt experiments
                  -> LE-07 sizing shadow learner
                      -> LE-08 advisory skills
                          -> LE-09 UI + controlled rollout audit
```

`LE-03` and `LE-04` may proceed in parallel after the ledger exists. Prompt and sizing trials
wait for the shared contribution engine so each path does not invent a different definition
of “positive”.
