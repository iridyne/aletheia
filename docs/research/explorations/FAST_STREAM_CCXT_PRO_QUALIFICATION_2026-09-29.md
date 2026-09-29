# FastMarketStream — CCXT Pro Qualification Evidence (2026-09-29)

Issue: #3699 Phase 8.
Decision: **Retain native stream.** CCXT Pro fails 3 of 12 required qualification gates.
Evidence scope: code probe + API surface inspection on CCXT 4.5.84 with `ccxt.pro.bitget`
and `ccxt.pro.binance`. No live stream probe required — API-surface failures are structural.

---

## Gate results

| # | Gate | Verdict | Notes |
|---|------|---------|-------|
| 1 | Exact venue + symbol + market type | PASS | Wrap CCXT unified symbol → InstrumentId at adapter boundary |
| 2 | Quote / trade / depth / candle channel meaning | PASS | `watch_ticker` → quote, `watch_trades` → trade, `watch_order_book` → depth, `watch_ohlcv` → candle |
| 3 | Source and receive times without inventing | PASS (conditional) | CCXT Pro Ticker/Trade `timestamp` = exchange-provided. Receive time captured as `int(time.time()*1000)` immediately after each `await watch_*()` — adds internal parse latency (~sub-ms). Acceptable given 2–120 s freshness windows. |
| **4** | **Subscription readiness / ack semantics** | **FAIL** | See §4 below |
| **5** | **Monotonic event-order identity** | **FAIL** | See §5 below |
| 6 | Stream-generation reset across reconnect | UNCERTAIN | `on_connected(client, message)` hook exists and is overridable; could bump generation ID. Not blocked on its own, but Gate 4 + 5 supersede. |
| 7 | Subscription-generation reset on instrument change | PASS | Managed in wrapper layer when calling `watch_*` per symbol |
| 8 | Stale / missing / out-of-order events fail closed | PASS (conditional) | Freshness is tracked by wrapper per `(instrument, channel)` last-received map; same `_monitoring_projection` logic applies |
| **9** | **Order-book continuity ≥ existing path** | **UNCERTAIN** | See §9 below — not a hard blocker by itself, but cannot be proved equivalent without live probing |
| 10 | Reconnect cannot silently replay old generation as current | UNCERTAIN | Contingent on Gate 6 hook reliability; superseded by Gate 4 |
| 11 | No cross-venue / sandbox substitution | PASS | `pro.bitget()` / `pro.binance()` without `set_sandbox_mode(True)` uses production public endpoints |
| 12 | Watcher / Entry activation deterministic, no-LLM | PASS | Transport layer does not affect this |

---

## §4 — Subscription readiness / ack semantics (FAIL)

**Required behavior:**
`FastStreamHealth.subscriptions_confirmed` is `True` only after the exchange has
acknowledged every requested subscription channel. The native implementation
processes explicit ACK frames:
- Bitget: `{"event": "subscribe", "arg": {...}}`
- Binance: `{"result": null, "id": N}` response to a SUBSCRIBE request

**What CCXT Pro provides:**
`ccxt.pro.bitget.handle_subscription_status` is a no-op returning the raw message.
Subscription confirmations are consumed internally to resolve coroutine futures, but
there is no public API to query per-channel subscription state after the fact.
`bg.subscriptions` attribute does not exist.

**Why this is a hard blocker:**
The Phase 8 spec explicitly states:
> "arrival of the first data event must not be silently redefined as a subscription ACK
> unless current contract/evidence proves equivalence"

Using first-data-event as a proxy for subscription confirmation is prohibited.
No other public CCXT Pro surface provides subscription ACK state.

Probe evidence:
```
$ uv run python3 -c "
import ccxt.pro as pro
bg = pro.bitget()
print(hasattr(bg, 'subscriptions'))   # False
print(bg.handle_subscription_status.__doc__)  # None (no-op)
"
False
None
```

---

## §5 — Monotonic event-order identity (FAIL)

**Required behavior:**
`FastMarketEvent.sequence` is stored in activation snapshots (`quote_sequence`,
`depth_sequence`) and validated at order execution time. The value must be
monotonically increasing within a stream generation and must not be fabricated
from non-exchange data.

The native Bitget adapter uses exchange-provided `seq` / `seqId` fields.
The native Binance adapter uses a local monotonic counter per `(instrument, event_type)`
key — but this is known to be within a single connected stream generation, and the
activation contract only requires ordering within a generation.

**What CCXT Pro provides:**
CCXT Pro's normalized `Ticker` dict does not expose raw exchange sequence numbers.
`watchTrades` returns Trade objects with an `id` field (exchange trade ID, a string),
which is not a monotonic counter across all event types. `watchOrderBook` returns an
OrderBook with `nonce` (if available) but this is venue-specific and not guaranteed.

The Phase 8 spec states:
> "if only a local monotonic counter is available, first prove the current Contract
> permits that meaning; otherwise this is not equivalent"

A local counter is NOT equivalent to the exchange-provided `seq` that the native
Bitget path uses, and the Contract has not been updated to explicitly permit it.
This makes CCXT Pro's sequence identity semantically incompatible.

---

## §9 — Order-book continuity (UNCERTAIN / cannot prove ≥ existing)

The existing `BinanceFastMarketStream._SpotBook` implements the full Binance
diff-depth continuity protocol:
- REST snapshot load with `lastUpdateId`
- Event buffering until snapshot arrives
- Sequence-ID gap detection via `U` (first) and `u` (last) fields
- Fail-closed reset on any continuity gap
- Synced-flag gating (no events emitted until synced)

`ccxt.pro.binance` has its own internal order book management via `handle_order_book`.
It also implements snapshot + diff, but:
- the internal continuity state (`synced` equivalent) is not publicly exposed
- whether it handles sequence gaps with the same fail-closed policy is unverifiable
  without deep CCXT Pro source inspection AND live probing under gap injection

Since equivalence cannot be proved without live probing, and Gate 4 is already a
hard blocker, this gate is recorded as UNCERTAIN rather than FAIL.

---

## Conclusion

Migration to CCXT Pro is **not approved** for `FastMarketStream` at CCXT 4.5.84.

Hard blockers: Gates 4 and 5.
Soft uncertainty: Gate 9.

The existing native WebSocket implementations in `core/aletheia/universe/fast_stream.py`
remain the single current owner of Fast Monitoring transport.

No second CCXT-Pro path is introduced. This file is the qualification evidence record.

---

## Future re-qualification conditions

Re-evaluate if CCXT Pro adds a public subscription confirmation API (e.g., a per-channel
`subscribed` state or an async ACK callback), AND if the Contract is explicitly updated
to permit a local monotonic counter in place of exchange-provided sequence IDs.
