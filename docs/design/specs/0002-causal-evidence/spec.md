---
id: 0002-causal-evidence
title: Causal inference and counterfactual evidence
status: draft
depth: D2
prefix: CE
issue: 3934
contracts:
  - ../../contracts/counterfactual_contract.md
  - ../../contracts/observation_lab_contract.md
  - ../../contracts/universe_twin_shadow_contract.md
  - ../../contracts/learning_contract.md
supersedes:
  - ../../subsystems/causal_counterfactual_research_guidance.md
superseded_by: []
---

# 0002 — Causal inference and counterfactual evidence

## Summary

Aletheia is a price taker: whether it trades does not move the market. So the most common
counterfactual it needs — "what would the price have done" — is **fully observed** and needs no
statistical guessing. Only three things are genuinely unobserved: whether an order would have
filled, what the AI would have decided under a different context, and how one position's
capital use affects another. This spec classifies every causal/counterfactual question by
which of these four it is, fixes the one method allowed for each, unifies the duplicated
estimators, deletes the stack that runs on frozen or retired data, and makes every claim
declare what it estimates and how strong its evidence is.

## Problem and evidence

Inventories of design docs, code and practice (2026-10-10; read-only).

**Where the two concepts are used today**

| Use | Question | Method | Drives a decision? |
|---|---|---|---|
| Candidate Selection Utility (`core/aletheia/universe/economic_utility.py:469`) | Expected 4h net return of a mechanism×direction cohort | shrunk cohort mean − SE over full-denominator outcomes | **Yes** — Universe admission order (`universe/service.py:785-811`) |
| Fixed-horizon outcomes FCO (`universe/filter_outcome_recorder.py`) | Net return had we taken the candidate | settlement, not estimation | data source for the above, Entry context, lesson reviewer |
| Observation Lab (`extensions/observation_lab/manifest.py:430`, `learning/verdicts.py:412`) | ITT effect of a treatment | stable hash assignment; conditional randomization test | wired into Entry/sizing/Risk; returns baseline — Fedora has 0 manifests (verified 2026-10-10), so it has never changed behavior |
| Filter shadow / filter replay (`observation_lab/filter_shadow.py`, `counterfactual/filter_replay.py`) | Would a looser threshold admit better candidates | shrunk mean + **IID** bootstrap | report only; replay off by default (`controller.shadow_enabled=False`) |
| Conviction-gate calibration, propensity, IPS/DR, advisories (`extensions/counterfactual/`) | Causal effect of the conviction gate | naive mean / logistic propensity / DR | **No** — CLI only; `shadow_trades` has no writer since #3704 |
| Twin and relative-rank shadows (`universe/twin_*`, `relative_rank_*`) | Step vs continuous scoring | hash clusters, 0.5 propensity | **No** — builders have no production caller; contract names a removed authority |
| Profit gradient, profit-loop counterfactual, sizing/momentum replays | Which layer loses money; delayed entry, breakeven | paired/cluster intervals, retrospective replay | **No** — CLI only |
| Exit breakeven shadow (`analytics/exit_breakeven_shadow.py`) | Breakeven lock value | paired bootstrap | **No** — no writer |
| Per-signal replay (`analytics/signal_replay.py`) | Plan outcome on 1m bars | deterministic replay | CLI; baseline helpers used only in tests |

**Defects found**

1. Wrong question: the conviction-gate research estimates the gate's "causal effect" on candidate outcomes, which is zero by construction (a price taker's selection does not change prices); the value of a selection policy is directly computable from full-denominator outcomes. A deterministic gate also violates the positivity IPS/DR need ([research/counterfactual/05](../../../research/counterfactual/05_rdd_gate_validity_2026-07-31.md) is a band mean difference, not a local RDD).
2. Three causal methods are dead or run on frozen data (shadow_trades stack, twin/relative-rank builders, exit breakeven).
3. Duplicates: 5 interval/bootstrap implementations, 3 hash-assignment functions, 3 shrinkage helpers, `_percentile` ×8; IID bootstrap is retired in `verdicts.py:577` but still used on overlapping outcomes in `filter_replay.py:936`.
4. Contradictory authorities: Lab "is" vs "is not yet" the only experiment authority (Lab contract L20 vs counterfactual contract L16-21; migration default `'legacy'`); Twin contract names `universe-step-v2` as authority although that path raises `candidate_selection_legacy_runtime_removed`.
5. Vocabulary: "shadow" has 7 meanings, "counterfactual" 3, "anytime" 3.
6. Unanswered product questions: does the AI's judgment beat a naive or random entry; does each admission gate help; does an exit rule help; how many hypotheses were tried before one looked good.

