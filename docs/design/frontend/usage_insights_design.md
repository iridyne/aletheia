# Usage Insights — Information Architecture and Visual Contract

> Status: implemented 2026-08-12. The page follows Aletheia's dense terminal language and
> progressive disclosure rules. Reference pattern: OpenAI/Anthropic usage reporting,
> LangSmith Cost & Tokens, Langfuse usage types, and Grafana general-to-specific dashboards.

## Operator questions and reading order

The vertical order is a contract, not incidental DOM placement. It follows the questions an
operator asks after opening a usage surface:

1. **Consumption & spend** — how many tokens/calls, and what did they cost?
2. **Token composition** — how much was input/output, cached/reasoning?
3. **Usage & cost trend** — when did consumption change?
4. **Breakdown** — which agents, runs, models, providers, and tools produced it?
5. **Reliability & budgets** — was the consumption healthy and bounded?
6. **Data coverage & gaps** — how trustworthy are the preceding numbers?
7. **Research yield** — what durable artifacts resulted?

Coverage is supporting provenance. A compact quality summary belongs near the page controls;
the complete coverage list belongs after operational facts. It must not outrank consumption.

## Token semantics

- `total_tokens = input_tokens + output_tokens` when both are measured.
- Cached tokens are a subtype of input; reasoning tokens are a subtype of output. They must
  never be added to total again or rendered as sibling pie slices.
- Composition uses two horizontal bars: ordinary/cached input and ordinary/reasoning output.
- Known and estimated costs remain separate provenance categories. The page never presents
  their sum as provider-billed truth.

## Progressive loading

The client defaults to 1D and requests three read projections:

1. `summary`: consumption, spend, and reliability.
2. `operations`: trends, attribution, tools, and budgets; enabled after fresh summary data.
3. `analysis`: research yield; enabled with operations and allowed to complete independently.

Initial gaps use section-shaped skeletons. Existing data remains visible during a refresh and
keeps its own window label until the selected window arrives. An indeterminate native progress
element and `aria-busy` communicate background work without fabricating completion percentages.

## Layout and bounds

- Six primary metrics use a fixed 6-column grid, collapsing to 3 columns below `lg` and 2
  below `sm`.
- Two-panel bands collapse at the canonical `lg` breakpoint (1024px).
- Every detail table uses the shared `DataTable`, 25 rows per page, a 420px scroll ceiling,
  and sticky headers. No API array is rendered wholesale into page-height DOM.
- Research Yield begins with aggregate counts; per-run artifacts are drill-down evidence.
- Coverage uses a text/status list, not a radar. Its axes have different denominators and are
  not a single comparable score.

## Honesty constraints

- Missing token counters, unmatched models, unsupported cache pricing, and incomplete
  attribution remain `N/A`, `Partial`, or `Unknown`.
- LiteLLM prices are estimates. Historical events may be projected in memory for the Usage
  response, but the append-only runtime ledger is not rewritten.
- Current model cooldown is current process state, not historical window evidence.
