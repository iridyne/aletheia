# Local Paper Account Controls Implementation Plan

> Date: 2026-07-11
>
> Scope: the `paper_internal` / “No exchange” simulation only. This plan does
> not alter any external exchange account, credential, live-order path, or risk
> limit configuration.

## Problem

`paper_internal` currently creates local fills and positions but has no local
cash account. Its configured per-position limit prevents unbounded individual
orders, but the operator cannot see, fund, withdraw from, or reset the paper
account. A zero-balance first run also has no explicit starting-capital record.
The existing `capital_events` table is deliberately not suitable: it is a
cross-broker performance-chart baseline, not a spendable account balance, and
does not reserve funds for open simulated positions.

## Account Model

Create an append-only `paper_account_events` ledger for `paper_internal` only.
Each row has an immutable id, timestamp, action, USDT delta, human/agent actor,
source, optional position id, note, and before/after balances. Permitted actions:

| Action | Delta | Trigger |
|---|---:|---|
| `initial_funding` | positive | first local-account use only |
| `manual_credit` | positive | operator adds funds |
| `manual_debit` | negative | operator removes funds |
| `manual_set_balance` | signed difference | operator sets cash balance |
| `position_opened` | zero | audit link for a new local position |
| `position_closed` | realized PnL | close settlement link |

`cash_balance_usdt` is the sum of ledger deltas. `reserved_margin_usdt` is
derived from every current open `paper_internal` position as
`quantity * entry_price / max(leverage, 1)`. This treats the existing simulated
spot/short/futures position model uniformly and avoids pretending that a short
spot position owns a real asset inventory. `available_balance_usdt` is
`max(0, cash_balance_usdt - reserved_margin_usdt)`. `equity_usdt` is cash plus
open-position unrealized PnL. Values are rounded for display, while ledger
amounts retain normal database precision.

Opening a position does not write a negative cash transaction; the reserve is
derived from the open position. Closing it releases the reserve automatically
because it is no longer open, then records the realized PnL once in the ledger.
This avoids double-counting reserve release and makes reconstruction from
positions plus immutable events deterministic.

### Initialization Rule

At the first local-paper account read or local fill, if **no ledger event exists**,
write one `initial_funding` event for `200 USDT`. The trigger is absence of an
account record, not “current balance equals zero”: after a deliberate manual
withdrawal or a “set to 0” action, the account must remain zero and block new
orders. Initial funding is idempotent under concurrent reads/writes.

## Changes By Module

1. `aletheia/storage/migrations.py`, `models.py`, `repositories.py`
   - Add the append-only table, model, indexes, and small repository functions.
   - Use the existing thread-local SQLite connection and pre-write transaction
     convention; no destructive migration or historical-position rewrite.

2. `aletheia/portfolio/paper_account.py` (new)
   - Centralize initialization, ledger writes, position-derived reserve, summary,
     affordability, and settlement. No exchange API calls.
   - Expose a bounded recent-event view for the UI and agent context.

3. `aletheia/execution/paper_broker.py` and `aletheia/portfolio/pnl.py`
   - For `paper_internal` only, ensure funding before a fill, record the opened
     position link, and settle realized PnL exactly once after a successful close.
   - External demo/live broker paths retain their current behavior.

4. `aletheia/risk/engine.py`
   - In local-paper mode, tighten the existing configured per-order cap by the
     local available balance. A missing/uninitialized account is initialized
     locally; an explicit zero balance rejects an order. External balance cache
     behavior is unchanged.
   - Preserve all existing risk checks, live gates, and configured hard limits.

5. `aletheia/server/routes/settings.py`
   - Add read-only local-account summary and explicit human-only mutations:
     credit, debit, set cash balance, close all local positions, close all local
     spot positions.
   - Mutations reject unless `paper_execution_mode == paper_internal`; close
     operations settle at the latest stored market price and fail plainly where a
     price is unavailable rather than inventing a price.
   - Every action writes both an account-ledger row and the hash-chained audit
     event with actor, source, before/after values, and affected position ids.

6. `aletheia/agent_shell/trading_context.py`
   - Add a read-only local account summary and latest human adjustments to the
     decision context when the current path is local paper. This explains capital
     changes to the agent; agents receive no callable control to change funds or
     positions.

7. `frontend/src/api/client.ts`, `frontend/src/pages/Settings.tsx`
   - Add a “Local Paper Account” execution-settings card, rendered only for
     `Paper (internal)`: cash, reserved margin, available balance, equity,
     recent events, amount controls, and explicitly confirmed close-all actions.
   - Refresh settings, positions, portfolio, and account summary after a mutation.
   - Do not show the card as an external exchange balance and do not add a live
     trading control.

8. Documentation and contracts
   - Update `docs/governance/api_reference.md` for every endpoint.
   - Update `docs/governance/data_model.md` for the new table.
   - Add executable claims for initialization, explicit-zero preservation,
     local-only scope, affordability, settlement, audit logging, and UI/API
     presence. Register the new spec in conformance collection.

## Safety and Concurrency

- All mutations are human dashboard routes; no agent tool, LLM prompt, or
  scheduler receives a mutation capability.
- A debit or set-balance operation cannot leave cash negative. It may leave
  available balance at zero while positions remain open; no new position can
  then pass the local balance check.
- Close-all actions never delete positions. They invoke the normal close/PnL
  path so historical positions, fills, realized PnL, and reviews remain intact.
- A `threading.Lock` scoped to ledger initialization/mutation avoids duplicate
  first funding and concurrent balance edits. The implementation keeps blocking
  SQLite work out of async handlers with `asyncio.to_thread`.
- The default funding amount is a code constant (`200.00 USDT`) in the local
  account service, not a new general configuration setting. This avoids a new
  Settings/config coupling for a one-time bootstrap value.

## Test-First Verification

1. New `tests/test_paper_account.py`
   - first read initializes exactly one 200-USDT event;
   - manual zero stays zero across subsequent reads;
   - manual credit/debit/set preserve an auditable ledger;
   - open positions reserve collateral and a close settles PnL once;
   - local affordability rejects overspending without affecting external modes.
2. Extend risk and paper-broker tests for the local balance boundary.
3. Add route tests for local-only mutation rejection, audit events, and close-all
   behavior.
4. Add frontend/API-source regression assertions and an executable SDD claim.
5. Run focused tests, the affected specification suite, architecture fitness,
   then `cd frontend && npm run build`. Do not restart the shared gateway from
   this worktree.

## Deliberate Non-Goals

- No real exchange balances, orders, credentials, or reconciliation changes.
- No historical reconstruction or mutation of prior paper trades.
- No asset-by-asset spot wallet: the current simulator models directional
  positions, so the honest operator action is “close local spot positions,” not
  “delete spot assets.”
- No automatic replenishment after an operator intentionally sets balance to zero.
