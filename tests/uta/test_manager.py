import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from aletheia.config.schema import ExchangeConfig
from aletheia.portfolio.uta.broker_protocol import AccountInfo
from aletheia.uta.manager import _shadow_differences, get_unified_state
from aletheia.uta.models import (
    AccountSnapshot,
    PositionListReport,
    QueryScope,
    ReadCompleteness,
    UTAAccountIdentity,
)
from aletheia.uta.registry import UTAAdapterConfig, UTARegistry


NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _identity(generation=7, execution_mode="okx_demo"):
    live = execution_mode.endswith("_live_limited")
    return UTAAccountIdentity(
        account_id=execution_mode,
        execution_mode=execution_mode,
        venue="okx",
        environment="live" if live else "demo",
        profile="okx_live" if live else "okx_demo",
        credential_generation=generation,
        adapter_implementation="CCXTUTAAdapter",
        adapter_version="1",
        ccxt_version="4.5.56",
    )


class FakeUTAAdapter:
    def __init__(
        self,
        identity,
        *,
        equity="0",
        position_completeness=ReadCompleteness.COMPLETE,
    ):
        self.identity = identity
        self.equity = equity
        self.position_completeness = position_completeness
        self.connected = False
        self.closed = False

    async def connect(self):
        self.connected = True

    async def close(self):
        self.closed = True

    async def get_account(self):
        return AccountSnapshot(
            identity=self.identity,
            completeness=ReadCompleteness.COMPLETE,
            scope=QueryScope(),
            received_at=NOW,
            profile_version="1",
            settlement_currency="USDT",
            equity=self.equity,
            available=self.equity,
            frozen="0",
        )

    async def get_positions(self):
        return PositionListReport(
            identity=self.identity,
            completeness=self.position_completeness,
            scope=QueryScope(),
            received_at=NOW,
            reason_code=(
                None
                if self.position_completeness is ReadCompleteness.COMPLETE
                else "private_positions_unavailable"
            ),
            profile_version="1",
        )


def _cfg(boundary):
    return type(
        "Config",
        (),
        {"exchange": ExchangeConfig(uta_read_boundaries={"okx_demo": boundary})},
    )()


def _registry(adapter, boundary):
    registry = UTARegistry()
    registry.register(
        UTAAdapterConfig(identity=adapter.identity, boundary=boundary),
        lambda _config: adapter,
    )
    return registry


@pytest.mark.asyncio
async def test_uta_v1_zero_projection_uses_no_legacy_client(monkeypatch):
    adapter = FakeUTAAdapter(_identity())
    monkeypatch.setattr(
        "aletheia.uta.manager.build_uta_registry",
        lambda _cfg: _registry(adapter, "uta_v1"),
    )
    calls = []
    monkeypatch.setattr(
        "aletheia.uta.manager._legacy_brokers",
        lambda modes=None: calls.append(modes) or {},
    )

    state = await get_unified_state(_cfg("uta_v1"))

    assert calls == [()]
    assert state.total_equity_usdt == 0.0
    assert state.total_unrealized_pnl == 0.0
    assert state.connected_brokers == ["okx_demo"]
    assert state.accounts[0]["identity"]["credential_generation"] == 7
    assert state.accounts[0]["boundary"] == "uta_v1"
    assert adapter.connected is True and adapter.closed is True


@pytest.mark.asyncio
async def test_incomplete_uta_position_view_keeps_account_but_revokes_totals(monkeypatch):
    adapter = FakeUTAAdapter(
        _identity(), position_completeness=ReadCompleteness.UNAVAILABLE
    )
    monkeypatch.setattr(
        "aletheia.uta.manager.build_uta_registry",
        lambda _cfg: _registry(adapter, "uta_v1"),
    )
    monkeypatch.setattr("aletheia.uta.manager._legacy_brokers", lambda modes=None: {})

    state = await get_unified_state(_cfg("uta_v1"))

    assert state.total_equity_usdt is None
    assert state.accounts[0]["position_completeness"] == "unavailable"
    assert state.connected_brokers == []
    assert state.errors == ["okx_demo:incomplete_read"]


@pytest.mark.asyncio
async def test_multi_account_reads_start_concurrently(monkeypatch):
    second_identity = _identity().model_copy(
        update={
            "account_id": "binance_testnet",
            "execution_mode": "binance_testnet",
            "venue": "binance",
            "environment": "testnet",
            "profile": "binance_testnet",
        }
    )
    started = 0
    both_started = asyncio.Event()

    class BlockingAdapter(FakeUTAAdapter):
        async def get_account(self):
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1.0)
            return await super().get_account()

    adapters = [BlockingAdapter(_identity()), BlockingAdapter(second_identity)]
    registry = UTARegistry()
    for adapter in adapters:
        registry.register(
            UTAAdapterConfig(identity=adapter.identity, boundary="uta_v1"),
            lambda _config, adapter=adapter: adapter,
        )
    cfg = type(
        "Config",
        (),
        {
            "exchange": ExchangeConfig(
                uta_read_boundaries={
                    "okx_demo": "uta_v1",
                    "binance_testnet": "uta_v1",
                }
            )
        },
    )()
    monkeypatch.setattr("aletheia.uta.manager.build_uta_registry", lambda _cfg: registry)
    monkeypatch.setattr("aletheia.uta.manager._legacy_brokers", lambda modes=None: {})

    state = await get_unified_state(
        cfg, execution_modes=("okx_demo", "binance_testnet")
    )

    assert started == 2
    assert state.connected_brokers == ["okx_demo", "binance_testnet"]


