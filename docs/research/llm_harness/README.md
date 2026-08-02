# LLM Harness Research Index

This directory is a living research record for Aletheia's framework-wide LLM
harness. It is deliberately separate from the narrower tokenomics record:
pricing and budgets are optional consumers of inference telemetry, not the
purpose of the harness.

## Product thesis

An Aletheia agent is not only a model prompt. It is a model operating through a
deterministic harness and a controlled trading environment. The harness must
make the call attributable, supply only truthful context, enforce permissions
outside prompt text, validate outputs, and emit enough telemetry to improve the
system over time.

The primary early operational metric is token usage:

- input tokens;
- output tokens;
- cache-read and cache-creation tokens only when the provider reports them;
- request count, latency, retry/fallback count, and outcome.

USD estimates are optional and must depend on an explicit immutable price
snapshot. Missing price must not make token telemetry unavailable.

## Reading order

1. [Unified inference harness](unified_inference_harness.md)
2. [Environmental-awareness context contract](environmental_awareness_context_contract.md)
3. [Agent pyramid and role topology](agent_pyramid_and_role_topology.md)
4. [Context adapters and header experiments](context_adapters_and_header_experiments.md)
5. [Provider usage coverage](provider_usage_coverage.md)
6. [Default token observation policy](token_observation_policy.md)
7. [News and Learning context assessment](news_learning_context_assessment.md)

## Evidence standard

Each document distinguishes:

- **external research** — linked source material, which may be vendor guidance
  rather than a universal result;
- **Aletheia evidence** — observed code/runtime facts and testable contracts;
- **proposal** — a reversible design choice to evaluate, not a claim of truth.

Research never authorizes changes to Risk, Execution, live trading, credentials,
or tool permissions. Those remain independently controlled runtime boundaries.

## Initial source set

- OpenTelemetry, [GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/).
- LangChain, [Context engineering](https://docs.langchain.com/oss/python/langchain/context-engineering).
- NVIDIA Elements, [Agent harness guidelines](https://nvidia.github.io/elements/docs/internal/guidelines/agent-harness/).
- UNU, [Engineering and Governing Agent Harness](https://unu.edu/publication/engineering-and-governing-agent-harness-technology-and-policy-framework-runtime-layer).
- Datadog, [LLM evaluation templates](https://docs.datadoghq.com/llm_observability/evaluations/custom_llm_as_a_judge_evaluations/template_evaluations/).

The OpenTelemetry GenAI conventions are evolving; field names and semantics
must be version-pinned when adopted rather than assumed permanently stable.
