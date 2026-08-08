# Practice Matrix

## Decision Scale

| Decision | Meaning |
|---|---|
| Adopt | Reusable mechanism fits Aletheia with little semantic change |
| Adapt | Valuable idea needs Aletheia-specific authority, identity, or durability |
| Preserve | Aletheia already expresses the stronger invariant |
| Defer | Plausible value, but current Alpha/workload evidence is insufficient |
| Investigate | A bounded source/runtime question must be answered first |
| Reject | Conflicts with state consistency, safety, auditability, or semantic honesty |

The decision applies to the mechanism, not the entire reference file or library.

## Domain 1: UI Design System

Primary reports: [`01-ui-design-system/`](../01-frontend/01-ui-design-system/).

| Decision | Practice | Reason |
|---|---|---|
| Adopt | one modal behavior contract | focus, escape, backdrop, and portal behavior repeat |
| Adopt | visible page-purpose and limitation text | improves operator orientation and state honesty |
| Adopt | typed DOM-to-chart color adapter | canvas cannot inherit CSS semantics directly |
| Adapt | semantic utility composition | extract only repeated boundaries, not global CSS rewrite |
| Adapt | empty-state family | require empty/stale/partial/unavailable/permission variants |
| Adapt | density rules | preserve decision clarity and accessible targets |
| Adapt | motion language | consume reduced-motion boundary; library is optional |
| Investigate | local font packaging | local-first fidelity versus bundle/startup cost |
| Reject | raw-button proliferation | behavior and disabled/destructive semantics remain distributed |
| Reject | A-share red-up/green-down convention | market semantics differ; keep two color planes |
| Reject | unconditional animation | conflicts with reduced-motion/accessibility |
| Reject | current custom calendar contract | keyboard/ARIA semantics insufficient |

## Domain 2: Information Architecture

Primary reports: [`02-information-architecture/`](../01-frontend/02-information-architecture/).

| Decision | Practice | Reason |
|---|---|---|
| Adopt | eager shell, lazy feature pages | improves startup and preserves global state |
| Adopt | canonical route redirects | keeps URLs, navigation, docs, and telemetry aligned |
| Adopt | cached initialization guard during background refresh | avoids shell blanking/flicker |
| Adapt | one route metadata manifest | keep metadata data-only so it does not eagerly import pages |
| Adapt | navigation as ambient status | small owned child indicators, not shell orchestration |
| Adapt | route loading/error boundary | destination, retry, return, and asset-skew recovery |
| Defer | dynamic plugin navigation | no MVP need for generic extension catalogue |
| Investigate | mobile/compact operator scope | determine supported workflow before redesign |
| Reject | flat capability catalogue | Aletheia workflow taxonomy is more meaningful |
| Reject | fail-open onboarding pattern for safety gates | authority must remain fail-closed and audited |
| Reject | business logic in Layout | global visibility does not imply global ownership |
| Reject | desktop-only fixed shell | preserve responsive rail/drawer behavior |

## Domain 3: Data-Intensive Components

Primary reports: [`03-data-components/`](../01-frontend/03-data-components/).

| Decision | Practice | Reason |
|---|---|---|
| Adopt | measured threshold virtualization | normal DOM for small sets, virtualization for demonstrated scale |
| Adopt | optional column controls optional I/O | avoids invisible provider/cache cost |
| Adopt | retain chart logical range | preserves operator working context |
| Adopt | `ResizeObserver` on chart container | window resize misses shell/grid geometry changes |
| Adapt | analytical column descriptors | include fetch/capability/partial semantics in typed model |
| Adapt | table/card alternate views | share canonical selection/filter/identity pipeline |
| Adapt | versioned view preferences | convenience only; migrate stable column IDs |
| Adapt | long-lived chart lifecycle hook | share mechanics, not financial option semantics |
| Investigate | actual high-cardinality pages | measure rows, interaction, memory, accessibility |
| Reject | silent batch truncation | partial scope must be positively disclosed |
| Reject | viewport-dependent safety work | risk/audit/execution cannot depend on mounted rows |
| Reject | mouse-only sorting/controls | dense interaction still needs keyboard/state semantics |
| Reject | universal ECharts migration | library change needs a concrete requirement |

## Domain 4: Client State

Primary reports: [`04-client-state/`](../01-frontend/04-client-state/).

