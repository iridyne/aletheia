---
id: learning-contribution-observability
status: accepted
related_contracts:
  - ../contracts/learning_contract.md
supersedes: []
---

# Learning Contribution Observability Proposal

> Proposal status: accepted design, partially implemented, not a support claim.
> Feature id: `learning_contribution_observability`.
> Projection schema: `lco-v1`.
> Executable claim namespace: `LCO-*` in
> `tests/specs/spec_learning_observability.py`.
> Status: partially implemented. The bounded, GET-only
> `/api/analytics/learning-contribution` owner summary, default Contribution tab,
> current-summary cache/`Server-Timing`, measured-path selector, and current-surface
> accessibility evidence are implemented. Dedicated drill-down APIs and the full
> Experiments/Operations/history/detail workbench remain targets.
> Historical sequencing alias: "Learning A2 Observability". Current normative
> documents must use the canonical name above because `A2` has unrelated meanings
> elsewhere in the repository.
> Last aligned with code: 2026-08-06. The executable registry contains implemented
> and partial claims; it has no strict-xfail placeholders. Full workbench delivery is
> tracked by GitHub issue #614 under the #470 Learning umbrella.

This proposal defines the intended owner-facing, read-only projection of the measured
Learning loop. Current behavior remains defined by
[`learning_contract.md`](../contracts/learning_contract.md); the broader measured
lifecycle is proposed in
[`learning_evolution.md`](learning_evolution.md). This proposal does not alter current measurement,
experiment, lifecycle, authority, or safety semantics.

## 1. Outcome And Boundary

The owner must be able to answer five questions without reading SQLite or source
code:

1. What Learning paths exist, and which can actually be measured?
2. What is the current identity-matched contribution verdict, with how much
   treatment/control evidence?
3. Which assignment clusters and outcome sources were included, excluded, or
   invalid?
4. Is the evaluator current, catching up, failed, or recovered from a prior
   failure?
5. What lifecycle action is recommended, and is any mutation actually available?

The feature is a projection, not a new control loop.

It may:

- read and aggregate existing Learning ledger and exact outcome facts;
- expose bounded, paginated history and drill-downs;
- classify current versus historical verdict identity;
- expose lifecycle recommendations and authority block reasons;
- cache immutable or slowly changing projections for owner-facing reads.

It must not:

- run the evaluator, settle an outcome, create an exposure, or process lifecycle;
- admit a trial, promote, demote, refreeze, archive, or mutate config;
- enable `paper_influence_enabled` or `lifecycle_automation_enabled`;
- add a scheduler job, table, data source, config field, or retention exception;
- import or call Risk, Execution, exchange, credential, permission, or live
  modules;
- imply positive contribution from artifacts, counts, PnL, activity, or job
  health.

No endpoint in this contract is a trust-boundary mutation. A future authenticated
owner admission route is separately scoped and is not part of this feature.

## 2. Current Implementation Truth

The following distinction is normative. A design document must not make a target
surface look shipped.

| Capability | Current fact | LCO target |
| --- | --- | --- |
| Measured ledger | Seven permanent Learning tables exist | Read-only typed projection |
| Measurable path | `universe_family_calibration` has a current causal cohort adapter; Operator remains consumer-wired but measurement-unsupported | Explicit `measurement_supported` per path |
| Current metric | `universe_family_calibration_cluster_allocation_utility_v5` | Current identity plus append-only metric history |
| Cohort inspection | Internal aggregate inspection exists | Bounded aggregate plus selected cluster detail |
| Lifecycle inspection | Internal read function returns progress, recommendation and job health | Public sanitized projection |
| Job health | Current and durable last-failure fields exist | Current, recovery and catch-up states remain separate |
| Public Learning API | Bounded additive `lco-v1` summary at `/analytics/learning-contribution`; no dedicated LCO detail/list family. Legacy Settings experiment list/detail GETs use a different contract. | Contribution summary implemented; dedicated experiment/health/detail GET endpoints remain targets |
| Learning UI | Contribution is the default tab, exposes every measurable path and preserves path-specific verdict/query states; legacy Dreaming, Knowledge Base and Reports remain | Experiments/Operations surfaces, detail/history and their accessibility proof remain targets |
| Trial admission | Separate Settings POST exists, remains default-closed and is tunnel-token protected | Observability remains recommendation-only; no mutation action is rendered here. The separate legacy `/settings/autonomous-learning` master toggle is also tunnel-token protected. |

