import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from aletheia.config.schema import ExchangeConfig
from aletheia.uta.manager import (
    DEFAULT_UNIFIED_EXECUTION_MODES,
    get_unified_state,
)
from aletheia.uta.models import (
    AccountSnapshot,
    PositionListReport,
    QueryScope,
    ReadCompleteness,
    UTAAccountIdentity,
)
from aletheia.uta.registry import UTAAdapterConfig, UTARegistry


NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["valid", "absent", "symbol", "market", "order_id", "query", "report"])
async def test_exact_order_read_validates_normalized_evidence(monkeypatch, case):
    from aletheia.uta.compat import scope_from_local
    from aletheia.uta.manager import query_uta
    from aletheia.uta.models import OrderLifecycle, OrderQuery, OrderQueryReport, OrderStatusReport

    adapter = FakeUTAAdapter(_identity())
    scope = scope_from_local("BTC/USDT", "usdt_futures")
    query = OrderQuery(scope=scope, venue_order_id="order-1", client_order_id="local-id")
    found = OrderStatusReport(
        identity=adapter.identity,
        instrument=scope_from_local(
            "ETH/USDT" if case == "symbol" else "BTC/USDT",
            "spot" if case == "market" else "usdt_futures",
        ).instrument,
        venue_order_id="other" if case == "order_id" else "order-1",
        client_order_id="venue-encoded-local-id",
        side="buy", order_type="limit", original_quantity="1", filled_quantity="0",
        state=OrderLifecycle.OPEN, received_at=NOW,
    )
    report = OrderQueryReport(
        identity=adapter.identity, scope=scope, completeness=ReadCompleteness.COMPLETE,
        received_at=NOW, profile_version="1", found=None if case == "absent" else found,
    )

    async def get_order(_query):
        return SimpleNamespace(identity=adapter.identity) if case == "report" else report

    adapter.get_order = get_order
    monkeypatch.setattr("aletheia.uta.manager.create_uta_adapter", lambda *_args: adapter)
    if case == "query":
        query = OrderQuery(scope=scope)
    if case in {"valid", "absent"}:
        assert await query_uta("okx_demo", "get_order", query, expected_account=adapter.identity) is report
    else:
        with pytest.raises(ValueError):
            await query_uta("okx_demo", "get_order", query, expected_account=adapter.identity)
    assert adapter.closed is True
    assert adapter.connected is (case != "query")


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


def _cfg():
    return SimpleNamespace(exchange=ExchangeConfig())


def _registry(adapter):
    registry = UTARegistry()
    registry.register(
        UTAAdapterConfig(identity=adapter.identity),
        lambda _config: adapter,
    )
    return registry


@pytest.mark.asyncio
async def test_uta_v1_zero_projection_uses_no_legacy_client(monkeypatch):
    adapter = FakeUTAAdapter(_identity())
    monkeypatch.setattr(
        "aletheia.uta.manager.build_uta_registry",
        lambda _cfg, **_kwargs: _registry(adapter),
    )

    state = await get_unified_state(_cfg())

    assert state.total_equity_usdt == 0.0
    assert state.total_unrealized_pnl == 0.0
    assert state.connected_brokers == ["okx_demo"]
    assert state.accounts[0]["identity"]["credential_generation"] == 7
    assert adapter.connected is True and adapter.closed is True


@pytest.mark.asyncio
async def test_incomplete_uta_position_view_keeps_account_but_revokes_totals(monkeypatch):
    adapter = FakeUTAAdapter(
        _identity(), position_completeness=ReadCompleteness.UNAVAILABLE
    )
    monkeypatch.setattr(
        "aletheia.uta.manager.build_uta_registry",
        lambda _cfg, **_kwargs: _registry(adapter),
    )

    state = await get_unified_state(_cfg())

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
            UTAAdapterConfig(identity=adapter.identity),
            lambda _config, adapter=adapter: adapter,
        )
    cfg = _cfg()
    monkeypatch.setattr("aletheia.uta.manager.build_uta_registry", lambda _cfg, **_kwargs: registry)

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
            UTAAdapterConfig(identity=adapter.identity),
            lambda _config, adapter=adapter: adapter,
        )
    cfg = _cfg()
    monkeypatch.setattr("aletheia.uta.manager.build_uta_registry", lambda _cfg, **_kwargs: registry)

    paper = await get_unified_state(cfg)
    targeted_live = await get_unified_state(
        cfg, execution_modes=("okx_live_limited",)
    )

    assert paper.total_equity_usdt == 10.0
    assert paper.connected_brokers == ["okx_demo"]
    assert [row["identity"]["execution_mode"] for row in paper.accounts] == [
        "okx_demo"
    ]
    assert paper.execution_modes == DEFAULT_UNIFIED_EXECUTION_MODES
    assert targeted_live.total_equity_usdt == 100.0
    assert targeted_live.connected_brokers == ["okx_live_limited"]
    assert targeted_live.execution_modes == ("okx_live_limited",)



@pytest.mark.asyncio
async def test_explicit_missing_unified_account_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "aletheia.uta.manager.build_uta_registry", lambda _cfg, **_kwargs: UTARegistry()
    )

    state = await get_unified_state(
        _cfg(), execution_modes=("okx_live_limited",)
    )

    assert state.total_equity_usdt is None
    assert state.connected_brokers == []
    assert state.errors == ["okx_live_limited:account_not_configured"]


@pytest.mark.asyncio
async def test_account_generation_change_prevents_private_query(monkeypatch):
    from aletheia.uta.manager import query_uta

    adapter = FakeUTAAdapter(_identity(generation=8))
    monkeypatch.setattr("aletheia.uta.manager.create_uta_adapter", lambda *_args: adapter)
    with pytest.raises(ValueError, match="uta_account_identity_mismatch"):
        await query_uta("okx_demo", "get_account", expected_account=_identity(generation=7))
    assert adapter.connected is False
    assert adapter.closed is True


@pytest.mark.asyncio
async def test_failed_position_read_keeps_other_account_but_revokes_aggregate(monkeypatch):
    failed = FakeUTAAdapter(_identity(), equity="5000")
    healthy = FakeUTAAdapter(_identity(execution_mode="binance_testnet"), equity="3000")
    async def unavailable():
        raise TimeoutError("private positions unavailable")
    failed.get_positions = unavailable
    registry = _registry(failed)
    registry.register(UTAAdapterConfig(identity=healthy.identity), lambda _config: healthy)
    monkeypatch.setattr("aletheia.uta.manager.build_uta_registry", lambda _cfg, **_kwargs: registry)
    state = await get_unified_state(_cfg())
    assert state.total_equity_usdt is None
    assert state.connected_brokers == ["binance_testnet"]
    assert state.accounts[0]["equity_usdt"] == 3000
    assert state.errors == ["okx_demo:account_read_unavailable"]


def test_targeted_adapter_never_resolves_unrelated_credentials(monkeypatch):
    from aletheia.exchanges import composition

    def scoped_secret(key):
        assert key.startswith("bitget.demo."), "unrelated credential scope accessed"
        return "synthetic-test-value"

    monkeypatch.setattr(composition, "get_secret", scoped_secret)
    monkeypatch.setattr(composition, "get_credential_generation", lambda _scope: 0)
    adapter = composition.build_uta_registry(_cfg(), execution_modes=("bitget_demo",)).create("bitget_demo")
    assert adapter.identity.execution_mode == "bitget_demo"
