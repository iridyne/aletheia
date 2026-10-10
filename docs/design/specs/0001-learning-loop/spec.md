---
id: 0001-learning-loop
title: Decision-level learning from own mistakes
status: approved
depth: D2
prefix: LL
issue: 3934
contracts:
  - ../../contracts/learning_contract.md
  - ../../contracts/entry-plan-contract.md
  - ../../contracts/knowledge_contract.md
supersedes:
  - ../../proposals/learning_evolution.md
superseded_by: []
---

# 0001 — Decision-level learning from own mistakes

## Summary

Aletheia learns like a discretionary trader: every Entry Planner decision is labelled with a
uniform cost-aware R outcome, reviewed for *process* before *outcome*, and recurring mistakes
become short, scoped, polarised rules in one Playbook. Up to five matching rules are shown to
the Entry Planner as advice (no blocking, no sizing); the whole Playbook is switched on/off by
randomisation so its effect is measured, and it is switched off automatically when it harms.
Rules can gain blocking authority only through a separate, strict, owner-admitted gate.

## Problem and evidence

- The current design has no legitimate path from a lesson to the next entry decision: every
  influence requires per-artifact prospective measurement, and the entry-prompt bundle is
  declared unmeasurable (evaluation G1,
  [LEARNING_DESIGN_EVALUATION_2026-10-10](../../../research/agents-learning/LEARNING_DESIGN_EVALUATION_2026-10-10.md)).
- Lessons are learned from closed trades (dozens per month) and judged by outcome only; the
  reviewer never sees the decision's reasons (`extensions/research/dreaming_agent.py:324-336`).
- Lessons carry no polarity, so a confirmed "avoid" lesson is demoted by its own evidence
  (`extensions/research/lesson_reviewer.py:279-289`); a 30-day TTL expires correct lessons.
- Seven overlapping stores and two lifecycles exist for one concept (evaluation G6).
- The competitor AstraQuant learns by LLM re-reading its ledger, in-sample self-scoring and an
  unbounded LLM position multiplier, with no counterfactual and no improvement measurement
  ([solution research §2](../../../research/agents-learning/LEARNING_LOOP_SOLUTION_2026-10-10.md)).
- Published evidence that reflection/memory improves out-of-sample trading P&L is weak to
  absent; memory degrades under noisy labels (evaluation §5).
- Measured throughput: ~255 Entry hypotheses per 24h, but only ~8 admitted Entry Plans per day
  (`docs/research/audits/OPPORTUNITY_LIFECYCLE_CAPABILITY_TRACKING_2026-10-09.md`, §1.2–1.3);
  1m bars are retained 2 days (`core/aletheia/storage/repositories.py:6208-6215`).

## Goals and non-goals

Goals:

1. The AI's mistakes are identified by process, not by profit alone.
2. Recurring mistakes become scoped rules that the next decision actually reads and answers.
3. Whether the Playbook helps is measured against a randomised control; harm stops it.
4. One store, one lifecycle, one outcome unit for the whole learning loop.

Non-goals:

