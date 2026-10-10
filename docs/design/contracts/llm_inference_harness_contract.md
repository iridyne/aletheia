# LLM Inference Harness Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `LH` · **Owner code:** `core/aletheia/agents/helpers.py`, `core/aletheia/model_provider/inference_contract.py`, `core/aletheia/model_provider/fallback.py`

## Scope

The harness is the single authority for how an LLM inference is identified, controlled,
validated and observed: one content-free logical call per inference, its caller identity,
prompt/context/tool/output envelope, model fallback and the durable telemetry around it. On the
mainline its load-bearing caller is the canonical Entry Planner (`agent_shell.entry_planner`).
Tokenomics remains the physical-consumption ledger. The harness grants no trading authority and
changes no Risk, Execution, exchange, credential, hard-limit or Live-unlock behavior. Learning
callers are listed as they run today; the planned `entry_planner` schema/version and Playbook
context changes are [spec 0001](../specs/0001-learning-loop/spec.md) LL-R01/LL-R12.

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/agents/helpers.py::llm_structured` | call | `core/aletheia/agent_shell/entry_planner.py` (`agent_shell.entry_planner`), `core/aletheia/agent_shell/commands.py` (`agent_shell.portfolio_review`), `extensions/data/news_analyzer.py`, research/learning producers |
| `core/aletheia/agents/helpers.py::llm_complete` / `llm_stream` | call | text callers in the matrix below, e.g. `core/aletheia/server/routes/operator.py` (`operator.llm_session.turn`) |
| startup and Settings probes (`startup_gate.provider_probe`, `provider_settings.ping`, `provider_settings.candidate_probe`) | call | `core/aletheia/agent_runtime/startup_gate.py`, `core/aletheia/server/routes/model.py`, `extensions/model_endpoints/credential_routes.py` |
| `core/aletheia/model_provider/fallback.py::complete_with_fallback` | call | every helper above, after Inference Broker admission |
| `/settings/model-profiles`, `/settings/model-routing` | API routes | Settings page |

Current production callers by grep of literal `caller="…"` arguments (core and extensions):
Entry Planner, portfolio review, general query, intent classification and synthesis
(`agent_shell.*`), the operator shell turn, news analysis, the three provider probes, and the
optional research/learning producers (`research_worker.*`, `research_agent.note`,
`post_trade_reviewer.review`, `risk_critic.review`, `dreaming.*`, `lesson_reviewer.judge`,
`insight_synthesizer.insight`, `learning.hypothesis_causal_red_team`) plus the frozen observers
(`equity_observer.market_read`, `prediction_observer.market_read`). No production call site uses
`agent_shell.entry_planner.v2` today; it is a registered version row.

## Durable state

### Durable state and consumers

| Table | Writer | Readers | Mutability |
|---|---|---|---|
| `llm_inference_calls` | `core/aletheia/economics/inference_calls.py` | harness audit, caller coverage, context evaluation | one content-free row per logical call; finalized once |
| `runtime_cost_events` (`event_type='llm'`) | provider attempt telemetry | token/cost/reliability analysis | append-only, one row per physical attempt |
| `llm_context_artifacts` | helper context recording | context audit, paired evaluation | append-only |
| `llm_context_pair_reviews` | owner blinded scoring | paired context evidence summary | aggregate scores only |
| `agent_tool_calls.inference_call_id` | agent runtime tool telemetry | AgentRun/tool-cost audit | set once |
| `agent_shell_commands.inference_call_id` | operator turn command audit | command audit | set once |

## Requirements

### Logical call envelope

#### LH-C01 Every production call names a registered caller

`Every production helper call shall pass a literal caller identity registered in CALLER_SPECS, and every application-level ModelRequest shall carry the logical envelope into provider tracing.`

- WHEN application code omits `caller=` or uses an unregistered id THEN the executable AST claim fails.

#### LH-C02 One stable logical call per inference

`When a helper is invoked, the harness shall create one InferenceCall before provider admission and keep it stable across structured repair retries and model fallback attempts, and every physical attempt shall use its logical_call_id as runtime_cost_events.call_id with monotonically increasing attempt indexes.`

| Layer | Persisted content-free facts |
| --- | --- |
| Identity | logical call ID, caller ID, AgentRun/runtime session identities when available |
| Prompt | template ID/version, static-system SHA-256, rendered request SHA-256, character counts |
| Context | selected adapter ID and bounded model-visible context manifest |
| Tools | model tool-interface type, exact allowed IDs when an interface exists, allowlist SHA-256 |
| Environment | permission mode, selected demo execution mode (`exchange.paper_execution_mode`), live state, logical agent/run identities, and explicitly bound evaluation/opportunity/decision/proposal IDs when already known |
| Request | requested provider, temperature, max tokens, fallback policy |
| Output | output mode, schema identity/SHA-256, parse-valid and schema-valid outcomes; on terminal structured failure only, a fixed response-shape enum plus bounded schema error class and first failing schema path |
| Evaluation | bounded pair, arm, fixture, and optional modality IDs when the call belongs to an offline evaluation |

#### LH-C03 No stored content

`The harness shall store no prompt text, response text, source body, credential or secret in the envelope, and economic attribution shall accept only the fixed identity keys and discard any other key before provider or ledger code can observe it.`

- Structured-failure diagnostics store no model content, values, model-supplied keys, lengths or content hashes; the only names they carry are the caller schema's own declared properties in the first failing path (`schema_error_path`, e.g. `$.risk_intent.posture`), truncated at the first segment the schema does not declare.

#### LH-C04 Explicit causal attribution only

`When the caller has bound existing causal entities, the harness shall copy only those opportunity_id, decision_id and proposal_id values into runtime_cost_events and the call's environment metadata, leaving entities not yet created NULL.`

