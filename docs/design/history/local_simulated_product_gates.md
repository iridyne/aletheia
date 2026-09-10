# Archived product gates and metrics before local exchange retirement

**Archived.** These are the former product definitions at commit `ce8a680da`.
They include local-simulator accounting and legacy Learning promotion claims;
they do not authorize current execution or qualify external accounts.
The separately approved CN Equity proposal remains a disabled implementation
boundary; preserving it here does not admit a runtime or account.
Current definitions: [admission gates](../../product/gates/PHASE_GATES.md) and
[metric meanings](../../product/gates/METRICS.md).

---

# Current Admission And Promotion Gates

> **Role:** Product Truth for current admission decisions. These gates express
> durable prerequisites; they are not a roadmap, mutable checklist, or statement
> that any Runtime currently passes.
>
> Runtime facts come from Doctor, provenance, logs, and the selected data root.
> Current ownership and progress live in GitHub. Historical strategy-phase gates
> were retired when the RuleSet/DSL/backtest decision path was removed.
>
> Last aligned with code and contracts: 2026-08-31.

## Gate A - Active paper mainline eligibility

Evidence may count toward the current MVP only when all of the following are true:

1. execution mode and decision path match
   [Active Runtime Scope](../../product/ACTIVE_RUNTIME_SCOPE.md);
2. the running code, instance/data root, runtime session, policy epoch, and
   relevant behavior/outcome contracts are identifiable;
3. Startup -> Scheduler -> Opportunity -> Decision -> Risk -> Execution ->
   Outcome is operational without an unresolved integrity incident;
4. Live remains disabled and every order path still crosses Risk approval and the
   Execution Gateway;
5. paper accounting is cost-complete and the two-table paper-ledger invariant is
   healthy;
6. unavailable, stale, synthetic, administrative, or unresolved facts are
   excluded or displayed honestly rather than converted to success/zero.

Passing a test suite does not pass this gate for a Runtime. Use
`uv run aletheia doctor` and current runtime evidence.

## Gate CN - Bounded CN Equity implementation and promotion

The accepted CN Equity Proposal and Product/Runtime scope authorize implementation
only. They do not pass a Runtime gate or make CN Equity supported. Promotion is
strictly one-way through `disabled -> observe -> shadow -> paper`; a failure
returns the extension to the last proved lower state without rewriting evidence.
No `live` state exists.

| Target state | Required proof before entry |
|---|---|
| `disabled` | Default. No CN V2 producer or CN Candidate/Proposal/reservation/order/accounting write is reachable. Contracts and compatibility readers may be merged behind this fence. |
| `observe` | Versioned CN identity, calendar/rules/tradability, point-in-time data manifest, and Universe publication are complete; missing/stale/unknown facts fail closed; observation has no Proposal, reservation, Risk approval, Gateway call, or execution side effect. |
| `shadow` | The complete Candidate → Plan → exact Proposal → Risk → Gateway → exit → Outcome chain is contract- and spec-conformant, but execution is denied and no executable approval or accounting mutation is emitted. An exact merged commit and frozen data/policy/account/experiment identity start a fresh window. |
| `paper` | The fresh Shadow window is clean; the owner explicitly admits the exact epoch; only the dedicated CNY Internal Paper account/Risk pool is reachable; settlement, fees, tax, reservations, corporate actions, restart, reconciliation, and canonical entry/exit are proven. |

CN qualification closes as two independent facts:
`paper_accounting_qualified` and exactly one of
`strategy_paper_promotion_passed` or `strategy_paper_promotion_failed` for
the same frozen epoch. A failed strategy result preserves valid accounting proof
but forbids strategy promotion. Backtest, Shadow, Paper, and any future broker
evidence are separate classes and cannot be pooled. Capability Ground Truth is
updated only from exact merged/runtime evidence and must continue to report Live
as unavailable.

## Gate B - Learning shadow measurement readiness

Only a registry path with all three capabilities may create current formal
measurement evidence:

- `consumer_wired=true`;
- `settings_supported=true`;
- `measurement_supported=true`.

The current set contains exactly one path: `universe_family_calibration`. Every
other declared treatment hook returns its static fallback and must not create a
new hypothesis, experiment, exposure, policy epoch, or authority claim.

A current shadow measurement additionally requires:

