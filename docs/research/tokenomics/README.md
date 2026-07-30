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
| `industry_llm_gateway_survey_2026-07-30.md` | How mature open-source LLM gateways (LiteLLM, Portkey) structure identity → policy → routing → metering → observability, and what translates to Aletheia's internal harness. |
| `llm_call_site_inventory_2026-07-30.md` | Every LLM call site in `aletheia/` funnels through three helpers in `agents/helpers.py`; two small verification bypassers noted. Compares four candidate insertion points for the future harness gate. |
| `attribution_flow_2026-07-30.md` | Two independent identity ContextVars (`run_id`, `economic_attribution`), how they reach the ledger, and four edge cases for future budget scoping. |
| `routing_and_fallback_2026-07-30.md` | Broker admission (per-provider concurrency, RPM, TPM, priority) and fallback (request-level model fallback + per-model cooldown) already implemented; corrects an earlier "no RPM/TPM" tracking-issue claim. |
| `config_and_surfaces_2026-07-30.md` | `ModelsConfig`/`ProviderConfig` fields already carry routing, fallback, and broker admission. No `/api/economics` backend route or Settings tile exists today — Phase 1 must build them. Sketches where Phase 2's `EconomicsConfig` block would land. |
| `system_prompt_patterns_2026-07-30.md` | Nine callers sampled: role → input contract → output contract → behavioural constraints. What every prompt is missing (time, run identity, budget-remaining, tool permissions) and a sketch of a Phase 5 harness environment header prepended in `helpers.py`. |

## Related current documents

- Contract-adjacent: `docs/references/ECONOMIC_VIABILITY_AUDIT.md` (PR #110's ledger contract).
- Provider layer code: `aletheia/model_provider/` and `aletheia/economics/`.
- GitHub tracking issue: see the tokenomics umbrella issue for coordination.
