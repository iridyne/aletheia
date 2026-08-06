# Aletheia Capability Inventory

This is the deduplicated capability list discovered from the current reviewed
worktree. It deliberately makes **no implementation-quality judgment**.

Table IDs are audit-only stable references, not Aletheia product terminology.
Outside these canonical tables, use the full capability name.

Discovery surfaces:

- 13 primary dashboard pages and 7 Settings groups;
- 314 FastAPI route declarations across 21 route modules;
- 129 Typer command declarations across 26 CLI modules;
- 44 scheduler jobs;
- active README, product, operations, API, data, and agent contracts;
- public behavior in Runtime, Market Data, Operator, Risk, Execution, Position,
  Analytics, and Learning modules.

## Runtime and operations

| ID | Capability | Product promise | Entry surfaces | Primary discovery evidence |
|---|---|---|---|---|
| RT-01 | Packaged runtime installation and first launch | Install and run Aletheia independently of a development checkout | Installer, tray, CLI, runbook | `packaging/`, `native/`, `docs/operations/PACKAGED_RUNTIME.md` |
| RT-02 | Stable Gateway lifecycle | Start, stop, inspect, and retain one stable Gateway across terminal sessions | CLI, API, tray | `aletheia/cli/gateway.py`, `aletheia/gateway/lifecycle.py` |
| RT-03 | Frontdoor and backend supervision | Keep stable frontdoor/backend processes available and restart failed children | Gateway runner, health UI | `aletheia/_gateway_runner.py`, `aletheia/gateway/state_machine.py` |
| RT-04 | Gateway pause and resume | Pause trading work while retaining observable runtime services, then safely resume | Top bar, command palette, CLI, API | `frontend/src/App.tsx`, `aletheia/server/routes/health.py` |
| RT-05 | Gateway restart | Restart the worker/supervised process and surface restart progress | Dashboard, API, CLI | `aletheia/server/routes/gateway.py`, `frontend/src/App.tsx` |
| RT-06 | Scheduler lifecycle and job visibility | Run configured jobs, expose actual scheduler state, and support selective pause | Settings, status, scheduler | `aletheia/agent_runtime/scheduler.py` |
| RT-07 | Connectivity auto-pause and auto-resume | Pause on sustained exchange loss and resume when connectivity returns | Scheduler, health, audit | `aletheia/agent_runtime/connectivity.py` |
| RT-08 | Startup safety gates and recovery | Detect unsafe startup state, remain observable, and recover without bypassing safety | Gateway startup, doctor | `aletheia/agent_runtime/startup_behavior_gate.py`, `resume_gate.py` |
| RT-09 | Runtime continuity candidate/shadow/promotion | Create, validate, promote, drain, and roll back a candidate runtime | Command Center, API, CLI | `aletheia/runtime_continuity/`, `aletheia/server/routes/runtime.py` |
| RT-10 | Health, readiness, doctor, and stability | Distinguish process liveness, UI readiness, business degradation, and consistency | Dashboard, API, CLI | `aletheia/server/routes/health.py`, `aletheia/diagnostics/doctor.py` |
| RT-11 | Backup, verification, export, and import | Create and verify recoverable local state bundles and safely import them | API, CLI | `aletheia/server/routes/persistence.py`, `aletheia/persistence/` |
| RT-12 | Data and filesystem retention | Prune bounded telemetry/files without deleting causal trading evidence | Scheduler, settings | `aletheia/maintenance/`, `docs/design/data/RETENTION_POLICY.md` |
| RT-13 | Update check, apply, activation, and rollback | Safely update development or packaged runtimes with progress and rollback | Settings, tray, API, CLI | `aletheia/update/`, `aletheia/server/routes/update.py` |
| RT-14 | Tunnel lifecycle and remote-owner protection | Configure and operate a tunnel while protecting mutations with owner authority | CLI, Settings | `aletheia/gateway/tunnel.py`, `aletheia/cli/tunnel.py` |
| RT-15 | Proxy/network routing and diagnostics | Configure direct/proxy networking and test reachability | CLI, Settings | `aletheia/net.py`, `aletheia/cli/network_cmd.py` |

## Market data and universe

