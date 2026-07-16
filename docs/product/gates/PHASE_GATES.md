# Phase Gate Criteria

> Extracted from [ROADMAP.md](../../status/roadmap/ROADMAP.md) for standalone reference.
> Each gate is a measurable checklist. A phase is "passed" when ALL items are checked.
> Phases may run in parallel except Phase 6 (Live) which requires explicit user authorization.
> Last updated: 2026-06-21

---

## Phase 0 - 1: Stabilize - Learning Quality

| # | Criterion | How to check |
|---|-----------|--------------|
| 1 | Gateway runs across multi-day timeframes (healthy/degraded, not dead) | `aletheia doctor` gateway_status |
| 2 | market_observer produces opportunities every ~15min (>70% non-error) | `SELECT COUNT(*) FROM opportunities WHERE created_at > datetime('now','-1 day')` |
| 3 | paper_trader accumulates fills (>=5 fills as sample-size guidance) | `SELECT COUNT(*) FROM fills` |
| 4 | position_manager closes at least 2 positions | `SELECT COUNT(*) FROM positions WHERE status='closed'` |
| 5 | post_trade_reviewer + risk_critic produce output | `SELECT COUNT(*) FROM knowledge_items WHERE item_type IN ('post_trade_review','risk_lesson')` |
| 6 | Demo orders visible on exchange (exchange_sync reflects positions) | Dashboard - Portfolio - exchange sync status |

**NOT a gate**: code_drift self-heal restarts are expected behavior, not failures.

---

## Phase 1 - 2A: Learning Quality - Structured Signals

| # | Criterion | How to check |
|---|-----------|--------------|
| 1 | 14 days continuous run without manual restart | `aletheia doctor` uptime counter |
| 2 | `lessons_consulted` > 0 (FTS5 retrieval firing) | `SELECT COUNT(*) FROM agent_runs WHERE outputs LIKE '%lessons_consulted%'` |
| 3 | Opportunity audit: >=60% have substantive key_observations | Manual sample of 10 recent opportunities |
| 4 | Review audit: >=50% point to specific actionable improvements | Manual sample of 5 post_trade_reviews |
| 5 | Paper trading: >=20 completed trades | `SELECT COUNT(*) FROM positions WHERE status='closed'` |

---

## Phase 2A - 2B: Structured Signals - Agent Coordination

| # | Criterion | How to check |
|---|-----------|--------------|
| 1 | paper_trader decision logs show `entry_conditions` evaluation | Agent run logs contain "entry_conditions" |
| 2 | At least 3 strategies have machine-readable `entry_conditions` | `SELECT COUNT(*) FROM strategies WHERE parameters LIKE '%entry_conditions%'` |
| 3 | Promotion gate distinguishes "strategy rule validated" vs "signal luck" | Code: `check_promotion_gates()` requires evaluation evidence |

---

## Phase 2A+: Strategy Pool and Signal Directionality

| # | Criterion | How to check |
|---|-----------|--------------|
| 1 | Bear regime produces short opportunities | `SELECT COUNT(*) FROM opportunities WHERE direction='short'` |
| 2 | At least 1 strategy with direction_affinity="short" in paper_running | `SELECT * FROM strategies WHERE direction_affinity='short' AND status='paper_running'` |
| 3 | Short opportunity - paper_trader - position_manager exit flow works | Closed short position exists |
| 4 | Bull regime correctly filters short signals | Ranker logs show regime filtering |
| 5 | RegimeConfig controllable via Dashboard Settings | Settings UI has regime panel |

---

## Phase 2B - 3A: Agent Coordination - Experience Loop

| # | Criterion | How to check |
|---|-----------|--------------|
| 1 | risk_critic-flagged opportunities skipped by paper_trader | Agent logs show "risk_flagged - skipped" |
| 2 | Workflow chain (observer-critic-trader) fires end-to-end | Event timeline shows sequential triggers |
| 3 | NoActionDecision records explain blocked opportunities | `SELECT COUNT(*) FROM no_action_decisions` or equivalent |
| 4 | System still trades when event chain delayed (cron fallback works) | Disable event trigger - cron still produces trades |

---

## Phase 3A - 3B: Experience Loop - Walk-Forward Evolution

| # | Criterion | How to check |
|---|-----------|--------------|
| 1 | `lessons_consulted` steadily > Phase 1 baseline | Trending upward in analytics |
| 2 | `activation_ratio` > 30% (strategies not all stuck in draft) | `SELECT COUNT(*) FROM strategies WHERE status IN ('paper_running','paper_passed') / total` |
| 3 | `review_coverage` > 50% | Traded strategies with post-trade reviews / total traded |
| 4 | paper_trader logs show "skipped/adjusted due to lesson X" | Agent run outputs |
| 5 | Strategies annotated with positive/negative expectancy | `strategy_evaluations` table has expectancy data |

