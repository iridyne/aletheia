"""Data API routes for dashboard consumption."""

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime, timezone

from aletheia.storage.sqlite import get_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    SerenityTheme,
    SerenityEvidence,
    SerenityOpportunity,
    SerenityRiskGate,
    SerenityReplaySnapshot,
    SerenityPostMortem,
    SerenityResearchRun,
    SerenityRunEvent,
    SerenityUserProfile,
    SerenityProfileSnapshot,
    SerenityFeedbackEvent,
)
from aletheia.trading.serenity_scoring import (
    make_replay_snapshot_payload,
    score_serenity_opportunity,
)
from aletheia.trading.serenity_evidence import (
    assess_evidence_tier,
    normalize_tier,
)

router = APIRouter(prefix="/api", tags=["data"])

VALID_KNOWLEDGE_TYPES = {
    "research_note",
    "strategy_memory",
    "daily_review",
    "dreaming_log",
    "post_trade_review",
}


def _ensure_db():
    # No-op: migrations run once at app startup (aletheia/server/app.py:44).
    # Calling run_migrations() per request was the dominant `database is locked` source
    # — `executescript(SCHEMA_SQL)` + 19 ALTER probes contended with every concurrent
    # writer. Kept as a function so existing route call sites stay valid.
    pass


def _truthy_flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "none", "null"}
    return bool(value)


def _parse_utc_timestamp(value) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    elif " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_json(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, (dict, list, tuple)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return fallback
        return parsed
    return fallback


def _dump_json(value, fallback="[]"):
    if value is None:
        return fallback
    if isinstance(value, str):
        # already serialized string
        return value
    return json.dumps(value)


PIPELINE_STAGE_ORDER = [
    "research",
    "validation",
    "demand_wave",
    "supply_chain",
    "ramp",
    "order_night",
    "candidate_position",
]


