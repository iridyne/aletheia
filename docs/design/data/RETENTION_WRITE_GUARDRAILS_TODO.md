# Retention — Deferred Write-Side Guardrails (Phase 3 follow-up)

> Companion to `RETENTION_POLICY.md`. Captures every write-side guardrail
> considered during the Phase 1-3 workflow but **not** deployed in PR #121.
> Purpose: unblock a future pass (human or agent) that can pick up any item
> without re-doing the discovery work.
>
> Last updated: 2026-07-17. Owner rotation: whoever picks up an item, please
> flip its status from `TODO` → `IN_PROGRESS` → `DONE` with the landing PR link.

## Status legend

- **TODO** — analyzed but not scheduled.
- **IN_PROGRESS** — someone owns it, PR open.
- **DONE** — landed; link the PR.
- **DROPPED** — decided against with a documented reason.

## Priority

Ranked by long-term MB/day saved AND risk-of-regrowth. High-priority items
here address the "top 90% guardrails not yet deployed" identified in the
Phase 3 wrap-up review. Lower items are principled cleanups (< 1 MB/day
each) that only matter cumulatively.

---

## P0 — Highest leverage (unclosed re-growth vector)

### P0-1  agent_shell/sessions/ strategy-stem file writes

**Status:** TODO

**Problem.** `attachSessionContext` (operator-shell TS) creates one
`agent_shell/sessions/<session-id>.jsonl` per session. Strategy sessions
alone run at ~1000/day. The Phase 3 DB-JOIN prune (14d window) leaves
~14,000 files always present. The result: `agent_shell/` plateaus at
~246 MiB even after Phase 4 backfill, well above the 50 MiB target from
the workflow synthesis.

**Rationale for deferral.** Phase 3 spec (Agent 4) added
`SKIP_TASK_NOTE_TYPES` for `agent_shell/tasks/*.md` but did not touch
`agent_shell/sessions/*.jsonl`. The session log is more replay-critical
than task notes (it holds the full conversation trace), so a blanket skip
risks losing debug capability.

**Proposed approach.**
- In `aletheia/agent_shell/file_artifacts.py:append_session_log`, add a
  `session_kind` gate. For `session_kind IN ('strategy_session',
  'observe_only_session')`: write to `~/.aletheia/scratch/session_ephemeral/
  <session-id>.jsonl` instead, and prune that directory at 3 days.
- Alternative: keep writing to the same path but shorten
  `session_logs_days` from 14 to 3 for `kind IN ('strategy_session',
  'observe_only_session')` — needs a per-kind retention column in
  `agent_shell_sessions` OR an in-code lookup table.

**Expected impact.** Reduces `agent_shell/sessions/` from ~184 MiB to
~30 MiB (only review/diagnostic/pi-session/dreaming stems survive the
14d window, and those are ~4,000 files at ~7 KiB each).

**Risks.**
- Loses ability to replay strategy-session LLM output for cases older
  than 3 days. Post-trade review depends on `agent_shell_events`
  (already 14d retention) and DB message rows (already 14d for
  non-context), not on the JSONL file, so debug capability degrades
  gracefully.
- Must NOT touch `operator-main.jsonl` — already exempted via
  `_PERSISTENT_SESSION_STEMS`.

**Code touchpoints.**
- `aletheia/agent_shell/file_artifacts.py:append_session_log` (~line 250)
- `aletheia/agent_shell/sessions.py:persist_session` (kind is captured)
- `aletheia/config/schema.py` — new `session_logs_strategy_days` field
- New test: `tests/test_write_guardrails.py::test_strategy_session_short_retention`

---

### P0-2  Cross-repo: operator-shell `content.context` sender

**Status:** TODO (external repo)

**Problem.** Server-side (`aletheia/server/routes/operator.py:512-524`)
drops `snapshot.context` before persisting. But
`operator-shell/src/operator_loop.ts:1023-1025` still sends a ~7.7 KiB
JSON payload per session, wasting network bandwidth and CPU on both ends
to serialize/parse a field that will be discarded.

