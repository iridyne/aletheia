# Aletheia Trading OS

## Project Overview

AI-native agentic trading system. Local-first, file-based "Trading-as-Git" platform.
- **Target users**: Solo trader (project owner) running automated trading strategies
- **Core function**: 7 AI agents orchestrate market analysis, strategy generation, risk management, and order execution on Bitget
- **Stage**: MVP — working prototype, needs trust hardening, not feature expansion

## Architecture

- **Tech stack**: Python 3.12 | FastAPI | Typer CLI | APScheduler | SQLite + JSONL | React/Vite frontend
- **Core modules**: `aletheia/` — agents/, execution/, risk/, exchanges/, trading/, knowledge/, server/, gateway/
- **Data flow**: Market data → Agent analysis → Strategy proposal → Risk Engine approval → Execution Gateway → Bitget API
- **External deps**: Bitget API (spot/futures), LLM providers (OpenAI-compatible, Anthropic-compatible)
- **Frontend**: `frontend/` — React/Vite dashboard (localhost control surface). Single endpoint: `:8787` (gateway serves API + static). For UI iteration, use `scripts/dev.ps1` (runs `vite build --watch` + backend watcher; gateway auto-restarts on changes). Do NOT rebuild `aletheia/dashboard_static/` manually in a tight loop — `dev.ps1` handles it.
- **Tests**: `tests/` — 515 tests (unit, integration, security, acceptance). Run `uv run pytest -q`.

## Development Rules

