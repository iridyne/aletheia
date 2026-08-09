# Alpha Attribution Audit

## Purpose

This is a read-only judging layer. It does not make the trading system smarter,
place trades, change risk, alter the ranker, or modify System Operator behavior.
It answers a narrower question: which layer appears to contribute or destroy
alpha in the currently recorded data?

Run:

```powershell
uv run aletheia opportunity audit --view alpha --hours 720
```

The older `aletheia alpha audit --days 30` command remains a deprecated
compatibility alias.

Optional API:

```text
GET /api/analytics/alpha-attribution?days=30
```

## Output Contract

The report always returns exactly five layer verdicts:

1. Universe Alpha
2. Ranker Alpha
3. System Operator Alpha
4. Learning Alpha
5. Serenity Alpha

Verdict values:

- `positive`
- `neutral`
- `negative`
- `insufficient_data`

`insufficient_data` is intentional. If sample size or baseline data is too thin,
the audit must say so instead of inventing a conclusion.

Each verdict includes:

- `sample_size`
- `baseline`
- `observed`
- `delta`
- `confidence_label`
- `evidence_type`
- `causal_grade`
- `contribution_verdict`
- `measurement_status`
- `suspected_modules`
- `reason`

The report also exposes four module-specific projections outside those five
verdicts:

- `universe_twin_evidence` consumes the existing randomized scanner-cluster
  Twin evaluator for one exact policy epoch and exposes fixed-4h outcome
  maturity, identity failures, cluster utility, and harm diagnostics.
- `exit_paired_evidence` projects one existing prospective paired Exit treatment
  in quote-currency net PnL.
- `risk_policy_evidence` estimates the authoritative local-Paper Risk policy's
  fixed-24h candidate value relative to a shadow pass-all policy.
- `execution_cost_evidence` pairs each eligible native local-Paper Position with
  immutable pre-fill entry/exit references to attribute actual fees and price
  impact.

None is a sixth judging-layer verdict and none grants behavior authority or
promotion.

These fields have different authority:

- `verdict` classifies the observed or directional result. A weak result may
  remain `negative` so the diagnostic signal is not lost.
- `contribution_verdict` is the evidence-authorized contribution judgment.
- `measurement_status` states whether the result is `measured_contribution`,
  `directional_evidence_only`, or `not_measured`.

`main_bottleneck` is authority-bearing and therefore has a stricter gate than an
individual verdict. A layer is eligible only when its
`contribution_verdict == negative` and
`measurement_status == measured_contribution`. If exactly one layer qualifies,
it may become actionable. If several qualify, the audit refuses to rank their
raw percentage deltas until estimand, horizon, statistical unit, and economic
unit are comparable. If none qualify, the report states that no actionable
gradient is currently established.

## Layer Definitions

### Universe Alpha

Question: do selected universe members outperform same-period tradable symbols?

Current implementation:

- Reads `universe_forward_outcomes`.
- Uses settled `1h`, `4h`, and `24h` outcomes.
- Converts returns directionally: short signals benefit when raw forward return is negative.
- Preferentially reads `alpha_control_observations`, which stores the selected
  members and same-refresh unselected candidates at Universe refresh time.
- Treats that prospective cohort as directional evidence only. The current rows
  do not freeze a policy epoch, behavior contract, primary horizon, or
  cohort-level inference contract, so they cannot grant contribution authority.
- Falls back to a deterministic reconstructed baseline from active universe registry
  symbols and available `market_ohlcv` symbols when prospective controls have not
  matured yet.

No random result changes between runs: fallback samples are selected by a stable hash.

### Universe Twin Evidence

Question: under one exact randomized scanner-run assignment, what fixed-4h net
allocation utility was observed for the frozen pessimistic Twin policy versus
the frozen authority control policy?

Current implementation:

- Selects the newest prospective Twin policy epoch represented inside the audit
  window, then invokes the existing read-only `evaluate_twin_shadow_epoch()` for
  that exact epoch.
- Uses complete scanner runs as assignment and statistical units. Assigned-policy
  selected candidates use canonical `filter_candidate_outcomes`; unselected
  candidates contribute zero to the full scanner-cluster denominator.
- Exposes treatment/control cluster counts, effective sample size, fixed-horizon
  completeness, outcome identity, cost, harm, and continuity blockers through
  the API, CLI, and deep Doctor projection.
- Evaluates only when the full frozen policy epoch fits inside the requested
  audit window and reports its exact start/end bounds. An epoch crossing the
  cutoff returns `not_measured`; it is never silently truncated or pooled.
- Always reports `authority=shadow_only`, `behavior_authority=false`,
  `promotion_eligible=false`, and `contribution_verdict=insufficient_data`.

This closes the evidence-reader gap without changing Universe selection. Even a
complete or negative Twin report is a prospective Shadow diagnostic, not a
measured Universe contribution verdict and never an actionable bottleneck.

### Ranker Alpha

Question: does ranker top1 outperform rank2-5 and the eligible shadow set?

Current implementation:

- Uses `shadow_trades` only for the prospective rank, batch, and policy-epoch
  assignment facts.