`/analytics/learning-funnel` remains an artifact/activity report. It must not be
renamed, reused, or presented as the contribution projection.
`/analytics/alpha-attribution` is an adjacent diagnostic and must not substitute
for the prospective A1 verdict.

## 3. Semantic Vocabulary

### 3.1 Four independent dimensions

Every API and UI presentation keeps these dimensions separate:

| Dimension | Question | Examples |
| --- | --- | --- |
| Activity | Were artifacts or observations produced? | artifact count, exposure count |
| Measurement | Is there an identity-matched prospective verdict? | `not_measured`, `positive`, `invalid` |
| Operations | Did the evaluator run as expected? | `healthy`, `degraded`, `catchup_due`, `failed` |
| Authority | May a treatment affect behavior? | `measurement_only`, `paper_10`, `blocked` |

An operationally healthy evaluator can report `insufficient_data`. A positive
historical verdict can be stale for current authority. A high artifact count can
coexist with `not_measured`. No aggregate "Learning health" word may collapse
these dimensions.

### 3.2 Measurement states

The only contribution states rendered as verdicts are:

| State | Meaning |
| --- | --- |
| `not_measured` | no eligible persisted verdict for the requested path/cohort |
| `insufficient_data` | evaluator ran but sample, time, or intervention gates are unmet |
| `neutral` | measured effect is inside the practical-neutral band |
| `positive` | the identity-matched measurement verdict passed its declared gates |
| `negative` | measured effect or harm gate failed |
| `invalid` | evidence identity, source, economics, selection, or contract is inconsistent |
| `unavailable` | projection could not safely read or classify the required facts |

`inactive`, `shadow`, `paper_10`, `paper_25`, `paper_50`, `paper_active`,
`refrozen`, and `archived` are lifecycle/authority states, not contribution
verdicts.

### 3.3 Current measurement identity

A verdict is current only when all of these fields match the selected open
experiment and running process:

- `path_id` and `hypothesis_id`;
- `experiment_id` and expected `stage`;
- `concurrent_policy_epoch`;
- `target_horizon`;
- `outcome_contract_version`;
- `metric_version`;
- deterministic `behavior_contract_hash` built from the declared path behavior
  version and the policy/horizon/outcome/metric contract above.

The projection exposes the identity and a stable mismatch reason. It never calls
an old verdict "current" because it is the newest row by timestamp. Old metric,
epoch, stage, horizon, outcome-contract, or behavior-contract rows remain
historical evidence. `code_commit` and the sorted source-commit set remain
provenance only: a behaviorally identical rebuild neither fragments a cohort nor
blocks a safety action. If the behavior contract cannot be established, current
authority is `unavailable`; history remains readable.

Cross-build cohorts expose sorted `source_code_commits`,
`source_code_commit_unknown_n`, and `code_commit_provenance_complete`. A mixed
known/NULL source never collapses to a reassuring single-commit provenance; the
legacy scalar `code_commit` is populated only for a complete one-commit cohort.

The persisted policy-epoch id remains a separate observation-window boundary.
Equivalent effective-policy hashes are reproducible across instances, but an
explicit A→B→A policy rotation does not pool the later A window with the earlier
one; B-class interventions always restart observation.

## 4. Data Sources And Projection Ownership

### 4.1 Existing durable sources

`lco-v1` derives from existing state only:

| Source | Projection use |
| --- | --- |
| `learning_hypotheses` | path, scope, action, horizon, hypothesis lifecycle |
| `learning_experiments` | stage, allocation, gates, activation and predecessor identity |
| `learning_exposures` | assignment units, member identities, before/after influence and causal ids |
| `learning_contribution_verdicts` | append-only measurement, confidence, metric and provenance history |
| `learning_lifecycle_events` | applied/blocked lifecycle history, recommendation context and JSONL mirror pending/failure state |
| `learning_job_health` | current run state, watermark, backlog and durable last failure |
| `learning_policy_epochs` | non-secret policy identity and affected paths |
| `filter_candidate_outcomes` | canonical full-arm outcome and allocator selection truth |
| `universe_forward_outcomes` | selected-member legacy mirror/fallback only |
| Learning registry/config | declared path capability and effective default-closed switches |
| APScheduler registry snapshot | whether expected Learning jobs are currently registered and next run time |

This is an internal derived read model, not a new acquired data source. It requires
no data-utilization acquisition claim and no migration. The seven Learning tables
remain permanent under the existing retention contract; supporting outcome rows
remain protected by active-exposure anchors.

### 4.2 Projection layer

The target dependency direction is:

```text
existing SQLite + registry + sanitized scheduler snapshot
                     |
                     v
       aletheia.learning.read_models (sync, read-only)
                     |
                     v
        FastAPI GET routes (bounded, cached)
                     |
                     v
       typed frontend client -> Learning workbench
```

GET handlers do not call `evaluate_experiment()`, `run_contribution_evaluator()`,
`process_lifecycle()`, `admit_shadow_trial()`, a config save, or any mutation
repository. Summary endpoints read persisted verdict facts. Only one selected
experiment detail may reconstruct exact cohort membership, and that work is
bounded and cached.

## 5. `lco-v1` Common Contract

Every top-level response contains:

```json
{
  "schema_version": "lco-v1",
  "generated_at": "2026-07-19T00:00:00+00:00",
  "availability": "available",
  "unavailable_reason": null,
  "data_freshness": {
    "last_evaluated_at": null,
    "age_sec": null,
    "stale_after_sec": 21600,
    "state": "not_available"
  }
}
```

Rules:

- `generated_at` is projection generation time, not evaluator success time.
- `stale_after_sec` is twice the configured evaluator interval; it is not a
  fixed two-hour claim when the scheduler cadence differs. If configuration
  itself cannot be read, an unavailable envelope reports this threshold as
  `null` rather than inventing a cadence.
- `availability` is `available`, `partial`, or `unavailable`.
- A failed optional section yields `partial` and a section-specific reason; it is
  not silently replaced with an empty list or zero.
- `null` means not recorded/not applicable. It must not be converted to `0`,
  `false`, `healthy`, or `never` unless the backend proves that value.
- Unknown enum values remain visible as `unknown:<stored-value>` or make the
  relevant section unavailable; they are not coerced to a reassuring state.
- IDs, reason codes and timestamps are stable facts. Human labels are additive.
- Arbitrary `manifest_json`, scope JSON, exception text, SQL, local paths and
  secrets are never returned wholesale. Projection fields use an explicit
  allowlist.

## 6. Read APIs

Only §6.1 has a current implementation. Sections §6.2 onward remain
**target/not implemented** until their individual behavior is proved and the
endpoint moves into the current endpoint index in `api_reference.md`.

### 6.1 `GET /api/analytics/learning-contribution`

Purpose: bounded summary by path/current experiment, suitable for the default
Contribution tab.

Current minimal slice:

- returns additive `lco-v1` fields while preserving the legacy activity fields
  for compatible consumers;
- reads at most 20 current open experiments and projects registry path
  capabilities, current identity, the latest historical verdict, cohort
  integrity, evaluator health, lifecycle readiness and the effective treatment
  authority decision from the existing authority resolver;
  open experiments whose `h.path_id` appears in
  `cfg.learning.paper_influence_paths` are ranked first (priority 0), followed by
  every registry-declared measurement-supported path (priority 1), then
  unsupported paths (priority 2); rows are newest-first inside each tier, so an
  empty allowlist still keeps measurable experiments ahead of unsupported ones;
- performs no evaluation, lifecycle transition, admission or config mutation;
- resolves only an already-open matching policy epoch on GET; epoch creation or
  rotation remains confined to behavior-producing and owner-mutation paths;
- returns an honest `availability=unavailable` envelope when the new projection
  fails rather than converting failure into an empty/zero contribution;
- renders no mutation action. `mutation_available` is readiness information for
  the separately scoped Settings route and does not make the observability
  surface mutable or prove that route's owner-authentication boundary.
- reports real treatment authority independently from the GET-only UI boundary.
  A read-only page does not force an otherwise authorized experiment to appear
  blocked, and resolving authority performs no admission or config mutation.
- preserves legacy activity aggregates only as compatibility context. A failed
  legacy query is named in `legacy_query_errors`; nullable counts remain `null`
  rather than being presented as genuine empty or zero activity.
- preserves measurement when an operations health read fails. Operations names
  failed evaluator/audit sections in `query_errors`, retains successful/config
  sections, and makes the envelope partial rather than unavailable.

The optional `path_id`, `status`, `limit` and `offset` query contract remains target work.
The current fixed-bound summary already uses a 30-second monotonic process-local cache and emits
`Server-Timing`; future endpoint-family cache and latency obligations remain scoped to those
future slices. The current server-side bound cannot be enlarged by a client.

Each item contains:

