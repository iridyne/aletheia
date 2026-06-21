# Aletheia Trading OS — Local-first Agentic Trading System Implementation Guide

> ⚠️ **v1 实现规范,部分内容已被上层文档取代(2026-06-21 标注):**
> 本文件是 MVP 初版的完整实现规范。其中"为什么做/做什么"已被 [VISION](../product/VISION.md) +
> [PRD](../product/PRD.md) 取代;具体子系统契约以 [docs/design/](../design/) 的 L2 金标准为准
> (Risk/Execution/Live/Promotion/data_model/agent_contracts/api_reference)。
> 本文件仍保留有效的 CLI/目录结构/部分实现细节作参考;**契约层冲突时以 L2 design 文档为准**。
> 长期不变的开发红线在 [trust_and_change_control](trust_and_change_control.md)。文档分层见 [DOC_SYSTEM_PLAN](../product/DOC_SYSTEM_PLAN.md)。

> **Target implementer:** Claude Code / Codex / Cursor / other AI coding agents  
> **Target user:** one-person local AI-native trading system  
> **Product name:** **Aletheia Trading OS**  
> **CLI name:** `aletheia`, with optional short alias `ato`  
> **Primary objective:** build a local-first, file-based, Trading-as-Git, self-learning agentic trading OS for BTC / ETH / SOL on Bitget, starting in paper/demo mode and supporting strictly bounded live-limited execution later.

---

## 0. Non-negotiable implementation rules

Read this section before writing any code.

1. **Do not build a heavy Docker/Postgres/Redis platform for MVP.**  
   MVP must run locally from a CLI gateway and serve a local dashboard.

2. **Do not fork OpenAlice as the production base.**  
   Use OpenAlice only as an inspiration for local-first design, Trading-as-Git, transparent reasoning, and guardrails.

3. **Do not make Hermes Agent a production dependency.**  
   Recreate only the trading-specific subset: gateway, scheduler, model routing, memory, playbooks, dreaming, and tool calling.

4. **Do not enable live trading by default.**  
   Initial state is always `live_disabled`. First live-limited activation requires explicit human action from Dashboard or CLI.

5. **Do not expose API keys or secrets to agents, logs, Knowledge Base, reports, or prompt context.**

6. **Agent autonomy is real but bounded.**  
   The agent may generate research, modify paper strategy configs, create playbook drafts, and propose live-limited promotion. It may not modify hard risk limits, credentials, execution gateway logic, or signer/credential code.

7. **Human controls boundaries, not strategy details.**  
   Human sets risk boundaries, allowed markets, live unlock, and feedback. The agent controls strategy design, parameter selection, timeframe selection, and paper experimentation inside those boundaries.

8. **All actions must be auditable.**  
   Write to JSONL audit logs and SQLite state tables.

---

## 1. Product identity

### 1.1 Name

**Aletheia Trading OS**

Rationale: *Aletheia* means disclosure / truth. The system must make every trade, strategy, belief, and model action traceable to evidence.

### 1.2 What it is

Aletheia is a **local-first AI-native trading operating system**:

- Runs locally through a CLI gateway.
- Starts a background service and local dashboard.
- Uses files, SQLite, JSONL, Markdown, and optional Git.
- Lets agents research, learn, dream, test, and evolve strategies.
- Uses hard-coded safety boundaries for capital protection.
- Supports Bitget spot and USDT futures for BTC / ETH / SOL.
- Starts with paper/demo trading and later supports constrained live-limited mode.

### 1.3 What it is not

Aletheia is not:

- A cloud trading platform.
- A multi-user SaaS system.
- A high-frequency trading engine.
- A fully autonomous unbounded live trading bot.
- A Docker/Postgres/Redis enterprise stack in MVP.
- A simple signal bot that only says buy/sell.

---

## 2. Design principles

### 2.1 Local-first

All core state lives on the user’s machine:

```text
SQLite    -> structured state
Markdown  -> research, reviews, playbooks, strategy notes
JSONL     -> event streams, audit logs, agent runs, tool calls
Git       -> optional versioning layer
```

No remote backend is required for MVP.

### 2.2 File-based + Trading as Git

The system should feel like a trading workspace that both humans and AI can read.

Trading-as-Git means:

- Strategies have versions.
- Trade proposals are staged before execution.
- Important actions are committed to local history.
- Reviews and evaluations are attached to strategy lifecycle records.
- Optional Git can track `data/knowledge`, `data/playbooks`, `data/strategies`, and `data/reports`.

### 2.3 AI-native

Strategies, execution logic, learning notes, and reviews must be AI-readable.

The agent should be able to answer:

- Why does this strategy exist?
- Which evidence created it?
- Which paper trades validated it?
- Which failures weakened it?
- What should be improved next?

### 2.4 Hard control

Aletheia may feel alive, but safety is not negotiated by the model.

Hard control means:

- Risk engine cannot be bypassed.
- API keys cannot enter prompt context.
- Live mode cannot be opened by the agent alone.
- Agent cannot edit execution gateway semantics.
- Agent cannot edit credential handling.
- Agent cannot change hard risk limits.

---

## 3. MVP scope

### 3.1 MVP must include

```text
Local gateway CLI
Local dashboard server
SQLite storage
Markdown/JSONL file storage
Optional Git integration
Agent Runtime
Knowledge Base
Dreaming/Learning light mode
Strategy Lifecycle
Bitget adapter
BTC/ETH/SOL support
Spot support
USDT futures support
Paper/demo trading
Live API support behind live_disabled gate
Risk Engine
Telegram light adapter
Model provider abstraction
```

