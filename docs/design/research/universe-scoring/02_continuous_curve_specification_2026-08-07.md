# Universe Continuous Curve Specification

> Date: 2026-08-07
>
> Status: accepted curve evidence for non-authoritative implementation; not active
>
> Baseline: `universe-step-v1`, frozen in commit `93effc79`
>
> Proposed identity: `universe-continuous-shadow-v1` (reserved, not active)
>
> Scope: existing Universe inputs and score path only
>
> Non-goals: new market signals, strategy redesign, quota changes, emission
> threshold changes, factor Learning, Paper authority, Risk/Execution changes,
> or Fedora deployment

> Future authority and the mandatory four safeguards are owned by the accepted
> [Universe continuous scoring safety Proposal](../../proposals/universe_continuous_scoring_safety.md).
> This dated specification supplies its exact curve anchors; it is not a current
> behavior Contract.

## 1. Approval summary

This contract replaces the current fixed factor jumps with deterministic,
bounded curves while retaining:

- the six existing scanner families and their current direction precedence;
- every current maximum factor contribution and the final `0..100` cap;
- the allocator weights `0.35 opportunity + 0.25 tradability + 0.15 liquidity`;
- existing hard tradability, direction, no-chase, and execution-safety gates;
- the old scorer as the only authority during the later twin-score phase;
- missing inputs as explicit states rather than invented market observations.

The recommended interpolation is a source-controlled, shape-preserving cubic
Hermite curve. It is continuous, locally reactive, bounded by visible anchors,
and cannot overshoot. Sigmoid/tanh tails and learned parameters are excluded.

Acceptance of this specification authorizes only task 3 implementation behind a
new non-authoritative scorer and only with the Proposal's raw-evidence and
pessimistic-missingness interfaces. It does not authorize ranking, Opportunity,
Paper, or Learning influence.

The existing `universe_family_calibration` Learning path, operator scanner
weights, family quotas, regime gates, explore/emission thresholds, and score
labels are not curve factors and are not redesigned here. A later twin scorer
must apply the same already-authorized external multiplier to both arms, record
it separately, and default it to `1.0`; factor-level Learning remains forbidden.

## 2. Runtime evidence used to place the anchors

Read-only Fedora Paper snapshot:

| Fact | Value |
| --- | ---: |
| Feature rows | 5,336 |
| Feature window | 2026-08-07 01:56:58 to 13:16:18 UTC |
| Universe member rows | 1,944 |
| RSI 1h p01 / p50 / p99 | 25.12 / 49.97 / 79.56 |
| RSI 4h p01 / p50 / p99 | 24.78 / 52.80 / 85.40 |
| 4h return p05 / p50 / p95 | -4.94% / 0.05% / 5.68% |
| 24h return p05 / p50 / p95 | -11.22% / 0.57% / 25.21% |
| Relative strength 24h p10 / p50 / p90 | -5.29% / 0.67% / 12.47% |
| ATR 1h p75 / p90 / p95 | 2.86% / 5.60% / 7.34% |
| Volume ratio p75 / p90 / p95 / p99 | 0.61 / 1.27 / 2.07 / 5.53 |
| Funding p25 / p50 / p75 / p90 | -0.0000023 / 0.0000340 / 0.0000596 / 0.0002467 |
| Taker ratio availability | not available: 0 / 5,336 observed |
| OI-delta availability | 623 / 5,336 observed; 88.3% missing |
| Spread availability | 1,802 / 5,336 observed; 66.2% missing |
| Depth availability | 819 / 5,336 observed; 84.7% missing |

The snapshot is placement evidence, not outcome evidence. It helps avoid curves
whose transition lies entirely outside observed inputs; it does not prove that
an anchor is profitable. Taker-ratio performance cannot be inferred from this
runtime because its observed sample is `not available`.

A follow-up read-only envelope check over 5,543 feature rows through 13:26:25
UTC estimates how often a single core input enters the proposed transition
region. These are not predicted candidates or trades because other factors,
hard gates, family competition, and quotas still apply.

