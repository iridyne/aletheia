---
type: architecture
title: Architecture Layers
description: Vertical layering of the Aletheia trading system from foundation through frontend.
tags: [architecture, layers]
---

# Architecture Layers

Each layer is a cohesive Python package with a well-defined boundary. Dependencies flow downward (higher layers depend on lower layers) and data flows around the trading loop.

## Foundation (`aletheia/`)

The root package. Contains CLI entrypoint, gateway process supervisor, cross-cutting utilities, and packaging support.

| Module | Responsibility |
|---|---|
| `__init__.py` | Package init, `__version__` |
| `main.py` | Typer CLI, aliased as `ato`. Entry point for all `aletheia` subcommands |
| `_gateway_runner.py` | Background gateway supervisor with respawn/backoff logic |
| `async_utils.py` | Windows `SelectorEventLoop` policy (fixes IOCP drain on Windows) |
| `net.py` | HTTP client factory with proxy fallback (SOCKS5 + direct) |
| `logging_config.py` | Structured stderr logging + JSONL error mirror |
| `runtime_paths.py` | Platform-neutral runtime home resolution (`~/.aletheia`) |
| `package_integrity.py` | Immutable payload integrity for packaged (frozen) runtimes |
| `packaged_process.py` | Child-process adapters for frozen PyInstaller binaries |
| `runtime_home_migration.py` | Runtime home migration with safety checks |

**Why it exists:** Provides the stable base for everything else — a single `uv run aletheia` CLI, a self-healing background process, and cross-platform runtime path resolution.

## Gateway (`aletheia/gateway/`)

The process supervisor and local network layer.

| Module | Responsibility |
|---|---|
| `lifecycle.py` | `start`/`stop`/`status`/`pause`/`resume`/`logs` — the gateway lifecycle |
| `pidfile.py` | PID file creation, process-tree kill, stale PID detection |
| `frontdoor.py` | Local front-door ASGI proxy on port 8787 (bypasses tunnel for LAN) |
| `frontdoor_route.py` | Resolves backend routes for front-door requests |
| `managed_service.py` | OS service abstraction for launchd / systemd / Task Scheduler |
| `operator_shell.py` | TypeScript operator shell supervisor |
| `state_machine.py` | Gateway state transitions (stopped → starting → running → etc.) |
| `state_store.py` | Persistent gateway state on disk |
| `network.py` | Bind host resolution, port management |
| `preflight.py` | Pre-flight checks before gateway start |
| `locks.py` | Lock management for exclusive gateway access |
| `logs.py` | Log tailing via CLI |
| `tunnel.py` | Cloudflare Tunnel management (for remote dashboard access) |
| `recovery.py` | Gateway crash recovery with backoff |
| `service_control.py` | Low-level service start/stop |
| `session_service.py` | Session service for dashboard auth |
| `dashboard_auth.py` | Dashboard authentication helpers |
| `git_hooks.py` | Git hooks for data/knowledge versioning |
| `console_text.py` | Console text decoding (handles Windows encoding issues) |
| `exchange_adapter.py` | Exchange adapter for frontend |
| `autostart.py` | Autostart configuration for OS boot |
| `reconciliation.py` | Gateway-level reconciliation |
| Windows-specific | `_win32_proc.py`, `windows_firewall.py`, `windows_identity.py`, `windows_machine_service.py` |

**Why it exists:** A single background process manages the server, the optional Cloudflare Tunnel, the operator shell, OS-level autostart, and crash recovery — so the user can treat trading as a managed service.

## Server/API (`aletheia/server/`)

The FastAPI backend exposing the REST/WebSocket API and mounting the dashboard.

| Module | Responsibility |
|---|---|
| `app.py` | FastAPI app construction, lifespan context, static dashboard mount |
| `dev_reload.py` | Dev auto-reload gating (not enabled in production) |
| `responsiveness.py` | `EventLoopLagMonitor` — detects if the ASGI event loop is unresponsive |
<!-- openwiki: broken internal link [api/routes.md] file "api/routes.md" does not exist. Fix the href or restore the target, then delete this comment. -->
| `routes/` | 24 route modules (see [API Routes](api/routes.md)) |

