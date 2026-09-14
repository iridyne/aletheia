"""Read-only planning for historical outcome-integrity remediation."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from typing import Any

from aletheia.analytics.learning_window import (
    CLEAN_WINDOW_ANCHOR_ID,
    CLEAN_WINDOW_ANCHOR_ISO,
)
from aletheia.analytics.outcome_integrity import (
    ADMINISTRATIVE_CLOSE_REASONS,
    MAINTENANCE_CLOSE_REASONS,
    OUTCOME_SEMANTICS_VERSION,
)


REMEDIATION_BATCH_ID = f"{CLEAN_WINDOW_ANCHOR_ID}:{OUTCOME_SEMANTICS_VERSION}"
_DERIVED_ITEM_TYPES = frozenset({
    "calibration_report",
    "reasoning_feedback",
    "trading_memo",
    "operator_assessment",
    "post_trade_review",
    "trade_case_study",
})


def _plan_outcome_history(db):
    classifications = _classification_plan(db)
    archives = _archive_plan(db)
    compensation_candidates = _compensation_candidates(db)
    result = {
        "batch_id": REMEDIATION_BATCH_ID,
        "semantics_version": OUTCOME_SEMANTICS_VERSION,
        "clean_window_anchor_id": CLEAN_WINDOW_ANCHOR_ID,
        "clean_window_anchor_iso": CLEAN_WINDOW_ANCHOR_ISO,
        "dry_run": True,
        "classification": {
            "planned": len(classifications),
            "applied": 0,
            "by_category": dict(sorted(Counter(
                item["close_category"] for item in classifications
            ).items())),
        },
        "archive": {
            "planned": len(archives),
            "applied": 0,
            "by_type": dict(sorted(Counter(
                item["item_type"] for item in archives
            ).items())),
        },
        "compensation_candidates": compensation_candidates,
        "owner_approval_required": True,
        "rebuild_candidates": ["calibration_report", "reasoning_feedback"],
    }
    return result, classifications, archives


def plan_outcome_history() -> dict[str, Any]:
    from aletheia.storage.sqlite import read_snapshot

    with read_snapshot() as db:
        result, _, _ = _plan_outcome_history(db)
    return result


def _classification_plan(db) -> list[dict[str, str]]:
    rows = db.execute(
        "SELECT id, close_reason FROM positions WHERE status='closed' "
        "AND outcome_semantics_version IS NULL ORDER BY id"
    ).fetchall()
    plan: list[dict[str, str]] = []
    for row in rows:
        reason = str(row["close_reason"] or "").strip().lower()
        if reason in ADMINISTRATIVE_CLOSE_REASONS:
            category = "administrative"
        elif reason in MAINTENANCE_CLOSE_REASONS:
            category = "maintenance"
        else:
            continue
        plan.append({
            "id": str(row["id"]),
            "close_category": category,
            "outcome_exclusion_reason": f"{category}_close",
        })
    return plan


def _archive_plan(db) -> list[dict[str, str]]:
    placeholders = ",".join("?" for _ in _DERIVED_ITEM_TYPES)
    rows = db.execute(
        f"SELECT id,item_type,content,tags,created_at FROM knowledge_items "
        f"WHERE status='active' AND item_type IN ({placeholders}) ORDER BY id",
        tuple(sorted(_DERIVED_ITEM_TYPES)),
    ).fetchall()
    plan: list[dict[str, str]] = []
    for row in rows:
        reason = _derived_archive_reason(dict(row))
        if reason:
            plan.append({
                "id": str(row["id"]),
                "item_type": str(row["item_type"]),
                "reason": reason,
            })
    return plan


def _derived_archive_reason(row: dict[str, Any]) -> str | None:
    created_at = str(row.get("created_at") or "")
    if created_at and created_at < CLEAN_WINDOW_ANCHOR_ISO:
        return "pre_clean_window_derived_artifact"
    if row.get("item_type") == "operator_assessment":
        return None
    if row.get("item_type") in {"post_trade_review", "trade_case_study"}:
        return None
    try:
        payload = json.loads(row.get("content") or "{}")
    except (TypeError, json.JSONDecodeError):
        return "unreadable_derived_artifact"
    if not isinstance(payload, dict):
        return "unreadable_derived_artifact"
    if payload.get("outcome_semantics_version") != OUTCOME_SEMANTICS_VERSION:
        return "unknown_semantics_version"
    if payload.get("clean_window_anchor_id") != CLEAN_WINDOW_ANCHOR_ID:
        return "unknown_clean_window_anchor"
    return None


def _compensation_candidates(db) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT e.id AS event_id,e.position_id,e.amount_usdt "
        "FROM paper_account_events e JOIN positions p ON p.id=e.position_id "
        "WHERE e.action='position_closed' AND ("
        "p.close_reason IN ({}) OR p.close_category IN ('administrative','maintenance')) "
        "ORDER BY e.id".format(",".join("?" for _ in (
            ADMINISTRATIVE_CLOSE_REASONS | MAINTENANCE_CLOSE_REASONS
        ))),
        tuple(sorted(ADMINISTRATIVE_CLOSE_REASONS | MAINTENANCE_CLOSE_REASONS)),
    ).fetchall()
    return [{
        "candidate_id": _candidate_id(str(row["event_id"])),
        "position_id": str(row["position_id"]),
        "paper_account_event_id": str(row["event_id"]),
        "amount_usdt": round(-float(row["amount_usdt"]), 8),
        "reason": "reverse_administrative_close_cash_effect",
        "owner_approval_required": True,
    } for row in rows]


def _candidate_id(event_id: str) -> str:
    return "oi-cash-" + hashlib.sha256(
        f"{REMEDIATION_BATCH_ID}:{event_id}".encode()
    ).hexdigest()[:24]