- No change to Risk limits, leverage bounds, credentials, Live unlock or the Execution Gateway.
- No automatic position-size or leverage learning (AstraQuant's multiplier is rejected).
- Universe family calibration and filter shadow experiments are outside this loop; their
  future is decided separately.
- No claim that the loop will produce positive returns; that is an empirical result.

## Glossary

| Term | Definition |
|---|---|
| Decision | One `EntryHypothesis` produced by the Entry Planner for one Planning Generation |
| Planning thread | All Planning Generations sharing one `planning_identity`; the independent unit |
| R | Net return divided by the planned stop distance; +2R = twice the initial risk |
| Judgment label | Replayed net R of the deterministic compiled geometry of an `ENTRY_PLAN` decision, ignoring admission gates |
| System label | Realised utility: actual position net R if filled, 0 if gated, not filled or `NO_PLAN` |
| Playbook | The single learning store: short rules with polarity, scope, optional trigger and counters |
| Advisory tier | Playbook rules shown to the Entry Planner; they cannot block or change size |
| Authority tier | A code-enforced block derived from an `AVOID` rule after the strict gate |
| Arm | `playbook_on` or `playbook_off` assignment of a planning thread |

## Requirements

### LL-R01 Uniform decision record

`The Entry Planner shall emit, in both arms, p_target_first (0..1) and lessons_considered (a list of {rule_id, followed, why}, empty when no rule was shown) on every EntryHypothesis.`

#### Scenarios

- WHEN the arm is `playbook_off` THEN `lessons_considered` is `[]` and `p_target_first` is still required.
- WHEN a model omits either field THEN the hypothesis fails validation and is counted as an arm unit with outcome "format failure", not dropped.

### LL-R02 Judgment label for every ENTRY_PLAN decision

`When an ENTRY_PLAN decision is compiled, the system shall replay its deterministic geometry on 1m bars and record net R, MFE/MAE in R, exit reason and ambiguity, regardless of admission-gate outcome.`

#### Scenarios

- WHEN the stop-floor gate rejects a compiled plan THEN its judgment label is still produced.
- WHEN the geometry cannot be compiled THEN the label is `not_replayable` with the compiler reason.

### LL-R03 Replay fidelity rules

`The replay shall only label entry tactics declared replayable; it shall reject a Post-Only order already through the market, cancel on invalidation_price before fill, book a fill-through-stop as −1R minus costs, and start at the first bar at or after ceil(decision time).`

#### Scenarios

- WHEN the tactic is TRIGGERED anchor-retrace-reclaim or the Post-Only leg of MAKER_PREFERRED THEN the label is `not_replayable` unless an approximation is declared and flagged in `ambiguity`.
- WHEN the first eligible bar opens beyond the stop THEN net R ≤ −1.

### LL-R04 Bars are secured before they expire

`If the 1m bars needed for a label are missing from local storage, then the system shall backfill them from the venue history API before labelling, and shall mark the label unavailable (never zero) when backfill fails.`

#### Scenarios

- WHEN a plan's horizon ends THEN the label is computed within the 1m retention window or after backfill.
- WHEN the replay venue differs from the decision's market-data venue THEN the label is `not_replayable`.

### LL-R05 System label reported separately

`The system shall record the system label for every decision and report it separately from the judgment label.`

#### Scenarios

- WHEN a decision is `NO_PLAN` or gated THEN system label = 0 and judgment label is unaffected.

### LL-R06 Blind process review first

`When a decision's judgment label settles, the reviewer shall first grade the decision from the frozen input and hypothesis alone, and only then receive the price path and labels.`

#### Scenarios

- WHEN the blind grade and the outcome-aware grade disagree THEN both are stored; the blind grade is the process grade.

### LL-R07 Code-determined error types

`The system shall determine STOP_PLACEMENT, TIMING, COST_DOMINATED and EXECUTION error types by deterministic rules; the LLM reviewer shall only assign THESIS_WRONG, IGNORED_OPPOSING, REGIME_MISMATCH or NONE_LUCK.`

#### Scenarios

- WHEN MAE ≥ 1R before MFE reaches the target and the stop was within one ATR of entry THEN `STOP_PLACEMENT` is set by code.
- WHEN the error type is `NONE_LUCK` THEN no rule may cite the decision as a source.

### LL-R08 Reviewer placebo and agreement

`If the reviewer's error-type rates on random-entry placebo plans are not distinguishable from its rates on real decisions, or inter-run agreement κ is below the configured floor, then the system shall mark reviewer output unreliable and draft no rules.`

#### Scenarios

- WHEN the placebo batch has not run in the configured interval THEN rule drafting pauses.

### LL-R09 One Playbook store

`The system shall keep every learned rule in one Playbook with fields id, version, polarity (AVOID/PREFER/ADJUST), scope (mechanism, direction, regime, selection policy epoch), optional trigger, text, source decision ids, helpful, harmful, last_used_at, status (candidate/active/retired).`

#### Scenarios

- WHEN a rule is edited THEN a new version is written and the previous version is retired, never deleted.

### LL-R10 Rule drafting threshold

`When an error type's rate in a scope exceeds its placebo-and-cross-scope baseline after shrinkage, with sources spanning at least three planning threads, two symbols and two UTC dates, the system shall draft one candidate rule or one edit to an existing rule.`

#### Scenarios

- WHEN three sources come from one planning thread THEN no rule is drafted.
- WHEN a batch proposes a full rewrite of the Playbook THEN it is rejected; only add/edit/retire deltas are accepted.

### LL-R11 Field-level admission linter

`If a candidate rule would widen a stop, raise leverage or size, push risk_intent above BASELINE, push capture_priority toward FILL_FIRST, lower a gate, or carry a scope broader than its sources, then the system shall reject it.`

#### Scenarios

- WHEN rule text contains instructions outside the rule template (prompt injection) THEN it is rejected.
- WHEN a selection policy epoch changes THEN active rules scoped to the old epoch return to `candidate`.

### LL-R12 Advisory application

`While the arm is playbook_on, the Entry Planner context shall include at most five active rules whose scope matches the frozen input, citable as playbook:<id>; the matching set shall be computed and stored for both arms.`

#### Scenarios

- WHEN the arm is `playbook_off` THEN the matched set is stored but not shown.
- WHEN no rule matches THEN the context section is present and empty.

### LL-R13 Advisory never acts

`The Playbook shall not block a candidate, alter risk_intent, leverage, size, stop or target, or bypass any Risk check.`

#### Scenarios

- WHEN an active `AVOID` rule matches and the AI still plans THEN the plan proceeds through the normal gates unchanged.

### LL-R14 Verified compliance

`When a shown rule has a trigger, the system shall determine followed from the trigger value and the final decision; the AI's self-report shall only be scored for consistency.`

#### Scenarios

- WHEN the AI reports `followed=true` but the code determination is false THEN a consistency miss is recorded.

### LL-R15 Counters rank, they do not prove

`The system shall update helpful/harmful counters from post-creation decisions only and use them only to rank and retire rules.`

#### Scenarios

- WHEN an `AVOID` rule is followed THEN credit compares the counterfactual re-plan (same frozen input, playbook withheld) against same-scope non-matching plans, not against zero.
- WHEN harmful exceeds helpful after shrinkage with n ≥ 10, or a rule is unused for 60 days, THEN it is retired; a candidate not activated in 30 days is retired.

### LL-R16 Bundle randomisation

`The system shall assign each planning thread to playbook_on or playbook_off by a stable hash through a dedicated Observation Lab adapter that is mutually exclusive with the entry_learning adapter.`

#### Scenarios

- WHEN a planning thread is re-planned THEN it keeps its arm.
- WHEN the model identity changes THEN a new stratum starts.

### LL-R17 Anytime harm stop

`If the anytime-valid harm test on judgment R among rule-matched units crosses its threshold, then the system shall switch the Playbook off for all threads and record why.`

#### Scenarios

- WHEN the harm test is positive for efficacy THEN no authority expands; the result is reported only.

### LL-R18 Success is measured by process first

`The system shall report, per arm and per model stratum, calibration (Brier score of p_target_first), repeat-error rate of non-NONE_LUCK types, compliance rate, judgment R and system R, with clustered confidence intervals.`

#### Scenarios

- WHEN fewer units than the declared minimum exist THEN the report says `insufficient` for that metric.

### LL-R19 Authority tier gate

`Where an AVOID rule with a trigger requests blocking authority, the system shall grant it only after post-creation evaluation shows trigger-matched plans worse than same-scope non-matched plans beyond a threshold deflated for every rule ever tested, and after owner admission.`

#### Scenarios

- WHEN the evaluation only shows that fewer trades lose less THEN authority is refused.

### LL-R20 Offline re-run before online change

`When a new Playbook version or Entry Planner prompt is proposed for online use, the system shall first re-run it on frozen inputs dated after the model's knowledge cutoff and score it with judgment labels.`

#### Scenarios

- WHEN the offline score is worse than the current version THEN it is not deployed to an arm.

### LL-R21 Owner visibility

`The system shall show, per decision, the matched, shown, cited and followed rules, and per rule its sources, counters and status, and the current arm and harm state.`

#### Scenarios

- WHEN the owner opens a decision THEN the shown rules equal exactly the rules in the frozen planner input.

### LL-R22 Learning cost accounting

`The system shall record the model and data cost of review, placebo and counterfactual re-plan calls and charge it to the arm that caused it.`

#### Scenarios

- WHEN cost data is unavailable THEN cost is `unavailable`, never zero.

## Contract Delta

### ADDED

- `learning_contract.md`: Playbook store and lifecycle (LL-R09–R11, R15); judgment/system labels (LL-R02–R05); process review (LL-R06–R08); bundle randomisation and harm stop (LL-R16–R18); authority gate (LL-R19); offline re-run (LL-R20).
- `entry-plan-contract.md`: `EntryHypothesis.p_target_first`, `lessons_considered`; Playbook context section and its two-arm matching (LL-R01, R12, R14).

### MODIFIED

- `learning_contract.md` §1.1/§1.2: advisory context no longer requires per-artifact measurement; one bundle-level measured path replaces the per-path registry for entry learning.
- `learning_contract.md` §11: the learning outcome unit is the judgment label in R, not 24h/4h forward returns or ±0.5% case labels.
- `learning_contract.md` §12: the bounded Entry Learning context and the Playbook adapter are mutually exclusive treatments.

### REMOVED

- `ps_lessons` lifecycle, 24h dry run and 30-day TTL (`learning_contract.md` §2).
- `LearningHypothesis` 12-state lifecycle (`learning_evolution.md` §3–§7, superseded).
- `lesson_reviewer` forward-return validation and deterministic win-rate demotion.
- Learned (auto-generated) guards as a separate store; they become `AVOID` rules with triggers.
- Memo, reasoning-feedback, calibration-feedback, draft-skill and FTS advisory paths that have no decision consumer (`knowledge_contract.md`); calibration comes from LL-R18.

## Clarify

| Question | Answer |
|---|---|
| Error path | Unlabellable decisions are `not_replayable`/`unavailable` with a reason (LL-R02–R04); reviewer failures leave the decision unreviewed and draft nothing (LL-R08); harm stops the Playbook (LL-R17) |
| Empty state | Empty Playbook ⇒ empty context section in both arms; the loop still labels and reviews |
| Boundaries | ≤5 rules shown; rule text ≤200 characters; κ floor, placebo interval, shrinkage prior and harm threshold are configuration with conservative defaults |
| Concurrency | One writer per Playbook version (compare-and-set on version); labelling is idempotent per decision id |
| Idempotency | Re-running labelling, review or drafting on the same inputs produces the same records; source ids count once |
| Authority | Advisory cannot act (LL-R13); blocking only via LL-R19 with owner admission; no change to Risk, leverage, Live or credentials |
| Observability | LL-R18 metrics and LL-R21 per-decision trace; harm state and arm are visible |

## Open questions

None.

## Decision log

- 2026-10-10 — Owner: advisory lessons may enter the entry prompt without per-artifact measurement; blocking/sizing keep strict gates ([DECISIONS](../../../governance/decisions/DECISIONS.md)).
- 2026-10-10 — Owner approved B (decision + replay R as learning unit), C (new EntryHypothesis fields), D (one Playbook replaces existing stores).
- 2026-10-10 — Owner: new designs use this SDD spec format as the golden standard.