| Decision | Practice | Reason |
|---|---|---|
| Adopt | typed query-key factories | one identity for query, mutation, push routing, tests |
| Adopt | push-to-invalidate derived state | REST/read model remains authoritative |
| Adopt | one owner per fallback poll | avoids duplicated work and hidden timing |
| Adopt | visible transport state | connected/reconnecting/fallback is not data freshness |
| Adopt | event-specific invalidation | limits cost and semantic fan-out |
| Adapt | source-to-read-model dependency graph | derive from typed key families and validate coverage |
| Adapt | hybrid push/poll contract | name fallback case, cadence, stale threshold, stop condition |
| Adapt | route-independent task state | backend id, durable state, replay/reconciliation |
| Adapt | per-consequence hidden-tab policy | alerts may need backend/OS path, not browser timers |
| Investigate | multi-tab leader election | only if duplicated sockets/polls are measured |
| Reject | user-disabled freshness for safety facts | critical state keeps approved freshness policy |
| Reject | speculative safety mutations | backend confirmation precedes success state |
| Reject | silent permanent event loss | diagnostics plus authoritative reconciliation required |
| Reject | whole-cache invalidation as normal flow | dependency graph should be primary |

## Domain 5: API Contracts

Primary reports: [`05-api-contracts/`](../01-frontend/05-api-contracts/).

| Decision | Practice | Reason |
|---|---|---|
| Adopt | one bounded transport helper plus small domain clients | shared mechanics without monolithic facade |
| Adopt | capability facts including limits | backend describes and enforces optional work |
| Adopt | masked credential status | presence/last-four only; raw value is write-only |
| Preserve | sanitized validation errors | retain safe field location/type/message |
| Adapt | stable error envelope | preserve status/code/retry/field/correlation identity |
| Adapt | critical backend response models and checked/generated TS | begin on safety/financial surfaces only |
| Adapt | runtime decoding of critical reads | static TS types do not validate network input |
| Adapt | capability/configuration/permission/health taxonomy | four independent axes |
| Adapt | named confirmation request types | UI can construct the entire backend contract |
| Investigate | remote token browser storage | choose session/CSP/bootstrap model in security scope |
| Reject | message-text routing | status/code should drive auth and recovery |
| Reject | `request<T>` cast as validation proof | producer model and runtime decode are required |
| Reject | frontend-only authority gates | backend remains the only authorization boundary |
| Reject | one giant type-generation rewrite | bounded critical surfaces first |

## Domain 6: FastAPI and Lifecycle

Primary reports: [`06-fastapi-architecture/`](../02-backend-data/06-fastapi-architecture/).

| Decision | Practice | Reason |
|---|---|---|
| Adopt | domain warm-up separate from process liveness | UI can diagnose while feature is honestly warming |
| Adopt | one analytical repository/cache owner | avoids independent hot-cache islands |
| Adopt | explicit startup dependency order | scheduler/consumer cannot race unbuilt prerequisites |
| Adopt | re-probe mutable capabilities | entitlement and health can change at runtime |
| Adapt | typed runtime context | explicit construction plus narrow consumers |
| Adapt | lifecycle manifest | owner, dependencies, gates, handle, shutdown policy |
| Adapt | typed readiness facts | alive/interface/data/operation/health are separate |
| Adapt | task ownership registry | every task/thread has start, failure, stop, timeout |
| Preserve | migration before writers | trading state cannot run on unknown schema |
| Preserve | runtime-role isolation | shadow/candidate cannot start background authority |
| Preserve | producer-before-consumer shutdown | shutdown protects next-boot semantics |
| Reject | raw daemon thread over shared state | no retained handle or recovery surface |
| Reject | module-global service locator | hides dependencies and broadens access |
| Reject | broad best-effort authority initialization | migrate/risk/execution/audit must fail or pause |

## Domain 7: Provider Normalization

Primary reports: [`07-provider-normalization/`](../02-backend-data/07-provider-normalization/).

| Decision | Practice | Reason |
|---|---|---|
| Adopt | versioned capability snapshots | distinguish denial, transient probe failure, and age |
| Adopt | dataset-specific provider choice | semantics/cost differ across datasets |
| Adopt | safe declarative observational mapping | bounded, no-eval, validated owner configuration |
| Adopt | segmented history acquisition | bounded memory/progress/failed scope |
| Preserve | exact instrument identity | exchange + canonical symbol + market type end to end |
| Preserve | durable collector outcome taxonomy | success/partial/empty/timeout/budget/disabled differ |
| Preserve | bounded coverage repair | readiness follows verified coverage, not fetch return |
| Adapt | provider descriptor | descriptive capability, never execution permission |
| Adapt | typed provider result envelope | requested/obtained/fallback/completeness/quality/times |
| Adapt | semantic fallback matrix by consumer | dashboard and protective execution differ |
| Adapt | canonical schema registry | one executable schema authority per dataset |
| Adapt | reject/quarantine counts | required malformed facts cannot silently become null |
| Investigate | account-wide budget ownership | multiple processes/products may share account/IP |
| Reject | silent venue/timeframe/market substitution | provenance and compatible policy required |
| Reject | partial frame as full replacement | completeness authorizes replacement |
| Reject | provenance only in logs | source must survive persistence/cache/response |

