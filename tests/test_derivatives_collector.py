"""Unit tests for aletheia/data/collectors/derivatives.py — B3 batch (plan §9.1).

Covers:
  - _parse_oi: LSR threading (value present + value None)
  - _fetch_lsr_binance_batch: graceful HTTP failure (returns {} without raising)
  - _maybe_backfill_funding_history: idempotency with ≥3 existing rows
  - _maybe_backfill_oi_history: swallows NotSupported (Bitget) into errors list

These mirror the DB-fixture pattern from
tests/specs/spec_macro_signals.py::test_backfill_idempotent_with_existing_rows
(monkeypatch cfg_loader.DATA_DIR → tmp_path; sq.close_db(); mig.run_migrations()).
Async functions are driven via asyncio.run(); no pytest-asyncio dependency.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone, timedelta

import pytest


# ---------------------------------------------------------------------------
# _parse_oi — LSR threading
# ---------------------------------------------------------------------------


def test_parse_oi_threads_lsr_value():
    """Given a raw CCXT OI dict + lsr=2.09, _parse_oi returns an
    OpenInterestSnapshot whose long_short_ratio == 2.09 (single-construction,
    no follow-up UPDATE).
    """
    from aletheia.data.collectors.derivatives import _parse_oi
    from aletheia.data.models import OpenInterestSnapshot

    raw = {
        "openInterestValue": 1_234_567.0,
        "info": {"sumOpenInterest": "12.34"},
    }
    snap = _parse_oi("BTC/USDT", "binance", raw, lsr=2.09)
    assert isinstance(snap, OpenInterestSnapshot)
    assert snap.long_short_ratio == 2.09
    assert snap.symbol == "BTC/USDT"
    assert snap.exchange == "binance"
    assert snap.open_interest_usd == 1_234_567.0


def test_parse_oi_lsr_none_when_absent():
    """Same raw dict, lsr=None → snapshot constructs with long_short_ratio=None
    (must not raise — LSR is an optional augmentation per MS-C01).
    """
    from aletheia.data.collectors.derivatives import _parse_oi
    from aletheia.data.models import OpenInterestSnapshot

    raw = {
        "openInterestValue": 999.0,
        "info": {},
    }
    snap = _parse_oi("ETH/USDT", "binance", raw, lsr=None)
    assert isinstance(snap, OpenInterestSnapshot)
    assert snap.long_short_ratio is None
    assert snap.open_interest_usd == 999.0


# ---------------------------------------------------------------------------
# _fetch_lsr_binance_batch — graceful HTTP failure
# ---------------------------------------------------------------------------


def test_fetch_lsr_binance_batch_returns_empty_on_http_error(monkeypatch):
    """Monkeypatch httpx_client so every client.get() raises — assert the function
    returns an empty dict and does not propagate (the per-symbol try/except inside
    the loop swallows each failure).

    Pattern mirrors spec_macro_signals.py::test_coingecko_top_degraded_does_not_raise.
    """
    import aletheia.data.collectors.derivatives as mod

    class FailingClient:
        async def get(self, *args, **kwargs):
            raise RuntimeError("simulated HTTP failure")

    class FakeCtx:
        async def __aenter__(self):
            return FailingClient()

        async def __aexit__(self, *a):
            return False

    def fake_client(*args, **kwargs):
        return FakeCtx()

    monkeypatch.setattr(mod, "httpx_client", fake_client)

    result = asyncio.run(mod._fetch_lsr_binance_batch(["BTC/USDT", "ETH/USDT"]))
    assert result == {}, f"expected empty dict on HTTP failure; got: {result!r}"


def test_lsr_timeout_does_not_cancel_primary_funding_or_oi(monkeypatch):
    """LSR is optional enrichment; its timeout must not consume the live path."""
    import aletheia.data.collectors.derivatives as mod
    from aletheia.config import loader as cfg_loader
    from aletheia.data import ccxt_provider as provider_mod

    class _Exchange:
        markets = {"BTC/USDT:USDT": {}}

        async def load_markets(self):
            return self.markets

        async def fetch_funding_rate(self, symbol):
            assert symbol == "BTC/USDT:USDT"
            return {"fundingRate": 0.0001}

        async def fetch_open_interest(self, symbol):
            assert symbol == "BTC/USDT:USDT"
            return {"openInterestValue": 123.0}

    class _Provider:
        _primary_name = "okx"

        def _ensure_primary(self):
            return _Exchange()

        async def close(self):
            return None

    async def _stalled_lsr(symbols):
        await asyncio.Event().wait()

    cfg = type(
        "Cfg", (), {"market_data": type("MD", (), {
            "enable_long_short_ratio": True,
            "derivatives_history_hours": 0,
        })()}
    )()
    monkeypatch.setattr(provider_mod, "get_market_data_provider", lambda: _Provider())
    monkeypatch.setattr(cfg_loader, "load_config", lambda: cfg)
    monkeypatch.setattr(mod, "_fetch_lsr_binance_batch", _stalled_lsr)
    monkeypatch.setattr(mod, "_LSR_BUDGET_SEC", 0.01)

    result = asyncio.run(mod.DerivativesCollector().collect(["BTC/USDT"]))

    assert len(result["funding"]) == 1
    assert len(result["open_interest"]) == 1
    assert "lsr_batch_timeout" in result["errors"]


def test_live_derivatives_collect_does_not_run_history_backfill():
    """Sparse history is maintenance work, not part of the two-minute live path."""
    from aletheia.data.collectors.derivatives import DerivativesCollector

    live_source = inspect.getsource(DerivativesCollector.collect)
    history_source = inspect.getsource(DerivativesCollector.collect_history)

    assert "_maybe_backfill_funding_history" not in live_source
    assert "_maybe_backfill_oi_history" not in live_source
    assert "_maybe_backfill_funding_history" in history_source
    assert "_maybe_backfill_oi_history" in history_source


# ---------------------------------------------------------------------------
# _maybe_backfill_funding_history — idempotency
# ---------------------------------------------------------------------------


def test_maybe_backfill_funding_idempotent_with_3_rows(tmp_path, monkeypatch):
    """Seed 3 funding_rate_snapshots rows within the past hour; assert the
    backfill skips fetch entirely (its FakeExchange.fetch_funding_rate_history
    would AssertionError if reached).
    """
    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import repositories as repo
    from aletheia.storage import migrations as mig
    sq.close_db()
    mig.run_migrations()

    now = datetime.now(timezone.utc)
    sym = "BTC/USDT"
    for offset in (1, 2, 3):
        repo.insert_funding_rate_snapshot({
            "symbol": sym,
            "exchange": "binance",
            "funding_rate": 0.0001,
            "funding_rate_annualized": 0.1095,
            "observed_at": (now - timedelta(minutes=offset)).isoformat(),
        })

    from aletheia.data.collectors.derivatives import _maybe_backfill_funding_history

    class _FakeExchange:
        async def fetch_funding_rate_history(self, *args, **kwargs):
            raise AssertionError(
                "backfill must NOT call fetch_funding_rate_history when ≥3 rows exist"
            )

    result = asyncio.run(_maybe_backfill_funding_history(
        _FakeExchange(), sym, "BTC/USDT:USDT", "binance", since_hours=24,
    ))
    assert result == 0, (
        f"backfill must be a no-op with 3 existing rows; got {result} inserts"
    )
    sq.close_db()


# ---------------------------------------------------------------------------
# _maybe_backfill_oi_history — swallows NotSupported
# ---------------------------------------------------------------------------


def test_maybe_backfill_oi_swallows_not_supported(tmp_path, monkeypatch):
    """When the exchange raises a 'not supported' exception (Bitget shape),
    _maybe_backfill_oi_history must return 0 and append a descriptive entry
    to the errors list — NOT re-raise.
    """
    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import migrations as mig
    sq.close_db()
    mig.run_migrations()

    from aletheia.data.collectors.derivatives import _maybe_backfill_oi_history

    class _FakeBitget:
        async def fetch_open_interest_history(self, *args, **kwargs):
            raise Exception("bitget fetchOpenInterestHistory() is not supported yet")

    errors: list[str] = []
    result = asyncio.run(_maybe_backfill_oi_history(
        _FakeBitget(), "BTC/USDT", "BTC/USDT:USDT", "bitget",
        since_hours=24, errors=errors,
    ))
    assert result == 0, f"expected 0 inserts on NotSupported; got {result}"
    assert errors, "errors list must be populated when fetch fails"
    assert any("not supported" in e.lower() for e in errors), (
        f"errors must record the 'not supported' failure; got: {errors}"
    )
    sq.close_db()
