# Aletheia Trading OS

Local-first, file-based, Trading-as-Git, AI-native, self-learning agentic trading system.

> **Running it unattended?** See **[docs/RUNBOOK.md](docs/RUNBOOK.md)** — how to take
> the system live for paper trading, the brakes, self-upgrade, and health monitoring.

## Quick Start

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run aletheia gateway start      # opens dashboard at http://127.0.0.1:8787
```

Then configure everything **in the dashboard** (Settings page), OpenAlice-style:

1. **Model Provider** — set base URL, model, and paste your API key (write-only;
   never shown again). Click **Test** to health-check the endpoint.
2. **Exchange Credentials** — paste Bitget demo (or live) API key / secret /
   passphrase. Click **Test Connection**.
3. The agent runtime starts automatically and runs on a schedule; watch it on the
   **Agent Runs**, **Opportunities**, and **Dreaming** pages, or trigger agents manually.
4. Approve paper proposals on **Proposals**; see fills/PnL on **Positions** and **Charts**.
5. Live trading stays **disabled** until you explicitly unlock it on the Settings page.

CLI equivalents exist but are secondary, e.g.:

```bash
uv run aletheia model set --provider openai_compatible --base-url URL --model NAME --api-key
uv run aletheia secrets set bitget.demo.api_key
uv run aletheia bitget test
uv run aletheia gateway status | pause | resume | stop
```

## Architecture

- Python 3.12 backend (FastAPI + Typer CLI), Bitget spot + USDT futures (BTC/ETH/SOL)
- SQLite + JSONL + Markdown storage; secrets in OS keyring / encrypted file (never in config/logs)
- React/Vite dashboard served from localhost — primary control surface
- APScheduler-driven agent runtime (market-data loop, market observer, dreaming)
- 7 agents call a configurable LLM (OpenAI-compatible / Anthropic-compatible / Codex CLI)
- Risk Engine enforces the full §21.3 check matrix + a global kill switch; all
  orders flow through the Execution Gateway (paper broker or live Bitget broker)
- Paper/demo first; live strictly gated behind explicit human unlock, with order
  reconciliation against the exchange

## Safety invariants

- Live trading off by default (`live_disabled`); first unlock requires human action
- Agents cannot call restricted tools, modify risk limits, credentials, or the gateway
- Every config/permission/live/trade action writes a JSONL audit event
- API keys are write-only over the API and never returned to the client

## Tests

```bash
uv run pytest        # 272 tests: unit, integration, security, §32 acceptance
```
