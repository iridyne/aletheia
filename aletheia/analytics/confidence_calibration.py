"""Conviction Calibration — measures whether System Operator conviction scores are predictive.

Formula: ECE (Expected Calibration Error) = Σ (n_bucket/total) × |actual_wr - expected_wr|
No LLM calls. Runs every 6h via scheduler; also available on-demand from API.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from aletheia.analytics.outcome_integrity import (
    OUTCOME_SEMANTICS_VERSION,
    eligible_strategy_outcomes,
    is_current_outcome_artifact,
)

logger = logging.getLogger("aletheia.analytics.confidence_calibration")

MIN_CALIBRATION_SAMPLE = 5
CALIBRATION_WINDOW_DAYS = 90
MIN_CALIBRATION_INTERVAL_HOURS = 6
MAX_ACTIVE_REPORT_AGE = timedelta(hours=24)

# (label, low_inclusive, high_inclusive, expected_win_rate = midpoint/100)
_BUCKETS = [
    ("low",       0,  49, 0.25),
    ("medium",   50,  64, 0.57),
    ("high",     65,  79, 0.72),
    ("very_high",80, 100, 0.90),
]


def build_calibration_report(window_days: int = CALIBRATION_WINDOW_DAYS) -> dict | None:
    """Compute calibration from all closed positions linked to pi_decisions.

    Returns CalibrationReport dict or None when insufficient data.
    """
    try:
        from aletheia.storage.sqlite import get_db
        from aletheia.analytics.learning_window import clean_window_cutoff
        db = get_db()
        # Clean-window floor: never ingest pre-2026-06-25T15:22 dirty Round 1 data.
        cutoff = clean_window_cutoff(
            (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        )

        rows = []
        for position in eligible_strategy_outcomes(closed_after=cutoff):
            proposal_id = position.get("proposal_id")
            if not proposal_id:
                continue
            decision = db.execute(
                "SELECT conviction FROM pi_decisions "
                "WHERE proposal_id = ? AND conviction > 0 "
                "AND action IN ('trade', 'scale_in', 'flip') "
                "AND created_at >= ? ORDER BY datetime(created_at) DESC LIMIT 1",
                (proposal_id, cutoff),
            ).fetchone()
            if decision:
                rows.append({
                    "conviction": decision["conviction"],
                    "realized_pnl": position["realized_pnl"],
                })

        if not rows:
            return None

        trades = [
            {"conviction": r["conviction"], "win": (r["realized_pnl"] or 0) > 0}
            for r in rows
        ]
        total = len(trades)

        if total < MIN_CALIBRATION_SAMPLE:
            return None

        buckets = []
        for label, lo, hi, expected_wr in _BUCKETS:
            subset = [t for t in trades if lo <= (t["conviction"] or 0) <= hi]
            n = len(subset)
            wins = sum(1 for t in subset if t["win"])
            actual_wr = wins / n if n > 0 else 0.0
            reliable = n >= MIN_CALIBRATION_SAMPLE
            cal_err = abs(actual_wr - expected_wr) if reliable else 0.0
            buckets.append({
                "label": label,
                "conviction_range": f"{lo}-{hi}",
                "sample_size": n,
                "win_count": wins,
                "actual_win_rate": round(actual_wr, 4),
                "expected_win_rate": round(expected_wr, 4),
                "calibration_error": round(cal_err, 4),
                "reliable": reliable,
            })

        # ECE: weighted over reliable buckets only
        reliable_n = sum(b["sample_size"] for b in buckets if b["reliable"])
        if reliable_n > 0:
            ece = sum(
                (b["sample_size"] / reliable_n) * b["calibration_error"]
                for b in buckets if b["reliable"]
            )
        else:
            ece = 0.0
        ece = round(ece, 4)

        if ece < 0.05:
            ece_grade = "excellent"
        elif ece < 0.10:
            ece_grade = "good"
        elif ece < 0.20:
            ece_grade = "fair"
        else:
            ece_grade = "poor"

        overall_wins = sum(1 for t in trades if t["win"])
        overall_wr = round(overall_wins / total, 4)

        overconfident = [b["label"] for b in buckets if b["reliable"] and b["actual_win_rate"] < b["expected_win_rate"] - 0.10]
        underconfident = [b["label"] for b in buckets if b["reliable"] and b["actual_win_rate"] > b["expected_win_rate"] + 0.10]

        if reliable_n == 0:
            summary = f"Insufficient data for calibration ({total} executed trades, need {MIN_CALIBRATION_SAMPLE}+ per bucket)"
        elif ece_grade in ("excellent", "good"):
            summary = f"Well-calibrated: ECE={ece:.3f}. Conviction scores are predictive."
        else:
            oc_str = ", ".join(overconfident) if overconfident else "none"
            summary = f"Miscalibrated (ECE={ece:.3f}): overconfident in [{oc_str}] buckets."

        now = datetime.now(timezone.utc).isoformat()
        report = {
            "generated_at": now,
            "window_days": window_days,
            "total_executed": total,
            "buckets": buckets,
            "ece": ece,
            "ece_grade": ece_grade,
            "overall_win_rate": overall_wr,
            "overconfident_buckets": overconfident,
            "underconfident_buckets": underconfident,
            "calibration_summary": summary,
            "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
        }

        _persist_report(report)
        return report

    except Exception as e:
        logger.warning("build_calibration_report failed: %s", e)
        return None


def get_latest_calibration_report() -> dict | None:
    """Read most-recent calibration report from knowledge_items."""
    try:
        from aletheia.storage.repositories import list_knowledge_items
        items = list_knowledge_items(item_type="calibration_report", limit=1)
        for item in items:
            content = item.get("content")
            if content:
                try:
                    report = json.loads(content)
                    if is_current_outcome_artifact(
                        report, max_age=MAX_ACTIVE_REPORT_AGE,
                    ):
                        return report
                except (json.JSONDecodeError, TypeError):
                    continue
        return None
    except Exception as e:
        logger.debug("get_latest_calibration_report failed: %s", e)
        return None


def render_calibration_for_pi(report: dict) -> str:
    """Render compact text for System Operator Layer 2 prompt injection."""
    ece = report.get("ece")
    grade = report.get("ece_grade", "?")
    total = report.get("total_executed", 0)
    # #157: the ECE format must be precomputed — `{ece:.3f if ece else '?'}` is an
    # invalid format spec that raised ValueError on every real (numeric-ece) report,
    # which the caller silently swallowed, killing the calibration feedback entirely.
    ece_str = f"{ece:.3f}" if isinstance(ece, (int, float)) else "?"
    lines = [f"\n## Conviction Calibration ({total} executed trades, ECE={ece_str} {grade})"]
    lines.append(report.get("calibration_summary", ""))

    overconf = report.get("overconfident_buckets", [])
    if overconf:
        lines.append(f"Recalibrate DOWN for high-conviction calls: {', '.join(overconf)} conviction → actual win rate is lower than stated.")

    underconf = report.get("underconfident_buckets", [])
    if underconf:
        lines.append(f"Trust your lower-conviction signals more: {', '.join(underconf)} conviction → outperforming stated probability.")

    for b in report.get("buckets", []):
        if b["reliable"]:
            lines.append(
                f"  {b['conviction_range']} conviction: {b['actual_win_rate']*100:.0f}% actual vs {b['expected_win_rate']*100:.0f}% expected ({b['sample_size']} trades)"
            )

    return "\n".join(lines)


def compute_calibration_score() -> float | None:
    """Return 0-100 score for go-live scorecard. 100=perfect, degrades with ECE."""
    report = get_latest_calibration_report()
    if not report:
        return None
    total = report.get("total_executed", 0)
    if total < MIN_CALIBRATION_SAMPLE:
        return None
    ece = report.get("ece", 1.0) or 1.0
    # ECE=0 → 100pts, ECE=0.20 → 60pts, ECE=0.50 → 0pts
    score = max(0.0, 100.0 - ece * 200.0)
    return round(min(100.0, score), 1)


def _persist_report(report: dict) -> None:
    """Persist report as a knowledge item (rate-limited to 6h)."""
    try:
        existing = get_latest_calibration_report()
        if existing:
            gen = datetime.fromisoformat(existing.get("generated_at", "2000-01-01"))
            if gen.tzinfo is None:
                gen = gen.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
            if age_h < MIN_CALIBRATION_INTERVAL_HOURS:
                return

        from aletheia.storage.repositories import insert_knowledge_item
        from aletheia.storage.models import KnowledgeItem

        insert_knowledge_item(KnowledgeItem(
            item_type="calibration_report",
            title=f"CalibrationReport {report['generated_at'][:10]}",
            content=json.dumps(report, ensure_ascii=False),
        ))
    except Exception as e:
        logger.debug("_persist_report failed: %s", e)