### 3.2 MVP markets

Only these instruments are tradable in MVP:

```text
BTC/USDT
ETH/USDT
SOL/USDT
```

Markets:

```text
Bitget Spot
Bitget USDT Perpetual Futures
```

Other coins may be observed later, but **must not be automatically traded in MVP**.

### 3.3 MVP exchange

Only Bitget is supported in MVP.

Do not implement Binance, OKX, Hyperliquid, DEX, xStocks, Polymarket, or Kalshi in MVP. Keep them in roadmap only.

### 3.4 MVP trading mode

Initial defaults:

```yaml
exchange: bitget
exchange_mode: demo
live_state: live_disabled
permission_mode: Research
tradable_symbols: [BTCUSDT, ETHUSDT, SOLUSDT]
```

---

## 4. Reference products and concepts

Use these as design inspirations, not as hard dependencies.

### 4.1 OpenAlice inspiration

Borrow:

- Local-first product feel.
- Transparent AI reasoning.
- Trading-as-Git concept.
- Agent + execution separation.
- Guard pipeline concept.
- Web UI as control center.

Do not fork it as production base. Current OpenAlice is an AI trading agent covering multiple asset classes and is AGPL-licensed, so treat it as a reference rather than a dependency.

### 4.2 Hermes Agent inspiration

Borrow:

- Gateway-style CLI.
- Background-running agent process.
- Model switching while gateway is running.
- Skills/playbooks.
- Memory and learning loop.
- Autonomous “alive” feeling.

Do not depend on Hermes. Implement a trading-native subset.

### 4.3 Freqtrade inspiration

Borrow:

- Practical crypto bot controls.
- WebUI + Telegram pattern.
- Backtesting/paper/live separation.
- Trade lists and bot state views.

### 4.4 Hummingbot inspiration

Borrow:

- Strategy management dashboard.
- CEX/DEX strategy deployment thinking.
- Multi-strategy instance concept for future.

### 4.5 TradingView inspiration

Borrow:

- Simple charts with trade markers.
- Strategy metrics.
- List of trades.
- Visual connection between chart and trade decisions.

---

## 5. Technology stack

### 5.1 Backend

Use:

```text
Python 3.12+
FastAPI or Litestar
Typer for CLI
SQLite
SQLModel or SQLAlchemy + Alembic-like lightweight migrations
Pydantic v2
APScheduler or custom scheduler
httpx
websockets if needed
pandas/polars for analysis
cryptography + keyring for local secret storage
```

Prefer FastAPI unless there is a strong reason not to.

### 5.2 Frontend

Use:

```text
Vite
React
TypeScript
TanStack Query
TanStack Table
lightweight-charts or Recharts
```

Build static assets and serve them from the Python backend.

Do not require a separate frontend dev server for normal user runtime.

### 5.3 Telegram

Use either:

```text
Python aiogram
```

or a minimal HTTP-based bot adapter.

MVP Telegram features are light only.

### 5.4 Packaging

MVP development:

```text
python -m aletheia gateway start
```

Later packaging:

```text
PyInstaller or Nuitka
```

Future:

```text
Windows exe
Tray app
Windows service installer
```

Do not require Docker for normal use.

---

## 6. Local runtime model

### 6.1 Gateway process

Aletheia must expose a gateway CLI similar to Hermes-style gateway.

Commands:

```bash
aletheia gateway start
aletheia gateway start --no-browser
aletheia gateway start --foreground
aletheia gateway stop
aletheia gateway restart
aletheia gateway status
aletheia gateway logs
aletheia gateway pause
aletheia gateway resume
aletheia gateway open-dashboard
```

Short alias may be provided:

```bash
ato gateway status
```

### 6.2 Background behavior

Default behavior:

```text
aletheia gateway start
```

should:

1. Load local config.
2. Initialize data directories.
3. Open SQLite database.
4. Start backend API server on localhost.
5. Serve dashboard static files.
6. Start scheduler.
7. Start Telegram adapter if configured.
8. Start agent runtime.
9. Write PID file.
10. Open browser automatically unless `--no-browser` is used.

### 6.3 Dashboard address

Default:

```text
http://127.0.0.1:8787
```

Bind to localhost only by default.

Do not expose to LAN unless user explicitly configures it.

### 6.4 Pause/resume

`pause` must stop all autonomous agent jobs and trading jobs.

It must not stop the dashboard.

`resume` re-enables jobs.

This is the system-level brake.

---

## 7. Project structure

Implement this structure or a close equivalent:

