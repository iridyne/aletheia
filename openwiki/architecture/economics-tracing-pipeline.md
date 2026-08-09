---
type: workflow
title: Economics Tracing Pipeline
description: LLM inference call costs tracked through budgets and viability analysis.
tags: [workflow, economics, costs]
---

# Economics Tracing Pipeline

Tracks LLM inference costs from individual calls through budget enforcement and viability analysis. Lives in `aletheia/economics/`.

```mermaid
flowchart LR
    A[ModelProvider\ncall] --> B[InferenceCall\nRecorded]
    B --> C[Budgets\nEnforced]
    C --> D[Viability\nAnalysis]
    D --> E[Knowledge\n(learn_cost_efficiency)]
```

## Stage 1: Inference Call Recording

**Source:** `aletheia/model_provider/inference_contract.py`, `aletheia/economics/inference_calls.py`

Every LLM call through the provider router creates an `InferenceCall` record:

| Field | Description |
|---|---|
| `call_id` | UUID |
| `provider` | Provider name (openai, anthropic, etc.) |
| `model` | Model name (gpt-4o, claude-3.5-sonnet, etc.) |
| `prompt_tokens` | Input tokens |
| `completion_tokens` | Output tokens |
| `cost_usd` | Calculated cost |
| `timestamp` | When the call was made |
| `agent` | Which agent made the call |
| `context` | What the call was for (market analysis, news, etc.) |

`build_inference_call()` in `inference_contract.py` is the single entry point.

## Stage 2: Cost Tracing

**Source:** `aletheia/economics/tracing.py`

- Accumulates costs per agent, per context, per day
- Flushes to the `inference_costs` table every 5 minutes
- Tracks both **request cost** (API cost) and **opportunity cost** (time value of compute)

## Stage 3: Budget Enforcement

**Source:** `aletheia/economics/budgets.py`

Enforces spend limits via the **inference broker** (`model_provider/broker.py`):

| Budget | Scope | Default |
|---|---|---|
| `daily_llm_budget_usd` | Per-day total LLM spend | $20.00 |
| `per_agent_budget_usd` | Per-agent daily limit | $5.00 |
| `per_context_budget_usd` | Per-context daily limit | $3.00 |
| `emergency_pause_threshold` | 80% of daily budget → soft limit | $16.00 |

When a budget is exceeded:
1. The inference broker blocks the call
2. The agent receives a budget-exceeded signal
3. The call is retried with a lower-tier model (fallback chain)
4. If all providers are budget-blocked, the agent operates in deterministic-only mode

## Stage 4: Viability Analysis

**Source:** `aletheia/economics/viability.py`

- Correlates LLM spend against paper trading outcomes
- Identifies which agents/contexts deliver the best cost-adjusted alpha
- Produces **viability scores** per agent context
- Feeds into model selection (high-viability contexts get premium models, low-viability get cost-optimized)

## Stage 5: Knowledge Integration

**Source:** `aletheia/economics/agent_roles.py`

- Viability analysis feeds `learn_cost_efficiency` into the knowledge base
- Guards are updated: "market_observer is not cost-effective on weekends"
- Model selection adapts: premium models only for high-viability contexts

## Key Symbols

| Symbol | Location | Description |
|---|---|---|
| `InferenceCall` | `model_provider/inference_contract.py` | Dataclass for LLM call record |
| `build_inference_call` | `model_provider/inference_contract.py` | Factory for InferenceCall |
| `record_inference_call` | `economics/inference_calls.py` | Records call to DB |
| `check_budget` | `economics/budgets.py` | Checks if a call exceeds budget |
| `enforce_budgets` | `model_provider/broker.py` | Broker-level budget enforcement |
| `calculate_viability` | `economics/viability.py` | Viability analysis |
| `ViabilityReport` | `economics/viability.py` | Viability score report |

## Test Coverage

- `tests/test_economics.py` — Tests for cost calculation, budget enforcement
- `tests/test_inference_contract.py` — Tests for InferenceCall construction
- `tests/test_budgets.py` — Tests for budget limits and throttling
- `tests/test_viability.py` — Tests for viability analysis
