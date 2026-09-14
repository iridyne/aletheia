# Slim Core private-I/O reachability evidence

Dated source audit for [#3134](https://github.com/iridyne/aletheia/issues/3134),
correction base `54a93724f7f0ccec848d076266aab3ba049ef319`. This is a bounded evidence
inventory, not a complete production subsystem inventory, a support promise, or
runtime qualification.
The accepted [UTA boundary](../../design/proposals/unified_trading_account.md)
and current Issue define the target; the older
[consumer matrix](uta_consumer_port_matrix_2026-08-27.md) is historical evidence.

## Concrete-client reachability

`UTA-S07` scans production imports outside `uta/` and `exchanges/`. Following
native-protection extraction and scoped credential-ingress separation (base
`a6ed49f74`), its concrete Bitget/OKX/Binance import allowlist is empty. Existing
server composition mounts the credential router only when available; this does
not claim that every Core credential or model-provider seam is converged.

| Entrypoint / consumer | Actual read and downstream authority | Containment consequence |
| --- | --- | --- |
| `server.routes.bitget` stored-credential connection endpoints, called by `ExecutionTab` | Previously three native clients read account/positions. They now call existing `uta.manager.probe_account_reachable` off the HTTP event loop. It accepts only complete account evidence, classifies errors without raw messages, and uses `query_uta` teardown. UI consumes `connected` / `error`; it does not consume the old Binance position count. | One existing account-probe owner; no separate route client or probe executor. Endpoint mode identifies the configured account, not trading permission. |
| `exchanges.credential_routes` credential management and candidate probes | Existing nine HTTP handlers and their request/status helpers moved intact out of Core. Fixed venue/profile credential keys, selected-Live conflict rejection, redacted status, audit, native Bitget account-info and temporary CCXT balance probes are unchanged. Candidate credentials are never persisted. | Core account routes import neither Secret Store nor concrete Exchange code. Missing credential-adapter imports remove only credential routes; configured-account routes and cached UTA state remain importable. AST equality and full OpenAPI equality bind this extraction; broader trust/model credential readers remain separate gaps. |
| `agent_runtime.scheduler._tpsl_fill_check_body` | Existing startup composition binds a read-only `NativeProtectionReader`. The Bitget implementation owns client construction, profile selection, parent/child wire parsing and symbol conversion. Core validates normalized account/parent identity, proves exact Order membership before `_claim_observed_tpsl_close`, then sends exact private fills to `materialize_bitget_close` after another configured-identity check. | No scheduler concrete-client import or wire parser remains. This necessary native exception adds no writer, transport, lifecycle owner or UTAAdapter v1 method. Raw per-request credential behavior is unchanged; the independent account proof remains mandatory. |

## Independently necessary evidence boundaries

The scheduler's UTA membership check precedes the durable pending-close claim:
rejecting a foreign execution Order only after claiming would wedge an open
Position. Plan status and trigger prices are not private Fill economics. Exact
Fill identity, quantity, price, fees and immutable materialization remain required.
The raw client's per-request credential resolution is distinct from UTA's
account-identity proof; moving only its import would not converge that authority.

Live trust's revision reader now crosses the existing UTA composition binding.
Only the selected live scope is resolved inside Exchange composition, using the
existing profile field list; Core receives the same hash and reference names,
not credential values. Synthetic pre-extraction identities for Bitget classic/
UTA, OKX and Binance match field-for-field, including config revision. Missing
credentials still fail before private probing; unbound or missing adapters are
unavailable. The unused raw account/environment parsers were deleted after
confirming no callers; normalized UTA authority remains the only active proof.

Installed CCXT `bitget.fetch_order` selects ordinary spot/mix/UTA order-detail
endpoints. `fetch_closed_orders` has a trigger-history option, but its normalized
parent/child evidence is not the scheduler's authority. The native client's
public `get_plan_order_history` now dispatches to the configured profile: classic
v2 uses plan history, while UTA v3 uses the official strategy-child endpoint and
preserves the existing exact-account-proof/fill-materialization bridge.

## Verification boundary

The additional startup inventory was bound to
`d1bac0e7e8a9da039a498c56577af3a4f2569cf6`. `server.app.lifespan` was the sole
production caller of `config.secrets.migrate_keyring_to_file`; it enumerated
Windows Credential Manager (or a broad fixed key list) and copied missing values
into the encrypted store before runtime initialization. This hidden historical
repair conflicts with #3134's startup/migration and bounded credential rules.
The caller, bulk migration function and advertised startup stage are removed.
Per-reference encrypted-file-first reads, keyring fallback, explicit writes,
credential generations and cross-process locking are unchanged. No operational
credential store is read, migrated or deleted by this source repair. Architecture
and startup-policy checks prevent restoration of the removed authority; scoped
keyring-only reads remain covered without materializing an encrypted store.

Stored-probe regression covers six account modes, complete versus partial
reports, execution off the request loop, refusal to construct native clients,
and sanitized failure output. Manager tests retain identity-mismatch teardown.
These mocked reads grant neither Live authority nor account qualification.
The unchanged concrete-import allowlist is deliberately not a claim that all
private I/O is contained: candidate probes and TP/SL recovery remain visible
above, and package movement alone cannot settle their ownership.

The current-head identity audit removes duplicate generic external-mode sets
from Gateway routing, position routing, pending/stale cleanup, funding
settlement, Demo Risk freshness, startup scope, and account-mode reconciliation.
Those consumers now reuse `config.exchanges` identity classifications. The
remaining direct mode branches are the documented UTA profile composition or
venue-native recovery exceptions above; this change does not claim
that those concrete clients have moved out of Core reachability.

The subsequent Entry identity pass also removes the generic consumers' direct
current-route comparisons. A small lookup over the existing
`ENTRY_EXECUTION_MINIMUM_PERMISSION` registry is shared by Entry authority,
compiler, Entry execution, Gateway and same-Plan Risk handling; demo account
matching and live exposure use the existing registered identity helpers. This
keeps the current Bitget Demo-only Entry policy while making any future route
admission an explicit registry change with its own Planner/Risk/Gateway tests.

The single-writer pass also removed the pending-close cleanup's direct
`orders.error_reason` SQL mutation. The owner-authorized release now calls a
repository CAS helper that changes only the expected cancelled Order reason;
the exchange/private-I-O boundaries above remain unchanged.

The follow-up production AST/literal-DML scan found no additional direct write
to a Core lifecycle table outside its documented owner. The only remaining
non-repository statements touching those facts are the materializer's
`fills.fee_source` provenance upgrade, the accounting owner's close-event
completion in `_commit_close_economics`, and two fixed-target historical Position
repairs. Their callers and predicates are explicit and bounded; no scheduler,
importer, optional Extension or UI path reaches them. Writes to settings,
Observation/Learning namespaces and audit-mirror metadata remain separate
owners, not alternate Order/Fill/Position lifecycle paths.

The merged optional-lifecycle repair closes the startup absence boundary without
moving private I/O. A missing Observation owner module resolves to `unknown`,
so Scheduler startup omits that optional cycle and reconciliation removes any
stale registration. Missing Learning control/lesson modules preserve Core job
guards and return an explicit unavailable advisory projection; Telegram adapter
import/configuration failure is non-blocking for lifespan startup. The focused
absence regressions (88 tests) and architecture fitness tests (19 tests) pass;
they do not grant account, exchange, deployment, or qualification authority.

The architecture-map seam is also merged at this checkpoint. The maintained
guide, map, Agent Contracts, and README D2 source/rendered pairs now identify
Candidate Planning Generations and immutable Entry Plans as the current entry
mainline, while retired Ranker/PaperTrader and learning evaluator/catch-up/
lifecycle jobs remain historical or absent. This changes no private-I/O owner
and is not runtime or exchange qualification evidence.

The execution-identity seam is also merged at this checkpoint. Generic Gateway
and capability consumers no longer import or maintain a second
mode-to-venue dictionary; they read `config.exchanges.EXECUTION_MODE_VENUES`.
The OKX Demo public sandbox loader now uses the managed generic CCXT factory,
while UTA profile composition and the Bitget-native private-I/O boundary remain
unchanged. The focused local tests (82 architecture/spec and 212
execution/registry/pipeline) cover this structural reuse; they do not prove
private-account containment, deployment, or exchange qualification.

The reconciliation identity seam is also merged at this checkpoint. Reconciliation
now derives its native Bitget versus non-Bitget UTA/CCXT partitions and live
subset from the shared execution identity registry. This removes a generic
mode-list duplicate without moving the documented Bitget native private-I/O
exception or changing account proof/materialization semantics. The 105-test
focused local evidence is structural and mocked only; it does not prove private
account containment, deployment, or exchange qualification.

The subsequent profile-generation repair keeps private-account evidence bound to
the selected identity: changing the configured Bitget profile invalidates both
registered Bitget demo and live UTA generations. Doctor's demo gate and native
TP/SL recovery now consume the registered environment set rather than separate
mode copies. No credentials were read or changed, and no account qualification is
implied.

The stale Gateway-lock test import was deleted after the implementation had
already been retired; no replacement lock owner or private-I/O path was added.
The active model healthcheck's stale OAuth-refresh wording was corrected to the
shared inference broker. OAuth/Codex/Databricks references that remain in source
are historical config identity/read surfaces or history documentation, not live
transport or credential-refresh callers.

The #3201 readiness seam changes no private-account reachability. Frontdoor's
explicit `/api/ready` handler uses a separate short-lived probe to the existing
backend `/_infra/livez` endpoint instead of consuming the ordinary proxy pool;
its retryable headers and the frontend retry scheduler are control-plane
transport only. No UTA, exchange client, credential, Risk, Gateway, settlement,
or private fill path moved or gained a caller. Local Frontdoor and focused
frontend communication checks, client Vitest, TypeScript, ESLint, API/settings
type checks, build/performance budgets, Ruff, docs governance, preflight, local
CI contract tests, and diff checks passed. The production pool's exact slot
owners, stream/finite isolation, Fedora runtime identity, and account
qualification remain open evidence gates.

The public capability seam was removed from the concrete private-I/O
allowlist earlier: `execution.venue_capabilities` delegates OKX demo symbol
discovery to the existing managed generic CCXT factory, and the duplicate
static method on `OKXClient` was deleted. That call loads only public sandbox
market metadata and always closes its loop-local client; no account
credentials, private endpoint, order mutation, or UTA identity is involved.
Correction to #3205: that merge added a probe helper but did not switch the
route caller, and its claimed allowlist reduction was false. The corrective
batch switches the caller; the concrete-import allowlist still has two entries.
No assertion was weakened to manufacture full architectural convergence.

The Model construction audit at `163b35a33` moves selected-endpoint client
construction and its API-key lookup from the Core router to `http_runtime`.
The existing Web/CLI entrypoints bind that constructor without creating clients
or resolving keys; the standalone harness script binds at its explicit main.
Core retains one cache, request-local model cloning, async lookup, health and
closure owner. The duplicate concrete-class registry is removed in favor of
existing access-method metadata; the implementation imports only the selected
HTTP adapter. Missing construction is unavailable, including when a cached
instance exists, and cannot fabricate a usable model. Core router/Web imports
are tested with the HTTP implementation and both SDKs absent. Configuration
identity hashing and management ingress still have separate raw-secret paths;
this is not full Core credential convergence or Scheme A physical movement.

The Model attempt-provenance audit at `163b35a33` found a second endpoint-name
resolver in the shared provider envelope. It inferred an alias from concrete
class names, current configuration, URL and model; even an unrelated sole
configuration could be selected. Normal cached providers already receive an
explicit endpoint binding from the router, and request-local model clones retain
that binding. The two candidate construction paths now bind the requested
endpoint before discovery/probing too. The inference envelope no longer scans
configuration or guesses an alias; an unbound direct test/transport instance
retains only its own label. This changes no stored history, pricing, fallback,
budget policy or writer. Model/Core concrete imports and credential ingress
remain separate, incomplete boundaries.

The Model identity audit at `2734b00f4` moves the existing credential-backed
configuration hash into the same HTTP implementation. Six stored/candidate
identity goldens across the three accepted protocols remain byte-identical.
Core reads opaque identity through the existing startup binding, never accepts
candidate secret overrides, and retains verification comparison and persistence.
An absent implementation cannot validate old evidence or persist new evidence;
health reports unavailable verification without rewriting the stored identity
or treating missing evidence as a proven mismatch. Candidate hashing stays in
the HTTP management path, whose broader credential ingress is still a separate
remaining boundary. No second hash implementation, credential cache or writer
is introduced.

The Model response audit at `25c8fb801` found Core startup, structured repair
and Settings ping decoding supplier usage/termination fields directly. HTTP
implementations now normalize completion confirmation, bounded finish reason
and optional output-token count into the existing response DTO. Missing counts
are not represented as measured zero. The three Core consumers preserve their
distinct success/content/retry policies, using only normalized facts. Existing
raw responses, cost usage and Core-owned fallback provenance are not rewritten.
No additional provider implementation or lifecycle is introduced; credential
management ingress remains a separate outstanding boundary.

The Model status audit at `f04b61e64` removes raw credential reads from Overview
and the Settings configuration projection. Both consume one sanitized status
operation bound to the existing HTTP implementation; unsupported historical
types and OAuth references remain inert. Missing implementation or a failed
secret-store read produces unknown status, not a false missing-key claim.
Overview's nullable setup status and Settings labels preserve that distinction;
no presence check becomes a usability probe. HTTP/CLI credential writes still
remain outside this completed read-only boundary.

Type-generation verification also exposed that direct script execution could
import a sibling editable checkout rather than this worktree. The exporter now
binds its own repository root before importing the app; its subprocess regression
compares the full schema to the current app. Regeneration then changes only the
nullable setup field, not unrelated optional routes.

## Pure market facts

The market-fact audit at `0f188b5694c21b38caa8b4c8177f953047f6d340` found
Core's causal-window, Planner and market-structure consumers importing the
concrete CCXT provider solely for bar durations. The existing timeframe mapping
and exact-ticker-venue validator now belong to the existing pure `data.providers`
module. Their AST is unchanged, as is all caller logic outside imports. CCXT
uses the shared duration function for trailing-bar closure; old helper exports
are deleted with no forwarding aliases. Fresh-process evidence proves causal
window handling and market-structure import remain available without the
concrete provider. Fetching consumers still using CCXT construction are a
separate, unfinished boundary; no data source, fallback, persistence or
qualification semantics change here.

## Startup filesystem repair ownership

The Doctor credential audit at `cb7237329bf7138f84ab1a825c4bee54e9541183`
found `_probe_credentials` reading raw Bitget Demo and selected-model secrets.
Doctor now consumes existing Model credential-status metadata and a boolean-only
selected-account presence query through the existing UTA composition binding.
Exchange composition reuses its profile reference map; it reads only the
requested account's fields, with no client creation, enumeration or writes.
Absent adapters, failed stores and malformed presence are unavailable rather
than fabricated missing/configured states. The existing Bitget Demo scope,
known present/partial/missing messages and independent inference probe remain.
Binding resolves no secrets; default-provider status uses only its configured
HTTP reference. Synthetic boundary and Doctor regressions do not prove actual
credential configuration, reachability, or runtime qualification.

The Telegram health-route audit at `54d525afc9d172918ec42690ab0bc11545a98f1d`
found Core's route module resolving `telegram.bot_token` and calling Telegram
directly even when the optional Telegram package was absent. The handler now
lives in `telegram.routes`, mounted by the existing Web composition only when
that package/route is available. The old Core route module is deleted. Handler
and decorator AST plus the full installed-app OpenAPI remain identical; only
an absent adapter removes its own endpoint. Token scope, HTTP request/response,
bot permissions and polling ownership are unchanged. Missing-package regression
and existing route/bot tests are isolated local evidence, not a live bot probe.

At `bcb88659ebece300db0a54a7802323e0d2f0a24e`, the next entrypoint audit found
two callers of `gateway.git_hooks.remove_managed_git_hooks`: backend lifespan
and explicit `diagnostics.doctor.apply_fixes`. The helper resolves the shared
Git hooks directory, removes only standalone managed legacy restart hooks and
their debounce stamp, and preserves foreign/wrapped hooks. This is historical
filesystem repair, not ordinary runtime reconciliation. The implicit lifespan
caller is removed under #3134's no-hidden-startup-repair boundary; explicit
`doctor --fix` retains the existing bounded implementation and the normal
Doctor probe remains read-only. An architecture regression checks the sole
production caller, while real temporary-repository tests cover idempotency and
foreign-hook preservation. No actual user or runtime hook files were changed.

Read-only Fedora inspection during this batch found checkout
`6749c31b62a36a0497659cf25608025f8ab4218f`, clean, with Gateway active/running
under PID 106503 and invocation `9f00a439f9964455b6c00f189665083f`. These facts
prove neither current program deployment nor schema/provider/settlement health.
