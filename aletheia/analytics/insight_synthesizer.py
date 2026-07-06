"""Insight Synthesizer — generates structured TradingMemo from historical performance.

Reads trade_insights + factor_feedback statistics, calls LLM for pattern discovery,
outputs a TradingMemo consumed by:
  - System Operator (System Operator) via trading_context Layer 2
  - market_observer via memory_block injection
  - paper_trader_ranker via scoring adjustments (future phase)

Scheduling: daily at 02:30 CST + event-driven when post_trade_reviewer produces 3+ new case studies.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

logger = logging.getLogger("aletheia.analytics.insight_synthesizer")

# Quality gate constants
MIN_TRADES_FOR_MEMO = 10
MIN_TRADES_FOR_REGIME_RULE = 5
MIN_TRADES_FOR_TIMING_RULE = 8
MIN_TRADES_FOR_SYMBOL_RULE = 3
MAX_MEMO_AGE_HOURS = 72
MAX_DIRECTION_BIAS_STRENGTH = 0.75
MIN_SYNTHESIS_INTERVAL_HOURS = 6


class RegimeRule(TypedDict):
    regime: str
    direction: str
    edge: str  # strong / weak / contra
    win_rate: float
    avg_pnl_pct: float
    sample_size: int


class TimingRule(TypedDict):
    dimension: str  # "hour" or "day"
    value: str  # "08-12" or "Monday"
    quality: str  # best / good / avoid
    win_rate: float
    avg_pnl_pct: float
    sample_size: int


class SymbolRule(TypedDict):
    symbol: str
    status: str  # hot / neutral / cold
    win_rate: float
    pnl: float
    sample_size: int


class AntiPattern(TypedDict):
    pattern: str
    loss_rate: float
    sample_size: int


class TradingMemo(TypedDict):
    generated_at: str
    valid_until: str
    data_window_days: int
    sample_size: int
    regime_playbook: list[RegimeRule]
    timing_windows: list[TimingRule]
    symbol_affinity: list[SymbolRule]
    anti_patterns: list[AntiPattern]
    direction_summary: dict[str, Any]
    key_insight: str  # one-paragraph LLM synthesis
    prior_corrections: list[dict]  # Module J: prior judgments overturned by subsequent results


def get_latest_memo() -> TradingMemo | None:
    """Retrieve the most recent valid TradingMemo from knowledge store."""
    try:
        from aletheia.storage.repositories import list_knowledge_items
        items = list_knowledge_items(item_type="trading_memo", limit=5)
        now = datetime.now(timezone.utc).isoformat()
        for item in items:
            # Check validity via tags JSON (contains valid_until)
            tags_raw = item.get("tags", "")
            try:
                tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
            except (json.JSONDecodeError, TypeError):
                tags = {}
            valid_until = tags.get("valid_until", "") if isinstance(tags, dict) else ""
            if valid_until and valid_until < now:
                continue
            content = item.get("content", "")
            if not content:
                continue
            try:
                return json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue
        return None
    except Exception as e:
        logger.debug("get_latest_memo failed: %s", e)
        return None


def _should_skip_synthesis() -> bool:
    """Rate-limit: don't re-synthesize if a recent memo exists."""
    memo = get_latest_memo()
    if not memo:
        return False
    try:
        gen = datetime.fromisoformat(memo["generated_at"])
        if gen.tzinfo is None:
            gen = gen.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
        return age_hours < MIN_SYNTHESIS_INTERVAL_HOURS
    except (KeyError, ValueError):
        return False