| ID | Capability | Product promise | Entry surfaces | Primary discovery evidence |
|---|---|---|---|---|
| MD-01 | Core market snapshot collection | Periodically collect fresh price/volume snapshots for trading decisions | Scheduler, Markets, health | `scheduler.py:market_data`, `aletheia/data/` |
| MD-02 | Watched-position price feed | Maintain fast exact-instrument prices for mark-to-market and protective exits | Scheduler, Positions | `scheduler.py:price_feed`, `aletheia/data/price_feed.py` |
| MD-03 | Orderbook, funding, and open-interest collection | Collect microstructure facts used by opportunity analysis | Scheduler, Markets, CLI | `scheduler.py:data_collectors`, `aletheia/data/collectors.py` |
| MD-04 | Liquidation, macro, and cross-venue context | Enrich analysis with bounded contextual market data | Scheduler, Markets | `scheduler.py:data_collector_context`, `aletheia/data/context_collectors.py` |
| MD-05 | Historical OHLCV backfill | Maintain bounded historical bars for active-universe features and settlement | Scheduler, CLI | `ohlcv_active_backfill`, `ohlcv_nightly_backfill` |
| MD-06 | Dynamic Universe generation | Discover, score, diversify, and persist tradable candidate instruments | Universe Board, Settings, scheduler | `aletheia/universe/`, `scheduler.py:universe_refresh` |
| MD-07 | Universe family calibration | Measure cost-adjusted treatment/control outcomes by scanner family | Learning UI, CLI, scheduler | `universe_forward_outcomes`, `learning_exposures`, calibration analytics |
| MD-08 | Forward outcome settlement | Settle universe/filter/shadow outcomes at declared horizons | Scheduler, Learning, CLI | `scheduler.py:outcome_settler`, `aletheia/learning/outcomes.py` |
| MD-09 | Market regime detection | Produce regime state and use it in analysis/sizing/risk context | Markets, Settings, CLI | `aletheia/regime/`, `aletheia/server/routes/data.py` |
| MD-10 | News ingestion and analysis | Fetch, deduplicate, analyze, and expose relevant news | News Intel, Markets, scheduler | `news_fetch`, `news_analyze`, `aletheia/news/` |
| MD-11 | Equity opportunity observation | Observe supported equities/xStocks during market hours and emit opportunities | Scheduler, Agent Activity | `scheduler.py:equity_observer`, `aletheia/agents/equity_observer.py` |
| MD-12 | Prediction-market observation | Collect prediction events/markets, claims, history, and emit opportunities | Prediction Lens, Settings, scheduler | `aletheia/prediction/`, `aletheia/server/routes/prediction.py` |
| MD-13 | Exchange symbol/market eligibility | Validate that an exact symbol and market type can be traded on the selected venue | Settings, execution preflight, doctor | `aletheia/config/exchanges.py`, `aletheia/execution/order_constraints.py` |

## Research, opportunities, and decisions

| ID | Capability | Product promise | Entry surfaces | Primary discovery evidence |
|---|---|---|---|---|
| DC-01 | Market Observer opportunity generation | Convert current market evidence into attributable opportunity generations | Scheduler, Agent Activity | `aletheia/agents/market_observer.py`, `scheduler.py:market_observer` |
| DC-02 | Paper Ranker admission | Rank open opportunities, apply eligibility evidence, and queue bounded candidates | Scheduler, Blotter | `aletheia/agents/paper_trader_ranker.py`, `scheduler.py:paper_trader` |
| DC-03 | System Operator trading decision | Make the sole terminal trade/skip/watch/flip/scale decision with durable reasoning | Blotter, Command Center, scheduler | `aletheia/agent_shell/trading_decision.py`, `pi_daemon.py` |
| DC-04 | PI scheduled decision fallback | Run decision assessments even when no event wake is received | Scheduler, Agent Activity | `scheduler.py:pi_trading_decision` |
| DC-05 | Proposal creation and causal identity | Convert an approved Operator decision into one attributable TradeProposal | Blotter, API, execution | `trading_decision.py`, `trade_proposals` |
| DC-06 | Paper proposal approval and rejection | Let a human approve eligible paper proposals or reject them without identity rewriting | Blotter, API | `frontend/src/pages/Blotter.tsx`, `server/routes/data.py` |
| DC-07 | Candidate ledger and decision trace | Show every terminal candidate outcome and its evidence chain | Blotter, CLI | `operator decisions` projection, `aletheia inspect decisions` |
| DC-08 | Agent run and opportunity observability | Show current/historical agent runs, outputs, failures, and opportunities | Agent Activity, API, CLI | `frontend/src/pages/AgentActivity.tsx` |
| DC-09 | Manual agent triggering | Allow explicit bounded runs of supported agents | Command palette, API | `startAgentRun`, agent runtime routes |
| DC-10 | Operator command workspace | Hold persistent command/diagnostic/review sessions with evidence-backed inspection | Command Center, API, CLI | `frontend/src/pages/CommandCenter.tsx`, `server/routes/operator.py` |
| DC-11 | Operator session branching, tasks, and extensions | Branch sessions, track operator tasks, and enable controlled extensions | Command Center, API | `aletheia/operator_shell/`, `server/routes/operator.py` |
| DC-12 | Serenity multi-lens research | Run long-horizon research, collect evidence/claims/themes, and promote qualified results | Serenity Lens, scheduler | `aletheia/serenity/`, `scheduler.py:dreaming_daily` |
| DC-13 | Serenity preferences and theme feedback | Adapt research from profile preferences and retain More/Less as non-behavioral theme history | Serenity Lens, API | `frontend/src/pages/SerenityLens.tsx`, serenity routes |

