# Slim Core production ownership inventory

## Current source disposition

The final physical batch is based on Ops cutover `c743d140cdc22e09b51977b80b12cb5e6bb648ca`.
Remaining Core source is `core/aletheia/`, with the sole Python namespace
`aletheia`; optional implementations, explicit operations and inert archive
readers have their existing `extensions/`, `ops/` and `legacy/` owners. There is
no old-root forwarding package. The package disposition table below is current;
earlier dated census values (including 528/48) and historical “remaining/open”
trace statements are not current counts, completion thresholds or a new queue.

Physical acceptance requires exact-head source identity, preserved source/frozen
resource roots, nonempty authority scans, optional-absence checks and unchanged
canonical writer assertions. Local checks do not establish Fedora deployment,
runtime qualification or economics; #1884/#3135/#2899 evidence remains separate.

### Operator fallback retirement (#3134, owner decision 2026-09-23)

TS operator-shell remains the sole runtime Operator. The former Python
`pi_operator_loop` scheduler/manual producer, config-action writer and
`operator.loop_enabled` fallback selector are retired. Existing assessments and
audit rows remain read-only history; no data migration or substitute writer is
introduced. Older references to that Python producer below are historical.
The separate Entry Planner daemon and its existing permissions are unchanged.

## Explicit Ops and inert Legacy owners

Human-triggered update, backup/restore, compaction and bounded historical repair
have unique implementations under `ops/`; the historical viability report lives
under `legacy/`. Core keeps Git identity facts, packaged runtime identity and its
single integrity error class, archive verification, capacity diagnostics and
ordinary backup retention. Retention preserves directory creation, ordering,
clamping and failure behavior without constructing an operational backup service.

The Core update initializer is inert. CLI/Web import operational implementations
only at their existing invocation boundaries; packaged feed pinning, activation,
rollback, continuity locks and embedded subprocess entrypoints use their actual
Ops owners. Existing source-package, bundle and frozen-entrypoint metadata includes
these owners without introducing a new distribution product. No migration, Risk,
economic, qualification or deployment semantics change. Focused cold-import proof
and the existing update/backup/continuity regressions cover this physical cut;
The final section above distinguishes Core root relocation from Fedora deployment.

## Remaining optional-family physical owners

Knowledge authoring/retrieval, Learning, Counterfactual, Policies, Sync and
Observation Lab now have unique implementations under `extensions/`; their old
`aletheia/` package paths are removed, without forwarding aliases. The four
policy-epoch helpers formerly under Agent Shell belong to Learning. Optional
filter controller/registry, learned-guard maintenance/reports and twin/relative
experiment analysis belong to `extensions/analytics`; explicit context-pair
evaluation belongs to Research. Existing Lab state paths, worker ownership,
cycle locks, event identities and logger names remain unchanged.

Core retains canonical Knowledge formats/index/persistence, the complete
`analytics/filter_evidence.py` fact/admission implementation, OutcomeEligibility,
verified economics and job lifecycle. CLI/Web bind the existing optional job
producers and one Counterfactual scorecard enrichment reader. Missing shadow
enrichment leaves real trade statistics available with `shadow_count=null` and
an explicit unavailable reason. Missing Knowledge disables only dependent
Research producers; Equity/Market observers and Core boot remain available.
No Risk policy, qualification, deployment, credential or execution authority is
changed. The local absence probes do not prove runtime qualification.

The same composition boundary binds Learning gate/start/skip/success/failure
callbacks as one job-lifecycle group, the existing Lesson facts reader and
Knowledge guard-candidate writer. Scheduler try/finally, attempt identity,
failure-recorded ordering and database cleanup remain Core; optional telemetry
cannot grant Learning execution when its gate is absent. Core diagnostic
projection and canonical settlement no longer import concrete Learning or
Knowledge implementations, even when installed. Focused structural guards cover
these named consumers in addition to fresh-process absence proofs.

## Supplemental Data family compartment

The unique yFinance macro, Polymarket, Position X material/features, positioning
evidence, structure shadow and Vergex aggregate implementations now belong to
`extensions/data`. Core retains shared DTOs, canonical derivatives facts, stored
context projection, repositories and canonical forward settlement. CLI/Web bind
only the exact macro/history readers and separate async structure-shadow settler;
missing supplements do not disable CCXT or canonical settlement. Prediction
composition checks its Data producer before any manual queue or prediction-run
write. Local prediction history remains available without the public fallback.

Explicit positioning/import/evaluation commands reject missing producers before
producer work; Doctor still reads stored structure facts independently. Vergex
remains owner aggregate import, not an automatic feed. Existing producer bodies,
policy epoch, whitelist, snapshot/shadow/audit transaction, return algorithms and
non-fatal shadow ordering are unchanged. No aliases, Equity algorithm, Risk,
economic, credential, schema or runtime changes are included. This is bounded
ownership/absence proof, not deployed or economic qualification.

## Lab storage-consumer boundary

Under accepted #3134, Main repositories consume six explicitly composed Lab
operations: authority, retention floor, validated retention refs, frozen priority
keys, active benchmark selection and the window outcome-write guard. Their sole
Lab implementations live in `observation_lab/main_readers.py`; CLI, Web and the
standalone retention-backfill entry compose them without reading runtime state.
Main retains its SQL writers and transactions; Lab retains claim validation,
window status and cycle exclusion. Missing authority/claims cannot widen deletion
or reopen legacy writers; unavailable priority evidence leaves ordinary settlement
available. The cycle guard spans status resolution, the Main transaction, pending
CAS and linked shadow update. Active-window reads remain Lab-first with the
existing legacy fallback; write permission remains legacy-row-first.

Focused proof covers absent/installed/broken-dependency composition, exact claims,
retention, window lock contention and Main settlement. The standalone Ops entry
uses the same bindings, not an implicit fallback. No schema, window, Risk,
economics, credential or deployment change is included. Physical Scheme A and
runtime qualification remain separate gates.

