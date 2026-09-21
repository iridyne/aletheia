"""Unit tests for extensions/data/collectors/derivatives.py — B3 batch (plan §9.1).

Covers:
  - _parse_oi: LSR threading (value present + value None)
  - _fetch_binance_ratio_batch: graceful HTTP failure (returns {} without raising)
  - _maybe_backfill_funding_history: idempotency with ≥3 existing rows
  - _maybe_backfill_oi_history: swallows NotSupported (Bitget) into errors list

These mirror the DB-fixture pattern from
tests/specs/spec_macro_signals.py::test_backfill_idempotent_with_existing_rows
(monkeypatch cfg_loader.DATA_DIR → tmp_path; sq.close_db(); mig.run_migrations()).
Async functions are driven via asyncio.run(); no pytest-asyncio dependency.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import pytest



def _market(symbol):
    return dict(id=symbol.replace('/', ''), symbol=symbol+':USDT', swap=True,
        base=symbol.split('/')[0], quote='USDT', settle='USDT', linear=True, contractSize=1)

def test_derivatives_source_time_survives_persistence_and_controls_freshness(
    tmp_path,
    monkeypatch,
):
    import extensions.data.collectors.derivatives as mod
    from aletheia.config import loader as cfg_loader
    from aletheia.data.freshness import fresh_row
    from aletheia.storage import migrations, repositories as repo
    from aletheia.storage import sqlite as sq

    acquired_at = datetime.now(timezone.utc).replace(microsecond=0)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return acquired_at

    monkeypatch.setattr(mod, "datetime", FixedDateTime)
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    sq.close_db()
    migrations.run_migrations()

    stale_ms = int((acquired_at - timedelta(hours=2)).timestamp() * 1000)
    funding = mod._parse_funding(
        "BTC/USDT",
        "okx",
        {"fundingRate": 0.0001, "timestamp": stale_ms},
    )
    oi = mod._parse_oi(
        "BTC/USDT",
        "okx",
        {"openInterestValue": 1_000_000.0, "info":{"oiUsd":1000000}, "timestamp": stale_ms}, market=_market("BTC/USDT"),
    )

    assert funding is not None
    assert oi is not None
    for snapshot, domain in ((funding, "funding"), (oi, "open_interest")):
        row = snapshot.model_dump()
        assert row["source_observed_at"] == (acquired_at - timedelta(hours=2)).isoformat()
        assert row["acquired_at"] == acquired_at.isoformat()
        assert row["source_time_status"] == "provided"
        assert row["observed_at"] == row["source_observed_at"]
        assert fresh_row(row, domain, now=acquired_at) is None

    recent_ms = int((acquired_at - timedelta(minutes=2)).timestamp() * 1000)
    recent = mod._parse_funding(
        "ETH/USDT",
        "okx",
        {"fundingRate": 0.0002, "timestamp": recent_ms},
    )
    recent_oi = mod._parse_oi(
        "ETH/USDT",
        "okx",
        {"openInterestValue": 2_000_000.0, "info":{"oiUsd":2000000}, "timestamp": recent_ms}, market=_market("ETH/USDT"),
    )
    assert recent is not None
    assert recent_oi is not None
    repo.insert_funding_rate_snapshot(recent.model_dump())
    repo.insert_open_interest_snapshot(recent_oi.model_dump())
    persisted = repo.latest_funding_rate("ETH/USDT", exchange="okx")
    persisted_oi = repo.latest_open_interest("ETH/USDT", exchange="okx")
    assert persisted is not None
    assert persisted_oi is not None
    assert persisted["source_observed_at"] == recent.source_observed_at
    assert persisted["acquired_at"] == recent.acquired_at
    assert persisted_oi["source_observed_at"] == recent_oi.source_observed_at
    assert persisted_oi["acquired_at"] == recent_oi.acquired_at
    assert fresh_row(persisted, "funding", now=acquired_at) is not None
    assert fresh_row(persisted_oi, "open_interest", now=acquired_at) is not None
    persisted["acquired_at"] = (acquired_at - timedelta(hours=2)).isoformat()
    assert fresh_row(persisted, "funding", now=acquired_at) is None

    future_ms = int((acquired_at + timedelta(minutes=6)).timestamp() * 1000)
    # "missing": no exchange timestamp, but acquired_at is populated → usable via
    # acquired_at fallback (exchange captures data without providing a source time).
    missing_snap = mod._parse_funding("SOL/USDT", "okx", {"fundingRate": 0.0001})
    assert missing_snap is not None
    assert missing_snap.source_time_status == "missing"
    assert missing_snap.source_observed_at is None
    assert fresh_row(missing_snap.model_dump(), "funding", now=acquired_at) is not None
    # "invalid" and "future": malformed/future source time → not usable.
    for raw, expected_status, expected_source_time in (
        ({"fundingRate": 0.0001, "timestamp": "bad"}, "invalid", None),
        (
            {"fundingRate": 0.0001, "timestamp": future_ms},
            "future",
            (acquired_at + timedelta(minutes=6)).isoformat(),
        ),
    ):
        snapshot = mod._parse_funding("SOL/USDT", "okx", raw)
        assert snapshot is not None
        assert snapshot.source_time_status == expected_status
        assert snapshot.source_observed_at == expected_source_time
        assert fresh_row(snapshot.model_dump(), "funding", now=acquired_at) is None

    sq.close_db()


# ---------------------------------------------------------------------------
# _parse_oi — LSR threading
# ---------------------------------------------------------------------------


def test_parse_oi_threads_lsr_value():
    """Given a raw CCXT OI dict + lsr=2.09, _parse_oi returns an
    OpenInterestSnapshot whose long_short_ratio == 2.09 (single-construction,
    no follow-up UPDATE).
    """
    from extensions.data.collectors.derivatives import _parse_oi
    from aletheia.data.models import OpenInterestSnapshot

    raw = {
        "openInterestValue": 1_234_567.0,
        "info": {"sumOpenInterestValue": "1234567"},
    }
    snap = _parse_oi(
        "BTC/USDT", "binance", raw, lsr=2.09, top_position_ratio=1.42, market=_market("BTC/USDT")
    )
    assert isinstance(snap, OpenInterestSnapshot)
    assert snap.long_short_ratio == 2.09
    assert snap.top_trader_position_ratio == 1.42
    assert snap.symbol == "BTC/USDT"
    assert snap.exchange == "binance"
    assert snap.open_interest_usd == 1_234_567.0


def test_parse_oi_lsr_none_when_absent():
    """Same raw dict, lsr=None → snapshot constructs with long_short_ratio=None
    (must not raise — LSR is an optional augmentation per MS-C01).
    """
    from extensions.data.collectors.derivatives import _parse_oi
    from aletheia.data.models import OpenInterestSnapshot

    raw = {
        "openInterestValue": 999.0,
        "info": {"sumOpenInterestValue":999},
    }
    snap = _parse_oi("ETH/USDT", "binance", raw, lsr=None, market=_market("ETH/USDT"))
    assert isinstance(snap, OpenInterestSnapshot)
    assert snap.long_short_ratio is None
    assert snap.open_interest_usd == 999.0


@pytest.mark.parametrize("invalid_ratio", ["NaN", "Infinity", "-Infinity", -1])
def test_parse_oi_drops_invalid_optional_positioning_without_dropping_oi(
    invalid_ratio,
):
    from extensions.data.collectors.derivatives import _parse_oi

    snap = _parse_oi(
        "BTC/USDT",
        "binance",
        {"openInterestValue": 1_000.0, "info":{"sumOpenInterestValue":1000}, "openInterestAmount": 10.0}, market=_market("BTC/USDT"),
        lsr=invalid_ratio,
        top_position_ratio=invalid_ratio,
    )

    assert snap is not None
    assert snap.open_interest_usd == 1_000.0
    assert snap.long_short_ratio is None
    assert snap.top_trader_position_ratio is None


# ---------------------------------------------------------------------------
# _fetch_binance_ratio_batch — graceful HTTP failure
# ---------------------------------------------------------------------------


def test_fetch_binance_ratio_batch_returns_empty_on_http_error(monkeypatch):
    """Monkeypatch httpx_client so every client.get() raises — assert the function
    returns an empty dict and does not propagate (the per-symbol try/except inside
    the loop swallows each failure).

    Pattern mirrors spec_macro_signals.py::test_coingecko_top_degraded_does_not_raise.
    """
    import extensions.data.collectors.derivatives as mod

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

    result = asyncio.run(mod._fetch_binance_ratio_batch(["BTC/USDT", "ETH/USDT"], mod._BINANCE_LSR_URL))
    assert result == {}, f"expected empty dict on HTTP failure; got: {result!r}"


def test_fetch_top_position_ratio_uses_public_binance_endpoint(monkeypatch):
    import extensions.data.collectors.derivatives as mod

    class Client:
        async def get(self, url, *, params):
            assert url.endswith("topLongShortPositionRatio")
            assert params["symbol"] == "BTCUSDT"
            return type(
                "Response",
                (),
                {"status_code": 200, "json": lambda self: [{"longShortRatio": "1.42"}]},
            )()

    class Context:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(mod, "httpx_client", lambda **_kwargs: Context())
    result = asyncio.run(
        mod._fetch_binance_ratio_batch(["BTC/USDT"], mod._BINANCE_TOP_POSITION_RATIO_URL)
    )
    assert {symbol: row['value'] for symbol, row in result.items()} == {"BTC/USDT": 1.42}
    assert result['BTC/USDT']['source_observed_at'] is None


@pytest.mark.parametrize("failure", ["timeout", "http_error"])
def test_lsr_failure_does_not_cancel_primary_funding_or_oi(monkeypatch, failure):
    """LSR is optional enrichment; its timeout must not consume the live path."""
    import extensions.data.collectors.derivatives as mod
    from aletheia.config import loader as cfg_loader
    from extensions.data import ccxt_provider as provider_mod

    class _Exchange:
        markets = {"BTC/USDT:USDT": _market("BTC/USDT")}

        async def load_markets(self):
            return self.markets

        async def fetch_funding_rate(self, symbol):
            assert symbol == "BTC/USDT:USDT"
            return {"fundingRate": 0.0001}

        async def fetch_open_interest(self, symbol):
            assert symbol == "BTC/USDT:USDT"
            return {"openInterestValue": 123.0, "info":{"oiUsd":123}}

    class _Provider:
        _primary_name = "okx"

        def _ensure_primary(self):
            return _Exchange()

        async def close(self):
            return None

    cancelled = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, *, params):
            if url == mod._BINANCE_LSR_URL and params["symbol"] == "BTCUSDT":
                return type("Response", (), {"status_code": 200, "json": lambda self: [{"longShortRatio": "1.42"}]})()
            if failure == "http_error":
                return type("Response", (), {"status_code": 503})()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append((url, params["symbol"]))

    cfg = type(
        "Cfg", (), {"market_data": type("MD", (), {
            "enable_long_short_ratio": True,
            "derivatives_history_hours": 0,
        })()}
    )()
    monkeypatch.setattr(provider_mod, "get_market_data_provider", lambda: _Provider())
    monkeypatch.setattr(cfg_loader, "load_config", lambda: cfg)
    monkeypatch.setattr(mod, "httpx_client", lambda **kwargs: Client())
    monkeypatch.setattr(mod, "_LSR_BUDGET_SEC", 0.01)

    result = asyncio.run(mod.DerivativesCollector().collect(["BTC/USDT", "ETH/USDT"]))

    assert len(result["funding"]) == 1
    assert len(result["open_interest"]) == 1
    assert result["open_interest"][0].long_short_ratio == 1.42
    assert result["open_interest"][0].top_trader_position_ratio is None
    assert "top_position_ratio_missing BTC/USDT" in result["errors"]
    assert "global_account_ratio_missing ETH/USDT" in result["errors"]
    assert len(cancelled) == (3 if failure == "timeout" else 0)
    from aletheia.data.collector_health import classify_collector_result
    assert classify_collector_result(result) == "partial"




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
            "funding_rate": 0.0001 * offset,
            "funding_rate_annualized": 0.1095 * offset,
            "observed_at": (now - timedelta(minutes=offset)).isoformat(),
        })

    from extensions.data.collectors.derivatives import _maybe_backfill_funding_history

    class _FakeExchange:
        async def fetch_funding_rate_history(self, *args, **kwargs):
            raise AssertionError(
                "backfill must NOT call fetch_funding_rate_history when ≥3 rows exist"
            )

    result = asyncio.run(_maybe_backfill_funding_history(
        _FakeExchange(), sym, "BTC/USDT:USDT", "binance", since_hours=24,
    ))
    assert result == (0, True), (
        f"backfill must be a no-op with 3 existing rows; got {result} inserts"
    )
    sq.close_db()


# ---------------------------------------------------------------------------
# _maybe_backfill_oi_history — swallows NotSupported
# ---------------------------------------------------------------------------


def test_maybe_backfill_oi_swallows_not_supported(tmp_path, monkeypatch):
    """When the exchange raises a 'not supported' exception (Bitget shape),
    _maybe_backfill_oi_history must return (0, False) and append a descriptive entry
    to the errors list — NOT re-raise.
    """
    from aletheia.config import loader as cfg_loader
    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import sqlite as sq
    from aletheia.storage import migrations as mig
    sq.close_db()
    mig.run_migrations()

    from extensions.data.collectors.derivatives import _maybe_backfill_oi_history

    class _FakeBitget:
        async def fetch_open_interest_history(self, *args, **kwargs):
            raise Exception("bitget fetchOpenInterestHistory() is not supported yet")

    errors: list[str] = []
    result = asyncio.run(_maybe_backfill_oi_history(
        _FakeBitget(), "BTC/USDT", "BTC/USDT:USDT", "bitget",
        since_hours=24, errors=errors,
    ))
    assert result == (0, False), f"expected 0 inserts on NotSupported; got {result}"
    assert errors, "errors list must be populated when fetch fails"
    assert any("not supported" in e.lower() for e in errors), (
        f"errors must record the 'not supported' failure; got: {errors}"
    )
    sq.close_db()


@pytest.mark.parametrize(
    "raw",
    [
        {"fundingRate": "NaN"},
        {"fundingRate": "Infinity"},
        {"fundingRate": "-Infinity"},
        {"fundingRate": 0.0001, "markPrice": "NaN"},
        {"fundingRate": 0.0001, "markPrice": "Infinity"},
        {"fundingRate": 0.0001, "markPrice": "-Infinity"},
        {"fundingRate": 0.0001, "markPrice": 0},
        {"fundingRate": 0.0001, "markPrice": -1},
    ],
)
def test_parse_funding_rejects_nonfinite_or_nonpositive_mark(raw):
    from extensions.data.collectors.derivatives import _parse_funding

    assert _parse_funding("BTC/USDT", "okx", raw) is None


@pytest.mark.parametrize("rate", [-0.0001, 0.0, 0.0001])
def test_parse_funding_preserves_finite_signed_rates_without_optional_mark(rate):
    from extensions.data.collectors.derivatives import _parse_funding

    snap = _parse_funding("BTC/USDT", "okx", {"fundingRate": rate})

    assert snap is not None
    assert snap.funding_rate == rate
    assert snap.mark_price is None






def test_parse_oi_returns_none_without_usd():
    """_parse_oi drops a row it cannot express in USD (honest gap over garbage)."""
    from extensions.data.collectors.derivatives import _parse_oi

    assert _parse_oi("BTC/USDT", "binance", {"openInterestAmount": 1.0}) is None


# ---------------------------------------------------------------------------
# open_interest_near + _parse_oi delta — true 1h window, venue-scoped, capped
# ---------------------------------------------------------------------------


def _seed_oi(repo, *, symbol, exchange, usd, age_min, now):
    from aletheia.data.derivatives_facts import ccxt_metadata
    stamp = (now-timedelta(minutes=age_min)).isoformat()
    repo.insert_open_interest_snapshot({
        "derivatives_metadata_json": ccxt_metadata(symbol,exchange,_market(symbol),{'openInterestValue':usd,'info':{'oiUsd':usd,'sumOpenInterestValue':usd}},family='open_interest'),
        "acquired_at":stamp, "source_time_status":"missing",
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
    from extensions.data.collectors.derivatives import _parse_oi
    sq.close_db()
    mig.run_migrations()

    now = datetime.now(timezone.utc)
    _seed_oi(repo, symbol="BTC/USDT", exchange="okx", usd=1000.0, age_min=60, now=now)
    # A fresh foreign-venue row with wildly different units — must not be used.
    _seed_oi(repo, symbol="BTC/USDT", exchange="binance", usd=5.0, age_min=2, now=now)

    snap = _parse_oi("BTC/USDT", "okx", {"openInterestValue": 1100.0,"info":{"oiUsd":1100}},market=_market("BTC/USDT"))
    assert snap is not None
    assert snap.open_interest_usd == 1100.0
    assert snap.oi_change_1h_pct == pytest.approx(10.0)  # (1100-1000)/1000, not vs the binance 5.0
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

    import extensions.data.collectors.derivatives as mod
    from extensions.data import ccxt_provider as provider_mod

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
        markets = {s+":USDT":_market(s) for s in ("BTC/USDT","ETH/USDT")}

        async def load_markets(self):
            return self.markets

        async def fetch_funding_rates(self, params=None):
            return {
                "BTC/USDT:USDT": {"fundingRate": 0.0001, "markPrice": 65000.0},
                "ETH/USDT:USDT": {"fundingRate": 0.0002, "markPrice": 3000.0},
            }

        async def fetch_open_interests(self, params=None):
            return {
                "BTC/USDT:USDT": {"openInterestValue": 2.0e9, "info":{"oiUsd":2e9,"sumOpenInterestValue":2e9}, "openInterestAmount": 30000.0},
                "ETH/USDT:USDT": {"openInterestValue": 1.5e9, "info":{"oiUsd":1.5e9}, "openInterestAmount": 500000.0},
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


def test_collect_per_symbol_path_rejects_unproved_amount_even_with_mark(tmp_path, monkeypatch):
    """Non-batch venue (Bitget): amount and mark do not prove the quantity unit."""
    calls = {"n": 0}

    class _PerSymbolExchange:
        id = "bitget"
        has = {}  # no batch support → per-symbol path
        markets = {"BTC/USDT:USDT": _market("BTC/USDT")}

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
    assert result["open_interest"] == []
    assert "open_interest_invalid BTC/USDT" in result["errors"]
    from aletheia.storage import sqlite as sq
    sq.close_db()


def test_live_collect_rejects_invalid_rows_before_persistence(tmp_path, monkeypatch):
    class _InvalidExchange:
        id = "bitget"
        has = {}
        markets = {"BTC/USDT:USDT": _market("BTC/USDT")}

        async def load_markets(self):
            return self.markets

        async def fetch_funding_rate(self, _symbol):
            return {"fundingRate": "NaN", "markPrice": "Infinity"}

        async def fetch_open_interest(self, _symbol):
            return {"openInterestAmount": 10, "openInterestValue": None}

    mod = _patch_collect_env(monkeypatch, tmp_path, _InvalidExchange())
    result = asyncio.run(mod.DerivativesCollector().collect(["BTC/USDT"]))

    assert result["funding"] == []
    assert result["open_interest"] == []
    assert result["errors"] == [
        "funding_invalid BTC/USDT",
        "open_interest_invalid BTC/USDT",
    ]

    from aletheia.storage import repositories as repo
    from aletheia.storage import sqlite as sq

    repo.insert_collector_snapshot_batch(
        funding=[row.model_dump() for row in result["funding"]],
        open_interest=[row.model_dump() for row in result["open_interest"]],
    )
    assert repo._select_all("funding_rate_snapshots") == []
    assert repo._select_all("open_interest_snapshots") == []
    from aletheia.data.freshness import fresh_row

    assert fresh_row(
        repo.latest_funding_rate("BTC/USDT", "bitget"), "funding"
    ) is None
    assert fresh_row(
        repo.latest_open_interest("BTC/USDT", "bitget"), "open_interest"
    ) is None
    sq.close_db()


def test_history_backfill_rejects_invalid_rows_before_persistence(
    tmp_path, monkeypatch
):
    from aletheia.config import loader as cfg_loader

    monkeypatch.setattr(cfg_loader, "DATA_DIR", tmp_path)
    from aletheia.storage import migrations as mig
    from aletheia.storage import repositories as repo
    from aletheia.storage import sqlite as sq

    sq.close_db()
    mig.run_migrations()

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    class _InvalidHistoryExchange:
        markets = {"BTC/USDT:USDT": _market("BTC/USDT")}
        async def fetch_funding_rate_history(self, _symbol, limit=24):
            return [
                {"timestamp": now_ms - 16 * 3_600_000, "fundingRate": 0.0001},
                {"timestamp": now_ms - 8 * 3_600_000, "fundingRate": "-Infinity"},
                {"timestamp": now_ms, "fundingRate": 0.0002},
            ]

        async def fetch_open_interest_history(self, _symbol, _timeframe, limit=24):
            return [
                {
                    "timestamp": now_ms,
                    "openInterestValue": "Infinity",
                    "openInterestAmount": -1,
                }
            ]

    from extensions.data.collectors.derivatives import (
        _maybe_backfill_funding_history,
        _maybe_backfill_oi_history,
    )

    errors: list[str] = []
    funding_count = asyncio.run(
        _maybe_backfill_funding_history(
            _InvalidHistoryExchange(),
            "BTC/USDT",
            "BTC/USDT:USDT",
            "bitget",
            since_hours=24,
            errors=errors,
        )
    )
    oi_count = asyncio.run(
        _maybe_backfill_oi_history(
            _InvalidHistoryExchange(),
            "BTC/USDT",
            "BTC/USDT:USDT",
            "bitget",
            since_hours=24,
            errors=errors,
        )
    )

    assert (funding_count, oi_count) == ((2, False), (0, False))
    assert errors == [
        "funding_history_invalid BTC/USDT",
        "oi_history_invalid BTC/USDT",
    ]
    funding = repo._select_all("funding_rate_snapshots", order="observed_at ASC")
    assert [row["funding_rate"] for row in funding] == [0.0001, 0.0002]
    assert all(row["next_funding_time"] == row["observed_at"] for row in funding)
    assert repo._select_all("open_interest_snapshots") == []
    sq.close_db()


# ---------------------------------------------------------------------------
# Cross-venue isolation — foreign rows must NOT suppress primary-venue backfill
# ---------------------------------------------------------------------------


def test_funding_backfill_not_suppressed_by_foreign_venue_rows(tmp_path, monkeypatch):
    """Seed 3 Bybit funding rows; assert a Binance backfill still fetches and inserts.

    Regression for issue #1172: sparse check previously used only (symbol, time),
    so Bybit rows could satisfy the ≥3 guard and silently skip the Binance fetch.
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
            "exchange": "bybit",
            "funding_rate": 0.0001,
            "funding_rate_annualized": 0.1095,
            "observed_at": (now - timedelta(minutes=offset)).isoformat(),
        })

    fetch_called = {"n": 0}

    class _FakeBinance:
        markets = {"BTC/USDT:USDT": _market("BTC/USDT")}
        async def fetch_funding_rate_history(self, perp_sym, limit=24):
            fetch_called["n"] += 1
            ts_ms = int((now - timedelta(hours=1)).timestamp() * 1000)
            return [{"timestamp": ts_ms, "fundingRate": 0.0002}]

    from extensions.data.collectors.derivatives import _maybe_backfill_funding_history
    result = asyncio.run(_maybe_backfill_funding_history(
        _FakeBinance(), sym, "BTC/USDT:USDT", "binance", since_hours=24,
    ))
    assert fetch_called["n"] == 1, "Binance fetch must occur when only Bybit rows exist"
    assert result == (1, False)
    rows = repo._select_all("funding_rate_snapshots", where="exchange=?", params=("binance",))
    assert len(rows) == 1
    assert rows[0]["next_funding_time"] == rows[0]["observed_at"]
    sq.close_db()


