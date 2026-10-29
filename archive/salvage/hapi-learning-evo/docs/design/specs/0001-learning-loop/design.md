# 0001 — Decision-level learning from own mistakes: design

Answers [spec.md](spec.md). Every section cites the requirement ids it serves.

## Overview

```text
Entry Planner decision (frozen input + EntryHypothesis, both arms same schema)      LL-R01
  → labels: judgment R (compiled geometry replay) + system R (realised)             LL-R02..R05
  → review: code error types → blind LLM grade → outcome-aware grade                LL-R06..R08
  → drafting: rate-above-baseline per scope → add/edit/retire delta                 LL-R10
  → admission: field-level linter + scope check                                     LL-R11
  → Playbook (one table, versions, counters)                                        LL-R09, R15
  → advisory context: ≤5 matched rules, shown only in playbook_on                   LL-R12..R14
  → measurement: planning-thread randomisation, anytime harm stop, process metrics  LL-R16..R18
  → evolution: offline re-run before any online Playbook/prompt change              LL-R20
  → (separate) authority gate for AVOID+trigger rules                               LL-R19
```

The design reuses `core/aletheia/analytics/signal_replay.py` (the one replay/cost model,
including `random_entry_baseline` and `block_bootstrap_mean_ci`), the frozen
`EntryAnalysisContext` (`core/aletheia/trading/entry_analysis.py:125`), the compiler
(`core/aletheia/trading/entry_plan_compiler.py`), `core/aletheia/data/history_downloader.py`
and the Observation Lab manifest/receipt/assignment machinery.

## Data model

Additive migrations only; existing tables keep their history.

| Table | Key | Content | Requirements |
|---|---|---|---|
| `decision_labels` | `hypothesis_id` | judgment label (status, net R, MFE/MAE R, exit reason, ambiguity, replay version, bar source), system label, `planning_identity`, arm, model identity | LL-R02–R05 |
| `decision_reviews` | `hypothesis_id`, `review_version` | code error types, blind grade, outcome-aware grade, reviewer model, cost | LL-R06, R07, R22 |
| `reviewer_calibration_runs` | run id | placebo rates, real rates, κ, reliable flag | LL-R08 |
| `playbook_rules` | `rule_id`, `version` | fields of LL-R09; append-only versions; `status` | LL-R09, R11, R15 |
| `playbook_exposures` | `hypothesis_id`, `rule_id` | matched (both arms), shown, cited, followed (code), followed (self), consistency | LL-R12, R14, R21 |
| `playbook_arm_state` | singleton per stratum | arm assignment salt, harm e-value, on/off, reason | LL-R16, R17 |

`EntryHypothesis` gains `p_target_first: float` and `lessons_considered: tuple[...]` (LL-R01);
the existing `learning_evidence_refs` carries `playbook:<id>` citations.

## Components and flow

### Labeller (LL-R02, R03, R04, R05)

- Trigger: hypothesis compiled (or compile failure) → enqueue; label when the geometry's
  time barrier ends.
- Signal from the compiled geometry: entry type/price, stop, target, time barrier, invalidation
  price, venue fees and funding. Admission gates are not applied (judgment label).
- Replayability table by `execution_mode × order_tactic`: market and plain limit/stop are
  replayable; TRIGGERED anchor-retrace-reclaim and the 60s Post-Only leg are `not_replayable`
  in v1. Post-Only through the market at placement ⇒ unfilled. Fill through stop ⇒ −1R − costs.
  Decision bar = first bar ≥ ceil_to_minute(hypothesis creation time).
- Bars: read local 1m; if incomplete, backfill through `history_downloader` for the plan's
  venue; failure ⇒ `unavailable`.
- System label: realised position net R from settlement when filled; 0 otherwise.
- Replay fidelity is measured per `order_tactic` against filled positions and reported with
  LL-R18; a large bias restricts judgment labels to arm comparison only.

### Reviewer (LL-R06, R07, R08, R22)

1. Code types: STOP_PLACEMENT (MAE ≥ 1R before MFE reaches target, stop within 1 ATR),
   TIMING (MFE reached target after stop-out / entry far from best within window),
   COST_DOMINATED (planned reward < 3× round-trip cost), EXECUTION (fill vs plan price beyond
   tolerance).
2. Blind LLM pass: frozen input + hypothesis only → process grade and one of THESIS_WRONG,
   IGNORED_OPPOSING, REGIME_MISMATCH, NONE_LUCK.
3. Outcome-aware pass: adds path and labels; stored, not used as the process grade.
4. Placebo: periodically generate `random_entry_baseline` plans on the same instruments and
   windows, review them identically; compare type rates and compute κ across two runs/models.

Both winning and losing decisions are reviewed.

