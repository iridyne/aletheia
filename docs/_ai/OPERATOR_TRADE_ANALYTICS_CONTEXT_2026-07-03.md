# Operator Trade Analytics Context 2026-07-03

来源: `discussion.md` D09 — System Operator should consume Learning plus Trades/Positions analytics.

边界: 只把现有 closed-position analytics 摘要注入 System Operator context/prompt。未改风险、
执行、live、凭证、下单阈值或 proposal 提交流程。

## Result

Before:

- System Operator prompt had 7d `outcome_stats`, `per_symbol`, strategy KPIs, trading memo,
  reasoning feedback, and calibration feedback.
- It did not directly render the dashboard-style `build_trade_stats()` analytics:
  `by_source`, `by_family`, `hold_distribution`, and totals.

After:

- `trading_context.layer2.trade_analytics` now reads compact data from
  `analytics.trade_stats.build_trade_stats()`.
- `trading_decision._build_assessment_prompt()` now renders a compact
  `Trades Analytics (closed positions)` block.

Prompt fields added:

| Field group | Source | Prompt line |
|---|---|---|
| totals | `trade_stats.totals` | `total: ... trades, WR, PnL, avg_hold, median_hold` |
| by source | `trade_stats.by_source` | `source opportunity/ruleset: ...` |
| by family | `trade_stats.by_family` | `family momentum/...: ...` |
| hold time | `trade_stats.hold_distribution` | `hold 1-4h=...` |

Quantified change:

| Metric | Before | After |
|---|---:|---:|
| direct Trades Analytics prompt block | 0 | 1 |
| analytics field groups rendered | 0 | 4 |
| focused tests for this path | 0 | 2 |

## Verification

Narrow RED->GREEN:

```powershell
uv run pytest tests/test_anti_hallucination_e2e.py::test_layer2_trade_analytics_collected tests/test_anti_hallucination_e2e.py::test_trade_analytics_rendered_into_operator_prompt -q
```

Result: `2 passed in 0.67s`.

Broader context/prompt:

```powershell
uv run pytest tests/test_anti_hallucination_e2e.py tests/specs/spec_learning_pipeline.py::test_pi_prompt_learning_paths_complete_enumeration tests/specs/spec_pi_decision_chain.py -q
```

Result: `19 passed, 1 skipped, 1 xfailed in 1.54s`.

Analytics/feedback:

```powershell
uv run pytest tests/specs/spec_analytics_contracts.py tests/e2e/test_pipeline_feedback_loop.py -q
```

Result: `6 passed, 1 skipped in 0.67s`.
