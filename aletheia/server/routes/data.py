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
)
from aletheia.trading.serenity_scoring import (
    make_replay_snapshot_payload,
    score_serenity_opportunity,
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
async def get_opportunities():
    _ensure_db()
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
        tier=payload.tier,
        supports=payload.supports,
        evidence_type=payload.evidence_type,
        event_at=payload.event_at or datetime.now(timezone.utc).isoformat(),
        actor=payload.actor,
        confidence=payload.confidence,
        tags=_dump_json(payload.tags),
    )
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
    opp = SerenityOpportunity(
        theme_id=payload.theme_id,
        symbol=payload.symbol,
        direction=payload.direction,
        thesis=payload.thesis,
        evidence_tier=payload.evidence_tier,
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


@router.get("/proposals")
async def get_proposals():
    _ensure_db()
    return repo.list_trade_proposals()


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


@router.get("/positions")
async def get_positions():
    _ensure_db()
    return repo.list_positions()


@router.get("/portfolio")
async def get_portfolio():
    _ensure_db()
    from aletheia.portfolio.pnl import compute_portfolio
    return compute_portfolio()


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


class PromoteRequest(BaseModel):
    target_state: str


@router.post("/strategies/{strategy_id}/promote")
async def promote_strategy_route(strategy_id: str, req: PromoteRequest):
    _ensure_db()
    from aletheia.trading.strategy_lifecycle import promote_strategy
    result = promote_strategy(strategy_id, req.target_state)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Promotion failed"))
    return result


@router.post("/strategies/{strategy_id}/retire")
async def retire_strategy_route(strategy_id: str):
    _ensure_db()
    from aletheia.trading.strategy_lifecycle import promote_strategy
    result = promote_strategy(strategy_id, "retired")
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Retire failed"))
    return result


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
async def get_portfolio_history(limit: int = 100):
    _ensure_db()
    return repo.list_portfolio_snapshots(limit=limit)


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
    from datetime import datetime, timedelta
    cutoff_24h = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    cutoff_7d = (datetime.utcnow() - timedelta(days=7)).isoformat()

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
    from datetime import datetime, timedelta

    cutoff_24h = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    cutoff_15m = (datetime.utcnow() - timedelta(minutes=15)).isoformat()

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


@router.get("/strategies/{strategy_id}/gates")
async def get_strategy_gates(strategy_id: str):
    """Check promotion gates for a strategy without actually promoting."""
    _ensure_db()
    s = repo.get_strategy(strategy_id)
    if s is None:
        raise HTTPException(404, "Strategy not found")
    from aletheia.trading.strategy_lifecycle import check_promotion_gates
    return check_promotion_gates(strategy_id)


# --- Serenity Research Workbench ---

VALID_RUN_STATUSES = {"queued", "running", "waiting_retry", "rate_limited",
                      "completed", "completed_empty", "partial", "failed", "cancelled"}
VALID_TRIGGER_TYPES = {"scheduled", "manual", "retry", "focused"}
VALID_RUN_MODES = {"standard", "quick_refresh", "focused"}
VALID_THEME_STATUSES = {"new", "active", "watching", "promoted", "dismissed", "expired"}


@router.get("/serenity/status")
async def get_serenity_status():
    """Dashboard status card: engine state, last runs, next scheduled, profile summary."""
    runs = repo.list_serenity_research_runs(limit=10)
    active_run = repo.get_active_serenity_research_run()
    profile = repo.get_serenity_user_profile()

    last_attempted = None
    last_successful = None
    for r in runs:
        if last_attempted is None:
            last_attempted = r
        if last_successful is None and r.get("status") in ("completed", "completed_empty"):
            last_successful = r
        if last_attempted and last_successful:
            break

    engine_status = "idle"
    if active_run:
        s = active_run.get("status", "")
        if s == "running":
            engine_status = "running"
        elif s == "queued":
            engine_status = "queued"
        elif s == "waiting_retry":
            engine_status = "retrying"
    elif last_attempted:
        s = last_attempted.get("status", "")
        if s == "rate_limited":
            engine_status = "rate_limited"
        elif s == "failed":
            engine_status = "failed"
        elif s in ("completed", "completed_empty"):
            engine_status = "healthy"

    active_themes = repo.list_serenity_themes(status="active", limit=100)
    watching_themes = repo.list_serenity_themes(status="watching", limit=100)

    profile_summary = None
    if profile:
        parts = []
        inc = _to_json(profile.get("include_topics"), [])
        exc = _to_json(profile.get("exclude_topics"), [])
        if inc:
            parts.append(f"Prioritize: {', '.join(inc[:5])}")
        if exc:
            parts.append(f"Avoid: {', '.join(exc[:5])}")
        profile_summary = "; ".join(parts) if parts else profile.get("raw_notes")

    return {
        "engineStatus": engine_status,
        "currentRun": _format_run_summary(active_run) if active_run else None,
        "lastAttemptedRun": _format_run_summary(last_attempted) if last_attempted else None,
        "lastSuccessfulRun": _format_run_summary(last_successful) if last_successful else None,
        "nextScheduledRunAt": None,
        "profileSummary": profile_summary,
        "artifactCounts": {
            "activeThemes": len(active_themes),
            "watchingThemes": len(watching_themes),
            "recentOpportunities": 0,
        },
    }


def _format_run_summary(run: dict | None) -> dict | None:
    if not run:
        return None
    return {
        "id": run["id"],
        "triggerType": run.get("trigger_type"),
        "mode": run.get("mode"),
        "status": run.get("status"),
        "stage": run.get("stage"),
        "startedAt": run.get("started_at"),
        "finishedAt": run.get("finished_at"),
        "errorType": run.get("error_type"),
        "errorMessage": run.get("error_message"),
        "artifactCounts": _to_json(run.get("artifact_counts"), {}),
        "createdAt": run.get("created_at"),
    }


class ResearchRunCreate(BaseModel):
    mode: str = "standard"
    triggerType: str = "manual"
    brief: str | None = None
    saveToProfile: bool = False
    focusThemeId: str | None = None
    idempotencyKey: str | None = None


@router.post("/serenity/runs", status_code=202)
async def create_serenity_run(payload: ResearchRunCreate):
    """Create a manual/focused research run. Returns immediately; run executes in background."""
    from aletheia.storage.models import SerenityResearchRun, SerenityProfileSnapshot, SerenityUserProfile

    if payload.mode not in VALID_RUN_MODES:
        raise HTTPException(400, f"Invalid mode: {payload.mode}")
    if payload.triggerType not in VALID_TRIGGER_TYPES:
        raise HTTPException(400, f"Invalid trigger type: {payload.triggerType}")

    if payload.idempotencyKey:
        existing = repo.get_serenity_research_run_by_idempotency_key(payload.idempotencyKey)
        if existing:
            return {"runId": existing["id"], "status": existing["status"], "deduplicated": True}

    active = repo.get_active_serenity_research_run()
    if active and payload.mode == "standard":
        raise HTTPException(409, detail={
            "error": "research_run_already_active",
            "activeRunId": active["id"],
            "message": "A standard research cycle is already running.",
        })

    profile_snapshot_id = None
    if payload.brief or payload.saveToProfile:
        profile = repo.get_serenity_user_profile()
        if payload.saveToProfile and payload.brief:
            parsed = _parse_brief_to_profile(payload.brief)
            if profile:
                repo.update_serenity_user_profile(profile["id"], **parsed,
                                                  updated_at=datetime.now(timezone.utc).isoformat())
            else:
                new_profile = SerenityUserProfile(**parsed)
                repo.insert_serenity_user_profile(new_profile)
                profile = new_profile.model_dump()

        snapshot_data = {}
        if profile:
            snapshot_data = {k: profile.get(k) for k in
                            ("include_topics", "exclude_topics", "include_assets",
                             "exclude_assets", "market_types", "time_horizon",
                             "risk_bias", "source_preferences")}
        snap = SerenityProfileSnapshot(
            profile_id=profile["id"] if profile else None,
            snapshot=json.dumps(snapshot_data),
            raw_brief=payload.brief,
            parser_version="v1",
        )
        repo.insert_serenity_profile_snapshot(snap)
        profile_snapshot_id = snap.id

    run = SerenityResearchRun(
        trigger_type=payload.triggerType,
        mode=payload.mode,
        status="queued",
        brief_snapshot=payload.brief,
        focus_theme_id=payload.focusThemeId,
        profile_snapshot_id=profile_snapshot_id,
        idempotency_key=payload.idempotencyKey,
    )
    repo.insert_serenity_research_run(run)

    from aletheia.storage.models import SerenityRunEvent
    repo.insert_serenity_run_event(SerenityRunEvent(
        run_id=run.id, event_type="run.queued", message="Run queued for execution"))

    from aletheia.agents.research_worker import enqueue_research_run
    enqueue_research_run(run.id)

    return {"runId": run.id, "status": "queued"}


@router.get("/serenity/runs")
async def list_serenity_runs(
    status: str | None = None,
    triggerType: str | None = None,
    limit: int = Query(default=20, le=100),
):
    runs = repo.list_serenity_research_runs(status=status, trigger_type=triggerType, limit=limit)
    return {"items": [_format_run_summary(r) for r in runs]}


@router.get("/serenity/runs/{run_id}")
async def get_serenity_run_detail(run_id: str):
    run = repo.get_serenity_research_run(run_id)
    if not run:
        raise HTTPException(404, "Research run not found")
    events = repo.list_serenity_run_events(run_id, limit=200)
    return {
        "run": _format_run_summary(run),
        "events": events,
    }


@router.post("/serenity/runs/{run_id}/retry")
async def retry_serenity_run(run_id: str):
    """Create a retry run with parent_run_id set."""
    from aletheia.storage.models import SerenityResearchRun, SerenityRunEvent

    parent = repo.get_serenity_research_run(run_id)
    if not parent:
        raise HTTPException(404, "Parent run not found")
    if parent["status"] not in ("failed", "rate_limited", "partial"):
        raise HTTPException(400, "Can only retry failed/rate_limited/partial runs")

    active = repo.get_active_serenity_research_run()
    if active:
        raise HTTPException(409, detail={
            "error": "research_run_already_active",
            "activeRunId": active["id"],
        })

    run = SerenityResearchRun(
        trigger_type="retry",
        mode=parent.get("mode", "standard"),
        status="queued",
        parent_run_id=run_id,
        brief_snapshot=parent.get("brief_snapshot"),
        focus_theme_id=parent.get("focus_theme_id"),
        profile_snapshot_id=parent.get("profile_snapshot_id"),
        retry_count=(parent.get("retry_count", 0) or 0) + 1,
    )
    repo.insert_serenity_research_run(run)
    repo.insert_serenity_run_event(SerenityRunEvent(
        run_id=run.id, event_type="run.queued", message="Retry run queued"))

    from aletheia.agents.research_worker import enqueue_research_run
    enqueue_research_run(run.id)

    return {"runId": run.id, "parentRunId": run_id, "status": "queued"}


@router.get("/serenity/profile")
async def get_serenity_profile():
    profile = repo.get_serenity_user_profile()
    if not profile:
        return {"profile": None}
    result = dict(profile)
    for field in ("include_topics", "exclude_topics", "include_assets",
                  "exclude_assets", "market_types", "source_preferences"):
        result[field] = _to_json(result.get(field), [])
    return {"profile": result}


class ProfileUpdate(BaseModel):
    includeTopics: list[str] | None = None
    excludeTopics: list[str] | None = None
    includeAssets: list[str] | None = None
    excludeAssets: list[str] | None = None
    marketTypes: list[str] | None = None
    timeHorizon: str | None = None
    riskBias: str | None = None
    sourcePreferences: list[str] | None = None
    rawNotes: str | None = None


@router.patch("/serenity/profile")
async def update_serenity_profile(payload: ProfileUpdate):
    from aletheia.storage.models import SerenityUserProfile

    profile = repo.get_serenity_user_profile()
    updates = {}
    if payload.includeTopics is not None:
        updates["include_topics"] = json.dumps(payload.includeTopics)
    if payload.excludeTopics is not None:
        updates["exclude_topics"] = json.dumps(payload.excludeTopics)
    if payload.includeAssets is not None:
        updates["include_assets"] = json.dumps(payload.includeAssets)
    if payload.excludeAssets is not None:
        updates["exclude_assets"] = json.dumps(payload.excludeAssets)
    if payload.marketTypes is not None:
        updates["market_types"] = json.dumps(payload.marketTypes)
    if payload.timeHorizon is not None:
        updates["time_horizon"] = payload.timeHorizon
    if payload.riskBias is not None:
        updates["risk_bias"] = payload.riskBias
    if payload.sourcePreferences is not None:
        updates["source_preferences"] = json.dumps(payload.sourcePreferences)
    if payload.rawNotes is not None:
        updates["raw_notes"] = payload.rawNotes

    if profile:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        repo.update_serenity_user_profile(profile["id"], **updates)
    else:
        new_profile = SerenityUserProfile(**updates)
        repo.insert_serenity_user_profile(new_profile)
        profile = new_profile.model_dump()

    return {"ok": True}


class BriefParse(BaseModel):
    brief: str


@router.post("/serenity/profile/parse")
async def parse_serenity_brief(payload: BriefParse):
    """Parse a natural language brief into structured profile without saving."""
    if len(payload.brief) > 4000:
        raise HTTPException(400, "Brief too long (max 4000 chars)")
    parsed = _parse_brief_to_profile(payload.brief)
    warnings = parsed.pop("_warnings", [])
    return {"parsed": {k: _to_json(v, v) if isinstance(v, str) else v
                       for k, v in parsed.items()}, "warnings": warnings}


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
        "risk_bias": "balanced",
        "source_preferences": "[]",
        "raw_notes": brief,
        "_warnings": warnings,
    }