```json
{
  "path_id": "universe_family_calibration",
  "hypothesis_id": "...",
  "experiment_id": "...",
  "stage": "shadow",
  "experiment_status": "active",
  "current_identity": {
    "policy_epoch": "...",
    "target_horizon": "4h",
    "outcome_contract_version": "...",
    "metric_version": "universe_family_calibration_cluster_allocation_utility_v5",
    "behavior_contract_hash": "...",
    "code_commit": "...",
    "matches_running_cohort": true,
    "mismatch_reason": null
  },
  "measurement": {
    "state": "insufficient_data",
    "reason": "no_actual_intervention",
    "baseline": null,
    "observed": null,
    "delta": null,
    "confidence_low": null,
    "confidence_high": null,
    "evaluated_at": "...",
    "window_start": "...",
    "window_end": "...",
    "coverage": {
      "basis": "eligible_assignment_unit_exposure_utc_day_either_arm",
      "status": "gap",
      "required_consecutive_days": 7,
      "current_consecutive_market_days": 3,
      "missing_market_day_n": 1,
      "missing_market_days": ["2026-08-03"],
      "outage_waiver": "unsupported_fail_closed"
    }
  },
  "samples": {
    "raw_exposures": 0,
    "treatment_assignment_units": 0,
    "assigned_treatment_units": 0,
    "authority_applied_treatment_units": 0,
    "actual_intervention_treatment_units": 0,
    "baseline_treatment_units": 0,
    "control_assignment_units": 0,
    "required_per_arm": 50,
    "complete_assignment_units": 0,
    "cluster_members": 0,
    "selected_members": 0
  },
  "integrity": {
    "canonical_members": 0,
    "fallback_members": 0,
    "matching_mirror_rows": 0,
    "excluded_assignment_units": 0,
    "exclusion_reasons": {},
    "invalid_reasons": {},
    "conflict_count": 0,
    "actual_intervention_state": "none",
    "actual_intervention_reason": "no_actual_intervention",
    "treatment_authority_rejection_reasons": {},
    "treatment_non_intervention_reasons": {},
    "harm_breached": false
  },
  "lifecycle": {
    "recommended_action": "admit_trial",
    "recommendation_reason": "...",
    "authority_enabled": false,
    "authority_block_reason": "experiment_stage_not_paper",
    "mutation_available": true,
    "mutation_unavailable_reason": null
  }
}
```

Counts are named by statistical unit. Permission and effective behavior remain
separate: an authorized all-no-op cluster is visible in
`authority_applied_treatment_units` but not `actual_intervention_treatment_units`.
`raw_exposures` and `cluster_members` must never be displayed as treatment/control
sample size.

`mutation_available=true` is readiness information only. It requires a current
identity-matched `admit_trial` recommendation plus all default-closed paper
gates. This observability surface remains GET-only and renders no mutation
button; the separately scoped, tunnel-token-protected trial-admission Settings
route owns the explicit owner action. The legacy master Learning toggle is a
different endpoint and is protected by the same tunnel owner token.

`matching_mirror_rows` is the persisted count of Universe rows that matched a
canonical outcome identity. It does not claim every row reached a settled
economic-agreement comparison. Proven economic or selection disagreements are
reported separately through invalid/conflict facts; the UI must not relabel this
counter as "agreeing mirrors".

### 6.2 `GET /api/learning/paths`

Purpose: expose declared capability without collapsing several meanings into one
`supported` flag.

Each registry path exposes:

- `path_id`, label, producer, consumer and overlap group;
- `consumer_wired`, `settings_supported`, `measurement_supported`;
- maximum authority, allowed effects and static fallback;
- declared outcome source/precedence and target horizon;
- expected health job and current availability.

An unwired or unmeasurable path remains visible and says why. It does not disappear
and is not called inactive solely because no current experiment exists.

### 6.3 `GET /api/learning/hypotheses`

Purpose: paginated hypothesis ledger.

Filters: `path_id`, `status`, `limit<=100`, `offset<=10000`. Rows expose stable
identity, sanitized scope/action, source count, horizon, lifecycle status, effect
threshold, harm budget, version and timestamps. Malformed stored JSON produces a
field-level `decode_state=unavailable`, not an empty object.

### 6.4 `GET /api/learning/experiments`

Purpose: paginated current and historical stage ledger.

Rows expose experiment/hypothesis/path identity, stage, status, allocation,
required samples/time, first real exposure start, predecessor/activation verdict,
current-identity status, latest measurement summary, recommendation, authority
block and next evaluation time. The list reads persisted summaries; it does not
rebuild all cohorts.

### 6.5 `GET /api/learning/experiments/{experiment_id}`