async def synthesize_trading_memo(window_days: int = 30) -> TradingMemo | None:
    """Main entry: build context → LLM synthesis → persist memo.

    Returns the memo dict or None on failure / insufficient data.
    """
    if _should_skip_synthesis():
        logger.info("Skipping synthesis — recent memo exists (< %dh)", MIN_SYNTHESIS_INTERVAL_HOURS)
        return None

    # Module J (refutable self-evolution): score the PRIOR memo against what
    # actually happened while it was active BEFORE we build the new one, so the
    # new memo can carry forward "this earlier judgment was overturned".
    prior_corrections: list[dict] = []
    try:
        from aletheia.analytics.memo_effectiveness import evaluate_prior_memo
        prior_eval = evaluate_prior_memo()
        if prior_eval and prior_eval.get("overturned_rules"):
            prior_corrections = prior_eval["overturned_rules"][:5]
    except Exception as e:
        logger.debug("prior memo evaluation skipped: %s", e)

    stats = _gather_statistics(window_days)
    if not stats or stats["total_trades"] < MIN_TRADES_FOR_MEMO:
        logger.info("Insufficient trades for synthesis: %d < %d",
                    stats.get("total_trades", 0) if stats else 0, MIN_TRADES_FOR_MEMO)
        return None

    memo = _build_deterministic_memo(stats)
    if prior_corrections:
        memo["prior_corrections"] = prior_corrections

    # LLM synthesis for key_insight (optional enrichment)
    insight = await _llm_synthesize_insight(stats, memo)
    if insight:
        memo["key_insight"] = insight

    _persist_memo(memo)
    logger.info("TradingMemo generated: %d trades, %d regime rules, %d timing rules, %d symbols",
                memo["sample_size"], len(memo["regime_playbook"]),
                len(memo["timing_windows"]), len(memo["symbol_affinity"]))
    return memo


