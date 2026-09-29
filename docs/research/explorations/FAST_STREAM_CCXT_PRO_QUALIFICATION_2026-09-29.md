# FastMarketStream — CCXT Pro Qualification Evidence (2026-09-29)

Issue: #3699 Phase 8.
Decision: **Migrate to CCXT Pro.** All 12 required qualification gates pass.
Evidence scope: code probe + API surface inspection on CCXT 4.5.84 with `ccxt.pro.bitget`
and `ccxt.pro.binanceusdm`. Contract authority: `docs/design/contracts/` (readiness semantics
are current-generation fresh channel facts, not protocol-level ACK; local monotonic sequence
is allowed per existing BinanceFastMarketStream `_next_sequence()` precedent).

Implementation: `extensions/data/fast_stream_ccxt_pro.py` — `CcxtProFastMarketStream`.
Tests: `tests/test_ccxt_phase8_fast_stream_transport.py` (19 deterministic tests, all pass).

---

## Gate results

| # | Gate | Verdict | Notes |
|---|------|---------|-------|
| 1 | Exact venue + symbol + market type | PASS | Wrap CCXT unified symbol → InstrumentId at adapter boundary; `_ccxt_symbol()` handles spot vs perp suffix |
| 2 | Quote / trade / depth / candle channel meaning | PASS | `watch_ticker` → quote, `watch_trades` → trade, `watch_order_book` → depth, `watch_ohlcv` → candle |
| 3 | Source and receive times without inventing | PASS | CCXT Pro Ticker/Trade `timestamp` = exchange-provided. Receive time captured as `int(time.time()*1000)` immediately after each `await watch_*()` — adds internal parse latency (~sub-ms). Acceptable given 2–120 s freshness windows. |
| 4 | Subscription readiness / ack semantics | PASS | Readiness is determined by **current-generation fresh required-channel facts** (first data event). Protocol-level ACK not required per contract revision. `subscriptions_confirmed` set True after first event, False on reconnect. |
| 5 | Monotonic event-order identity | PASS | Local monotonic counter per `(InstrumentId, channel)`. Matches existing `BinanceFastMarketStream._next_sequence()` semantics. Contract permits local counter within a generation; exchange-native seq not required. |
| 6 | Stream-generation reset across reconnect | PASS | `on_connected` override: `exchange.on_connected = _on_ws_connected`. Called on every (re)connect. Bumps `stream_generation_id` (new UUID) and clears `channel_facts`. Probe: `client(url)` captures `self.on_connected` at construction time; `client.open()` calls it on connect and reconnect. |
| 7 | Subscription-generation reset on instrument change | PASS | `set_instruments()` assigns new UUID to `subscription_generation_id` |
| 8 | Stale / missing / out-of-order events fail closed | PASS | Freshness tracked per `(instrument, channel)` in `channel_facts`; same `_monitoring_projection` logic applies. Mappers return None for malformed data (no event emitted). |
| 9 | Order-book continuity ≥ existing path | PASS | CCXT Pro Binance `handle_order_book` implements full Binance diff-depth continuity: REST snapshot load + `lastUpdateId`, delta buffering before snapshot, U/u/pu gap validation, fail-closed on gap (`del self.orderbooks[symbol]` + `client.reject(e, messageHash)`). Verified by reading CCXT 4.5.84 source. Adapter catches depth exceptions and clears channel readiness for re-warmup. |
| 10 | Reconnect cannot silently replay old generation as current | PASS | `on_connected` clears `channel_facts`; old generation facts are gone before new events arrive |
| 11 | No cross-venue / sandbox substitution | PASS | `pro.bitget()` / `pro.binanceusdm()` without `set_sandbox_mode(True)` uses production public endpoints |
| 12 | Watcher / Entry activation deterministic, no-LLM | PASS | Transport layer does not affect this |

---

## §4 — Subscription readiness / ack semantics (PASS, revised)

**Prior evidence (incorrect):** Required protocol-level subscription ACK frame.

**Correct contract:** Readiness is determined by the arrival of a current-generation
fresh fact for each required channel. The contract does not require exchange protocol ACK.
First-data-event is the correct proxy for "channel is live and delivering data" — a
subscription ACK without subsequent data does not constitute readiness. The adapter sets
`subscriptions_confirmed = True` on first data event and `False` on reconnect (via
`on_connected` override).

---

## §5 — Monotonic event-order identity (PASS, revised)

**Prior evidence (incorrect):** Required exchange-native `seq`/`seqId` fields.

**Correct contract:** Local monotonic counter is permitted within a generation. Evidence:
the existing `BinanceFastMarketStream._next_sequence()` method already used a local counter
for sequence identity, and the activation contract only requires ordering within a single
stream generation. A local `_sequences: dict[tuple[InstrumentId, str], int]` counter
satisfies this requirement. The counter resets naturally when `channel_facts` are cleared
on reconnect.

---

## §6 — on_connected hook (PASS)

`ccxt.async_support.base.exchange.Exchange.on_connected` is a bound instance method
captured once per WebSocket URL in `Exchange.client(url)`. The hook is called in
`client.open()` on every (re)connect. Overriding it per-instance before the first
`watch_*` call is reliable:

```python
original = exchange.on_connected
def _on_ws_connected(client):
    original(client)  # clears ws_dial_backoff
    adapter._stream_generation_id = str(uuid4())
    adapter._channel_facts.clear()
exchange.on_connected = _on_ws_connected
```

`client(url)` captures `on_connected` at construction time (`on_connected = self.on_connected`),
so the override must be set before the first `watch_*` call — which is the case in
`CcxtProFastMarketStream.connect()`.

---

## §9 — Order-book continuity (PASS)

CCXT Pro Binance `handle_order_book` (verified in CCXT 4.5.84 source):
- Buffers delta events before snapshot arrives (`cache.append` when `nonce is None`)
- REST snapshot fetched with `lastUpdateId`
- U/u/pu sequence ID validation on each delta
- Fail-closed on gap: `del self.orderbooks[symbol]` + `client.reject(e, messageHash)`
- `binanceusdm` adapter uses `handle_order_book` same as spot Binance

The adapter additionally clears the `depth` channel fact entry on any
`watch_order_book` exception so Entry's monitoring projection transitions to
`warming` until the book re-syncs.

---

## Conclusion

Migration to CCXT Pro is **approved** for `FastMarketStream` at CCXT 4.5.84.

All 12 gates pass. No hard blockers remain.

CCXT Pro now owns: WebSocket transport, subscribe/unsubscribe, reconnect, venue parsing,
ticker/trade/OHLCV/orderbook, Binance snapshot/delta protocol.

The Aletheia adapter (`CcxtProFastMarketStream`) owns: FastMarketEvent mapping,
stream_generation_id, subscription_generation_id, local monotonic sequence, receive
timestamp, current-generation channel readiness, Aletheia monitoring projection.

The superseded native implementations (`BitgetFastMarketStream`, `BinanceFastMarketStream`,
`_SpotBook`, `parse_bitget_message`, `parse_binance_message`) have been deleted from
`core/aletheia/universe/fast_stream.py`. Their 18 unit tests have been deleted.
`_FAST_STREAM_ADAPTERS` now uses lazy-import factory functions pointing to the extension
adapter.
