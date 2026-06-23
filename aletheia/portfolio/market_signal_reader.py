"""Read market signals from DB for position-aware exit decisions.

Pure DB read — no LLM, no network. Safe to call from the io executor thread.
Consumes Market Observer output (opportunities table) + universe features
to build a lightweight signal context per symbol.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("aletheia.portfolio.market_signal_reader")


@dataclass
class MarketSignalContext:
    symbol: str
    direction_bias: str | None = None
    confidence_score: int | None = None
    signal_age_min: float | None = None
    rsi_14: float | None = None
    return_1h: float | None = None
    return_4h: float | None = None
    atr_pct: float | None = None


def get_market_signals_for_positions(
    symbols: list[str],
    max_signal_age_min: float = 35.0,
) -> dict[str, MarketSignalContext]:
    """Batch-fetch the latest market signal for each held symbol.

    Queries the most recent opportunity per symbol (within age window) and
    augments with universe_feature_snapshots data (RSI, momentum, ATR).
    """
    if not symbols:
        return {}

    result: dict[str, MarketSignalContext] = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_signal_age_min)).isoformat()

    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        placeholders = ", ".join("?" * len(symbols))
        rows = db.execute(
            f"""
            SELECT symbol, direction_bias, confidence_score, created_at
            FROM opportunities
            WHERE symbol IN ({placeholders})
              AND status = 'open'
              AND created_at >= ?
              AND asset_class = 'crypto'
            ORDER BY created_at DESC
            """,
            (*symbols, cutoff),
        ).fetchall()

        seen: set[str] = set()
        now = datetime.now(timezone.utc)
        for row in rows:
            sym = row["symbol"]
            if sym in seen:
                continue
            seen.add(sym)
            created = row["created_at"]
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age = (now - dt).total_seconds() / 60.0
            except (ValueError, TypeError):
                age = None
            result[sym] = MarketSignalContext(
                symbol=sym,
                direction_bias=row["direction_bias"],
                confidence_score=row["confidence_score"],
                signal_age_min=round(age, 1) if age is not None else None,
            )
    except Exception as e:
        logger.debug("opportunity query failed: %s", e)

    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        for sym in symbols:
            ctx = result.get(sym) or MarketSignalContext(symbol=sym)
            snap = db.execute(
                "SELECT uf.rsi_14, uf.return_1h, uf.return_4h, uf.atr_pct, uf.funding_rate "
                "FROM universe_feature_snapshots uf "
                "JOIN universe_members um ON um.asset_id = uf.asset_id "
                "WHERE um.symbol = ? ORDER BY uf.ts DESC LIMIT 1",
                (sym,),
            ).fetchone()
            if snap:
                ctx.rsi_14 = snap["rsi_14"]
                ctx.return_1h = snap["return_1h"]
                ctx.return_4h = snap["return_4h"]
                ctx.atr_pct = snap["atr_pct"]
            result[sym] = ctx
    except Exception as e:
        logger.debug("feature snapshot query failed: %s", e)

    return result