## Risk and authority

| ID | Capability | Product promise | Entry surfaces | Primary discovery evidence |
|---|---|---|---|---|
| RK-01 | Mandatory proposal risk evaluation | Every executable proposal is approved or rejected by the deterministic Risk Engine | Execution Gateway, Risk UI | `aletheia/risk/engine.py`, `execution/gateway.py` |
| RK-02 | Scoped demo/live risk policy management | View, edit, copy, reset, and revision-protect separate risk policies | Risk & Controls, Settings, API | `server/routes/risk.py`, `server/routes/settings.py` |
| RK-03 | Global and mode trading brakes | Immediately block trading globally or per mode; require explicit release | Risk UI, command palette, CLI, API | `aletheia/risk/kill_switch.py` |
| RK-04 | Loss, margin, leverage, and liquidation limits | Enforce account and trade-level loss/leverage/liquidation boundaries | Risk Engine, Risk UI | `aletheia/risk/policy.py`, `risk/engine.py` |
| RK-05 | Exposure and concentration limits | Enforce total, position, symbol, sector, and correlation concentration limits | Risk Engine, Risk UI | risk policy and engine |
| RK-06 | Data freshness and instrument integrity gates | Reject proposals with stale, mismatched, or non-tradable market facts | Risk Engine, doctor | risk engine and entry snapshot |
| RK-07 | Permission modes and restricted tool authority | Constrain what agents/operators may inspect, mutate, or execute | Top bar, Settings, CLI | `agent_runtime/permissions.py`, `tool_registry.py` |
| RK-08 | Go-live eligibility scorecard | Compute explicit blockers and criteria before live unlock | Overview, Analytics, Live route | `aletheia/analytics/go_live_scorecard.py` |

## Execution and position lifecycle

| ID | Capability | Product promise | Entry surfaces | Primary discovery evidence |
|---|---|---|---|---|
| EX-01 | Automatic local paper trading | Carry eligible candidates through decision, risk, local fill, position, and outcome | Scheduler, Blotter, Positions | `paper_trader`, `execution/paper_broker.py` |
| EX-02 | Exchange demo trading | Execute risk-approved demo orders through Bitget/OKX/Binance adapters | Settings, Blotter, exchange adapters | `execution/*_broker.py` |
| EX-03 | Live Unlock and relock | Human-unlock live-limited authority and return it to disabled state | Settings, API | `server/routes/live.py`, live contract |
| EX-04 | New-proposal live execution mode | Route newly authorized proposals to the selected live-limited broker | Operator, Risk, Execution Gateway | `trading_decision.py`, `execution/gateway.py` |
| EX-05 | Order constraint normalization | Normalize quantity/price/contract constraints without changing approved intent | Execution Gateway | `execution/order_constraints.py` |
| EX-06 | Exchange order materialization | Turn an approved durable order into one exchange order/fill/position chain | Execution Gateway, exchange adapters | `execution/exchange_materialization.py` |
| EX-07 | Exchange-native TP/SL brackets | Place and reconcile venue-native protective orders where supported | Settings, Execution Gateway | `execution/stops.py`, broker adapters |
| EX-08 | Local protective exits | Close local/demo positions on stop, target, max-hold, catastrophic, or thesis rules | Scheduler, Positions | `portfolio/position_manager.py` |
| EX-09 | Market-aware position review | React to adverse current signals by waking the Operator with close/tighten/hold evidence; do not mutate the position directly | Settings, Position Manager, Operator | market-aware config, `exit_rules.py` |
| EX-10 | Horizon and thesis review | Wake or close positions when horizon or thesis conditions require a decision | Position Manager, Operator | `position_manager.py`, wake events |
| EX-11 | External demo-position adoption | Adopt supported external demo positions into managed lifecycle when enabled | Settings, reconciliation scheduler | `portfolio/position_reconciler.py` |
| EX-12 | Exchange order/position reconciliation | Detect and converge local/exchange drift without fabricating fills | Scheduler, Positions, API | `execution/reconciliation.py`, `position_reconciler.py` |
| EX-13 | Stale order and disappearance handling | Resolve stale/missing exchange orders and positions with durable evidence | Scheduler, execution | `stale_order_cleanup.py`, `disappearance_settlement.py` |
| EX-14 | Local paper cost-complete close settlement | Persist exact fills, fees, slippage, funding, realized PnL, and paper ledger settlement on close | Positions, Analytics, Learning | `portfolio/outcome_closure.py`, `paper_account.py`, `local_funding.py` |
| EX-15 | Partial reduce and scale-in lifecycle | Retain bounded reference behavior while production mutation remains explicitly unavailable | Operator, Position lifecycle | decision actions, execution/position services |
| EX-16 | Exchange-backed cost-complete close settlement | Settle exchange-backed futures only when private funding/bill evidence is attributable | Positions, Analytics, Learning | `portfolio/outcome_closure.py`, exchange private-ledger producers |

