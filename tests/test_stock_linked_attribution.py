"""Focused regression tests for frozen stock-linked attribution context."""

from __future__ import annotations

import json
from types import SimpleNamespace


def _background(symbol: str, *, stock_linked: bool) -> dict:
    from aletheia.data.symbols import build_instrument_background

    base = symbol.split("/", 1)[0]
    info = (
        {"contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY"}
        if stock_linked
        else {"contractType": "PERPETUAL", "underlyingType": "COIN"}
    )
    return build_instrument_background(
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
    from aletheia.execution.paper_broker import _paper_position_attribution
    from aletheia.portfolio.pnl import _btc_benchmark_applicable

    snapshot = _snapshot(monkeypatch, symbol="SOXL/USDT", stock_linked=True)

    assert snapshot["btc_entry_price"] is None
    assert snapshot["regime_tag"] is None
    context = snapshot["market_context"]
    assert context["analysis_context"]["kind"] == "stock_linked_perp"
    assert context["instrument_background"]["reference_asset"] == "SOXL"

    attribution = _paper_position_attribution("SOXL/USDT", 100.0, snapshot)
    assert attribution["btc_entry_price"] is None
    assert attribution["regime_tag"] is None
    summary = json.loads(attribution["entry_market_summary"])
    assert "regime" not in summary
    assert summary["analysis_context"]["kind"] == "stock_linked_perp"
    assert _btc_benchmark_applicable({"entry_market_summary": attribution["entry_market_summary"]}) is False


def test_crypto_snapshot_keeps_btc_benchmark_and_regime(monkeypatch):
    snapshot = _snapshot(monkeypatch, symbol="BTC/USDT", stock_linked=False)

    assert snapshot["btc_entry_price"] == 60_000.0
    assert snapshot["regime_tag"] == "bull"
    assert snapshot["market_context"]["analysis_context"]["kind"] == "crypto"