def test_oi_backfill_not_suppressed_by_foreign_venue_rows(tmp_path, monkeypatch):
    """Seed 3 Bybit OI rows and one same-timestamp Bybit row; assert Binance
    backfill fetches, inserts, and handles an identical timestamp independently.

    Regression for issue #1172: both the sparse check and the per-row dedup
    previously used only (symbol, time), suppressing Binance inserts entirely.
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
    shared_ts = now - timedelta(hours=1)
    for offset in (1, 2, 3):
        repo.insert_open_interest_snapshot({
            "symbol": sym,
            "exchange": "bybit",
            "open_interest_usd": 1.0e9,
            "open_interest_coin": None,
            "oi_change_1h_pct": None,
            "long_short_ratio": None,
            "top_trader_position_ratio": None,
            "observed_at": (shared_ts - timedelta(minutes=offset)).isoformat(),
        })
    # seed a Bybit row at the exact same timestamp Binance history will return
    repo.insert_open_interest_snapshot({
        "symbol": sym,
        "exchange": "bybit",
        "open_interest_usd": 1.0e9,
        "open_interest_coin": None,
        "oi_change_1h_pct": None,
        "long_short_ratio": None,
        "top_trader_position_ratio": None,
        "observed_at": shared_ts.isoformat(),
    })

    shared_ts_ms = int(shared_ts.timestamp() * 1000)

    class _FakeBinance:
        markets = {"BTC/USDT:USDT": _market("BTC/USDT")}
        async def fetch_open_interest_history(self, perp_sym, timeframe, limit=24):
            return [{"timestamp": shared_ts_ms, "openInterestValue": 2.0e9, "info":{"oiUsd":2e9,"sumOpenInterestValue":2e9}, "openInterestAmount": 30000.0}]

    from extensions.data.collectors.derivatives import _maybe_backfill_oi_history
    result = asyncio.run(_maybe_backfill_oi_history(
        _FakeBinance(), sym, "BTC/USDT:USDT", "binance", since_hours=24,
    ))
    assert result == (1, False), f"expected 1 Binance OI insert at shared timestamp; got {result}"

    # same-venue rerun must be idempotent
    result2 = asyncio.run(_maybe_backfill_oi_history(
        _FakeBinance(), sym, "BTC/USDT:USDT", "binance", since_hours=24,
    ))
    assert result2 == (0, False), f"same-venue rerun must be a no-op; got {result2}"
    sq.close_db()


def test_ratio_batch_covers_unique_targets_with_bounded_concurrency(monkeypatch):
    import extensions.data.collectors.derivatives as mod
    active = peak = 0
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            assert active == 0

        async def get(self, url, *, params):
            nonlocal active, peak
            calls.append(params["symbol"])
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return type("Response", (), {"status_code": 200, "json": lambda self: [{"longShortRatio": "1.42"}]})()

    monkeypatch.setattr(mod, "httpx_client", lambda **kwargs: Client())
    symbols = [f"T{i}/USDT" for i in range(23)]
    result = asyncio.run(mod._fetch_binance_ratio_batch(symbols + symbols[:2], mod._BINANCE_LSR_URL))
    assert {symbol: row['value'] for symbol, row in result.items()} == dict.fromkeys(symbols, 1.42)
    assert len(calls) == 23
    assert 1 < peak <= 4


def test_history_already_current_is_success_but_empty_is_not(tmp_path, monkeypatch):
    from aletheia.storage import repositories as repo, sqlite as sq
    from aletheia.data.collector_health import classify_collector_result

    class Exchange:
        id = "okx"
        markets = {s+":USDT":_market(s) for s in ("BTC/USDT","ETH/USDT")}

        async def load_markets(self):
            pass

        async def fetch_funding_rate_history(self, *args, **kwargs):
            return []

        async def fetch_open_interest_history(self, *args, **kwargs):
            return []

    mod = _patch_collect_env(monkeypatch, tmp_path, Exchange())
    from aletheia.config import loader
    loader.load_config().market_data.derivatives_history_hours = 24
    now = datetime.now(timezone.utc)
    for offset in (1, 2, 3):
        observed = (now - timedelta(minutes=offset)).isoformat()
        repo.insert_funding_rate_snapshot({"symbol": "BTC/USDT", "exchange": "okx", "funding_rate": 0.0001 * offset, "funding_rate_annualized": 0.1095 * offset, "observed_at": observed})
        repo.insert_open_interest_snapshot({"symbol": "BTC/USDT", "exchange": "okx", "open_interest_usd": 100, "observed_at": observed})
    current = asyncio.run(mod.DerivativesCollector().collect_history(["BTC/USDT"]))
    assert classify_collector_result(current) == "success", current
    assert current["already_current"] == 2
    assert current["funding"] == current["open_interest"] == 0
    empty = asyncio.run(mod.DerivativesCollector().collect_history(["ETH/USDT"]))
    assert classify_collector_result(empty) != "success"
    mixed = asyncio.run(mod.DerivativesCollector().collect_history(["BTC/USDT", "ETH/USDT"]))
    assert classify_collector_result(mixed) == "partial"
    assert classify_collector_result(asyncio.run(mod.DerivativesCollector().collect_history([]))) == "empty"
    unsupported = asyncio.run(mod.DerivativesCollector().collect_history(["BTC/USDT", "UNKNOWN/USDT"]))
    assert classify_collector_result(unsupported) == "partial"

    async def history(*args, **kwargs):
        return [{"timestamp": int(now.timestamp() * 1000), "openInterestValue": 100,"info":{"oiUsd":100}}]

    def fail_write(data):
        raise OSError("disk full")

    monkeypatch.setattr(Exchange, "fetch_open_interest_history", history)
    monkeypatch.setattr(repo, "insert_open_interest_snapshot", fail_write)
    failed = asyncio.run(mod.DerivativesCollector().collect_history(["BTC/USDT", "ETH/USDT"]))
    assert classify_collector_result(failed) == "partial"
    assert any("disk full" in error for error in failed["errors"])
    sq.close_db()
