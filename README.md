# Aletheia Trading OS

Local-first, file-based, AI-native, self-learning agentic trading system —
paper by default, live only behind an explicit human unlock.

> **Running it unattended?** See **[docs/operations/runbooks/RUNBOOK.md](docs/operations/runbooks/RUNBOOK.md)** —
> going live for paper trading, the brakes, self-upgrade, and health monitoring.
> For the phase-by-phase local runtime hardening plan, see
> **[docs/operations/architecture/STABILITY_TARGET_ARCHITECTURE.md](docs/operations/architecture/STABILITY_TARGET_ARCHITECTURE.md)**.

## For AI agents

If you are an AI agent (Claude, Codex, or any autonomous system) working in
this repo, **read [`AGENTS.md`](AGENTS.md) first** — it is the mandatory root
instruction file, not an optional style guide. When your session is inside
Orca, also run `orca skills get orca-cli` and read
[`docs/operations/development/ORCA_CLI_WORKFLOW.md`](docs/operations/development/ORCA_CLI_WORKFLOW.md)
before any repo-mutating action.

## Quick start (humans)

```bash
uv sync
uv run aletheia-install
aletheia gateway start   # dashboard at http://127.0.0.1:8787
```

`aletheia-install` writes a small reroute command into
[uv's tool bin](https://docs.astral.sh/uv/concepts/tools/) so that `aletheia
...` from any directory delegates back to this checkout's lockfile and virtual
environment. Rerun the installer after moving the clone; if uv reports its
tool bin is not on `PATH`, run `uv tool update-shell` once and open a new shell.

Configure everything (model provider, exchange credentials, agent runtime,
live-trading unlock) **inside the dashboard's Settings page** — full
step-by-step in the runbook. Live trading stays disabled until you explicitly
unlock it.

The CLI is the bootstrap surface only:

```bash
uv run aletheia gateway start | restart | enable-autostart
uv run aletheia doctor    # single source of truth: real local state
```

## Dev preview

For UI-only iteration on a worktree, use Vite HMR on `http://127.0.0.1:5173`
(read-only proxy to the stable `:8787` API by default). Contract, isolated
backend, and Windows commands live in
[Development preview](docs/operations/development/DEVELOPMENT_PREVIEW.md).

## Find your document

| Task | Read |
| --- | --- |
| Run the system, take it live, monitor health | [Runbook](docs/operations/runbooks/RUNBOOK.md) |
| Contribute code or docs (human) | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Contribute code as an AI agent | [AGENTS.md](AGENTS.md) |
| Understand the architecture | [System Architecture Guide](docs/design/architecture/SYSTEM_ARCHITECTURE_GUIDE.md) |
| Report a vulnerability | [SECURITY.md](SECURITY.md) |

AGENTS.md contains the deeper *Read when needed* task-to-document routing.

## Architecture

- Python 3.12 backend (FastAPI + Typer CLI), Bitget spot + USDT futures (BTC/ETH/SOL)
- SQLite + JSONL + Markdown storage; secrets in OS keyring / encrypted file (never in config or logs)
- React/Vite dashboard served from localhost — primary control surface
- APScheduler-driven agent runtime (market-data loop, market observer, dreaming)
- 7 agents call a configurable LLM (OpenAI-compatible / Anthropic-compatible / Codex CLI)
- Risk Engine enforces the full §21.3 check matrix + a global kill switch; every order flows through the Execution Gateway (paper broker or live Bitget broker)
- Paper/demo first; live strictly gated behind explicit human unlock, with order reconciliation against the exchange

### System overview

![System Overview](docs/diagrams/rendered/01_system_overview.png)

### Trading flow (system operator sole decision maker)

![Trading Flow](docs/diagrams/rendered/trading_flow_pi_sole_decision_maker.png)

> Detailed flow diagrams (data collection, signal discovery, decision process,
> execution, position management, feedback loop, strategy lifecycle) live in the
> [System Architecture Guide](docs/design/architecture/SYSTEM_ARCHITECTURE_GUIDE.md).

## Safety invariants

- Live trading off by default (`live_disabled`); first unlock requires human action
- Agents cannot call restricted tools, modify risk limits, credentials, or the gateway
- Every config, permission, live-state, and trade action writes a JSONL audit event
- API keys are write-only over the API and never returned to the client

## Tests

```bash
uv run pytest    # full local suite; use focused tests during narrow fixes
```
