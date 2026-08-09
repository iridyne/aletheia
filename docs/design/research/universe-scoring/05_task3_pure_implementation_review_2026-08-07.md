# Task 3 Pure Continuous Scorer Implementation Review

> Date: 2026-08-07
>
> Verdict: PASS for task 3 only
>
> Behavior identity: `universe-continuous-shadow-v1`
>
> Authority: none; `universe-step-v1` remains the only current scorer

## 1. Implemented boundary

Task 3 adds the pure module
`aletheia/universe/continuous_scoring.py` and its focused property and regression
suite. The module implements the accepted six-family continuous curve
specification, tradability composition, explicit point and pessimistic totals,
raw-evidence declarations, whole-score sensitivity diagnostics, evidence-group
concentration flags, and frozen per-feature influence budgets.

The module has no production consumer. It does not import or call the allocator,
scanner service, Opportunity emitter, persistence, Learning, Risk, Execution,
exchange, or runtime modules. It does not rank candidates, emit opportunities,
write state, alter a policy epoch, or place orders. No Fedora instance was
changed or restarted.

This boundary is intentional: task 3 proves deterministic scorer behavior. A
later, separately reviewed task must wire a twin Shadow consumer while keeping
`universe-step-v1` authoritative.

## 2. Public audit surface

The implementation exposes:

- `shape_preserving_curve`: clamped shape-preserving cubic Hermite
  interpolation with exact anchors and no interval overshoot;
- `score_continuous_family` and `score_all_continuous_families`: pure family
  scoring for the existing momentum, mean-reversion, volatility, derivatives,
  event, and short-bias families;
- `evaluate_continuous_candidate`: a complete pure candidate evaluation with
  point and pessimistic scores plus diagnostics;
- `raw_feature_declarations`: a copied manifest containing each stable raw
  feature id, evidence group, all declared curve consumers, neutral value, and
  influence budget;
- immutable evidence, component, sensitivity, and candidate result records.

Callers cannot mutate the internal manifest through the returned mapping. A
present Boolean, wrong type, non-finite value, or physically invalid numeric
domain fails closed rather than being reinterpreted as zero or missing. A
present numeric zero remains an observed value and cannot trigger a fallback.

## 3. Safeguard coverage at this stage

The accepted Proposal defines four safeguards. Task 3 implements only the pure
primitives that can exist without runtime or Learning authority:

| Safeguard | Task-3 status | Evidence |
| --- | --- | --- |
| Raw-evidence aggregation and concentration | Implemented in the pure result | Sensitivity perturbs the complete scorer once per `raw_feature_id`; correlated raw features share an evidence group; interactions are labelled and are not re-added as independent contributions; single-source, concentration, and influence-budget flags are emitted. |
| Missingness cannot improve authority | Implemented in the pure result | Point and pessimistic totals are calculated separately; missing-feature ids and deltas remain explicit; property tests enforce pessimistic dominance over point and admissible observed values. |
| Reversible negative-only suppression and continual observation | Not implemented in task 3 | This requires registered Learning treatment, expiry/refreeze, stable control assignment, and all-candidate settlement. Adding it here would violate the no-authority boundary. |
| Pessimistic anytime-valid promotion gate | Not implemented in task 3 | This requires persisted prospective cohorts, overlap, outcomes, costs, confidence sequences, and owner admission. No task-3 result can promote behavior. |

The last two safeguards remain mandatory before factor Learning can become
measurement-supported. Their absence is not treated as a permissive fallback:
the new scorer has no consumer and therefore no route to authority.

## 4. Adversarial self-review

The implementation was reviewed from the failure side, not only against happy
path examples:

| Attack or defect class | Result |
| --- | --- |
| Interpolation jumps or overshoot between anchors | Exact-anchor, continuity, monotonic-run, bounded-range, and generated no-overshoot properties pass. |
| Long/short asymmetry introduced by continuous transforms | Fixed examples and generated symmetric inputs pass. |
| Missing input receives a better score than observed admissible input | Per-feature declared-domain cases and generated candidates pass pessimistic dominance. |
| Observed numeric zero silently treated as absent | Explicit fallback regression passes. |
| Same observation counted as several independent witnesses | One complete-score perturbation is recorded per raw feature; evidence groups expose price-derived correlation. |
| Interaction contribution added once for every input | Interaction inputs are audit-labelled; only the interaction term contributes to the score. |
| Continuous curve exceeds the frozen step scorer's feature envelope silently | Budgets are derived from the frozen golden boundary fixture and every excess is an explicit blocking diagnostic. |
| Invalid or non-finite present data becomes a reassuring default | Domain validation returns an invalid result. |
| Evaluation mutates the input mapping | Non-mutation regression passes. |
| Refactor changes the frozen step-v1 scorer or existing Universe behavior | Existing scanner, allocator, filter, and frozen-score tests remain green. |
| New module leaks into a production authority path | Structural/text reference audit finds imports only in the focused test module. |

Three defects found during this adversarial pass were corrected before the
verdict: volatility now applies its direction multiplier after all additive
inputs; short-bias applies its direction multiplier only after its complete
additive subtotal; and anchor endpoints use the required one-sided sensitivity
perturbation instead of reporting a false zero. Derivatives interactions now
preserve both raw inputs in the audit record.

## 5. Verification evidence

All checks below were run locally and were deliberately focused rather than a
large macOS test suite:

| Check | Result |
| --- | --- |
| New pure scorer suite, including Hypothesis properties and deterministic fuzzing | `60 passed` |
| New suite plus existing scanner, allocator, filter, and frozen-score regressions | `145 passed` |
| Ruff correctness/style and cyclomatic-complexity checks | passed |
| Semgrep Python ruleset on the new module and tests | passed, zero findings |
| Python bytecode compilation | passed |
| Production import/reference isolation audit | only the focused test imports the module |
| Documentation governance check | passed |

This proves the checked deterministic properties and isolation boundary. It
does not prove runtime compatibility, ranking improvement, Paper profitability,
positive contribution, or safe Learning promotion.

## 6. Residual work and gate

Task 4 may add a versioned twin Shadow consumer only after separate review. It
must preserve step-v1 ranking, admission, Opportunity emission, risk flags, and
all downstream authority. It must also expose the already-computed Bollinger
squeeze ratio without turning that producer change into a new signal.

Persistence, feature-source fingerprinting, availability-profile rotation,
behavior/policy epoching, full-candidate outcome settlement, and all Learning
or promotion machinery remain out of scope and unavailable. Until those later
gates pass, the continuous result is diagnostic only and cannot be described as
confidence, authority-eligible rank, learned improvement, or profit evidence.
