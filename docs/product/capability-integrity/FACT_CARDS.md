# Capability Fact Cards

This file is the compact, reviewed card set for all inventory IDs. The exact
product promise and discovery sources are inherited from
[`CAPABILITY_INVENTORY.md`](CAPABILITY_INVENTORY.md); each row below records the
rest of the required chain.

Notation:

- `entry → service → state → consumer → result` is the production path.
- `Visible / recovery` names the owner-facing result and failure/recovery seam.
- `Review` records the reviewer challenge, especially where a page, producer,
  helper, or test could be mistaken for a final consumer.
- `E2` refers to the focused test manifest in
  [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md), not merely the existence of a test
  file.

## Runtime and operations

| ID | Runtime path and durable state → final consumer/result | Visible / recovery | Evidence and verdict | Review |
|---|---|---|---|---|
| RT-01 | installer/release → packaged launcher → runtime payload/state → tray/CLI → first launch | install status; activation rollback | E1+E2, Supported | Package tests cross activation seams; installed-runtime observation is optional. |
| RT-02 | CLI/tray → gateway lifecycle → pid/state files → supervisor → stable endpoint | status/stop; stale-pid recovery | E1+E2, Supported | Lifecycle result, not process-spawn alone, is asserted. |
| RT-03 | gateway runner → state machine → child state → frontdoor proxy → backend availability | health/stability; child restart | E1+E2, Supported | Crash/restart contract is asserted without requiring a natural crash. |
| RT-04 | UI/CLI/API → pause state → scheduler/execution guards → work stops/resumes | top bar/status; explicit resume | E1+E2, Supported | Layered pause tests cross state, scheduler, and resume seams. |
| RT-05 | UI/CLI/API → restart request → restart state → supervisor → new worker | progress/status; prior worker retained on failure | E1+E2, Supported | Restart success and failure retention are executable acceptance facts. |
| RT-06 | startup → scheduler registry → job/runtime records → job runner → periodic work | Settings/status; timeouts and selective pause | E1+E2+E3, Supported | An isolated Runtime started 44 jobs and naturally executed collector, Universe, backfill, PI, and maintenance work. |
| RT-07 | connectivity monitor → pause reason/state → scheduler/execution gates → auto-resume | health/audit; hysteresis recovery | E1+E2, Supported | Connectivity and auto-resume tests cross the recovery seam. |
| RT-08 | startup gates → gate/recovery state → runtime admission → safe observable service | doctor/readiness; bounded recovery | E1+E2, Supported | Safety gate and bounded recovery consumer are executable. |
| RT-09 | Command Center/API/CLI → continuity coordinator → session/checkpoint state → promotion router | candidate/shadow status; drain/rollback | E1+E2, Experimental | Executable subsystem exists but remains outside the declared support surface. |
| RT-10 | API/CLI → health/doctor/stability probes scoped to serving Runtime → reports → UI/operator → differentiated status | dashboard/JSON; fix hints | E1+E2, Supported | Serving Runtime ID/PID/port and two independent contexts are executable; default-branch delivery resolves #426. |
| RT-11 | API/CLI → persistence service → bundle/manifest → verifier/importer → restored state | progress/report; reject invalid bundle | E1+E2, Supported | Backup integrity tests cross WAL/verification seams. |
| RT-12 | scheduler/settings → retention policies → prune audit/state → bounded stores → retained evidence | diagnostics; safe retry | E1+E2, Supported | Focused retention tests protect causal evidence and indexes. |
| RT-13 | Settings/tray/API/CLI → update service → immutable payload/activation state → launcher → new version | progress/version; rollback/recovery | E1+E2, Supported | Package activation and recovery are executable acceptance facts. |
| RT-14 | CLI/Settings → tunnel start → durable process identity → verified status/stop → remote frontdoor | tunnel status; ownership mismatch and auth failure visible | E1+E2, Implemented | PID, executable, and config command must match; unrelated reused/legacy PIDs are never terminated. |
| RT-15 | CLI/Settings → network config → client/session factory → provider calls → routed request | diagnostics; direct fallback/error | E1+E2, Supported | Egress and CLI tests cover routing and failure semantics. |

## Market data and universe