```text
aletheia-trading-os/
  pyproject.toml
  README.md
  IMPLEMENTATION_GUIDE.md

  aletheia/
    __init__.py
    main.py

    cli/
      app.py
      gateway.py
      model.py
      permission.py
      bitget.py
      strategy.py
      kb.py

    server/
      app.py
      routes/
        health.py
        dashboard.py
        agent_runs.py
        opportunities.py
        proposals.py
        trades.py
        positions.py
        strategies.py
        knowledge.py
        settings.py
        risk.py

    gateway/
      daemon.py
      lifecycle.py
      pidfile.py
      browser.py
      logs.py

    config/
      schema.py
      loader.py
      defaults.yaml
      secrets.py

    storage/
      sqlite.py
      migrations.py
      repositories.py
      models.py

    files/
      paths.py
      jsonl.py
      markdown.py
      git.py

    model_provider/
      base.py
      openai_compatible.py
      anthropic_compatible.py
      codex_cli.py
      router.py

    agent_runtime/
      orchestrator.py
      scheduler.py
      tool_registry.py
      permissions.py
      run_logger.py
      prompts.py
      memory.py
      dreaming.py

    agents/
      market_observer.py
      research_agent.py
      strategy_designer.py
      risk_critic.py
      portfolio_agent.py
      post_trade_reviewer.py
      dreaming_agent.py

    knowledge/
      evidence_vault.py
      research_notes.py
      strategy_memory.py
      agent_memory.py
      search.py
      consolidation.py

    trading/
      instruments.py
      market_data.py
      features.py
      signals.py
      strategy_lifecycle.py

    exchanges/
      bitget/
        client.py
        auth.py
        symbols.py
        spot.py
        futures.py
        demo.py
        live.py
        errors.py

    execution/
      gateway.py
      paper_broker.py
      bitget_broker.py
      orders.py
      fills.py
      reconciliation.py

    risk/
      engine.py
      rules.py
      defaults.yaml
      checks.py
      kill_switch.py

    portfolio/
      ledger.py
      positions.py
      pnl.py
      snapshots.py

    telegram/
      bot.py
      commands.py

    dashboard_static/
      # built frontend assets copied here

  frontend/
    package.json
    vite.config.ts
    src/
      main.tsx
      App.tsx
      api/
      pages/
      components/
      charts/
      state/

  data/
    # generated at runtime, do not commit real data
```

---

## 8. Local data directory

At first run, create:

```text
data/
  config.yaml
  secrets.enc
  app.db
  gateway.pid

  logs/
    gateway.log
    audit.jsonl
    agent_runs.jsonl
    tool_calls.jsonl
    market_snapshots.jsonl
    errors.jsonl

  knowledge/
    evidence/
    research_notes/
    strategies/
    agent_memory/
    daily_reviews/
    dreaming_logs/
    deprecated/

  playbooks/
    market_observation.md
    strategy_design.md
    paper_evaluation.md
    live_promotion.md
    risk_critique.md

  reports/
    daily/
    post_trade/
    system_health/

  exports/
```

`data/` must be user-readable and portable.

Secrets must not be stored in Markdown/JSONL.

---

## 9. Storage architecture

### 9.1 SQLite

Use SQLite for structured state.

Tables:

```text
settings
agent_runs
agent_run_steps
agent_tool_calls
opportunities
trade_proposals
risk_decisions
approvals
orders
fills
positions
portfolio_snapshots
strategy_records
strategy_experiments
knowledge_items
market_snapshots
audit_events
```

### 9.2 JSONL

Use JSONL for event streams:

```text
logs/audit.jsonl
logs/agent_runs.jsonl
logs/tool_calls.jsonl
logs/errors.jsonl
logs/market_snapshots.jsonl
```

Every important state transition must write an audit event.

### 9.3 Markdown

Use Markdown for AI-native knowledge:

```text
research notes
strategy descriptions
daily reviews
post-trade reviews
playbooks
memory summaries
dreaming logs
```

### 9.4 Optional Git

Git is optional.

If enabled, version:

```text
data/knowledge/
data/playbooks/
data/reports/
```

Do not commit:

```text
data/secrets.enc
data/app.db
raw API keys
private logs with secrets
```

---

## 10. Config model

Create `data/config.yaml` on first run.

Example:

```yaml
app:
  name: Aletheia Trading OS
  dashboard_port: 8787
  auto_open_browser: true
  bind_host: 127.0.0.1

runtime:
  gateway_mode: background
  scheduler_enabled: true
  paused: false

exchange:
  name: bitget
  mode: demo   # demo | live
  enabled_markets:
    spot: true
    usdt_futures: true
  tradable_symbols:
    - BTCUSDT
    - ETHUSDT
    - SOLUSDT

live:
  state: live_disabled   # live_disabled | live_limited | live_active_future
  first_live_confirmation_done: false

agent:
  permission_mode: Research
  autonomous_learning: true
  autonomous_paper_trading: false

models:
  default_provider: openai_compatible
  providers:
    openai_compatible:
      base_url: http://localhost:8000/v1
      protocol: chat_completions # chat_completions | responses
      model: default
    anthropic_compatible:
      base_url: http://localhost:8001
      model: default
    codex_cli:
      enabled: false
      command: codex
      mode: exec

risk:
  max_daily_loss_pct: 1.0
  max_single_trade_risk_pct: 0.25
  max_total_live_exposure_pct: 5.0
  max_live_position_notional_usdt: 50
  max_live_limited_cap_usdt: 100
  max_futures_leverage_live: 2
  max_futures_leverage_demo: 5
  allow_short_demo: true
  allow_short_live: true
  require_risk_check_for_all_orders: true

telegram:
  enabled: false
  allowed_chat_ids: []
```

---

## 11. Secrets handling

### 11.1 Requirements

Secrets include:

```text
Bitget API key
Bitget API secret
Bitget passphrase
Telegram bot token
Model endpoint API keys if any
```

Rules:

- Store secrets locally encrypted when possible.
- Prefer OS keyring.
- Fallback to encrypted `data/secrets.enc`.
- Never write secrets to logs.
- Never expose secrets to agent prompt context.
- Never include secrets in Knowledge Base.
- Redact secrets in all error messages.

### 11.2 CLI commands

```bash
aletheia secrets set bitget.demo.api_key
aletheia secrets set bitget.demo.api_secret
aletheia secrets set bitget.demo.passphrase
aletheia secrets set bitget.live.api_key
aletheia secrets set bitget.live.api_secret
aletheia secrets set bitget.live.passphrase
aletheia secrets test bitget
```

