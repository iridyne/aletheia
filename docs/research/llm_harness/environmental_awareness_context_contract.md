# Environmental-Awareness Context Contract

## Problem

An agent needs operating context, but a large universal system prompt is noisy,
expensive, hard to test, and can imply authority that the runtime has not
granted. Environmental awareness therefore needs a typed contract with truthful
provenance, not an ever-growing header.

## Research synthesis

Context-engineering guidance consistently distinguishes per-call model context,
tool context, and durable lifecycle context. The useful practice is incremental
selection: include the smallest current, relevant, attributable context rather
than all available history. See [LangChain context engineering](https://docs.langchain.com/oss/python/langchain/context-engineering)
and [NVIDIA agent harness guidelines](https://nvidia.github.io/elements/docs/internal/guidelines/agent-harness/).

Evaluation sources such as [Datadog LLM evaluation templates](https://docs.datadoghq.com/llm_observability/evaluations/custom_llm_as_a_judge_evaluations/template_evaluations/)
reinforce that context changes require task-level evaluation, not merely a
syntax check or a subjective one-off preference.

## Contract layers

The proposed `HarnessContext` is composed from independently sourced layers.
A caller selects only the layers it needs.

| Layer | Examples | Provenance / rule |
| --- | --- | --- |
| Identity | logical agent, caller class, run/task ID | runtime attribution only |
| Operating mode | UTC time, paper/demo/live-disabled mode | current config/runtime only |
| Authority | runtime-enforced tool permissions; explicit prohibitions | registry/permission runtime, never model claim |
| Input provenance | market snapshot age, upstream artifact IDs, decision/proposal IDs | stored data and causal IDs only |
| Output contract | schema ID, required rationale/uncertainty fields | caller-owned validated contract |
| Telemetry | context-adapter version, provider/model, token usage after call | call lifecycle only |

The contract intentionally excludes secrets, raw credentials, fabricated health
claims, unaudited market facts, and an implication that text itself grants a
tool permission.

## Context selection rules

1. **Truth before usefulness.** Omit a missing fact rather than infer it.
2. **Least context.** Start with identity and output contract; add a layer only
   when a measured task benefit justifies it.
3. **Stable prefix.** Keep static instructions stable to preserve provider cache
   opportunities; place volatile time/data fields in a bounded dynamic section.
4. **References over bodies.** Prefer causal/artifact IDs and concise freshness
   summaries over duplicating large upstream documents.
5. **No authority by prompt.** A context line can describe the runtime rule but
   only registry/gateway code may enforce it.
6. **Version every adapter.** Record context-adapter ID/version so output and
   token changes are attributable.

## Minimal first adapter

A safe candidate for experiment is:

```text
[Aletheia Harness]
identity: <logical-agent and run, if available>
operating mode: <truthful current mode>
authority: runtime enforces tool permissions; no direct live-order authority
output contract: <only if the caller has one>
```

This is not enabled globally. It is a versioned adapter eligible for a paired
experiment against the unmodified caller prompt.

## Acceptance criteria before default use

- all included fields have a testable runtime source;
- redacted/sanitized rendering never exposes secrets;
- the adapter can be disabled per call and globally;
- no changed trading, risk, execution, or tool-permission behavior;
- paired evidence measures token overhead, validation success, retry rate, and
  task quality for the affected caller class.
