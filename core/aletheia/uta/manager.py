"""Core coordination for the exchange-neutral UTA port."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import math
from typing import Protocol

from aletheia.config.exchanges import (
    DEMO_EXECUTION_MODES,
    EXECUTION_MODE_ENVIRONMENTS,
    EXTERNAL_EXECUTION_MODES,
)
from aletheia.config.loader import load_config
from aletheia.portfolio.disappearance_confirmation import logical_account_fingerprint
from aletheia.uta.models import FillReport, OrderQuery, OrderQueryReport, ReadCompleteness, UTAAccountIdentity
from aletheia.uta.registry import UTARegistry


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


@dataclass(frozen=True)
class NativeProtectionHistory:
    """Typed result for an Extension's native protection-plan history read."""

    identity: UTAAccountIdentity
    plan_order_id: str
    execute_order_ids: tuple[str, ...] = ()


class NativeProtectionReader(Protocol):
    identity: UTAAccountIdentity

    async def get_plan_order_history(self, *, order_id: str) -> NativeProtectionHistory: ...

    async def get_order_fill_reports(
        self,
        *,
        order_id: str,
        symbol: str,
        market_type: str,
        identity: UTAAccountIdentity,
    ) -> tuple[FillReport, ...]: ...

    async def close(self) -> None: ...
# The registry is Core configuration; concrete adapters are supplied by an Extension.
DEFAULT_UNIFIED_EXECUTION_MODES = tuple(
    mode for mode in EXECUTION_MODE_ENVIRONMENTS if mode in DEMO_EXECUTION_MODES
)
UTA_EXECUTION_MODES: tuple[str, ...] = tuple(
    mode for mode in EXECUTION_MODE_ENVIRONMENTS if mode in EXTERNAL_EXECUTION_MODES
)


_RegistryFactory = Callable[..., UTARegistry]
_RetentionContract = Callable[..., tuple[int, str]]
_IdentityResolver = Callable[..., UTAAccountIdentity]
_NativeProtectionReaderFactory = Callable[..., NativeProtectionReader]
_CandidateCredentialProbe = Callable[..., Awaitable[dict]]
_ConfigurationDiagnostic = Callable[..., tuple[str, bool, str]]
_CredentialConfiguration = Callable[..., tuple[int, int]]
_registry_factory: _RegistryFactory | None = None
_retention_contract: _RetentionContract | None = None
_identity_resolver: _IdentityResolver | None = None
_native_protection_reader_factory: _NativeProtectionReaderFactory | None = None
_candidate_credential_probe: _CandidateCredentialProbe | None = None
_configuration_diagnostic: _ConfigurationDiagnostic | None = None
_credential_configuration: _CredentialConfiguration | None = None
_profile_accounts: Callable[[str, str], tuple[str, ...]] | None = None
_public_connectivity_probe: Callable[[str], Awaitable[dict]] | None = None


def configure_uta_provider(
    *,
    registry_factory: _RegistryFactory,
    funding_history_retention_contract: _RetentionContract,
    identity_resolver: _IdentityResolver | None = None,
    native_protection_reader_factory: _NativeProtectionReaderFactory | None = None,
    candidate_credential_probe: _CandidateCredentialProbe | None = None,
    configuration_diagnostic: _ConfigurationDiagnostic | None = None,
    credential_configuration: _CredentialConfiguration | None = None,
    profile_accounts: Callable[[str, str], tuple[str, ...]] | None = None,
    public_connectivity_probe: Callable[[str], Awaitable[dict]] | None = None,
) -> None:
    """Bind one optional Exchange Extension to the Core UTA port."""
    global _registry_factory, _retention_contract, _identity_resolver
    global _native_protection_reader_factory, _candidate_credential_probe
    global _configuration_diagnostic
    global _credential_configuration
    global _profile_accounts
    global _public_connectivity_probe
    _registry_factory = registry_factory
    _retention_contract = funding_history_retention_contract
    _identity_resolver = identity_resolver
    _native_protection_reader_factory = native_protection_reader_factory
    _candidate_credential_probe = candidate_credential_probe
    _configuration_diagnostic = configuration_diagnostic
    _credential_configuration = credential_configuration
    _profile_accounts = profile_accounts
    _public_connectivity_probe = public_connectivity_probe


