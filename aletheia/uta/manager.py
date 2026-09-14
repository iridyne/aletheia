"""Multi-account UTA read coordination and account projection."""

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
from aletheia.uta.models import OrderQuery, OrderQueryReport, ReadCompleteness, UTAAccountIdentity
from aletheia.uta.registry import UTAAdapterConfig, UTARegistry


DEFAULT_UNIFIED_EXECUTION_MODES = (
    "binance_testnet",
    "okx_demo",
    "bitget_demo",
)

# Every execution mode UTA can build an adapter for — the authoritative list of
# real-exchange account identities. Physical venue lookup belongs to the shared
# `config.exchanges.EXECUTION_MODE_VENUES` registry; the OKX demo capability seam
# is a specialized consumer, not a second identity map.
UTA_EXECUTION_MODES: tuple[str, ...] = (
    "binance_testnet",
    "okx_demo",
    "bitget_demo",
    "binance_live_limited",
    "okx_live_limited",
    "bitget_live_limited",
)


@dataclass
class UnifiedState:
    total_equity_usdt: float | None
    total_available_usdt: float | None
    total_unrealized_pnl: float | None
    accounts: list[dict] = field(default_factory=list)
    positions: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    connected_brokers: list[str] = field(default_factory=list)
    fetched_at: str = ""
    execution_modes: tuple[str, ...] = field(default_factory=tuple)


def _profile_spec(cfg, execution_mode: str):
    if execution_mode == "binance_testnet":
        from aletheia.uta.profiles.binance import BinanceTestnetProfile

        return (
            "binance.testnet",
            "binance_testnet",
            "binance",
            "testnet",
            ("api_key", "api_secret"),
            BinanceTestnetProfile,
        )
    if execution_mode == "okx_demo":
        from aletheia.uta.profiles.okx import OKXDemoProfile

        return (
            "okx.demo",
            "okx_demo",
            "okx",
            "demo",
            ("api_key", "api_secret", "passphrase"),
            OKXDemoProfile,
        )
    if execution_mode == "binance_live_limited":
        from aletheia.uta.profiles.binance import BinanceLiveProfile

        return (
            "binance.live",
            "binance_live",
            "binance",
            "live",
            ("api_key", "api_secret"),
            BinanceLiveProfile,
        )
    if execution_mode == "okx_live_limited":
        from aletheia.uta.profiles.okx import OKXLiveProfile

        return (
            "okx.live",
            "okx_live",
            "okx",
            "live",
            ("api_key", "api_secret", "passphrase"),
            OKXLiveProfile,
        )
    if execution_mode == "bitget_demo":
        from aletheia.uta.profiles.bitget import BitgetClassicV2Profile, BitgetUTAV3Profile

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
        from aletheia.uta.profiles.bitget import BitgetClassicV2LiveProfile, BitgetUTAV3LiveProfile

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


def funding_history_retention_contract(
    execution_mode: str,
    *,
    cfg=None,
) -> tuple[int, str]:
    """Return the active profile's credential-free funding retention contract."""
    cfg = cfg or load_config()
    profile_type = _profile_spec(cfg, execution_mode)[-1]
    return (
        int(profile_type.private_history_retention_days),
        str(profile_type.profile_version),
    )


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


def build_uta_registry(
    cfg=None, *, execution_modes: tuple[str, ...] = UTA_EXECUTION_MODES,
) -> UTARegistry:
    """Resolve only requested account scopes; no client/session is created."""
    cfg = cfg or load_config()
    registry = UTARegistry()
    for execution_mode in UTA_EXECUTION_MODES:
        if execution_mode not in execution_modes:
            continue
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
        config = UTAAdapterConfig(identity=identity)

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
    return build_uta_registry(cfg, execution_modes=(execution_mode,)).create(execution_mode)


