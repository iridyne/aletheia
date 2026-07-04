"""Enriched trading context builder for System Operator decisions.

Assembles a three-layer context snapshot:
  Layer 1 — Environment Snapshot (deterministic, read-only, fresh each call)
  Layer 2 — Short-term Memory (7-day sliding window)
  Layer 3 — Long-term Wisdom (lessons + strategy performance)

Token budget target: ~2200 tokens total (vs ~800 in the old thin context).
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("aletheia.agent_shell.trading_context")


def _dedupe_symbol_direction(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Keep the first item for each symbol/direction pair."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None]] = set()
    for item in items:
        key = (item.get("symbol"), item.get("direction"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _excluded_candidates_summary(items: list[dict[str, Any]], *, examples_per_reason: int = 3) -> list[dict[str, Any]]:
    """Summarize skipped ranker candidates without letting them occupy prompt slots."""
    counts: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        raw_reason = str(item.get("skip_reason") or "unknown")
        reason = _candidate_skip_bucket(raw_reason)
        counts[reason] += 1
        bucket_examples = examples.setdefault(reason, [])
        if len(bucket_examples) < examples_per_reason:
            bucket_examples.append({
                "symbol": item.get("symbol"),
                "direction": item.get("direction"),
                "trade_score": item.get("trade_score"),
                "scanner_family": item.get("scanner_family"),
                "skip_reason": raw_reason,
            })
    return [
        {"reason": reason, "count": count, "examples": examples.get(reason, [])}
        for reason, count in counts.most_common()
    ]


def _candidate_skip_bucket(reason: str) -> str:
    if reason == "okx_demo_symbol_not_supported" or "demo_symbol_not_supported" in reason:
        return "unsupported_demo_symbol"
    if reason.startswith("guard_blocked:"):
        return "guard_blocked"
    if reason.startswith("confidence_below_"):
        return "confidence_below"
    return reason or "unknown"


def _record_candidate_supply_snapshot(layer: dict[str, Any]) -> None:
    """Record candidate supply telemetry for recommendations without changing decisions."""
    try:
        from aletheia.analytics.candidate_supply import build_candidate_supply_snapshot

        snapshot = build_candidate_supply_snapshot(
            executable_candidates=layer.get("executable_candidates") or [],
            excluded_candidates_summary=layer.get("excluded_candidates_summary") or [],
        )
    except Exception as exc:
        logger.debug("candidate supply snapshot build failed: %s", exc)
        layer["candidate_supply"] = {
            "executable_count": len(layer.get("executable_candidates") or []),
            "below_floor": None,
            "main_reason": "insufficient_data",
        }
        return

    layer["candidate_supply"] = snapshot
    try:
        from aletheia.agent_shell.operator_store import create_event

        event_id = create_event(
            event_type="candidate_supply_snapshot",
            severity="warning" if snapshot.get("below_floor") else "info",
            source="trading_context",
            related_entity_type="candidate_supply",
            related_entity_id="system_operator",
            payload=snapshot,
        )
        snapshot["event_id"] = event_id
    except Exception as exc:
        logger.debug("candidate supply snapshot skipped: %s", exc)
        snapshot["event_status"] = "not_persisted"


def build_enriched_trading_context() -> dict[str, Any]:
    """Build the three-layer context for System Operator trading decisions.

    Returns a dict with keys: layer1, layer2, layer3, each containing
    pre-formatted data ready for prompt injection.
    """
    return {
        "layer1": _build_layer1_snapshot(),
        "layer2": _build_layer2_short_term(),
        "layer3": _build_layer3_wisdom(),
    }


# ── Layer 1: Environment Snapshot ────────────────────────────────────────────


def _top_observations(raw, n: int = 2) -> list[str]:
    """Parse the key_observations JSON blob → first n data-cited bullets.

    market_observer stores its most specific LLM output (numeric citations like
    "1h RSI 68, price > SMA20") in opportunities.key_observations, but the PI
    layer1 snapshot historically dropped it — the PI saw only a truncated thesis.
    Surfacing a couple of observations restores the citations for the decision.
    """
    if not raw:
        return []
    try:
        import json as _json
        obs = _json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(obs, list):
            return [str(x) for x in obs[:n] if x]
    except Exception:
        pass
    return []


def _build_layer1_snapshot() -> dict[str, Any]:
    """Current market + portfolio state. Fresh each call, never cached."""
    layer: dict[str, Any] = {}

    # Portfolio: open positions
    try:
        from aletheia.storage.repositories import list_positions
        open_pos = list_positions(status="open", limit=10)
        layer["positions"] = [
            {
                "id": p.get("id"),
                "symbol": p.get("symbol"),
                "direction": p.get("direction"),
                "quantity": p.get("quantity"),
                "entry_price": p.get("entry_price"),
                "current_price": p.get("current_price"),
                "unrealized_pnl": p.get("unrealized_pnl"),
                "hold_hours": _hours_since(p.get("opened_at")),
                "proposal_id": p.get("proposal_id"),
                "opportunity_id": p.get("opportunity_id"),
                "strategy_id": p.get("strategy_id"),
                "trade_school": p.get("trade_school"),
                # Futures telemetry for the leverage/margin context + near-liquidation alerts.
                "leverage": p.get("leverage"),
                "liquidation_price": p.get("liquidation_price"),
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

    # Today's realized PnL (scoped to active execution mode so PI sees
    # the same figure as the paper dashboard, not a cross-mode total).
    try:
        from aletheia.portfolio.pnl import today_realized_pnl
        from aletheia.config import load_config as _load_cfg
        _mode = _load_cfg().exchange.paper_execution_mode or "paper_internal"
        layer["today_pnl"] = round(today_realized_pnl(execution_mode=_mode), 2)
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

    # Open opportunities (what market_observer has flagged — System Operator was blind to these)
    try:
        from aletheia.storage.repositories import _select_all
        opps = _select_all(
            "opportunities",
            where="status = 'open' AND asset_class = 'crypto'",
            order="created_at DESC",
            limit=10,
        )
        # Thesis evidence (P1a 2026-07-02): per-(scanner_family, direction) live edge
        # from settled forward outcomes, so the PI can weigh each candidate against
        # the actual track record of its scanner family + direction — the missing
        # decision-time signal the learning loop already computed but never surfaced.
        _edge_map: dict[tuple, dict] = {}
        try:
            from aletheia.storage.repositories import family_direction_edge
            for _e in family_direction_edge(horizon="24h", within_days=7):
                _edge_map[(_e["scanner_family"], _e["direction"])] = {
                    "n": _e["n"], "win_rate": _e["win_rate"], "avg_return": _e["avg_return"],
                }
        except Exception:
            _edge_map = {}

        def _family_edge_for(o: dict) -> dict | None:
            fam = o.get("scanner_family")
            d = (o.get("direction_bias") or "long")
            if d not in ("long", "short"):
                d = "long"
            return _edge_map.get((fam, d))

        layer["opportunities"] = [
            {
                # R0 fix: carry the opportunity id so the System Operator's pick can be
                # matched back to its source opportunity and the proposal records
                # a real opportunity_id (not None). Without this the whole
                # attribution chain is severed (LEARNING_LOOP_AUDIT_2026-06-26 §R0).
                "id": o.get("id"),
                "linked_strategy_id": o.get("linked_strategy_id"),
                "symbol": o.get("symbol"),
                # Instrument type (spot | usdt_futures) so the PI sees what the
                # opportunity is for and can choose market_type informedly, and so
                # _attach_opportunity_context can inherit it (no hardcoded perp).
                "market": o.get("market"),
                "direction_bias": o.get("direction_bias"),
                "confidence": o.get("confidence"),
                "confidence_score": o.get("confidence_score"),
                "scanner_family": o.get("scanner_family"),
                # Per-(scanner_family, direction) live edge from settled forward
                # outcomes (thesis evidence). None when the family has no settled
                # outcomes in the 7d window yet.
                "family_edge": _family_edge_for(o),
                "thesis": (o.get("thesis") or "")[:220],
                # market_observer's LLM writes its most specific, data-cited output
                # here; the PI was previously blind to it (only the truncated thesis
                # surfaced). Expose the top observations so the decision can cite them.
                "key_observations": _top_observations(o.get("key_observations")),
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
            # kill_switch_active lives on RuntimeConfig, not RiskConfig. The old
            # cfg.risk.kill_switch_active raised AttributeError, silently caught
            # below → the entire risk block was {} and the prompt showed
            # "max_daily_loss=?%, max_positions=?", blinding the System Operator to its
            # own risk limits (2026-06-23 profit-loop audit, freeze exception).
            "kill_switch": cfg.runtime.kill_switch_active,
            "max_daily_loss_pct": cfg.risk.max_daily_loss_pct,
            "max_open_positions": cfg.risk.max_open_positions_demo,
        }
    except Exception:
        layer["risk"] = {}

    # Data health — lets System Operator reason about its own environment quality
    layer["data_health"] = _get_data_health_summary()

    # Futures: leverage ceilings + account leverage ratio + margin headroom so the
    # System Operator chooses spot/perp and a leverage level against real limits.
    try:
        from aletheia.config.loader import load_config
        layer["futures"] = _build_futures_context(layer, load_config())
    except Exception:
        layer["futures"] = {}

    return layer


def _build_futures_context(layer: dict[str, Any], cfg) -> dict[str, Any]:
    """Leverage limits + account leverage ratio + margin headroom for the System Operator.

    Best-effort: any failure yields a partial/empty dict so the decision path never
    breaks on missing futures telemetry. Symbols come from the already-built feature +
    position lists in ``layer`` (no extra query). ``available_margin_pct`` is the share
    of equity NOT tied up as futures margin (used_margin = Σ notional/leverage).
    """
    fut: dict[str, Any] = {}
    try:
        from aletheia.trading.instruments import get_asset_tier, get_max_leverage
        symbols: list[str] = []
        for f in (layer.get("features") or []):
            s = f.get("symbol")
            if s and s not in symbols:
                symbols.append(s)
        for p in (layer.get("positions") or []):
            s = p.get("symbol")
            if s and s not in symbols:
                symbols.append(s)
        fut["leverage_limits"] = {
            s: {
                "tier": get_asset_tier(s),
                "max_demo": get_max_leverage(s, False, cfg),
                "max_live": get_max_leverage(s, True, cfg),
            }
            for s in symbols[:12]
        }
    except Exception:
        fut["leverage_limits"] = {}

    try:
        from aletheia.risk.engine import check_account_leverage
        acct = check_account_leverage(cfg)
        ratio = acct.get("ratio")
        fut["account_leverage_ratio"] = round(ratio, 2) if ratio is not None else None
        fut["account_leverage_cap"] = acct.get("cap")
    except Exception:
        fut["account_leverage_ratio"] = None
        fut["account_leverage_cap"] = None

    # Available margin %: equity not consumed by futures margin (notional / leverage).
    try:
        balance = layer.get("balance") or {}
        equity = balance.get("equity_usdt")
        used_margin = 0.0
        for p in (layer.get("positions") or []):
            qty = float(p.get("quantity") or 0)
            price = float(p.get("current_price") or p.get("entry_price") or 0)
            lev = float(p.get("leverage") or 1) or 1.0
            used_margin += abs(qty * price) / lev
        if equity and equity > 0:
            fut["available_margin_pct"] = round(max(0.0, 1.0 - used_margin / equity) * 100, 1)
        else:
            fut["available_margin_pct"] = None
    except Exception:
        fut["available_margin_pct"] = None

    # Positions within 15% of their estimated liquidation price.
    near: list[dict] = []
    try:
        for p in (layer.get("positions") or []):
            liq = p.get("liquidation_price")
            cur = p.get("current_price") or p.get("entry_price")
            if not liq or not cur:
                continue
            gap_pct = abs(cur - liq) / cur * 100
            if gap_pct <= 15.0:
                near.append({
                    "symbol": p.get("symbol"),
                    "liquidation_price": liq,
                    "gap_pct": round(gap_pct, 1),
                })
    except Exception:
        pass
    fut["positions_near_liquidation"] = near

    return fut


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
        # Exclude one-time technical/reconcile closes (e.g. units_bug_cleanup —
        # the 3 contractSize-inflated positions force-closed 2026-06-23). These
        # are migration debt, NOT strategy performance; counting them poisoned the
        # 7d stats (total_pnl -13.46 vs real +9.80, worst -23.13 vs real -6.95),
        # making the System Operator see a phantom crisis and skip every signal.
        _NON_STRATEGY_CLOSE_REASONS = {"units_bug_cleanup", "reconciled_gone"}
        recent = [
            p for p in closed
            if (p.get("closed_at") or "") >= cutoff
            and p.get("close_reason") not in _NON_STRATEGY_CLOSE_REASONS
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
        # strategy_records column is `state`, not `status` — the old s.get("status")
        # always returned None so the filter matched nothing and the System Operator saw
        # "(none active)" while 4 strategies were paper_running (2026-06-23 audit).
        active = [s for s in strategies if s.get("state") in ("paper_running", "active")]
        # S-Bug-C (LEARNING_LOOP_AUDIT): strategy_records has NO win_rate/total_pnl/
        # trade_count columns, so the System Operator saw None/'?' for every strategy. Real
        # KPIs come from build_strategy_kpis() (computed live from fills + positions).
        from aletheia.analytics.trade_stats import build_strategy_kpis
        kpis = build_strategy_kpis()  # {strategy_id: {trade_count, win_rate, total_pnl}}
        strat_perf: list[dict] = []
        for strat in active[:5]:
            sid = strat.get("id")
            k = kpis.get(sid, {}) if sid else {}
            strat_perf.append({
                "name": strat.get("name", "unnamed"),
                "win_rate": k.get("win_rate"),
                "total_pnl": k.get("total_pnl"),
                "trade_count": k.get("trade_count", 0),
            })
        layer["strategies"] = strat_perf
    except Exception:
        layer["strategies"] = []

    # Trades & Positions analytics — the same closed-position analytics surfaced
    # in the UI, compressed for System Operator consumption.
    try:
        from aletheia.analytics.trade_stats import build_trade_stats
        trade_stats = build_trade_stats()
        layer["trade_analytics"] = {
            "totals": trade_stats.get("totals", {}),
            "by_source": (trade_stats.get("by_source") or [])[:4],
            "by_family": sorted(
                trade_stats.get("by_family") or [],
                key=lambda x: x.get("trade_count", 0),
                reverse=True,
            )[:6],
            "hold_distribution": trade_stats.get("hold_distribution") or [],
        }
    except Exception:
        layer["trade_analytics"] = None

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
        ranker_candidates = [
            {
                "opportunity_id": c.get("opportunity_id"),
                "signal_source": "market_observer",
                "symbol": c.get("symbol"),
                "direction": c.get("direction"),
                "trade_score": c.get("trade_score"),
                "skip_reason": c.get("skip_reason"),
                "scanner_family": c.get("scanner_family"),
                "strategy_id": c.get("strategy_id") or c.get("linked_strategy_id"),
                "market_type": c.get("market_type") or c.get("market"),
            }
            for c in candidates
        ]
        executable_candidates = [c for c in ranker_candidates if not c.get("skip_reason")]
        excluded_candidates = [c for c in ranker_candidates if c.get("skip_reason")]
        layer["executable_candidates"] = _dedupe_symbol_direction(executable_candidates, limit=8)
        layer["excluded_candidates_summary"] = _excluded_candidates_summary(excluded_candidates)
        # Compatibility alias for older consumers; new prompt rendering should use
        # executable_candidates so skipped rows cannot consume the main candidate slots.
        layer["ranker_candidates"] = layer["executable_candidates"]
    except Exception:
        layer["executable_candidates"] = []
        layer["excluded_candidates_summary"] = []
        layer["ranker_candidates"] = []
    _record_candidate_supply_snapshot(layer)

    # Phase 4b: Pending signals awaiting System Operator evaluation
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        rows = db.execute(
            "SELECT symbol, direction, reasoning, created_at, opportunity_id, signal_source "
            "FROM pi_decisions "
            "WHERE action = 'signal_pending' AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT 5",
            (cutoff,),
        ).fetchall()
        pending_signals = [
            {
                "symbol": r[0], "direction": r[1], "reasoning": r[2][:100],
                "at": r[3], "opportunity_id": r[4], "signal_source": r[5],
            }
            for r in rows
        ]
        layer["pending_signals"] = _dedupe_symbol_direction(pending_signals, limit=5)
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
        # L-Bug-A (LEARNING_LOOP_AUDIT): the old filter ('lesson','accepted_lesson',
        # 'trading_guard') matched ZERO rows — none of those item_types have a
        # producer, so Layer3 lessons was permanently empty. Real attributed lessons
        # live in trade_case_study (closed-trade cases with full entry→exit chain,
        # which the R0 opportunity_id fix now lets complete). Query by item_type
        # directly so the limit window isn't swallowed by high-frequency rows.
        case_items = list_knowledge_items(item_type="trade_case_study", limit=7)
        lessons: list[dict] = []
        for item in case_items:
            meta = item.get("metadata") or {}
            raw_content = item.get("content") or item.get("summary") or ""
            if not raw_content:
                # L-Bug-D fix: DB content=NULL; actual content lives on disk at file_path.
                # Read frontmatter (outcome/pnl/factor_key) + body prefix to give PI real data.
                fpath = item.get("file_path")
                if fpath:
                    try:
                        from pathlib import Path as _Path
                        from aletheia.knowledge.frontmatter import parse_frontmatter as _pfm
                        _meta, _body = _pfm(_Path(fpath))
                        parts: list[str] = []
                        if _meta:
                            if _meta.get("outcome"):
                                parts.append(
                                    f"outcome={_meta['outcome']} pnl={_meta.get('realized_pnl_pct', '?')}%"
                                )
                            if _meta.get("factor_key"):
                                parts.append(
                                    f"factor={_meta['factor_key']} scanner={_meta.get('scanner_family', '?')}"
                                )
                            if _meta.get("close_reason"):
                                parts.append(f"close={_meta['close_reason']}")
                        if _body:
                            parts.append(_body[:150].strip())
                        raw_content = " | ".join(parts) if parts else (item.get("title") or "")
                    except Exception:
                        raw_content = item.get("title") or ""
            lessons.append({
                "type": item.get("item_type"),
                "symbol": meta.get("symbol"),
                "content": raw_content[:300],
                "source": meta.get("source_trade"),
            })
        layer["lessons"] = lessons
    except Exception:
        layer["lessons"] = []

    # Recent guard candidates (from self-evolution Tier 1)
    try:
        from aletheia.storage.repositories import list_knowledge_items
        # L-Bug-A' (LEARNING_LOOP_AUDIT): the old list_knowledge_items(limit=30)
        # scanned the whole table newest-first, and that window was permanently
        # occupied by high-frequency system_action rows, so guard_candidate never
        # appeared. Query by item_type directly.
        items = list_knowledge_items(item_type="guard_candidate", limit=3)
        guards: list[dict] = []
        for item in items:
            meta = item.get("metadata") or {}
            raw_content = item.get("content") or item.get("summary") or item.get("title") or ""
            if not raw_content:
                # Phase 3b: guard_candidate content lives on disk; DB content may be NULL.
                fpath = item.get("file_path")
                if fpath:
                    try:
                        from pathlib import Path as _Path
                        from aletheia.knowledge.frontmatter import parse_frontmatter as _pfm
                        _meta, _body = _pfm(_Path(fpath))
                        parts: list[str] = []
                        if _meta:
                            if _meta.get("outcome"):
                                parts.append(f"outcome={_meta['outcome']} return={_meta.get('forward_return_pct', '?')}%")
                            if _meta.get("scanner_family"):
                                parts.append(f"scanner={_meta['scanner_family']}")
                        if _body:
                            parts.append(_body[:150].strip())
                        raw_content = " | ".join(parts) if parts else (item.get("title") or "")
                    except Exception:
                        raw_content = item.get("title") or ""
            guards.append({
                "symbol": meta.get("symbol"),
                "content": raw_content[:200],
            })
        layer["guard_candidates"] = guards
    except Exception:
        layer["guard_candidates"] = []

    # Serenity research opportunities (most recent active, for System Operator consumption)
    try:
        from aletheia.storage.repositories import (
            list_serenity_evidence,
            list_serenity_opportunities,
            list_serenity_risk_gates,
        )
        from aletheia.trading.serenity_scoring import score_serenity_opportunity

        opps = list_serenity_opportunities(status="active", limit=10)
        serenity_opps: list[dict] = []
        for o in opps[:5]:
            # Phase 2: compute the deterministic score/bucket the same way the
            # Serenity scorer does, so the PI weighs a research pick on a transparent
            # scale rather than a bare thesis. Evidence/gates are best-effort: a scoring
            # failure must not blank out the whole opportunity for the PI.
            score: dict = {}
            key_evidence = ""
            try:
                evidence = list_serenity_evidence(theme_id=o.get("theme_id"), limit=200)
                gates = list_serenity_risk_gates(opportunity_id=o.get("id"), limit=200)
                score = score_serenity_opportunity(o, evidence=evidence, risk_gates=gates)
                # Summarise the strongest supporting evidence's external reference so the
                # PI can see WHAT grounds the thesis, not just that evidence exists.
                refs = [
                    (e.get("source_ref") or e.get("url") or e.get("title") or "").strip()
                    for e in evidence
                    if e and (e.get("source_ref") or e.get("url") or e.get("title"))
                ]
                key_evidence = "; ".join(refs[:2])[:200]
            except Exception:
                pass
            # S4: parse verdict_json for PI consumption (forced verdict).
            try:
                import json as _json
                verdict = _json.loads(o.get("verdict_json") or "{}")
            except Exception:
                verdict = {}
            _asset_class = o.get("asset_class") or "crypto"
            _market_type = o.get("market_type") or "spot"
            # EQ-R (2026-07-01): pure-observation equity tokens (equity_observer)
            # are NOT execution candidates. Serenity-produced "equity_tradeable"
            # stock tokens ARE eligible — they passed the research pipeline.
            _execution_eligible = (_asset_class not in ("equity",))
            serenity_opps.append({
                # Phase 2: attribution anchor — without the id the PI's pick can never be
                # linked back to serenity_opportunities (signal_source='serenity').
                "id": o.get("id"),
                "symbol": o.get("symbol"),
                "direction": o.get("direction"),
                "thesis": (o.get("thesis") or "")[:200],
                "confidence_score": o.get("confidence_score"),
                # D2: PI must judge on the INDEPENDENTLY assessed tier, not the generator's
                # self-grade. `assessed_tier` NULL (legacy/unassessed) → "unassessed" so PI
                # treats it as ungraded rather than trusting an inflated proposal.
                "assessed_tier": o.get("assessed_tier") or "unassessed",
                "evidence_tier": o.get("evidence_tier"),  # generator's self-grade (advisory)
                "score": score.get("score"),
                "bucket": score.get("bucket"),
                "evidence_count": score.get("evidence_count", 0),
                "key_evidence": key_evidence,
                # S4: forced verdict (AI Berkshire integration) — recommendation +
                # explicit bull/bear cases + key_assumption + invalidation condition.
                "verdict": verdict,
                # S2: researchability grade (A/B/C) — informs PI of data quality.
                "researchability_grade": o.get("researchability_grade") or "C",
                # EQ-R: instrument classification for execution routing and display.
                # execution_eligible=False means PI treats this as research context only.
                "asset_class": _asset_class,
                "market_type": _market_type,
                "execution_eligible": _execution_eligible,
            })
        layer["serenity_opportunities"] = serenity_opps
    except Exception:
        layer["serenity_opportunities"] = []

    # L-Bug-B fix: inject APPLIED lessons (evidence-reviewed + policy-stamped),
    # not proposed (unreviewed raw output). Status flow: proposed → accepted → applied.
    # lesson_reviewer accepts then immediately marks_applied in the same run, so
    # 'accepted' is a transient state; 'applied' is the stable post-review population.
    # These lessons have passed: deterministic floor (≥20 settled outcomes) + LLM judge.
    try:
        from aletheia.learning.lessons import LessonStore
        ps = LessonStore().list_all(status="applied", limit=5)
        layer["ps_lessons"] = [
            {"summary": l.summary, "severity": l.severity, "category": l.category}
            for l in ps
        ]
    except Exception:
        layer["ps_lessons"] = []

    return layer


# ── data health ─────────────────────────────────────────────────────────────


def _get_data_health_summary() -> dict[str, Any]:
    """Build a concise data-health dict for System Operator context injection."""
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
                      SUM(CASE
                        WHEN direction = 'short' AND forward_return_pct < 0 THEN 1
                        WHEN direction = 'long' AND forward_return_pct > 0 THEN 1
                        ELSE 0
                      END)
               FROM universe_forward_outcomes
               WHERE status = 'settled' AND horizon = '24h'
                 AND settled_at >= ? AND ABS(forward_return_pct) <= 100
                 AND direction IN ('long', 'short')""",
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