## Domain 8: Analytical Storage

Primary reports: [`08-analytical-storage/`](../02-backend-data/08-analytical-storage/).

| Decision | Practice | Reason |
|---|---|---|
| Adopt | name read models by workload | latest cross section, session history, sparse dates, matrix differ |
| Adopt | single-flight identical expensive reads | reuse leader result without serializing unrelated keys |
| Adopt | byte/entry/TTL cache bounds plus metrics | TTL alone is not memory control |
| Adopt | monotonic TTL | wall-clock changes do not distort expiry |
| Adopt | projection before materialization | columns and predicates reduce I/O/memory |
| Adopt | sparse partition reads | exact event dates should not scan intervals |
| Adopt | actual session axes and warm-up | bars are not calendar days |
| Adopt | manifest durable derived caches | identity, versions, coverage, layout, completeness |
| Adapt | one-way analytical mirror | only after measured SQLite analytical bottleneck |
| Adapt | typed analytical result envelope | ready/warming/partial/failed with scope/provenance |
| Adapt | staged analytical readiness | optional research cache cannot gate trading readiness |
| Adapt | supervised prewarming | owned task, cancellation, role, shutdown |
| Defer | Parquet/DuckDB OHLCV mirror | requires Alpha workload, export watermark, rebuild proof |
| Defer | disk-backed dense matrix | requires repeated multi-year vectorized bottleneck |
| Reject | Parquet for canonical trading state | lacks relational/causal transaction authority |
| Reject | unbounded process caches | unknown key diversity can retain unknown memory |
| Reject | `union_by_name` as semantic proof | names do not prove units/provider/timeframe/meaning |

## Domain 9: I/O and Consistency

Primary reports: [`09-io-consistency/`](../02-backend-data/09-io-consistency/).

| Decision | Practice | Reason |
|---|---|---|
| Adopt | same-directory temporary publication | prevents readers seeing torn single files |
| Adopt | refuse unexplained shrinking rebuilds | protects known-good coverage |
| Adopt | preserve old data for failed scopes | failed acquisition is partial/stale, not deletion |
| Adopt | pending and running both active | closes admission/start race |
| Adopt | writer permit follows physical mutation | timeout changes status, not execution |
| Preserve | commit business truth before retryable mirrors | durable outbox and stable identity |
| Preserve | definitive write outcome | caller waits for commit/rollback after admission |
| Preserve | verified state movement | checkpoint, stage, hash, validate, marker-last |
| Adapt | dataset generation publication | complete staging plus manifest/pointer-last |
| Adapt | completeness envelope | requested/succeeded/failed/coverage/publication id |
| Adapt | atomic typed convenience store | lock, schema, replace, quarantine, permissions |
| Adapt | fsync by authority class | acknowledge durability according to recovery contract |
| Investigate | remaining Aletheia direct writers and dual writes | classify by authority before changing |
| Reject | per-file atomicity as dataset atomicity | mixed generations remain possible |
| Reject | attempt time called last sync | success/completeness must be proven |
| Reject | file mtime as migration proof | identity, schema, scope, source are missing |
| Reject | progress “done” before terminal classification | traversal and publication truth differ |

## Domain 10: Realtime Synchronization

Primary reports: [`10-realtime-sync/`](../03-runtime-engineering/10-realtime-sync/).

| Decision | Practice | Reason |
|---|---|---|
| Adopt | push invalidation plus REST authority | latency without duplicating state authority |
| Adopt | one application-root connection owner | avoids per-page competing streams |
| Adopt | initial catch-up on connect | reconnect must reconcile current state |
| Adopt | visible degraded connection | transport silence is not health |
| Adopt | centralized change-to-query routing | fan-out becomes reviewable dependency graph |
| Adopt | publish only after verified state publication | notification cannot precede truth |
| Adapt | per-client bounded channels | isolation plus drop/coalesce telemetry |
| Adapt | monotonic committed change sequence | timestamp max can miss corrections |
| Adapt | consequence-based cadence tiers | alerts, controls, analytics have different urgency |
| Adapt | durable alert cursor/replay | needed where missed events have consequence |
| Adapt | source/as-of/freshness response envelope | component sees provenance, not only value |
| Preserve | exact decision freshness | protective paths do not accept generic stale fallback |
| Reject | connected equals fresh | transport and authoritative refresh age differ |
| Reject | transient stream as audit | durable events need identity/replay authority |
| Reject | sequential unbounded broadcast | one client cannot block all delivery |
| Reject | silent prolonged chart failure | old data must be visibly stale/degraded |