Use one config file with `exchange.mode=demo|live`; credentials may contain both demo and live profiles under encrypted secret storage.

---

## 12. Model provider layer

### 12.1 Required providers

MVP must support these provider types:

1. **OpenAI-compatible endpoint**
   - Supports `/v1/chat/completions` style.
   - Supports `/v1/responses` style.

2. **Anthropic-compatible endpoint**
   - Supports Anthropic Messages-style API.

3. **ChatGPT Codex subscription / local authenticated CLI mode**
   - Uses a locally authenticated Codex CLI path if available in the user environment.
   - Must fail gracefully if unavailable.

### 12.2 Provider abstraction

Implement:

```python
class ModelProvider:
    async def complete(self, request: ModelRequest) -> ModelResponse: ...
    async def structured(self, request: StructuredModelRequest, schema: type) -> Any: ...
    async def healthcheck(self) -> ModelHealth: ...
```

Do not hardcode vendor accounts. Make it endpoint-first.

### 12.3 CLI commands

```bash
aletheia model status
aletheia model providers
aletheia model set --provider openai_compatible --protocol responses --base-url ... --model ...
aletheia model set --provider openai_compatible --protocol chat_completions --base-url ... --model ...
aletheia model set --provider anthropic_compatible --base-url ... --model ...
aletheia model set --provider codex_cli --enabled true
aletheia model test
```

---

## 13. Agent permission modes

Implement Claude Code-like permission levels.

### 13.1 Modes

```text
Read Only
Research
Paper Auto
Allow Editing
Auto
```

### 13.2 Default

Default MVP mode:

```text
Research
```

### 13.3 Semantics

#### Read Only

Agent can:

- Read market data.
- Read positions.
- Read reports.
- Summarize state.

Agent cannot:

- Create proposals.
- Create paper orders.
- Edit files.

#### Research

Agent can:

- Create research notes.
- Create opportunities.
- Create strategy hypotheses.
- Write dreaming logs.

Agent cannot:

- Place paper/demo/live orders automatically.

#### Paper Auto

Agent can:

- Create paper strategy candidates.
- Create paper/demo proposals.
- Execute paper/demo trades through paper/demo execution gateway.

Agent cannot:

- Trade live.
- Promote to live.
- Edit code.

#### Allow Editing

Agent can:

- Modify prompt drafts.
- Modify playbook drafts.
- Modify paper strategy configs.
- Generate code change proposals.

Agent cannot:

- Directly edit live execution logic.
- Directly change hard risk limits.
- Directly change credentials.

#### Auto

Agent can:

- Run paper/demo lifecycle autonomously.
- Propose live-limited promotion.
- If first live confirmation is already done and hard gates pass, operate within `live_limited`.

Agent cannot:

- Open first live-limited activation without human confirmation.
- Enter future `live_active` without new explicit design.
- Modify risk hard limits.

### 13.4 Permission changes

Dashboard may directly change permission modes without extra authentication.

All changes must write audit logs.

CLI:

```bash
aletheia permission status
aletheia permission set Research
aletheia permission set PaperAuto
aletheia permission set Auto
```

---

## 14. Strategy Lifecycle

### 14.1 States

Implement these states:

```text
draft
research_note
strategy_hypothesis
paper_candidate
paper_running
paper_passed
live_candidate
live_limited
retired
deprecated
```

`live_active` is future only, not MVP.

### 14.2 Lifecycle rule

A strategy can move forward only through the lifecycle.

No direct jump from research to live.

### 14.3 Promotion gates

Default paper-to-live-candidate gates:

```yaml
min_paper_runtime_days: 14
min_paper_trades: 30
positive_expectancy_required: true
max_drawdown_pct_allowed: 5.0
max_consecutive_losses_allowed: 5
critical_risk_violations_allowed: 0
post_trade_reviews_required: true
```

These are conservative defaults. The agent may propose changes as a suggestion, but hard gates require human/system approval.

### 14.4 Live-limited gate

First move to `live_limited` requires explicit human confirmation from Dashboard or CLI.

After first confirmation, future promotions may be automated only if:

- Permission mode is `Auto`.
- Live state is unlocked.
- Risk engine gates pass.
- Strategy lifecycle gates pass.
- Audit log is written.

---

## 15. Human/agent/risk division

### 15.1 Human role

Human defines:

- Risk boundaries.
- Live unlock.
- Allowed markets.
- Allowed symbols.
- Feedback and suggestions.
- Whether autonomous promotion is allowed.

Human should not directly control:

- RSI values.
- Timeframes.
- Entry/exit parameters.
- Strategy internals.

### 15.2 Agent role

Agent defines:

- Strategy ideas.
- Parameters.
- Timeframes.
- Paper experiments.
- Hypotheses.
- Research notes.
- Promotion proposals.

### 15.3 Risk Engine role

Risk Engine enforces:

- Max capital exposure.
- Max position size.
- Max leverage.
- Max single trade risk.
- Max daily loss.
- Tradeable symbols.
- Live state.
- Demo/live separation.

---

## 16. Knowledge Base

### 16.1 Knowledge Base principles

Use a hybrid KB:

```text
Markdown as primary knowledge
SQLite as index/state
JSONL as event stream
Vector search later, not MVP
Temporal graph later, not MVP
```

### 16.2 Components

#### Evidence Vault

```text
data/knowledge/evidence/
```

Stores summaries, links, key excerpts, source metadata.

Do not save full copyrighted content by default.

