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


@pytest.mark.parametrize("accounts", [None, (), ("unknown_demo",)])
def test_profile_accounts_fail_closed(monkeypatch, accounts):
    import aletheia.uta.manager as manager

    monkeypatch.setattr(manager, "_profile_accounts", None if accounts is None else lambda *_: accounts)
    with pytest.raises(RuntimeError if accounts is None else ValueError):
        manager.profile_accounts("bitget_profile", "uta_v3")


@pytest.mark.anyio
async def test_native_protection_reader_owns_wire_symbol_conversion(monkeypatch):
    from extensions.exchanges.bitget import extension

    identity = _identity(execution_mode="bitget_demo").model_copy(update={"venue": "bitget", "profile": "classic_v2"})
    calls = []
    class Client:
        def __init__(self, **kwargs):
            pass
        async def get_order_fill_reports(self, **kwargs):
            calls.append(kwargs)
            return []
    monkeypatch.setattr(extension.bitget_client, "BitgetClient", Client)
    reader = extension._BitgetNativeProtectionReader(identity)
    assert await reader.get_order_fill_reports(
        order_id="exact-order", symbol="BTC/USDT", market_type="usdt_futures", identity=identity,
    ) == ()
    assert calls == [{"order_id": "exact-order", "symbol": "BTCUSDT",
                      "market_type": "usdt_futures", "identity": identity}]


@pytest.mark.parametrize("mode,scope", [("bitget_demo", "bitget.demo"), ("okx_demo", "okx.demo")])
def test_credential_configuration_reads_only_selected_account(monkeypatch, mode, scope):
    from extensions.exchanges import composition
    from aletheia.config.schema import AletheiaConfig

    reads = []
    def read(key):
        reads.append(key)
        return "synthetic-fixture" if key.endswith(".api_key") else None
    monkeypatch.setattr(composition, "get_secret", read)
    assert composition.credential_configuration(AletheiaConfig(), mode) == (1, 3)
    assert reads == [f"{scope}.{field}" for field in ("api_key", "api_secret", "passphrase")]


@pytest.mark.parametrize("counts", [(4, 3), (-1, 3), (0, 0), (True, 3)])
def test_credential_configuration_rejects_invalid_adapter_counts(monkeypatch, counts):
    from aletheia.uta import manager

    monkeypatch.setattr(manager, "_credential_configuration", lambda *args: counts)
    with pytest.raises(ValueError, match="invalid_credential_configuration_counts"):
        manager.credential_configuration("fixture-account")


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
@pytest.mark.parametrize("case", [
    "match", "not_found", "wrong_order", "missing_order", "wrong_symbol",
    "wrong_market", "untyped", "client_id_encoded",
])
async def test_exact_order_read_rejects_foreign_evidence_and_always_closes(monkeypatch, case):
    from aletheia.uta.compat import instrument_from_local
    from aletheia.uta.manager import query_uta
    from aletheia.uta.models import OrderQuery, OrderQueryReport, OrderStatusReport

    identity = _identity()
    instrument = instrument_from_local("BTC/USDT", "usdt_futures")
    request = OrderQuery(scope=QueryScope(instrument=instrument), venue_order_id="expected-order")
    if case == "client_id_encoded":
        request = OrderQuery(scope=request.scope, client_order_id="close-position-1")
    returned_instrument = instrument_from_local(
        "ETH/USDT" if case == "wrong_symbol" else "BTC/USDT",
        "spot" if case == "wrong_market" else "usdt_futures",
    )
    found = OrderStatusReport(
        identity=identity, instrument=returned_instrument,
        client_order_id="closeposition1" if case == "client_id_encoded" else None,
        venue_order_id=None if case == "missing_order" else "other-order" if case == "wrong_order" else "expected-order",
        side="buy", order_type="market", original_quantity="1", filled_quantity="1",
        state="filled", received_at=NOW,
    )
    report = OrderQueryReport(
        identity=identity, completeness="complete", scope=request.scope,
        received_at=NOW, profile_version="1", found=None if case == "not_found" else found,
    )
    if case == "untyped":
        report = SimpleNamespace(identity=identity, found=found)
    adapter = FakeUTAAdapter(identity)
    calls = []

    async def get_order(query):
        calls.append(query)
        return report

    adapter.get_order = get_order
    monkeypatch.setattr("aletheia.uta.manager.create_uta_adapter", lambda *_args: adapter)
    if case in {"match", "not_found", "client_id_encoded"}:
        assert await query_uta("okx_demo", "get_order", request, expected_account=identity) is report
    else:
        with pytest.raises(ValueError, match="uta_exact_order_"):
            await query_uta("okx_demo", "get_order", request, expected_account=identity)
    assert calls == [request]
    assert adapter.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_query", [False, True])
async def test_exact_order_without_selector_never_connects(monkeypatch, missing_query):
    from aletheia.uta.manager import query_uta
    from aletheia.uta.models import OrderQuery

    adapter = FakeUTAAdapter(_identity())
    monkeypatch.setattr("aletheia.uta.manager.create_uta_adapter", lambda *_args: adapter)
    with pytest.raises(ValueError):
        await query_uta(
            "okx_demo", "get_order", None if missing_query else OrderQuery(),
            expected_account=adapter.identity,
        )
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
    from extensions.exchanges import composition as manager

    def scoped_secret(key):
        assert key.startswith("bitget.demo."), "unrelated credential scope accessed"
        return "synthetic-test-value"

    monkeypatch.setattr(manager, "get_secret", scoped_secret)
    monkeypatch.setattr(manager, "get_credential_generation", lambda _scope: 0)
    adapter = manager.build_uta_registry(_cfg(), execution_modes=("bitget_demo",)).create("bitget_demo")
    assert adapter.identity.execution_mode == "bitget_demo"
