# Frontend Latency Top Offenders Fix - 2026-07-03

## Scope

Branch: `fix/frontend-latency-top-offenders`

This fix only touches System Operator recommendation synthesis:

- optimized `/api/operator/recommendations` request-internal artifact reads
- added a focused regression test for duplicate artifact scans
- did not change recommendation kinds, recommendation payload semantics, UI
  behavior, trading, risk evaluation, execution gateway, live-unlock, order
  placement, or credential handling

## Root Cause

The runtime audit found `/api/operator/recommendations` was the slowest measured
dashboard API:

- avg: 492.0 ms
- p50: 476.4 ms
- p90: 545.5 ms
- payload: only 2,341 bytes

Source inspection and a failing regression test showed a single request called
`list_artifacts(limit=500)` 6 times. Those scans were used to derive multiple
recommendation categories from the same recent artifact window.

## Change

`build_recommendations()` now fetches `recent_artifacts` once and reuses it for:

- strategy evaluation recommendations
- prompt-change proposal suppression
- schedule-change proposal suppression
- risk-boundary proposal suppression through `build_risk_summary(...)`
- command-center behavior proposal suppression through
  `build_command_center_behavior_summary(...)`

`build_risk_summary()` and `build_command_center_behavior_summary()` keep their
old behavior for external callers: if no artifact list is supplied, they still
fetch their own `list_artifacts(limit=500)` data.

## Quantified Outcome

- Duplicate unscoped artifact scans per `/api/operator/recommendations` request:
  6 -> 1
- Reduction in that scan class: 5 fewer scans, 83.3% fewer duplicate artifact
  reads per recommendation request
- Regression result before fix:
  `test_operator_recommendations_reuse_recent_artifact_scan` failed with
  `assert 6 == 1`
- Regression result after fix:
  `test_operator_recommendations_reuse_recent_artifact_scan` passed

The live `:8787` gateway was not restarted during this branch, so the HTTP p50
and p90 values from the audit remain the pre-fix runtime baseline. Deployment or
integration restart should re-run the same 10-sample HTTP measurement and compare
against the 476.4 ms p50 / 545.5 ms p90 baseline.

## Verification

- `uv run pytest tests/test_operator_events.py::test_operator_recommendations_reuse_recent_artifact_scan -q`
  - `1 passed`
- `uv run pytest tests/test_operator_events.py -q`
  - `32 passed`
- `uv run pytest tests/test_risk_routes.py -q`
  - `9 passed`
