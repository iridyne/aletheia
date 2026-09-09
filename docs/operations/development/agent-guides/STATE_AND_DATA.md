# Agent Guide: State and Data

Read before changing persistence, market data, accounting, config, symbols, or
async API behavior.

- Use a fresh `get_market_data_provider()` per call. `BitgetClient` is
  execution-only; never substitute another venue for prices or funding. The
  documented Bitget-only ON-series xStock call site is the sole exception.
- Runtime state belongs in `~/.aletheia/` or an absolute `ALETHEIA_DATA_DIR`,
  never `aletheia/data/` or repository-root `data/`. Keep `/data/` root-anchored
  in `.gitignore` for legacy migration only.
- Store symbols as `BTC/USDT`; convert only at the execution adapter. Frontend
  symbols use `fmtSymbol(symbol, marketType)`.
- Preserve SQLite safeguards: process-wide `_WRITE_LOCK`, WAL plus 30-second
  `busy_timeout`, one thread-local connection, and `was_in_tx` captured before
  writes; commit only when it was false. Keep scheduler `max_workers <= 3`; no
  transaction may span `await` or network/I/O.
- Current external closes use verified exchange evidence through
  `aletheia/portfolio/outcome_closure.py`; preserve Position, Order, Fill and
  outcome identity. Retired simulator accounting is read-only history, not a
  repair or settlement path; see [archived account controls](../../../design/history/local_paper_account_controls.md).
  Never rewrite historical account events or convert them into external evidence.
- New config fields require Settings controls; users do not edit config files.
  After frontend changes run `cd frontend && npm run build`.
- Async FastAPI handlers never block the event loop. Read
  [async performance](../ASYNC_PERFORMANCE.md) and measure hot endpoints on
  backend port `8788`.
