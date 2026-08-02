# Unified Inference Harness

## Question

How should Aletheia make LLM calls consistent across worker agents, the Agent
Shell, runtime services, and operator tooling without confusing the harness
with a trading authority or a billing system?

## Research synthesis

The external sources converge on a practical distinction: an agent is the
combination of model, deterministic harness, and environment. The harness owns
context selection, tool schemas, task lifecycle, validation, recovery, and
observability; a prompt alone cannot reliably provide those properties.

- NVIDIA's [agent harness guidelines](https://nvidia.github.io/elements/docs/internal/guidelines/agent-harness/)
  emphasise explicit resources, tool use, verification, and ground-truth
  observation.
- The [UNU framework](https://unu.edu/publication/engineering-and-governing-agent-harness-technology-and-policy-framework-runtime-layer)
  treats governance and runtime controls as a harness layer, rather than model
  intent.
- LangChain's [context engineering guidance](https://docs.langchain.com/oss/python/langchain/context-engineering)
  separates transient call context from durable lifecycle and tool context.

These are design references, not proof that one multi-agent topology is always
best. The useful shared principle is to place deterministic controls around the
model rather than encode them as wishful prompt instructions.

## Aletheia facts today

Most application-level calls converge through `aletheia/agents/helpers.py`:

- `llm_complete()` for ordinary completions;
- `llm_structured()` for schema-oriented responses, including bounded repair
  retries and optional non-critical defaults;
- `llm_stream()` for streamed output.

Those helpers resolve a provider and route completions through
`complete_with_fallback()`. The lower control plane selects candidate models,
tracks provider health/cooldowns, records fallback metadata, and drives the
provider adapters. A small number of bounded probes call the lower layer
directly; they remain visible to base-provider tracing.

`ModelProvider` tracing records a physical-attempt ledger event. That is the
right place for measurement. Budget/control decisions belong above it so the
ledger remains an honest observation surface rather than a hidden policy engine.

## Target contract

Every logical inference should carry the following non-secret contract:

```text
InferenceCall
  identity: logical_agent, caller_class, run/task/decision correlation IDs
  request: provider selection, model/tier, output contract, priority
  context: selected truthful context artifact IDs and context-adapter version
  execution: attempts, fallback path, latency, status
  usage: input/output/cache/reasoning tokens when supplied by provider
  outcome: validation result and sanitized failure category
```

This does **not** require retaining raw prompts in the cost ledger. Prompt
content may be available only inside the short-lived call path or a separately
approved privacy-preserving evaluation fixture.

## Decisions

1. Token telemetry is the primary early metric; USD is a nullable enrichment.
2. `usage unavailable` is distinct from `cost unknown`: the former means the
   provider supplied no usable token data; the latter can have complete tokens
   but no applicable price snapshot.
3. A provider's cache token fields are recorded verbatim when available. The
   harness will not invent a cache estimate.
4. Fallback attempts remain separately observable; collapsing them would hide
   real token consumption and reliability costs.
5. The harness returns explicit sanitized failure classes. It must never
   silently turn a model denial/failure into an approved trading action.

## Initial implementation path

1. Build token-first aggregate read models and API/CLI views from the existing
   `runtime_cost_events` ledger.
2. Normalize provider usage fields into the contract above, preserving raw
   provider usage as attached evidence when needed.
3. Add a typed call-context envelope used by normal, structured, and stream
   paths.
4. Validate coverage using deterministic fixtures for every supported provider
   adapter and representative callers.

## Evaluation

Track coverage rather than early dollars:

- percentage of successful LLM attempts with input and output token counts;
- percentage with cache fields when the provider exposes them;
- attribution completeness for logical agent and correlation IDs;
- fallback/retry rate by caller and provider;
- schema-valid response rate and latency percentiles.
