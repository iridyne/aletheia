# Issue #2325 Packet 0 — Source / T0 / Path / Contract Audit

## Audit identity and change contract

- Issue baseline: `dccf0a8bcd683b62a0ebd94dbe8d2f8fd278c0a7`.
- Re-audited current `master`/HEAD: `45159140229c88a1226340b508ccf34d9f92ef95`.
- Baseline delta: 338 files changed. Learning-specific changes are limited to `demo_entry_quality.py` and `policy_epochs.py`; no Packet 1–8 semantic contract, nomination, T0, compiler, Economic Knowledge, Yield, or Meta-Evolution implementation already exists.
- SDD depth: **D2**, authorized by accepted `docs/design/proposals/learning_evolution.md` and canonical Issue #2325.
- Contract Delta: **ADDED** — immutable T0/Memory/Reflection/nomination contracts, one append-only nomination record, read-only Economic Knowledge/Yield/Meta projections, and read-only operator surfaces. **MODIFIED** — explicit research nomination becomes path/Consumer/T0 aware. **REMOVED** — Markdown-first and downstream-stage facts as automatic upstream causal evidence.
- Changed boundary: Learning research/qualification projection and handoff into the existing measured lifecycle.
- Unchanged boundary: Risk, Execution, Live, credentials, hard limits, order submission, current UFC behavior/metric/policy epoch/fixed-look history, Ranker Paper authority, and every existing static fallback.

Packet 0 itself is D0/no behavior change: it records current facts and establishes executable failures.

## Semantic findings

1. `ps_lessons`, policy versions, `ps_skills`, Knowledge Markdown and FTS are cognitive artifacts. Active/applied status is not an Experiment, intervention, Contribution verdict, or Economic Knowledge.
2. Shadow assigns arms but both execute baseline. Any apparent arm difference is trial-integrity noise and cannot establish Contribution.
3. Economic Knowledge can be a pure read-only projection over the existing hypothesis, experiment, exposure/cohort, fixed-look, verdict, lifecycle and authority facts. A second mutable verdict or qualification lifecycle is unnecessary and prohibited.
4. `learning_hypotheses` stores behavior identity once. It cannot honestly retain exact retries, rejected/insufficient/advisory attempts, changed evidence for the same behavior, generation cost, or many nominations to one hypothesis. One append-only nomination table is the minimum missing fact.
5. Current `hypothesis_research.py` is path-agnostic and reconstructs from trade-case Markdown frontmatter. It can include `PiDecision`/Operator reasoning after an upstream Ranker boundary. That data is future-stage information for Ranker.
6. Source/event time does not establish availability. `received_at > anchor_at` is future information even when publication time is earlier.
7. Case, review, memo, lesson, reflection and skill derived from one Position share one `root_outcome_identity`; they are one independent economic observation.
8. Ranker/Universe/Guard effects are population-relative. A selected Position without its contemporaneous candidate population cannot automatically nominate a score/filter delta.
9. Current UFC production evidence is a consumed **negative** fixed-look result and refrozen history. Positive UFC projection must be proved with deterministic fixtures only; it must not be described as current production Contribution.

## Current Source / Outcome / Retention map

