# Hyperliquid Public-Data Path

Date: 2026-08-09
Role: Research evidence

## Decision

Hyperliquid is a practical zero-credential research source for public trades,
L2 snapshots, candles, funding, and open interest. It should be treated as a
cross-venue context source, not as a new Aletheia execution venue and never as a
substitute for Bitget execution marks or account truth.

This report does not authorize a new collector. Under the current experiment
protocol, a new production data source is separately scoped and deferred while
qualification is unfinished.

## Current repository coverage

`aletheia/data/collectors/hyperliquid.py` already calls the public Info endpoint
without a key:

- `metaAndAssetCtxs` supplies per-asset mark, current funding, and open
  interest;
- `predictedFundings` supplies cross-venue predicted funding observations.

The collector persists to the existing funding-rate and open-interest tables
with Hyperliquid provenance. OI is converted from base units to USD using the
same response's mark price, and the 1h change uses only a same-source bounded
baseline. It currently collects no Hyperliquid trades, L2, candles, or global
liquidation events.

## Public capability

Hyperliquid's official documentation exposes:

| Feed | Public path | Relevant limit or caveat |
| --- | --- | --- |
| L2 book | Info `l2Book` and WebSocket `l2Book` | Info returns at most 20 levels per side. |
| Trades | WebSocket `trades`; bounded `recentTrades` Info request | Real-time stream is public; do not assume deep paginated history from `recentTrades`. |
| Candles | Info `candleSnapshot` and WebSocket `candle` | Info documents only the most recent 5,000 candles; enforce an application bound. |
| Funding/OI | Info market/asset contexts and funding history | Already partially consumed by Aletheia. |
| Historical L2/trades | Public S3 archives | Archive freshness and completeness are not guaranteed; requester may pay transfer costs. |
| User fills/liquidations | User-specific subscriptions/queries | Requires a user address and is not a public global liquidation feed. |

Official references:

- [Info endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
- [WebSocket subscriptions](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions)
- [WebSocket connection and recovery](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket)
- [Rate limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)
- [Historical data](https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data)

A bounded read-only probe on 2026-08-09 successfully returned asset contexts, a
BTC L2 book, and BTC 1h candles. The candle response slightly exceeded the
documented headline count in that probe, which reinforces the need to apply
local time and count bounds rather than trust response size implicitly.

## Minimum useful data path

If separately approved, start with one read-only adapter and one or two liquid
perpetuals:

1. bootstrap a book snapshot and subscribe to public `l2Book` plus `trades`;
2. assign exact Hyperliquid instrument identity and preserve event timestamps;
3. deduplicate trades using the documented stable tuple rather than arrival
   order alone;
4. reconnect, resubscribe, and repair the gap with the corresponding Info
   request where available;
5. record continuity gaps explicitly and invalidate affected derived features;
6. keep raw observations separate from derived OFI, volume profile, and
   liquidity features;
7. cap symbols, retention, and WebSocket connections below published limits.

The adapter should fail closed on malformed numbers, time regression,
instrument ambiguity, missed recovery, and inconsistent snapshot identity.

## Candidate observations

The first useful facts are direct and compact:

- trade count, notional, and aggressor-side imbalance over frozen windows;
- observed session Volume Profile and POC from trade prices and sizes;
- best-level and multi-level book imbalance;
- spread, depth, depth slope, and short-horizon OFI;
- divergence between Hyperliquid funding/OI/flow and Aletheia's primary venue.

Cross-venue disagreement is context, not an execution override. Every fact must
name the venue, coverage window, source latency, and gap state.

## Liquidation limitation

The official public subscriptions list trades, candles, and books, but the
documented liquidation object appears under user-specific events. That does not
provide a complete public market liquidation stream. Aletheia must not infer a
global liquidation map from a single user's events or rename large trades as
liquidations.

Current liquidation collection from other providers remains separate and
venue-specific. A future Hyperliquid liquidation feature requires either a
documented global source or an explicitly labeled partial observation.

## Governance before implementation

Before adding the feed to production:

- register the new source and its authority/fallback semantics;
- add the corresponding data-utilization claim before the collector/table;
- define retention, recovery, and deletion behavior;
- keep credentials absent and do not introduce exchange-order methods;
- verify that adding cross-venue facts does not silently change current
  decision behavior;
- run offline coverage/replay first, then request a separate experiment if a
  derived fact is proposed for the model context.

## Recommendation

Hyperliquid trades are the best first research input for a true Volume Profile
because they resolve the price-allocation ambiguity in candle-only POC without a
paid API. L2 should follow only when trade continuity is stable. Global
liquidations should not be promised from Hyperliquid until a documented public
source exists.