| ID | Runtime path and durable state → final consumer/result | Visible / recovery | Evidence and verdict | Review |
|---|---|---|---|---|
| MD-01 | scheduler `market_data` → provider → market snapshots → observer/risk/UI → current facts | Markets/health; stale/error state | E1+E2+E3, Supported | An isolated Runtime persisted 39 spot/perp snapshots and surfaced reported provider errors honestly. |
| MD-02 | scheduler `price_feed` → exact-instrument fetch → cache → Position Manager → protective mark | Positions/health; stale price blocks close | E1+E2, Supported | Exact venue+market keys prevent cross-venue false freshness. |
| MD-03 | collectors → venue APIs → orderbook/funding/OI tables → scanners/observer → microstructure evidence | Markets/diagnostics; per-source errors | E1+E2+E3, Supported | Natural collectors persisted orderbook, funding, and OI rows and the coverage API reported current source-specific state. |
| MD-04 | context scheduler → optional sources → context snapshots → analysis bundle → bounded context | Markets; missing-provider degradation | E1+E2, Supported | Freshness, degradation and final data-utilization acceptance pass with mocked provider boundaries. |
| MD-05 | catch-up/nightly jobs → OHLCV provider → bars/readiness state → features/settler → historical inputs | coverage/CLI; partial retry | E1+E2+E3, Supported | Natural active-universe backfill planned and completed three ready symbols with 4,219 fetched bars. |
| MD-06 | refresh job/UI → scanners/allocator → universe assets/members → observer/ranker → candidates | Universe Board; expiry/refresh | E1+E2+E3, Supported | Natural refresh scanned 1,000 assets, selected 30, persisted a snapshot, and emitted nine attributable candidates. |
| MD-07 | outcome jobs → exposures/outcomes → family calibration stats → allocator multiplier → bounded scoring | Learning/CLI; neutral 1.0 fallback | E1+E2, Experimental | External Windows runtime observations are tracked separately from this local code review. |
| MD-08 | scheduler → outcome settler → horizon outcomes → calibration/learning consumers → settled evidence | Learning/CLI; retry/incomplete | E1+E2, Supported | Tests assert cost-aware settlement through its consumers. |
| MD-09 | regime job/API → regime detector → regime rows → observer/persona/risk context → regime-aware input | Markets/doctor; neutral fallback | E1+E2, Supported | Detector, Universe/ranker/position consumers and attribution checks pass. |
| MD-10 | fetch/analyze jobs → news stores → relevance analysis → News UI/agent context → news evidence | News Intel; dedupe/provider errors | E1+E2, Supported | Analysis lifecycle, retry/failure, research injection and owner visibility checks pass. |
| MD-11 | market-hours job → equity observer → opportunities/runs → Operator → equity candidate decision | Agent Activity; market-hours skip | E1+E2, Experimental | Tested, but equity is outside active crypto scope. |
| MD-12 | prediction jobs → prediction stores/claims → observer/UI → research opportunity | Prediction Lens; provider errors | E1, Experimental | Research output is not an execution promise. |
| MD-13 | settings/preflight → venue metadata/constraints → eligibility result → Risk/Gateway → accept/reject | Settings/doctor; hard reject | E1+E2+E3, Supported | Natural Runtime marked all nine candidates ineligible when live prices were absent and PI correctly produced no evaluable action. |

## Research, opportunities, and decisions

