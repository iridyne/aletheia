# Evidence and AI Injection Policy

Date: 2026-08-09
Role: Research evidence

## Purpose

This report defines how a trading theory can become a compact AI input without
turning names, narratives, or retrospective charts into false facts.

It is not an implementation approval. Current behavior remains governed by the
relevant Product Truth, Contracts, Decisions, Risk policy, and experiment
protocol.

## Evidence grades

### Grade A: directly observed and reproducible

Examples include closed OHLCV, sequenced trades, venue order-book events, and
venue-reported liquidations. Grade A describes data provenance, not predictive
value. An observed event can still have no forward edge.

### Grade B: deterministic transformation

Examples include RSI, ATR, confirmed swings, a pre-registered FVG rule, OFI,
and a profile computed from complete trade data. The same ordered input and
version must produce the same output.

### Grade C: model estimate or heuristic

Examples include candle-approximated volume-at-price, order-block candidates,
inferred liquidation maps, and clustered support/resistance. These require
parameter, uncertainty, and missingness disclosure.

### Grade D: narrative or unobservable claim

Examples include `institutions are accumulating`, `smart money intends to run
stops`, or a chart label chosen after the outcome. Grade D may be a hypothesis
or explanation style but cannot enter Aletheia as an authoritative market fact.

## Feature, signal, and action are different objects

- A **feature** is a point-in-time fact or estimate: `rsi_1h=68` or
  `sweep_candidate=true`.
- A **signal** is a versioned interpretation: `sweep plus positive flow supports
  a long hypothesis`.
- An **action proposal** includes direction, timing, invalidation, expected
  costs, and uncertainty.
- Risk approval and Execution remain downstream authorities.

Combining these stages in one LLM sentence makes replay and falsification
impossible.

## Compact AI context

AI should receive structured deltas and levels rather than hundreds of raw
candles. A candidate representation is:

```json
{
  "as_of": "closed-bar timestamp",
  "instrument": "venue + symbol + market_type",
  "timeframe": "1h",
  "structure": {
    "state": "higher_high_higher_low",
    "last_confirmed_swing_high": 0.0,
    "last_confirmed_swing_low": 0.0,
    "events": ["bullish_bos_candidate"]
  },
  "levels": [
    {
      "kind": "swing_resistance",
      "price": 0.0,
      "distance_atr": 0.0,
      "confirmed_at": "timestamp"
    }
  ],
  "flow": {"state": "unavailable", "reason": "no_sequenced_trade_feed"},
  "quality": {"state": "complete", "reasons": []},
  "algorithm_version": "market-structure-v1",
  "input_fingerprint": "sha256"
}
```

Fields that are unavailable should be omitted or explicitly unavailable, not
invented by the model. Full raw input remains outside the prompt and is linked
through a fingerprint or durable source identity.

## Preventing token and evidence duplication

- Compute facts once in code and reuse one versioned snapshot.
- Send levels and state changes, not repeated indicator explanations.
- Do not send SMC, ICT, Price Action, and failed-breakout aliases as separate
  confirmations of the same event.
- Include only the nearest material levels and bounded event history.
- Keep formula definitions in versioned code/docs, not repeated in every prompt.
- Separate numeric fact fields from model-authored interpretation.

## Validation ladder

### 1. Numerical reference

Prove deterministic batch results, incremental equivalence, closed-bar timing,
gap handling, and instrument isolation.

### 2. Historical point-in-time replay

Reconstruct exactly what was knowable at each decision timestamp. Freeze all
parameters before evaluating outcomes.

### 3. Walk-forward and holdout evaluation

Use non-overlapping future periods, assets, and regimes. Control multiple
hypothesis testing and report negative results.

### 4. Costed Shadow observation

Record the feature and hypothetical decision without changing actual behavior.
Measure gross and net outcomes, adverse excursion, turnover, coverage, and
missingness.

### 5. Prospective decision experiment

Only separately approved features enter the decision context. This creates a
new behavior identity, policy epoch, and observation window. Pre-change and
post-change samples are not pooled.

### 6. Risk and execution remain independent

No research result removes Risk checks, changes Live defaults, or permits an
agent to route orders outside the Execution Gateway.

## Recommended research order

1. Expose already computed technical facts consistently before adding new data.
2. Add closed-bar swings, support/resistance, BOS, FVG, and sweep candidates as
   observation-only facts.
3. Add TPO or Volume Profile only after its source and session identity are
   explicit.
4. Add trade-flow, L2 OFI, and liquidation events only with sequence, latency,
   and coverage provenance.
5. Test combinations after individual features have stable prospective
   coverage; do not begin with a large SMC/ICT confluence score.

## Promotion checklist

A feature is not eligible for a decision experiment until all answers are yes:

- Is the definition frozen and executable?
- Are all inputs timestamped at or before the decision?
- Are venue, instrument, timeframe, session, and algorithm versions explicit?
- Does missing data fail closed?
- Is the feature distinct from other counted evidence?
- Has point-in-time replay passed?
- Is there holdout or walk-forward evidence after costs?
- Is the prospective experiment identity and outcome contract defined?
- Can the feature be removed without touching Risk or Execution authority?

Paper readiness, test success, and plausible explanation are engineering
evidence. None of them alone is profitability proof.
