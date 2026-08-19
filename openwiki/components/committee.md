---
type: component
title: Investment Committee
description: Multi-agent evaluation system (Trend/Macro/News/Risk + LLM) that produces structured, auditable trading opinions with rule-gated explainability and quality-aware position sizing.
tags: [committee, decisions, evaluation, capital-allocation, explainability]
---

# Investment Committee

The **Investment Committee** (`aletheia/agents/committee/`) is Aletheia's multi-agent decision-making layer. It evaluates market opportunities through specialized agents that each produce structured, evidence-backed opinions. A deterministic rule engine gates decisions, and two post-decision quality adjustments (short penalty + UNI variance cap) are applied to position sizing. Committee output is advisory and persisted to the append-only `committee_reports` table; the AI Jury page (`frontend/src/pages/Committee.tsx`) renders it via the read-only `/api/committee/reports` route.

## Architecture

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
flowchart TD
    A[Universe Candidates] --> B[InvestmentCommittee.evaluate / evaluate_with_llm]
    B --> C1[Trend Agent]
    B --> C2[Macro Agent]
    B --> C3[News Agent]
    B --> C4[Risk Agent]
    B --> C5[LLM Agent<br/>(tokens + reasoning)]
    C1 --> D[Decision Core / Rule Engine]
    C2 --> D
    C3 --> D
    C4 --> D
    C5 --> D
    D --> E[Quality Adjustments<br/>short penalty / UNI cap]
    E --> F[DecisionReport]
    F --> G[Persisted committee_reports]
    G --> H[AI Jury UI / DecisionTraceDrawer]
