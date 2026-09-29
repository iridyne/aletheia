"""Lifecycle diagnosis assembly from a LifecycleLineage."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aletheia.analytics.candidate_lifecycle.lineage_reader import LifecycleLineage

CONTRACT_VERSION = "lifecycle_diagnosis/v1"


def build_lifecycle_diagnosis(lineage: LifecycleLineage, *, cutoff: datetime) -> dict[str, Any]:
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

    stages = _derive_stages(lineage)
    first_break = _find_first_break(lineage)
    attention = (
        "evidence_gap" if lineage.evidence_gaps else
        "breakpoint" if lineage.identity_conflicts else
        "advancing"
    )

    return {
        "contract_version": CONTRACT_VERSION,
        "anchor": {"kind": anchor_kind, "id": anchor_id},
        "source": {"kind": lineage.source_kind, "ids": ids},
        "evidence_context": {
            "cutoff": cutoff.isoformat(),
            "read_at": datetime.now(timezone.utc).isoformat(),
        },
        "stages": stages,
        "current_stage": stages[-1]["stage"] if stages else "unknown",
        "attention_state": attention,
        "first_break": first_break,
        "coverage": {
            "legs": len(lineage.legs),
            "gaps": list(lineage.evidence_gaps),
            "identity_conflicts": list(lineage.identity_conflicts),
            "truncated": False,
        },
        "economics": {
            "candidate_fact": {
                "availability": "unavailable",
                "unavailable_reason": "not_computed_by_lifecycle_inspector",
            },
            "position_fact": {
                "availability": "unavailable",
                "unavailable_reason": "not_computed_by_lifecycle_inspector",
            },
        },
        "next_inspection": _next_inspection_hints(lineage, anchor_kind),
    }


def _derive_stages(lineage: LifecycleLineage) -> list[dict[str, Any]]:
    stages: list[dict[str, Any]] = []
    if lineage.source_kind == "universe":
        stages.append({
            "stage": "candidate_discovery",
            "status": "available" if lineage.universe_member else "unavailable",
            "record_status": "found" if lineage.universe_member else "missing",
        })
        stages.append({
            "stage": "planning_generation",
            "status": "available" if lineage.generation else "unavailable",
            "record_status": "found" if lineage.generation else "missing",
        })
    elif lineage.source_kind in ("serenity", "opportunity"):
        stages.append({
            "stage": "opportunity",
            "status": "available" if lineage.opportunity_row else "unavailable",
            "record_status": "found" if lineage.opportunity_row else "missing",
        })
    stages.append({
        "stage": "entry_plan",
        "status": "available" if lineage.entry_plan else "unavailable",
        "record_status": "found" if lineage.entry_plan else "missing",
    })
    if lineage.legs:
        stages.append({
            "stage": "proposal",
            "status": "available",
            "record_status": "found",
            "count": len(lineage.legs),
        })
    else:
        stages.append({
            "stage": "proposal",
            "status": "unavailable",
            "record_status": "missing",
        })
    return stages


def _find_first_break(lineage: LifecycleLineage) -> dict[str, Any] | None:
    if lineage.evidence_gaps:
        return {
            "stage": "evidence",
            "reason": lineage.evidence_gaps[0],
            "owner": "lineage_reader",
            "evidence_refs": list(lineage.evidence_gaps),
        }
    if lineage.identity_conflicts:
        return {
            "stage": "identity",
            "reason": lineage.identity_conflicts[0],
            "owner": "lineage_reader",
            "evidence_refs": list(lineage.identity_conflicts),
        }
    return None


def _next_inspection_hints(lineage: LifecycleLineage, anchor_kind: str) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    ep = lineage.entry_plan
    if ep and ep.get("id") and anchor_kind != "entry_plan":
        hints.append({"command": "aletheia inspect lifecycle", "args": {"--entry-plan-id": ep["id"]}})
    cand = lineage.candidate
    if cand and cand.get("id") and anchor_kind not in ("candidate", "entry_plan"):
        hints.append({"command": "aletheia inspect lifecycle", "args": {"--candidate-id": cand["id"]}})
    return hints
