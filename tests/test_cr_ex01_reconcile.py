"""Tests for CR-EX-01: OKX/Binance live-order reconciliation via CCXT get_open_orders.

Docs/CCXT-implemented; the exchange call is mocked here (no keys/network). The real
response shape is pending one demo sampling (see PARTIAL_REDUCE_RESEARCH.md).
"""

import asyncio

import aletheia.execution.reconciliation as recon


class _FakeExchange:
    def __init__(self, orders):
        self._orders = orders

    async def fetch_open_orders(self):
        return self._orders


class _FakeClient:
    def __init__(self, open_ids=None, raise_exc=None):
        self._open_ids = open_ids or []
        self._raise = raise_exc

    async def get_open_orders(self):
        if self._raise:
            raise self._raise
        return [{"id": i} for i in self._open_ids]


# ── get_open_orders response parsing (real client, mocked ccxt exchange) ─────

def test_okx_get_open_orders_parses(monkeypatch):
    import aletheia.exchanges.okx.client as okxmod
    monkeypatch.setattr(okxmod, "get_secret", lambda k: "x")
    c = okxmod.OKXClient(mode="demo")
    monkeypatch.setattr(c, "_ensure_exchange", lambda: _FakeExchange([
        {"id": 123, "symbol": "BTC/USDT:USDT", "status": "open"},
        {"id": None},          # dropped — no id
        {"symbol": "no-id"},   # dropped — no id
    ]))
    assert asyncio.run(c.get_open_orders()) == [
        {"id": "123", "symbol": "BTC/USDT:USDT", "status": "open"}
    ]


def test_binance_get_open_orders_parses(monkeypatch):
    import aletheia.exchanges.binance.client as binmod
    monkeypatch.setattr(binmod, "get_secret", lambda k: "x")
    c = binmod.BinanceClient(mode="testnet")
    monkeypatch.setattr(c, "_ensure_exchange", lambda: _FakeExchange([
        {"id": "abc", "symbol": "ETH/USDT:USDT", "status": "open"},
    ]))
    assert asyncio.run(c.get_open_orders()) == [
        {"id": "abc", "symbol": "ETH/USDT:USDT", "status": "open"}
    ]


# ── _reconcile_ccxt_broker wiring ────────────────────────────────────────────

def _insert_order(db, oid, mode, *, status="submitted", ex_id="ex-1"):
    db.execute(
        "INSERT INTO orders (id,symbol,market_type,direction,order_type,quantity,"
        "execution_mode,status,exchange_order_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (oid, "BTC/USDT", "usdt_futures", "long", "limit", 0.1, mode, status, ex_id),
    )


def test_reconcile_marks_missing_as_filled(monkeypatch):
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import list_orders, _select_one

    db = get_db()
    _insert_order(db, "o1", "okx_live_limited", ex_id="still-open")
    _insert_order(db, "o2", "okx_live_limited", ex_id="gone")
    # exchange still reports only "still-open" → o2 (gone) converges to filled
    monkeypatch.setattr(recon, "_ccxt_client_for_mode", lambda mode: _FakeClient(open_ids=["still-open"]))

    res = recon._reconcile_ccxt_broker("okx_live_limited", list_orders(limit=500))
    assert res["checked"] == 2 and res["filled"] == 1 and res["unreconciled"] == []
    assert _select_one("orders", "o2")["status"] == "filled"
    assert _select_one("orders", "o1")["status"] == "submitted"


def test_reconcile_fetch_failure_infers_nothing(monkeypatch):
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import list_orders, _select_one

    db = get_db()
    _insert_order(db, "o3", "binance_live_limited", ex_id="whatever")
    monkeypatch.setattr(recon, "_ccxt_client_for_mode",
                        lambda mode: _FakeClient(raise_exc=RuntimeError("network down")))

    res = recon._reconcile_ccxt_broker("binance_live_limited", list_orders(limit=500))
    assert res["filled"] == 0
    assert len(res["unreconciled"]) == 1 and res["errors"]
    # fail-safe: a failed read never fabricates a fill
    assert _select_one("orders", "o3")["status"] == "submitted"


def test_reconcile_no_live_orders_is_noop(monkeypatch):
    from aletheia.storage.repositories import list_orders
    called = []
    monkeypatch.setattr(recon, "_ccxt_client_for_mode",
                        lambda mode: called.append(mode) or _FakeClient())
    res = recon._reconcile_ccxt_broker("okx_live_limited", list_orders(limit=500))
    assert res == {"checked": 0, "filled": 0, "errors": [], "unreconciled": []}
    assert called == []  # no client built when there's nothing to reconcile
