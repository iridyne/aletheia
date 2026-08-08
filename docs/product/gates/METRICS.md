# Product Evaluation Metrics

> **Role:** Product Truth for metric meaning and source. This document defines
> what a metric can prove; it does not record current values, progress, or an
> observation-window checkpoint.
>
> Last aligned with code: 2026-08-08.

## 1. Primary outcome

The primary product outcome is **attributable, cost-aware improvement under
bounded authority**.

Activity is not improvement. Opportunities, reviews, lessons, exposures,
experiments, evaluator cycles, and PnL are inputs or observations. A Learning
contribution claim requires a prospective, identity-matched treatment/control
cohort whose actual intervention, outcomes, costs, harm state, and policy epoch
are all known.

The current measured path is `universe_family_calibration`. Its software evidence
unit is one complete assignment cluster. Selected members contribute fixed-horizon
net return; dropped members contribute zero under intent-to-treat allocation
utility. The current metric version is
`universe_family_calibration_cluster_allocation_utility_v8`.

## 2. Learning qualification metrics

| Metric | Meaning | Current source |
|---|---|---|
| Treatment/control assignment units | Complete, unique prospective clusters per arm | Learning cohort builder and `lco-v1` projection |
| First Paper trial integrity | `ufc-shadow-trial-integrity/v1`: 10 complete units per arm in one current eligible UTC market day; no contribution claim | Shadow verdict and protected owner admission |
| Minimum samples | 50 complete assignment units in each arm | `LearningExperiment.min_samples_per_arm` |
| Continuous market days | At least 7 consecutive UTC dates with a complete eligible exposure unit from either arm; 50-per-arm sufficiency is independent and scoped to that run | `LearningExperiment.min_market_days` and verdict `market_day_coverage` |
| Actual intervention | At least the per-arm minimum has consistent treatment permission and one or more members with a finite before/after behavior delta | Exposure manifest, before/after values, and cohort sample |
| Contribution delta | Cost-aware treatment mean minus control mean | `learning/verdicts.py` |
| Monitoring interval | Deterministic descriptive bootstrap interval for the contribution delta; never promotion authority | `learning/verdicts.py` |
| Confirmatory decision | One prospective, outcome-blind frozen cutoff and one consumed fixed look at authority alpha `0.025` | `learning/fixed_look.py` and `learning_inference_decisions` |
| Harm state | `indeterminate`, `clear`, or `breached`; underpowered evidence never claims safety | contribution verdict harm metrics |
| Measurement identity | Exact hypothesis, stage, epoch, horizon, outcome contract, metric version, and behavior-contract hash | Learning ledger/authority resolver |
| Operational health | Evaluator/lifecycle freshness, failures, watermark, and catch-up state | `learning_job_health` and `lco-v1` |

`min_market_days` is a consecutive coverage gate, not elapsed wall-clock span. A
UTC date with no eligible unit in either arm resets the current window; samples
before that gap do not satisfy counts, economics, confidence, or harm gates
afterward. Daily arm imbalance remains visible and is handled by the independent
50-per-arm gate. Planned or partial outages have no implicit waiver when they leave
a full UTC day uncovered. Metric v4 and UFC behavior contract v5 started the
continuous-window cohort. Metric v5 and UFC behavior contract v6 started another
from-zero cohort for explicit effective-intervention provenance. Metric v6 and UFC
behavior contract v7 add the prospective fixed-look authority contract: recurring
monitoring stays descriptive, one exposure-ordered cutoff is frozen without looking
at outcomes, and exactly one matching confirmatory verdict may be consumed. Later
samples cannot turn the same stage positive; current monitoring negative, invalid,
or determinate harm remains an anytime safety withdrawal. Older identities remain
immutable, non-authorizing history.
Metric v8 and UFC behavior contract v9 separate the one-day/10-per-arm Shadow
integrity admission from those unchanged economic promotion gates. This starts a
fresh cohort; the integrity verdict remains `insufficient_data` and grants no
automatic authority or contribution claim.

