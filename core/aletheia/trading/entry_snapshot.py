"""Read-only proposal-time attribution snapshot helpers.

This module deliberately lives outside the execution package: decision code may
prepare immutable Proposal intent, but it must not import an order-placement
gateway or broker merely to capture read-only market context.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("aletheia.trading.entry_snapshot")


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
        if regime is not None:
            return "unknown"
    except Exception:
        logger.warning(
            "entry_snapshot: market regime lookup failed (falling back to macro snapshot)",
            exc_info=True,
        )
    try:
        from aletheia.storage.repositories import latest_macro_snapshot

        macro = latest_macro_snapshot()
        if macro and macro.get("risk_regime"):
            return str(macro["risk_regime"])
    except Exception:
        logger.warning(
            "entry_snapshot: macro snapshot fallback failed (returning 'unknown')",
            exc_info=True,
        )
    return "unknown"


def _physical_market_type(market_type: str | None) -> str:
    normalized = str(market_type or "").strip().lower()
    if normalized in {"usdt_futures", "swap"}:
        return "perp"
    if normalized in {"spot", "perp", "futures"}:
        return normalized
    return ""


def _unavailable_analysis_context(reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "kind": "unknown",
        "reason": reason,
    }


def _resolve_opportunity_context(
    symbol: str,
    *,
    exchange: str,
    market_type: str,
    opportunity_id: str | None,
) -> dict[str, Any] | None:
    """Resolve frozen instrument applicability without inventing legacy facts."""
    if not opportunity_id:
        return None

    try:
        from aletheia.data.symbols import (
            instrument_analysis_context,
            instrument_analysis_context_from_metadata,
            instrument_background_from_metadata,
            instrument_background_rejection_reason,
        )
        from aletheia.storage.repositories import get_opportunity

        opportunity = get_opportunity(opportunity_id)
        if not opportunity:
            return {"analysis_context": _unavailable_analysis_context(
                "opportunity_missing"
            )}
        raw_metadata = opportunity.get("metadata")
        if isinstance(raw_metadata, str):
            raw_metadata = json.loads(raw_metadata)
        if not isinstance(raw_metadata, dict):
            return {"analysis_context": _unavailable_analysis_context(
                "opportunity_metadata_unavailable"
            )}
        has_context_fields = (
            "instrument_background" in raw_metadata
            or "analysis_context" in raw_metadata
        )
        if not has_context_fields:
            # Legacy Opportunities predate the frozen instrument contract; keep
            # their existing generic attribution behavior readable.
            return None

        background = instrument_background_from_metadata(raw_metadata)
        frozen_context = instrument_analysis_context_from_metadata(raw_metadata)
        physical_market_type = _physical_market_type(market_type)
        requested_exchange = str(exchange or "").strip().lower()
        venue = str((background or {}).get("venue") or "").strip().lower()
        if background is None or frozen_context is None:
            return {"analysis_context": _unavailable_analysis_context(
                "instrument_context_incomplete"
            )}
        if requested_exchange and venue != requested_exchange:
            return {"analysis_context": _unavailable_analysis_context(
                "instrument_context_exchange_conflict"
            )}
        reason = instrument_background_rejection_reason(
            background,
            symbol=symbol,
            market_type=physical_market_type,
            venue=venue or requested_exchange or None,
        )
        resolved_context = instrument_analysis_context(background)
        if reason or resolved_context is None or frozen_context != resolved_context:
            return {"analysis_context": _unavailable_analysis_context(
                reason or "instrument_analysis_context_conflict"
            )}
        return {
            "analysis_context": resolved_context,
            "instrument_background": background,
            "exchange": venue,
            "market_type": physical_market_type,
        }
    except Exception:
        return {"analysis_context": _unavailable_analysis_context(
            "instrument_context_unavailable"
        )}


def capture_attribution_snapshot(
    symbol: str,
    *,
    exchange: str = "",
    market_type: str = "",
    opportunity_id: str | None = None,
) -> dict[str, Any]:
    """Capture best-effort read-only context before Proposal hashing."""
    exact_exchange = str(exchange or "").strip().lower()
    physical_market_type = _physical_market_type(market_type)
    resolved_context = _resolve_opportunity_context(
        symbol,
        exchange=exact_exchange,
        market_type=market_type,
        opportunity_id=opportunity_id,
    )
    context_bound = resolved_context is not None
    analysis_context = (
        resolved_context.get("analysis_context") if resolved_context else None
    )
    if resolved_context and resolved_context.get("exchange"):
        exact_exchange = resolved_context["exchange"]
        physical_market_type = resolved_context["market_type"]
    background = resolved_context.get("instrument_background") if resolved_context else None
    benchmark_applicable = not context_bound or (
        isinstance(analysis_context, dict)
        and analysis_context.get("kind") == "crypto"
    )
    snapshot: dict[str, Any] = {
        "btc_entry_price": _btc_price_now() if benchmark_applicable else None,
        "regime_tag": _current_regime() if benchmark_applicable else None,
    }
    market_context: dict[str, Any] = {
        "instrument": {
            "symbol": symbol,
            "exchange": exact_exchange,
            "market_type": physical_market_type,
        },
    }
    if analysis_context is not None:
        market_context["analysis_context"] = analysis_context
    if background is not None:
        market_context["instrument_background"] = background
    try:
        from aletheia.data.context_builder import build_market_context_from_db

        context = build_market_context_from_db(
            [symbol],
            exchange=exact_exchange,
            market_type=physical_market_type,
        )
        market_context.update({
            "data_completeness": context.data_completeness,
            "tradability": context.tradability.get(symbol, {}),
            "leverage": context.leverage.get(symbol, {}),
        })
    except Exception:
        # Attribution snapshot proceeds without data_completeness/tradability/
        # leverage when the market-context builder fails — the snapshot is a
        # proposal-time audit artefact, not decision input, so omission is
        # tolerable. Bare ``pass`` however hid every real defect
        # (schema drift, DB unavailable, context_builder bug) as "no context
        # today". Log with a traceback so a regression surfaces without
        # changing the snapshot shape or downstream behaviour.
        logger.error(
            "Attribution snapshot market_context builder failed for %s (proceeding without context)",
            symbol,
            exc_info=True,
        )
    snapshot["market_context"] = market_context
    return snapshot


def capture_attribution_snapshot_json(
    symbol: str,
    *,
    exchange: str = "",
    market_type: str = "",
    opportunity_id: str | None = None,
) -> str | None:
    try:
        return json.dumps(
            capture_attribution_snapshot(
                symbol,
                exchange=exchange,
                market_type=market_type,
                opportunity_id=opportunity_id,
            ),
            default=str,
        )
    except Exception:
        # Attribution snapshot is a proposal-time diagnostic — a failure here
        # is legitimate (missing market data, DB unavailable) and consumers
        # already handle a ``None`` return via ``str | None``. But a bare
        # ``return None`` loses the reason; any regression in
        # capture_attribution_snapshot or json.dumps would be invisible.
        # Log with a traceback so a real defect surfaces without changing
        # the caller-visible contract.
        logger.error(
            "Attribution snapshot capture failed for %s (returning None)",
            symbol,
            exc_info=True,
        )
        return None