| ID | Runtime path and durable state → final consumer/result | Visible / recovery | Evidence and verdict | Review |
|---|---|---|---|---|
| DC-01 | scheduler → Market Observer → opportunity/generation rows → ranker/Operator → candidate evidence | Agent Activity; durable failure | E1+E2, Supported | E2 crosses observer-to-Operator seam. |
| DC-02 | paper job → ranker → admission/evidence rows → Operator queue → bounded candidate | Blotter; terminal reject reasons | E1+E2, Supported | Ranker is admission, not strategy authority. |
| DC-03 | wake/schedule → System Operator → decision row → proposal/terminal ledger → trade/skip | Blotter/Command Center; retryable pending | E1+E2, Supported | Sole-authority and dedup constraints asserted. |
| DC-04 | scheduler → PI daemon → pending scan/wake → Operator → decision even without event | Agent Activity; timeout/retry | E1+E2, Supported | Scheduled fallback has production trigger and tests. |
| DC-05 | Operator trade decision → proposal builder → trade_proposals → Gateway → attributable order path | Blotter/drawer; identity rejection | E1+E2, Supported | Tests retain opportunity/decision identity. |
| DC-06 | Blotter/API → approve/reject service → proposal state/audit → Gateway or terminal reject | Blotter; conflict/error | E1+E2, Supported | Human action reaches final proposal state. |
| DC-07 | projections/CLI → causal joins → decision/ledger read model → owner → complete candidate trace | Blotter/CLI; missing-link markers | E1+E2, Supported | Does not infer absent stages as success. |
| DC-08 | agent routes → run/opportunity stores → read models → Agent Activity → history/failure | page/API; explicit error | E1+E2, Supported | Durable failure lifecycle, read APIs and UI error rendering are covered. |
| DC-09 | command/API → runtime trigger → agent run row → supported agent → output | command palette; rejection/timeout | E1+E2, Supported | Route returns job identity and bounded worker persists queued through final state. |
| DC-10 | UI/API/CLI → operator shell → sessions/messages/tasks → tools/read models → command result | Command Center; supervision/recovery | E1+E2, Supported | Tests cover workspace persistence and supervision. |
| DC-11 | UI/API → operator shell → branch/task/extension stores → bounded executor → session result | Command Center; permission failure | E1, Experimental | Existence does not promote self-extension to supported behavior. |
| DC-12 | daily job/UI → Serenity agents → evidence/claim/theme stores → review/promotion → research artifact | Serenity Lens; attribution/errors | E1, Experimental | Kept outside active execution path. |
| DC-13 | UI/API → profile parser → snapshot → research worker → adapted research; theme More/Less → feedback event → detail history only | Serenity Lens explicitly labels history-only behavior | E1+E2, Experimental, CI-09 | The two subpaths are now honestly separated; feedback persistence is not represented as adaptation. |

## Risk and authority

| ID | Runtime path and durable state → final consumer/result | Visible / recovery | Evidence and verdict | Review |
|---|---|---|---|---|
| RK-01 | proposal → Execution Gateway → Risk Engine → risk_decisions → gateway → approve/reject | Risk/Blotter; fail closed | E1+E2, Supported | E2 proves Risk cannot be bypassed on active path. |
| RK-02 | Settings/API → revision-checked policy service → config/history → Risk Engine → scoped policy | Risk UI; conflict/reset | E1+E2, Supported | Persistence is tied to the actual engine consumer. |
| RK-03 | UI/CLI/API → kill switch → runtime/config state → Risk/Gateway → block | Risk/top bar; explicit release | E1+E2, Supported | Global and mode scopes reach enforcement. |
| RK-04 | proposal/account state → risk checks → risk decision → Gateway → bounded leverage/loss/margin | Risk UI; violations | E1+E2, Supported | Limits are deterministic final rejects, not advisory. |
| RK-05 | proposal/positions → exposure checks → risk decision → Gateway → concentration reject | Risk UI; violations | E1+E2, Supported | Portfolio/symbol constraints are consumed at approval. |
| RK-06 | proposal snapshot/instrument → freshness/integrity checks → decision → Gateway → hard reject | Risk/doctor; reason codes | E1+E2, Supported | Exact-data failure cannot fall through to execution. |
| RK-07 | UI/CLI/config → permission resolver/tool registry → audit/state → operator tools → allow/deny | top bar/Settings; denied action | E1+E2, Supported | Auto does not bypass constitutional restrictions. |
| RK-08 | Overview/live route → scorecard → criteria result → live unlock gate → block/allow state transition | Overview/Settings; blockers | E1+E2, Experimental | Scorecard gates unlock state, not live proposal routing; Live remains outside the supported surface. |

## Execution and position lifecycle

