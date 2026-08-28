"""Multi-account UTA read coordination and compatibility projection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from importlib.metadata import version

from aletheia.config.loader import load_config
from aletheia.config.secrets import get_credential_generation, get_secret
from aletheia.portfolio.disappearance_confirmation import logical_account_fingerprint
from aletheia.uta.ccxt_adapter import CCXTUTAAdapter
from aletheia.uta.models import ReadCompleteness, UTAAccountIdentity
from aletheia.uta.profiles import (
    BinanceLiveProfile,
    BinanceTestnetProfile,
    BitgetClassicV2LiveProfile,
    BitgetClassicV2Profile,
    BitgetUTAV3LiveProfile,
    BitgetUTAV3Profile,
    OKXDemoProfile,
    OKXLiveProfile,
)
from aletheia.uta.registry import UTAAdapterConfig, UTARegistry


DEFAULT_UNIFIED_EXECUTION_MODES = (
    "binance_testnet",
    "okx_demo",
    "bitget_demo",
)


@dataclass
class UnifiedState:
    total_equity_usdt: float | None
    total_available_usdt: float | None
    total_unrealized_pnl: float | None
    accounts: list[dict] = field(default_factory=list)
    positions: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    shadow_errors: list[str] = field(default_factory=list)
    connected_brokers: list[str] = field(default_factory=list)
    fetched_at: str = ""


def _profile_spec(cfg, execution_mode: str):
    if execution_mode == "binance_testnet":
        return (
            "binance.testnet",
            "binance_testnet",
            "binance",
            "testnet",
            ("api_key", "api_secret"),
            BinanceTestnetProfile,
        )
    if execution_mode == "okx_demo":
        return (
            "okx.demo",
            "okx_demo",
            "okx",
            "demo",
            ("api_key", "api_secret", "passphrase"),
            OKXDemoProfile,
        )
    if execution_mode == "binance_live_limited":
        return (
            "binance.live",
            "binance_live",
            "binance",
            "live",
            ("api_key", "api_secret"),
            BinanceLiveProfile,
        )
    if execution_mode == "okx_live_limited":
        return (
            "okx.live",
            "okx_live",
            "okx",
            "live",
            ("api_key", "api_secret", "passphrase"),
            OKXLiveProfile,
        )
    if execution_mode == "bitget_demo":
        profile = cfg.exchange.bitget_profile
        profile_type = BitgetUTAV3Profile if profile == "uta_v3" else BitgetClassicV2Profile
        return (
            "bitget.demo",
            profile,
            "bitget",
            "demo",
            ("api_key", "api_secret", "passphrase"),
            profile_type,
        )
    if execution_mode == "bitget_live_limited":
        profile = cfg.exchange.bitget_profile
        profile_type = (
            BitgetUTAV3LiveProfile
            if profile == "uta_v3"
            else BitgetClassicV2LiveProfile
        )
        return (
            "bitget.live",
            profile,
            "bitget",
            "live",
            ("api_key", "api_secret", "passphrase"),
            profile_type,
        )
    raise KeyError(execution_mode)


def _account_generation(cfg, execution_mode: str, scope: str, profile: str) -> int:
    secret_generation = get_credential_generation(scope)
    config_generation = cfg.exchange.uta_account_config_generation_for(execution_mode)
    profile_generation = {"classic_v2": 1, "uta_v3": 2}.get(profile, 0)
    secret_and_config = (
        (secret_generation + config_generation)
        * (secret_generation + config_generation + 1)
        // 2
        + config_generation
    )
    return (
        (secret_and_config + profile_generation)
        * (secret_and_config + profile_generation + 1)
        // 2
        + profile_generation
    )


def build_uta_registry(cfg=None) -> UTARegistry:
    """Build immutable factories from current config; no client/session is created."""
    cfg = cfg or load_config()
    registry = UTARegistry()
    for execution_mode in (
        "binance_testnet",
        "okx_demo",
        "bitget_demo",
        "binance_live_limited",
        "okx_live_limited",
        "bitget_live_limited",
    ):
        scope, profile_name, venue, environment, fields, profile_type = _profile_spec(
            cfg, execution_mode
        )
        credentials = {field: get_secret(f"{scope}.{field}") for field in fields}
        if not all(credentials.values()):
            continue
        generation = _account_generation(cfg, execution_mode, scope, profile_name)
        identity = UTAAccountIdentity(
            account_id=execution_mode,
            execution_mode=execution_mode,
            venue=venue,
            environment=environment,
            profile=profile_name,
            account_fingerprint=logical_account_fingerprint(
                broker_id=execution_mode,
                account_environment=environment,
                execution_mode=execution_mode,
            ),
            credential_generation=generation,
            adapter_implementation="CCXTUTAAdapter",
            adapter_version="1",
            ccxt_version=version("ccxt"),
        )
        config = UTAAdapterConfig(
            identity=identity,
            boundary=cfg.exchange.uta_read_boundary_for(execution_mode),
        )

        def factory(adapter_config, profile_type=profile_type, credentials=credentials):
            profile_kwargs = {
                "api_key": credentials["api_key"],
                "secret": credentials["api_secret"],
            }
            if "passphrase" in credentials:
                profile_kwargs["passphrase"] = credentials["passphrase"]
            return CCXTUTAAdapter(
                adapter_config.identity,
                profile_type(**profile_kwargs),
            )

        registry.register(config, factory)
    return registry


def create_uta_adapter(execution_mode: str, cfg=None):
    """Create one loop-local evidence adapter for the exact configured account."""
    return build_uta_registry(cfg).create(execution_mode)


async def query_uta(
    execution_mode: str,
    method: str,
    query=None,
    *,
    cfg=None,
):
    """Run one bounded UTA read with deterministic loop-local teardown."""
    adapter = create_uta_adapter(execution_mode, cfg)
    try:
        await adapter.connect()
        operation = getattr(adapter, method)
        return await operation() if query is None else await operation(query)
    finally:
        try:
            await adapter.close()
        except Exception:
            pass


def _legacy_brokers(execution_modes: tuple[str, ...] | None = None) -> dict[str, object]:
    from aletheia.portfolio.uta.registry import create_all_configured, create_broker

    if execution_modes is not None:
        return {
            execution_mode: broker
            for execution_mode in execution_modes
            if (broker := create_broker(execution_mode)) is not None
        }
    return {broker.broker_id: broker for broker in create_all_configured()}


def _legacy_identity(cfg, execution_mode: str, broker) -> UTAAccountIdentity:
    try:
        scope, profile, venue, environment, _fields, _profile_type = _profile_spec(
            cfg, execution_mode
        )
        generation = _account_generation(cfg, execution_mode, scope, profile)
    except KeyError:
        profile, venue, environment, generation = "legacy", execution_mode, "local_paper", 0
    return UTAAccountIdentity(
        account_id=execution_mode,
        execution_mode=execution_mode,
        venue=venue,
        environment=environment,
        profile=profile,
        credential_generation=generation,
        adapter_implementation=type(broker).__name__,
        adapter_version="legacy",
    )


def expected_cache_identity(
    cfg, uta_identity: UTAAccountIdentity, boundary: str
) -> UTAAccountIdentity:
    """Return the identity of the adapter that owns the authoritative cache row."""
    if boundary == "uta_v1":
        return uta_identity
    legacy_implementations = {
        "binance_testnet": "BinanceTestnetAdapter",
        "okx_demo": "OkxDemoAdapter",
        "bitget_demo": "BitgetDemoAdapter",
    }
    return uta_identity.model_copy(
        update={
            "adapter_implementation": legacy_implementations[uta_identity.execution_mode],
            "adapter_version": "legacy",
            "ccxt_version": None,
        }
    )


async def _query_legacy(broker):
    try:
        return await asyncio.gather(broker.get_account(), broker.get_positions())
    finally:
        try:
            await broker.close()
        except Exception:
            pass


async def _query_uta(adapter):
    try:
        await adapter.connect()
        return await asyncio.gather(adapter.get_account(), adapter.get_positions())
    finally:
        try:
            await adapter.close()
        except Exception:
            pass


def _identity_payload(identity: UTAAccountIdentity) -> dict:
    return identity.model_dump(mode="json")


def _legacy_projection(identity, boundary, broker, account, positions):
    environment = identity.environment
    fingerprint = logical_account_fingerprint(
        broker_id=identity.execution_mode,
        account_environment=environment,
        execution_mode=identity.execution_mode,
    )
    account_row = {
        "broker_id": identity.execution_mode,
        "display_name": broker.display_name,
        "boundary": boundary,
        "identity": _identity_payload(identity),
        "account_completeness": ReadCompleteness.COMPLETE.value,
        "position_completeness": ReadCompleteness.COMPLETE.value,
        "equity_usdt": account.equity_usdt,
        "available_usdt": account.available_usdt,
        "frozen_usdt": account.frozen_usdt,
        "unrealized_pnl": account.unrealized_pnl,
        "asset_breakdown": account.asset_breakdown,
    }
    position_rows = [
        {
            "broker_id": identity.execution_mode,
            "account_environment": environment,
            "account_fingerprint": getattr(position, "account_fingerprint", None)
            or fingerprint,
            "account_identity": _identity_payload(identity),
            "position_id": getattr(position, "position_id", None),
            "symbol": position.symbol,
            "side": position.side,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
            "unrealized_pnl": position.unrealized_pnl,
            "leverage": position.leverage,
            "leverage_source": position.leverage_source,
            "mark_price": position.mark_price,
            "cTime": position.opened_at_ms,
            "market_type": position.market_type,
        }
        for position in positions
    ]
    return account_row, position_rows


def _uta_projection(boundary, account, positions):
    identity = account.identity
    fingerprint = identity.account_fingerprint or logical_account_fingerprint(
        broker_id=identity.execution_mode,
        account_environment=identity.environment,
        execution_mode=identity.execution_mode,
    )
    breakdown = []
    for balance in account.balances:
        settlement_value = balance.total if balance.currency == account.settlement_currency else None
        breakdown.append(
            {
                "coin": balance.currency,
                "usdt_value": float(settlement_value) if settlement_value is not None else None,
                "pct": (
                    float(settlement_value / account.equity * 100)
                    if settlement_value is not None and account.equity not in {None, 0}
                    else None
                ),
            }
        )
    unrealized_pnl = account.unrealized_pnl
    if unrealized_pnl is None and positions.completeness is ReadCompleteness.COMPLETE:
        position_upnl = [position.unrealized_pnl for position in positions.positions]
        if all(value is not None for value in position_upnl):
            unrealized_pnl = sum(position_upnl, start=Decimal(0))
    account_row = {
        "broker_id": identity.execution_mode,
        "display_name": identity.execution_mode,
        "boundary": boundary,
        "identity": _identity_payload(identity),
        "account_completeness": account.completeness.value,
        "position_completeness": positions.completeness.value,
        "reason_code": account.reason_code or positions.reason_code,
        "equity_usdt": float(account.equity) if account.equity is not None else None,
        "available_usdt": float(account.available) if account.available is not None else None,
        "frozen_usdt": float(account.frozen) if account.frozen is not None else None,
        "unrealized_pnl": float(unrealized_pnl) if unrealized_pnl is not None else None,
        "asset_breakdown": breakdown,
    }
    position_rows = [
        {
            "broker_id": identity.execution_mode,
            "account_environment": identity.environment,
            "account_fingerprint": fingerprint,
            "account_identity": _identity_payload(identity),
            "position_id": position.venue_position_id,
            "symbol": position.instrument.canonical_symbol,
            "side": position.side,
            "quantity": float(position.quantity),
            "entry_price": (
                float(position.entry_price) if position.entry_price is not None else None
            ),
            "unrealized_pnl": (
                float(position.unrealized_pnl)
                if position.unrealized_pnl is not None
                else None
            ),
            "leverage": float(position.leverage) if position.leverage is not None else None,
            "leverage_source": (
                "exchange_reported" if position.leverage is not None else "unavailable"
            ),
            "mark_price": float(position.mark_price) if position.mark_price is not None else None,
            "cTime": (
                int(position.opened_at.timestamp() * 1000)
                if position.opened_at is not None
                else None
            ),
            "market_type": position.instrument.market_type,
        }
        for position in positions.positions
    ]
    return account_row, position_rows


def _shadow_differences(legacy_account, legacy_positions, uta_account, uta_positions):
    def decimal_value(value):
        return None if value is None else Decimal(str(value))

    legacy_position_facts = [
        (
            getattr(position, "position_id", None),
            str(position.symbol),
            str(position.market_type),
            str(position.side).lower(),
            decimal_value(position.quantity),
            decimal_value(position.entry_price),
            decimal_value(position.mark_price),
            decimal_value(position.leverage),
            decimal_value(position.unrealized_pnl),
        )
        for position in legacy_positions
    ]
    uta_position_facts = [
        (
            getattr(position, "venue_position_id", None),
            position.instrument.canonical_symbol,
            position.instrument.market_type,
            str(position.side).lower(),
            position.quantity,
            position.entry_price,
            position.mark_price,
            position.leverage,
            position.unrealized_pnl,
        )
        for position in uta_positions.positions
    ]
    legacy_position_facts.sort(key=repr)
    uta_position_facts.sort(key=repr)
    differences = []
    comparisons = {
        "equity": (legacy_account.equity_usdt, uta_account.equity),
        "available": (legacy_account.available_usdt, uta_account.available),
        "unrealized_pnl": (legacy_account.unrealized_pnl, uta_account.unrealized_pnl),
        "position_count": (len(legacy_positions), len(uta_positions.positions)),
        "positions": (legacy_position_facts, uta_position_facts),
    }
    for field, (legacy_value, uta_value) in comparisons.items():
        if field in {"position_count", "positions"}:
            equal = legacy_value == uta_value
        else:
            equal = uta_value is not None and Decimal(str(legacy_value)) == uta_value
        if not equal:
            differences.append(field)
    return differences


async def _read_selected_account(cfg, registry, legacy, identity):
    boundary = cfg.exchange.uta_read_boundary_for(identity.execution_mode)
    broker = legacy.get(identity.execution_mode)
    errors = []
    shadow_errors = []
    try:
        if boundary in {"legacy", "shadow_read"}:
            if broker is None:
                raise RuntimeError("legacy broker unavailable")
            legacy_account, legacy_positions = await _query_legacy(broker)
            account_row, position_rows = _legacy_projection(
                identity, boundary, broker, legacy_account, legacy_positions
            )
            if boundary == "shadow_read":
                try:
                    uta_account, uta_positions = await _query_uta(
                        registry.create(identity.account_id)
                    )
                    account_row["shadow_differences"] = _shadow_differences(
                        legacy_account,
                        legacy_positions,
                        uta_account,
                        uta_positions,
                    )
                    if (
                        uta_account.completeness is not ReadCompleteness.COMPLETE
                        or uta_positions.completeness is not ReadCompleteness.COMPLETE
                    ):
                        shadow_errors.append(
                            f"{identity.execution_mode}:shadow_read_incomplete"
                        )
                except Exception:
                    shadow_errors.append(
                        f"{identity.execution_mode}:shadow_read_unavailable"
                    )
        else:
            uta_account, uta_positions = await _query_uta(
                registry.create(identity.account_id)
            )
            account_row, position_rows = _uta_projection(
                boundary, uta_account, uta_positions
            )
            if (
                uta_account.completeness is not ReadCompleteness.COMPLETE
                or uta_positions.completeness is not ReadCompleteness.COMPLETE
            ):
                errors.append(f"{identity.execution_mode}:incomplete_read")
        return account_row, position_rows, errors, shadow_errors
    except Exception:
        return (
            None,
            [],
            [f"{identity.execution_mode}:account_read_unavailable"],
            shadow_errors,
        )


async def get_unified_state(
    cfg=None,
    *,
    execution_modes: tuple[str, ...] | None = None,
) -> UnifiedState:
    cfg = cfg or load_config()
    fetched_at = datetime.now(timezone.utc).isoformat()
    requested_modes = tuple(dict.fromkeys(
        DEFAULT_UNIFIED_EXECUTION_MODES
        if execution_modes is None
        else execution_modes
    ))
    try:
        registry = build_uta_registry(cfg)
    except Exception:
        return UnifiedState(
            total_equity_usdt=None,
            total_available_usdt=None,
            total_unrealized_pnl=None,
            errors=["uta_registry_configuration_invalid"],
            fetched_at=fetched_at,
        )
    configured_identities = tuple(
        identity
        for identity in registry.configured_identities()
        if identity.execution_mode in requested_modes
    )
    identities_by_mode = {
        identity.execution_mode: identity for identity in configured_identities
    }
    legacy_modes = tuple(
        execution_mode
        for execution_mode in (
            (identity.execution_mode for identity in configured_identities)
            if configured_identities
            else requested_modes
        )
        if cfg.exchange.uta_read_boundary_for(execution_mode)
        in {"legacy", "shadow_read"}
    )
    legacy = _legacy_brokers(legacy_modes)
    for execution_mode, broker in legacy.items():
        identities_by_mode[execution_mode] = _legacy_identity(cfg, execution_mode, broker)
    identities = (
        tuple(identities_by_mode.values())
        if execution_modes is None
        else tuple(
            identities_by_mode[execution_mode]
            for execution_mode in requested_modes
            if execution_mode in identities_by_mode
        )
    )
    missing_modes = (
        tuple(
            execution_mode
            for execution_mode in requested_modes
            if execution_mode not in identities_by_mode
        )
        if execution_modes is not None
        else ()
    )
    if not identities:
        if missing_modes:
            return UnifiedState(
                None,
                None,
                None,
                errors=[
                    f"{execution_mode}:account_not_configured"
                    for execution_mode in missing_modes
                ],
                fetched_at=fetched_at,
            )
        return UnifiedState(0.0, 0.0, 0.0, fetched_at=fetched_at)
    accounts = []
    positions = []
    errors = [
        f"{execution_mode}:account_not_configured"
        for execution_mode in missing_modes
    ]
    shadow_errors = []
    connected = []
    totals = [0.0, 0.0, 0.0]

    results = await asyncio.gather(
        *[
            _read_selected_account(cfg, registry, legacy, identity)
            for identity in identities
        ]
    )
    for identity, (account_row, position_rows, read_errors, read_shadow_errors) in zip(
        identities, results
    ):
        errors.extend(read_errors)
        shadow_errors.extend(read_shadow_errors)
        if account_row is None:
            continue
        accounts.append(account_row)
        positions.extend(position_rows)
        if (
            account_row["account_completeness"] != ReadCompleteness.COMPLETE.value
            or account_row["position_completeness"] != ReadCompleteness.COMPLETE.value
        ):
            continue
        values = (
            account_row.get("equity_usdt"),
            account_row.get("available_usdt"),
            account_row.get("unrealized_pnl"),
        )
        if any(value is None for value in values):
            errors.append(f"{identity.execution_mode}:account_value_unavailable")
            continue
        connected.append(identity.execution_mode)
        for index, value in enumerate(values):
            totals[index] += float(value)

    complete = not errors and len(connected) == len(identities)
    return UnifiedState(
        total_equity_usdt=round(totals[0], 4) if complete else None,
        total_available_usdt=round(totals[1], 4) if complete else None,
        total_unrealized_pnl=round(totals[2], 4) if complete else None,
        accounts=accounts,
        positions=positions,
        errors=errors,
        shadow_errors=shadow_errors,
        connected_brokers=connected,
        fetched_at=fetched_at,
    )


def get_unified_state_sync(
    execution_modes: tuple[str, ...] | None = None,
) -> UnifiedState:
    from concurrent.futures import ThreadPoolExecutor

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(get_unified_state(execution_modes=execution_modes))
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(
            asyncio.run,
            get_unified_state(execution_modes=execution_modes),
        ).result(timeout=30)
