# Current Task

## Status: IDLE

No active task assigned. System is in maintenance-ready state.

## Last Completed

- 2026-06-08: xStocks 接入（Bitget ON-series tokenized equities，observation-only，asset_class="equity"）
- 2026-06-08: 管道 bug fix 三项（confidence default / datetime tz / xStocks ticker snapshots）
- 2026-06-08: Chart blackout fix (stale-process guard + provider factory consolidation)
- 2026-06-08: InstrumentId + Symbol Format Unification (exchange:symbol:market_type)
- 2026-06-08: Full-market analyzer (stock_observer, prediction_observer, Polymarket provider)

## Suggested Next Steps

1. 验证测试基线：`uv run pytest -q` — 应为 474 passed，1 既存 flaky
2. 启动 gateway：`uv run aletheia gateway start`
3. 手动触发验证管道：`aletheia agent run market_observer` → `aletheia agent run paper_trader`（确认 xStocks opportunity 产生但不开仓，crypto opportunity 正常进入 paper trade）
4. 验证 xStocks ticker 写入 market_snapshots：查询 DB `SELECT symbol, exchange FROM market_snapshots WHERE exchange='bitget'`
5. 开始 7–14 天 unattended paper trading run（stage-0 证据）
