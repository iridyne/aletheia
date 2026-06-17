# Aletheia Trading OS

## Project Overview

AI-native agentic trading system. Local-first, file-based "Trading-as-Git" platform.
- **Target users**: Solo trader (project owner) running automated trading strategies
- **Core function**: 7 AI agents orchestrate market analysis, strategy generation, risk management, and order execution on Bitget
- **Stage**: MVP — working prototype, needs trust hardening, not feature expansion

## Architecture

- **Data flow**: Market data → Agent analysis → Strategy proposal → Risk Engine approval → Execution Gateway → Bitget API
- **External deps**: Bitget API (spot/futures), LLM providers (OpenAI-compatible, Anthropic-compatible)
- **Frontend**: `frontend/` — React/Vite dashboard, single endpoint `:8787`. Dev iteration: `scripts/dev.ps1` (Vite watch + backend watcher). Do NOT rebuild `aletheia/dashboard_static/` manually in a tight loop.
- **Tests**: `tests/` — 515+ tests. See **Testing Strategy** below.

## Development Rules

- Smallest change principle — MVP, not greenfield
- No uncontrolled expansion: no new exchanges, no Docker/Postgres/Redis, no vector search, no web ingestion
- Trust boundaries (change-controlled — touch only for tasks that explicitly require it):
  `aletheia/risk/`, `aletheia/execution/`, `exchanges/bitget/auth.py`, `exchanges/bitget/client.py`,
  `config/secrets.py`, `agent_runtime/permissions.py`, `server/routes/live.py`
- Trading state invariants: Orders flow through Execution Gateway only; live trading disabled by default
- Market data: always go through `get_market_data_provider()` (`aletheia/data/ccxt_provider.py`) — never import `BitgetClient` for market data (execution-context only; REST v2 rejects canonical `BTC/USDT`). Providers are per-call, never cached (CCXT binds aiohttp to the creating event loop). Only exception: Bitget-only ON-series xStocks, which construct `CCXTProvider(exchange="bitget", fallback_exchange="bitget")` directly (see comments at call sites).
- Symbol format: canonical `BTC/USDT` everywhere internally (storage, risk, PnL, opportunities); `BTCUSDT` exists ONLY at the execution adapter boundary via `to_bitget()`. Converters/`InstrumentId` live in `aletheia/data/symbols.py`. A mismatch silently drops opportunities at `paper_trader`'s filter.
- SQLite concurrency (do not break): Three pillars must all hold — (1) WAL + `busy_timeout` (`storage/sqlite.py`); (2) one connection per thread via `threading.local` (never cache a connection across threads); (3) `was_in_tx = db.in_transaction` captured **before** every write in `repositories.py`, committing only `if not was_in_tx` — sqlite3 auto-opens a tx on INSERT/UPDATE/DELETE, so checking *after* the write never commits and hangs the WAL lock (the historical `database is locked` bug). Scheduler capped at `max_workers=3`; don't raise it.
- Config ↔ UI rule: every new field in `aletheia/config/schema.py` MUST have a corresponding control in the Web frontend settings page. Users never edit `data/config.yaml` directly.
- WebUI static build: `aletheia/dashboard_static/` must be committed to git (pre-built). After frontend changes run `cd frontend && npm run build` and commit. Gateway startup rejects if static files are missing.
- GitHub Actions failures: disable the broken workflow to stop alerts; do not archive the repo.
- Priority: State consistency > Safety > Auditability > Semantic honesty > Tests > Capability > Agent autonomy

## Testing Strategy

全量测试 ~5min（515+ tests，I/O-bound，SQLite setup/teardown 是瓶颈，并行无收益）。

- **日常**：`uv run pytest tests/test_xxx.py -q`（秒级）；针对性用 `-k "knowledge or dreaming"`
- **提交前**：后台跑全量，不阻塞工作流
- **全量命令**：`uv run pytest tests/ -q --tb=line`

不等全量完成再继续工作——后台跑，有失败再回头修。

## Gateway 进程管理

三进程架构（supervisor → frontdoor:8787 + backend:8788），`lifecycle.py` 统管。不要手动 kill，用 `uv run aletheia gateway <restart|stop|start|status|logs>`。代码改动后必须 restart 才能生效。`--no-browser` 避免弹浏览器。Task Scheduler 看门狗每 5 分钟自动拉起。

## Repo Setup

**`aletheia/data/` is the source package** (symbols.py, ccxt_provider.py, etc.) — distinct from repo-root `data/` runtime dir (gitignored). `.gitignore` must keep the runtime rule root-anchored as `/data/`; a bare `data/` silently excludes the source package from git. After cloning verify: `uv run python -c "import aletheia.risk.engine"`.

## AI Collaboration Rules

意图/事实分离：git 同步**意图**（代码+文档），绝不写运行态事实（gateway 进程/app.db/config.yaml/凭证）进任何 git 文档——在另一台机器上必然过期。详见 `docs/_ai/DEV_PROTOCOL.md`。

会话开始：`git pull --ff-only` → `aletheia doctor` → 读 CLAUDE.md / AGENTS.md / CURRENT_TASK.md / AI_HANDOFF.md。
会话结束：更新 `docs/_ai/AI_HANDOFF.md`、`DEVLOG.md`、`CURRENT_TASK.md`（只写计划/意图，不写运行态）。架构决策记入 `docs/_ai/DECISIONS.md`。

Issue 分工：`docs/issues/BACKEND_ISSUES.md`（bugs/contract drift/dead code，带 file:line）；`docs/issues/FRONTEND_ISSUES.md`（UI spec gaps/honesty breaches）。

## Safety Rules

- Never read or output API keys, exchange secrets, or credentials
- Never commit .env, .secret.key, secrets.enc, or token files
- Never enable live trading without explicit user confirmation
- Never modify risk limits, credentials, or execution paths without user approval
- Never place live orders or unlock live mode autonomously