**Rationale for deferral.** `operator-shell/` is a separately-versioned
repo (`github.com:iridite/operator-shell`). This PR could not coordinate
a synchronized release across both without pausing feature work.

**Proposed approach.**
- In `operator-shell/src/operator_loop.ts:attachSessionContext`, change
  the `createMessage` body from
  `{text, profile, session_kind, sections, context: snapshot.context}` to
  `{text, profile, session_kind, sections}`.
- Also drop the `context` key from the type union that
  `MessagePayload` accepts.
- Verify by reading `aletheia/server/routes/operator.py:512-524` — the
  server already ignores the field, so removing it client-side is a
  drop-in.

**Expected impact.** Per-message payload drops from ~8 KiB to ~500 B
over the wire. At 1000 sessions/day this saves ~7.5 MiB/day of network
+ CPU serialization. DB impact is already zero (server strips).

**Coordination.**
- Land the TS PR first (breaks nothing — field is optional).
- Land a follow-up Python PR that raises a warning if `context` key
  appears in the request body (surfaces stragglers).
- Delete the retro-strip logic in
  `aletheia/maintenance/data_retention_backfill.py:_retro_strip_message_context`
  once the field has been absent for 3+ days.

**Code touchpoints (external).**
- `operator-shell/src/operator_loop.ts:1023-1025`
- `operator-shell/src/types/message.ts` (or wherever `MessagePayload` lives)

---

### P0-3  `filter_decisions` scanner_weights_quota — flip writer

**Status:** PARTIAL (Phase 3 shipped read-side dual-decode only)

**Problem.** Per Phase 3 spec Agent 8: "Ship as read-side dual-decode
FIRST (accept both formats), then flip writer." The read-side landed in
PR #121. The writer at `aletheia/analytics/causal_controls.py:260-312`
still writes one `filter_decisions` row per allocated symbol. Batch
form via `record_filter_decision_batch` would collapse 11,105 rows
(24% of daily filter_decisions volume) into a handful of compressed
batches.

**Rationale for deferral.** Cutover safety — running dual-decode for
2-3 days confirms the read path is safe before we lose the option to
roll back.

**Proposed approach.**
- Wait 7 days after PR #121 merges (retention nightly job runs → any
  read-side issues surface).
- Then flip the writer in `causal_controls.py:_record_universe_
  allocation_decisions`. Compute the batch summary (fields + rows
  matrix) once, call `record_filter_decision_batch` with a single row,
  drop the per-symbol `record_filter_decision` loop.
- Regression check: `compute_filter_feedback` and
  `guard_effectiveness_stats` still return the same aggregates.

**Expected impact.** ~1.2 MiB/day steady-state saved on
`filter_decisions`, moving 45d plateau from ~200 MiB to ~150 MiB.

**Risks.**
- Batch format is content-hashed for dedup — malformed rows in the
  batch fail-close the entire batch. Handle by falling back to
  per-symbol writes on batch-encoding failure.