| ID | Runtime path and durable state → final consumer/result | Visible / recovery | Evidence and verdict | Review |
|---|---|---|---|---|
| EX-01 | scheduler candidate → Operator → proposal → Risk → paper broker → order/fill/position/outcome | Blotter/Positions/Analytics; fail closed | E1+E2, Supported | Real paper roundtrip crosses all necessary seams. |
| EX-02 | approved demo proposal → Gateway → venue adapter → exchange order/fill → local position/reconcile | Blotter/Positions; adapter error | E1+E2, Experimental | Mocked adapters are not exchange-runtime proof; outside active scope. |
| EX-03 | Settings/API → scorecard/confirm → live state+permission+audit → authority gates → unlocked/relocked state | Settings labels this an experimental prerequisite; lock symmetry | E1+E2, Experimental | Card is only the state transition; Live remains outside the supported surface and EX-04/#418 covers the broken proposal behavior. |
| EX-04 | live state → **no consumer in new-proposal mode selection** → paper/demo proposal | UI now states the boundary; diagnostic remains warning; relock works | E1+E2, Broken, CI-02, `LU-C07` missing | Broker branches do not repair the missing upstream trigger. |
| EX-05 | approved intent → constraint normalizer → normalized order fields → broker → venue-valid request | decision/order evidence; reject | E1+E2, Supported | Normalization cannot rewrite approved direction/notional intent. |
| EX-06 | durable approved order → materializer → exchange ids/fills/positions → reconciler → one chain | Blotter/Positions; idempotent recovery | E1+E2, Implemented | Contract is executable, but no external execution mode is owner-declared as supported. |
| EX-07 | proposal/stops → broker-specific bracket helpers → exchange order ids → reconcile → protection | Positions; unsupported/failure | E1+E2, Experimental | Private-fill paths are tested; real venue behavior remains outside the supported surface. |
| EX-08 | price-feed tick → Position Manager → exit rule → close service → fill/position/outcome | Positions/alerts; exact-price fail closed | E1+E2, Supported | Local stop/target/max-hold chain reaches verified close. |
| EX-09 | adverse signal → exit evaluator → best-effort in-memory wake → entry-opportunity evaluator; failed delivery writes `wake_failed` and an error | Settings says review-only; wake/error visible | E1+E2, Broken, CI-03, `PL-PM-14` | With no pending entry candidate, evaluation returns before attaching the wake. Otherwise the wake changes the decision fingerprint but is not rendered into the prompt, and the active schema rejects hold/adjust/close. |
| EX-10 | horizon/thesis condition → Position Manager → wake/authorized close → position outcome | Positions/Operator; reevaluation | E1+E2, Supported | Tests distinguish review wake from automatic lifecycle close. |
| EX-11 | reconciliation job → external position classifier → adoption record → manager/reconciler → managed lifecycle | Positions; opt-in/refusal | E1+E2, Experimental | External adoption remains optional and outside core. |
| EX-12 | scheduler/API → local/exchange readers → reconciliation state/events → repair/classification → converged view | Positions; stale/error | E1+E2, Implemented | Tests assert no fabricated fills. |
| EX-13 | cleanup/reconcile jobs → stale/disappearance evidence → order/position state → settlement path → resolved status | Positions/audit; retry | E1+E2, Implemented | Administrative close remains outcome-ineligible when economics absent. |
| EX-14 | local paper close → exact fills/fees + local funding evidence → atomic position/cash/outcome settlement → analytics/Learning | Positions/Analytics; explicit incomplete and recovery state | E1+E2, Supported | Futures fail closed when the local funding window is incomplete; recovery materializes settlement exactly once. |
| EX-15 | Operator rejects reduce/scale-in; partial-reduce dispatcher fails closed → isolated OKX helper remains reference-only | settings/action surface | E1+E2, Experimental, CI-06 | No broker mutation is promised; full close remains the supported lifecycle. |
| EX-16 | exchange close/reconciliation → exact fill/fee verification → private funding/bill lookup → position/outcome settlement | Positions/Analytics; explicit incomplete | E1+E2, Partial, CI-05 | The verifier fails closed, but no private exchange funding/bill producer exists. |

## Portfolio, performance, and attribution

