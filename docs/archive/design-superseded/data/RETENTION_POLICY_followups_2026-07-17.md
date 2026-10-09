# Data Retention Policy — dated follow-ups (archived 2026-10-09)

> 2026-10-09 归档：原为 `docs/design/data/RETENTION_POLICY.md` 的 §7–§11（容量投影与实测、
> Phase 2 开放问题裁决、跨仓库 TODO、前端后续、延期写入守卫、旧源码树删除 / CronCreate）。
> 这些是 2026-07-17 至 2026-08-16 工作流的带日期记录与待办，不是当前保留规则；
> 仍需做的条目应开 GitHub Issue。当前规则见
> [`docs/design/data/RETENTION_POLICY.md`](../../../design/data/RETENTION_POLICY.md)。

## 7. Trajectory and corrected capacity evidence

The table below is the 2026-07-17 planning projection, not proof that Phase 4
completed. On CASI at 2026-08-06, the measured `app.db` was 1781.84 MiB with
88.11 MiB on the freelist and `auto_vacuum=NONE`; therefore the projected
530–550 MiB state was never established. From the documented 1031 MiB point to
the current measurement the historical net slope is about 37.6 MiB/day, but
recent high-volume experiment writers stopped on 2026-08-03, so that slope must
not be extrapolated as a precise forward forecast.

| Day | `app.db` (MiB) | `agent_shell/` (MiB) | Notes |
|-----|----------------|----------------------|-------|
| 0 (before Phase 4) | 1031 | 278 | Starting state 2026-07-17. `agent_shell_messages` 219 / `market_ohlcv` 103 / `agent_run_steps` 53 / `filter_candidate_outcomes` 49 / `universe_feature_snapshots` 60 / `controller_recommendations` 42 / `filter_decisions` 34 / `filter_decision_batches` 32 / `agent_shell_events` 32 / `funding_rate_snapshots` 47 / `open_interest_snapshots` 38 / others. Audit chain 15 MiB, learning 8 MiB. |
| 0 (immediately after Phase 4) | 530 | 25 | Post gateway-stop → backup → prune → VACUUM → restart. Expected reclaim ~442 MiB rows + ~50 MiB via VACUUM. Retro-strip of `content.context` on surviving <3d rows adds ~8 MiB. If landing lands at 550 MiB, run VACUUM again after `wal_checkpoint(TRUNCATE)` and `PRAGMA optimize`. |
| 7 | 520 | 30 | Steady state stabilises. Write-guardrails 1 / 3 / 14 / 18 / 15 / 5 deployed by Day 7 cut daily growth from ~25 MiB/day to ~5-8 MiB/day; nightly prune counterbalances. |
| 30 | 540 | 40 | Full steady state; retention windows fully populated. `filter_candidate_outcomes` @ 30d ≈ 200 MiB dominates. Add write-guardrail #6 (batch scanner_weights_quota) early to keep `filter_decisions` 45d plateau near ~150 MiB rather than 200 MiB. |
| 60 | 530 | 45 | Long-run steady state with all guardrails and clean_window_cutoff active. `filter_candidate_outcomes` holds at ~200 MiB (would blow past 500 MiB without retention). If future writers land in currently-empty `artifacts/` or `reports/` subdirs, per-subdir retention must be in place before those writers ship. |

The runtime now appends one exact post-cleanup sample to
`logs/database_capacity.jsonl`, including main DB, WAL, logical used bytes,
freelist bytes, capacity ratio, reclaim mode, and the latest PASSIVE-checkpoint
backlog. Use at least 30 samples before
claiming a new steady-state curve. A first-order post-v4 estimate is roughly
1.4–1.7 GiB after redundant-index removal and offline compaction, with a 2 GiB
operational budget; this remains a model until the nightly history validates it.

`aletheia data capacity --deep` uses SQLite `dbstat` aggregate mode and reports
per-object packing ratio, so a VACUUM decision can distinguish freelist/poor page
packing from legitimate retained payload. Capacity trend uses the median of
pairwise physical-size slopes; fewer than 3 samples or less than 7 elapsed days is
reported as provisional/insufficient rather than a reliable ETA.

