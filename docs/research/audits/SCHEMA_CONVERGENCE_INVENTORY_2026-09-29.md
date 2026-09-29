# Schema Convergence Inventory — 2026-09-29

**Issue:** #3706 (audit child), parent #3700
**Audit SHA:** `63fc1c40776431641875d7db4ed42630f2a29e86`
**Migration head:** `20260927_0089_entry_cancel_retry_v90` (89 migrations applied)
**Schema version:** `aletheia-sqlite-v90`
**Fedora runtime SHA (recorded/config):** `12d4d21d8b55988f987514fb8502d449b8c5ec10`
**Fedora schema version:** `aletheia-sqlite-v90` (matches fresh build — confirmed)
**Audit date:** 2026-09-29
**Status:** `READ-ONLY / NO SOURCE COMMIT`

---

## 1. Method

- Fresh isolated database from `ALETHEIA_DATA_DIR=/tmp/aletheia_schema_audit_FVRr uv run aletheia data migrate`
- Full `sqlite_master` dump of tables, indexes, triggers, views
- Static reachability via `git grep` across `core/`, `extensions/`, `ops/`, `tests/`
- Read-only Fedora census via Python `sqlite3` URI `file:...?mode=ro` (no writes, no migration, no restart)
- Three parallel fork agents for deep analysis

---

## 2. Fresh Effective Schema — Full Object Count

| Object type | Count |
|---|---|
| Total `sqlite_master` objects | 149 tables + 328 indexes + 54 triggers + 0 views |
| SQLite internal (sqlite_sequence, sqlite_stat1) | 2 |
| FTS shadow (knowledge_fts + 5 shadow tables) | 6 |
| Migration ledger (aletheia_schema_migrations) | 1 |
| **Application tables** | **141** |

