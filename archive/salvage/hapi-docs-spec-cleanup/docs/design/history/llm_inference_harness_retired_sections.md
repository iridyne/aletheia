# LLM Inference Harness Contract — retired and superseded text

> **History (moved 2026-10-10).** Retired or superseded design kept as evidence; not current authority. See [design history](README.md).

Moved verbatim from [`llm_inference_harness_contract.md`](../contracts/llm_inference_harness_contract.md)
when it was rewritten into the numbered Contract form. Current behavior lives only in that Contract.

## Former transport paragraph with its progress disclaimer

Generic OpenAI Chat Completions / Responses and Anthropic Messages dispatch and
model discovery use their official SDKs over the existing loop-owned HTTP client. SDK automatic
retries and ambient credential/header inheritance are disabled; the existing
explicit optional-field and model fallback policies remain authoritative.
Anthropic profile/federation credential resolution is not an alternative to the
explicit endpoint credential. Its x-api-key/bearer choice and current bearer
temperature omission remain adapter-owned.
SDK content-bearing debug logs are disabled, status failures retain the shared
credential-scrubbing boundary, and cancellation propagates. This bounded
[transport convergence](../proposals/slim_core_http_model_transport.md) does not
claim completion of the remaining HTTP-only endpoint/routing retirement work.

## Retired model transports (former Entry points paragraph)

Codex, Claude and Pi subprocess model transports, fixed-origin native adapters,
Databricks workspace/serving transport, subscription OAuth and Codex device-login
routes are retired. Their historical provider configuration and routing references
remain readable, but unsupported types cannot construct or probe a provider,
renew verification, resolve credentials for a config/health read, or receive
new routing assignments. An explicit unsupported type is never reinterpreted
from its profile ID. Settings shows an assigned retired type as unavailable;
the owner can clear its role or explicitly select a supported endpoint.
Operator CLI commands for configuring HTTP endpoints remain distinct from
retired subprocess model transports. HTTP config/health reads and profile deletion
never resolve or delete historical OAuth credentials, even on an HTTP profile.
Current model configuration/probe requests reject obsolete workspace fields;
only an explicit HTTP base URL configures a current endpoint. Historical workspace
identity fields remain decode/read provenance, never routing or credential scope.

## Former Market Observer wording (Market Observer is retired)

The context adapter is independently identified. `none` preserves the caller
system prompt byte-for-byte. `minimal-v1` is default-off and renders only its
bounded truthful manifest. `technical-context-v1` leaves the static system
prompt unchanged, identifies the Market Observer's dynamic closed-bar block,
and requires at least one linked technical artifact ID. Text cannot grant a
permission.

Reasoning control is caller-owned and default-off. `llm_structured()` preserves
the existing provider request shape unless a caller explicitly selects a
`reasoning_effort`. Market Observer and News Analyzer select `none` for bounded
schema reads; the canonical Entry Planner selects `medium`.

## Former observable-result summary

For any registered production inference, an owner can determine why it ran,
which versioned prompt identity and context adapter it used, which model tool
surface it could access, which output schema it had to satisfy, how many
physical attempts it consumed, whether fallback occurred, and whether parsing
and schema validation succeeded—without opening a raw prompt archive. For a
canonical Entry Planner call, the owner can identify the exact planning
generation through its ContextArtifact while correctly observing that a
Proposal may not exist. Historical entry attribution retains its original meaning.

## Retired context-effectiveness harness (former Known gaps)

The context-effectiveness harness (`learning-context-b1`,
`technical-context-check`, `context-ablation` and their score/summary
commands) was retired together with the Market Observer: its control prompt
and response schema lived in that module, so no pair could be built. Restoring
prompt-ablation research requires a new design against a live prompt, not a
repair of the removed harness.
