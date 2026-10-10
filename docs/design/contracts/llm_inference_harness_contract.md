# LLM Inference Harness Contract

## Purpose and current scope

This Contract is the single current authority for how Aletheia identifies,
controls, validates, and observes an LLM inference. Tokenomics remains the
physical-consumption ledger; the harness adds the logical reason and policy
envelope around that consumption.

The harness does not grant trading authority. It does not change Risk,
Execution, exchange, credential, hard-limit, or live-unlock behavior.

## Entry points

Application inference enters through `llm_complete()`, `llm_structured()`, or
`llm_stream()` in `core/aletheia/agents/helpers.py`. Startup and Provider Settings
connectivity probes enter the same logical-call contract before using the
broker directly. Provider adapters remain the physical network boundary.

Adapters also normalize completion evidence, bounded finish reason and optional
output-token count into `ModelResponse`; Core startup, Settings ping and structured
repair do not inspect supplier wire fields. Missing/malformed counts remain
unknown in the DTO. The existing consumer policies stay distinct: startup accepts
text or positive output-token evidence, ping accepts text or confirmed completion,
and structured repair still requires valid content and its existing retry policy.
Raw usage and responses remain unchanged for existing cost/provenance handling.

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

Credential-presence projections are resolved by the Model implementation for the
configured endpoint and expose only presence/masking metadata. These Core and
Overview projections never read credential values. Missing implementation or secret-store failure is
unknown/unavailable, not proof that no key is configured; presence is not inference
usability. Settings and Overview preserve that distinction.

Settings creates or updates HTTP profiles through `/settings/model-profiles`
and assigns roles separately through `/settings/model-routing`. The fixed-provider
`POST /settings/model` compatibility endpoint and combined role-assignment flags
are retired, with no forwarding alias. Profile writes retain the existing bounded
verification, secret rollback, cache/broker invalidation and redacted audit path.

Every production helper call declares a literal registered `caller` identity.
An executable AST claim fails when application code omits that identity or
uses a caller that is absent from `CALLER_SPECS`.
Application-level physical `ModelRequest` construction is checked separately;
each such request must carry the logical envelope into provider tracing.

## Endpoint/model routing

Fast and Deep each own an explicit `(endpoint_id, model_id)` selection. They may
select different models on one HTTP endpoint without changing that endpoint's
preferred model, credentials, activation, or another tier. A missing selection
does not borrow Default; unknown tiers fail closed. Default and explicit untiered
endpoint overrides retain their existing preferred-model semantics.

Historical `fast_provider` / `deep_provider` fields decode into the pair the old
configuration resolved, without modifying the input or writing a migration.
Unresolved historical identities remain unavailable. These fields are not current
runtime aliases and are rejected by routing requests. Mixed old/new tier fields
are invalid. Clearing a current selection leaves it empty.

Assignment verifies the exact endpoint/model through the existing bounded probe,
then rechecks its identity under the existing configuration mutation lock. Probe
failure or identity drift cannot partially assign roles or change the endpoint's
preferred model. Endpoint verification for a different preferred model is not
proof of the selected model; successful routing probes retain their own audit
metadata. Discovery supplies suggestions, never an implicit assignment.

Helpers preserve the selected model through dispatch while broker admission,
fallback configuration and budget attribution retain the endpoint identity.
Per-call model selection never mutates the cached preferred-model provider.
Missing inference configuration cannot authorize new planning; recovery of an
already validated hypothesis remains compilation-only and needs no model slot.
Caller-owned unavailable outcomes remain intact: no inferred lessons or market
reads are fabricated, and the deterministic daily review needs no model call.

## Logical call envelope

One `InferenceCall` is created before provider admission and remains stable
across structured repair retries and model fallback attempts.