- Joins every candidate through its exact `outcome_candidate_key` to the
  canonical settled `filter_candidate_outcomes` row at the declared `24h`
  horizon.
- Requires matching venue, symbol, market type, direction, policy epoch, and
  cost-model version, and deduplicates repeated ticks to one candidate vote.
- Compares rank `1` candidates against ranks `2-5`; only batches containing both
  arms contribute.

The statistical unit is the candidate key, the assignment unit is the batch,
and the economic unit is cost-aware `net_return_pct`. This removes the previous
variable-hold Position/shadow mismatch. It remains directional evidence because
rank assignment is not randomized and the audit does not infer clustered
uncertainty.

### System Operator Alpha

Question: do candidates passed by the System Operator outperform candidates it
rejected under one behavior contract?

Current implementation:

- Reads the authoritative `pi_decisions` action and its append-only
  `filter_decisions` trace.
- Requires durable runtime-session provenance, matching producer commit and
  behavior fingerprint, and uses first exposure only.
- Joins the exact `pi_decision:<id>` outcome key to the canonical settled
  `filter_candidate_outcomes` row at `24h`.
- Requires matching venue, symbol, market type, direction, behavior fingerprint,
  producer commit, and cost-model version.
- Compares candidate-level net returns for consistent `trade` decisions versus
  consistent `skip` / `portfolio_guard_skip` decisions. Trace/action conflicts
  are excluded rather than forced into either arm.

This no longer mixes actual Position lifecycle, Risk, Execution, and Exit effects
with a fixed-horizon Shadow baseline. It is still directional because Operator
assignment is not randomized and the passed/rejected cohorts are selected by the
policy itself; it cannot by itself prescribe a prompt or model change.

### Learning Alpha

Question: does an exact supported Learning treatment improve its declared
cost-aware outcome, and what do weaker learning/memo/guard diagnostics suggest?

Current implementation:

- Preferentially consumes exactly one current, already-consumed fixed-look
  confirmatory verdict for a measurement-supported Learning path.
- Requires the durable inference decision, authority eligibility, exact
  experiment/hypothesis/stage/policy epoch/horizon/outcome/metric/code/behavior
  identities, arm sample contract, and complete baseline/observed/delta.
- Never averages multiple Learning estimands. Missing, stale, incompatible, or
  conflicting authority evidence fails closed.
- Reads accepted/applied `ps_lessons`.
- Also reads structured `knowledge_items` for `trading_memo`, `trading_guard`,
  but not `guard_candidate` because candidates are not active behavior yet.
- Extracts group keys when evidence/tags include:
  `scanner_family`, `direction`, `symbol`, `failure_bucket` or `close_reason`.
- Uses difference-in-differences when non-matching contemporaneous closed trades
  provide a control group.
- Falls back to same-pattern before/after only when controls are too thin.

Only the exact consumed confirmatory path can produce
`strong_prospective_control` and measured contribution. Generic lesson/memo/guard
fallbacks remain `moderate_quasi_causal` or `weak_before_after`; disabling active
safety guards for random trades would be unsafe.

### Exit Paired Evidence

Question: for the frozen `entry-lock-after-1.5pct/v1` treatment, how would the
same eligible Position's net quote-currency PnL have changed?

Current implementation:

- Reads immutable prospective assignments and paired outcomes from the existing
  Exit breakeven shadow experiment.
- Uses the same Position as the statistical unit and reports actual, shadow, and
  incremental net PnL under the frozen cost model.
- Includes only rows closed within the audit window; missing exact one-minute
  path, ordering, identity, or funding remains unavailable.
- Always reports `behavior_authority=false`, `promotion_eligible=false`, and
  `contribution_verdict=insufficient_data`.

This is strong treatment-specific paired evidence, not a generic Exit layer
estimand. The durable outcome does not carry `close_reason`, so the Audit also
states that stop-loss-specific attribution is unavailable.

### Risk Policy Evidence

Question: on the exact native local-Paper proposals that reached authoritative
Risk, how much 24h cost-aware candidate return did the current Risk policy add
or remove relative to passing every candidate?

Current implementation:

- Uses persisted authoritative `RiskDecision` as the treatment assignment and
  requires exact Proposal commit/quantity, Demo policy identity, and matching
  Proposal-to-Risk runtime/build/behavior provenance plus its append-only Risk
  filter trace.
- Joins only the exact `proposal:<id>` canonical settled `24h`
  `filter_candidate_outcomes` result and verifies instrument, entry, horizon,
  settlement, cost-model identity, and net-return arithmetic.
- Defines pass-all control value as each candidate's cost-aware net return.
  Approved candidates have the same treatment value; rejected candidates are
  actual Risk interventions whose treatment value is zero exposure.
- Requires one policy/build/behavior/cost identity, enough candidates, and enough
  actual rejections. Missing or mixed evidence fails closed.

An audit window may span clean Gateway restarts. Runtime session is therefore
validated on every Proposal-to-Risk lineage but is not a cohort-wide identity;
policy, build, behavior, and cost model remain cohort-wide identities.

