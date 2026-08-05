"""Tests for UTA (Unified Trading Account) layer.

Covers:
- IBroker Protocol structural check
- Registry create/register/list
- UTAManager aggregation with mock adapters
- UnifiedState totals (equity, available, upnl)
- Error isolation: one broker failing doesn't drop the other
- get_unified_state_sync() smoke test
"""

import asyncio
import pytest

from aletheia.portfolio.uta.broker_protocol import AccountInfo, BrokerPosition, IBroker
from aletheia.portfolio.uta.registry import (
    register_broker, create_broker, create_all_configured, available_brokers,
    _BROKER_REGISTRY,
)
from aletheia.portfolio.uta.manager import get_unified_state, UnifiedState


# ---------------------------------------------------------------------------
# Minimal mock broker for testing
# ---------------------------------------------------------------------------

class MockAdapter:
    def __init__(self, broker_id: str, display_name: str, equity: float = 1000.0,
                 available: float = 800.0, frozen: float = 200.0, upnl: float = 50.0,
                 positions: list | None = None, fail: bool = False):
        self._broker_id = broker_id
        self._display_name = display_name
        self._equity = equity
        self._available = available
        self._frozen = frozen
        self._upnl = upnl
        self._positions = positions or []
        self._fail = fail
        self.closed = False

    @property
    def broker_id(self) -> str:
        return self._broker_id

    @property
    def display_name(self) -> str:
        return self._display_name

    async def get_account(self) -> AccountInfo:
        if self._fail:
            raise RuntimeError("mock connection error")
        return AccountInfo(
            equity_usdt=self._equity,
            available_usdt=self._available,
            frozen_usdt=self._frozen,
            unrealized_pnl=self._upnl,
            asset_breakdown=[{"coin": "USDT", "usdt_value": self._equity, "pct": 100.0}],
        )

    async def get_positions(self) -> list[BrokerPosition]:
        if self._fail:
            raise RuntimeError("mock connection error")
        return self._positions

    async def test_connection(self) -> bool:
        return not self._fail

    async def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

def test_mock_adapter_satisfies_ibroker():
    adapter = MockAdapter("test", "Test")
    assert isinstance(adapter, IBroker)


def test_account_info_fields():
    a = AccountInfo(equity_usdt=1000, available_usdt=800, frozen_usdt=200, unrealized_pnl=50)
    assert a.equity_usdt == 1000
    assert a.asset_breakdown == []


def test_broker_position_defaults():
    p = BrokerPosition(
        symbol="BTC/USDT", side="long", quantity=0.1,
        entry_price=60000, unrealized_pnl=100,
    )
    assert p.leverage == 1
    assert p.mark_price == 0.0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_register_and_create(monkeypatch):
    monkeypatch.setitem(_BROKER_REGISTRY, "test_broker", lambda: MockAdapter("test_broker", "Test"))
    b = create_broker("test_broker")
    assert b is not None
    assert b.broker_id == "test_broker"


def test_create_broker_unknown_returns_none():
    b = create_broker("__nonexistent__")
    assert b is None


def test_create_broker_failing_constructor_returns_none(monkeypatch):
    def _fail():
        raise RuntimeError("creds missing")
    monkeypatch.setitem(_BROKER_REGISTRY, "_fail_broker", _fail)
    assert create_broker("_fail_broker") is None


# ---------------------------------------------------------------------------
# UTAManager aggregation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unified_state_sums_two_brokers(monkeypatch):
    brokers = [
        MockAdapter("bitget_demo", "Bitget Demo", equity=5000, available=4000, upnl=-100),
        MockAdapter("binance_testnet", "Binance Testnet", equity=3000, available=2500, upnl=50),
    ]
    monkeypatch.setattr(
        "aletheia.portfolio.uta.registry.create_all_configured",
        lambda: brokers,
    )

    state: UnifiedState = await get_unified_state()

    assert state.total_equity_usdt == 8000.0
    assert state.total_available_usdt == 6500.0
    assert state.total_unrealized_pnl == -50.0
    assert len(state.accounts) == 2
    assert state.errors == []


@pytest.mark.asyncio
async def test_unified_state_one_broker_fails(monkeypatch):
    brokers = [
        MockAdapter("bitget_demo", "Bitget Demo", equity=5000, available=4000, upnl=100),
        MockAdapter("binance_testnet", "Binance Testnet", fail=True),
    ]
    monkeypatch.setattr(
        "aletheia.portfolio.uta.registry.create_all_configured",
        lambda: brokers,
    )

    state: UnifiedState = await get_unified_state()

    # Healthy broker still contributes
    assert state.total_equity_usdt == 5000.0
    assert len(state.accounts) == 1
    assert len(state.errors) == 1
    assert "binance_testnet" in state.errors[0]


