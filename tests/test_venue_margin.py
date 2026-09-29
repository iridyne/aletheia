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


# --- Demand-driven maintenance (Issue #3741) ---------------------------------


class _RecordingBitgetExchange:
    """Bitget tier fake that records which instruments were actually fetched."""

    def __init__(self, tiers, *, failing=()):
        self._tiers = tiers
        self._failing = {str(symbol) for symbol in failing}
        self.fetched: list[str] = []

    async def fetch_market_leverage_tiers(self, symbol):
        self.fetched.append(symbol)
        if symbol in self._failing:
            raise RuntimeError("tier api unavailable")
        return self._tiers


async def test_demand_refresh_fetches_missing_and_skips_fresh(tmp_path, monkeypatch):
    """A steady state costs nothing; only missing/stale instruments are fetched."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    now = 1_000_000_000_000
    vm.store_venue_margin_envelope(_make_envelope(vm, fetched_at_ms=now))

    exchange = _RecordingBitgetExchange(_GOOD_BITGET_TIERS)
    result = await vm.refresh_venue_margin_demand(
        execution_mode="bitget_demo",
        pairs=[("BTC/USDT", "perp"), ("ETH/USDT", "perp")],
        exchange=exchange,
        now=now + 1_000,
    )

    assert exchange.fetched == ["ETH/USDT:USDT"]  # ccxt perp form, missing one only
    assert (result["status"], result["venue"]) == ("ok", "bitget")
    assert (result["considered"], result["refreshed"], result["fresh"]) == (2, 1, 1)
    assert result["unavailable"] == []
    assert vm.inspect_venue_margin_envelope(
        venue="bitget", symbol="ETH/USDT", market_type="perp"
    ) is not None


async def test_demand_refresh_refetches_a_stale_row(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    stale_at = 1_000_000_000_000
    vm.store_venue_margin_envelope(_make_envelope(vm, fetched_at_ms=stale_at))

    exchange = _RecordingBitgetExchange(_GOOD_BITGET_TIERS)
    result = await vm.refresh_venue_margin_demand(
        execution_mode="bitget_demo",
        pairs=[("BTC/USDT", "perp")],
        exchange=exchange,
        now=stale_at + vm._TTL_MS + 1,
    )

    assert exchange.fetched == ["BTC/USDT:USDT"]
    assert (result["refreshed"], result["fresh"]) == (1, 0)


async def test_demand_refresh_isolates_one_instrument_failure(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    exchange = _RecordingBitgetExchange(_GOOD_BITGET_TIERS, failing=("ETH/USDT:USDT",))
    result = await vm.refresh_venue_margin_demand(
        execution_mode="bitget_demo",
        pairs=[("ETH/USDT", "perp"), ("SOL/USDT", "perp")],
        exchange=exchange,
        now=1_000_000_000_000,
    )

    assert result["refreshed"] == 1
    assert result["unavailable"] == [
        {"symbol": "ETH/USDT", "market_type": "perp", "reason": vm.REASON_TIERS_UNAVAILABLE}
    ]
    assert vm.inspect_venue_margin_envelope(
        venue="bitget", symbol="SOL/USDT", market_type="perp"
    ) is not None
    assert vm.inspect_venue_margin_envelope(
        venue="bitget", symbol="ETH/USDT", market_type="perp"
    ) is None


async def test_demand_refresh_uses_the_routes_submission_margin_mode(tmp_path, monkeypatch):
    """The cached mode is not part of the key, so a row in another mode is not usable.

    The Compiler asks for the route's submission mode; a refresh that wrote any
    other mode would leave a row that still fails closed with
    REASON_MARGIN_MODE_UNAVAILABLE.
    """
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    now = 1_000_000_000_000
    vm.store_venue_margin_envelope(_make_envelope(vm, margin_mode="cross", fetched_at_ms=now))

    exchange = _RecordingBitgetExchange(_GOOD_BITGET_TIERS)
    result = await vm.refresh_venue_margin_demand(
        execution_mode="bitget_demo",  # submission mode for this route is isolated
        pairs=[("BTC/USDT", "perp")],
        exchange=exchange,
        now=now + 1_000,
    )

    assert exchange.fetched == ["BTC/USDT:USDT"], "a cross row is not fresh for an isolated order"
    assert (result["refreshed"], result["fresh"], result["margin_mode"]) == (1, 0, "isolated")
    stored = vm.load_venue_margin_envelope(
        venue="bitget", symbol="BTC/USDT", market_type="perp",
        margin_mode="isolated", now=now + 2_000,
    )
    assert stored.margin_mode == "isolated"


async def test_demand_refresh_is_not_applicable_without_a_qualified_margin_mode(tmp_path, monkeypatch):
    """A route with no submission mode cannot be financed, so nothing is fetched for it."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    exchange = _RecordingBitgetExchange(_GOOD_BITGET_TIERS)
    result = await vm.refresh_venue_margin_demand(
        execution_mode="binance_testnet",  # venue registered, no submission margin mode
        pairs=[("BTC/USDT", "perp")],
        exchange=exchange,
    )

    assert result["status"] == "not_applicable"
    assert result["reason"] == "margin_mode_unavailable"
    assert exchange.fetched == []