## Portfolio, performance, and attribution

| ID | Capability | Product promise | Entry surfaces | Primary discovery evidence |
|---|---|---|---|---|
| PF-01 | Local paper cash account | Maintain cash, reserve, equity, funding events, and affordability for local paper | Settings, Risk, scheduler | `portfolio/paper_account.py` |
| PF-02 | Capital event recording | Record deposits/withdrawals manually and from initial broker sync | Settings, API, exchange sync | `server/routes/capital.py`, `capital_events` |
| PF-03 | Capital-adjusted performance curve | Remove external deposits/withdrawals from displayed investment performance | Settings, Overview, Charts | capital event promise, equity/PnL analytics |
| PF-04 | Portfolio snapshots and account equity history | Persist and display an honest account-equity time series | Overview, API, scheduler | `portfolio/pnl.py`, `market/account-equity-history` |
| PF-05 | Position and trade blotter | Display pending/open/closed orders, fills, costs, and close reasons | Trades & Positions, API, CLI | `frontend/src/pages/TradesPositions.tsx` |
| PF-06 | Trade insights and performance analytics | Show outcome-cohort-aware PnL, drawdown, expectancy, regime, hold, and exit analytics | Overview, Charts, API, CLI | `analytics/trade_insights.py`, `performance_summary.py` |
| PF-07 | Trade attribution and thesis lineage | Reconstruct opportunity→decision→proposal→risk→order→fill→position→outcome | Drawers, CLI, analytics | `analytics/trade_attribution.py`, `thesis_lineage.py` |
| PF-08 | Opportunity flow and funnel attribution | Count every generation, candidate, decision, rejection, fill, and outcome without survivor bias | Overview, Blotter, CLI | `analytics/opportunity_flow.py`, `trade_funnel.py` |
| PF-09 | Alpha/family/factor attribution | Attribute cost-adjusted outcomes to scanner family, factors, and treatment paths | CLI, Learning, Analytics | `analytics/alpha_attribution.py`, `factor_feedback.py` |

## Learning and policy evolution

