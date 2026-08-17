"""Exchange unification map — single source of truth for CLI and Web routes."""

from __future__ import annotations

# Maps exchange_id → (ccxt_exchange_name, paper_execution_mode)
EXCHANGE_UNIFICATION_MAP: dict[str, tuple[str, str]] = {
    "okx": ("okx", "okx_demo"),
    "bitget": ("bitget", "bitget_demo"),
    "binance": ("binance", "binance_testnet"),
}

VALID_EXCHANGES: list[str] = sorted(EXCHANGE_UNIFICATION_MAP.keys()) + ["paper_internal"]

# The live account is deliberately a different execution mode from the paper
# broker.  Keep this mapping beside the paper unification map so every trust,
# Risk, and Gateway check uses the same broker identity.
LIVE_EXECUTION_MODES: dict[str, str] = {
    exchange_id: f"{exchange_id}_live_limited"
    for exchange_id in EXCHANGE_UNIFICATION_MAP
}


def live_execution_mode_for_exchange(exchange_id: str) -> str | None:
    """Return the canonical live execution mode for a supported exchange."""
    return LIVE_EXECUTION_MODES.get(str(exchange_id or "").strip().lower())
