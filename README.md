# Aletheia Trading OS

Local-first, file-based, Trading-as-Git, AI-native, self-learning agentic trading system.

## Quick Start

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
aletheia --help
aletheia gateway start
```

## Architecture

- Python 3.12 backend (FastAPI + Typer CLI)
- SQLite + JSONL + Markdown storage
- React/Vite dashboard served from localhost
- APScheduler-driven agent runtime
- Bitget spot + USDT futures (BTC/ETH/SOL)
- Paper/demo first, live strictly gated
