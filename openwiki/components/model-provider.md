---
type: component
title: Model Provider
description: LLM model routing, cost tracking, and token management layer.
tags: [llm, models, routing, cost, tokens]
---

# Model Provider

The **Model Provider** (`aletheia/models/`) is the LLM abstraction layer that handles **model routing, cost tracking, token estimation, and response validation**. It provides a unified interface for calling different LLM providers (OpenAI, Anthropic, local models) with automatic fallback, cost control, and prompt token tracking for budget enforcement.

## Architecture

```mermaid
flowchart TD
    A[Callers\nAgents, Serenity, etc.] --> B[Model Provider]
    B --> C[Model Router\n(priority-based)]
    C --> D1[OpenAI gpt-4o]
    C --> D2[Anthropic claude-3.5]
    C --> D3[Local Llama 3\n(Qwen fallback)]
    C --> D4[DeepSeek v2\n(Cost guard)]
    B --> E[Token Tracker]
    E --> F[Cost Budget\nEnforcement]
    B --> G[Response Validator]
    G --> H[Usage Stats]
    H --> I[Billing DB]
```

## Model Routing

**Source:** `models/router.py`

Models are routed by **priority order** and **context type**:

| Context | Priority 1 | Priority 2 | Priority 3 (fallback) |
|---|---|---|---|
| **Analysis** | gpt-4o | claude-3.5 | Llama 3 |
| **Fast response** | gpt-4o-mini | claude-3-haiku | DeepSeek |
| **Cost-sensitive** | DeepSeek v2 | gpt-4o-mini | Llama 3 |
| **High-stakes** | gpt-4o | claude-3.5 | claude-3-haiku |

Routing criteria:
1. **Token budget** — If cost guard active, routes to cheapest capable model
2. **Response quality** — High-stakes decisions route to premium models
3. **Latency** — Fast-response contexts route to mini/haiku models
4. **Availability** — If primary model fails, automatic fallback to next in priority

## Token Tracking & Cost Management

**Source:** `models/tokens.py`, `models/cost.py`

Token usage is tracked at multiple levels:

### TokenBudget

Enforces spending limits:

| Budget Type | Daily Limit | Reset |
|---|---|---|
| **Production** | $200 | UTC midnight |
| **Paper trading** | $50 | UTC midnight |
| **Development** | $25 | UTC midnight |

Token budget enforcement:
1. **Pre-request check** — Estimated token cost checked against remaining budget
2. **Real-time tracking** — Actual tokens counted per response
3. **Budget alerts** — Warnings at 50%, 75%, 90% of budget
4. **Budget exhaustion** — Fallback to cheapest model or local model only

### CostGuard

**Source:** `models/cost_guard.py`

Dynamic cost adjustment based on:
- Remaining daily budget
- Opportunity viability score
- Time of day (off-peak = more budget)
- System performance (learning experiments can use more budget)

## Response Validation

**Source:** `models/validation.py`

Every LLM response is validated:

1. **Schema validation** — Response must match expected JSON schema
2. **Content validation** — No hallucinated facts, references checked against available data
3. **Confidence scoring** — LLM's self-reported confidence is cross-checked
4. **Fact checking** — Claims cross-referenced against Knowledge Base

Invalid responses trigger:
- **Fallback retry** — Try next model in priority
- **Response rejection** — Return empty/default response
- **Knowledge guard creation** — If systemic issue, create guardrail

## Prompt Templates

**Source:** `models/templates/`

Templates are organized by use case:

| Template | Use Case | Model |
|---|---|---|
| `opportunity_analysis.j2` | Opportunity scoring | gpt-4o |
| `regime_analysis.j2` | Macro regime assessment | claude-3.5 |
| `fact_check.j2` | Fact checking | gpt-4o |
| `news_sentiment.j2` | News sentiment | gpt-4o-mini |
| `cohort_summary.j2` | Cohort consolidation | claude-3-haiku |

Templates support:
- Dynamic context injection
- Few-shot examples
- Format constraints (JSON, YAML)
- Token-efficient truncation

## Model Configuration

**Source:** `models/config.py`

| Parameter | Default | Description |
|---|---|---|
| `timeout_seconds` | 30 | Request timeout |
| `max_retries` | 3 | Retry on failure |
| `temperature` | 0.7 | Default temperature |
| `top_p` | 0.9 | Nucleus sampling |
| `max_tokens` | 4096 | Max response tokens |
| `budget_guard` | True | Enable cost guard |

## Key Symbols

| Symbol | Location | Description |
|---|---|---|
| `ModelProvider` | `models/provider.py` | Main LLM provider class |
| `route_request` | `models/router.py` | Model routing logic |
| `TokenBudget` | `models/tokens.py` | Budget enforcement |
| `CostGuard` | `models/cost_guard.py` | Dynamic cost adjustment |
| `validate_response` | `models/validation.py` | Response validation |
| `render_template` | `models/templates/__init__.py` | Template rendering |
| `record_usage` | `models/cost.py` | Usage recording |
| `MODEL_CONFIGS` | `models/config.py` | Per-model configuration |
| `estimate_tokens` | `models/tokens.py` | Token count estimation |

## Test Coverage

- `tests/test_model_provider.py` — Tests for routing, fallback
- `tests/test_token_budget.py` — Tests for budget enforcement
- `tests/test_cost_guard.py` — Tests for dynamic cost adjustment
- `tests/test_model_validation.py` — Tests for response validation
- `tests/test_templates.py` — Tests for prompt templates
- `tests/test_usage_tracking.py` — Tests for token/cost tracking

## Relationship to Other Components

- [Serenity Lens](serenity-lens.md) — LLM for regime/narrative analysis
- [Market Observer](market-observer.md) — LLM for per-symbol analysis
<!-- openwiki: broken internal link [fact-critic.md] file "fact-critic.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- [Fact Critic](fact-critic.md) — LLM for fact-checking
<!-- openwiki: broken internal link [news-analyzer.md] file "news-analyzer.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- [News Analyzer](news-analyzer.md) — LLM for sentiment analysis
- [Counterfactual](../architecture/counterfactual-pipeline.md) — LLM for advisories