| Requirement | Current software rule |
|---|---|
| Prospective cohort | Stable assignment at the persisted assignment-unit cluster |
| Complete outcomes | Canonical full-arm outcome or valid selected-member mirror/fallback; conflicts fail closed |
| First-trial integrity sample | 10 complete assignment units in treatment and 10 in control |
| First-trial integrity time | At least one continuous UTC date with a complete eligible exposure unit from either arm |
| Economic promotion sample/time | After owner admission, every Paper stage still requires 50 complete units per arm inside seven consecutive eligible UTC dates |
| Exact identity | Current hypothesis, stage, epoch, horizon, outcome contract, metric version, and behavior-contract hash |
| Harm semantics | First-trial underpower may remain `indeterminate/insufficient_samples`; any determinate breach blocks admission or authority |
| Actual intervention | Shadow has none and yields `insufficient_data/trial_integrity_ready_no_actual_intervention` after the integrity gate; it cannot claim contribution |

A UTC date with no complete eligible unit in either arm resets the current
qualification run. Daily arm imbalance remains visible and is handled by the
independent per-arm sample gate. Planned or partial outages have no implicit waiver
when they leave a full day uncovered; pre-gap samples remain history and cannot
satisfy post-gap sample or outcome gates. Admission requires the current
one-day integrity window; promotion still requires the full qualified seven-day
window.

## Gate C - Explicit shadow-to-paper trial admission

Shadow evidence never self-authorizes. The scheduled lifecycle controller may
recommend `admit_trial`, but only the protected authenticated owner route may
cross from `shadow` to `paper_10`.

Admission requires all of the following at the moment of mutation:

1. exact current shadow verdict is `insufficient_data` with reason
   `trial_integrity_ready_no_actual_intervention` (or the stricter mature
   `no_actual_intervention` result);
2. verdict, experiment, hypothesis, stage, policy epoch, horizon, metric, outcome
   contract, and behavior-contract identities all match;
3. there is no determinate harm breach; the integrity result may be
   `indeterminate` only for `insufficient_samples`;
4. the experiment is active and the path remains measurement-supported;
5. autonomous Learning, contribution evaluation, lifecycle automation, paper
   influence, and the exact path allowlist are enabled;
6. Runtime is not paused and no global reconciliation refreeze is active;
7. owner supplies the expected epoch and reason through the protected route;
8. one transactional lifecycle event closes shadow and creates a fresh
   `paper_10` successor with a new assignment salt.

Missing, stale, malformed, or changed evidence fails closed. Read-only owner
surfaces do not expose an admission mutation.

## Gate D - Bounded paper progression

The supported progression is:

```text
shadow --explicit owner admission--> paper_10 -> paper_25 -> paper_50 -> paper_active
```

After owner admission, automated progression is allowed only one stage at a time
when the current stage has an authority-eligible, identity-matched, harm-safe
`confirmatory` verdict consumed exactly once from its prospective frozen cutoff and
all promotion gates remain enabled. A recurring `monitoring` positive is descriptive
and can never promote. Allocation is bounded to 10%, 25%, 50%, then the configured
`paper_active_allocation_pct` (default 80%, maximum 90%) so a control holdout remains.

The primary result remains randomized intent-to-treat: all qualified assigned
treatment units, including baseline fallbacks, contribute their realized outcome to
the treatment mean, confidence interval and harm calculation. Actual intervention is
a separate promotion gate. `authority_applied` records permission consistently across
the cluster; an actual intervention additionally requires at least one member with a
finite before/after behavior delta. The full per-arm minimum must be actual
interventions. Refused and all-no-op clusters remain visible and stay in ITT, so they
dilute the policy effect, but they do not count toward the intervention minimum.
Lifecycle rechecks these counts so a legacy or malformed positive verdict cannot grant
a successor stage.

A validly activated bounded Paper stage does not lose its existing allocation merely
because its current `insufficient_data` verdict has an `indeterminate` harm state from
sample/continuity underpower. It may continue collecting the predeclared window.
Negative/invalid evidence, determinate harm, identity/config/refreeze failure, or a
non-underpower indeterminate reason still withdraws authority immediately; promotion
continues to require positive ITT, clear-harm, and sufficient actual-intervention evidence.

| Current evidence | Required lifecycle result |
|---|---|
| consumed fixed-look `positive` and all gates current | Promote one stage |
| monitoring `positive` | Hold; descriptive only |
| `neutral` or ordinary insufficient data | Hold |
| `negative` or determinate harm breach | Refreeze/fail to static baseline |
| `invalid` | Invalidate/fail to static baseline |
| stale epoch/horizon/contract identity | Cannot grant authority; safety withdrawal remains reachable |
| missing or malformed evidence | Hold/fail closed |

Any change to decision input, treatment, allocation semantics, statistical unit,
outcome economics, harm gate, or continuity rule is B-class: close the old cohort,
rotate identity/epoch as required, and collect a new window from zero.

