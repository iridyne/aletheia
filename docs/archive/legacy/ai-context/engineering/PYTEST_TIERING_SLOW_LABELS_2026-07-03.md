# Pytest Tiering And Slow Labels - 2026-07-03

## Boundary

Touched pytest configuration, test collection metadata, slow-test analysis
tooling, and documentation only.

Did not change production trading, risk, execution gateway, live unlock,
credential handling, model behavior, or runtime business logic.

## Implemented

1. Layered pytest markers:
   - `tier_unit`
   - `tier_integration`
   - `tier_e2e`
   - `tier_spec`

2. Domain pytest markers:
   - `domain_operator`
   - `domain_universe`
   - `domain_serenity`
   - `domain_risk`
   - `domain_execution`
   - `domain_market_data`
   - `domain_learning`
   - `domain_frontend`
   - `domain_runtime`
   - `domain_storage`
   - `domain_security`
   - `domain_config`
   - `domain_misc`

3. Slow-task binary labels:
   - `slow_optimizable`: slow pytest `call` phase.
   - `slow_structural`: slow pytest `setup` or `teardown` phase.

4. Failed-only workflow:
   - Removed `-p no:cacheprovider` so `pytest --lf` and `pytest --ff` work.
   - `.pytest_cache/` is already ignored by git.

5. Slow classifier tool:
   - `scripts/pytest_slow_classifier.py`
   - Reads pytest `--durations` output from stdin or a file.
   - Emits a table or JSON summary.

## Quantified Collection Result

Command:

```powershell
uv run pytest --collect-only -q
```

Result:

- `2274 tests collected in 1.82s`

Layer coverage:

| Layer | Count |
|---|---:|
| `tier_unit` | 1230 |
| `tier_integration` | 416 |
| `tier_e2e` | 63 |
| `tier_spec` | 565 |

The four layer counts sum to 2274, so layer coverage is 100%.

## Quantified Runtime Result

Full suite with `spec_*.py` collected:

- Wall clock: 200.89s
- Pytest time: 164.98s
- Passed: 2246
- Failed: 10
- Skipped: 10
- Xfailed: 7
- Failure rate by collected tests: 10 / 2274 = 0.44%

Fast layers:

| Command | Result | Pytest time |
|---|---|---:|
| `uv run pytest -m tier_unit -q` | 1228 passed, 1 skipped, 1044 deselected | 59.88s |
| `uv run pytest -m tier_e2e -q` | 63 passed, 2210 deselected | 5.73s |
| `uv run pytest -m "tier_integration and not domain_learning" -q` | 409 passed, 2 skipped, 1862 deselected | 45.09s |
| `uv run pytest -m tier_spec -q` | 538 passed, 12 failed, 3 skipped, 7 xfailed | 21.65s |

The new `tier_spec` layer intentionally exposes SDD red lights that were only
visible when spec files were explicitly named before this branch.

## Slow-Task Binary Classification

Full-suite `--durations=20` parsed with:

```powershell
uv run python scripts/pytest_slow_classifier.py --threshold 1.0
```

Result:

| Slow tag | Count | Total seconds | Max seconds |
|---|---:|---:|---:|
| `slow_optimizable` | 20 | 85.20 | 20.98 |
| `slow_structural` | 0 | 0.00 | 0.00 |

Interpretation: the current slowest tasks are all pytest `call` phase work. The
next optimization pass should inspect test body/provider/fixture behavior, not
setup or teardown.

## Accuracy Checks

- `tests/test_pytest_tiering.py` sample tier classifier accuracy: 6 / 6.
- Domain classifier sample coverage: 10 / 10.
- Slow duration parser and binary label sample: 3 / 3.
- Regression command: `uv run pytest tests/test_pytest_tiering.py -q`
  - `5 passed`

## Current Red Layer

The full run is not fully green. Current visible failures:

- `tier_spec`: 12 failures, mostly SDD/spec drift or spec fixture assumptions.
- full suite: 10 failures after collection ordering/overlap, including spec
  failures plus the known Learning penalty failures.

These were not hidden by this branch. The point of this branch is to make them
easy to isolate by layer and domain.