**Why it exists:** Decouples HTTP/WebSocket concerns from the agent runtime. All dashboard data and operator controls flow through REST routes or SSE streams.

## Agent Runtime (`aletheia/agent_runtime/`)

The orchestration layer that schedules and monitors all automated work.

| Module | Responsibility |
|---|---|
| `scheduler.py` | APScheduler initialization, 30+ job definitions across 5 executor pools |
| `job_queue.py` | Async job queue for deferred work |
| `health.py` | Health monitoring (LLM availability, system health), self-heal triggers |
| `permissions.py` | Permission gates for agent actions |
| `startup_gate.py` | LLM provider probe at boot — blocks agents until providers are healthy |
| `startup_behavior_gate.py` | Additional startup behavior checks |
| `pi_daemon.py` | Event-driven System Operator daemon |
| `wake_events.py` | Wake event coalescing for agent scheduling |
| `runtime_session.py` | Runtime session tracking |
| `build_info.py` | Boot commit tracking, code drift detection |
| `run_logger.py` | Agent run logging |
| `run_view.py` | Agent run view for dashboard |
| `connectivity.py` | Connectivity monitoring |
| `auto_resume.py` | Auto-resume logic after interruption |
| `resume_gate.py` | Resume gate logic |
| `stability_monitor.py` | Stability monitoring for agents |
| `tool_registry.py` | Tool registry for agent shell |
| `async_utils.py` | Async utilities for the runtime |
| `orchestrator.py` | High-level orchestrator |

**Why it exists:** Provides a single scheduler, health checker, and startup gate so agents don't need to manage their own cron-like scheduling or retry logic. The scheduler uses 5 executor pools (blocking, async, thread, process, llm) with configurable timeouts.

## Agent Shell (`aletheia/agent_shell/`)

The System Operator's reasoning and execution shell — the **sole authority for trading decisions**.

| Module | Responsibility |
|---|---|
| `operator_loop.py` | 4-hour PI operator assessment loop |
| `trading_decision.py` | Trading decision engine — evaluates candidates and produces `pi_decision` |
| `trading_context.py` | Trading context building for decisions |
| `context.py` | Context profiles and sections |
| `commands.py` | Command definitions |
| `executor.py` | Command executor |
| `registry.py` | Command registry |
| `intent.py` | Intent parsing |
| `permissions.py` | Shell permissions |
| `runtime.py` | Shell runtime |
| `sessions.py` | Shell sessions |
| `operator_recommendations.py` | Operator recommendations |
| `operator_store.py` | Operator store (sessions, tasks, artifacts) |
| `reflection.py` | Reflection on past decisions |
| `compaction.py` | Conversation compaction |
| `file_artifacts.py` | File artifact management |
| `decision_facts.py` | Decision facts recording |
| `vergex_policy.py` | VergeX policy integration |
| `skip_policy.py` | Skip policy for candidates |
| `b2_evidence_policy.py` | B2 evidence policy for LLM calls |
| `synthesis.py` | Synthesis of agent outputs |

**Why it exists:** Isolates the System Operator's reasoning from the agent runtime. The shell has its own command executor with permission gates, intent parsing, and session management — so trading decisions are auditable and replayable.

## Agents (`aletheia/agents/`)

Registered trading agents, each covering a specific cognitive or operational role. Agents are registered via a decorator and dispatched by the scheduler.

