# Canonical Entry Lifecycle #2851 convergence evidence

Starting master: `4031bed5d1dd7f5e5c6f56abbd06ba32b32a1ed9` (2026-09-10).
Branch: `converge/entry-lifecycle-2851`.
Scope: owner-approved #2851 convergence, absorbing #2720, #2864 and the
no-EntryPlan/Compiler dead-end in #3009. Broader #2886 economics/readiness/Alpha
work remains outside this completion.

Implementation and scoped Fedora deployment, formation and recovery acceptance
are complete. Main convergence #3026 merged as
`e7904a7d44816beb785c0a1a0e4ee025d6de6010`; corrective #3028 merged as
`1543c7c1e705de816a13e63f3a4aa3f9f8bd8038` and was deployed through normal
`update --verbose`. Final evidence below supersedes earlier deployment-pending
statements. This is not profitability, Live or full triggered-trade qualification.

## Authority and production ownership

The [current contract](../../design/contracts/entry-plan-contract.md) and
[accepted proposal](../../design/proposals/canonical_entry_lifecycle.md) define
the target. Later merged repairs take precedence over superseded issue wording:
#2875 versionless identity, #2884/#2893 requalification, #2945 single PI/Serenity
mainline, #3003 trigger resynchronization, #3007 exchange vocabulary and #3022
Paper Internal retirement are preserved.

Universe and Serenity publish through `ensure_candidate_planning_generation`.
PI and scheduler invoke the same `run_entry_planner_batch`, claim owner and
`plan_next_candidate`. Frozen context and validated EntryHypothesis feed
`compile_entry_plan`; `complete_candidate_planning_with_plan` activates its
result. Fast Monitoring earns and persists trigger activation before
`execute_entry_plan` hands a receipt to the existing Risk/Gateway boundary.

Candidate attention remains with Universe and its planning association. Cycle,
input, attempts and outcomes remain in `candidate_planning_generations`. Plans
remain in `entry_plans`; immutable activation facts reference those Plans.
Proposal/Order/Fill/Position retain execution ownership. `entry_lifecycle.py` is
a shared projection, not another ledger. No numeric production selector, new
Candidate/Planning/Plan ledger, economics change or Live authority is introduced.

## Acceptance matrix

Statuses describe the starting baseline and the resulting repository evidence;
they do not substitute for runtime qualification.

