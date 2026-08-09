---
type: component
title: Prediction Markets
description: Prediction market integration for probability-based trading signals and sentiment analysis.
tags: [prediction, markets, sentiment, polymarket, kalshi, probtrade]
---

# Prediction Markets

The **Prediction Markets** integration (`aletheia/data/prediction_markets.py`, `aletheia/components/prediction_markets/`) connects Aletheia to external prediction market platforms (Polymarket, Kalshi, ProbTrade) to source **probability-based trading signals** and alternative sentiment indicators. Prediction market prices often lead traditional market sentiment, providing early signals on macro events, earnings, and regulatory developments.

## Architecture

```mermaid
flowchart TD
    A[Polymarket API] --> B[Prediction Market\nCollector]
    C[Kalshi API] --> B
    D[ProbTrade API] --> B
    B --> E[Prediction Data DB\n(prediction_* tables)]
    E --> F[Fact Critic\n(Prediction ↔ Reality)]
    E --> G[Committee L2\n(Deep Analysis)]
    E --> H[Serenity Lens\n(Event risk)]
    B --> I[Sentiment Engine\n(Combined signals)]
    I --> J[Signal Weight\n(0-10% of portfolio)]
```

## Supported Platforms

| Platform | API Source | Market Types | Update Frequency |
|---|---|---|---|
| **Polymarket</span>** | `polymarket/` | Crypto events, macro, politics | 1 min |
| **Kalshi</span>** | `kalshi/` | Macro, weather, sports | 1 min |
| **ProbTrade</span>** | `probtrade/` | Earnings, events | 5 min |

### Polymarket

**Source:** `data/prediction_markets/polymarket.py`

Polymarket markets on:
- Crypto regulations (ETF approval, sanctions)
- Macro events (FED rate decisions, CPI)
- Politics (elections, policy outcomes)
- Technology events (hard forks, upgrades)

```python
class PolymarketCollector:
    """Collect prediction market data from Polymarket."""
    
    def fetch_markets(self) -> List[PredictionMarket]:
        """Fetch active prediction markets."""
        response = self.client.get("/markets")
        return [self.parse_market(m) for m in response["markets"]]
    
    def parse_market(self, raw) -> PredictionMarket:
        """Parse a Polymarket market into structured data."""
        return PredictionMarket(
            platform="polymarket",
            event=raw["question"],
            yes_price=raw["yesPrice"],
            no_price=raw["noPrice"],
            volume=raw["volume"],
            end_date=raw["endDate"],
            resolution=raw["resolution"],
            outcomes=raw["outcomes"]
        )
```

### Kalshi

**Source:** `data/prediction_markets/kalshi.py`

Kalshi markets on:
- Economic data releases (CPI, NFP, GDP)
- Weather events
- Sports outcomes
- Energy prices

### ProbTrade

**Source:** `data/prediction_markets/probtrade.py`

ProbTrade markets on:
- Corporate earnings
- Product launches
- M&A events

## Data Model

**Source:** `data/prediction_markets/schema.py`

```python
@dataclass
class PredictionMarket:
    platform: str          # polymarket, kalshi, probtrade
    event: str             # Market question/event
    yes_price: float       # Price of YES outcome (0.0-1.0)
    no_price: float        # Price of NO outcome (0.0-1.0)
    volume: float          # Trading volume
    end_date: datetime     # Market resolution date
    resolution: str        # How the market resolves
    outcomes: List[str]    # Possible outcomes
    confidence: float      # Aletheia's confidence in the signal
    last_updated: datetime

@dataclass
class PredictionSignal:
    symbol: str            # Related trading symbol
    market_event: str      # Related prediction market event
    probability: float     # Expected probability
    confidence: float      # Signal confidence
    direction: Direction   # Expected market direction
    timeframe: str         # Expected timeframe for outcome
    timestamp: datetime
```

## Signal Processing

**Source:** `data/prediction_markets/signals.py`

The signal processor converts prediction market prices into trading signals:

