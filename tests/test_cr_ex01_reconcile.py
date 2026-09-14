"""Tests for CR-EX-01: OKX/Binance live-order reconciliation via CCXT get_open_orders.

Docs/CCXT-implemented; the exchange call is mocked here (no keys/network). The real
response shape is pending one demo sampling (see PARTIAL_REDUCE_RESEARCH.md).
"""

import asyncio



class _FakeExchange:
    def __init__(self, orders):
        self._orders = orders

    async def fetch_open_orders(self):
        return self._orders




# ── get_open_orders response parsing (real client, mocked ccxt exchange) ─────

def test_okx_get_open_orders_parses(monkeypatch):
    import extensions.exchanges.okx.client as okxmod
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
    import extensions.exchanges.binance.client as binmod
    monkeypatch.setattr(binmod, "get_secret", lambda k: "x")
    c = binmod.BinanceClient(mode="testnet")
    monkeypatch.setattr(c, "_ensure_exchange", lambda: _FakeExchange([
        {"id": "abc", "symbol": "ETH/USDT:USDT", "status": "open"},
    ]))
    assert asyncio.run(c.get_open_orders()) == [
        {"id": "abc", "symbol": "ETH/USDT:USDT", "status": "open"}
    ]
