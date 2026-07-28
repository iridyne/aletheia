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
- `suspected_modules`
- `reason`

The final `main_bottleneck` chooses the most negative measured layer. If no layer
has enough data, it tells the operator to collect more settled outcomes before
changing scanner, ranker, prompt, or learning logic.

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

This is the cleanest current alpha read because shadow batches already preserve
the ranker counterfactual set.

### System Operator Alpha

Question: do accepted/executed System Operator trades outperform same-cycle
shadow candidates?

Current implementation:

- Reads executed `trade_proposals`.
- Joins closed `positions`.
- Finds the matching shadow batch by `opportunity_id`.
- Compares actual closed position PnL against unselected resolved shadows in the
  same batch.

If this verdict is negative, the likely bottleneck is model, prompt, context, or
decision logic rather than scanner selection.

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
- Verdicts are meant to locate the likely bottleneck and suspected modules, not to
  replace full strategy research.
