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
`llm_stream()` in `aletheia/agents/helpers.py`. Startup and Provider Settings
connectivity probes enter the same logical-call contract before using the
broker directly. Provider adapters remain the physical network boundary.

Every production helper call declares a literal registered `caller` identity.
An executable AST claim fails when application code omits that identity or
uses a caller that is absent from `CALLER_SPECS`.
Application-level physical `ModelRequest` construction is checked separately;
each such request must carry the logical envelope into provider tracing.

## Logical call envelope

One `InferenceCall` is created before provider admission and remains stable
across structured repair retries and model fallback attempts.

| Layer | Persisted content-free facts |
| --- | --- |
| Identity | logical call ID, caller ID, AgentRun/runtime session identities when available |
| Prompt | template ID/version, static-system SHA-256, rendered request SHA-256, character counts |
| Context | selected adapter ID and bounded model-visible context manifest |
| Tools | model tool-interface type, exact allowed IDs when an interface exists, allowlist SHA-256 |
| Environment | permission mode, paper execution mode, live state, logical agent/run identities |
| Request | requested provider, temperature, max tokens, fallback policy |
| Output | output mode, schema identity/SHA-256, parse-valid and schema-valid outcomes; on terminal structured failure only, a fixed response-shape enum plus bounded schema error class |
| Evaluation | bounded pair, arm, and fixture IDs when the call belongs to an offline evaluation |

Prompt text, response text, source bodies, credentials, and secrets are not
stored in this envelope. Structured-failure diagnostics never store model
content, values, arbitrary root keys, field names, lengths, or content hashes.

## Prompt control

Prompts remain caller-owned rather than being merged into one global prompt.
The central registry supplies a stable template identity and version; runtime
hashes expose an unversioned rendered change without retaining its content.

The context adapter is independently identified. `none` preserves the caller
system prompt byte-for-byte. `minimal-v1` is default-off and renders only its
bounded truthful manifest. Text cannot grant a permission.

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

Selected News, Learning, and environment artifacts are recorded as content-free
`ContextArtifact` rows linked to the exact logical call. Evaluation rows also
carry pair, arm, and fixture identities. A zero-character or absent Learning
artifact is not evidence that the model used Learning.

## Output validation

`llm_structured()` first requires a JSON object and then validates it against
the caller's declared schema. Pydantic schemas use `model_validate`; lightweight
schema shims use the bounded JSON-Schema subset implemented by the helper.
Schema-invalid objects enter the existing bounded repair loop and never become
a successful logical outcome. Non-critical default fallback remains explicit
and the logical call remains an error.

## Durable state and consumers

| State | Purpose | Consumer |
| --- | --- | --- |
| `llm_inference_calls` | one content-free row per logical call | harness audit, caller coverage, context-effectiveness evaluation |
| `runtime_cost_events` (`event_type='llm'`) | one row per physical provider attempt | token/cost/reliability analysis |
| `llm_context_artifacts` | selected context identities and rendered sizes | context audit and paired evaluation |
| `llm_context_pair_reviews` | aggregate blinded owner scores only | Learning B evidence summary |
| `agent_tool_calls.inference_call_id` | task-local tool association | AgentRun/tool-cost audit |
| `agent_shell_commands.inference_call_id` | exact operator-turn command origin | command audit |

All physical attempts for a logical call use its `logical_call_id` as
`runtime_cost_events.call_id` and claim monotonically increasing attempt
indexes. This preserves real retry/fallback consumption while making the
logical unit queryable.

## Failure and recovery semantics

- Measurement writes are best-effort and never authorize or block a trade.
- Provider/budget/timeout/parse/schema failure finalizes the logical row as
  `error`; it is never relabelled as success because a default object exists.
