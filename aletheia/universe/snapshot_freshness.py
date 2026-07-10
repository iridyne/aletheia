"""Market-type-safe freshness checks for universe candidates.

Spot and perpetual instruments share the canonical symbol (for example
``BTC/USDT``), so a fresh snapshot for one must never make the other tradable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aletheia.storage.repositories import list_market_snapshots


def has_fresh_market_snapshot(
    symbol: str,
    market_type: str | None,
    *,
    max_age_hours: int = 2,
    now: datetime | None = None,
) -> bool:
    """Return whether this exact instrument has a recent price snapshot.

    There is deliberately no symbol-only fallback. A fresh spot quote cannot
    establish that a perpetual contract is executable, or vice versa.
    """
    storage_market_type = _snapshot_market_type(market_type)
    if not symbol or not storage_market_type:
        return False
    snapshots = list_market_snapshots(
        symbol=symbol,
        market_type=storage_market_type,
        limit=1,
    )
    if not snapshots:
        return False
    snapshot_at = snapshots[0].get("snapshot_at") or ""
    try:
        observed_at = datetime.fromisoformat(snapshot_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return observed_at >= reference - timedelta(hours=max_age_hours)


def _snapshot_market_type(market_type: str | None) -> str | None:
    """Map execution/universe market names to market_snapshots vocabulary."""
    normalized = str(market_type or "").strip().lower()
    if normalized == "usdt_futures":
        return "perp"
    if normalized in {"spot", "perp", "futures"}:
        return normalized
    return None