def _gather_statistics(window_days: int) -> dict[str, Any] | None:
    """Gather all statistics needed for memo generation."""
    try:
        from aletheia.storage.sqlite import get_db
        from aletheia.storage.repositories import get_latest_market_regime
        from aletheia.analytics.learning_window import clean_window_cutoff

        from aletheia.analytics.close_reasons import SYNTHETIC_CLOSE_REASONS

        db = get_db()
        # Clean-window floor: never ingest pre-2026-06-25T15:22 dirty Round 1 data.
        cutoff = clean_window_cutoff(
            (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        )

        # #160: exclude synthetic/administrative closes (reconciler sweeps, cleanups) —
        # they are not trade outcomes, and this memo steers the System Operator prompt,
        # so fitting regime/direction/anti-pattern rules on fabricated exits mis-teaches it.
        _syn = tuple(SYNTHETIC_CLOSE_REASONS)
        _ph = ",".join(["?"] * len(_syn))
        rows = db.execute(
            "SELECT id, symbol, direction, realized_pnl, entry_price, quantity, "
            "       opened_at, closed_at, regime_tag "
            "FROM positions "
            "WHERE status = 'closed' AND closed_at >= ? "
            f"  AND (close_reason IS NULL OR close_reason NOT IN ({_ph})) "
            "ORDER BY closed_at ASC",
            (cutoff, *_syn),
        ).fetchall()

        if not rows:
            return None

        current_regime = get_latest_market_regime()

        return {
            "total_trades": len(rows),
            "rows": rows,
            "current_regime": current_regime,
            "window_days": window_days,
        }
    except Exception as e:
        logger.warning("_gather_statistics failed: %s", e)
        return None


def _build_deterministic_memo(stats: dict) -> TradingMemo:
    """Build memo from pure statistics (no LLM needed for the structured parts)."""
    rows = stats["rows"]
    now = datetime.now(timezone.utc)

    regime_playbook = _compute_regime_playbook(rows)
    timing_windows = _compute_timing_windows(rows)
    symbol_affinity = _compute_symbol_affinity(rows)
    anti_patterns = _compute_anti_patterns(rows)
    direction_summary = _compute_direction_summary(rows)

    return {
        "generated_at": now.isoformat(),
        "valid_until": (now + timedelta(hours=MAX_MEMO_AGE_HOURS)).isoformat(),
        "data_window_days": stats["window_days"],
        "sample_size": stats["total_trades"],
        "regime_playbook": regime_playbook,
        "timing_windows": timing_windows,
        "symbol_affinity": symbol_affinity,
        "anti_patterns": anti_patterns,
        "direction_summary": direction_summary,
        "key_insight": "",
    }


def _pnl_pct(row) -> float:
    pnl = row["realized_pnl"] or 0.0
    notional = (row["entry_price"] or 1.0) * (row["quantity"] or 1.0)
    return pnl / notional * 100.0 if notional > 0 else 0.0


def _compute_regime_playbook(rows) -> list[RegimeRule]:
    """Win rate and expectancy per regime × direction combination."""
    from collections import defaultdict

    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        regime = r["regime_tag"] or "unknown"
        direction = r["direction"] or "unknown"
        buckets[(regime, direction)].append(_pnl_pct(r))

    rules: list[RegimeRule] = []
    for (regime, direction), pnls in buckets.items():
        if len(pnls) < MIN_TRADES_FOR_REGIME_RULE:
            continue
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / len(pnls) * 100
        avg_pnl = sum(pnls) / len(pnls)

        if win_rate >= 60 and avg_pnl > 0.1:
            edge = "strong"
        elif win_rate >= 45:
            edge = "weak"
        else:
            edge = "contra"

        rules.append({
            "regime": regime,
            "direction": direction,
            "edge": edge,
            "win_rate": round(win_rate, 1),
            "avg_pnl_pct": round(avg_pnl, 2),
            "sample_size": len(pnls),
        })

    return sorted(rules, key=lambda r: r["sample_size"], reverse=True)


def _compute_timing_windows(rows) -> list[TimingRule]:
    """Identify best/worst hours and days."""
    from collections import defaultdict

    hour_buckets: dict[int, list[float]] = defaultdict(list)
    day_buckets: dict[int, list[float]] = defaultdict(list)

    for r in rows:
        try:
            dt = datetime.fromisoformat(str(r["opened_at"]).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            hour_buckets[dt.hour].append(_pnl_pct(r))
            day_buckets[dt.weekday()].append(_pnl_pct(r))
        except (ValueError, TypeError):
            continue

    rules: list[TimingRule] = []
    _DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    # Hour windows (group into 4h blocks for statistical significance)
    for block_start in range(0, 24, 4):
        block_pnls: list[float] = []
        for h in range(block_start, min(block_start + 4, 24)):
            block_pnls.extend(hour_buckets.get(h, []))
        if len(block_pnls) < MIN_TRADES_FOR_TIMING_RULE:
            continue
        wins = sum(1 for p in block_pnls if p > 0)
        win_rate = wins / len(block_pnls) * 100
        avg_pnl = sum(block_pnls) / len(block_pnls)

        if win_rate >= 60 and avg_pnl > 0.1:
            quality = "best"
        elif win_rate < 40 or avg_pnl < -0.2:
            quality = "avoid"
        else:
            quality = "good"

        rules.append({
            "dimension": "hour",
            "value": f"{block_start:02d}-{block_start+4:02d} UTC",
            "quality": quality,
            "win_rate": round(win_rate, 1),
            "avg_pnl_pct": round(avg_pnl, 2),
            "sample_size": len(block_pnls),
        })

    # Day-of-week
    for dow, pnls in day_buckets.items():
        if len(pnls) < MIN_TRADES_FOR_TIMING_RULE:
            continue
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / len(pnls) * 100
        avg_pnl = sum(pnls) / len(pnls)

        if win_rate >= 60 and avg_pnl > 0.1:
            quality = "best"
        elif win_rate < 40 or avg_pnl < -0.2:
            quality = "avoid"
        else:
            quality = "good"

        rules.append({
            "dimension": "day",
            "value": _DOW[dow],
            "quality": quality,
            "win_rate": round(win_rate, 1),
            "avg_pnl_pct": round(avg_pnl, 2),
            "sample_size": len(pnls),
        })

    return rules


def _compute_symbol_affinity(rows) -> list[SymbolRule]:
    """Recent per-symbol performance classification."""
    from collections import defaultdict

    buckets: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        sym = r["symbol"] or "unknown"
        buckets[sym].append(r["realized_pnl"] or 0.0)

    rules: list[SymbolRule] = []
    for sym, pnls in buckets.items():
        if len(pnls) < MIN_TRADES_FOR_SYMBOL_RULE:
            continue
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / len(pnls) * 100
        total_pnl = sum(pnls)

        if win_rate >= 65 and total_pnl > 0:
            status = "hot"
        elif win_rate < 35 or total_pnl < 0:
            status = "cold"
        else:
            status = "neutral"

        rules.append({
            "symbol": sym,
            "status": status,
            "win_rate": round(win_rate, 1),
            "pnl": round(total_pnl, 4),
            "sample_size": len(pnls),
        })

    return sorted(rules, key=lambda r: r["pnl"], reverse=True)


def _compute_anti_patterns(rows) -> list[AntiPattern]:
    """Detect consistently losing combinations (regime+direction)."""
    from collections import defaultdict

    buckets: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        regime = r["regime_tag"] or "unknown"
        direction = r["direction"] or "unknown"
        key = f"{direction} in {regime}"
        buckets[key].append(_pnl_pct(r))

    patterns: list[AntiPattern] = []
    for pattern, pnls in buckets.items():
        if len(pnls) < MIN_TRADES_FOR_REGIME_RULE:
            continue
        losses = sum(1 for p in pnls if p <= 0)
        loss_rate = losses / len(pnls) * 100
        if loss_rate >= 70:
            patterns.append({
                "pattern": pattern,
                "loss_rate": round(loss_rate, 1),
                "sample_size": len(pnls),
            })

    return sorted(patterns, key=lambda p: p["loss_rate"], reverse=True)


def _compute_direction_summary(rows) -> dict[str, Any]:
    """Overall long vs short performance summary."""
    long_pnls = [_pnl_pct(r) for r in rows if r["direction"] == "long"]
    short_pnls = [_pnl_pct(r) for r in rows if r["direction"] == "short"]

    def _stats(pnls: list[float]) -> dict:
        if not pnls:
            return {"count": 0, "win_rate": 0, "avg_pnl_pct": 0}
        wins = sum(1 for p in pnls if p > 0)
        return {
            "count": len(pnls),
            "win_rate": round(wins / len(pnls) * 100, 1),
            "avg_pnl_pct": round(sum(pnls) / len(pnls), 2),
        }

    long_stats = _stats(long_pnls)
    short_stats = _stats(short_pnls)

    total = long_stats["count"] + short_stats["count"]
    # Positive-feedback-trap guard (Module I): if the sample is already extremely
    # one-sided (one direction > MAX_DIRECTION_BIAS_STRENGTH of trades), do NOT let
    # the memo reinforce that direction — the stats are unreliable AND reinforcing
    # would deepen the very concentration that hurt us (Round 1: short 17/23 ≈ 74%).
    # Force a neutral bias + flag it. Neutral never blocks an opportunity, it only
    # stops the memo from boosting the over-concentrated side.
    bias_capped = False
    if total > 0:
        dominant_share = max(long_stats["count"], short_stats["count"]) / total
        bias_capped = dominant_share > MAX_DIRECTION_BIAS_STRENGTH
        long_edge = long_stats["avg_pnl_pct"] - short_stats["avg_pnl_pct"]
        if bias_capped:
            bias = "balanced"  # one-sided sample → refuse to reinforce
        elif long_edge > 0.3:
            bias = "long_preferred"
        elif long_edge < -0.3:
            bias = "short_preferred"
        else:
            bias = "balanced"
    else:
        bias = "insufficient_data"

    return {
        "long": long_stats,
        "short": short_stats,
        "bias": bias,
        "bias_capped": bias_capped,
    }


async def _llm_synthesize_insight(stats: dict, memo: TradingMemo) -> str | None:
    """Call LLM for a one-paragraph synthesis of the key trading insight."""
    try:
        from aletheia.agents.helpers import llm_complete

        regime_info = stats.get("current_regime")
        regime_str = regime_info.get("state", "unknown") if regime_info else "unknown"

        prompt = f"""Based on the following 30-day trading performance summary, write ONE concise paragraph (3-5 sentences) identifying the most actionable insight for the next trading session.

Performance ({memo['sample_size']} trades, {memo['data_window_days']} days):
- Direction: {json.dumps(memo['direction_summary'], ensure_ascii=False)}
- Regime playbook: {json.dumps(memo['regime_playbook'][:4], ensure_ascii=False)}
- Timing: {json.dumps(memo['timing_windows'][:4], ensure_ascii=False)}
- Hot/Cold symbols: {json.dumps(memo['symbol_affinity'][:5], ensure_ascii=False)}
- Anti-patterns: {json.dumps(memo['anti_patterns'], ensure_ascii=False)}
- Current regime: {regime_str}

Focus on: what specific combination of regime + direction + timing gives the strongest historical edge right now? What should the system avoid? Be data-driven and specific — cite win rates and sample sizes."""

        content, err = await llm_complete(
            system="You are a quantitative trading analyst. Write concise, data-driven insights. No disclaimers.",
            user=prompt,
            max_tokens=300,
        )
        if err:
            logger.debug("LLM insight synthesis failed: %s", err)
            return None
        return (content or "").strip()[:500]
    except Exception as e:
        logger.debug("_llm_synthesize_insight failed: %s", e)
        return None


def _persist_memo(memo: TradingMemo) -> None:
    """Store memo as a knowledge item for FTS retrieval + direct lookup."""
    try:
        from aletheia.storage.repositories import insert_knowledge_item
        from aletheia.storage.models import KnowledgeItem

        k = KnowledgeItem(
            item_type="trading_memo",
            title=f"TradingMemo {memo['generated_at'][:10]}",
            content=json.dumps(memo, ensure_ascii=False),
            tags=json.dumps({
                "valid_until": memo["valid_until"],
                "sample_size": memo["sample_size"],
                "source": "insight_synthesizer",
            }),
        )
        insert_knowledge_item(k)
    except Exception as e:
        logger.warning("Failed to persist trading memo: %s", e)


def render_memo_for_observer(memo: TradingMemo, symbol: str | None = None) -> str:
    """Render a concise text block for injection into market_observer/System Operator prompts."""
    from aletheia.analytics.memo_effectiveness import memo_half_life_weight

    memo_weight = memo_half_life_weight(memo.get("generated_at"))
    lines = ["\n## Historical Performance Context (Trading Memo)"]
    lines.append(f"Data: {memo['sample_size']} trades over {memo['data_window_days']} days")
    lines.append(f"Memo time weight: {memo_weight:.2f} (half-life 7d)")

    # Direction summary
    ds = memo.get("direction_summary", {})
    if ds.get("long") and ds.get("short"):
        l = ds["long"]
        s = ds["short"]
        lines.append(f"Direction: Long {l['win_rate']}% win ({l['count']} trades, avg {l['avg_pnl_pct']}%) | "
                     f"Short {s['win_rate']}% win ({s['count']} trades, avg {s['avg_pnl_pct']}%) → {ds.get('bias', 'balanced')}")

    # Regime playbook (top 3 most relevant)
    if memo.get("regime_playbook"):
        lines.append("Regime edges:")
        for rule in memo["regime_playbook"][:4]:
            weighted_win = round(float(rule["win_rate"]) * memo_weight, 1)
            lines.append(f"  - {rule['direction']} in {rule['regime']}: "
                         f"{rule['win_rate']}% win, time-weighted {weighted_win}%, avg {rule['avg_pnl_pct']}% "
                         f"({rule['sample_size']} trades) → {rule['edge'].upper()}")

    # Timing
    best_times = [t for t in memo.get("timing_windows", []) if t["quality"] == "best"]
    avoid_times = [t for t in memo.get("timing_windows", []) if t["quality"] == "avoid"]
    if best_times:
        lines.append(f"Best timing: {', '.join(t['value'] for t in best_times[:2])}")
    if avoid_times:
        lines.append(f"Avoid timing: {', '.join(t['value'] for t in avoid_times[:2])}")

    # Symbol-specific (if provided)
    if symbol and memo.get("symbol_affinity"):
        for sr in memo["symbol_affinity"]:
            if sr["symbol"] == symbol:
                lines.append(f"This symbol ({symbol}): {sr['status'].upper()} — "
                             f"{sr['win_rate']}% win, PnL={sr['pnl']:.4f} ({sr['sample_size']} trades)")
                break

    # Anti-patterns
    if memo.get("anti_patterns"):
        lines.append("Anti-patterns (avoid):")
        for ap in memo["anti_patterns"][:3]:
            lines.append(f"  - {ap['pattern']}: {ap['loss_rate']}% loss rate ({ap['sample_size']} trades)")

    # Prior corrections (Module J): judgments the previous memo made that
    # subsequent results overturned. Surfaces the revision so the agent sees the
    # learning is self-correcting, not static.
    if memo.get("prior_corrections"):
        lines.append("Corrections (prior guidance overturned by later results — judgment revised):")
        for c in memo["prior_corrections"][:3]:
            lines.append(
                f"  - Previously '{c.get('direction')} in {c.get('regime')}: "
                f"{c.get('claimed_edge')}' → actual {c.get('actual_win_rate')}% win over "
                f"{c.get('sample')} trades; {c.get('reason')}"
            )

    # Key insight (LLM summary)
    if memo.get("key_insight"):
        lines.append(f"Insight: {memo['key_insight']}")

    return "\n".join(lines)
