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

Verification uses a real SDK with an in-memory HTTP transport: exact protocol
paths and payloads, missing optional fields, status/error redaction, no SDK
retry, cancellation, no environment credential fallback, raw usage preservation
and loop-owned client closure. Existing inference/fallback tests must retain
their observable behavior; no real inference request is an acceptance fixture.