| Agent | Role | Output |
|---|---|---|
| `market_observer.py` | Per-symbol LLM market analysis | Opportunity rows |
| `paper_trader.py` | Ranks candidate opportunities | signal_pending carriers |
| `paper_trader_ranker.py` | Deterministic candidate ranking (no LLM/DB) | Ranked list |
| `dreaming_agent.py` | Daily consolidation → review + dreaming logs | Daily review |
| `equity_observer.py` | US equity observation (yfinance + xStocks) | Equity opportunities |
| `prediction_observer.py` | Polymarket prediction market observation | Prediction opportunities |
| `divergence_scanner.py` | Polymarket/crypto price divergence | Tradeable opportunities |
| `post_trade_reviewer.py` | Reviews closed positions | Lessons |
| `risk_critic.py` | Reviews risk decisions | Lessons |
| `lesson_reviewer.py` | Reviews learned lessons for promotion | Promoted knowledge |
| `research_worker.py` | Serenity research pipeline | Evidence → claims → themes |
| `insight_synthesizer.py` | Daily insight synthesis | Insights |
| `committee/` | Investment Committee sub-agents | Committee evaluation |
| `helpers.py` | Shared LLM call helpers + context adapter | — |
| `checkpoints.py` | Trading checkpoints | Checkpoints |
| `round_robin.py` | Round-robin LLM provider rotation | Provider selection |
| `portfolio_agent.py` | Portfolio agent | Portfolio signals |

**Why it exists:** Decomposes trading intelligence into focused, independently testable agents. Each agent consumes from the knowledge base and produces structured output that feeds the next stage.

## Risk + Execution (`aletheia/risk/` + `aletheia/execution/`)

The safety and trading layer.

| Module | Responsibility |
|---|---|
| `risk/__init__.py` | Risk package init |
| `risk/engine.py` | `evaluate_proposal` — §21.3 check matrix (leverage, exposure, max order, daily loss, etc.) |
| `risk/kill_switch.py` | Global kill switch (off for live by default) |
| `risk/policy.py` | Risk policy resolution and hash |
| `risk/causal.py` | Causal inference for risk decisions |
| `execution/__init__.py` | Execution package init |
| `execution/gateway.py` | `submit_proposal`, `execute_existing_proposal` — routes proposals through risk to brokers |
| `execution/paper_broker.py` | Local paper fill simulation with slippage |
| `execution/paper_tools.py` | Paper trading tools |
| `execution/bitget_broker.py` | Bitget exchange demo/live order routing |
| `execution/binance_broker.py` | Binance exchange orders |
| `execution/okx_broker.py` | OKX exchange orders |
| `execution/exchange_materialization.py` | Exchange materialization |
| `execution/reconciliation.py` | Exchange order reconciliation |
| `execution/position_enrichment.py` | Position liquidation price enrichment |
| `execution/stops.py` | Stop-loss logic |
| `execution/tpsl_fill_check.py` | TPSL fill checking |
| `execution/oco.py` | OCO (One-Cancels-the-Other) orders |
| `execution/verified_exit.py` | Verified protective exit |
| `execution/stale_order_cleanup.py` | Stale order detection and cleanup |
| `execution/order_constraints.py` | Exchange order constraint validation |
| `execution/errors.py` | Execution errors |
| `execution/post_commit_audit.py` | Post-commit audit trail |
| `execution/broker_funding.py` | Broker funding settlement |
| `execution/broker_utils.py` | Broker utilities |
| `execution/submission_errors.py` | Exchange submission errors |

**Why it exists:** All trading passes through `risk/engine.py::evaluate_proposal` before any broker is called. The Execution Gateway (`execution/gateway.py`) is the single chokepoint that enforces this invariant.

## Data (`aletheia/data/`)

Market data ingestion, normalization, and the standalone collector subprocess.

