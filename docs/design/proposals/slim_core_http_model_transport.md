---
id: slim-core-http-model-transport
status: accepted
github_issue: 3134
related_contracts:
  - ../contracts/llm_inference_harness_contract.md
---

# Official HTTP model transport

This D2 transport convergence implements the owner-approved
[#3134](https://github.com/iridyne/aletheia/issues/3134) HTTP-only model boundary.
The first bounded change replaces the generic OpenAI Chat Completions and
Responses adapters' handwritten HTTP dispatch with the official OpenAI SDK.
It does not add another provider framework or a second inference entry point.

The existing ModelProvider envelope remains responsible for logical-call
identity, measured usage, structured repair and explicit fallback. The existing
loop-owned HTTP client remains responsible for network/proxy policy and closure.
SDK automatic retries are disabled; only the existing explicit optional-field
compatibility retries remain. SDK status errors pass through the existing
credential-scrubbing HTTP error boundary. An endpoint without a credential must
not inherit an environment credential or transmit a fabricated bearer token.
Model discovery stays bounded and manual model selection stays valid.

Use the HTTPX-compatible official OpenAI 2.x release line (minimum 2.54.0),
verified against its [published dependency metadata](https://pypi.org/pypi/openai/2.54.0/json).
OpenAI 3.x uses HTTPX2; changing the application's shared network stack is not
part of this batch. Do not introduce a parallel SDK-owned network-policy stack.

The next bounded change applies the same transport boundary to generic
Anthropic Messages and its catalogue using official Anthropic 0.125.0, the
HTTPX-compatible release line below 1.0, as verified by its
[published dependency metadata](https://pypi.org/pypi/anthropic/0.125.0/json).
Explicit x-api-key or bearer selection remains unchanged, including the existing
bearer endpoint temperature omission. SDK credential/profile auto-discovery,
ambient headers, retries and content debug logs must not become new authorities.
The provider continues borrowing the existing loop-owned HTTP client.

The accepted program still requires retirement of
CLI/OAuth/native proprietary transports and explicit endpoint/model routing.
This bounded transport change does not claim those remaining boundaries are
complete. Existing Databricks-specific transport remains outside this batch.
No stored endpoint, secret, routing choice, runtime or trading authority changes.

## CLI transport retirement

The same accepted HTTP-only boundary removes Codex, Claude and Pi subprocess
transports, their shared process harness and Codex device-login owner. Their
registry entries, constructors, API controls, model discovery and frontend
activation controls are removed together, without forwarding aliases.
Operator CLI commands that configure HTTP endpoints are not model transports
and remain available.

Historical provider configuration remains decodable, but an unsupported type
cannot construct a provider, probe, authenticate, renew verification, receive
new routing assignments or make diagnostics report a configured model. Merely
viewing its configuration or health must not resolve credential references.
An explicit retired type cannot be reinterpreted as HTTP because its profile
identifier happens to match a supported adapter. Historical routing references
remain visible and unavailable until the owner explicitly selects a supported
endpoint; no automatic rebinding, credential migration or config deletion occurs.

The bounded consumer inventory includes router/verification, settings and
Codex routes, bootstrap setup detection, doctor credential diagnostics, model
configuration CLI, settings routing and inference-admission controls. Tests of
the deleted subprocess implementations are replaced by retirement containment
and fail-closed tests; HTTP and generic startup/fallback behavior remain covered.
OAuth and Databricks transports are separate remaining convergence work, not
implicit exceptions to the accepted final HTTP-only product scope.

## Native and subscription transport retirement

Remove the fixed-origin OpenAI/Anthropic native adapters, subscription login
managers, shared PKCE/token/callback server and router token-refresh owner.
Model calls retain the existing generic HTTP adapters and official SDK boundary;
there is no automatic conversion of a subscription identity to an API endpoint.
The async router uses the same constructor/cache owner off the event loop;
there is no async token-refresh constructor branch. Existing non-secret identity
inputs remain stable; historical OAuth credential fingerprints are no longer
resolved, so verification that depended on them fails closed on identity mismatch.
Native-only catalogue heuristics and SSE parsers are retired with their owner;
the existing generic ModelProvider streaming envelope remains unchanged.

Remove OAuth API/UI controls, including singleton integration mutation. Config
and health reads do not resolve historical OAuth references, even if such a
reference is attached to an HTTP profile. HTTP credential deletion is bounded
to its configured API credential; historical subscription tokens are neither
read nor deleted. Stored fields remain decode/read provenance, not refresh,
login, fallback or activation authority. This narrows access and performs no
credential migration, storage change or runtime mutation.

Reuse the unsupported-type retirement guards for native configurations and
extend containment tests to login, callback and refresh owners. Retain generic
HTTP streaming/usage and historical economic-attribution tests; remove only
tests of the deleted native/OAuth producers. Databricks and explicit endpoint
model-pair routing remain separate approved convergence work.

## Proprietary workspace transport retirement

The current Databricks adapter independently constructs workspace serving URLs,
catalogue responses and OpenAI/Anthropic wire payloads. Its consumers are the
model router, settings/probe routes and provider forms; no Core caller requires
this concrete type. Retire the adapter, registry/constructor branches, workspace
mutation fields and dormant standalone form alongside their producer-only tests.
Do not recreate serving-endpoint routing as a generic protocol or forwarding shim.

Historical Databricks configuration and credential references remain inert and
are not migrated, deleted or automatically rebound. A manually configured HTTP
endpoint uses an explicit base URL, one supported protocol and an owner-supplied
credential; no model, URL or credential is inferred from a workspace. Workspace
fields cannot supply a missing HTTP base URL or be silently accepted by current
configuration/probe requests. Config decoding/read provenance remains available.
Existing HTTP discovery, manual model IDs, bounded verification, cache ownership
and inference/fallback behavior remain covered. Endpoint/model-pair assignments
and the final provider-neutral credential boundary remain separate program work.

Verification uses a real SDK with an in-memory HTTP transport: exact protocol
paths and payloads, missing optional fields, status/error redaction, no SDK
retry, cancellation, no environment credential fallback, raw usage preservation
and loop-owned client closure. Existing inference/fallback tests must retain
their observable behavior; no real inference request is an acceptance fixture.

## Explicit fallback ownership

The accepted ordered-fallback boundary retires live catalogue expansion and
caller/model-name-driven candidate injection. Discovery is a Settings operation,
not inference routing authority. An empty configured fallback list means only
the selected model; an enabled unrelated endpoint is not a fallback candidate.
Structured output, including trading callers, uses the same explicit order.
Remove the JSON preference flag and its product-name policy rather than leaving
a dormant switch or replacement heuristic. Retain explicit cross-endpoint
entries, bounded attempts, disabled-fallback behavior, cooldown, broker admission,
budget enforcement, cancellation and structured repair/exclusion semantics.
This does not reassign stored routes or claim endpoint/model-pair migration.

## Current configuration entrypoint convergence

Retire the fixed-provider `POST /settings/model` migration endpoint and its
unused frontend client method. Current Settings uses profile create/update and
the separate routing endpoint. The retired endpoint must not bypass bounded
verification or assign routing roles while writing a profile. Remove its
role-assignment request flags and reuse the existing profile-field validation
for the shared internal mutation function. Preserve current verification,
credential rollback, cache/broker invalidation and redacted audit behavior.
Historical configuration remains readable; there is no forwarding endpoint or
automatic mutation. Endpoint/model-pair routing remains a separate convergence.

## Explicit task-tier model selection

Replace the provider-only fast/deep fields with explicit endpoint/model pairs.
The existing Default role and explicit endpoint calls retain their current
preferred-model behavior; neither supplies a fallback for an unassigned current
fast/deep tier. Unknown tiers fail rather than selecting Default. Historical
provider-only tier configuration is decoded deterministically to the endpoint
and model it previously resolved, without network, credential reads or an
automatic config rewrite. Missing/retired historical targets remain visible
and unavailable; current empty assignments stay empty. No runtime alias for the
old fields remains.

The router reuses its existing endpoint instance/cache and the existing
request-local model clone behavior. Selecting two models on one endpoint must
not mutate that shared instance. Helpers preserve the selected pair through
dispatch while broker capacity, budget lookup and Planner provider provenance
continue to identify the endpoint. Existing explicit endpoint fallback lists,
timeouts, structured repair and physical-attempt accounting remain unchanged.

Settings exposes endpoint and model selection together, with manual model IDs
and optional existing catalogue discovery. Assigning a nonempty tier performs
the existing bounded usability probe for that exact model and rechecks its
configuration identity against the latest committed config before a targeted
role write. Probe failure cannot change roles or the endpoint's preferred model.
Successful assignment audits the pair and probe identity; it does not claim that
the endpoint's preferred-model verification proves a different model. Existing
probe persistence may update endpoint verification only on an exact identity
match. Clearing a role and deleting its endpoint leave the tier unassigned;
assignment never enables an endpoint or changes credential scope.

## Candidate credential confinement

Discovery, candidate verification and profile-update verification cannot lend
a saved endpoint's credential to an overridden URL, protocol, provider type or
authentication scheme. Such a candidate must supply its own explicit key;
otherwise reject before Secret Store resolution or a network probe. Unchanged
endpoints may reuse their own configured reference, including when selecting a
different model. Explicit candidate keys do not trigger an unused stored-key
lookup. Anonymous endpoints without a configured reference remain anonymous.
This restores #3134's configured-endpoint scope; it does not migrate credentials
or claim that the remaining Model/Core import boundary has been converged.

## Physical HTTP Extension boundary

The accepted #3134 Scheme A target places concrete HTTP SDK transports,
endpoint construction/credential-backed identity and HTTP/CLI credential ingress
under `extensions/model_endpoints/`. Existing Web/CLI entrypoints compose them;
the Model Port, normalized DTOs, router, broker and explicit fallback remain Core.
Removing the Extension package leaves Core status/health/routing importable with
explicit unavailable results, not a replacement transport or credential fallback.
Old implementation paths are removed in the same change with no forwarding alias.
The existing distribution includes the new source directory; there is no second
distribution, plugin loader, runtime lifecycle, schema migration or credential
scope change. Other Mixed packages require their own proven dispositions; this
boundary does not authorize moving a whole package solely by its current name.
