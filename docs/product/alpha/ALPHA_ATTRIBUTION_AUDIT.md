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
- Falls back to a deterministic reconstructed baseline from active universe registry
  symbols and available `market_ohlcv` symbols when prospective controls have not
  matured yet.

No random result changes between runs: fallback samples are selected by a stable hash.

### Ranker Alpha

Question: does ranker top1 outperform rank2-5 and the eligible shadow set?

Current implementation:

- Reads resolved `shadow_trades`.
- Groups by `batch_id`.
- Compares rank `1` PnL against ranks `2-5`.

This is directional evidence. Shadow batches preserve the candidate set, but do
not constitute a prospective Ranker treatment/control experiment.

### System Operator Alpha

Question: do accepted/executed System Operator trades outperform same-cycle
shadow candidates?

Current implementation:

- Reads executed `trade_proposals`.
- Joins closed `positions`.
- Finds the matching shadow batch by `opportunity_id`.
- Compares actual closed position PnL against unselected resolved shadows in the
  same batch.

This comparison mixes downstream queue, Risk, Execution, and Exit effects, so it
is directional and cannot by itself prescribe a prompt or model change.

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

- `strong_prospective_control`: selected and control observations were logged at
  decision time, before outcomes were known.
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