| Module | Responsibility |
|---|---|
| `ccxt_provider.py` | CCXT primary + fallback market data provider |
| `providers.py` | Candle, Ticker dataclasses |
| `symbols.py` | Symbol canonicalization, InstrumentId |
| `market_data.py` | Market data model |
| `market_session.py` | Market session detection |
| `normalizer.py` | Data normalization |
| `context_builder.py` | LLM context enrichment (features, news, macro, risk) |
| `price_feed.py` | Fast in-memory price cache (30s refresh) |
| `freshness.py` | Snapshot freshness checking |
| `cache.py` | Data caching |
| `models.py` | MarketSnapshot, FundingSnapshot, etc. |
| `collector_health.py` | Collector health monitoring |
| `venue_health.py` | Venue availability, cooldown |
| `venue_listing_cache.py` | Venue listing cache |
| `data_coverage.py` | Data coverage tracking |
| `history_downloader.py` | Historical data download |
| `active_ohlcv_backfill.py` | Bounded OHLCv catch-up |
| `perp_structure_shadow.py` | Perp structure shadow forward settlement |
| `position_x.py` | Position X observations |
| `news_collector.py` | RSS feed ingestion |
| `news_analyzer.py` | LLM sentiment/related-symbol extraction |
| `news_entities.py` | Deterministic crypto entity matching |
| `yfinance_provider.py` | yFinance macro context |
| `polymarket_provider.py` | Polymarket prediction market provider |
| `collector_runner.py` | Standalone collector subprocess |
| `collectors/` | 9 collector modules (coinalyze, coinvoke_top, defillama, derivatives, hyperliquid, macro, microstructure, vergex_structure, volume_scanner) |
| `sources/` | 3 source modules (chain, binance_public, coingecko) |

**Why it exists:** Centralizes all market data ingestion behind a uniform provider interface, with health monitoring and a standalone subprocess for reliability.

## Universe (`aletheia/universe/`)

Signal scanning, feature extraction, and candidate allocation.

| Module | Responsibility |
|---|---|
| `service.py` | Main universe refresh service |
| `scanners.py` | Market scanner families (momentum, mean_reversion, volatility, derivatives, event, core) |
| `features.py` | Feature extraction (RSI, BB, vol_ratio, OI divergence, etc.) |
| `allocator.py` | Scoring, quota, TTL, explore/exploit |
| `filters.py` | Universe filters |
| `signal_emitter.py` | Deterministic opportunity emission |
| `registry.py` | Symbol/instrument registry |
| `filter_advisor.py` | Filter advisor |
| `filter_outcome_recorder.py` | Filter outcome recording |
| `outcome_recorder.py` | Opportunity outcome recording |
| `outcome_economics.py` | Outcome economics |
| `funding_units.py` | Funding rate units |
| `snapshot_freshness.py` | Snapshot freshness |

**Why it exists:** Transforms raw market data into structured Opportunity candidates, with deterministic scoring and TTL-based freshness enforcement.

## Portfolio (`aletheia/portfolio/`)

Position lifecycle management, PnL, and the paper account.

| Module | Responsibility |
|---|---|
| `position_manager.py` | Position lifecycle (stop/take-profit/trailing/max-hold) — **autonomous protective closes only** |
| `paper_account.py` | Paper account management (cash, positions, history) |
| `pnl.py` | PnL calculation |
| `pnl_attribution.py` | PnL attribution |
| `exit_plan.py` | Exit plan persistence and effective resolution |
| `exit_rules.py` | Exit rule evaluation |
| `account_loss.py` | Daily loss tracking |
| `exchange_sync.py` | Exchange sync |
| `position_reconciler.py` | Position reconciliation |
| `reconcile_monitor.py` | Reconciliation monitoring |
| `reconciliation_integrity.py` | Reconciliation integrity |
| `disappearance_confirmation.py` | Disappearance confirmation |
| `disappearance_settlement.py` | Disappearance settlement |
| `outcome_closure.py` | Outcome closure |
| `outcome_hooks.py` | Outcome hooks |
| `market_signal_reader.py` | Market signal reader |
| `local_funding.py` | Local funding settlement |
| `broker_funding_settlement.py` | Broker funding settlement |
| `backtest_engine.py` | Paper trading backtest engine |
| `uta/` | UTA (Unified Trading Account) sub-package |

**Why it exists:** Manages the complete position lifecycle independently from entry decisions. The position manager can **autonomously close positions** for risk management (stop-loss, max-hold, protective exits) but cannot open new positions.

## Storage (`aletheia/storage/`)

SQLite persistence layer.

| Module | Responsibility |
|---|---|
| `sqlite.py` | Thread-local connection, WAL mode, write lock |
| `models.py` | All Pydantic DB models |
| `repositories.py` | All DB operations |
| `migrations.py` | Versioned schema migrations |
| `event_store.py` | Event store |
| `artifact_store.py` | Artifact store |
| `hashing.py` | Content hash utilities |
| `market_ohlcv_layout.py` | OHLCV layout |

