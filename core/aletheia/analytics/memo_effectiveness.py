"""Pure age weighting for persisted trading-memo rendering."""
from __future__ import annotations

from datetime import datetime, timezone
from math import exp, log

MEMO_HALF_LIFE_DAYS = 7.0


def memo_half_life_weight(generated_at: str | None, *, now: datetime | None = None, half_life_days: float = MEMO_HALF_LIFE_DAYS) -> float:
    """Return a 0..1 influence weight for a trading memo by age.

    A fresh memo has weight 1.0. Every half_life_days the influence halves, so
    stale regime guidance fades even when no explicit overturn signal exists.
    """
    if not generated_at:
        return 1.0
    try:
        ts = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    except ValueError:
        return 1.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
    if half_life_days <= 0:
        return 0.0
    return round(exp(-log(2) * age_days / half_life_days), 4)