### 7.1 Explicit physical-layout optimization and remaining boundaries

- `market_ohlcv` retention now deletes by the durable composite primary key rather
  than hidden `rowid`. Explicit offline compact can therefore rebuild the table as
  `WITHOUT ROWID`, eliminating the separate primary-key B-tree while preserving
  the logical schema and indexed timeframe query. This is intentionally not a v6
  migration: migrations remain additive-only, and no dataset-sized rebuild is an
  implicit first-deploy or Gateway-start side effect.
- A read-only CASI sample on 2026-08-06 copied 250,000 real retained rows into both
  layouts. Rowid table + PK + timeframe index used 38,699,008 bytes; WITHOUT ROWID
  table + timeframe index used 33,611,776 bytes, a 13.15% reduction. Point primary-key
  lookup measured 0.021277 ms versus 0.021260 ms and the prune selector 0.024 ms versus
  0.023 ms, so no regression was measurable at this sample size. Applied to the current
  post-v4 OHLCV objects, the projected saving is about 36 MiB; this remains a projection
  until an owner schedules and measures production compaction.
- Permanent Learning/audit ledgers must not be TTL-pruned. If their growth becomes
  material, use a separately specified content-addressed manifest or verified cold
  archive with transparent replay; never silently delete or detach authority evidence.
- Time retention is paired with per-instrument bar caps and capacity alerts. A hard
  global row cap must not override active-experiment clean-window guards.

