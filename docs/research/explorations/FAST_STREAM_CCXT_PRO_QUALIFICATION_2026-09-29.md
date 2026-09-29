# FastMarketStream — CCXT Pro Qualification Evidence (2026-09-29)

Issue: #3699 Phase 8.
Decision: **Migrate to CCXT Pro.** All 12 required qualification gates pass.
Evidence scope: code probe + API surface inspection on CCXT 4.5.84 with `ccxt.pro.bitget`
and `ccxt.pro.binanceusdm`, plus public-WS smoke re-runs on 2026-09-29 (see
[Public-WS smoke](#public-ws-smoke-2026-09-29)). Contract authority: `docs/design/contracts/` (readiness semantics
are current-generation fresh channel facts, not protocol-level ACK; local monotonic sequence
is allowed per existing BinanceFastMarketStream `_next_sequence()` precedent).

Implementation: `extensions/data/fast_stream_ccxt_pro.py` — `CcxtProFastMarketStream`.
Tests: `tests/test_ccxt_phase8_fast_stream_transport.py` (22 deterministic tests, all pass).

---

## Gate results

| # | Gate | Verdict | Notes |
|---|------|---------|-------|
| 1 | Exact venue + symbol + market type | PASS | Wrap CCXT unified symbol → InstrumentId at adapter boundary; `_ccxt_symbol()` handles spot vs perp suffix |
| 2 | Quote / trade / depth / candle channel meaning | PASS, repaired 2026-09-29 | `watch_trades` → trade, `watch_order_book` → depth, `watch_ohlcv` → candle. Quote source is per venue: `watch_ticker` where that stream carries bid/ask (bitget), else `watch_bids_asks` (#3723 — `binanceusdm.watch_ticker` defaults to the bookless `24hrMiniTicker`, so its quote fact comes from the `bookTicker` stream). See [§2](#2-quote-source-per-venue-repaired-2026-09-29). |
| 3 | Source and receive times without inventing | PASS, repaired 2026-09-29 | CCXT Pro Ticker/Trade/OrderBook/OHLCV `timestamp` is exchange-provided when present. The initial implementation fell back to `int(time.time()*1000)` (the receive clock) whenever `timestamp` was `None` — inventing exchange time exactly as this gate forbids. Repaired: a missing/malformed source timestamp now drops the fact (`_source_ms()` returns `None` → mapper returns `None`, same fail-closed path as missing bid/ask). See [§3](#3-source-vs-receive-time-repaired-2026-09-29). Receive time captured as `int(time.time()*1000)` immediately after each `await watch_*()` — adds internal parse latency (~sub-ms). Acceptable given 2–120 s freshness windows. |
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

## §2 — Quote source per venue (repaired 2026-09-29, #3723)

**Prior evidence (incomplete):** "`watch_ticker` → quote" was recorded as PASS from the
API surface alone, without checking what that channel *carries* on each venue.

**Actual initial behaviour:** on `binanceusdm`, `watch_ticker` resolves to the bookless
`24hrMiniTicker` stream (`ccxt/pro/binance.py`: `watch_ticker` → `watch_tickers` →
`handle_option_and_params(params, 'watchTickers', 'name', 'miniTicker')`), so `bid`/`ask`
are always `None` and `_map_ticker`'s bid/ask guard dropped every ticker. The quote
channel on that venue therefore never produced a fact and never became `ready`, which
blocks quote-gated entry activation under `binance_testnet`.

**Repair:** the quote source is chosen per exchange in `_QUOTE_FROM_BIDS_ASKS`
(`_watch_quote()`): `binanceusdm` reads `watch_bids_asks([symbol])` (the `bookTicker`
stream, `bid`/`ask`/`timestamp` all populated), `bitget` keeps `watch_ticker` (its
ticker already carries bid/ask). Whether a ticker stream carries a top-of-book is
venue stream knowledge, not a CCXT capability flag, so it is recorded as a small
explicit venue set with its evidence rather than auto-detected.

**Re-qualification of the affected gates:**

- Gate 2: the `quote` meaning (proven best bid/ask with a source timestamp) is now
  satisfied on both venues; `_map_ticker` and the fail-closed mapper contract are
  unchanged.
- Gate 4 / 6 / 7: unchanged — `watch_bids_asks()` goes through the same
  `watch_multi_ticker_helper` → `client()` machinery, so the per-instance
  `on_connected` override (generation bump + `channel_facts` clear) applies to the
  bookTicker socket exactly as to the ticker socket. Confirmed live: the smoke's
  debug hook logged the generation bump on the `bookTicker` client URL.
- Gate 3: unchanged — `watch_bids_asks` returns a CCXT ticker structure, so the
  `_source_ms()` fail-closed rule applies to it identically.
- New deterministic tests: `test_binanceusdm_quote_comes_from_book_ticker` (fails
  against the pre-repair loop) and `test_bitget_quote_still_comes_from_watch_ticker`.

---

## §3 — Source vs receive time (repaired 2026-09-29)

**Prior evidence (incorrect):** claimed CCXT Pro `timestamp` is always exchange-provided.

**Actual initial implementation:** `_map_ticker` / `_map_trade` / `_map_orderbook` used
`ticker.get("timestamp") or self._ts_now()` — when a venue payload had no `timestamp`,
the adapter stamped `exchange_timestamp_ms` with the local receive clock. That is exactly
the invented-source-time failure this gate exists to catch: a fact with no proven exchange
clock would look maximally fresh to entry activation's `0 <= received - exchange <= freshness`
check, instead of being unusable.

**Repair:** `_source_ms(value)` parses a CCXT timestamp and returns `None` for `None`,
non-numeric, or non-positive values. Each mapper now requires a usable source timestamp
the same way it already required bid/ask or non-empty book levels — missing it drops the
fact (`_drop_without_source_time`, debug-logged) instead of emitting one. No new channel
state or reason vocabulary: an unusable-source-time channel stays `warming`, same as a
channel with no data at all.

Regression test: `tests/test_ccxt_phase8_fast_stream_transport.py::test_mappers_fail_closed_when_source_timestamp_unusable`
(fails against the pre-repair implementation, passes after).

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
ticker/bookTicker/trade/OHLCV/orderbook, Binance snapshot/delta protocol.

The Aletheia adapter (`CcxtProFastMarketStream`) owns: FastMarketEvent mapping,
stream_generation_id, subscription_generation_id, local monotonic sequence, receive
timestamp, current-generation channel readiness, Aletheia monitoring projection.

The superseded native implementations (`BitgetFastMarketStream`, `BinanceFastMarketStream`,
`_SpotBook`, `parse_bitget_message`, `parse_binance_message`) have been deleted from
`core/aletheia/universe/fast_stream.py`. Their 18 unit tests have been deleted.
`_FAST_STREAM_ADAPTERS` now uses lazy-import factory functions pointing to the extension
adapter.

---

## Public-WS smoke (2026-09-29)

Re-run after the §3 repair, against real production public WebSockets (no credentials,
no sandbox). `CcxtProFastMarketStream("bitget")` and `CcxtProFastMarketStream("binanceusdm")`,
instrument `BTC/USDT` perp, 25 s per venue, debug logging on
`aletheia.universe.fast_stream_ccxt_pro` to count `_drop_without_source_time` calls.

| Venue | quote | trade | depth | candle | source-timestamp drops |
|---|---|---|---|---|---|
| bitget | 211 events, ready | 21 events, ready | 198 events, ready | 10 events, ready | 0 |
| binanceusdm | **0 events, warming** | 216 events, ready | 235 events, ready | 41 events, ready | 0 |

**No invented exchange time observed on either venue** — every delivered event carried a
real CCXT `timestamp`, and zero facts were dropped for a missing source timestamp. The
repair is validated against live traffic, not only the deterministic unit tests.

**binanceusdm quote gap (pre-existing, not caused by this repair):** `watch_ticker` on
`binanceusdm` returns a bookless `24hrMiniTicker` (`info.e == "24hrMiniTicker"`,
`bid`/`ask` both `None`) — `_map_ticker`'s existing bid/ask guard has always dropped this.
Direct probe confirms `exchange.watch_bids_asks(["BTC/USDT:USDT"])` delivers a real
bookTicker. That is a channel-source-selection defect (gate 2), not a timestamp-honesty
defect (gate 3), so it was recorded here as a follow-up and then repaired separately in
[§2](#2-quote-source-per-venue-repaired-2026-09-29) / #3723.

### Post-repair re-run (§2 + §3)

After the §2 quote-source repair, the same smoke was re-run on merged master
(`CcxtProFastMarketStream("bitget")` / `("binanceusdm")`, BTC/USDT perp, 25 s each):

| Venue | quote | trade | depth | candle | source-timestamp drops |
|---|---|---|---|---|---|
| bitget | 217 events, ready | 8 events, ready | 163 events, ready | 9 events, ready | 0 |
| binanceusdm | 1109 events, ready | 135 events, ready | 235 events, ready | 50 events, ready | 0 |

Both venues now report every channel `ready`, including `quote` on `binanceusdm`
(~44 quote events/s from the `bookTicker` stream, sane bid<ask spreads), with zero
source-timestamp drops and <200 ms source lags on quote/trade/depth.

Gates 4/6/7 hold for the new source: the smoke's generation-bump hook fired on the
`binanceusdm` quote socket's own client URL (`wss://fstream.binance.com/public/ws/*`,
the `public` WS category that carries `bookTicker`), alongside the `market/ws/*`
client — i.e. the per-instance `on_connected` override covers the bookTicker socket,
not just the ticker socket. `bitget` still uses a single client
(`wss://ws.bitget.com/v2/ws/public`) because its quote comes from `watch_ticker`.