@pytest.mark.asyncio
async def test_unified_state_no_brokers(monkeypatch):
    monkeypatch.setattr(
        "aletheia.portfolio.uta.registry.create_all_configured",
        lambda: [],
    )
    state: UnifiedState = await get_unified_state()
    assert state.total_equity_usdt == 0.0
    assert state.accounts == []
    assert state.errors == []


@pytest.mark.asyncio
async def test_broker_close_called_after_query(monkeypatch):
    adapter = MockAdapter("test", "Test", equity=1000)
    monkeypatch.setattr(
        "aletheia.portfolio.uta.registry.create_all_configured",
        lambda: [adapter],
    )
    await get_unified_state()
    assert adapter.closed is True


@pytest.mark.asyncio
async def test_positions_merged_across_brokers(monkeypatch):
    pos_a = BrokerPosition("BTC/USDT", "long", 0.1, 60000, 100)
    pos_b = BrokerPosition("ETH/USDT", "short", 1.0, 3000, -20)
    brokers = [
        MockAdapter("bitget_demo", "Bitget", equity=5000, positions=[pos_a]),
        MockAdapter("binance_testnet", "Binance", equity=3000, positions=[pos_b]),
    ]
    monkeypatch.setattr(
        "aletheia.portfolio.uta.registry.create_all_configured",
        lambda: brokers,
    )
    state = await get_unified_state()
    assert len(state.positions) == 2
    broker_ids = {p["broker_id"] for p in state.positions}
    assert broker_ids == {"bitget_demo", "binance_testnet"}


@pytest.mark.asyncio
async def test_unified_state_preserves_broker_open_time(monkeypatch):
    opened_at_ms = 1784044800000
    adapter = MockAdapter(
        "bitget_demo", "Bitget",
        positions=[BrokerPosition("BTC/USDT", "long", 0.1, 60000, 100,
                                   opened_at_ms=opened_at_ms)],
    )
    monkeypatch.setattr(
        "aletheia.portfolio.uta.registry.create_all_configured",
        lambda: [adapter],
    )

    state = await get_unified_state()

    assert state.positions[0]["cTime"] == opened_at_ms


# ---------------------------------------------------------------------------
# Sync wrapper
# ---------------------------------------------------------------------------

def test_get_unified_state_sync(monkeypatch):
    adapter = MockAdapter("test_sync", "Test Sync", equity=999)
    monkeypatch.setattr(
        "aletheia.portfolio.uta.registry.create_all_configured",
        lambda: [adapter],
    )
    from aletheia.portfolio.uta.manager import get_unified_state_sync
    state = get_unified_state_sync()
    assert state.total_equity_usdt == 999.0


# ---------------------------------------------------------------------------
# Fail-closed: positions query failure must not produce authoritative empty view
# ---------------------------------------------------------------------------

class _PartialFailAdapter(MockAdapter):
    """Account succeeds; positions raises — simulates transport/auth failure."""
    async def get_positions(self) -> list[BrokerPosition]:
        raise RuntimeError("positions query failed")


@pytest.mark.asyncio
async def test_positions_query_failure_removes_broker_from_connected(monkeypatch):
    """A broker whose positions query fails must NOT appear in connected_brokers.

    Positions failure must propagate as an error so the reconciler cannot
    treat an empty position list as authoritative evidence of absence.
    """
    partial = _PartialFailAdapter("bitget_demo", "Bitget Demo", equity=5000)
    healthy = MockAdapter(
        "binance_testnet", "Binance",
        positions=[BrokerPosition("ETH/USDT", "long", 1.0, 3000, 0)],
    )
    monkeypatch.setattr(
        "aletheia.portfolio.uta.registry.create_all_configured",
        lambda: [partial, healthy],
    )

    state = await get_unified_state()

    assert "bitget_demo" not in state.connected_brokers
    assert len(state.errors) == 1
    assert "bitget_demo" in state.errors[0]
    assert not any(p["broker_id"] == "bitget_demo" for p in state.positions)
    assert "binance_testnet" in state.connected_brokers
    assert len(state.positions) == 1


@pytest.mark.asyncio
async def test_successful_empty_positions_remains_complete_view(monkeypatch):
    """A broker returning an empty positions list (no open positions) is still connected."""
    adapter = MockAdapter("bitget_demo", "Bitget Demo", equity=5000, positions=[])
    monkeypatch.setattr(
        "aletheia.portfolio.uta.registry.create_all_configured",
        lambda: [adapter],
    )

    state = await get_unified_state()

    assert "bitget_demo" in state.connected_brokers
    assert state.errors == []
    assert state.positions == []