| ID | Runtime path and durable state → final consumer/result | Visible / recovery | Evidence and verdict | Review |
|---|---|---|---|---|
| PF-01 | settings/fills → paper account ledger → cash/reserve/equity state → Risk/analytics → affordability/equity | Settings/Overview; reconciliation | E1+E2, Supported | Ledger events, not derived UI totals, are authoritative. |
| PF-02 | Settings/sync → capital route/repository → capital_events → net-deposit read model → recorded cash-flow summary | Settings; validation/audit | E1+E2, Supported | Recording is split from adjusted-performance consumer. |
| PF-03 | capital_events → visible snapshot-window flow join → adjusted account-equity points/markers → Overview second series | Raw and adjusted Account Equity remain distinct; verified PnL unchanged | E1+E2, Implemented, CI-01 | API returns method, scope, baseline, markers, and both series; capital writes invalidate the view cache. |
| PF-04 | scheduler/account state → portfolio snapshots → equity-history route → Overview → time series | Overview; gaps/staleness | E1+E2, Supported | Raw equity semantics must remain distinct from PF-03. |
| PF-05 | orders/fills/positions → read routes → UI blotter → owner → lifecycle/cost view | Trades & Positions; errors | E1+E2, Supported | Final close/cost fields are represented explicitly. |
| PF-06 | eligible outcomes → analytics builders → read models → Overview/CLI → PnL/drawdown/expectancy | charts; unavailable reasons | E1+E2, Supported | Administrative/incomplete records are excluded, not zero-filled. |
| PF-07 | causal ids → attribution joins → lineage models → drawers/CLI → full ancestry | detail drawers; missing-link flags | E1+E2, Supported | Tests cross opportunity-to-outcome identity. |
| PF-08 | all candidate stages → flow/funnel ledger → API/CLI/UI → owner → survivor-bias-aware counts | Overview/Blotter; exclusions | E1+E2, Supported | Terminal skips/rejects remain in denominator. |
| PF-09 | exposures/outcomes → attribution analyzers → reports → Learning/owner → factor/family net evidence | CLI/Learning; cohort warnings | E1+E2, Experimental | No qualified natural cohort verdict; measurement is not Alpha proof. |

## Learning and policy evolution

| ID | Runtime path and durable state → final consumer/result | Visible / recovery | Evidence and verdict | Review |
|---|---|---|---|---|
| LN-01 | post-trade job → reviewer/case builder → lessons/cases → retrieval/review → attributable review | Learning; ineligible skip | E1+E2, Supported | Only genuine eligible outcomes enter the consumer path. |
| LN-02 | risk critic job → risk history analysis → risk lessons → review/Risk constraint consumer → reusable lesson | Learning; reject/archive | E1+E2, Supported | Structured applied lessons can only tighten/reject. |
| LN-03 | daily job → dreaming agent → dreaming log/daily review + trade-case lessons → lesson reviewer → applied lessons → Layer3/Operator prompt | Learning; agent outputs/errors and lesson lifecycle | E1+E2, Supported | Scheduler/producer tests and the exact `trade_case_study` lesson consumer acceptance close the final-reuse join. |
| LN-04 | scheduler → insight synthesizer → trading memo → observer/ranker/Operator bundle → bounded advice | Learning/agent evidence; expiry | E1+E2, Supported | Memo remains advisory and has neutral fallback. |
| LN-05 | agent request → knowledge retrieval/FTS → retrieved bundle → prompts/ranker → later decision evidence | CLI/Learning; empty fallback | E1+E2, Supported | Final-consumer tests prove injection, not merely search. |
| LN-06 | Learning/API → lesson store transitions → history/state → eligible consumers → accepted/promoted/overturned | Learning; dry-run/reject | E1+E2, Supported | Review state gates consumption. |
| LN-07 | eligible lesson → skill drafter → draft store → human review → draft artifact | Learning; reject/archive | E1, Experimental | No automatic activation is intentional. |
| LN-08 | outcomes/job → guard generator/store → review/promotion → paper ranker → block/penalty with shadow fallback | Learning/Settings; disable | E1+E2, Supported | Consumer exists; influence is default closed. |
| LN-09 | predictions+outcomes → calibration analytics → calibration state/report → Operator/owner → calibrated evidence | Learning; insufficient sample | E1+E2, Supported | Report existence is separated from behavioral influence. |
| LN-10 | non-selected candidate → shadow record → resolver/outcome → analytics → counterfactual result | CLI/Learning; unresolved | E1+E2, Experimental | Observe-only, never execution authority. |
| LN-11 | filter outcomes → controller report → shadow recommendation → owner → diagnosis | Settings/CLI; neutral/no mutation | E1+E2, Experimental | Registry explicitly says filter-parameter consumer is unwired. |
| LN-12 | exposures/outcomes → evidence evaluator → contribution verdict → Learning UI/API → measured effect | Learning; cohort invalidation | E1+E2, Experimental | Current natural treatment/control verdict absent. |
| LN-13 | scheduler → lifecycle controller → experiment/config state → authority gate → advance/refreeze | Settings/doctor; freeze | E1+E2, Experimental | Automation is intentionally paper-only and experiment-gated. |
| LN-14 | Settings → active persona notional/Kelly → effective params → System Operator sizing → immutable Proposal quantity | Settings/sizing trace; legacy fallback and Risk caps | E1+E2, Supported, `PS-S14` | Base sizing is complete; leverage/stop/add ranges are explicitly assigned to LN-16/LN-17 experiments. |
| LN-15 | clean regime cohorts → appetite learner → bounded adjustment → persona sizing → notional change | doctor/sizing trace; zero fallback | E1+E2, Experimental | Learning changes bounded appetite/notional only. |
| LN-16 | lifetime eligible close count/distress → consumer-filtered unlock resolver → effective notional/Kelly → entry sizing | Settings states global-history limitation; distress refreeze | E1+E2, Experimental, CI-04, `PS-S10` | Only active entry parameters can unlock, but historical outcomes are not scoped to the current Persona/policy/step; #409. |
| LN-17 | policy helper input → bounded reference decision → no production consumer by design | Settings/API block new enablement; legacy configs can switch off | E1+E2, Experimental, CI-06, `PS-S11`/`PS-S12` | Helper output is explicitly non-operational and cannot create an order. |
| LN-18 | 8-hour job → integrity/distress checks → explicit safety hold → no config mutation | Settings blocks new enablement; legacy configs can switch off | E1+E2, Experimental, `PS-S13` | Time and broad health are not a prospective step verdict; automatic progression is fail-closed under #409. |
| LN-19 | policy writes → version/history stores → static diff/read routes → API → immutable history view | API diff/detail/history; 404 on unknown versions | E1+E2, Supported, `LP-S22` | Static diff route precedes the dynamic policy-type route and reaches its final consumer. |