@router.patch("/serenity/themes/{theme_id}/status")
async def update_serenity_theme_status(theme_id: str, payload: dict):
    """Update theme lifecycle status."""
    row = repo.get_serenity_theme(theme_id)
    if not row:
        raise HTTPException(404, "Theme not found")
    new_status = payload.get("status")
    if new_status not in VALID_THEME_STATUSES:
        raise HTTPException(400, f"Invalid status: {new_status}")
    updates = {"status": new_status, "updated_at": datetime.now(timezone.utc).isoformat()}
    if new_status == "dismissed":
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    elif new_status == "promoted":
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    repo.update_serenity_theme(theme_id, **updates)
    return {"id": theme_id, "status": new_status}


class ThemeFeedback(BaseModel):
    action: str
    value: dict | None = None


@router.post("/serenity/themes/{theme_id}/feedback")
async def create_serenity_theme_feedback(theme_id: str, payload: ThemeFeedback):
    """Record user feedback on a theme."""
    from aletheia.storage.models import SerenityFeedbackEvent
    row = repo.get_serenity_theme(theme_id)
    if not row:
        raise HTTPException(404, "Theme not found")
    event = SerenityFeedbackEvent(
        target_type="theme",
        target_id=theme_id,
        action=payload.action,
        value=json.dumps(payload.value or {}),
    )
    repo.insert_serenity_feedback_event(event)
    return {"id": event.id}


@router.post("/serenity/themes/{theme_id}/deep-dive")
async def create_serenity_theme_deep_dive(theme_id: str, payload: dict | None = None):
    """Create a focused research run for a specific theme."""
    from aletheia.storage.models import SerenityResearchRun, SerenityRunEvent

    row = repo.get_serenity_theme(theme_id)
    if not row:
        raise HTTPException(404, "Theme not found")

    active = repo.get_active_serenity_research_run()
    if active:
        raise HTTPException(409, detail={
            "error": "research_run_already_active",
            "activeRunId": active["id"],
        })

    brief = (payload or {}).get("brief")
    run = SerenityResearchRun(
        trigger_type="focused",
        mode="focused",
        status="queued",
        brief_snapshot=brief,
        focus_theme_id=theme_id,
    )
    repo.insert_serenity_research_run(run)
    repo.insert_serenity_run_event(SerenityRunEvent(
        run_id=run.id, event_type="run.queued", message=f"Focused deep-dive on theme {theme_id}"))

    from aletheia.agents.research_worker import enqueue_research_run
    enqueue_research_run(run.id)

    return {"runId": run.id, "status": "queued"}
