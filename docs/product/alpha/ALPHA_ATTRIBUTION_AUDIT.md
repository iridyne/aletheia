# Alpha Attribution Audit

## Purpose

The [pre-retirement definition](../../design/history/local_simulated_alpha_audit.md)
is archived. Historical simulator evidence retains its original identity and
cannot qualify current external exchange execution.

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
- `universe_relative_rank_evidence` reads one exact relative-rank shadow policy
  epoch whose complete window fits the requested audit window.
- `exit_paired_evidence` reads archived prospective Exit assignments and paired
  outcomes. It has no current assignment or settlement producer.
- `exit_trailing_qualification` reads archived dynamic-trailing assignments and
  outcomes, with enrollment disabled and no promotion authority.

The former `risk_policy_evidence` and `execution_cost_evidence` fields and their
local-simulator producers are removed. Their definitions are retained only in
the [archived qualification contract](../../design/history/local_simulated_qualification.md).

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
  cost-model version. Repeated ticks use only the first rank observation aligned
  within 60 seconds of the canonical outcome entry; later re-ranks cannot inherit
  an earlier return window.
- Compares rank `1` candidates against ranks `2-5`; only batches containing both
  arms with entry times inside the same 60-second cohort contribute. Each batch
  gets equal weight rather than giving larger rank2-5 sets more votes.

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

The current projection reads persisted Lab measurements through
`build_learning_contribution_projection`. It exposes experiment identity,
availability, freshness, samples, inference, integrity and audit-window
compatibility. It returns `insufficient_data` with
`evidence_type=canonical_lab_measurements`; persisted or historical measurements
do not become current consumed qualification proof. The former local-Paper
confirmatory and lesson before/after definitions are archived.

### Exit Paired Evidence

Question: for the frozen `entry-lock-after-1.5pct/v1` treatment, how would the
same eligible Position's net quote-currency PnL have changed?

Archived evidence reader:

- Reads immutable prospective assignments and paired outcomes from the existing
  retired Exit breakeven shadow experiment; it creates no new records.
- Uses the same Position as the statistical unit and reports actual, shadow, and
  incremental net PnL under the frozen cost model.
- Reports available-outcome coverage and a deterministic position-level
  percentile-bootstrap 95% interval for mean incremental net PnL when at least
  two outcomes are available. Eligible closed assignments without a settled
  outcome and unavailable outcomes stay in the coverage denominator and are
  never imputed as zero. Bootstrap work is capped independently of cohort size.
- Includes only rows closed within the audit window; missing exact one-minute
  path, ordering, identity, or funding remains unavailable.
- Joins the immutable paired result back to its Position solely to expose a
  descriptive `close_reason` breakdown in the API and CLI.
- Always reports `behavior_authority=false`, `promotion_eligible=false`, and
  `contribution_verdict=insufficient_data`.

This is strong treatment-specific paired evidence, not a generic Exit layer
estimand. The close-reason breakdown is post-outcome diagnostic context; it
cannot authorize applying the treatment selectively by a future close reason.
Coverage and uncertainty remain descriptive and do not create behavior or
promotion authority.

### Retired Risk and Execution projections

The local Risk-policy and execution-cost projections are no longer part of this
report. Their frozen definitions remain in the
[historical Risk and execution contracts](../../design/history/local_simulated_risk_execution.md).
No external-account contribution or routing-quality claim is inferred from their
old simulator results.

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
- Learning Alpha exposes persisted Lab evidence and remains insufficient for
  current measured contribution; historical qualification does not promote it.
- Exit paired evidence covers one frozen treatment; its actual `close_reason`
  breakdown is descriptive and cannot define an ex-ante treatment policy.
- Exit paired and trailing projections are archival readers. New external
  Positions are not enrolled into the retired simulator experiments.
- Local Risk-policy and execution-cost projections are removed. Their historical
  results do not establish current external exchange qualification.
- Directional verdicts retain suspected-module metadata for diagnosis, but the
  CLI does not render that metadata as confirmed or prescriptive advice.
- A negative Ranker or System Operator observational delta does not prove either
  module is the causal bottleneck.