| Producer / artifact | Canonical store / owner | Evidence class | Root identity | Outcome / measurement owner | Retention / replay truth | Current authority |
|---|---|---|---|---|---|---|
| Universe member/setup/opportunity generation | Universe snapshot/member/setup, `OpportunityGenerationV2`, Opportunity evidence manifest | observed + deterministic-derived | scanner run / generation / candidate | `universe_forward_outcomes`, UFC cohort adapter | durable bounded identities; feature telemetry may prune | none by artifact |
| Filter/ranker decision | `FilterDecision.input_snapshot`, `parameter_snapshot`, candidate outcome key | observed + deterministic-derived | exact filter decision/candidate | `filter_candidate_outcomes`; `cohorts.py` | decision rows durable; referenced feature snapshots normally 14 days | Shadow measurement for `ranker_case_score`; no Paper authority |
| Operator decision | `PiDecision.evidence_manifest_json`, `learning_manifest_json`, Proposal lineage | observed facts + model belief | exact decision/proposal | decision-linked or Position outcome only where declared | durable decision manifests | current Learning bundle path unsupported |
| Position and path observation | Position, attribution, `position_path_observations` | settled quantitative root + derived attribution | Position / exact path observation | realized/cost-aware outcome owners | permanent trust-bearing rows | evidence only |
| Trade case / post-trade review | `knowledge_items`, verified canonical Knowledge representation | narrative/analysis | referenced Position | never its own quantitative outcome | DB + Markdown provenance; #1839/#1894 constraints apply | none |
| Lesson / policy version | `ps_lessons`, lesson policy versions | structured cognitive evidence | source Position/outcome cohort | no independent outcome | durable, overturnable | artifact lifecycle only |
| Memo / factor / reasoning / calibration / reflection | their current analytic/Knowledge stores | model/derived belief | source decision/Position roots | no independent outcome | current retention per owner | advisory only |
| Skill | `ps_skills` + source lesson/policy lineage | inherited advisory artifact | inherited source roots | inherits source path; no independent vote | durable, human activation | advisory only; never auto-qualified |
| Hypothesis | `learning_hypotheses` | behavior identity | fingerprint + source cohort | existing Experiment ledger | durable | proposed/shadow identity only |
| Experiment / Exposure | `learning_experiments`, `learning_exposures` | prospective causal evidence | experiment + assignment unit | `cohorts.py` exact adapters | additive durable ledger | Shadow or owner-gated Paper only |
| Contribution / fixed look | `learning_contribution_verdicts`, `learning_inference_decisions` | monitoring/confirmatory evidence | exact cohort/inference identity | `verdicts.py`, `fixed_look.py` | immutable/consumed evidence | consumed verdict may support lifecycle; no separate Knowledge truth |
| Lifecycle / authority | lifecycle events, policy epochs, control/config | operational permission | exact path/experiment/stage/epoch | `lifecycle.py`, `authority.py`, `control.py` | additive audit + current config | only existing resolver may authorize |
| Observation Lab | canonical `app.db` plus bounded `lab.db` derived state | observation-window evidence | observation/window identity | existing Lab runner | separate worker ownership; not Learning truth duplication | none to Learning by implication |

Only exact `SETTLED_QUANTITATIVE` root outcomes add independent quantitative sample weight. Narrative/model artifacts may add explanations or contradictions, not samples.

## Path / T0 / Consumer / Measurement / Authority map

| `path_id` | Consumer and exact causal T0 | Canonical anchor / required decision-visible context | Current wiring / measurement / authority | Statistical unit / outcome / fallback / overlap | Gap and Packet disposition |
|---|---|---|---|---|---|
| `universe_family_calibration` | Universe allocator, immediately before allocation score multiplier | scanner run + generation + complete member population; features, market/regime, liquidity/cost, policy inputs | wired / supported / Paper-capable through existing owner gate | `universe_allocation_member`; exact fixed-horizon candidate outcome; multiplier 1.0; `universe` | reuse unchanged; first Economic Knowledge integration fixture |
| `ranker_case_score` | Paper Ranker, immediately before Ranker evaluation | exact FilterDecision/candidate + contemporaneous population/order/features/cost | wired / supported / **Paper unsupported** | `ranker_candidate`; exact 4h filter candidate outcome; zero delta; `ranker` | preferred Shadow handoff; missing population blocks nomination |
| `ranker_memo_score` | Paper Ranker before evaluation | same Ranker population plus exact memo version available then | wired / unsupported / none | future candidate outcome; zero memo delta; `ranker` | advisory only |
| `ranker_factor_filter` | Paper Ranker before filter/score | complete affected population, factor inputs, policy and cost | wired / unsupported / none | future candidate supply/outcome; disabled; `ranker` | advisory only |
| `ranker_learned_guard` | Paper Ranker immediately before learned guard | candidate + population + exact guard version/input | wired / unsupported / none | blocked-candidate outcome; observe-only; `ranker` | #1765-safe nomination required; no behavior authority |
| `observer_advisory_bundle` | Market Observer before prompt/input rendering | market/universe facts, exact Learning bundle and model/template | wired / unsupported / none | emitted-opportunity unit/outcome; empty bundle; `observer` | advisory only |
| `operator_learning_bundle` | System Operator immediately before prompt rendering | Opportunity generation, candidate facts, Risk/portfolio/account facts, exact bundle/model/template | wired / unsupported / none | decision-linked outcome; no bundle; `operator` | Operator facts must not leak upstream |
| `risk_lesson_constraint` | Risk evaluation before proposal decision | proposal, immutable Risk policy/limits and exact lesson input | wired / unsupported / no Learning authority | proposal/decision outcome; static Risk behavior; `risk_lesson` | out of #2325 behavior scope |
| `persona_sizing` | immediately before sizing calculation | proposal/decision, balance, exposure, immutable limits, liquidity/capacity, sizing inputs | wired / unsupported / none | future proposal/Position outcome; static persona baseline; `sizing` | advisory only |
| `advisory_skill` | before the declared prompt/ranker advisory consumer | inherited source Knowledge plus exact consumer manifest | not wired / unsupported / none | inherited source path; inactive skill; `skill` | cognitive retrieval only |
| `exit_policy_advice` | each position-management decision instant | exact `position_path_observation`, protection/market/policy state | read-only / unsupported / none | trajectory observation; no mutation; `diagnostic` | ordered T0 envelopes required; no authority |
| `filter_ttl_shadow` | filter decision boundary | exact filter decision/input/population | observation-only | fixed-horizon filter outcome; production unchanged; `filter_shadow` | not a Learning treatment |
| `operator_safety_control` | current safety-control decision | operator health/audit facts | existing control / not Learning | health outcome; existing contract; `concurrent_policy` | never withheld or credited to Learning |
| `trust_guard_observability` | pre-Risk audit context | exact trust/audit facts | observability only | audit facts; no effect; `trust_observability` | not Learning authority |
| `exit_trailing_evidence` | each trailing decision | Position/path observation and policy epoch | observer only / unsupported | Position; realized cost-complete PnL; static no-trailing control | no Learning authority |
| `entry_correction_v2` | entry decision boundary | exact Opportunity/decision/proposal lineage | observer only / unsupported | Position; realized cost-complete PnL; pre-correction baseline | no Learning authority |
| `filter_parameter_tune` | future exact filter check | not implemented | not wired / unsupported | future aggregate filter cell; static parameter | placeholder only; no Packet activation |

