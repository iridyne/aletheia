"""Focused regression tests for frozen stock-linked attribution context."""

from __future__ import annotations

import json
from types import SimpleNamespace


def _background(symbol: str, *, stock_linked: bool) -> dict:
    from extensions.market_data import decode_market_background

    base = symbol.split("/", 1)[0]
    info = (
        {"contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY"}
        if stock_linked
        else {"contractType": "PERPETUAL", "underlyingType": "COIN"}
    )
    return decode_market_background(
        symbol=symbol,
        venue="binance",
        market_type="perp",
        market_info={"base": base, "quote": "USDT", "info": info},
    )


def _snapshot(monkeypatch, *, symbol: str, stock_linked: bool):
    from aletheia.data.symbols import instrument_analysis_context
    from aletheia.storage import repositories as repo
    from aletheia.trading import entry_snapshot

    background = _background(symbol, stock_linked=stock_linked)
    metadata = {
        "instrument_background": background,
        "analysis_context": instrument_analysis_context(background),
    }
    monkeypatch.setattr(
        repo,
        "get_opportunity",
        lambda _oid: {"metadata": json.dumps(metadata)},
    )
    monkeypatch.setattr(entry_snapshot, "_btc_price_now", lambda: 60_000.0)
    monkeypatch.setattr(entry_snapshot, "_current_regime", lambda: "bull")
    monkeypatch.setattr(
        "aletheia.data.context_builder.build_market_context_from_db",
        lambda *_args, **_kwargs: SimpleNamespace(
            data_completeness={symbol: "complete"},
            tradability={symbol: {"is_tradable": True}},
            leverage={symbol: {"max": 3}},
        ),
    )
    return entry_snapshot.capture_attribution_snapshot(
        symbol,
        exchange="binance",
        market_type="usdt_futures",
        opportunity_id="opportunity-1",
    )


def test_stock_linked_snapshot_omits_crypto_benchmark_and_regime(monkeypatch):
    from aletheia.portfolio.pnl import _btc_benchmark_applicable

    snapshot = _snapshot(monkeypatch, symbol="SOXL/USDT", stock_linked=True)

    assert snapshot["btc_entry_price"] is None
    assert snapshot["regime_tag"] is None
    context = snapshot["market_context"]
    assert context["analysis_context"]["kind"] == "stock_linked_perp"
    assert context["instrument_background"]["reference_asset"] == "SOXL"

    assert _btc_benchmark_applicable({"entry_market_summary": json.dumps(context)}) is False


def test_crypto_snapshot_keeps_btc_benchmark_and_regime(monkeypatch):
    snapshot = _snapshot(monkeypatch, symbol="BTC/USDT", stock_linked=False)

    assert snapshot["btc_entry_price"] == 60_000.0
    assert snapshot["regime_tag"] == "bull"
    assert snapshot["market_context"]["analysis_context"]["kind"] == "crypto"
