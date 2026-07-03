# SDD Global Alignment Audit - 2026-07-03

## Scope

This audit follows the owner instruction to first align Learning SDD docs, then
expand the same method to the whole project.

This file records documentation alignment work only. It does not describe local
runtime truth. Use `aletheia doctor` for runtime state.

## Completed In Learning Scope

- Rewrote `docs/design/learning_contract.md` into a clean current-state contract.
- Updated `knowledge_contract.md` for applied `ps_lessons`, draft skills,
  memo half-life, contradiction detection, and guard promotion paths.
- Updated `api_reference.md` for Learning UI endpoints.
- Updated Learning-related spec comments to stop describing implemented behavior
  as xfail/defer.

Verification:

```powershell
uv run pytest tests/specs/spec_learning_pipeline.py tests/specs/spec_insight_feedback_loop.py -q
```

Master verification after merge: 32 passed.

## Completed In First Global Pass

### Strategy / RuleSet Removal Drift

Position C removed the rules engine, but current SDD docs still had live-path
language around `strategy_designer`, `backtest_orchestrator`, RuleSet exits, and
Optuna. This pass corrected the highest-risk current design docs:

- `agent_contracts.md`
  - Scheduler job count corrected to 31.
  - `paper_trader` no longer claims RuleSet exits can close positions directly.
  - `position_manager` inputs/triggers no longer list RuleSet exits.
- `knowledge_contract.md`
  - `strategy_designer` producer/consumer references downgraded to legacy.
- `PI_OPERATOR_DESIGN_DECISIONS.md`
  - Added Position C boundary note.
  - Removed current-path claims that Operator can trigger strategy_designer,
    backtest, or Optuna.
- `DOC_ATLAS_BY_DOMAIN.md`
  - Strategy domain changed to legacy strategy/promotion scope.
- `SYSTEM_MAP.md`, `serenity_contract.md`, and spec comments
  - Updated current prompt terminology from "PI prompt" to "System Operator
    prompt" where the reference is not historical.

Verification:

```powershell
uv run pytest tests/specs/spec_learning_pipeline.py tests/specs/spec_insight_feedback_loop.py tests/specs/spec_data_utilization.py tests/specs/spec_serenity_evidence.py tests/specs/spec_serenity_attribution.py tests/specs/spec_universe_pipeline.py tests/specs/spec_strategy_pool.py tests/specs/spec_pipeline_position_manager.py -q
```

Result in the SDD worktree: 102 passed, 3 skipped.

## Remaining Global SDD Work

The project-wide sweep is not finished. Continue with these checks:

1. API reference completeness
   - Compare every FastAPI route against `docs/design/api_reference.md`.
   - Current pass added major missing modern endpoints, but a generated
     route-vs-doc diff should still be run before claiming completion.

2. Terminology cleanup
   - Decide which historical `PI Agent` files should remain explicitly
     historical, and which current L2 docs/spec comments should say
     `System Operator`.

3. Strategy legacy boundary
   - Continue scanning current L2 docs for `strategy_designer`,
     `backtest_orchestrator`, `RuleSet`, `ruleset_exit`, and Optuna language.
   - Historical plans/issues may keep old wording, but current contracts must
     state that rules engine paths are deleted.

4. Config-to-UI documentation
   - Cross-check `aletheia/config/schema.py` against Settings UI and
     `api_reference.md` after recent settings additions.

5. Spec status honesty
   - Audit `tests/specs` for `status="missing"` and xfail claims.
   - Missing claims are allowed when intentional, but each should point to a
     current product decision or issue, not a stale implementation gap.

