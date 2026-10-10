"""Exchange unification map — single source of truth for CLI and Web routes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AccountConfigBinding(str, Enum):
    GLOBAL = "global"
    SELF_CONTAINED = "self_contained"


# Existing account-policy attributes, not an adapter-granted Risk capability.
# The owner explicitly retained these exemptions in #3134.
EXECUTION_MODE_ACCOUNT_CONFIG_BINDINGS = {
    "bitget_demo": AccountConfigBinding.GLOBAL,
    "bitget_live_limited": AccountConfigBinding.GLOBAL,
    "okx_demo": AccountConfigBinding.SELF_CONTAINED,
    "okx_live_limited": AccountConfigBinding.SELF_CONTAINED,
    "binance_testnet": AccountConfigBinding.SELF_CONTAINED,
    "binance_live_limited": AccountConfigBinding.SELF_CONTAINED,
}


def execution_mode_account_config_binding(execution_mode: str) -> AccountConfigBinding:
    """Unknown identities never inherit an exemption; Risk separately rejects them."""
    return EXECUTION_MODE_ACCOUNT_CONFIG_BINDINGS.get(
        execution_mode,
        AccountConfigBinding.GLOBAL,
    )


# Maps exchange_id → (ccxt_exchange_name, paper_execution_mode)
EXCHANGE_UNIFICATION_MAP: dict[str, tuple[str, str]] = {
    "okx": ("okx", "okx_demo"),
    "bitget": ("bitget", "bitget_demo"),
    "binance": ("binance", "binance_testnet"),
}

VALID_EXCHANGES: list[str] = sorted(EXCHANGE_UNIFICATION_MAP)

# The live account is deliberately a different execution mode from the paper
# broker.  Keep this mapping beside the paper unification map so every trust,
# Risk, and Gateway check uses the same broker identity.
LIVE_EXECUTION_MODES: dict[str, str] = {
    exchange_id: f"{exchange_id}_live_limited"
    for exchange_id in EXCHANGE_UNIFICATION_MAP
}

# Execution-mode names are persisted account identities. Keep their
# environment classification in this registry so Core callers do not infer
# safety or accounting semantics from string suffixes.
EXECUTION_MODE_ENVIRONMENTS: dict[str, str] = {
    "paper_internal": "local",
    "binance_testnet": "testnet",
    "okx_demo": "demo",
    "bitget_demo": "demo",
    "binance_live_limited": "live",
    "okx_live_limited": "live",
    "bitget_live_limited": "live",
}
EXTERNAL_EXECUTION_MODES = frozenset(
    mode for mode, environment in EXECUTION_MODE_ENVIRONMENTS.items()
    if environment != "local"
)
DEMO_EXECUTION_MODES = frozenset(
    mode for mode, environment in EXECUTION_MODE_ENVIRONMENTS.items()
    if environment in {"demo", "testnet"}
)
LIVE_EXECUTION_MODE_SET = frozenset(
    mode for mode, environment in EXECUTION_MODE_ENVIRONMENTS.items()
    if environment == "live"
)
EXECUTION_MODE_VENUES: dict[str, str] = {
    demo_mode: venue
    for venue, (_, demo_mode) in EXCHANGE_UNIFICATION_MAP.items()
}
EXECUTION_MODE_VENUES.update(
    {live_mode: venue for venue, live_mode in LIVE_EXECUTION_MODES.items()}
)


def execution_mode_environment(execution_mode: str | None) -> str | None:
    """Return the registered environment for a persisted execution identity."""
    mode = str(execution_mode or "").strip().lower()
    return EXECUTION_MODE_ENVIRONMENTS.get(mode)


def execution_mode_venue(execution_mode: str | None) -> str | None:
    """Return the registered physical venue for an external identity."""
    mode = str(execution_mode or "").strip().lower()
    return EXECUTION_MODE_VENUES.get(mode)


def is_live_execution_mode(execution_mode: str | None) -> bool:
    """Return whether the registered execution identity is live-limited."""
    return execution_mode_environment(execution_mode) == "live"


def live_execution_mode_for_exchange(exchange_id: str) -> str | None:
    """Return the canonical live execution mode for a supported exchange."""
    return LIVE_EXECUTION_MODES.get(str(exchange_id or "").strip().lower())


def selected_live_execution_mode(cfg) -> str | None:
    """The live account of the venue ``exchange.paper_execution_mode`` selects."""
    return live_execution_mode_for_exchange(
        execution_mode_venue(cfg.exchange.paper_execution_mode) or ""
    )


@dataclass(frozen=True)
class EntryRoutePolicy:
    """What qualifies one execution route for autonomous Entry Plans."""

    minimum_permission: str  # venue API-key permission the route needs
    margin_mode: str  # "isolated" | "cross": what the entry path submits
    native_tpsl_default: bool  # exchange-side TP/SL when the flag is unset


# The one place an execution route is qualified for entries. A route missing
# here fails closed for entry permission, margin mode and native TP/SL default.
# Add a route only together with matching Planner, Risk/Gateway and execution
# tests (#3729 §8, #3848 PR-A item 4).
ENTRY_ROUTE_POLICIES: dict[str, EntryRoutePolicy] = {
    "bitget_demo": EntryRoutePolicy(
        minimum_permission="Full", margin_mode="isolated", native_tpsl_default=True,
    ),
}