#### Research Notes

```text
data/knowledge/research_notes/
```

Each note includes:

```text
title
source links
summary
key concepts
trading hypothesis
counterarguments
confidence
status
created_at
```

#### Strategy Memory

```text
data/knowledge/strategies/<strategy_id>/
```

Files:

```text
strategy.md
lifecycle.json
experiments.jsonl
reviews.md
```

#### Agent Memory

```text
data/knowledge/agent_memory/
```

Includes:

```text
agent_performance.md
failed_reasoning_patterns.md
risk_lessons.md
user_boundaries.md
strategy_lessons.md
```

#### Dreaming Logs

```text
data/knowledge/dreaming_logs/
```

Shows what the system is consolidating.

### 16.3 Knowledge rules

- KB is local-first.
- Cloud backup is optional future only.
- External sources retain summary + link + key snippets.
- AI cannot hard-delete KB entries.
- AI may archive or mark deprecated.
- Memory must support confidence levels:

```text
high_confidence
medium_confidence
speculative
deprecated
```

- Contradiction detection is required.
- Prompt/playbook changes can be drafted by agent but official activation goes through lifecycle.

### 16.4 MVP KB pages

Dashboard must include lightweight KB pages:

```text
Research Notes
Strategy Memory
Daily Reviews
Dreaming Logs
```

Advanced vector search and knowledge graph are future upgrades.

---

## 17. Dreaming / Learning system

### 17.1 Purpose

Dreaming is a scheduled background consolidation process.

It makes the product feel alive without sacrificing control.

### 17.2 MVP dreaming tasks

Implement:

```text
Daily trading review
Agent run compression
Strategy performance summary
Failed reasoning pattern extraction
Risk lesson extraction
Research note synthesis
New strategy hypothesis generation
```

### 17.3 Future external learning

Future feature, not MVP:

- Search public internet.
- Read configured RSS/web sources.
- Read YouTube title/description/subtitles.
- Summarize ICT/SMC/IAT-like trading concepts.
- Convert concepts into research notes.
- Convert notes into strategy hypotheses.
- Send through paper lifecycle.

Do not implement full YouTube learning in MVP. Reserve hooks and page sections.

### 17.4 Dreaming chain

```text
External/internal signal
  -> Evidence
  -> Research Note
  -> Insight
  -> Strategy Hypothesis
  -> Paper Candidate
  -> Evaluation
  -> Strategy Lifecycle Update
```

---

## 18. Agent Runtime

### 18.1 Core agents

Implement these MVP agents:

```text
Market Observer
Research Agent
Strategy Designer
Risk Critic
Portfolio Agent
Post-trade Reviewer
Dreaming Agent
```

### 18.2 Agent run record

Each run must record:

```text
run_id
agent_name
trigger_type
permission_mode
started_at
ended_at
status
model_provider
model_name
tools_called
outputs_created
errors
cost_estimate_optional
```

### 18.3 Tool registry

Agent tools must be permissioned.

Tool classes:

```text
read_tool
research_tool
paper_tool
editing_tool
restricted_tool
```

Restricted tools cannot be called by LLM directly.

### 18.4 Required tools

Read tools:

```text
get_market_snapshot
get_positions
get_account_summary
get_risk_boundaries
get_strategy_status
get_recent_agent_runs
```

Research tools:

```text
create_research_note
create_opportunity
create_strategy_hypothesis
write_dreaming_log
```

Paper tools:

```text
create_paper_proposal
run_paper_order
update_paper_strategy_config
```

Editing tools:

```text
create_playbook_draft
create_prompt_draft
create_code_change_proposal
```

Restricted tools:

```text
unlock_live
place_live_order
change_hard_risk_limits
modify_credentials
modify_execution_gateway
```

---

## 19. Bitget integration

### 19.1 Required support

MVP supports:

```text
Bitget Spot
Bitget USDT Futures
Demo mode
Live mode
BTC/USDT
ETH/USDT
SOL/USDT
Long and short for futures
```

### 19.2 Implementation strategy

Prefer a native Bitget adapter rather than relying only on ccxt, especially for demo mode.

Bitget demo API requires demo API keys created in demo mode. Official demo docs state demo calls should include `paptrading: 1` in request headers for demo API use.

### 19.3 Adapter interface

Implement:

```python
class ExchangeAdapter:
    async def get_account(self) -> AccountSummary: ...
    async def get_balances(self) -> list[Balance]: ...
    async def get_positions(self) -> list[Position]: ...
    async def get_ticker(self, symbol: str, market_type: str) -> Ticker: ...
    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list[Candle]: ...
    async def place_order(self, order: OrderRequest) -> OrderResponse: ...
    async def cancel_order(self, order_id: str) -> CancelResponse: ...
    async def get_open_orders(self) -> list[Order]: ...
    async def get_fills(self, since: datetime | None) -> list[Fill]: ...
```

### 19.4 Demo/live mode

Single config file with:

```yaml
exchange:
  mode: demo # demo | live
```

But secrets may store both demo and live credentials encrypted.

### 19.5 Live rules

Even if live credentials exist:

- `live_state` starts as `live_disabled`.
- Live order placement returns error unless live is unlocked.
- First live-limited requires human action.
- Risk engine must approve every live order.

---

## 20. Execution architecture

### 20.1 Execution modes

```text
paper_internal
bitget_demo
bitget_live_limited
```

MVP default:

```text
paper_internal or bitget_demo
```

### 20.2 Execution Gateway

All orders go through `ExecutionGateway`.