Purpose: one bounded drill-down used by the experiment/verdict drawer.

Query parameters:

- `cluster_limit`: default 20, maximum 100;
- `cluster_offset`: default 0, maximum 10,000;
- `member_limit`: default 20, maximum 100 per selected cluster;
- `verdict_limit`: default 20, maximum 100;
- `event_limit`: default 20, maximum 100.

The detail contains:

- experiment, hypothesis, current identity and lifecycle recommendation;
- append-only verdict history with explicit `current` boolean and mismatch reason;
- aggregate source, exclusion, invalid, selection and harm facts;
- assignment clusters with `assignment_unit_key`, arm, expected/member/selected
  counts, completeness, intervention-applied state and source mix;
- bounded member rows with candidate/outcome identity, selected/dropped state,
  canonical/fallback/mirror source and exact exclusion/invalid reason;
- bounded lifecycle events.

Conflict fingerprints may be returned as stable hashes. Raw arbitrary manifests,
source prose and local file paths are not returned.

### 6.6 `GET /api/learning/health`

Purpose: show effective configuration, evaluator continuity and recovery without
inventing scheduler facts.

```json
{
  "config": {
    "contribution_evaluation_enabled": true,
    "autonomous_learning_enabled": false,
    "lifecycle_automation_enabled": false,
    "paper_influence_enabled": false
  },
  "global_refreeze_reason": null,
  "measurement_catchup": {
    "current_window": "...",
    "input_watermark": "...",
    "due": false,
    "state": "current"
  },
  "jobs": [{
    "job_id": "learning_contribution_evaluator",
    "registered": true,
    "essential_during_layered_pause": true,
    "next_scheduler_run_at": "...",
    "current_status": "healthy",
    "last_expected_at": null,
    "last_started_at": "...",
    "last_succeeded_at": "...",
    "next_due_at": "...",
    "input_watermark": "...",
    "backlog_count": 0,
    "oldest_backlog_at": null,
    "consecutive_failures": 0,
    "current_error_code": null,
    "last_failed_at": "...",
    "last_failure_code": "internal_failure"
  }]
}
```

`last_expected_at` and output counts remain `null` until the backend truly records
them. Stored `last_error`/`last_failure_reason` currently contain truncated raw exception text;
the projection must classify them into an allowlisted stable code such as `timeout`,
`database_unavailable`, `invalid_result`, or `internal_failure`. It never returns the raw stored
text, local paths, SQL or exception details. A recovered `current_status=healthy` does not erase
`last_failed_at` or the sanitized `last_failure_code`. Historical `learning_verdict_runner` rows are shown only
under `historical_unregistered_jobs`; they never imply registration.

Catch-up state is derived from the evaluator watermark and current window. The
one-shot catch-up job has no fabricated durable row.

### 6.7 `GET /api/learning/exposures/{entity_id}`

Purpose: explain why one known entity received or did not receive Learning
influence.

The initial slice supports only `entity_type=candidate`, using the leftmost
`candidate_key` prefix of the existing exposure entity index. List limit is maximum 100.
A broad `OR` across every nullable entity column is forbidden. Opportunity, decision,
proposal, position and observer-run lookup remain unavailable until a separately justified
indexed query contract exists.

Rows expose assignment, consumer/stage/path, before/after allowlisted values,
experiment/policy/build identity, authority-applied state, exact outcome key/source
and causal ids. Arbitrary stored manifests are sanitized before response.

## 7. Query, Cache And Performance Contract

The summary and experiment reads enforce positive server caps and run as synchronous
FastAPI handlers. The summary uses a 30-second monotonic process-local cache, emits
`Server-Timing`, and has read-only/event-loop regression proof. Detail/pagination
families beyond the current experiment endpoints remain future slices.

### 7.1 Bounded reads

- Summary/list endpoints use indexed ledger reads and persisted verdict/harm facts.
- Exact outcome reads retain bounded key batches; there is no `SELECT *` over an
  unbounded experiment population.
- Cohort reconstruction is permitted only for one explicit experiment detail.
- Every history/detail list has server-enforced caps from §6.
- No Markdown, JSONL history, LLM, exchange, network or filesystem scan occurs in
  these GET paths.
- No transaction or write lock is opened for a projection.

### 7.2 FastAPI execution

SQLite/config/scheduler snapshot work runs in synchronous `def` handlers, which
FastAPI sends to its worker pool, or through an explicit thread offload. An
`async def` handler must not directly call blocking SQLite, filesystem or
`threading.Lock` code.

### 7.3 Cache

