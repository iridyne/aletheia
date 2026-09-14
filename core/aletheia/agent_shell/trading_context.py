"""Read-only market data health for Operator governance and scorecards."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("aletheia.agent_shell.trading_context")


def _get_data_health_summary() -> dict[str, Any]:
    """Build a concise data-health dict for System Operator context injection."""
    try:
        from aletheia.config.loader import load_config
        from aletheia.storage.repositories import (
            complete_outcome_economics_sql,
            get_active_universe_members,
            get_universe_asset,
        )
        from aletheia.storage.sqlite import get_db
        from aletheia.universe.outcome_economics import COST_MODEL_VERSION

        cfg = load_config()
        min_bars = cfg.historical_data.min_bars_for_backtest
        db = get_db()

        members = get_active_universe_members()
        total_members = len(members)
        ready_count = 0
        for m in members:
            sym = m.get("symbol", "")
            if not sym:
                continue
            asset = get_universe_asset(m.get("asset_id")) or {}
            venue = asset.get("venue", "")
            market_type = m.get("market_type") or asset.get("market_type", "")
            bars = db.execute(
                "SELECT COUNT(*) FROM market_ohlcv "
                "WHERE exchange=? AND symbol=? AND market_type=? AND timeframe='1h'",
                (venue, sym, market_type),
            ).fetchone()[0]
            if bars >= min_bars:
                ready_count += 1

        ohlcv_ready = ready_count == total_members and total_members > 0

        # Signal effectiveness (7d, 24h horizon)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        row = db.execute(
            f"""SELECT COUNT(*),
                      SUM(CASE WHEN net_return_pct > min_tradable_edge_pct
                               THEN 1 ELSE 0 END)
               FROM universe_forward_outcomes
               WHERE status = 'settled' AND horizon = '24h'
                 AND settled_at >= ?
                 AND cost_model_version = ?
                 AND {complete_outcome_economics_sql()}
                 AND direction IN ('long', 'short')""",
            (cutoff, COST_MODEL_VERSION),
        ).fetchone()
        settled, wins = row if row else (0, None)
        win_rate = round((wins or 0) / settled * 100, 1) if settled else None

        if win_rate is None:
            signal_status = "insufficient_data"
        elif win_rate < 40:
            signal_status = "degraded"
        elif win_rate >= 55:
            signal_status = "healthy"
        else:
            signal_status = "neutral"

        return {
            "ohlcv_ready": ohlcv_ready,
            "members_with_full_coverage": ready_count,
            "total_members": total_members,
            "signal_win_rate_24h": win_rate,
            "signal_status": signal_status,
            "cost_model_version": COST_MODEL_VERSION,
        }
    except Exception as e:
        logger.debug("data_health_summary failed: %s", e)
        return {
            "ohlcv_ready": False,
            "members_with_full_coverage": 0,
            "total_members": 0,
            "signal_win_rate_24h": None,
            "signal_status": "unknown",
            "cost_model_version": "unavailable",
        }
