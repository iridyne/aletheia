# Context Adapters and Header Experiments

## Position

A Header is an implementation mechanism for an environmental-awareness context
adapter. It is not synonymous with the LLM harness and it must not become a
universal, unmeasured system prompt.

Aletheia's existing Phase 5 experiment found complete automated
format/schema compliance in both header and control arms, while the tested
header increased input usage by roughly 73–75 tokens and changed output length.
The dated evidence is in
`docs/research/tokenomics/harness_header_ablation_2026-08-01.md`. The feature
flag remains disabled because the required blinded PI-quality scoring has not
been completed.

## Research synthesis

Context engineering recommends measuring context changes against the intended
task, retaining only relevant information, and keeping stable instructions
cache-friendly. Agent-harness guidance recommends separating deterministic
runtime enforcement from model-visible explanation. References:

- [LangChain context engineering](https://docs.langchain.com/oss/python/langchain/context-engineering)
- [NVIDIA agent harness guidelines](https://nvidia.github.io/elements/docs/internal/guidelines/agent-harness/)
- [Datadog evaluation templates](https://docs.datadoghq.com/llm_observability/evaluations/custom_llm_as_a_judge_evaluations/template_evaluations/)

These are useful hypotheses, not proof that added context improves Aletheia
trading decisions. Aletheia must use its own paired evaluations.

## Adapter variants

| Adapter | Contents | Intended callers | Default |
| --- | --- | --- | --- |
| `none` | Original caller prompt only | all | enabled |
| `minimal-v1` | identity, mode, runtime-authority statement | non-trading workers | experiment only |
| `decision-v1` | minimal plus causal IDs and output contract | typed decision/review tasks | experiment only |
| `operator-v1` | minimal plus bounded health/provenance facts | System Operator | experiment only |

No variant includes secret values, unverified market assertions, a claim of
risk approval, or text that itself grants tool authority.

## Experiment protocol

For any affected caller/model pair:

1. Freeze fixture, model, decoding settings, schema, and evaluation rubric.
2. Randomise paired `none` and adapter trials; conceal arm identity from human
   reviewers.
3. Measure input/output/cache tokens, latency, retry/fallback rate, schema
   validity, and task-specific quality.
4. Report effect size and failures, not only pass percentage.
5. Keep the adapter disabled if quality is neutral/negative or evidence is
   insufficient.
6. Re-run after provider, model, context-contract, or task-rubric changes.

The quality rubric for PI decisions must be owner-approved and human-scored;
an LLM judge can assist triage but must not satisfy that gate by itself.

## Implementation rules

- Render from typed context, not arbitrary concatenated strings.
- Include a context-adapter ID/version in telemetry.
- Allow global default-off and per-call opt-in/opt-out.
- Use the same rendering path for plain, structured, and streaming calls where
  the adapter is applicable.
- Assert in focused tests that disabled mode leaves the caller prompt unchanged.

## Decision gate

Default-on requires all of: a stated quality threshold, blinded human review,
no safety semantic regression, acceptable token/retry impact, and a recorded
owner decision. Until then, `harness_header_default_on` remains false.