- Process-local monotonic TTL: 30 seconds by default, maximum 60 seconds.
- Cache key includes endpoint, schema version, filters, pagination and experiment
  identity.
- Compute occurs outside the cache lock; only lookup/publication is locked.
- Cache values are immutable response projections, not database connections or
  provider instances.
- A write-side invalidation hook is optional for the first slice because the
  maximum staleness is explicit. If added later, it must not couple evaluator
  transactions to HTTP state.
- Responses expose `generated_at` and source freshness so a cache hit remains
  explainable.

### 7.4 Measurement and diagnostics

Responses emit `Server-Timing` phases such as `cache_hit`, `ledger`, and `total`.
Before deployment, implementation acceptance measures cold and warm requests on the
isolated Preview API port from `DEVELOPMENT_PREVIEW.md`; only the owning stable Runtime
may be measured on reserved backend `:8788`. A Worktree must never bind `:8788` merely
to satisfy performance evidence:

- summary, paths, hypotheses, experiments and health: cold target under 100 ms;
- one experiment detail: bounded and reported; any cold result above 100 ms must
  be explained and have a warm-cache measurement;
- frontend polling: no faster than 30 seconds and only while the relevant Learning
  tab is visible.

PR #506 recorded isolated Preview `:8791` evidence for the current summary: 37.290 ms cold and
2.822 ms warm. Stable Runtime `:8788` evidence remains unavailable until an owning-runtime
deployment and is not implied by that Preview measurement.

## 8. Learning Workbench Information Architecture

The Contribution tab exposes every measurement-supported path through a labelled
native selector, keeps verdict/query state path-specific, and polls no faster than
30 seconds while mounted. Responsive 1100/700px layouts and browser accessibility
proof cover this surface. The complete five-tab workbench, history, drawers and
dedicated Operations surface remain targets.

### 8.1 Page structure

The route remains `/learning`. The target top-level tabs are:

1. **Contribution** - default measured-contribution surface.
2. **Experiments** - current and historical hypothesis/experiment ledger.
3. **Dreaming** - existing artifact-production view.
4. **Knowledge Base** - existing truthful item taxonomy and review actions.
5. **Operations** - evaluator, catch-up, backlog, failure and refreeze state.

The existing `Reports` content is decomposed:

- the knowledge funnel remains an activity panel under Knowledge or Operations and
  is labelled "Activity, not measured contribution";
- portfolio PnL remains optional context and is labelled "Portfolio context, not
  Learning attribution";
- contribution verdict/history belongs only in Contribution/Experiments.

Legacy `/dreaming`, `/knowledge`, and `/reports` links must select a deliberate
tab rather than all defaulting to Dreaming.

### 8.2 Status strip

Follow the dense Blotter status-token pattern with five independent values:

| Token | Source |
| --- | --- |
| Authority | effective stage and authority block |
| Contribution | current measurement state only |
| Experiments | open experiment count |
| Evidence | unique treatment/control assignment units versus requirement |
| Evaluator | current job/catch-up state plus last success age |

Green is allowed for an identity-matched `positive` measurement or a truly
healthy job, but those are separate tokens. Cyan means selected/current focus,
not success. Missing requests render `Unavailable`, not zero, `Never`, or healthy.

### 8.3 Contribution tab

Reading order:

1. Full-width Contribution by Path `DataTable`.
2. Desktop 7/5 band: Current Cohort and Lifecycle Recommendation.
3. Full-width append-only Verdict History.
4. `DetailDrawer` for experiment, cluster and member provenance.

Every configured path remains visible. `measurement_supported=false`,
`not_measured`, and `inactive` are explicit states. The recommendation panel says
"Read-only recommendation" and contains no admission button.

A path with `measurement_supported=false` cannot create a formal hypothesis,
experiment, or exposure. Its consumer receives the declared static fallback with
`measurement_unsupported`. Historical open experiments for such paths are
soft-terminalized only through the owner maintenance command, which defaults to
dry-run and preserves all prior ledger rows.

### 8.4 Experiments tab

The ledger exposes path, hypothesis, stage, status, allocation, required samples,
market days, first real exposure time, policy epoch, metric identity, current
recommendation, authority block and end/refreeze reason. Registration time is not
displayed as observation start.

### 8.5 Operations tab

Separate panels show:

- effective Learning switches and global refreeze reason;
- current evaluator status, last success, next due and catch-up state;
- durable last failure even after recovery;
- lifecycle audit-mirror current health, pending backlog and durable last failure;
- real backlog/watermark fields where recorded;
- outcome/source integrity counts from current measurements.

