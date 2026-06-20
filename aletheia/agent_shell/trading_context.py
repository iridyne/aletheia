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
            features.append({
                "symbol": sym,
                "price": s.get("price"),
                "price_source": price_meta.get("provider") if price_meta else None,
                "rsi_14": s.get("rsi_14"),
                "atr_pct": s.get("atr_pct"),
                "return_1h": s.get("return_1h"),
                "return_4h": s.get("return_4h"),
                "return_24h": s.get("return_24h"),
                "funding_rate": s.get("funding_rate"),
                "oi_delta_1h": s.get("oi_delta_1h"),
                "btc_beta": s.get("btc_beta"),
                "volume_24h": s.get("quote_volume_24h"),
            })
            if len(features) >= 10:
                break
        layer["features"] = features
    except Exception:
        layer["features"] = []

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

    return layer


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