## Gate E - Live readiness

Live is outside the active MVP proof and remains disabled by default. No score,
test, document, or merged PR enables it.

Before any owner-authorized live transition, all of the following must hold:

- the current [Go-Live Scorecard](../../product/gates/METRICS.md#5-go-live-scorecard) satisfies the
  exact threshold and blocker rules enforced by the live contract;
- eligible net realized PnL is positive and outcome integrity is current;
- reconciliation, credentials, connectivity, Risk, kill switch, position caps,
  and confirmation requirements pass;
- the owner explicitly authorizes the exact live mode and bounded capital/risk
  profile;
- the implementation and verification record for any trust-boundary change is in
  Git/PR/CI.

See `docs/design/contracts/live_unlock_contract.md` for the executable contract.

## Retired gates

The following are not current admission criteria:

- counts of legacy strategies or strategy lifecycle transitions;
- RuleSet/DSL/backtest promotion;
- a top-level `knowledge_items.item_type='risk_lesson'` query;
- the former internal-simulator proof profile or its adapter-visibility exclusions;
- lesson/artifact counts as evidence of positive Learning contribution;
- the June 2026 Round 1 calendar, tags, or MVP script.

Historical Round 1 material remains provenance only. It cannot authorize current
work or qualify a current Runtime.

---

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
`universe_family_calibration_cluster_allocation_utility_v10`.

## 2. Learning qualification metrics

| Metric | Meaning | Current source |
|---|---|---|
| Treatment/control assignment units | Complete, unique prospective clusters per arm | Learning cohort builder and `lco-v1` projection |
| First Paper trial integrity | `ufc-shadow-trial-integrity/v1`: 10 complete units per arm in one current eligible UTC market day; no contribution claim | Shadow verdict and protected owner admission |
| Minimum samples | 50 complete assignment units in each arm | `LearningExperiment.min_samples_per_arm` |
| Continuous market days | At least 7 consecutive UTC dates with a complete eligible exposure unit from either arm; 50-per-arm sufficiency is independent and scoped to that run | `LearningExperiment.min_market_days` and verdict `market_day_coverage` |
| Actual intervention | At least the per-arm minimum has consistent treatment permission and one or more members with a finite before/after behavior delta | Exposure manifest, before/after values, and cohort sample |
| Contribution delta | Cost-aware treatment mean minus control mean | `learning/verdicts.py` |
| Monitoring inference | Deterministic conditional randomization p-value for the randomized ITT delta; never promotion authority | `learning/verdicts.py` |
| Confirmatory decision | One prospective, outcome-blind frozen cutoff and one consumed conditional-randomization look at authority alpha `0.025` | `learning/fixed_look.py` and `learning_inference_decisions` |
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
Metric v9 and UFC behavior contract v10 separate the one-day/10-per-arm Shadow
integrity admission from those unchanged economic promotion gates. This starts a
fresh cohort; the integrity verdict remains `insufficient_data` and grants no
automatic authority or contribution claim.
Metric v10 and UFC behavior contract v11 retire the IID outcome bootstrap. Adjacent
10-minute scanner decisions may share most of a 1h/4h market path, so the current
confirmatory contract conditions on the prospectively randomized assignment count
and tests the sharp no-assignment-effect null without assuming independent outcomes.
The legacy confidence fields remain empty rather than relabelling an invalid interval.

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

---

# Former strategy-promotion computation rules

Archived from `docs/governance/policies/trust_and_change_control.md` at commit
`50dbaa772`. The following local-simulator and strategy-lifecycle gates no longer
admit current runtime, Learning or Live authority. Human-controlled Live unlock
and the current Risk/Execution contracts remain required.

## 5. Promotion Gate Computation Spec

Strategy promotion gates must be computed from stored records, not from strategy
state labels alone.

### 5.1 Gate Source Tables

Use these data sources:

- `strategy_records`: current strategy state and timestamps.
- `trade_proposals`: strategy-linked proposals via `strategy_id`.
- `risk_decisions`: risk decisions linked through proposals.
- `orders`: paper/demo/live order records linked through proposals.
- `fills`: fills linked through orders.
- `positions`: realized PnL and closed position history via `strategy_id`.
- `knowledge_items`: post-trade review existence via
  `item_type="post_trade_review"`.

If the current schema cannot compute a metric honestly, fail the gate with an
explicit `insufficient_*` reason. Do not pass by assumption.

### 5.2 Paper Runtime Days

Default gate:

```text
min_paper_runtime_days = 14
```

Computation:

- Consider only orders linked to the strategy through
  `orders.proposal_id -> trade_proposals.id -> strategy_id`.
- Consider only paper/demo execution modes: `paper_internal` and `bitget_demo`.
- Runtime starts at the earliest strategy-linked paper/demo order `created_at`.
- Runtime ends at current UTC time.
- Pass if elapsed full days is greater than or equal to 14.
- Fail with `insufficient_paper_runtime_days:<actual><14` if too short.
- Fail with `no_strategy_paper_orders` if no order exists.

### 5.3 Paper Trade Count

Default gate:

```text
min_paper_trades = 30
```

Computation:

- Count strategy-linked paper/demo filled orders, or fills if fill data is the
  more precise available source.
- Prefer fills when present.
- Pass if count is greater than or equal to 30.
- Fail with `insufficient_paper_trades:<actual><30`.

### 5.4 Positive Expectancy

Default gate:

```text
positive_expectancy_required = true
```

Computation:

- Use closed `positions` with matching `strategy_id`.
- Sum `positions.realized_pnl`.
- Pass if total realized PnL is greater than 0.
- Fail with `non_positive_expectancy:<pnl>` if total realized PnL is less than
  or equal to 0.
- Fail with `insufficient_closed_positions_for_pnl_checks` if no closed
  strategy-linked positions exist.

### 5.5 Max Drawdown

Default gate:

```text
max_drawdown_pct_allowed = 5.0
```

MVP computation:

- Use closed strategy-linked positions ordered by `closed_at`.
- Build an equity curve from cumulative `realized_pnl`.
- Resolve the Demo risk policy capital base through its configured source.
  The shipped and migrated default follows the matching paper/demo account
  equity; `custom` uses the explicitly stored `capital.base_usdt`.
- If account-equity mode has no fresh, positive authoritative snapshot, treat
  the capital base as unavailable and fail the dependent gate closed. Never
  silently fall back to the stored custom amount.
- Compute drawdown as peak-to-trough cumulative PnL decline divided by capital
  base.
- Pass if drawdown percentage is less than or equal to 5.0.
- Fail with `max_drawdown_exceeded:<actual>>5.0`.
- Fail with `insufficient_closed_positions_for_pnl_checks` if closed positions
  are absent.

The resolved base and source must remain auditable and covered by tests.

### 5.6 Max Consecutive Losses

Default gate:

```text
max_consecutive_losses_allowed = 5
```

Computation:

- Use closed strategy-linked positions ordered by `closed_at`.
- A loss is `realized_pnl < 0`.
- Count the longest run of consecutive losses.
- Pass if the longest run is less than or equal to 5.
- Fail with `max_consecutive_losses_exceeded:<actual>>5`.
- Fail with `insufficient_closed_positions_for_pnl_checks` if closed positions
  are absent.

### 5.7 Critical Risk Violations

Default gate:

```text
critical_risk_violations_allowed = 0
```

Computation:

- Read risk decisions linked to the strategy through proposals.
- Parse `risk_decisions.violations`.
- Any rejected live execution decision is critical.
- Any violation containing these tokens is critical:
  `live_disabled`, `kill_switch_active`, `account_mode_mismatch`,
  `exceeds_max_position_notional`, `exceeds_single_trade_risk`,
  `max_daily_loss_exceeded`, `stale_market_data`, `strategy_not_live_eligible`.
- Pass if critical violation count is 0.
- Fail with `critical_risk_violations:<actual>>0`.

### 5.8 Post-Trade Reviews

Default gate:

```text
post_trade_reviews_required = true
```

MVP computation:

- Require at least one active `knowledge_items` row with
  `item_type="post_trade_review"`.
- If the review is strategy-specific in metadata/tags, prefer matching the
  strategy ID.
- Until strategy-specific metadata is standardized, a global active
  `post_trade_review` satisfies the MVP gate.
- Fail with `post_trade_review_required` if no active review exists.

### 5.9 Live Candidate Promotion

Promotion from `paper_passed` to `live_candidate` passes only if:

- Current state is exactly `paper_passed`.
- All gates in this section pass.
- An audit event is written for the promotion.

Do not allow `paper_passed -> live_candidate` based only on state name.

### 5.10 Live Limited Promotion

Promotion from `live_candidate` to `live_limited` additionally requires:

- `live.state != "live_disabled"`.
- `live.first_live_confirmation_done == true`.
- Permission mode allows the promotion path.
- Risk Engine permits live-limited execution.
- Audit event records the actor and source.

First live unlock must remain human-controlled.

---