def _summarize_failure_buckets(post_mortems: list[dict]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in post_mortems:
        bucket = row.get("failure_bucket") or "unclassified"
        if bucket is None:
            bucket = "unclassified"
        bucket = str(bucket).strip() or "unclassified"
        summary[bucket] = summary.get(bucket, 0) + 1
    return summary


def _summarize_gate_status(gates: list[dict]) -> dict[str, int]:
    status_counts = {"pass": 0, "warn": 0, "fail": 0, "unknown": 0}
    for g in gates:
        status = str(g.get("status", "")).strip().lower()
        if status not in status_counts:
            status_counts["unknown"] += 1
        else:
            status_counts[status] += 1
    return status_counts


def _normalize_stage_for_pipeline(stage: str | None) -> str:
    stage_value = str(stage or "research").strip().lower()
    if stage_value in {"design_in", "design-in", "sample"}:
        return "supply_chain"
    if stage_value in {"order"}:
        return "order_night"
    if stage_value == "candidate":
        return "candidate_position"
    return stage_value if stage_value in PIPELINE_STAGE_ORDER else "research"


def _parse_replay_payload(payload: str | None) -> dict:
    parsed = _to_json(payload, {})
    return parsed if isinstance(parsed, dict) else {}


def _score_serenity_opportunity_payload(opportunity: dict, include_evidence=True, include_gates=True) -> dict:
    evidence: list[dict] = []
    risk_gates: list[dict] = []
    if include_evidence and opportunity.get("theme_id"):
        evidence = repo.list_serenity_evidence(theme_id=opportunity["theme_id"], limit=200)
    if include_gates:
        risk_gates = repo.list_serenity_risk_gates(opportunity_id=opportunity["id"], limit=200)
    score = score_serenity_opportunity(opportunity, evidence=evidence, risk_gates=risk_gates)
    payload = dict(opportunity)
    payload["risk_gates"] = risk_gates
    payload["risk_flags"] = _to_json(opportunity.get("risk_flags"), [])
    payload["risk_metrics"] = _to_json(opportunity.get("risk_metrics"), {})
    payload["serenity_score"] = score
    return payload


def _build_research_cycle_payload(opportunity: dict) -> dict:
    evidence = repo.list_serenity_evidence(theme_id=opportunity.get("theme_id"), limit=200)
    risk_gates = repo.list_serenity_risk_gates(opportunity_id=opportunity["id"], limit=200)
    snapshots = repo.list_serenity_replay_snapshots(opportunity_id=opportunity["id"], limit=200)
    post_mortems = repo.list_serenity_post_mortems(opportunity_id=opportunity["id"], limit=200)

    score = score_serenity_opportunity(opportunity, evidence=evidence, risk_gates=risk_gates)

    gate_status = _summarize_gate_status(risk_gates)
    timeline = []
    for snap in snapshots:
        snap_payload = _parse_replay_payload(snap.get("payload"))
        timeline.append({
            "id": snap["id"],
            "as_of": snap["as_of"],
            "created_at": snap["created_at"],
            "event_type": snap.get("event_type"),
            "event_title": snap.get("event_title"),
            "stage": snap.get("stage"),
            "score_total": snap.get("score_total"),
            "bucket": snap_payload.get("bucket"),
            "evidence_count": snap.get("evidence_count"),
            "risk_fail_gates": snap.get("risk_fail_gates"),
            "risk_warn_gates": snap.get("risk_warn_gates"),
        })

    payload = _score_serenity_opportunity_payload(opportunity, include_evidence=False, include_gates=False)
    payload["serenity_score"] = score
    payload["evidence_count"] = len(evidence)
    return {
        "opportunity": payload,
        "timeline": list(reversed(timeline)),
        "risk_gates": risk_gates,
        "risk_gate_summary": gate_status,
        "post_mortems": post_mortems,
        "post_mortem_summary": {
            "count": len(post_mortems),
            "failure_bucket": _summarize_failure_buckets(post_mortems),
        },
    }


@router.get("/agent-runs")
async def get_agent_runs():
    _ensure_db()
    return repo.list_agent_runs()


@router.get("/agent-runs/{run_id}")
async def get_agent_run(run_id: str):
    _ensure_db()
    return repo.get_agent_run(run_id)


@router.get("/agent-runs/{run_id}/tool-calls")
async def get_agent_run_tool_calls(run_id: str):
    """Get structured tool call records for a specific agent run."""
    _ensure_db()
    return repo.list_tool_calls(run_id=run_id, limit=200)


@router.get("/agent-runs/{run_id}/steps")
async def get_agent_run_steps(run_id: str):
    """Get the ordered reasoning/action steps (inputs/outputs) for an agent run.

    Read-only view of the agent_run_steps table. Returns [] for runs that recorded
    no steps — the UI renders that honestly rather than implying a trace exists.
    """
    _ensure_db()
    return repo.list_run_steps(run_id=run_id, limit=500)


class StartAgentRequest(BaseModel):
    agent_name: str = "market_observer"


@router.post("/agent-runs/start")
async def start_agent_run(req: StartAgentRequest):
    _ensure_db()
    from aletheia.agent_runtime.orchestrator import run_agent
    result = await run_agent(req.agent_name, trigger_type="manual")
    return result


@router.get("/opportunities")
async def get_opportunities(asset_class: str | None = None):
    _ensure_db()
    if asset_class:
        return repo.list_opportunities_by_asset_class(asset_class)
    return repo.list_opportunities()


class SerenityThemeCreate(BaseModel):
    name: str
    demand_wave: str | None = None
    objective: str | None = None
    description: str | None = None
    strategy_notes: str | None = None
    tags: list[str] = []
    status: str = "active"


@router.get("/serenity/themes")
async def get_serenity_themes(status: str = Query(default="active")):
    _ensure_db()
    items = repo.list_serenity_themes(status=status)
    for item in items:
        item["tags"] = _to_json(item.get("tags"), [])
    return items


@router.post("/serenity/themes")
async def create_serenity_theme(payload: SerenityThemeCreate):
    _ensure_db()
    theme = SerenityTheme(
        name=payload.name,
        demand_wave=payload.demand_wave,
        objective=payload.objective,
        description=payload.description,
        strategy_notes=payload.strategy_notes,
        tags=_dump_json(payload.tags),
        status=payload.status,
    )
    repo.insert_serenity_theme(theme)
    return {"id": theme.id}


@router.get("/serenity/themes/{theme_id}/overview")
async def get_serenity_theme_overview(theme_id: str):
    _ensure_db()
    row = repo.get_serenity_theme(theme_id)
    if row is None:
        raise HTTPException(404, "Theme not found")
    evidence = repo.list_serenity_evidence(theme_id=theme_id, limit=1000)
    opportunities = repo.list_serenity_opportunities(theme_id=theme_id, status=None, limit=1000)

    stage_counts: dict[str, int] = {}
    avg_mode_counts: dict[str, int] = {}
    for opp in opportunities:
        stage = _normalize_stage_for_pipeline(opp.get("stage"))
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        mode = opp.get("average_mode") or "none"
        avg_mode_counts[str(mode)] = avg_mode_counts.get(str(mode), 0) + 1

    return {
        "theme": row,
        "metrics": {
            "opportunity_count": len(opportunities),
            "evidence_count": len(evidence),
            "stage_counts": stage_counts,
            "average_mode_counts": avg_mode_counts,
            "pipeline_order": PIPELINE_STAGE_ORDER,
        },
    }


@router.get("/serenity/themes/{theme_id}")
async def get_serenity_theme(theme_id: str):
    _ensure_db()
    row = repo.get_serenity_theme(theme_id)
    if row is None:
        raise HTTPException(404, "Theme not found")
    row["tags"] = _to_json(row.get("tags"), [])
    return row


class SerenityEvidenceCreate(BaseModel):
    theme_id: str
    symbol: str | None = None
    source: str = "manual"
    source_ref: str | None = None
    title: str
    summary: str = ""
    tier: str = "speculative"
    supports: bool = True
    evidence_type: str = "thesis"
    event_at: str | None = None
    actor: str | None = None
    confidence: str = "speculative"
    tags: list[str] = []


@router.get("/serenity/evidence")
async def get_serenity_evidence(theme_id: str | None = None, limit: int = 100):
    _ensure_db()
    items = repo.list_serenity_evidence(theme_id=theme_id, limit=limit)
    for item in items:
        item["tags"] = _to_json(item.get("tags"), [])
    return items


@router.post("/serenity/evidence")
async def create_serenity_evidence(payload: SerenityEvidenceCreate):
    _ensure_db()
    evidence = SerenityEvidence(
        theme_id=payload.theme_id,
        symbol=payload.symbol,
        source=payload.source,
        source_ref=payload.source_ref,
        title=payload.title,
        summary=payload.summary,
        # `tier` stays the submitter's proposal; the deterministic evaluator assigns the
        # authoritative `assessed_tier` that gates scoring (objective source/ref constraints
        # apply to manual entries too, e.g. "disclosed" requires a checkable external ref).
        tier=normalize_tier(payload.tier),
        supports=payload.supports,
        evidence_type=payload.evidence_type,
        event_at=payload.event_at or datetime.now(timezone.utc).isoformat(),
        actor=payload.actor,
        confidence=normalize_tier(payload.confidence),
        tags=_dump_json(payload.tags),
    )
    evidence.assessed_tier, evidence.assessed_by = assess_evidence_tier(evidence.model_dump())
    repo.insert_serenity_evidence(evidence)
    return {"id": evidence.id}


class SerenityOpportunityCreate(BaseModel):
    theme_id: str
    symbol: str
    direction: str
    thesis: str | None = None
    evidence_tier: str = "speculative"
    stage: str = "research"
    average_mode: str = "none"
    action_intent: str = "watch"
    status: str = "active"
    risk_flags: list[str] = []
    risk_metrics: dict | None = None
    confidence_score: int | None = None


@router.get("/serenity/opportunities")
async def get_serenity_opportunities(
    theme_id: str | None = None,
    status: str | None = None,
    stage: str | None = None,
    include_scores: bool = True,
    limit: int = 100,
):
    _ensure_db()
    opportunities = repo.list_serenity_opportunities(theme_id=theme_id, status=status, stage=stage, limit=limit)
    if not include_scores:
        return opportunities
    return [_score_serenity_opportunity_payload(opportunity=o) for o in opportunities]


@router.post("/serenity/opportunities")
async def create_serenity_opportunity(payload: SerenityOpportunityCreate):
    _ensure_db()
    # Manual operator-created opportunities: the human IS the independent evaluator,
    # so the submitted tier is authoritative (assessed_by="operator"). Normalize into
    # the canonical ladder so it gates scoring identically to agent-produced records (D2).
    normalized_tier = normalize_tier(payload.evidence_tier)
    opp = SerenityOpportunity(
        theme_id=payload.theme_id,
        symbol=payload.symbol,
        direction=payload.direction,
        thesis=payload.thesis,
        evidence_tier=normalized_tier,
        assessed_tier=normalized_tier,
        assessed_by="operator",
        stage=payload.stage,
        average_mode=payload.average_mode,
        action_intent=payload.action_intent,
        status=payload.status,
        risk_flags=_dump_json(payload.risk_flags),
        risk_metrics=json.dumps(payload.risk_metrics or {}),
        confidence_score=payload.confidence_score,
    )
    repo.insert_serenity_opportunity(opp)
    return {"id": opp.id}


@router.get("/serenity/opportunities/{opportunity_id}")
async def get_serenity_opportunity(opportunity_id: str, include_scores: bool = True):
    _ensure_db()
    row = repo.get_serenity_opportunity(opportunity_id)
    if row is None:
        raise HTTPException(404, "Opportunity not found")
    if include_scores:
        return _score_serenity_opportunity_payload(opportunity=row)
    row["risk_flags"] = _to_json(row.get("risk_flags"), [])
    row["risk_metrics"] = _to_json(row.get("risk_metrics"), {})
    return row


@router.get("/serenity/opportunities/{opportunity_id}/research-cycle")
async def get_serenity_opportunity_research_cycle(opportunity_id: str):
    _ensure_db()
    opp = _ensure_serenity_opportunity_exists(opportunity_id)
    return _build_research_cycle_payload(opp)


@router.get("/serenity/opportunities/{opportunity_id}/score")
async def score_serenity_opportunity_endpoint(opportunity_id: str):
    _ensure_db()
    opp = repo.get_serenity_opportunity(opportunity_id)
    if opp is None:
        raise HTTPException(404, "Opportunity not found")
    evidence = repo.list_serenity_evidence(theme_id=opp.get("theme_id"), limit=200)
    gates = repo.list_serenity_risk_gates(opportunity_id=opportunity_id, limit=200)
    return score_serenity_opportunity(opp, evidence=evidence, risk_gates=gates)


class SerenityRiskGateCreate(BaseModel):
    gate_name: str
    status: str
    metric: str
    observed: str | int | float | None = None
    threshold: str | int | float | None = None
    message: str | None = None


@router.get("/serenity/opportunities/{opportunity_id}/risk-gates")
async def get_serenity_opportunity_risk_gates(opportunity_id: str):
    _ensure_db()
    _ensure_serenity_opportunity_exists(opportunity_id)
    return repo.list_serenity_risk_gates(opportunity_id=opportunity_id, limit=200)


@router.post("/serenity/opportunities/{opportunity_id}/risk-gates")
async def create_serenity_opportunity_risk_gate(opportunity_id: str, payload: SerenityRiskGateCreate):
    _ensure_db()
    _ensure_serenity_opportunity_exists(opportunity_id)
    gate = SerenityRiskGate(
        opportunity_id=opportunity_id,
        gate_name=payload.gate_name,
        status=payload.status,
        metric=payload.metric,
        observed=payload.observed,
        threshold=payload.threshold,
        message=payload.message,
    )
    repo.insert_serenity_risk_gate(gate)
    return {"id": gate.id}


class SerenityReplaySnapshotCreate(BaseModel):
    as_of: str
    stage: str | None = None
    event_type: str = "snapshot"
    event_title: str = "snapshot"
    payload: dict | None = None


@router.get("/serenity/opportunities/{opportunity_id}/replay")
async def get_serenity_replay(opportunity_id: str, limit: int = 50):
    _ensure_db()
    _ensure_serenity_opportunity_exists(opportunity_id)
    return repo.list_serenity_replay_snapshots(opportunity_id=opportunity_id, limit=limit)


@router.post("/serenity/opportunities/{opportunity_id}/replay")
async def create_serenity_replay_snapshot(opportunity_id: str, payload: SerenityReplaySnapshotCreate):
    _ensure_db()
    opp = _ensure_serenity_opportunity_exists(opportunity_id)
    evidence = repo.list_serenity_evidence(theme_id=opp.get("theme_id"), limit=200)
    gates = repo.list_serenity_risk_gates(opportunity_id=opportunity_id, limit=200)
    score = score_serenity_opportunity(opp, evidence=evidence, risk_gates=gates)
    extra = payload.payload or make_replay_snapshot_payload(opp, score=score)
    row = SerenityReplaySnapshot(
        opportunity_id=opportunity_id,
        as_of=payload.as_of,
        evidence_count=score["evidence_count"],
        score_total=score["score"],
        stage=payload.stage or opp.get("stage", "research"),
        event_type=payload.event_type,
        event_title=payload.event_title,
        payload=json.dumps(extra),
    )
    repo.insert_serenity_replay_snapshot(row)
    return {"id": row.id, "score": score}


class SerenityPostMortemCreate(BaseModel):
    outcome: str
    failure_bucket: str | None = None
    score_delta: int | None = None
    attribution: str | None = None
    summary: str
    created_by: str = "system"


@router.get("/serenity/opportunities/{opportunity_id}/post-mortems")
async def get_serenity_post_mortems(opportunity_id: str):
    _ensure_db()
    _ensure_serenity_opportunity_exists(opportunity_id)
    return repo.list_serenity_post_mortems(opportunity_id=opportunity_id, limit=200)


@router.post("/serenity/opportunities/{opportunity_id}/post-mortems")
async def create_serenity_post_mortem(opportunity_id: str, payload: SerenityPostMortemCreate):
    _ensure_db()
    _ensure_serenity_opportunity_exists(opportunity_id)
    row = SerenityPostMortem(
        opportunity_id=opportunity_id,
        outcome=payload.outcome,
        failure_bucket=payload.failure_bucket,
        score_delta=payload.score_delta,
        attribution=payload.attribution,
        summary=payload.summary,
        created_by=payload.created_by,
    )
    repo.insert_serenity_post_mortem(row)
    return {"id": row.id}


def _ensure_serenity_opportunity_exists(opportunity_id: str) -> dict:
    row = repo.get_serenity_opportunity(opportunity_id)
    if row is None:
        raise HTTPException(404, "Opportunity not found")
    return row


@router.get("/serenity/pipeline")
async def get_serenity_pipeline():
    """Serenity research pipeline summary: recent runs + active opportunity count (D7)."""
    _ensure_db()
    from aletheia.storage.sqlite import get_db as _get_db
    runs = repo.list_serenity_research_runs(limit=20)
    db = _get_db()
    active_count = db.execute(
        "SELECT COUNT(*) FROM serenity_opportunities WHERE status = 'active'"
    ).fetchone()[0]
    return {"runs": runs, "active_opportunity_count": active_count}


@router.get("/proposals")
async def get_proposals():
    _ensure_db()
    return repo.list_trade_proposals()


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str):
    _ensure_db()
    row = repo.get_trade_proposal(proposal_id)
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="proposal not found")
    return row


