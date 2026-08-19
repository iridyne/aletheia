# Observation Lab v1 — Consolidated Design

> Status: authoritative v1 design as of 2026-08-19. Consolidates Clusters 1-5
> (PRs #1083 / #1111 / #1116 / #1117 / #1118 / #1119 / #1120) into one
> reviewable shape before Fedora deployment.
> Companion docs:
> - [observation_lab_contract.md](observation_lab_contract.md) — owner-review contract (what Lab is / isn't).
> - [observation_lab_backup_contract.md](observation_lab_backup_contract.md) — joint app.db + lab.db + manifest consistency rule.
> - [../../research/audits/UFC_OWNERSHIP_MAP_2026-08-18.md](../../research/audits/UFC_OWNERSHIP_MAP_2026-08-18.md) — Packet 0 audit map.

This document is what to read before deploying. If code contradicts it, the code is wrong; fix the code first, then rewrite this doc.

## 1. Executive shape

```
        every 6h (default) via APScheduler filter_controller_observe
                              │
                              ▼
              filter_controller.run_controller_observe()
                              │
        ┌─────────────────────┼────────────────────┐
        │                     │                    │
  build+register        run_lab_windows()     (existing recommendation persist)
  filter replays              │                    │
                              ▼
       ┌────────────┬─────────────────┬──────────────────┐
       │ filter_    │ ufc_shadow_     │ (Packet 2:       │
       │  shadow_   │  equivalence    │  canonical_ufc,  │
       │  evaluate  │                 │  canary, …)      │
       └─────┬──────┴────────┬────────┴─────────┬────────┘
             │               │                  │
        writes lab.db   writes lab.db      writes lab.db
        + evals in      + equivalence      + lab_exposures
        app.db          history            + joint checkpoint
```

Two SQLite files, one process, one 6h heartbeat. Everything else is layered on that.

## 2. Files & data model

### 2.1 File layout

| Path | Owner | RW | Purpose |
|---|---|---|---|
| `~/.aletheia/app.db` | Production canonical | Lab reads (mostly RW; RO helper exists for auditor paths) | Filter decisions, filter outcomes, learning exposures/verdicts, filter-shadow experiments |
| `~/.aletheia/observation-lab/lab.db` | Observation Lab | Lab reads+writes; production never touches | Frozen units, UFC equivalence, execution manifests, lab exposures, joint checkpoints |
| `~/.aletheia/config.yaml` | Operator | Read only | `controller.observe_interval_hours` gates Lab cadence |

Data dir resolution: `aletheia.config.loader.get_data_dir()` returns `~/.aletheia` by default; `ALETHEIA_DATA_DIR` env overrides; preview mode uses `~/.aletheia/instances/dev-preview/…`. Lab honors this — it never hard-codes `~/.aletheia`.

### 2.2 lab.db schema (Cluster 1-5 combined)

Table | Purpose | Append-only? | Written by | Read by
---|---|---|---|---
`filter_shadow_frozen_units` | First-exposure snapshot for ranker-confidence filter POC | via UNIQUE + INSERT OR IGNORE | `capture_frozen_units_for` | filter-shadow evaluator on rebuild
`ufc_shadow_equivalence` | Per-cycle equivalence proof of UFC evaluator | yes | `run_ufc_shadow_equivalence_pass` | operator status; audit
`execution_manifests` | Frozen treatment contracts Main Runtime consumes | yes (UNIQUE on `(observation_id, generation)` + on `manifest_hash`) | `publish_manifest` | Main Runtime resolver; conflict detector; checkpoint fingerprint
`lab_exposures` | Typed exposure fact (Cluster 4 PR #1119) — one row per eligible unit | yes (SQL trigger enforces) | `record_lab_exposure` (post-Packet-2 caller) | audit; conflict analysis
`joint_checkpoints` | (Cluster 5 PR #1120) fingerprint chain for app+lab+manifest consistency | idempotent-on-unchanged | `save_joint_checkpoint` | `verify_joint_checkpoint`; operator status

### 2.3 app.db surface Lab touches

**Lab READS** these production tables (all through `aletheia.storage.sqlite.get_db` — the same connection production uses):

- `filter_decisions` (via `FIRST_EXPOSURE_CTE_SQL` in `analytics/filter_evidence.py`)
- `filter_candidate_outcomes`
- `filter_shadow_experiments` / `filter_shadow_evaluations`
- `learning_hypotheses` / `learning_experiments` / `learning_exposures` / `learning_policy_epochs` / `learning_contribution_verdicts`
- `aletheia_schema_migrations` (checkpoint reads migration head)

**Lab WRITES** these production tables:

- `filter_shadow_experiments` — via `register_replay_ready_experiments`. Hard SQL `CHECK (production_mutation_allowed = 0)` prevents behavior mutation. Pre-existing before #1072.
- `filter_shadow_evaluations` — append-only evaluation history. Pre-existing.
- Migration adds one column: `filter_shadow_experiments.adapter_identity_hash` (PR #1083). Additive-only.

Lab **never touches** `learning_*` writes, `orders`, `fills`, `positions`, `proposals`, `risk_*`, `filter_decisions`, `filter_candidate_outcomes`, or any exchange/credential path.

### 2.4 Physical read-only helper

`aletheia.storage.sqlite.open_readonly_snapshot(path=None)` opens the given SQLite file with `mode=ro` URI + `PRAGMA query_only=ON`. Verified in tests to reject writes at the driver layer.

**Current usage**: architecture assertion tests only. Lab's own read paths use `get_db()` (a normal RW handle) because Lab and Main Runtime share the process. When Lab is out-of-process (post v1), swap those callsites to `open_readonly_snapshot` — the swap is mechanical, one grep. This is a deliberate deferred hardening, not an oversight.

## 3. Runtime shape

### 3.1 Cadence

- APScheduler job `filter_controller_observe` (registered in `aletheia/agent_runtime/scheduler.py:1378`) fires every `controller.observe_interval_hours` hours (config default `6`).
- Job executes `run_controller_observe(min_samples=controller.min_samples_gate)`.
- `run_controller_observe` does three things:
  1. Build + register filter replays (unchanged pre-#1072 behavior).
  2. Call `run_lab_windows()` — iterates every registered Observation Window under one bounded, fair, exception-isolated pass.
  3. Return summary dict with per-window results + convenience projections.
- `_is_fresh_prior_run` freshness gate: if last run was within `interval / 2`, skip. Prevents scheduler-misfire duplication.

**Ownership contract** (Issue #1072 first-patch update — supersedes the earlier "no systemd timer" claim):

- ``config.observation.execution_owner`` selects one of ``main | worker``. There is no ``both`` state.
- ``main`` (default, backward compatible) — the APScheduler job above owns and executes the cycle exactly as before.
- ``worker`` — a sibling ``/home/admin/aletheia-observer`` checkout owns the cycle via its own systemd user timer (see `deploy/systemd/`) invoking ``aletheia observe run``. Main APScheduler MUST NOT register the job in that mode.

Enforced at three seams inside `aletheia/agent_runtime/scheduler.py`:

1. **Startup** (~L1376) — `if is_main_owner()` guards the initial `add_job` call. Worker mode logs "skipped registration" and moves on.
2. **Reconcile** (`reconcile_filter_controller`) — Worker mode always strips any existing job, refuses to add one, and returns True. Settings reconciliation cannot re-add it while Worker owns.
3. **Runner** (`_run_filter_controller_observe`) — Main-side entry point calls `require_main_or_absent`; a stray manual invocation while Worker owns is recorded as `worker_owns_cycle` and skipped.

`aletheia observe run` is the Worker's one-shot entry point. It:
- Calls `require_worker_owner` — exits code 2 if config says `main`.
- Acquires a cross-process file lock at `~/.aletheia/observation-lab/worker.lock` via `fcntl.flock`. Second concurrent pass exits code 3, does not wait.
- Records worker SHA + acquired timestamp + PID into the lock file for audit.
- Invokes the exact same `run_controller_observe → run_lab_windows` chain — no Worker-specific reimplementation.
- Does NOT start Gateway, Frontdoor, Main scheduler, Operator shell, Risk, Execution, exchange adapters, or trading authority. Systemd unit explicitly clears exchange credential env vars.

### 3.2 The runner (Cluster 2)

`aletheia.observation_lab.runner.run_lab_windows(*, max_windows=None)`:

- Iterates `registered_windows()` alphabetically (deterministic fair ordering — anti-starvation).
- Each window is a `LabWindow(name, kind, fn)` descriptor. `fn` returns a summary dict.
- Per-window `try/except`: raise → `results[name] = {"status": "error", "error": …}`, runner continues.
- Returns `{"windows_run": N, "windows_errored": K, "results": {name: summary}}`.

Default windows registered at module import:
- `filter_shadow_evaluate` — Cluster 1/2 (filter-shadow capture + evaluate).
- `ufc_shadow_equivalence` — Cluster 1/2 (UFC evaluator re-run + compare).

New windows plug in via `register_window(LabWindow(…))`.

### 3.3 Failure isolation

Two isolation layers:

1. **Inside runner**: one window's raise never leaks to another; results dict carries typed error.
2. **Outside runner**: `run_controller_observe` wraps the whole `run_lab_windows()` call in its own `try/except`. Even a catastrophic runner failure (e.g. lab.db corrupt, module import error) does not stop `filter_controller_observe`'s replay registration or the scheduler from ticking.

Contract: **no Lab failure ever prevents ordinary Paper operation**. `learning_exposures` writes, Risk gates, order placement, and the existing `filter_controller` recommendations remain unaffected.

## 4. Boundary invariants

Every deployed release MUST satisfy:

| Invariant | Where enforced |
|---|---|
| `filter_shadow_experiments.production_mutation_allowed = 0` — CHECK constraint | SQLite schema (migrations.py) |
| `lab_exposures` never UPDATEs / DELETEs | SQL trigger `trg_lab_exposures_no_update` / `_no_delete` |
| `execution_manifests` immutable per `(observation_id, generation)` | UNIQUE constraint + `ManifestGenerationConflict` in `publish_manifest` |
| `execution_manifests.manifest_hash` UNIQUE globally | UNIQUE constraint |
| Lab has no import path to Risk / Execution / Gateway / credentials | Verified in `aletheia/observation_lab/` module imports; add architecture test if drift becomes a concern |
| Adapter causal identity vector, not `code_commit`, decides invalidation | `filter_experiments._invalidation_for` + `_ADAPTER_CAUSAL_IDENTITY` |
| Lab's own state root is under `get_data_dir()` — never hard-coded | `observation_lab.db.lab_db_path()` |

## 5. Design gaps to fix BEFORE Packet 2 deployment

Enumerated so nothing surprises us at deployment. Each has a suggested fix and a "must fix before X" gate.

### G1. `observation_id` uses `experiment_row.get("policy_epoch")` which does not exist on `LearningExperiment` (returns None → fallback `"unknown"`)

- **Impact**: UFC shadow-equivalence records currently show `observation_id=ufc:<exp_id>:unknown:<gen>`. Semantically wrong: two policy-epoch rotations for the same experiment_id collapse to the same observation_id.
- **Root cause**: `policy_epoch` lives on `LearningExposure.concurrent_policy_epoch` / `LearningContributionVerdict.policy_epoch`, not on the experiment row.
- **Fix**: `ufc_shadow._observation_id` should call `aletheia.learning.experiments.current_policy_epoch(path_id="universe_family_calibration")` OR pull from the latest verdict row's `policy_epoch`. ~20 lines.
- **Gate**: fix before Packet 2. Before that, `unknown` is harmless (equivalence records still accumulate correctly; only the id label is coarse).

### G2. No CLI to publish an `ExecutionManifest`

- **Impact**: When Owner admits a new UFC generation (Packet 2 trigger), there's no operator command to publish the corresponding manifest to `lab.db`.
- **Fix**: add `aletheia observe publish-manifest <spec.yaml>` — YAML schema mirrors `build_manifest` kwargs. Handles the `ManifestGenerationConflict` case with a clear error.
- **Gate**: fix before Packet 2. Not needed for v1 shadow-equivalence.

### G3. `verify_joint_checkpoint` primitive exists but nothing calls it

- **Impact**: `joint_checkpoints` table stays empty until we add the caller. Cluster 5's guarantee is currently inactive.
- **Fix**: Runner's first action per pass is `save_joint_checkpoint(compute_joint_checkpoint())`. On `verify_joint_checkpoint()` mismatch, log a warning and (Packet 2+) mark affected windows invalidated.
- **Gate**: fix before Fedora deployment. Otherwise Cluster 5 is dead code.

### G4. Observe CLI does not surface Cluster 3-5 state

- **Impact**: Operator can't see manifests, exposures, or checkpoint chain from CLI. Existing `aletheia observe list / status / sync / evaluate` only shows filter shadow experiments.
- **Fix**: extend `status` to also show latest N UFC equivalence records, active manifest count, latest checkpoint verdict.
- **Gate**: fix before Fedora deployment. Without it, we deploy blind.

### G5. No end-to-end integration test

- **Impact**: All Cluster 1-5 tests are unit-level with heavy mocking. No test proves the full `run_controller_observe → run_lab_windows → all windows → lab.db writes → verify_joint_checkpoint` chain works against a real SQLite pair.
- **Fix**: One integration test that (a) sets up a temp `~/.aletheia`, (b) seeds a fake UFC experiment + verdict + exposures in app.db, (c) calls `run_controller_observe`, (d) asserts lab.db has expected rows.
- **Gate**: fix before Fedora deployment. Unit tests miss inter-file / inter-connection issues.

## 6. Deferred to Packet 2+ (deliberately NOT in v1)

Not gaps — deliberate boundaries. Deployment does not require these.

- Main Runtime `apply_numeric_treatment` rewiring to consume manifests — needs an admitted Lab-native UFC generation; Owner review §1 forbids doing this to #447 mid-flight.
- Deleting duplicated UFC implementation in `learning/experiments.py` — Packet 3.
- Additional adapters (`family_ttl`, `observer_pre_filter`) in `_ADAPTER_CAUSAL_IDENTITY` — Phase 2.
- Second, out-of-process Lab runner — v2.
- Automatic backup/restore machinery — out of scope.
- Web dashboard — new #1072 §"Non-goals" explicitly forbids.

## 7. Failure modes + responses

| Failure | Detection | Response |
|---|---|---|
| lab.db missing / not created | `get_lab_db()` calls `mkdir(parents=True, exist_ok=True)` and creates schema idempotently | Silent recover; first-boot works. |
| lab.db corrupt / can't open | SQLite raises → caught by runner's per-window try/except | That window's result = error; other windows + main runtime continue. Operator sees `windows_errored > 0` in observe summary. Manual: delete lab.db and let next pass recreate; frozen units and equivalence history lost — reintegrate on next accumulation. |
| app.db schema drift | `verify_joint_checkpoint()` returns `ok=False` with typed reason | Once G3 wired: log warning + (post-Packet-2) invalidate affected windows. Operator inspects `latest_joint_checkpoint()` + fresh compute. |
| Two Fedora processes racing on lab.db | SQLite WAL + application-level `acquire_write_lock` in app.db path; lab.db uses `isolation_level=None` autocommit + 5s timeout. | Second writer waits up to 5s. Beyond that: exception, caught by runner. Not expected in current single-process Fedora deployment. |
| Learning evaluator raises during equivalence pass | Caught in `ufc_shadow.py::run_ufc_shadow_equivalence_pass` per-experiment; records `evaluator_error` mismatch row | Never propagates. Operator sees mismatch record with `notes` naming the exception. |
| Filter-shadow evaluator raises | Caught by runner window boundary | `filter_shadow_evaluate` result = error; UFC equivalence + checkpoint still run. |
| Config `observe_interval_hours` set to 0 or missing | `max(1, ...)` clamps to 1h in scheduler registration | Never disabled entirely; to disable, set `controller.shadow_enabled = False` (Cluster 1-5 all sit behind that gate via scheduler wiring). |
| Adapter identity vector bumped by mistake | Every collecting cohort in that adapter fails closed with `adapter_causal_identity_changed` | Reason: this is exactly what should happen. Recovery: bump back if unintended; historical evaluations preserve state. |
| Manifest publish for same generation with different content | `ManifestGenerationConflict` raised in `publish_manifest` | Caller must bump `generation` counter. Old manifest continues effective until its `effective_until`. |

## 8. Fedora deployment plan

Order matters. Do not shortcut.

### 8.1 Pre-flight (before merging any of #1119 / #1120)

- [ ] Confirm design gaps G1-G5 either fixed or deliberately accepted.
- [ ] Verify all open PRs pass CI on their exact heads.
- [ ] Read this doc against the merged code — inconsistencies mean code is wrong.

### 8.2 Merge order

1. #1119 (Cluster 4) — safe: additive schema + typed exposure primitive; no code calls `record_lab_exposure` yet.
2. #1120 (Cluster 5) — safe: additive schema + checkpoint primitive; no code calls `save_joint_checkpoint` yet unless G3 lands.
3. Small G3+G4 PR wiring the runner to save/verify checkpoints and surface state via `observe status`.
4. Small G5 PR adding one integration test.

Between each merge, `git pull master` on Fedora — the existing deploy pipeline handles the rest.

### 8.3 First Fedora boot after merge

- Confirm `~/.aletheia/observation-lab/lab.db` created on first `filter_controller_observe` fire.
- Confirm `sqlite3 ~/.aletheia/observation-lab/lab.db '.schema'` shows all five tables.
- After first cycle: `sqlite3 ~/.aletheia/observation-lab/lab.db 'select count(*), max(checked_at) from ufc_shadow_equivalence'` should show ≥1 row.

### 8.4 Steady-state observability

Every 6h (or `observe_interval_hours` value):

- `sqlite3 ~/.aletheia/observation-lab/lab.db 'select checked_at, matched, mismatch_field from ufc_shadow_equivalence order by checked_at desc limit 5'` — recent UFC equivalence.
- `sqlite3 ~/.aletheia/observation-lab/lab.db 'select recorded_at, app_migration_count, active_manifest_count from joint_checkpoints order by recorded_at desc limit 5'` — checkpoint drift.
- `journalctl` / gateway logs for `filter_controller observe` lines — those now include `ufc_equivalence=…` and `lab_windows=…` fields.

### 8.5 Rollback

Everything in this design is additive:
- Lab tables live in a separate file → delete `~/.aletheia/observation-lab/` to reset Lab state.
- `filter_shadow_experiments.adapter_identity_hash` column is nullable and only consulted by ranker-confidence adapter → old code ignores it.
- Adapter identity checks fall back to legacy `_production_version_changed` for other adapters.

To fully disable: set `controller.shadow_enabled = False` in `config.yaml`. Scheduler stops firing `filter_controller_observe`, Lab quiesces.

### 8.6 Worker split (Issue #1072 first-patch update)

Independent Worker execution ownership is the ONLY pre-release implementation objective; the changes below supersede §8.2 / §8.3 for a Worker-mode deploy.

**One-time Fedora bring-up:**

```sh
# Sibling checkout, deliberately pinned to a different SHA than Main.
git -C /home/admin clone git@github.com:iridyne/aletheia.git aletheia-observer
cd /home/admin/aletheia-observer && uv sync

# Flip ownership in the SHARED data-root config (~/.aletheia/config.yaml):
#   observation:
#     execution_owner: worker

# Restart Main so the ownership gate takes effect there:
systemctl --user restart aletheia-gateway  # or your usual restart path

# Install & enable the Worker timer:
mkdir -p ~/.config/systemd/user
cp deploy/systemd/aletheia-observer.service ~/.config/systemd/user/
cp deploy/systemd/aletheia-observer.timer   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now aletheia-observer.timer
```

**Acceptance for closing #1072** (must all hold):

| Check | Command |
|---|---|
| Sibling checkout exists | `test -d /home/admin/aletheia-observer && echo ok` |
| Main SHA visible | `cd /home/admin/aletheia && git rev-parse HEAD` |
| Worker SHA visible | `cd /home/admin/aletheia-observer && git rev-parse HEAD` |
| Main Scheduler has NO observation job | `curl -s http://127.0.0.1:8787/api/scheduler/jobs \| grep -c filter_controller_observe` → `0` |
| Worker systemd timer succeeded | `systemctl --user status aletheia-observer.timer` |
| ≥2 windows in one Worker pass | `journalctl --user -u aletheia-observer.service -n 20 \| grep 'windows_run'` → `windows_run=2` |
| Main can restart/update while Worker pinned | run `aletheia update` on Main; verify Worker SHA unchanged |
| Existing Observation identities survive | `sqlite3 ~/.aletheia/observation-lab/lab.db 'select count(*) from ufc_shadow_equivalence'` non-decreasing |
| No duplicate cycle | `select count(*) from joint_checkpoints where recorded_at > date("now","-1 hour")` ≤ 1 |
| Shared canonical `app.db` used | Same `~/.aletheia/app.db` inode from Main + Worker |
| No Risk/Execution/config mutation | `journalctl --user -u aletheia-observer.service` shows no risk/execution imports; systemd unit clears exchange creds |

**Rollback path** (no data migration required):

```sh
systemctl --user disable --now aletheia-observer.timer
# Edit ~/.aletheia/config.yaml: observation.execution_owner: main
# Restart Main Gateway — reconcile re-adds filter_controller_observe.
```

`app.db` and `lab.db` remain untouched during rollback. All Observation identities/history survive both the split and the rollback.

## 9. Operator quick reference

**Is Lab running?**
- `journalctl -u aletheia --since='6 hours ago' | grep 'filter_controller observe'` — should see one line per interval.
- Check `~/.aletheia/observation-lab/lab.db` mtime — should be newer than last observe interval.

**Is UFC equivalence still matching?**
- `sqlite3 ~/.aletheia/observation-lab/lab.db 'select matched, count(*) from ufc_shadow_equivalence where checked_at > date("now", "-1 day") group by matched'` — `matched=1` should dominate; `matched=0` rows warrant investigation via `mismatch_field`.

**Did anything drift?**
- `sqlite3 ~/.aletheia/observation-lab/lab.db 'select joint_fingerprint from joint_checkpoints order by recorded_at desc limit 2'` — if the two rows differ, that's the moment something moved.

**How do I temporarily disable Lab?**
- Set `controller.shadow_enabled = False` in `~/.aletheia/config.yaml` and restart or wait for the next scheduler reconcile.

## 10. Post-deployment gates (per Hindsight repo policy)

Complete-fix definition for #1072 requires (in order):
1. ✅ Scoped changes committed and pushed (Clusters 0-5 done).
2. ✅ Review-ready PRs target master.
3. ✅ Exact-head CI passes.
4. ⏳ All review threads resolved (as they come in).
5. ⏳ PRs observed `merged=true` (currently #1119 / #1120 open).
6. ❌ Linked issue #1072 observed closed. **Blocked on Packets 2-3** — needs Owner admission + Fedora runtime evidence.
7. ⏳ Fedora deployment confirmed:
   - `boot_commit == current_commit` — deploy pipeline handles.
   - `/api/health` healthy — deploy pipeline handles.
   - `~/.aletheia/observation-lab/lab.db` has non-zero rows across all five tables after 24h.

Gates 6 requires external events (Owner action, natural UFC lifecycle progression) that this session cannot force. Gate 7 needs deployment.
