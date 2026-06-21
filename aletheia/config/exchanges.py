"""Exchange unification map — single source of truth for CLI and Web routes."""

from __future__ import annotations

# Maps exchange_id → (ccxt_exchange_name, paper_execution_mode)
EXCHANGE_UNIFICATION_MAP: dict[str, tuple[str, str]] = {
    "okx": ("okx", "okx_demo"),
    "bitget": ("bitget", "bitget_demo"),
    "binance": ("binance", "binance_testnet"),
}

VALID_EXCHANGES: list[str] = sorted(EXCHANGE_UNIFICATION_MAP.keys()) + ["paper_internal"]
