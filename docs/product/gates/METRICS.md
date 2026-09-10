# Product Evaluation Metrics

> **Role:** Product Truth for metric meaning and source. This document defines
> what a metric can prove; it does not record current values, progress, or an
> observation-window checkpoint.
>
> Superseded simulator and legacy Learning metric definitions are
> [archived](../../design/history/local_simulated_product_gates.md).

## 1. Primary outcome

The primary product outcome is **attributable, cost-aware improvement under
bounded authority**.

Activity is not improvement. Opportunities, reviews, lessons, exposures,
experiments, evaluator cycles, and PnL are inputs or observations. A Learning
contribution claim requires a prospective, identity-matched treatment/control
cohort whose actual intervention, outcomes, costs, harm state, and policy epoch
are all known.

Path-specific statistical units, metric versions, sample/time thresholds and
influence gates belong to the [Learning contract](../../design/contracts/learning_contract.md),
its registry and the exact Observation Lab generation contract. A product metric
page does not define a second set of admission thresholds.

## 2. Learning qualification metrics

Observation Lab is the canonical online Experiment authority. The read-only
`lco-v1` projection reports persisted windows, results, exposures, inference plans
and lifecycle events. It does not recompute a verdict or grant promotion authority.

| Metric | Meaning | Current source |
|---|---|---|
| Assignment and intervention counts | Distinct treatment/control assignment units, applied permission and actual behavior change remain separate facts | Lab exposures and `lco-v1` samples |
| Measurement result | Persisted verdict, reason, baseline, observed value and delta; missing fields remain unavailable | Lab result and its domain payload |
| Inference identity | Registered plan, cutoff fingerprint, evaluation role and consumed evaluation identity | Lab inference plan and evaluation |
| Measurement identity | Exact observation, generation, policy epoch, stage, horizon, metric and behavior/outcome contracts | Lab generation contract |
| Lifecycle and adoption | Persisted event and owner-adoption requirement; a result is not admission | Lab lifecycle events and path-specific authority gate |
| Freshness | Age and availability of persisted evidence, distinct from economic validity | `lco-v1` data freshness |

Verdicts must be interpreted under their exact registered experiment contract.
The read projection reports `not_measured` when no recognized persisted verdict
exists and unavailable when canonical authority cannot be read. It exposes
`authority_eligible=false` and no lifecycle mutation; a positive display alone
cannot promote a treatment. A healthy evaluator, large sample count or historical
simulator result does not qualify an external-account intervention.

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
external demo/testnet behavior or contribute to readiness, but do not by themselves
prove Learning causality:

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
criteria and a separate positive-net-PnL gate. Outcome-based criteria read the
selected external-account cohort; unresolved account selection supplies no outcome
evidence. Historical simulator outcomes do not qualify this cohort:

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
| How is the selected external account performing? | Trade insights/performance summary with explicit window metadata |
| Is live readiness satisfied? | Go-Live Scorecard plus the live-unlock contract |
| Is this Runtime healthy now? | `uv run aletheia doctor`, runtime provenance, logs, and current data |

Runtime values and observation progress belong in runtime evidence and GitHub,
not in this file.