The statistical unit is `proposal_id`; the economic unit is percentage points of
candidate `net_return_pct`. This is a paired value estimate for the Risk gate,
not realized Position PnL or a full portfolio simulation. It does not model
capacity/interference, and `behavior_authority=false` plus
`promotion_eligible=false` remain explicit.

### Execution Cost Evidence

Question: for an exact native local-Paper Position, how much net return did its
actual fills and fees add or remove relative to frictionless fills at the prices
known immediately before execution?

Current implementation:

- Persists the Risk-verified Proposal price on each new Paper entry Order and the
  exact fresh mark on each Paper close Order before adverse slippage is applied.
  Schema v15 makes those reference fields immutable; historical rows stay NULL.
- Reads only central `eligible_strategy_outcomes` and requires the complete
  Proposal→authoritative RiskDecision→entry Order/Fill→Position→close Order/Fill
  chain, exact quantity/instrument/provenance, matching Position fees and complete
  funding economics.
- Holds quantity, direction, entry/exit timing, and funding fixed. The control
  uses the requested entry/exit prices with zero execution fees; the treatment
  uses the actual local fills and fees.
- Reports mean net-return delta plus quote-currency fee, price-impact, and total
  incremental PnL. Missing references, mixed identities, partial/ambiguous chains,
  or insufficient samples fail closed.

The statistical unit is `position_id`. This is prospective paired accounting for
Execution cost, not a randomized routing-policy experiment. It measures fees and
fill-price impact exactly within the Paper simulator, but does not measure whether
the current router avoided worse fills or outperformed an alternative algorithm.
It remains read-only with `behavior_authority=false` and
`promotion_eligible=false`.

### Serenity Alpha

Question: do Serenity opportunities outperform observed same-period baselines?

Current implementation:

- Reads `serenity_opportunities`.
- Preferentially reads `alpha_control_observations`, which stores the Serenity
  opportunity and contemporaneous observable controls when the opportunity is inserted.
- Keeps that prospective comparison directional for the same missing authority
  identities; logging controls before outcomes are known is not by itself a
  causal contribution contract.
- Falls back to recomputed `1d`, `3d`, and `7d` forward returns from `market_ohlcv`.
- Equity-like symbols prefer other equity-like baseline symbols when available.

Serenity will often return `insufficient_data` until enough daily OHLCV exists.

## Boundaries

This audit is read-only:

- No DB writes.
- No order submission.
- No risk engine calls.
- No ranker changes.
- No System Operator prompt or model changes.
- No live/execution/credential changes.

## Causal Evidence Grades

- `strong_prospective_control`: a prospective treatment/control comparison also
  has the declared policy, behavior, horizon, statistical-unit, outcome, cost,
  and integrity identities required by its contribution contract. The current
  `alpha_control_observations` producer does not meet this grade.
- `strong_prospective_paired_shadow`: one Position has a prospective frozen
  treatment and an identity- and cost-complete paired shadow outcome. This grade
  is treatment-specific and does not by itself grant module contribution
  authority.
- `prospective_cluster_shadow_only`: complete scanner runs were assigned before
  their fixed-horizon outcomes and evaluated under one frozen policy epoch, but
  no behavior authority or actual intervention was granted. This grade remains
  non-actionable.
- `strong_prospective_paired_policy`: every candidate has an ex-ante
  authoritative policy action and exact fixed-horizon control outcome; actual
  reject interventions produce paired policy-value deltas. This measures the
  declared candidate-level policy estimand, not portfolio-wide profitability.
- `strong_prospective_paired_accounting`: immutable pre-fill references and the
  exact verified execution/outcome chain permit same-Position cost decomposition.
  This is accounting authority for the declared cost estimand, not evidence that
  one routing policy causally beats another.
- `moderate_quasi_causal`: difference-in-differences against contemporaneous
  controls is available, but there was no randomized holdout.
- `weak_reconstructed`: the baseline was reconstructed after the fact from OHLCV.
- `weak_before_after`: only pre/post treated behavior is available.
- `insufficient`: samples or matured outcomes are too thin.

## Known Limits

- Historical Universe and Serenity observations before this feature still fall back
  to reconstructed OHLCV baselines.
- Universe Twin evidence is evaluated by exact policy epoch and remains
  Shadow-only; collecting more clusters cannot by itself create behavior
  authority or a measured Universe-layer verdict.
- Generic Learning Alpha depends on structured evidence/tags. Unstructured
  narrative-only lessons cannot be judged, and unsupported paths remain
  directional even when they are consumer-wired.
- Exit paired evidence covers one frozen treatment and does not yet attribute by
  actual `close_reason`.
- Risk policy evidence uses a pass-all candidate control and does not model
  portfolio capacity, simultaneous exposures, or downstream Execution/Exit.
- Execution cost evidence is limited to newly opened and closed native
  `paper_internal` Positions under schema v16. Exchange/demo/live rows, legacy
  rows, partial closes, and routing-policy value remain unmeasured.
- Directional verdicts retain suspected-module metadata for diagnosis, but the
  CLI does not render that metadata as confirmed or prescriptive advice.
- A negative Ranker or System Operator observational delta does not prove either
  module is the causal bottleneck.
