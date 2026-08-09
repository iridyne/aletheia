---
type: overview
title: Aletheia System Overview
description: Local-first AI-native agentic trading system. Paper by default, live trading behind explicit human unlock.
tags: [overview, architecture]
---

# Aletheia Trading OS — System Overview

Aletheia is a **local-first, file-based, AI-native, self-learning agentic trading system**. It runs entirely on the user's machine, with all state persisted to a local SQLite database (`app.db`, WAL mode) inside the runtime data directory (`~/.aletheia` or `ALETHEIA_DATA_DIR`).

## Design Philosophy

1. **Paper by default, live behind explicit human unlock.** All trading starts in paper mode. Live exchange trading is gated behind a multi-step unlock requiring a typed confirmation phrase. The Risk Engine kill switch defaults to off for live.
2. **Durable evidence loop.** Market facts → signals → decisions → risk verdicts → fills → outcomes → reviews → learning evidence — all remain traceable in local state.
3. **File-based persistence + knowledge base.** Trading knowledge, lessons, and guardrails are stored as Markdown files in `~/.aletheia/knowledge/` and indexed with SQLite FTS5.
4. **Deterministic by default, LLM-augmented.** Candidate ranking, risk checks, and order construction are deterministic. LLMs are used for market analysis, news sentiment, and committee deliberation — behind rate limits (inference broker) and failover (fallback chain).

## Roles

| Role | Description | System entry point |
|---|---|---|
| **Trader** | Human operator — configures, unlocks live trading, reviews recommendations | Dashboard Settings, `aletheia gateway start` |
| **System Operator** | The sole entry-decision authority — evaluates candidate signals with the Investment Committee and produces `pi_decision` records | `agent_runtime/pi_daemon.py` → `agent_shell/trading_decision.py` |
| **Market Observer** | Per-symbol LLM market analysis producing Opportunity rows | `agents/market_observer.py` |
| **Paper Trader** | Deterministically ranks candidate opportunities into paper positions | `agents/paper_trader.py`, `paper_trader_ranker.py` |
| **Dreaming Agent** | Daily consolidation → daily review + dreaming logs | `agents/dreaming_agent.py` |
| **Committee Agents** | Multi-agent investment committee (trend, macro, news, risk, rules, LLM) | `agents/committee/` |
| **Learning System** | AT-01 measurement experiments that gate whether learned behavior can influence trades | `learning/` |

## High-Level Data Flow

```mermaid
flowchart TB
    subgraph Data "Data Collection"
        A[Market Data\nCollectors] -->|OHLCV, funding, OI| B[SQLite: market_data]
        C[News RSS] -->|Headlines| D[News Analyzer\nLLM: sentiment, symbols]
        E[Polymarket/yFinance] -->|Macro, predictions| B
    end

    subgraph Signal "Signal Generation"
        B --> F[Universe Service\nScanners + Features]
        F --> G[Signal Emitter\nDeterministic: Opportunity]
        G --> H[Paper Trader\nRank candidates]
    end

    subgraph Decision "Decision Layer"
        H --> I[System Operator\n(pi_daemon)]
        J[Investment\nCommittee] --> I
        K[Knowledge\nGuards] --> I
        L[Risk Engine] --> I
        I -->|pi_decision| M[Execution\nGateway]
    end

    subgraph Execution "Execution & Portfolio"
        M -->|TradeProposal| L
        L -->|risk verdict| M
        M -->|Order| N[Broker\n(Paper / Bitget / Binance / OKX)]
        N -->|Fill| O[Portfolio\nPosition Manager]
        O -->|PnL| P[Learning\n(AT-01)]
        O -->|Outcome| Q[Post-Trade\nReviewer]
        Q -->|Lesson| R[Knowledge Base]
        R -->|Guards| K
    end

    subgraph Runtime "Runtime"
        S[Gateway] -->|start/stop| T[FastAPI Server]
        U[APScheduler\nScheduler] -->|jobs| F
        U -->|jobs| I
        U -->|jobs| P
        U -->|jobs| Q
        V[Frontend\nDashboard] --> T
        W[Operator\nShell] --> T
    end
```

## Architecture Layers

The system is organized vertically into layers — see [Architecture Layers](layers.md) and the [trading loop](trading-loop.md) for the full pipeline.

| Layer | Package | Responsibility |
|---|---|---|
| **Foundation** | `aletheia/` (root) | CLI, gateway runner, async utils, net, logging, packaging |
| **Gateway** | `aletheia/gateway/` | Lifecycle, PID management, front-door proxy, OS service, tunnel |
| **Server/API** | `aletheia/server/` | FastAPI app, lifespan, 24 route modules |
| **Agent Runtime** | `aletheia/agent_runtime/` | Scheduler, health, permissions, PI daemon, startup gate |
| **Agent Shell** | `aletheia/agent_shell/` | Operator decision loop, context, command executor |
| **Agents** | `aletheia/agents/` | Registered trading agents (observer, trader, reviewer, etc.) |
| **Risk** | `aletheia/risk/` | Risk engine, kill switch, policy |
| **Execution** | `aletheia/execution/` | Gateway, paper/bitget/binance/okx brokers |
| **Exchanges** | `aletheia/exchanges/` | CCXT wrappers + native clients (Bitget, Binance, OKX) |
| **Data** | `aletheia/data/` | Market data, news, collectors, providers, models |
| **Universe** | `aletheia/universe/` | Scanners, features, filters, allocation |
| **Portfolio** | `aletheia/portfolio/` | Position management, PnL, paper account |
| **Storage** | `aletheia/storage/` | SQLite, models, repositories, migrations |
| **Config** | `aletheia/config/` | Loader, schema, secrets |
| **Model Provider** | `aletheia/model_provider/` | Provider router, broker, fallback |
| **Knowledge** | `aletheia/knowledge/` | Knowledge manager, retrieval, guards |
| **Learning** | `aletheia/learning/` | AT-01 experiments, lifecycle, authority |
| **Analytics** | `aletheia/analytics/` | Performance summaries, attribution, diagnostics |
| **Counterfactual** | `aletheia/counterfactual/` | Shadow trades, propensity, calibration |
| **Regime** | `aletheia/regime/` | Bull/Bear/Sideways/Volatile detection |
| **Economics** | `aletheia/economics/` | Inference costs, budgets, viability |
| **Runtime Continuity** | `aletheia/runtime_continuity/` | Shadow/candidate/promote/rollback |
| **Diagnostics** | `aletheia/diagnostics/` | Doctor probes, pipeline progress, config consistency |
| **Observability** | `aletheia/observability/` | LLM metrics, health monitoring |
| **Files** | `aletheia/files/` | JSONL, markdown, git helpers |
| **Infra** | `aletheia/infra/` | Events, errors, error summary |
| **Frontend** | `frontend/src/` | React/Vite dashboard |
| **Operator Shell** | `operator-shell/` | TypeScript operator shell |

## Entry Points

```
uv run aletheia                        # CLI (Typer), aliased as: ato
uv run aletheia gateway start          # Start background gateway + server + tunnel
uv run aletheia gateway status         # Check gateway state
uv run aletheia gateway stop           # Stop everything
uv run aletheia doctor                 # Diagnose machine state
uv run aletheia inspect ...            # Inspect runtime state
uv run aletheia migrate                # Run DB migrations
uv run aletheia knowledge ...          # Knowledge base commands
uv run aletheia audit verify           # Verify audit hash chain
uv run aletheia model ...              # LLM provider management
uv run aletheia bitget ...             # Exchange commands
uv run aletheia risk ...               # Risk control commands
```
