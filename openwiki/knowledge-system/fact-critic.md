---
type: component
title: Fact Critic
description: Cross-referencing system that validates facts across news, market data, predictions, and LLM claims.
tags: [fact-checking, cross-reference, validation, critics]
---

# Fact Critic

The **Fact Critic** (`knowledge/fact_critic.py`, `knowledge/fact_critic_v2.py`) is Aletheia's fact-checking and cross-referencing system. It validates claims and facts by comparing them against multiple independent data sources, identifying contradictions, and generating **guardrails** when facts are found to be unreliable.

## Architecture

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
flowchart LR
    A[Fact Claims] --> B[Fact Critic\n(cross_ref.py)]
    B --> C1[News ↔ Price]
    B --> C2[Prediction ↔ Reality]
    B --> C3[Regime ↔ Performance]
    B --> C4[LLM Claim ↔ Data]
    C1 --> D[Validation\nScores]
    C2 --> D
    C3 --> D
    C4 --> D
    D --> E{Confidence\n< 0.6?}
    E -->|Yes| F[Create Guardrail]
    E -->|No| G[Knowledge\nEntry Validated]
```

## Cross-Reference Checks

**Source:** `knowledge/cross_ref.py`

### 1. News ↔ Price (`check_news_vs_price`)

**Source:** `knowledge/fact_critic.py::check_news_vs_price`

Validates news sentiment against actual price movements:

```python
def check_news_vs_price(headline, sentiment, symbol, time_window="60min"):
    """Check if news sentiment matches price movement."""
    price_before = get_price(symbol, headline.timestamp - time_window)
    price_after = get_price(symbol, headline.timestamp + time_window)
    actual_move = (price_after - price_before) / price_before
    
    predicted_sentiment = sentiment  # positive/negative/neutral
    predicted_direction = sentiment_to_direction(predicted_sentiment)
    
    # Contradiction if sentiment predicts move opposite to actual
    if sign(actual_move) != sign(predicted_direction) and abs(actual_move) > 0.02:
        return {
            "validated": False,
            "confidence": 0.3,
            "contradiction": f"Sentiment {sentiment} vs move {actual_move:.2%}"
        }
    return {"validated": True, "confidence": 0.8}
```

### 2. Prediction ↔ Reality (`check_prediction_vs_actual`)

**Source:** `knowledge/fact_critic.py::check_prediction_vs_actual`

Compares prediction market probabilities against actual outcomes:

```python
def check_prediction_vs_actual(prediction_id, predicted_prob, outcome):
    """Check prediction accuracy."""
    predicted = predicted_prob
    actual = 1.0 if outcome == "yes" else 0.0
    error = abs(predicted - actual)
    
    confidence = 1.0 - error  # 0.0 to 1.0
    return {"validated": confidence > 0.5, "confidence": confidence}
```

### 3. Regime ↔ Performance (`check_regime_vs_performance`)

**Source:** `knowledge/fact_critic_v2.py::check_regime_performance`

Validates regime classifications against actual trading performance:

- If regime says "volatile" but portfolio volatility is low → contradiction
- If regime says "bull" but returns are negative → contradiction
- If regime says "bear" but drawdown is < 2% → contradiction

### 4. LLM Claim ↔ Data (`check_llm_claims`)

**Source:** `knowledge/fact_critic.py::check_llm_claims`

Validates LLM-generated claims against historical data:

```python
def check_llm_claim(claim, supporting_data):
    """Check LLM claim against supporting data."""
    # Parse claim into structured assertion
    assertion = parse_claim(claim)
    
    # Look for matching data
    evidence = find_supporting_evidence(assertion, supporting_data)
    
    if not evidence:
        return {"validated": False, "confidence": 0.1, "reason": "no_data"}
    
    # Check if data supports claim
    confidence = compute_support_confidence(evidence, assertion)
    return {"validated": confidence > 0.7, "confidence": confidence}
```

## Guardrail Generation

When a fact is invalidated (confidence < 0.6), the Fact Critic generates a **guardrail**:

**Source:** `knowledge/fact_critic.py::generate_guardrail`

```python
def generate_guardrail(fact, contradiction, sources):
    """Generate a guardrail from a fact contradiction."""
    guard = Guardrail(
        type=classify_guardrail_type(fact.domain),
        condition=contradiction.condition,
        action=contradiction.recommended_action,
        confidence=contradiction.confidence,
        source=fact_id,
        expires_at=datetime.utcnow() + timedelta(days=90)
    )
    knowledge_base.add_guardrail(guard)
    logger.warning(f"Created guardrail: {guard.condition} -> {guard.action}")
```

## Fact Critic Reports

**Source:** `knowledge/fact_critic_v2.py::generate_report`

The Fact Critic generates periodic reports:

| Report Type | Frequency | Content |
|---|---|---|
| `daily_summary` | Daily | Top contradictions, new guardrails |
| `weekly_analysis` | Weekly | Trend analysis of fact reliability |
| `domain_breakdown` | Weekly | Per-domain validation scores |
| `guardrail_effectiveness` | Weekly | Which guardrails triggered and their impact |

## Validation Thresholds

| Check | Failure Threshold | Guardrail Action |
|---|---|---|
| News↔Price | 2% opposite move | `entry_guard` for related symbols |
| Prediction↔Reality | >20% error | `risk_guard` for prediction domain |
| Regime↔Performance | 3 contradictory signals | `regime_guard` for that regime |
| LLM Claim↔Data | No supporting evidence | `cost_guard` for that claim type |

## Key Symbols

| Symbol | Location | Description |
|---|---|---|
| `FactCritic` | `knowledge/fact_critic.py` | Main fact critic class |
| `check_news_vs_price` | `knowledge/fact_critic.py` | News↔price validation |
| `check_prediction_vs_actual` | `knowledge/fact_critic.py` | Prediction validation |
| `check_regime_vs_performance` | `knowledge/fact_critic_v2.py` | Regime validation |
| `check_llm_claims` | `knowledge/fact_critic.py` | LLM claim validation |
| `generate_guardrail` | `knowledge/fact_critic.py` | Guardrail creation |
| `cross_reference_all` | `knowledge/cross_ref.py` | All cross-ref checks |
| `CrossRefResult` | `knowledge/cross_ref.py` | Cross-ref result type |
| `GUARDRAIL_THRESHOLDS` | `knowledge/fact_critic.py` | Validation thresholds |

## Test Coverage

- `tests/test_fact_critic.py` — Tests for all cross-reference checks
- `tests/test_fact_critic_v2.py` — Tests for v2 regime/performance checks
- `tests/test_guardrail_generation.py` — Tests for guardrail creation
- `tests/test_cross_ref.py` — Tests for cross-reference engine

## Relationship to Other Components

- [Knowledge System](overview.md) — Part of the knowledge system
- [Serenity Lens](../components/serenity-lens.md) — Regime validation
- [Fact Critic](fact-critic.md) — The component itself
- [Model Provider](../components/model-provider.md) — LLM claim validation
- [Market Observer](../components/market-observer.md) — News↔price validation