async def test_demand_refresh_is_not_applicable_without_a_registered_venue(tmp_path, monkeypatch):
    """An internal/paper route holds no exchange position, so there is nothing to fetch."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    exchange = _RecordingBitgetExchange(_GOOD_BITGET_TIERS)
    result = await vm.refresh_venue_margin_demand(
        execution_mode="paper_internal",
        pairs=[("BTC/USDT", "perp")],
        exchange=exchange,
    )

    assert result["status"] == "not_applicable"
    assert exchange.fetched == []


async def test_demand_refresh_bounds_one_tick_and_ignores_spot(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading import venue_margin as vm

    exchange = _RecordingBitgetExchange(_GOOD_BITGET_TIERS)
    result = await vm.refresh_venue_margin_demand(
        execution_mode="bitget_demo",
        pairs=[
            ("AAA/USDT", "perp"),
            ("BBB/USDT", "perp"),
            ("CCC/USDT", "perp"),
            ("SPOT/USDT", "spot"),
        ],
        max_symbols=2,
        exchange=exchange,
        now=1_000_000_000_000,
    )

    assert len(exchange.fetched) == 2
    assert (result["considered"], result["skipped_over_cap"]) == (2, 1)
    assert result["skipped_market_type"] == 1


def test_planning_demand_is_exactly_the_planners_claimable_queue(tmp_path, monkeypatch):
    """The maintained set cannot drift from what the Planner will claim."""
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env as setup_entry_env
    from tests.test_entry_planner import _typed_candidate

    setup_entry_env(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.universe.service import _ensure_planning_generations

    now = datetime.now(timezone.utc)
    candidate = _typed_candidate(
        mechanism="continuation", family="momentum", source_at=now.isoformat()
    )
    _ensure_planning_generations(
        [candidate], soft_lease_sec=900, max_attention_age_sec=3600, now=now
    )

    demand = repo.list_candidate_planning_demand(now=now)
    assert demand == [(candidate.symbol, "perp")]
    assert len(demand) == repo.count_pending_candidate_planning(now=now) == 1

    # A stale lease drops it from the Planner's queue, so it must leave demand too.
    assert repo.list_candidate_planning_demand(now=now + timedelta(hours=2)) == []
    assert repo.count_pending_candidate_planning(now=now + timedelta(hours=2)) == 0

    # An execution-ineligible Candidate is not financing demand either.
    repo.execute_write(
        "UPDATE universe_members SET execution_eligible=0 WHERE id=?", (candidate.id,)
    )
    assert repo.list_candidate_planning_demand(now=now) == []
    assert repo.count_pending_candidate_planning(now=now) == 0


def test_scheduler_job_maintains_facts_for_the_live_demand(monkeypatch):
    """The job reads the Planner's demand and refreshes it on the executing venue."""
    import asyncio

    from aletheia.agent_runtime import scheduler
    from aletheia.storage import repositories as repo
    from aletheia.trading import venue_margin as vm

    recorded: dict = {}

    class _Exchange:
        paper_execution_mode = "bitget_demo"

    class _Live:
        state = "live_disabled"

    class _Agent:
        permission_mode = "Full"

    class _Cfg:
        exchange = _Exchange()
        live = _Live()
        agent = _Agent()

    async def fake_refresh(**kwargs):
        recorded.update(kwargs)
        return {"venue": "bitget", "considered": 2, "refreshed": 1, "fresh": 1, "unavailable": []}

    monkeypatch.setattr(scheduler, "load_config", lambda *a, **k: _Cfg())
    monkeypatch.setattr(repo, "list_candidate_planning_demand", lambda: [("BTC/USDT", "perp")])
    monkeypatch.setattr(vm, "refresh_venue_margin_demand", fake_refresh)
    monkeypatch.setattr(scheduler, "_run_async_with_timeout", lambda job_id, awaitable: asyncio.run(awaitable))
    failures: list = []
    monkeypatch.setattr(scheduler, "_record_job_failure", lambda *args: failures.append(args))

    scheduler._run_venue_margin_facts()

    assert failures == []
    assert recorded == {"execution_mode": "bitget_demo", "pairs": [("BTC/USDT", "perp")]}


def test_scheduler_job_skips_when_no_entry_route_is_configured(monkeypatch):
    """No external entry route means no position to hold, so no fetch is attempted."""
    from aletheia.agent_runtime import scheduler
    from aletheia.storage import repositories as repo
    from aletheia.trading import venue_margin as vm

    class _Exchange:
        paper_execution_mode = "paper_internal"

    class _Live:
        state = "live_disabled"

    class _Agent:
        permission_mode = "Full"

    class _Cfg:
        exchange = _Exchange()
        live = _Live()
        agent = _Agent()

    def unexpected(*args, **kwargs):
        pytest.fail("no fact refresh may run without an external entry route")

    monkeypatch.setattr(scheduler, "load_config", lambda *a, **k: _Cfg())
    monkeypatch.setattr(repo, "list_candidate_planning_demand", lambda: [("BTC/USDT", "perp")])
    monkeypatch.setattr(vm, "refresh_venue_margin_demand", unexpected)

    scheduler._run_venue_margin_facts()
