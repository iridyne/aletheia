# OKX Order-book Continuity Audit (#1002)

**Audit date:** 2026-08-17
**Repository:** `iridite/aletheia`
**Audited revision:** `d8c202bd`
**Role:** Evidence / Archive

## Conclusion

Aletheia has no affected OKX order-book WebSocket path. The active path is a
stateless REST snapshot poll through CCXT: one `fetch_order_book` response is
parsed and stored as one `OrderbookSnapshot`. No code reads `checksum`, applies
incremental updates, maintains a local book, or reconnects/resubscribes to an
OKX order-book channel. Therefore there is no checksum decision to migrate and
no `seqId`/`prevSeqId` continuity implementation to repair in this revision.

This is an evidence-only closure for #1002. It deliberately does not add a
second order-book implementation or enable a new exchange data path.

## External contract checked

OKX's [2026-06-23 change log](https://www.okx.com/docs-v5/log_en/#2026-06-23)
states that `checksum` remains present but is fixed to `0` and must not be used
for integrity verification on the `books`, `books-l2-tbt`, and
`books50-l2-tbt` WebSocket channels. Those channels use `seqId`/`prevSeqId`
for continuity. `books5` and `bbo-tbt` do not carry a checksum and are not
affected.

That contract applies to a streaming snapshot/incremental consumer. It does
not turn a standalone REST snapshot into an incremental stream.

## Repository-wide inventory

| Path / symbol | Observed behavior | Continuity impact |
|---|---|---|
| `aletheia/data/collectors/microstructure.py::MicrostructureCollector.collect` | Gets a fresh provider, calls `load_markets()`, then `exchange.fetch_order_book(fetch_sym, limit=20)` once per instrument and passes the response to `_parse_orderbook`. | REST point-in-time snapshot only; no local state, `checksum`, `seqId`, `prevSeqId`, delta application, or reconnect path. |
| `aletheia/net.py::make_ccxt` | Constructs `ccxt.async_support` with the managed `aiohttp` session. | REST CCXT client; no `ccxt.pro` or `watch_order_book` construction. |
| `aletheia/agent_runtime/scheduler.py::_async_data_collectors` | Runs microstructure under the bounded collector deadline and persists parsed snapshots in a batch. | A failed/empty poll is surfaced through collector health; it cannot mutate an old in-memory book. |
| `aletheia/storage/repositories.py::insert_orderbook_snapshot` / `latest_orderbook` | Stores and reads rows keyed by exchange, symbol, market type, and observation time. | Storage is snapshot history; it never merges order-book deltas. |
| `aletheia/agents/market_observer.py`, `aletheia/universe/features.py`, `aletheia/data/context_builder.py` | Read the exact instrument's latest row and apply the existing freshness policy. | Consumers use a bounded snapshot or no fresh microstructure evidence; none consumes a sequence. |
| `aletheia/cli/inspect_cmd.py` and `aletheia/diagnostics/doctor.py` | Report the latest snapshot and its freshness/identity. | Observability only; no integrity decision based on checksum or sequence fields. |
| `tests/test_microstructure_collector.py`, `tests/test_data_collectors.py`, `tests/specs/spec_data_utilization.py` | Cover CCXT level-shape parsing, contract size, instrument identity, and persistence/consumer wiring. | No sequence fixtures are applicable because this repository has no streaming owner. |

The source/test/frontend audit found no affected-path references to
`checksum`, `seqId`, `prevSeqId`, `watch_order_book`, `watchOrderBook`, or
`ccxt.pro`. Generic archive/migration checksum code and the application's own
frontend WebSocket transport are unrelated to OKX order-book sequencing.

The dependency declaration is `ccxt>=4.5.56` in `pyproject.toml`; the checked
lock resolves it to `4.5.56`. That is reproducibility for the REST client, not a
claim that CCXT owns or proves a WebSocket continuity contract here. No
dependency upgrade or pin change is needed for the active path.

## Required-case disposition

| Case from #1002 | Disposition in the current repository |
|---|---|
| REST snapshot | Supported as an independent observation; parsed only when both sides are present and positive. |
| `checksum == 0` | Not read or used. |
| Valid incremental update | Not consumed; no affected path. |
| Gap, duplicate, or out-of-order update | Not consumed; no local book can be left partially updated. |
| Reconnect/resubscribe | No order-book WebSocket connection exists; each poll starts from a new REST response. |
| Bounded resync/backoff | Not applicable to the current REST-only path; poll failure is bounded by the collector deadline and health state. |
| `books5` / `bbo-tbt` | No channel subscriptions exist, so no checksum contract is incorrectly applied. |

If a future change introduces an OKX WebSocket order-book consumer, it must
become the sole owner of a local book and must add deterministic fixtures for a
snapshot, contiguous updates, zero checksum, gap, duplicate, out-of-order
delivery, reconnect, and bounded retry. It must reject the local book and
resnapshot on a sequence failure; it must never fall back to accepting a
checksum or an unverified delta stream.

## Evidence commands

The audit was performed against the checked-out revision with repository-wide
searches for OKX/order-book, checksum, sequence, and WebSocket ownership, plus
source inspection of the collector, CCXT factory, scheduler, storage, and
consumers listed above. The focused source search was:

```sh
rg -n -i --glob '*.py' --glob '*.ts' --glob '*.tsx' --glob '*.js' \
  '(checksum|seqid|prevseqid|watch_order_book|watchOrderBook|ccxt\.pro|fetch_order_book)' \
  aletheia tests frontend .github
```

The only affected-path hit is the REST `fetch_order_book` call listed above;
the remaining checksum hits are generic archive/migration integrity code.
No external network or live credentials were used.
