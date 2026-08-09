# Files

- [Agent Shell Command Cycle](agent-shell-command-cycle.md) - Intent parsing to command execution with permission gates and session management.
- [Counterfactual Analysis Pipeline](counterfactual-pipeline.md) - Shadow trades, propensity scoring, conviction-gate calibration, and rotation advisories.
- [Data Collector Subprocess](data-collector-subprocess.md) - Standalone market data collection subprocess with 9 collectors and health monitoring.
- [Economics Tracing Pipeline](economics-tracing-pipeline.md) - LLM inference call costs tracked through budgets and viability analysis.
- [Architecture Layers](layers.md) - Vertical layering of the Aletheia trading system from foundation through frontend.
- [Aletheia System Overview](overview.md) - Local-first AI-native agentic trading system. Paper by default, live trading behind explicit human unlock.
- [Regime Detection Pipeline](regime-detection-pipeline.md) - VIX, DXY, stablecoin TVL, and DEX volume input collection with debounced regime classification.
- [Runtime Continuity Lifecycle](runtime-continuity-workflow.md) - Shadow candidate promotion, current promotion, drain, and rollback workflow for zero-downtime updates.
- [APScheduler Job Registry](scheduler.md) - 30+ scheduled jobs across 5 executor pools with health checks and pause/resume.
- [The Aletheia Trading Cognition Loop](trading-loop.md) - End-to-end evidence loop from market data to learning.
