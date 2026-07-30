# Product Evaluation Metrics

> Single source of truth for what we measure and why.
> Each metric is tied to a concrete code path or DB query.
> Last updated: 2026-06-21

---

## 1. Primary Outcome Metric

**Closed-Loop Learning Velocity** -- how fast the system discovers, validates, and discards trading hypotheses.

Decomposed into 4 sub-dimensions:

| Dimension | Metric | Code path |
|-----------|--------|-----------|
| Observation yield | Opportunities with substantive key_observations / total | `agents/market_observer.py` output quality |
| Knowledge consumption | `lessons_consulted > 0` rate across PI decisions | `dreaming_agent.py:115-118` FTS5 lookup |
| Strategy improvement | Strategies that pass walk-forward / total proposed | `strategy_lifecycle.py` promotion gates |
| Profitability proof | Paper profit factor > 1.0 over rolling 30d | `analytics/trade_insights.py` |

### Guardrail Metrics (never sacrifice for velocity)

| Guardrail | Threshold | Source |
|-----------|-----------|--------|
| System Uptime | >99% (7d rolling) | `compute_health()` in `health.py` |
| Max Drawdown | <15% of peak equity | `_check_max_drawdown()` in `go_live_scorecard.py` |
| Confidence Calibration | ECE < 0.15 | `confidence_calibration.compute_calibration_score()` |

---

## 2. Phase 0 KPIs (Stabilize)

| KPI | Target | How to measure |
|-----|--------|----------------|
| Gateway Uptime | >95% (no multi-hour outages) | `aletheia doctor` gateway_status history |
| Doctor Zero-Critical | 0 critical issues for 3 consecutive days | `doctor` output parsing |
| Signal Coverage | >=3 symbols producing snapshots | `SELECT DISTINCT symbol FROM market_snapshots WHERE created_at > datetime('now','-1 hour')` |
| Agent Error Rate | <5% of agent runs | `SELECT COUNT(*) FROM agent_runs WHERE status='error' / total` |

---

## 3. Phase 1 KPIs (Learning Quality)

| KPI | Target | How to measure |
|-----|--------|----------------|
| Lessons Consulted Rate | >50% of PI decisions reference a lesson | Agent run outputs containing `lessons_consulted:N` where N>0 |
| Confidence Calibration | ECE < 0.15 | `analytics/confidence_calibration.py` -- formula: `max(0, 100 - ECE*200)` |
| Memory Produce Rate | >0 new knowledge_items/day | `SELECT COUNT(*) FROM knowledge_items WHERE created_at > datetime('now','-1 day')` |
| Post-Trade Review Coverage | >80% of closed positions have a review | `knowledge_items.item_type='post_trade_review'` / closed positions |

---

## 4. Phase 2 KPIs (Strategy Execution)

| KPI | Target | How to measure |
|-----|--------|----------------|
| Paper Win Rate | >=45% | Closed positions with realized_pnl > 0 / total closed |
| Average R (reward/risk) | >1.5 | `realized_pnl / abs(entry_price - stop_price) * qty` per trade |
| Sharpe Ratio | >0.5 (annualized from daily returns) | `portfolio_snapshots` time series |
| Profit Factor | >=1.2 | sum(winning trades) / abs(sum(losing trades)) |
| Strategy Turnover | >=1 new strategy/month reaching paper_running | `strategies` table status transitions |

---

## 5. Phase 3+ KPIs (Self-Evolution)

| KPI | Target | How to measure |
|-----|--------|----------------|
| Parameter Feedback Loop Active | lessons influence next decisions | `reasoning_feedback.py` -- rejection_rate/win_rate by bucket |
| Strategy Iteration Cycle | <7d from proposal to walk-forward result | `strategy_edits` timestamp delta |
| Guard Candidate Auto-Generation | >0/week | `outcome_recorder.py:249` -- 7-day dedup window |
| Activation Ratio | >30% | `paper_running + paper_passed / total strategies` |

---

## 6. Phase 6 Go-Live KPIs (Composite Score)

The Go-Live Scorecard produces a composite score (0-100). Target: >=70.

| # | Criterion | Weight | Threshold | Score formula |
|---|-----------|--------|-----------|---------------|
| 1 | Trade Volume | 0.135 | >=50 closed | linear 0-100 scaled to target |
| 2 | Profit Factor | 0.18 | >1.0 | highest-weighted; 0 if <=1.0 |
| 3 | Win Rate | 0.09 | >=45% | linear scale |
| 4 | Max Drawdown | 0.135 | <15% | inverse: lower is better |
| 5 | Exchange Connectivity | 0.09 | API responds | binary 0/100 |
| 6 | Risk Violations | 0.09 | 0 in 7d | binary 0/100 |
| 7 | Data Coverage | 0.09 | All symbols fresh | percentage of symbols with recent data |
| 8 | Model Health | 0.09 | Provider available | binary 0/100 |
| 9 | Reconciliation | 0.09 | No drift | binary 0/100 |
| 10 | Calibration | 0.10 | ECE < 0.15 | `max(0, 100 - ECE*200)` |

Traffic light: <50 = RED, 50-75 = YELLOW, >75 = GREEN.

Active Blockers = 0 required before any live action.

---

## 7. Metric Collection Architecture

```
Scheduler (30 jobs)
  |
  +-- market_data (5min) --> market_snapshots table
  +-- portfolio_snapshot (15min) --> portfolio_snapshots table
  +-- confidence_calibration (6h) --> calibration scores
  +-- insight_synthesizer (daily) --> reasoning_feedback piggyback
  +-- health_check (2min) --> doctor probes
  |
  v
Analytics layer (on-demand + cron):
  - go_live_scorecard.py: composite from 10 checks
  - trade_insights.py: win_rate, profit_factor, sharpe
  - confidence_calibration.py: ECE per conviction bucket
  - reasoning_feedback.py: per-bucket rejection/win rates
  |
  v
API endpoints:
  - GET /api/analytics/go-live-scorecard
  - GET /api/analytics/confidence-calibration
  - GET /api/analytics/reasoning-feedback
  - GET /api/analytics/trade-insights
```
