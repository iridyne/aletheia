# Universe Continuous Scoring Adversarial Review

> Date: 2026-08-07
>
> Role: dated review evidence, not current product behavior
>
> Reviewed design:
> [Universe continuous scoring safety Proposal](../../proposals/universe_continuous_scoring_safety.md)
>
> Verdict: task 2 design passes for pure non-authoritative implementation;
> Paper, Fedora, factor authority, and positive contribution do not pass

## 1. Review method

The design was reviewed from two deliberately conflicting positions:

1. **Implementer:** can every requirement be represented deterministically,
   persisted without semantic invention, tested, and split across the existing
   staged tasks?
2. **Adversarial risk reviewer:** how could the same design manufacture evidence,
   hide missingness, starve a losing cohort, exploit repeated testing, or allow
   one Learning path to validate itself?

The review does not use test success as proof of profitability. It checks only
whether task 2 is sufficiently bounded to enter task 3 without choosing policy
inside implementation.

## 2. Implementer review

The design is implementable without a new model or runtime dependency:

| Requirement | Deterministic implementation boundary | Fail-closed result |
| --- | --- | --- |
| Continuous curves | source-controlled anchor manifest and shape-preserving interpolation | invalid non-finite input; no authority import |
| Raw evidence identity | stable feature/group ids plus complete consumer list | missing/undeclared identity invalidates record |
| Aggregate sensitivity | perturb complete score once on normalized anchor coordinates | missing manifest delta invalidates record |
| Missingness | point total plus interval/anchor-derived pessimistic total | required missing input invalid; no optimistic authority |
| Source drift | versioned source and availability-profile fingerprints | new epoch and from-zero evidence |
| Reversible treatment | immutable baseline plus bounded positive-bonus suppression | expiry/negative/invalid restores baseline |
| Evaluation | complete cluster assignment, full-candidate outcomes, replay and DR | no overlap/disagreement/underpower cannot authorize |
| Harm/fairness | preregistered tail and material-cohort confidence gates | unavailable cohort blocks global claim |

Task ownership remains separable:

- task 3 implements only pure curves, raw-evidence metadata, influence budgets,
  and pessimistic bounds;
- task 4 computes twin scores while `universe-step-v1` remains authoritative;
- task 5 persists full factor, sensitivity, missingness, rank, assignment, and
  outcome evidence;
- task 6 opens the new behavior identity and policy epoch in Shadow only.

None of these tasks alone registers `universe_factor_curve_learning` as
measurement-supported or admits Paper influence.

## 3. Adversarial findings and corrections

| Attack | Failure if uncorrected | Correction now required | Result |
| --- | --- | --- | --- |
| Price-derived pseudo-corroboration | RSI, returns, ATR, and Bollinger could look like independent votes | all share the `price_path` evidence group | closed for design |
| Hidden duplicate consumers | volume/RSI/returns can affect scanner and allocator more than once | one raw id, complete consumer list, whole-score perturbation, frozen influence budget | closed for design |
| Unit-sensitive derivatives | funding, RSI, spread, and returns have incomparable raw units | normalized anchor-coordinate perturbation, versioned with behavior | closed for design |
| Optimistic missing spread/depth/flow | absent data could outrank honestly observed weak data | point score becomes diagnostic; pessimistic score is the only future authority candidate | closed for design |
| Multiple missing interactions | per-factor lower bounds could miss a harmful multiplier interaction | joint finite anchor extrema for declared interactions | closed for design |
| Semantic leakage | a diagnostic point score could be shown as confidence or authoritative rank | explicit diagnostic/imputed label and threshold crossing grants no eligibility | closed for design |
| Silent feed recovery or replacement | a new feed could improve treatment mid-window and receive false credit | source fingerprint, pre-treatment availability profile, drift gate, new epoch | closed for design |
| Cohort starvation | suppression could remove examples that would prove recovery | settle every eligible candidate, retain control, expire authority, return to baseline | closed for design |
| Pseudo-replication | thousands of member rows could masquerade as independent samples | complete scanner-cluster unit, propensity ESS, consecutive-date gate | closed for design |
| Optional stopping and curve shopping | repeated checks/candidates could eventually produce a false winner | disjoint tuning/confirmation windows, preregistration, anytime-valid sequences, total error allocation | closed for design |
| Mean hides tail/cohort harm | average return could improve while one direction or liquidity tier deteriorates | loss, MAE p95, CVaR, cost, concentration, coverage, and material-cohort gates | closed for design |
| Overlapping Learning paths | family calibration or operator policy could be credited to a factor | one treatment per candidate; concurrent controls frozen equally; overlap invalidates epoch | closed for design |
| Permanent self-confirmation | a winning treatment could keep generating only data favorable to itself | 30-day expiry, baseline restoration, no same-window renewal, fresh owner admission | closed for design |

Two review corrections materially changed the first draft:

- semantic feature families were replaced by source-level groups because
  several indicators are transforms of one price history;
- overlapping Learning treatment was made an explicit invalidation condition,
  not merely another field in the behavior hash.

## 4. Research alignment

The design uses current research only for the relevant safety principles:

- [variance-adaptive betting bounds and freezing](https://proceedings.mlr.press/v291/ryu25a.html)
  support conservative selection in small-data regimes;
- [pessimistic offline policy optimization](https://proceedings.mlr.press/v238/wang24a.html)
  addresses distribution shift;
- [off-policy confidence sequences](https://proceedings.mlr.press/v139/karampatziakis21a.html)
  remain valid under optional stopping and support gated deployment;
- [confounding/no-overlap evaluation](https://proceedings.mlr.press/v286/zhang25d.html)
  supports bounds or an unavailable conclusion when point identification is not
  justified;
- [fair-bandit work](https://proceedings.mlr.press/v244/sinha24a.html) motivates
  preventing reward optimization from starving an arm or cohort;
- [performative-effect research](https://proceedings.mlr.press/v236/boeken24a.html)
  warns that naive retraining on outcomes changed by the prior policy can
  underestimate risk.

These papers do not prove Aletheia's curves are profitable and do not supply the
project-specific `0.50`, 10%, 10%, or 30-day policy bounds. Those values are
explicit conservative owner policies and must not be described as research
constants.

## 5. Residual risks that remain open by design

- The chosen anchors preserve intended scoring semantics; their economic value
  is not yet known.
- Raw-feature budgets, pessimistic interval arithmetic, twin persistence,
  replay, confidence sequences, and cohort gates are not implemented.
- `universe_factor_curve_learning` remains measurement-unsupported and must not
  create an exposure or authority identity.
- Current Fedora/runtime evidence has not been restarted or changed by this
  documentation work.
- Thin or unavailable cohorts may prevent promotion indefinitely. This is an
  honest `not available` outcome, not a reason to pool incompatible evidence.
- Passing all later gates still demonstrates bounded positive Paper evidence,
  not guaranteed future profit or Live readiness.

## 6. Final self-verdict

**Task 2: PASS for design completeness and entry into task 3.** The exact
curves, missing semantics, four safeguards, failure states, recovery behavior,
authority boundaries, and verification gates are now specified without leaving
implementation to invent policy.

**Production/Paper verdict: NOT PASSED and not requested.** The frozen step
scorer remains the only authority. The next permissible action is the pure task
3 implementation; twin wiring, persistence, Shadow identity, and any factor
experiment remain separate later gates.
