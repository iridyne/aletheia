# Position Replay and Lifecycle Story Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `PL` · **Owner code:** `core/aletheia/analytics/position_replay.py::build_position_replay`

## Scope

This capability lets the owner and the post-trade review read back what happened to one
trade on the mainline (Candidate → Entry Plan → Proposal → Risk → Order → Fill → Position →
verified close): an exact-ID Position replay, a deterministic lifecycle story, a
candidate-first lineage diagnosis, and the 5-minute path observations that feed them. The
replay, story and diagnosis are read-only inspection; the Position Path Observer is the one
scheduler writer. It has no trading authority. The fixed-horizon candidate funnel, the
first-60-minute counterfactual registry and the seven-layer Profit Gradient are frozen
research tools owned by the [Profit Gradient Contract](profit_gradient_evidence_contract.md).
Decision-level learning labels (judgment R) come from
`core/aletheia/analytics/signal_replay.py`, not from this replay
([spec 0001](../specs/0001-learning-loop/spec.md)).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/cli/inspect_cmd.py::position_replay` | CLI `aletheia inspect position-replay --id <id> [--as-of <ts>] [--json]` | owner / offline review |
| `core/aletheia/cli/inspect_cmd.py::position_story` | CLI `aletheia inspect position-story --id <id> [--as-of <ts>] [--json]` | owner / offline review |
| `core/aletheia/cli/inspect_cmd.py::position_diagnosis` | CLI `aletheia inspect position-diagnosis` | owner |
| `core/aletheia/cli/inspect_cmd.py::lifecycle` | CLI `aletheia inspect lifecycle --<anchor>-id <id> [--as-of <ts>]` | owner |
| `core/aletheia/cli/inspect_cmd.py::position_path` | CLI `aletheia inspect position-path --id <id>` | owner |
| `core/aletheia/agent_runtime/scheduler.py::_run_position_path_observer` | scheduler job `position_path_observer`, every 5 min | Gateway scheduler |
| `core/aletheia/cli/control_cmd.py` (`build_position_path_observer_status`) | `aletheia control status` section | owner |
| `core/aletheia/storage/repositories.py::insert_trade_proposal` → `core/aletheia/storage/proposal_hypothesis.py::freeze_position_hypothesis` | call before the Proposal hash | every Proposal insert |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `position_path_observations` | `core/aletheia/portfolio/position_path_observer.py::observe_open_position_paths` | replay, story, `inspect position-path`, control status | append-only (update/delete triggers) |
| `position_path_observation_deferred_buckets` | same observer (`repo.defer_position_path_observation_bucket`, `repo.resolve_deferred_position_path_observation`) | observer, control status | replaceable work queue |
| `trade_proposals.attribution_snapshot.position_hypothesis` | `core/aletheia/storage/proposal_hypothesis.py::freeze_position_hypothesis` | replay, story, counterfactual | immutable, inside the Proposal hash |

Replay, story and diagnosis artifacts are never persisted. They read `trade_proposals`,
`risk_decisions`, `orders`, `fills`, `positions`, `entry_plans`,
`candidate_planning_generations`, `universe_members`, settlement/runtime provenance and
`market_ohlcv` through caller-owned or read-only connections.

## Requirements

### Position replay

#### PL-C01 Exact-ID causal joins

`When a Position is replayed, the replay shall join Proposal, authoritative Risk decision, Order, Fill, Position, exit plan, protection, settlement, runtime provenance and OHLCV only by exact durable IDs and shall never select a latest related record as a fallback.`

- WHEN the entry Order's `risk_decision_id` differs from the joined Risk decision THEN `causal_errors` contains `authoritative_risk_identity_mismatch`.
- WHEN a closed Position has no close Fill chain THEN `causal_errors` contains `close_fills_missing`.

Evidence: `core/aletheia/analytics/position_replay.py::build_position_replay`.

#### PL-C02 Universe ancestry needs no PI decision

`When the replayed Proposal has no opportunity_id, the replay shall resolve ancestry Candidate → Planning Generation → Entry Plan → Proposal through the shared lineage reader and shall not report a missing PI decision as a causal error.`

- WHEN a Universe-origin Position has a recorded Planning Generation THEN `causal_errors` contains no `pi_decision_not_recorded`.
- WHEN lineage finds no Planning Generation THEN `causal_errors` contains `planning_generation_not_recorded`.

Evidence: `core/aletheia/analytics/position_replay.py::build_position_replay` (calls `candidate_lifecycle.for_position`). Serenity/Opportunity-origin Proposals (Serenity is Experimental) resolve via `opportunity_id`.

#### PL-C03 Cutoff visibility

`When as_of_at is supplied, the replay shall exclude every row created after that cutoff, and rerunning against unchanged durable facts and the same cutoff shall produce the same canonical fact and OHLCV window hash.`

- WHEN a Risk decision is created after `as_of_at` THEN the replay treats it as absent.

#### PL-C04 Ordered path is complete or unavailable

`The replay shall build the ordered OHLCV path only from closed one-minute bars of the exact instrument with complete coverage, shall report missing coverage as unavailable, and shall treat a same-bar Stop/Target order as ambiguous with pessimistic bounds and MFE/MAE bounded at zero.`

- WHEN one bar inside the holding window is missing THEN `stages.holding.ohlcv.availability` is `unavailable`, never a zero excursion.

Evidence: `core/aletheia/analytics/trade_path.py` (bounded path classification).

#### PL-C05 Gross-to-net reconciliation

`The replay shall deduct entry fee, exit fee, funding and slippage exactly once from gross economics and shall report the residual against settled net economics.`

- WHEN settled economics are missing THEN `economics.availability` is `unavailable`.

#### PL-C06 Position hypothesis is frozen before the Proposal hash

`When a native entry Proposal with positive quantity and long/short direction is inserted, the system shall freeze position_hypothesis/v1 into attribution_snapshot before the commit hash is computed, and shall never backfill historical or non-entry Proposals, which remain not_recorded.`

- WHEN an existing `position_hypothesis` is present THEN it is validated, never rewritten.

Evidence: `core/aletheia/storage/proposal_hypothesis.py::freeze_position_hypothesis`. See Known conformance gaps.

### Lifecycle story

#### PL-C07 Deterministic story with a machine projection

`The position-story command shall render position_lifecycle_story/v1 deterministically without any model call, and only its structured_events projection shall be a machine input; rendered text shall never enter learning or statistical attribution.`

- WHEN the same Position and cutoff are rendered twice THEN the output is identical.
- WHEN a path observation is visible at the cutoff THEN `structured_events` holds a `position_path_observed` event whose `occurred_at` is the observation's `observed_at` (not the bucket open) and whose facts keep `producer_commit` and `runtime_session_id`.

Evidence: `core/aletheia/analytics/position_lifecycle_story/service.py::build_position_lifecycle_story` (`STORY_CONTRACT_VERSION`), `core/aletheia/analytics/position_lifecycle_story/events.py`.

#### PL-C08 Story vocabulary

`The story shall report first meaningful favorable excursion, maximum favorable excursion, hypothesis weakening and invalidation, expected horizon, management decisions, ExitPlan revisions, protection activation, exit triggers, close fills and verified costs over the actual opened_at → closed_at path.`

- WHEN the opening thesis is rendered THEN it is a human field, not a `structured_events` input.

#### PL-C09 Hypothesis prefix evaluation

`The hypothesis-state evaluator shall read only the completed bar prefix supplied by its caller and shall return not_evaluable when a machine-evaluable input is missing.`

Evidence: `core/aletheia/storage/proposal_hypothesis.py::evaluate_hypothesis_prefix`.

### Lifecycle diagnosis

#### PL-C10 One anchor, one lineage reader

`When aletheia inspect lifecycle receives exactly one of candidate, generation, entry_plan, proposal, order or position IDs, the inspector shall emit lifecycle_diagnosis/v1 from the shared candidate_lifecycle lineage reader, and it shall exit 2 for any other anchor count.`

- WHEN two anchors are given THEN the command exits 2 with `exactly one anchor required`.
- WHEN the database is missing THEN it prints `availability=unavailable`, `unavailable_reason=database_missing` and exits 0.

Evidence: `core/aletheia/cli/inspect_cmd.py::lifecycle`, `core/aletheia/analytics/candidate_lifecycle/lineage_reader.py` (`for_candidate` … `for_position`), `core/aletheia/analytics/candidate_lifecycle/inspector.py::build_lifecycle_diagnosis`. The same reader serves `position_replay.py`, `position_lifecycle_story/readers.py`, `family_scorecard.py` and `trade_attribution.py`.

#### PL-C11 Source kind resolution

`The lineage reader shall take source_kind from plan_json.$.source_kind, else from Proposal columns (opportunity_id → serenity/opportunity; entry_plan_id with a Candidate → universe), else from discriminating tables, and shall report unknown with entry_plan_not_found when nothing discriminates.`

- WHEN both `entry_plan_id` and `opportunity_id` are NULL THEN `source_kind='unknown'` and `evidence_gaps` contains `entry_plan_not_found`, with no cross-branch fallback.
- WHEN `plan_json` extracts disagree with joined rows THEN `identity_conflicts` is non-empty.

#### PL-C12 Per-leg stages and attention state

`The inspector shall emit ancestry stages with leg=null, expand each entry leg (entry_leg_index 0 or 1) into proposal → risk_decision → order → fill → position → exit, and derive attention_state as breakpoint, evidence_gap, ended_ok, ended_reject, waiting or advancing.`

- WHEN `identity_conflicts` is non-empty THEN `attention_state` is `breakpoint`.
- WHEN the latest visible Position is `closed` or `archived` THEN `attention_state` is `ended_ok`.
- WHEN the latest visible Order is `rejected`, `failed`, `cancelled` or `invalidated` THEN `attention_state` is `ended_reject`.

Evidence: `core/aletheia/analytics/candidate_lifecycle/inspector.py::_derive_attention_state`.

#### PL-C13 Diagnosis cutoff and economics

`The lineage reader shall filter every SELECT by created-at ≤ cutoff, reporting a later row as <entity>_not_yet_visible, and the inspector shall embed the Position replay only for a position anchor and report anchor_is_not_a_position otherwise.`

- WHEN the anchor is a Candidate THEN `economics.position_fact.unavailable_reason` is `anchor_is_not_a_position` and `economics.candidate_fact` is `unavailable`.

### Position Path Observer

#### PL-C14 Expected-bucket registration before provider I/O

`When the position_path_observer job runs, the observer shall, for every open or closed Position in an exchange-demo or live execution mode, durably register the oldest not-yet-materialized complete 5-minute bucket (capped at closed_at) before any provider request.`

- WHEN a tick was skipped THEN the next run registers the oldest missing bucket first, oldest-first, at most two bounded recovery passes per run.
- WHEN a Position is `archived` or in a retired simulated mode THEN it is not observed and its existing rows stay readable.

Evidence: `core/aletheia/portfolio/position_path_observer.py::observe_open_position_paths` (`_MAX_RECOVERY_PASSES_PER_CYCLE = 2`), `core/aletheia/storage/repositories.py::list_positions_for_path_observation_recovery` (`_POSITION_PATH_EXECUTION_SCOPE_SQL`).

#### PL-C15 Exact venue and closed-bar proof

`The observer shall fetch the bar from the exact venue derived from the Position's execution mode, canonical symbol and market type, and shall accept only a provider candle whose timestamp equals the bucket open and whose is_closed is true.`