| Core input | Step-v1 non-zero region | Proposed transition-or-higher region |
| --- | ---: | ---: |
| Momentum 24h RS | 52.9% | 66.7% |
| Mean-reversion RSI 1h | 2.9% | 7.6% |
| Mean-reversion return 4h | 5.4% | 10.6% |
| Volatility direction return 4h | 28.6% | 48.0% |
| Derivatives funding, observed rows | 76.1% | 79.0% |
| Derivatives OI delta, observed rows | 5.2% | 14.7% |
| Event return 1h | 3.4% | 6.6% |
| Short-bias core return 4h | 22.2% | 22.2% |

The volatility and momentum envelopes broaden materially. That is the primary
reason the continuous scorer must remain twin-score Shadow until full-candidate
rank turnover and emission deltas are measured.

## 3. Normative curve representation

For ordered anchors `[(x0, y0), ..., (xn, yn)]`, define:

```text
H(x; anchors) = shape-preserving cubic Hermite interpolation
```

The implementation contract is:

1. use the Fritsch-Carlson monotone-slope rules on each monotone run;
2. never overshoot the adjacent anchor values;
3. return `y0` below `x0` and `yn` above `xn`;
4. return the exact anchor value at every anchor;
5. allow plateaus and bounded single-peak/single-valley bands;
6. reject non-finite inputs; do not coerce them to zero;
7. introduce no runtime dependency; implement the small deterministic helper in
   the repository and cover it with property tests;
8. round only for display. Ranking and shadow persistence retain the float.

Common transforms:

| Symbol | Definition |
| --- | --- |
| `d` | `+1` for long, `-1` for short |
| `aligned(x, d)` | `d * x`; positive values support the selected direction |
| `magnitude(x)` | `abs(x)` |
| `rsi_extreme` | `abs(rsi - 50)` |
| `taker_aligned` | `ratio` for long, `1 / ratio` for short |
| `clip01(x)` | clamp to `[0, 1]` |

All additive family subtotals are clamped to `[0, 100]`. A negative curve value
is a penalty. A multiplier is applied only where the family table explicitly
states it. A non-positive taker ratio and a squeeze ratio with a non-positive
denominator are invalid inputs, not zero or missing observations.

## 4. Hard gates that remain outside continuous scoring

These are not converted into soft points in this proposal:

| Gate | Proposed behavior |
| --- | --- |
| Stablecoin / configured blacklist | reject unchanged |
| Quote volume below configured minimum | reject unchanged |
| Critically shallow depth below `0.1 × min_depth` | reject unchanged |
| Coarse/fine extreme-spread rejection | reject unchanged |
| Data older than the hard freshness limit | reject unchanged |
| Derivatives family on a non-derivatives market | family invalid unchanged |
| No directional core evidence | family score `0`, direction `None` |
| Aligned 15m candle range above `1.5 ATR` | no-chase rejection unchanged |
| Explore admission score `20` | unchanged and step-v1-authoritative in Shadow |
| Opportunity emission score `45` | unchanged and step-v1-authoritative in Shadow |
| High-label threshold `60` and regime override threshold | unchanged and step-v1-authoritative in Shadow |
| Existing `late_entry` / `crowded` / `wide_spread` risk flags | derived from step-v1 components only in Shadow |
| Missing live market snapshot at emission | execution-ineligible unchanged |

Keeping these gates means the complete candidate decision is not globally
continuous. The score is continuous inside the admissible region; safety and
data-validity boundaries remain explicit decisions.

Scanner-internal score buckets are different from the hard gates above. The
volatility direction threshold, event displacement threshold, and short-bias
funding/RSI rejection steps become zero-to-one curve multipliers. Their
transition regions therefore admit small non-zero Shadow scores where step-v1
was zero. This is intentional and must be measured against the frozen scorer;
it receives no authority in tasks 3 or 4.

## 5. Momentum family

Direction precedence remains: beta-adjusted 24h relative strength, then raw
24h relative strength, then 4h-return fallback. A present numeric zero is an
observed zero and no longer triggers fallback.

