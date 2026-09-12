"""Exchange unification map — single source of truth for CLI and Web routes."""

from __future__ import annotations

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
