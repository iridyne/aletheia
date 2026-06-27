"""Tests for aletheia.data.collectors.coingecko_top.

Covers CoinGeckoTopCollector.collect() and _build_movers_summary():
- field mapping from CoinGecko response → row dict
- non-fatal network failure path (returns empty rows + errors)
- per_page clamping to CoinGecko 250-row hard ceiling
- top gainers/losers summary formatting
- summary returns None when too few rows have 24h pct change
"""

from __future__ import annotations

import asyncio

import pytest

from aletheia.data.collectors import coingecko_top as cg_mod
from aletheia.data.collectors.coingecko_top import (
    CoinGeckoTopCollector,
    _build_movers_summary,
)


# ---------- helpers ----------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeClient:
    """Mimics the async client yielded by httpx_client(...)."""

    def __init__(self, payload, captured_kwargs: list):
        self._payload = payload
        self._captured = captured_kwargs

    async def get(self, url, **kwargs):
        self._captured.append({"url": url, **kwargs})
        return _FakeResponse(self._payload)


class _FakeCtx:
    """Async context manager that yields a _FakeClient."""

    def __init__(self, payload, captured_kwargs: list):
        self._payload = payload
        self._captured = captured_kwargs

    async def __aenter__(self):
        return _FakeClient(self._payload, self._captured)

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FailingCtx:
    """Async context manager whose __aenter__ blows up — emulates network failure."""

    async def __aenter__(self):
        raise RuntimeError("connection reset")

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _patch_httpx(monkeypatch, payload, captured):
    def factory(*args, **kwargs):
        return _FakeCtx(payload, captured)

    monkeypatch.setattr(cg_mod, "httpx_client", factory)


def _make_coin(i: int, pct_24h: float | None = 0.0, pct_1h: float | None = 0.1, pct_7d: float | None = 0.5):
    return {
        "id": f"coin-{i}",
        "symbol": f"sym{i}",
        "name": f"Coin {i}",
        "current_price": 100.0 + i,
        "market_cap": 1_000_000 * (i + 1),
        "market_cap_rank": i + 1,
        "total_volume": 50_000 * (i + 1),
        "price_change_percentage_1h_in_currency": pct_1h,
        "price_change_percentage_24h_in_currency": pct_24h,
        "price_change_percentage_7d_in_currency": pct_7d,
    }


# ---------- tests ----------


def test_collect_maps_fields(monkeypatch):
    payload = [_make_coin(i, pct_24h=float(i)) for i in range(5)]
    captured: list = []
    _patch_httpx(monkeypatch, payload, captured)

    collector = CoinGeckoTopCollector()
    result = asyncio.run(collector.collect(top_n=5))

    assert result["errors"] == []
    assert isinstance(result["rows"], list)
    assert len(result["rows"]) == 5

    expected_keys = {
        "coin_id",
        "symbol",
        "name",
        "price_usd",
        "market_cap_usd",
        "market_cap_rank",
        "volume_24h_usd",
        "price_change_1h_pct",
        "price_change_24h_pct",
        "price_change_7d_pct",
        "observed_at",
    }
    for row in result["rows"]:
        assert set(row.keys()) == expected_keys

    # spot-check first row (symbols are upper-cased)
    first = result["rows"][0]
    assert first["coin_id"] == "coin-0"
    assert first["symbol"] == "SYM0"
    assert first["name"] == "Coin 0"
    assert first["price_usd"] == 100.0
    assert first["market_cap_rank"] == 1
    assert first["volume_24h_usd"] == 50_000
    assert first["price_change_24h_pct"] == 0.0


def test_collect_returns_empty_rows_on_network_failure(monkeypatch):
    def failing_factory(*args, **kwargs):
        return _FailingCtx()

    monkeypatch.setattr(cg_mod, "httpx_client", failing_factory)

    collector = CoinGeckoTopCollector()
    # Should NOT raise — failure is swallowed per spec MS-C03
    result = asyncio.run(collector.collect(top_n=10))

    assert result["rows"] == []
    assert isinstance(result["errors"], list)
    assert len(result["errors"]) >= 1
    assert "coingecko_top" in result["errors"][0]
    assert result["movers_summary"] is None


def test_collect_clamps_top_n_to_250(monkeypatch):
    payload: list = []  # empty list is fine — we only inspect captured params
    captured: list = []
    _patch_httpx(monkeypatch, payload, captured)

    collector = CoinGeckoTopCollector()
    asyncio.run(collector.collect(top_n=500))

    assert len(captured) == 1
    params = captured[0].get("params")
    assert params is not None, f"client.get was not called with params kwarg: {captured[0]}"
    assert params["per_page"] == 250
    assert params["vs_currency"] == "usd"
    assert params["order"] == "market_cap_desc"


def test_build_movers_summary_picks_top5():
    # 10 rows with pct_24h spanning -10 .. +12
    pcts = [-10.0, -7.5, -5.0, -2.0, 0.0, 1.0, 3.0, 6.0, 9.0, 12.0]
    rows = []
    for i, p in enumerate(pcts):
        rows.append({
            "symbol": f"SYM{i}",
            "price_change_24h_pct": p,
        })

    summary = _build_movers_summary(rows, top_k=5)
    assert summary is not None
    assert "Top Gainers:" in summary
    assert "Top Losers:" in summary

    g_idx = summary.index("Top Gainers:")
    l_idx = summary.index("Top Losers:")
    assert g_idx < l_idx, "gainers section must come before losers section"

    # The top 5 gainers (by pct desc) are SYM9..SYM5 (12, 9, 6, 3, 1)
    gainers_section = summary[g_idx:l_idx]
    for sym in ("SYM9", "SYM8", "SYM7", "SYM6", "SYM5"):
        assert sym in gainers_section, f"{sym} should appear in gainers section: {gainers_section}"

    # The top 5 losers are SYM0..SYM4 (-10, -7.5, -5, -2, 0)
    losers_section = summary[l_idx:]
    for sym in ("SYM0", "SYM1", "SYM2", "SYM3", "SYM4"):
        assert sym in losers_section, f"{sym} should appear in losers section: {losers_section}"


def test_build_movers_summary_returns_none_with_too_few_rows():
    rows = [
        {"symbol": "AAA", "price_change_24h_pct": 1.0},
        {"symbol": "BBB", "price_change_24h_pct": -2.0},
        {"symbol": "CCC", "price_change_24h_pct": 3.0},
    ]
    assert _build_movers_summary(rows) is None
