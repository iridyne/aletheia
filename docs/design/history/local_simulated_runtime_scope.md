# Archived local simulated runtime scope

`crypto-stock-linked-paper-mainline-v2` declared `paper_internal` as its active
execution mode, with `trading_decision.evaluate_trading_opportunity` as decision
authority and a paper wrapper before Gateway. That simulator route required Paper
permission and could pass exchange completeness without an external account.
Startup also accepted supported demo adapters by unioning a hardcoded mode set
with the scope declaration; automatic update accepted `paper_internal/local_paper`
as a healthy deployment target. These definitions and exemptions are retired.

The current [runtime scope](../../product/ACTIVE_RUNTIME_SCOPE.md) describes explicit
external accounts. Startup mode must be both registered and declared. Existing
historical records retain their scope, hashes and economics; they do not become
external-account qualification. The automatic-update gate retains only its
existing Bitget Demo target; retirement does not authorize a new deployment route.

The stability report also counted `paper*` proposals without `strategy_id` as
strategy drift and deducted up to 30 points. That metric belongs to the retired
strategy-to-simulator loop and has been removed from the current report and
score. Current Operator proposals leave this historical binding empty; archived
proposals remain readable without being graded by that retired requirement.
The existing causal-chain and agent-run failure checks remain in place.

---

## Former execution and market-data alignment

The following definition is archived from `exchange_source_alignment.md` at
commit `9d80afbf8`. Local-account alignment and unknown-mode fallback no longer
exist. Current alignment uses registered external modes and explicit unavailable
states; the historical text does not grant routing authority.

# Execution and Market-Data Exchange Alignment

## Problem

An external demo/live account can execute orders on one exchange while analysis,
funding, OI, orderbook, and Universe selection use another. That split can be
intentional, but it changes prices, contract availability, funding, depth, and the
meaning of every opportunity. A silent split is therefore a semantic-honesty bug.

`exchange.name` is only a configured preference and can be stale. The actual order
target is derived from `exchange.paper_execution_mode`. All alignment checks now use
`aletheia/config/exchange_alignment.py`:

| Paper execution mode | Actual execution exchange |
|---|---|
| `paper_internal` | none (local simulator) |
| `bitget_demo` | Bitget |
| `binance_testnet` / `binance_live` | Binance |
| `okx_demo` / `okx_live` | OKX |

Unknown future external modes fall back to `exchange.name` until explicitly added.

## State model

- `local_paper`: no external execution exchange. The normalized
  `market_data.ccxt_exchange` is both slow and fast public market-data authority
  without a divergence warning. Fast Monitoring supports Bitget and Binance;
  any other venue remains visible but fails closed without substitution.
- `aligned`: execution and `market_data.ccxt_exchange` are the same exchange.
- `diverged`: external execution and market data use different exchanges.

`GET /api/status`, doctor, Overview, and Settings read this same model. Overview's
connection probe targets `execution_exchange`, not the retained preference name.

## Mutation guard

Both ways of creating a split are guarded:

1. Changing `market_data.ccxt_exchange` while an external execution mode is active.
2. Changing `paper_execution_mode` from local/aligned to an exchange different from
   the current market-data source.

The backend returns HTTP 409 with code
`exchange_source_divergence_confirmation_required` before saving. The request must
be repeated with `confirm_exchange_divergence=true`. Settings presents an explicit
checkbox naming both venues and disables Save until checked. Existing intentional
divergence does not block unrelated market-data edits; confirmation is required
when the primary data exchange or execution target actually changes.

Market-data updates are normalized and validated in a temporary map before the
confirmation check or any config object mutation. A 400 validation error or 409
confirmation conflict therefore leaves both the cached object and config file
unchanged; there is no partial in-memory save.

The unified Primary Exchange control remains the normal path and atomically aligns
data, execution, and risk allowlist.

## Visibility

When diverged, Overview shows `X exec · Y data` with a yellow status light and a
persistent explanation. Settings shows the same split at the top of Execution.
Doctor reports the actual execution venue and data venue. Local paper continues to
show `Local paper · no exchange` and does not invent an external connection warning.

## Non-goals

- No automatic config rewrite or forced alignment.
- No credential, live-unlock, risk, order-routing, or broker change.
- No cross-exchange price conversion or fallback.
- Confirmation acknowledges semantic divergence; it does not claim the split is safe.
