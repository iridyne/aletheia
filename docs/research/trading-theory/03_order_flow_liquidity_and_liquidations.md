# Order Flow, Liquidity, and Liquidations

Date: 2026-08-09
Role: Research evidence

## Research question

Which microstructure facts add information beyond OHLCV, and what prevents them
from being treated as complete market truth?

## Order flow

Trade flow needs time-sequenced executions with an aggressor classification.
Candidate features include:

- buyer-initiated minus seller-initiated volume;
- taker buy ratio and signed notional;
- rolling cumulative volume delta (CVD);
- burst intensity and trade-size distribution;
- divergence between signed flow and price change.

The existing taker ratio is a narrow aggregate, not a substitute for a durable
trade stream. CVD cannot be reconstructed reliably from OHLCV alone.

## Order Flow Imbalance (OFI)

Cont, Kukanov, and Stoikov define OFI from changes in supply and demand at the
best bid and ask. Their NYSE study found short-interval price changes closely
related to OFI, with impact inversely related to market depth:
[Journal of Financial Econometrics](https://academic.oup.com/jfec/article-abstract/12/1/47/816163).

This supports OFI as a measurable feature, not as guaranteed Alpha in crypto.
The original result must be revalidated for each venue, market type, latency,
sampling interval, and execution model.

Minimum inputs are sequenced L2 snapshots or deltas with:

- venue sequence identifiers;
- exchange and local timestamps;
- bid/ask price and size updates;
- snapshot/delta recovery rules;
- gap detection and resynchronization.

## Liquidity and depth

Useful point-in-time facts include:

- spread in ticks and basis points;
- depth within fixed basis-point bands;
- bid/ask depth imbalance;
- estimated market-order slippage for fixed notionals;
- book slope or concentration;
- cancellation and replenishment intensity when event data permit.

Displayed liquidity can be cancelled and is not guaranteed executable. A model
must not call an order-book wall `support` without qualification. Executability
also depends on queue position, latency, and the system's own order size.

## Liquidations

A liquidation event is observed forced execution reported by a venue. Useful
features include:

- signed liquidation notional;
- long versus short imbalance;
- burst count and notional over fixed windows;
- distance between bankruptcy/liquidation price and current mark;
- cross-venue agreement and coverage ratio.

Venue feeds have material limitations. For example, Bitget's public liquidation
channel pushes once per second and retains at most the largest long and short
liquidation record per pair for that interval:
[Bitget API](https://www.bitget.com/api-doc/uta/websocket/public/Liquidation-Channel).
Bybit documents a 500ms all-liquidation stream with side, size, and bankruptcy
price for its covered contracts:
[Bybit API](https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation).

These streams describe their venues, not the entire market. Aggregation,
omission, latency, side conventions, and contract units must be captured in
provenance.

## Liquidation maps versus liquidation events

An observed liquidation event says forced flow occurred. A predicted
liquidation map estimates where current positions might be forced to close.
They require different data:

- events: venue liquidation feed;
- map: position side, size, leverage, entry, margin mode, maintenance margin,
  and liquidation formula or a trusted provider estimate.

Open interest plus price does not reveal the distribution of entry prices and
leverage. A heatmap built without those inputs is a model estimate and must be
labelled as such.

## Time alignment and failure handling

Every microstructure snapshot should include:

- `event_time`, `received_at`, and maximum observed delay;
- exact venue, symbol, market type, and contract unit;
- source sequence/gap status;
- aggregation window;
- completeness and coverage flags;
- source version or endpoint contract.

Missing or gapped input must produce `unavailable` or `degraded`, never a zero
that looks like balanced flow.

## Research disposition

| Feature | Required source | Initial authority |
|---|---|---|
| Taker ratio | venue aggregate endpoint | Existing narrow context |
| Signed trade flow / CVD | public trades | Observation-only candidate |
| Best-level OFI | sequenced L2 events | Observation-only candidate |
| Depth/slippage estimate | fresh L2 book | Observation and execution-cost context |
| Liquidation bursts | venue liquidation events | Observation/risk context |
| Global liquidation map | cross-venue positions or model | Unavailable as market truth |

No microstructure feature bypasses Risk approval or grants execution authority.
