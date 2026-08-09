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

Question: after learning/memo/guard events, do same-pattern losses improve?

Current implementation:

- Reads accepted/applied `ps_lessons`.
- Also reads structured `knowledge_items` for `trading_memo`, `trading_guard`,
  but not `guard_candidate` because candidates are not active behavior yet.
- Extracts group keys when evidence/tags include:
  `scanner_family`, `direction`, `symbol`, `failure_bucket` or `close_reason`.
- Uses difference-in-differences when non-matching contemporaneous closed trades
  provide a control group.
- Falls back to same-pattern before/after only when controls are too thin.

This is still weaker than a randomized learning holdout, because disabling active
guards for random trades would be unsafe. The report labels this as
`moderate_quasi_causal` when controls exist and `weak_before_after` otherwise.

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
- `moderate_quasi_causal`: difference-in-differences against contemporaneous
  controls is available, but there was no randomized holdout.
- `weak_reconstructed`: the baseline was reconstructed after the fact from OHLCV.
- `weak_before_after`: only pre/post treated behavior is available.
- `insufficient`: samples or matured outcomes are too thin.

## Known Limits

- Historical Universe and Serenity observations before this feature still fall back
  to reconstructed OHLCV baselines.
- Learning Alpha depends on structured evidence/tags. Unstructured narrative-only
  lessons cannot be judged by this v1 audit.
- Learning causality is quasi-causal unless the project explicitly introduces
  randomized learning holdouts, which is not appropriate for active safety guards.
- Directional verdicts retain suspected-module metadata for diagnosis, but the
  CLI does not render that metadata as confirmed or prescriptive advice.
- A negative Ranker or System Operator observational delta does not prove either
  module is the causal bottleneck.
