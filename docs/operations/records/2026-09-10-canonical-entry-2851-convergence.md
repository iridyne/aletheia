# Canonical Entry Lifecycle #2851 convergence evidence

Starting master: `4031bed5d1dd7f5e5c6f56abbd06ba32b32a1ed9` (2026-09-10).
Branch: `converge/entry-lifecycle-2851`.
Scope: owner-approved #2851 convergence, including #2720, #2864, #2886 and #3009.
This record establishes repository evidence. The changes have not yet been
deployed; production qualification is pending.

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
| Fedora exact commit/schema/provider/session and natural formation | Baseline only | Pending deployment and real runtime acceptance; local checks do not establish this row |

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

Before production acceptance, deploy the exact merged commit through the normal
Fedora `update` command and establish runtime commit, schema v79, provider,
session and Gateway/settlement health. Capture actual pre/post historical
payload/hash comparisons. Observe a naturally formed model/Compiler Plan and
verify refresh/restart idempotency, expired/current activation behavior and old
Plan non-authorization. Do not manufacture Candidate, market or economic rows
to make that gate pass. Health alone is not formation or trading qualification.

The last recorded Fedora observation was starting commit 4031bed5, schema v77,
Bitget Demo and healthy service state. It is a baseline observation, not evidence
that these changes are deployed or currently effective. Historical measurement
formats deliberately remain read-only; their presence is not a second current
runtime. Live/Risk/Gateway authority, Candidate Selection economics and Alpha
remain outside this change.
