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


# ---------------------------------------------------------------------------
# extract_oi_usd — USD-notional unit rule (single source of truth)
# ---------------------------------------------------------------------------


def test_extract_oi_usd_prefers_open_interest_value():
    """OKX exposes both openInterestAmount (contracts) and openInterestValue
    (USD). The USD notional must win; the amount is retained as coin."""
    from aletheia.data.collectors.derivatives import extract_oi_usd

    result = extract_oi_usd(
        {"openInterestAmount": 3_088_517.62, "openInterestValue": 2_016_542_570.38}
    )
    assert result == (2_016_542_570.38, 3_088_517.62)


def test_extract_oi_usd_falls_back_to_amount_times_mark():
    """When openInterestValue is absent (Binance live OI), convert the coin
    amount to USD with the mark price rather than storing the coin count."""
    from aletheia.data.collectors.derivatives import extract_oi_usd

    oi_usd, oi_coin = extract_oi_usd(
        {"openInterestAmount": 107_149.829, "openInterestValue": None},
        mark_price=65_509.2,
    )
    assert oi_coin == 107_149.829
    assert abs(oi_usd - 107_149.829 * 65_509.2) < 1e-3


def test_extract_oi_usd_none_when_not_derivable():
    """No USD notional and no mark price → None, so callers store None instead
    of a wrong-unit value (was ~660× off on OKX BTC)."""
    from aletheia.data.collectors.derivatives import extract_oi_usd

    assert extract_oi_usd(
        {"openInterestAmount": 107_149.829, "openInterestValue": None}, mark_price=None
    ) is None


def test_parse_oi_returns_none_without_usd():
    """_parse_oi drops a row it cannot express in USD (honest gap over garbage)."""
    from aletheia.data.collectors.derivatives import _parse_oi

    assert _parse_oi("BTC/USDT", "binance", {"openInterestAmount": 1.0}) is None


# ---------------------------------------------------------------------------
# open_interest_near + _parse_oi delta — true 1h window, venue-scoped, capped
# ---------------------------------------------------------------------------


def _seed_oi(repo, *, symbol, exchange, usd, age_min, now):
    repo.insert_open_interest_snapshot({
        "symbol": symbol,
        "exchange": exchange,
        "open_interest_usd": usd,
        "observed_at": (now - timedelta(minutes=age_min)).isoformat(),
    })


def test_open_interest_near_picks_band_and_scopes_exchange(tmp_path, monkeypatch):
    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import repositories as repo
    from aletheia.storage import migrations as mig
    sq.close_db()
    mig.run_migrations()

    now = datetime.now(timezone.utc)
    # okx: one row inside the [45,75]min band (~60m) plus out-of-band rows.
    _seed_oi(repo, symbol="BTC/USDT", exchange="okx", usd=1000.0, age_min=60, now=now)
    _seed_oi(repo, symbol="BTC/USDT", exchange="okx", usd=2000.0, age_min=30, now=now)   # too recent
    _seed_oi(repo, symbol="BTC/USDT", exchange="okx", usd=3000.0, age_min=200, now=now)  # too old
    # foreign venue inside the band — must be ignored by an exchange-scoped read.
    _seed_oi(repo, symbol="BTC/USDT", exchange="binance", usd=999999.0, age_min=60, now=now)

    near = repo.open_interest_near("BTC/USDT", "okx")
    assert near is not None and near["open_interest_usd"] == 1000.0

    # No okx row in band for a different symbol → None (never a foreign/stale row).
    assert repo.open_interest_near("ETH/USDT", "okx") is None
    sq.close_db()


def test_parse_oi_delta_true_1h_ignores_foreign_venue(tmp_path, monkeypatch):
    """Delta is computed against a ~1h-ago snapshot on the SAME venue, not the
    freshest cross-venue row (that mixing fabricated ±100%–2500% deltas)."""
    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import repositories as repo
    from aletheia.storage import migrations as mig
    from aletheia.data.collectors.derivatives import _parse_oi
    sq.close_db()
    mig.run_migrations()

    now = datetime.now(timezone.utc)
    _seed_oi(repo, symbol="BTC/USDT", exchange="okx", usd=1000.0, age_min=60, now=now)
    # A fresh foreign-venue row with wildly different units — must not be used.
    _seed_oi(repo, symbol="BTC/USDT", exchange="binance", usd=5.0, age_min=2, now=now)

    snap = _parse_oi("BTC/USDT", "okx", {"openInterestValue": 1100.0})
    assert snap is not None
    assert snap.open_interest_usd == 1100.0
    assert snap.oi_change_1h_pct == 10.0  # (1100-1000)/1000, not vs the binance 5.0
    sq.close_db()


