"""Lifecycle diagnosis assembly from a LifecycleLineage.

Emits `lifecycle_diagnosis/v1`. Per issue #3281 §5:
  - per-leg × per-stage decomposition (jurisdiction ancestry, plus one execution
    branch per entry-leg proposal covering risk → order → fill → position → exit)
  - `attention_state` in {advancing, waiting, ended_ok, ended_reject,
    evidence_gap, breakpoint}
  - `first_break` naming the earliest failing stage
  - `economics.position_fact` is loaded via `build_position_replay` when the
    anchor is a position and the position row is visible.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aletheia.analytics.candidate_lifecycle.lineage_reader import LifecycleLineage

CONTRACT_VERSION = "lifecycle_diagnosis/v1"

_TERMINAL_POSITION_STATUS = {"closed", "archived"}
_REJECT_ORDER_STATUS = {"rejected", "failed", "cancelled", "invalidated"}


def build_lifecycle_diagnosis(
    lineage: LifecycleLineage,
    *,
    cutoff: datetime,
    db: Any = None,
) -> dict[str, Any]:
    anchor_kind, anchor_id = lineage.anchor
    ep = lineage.entry_plan or {}
    ids: dict[str, Any] = {}
    if ep.get("id"):
        ids["entry_plan_id"] = ep["id"]
    if lineage.candidate and lineage.candidate.get("id"):
        ids["candidate_id"] = lineage.candidate["id"]
    if lineage.generation and lineage.generation.get("id"):
        ids["generation_id"] = lineage.generation["id"]
    if lineage.opportunity_row and lineage.opportunity_row.get("id"):
        ids["opportunity_id"] = lineage.opportunity_row["id"]

    ancestry_stages = _derive_ancestry_stages(lineage)
    leg_stages = _derive_leg_stages(lineage, db, cutoff) if db is not None else []
    stages = ancestry_stages + leg_stages

    first_break = _find_first_break(lineage, leg_stages)
    attention = _derive_attention_state(lineage, leg_stages, first_break)

    economics = _load_economics(lineage, anchor_kind, anchor_id, cutoff)

    return {
        "contract_version": CONTRACT_VERSION,
        "anchor": {"kind": anchor_kind, "id": anchor_id},
        "source": {"kind": lineage.source_kind, "ids": ids},
        "evidence_context": {
            "cutoff": cutoff.isoformat(),
            "read_at": datetime.now(timezone.utc).isoformat(),
        },
        "stages": stages,
        "current_stage": _current_stage_name(stages),
        "attention_state": attention,
        "first_break": first_break,
        "coverage": {
            "legs": len(lineage.legs),
            "gaps": list(lineage.evidence_gaps),
            "identity_conflicts": list(lineage.identity_conflicts),
            "truncated": False,
        },
        "economics": economics,
        "next_inspection": _next_inspection_hints(lineage, anchor_kind),
    }


# ---------------------------------------------------------------------------
# Ancestry stages (source-agnostic view, one row per link)
# ---------------------------------------------------------------------------

def _derive_ancestry_stages(lineage: LifecycleLineage) -> list[dict[str, Any]]:
    stages: list[dict[str, Any]] = []
    if lineage.source_kind == "universe":
        stages.append(_stage("candidate_discovery", lineage.universe_member))
        stages.append(_stage("planning_generation", lineage.generation))
    elif lineage.source_kind in ("serenity", "opportunity"):
        stages.append(_stage("opportunity", lineage.opportunity_row))
    stages.append(_stage("entry_plan", lineage.entry_plan))
    return stages


def _stage(name: str, row: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "stage": name,
        "leg": None,
        "status": "available" if row else "unavailable",
        "record_status": "found" if row else "missing",
    }


# ---------------------------------------------------------------------------
# Per-leg execution stages (proposal → risk → order → fill → position → exit)
# ---------------------------------------------------------------------------

def _derive_leg_stages(
    lineage: LifecycleLineage,
    db: Any,
    cutoff: datetime,
) -> list[dict[str, Any]]:
    """Walk each entry-leg proposal into risk/order/fill/position/exit."""
    out: list[dict[str, Any]] = []
    cutoff_iso = cutoff.isoformat()

    for leg_row in lineage.legs:
        leg_index = leg_row.get("entry_leg_index") or 0
        proposal_id = leg_row.get("id")
        out.append({
            "stage": "proposal",
            "leg": leg_index,
            "status": "available",
            "record_status": "found",
            "proposal_id": proposal_id,
            "proposal_status": leg_row.get("status"),
        })

        risk = _read_first(
            db,
            "SELECT * FROM risk_decisions WHERE proposal_id=? "
            "AND datetime(created_at) <= datetime(?) "
            "ORDER BY datetime(created_at) DESC, rowid DESC LIMIT 1",
            (proposal_id, cutoff_iso),
        )
        out.append(_leg_row(
            "risk_decision", leg_index, risk,
            extra={
                "approved": bool(risk["approved"]) if risk else None,
                "role": risk.get("decision_role") if risk else None,
            } if risk is not None else None,
        ))

        orders = _read_all(
            db,
            "SELECT * FROM orders WHERE proposal_id=? "
            "AND datetime(created_at) <= datetime(?) "
            "AND (close_reason IS NULL OR trim(close_reason)='') "
            "ORDER BY datetime(created_at)",
            (proposal_id, cutoff_iso),
        )
        entry_order = orders[0] if orders else None
        out.append(_leg_row(
            "order", leg_index, entry_order,
            extra={"order_status": entry_order.get("status")} if entry_order else None,
        ))

        fill_count = 0
        if entry_order is not None:
            fills = _read_all(
                db,
                "SELECT * FROM fills WHERE order_id=? "
                "AND datetime(filled_at) <= datetime(?) "
                "ORDER BY datetime(filled_at)",
                (entry_order["id"], cutoff_iso),
            )
            fill_count = len(fills)
        out.append({
            "stage": "fill",
            "leg": leg_index,
            "status": "available" if fill_count else "unavailable",
            "record_status": "found" if fill_count else "missing",
            "fill_count": fill_count,
        })

        position = None
        if entry_order is not None and entry_order.get("position_id"):
            position = _read_first(
                db,
                "SELECT * FROM positions WHERE id=? "
                "AND datetime(opened_at) <= datetime(?)",
                (entry_order["position_id"], cutoff_iso),
            )
        out.append(_leg_row(
            "position", leg_index, position,
            extra={"position_status": position.get("status")} if position else None,
        ))

        close_reason = None
        exit_status: str
        exit_record: str
        if position and str(position.get("status") or "").lower() in _TERMINAL_POSITION_STATUS:
            exit_status = "available"
            exit_record = "found"
            close_reason = position.get("close_reason")
        elif position:
            exit_status = "unavailable"
            exit_record = "open"
        else:
            exit_status = "unavailable"
            exit_record = "missing"
        out.append({
            "stage": "exit",
            "leg": leg_index,
            "status": exit_status,
            "record_status": exit_record,
            "close_reason": close_reason,
        })

    return out


def _leg_row(
    stage: str,
    leg: int,
    row: dict[str, Any] | None,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "stage": stage,
        "leg": leg,
        "status": "available" if row else "unavailable",
        "record_status": "found" if row else "missing",
    }
    if extra:
        body.update(extra)
    return body


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------

def _current_stage_name(stages: list[dict[str, Any]]) -> str:
    """Latest available stage's name; falls back to the last one on the list."""
    for stage in reversed(stages):
        if stage.get("status") == "available":
            return stage["stage"]
    return stages[-1]["stage"] if stages else "unknown"