## Security, configuration, and owner visibility

| ID | Runtime path and durable state → final consumer/result | Visible / recovery | Evidence and verdict | Review |
|---|---|---|---|---|
| OS-01 | Settings/CLI → secret store → key reference only → provider/exchange client → authenticated use | masked status/test; overwrite/delete | E1+E2, Supported | Read APIs do not return secret values. |
| OS-02 | Settings/API/CLI → provider config/auth → config/credential refs → model router → completion/fallback | Settings/status; fallback error | E1+E2, Supported | Provider routing and fallback tests pass. |
| OS-03 | Settings/API → candidate private read-only test → write-only store/status → stored private read-only test | Settings; reject without save; secret-safe error | E1+E2, Implemented | Bitget/OKX/Binance verification is executable but intentionally outside Supported Core. |
| OS-04 | Settings → validation/update route → config/audit → runtime consumers → effective behavior/coverage | Settings; validation/conflict | E1+E2, Supported | Cards separately flag settings with missing final consumers. |
| OS-05 | trusted mutation → audit writer → JSONL/DB audit → projections/UI/CLI → attributable history | audit views; append failure | E1+E2, Supported | Critical routes assert real actor/source. |
| OS-06 | scheduler/events → notification service → send attempt/state → Telegram API → alert/summary | Settings/test/status; delivery error | E1+E2, Supported | Delivery attempts and failures are explicit; provider availability is external. |
| OS-07 | subsystem failure → structured error/health/alert state → UI/CLI → owner → actionable failure | status/doctor/alerts; recovery hint | E1+E2+E3, Supported | Missing LLM configuration produced durable failed agent runs plus explicit degraded health/status instead of false success. |
| OS-08 | backend events/queries → websocket/SSE/poll state → frontend cache → pages → refreshed values | connection state; polling fallback | E1+E2, Supported | Realtime transport tests cross update and fallback seams. |
