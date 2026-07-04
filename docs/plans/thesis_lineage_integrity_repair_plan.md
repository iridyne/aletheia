# Thesis Lineage Integrity Repair Plan

Created: 2026-07-04

## Goal

Repair the currently actionable Thesis Lineage data-chain defects without adding
LLM calls, new tables, or trading behavior changes.

The repaired chain should make these relationships reconstructable:

```text
universe -> opportunity -> decision -> proposal/skip -> shadow/position -> outcome
```

## Non-Goals

- No new thesis generator.
- No LLM normalizer or after-the-fact reviewer.
- No prompt optimization from audit results.
- No new tables.
- No risk-limit, credential, live-trading, or execution-gateway changes.
- No fabricated historical thesis text.

## Root Causes Found

1. `pi_decisions` raw missing counts mixed different semantics.

   A large share of `opportunity_id=NULL` rows are global `skip/watch` decisions
   with no `symbol`, `direction`, or `proposal_id`. These are not per-opportunity
   chain breaks and must not be counted as such.

2. Some actionable decisions can lose their parent ID before persistence.

   The ranker context omitted `opportunity_id`, so a PI pick sourced from
   `ranker_candidates` could fail attribution if the compact Layer-1 opportunity
   list did not contain the same row.

3. Close/reduce decisions can be targetless even when the reasoning names an open
   position.

   Open-position context omitted the causal IDs, and `_attach_opportunity_context`
   returned early when the model omitted `symbol`.

4. Proposal decision metadata had key drift.

   `submit_decision_as_proposal()` wrote `supporting` / `opposing`, while the
   Thesis Lineage parser looked for `supporting_signals` / `opposing_signals`.

5. Outcome missing counts mixed pending/cancelled rows with true gaps.

   `forward_return_pct=NULL` is expected for pending or cancelled forward outcomes.
   Shadow trades younger than the horizon are also not broken yet.

6. Historical trade decisions with missing `opportunity_id` are not always safely
   backfillable.

   A deterministic backfill is safe only when an existing child row proves the
   parent, e.g. `pi_decisions.proposal_id -> trade_proposals.opportunity_id`.
   Current production data has zero such proposal-backfillable PI rows in the
   7-day window; many historical missing trade rows have proposals that also lack
   `opportunity_id`.

## Repairs

1. Preserve causal IDs in context.

   `trading_context` now carries `opportunity_id`, `strategy_id`, and related
   fields for ranker candidates and open positions. These IDs are not prompt
   instructions; they are deterministic context for attribution.

2. Improve deterministic decision attribution.

   `_attach_opportunity_context()` now:

   - links PI picks from ranker candidates when symbol/direction agree;
   - refuses ranker attribution when Layer-1 has the same symbol with opposite
     direction;
   - links close/reduce decisions to the single matching open position when the
     target is deterministic;
   - avoids guessing when multiple positions are possible.

3. Persist existing decision fields.

   `commands.evaluate_trading_opportunity()` now passes through existing
   `bear_case`, `key_assumptions`, `supporting_signals`, and `opposing_signals`
   into the reflection/DB persistence path.

4. Keep proposal metadata parseable and semantically stable.

   Future proposal rationale JSON includes both explicit
   `supporting_signals` / `opposing_signals` and legacy
   `supporting` / `opposing` keys.

5. Reconstruct deterministic historical links read-only.

   Thesis Lineage read model can infer a missing decision opportunity from
   `proposal_id -> trade_proposals.opportunity_id` and marks it as:

   ```text
   decision_opportunity_id_inferred_from = "proposal_id"
   ```

   This does not mutate historical rows.

6. Split audit metrics into real gaps and expected blanks.

   The audit now reports:

   - `global_no_target_count`
   - `link_required_count`
   - `link_required_missing_opportunity_id`
   - `proposal_backfillable_missing_opportunity_id`
   - `stale_missing_pnl_pct`
   - `overdue_pending`
   - mature-only shadow/forward missing counts

## Verification Plan

Required before calling the repair complete:

1. Focused tests:

   ```powershell
   uv run pytest tests/test_thesis_lineage.py tests/test_anti_hallucination_e2e.py tests/test_proposal_rationale.py tests/specs/spec_data_model.py::test_pi_decision_carries_opportunity_id tests/specs/spec_decision_lifecycle.py tests/specs/spec_serenity_attribution.py -q
   ```

2. Architecture/spec smoke:

   ```powershell
   uv run pytest tests/test_architecture_fitness.py tests/specs/ -q
   ```

3. End-to-end diagnostics after merge/restart:

   ```powershell
   uv run aletheia gateway restart --no-browser
   uv run aletheia doctor --deep
   uv run aletheia thesis audit --days 7
   uv run aletheia thesis snapshot --days 7
   ```

4. Final evaluation:

   Compare the new audit with the prior baseline:

   - raw `missing_opportunity_id` remains useful as historical debt;
   - `global_no_target_count` should explain legitimate global skip/watch rows;
   - `link_required_missing_opportunity_id` is the true PI linkage defect count;
   - `signal_pending` rows created after this repair should stay at zero missing
     opportunity IDs;
   - `overdue_pending` should be near zero when the settler is healthy;
   - `stale_missing_pnl_pct` should trend down or stay bounded.

## Current Production Baseline

Read-only evaluation against the local production database on 2026-07-04 showed:

- 7-day total thesis records: 6818
- raw `pi_decisions.missing_opportunity_id`: 4347
- global no-target skip/watch rows: 2412
- link-required PI rows: 3900
- link-required missing opportunity IDs: 1935
- proposal-backfillable PI rows: 0
- shadow rows missing PnL: 493
- stale shadow rows missing PnL: 403
- settled forward outcomes missing return: 0

Interpretation: the top priority is preventing new actionable rows from losing
their parent IDs. Most historical gaps cannot be repaired without unsafe
guesswork, so they must remain visible as historical debt rather than rewritten.