```

## Committee Modes

`InvestmentCommittee` exposes two evaluation paths:

- **`evaluate(symbol, market_type, features, ...)`** — deterministic, no LLM. Runs the rule-based Trend/Macro/News/Risk agents (`trend_agent.py`, `macro_agent.py`, `news_agent.py`, `risk_agent.py`) and aggregates them via `decide()` in `decision_core.py`.
- **`evaluate_with_llm(symbol, market_type, ...)`** — LLM-powered. Calls the model provider through `llm_agent.py`, parses structured output (`direction`, `confidence`, `evidence`, `reasoning`, `position_size_factor`), then runs the rule engine and applies quality adjustments.

## Position Sizing Quality Adjustments

After the rule engine clears, `committee.py` applies two empirical sizing adjustments before persisting:

| Adjustment | Source field | Value | Rationale |
|---|---|---|---|
| **Short penalty** | `position_size_factor` | × `0.5` when `direction == "short"` | Historical shorts lost −134.54 vs longs +545.88; halves the dominant loss source while preserving high-conviction short access. |
| **UNI variance cap** | `position_size_factor` | × `0.6` when symbol starts with `UNI` | UNI produced both the biggest win (+423.72) and biggest loss (−232.40) — cap keeps a single symbol from dominating the portfolio. |

Constants: `SHORT_SIZE_MULTIPLIER = 0.5`, `UNI_SIZE_MULTIPLIER = 0.6`.

## Rule Engine

**Source:** `aletheia/agents/committee/rule_engine.py`

Deterministic post-decision checks over the preliminary `DecisionReport`. Rules can `warn`, or `block` entirely. Default set:

| Rule | Severity | Condition |
|---|---|---|
| `min_confidence` | block | `final_confidence >= 0.3` |
| `agent_disagreement` | warning | no agent conflict |
| `min_evidence_total` | block | `total_evidence_count >= 1` (was 3, lowered to match the LLM committee's single-evidence-per-opinion contract) |
| `explainability_floor` | block | `explainability_score >= 0.4` |
| `position_size_cap` | block | `position_size_factor <= 0.5` |
| `position_size_floor` | warning | `>= 0.05` unless neutral |
| `high_risk_veto` | block | Risk agent high-risk veto |
| `max_daily_trades` | block | daily trade count `< 20` |

`run_rules(decision_report, extra_context=None, rules=None)` returns a `list[RuleCheckResult]`.

## Parallel Enrichment

**Source:** `aletheia/agents/committee/integration.py`

`enrich_candidates_parallel(candidates, provider, max_concurrency, total_timeout)` bounds the whole LLM batch with `asyncio.wait_for` so a slow/straggling provider cannot hang the scheduler's single-threaded async worker. On timeout, stragglers are cancelled, finished + un-enriched candidates are returned, and the caller degrades gracefully.

Default `total_timeout=240.0` and `max_concurrency=3` in `paper_trader.py` (down from 10) keep CPU headroom for API responses while the 5-minute cadence sweeps the full queue over time.

## Committee Reports

Evaluations are persisted to the append-only `committee_reports` table. Two read paths consume them:

- **`analytics/committee_overview.py::assemble_committee_overview(symbol, limit)`** — parses rows into structured opinions/evidence/risks plus a window summary (direction mix, avg confidence, avg explainability, blocked/fallback counts, model usage). Read failures degrade to `status="read_failed"` — never benign zeros.
- **`server/routes/data.py::get_committee_reports`** — thin route over `assemble_committee_overview` at `GET /api/committee/reports`. Advisory only: no writes, no trade/risk influence.
- **`frontend/src/pages/Committee.tsx`** — standalone AI Jury workbench (`/committee` route) that renders the parsed evaluations and window summary.

## Key Symbols

| Symbol | Location | Description |
|---|---|---|
| `InvestmentCommittee` | `agents/committee/committee.py` | Main orchestrator (`evaluate`, `evaluate_with_llm`) |
| `DecisionReport` | `agents/committee/base.py` | Auditable decision report dataclass |
| `AgentOpinion` | `agents/committee/base.py` | Structured agent output |
| `EvidenceItem` | `agents/committee/base.py` | One cited data point |
| `Rule` / `RuleCheckResult` | `agents/committee/rule_engine.py` | Deterministic rule definitions |
| `run_rules` | `agents/committee/rule_engine.py` | Run default rule set |
| `decide` | `agents/committee/decision_core.py` | Aggregate agent opinions into a decision |
| `enrich_candidates_parallel` | `agents/committee/integration.py` | Bounded parallel LLM enrichment |
| `assemble_committee_overview` | `analytics/committee_overview.py` | AI Jury payload assembly |
| `get_committee_reports` | `server/routes/data.py` | Read-only committee reports endpoint |

## Test Coverage

- `tests/test_committee_overview_endpoint.py` — Read-only endpoint contract (newest-first, JSON parsing, symbol filter, read-mutates-nothing)
- `tests/test_committee_confidence_display.py` — Confidence display contract
- `tests/test_committee_llm_path.py` — LLM committee path
- `tests/test_paper_trader_guards.py`, `tests/test_ranker_*.py` — Paper trader/ranker guards

## Relationship to Other Components

- [Serenity Lens](serenity-lens.md) — Regime assessment context for committee evaluation
- [Risk Engine](risk-engine.md) — Portfolio/position risk checks beyond committee advisory scope
<!-- openwiki: broken internal link [components/knowledge-base.md] file "components/knowledge-base.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- [Knowledge Base](components/knowledge-base.md) — Guardrails consulted alongside committee decisions
- [Market Observer](market-observer.md) — Per-symbol LLM analysis feeding candidates
- [Position Manager](position-manager.md) — Receives approved proposals downstream of committee
<!-- openwiki: broken internal link [components/learning-system.md] file "components/learning-system.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- [Learning System](components/learning-system.md) — AT-01 measurement of committee outcomes
<!-- openwiki: broken internal link [components/model-provider.md] file "components/model-provider.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- [Model Provider](components/model-provider.md) — LLM routing for `evaluate_with_llm`
- [AI Jury Workbench](../frontend/overview.md#ai-jury-committee-workbench) — Read-only UI rendering committee data