```python
def predict_to_signal(market: PredictionMarket, symbol: str) -> PredictionSignal:
    """Convert prediction market data to trading signal."""
    # Price represents probability
    prob = market.yes_price
    
    # High probability + high volume = stronger signal
    confidence = compute_signal_confidence(prob, market.volume)
    
    # Direction based on event impact
    direction = determine_direction(market.event, symbol)
    
    return PredictionSignal(
        symbol=symbol,
        market_event=market.event,
        probability=prob,
        confidence=confidence,
        direction=direction,
        timeframe="event: " + market.end_date.isoformat()
    )

def compute_signal_confidence(prob: float, volume: float) -> float:
    """Compute signal confidence from probability and volume."""
    # Probability distance from 0.5 (more certain)
    prob_confidence = abs(prob - 0.5) * 2
    
    # Volume scaling (logarithmic)
    volume_confidence = min(1.0, math.log10(volume + 1) / 5)
    
    return prob_confidence * 0.7 + volume_confidence * 0.3
```

## Integration with Analysis Layers

### Fact Critic (Prediction ↔ Reality)

**Source:** `knowledge/fact_critic.py::check_prediction_vs_actual`

After a prediction market resolves:
1. Compare predicted probability vs actual outcome
2. If error > threshold, create a guardrail
3. Adjust future signal confidence from that platform

### Serenity Lens

**Source:** `serenity/analysis/event_risk.py`

Prediction markets contribute to regime risk assessment:
- High volatility in prediction prices → "uncertain" regime
- Converging prediction prices → "clear direction" regime
- Diverging prediction prices → "conflicted" regime

### Committee

**Source:** `committee/l2_analysis.py`

Prediction signals are one input to the L2 analysis:
- Weight: 15% of the composite signal
- Weighted higher for event-driven opportunities
- Cross-referenced against news sentiment

## Database Schema

**Source:** `data/prediction_markets/schema.py`

| Table | Description |
|---|---|
| `prediction_markets` | Active prediction markets |
| `prediction_signals` | Processed trading signals |
| `prediction_history` | Historical market data |
| `prediction_validation` | Prediction vs actual outcomes |
| `prediction_platform_health` | API health tracking |

## Key Symbols

| Symbol | Location | Description |
|---|---|---|
| `PredictionMarketCollector` | `data/prediction_markets/collector.py` | Main collector |
| `PolymarketCollector` | `data/prediction_markets/polymarket.py` | Polymarket API |
| `KalshiCollector` | `data/prediction_markets/kalshi.py` | Kalshi API |
| `ProbTradeCollector` | `data/prediction_markets/probtrade.py` | ProbTrade API |
| `predict_to_signal` | `data/prediction_markets/signals.py` | Signal conversion |
| `compute_signal_confidence` | `data/prediction_markets/signals.py` | Confidence calc |
| `PredictionMarket` | `data/prediction_markets/schema.py` | Market dataclass |
| `PredictionSignal` | `data/prediction_markets/schema.py` | Signal dataclass |

## Test Coverage

- `tests/test_prediction_markets.py` — Tests for signal processing
- `tests/test_polymarket.py` — Tests for Polymarket collector
- `tests/test_kalshi.py` — Tests for Kalshi collector
- `tests/test_prediction_signals.py` — Tests for signal conversion
- `tests/test_prediction_validation.py` — Tests for prediction vs reality
- `tests/test_event_risk.py` — Tests for event risk in Serenity Lens

## Relationship to Other Components

- [Fact Critic](../knowledge-system/fact-critic.md) — Prediction validation
<!-- openwiki: broken internal link [serenity-lens.md] file "serenity-lens.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- [Serenity Lens](serenity-lens.md) — Event risk assessment
<!-- openwiki: broken internal link [committee.md] file "committee.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- [Investment Committee](committee.md) — L2 analysis input
- [Data Collector Subprocess](../architecture/data-collector-subprocess.md) — Collection subprocess
<!-- openwiki: broken internal link [knowledge-base.md] file "knowledge-base.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- [Knowledge Base](knowledge-base.md) — Learning from prediction accuracy