### Verdict meaning

| Verdict | Meaning |
|---|---|
| `insufficient_data` | Sample/time/actual-intervention requirement is unmet, or Shadow has only integrity readiness and no actual intervention |
| `invalid` | Identity, provenance, or economic evidence cannot support comparison |
| `negative` | Treatment trails control or breaches a determinate harm gate |
| `neutral` | Effect is practically small or confidence remains inconclusive |
| `positive` | ITT cost-aware treatment improvement clears the practical/confidence threshold, actual-intervention minimum, and harm gate; it grants promotion authority only when it is the consumed confirmatory fixed look |

A healthy evaluator or a large cohort count cannot substitute for one of these
measurement states.

## 3. Evidence-integrity guardrails

These are prerequisites, not weighted offsets for weak economics:

| Guardrail | Required interpretation |
|---|---|
| Causal identity | Opportunity, decision, proposal, RiskDecision, order, fill, position, and outcome links remain attributable |
| Outcome integrity | Administrative, synthetic, unresolved, or economically incomplete closes do not enter strategy/Learning performance |
| Exact venue/provider | Prices and funding preserve their actual source identity; cross-venue substitution is not evidence |
| Cohort isolation | Old policy epochs, behavior contracts, stages, horizons, and conflicting sources cannot authorize current behavior |
| Control holdout | Paper authority allocation never exceeds its configured ceiling and retains a control arm |
| Safety withdrawal | Negative, invalid, stale, or harmful current evidence restores/fails to the static baseline |
| Runtime continuity | Qualification provenance and scheduler/outcome/evaluator continuity are verified from current runtime facts |

## 4. Trading and decision quality

The following metrics describe eligible genuine outcomes only. They may evaluate
paper behavior or contribute to readiness, but do not by themselves prove Learning
causality:

- net realized PnL and cost completeness;
- profit factor and win rate;
- max drawdown;
- trade volume and opportunity-to-outcome funnel conversion;
- conviction calibration/ECE;
- family, factor, thesis, and decision attribution;
- Risk rejection/violation and reconciliation integrity.

Analytics must expose exclusions, window identity, and unavailable states. A zero
must never stand in for a failed read or missing evidence.

## 5. Go-Live Scorecard

`aletheia.analytics.go_live_scorecard.compute_scorecard()` currently evaluates ten
criteria and a separate positive-net-PnL gate:

| Criterion | Weight | Code target |
|---|---:|---|
| Eligible trade volume | 0.135 | 50 closed genuine outcomes |
| Profit factor | 0.18 | 1.2; PF below 0.5 scores zero |
| Win rate | 0.135 | 45% |
| Max drawdown | 0.135 | below 10% |
| Exchange connectivity | 0.09 | cached state younger than 300 seconds |
| Risk violations | 0.09 | zero violation events in 24 hours |
| Data coverage | 0.045 | complete current OHLCV coverage |
| Model health | 0.045 | at least one provider available |
| Reconciliation | 0.045 | zero reconciliation errors in 7 days |
| Conviction calibration | 0.10 | current report, target ECE below 0.10 |

The composite is red below 50, yellow from 50 through 75, and green above 75.
Live eligibility is stricter than color: the protected live contract also requires
the configured score threshold, zero blockers, positive eligible net realized PnL,
the remaining live safety gates, and explicit human authorization. The scorecard
is readiness evidence, not permission to trade.

## 6. Canonical projections

| Question | Canonical projection |
|---|---|
| Is measured Learning effective? | `GET /api/analytics/learning-contribution` (`lco-v1`) |
| Are current outcomes eligible? | Outcome Integrity assessment and eligible-outcome consumers |
| How is paper trading performing? | Trade insights/performance summary with explicit window metadata |
| Is live readiness satisfied? | Go-Live Scorecard plus the live-unlock contract |
| Is this Runtime healthy now? | `uv run aletheia doctor`, runtime provenance, logs, and current data |

Runtime values and observation progress belong in runtime evidence and GitHub,
not in this file.
