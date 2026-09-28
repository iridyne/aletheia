# Dual-Mainline Convergence Plan

**Date:** 2026-09-07
**Baseline commit:** `772666741` (`origin/master`, after PR #2945 and #2947)
**Scope:** converge four divergent branches, then eliminate dual mainlines repo-wide
**Mutation boundary:** this document only; no application code, database, config, provider, or Runtime was changed while producing it

## Execution status (2026-09-07)

Phases A, B (partial) and C are done and open as PRs. All three divergent
worktrees are converged and removed.

| PR | Content | Phase |
|---|---|---|
| #2948 | live order failure fails the entry plan | A2 — real defect |
| #2949 | filter outcome upsert targets its real unique key | A1 — hardening, see note |
| #2950 | filter specs stop pointing at deleted modules | A4 |
| #2951 | pi heartbeat daemon path no longer fails silently | B, salvaged |
| #2952 | private-history fake enforces account identity (12 failures) | B, salvaged |
| #2956 | four assertions that outlived their production code | B |
| #2953 | AgentRun lifecycle ownership + vocabulary | C (stack base) |
| #2954 | Order lifecycle transitions + vocabulary | C (on #2953) |
| #2955 | Position field ownership | C (on #2954) |

**#2953 → #2954 → #2955 is a stack and must merge in that order** — the migrations
are `v75`, `v76`, `v77` and `_validate_applied_migrations` requires applied migrations
to form a prefix.

Worktree disposition:

| Worktree / branch | Outcome |
|---|---|
| `fix/entry-convergence-2886` | Removed; PR #2889 closed — content already verbatim on master (verified 3 ways) |
| `debt-clear` (branch) | Deleted — it was #2945's pre-squash source branch |
| `fix/pr2945-regressions` | Salvaged into #2951/#2952/#2956; remainder kept as local `archive/pr2945-regressions-uncommitted` |
| `audit/backend-state-authority-parity-20260906` | Replayed as #2953/#2954/#2955; two commits withdrawn (see below) |

**Two of the thirteen commits were withdrawn rather than replayed**, for the same
reason in opposite directions:

- **`a4649bb9b` (UTA `SelectedAccountPort`) — withdrawn as redundant.** It centralizes a
  resolver for `uta_read_boundary_for`, and #2945 deleted that concept outright,
  together with the `ExchangeConfig.uta_read_boundaries` switch and the duplicate
  `portfolio/uta/` package. #2945 solved the same dual mainline more thoroughly. This
  is the pattern to copy: **a capability with two paths and a config flag choosing
  between them is not converged until the flag is gone too.**
- **`1d3911577` (observation `experiment_owner`) — withdrawn as premature**, kept on
  `archive/observation-experiment-owner`. Landing it now would create a *third*
  mechanism: #2945 added `_require_lab_authority` (`filter_experiments.py:76`) covering
  2 files, while `get_authority_source` is still called directly from 15. Adding a
  202-line owner on top of a partial helper, without migrating the other 13 consumers
  in the same move, is how a dual mainline becomes a triple one. It belongs in Phase D
  as one decision — pick the surviving mechanism, then migrate all 15 consumers at
  once.

### Two findings that changed the plan as written

**A1 and A3 were misdiagnosed in the original audit.** Both were reported as live P1
defects. They are not:

- **A1** (`ON CONFLICT(id)`) is *correct today*. The sole constructor of
  `FilterCandidateOutcome` derives `id` as `sha256(f"{key}|{horizon}")[:12]`
  (`filter_evidence.py:1391`), so the id conflict is the same conflict. #2949 still
  lands it, reframed as removing an undocumented coupling — the model defaults `id`
  to a random `new_id()`, so the first caller that skips the derived id would get an
  `IntegrityError` instead of the intended upsert.
- **A3 was wrong in a way that would have caused an outage.** Turning
  `ledger.list_open_experiments`/`get_exposure` into `raise` would have broken
  production: `candidate_selection_overlap_block_reason` already fails closed
  (`except Exception: return "candidate_selection_overlap_evidence_unavailable"`), so
  raising there would have **permanently blocked candidate selection**. And
  `_require_momentum_geometry_authority` already raises on `exposure is None`. Both
  readers were correct; the empty table is the accurate answer now that #2945 removed
  the writer. **Not done, and should not be done.**

The lesson is the same one the vocabulary work below teaches: a reported defect is a
hypothesis until the calling code is read.

**Closing a status vocabulary is only safe after enumerating what stored rows hold.**
Six values were missing across two entities, each named by a *read* path, and every
one was found by running the full suite rather than by inspection:

| Entity | Missing | Named by |
|---|---|---|
| `Order` | `executed` | `trade_stats.completed_fill_statuses` |
| `Order` | `failed`, `rejected` | `trade_stats.definitive_failure_statuses` |
| `AgentRun` | `error` | `run_view.FAILED_AGENT_RUN_STATUSES` |
| `AgentRun` | `success` | `run_view.SUCCESS_AGENT_RUN_STATUSES` |
| `AgentRun` | `cancel_requested` | `health._FAILED_STATUSES` |

Omitting any of them would have **fail-closed a real upgrade**: the migrations report
the offending value and stop rather than rewriting history, so an operator would have
been blocked with no safe move. All six are legal with no incoming transition — history
stays readable, new writes cannot produce them.

This also surfaced a dual mainline the audit had not listed: **AgentRun status
classification exists in three independent copies** — `run_view.py`, `health.py`, and
now the `Literal`. Deriving the first two from the Literal is a Phase D item; the
comment in `models.py` exists to stop a fourth copy appearing.

### Phase B remainder

`tests/specs/` is at **54 failed / 1350 passed**, down from 55/1337. Three known items
need more than an assertion edit and are not yet done:

- `spec_learning_contract` LC-S01 needs lab authority seeded — `experiments.py:79`
  checks authority before measurement support, so editing the expected string would
  delete the invariant instead of testing it.
- `spec_learning_attribution` imports `_ensure_experiment`, removed from
  `learning/experiments.py`.
- `spec_agent_contracts` AC-S13 and the `equity_observer` source-scan assertion read
  production source *text*; they need a decision on whether the behaviour still
  exists. AC-S13 is also the one place a #2945 deletion may hide a real regression
  (`guard_set_changed` and `operator_queue_slot_released` now have zero production
  references) — verify before touching it.

Phase D (dual-mainline elimination) and Phase E (the written policy) are unstarted.

## Why this plan exists

Three separate problems turned out to be one problem:

1. Four branches diverged and now conflict with each other and with `master`.
2. PR #2945 ("converge entry, UTA, learning and deployment mainlines") landed while red.
3. The repository carries **dual mainlines** — one capability with two live implementations — caused by years of backward-compatibility accretion.

(1) and (2) are symptoms. (3) is the disease, and #2945 was an attempt to treat it that
was merged without a working verification signal.

## First-principles reading

Two independently-decided policies combined into the defect:

**Policy A — Additive-Only Schema Migrations** (recorded project decision): columns and
tables are never renamed or dropped, so historical rows stay auditable. This is correct
**for the data layer**.

**Policy B — CI is manual-only** (`.github/workflows/ci.yml` is `on: workflow_dispatch`;
the file header states "Agents are responsible for running relevant repository tests
during development"). Deliberate: CI upkeep cost exceeded its yield.

The failure is that **Policy A leaked out of the data layer into the code layer**. Keeping
an old *column* costs nothing. Keeping an old *code path* costs a second implementation
that must be edited in lockstep forever. Nothing in Policy A ever required that, but the
habit generalized, and 1034 `legacy` / 613 `fallback` / 136 `deprecated` mentions
accumulated in `aletheia/**.py`.

Policy B then removed the mechanism that would have made the cost visible. With no
automatic gate, correctness depends on an agent choosing to run the right tests. #2945
ran "251 focused Python tests" out of **9764** collected and declared itself validated.

### The red baseline is the largest single debt

`tests/specs/` on three revisions (same command, same ignore, `-p no:randomly`):

| Revision | Result |
|---|---|
| `57a787961` (before #2945) | 29 failed, **1699 passed** |
| `8fcdac83b` (audit branch, base `4b821b258`) | 30 failed, 1698 passed |
| `772666741` (**current master**) | **55 failed, 1337 passed** |

And the whole repository, measured on `772666741`:

```
pytest tests/ -q -p no:randomly   →  141 failed, 9594 passed, 23 skipped, 6 xfailed
                                     in 286.38s (4m46s)
```

**Speed was never the obstacle.** The spec suite runs in 68 seconds; the entire 9764-test
repository runs in under five minutes. An agent can afford to run everything on every
change. What it cannot afford is a **141-failure baseline**, because then a red result
carries no information: there is no way to tell "I broke this" from "this was already
broken", so the rational move is to skip verification — which is exactly what #2945 did.

**Value of a test = frequency it is run × credibility of its verdict.** A suite nobody
trusts is worth zero no matter how many assertions it holds. 9764 assertions at 1.4%
ambient red is worth less than 500 assertions at 0% red, because only the second one can
answer the single question that matters: *did my change break something?*

This yields the ordering rule for everything below: **restore a green signal before adding
any new assertion.** And it sets the target — not "fix all 141", which is its own
over-engineering trap, but "define the set that must be green, make it green, and run it
every time."

## Evidence: branch-by-branch disposition

All four verified against `772666741`. `git cherry` was **not** trusted on its own —
`master` is squash-rewritten, which makes cherry's `+`/`-` direction unreliable; every
verdict below rests on content comparison.

| Branch | Position | Verdict |
|---|---|---|
| `audit/backend-state-authority-parity-20260906` | 13 commits, 112 files, +3920/−861 | **Salvage — highest value** |
| `debt-clear` | 3 ahead / 2 behind | **Discard** — it is #2945's source branch |
| `fix/entry-convergence-2886` | 2 ahead / 44 behind | **Discard** — content already verbatim on master |
| `fix/pr2945-regressions` | **0 commits**, 25 uncommitted files | **Partial salvage** — see below |

### `audit/…-parity-20260906` — salvage

- All 11 owner symbols it introduces (`claim_agent_run`, `begin_agent_run`,
  `finish_agent_run`, `mark_agent_run_stale`, `AgentRunStatus`, `transition_order`,
  `update_order_metadata`, `claim_close_order`, `transition_position_close`,
  `SelectedAccountPort`, `idx_agent_runs_one_active_per_agent`) are **absent** from
  master. #2945 did not cover this ground.
- Its 36 new tests pass. Against its own base it is net-neutral on the spec suite
  (29→30 failed) and it **fixes** `spec_uta_boundary.py::test_selected_reads_use_canonical_manager_and_unmigrated_mutations_stay_disabled`.
- The P1 defect it repairs is **still live on master**: `aletheia/agent_runtime/scheduler.py:687`
  ends agent runs with `WHERE agent_name = ? AND status = 'running'`, so a concurrent run
  can be assigned another run's terminal result. #2945 moved this code without fixing it.
- Two self-inflicted failures, both test-harness gaps, not production defects:
  `_Exch` fake lacks a `bitget_profile` attribute (`aletheia/uta/manager.py:88`), and one
  settings test does not seed experiment authority (`503 experiment authority unavailable`).

### `debt-clear` — discard

Its two commits carry #2945's exact subject lines; it is the pre-squash source branch.
`git cherry` marks both `+` (absent upstream) — a false signal caused by squash rewriting.

### `fix/entry-convergence-2886` — discard

Both commits' content is already on master verbatim — the `entry_plan_running_contract_mismatch`
guard sits in `complete_candidate_planning_with_plan` in master's `repositories.py`,
byte-identical to the branch version.

### `fix/pr2945-regressions` — partial salvage

Zero commits; all value is uncommitted. Its purpose is **not** restoring capability #2945
deleted — it is turning #2945's red tests green, mostly by deleting tests. After
`ruff format` normalization the real semantic delta is **+8/−260** (about 1186 of
`scheduler.py`'s 1428 changed lines are formatter noise).

**Take (real value):**
- `scheduler.py` — `_run_pi_trading_heartbeat` no longer swallows exceptions silently
  (`except Exception: pass` → log + `_record_job_failure` + explicit return). 8 lines, the
  only production bug fix in the set.
- `spec_pipeline_exchange_materialization.py` + `uta_private_history.py` — the shared UTA
  fake now **asserts** account identity instead of ignoring it; fixes 12 failures at once.
  This is hardening, not deletion.
- `spec_uta_boundary.py` — removes `scheduler.py` from the unrestricted-legacy-private-IO
  allowlist (tightens the trust boundary).
- Assertion corrections in `test_scheduler_timeouts.py`, `spec_agent_contracts.py`,
  `spec_learning_attribution.py`, `spec_learning_contract.py` — each asserted something
  production no longer emits (`learning_measurement` pool, `exchange_sync_total_failure`,
  a kwargs-less `sync_all_exchanges`).

**Do not take as-is (deletions that cost real coverage):**
- `spec_universe_distribution_audit.py` (223 lines, staged for deletion) — 3 of its 4 tests
  **pass** on master; the single failure is one stale entry in an import list
  (`aletheia.universe.signal_emitter`). One line fixes it.
- `test_filter_evidence.py` (−239 lines) — the production code under test is **alive** in
  `aletheia/analytics/filter_controller.py`; failures are stale setup (`StopIteration`,
  unseeded authority), and one deleted test **passes** on master.
- `spec_live_unlock.py` — the two deleted tests guard a **trading-authority invariant**
  (unlocked → new proposals go live; locked → stay paper). They fail only because
  `_configured_paper_execution_mode` moved. Repoint the monkeypatch.
- `spec_agent_contracts.py` AC-S13 — red because `guard_set_changed` and
  `operator_queue_slot_released` now have zero production references. Deleting the
  assertion hides the question of whether #2945 removed two wake-up paths. **Verify
  separately.**

**Separate decision — `tpsl_fill_check` job removal (227 lines):** its dependencies
(`materialize_bitget_close`, `configured_bitget_profile`, `BitgetClient`) are all alive and
`exchange_tpsl_order_id` is still written by `execution/coordinator.py`. It is the only
consumer on the "Bitget TPSL plan trigger → private fill posted" path and no successor was
wired in (the intended replacement path's own two tests are still red). The motive is
legitimate — it was scheduler's last UTA-boundary bypass — but this is a **capability
removal**, not cleanup. It gets its own PR and its own decision.

**Unfinished:** 3 failures remain, both diagnosed —
`spec_pipeline_live_disappearance_settlement.py` patches `_client_for_mode` on a module
that never had it, and one materialization test bypasses the new `_patch_bitget_history`
helper (one-line fix).

## Evidence: did #2945 delete real capability?

**No.** Every deleted module has a live replacement; the commit has 0 new files and 0
renames, so capability was absorbed into existing files rather than moved:

| Deleted | Replacement |
|---|---|
| `learning/authority.py` (−647) | `observation_lab/canonical.py` `get_authority_source` |
| `learning/lifecycle.py` (−1306) | `observation_lab/runner.py` `run_lab_windows` → `evaluator.py` |
| `execution/{binance,bitget,okx}_broker.py` (−2589) | `uta/ccxt_adapter.py` `submit_order` + `uta/profiles/*` + `coordinator.py` |
| `agent_shell/trading_decision.py` (−3903) | `entry_planner.py` `run_entry_planner_batch` + `entry_plan_compiler.py` |
| `portfolio/uta/*` (−529) | `aletheia/uta/` (v3) |
| `runtime_continuity/*` (−1446) | `aletheia/update/*` (retirement is backup-verified) |

Master is runtime-coherent: `import aletheia.server.app` OK, `ruff check aletheia/` clean,
9764 tests collected with 0 import errors.

**What #2945 did leave behind:**
1. 43 new spec failures, all stale contract assertions. `spec_agent_contracts.py` is
   self-contradictory — #2945 edited that file yet missed its own `learning_measurement`
   assertion.
2. `analytics/filter_registry.py` — **8** `hardcoded_at` pointers to deleted modules
   (lines 229, 247, 273, 292, 309, 326, 416, 619); 2 `stage="ranker_rejected"` FilterSpecs
   can never fire again.
3. `agents/committee/` — 11 modules orphaned (the ~549-line integration glue was deleted,
   the glued package kept).
4. Frontend `settingsSwitchContract.test.ts` hardcodes `toHaveLength(65)`; actual 64.
5. **Operational consequence:** `reconciliation.py:1064-1072` now requires
   `order["uta_account_identity_json"]`. Orders created before #2945 will never reconcile
   again — they park permanently at `inconclusive`. Fail-closed is the intended semantic,
   but the pre-existing rows need a deliberate backfill or write-off decision.

## Test policy for this program

Constraints, in force for every phase below:

1. **No test whose only justification is coverage.** Coverage floors and `.coveragerc`
   file lists are not evidence; they are ratchets that survive their own usefulness.
2. **No new gate attached to `ci.yml`.** It is manual-only by decision. Anything hung there
   runs never and is pure liability.
3. **A test must be able to fail for a real reason.** An assertion about a symbol that no
   longer exists is not protection — it is noise that trains people to ignore red.
4. **Delete stale tests, don't adapt them.** If the production symbol is gone and gone
   deliberately, the test goes with it. If the production symbol is *alive*, fix the setup
   instead of deleting the test. This distinction is the whole difference between the
   `fix/pr2945-regressions` deletions worth taking and the ones worth reverting.
5. **Prefer one assertion at the chokepoint over N assertions at the call sites.** A DB
   `CHECK` or a guard in the single owner function replaces a test per caller.

Applying rule 1 and 2 to the audit branch's own additions, the following are **withdrawn**
before it lands (about −60 lines of scaffolding):

- `.github/workflows/ci.yml` (+38) — new steps on a disabled workflow
- `scripts/ci/coverage_floors.py` (+4 floors) and `.coveragerc` (+4 files) — coverage ratchet
- `scripts/ci/ci_plan.py` (+11) — CI path plumbing
- `tests/test_production_parity.py::test_parity_sources_cannot_use_inmemory_or_seed_objects_under_proof`
  — a meta-test asserting that the other tests do not use an in-memory DB; over-defensive

Kept: the `production_parity` marker in `pyproject.toml` (makes `-m production_parity`
usable locally) and the 6 remaining parity tests, which assert data-corruption invariants
(migration authority on fresh **and** upgrade paths; one-active-run admission under
concurrency; owner serialization) — these can each fail for a real reason.

## Execution order

Two rules force the ordering:

1. **Nothing lands on top of an unreadable signal.**
2. **A zero-conflict fix never waits behind a rebase.** #2945 already demonstrated the cost
   of letting a large refactor block everything else while diverging in parallel.

Rule 2 comes first, because four live P1 defects turn out to be one-word fixes.

### Phase A — Four one-line P1 fixes, zero conflicts (do first)

These depend on nothing, conflict with nothing, and each repairs a verified production
defect. All four confirmed by direct reading of master.

**A1 — `storage/repositories.py:10733`, wrong conflict target.** The upsert says
`ON CONFLICT(id)` but the table's real unique key is `UNIQUE (outcome_candidate_key, horizon)`
(`migrations.py:368`), and `id` defaults to a random `new_id()` — so the conflict target can
never match. Change the target to `(outcome_candidate_key, horizon)`. Highest value per
character in this entire plan.

**A2 — `execution/gateway.py:2226`, live branch cannot fail a plan.** The demo failure path
runs `update_trade_proposal(status="error")` **and** `_fail_entry_plan(proposal, "demo_order_failed")`
(line 2043). The live path runs only the first. A live order rejection therefore parks
`entry_plans.status` at `ordered` permanently. Add the missing call, or lift it into the
shared failure path.

**A3 — `learning/ledger.py`, silently disabled interlocks.** `learning_experiments` has
**zero INSERT sites** in `aletheia/` — #2945 deleted its only writer while keeping three
readers, so `learning_hypotheses.status` can now never leave `proposed`. Separately,
`learning_exposures` has **zero INSERT sites in the production database**, while
`observation_lab/exposure.py:3` claims in its docstring that "the production
`learning_exposures` table remains the canonical write" target — its code writes
`get_lab_db()`, a different database. The readers read the empty table forever. Turn these
silent `return None` / `return []` paths into `raise`, so a disabled interlock announces
itself instead of returning a frozen verdict. **A disabled safety interlock that reports
success is the most dangerous single item in this plan.**

**A4 — `analytics/filter_registry.py`, eight dangling pointers.** Lines 229, 247, 273, 292,
309, 326, 416, 619 name modules #2945 deleted. Two `stage="ranker_rejected"` FilterSpecs can
never fire again. Documentation-grade damage, but it misleads the filter-evidence surface.

Note what A1–A3 have in common: **every one is a case of "tightened one side, missed its
twin."** That is the dual-mainline failure mode expressing itself as a bug rather than as
duplicate code.

### Phase B — Restore the signal

The target is **not** "fix all 141 failures" — chasing a whole-repo green on a suite with
years of ambient red is the same over-engineering trap in a different costume. The target
is: **one named set that is green, fast, and actually run every time.**

`tests/specs/` is the right set. It is the contract layer (it asserts what the system
promises, not how it is built), it runs in 68 seconds, and it is where #2945's damage
concentrated. Everything outside it stays runnable but is not a gate.

Step one is bringing `tests/specs/` from 55 failures to the pre-#2945 baseline of 29, using
only the Phase-B classification (fix stale setup / delete dead assertion), no new features.

Take from `fix/pr2945-regressions`: the `uta_private_history.py` +
`spec_pipeline_exchange_materialization.py` pair (12 failures), the four assertion
corrections, and the dead-test deletions where the production symbol is provably gone
(`enqueue_serenity_theme_for_pi`, `snapshot_opportunity_identity`, `promote_signal`).
Reject its deletions of `spec_universe_distribution_audit.py`, `test_filter_evidence.py`,
and `spec_live_unlock.py` — repair those instead. Fix the two known unfinished items.

Also in scope: the 8 `filter_registry.py` dangling pointers, and the frontend
`toHaveLength(65)` → 64.

Then the 29 pre-existing failures, **as a separate explicitly-scoped pass**: each one gets
fixed, or gets deleted with a written reason. Expect a large fraction to be the same class
of stale assertion — the act of writing the reason is what exposes them.

Exit criteria: `tests/specs/` is green and `uv run pytest tests/specs/ -q` is the stated
pre-commit check. Not a hook, not a workflow — a line in `AGENTS.md` and a suite that
stays green because a red result now means something.

The remaining ~86 failures outside `tests/specs/` are logged, not fixed here. They become
Phase D fuel: several are the *symptom* of a dual mainline, and they resolve for free when
the duplicate path is deleted. Fixing them before the cleanup would mean fixing them twice.

### Phase C — Land the audit branch's ownership work

The branch already did the expensive part. Measured against master:

| | master | branch |
|---|---|---|
| Files calling `update_position(` | **17** | **0** (function deleted) |
| `update_position` / `update_order` call sites | **42 / 27** (13 using `**kwargs`, statically unauditable) | migrated |
| `positions` / `orders` / `agent_runs` status `CHECK` | **none** | **all three** |

So this phase is **rebase engineering, not migration work**. Rebase as **five**
independently-reviewable PRs, not one 112-file PR.

**Obstacle 1 — migrations genuinely collide.** #2945 took slots
`20260907_0070…0073` and versions `v71…v74`; the branch took `20260906_0070…0072` and
`v71…v73`. The branch's three migrations must move to `0074/0075/0076` and `v75/v76/v77`,
with recomputed checksums (`_validate_migration_registry` checks them). **Renaming is not
enough** — `_validate_applied_migrations` requires applied migrations to form a prefix, so
they must be genuinely reordered to the tail or any database that already applied the
0907 batch will fail on startup.

**Obstacle 2 — deleted brokers.** The three `execution/*_broker.py` files are gone. Those
hunks are mechanical writer→owner substitutions, so **drop them** — zero loss. Same for the
hunks touching the deleted `learning/{lifecycle,maintenance}.py`; the new file
`observation_lab/experiment_owner.py` lands cleanly on its own. Note
`execution/close_convergence.py` is a **read-only view** (from #1065), not a writer.

| PR | Content | Note |
|---|---|---|
| 1 | AgentRun lifecycle + migration `v75` | **No conflicts.** Fixes the live P1 at `scheduler.py:687` |
| 2 | Order lifecycle + `v76` | Drop the 3 broker hunks |
| 3 | Position lifecycle + `v77`, generic-writer split | Largest; retires `update_position(**kwargs)` |
| 4 | Observation experiment authority epoch | Drop the `learning/{lifecycle,maintenance}` hunks; add the missing test seed |
| 5 | Audit document + 6 parity tests | Retained invariant tests only |

**Withdrawn: the UTA commit (`a4649bb9b`).** It routes through `uta_read_boundary_for`, and
#2945 **deleted that concept entirely** — along with the config switch
(`ExchangeConfig.uta_read_boundaries`) and the whole duplicate `portfolio/uta/` package.
#2945 solved the same dual mainline more thoroughly, by deleting the legacy branch instead
of centralizing a resolver for it. Keep only its `_Exch` fake fix, folded into Phase B.

This is worth recording as the pattern to copy: **#2945's UTA work is the one place it
deleted the switch along with the branch.** A capability with two paths and a config flag
choosing between them is not converged until the flag is gone too — otherwise the second
path is one config change away from returning.

Each PR carries its own verification run against `tests/specs/`; none is declared done on a
subset. That rule is what #2945 broke.

### Phase D — Eliminate the remaining dual mainlines

Ordered by damage. **The lever matters more than the list: prefer one change that closes all
bypasses over a migration per caller.**

**D1 — the 13 unauditable `**kwargs` write sites. Use a database trigger.** After Phase C
lands the owner functions, 13 call sites still reach position writes through `**kwargs`,
which **static analysis cannot see** — a source-scanning test can never prove they are gone.
A `BEFORE UPDATE OF pending_close_order_id, pending_close_status` trigger on `positions` is
the only mechanism that catches them, and it closes all 7 offending modules at once
(`exchange_materialization.py` alone has 7 uncontrolled writes).

This route is already proven here: `positions` **already carries 5 triggers**, including
`trg_positions_exit_plan_projection_guard` with referential and monotonicity checks, and
`entry_plans` is guarded by `trg_entry_plan_facts_immutable`. The reference pattern is
stronger than "Literal + CHECK + CAS" — it is those **plus a trigger**, because the trigger
is the only layer a `**kwargs` writer cannot route around.

**D2 — status vocabularies with no `CHECK`.** `positions`, `orders`, `agent_runs`,
`trade_proposals` — all four have free-string status columns; only `entry_plans` has a
`CHECK`. Phase C adds three. Which leaves:

**D3 — `trade_proposals` is the one lifecycle entity nobody converged.**
`update_trade_proposal` has **28 call sites, all 28 writing `status`**, 24 of them in
`gateway.py`, and 10 passing a variable rather than a literal (so the vocabulary is not even
statically enumerable). By call-site concentration this is *worse* than the
`update_position` case Phase C fixes. Copy the `transition_order` + `_ORDER_TRANSITIONS`
pattern verbatim. This is the first item after Phase C.

**D4 — the second close-claim path.** `coordinator.py:402 _claim_close()` is correct
(transaction + re-read + `has_unresolved_close_claim` + stamps UTA identity).
`scheduler.py:2555 _claim_observed_tpsl_close()` mints its own Order, invents its own
conflict check, and **omits `uta_account_identity_json`** — and #2945 then added three
strict readers of that field in `pending_close_recovery.py:194/248/286`. Confirmed:
`UTAAccountIdentity.model_validate_json('')` raises `ValidationError`, so orders minted by
the scheduler path fail in all three recovery readers. Lever: export
`claim_close(..., status=, exchange_order_id=)` from the one correct construction point and
delete `scheduler.py:2555-2612`. (Overlaps the `tpsl_fill_check` decision below.)

**D5 — live/demo mode decided twice.** `risk/policy.py:13-23` holds
`DEMO/LIVE_EXECUTION_MODES` and **raises** on an unregistered mode — the only fail-closed
version. `risk/engine.py:490 _is_live()` hardcodes a 3-value tuple plus five
`endswith("_live_limited")` checks, and it runs on every futures admission
(`engine.py:2552`). Trigger condition is one line long: **add a fourth exchange and
`_is_live` silently classifies a real live account as demo**, applying demo limits to live
money. Lever: derive `policy.py:19` from `config/exchanges.py`'s
`EXCHANGE_UNIFICATION_MAP` instead of enumerating, then delete `engine.py:490-491`.

**D6 — risk config flat vs nested, and the dashboard lies.** `RiskConfig` carries ~45 flat
fields alongside `ModeRiskPolicy`; `__setattr__` mirrors flat→nested but **not the reverse**
(reproduced: write 5.0 to nested, flat still reads 1.5, and it survives reload).
`check_account_leverage` (`engine.py:2971`) reads flat while `check_evaluate`
(`engine.py:1029`) injects the nested view — so **the limit shown in the dashboard is not
the limit the engine enforces.** Zero-risk interim: point `engine.py:2969-2974` at
`resolve_risk_policy` (three lines). Full fix: `ConfigDict(extra="forbid")` at
`schema.py:807` and delete the flat fields, which exposes all 12 remaining
`getattr(cfg.risk, "…")` bypasses at once.

**D7 — CLI vs web dashboard.** `aletheia/cli/_deprecated.py` (30 lines) prints a stderr
pointer while keeping five fully-functional CLI twins of dashboard features
(`bitget_cmd.py`, `model.py`, `permission.py`, `risk_cmd.py`, `secrets_cmd.py`). **Soft
deprecation *is* the dual mainline** — it is the mechanism that keeps the second
implementation alive. Delete the superseded commands; keep the deliberate non-duplicates
(`bitget sync`, `knowledge reindex/search`) and the bootstrap surface
(`gateway start/restart`, `doctor`).

**D8 — observation authority re-decided in 9 places.** The switch is
`observation_authority_state.authority_source` via `canonical.get_authority_source()`. But
`repositories.py:349 _benchmark_outcome_write_allowed` **ignores the switch** and invents its
own "check legacy table first, else fall back to lab" precedence — across **two different
databases** (`app.db` vs `observation-lab/lab.db`), where no FK, CHECK, or trigger can help.
Point it at `get_authority_source()`. The audit branch did **not** fix this one.

**D9 — `trading/entry_compat.py`** (293 lines, 6 production callers including
`risk/engine.py`). A live compatibility bridge, not dead code. Needs a per-caller read.

**D10 — shadow dedup implemented 3 ways.** Authoritative `trades.py:444` takes the *latest*
tick; `alpha_attribution.py:897` takes the *earliest* with a `rank 1..5` prefilter;
`ranker_objective_audit.py:626` uses a *different partition key* entirely. Currently zero
executable evidence for the invariant.

**Dead paths — delete, don't fix (P3):**
- `update_opportunity` (`repositories.py:1137`) — **zero callers**, sitting immediately
  beside the guarded `update_open_opportunity` (:1141) which raises
  `"Opportunity lifecycle status requires a transition helper"`. **The guard is opt-in, so
  it is useless.** Remember this one: it is the anti-pattern this whole plan exists to
  eliminate — a correct owner is worth nothing while an unguarded twin remains callable.
- `position_enrichment.build_entry_enrichment` — docstring promises "consistent metadata
  across four brokers"; zero callers (three brokers deleted, and `paper_broker` never used
  it — it copied its own version).
- `trust/guard_chain.py` (168 lines) — zero callers, reimplements four engine risk checks
  with defaults that contradict the schema (5.0 vs 1.0).
- `agents/committee/` — 11 modules orphaned; the ~549-line integration glue was deleted and
  the glued package kept.
- ⚠️ `pending_close_recovery`'s automatic half (`recover_absent_stale_close_orders`,
  `release_stale_submission_rejected_claims`) — **zero production callers since birth**
  (`git log -S` finds no scheduler registration, ever). The deadlock its docstring describes
  has **no automatic recovery in production**. Before wiring it up, fix the vocabulary
  first: `VENUE_TRADE_RATE_LIMITED_STATUS` (:57) is not in `CLOSE_CLAIM_STATES`, so wiring
  it as-is detonates a P1.

**Not debt — do not touch:**
- `aletheia/uta/compat.py` (56 lines) — a legitimate local-row→UTA-model conversion adapter
  with 7 healthy callers. Misleading name only; rename at most.
- `model_provider/{openai,anthropic}_compatible.py` — "OpenAI-compatible protocol",
  unrelated to backward compatibility.
- `storage/migrations.py` (134 `legacy` mentions) — historical migration steps. Policy A
  applies here and is correct.

This is why the sweep cannot be keyword-driven: of the five filename matches for
`compat|legacy|shim|deprecated`, **two are not debt at all**. Every candidate needs a
semantic read before deletion.

**Not debt — do not touch:**
- `aletheia/uta/compat.py` (56 lines) — a legitimate local-row→UTA-model conversion
  adapter with 7 healthy callers. Misleading name only; rename at most.
- `model_provider/{openai,anthropic}_compatible.py` — "OpenAI-compatible protocol",
  unrelated to backward compatibility.
- `storage/migrations.py` (134 `legacy` mentions) — historical migration steps. Policy A
  applies here and is correct.

This is why the sweep cannot be keyword-driven: of the five filename matches for
`compat|legacy|shim|deprecated`, **two are not debt at all**. Every candidate needs a
semantic read before deletion.

### Phase E — Prevent recurrence

One rule, written down where it will be read (`AGENTS.md`): **Policy A is a data-layer
policy.** Keeping a column is free; keeping a code path is not. A migration may preserve
any column it likes, and the code that reads it must still have exactly one
implementation.

No new tooling, no new gate, no lint rule — those were tried and became the maintenance
cost that shut CI down.

## Open decisions requiring the owner

1. **`tpsl_fill_check`** — accept the capability removal, or wire a successor first?
2. **Pre-#2945 orders stuck at `inconclusive`** — backfill `uta_account_identity_json`, or
   accept them as permanently unreconcilable?
3. **`agents/committee/`** — delete the 11 orphaned modules, or re-wire the integration?
4. **The 29 pre-existing spec failures** — fix them in Phase B, or freeze them as a
   documented baseline and only forbid new ones?
