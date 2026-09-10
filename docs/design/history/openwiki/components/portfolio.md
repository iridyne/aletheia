> Archived OpenWiki snapshot from `b7ec0d670`. This page is historical context,
> not current code, configuration, execution authority or acceptance criteria.

---
type: component
title: Portfolio Management
description: Position lifecycle management including paper account, PnL tracking, exit planning, protective barriers, and reconciliation.
tags: [portfolio, positions, pnl, paper-account, exits]
openwiki:
  roles: [domain, architecture]
  change_kinds: [public-api]
  source_paths:
    - aletheia/portfolio/position_manager.py
    - aletheia/portfolio/paper_account.py
    - aletheia/portfolio/pnl.py
    - aletheia/portfolio/exit_plan.py
    - aletheia/portfolio/exit_rules.py
    - aletheia/portfolio/protective_barrier.py
    - aletheia/portfolio/position_reconciler.py
    - aletheia/portfolio/local_funding.py
  symbols:
    - get_paper_account
    - close_position
    - effective_exit_plan
    - evaluate_market_exit_rules
    - activate_new_paper_position
    - ReconcileItem
    - LocalFundingResult
  test_paths:
    - tests/test_portfolio.py
    - tests/test_paper_account.py
    - tests/test_pnl.py
  invariants:
    - LIVE positions are NEVER auto-closed by the position manager.
    - The kill switch does NOT block closing — closing reduces risk.
    - Paper account is the sole capital source for paper_internal execution.
  validation_commands:
    - uv run pytest tests/test_portfolio.py
---

# Portfolio Management

The **Portfolio Management** system handles the full position lifecycle: entry execution, ongoing management, protective stops, exit planning, PnL tracking, and reconciliation. It is the durable state layer between execution and learning.

## Architecture

```mermaid
flowchart TD
    subgraph Entry
        A[Execution Gateway\nFilled Order] --> B[Position Manager\nNew Position]
        B --> C[activate_new_paper_position\nProtective Barrier]
    end

    subgraph Ongoing Management
        D[Position Manager\n30s cadence] --> E{Open Positions}
        E -->|Paper| F{Evaluate Exit}
        E -->|Live| G[Never Auto-Close]
        F -->|Stop/TP/Max-Hold| H[Close Position]
    end

    subgraph Exit Planning
        I[ExitPlan\nOperator or Default] --> J[Exit Rules\nDeterministic]
        J --> K{urgent_exit?|tighten_stop?|none?}
        K -->|urgent_exit| L[Immediate Close]
        K -->|tighten_stop| M[Adjust Stop]
        K -->|none| N[Continue]
    end

    subgraph PnL & Reconciliation
        H --> O[close_position\nFunding Validation]
        O --> P[Realized PnL\n+ Economics]
        P --> Q[Post-Trade Reviewer]
        R[Reconciler\n2min cadence] --> S{Classification}
        S -->|managed| T[Local + Exchange]
        S -->|external| U[Adoption Candidate]
        S -->|orphaned| V[Reconcile-Close]
    end

    subgraph Paper Account
        W[Paper Account\nCash + Margin] --> X[Position Sizing]
        W --> Y[Available Balance]
    end
```

## Position Manager (`portfolio/position_manager.py`)

Closes open **paper** positions by stop-loss / take-profit / max-hold.

**Key constraints:**
- **LIVE positions are NEVER auto-closed** (governance)
- **Kill switch does NOT block closing** — closing reduces risk
- No latest price → skip (never fabricate exit)
- `_PAPER_MODES = {"paper_internal", "bitget_demo", "binance_testnet", "okx_demo"}`
- `_OPEN_CLOSE_STATES`: pending/submitted/partial/partially_filled/submission_unknown/submission_rejected/venue_trade_rate_limited/materialization_missing/position_absence_unverified/overfilled

**Pending-close reconciliation:**
- Stale claims (`_escalate_stale_pending_close`)
- Invalid timestamps
- Venue trade rate limiting (Bitget 40725)

## Paper Account (`portfolio/paper_account.py`)

### `get_paper_account(event_limit=20) -> dict`

Returns cash balance, reserved margin, available balance, equity, economics completeness.

