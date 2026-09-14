# Slim Core production ownership inventory

Source checkpoint: `29eb0b0ce485e9d90c49c318fbc32b1b3a32a433`, under
[#3134](https://github.com/iridyne/aletheia/issues/3134). This is an inventory of
current boundaries, not a declaration that compartmentalization or all single
writers are proven. No runtime/data migration follows from classification here.

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

These are observed API owners and callers, not a blanket claim that each
logical authority already has exactly one producer. Repository field guards
are narrower than generic SQL but do not alone prove application ownership.

| Authority | Current persistence boundary and actual caller families | Remaining distinction |
| --- | --- | --- |
| Candidate creation | `repositories.publish_candidate_snapshot`; Universe refresh, Serenity publication and isolated preview seed call it | Snapshot/member creation converged; attention/retention writes remain distinct operations. |
| Generation / EntryPlan | Repository generation claim/bind/defer/complete APIs; `transition_entry_plan`, trigger binding and monitoring | Planner/compiler/watcher ownership and multi-leg aggregation remain Core; complete callback/dynamic-write audit still needed. |
| Proposal | Repository immutable Proposal helpers and native per-leg construction | Research-bundle import rejects Proposal rows; complete callback and dynamic-write audit remain separate from the importer boundary. |
| RiskDecision | `risk.engine._persist_risk_result` calls ordinary or authoritative insert; authoritative repository insertion owns deduplication | Preflight and final exact-order decisions are different facts, not interchangeable duplicate implementations. Revalidated at `62ae485498e107607e75ae9635a055958a6c05d4`: named writer imports/calls are confined to Risk Engine and the canonical repository delegation; literal table writes are restricted to `insert_risk_decision` or migrations. The guard covers Core, Extensions, Legacy, Ops and scripts, including relative imports, keyword table arguments, quoted SQL identifiers and forbidden repository wrappers; arbitrary dynamic SQL remains outside this lexical proof. |
| Order | `insert_order`, `transition_order`, `update_order_metadata`, `replace_order_error_reason`, acknowledgement finalization | Creation is partitioned by fact: Coordinator owns submission intent, exchange materialization owns observed closes including native TP/SL claims, and position reconciler owns explicitly adopted external exposure. Scheduler delegates observed claims and no longer creates Orders or claims Position close state. These distinct facts must not be collapsed into a new submission path. Pending-close reason-only cleanup uses the repository CAS writer; remaining transition/callback/dynamic-write proof is still open. |
| Fill | `insert_fill` called by exchange materialization and observed administrative close evidence | Private Fill normalization versus administrative synthetic evidence must not be conflated. `_verify_durable_fills` also validates/amends historical fee-source provenance. |
| Position entry | `insert_position`: native private-fill materialization or explicit external-position adoption | These represent distinct provenance. Generic compatible-position coalescing needs exact Plan/account audit before changing it. |
| Position field groups | Repository materialization / exit-plan / observation / closed-settlement field allowlists and status CAS | Producers span materializer, position manager, protection coordinator, reconciler and PnL; field ownership is not proof of one high-level owner. |
| Close lifecycle | `finalize_position_close`: `pnl._commit_close_economics` or `outcome_closure.close_position_administrative` | Administrative lifecycle closure must remain distinct from cost-complete strategy Outcome admission. |
| Final PnL / eligibility | Shared `pnl._commit_close_economics` and `analytics.outcome_integrity`; delayed cost completion calls the same commit operation | The eight named settlement calls are classified below. Attribution retry may update only eligibility/reason; expired funding may update only its exclusion/evidence fields; exit explanation may update only reasoning. These operations cannot directly write final PnL. Owner-approved same-Plan per-leg ancestry remains unchanged; no historical backfill or runtime qualification is claimed. |
| Outcome feedback delivery | `portfolio.outcome_hooks` owns enrollment, eligibility reread, job leases/retry/completion and invalidation transaction fence | Existing Web/CLI composition binds a synchronous optional executor. `learning.outcome_feedback` owns Serenity post-mortem/opportunity feedback and delegates shadow feedback to `counterfactual.trades`; Core PnL no longer implements these optional writes. Absent feedback leaves committed close/economics unchanged and delivery durably failed/retryable, never falsely completed. |
| Risk lesson evidence | Core `repositories.list_lessons` reads persisted rules; Risk Engine interprets matching/static/category/expiry/dry-run facts and owns every verdict | Optional `learning.risk_evidence` supplies only the existing exact treatment result via Web/CLI binding. Missing treatment is fail-closed when needed; manual/owner/human constraints remain readable without Learning imports. No rule authoring, experiment admission, epoch, or Risk policy changes. |
| Demo-entry quality epoch | Core `risk.demo_entry_quality` keeps quality predicates and unavailable tokens; optional Learning retains epoch persistence | Existing Web/CLI bootstrap binds the writer without invoking it. The original check-time call, epoch payload/hash/history and failure rejection remain unchanged; Core no longer imports the concrete Learning implementation. |
| Pause / resume | `agent_runtime.pause_ownership` durable leases, lock and config mutation; scheduler pause/resume and resume gates consume it | Separate owner leases coordinate one pause state; optional components may not bypass leases or independently clear authority. |
| Execution identity | `config.exchanges.EXECUTION_MODE_ENVIRONMENTS`, `EXECUTION_MODE_VENUES`, and derived Demo/Live/External sets; Gateway, Risk, lifecycle cleanup, startup scope and reconciliation consume these helpers/sets | UTA profile composition and venue-native recovery branches remain explicit exceptions; unknown identities stay unavailable. |
| Historical Position repair | Schema migrations, bounded Outcome remediation and fixed-target #3111 overlap repair | The #3111 helper is explicit module CLI only, exact-snapshot/evidence checked and audit-idempotent; no production importer/caller. No repair was executed in this audit. |

## Observed-close ownership convergence

The current-head audit at base `64a14b3e50f0fc74376043ad9360231b206b56df`
found `scheduler._claim_observed_tpsl_close` creating an Order and claiming
Position close state directly. The transaction now lives as
`execution.exchange_materialization.claim_observed_tpsl_close`; the old
definition is deleted and the scheduler calls the materializer after the same
exact-account/remote-order proof. No exchange command is introduced.

The operation remains distinct from Coordinator's active `_claim_close`:
observed execution starts as `materialization_missing` with a known remote
order ID and unknown private fills; active close starts as `pending` before a
new external command. Combining them would conflate observation and authority.
The existing claim identity/conflict/idempotency checks, immediate transaction,
causal links and subsequent private-fill materialization are unchanged.

The bounded architecture guard rejects the demonstrated scheduler-to-Order or
close-claim writer calls/imports. Existing PL-TPSL regressions cover exact
account membership, configuration rotation, pending-claim conflicts and
private evidence. This is not an arbitrary dynamic-SQL proof, a complete Order
transition-writer audit, or runtime qualification.

## Fitness baseline and next proof

### Closed-Position settlement call inventory

At base `54795398bd91f0b58db03dc06cb599a740c49b7c`, AST inspection across
production roots and scripts found eight calls to the final-close, closed-field
and canonical economic-commit operations. They represent these distinct facts:

| Fact / caller | Mutation boundary | Proof required by existing implementation |
| --- | --- | --- |
| Initial economic close: `pnl.close_position` | `_commit_close_economics` then `finalize_position_close` | Current open Position, close evidence/completeness assessment, caller's writer transaction, atomic close event and hook enrollment. |
| Late cost completion: `broker_funding_settlement.materialize_exchange_close_costs` | The same `_commit_close_economics`, then `update_closed_position_settlement` | Unchanged incomplete close and matching NULL-economics close event; rechecked funding/fee evidence, no overwrite of known costs/economics. |
| Administrative closure: `outcome_closure.close_position_administrative` | `finalize_position_close` | Exact reconciliation identity/evidence and open-state CAS; no verified exit, final PnL or eligible strategy Outcome is fabricated. |
| Attribution retry: `outcome_hooks._load_eligible_position` | Closed-field writer: `outcome_eligible`, `outcome_exclusion_reason` only | Existing `attribution_read_failed` marker, re-read under writer lock, shared eligibility assessment and audit. It is not a cost-completion operation. |
| Expired funding: `broker_funding_settlement._terminalize_funding_history_expired` | Closed-field writer: `funding_source`, eligibility/reason, reconciliation evidence only | Typed retention proof, exact unchanged closed snapshot and one matching NULL-economics event; stale evidence is a no-op. It does not turn unavailable funding into zero. |
| Exit explanation: `pnl.persist_exit_reasoning` | Closed-field writer: `exit_reasoning` only | Position already closed; explanation cannot create a close or alter economics. |

`tests/test_position_update_ownership.py` now checks the eight exact named-call
owners, rejects named-writer callback/alias escape and restricts the three narrow field
writers. The existing raw-Position guard is expanded from an uppercase
substring under `aletheia/` to literal SQL and generic-helper checks across
`aletheia/`, `core/`, `extensions/`, `legacy/`, `ops/` and `scripts/`, including
case, quoted identifiers and schema-qualified names. Existing repository,
migration and explicitly bounded historical-repair exceptions are unchanged.

These are executable named-call/literal-write protections, not arbitrary
dynamic-SQL or every callback/data-flow proof. Existing cost, administrative
close, attribution retry and retention regressions establish the behavioral
distinctions above. This batch changes no production code, state transitions,
eligibility rule, repair authority or historical data.

### Earlier focused baseline

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