**Code touchpoints.**
- `aletheia/analytics/causal_controls.py:_record_universe_allocation_decisions`
- Read-side (already lands in PR #121):
  `aletheia/analytics/filter_evidence.py:_load_decision_rows`

---

### P0-4  `market_ohlcv` legacy_unknown writer path — hunt and seal

**Status:** TODO

**Problem.** Phase 4 backfill hard-deleted 27,554 rows where
`exchange='legacy_unknown'` (a compatibility shim from a dead code
path). If some import path still calls `upsert_ohlcv_candles` without
an explicit `exchange` argument, `_OHLCV_WINDOW_DEFAULT` will re-open
the legacy channel and the orphan rows return.

**Rationale for deferral.** The one-time delete gave immediate relief;
sealing the writer requires a codebase audit that was not in Phase 3's
critical path.

**Proposed approach.**
1. `grep -rn "upsert_ohlcv_candles\|market_ohlcv" aletheia/ | grep -v
   "exchange="` — list every writer that omits the exchange argument.
2. For each caller: verify the exchange is threaded through from the
   provider name (should be `okx` / `binance` / `bybit` / `bitget`).
3. In `aletheia/storage/repositories.py:upsert_ohlcv_candles`, remove
   the `exchange or 'legacy_unknown'` fallback (line 3140). Raise
   `ValueError('exchange required')` instead.
4. Add a fitness test in `tests/test_architecture_fitness.py`:
   `assert 'legacy_unknown' not in _all_referenced_exchange_names`.

**Expected impact.** Zero immediate MB (all rows already gone). Prevents
future regrowth (~1 MiB/week if a stray writer is still active).

**Risks.**
- Some historical Migration or backfill code may still use the legacy
  path. Grep first, migrate any real callers to a real exchange name
  before removing the fallback.

**Code touchpoints.**
- `aletheia/storage/repositories.py:upsert_ohlcv_candles` (line ~3123)
- Every caller found by the grep above.

---

## P1 — Principled defense-in-depth (< 1 MB/day each)

### P1-1  `portfolio_snapshots` skip-if-unchanged

**Status:** TODO

**Problem.** `record_portfolio_snapshot` in
`aletheia/storage/repositories.py` writes on every scheduler tick even
when balance / positions have not changed. Retention is 90d; steady
state ~5 MB. Portfolio can stay identical for hours during quiet
markets.

**Proposed approach.** Fetch latest_portfolio_snapshot; skip INSERT if
`equity`, `cash`, `positions_json`, `active_symbols` all match. Keep
an hourly heartbeat floor so `doctor.data.freshness` probes stay green.

**Expected impact.** ~0.3 MB/day saved.

**Code touchpoints.**
- `aletheia/storage/repositories.py:record_portfolio_snapshot`
- New guardrail test in `tests/test_write_guardrails.py`.

---

### P1-2  `macro_snapshots` + `onchain_snapshots` skip-if-unchanged

**Status:** TODO

**Problem.** Fear-and-greed index moves slowly (daily). Onchain flow
metrics update at exchange cadence but often report zero deltas.
Neither writer dedups.

**Proposed approach.** Same shape as P1-1: compare newest row to
prospective insert; skip on identical semantic payload with hourly
floor.

**Expected impact.** ~0.2 MB/day combined.

**Code touchpoints.**
- `aletheia/storage/repositories.py:record_macro_snapshot`
- `aletheia/storage/repositories.py:record_onchain_snapshot`

---

### P1-3  `prediction_probability_snapshots` skip-if-unchanged

**Status:** TODO

**Problem.** Prediction market probabilities update per poll interval;
if source hasn't refreshed, the snapshot is byte-identical to the
prior row. 90d prune bounds size but daily inserts are still ~500 rows
that could collapse.

**Proposed approach.** Skip INSERT if `probability_yes`, `probability_no`,
`volume_24h_usd` all identical to newest row for the same claim_id
within a 4h window.

**Expected impact.** ~0.15 MB/day saved.

**Code touchpoints.**
- `aletheia/storage/repositories.py:record_prediction_probability_snapshot`

---

### P1-4  `serenity_evidence` content_hash dedup

**Status:** TODO (synthesis guardrail #20)

**Problem.** Schema at `aletheia/storage/migrations.py:...` includes a
`content_hash` column, but writers at
`aletheia/agents/research_worker.py:689`,
`aletheia/agents/prediction_observer.py:434`, and
`aletheia/server/routes/data.py:390` insert unconditionally.

**Proposed approach.** Before INSERT, `SELECT 1 FROM serenity_evidence
WHERE theme_id=? AND content_hash=?`. Skip on match.

**Expected impact.** ~0.05 MB/day today (~1 MB total) but preventive
against future re-crawl storms.

**Code touchpoints.**
- All three writers above.
- Suggested one-line helper in `repositories.py:insert_serenity_evidence`
  so writers don't duplicate the dedup check.

---

### P1-5  `universe_scanner_runs` dedup on empty result

**Status:** TODO

**Problem.** Every scanner run appends a row even when the run produced
zero candidates. Small table (3,242 rows today) but grows every 10 min
forever.

**Proposed approach.** Skip INSERT if the run's `candidate_count == 0`
AND the previous run for the same scanner_family also had zero
candidates. Preserve a hourly heartbeat floor.

**Expected impact.** ~50 KB/day. Principled — the retention (30d, PR
#121) already bounds this, but dedup prevents wasted rows.

**Code touchpoints.**
- `aletheia/universe/service.py:_persist_scanner_run`

---

### P1-6  `agent_shell_events` payload trim for non-command types

**Status:** PARTIAL (Phase 3 trimmed `command_*` only)

**Problem.** `command_received` / `command_executed` / `command_rejected`
payloads were truncated to 200 chars. Other event types
(`session_compacted`, `agent_started`, `agent_stopped`,
`candidate_supply_snapshot`, `review_report_refreshed`) can carry
larger payloads.

**Rationale for deferral.** Selective — some payloads (e.g.,
`candidate_supply_snapshot.excluded_candidates_summary`) drive the
drought-recommendation logic at `operator_recommendations.py:246-260`
and must NOT be trimmed.

**Proposed approach.** Per event type, decide keep-full vs trim. Suggest:
- `session_compacted` — trim `history_summary` to 500 chars
- `review_report_refreshed` — trim `report_summary` to 500 chars
- `candidate_supply_snapshot` — leave alone (load-bearing)
- `agent_started` / `agent_stopped` — leave alone (small already)

**Expected impact.** ~200 KB/day, mainly from `review_report_refreshed`
(510 KB/peak-day today).

**Code touchpoints.**
- `aletheia/agent_shell/operator_store.py:record_event`
- Per-caller: check every `record_event(...)` invocation.

---

## P2 — Low priority, principle-only

### P2-1  `agent_run_steps.reasoning_summary` truncation

**Status:** TODO

**Problem.** `record_step` at `aletheia/agents/helpers.py` accepts
arbitrary `reasoning` strings. If an agent's chain-of-thought grows to
~5 KB per step, and a run has 50 steps, that's 250 KB per run × ~500
runs/day = 125 MB/day at worst.

**Proposed approach.** Truncate `reasoning` to 2000 chars + `len=N`
suffix at write time. The full chain-of-thought is available in the
LLM trace log if needed for post-mortem.

**Expected impact.** Highly variable. Today's average is ~200 B per
step so impact is minimal, but this is defense against future
verbose-reasoning agents.

**Code touchpoints.**
- `aletheia/agents/helpers.py:record_step`

---

### P2-2  `filter_decision_batches.candidate_facts` compression

**Status:** TODO

**Problem.** `candidate_facts` is stored as UTF-8 JSON. `record_filter_
decision_batch` at `filter_evidence.py:211-267` writes the raw text.
Compression (zlib) would reduce ~40% of the JSON size for typical
batches.

**Proposed approach.** Wrap `candidate_facts` in zlib compression at
write time; decompress at read time in `_load_batch_row`. Migration
path: add a `candidate_facts_compressed` boolean column, dual-decode
during transition.

**Expected impact.** ~0.3 MB/day saved.

**Risks.**
- Read-side must handle both compressed and uncompressed rows during
  transition.
- Compressed blobs are opaque to `SELECT candidate_facts FROM
  filter_decision_batches WHERE candidate_facts LIKE '%foo%'` queries.
  No known consumer does this; verify with grep before shipping.

**Code touchpoints.**
- `aletheia/analytics/filter_evidence.py:record_filter_decision_batch`
- `aletheia/analytics/filter_evidence.py:_load_batch_row`

---

### P2-3  Data-collector poll rate reduction

**Status:** TODO / DROPPED (see below)

**Problem.** `market_ohlcv` for 1m bars fetches every 60s. This is
inherent to 1m-timeframe data and can't be reduced without losing
resolution. Post-Phase-3 per-timeframe TTL caps steady-state size at
2 days for 1m data, which is the correct fix.

**Proposed approach.** DROPPED — the retention TTL already bounds the
storage cost. Reducing poll rate would hurt paper-trader signal
quality more than it helps DB size.

---

### P2-4  `runtime_events` — verify prune coverage

**Status:** TODO (audit-only)

**Problem.** `runtime_events` (54,342 rows / ~2 MB today per Phase 1
audit) was noted as "no prune" in the completeness critic report.
Post-Phase-3 verification: was this table wired into `_run_cleanup`
or is it still unbounded?

**Proposed approach.** `grep runtime_events aletheia/storage/repositories.py
aletheia/agent_runtime/scheduler.py` and confirm coverage.

**Expected impact.** ~0.1 MB/day (small growth vector).

---

## P3 — Watch-list (no action, monitor)

These are tables/paths that are currently well-behaved but could grow
if usage patterns shift.

- `~/.aletheia/knowledge/**` — 28 KB today, but daily_reviews and
  operator_memory subdirs get one file per day. Add a monitor at
  100 MB threshold.
- `~/.aletheia/exports/`, `~/.aletheia/gateway/`, `~/.aletheia/events/` —
  currently empty but pre-created for future writers. Any writer landing
  here escapes retention until a policy is added.
- `~/.aletheia/logs/*.jsonl` — per-family cap is 50 MiB × 100 generations
  = 5 GB per family. Total-directory cap not enforced. Adding a
  ~200 MiB total cap is straightforward but low priority.
- `~/.aletheia/observation/__pycache__/` — Python bytecode in a data
  dir. Move `short_chain_monitor.py` to source tree, or set
  `PYTHONDONTWRITEBYTECODE=1` for gateway subprocesses.

---

## Nightly monitoring — what to watch

The recurring `_run_cleanup` job now emits a `data_pruned` audit_event
with per-table row counts. Trend to watch (post-merge, 7-day rolling):

| Table | Expected daily prune count | Alarm if > |
|---|---|---|
| agent_shell_heartbeats | ~20,000 | 50,000 |
| agent_shell_messages | ~1,000-2,000 | 10,000 |
| agent_run_steps | ~8,000-15,000 | 30,000 |
| market_ohlcv | ~30,000 (1m tier rolling out) | 100,000 |
| universe_feature_snapshots | ~15,000-20,000 | 50,000 |
| funding_rate + open_interest | ~25,000 combined | 60,000 |

If any table's prune count grows > 2× its expected steady state, a
writer regression is likely. Investigate before shortening retention.

The recurring `PRAGMA incremental_vacuum(1000)` at the end of
`_run_cleanup` should keep `PRAGMA freelist_count` near zero. If
`freelist_count` grows above ~5,000 pages (~20 MB), the incremental
vacuum step is not keeping up and a full `VACUUM` maintenance window
should be scheduled.

---

## References

- `docs/design/data/RETENTION_POLICY.md` — the active retention policy.
- `docs/design/data/data_model.md` — schema-level retention notes per
  table.
- `docs/product/experiments/EXPERIMENT_PROTOCOL.md` — the
  `clean_window_cutoff` guard exists because verdict fingerprints in
  `contribution_runner.py:96` hash retained rows.
- PR #121 — first-pass write guardrails landing.
- Workflow synthesis JSON (Phase 1 output, ~68 KB) — the raw
  discovery data behind these items. If you need the full write/read
  path analysis for a specific table, re-run the discovery workflow
  scoped to that table.