| Layer | Persisted content-free facts |
| --- | --- |
| Identity | logical call ID, caller ID, AgentRun/runtime session identities when available |
| Prompt | template ID/version, static-system SHA-256, rendered request SHA-256, character counts |
| Context | selected adapter ID and bounded model-visible context manifest |
| Tools | model tool-interface type, exact allowed IDs when an interface exists, allowlist SHA-256 |
| Environment | permission mode, paper execution mode, live state, logical agent/run identities, and explicitly bound evaluation/opportunity/decision/proposal IDs when already known |
| Request | requested provider, temperature, max tokens, fallback policy |
| Output | output mode, schema identity/SHA-256, parse-valid and schema-valid outcomes; on terminal structured failure only, a fixed response-shape enum plus bounded schema error class and first failing schema path |
| Evaluation | bounded pair, arm, fixture, and optional modality IDs when the call belongs to an offline evaluation |

Prompt text, response text, source bodies, credentials, and secrets are not
stored in this envelope. Structured-failure diagnostics never store model
content, values, model-supplied keys, lengths, or content hashes. The only names
they carry are the caller schema's own declared properties in the first failing
path (`schema_error_path`, e.g. `$.risk_intent.posture`), truncated at the first
segment the schema does not declare.
Economic attribution accepts only the fixed identity keys documented here;
unregistered keys are discarded before provider or ledger code can observe them.

## Prompt control

Prompts remain caller-owned rather than being merged into one global prompt.
The central registry supplies a stable template identity and version; runtime
hashes expose an unversioned rendered change without retaining its content.

The context adapter is independently identified. `none` preserves the caller
system prompt byte-for-byte. `minimal-v1` is default-off and renders only its
bounded truthful manifest. `technical-context-v1` leaves the static system
prompt unchanged, identifies the Market Observer's dynamic closed-bar block,
and requires at least one linked technical artifact ID. Text cannot grant a
permission.

## Tool control

Provider-native tool calling is unavailable to registered callers in this
Contract. Those callers therefore record `provider-native-tools-none-v1` and an
empty allowlist.

`operator.llm_session.turn` is the sole text-mediated command interface. Each
turn records the exact caller-supplied command allowlist. A parsed command still
passes the existing Agent Shell registry, operator permission check,
worker/global permission check, and confirmation gate before execution. Its
command audit row records the originating logical call ID. Prompt text never
replaces these runtime checks.

Agent runtime tool telemetry also carries the most recent task-local logical
call ID when the tool event follows an inference, allowing the tool-cost ledger
to expose that association without changing permission decisions.

## Environment awareness

The envelope captures non-secret runtime facts separately from the subset made
model-visible by a context adapter. This distinction is load-bearing:
`environment` means what the runtime knew, while `context_manifest` means what
the adapter declared visible to the model. Missing facts remain absent.

Canonical entry inference uses `agent_shell.entry_planner`. Its grounded
EntryAnalysis input identifies the durable Candidate planning generation; an
environment ContextArtifact links `planning-generation:<id>` from
`candidate_planning_generations` to the exact logical call, with the analysis
cutoff and rendered model-view character count. The call also binds
`planning_generation_id` as explicit causal attribution, so the logical call's
environment metadata names the generation without a join. This is not a PiDecision or
Proposal identity. Downstream entities not yet created remain absent, and
`run_id` exists only when an actual AgentRun is bound.

Historical System Operator evaluation, Opportunity, and `signal_pending`
PiDecision attribution remains readable as observations of the retired entry
path, not current Planner attribution or permission to infer again. Neither
row order nor time proximity supplies missing identities.

Selected News, Learning, technical, and environment artifacts are recorded as
content-free `ContextArtifact` rows linked to the exact logical call. Technical
facts retain their bundle fingerprint and algorithm identity in the stable ID
and source without storing the rendered body. Evaluation rows also carry pair,
arm, and fixture identities. A zero-character or absent artifact is not evidence
that the model used it.

## Output validation