- Smallest change principle — MVP, not greenfield
- No uncontrolled expansion: no new exchanges, no Docker/Postgres/Redis, no vector search, no web ingestion
- Trust boundaries: risk/, execution/, auth.py, client.py, secrets.py, permissions.py, live.py are change-controlled
- Trading state invariants: Orders flow through Execution Gateway only; live trading disabled by default
- Market data: always go through `get_market_data_provider()` (`aletheia/data/ccxt_provider.py`) — never import `BitgetClient` for market data (it's execution-context only, and its REST v2 rejects canonical `BTC/USDT` symbols). Providers are per-call, never cached (CCXT binds aiohttp to the creating event loop; the scheduler uses a fresh loop per job). Only exception: Bitget-only ON-series xStocks, which construct `CCXTProvider(exchange="bitget", fallback_exchange="bitget")` directly (see comments at the call sites).
- Symbol format: canonical `BTC/USDT` everywhere internally (storage, risk, PnL, opportunities); the concatenated `BTCUSDT` exists ONLY at the execution adapter boundary via `to_bitget()`. Converters/`InstrumentId` live in `aletheia/data/symbols.py`. A mismatch silently drops opportunities at `paper_trader`'s filter.
- SQLite concurrency (do not break): APScheduler's `ThreadPoolExecutor` and uvicorn async handlers race on one `data/app.db`. Three pillars must all hold — (1) WAL + `busy_timeout` (`storage/sqlite.py`); (2) one connection per thread via `threading.local` (never cache a connection across threads); (3) `was_in_tx = db.in_transaction` captured **before** every write in `repositories.py`, committing only `if not was_in_tx` — sqlite3 auto-opens a tx on INSERT/UPDATE/DELETE, so checking *after* the write never commits and hangs the WAL lock (the historical `database is locked` bug). Scheduler is capped at `max_workers=3` on purpose; don't raise it or add a 4th writer pattern.
- Config ↔ UI rule: every new field in `aletheia/config/schema.py` MUST have a corresponding control in the Web frontend settings page. Users never edit `data/config.yaml` directly — all operational config is managed through the dashboard UI.
- **WebUI 静态文件构建规则**:
  - `aletheia/dashboard_static/` 必须提交到 Git(预构建，确保克隆后即可用)
  - 修改前端后运行 `cd frontend && npm run build` 更新静态文件并提交
  - Gateway 后台启动依赖已构建的静态文件(**不**自动运行 Vite watch)
  - 开发迭代用 `scripts/dev.ps1`(独立 Vite watch + 后端 watcher)
  - 启动前会运行 preflight checks，静态文件缺失时**拒绝启动并给出修复建议**
- Priority: State consistency > Safety > Auditability > Semantic honesty > Tests > Capability > Agent autonomy

## Gateway 进程管理

Gateway 是三进程架构（supervisor → frontdoor:8787 + backend:8788），由 `lifecycle.py` 统管。**不要手动 kill 进程**，用 CLI 命令：

```
uv run aletheia gateway restart --no-browser   # 一键重启（tree-kill + 孤儿清扫 + 端口释放 + 健康验证）
uv run aletheia gateway stop                    # 停止
uv run aletheia gateway start --no-browser      # 启动（已运行则跳过）
uv run aletheia gateway status                  # 查看 PID、端口、暂停状态
uv run aletheia gateway logs [-n 50]            # 尾部日志
```

- 代码改动后需要重启 Gateway 才能生效（scheduler 跑的是内存中的旧代码）
- `--no-browser` 避免每次重启弹浏览器；省略则自动打开 dashboard
- Windows Task Scheduler 看门狗每 5 分钟自动拉起（`enable-autostart` 已开启）
- PID 文件在 `data/gateway.pid`；孤儿检测基于 cmdline 标记，不会误杀其他 Python 进程

## Repo Setup

- **`aletheia/data/` is the source package** for the data layer (`symbols.py`, `ccxt_provider.py`, `providers.py`, `cache.py`, `polymarket_provider.py`, `yfinance_provider.py`) — imported by `risk/`, `execution/`, `trading/`, and the agents. It is distinct from the repo-root `data/` runtime dir (`get_data_dir()` → `<repo>/data`, holding `config.yaml`/`app.db`/logs, which IS gitignored). `.gitignore` must keep the runtime rule root-anchored as `/data/`; a bare `data/` also matches `aletheia/data/` and silently excludes the source package from git, breaking every clone. After cloning, sanity-check with `uv run python -c "import aletheia.risk.engine"` and `uv run pytest -q`.

## AI Collaboration Rules

- **意图 / 事实分离铁律(双机开发,见 `docs/_ai/DEV_PROTOCOL.md`)**:git 同步的是*意图*(代码 + 文档);它**不**同步*运行态事实*(本地 `app.db`/`config.yaml`/secrets/gateway 进程)。**绝不把"gateway 在跑 / 凭证已存 / 观察期第 N 小时 / demo 单已落"这类运行态事实写进任何 git 文档**——它在另一台机器上必然过期。文档只写机器无关、长期有效的意图。
- **会话开始**:(1) `git pull --ff-only` 拿最新意图;(2) 运行 `aletheia doctor` 拿真实事实——**事实的唯一来源是机器本身,不是文档叙事**;(3) 再读 CLAUDE.md, AGENTS.md, docs/_ai/PROJECT_CONTEXT.md, docs/_ai/CURRENT_TASK.md(纯计划), docs/_ai/AI_HANDOFF.md(变更史)。
- End each session by updating: docs/_ai/AI_HANDOFF.md, docs/_ai/DEVLOG.md, docs/_ai/CURRENT_TASK.md(只更新计划/意图,不写运行态)
- Log architectural decisions in: docs/_ai/DECISIONS.md
- AGENTS.md contains detailed governance rules — always defer to it for safety boundaries
- **Backend issues** (specific bugs / contract drift / dead code with file:line): `docs/issues/BACKEND_ISSUES.md`. Append new findings here, not in `docs/references/north_star.md` (which is the long-term direction map).
- **Frontend issues** (§23 spec gaps / UI honesty breaches / engineering basics with file:line): `docs/issues/FRONTEND_ISSUES.md`. Same severity legend as backend issues; do not bypass backend gaps with fake frontend data — entries that depend on backend fixes are explicitly cross-referenced.

## Safety Rules

- Never read or output API keys, exchange secrets, or credentials
- Never commit .env, .secret.key, secrets.enc, or token files
- Never enable live trading without explicit user confirmation
- Never modify risk limits, credentials, or execution paths without user approval
- Never place live orders or unlock live mode autonomously