Current package census and mixed-owner corrections: source
`851aed04171b3943b1b11039a747ac930e663976` plus the Data cutover below, under
[#3134](https://github.com/iridyne/aletheia/issues/3134). Original inventory
baseline: `29eb0b0ce485e9d90c49c318fbc32b1b3a32a433`. The dated convergence
sections below retain their original proof scope; their historical next-step
wording is not the current work queue. This inventory is not a declaration that
compartmentalization or all single writers are proven. No runtime/data migration
follows from classification here.

## Shared Core formats and Knowledge persistence follow-up

Core runtime scope/identity, canonical Knowledge row/document history and FTS
maintenance no longer import optional Knowledge authoring/retrieval. The unique
format implementations live in `files/frontmatter.py` and
`files/knowledge_document.py`; `storage/knowledge_index.py` owns the existing
high-confidence index gate, explicit rebuild and removal. Schema-v3 strict
rebuild and Operator assessment use that same Core owner. Canonicalization,
100000-row rebuild bound, transaction ownership, integrity conflicts and logger
identities are unchanged; optional retrieval still owns advisory scoring.

Candidate timestamp validation uses `utils.dt.parse_utc_instant`, moved unchanged
from the temporal report. Its rejection of naive timestamps remains distinct
from `utils.dt.parse_utc`. Report bucketing consumes the same strict kernel.
Focused absence proof exercises actual scope validation, document persistence,
history, fresh migrations and Operator indexing without Knowledge; it is not a
claim that every optional Knowledge/Learning path is independent or Scheme A ready.

## Advisory Research compartment follow-up

Dreaming, Lesson Reviewer and the Serenity research worker now have their unique
implementations in `extensions/research`. Manual agents use the existing registry;
the existing Scheduler owns only the exact async extraction and sync enqueue
callbacks. Missing producers are omitted before cron/catch-up guards and before
create/retry/deep-dive durable writes. History, cancellation and startup interrupted
recovery remain Core-accessible. Producer bodies, shared policy epochs, cursor/CAS,
immutable lesson-policy history, draft skills and Core Candidate publication are
unchanged; this cutover does not claim runtime or whole-Learning absence proof.

Market Observer, Post-trade Reviewer and their sole shared durable round-robin
helper now live in `extensions/research`. Producer behavior is unchanged apart
from the helper import path: existing epoch/exposure/filter facts, exact admitted
Opportunity enrichment, central eligible-Outcome reads, knowledge/case/guard
writes and namespaced cursors retain their semantics. CLI/Web use the existing
registry; absent Research omits the two scheduler jobs and prevents manual queue
writes. Explicit paired context evaluation blocks before provider access when
Research is absent. Core registry/inference/telemetry and Portfolio Agent remain
in Core; the latter is a manual/scheduled snapshot delegate with canonical
Position-mark effects, not an optional Model-backed rebalancer.

Prediction Observer and Divergence Scanner now also live in `extensions/research`
without forwarding modules or producer-body changes. Their separate CLI/Web
composition block binds both through the existing registry; missing Research or
the existing Data adapter clears both bindings, not other Research agents. The
scheduler requires both bindings and preserves observer-then-divergence order;
manual Prediction refresh still enqueues only the Observer. Internal import
defects propagate. The existing normalized prediction/claim/Opportunity writes,
Serenity evidence and same-theme grade refresh, shared Learning policy-epoch
check, source-version dedupe, terminal filter facts and transaction/savepoint
boundaries are preserved. This is not whole-Research absence or runtime proof.

Equity Observer now also lives in `extensions/research`, retaining its unchanged
public-data/model algorithm and observation-only Opportunity writes. CLI/Web bind
it in the existing registry; absent Research omits the equity scheduler job and
marks its manual catalog entry unavailable. Eastern-time cadence, DST/misfire
behavior, permissions and execution eligibility are unchanged.

The unchanged Research Agent and Risk Critic implementations now live in
`extensions/research`; their former modules are deleted without forwarding
facades. Core retains the existing agent registry, inference helpers and execution
lifecycle. CLI/Web composition registers the two functions without running them;
an absent Research package clears stale registrations, while internal import
defects propagate. The manual catalog reports unavailable implementations rather
than implying they can run. Model calls, note confidence/admission, permissions,
scheduled cadence and Risk authority are unchanged. This bounded cutover does
not move the remaining observers/reviewers or prove whole-Research absence safety.

## Exchange compartment follow-up

Rechecked against base `c2bdd7f62e71c10432f2ee3296fb99e01fa92905`:
the existing UTA account factories and scoped credential HTTP ingress belong to
`extensions.exchanges.composition` and `extensions.exchanges.credential_routes`.
The CCXT order-rule reader and concrete transport-error classification belong to
`extensions.exchanges.ccxt`. Their former `aletheia.exchanges` files and package
initializers are removed without a forwarding facade. Core UTA identity,
registry/lifecycle and numeric quantity policy remain their existing owners.

Only CLI/Web and the two explicit maintenance entrypoints bind the existing
factories; binding performs no credential or network access. Web mounts the
existing credential routes conditionally. Missing Exchange clears the existing
bindings; unrelated internal import failures still propagate. The import,
composition, credential/redaction, exact-instrument quantity and UTA specs cover
this bounded cutover. This proof does not establish full Scheme A or
deployed/runtime/economic acceptance.

## Regime compartment follow-up

Rechecked against base `301a62d60ff1856a70c0f9a4d74e4bcff1032964`:
the three detection/collection modules now belong to `extensions.regime`, with
no surviving source or forwarding module at `aletheia.regime`. Detection,
debounce/cache, freshness, policy-epoch attribution and log categories are
unchanged. CLI/Web still bind the existing readers without collecting data.
Universe retains `insert_market_regime` and publication; Risk retains its
strict unavailable handling and every verdict. Historical Trade Insights reads
persisted labels without importing the detector (#3325). Package absence does
not stop publication/history; internal import defects are not optional absence.
This bounded move does not establish the ownership of other mixed packages.

## Public Data compartment follow-up

The existing CCXT provider, fallback chain, CoinGecko and Binance public sources
now belong to `extensions.data`; their old concrete source files are removed.
The implementation bodies and existing configured fallback policy are unchanged.
Core retains normalized DTOs, cache/freshness/market facts and canonical repository
writers. Public OHLCV persistence still delegates to the same repository function;
this move grants no new writer or credential authority.

CLI/Web/collector composition binds the existing factory and listing reader.
Missing adapter or parent package clears both bindings; unrelated internal import
errors propagate. Optional native collectors keep their existing concrete
metadata reads, while standard Core reads retain the provider port. This does
not move all Data or observers, or prove their whole-program absence safety.

## Coverage and entrypoints

The native collector follow-up places Microstructure, Derivatives and
VolumeScanner in `extensions/data/collectors` without body changes. The existing
Data provider reuses the relocated orderbook parser. Scheduler composition owns
cadence, live-snapshot persistence and health; sparse derivatives history calls
the same Core repository writers. Volume conversion remains the existing shared
numeric helper. These dependencies are not new lifecycle or writer owners.

The public-context collector follow-up moves the unchanged CoinGecko Top,
DefiLlama and Hyperliquid implementations into `extensions/data/collectors`.
Scheduler composition, canonical market persistence and health ownership stay
in Core. Hyperliquid's existing Learning policy-epoch provenance is preserved,
not reclassified as pure I/O. A missing adapter is recorded as context-lane
failure while the independent primary lane remains callable; other optional
Data dependencies remain outside this bounded absence proof.

Macro/FRED and Coinalyze now also live in `extensions/data/collectors`, with
unchanged bodies and the owner-approved own-credential-reference exception.
Scheduler retains snapshot persistence; Doctor consumes only Coinalyze credential
presence and persisted facts. FRED research attempt state and Coinalyze's existing
Learning policy-epoch side effect remain explicit, not erased by the move.
Stored macro classification still uses the Core freshness projection independently
of adapter availability. Context-lane absence and Doctor unavailability are bounded
proofs, not whole-program optional-subsystem or online feed qualification.

Options acquisition/GEX and IV derivation now live in the Data Extension. Core
retains the unchanged persisted IV DTO/version identifiers and repository writers.
The scheduler is the only production derivation composition path. Fresh-process
validation/persistence/history export works with all Extensions denied; this does
not grant options trading authority or prove online Deribit qualification.

News RSS acquisition, Model-backed analysis and their optional API now belong to
`extensions/data`. Their old modules are removed; shared exact-entity matching
and historical `batch_news` reads remain Core. Web composition skips an absent
news router but propagates unrelated internal import failures. Scheduler cadence,
bounded workers, pause/config gates, failure accounting, namespaced news writes
and existing Learning policy-epoch effects retain their owners and semantics.
Fresh-process route/history checks do not start a Gateway or qualify live feeds.

At the dated census checkpoint, tracked source contained 528 Python files under `aletheia`: 33
first-level packages and 12 root modules. `extensions` contains 48 Python files:
18 Exchange, 6 Model Endpoint, 20 Data, 3 Regime and one root initializer. Counts include
package initializers, exclude bytecode, and are dated observations rather than
brittle acceptance thresholds. Reproduce the census from `git ls-files
'aletheia/*.py' 'extensions/*.py'`, grouping by the first package below each root.
The original inventory contained 562 files under `aletheia`; that count is not
the current tree or a completion threshold. AST inspection covered
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

This is the current physical-owner map after the accepted optional/Ops cutovers
and final Core relocation. Python imports remain `aletheia.*` for Core; `core/`
is only its source root. Counts and boundary-open commentary in the dated trace
sections below are historical evidence, not a new implementation queue.

| Current physical package | Preserved authority / disposition |
| --- | --- |
| `core/aletheia` root modules | Thin CLI/bootstrap, shared paths/logging/networking and runtime-home guards. |
| `core/aletheia/agent_runtime` | Scheduler, jobs, runtime session and pause/lifecycle authority. |
| `core/aletheia/agent_shell` | Entry Planner, operator runtime and tools under existing permissions. |
| `core/aletheia/agents` | Registry, shared inference helpers and canonical Portfolio snapshot delegate. |
| `core/aletheia/analytics` | Shared Outcome eligibility/semantics, canonical filter evidence and operator read projections. Optional authors/controllers remain under Extensions. |
| `core/aletheia/backup` | Archive verification used by registered migrations; no backup creation/restore service. |
| `core/aletheia/cli` | Minimum controls and invocation-local optional/Ops command composition. |
| `core/aletheia/config` | Schema, redaction/storage and normalized execution identity; credential consumers retain their scoped boundaries. |
| `core/aletheia/data` | Provider port, cache/freshness, normalized DTOs/facts/geometry and collector process composition. |
| `core/aletheia/diagnostics` | Operator read projections; explicit repairs invoke Ops without turning a probe into apply. |
| `core/aletheia/economics` | Inference identity/cost provenance, budgets and pricing evidence. |
| `core/aletheia/execution` | Canonical Risk-gated submission, claims/reconciliation and broker fact materialization. |
| `core/aletheia/files` | Shared document codecs, paths and audit/artifact primitives. |
| `core/aletheia/gateway` | Existing operator plane, process/service lifecycle and front door. |
| `core/aletheia/infra` | Events and bounded error projections. |
| `core/aletheia/maintenance` | Runtime capacity/checkpoint/planner/retention and inert historical-accounting read projection. |
| `core/aletheia/model_provider` | Model port, routing/broker, guardrails and inference semantics; no concrete SDK transport. |
| `core/aletheia/portfolio` | Position/close/funding/PnL and durable Outcome job authority. |
| `core/aletheia/risk` | Canonical evaluation, policy/kill switch and separately persisted Risk decisions. |
| `core/aletheia/server` | Minimum API/lifespan and conditional route composition. |
| `core/aletheia/storage` | Canonical repositories/transactions/migrations, document index and persisted facts. |
| `core/aletheia/trading` | Typed Entry/compiler/capture/activation and canonical source publication. |
| `core/aletheia/trust` | Safety, Learning influence and Live authority; absence never grants permission. |
| `core/aletheia/universe` | Canonical Candidate/Generation semantics, normalized observations and settlement. |
| `core/aletheia/update` | Read-only Git/packaged identity, one integrity error type and shared process environment. |
| `core/aletheia/uta` | Normalized Exchange port/account lifecycle; concrete transports are Extensions. |
| `core/aletheia/utils` | Shared datetime primitive. |
| `extensions/{analytics,research}` | Optional observers/reviewers, analytical controllers and explicit context evaluation. |
| `extensions/{knowledge,learning,counterfactual,observation_lab,policies,sync}` | Unique optional authoring/measurement/history/bundle owners; canonical Core fields and approved permissions remain unchanged. |
| `extensions/data` | Public acquisition/collectors and optional evidence producers; FRED/Coinalyze own-reference credential exception is unchanged. |
| `extensions/{exchanges,model_endpoints}` | Concrete Exchange/Model transport and scoped credential composition. |
| `extensions/{regime,telegram}` | Optional market context and notification adapters, bound by existing composition. |
| `ops/{update,backup,maintenance}` | Explicit update/restore/compaction/repair operations; no autonomous authority added. |
| `legacy/historical_viability.py` | Inert archived report list/get/render only; no generation, revaluation or write. |

### Historical mixed-owner evidence and gate traces

The census covers every tracked first-level Python package, not every dynamic
runtime path. Current source traces establish the following distinctions:

- `agents.__init__` is the existing registry used by Core `orchestrator` and
  `AgentJobManager.enqueue`; the latter validates the function before persisting
  a queued run. `agents.helpers` supplies Entry Planner inference and
  `run_logger`/repository telemetry flushing. Moving these with Research would
  invert a Core dependency.
- `agents.portfolio_agent` delegates `portfolio.pnl.compute_and_store_snapshot`;
  it does not introduce another accounting algorithm. That Core operation also
  persists open-Position marks through the canonical observation-field writer,
  before filtering the portfolio snapshot to the configured execution mode.
- `extensions.research.research_worker` owns namespaced Serenity runs/evidence. It claims a
  queued run and acquires/releases the existing research lock; its bounded
  retry registers on the existing Core scheduler, not another APScheduler.
  The daily `dreaming_daily` cron, startup catch-up and explicit
  create/retry/deep-dive API calls enqueue the same worker. Before the stages,
  `ensure_opportunity_information_policy_epoch` persists shared
  `learning_policy_epochs` attribution; Learning is a real dependency here,
  not proven absent-safe. Stage writes are namespaced evidence, claims, themes, links,
  opportunities and run events; Model calls use the shared inference path.
  Candidate publication delegates to `trading.serenity_publication`, which
  revalidates source/PA facts and uses the canonical Candidate/Generation writers.
  Manual and automatic publication share that implementation, not Order/Risk
  mutation. The optional equity context can perform public yfinance I/O; this
  producer is not an inert reader.
  Queued claims and worker/API terminal transitions are CAS-guarded; startup
  recovery's failed transition instead uses the ordinary unrestricted run update.
  Retries use a durable parent/retry identity. Cancellation is cooperative at pipeline checkpoints,
  as required by Serenity Contract §5.1/§13, not physical thread termination.
  Web lifespan does not join this daemon thread; actual process exit removes it,
  and scheduler startup reconciles interrupted runs before catch-up. Existing
  manual AgentJobManager owns different `AgentRun` jobs, not Serenity attempts;
  neither thread count nor similar status names prove duplicate Core authority.
  On `026062610`, 107 focused workbench/scheduler/publication checks (11 claims)
  pass. This proves the bounded source/fixture flows, not a Fedora shutdown or
  a hard-stop guarantee, and does not justify a new worker framework.
- `data.price_feed` and `history_downloader` now construct exact-venue providers
  through the existing Data port (#3330). Cache/event and history-writer owners
  remain unchanged; this does not classify every collector as Core.
- `data.context_builder` owns the pure stored Position X advisory projection.
  Core attribution and historical rendering do not import `data.position_x`;
  offline material parsing and holder/batch calculations remain optional producer
  work, not Core read dependencies. The fresh-process absence regression uses
  persisted market facts and the real attribution reader; no online qualification
  or whole optional-subsystem absence is inferred from it.
- Missing `learning.control` fails classified Learning jobs before Model or
  knowledge effects (#3331), while Core/measurement jobs continue. Classification
  has one definition in `trust.learning_safety`; this is not blanket absence
  proof for the entire optional tree.
- `economics.historical_viability` and the HTTP viability report projection are
  inert archive readers for explicit CLI/API reads. They use the existing read-only database context, not
  current economics schema initialization/backfill. Missing history remains
  empty without creating a database or tables; historical report values,
  ordering and pagination are unchanged. This does not classify the active
  inference ledger, pricing worker or paired-evaluation writers as Legacy.

### Agent producer disposition

Source trace at `eb87f2202e4a77184998d39179e04b363741af6d` separates the
remaining producer functions from the shared registry/inference helpers above.
Existing scheduler/manual execution owns their invocation; this table does not
grant a producer its own scheduler or imply that removing its dependencies is safe.

| Producer | Observed effects and ownership constraint |
| --- | --- |
| `research_agent` / `risk_critic` | Shared Model calls and knowledge notes, not Candidate/RiskDecision writers. Research notes use speculative confidence; Risk Critic reads Proposal/Risk history and writes a medium-confidence advisory note. Knowledge admission remains a separate gate. |
| `portfolio_agent` | Resolves the configured execution identity and delegates `compute_and_store_snapshot`. Its `compute_portfolio(persist_marks=True)` first calls `mark_positions_to_market`, which persists `unrealized_pnl`, its source and available exact `current_price` through `update_open_position_observation` across open UTA Positions; snapshot mode filtering happens afterward. This is Core Position observation plus portfolio snapshot persistence, not a Model-backed optional rebalancer or a second accounting algorithm. |
| `equity_observer` | Public yfinance bars/live quotes and request-local Data-adapter token quotes, shared Model interpretation, observation-only equity Opportunities and telemetry. It reads historical strategy status only to attach `linked_strategy_id`; no strategy lifecycle transition occurs. |
| `prediction_observer` | Public prediction acquisition, shared Model interpretation, prediction run/event/market/snapshot/claim persistence, observation-only Opportunities and optional Serenity evidence. Prediction research is not Exchange order authority. |
| `divergence_scanner` | Reads prediction observations, resolves a public spot ticker and closes its per-call provider in `finally`. It writes shared Learning epoch attribution and filter evidence; Opportunity creation and its terminal producer fact share a transaction/savepoint. Current code sets `execution_eligible=False` despite the old module header. Source-version deduplication is research identity, not Candidate/Order admission. |
| `market_observer` | Reads the exact open snapshot/member carrier and verifies persisted emitter admission before enrichment. It writes pre-filter evidence, Learning attribution/exposure, inference telemetry, namespaced latency/cursor settings and open-Opportunity enrichment. Scanner direction/score stay producer-owned; no standalone Opportunity or Candidate is created. Public Data prefetch and shared Model inference are real side effects, not an inert historical reader. |
| `post_trade_reviewer` | Central `eligible_strategy_outcomes` controls cohort admission after a cheap closed-Position prefilter. Shared Model calls write medium-confidence knowledge reviews; deterministic case/guard generation uses the existing case builder. Shared Learning epoch and durable forward/reopened-prefix cursor writes are additional effects. No final PnL/eligibility writer is introduced. |
| `dreaming_agent` | Full manual agent execution writes knowledge summaries/daily reviews, invokes `_extract_lessons` and attempts a Git artifact snapshot. The scheduled `dreaming_daily` wrapper enqueues the existing Serenity worker and invokes the same extraction helper; it does not call the full manual function. Both entry paths can propose lessons from supported review evidence, route automatic block recommendations to shadow guard candidates and advance the extraction cursor. Historical strategy summaries do not restore strategy execution authority. |
| `lesson_reviewer` | Reads scoped Candidate outcome evidence, applies deterministic floors and shared Model review, then uses LessonStore CAS and a transaction to accept/reject/apply lessons and create immutable policy versions. Draft-skill creation/recovery is another namespaced write; it does not activate skills or mutate RiskDecision/Order/Position. `ps_policy_versions` is current optional policy history, not proof of a retired PS execution owner. |

`agents.round_robin` persists namespaced `settings` cursors used by the bounded
observer/reviewer loops; it is not a pure ordering helper. The registry's lazy
module discovery, scheduler's direct lesson-extraction import, Doctor's reviewer
probe and context-effectiveness evaluation's MarketRead/prompt import remain
physical-cutover consumers. They must be accounted for when compartmentalizing
these producers; moving the whole `agents` package would also move Core helpers.

Focused observer/knowledge/lesson regressions on this base: 170 passed, 3 skipped,
28 spec claims. The skips cover two retired `pi_agent_*` layout probes and a
production-database-dependent lesson check; they are not verified behavior.
Together with the separate Serenity worker trace, this establishes the listed
source dispositions, not complete optional-package absence safety, Fedora
shutdown behavior, deployed economics or readiness for the whole Scheme A move.

### Analytics shared rules and active writer disposition

These source traces separate shared trading evidence rules from optional report
production. They do not classify all Analytics modules as optional or read-only.

| Observed flow | Owner, side effects and movement constraint |
| --- | --- |
| `universe.service` → Candidate selection bundle → `candidate_eligibility` / `economic_utility`; fixed-horizon recorder → `candidate_funding` | Current Candidate provenance, cohort cutoffs and frozen funding proof consume `analytics.temporal.parse_utc_instant`. It rejects naive timestamps; `utils.dt.parse_utc` accepts them as UTC. They are not interchangeable. Temporal PnL bucketing also uses the strict parser but does not own Candidate admission. |
| `outcome_integrity.assess_reportable_outcome` → `learning_window` | Versioned clean-window and incident exclusions are shared reportability rules, not optional Learning configuration. `portfolio.outcome_hooks` uses this assessment when the existing attribution-retry owner persists eligibility/reason. The window helper itself is pure; it does not settle a Position or rewrite history. |
| Repository calibration/pressure/spread readers → `learning_window.clean_window_cutoff` | Query-time cohort restrictions preserve the existing data floor and cost-model scope. Operator assessment reads family multipliers; this does not make historical quota allocation the current Candidate selection owner. Whole-package relocation would misclassify shared evidence dependencies. |
| Scheduler → `insight_synthesizer` → `memo_effectiveness` | Synthesis reads eligible outcomes, optionally calls the shared Model path and inserts a namespaced `trading_memo`. Prior-memo evaluation updates that knowledge item's verdict/cohort tags through `update_knowledge_item`; it is not a pure reader. These effects do not update realized PnL or promote an execution policy. |
| Scheduler / analytics calibration GET → `confidence_calibration`; synthesis job → `reasoning_feedback` | Builders persist namespaced knowledge reports through the canonical repository, with existing six-hour throttles. Calibration GET can invoke the builder when no report is available; reasoning-feedback GET reads the report. Thus HTTP GET is not evidence of physical read-only behavior for the calibration path. Cohort identity and incomplete-evidence handling remain shared rules. |
| Post-trade reviewer → `trade_case_builder` → `knowledge.manager` | Case studies and learned guard candidates use existing knowledge document and row writers. Only high-confidence case studies are eligible for best-effort FTS indexing; learned guard candidates are medium-confidence and skipped by the index writer. Learned candidates retain shadow-only/observe-only semantics; generation does not grant blocking or execution authority. Review tool/run telemetry remains a separate existing side effect. |
| Explicit guard API / scheduled TTL maintenance → `guard_promotion` | Explicit promotion persists knowledge rows and the promotion event in one repository transaction, with best-effort FTS indexing. After commit it attempts the retryable JSONL mirror and materializes the knowledge document; index or mirror failure does not roll back the canonical rows/event. Scheduled expiry touches learned guards only, updating files/rows and emitting deprecation events; seed/manual guards remain persistent. Neither path is correctly described as report-only, and learned shadow-only semantics are preserved. |
| Serenity opportunity repository → `causal_controls` | Namespaced `alpha_control_observations` inserts join an existing caller transaction and commit only when not already in one. This is an observation writer, not Candidate publication or Order/Position settlement. The row builder supports outcome seeding with exact venue/price evidence, but this Serenity caller passes no venue and therefore does not seed those outcomes. |
| Core Risk / Candidate producers → `filter_evidence`; scheduled controller → Lab | Filter facts use canonical repository writers; required Risk evidence joins the authoritative decision transaction. Controller recommendation persistence is only part of its effect: it builds bounded replays, registers experiments and invokes the existing Lab window runner. Main/worker ownership and cycle exclusion remain with the existing scheduler/Lab owners. Lab evaluation can revoke paper manifests through its canonical safety disposition; it is not a second Core settlement owner. |

The four namespaced report producers above now live together in
`extensions/analytics/`, along with their two exclusive API handlers. CLI/Web
composition binds their existing scheduler builders and Core scorecard readers;
runtime job bodies do not import the concrete report implementation. Without
that package, report cron jobs are not registered, startup catch-up marks the
memo unavailable without advancing its watermark, and calibration contributes
the existing fail-closed zero score. Explicit Ops remediation accepts the two
deterministic builders from its CLI composition and reports unavailable rebuilds
after canonical maintenance commits. Observer imports the optional memo owner;
Doctor reads persisted history without loading the producer. Algorithms,
six-hour throttles, logger identities, Knowledge writers, strict temporal rules,
cohort eligibility and economic meanings are unchanged. This closes this finite
report group, not all optional Analytics or Learning consumers.

The report-facing `temporal`, `attribution`, `streaks` and performance-summary
calculations do not themselves write trading state. The unused eager package
re-exports are removed: Core accounting can import shared Outcome rules without
loading those reports or lifecycle stories. Direct report consumers keep their
existing module APIs, and Candidate's strict temporal parser remains a required
shared dependency. This narrow import proof is not whole-package absence safety;
remaining read models and dynamic consumers need disposition before a package-wide move.

Behavioral evidence includes `test_candidate_funding`,
`test_candidate_selection_utility`, `test_outcome_integrity_read_model`,
`test_learning_analytics_fixes`, `test_confidence_calibration`,
`test_trade_case_builder`, `test_filter_shadow_experiments`,
`test_observation_lab_evaluator` and `test_write_guardrails`.
These fixtures do not prove deployed concurrency, runtime health or economics.

### Replay, lifecycle and research evidence disposition

These traces distinguish persisted-fact readers from evidence admission and
explicit artifact output. A report's lack of trading writes does not imply that
its entire dependency package is optional or that its database connection is
physically read-only.

| Observed flow | Owner, side effects and movement constraint |
| --- | --- |
| Inspect replay/diagnosis and closed-Position story → `position_replay` | Reads persisted Position, Proposal, Order/Fill, Risk, producer identity, protection and exact-instrument OHLCV facts. Standalone replay uses `readonly_db`; an injected connection's existing transaction is preserved. Cutoff masks and central Outcome assessment prevent later mutable facts or incomplete economics from becoming historical proof. No acquisition, settlement or history repair is performed. |
| Inspect Position/Opportunity stories → lifecycle-story builders | Join persisted ancestry, decisions, Risk, execution, protection and path evidence. Opportunity stories distinguish absent sources from empty descendants; nested Position stories share the caller's connection. Standalone stories use ordinary repository connections, not a physical read-only guarantee. Rendering and prefix hypothesis evaluation do not submit actions or call Model endpoints. Required shared Outcome/identity helpers must not move with optional report presentation. |
| Inspect profit-gradient → `profit_gradient` / `profit_gradient_estimators` | Pure supplied-evidence calculations retain distinct layer denominators, cohort identities, reconciliation and deterministic local bootstrap. The verdict explicitly denies automatic behavior-change authority. CLI accepts the current compiled artifact format and revalidates its sources before selection; a historical artifact's hash alone is not current CLI admission. |
| Inspect profit-gradient-evidence → `profit_gradient_evidence` | Compiler uses a physical read-only database snapshot and existing Candidate facts/Position replay readers. Registered epoch, exact producer/runtime identities, cutoff, cost-model provenance and explicit treatment/control pairing gate confirmatory samples. Exploratory counterfactuals remain excluded. The CLI then explicitly creates the requested output directory/file; artifact output is a side effect, not a Core authority writer. No registration or qualification window is created/reset. |
| Inspect profit-counterfactual → `profit_loop_counterfactual` | Reads existing registration epochs through ordinary `get_db` connections, validates their immutable identity/hash and evaluates a finite fixed-prefix registry against supplied costs and frozen Position facts. Pure `build_policy_epoch` hashing is used, not epoch persistence. Output remains exploratory and `promotion_eligible=False`; unavailable registration, costs or provenance do not become permission to evaluate/promote. |
| Alpha/report consumers → `learning.read_models` | First resolves experiment authority through Main's ordinary `get_db` connection; on a missing application database this can create the database and writer-lock/SQLite sidecars before returning unavailable. Only the subsequent projection of existing Lab windows, results, inference plans, exposures and job health uses the Lab read-only connection. Running-cohort comparison stays unavailable without evidence; projected measurement and lifecycle explicitly deny authority/mutation. Projection failure is unavailable, not an empty healthy Lab. This reader does not own Lab registration, evaluation or adoption; the whole call is not physically read-only. |
| Explicit module reports → `exit_attribution` / `exit_driver_comparison`; report consumers → `holding_time`, `performance_summary`, `streaks` | Closure distribution includes raw closed records, while exit-driver performance first loads centrally eligible strategy outcomes. These answer different questions and must not be merged merely because both group exits. Aggregation, holding-time availability and supplied-PnL metrics are pure; report loaders use existing repository readers. None is a close/settlement writer or evidence of causal policy improvement. |
| Filter controller → `filter_registry` | Immutable descriptors retain historical ID decode separately from the current control list. Retired entry stages are excluded from `FILTER_REGISTRY`; the registry itself neither writes config nor actuates recommendations. Controller/Lab side effects remain with the active writer disposition above, not with this descriptor table. Historical capability/reason strings are not Core business routing. |

Relevant bounded checks include `test_position_replay`, lifecycle-story tests,
`test_profit_gradient` and its property tests, evidence-compiler tests,
`test_profit_loop_counterfactual`, `test_learning_read_models`,
`test_exit_attribution`, `test_exit_driver_comparison`, `test_performance_summary`
and `test_streaks`. They do not prove deployed economics or whole optional-tree
absence; direct shared dependencies and composition remain separate obligations.

### Economics and research consumer disposition

This source audit distinguishes current inference provenance from optional
research and archive access. The package name is not an ownership boundary.

| Observed flow | Owner, side effects and movement constraint |
| --- | --- |
| `model_provider.base` → `economics.tracing` | Physical Model attempts append `runtime_cost_events`; `agent_runtime.run_logger` initializes tracing and reads run aggregates. Pricing snapshots have one catalog writer. These are current Core inference facts, not realized trading PnL or Legacy report generation. `agent_roles` only classifies attribution. |
| `agents.helpers`, startup provider probe and explicit model ping → `inference_calls` / `context_artifacts` | Logical calls, structured attempt outcomes and bounded prompt-free context metadata have dedicated ledger writers. Web startup reconciles interrupted logical calls. Explicit model ping also records success/empty-response/failure verification through `model_provider.router.persist_provider_verification`, which uses canonical `mutate_config` and invalidates the health cache after persistence. Learning entry-context reads the same persisted identities and costs; optional research does not own the shared provenance kernel. |
| `economics.schema` | Owns economics DDL, the `agent_tool_calls` cost-mirroring trigger and bounded backfill marker. First-time DDL refuses an active caller transaction. Active telemetry query helpers may still invoke initialization, so they are not claimed physically inert; archive viability reads are separately confined above. No Order/Position/Outcome authority is created here. |
| `economics.pricing_sync` → composed `extensions.data.public_pricing` fetcher | Only public catalog HTTP acquisition belongs to Data. CLI/Web bind the optional fetcher; absent Data reports `pricing_catalog_unavailable` while verified cache/history remain usable. Core retains parsed-byte equality, minimum-size and shrink validation, cache trust/publication, immutable event-time resolution, and the one bounded refresh worker. Web startup, scheduler, explicit API refresh and unmatched-model resolution trigger that same worker with the existing per-catalog negative cache. API/worker audit events and the single pricing snapshot writer remain unchanged. Whole-module movement would invert Core accounting dependencies; the public LiteLLM catalog is not an embedded LiteLLM transport SDK. |
| `economics.budgets` / `usage_insights` | Explicit audit CLI commands declare/enable/remove budget policies. Shared Model fallback computes would-block metadata and owns conditional admission in `_enforce_budget_if_configured`: enabled economics, an exceeded observed budget and a non-`warn_only` mode can raise `BudgetExceededError` before inference. Safety callers `pi_trading_decision`, `risk_critic` and `agent_shell/trading_decision` remain exempt. Usage v2 projects logical/physical ledgers through `readonly_db`, including missing-evidence states, without persisting its display calculations. Neither is a second trading settlement owner. |
| `economics.context_effectiveness` | Explicit CLI paired evaluations call the shared Model path and write blinded packets outside the repository; score commands persist aggregate owner reviews. This is optional research, not automatic effectiveness/policy promotion. Its inference ledger dependencies remain shared Core provenance. |
| `extensions.research.research_worker` | Optional Serenity producer writes namespaced runs/evidence/claims/themes/opportunities and invokes the Learning information-policy epoch helper. CAS claims and guarded terminal transitions preserve cancellation truth. The research lock is a fixed, non-renewed 1,800-second lease: an expired lease can be acquired while the prior thread is still running, so it does not prove whole-pipeline mutual exclusion. Manual and automatic retries share durable retry identity; automatic retry uses the existing scheduler, not another scheduler instance. |
| Serenity publication | Manual API promotion and worker auto-promotion both call `trading.serenity_publication`; exact source/PA preparation precedes a short transaction that revalidates evidence and calls canonical `publish_candidate_snapshot` / current Generation owners. Research does not create a second Candidate writer, execute a Proposal, or bypass Risk/Gateway. |

Serenity worker threads are daemon-owned, with cancellation checkpoints between
stages/lenses/provider calls. The existing scheduler reconciles prior-process
attempts at startup; Core lifespan does not individually join these threads.
The [Serenity Contract](../../design/contracts/serenity_contract.md#51-research-run-lifecycle-truth)
preserves cooperative cancellation, not physical thread termination. This
inventory does not claim whole-subsystem shutdown or optional-absence proof.

Behavioral evidence includes `test_economics_cli_crud`, `test_economics_routes`,
`test_economics_budgets`, `test_economics_budget_contract`, `test_usage_insights_v2`,
`test_pricing_sync`, `test_serenity_workbench`,
`test_serenity_scheduler_continuity` and `test_serenity_p0_p3_e2e`.
Fixture results establish their named behaviors, not live model availability,
deployed lifecycle health, or economic qualification.

### Data consumer and side-effect disposition

The following source observations distinguish shared facts from optional
acquisition/research. They supplement the package census, not authorize moving
the whole `data` package or claim whole-subsystem absence safety.

| Observed flow | Ownership evidence and movement constraint |
| --- | --- |
| `news_collector` / `news_analyzer` | Optional RSS acquisition and Model-backed analysis write namespaced `news_items`; both also invoke the existing Learning information-policy epoch helper. Scheduler jobs retain their existing gates and failure accounting. These are not pure readers, and moving them must preserve those side effects without creating another scheduler. The manual news route uses the same analyzer. |
| `news_entities.news_matches_symbol` | Shared pure fact matcher: Core `repositories.batch_news` revalidates stored article text with it; research and acquisition also consume it. It cannot simply follow optional RSS acquisition out of Core. |
| `polymarket_provider` / `yfinance_provider` | Optional public HTTP/executor acquisition, without their own persistence or trading authority. Prediction observer owns namespaced run/snapshot persistence; prediction routes also consume history acquisition. Market context catches unavailable yFinance enrichment. These specific consumers do not prove every optional route is absent-safe. |
| `macro_liquidity` | Mixed module: Core `repositories.insert_fred_source_batch` consumes its constants and immutable-batch validation, while the Macro Extension reads prior source batches for cadence/provenance and CLI research derives as-of liquidity. Preserve the persisted validation contract; whole-module relocation would invert a Core dependency. No credential resolution occurs in this module. |
| `positioning_evidence` | Read-only as-of research over canonical derivatives facts, consumed by CLI. Comparisons retain exact venue/instrument/unit provenance and compatible grouping; this is not execution or settlement authority. |
| `position_x` / `collectors.vergex_structure` / `perp_structure_shadow` | Offline material/aggregate production and explicit CLI ingestion write namespaced structure snapshots and shadow outcomes, with the existing Learning epoch/audit effects. Shadow settlement reuses market observations and is called non-fatally by the existing outcome recorder; it is not realized Position settlement or a second scheduler. The stored advisory projection belongs to Core `context_builder` as described above. |
| `market_structure` / `universe.market_structure` / `smc_enrichment` | Shared causal geometry is consumed by Entry analysis and repository requalification. The Universe projection serves the production momentum trigger and reuses that kernel; it is not a duplicate geometry algorithm. The provider wrapper reads an injected Data provider; optional FVG enrichment does not grant execution authority. Do not delete or move the shared kernel based on the wrapper's caller count. |
| `data_coverage` | Operator read model over persisted facts and current collector targets, with a bounded in-memory refresh cache. It does not acquire market data or own a Core lifecycle writer. |

Relevant behavioral evidence is in `test_news_integrity`,
`test_positioning_evidence`, `test_macro_liquidity`, `test_market_structure`,
`test_market_data_coverage`, `spec_market_structure`, `test_market_fact_boundary`,
and the Position X absence/settlement tests. Source traces establish the named
consumers and side effects; these tests do not establish deployed health,
economic qualification, or complete dynamic-import absence coverage.

Full Scheme A remains pending: Core versus optional roles inside mixed
analytics, CLI/API, Data, Agent Shell and Universe still need complete
consumer/side-effect disposition. The table must not be used as a package-wide
move list. Named-writer/literal-SQL guards cannot prove arbitrary dynamic SQL,
runtime call cardinality, deployed holder identity or economic qualification.

### Candidate and Position read-model disposition

These source traces distinguish evidence projection from the producers and
authority owners whose persisted rows it reads.

| Observed flow | Owner, side effects and movement constraint |
| --- | --- |
| `inspect candidate-facts` / candidate diagnosis / profit-gradient evidence → `candidate_facts` | Fixed-horizon first-exposure projection over filter facts and their recorded ancestry. Its standalone builder opens `readonly_db`; an injected connection is borrowed, with `BEGIN DEFERRED` / `ROLLBACK` only when no caller transaction exists. It does not publish Candidates or settle outcomes. Historical Opportunity/PI terminal labels do not define current Planning state. |
| `control status` coverage → `candidate_coverage_counts` | Uses the caller's read-only connection and window-bounded identity set, streaming historical batch payloads to disprove earlier exposure. Registering the timestamp SQL function changes connection-local behavior, not durable rows. This is a current operator consumer, so the module is not wholly disposable Legacy. |
| Candidate / Position diagnosis CLI → `profit_loop_diagnosis` | Derives metrics and classifications from supplied facts; does not open a database, run a policy or persist its diagnoses. Candidate fixed-horizon assessment and Position hypothesis-prefix evaluation reuse shared evidence rules. Directional comparisons are not realized settlement or policy authorization. |
| Report builders / post-trade reviewer / hypothesis research → `trade_attribution` | Joins exact Proposal, Order, source-specific Opportunity or EntryPlan/Candidate ancestry and shared outcome eligibility. Reads historical PI context without creating synthetic Opportunity ancestry for current Universe entries. Report rows also feed case-study and learned guard-candidate document/row writers through the post-trade reviewer; candidates remain shadow-only, not blocking authority. The row builder itself does not publish those documents. |
| Trade-path API / exit-policy report / geometry / Position replay → `trade_path` | Reads exact-instrument candles and persisted protection evidence, deriving excursion bounds and separating post-exit observations from holding-period claims. It does not fetch market data, create protection triggers or close Positions. Replay can supply its connection and explicit as-of cutoff; other callers use repository readers. |
| Opportunity entry-exit audit / trade-insight R distribution → `entry_exit_geometry` | Verifies stored Proposal/Fill/plan/protection evidence and frozen historical cost arithmetic. The standalone report uses `readonly_db`. At this trace's base (`3560dfacd`), trade-insight helpers pass a read connection only when opening succeeds; opening failure falls back to ordinary `repo.get_db`, so this caller is not proven physically read-only. Historical paper-cost reconstruction grants no simulator, settlement or current cost-policy authority. |
| `inspect factor-feedback` → `factor_feedback` | Retrospective grouping of eligible attribution rows. Labels such as `hard_blocked` describe the report's classification, not a current Candidate filter writer. No caller of this builder in the traced production flow applies those labels to admission. |
| Family scorecard API / CLI / trade statistics → `family_scorecard` | Aggregates eligible Position outcomes and deduplicated historical shadows. Shadow epoch detection and deduplication are reads, not the similarly located epoch-creation path. The Data API wrapper calls its existing database initializer before the report; this route is not proven physically read-only merely because aggregation uses SELECTs. |

Query-only intent is not proof of connection permissions: attribution, trade-path
and family report branches can use ordinary repository openers when no read
connection is supplied. This inventory does not claim package-wide physical
read-only enforcement or optional-Extension absence safety. The read models do
not replace Core accounting or the namespaced writers that consume their output.
Behavioral evidence includes `test_candidate_facts`, `test_candidate_coverage_counts`,
`test_profit_loop_diagnosis`, `test_trade_attribution`, `test_trade_path`,
`test_factor_feedback` and `test_exit_policy_advisor`; it is not runtime or
economic qualification evidence.

### Historical report and diagnostic consumers

The following disposition separates read projections from current producers;
neither a historical name nor a `read_only` output flag proves inertness.

| Observed flow | Owner, side effects and movement constraint |
| --- | --- |
| `opportunity audit --view ranker-objective` → `ranker_objective_audit` | Uses `readonly_db` plus a temporary query-only guard to analyze persisted shortlist membership and fixed-horizon outcomes. Reads batch boundaries, exact instrument/time linkage and historical admission facts; never invokes the retired Ranker, loads its policy or promotes a result. Missing intended membership remains unavailable; descriptive historical metrics do not grant current admission authority. |
| `inspect position-path` / `stop-loss-path` → `position_path_observations` | Reads append-only observations and existing Position economics; shares the observer's pure expected-bucket calculation, not its collection loop. Stop-loss denominators and exclusions depend on central `assess_reportable_outcome`. Derives coverage, excursions and attribution without acquiring candles, settling Positions or writing observations. Ordinary repository openers mean physical read-only permissions are not established by this trace. |
| Trade-statistics / strategy-KPI API and `inspect orders` / `status` / `pnl` CLI → `trade_stats` | Aggregates persisted Order lifecycle facts and centrally eligible Position outcomes, with family-scorecard reads. Status consumes both rolling-trade and Order statistics; PnL consumes rolling-trade statistics. These consumers do not submit Orders or materialize final PnL. Order fill-rate summaries and realized Outcome cohorts remain different populations. Default-mode resolution is a separate configuration dependency: its implicit migration persistence was repaired in #3364 using the existing read-only config context. Ordinary repository connections still preclude a blanket physically read-only claim. |
| Trade-insight API → `trade_insights` | Other chart helpers project the loaded Outcome cohort; R distribution additionally reads Position, Proposal, frozen exit-plan, protection-state, Order and Fill rows through `readonly_db` and the hard `entry_exit_geometry` module dependency. Database acquisition and per-row evidence failures report unavailable R evidence without an ordinary-opener fallback; a missing geometry module still propagates. Default-mode configuration resolution shares the #3364 repair above. This narrow guarantee does not cover every cohort reader or the whole Analytics package. |
| Doctor / sizing CLI / Opportunity flow → `sizing_trace` | Separates persisted proposal/Fill/Position notional from a legacy-formula counterfactual using current config values. Independently reconstructs base-notional/sample-count/Kelly/ATR scaling, reusing `compute_position_size`; it is not the authoritative `available_balance_risk_budget_size` path described in [Risk Contract §5](../../design/contracts/risk_engine_contract.md#5-仓位计算当前权威路径). Risk policy reads do not submit a trade or change a Risk decision. Config migration persistence was disabled in #3363; its database opener is still ordinary, so the full diagnostic is not proven physically read-only. Historical-data gaps must not be repaired by inventing eligible Outcome counts. |

Sizing also depends on persisted `pi_decisions` conviction, `risk_decisions`
cap evidence, `orders` for Fill linkage, and `universe_feature_snapshots` joined
to `universe_registry` for ATR. These evidence owners remain required even when
the final sample-dependent projection is unavailable.

Focused evidence includes `test_ranker_objective_audit`,
`test_ranker_objective_cli`, `test_stop_loss_path_attribution`,
`test_trade_insights` and `test_trade_insights_m1`. The sizing trace's historical
schema regression is recorded separately in its corrective PR; this inventory
does not claim its entire suite is green or that all diagnostic import and
configuration side effects are closed. Package movement must preserve the shared
read rules while keeping collection, configuration migration and trading writers
with their actual owners.

## Canonical mutation seams

These are observed API owners and callers, not a blanket claim that each
logical authority already has exactly one producer. Repository field guards
are narrower than generic SQL but do not alone prove application ownership.

| Authority | Current persistence boundary and actual caller families | Remaining distinction |
| --- | --- | --- |
| Candidate creation | `repositories.publish_candidate_snapshot`; Universe refresh, Serenity publication and isolated preview seed call it | Shared bounded writer checks confine named publication to these callers and generic snapshot/member insertion to the unique top-level publisher, counting direct/attribute and positional/keyword insertion sites. The publisher cannot use other generic mutations or raw Candidate DML. Exact TTL/expiry SQL and complete retention SQL expression ASTs are separately allowed only in their canonical repository functions; static concatenation is scanned as a whole and suffixes cannot reuse a permitted fragment. Candidate DML is forbidden in migrations; schema DDL is limited to migration-owned table creation and column addition. Generic writer imports/re-exports are rejected outside repositories, including unchanged names. Commented/quoted SQL, nested owners and writes across Core/Extension/Legacy/Ops/script roots are covered; package/relative Serenity imports into repositories are rejected. Arbitrary dynamic SQL, runtime retention placeholders/parameters and runtime call cardinality remain outside static proof; attention and retention semantics are unchanged. |
| Derivative source time | Read-only `data.derivatives_facts.provider_source_time` | Funding/OI/price/metadata normalization and Universe feature source-time reads no longer import the optional derivatives collector. Collector calls the same facts parser, with no old re-export. The parser is AST-identical to its prior implementation: source timestamp precedence, missing/invalid/provided/future states, UTC conversion and the existing skew threshold are unchanged. Fresh-process tests deny the collector import while exercising all four normalization entries and the feature-time consumer; native acquisition and remaining metadata ownership are separate unfinished boundaries. |
| Taker observation | Data provider owns native 1h endpoint calls/ratio decoding; Core Features owns the mover trigger and missing-value behavior | Features binds the optional reader from the existing per-scan perpetual provider, with no additional client or factory. Native endpoint names and response parsing leave Core. The strict absolute 1h-return threshold, spot exclusion, 5-second native request timeout, adapter-id coverage and zero/unknown distinctions remain unchanged. Other native feature acquisition remains a separate unfinished boundary. |
| Funding/OI feature observations | Existing CCXT Data provider owns exact-venue endpoint calls and normalized source-timestamped observations; Core Features owns freshness admission, stored fallback, Candidate proof version and feature snapshot persistence | Funding uses the existing Bitget ticker funding fields without a second per-symbol request and preserves the configured contract interval provenance. Other venues retain the existing 5-second funding request; OI retains proved USD-unit metadata and 5-second request semantics. Missing, stale, invalid or errored observations leave the fresh stored row unchanged. No provider fallback, persistence or trading authority was added; remaining native feature reads are separate unfinished boundaries. |
| Orderbook feature observations | Existing CCXT Data provider owns exact-venue market validation, native orderbook acquisition and microstructure normalization; Core Features owns fresh-row reuse, diagnostics and the sole `insert_orderbook_snapshot` write | Spot/perp identity, contract-size depth, five-second timeout, parser rejection and cancellation behavior remain unchanged. The normalized observation is non-persistent and no provider fallback is used; Core rejects mismatched symbol/venue/market rows before its existing writer. Other orderbook collector/planner consumers and remaining native feature reads remain separate boundaries. |
| Feature ticker observations | Existing CCXT Data provider owns primary-venue batch ticker acquisition, chunking and canonical spot/perp symbol mapping; Core Features retains ticker availability, volume prefilter and feature semantics | Existing primary-only behavior, ASCII/loaded-market filtering, 100-symbol chunks, per-chunk failure handling and Bitget ticker payload reuse remain unchanged. The observation is non-persistent and never uses configured cross-venue fallback; OHLCV acquisition remains a separate boundary. |
| Feature OHLCV observations | Existing CCXT Data provider owns exact-venue OHLCV acquisition and validated `OHLCVResult`; Core Features retains closed-bar filtering, technical calculations, enrichment and exact-candle persistence | Feature scans use the existing observation capability with cache disabled; that capability performs one primary-venue attempt without configured cross-venue fallback. Candle provider/exchange identity is required before the unchanged Core row shape is consumed; BTC baseline uses the same reader. Feature construction binds through the existing provider port. No provider persistence, cross-venue recovery or technical/Risk policy change. |
| Candidate information identity | Core `analytics.filter_evidence.POLICY_EPOCH_LABEL` | Universe and evidence consumers read the unchanged shared label without importing Learning. Optional Learning retains its existing durable epoch writer. The uncalled Universe `_active_paper_learning_identity` legacy-ledger helper and its isolated tests are deleted; canonical Lab authority is unchanged. |
| Candidate publication veto | Current semantic/config identity and the Core publication transaction | Removed the old `learning_experiments` overlap veto and unused reverse helper. The current Utility contract already retires calibration as a runtime dependency. Old Paper rows or unavailable legacy ledger cannot block Candidate/Planning publication; exact current identity, physical eligibility and atomic publication guards remain required. |
| Persona sizing evidence | Core `sizing_policy.resolve_learned_notional` owns the static notional/trace boundary; optional `learning.sizing_evidence` owns the existing treatment adapter | Web/CLI bind lazily. Missing evidence cannot interrupt Core sizing; unsupported Persona evidence cannot grant actual size authority. This does not classify remaining Persona reward/rollout dependencies as converged. |
| Persona Learning safety queries | Core `trust.learning_safety` consumes explicitly bound read-only gate/refreeze queries; optional `learning.control` retains the unchanged rules | Persona context, sizing and rollout no longer import concrete Learning control. Web/CLI bind without executing queries; missing/error/malformed evidence denies influence and reports unavailable freeze state. Durable incident, pause/config gates, rollout persistence and distress policy are unchanged. Reward calculation and the remaining optional Learning inventory are separate from this query boundary. |
| Candidate-filter path declaration | Core `trust.behavior_authority` owns source/path/prospective checks; optional `learning.registry` supplies only a declared-capability boolean | Web/CLI bind without querying. Missing/error/non-boolean evidence cannot authorize learned influence; static seed/manual behavior remains available without Learning. Registry membership is not prospective admission, measurement support or trading authority. |
| Regime context | Optional `extensions.regime.detector` owns detection/cache and returns the existing normalized `MarketRegimeRecord`; Universe retains record persistence and one frozen Candidate context | Web/CLI bind refresh and published-evidence readers without running them. Risk retains finite-number validation, thresholds, unpublished-versus-read-failed semantics and failure tokens. The reader preserves fresh-cache preference and one repository fallback without a fabricated sideways default. Missing Regime does not stop Candidate publication; strict Risk reads remain unavailable. Persistence failure retains the already-detected context as before. No second scheduler, writer, cache or DTO was introduced. |
| Scheduled learned-guard maintenance | Existing `_deprecate_expired_learned_guards` TTL owner; unchanged persisted `guard_promotion` job identity/gates | Deleted `run_promotion_check`, its private promotion wrapper and loss-rate status writer. Historical auto-promote/min-match config is decode/read-only: Settings rejects both request fields before mutation, and doctor offers no retired-toggle action. Explicit manual promotion, other current guard settings, namespaced document/DB/audit synchronization and TTL semantics remain. |
| Generation / EntryPlan | Repository generation claim/bind/defer/complete APIs; `transition_entry_plan`, trigger binding and monitoring | `test_planning_writer_boundary.py` pins named creation/claim/bind/completion/transition owners and table-specific literal/generic writes to the existing Repository functions or migrations, reusing the Order scanner. Planner regressions cover claim loss, stale results and atomic completion. This is bounded static evidence; arbitrary dynamic SQL/callback targets and multi-leg aggregation still need separate proof. |
| Proposal | Repository hash-chain insertion and status-only update; native per-leg/Gateway intent and explicit external-position adoption are distinct insertion callers | Shared writer checks confine named insertion to Entry-plan execution, Gateway and position reconciler; status updates to Risk, Gateway, exchange materialization and the guarded owner-rejection route. Generic/literal table writes stay in the canonical repository functions or migrations, DDL in migrations. The update API rejects immutable fields before any write; status changes retain the stored content hash. Research-bundle import rejects Proposal rows. Arbitrary dynamic SQL/callbacks, status-transition policy and full runtime proof remain separate. |
| RiskDecision | `risk.engine._persist_risk_result` calls ordinary or authoritative insert; authoritative repository insertion owns deduplication | Preflight and final exact-order decisions are different facts, not interchangeable duplicate implementations. Pre-change audit base: `7281e1e660f8bae8976f096c4e8e56a1d62df1ae`; the current shared checker replaces that base's weaker Risk-only scanner. Named writer imports/calls stay in Risk Engine with one direct canonical authoritative-to-ordinary insertion call site (zero/two sites fail); literal/generic table mutations, including `_delete_by_rowid_batches`, stay in the unique top-level `insert_risk_decision` body or migrations, and CREATE/ALTER/DROP authority-table DDL stays in migrations. Regression cases cover SQL comments, UPDATE OR, helper aliases, nested same-name writers, decorators and renamed module-level exports across Core/Extension/Legacy/Ops/script roots. No production writer or Risk policy changes. Arbitrary dynamic SQL/call targets and runtime call cardinality remain outside this bounded static proof. |
| Order | `insert_order`, `transition_order`, `update_order_metadata`, `replace_order_error_reason`, acknowledgement finalization | Creation is partitioned by fact: Coordinator owns submission intent, exchange materialization owns observed closes including native TP/SL claims, and position reconciler owns explicitly adopted external exposure. Scheduler delegates observed claims and no longer creates Orders or claims Position close state. These distinct facts must not be collapsed into a new submission path. Named writers are mechanically confined to the observed Core owners, with only acknowledgement-to-transition repository delegation. Literal/quoted SQL and generic literal-table writes stay in the four canonical storage functions or migrations; new repository wrappers and direct/imported generic aliases are tested. Arbitrary dynamic SQL and dynamically constructed call targets remain outside this lexical proof. |
| Fill | `insert_fill` called by exchange materialization and observed administrative close evidence | Private Fill normalization versus administrative synthetic evidence must not be conflated. Pre-change audit base: `3fd9d2cd2346949ce1bff0264ffee6b9c3e19fef`; the current shared checker confines named inserts to `execution.exchange_materialization`, raw insertion to the unique repository `insert_fill` body or migrations, and DDL to migrations. The sole non-repository literal-write exception is the exact `UPDATE fills SET fee_source=? WHERE id=?` in the unique top-level `_verify_durable_fills` body, which already validates same-id private identity/economics/time before provenance upgrade. Other functions, changed columns, nested functions/lambdas/decorators, generic deletion, helper aliases and quoted/commented SQL cannot reuse this exception. The old whole-file SQL permission is removed; no production logic or persisted facts changed. Dynamic SQL/call targets remain outside this bounded proof. |
| Position entry | `insert_position`: native private-fill materialization or explicit external-position adoption | These represent distinct provenance. The named-writer guard confines insertion to these two current Core callers; direct and imported-alias Extension calls are rejected. Generic compatible-position coalescing needs exact Plan/account audit before changing it. |
| Position field groups | Repository materialization / exit-plan / observation / closed-settlement field allowlists and status CAS | Named materialization, exit-plan, observation and close-claim access is confined to the current Core caller inventory. Raw historical-repair exceptions do not grant named runtime-writer access. Existing field/CAS and settlement-specific guards remain unchanged. This is bounded named-reference proof, not arbitrary dynamic SQL/call-target or runtime cardinality proof. |
| Close lifecycle | `finalize_position_close`: `pnl._commit_close_economics` or `outcome_closure.close_position_administrative` | Administrative lifecycle closure must remain distinct from cost-complete strategy Outcome admission. |
| Final PnL / eligibility | Shared `pnl._commit_close_economics` and `analytics.outcome_integrity`; delayed cost completion calls the same commit operation | The eight named settlement calls are classified below. Attribution retry may update only eligibility/reason; expired funding may update only its exclusion/evidence fields; exit explanation may update only reasoning. These operations cannot directly write final PnL. Owner-approved same-Plan per-leg ancestry remains unchanged; no historical backfill or runtime qualification is claimed. |
| Outcome feedback delivery | `portfolio.outcome_hooks` owns enrollment, eligibility reread, job leases/retry/completion and invalidation transaction fence | Existing Web/CLI composition binds a synchronous optional executor. `learning.outcome_feedback` owns Serenity post-mortem/opportunity feedback and delegates shadow feedback to `counterfactual.trades`; Core PnL no longer implements these optional writes. Absent feedback leaves committed close/economics unchanged and delivery durably failed/retryable, never falsely completed. |
| Risk lesson evidence | Core `repositories.list_lessons` reads persisted rules; Risk Engine interprets matching/static/category/expiry/dry-run facts and owns every verdict | Optional `learning.risk_evidence` supplies only the existing exact treatment result via Web/CLI binding. Missing treatment is fail-closed when needed; manual/owner/human constraints remain readable without Learning imports. No rule authoring, experiment admission, epoch, or Risk policy changes. |
| Demo-entry quality epoch | Core `risk.demo_entry_quality` keeps quality predicates and unavailable tokens; optional Learning retains epoch persistence | Existing Web/CLI bootstrap binds the writer without invoking it. The original check-time call, epoch payload/hash/history and failure rejection remain unchanged; Core no longer imports the concrete Learning implementation. |
| Entry Learning context | Core `trading.entry_learning` owns the typed evidence/empty-context contract; `entry_analysis` retains canonical planning composition and treatment identity binding | Optional `learning.entry_context` retains its read-only historical projection and canonical Lab treatment resolver, composed at existing Web/CLI bootstrap. Core imports no concrete producer; repository lineage decoding uses the same Core cutoff decoder. Missing producer yields explicit unavailable context; schema, epoch/metric identifiers and configured treatment flow are preserved. |
| Shared Outcome assessment | Core `analytics.outcome_semantics` owns Candidate temporal/cost eligibility and the clean-window realized assessment wrapper | All former `learning.outcomes` callers use the existing Core module; the old file and duplicate strict numeric parser are deleted. The functions remain read-only and AST-identical; Candidate fixed-horizon evidence is not Position realized PnL, and the existing as-of/data-floor policies remain unchanged. |
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
