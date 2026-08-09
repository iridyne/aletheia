# Price Action and Market Structure

Date: 2026-08-09
Role: Research evidence

## Research question

Which parts of Price Action, Smart Money Concepts (SMC), and Inner Circle
Trader (ICT) vocabulary can be represented as point-in-time facts, and which
parts remain subjective explanations?

## Evidence boundary

Price Action broadly infers possible future movement from past price movement.
Menkhoff and Taylor describe technical analysis in this inductive sense and
find that it is widely used in foreign exchange, while the reason for any
profitability remains market- and mechanism-dependent:
[Journal of Economic Literature, 2007](https://www.aeaweb.org/articles?id=10.1257/jel.45.4.936).

No peer-reviewed or standards-body specification was found that uniquely
defines the combined SMC/ICT system. The vocabulary is therefore a practitioner
taxonomy, not one reproducible algorithm. Aletheia should test measurable
subcomponents without claiming that OHLC alone reveals institutional intent.

The local imported community material reports practical interest in ICT, SMC,
FVG, order blocks, and multi-timeframe confirmation. Those reports are useful
for hypothesis generation but do not establish causal or profitability proof.

## Computable primitives

### Confirmed swing high and swing low

A candidate swing high at bar `t` is higher than a fixed number of closed bars
on both sides; swing low is the mirror definition.

The right-side confirmation means the fact becomes available only after those
later bars close. The feature timestamp must be the confirmation time, not the
historical pivot time. Backtests that expose the pivot at `t` leak future data.

Required parameters:

- left and right confirmation windows;
- strict versus equal-price comparison;
- timeframe;
- minimum prominence, optionally normalized by ATR.

### Break of Structure (BOS)

A BOS candidate is a closed-bar break beyond the latest previously confirmed
swing in the current structural direction.

The implementation must freeze:

- wick touch versus close beyond the level;
- minimum penetration in ticks, basis points, or ATR;
- whether a retest is required;
- maximum age of the referenced swing.

BOS describes a price event. It does not prove continuation or institutional
participation.

### Change of Character (CHoCH)

A CHoCH candidate is a break against a previously defined structural sequence,
such as the first confirmed lower-low violation during a higher-high/higher-low
state.

It is only meaningful when the prior state machine is explicit. Without that
state, CHoCH is a retrospective chart label.

### Fair Value Gap (FVG)

A minimal three-candle bullish FVG exists when the first candle's high is below
the third candle's low after all three candles close. A bearish FVG is the
mirror condition.

Candidate facts include:

- lower and upper boundary;
- creation and confirmation timestamp;
- width in ticks, basis points, and ATR;
- fill fraction using only subsequent bars;
- invalidation rule and age.

The word `fair` is narrative. The observable fact is an untraded or
non-overlapping candle interval under a specified OHLC rule.

### Liquidity sweep or failed breakout

A bullish-side sweep candidate occurs when price trades above a frozen prior
high but closes back below it within a fixed confirmation window. The downside
case is symmetric.

This can also be described as a failed breakout or stop-run candidate. Aletheia
should store one event, not count the aliases as independent confluence.

Required parameters include penetration, reclaim distance, volume condition,
confirmation window, and referenced-level age.

### Order block

The common mechanical approximation is the last opposite-direction candle or
candle cluster before a displacement and structure break. This is computable
only after defining:

- displacement threshold;
- qualifying BOS;
- one candle versus a cluster;
- body versus wick boundaries;
- mitigation and invalidation rules.

Even after rule-freezing, the result is an `order_block_candidate`; OHLC does
not reveal who placed the orders or whether an institution caused the move.

## Multi-timeframe structure

A reasonable research mapping is:

- 15m: entry timing and local invalidation;
- 1h: primary swing and setup structure;
- 4h: broader range and directional context;
- 1d: long-horizon location and regime.

This mapping is a hypothesis, not a universal truth. Each timeframe produces
its own closed-bar facts. Higher-timeframe bars must not be treated as closed
before their actual close.

## Primary failure modes

- hindsight selection of the visually best swing;
- using future bars to identify a pivot at the pivot timestamp;
- changing thresholds after seeing outcomes;
- counting synonymous events as independent confirmation;
- inferring hidden intent from OHLC;
- testing many patterns and reporting only the winners;
- ignoring fees, slippage, latency, and regime dependence.

## Research disposition

| Concept | Computability | Initial authority |
|---|---:|---|
| Confirmed swings | High | Observation-only |
| BOS / CHoCH | Medium-high after rule freeze | Observation-only |
| FVG | High after rule freeze | Observation-only |
| Sweep / failed breakout | Medium-high | Observation-only |
| Order-block candidate | Medium, parameter-sensitive | Research-only first |
| Institutional intent / smart-money identity | Not observable from OHLC | Explanation forbidden as fact |

No item in this report grants direction, sizing, Risk, or execution authority.
