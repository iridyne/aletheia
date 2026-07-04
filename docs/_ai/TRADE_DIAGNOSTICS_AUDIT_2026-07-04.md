# Trade Diagnostics Audit - 2026-07-04

## What Was Added

- Added a read-only Trade Funnel Audit that shows where opportunities stop before becoming positions.
- Added a read-only Sizing Trace Audit that explains why recent proposal/position sizes are small.
- Added compact Alpha/Funnel/Sizing summaries to `aletheia doctor --deep` and `aletheia doctor --json --deep`.
- Added standalone commands for detailed reports:
  - `uv run aletheia funnel audit --hours 24`
  - `uv run aletheia funnel audit --days 7 --json`
  - `uv run aletheia sizing trace --hours 24`
  - `uv run aletheia sizing trace --days 7 --json`

## Why Doctor Is The Entry Point

The owner needs one command that answers "where should I look first?" Doctor is already the main
machine diagnostic entry point, so deep doctor now includes a compact trading diagnostic block.

The audit logic remains separate in analytics modules:

- `aletheia/analytics/alpha_attribution.py`
- `aletheia/analytics/trade_funnel.py`
- `aletheia/analytics/sizing_trace.py`

This keeps doctor as an orchestrator, not a trading-analysis implementation file.

## Read-Only Guarantees

These audits only read local SQLite state and current config.

They do not:

- submit orders
- create proposals
- update opportunities or `pi_decisions`
- modify positions
- change risk, trading, Kelly, leverage, persona, or prompt config
- call LLMs
- call exchange APIs
- trigger agents or scheduler jobs
- auto-fix trading behavior from doctor

`doctor --fix` remains infrastructure-only and does not use audit output to change trading behavior.

## How To Interpret The Three Audits

Alpha Attribution Audit answers: which layer appears to add or destroy alpha?

Trade Funnel Audit answers: where does flow stop between opportunities, pending signals, decisions,
proposals, risk, execution, and positions?

Sizing Trace Audit answers: why are recent intended or executed sizes small?

Use them in this order:

1. Run `uv run aletheia doctor --deep` to find the likely area.
2. If funnel is the issue, run `uv run aletheia funnel audit --hours 24` or `--days 7`.
3. If small sizing is the issue, run `uv run aletheia sizing trace --hours 24` or `--days 7`.

## Known Limitations

- Ranker counts are a current snapshot from `build_paper_candidates()`, not a historical reconstruction.
- Sizing reconstruction uses current config because historical config snapshots are not stored.
- The sizing trace therefore marks trace notes with `reconstructed_from_current_config`.
- Missing tables or too few samples return `insufficient_data` instead of forcing a conclusion.
- Trading diagnostics are warnings/information only; bad performance is not an infrastructure blocker.