| Requirement | Starting baseline | Current evidence |
| --- | --- | --- |
| One Candidate/Planning production path | Already satisfied by #2945 | Both publication and scheduling callers traced to shared owners; preserved |
| Historical formats cannot authorize current execution | Partial: execution gate existed, watcher retained Shadow and old engine paths | Deleted Shadow producer and old TriggerEngine dispatch; old Plans follow cleanup only; canonical registry contains only anchor-retrace-reclaim |
| One model protocol and Compiler path | Partial: synthetic PlanIntent and unreachable treatment branches remained | Compiler consumes validated EntryHypothesis directly; obsolete protocol, trigger translator and treatment authority lookup deleted |
| Historical rows/JSON/hashes immutable | Existing read-only decoder | Production migration v77→v79 plus reopen compares every original domain column exactly; no activation or checkpoint fabricated for old rows |
| Behavior/schema/input/Git identities distinct | Partial: source formatting changed behavior identity and some executable dependencies were absent | Normalized AST ignores editorial edits; Compiler, trigger, activation, execution and shared generation check covered; schema remains independently derived |
| Current runtime authority before Plan activation/execution | Already present | Preserved and applied to activation binding; old or mismatched Plans cannot regain authority |
| Runtime/data/capability failure distinct from semantic NO_PLAN | Mostly present | Preserved non-economic outcomes and preflight; unavailable terminal UI no longer promises an automatic retry |
| Schema/reference failure terminal | Already present | Existing validation remains strict; fixture corrections retain original failure assertions |
| Unsupported mechanism/profile classification | Original issue wording superseded | Current contract's non-economic blocked classification retained |
| Transient Compiler recovery reuses frozen facts | Missing validated hypothesis persistence | Write-once checkpoint on existing generation; lease/attention/input CAS; resume skips model and works at zero model capacity; bounded attempts preserved |
| Post-Only crossing is market wait | Missing at frozen-context compilation | Fixed passive price survives compilation; fresh BBO still refuses crossing submission; no repricing/IOC conversion; monitoring and expiry show waiting truth |
| Terminal recovery and material requalification | Mostly present | Existing causal/hash/time/CAS gates preserved; later accepted structural recovery is permitted for terminal no-Plan cycles; exact pre-model dependency recovery remains distinct |
| Expiry and duplicate wakes produce one successor | Existing serial/CAS paths | Existing expiry, replay, rank jitter and lease tests retained; unresolved pre-Order Proposal/claim now also blocks successor |
| Canonical immutable trigger activation | Missing durable authority; caller geometry could submit | Typed proof binds Plan/hash/spec/geometry/events/generations/times; watcher CAS persists it; execution and Gateway validate stored receipt before claim and immediately before broker |
| Restart/reconnect/rearm preserves watchability | Partial | All watched Plans retain subscriptions, including valid IOC activation; healthy rearmed engines survive refresh; both tactics exercised through real loop/storage |
| Proposal concurrency preserves identity | Broad insertion exception could mask unrelated failures | Only Plan unique conflict recovers winner; receipt rebound/rehashed; other storage/hash/constraint failures propagate |
| Learning operational outcomes are not economic NO_PLAN | Current context filtering already satisfied | Preserved filters and thread concentration; retired geometry authority marked unwired while historical measurement identities remain readable |
| Workbench current state and cycle history | Partial: activation renewal lost history and SQL alias hid Plan link | Shared projection uses actual linked Plan, all activation facts, material monitoring transition time and truthful wait/terminal actions; rendered drawer tests pass |
| Paper Internal production route | Superseded and retired by #3022 | Not restored; obsolete fingerprint material and old test expectations removed |
| Fedora exact commit/schema/provider/session and natural formation | Baseline only | Verified 1543c7c1 / aletheia-sqlite-v79 / bitget_demo / session d96c5d259218; natural cycle b0a26e335f2f formed a Plan |

## Root causes and regression evidence

Each implementation defect was reproduced before repair. The executable tests
are retained in this branch; transient `/tmp` logs are supporting work notes,
not the durable source of acceptance.

| Root cause | Pre-fix counterexample | Retained verification |
| --- | --- | --- |
| Fingerprint hashed editorial source and omitted behavior dependencies | Six initial identity cases failed; shared generation helper mutation also left identity unchanged | `tests/test_entry_contract.py` |
| Compiler recovery discarded validated model result and required model capacity | Both profiles repeated inference or failed to resume at zero capacity; tampered checkpoint/Plan accepted | `tests/test_entry_planner.py`, lifecycle tests |
| Frozen crossing quote made a passive Plan deterministically invalid | Valid Plan failed with POST_ONLY_WOULD_CROSS | Planner, activation and execution tests retain fresh-BBO rejection and market wait |
| Caller-authored geometry stood in for activation | Both tactics reached Gateway without a persisted activation | `tests/test_entry_plan_execution.py`, `tests/test_entry_activation.py`; removing final pre-broker gate reproduces two failures |
| Loader discarded subscriptions or rearmed state | IOC restoration lost subscription; periodic refresh prevented new-generation sequence completion | Real watcher-loop cases in activation tests, both tactics |
| Historical production dispatch survived | Historical/missing specs constructed an engine; retired treatment requested legacy receipt | Watcher and execution rejection cases; historical measurement/replay checks preserved |
| Any insertion exception could recover a Proposal winner | Three unrelated storage/constraint/hash failures were swallowed | Real SQLite duplicate and error-propagation cases in execution tests |
| Successor gate began at Order, omitting earlier external ownership | Five pre-Order claim/status cases created unsafe successors | `tests/test_entry_lifecycle_cutover.py`, including recovery after lease expiry/resolution |
| Projection lost activation history and Plan identity | Two-tactic renewal and unknown transition-time regressions failed | Activation/lifecycle tests plus `frontend/src/pages/Universe.test.tsx` |

