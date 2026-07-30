# tokenomics/ — LLM Harness and Cost-Control Research

Frozen research notes that feed the `tokenomics` branch work: extending
Aletheia's LLM control surface from the metering baseline established by
PR #110 (`aletheia/economics/`) toward a full inference harness — budget
enforcement, rate limiting, prompt/response guardrails, and a uniform
harness environment for agent callers.

These files are evidence at a point in time. Once a conclusion becomes
authoritative it moves into `design/proposals/`, `design/decisions/`, or
`design/contracts/` per documentation governance.

## Files

| File | What it captures |
|---|---|
| `industry_llm_gateway_survey_2026-07-30.md` | Notes on how mature open-source LLM gateways (LiteLLM, Portkey) structure identity → policy → routing → metering → observability, and what that implies for Aletheia's internal harness. |

## Related current documents

- Contract-adjacent: `docs/references/ECONOMIC_VIABILITY_AUDIT.md` (PR #110's ledger contract).
- Provider layer code: `aletheia/model_provider/` and `aletheia/economics/`.
- GitHub tracking issue: see the tokenomics umbrella issue for coordination.