If one experiment fails while the evaluator continues processing others, the
durable job state is `degraded`, records the bounded failure count, and does not
advance the measurement watermark. It must not report `healthy` for that cycle.

No panel says the Learning strategy is effective because the evaluator is healthy.

### 8.6 Loading, empty, error and stale states

Each query distinguishes:

- loading: shape-preserving skeleton;
- genuine empty: backend returned `available` with zero rows;
- unavailable: request/section failed or backend cannot classify the data;
- stale: last successful projection remains visible with timestamp and stale label;
- partial: usable sections remain visible while failed sections name their reason.

The summary carries one `path_states` entry for every measurement-supported path,
including path-local availability, empty/active state, freshness and a stable
unavailable reason. One failed path therefore makes the envelope partial without
hiding a healthy path or relabelling its verdict.

Failed queries never default to `[]` or `0`. Retry is explicit. Timestamp freshness
is visible on Contribution and Operations.

### 8.7 Responsive and accessibility rules

- Above 1100 px: five status tokens, full ledger, 7/5 detail band.
- 700-1100 px: three-column status strip and stacked detail band.
- Below 700 px: two-column strip, horizontally scrollable top tabs, wrapped
  toolbars, full-width drawer and horizontally scrollable tables.
- Use existing `Tabs`, `DataTable`, `DetailDrawer`, `Card`, `Badge`, `StatusText`
  and `Meter` primitives; do not create lookalike replacements.
- Rows opening a drawer are keyboard activatable; tabs retain proper tab roles and
  visible focus.

## 9. Failure And Honesty Rules

| Failure | Required projection behavior |
| --- | --- |
| Database read fails | section or response `unavailable`; no empty fallback |
| Stored JSON malformed | field-level decode failure; preserve row identity |
| Running commit unavailable | current identity/authority unavailable; history readable |
| Verdict belongs to old metric/epoch/stage/build | historical with mismatch reason |
| Canonical/fallback economics disagree | `invalid`; count, source row ids and exact differing fields visible |
| Allocation selection disagrees | explicit exclusion with provenance; row contributes no evidence |
| Harm sample gate is not met | `indeterminate`; authority blocked without claiming a breach |
| Assignment cluster incomplete | excluded with exact reason; survivors are not samples |
| Evaluator recovered after failure | current healthy plus durable prior failure |
| Catch-up watermark missing/old | `catchup_due` or `unavailable`, never healthy by inference |
| Scheduler job absent | `registered=false`; durable old health does not override |
| `admit_trial` recommended | recommendation visible; no mutation control; readiness remains distinct from owner authentication |
| Path consumer wired but evaluator unsupported | both booleans shown separately |
| API response cached | generation/source freshness visible |

## 10. Implementation Slices And File Boundary

Implemented and remaining slices are split so a review can prove one boundary at a time.

### Slice LCO-BE1 - summary, paths and health (partially implemented)

Expected files:

- `aletheia/learning/read_models.py` and the thin summary route in
  `aletheia/server/routes/analytics.py` are implemented with focused tests;
- separate paths, experiment and health routes plus their route mount remain
  target work.

No scheduler behavior or schema change. If per-job registration is needed, add a
sanitized read-only scheduler snapshot helper only.

### Slice LCO-BE2 - experiment and exposure detail

Add bounded pagination, one-experiment cluster reconstruction, allowlisted
manifest projection and cache diagnostics. Do not add an index in this slice
unless direct `:8788` measurements prove the need; any new index is an additive
data-model batch with its own spec/document update.

### Slice LCO-FE1 - Contribution and Operations (partially implemented)

Typed client models, honest path-specific query states, registry-backed measured-path selection,
cohort/recommendation panels and responsive native-control access are implemented.
Status strip, history, Operations and responsive detail drawer remain. Preserve
Dreaming/Knowledge taxonomies and keep their mutations outside LCO panels.

### Slice LCO-FE2 - Experiments and artifact honesty cleanup

Add experiment ledger/deep links, make legacy routes select the correct tab, and
repair existing activity panels that currently turn fetch failures into empty/zero
states. This is adjacent UI honesty work, not contribution authority.

### Explicitly excluded files/modules

- `aletheia/risk/`, `aletheia/execution/`, exchange adapters and live routes;
- secrets, signer, permissions and restricted tool registries;
- contribution metric/cohort assignment/lifecycle transition logic;
- config schema/defaults and Settings controls;
- storage migrations and retention policy unless separately justified by measured
  query performance.

