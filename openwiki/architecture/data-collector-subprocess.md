---
type: workflow
title: Data Collector Subprocess
description: Standalone market data collection subprocess with 9 collectors and health monitoring.
tags: [workflow, data, collectors]
---

# Data Collector Subprocess

Market data is collected by a **standalone subprocess** for reliability isolation. The main gateway spawns the collector runner, and health is monitored by `data/collector_health.py`.

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
flowchart LR
    A[Scheduler<br/>(collect_market_data)] --> B[Collector Runner<br/>Subprocess]
    B --> C[Collector Pool<br/>6 threads]
    C --> D1[CCXT Provider<br/>OHLCV + Fallback]
    C --> D2[Coinalyze<br/>OI + Funding]
    C --> D3[Derivatives<br/>Derivative metrics]
    C --> D4[Macro<br/>Macro data]
    C --> D5[Microstructure<br/>Orderbook depth]
    C --> D6[Volume Scanner<br/>Volume spikes]
    C --> D7[Volatility<br/>Volatility metrics]
    C --> D8[Hyperliquid<br/>Perp data]
    C --> D9[Verge Structure<br/>Structure obs]
    B --> E[News Collector<br/>RSS feeds]
    B --> F[News Analyzer<br/>LLM sentiment]
    D1 --> G[SQLite<br/>market_data table]
    D2 --> G
    D3 --> G
    D4 --> G
    D5 --> G
    D6 --> G
    D7 --> G
    D8 --> G
    D9 --> G
    E --> H[SQLite<br/>news table]
    F --> H
    B --> I[Health Monitor]
    I -->|stale/ok| J[Scheduler<br/>resume jobs]
    I -->|crisis| K[Gateway restart]
```

## Collector Runner

**Source:** `aletheia/data/collector_runner.py`

The collector runner is a standalone subprocess invoked by the scheduler job `collect_market_data`. It:

1. Bootstraps collectors (CCXT auth, API keys from keyring)
2. Runs collectors in parallel using the `collectors_pool` executor (6 threads)
3. Monitors freshness — each collector writes a freshness timestamp
4. Handles venue cooldowns — if a venue fails, it's cooldown for N seconds
5. Reports health status to `data/collector_health.py`

## The 9 Market Data Collectors

| Collector Module | Data Collected | Output Table |
|---|---|---|
| `collectors/ccxt_provider.py` | OHLCV (1m-1d), ticker, order book, funding | `market_data` |
| `collectors/coinalyze.py` | Open interest, funding rates, long/short ratio | `market_data_oi`, `market_data_funding` |
| `collectors/derivatives.py` | Perpetual contract specifications, expiry data | `market_data_derivatives` |
| `collectors/macro.py` | VIX, DXY, yield curves, economic calendar | `market_data_macro` |
| `collectors/microstructure.py` | Orderbook depth, bid-ask spread, order flow | `market_data_microstructure` |
| `collectors/volume_scanner.py` | Unusual volume spikes across symbols | `market_data_volume` |
| `collectors/volatility_scanner.py` | Implied vs realized volatility, IV skew | `market_data_volatility` |
| `collectors/hyperliquid.py` | Hyperliquid perp data, L2 orderbook | `market_data_hyperliquid` |
| `collectors/verge_structure.py` | Verge structure, funding, open interest | `market_data_verge` |

## News Pipeline

| Component | Source | Output |
|---|---|---|
| `news_collector.py` | RSS feeds (CoinDesk, CoinTelegraph, etc.) | `news` table (raw headlines) |
| `news_analyzer.py` | LLM sentiment + entity extraction | `news` table (enriched: sentiment, symbols) |
| `news_entities.py` | Deterministic crypto entity matching | `news_entities` table |

News collection runs every 10 min. News analysis uses the LLM pool.

## Health Monitoring

**Source:** `data/collector_health.py`

- Tracks per-collector: last success, last attempt, failure count, error message
- Freshness check: if a collector hasn't succeeded in the freshness window (default 180s), it's marked stale
- Venue cooldown: `data/venue_health.py` tracks exchange API health — failed venues are cooldown for the cooldown period (default 300s)
- Crisis detection: if >50% of collectors are stale, the runner reports a crisis — gateway restart triggered
- Recovery: collector runner auto-restarts individual collectors on failure (max 3 retries)

## Data Freshness

**Source:** `data/freshness.py`

- Validates that market_data snapshots are no older than 30s (configurable per symbol)
- Stale symbols are excluded from signal generation
- Freshness is checked before each universe refresh cycle

## Provider Fallback

**Source:** `data/ccxt_provider.py`

The CCXT provider implements a primary + fallback strategy:

1. Primary: Binance (for USDT pairs), Coinbase (for USD pairs)
2. Fallback: KuCoin -> OKX -> Bybit (if primary fails or rate-limited)
3. Each provider has its own rate limit budget
4. Provider selection is logged for cost/regret analysis

## Key Symbols

| Symbol | Location | Description |
|---|---|---|
| `run_collectors` | `data/collector_runner.py` | Main collector subprocess entry |
| `CollectorHealth` | `data/collector_health.py` | Collector health tracker |
| `check_freshness` | `data/freshness.py` | Snapshot freshness validation |
| `VenueHealth` | `data/venue_health.py` | Exchange/API venue health |
| `CCXTProvider` | `data/ccxt_provider.py` | CCXT provider with fallback |
| `collect_news` | `data/news_collector.py` | RSS news collection |
| `analyze_news` | `data/news_analyzer.py` | LLM news analysis |

## Test Coverage

- `tests/test_data_collectors.py` — Tests for collector runner, health monitoring
- `tests/test_ccxt_provider.py` — Tests for CCXT provider with fallback
- `tests/test_news_collector.py` — Tests for RSS collection
- `tests/test_news_analyzer.py` — Tests for sentiment analysis
- `tests/test_freshness.py` — Tests for freshness checks
- `tests/test_venue_health.py` — Tests for venue cooldown
