# Execution and Market-Data Exchange Alignment

## Problem

An external demo/live account can execute orders on one exchange while analysis,
funding, OI, orderbook, and Universe selection use another. That split can be
intentional, but it changes prices, contract availability, funding, depth, and the
meaning of every opportunity. A silent split is therefore a semantic-honesty bug.

`exchange.name` is only a configured preference and can be stale. The actual order
target is derived from `exchange.paper_execution_mode`. All alignment checks now use
`aletheia/config/exchange_alignment.py`:

| Configured execution mode | Resolved execution exchange |
|---|---|
| `bitget_demo` | Bitget |
| `binance_testnet` | Binance |
| `okx_demo` | OKX |
| Registered `*_live_limited` mode | The retained `exchange.name` value; Live authority remains separately gated |

Missing, retired and unknown modes resolve to no execution exchange. They do not
fall back to `exchange.name` or create a local account. Credential-profile names
`binance_live` and `okx_live` are not execution modes.

## State model

- `not_configured`: no execution mode was selected.
- `unavailable`: a selected mode has no supported execution exchange.
- `aligned`: execution and `market_data.ccxt_exchange` are the same exchange.
- `diverged`: external execution and market data use different exchanges.

`GET /api/status`, doctor, Overview, and Settings read this same model. Overview's
connection probe targets `execution_exchange`, not the retained preference name.

## Mutation guard

Both ways of creating a split are guarded:

1. Changing `market_data.ccxt_exchange` while an external execution mode is active.
2. Changing `paper_execution_mode` from missing/aligned to an exchange different from
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
Doctor reports the resolved execution venue and data venue. Missing or retired
execution modes remain explicitly unconfigured/unavailable. The former local-account
alignment definition is [archived](../history/local_simulated_runtime_scope.md#former-execution-and-market-data-alignment).

## Non-goals

- No automatic config rewrite or forced alignment.
- No credential, live-unlock, risk, order-routing, or broker change.
- No cross-exchange price conversion or fallback.
- Confirmation acknowledges semantic divergence; it does not claim the split is safe.
