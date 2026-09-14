# Slim Core production ownership inventory

Source checkpoint: `29eb0b0ce485e9d90c49c318fbc32b1b3a32a433`, under
[#3134](https://github.com/iridyne/aletheia/issues/3134). This is an inventory of
current boundaries, not a declaration that compartmentalization or all single
writers are proven. No runtime/data migration follows from classification here.

## Scheme A follow-up

The source review began against a dirty working tree based on
`b1cfd7a7573af925520c85b86c1d043dc3e7cb58`. The reviewed architecture, source,
test and documentation batches were subsequently partitioned into scoped commits
from `c3dc36ebc` through `3f8994b3f`. The earlier counts, paths and checkpoint
claims below remain point-in-time evidence; commit partitioning does not promote
them into final-head, deployment or runtime proof.

### End-to-end completion audit — 2026-09-14

Product Truth follow-up at working base
`b1cfd7a7573af925520c85b86c1d043dc3e7cb58`: the root README still described
OKX/Binance and the former stock-linked perpetual profile as active scope, and
the architecture navigation listed all three registered venues as the current
exchange integration. Both now match the canonical Slim Product declaration:
Bitget Demo crypto spot/perpetual is the active qualification route; retained
OKX/Binance and stock-linked identities are frozen for history/recovery, not
new-entry support. The architecture LLM row now names only the three accepted
HTTP protocol families. Archived capability-review files remain unchanged and
explicitly historical.

`SYSTEM_MAP` also claimed an `okx_demo_symbols` 4h job, while current code and
tests prove no such scheduler job exists. The stale row and its unreachable
pause-essential ID were deleted; the frozen Extension's on-demand read helper
was not promoted, scheduled or removed. Scheduler/OKX/execution verification
passes 37 tests with 20 spec claims. Ruff/docs/diff checks pass. This closes the
identified entry-document contradictions; it is not yet a complete rendered
Product Truth or final-head audit.

Candidate writer coverage follow-up: bounded search found no Candidate publisher
or raw Candidate snapshot/member insert in `ops/`. The existing single-owner AST
guard now scans Ops in addition to Core and Extensions, and rejects direct
`publish_candidate_snapshot` calls outside Core. Its focused run passes (1 test,
143 deselected, 3.23s); Ruff/diff pass. This covers direct calls and literal SQL,
not arbitrary dynamically assembled SQL or final-head reachability.

Planning/Entry writer follow-up: all located direct writes to
`candidate_planning_generations` and `entry_plans` are in Core repositories;
bounded schema-copy writes remain in migrations. No direct table writer was
found in other Core modules, Extensions or Ops. A scoped AST guard now reserves
literal INSERT/REPLACE/UPDATE/DELETE statements for those two storage files
across all three roots and self-checks its INSERT-OR-IGNORE, case-insensitive
UPDATE and DELETE forms. Candidate plus Planning/Entry guards pass 2 tests (143
deselected, 4.09s); Ruff/diff pass. Named repository calls still have multiple
legitimate lifecycle transitions, so this proves one direct storage boundary,
not yet semantic uniqueness of every state transition or dynamic SQL.

RiskDecision writer follow-up: production searches locate only Risk Engine calls
to `insert_risk_decision` / `insert_authoritative_risk_decision`; repositories
own the actual insert and authoritative lookup-insert transaction. Extensions
and Ops have no direct writer. A scoped AST guard now reserves RiskDecision
writer imports/calls for those two Core files and literal table mutation for
repositories/migrations. It self-checks INSERT-OR-IGNORE, case-insensitive UPDATE
and DELETE patterns. The three Candidate/Planning/Risk writer guards pass (3
tests, 143 deselected, 5.25s); Ruff/diff pass. Tests may still construct fixture
decisions directly; arbitrary dynamic SQL and semantic correctness of each Risk
decision remain outside this lexical proof.

Order boundary follow-up: direct `orders` table mutation remains in Core
repositories/migrations, while external `submit_order` dispatch remains in
Execution Coordinator and its production entry import remains in Gateway. A
scoped AST guard now checks literal Order DML and `_insert("orders", ...)` across
Core, Extensions and Ops, rejects Core submission dispatch outside Coordinator,
and rejects production `execute_order` imports/calls outside Gateway. The four
Candidate/Planning/Risk/Order ownership guards pass (4 tests, 143 deselected,
6.40s); Ruff/diff checks pass. Reconciliation, materialization and recovery keep
their existing repository-mediated lifecycle transitions; this lexical proof
does not claim one semantic owner for every Order state transition, detect
arbitrarily assembled SQL, or remove the frozen venue clients' unused legacy
methods.

Fresh working-tree verification follow-up after the approved Risk binding:
architecture fitness **142 passed in 99.49s**; Ruff passes all existing `core/`,
`extensions/` and `ops/` source directories; `git diff --check` passes. There is
no `legacy/` directory in this checkout, so an initial four-root Ruff invocation
reported that absent path and was rerun against actual roots. No empty Legacy
directory was created, and this does not prove all legacy behavior is inert.
The tree has 942 status entries, including 38 tracked deletions and 212 untracked
files. These are dirty-tree counts, not a reviewed/staged final change set.
No old Telegram bot import/path remains in current production/tooling/tests
except the intentional retired-helper negative assertion. Local planner accepts
the reviewed new Python paths, but its GitHub CI metadata remains advisory and
was not dispatched or treated as validation authority. Exact final-head and
runtime/qualification gates are still unsatisfied; the older matrix below is
retained as historical gap context, not promoted to completed status.
The bounded read-only readyz retry to the recorded Fedora address returned
curl exit 28 after the 3-second connect timeout (HTTP 000). This differs from
the older exit-7 observation below but still proves only client-to-endpoint
unreachability, not service state. No restart, deployment or saturation test ran.

This is an incomplete-evidence audit, not a phase gate. Live GitHub master is
`98113da4d6323ba6abc64dda0d5bf17b950d9fe9`; the working tree remains based on
`b1cfd7a7573af925520c85b86c1d043dc3e7cb58`, with 912 porcelain status entries at
the audit read. No focused working-tree result below proves either exact
master or final committed-head validation. #1884, #3135 and #2899 remain open.

| #3134 DoD requirement | Evidence boundary / remaining proof |
| --- | --- |
| Slim Product Truth | Several stale support claims corrected, including frozen Binance streams; full supported-surface audit remains incomplete. |
| Complete ownership/writer/side-effect inventory | API owners are mapped below, but the inventory itself identifies unresolved dynamic/raw-SQL and high-level ownership distinctions. Not complete. |
| Exchange/provider-agnostic Core | Reviewed derivatives acquisition is normalized; remaining historical source-layout and other subsystems are not globally proven. |
| No product-name business selectors where facts suffice | Multiple repaired paths have focused guards; no whole-Core completion claim. |
| UTA/generic CCXT/thin venue convergence | Reviewed adapter and reconciliation paths pass focused tests; all native exceptions still need complete necessity/ownership proof. |
| HTTP Model Endpoint support | Seven-file model transport/config/routing group revalidated: 235 passed; frontend support claims and final-head evidence remain separate. |
| Bitget Demo close and Outcome qualification | Uncostable-fee gate passes 3 cases both in the working tree and exact master `98113da4`; Fedora execution/Outcome qualification remains unproven. |
| #3135 one settlement authority | Shared monetary commit path is documented; complete writer, UI and runtime acceptance remains unproven. |
| #1884 reliable operator plane | Current source has independent readiness, truthful PoolTimeout classification and holder diagnostics; ordinary HTTP still uses one shared client. Required live attribution/isolation proof missing. |
| Legacy inertness | Partial reachability and import guards exist; no all-entrypoint conclusion. |
| Optional/frozen absence safety | Focused fresh-process tests cover reviewed packages; not every optional entrypoint is certified. |
| Highest-risk single-writer/credential/migration/idempotency/sandbox protection | Several focused guards exist; complete invariant coverage is not proven. |
| Scheme A, no permanent facade | Large uncommitted movement exists; final physical/layout and remaining-path audit is incomplete. |
| Exact final-head focused/architecture checks | No final committed head exists for the current dirty changes; cannot claim this gate. |
| Independently recoverable final evidence | Batch checkpoints exist, but no final DoD evidence package exists. |

#1884 source inspection confirms `_get_proxy_client` remains shared by ordinary
HTTP; `capacity_snapshot` exposes bounded categories, not secret paths or bodies.
The recorded incident endpoint `http://10.2.2.66:8787/_infra/readyz` failed to
connect during this audit (`curl` exit 7). This is only client-to-endpoint
unreachability, not proof of Fedora service failure, backend health, or pool
holders. No saturation, restart or deployment was attempted. The required
same-window three probes and holder attribution remain unavailable.

For #2899's explicit first-breakpoint gate, the current worktree test
`test_close_with_uncostable_fee_is_not_settled_as_complete` passes all 3 fee
cases (8 deselected), including replay/one-close-event assertions. Reading the
same test at the exact GitHub master SHA also shows no xfail and the accepted
#3135 assertions; the Issue's statement that master still has strict xfail is
therefore stale as a source-text claim. A subsequent isolated detached worktree
at that SHA executed the exact three-case node: **3 passed in 1.43s**, with
`-m "not online"`, a disposable `ALETHEIA_DATA_DIR`, and the existing dependency
environment without installing or building packages. The imported `aletheia`
source was verified inside `/tmp/aletheia-3134-master-3008.4iq8Id`; the detached
worktree remained clean. This proves the local deterministic first-breakpoint
gate on that master commit, not a real Demo close or Fedora qualification.

Next runnable verification is the current settlement acceptance/writer/UI
proof. The #1884 live-attribution
branch remains separately evidence-blocked; it does not stop independent work.

### Settlement acceptance verification — 2026-09-14

Current dirty-tree verification (not exact master or Fedora): the six-file
outcome-closure, outcome-read-model, broker-funding-economics, close-snapshot,
funding-spec and outcome-integrity-spec group passes **181 tests in 4.25s**
(14 spec claims), offline with an isolated data directory. The existing
`TradesPositions.staleSelection.test.tsx` passes **3 tests**. Its incomplete-cost
case checks explicit unavailable funding, no signed zero substitution, the
not-final known-net drawer label, missing components and the table's known-net
label. `/positions`, closed-position export and overview readers call
`position_realized_pnl_view`; this derives known components without adding
durable columns or changing the input row. This is source/component-test proof,
not a browser-to-Fedora end-to-end claim.

The named production settlement repository calls were rechecked. Initial
economic close and late cost completion call `pnl._commit_close_economics`.
Other calls update close explanation only, re-assess failed attribution under
the writer lock, or terminalize expired funding as unavailable/ineligible with
audit evidence. Administrative close performs an open-state CAS and writes
NULL realized economics, not a second final-PnL calculation. These retained
operations answer different questions and are not redundant monetary writers.
The tested funding cursor survives connection reopen: seven expired rows are
consumed with scan limit three, and the following fresh row settles on the
third scan. Audit recovery and stale-owner tests are included in the group.

Remaining proof: complete raw-SQL/dynamic write and maintenance reachability
audit, final-head validation, complexity comparison against the approved
baseline, and exact Fedora runtime/economic evidence. Named-helper convergence
alone does not prove the global single-writer acceptance criterion.

Follow-up direct-SQL audit: the existing Position ownership tests now cover
both Core and Extensions, with case-insensitive whitespace-tolerant raw UPDATE
detection. Four synthetic forbidden-writer cases prove detection in both
domains; this remains a lexical guard, not a proof against arbitrary dynamic
SQL construction. The ownership, historical-remediation and administrative
repair group passes 18 tests. The inspected migration UPDATE projects only
`exit_plan_effective_at`. Historical remediation writes classification and
eligibility, not money; doctor calls it with `dry_run=True`, and CLI application
is opt-in. Administrative repair reuses the normal close owner after a guarded
reopen. The other close-event insertion is partial-close materialization
(`is_partial=1`), which reduces remaining quantity and allocates known entry
fees; futures partial funding remains unavailable. It is not a second full-close
settlement and must not be deleted merely to reduce the writer count. No
historical repair was executed and no production mutation was added.

Dynamic-helper follow-up: current `_update` callsites use explicit non-Position
tables; `_update_pending` serves observation outcome tables, not Position or
close-event settlement. The maintenance requeue's fixed three tables are also
observation outcomes and it resets only status/settled_at for never-observed
failures. Bundle import's dynamic INSERT is restricted to six research tables;
it cannot import Core trading facts. The existing rejection regression now
explicitly covers Orders, Fills, Positions and close events in both dry-run and
merge modes, checking that neither research rows nor artifacts are committed
when a forbidden table occurs. Close-ledger, I/O honesty, settlement-deferral
and persistence-spec verification passes **93 tests in 2.55s** (6 spec claims).
No new runtime guard/helper was needed. This narrows the reviewed dynamic-write
surface; it does not claim arbitrary SQL, restore workflows, or the final
committed tree have been exhaustively certified.

### Architecture fitness revalidation and remaining physical gap

At working base `b1cfd7a7573af925520c85b86c1d043dc3e7cb58` (915 porcelain
entries at inspection), the complete existing `test_architecture_fitness.py`
passes **136 tests in 92.19s**, isolated data and `not online`. This includes
fresh-process optional composition, no concrete Extension imports by Core
consumers, selected direct-storage/lifecycle boundaries, Candidate owner,
credential/startup restrictions, safety defaults and coverage registration.
It is not the final-head gate and does not certify requirements absent from
that suite.

In particular, `ops/` is absent and independent maintenance commands still live
under Core. This contradicts a claim of completed Scheme A even though current
architecture tests pass. The fixed-target `repair_doge_adoption_overlap` tool
is the next bounded movement candidate: current references are its explicit
module command, dedicated tests and ownership guards, not an ordinary runtime
caller. Move only after its full caller/entrypoint check; preserve exact-target
validation and explicit historical-repair authority, remove the old module in
the same batch, and do not execute the repair. Runtime diagnostics and backup
services need their own consumer audit and are not moved by directory name.

The subsequent bounded Ops move is now implemented: the fixed-target tool lives
at `ops/maintenance/repair_doge_adoption_overlap.py`; the old Core module is
deleted, with no forwarding file. Only module command documentation changed
inside the moved tool; its validation and mutation logic is unchanged. Tests
import the Ops module. The raw Position SQL guard now scans Ops too, permitting
only this named historical-repair exception, and the no-runtime-import guard
also asserts that the old Core file is absent. Recovery and ownership tests
pass **21 cases in 1.89s**; Ruff and scoped diff checks pass. The new module's
`--help` succeeds; neither snapshot nor apply was run. `ops/` therefore now
exists, superseding the preceding physical snapshot, but remaining standalone
maintenance tools and full Scheme A are not yet certified.

The independent administrative-close repair command is now also at
`ops/maintenance/repair_administrative_close.py`, with its old Core module
deleted. Its current only caller outside the module was the dedicated test;
the identity architecture test now reads the Ops path, and the existing
no-production-import/old-path-absence guard covers both relocated repairs.
Command documentation uses the new path and makes historical owner authority
explicit. Runtime recovery logic, source/audit identities, private-evidence
checks and atomic reopen/canonical-close replay are unchanged. Both repair
suites plus Position ownership and the identity architecture node pass
**27 tests in 2.91s**; Ruff and scoped diff checks pass. Only `--help` was
executed for the new command; no private read or recovery was performed.

The one-shot retention backfill is now at
`ops/maintenance/data_retention_backfill.py`, with the old Core module removed.
Current references were standalone command documentation and tests, not the
nightly scheduler; recurring prune helpers retain their current ownership.
Only command-path text changed in the moved module. Tests and maintained
retention documentation use the new path. Initial verification exposed that
the exact legacy Experiment-reference/writer inventory scanned only Core;
it now scans Ops as well, retaining this same named tool in the inventory.
The no-production-import/old-path-absence guard also covers the backfill.
Final retention, legacy ownership and Position ownership group passes
**25 tests in 4.86s** (6 spec claims), with Ruff and scoped diff checks passing.
The new module's `--help` works. No real dry-run, prune, backfill, restore or
VACUUM was performed; retention defaults and destructive-operation authority
are unchanged. This relocation does not certify the tool's historical apply
workflow for use on current runtime data.

Remaining maintenance consumer classification (current source, not a decision
to move a directory wholesale):

| Module | Proven current consumers | Boundary / next work |
| --- | --- | --- |
| `database_capacity` | Scheduler bounded freelist/planner/checkpoint/capacity work; data CLI inspection and compaction | Runtime maintenance is Core; explicit full compaction is a distinct Ops candidate, requiring a function-level split rather than a reverse import |
| `filesystem_retention` | Scheduler cleanup and the relocated one-shot backfill | Shared recurring retention belongs with the existing lifecycle owner; no second Ops scheduler |
| `historical_accounting` | PnL read projection and inspect CLI | Read-only historical reconciliation, deliberately not verified PnL; preserving history visibility is required |
| `outcome_integrity` | Doctor `dry_run=True`; explicit knowledge CLI `--apply` | Mixed read-plan and historical-write surface; classify shared read helpers before moving the writer |
| `settlement_requeue` | Doctor count-only diagnostic; explicit data CLI `--apply` | Mixed observation backlog read and manual requeue; separate the Ops write from retained read diagnostics without duplicating the predicate |

This closes the assumption that every remaining `maintenance` file is an
independent Ops command. The next bounded seam is observation requeue's
read/write separation. Moving all five would create reverse dependencies or
hide current lifecycle ownership. No production changes or real maintenance
operations were made during this classification.
Capacity, capacity-CLI, retired execution/history and outcome-remediation
regressions pass **46 tests in 1.46s**, isolated data and `not online`; these
cover the retained behavior and are not proof of a completed physical split.

Observation requeue split now implemented: Core
`maintenance/settlement_requeue.py` retains only the read-only count and the
shared table/predicate constants; `ops/maintenance/settlement_requeue.py` owns
the existing manual transaction and CLI command. The Core data-command module
no longer imports or wraps the writer. Existing `main.py` composition registers
the Ops command if available, preserving its CLI name, default preview and
explicit `--apply`. There is no new registry, scheduler, fallback writer or
duplicate predicate. Doctor stays on the read side. The former Core writer is
absent, not forwarded. Settlement-deferral and opportunity-outcome specs pass
**38 tests in 1.33s** (19 spec claims), including actual CLI invocation for both
preview and apply against the isolated test database. Ruff and scoped diff
checks pass. No real observation rows or runtime state were modified.

Outcome-history split now implemented: Core `maintenance/outcome_integrity.py`
owns only classification/archive/compensation planning; Doctor uses its
`plan_outcome_history()` under the existing read snapshot. The explicit writer,
deterministic report rebuild and CLI callback live in
`ops/maintenance/outcome_integrity.py`, registered by the existing thin `main.py`
composition. No Core forwarding writer remains. Ops reuses the same planning
functions inside its existing apply transaction, preserving classification
predicates, rollback, replay markers and report-only compensation. No schema,
historical rows, monetary policy or runtime state changed.

The remediation, calibration, Position ownership and scheduler-timeout group
passes **90 tests in 4.41s**, isolated data and `not online`. Coverage checks
Core plan equality with dry-run and zero database changes, additive apply,
replay, rollback, default-preview CLI and Doctor readiness. Source guards now
cover both split modules and name the Ops classification-only SQL exception;
they still forbid deletion and compensation writes. Ruff and diff checks pass.
The extra module separates existing owners rather than adding a second plan or
writer; the next runnable maintenance seam is explicit database compaction.
This remains dirty-tree evidence, not final-head or Fedora acceptance.

Explicit database compaction now lives in
`ops/maintenance/database_compaction.py`, together with its existing CLI callback;
the thin `main.py` registers `data compact` when Ops is present. Core retains
capacity snapshots/trends, planner work, WAL checkpoint and bounded incremental
freelist reclaim. The old Core full-compaction function and CLI wrapper are
removed. Ops imports existing read helpers, not another capacity implementation.
The full compaction function body is unchanged: stopped-Gateway/free-space
checks, verified backup, shared writer permit, physical-layout validation,
VACUUM, integrity checks and original-WAL restoration remain in place.

Capacity and capacity-CLI tests pass **19 cases in 1.27s**, including temporary
database apply, row preservation, refusal/WAL restoration, Core writer absence,
and CLI preview/explicit apply dispatch. Ruff and diff checks pass. No real
database, Gateway, data history, credentials or packaging were touched. This
closes the classified mixed full-compaction seam, not the broader Ops/restore
inventory or final-head/runtime DoD gate.

Backup/restore consumer audit: `BackupService` remains used by persistence API
create/list, shared backup retention, update preflight/recovery and Ops
compaction; verification also gates explicit retired-runtime-table migration.
The API creates only online, non-recovery snapshots and constrains verification
to managed archive roots. Whole-store restore is not exposed by that API:
current production calls are update preflight and recovery. A wholesale backup
package move would therefore introduce reverse dependencies unless these
consumers are first separated. No package movement or packaged-update change
was made; this audit does not certify restore ownership as fully converged.

Five focused backup/checkpoint/security/integrity/WAL/config-redaction files
pass 38 tests with one WAL-sidecar test skipped because SQLite checkpointed it
early. That skipped scenario is not proven by this run. The destination-symlink
test previously only searched source text; it now creates a valid temporary
archive, invokes restore, checks explicit rejection/zero restored files, and
proves the external target bytes and symlink survive unchanged. No production
restore logic, recovery authority, real files or credentials changed. Existing
tests cover archive snapshot identity, copy-failure rollback, audit-generation
replacement and opt-in authoritative-profile refusal; they do not prove
process-crash recovery or every caller's quiescence boundary. Restore caller
authority and source-update rollback remain the next bounded audit seam;
wheel/pkg work remains excluded by owner instruction.

Source rollback checkpoint conformance: the persistence recovery Contract
requires update rollback to reject online/legacy/non-recovery archives, but
`update.auto._restore_old_runtime` omitted the existing
`require_recovery_checkpoint` flag. The shared restore invocation now sets it
to true; temporary candidate preflight remains unchanged. This is D0,
Contract Delta NONE, not a new recovery policy or permission.

The existing source-restore regression now checks the required flag and both
success/refusal outcomes under worker exclusion: refusal returns
`data_restore_failed`, does not invalidate continuity and does not start the
Gateway; success retains the existing explicit continuity invalidation. Both
cases failed before the fix. Source-update, backup-checkpoint and
update-settlement-hot-path groups then pass **65 tests in 0.97s**; Ruff and diff
checks pass. The source-update flow already stops the candidate before rollback
and creates its authoritative activation archive after stopping receipt producers
under the settlement barrier. These are source/mocked-test facts, not permission
to run an update or proof of crash-safe rollback. No real source reset, install,
restore, Gateway operation, wheel/pkg change or credential access occurred.

Next source-layout audit candidate: interactive source-update schema-change
detection still names `aletheia/storage/migrations.py`, while Scheme A places it
under `core/aletheia/storage/migrations.py`; prove the affected decision/test
before changing it. Packaged paths remain outside this work.

Interactive source-update schema detection now matches the current
`core/aletheia/storage/migrations.py` path. The existing no-restart regression
failed before this one-line correction because execution incorrectly reached
ordinary smoke instead of the schema refusal. The five schema-selected update
tests pass after the fix (**5 passed, 60 deselected in 1.21s**), covering refusal,
candidate failure audit, failed source restoration and backup-before-restart.
Ruff and diff checks pass. No compatibility path or new update mechanism added.

Verification incident: the initial broader schema selection was terminated
(exit 143), not passed. With the faulty detection, a test unexpectedly reached
the real `_restart_gateway` and launched local processes despite the intended
mocked test boundary. The shared clean-pull test helper now fails immediately
unless a test explicitly fakes restart. The spawned Gateway/Frontdoor/backend
processes were terminated and their absence checked. A launchd job was observed
not running with `runs=1`, bound to
`/private/tmp/aletheia-3134-ops-move.aBrn10/service/com.aletheia.trading-os.gateway.session.plist`;
that exact job was unloaded and absence rechecked. The temporary data directory
is retained, not reused as clean validation evidence. This was an unintended
local runtime action, not Fedora deployment or runtime qualification, and must
not be described as a purely mocked initial run. No wheel/pkg work or handoff
edit occurred. Future focused checks use a fresh isolated data root.

Candidate-import follow-up: `auto._candidate_preflight` previously prepended only
the candidate root, while `target_fresh._run_json_script` relied on cwd/inherited
environment. Neither prioritized Scheme A's `core/` source. A real fresh-child
regression with temporary candidate and stale sentinel packages imported the
stale package before the fix; the candidate preflight path assertion also failed.
Both child entrypoints now prepend candidate `core/` and root before inherited
PYTHONPATH. The shared target-fresh helper copies the supplied environment, so
callers are not mutated. Root remains necessary for concrete Extension imports;
no legacy package facade or install step was added.

Source-auto and target-freshness tests pass **57 cases in 0.91s**, isolated data
under `/tmp/aletheia-3134-update-guards.AuRqwa`, with Ruff and diff checks passing.
The real child imports only synthetic sentinel code; update/Git/restore/runtime
side effects stay mocked. Gateway process and launchd job absence was rechecked.
This proves candidate import precedence for the covered helpers, not a deployed
update, absent-candidate fallback safety, or final-head/global DoD completion.
Remaining source-layout references require caller-level classification; packaging
and wheel work remain excluded.

Missing-candidate follow-up: the real sentinel regression also proved that an
absent candidate package fell back to the stale environment. Both child scripts
now check the candidate `core/aletheia/__init__.py` before any application import
and raise `candidate_source_missing` when absent. The target-fresh regression
tests both presence and absence; the captured auto-preflight script is also run
against an absent candidate plus a harmless stale package that raises if loaded.
It refuses before loading that package. This is a source-presence check, not a
cryptographic Git-tree or whole-dependency provenance proof.

Auto-update and target-freshness groups pass **58 tests in 0.96s**, with Ruff/diff
checks passing and no Gateway process or session job observed. No real update,
restore, install, packaging or credential operation occurred. The remaining
`git_ops.version_at_ref` Git-object query still names the old package path;
because this reader also inspects historical rollback commits, classify its
historical-read requirement before choosing current-only versus historical
decode handling. Do not introduce a runtime package facade for that reader.

Git-object version reader now inspects the requested commit's tree and reads
`core/aletheia/__init__.py` when present, otherwise the historical
`aletheia/__init__.py` object. This historical decode is needed because actual
HEAD `b1cfd7a7573af925520c85b86c1d043dc3e7cb58` still contains the old path;
read-only invocation returns version `0.8.6`. No checkout/import, runtime facade
or alternate live implementation is involved. A failed tree query or failed
selected-file read returns unavailable rather than trying another version file.

Tests cover current-only, historical-only, both-path and no-path trees, plus tree
and selected-object failure with no fallback. Auto-update and target-freshness
groups pass **64 tests in 0.98s**, isolated data; Ruff and diff checks pass.
Current-path scenarios are deterministic Git-command tests, not a final committed
Scheme A tree. No real update, install, Gateway, wheel/pkg or credential action
occurred. This closes the reviewed version-path seam; global physical ownership,
final committed-head validation and Fedora evidence remain incomplete.

Post-Ops/update architecture revalidation: current working base remains
`b1cfd7a7573af925520c85b86c1d043dc3e7cb58` with 927 porcelain entries at
inspection. Existing architecture fitness passes **136 tests in 92.36s**.
Entry execution projection and Universe Fast Trigger tests pass **101 tests in
1.04s**. These are focused current dirty-tree results, not final-head acceptance.

EntryPlan owner audit identifies a concrete remaining semantic-owner gap:
`entry-plan-contract.md` section "Plan authority and leg accounting" assigns
aggregate terminalization to the watcher, but
`execution.entry_plan_execution.execute_entry_plan` also calls
`transition_entry_plan` directly in its STOP/DONE branch. Its only current
production caller is the watcher's `_execute_entry_plan` via `asyncio.to_thread`.
The watcher already applies the shared pure `resolve_entry_execution` result.
Therefore shared repository CAS alone does not prove one high-level owner.
Do not claim that multiple callers are automatically acceptable.

The watcher also handles expiry before that projection branch, and its
pre-Order invalidation holds a transaction, checks Order/Proposal/claim evidence,
and routes existing Order cancellation through Gateway. These distinctions
must survive convergence. Literal SQL search finds current EntryPlan/Generation
writes in repositories and historical migrations only; it does not certify
arbitrary dynamic writes. Next bounded mutation is to prove and remove the
execution-side aggregate terminal writer against watcher regression tests,
without altering cancellation, unknown outcomes or historical states.

EntryPlan aggregate-owner repair: removed the STOP/DONE repository transition
from `execute_entry_plan`. It still returns the same directive/status to its
watcher caller; the watcher alone persists aggregate terminalization from the
shared facts. Three new behavioral cases (filled/cancelled/failed) first failed
because execution changed the durable row. After deletion they verify the row
is unchanged by execution, then invoke the actual watcher loader and verify its
terminal write. No new writer, forwarding function or lifecycle was added.
This is D0 against the existing Plan authority Contract, Delta NONE; order/Risk,
cancel/reconcile logic, credentials and historical states are unchanged.

Four-file execution/watcher/projection/EntryPlan-spec regression initially had
207 passes and two spec failures: those specs lacked explicit Bitget stream
composition after Extension separation. Adding that test-only binding to the
two relevant specs restored their intended exact-venue/market-quality setup,
without loosening absent-safe production behavior. Final group: **209 passed in
2.35s**, 28 spec claims; Ruff and diff checks pass. No real exchange or Gateway
operation. Gateway's `_fail_entry_plan` preparation-failure calls remain a
separate consumer requiring semantic classification; this bounded deletion
does not alone prove every EntryPlan/Generation writer or global DoD.

Gateway Plan-owner follow-up: the seven `_fail_entry_plan` callsites combined
ordinary Proposal rejection/error with frozen-price authority revocation.
Removed generic Plan `failed` writes after Proposal/Order outcomes; the watcher
derives aggregate terminal status from those existing durable facts. Retained
only the exact pre-Order `entry_plan_price_invalidated` branch, under its
existing transaction, as `_invalidate_entry_plan_price`. This is immediate
invalidated-authority semantics, not a second aggregate outcome writer. Its
existing spec asserts `invalidated` and the exact terminal reason; deleting it
would have changed that distinct accepted meaning.

The new Gateway-rejection regression first failed on its parent-Plan row change;
it now verifies Proposal rejection is preserved while the Plan remains unchanged.
Execution/watcher/projection/EntryPlan-spec/Gateway-constraint verification
passes **217 tests in 2.29s**, 28 spec claims, including the price-invalidation
case. Ruff and diff checks pass. D0 / Contract Delta NONE: no Risk rule, order
submission/cancellation, unknown-result recovery, credential or historical
rewrite changed. No live/runtime operations were executed. Remaining work is
Generation owner-token/publication and dynamic-write proof, not another generic
Plan failure helper or completed global DoD claim.

PlanningGeneration owner-token follow-up: current production claim, hypothesis
binding and Plan publication callers converge in `agent_shell.entry_planner`;
repository publication rechecks owner/current-generation identity in the same
transaction as the Plan insert. Extended the expired-claim restart regression:
after takeover, the old owner cannot bind the same frozen input, release the
new claim, or publish a Plan; the complete generation row stays unchanged.
No new lease-expiry semantics or production state machine was introduced.

The focused first run had seven failures among ten selected cases: the added
snapshot assertion included the `_plan` fixture's own input write, and six
cancellation cases lacked explicit fast-stream Extension binding. The test
fixture now binds the supported Bitget stream without constructing/connecting
it, and freezes the assertion snapshot after fixture setup. The ten cases pass;
the complete Planner/data-collector/macro-liquidity group passes **156 tests in
3.99s**. Existing Coinalyze/FRED checks prove own-reference-only resolution,
missing-key no-HTTP behavior and error sanitization with mocked credentials.
No actual keys, online acquisition, Gateway start, wheel/pkg operation or
handoff-file update was used. These are dirty-tree local checks at base
`b1cfd7a7573af925520c85b86c1d043dc3e7cb58`, not final-head/runtime qualification.

RiskDecision owner follow-up: current production `evaluate_proposal` calls are
the Gateway preflight and post-normalization authoritative evaluations. Both
route through `risk.engine._persist_risk_result`; its authoritative branch uses
the repository immediate-transaction lookup/insert, protected by the partial
unique snapshot index (Proposal/hash/evaluated quantity/policy/attempt). The
outer Risk transaction includes final filter evidence and pre-Order reservation.
Preflight evidence does not authorize broker submission and is not a competing
Risk owner. RiskDecision table references outside these owners are reads,
schema/index construction or shared audit/retention protection; no concrete
Extension/Ops RiskDecision constructor or literal table writer was found.
This bounded caller/table-reference audit is not proof against arbitrary
future dynamic SQL.

Extended the existing successful Risk/filter atomicity regression to repeat
evaluation and assert the same decision ID, unchanged complete Risk/filter/
outcome rows and one reservation. The failure regression continues to prove
rollback and no broker/order/position on evidence failure. Risk unit/spec and
pipeline Risk/execution integrity tests pass **172 tests in 3.46s**, with two
existing strict Serenity tripwires xfailed (separate executor and school-isolated
PnL), 123 claims loaded. Neither tripwire grants authority to build those
capabilities. Ruff and scoped diff checks pass. No production Risk policy,
credential, execution, schema or runtime change was made in this test-only
batch; global final-head and operational qualification remain unproven.

Order owner follow-up: moved Scheduler's `_claim_observed_tpsl_close` into
the existing Execution Coordinator as `claim_observed_tpsl_close`. The Scheduler
now delegates this atomic Order creation/Position close-claim operation;
its old implementation is deleted with no forwarding alias. Function text is
unchanged except the name and removal of four imports already available in
Coordinator. Exact account proof, existing-claim identity validation, pending
claim conflict handling and immediate transaction remain unchanged. This
records an already-observed TPSL execution; it does not submit a new order.

The current acknowledgement writer is `finalize_exchange_acknowledgement`,
called by Coordinator and guarded against mismatched venue IDs, lost close
claims and overwriting private-fill progress. State transitions remain with
repository `transition_order`; reason-only recovery uses its dedicated CAS,
while metadata updates cannot set lifecycle fields. Adoption and observed
external-close creation remain distinct provenance paths, still to include in
the complete Order producer audit rather than declaring every Order writer
converged from this move alone.

TPSL private-materialization specs, exchange-submission integrity and storage
tests pass **158 tests in 2.12s**, 13 spec claims, including account rotation,
unproven old claims, missing private fills, unknown submission retry suppression
and real temporary-DB acknowledgement/materialization races. Ruff and scoped
diff checks pass. No Risk/credential semantics, real exchange call, Gateway
start, deployment or historical mutation was performed. This implements the
accepted #3134 ownership boundary, not a new lifecycle or runtime qualification.

Observed external-close ownership follow-up: moved
`outcome_closure._materialize_observed_close_chain` into the existing Execution
`exchange_materialization.materialize_observed_close_chain`. Outcome closure
no longer constructs/inserts Orders or Fills; its existing callback invokes
the materializer inside the same canonical close transaction. The old function
and its Order/Fill imports are removed with no forwarding alias. Exact source
comparison verifies unchanged function text apart from the name and redundant
self-import. Remote-order matching, adopted provenance, account-scoped Fill IDs,
same-ID conflict rejection, and all-or-nothing close behavior are preserved.

External-position adoption remains a distinct operation in
`position_reconciler.adopt_position`: it records an already-existing exposure,
not a Risk-approved new trade. It rechecks existing Position/native unresolved
entry ownership in the insertion transaction, labels its Proposal/Order/
Position as adopted, and creates no private Fill. This must not be relabeled
as native execution or merged with verified private-close evidence merely
because both create an Order row.

Outcome-closure tests, adopted-position specs and Position reconciler tests
pass **176 tests in 4.40s**, 37 claims, including unchanged durable Fill conflict
rejection, closure failure rollback, incomplete-cost handling and adoption
without exchange submission. Ruff and scoped diff checks pass. No economic
policy, schema/history, external network or runtime action changed. These
remaining source distinctions are explicit in the writer inventory, not new
execution authority or final-head/operational DoD proof.

DoD/credential follow-up: the current Issue still requires all fifteen DoD
items; the checkout has 928 status entries and no final committed head for the
migration. Recent owner fixes do not close the older matrix's global/runtime
gaps. A current raw-credential caller scan identified an actionable UI boundary:
`ui._model_provider_usable` received the raw model key merely to test presence.
It now consumes the existing `get_secret_fingerprint`, as Doctor/router already
do, and exposes only the unchanged boolean/setup-required projection. No new
secret service, access grant or fingerprint response field was added.

Credential management routes, Telegram, remote-control authentication and Live
credential-revision code remain separate raw-secret consumers requiring scoped
classification/convergence; this UI repair is not global credential-isolation
proof. Existing UI test now asserts absence of its raw getter plus configured
and absent fingerprint behavior. Model profile tests preserve retired-provider
and historical OAuth-reference rejection. One initial group failure was the
Overview success fixture with no resolved account; it now explicitly selects
its demo identity, while the separate unavailable-account regression remains.
Production unresolved-account behavior was not changed.

Live credential-revision follow-up: `trust.live_unlock._credential_revision`
now requests only existing `get_secret_fingerprint` results; removed its raw
getter import and duplicate `_secret_digest`. The SHA-256 field digests, ordered
reference list and final JSON digest remain byte-equivalent to the former
algorithm. This does not change Live unlock, account selection, configuration
revision, expiry or rotation invalidation semantics. Three broker-specific
regressions failed before the new boundary existed and now prove exact old
revision equality, scoped references and missing-field rejection. Updated six
test files to supply fingerprint fixtures using the existing hash helper.
Live trust/gate/security/EX-04 and live specs pass **86 tests in 3.38s**, with
one existing LU-C05 Serenity-isolation xfail and 24 claims. Ruff and diff checks
pass. No actual keys, account requests, Live unlock, data or runtime operation
was used; credential administration and transport authentication remain open
consumer classes, not covered by this revision-only repair.

Credential-status projection follow-up: the existing secrets module now owns
`get_secret_status(reference)`, returning only the existing configured/last4
projection for that one reference. Bitget/Binance/OKX status helpers no longer
receive raw values or import the raw getter. Model `_key_status` uses the same
projection and retains its existing `****` presentation for configured short
values. This deletes repeated route-level extraction; it adds no enumeration,
secret-storage implementation or API field. Missing/empty/short/normal synthetic
cases failed before the helper existed, then passed. Secret/model-profile/Live
trust tests pass **128 tests in 3.37s**; Ruff and scoped diff checks pass.

Model mutation's remaining raw reads are specifically previous-value capture
for replacement/deletion rollback. They were traced and intentionally not
removed in this status-only batch: deleting them would lose rollback safety.
They remain a credential-boundary seam to converge inside the existing secret
store, not accepted Core raw-key authority. No real credential was read or
modified, and no account request, runtime action, wheel/pkg work or handoff
update occurred. Global credential and final-head DoD remain incomplete.

Model rollback ownership follow-up: replacement/deletion now use the existing
secret module's single-reference `restore_secret_on_error` context. Old values
remain inside that module and are never yielded to routes; the duplicate
route-level snapshots/restore loops and Model raw `get_secret` import are
deleted. Existing write-only request handling, explicit deletion confirmation,
profile verification and audit remain unchanged. Exceptions restore the prior
value (including known empty) or remove a newly introduced reference; successful
operations retain the change. This is exception recovery, not crash atomicity
or a new cross-resource transaction/serialization claim.

Six synthetic previous-value/success/failure cases failed before implementation;
existing profile creation/update/delete failure regressions continue to check
rollback and preservation of unrelated historical OAuth references. No real
credential store was accessed. Credential-status and fingerprint interfaces
remain the only stored-secret reads used by Model route projections. Incoming
write-only credentials and actual transport authentication still require their
own authority classification; this does not certify global credential isolation.

Remote-control/Telegram credential follow-up: Settings' token-protection
presence check now uses existing fingerprints instead of receiving raw values.
The primary remote-control then legacy tunnel-reference lookup order is
unchanged; missing both still rejects without saving config or success audit.
Three synthetic cases failed at the raw getter before the two-line repair.
Remote network/tunnel-auth/settings honesty tests pass **63 tests in 2.20s**.
Actual frontdoor/backend authentication and readiness credentials were not
changed or exercised against a running service.

Telegram health is a real credential-bearing transport, not a presence check.
Its upstream rejection `description` was returned verbatim despite exception
sanitization. A synthetic rejection carrying the token reproduced a response
leak; rejection now returns stable `request_rejected`, matching the existing
no-token-echo invariant. No Telegram authorization, command or token scope
changed. Optional transport placement remains unresolved and is not certified
by these two bounded credential repairs. No real token/network/runtime or
wheel/pkg/handoff operation occurred.

Telegram call-chain follow-up: API lifespan owns optional bot startup/stop;
notifications instantiate it for delivery under existing configuration/category
gates. The bot mixes credential-bearing polling/send with Core pause/resume,
kill confirmation and account projections. Moving the whole class into an
Extension would move those command responsibilities too, so no blind package
move was performed.

Found a demonstrated account-projection gap first: `/pnl` called
`compute_portfolio()` without a mode, whose current implementation aggregates
all modes. Its old mode-scoped test did not assert a mode argument. The command
now uses the existing selected-execution-mode resolver, passes that exact mode
and returns unavailable without any portfolio read when unresolved. Four
test-first cases failed before the repair (two selected accounts, incomplete
economics, no selected account). No economic formula, trading permission,
transport or notification cadence changed. Telegram's optional transport/Core
command separation remains an explicit unfinished boundary.

Telegram logical-owner split follow-up: `telegram.commands.TelegramCommands`
now owns the existing command dispatch, fresh authorization read, pause/resume
delegation, projections and per-session kill-confirmation state. Method bodies
were moved unchanged; `TelegramBot` composes one handler and retains only
transport/configuration/polling/retry duties. Old bot command methods were
deleted rather than forwarded. Command tests target their owner, while polling
tests replace the composed handler. Additional mocked kill tests cover wrong
chat, expiry and one-time consumption without activating real trading state.
This establishes the logical seam only: credential-bearing bot transport and
health still require optional-Extension compartmentalization and composition
wiring. No new credential scope, lifecycle, framework or packaging was added.

Telegram transport compartment follow-up: concrete polling/send and getMe
health implementations now live in `extensions/telegram`; the old Core bot
file is deleted. Existing CLI/API composition entrypoints bind the finite
transport factory and health callback in `aletheia.telegram`. Core notification
delivery and API health consume that binding without importing the concrete
adapter or resolving tokens. Missing transport yields no notification/poller
and explicit `adapter_unavailable` health. API lifespan remains the sole polling
task owner; send-only instances do not start a loop. Protocol and command bodies,
rate limits, delivery deduplication and retry/quarantine behavior are unchanged.
Architecture checks now cover Telegram direct-storage/lifecycle imports, Core
raw-secret imports and fresh-process API/CLI composition with the extension
present or absent, while forbidding credential resolution during composition.
This relocates existing own-token authority; it grants no new credential scope.

Remote Control inbound credential follow-up: Frontdoor and backend guards
previously each resolved the same primary/fallback secret and compared the raw
value. Both now call `config.secrets.verify_remote_control_token`, which keeps
the stored credential inside the Secret Store and returns only None (missing),
True (matched) or False (denied). Primary-reference precedence, missing-token
versus mismatch 401 responses, configuration gating and operator-service bypass
remain unchanged. Non-ASCII presented headers are denied rather than raising
from `hmac.compare_digest`. Tests cover primary/fallback precedence, missing,
wrong/empty/non-ASCII candidates, and both real ASGI guard paths with synthetic
headers. Outbound local pause/resume HTTP transports still require the token
in their request header; they were not replaced with a misleading presence
check or stripped of authentication. Frontdoor config-error behavior is also
unchanged and is not claimed as newly proven fail-closed behavior.

Frontdoor configuration-failure follow-up: current `_remote_auth_check` returned
allow on `load_config` exceptions, reaching rich status/Doctor even though the
remote-auth requirement was unknown. This contradicted the existing private
diagnostic/default-deny boundary (also specified by #398); the code comment was
not treated as authorization. The exception branch now returns a stable 503
without config details, before private probing/proxying. Normal configured-local
access, valid-token access and public routes are unchanged. Two test-first ASGI
cases reached forbidden private work before the fix; after it both reject and
retain static shell/livez plus mocked healthy readiness. This proves only the
exception branch, not every possible malformed/defaulted configuration or any
Fedora runtime outcome. No config writes, token rotation or auth scope expansion.

Capability-selection audit follow-up: Risk rule 9a/9b in `risk.engine` still
branches on four OKX/Binance persisted modes to skip global allowed-exchange
and account-mode checks. `risk_engine_contract.md` explicitly retains the
self-contained exemption. Replacing it with uniform UTA-account checks would
therefore change admission policy, not merely replace a name selector. Current
Bitget composition already supplies exact demo/live identities, so the old
comment about deriving wire mode from global `exchange.mode` is not sufficient
evidence to choose a new Risk policy. This branch remains unchanged pending
the owner's requested decision: preserve current exemptions as an explicit
account-configuration property, without unifying/removing them in this batch.
Focused Risk baseline: 22 passed; this is not proof of all four exemption cases.
No new Risk capability or approval was inferred from these green tests.

Independent next candidate: startup behavior gate duplicates Bitget profile
validation in `_check_bitget_profile` and selects the active warning via a
substring search. Config schema already validates the known profile set and
Extension profile tests exercise it; any convergence must preserve the existing
non-blocking Experimental warning while removing concrete profile logic from
Core. That warning must not simply be deleted to satisfy an import/name check.

Startup profile ownership follow-up: concrete profile validation/warning moved
to `extensions.exchanges.bitget.profile_config.configuration_diagnostic` and
is bound by the existing UTA provider configuration. Core startup reads an
optional `(name, ok, detail)` note and itself fixes `blocking=False`; there is
no new lifecycle, registry or permission. Existing `bitget_profile` name,
Classic/Experimental warnings and invalid-profile result are retained. The old
Core helper is deleted with no alias. Optional absence omits only this
adapter-specific note; callback failure yields a sanitized unavailable note,
not startup authority or a blocking check. Three test-first absent/warning/error
cases failed before the change; startup/profile regression passed 66 cases.
Risk rule 9a/9b and its outstanding owner decision remain untouched.

Update/Doctor name-selector follow-up: auto-update's exact Bitget Demo +
external_exchange + live-disabled deployment gate is a qualification restriction,
not interchangeable Core account semantics. It was not generalized to all Demo
identities. Its regression now explicitly rejects valid frozen `okx_demo` and
`binance_testnet` names (not just obsolete/invalid spellings), and independently
rejects boot commit, current commit or version mismatches despite healthy status.
This is mocked gate evidence, not deployment proof.

Doctor `_probe_credentials` still always inspects three Bitget Demo references,
regardless of selected account. UTA `resolve_uta_identity` explicitly works
without complete credentials; its fingerprint/generation therefore cannot
replace a credential completeness diagnostic. `build_uta_registry` excludes
incomplete accounts but discards the partial count, so using only registry
membership would also lose existing partial-configuration evidence. Next scoped
repair should obtain selected-account configured/required counts through the
existing Exchange boundary, with no raw credential in Core and no inference of
network usability from presence. No Doctor behavior was changed in this audit.

Doctor selected-account credential follow-up: `_probe_credentials` now resolves
the existing selected execution identity and requests configured/required counts
through the UTA provider binding. Concrete scope/field selection and secret reads
remain in Exchange composition; no client is constructed. Core reports the one
`creds.exchange` result: unresolved account is informational, unavailable adapter
or inspection is a warning, zero configured fields blocks, partial warns, complete
is configuration-only OK with reachability explicitly unverified. Model credential
diagnostics remain separate. Old fixed Bitget references/result name are removed,
not aliased. Count validation rejects negative, overfull, empty-required or bool
payloads. Six test-first Doctor cases failed before implementation; full Doctor
and UTA manager regression passed 118 cases, including exact own-account reference
reads for Bitget/OKX fixtures. No real key, network, Live or Risk policy operation.

Model credential diagnostic follow-up: default-provider `creds.llm` remains a
valid diagnostic for the runtime's separate default route, but it no longer
stands in for explicit task tiers. Doctor now uses the existing
`get_provider_for_tier` resolver and the same credential-check implementation
to report `creds.llm.fast` and `creds.llm.deep`, including each selected model.
Missing selection does not fall back to default; a configured default cannot
hide missing keys on the selected tier endpoints. All notes remain configuration
facts, not inference proof. The new regression failed before implementation;
the complete Doctor suite passed 95 cases after the fixture patched the resolver's
actual config import site. No provider construction, network or routing changes.
The owner has now explicitly approved retaining Risk self-contained exemptions
while replacing the name branch with an explicit account property; that formerly
held branch is runnable, without permission to unify/remove the exemptions.

Approved Risk binding follow-up: the existing account identity registry now
declares `AccountConfigBinding.GLOBAL` for Bitget demo/live and
`SELF_CONTAINED` for the four existing OKX/Binance identities. Risk rule 9a/9b
consumes that property instead of a concrete mode-name exemption list. Unknown
identities default to no exemption and are separately rejected by existing
mode validation. This property is code-owned policy metadata, not a user/adapter
permission switch, and changes no credential wire behavior or persisted schema.
Six full preflight fixtures passed before the edit and after it with exact
existing allowlist/mode-mismatch results. Synthetic property overrides prove the
rule follows the property rather than a second hidden list; unknown/legacy/
noncanonical names do not inherit exemptions. Focused Risk + contract regression:
128 passed, 2 existing Serenity-isolation xfails. Live unlock, daily-loss/brake,
orders, policy thresholds and rejection strings remain unchanged. The earlier
Risk decision hold is resolved by the owner's explicit approval and this batch.

Native protection boundary follow-up: Scheduler encoded canonical Position
symbols with `to_bitget` before calling the Core native-protection reader. The
conversion now occurs inside the Bitget reader immediately before the same
client call; Core passes the canonical symbol. Exact wire argument/order id/
market/account identity are unchanged. The adapter test failed before the move;
TPSL private-materialization, native TPSL, UTA-v3 and manager tests passed 65
cases. A structural guard forbids venue symbol converters in the Core TPSL body.
Plan history currently maps a parent protection plan to executed child order ids;
ordinary UTA fill queries return fill evidence with time/history requirements.
Those are distinct questions, so no native reader was deleted merely because
both mention orders/fills. Full CCXT replacement feasibility is not established
by this local ownership repair; no online/native-endpoint capability claim is made.

Remaining symbol-converter audit: Core `to_bitget` calls now occur in legacy
snapshot-key reads (`price_feed`, `market_data`, counterfactual historical
lookup), not outgoing requests. They were not mechanically deleted. A separate
freshness ambiguity was reproduced with mocked repository rows: requesting
`usdt_futures` with only a fresh spot snapshot returns that spot row and a Risk
age under 120 seconds, while `get_latest_exact_market_snapshot` for the same
venue/market returns None. This is timestamp acceptance, not proof that a full
Proposal would pass every Risk rule.

`PL-MD-07` explicitly requires the cross-market fallback for historical spot-only
databases, while `PL-MD-09` only prefers a typed row when present. Thus removing
fallback is a Risk admission change, not the already-approved account-binding
representation change. Owner decision requested: use the existing exact
venue/market snapshot reader for Risk only, preserve historical display reads,
age thresholds and account exemptions, and update the explicit fallback claim.
The owner subsequently approved exact venue/market freshness. Risk now reuses
`get_latest_exact_market_snapshot`, with venue resolved from the Proposal's
execution mode at both Live and Demo call sites. PL-MD-07 rejects cross-market
and cross-venue substitution; PL-MD-09 keeps exact fresh-perp coverage. Historical
display fallback, age/future-skew thresholds and account exemptions are unchanged.
Six-mode full Risk regressions check the freshness token with and without an
exact snapshot. Test prerequisites now label their synthetic Bitget snapshots;
no production snapshot default, data migration or real trading action changed.

The Candidate single-writer AST guard now scans Extensions as well as Core and
rejects direct Extension calls to the Core Candidate publisher, including calls
with keyword-only arguments. Focused Candidate/collector guards: 12 passed.
This is direct-source coverage, not a complete transitive writer proof.

The five focused Risk/market-data files passed 207 tests with 2 existing xfails.
Expanded local Risk validation including middle-state integrity returned 223
passed, 2 existing xfailed and 2 failures at the pre-Risk order-constraints gate
(`exchange_extension_unavailable`); those failures do not exercise freshness and
were traced to two tests lacking explicit executable order-rule prerequisites.
A scoped synthetic rule fixture now lets those tests reach their original
post-Risk terminal rejection/cleanup assertions; no production behavior changed.
The six-file combined regression now passes 225 tests with 2 existing xfails
(113 spec claims), including both reservation tests. Ruff and diff checks pass.
Ruff, docs governance and diff whitespace
checks passed. No merge, deployment or runtime qualification is claimed.

### HTTP Model Endpoint verification — 2026-09-14

Direct-network diagnostic audit follow-up: Doctor `_probe_network` emitted the
complete configured proxy URL in HTTP-success and SOCKS diagnostic details.
Those unnecessary URL fields are removed, and config-read exceptions report
only their class. Synthetic authenticated-proxy tests cover both branches
without opening sockets; Doctor/network CLI verification passes 103 tests in
6.92s, with Ruff/diff checks passing. No real credentials were inspected.
The network probe target and connectivity pause/resume behavior are unchanged.
Remaining concrete protocol seam: `agent_runtime.connectivity` owns the venue
public-probe URL map, while Settings/Doctor independently probe Binance.
Settings and exchange-probe raw exception strings were subsequently reproduced
as exposing a synthetic authenticated proxy URL (2 failing regression cases).
Both now return exception class only, preventing that raw message from reaching
the API and connectivity status/escalation consumers. Connectivity, config
routes, Doctor and network CLI verification passes 227 tests in 14.63s;
Ruff/diff checks pass. Reachability, timeout and pause/resume semantics are
unchanged. This is not a claim that every network diagnostic or
credential-egress path has been certified.

Unknown-venue probe follow-up at the same working base: an unmapped exchange
previously returned `reachable=True` without any HTTP observation, and the
combined probe used that result to override a failed external-network probe.
An isolated regression reproduced the false success before the fix. Missing
probe mappings now return `reachable=False`, no latency, and
`exchange_probe_unavailable`; the independent external result remains intact.
Connectivity/startup-gate tests pass 54 cases in 4.03s; Ruff/diff pass.
No known-venue URL, timeout or recovery threshold changed. This closes a false
availability branch, not the remaining concrete public-protocol ownership gap.

Public-probe ownership follow-up: the venue URL map and HTTP request now live in
`extensions/exchanges/composition.py`, injected by the existing Bitget UTA
composition root. Core connectivity delegates the probe but retains all
counters, pause/resume and external-network aggregation. The UTA boundary
returns explicit unavailable when the Extension is absent and sanitizes raised
exceptions. No generic CCXT-to-venue import was introduced, no new lifecycle or
file was created. Known venue URLs, timeout=8 and HTTP-200 semantics are locked
by six synthetic adapter cases; absence has a no-HTTP regression. Connectivity,
startup gate and UTA manager pass 89 tests in 3.69s; Ruff/diff pass. Settings and
Doctor's separate network-test target remains a distinct pending seam. This
does not promote frozen venues or prove private-account/trading reachability.

Settings egress convergence: `/network/test` now consumes the existing
independent external-network probe instead of maintaining its own Binance
request. API keys remain `reachable`, integer total-elapsed `latency_ms`, and
sanitized `error`; reachability now means egress, not exchange entitlement or
account capability. It uses the existing five-second overall bound and non-5xx
transport-reachability criterion. Two route tests prove delegation and stable
response shape for success/failure. Config routes/connectivity/Doctor/network
CLI pass 237 tests in 13.58s; Ruff/diff pass. Doctor's explicit-proxy/direct-exit
comparison answers a different question and was not merged into this probe;
its SOCKS message now says unprobed here rather than claiming verification.
No new abstraction, endpoint, or pause/resume rule was introduced.

Probe report boundary validation: five malformed adapter-result cases first
reproduced either a consumer exception or false success (including string
`false`, wrong venue and contradictory success/error). Core now requires an
actual boolean, matching venue and, for success, finite nonnegative latency
with no error. Invalid reports become `invalid_exchange_probe_result` and may
not replace independent external-failure evidence. Seven negative cases and
six known-venue wire cases through the Core boundary are covered; the combined
connectivity/startup/UTA group passes 96 tests in 4.02s. Ruff/diff pass. No new
DTO framework or recovery lifecycle was added; valid adapter behavior is unchanged.

Failure-detail follow-up: the boundary still accepted arbitrary adapter-supplied
failure strings, which flow to connectivity logs and stall notifications. A
synthetic authenticated-proxy result reproduced that path. The Extension now
lets request exceptions cross the callback so Core reduces them to their class.
Returned failures are limited to a bounded HTTP status with finite nonnegative
latency or the explicit no-mapping token; arbitrary strings, absent errors and
invalid latency become `invalid_exchange_probe_result`. The same group now
passes 99 tests in 3.83s; Ruff/diff/docs pass. Existing exception-class and HTTP
status diagnosis remains observable without carrying raw adapter messages.
The demonstrated ownership regression is now mechanically guarded: Core
connectivity cannot regain the three concrete venue domains, and its exchange
probe function cannot call the HTTP client directly. Related architecture and
optional-composition selection passes 73 tests (71 deselected, 66.92s). This
guard is deliberately scoped to the moved seam rather than a new lint taxonomy.

Remaining selector audit at working base `b1cfd7a7573af925520c85b86c1d043dc3e7cb58`:
Settings `set_exchange_markets` still validates concrete Bitget profile values
and advances the two Bitget account generations directly. This is a remaining
configuration-ownership seam, not proof of exchange-agnostic completion.
The existing profile/generation test group passes 24 cases; the route regression
now additionally proves unrelated OKX/Binance generations stay unchanged and
re-saving the same profile is idempotent (1 focused pass; Ruff/diff pass).
Follow-up implementation moves route profile validation/account selection to
Bitget `profile_config.profile_accounts`, bound through the existing UTA
composition function. Core retains the config lock, publication permit,
generation increments and audit ownership; no venue comparison remains in the
route. Missing Extension rejects the profile write with 503 without modifying
config, and empty/unregistered adapter account results are rejected. The config
schema still owns persisted-field validation; this does not claim all concrete
configuration names have disappeared from Core. Three-file config/profile/UTA
verification passes 139 tests, including unchanged other-account generations,
same-profile idempotency and absent-adapter non-mutation. Ruff/docs/diff pass.
No profile registry/framework, credential access or runtime operation was added.
Related architecture/optional-composition selection passes 72 tests (71
deselected, 64.25s); this is not the full final-head architecture gate.
The reconciliation name checks reviewed here
validate a specifically shaped historical private-fill close chain; the
stock-linked analysis context also has a bounded current contract. Neither was
mechanically generalized by deleting venue strings.

Re-read current #3134 canonical body and checked current model implementations.
The OpenAI-compatible Extension dispatches Chat Completions and Responses via
the official SDK; Anthropic Messages uses its official SDK. SDK retries are
disabled, endpoint credentials are explicitly scoped, and synthetic HTTP tests
exercise ambient credential/header exclusion and sanitized status failures.
Catalogue calls are bounded to four seconds; configured manual IDs remain
usable through explicit inference verification when discovery is unavailable.
`get_provider_for_tier` requires explicit endpoint/model pairs for fast/deep
and fails absent assignments instead of using the default tier.

The current dirty-tree group (`test_model_provider`, `test_model_sdk_transport`,
`test_model_catalogue_health`, `test_model_profiles`, `test_model_fallback`,
`test_retired_model_providers`, `spec_model_routing`) passes **235 tests in
4.55s**, with 4 spec claims, isolated data and `not online`. Coverage includes
selected endpoint identity, independent models on one endpoint, catalogue/manual
ID behavior, configured fallback handling, credential rollback on failed config
save, and absent retired OAuth/CLI mutation surfaces. Real SDKs use synthetic
HTTP responses; no real model endpoint or credential was exercised. Frontend
support/configuration presentation and exact final-head proof remain pending;
this is not a live endpoint health claim. No transport implementation change
was needed in this verification batch.

Frontend follow-up: API profile cards previously displayed provider type/model
but omitted the configured protocol, making Chat Completions and Responses
indistinguishable without opening the editor. Cards now render the existing
protocol field, or explicitly `protocol not configured`; no new setting or
inferred protocol was added. Four settings files pass **28 component/state
tests**, including all three protocol labels and absent protocol, exact tier
pair submission across refresh, strict API payloads, no forced clean-state
assignment, retired login controls absent, and stale health not green. The
fixture's obsolete `openai` protocol value was corrected to `chat_completions`.
This is rendered component evidence, not a live browser/runtime observation.

### Settlement complexity comparison against Issue creation baseline

Compared current working source with immutable
`6749c31b62a36a0497659cf25608025f8ab4218f` (#3135 creation baseline), accounting
for the `aletheia/` to `core/aletheia/` move:

| Responsibility | Baseline | Current |
| --- | --- | --- |
| Full-close monetary/eligibility commit | `pnl.close_position`, `outcome_closure.close_position_verified`, and `materialize_broker_funding_economics` each assembled/wrote settlement and eligibility | Initial and delayed economics commit through `pnl._commit_close_economics`; verified-exit normalization calls normal `close_position` |
| Verified-exit economic arithmetic | Separate gross/net calculation and Outcome-hook dispatch in `outcome_closure` | Removed there; source evidence normalization and exact-evidence callback remain |
| Close-event delayed monetary projection | Funding finalizer directly updated the close-event row | Shared commit updates Position and matching full-close event within the caller transaction |
| Known but incomplete economics | No corresponding known-net read projection in that close flow | Derived read fields; no matching fields in storage models/migrations |

Git's rename-aware three-file comparison reports 664 insertions and 675
deletions (net -11 lines) across `pnl.py`, `outcome_closure.py`, and
`broker_funding_settlement.py`; `outcome_closure.py` alone is +83/-217. These
counts are supporting evidence only, not a complexity metric or a claim that
the entire program shrank. The substantive reduction is three independently
assembled full-close commit paths to one shared monetary/eligibility/event/hook
boundary. Fee/funding evidence acquisition, lifecycle CAS, administrative
unavailability, partial reductions and audit replay remain because they protect
different facts. No retained funding fingerprint is treated as a new ledger.
Runtime and final-head verification remain outstanding.

### Current ordinary reconciliation convergence

Ordinary unresolved Orders now share one account-bound typed UTA workflow.
The Bitget/other-account partition selectors, shorter duplicate read/materialize
loop and its two private-read forwarding helpers are deleted. All configured
account identities receive the retained client-id recovery, exact/symbol-scoped
Fill reads, IOC terminal-quantity matching, funding read and canonical
materialization path. Funding requests retain the Order's physical market type.
Historical filled-gap recovery and verified-closed pending cleanup remain
separately bounded; their existing selection/authority rules were not widened.

This resolves the ordinary partition work described as outstanding in earlier
batch notes below. It does not promote frozen venues, install adapters, grant
trading permission, or prove runtime qualification. Twelve new cases use
normalized fake UTA reports across all six retained execution modes: matching
IOC terminal quantity ends the remainder, mismatched quantity stays partial,
and replay preserves one Fill/Position effect. The full focused reconciliation,
materialization, historical recovery, multibroker, EntryPlan, mode-identity,
client-id recovery and pending-close group passes 266 tests (76 spec claims).

Order reads now retain OrderStatusReport end-to-end inside reconciliation.
The former conversion to orderId/clientOid dictionaries was then fed through
the FillReport-only _response_rows filter, silently discarding every open Order.
Three real query/projection/caller regressions failed before this repair.
The dictionary projection and duplicate client-id detail wrapper are deleted;
detail recovery reuses get_order_status with the original Order history start.
Client-id matches with conflicting instruments/market types or multiple venue
IDs remain unresolved without binding a guessed identity. Six focused cases
cover open-list and exact-detail recovery while proving no Fill is fabricated.

The shared query_uta boundary now also checks exact Order reads before returning
evidence to callers: a selector is required before connecting, the result must
be an OrderQueryReport, and a found Order must match any requested venue Order
ID and canonical instrument/market type. Five fault cases were incorrectly
accepted before the guard. Rejection retains the existing finally-close path.
Not-found remains a valid report; client-id wire encoding remains adapter-owned
and is not guessed by Core. These checks do not claim to validate every UTA
report family or replace adapter-owned client-id translation.

Generic CCXT exact Order reads now reject conflicting normalized records
instead of choosing the last record for the same venue/client identity.
Identical duplicate records still deduplicate. Matching IDs with a different
canonical symbol/market, or a direct venue-ID response carrying another ID or
client ID, return partial with no found Order rather than complete/not-found.
Three conflict/scope cases failed before the guard. This protects adapter-
internal exact reads as well as Core query callers; it does not move venue
client-id encoding into Core. Final adapter, client-id recovery, UTA manager,
reconciliation and multibroker verification passes 212 tests (36 spec claims).

Open-order list deduplication now also marks conflicting normalized records
for the same venue Order ID/instrument/market as partial, instead of silently
claiming the first record is complete evidence. Identical duplicates remain
complete; the existing reconciliation projection rejects partial lists before
identity recovery. Four profile conflict regressions failed before the repair;
the same focused adapter/recovery/manager/reconciliation/multibroker group now
passes 220 tests (36 spec claims). This does not certify other report families.

### Current derivatives price boundary

Core price_snapshot now consumes named price/source-time facts rather than a
raw provider response. Its provider-name branch and CCXT markPrice/indexPrice
parsing are removed. The CCXT adapter owns exact source-symbol validation,
field extraction and source-clock parsing in _parse_price; Hyperliquid supplies
its mark/oracle values with the existing missing source-time semantics. Core
retains instrument/positive-finite-price validation, deterministic snapshot IDs
and downstream same-snapshot premium projection. Both production callers and
the readiness/positioning fixtures use the normalized interface; no forwarding
signature or second price writer remains.

Three adapter cases cover absent/matching/foreign source symbols, source-clock
preservation, deterministic replay ID and premium projection. A paired AST
guard rejects raw input and concrete provider/wire literals in the Core builder.
Final focused verification is 212 passed, 1 online test deselected (7 spec
claims). The earlier unfiltered run reached the existing public Hyperliquid
connectivity probe and skipped on ConnectTimeout; it supplies no online proof.
Changed-file F401/F821/F822/F823 checks pass.

The metadata follow-up removes ccxt_metadata from Core without a forwarding
entrypoint. Generic CCXT metadata assembly now lives in its exchange package;
OKX/Binance packages own the native USD-value and funding-timestamp field rules.
The existing composition supplies the metadata reader to all current/history/
comparison/primary OI paths, including the standalone snapshot parsers. Unit
proof still requires finite equality to a permitted native field; unconfigured
generic metadata keeps the unit unknown. Missing configured native rules raise
rather than silently using a generic proof. Existing normalized Core metadata
serialization and historical read/identity/dedup semantics remain unchanged.

Six rule cases cover own/cross-venue fields and generic unknown defaults; two
absence cases cover missing OKX/Binance rules. Test wire fixtures use explicit
composition-bound parsers instead of importing a removed Core decoder. Final
focused metadata/collector/comparison/price/positioning/factory tests plus Core
import and Extension storage/lifecycle guards pass 224 tests (1 focused spec
claim). Changed-file F401/F821/F822/F823 checks pass, including cleanup of the
previously noted unused imports in derivatives tests. The broader
spec_data_utilization file also exposed a Fast Stream assertion expecting
Binance implementation in its retired Core location. The following DU-S28
conformance repair resolves that separate failed check: data_utilization.md and
its claim now describe the frozen Binance Extension with no active startup
registration. The test checks the retained Extension class/protocol shape,
absence of the concrete Core class, Bitget-only registration, and unavailable
factory results for both Binance execution modes. The full data-utilization
spec/Fast Trigger group passes 104 tests (34 spec claims); four fresh-process
CLI/API startup cases also pass, including absent Bitget and unloaded Binance.
This changes no stream implementation, registration or trading authority.

instrument_from_market and funding_interval_seconds are now removed from Core
and live in the existing CCXT metadata module, unchanged in behavior. The
production caller audit found only CCXT Extension consumers. No old import
facade remains. Candidate/Funding fixtures import acquisition conversion from
the Extension, while the Core independent-process test now blocks all Extension
imports and supplies a normalized instrument directly. Six extra cases verify
valid/fractional/missing/invalid intervals, missing instrument facts and
nonfinite contract size, alongside absence of the old Core functions. The
focused derivatives/Candidate/factory and dependency guards pass 179 tests;
changed-file F401/F821/F822/F823 checks pass.

Core normalize_funding now accepts a rate and explicit parsed source time/status
rather than a raw CCXT dictionary. The sole production caller, CCXT primary
funding acquisition, reuses its existing parsed clock for both the fact and
outer result. Missing clocks stay missing; invalid/future clocks and nonfinite
rates remain invalid, and negative finite funding remains meaningful. Interval,
forecast, freshness and Candidate policy semantics are unchanged. Four real
adapter cases cover provided/missing/invalid/future clocks, paired with a Core
signature guard and existing isolated Core/forecast/nonfinite tests. The final
Candidate/facts/factory/semantics/Universe/comparison group passes 194 tests;
changed-file F401/F821/F822/F823 checks pass.

The OI/time follow-up completes that acquisition-input seam: Core
open_interest_usd and normalize_open_interest consume explicit amount/reported
value/source-time facts. CCXT extracts unified fields in the Extension;
Hyperliquid supplies its base amount to the same Core conversion primitive.
USD proof, contract/base conversion, zero/negative/nonfinite behavior and all
history/Outcome policy remain unchanged. The CCXT-shaped provider_source_time
parser has moved unchanged into the existing CCXT metadata module; Binance
positioning shares it, using the existing Core future-skew constant. No old Core
parser entrypoint remains. Six scalar/structure cases protect the boundary;
existing timestamp and adapter tests cover parsing behavior. Final derivatives,
Hyperliquid, Universe, Candidate, factory and dependency checks pass 315 tests,
with 1 online test explicitly deselected (7 spec claims). Changed-file
F401/F821/F822/F823 checks pass. This proves the reviewed acquisition seam,
not every historical projection's provider selection or the global DoD.

Historical projection/provider handling is distinct from acquisition decoding.
The ratio projection previously relabelled even explicitly supplied identities
as Binance. New Binance positioning observations now carry provider/venue in
their existing nested metadata; Core respects those facts. Only the historical
format lacking both fields retains its original Binance interpretation. An
incomplete/null explicit identity remains unavailable, never backfilled with a
guessed venue. Three cases failed before this repair; the legacy case preserves
its prior result. Tests also assert the input row is not mutated. No schema or
historical-data rewrite was introduced. The full focused facts/collector/
comparison/semantics/positioning/readiness/Universe/data-utilization group passes
245 tests (34 spec claims); changed-file F401/F821/F822/F823 checks pass. Remaining
read_derivatives_facts branches for historical source layout still require
separate review and do not prove overall provider-agnostic completion.

### Current public physical-client selection

Generic CCXTProvider no longer selects a physical client by matching Binance
names. The Binance public adapter owns its perpetual CCXT client identifier;
the existing extensions/market_data.py composition supplies primary/fallback
perpetual identifiers explicitly. Generic runtime retains lazy loop-local
creation, per-route reuse and idempotent client/session cleanup, with no new
factory framework or forwarding path. All production construction was traced
to that existing composition entrypoint. Tests that asserted Binance wire
selection now use that entrypoint without weakening their physical-client,
logical-venue or cleanup assertions.

Synthetic non-venue identifiers prove both routes use supplied facts, while
missing configured Binance public code fails before construction rather than
silently choosing generic transport. An unrelated OKX-only configuration still
constructs without that module. Factory, listings, venue health, OHLCV boundary,
comparison, Universe, history downloader, derivatives collector and architecture
checks pass 380 tests. Scoped F401/F821/F822/F823 checks pass except three
untouched unused imports in tests/test_derivatives_collector.py (lines 353,
376 and 1010 at this checkpoint); its F821/F822/F823 check passes.
Generic live-derivatives batch calls now omit the redundant instType parameter.
Installed CCXT 4.5.56 already builds the OKX perpetual request internally; no
new parameter registry or venue callback was needed. An offline regression
executes the real CCXT methods with intercepted HTTP endpoints, checks both
zero-argument unified calls and their exact generated wire requests, and closes
the provider. It failed before removal of the redundant parameters. The focused
factory/derivatives collector/comparison/facts-contract/semantics group passes
136 tests; changed-file F401/F821/F822/F823 checks pass. This verifies the pinned
local dependency path, not online exchange availability or other CCXT versions.

### Current public instrument-background ownership

Core data/symbols.py builds instrument-background/v1 from normalized asset class,
classification and reference facts; it no longer receives market_info or parses
venue wire fields. Bitget, Binance and OKX public metadata decoders live in their
own exchange packages. The existing extensions/market_data.py composition selects
the decoder and passes a catalog decoding callable into generic CCXT. OKX category
3 exclusion is venue-owned as well. The generic catalog keeps acquisition,
normalized entry assembly, shared filters and provider cleanup, without concrete
venue imports or raw classification fields. Missing concrete decoders propagate
unavailability rather than falling back; unknown venues remain unresolved.

Core applicability/rejection semantics, including the bounded Binance
stock-linked context, remain in place. No private-account profile, credential
access, persistence writer, new plugin framework or packaging initiative was
introduced. Existing focused Universe/background/attribution tests pass 92 cases.
New classification/default/missing-decoder/OKX catalog cleanup and Core/generic
wire-boundary checks, together with fresh-process optional public-data binding
checks, pass 40 cases. These are local uncommitted-source checks, not final-head,
runtime, or whole-Issue DoD proof.
After removing dead OKX normalization branches, the combined background,
Universe, attribution and new adapter regression group passes 108 tests.

### Current entry materialization ancestry repair

The Bitget match-cap close-pricing branch is an existing execution-contract
policy, not a concrete SDK import to remove by name. Its public ticker provider
did, however, escape cleanup. Core coordinator now constructs, reads and closes
that fresh provider inside one coroutine, reusing the existing _call_and_close
helper. Two strengthened regression cases failed before the repair. Final
coverage includes synchronous callers and callers already on an event loop,
exactly one close attempt, cleanup failure without altering a valid limit order,
and quote failure with cleanup plus the existing clipped-market fallback.
Clipped-close, pending-close recovery, reconciliation and exchange
materialization tests pass 110 cases (30 spec claims). Order/Risk/account
authority, price tolerance, clipping policy and owner-only parked-close release
remain unchanged. This is local lifecycle evidence, not live execution proof.

The close-pricing policy is now selected by the existing UTA capability envelope:
match_cap_close_fallback defaults to unknown; generic CCXT reports the profile
declaration, Bitget profiles declare support, and other profiles do not. Core
retains the same confirmed-cancel prerequisite, bounded limit/clip algorithm and
owner-only parked-claim release. It uses the bound account's venue for public
quotes rather than a literal venue. No new retry manager or trading permission
was introduced. Explicit capability tests cover all eight retained demo/testnet/
limited-live profiles without constructing clients. The coordinator also proves
that unsupported/unknown capability prevents the special fallback even for a
Bitget-named account. Capability/model/adapter/coordinator/clipped-close tests pass
169 cases; adjacent pending-close/reconciliation/materialization/entry/TP-SL tests
pass 114 cases. Reconciliation's venue partition remains separate unresolved work.

Reconciliation now passes canonical symbols through historical exact-fill,
client-order-id detail, current exact-fill and symbol-scoped history paths. The
premature Bitget wire encoding and its imports are deleted; the UTA boundary
continues building the normalized instrument and adapter-specific wire encoding
stays outside reconciliation. An architecture regression failed before this
removal. The two account partitions are not yet converged: the longer path also
owns identity recovery, IOC terminal-quantity checks and bounded historical-gap
recovery, so replacing it with the shorter materialization loop would lose
required behavior.

The expanded suite exposed eight stale averaging fixtures whose old Position
had no primary Order/Proposal ancestry. Those fixtures now seed an authorized,
account-bound same-Proposal primary Order while retaining the original
unverified/inconsistent price provenance. Their existing assertions still
require that a new verified fill cannot upgrade old unverified history. Final
reconciliation, materialization, historical-gap, multibroker, client-id recovery
and EntryPlan verification passes 205 tests (76 spec claims). No production
ancestry guard was weakened and historical recovery scope was not expanded.

The two ordinary reconciliation readers now invoke one shared native
materialization/failure-recording function. Duplicate entry/close dispatch,
integrity-rejection reporting, storage-failure Order status updates and audit
handling were deleted from both loops. Read strategies, client-id recovery, IOC
terminal matching and the separately authorized historical-gap materialization
call remain unchanged; this is not a claim that the account partitions are
fully converged. Twelve injected failure cases cover both entry/close dispatch
and both account-mode inputs: integrity/ValueError rejection leaves the
Order unchanged, storage failure records materialization_missing, each makes
one materializer call, no Fill is fabricated, and exception payloads do not
enter the error summary or log. The focused reconciliation file passes 28 tests.

The unused absence-only resolve_orders_missing_from_exchange helper and its
private _CCXT_LIVE_MODES selector are now deleted. CodeGraph and source caller
checks found only tests. Its recommendation to infer filled from disappearance
in an open-order list contradicted the current execution contract. Seven tests
and their local strategies asserting that obsolete predicate were removed;
ordinary OCO tests and normalized Fill-quantity properties remain. The active
trust-boundary coverage index now points to actual reconciliation's no-private-
evidence/unresolved checks. A history note records the retirement without
retaining an executable fallback. Focused OCO, property, identity, reconciliation
and multibroker verification passes 84 tests (36 spec claims). No production
data changed; ordinary read-partition convergence remains outstanding.

The shared Core entry materializer now checks Position ancestry for both durable
backlinks and automatic coalescing. Different Proposals may share a Position
only through the same Plan, distinct leg indices, matching source/instrument
facts, and equal validated persisted UTA account identities. Unrelated entries
create their own Position; an explicit unrelated backlink or a damaged same-Plan
account/Order link fails inside the existing transaction, without adding a
second writer or rewriting history. Same-Order replay remains idempotent.

The current capture test now materializes the second private Fill and replays it,
in addition to its existing mocked Gateway submission assertions. Missing or
changed primary account identity and a broken primary Order/Plan link reject
the second Fill without changing its Order or the original Position. Separate
tests cover unrelated Proposal/school/account coalescing and explicit backlink
rollback. An existing economic-overflow fixture now uses same-Proposal entry
Orders and directly proves the overflow guard, rather than relying on unrelated
Proposal coalescing. Focused local verification: 234 passed across entry capture,
materialization, unresolved exposure, Outcome ancestry, reconciliation, TP/SL,
and entry-to-Position tests. This is working-tree evidence, not final-head or
runtime qualification; global writer/DoD verification remains incomplete.

The same selector also rejects ambiguous Position ownership: it reads up to two
primary/backlink claims together instead of preferring the backlink or first
row, and does not pick the first of multiple ancestry-compatible candidates.
Three real-materializer regression cases failed before this guard and now prove
transactional no-mutation on repeated replay. The expanded focused group passed
237 tests. No historical duplicate is deleted or repaired by this check.

Before coalescing a related entry, the existing primary Order must also be
native and pass the existing repository Order/Proposal/Risk snapshot validator.
No second hash or approval validator was added. Three before-fix failures cover
unapproved primary RiskDecision, corrupt primary Proposal hash and adopted
primary Order; all reject without second-Fill/Order/Position writes after the
repair. The focused group now passes 240 tests.

### Current follow-up: observation config and Learning reviewer

Fast Stream wire ownership is now split: Bitget and Binance WebSocket endpoints,
subscriptions, parsers and connection implementations live in their respective
extensions/exchanges venue packages. Their wire bodies are unchanged. Core
universe/fast_stream.py retains FastMarketEvent/FastStreamHealth, the existing
FastMarketStream protocol, monitoring projection and pure timestamp validation.
Its existing factory map starts empty and receives factory/market capability
bindings, replacing concrete classes and the Binance-specific market branch.
Existing CLI/API composition binds only Bitget without constructing a client;
Binance remains frozen and is injected explicitly only in adapter tests. No
Core concrete-class or parser forwarding aliases remain. Fast Trigger Watch
still owns stream lifecycle. Four fresh-process cases prove CLI/API presence
and missing-Bitget degradation without client construction or Binance loading.
Focused caller/adapter/activation validation passes 194 tests; direct import,
transport boundary and fresh-process composition checks pass 15 tests. This
repairs one concrete implementation missed by direct-import-only checks, not
proof that all embedded protocols or Extension side effects have been audited.

Candidate/Serenity consumer follow-up: admission, publication rollback and
Outcome tests explicitly request the existing Bitget stream fixture instead of
depending on process import order. No global autouse binding was added. A real
Universe refresh with the binding removed reports all 20 controlled instruments
as FAST_MARKET_DATA_STREAM_UNAVAILABLE and publishes no current Candidate or
PlanningGeneration. Repeated Serenity publication with the binding removed
creates no snapshot/member/generation/PI decision and leaves its theme active.
Bound-route publication, rollback and Outcome checks still pass; the focused
five-file consumer group passes 128 tests. No production admission rule changed.

After the analytics producer moves, fresh CLI/API processes with the entire
Learning package unavailable still execute calibration/feedback API readers and
the memo reader against isolated initialized storage: they return no_data/None
without changing the connection's total_changes. This is bounded read-side
absence evidence, not a claim of persisted nonempty-report coverage or runtime
health. The explicit knowledge remediate-outcomes command is composed through
the existing main CLI, sharing the same producer bindings as scheduled work.

Reasoning-feedback aggregation and rate-limited knowledge persistence now live
in extensions/learning/reasoning_feedback.py. Core keeps the mode/cohort-validity
reader and one producer binding configured by existing CLI/API roots. Scheduler
piggyback and explicit maintenance use that binding, with no Core build function
or forwarding facade. Missing feedback leaves the independently completed memo
job intact and maintenance reports unavailable. Complete Outcome admission,
proposal-time pattern attribution, thresholds and write-failure propagation are
unchanged. Existing APIs continue reading Core without importing the producer.

Calibration computation and rate-limited knowledge persistence now live in
extensions/learning/confidence_calibration.py. Core retains persisted validity
and mode-scoped reading, scorecard semantics and a single producer binding.
Existing CLI/API roots bind it; scheduler and explicit outcome-integrity rebuild
use that same callable. Missing binding is calibration_builder_unavailable for
the job and unavailable for maintenance, never insufficient or generated.
No Core build_calibration_report forwarding function remains. Sample thresholds,
ECE/None behavior, selected-mode filtering and persistence failure propagation
are unchanged. Fresh-process composition tests cover presence and whole-Learning
absence; GET and scorecard still read Core without producer imports.

Prior-memo evaluation now lives in extensions/learning/memo_effectiveness.py;
its only production caller is the optional memo synthesizer. It reads a complete
Core Outcome cohort and requests update_knowledge_item for the prior memo's
evaluation tags, not trading/Outcome authority fields. Core memo_effectiveness
retains only pure age weighting needed by observer rendering, with no evaluation
facade. Cohort, exposure, rotation and overturn thresholds, and best-effort tag
write-back behavior are unchanged. No independent job or callback was needed.

Concrete memo synthesis now lives in extensions/learning/insight_synthesizer.py.
The original Core module retains TradingMemo types, validated persisted reading
and observer rendering; it contains no synthesis forwarding function. Existing
CLI/API composition binds the optional producer to configure_memo_synthesizer.
Core scheduler retains cadence, pause, timeout worker and job health; missing
binding records memo_synthesizer_unavailable and does not suppress independent
reasoning-feedback aggregation. Synthesis uses the same Core complete-cohort
reader and knowledge repository, preserving filters, memo identity, inference
caller, persistence error propagation and rate limit. No new schema or scheduler.
Fresh-process CLI/API tests cover whole-Learning absence and exact producer
binding; existing observer/Doctor read paths remain Core consumers.

The old render_calibration_for_pi and render_feedback_for_pi functions had no
production consumers after Operator entry-prompt retirement (learning_contract
producer table). Both have been deleted, with an absence regression replacing
the old numeric-format test for the retired calibration renderer. Report
generation, persisted API/history readers and calibration scorecard consumption
remain unchanged. No feedback was injected into the canonical Entry Planner.

Calibration and reasoning-feedback producers now propagate computation/storage
exceptions instead of returning None, and their persistence helpers no longer
swallow writes that failed. Legitimate unresolved-mode/insufficient-sample returns
remain None. Existing scheduler handling records calibration failure; explicit
maintenance rebuild distinguishes failed from insufficient. Repository-failure
tests exercise both producer/helper pairs and these caller classifications.
Persisted-report readers retain their unavailable fallback; no new retry or
activation authority was introduced.

Memo synthesis is invoked by the Core scheduler's owned async timeout worker.
The concrete producer persists a trading_memo through insert_knowledge_item;
market observer and Doctor read it. The persistence helper formerly swallowed
write errors, allowing synthesis to return a memo and the scheduler to record
success without durable output. It now re-raises after logging, reaching the
existing scheduler failure recorder. A regression uses actual synthesis and
persistence helper with a failing repository write, and verifies no job success
while independent reasoning-feedback aggregation still runs. No retry, worker,
storage owner or memo eligibility semantics were added or changed.

Confidence calibration has an active scheduled producer, a persisted-report API
reader and an explicit outcome-integrity maintenance rebuild. The API formerly
called build_calibration_report on a cache miss, causing knowledge writes during
GET /analytics/confidence-calibration. That implicit producer has been removed:
the API returns the existing no_data representation when no current report is
available. The six-hour scheduler and explicit maintenance rebuild retain the
same computation and persistence helper. A fail-on-generation route regression
demonstrated the old GET side effect before the removal. This establishes the
read/write boundary, not completed compartmentalization of calibration, memo
synthesis or reasoning feedback, which still have Core consumers.

Policy and treatment-receipt writer trace at this working-tree base:

| State / operation | Production entry and mutation owner | Evidence boundary |
| --- | --- | --- |
| ps_policy_versions creation | LessonStore.apply_reviewed_lessons is the sole production create_version caller; PolicyStore emits the INSERT and creation event inside the caller's transaction | Policy routes, diff and sync conflict detection are readers; migration creates schema only; deprecate_version has no current caller |
| observation_delivery_outbox insertion | canonical.append_delivery_receipt validates and joins/opens the Main transaction, then _insert_receipt performs identity-checked INSERT | Current producers are canonical treatment resolution, record_owner_action and the Learning global-gate refusal path; outcome seeds join the same transaction |
| Receipt acknowledgement | canonical.import_delivery_receipts updates acknowledgement/application fields only after the Lab transaction commits | Failed acknowledgement leaves Main receipt pending; retry does not authorize a second canonical exposure |
| Receipt retention | canonical.prune_observation_delivery_outbox, called by the existing runner | Bounded acknowledged rows and grace period; Lab authority additionally requires checkpoint/retry coverage and exact source receipt evidence, including verified archives |

The scan covered production core/extensions/scripts Python references, followed
by source inspection of the operations above. No legacy root currently exists.
The Main outbox is transport/provenance, not a second treatment authority; these
operations are not interchangeable writers of the same lifecycle transition.
Policy rollback/replay, receipt identity, bridge, joint-checkpoint and update
settlement tests provide bounded executable evidence, not live deployment proof.
The unused deferred_exposure_writes branch has now been deleted from the Learning
numeric seam: no production caller supplied it and no production consumer drained
canonical_receipt payloads. Numeric influence retains the existing synchronous
canonical receipt transaction and bounded-backpressure handling. The two former
deferred tests now inspect persisted capture/refusal and baseline behavior on that
live path. The canonical resolver's separate persist_receipt option is unchanged;
this inventory does not claim all Observation Lab state ownership is closed.

The obsolete inherit_behavioral_economic_knowledge entrypoint had no production
callers and has been deleted. Its qualification projection always returns
unavailable under learning_contract section 7.3, making its old verdict-to-treatment
branch unreachable. That branch was the sole caller supplying the silently
ignored expected_hypothesis_fingerprint / expected_experiment_id parameters to
apply_numeric_treatment; those parameters and the unused
suppress_shadow_if_paper_active parameter are also deleted. Read-only historical
Economic Knowledge projections remain. Current numeric/boolean treatment still
uses the canonical Observation Lab resolver and its receipt/authority gates;
no replacement inheritance authority or historical data migration was added.

At the same uncommitted base, observed_stocks and observed_xstocks are no longer
runtime model fields. MarketDataConfig performs bounded historical input decode;
canonical ticker precedence and token default/merge semantics are preserved.
Runtime consumers, settings and UI use only observed_equities/observed_stock_tokens.
The dedicated equities update and confirmed-delist cleanup use the existing
locked mutate_config owner. No decoder-triggered startup write was added.

The owner approved the narrow Coinalyze/FRED exception after the earlier
credential note below: each concrete Data adapter resolves only its own fixed
credential reference/environment key, without enumeration or transferring raw
values to Core/Research/UI. Missing-key and redaction checks are offline evidence,
not proof that real credentials exist or that either provider is online.

Lesson Reviewer now lives in extensions/learning/lesson_reviewer.py, with no
old Core module or import-time registration decorator. Existing CLI/API roots
explicitly register it through the Core Agent registry. Its reachable chain is
scheduler _run_lesson_reviewer -> Core orchestrator/AgentRun -> registered
extensions.learning.lesson_reviewer.run_lesson_reviewer. It creates no private scheduler
or thread. Evidence reads query settled candidate outcomes and revalidate their
cost/provenance eligibility before judgment; model calls use the Core inference
helper. Its writes and consumers are distinct:

| Boundary | Current owner / effect | Retained constraint |
| --- | --- | --- |
| Lesson promotion/demotion/application | LessonStore transitions ps_lessons and lifecycle events | Compare expected status; human rejection cannot be overwritten by a stale reviewer |
| Review-to-policy handoff | LessonStore.apply_reviewed_lessons rechecks accepted IDs inside its transaction, uses PolicyStore for lesson_policy and stamps applied versions | Existing version reuse and transaction rollback; reviewer submits IDs only, not a new RiskDecision writer |
| Skill proposal | SkillStore.create_from_lesson writes namespaced draft skill and lineage | Draft only, advisory layers, no activation |
| Proposal blocking | Core risk.engine._check_lesson_constraints / _learned_lesson_constraint_authorized | Automatic lessons require exact risk_lesson_constraint treatment authority; applied alone is insufficient; owner/manual rules retain their separate static authority |
| Runtime lifecycle | Core scheduler, orchestrator and AgentRun | Reviewer does not own timeout workers or a second run lifecycle |

This proves the reviewer is not a pure transport and identifies the Core Risk
gate that must survive any optional move. It does not establish that every
PolicyStore caller or Learning treatment writer has one owner. Next logical
seam was the review-to-policy transaction, now moved intact from the concrete
reviewer into LessonStore.apply_reviewed_lessons. Policy identity, evidence basis,
status revalidation and version reuse are unchanged. The former production-DB /
source-string policy-link spec now checks actual rollback of a partial linkage,
successful linking and no-op replay in an isolated database. Concrete judgment
now lives independently; shared LessonStore, PolicyStore and Risk remain Core.
Missing Learning implementation is tolerated by composition and produces a
durable completed_with_errors AgentRun on attempted execution, without inference
or policy linkage. Doctor checks actual registry binding, not module presence.
The existing logger identity remains stable for historical diagnostics. This
does not compartmentalize every Learning producer.

Dreaming implementation now also lives in extensions/learning/dreaming_agent.py.
Its full agent and nightly lesson-only producer are distinct entrypoints: Core
nightly scheduling first requests Serenity's existing durable admission, then
invokes the explicitly bound lesson extractor under the existing timeout worker.
An absent extractor records lesson_extractor_unavailable without suppressing
Serenity admission. CLI/API composition independently registers the full agent
and binds its extractor; old Core module/decorator are removed without a facade.

Extraction reads verified trade-case documents, proposes lessons through
LessonStore, routes automatic block recommendations through the existing guard
nomination service, and advances the existing learning cursor only after full
case coverage. It does not publish Risk decisions or trading orders. Full-agent
dreaming logs/daily reviews retain Core knowledge permission/quality/dedup gates;
neither entrypoint creates a private scheduler or worker. Source and deadline
semantics are unchanged.

Post-trade reviewer now lives in extensions/learning/post_trade_reviewer.py,
with independent CLI/API registration and no old Core module/decorator. Its
bounded strategy/prefix cursor reads are not an Outcome authority: the shared
eligible_strategy_outcomes reader still validates admission after the cheap SQL
prefilter. Review prose uses create_post_trade_review with medium confidence and
source/window tags. Deterministic case studies and guard candidates are requested
from the existing trade_case_builder service through knowledge-manager writers;
they are not activated Risk rules. Epoch setup uses the fixed Core information
policy service and is not moved or reinterpreted. Cursor state key, logger identity,
archive regeneration, source limits and failure reporting are unchanged.

Core scheduler and AgentRun retain execution/termination. Missing registration
is independently tolerated and attempts end with durable completed_with_errors,
without inference, review publication or case-building. This separates the
concrete reviewer but does not establish full transitive isolation of the shared
trade_case_builder, guard nomination or other remaining optional analytics.

The case builder's current production caller is exclusively the optional
post-trade reviewer; no CLI/job entrypoint calls it independently. Concrete
rendering and repeated-loss candidate generation now live alongside that caller
in extensions/learning/trade_case_builder.py, without a Core facade or new port.
It reads Core build_trade_attribution_rows (which uses eligible_strategy_outcomes),
then applies the existing close-time/source/dedup filters. Writes request Core
knowledge-manager case and guard-candidate creation, not Position/Outcome/Risk
mutation. Guard candidates stay auto_generated/shadow_only/non-blocking, with
Core creation-time shadow enforcement retained. Missing module reports both
case-building and guard-detection failures through the reviewer's existing error
result rather than pretending that zero artifacts is success. Shared attribution,
knowledge writer and guard authorization remain separate Core owners.

- Python production sources now live under `core/aletheia`; concrete exchange
  clients, generic CCXT transport and venue profiles live under
  `extensions/exchanges`. HTTP Model SDK transports live under
  `extensions/model_endpoints`, registered through the existing CLI/API
  composition entrypoints and existing Model registry. These moves are not yet
  committed or deployed.
- Direct import inspection of both current Extension packages finds no storage,
  SQLite or scheduler imports. The architecture suite now checks those packages
  explicitly, including `from aletheia import storage` spellings. This guards
  direct dependencies; it does not prove that all transitive helper calls are
  free of persistence or that every future optional Extension has been audited.
- Core macro read-time projection no longer imports the concrete collector.
  The unchanged risk-regime calculation belongs to the shared freshness module;
  collection reuses that projection. A blocked collector import no longer
  prevents reading persisted macro facts.
- Gateway symbol checks consume the shared normalized capability result. The
  OKX sandbox cache and public CCXT market loader now live in Exchange Extensions;
  account-specific source selection is bound through existing composition.
  Core retains its distinct universe-registry cache. The unconditional frozen
  OKX background refresh job and Gateway cache aliases are deleted.
- News and Prediction route registration is independently absence-safe in the
  API composition root: missing route modules/dependencies omit that optional
  surface while Core routes still register. Fresh-process tests cover each route
  module missing and both missing. This does not prove full subsystem absence,
  scheduler independence or complete route-handler dependency failure handling.
  News API now lives in `extensions/news/api.py`: direct writes are confined to
  namespaced `news_items`, and manual analysis delegates to the existing producer.
  Its old Core route path is deleted without an alias. News collector/analyzer
  now live in the same Extension; existing CLI/API composition binds their fetch,
  analysis and pruning operations to the single Core scheduler. Missing producer
  modules bind unavailable independently; no second scheduler or timeout worker
  was created. The shared News entity matcher remains used by Core persisted-fact
  reads. Namespaced Learning information-policy attribution remains unchanged.
  Prediction API now lives in `extensions/prediction/api.py`, without a Core
  forwarding route. Reads use existing repositories; configuration changes use
  the existing config owner, and manual refresh still goes through AgentJobManager
  admission and queueing. Prediction observer and Polymarket provider now live
  in that Extension too, registered by CLI/API composition through the existing
  Agent registry. Core scheduler/queue keep lifecycle ownership. Existing
  non-executable Opportunity publication, namespaced prediction repositories and
  Serenity evidence assessment are unchanged; broader transitive ownership proof
  and other optional producers remain open.
- The retired `flow` command facade is deleted. Other deprecated control groups
  still require individual reachability review; the old entrypoint table below
  is not proof that they have been retired.
- Optional data and research ownership remains mixed. In particular, context
  scheduling still imports concrete collectors, and Coinalyze collection invokes
  the persisted Learning information-policy epoch. That namespaced attribution
  must not be confused with a second Order/Position writer or silently deleted.
- DefiLlama public HTTP collection now lives in `extensions/defillama/collector.py`.
  Its only production consumer was context scheduling. The collector returns
  `OnchainSnapshot` DTOs and does not call database writers; the existing Core
  context transaction still owns `insert_onchain_snapshot_batch`. CLI/API and
  the existing standalone collector-process entrypoint bind a collection callback,
  while an unbound callback fails
  inside the existing per-collector timeout/health boundary. The old Core
  implementation path is deleted; cadence, configuration gates and persistence
  are unchanged. Other context collectors still have direct Core imports and
  are not covered by this compartmentalization claim.
- Model router configuration identity consumes credential fingerprints rather
  than raw stored keys. Credential management routes and other Core consumers
  remain separate open seams; this is not proof of complete credential isolation.
- Doctor credential-presence checks now consume existing scoped fingerprint
  results rather than receiving raw exchange/model keys. The output remains
  SET/MISSING and configured/unverified, never the fingerprint. Coinalyze's
  Doctor helper and both Coinalyze/FRED collectors remain unresolved: their
  existing data-provider key reads conflict with #3134's explicit no-Data-
  credential-authority wording. A limited per-provider Data-adapter exception
  was requested from the owner; no exception or credential change is assumed.
- CoinGecko Top-N collection now lives in `extensions/coingecko/top.py` with
  no old Core facade. It returns rows, source-time/batch provenance and movers
  summary; scheduler still owns the same-tick macro merge and
  `insert_coingecko_top_rows` transaction. API, CLI and standalone collector
  entrypoints bind the optional callback independently from DefiLlama.
  The distinct CoinGecko ticker fallback is not the same capability as Top-N
  enrichment; its separate transport move is recorded below.
- Public collector absence was checked through the real health persistence path:
  `_bounded` records `failed` for an unbound producer, replacing previous success;
  the operator rollup reports `attention` and a warning immediately. Existing
  overdue/three-consecutive-problem rules control promotion to `degraded`.
  Context job completion counters are not proof that all domains are healthy:
  they currently aggregate persistence/history errors, while domain health is
  stored separately. This review does not change that alert policy or use a
  successful context tick as evidence of provider availability.
- Hyperliquid public collection and its venue-specific USDC physical-instrument
  metadata builders now live in `extensions/hyperliquid/collector.py`.
  Scheduler retains snapshot persistence and cadence; API/CLI/standalone process
  entrypoints bind the optional producer. Unlike the other two public collectors,
  this producer also calls the existing Learning epoch owner (writes only
  `learning_policy_epochs`) and reads the same-series historical OI baseline
  through the shared derivatives projection. Those dependencies are retained,
  not misrepresented as a pure or persistence-free transport. Generic projection,
  baseline semantics and namespaced epoch attribution are unchanged.
- The existing direct storage/lifecycle import guard now covers DefiLlama,
  CoinGecko and Hyperliquid packages as well as Exchange/Model transports.
  Public producers may import shared storage DTOs; only each package initializer
  may bind the Core scheduler. Direct storage/SQLite/job-manager/APScheduler
  imports remain prohibited, including parent-package alias imports. A temporary
  alias-import mutation was rejected and removed before the passing rerun.
  This guard is intentionally not a transitive single-writer proof.
- Deribit GEX collection and raw mark-IV projection now live together in
  `extensions/deribit`; their shared instrument parser no longer creates a
  Core-to-collector dependency. The persisted `OptionsVolatilitySnapshot` schema
  remains in Core for repository validation and historical reads. Scheduler
  still owns both GEX and IV writes and binds through the existing three process
  entrypoints; missing Deribit does not require loading a collector to decode
  persisted IV. Existing GEX/IV derivation and observation-only authority are
  unchanged; these are not new Risk, sizing or execution inputs.
- CoinGecko ticker fallback and Binance public spot REST now live in their
  respective Extensions. Existing `FallbackChainProvider` keeps ordering,
  spot/perp isolation, diagnostics and close behavior. The public market-data
  factory consumes an optional, typed fresh-adapter factory bound by CLI/API/
  standalone composition; the builder keeps CoinGecko before Binance and skips
  independently missing modules. Unbound public fallbacks retain the primary
  CCXT provider. Binance venue-health callbacks remain existing Core diagnostic
  effects; the native CCXT provider itself is still a separate unresolved seam.
- `CCXTProvider` is not ready for a whole-file transport move: it owns lazy
  exchange/session construction, shared market-cache access, venue-health calls,
  and an awaited worker-thread call to `_persist`/`upsert_ohlcv_candles` for closed
  bars. The observation method now explicitly disables shared cache use as well
  as persistence and venue-health writes. Previously omitting `since` reached
  the shared cache despite those other flags; a regression covers both omitted
  and explicit `since`. Current Position Path production use passes an explicit
  bucket timestamp, so this is a port-level isolation repair, not evidence of
  a historical Position Path cache mutation. Core writer/cache ownership must
  be separated before the remaining CCXT transport can move.
- The native-consumer audit found direct construction in equity token pricing,
  trading xStock collection, derivatives comparison, Universe registry/features,
  and watched-price refresh (besides the central factory). Watched-price refresh
  now uses the same existing native factory for every venue group, including
  stock tokens, with primary and fallback both explicitly pinned to that venue.
  It retains the exact-provider rejection guard, Core cache writes and awaited
  close. This removes one direct-construction branch, not the native transport
  dependency. Other consumers still access raw market metadata/batch APIs through
  `_ensure_primary`; simply moving the class would expose concrete protocols
  across the Core boundary and move its OHLCV writer with it. Those consumers
  require normalized boundary work before physical migration.
- Equity token pricing and trading xStock snapshots now also use the existing
  native factory with same-venue fallback. Their local close/finally and snapshot
  batch-writer ownership remain unchanged. A fake-provider regression exercises
  both consumers, rejects direct construction and asserts pinned arguments,
  successful output and awaited cleanup. Universe registry/features and
  derivatives comparison remain direct constructors; factory convergence alone
  does not remove their raw protocol/metadata dependency.
- Universe registry/features and derivatives comparison now enter through the
  same native factory too. Universe passes its supplied configuration's resolved
  primary/fallback explicitly; comparison pins both names to each workset venue.
  Separate spot/perp provider instances, close ownership, Core writers and raw
  metadata/batch behavior are preserved. Focused consumer fakes assert those
  factory arguments. No production direct constructor remains outside the
  factory at this audit point; raw `_ensure_primary` consumers still prevent
  treating this as complete transport compartmentalization.
- Removed the ordinary fallback chain's `_ensure_primary` and `_primary_name`
  forwarding facade. The two remaining consumers (derivatives current/history
  collection) now explicitly request the existing native provider, preserving
  the same underlying primary source and close ownership. Ordinary chain users
  no longer inherit native-client access; direct native metadata consumers remain
  an open boundary. Collector fakes require the native flag, and the factory
  regression rejects reintroducing those chain attributes.

Exchange qualification, #3135 final accounting, #1884 runtime attribution, the
remaining single-writer/transitive-side-effect review and the final exact-head
verification are still required. No local source test establishes Fedora runtime
identity or economic qualification.

### Coinalyze credential and collection boundary (uncommitted tree)

The owner approved the limited Data-adapter credential exception: resolve only
the adapter's own configured reference; no raw credentials or generic secret
enumeration for Core/Research/UI. Coinalyze acquisition and its fixed
`coinalyze_api_key`/`COINALYZE_API_KEY` resolution now live in
`extensions/coinalyze`. Existing entrypoints bind collection and a boolean
configuration query; doctor no longer imports the producer. No key returns
`no_key`/`not_configured` before HTTP or policy-epoch persistence. An absent
adapter fails within the existing scheduler health boundary.

Core retains scheduling and OI/liquidation persistence. The adapter retains a
specific same-source `open_interest_near` read and, only when configured, the
existing transitive namespaced policy-epoch persistence. These are explicit
remaining dependencies, not a claim of completely side-effect-free acquisition.
Verification uses offline fakes; no real Coinalyze or FRED key was obtained or
used.

FRED HTTP acquisition, fixed `fred_api_key`/`FRED_API_KEY` resolution, source
metadata decoding and day/week request throttling now live in `extensions/fred`.
Existing entrypoints bind ordinary macro results, research batches and boolean
configuration status within the optional macro Extension. Core receives no raw
FRED key and retains existing scheduler persistence. Missing keys produce explicit
`not_configured` research batches before HTTP-client creation. Missing adapter
leaves the research callback unavailable; existing persisted history remains
readable. Ordinary yFinance fallback is unchanged and never fills FRED research
evidence. The adapter still reads persisted source batches through the existing
Core research helper to preserve restart-safe cadence; it has no direct writer.

The remaining MacroCollector now lives in `extensions/macro`, with no old Core
producer facade. It owns CoinGecko-global/Alternative.me/yFinance acquisition,
ordinary fallback ordering and composite DTO assembly. Core retains
`fresh_macro_row`, the MacroSnapshot contract, cadence, same-tick mover injection
and transactional macro/FRED-batch persistence. FRED-to-macro binding is internal
to Extensions. Absent macro production fails within existing collector health;
persisted macro history and the shared freshness/risk projection do not import
either producer. No second scheduler or writer was introduced.

### Native OHLCV download writer boundary (uncommitted tree)

Historical download previously persisted twice: native CCXT acquisition wrote
its response before `history_downloader` applied the requested range, then the
downloader wrote its filtered page. An offline test using the real provider
control flow demonstrated both duplicate writes and an out-of-range bar in the
first write. Forward, backward and new-listing bootstrap fetches now disable
provider persistence; the existing downloader page owner writes after filtering.
Venue-health updates and normal provider persistence outside downloads are
unchanged. This removes the duplicate download write path, not the broader
native CCXT acquisition/cache/health/history coupling; that split remains open.

Confirmed-bar persistence is now owned by Core
`data.market_history.persist_closed_candles`, not a static method on the concrete
CCXT class. It accepts normalized candles and explicit provider provenance,
filters forming bars and calls the same repository with failures propagated.
Both the public CCXT fetch path and Universe feature causal-input persistence
now await this single policy in a worker thread. Public fetch alone catches
optional history failures; Universe still stops before feature computation if
its required history write fails. Observation fetches bypass persistence. No old
`_persist` facade or second writer mode was added. This separates storage policy
from caller failure semantics, not the remaining Universe raw transport.

The public CCXT provider, client construction and malformed-market-ID guard live in
`extensions/exchanges/ccxt/market_data.py`. The existing market-data composition
binds the provider factory alongside spot fallbacks at API/CLI/collector startup.
Construction remains lazy through `net.make_ccxt`; the Extension owns physical
market selection, wire normalization and bounded client/session cleanup. Core
retains timeframe and exact-venue checks, composition, cache/health services and
the confirmed-history writer. The Extension invokes those Core services; this
is not a claim of side-effect-free acquisition. Missing native adapter reports
`ccxt_market_data_extension_unavailable` for native-only requests; configured
public spot fallbacks remain usable. No old Core class or forwarding alias
remains. The native consumers below still receive raw client objects and need
normalized acquisition contracts.

The 2026-09-14 raw-consumer audit identifies the requirements below, with listing
rows updated after the normalized-boundary extraction.
Calls using only normalized ticker/OHLCV methods (price feed, equity pricing,
Position Path, outcome settlement and history download) are not raw-client
consumers merely because they request `native_ccxt=True`.

| Raw consumer | Required behavior still coupled to CCXT |
| --- | --- |
| `universe.registry` | Now consumes normalized `MarketCatalogEntry` values; CCXT enumeration/decoding is in `extensions/exchanges/ccxt/registry_catalog.py`. Core retains blacklist, demo/futures scope, support rejection and atomic publication. Empty/malformed raw catalogs fail before publication; a complete catalog filtered to zero supported assets remains distinct. |
| `universe.features` | Raw spot/perp bootstrap, batch ticker fetching/unit conversion, primary-only OHLCV/OI/funding/taker and discovery orderbook acquisition now live in Extensions. Single-asset acquisition accepts no raw exchange or CCXT symbol converter; Core receives normalized values, checks identity, owns persistence/freshness and the taker mover gate. Core keeps independent market readiness and provider cleanup, without raw-client access. Tickers use canonical `(market_type, symbol)` keys; only Extension converts request symbols to CCXT notation. Missing input/unit evidence stays unknown; explicit observed zero remains distinct. |
| `data.collectors.derivatives` | Live, history and comparison acquisition now use the existing provider's normalized results; raw CCXT requests, Funding/OI wire parsing and symbol lookup live in the CCXT Extension. Core retains registry/workset selection, positioning association, same-venue historical OI delta enrichment and provider cleanup. Comparison returns separate rows/prices/coverage, persisted in the context scheduler savepoint. Sparse history owns bounded existing-row checks and direct identity-checked, deduplicated writes outside the live budget. These paths cannot be collapsed into a single collection/writer lifecycle. Venue-specific comparison readers are selected by the existing composition root, not by venue-name branches in the generic request loop. |
| `data.collectors.microstructure` | Now a normalized orderbook batch port; concrete acquisition lives in `extensions/exchanges/ccxt/orderbook.py`. Scheduler and Entry Planner retain their own persistence/decision paths. |
| `extensions.volume_scanner` | Exact grouped-instrument tickers and contract-aware quote-volume ranking. |
| `trading.market_data._listed_symbols_on_exchange` | Now consumes normalized spot-only symbols; Core retains its hard deadline and unknown result on failure. |
| `data.venue_listing_cache._load_listing` | Now consumes normalized all-market symbols, including equity tokens not marked spot; Core retains cache/deadline/fail-open handling. |

The two listing readers answer different questions and must not be merged merely
because both return symbol sets. This audit also found missing `perp` propagation
in the live and historical derivatives lanes: both selected the Binance spot
client. Both now pass the existing perpetual type, matching the comparison,
microstructure and volume paths. A real-selector/fake-client regression failed
for both lanes before repair and verifies physical `binanceusdm` selection and
cleanup afterward. No new exchange support or funding/economic semantics added.

Listing acquisition now lives in the existing generic CCXT market-capabilities
module. Its explicit spot-only/all-market argument preserves both filtering
rules; it pins primary/fallback to the requested venue and closes the client in
the deadline-owned task. One bound listing callback returns only canonical
`frozenset[str]` values to the two Core consumers. No new cache, scheduler or
writer is introduced. Missing adapter leaves both queries indeterminate rather
than confirming delisting. The Extension still uses the existing native provider
to acquire its raw client; that internal dependency remains part of the larger
CCXT transport split.

### Derivatives collection boundary (uncommitted tree)

Derivatives extraction prerequisite: `provider_source_time` now lives in the
existing Core facts module, unchanged, rather than in the network collector.
Funding/OI/price/metadata normalization no longer imports the collector simply
to parse a source timestamp. Collector and CCXT provider call the one facts
implementation; the former collector function/export is deleted without an
alias. A fresh-process regression blocks collector import during normalization
and checks that the old entrypoint is absent. This does not move the remaining
derivatives acquisition or historical writers out of their current owners.

The live path's independent Binance positioning HTTP reader now lives in its
venue Extension, including endpoint selection, four-request concurrency, bounded
stream budget and cancellation draining. Existing composition binds the optional
reader; Core selects the requested ratio family and consumes normalized maps.
Missing reader returns no enrichment, not fabricated zero; primary Funding/OI
continue and existing missing-ratio diagnostics remain. Collector HTTP import,
Binance URL constants and the old batch function are removed from Core without
aliases. Primary live CCXT batch/per-symbol acquisition now resides on the existing
CCXT provider; Core obtains batch capability before choosing registry-wide targets,
then receives normalized snapshots for its scheduler writer. The provider does not
select registry targets or close itself. A provider with no raw-client interface
exercises success and prepare/fetch failure cleanup. Live OI wire parsing has also
moved to the provider module without a Core alias. It returns no historical delta
and does not query historical state. Core computes that delta after receipt using
the existing exact-venue projection with comparison excluded; the scheduler still
persists the enriched snapshot. Tests preserve the one-hour baseline and exclusion
of comparison observations, while proving zero and nonzero wire observations do
not query history.

History market preparation, symbol lookup and bounded Funding/OI requests now
reside on the existing provider. Core receives normalized snapshots and diagnostic
codes. Core retains the three-row sparse-window gate, exact-venue deduplication,
observed-rate next-time freezing, writer calls, counts and cleanup. Before any
history write, the entire result batch must match the requested canonical symbol
and venue. Regression coverage exercises normalized-only providers, cross-identity
rejection without writes, repeated same-venue reads and foreign-venue isolation.
Comparison request orchestration and Funding parsing now reside on the existing
provider, with no Core parser alias or concrete collector dependency from that
provider. The provider retains the three-second request and ten-second venue
budgets so partial successful facts survive a later request failure. Core selects
the complete unranked workset and primary ownership flag, merges normalized
coverage, closes providers, and leaves persistence to the original context lane.
The four-symbol/ten-minute rotation and existing source metadata are unchanged.
Replay tests preserve primary Funding/OI exclusion, partial venue failure,
instrument provenance and isolation from legacy scalar readers. A normalized-only
provider verifies Core does not require a raw client and closes on failure.
MS-C08 now tests real parser clock behavior instead of requiring clock fields to
appear in a particular Core source file. Existing composition now injects Bitget's
settlement-homogeneous funding reader and Binance's USD-proven OI history reader
from their venue modules. Funding-response price reuse is an explicit capability.
Generic comparison no longer branches on venue names or imports venue packages.
Tests verify separate USDT/USDC requests, nine-symbol coverage with four-symbol
rotation, and no history-reader activation merely from an unconfigured venue's
CCXT capability flag. Writer boundaries are unchanged.

### Optional Yahoo macro prompt enrichment (uncommitted tree)

The sole production consumer of the old Core `data.yfinance_provider` was the
default-off market-context macro block. Its four-ticker synchronous download and
rendering now reside in `extensions/yahoo/market_data.py`; the old Core file
is deleted without a facade. Existing market-data composition binds the async reader.
Core retains the configuration gate and omits the block when unbound or failed.
No persistence, credentials or trading writes were introduced. Focused tests prove
enabled/disabled/absent behavior, no fetch on registration, missing-SDK empty
output and executor offloading.

Research-worker Yahoo fundamentals now use the same existing Yahoo text-acquisition
module through a separate bound reader. Core selects configured tickers, keeps the
six-ticker budget and composes the observation-only context; the Extension performs
the public read and unchanged field projection, without storage or scheduling.
Absent binding preserves the configured universe text; missing SDK returns no
fundamentals, individual ticker failure is isolated, and observed zero growth or
margin is retained. DU-S08 now exercises adapter-to-research text propagation from
synthetic responses rather than requiring vendor wire fields in Core source.
Yahoo macro text, research fundamentals and equity daily/live reads now reside
in one Yahoo Extension, independently of Macro collector presence. Core equity
observation consumes optional bound readers and a normalized EquityFetchError,
retaining hard/soft classification, executor ownership, stock-token cross-check,
thresholds, LLM and Opportunity handling. Missing adapter reports
equity_data_unavailable; missing live price retains prior-close fallback. No old
Core fetch-function aliases remain. The existing 90-day download and one retry
are unchanged. Broader optional-agent lifecycle ownership still requires audit.

The broader test pass exposed four observer fixtures missing current explicit
model selection and DU-S26's obsolete Core-source-position assertion. The four
fixtures now provide a synthetic selection to their existing mocked LLM, without
changing production's not-configured protection. DU-S26 verifies actual normalized
global/top-position ratios remain distinct and absent values remain None, while
retaining the consumer-registration checks. Both complete test files now pass
(69 tests); this is scoped integration evidence, not global completion.

### Research-to-Candidate ownership seam (uncommitted tree)

Automatic theme grouping, score-gated source publication and post-publication
Planner wake retry state now reside in the existing Core serenity_publication
module beside publish_serenity_candidates. The research pipeline passes source
IDs and the configured score threshold, then emits its namespaced count event.
It no longer owns the publication helper or wake retry set/lock. The unused
research executor lock was removed after confirming it had no references.
No alias remains for the old research auto-promote function. Manual and automatic
flows still share the existing source admission/Candidate writer; a regression
proves failed wake retry without duplicate Candidate publication and unchanged
threshold forwarding. This is a prerequisite boundary extraction, not a claim
that the optional research implementation is already fully compartmentalized.

### Research retry scheduling truth (uncommitted tree)

Current reachability: three research API paths and scheduled/retry jobs use the
Core scheduler enqueue entrypoint; it submits to a dedicated instance of the
existing capacity-bounded persistent async worker, owned by Core scheduler.
The research processor claims its namespaced run atomically,
writes Serenity evidence/claims/themes/opportunities, and requests one-shot retries
from the existing Core scheduler. This is not yet proof of optional lifecycle convergence.
The manual AgentJobManager cannot be substituted blindly: it owns AgentRun rows,
whereas this pipeline owns SerenityResearchRun rows and separate cancellation.

A concrete duplicate-decision defect was reproduced: rate-limit handling persisted
one next_retry_at, then retry scheduling drew a second jitter and clock, yielding
a different run_date. Scheduling now consumes the exact timestamp from the
successful terminal-state write. One regression failed with a roughly 15-second
discrepancy before the repair; it requires exact timestamp equality and one jitter
draw. Backoff levels, retry caps, claim/CAS, cancellation and parent/idempotency
behavior are unchanged. One-shot registration and retry-run creation now reside
in scheduler.schedule_serenity_retry, beside scheduled research enqueue. The worker
retains rate-limit classification and retry eligibility, then submits the persisted
deadline. No old worker scheduling alias remains. A scheduler regression proves
unavailable/stopped scheduler makes no child run, exact job options are retained,
and duplicate callback execution creates/enqueues one idempotent child.
The old worker enqueue function and threading import are removed, without an alias.
Core scheduler retains the previous exception classification and failure-write
behavior through the research processor helpers. Per-run daemon thread creation
is removed. stop_scheduler and scheduler startup failure close the research lane;
safe worker preparation refuses replacement while its old thread is draining.
Research readiness is checked only on research admission; a draining Research
lane cannot prevent preparation of the independent Core async worker lanes.
Shutdown cancellation (including cancellation before the first coroutine step)
and admission failure use the existing crash handler/terminal CAS, preserving
completed/cancelled runs. Repeated runs reuse one loop rather than leaking workers.
Scheduler now accepts explicit
processor/crash-handler bindings from the existing CLI/API composition roots;
it no longer imports the concrete research worker. Missing binding refuses thread
creation, daily enqueue, and retry registration/callback before new run writes.
All three API creation paths reject unavailable processing before run/profile writes.
The concrete processor now lives in `extensions/serenity/research_worker.py`, with
the old Core module removed and executable callers/spec paths updated. Yahoo
composition tolerates an absent Serenity module without disabling market data.
Its direct repository writes maintain Serenity namespaced state. Candidate
publication invokes Core serenity_publication; epoch activation invokes the existing
Core information-policy service, whose sole transactional writer is persist_policy_epoch.
Research supplies no epoch policy values and does not directly write that table.
Activation stays after successful claim and before pipeline execution; rejected
claims/missing/terminal runs do not activate it, and activation failure stops the
pipeline with a durable failed run. This is a Core service request, not a second
epoch writer. Other optional lifecycle owners still require audit. Status projects missing
processing as unavailable while retaining historical run details and preserving
active/disabled states. Binding is not proof of full optional implementation isolation.

### Research-note agent (uncommitted tree)

The independent research-note agent is not the Serenity multi-stage processor:
it publishes a speculative research_note through Core knowledge.manager, with
the existing permission, quality, idempotency and retrieval gates. It neither
publishes Candidate nor owns a scheduler/thread. Its concrete prompts/prediction
context/model call now live in extensions/research/research_agent.py; the old
Core module is removed without a facade. Existing CLI/API composition explicitly
registers it with the existing Core Agent registry. Importing the implementation
alone no longer registers a runtime agent. Missing implementation leaves it
unregistered; Core orchestrator records the unknown-agent error and existing
completed_with_errors terminal state without model or note publication.
Existing AgentRun claim, queue, permissions and termination
remain the only execution lifecycle. This does not establish isolation of every
other Research/Observation/Learning implementation.

### Equity observation implementation (uncommitted tree)

The equity observer now lives in extensions/observation/equity_observer.py;
the old Core implementation and import-time registration decorator are removed.
Existing CLI/API entrypoints explicitly register it with the existing Agent
registry; Core scheduler retains market-session triggers, timeouts and AgentRun
orchestration. The observer writes execution_eligible=False equity_daily records
through the existing Opportunity repository, not Candidate publication or orders.
Model calls retain Core inference and permission gates. Its Yahoo readers and
token-price Core provider boundary are unchanged. Missing Observation registration
is tolerated and fails agent lookup explicitly; market-data composition tolerates
the missing module independently of Yahoo macro and Serenity fundamentals.
The duplicate xstock metadata writer is removed after finding no production
reader; stock_token remains canonical. Focused cases cover absent/normal/premium/
anomalous token prices, unchanged LLM anomaly exclusion and execution_eligible=False.
Historical rows are not rewritten; the retired alias is documented in design/history.
Legacy observed_stocks/observed_xstocks config fallback remains separately
identified convergence work, not proven retired here.
The duplicate observed_stocks UI writer is now removed. The canonical equities
editor shows the effective current list; explicit save clears the legacy ticker
list alongside the canonical write, with both lists represented in the audit.
This repairs empty-save reactivation without silently changing config at startup.
The deprecated observed_stocks generic API write field is removed and rejected
by request validation before config access; stored-config/runtime read retirement
remains open. OpenAPI generation invokes the existing exporter as a module from
the repository root so optional News/Prediction routes are not silently omitted.
The observed_xstocks API writer is also removed. Explicit canonical token saves
clear that legacy list inside the existing candidate-publication-permitted config
mutation, while unrelated updates leave it unchanged. UI shows the merged current
token list until saved; generated request types expose only the canonical writer.

### Shared orderbook boundary (uncommitted tree)

The microstructure producer now lives in the existing CCXT Extension. The
Core `collect_orderbooks` binding returns the existing snapshot/error envelope
to both scheduler and Entry Planner; no old collector class facade remains.
API/CLI/collector composition binds it independently and missing implementation
returns no facts with the existing provider-setup error phase. Physical market
selection, metadata loading, depth request and provider cleanup stay together
in the Extension, with no direct database or scheduling ownership. The pure
contract-size validator is shared in `data.orderbook`. Discovery calls the existing
provider's normalized `get_orderbook(InstrumentId)` using its already-loaded
primary client, retaining the five-second request bound without a new lifecycle.
Core rejects mismatched symbol/venue/market DTOs before its existing writer;
cache freshness/reuse and post-acquisition freshness checks remain Core-owned.
Logical Binance identity is preserved when the provider uses its physical
`binanceusdm` derivatives client.

Discovery market preparation now lives on the existing provider. It selects the
owned spot/perp client, sets its wire mode and loads metadata; derivative contract
acquisition time is assigned only after successful load and cleared before a new
attempt so failed/cancelled refresh cannot retain a prior acquisition claim.
Core tracks readiness separately for each market and retains its finally-close
owner. No raw client, metadata or timestamp is returned from preparation.
Batch ticker requests now accept canonical symbols and return market-qualified
canonical keys. The Extension owns wire conversion, deduplicates wire requests
and restricts results to requested identities. Core's prefilter evidence records
canonical ticker symbol and explicit market type, without a conversion callback.
Same-symbol spot/perp values remain separate through filtering. Chunk sizes,
partial-chunk survival and missing-versus-zero rules are unchanged.

Taker endpoint selection/response decoding now live in the Binance and OKX
venue packages, bound by existing native-provider composition. Generic CCXT
owns only exact primary selection and the optional reader call. Unconfigured
taker capability returns unavailable without creating a client. Core retains
the derivative-only strict 1h mover threshold and five-second request bound.
Venue readers reject nonfinite/bool/negative invalid quantities; explicit OKX
zero buy volume remains zero. Binance uses its logical identity with the existing
physical derivatives client, not a Core `exchange.id` branch. No frozen venue is
enabled by this migration. Wider taker source-time evidence is not proved here.

Discovery funding now uses the existing native-provider composition to bind its
input reader. The generic reader uses the unified funding response; Bitget's
reader lives in its venue package and consumes only the existing normalized
ticker and loaded contract interval, without an extra request. Generic CCXT
does not import that venue package. Shared normalization returns finite observed
rate/source time plus separate economic proof and interval provenance. Core owns
freshness, historical fallback and candidate evidence version. A missing interval
or its acquisition time is not invented; a finite observed rate does not by
itself qualify complete Candidate funding economics. Missing Bitget reader fails
provider construction rather than silently using the generic request path.

Discovery OI now calls the existing provider's primary-only method, returning
the existing `DerivativesFact` after shared unit/identity/source-time normalization.
Core checks venue, canonical symbol, fact family, observed venue-native USD
semantics, availability and current freshness before replacing its verified
historical OI value. Adapter failure or unproved facts retain that historical
fallback. The adapter owns no history writer, close or second lifecycle. The
per-asset call receives its existing provider instead of one new callback per
data family. Shared CCXT metadata helpers and other derivatives consumers still
require their broader ownership audit; this is not full derivatives convergence.

Discovery OHLCV acquisition now uses the existing provider's primary-only
`get_primary_ohlcv(InstrumentId)`. It shares normalization with public OHLCV
reads but does not access fallback/cache/health/history or close the provider.
Core retains per-request timeout, exact venue and closed-window admission.
The same accepted candles feed the strict per-asset history writer and feature
calculations; rejected rows cannot influence features while being absent from
history. Required history write failures still stop computation. BTC baseline
uses the same normalized primary read and excludes unclosed/wrong-venue candles.
Its admitted candles are now passed through the same strict Core history writer
before assigning any BTC closes/returns to asset calculations. A failed baseline
write leaves all BTC inputs unavailable without suppressing unrelated asset
collection. This proves current input persistence, not historical reconstruction
of older snapshots or timestamp alignment of beta windows. The
generic observation method retains its existing fallback behavior for its own
consumers and is not used for exact-venue discovery.

Comparison with Universe discovery exposed an identity gap in microstructure:
it previously stamped requested identity onto mismatched market metadata or
response symbols. Its Extension now rejects an explicit InstrumentId/provider
venue mismatch, metadata symbol/contract-type mismatch and an explicitly wrong
response symbol before producing a snapshot. Missing response symbol remains
accepted as before. Four regression cases first produced wrongly labelled
snapshots, then fail closed with existing phase-specific errors after repair.
This does not merge Universe's caller-owned session, strict spot metadata rule,
five-second acquisition timeout or independent persistence into the batch lane.

VolumeScanner public acquisition now lives in `extensions/volume_scanner`, with
no old Core facade. Existing CLI/API/standalone collector entrypoints bind its
callback; scheduler keeps the wave-one ranking, same-tick macro dependency and
`save_volume_ranking_snapshot` transaction. Unbound collection fails within the
existing health/timeout boundary. Its unit conversion now directly reuses
`extensions.exchanges.ccxt.tickers.quote_volume_24h`; no Core Universe helper
alias remains. Universe uses the same conversion through its native provider's
batch method, preserving loaded-client reuse, ASCII/listed-symbol filtering,
100-symbol chunks and partial chunk results. Core retains threshold/priority
decisions and persisted feature ownership; the remaining raw feature inputs are
not claimed normalized by this volume-boundary extraction.

Microstructure ownership is not scheduler-only: Entry Planner performs bounded
on-demand acquisition, and Universe feature collection parses and persists exact
orderbooks. Shared geometry/level validation and USD-depth projection now live in
`data.orderbook`, returning the existing `OrderbookSnapshot` without transport or
storage. The collector and Universe use that one implementation; no old parser
facade remains. Collector absence does not prevent shared projection. Concrete
`contractSize` metadata conversion and acquisition still require Extension
separation; this move alone does not establish optional collector independence.

### Close settlement write-path audit (uncommitted tree)

Initial verified economic closes and deferred fee/funding completion both use
`portfolio.pnl._commit_close_economics`. Inside the caller's transaction it
updates Position, inserts or conditionally updates the matching full-close
event, computes eligibility and enqueues durable hook intents. Post-commit
dispatch consumes those intents; it does not enqueue another settlement.
The close-event amount is a checked mutable projection of the same close,
not independently calculated money. This scoped path review is not a global
proof against every raw SQL writer.

| Remaining write path | Fields/authority observed | Boundary still to prove |
| --- | --- | --- |
| `outcome_closure` administrative close | Open-to-closed CAS, no verified exit price or realized amount, eligibility false | Lifecycle-only path must remain distinct from invented economic evidence |
| `broker_funding_settlement` terminal expiry | Unavailable funding, false eligibility, terminal reason/evidence and durable audit | No final amount or repeated exchange close; fair queue progress remains required |
| `pnl.load_eligible_position_for_delivery` attribution recovery | Reassesses `attribution_read_failed` under writer lock and writes eligibility/reason plus audit | Moved from hook delivery to the existing close-economics owner; global raw-SQL writer audit still required |
| `pnl.persist_exit_reasoning` | Closed-position explanatory JSON only | Not an economic settlement writer |

The funding finalizer rechecks source evidence and fingerprints under the same
transaction before monetary commit; these checks protect stale-input/idempotent
recovery and cannot be deleted just because both Position and close-event retain
amounts. Audit mirroring runs after commit and has a separate replay path.

## Coverage and entrypoints

The source walk contains 562 Python files: 35 first-level packages and 12 root
modules. Counts include package initializers, exclude bytecode, and are dated
observations rather than brittle acceptance thresholds. AST inspection covered
calls to the named Core persistence APIs below and literal SQL statements for
the nine authority tables. Dynamic table/SQL construction is reviewed separately;
the bundle importer is allowlisted and preflights all payloads before mutation.

| Entrypoint | Current lifecycle / effects | Boundary still requiring proof |
| --- | --- | --- |
| `pyproject.toml`: `aletheia` / `ato` → `main._entry_point` | Registers CLI command groups; commands distinguish reads, explicit controls and maintenance. | Deprecation labels are not evidence of inertness. Hidden `flow` alias and old model/permission/secrets/risk groups remain visible to reachability review. |
| `_gateway_runner.run_supervised` | Owns backend, Frontdoor, collector and optional operator/build-watch subprocesses; PID/route files and bounded process restart. | Process authority and absence-safe children must survive physical moves; no additional supervisor. |
| `gateway.frontdoor_runner.main` / `frontdoor.app` | Binds selected operator addresses, proxies to the backend and serves failure/static surfaces. | #1884 exact runtime/browser evidence remains distinct from source regressions. |
| `server.app.lifespan` | Verifies migrations, starts runtime provenance/reconciliation, scheduler/job manager and registered background tasks; stops producers before consumers. Optional Telegram adapter import/configuration is absence-safe. | Remaining top-level imports still need reachability review. Secret bulk migration and implicit Git-hook cleanup are removed; ordinary interrupted-work reconciliation is not historical repair. |
| `agent_runtime.scheduler.init_scheduler` | One APScheduler owner; registered jobs drive market collection, planning, position/protection/recovery and optional research. | Collector subprocess substitutes cadence for two scheduled collectors; mutually exclusive environment wiring must remain explicit. |
| `data.collector_runner.main` | Supervised two-minute collection loop plus ten-minute context collection; calls existing scheduler bodies. | It is active network/market-state work, not an inert library. |
| `server.app.LIFESPAN_TASK_CATALOG` | Read-only catalogue of startup LLM/exchange/behavior gates, PI daemon, Fast Trigger, Telegram, lag monitoring and module-owned rechecks/resume. | The catalogue is observability, not a new scheduler or permission registry. |
| `operator-shell/src/index.ts` | Starts `OperatorLoop`, signal-driven shutdown; Python `gateway.operator_shell` owns process monitoring. | Optional operational autonomy must not become another trading writer/lifecycle. |
| `deploy/systemd/aletheia-observer.service` → `aletheia observe run` | Explicit bounded Observation worker; template declares separate checkout and Lab state ownership. | Template claims are not deployed-unit verification or proof of every worker import. |
| `native/aletheia-host` binaries / `frontend` | Packaged CLI/tray launch and browser operator surface. | Keep bootstrap/control separate from backend trading authority; UI absence/failure cannot fabricate health. |
| `update`, `backup`, `maintenance`, deployment scripts | Explicit install/update, restore, migration and bounded historical repair entrypoints. | Preserve human deployment/destructive-repair gates; never invoke these merely to complete an inventory. |

## Package disposition map

“Mixed” means moving the entire directory would misclassify current authority.
Names are existing package paths, not proposed new abstractions. The map covers
all first-level Python packages; detailed per-consumer independence remains an
open gate where stated.

| Current package (Python files) | Authority / side-effect surface | Target disposition / open seam |
| --- | --- | --- |
| Root modules (12) | CLI/bootstrap, paths, logging, networking, packaged process and runtime-home migration | Thin composition / Core utility / Ops; split by role, not a new application tier. |
| `agent_runtime` (22) | Scheduler, job queue, runtime session, pause/resume, connectivity and daemon lifecycle | Mixed Core lifecycle and optional agent orchestration. |
| `agent_shell` (23) | Entry Planner plus operator tools, permissions, context and recommendations | Mixed Core planning and optional operator capability; tools must retain Risk/Gateway boundaries. |
| `agents` (14) | Model-backed observer/research/learning consumers | Optional enrichment; no separate execution writer. |
| `analytics` (60) | Shared realized Outcome eligibility plus attribution, read models and counterfactual measurements | Mixed Core eligibility and optional analytical writers; preserve separate economic meanings. |
| `backup` (4) | Artifact creation, verification and explicit restore | Ops; whole-DB restoration is not ordinary table import. |
| `cli` (33) | Operator commands, diagnostics and explicit maintenance entrypoints | Mixed minimum Core controls and optional/Ops commands. |
| `config` (9) | Typed schema, locked atomic mutation, redaction, secret storage, and the execution-identity environment/venue registry | Core configuration plus scoped adapter credential composition; raw secret access not yet compartmentalized. Generic Core callers consume the registry; profile/native capability exceptions remain scoped to their extension boundaries. |
| `counterfactual` (8) | Replay, advisory, calibration and experiment measurements | Optional research state, never authoritative realized trading economics. |
| `data` (45) | Public venue/network collectors, caches and market facts | Mixed market boundary and optional data enrichment; concrete protocol implementation belongs outside Core. |
| `diagnostics` (3) | Read-only reports/config checks and explicit Doctor infrastructure repair | Core operator reads / Ops repair; ordinary probe does not authorize fix. |
| `economics` (11) | Logical inference calls, usage/budgets, pricing references and context measurements | Mixed Core inference provenance and optional reference/analysis work. |
| `exchanges` (10) | Concrete Bitget/OKX/Binance clients, auth and submission classification | Exchange Extensions; active native exceptions still require consumer-level proof. |
| `execution` (19) | Gateway/Coordinator, claims, reconciliation, materialization and broker helpers | Mixed Core semantics and concrete normalization; keep one Risk-gated command path. |
| `files` (5) | JSONL/audit, Markdown, paths and Git filesystem operations | Core audit primitives / Ops or optional artifact work. |
| `gateway` (22) | Frontdoor, process/PID/service/network control, explicit hook cleanup | Core operator plane / Ops composition. |
| `infra` (4) | Events and bounded error projections | Core observability; event publication must not grant trading authority. |
| `knowledge` (10) | Retrieval, guards, seeds, maintenance and recovery | Optional knowledge; learned safety influence remains separately gated. |
| `learning` (32) | Cohorts, evidence windows, experiments, hypotheses and guarded consumers | Optional; shared Core Outcome eligibility cannot move into this owner. |
| `maintenance` (9) | Offline retention, accounting and fixed-target recovery | Explicit Ops/historical repair; not ordinary Core runtime writers. |
| `model_provider` (10) | HTTP SDK transports, endpoint router, inference broker and explicit fallback | Mixed Core Model Port/inference semantics and concrete HTTP Extensions. |
| `observation_lab` (19) | Lab DB, windows, runner/checkpoints and ownership checks | Optional namespaced Observation; owner import absence is fail-closed and covered by startup/reconciliation tests. |
| `policies` (3) | `PolicyStore` writes immutable `ps_policy_versions`; lesson reviewer is a current consumer | Optional policy history, not equivalent to retired PS execution tables; do not delete by `ps_` name. |
| `portfolio` (17) | Position/close/funding reconciliation, PnL and Outcome hooks | Core lifecycle/economics with concrete account seams still to isolate. |
| `regime` (3) | Regime collection/detection | Optional market enrichment. |
| `risk` (5) | Canonical evaluation, policy/kill switch and quality gates | Core safety authority; independently persists preflight/authoritative decisions. |
| `server` (31) | FastAPI lifespan, middleware and route composition | Mixed minimum Core API and optional route surfaces. |
| `storage` (12) | SQLite/transactions, models, repositories, migrations, event/artifact stores | Mixed canonical operational persistence and namespaced optional schemas; table use must determine ownership. |
| `sync` (4) | Bundle export/import and conflict handling | Import is restricted to six namespaced research tables; Core Proposal and retired PS runtime rows are rejected before mutation. Artifact-category and whole-payload preflight remain separate restore/atomicity boundaries. |
| `telegram` (2) | Configured notification/polling endpoint | Optional external adapter; no independent execution authority. |
| `trading` (34) | Typed Entry contracts/compiler/capture/activation plus source publication | Mixed Core Entry semantics and Serenity source orchestration; Candidate creation now uses Core publication. |
| `trust` (3) | Behavior authority and Live unlock | Core safety boundary; no authority expansion in #3134. |
| `universe` (37) | Candidate selection/publication, attention and market/scanner consumers | Core Candidate semantics mixed with optional source/scanner implementations. |
| `update` (11) | Deployment identity/continuity, bootstrap, smoke and source/package update | Ops, explicit deployment authority; never inferred from green tests. |
| `uta` (14) | Existing normalized contract/models, loop-owned registry, CCXT adapter, venue profiles and credential composition | Mixed Core Exchange Port and Exchange Extensions; reuse this boundary rather than adding another framework. |
| `utils` (2) | Datetime helper | Core utility; no new wrapper needed. |

## Canonical mutation seams

Order reconciliation has a stronger identity gate than the logical Fill key:
before any private query, persisted UTAAccountIdentity must equal the registered
current identity. Missing identity, old credential generation, different account
ID, profile or adapter version remain inconclusive with the stored Order intact;
five focused cases forbid query_uta. Position coalescing now separately checks
same-Proposal or exact same-Plan ancestry, validated account identity, primary
native authorization and unambiguous ownership as detailed above. This upstream
guard is independent from downstream Learning contract 11.1 Outcome exclusion;
neither is a substitute for current runtime qualification.

Account/adoption follow-up: durable_private_fill_id currently namespaces remote
IDs by execution_mode, and the multi-account reconciliation regression proves
six configured logical modes can materialize the same remote ID independently.
logical_account_fingerprint likewise identifies broker/environment/mode, not a
venue account UID or credential generation. This does not prove same-mode
account-rotation isolation. Adoption validates supplied logical identity, writes
Proposal/Order/Position as adopted, and rechecks open Position or pending native
entry ownership inside the write transaction. A new failure-injection regression
at exit-plan persistence proves earlier adoption rows roll back together.
The coalescing guard covers account-generation mismatches but does not change
the logical Fill key or prove collision-free account rotation for all writers.
Two actual entry-materializer regressions now prove that reusing a remote Fill
ID after changing account ID or credential generation in the same mode raises
the existing fills.id uniqueness error and rolls back all Order/Fill/Position/
Proposal changes on repeated attempts. This is fail-closed history protection,
not support for collision-free account rotation. insert_fill uses plain INSERT,
not replacement or conflict-ignore. Observed-close code separately checks an
existing Fill's Order ownership before reuse. No key migration was introduced;
all-writer/read-side rotation semantics remain unverified. The focused entry,
Fill, reconciliation and observed-close group passed 152 tests.

Observed-close follow-up at the uncommitted base: close_position_verified passes
materialize_close into the existing pnl.close_position owner. Its callback
revalidates current Position identity and materializes external private fills
inside that close transaction; it does not independently settle economics.
Existing regressions cover rollback when close-event persistence fails and
nonfinite durable fills. Added five same-ID conflict cases (finite price,
quantity, fee, currency and timestamp), each retried twice: all reject without
changing Orders/Fills/Positions/close events or creating Outcome hook jobs.
This is bounded collision/atomicity evidence, not completed cross-account and
all-caller proof for the full materialization subsystem.

These are observed API owners and callers, not a blanket claim that each
logical authority already has exactly one producer. Repository field guards
are narrower than generic SQL but do not alone prove application ownership.

| Authority | Current persistence boundary and actual caller families | Remaining distinction |
| --- | --- | --- |
| Candidate creation | `repositories.publish_candidate_snapshot`; Universe refresh, Serenity publication and isolated preview seed call it | Snapshot/member creation converged; attention/retention writes remain distinct operations. |
| Generation / EntryPlan | Repository generation claim/bind/defer/complete APIs; `transition_entry_plan`, trigger binding and monitoring | Planner/compiler/watcher ownership and multi-leg aggregation remain Core; complete callback/dynamic-write audit still needed. |
| Proposal | Repository immutable Proposal helpers and native per-leg construction | Research-bundle import rejects Proposal rows; complete callback and dynamic-write audit remain separate from the importer boundary. |
| RiskDecision | `risk.engine._persist_risk_result` calls ordinary or authoritative insert; authoritative repository insertion owns deduplication | Preflight and final exact-order decisions are different facts, not interchangeable duplicate implementations. |
| Order | `insert_order`, `transition_order`, `update_order_metadata`, `replace_order_error_reason`, acknowledgement finalization | Coordinator/Gateway, reconciliation/materialization, observed TP/SL and adopted/administrative evidence producers remain to classify; pending-close reason-only cleanup now uses the repository CAS writer. |
| Fill | `insert_fill` called by Execution exchange materialization, including observed external-close evidence; Outcome closure delegates inside its close transaction | Verified private fills persist under adopted or existing Orders; external Position adoption creates no Fill. Durable/raw ID collision, order/price/quantity/fee/currency/time comparisons precede replay. Administrative lifecycle classification remains distinct from fill provenance. `_verify_durable_fills` also validates/amends historical fee-source provenance. |
| Position entry | `insert_position`: native private-fill materialization or explicit external-position adoption | Distinct provenance. Entry coalescing validates same-Proposal/Plan ancestry, account, primary authorization and unique ownership; adoption rollback is separately tested. |
| Position field groups | Repository materialization / exit-plan / observation / closed-settlement field allowlists and status CAS | Producers span materializer, position manager, protection coordinator, reconciler and PnL; field ownership is not proof of one high-level owner. |
| Close lifecycle | `finalize_position_close`: `pnl._commit_close_economics` or `outcome_closure.close_position_administrative` | Administrative lifecycle closure must remain distinct from cost-complete strategy Outcome admission. |
| Final PnL / eligibility | Shared `pnl._commit_close_economics` and `analytics.outcome_integrity`; funding recovery and hooks use existing boundaries | Owner accepted the #3134 D1 same-Plan per-leg ancestry exception in Learning §11.1. Shared writer/read eligibility checks retain exact account and economic gates; no historical backfill or runtime qualification is claimed. |
| Pause / resume | `agent_runtime.pause_ownership` durable leases, lock and config mutation; scheduler pause/resume and resume gates consume it | Separate owner leases coordinate one pause state; optional components may not bypass leases or independently clear authority. |
| Execution identity | `config.exchanges.EXECUTION_MODE_ENVIRONMENTS`, `EXECUTION_MODE_VENUES`, and derived Demo/Live/External sets; Gateway, Risk, lifecycle cleanup, startup scope and reconciliation consume these helpers/sets | UTA profile composition and venue-native recovery branches remain explicit exceptions; unknown identities stay unavailable. |
| Historical Position repair | Schema migrations, bounded Outcome remediation and fixed-target #3111 overlap repair | The #3111 helper is explicit module CLI only, exact-snapshot/evidence checked and audit-idempotent; no production importer/caller. No repair was executed in this audit. |

## Fitness baseline and next proof

Exact-base focused baseline: 32 passed, 2 failed, 12 spec claims. Failures were
the omitted fixed-target #3111 repair in the raw-Position-writer inventory and
the trust policy's missing `spec_pipeline_execution_chain.py` reference. That
file was deleted in `e684157c89850a83510e19ea9b97fdd997fe0acf` when Paper
Internal was retired, not replaced by another simulated execution path.

The evidence-only correction records the existing bounded repair and prevents
production imports of it. The policy keeps all controlled paths unchanged and
points to current exchange Entry-to-Position plus private-fill materialization
tests, alongside existing execution specs. No safety requirement is removed.
Afterward, the combined architecture/ownership/repair/UTA/trust/execution and
replacement exchange tests pass: 117 tests / 57 claims. These are isolated local
fixtures, not exchange qualification or proof of the entire #3134 DoD.

The current-head identity audit also removes repeated external-mode enumerations
from generic Gateway/position routing, pending/stale cleanup, broker funding
settlement, Demo Risk freshness, startup scope, and account-mode reconciliation.
This is structural reuse only: it does not broaden the supported account set or
change a venue-native branch. Unknown execution identities remain unavailable.

The following Entry-route pass removes the last repeated current-route predicate
from generic Entry/Risk consumers. Compiler, runtime authority, Entry execution,
Gateway deferral/tactic checks and same-Plan Risk handling now consult the single
`ENTRY_EXECUTION_MINIMUM_PERMISSION` capability registry; Risk demo account
matching uses the registered execution environment and live exposure uses the
registered live-mode set. The current Bitget Demo + Full policy is unchanged,
and the architecture fitness test rejects a future hard-coded `bitget_demo`
comparison in these consumers. UTA profile composition, OKX sandbox loading,
Bitget TPSL/recovery and self-contained account allowlists remain explicit
extension/profile exceptions rather than generic Core route gates.

The dynamic-SQL pass found no remaining direct generic Order lifecycle write in
`pending_close_recovery`: its owner-authorized reason replacement now uses
`repositories.replace_order_error_reason`, with an exact status/reason CAS and
no collateral field rewrite. A second AST/literal-DML pass over production
modules found no other generic writer for the authority tables enumerated above.
The remaining direct statements are bounded owner exceptions:
`exchange_materialization` upgrades `fills.fee_source` while verifying its own
durable Fill, `pnl._commit_close_economics` completes the matching close-event
snapshot inside the same accounting transaction, and the two `maintenance/`
Position statements are explicit, fixed-target historical repairs with no
runtime caller. Namespaced settings, Observation, Learning, audit-mirror and
research tables retain their own owners and are not Core lifecycle writers.
These exceptions are therefore not folded into the ordinary Order/Position
lifecycle APIs or treated as a second producer.

The optional-extension absence pass is now merged at this checkpoint. Scheduler
owner loading catches a missing Observation package as `unknown`; startup skips
the optional cycle without leaving a half-initialized Scheduler, and settings
reconciliation removes stale registrations for worker, unknown, or unavailable
owners. Missing Learning hooks no longer stop Core job guards or failure
accounting, while the Agent Shell diagnostic projection reports an unavailable
advisory context. Telegram import/configuration failures are likewise logged by
type and do not abort the Core lifespan. Focused regression evidence is 88
scheduler/Observation/lifespan tests plus 19 architecture tests; this is source
and mocked absence evidence, not runtime deployment or qualification.

Next runnable seams are concrete private-account callers in the
[private-I/O audit](SLIM_CORE_PRIVATE_IO_2026-09-13.md), optional lifecycle
reachability already covered above, and the remaining per-consumer single-writer
evidence. The bundle
importer seam is closed for current Core Proposal/retired-PS writes, while
research artifact overwrite and full restore remain separate Ops boundaries.
Logical ownership and absence tests must precede Scheme A folder movement.
Multi-leg realized admission remains a distinct contract-decision boundary;
fixed-4h counterfactual learning does not by itself authorize admitting a new
realized Outcome cohort.

The architecture-map seam is now merged at this checkpoint. The maintained
architecture guide, System Map, Agent Contracts, and three README D2 diagrams
describe the current Candidate → Planning Generation → Entry Plan mainline;
retired PaperTrader/Ranker and learning evaluator/catch-up/lifecycle jobs are
explicitly historical or absent from the Scheduler. The Position Manager
cadence is documented as 30 seconds. This is documentation and comment
conformance only; it does not prove deployment, exchange qualification, or
runtime health.

The execution-identity seam is now merged at this checkpoint. The duplicate
`execution.venue_capabilities.EXECUTION_MODE_TO_VENUE` map was removed; Gateway
and normalized capability loading use the existing
`config.exchanges.EXECUTION_MODE_VENUES` registry, including the registered OKX
Demo identity. OKX Demo's CCXT sandbox capability loading remains a specialized
venue-native consumer, while the registry is the sole generic mode-to-venue
identity source. Focused local evidence is 82 architecture/spec tests and 212
execution/registry/pipeline tests; no venue, permission, route, or deployment
authority changed.

The reconciliation identity seam is now merged at this checkpoint. Its native
Bitget and non-Bitget UTA/CCXT mode partitions, plus the CCXT live subset, are
derived from `UTA_EXECUTION_MODES` and the shared environment/venue registry
instead of a second literal mode list. This preserves the existing venue-native
recovery exception and unknown-outcome handling while preventing future mode
identity drift. Focused evidence is 105 reconciliation/UTA/identity/pipeline
tests with Ruff and local preflight/plan passing; no account, route, or runtime
authority changed.

The profile-generation seam is now merged at this checkpoint. A Bitget profile
change advances the UTA configuration generation for every registered Bitget
execution identity, so cached demo/live account evidence cannot survive a
credential/profile switch. The Doctor demo margin gate likewise consumes the
registered demo/testnet identity set, and native Bitget TP/SL recovery derives
its account environment from the same registry. These are identity/conformance
repairs only; credential scope, Risk policy, submission authority, and runtime
state are unchanged.

The stale-test retirement seam removed a test-only import of the already-retired
Gateway lock module; historical `ps_*` schema/read surfaces remain untouched.
The active model healthcheck documentation now describes the shared inference
broker rather than a retired OAuth-refresh path. Neither change adds a runtime
mainline, compatibility facade, transport, credential authority, or external
I/O. Local focused tests, Ruff, documentation governance, and diff checks pass;
remote CI remains retired and is not evidence.

The #3201 readiness seam is now merged at this checkpoint. Frontdoor exposes an
explicit `/api/ready` route that probes the existing backend `/_infra/livez`
endpoint through a short-lived client outside the ordinary proxy pool. A
retryable failure preserves the existing liveness response shape while adding
bounded `Retry-After`, `backend_unavailable`, and `no-store` headers; the
frontend's shared React Query retry delay consumes that server value. This is a
control-plane transport repair only: backend business routes, Risk, Gateway,
execution, and account authority are unchanged. Frontdoor, focused frontend
communication, client Vitest, TypeScript, ESLint, API/settings type checks,
build/performance budgets, Ruff, docs governance, preflight, local CI contract
tests, and diff checks passed locally. It does not prove #1884's production
slot-owner attribution, stream/finite-pool isolation, Fedora runtime, or
qualification.

The public capability seam is now merged at this checkpoint. OKX demo market
metadata is loaded by the existing generic CCXT factory in
`data.ccxt_provider.load_public_market_capabilities`; the concrete
`OKXClient.load_demo_markets` duplicate and the Core import of that class are
gone. The helper preserves active USDT swap/spot filtering, sandbox selection,
loop-local cleanup, and the known-empty versus unavailable distinction used by
the symbol cache. This is a public read-only capability repair: private account,
order, Risk, Gateway, settlement, and credential ownership are unchanged.
Focused execution/registry/UTA/capability tests (47), Ruff, and diff checks pass;
remote CI remains retired and is not evidence.
