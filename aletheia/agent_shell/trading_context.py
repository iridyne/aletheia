"""Enriched trading context builder for PI Agent decisions.

Assembles a three-layer context snapshot:
  Layer 1 — Environment Snapshot (deterministic, read-only, fresh each call)
  Layer 2 — Short-term Memory (7-day sliding window)
  Layer 3 — Long-term Wisdom (lessons + strategy performance)

Token budget target: ~2200 tokens total (vs ~800 in the old thin context).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("aletheia.agent_shell.trading_context")


def build_enriched_trading_context() -> dict[str, Any]:
    """Build the three-layer context for PI Agent trading decisions.

    Returns a dict with keys: layer1, layer2, layer3, each containing
    pre-formatted data ready for prompt injection.
    """
    return {
        "layer1": _build_layer1_snapshot(),
        "layer2": _build_layer2_short_term(),
        "layer3": _build_layer3_wisdom(),
    }


# ── Layer 1: Environment Snapshot ────────────────────────────────────────────


def _build_layer1_snapshot() -> dict[str, Any]:
    """Current market + portfolio state. Fresh each call, never cached."""
    layer: dict[str, Any] = {}

    # Portfolio: open positions
    try:
        from aletheia.storage.repositories import list_positions
        open_pos = list_positions(status="open", limit=10)
        layer["positions"] = [
            {
                "symbol": p.get("symbol"),
                "direction": p.get("direction"),
                "quantity": p.get("quantity"),
                "entry_price": p.get("entry_price"),
                "unrealized_pnl": p.get("unrealized_pnl"),
                "hold_hours": _hours_since(p.get("opened_at")),
            }
            for p in open_pos
        ]
    except Exception:
        layer["positions"] = []

    # Account balance
    try:
        from aletheia.portfolio.exchange_sync import get_cached_state
        state = get_cached_state()
        if state and state.get("age_seconds", 999) < 300:
            layer["balance"] = {
                "equity_usdt": state.get("equity_usdt") or state.get("total_equity_usdt"),
                "available_usdt": state.get("available_usdt") or state.get("total_available_usdt"),
            }
        else:
            layer["balance"] = None
    except Exception:
        layer["balance"] = None

    # Today's realized PnL
    try:
        from aletheia.portfolio.pnl import today_realized_pnl
        layer["today_pnl"] = round(today_realized_pnl(), 2)
    except Exception:
        layer["today_pnl"] = None

    # Market regime
    try:
        from aletheia.storage.repositories import get_latest_market_regime
        regime = get_latest_market_regime()
        if regime:
            layer["regime"] = {
                "state": regime.get("state"),
                "confidence": regime.get("confidence"),
                "long_bias": regime.get("long_bias"),
                "short_bias": regime.get("short_bias"),
            }
        else:
            layer["regime"] = None
    except Exception:
        layer["regime"] = None

    # Technical features (latest snapshot per symbol, top 10)
    try:
        from aletheia.data.price_feed import get_cached_price_with_meta
        from aletheia.storage.repositories import list_universe_feature_snapshots
        snaps = list_universe_feature_snapshots(limit=50)
        seen: set[str] = set()
        features: list[dict] = []
        for s in snaps:
            sym = s.get("symbol", "")
            if sym in seen:
                continue
            seen.add(sym)
            price_meta = get_cached_price_with_meta(sym)
            fr = s.get("funding_rate")
            oi_delta = s.get("oi_delta_1h")
            crowding_signal = (
                fr is not None and oi_delta is not None
                and abs(fr) > 0.0005 and abs(oi_delta) > 5
            )
            features.append({
                "symbol": sym,
                "price": s.get("price"),
                "price_source": price_meta.get("provider") if price_meta else None,
                "rsi_14": s.get("rsi_14"),
                "atr_pct": s.get("atr_pct"),
                "return_1h": s.get("return_1h"),
                "return_4h": s.get("return_4h"),
                "return_24h": s.get("return_24h"),
                "funding_rate": fr,
                "oi_delta_1h": oi_delta,
                "btc_beta": s.get("btc_beta"),
                "volume_24h": s.get("quote_volume_24h"),
                "taker_buy_ratio": s.get("taker_buy_ratio"),
                "vol_ratio": s.get("vol_ratio"),
                "relative_strength_24h": s.get("relative_strength_24h"),
                "oi_price_divergence": s.get("oi_price_divergence"),
                "crowding_signal": crowding_signal,
            })
            if len(features) >= 10:
                break
        layer["features"] = features
    except Exception:
        layer["features"] = []

    # Open opportunities (what market_observer has flagged — PI Agent was blind to these)
    try:
        from aletheia.storage.repositories import _select_all
        opps = _select_all(
            "opportunities",
            where="status = 'open' AND asset_class = 'crypto'",
            order="created_at DESC",
            limit=10,
        )
        layer["opportunities"] = [
            {
                "symbol": o.get("symbol"),
                "direction_bias": o.get("direction_bias"),
                "confidence": o.get("confidence"),
                "confidence_score": o.get("confidence_score"),
                "scanner_family": o.get("scanner_family"),
                "thesis": (o.get("thesis") or "")[:150],
            }
            for o in opps
        ]
    except Exception:
        layer["opportunities"] = []

    # Factor feedback status (which scanner+direction combos are blocked/preferred)
    try:
        from aletheia.analytics.factor_feedback import build_factor_feedback
        all_factors = build_factor_feedback(limit=200, min_trades=3)
        layer["factor_feedback"] = [
            {
                "key": f.get("key"),
                "group_type": f.get("group_type"),
                "status": f.get("status"),
                "win_rate": f.get("win_rate"),
                "expectancy": f.get("expectancy"),
                "trade_count": f.get("trade_count"),
            }
            for f in all_factors
            if f.get("status") in ("blocked_candidate", "preferred", "paper_failing")
        ][:10]
    except Exception:
        layer["factor_feedback"] = []

    # Risk state
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        layer["risk"] = {
            "kill_switch": cfg.risk.kill_switch_active,
            "max_daily_loss_pct": cfg.risk.max_daily_loss_pct,
            "max_open_positions": cfg.risk.max_open_positions_demo,
        }
    except Exception:
        layer["risk"] = {}

    # Data health — lets PI Agent reason about its own environment quality
    layer["data_health"] = _get_data_health_summary()

    return layer


# ── Layer 2: Short-term Memory (7-day window) ────────────────────────────────


def _build_layer2_short_term() -> dict[str, Any]:
    """Recent trade outcomes + pattern performance (7-day sliding window)."""
    layer: dict[str, Any] = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    # Reflection: last decision outcome
    try:
        from aletheia.agent_shell.reflection import get_last_reflection
        layer["reflection"] = get_last_reflection()
    except Exception:
        layer["reflection"] = None

    # Recent closed positions — aggregate stats
    try:
        from aletheia.storage.repositories import list_positions
        closed = list_positions(status="closed", limit=200)
        recent = [
            p for p in closed
            if (p.get("closed_at") or "") >= cutoff
        ]

        if recent:
            wins = [p for p in recent if (p.get("realized_pnl") or 0) > 0]
            losses = [p for p in recent if (p.get("realized_pnl") or 0) <= 0]
            total_pnl = sum(p.get("realized_pnl", 0) for p in recent)
            avg_return = total_pnl / len(recent) if recent else 0

            layer["outcome_stats"] = {
                "total_trades": len(recent),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(recent) * 100, 1) if recent else 0,
                "total_pnl": round(total_pnl, 2),
                "avg_pnl_per_trade": round(avg_return, 2),
                "worst_loss": round(min((p.get("realized_pnl", 0) for p in recent), default=0), 2),
                "best_win": round(max((p.get("realized_pnl", 0) for p in recent), default=0), 2),
            }

            # Per-symbol breakdown (top 5 by trade count)
            from collections import Counter
            sym_counts = Counter(p.get("symbol") for p in recent)
            top_syms = [s for s, _ in sym_counts.most_common(5)]
            per_symbol: list[dict] = []
            for sym in top_syms:
                sym_trades = [p for p in recent if p.get("symbol") == sym]
                sym_wins = [p for p in sym_trades if (p.get("realized_pnl") or 0) > 0]
                sym_pnl = sum(p.get("realized_pnl", 0) for p in sym_trades)
                per_symbol.append({
                    "symbol": sym,
                    "trades": len(sym_trades),
                    "wins": len(sym_wins),
                    "pnl": round(sym_pnl, 2),
                })
            layer["per_symbol"] = per_symbol
        else:
            layer["outcome_stats"] = None
            layer["per_symbol"] = []

    except Exception:
        layer["outcome_stats"] = None
        layer["per_symbol"] = []

    # Active strategies performance (7-day)
    try:
        from aletheia.storage.repositories import list_strategies
        strategies = list_strategies(limit=20)
        active = [s for s in strategies if s.get("status") in ("paper_running", "active")]
        strat_perf: list[dict] = []
        for strat in active[:5]:
            strat_perf.append({
                "name": strat.get("name", "unnamed"),
                "win_rate": strat.get("win_rate"),
                "total_pnl": strat.get("total_pnl"),
                "trade_count": strat.get("trade_count"),
            })
        layer["strategies"] = strat_perf
    except Exception:
        layer["strategies"] = []

    # Trading Memo — structured insights from historical performance analysis
    try:
        from aletheia.analytics.insight_synthesizer import get_latest_memo, render_memo_for_observer
        memo = get_latest_memo()
        if memo:
            layer["trading_memo"] = {
                "generated_at": memo.get("generated_at"),
                "sample_size": memo.get("sample_size"),
                "direction_summary": memo.get("direction_summary"),
                "regime_playbook": memo.get("regime_playbook", [])[:3],
                "anti_patterns": memo.get("anti_patterns", [])[:3],
                "key_insight": memo.get("key_insight", ""),
                "rendered": render_memo_for_observer(memo),
            }
        else:
            layer["trading_memo"] = None
    except Exception:
        layer["trading_memo"] = None

    # Reasoning quality feedback — rolling pattern win rates from historical proposals
    try:
        from aletheia.analytics.reasoning_feedback import get_latest_reasoning_feedback, render_feedback_for_pi
        fb = get_latest_reasoning_feedback()
        layer["reasoning_feedback"] = render_feedback_for_pi(fb) if fb else None
    except Exception:
        layer["reasoning_feedback"] = None

    # Conviction calibration — actual vs expected win rate per conviction bucket
    try:
        from aletheia.analytics.confidence_calibration import get_latest_calibration_report, render_calibration_for_pi
        cal = get_latest_calibration_report()
        layer["calibration_feedback"] = render_calibration_for_pi(cal) if cal else None
    except Exception:
        layer["calibration_feedback"] = None

    # Ranker candidates — what paper_trader would pick right now
    try:
        from aletheia.agents.paper_trader_ranker import build_paper_candidates
        candidates = build_paper_candidates(limit=30)
        layer["ranker_candidates"] = [
            {
                "symbol": c.get("symbol"),
                "direction": c.get("direction_bias"),
                "trade_score": c.get("trade_score"),
                "skip_reason": c.get("skip_reason"),
                "scanner_family": c.get("scanner_family"),
            }
            for c in candidates[:8]
        ]
    except Exception:
        layer["ranker_candidates"] = []

    # Phase 4b: Pending signals awaiting PI Agent evaluation
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        rows = db.execute(
            "SELECT symbol, direction, reasoning, created_at FROM pi_decisions "
            "WHERE action = 'signal_pending' AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT 5",
            (cutoff,),
        ).fetchall()
        layer["pending_signals"] = [
            {"symbol": r[0], "direction": r[1], "reasoning": r[2][:100], "at": r[3]}
            for r in rows
        ]
    except Exception:
        layer["pending_signals"] = []

    return layer


# ── Layer 3: Long-term Wisdom ────────────────────────────────────────────────


def _build_layer3_wisdom() -> dict[str, Any]:
    """Lessons + guard candidates — natural language, not truncated."""
    layer: dict[str, Any] = {}

    # Active lessons (full text, not truncated)
    try:
        from aletheia.storage.repositories import list_knowledge_items
        items = list_knowledge_items(limit=20)
        lessons: list[dict] = []
        for item in items:
            if item.get("item_type") in ("lesson", "accepted_lesson", "trading_guard"):
                lessons.append({
                    "type": item.get("item_type"),
                    "symbol": item.get("metadata", {}).get("symbol"),
                    "content": item.get("content", "")[:300],
                    "source": item.get("metadata", {}).get("source_trade"),
                })
                if len(lessons) >= 7:
                    break
        layer["lessons"] = lessons
    except Exception:
        layer["lessons"] = []

    # Recent guard candidates (from self-evolution Tier 1)
    try:
        from aletheia.storage.repositories import list_knowledge_items
        items = list_knowledge_items(limit=30)
        guards: list[dict] = []
        for item in items:
            if item.get("item_type") == "guard_candidate":
                guards.append({
                    "symbol": item.get("metadata", {}).get("symbol"),
                    "content": item.get("content", "")[:200],
                })
                if len(guards) >= 3:
                    break
        layer["guard_candidates"] = guards
    except Exception:
        layer["guard_candidates"] = []

    # Serenity research opportunities (most recent active, for PI Agent consumption)
    try:
        from aletheia.storage.repositories import list_serenity_opportunities
        opps = list_serenity_opportunities(status="active", limit=10)
        serenity_opps: list[dict] = []
        for o in opps[:5]:
            serenity_opps.append({
                "symbol": o.get("symbol"),
                "direction": o.get("direction"),
                "thesis": (o.get("thesis") or "")[:200],
                "confidence_score": o.get("confidence_score"),
                "evidence_tier": o.get("evidence_tier"),
            })
        layer["serenity_opportunities"] = serenity_opps
    except Exception:
        layer["serenity_opportunities"] = []

    return layer


# ── data health ─────────────────────────────────────────────────────────────


def _get_data_health_summary() -> dict[str, Any]:
    """Build a concise data-health dict for PI Agent context injection."""
    try:
        from aletheia.storage.repositories import get_active_universe_members
        from aletheia.storage.sqlite import get_db
        from aletheia.config.loader import load_config

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
            bars = db.execute(
                "SELECT COUNT(*) FROM market_ohlcv WHERE symbol=? AND timeframe='1h'",
                (sym,),
            ).fetchone()[0]
            if bars >= min_bars:
                ready_count += 1

        ohlcv_ready = ready_count == total_members and total_members > 0

        # Signal effectiveness (7d, 24h horizon)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        row = db.execute(
            """SELECT COUNT(*),
                      SUM(CASE WHEN forward_return_pct > 0 THEN 1 ELSE 0 END)
               FROM universe_forward_outcomes
               WHERE status = 'settled' AND horizon = '24h'
                 AND settled_at >= ? AND ABS(forward_return_pct) <= 100""",
            (cutoff,),
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
        }
    except Exception as e:
        logger.debug("data_health_summary failed: %s", e)
        return {
            "ohlcv_ready": False,
            "members_with_full_coverage": 0,
            "total_members": 0,
            "signal_win_rate_24h": None,
            "signal_status": "unknown",
        }


# ── helpers ──────────────────────────────────────────────────────────────────


def _hours_since(iso_str: str | None) -> float | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return round(delta.total_seconds() / 3600, 1)
    except (ValueError, TypeError):
        return None
