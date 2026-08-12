"""Economics API routes.

Mirrors the `aletheia audit` CLI so operators can see LLM spend, broker
headroom, and per-model cooldown state from the dashboard instead of
dropping to a shell. Immutable manual pricing writers stay CLI-only; the sole
dashboard mutation starts a bounded LiteLLM reference-price refresh whose
results are explicitly estimated.

See `docs/design/proposals/tokenomics_harness_phase_plan_2026-07-30.md`
§4 Phase 1 for the original read-side constraints.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query

from aletheia.economics.budgets import (
    list_budgets,
    list_recent_would_block_events,
)
from aletheia.economics.context_artifacts import (
    list_context_artifacts,
    summarize_context_artifacts,
)
from aletheia.economics.pricing_sync import (
    pricing_refresh_status,
    start_pricing_refresh,
)
from aletheia.economics.schema import ensure_economics_schema
from aletheia.economics.usage_insights import build_usage_insights
from aletheia.economics.tracing import (
    list_cost_breakdown_by_provider,
    list_cost_breakdown_by_tool,
    list_cost_summary,
    list_token_summary,
    tracing_enabled,
)
from aletheia.economics.viability import (
    audit_enabled,
    build_operator_viability,
    build_system_viability,
)
from aletheia.files.jsonl import write_audit
from aletheia.model_provider.fallback import get_model_health_snapshot

router = APIRouter(prefix="/api/economics", tags=["economics"])


@router.get("/usage-insights")
def usage_insights(
    days: int = Query(1),
    group: Literal["summary", "operations", "analysis"] | None = None,
) -> dict:
    """Exact aggregate-only usage projection; never reads prompts or responses."""
    if days not in {1, 7, 30, 90}:
        raise HTTPException(status_code=422, detail="days must be one of 1, 7, 30, or 90")
    return build_usage_insights(days=days, group=group)


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


@router.get("/tokens")
def tokens(
    days: int = Query(7, ge=1, le=365),
    run_id: Optional[str] = None,
) -> dict:
    """Token-first LLM usage summary independent of USD pricing coverage."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return list_token_summary(
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        run_id=run_id,
    )


@router.get("/costs/by-provider")
def costs_by_provider(
    days: int = Query(7, ge=1, le=365),
    run_id: Optional[str] = None,
    event_limit: int = Query(1_000, ge=1, le=100_000),
) -> dict:
    """Per-provider token breakdown + daily time series for the Usage dashboard.

    Pivots LLM ``runtime_cost_events`` rows by ``provider`` and UTC date, parses
    the persisted ``usage_json`` blob (which stores the normalized token fields),
    and returns aggregations compact enough to poll at the same cadence as
    ``/costs``. Read-only; writers stay CLI-only per ``economics.py`` contract.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return list_cost_breakdown_by_provider(
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        run_id=run_id,
        event_limit=event_limit,
    )


@router.get("/costs/by-tool")
def costs_by_tool(
    days: int = Query(7, ge=1, le=365),
    run_id: Optional[str] = None,
    event_limit: int = Query(10_000, ge=1, le=100_000),
) -> dict:
    """Per-tool call rollup for the Usage dashboard.

    Mirrors ``/costs/by-provider`` for ``event_type='tool'`` rows (the
    ``agent_tool_calls`` mirror): call counts split by recorded/denied plus
    known cost from ``tool_cost_profiles``. Tool rows carry no token usage or
    latency, so this rollup deliberately omits those fields.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return list_cost_breakdown_by_tool(
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        run_id=run_id,
        event_limit=event_limit,
    )


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
    total_value = total_row[0] if total_row else 0
    total = total_value if isinstance(total_value, int) else 0
    return {"reports": rows, "limit": limit, "offset": offset, "total": total}


@router.get("/model-health")
def model_health() -> dict:
    """Per-(provider, model) cooldown state from the fallback subsystem."""
    return {"snapshot": get_model_health_snapshot()}


@router.get("/budgets")
def budgets(
    scope: Optional[str] = Query(
        None,
        pattern="^(system|agent_run|logical_agent|provider|provider_tier)$",
    ),
    enabled_only: bool = False,
) -> dict:
    """List declared budget policies (read-only; writers stay in the CLI)."""
    rows = list_budgets(scope=scope, enabled_only=enabled_only)
    return {"budgets": rows}


@router.get("/budgets/would-block")
def budgets_would_block(
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(200, ge=1, le=5_000),
    scope: Optional[str] = Query(
        None,
        pattern="^(system|agent_run|logical_agent|provider|provider_tier)$",
    ),
) -> dict:
    """Recent shadow-mode observations of calls that would have been blocked."""
    events = list_recent_would_block_events(days=days, limit=limit, scope=scope)
    return {"events": events, "window_days": days, "limit": limit}


@router.get("/context-artifacts")
def context_artifacts(
    days: int = Query(7, ge=1, le=365),
    run_id: Optional[str] = None,
    include_artifacts: bool = False,
    limit: int = Query(200, ge=1, le=5_000),
) -> dict:
    """Prompt-free Market Observer context-consumption audit."""
    report = summarize_context_artifacts(days=days, run_id=run_id)
    if include_artifacts:
        report["items"] = list_context_artifacts(days=days, run_id=run_id, limit=limit)
    return report


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


@router.get("/pricing/refresh-status")
def pricing_refresh_status_route() -> dict:
    """Return the in-process LiteLLM refresh state; no network or DB work."""
    return pricing_refresh_status()


@router.post("/pricing/refresh", status_code=202)
def pricing_refresh() -> dict:
    """Start one background reference-price refresh and return immediately."""
    write_audit(
        {
            "actor": "human",
            "source": "dashboard",
            "event_type": "pricing_refresh_requested",
            "entity_type": "pricing_reference",
            "entity_id": "litellm_public_map",
            "message": "Operator requested a LiteLLM estimated-price refresh.",
        }
    )
    return start_pricing_refresh(trigger="dashboard")