| ID | Input / transform | Anchors `(x, contribution)` | Cap | Missing semantics |
| --- | --- | --- | ---: | --- |
| MOM-01 | `magnitude(RS24h)`; sign sets direction | `(1.5,0), (3.5,20), (4.0,20), (6.0,30)` | +30 | beta-adjusted missing → raw RS; both missing → MOM-02 |
| MOM-02 | `magnitude(return_4h)` fallback | `(2.0,0), (4.0,15)` | +15 | family invalid if MOM-01 and return are missing |
| MOM-03 | `aligned(RS4h,d)` | `(0.5,0), (1.5,15)` | +15 | `0`, observed=false |
| MOM-04 | RSI 1h directional band | long `(50,0),(55,5),(60,10),(70,10),(75,5),(80,0)`; short `(20,0),(25,5),(30,10),(40,10),(45,5),(50,0)` | +10 | `0`, observed=false |
| MOM-05 | RSI 4h directional band | MOM-04 anchors with contributions divided by 2 | +5 | `0`, observed=false |
| MOM-06 | `vol_ratio` | `(1.0,0),(1.5,5),(2.0,10),(3.0,15),(5.0,20)` | +20 | `0`, observed=false |
| MOM-07 | `taker_aligned` | `(1.0,0),(1.2,5),(1.5,10)` | +10 | `0`, observed=false |
| MOM-08 | `aligned(oi_delta_1h,d)` | `(0,0),(5,5),(10,10)` | +10 | `0`, observed=false |
| MOM-09 | `aligned(funding_rate,d)` | `(0.00005,0),(0.00010,-7.5),(0.00020,-15)` | -15 | `0`, observed=false |
| MOM-10 | `magnitude(return_24h)` | `(7.5,0),(10,-10),(15,-20)` | -20 | `0`, observed=false |

MOM-04 and MOM-05 are intentionally bounded bands: moderate directional RSI
confirms momentum, while very high/low RSI stops adding momentum points. This
preserves the current sign and cap without treating every value inside a wide
band as identical.

## 6. Mean-reversion family

Direction precedence remains RSI 1h → RSI 4h → return 4h → return 24h. To keep
this change isolated to continuity, this proposal does not redesign legacy
direction precedence or family selection.

| ID | Input / transform | Anchors `(x, contribution)` | Cap | Missing semantics |
| --- | --- | --- | ---: | --- |
| MR-01 | `rsi_extreme` 1h; side of 50 sets reversion direction | `(20,0),(25,20),(30,35),(40,35)` | +35 | `0`; next core may set direction |
| MR-02 | `rsi_extreme` 4h | `(15,0),(25,15),(35,15)` | +15 | `0`; next core may set direction |
| MR-03 | `magnitude(return_4h)`; opposite side is reversion direction | `(5,0),(8,25),(12,25)` | +25 | `0`; next core may set direction |
| MR-04 | `magnitude(return_24h)`; opposite side is reversion direction | `(8,0),(12,15),(20,15)` | +15 | `0` |
| MR-05 | `vol_ratio`, decreasing | `(0.0,15),(0.4,15),(0.7,15),(1.0,0)` | +15 | `0`, observed=false |
| MR-06 | crowding funding: `funding` for short, `-funding` for long | `(0.00002,0),(0.00005,15),(0.00010,15)` | +15 | `0`, observed=false |

As in the current scorer, MR-05 cannot create a direction by itself. MR-06 is
evaluated only after a direction exists.

## 7. Volatility family

The current Boolean `bb_squeeze` destroys the degree of compression. The
continuous scorer therefore exposes the already-computed ratio
`bb_squeeze_ratio = current_width / rolling_average_width`. This is not a new
market signal; it is the numeric value currently collapsed into the Boolean.
It must be persisted for audit before it can contribute.

| ID | Input / transform | Anchors `(x, contribution)` | Cap | Missing semantics |
| --- | --- | --- | ---: | --- |
| VOL-01 | ATR 1h percent | `(2,0),(3,20),(5,30),(8,30)` | +30 | `0`, observed=false |
| VOL-02 | `bb_squeeze_ratio`, decreasing | `(0.4,15),(0.6,15),(0.8,5),(1.0,0)` | +15 | use VOL-03 fallback |
| VOL-03 | absolute BB width 1h, decreasing fallback | `(1,10),(2,10),(4,0)` | +10 | `0`, observed=false |
| VOL-04 | ATR 4h percent | `(2,0),(4,12),(8,12)` | +12 | `0`, observed=false |
| VOL-05 | BB width 4h, decreasing | `(1.5,12),(3,12),(6,0)` | +12 | `0`, observed=false |
| VOL-06 | `magnitude(return_4h)` direction multiplier | `(1,0),(2,0.5),(3,1)` | ×1 | family invalid if missing; sign sets direction |
| VOL-07 | `taker_aligned` | `(1.0,0),(1.2,5),(1.5,10)` | +10 | `0`, observed=false |
| VOL-08a | known-volume subtotal multiplier | `(0,0.5),(1.0,0.5),(1.5,1)` | ×1 | missing uses fixed ×0.7 |
| VOL-08b | known-volume bonus | `(1.5,0),(2,10),(3,25)` | +25 | missing adds `0` |