- Neither row order nor time proximity supplies a missing identity; historical System Operator / `signal_pending` PiDecision attribution stays readable as the retired entry path only.

### Prompt control

#### LH-C05 Caller-owned prompts with versioned identity

`The harness shall keep prompts caller-owned, take a stable template identity and version from the central registry, and expose an unversioned rendered change only through its hash.`

#### LH-C06 Context adapters

`The harness shall record the selected context adapter independently: none shall preserve the caller system prompt byte-for-byte, minimal-v1 (default off, economics.harness_header_default_on) shall render only its bounded manifest, and technical-context-v1 shall leave the static system prompt unchanged and require at least one linked technical-context-v1 artifact.`

- WHEN `technical-context-v1` is selected without a linked artifact THEN the call raises before admission.
- No current production caller selects `technical-context-v1`; the adapter and its renderer `core/aletheia/trading/market_context.py::build_market_context` have no production call site.
- Text cannot grant a permission.

### Tool control

#### LH-C07 Text-mediated commands pass runtime checks

`The harness shall record provider-native-tools-none-v1 with an empty allowlist for every registered caller except operator.llm_session.turn, whose parsed commands shall still pass the Agent Shell registry, operator permission, worker/global permission and confirmation checks before execution.`

- The command audit row and agent tool telemetry carry the originating `inference_call_id`; prompt text never replaces these checks.

### Environment awareness

#### LH-C08 Environment versus model-visible context

`The harness shall record what the runtime knew (environment) separately from what the adapter declared visible to the model (context_manifest), and selected News, Learning, technical and environment artifacts shall be recorded as content-free ContextArtifact rows linked to the exact logical call.`

- WHEN the Entry Planner runs THEN an environment ContextArtifact links `planning-generation:<id>` with the analysis cutoff and rendered character count, and `planning_generation_id` is bound as explicit attribution; `run_id` exists only when an AgentRun is bound.
- A zero-character or absent artifact is not evidence that the model used it.

### Output validation

#### LH-C09 Schema-valid or error

`When llm_structured receives output, the harness shall require a JSON object, validate it against the caller's schema (Pydantic model_validate, then the bounded JSON-Schema subset on the normalized model_dump), and finalize any parse or schema failure as an error, never as success.`

- At most two attempts share a 180-second wall-clock budget: the original request and one nonce-bearing repair with the same output budget; only empty or non-JSON output excludes that model from the repair.
- `fallback_to_defaults` is off by default and no production caller enables it; when enabled the defaults are returned with a `(fallback)` error marker and the logical call stays `error`.

### Failure and recovery semantics

#### LH-C10 Explicit model fallback only

`If a provider attempt fails with a transient error, then complete_with_fallback shall try only the next model from the selected endpoint's explicit ordered fallback_models (including explicit @provider:model entries) within eight models per request, and discovery, catalogues, unrelated endpoints, caller ids and model names shall never supply a candidate.`

