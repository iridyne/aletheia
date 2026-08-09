---
type: component
title: Market Observer
description: Multi-layer market analysis with pattern detection, indicator computation, and signal generation.
tags: [market, observer, analysis, patterns, indicators, signals]
---

# Market Observer

The **Market Observer** (`aletheia/market_observer/`) is Aletheia's market analysis engine. It performs multi-timeframe, multi-dimensional analysis of market data, including pattern detection, technical indicator computation, and signal generation. The Observer feeds analysis results to the Investment Committee and the Serenity Lens.

## Architecture

```mermaid
flowchart TD
    A[Market Data] --> B[Market Observer]
    B --> C1[Pattern Detector\n(LOFO, Verge)]
    B --> C2[Indicator Engine\n(RSI, MACD, etc.)]
    B --> C3[Signal Generator\n(Composite scores)]
    C1 --> D[Pattern DB]
    C2 --> D
    C3 --> D
    D --> E[Observation\nDatabase]
    E --> F[Committee L2\n(Deep Analysis)]
    E --> G[Fact Critic\n(Cross-reference)]
    C1 --> H[Structure\nAnalyzer]
    H --> I[Verge\nDetector]
```

## Analysis Layers

### Layer 1: Pattern Detection

**Source:** `market_observer/patterns/lofo.py`, `market_observer/patterns/verge.py`

Pattern detection runs two primary systems:

#### LOFO (Local Factor Optimization)

**Source:** `market_observer/patterns/lofo.py`

LOFO detects local regime changes by analyzing:
- Short-term momentum deviation from medium-term trend
- Volume spike confirmation
- Volatility contraction before breakout

```python
def detect_lofo_signal(symbol, data):
    """Detect LOFO pattern for a symbol."""
    short_momentum = compute_momentum(data, window=5)
    medium_trend = compute_ema(data, window=21)
    deviation = (short_momentum - medium_trend) / medium_trend
    
    volume_spike = detect_volume_spike(data)
    vol_contraction = detect_volatility_contraction(data)
    
    if abs(deviation) > 0.03 and volume_spike and vol_contraction:
        return PatternSignal(
            type="LOFO",
            direction=Direction.UP if deviation > 0 else Direction.DOWN,
            confidence=min(abs(deviation) * 20, 1.0),
            timeframe="2h"
        )
```

#### Verge

**Source:** `market_observer/patterns/verge.py`

Verge detects structural breakout/breakdown setups:
- Support/resistance level testing
- Order book depth analysis
- Recent price action structure

### Layer 2: Technical Indicators

**Source:** `market_observer/indicators/compute.py`

Computes standard technical indicators across multiple timeframes:

| Indicator | Timeframes | Purpose |
|---|---|---|
| `RSI` | 1m, 5m, 15m, 1h, 4h, 1d | Momentum/Oversold-oversold |
| `MACD` | 1h, 4h, 1d | Trend direction/momentum |
| `EMA` | 9, 21, 50, 200 | Moving average trends |
| `Bollinger Bands` | 1h, 4h, 1d | Volatility bands |
| `ATR` | 1h, 4h, 1d | Volatility measure |
| `Volume Profile` | 1h, 4h | Price-volume analysis |
| `Fibonacci Retracement` | 4h, 1d | Support/resistance levels |
| `VWAP` | 1m, 5m, 15m | Volume-weighted average price |

### Layer 3: Signal Generation

**Source:** `market_observer/signals/generator.py`

Combines pattern and indicator analysis into composite signals:

```python
def generate_signal(symbol, observations):
    """Generate composite signal from observations."""
    pattern_score = weight_pattern(observations.patterns)
    indicator_score = weight_indicators(observations.indicators)
    structure_score = weight_structure(observations.structure)
    volume_score = weight_volume(observations.volume)
    
    composite = (
        pattern_score * 0.3 +
        indicator_score * 0.3 +
        structure_score * 0.2 +
        volume_score * 0.2
    )
    
    return Signal(
        symbol=symbol,
        confidence=composite,
        direction=Direction.UP if composite > 0.1 else 
                   Direction.DOWN if composite < -0.1 else 
                   Direction.NEUTRAL,
        timeframe=observations.timeframe
    )
```

## Observation Aggregation

**Source:** `market_observer/observations.py`

Observations are aggregated across timeframes:

```python
class Observation:
    symbol: str
    timeframe: str
    timestamp: datetime
    patterns: List[PatternSignal]
    indicators: Dict[str, float]
    signals: List[Signal]
    confidence: float

class ObservationStore:
    """Stores observation results."""
    
    def add_observation(obs: Observation):
        """Store an observation."""
        ...
    
    def get_observations(symbol, timeframe, lookback):
        """Retrieve historical observations."""
        ...
```

## Multi-Timeframe Analysis

The Observer analyzes each symbol across multiple timeframes:

| Resolution | Timeframes | Primary Use |
|---|---|---|
| **Tick** | 1m | Entry/exit timing |
| **Intraday** | 5m, 15m | Short-term momentum |
| **Daily** | 1h, 4h, 1d | Trend direction |
| **Weekly** | 1d | Position sizing |

## Structure Analyzer

**Source:** `market_observer/structure/analyzer.py`

The Structure Analyzer identifies:
- Support/resistance levels (swing highs/lows)
- Chart patterns (triangles, rectangles, flags)
- Breakout/breakdown confirmation
- False breakout detection

## Key Symbols

| Symbol | Location | Description |
|---|---|---|
| `MarketObserver` | `market_observer/core.py` | Main observer class |
| `detect_lofo_signal` | `market_observer/patterns/lofo.py` | LOFO pattern detection |
| `detect_verge_signal` | `market_observer/patterns/verge.py` | Verge pattern detection |
| `compute_indicators` | `market_observer/indicators/compute.py` | Indicator computation |
| `generate_signal` | `market_observer/signals/generator.py` | Signal generation |
| `Observation` | `market_observer/observations.py` | Observation dataclass |
| `ObservationStore` | `market_observer/observations.py` | Observation storage |
| `StructureAnalyzer` | `market_observer/structure/analyzer.py` | Structure analysis |

## Test Coverage

- `tests/test_market_observer.py` — Tests for observer core
- `tests/test_lofo_patterns.py` — Tests for LOFO detection
- `tests/test_verge_patterns.py` — Tests for Verge detection
- `tests/test_indicators.py` — Tests for technical indicators
- `tests/test_signal_generator.py` — Tests for signal generation
- `tests/test_structure_analyzer.py` — Tests for structure analysis

## Relationship to Other Components

- [Investment Committee](committee.md) — Feeds L2 deep analysis
- [Serenity Lens](serenity-lens.md) — Pattern context for regime analysis
<!-- openwiki: broken internal link [fact-critic.md] file "fact-critic.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- [Fact Critic](fact-critic.md) — Cross-references market signals
- [Position Manager](position-manager.md) — Signals for position decisions
- [Data Collector Subprocess](../architecture/data-collector-subprocess.md) — Market data source