Order is normative: sum VOL-01..05 and VOL-07, multiply by VOL-06, then apply
VOL-08a and add VOL-08b. This preserves the current principle that weak volume
discounts the whole breakout thesis.

## 8. Derivatives family

Market type remains a hard family gate. Funding keeps direction priority over
OI/price and divergence, matching the current behavior.

| ID | Input / transform | Anchors / formula | Cap | Missing semantics |
| --- | --- | --- | ---: | --- |
| DER-01 | `magnitude(funding_rate)`; sign sets opposite direction | `(0.00001,0),(0.00002,10),(0.00005,25),(0.00010,40),(0.00020,40)` | +40 | `0`; other core may set direction |
| DER-02 | `magnitude(oi_delta_1h)` | `(5,0),(10,20),(20,30),(30,30)` | +30 | `0`, observed=false |
| DER-03 | rising-OI price direction activation | `a_oi=H(oi;[(5,0),(10,1)])`; `a_ret=H(abs(ret_1h);[(0.25,0),(0.5,1)])`; activation=`a_oi*a_ret`, sign from return | ×1 | cannot set direction unless both observed |
| DER-04 | continuous OI/price divergence | qualifying opposite signs only; `15 * H(abs(oi);[(3,0),(10,1)]) * H(abs(ret_1h);[(0.5,0),(2,1)])` | +15 | `0`, observed=false |
| DER-05 | RSI/return proxy when funding and OI are both missing | short uses `RSI-50` and positive return; long uses `50-RSI` and negative return; `20 * H(directional_RSI_extreme;[(20,0),(25,1)]) * H(aligned_reversion_return;[(3,0),(5,1)])` | +20 | invalid unless RSI and return observed; remains `is_proxy=true` |

When funding has already set direction, DER-02 contributes at full magnitude as
today. When OI must set direction, DER-02 is multiplied by DER-03 so its score
approaches zero continuously as the price-direction evidence disappears.
DER-04 replaces the existing `{-1,0,+1}` divergence bucket using the same OI
and return inputs; no additional market feed or learned interaction is added.

## 9. Event family

| ID | Input / transform | Anchors / formula | Cap | Missing semantics |
| --- | --- | --- | ---: | --- |
| EVT-01 | `magnitude(return_1h)`; sign sets direction | `(2.5,0),(4,25),(8,45),(12,45)` | +45 | family invalid if missing |
| EVT-02 | `taker_aligned` | `(1,0),(1.5,15),(2,15)` | +15 | `0`, observed=false |
| EVT-03 | `magnitude(oi_delta_1h)` | `(7.5,0),(15,10),(25,10)` | +10 | `0`, observed=false |
| EVT-04 | directional RSI extension multiplier, `abs(rsi-50)` on event side | `(20,1),(30,0.85),(40,0.7)` | ×1 | ×1, observed=false |
| EVT-05 | `aligned(return_4h,d)` | `(1.5,0),(3,5),(6,5)` | +5 | `0`, observed=false |
| EVT-06a | known-volume subtotal multiplier | `(0,0.5),(1,0.5),(1.5,1)` | ×1 | missing uses fixed ×0.7 |
| EVT-06b | known-volume bonus | `(1.5,0),(2,20),(4,30),(6,30)` | +30 | missing adds `0` |

Order is normative and follows the existing scorer: sum EVT-01..03, multiply by
EVT-04, add EVT-05, apply EVT-06a, then add EVT-06b. EVT-04 uses multiplier
`1` when RSI is on the opposite side of 50 from the event direction; only
extension in the chase direction is discounted.

## 10. Short-bias family

The family remains short-only. A missing 24h return becomes family-invalid
because the existing source calls 24h confirmation “required”; allowing missing
data to bypass that gate is semantically inconsistent.