### T0 domain states and source classes

Every path-specific envelope must preserve `available`, `not_applicable`, `not_collected`, `stale`, `unavailable`, `integrity_conflict`, or `pruned`, and classify values as `observed_fact`, `deterministic_derived`, `model_belief`, or `human_or_policy_belief`. Required domains are code-declared before outcomes are seen. A bounded captured projection survives normal telemetry pruning; otherwise replay reports `pruned`/`reconstructed_partial` rather than substituting a later row.

## Experiment / authority reuse map

| Concern | Sole existing owner | #2325 action |
|---|---|---|
| Behavior identity | `LearningHypothesis`, fingerprint | retain; add nomination lineage beside it |
| Experiment identity/assignment/Exposure | `experiments.py`, `ledger.py` | reuse; explicit proposed→Shadow handoff only after preflight |
| Exact cohorts/outcomes/ITT | `cohorts.py` | read only; no changed-row filtering |
| Cost-aware Contribution/harm | `contribution_runner.py`, `verdicts.py` | read only |
| Confirmatory cutoff/consumption | `fixed_look.py` | read only |
| Staged Paper/refreeze | `lifecycle.py` | read only; project current death state |
| Current permission/holdout | `authority.py`, `control.py`, registry/config | read only; compute actionability separately |
| Observation-window execution | Observation Lab | reference only; no duplicate clock/window |
| Research action vocabulary | #1093 `EXTEND/REPLICATE/DIFFERENT_TEST/RETIRE` | reuse read-only |

## Deletion / non-duplication map

Do **not** create: a Memory content store, World Model, second hypothesis/experiment/exposure/verdict/fixed-look/lifecycle/authority system, second Observation Lab, causal graph, vector database, scheduler, dashboard, generic Learning Agent, automatic Paper admission, generic `apply_knowledge()`, or model-written trading action surface.

Delete/replace during cutover:

- Markdown-frontmatter-first causal anchoring in `hypothesis_research.py` → canonical anchor/T0 envelope input.
- Keyword matching that promotes narrative volatility/news text to observed domains → typed domain adapters.
- One-hypothesis-row representation of generation provenance → append-only nomination rows.
- Any single status that conflates economic qualification and operational actionability → two booleans plus reasons.

## Packet 0 executable failures

The focused Packet 0 run on this exact head produced eight intended failures:

- missing `economic_knowledge.py` and read-only projection;
- missing nomination identity separate from hypothesis identity;
- missing `t0_context.py` assessment and stage/receive-time/population guards;
- missing `memory_assets.py` root-outcome dedup.

No production behavior, UFC identity/evidence, registry authority flag, migration, Runtime, or config changed in Packet 0.