**Why it exists:** Single source of truth for all persistent state, with versioned migrations and thread-safe access.

## Config (`aletheia/config/`)

Configuration schema, loader, and secrets management.

| Module | Responsibility |
|---|---|
| `loader.py` | Config load/save, bootstrap, data dir |
| `schema.py` | Complete Pydantic config schema |
| `secrets.py` | Secret storage (OS keyring / encrypted file) |
| `exchange_alignment.py` | Exchange alignment (which exchange for which trading mode) |

**Why it exists:** All operational behavior is config-driven. Secrets are never stored in `config.yaml` — they go in the OS keyring or an encrypted file.

## Model Provider (`aletheia/model_provider/`)

Multi-provider LLM routing with failover and rate limiting.

| Module | Responsibility |
|---|---|
| `base.py` | `ModelProvider` ABC, `ModelRequest`/`ModelResponse` |
| `router.py` | Provider creation, OAuth refresh, health check (PROVIDER_REGISTRY) |
| `broker.py` | Process-wide inference admission + rate limiting |
| `fallback.py` | Multi-provider fallback chain |
| `inference_contract.py` | `InferenceCall`, `build_inference_call` |
| `openai_compatible.py` | OpenAI-compatible provider |
| `anthropic_compatible.py` | Anthropic-compatible |
| `openai_native.py` | OpenAI native |
| `anthropic_native.py` | Anthropic native |
| `databricks.py` | Databricks provider |
| `codex_cli.py` | Codex CLI provider |
| `claude_cli.py` | Claude CLI provider |
| `pi_cli.py` | PI CLI provider |
| `claude_oauth.py` | Claude OAuth |
| `openai_oauth.py` | OpenAI OAuth |
| `codex_auth.py` | Codex auth |
| `oauth.py` | OAuth utilities |
| `registry.py` | Provider registry |
| `guardrails.py` | Inference guardrails |

**Why it exists:** Decouples agent reasoning from specific LLM providers. The broker enforces rate limits; the fallback chain provides resilience; the inference contract makes every LLM call auditable.

## Knowledge (`aletheia/knowledge/`)

Experience-loop lessons and trading guardrails stored as Markdown.

| Module | Responsibility |
|---|---|
| `manager.py` | Knowledge item lifecycle |
| `retrieval.py` | FTS5 retrieval with recency/importance scoring |
| `guards.py` | Trading guard evaluation |
| `seed_guards.py` | Seed guard definitions |
| `frontmatter.py` | YAML frontmatter parsing |
| `maintenance.py` | KB maintenance |
| `recovery.py` | KB recovery |
| `router.py` | Knowledge router |

**Why it exists:** Captures lessons from post-trade reviews and risk critiques, then surfaces them as guardrails when evaluating future opportunities.

## Learning (`aletheia/learning/`)

AT-01 measurement experiments that gate learned behavior influence.

| Module | Responsibility |
|---|---|
| `authority.py` | AT-01 verdict authority resolver |
| `lifecycle.py` | Stage transitions (shadow → paper_10 → paper_25 → paper_50 → paper_active) |
| `ledger.py` | Idempotent ledger write/read |
| `verdicts.py` | Measurement verdicts (METRIC_VERSION v4) |
| `contribution.py` | Contribution aggregation |
| `contribution_runner.py` | Contribution evaluation |
| `control.py` | Learning gate (pause, refreeze, autonomous enable) |
| `cohorts.py` | Cohort isolation |
| `experiments.py` | Experiment management |
| `policy_epochs.py` | Policy epoch management |
| `skills.py` | Learning skills |
| `read_models.py` | Read models |
| `behavior_contracts.py` | Behavior contracts |
| `hypotheses.py` | Learning hypotheses |
| `evidence.py` | Evidence contracts |
| `lessons.py` | Lesson extraction |
| `models.py` | Learning experiment/hypothesis models |
| `opportunity_information_policy.py` | Policy epoch label |
| `maintenance.py` | Learning maintenance |
| `guard_routing.py` | Guard routing |
| `outcomes.py` | Learning outcomes |

