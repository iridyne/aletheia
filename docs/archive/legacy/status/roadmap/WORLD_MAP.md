# Aletheia Trading OS — Core Task World Map

> Source: External AI consultation (2026-06-08)
> Role: Architecture reference — full system vision for module design and data contracts.
> NOT an execution plan. See ROADMAP.md for phased implementation.
>
> ⚠️ **目标态架构,非当前状态(2026-06-21 标注):** 下面的 9 步认知循环是**理想形态**,
> 当前代码尚未全部走完(如 Synthesize/Evolve 仍在路上)。这是金字塔的 [L2 契约层](../../../../product/documentation/DOC_SYSTEM_PLAN.md)
> "系统该是什么样"的总览;细节契约见 [docs/design/](../../../../design/contracts),现状看 `aletheia doctor`。

---

## Trading Cognitive Loop (Target Architecture)

```
1. Sense     — Market, news, portfolio, strategy state, external knowledge
2. Normalize — Convert noisy input into typed events and facts
3. Synthesize — Link evidence, detect patterns, classify regime, score relevance
4. Discover  — Generate opportunities and strategy hypotheses
5. Decide    — Evaluate with Strategy Architect, Risk Critic, Portfolio Director
6. Execute   — Paper/demo/live-limited execution through controlled gateways
7. Review    — Evaluate trade, strategy, agent, risk, and data quality
8. Dream     — Consolidate memory, update lessons, generate new experiments
9. Evolve    — Advance or retire strategies through lifecycle gates
```

---

## Module Map

```
0.  Gateway & Runtime
1.  Storage & Event Bus
2.  Market Data Layer
3.  Scanners
    3.1 Crypto Core Scanner
    3.2 RWA / xStocks Scanner
    3.3 Prediction Market Scanner
    3.4 News Intelligence Scanner
    3.5 Strategy Learning Scanner
4.  Intelligence Synthesizer
5.  Opportunity & Hypothesis Engine
6.  Strategy Lifecycle Engine
7.  Decision Layer
    7.1 Opportunity Analyst
    7.2 Strategy Architect
    7.3 Risk Critic
    7.4 Portfolio Director
8.  Risk Engine
9.  Execution Layer
    9.1 Execution Planner
    9.2 Execution Gateway
    9.3 Bitget Adapter
    9.4 Paper/Demo Broker
10. Ledger & Portfolio State
11. Review Engine
12. Dreaming / Knowledge Engine
13. Model Provider Layer
14. Dashboard / CLI / Telegram Integration
15. Observability, Audit, Safety
```

---

## Key Data Contracts

All objects include: id, created_at, source, status, audit trail pointer.
Agent-generated objects add: agent_id, agent_run_id, model_id, confidence, evidence_ids, reasoning_summary.

Core entities:
- SystemStatus, RuntimeState, PermissionMode
- MarketSnapshot, MarketSignal, CryptoRegimeSignal
- NewsEvent, EventClassification
- ResearchNote, Opportunity, OpportunityCluster
- StrategyHypothesis, Strategy, StrategyLifecycleEvent, StrategyEdit
- TradeProposal, RiskDecision, ExecutionPlan
- Order, Fill, Position, PortfolioSnapshot
- ReviewReport, TradeOutcome, DreamingRun
- KnowledgeItem, AuditEvent, AgentRun, AgentToolCall
- DataQualityReport, NoActionReason

---

## Decision Layer Roles

| Role | Responsibility | Outputs |
|---|---|---|
| Opportunity Analyst | Interpret opportunities | watch / ignore / create_hypothesis / create_proposal / request_more_data |
| Strategy Architect | Design strategy experiments | strategy_draft / paper_experiment_config / parameter_space / validation_plan |
| Risk Critic | Attempt to disprove ideas | risk_objection / risk_warning / reject_reason / risk_mitigation |
| Portfolio Director | Final pre-risk-engine recommendation | NoActionDecision / TradeProposal / ExitProposal / ReduceExposureProposal |

---

## Intelligence Synthesizer (Target)

Inputs: MarketSignal, NewsEvent, ResearchNote, DataQualityReport, PortfolioState, StrategyMemory, RiskLessons

Core tasks:
- Deduplication, asset linking, time alignment
- Signal confidence scoring, source credibility weighting
- Regime classification, cross-market relation detection
- Risk pre-filtering

Outputs: Opportunity, OpportunityCluster, MarketRegime, Insight, Contradiction, NoActionReason

---

## Do-Not-Break List

- Scanners never place orders
- Agent never bypasses Risk Engine
- Agent never edits live mode automatically
- Agent never edits hard risk limits automatically
- API keys never enter model context
- No Docker required for normal use
- Tradable symbols: BTC/ETH/SOL only (MVP)
- No prediction market or RWA trading in MVP
- Frontend shell unchanged unless explicitly requested

---

## Reference Workflows

### Normal Market Scan
Scanner -> MarketSnapshot + MarketSignals -> Data Quality -> Intelligence Synthesizer -> Opportunity Engine -> Dashboard

### Trade Proposal
Opportunity -> Analyst -> Architect -> Critic -> Director -> Risk Engine -> Execution Gateway -> Ledger

### Strategy Learning
External Source -> Evidence -> Research Note -> Hypothesis -> Paper Candidate -> Evaluation -> Promotion/Rejection

### Review & Dreaming
Daily Trigger -> Trade/Strategy/Risk Reviews -> Dreaming Agent -> Knowledge Base -> New Hypotheses -> Lifecycle

---

> Full module specifications preserved from source document.
> Implementation sequence governed by ROADMAP.md phases and observation gates.