async def probe_public_connectivity(venue: str) -> dict:
    """Read public reachability only; it never proves account/trading capability."""
    if _public_connectivity_probe is None:
        return {"reachable": False, "latency_ms": None,
                "error": "exchange_extension_unavailable", "exchange": venue}
    try:
        result = await _public_connectivity_probe(venue)
    except Exception as exc:
        return {"reachable": False, "latency_ms": None,
                "error": type(exc).__name__, "exchange": venue}
    latency = result.get("latency_ms") if isinstance(result, dict) else None
    error = result.get("error") if isinstance(result, dict) else None
    valid_latency = (
        type(latency) in (int, float) and math.isfinite(latency) and latency >= 0
    )
    valid_failure = (
        error == "exchange_probe_unavailable" and latency is None
    ) or (
        isinstance(error, str) and error.startswith("HTTP ")
        and error[5:].isdigit() and 100 <= int(error[5:]) <= 599
        and valid_latency
    )
    if (
        not isinstance(result, dict)
        or type(result.get("reachable")) is not bool
        or result.get("exchange") != venue
        or "error" not in result
        or "latency_ms" not in result
        or (result["reachable"] and (
            result["error"] is not None
            or not valid_latency
        ))
        or (not result["reachable"] and not valid_failure)
    ):
        return {"reachable": False, "latency_ms": None,
                "error": "invalid_exchange_probe_result", "exchange": venue}
    return {key: result[key] for key in ("reachable", "latency_ms", "error", "exchange")}


def profile_accounts(field: str, value: str) -> tuple[str, ...]:
    """Validate an adapter profile and return the accounts sharing that setting."""
    if _profile_accounts is None:
        raise RuntimeError("exchange_extension_unavailable")
    modes = _profile_accounts(field, value)
    if not modes or any(mode not in UTA_EXECUTION_MODES for mode in modes):
        raise ValueError("invalid_profile_account_identity")
    return tuple(dict.fromkeys(modes))


def credential_configuration(execution_mode: str, cfg=None) -> tuple[int, int] | None:
    """Return configured/required counts for one account, never credential values."""
    if _credential_configuration is None:
        return None
    configured, required = _credential_configuration(cfg, execution_mode)
    if not (type(configured) is int and type(required) is int and 0 <= configured <= required and required > 0):
        raise ValueError("invalid_credential_configuration_counts")
    return configured, required


def configuration_diagnostic(cfg) -> tuple[str, bool, str] | None:
    """Read the optional adapter's configuration note; never grant startup authority."""
    if _configuration_diagnostic is None:
        return None
    try:
        return _configuration_diagnostic(cfg)
    except Exception as exc:
        return "exchange_configuration", False, f"configuration diagnostic unavailable: {type(exc).__name__}"


def build_uta_registry(
    cfg=None, *, execution_modes: tuple[str, ...] = UTA_EXECUTION_MODES
) -> UTARegistry:
    """Build requested account identities without making Core depend on an adapter."""
    if _registry_factory is None:
        return UTARegistry()
    return _registry_factory(cfg, execution_modes=execution_modes)

def configured_uta_identity(execution_mode: str, cfg=None) -> UTAAccountIdentity:
    """Return the exact configured account identity for one execution mode."""
    if _identity_resolver is not None:
        return _identity_resolver(cfg, execution_mode)
    identity = next(
        (
            item
            for item in build_uta_registry(cfg, execution_modes=(execution_mode,)).configured_identities()
            if item.execution_mode == execution_mode
        ),
        None,
    )
    if identity is None:
        raise ValueError("uta_account_identity_unavailable")
    return identity


def create_uta_adapter(execution_mode: str, cfg=None):
    """Create one loop-local adapter for the exact configured account."""
    return build_uta_registry(cfg, execution_modes=(execution_mode,)).create(execution_mode)


def create_native_protection_reader(
    execution_mode: str,
    *,
    expected_account: UTAAccountIdentity | None = None,
    cfg=None,
) -> NativeProtectionReader:
    """Create the selected Extension's native-protection observation reader."""
    if _native_protection_reader_factory is None:
        raise RuntimeError("exchange_extension_unavailable")
    reader = _native_protection_reader_factory(
        execution_mode, expected_account=expected_account, cfg=cfg
    )
    if expected_account is not None and reader.identity != expected_account:
        raise ValueError("uta_account_identity_mismatch")
    return reader


async def probe_candidate_credentials(
    api_key: str,
    api_secret: str,
    passphrase: str,
    profile: str,
) -> dict:
    """Probe candidate credentials through the selected Extension only."""
    if _candidate_credential_probe is None:
        raise RuntimeError("exchange_extension_unavailable")
    return await _candidate_credential_probe(api_key, api_secret, passphrase, profile)

def funding_history_retention_contract(
    execution_mode: str,
    *,
    cfg=None,
) -> tuple[int, str]:
    """Return the Extension's credential-free funding retention contract."""
    if _retention_contract is None:
        raise RuntimeError("exchange_extension_unavailable")
    return _retention_contract(execution_mode, cfg=cfg)


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