Baseline tests had 23 failures caused by a fixture relying on the retired
implicit Internal route. Explicit Bitget Demo/Full setup repairs that fixture;
original validation, freshness and cancellation assertions remain. Obsolete
PlanIntent-only tests and source-string checks were retired with their dead
implementation. Historical measurement compatibility remains tested. No new
Contract conflict was found: broad terminal structural recovery is explicitly
accepted by the later contract and was not narrowed based on older assumptions.

## Verification and remaining gates

Combined focused verification on this change set: **396 passed**:

```sh
uv run --no-sync pytest -q tests/test_entry_contract.py tests/test_entry_activation.py tests/test_universe_fast_trigger.py tests/test_entry_lifecycle_cutover.py tests/test_entry_planner.py tests/test_entry_analysis.py tests/test_entry_plan_execution.py tests/test_entry_learning_context.py tests/test_migration_fast_path.py tests/test_momentum_entry_geometry.py tests/specs/spec_entry_plan_contract.py tests/specs/spec_learning_registry.py tests/specs/spec_learning_contract.py
```

Frontend Universe rendered-markup checks: **14 passed**; frontend TypeScript
check passed. Focused Ruff, documentation governance and diff checks pass.
No full suite, local Gateway or remote CI was started for this convergence.

The real production migration-path test creates a v77 database with historical
Unicode and deliberately noncanonical JSON whitespace, snapshots every original
column of all domain tables, upgrades to v79, decodes historical Plans, and
reopens the database. Original values remain exact; new nullable fields stay
NULL; no synthetic activation appears. Only the migration ledger is excluded
from equality because its new migration facts must append. This proves the
isolated migration and read path, not preservation of Fedora's actual database.

The actual Fedora historical comparisons and deployed checks below complete the
scoped operational gate. Historical measurement formats remain read-only; their
presence is not a second current runtime. Live/Risk/Gateway authority, Candidate
Selection economics and Alpha remain outside this change.

## Deployed evidence and corrective review (#3027)

#3026 merged as `e7904a7d44816beb785c0a1a0e4ee025d6de6010` and deployed
through Fedora's normal update command. Verified session `54ad4d1f2a39` ran that
SHA with schema v79 and Bitget Demo. Health completed initialization successfully.
Two natural compatibility-cutover cycles first recorded execution-context
unavailability without model/Compiler invocation, then automatically recovered
and created Plans with validated hypothesis checkpoints. Two Workbench refreshes
preserved the same Plan/cycle identities. A normal Gateway restart produced
session `d0a700350619` on the same SHA; Plan hashes remained unchanged and market
monitoring reconnected. One Plan subsequently reached canonical Risk rejection;
the other remained in passive market wait. No runtime rows were injected.

Actual deployment comparison found zero missing/changed payloads among 135 old
Plans, 351 frozen inputs and 35 Entry Proposal attributions. Comparing the normal
pre-activation backup to the live database also proved all original columns of
151 historical runtime-unsupported generations and the existing Universe
discovery benchmark definition unchanged. All 151 classify as planning_blocked;
all 135 old Plans fail current runtime-authority validation. Live remains disabled.

The completed review of #3026 identified two executable defects and one stale
retirement surface, tracked separately in #3027:

- Renewed/current IOC quotes could validate while the stored Proposal retained
  a different price. Shared validation now compares rebuilt and immutable intent
  prices before claim and before broker submission. The pre-fix IOC counterexample
  fails; same-price intent and venue quantity normalization remain supported.
- An invalid receipt after claim could silently move executing back to pending.
  Both receipt-only Risk refusal and final activation validation now use the
  existing terminal error/audit helper, consistent with trust/execution contracts.
  Six pre-fix cases cover both tactics at preflight, authoritative Risk and final
  pre-broker validation. No broker call occurs; claim release is preserved.
  A still-pending, unclaimed intent may renew its activation, while a failed claimed
  attempt is terminal. Neither Proposal nor Plan hashes are rewritten.
- Workbench funding_readiness belonged exclusively to the paper_internal producer
  deleted by #3022. Remaining UI/types/API declarations are removed and archived;
  external funding settlement is not represented by that retired field.