### Drafter and linter (LL-R10, R11)

- Per scope cell (mechanism × direction × regime × selection epoch), estimate each error
  type's rate with a beta-binomial shrinkage toward the placebo/cross-scope baseline; draft only
  when the posterior lower bound exceeds the baseline and sources span ≥3 planning threads,
  ≥2 symbols, ≥2 UTC dates.
- The LLM fills a fixed rule template (polarity, scope, optional trigger expression over frozen
  input fields, ≤200-char text). Output is a delta: add / edit / retire.
- Linter checks fields, not phrases: forbidden directions on stop, leverage, size, risk_intent,
  capture_priority, gates; scope ⊆ union of source scopes; trigger evaluates on frozen input;
  free text outside the template rejected.

### Context provider (LL-R12, R13, R14)

- For each Planning Generation, compute the matched rule set (active, scope match, trigger true
  or absent), rank by shrunk net helpfulness, keep ≤5, store for both arms; render only for
  `playbook_on`.
- Compliance: for trigger rules, followed = (AVOID ⇒ NO_PLAN or opposite direction;
  PREFER/ADJUST ⇒ the declared field matches). Self-report compared for consistency.
- Nothing in this path reaches the compiler, Risk or Gateway.

### Counters (LL-R15)

- Only decisions after the rule's `created_at`.
- AVOID followed: a counterfactual re-plan of the same frozen input with the Playbook withheld
  (one extra LLM call, cost charged per LL-R22) gives the plan the rule prevented; its judgment
  R is compared with same-scope non-matching plans' R (so "trading less" earns nothing by itself).
- PREFER/ADJUST: matched-and-followed vs matched-and-not-followed, shrunk; used for ranking only.

### Measurement (LL-R16, R17, R18)

Inference, interference and epoch rules follow [spec 0002](../0002-causal-evidence/spec.md)
(CE-R07, R08, R10, R14).

- New Observation Lab adapter `playbook_bundle`: unit = `planning_identity`, arm = hash(salt,
  unit), outcome contract = judgment R; exclusive with the `entry_learning` adapter on the same
  interference key; stratified by model identity; format failures stay as units.
- Harm: mixture-martingale e-process on the difference in judgment R, restricted to units with a
  non-empty matched set (both arms); crossing 1/α switches the Playbook off.
- Report LL-R18 metrics with cluster-robust (planning thread / UTC day) intervals.

Sample-size honesty: with σ ≈ 1.2–1.6 R, an anytime penalty ≈ ×1.55 and clustering ≈ ×2–3,
detecting 0.15R needs roughly 3,000–8,000 labelled units per arm. At ~255 hypotheses/day
before clustering, judgment-R efficacy takes months to a year; at ~8 admitted plans/day,
system-R efficacy takes years. Therefore efficacy is judged first on process metrics
(Brier, repeat-error rate, compliance), judgment R is the harm guardrail and secondary metric,
and system R is reported only.

### Authority gate (LL-R19)

AVOID + trigger only. Post-creation units; trigger-matched vs same-scope non-matched judgment
R; threshold deflated for the count of all rules ever evaluated (Benjamini–Hochberg over the
rule history, or Deflated-Sharpe-style adjustment); owner admission; the block is then a
static Risk rule with provenance, revocable by the same evaluation turning negative.

### Offline re-run (LL-R20)

Replay a fixed sample of frozen `EntryAnalysisContext` inputs dated after the model's knowledge
cutoff through the candidate Playbook/prompt, label with the Labeller, compare with the current
version on process metrics and judgment R. Budgeted by LL-R22 accounting.

### Owner surface (LL-R21)

Read-only views over `playbook_exposures`, `playbook_rules`, `playbook_arm_state` and
`decision_labels`; shown rules are read from the frozen planner input, not recomputed.

## Failure semantics

| Failure | Result |
|---|---|
| Bars missing and backfill fails | label `unavailable`; excluded from metrics with count shown |
| Tactic not replayable | `not_replayable`; excluded; per-tactic coverage reported |
| Reviewer LLM unavailable | decision stays unreviewed; nothing drafted from it |
| Reviewer unreliable (placebo/κ) | drafting paused; existing rules unchanged |
| Linter rejects | rule not stored as active; rejection reason kept |
| Lab adapter unavailable | all threads behave as `playbook_off` (empty section), recorded |
| Harm crossing | Playbook off for all threads; requires owner action to restart a new stratum |
| Model identity changes | new stratum; old e-process frozen |

## Trust and complexity review

- Authority: advisory rules reach only the planner prompt (LL-R13); blocking requires LL-R19
  and owner admission; linter blocks every size/leverage/stop/gate direction (LL-R11).