## 11. Executable Claims

| Claim | Level | Requirement | Current status |
| --- | --- | --- | --- |
| LCO-S01 | L2a | The typed GET-only `lco-v1` summary route is indexed; no LCO mutation exists | implemented |
| LCO-B01 | L2b | Cohorts expose raw exposures, complete treatment/control assignment units, member/selected counts and stable exclusion/invalid reasons | implemented |
| LCO-B02 | L2b | Canonical, fallback, matching mirror, allocation inference and proven disagreement are distinct; conflicts never become evidence | implemented |
| LCO-B03 | L2b | Current metric/cohort identity is separate from append-only historical verdict versions | implemented |
| LCO-B04 | L2b | Current job status, last success, next due, sanitized durable last failure, watermark and catch-up due state remain distinct; raw stored errors never leave the backend | implemented |
| LCO-C01 | L2c | Observability remains read-only; admission readiness is separate from owner-route authentication and every paper gate | implemented |
| LCO-C02 | L2c | UI contribution state comes only from backend measurement facts; counts and PnL cannot imply effectiveness | partial — current Contribution verdict derivation and rendered Workbench activity/PnL context are behaviorally covered by Vitest; the Python executable-claim registry does not yet execute that frontend proof |
| LCO-C03 | L2c | Reads perform no writes, evaluation, lifecycle transition, or authority mutation and blocking work stays off the event loop | implemented |
| LCO-S02 | L2a | LCO projection/routes have no Risk, Execution, exchange, secrets, permission or live imports | implemented |
| LCO-B05 | L2b | consumer-wired, Settings-supported and measurement-supported remain separate path capabilities | implemented |
| LCO-C04 | L2c | loading, genuine empty, unavailable, stale and partial states remain distinct; fetch failure never becomes zero/empty | partial — current Contribution and legacy Workbench reads now preserve loading/error/stale/partial and nullable optional sections with backend/frontend behavioral tests; the Python executable-claim registry does not yet execute the frontend proof |
| LCO-C05 | L2c | desktop/mobile layouts and native keyboard/label semantics preserve access to the current measured-path selector and Contribution summary | partial — component Vitest proof exists, but the Python executable claim still checks source/proof presence rather than running the UI behavior |
| LCO-C06 | L2c | the current summary uses a monotonic TTL cache and emits explainable cold/cache-hit `Server-Timing` | implemented for the current summary route; immutable publication/order have adjacent tests, while future endpoint and stable-runtime latency slices retain separate obligations |

Partial claims stay partial until their executable proof runs the claimed behavior.
This proposal currently has no strict-xfail placeholder for future workbench slices;
their delivery state belongs to GitHub issue #614. Source-string presence alone
cannot make a behavioral claim GREEN.

## 12. Acceptance Matrix

| Concern | Required proof |
| --- | --- |
| Naming | Current normative docs use the canonical feature id/name; bare A2 is historical only |
| API index | Target routes stay in a clearly not-implemented section until code ships |
| Read-only | Snapshot all seven Learning table counts/content around every GET; no change |
| No hidden action | Mocks prove no evaluator, lifecycle, admission or config mutation call |
| Cluster integrity | Incomplete clusters remain excluded; selected survivors cannot become samples |
| Source provenance | Canonical, fallback, mirror and conflict fixtures remain distinct |
| Metric history | Old metric/build/epoch verdict remains history, never current |
| Scheduler recovery | Healthy current state can coexist with a visible durable last failure |
| Catch-up | Current window, watermark and due state are independently asserted |
| Recommendation | `admit_trial` renders without a button or mutation invocation from the observability surface |
| Path capability | Consumer/settings/measurement support booleans cannot collapse |
| Empty/error honesty | Request failure is unavailable, not an empty cohort or zero count |
| Performance | Current summary limits/cache/`Server-Timing` and cold/warm Preview evidence recorded; future endpoints and stable-runtime qualification retain separate proof |
| Frontend | Typecheck, unit tests, desktop/mobile checks and `npm run build` pass |
| Safety | Focused architecture test proves no Risk/Execution/exchange/live imports |

## 13. Delivery And Completion

Design completion means this contract, adjacent references and missing executable
claims agree. It does not mean the feature exists.

Implementation completion requires all `LCO-*` claims GREEN, focused backend and
frontend tests, direct backend latency evidence, frontend production build, and a
PR/CI record. It still does not prove Learning improves profitability; it proves
the owner can inspect the measured evidence honestly and without granting new
authority.
