# Project Context

## Background

Aletheia Trading OS is a personal AI-native trading system for solo automated trading on Bitget (BTC/ETH/SOL USDT perpetuals + tokenized equities). Local-first, file-based "Trading-as-Git" architecture where 7+ AI agents orchestrate market observation through order execution.

## Goals

- Automate market analysis and trade execution with AI agent oversight
- Maintain strict safety: risk limits, execution gateway, human approval for live mode
- Local-first architecture: all data in SQLite/JSONL/Markdown, no cloud dependencies
- Self-evolving: experience loop feeds post-trade lessons back into strategy design

## Known Constraints

- Live trading disabled by default (requires explicit unlock)
- Bitget API rate limits apply
- All changes to trust boundary modules require user authorization
- SQLite concurrency: WAL + busy_timeout + threading.local connections + max_workers=3
- Market data providers are per-call, never cached (aiohttp event-loop binding)
- `code_drift` self-heal restart after commits is EXPECTED behavior, not a failure
- Facts (gateway state, credentials, loop activity) never go in git — use `aletheia doctor`
- Daily dashboard/release acceptance runs on 127.0.0.1:8787 (frontdoor proxies to backend :8788); Worktree HMR runs on :5173 and is read-only against :8787 by default. See `docs/operations/development/DEVELOPMENT_PREVIEW.md`.