| ID | Input / transform | Anchors / formula | Cap | Missing semantics |
| --- | --- | --- | ---: | --- |
| SH-01 | decline magnitude `-return_4h` | `(1,0),(2.5,22),(5,30),(8,30)` | +30 | family invalid |
| SH-02a | 24h negative-trend multiplier `-return_24h` | `(0,0),(1,0.5),(3,1)` | ×1 | family invalid; nonnegative value gives ×0 |
| SH-02b | 24h alignment bonus | `(1,0),(3,15),(6,15)` | +15 | family invalid |
| SH-03 | relative weakness magnitude `-RS24h` | `(1.5,0),(3,15),(6,15)` | +15 | `0`, observed=false |
| SH-04a | positive funding support | `(0.00002,0),(0.00005,10),(0.00010,10)` | +10 | `0`, observed=false |
| SH-04b | negative-funding crowding multiplier | on `abs(min(funding,0))`: `(0.00005,1),(0.00010,0.5),(0.00020,0)` | ×1 | ×1, observed=false |
| SH-05a | RSI 1h oversold-risk multiplier | `(15,0),(25,0.5),(30,1)` | ×1 | ×1, observed=false |
| SH-05b | RSI 1h bearish-confirmation band | `(25,0),(35,8),(45,8),(55,0)` | +8 | `0`, observed=false |
| SH-06a | RSI 4h oversold-risk multiplier | `(15,0),(25,0.5),(30,1)` | ×1 | ×1, observed=false |
| SH-06b | RSI 4h bearish-confirmation band | `(25,0),(35,10),(45,10),(55,0)` | +10 | `0`, observed=false |
| SH-07a | liquidation magnitude `-oi_delta`, negative side only | `(5,0),(10,12),(20,12)` | +12 | `0`, observed=false |
| SH-07b | new-short OI, positive side only | `(5,0),(10,10),(20,10)` | +10 | `0`, observed=false |

Order is normative: form the additive subtotal from SH-01, SH-02b, SH-03,
SH-04a, SH-05b, SH-06b, and one SH-07 branch; multiply by SH-02a, SH-04b,
SH-05a, and SH-06a; clamp to `[0,100]`.

## 11. Tradability score

Hard filters run before this score and remain unchanged. Ratios below use the
configured minimums, so configuration remains the authority.

| ID | Input / transform | Anchors `(x, contribution)` | Cap | Missing semantics |
| --- | --- | --- | ---: | --- |
| TRD-01 | `quote_volume_24h / min_volume` | `(0,0),(1,20),(2,30),(5,40)` | +40 | hard filter rejects missing/zero |
| TRD-02 | observed `spread_bps`, decreasing | `(0,35),(5,35),(10,28),(25,20),(45,0)` | +35 | explicit imputed +15, `observed=false` |
| TRD-03 | `data_freshness_sec` | `(0,25),(300,25),(3600,20),(7200,10),(9000,0)` | +25 | family candidate invalid if unavailable |
| TRD-04 | `depth_10bps_usd / min_depth` | `(0,-10),(0.1,-10),(0.25,0),(1,5),(3,10)` | +10 / -10 | `0`, observed=false |

Spread remains imputed at +15 when unavailable because 66.2% of the current
sample lacks spread; changing it to zero in the continuity patch would combine
a data-feed policy change with the curve change and could collapse coverage.
The imputation must be persisted and displayed as imputed, never as an observed
spread quality fact.

## 12. Allocator score

The final formula and family weights do not change.

| ID | Input / transform | Anchors / formula | Cap | Missing semantics |
| --- | --- | --- | ---: | --- |
| ALLOC-01 | `quote_volume_24h / min_volume` liquidity | `(1,40),(2,60),(5,75),(10,90),(20,90)` | +90 before ×0.15 | hard-filter-invalid if missing |
| ALLOC-02 | observed spread cost | `min(20, max(0, spread_bps) * 0.5)` | -20 | explicit imputed cost 5 from 10 bps |
| ALLOC-03a | `magnitude(return_1h)` late penalty | `(2,0),(3,4),(4,8),(5,11.5),(6,15)` | -15 | `0`, observed=false |
| ALLOC-03b | `magnitude(return_4h)` late penalty | `(5,0),(7,2.5),(8.5,5),(10,7.5),(12,10)` | -10 | `0`, observed=false |
| ALLOC-03c | `rsi_extreme` late penalty | `(22,0),(28,2.5),(31.5,5),(35,7.5),(40,10)` | -10 | `0`, observed=false |
| ALLOC-04 | directional crowding interaction | `15 * a_rsi * a_ret`; `a_rsi=H(rsi_extreme;[(15,0),(20,0.7302967433),(30,1)])`; `a_ret=H(aligned(return_4h,d);[(2,0),(3,0.7302967433),(5,1)])` | -15 | `0`, observed=false |
| ALLOC-05 | staleness penalty | `(1200,0),(1800,2.5),(2400,5),(3600,7.5),(5400,10)` | -10 | candidate invalid if freshness missing |

