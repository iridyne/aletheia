# Universe Scoring References and Practical Notes

> Role: durable research and engineering reference
>
> Scope: continuous scoring, conservative offline evaluation, fairness,
> missingness, non-stationarity, and anti-self-reinforcement

## 1. Primary research

| Reference | Transferable idea | Aletheia use | What it does not prove |
| --- | --- | --- | --- |
| [Counterfactual Risk Minimization](https://proceedings.mlr.press/v37/swaminathan15.html) | Logged propensities and variance-sensitive policy learning | stable randomized assignment and rejection of fragile high-variance improvements | that a proposed Universe curve is profitable |
| [Doubly Robust Policy Evaluation](https://arxiv.org/abs/1103.4601) | Combine outcome and logging-policy models | require DR diagnostics beside direct full-candidate replay | that overlap can be invented when none exists |
| [Off-Policy Confidence Sequences](https://proceedings.mlr.press/v139/karampatziakis21a.html) | Time-uniform inference valid under optional stopping | gated evaluation without ordinary interval peeking | that repeated curve search is harmless |
| [Non-stationary Contextual Bandits](https://proceedings.mlr.press/v75/luo18a.html) | A fixed historical policy is inadequate under changing environments | policy epochs, fresh windows, concurrent controls, and refreeze | that one historical backtest generalizes to a new regime |
| [Continuous-Treatment Policy Evaluation](https://proceedings.mlr.press/v84/kallus18a.html) | Continuous actions need specialized evaluation | use a finite preregistered treatment set rather than unrestricted online weights | that arbitrary real-valued tuning is safe for the MVP |
| [Oracle-Efficient Pessimism](https://proceedings.mlr.press/v238/wang24a.html) | Pessimistic policy optimization mitigates distribution shift | authority-eligible pessimistic score and lower-bound promotion | that point imputation is conservative |
| [Improved Offline Contextual Bandits: Betting and Freezing](https://proceedings.mlr.press/v291/ryu25a.html) | Variance-adaptive bounds and freezing help small-data regimes | conservative selection, finite candidates, and frozen fallback | that more complex models are automatically more advanced |
| [Causal Eligibility Traces](https://proceedings.mlr.press/v286/zhang25d.html) | Bound value under confounding or no overlap | return bounded/not-available evidence instead of a false point claim | that observational data identifies every treatment effect |
| [BanditQ](https://proceedings.mlr.press/v244/sinha24a.html) | Reward optimization can starve other arms | continual control and observation of suppressed cohorts | that equal scores or equal capital allocation are required |
| [Evaluating Performative Effects](https://proceedings.mlr.press/v236/boeken24a.html) | A deployed decision rule changes later observed data; naive retraining can underestimate risk | expiring treatment, immutable baseline, full-candidate outcomes, and no self-renewal | that a treated outcome is automatically unbiased feedback |
| [Multiaccuracy under Distribution Shift](https://proceedings.mlr.press/v287/kapash25a.html) | Aggregate performance can hide subgroup calibration error | preregistered family/direction/regime/liquidity/data-completeness checks | that every thin cohort can support a global claim |

These sources justify evaluation and safety structure. Project thresholds such
as 50% concentration, 10% maximum suppression, 10% control, the availability-
drift detector, and 30-day expiry are conservative Aletheia policy choices, not
numbers claimed by the papers.

## 2. Public industry evidence boundary

Useful public references:

- [VergeX harness description](https://paragraph.com/@vergex/vergex-the-harness-layer-for-ai-trading)
  separates strategy, validation/risk, and deterministic execution concerns.
- The official [NOFX skills repository](https://github.com/NoFxAiOS/nofx-skills)
  describes AI500 as an external AI-scored candidate pool and shows the kinds
  of market context presented with candidates.
- The official [NOFX strategy module](https://github.com/NoFxAiOS/nofx/blob/dev/docs/architecture/STRATEGY_MODULE.md)
  consumes that pool as one candidate source rather than treating it as direct
  execution authority.

Not available from the public official material:

- the exact AI500 formula, weights, labels, training window, or retraining
  cadence;
- whether its factors are continuous or stepwise;
- propensity logging, treatment/control, conservative OPE, drift, or rollback
  details;
- probability calibration or an auditable causal performance protocol.

Therefore AI500 is a useful presentation/context reference, not an evidence
source for copying a hidden score or claiming that it is calibrated.

## 3. Reusable engineering lessons

### Score semantics

- A ranking score is not a probability. Use `score n/100`, never `n%`, unless a
  separately validated calibration contract exists.
- Round only at presentation. Ranking, replay, and persistence retain the
  underlying float.
- Continuous score factors do not remove hard safety, validity, or execution
  gates. Those boundaries should remain explicit decisions.

### Evidence identity

- Group by data-generating source, not indicator name. RSI, returns, ATR, and
  Bollinger bands are different transforms of the same price path.
- Perturb the complete score once per raw feature. Adding derivatives from each
  consuming function counts repeated use several times.
- Local sensitivity is an audit signal, not causal factor contribution. A
  correlation, SHAP value, or score delta cannot replace a prospective
  treatment/control comparison.

### Missing data

- Numeric zero is observed evidence; it must not trigger a falsy fallback.
- Missing, invalid, stale, and imputed are different states and require
  different provenance.
- Keep an explicitly labelled point-imputed score for comparison, but use a
  pessimistic lower score for any future authority proposal.
- A new or recovered feed changes what the policy knows. It cannot silently
  join an existing outcome window.

### Feedback safety

- Record outcomes for all eligible candidates, not only selected or traded
  survivors.
- Keep treatment and control at the complete scanner-cluster unit. Member-row
  counts are not independent sample counts.
- First-generation factor feedback should only reduce one positive bonus toward
  neutral. It must not boost, flip signs, or weaken penalties and hard gates.
- Learned suppression expires back to an immutable baseline. It cannot renew
  itself using outcomes produced under its own selective behavior.
- One candidate must not receive overlapping Learning treatments when the
  experiment claims factor-level attribution.

### Honest conclusions

- No overlap, missing propensity, incompatible estimators, cohort underpower,
  or economic-source conflict means `not available`, `insufficient`, or
  `invalid`, never zero or positive by inference.
- Passing deterministic tests proves bounded implementation. Passing a Shadow
  or Paper gate proves evidence only for the declared cohort and window. Neither
  guarantees future profit or Live readiness.