`llm_structured()` first requires a JSON object and then validates it against
the caller's declared schema. Pydantic schemas use `model_validate` first, so
their normalizers run, and the bounded JSON-Schema subset then checks the
normalized `model_dump`, which is the object the caller receives; lightweight
schema shims use that subset alone. Schema-invalid objects enter the existing
bounded repair loop with the same model and never become a successful logical
outcome; only empty or non-JSON output excludes the model that produced it from
the repair attempt. Non-critical default fallback remains explicit
and the logical call remains an error.

Reasoning control is caller-owned and default-off. `llm_structured()` preserves
the existing provider request shape unless a caller explicitly selects a
`reasoning_effort`. Market Observer and News Analyzer select `none` for bounded
schema reads; the canonical Entry Planner selects `medium`.
OpenAI-compatible adapters send that field only when
selected. If an endpoint returns a 400/422 that explicitly identifies
`reasoning_effort` as unknown or unsupported, the adapter retries that read-only
completion exactly once without the optional field; unrelated client errors
remain failures. Soft prompt text such as `/no_think` is not treated as proof
that provider-side reasoning was disabled.

## Durable state and consumers

| State | Purpose | Consumer |
| --- | --- | --- |
| `llm_inference_calls` | one content-free row per logical call | harness audit, caller coverage, context-effectiveness evaluation |
| `runtime_cost_events` (`event_type='llm'`) | one row per physical provider attempt | token/cost/reliability analysis |
| `llm_context_artifacts` | selected context identities and rendered sizes | context audit and paired evaluation |
| `llm_context_pair_reviews` | aggregate blinded owner scores only | paired context evidence summary |
| `agent_tool_calls.inference_call_id` | task-local tool association | AgentRun/tool-cost audit |
| `agent_shell_commands.inference_call_id` | exact operator-turn command origin | command audit |

All physical attempts for a logical call use its `logical_call_id` as
`runtime_cost_events.call_id` and claim monotonically increasing attempt
indexes. This preserves real retry/fallback consumption while making the
logical unit queryable.

When the caller has explicitly bound existing causal entities,
`runtime_cost_events.opportunity_id`, `decision_id`, and `proposal_id` copy only
those IDs. Missing or not-yet-created downstream entities stay `NULL`. The same
content-free attribution is retained in the logical call's environment metadata,
so a successful model call with no Proposal can be distinguished from an
unattributable call without storing prompt or response content. Telemetry lookup
failure is fail-open for the business call and records no database exception text.

## Failure and recovery semantics

- Model fallback uses only the selected model and its explicit ordered
  configuration, within the existing per-request attempt bound. Empty fallback
  configuration does not discover candidates. Catalogue results and unrelated
  enabled endpoints never supply implicit candidates; caller IDs and model
  product names cannot add or promote them, including for structured output.
  Explicit cross-endpoint fallback, cooldown and structured repair remain
  subject to the same broker, budget and cancellation boundaries.
- Measurement writes are best-effort and never authorize or block a trade.
- Provider/budget/timeout/parse/schema failure finalizes the logical row as
  `error`; it is never relabelled as success because a default object exists.
- External task cancellation finalizes an already-started logical structured call
  as `error` with content-free `error_type=cancelled`, then re-raises the original
  `CancelledError`. Cancellation is never converted into a default object or an
  availability tuple.
- Terminal parse/schema failures may add only allowlisted, content-free enums to
  `metadata_json.structured_failure`: response shape, schema error class and
  schema error path.
  These facts diagnose a future replay without normalizing
  or accepting the failed response.
- A caller may disable model fallback. Paired model evaluations do so to keep
  provider/model identity frozen.
- Existing database upgrades are additive. Schema creation or upgrade refuses
  to cross an active caller transaction.
- Missing provider configuration produces an honest unavailable result; it
  does not create a synthetic effectiveness conclusion.

## Current caller coverage matrix

The following block is rendered from the executable registry and checked by
the Contract spec.

