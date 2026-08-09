# Quantitative Market-Structure Methods

Date: 2026-08-09
Role: Research evidence

## Research question

Which parts of Price Action, support/resistance, SMC, and ICT have a reusable
quantitative basis, and which parts require Aletheia-specific hypotheses?

## Evidence found

### Pattern recognition can be formalized

Lo, Mamaysky, and Wang proposed non-parametric kernel-regression recognition of
chart patterns and compared conditional with unconditional return
distributions across a long US-equity sample. This supports formalizing a
shape before testing it; it does not establish that any named pattern transfers
to crypto, intraday data, or current regimes.

Source: [Foundations of Technical Analysis](https://business.columbia.edu/faculty/research/foundations-technical-analysis-computational-algorithms-statistical-inference-and).

### Support/resistance can be tested as a temporary barrier

Chung and Bellotti describe a heuristic support/resistance discovery algorithm.
Their reported bounce probability increases with prior bounces and decays with
time. Osler's foreign-exchange study found that published support/resistance
levels were associated with trend interruptions, while effects varied across
firms and the firms' own strength labels were not consistently informative.

Sources: [Chung and Bellotti](https://arxiv.org/abs/2101.07410),
[Osler, Federal Reserve Bank of New York](https://www.newyorkfed.org/medialibrary/media/research/epr/00v06n2/0007osle.pdf).

These findings support point-in-time level construction plus touch/bounce/age
statistics. They do not justify a universal support/resistance parameter set.

### Regime changes have established online methods

Adams and MacKay's Bayesian Online Changepoint Detection maintains the posterior
distribution of the current run length and detects changes without retrospective
segmentation. It is a reusable regime primitive, although the observation model,
hazard, truncation, and crypto calibration remain Aletheia choices.

Source: [Bayesian Online Changepoint Detection](https://arxiv.org/abs/0710.3742).

### Rule-based edges can decay

Published rule-based chart-pattern studies report that results vary by sample
and can weaken or disappear in later periods. The operational lesson is to use
walk-forward evaluation and regime stratification, not to preserve a historical
pattern indefinitely because it once worked.

Source: [Zapranis and Tsinaslanidis](https://www.sciencedirect.com/science/article/pii/S0957417411016253).

## What was not found

No peer-reviewed or exchange-authored standard was found for ICT Fair Value
Gaps, order blocks as institutional intent, CHoCH, or SMC as a unified model.
These names are therefore not reusable algorithms. They can only become
Aletheia features after their observable sequence, parameters, confirmation
delay, and invalidation rule are frozen.

An OHLC pattern cannot demonstrate that an institution intended to accumulate,
distribute, or hunt stops. Such intent remains unobservable narrative.

## Reusable causal primitives

### 1. Confirmed pivot

Two safe candidates are:

- a local high/low confirmed after `k` closed bars on the right; or
- a directional-change pivot confirmed after price reverses by
  `delta = c * ATR`.

Both expose a real confirmation delay. A retrospective ZigZag that moves old
pivots after future bars arrive is not a live feature.

### 2. Support/resistance zone

Cluster only confirmed pivots within a frozen distance such as `epsilon * ATR`.
Maintain:

- center and width;
- first and last confirmation times;
- point-in-time touch, rejection, break, and reclaim counts;
- age-decayed strength;
- distance from current close in ATR units.

The zone must be computed only from information available at the decision time.

### 3. Structure state machine

Use the last confirmed high and low transitions to classify a bounded state:

- higher-high/higher-low;
- lower-high/lower-low;
- range/ambiguous;
- confirmed breakout or failed breakout.

`BOS`, `CHoCH`, `breakout`, and `trend transition` should be presentation aliases
over one state machine, not independent signals.

### 4. Sweep candidate

A causal candidate can be defined as:

1. price trades beyond a previously confirmed level by a frozen tolerance;
2. a closed bar returns inside the level or zone within a frozen horizon;
3. optional follow-through is evaluated separately.

This measures a failed excursion. Calling it a stop hunt or institutional
liquidity action would exceed the data.

### 5. FVG candidate

A three-candle non-overlap rule can be implemented deterministically, but it is
an Aletheia hypothesis rather than an established academic feature. Its gap
boundaries, wick/body choice, minimum ATR size, fill rule, expiry, and timeframe
must all be versioned.

### 6. Online regime state

BOCPD is the academically grounded option when a posterior over run length is
valuable. A simpler CUSUM-style detector is a lower-cost baseline. The more
complex method should be adopted only if it produces a measured improvement in
regime classification or downstream decisions.

### 7. Prospective outcome labels

Triple-barrier-style labels can evaluate an event by first contact with upper,
lower, or vertical barriers after the event. They are outcome data for training
or validation, never an online input at the event timestamp.

## Implementation order

1. Confirmed pivots and a structure state machine.
2. ATR-normalized support/resistance zones with age and touch statistics.
3. Failed-breakout/sweep candidates.
4. A simple regime baseline, compared with BOCPD only if needed.
5. FVG as a separately named experimental feature.

Do not begin with a combined SMC score. That would hide which primitive, if
any, contributes information and would create severe multiple-testing freedom.

## Validation contract

For every candidate:

- use closed bars and record confirmation latency;
- freeze parameters before the evaluation period;
- replay point-in-time state without pivot backfill;
- use walk-forward, purged, or embargoed splits where paths overlap;
- stratify by asset, timeframe, volatility, and regime;
- report coverage, missingness, confidence intervals, turnover, and results
  after fees, funding, and slippage;
- preserve negative and decayed results;
- keep theory names out of the decision context unless the underlying numeric
  fact is also supplied.

The research supports reusable computation patterns, not a claim of Alpha.