**ps_* intentional historical tables (#3134):** 15 of the 141
**Effective current application tables:** 126

---

## 3. Already Retired / Absent from Fresh Schema

These tables were dropped by migration and are NOT in the fresh DB:

| Table | Dropped by | Authority |
|---|---|---|
| strategy_experiments | `_LEGACY_EXPERIMENT_DROP_ORDER` in migration 0060/0061 | #2737 |
| learning_experiments | same | #2737 |
| learning_exposures | same | #2737 |
| learning_contribution_verdicts | same | #2737 |
| learning_inference_decisions | same | #2737 |
| learning_lifecycle_events | same | #2737 |
| filter_shadow_experiments | same | #2737 |
| filter_shadow_evaluations | same | #2737 |

**Note:** `prune_filter_shadow_experiments()` and `prune_filter_shadow_evaluations()` still exist in `repositories.py` and the scheduler still calls them. These are dead code. The prune functions return 0 safely (they check `_canonical_lab_authority()` first), but the scheduler entry and repository functions are cleanup residue (#2737 follow-up boundary).

---

## 4. Table Classification — All 141 Application Tables

### Category A: Current Canonical (core causal chain, do not touch)

These 94 tables own exactly one current fact per #3700 §5 intentional separation:

| Subsystem | Tables |
|---|---|
| Agent Shell | agent_run_artifacts, agent_run_steps, agent_runs, agent_shell_artifacts, agent_shell_commands, agent_shell_events, agent_shell_heartbeats, agent_shell_messages, agent_shell_sessions, agent_shell_tasks, agent_tool_calls |
| Audit / Identity | audit_events, runtime_sessions, observation_authority_cutover_proofs, observation_authority_state, operator_evaluation_events |
| Data Collection | broker_funding_settlement_cursor, coingecko_top_snapshots, collector_health_state, fast_trigger_market_snapshots, fred_source_batches, funding_rate_snapshots, liquidation_events, macro_snapshots, market_ohlcv, market_regimes, market_snapshots, news_items, ohlcv_backfill_state, onchain_snapshots, open_interest_snapshots, options_market_structure_snapshots, options_volatility_snapshots, orderbook_snapshots, perp_price_snapshots, volume_ranking_snapshots |
| Entry / Planning | candidate_planning_generations, conditional_entry_authorizations, entry_plans, entry_risk_reservations, entry_trigger_activations |
| Execution / Risk | fills, orders, positions, proposal_execution_claims, risk_decisions, trade_proposals, exit_breakeven_shadow_assignments, exit_breakeven_shadow_outcomes, reconciliation_integrity_incidents |
| Filter / Observation | filter_candidate_outcomes (FCO), filter_decision_batches, filter_decisions, observation_delivery_outbox, observation_deny_tombstones |
| Learning | knowledge_items, learning_hypotheses, learning_hypothesis_nominations, learning_job_health, learning_lesson_reviewer_attempts, learning_policy_epochs |
| Portfolio / Accounting | capital_events, historical_accounting_reconciliations, paper_account_events, paper_funding_revival_cursor, perp_structure_shadow_outcomes, perp_structure_snapshots, portfolio_snapshots, position_close_events, position_disappearance_candidates, position_disappearance_observations, position_exit_plan_versions, position_path_observation_deferred_buckets, position_path_observations, position_path_observer_cursor, position_protection_state, position_protection_triggers, position_realtime_revision |
| Prediction Research | prediction_events, prediction_market_claims, prediction_markets, prediction_observer_runs, prediction_probability_snapshots |
| Runtime / Config | extension_registry, extension_runs, persona_rollout_events, persona_rollouts, settings |
| Serenity | serenity_agent_locks, serenity_claims, serenity_evidence, serenity_feedback_events, serenity_opportunities, serenity_post_mortems, serenity_profile_snapshots, serenity_replay_snapshots, serenity_research_runs, serenity_risk_gates, serenity_run_events, serenity_theme_claims, serenity_themes, serenity_user_profiles |
| Universe | controller_recommendations, universe_discovery_benchmarks, universe_feature_snapshots, universe_members, universe_registry, universe_scanner_runs, universe_setups, universe_snapshots, universe_twin_shadow_batches, universe_twin_shadow_records |
| **System Operator Decisions** | **pi_decisions** (active writer in commands.py; uniquely links decision → proposal → position) |
| **Outcomes (co-existing with UFO)** | **filter_candidate_outcomes** (FCO; 184,345 rows, active writer) |

### Category B: Current Supporting / Mixed Owner (no whole-table retirement)

| Table | Classification | Status | Owner |
|---|---|---|---|
| `opportunities` | mixed owner (3 active writers: equity, prediction, crypto) | active | no retirement |
| `alpha_control_observations` | mixed owner by layer | serenity-layer writer current; universe-layer entries are old writer's residue (last: 2026-09-10) | layer-level cleanup; Serenity owner stays |
| `outcome_hook_jobs` | current supporting | 788 rows, all completed, last entry today; drives shadow settlement replay | current |

### Category C: Intentional Historical (#3134)

15 `ps_*` tables retained as immutable historical ledger:
ps_checkpoints, ps_event_log, ps_execution_intents, ps_fills, ps_gateway_locks, ps_gateway_sessions, ps_job_runs, ps_lessons, ps_order_intents, ps_orders, ps_policy_versions, ps_positions, ps_reconciliation_reports, ps_scheduled_jobs, ps_skills

### Category D: Retirement Candidates (confirmed — all four conditions met)

| Table | Fedora rows | Last write | Production writer | Business reader | Owner |
|---|---|---|---|---|---|
| `backtest_runs` | 0 | never | NONE | NONE | #3703 |
| `backtest_trades` | 0 | never | NONE | NONE | #3703 |
| `strategy_records` | 0 | never | NONE | legacy compatibility only (observers attach frozen strategy_id to new facts) | #3702 |
| `approvals` | 0 | never | NONE | NONE | new focused child (after #3706 proof) |

### Category E: Conditional Retirement Candidate (producer dead, readers live)

| Table | Fedora rows | Last write | Production writer | Business readers | Owner |
|---|---|---|---|---|---|
| `shadow_trades` | 37,968 | 2026-08-27 (>1 month ago) | NONE (persist_shadow_batch's sole caller agents/paper_trader.py deleted in #2945) | 15+ active analytics/learning/counterfactual readers | #3704 |

### Category F: Active Writer — Scheduled for Convergence (#3701)

| Table | Fedora rows | Status | Writer | Owner |
|---|---|---|---|---|
| `universe_forward_outcomes` (UFO) | 101,828 | pending: 987, settled: 88,078, cancelled: 12,763 | `service.py:1127`, `outcome_recorder.py:239,398,417,458`, `registry.py:90` (ALL ACTIVE TODAY) | #3701 |
| `filter_candidate_outcomes` (FCO) | 184,345 | active co-writer | `service.py`, `twin_persistence.py`, `filter_outcome_recorder.py` (active) | current canonical, expands after #3701 |

**Critical:** FCO has a Learning fallback path that reads UFO when FCO coverage is incomplete (`cohorts.py:962-965`, flag `_learning_outcome_source: "universe_forward_outcomes_legacy_fallback"`). UFO cannot retire until this fallback is removed.

### Category G: No Current Writer / Config Fallback (retirement path via existing issue)

| Table | Fedora rows | Current writer | Current reader | Owner |
|---|---|---|---|---|
| `universe_policies` | 0 | NONE (test-only writer) | `get_active_universe_policy()` with config fallback | #2914 |

### Category H: Current Candidate Version Matrix (#3708)

Not a table but a naming surface. See §6 below.

---

## 5. Fedora Read-Only Census (2026-09-29)

**DB path:** `/home/admin/.aletheia/app.db`
**Schema:** `aletheia-sqlite-v90` (matches fresh migration)
**Gateway:** not running (config-recorded SHA: `12d4d21d8b55`)
**Access method:** `python3 sqlite3` via SSH, read-only URI

### Candidate / Mixed-Owner Table Census

| Table | Rows | Min timestamp | Max timestamp | Key distribution |
|---|---|---|---|---|
| backtest_runs | 0 | — | — | — |
| backtest_trades | 0 | — | — | — |
| strategy_records | 0 | — | — | — |
| shadow_trades | 37,968 | 2026-08-07 | 2026-08-27 | resolved: 36,349; unresolved: 1,619; epoch_ids: 226de913875e (28,574), 95c7c02395a5 (7,832), a0ee9b3fe1ed (1,562) |
| universe_forward_outcomes | 101,828 | 2026-08-29 | 2026-09-29 | settled: 88,078; cancelled: 12,763; pending: 987; 4h/1h/24h/15m: 25,457 each |
| filter_candidate_outcomes | 184,345 | — | — | 4h: 116,280; 1h: 56,245; 24h: 5,910; 15m: 5,910 |
| approvals | 0 | — | — | — |
| universe_policies | 0 | — | — | — |
| alpha_control_observations | 69,798 | 2026-08-07 | 2026-09-10 | layer: all "universe" (old writer's residue; current writer is serenity-layer) |
| pi_decisions | 1,676 | 2026-08-07 | 2026-09-29 | signal_source: all "market_observer" (default string, not a live signal); action: trade(737), evicted(280), skip(228), close(153), adjust(128) |
| outcome_hook_jobs | 788 | 2026-08-07 | 2026-09-29 | status: all "completed" |

**Fedora fact:** `shadow_trades` last write was 2026-08-27 (32+ days before audit). `universe_forward_outcomes` last write was today (2026-09-29 03:48 UTC). Both confirm producer-dead vs active-writer status.

---

## 6. Writer/Reader/Authority Matrix — Retirement Candidates

### 6.1 backtest_runs + backtest_trades

| Dimension | Finding |
|---|---|
| DDL | `migrations.py:2618` (legacy_schema v2) |
| Production writer | NONE — Backtest engine/DSL deleted |
| Production reader | NONE |
| Scheduler residue | `scheduler.py:3049` calls `repo.prune_backtest_runs(r.backtest_runs_days)` — dead maintenance |
| Config residue | `schema.py:1445` `backtest_runs_days: int = 60` — dead config field |
| Repository residue | `repositories.py:6176-6211` — `prune_backtest_runs/trades()` functions |
| API/CLI/Frontend | NONE |
| Tests | `test_data_retention.py:1492-1510`, `spec_data_model.py:83` — protect the dead maintenance |
| **Retirement verdict** | **CLEAR** — #3703 may proceed without a stop condition |

### 6.2 strategy_records

| Dimension | Finding |
|---|---|
| DDL | `migrations.py:473` (base migration) |
| Production writer | NONE — Strategy generation/DSL/promotion engine deleted; 0 rows in Fedora |
| Compatibility readers (attach legacy strategy_id to new facts) | `extensions/research/divergence_scanner.py:302`, `equity_observer.py:492`, `prediction_observer.py:299`, `dreaming_agent.py:208` — call `list_strategies()` and attach a frozen strategy_id to new observations |
| API/UI readers | `server/routes/data.py` `/strategies`, `/strategies/summary`; frontend `getStrategies()` / `StrategyRecord` type |
| Agent context reader | `agent_shell/context.py:345` — `list_strategies(limit=20)` in operator context |
| Analytics reader | `trade_stats.py`, `trade_attribution.py` — attribution via frozen rows |
| Config/retention | No `strategy_records_days`, not in `_PRUNABLE`, rows are permanent |
| **#3702 Batch A target** | Remove every observer's `strategy_id` attachment to new facts |
| **#3702 Batch B target** | Remove API routes, frontend types, agent context pool, analytics KPI surfaces |
| **Retirement verdict** | **CONDITIONAL** — requires #3702 to first sever live compatibility surfaces |

### 6.3 shadow_trades

| Dimension | Finding |
|---|---|
| DDL | `migrations.py:217` (base) + many ALTER TABLE additive columns |
| Production writer | NONE — `persist_shadow_batch()` in `extensions/counterfactual/trades.py:46` has zero production callers; sole caller `agents/paper_trader.py` deleted in commit `2e69baae9` (#2945) |
| Scheduler maintenance (updates only, no creates) | `scheduler.py:2318-2336` — `resolve_stale_shadows()` settles existing rows; `scheduler.py:2339-2357` — `outcome_hook_replay` processes hook jobs but shadow_trades rows came from producer |
| Active business readers | 15+ paths: `reflection.py`, `data.py` API, CLI `inspect shadow-trades`, `thesis.py`, `content.py` Doctor probe, `alpha_attribution.py`, `ranker_objective_audit.py`, `opportunity_lifecycle_story.py`, `opportunity_flow.py`, `thesis_lineage.py`, `learning/hypothesis_research.py`, `learning/outcome_feedback.py`, `counterfactual/advisories.py`, `counterfactual/propensity.py`, `counterfactual/calibration.py`, `analytics/guard_impact.py` |
| Unique semantics requiring census | batch_id, rank/score, selected assignment, policy_epoch_id, Ranker-era economics, Opportunity linkage |
| **Retirement verdict** | **#3704 DECISION REQUIRED** — producer dead, Fedora data frozen as of 2026-08-27, but consumers require individual disposition. Cannot retire without full #3704 semantic census. |

### 6.4 approvals

| Dimension | Finding |
|---|---|
| DDL | `migrations.py:140` |
| Production writer | NONE — no `INSERT INTO approvals` anywhere |
| Production reader | NONE — no repo function, no route, no analytics consumer |
| Test references | `test_architecture_fitness.py:701` — guards that it is NOT in `_PRUNABLE` (this guard itself becomes moot on retirement) |
| Retention backfill | Listed in `data_retention_backfill.py` as non-prunable (protective, not a writer) |
| **Retirement verdict** | **CLEAR** — cleanest orphan in the schema; create focused child after #3706 |

### 6.5 universe_forward_outcomes (UFO)

| Dimension | Finding |
|---|---|
| Production writers | `service.py:1127` (universe_refresh scheduler), `outcome_recorder.py:239,398,417,458` (settler), `registry.py:90` (cancel on reset) — ALL CURRENTLY ACTIVE |
| Production readers | `outcome_recorder.py:295,371`, API `/universe/{id}/outcomes`, `alpha_attribution.py`, many analytics, Agent context, Learning cohort fallback |
| **Learning fallback path** | `extensions/learning/cohorts.py:962-965` — falls back to UFO when FCO coverage incomplete; this path MUST be removed before UFO can retire |
| **Retirement verdict** | **NOT READY** — active producer today; #3701 must prove full FCO parity before UFO retirement |

### 6.6 universe_policies

| Dimension | Finding |
|---|---|
| Production writer | NONE (test-only: `repo_test_helpers.py:538`) |
| Production reader | `get_active_universe_policy()` → `server/routes/universe.py:997` BUT falls back to config defaults when no rows |
| Frontend | `client.ts` defines `getUniversePolicy()` but zero components call it |
| **Retirement verdict** | **#2914 owns** — retirement straightforward once route/client stub removed |

---

## 7. V1/V2/V3/V4 Candidate Name Matrix (#3708)

### Python class names (cosmetic only — no competing implementations)

| Current name | Should become | Evidence |
|---|---|---|
| `CandidateOpportunityContextV1` | `CandidateOpportunityContext` | Single implementation; persisted literal "candidate-opportunity-context/v1" |
| `CandidateSelectionUtilityV1` | `CandidateSelectionUtility` | Single implementation; persisted literal is "candidate-selection-utility/v2" (mismatch!) |
| `HardGateDecisionV1` | `HardGateDecision` | Single implementation |
| `TailEvidenceV1` | `TailEvidence` | Single implementation |
| `EconomicUtilityEstimateV2` | `EconomicUtilityEstimate` | Single implementation (jumped from no V1 to V2) |

### Persisted literals with REAL discriminator branch logic

| Literal | Location | Status |
|---|---|---|
| `candidate-selection-utility-record/v4` | current writer (`candidate_selection_shadow.py:35`) | CURRENT; v1/v2/v3 are historical-only |
| `candidate_selection_utility_v1` | `config/schema.py:1781` default; `repositories_universe.py:397-399` | **CURRENT ACTIVE POLICY NAME** — not a legacy literal |
| `legacy_total_score_v1` | `candidate_selection_shadow.py:407` | **STILL WRITTEN** when shadow authority not applied; both this and `candidate_selection_utility_v1` are written today |
| `candidate-selection-utility/v2` | `CandidateSelectionUtilityV1.version` literal | Current (confusingly named) |

### #3708 Stop Note

`legacy_total_score_v1` is still produced by the shadow system (`candidate_selection_shadow.py:407`) when authority is not applied. It appears in validators and WHERE clauses. Removing it from the current code before its writer is retired would corrupt records. **#3708 must not execute until `legacy_total_score_v1` write path is severed** (likely follows #3702 Strategy retirement, which removes the authority confusion).

---

## 8. Corrections to #3706 Static Finding Registry

| Original claim | Corrected fact | Evidence |
|---|---|---|
| `universe_forward_outcomes`: "current selected-outcome semantics still exist" | CORRECT but understated: UFO has an ACTIVE CURRENT WRITER writing today. #3701 must wait for full FCO parity before retiring UFO. | Fedora: max entry_ts = 2026-09-29, 987 pending rows |
| `alpha_control_observations`: "mixed owner by layer; Serenity writer remains" | CORRECT: only serenity-layer writer exists in current code; all 69,798 Fedora rows are layer="universe" from the OLD writer that was retired. Current code has zero universe-layer writer. | `causal_controls.py:22,179` — LAYERS dict has only "serenity" |
| `pi_decisions`: not in registry | pi_decisions is CURRENT CANONICAL — active writer in `commands.py:1753,2151,2317`, last Fedora write today. NOT a retirement candidate. | Fedora max: 2026-09-29T02:13 |
| `approvals`: "high-confidence orphan/retirement candidate; dynamic proof missing" | CONFIRMED orphan — zero rows Fedora, zero production writers or readers anywhere | grep zero results for INSERT/SELECT |

---

## 9. Evidence Blind Spots

| Blind spot | Risk | Mitigation |
|---|---|---|
| Fedora gateway not running | Cannot verify live scheduler writes vs config; boot_sha unconfirmed | Schema v90 matches fresh build; recorded_sha is old (12d4d21d) but schema fact is sufficient |
| `prune_filter_shadow_experiments()` in scheduler on dropped table | If table resurrection attempt occurs, scheduler would fail | Function returns 0 safely via `_canonical_lab_authority()` check; medium risk |
| Dynamic SQL / generic `table_utils.py` helper | May touch tables not found by grep | `table_utils.py` inspected — only DDL introspection, no data writes |
| External maintenance scripts (`ops/`) | Could theoretically write to any table | Checked `data_retention_backfill.py` — lists tables for protection, no INSERT |
| `aletheia.db` on Fedora (separate file) | Unknown contents — not the operational `app.db` | Legacy filename; `app.db` is current operational DB |

---

## 10. Unique Owner Mapping

| Finding | Unique owner Issue | Next action |
|---|---|---|
| `backtest_runs` + `backtest_trades` retirement | #3703 | Activate after #3706 DoD |
| `strategy_records` retirement | #3702 | Activate after #3703 |
| `universe_forward_outcomes` → FCO convergence | #3701 | Activate after #3703 + #3702 |
| `shadow_trades` disposition | #3704 | Activate after #3701 |
| Current Candidate naming convergence | #3708 | Activate after #3704; wait for `legacy_total_score_v1` stop note resolution |
| `approvals` retirement | NEW focused child | Create after #3706 DoD; simplest retirement in the program |
| `universe_policies` retirement | #2914 | Independent of serial order; follow #2914 |
| `alpha_control_observations` universe-layer cleanup | Layer-level proof, not whole-table | Investigate: are universe-layer rows used? If not, time-bound deletion; #3700 master owns decision |
| Scheduler dead code for dropped experiment tables | #2737 follow-up | Low urgency; functions return 0 safely |

---

## 11. Recommended Next Active Child

**#3703 — Backtest retirement**

Evidence:
- 0 rows in Fedora for both tables
- No production writer (engine deleted)
- No current business reader
- Residue is limited to: config field `backtest_runs_days`, scheduler cleanup call, two prune functions, tests that protect the dead maintenance, and 3 lines in `ops/maintenance/data_retention_backfill.py`
- Smallest, cleanest retirement; validates the retirement pattern for later children

No STOP conditions found. #3703 may activate once #3706 DoD is confirmed.

---

## 12. Definition of Done Status

| DoD item | Status |
|---|---|
| Exact-head fresh schema exported and every application table classified | ✅ 141 tables classified |
| Candidate and mixed-owner tables have writer/reader/authority/semantic matrices | ✅ §6 |
| Fedora read-only census records exact runtime identity and relevant table facts | ✅ §5 (schema v90 confirmed, key tables censused) |
| Dynamic SQL/test-only/external-script blind spots bounded and documented | ✅ §9 |
| Every confirmed finding maps to one unique focused owner | ✅ §10 |
| New candidates (approvals) receive evidence-backed focused child or KEEP | ✅ approvals: new focused child recommended |
| Mixed tables split by semantic subset | ✅ alpha_control_observations (by layer), pi_decisions (by action subset, not retiring) |
| Stale docs, tests and old Issue assumptions listed for actual owner | ✅ §8 corrections |
| #3700 updated with next active child | pending (post this checkpoint) |

---

*Generated by: exact-head static reachability + Fedora read-only census*
*Commit: none (READ-ONLY audit)*
