"""Read-only economics API routes.

Mirrors the `aletheia audit` CLI so operators can see LLM spend, broker
headroom, and per-model cooldown state from the dashboard instead of
dropping to a shell. Writers (pricing-set, tool-pricing-set) stay
CLI-only — they mutate the immutable accounting contract and the CLI
already carries the correct identity semantics.

See `docs/design/proposals/tokenomics_harness_phase_plan_2026-07-30.md`
§4 Phase 1 for the design constraints. No behaviour changes anywhere
else; this module only reads.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from aletheia.economics.schema import ensure_economics_schema
from aletheia.economics.tracing import list_cost_summary, tracing_enabled
from aletheia.economics.viability import (
    audit_enabled,
    build_operator_viability,
    build_system_viability,
)
from aletheia.model_provider.fallback import get_model_health_snapshot

router = APIRouter(prefix="/api/economics", tags=["economics"])


@router.get("/status")
def economics_status() -> dict:
    """Report kill-switch state for the two economics subsystems."""
    return {
        "tracing_enabled": tracing_enabled(),
        "audit_enabled": audit_enabled(),
    }


@router.get("/costs")
def costs(
    days: int = Query(7, ge=1, le=365),
    run_id: Optional[str] = None,
    include_events: bool = False,
    event_limit: int = Query(200, ge=1, le=5_000),
) -> dict:
    """Windowed runtime cost summary; mirror of `aletheia audit costs`."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    report = list_cost_summary(
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        run_id=run_id,
    )
    if not include_events:
        report.pop("events", None)
    else:
        report["events"] = report["events"][:event_limit]
    return report


@router.get("/viability")
def viability(
    scope: str = Query("system", pattern="^(system|operator)$"),
    days: int = Query(7, ge=1, le=365),
    execution_mode: str = "paper_internal",
    data_cost_usd: Optional[float] = Query(None, ge=0),
    infrastructure_cost_usd: Optional[float] = Query(None, ge=0),
    other_cost_usd: Optional[float] = Query(None, ge=0),
    as_of: Optional[str] = None,
) -> dict:
    """Compute a viability report on demand.

    Persistence is intentionally disabled on the GET path: this endpoint
    exists for dashboards, not for producing audit rows. `aletheia audit
    viability` remains the persisted-write path.
    """
    if not audit_enabled():
        raise HTTPException(
            status_code=503,
            detail="viability audit disabled by ALETHEIA_VIABILITY_AUDIT",
        )
    if scope == "system":
        return build_system_viability(
            days=days,
            execution_mode=execution_mode,
            data_cost_usd=data_cost_usd,
            infrastructure_cost_usd=infrastructure_cost_usd,
            other_cost_usd=other_cost_usd,
            window_end=as_of,
            persist=False,
        )
    return build_operator_viability(
        days=days,
        execution_mode=execution_mode,
        data_cost_usd=data_cost_usd,
        window_end=as_of,
        persist=False,
    )


@router.get("/viability/reports")
def viability_reports(
    scope: Optional[str] = Query(None, pattern="^(system|operator)$"),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """List historical viability reports without recomputing."""
    db = ensure_economics_schema()
    where_clauses: list[str] = []
    params: list = []
    if scope:
        where_clauses.append("scope = ?")
        params.append(scope)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    rows = [
        dict(row)
        for row in db.execute(
            f"SELECT * FROM viability_reports {where_sql} "
            f"ORDER BY window_end DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    ]
    total_row = db.execute(
        f"SELECT COUNT(*) FROM viability_reports {where_sql}",
        tuple(params),
    ).fetchone()
    total = int(total_row[0]) if total_row else 0
    return {"reports": rows, "limit": limit, "offset": offset, "total": total}


@router.get("/model-health")
def model_health() -> dict:
    """Per-(provider, model) cooldown state from the fallback subsystem."""
    return {"snapshot": get_model_health_snapshot()}


@router.get("/pricing")
def pricing() -> dict:
    """List currently effective pricing snapshots and tool cost profiles."""
    db = ensure_economics_schema()
    models = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM pricing_snapshots "
            "ORDER BY effective_from DESC, created_at DESC LIMIT 200"
        ).fetchall()
    ]
    tools = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM tool_cost_profiles "
            "ORDER BY effective_from DESC, created_at DESC LIMIT 200"
        ).fetchall()
    ]
    return {"models": models, "tools": tools}
