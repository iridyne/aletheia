"""TradingMemo types, persisted read validation and observer rendering."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, NotRequired, TypedDict

from aletheia.analytics.execution_mode import (
    resolve_selected_execution_mode,
)
from aletheia.analytics.outcome_integrity import (
    is_current_outcome_artifact,
)

logger = logging.getLogger("aletheia.analytics.insight_synthesizer")

MAX_MEMO_AGE_HOURS = 72


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
    outcome_semantics_version: str
    clean_window_anchor_id: str
    clean_window_anchor_iso: str
    source_raw_event_count: int
    loaded_raw_event_count: int
    cohort_complete: bool
    ordered_source_digest: str
    cohort_identity: dict[str, str | None]
    execution_mode: NotRequired[str]  # absent in pre-mode-tagging memos


def get_latest_memo(
    *, execution_mode: str | None = None,
) -> TradingMemo | None:
    """Retrieve the most recent valid TradingMemo for the selected mode.

    Only returns a persisted memo whose ``execution_mode`` matches the
    resolved (or provided) selected mode (#1939).  Legacy mode-less memos
    are ignored so a mode switch never reuses the other cohort's Learning
    output under the new selected-mode identity.
    """
    if execution_mode is None:
        execution_mode = resolve_selected_execution_mode()
    if not execution_mode:
        return None
    try:
        from aletheia.storage.repositories import list_knowledge_items
        items = list_knowledge_items(item_type="trading_memo", limit=10)
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
                memo = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue
            if memo.get("execution_mode") != execution_mode:
                continue
            if memo.get("cohort_complete") is not True or not memo.get(
                "ordered_source_digest"
            ):
                continue
            if is_current_outcome_artifact(
                memo, max_age=timedelta(hours=MAX_MEMO_AGE_HOURS),
            ):
                return memo
        return None
    except Exception as e:
        logger.debug("get_latest_memo failed: %s", e)
        return None


def render_memo_for_observer(
    memo: TradingMemo,
    symbol: str | None = None,
    compact: bool = False,
) -> str:
    """Render a concise text block for injection into market_observer/System Operator prompts.

    compact=True trims to direction bias + top-2 regime edges + top-2 anti-patterns,
    dropping timing windows, prior corrections, metadata, and key insight. Use when the
    memo is sent per-symbol in a multi-symbol loop to reduce repeated token cost.
    """
    from aletheia.analytics.memo_effectiveness import memo_half_life_weight

    memo_weight = memo_half_life_weight(memo.get("generated_at"))
    lines = ["\n## Historical Performance Context (Trading Memo)"]
    if memo.get("cohort_complete") is True and memo.get("ordered_source_digest"):
        lines.append(
            "Cohort: complete "
            f"{memo.get('loaded_raw_event_count')}/{memo.get('source_raw_event_count')} "
            f"digest={str(memo['ordered_source_digest'])[:12]}"
        )

    if not compact:
        lines.append(f"Data: {memo['sample_size']} trades over {memo['data_window_days']} days")
        lines.append(f"Memo time weight: {memo_weight:.2f} (half-life 7d)")

    # Direction summary
    ds = memo.get("direction_summary", {})
    if ds.get("long") and ds.get("short"):
        l = ds["long"]
        s = ds["short"]
        lines.append(f"Direction: Long {l['win_rate']}% win ({l['count']} trades, avg {l['avg_pnl_pct']}%) | "
                     f"Short {s['win_rate']}% win ({s['count']} trades, avg {s['avg_pnl_pct']}%) → {ds.get('bias', 'balanced')}")

    # Regime playbook — top 4 in full mode, top 2 in compact
    if memo.get("regime_playbook"):
        lines.append("Regime edges:")
        limit = 2 if compact else 4
        for rule in memo["regime_playbook"][:limit]:
            weighted_win = round(float(rule["win_rate"]) * memo_weight, 1)
            lines.append(f"  - {rule['direction']} in {rule['regime']}: "
                         f"{rule['win_rate']}% win, time-weighted {weighted_win}%, avg {rule['avg_pnl_pct']}% "
                         f"({rule['sample_size']} trades) → {rule['edge'].upper()}")

    # Timing — omitted in compact mode
    if not compact:
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

    # Anti-patterns — top 3 in full mode, top 2 in compact
    if memo.get("anti_patterns"):
        lines.append("Anti-patterns (avoid):")
        limit = 2 if compact else 3
        for ap in memo["anti_patterns"][:limit]:
            lines.append(f"  - {ap['pattern']}: {ap['loss_rate']}% loss rate ({ap['sample_size']} trades)")

    # Prior corrections — omitted in compact mode
    if not compact and memo.get("prior_corrections"):
        lines.append("Corrections (prior guidance overturned by later results — judgment revised):")
        for c in memo["prior_corrections"][:3]:
            lines.append(
                f"  - Previously '{c.get('direction')} in {c.get('regime')}: "
                f"{c.get('claimed_edge')}' → actual {c.get('actual_win_rate')}% win over "
                f"{c.get('sample')} trades; {c.get('reason')}"
            )

    # Key insight (LLM summary) — omitted in compact mode
    if not compact and memo.get("key_insight"):
        lines.append(f"Insight: {memo['key_insight']}")

    return "\n".join(lines)
