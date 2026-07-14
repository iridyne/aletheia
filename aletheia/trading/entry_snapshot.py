"""Read-only proposal-time attribution snapshot helpers.

This module deliberately lives outside the execution package: decision code may
prepare immutable Proposal intent, but it must not import an order-placement
gateway or broker merely to capture read-only market context.
"""

from __future__ import annotations

import json
from typing import Any


def _btc_price_now() -> float | None:
    try:
        from aletheia.trading.market_data import latest_snapshot

        snapshot = latest_snapshot("BTC/USDT")
        return float(snapshot["price"]) if snapshot else None
    except Exception:
        return None


def _current_regime() -> str:
    try:
        from aletheia.storage.repositories import get_latest_market_regime

        regime = get_latest_market_regime()
        if regime and regime.get("state"):
            return str(regime["state"])
    except Exception:
        pass
    try:
        from aletheia.storage.repositories import latest_macro_snapshot

        macro = latest_macro_snapshot()
        if macro and macro.get("risk_regime"):
            return str(macro["risk_regime"])
    except Exception:
        pass
    return "unknown"


def capture_attribution_snapshot(symbol: str) -> dict[str, Any]:
    """Capture best-effort read-only context before Proposal hashing."""
    snapshot: dict[str, Any] = {
        "btc_entry_price": _btc_price_now(),
        "regime_tag": _current_regime(),
    }
    try:
        from aletheia.data.context_builder import build_market_context_from_db

        context = build_market_context_from_db([symbol])
        snapshot["market_context"] = {
            "data_completeness": context.data_completeness,
            "tradability": context.tradability.get(symbol, {}),
            "leverage": context.leverage.get(symbol, {}),
        }
    except Exception:
        pass
    return snapshot


def capture_attribution_snapshot_json(symbol: str) -> str | None:
    try:
        return json.dumps(capture_attribution_snapshot(symbol), default=str)
    except Exception:
        return None