def _find_first_break(
    lineage: LifecycleLineage,
    leg_stages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if lineage.identity_conflicts:
        return {
            "stage": "identity",
            "reason": lineage.identity_conflicts[0],
            "owner": "lineage_reader",
            "evidence_refs": list(lineage.identity_conflicts),
        }
    if lineage.evidence_gaps:
        return {
            "stage": "evidence",
            "reason": lineage.evidence_gaps[0],
            "owner": "lineage_reader",
            "evidence_refs": list(lineage.evidence_gaps),
        }
    for stage in leg_stages:
        if stage.get("status") == "unavailable" and stage.get("record_status") == "missing":
            return {
                "stage": stage["stage"],
                "reason": f"{stage['stage']}_missing_for_leg_{stage.get('leg')}",
                "owner": "execution_pipeline",
                "evidence_refs": [],
            }
    return None


def _derive_attention_state(
    lineage: LifecycleLineage,
    leg_stages: list[dict[str, Any]],
    first_break: dict[str, Any] | None,
) -> str:
    if lineage.identity_conflicts:
        return "breakpoint"
    if lineage.evidence_gaps:
        return "evidence_gap"

    # Look at the latest per-leg position/order status to decide the state.
    latest_position: dict[str, Any] | None = None
    latest_order: dict[str, Any] | None = None
    for stage in reversed(leg_stages):
        if stage["stage"] == "position" and stage.get("status") == "available":
            latest_position = stage
            break
    for stage in reversed(leg_stages):
        if stage["stage"] == "order" and stage.get("status") == "available":
            latest_order = stage
            break

    if latest_position:
        status = str(latest_position.get("position_status") or "").lower()
        if status in _TERMINAL_POSITION_STATUS:
            return "ended_ok"
    if latest_order and str(latest_order.get("order_status") or "").lower() in _REJECT_ORDER_STATUS:
        return "ended_reject"
    if first_break:
        return "waiting"
    if leg_stages:
        return "advancing"
    # No legs yet, ancestry only.
    return "waiting"


# ---------------------------------------------------------------------------
# Economics loader
# ---------------------------------------------------------------------------

def _load_economics(
    lineage: LifecycleLineage,
    anchor_kind: str,
    anchor_id: str,
    cutoff: datetime,
) -> dict[str, Any]:
    """Attach position_fact when the anchor names a position; candidate_fact stays out of scope here."""
    candidate_fact: dict[str, Any] = {
        "availability": "unavailable",
        "unavailable_reason": "candidate_fact_not_owned_by_lifecycle_inspector",
    }
    position_fact: dict[str, Any] = {
        "availability": "unavailable",
        "unavailable_reason": "anchor_is_not_a_position",
    }
    if anchor_kind == "position" and lineage.evidence_gaps == [] and lineage.entry_plan is not None:
        try:
            from aletheia.analytics.position_replay import build_position_replay
            replay = build_position_replay(anchor_id, as_of_at=cutoff.isoformat())
            position_fact = {"availability": "available", "profit_loop_fact": replay}
        except (ValueError, RuntimeError) as exc:  # narrow — never swallow logic bugs
            position_fact = {
                "availability": "unavailable",
                "unavailable_reason": f"position_replay_failed:{type(exc).__name__}",
            }
    return {"candidate_fact": candidate_fact, "position_fact": position_fact}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_inspection_hints(lineage: LifecycleLineage, anchor_kind: str) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    ep = lineage.entry_plan
    if ep and ep.get("id") and anchor_kind != "entry_plan":
        hints.append({"command": "aletheia inspect lifecycle", "args": {"--entry-plan-id": ep["id"]}})
    cand = lineage.candidate
    if cand and cand.get("id") and anchor_kind not in ("candidate", "entry_plan"):
        hints.append({"command": "aletheia inspect lifecycle", "args": {"--candidate-id": cand["id"]}})
    return hints


def _read_first(db: Any, sql: str, params: tuple) -> dict[str, Any] | None:
    row = db.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def _read_all(db: Any, sql: str, params: tuple) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(sql, params).fetchall()]