def test_parse_oi_delta_sanity_cap_drops_artifact(tmp_path, monkeypatch):
    """A >300% computed delta (unit transition / relisted market) → None."""
    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import repositories as repo
    from aletheia.storage import migrations as mig
    from aletheia.data.collectors.derivatives import _parse_oi
    sq.close_db()
    mig.run_migrations()

    now = datetime.now(timezone.utc)
    _seed_oi(repo, symbol="BTC/USDT", exchange="okx", usd=1.0, age_min=60, now=now)

    snap = _parse_oi("BTC/USDT", "okx", {"openInterestValue": 1000.0})
    assert snap is not None
    assert snap.open_interest_usd == 1000.0
    assert snap.oi_change_1h_pct is None  # 99900% delta capped out
    sq.close_db()
# ---------------------------------------------------------------------------
# collect() — batch (OKX) vs per-symbol (Bitget) fetch strategy + full coverage
# ---------------------------------------------------------------------------


def _patch_collect_env(monkeypatch, tmp_path, exchange, *, lsr=False):
    """Wire a fake provider + config + fresh DB for a DerivativesCollector.collect run."""
    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import migrations as mig
    sq.close_db()
    mig.run_migrations()

    import aletheia.data.collectors.derivatives as mod
    from aletheia.data import ccxt_provider as provider_mod

    class _Provider:
        _primary_name = exchange.id

        def _ensure_primary(self):
            return exchange

        async def close(self):
            return None

    cfg = type("Cfg", (), {"market_data": type("MD", (), {
        "enable_long_short_ratio": lsr, "derivatives_history_hours": 0,
    })()})()
    monkeypatch.setattr(provider_mod, "get_market_data_provider", lambda: _Provider())
    monkeypatch.setattr(cfg_loader, "load_config", lambda: cfg)
    return mod


def test_collect_batch_path_covers_all_targets_in_usd(tmp_path, monkeypatch):
    """OKX-style batch venue: 2 batch calls cover every passed perp, USD-normalized."""
    class _BatchExchange:
        id = "okx"
        has = {"fetchOpenInterests": True, "fetchFundingRates": True}
        markets = {"BTC/USDT:USDT": {}, "ETH/USDT:USDT": {}}

        async def load_markets(self):
            return self.markets

        async def fetch_funding_rates(self, params=None):
            return {
                "BTC/USDT:USDT": {"fundingRate": 0.0001, "markPrice": 65000.0},
                "ETH/USDT:USDT": {"fundingRate": 0.0002, "markPrice": 3000.0},
            }

        async def fetch_open_interests(self, params=None):
            return {
                "BTC/USDT:USDT": {"openInterestValue": 2.0e9, "openInterestAmount": 30000.0},
                "ETH/USDT:USDT": {"openInterestValue": 1.5e9, "openInterestAmount": 500000.0},
            }

    mod = _patch_collect_env(monkeypatch, tmp_path, _BatchExchange())
    result = asyncio.run(mod.DerivativesCollector().collect(["BTC/USDT", "ETH/USDT"]))

    oi_by_sym = {s.symbol: s for s in result["open_interest"]}
    assert set(oi_by_sym) == {"BTC/USDT", "ETH/USDT"}
    assert oi_by_sym["BTC/USDT"].open_interest_usd == 2.0e9   # USD notional, not contracts
    assert oi_by_sym["ETH/USDT"].open_interest_usd == 1.5e9
    assert len(result["funding"]) == 2
    from aletheia.storage import sqlite as sq
    sq.close_db()


def test_collect_per_symbol_path_converts_amount_to_usd(tmp_path, monkeypatch):
    """Non-batch venue (Bitget): per-symbol loop, amount×mark → USD."""
    calls = {"n": 0}

    class _PerSymbolExchange:
        id = "bitget"
        has = {}  # no batch support → per-symbol path
        markets = {"BTC/USDT:USDT": {}}

        async def load_markets(self):
            return self.markets

        async def fetch_funding_rate(self, sym):
            return {"fundingRate": 0.0001, "markPrice": 65000.0}

        async def fetch_open_interest(self, sym):
            calls["n"] += 1
            return {"openInterestAmount": 100.0, "openInterestValue": None}

    mod = _patch_collect_env(monkeypatch, tmp_path, _PerSymbolExchange())
    result = asyncio.run(mod.DerivativesCollector().collect(["BTC/USDT"]))

    assert calls["n"] == 1  # per-symbol fetch was used
    assert len(result["open_interest"]) == 1
    assert result["open_interest"][0].open_interest_usd == 100.0 * 65000.0
    from aletheia.storage import sqlite as sq
    sq.close_db()