Corrective verification: 232 focused backend/spec checks, 14 Universe checks,
frontend production build/performance, Ruff, docs governance and diff checks pass.
#3028 completed these corrections and its exact merged commit was deployed.

## Final Fedora acceptance and issue disposition

Source: the local `Entry-2851-evidence-2026-09-10` packet, including
`final-deployment-identity.json`, `final-historical-comparison.json`,
`historical-backup-proof.json`, `natural-plans-and-refreshes.json` and its final
README. The packet manifest's 13 file digests were verified for this sync. These
are recorded deployment observations on 2026-09-10, not a claim about a later
runtime or a deployment of this documentation-only change.

| Evidence | Final recorded result |
| --- | --- |
| Deployment identity (2026-09-10 04:02:01 UTC) | Running, disk, cached master and recorded SHA all `1543c7c1e705de816a13e63f3a4aa3f9f8bd8038`; verified Gateway process session `d96c5d259218`; schema `aletheia-sqlite-v79`; execution `bitget_demo` |
| Update and health | Normal update completed in 29,816 ms and restarted Gateway; subsequent health healthy, issues empty, pause blockers empty; deployed execution and Workbench source hashes match the tested tree |
| Final-commit natural formation | Cycle `b0a26e335f2f`: `planned` / `plan_created`, model_invoked=1, compiler_invoked=1, `entry_plan_created`, producer_commit `1543c7c1e705de816a13e63f3a4aa3f9f8bd8038` |
| Natural recovery and restart on e7904a7d | Cycles `9a21cd211442` and `46b3a9df17df` recovered from execution-context unavailability to Plans `7b07a362f74f` and `25963a63e7cb`; two refreshes and ordinary restart preserved Plan/cycle/hash identities and restored monitoring |
| Final immutable-payload comparison | 135/135 old Plan JSON/hash pairs, 351/351 frozen input/hash pairs and 35/35 Proposal attributions unchanged, zero missing; total rows at comparison were 137, 354 and 36 respectively |
| Backup equality and historical authority | All original columns of 151 historical runtime-unsupported generations and one Universe discovery benchmark definition unchanged; all 151 classify as `planning_blocked`; all 135 old Plans reject current authority with `ai_entry_running_contract_mismatch` |
| Workbench and Live | All sections available, no stale flags; active=1 and needs_planning=0; `live_disabled`, zero new live-mode Proposals since deployment began |

On the final deployed Fedora code, an isolated temporary runtime/database with
network/exchange fakes ran
`test_first_freeze_waits_for_exact_fresh_book_then_resumes` and
`test_expired_activation_after_claim_is_audited_terminal_error`: **16 passed in
2.10 s**. No stable database injection or real exchange call was used. The 396
main-convergence, 232 corrective and 16 deployed checks are different selected
groups, not a whole-suite metric. No remote GitHub CI was dispatched.

| Issue | Final disposition and evidence |
| --- | --- |
| #2851 | Completed: canonical implementation plus scoped exact-commit deployment, natural formation/recovery, refresh/restart and historical preservation acceptance |
| #2720 | Subsumed by #2851: accepted versionless compatibility cutover and natural successor recovery replace its older numeric-version proposal; historical facts remain immutable and refresh/restart does not duplicate cycles |
| #2864 | Subsumed by #2851: current/stored identity gates reject all 135 historical Plans; final-commit natural formation demonstrates the current path operates |
| #3027 | Completed by merged and deployed #3028: IOC immutable-price validation, claimed-attempt terminal audit and retired funding-readiness UI cleanup |
| #3009 | Resolved/subsumed by #2851 for the no-EntryPlan/Compiler dead-end: natural model and Compiler invocation now produces Plans, including on 1543c7c1; its existing completed state is retained |

No natural triggered Plan activation/order/fill was observed in the bounded
window. Trigger/concurrency/expiry regression fixtures are not real exchange
fill or economic qualification evidence. The independent Observation worker
reported `3a3f3b20a665` and is not used as proof for the new Entry implementation.
Full Gate B entry-to-close qualification and broader #2886 remain outside this
closure. No qualification window is opened or reset by this evidence sync.
