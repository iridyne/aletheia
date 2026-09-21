# Slim Core production ownership inventory

Current package census and mixed-owner corrections: source
`851aed04171b3943b1b11039a747ac930e663976` plus the Data cutover below, under
[#3134](https://github.com/iridyne/aletheia/issues/3134). Original inventory
baseline: `29eb0b0ce485e9d90c49c318fbc32b1b3a32a433`. The dated convergence
sections below retain their original proof scope; their historical next-step
wording is not the current work queue. This inventory is not a declaration that
compartmentalization or all single writers are proven. No runtime/data migration
follows from classification here.

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

The current tracked source contains 528 Python files under `aletheia`: 33
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

“Mixed” means moving the entire directory would misclassify current authority.
Names are existing package paths, not proposed new abstractions. The map covers
all first-level Python packages; detailed per-consumer independence remains an
open gate where stated.

| Current package (Python files) | Authority / side-effect surface | Target disposition / open seam |
| --- | --- | --- |
| Root modules (12) | CLI/bootstrap, paths, logging, networking, packaged process and runtime-home migration | Thin composition / Core utility / Ops; split by role, not a new application tier. |
| `agent_runtime` (22) | Scheduler, job queue, runtime session, pause/resume, connectivity and daemon lifecycle | Core lifecycle schedules both required work and optional producers; optional producer implementations must not take over that lifecycle. |
| `agent_shell` (23) | Entry Planner plus operator tools, permissions, context and recommendations | Mixed Core planning and optional operator capability; tools must retain Risk/Gateway boundaries. |
| `agents` (14) | Shared registry/inference/telemetry, portfolio snapshot adapter, and observer/research/learning producers | Mixed, not wholesale optional enrichment. Registry and helpers serve Core runtime/planning/storage; Portfolio Agent delegates the canonical snapshot writer. Producer-specific reads, research writes and lifecycle reachability must be classified separately. |
| `analytics` (60) | Shared realized Outcome eligibility plus attribution, read models and counterfactual measurements | Mixed Core eligibility and optional analytical writers; preserve separate economic meanings. |
| `backup` (4) | Artifact creation, verification and explicit restore | Ops; whole-DB restoration is not ordinary table import. |
| `cli` (33) | Operator commands, diagnostics and explicit maintenance entrypoints | Mixed minimum Core controls and optional/Ops commands. |
| `config` (9) | Typed schema, locked atomic mutation, redaction, secret storage, and the execution-identity environment/venue registry | Core configuration/storage primitives; concrete Exchange/Model credential resolution and ingress are in their Extensions. Secret-store ownership does not itself authorize a consumer to read raw values; other operator/optional credential callers still need scoped classification. Generic Core callers consume the identity registry. |
| `counterfactual` (8) | Replay, advisory, calibration and experiment measurements | Optional research state, never authoritative realized trading economics. |
| `data` (29) | Public venue/network collectors, caches and market facts | Mixed market boundary and optional enrichment. Listing consumers and chart/technical-context/Outcome/Position-observation/registry provider creation use the existing `data.providers` contract, bound by CLI/Web/collector composition; `extensions.data.ccxt_provider` owns concrete fresh-provider policy and listing I/O. Exact-venue reads retain pinned native-provider selection without a Core CCXT flag; ordinary configured fallback remains unchanged. Position observation requires the documented optional no-history/no-venue-health-write capability. Registry consumes normalized `MarketInstrument` facts; Data owns raw market metadata/precision decoding and supplies normalized contract-size metadata for feature volume conversion, while Core retains demo whitelist, exclusions, product support, blacklists and atomic registry writes. Missing/error/wrong-identity facts cannot write registry. The same provider now owns normalized funding/OI and orderbook feature observations; Core retains freshness/reuse, diagnostics, Candidate proof stamping, feature snapshot and orderbook repository writes. Universe feature construction uses only the provider port. |
| `diagnostics` (3) | Read-only reports/config checks and explicit Doctor infrastructure repair | Core operator reads / Ops repair; ordinary probe does not authorize fix. |
| `economics` (11) | Logical inference calls, usage/budgets, pricing references and context measurements | Mixed Core inference provenance and optional reference/analysis work. |
| `execution` (19) | Gateway/Coordinator, claims, reconciliation, materialization and broker helpers | Mixed Core semantics and concrete normalization; keep one Risk-gated command path. |
| `files` (5) | JSONL/audit, Markdown, paths and Git filesystem operations | Core audit primitives / Ops or optional artifact work. |
| `gateway` (22) | Frontdoor, process/PID/service/network control, explicit hook cleanup | Core operator plane / Ops composition. |
| `infra` (4) | Events and bounded error projections | Core observability; event publication must not grant trading authority. |
| `knowledge` (10) | Retrieval, guards, seeds, maintenance and recovery | Optional knowledge; learned safety influence remains separately gated. |
| `learning` (34) | Cohorts, evidence windows, experiments, hypotheses and guarded consumers | Optional; shared Core Outcome eligibility and job classification remain outside this owner. |
| `maintenance` (9) | Offline retention, accounting and fixed-target recovery | Explicit Ops/historical repair; not ordinary Core runtime writers. |
| `model_provider` (8) | Model Port/DTOs, endpoint routing, inference broker, guardrails, explicit fallback and pure protocol metadata | Core inference semantics. Concrete official-SDK transport and scoped credential ingress are in `extensions/model_endpoints`; a protocol label in Core metadata is not a second transport owner. |
| `observation_lab` (19) | Lab DB, windows, runner/checkpoints and ownership checks | Optional namespaced Observation; owner import absence is fail-closed and covered by startup/reconciliation tests. |
| `policies` (3) | `PolicyStore` writes immutable `ps_policy_versions`; lesson reviewer is a current consumer | Optional policy history, not equivalent to retired PS execution tables; do not delete by `ps_` name. |
| `portfolio` (17) | Position/close/funding reconciliation, PnL and Outcome hooks | Core lifecycle/economics with concrete account seams still to isolate. |
| `risk` (5) | Canonical evaluation, policy/kill switch and quality gates | Core safety authority; independently persists preflight/authoritative decisions. |
| `server` (29) | FastAPI lifespan, middleware and route composition | Mixed minimum Core API and optional route surfaces. Telegram routes now belong to the optional Telegram package. |
| `storage` (12) | SQLite/transactions, models, repositories, migrations, event/artifact stores | Mixed canonical operational persistence and namespaced optional schemas; table use must determine ownership. |
| `sync` (4) | Bundle export/import and conflict handling | Import is restricted to six namespaced research tables; Core Proposal and retired PS runtime rows are rejected before mutation. Artifact-category and whole-payload preflight remain separate restore/atomicity boundaries. |
| `telegram` (3) | Configured notification/polling endpoint and optional routes | Optional external adapter; no independent execution authority. |
| `trading` (35) | Typed Entry contracts/compiler/capture/activation plus source publication | Mixed Core Entry semantics and Serenity source orchestration; Candidate creation uses Core publication. Typed Entry Learning context belongs to Core, its optional evidence producer does not. |
| `trust` (4) | Behavior authority, Learning safety and Live unlock | Core safety boundary. Unchanged autonomous-job classification is available without Learning; unavailable control cannot authorize those jobs. No authority expansion in #3134. |
| `universe` (37) | Candidate selection/publication, attention and market/scanner consumers | Core Candidate semantics mixed with optional source/scanner implementations. |
| `update` (11) | Deployment identity/continuity, bootstrap, smoke and source/package update | Ops, explicit deployment authority; never inferred from green tests. |
| `uta` (8) | Existing normalized contract/models/errors, funding facts, account manager and loop-owned registry | Core Exchange Port/lifecycle. `compat.py` normalizes local rows into typed query scopes; it is not a forwarding alias for a retired adapter. Concrete CCXT, venue profiles and credential composition have moved to Extensions. |
| `utils` (2) | Datetime helper | Core utility; no new wrapper needed. |
| `extensions` root (1) | Package initializer | No runtime authority. |
| `extensions/data` (20) | CCXT public Data, fallback chain, public sources, nine context/native collectors, IV derivation and news acquisition/analysis/API | Existing normalized Data provider and request-local acquisition; no Exchange mutation. Macro/FRED and Coinalyze retain the approved own-reference credential exception. Core caches/facts and repository owners remain unchanged. |
| `extensions/exchanges` (18) | Generic CCXT adapter, profiles, Bitget native reads/protection, scoped auth and composition/credential ingress | Concrete Exchange boundary; normal orders remain Core Risk/Gateway/UTA commands. Retired alternate native trading clients are not retained. |
| `extensions/model_endpoints` (6) | Official HTTP SDK transports and scoped endpoint credential/probe composition | Concrete Model boundary; Core keeps selection, task tiers, inference identity and explicit fallback. |
| `extensions/regime` (3) | Detection, collection and cache | Optional market context; Core Universe retains persistence/publication and Risk retains verdicts. |

### Current mixed-owner evidence and remaining gates

The census covers every tracked first-level Python package, not every dynamic
runtime path. Current source traces establish the following distinctions:

- `agents.__init__` is the existing registry used by Core `orchestrator` and
  `AgentJobManager.enqueue`; the latter validates the function before persisting
  a queued run. `agents.helpers` supplies Entry Planner inference and
  `run_logger`/repository telemetry flushing. Moving these with Research would
  invert a Core dependency.
- `agents.portfolio_agent` delegates `portfolio.pnl.compute_and_store_snapshot`;
  it does not introduce another accounting algorithm or writer.
- `agents.research_worker` owns namespaced Serenity runs/evidence. It claims a
  queued run and acquires/releases the existing research lock; its bounded
  retry registers on the existing Core scheduler, not another APScheduler.
  Candidate publication delegates to `trading.serenity_publication`. Full
  pipeline/caller and shutdown proof remain open; a thread alone does not prove
  a duplicate Runtime authority.
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
- `economics.historical_viability` is an inert archive reader used by explicit
  CLI list/show commands. It uses the existing read-only database context, not
  current economics schema initialization/backfill. Missing history remains
  empty without creating a database or tables; historical report values,
  ordering and pagination are unchanged. This does not classify the active
  inference ledger, pricing worker or paired-evaluation writers as Legacy.

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
| Position entry | `insert_position`: native private-fill materialization or explicit external-position adoption | These represent distinct provenance. Generic compatible-position coalescing needs exact Plan/account audit before changing it. |
| Position field groups | Repository materialization / exit-plan / observation / closed-settlement field allowlists and status CAS | Producers span materializer, position manager, protection coordinator, reconciler and PnL; field ownership is not proof of one high-level owner. |
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
