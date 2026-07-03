# DB Retention / Reduce Size Decision Audit - 2026-07-03

## Scope

Branch: `audit/db-retention-reduce-size`

This covers D14 and D15 as a read-only decision audit:

- no row deletes
- no archival writes
- no `VACUUM`
- no Risk Engine behavior changes
- no hardening of `reduce_size`

## D14: app.db / Knowledge Bloat

Production DB snapshot:

- `data/app.db`: 448,491,520 bytes
- page count: 109,495
- page size: 4,096
- freelist count: 14 pages
- immediately reclaimable freelist: about 57 KB

`VACUUM` is not a useful immediate fix because freelist is only about 0.013% of
the DB file.

Largest DB objects from `dbstat`:

| Object | Bytes |
|---|---:|
| `opportunities` | 61,349,888 |
| `agent_shell_messages` | 52,092,928 |
| `agent_run_steps` | 43,581,440 |
| `universe_feature_snapshots` | 32,890,880 |
| `universe_forward_outcomes` | 25,194,496 |
| `funding_rate_snapshots` | 18,669,568 |
| `agent_shell_heartbeats` | 14,012,416 |
| `open_interest_snapshots` | 13,279,232 |
| `agent_shell_artifacts` | 9,797,632 |
| `market_snapshots` | 9,134,080 |
| `agent_shell_events` | 8,425,472 |
| `agent_tool_calls` | 7,761,920 |

Key row counts:

- `opportunities`: 21,424
- `agent_shell_messages`: 20,042
- `agent_run_steps`: 124,348
- `agent_tool_calls`: 80,097
- `market_snapshots`: 73,493
- `agent_runs`: 12,026
- `knowledge_items`: 7,090
- `ps_lessons`: 374

Knowledge is not the main DB-size driver:

- `knowledge_items` content bytes: 228,919
- `knowledge_items` count: 7,090
- archived `post_trade_review`: 1,937
- archived `trade_case_study`: 435
- active `system_action`: 3,561
- low-confidence distribution:
  - `medium_confidence`: 6,212
  - `high_confidence`: 877
  - `speculative`: 1

Existing safe cleanup tooling:

- `scripts/archive_post_trade_reviews.py` archives old post-trade reviews and
  never deletes rows.
- `scripts/cleanup_duplicate_knowledge.py` archives duplicate DB rows; file
  deletion is optional and explicit.
- scheduler retention tests prove cleanup avoids trust-bearing causal records.

## D15: `reduce_size` Hard Risk Decision

Current implementation:

- `dreaming_agent` may ask the LLM to recommend
  `{"action": "reduce_size", "factor": 0.3-0.8}`.
- `risk/engine.py` treats `reduce_size` as advisory and logs it as not enforced
  as a hard block.

Production data snapshot:

- `ps_lessons` rows: 374
- `ps_lessons` rows containing `reduce_size`: 0
- `knowledge_items` rows containing `reduce_size`: 0

Because no `reduce_size` lesson exists in the sampled DB, there is no production
evidence that would justify turning it into a hard risk control today.

## Quantified Outcome

- DB-size root cause corrected: knowledge is only about 229 KB of content, while
  top runtime/telemetry tables account for hundreds of MB.
- Immediate `VACUUM` expected benefit: about 57 KB, effectively zero.
- `reduce_size` production instances found: 0.
- Safe automatic changes recommended now: 0.

## Verification

- `uv run pytest tests/test_retention.py -q`
  - `9 passed`
- `uv run pytest tests/specs/spec_lesson_guards.py tests/specs/spec_pi_decision_chain.py::test_guard_chain_is_advisory_not_hard_block -q`
  - `5 passed`
- `uv run pytest tests/specs/spec_risk_engine.py::test_risk_kill_switch_blocks tests/test_risk_engine.py -q`
  - `11 passed`

## Required Owner Decisions

1. DB retention policy:
   Decide whether to add retention/prune rules for high-volume non-causal tables
   such as `opportunities`, `universe_feature_snapshots`, and
   `agent_shell_messages`. This should be a separate branch with table-by-table
   invariants and regression tests.

2. `reduce_size` semantics:
   Decide whether `reduce_size` should remain advisory or become a hard risk
   constraint. If it becomes hard, it must update `risk_engine_contract.md`, add a
   focused Risk Engine spec, and define how size reduction maps to approval,
   rejection, or adjusted notional. This cannot be safely inferred by an agent.
