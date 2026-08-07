# Universe Score Continuity and Learning Safety Review

> Date: 2026-08-07
>
> Status: research resolved into an accepted Shadow-only Proposal; no scoring
> behavior is implemented or active
>
> Scope: minimum money loop only; Universe scoring, score semantics, and future
> factor-level Learning feedback
> Non-goals: new factors, exchanges, strategies, Risk/Execution authority, live
> enablement, autonomous online weight updates

## 1. Decision summary

The current Universe scorer loses market information through stepwise factor
contributions, bucketed tradability/liquidity/penalties, integer rounding, and a
UI that renders an ordinal ranking score as a probability percentage.

Two changes must remain separate:

1. **Score semantics** is a display and contract correction. It does not change
   ranking behavior and can ship independently.
2. **Continuous scoring** changes candidate order and therefore is a B-class
   behavior change. It requires an approved curve specification, a new behavior
   version/policy epoch, full-candidate replay, twin-score Shadow evidence, and
   an explicit owner decision before Paper influence.

Factor-level Learning is a third, later change. It must not be smuggled into the
continuous-score rewrite. Current Learning authority supports only a bounded
family-level multiplier; it does not support learning individual RSI, return,
volume, funding, liquidity, or penalty curves.

## 2. Current implementation findings

### 2.1 Where information is quantized

The current score chain contains step contributions in:

- all six scanner families: momentum, mean reversion, volatility,
  derivatives, event, and short bias;
- tradability volume, spread, freshness, and depth;
- allocator liquidity, crowding, late-entry, and staleness penalties;
- Opportunity emission, which rounds `total_score` to an integer;
- the UI, which appends `%` to the integer and presents it as confidence.

The hard eligibility gates for stablecoins, blacklists, critically shallow
depth, extreme spread, stale data, missing direction, and no-chase behavior are
not score buckets. They are safety/data-quality invariants and must remain hard
gates unless separately authorized.

### 2.2 SNXX example

The observed `SNXX/USDT` mean-reversion short had:

```text
opportunity score   45.000
tradability score   98.000
liquidity score     90.000
estimated cost      -4.825
--------------------------------
total score         48.925
displayed value     49%
```

`49%` was not a 49% success probability. It was a composite ranking score
rounded to an integer.

### 2.3 Runtime distribution evidence

On the Fedora Paper instance, the latest 24-hour sample contained 414 Universe
Opportunities. Their underlying member scores had 290 distinct values, but the
Opportunity/UI layer showed only 21 distinct integers. This confirms that both
the factor functions and presentation layer contribute to the apparent
discreteness.

### 2.4 Current Learning authority

The normative [Learning contract](../../contracts/learning_contract.md) permits
measurement support only for `universe_family_calibration`.

Current behavior:

- cohort: `family × direction × market_type`;
- outcome: fixed-horizon, cost-aware net candidate outcome;
- retrospective proposal: shrink toward neutral and bound adjustment;
- treatment: current implementation reduces the proposal to `0.9`, `1.0`, or
  `1.1` opportunity multipliers;
- authority: stable cluster assignment, control holdout, behavior contract,
  policy epoch, minimum samples/time, harm gates, staged Paper lifecycle, and
  owner admission;
- fallback: multiplier `1.0`.

Fedora read-only snapshot at 2026-08-07 12:24 UTC:

| Runtime fact | Value |
| --- | ---: |
| Active Universe calibration Shadow experiments | 9 |
| Shadow control exposures | 2,700 |
| Shadow treatment exposures | 2,632 |
| Exposures with authority actually applied | 0 |
| Settled full-candidate filter outcomes | 15,199 |
| Pending full-candidate filter outcomes | 8,753 |
| Latest complete treatment/control units per verdict | 0–6 per arm |
| Latest verdicts | all `insufficient_data` |

Raw exposure or settled-row counts are not the experiment sample size. Complete
assignment units, current behavior identity, both arms, and consecutive market
days remain required. The current runtime is therefore correctly staying in
Shadow.

## 3. External research

### 3.1 What is transferable