async def query_uta(
    execution_mode: str,
    method: str,
    query=None,
    *,
    cfg=None,
    expected_account: UTAAccountIdentity | None = None,
):
    """Run one bounded UTA read with deterministic loop-local teardown."""
    adapter = create_uta_adapter(execution_mode, cfg)
    try:
        if expected_account is not None and adapter.identity != expected_account:
            raise ValueError("uta_account_identity_mismatch")
        if method == "get_order":
            if not isinstance(query, OrderQuery):
                raise ValueError("uta_exact_order_query_invalid")
            query.require_exact()
        await adapter.connect()
        operation = getattr(adapter, method)
        report = await operation() if query is None else await operation(query)
        if expected_account is not None and report.identity != expected_account:
            raise ValueError("uta_account_identity_mismatch")
        if method == "get_order":
            if not isinstance(report, OrderQueryReport):
                raise ValueError("uta_exact_order_report_invalid")
            found = report.found
            if found is not None:
                if query.venue_order_id and found.venue_order_id != query.venue_order_id:
                    raise ValueError("uta_exact_order_identity_mismatch")
                expected = query.scope.instrument
                if expected is not None and (
                    found.instrument.canonical_symbol, found.instrument.market_type
                ) != (expected.canonical_symbol, expected.market_type):
                    raise ValueError("uta_exact_order_instrument_mismatch")
        return report
    finally:
        try:
            await adapter.close()
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


def _uta_projection(account, positions):
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


async def _read_selected_account(cfg, registry, identity):
    try:
        account, positions = await _query_uta(registry.create(identity.account_id))
        account_row, position_rows = _uta_projection(account, positions)
        errors = []
        if (
            account.completeness is not ReadCompleteness.COMPLETE
            or positions.completeness is not ReadCompleteness.COMPLETE
        ):
            errors.append(f"{identity.execution_mode}:incomplete_read")
        return account_row, position_rows, errors
    except Exception:
        return None, [], [f"{identity.execution_mode}:account_read_unavailable"]


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
        registry = build_uta_registry(cfg, execution_modes=requested_modes)
    except Exception:
        return UnifiedState(
            total_equity_usdt=None,
            total_available_usdt=None,
            total_unrealized_pnl=None,
            errors=["uta_registry_configuration_invalid"],
            fetched_at=fetched_at,
            execution_modes=requested_modes,
        )
    configured_identities = tuple(
        identity
        for identity in registry.configured_identities()
        if identity.execution_mode in requested_modes
    )
    identities_by_mode = {
        identity.execution_mode: identity for identity in configured_identities
    }
    identities = tuple(
        identities_by_mode[mode]
        for mode in requested_modes
        if mode in identities_by_mode
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
        return UnifiedState(
            None, None, None,
            errors=[f"{mode}:account_not_configured" for mode in requested_modes],
            fetched_at=fetched_at,
            execution_modes=requested_modes,
        )
    accounts = []
    positions = []
    errors = [
        f"{execution_mode}:account_not_configured"
        for execution_mode in missing_modes
    ]
    connected = []
    totals = [0.0, 0.0, 0.0]

    results = await asyncio.gather(
        *[
            _read_selected_account(cfg, registry, identity)
            for identity in identities
        ]
    )
    for identity, (account_row, position_rows, read_errors) in zip(
        identities, results
    ):
        errors.extend(read_errors)
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
        connected_brokers=connected,
        fetched_at=fetched_at,
        execution_modes=requested_modes,
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


def probe_account_reachable(execution_mode: str, *, cfg=None) -> dict:
    """One signed read-only account probe for credential/connectivity checks.

    Replaces the per-venue hand-rolled `test_connection()` methods. Uses the same
    adapter, identity validation and teardown as every other UTA read, so a probe
    can never succeed against an account the rest of the system would reject.

    Never returns `str(exc)`: a CCXT/venue error can carry the signed request, and
    therefore the API key, in its message. The exception CLASS name is enough to
    act on, and the categorised cases below cover what an operator can actually
    fix.
    """
    from aletheia.async_utils import run_coro_from_sync

    try:
        report = run_coro_from_sync(
            query_uta(execution_mode, "get_account", cfg=cfg), timeout=20
        )
    except Exception as exc:  # noqa: BLE001 - classified below, never echoed raw
        message = str(exc).lower()
        if "not configured" in message or "credential" in message:
            reason = "credentials_not_configured"
        elif "timeout" in message:
            reason = "connection_timeout"
        elif "identity_mismatch" in message:
            reason = "account_identity_mismatch"
        else:
            reason = type(exc).__name__
        return {"connected": False, "execution_mode": execution_mode, "error": reason}
    if report.completeness is not ReadCompleteness.COMPLETE:
        return {
            "connected": False,
            "execution_mode": execution_mode,
            "error": report.reason_code or "account_read_incomplete",
        }
    return {"connected": True, "execution_mode": execution_mode}
