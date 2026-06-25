# Futures/Leverage — Round 2 Deferred Items

> These changes require modifying EXPERIMENT FREEZE paths. Execute once Round 1
> observation period ends and the freeze lifts.

## Prerequisites

Round 1 infrastructure (committed in this batch) provides:
- Per-tier leverage limits in config + risk engine enforcement
- `create_paper_proposal(leverage=...)` passthrough
- Account-level leverage ratio cap (check #17)
- Liquidation buffer guard (check #18)
- Frontend: Settings controls + Positions leverage column

## Deferred Changes

### 1. `agents/market_observer.py` — Emit perp opportunities

The market observer currently only emits spot opportunities. After unfreeze:
- Surface `funding_rate`, `open_interest`, `oi_delta_1h` from
  `universe_feature_snapshots` into the observer's output context
- When a perp instrument scores highly, emit an opportunity with
  `market="perp"` (the field already exists on Opportunity)

### 2. `agents/paper_trader*.py` — Use leverage + market_type from opportunity

- Read `opportunity.market` to determine `market_type` for the proposal
- When `market_type == "usdt_futures"`, choose leverage via:
  - `get_max_leverage(symbol, is_live, cfg)` as the ceiling
  - Confidence-based heuristic: high → tier max, medium → tier max / 2, low → 1x
- Pass `leverage=N` to `create_paper_proposal()`
- Update `paper_trader_ranker` to handle derivatives-family scoring

### 3. `agent_shell/trading_context.py` — Futures context for PI Agent

Add a `_build_futures_context(cfg, open_positions)` helper that injects:
```python
{
    "leverage_limits": {symbol: {tier, max_demo, max_live}},
    "account_leverage_ratio": current_ratio,
    "available_margin_pct": (1.0 - used_margin / equity) * 100,
    "positions_near_liquidation": [...],
}
```

### 4. `agent_shell/trading_decision.py` — Parse leverage from LLM output

- Add `leverage: int = 1` to the trade proposal JSON schema
- After parsing, clamp to `get_max_leverage()` ceiling
- Forward to `create_paper_proposal(leverage=parsed_leverage)`

### 5. `aletheia/universe/service.py` — Inject perp members

- When `usdt_futures` is enabled, `sync_registry` already seeds perp assets
- The remaining gap: volume-ranking injection at line 157 only creates spot
- After unfreeze, conditionally inject perp duplicates for volume-ranked symbols
  that are in `cfg.exchange.futures_symbols`

### 6. `config.yaml` — Add perp observed_instruments

```yaml
market_data:
  observed_instruments:
    - "binance:BTC/USDT:spot"
    - "binance:BTC/USDT:perp"   # NEW
    - "binance:ETH/USDT:spot"
    - "binance:ETH/USDT:perp"   # NEW
    - "binance:SOL/USDT:spot"
    - "binance:SOL/USDT:perp"   # NEW
```

### 7. `strategy/` — market_type affinity on StrategyRecord

- Add `market_type_affinity: str = "both"` field
- Strategy designer prompted to specify spot vs futures intent
- Backtest gates incorporate funding rate drag for perp strategies

### 8. `learning/` — Leverage-adjusted lesson metrics

- Add `market_type` to lesson schema
- Track `leverage_adjusted_return = pnl / (notional / leverage)`
- Prevents learning "more leverage = better" from lucky streaks

## Validation After Unfreeze

1. Open a demo futures position via PI Agent with leverage > 1x
2. Verify CausalChain shows all nodes (opportunity → strategy → risk → execution)
3. Verify account leverage ratio updates live on the dashboard
4. Verify liquidation price displayed correctly
5. Run full test suite with no regressions