---

## Phase 3B - 4: Walk-Forward - Adversarial Hardening

| # | Criterion | How to check |
|---|-----------|--------------|
| 1 | At least 3 StrategyEdit proposals generated | `SELECT COUNT(*) FROM strategy_edits` or equivalent |
| 2 | At least 1 passes walk-forward validation and is applied | Edit with status='applied' exists |
| 3 | Applied edit does not worsen performance over 14-day observation | Compare pre/post edit metrics |
| 4 | No strategy modified without walk-forward validation | Audit trail shows validation for every edit |

---

## Phase 4 - 5: Adversarial Hardening - Intelligence Expansion

| # | Criterion | How to check |
|---|-----------|--------------|
| 1 | At least 2 strategies experience full lifecycle (create-run-revise-retire/promote) | Strategy lifecycle events span full cycle |
| 2 | Retirement reasons are traceable | `strategy_evaluations` or lifecycle events have reason field |
| 3 | risk_critic successfully blocked at least 1 edit that would have lost money | Audit shows blocked edit + subsequent negative outcome avoided |
| 4 | System runs 4+ weeks unattended | `aletheia doctor` uptime |

---

## Phase 5 - 6: Intelligence Expansion - Live Readiness

| # | Criterion | How to check |
|---|-----------|--------------|
| 1 | News events produce structured signals that influence opportunities | news_events - opportunities correlation |
| 2 | Synthesizer deduplicates and prevents signal flooding | Opportunity rate stable despite more scanners |
| 3 | Regime signal observable and correlated with real market behavior | Dashboard regime indicator matches market |
| 4 | No news scanner directly creates orders (sensing only) | Code audit: scanners have no execution path |

---

## Phase 6: Live Readiness (REQUIRES EXPLICIT USER AUTHORIZATION)

### Prerequisites (before starting Phase 6 work)

- [ ] Phase 4 observation gate fully passed
- [ ] 12+ weeks of paper trading with positive expectancy
- [ ] Human explicitly authorizes beginning of Phase 6 work

### Go-Live Scorecard (10 criteria, composite >=70 required)

| # | Criterion | Weight | Target | Source |
|---|-----------|--------|--------|--------|
| 1 | Trade Volume | 0.135 | >=50 closed positions | `positions WHERE status='closed'` |
| 2 | Profit Factor | 0.135 | >1.0 (gross profit / gross loss) | `positions.realized_pnl` |
| 3 | Win Rate | 0.09 | >=45% | closed positions with pnl > 0 |
| 4 | Max Drawdown | 0.135 | <15% of peak equity | `portfolio_snapshots` series |
| 5 | Exchange Connectivity | 0.09 | API responds within timeout | `api.testBitget()` |
| 6 | Risk Violations | 0.09 | 0 recent violations | `risk_decisions WHERE approved=false` (7d) |
| 7 | Data Coverage | 0.09 | All observed symbols have recent snapshots | `market_snapshots` freshness |
| 8 | Model Health | 0.09 | Default LLM provider available | `/api/model/health` |
| 9 | Reconciliation | 0.09 | Local vs exchange state match | `exchange_sync` drift check |
| 10 | Calibration | 0.10 | ECE < 0.15 (confidence calibration) | `confidence_calibration.compute_calibration()` |

### Safety Gates

- [ ] Live remains off unless explicitly unlocked by human
- [ ] First live_limited action requires confirmed human interaction
- [ ] Live uses strict capital/risk boundaries (cannot exceed configured caps)
- [ ] Position mismatch detection works and triggers kill switch
- [ ] Kill switch - all activity stops until human releases

---

## Phase 7+: Advanced (Each item requires separate authorization)

No gate criteria -- each item is independently authorized and scoped. See ROADMAP.md for the full list of advanced directions.

---

## Track B Stage 1: Operator Layer (orthogonal to Phase axis)

| # | Criterion | How to check |
|---|-----------|--------------|
| 1 | User asks "what is the system doing?" - gets real-time data answer | Command Center query test |
| 2 | User asks "why did this proposal fail?" - gets risk_decision citation | Command Center with proposal ID |
| 3 | pause/resume blocked by permission layer (default Read Only) | Attempt pause in Read Only mode - blocked |
| 4 | Every query/command has audit record | `SELECT COUNT(*) FROM agent_shell_commands` |
| 5 | Shell degrades gracefully (LLM unavailable - keyword fallback, no crash) | Kill LLM - system still responds |
| 6 | No live trading / no credential access (security test passes) | Code audit: no execution path from shell |
