"""Tests for the canonical VenueMarginEnvelope facts entry point (Issue #3729 §13.3).

Governance under test:
- CACHE-READ-ONLY: `load_venue_margin_envelope` never touches the network.
- FAIL CLOSED: absent / stale / mode-mismatched / malformed facts raise a typed
  failure with a stable reason code -- never a default MMR guess.
"""

from __future__ import annotations

import json

import pytest

from aletheia.storage.migrations import run_migrations
from aletheia.storage.sqlite import reset_db


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    monkeypatch.setattr(sq, "_db_conn", None)
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(sq, "_db_conn", conn)
    run_migrations()


def _tier_at(envelope, notional: float) -> int:
    """The tier index covering `notional`, failing loudly when none does."""
    tier = envelope.tier_for_notional(notional)
    assert tier is not None, notional
    return tier.tier


def _make_envelope(vm, *, margin_mode="isolated", fetched_at_ms=1_000_000_000_000):
    return vm.VenueMarginEnvelope(
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        margin_mode=margin_mode,
        leverage_step=None,
        tiers=(
            vm.MarginTier(
                tier=1, min_notional=0.0, max_notional=200_000.0,
                max_leverage=150.0, maintenance_margin_rate=0.004,
            ),
            vm.MarginTier(
                tier=2, min_notional=200_000.0, max_notional=1_000_000.0,
                max_leverage=100.0, maintenance_margin_rate=0.005,
            ),
            vm.MarginTier(
                tier=3, min_notional=1_000_000.0, max_notional=None,
                max_leverage=75.0, maintenance_margin_rate=0.007,
            ),
        ),
        source="ccxt:bitget",
        fetched_at_ms=fetched_at_ms,
        age_ms=0,
        facts_fingerprint="test-fingerprint",
    )


