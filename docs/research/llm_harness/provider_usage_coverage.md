# Provider Usage Coverage

## Purpose

This record defines the token telemetry contract independently of price. A
provider is allowed to omit fields; the harness records that absence as
unavailable and never estimates cache usage.

## Normalized fields

| Normalized field | Meaning | Typical source |
| --- | --- | --- |
| `input_tokens` | provider-reported request input total | OpenAI `prompt_tokens`, Anthropic `input_tokens` |
| `output_tokens` | provider-reported generated output total | OpenAI `completion_tokens`, Anthropic `output_tokens` |
| `total_tokens` | provider-reported total, if supplied | `total_tokens` |
| `reasoning_tokens` | separately reported reasoning component | top-level or output-details field |
| `cached_input_tokens` | generic cached-input counter | OpenAI `prompt_tokens_details.cached_tokens` |
| `provider_cache_read_input_tokens` | provider-specific cache read counter | Anthropic-compatible `cache_read_input_tokens` |
| `provider_cache_creation_input_tokens` | provider-specific cache creation counter | Anthropic-compatible `cache_creation_input_tokens` |

Input tokens remain the provider-reported total. Cache counters are supplemental
breakdowns, not values subtracted from input usage.

## Adapter evidence

The current provider adapters retain their provider `usage` payload on the
`ModelResponse` or terminal `StreamChunk`. Base-provider tracing normalizes that
payload at the physical-attempt boundary. Deterministic fixtures cover:

- OpenAI-style `prompt_tokens` / `completion_tokens`, nested prompt cache usage,
  and nested output reasoning usage;
- Anthropic-style `input_tokens` / `output_tokens` with cache-read and
  cache-creation counters;
- missing fields, which remain `null` in normalized usage and contribute zero
  only to aggregate totals while reducing field availability.

## Non-goals

- Do not derive cache use from prompt text or response timing.
- Do not treat unavailable usage as zero usage in coverage reporting.
- Do not require a price snapshot to expose token telemetry.
- Do not compare cache semantics across providers as though they had an
  identical billable definition.

## Follow-up evidence

When CASI provider access is configured, capture one sanitized live response per
active adapter shape and compare its normalized ledger row to this contract.
Any new field naming must add a deterministic fixture before it is aggregated.