**Why it exists:** Before learned behavior can influence real or paper trading, it must pass AT-01 measurement experiments. The lifecycle enforces gradual exposure with contribution analysis to prevent catastrophic learning.

<!-- openwiki: broken internal link [components/learning-system.md] file "components/learning-system.md" does not exist. Fix the href or restore the target, then delete this comment. -->
<!-- openwiki: broken internal link [diagrams/learning-experiments.mmd] file "diagrams/learning-experiments.mmd" does not exist. Fix the href or restore the target, then delete this comment. -->
See also: [Learning System](components/learning-system.md), [Learning Experiments](diagrams/learning-experiments.mmd)

## Analytics (`aletheia/analytics/`)

Performance summaries, attribution, and diagnostic views.

| Module | Responsibility |
|---|---|
| `performance_summary.py` | Equity curve, max drawdown, profit factor |
| `trade_attribution.py` | Alpha attribution, trade funnel |
| `skip_diagnosis.py` | Why signals get skipped |
| `rotation_advisories.py` | Rotation advisory generation |
| `three_gaps.py` | Three-gap position analysis |
| `horizon_exit.py` | Horizon-based exit planning |
| `protective_exit.py` | Verified protective price exit |
| `oco.py` | OCO order handling |
| `stale_order_cleanup.py` | Stale order detection and cleanup |
| `exchange_sync.py` | Portfolio exchange synchronization |
| `outcome_closure.py` | Outcome settlement pipeline |
| `reconciliation.py` | Exchange order reconciliation |
| `position_reconciler.py` | Position reconciliation against exchange |
| `broker_funding.py` | Funding rate settlement |
| `local_funding.py` | Local paper funding simulation |
| `post_commit_audit.py` | Post-commit audit trail |
| `position_enrichment.py` | Position enrichment with liquidation price |
| `order_constraints.py` | Exchange order constraint validation |
| `client_order_id.py` | Client order ID generation |
| And ~15 more diagnostic/analysis modules | |

**Why it exists:** Provides the observability needed to understand system behavior, diagnose skip reasons, attribute alpha, and verify execution quality.

## Counterfactual (`aletheia/counterfactual/`)

Shadow trades and causal estimation.

| Module | Responsibility |
|---|---|
| `trades.py` | Shadow trade generation/resolution |
| `estimators.py` | DR/IPW estimators |
| `propensity.py` | Propensity scoring |
| `calibration.py` | Conviction-gate calibration |
| `advisories.py` | Policy-epoch rotation advisories |
| `filter_experiments.py` | Filter experiment analysis |
| `filter_replay.py` | Filter replay |
| `outcome_economics.py` | Counterfactual outcome economics |

**Why it exists:** Estimates what would have happened if a different decision was made, without risking real capital.

## Regime (`aletheia/regime/`)

Market regime detection.

| Module | Responsibility |
|---|---|
| `collector.py` | Regime input collection (VIX, DXY, stablecoin TVL, DEX volume) |
| `detector.py` | Deterministic detection with debounce (Bull/Bear/Sideways/Volatile) |

## Economics (`aletheia/economics/`)

LLM cost tracking and budget enforcement.

| Module | Responsibility |
|---|---|
| `tracing.py` | LLM attempt/trace recording |
| `inference_calls.py` | Inference call recording |
| `budgets.py` | Spend budget enforcement |
| `viability.py` | Viability analysis |
| `agent_roles.py` | Agent role economics |
| `schema.py` | Economics schema |
| `context_artifacts.py` | Context artifacts |
| `context_effectiveness.py` | Context effectiveness |

## Runtime Continuity (`aletheia/runtime_continuity/`)

Shadow → promote → rollback lifecycle for zero-downtime updates.