- Prompt-injection surface: rule text is template-generated and linted.
- Deletions (one implementation per capability): `ps_lessons` lifecycle and reviewer validation,
  `LearningHypothesis`, learned guards store, memo/reasoning/calibration/draft-skill/FTS advisory
  paths without decision consumers, per-artifact registry for entry learning.
- Kept: counterfactual ledger, `signal_replay.py`, Observation Lab machinery, settlement costs.
- Why not smaller: wiring lessons into the prompt alone (PR #3935) keeps outcome-only judging,
  polarity-less lessons and unmeasured effect — the three defects that make learning noise.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Keep per-artifact prospective trials (`learning_evolution.md`) | unreachable sample size per lesson; blocks the advisory path entirely |
| Learn from closed trades only (current, AstraQuant) | dozens per month; outcome-only; no counterfactual |
| AstraQuant LLM position multiplier | unbounded noise applied to real margin, no rollback |
| AstraQuant in-sample health score | scores rules on the data that produced them |
| Free-text memory rewrite each cycle | context collapse (ACE); replaced by add/edit/retire deltas |
| Rule from ≥3 same-type decisions | ~60% false-rule rate in busy cells (red-team); replaced by rate-above-baseline |
| Reuse the `entry_learning` adapter as is | its context DTO and 4h fill-T0 outcome cannot carry rules or R; new adapter instead |
| Shadow plan written by the declining call | self-serving; replaced by withheld-Playbook re-plan |

## Traceability

| Requirement | Design section | Verification | Evidence type |
|---|---|---|---|
| LL-R01 | Data model | `tests/specs/spec_learning_loop.py::LL-R01` | deterministic test |
| LL-R02 | Labeller | `::LL-R02` gated plan still labelled | deterministic test |
| LL-R03 | Labeller | `::LL-R03` Post-Only through market, fill-through-stop, ceil decision bar | deterministic test |
| LL-R04 | Labeller | `::LL-R04` missing bars → backfill → unavailable | deterministic test |
| LL-R05 | Labeller | `::LL-R05` | deterministic test |
| LL-R06 | Reviewer | `::LL-R06` blind pass input excludes path | deterministic test |
| LL-R07 | Reviewer | `::LL-R07` code types; NONE_LUCK not a source | deterministic test |
| LL-R08 | Reviewer | `::LL-R08` placebo indistinguishable → drafting paused | deterministic test |
| LL-R09 | Data model | `::LL-R09` edit = new version, old retired | deterministic test |
| LL-R10 | Drafter | `::LL-R10` single-thread sources rejected; rewrite rejected | deterministic test |
| LL-R11 | Drafter and linter | `::LL-R11` forbidden directions, scope widening, epoch change | deterministic test |
| LL-R12 | Context provider | `::LL-R12` matched stored both arms, shown only on | deterministic test |
| LL-R13 | Context provider | `::LL-R13` AVOID match does not alter compiler/Risk input | deterministic test |
| LL-R14 | Context provider | `::LL-R14` consistency miss recorded | deterministic test |
| LL-R15 | Counters | `::LL-R15` pre-creation decisions ignored; retirement rules | deterministic test |
| LL-R16 | Measurement | `::LL-R16` stable arm per thread; exclusive with entry_learning | deterministic test |
| LL-R17 | Measurement | `::LL-R17` harm crossing switches off; efficacy grants nothing | deterministic test |
| LL-R18 | Measurement | `::LL-R18` insufficient below minimum; clustered CI | deterministic test + runtime report |
| LL-R19 | Authority gate | `::LL-R19` "trade less" refused; owner admission required | deterministic test |
| LL-R20 | Offline re-run | `::LL-R20` worse offline score not deployed | deterministic test |
| LL-R21 | Owner surface | `::LL-R21` shown rules equal frozen input | API round-trip test |
| LL-R22 | Reviewer, Counters | `::LL-R22` cost unavailable ≠ 0 | deterministic test |

## Rollout and rollback

- Order of dependency (execution tracked in GitHub under #3934): labels and reviewer
  (LL-R01–R08) → Playbook, linter, context, compliance (LL-R09–R15) → Lab adapter and harm
  stop (LL-R16–R18) → owner surface and cost (LL-R21–R22) → authority gate and offline re-run
  (LL-R19–R20).
- Labels and reviews run before any rule is shown; the first Playbook exposure starts with an
  owner-authorised Lab window on an exact deployed commit.
- Rollback: switch `playbook_arm_state` off (all threads behave as `playbook_off`); tables are
  additive and remain as history. Retired stores are deleted in code with their consumers in the
  same change, per convergence policy.
- Known measurement uncertainties to resolve with the first data, not assumptions: replay bias
  per tactic; planning-thread throughput; reviewer placebo separation; LLM compliance rate.