- WHEN a recovered bucket is materialized THEN funding and historical protection levels are `unavailable`; current mutable state is not reused, and `exit_plan_version=0` carries no ExitPlan identity.

Evidence: `core/aletheia/portfolio/position_path_observer.py::_resolve_instrument`, `observe_open_position_paths` (`exact_venue=True`).

#### PL-C16 Bounded retries end in an unavailable fact

`If a bucket's provider fetch fails transiently, then the observer shall keep it in the deferred queue, and after POSITION_PATH_MAX_RETRY_ATTEMPTS (3) attempts shall append an unavailable fact with reason max_retries_exceeded:<reason> and remove the queue row.`

- WHEN a crash left an exhausted queue row THEN the next run materializes it as `unavailable` before discovering new gaps.

Evidence: `core/aletheia/portfolio/position_path_observer.py::observe_open_position_paths` (`defer_retry`), `core/aletheia/storage/repositories.py` (`POSITION_PATH_MAX_RETRY_ATTEMPTS`).

#### PL-C17 Observer status is visible

`The control status surface shall expose the oldest missing bucket, oldest deferred bucket, durable deferred count, observer lag and trailing 24h available/unavailable/skipped counts.`

Evidence: `core/aletheia/portfolio/position_path_observer.py::build_position_path_observer_status`, read by `core/aletheia/cli/control_cmd.py`.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| ID, cutoff, accounting or OHLCV evidence missing/conflicting | return the fact with reasons; no fallback fact, no cached terminal error, no mutation | `causal_errors`, `unavailable_reason`, `evidence_gaps`, `identity_conflicts` | `unavailable`, never `0` |
| Database missing for `inspect lifecycle` | exit 0 | `unavailable_reason=database_missing` | `unavailable` |
| Bad CLI arguments | exit 2 | Typer `BadParameter` | — |
| Path provider transient failure | deferred retry, then append-only `unavailable` fact | control status deferred count / lag | `unavailable` row, never a synthetic bar |
| Observer job error | logged, job reports errors | `position_path_observer_reported_errors` | — |