- Transient means `ProviderResponseError`, network/remote-protocol/proxy transport errors, HTTP 400/404/408/429/500/502/503/504, or a capacity/rate-limit message marker.
- A model with two consecutive failures is cooled down for 300 s (`_record_model_failure`).
- WHEN `allow_model_fallback=False` (paired evaluations) THEN only the selected model is called.
- Explicit cross-endpoint fallback, cooldown and structured repair stay inside the same broker, budget and cancellation boundaries.

#### LH-C11 Cancellation propagates

`When an external task cancels a started structured call, the harness shall finalize the logical row as error with error_type=cancelled and re-raise the original CancelledError.`

#### LH-C12 Telemetry is fail-open

`If a telemetry write or lookup fails, then the harness shall let the business call proceed and record no database exception text, and a measurement write shall never authorize or block a trade.`

- Schema creation or upgrade is additive and refuses to cross an active caller transaction.
- Missing provider configuration produces an honest unavailable result, never a synthetic conclusion.

#### LH-C13 Reasoning effort is caller-owned

`The harness shall send reasoning_effort only when the caller selects it, and if an OpenAI-compatible endpoint returns 400/422 identifying reasoning_effort as unsupported, then the adapter shall retry that read-only completion exactly once without the field.`

- Current selectors: the Entry Planner `medium`; portfolio review, news analysis, lesson extraction, lesson reviewer and hypothesis red-team `none`.
- Soft prompt text such as `/no_think` is not proof that provider reasoning was disabled.

### Endpoint/model routing

#### LH-C14 Fast and Deep select an explicit endpoint/model pair

`The system shall give the Fast and Deep tiers each an explicit (endpoint_id, model_id) selection that never borrows Default, and shall fail closed on an unknown tier.`

- Two tiers may select different models on one HTTP endpoint without changing that endpoint's preferred model, credentials or activation.
- Historical `fast_provider` / `deep_provider` decode into the pair they resolved to, are rejected by routing requests, and mixed old/new tier fields are invalid.
- Per-call model selection never mutates the cached preferred-model provider; broker admission, fallback configuration and budget attribution keep the endpoint identity.

#### LH-C15 Assignment is verified and atomic

`When a role is assigned, the system shall verify the exact endpoint/model with the bounded probe and recheck its identity under the configuration mutation lock, and a probe failure or identity drift shall assign nothing.`

- Discovery supplies suggestions, never an implicit assignment.
- Profiles are written through `/settings/model-profiles` and roles through `/settings/model-routing`; the fixed-provider `POST /settings/model` endpoint is retired with no alias.

#### LH-C16 Only HTTP endpoints are current transports

`The system shall construct, probe and route only HTTP model endpoints, dispatching OpenAI Chat Completions/Responses and Anthropic Messages through their official SDKs with SDK automatic retries, ambient credential inheritance and content-bearing debug logs disabled.`

- An assigned retired type (Codex/Claude/Pi subprocess, fixed-origin native, Databricks workspace, subscription OAuth) is shown unavailable; it cannot construct or probe a provider, resolve credentials or receive routing.
- Config/health reads and profile deletion never resolve or delete historical OAuth credentials; obsolete workspace fields are rejected on write and are read-only provenance.

#### LH-C17 Credential presence is not usability

`The Settings and Overview projections shall expose only credential presence and masking metadata, and a missing implementation or secret-store failure shall be reported as unknown, not as no key configured.`

#### LH-C18 Adapters normalize completion evidence

`Provider adapters shall normalize completion evidence, bounded finish reason and optional output-token count into ModelResponse, so Core startup, Settings ping and structured repair never inspect supplier wire fields.`

- Startup accepts text or positive output-token evidence; ping accepts text or confirmed completion; structured repair still requires valid content. Missing counts stay unknown.

## Current caller coverage matrix