SQLite authorities for these choices: [auto/incremental vacuum and optimize](https://www.sqlite.org/pragma.html),
[WAL checkpoint behavior](https://www.sqlite.org/wal.html),
[partial indexes](https://www.sqlite.org/partialindex.html),
[dbstat aggregate mode](https://www.sqlite.org/dbstat.html), and
[WITHOUT ROWID trade-offs](https://www.sqlite.org/withoutrowid.html).

---

## 8. Open-questions resolution (Phase 2 decisions)

All 11 open questions from workflow Phase 1 are resolved. Decisions below
match `/tmp/RETENTION_SPEC.md#user-confirmed-decisions-phase-2`.

| # | Question | Decision |
|---|----------|----------|
| 1 | Filter tables safety (`filter_candidate_outcomes` + `filter_decisions`) | **A** — 30d/45d with clean_window_cutoff guard. Effective cutoff = `min(now-Nd, MIN(exposed_at) for active open experiments)`. Unexposed registrations are ignored. |
| 2 | `content.context` write-fix coordination | Cross-repo coordinated. Python-side (`aletheia/server/routes/operator.py:512-524` + `aletheia/agent_shell/operator_store.py`) ships in this workflow. TypeScript peer (`operator-shell/src/operator_loop.ts:1023`) documented as TODO in §9 for the external repo but NOT implemented here. |
| 3 | `auto_vacuum` mode | **INCREMENTAL**. One-time `PRAGMA auto_vacuum=INCREMENTAL` in the backfill (takes effect after VACUUM), then rely on `PRAGMA incremental_vacuum(1000)` in `_run_cleanup`. |
| 4 | `agent_shell_messages` retention shape | **Type-tiered**. `message_type='context'`: 3d. All other message_types: 14d. |
| 5 | Backup rotation | **`keep_recent=7`** (count-based). |
| 6 | `filter_shadow_evaluations` schema | **Keep append-only.** No UPSERT-latest schema change. |
| 7 | `universe_snapshots` + `universe_members` + `universe_scanner_runs` | **30d retention plus selected-member fallback guard.** Parent pruning preserves rows still referenced by a guarded `universe_forward_outcomes` observation. |
| 8 | Learning cohort hash | **Drop `code_commit` from statistical identity.** `learning_exposures.code_commit` remains provenance; authority/replay currentness uses the versioned, secret-free `behavior_contract_hash`. |
| 9 | Retro-strip `content.context` | **Yes.** One-shot UPDATE strips `content.context` from surviving <3d rows inside the backfill script. Saves ~8 MiB immediately. |
| 10 | Legacy source tree removal | **NOT part of this workflow.** Schedule a `CronCreate` job for 2026-08-16. See §11. |
| 11 | Doctor threshold regression tests | **Yes.** Agent 10 writes `tests/test_doctor_thresholds.py` covering agent_shell_events counts, filter_candidate_outcomes registered/settled ratios, coingecko_top_snapshots 30-min freshness. |

---

## 9. Cross-repo TODO: `operator-shell/src/operator_loop.ts:1023`

The TypeScript peer to write-guardrail #1 lives in the external
`operator-shell` repository. In this workflow the Python server-side drop
of the `context` field ships (`aletheia/server/routes/operator.py:512-524`
in `_seed_branch_workspace`). Until the TS PR lands, the TS writer keeps
producing bloated `context` payloads on every re-entered session and the
Python server strips them at persistence.

Action for the external repo:

- File: `operator-shell/src/operator_loop.ts`, line 1023.
- Change the `createMessage` body from
  `{text, profile, session_kind, sections, context: snapshot.context}` to
  `{text, profile, session_kind, sections}`.
- Ship as a coordinated PR paired with the Python-side deploy.

Coordinated PR status: in flight; tracked separately from Phase 3.

---

## 10. Frontend follow-up: `AgentActivity.tsx` UNION

Agent 8 removed the `agent_run_steps` double-write inside `log_tool`
(write-guardrail #2). The server endpoint `/api/agent-runs/{run_id}/steps`
now UNIONs `agent_run_steps` and `agent_tool_calls` at read time
(`aletheia/server/routes/data.py:238-246`).

The frontend `AgentActivity.tsx` component still reads only
`/api/agent-runs/{id}/steps` today. Because the endpoint contract change is
a superset (steps + tool_calls appear as one merged list), the UI works
unchanged. Future frontend work should:

- Confirm the merged list renders correctly for legacy runs (pre-guardrail)
  where `agent_run_steps` contains duplicate tool-call rows.
- Optionally show tool-call rows and reasoning-step rows differently once
  the transition window has passed (~ 14d, when older duplicated
  `agent_run_steps` rows have been pruned).

Not in scope for Phase 3.

---

## 10.5. Deferred write-side guardrails

Phase 3 deployed the top-priority write guardrails (Section 4) but did not
close every writer path considered during the workflow. A running list of
unfinished items, with rationale, expected impact, and code touchpoints,
lives in the archived companion
[`RETENTION_WRITE_GUARDRAILS_TODO_2026-07-17.md`](RETENTION_WRITE_GUARDRAILS_TODO_2026-07-17.md). Read
that file before starting a "second-round retention" push so previously
analyzed context is not re-discovered.

Highest-leverage deferred items (see companion doc for details):

- **P0-1** `agent_shell/sessions/` strategy-stem file writes — the main
  reason `agent_shell/` sits at ~246 MiB instead of the 50 MiB synthesis
  target. Requires a new session_kind gate in `append_session_log`.
- **P0-2** Cross-repo `operator-shell/src/operator_loop.ts:1023` context
  sender — server strips the payload, TS side still generates it.
- **P0-3** `filter_decisions` scanner_weights_quota — flip writer to
  batch form (read-side dual-decode already shipped in PR #121).
- **P0-4** `market_ohlcv` legacy_unknown writer — one-time delete done;
  writer path still needs sealing to prevent regrowth.

---

## 11. Legacy source tree removal (2026-08-16)

The runtime-home migration (`.runtime-home-migration.json.completed_at =
2026-07-17T11:25:17`) preserved `/Users/yixian/Projects/aletheia/data/`
(~3 GiB) as a rollback source. Per user decision Q10 that path is NOT
touched by Phase 3.

Schedule a session-scope `CronCreate` job for **2026-08-16** that:

1. Verifies runtime-home stability — `aletheia gateway status` running,
   `aletheia doctor` fully green, `PRAGMA integrity_check` on `app.db`
   returns `ok`, `~/.aletheia/.runtime-home-migration.json` unchanged.
2. Prompts the user before running
   `rm -rf /Users/yixian/Projects/aletheia/data/`.
3. Records the removal (or the user's decline) in a durable audit event.

The scheduling lives outside code (session-scope, not durable). See
[claude scheduling section](#) for how to instantiate.

---