| ID | Capability | Product promise | Entry surfaces | Primary discovery evidence |
|---|---|---|---|---|
| LN-01 | Post-trade review | Convert eligible closed outcomes into attributable lessons/reviews | Scheduler, Learning | `post_trade_reviewer`, `trade_case_builder.py` |
| LN-02 | Risk critic | Review risk-decision history and write reusable risk lessons | Scheduler, Learning | `scheduler.py:risk_critic` |
| LN-03 | Dreaming consolidation | Periodically consolidate research, trades, and knowledge into durable artifacts | Learning, scheduler | `dreaming_daily`, `dreaming_agent.py` |
| LN-04 | Trading memo synthesis | Produce bounded decision memos from current knowledge and outcomes | Learning, scheduler | `insight_synthesizer` |
| LN-05 | Knowledge retrieval | Search and inject eligible active knowledge into later agent decisions | Learning, CLI, agent prompts | `knowledge/retrieval.py`, FTS |
| LN-06 | Lesson review lifecycle | Accept, reject, archive, dry-run, promote, and overturn lessons with evidence | Learning, persistence API | lesson store and reviewer |
| LN-07 | Skill drafting and review | Create draft skills from eligible lessons without automatic activation | Persistence API, Learning | skill store and lesson reviewer |
| LN-08 | Learned guard generation and observation | Generate and review learned guards, then observe candidate matches without granting learned block/penalty authority | Learning, Settings, scheduler | `knowledge/guards.py`, `guard_promotion` |
| LN-09 | Confidence calibration | Compare prediction confidence with realized outcomes | Learning, analytics, scheduler | `analytics/confidence_calibration.py` |
| LN-10 | Shadow trades and counterfactual resolution | Record non-selected candidates and later settle their counterfactual outcomes | CLI, analytics, scheduler | `counterfactual/trades.py`, `shadow_resolver` |
| LN-11 | Filter controller observation | Measure filter outcomes and produce shadow recommendations without mutating config | Learning, Settings, scheduler | `analytics/filter_controller.py` |
| LN-12 | Measured Learning contribution | Separate treatment/control evidence, cohort integrity, and evaluator health from activity | Learning UI, API, scheduler | `learning/evidence.py`, contribution routes |
| LN-13 | Learning lifecycle automation | Advance/refreeze authorized paper experiments only when all gates hold | Settings, scheduler | `learning/lifecycle.py`, lifecycle controller |
| LN-14 | Persona base sizing | Use the active persona to determine entry notional/Kelly inside Risk caps | Settings, Operator | `trading/persona.py`, `trading/sizing.py` |
| LN-15 | Persona regime learning | Learn a bounded paper sizing adjustment from clean regime cohorts | Settings, scheduler, Operator | `trading/sizing_policy.py` |
| LN-16 | Persona progressive unlock | Unlock operator-decidable persona parameters by clean evidence and refreeze on distress | Settings, scheduler | persona policy and rollout |
| LN-17 | Persona pyramid decision | Evaluate bounded reference add-on policy without promising order placement | Settings, Operator | `trading/sizing_policy.py` |
| LN-18 | Persona rollout automation | Advance persona experiment steps on an 8-hour health-gated schedule | Settings, scheduler, doctor | `trading/persona_rollout.py` |
| LN-19 | Policy/version history and diff | Persist versioned policies and inspect changes without rewriting history | Persistence API, CLI | `policies/`, persistence routes |

## Security, configuration, and owner visibility

| ID | Capability | Product promise | Entry surfaces | Primary discovery evidence |
|---|---|---|---|---|
| OS-01 | Write-only secret management | Store/test credentials without returning or logging secret values | Settings, CLI, model/exchange routes | `config/secrets.py`, secret routes |
| OS-02 | Model provider configuration and fallback | Configure, verify, authenticate, and route supported model providers | Settings, API, CLI | `model_provider/`, `server/routes/model.py` |
| OS-03 | Exchange credential verification | Verify candidate demo/live credentials before saving or use | Settings, exchange routes | Bitget/OKX/Binance routes |
| OS-04 | Settings persistence and coverage | Persist validated product settings and expose honest read-only coverage for internals | Settings, API | `server/routes/settings.py`, `frontend/src/pages/Settings.tsx` |
| OS-05 | Append-only audit trail | Record trust-bearing config, permission, live, risk, and trade changes with real actor/source | UI, CLI, persistence | `files/jsonl.py`, audit projections |
| OS-06 | Telegram alerts and daily summaries | Deliver configured alerts/summaries and report reachability honestly | Settings, scheduler, API | `notifications.py`, `daily_summary` |
| OS-07 | Owner-visible failure states | Surface backend, agent, exchange, collector, and operator failures instead of showing success | Status bar, health, CLI | health routes, `inspect alerts` |
| OS-08 | Realtime dashboard updates | Refresh visible system state through websocket/SSE/polling without inventing values | Dashboard shell | `routes/ws.py`, frontend query hooks |

## Deduplication notes

- The 314 API declarations are entry surfaces, not 314 capabilities.
- Risk limit fields are grouped by the final deterministic evaluation behavior.
- Scheduler startup catch-up jobs are grouped with the capability they recover.
- UI analytics variants are grouped by their underlying outcome projection.
- Exchange-specific adapters remain one capability when they promise the same
  execution result; known venue differences are recorded in fact cards.
- `UI-only` and `Backend-only` will be recorded as gap types during audit, not
  added as duplicate inventory capabilities.