Sources: industry practice digest (OPE, switchback, always-valid inference, CUPED, López de Prado
*Causal Factor Investing*), cited in [design.md](design.md#alternatives-considered).

## Goals and non-goals

Goals:

1. Every counterfactual question uses the cheapest method that actually identifies it.
2. One implementation each of replay, assignment, interval inference and shrinkage.
3. Every causal claim states its estimand, class, evidence rung and multiplicity, so no report overstates.
4. The product's open causal questions (AI value, gate value, exit value) get a defined method.

Non-goals:

- No change to Risk, Execution, Live, credentials, or the runtime authority of Candidate Selection Utility.
- No new statistical machinery beyond what replaces duplicates (no causal forests, no OPE platform).
- No runtime experiment is opened by this spec.

## Glossary

| Term | Definition |
|---|---|
| Market counterfactual | Outcome that depends only on prices after the decision (price-taker): fully observable by recompute or replay |
| Execution counterfactual | Whether/at what price an order would have filled; partially observable; ground truth is a real fill |
| Agent counterfactual | What the AI would have decided under another context, prompt, model or Playbook; unobserved unless re-run or randomized |
| Portfolio counterfactual | Effect of one decision on others through shared capital, caps or occupancy (interference) |
| Evidence rung | E0 association · E1 full-information recompute/replay · E2 offline agent re-run · E3 online randomized |
| Claim | Any persisted report, verdict, gate input or recommendation that compares alternatives |
| Research pool | Data pooled across epochs for exploration; never an experiment arm |

## Requirements

### CE-R01 Every claim declares itself

`The system shall persist, with every claim, its estimand, counterfactual class, evidence rung, unit, epoch scope and multiplicity-registry id.`

#### Scenarios

- WHEN a report omits the class or rung THEN it fails its producer's validation and is not shown as evidence.
- WHEN an E0 claim is displayed THEN it is labelled "association".

### CE-R02 Cheapest identifying method

`The system shall evaluate market counterfactuals by full-denominator recompute or replay, and shall not use IPS, DR or RDD unless the logging decision was stochastic with logged propensities and the outcome is otherwise unobserved.`

#### Scenarios

- WHEN a selection or gate rule is evaluated THEN its value is the recomputed outcome of the candidates it would select, with no propensity model.
- WHEN a deterministic gate is analysed THEN any IPS/DR result is rejected for positivity failure.

### CE-R03 Complete denominator

`The market-counterfactual ledger shall hold every produced candidate in every direction with outcome windows starting after the decision time, and shall report attrition.`

#### Scenarios

- WHEN a candidate is filtered, expired or unselected THEN its fixed-horizon outcome is still settled.
- WHEN outcome bars are unavailable THEN the row is `unavailable`, never zero.

### CE-R04 One replay

`The system shall compute every plan, exit or tactic replay through core/aletheia/analytics/signal_replay.py and record its replay version.`

#### Scenarios

- WHEN a second replay loop over bars computes fills or exits THEN it is a conformance defect.

### CE-R05 Execution counterfactuals are bounded, not guessed

`If an entry tactic cannot be represented on the stored bars, then the system shall mark its execution counterfactual not_replayable and shall report replay-vs-fill fidelity per tactic from real fills.`

#### Scenarios

- WHEN a Post-Only order would be through the market at placement THEN the replay records no fill.

### CE-R06 Agent counterfactuals offline

`When an agent counterfactual is estimated offline, the system shall re-run the pinned model on frozen inputs dated after the model's knowledge cutoff, with at least the configured number of samples per input, and report sample variance.`

#### Scenarios

- WHEN an input predates the model's knowledge cutoff THEN it is excluded from the re-run set.

### CE-R07 One assignment function

`The system shall assign every online treatment through the Observation Lab manifest hash, at the unit where interference stops, and check sample-ratio mismatch.`

#### Scenarios

- WHEN observed arm shares deviate from the declared split beyond the SRM threshold THEN the experiment's verdicts are invalid until explained.

### CE-R08 Inference matches dependence

`The system shall compute intervals and tests with cluster-level randomization inference or cluster/block bootstrap, shall not use IID bootstrap on overlapping or clustered outcomes, and shall use anytime-valid procedures wherever results are monitored before a pre-registered look.`

#### Scenarios

- WHEN 4h outcomes from one scanner run are tested THEN the run, not the row, is the resampling unit.

### CE-R09 Covariates are pre-assignment

`Where variance reduction is used, the system shall adjust only for covariates fixed before assignment.`

#### Scenarios

- WHEN a covariate is measured after the decision (fill, MAE) THEN it is rejected as an adjustment covariate.

### CE-R10 Epoch stratification

`The system shall not pool experiment units across model, prompt or policy epochs without a declared stationarity test; pooled exploratory summaries shall be labelled research pool.`

#### Scenarios

- WHEN the Entry Planner model identity changes THEN a new stratum begins and old units are not merged into its arms.

### CE-R11 Multiplicity registry

`The system shall register every rule, threshold or variant it evaluates before evaluation and shall apply a multiplicity-adjusted threshold over the registry count to any promotion.`

#### Scenarios

- WHEN the 40th variant of a gate is tested THEN its promotion threshold accounts for all 40.

### CE-R12 Forward-only confirmation

`When a rule is discovered on historical data, the system shall confirm it only on data dated after its registration.`

#### Scenarios

- WHEN a confirmation window overlaps the discovery window THEN the confirmation is invalid.

### CE-R13 Collider guard

`If an estimate conditions on a post-decision state (selected, filled, survived Risk, closed), then the system shall label it conditional and shall not use it as the value of the earlier decision.`

#### Scenarios

- WHEN win rate of filled trades is compared across gates THEN it is labelled conditional, not gate value.

### CE-R14 Interference

`If a claim compares realised account P&L between arms that share capital or caps, then the system shall use time-block switchback assignment or per-arm caps; otherwise the primary metric shall be portfolio-free replay R.`

#### Scenarios

- WHEN the Playbook arms of spec 0001 are compared THEN the primary metric is judgment R, not account P&L.

### CE-R15 AI value baselines

`The system shall report, on the same candidates, the AI's judgment R against a random-direction baseline and a deterministic mechanism-rule baseline, per model stratum.`

#### Scenarios

- WHEN the AI's judgment R is not distinguishable from the random baseline THEN the report says so; it never reports AI value without the baselines.

### CE-R16 Gate and exit value by replay

`The system shall evaluate each admission gate and each exit rule by replaying the plans or positions it changed against the same plans without it.`

#### Scenarios

- WHEN the stop-floor gate rejects compiled plans THEN its value is the replayed judgment R of those plans versus zero.

### CE-R17 Every method has a consumer

`If a causal or counterfactual module has no decision consumer and no owner-review consumer, then it shall be deleted with its tests in the same change.`

#### Scenarios

- WHEN a producer's input table has no writer THEN the producer is deleted, not kept for history; history stays in the table.

### CE-R18 Controlled vocabulary

`The system shall use "shadow" only for a treatment that is computed but not applied, and "counterfactual" only with one of the four classes.`

#### Scenarios

- WHEN a new table or module name uses "shadow" for an observation snapshot THEN it is rejected in review.

## Contract Delta

### ADDED

- `counterfactual_contract.md`: the four counterfactual classes, evidence rungs E0–E3, claim declaration, method rule, multiplicity registry, forward-only confirmation, collider guard, interference rule (CE-R01–R03, R10–R14, R18).
- `counterfactual_contract.md`: AI value baselines and gate/exit value by replay (CE-R15, R16).
- `observation_lab_contract.md`: SRM check and unit-where-interference-stops rule (CE-R07).

### MODIFIED

- `counterfactual_contract.md` becomes the single authority for causal method; the L0–L5 ladder of the research guidance is replaced by classes × rungs (L0–L1 integrity remain as preconditions).
- `observation_lab_contract.md` / `counterfactual_contract.md`: one statement of current experiment authority, set from verified runtime fact (CE-R07).
- Filter replay and filter shadow intervals use cluster inference (CE-R08).

### REMOVED

- `shadow_trades`-based calibration, propensity, IPS/DR estimators and advisories (`extensions/counterfactual/{calibration,propensity,estimators,advisories}.py`) and their CLIs; the table stays as history.
- Twin and relative-rank prospective builders (`build_prospective_*_bundle`, no production caller) and their own assignment functions; their evaluators stay only while the alpha-attribution owner report reads them (CE-R17).
- `universe_twin_shadow_contract.md` moves to `docs/design/history/`; the `universe_twin_shadow_records` table semantics that Candidate Selection Utility still writes move into `candidate_selection_utility_contract.md`.
- `exit_breakeven_shadow` reader (no writer); `learning/contribution.aggregate_observations` (test-only).
- Duplicate shrinkage, bootstrap, percentile and hash-assignment helpers.
- `subsystems/causal_counterfactual_research_guidance.md` (superseded by this spec); `docs/research/counterfactual/*` marked historical.

## Clarify

| Question | Answer |
|---|---|
| Error path | Missing bars ⇒ `unavailable`; unrepresentable tactic ⇒ `not_replayable`; SRM breach ⇒ verdicts invalid; missing declaration ⇒ claim not shown |
| Empty state | No units ⇒ `insufficient` with counts; no registry entry ⇒ no promotion possible |
| Boundaries | Configurable SRM threshold, sample count for offline re-runs, multiplicity method (e-BH or deflated threshold); defaults conservative |
| Concurrency | Registry writes are append-only with unique claim ids; assignment is a pure hash |
| Idempotency | Recompute/replay with the same version and inputs gives the same result |
| Authority | Claims never change behavior by themselves; behavior changes remain owner-admitted per the owning contract |
| Observability | Each claim shows class, rung, estimand, unit, epoch and how many variants were tried |

## Open questions

- [NEEDS CLARIFICATION: Owner — approve deleting the dead/frozen causal stack listed under REMOVED (history tables are kept)?]
- [NEEDS CLARIFICATION: Owner — Candidate Selection Utility stays the live selection authority as an E1 claim with forward-only confirmation (CE-R12), instead of adding a randomized holdout. Agree?]
- Verified on Fedora read-only 2026-10-10: `observation_authority_state.experiment_authority = lab` since 2026-09-03 (cutover proof recorded); `execution_manifests` = 0 rows, `manifest_revocations` = 0. No Observation Lab treatment has ever changed behavior; documents stating authority is still `legacy` are stale.

## Decision log

- 2026-10-10 — Owner: extract causal inference and counterfactual analysis into one standalone spec in the SDD format, like 0001.