The block is rendered from `CALLER_SPECS` and checked by the Contract spec. Scope of the
non-mainline rows: `equity_observer.market_read` and `prediction_observer.market_read` are frozen
(spec 0003 FC-R04); `dreaming.*`, `lesson_reviewer.judge`, `insight_synthesizer.insight` are
replaced by spec 0001.

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
| `equity_observer.market_read` | `equity_observer.market_read` | `v1` | `structured` | `none` |
| `insight_synthesizer.insight` | `insight_synthesizer.insight` | `v1` | `text` | `none` |
| `learning.hypothesis_causal_red_team` | `learning.hypothesis_causal_red_team` | `v1` | `structured` | `none` |
| `lesson_reviewer.judge` | `lesson_reviewer.judge` | `v1` | `structured` | `none` |
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
<!-- END GENERATED CALLER MATRIX -->

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| provider / budget / timeout / parse / schema failure | logical row `error`; caller degrades | `llm_inference_calls` outcome, AgentRun errors | never a successful default object |
| transient provider error | next explicit fallback model, or raise | one `runtime_cost_events` row per attempt | attempts never collapsed |
| cancellation | row `error` (`cancelled`), re-raise | logical row | not an availability tuple |
| telemetry write fails | business call proceeds | log only | no fabricated row |
| provider not configured | caller-owned unavailable | caller result | `unavailable`, no synthetic conclusion |
| retired transport assigned | unavailable in Settings | Settings role status | not a probe failure |

## Authority

The harness may admit, retry, validate and record model calls; prompt text has zero permission
effect, Risk approval and the Execution Gateway stay mandatory, physical attempts are never
collapsed or deleted to improve apparent cost, and a context or prompt change that can change
decisions must be attributable by identity/version/hash and evaluated before default-on use.
No stored prompt, response, source body, credential, or secret (LH-C03).

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| LH-C01 | `tests/specs/spec_llm_inference_harness.py` (LLMH-S01, LLMH-S05, LLMH-S07) |
| LH-C02, LH-C03, LH-C04 | `tests/specs/spec_llm_inference_harness.py` (LLMH-S03); `tests/specs/spec_data_utilization.py` (DU-S25); `tests/test_llm_inference_harness.py` |
| LH-C05, LH-C06, LH-C08 | `tests/test_llm_inference_harness.py` |
| LH-C07 | `tests/specs/spec_llm_inference_harness.py` (LLMH-S04) |
| LH-C09, LH-C11, LH-C12 | `tests/specs/spec_llm_inference_harness.py` (LLMH-S06, LLMH-S08); `tests/test_llm_inference_harness.py` |
| LH-C10 | `tests/specs/spec_agent_contracts.py` (AC-B02, AC-B02b); `tests/test_model_fallback.py`; `tests/test_inference_broker.py` |
| LH-C13 | `tests/test_llm_inference_harness.py` |
| LH-C14, LH-C15 | `tests/specs/spec_llm_inference_harness.py` (MR-S01, MR-S02, MR-S03, MR-S05); `tests/test_model_profiles.py` |
| LH-C16, LH-C17, LH-C18 | `tests/test_retired_model_providers.py`, `tests/test_model_profiles.py`, `tests/test_economic_cli.py` |
| caller matrix | `tests/specs/spec_llm_inference_harness.py` (LLMH-S02) |

## Known conformance gaps

- **Fallback retries some permanent 4xx.** The previous contract text limited transient errors
  to 429/502/503/504 plus transport failures and said "authentication and other permanent 4xx
  failures do not retry". `fallback.py::_TRANSIENT_STATUS_CODES` is
  `{400, 404, 408, 429, 500, 502, 503, 504}` and every `ProviderResponseError` is transient, so a
  400 or 404 also walks the fallback chain. 401/403 do not retry. LH-C10 states current code.
- **Registered caller with no call site.** `agent_shell.entry_planner.v2` is in `CALLER_SPECS`
  but the Entry Planner calls `agent_shell.entry_planner` (v1).
- **Orphan adapter.** `technical-context-v1` and `trading/market_context.py::build_market_context`
  have no production caller since the Market Observer was retired.
- **Provider management lives here.** LH-C14–LH-C18 (routing, transports, credential presence)
  are model-endpoint management rather than inference identity; they have no separate Contract.
- **spec 0001 delta.** spec 0001 LL-R01 changes the `agent_shell.entry_planner` schema
  (`p_target_first`, `lessons_considered`) and LL-R12 records the Playbook as context; spec 0001's
  Contract Delta does not yet list this Contract.

## Decision references

- Research rationale and dated evidence: [`../../research/llm_harness/README.md`](../../research/llm_harness/README.md).
- HTTP transport convergence: [slim core HTTP model transport](../proposals/slim_core_http_model_transport.md).
- Retired transports, former Market Observer wording and the retired context-effectiveness
  harness: [harness history](../history/llm_inference_harness_retired_sections.md).