Flow:

```text
Strategy/Agent Proposal
  -> Risk Engine
  -> Permission Check
  -> Live State Check
  -> Execution Gateway
  -> Paper Broker or Bitget Adapter
  -> Fill/Reconciliation
  -> Portfolio Ledger
  -> Audit Log
```

### 20.3 Forbidden path

Never allow:

```text
Agent -> Bitget API directly
Dashboard -> Bitget API directly
Telegram -> Bitget API directly
```

Everything must go through backend gateway.

---

## 21. Risk Engine

### 21.1 Hard limits

Default conservative hard limits:

```yaml
max_daily_loss_pct: 1.0
max_single_trade_risk_pct: 0.25
max_total_live_exposure_pct: 5.0
max_live_position_notional_usdt: 50
max_live_limited_cap_usdt: 100
max_futures_leverage_live: 2
max_futures_leverage_demo: 5
max_open_positions_live: 3
max_open_positions_demo: 6
allowed_symbols: [BTCUSDT, ETHUSDT, SOLUSDT]
allowed_exchange: bitget
allow_short_demo: true
allow_short_live: true
```

### 21.2 Agent cannot modify

Agent cannot modify hard limits.

Agent can propose changes as a report.

### 21.3 Risk checks

Implement:

```text
symbol allowed
market allowed
live state
permission mode
max position size
max daily loss
max single trade risk
max leverage
max open positions
data freshness
kill switch
account mode match
strategy lifecycle state
```

### 21.4 Risk decision output

Every proposal must receive:

```json
{
  "approved": false,
  "risk_score": 0,
  "violations": [],
  "allowed_execution_mode": "paper_internal",
  "message": "..."
}
```

---

## 22. Trading strategy autonomy

### 22.1 Timeframes

Do not hard-code one fixed strategy timeframe.

Agent may choose timeframe/parameters inside available data constraints.

MVP should provide data for:

```text
5m
15m
1h
4h
1d
```

Agent chooses what to use.

### 22.2 Strategy details

Human should not manually control strategy internals.

Agent may autonomously choose:

```text
indicators
price action logic
timeframes
entry rules
exit rules
position sizing proposal
long/short direction
```

Risk engine may reject.

### 22.3 MVP strategy seed

Provide starter playbooks, not fixed strategies:

```text
core_crypto_regime_observation
trend_following_hypothesis
volatility_regime_hypothesis
funding_pressure_hypothesis
breakout_or_reversal_hypothesis
```

The agent turns them into paper strategy candidates.

---

## 23. Dashboard

### 23.1 Dashboard role

Dashboard is:

```text
control room
approval desk
trading cockpit
agent transparency view
strategy lifecycle view
dreaming/learning surface
```

### 23.2 MVP pages

Implement these pages:

```text
Overview
Agent Runs
Dreaming / Learning
Knowledge Base
Opportunities
Proposals
Paper Trades
Positions
Strategy Lifecycle
Reports
Settings
```

### 23.3 Overview

Show:

```text
Gateway status
Paused/running state
Exchange mode demo/live
Live state
Permission mode
Account summary
Today PnL
Current positions
Latest agent run
Latest proposal
Risk state
```

### 23.4 Agent Runs

Show:

```text
run list
agent name
status
trigger
model provider
tools called
outputs created
errors
trace details
```

### 23.5 Dreaming / Learning

Show:

```text
current dreaming task
recent consolidations
new research notes
new strategy hypotheses
deprecated memories
contradictions found
```

### 23.6 Knowledge Base

MVP tabs:

```text
Research Notes
Strategy Memory
Daily Reviews
Dreaming Logs
```

### 23.7 Opportunities

Show:

```text
symbol
market
direction bias
thesis
confidence
source agent
created_at
status
linked strategy hypothesis
```

### 23.8 Proposals

Show:

```text
proposal id
strategy id
symbol
spot/futures
direction
execution mode
risk decision
agent rationale
approval/action buttons
```

### 23.9 Paper Trades

Show:

```text
orders
fills
PnL
entry/exit reason
linked proposal
linked strategy
```

### 23.10 Positions

Show:

```text
spot balances
futures positions
paper positions
demo positions
live positions if enabled
exposure by symbol
```

### 23.11 Strategy Lifecycle

Show pipeline:

```text
draft -> paper_candidate -> paper_running -> paper_passed -> live_candidate -> live_limited
```

### 23.12 Settings

Settings must allow:

```text
permission mode switch
exchange mode switch demo/live
live unlock action
model provider config
Bitget connection test
Telegram config
risk boundaries display
pause/resume
```

Changing permission mode does not need extra auth, but must write audit logs.

### 23.13 Charts

MVP charts:

```text
basic price chart
trade markers
simple PnL chart
```

Do not implement full TradingView-level charting in MVP.

---

## 24. Telegram MVP

### 24.1 Commands

Implement light commands only:

```text
/status
/pause
/resume
/latest
/help
```

Optional:

```text
/open
```

### 24.2 Not in Telegram MVP

Do not put full approval flow in Telegram MVP.

Do not put strategy lifecycle control in Telegram MVP.

These live in Dashboard.

### 24.3 Security

Telegram commands require allowed chat IDs.

All Telegram actions write audit logs.

---

## 25. API routes

Backend local API should expose:

```text
GET  /api/health
GET  /api/status
POST /api/gateway/pause
POST /api/gateway/resume

GET  /api/agent-runs
GET  /api/agent-runs/{id}
POST /api/agent-runs/start

GET  /api/opportunities
GET  /api/proposals
POST /api/proposals/{id}/approve-paper
POST /api/proposals/{id}/reject

GET  /api/trades
GET  /api/positions
GET  /api/portfolio

GET  /api/strategies
GET  /api/strategies/{id}
POST /api/strategies/{id}/promote
POST /api/strategies/{id}/retire

GET  /api/knowledge/research-notes
GET  /api/knowledge/dreaming-logs
GET  /api/knowledge/strategy-memory

GET  /api/settings
POST /api/settings/permission-mode
POST /api/settings/exchange-mode
POST /api/settings/model
POST /api/settings/live-unlock

POST /api/bitget/test-connection
```

All routes are localhost-only by default.

---

## 26. Event and audit model

Every important action writes an audit event.

Audit event fields:

```json
{
  "event_id": "...",
  "timestamp": "...",
  "actor": "agent|human|system|telegram|cli",
  "source": "dashboard|cli|scheduler|agent_runtime|telegram",
  "event_type": "...",
  "entity_type": "...",
  "entity_id": "...",
  "before": {},
  "after": {},
  "message": "..."
}
```

Examples:

```text
permission_changed
live_unlocked
proposal_created
risk_decision_created
paper_order_executed
strategy_promoted
strategy_deprecated
gateway_paused
model_provider_changed
```

---

## 27. Implementation milestones

### Milestone 0 — Repo skeleton

Deliver:

```text
pyproject.toml
frontend skeleton
CLI skeleton
data directory bootstrap
config loader
basic tests
```

Acceptance:

```bash
aletheia --help
aletheia gateway status
```

### Milestone 1 — Gateway + local server

Deliver:

```text
gateway start/stop/status/logs
FastAPI server
static dashboard serving
browser auto-open
PID handling
pause/resume
```

Acceptance:

```bash
aletheia gateway start
# browser opens dashboard
aletheia gateway status
aletheia gateway pause
aletheia gateway resume
aletheia gateway stop
```

### Milestone 2 — Storage

Deliver:

```text
SQLite schema
JSONL writer
Markdown writer
repositories
audit events
```

Acceptance:

```text
app.db created
audit.jsonl created
basic CRUD works
```

### Milestone 3 — Dashboard MVP

Deliver pages:

```text
Overview
Agent Runs
Opportunities
Proposals
Paper Trades
Positions
Strategy Lifecycle
Reports
Settings
```

Acceptance:

```text
Dashboard loads from local backend
Settings can change permission mode
Pause/resume works
```

### Milestone 4 — Model provider layer

Deliver:

```text
OpenAI-compatible adapter
Anthropic-compatible adapter
Codex CLI adapter stub/implementation
gateway model commands
healthcheck
```

Acceptance:

```bash
aletheia model status
aletheia model test
```

### Milestone 5 — Agent Runtime

Deliver:

```text
scheduler
agent runs
tool registry
permission enforcement
Research Agent
Market Observer
Dreaming Agent light
```

Acceptance:

```text
Agent run creates research note and opportunity
Agent run visible in Dashboard
Tool calls logged
```

### Milestone 6 — Knowledge Base

Deliver:

```text
Research Notes
Strategy Memory
Daily Reviews
Dreaming Logs
Archive/deprecate behavior
SQLite index
```

Acceptance:

```text
Research note appears in Dashboard
Dreaming log appears in Dashboard
No hard delete path for KB
```

### Milestone 7 — Bitget adapter

Deliver:

```text
Bitget auth
Spot market data
USDT futures market data
Demo mode header support
Account/balance fetch
Order placement adapter, gated
```

Acceptance:

```bash
aletheia bitget test
```

With demo credentials:

```text
Can fetch balances
Can fetch tickers/candles
Can place demo order only when allowed by execution mode
```

### Milestone 8 — Risk Engine + Paper Broker

Deliver:

```text
risk rules
risk decisions
paper broker
orders/fills/positions
PnL snapshots
```

Acceptance:

```text
Proposal -> RiskDecision -> PaperOrder -> Fill -> Position
visible in Dashboard
```

### Milestone 9 — Strategy Lifecycle

Deliver:

```text
strategy records
paper_candidate/paper_running/paper_passed states
promotion gates
reports
```

Acceptance:

```text
Strategy lifecycle visible
Agent can create strategy hypothesis
Paper experiment records update lifecycle
```

### Milestone 10 — Telegram light adapter

Deliver:

```text
/status
/pause
/resume
/latest
allowed chat IDs
```

Acceptance:

```text
Telegram can view status and pause/resume
Audit events written
```

### Milestone 11 — Live-limited gated path

Deliver:

```text
live_disabled default
live unlock UI/CLI
first live confirmation flag
live_limited risk checks
```

Acceptance:

```text
No live order possible by default
Live order requires explicit unlock and risk approval
All actions audited
```

---

## 28. Testing requirements

### 28.1 Unit tests

Required:

```text
config loading
secret redaction
permission checks
risk checks
strategy lifecycle transitions
JSONL writing
SQLite repositories
model provider routing
Bitget request signing helper
```

### 28.2 Integration tests

Required:

```text
gateway start/status/stop
agent run -> opportunity
proposal -> risk decision
paper order -> fill -> position
permission mode switch
pause/resume behavior
```

### 28.3 Security tests

Required:

```text
secrets not in logs
secrets not in agent context
agent cannot call restricted tools
live disabled blocks live execution
risk hard limits cannot be modified by agent
Dashboard permission changes create audit events
```

### 28.4 Manual tests

Required checklist:

```text
Start gateway
Open dashboard
Switch permission mode
Run market observer
Create research note
Create paper proposal
Execute paper order
Pause all agents
Resume agents
Test Bitget demo connection
```

---

## 29. Safety defaults

The product should feel usable but conservative.

Initial config:

```yaml
permission_mode: Research
exchange_mode: demo
live_state: live_disabled
scheduler_enabled: true
autonomous_learning: true
autonomous_paper_trading: false
```

User can switch to `Paper Auto` from Dashboard.

`Auto` exists but should show clear status warnings.

No additional password is required for Dashboard switching, but every switch is audited.

---

## 30. Roadmap after MVP

Do not implement these in MVP unless explicitly requested.

### 30.1 Next markets

```text
Binance
OKX
Hyperliquid
DEX/Jupiter
xStocks/TQQQx
Polymarket/Kalshi
```

### 30.2 Advanced learning

```text
YouTube subtitles ingestion
public web search
RSS/news ingestion
ICT/SMC concept learning
local vector search
knowledge graph
```

### 30.3 Packaging

```text
Windows exe
tray icon
startup shortcut
Windows service install/uninstall
```

### 30.4 Advanced trading

```text
multi-strategy portfolio allocation
agent performance scoring
automated paper-to-live-limited promotion after first confirmation
richer backtesting
walk-forward validation
```

### 30.5 Heavy architecture future only

```text
Postgres
Redis
Docker Compose
ClickHouse
message queue
multi-user auth
remote deployment
```

Keep these out of MVP.

---

## 31. Claude Code implementation instructions

When using Claude Code, follow this workflow.

### 31.1 Initial prompt

```text
You are implementing Aletheia Trading OS, a local-first AI-native agentic trading system.

Read IMPLEMENTATION_GUIDE.md fully before coding.

Do not build a Docker/Postgres/Redis system.
Do not fork OpenAlice.
Do not depend on Hermes Agent.
Do not enable live trading by default.
Do not expose secrets to agents, logs, reports, or Knowledge Base.

Implement the product as a local Python backend + CLI gateway + static React dashboard served from localhost.

MVP supports only:
- Bitget
- BTC/ETH/SOL
- Spot and USDT futures
- Demo/paper first
- Live API only behind live_disabled gate

Proceed milestone by milestone.
Before each milestone, create a short plan.
After each milestone, run tests and update CHANGELOG.md.
```

### 31.2 Coding rules for Claude Code

Claude Code must:

- Preserve architecture boundaries.
- Implement tests with each subsystem.
- Keep secrets redacted.
- Avoid overbuilding.
- Avoid adding unrequested exchanges.
- Avoid introducing Docker as required runtime.
- Use local-first storage.
- Keep live execution gated.

### 31.3 Change control

Any modification to these modules requires explicit change note:

```text
risk/
execution/
exchanges/bitget/auth.py
config/secrets.py
agent_runtime/permissions.py
```

Agent-generated code changes should be proposed, not silently applied, once the system is running in dogfood mode.

---

## 32. Acceptance criteria for MVP

MVP is complete when all are true:

1. `aletheia gateway start` launches background service and dashboard.
2. Dashboard opens automatically by default.
3. `aletheia gateway status/stop/pause/resume` works.
4. SQLite + JSONL + Markdown storage is initialized.
5. Dashboard shows Overview, Agent Runs, Opportunities, Proposals, Paper Trades, Positions, Strategy Lifecycle, Reports, Settings.
6. Model provider layer supports OpenAI-compatible, Anthropic-compatible, and Codex CLI mode.
7. Agent Runtime can create research notes and opportunities.
8. Knowledge Base stores Research Notes, Strategy Memory, Daily Reviews, Dreaming Logs.
9. Bitget adapter can test demo connection.
10. BTC/ETH/SOL market data can be fetched.
11. Paper/demo proposal can become risk decision, order, fill, position, PnL.
12. Risk Engine blocks disallowed symbols and live execution by default.
13. Permission mode changes are visible and audited.
14. Telegram `/status`, `/pause`, `/resume`, `/latest` work if configured.
15. No live trade can occur unless live is explicitly unlocked.
16. Secrets are not present in logs, reports, agent context, or KB.

---

## 33. Source notes for implementer

Use official/latest docs during implementation, especially for Bitget endpoints and model provider protocols.

Reference links:

- Bitget API docs: https://www.bitget.com/api-doc/common/intro
- Bitget Demo Trading: https://www.bitget.com/api-doc/common/demotrading/restapi
- OpenAlice GitHub: https://github.com/TraderAlice/OpenAlice
- Freqtrade docs: https://www.freqtrade.io/en/stable/
- Hummingbot: https://hummingbot.org/
- OpenAI Codex CLI docs: https://developers.openai.com/codex/cli/reference
- Anthropic docs: https://docs.anthropic.com/

Always re-check official Bitget docs before implementing order endpoints.

---

## 34. Final product summary

Aletheia Trading OS should feel like:

```text
OpenAlice-style local trading OS
+ Hermes-style always-running agent gateway
+ Trading-as-Git memory and strategy evolution
+ FreqUI-like bot dashboard
+ Hummingbot-like strategy operations thinking
+ TradingView-inspired chart/trade visibility
+ hard-coded safety boundaries
```

The final MVP is not a profit machine. It is a controlled local agentic trading organism:

```text
It observes.
It learns.
It dreams.
It proposes.
It paper-trades.
It reviews.
It evolves.
It can eventually trade live-limited, but only inside hard boundaries.
```
