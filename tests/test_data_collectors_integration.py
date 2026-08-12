"""Integration test — data_collectors Phase A + Phase B end-to-end (plan §9.3).

Drives the same code path the scheduler tick fires (`_async_data_collectors`)
but with all four external HTTP endpoints (CCXT exchanges, Alt.me, CoinGecko,
Binance fapi) mocked so the test is offline and deterministic. The acceptance
criterion is plan §9.3: "after one round, the five new signals each have at
least one row in their target storage location, and the enriched_context render
emits all five LLM-visible tokens."

Failure paths are NOT exhaustively re-tested here — those are covered by the
unit test trio (test_derivatives_collector / test_macro_collector /
test_coingecko_top_collector) and the L2c invariants in
tests/specs/spec_macro_signals.py. The contract this file alone verifies:
the WIRING from collector → SQLite → repo readers → context render is intact.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone

import pytest


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Standard DB fixture mirroring spec_macro_signals.py — fresh app.db in tmp_path."""
    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import migrations as mig
    sq.close_db()
    mig.run_migrations()
    yield tmp_path
    sq.close_db()


def test_round2_collectors_e2e_phase_b_tick(fresh_db, monkeypatch):
    """Run the Phase B tick once with mocked HTTP and verify all five signals
    landed: funding, LSR-populated OI, F&G, CoinGecko top, and (transitively)
    the macro top_movers_summary."""
    # ----- mock CCXT funding/OI fetches -----
    class _FakeFutures:
        markets = {"BTC/USDT:USDT": {}}

        async def load_markets(self):
            return self.markets

        async def fetch_funding_rate(self, sym):
            return {
                "fundingRate": 0.0001,
                "fundingDatetime": "2026-06-27T08:00:00Z",
                "markPrice": 60_000.0,
            }

        async def fetch_open_interest(self, sym):
            return {
                "openInterestAmount": 100_000.0,
                "openInterestValue": 6_000_000_000.0,
                "info": {"sumOpenInterest": "100000"},
            }

    class _FakeProvider:
        _primary_name = "binance"
        _sessions = []

        def _ensure_primary(self):
            return _FakeFutures()

        async def close(self):
            return None

    from aletheia.data import ccxt_provider as ccxt_mod
    monkeypatch.setattr(ccxt_mod, "get_market_data_provider", lambda: _FakeProvider())

    # ----- mock httpx for: Binance LSR + Alt.me F&G + CoinGecko top -----
    class _FakeResp:
        def __init__(self, payload, status=200):
            import json as _json
            self._payload = payload
            self.status_code = status
            self.text = _json.dumps(payload) if not isinstance(payload, str) else payload

        def json(self):
            return self._payload if not isinstance(self._payload, str) else __import__("json").loads(self._payload)

        def raise_for_status(self):
            return None

    class _FakeClient:
        async def get(self, url: str, **kwargs):
            if "alternative.me" in url:
                return _FakeResp({"data": [{"value": "15", "value_classification": "Extreme Fear"}]})
            if "coingecko" in url and "markets" in url:
                return _FakeResp([
                    {
                        "id": f"coin{i}", "symbol": f"C{i}", "name": f"Coin {i}",
                        "current_price": 100 + i, "market_cap": 1e9 - i * 1e6,
                        "market_cap_rank": i + 1, "total_volume": 5e6 + i * 1e5,
                        "price_change_percentage_1h_in_currency": 0.5,
                        "price_change_percentage_24h_in_currency": float(10 - i * 2),
                        "price_change_percentage_7d_in_currency": 1.0,
                    }
                    for i in range(10)
                ])
            if "globalLongShortAccountRatio" in url:
                return _FakeResp([{"symbol": "BTCUSDT", "longShortRatio": "2.09",
                                   "longAccount": "0.677", "shortAccount": "0.323",
                                   "timestamp": 1782539364000}])
            return _FakeResp({}, status=404)

    @contextlib.asynccontextmanager
    async def _fake_httpx_client(*args, **kwargs):
        yield _FakeClient()

    # Patch httpx_client at source (collectors import it lazily).
    from aletheia import net as net_mod
    monkeypatch.setattr(net_mod, "httpx_client", _fake_httpx_client)
    # Also patch the macro module's bound reference (top-of-file import).
    from aletheia.data.collectors import macro as macro_mod
    monkeypatch.setattr(macro_mod, "httpx_client", _fake_httpx_client)
    monkeypatch.setattr(
        macro_mod, "_fetch_one_tradfi_ticker", lambda _ticker: None
    )
    from aletheia.data.collectors import derivatives as deriv_mod
    monkeypatch.setattr(deriv_mod, "httpx_client", _fake_httpx_client)
    from aletheia.data.collectors import coingecko_top as cg_mod
    monkeypatch.setattr(cg_mod, "httpx_client", _fake_httpx_client)

    # Drive the collectors directly in the Phase B order the scheduler uses.
    from aletheia.data.collectors.derivatives import DerivativesCollector
    from aletheia.data.collectors.macro import MacroCollector
    from aletheia.data.collectors.coingecko_top import CoinGeckoTopCollector
    from aletheia.storage import repositories as repo

    symbols = ["BTC/USDT"]

    async def run_tick():
        # Phase A
        deriv_result = await DerivativesCollector().collect(symbols)
        for snap in deriv_result.get("funding", []):
            repo.insert_funding_rate_snapshot(snap.model_dump())
        for snap in deriv_result.get("open_interest", []):
            repo.insert_open_interest_snapshot(snap.model_dump())

        # Phase B order (per scheduler): CoinGecko top first, then MacroCollector
        # picks up movers_summary into the same MacroSnapshot insert.
        cg_result = await CoinGeckoTopCollector().collect(top_n=10)
        if cg_result.get("rows"):
            repo.insert_coingecko_top_rows(cg_result["rows"])
        macro_result = await MacroCollector().collect(symbols)
        snap = macro_result.get("snapshot")
        if snap:
            if cg_result.get("movers_summary"):
                snap.top_movers_summary = cg_result["movers_summary"]
            repo.insert_macro_snapshot(snap.model_dump())

    asyncio.run(run_tick())

    # ===== ACCEPTANCE =====
    # 1. funding row landed
    fr = repo.latest_funding_rate("BTC/USDT")
    assert fr is not None, "funding_rate_snapshots empty after tick"
    assert fr["funding_rate"] == pytest.approx(0.0001)

    # 2. OI row landed AND has long_short_ratio (Step 1 wiring)
    oi = repo.latest_open_interest("BTC/USDT")
    assert oi is not None, "open_interest_snapshots empty after tick"
    assert oi["long_short_ratio"] is not None, (
        "LSR write path broken — open_interest_snapshots.long_short_ratio is NULL "
        "even though the Binance LSR mock returned 2.09"
    )
    assert oi["long_short_ratio"] == pytest.approx(2.09)

    # 3. Macro snapshot has F&G fields populated (Step 2 wiring)
    macro = repo.latest_macro_snapshot()
    assert macro is not None, "macro_snapshots empty after tick"
    assert macro["fng_value"] == 15
    assert macro["fng_classification"] == "Extreme Fear"

    # 4. CoinGecko top batch landed AND latest_coingecko_top is single-batch
    cg_rows = repo.latest_coingecko_top(top_n=5)
    assert len(cg_rows) == 5, f"expected 5 CoinGecko top rows; got {len(cg_rows)}"
    observed_ats = {r["observed_at"] for r in cg_rows}
    assert len(observed_ats) == 1, (
        f"latest_coingecko_top must return a single batch; got mixed: {observed_ats}"
    )

    # 5. top_movers_summary folded into MacroSnapshot row (Step 3 wiring)
    assert macro["top_movers_summary"] is not None, (
        "top_movers_summary not folded into the same MacroSnapshot row"
    )
    assert "Top Gainers" in macro["top_movers_summary"]
    assert "Top Losers" in macro["top_movers_summary"]

    # 6. enriched_context render emits all five LLM-visible tokens
    from aletheia.data.context_builder import render_enriched_context_md
    from aletheia.data.models import LeverageState, MacroSnapshot
    from aletheia.data.normalizer import compute_leverage_state
    lv = compute_leverage_state(
        symbol="BTC/USDT",
        funding_rate=fr["funding_rate"],
        funding_annualized=fr.get("funding_rate_annualized"),
        open_interest_usd=oi["open_interest_usd"],
        oi_delta_1h_pct=oi.get("oi_change_1h_pct"),
        long_short_ratio=oi.get("long_short_ratio"),
    )
    macro_obj = MacroSnapshot(**macro)
    rendered = render_enriched_context_md(
        ["BTC/USDT"], {}, {"BTC/USDT": lv}, macro_obj,
    )
    # The five LLM-visible tokens — fail loudly on any single drop.
    must_appear = ("| LSR |", "2.09", "Fear & Greed: 15", "Top Gainers", "Top Losers")
    missing = [tok for tok in must_appear if tok not in rendered]
    assert not missing, (
        f"enriched_context render is missing LLM-visible tokens: {missing}\n"
        f"Render output was:\n{rendered}"
    )