@router.post("/proposals/{proposal_id}/approve-paper")
async def approve_proposal(proposal_id: str):
    """Approve a proposal and route it through the execution gateway (paper)."""
    _ensure_db()
    from aletheia.files.jsonl import write_audit
    p = repo.get_trade_proposal(proposal_id)
    if p is None:
        raise HTTPException(404, "Proposal not found")

    status = str(p.get("status") or "").lower()
    if status == "executed":
        return {"proposal_id": proposal_id, "execution": {"status": "already_executed", "proposal_id": proposal_id}}
    if status in {"error", "rejected"}:
        return {"proposal_id": proposal_id, "execution": {"status": status, "proposal_id": proposal_id}}

    old_status = status
    repo.update_trade_proposal(proposal_id, status="approved")
    write_audit({"actor": "human", "source": "dashboard", "event_type": "proposal_approved",
                 "entity_type": "trade_proposal", "entity_id": proposal_id,
                 "before": {"status": old_status}, "after": {"status": "approved"},
                 "message": "Proposal approved for paper execution"})
    from aletheia.execution.gateway import execute_existing_proposal
    try:
        result = execute_existing_proposal(proposal_id, execution_mode="paper_internal")
    except Exception as e:
        repo.update_trade_proposal(proposal_id, status="error")
        write_audit({"actor": "system", "source": "dashboard", "event_type": "execution_error",
                     "entity_type": "trade_proposal", "entity_id": proposal_id,
                     "before": {"status": "approved"}, "after": {"status": "error"},
                     "message": f"Execution failed after approval: {e}"})
        return {"proposal_id": proposal_id, "execution": {"status": "error", "message": str(e)}}
    return {"proposal_id": proposal_id, "execution": result}


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: str):
    _ensure_db()
    from aletheia.files.jsonl import write_audit
    p = repo.get_trade_proposal(proposal_id)
    if p is None:
        raise HTTPException(404, "Proposal not found")
    repo.update_trade_proposal(proposal_id, status="rejected")
    write_audit({"actor": "human", "source": "dashboard", "event_type": "proposal_rejected",
                 "entity_type": "trade_proposal", "entity_id": proposal_id,
                 "message": "Proposal rejected by human"})
    return {"proposal_id": proposal_id, "status": "rejected"}


@router.get("/trades")
async def get_trades():
    _ensure_db()
    return repo.list_orders()


@router.get("/trades/export")
async def export_trades(fmt: str = Query(default="csv", pattern="^csv$"), limit: int = Query(default=5000, le=50000)):
    """Export trade history as CSV for tax/PnL reporting."""
    from fastapi.responses import StreamingResponse
    import csv
    import io

    _ensure_db()
    rows = repo.list_orders(limit=limit)
    if not rows:
        return StreamingResponse(
            iter(["no data\n"]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=trades_export.csv"},
        )

    columns = ["created_at", "symbol", "direction", "order_type", "quantity", "price",
               "execution_mode", "status", "filled_at", "market_type", "exchange_order_id"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(c, "") for c in columns])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trades_export.csv"},
    )


@router.get("/positions")
async def get_positions():
    _ensure_db()
    return repo.list_positions()


@router.get("/portfolio")
async def get_portfolio():
    _ensure_db()
    from aletheia.portfolio.pnl import compute_portfolio
    return compute_portfolio(execution_mode=None)


@router.get("/exchange/account")
async def get_exchange_account():
    """Cached real-account view (closes portfolio-sync-gap).

    Read-only. Returns the last sync result + freshness + status. The HTTP path
    NEVER hits Bitget synchronously — the scheduler refreshes the cache. UI uses
    `status` to render honest fallbacks (unconfigured / stale / error).
    """
    _ensure_db()
    from aletheia.portfolio import exchange_sync
    state = exchange_sync.get_cached_state()
    status = exchange_sync.status_for(state)
    return {"status": status, "state": state}


@router.get("/positions/reconciliation")
async def get_positions_reconciliation():
    """Classify every account position as managed / external / orphaned.

    Read-only. Joins the cached exchange position view against locally-tracked open
    positions so the UI/operator can see which positions Aletheia did NOT open
    (external — adoption candidates) and which it tracks but the exchange no longer
    has (orphaned — reconcile-close candidates). Never hits Bitget synchronously.
    """
    _ensure_db()
    from aletheia.portfolio.position_reconciler import classify_positions
    return classify_positions().to_dict()


