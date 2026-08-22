"""Reasoning Quality Feedback — tracks which System Operator reasoning patterns lead to
approved/rejected proposals and whether approved ones actually win.

No LLM calls — pure SQL aggregation. Results injected into System Operator Layer 2 prompt
so the agent can self-correct on next decision cycle.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from aletheia.analytics.outcome_integrity import (
    eligible_strategy_outcomes,
    is_current_outcome_artifact,
)
from aletheia.analytics.learning_window import outcome_window_metadata

logger = logging.getLogger("aletheia.analytics.reasoning_feedback")

MIN_SAMPLE_SIZE = 3
MIN_APPROVAL_TO_ANALYZE = 5
MIN_FEEDBACK_INTERVAL_HOURS = 6
FEEDBACK_WINDOW_DAYS = 30
MAX_ACTIVE_FEEDBACK_AGE = timedelta(hours=24)


def build_reasoning_feedback(window_days: int = FEEDBACK_WINDOW_DAYS) -> dict | None:
    """Aggregate proposal rejection patterns and approval win rates.

    Returns a dict or None when insufficient data.
    """
    try:
        from aletheia.storage.sqlite import get_db
        from aletheia.analytics.learning_window import clean_window_cutoff
        db = get_db()
        # Clean-window floor: never ingest pre-2026-06-25T15:22 dirty Round 1 data.
        cutoff = clean_window_cutoff(
            (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        )

        rows = db.execute(
            """
            SELECT
                tp.id AS proposal_id,
                tp.status AS proposal_status,
                tp.agent_rationale,
                tp.direction,
                tp.opportunity_id,
                pd.conviction,
                pd.reasoning AS pi_reasoning,
                o.regime_tag AS proposal_regime_tag,
                o.scanner_family AS proposal_scanner_family
            FROM trade_proposals tp
            LEFT JOIN pi_decisions pd
                ON pd.proposal_id = tp.id
            LEFT JOIN opportunities o
                ON o.id = tp.opportunity_id
            WHERE tp.created_at >= ?
            ORDER BY tp.created_at DESC
            """,
            (cutoff,),
        ).fetchall()

        if not rows:
            return None

        outcomes_by_proposal = {
            str(row["proposal_id"]): row
            for row in eligible_strategy_outcomes(closed_after=cutoff)
            if row.get("proposal_id")
        }
        proposals = []
        for raw in rows:
            proposal = dict(raw)
            outcome = outcomes_by_proposal.get(str(proposal["proposal_id"]))
            proposal.update({
                "realized_pnl": outcome.get("realized_pnl") if outcome else None,
                "regime_tag": proposal.get("proposal_regime_tag") or (outcome.get("regime_tag") if outcome else None),
            })
            proposals.append(proposal)
        total = len(proposals)

        # Classify each proposal
        executed_wins = []
        executed_losses = []
        rejected = []

        for p in proposals:
            status = p.get("proposal_status", "")
            pnl = p.get("realized_pnl")
            if status == "rejected":
                rejected.append(p)
            # #160: a synthetic/administrative close (reconciler sweep, cleanup) is not
            # a genuine win/loss — exclude it from the executed-win/loss classification
            # that feeds recommended_pursue/avoid patterns into the Layer-2 prompt.
            elif pnl is not None:
                if pnl > 0:
                    executed_wins.append(p)
                else:
                    executed_losses.append(p)

        executed = executed_wins + executed_losses
        sample_executed = len(executed)

        if sample_executed < MIN_APPROVAL_TO_ANALYZE and len(rejected) < MIN_SAMPLE_SIZE:
            return None

        # Build pattern buckets by (direction, scanner_family, regime_tag)
        def _extract_scanner_family(row: dict) -> str:
            # Prefer proposal-time scanner_family from the opportunity (#1981)
            if row.get("proposal_scanner_family"):
                return str(row["proposal_scanner_family"])
            rationale = row.get("agent_rationale") or ""
            try:
                # agent_rationale format: "{reasoning[:200]}\n---\n{decision_meta_json}"
                if "---" in rationale:
                    meta_part = rationale.split("---", 1)[1].strip()
                    meta = json.loads(meta_part)
                    return str(meta.get("scanner_family") or meta.get("supporting", [{}])[0].get("scanner_family") or "unknown")
            except Exception:
                pass
            return "unknown"

        from collections import defaultdict
        # key: (direction, scanner_family, regime_tag)
        reject_buckets: dict[tuple, list] = defaultdict(list)
        win_buckets: dict[tuple, list] = defaultdict(list)
        loss_buckets: dict[tuple, list] = defaultdict(list)

        for p in rejected:
            key = (p.get("direction") or "?", _extract_scanner_family(p), p.get("regime_tag") or "unknown")
            reject_buckets[key].append(p)

        for p in executed_wins:
            key = (p.get("direction") or "?", _extract_scanner_family(p), p.get("regime_tag") or "unknown")
            win_buckets[key].append(p)

        for p in executed_losses:
            key = (p.get("direction") or "?", _extract_scanner_family(p), p.get("regime_tag") or "unknown")
            loss_buckets[key].append(p)

        # All keys seen across rejected + executed
        all_keys = set(reject_buckets.keys()) | set(win_buckets.keys()) | set(loss_buckets.keys())

        approval_outcomes = []
        rejection_patterns = []

        for key in all_keys:
            direction, scanner, regime = key
            pattern_label = f"{direction}+{scanner}+{regime}"
            r_count = len(reject_buckets[key])
            w_count = len(win_buckets[key])
            l_count = len(loss_buckets[key])
            proposal_total = r_count + w_count + l_count

            if proposal_total < MIN_SAMPLE_SIZE:
                continue

            rejection_rate = r_count / proposal_total if proposal_total > 0 else 0.0

            if rejection_rate >= 0.5 and r_count >= MIN_SAMPLE_SIZE:
                rejection_patterns.append({
                    "pattern_label": pattern_label,
                    "rejection_count": r_count,
                    "proposal_count": proposal_total,
                    "rejection_rate": round(rejection_rate, 3),
                })

            exec_total = w_count + l_count
            if exec_total >= MIN_SAMPLE_SIZE:
                win_rate = w_count / exec_total
                avg_pnl = (
                    sum(p.get("realized_pnl", 0) or 0 for p in win_buckets[key] + loss_buckets[key])
                    / exec_total
                )
                convictions = [
                    p.get("conviction") or 0
                    for p in win_buckets[key] + loss_buckets[key]
                    if p.get("conviction") is not None
                ]
                avg_conviction = sum(convictions) / len(convictions) if convictions else 0.0
                approval_outcomes.append({
                    "pattern_label": pattern_label,
                    "approval_count": exec_total,
                    "win_count": w_count,
                    "loss_count": l_count,
                    "win_rate": round(win_rate, 3),
                    "avg_pnl": round(avg_pnl, 4),
                    "avg_conviction": round(avg_conviction, 1),
                    "sample_size": exec_total,
                })

        # Sort
        rejection_patterns.sort(key=lambda x: x["rejection_rate"], reverse=True)
        approval_outcomes.sort(key=lambda x: (x["win_rate"], x["approval_count"]), reverse=True)

        recommended_avoid = [p["pattern_label"] for p in rejection_patterns[:3]]
        recommended_pursue = [
            p["pattern_label"] for p in approval_outcomes
            if p["win_rate"] >= 0.60 and p["sample_size"] >= MIN_APPROVAL_TO_ANALYZE
        ][:3]

        # Conviction tier win rates
        high_conv = [p for p in executed if (p.get("conviction") or 0) >= 80]
        low_conv = [p for p in executed if (p.get("conviction") or 0) < 60]

        def _win_rate(subset: list) -> float | None:
            if len(subset) < MIN_SAMPLE_SIZE:
                return None
            wins = sum(1 for p in subset if (p.get("realized_pnl") or 0) > 0)
            return round(wins / len(subset), 3)

        now = datetime.now(timezone.utc).isoformat()
        result = {
            "generated_at": now,
            "window_days": window_days,
            "sample_proposals": total,
            "sample_executed": sample_executed,
            "approval_outcomes": approval_outcomes,
            "rejection_patterns": rejection_patterns,
            "recommended_avoid": recommended_avoid,
            "recommended_pursue": recommended_pursue,
            "high_conviction_win_rate": _win_rate(high_conv),
            "low_conviction_win_rate": _win_rate(low_conv),
            **outcome_window_metadata(),
        }

        _persist_feedback(result)
        return result

    except Exception as e:
        logger.warning("build_reasoning_feedback failed: %s", e)
        return None


def get_latest_reasoning_feedback() -> dict | None:
    """Read most-recent reasoning feedback from knowledge_items store."""
    try:
        from aletheia.storage.repositories import list_knowledge_items
        items = list_knowledge_items(item_type="reasoning_feedback", limit=1)
        for item in items:
            content = item.get("content")
            if content:
                try:
                    feedback = json.loads(content)
                    if is_current_outcome_artifact(
                        feedback, max_age=MAX_ACTIVE_FEEDBACK_AGE,
                    ):
                        return feedback
                except (json.JSONDecodeError, TypeError):
                    continue
        return None
    except Exception as e:
        logger.debug("get_latest_reasoning_feedback failed: %s", e)
        return None


def render_feedback_for_pi(fb: dict) -> str:
    """Render compact text block for System Operator Layer 2 prompt injection."""
    n_prop = fb.get("sample_proposals", 0)
    n_exec = fb.get("sample_executed", 0)
    lines = [f"\n## Reasoning Quality Feedback ({fb.get('window_days', 30)}d, {n_prop} proposals → {n_exec} executed)"]

    pursue = fb.get("recommended_pursue", [])
    if pursue:
        lines.append("Pursue patterns (high win rate):")
        for p in fb.get("approval_outcomes", []):
            if p["pattern_label"] in pursue:
                lines.append(f"  - {p['pattern_label']}: {p['win_rate']*100:.0f}% wr ({p['sample_size']} trades, avg {p['avg_pnl']:+.3f})")

    avoid = fb.get("recommended_avoid", [])
    if avoid:
        lines.append("Avoid patterns (high rejection rate):")
        for p in fb.get("rejection_patterns", []):
            if p["pattern_label"] in avoid:
                lines.append(f"  - {p['pattern_label']}: {p['rejection_rate']*100:.0f}% rejection ({p['rejection_count']}/{p['proposal_count']})")

    hc = fb.get("high_conviction_win_rate")
    lc = fb.get("low_conviction_win_rate")
    if hc is not None or lc is not None:
        parts = []
        if hc is not None:
            parts.append(f"≥80 conviction wins {hc*100:.0f}%")
        if lc is not None:
            parts.append(f"<60 conviction wins {lc*100:.0f}%")
        lines.append("Conviction calibration: " + "; ".join(parts))

    return "\n".join(lines)


def _persist_feedback(fb: dict) -> None:
    """Persist feedback as a knowledge item (rate-limited to 6h)."""
    try:
        existing = get_latest_reasoning_feedback()
        if existing:
            gen = datetime.fromisoformat(existing.get("generated_at", "2000-01-01"))
            if gen.tzinfo is None:
                gen = gen.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
            if age_h < MIN_FEEDBACK_INTERVAL_HOURS:
                return

        from aletheia.storage.repositories import insert_knowledge_item
        from aletheia.storage.models import KnowledgeItem

        insert_knowledge_item(KnowledgeItem(
            item_type="reasoning_feedback",
            title=f"ReasoningFeedback {fb['generated_at'][:10]}",
            content=json.dumps(fb, ensure_ascii=False),
        ))
    except Exception as e:
        logger.debug("_persist_feedback failed: %s", e)