- Terminal parse/schema failures may add only allowlisted, content-free enums to
  `metadata_json.structured_failure`: response shape and schema error class.
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
| `agent_shell.general_query` | `agent_shell.general_query` | `v1` | `text` | `none` |
| `agent_shell.intent_classification` | `agent_shell.intent_classification` | `v1` | `structured` | `none` |
| `agent_shell.portfolio_review` | `agent_shell.portfolio_review` | `v1` | `structured` | `none` |
| `agent_shell.synthesis` | `agent_shell.synthesis` | `v1` | `text` | `none` |
| `dreaming.lesson_extraction` | `dreaming.lesson_extraction` | `v1` | `text` | `none` |
| `dreaming.nightly_consolidation` | `dreaming.nightly_consolidation` | `v1` | `text` | `none` |
| `equity_observer.market_read` | `equity_observer.market_read` | `v1` | `structured` | `none` |
| `evaluation.learning_b.control` | `market_observer.market_read` | `v1` | `structured` | `none` |
| `evaluation.learning_b.treatment` | `market_observer.market_read` | `v1` | `structured` | `none` |
| `insight_synthesizer.insight` | `insight_synthesizer.insight` | `v1` | `text` | `none` |
| `lesson_reviewer.judge` | `lesson_reviewer.judge` | `v1` | `text` | `none` |
| `market_observer.market_read` | `market_observer.market_read` | `v1` | `structured` | `none` |
| `news_analyzer.analysis` | `news_analyzer.analysis` | `v1` | `structured` | `none` |
| `operator.llm_session.turn` | `operator.llm_session.turn` | `v1` | `text` | `typed-command-text` |
| `post_trade_reviewer.review` | `post_trade_reviewer.review` | `v1` | `text` | `none` |
| `prediction_observer.market_read` | `prediction_observer.market_read` | `v1` | `structured` | `none` |
| `provider_settings.candidate_probe` | `provider_settings.candidate_probe` | `v1` | `text` | `none` |
| `provider_settings.ping` | `provider_settings.ping` | `v1` | `text` | `none` |
| `research_agent.note` | `research_agent.note` | `v1` | `text` | `none` |
| `research_worker.claims` | `research_worker.claims` | `v1` | `text` | `none` |
| `research_worker.lens` | `research_worker.lens` | `v1` | `text` | `none` |
| `research_worker.methodology` | `research_worker.methodology` | `v1` | `text` | `none` |
| `research_worker.opportunities` | `research_worker.opportunities` | `v1` | `text` | `none` |
| `research_worker.themes` | `research_worker.themes` | `v1` | `text` | `none` |
| `risk_critic.review` | `risk_critic.review` | `v1` | `text` | `none` |
| `startup_gate.provider_probe` | `startup_gate.provider_probe` | `v1` | `text` | `none` |
| `stock_observer.market_read` | `stock_observer.market_read` | `v1` | `structured` | `none` |
| `system_operator.assess_opportunity` | `system_operator.assess_opportunity` | `v1` | `structured` | `none` |
| `system_operator.debate_bear` | `system_operator.debate_bear` | `v1` | `structured` | `none` |
| `system_operator.debate_bull` | `system_operator.debate_bull` | `v1` | `structured` | `none` |
| `system_operator.reassess_direct_skip` | `system_operator.reassess_direct_skip` | `v1` | `structured` | `none` |
<!-- END GENERATED CALLER MATRIX -->

## Observable result

For any registered production inference, an owner can determine why it ran,
which versioned prompt identity and context adapter it used, which model tool
surface it could access, which output schema it had to satisfy, how many
physical attempts it consumed, whether fallback occurred, and whether parsing
and schema validation succeeded—without opening a raw prompt archive.

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
- `tests/test_context_effectiveness.py`

Research rationale and dated evidence remain non-normative in
[`../../research/llm_harness/README.md`](../../research/llm_harness/README.md).

## Known gaps

Context effectiveness still requires a configured real model provider and
owner blind scoring. Mechanical fixtures or fake providers prove only the
contract and linkage, not improved reasoning quality.