def test_seeded_fact_row_round_trips_through_load(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    envelope = _make_envelope(vm, fetched_at_ms=1_000_000_000_000)
    vm.store_venue_margin_envelope(envelope)

    loaded = vm.load_venue_margin_envelope(
        venue="bitget", symbol="BTC/USDT", market_type="perp",
        margin_mode="isolated", now=1_000_000_000_000 + 60_000,
    )

    assert loaded.venue == "bitget"
    assert loaded.tiers == envelope.tiers
    assert loaded.facts_fingerprint == "test-fingerprint"
    assert loaded.age_ms == 60_000


def test_build_store_load_round_trips_with_unchanged_fingerprint(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    tiers = (
        vm.MarginTier(tier=1, min_notional=0.0, max_notional=200_000.0,
                       max_leverage=150.0, maintenance_margin_rate=0.004),
        vm.MarginTier(tier=2, min_notional=200_000.0, max_notional=None,
                       max_leverage=100.0, maintenance_margin_rate=0.005),
    )
    built = vm.build_venue_margin_envelope(
        venue="bitget", symbol="ETH/USDT", market_type="perp", margin_mode="isolated",
        tiers=tiers, source="ccxt:bitget", fetched_at_ms=1_000_000_000_000,
    )
    vm.store_venue_margin_envelope(built)

    loaded = vm.load_venue_margin_envelope(
        venue="bitget", symbol="ETH/USDT", market_type="perp",
        margin_mode="isolated", now=1_000_000_000_000 + 5_000,
    )

    assert loaded.tiers == built.tiers
    assert loaded.facts_fingerprint == built.facts_fingerprint
    # Same facts through refresh's own construction path must fingerprint identically.
    assert built.facts_fingerprint == vm._fingerprint(
        "bitget", "ETH/USDT", "perp", "isolated", tiers
    )


def test_absent_row_is_tiers_unavailable(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    with pytest.raises(vm.VenueMarginUnavailable) as exc_info:
        vm.load_venue_margin_envelope(
            venue="bitget", symbol="BTC/USDT", market_type="perp", margin_mode="isolated",
        )
    assert exc_info.value.reason == vm.REASON_TIERS_UNAVAILABLE


def test_stale_row_is_tiers_stale(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    envelope = _make_envelope(vm, fetched_at_ms=1_000_000_000_000)
    vm.store_venue_margin_envelope(envelope)

    too_late = 1_000_000_000_000 + vm._TTL_MS + 1
    with pytest.raises(vm.VenueMarginUnavailable) as exc_info:
        vm.load_venue_margin_envelope(
            venue="bitget", symbol="BTC/USDT", market_type="perp",
            margin_mode="isolated", now=too_late,
        )
    assert exc_info.value.reason == vm.REASON_TIERS_STALE


def test_margin_mode_mismatch_is_margin_mode_unavailable(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    envelope = _make_envelope(vm, margin_mode="isolated", fetched_at_ms=1_000_000_000_000)
    vm.store_venue_margin_envelope(envelope)

    with pytest.raises(vm.VenueMarginUnavailable) as exc_info:
        vm.load_venue_margin_envelope(
            venue="bitget", symbol="BTC/USDT", market_type="perp",
            margin_mode="cross", now=1_000_000_000_000 + 1,
        )
    assert exc_info.value.reason == vm.REASON_MARGIN_MODE_UNAVAILABLE


def test_tier_for_notional_boundaries(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    envelope = _make_envelope(vm)

    assert _tier_at(envelope, 0.0) == 1
    assert _tier_at(envelope, 199_999.99) == 1
    # Upper bound of a tier is open: exactly max_notional belongs to the next tier.
    assert _tier_at(envelope, 200_000.0) == 2
    assert _tier_at(envelope, 999_999.0) == 2
    assert _tier_at(envelope, 1_000_000.0) == 3
    # Last tier's upper bound is open (no cap).
    assert _tier_at(envelope, 50_000_000.0) == 3
    # Below the first tier's floor: no tier covers it.
    assert envelope.tier_for_notional(-1.0) is None


def test_tiers_must_be_ascending_by_notional(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from pydantic import ValidationError

    from aletheia.trading import venue_margin as vm

    with pytest.raises(ValidationError):
        vm.VenueMarginEnvelope(
            venue="bitget", symbol="BTC/USDT", market_type="perp", margin_mode="isolated",
            leverage_step=None,
            tiers=(
                vm.MarginTier(tier=1, min_notional=200_000.0, max_notional=1_000_000.0,
                               max_leverage=100.0, maintenance_margin_rate=0.005),
                vm.MarginTier(tier=2, min_notional=0.0, max_notional=200_000.0,
                               max_leverage=150.0, maintenance_margin_rate=0.004),
            ),
            source="ccxt:bitget", fetched_at_ms=1, age_ms=0, facts_fingerprint="x",
        )


def test_malformed_cached_payload_fails_closed(tmp_path, monkeypatch):
    """A corrupted cache row must never fall back to a default MMR -- it fails closed."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.sqlite import execute_write
    from aletheia.trading import venue_margin as vm

    execute_write(
        "INSERT INTO settings(key, value) VALUES(?, ?)",
        (vm._settings_key("bitget", "BTC/USDT", "perp"), json.dumps({"tiers": []})),
    )

    with pytest.raises(vm.VenueMarginUnavailable) as exc_info:
        vm.load_venue_margin_envelope(
            venue="bitget", symbol="BTC/USDT", market_type="perp", margin_mode="isolated",
        )
    assert exc_info.value.reason == vm.REASON_TIERS_UNAVAILABLE


class _FakeBitgetExchange:
    def __init__(self, tiers):
        self._tiers = tiers

    async def fetch_market_leverage_tiers(self, symbol):
        return self._tiers


class _FakeBinanceusdmExchange:
    def __init__(self, tiers_by_symbol):
        self._tiers_by_symbol = tiers_by_symbol

    async def fetch_leverage_tiers(self, symbols):
        return self._tiers_by_symbol


_GOOD_BITGET_TIERS = [
    {"tier": 1, "minNotional": 0.0, "maxNotional": 200000.0,
     "maintenanceMarginRate": 0.004, "maxLeverage": 150.0},
    {"tier": 2, "minNotional": 200000.0, "maxNotional": 1000000.0,
     "maintenanceMarginRate": 0.005, "maxLeverage": 100.0},
]


async def test_refresh_persists_ccxt_tiers_for_bitget(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    exchange = _FakeBitgetExchange(_GOOD_BITGET_TIERS)
    envelope = await vm.refresh_venue_margin_envelope(
        exchange, venue="bitget", symbol="BTC/USDT", market_type="perp",
        margin_mode="isolated", now=1_000_000_000_000,
    )

    assert len(envelope.tiers) == 2
    assert envelope.tiers[0].max_leverage == 150.0
    assert envelope.tiers[0].maintenance_margin_rate == 0.004

    loaded = vm.load_venue_margin_envelope(
        venue="bitget", symbol="BTC/USDT", market_type="perp",
        margin_mode="isolated", now=1_000_000_000_000 + 1,
    )
    assert loaded.tiers == envelope.tiers


async def test_refresh_persists_ccxt_tiers_for_binanceusdm(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    exchange = _FakeBinanceusdmExchange({"BTC/USDT:USDT": _GOOD_BITGET_TIERS})
    envelope = await vm.refresh_venue_margin_envelope(
        exchange, venue="binanceusdm", symbol="BTC/USDT", market_type="perp",
        margin_mode="cross", now=1_000_000_000_000,
    )
    assert len(envelope.tiers) == 2
    assert envelope.venue == "binanceusdm"


async def test_refresh_fails_closed_on_missing_mmr_field_never_defaults(tmp_path, monkeypatch):
    """A tier missing maintenanceMarginRate must never resolve to FUTURES_MMR's 0.005 default."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    bad_tiers = [
        {"tier": 1, "minNotional": 0.0, "maxNotional": 200000.0, "maxLeverage": 150.0},
    ]
    exchange = _FakeBitgetExchange(bad_tiers)

    with pytest.raises(vm.VenueMarginUnavailable) as exc_info:
        await vm.refresh_venue_margin_envelope(
            exchange, venue="bitget", symbol="BTC/USDT", market_type="perp",
            margin_mode="isolated",
        )
    assert exc_info.value.reason == vm.REASON_TIERS_UNAVAILABLE

    # And nothing was persisted -- a failed refresh must not leave a fabricated row.
    assert vm.inspect_venue_margin_envelope(
        venue="bitget", symbol="BTC/USDT", market_type="perp"
    ) is None


async def test_refresh_fails_closed_for_unmapped_venue(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    with pytest.raises(vm.VenueMarginUnavailable) as exc_info:
        await vm.refresh_venue_margin_envelope(
            object(), venue="okx", symbol="BTC/USDT", market_type="perp", margin_mode="isolated",
        )
    assert exc_info.value.reason == vm.REASON_TIERS_UNAVAILABLE


def test_clear_helper_removes_the_fact_row(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    vm.store_venue_margin_envelope(_make_envelope(vm))
    assert vm.inspect_venue_margin_envelope(
        venue="bitget", symbol="BTC/USDT", market_type="perp"
    ) is not None

    vm.clear_venue_margin_envelope(venue="bitget", symbol="BTC/USDT", market_type="perp")
    assert vm.inspect_venue_margin_envelope(
        venue="bitget", symbol="BTC/USDT", market_type="perp"
    ) is None
