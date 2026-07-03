# Pytest Spec Redlights Closure - 2026-07-03

Branch: `fix/pytest-spec-redlights`

Scope:
- Close pytest/spec red lights exposed by default `spec_*.py` collection.
- Keep trading execution, risk-engine semantics, live unlock, and credential handling unchanged.

## Results

Baseline from the pytest tiering branch:
- `tier_spec`: 8 failed, 543 passed, 7 skipped, 7 xfailed in 36.25s.
- Full suite: 10 failed, 2246 passed, 10 skipped, 7 xfailed in 164.98s.

After this branch:
- `tier_spec`: 0 failed, 550 passed, 8 skipped, 7 xfailed in 23.66s.
- Full suite: 0 failed, 2256 passed, 11 skipped, 7 xfailed in 138.30s.
- Hard pytest failure rate: 0.44% -> 0.00% for full suite, 1.41% -> 0.00% for `tier_spec`.
- `tier_spec` pytest runtime: 36.25s -> 23.66s, a 34.7% reduction.
- Full-suite pytest runtime: 164.98s -> 138.30s, a 16.2% reduction.

## Fix Classes

1. Test/mock drift:
   - Updated gateway-stability LLM mock to match `get_provider(provider_name=None)`.
   - Patched tunnel-auth spec at the actual middleware import boundary.
   - Reset config cache through `loader._invalidate_config_cache()`.
   - Updated learning-loop mocks to use structured `Lesson.outcome="loss"`.

2. Over-narrow structural specs:
   - Allowed `_XSTOCK_BASE_TO_TICKER` to be built by dict comprehension from the stock-token registry.
   - Marked superseded `FL-S17` strategy-affinity ranker behavior as skipped after Position C removed StrategyRecord gating.

3. Test isolation:
   - Production-DB tripwires now skip when the local `data/app.db` exists but lacks the required tables.
   - Drawdown spec now writes `snapshot_at`, the real ordering column, instead of an ignored `created_at` field.

4. Observation-only behavior:
   - `equity_observer` now iterates configured stock-token candidates in priority order and only uses the first token with a successful price.
   - Fixed stock-token price helper to read the `Ticker.last_price` dataclass field.
   - xStock observer integration test now clears both legacy and current stock-token config when cross-validation is intentionally disabled.

## Slow Test Classification

Latest `tier_spec --durations=20` top slow items are call-heavy, not setup/teardown-heavy:
- `slow_optimizable`: 18 call-phase entries, max 6.37s.
- `slow_structural`: 2 setup/teardown-phase entries, max 0.84s.

The main remaining optimization candidates are `spec_serenity_verdict.py::test_pipeline_persists_verdict` and doctor/workbench integration tests in the full suite.
