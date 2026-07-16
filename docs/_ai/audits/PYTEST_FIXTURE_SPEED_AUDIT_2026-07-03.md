# Pytest / Fixture Speed Audit - 2026-07-03

## Scope

Branch: `audit/test-fixture-speed`

This is a measurement-only audit for D13. It does not change production code,
test fixtures, pytest settings, or test selection defaults.

## Collection Baseline

Full test collection:

- collected tests: 1,707
- collection time: 4.85s
- warning classes observed during collection: 1 Starlette/httpx deprecation warning

## Representative Suite Timing

The worktree virtual environment was warmed first with `uv run python -c
"print('test-env-ready')"`, so the first dependency install/build time is not
counted below.

| Suite | Wall seconds | Pytest summary |
|---|---:|---|
| operator/risk | 20.06 | 185 passed in 18.19s |
| learning/universe | 4.88 | 87 passed, 1 skipped, 1 xfailed in 4.12s |
| serenity | 26.82 | 150 passed, 1 warning in 25.74s |
| openapi | 2.60 | 1 passed in 1.71s |

Representative total:

- tests run: 423
- failed: 0
- skipped/xfailed: 1 skipped, 1 xfailed
- wall-clock total across measured suites: 54.36s

## Slowest Calls

Top slow calls from `--durations=20`:

- 6.73s `tests/test_serenity_workbench.py::test_research_worker_success`
- 3.22s `tests/test_serenity_workbench.py::test_research_worker_rate_limit`
- 3.02s `tests/specs/spec_serenity_verdict.py::test_pipeline_persists_verdict`
- 2.94s `tests/test_serenity_workbench.py::test_pipeline_full_stages`
- 2.74s `tests/test_serenity_workbench.py::test_research_worker_generic_failure`
- 2.67s `tests/test_operator_routes.py::test_operator_public_branch_create_generates_child_session_and_selects_it`
- 2.35s `tests/test_agent_shell.py::test_runtime_includes_recent_session_context_and_trace_in_branch_summary`

## Findings

1. Test health is acceptable for this MVP-hardening phase. The measured suite
   has 0 failures across 423 representative tests.

2. The slowest tests are high-value integration paths, mainly Serenity research
   worker and verdict persistence. They exercise realistic multi-stage behavior
   and should not be replaced with pure source-string checks.

3. Fixture setup is not the dominant global bottleneck. Full collection is under
   5 seconds, and most non-worker tests complete under 1 second.

4. The best future optimization target is a narrow Serenity worker fixture that
   shortcuts artificial waits/provider retries while preserving persisted run
   events and artifacts. This should be a separate test-only branch if pursued.

## Quantified Outcome

- Audited 1,707-test collection.
- Timed 423 representative tests.
- Identified 7 slowest actionable tests, 5 of which are Serenity worker/verdict
  paths.
- Recommended production code changes: 0.
- Recommended immediate fixture rewrites: 0.

## Verification Commands

- `uv run pytest --collect-only -q`
- representative suite timing:
  - `uv run pytest tests/test_agent_shell.py tests/test_operator_loop.py tests/test_operator_events.py tests/test_operator_routes.py tests/test_risk_routes.py -q`
  - `uv run pytest tests/test_anti_hallucination_e2e.py tests/specs/spec_learning_pipeline.py::test_pi_prompt_learning_paths_complete_enumeration tests/specs/spec_learning_pipeline.py::test_trading_decision_reads_lessons_from_lesson_store tests/specs/spec_pi_decision_chain.py tests/specs/spec_universe_distribution_audit.py tests/specs/spec_universe_pipeline.py tests/specs/spec_market_type_dedup.py tests/test_universe_signal_emitter.py -q`
  - `uv run pytest tests/test_serenity_workbench.py tests/test_serenity_routes.py tests/test_serenity_scoring.py tests/test_serenity_p0_p3_e2e.py tests/specs/spec_serenity_promote.py tests/specs/spec_serenity_evidence.py tests/specs/spec_serenity_multi_lens.py tests/specs/spec_serenity_researchability.py tests/specs/spec_serenity_verdict.py tests/specs/spec_serenity_attribution.py tests/specs/spec_serenity_live_readiness.py tests/specs/spec_serenity_xstock_execution.py -q`
  - `uv run pytest tests/test_openapi_export.py -q`