## Domain 11: Background Jobs

Primary reports: [`11-background-jobs/`](../03-runtime-engineering/11-background-jobs/).

| Decision | Practice | Reason |
|---|---|---|
| Adopt | reserve server task identity before scheduling | closes duplicate-click race |
| Adopt | overall and stage-local progress | operator sees traversal and current unit |
| Adopt | progress compaction | bounded high-frequency logs |
| Adopt | reconnect by server-owned id | route/browser lifetime does not define task lifetime |
| Adopt | process isolation for heavy analysis | cancellation, RSS, serialization, cleanup visibility |
| Adopt | workload-specific pools | analysis cannot starve control/data work |
| Adopt | schedule next/last facts near data health | timing becomes operational context |
| Adapt | durable attempt envelope | owner, heartbeat, publication, partial, recovery |
| Adapt | cancellation state machine | request, acknowledge, cooperative stop, force, cleaned |
| Adapt | schedule contract | timezone, coalesce, misfire, max instances, runtime owner |
| Preserve | Aletheia bounded workers and role ownership | stronger than reference process-local slots |
| Preserve | durable Agent run/manual queue/reconciliation | restart semantics already stronger |
| Reject | metadata-only cancellation | UI label cannot claim execution stopped |
| Reject | release lock after stale timeout | zombie writer may still mutate |
| Reject | process mutex as deployment uniqueness | multiple workers/processes remain possible |
| Reject | terminal-only history for mutating work | active attempt vanishes on restart |

## Domain 12: Engineering Assurance

Primary reports: [`12-engineering-assurance/`](../03-runtime-engineering/12-engineering-assurance/).

| Decision | Practice | Reason |
|---|---|---|
| Adopt | phase/resource telemetry on expensive boundaries | actionable evidence without log noise |
| Adopt | dataset coverage as health fact | availability is more than file existence |
| Adopt | final-artifact runtime property checks | verifies package, not build intent |
| Adopt | explicit optional/legal capability gates | absent-by-default and visible |
| Adapt | one credential classification registry | field semantics drive storage/response/export/redaction |
| Adapt | outbound destination/response policy | configurable URLs grant network authority |
| Adapt | universal resource budget vocabulary | HTTP/cache/queue/process/log/bundle/database |
| Adapt | final-platform install/upgrade/rollback smoke | release tier, not every local loop |
| Preserve | write-only secret APIs | never return stored credential |
| Preserve | PR CI, secret scan, locked deps, migration ledger | Aletheia source is stronger |
| Preserve | verified backup, signed update, health soak, rollback | do not replace with manual download flow |
| Reject | raw webhook secret readback | broken credential classification |
| Reject | live-runtime pip/npm mutation | supply-chain and rollback boundary violation |
| Reject | unsigned/unverified artifact as finished release | identity and platform trust missing |
| Reject | mutable data inside macOS app bundle | upgrade can replace user data |

## Cross-Domain Preserve List

These Aletheia invariants dominate every adaptation:

- state consistency before convenience;
- Risk approval before Execution Gateway;
- live off by default;
- exact venue/symbol/market identity;
- backend-enforced capability and permission;
- SQLite authority for causal trading facts;
- short database-only transactions and definitive outcomes;
- runtime-role ownership and producer-before-consumer shutdown;
- durable provenance, outbox, audit, and restart facts;
- write-only credentials and redacted diagnostics;
- fail-closed migrations and verified backups;
- signed immutable update identity and rollback;
- `Not available` rather than fabricated health, freshness, or completeness.

## Cross-Domain Defer List

These ideas are not rejected, but their cost is unjustified without measured
need:

- full visual-system rewrite;
- universal light mode;
- generic plugin/extension platform;
- universal provider interface;
- broad schema generation across every endpoint;
- account-wide distributed quota service;
- columnar OHLCV mirror;
- dense disk-backed matrix cache;
- multi-tab socket leader election;
- generalized untrusted strategy runtime;
- expansion to new databases or infrastructure products.

## Cross-Domain Rejection List

The following patterns repeatedly produced attractive but false confidence:

- frontend-disabled means unauthorized;
- connected means fresh;
- non-empty means complete;
- atomic file means atomic dataset;
- timeout means terminated;
- 100 percent means succeeded;
- build means verified release;
- AST validation means sandboxed;
- encrypted with adjacent key means protected from local compromise;
- provider available means data semantically usable;
- same column names mean same data meaning;
- file mtime means successful synchronization;
- UI/localStorage/module memory means durable state;
- broad green health means every operational layer is ready.