@router.post("/positions/reconcile")
async def run_positions_reconcile():
    """Manual trigger: classify → auto-adopt externals → close orphans (demo only).

    Same logic as the scheduled job but on-demand. Live mode returns classification
    only (no autonomous action). Surfaces in /api/agent-runs for observability.
    """
    _ensure_db()
    from aletheia.portfolio.position_reconciler import run_reconcile
    return run_reconcile()


@router.get("/audit/events")
async def get_audit_events(limit: int = 100):
    _ensure_db()
    limit = max(1, min(limit, 500))
    return repo.list_audit_events(limit=limit)


@router.get("/strategies")
async def get_strategies():
    _ensure_db()
    return repo.list_strategies()


@router.get("/strategies/summary")
async def get_strategies_summary():
    """Aggregated strategy-governance summary for operator evaluation.

    Surfaces operator-facing strategy flags plus recent strategy evaluation artifacts so the
    operator loop can decide which strategies still need a fresh evaluation session.
    """
    _ensure_db()
    from aletheia.agent_shell.operator_store import list_artifacts

    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    strategies = repo.list_strategies(limit=200)

    active_paper_strategies = [
        item for item in strategies if item.get("state") in {"paper_candidate", "paper_running", "paper_passed"}
    ]
    under_review_strategies = [item for item in strategies if _truthy_flag(item.get("under_review"))]
    paused_paper_strategies = [item for item in strategies if _truthy_flag(item.get("paper_trading_paused"))]
    live_candidate_strategies = [
        item for item in strategies if item.get("state") in {"live_candidate", "live_limited"}
    ]

    raw_recent_strategy_evaluations = [
        artifact
        for artifact in list_artifacts(limit=500)
        if artifact.get("artifact_type") == "strategy_evaluation"
        and (_parse_utc_timestamp(artifact.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
        > cutoff_24h
    ]
    recent_strategy_evaluations = []
    for artifact in raw_recent_strategy_evaluations:
        content = artifact.get("content")
        if not isinstance(content, dict):
            content = {}
        recent_strategy_evaluations.append(
            {
                "id": artifact.get("id"),
                "task_id": artifact.get("task_id"),
                "session_id": artifact.get("session_id"),
                "artifact_type": artifact.get("artifact_type"),
                "title": artifact.get("title"),
                "created_at": artifact.get("created_at"),
                "strategy_id": content.get("strategy_id"),
                "strategy_name": content.get("strategy_name"),
                "summary": content.get("summary"),
                "evaluation": content.get("evaluation"),
                "content": content,
            }
        )
    evaluated_strategy_ids = {
        str(item.get("strategy_id"))
        for item in recent_strategy_evaluations
        if item.get("strategy_id")
    }
    raw_recent_strategy_promotion_proposals = [
        artifact
        for artifact in list_artifacts(limit=500)
        if artifact.get("artifact_type") == "strategy_promotion_proposal"
        and (_parse_utc_timestamp(artifact.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
        > cutoff_24h
    ]
    recent_strategy_promotion_proposals = []
    for artifact in raw_recent_strategy_promotion_proposals:
        content = artifact.get("content")
        if not isinstance(content, dict):
            content = {}
        recent_strategy_promotion_proposals.append(
            {
                "id": artifact.get("id"),
                "task_id": artifact.get("task_id"),
                "session_id": artifact.get("session_id"),
                "artifact_type": artifact.get("artifact_type"),
                "title": artifact.get("title"),
                "created_at": artifact.get("created_at"),
                "strategy_id": content.get("strategy_id"),
                "strategy_name": content.get("strategy_name"),
                "current_state": content.get("current_state"),
                "target_state": content.get("target_state"),
                "gate_preview": content.get("gate_preview"),
                "rationale": content.get("rationale"),
                "content": content,
            }
        )
    raw_recent_live_limited_enablement_proposals = [
        artifact
        for artifact in list_artifacts(limit=500)
        if artifact.get("artifact_type") == "live_limited_enablement_proposal"
        and (_parse_utc_timestamp(artifact.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
        > cutoff_24h
    ]
    recent_live_limited_enablement_proposals = []
    for artifact in raw_recent_live_limited_enablement_proposals:
        content = artifact.get("content")
        if not isinstance(content, dict):
            content = {}
        recent_live_limited_enablement_proposals.append(
            {
                "id": artifact.get("id"),
                "task_id": artifact.get("task_id"),
                "session_id": artifact.get("session_id"),
                "artifact_type": artifact.get("artifact_type"),
                "title": artifact.get("title"),
                "created_at": artifact.get("created_at"),
                "strategy_id": content.get("strategy_id"),
                "strategy_name": content.get("strategy_name"),
                "current_state": content.get("current_state"),
                "target_state": content.get("target_state"),
                "gate_preview": content.get("gate_preview"),
                "rationale": content.get("rationale"),
                "content": content,
            }
        )

    flagged_strategy_ids = {
        str(item.get("id"))
        for item in [*under_review_strategies, *paused_paper_strategies]
        if item.get("id")
    }
    needs_operator_evaluation_strategy_ids = sorted(flagged_strategy_ids - evaluated_strategy_ids)
    promotion_ready_strategy_targets = {
        (
            str(item.get("strategy_id")),
            "paper_passed"
            if str(
                ((item.get("evaluation") or {}).get("strategy") or {}).get("state") or ""
            )
            == "paper_running"
            else "live_candidate",
        )
        for item in recent_strategy_evaluations
        if item.get("strategy_id")
        and isinstance(item.get("evaluation"), dict)
        and isinstance(item["evaluation"].get("strategy"), dict)
        and str(item["evaluation"]["strategy"].get("state") or "")
        in {"paper_running", "paper_passed"}
        and isinstance(item["evaluation"].get("promotion_gates"), dict)
        and bool(item["evaluation"]["promotion_gates"].get("passed")) is True
    }
    proposed_strategy_targets = {
        (str(item.get("strategy_id")), str(item.get("target_state") or ""))
        for item in recent_strategy_promotion_proposals
        if item.get("strategy_id") and item.get("target_state")
    }
    needs_operator_promotion_proposal_strategy_ids = sorted(
        strategy_id
        for strategy_id, target_state in promotion_ready_strategy_targets
        if (strategy_id, target_state) not in proposed_strategy_targets
    )
    live_limited_ready_strategy_ids = {
        str(item.get("strategy_id"))
        for item in recent_strategy_evaluations
        if item.get("strategy_id")
        and isinstance(item.get("evaluation"), dict)
        and isinstance(item["evaluation"].get("strategy"), dict)
        and str(item["evaluation"]["strategy"].get("state") or "") == "live_candidate"
        and isinstance(item["evaluation"].get("live_limited_gates"), dict)
        and bool(item["evaluation"]["live_limited_gates"].get("passed")) is True
    }
    proposed_live_limited_strategy_ids = {
        str(item.get("strategy_id"))
        for item in recent_live_limited_enablement_proposals
        if item.get("strategy_id")
    }
    needs_operator_live_limited_enablement_strategy_ids = sorted(
        live_limited_ready_strategy_ids - proposed_live_limited_strategy_ids
    )

    return {
        "active_paper_strategies": active_paper_strategies,
        "under_review_strategies": under_review_strategies,
        "paused_paper_strategies": paused_paper_strategies,
        "live_candidate_strategies": live_candidate_strategies,
        "recent_strategy_evaluations": recent_strategy_evaluations,
        "needs_operator_evaluation_strategy_ids": needs_operator_evaluation_strategy_ids,
        "recent_strategy_promotion_proposals": recent_strategy_promotion_proposals,
        "needs_operator_promotion_proposal_strategy_ids": needs_operator_promotion_proposal_strategy_ids,
        "recent_live_limited_enablement_proposals": recent_live_limited_enablement_proposals,
        "needs_operator_live_limited_enablement_strategy_ids": needs_operator_live_limited_enablement_strategy_ids,
    }


@router.get("/strategies/{strategy_id}")
async def get_strategy(strategy_id: str):
    _ensure_db()
    s = repo.get_strategy(strategy_id)
    if s is None:
        raise HTTPException(404, "Strategy not found")
    return s


_VALID_VENUES = {"bitget_demo", "binance_testnet", "okx_demo", "paper_internal", None}


class SetVenueRequest(BaseModel):
    execution_venue: str | None = None


@router.patch("/strategies/{strategy_id}/venue")
async def set_strategy_venue(strategy_id: str, req: SetVenueRequest):
    _ensure_db()
    s = repo.get_strategy(strategy_id)
    if s is None:
        raise HTTPException(404, "Strategy not found")
    if req.execution_venue and req.execution_venue not in _VALID_VENUES:
        raise HTTPException(400, f"Invalid venue: {req.execution_venue}")
    repo.update_strategy(strategy_id, execution_venue=req.execution_venue)
    return {"strategy_id": strategy_id, "execution_venue": req.execution_venue}


@router.get("/market/snapshots")
async def get_market_snapshots(
    symbol: str | None = None,
    limit: int = 200,
    exchange: str = "",
):
    """Return market snapshots.

    Optional filters:
    - symbol: canonical BTC/USDT format
    - exchange: source exchange name (e.g. "binance", "bybit")
    """
    _ensure_db()
    return repo.list_market_snapshots(symbol=symbol, limit=limit, exchange=exchange)


@router.get("/market/ohlcv")
async def get_market_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    limit: int = 100,
    exchange: str = "",
    market_type: str = "spot",
    before: int = 0,
):
    """Fetch OHLCV candles via CCXT for charting (public endpoint).

    Optional:
    - exchange: prefer this exchange (e.g. "binance"). Defaults to config ccxt_exchange.
    - market_type: "spot" (default) | "perp" — for perpetual contract candles.
    - before: unix SECONDS. When > 0, return the page of up to `limit` candles
      whose open time is strictly BEFORE this timestamp — i.e. older history, used
      when the chart scrolls back. When 0 (default), return the most recent `limit`.

    Caching: the TTL cache is only used for the default latest view at the default
    limit (before unset). Historical pages (before>0) and any non-default limit are
    fetched fresh — historical because each window is distinct, and the latest view
    needs a live tail rather than a cached candle that would freeze the chart.
    """
    import logging
    from aletheia.config.loader import load_config
    from aletheia.data.ccxt_provider import get_market_data_provider, timeframe_ms
    from aletheia.data.cache import get_cache
    from aletheia.data.symbols import to_canonical, InstrumentId
    cfg = load_config()
    md_cfg = cfg.market_data
    sym = to_canonical(symbol)
    effective_exchange = exchange or md_cfg.ccxt_exchange
    limit = max(1, min(limit, 1000))  # bound to CCXT's typical per-request ceiling

    # Build an InstrumentId so CCXTProvider uses the correct CCXT symbol internally
    try:
        instrument = InstrumentId.from_parts(effective_exchange, sym, market_type)
    except ValueError:
        return {"error": f"invalid_instrument: {effective_exchange}:{sym}:{market_type}", "candles": []}

    # Historical pagination window: ask the exchange for `limit` candles ending
    # just before `before`. since = before - limit*tf yields a window whose newest
    # candle is the one immediately preceding `before`.
    since_ms: int | None = None
    if before > 0:
        tf_ms = timeframe_ms(timeframe)
        if tf_ms == 0:
            return {"error": f"unsupported_timeframe: {timeframe}", "candles": []}
        since_ms = before * 1000 - limit * tf_ms

    cache = get_cache()
    cache_key = sym  # cache by canonical symbol (same data regardless of exchange for spot)
    use_cache = since_ms is None and limit == 100
    try:
        candles = cache.get(cache_key, timeframe, limit) if use_cache else None
        if candles is None:
            provider = get_market_data_provider(effective_exchange)
            try:
                candles = await provider.get_ohlcv(
                    instrument, timeframe=timeframe, limit=limit, since=since_ms,
                )
                if use_cache:
                    cache.set(cache_key, timeframe, limit, candles)
            finally:
                await provider.close()
        # For a historical page, drop any candle at/after `before` so the prepend
        # on the frontend never overlaps the existing left-most bar.
        if before > 0:
            cutoff_ms = before * 1000
            candles = [c for c in candles if c.timestamp < cutoff_ms]
        # lightweight-charts wants ascending time in seconds
        rows = [
            {"time": int(c.timestamp / 1000), "open": c.open, "high": c.high,
             "low": c.low, "close": c.close, "volume": c.volume}
            for c in sorted(candles, key=lambda c: c.timestamp)
        ]
        return rows
    except Exception as e:
        logging.getLogger("aletheia.routes.data").warning("OHLCV fetch failed: %s", e)
        return {"error": "market_data_unavailable", "candles": []}


@router.get("/market/portfolio-history")
async def get_portfolio_history(limit: int = 500, days: int | None = None):
    _ensure_db()
    return repo.list_portfolio_snapshots(limit=limit, days=days)


@router.get("/market/macro")
async def get_macro_snapshot():
    """Latest macro snapshot: VIX, DXY, SPY, BTC dominance, risk regime."""
    _ensure_db()
    snap = repo.latest_macro_snapshot()
    if not snap:
        return {"available": False, "data": None}
    return {"available": True, "data": snap}


@router.get("/knowledge")
async def get_knowledge(type: str = Query(default="research_note")):
    _ensure_db()
    if type not in VALID_KNOWLEDGE_TYPES:
        raise HTTPException(400, f"Invalid knowledge type. Choose from: {sorted(VALID_KNOWLEDGE_TYPES)}")
    return repo.list_knowledge_items(item_type=type)


@router.get("/knowledge/research-notes")
async def get_research_notes():
    _ensure_db()
    return repo.list_knowledge_items(item_type="research_note")


@router.get("/knowledge/dreaming-logs")
async def get_dreaming_logs():
    _ensure_db()
    return repo.list_knowledge_items(item_type="dreaming_log")


@router.get("/knowledge/strategy-memory")
async def get_strategy_memory():
    _ensure_db()
    return repo.list_knowledge_items(item_type="strategy_memory")


@router.get("/knowledge/guards")
async def get_guards():
    """List all active trading guards."""
    try:
        from aletheia.knowledge.guards import load_trading_guards
        guards = load_trading_guards(status="active")
        return [g.model_dump() for g in guards]
    except Exception as e:
        return {"error": str(e), "guards": []}


@router.get("/knowledge/guards/stats")
async def get_guard_stats():
    """Guard effectiveness statistics."""
    try:
        from aletheia.analytics.guard_effectiveness import compute_guard_stats
        return compute_guard_stats()
    except Exception as e:
        return {"error": str(e), "stats": []}


@router.get("/knowledge/case-studies")
async def get_case_studies():
    _ensure_db()
    return repo.list_knowledge_items(item_type="trade_case_study")


@router.get("/proposals/{proposal_id}/risk-decision")
async def get_proposal_risk_decision(proposal_id: str):
    """Get risk decision for a specific proposal."""
    _ensure_db()
    rows = repo._select_all("risk_decisions", where="proposal_id = ?", params=(proposal_id,), limit=1)
    if not rows:
        return {"proposal_id": proposal_id, "decision": None}
    return {"proposal_id": proposal_id, "decision": rows[0]}


@router.get("/dreaming/summary")
async def get_dreaming_summary():
    """Aggregated dreaming/learning summary for the Dreaming page (§23.5).

    Returns recent 24h activity across knowledge types + deprecated items.
    """
    _ensure_db()
    from datetime import datetime, timedelta, timezone
    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    recent_research = repo._select_all(
        "knowledge_items",
        where="item_type = ? AND created_at > ?",
        params=("research_note", cutoff_24h),
        limit=20,
    )
    recent_hypotheses = repo._select_all(
        "knowledge_items",
        where="item_type = ? AND created_at > ?",
        params=("strategy_memory", cutoff_24h),
        limit=20,
    )
    deprecated_memories = repo._select_all(
        "knowledge_items",
        where="(status = ? OR status = ?) AND updated_at > ?",
        params=("archived", "deprecated", cutoff_7d),
        limit=20,
    )
    recent_dreaming_logs = repo._select_all(
        "knowledge_items",
        where="item_type = ? AND created_at > ?",
        params=("dreaming_log", cutoff_24h),
        limit=10,
    )
    recent_daily_reviews = repo._select_all(
        "knowledge_items",
        where="item_type = ? AND created_at > ?",
        params=("daily_review", cutoff_24h),
        limit=10,
    )
    recent_runs = repo._select_all(
        "agent_runs",
        where="agent_name = ? AND started_at > ?",
        params=("dreaming_agent", cutoff_24h),
        limit=5,
    )
    return {
        "recent_research_notes": recent_research,
        "recent_strategy_hypotheses": recent_hypotheses,
        "deprecated_memories": deprecated_memories,
        "recent_dreaming_logs": recent_dreaming_logs,
        "recent_dreaming_runs": recent_runs,
        "recent_daily_reviews": recent_daily_reviews,
    }


@router.get("/reviews/summary")
async def get_reviews_summary():
    """Aggregated post-trade review summary for operator delegation.

    Returns recent closed positions, recent post-trade reviews, recent reviewer runs,
    and the strategy IDs that still lack fresh review coverage.
    """
    _ensure_db()
    from datetime import datetime, timedelta, timezone

    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cutoff_15m = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()

    recent_closed_positions = repo._select_all(
        "positions",
        where="status = ? AND closed_at IS NOT NULL AND closed_at > ?",
        params=("closed", cutoff_24h),
        order="closed_at DESC",
        limit=20,
    )
    recent_post_trade_reviews = repo._select_all(
        "knowledge_items",
        where="item_type = ? AND created_at > ?",
        params=("post_trade_review", cutoff_24h),
        limit=20,
    )
    recent_reviewer_runs = repo._select_all(
        "agent_runs",
        where="agent_name = ? AND started_at > ?",
        params=("post_trade_reviewer", cutoff_15m),
        limit=5,
    )

    reviewed_strategy_ids = {
        str(item.get("strategy_id"))
        for item in recent_post_trade_reviews
        if item.get("strategy_id")
    }
    missing_review_strategy_ids = sorted(
        {
            str(position.get("strategy_id"))
            for position in recent_closed_positions
            if position.get("strategy_id") and str(position.get("strategy_id")) not in reviewed_strategy_ids
        }
    )

    return {
        "recent_closed_positions": recent_closed_positions,
        "recent_post_trade_reviews": recent_post_trade_reviews,
        "recent_post_trade_reviewer_runs": recent_reviewer_runs,
        "missing_review_strategy_ids": missing_review_strategy_ids,
    }


@router.get("/knowledge/{item_id}/content")
async def get_knowledge_content(item_id: str):
    """Read the markdown content of a knowledge item by its file_path."""
    _ensure_db()
    from pathlib import Path
    items = repo._select_all("knowledge_items", where="id = ?", params=(item_id,), limit=1)
    if not items:
        raise HTTPException(404, "Knowledge item not found")
    file_path = items[0].get("file_path")
    if not file_path:
        return {"id": item_id, "content": "", "error": "no_file_path"}
    p = Path(file_path)
    if not p.exists():
        return {"id": item_id, "content": "", "error": "file_not_found"}
    try:
        content = p.read_text(encoding="utf-8")
    except Exception:
        return {"id": item_id, "content": "", "error": "read_failed"}
    return {"id": item_id, "content": content}


# ---------------------------------------------------------------------------
# Serenity Research Workbench endpoints
# ---------------------------------------------------------------------------

@router.get("/serenity/status")
async def get_serenity_status():
    """Workbench status: engine state, current/last runs, artifact counts."""
    _ensure_db()
    active = repo.get_active_serenity_research_run()
    all_runs = repo.list_serenity_research_runs(limit=10)
    themes = repo.list_serenity_themes(status="active")

    last_attempted = all_runs[0] if all_runs else None
    last_successful = next(
        (r for r in all_runs if r["status"] in ("completed", "completed_empty")), None
    )

    if active:
        engine_status = "running"
    elif last_attempted and last_attempted["status"] == "rate_limited":
        engine_status = "rate_limited"
    elif last_attempted and last_attempted["status"] == "failed":
        engine_status = "error"
    elif last_successful:
        engine_status = "healthy"
    else:
        engine_status = "idle"

    def _run_summary(r):
        if not r:
            return None
        return {
            "id": r["id"],
            "status": r["status"],
            "triggerType": r.get("trigger_type"),
            "mode": r.get("mode"),
            "startedAt": r.get("started_at"),
            "finishedAt": r.get("finished_at"),
            "errorType": r.get("error_type"),
            "errorMessage": r.get("error_message"),
            "artifactCounts": json.loads(r.get("artifact_counts") or "{}"),
        }

    return {
        "engineStatus": engine_status,
        "currentRun": _run_summary(active),
        "lastAttemptedRun": _run_summary(last_attempted),
        "lastSuccessfulRun": _run_summary(last_successful),
        "artifactCounts": {
            "activeThemes": len(themes),
        },
    }


@router.post("/serenity/runs", status_code=202)
async def create_serenity_run(body: dict):
    """Create a new research run. Returns 409 if one is already active."""
    _ensure_db()
    mode = body.get("mode", "standard")
    trigger_type = body.get("triggerType", "manual")
    brief = body.get("brief")
    save_to_profile = body.get("saveToProfile", False)
    idempotency_key = body.get("idempotencyKey")

    if idempotency_key:
        existing = repo.get_serenity_research_run_by_idempotency_key(idempotency_key)
        if existing:
            return {"runId": existing["id"], "status": existing["status"], "deduplicated": True}

    active = repo.get_active_serenity_research_run()
    if active:
        raise HTTPException(409, "A research run is already active")

    profile_snapshot_id = None
    if brief:
        parsed = _parse_brief_to_profile(brief)
        snap = SerenityProfileSnapshot(
            include_topics=parsed["include_topics"],
            exclude_topics=parsed["exclude_topics"],
            include_assets=parsed["include_assets"],
            exclude_assets=parsed["exclude_assets"],
            market_types=parsed["market_types"],
            time_horizon=parsed.get("time_horizon"),
            raw_brief=brief,
        )
        repo.insert_serenity_profile_snapshot(snap)
        profile_snapshot_id = snap.id

        if save_to_profile:
            existing_profile = repo.get_serenity_user_profile()
            if existing_profile:
                repo.update_serenity_user_profile_fields(
                    existing_profile["id"],
                    include_topics=parsed["include_topics"],
                    exclude_topics=parsed["exclude_topics"],
                    time_horizon=parsed.get("time_horizon"),
                    raw_brief=brief,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            else:
                profile = SerenityUserProfile(
                    include_topics=parsed["include_topics"],
                    exclude_topics=parsed["exclude_topics"],
                    time_horizon=parsed.get("time_horizon"),
                    raw_brief=brief,
                )
                repo.upsert_serenity_user_profile(profile)

    run = SerenityResearchRun(
        trigger_type=trigger_type,
        mode=mode,
        status="queued",
        idempotency_key=idempotency_key,
        profile_snapshot_id=profile_snapshot_id,
        brief_snapshot=brief,
    )
    repo.insert_serenity_research_run(run)

    from aletheia.agents.research_worker import enqueue_research_run
    enqueue_research_run(run.id)

    return {"runId": run.id, "status": "queued"}


@router.get("/serenity/runs")
async def list_serenity_runs(
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    _ensure_db()
    items = repo.list_serenity_research_runs(status=status, limit=limit)
    return {
        "items": [
            {
                "id": r["id"],
                "status": r["status"],
                "triggerType": r.get("trigger_type"),
                "mode": r.get("mode"),
                "startedAt": r.get("started_at"),
                "finishedAt": r.get("finished_at"),
                "errorType": r.get("error_type"),
                "artifactCounts": json.loads(r.get("artifact_counts") or "{}"),
                "createdAt": r.get("created_at"),
            }
            for r in items
        ]
    }


@router.get("/serenity/runs/{run_id}")
async def get_serenity_run_detail(run_id: str):
    _ensure_db()
    run = repo.get_serenity_research_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    events = repo.list_serenity_run_events(run_id)
    return {
        "run": {
            "id": run["id"],
            "status": run["status"],
            "triggerType": run.get("trigger_type"),
            "mode": run.get("mode"),
            "stage": run.get("stage"),
            "startedAt": run.get("started_at"),
            "finishedAt": run.get("finished_at"),
            "errorType": run.get("error_type"),
            "errorMessage": run.get("error_message"),
            "retryCount": run.get("retry_count", 0),
            "artifactCounts": json.loads(run.get("artifact_counts") or "{}"),
            "briefSnapshot": run.get("brief_snapshot"),
            "parentRunId": run.get("parent_run_id"),
        },
        "events": [
            {
                "id": e["id"],
                "eventType": e["event_type"],
                "stage": e.get("stage"),
                "message": e.get("message"),
                "createdAt": e.get("created_at"),
            }
            for e in events
        ],
    }


@router.post("/serenity/runs/{run_id}/retry")
async def retry_serenity_run(run_id: str):
    _ensure_db()
    run = repo.get_serenity_research_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["status"] not in ("failed", "rate_limited"):
        raise HTTPException(400, "Only failed or rate_limited runs can be retried")

    new_run = SerenityResearchRun(
        trigger_type=run.get("trigger_type", "manual"),
        mode=run.get("mode", "standard"),
        status="queued",
        parent_run_id=run_id,
        profile_snapshot_id=run.get("profile_snapshot_id"),
        brief_snapshot=run.get("brief_snapshot"),
        focus_theme_id=run.get("focus_theme_id"),
        retry_count=(run.get("retry_count") or 0) + 1,
    )
    repo.insert_serenity_research_run(new_run)

    from aletheia.agents.research_worker import enqueue_research_run
    enqueue_research_run(new_run.id)

    return {"runId": new_run.id, "status": "queued", "parentRunId": run_id}


@router.post("/serenity/runs/{run_id}/cancel")
async def cancel_serenity_run(run_id: str):
    _ensure_db()
    run = repo.get_serenity_research_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["status"] not in ("queued", "running"):
        raise HTTPException(400, "Only queued or running runs can be cancelled")

    from aletheia.storage.models import SerenityRunEvent
    now = datetime.now(timezone.utc).isoformat()
    repo.update_serenity_research_run(run_id, status="cancelled", finished_at=now, updated_at=now)
    repo.insert_serenity_run_event(SerenityRunEvent(
        run_id=run_id, event_type="run.cancelled", message="Cancelled by user"))
    return {"runId": run_id, "status": "cancelled"}


@router.get("/serenity/profile")
async def get_serenity_profile():
    _ensure_db()
    profile = repo.get_serenity_user_profile()
    if not profile:
        return {"profile": None}
    return {
        "profile": {
            "id": profile["id"],
            "includeTopics": json.loads(profile.get("include_topics") or "[]"),
            "excludeTopics": json.loads(profile.get("exclude_topics") or "[]"),
            "includeAssets": json.loads(profile.get("include_assets") or "[]"),
            "excludeAssets": json.loads(profile.get("exclude_assets") or "[]"),
            "marketTypes": json.loads(profile.get("market_types") or "[]"),
            "timeHorizon": profile.get("time_horizon"),
            "rawBrief": profile.get("raw_brief"),
        }
    }


@router.patch("/serenity/profile")
async def patch_serenity_profile(body: dict):
    _ensure_db()
    existing = repo.get_serenity_user_profile()
    now = datetime.now(timezone.utc).isoformat()

    if existing:
        updates = {"updated_at": now}
        if "includeTopics" in body:
            updates["include_topics"] = json.dumps(body["includeTopics"])
        if "excludeTopics" in body:
            updates["exclude_topics"] = json.dumps(body["excludeTopics"])
        if "includeAssets" in body:
            updates["include_assets"] = json.dumps(body["includeAssets"])
        if "excludeAssets" in body:
            updates["exclude_assets"] = json.dumps(body["excludeAssets"])
        if "marketTypes" in body:
            updates["market_types"] = json.dumps(body["marketTypes"])
        if "timeHorizon" in body:
            updates["time_horizon"] = body["timeHorizon"]
        if "rawBrief" in body:
            updates["raw_brief"] = body["rawBrief"]
        repo.update_serenity_user_profile_fields(existing["id"], **updates)
    else:
        profile = SerenityUserProfile(
            include_topics=json.dumps(body.get("includeTopics", [])),
            exclude_topics=json.dumps(body.get("excludeTopics", [])),
            include_assets=json.dumps(body.get("includeAssets", [])),
            exclude_assets=json.dumps(body.get("excludeAssets", [])),
            market_types=json.dumps(body.get("marketTypes", [])),
            time_horizon=body.get("timeHorizon"),
            raw_brief=body.get("rawBrief"),
        )
        repo.upsert_serenity_user_profile(profile)

    return {"ok": True}


@router.post("/serenity/profile/parse")
async def parse_serenity_brief(body: dict):
    _ensure_db()
    brief = body.get("brief", "")
    if len(brief) > 2000:
        raise HTTPException(400, "Brief too long (max 2000 chars)")
    parsed = _parse_brief_to_profile(brief)
    warnings = parsed.pop("_warnings", [])
    return {
        "parsed": {
            "includeTopics": json.loads(parsed["include_topics"]),
            "excludeTopics": json.loads(parsed["exclude_topics"]),
            "timeHorizon": parsed.get("time_horizon"),
        },
        "warnings": warnings,
    }


@router.patch("/serenity/themes/{theme_id}/status")
async def patch_serenity_theme_status(theme_id: str, body: dict):
    _ensure_db()
    new_status = body.get("status")
    valid = {"active", "watching", "dismissed", "promoted", "expired"}
    if new_status not in valid:
        raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(sorted(valid))}")
    theme = repo.get_serenity_theme(theme_id)
    if not theme:
        raise HTTPException(404, "Theme not found")
    repo.update_serenity_theme(theme_id, status=new_status,
                               updated_at=datetime.now(timezone.utc).isoformat())
    # Cascade dismiss/expire to child opportunities so thesis-invalidation exit fires
    if new_status in ("dismissed", "expired"):
        active_opps = repo.list_serenity_opportunities(theme_id=theme_id, status="active", limit=200)
        for opp in active_opps:
            repo.update_serenity_opportunity(opp["id"], status=new_status)
    return {"id": theme_id, "status": new_status}


@router.post("/serenity/themes/{theme_id}/feedback")
async def post_serenity_theme_feedback(theme_id: str, body: dict):
    _ensure_db()
    theme = repo.get_serenity_theme(theme_id)
    if not theme:
        raise HTTPException(404, "Theme not found")
    event = SerenityFeedbackEvent(
        target_type="theme",
        target_id=theme_id,
        action=body.get("action", "unspecified"),
        value=json.dumps(body.get("value", {})),
    )
    repo.insert_serenity_feedback_event(event)
    return {"id": event.id}


@router.post("/serenity/themes/{theme_id}/deep-dive")
async def post_serenity_theme_deep_dive(theme_id: str, body: dict):
    _ensure_db()
    theme = repo.get_serenity_theme(theme_id)
    if not theme:
        raise HTTPException(404, "Theme not found")

    active = repo.get_active_serenity_research_run()
    if active:
        raise HTTPException(409, "A research run is already active")

    brief = body.get("brief", f"Deep dive into theme: {theme['name']}")
    run = SerenityResearchRun(
        trigger_type="focused",
        mode="focused",
        status="queued",
        focus_theme_id=theme_id,
        brief_snapshot=brief,
    )
    repo.insert_serenity_research_run(run)

    from aletheia.agents.research_worker import enqueue_research_run
    enqueue_research_run(run.id)

    return {"runId": run.id, "status": "queued"}


@router.post("/serenity/themes/{theme_id}/promote")
async def promote_serenity_theme(theme_id: str):
    """Promote a Serenity theme's opportunities into the PI evaluation queue.

    Phase 3 (Option A, D5): Replaces the old StrategyRecord(state='draft') dead-record
    path. For each active opportunity under the theme:
      1. Set action_intent='pi_evaluate' on the opportunity
      2. Write a signal_pending PiDecision with signal_source='serenity' and
         opportunity_id=<serenity_opportunities.id>
      3. Wake the PI daemon so the new signals are evaluated promptly

    PI Agent remains the sole trade-decision maker — these rows are inputs, not orders.
    """
    _ensure_db()
    theme = repo.get_serenity_theme(theme_id)
    if not theme:
        raise HTTPException(404, "Theme not found")
    if theme["status"] == "promoted":
        raise HTTPException(400, "Theme already promoted")

    from aletheia.storage.models import PiDecision
    now = datetime.now(timezone.utc).isoformat()

    opps = repo.list_serenity_opportunities(theme_id=theme_id, status="active", limit=200)
    queued = 0
    queued_ids: list[str] = []
    for opp in opps:
        try:
            repo.update_serenity_opportunity(
                opp["id"],
                action_intent="pi_evaluate",
                updated_at=now,
            )
            pi_dec = PiDecision(
                symbol=opp.get("symbol"),
                direction=opp.get("direction"),
                action="signal_pending",
                conviction=0,
                reasoning=f"Serenity promote: theme={theme_id[:8]} opp={opp['id'][:8]}",
                opportunity_id=opp["id"],
                signal_source="serenity",
                debate_ran=False,
            )
            repo.insert_pi_decision(pi_dec)
            queued += 1
            queued_ids.append(opp["id"])
        except Exception:
            # one-row failure must not block the rest of the theme; log via audit
            continue

    repo.update_serenity_theme(theme_id, status="promoted", promoted_at=now, updated_at=now)

    try:
        from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
        notify_pi_daemon(source="serenity_promote")
    except Exception:
        pass

    return {
        "theme_id": theme_id,
        "queued": queued,
        "opportunity_ids": queued_ids,
        "status": "submitted_to_pi",
        "message": f"Theme promoted: {queued} opportunit{'y' if queued == 1 else 'ies'} enqueued for PI evaluation.",
    }


@router.get("/serenity/claims")
async def list_serenity_claims(
    run_id: str | None = Query(None),
    price_relevant: bool | None = Query(None),
    limit: int = Query(200, le=500),
):
    _ensure_db()
    items = repo.list_serenity_claims(run_id=run_id, price_relevant=price_relevant, limit=limit)
    return {"items": items}


@router.get("/serenity/themes/{theme_id}/claims")
async def get_serenity_theme_claims(theme_id: str, limit: int = Query(200, le=500)):
    _ensure_db()
    theme = repo.get_serenity_theme(theme_id)
    if not theme:
        raise HTTPException(404, "Theme not found")
    claims = repo.list_serenity_claims_for_theme(theme_id, limit=limit)
    return {"items": claims, "count": len(claims)}


@router.get("/serenity/themes/{theme_id}/detail")
async def get_serenity_theme_detail(theme_id: str):
    _ensure_db()
    theme = repo.get_serenity_theme(theme_id)
    if not theme:
        raise HTTPException(404, "Theme not found")
    claims = repo.list_serenity_claims_for_theme(theme_id, limit=100)
    evidence = repo.list_serenity_evidence(theme_id=theme_id, limit=100)
    feedback = repo.list_serenity_feedback_events("theme", theme_id, limit=50)
    opportunities = repo.list_serenity_opportunities(theme_id=theme_id, limit=50)
    return {
        "theme": theme,
        "claims": claims,
        "evidence": evidence,
        "feedback": feedback,
        "opportunities": opportunities,
    }


@router.get("/serenity/runs/{run_id}/artifacts")
async def get_serenity_run_artifacts(run_id: str):
    _ensure_db()
    run = repo.get_serenity_research_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    themes = repo.list_serenity_themes(status=None, limit=100)
    run_themes = [t for t in themes if t.get("run_id") == run_id]
    claims = repo.list_serenity_claims(run_id=run_id, limit=500)
    evidence = repo.list_serenity_evidence(limit=500)
    run_evidence = [e for e in evidence if e.get("run_id") == run_id]
    return {
        "themes": run_themes,
        "claims": claims,
        "evidence": run_evidence,
        "counts": {
            "themes": len(run_themes),
            "claims": len(claims),
            "evidence": len(run_evidence),
        },
    }


def _parse_brief_to_profile(brief: str) -> dict:
    """Simple keyword-based parser for research briefs. No LLM call in MVP."""
    include_topics: list[str] = []
    exclude_topics: list[str] = []
    warnings: list[str] = []

    danger_patterns = ["buy", "sell", "trade", "order", "execute", "ignore previous",
                       "bypass", "unlock", "disable risk"]
    brief_lower = brief.lower()
    for pattern in danger_patterns:
        if pattern in brief_lower:
            warnings.append(f"Instruction '{pattern}' ignored. Serenity Lens only supports research preferences.")

    lines = brief.replace("，", ",").replace("、", ",").split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if any(kw in line.lower() for kw in ["avoid", "exclude", "不要", "避开", "排除"]):
            parts = [p.strip() for p in line.split(",") if p.strip()]
            for part in parts:
                cleaned = part.strip()
                for prefix in ["avoid", "exclude", "不要", "避开", "排除"]:
                    cleaned = cleaned.replace(prefix, "").strip(" :：")
                if cleaned:
                    exclude_topics.append(cleaned)
        elif any(kw in line.lower() for kw in ["focus", "include", "关注", "优先", "prioritize"]):
            parts = [p.strip() for p in line.split(",") if p.strip()]
            for part in parts:
                cleaned = part.strip()
                for prefix in ["focus on", "focus", "include", "关注", "优先看", "优先", "prioritize"]:
                    cleaned = cleaned.replace(prefix, "").strip(" :：")
                if cleaned:
                    include_topics.append(cleaned)
        else:
            parts = [p.strip() for p in line.split(",") if p.strip()]
            include_topics.extend(parts)

    time_horizon = None
    for hint in ["1-7d", "1-7天", "intraday", "1-4w", "long-term"]:
        if hint in brief_lower:
            time_horizon = hint.replace("天", "d")
            break

    return {
        "include_topics": json.dumps(include_topics) if include_topics else "[]",
        "exclude_topics": json.dumps(exclude_topics) if exclude_topics else "[]",
        "include_assets": "[]",
        "exclude_assets": "[]",
        "market_types": "[]",
        "time_horizon": time_horizon,
        "_warnings": warnings,
    }


# ── Edge Lab: analytics endpoints ───────────────────────────────────────────

@router.get("/analytics/family-scorecard")
async def get_family_scorecard():
    """Per-scanner_family performance statistics (win_rate, expectancy, alpha)."""
    _ensure_db()
    from aletheia.analytics.family_scorecard import compute_family_scorecard
    return compute_family_scorecard()


@router.get("/analytics/shadow-trades")
async def get_shadow_trades(
    batch_id: str | None = None,
    scanner_family: str | None = None,
    resolved: bool | None = None,
    limit: int = 100,
):
    """List shadow trade records for counterfactual analysis."""
    _ensure_db()
    return repo.list_shadow_trades(
        batch_id=batch_id,
        scanner_family=scanner_family,
        resolved=resolved,
        limit=limit,
    )