- `INITIAL_PAPER_CASH_USDT = 200.0`
- `_reserved_margin_usdt()`: sum of `quantity * entry_price / leverage`
- `_unsettled_local_closes()`: closed positions with in-flight funding economics
- `_FUNDING_GIVE_UP_HOURS = 24.0`: terminal "gave up" threshold

## PnL (`portfolio/pnl.py`)

### `close_position()`

Computes realized PnL with validated funding evidence.

**Key functions:**
- `_latest_price(symbol)`: `get_price_for_pnl()`
- `_validate_complete_funding_evidence()`: rejects claimed-complete funding handoffs that cannot be replayed (chronological events, sum matching, holding window bounds)
- `_build_exchange_upnl_index()`: exact broker/instrument/direction exchange uPnL matches

## Exit Plan (`portfolio/exit_plan.py`)

### `ExitPlan` Dataclass

Declarative exit intent with `stop_price`, `target_price`, `expected_horizon_hours`, `trailing`, `thesis_invalidation`, `provenance` ("operator"|"default"), `version`.

### `effective_exit_plan(position, cfg) -> ExitPlan`

Resolved plan for the executor — operator-authored levels where present, config defaults where absent. Mirrors `_effective_stop` / `_effective_take_profit` exactly.

### `load_exit_plan(position) -> ExitPlan`

Reads from flat `position` columns (NO config fallback).

## Exit Rules (`portfolio/exit_rules.py`)

### `evaluate_market_exit_rules(position, price, signal, cfg) -> MarketExitAction`

Deterministic, pure functions. Rule priority: `urgent_exit > tighten_stop > none`.

- **Urgent exit**: overwhelming counter-signal while losing (score ≥ `urgent_exit_score`, pnl ≤ `urgent_exit_max_pnl_pct`)
- **Tighten stop**: confident counter-signal, or confidence collapse (neutral + low score + losing)
- Uses `MarketSignalContext` (direction_bias, confidence_score, atr_pct, signal_age_min)

## Protective Barrier (`portfolio/protective_barrier.py`)

### `activate_new_paper_position(position_id) -> dict`

Atomically activates immutable protection for new paper positions. Creates:
- `PositionExitPlanVersion` (immutable plan with hash)
- `PositionProtectionState` (exact-instrument cursor, `next_bar_open_ms`)

**Trailing qualification (`_trailing_qualification_assignment`):** 90/10 treatment/control assignment via SHA-256 bucket. Only for `trade_school="short_term"` + `source="native"` positions.

## Position Reconciler (`portfolio/position_reconciler.py`)

### `ReconcileItem` Dataclass

Classifies positions as:
- **managed**: tracked locally AND on exchange
- **external**: on exchange, NOT tracked locally (adoption candidate)
- **orphaned**: open locally, NOT on exchange (reconcile-close candidate)

**Match identity:** `(canonical symbol, normalized market type, direction)`. Quantity tolerance: `_QTY_REL_TOL = 0.02` (2%).

**`_per_symbol_notional_cap_usdt()`:** Mirrors CUM-ADD cap from risk engine for external adoption.

## Local Funding (`portfolio/local_funding.py`)

### `LocalFundingResult` Dataclass

Deterministic funding economics for `paper_internal` futures. Consumes only durable funding schedule snapshots (`_METHOD = "active_entry_fill_notional_x_scheduled_rate"`).

- `_MAX_OBSERVATION_GAP = timedelta(minutes=15)`
- `_load_scheduled_snapshots()`: reads `funding_rate_snapshots` table
- `_load_exact_entry_fills()`: reconstructs from immutable Order→Position backlinks

## Key Invariants

1. **Paper/live isolation** — Paper positions are never auto-closed; live positions are never auto-closed by the manager.
2. **Kill switch exception** — Closing reduces risk, so the kill switch does NOT block closing.
3. **No fabricated exits** — No latest price → skip (never fabricate exit).
4. **Funding validation** — Rejects claimed-complete funding handoffs that cannot be replayed.
5. **Immutable protection** — New paper positions get atomic, hash-verified exit plans.

## Relationship to Other Components

- [Execution Gateway](../components/execution-gateway.md) — Creates positions from fills
- [Risk Engine](../components/risk-engine.md) — Pre-trade approval gate
- [Agent Decision Loop](../architecture/agent-decision-loop.md) — Creates proposals that become positions
- [Trading Loop](../architecture/trading-loop.md) — Full evidence pipeline