@pytest.mark.asyncio
async def test_default_unified_scope_excludes_configured_live_account(monkeypatch):
    demo = FakeUTAAdapter(_identity(), equity="10")
    live = FakeUTAAdapter(_identity(execution_mode="okx_live_limited"), equity="100")
    registry = UTARegistry()
    for adapter in (demo, live):
        registry.register(
            UTAAdapterConfig(identity=adapter.identity, boundary="uta_v1"),
            lambda _config, adapter=adapter: adapter,
        )
    cfg = type(
        "Config",
        (),
        {
            "exchange": ExchangeConfig(
                uta_read_boundaries={
                    "okx_demo": "uta_v1",
                    "okx_live_limited": "uta_v1",
                }
            )
        },
    )()
    monkeypatch.setattr("aletheia.uta.manager.build_uta_registry", lambda _cfg: registry)
    monkeypatch.setattr("aletheia.uta.manager._legacy_brokers", lambda modes=None: {})

    paper = await get_unified_state(cfg)
    targeted_live = await get_unified_state(
        cfg, execution_modes=("okx_live_limited",)
    )

    assert paper.total_equity_usdt == 10.0
    assert paper.connected_brokers == ["okx_demo"]
    assert [row["identity"]["execution_mode"] for row in paper.accounts] == [
        "okx_demo"
    ]
    assert targeted_live.total_equity_usdt == 100.0
    assert targeted_live.connected_brokers == ["okx_live_limited"]


@pytest.mark.asyncio
async def test_default_unified_scope_ignores_live_legacy_boundary(monkeypatch):
    demo = FakeUTAAdapter(_identity(), equity="10")
    live = FakeUTAAdapter(_identity(execution_mode="okx_live_limited"), equity="100")
    registry = UTARegistry()
    registry.register(
        UTAAdapterConfig(identity=demo.identity, boundary="uta_v1"),
        lambda _config: demo,
    )
    registry.register(
        UTAAdapterConfig(identity=live.identity, boundary="legacy"),
        lambda _config: live,
    )
    cfg = type(
        "Config",
        (),
        {
            "exchange": ExchangeConfig(
                uta_read_boundaries={
                    "okx_demo": "uta_v1",
                    "okx_live_limited": "legacy",
                }
            )
        },
    )()
    calls = []
    monkeypatch.setattr("aletheia.uta.manager.build_uta_registry", lambda _cfg: registry)
    monkeypatch.setattr(
        "aletheia.uta.manager._legacy_brokers",
        lambda modes=None: calls.append(modes) or {},
    )

    state = await get_unified_state(cfg)

    assert state.total_equity_usdt == 10.0
    assert state.errors == []
    assert state.connected_brokers == ["okx_demo"]
    assert calls == [()]
    assert live.connected is False


@pytest.mark.asyncio
async def test_explicit_missing_unified_account_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "aletheia.uta.manager.build_uta_registry", lambda _cfg: UTARegistry()
    )
    monkeypatch.setattr("aletheia.uta.manager._legacy_brokers", lambda modes=None: {})

    state = await get_unified_state(
        _cfg("legacy"), execution_modes=("okx_live_limited",)
    )

    assert state.total_equity_usdt is None
    assert state.connected_brokers == []
    assert state.errors == ["okx_live_limited:account_not_configured"]


class FakeLegacyBroker:
    broker_id = "okx_demo"
    display_name = "OKX Demo"

    async def get_account(self):
        return AccountInfo(100.0, 80.0, 20.0, 0.0)

    async def get_positions(self):
        return []

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_shadow_read_never_changes_legacy_authority(monkeypatch):
    adapter = FakeUTAAdapter(_identity())

    async def different_account():
        account = await FakeUTAAdapter.get_account(adapter)
        return account.model_copy(
            update={"equity": account.equity + 1, "available": 80, "unrealized_pnl": 0}
        )

    adapter.get_account = different_account
    monkeypatch.setattr(
        "aletheia.uta.manager.build_uta_registry",
        lambda _cfg: _registry(adapter, "shadow_read"),
    )
    monkeypatch.setattr(
        "aletheia.uta.manager._legacy_brokers",
        lambda modes=None: {"okx_demo": FakeLegacyBroker()},
    )

    state = await get_unified_state(_cfg("shadow_read"))

    assert state.total_equity_usdt == 100.0
    assert state.accounts[0]["identity"]["adapter_implementation"] == "FakeLegacyBroker"
    assert state.accounts[0]["identity"]["adapter_version"] == "legacy"
    assert state.accounts[0]["shadow_differences"] == ["equity"]
    assert state.shadow_errors == []


def test_shadow_read_compares_exact_position_facts_not_only_count():
    account = SimpleNamespace(
        equity_usdt=100.0,
        available_usdt=80.0,
        unrealized_pnl=0.0,
    )
    uta_account = SimpleNamespace(
        equity=Decimal("100"),
        available=Decimal("80"),
        unrealized_pnl=Decimal("0"),
    )
    legacy_position = SimpleNamespace(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        side="long",
        quantity=1,
        entry_price=60_000,
        mark_price=60_100,
        leverage=2,
        unrealized_pnl=100,
    )
    uta_position = SimpleNamespace(
        instrument=SimpleNamespace(
            canonical_symbol="ETH/USDT", market_type="usdt_futures"
        ),
        side="long",
        quantity=Decimal("1"),
        entry_price=Decimal("60000"),
        mark_price=Decimal("60100"),
        leverage=Decimal("2"),
        unrealized_pnl=Decimal("100"),
    )

    differences = _shadow_differences(
        account,
        [legacy_position],
        uta_account,
        SimpleNamespace(positions=[uta_position]),
    )

    assert "position_count" not in differences
    assert "positions" in differences