The combined ALLOC-03 penalty remains capped at 30. ALLOC-04 preserves the old
8-point and 15-point joint-risk landmarks: `0.7302967433² × 15 = 8`.

Observed `spread_bps=0` now produces zero estimated cost. Only missing spread
uses the explicit 10-bps prior. This removes the current falsy-value ambiguity
without pretending a missing spread was measured.

## 13. Missing-value contract

Every contribution record in the later Shadow scorer must contain
`value`, `observed`, `fallback_source`, `contribution`, and `curve_id`. These
tables define the explicit **point-score** imputation used for comparison. They
do not make that point score authority-eligible. The separate pessimistic score,
raw-feature identity, missingness bounds, source-availability rules, and
promotion semantics are normative in the accepted
[safety Proposal](../../proposals/universe_continuous_scoring_safety.md).

| State | Required treatment |
| --- | --- |
| Numeric zero | observed zero; never trigger `or` fallback |
| `None` / absent | apply the factor-specific policy above and set `observed=false` |
| NaN / infinity / invalid type | reject as invalid input; never coerce |
| Spread missing | impute score +15 and cost 5, both labelled imputed |
| Depth missing | contribution 0, candidate may continue as today |
| Taker missing | contribution 0; no claim of neutral order flow |
| Funding/OI missing | contribution 0; derivatives proxy only under its existing both-missing rule |
| Freshness missing | candidate invalid; freshness cannot be inferred |
| Short-bias 24h return missing | family invalid; required trend cannot be inferred |
| Volume ratio missing in volatility/event | retain explicit ×0.7 uncertainty multiplier |

## 14. Invariants and property tests required in task 3

1. Exact value at every anchor and endpoint clamping.
2. No overshoot between any adjacent anchors.
3. Continuity within `1e-9` around every anchor.
4. Monotonicity for monotone curves and one bounded peak/valley for bands.
5. Intended long/short symmetry for RSI, return magnitude, funding magnitude,
   and taker alignment.
6. Every additive contribution stays inside its declared cap.
7. Missing and invalid values follow the table; zero is never missing.
8. Existing hard-gate decisions are byte-for-byte identical.
9. Old scorer output is unchanged and still passes the frozen golden fixture.
10. New scorer cannot be imported into the authoritative allocation path in
    task 3; task 4 owns twin wiring.
11. Every raw input has one stable feature/group identity and all duplicate
    consumers are enumerated; its aggregate continuous influence does not exceed
    the frozen step-v1 feature budget.
12. For every missing input, the pessimistic score is no greater than the point
    score or the score under any admissible observed value; required/invalid
    fields remain invalid.
13. Concentration, single-source evidence, missingness delta, and source identity
    are deterministic and fail closed when required metadata is absent.

## 15. Explicit owner decisions

Acceptance of task 2 means accepting these six curve choices plus all four
safeguards in the linked Proposal for Shadow implementation:

1. shape-preserving cubic Hermite interpolation with the exact anchors above;
2. exposure of the existing numeric BB squeeze ratio instead of its Boolean
   bucket;
3. continuous reconstruction of the existing OI/price divergence bucket from
   the same two raw inputs;
4. observed zero is distinct from missing, missing freshness and missing
   short-bias 24h trend invalidate that candidate/family, while missing spread
   keeps an explicit labelled prior;
5. scanner-internal direction/quality steps become bounded multipliers and can
   create small Shadow scores inside the declared transition regions;
6. existing direction precedence, family selection, safety/data hard gates,
   score cutoffs, total weights, caps, and authoritative step scorer remain
   unchanged; Shadow-derived risk flags cannot reach trading guards.

Any requested anchor change must be made here before task 3. Task 3 will not
choose or tune values while implementing them. It also cannot omit the Proposal's
raw-evidence aggregation or pessimistic score as “later observability”; those are
part of the pure scorer return contract.