<!-- BEGIN GENERATED CALLER MATRIX -->
| Caller ID | Prompt template | Version | Output | Model tool interface |
| --- | --- | --- | --- | --- |
| `agent_shell.entry_planner` | `agent_shell.entry_planner` | `v1` | `structured` | `none` |
| `agent_shell.entry_planner.v2` | `agent_shell.entry_planner` | `v2` | `structured` | `none` |
| `agent_shell.general_query` | `agent_shell.general_query` | `v1` | `text` | `none` |
| `agent_shell.intent_classification` | `agent_shell.intent_classification` | `v1` | `structured` | `none` |
| `agent_shell.portfolio_review` | `agent_shell.portfolio_review` | `v1` | `structured` | `none` |
| `agent_shell.synthesis` | `agent_shell.synthesis` | `v1` | `text` | `none` |
| `dreaming.lesson_extraction` | `dreaming.lesson_extraction` | `v1` | `structured` | `none` |
| `dreaming.nightly_consolidation` | `dreaming.nightly_consolidation` | `v1` | `text` | `none` |
| `insight_synthesizer.insight` | `insight_synthesizer.insight` | `v1` | `text` | `none` |
| `learning.hypothesis_causal_red_team` | `learning.hypothesis_causal_red_team` | `v1` | `structured` | `none` |
| `lesson_reviewer.judge` | `lesson_reviewer.judge` | `v1` | `structured` | `none` |
| `operator.llm_session.turn` | `operator.llm_session.turn` | `v1` | `text` | `typed-command-text` |
| `post_trade_reviewer.review` | `post_trade_reviewer.review` | `v1` | `text` | `none` |
| `provider_settings.candidate_probe` | `provider_settings.candidate_probe` | `v1` | `text` | `none` |
| `provider_settings.ping` | `provider_settings.ping` | `v1` | `text` | `none` |
| `research_worker.claims` | `research_worker.claims` | `v1` | `text` | `none` |
| `research_worker.lens` | `research_worker.lens` | `v1` | `text` | `none` |
| `research_worker.methodology` | `research_worker.methodology` | `v1` | `text` | `none` |
| `research_worker.opportunities` | `research_worker.opportunities` | `v1` | `text` | `none` |
| `research_worker.themes` | `research_worker.themes` | `v1` | `text` | `none` |
| `startup_gate.provider_probe` | `startup_gate.provider_probe` | `v1` | `text` | `none` |
<!-- END GENERATED CALLER MATRIX -->

## Observable result

For any registered production inference, an owner can determine why it ran,
which versioned prompt identity and context adapter it used, which model tool
surface it could access, which output schema it had to satisfy, how many
physical attempts it consumed, whether fallback occurred, and whether parsing
and schema validation succeeded—without opening a raw prompt archive. For a
canonical Entry Planner call, the owner can identify the exact planning
generation through its ContextArtifact while correctly observing that a
Proposal may not exist. Historical entry attribution retains its original meaning.

## Invariants and trust boundaries

1. No stored prompt, response, source body, credential, or secret.
2. Runtime permission code remains authoritative; prompt text has zero
   permission effect.
3. Risk approval and Execution Gateway remain mandatory and unchanged.
4. Physical attempts are never collapsed or deleted to improve apparent cost.
5. A context or prompt change is attributable by identity/version/hash and must
   be evaluated before default-on use when it can change decisions.

## Executable evidence

- `tests/specs/spec_llm_inference_harness.py`
- `tests/test_llm_inference_harness.py`
- `tests/test_economic_cli.py`
- `tests/test_inference_broker.py`
- `tests/test_model_fallback.py`
- `tests/test_retired_model_providers.py`
- `tests/test_model_profiles.py`

Research rationale and dated evidence remain non-normative in
[`../../research/llm_harness/README.md`](../../research/llm_harness/README.md).

## Known gaps

The context-effectiveness harness (`learning-context-b1`,
`technical-context-check`, `context-ablation` and their score/summary
commands) was retired together with the Market Observer: its control prompt
and response schema lived in that module, so no pair could be built. Restoring
prompt-ablation research requires a new design against a live prompt, not a
repair of the removed harness.