## Authority

Read-only inspection plus append-only path observations; it may never change a Position,
protection, Risk, Execution, Entry Planner input, Learning state, Live unlock or an
experiment cohort, and no analytics result authorizes an automatic behavior change.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| PL-C01, PL-C03, PL-C04, PL-C05 | `tests/test_position_replay.py`, `tests/test_stop_loss_path_attribution.py` |
| PL-C02 | `tests/test_universe_lifecycle_regression.py` |
| PL-C06, PL-C09 | `tests/test_proposal_hypothesis.py` |
| PL-C07, PL-C08 | `tests/test_position_lifecycle_story.py` |
| PL-C10 – PL-C13 | `tests/specs/spec_candidate_lifecycle_lineage_reader.py` (LC-S01 … LC-S06), `tests/test_profit_loop_cli.py` (`test_inspect_lifecycle_*`), `tests/test_trade_attribution.py::test_build_rows_universe_origin_is_included_via_lineage_reader` |
| PL-C14 – PL-C17 | `tests/test_position_path_observer.py` |
| diagnosis read-only flags | `tests/test_profit_loop_diagnosis.py` |

## Known conformance gaps

- **PL-C06 never fires on the Universe mainline.** `core/aletheia/storage/proposal_hypothesis.py::freeze_position_hypothesis`
  returns early when `not proposal.opportunity_id`, and
  `core/aletheia/execution/entry_plan_execution.py::build_entry_plan_order_intent` sets
  `opportunity_id=None` for every non-Serenity Entry Plan. Universe-origin trades therefore
  carry no `position_hypothesis/v1`; the story and the counterfactual registry
  (`frozen_inputs_unavailable`) have no frozen hypothesis for them.
- **PL-C02 decision stage.** `core/aletheia/analytics/position_replay.py::build_position_replay`
  sets `stages.decision.availability` to `available` only when `pi_decision` exists, so every
  Universe-origin replay reports the decision stage `unavailable` even though no PI decision
  is required on that path.

## Decision references

- Moved runtime readings, progress notes and the 2026-08-13 registration note:
  [history](../history/profit_loop_and_rationale_retired_text.md); retired simulated
  observer definitions: [local simulated position observation](../history/local_simulated_position_observation.md).
- Product plan: [position replay / profit gradient proposal](../proposals/position_replay_profit_gradient.md).
- Entry-leg and scale-in fill-chain verification in the replay: #1271.
- Owner 2026-10-10: the AI trades like a person; review → lesson → next decision is the
  mainline ([spec 0001](../specs/0001-learning-loop/spec.md)).