| Module | Responsibility |
|---|---|
| `service.py` | State machine (candidate → shadow → promote → current → drain → rollback) |
| `processes.py` | Shadow gateway spawn/shutdown |
| `models.py` | Runtime DTOs |
| `bundles.py` | Git worktree materialization |

## Diagnostics (`aletheia/diagnostics/`)

Health checks and system diagnostics.

| Module | Responsibility |
|---|---|
| `doctor.py` | Ground-truth system probes |
| `pipeline_progress.py` | Pipeline progress tracking |
| `config_consistency.py` | Config consistency checks |

## Observability (`aletheia/observability/`)

| Module | Responsibility |
|---|---|
| `llm_metrics.py` | Per-provider LLM completion counters |

## Files (`aletheia/files/`)

| Module | Responsibility |
|---|---|
| `jsonl.py` | Append-only JSONL audit/error/event logging + hash chain |
| `markdown.py` | Markdown file writing |
| `git.py` | Git helpers (versioning, worktrees) |
| `paths.py` | Path helpers |

## Infra (`aletheia/infra/`)

| Module | Responsibility |
|---|---|
| `events.py` | Infra event emission |
| `errors.py` | Error recording |
| `error_summary.py` | Error summary |

## Trading (`aletheia/trading/`)

| Module | Responsibility |
|---|---|
| `instruments.py` | Instrument definitions (TIMEFRAMES, observed instruments) |
| `market_data.py` | Market data models |
| `market_session.py` | Market session detection |
| `market_context.py` | Market context building |
| `indicators.py` | Stdlib-only technical indicators |
| `sizing.py` | Conviction-based position sizing |
| `sizing_policy.py` | Sizing policy |
| `entry_plan.py` | Risk-reward calculation |
| `entry_snapshot.py` | Entry attribution snapshot |
| `persona.py` | Trading persona |
| `persona_rollout.py` | Persona rollout |
| `playbooks.py` | Playbooks |
| `strategy_lifecycle.py` | Strategy lifecycle |
| `serenity_evidence.py` | Serenity evidence tiers |
| `serenity_scoring.py` | Serenity scoring |

## Simulation (`aletheia/simulation/`)

| Module | Responsibility |
|---|---|
| `control_loop/` | Clock, invariants, artifacts, runner |

## Maintenance (`aletheia/maintenance/`)

| Module | Responsibility |
|---|---|
| `data_retention_backfill.py` | Retention scheduling |
| `database_capacity.py` | Database capacity |
| `filesystem_retention.py` | Filesystem retention |
| `historical_accounting.py` | Historical accounting |
| `outcome_integrity.py` | Outcome integrity |
| `paper_funding_reconciliation.py` | Paper funding reconciliation |
| `settlement_requeue.py` | Settlement requeue |

## Update (`aletheia/update/`)

| Module | Responsibility |
|---|---|
| `git_ops.py` | Git operations |
| `packaged.py` | Packaged update transactions |

## Trust (`aletheia/trust/`)

| Module | Responsibility |
|---|---|
| `guard_chain.py` | Composable pre-risk observability |
| `behavior_authority.py` | Learning influence authorization |

## Policies (`aletheia/policies/`)

| Module | Responsibility |
|---|---|
| `store.py` | Policy storage |
| `diff.py` | Policy diff |

## Sync (`aletheia/sync/`)

| Module | Responsibility |
|---|---|
| `exporter.py` | Data export |
| `importer.py` | Data import |
| `conflicts.py` | Conflict resolution |

## Backup (`aletheia/backup/`)

| Module | Responsibility |
|---|---|
| `service.py` | Backup service |
| `restore.py` | Restore |
| `verify.py` | Verify |

## Frontend (`frontend/src/`)

React/Vite dashboard with 14 pages: Overview, TradesPositions, CommandCenter, Charts, AgentActivity, Committee, Learning, SerenityLens, PredictionLens, NewsIntel, RiskControls, Universe, Usage, Settings.

## Operator Shell (`operator-shell/`)

TypeScript operator shell — provides a rich terminal UI for the System Operator, distinct from `aletheia/agent_shell/operator_shell.py`.