- [Counterfactual Risk Minimization](https://proceedings.mlr.press/v37/swaminathan15.html)
  requires logged action propensity and incorporates propensity-weighted
  variance into the learning objective. This supports stable randomized logging
  and variance-aware rejection of fragile improvements.
- [Doubly Robust Policy Evaluation](https://arxiv.org/abs/1103.4601) combines a
  reward model with a logging-policy model. It reduces dependence on either one
  model being perfect, but it cannot manufacture overlap where none exists.
- [Off-Policy Confidence Sequences](https://proceedings.mlr.press/v139/karampatziakis21a.html)
  provides intervals valid under optional stopping and explicitly supports
  gated deployment. This is safer than repeatedly checking ordinary confidence
  intervals until one becomes positive.
- [Non-stationary contextual bandits](https://proceedings.mlr.press/v75/luo18a.html)
  show why comparison with one historical fixed policy is inadequate when the
  environment changes. Fresh windows, policy epochs, drift detection, and
  refreeze semantics are necessary in markets.
- [Continuous-treatment policy evaluation](https://proceedings.mlr.press/v84/kallus18a.html)
  shows that arbitrary real-valued treatments need more than ordinary discrete
  IPS/DR. The MVP should test a finite set of preregistered candidate curves,
  not learn unrestricted real-valued weights online.

### 3.2 VergeX / AI500 evidence boundary

Publicly verifiable facts:

- [VergeX](https://paragraph.com/@vergex/vergex-the-harness-layer-for-ai-trading)
  publicly describes a harness with strategy, validation/risk, and
  deterministic execution layers.
- The official [NOFX AI500 skill](https://github.com/NoFxAiOS/nofx-skills)
  describes AI500 as an external pool of AI-scored candidates above 70.
- Its reports contain score, cumulative return, OI across several horizons,
  institutional flow, candles, volume, funding, and suggested actions.
- The official [NOFX strategy module](https://github.com/NoFxAiOS/nofx/blob/dev/docs/architecture/STRATEGY_MODULE.md)
  consumes the AI500 pool as one candidate source.

Not available in public official material:

- exact AI500 score formula or factor weights;
- continuous versus stepwise factor curves;
- labels, training window, retraining cadence, and cost-aware target;
- treatment/control, propensity logging, OPE, drift detection, or rollback;
- probability-calibration evidence;
- an auditable historical performance protocol for the AI500 score.

AI500 is useful as a reference for rich candidate context and reporting. Its
unpublished mechanism is not evidence that Aletheia should copy or trust an
automatic self-modifying score.

## 4. Proposed continuous-score plan — approval required

### Phase C0 — freeze and characterize

1. Freeze the existing scorer as `universe-score-step-v1`.
2. Build a golden full-candidate fixture from real feature snapshots, including
   values immediately below/at/above every current threshold.
3. Persist every current factor contribution, family subtotal, allocator
   component, direction, and hard-gate result.
4. Measure current score distribution, family mix, direction mix, emission
   rate, quota utilization, and top-N turnover.

No behavior changes in this phase.

### Phase C1 — explicit curve specification

Recommended representation for owner review:

- use bounded, deterministic, source-controlled curves;
- retain current inputs, signs, maximum contributions, total-score weights, hard
  gates, and emission threshold;
- replace each factor's discontinuous jump with an explicit transition interval;
- prefer auditable monotone interpolation for monotonic factors and an explicit
  bounded peak/valley curve for band factors such as momentum RSI;
- do not add learned interactions or change family selection in the same patch.

Two viable interpolation choices:

| Choice | Benefit | Cost | Recommendation |
| --- | --- | --- | --- |
| Piecewise linear | simplest audit and exact local slope | derivative has kinks | acceptable baseline |
| Shape-preserving smooth interpolation | continuous value and sensitivity, no overshoot | more implementation/test complexity | preferred if implemented without a new runtime dependency |

Sigmoid/tanh curves are not recommended for the first version: their tails
change scores outside visible transition regions and make exact audit harder.

Each factor specification must state:

- neutral/noise region;
- transition start and end;
- contribution at every anchor;
- saturation cap;
- direction/sign behavior;
- missing-data behavior;
- monotonicity or intended single peak;
- hard gates that remain outside the curve.

The owner must approve these values because a continuous function cannot both
retain a zero value immediately below an old threshold and retain the old fixed
bonus immediately above it. The transition interval is a real policy choice.

### Phase C2 — twin scorer, Shadow only

1. Compute `step_v1_score` and `continuous_v1_score` for every candidate.
2. Keep `step_v1` authoritative for allocation and Opportunity emission.
3. Persist curve version, raw factor values, per-factor contributions, local
   sensitivity, both totals, rank delta, selected/dropped delta, and the exact
   scanner-run cluster identity.
4. Start a new behavior contract and from-zero policy epoch.
5. Settle all candidates using the existing cost-aware fixed-horizon path.

No treatment may affect Paper ranking during C2.

### Phase C3 — acceptance gates before Paper proposal

The continuous scorer must pass:

- deterministic replay and property tests;
- continuity tests around every anchor;
- boundedness, sign symmetry where intended, monotonicity, and no-overshoot
  properties;
- unchanged hard-gate behavior;
- no missing factor provenance;
- no canonical/mirror economic conflicts;
- stable family/direction coverage without one family collapse;
- turnover and emission-rate bounds approved by the owner;
- cost-aware outcome report by family, direction, market, regime, and horizon;
- tail-risk/MAE report, not only mean return and win rate.

Only after owner review may a separate, stable-randomized Paper trial be
registered. Continuousization and Learning parameter adaptation must not be
tested simultaneously.

## 5. Future factor-level Learning plan — not authorized for implementation

Proposed new path name: `universe_factor_curve_learning`.

It must start as `measurement_supported=False` and produce no formal authority.

```text
raw features
  -> frozen versioned continuous curves
  -> per-factor contribution + local sensitivity
  -> baseline score and allocation
  -> full-candidate cost-aware outcomes
  -> preregistered candidate curve
  -> replay + DR/OPE diagnostic
  -> Shadow twin score
  -> owner-gated paper_10 trial
  -> paper_25 -> paper_50 -> paper_active, or refreeze
```

Required constraints:

1. One experiment changes one factor parameter family in one declared cohort.
2. No per-trade or per-cycle gradient update.
3. No simultaneous score, quota, sizing, Risk, and exit changes.
4. Family, direction, market type, regime, and horizon remain explicit.
5. All candidate results are recorded, not only selected/traded survivors.
6. Assignment probability and overlap diagnostics are mandatory for IPS/DR.
7. Direct allocator replay and DR/OPE must both be reported; disagreement blocks.
8. Promotion requires a positive lower confidence bound on cost-aware allocation
   utility plus unchanged harm gates.
9. Ordinary repeated confidence-interval peeking is forbidden; use fixed
   preregistered evaluation times or anytime-valid confidence sequences.
10. Any behavior version, policy, data-source, or cost-model change rotates the
    epoch and restarts the window.
11. Owner admission is required at every Paper stage; no route leads to Live,
    Risk relaxation, credential access, or direct order authority.

The first safe research hypothesis should be deliberately narrow:

> In `mean_reversion × short × perp`, does changing only the RSI-extremity
> response slope improve 1h cost-aware allocation utility without worsening
> loss rate or MAE?

This is a research question, not a proposed active change.

## 6. Explicitly forbidden designs

- reward/punish a factor immediately after one trade closes;
- update weights continuously from the latest PnL;
- learn only from selected or executed candidates;
- optimize win rate instead of cost-aware net utility;
- mix policy epochs, curve versions, directions, markets, or incompatible cost
  contracts;
- change several factors and attribute the result to one;
- use IPS/DR without logged assignment probability and overlap;
- treat SHAP, correlation, rank correlation, or retrospective backtest as causal
  contribution;
- promote on the first positive ordinary confidence interval;
- display a ranking score as a calibrated probability;
- allow an LLM to write, activate, or deploy a score formula;
- infer AI500 internals from product claims where official evidence is absent.

## 7. Owner decisions required

No scoring-behavior implementation or Fedora deployment should proceed until
the owner approves:

1. continuous curve representation (piecewise linear or shape-preserving
   smooth interpolation);
2. every factor's transition interval and anchors;
3. acceptable distribution/turnover/emission drift bounds;
4. Shadow observation duration and minimum complete assignment units;
5. whether C2 twin scoring may be implemented before any Paper trial;
6. the separate future factor-Learning path, if it is pursued at all.

The already-completed score-semantic correction is independent: it changes
`49%` to source-aware `Scanner score 49/100` and keeps model confidence as
`Confidence score n/100`. It does not claim probability calibration and does
not alter trading decisions.

## 8. Resolution

On 2026-08-07 the curve anchors and four fail-closed safeguards were accepted
for staged, non-authoritative implementation. The durable future design is
[Universe continuous scoring safety](../../proposals/universe_continuous_scoring_safety.md);
the exact anchors remain in the dated
[continuous curve specification](02_continuous_curve_specification_2026-08-07.md).

This resolution does not claim that the scorer, twin wiring, factor treatment,
Fedora deployment, Paper authority, or positive contribution exists. The frozen
step scorer remains authoritative.
