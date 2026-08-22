"""Data API routes for dashboard consumption."""

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

from aletheia.config.loader import is_preview_mode, load_config
from aletheia.portfolio.pnl import compute_portfolio
from aletheia.storage.sqlite import get_db
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
logger = logging.getLogger("aletheia.server.routes.data")

VALID_KNOWLEDGE_TYPES = {
    "calibration_report",
    "dreaming_log",
    "daily_review",
    "guard_candidate",
    "operator_assessment",
    "post_trade_review",
    "reasoning_feedback",
    "research_note",
    "strategy_memory",
    "system_action",
    "trade_case_study",
    "trading_guard",
    "trading_memo",
}

# --- In-process result caches (avoids re-computing expensive aggregations on every poll) ---
_portfolio_cache_lock = threading.Lock()
_portfolio_cache: "tuple[float, dict] | None" = None

_equity_history_cache_lock = threading.Lock()
_equity_history_cache: "dict[tuple, tuple[float, dict]]" = {}


def _ensure_db():
    # No-op: the backend verifies the durable migration ledger before yielding.
    # Kept as a function so existing route call sites stay focused on route behavior.
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


def _agent_run_output_events(run: dict) -> list[dict]:
    """Classify Paper Trader output strings without rewriting their raw record."""
    if run.get("agent_name") != "paper_trader":
        return []
    outputs = _to_json(run.get("outputs_created"), [])
    if not isinstance(outputs, list):
        return []

    classifications = {
        "skip": ("ranker_filter", None, True),
        "skip_recent_signal": ("signal_cooldown", "signal_cooldown", True),
        "reuse_active_carrier": ("carrier_reuse", "active_operator_carrier", True),
        "backpressure": ("queue_wait", "signal_queue_capacity", True),
        "signals_pending": ("signal_enqueue", "signal_enqueued", False),
        "skip_existing_position": ("existing_position", "open_position_duplicate", True),
        "skip_already_evaluated": (
            "already_evaluated",
            "operator_already_evaluated",
            False,
        ),
        "skip_stale_plan": ("stale_plan", "opportunity_not_open", False),
    }
    events: list[dict] = []
    for raw in outputs:
        if not isinstance(raw, str):
            continue
        parts = raw.split(":")
        prefix = parts[0]
        classification = classifications.get(prefix)
        if classification is None:
            continue
        event_class, reason_code, retryable = classification
        if prefix == "skip" and len(parts) >= 3:
            reason_code = parts[2]
        events.append(
            {
                "raw_output": raw,
                "event_class": event_class,
                "reason_code": reason_code,
                "retryable": retryable,
                "is_operator_decision": False,
            }
        )
    return events


def _with_agent_run_output_events(run: dict | None):
    if run is None:
        return None
    payload = dict(run)
    payload["output_events"] = _agent_run_output_events(payload)
    return payload


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
        timeline.append(
            {
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
            }
        )

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
def get_agent_runs(
    days: Annotated[int | None, Query(ge=1, le=90)] = None,
    limit: Annotated[int, Query(ge=1, le=5_000)] = 50,
):
    _ensure_db()
    if days is None and limit == 50:
        runs = repo.list_agent_runs()
    else:
        runs = repo.list_agent_runs(limit=limit, days=days)
    return [_with_agent_run_output_events(run) for run in runs]


@router.get("/agent-runs/{run_id}")
def get_agent_run(run_id: str):
    _ensure_db()
    return _with_agent_run_output_events(repo.get_agent_run(run_id))


@router.get("/agent-runs/{run_id}/tool-calls")
def get_agent_run_tool_calls(run_id: str):
    """Get structured tool call records for a specific agent run."""
    _ensure_db()
    return repo.list_tool_calls(run_id=run_id, limit=200)


@router.get("/agent-runs/{run_id}/steps")
def get_agent_run_steps(run_id: str):
    """Get the ordered reasoning/action steps (inputs/outputs) for an agent run.

    UNIONs `agent_run_steps` (non-tool reasoning steps) with `agent_tool_calls`
    (tool events) so the write-side no longer has to mirror every tool call into
    both tables. Rows are returned in execution order; tool-call rows carry a
    `source='tool_call'` marker so the UI can style them distinctly.
    """
    _ensure_db()
    db = get_db()
    rows: list[dict] = []
    steps = db.execute(
        "SELECT id, run_id, step_number, action, input_data, output_data, created_at "
        "FROM agent_run_steps WHERE run_id = ? ORDER BY step_number ASC LIMIT 500",
        (run_id,),
    ).fetchall()
    for raw in steps:
        row = dict(raw)
        row["source"] = "step"
        rows.append(row)
    tool_calls = db.execute(
        "SELECT id, run_id, tool_name, tool_class, permitted, input_data, "
        "output_data, created_at "
        "FROM agent_tool_calls WHERE run_id = ? ORDER BY created_at ASC LIMIT 500",
        (run_id,),
    ).fetchall()
    for raw in tool_calls:
        row = dict(raw)
        permitted = bool(row.pop("permitted", 1))
        tool_name = row.pop("tool_name", "") or ""
        row.pop("tool_class", None)
        row["action"] = f"tool:{tool_name}" + ("" if permitted else " (denied)")
        row["step_number"] = None
        row["source"] = "tool_call"
        rows.append(row)
    rows.sort(key=lambda item: (item.get("created_at") or "", item.get("step_number") or 0))
    return rows[:500]


# Explicit allowlist of agents that are safe to trigger manually from the Dashboard.
# side_effect_class: "observe" | "research" | "review" | "rank" | "behavior"
# cost_class: "low" | "medium" | "high"
# gate_check: True → block when kill_switch_active or runtime paused
_MANUAL_AGENT_ALLOWLIST: dict[str, dict] = {
    # Observational — read-only market data, no model calls, no artifacts
    "market_observer": {"side_effect_class": "observe", "cost_class": "low", "gate_check": False},
    "equity_observer": {"side_effect_class": "observe", "cost_class": "low", "gate_check": False},
    "stock_observer": {"side_effect_class": "observe", "cost_class": "low", "gate_check": False},
    "prediction_observer": {"side_effect_class": "observe", "cost_class": "low", "gate_check": False},
    "divergence_scanner": {"side_effect_class": "observe", "cost_class": "low", "gate_check": False},
    # Research / analysis — uses LLM, writes knowledge/insight artifacts only
    "research_agent": {"side_effect_class": "research", "cost_class": "high", "gate_check": False},
    # Review / critique — uses LLM, writes review/lesson/signal artifacts
    "post_trade_reviewer": {"side_effect_class": "review", "cost_class": "medium", "gate_check": False},
    "risk_critic": {"side_effect_class": "review", "cost_class": "medium", "gate_check": False},
    "lesson_reviewer": {"side_effect_class": "review", "cost_class": "medium", "gate_check": False},
    "dreaming_agent": {"side_effect_class": "review", "cost_class": "medium", "gate_check": False},
    "portfolio_agent": {"side_effect_class": "review", "cost_class": "medium", "gate_check": False},
    # Behavior-producing — creates paper proposals/orders; respects kill-switch and pause
    "paper_trader": {"side_effect_class": "behavior", "cost_class": "medium", "gate_check": True},
}

# Caller-specific capability guard for the Operator service identity (Issue #1100).
# Operator service callers are restricted to this subset of _MANUAL_AGENT_ALLOWLIST.
# New entries in _MANUAL_AGENT_ALLOWLIST do NOT automatically expand service authority —
# explicit addition here is required.
_OPERATOR_SERVICE_AGENT_ALLOWLIST: frozenset[str] = frozenset({
    "market_observer",
    "equity_observer",
    "stock_observer",
    "prediction_observer",
    "divergence_scanner",
    "research_agent",
    "post_trade_reviewer",
    "risk_critic",
    "lesson_reviewer",
    "dreaming_agent",
    "portfolio_agent",
})


@router.get("/agents/catalog")
def get_agents_catalog():
    """Read-only catalog of agents that may be triggered manually from the Dashboard."""
    return {
        "agents": [
            {"name": name, **meta}
            for name, meta in _MANUAL_AGENT_ALLOWLIST.items()
        ]
    }


class StartAgentRequest(BaseModel):
    agent_name: str = "market_observer"
    # Optional caller-supplied key for duplicate-click prevention.  Two requests
    # with the same idempotency_key and agent_name while the first is still
    # queued/running will return 409 with the in-flight run_id.
    idempotency_key: str | None = None


@router.post("/agent-runs/start", status_code=202)
def start_agent_run(req: StartAgentRequest, request: Request):
    _ensure_db()
    from fastapi import HTTPException
    from aletheia.agent_runtime.job_queue import (
        AgentJobAlreadyQueued,
        AgentJobManagerClosed,
        AgentJobQueueFull,
        get_agent_job_manager,
    )

    # Capability policy: only explicitly allowed agents may be triggered manually.
    if req.agent_name not in _MANUAL_AGENT_ALLOWLIST:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "agent_not_in_manual_allowlist",
                "agent_name": req.agent_name,
                "allowed": sorted(_MANUAL_AGENT_ALLOWLIST),
            },
        )

    # Caller-specific capability gate: the Operator service identity is restricted to
    # non-behavior-producing specialists only. This is checked after the manual allowlist
    # so the service caller must pass both guards.
    if getattr(request.state, "operator_service_authenticated", False):
        if req.agent_name not in _OPERATOR_SERVICE_AGENT_ALLOWLIST:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "agent_not_in_service_allowlist",
                    "agent_name": req.agent_name,
                    "allowed": sorted(_OPERATOR_SERVICE_AGENT_ALLOWLIST),
                },
            )

    meta = _MANUAL_AGENT_ALLOWLIST[req.agent_name]

    # Gate check for behavior-producing agents: honour kill-switch and pause state
    # so a manual trigger cannot bypass protections that scheduled runs respect.
    if meta["gate_check"]:
        cfg = load_config()
        if getattr(getattr(cfg, "runtime", None), "kill_switch_active", False):
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "kill_switch_active",
                    "message": "Kill switch is active; behavior-producing agents cannot be started manually.",
                },
            )
        if getattr(getattr(cfg, "runtime", None), "paused", False):
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "gateway_paused",
                    "message": "Gateway is paused; behavior-producing agents cannot be started manually.",
                },
            )

    # Actor attribution: service-delegated Operator calls are recorded as
    # "operator_service"; authenticated tunnel owner calls as "manual_owner";
    # local dashboard calls (no auth) as "manual".
    _is_operator_service = getattr(request.state, "operator_service_authenticated", False)
    _is_tunnel_auth = getattr(request.state, "tunnel_authenticated", False)
    if _is_operator_service:
        trigger_type = "operator_service"
    elif _is_tunnel_auth:
        trigger_type = "manual_owner"
    else:
        trigger_type = "manual"

    manager = get_agent_job_manager()
    try:
        run = manager.enqueue(
            req.agent_name,
            trigger_type=trigger_type,
            idempotency_key=req.idempotency_key,
        )
    except AgentJobAlreadyQueued as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "agent_already_queued",
                "agent_name": exc.agent_name,
                "run_id": exc.run_id,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AgentJobQueueFull as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "agent_job_queue_full",
                "job_id": exc.run_id,
                "capacity": exc.capacity,
            },
        ) from exc
    except AgentJobManagerClosed as exc:
        raise HTTPException(status_code=503, detail="agent_job_manager_unavailable") from exc
    return {
        "job_id": run.id,
        "run_id": run.id,
        "status": "queued",
        "trigger_type": run.trigger_type,
        "queue": manager.snapshot(),
    }


@router.get("/opportunities")
def get_opportunities(asset_class: str | None = None):
    _ensure_db()
    if asset_class:
        return repo.list_opportunities_by_asset_class(asset_class, limit=25)
    return repo.list_opportunities(limit=25)


@router.get("/market/opportunity-instruments")
def get_market_opportunity_instruments():
    _ensure_db()
    return repo.list_opportunity_instruments()


@router.get("/market/opportunities")
def get_market_opportunities(
    symbol: str,
    market_type: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
):
    _ensure_db()
    return repo.list_opportunities_for_instrument(symbol, market_type, limit)


@router.get("/config/default-exchange")
def get_default_exchange_config():
    """Preferred instrument exchange for observation-only UI ordering.

    Prefers the operator-selected `exchange.name` when it names a real venue;
    falls back to `market_data.ccxt_exchange` so the chart's OHLCV provider and
    the instrument search popup agree on ordering. Never surfaces credentials.
    """
    cfg = load_config()
    venue = getattr(cfg.exchange, "name", "") or ""
    if venue and venue.lower() not in {"paper_internal", "unknown", ""}:
        return {"exchange": venue.lower()}
    fallback = getattr(cfg.market_data, "ccxt_exchange", "") or "binance"
    return {"exchange": fallback.lower()}


class SerenityThemeCreate(BaseModel):
    name: str
    demand_wave: str | None = None
    objective: str | None = None
    description: str | None = None
    strategy_notes: str | None = None
    tags: list[str] = []
    status: str = "active"


@router.get("/serenity/themes")
def get_serenity_themes(status: str = Query(default="active")):
    _ensure_db()
    items = repo.list_serenity_themes(status=status)
    for item in items:
        item["tags"] = _to_json(item.get("tags"), [])
    return items


@router.post("/serenity/themes")
def create_serenity_theme(payload: SerenityThemeCreate):
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
def get_serenity_theme_overview(theme_id: str):
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
def get_serenity_theme(theme_id: str):
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
def get_serenity_evidence(theme_id: str | None = None, limit: int = 100):
    _ensure_db()
    items = repo.list_serenity_evidence(theme_id=theme_id, limit=limit)
    for item in items:
        item["tags"] = _to_json(item.get("tags"), [])
    return items


@router.post("/serenity/evidence")
def create_serenity_evidence(payload: SerenityEvidenceCreate):
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
def get_serenity_opportunities(
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
def create_serenity_opportunity(payload: SerenityOpportunityCreate):
    _ensure_db()
    # #100 fix: manual operator-created opportunities used to copy
    # `payload.evidence_tier` directly into `assessed_tier` and label the
    # assessment `operator`, bypassing the serenity_contract §5 rule that
    # scoring/promotion tier must be derived from independently assessed
    # supporting evidence. A manual submission with NO evidence could then
    # score as `disclosed` instead of the documented `speculative` floor.
    # Now the manual path calls the same `assess_opportunity_tier(...)` the
    # research pipeline uses (with the theme's existing evidence corpus). If
    # the operator's submitted `evidence_tier` is STRONGER than the
    # evidence-derived value, we KEEP the evidence-derived value — the human
    # is trusted to add evidence via the /serenity/evidence route, not to
    # skip the assessment step.
    from aletheia.trading.serenity_evidence import assess_opportunity_tier

    normalized_tier = normalize_tier(payload.evidence_tier)
    theme_evidence = repo.list_serenity_evidence(theme_id=payload.theme_id, limit=200)
    derived_tier, derived_by = assess_opportunity_tier({}, theme_evidence)
    opp = SerenityOpportunity(
        theme_id=payload.theme_id,
        symbol=payload.symbol,
        direction=payload.direction,
        thesis=payload.thesis,
        evidence_tier=normalized_tier,
        # The operator's `evidence_tier` submission stays advisory (mirrors
        # LLM-produced opportunities). Authority sits in the evidence-derived
        # `assessed_tier`; if that's `speculative` because there's no
        # supporting evidence, the manual submission is floored — the
        # operator must add evidence via /serenity/evidence to lift the tier.
        assessed_tier=derived_tier,
        # Preserve traceability: the assessment came from the pipeline (not
        # the operator's own claim), but the opportunity itself is
        # operator-authored — expose both in one field.
        assessed_by=f"pipeline+operator({derived_by})",
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
def get_serenity_opportunity(opportunity_id: str, include_scores: bool = True):
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
def get_serenity_opportunity_research_cycle(opportunity_id: str):
    _ensure_db()
    opp = _ensure_serenity_opportunity_exists(opportunity_id)
    return _build_research_cycle_payload(opp)


@router.get("/serenity/opportunities/{opportunity_id}/score")
def score_serenity_opportunity_endpoint(opportunity_id: str):
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
def get_serenity_opportunity_risk_gates(opportunity_id: str):
    _ensure_db()
    _ensure_serenity_opportunity_exists(opportunity_id)
    return repo.list_serenity_risk_gates(opportunity_id=opportunity_id, limit=200)


@router.post("/serenity/opportunities/{opportunity_id}/risk-gates")
def create_serenity_opportunity_risk_gate(opportunity_id: str, payload: SerenityRiskGateCreate):
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
def get_serenity_replay(opportunity_id: str, limit: int = 50):
    _ensure_db()
    _ensure_serenity_opportunity_exists(opportunity_id)
    return repo.list_serenity_replay_snapshots(opportunity_id=opportunity_id, limit=limit)


@router.post("/serenity/opportunities/{opportunity_id}/replay")
def create_serenity_replay_snapshot(opportunity_id: str, payload: SerenityReplaySnapshotCreate):
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
def get_serenity_post_mortems(opportunity_id: str):
    _ensure_db()
    _ensure_serenity_opportunity_exists(opportunity_id)
    return repo.list_serenity_post_mortems(opportunity_id=opportunity_id, limit=200)


@router.post("/serenity/opportunities/{opportunity_id}/post-mortems")
def create_serenity_post_mortem(opportunity_id: str, payload: SerenityPostMortemCreate):
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
def get_serenity_pipeline():
    """Serenity research pipeline summary: recent runs + active opportunity count (D7)."""
    _ensure_db()
    from aletheia.storage.sqlite import get_db as _get_db

    runs = repo.list_serenity_research_runs(limit=20)
    db = _get_db()
    active_count = db.execute("SELECT COUNT(*) FROM serenity_opportunities WHERE status = 'active'").fetchone()[0]
    return {"runs": runs, "active_opportunity_count": active_count}


_PROPOSAL_SKIP = frozenset({"attribution_snapshot"})


@router.get("/proposals")
def get_proposals():
    _ensure_db()
    rows = repo.list_trade_proposals(limit=25)
    return [{k: v for k, v in row.items() if k not in _PROPOSAL_SKIP} for row in rows]


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: str):
    _ensure_db()
    row = repo.get_trade_proposal(proposal_id)
    if not row:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="proposal not found")
    return row


@router.post("/proposals/{proposal_id}/approve-paper")
def approve_proposal(proposal_id: str, request: Request):
    """Approve a stored local-paper proposal through the execution gateway."""
    if is_preview_mode():
        raise HTTPException(
            403,
            detail={
                "reason_code": "preview_execution_blocked",
                "message": "Proposal approval is unavailable in the isolated preview API.",
            },
        )
    _ensure_db()
    from aletheia.files.jsonl import write_audit

    p = repo.get_trade_proposal(proposal_id)
    if p is None:
        raise HTTPException(404, "Proposal not found")

    execution_mode = str(p.get("execution_mode") or "")
    if execution_mode != "paper_internal":
        raise HTTPException(
            409,
            "Only a proposal stored for local paper execution can be approved here; "
            "the stored execution mode was not changed.",
        )

    status = str(p.get("status") or "").lower()
    if status == "executed":
        return {"proposal_id": proposal_id, "execution": {"status": "already_executed", "proposal_id": proposal_id}}
    if status in {"error", "rejected"}:
        return {"proposal_id": proposal_id, "execution": {"status": status, "proposal_id": proposal_id}}
    if status not in {"pending", "approved", "executing"}:
        raise HTTPException(
            409,
            "This proposal is no longer awaiting paper approval; no order was sent.",
        )

    _source = "owner" if getattr(request.state, "tunnel_authenticated", False) else "dashboard"
    if status == "pending":
        repo.update_trade_proposal(proposal_id, status="approved")
        try:
            write_audit(
                {
                    "actor": "human",
                    "source": _source,
                    "event_type": "proposal_approved",
                    "entity_type": "trade_proposal",
                    "entity_id": proposal_id,
                    "before": {"status": status},
                    "after": {"status": "approved"},
                    "message": "Proposal approved for paper execution",
                }
            )
        except Exception as exc:
            # The SQLite approval is authoritative. A process can also die in
            # this gap, so approved/executing states above deliberately re-enter
            # Gateway on the next idempotent request.
            logger.warning(
                "Proposal %s approval audit unavailable; execution continues (%s)",
                proposal_id,
                type(exc).__name__,
            )
    from aletheia.execution.gateway import execute_existing_proposal

    try:
        result = execute_existing_proposal(proposal_id, execution_mode="paper_internal")
    except Exception as exc:
        # Configuration/exchange exceptions can contain credentials or signed
        # request details. Keep only the class in process logs and use a stable,
        # secret-free reason in the API response and durable audit trail.
        logger.warning(
            "Paper approval execution failed for proposal %s (%s)",
            proposal_id,
            type(exc).__name__,
        )
        repo.update_trade_proposal(proposal_id, status="error")
        try:
            write_audit(
                {
                    "actor": "system",
                    "source": _source,
                    "event_type": "execution_error",
                    "entity_type": "trade_proposal",
                    "entity_id": proposal_id,
                    "before": {"status": "approved"},
                    "after": {"status": "error"},
                    "message": "paper_execution_failed: Paper execution could not be completed.",
                }
            )
        except Exception as audit_exc:
            logger.warning(
                "Proposal %s execution error kept after audit failure (%s)",
                proposal_id,
                type(audit_exc).__name__,
            )
        return {
            "proposal_id": proposal_id,
            "execution": {
                "status": "error",
                "reason_code": "paper_execution_failed",
                "message": "Paper execution could not be completed.",
            },
        }
    return {"proposal_id": proposal_id, "execution": result}


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: str, request: Request):
    _ensure_db()
    from aletheia.files.jsonl import write_audit

    # Re-read under the writer claim so an execution worker cannot acquire its
    # lease or insert an Order between this check and the status transition. This
    # route declines only intent that has not crossed the execution boundary; it
    # never pretends to cancel an accepted venue Order.
    with repo.transaction(immediate=True):
        p = repo.get_trade_proposal(proposal_id)
        if p is None:
            raise HTTPException(404, "Proposal not found")
        current = p.get("status") if isinstance(p, dict) else getattr(p, "status", None)
        rejectable = {"pending", "approved"}
        has_order = repo.has_order_for_proposal(proposal_id)
        has_execution_claim = repo.get_proposal_execution_claim(proposal_id) is not None
        if current not in rejectable or has_order or has_execution_claim:
            raise HTTPException(
                409,
                (
                    f"Proposal is '{current}' or execution has already begun; "
                    "it cannot be rejected here. Reconcile or close the real order instead."
                ),
            )
        repo.update_trade_proposal(proposal_id, status="rejected")
        # An authoritative Risk approval may already have reserved portfolio
        # capacity before Gateway acquires its execution lease.  A human reject
        # is a terminal no-Order state, so release that allowance in the same
        # transaction; otherwise one declined Proposal can consume a slot
        # forever even though no Order exists.
        repo.release_entry_risk_reservation(proposal_id)
    _source = "owner" if getattr(request.state, "tunnel_authenticated", False) else "dashboard"
    try:
        write_audit(
            {
                "actor": "human",
                "source": _source,
                "event_type": "proposal_rejected",
                "entity_type": "trade_proposal",
                "entity_id": proposal_id,
                "message": "Proposal rejected by human before execution began",
            }
        )
    except Exception as exc:
        # SQLite is authoritative after commit. A secondary JSONL failure must not
        # turn a completed rejection into a retryable-looking API error.
        logger.warning(
            "Proposal %s kept rejected after audit sink failure (%s)",
            proposal_id,
            type(exc).__name__,
        )
    return {"proposal_id": proposal_id, "status": "rejected"}


@router.get("/trades")
def get_trades():
    _ensure_db()
    return repo.list_orders()


@router.get("/trades/export")
def export_trades(fmt: str = Query(default="csv", pattern="^csv$"), limit: int = Query(default=5000, le=50000)):
    """Export closed positions as CSV for tax/PnL reporting.

    #65 fix: previously exported OPEN orders — no realized PnL, no fills, no fees,
    no closed_at. That contradicted the docstring's tax/PnL claim; a user
    downloading this file would need to reconstruct realized PnL themselves.
    Now emits one row per CLOSED position with entry/exit prices, quantities,
    fees, realized PnL, close reason, and BTC benchmark — the fields a tax
    report actually needs. Still CSV, still under the same URL, only the
    columns change.
    """
    from fastapi.responses import StreamingResponse
    import csv
    import io

    _ensure_db()
    from aletheia.portfolio.pnl import position_realized_pnl_view

    rows = [position_realized_pnl_view(row) for row in repo.list_positions(status="closed", limit=limit)]
    if not rows:
        return StreamingResponse(
            iter(["no data\n"]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=trades_export.csv"},
        )

    columns = [
        "opened_at",
        "closed_at",
        "symbol",
        "market_type",
        "direction",
        "quantity",
        "entry_price",
        "current_price",
        "realized_pnl",
        "realized_pnl_complete",
        "realized_pnl_unavailable_reason",
        "entry_fee",
        "exit_fee",
        "slippage_cost",
        "execution_mode",
        "close_reason",
        "btc_entry_price",
        "btc_exit_price",
        "trade_school",
        "position_id",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        # `current_price` on a closed position is the exit price (pnl.close_position
        # writes exit_price into current_price at close). Keep the column labelled
        # `current_price` so we don't invent a schema, but the semantic is exit.
        writer.writerow(
            [
                row.get("opened_at", ""),
                row.get("closed_at", ""),
                row.get("symbol", ""),
                row.get("market_type", ""),
                row.get("direction", ""),
                row.get("quantity", ""),
                row.get("entry_price", ""),
                row.get("current_price", ""),
                row.get("realized_pnl", ""),
                row.get("realized_pnl_complete", ""),
                row.get("realized_pnl_unavailable_reason", ""),
                row.get("entry_fee", ""),
                row.get("exit_fee", ""),
                row.get("slippage_cost", ""),
                row.get("execution_mode", ""),
                row.get("close_reason", ""),
                row.get("btc_entry_price", ""),
                row.get("btc_exit_price", ""),
                row.get("trade_school", ""),
                row.get("id", ""),
            ]
        )

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trades_export.csv"},
    )


@router.get("/positions")
def get_positions():
    _ensure_db()
    from aletheia.portfolio.pnl import position_realized_pnl_view

    return [position_realized_pnl_view(row) for row in repo.list_positions()]


@router.get("/portfolio")
def get_portfolio():
    _ensure_db()
    global _portfolio_cache
    now = time.monotonic()
    with _portfolio_cache_lock:
        if _portfolio_cache is not None:
            ts, cached = _portfolio_cache
            if now - ts < 30.0:
                return cached
    result = compute_portfolio(execution_mode=None)
    with _portfolio_cache_lock:
        _portfolio_cache = (now, result)
    return result


@router.get("/exchange/account")
def get_exchange_account():
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
def get_positions_reconciliation():
    """Classify every account position as managed / external / orphaned.

    Read-only. Joins the cached exchange position view against locally-tracked open
    positions so the UI/operator can see which positions Aletheia did NOT open
    (external — adoption candidates) and which it tracks but the exchange no longer
    has (orphaned — disappearance-evidence candidates). Never hits Bitget synchronously.
    """
    _ensure_db()
    from aletheia.portfolio.position_reconciler import classify_positions

    return classify_positions().to_dict()


@router.post("/positions/reconcile")
def run_positions_reconcile():
    """Manual trigger: classify, record disappearance evidence, and demo auto-adopt.

    Missing positions stay open pending verified-exit/accounting phases. Live mode
    records scoped evidence but takes no adoption/close action.
    """
    _ensure_db()
    from aletheia.portfolio.position_reconciler import run_reconcile

    return run_reconcile()


class ManualCloseRequest(BaseModel):
    """Payload for a dashboard-initiated manual close of a single position."""

    order_type: str = "market"  # "market" or "limit"
    limit_price: float | None = None  # required when order_type=="limit"
    reason: str | None = None  # optional human-readable note


@router.post("/positions/{position_id}/close")
async def manual_close_position(position_id: str, req: ManualCloseRequest):
    """Owner-initiated manual close of one open position.

    Routing and constraints (execution_contract.md §10.2):
    - `paper_internal` → resolved-mark local close via paper_broker.
    - `*_demo` / `*_testnet` → real exchange close order via the matching broker.
    - `*_live_limited` → HTTP 409 with code ``live_position_requires_exchange_close``;
      live positions hold REAL funds and must be closed from the exchange UI.
    - `market` closes immediately; `limit` rests exit_limit_price + exit_limit_source.
    - Authentication: guarded by ``_PROTECTED_METHOD_PREFIXES`` POST /api/positions/
      (app.py) — requires X-Aletheia-Token when Remote Control or tunnel is active.
    - Audit: writes `position_closed` audit event (via pnl.close_position) with
      actor='human', source='dashboard_manual_close'.
    """
    _ensure_db()

    order_type = (req.order_type or "market").strip().lower()
    if order_type not in ("market", "limit"):
        raise HTTPException(400, {"code": "invalid_order_type", "message": "order_type must be 'market' or 'limit'"})

    import asyncio
    from aletheia.storage.repositories import get_position
    from aletheia.portfolio.position_manager import proactive_exit

    pos = get_position(position_id)
    if pos is None or pos.get("status") != "open":
        raise HTTPException(404, {"code": "position_not_found_or_not_open", "position_id": position_id})

    exec_mode = pos.get("execution_mode", "")
    if exec_mode.endswith("_live_limited"):
        raise HTTPException(
            409,
            {
                "code": "live_position_requires_exchange_close",
                "position_id": position_id,
                "execution_mode": exec_mode,
                "message": (
                    "Live positions hold real funds and must be closed from the exchange "
                    "UI directly. This endpoint is restricted to paper/demo positions."
                ),
            },
        )

    def _do_close() -> dict:
        result = proactive_exit(
            position_id,
            order_type=order_type,
            limit_price=req.limit_price,
            reason=req.reason,
            actor="human",
        )
        if result.get("error"):
            error_code = result["error"]
            if error_code == "limit_price_required":
                raise HTTPException(
                    400, {"code": error_code, "message": "limit_price must be a positive finite number"}
                )
            raise HTTPException(409, result)
        return result

    return await asyncio.to_thread(_do_close)


@router.get("/audit/events")
def get_audit_events(limit: int = 100):
    _ensure_db()
    limit = max(1, min(limit, 500))
    return repo.list_audit_events(limit=limit)


@router.get("/strategies")
def get_strategies():
    _ensure_db()
    return repo.list_strategies()


def _truthy_strategy_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _strategy_operator_summary(strategies: list[dict]) -> dict:
    under_review = [item for item in strategies if _truthy_strategy_flag(item.get("under_review"))]
    paused_paper = [item for item in strategies if _truthy_strategy_flag(item.get("paper_trading_paused"))]
    active_paper = [
        item for item in strategies if item.get("state") in {"paper_candidate", "paper_running", "paper_passed"}
    ]
    live_candidate = [item for item in strategies if item.get("state") in {"live_candidate", "live_limited_candidate"}]

    flagged_ids = {str(item.get("id")) for item in [*under_review, *paused_paper] if item.get("id")}
    promotion_ids = {
        str(item.get("id")) for item in strategies if item.get("id") and item.get("state") == "paper_passed"
    }
    live_limited_ids = {str(item.get("id")) for item in live_candidate if item.get("id")}

    return {
        "active_paper_strategies": active_paper,
        "under_review_strategies": under_review,
        "paused_paper_strategies": paused_paper,
        "live_candidate_strategies": live_candidate,
        "recent_strategy_evaluations": [],
        "needs_operator_evaluation_strategy_ids": sorted(flagged_ids),
        "recent_strategy_promotion_proposals": [],
        "needs_operator_promotion_proposal_strategy_ids": sorted(promotion_ids),
        "recent_live_limited_enablement_proposals": [],
        "needs_operator_live_limited_enablement_strategy_ids": sorted(live_limited_ids),
    }


@router.get("/strategies/summary")
def get_strategy_summary():
    """Operator-facing strategy summary used by the local operator shell."""
    _ensure_db()
    return _strategy_operator_summary(repo.list_strategies(limit=500))


@router.get("/market/snapshots")
def get_market_snapshots(
    symbol: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    exchange: str = "",
):
    """Return market snapshots.

    Optional filters:
    - symbol: canonical BTC/USDT format
    - exchange: source exchange name (e.g. "binance", "bybit")

    ``limit`` is clamped to 1..1000; values outside this range return 422.
    """
    _ensure_db()
    return repo.list_market_snapshots(symbol=symbol, limit=limit, exchange=exchange)


@router.get("/market/ohlcv")
async def get_market_ohlcv(
    response: Response,
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
    `X-Aletheia-Data-Source` reports memory_cache, provider_fetch, database_fallback,
    or unavailable without changing the historical array response shape.
    """
    import asyncio
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
        response.headers["X-Aletheia-Data-Source"] = "unavailable"
        return {"error": f"invalid_instrument: {effective_exchange}:{sym}:{market_type}", "candles": []}

    # Historical pagination window: ask the exchange for `limit` candles ending
    # just before `before`. since = before - limit*tf yields a window whose newest
    # candle is the one immediately preceding `before`.
    since_ms: int | None = None
    if before > 0:
        tf_ms = timeframe_ms(timeframe)
        if tf_ms == 0:
            response.headers["X-Aletheia-Data-Source"] = "unavailable"
            return {"error": f"unsupported_timeframe: {timeframe}", "candles": []}
        since_ms = before * 1000 - limit * tf_ms

    cache = get_cache()
    cache_key = sym
    use_cache = since_ms is None and limit == 100
    data_source = "memory_cache"
    try:
        candles = (
            cache.get(
                cache_key,
                timeframe,
                limit,
                market_type=market_type,
                exchange=effective_exchange,
            )
            if use_cache
            else None
        )
        if candles is None:
            data_source = "provider_fetch"
            provider = get_market_data_provider(effective_exchange)
            try:
                try:
                    candles = await provider.get_ohlcv(
                        instrument,
                        timeframe=timeframe,
                        limit=limit,
                        since=since_ms,
                    )
                except Exception:
                    # The chart remains usable during a provider outage, but exact
                    # venue/market/time paging is preserved and provenance is explicit.
                    db_rows = await asyncio.to_thread(
                        repo.get_ohlcv_candles,
                        sym,
                        timeframe,
                        limit=limit,
                        exchange=effective_exchange,
                        market_type=market_type,
                        before_ts=before * 1000 if before > 0 else None,
                    )
                    if not db_rows:
                        raise
                    candles = db_rows
                    data_source = "database_fallback"
            finally:
                await provider.close()

        def _value(candle, key: str):
            return candle.get(key) if isinstance(candle, dict) else getattr(candle, key)

        # For a historical page, drop any candle at/after `before` so the prepend
        # on the frontend never overlaps the existing left-most bar.
        if before > 0:
            cutoff_ms = before * 1000
            candles = [c for c in candles if _value(c, "ts" if isinstance(c, dict) else "timestamp") < cutoff_ms]
        # lightweight-charts wants ascending time in seconds
        rows = [
            {
                "time": int(_value(c, "ts" if isinstance(c, dict) else "timestamp") / 1000),
                "open": _value(c, "open"),
                "high": _value(c, "high"),
                "low": _value(c, "low"),
                "close": _value(c, "close"),
                "volume": _value(c, "volume"),
            }
            for c in sorted(
                candles,
                key=lambda candle: _value(
                    candle,
                    "ts" if isinstance(candle, dict) else "timestamp",
                ),
            )
        ]
        observed_exchanges = {
            (c.get("exchange") if isinstance(c, dict) else getattr(c, "exchange", None))
            for c in candles
        }
        observed_exchanges.discard(None)
        actual_exchange = (
            next(iter(observed_exchanges))
            if len(observed_exchanges) == 1
            else effective_exchange
        )
        response.headers["X-Aletheia-Data-Source"] = data_source
        response.headers["X-Aletheia-Data-Instrument"] = f"{actual_exchange}:{sym}:{market_type}:{timeframe}"
        return rows
    except Exception as e:
        logging.getLogger("aletheia.routes.data").warning("OHLCV fetch failed: %s", e)
        response.headers["X-Aletheia-Data-Source"] = "unavailable"
        return {"error": "market_data_unavailable", "candles": []}


@router.get("/market/portfolio-history")
def get_portfolio_history(limit: int | None = None, days: int | None = None):
    _ensure_db()
    return repo.list_portfolio_snapshots(limit=limit, days=days)


@router.get("/market/account-equity-history")
def get_account_equity_history(
    limit: Annotated[int | None, Query(ge=1, le=5000)] = None,
    days: Annotated[int | None, Query(ge=1, le=3650)] = None,
    max_points: Annotated[int | None, Query(ge=32, le=1000)] = None,
):
    """One stable account-equity curve for the Overview.

    The endpoint selects the current account's explicit equity snapshot field.
    It deliberately excludes old local `total_value_usdt` rows because those
    recorded position notional rather than cash-account equity.
    """
    _ensure_db()
    local_paper = load_config().exchange.paper_execution_mode == "paper_internal"
    execution_mode = "paper_internal" if local_paper else "exchange_account"
    cache_key = (days, limit, max_points, execution_mode)
    now = time.monotonic()
    with _equity_history_cache_lock:
        entry = _equity_history_cache.get(cache_key)
        if entry is not None:
            ts, cached = entry
            if now - ts < 60.0:
                return cached
    rows = repo.list_account_equity_snapshots(
        execution_mode=execution_mode,
        limit=limit,
        days=days,
    )
    points = [{"time": row["snapshot_at"], "equity_usdt": row["account_equity_usdt"]} for row in rows]
    capital_flow_markers: list[dict] = []
    adjusted_points: list[dict] = []
    if points:
        if local_paper:
            account_events = repo.list_paper_cash_events_between(
                points[0]["time"], points[-1]["time"]
            )
            capital_events = [
                {
                    "id": event["id"],
                    "detected_at": event["created_at"],
                    "broker_id": "paper_internal",
                    "event_type": (
                        "deposit" if float(event["amount_usdt"]) > 0 else "withdraw"
                    ),
                    "amount_usdt": abs(float(event["amount_usdt"])),
                    "signed_amount_usdt": float(event["amount_usdt"]),
                    "change_type": event["action"],
                    "balance_before_usdt": float(event["cash_balance_before_usdt"]),
                    "balance_after_usdt": float(event["cash_balance_after_usdt"]),
                    "note": event["note"],
                }
                for event in account_events
            ]
        else:
            capital_events = repo.list_capital_events_between(
                points[0]["time"], points[-1]["time"]
            )
        event_index = 0
        cumulative_net_flow = 0.0
        for point in points:
            while (
                event_index < len(capital_events) and str(capital_events[event_index]["detected_at"]) <= point["time"]
            ):
                event = capital_events[event_index]
                signed_amount = float(
                    event.get("signed_amount_usdt") or event["amount_usdt"]
                )
                if (
                    "signed_amount_usdt" not in event
                    and event["event_type"] == "withdraw"
                ):
                    signed_amount = -signed_amount
                cumulative_net_flow += signed_amount
                capital_flow_markers.append(
                    {
                        "id": event["id"],
                        "time": event["detected_at"],
                        "broker_id": event["broker_id"],
                        "event_type": event["event_type"],
                        "amount_usdt": float(event["amount_usdt"]),
                        "signed_amount_usdt": round(signed_amount, 4),
                        "change_type": event.get("change_type", event["event_type"]),
                        "balance_before_usdt": event.get("balance_before_usdt"),
                        "balance_after_usdt": event.get("balance_after_usdt"),
                        "note": event.get("note", ""),
                    }
                )
                event_index += 1
            adjusted_points.append(
                {
                    "time": point["time"],
                    "raw_equity_usdt": point["equity_usdt"],
                    "cumulative_net_flow_usdt": round(cumulative_net_flow, 4),
                    "adjusted_equity_usdt": round(
                        float(point["equity_usdt"]) - cumulative_net_flow,
                        4,
                    ),
                }
            )
    if max_points and len(points) > max_points:
        # Stable first/last plus evenly spaced interior samples keep the chart
        # bounded without selecting a favorable window or changing values.
        indexes = {0, len(points) - 1}
        step = (len(points) - 1) / (max_points - 1)
        indexes.update(round(index * step) for index in range(max_points))
        selected = sorted(indexes)
        points = [points[index] for index in selected]
        adjusted_points = [adjusted_points[index] for index in selected]

    result = {
        "source": "local_paper_ledger" if local_paper else "exchange_account_snapshot",
        "state": "available" if points else "unavailable",
        "points": points,
        "adjusted_points": adjusted_points,
        "capital_flow_markers": capital_flow_markers,
        "adjustment": {
            "state": "available" if adjusted_points else "unavailable",
            "method": "visible_window_net_flow_adjusted_usdt",
            "scope": "aggregate_tracked_account_equity",
            "baseline_at": points[0]["time"] if points else None,
            "note": (
                "Raw equity minus audited cash or administrative balance changes "
                "after the first visible snapshot. Verified trading PnL is unchanged."
            ),
        },
        "as_of": points[-1]["time"] if points else None,
    }
    with _equity_history_cache_lock:
        _equity_history_cache[cache_key] = (now, result)
    return result


def invalidate_account_equity_history_cache() -> None:
    """Invalidate adjusted/raw equity views after an authoritative cash-flow write."""
    with _equity_history_cache_lock:
        _equity_history_cache.clear()


@router.get("/market/macro")
def get_macro_snapshot():
    """Latest macro snapshot: VIX, DXY, SPY, BTC dominance, risk regime."""
    _ensure_db()
    snap = repo.latest_macro_snapshot()
    if not snap:
        return {"available": False, "data": None}
    return {"available": True, "data": snap}


@router.get("/market/collector-health")
def get_market_collector_health():
    """Bounded per-domain collector state; sync route keeps SQLite off the event loop."""
    _ensure_db()
    from aletheia.data.collector_health import get_collector_health_report

    return get_collector_health_report()


@router.get("/market/data-coverage")
def get_market_data_coverage(response: Response):
    """Cached table coverage and freshness; separate from collector execution health."""
    _ensure_db()
    from aletheia.data.data_coverage import get_data_coverage_report

    timings: dict[str, float] = {}
    report = get_data_coverage_report(timings=timings)
    response.headers["Server-Timing"] = ", ".join(
        f"{name};dur={duration_ms:.1f}" for name, duration_ms in timings.items()
    )
    return report


@router.get("/knowledge")
def get_knowledge(
    type: str = Query(default="research_note"),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    q: str = Query(default="", max_length=200),
    status: str = Query(default="active"),
):
    _ensure_db()
    if type == "all":
        db = get_db()
        if status not in {"active", "archived", "deprecated", "all"}:
            raise HTTPException(400, "Invalid knowledge status")
        clauses: list[str] = []
        params: list[object] = []
        if status == "active":
            clauses.append("(ki.status = 'active' OR ki.status IS NULL)")
        elif status != "all":
            clauses.append("ki.status = ?")
            params.append(status)
        search = q.strip()
        if search:
            clauses.append("(ki.title LIKE ? OR ki.tags LIKE ? OR ki.item_type LIKE ?)")
            pattern = f"%{search}%"
            params.extend((pattern, pattern, pattern))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total = int(db.execute(
            f"SELECT COUNT(*) FROM knowledge_items ki {where}", params,
        ).fetchone()[0])
        rows = db.execute(
            "SELECT ki.id, ki.item_type, ki.title, ki.confidence, ki.status, "
            "ki.strategy_id, ki.tags, ki.created_at, ki.updated_at, "
            "(SELECT ara.run_id FROM agent_run_artifacts ara "
            " WHERE ara.artifact_id = ki.id AND ara.artifact_type = ki.item_type "
            " ORDER BY ara.created_at DESC LIMIT 1) AS producer_run_id "
            f"FROM knowledge_items ki {where} ORDER BY ki.rowid DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        items = [dict(row) for row in rows]
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "returned": len(items),
            "has_more": offset + len(items) < total,
        }
    if type not in VALID_KNOWLEDGE_TYPES:
        raise HTTPException(400, f"Invalid knowledge type. Choose from: {sorted(VALID_KNOWLEDGE_TYPES)}")
    rows = get_db().execute(
        "SELECT ki.id, ki.item_type, ki.title, ki.confidence, ki.status, "
        "ki.strategy_id, ki.tags, ki.created_at, ki.updated_at, "
        "(SELECT ara.run_id FROM agent_run_artifacts ara "
        " WHERE ara.artifact_id = ki.id AND ara.artifact_type = ki.item_type "
        " ORDER BY ara.created_at DESC LIMIT 1) AS producer_run_id "
        "FROM knowledge_items ki WHERE ki.item_type = ? ORDER BY ki.rowid DESC LIMIT ?",
        (type, limit),
    ).fetchall()
    return [dict(row) for row in rows]


@router.get("/knowledge/research-notes")
def get_research_notes():
    return get_knowledge(type="research_note", limit=50)


@router.get("/knowledge/dreaming-logs")
def get_dreaming_logs():
    return get_knowledge(type="dreaming_log", limit=50)


@router.get("/knowledge/strategy-memory")
def get_strategy_memory():
    return get_knowledge(type="strategy_memory", limit=50)


def _guard_experiments_by_id() -> dict[str, dict]:
    """Return the latest experiment metadata per learned Guard without mutating state."""
    from aletheia.learning.registry import get_learning_path

    if not get_learning_path("ranker_learned_guard").measurement_supported:
        return {}
    try:
        rows = (
            get_db()
            .execute(
                "SELECT e.experiment_id, e.stage, e.status, e.created_at, h.scope_json "
                "FROM learning_experiments e JOIN learning_hypotheses h "
                "ON h.hypothesis_id=e.hypothesis_id "
                "WHERE h.path_id='ranker_learned_guard' AND e.ended_at IS NULL "
                "ORDER BY datetime(e.created_at) DESC, e.experiment_id DESC"
            )
            .fetchall()
        )
    except Exception:
        logger.debug("Guard experiment metadata is unavailable", exc_info=True)
        return {}

    result: dict[str, dict] = {}
    for raw in rows:
        row = dict(raw)
        try:
            guard_id = str(json.loads(row.pop("scope_json") or "{}").get("guard_id") or "")
        except (TypeError, ValueError, AttributeError):
            continue
        if guard_id and guard_id not in result:
            result[guard_id] = row
    return result


def _guard_response(
    guard,
    experiment: dict | None = None,
    *,
    evaluation_enabled: bool = True,
) -> dict:
    """Project storage state separately from authority and effective baseline behavior."""
    from aletheia.knowledge.guards import guard_origin
    from aletheia.learning.registry import get_learning_path

    row = guard.model_dump()
    origin = guard_origin(guard)
    learned_measurement_supported = get_learning_path("ranker_learned_guard").measurement_supported
    if origin == "learned" and not learned_measurement_supported:
        experiment = None
    proposed_block = guard.proposed_block_candidate
    if proposed_block is None:
        proposed_block = guard.pre_shadow_block_candidate
    if proposed_block is None:
        proposed_block = guard.block_candidate
    proposed_penalty = guard.proposed_score_penalty
    if proposed_penalty is None:
        proposed_penalty = guard.pre_shadow_score_penalty
    if proposed_penalty is None:
        proposed_penalty = guard.score_penalty
    proposed_penalty_value = float(proposed_penalty or 0.0)
    if origin == "learned":
        proposed_penalty_value = max(0.0, proposed_penalty_value)

    active = guard.status == "active"
    guard_authority = active and not guard.shadow_only and origin != "learned"
    effect_enabled = guard_authority and evaluation_enabled
    actual_effect = {
        "block_candidate": bool(guard.block_candidate) if effect_enabled else False,
        "score_penalty": float(guard.score_penalty or 0.0) if effect_enabled else 0.0,
    }
    if active:
        lifecycle_status = (
            "active_enforced"
            if actual_effect["block_candidate"] or actual_effect["score_penalty"] != 0
            else "active_observe"
        )
    else:
        lifecycle_status = guard.status

    activation_effect = {
        "block_candidate": bool(proposed_block),
        "score_penalty": proposed_penalty_value,
    }
    if guard.status == "candidate":
        if origin == "learned" or guard.shadow_only:
            activation_effect = {"block_candidate": False, "score_penalty": 0.0}
        else:
            # Manual promotion currently fills these exact defaults when omitted.
            activation_effect = {
                "block_candidate": True,
                "score_penalty": float(guard.score_penalty or 0.0) or 10.0,
            }
    else:
        activation_effect = actual_effect

    row.update(
        {
            "lifecycle_status": lifecycle_status,
            "origin": origin,
            "proposed_effect": {
                "block_candidate": bool(proposed_block),
                "score_penalty": proposed_penalty_value,
            },
            "activation_effect": activation_effect,
            "experiment_id": experiment.get("experiment_id") if experiment else None,
            "experiment_stage": experiment.get("stage") if experiment else None,
            "experiment_status": experiment.get("status") if experiment else None,
            # Assignment is unavailable now; a future supported path would resolve it per candidate.
            "assignment": None,
            "authority_granted": guard_authority,
            "authority_scope": ("exact_candidate_treatment" if learned_measurement_supported else "unsupported")
            if origin == "learned"
            else ("guard" if guard_authority else "none"),
            "evaluation_enabled": evaluation_enabled,
            "actual_effect": actual_effect,
        }
    )
    return row


@router.get("/knowledge/guards")
def get_guards():
    """List active guards with lifecycle and authority projected separately."""
    try:
        from aletheia.knowledge.guards import load_trading_guards

        guards = load_trading_guards(status="active")
        experiments = _guard_experiments_by_id()
        cfg = load_config(persist_migrations=False)
        evaluation_enabled = bool(cfg.guard.enabled and cfg.guard.enforce_in_paper_ranker)
        return [
            _guard_response(
                g,
                experiments.get(g.guard_id),
                evaluation_enabled=evaluation_enabled,
            )
            for g in guards
        ]
    except Exception as e:
        raise HTTPException(503, "guard_read_failed") from e


@router.get("/knowledge/guard-candidates")
def get_guard_candidates():
    """List guard candidates with proposed effects and no implied authority."""
    try:
        from aletheia.knowledge.guards import load_trading_guards

        guards = load_trading_guards(status="candidate")
        experiments = _guard_experiments_by_id()
        return [_guard_response(g, experiments.get(g.guard_id), evaluation_enabled=False) for g in guards]
    except Exception as e:
        raise HTTPException(503, "guard_read_failed") from e


@router.post("/knowledge/guard-candidates/{guard_id}/promote")
def promote_guard_candidate(guard_id: str):
    """Manually promote a candidate guard to an active trading guard."""
    from aletheia.analytics.guard_promotion import promote_guard_candidate_to_active
    from aletheia.knowledge.guards import load_trading_guards

    if not any(g.guard_id == guard_id for g in load_trading_guards(status="candidate")):
        raise HTTPException(404, "Guard candidate not found")
    ok = promote_guard_candidate_to_active(
        guard_id,
        actor_type="operator",
        actor_id="web",
        reason="manual_review",
    )
    if not ok:
        raise HTTPException(404, "Guard candidate not found")
    promoted = [g for g in load_trading_guards(status="active") if g.guard_id == guard_id]
    if not promoted:
        raise HTTPException(500, "Guard promotion did not create an active guard")
    experiments = _guard_experiments_by_id()
    cfg = load_config(persist_migrations=False)
    return _guard_response(
        promoted[0],
        experiments.get(guard_id),
        evaluation_enabled=bool(cfg.guard.enabled and cfg.guard.enforce_in_paper_ranker),
    )


@router.get("/knowledge/guards/stats")
def get_guard_stats():
    """Guard effectiveness statistics."""
    try:
        from aletheia.analytics.guard_effectiveness import compute_guard_stats

        return compute_guard_stats()
    except Exception as e:
        raise HTTPException(503, "guard_stats_read_failed") from e


@router.get("/knowledge/case-studies")
def get_case_studies():
    _ensure_db()
    return repo.list_knowledge_items(item_type="trade_case_study")


@router.get("/proposals/{proposal_id}/risk-decision")
def get_proposal_risk_decision(proposal_id: str):
    """Get risk decision for a specific proposal."""
    _ensure_db()
    rows = repo._select_all("risk_decisions", where="proposal_id = ?", params=(proposal_id,), limit=1)
    if not rows:
        return {"proposal_id": proposal_id, "decision": None}
    return {"proposal_id": proposal_id, "decision": rows[0]}


@router.get("/dreaming/summary")
def get_dreaming_summary():
    """Aggregated dreaming/learning summary for the Dreaming page (§23.5).

    Returns recent 24h activity across knowledge types + deprecated items.
    """
    _ensure_db()
    from datetime import datetime, timezone

    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    # #267: include only active items in recent_* collections so archived/
    # deprecated items do not also appear as fresh knowledge production.
    _active = "(status = 'active' OR status IS NULL)"
    recent_research = repo._select_all(
        "knowledge_items",
        where=f"item_type = ? AND created_at > ? AND {_active}",
        params=("research_note", cutoff_24h),
        limit=20,
    )
    recent_hypotheses = repo._select_all(
        "knowledge_items",
        where=f"item_type = ? AND created_at > ? AND {_active}",
        params=("strategy_memory", cutoff_24h),
        limit=20,
    )
    deprecated_memories = repo._select_all(
        "knowledge_items",
        where="(status = ? OR status = ?) AND updated_at > ?",
        params=("archived", "deprecated", cutoff_7d),
        limit=20,
    )
    deprecated_memories_total = int(get_db().execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE (status = ? OR status = ?) AND updated_at > ?",
        ("archived", "deprecated", cutoff_7d),
    ).fetchone()[0])
    recent_dreaming_logs = repo._select_all(
        "knowledge_items",
        where=f"item_type = ? AND created_at > ? AND {_active}",
        params=("dreaming_log", cutoff_24h),
        limit=10,
    )
    recent_daily_reviews = repo._select_all(
        "knowledge_items",
        where=f"item_type = ? AND created_at > ? AND {_active}",
        params=("daily_review", cutoff_24h),
        limit=10,
    )
    for items in (recent_research, recent_hypotheses, deprecated_memories, recent_dreaming_logs, recent_daily_reviews):
        for item in items:
            row = get_db().execute(
                "SELECT run_id FROM agent_run_artifacts WHERE artifact_id = ? AND artifact_type = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (item["id"], item["item_type"]),
            ).fetchone()
            item["producer_run_id"] = row["run_id"] if row else None
    recent_knowledge_items = [dict(row) for row in get_db().execute(
        "SELECT ki.id, ki.item_type, ki.title, ki.confidence, ki.status, "
        "ki.strategy_id, ki.tags, ki.created_at, ki.updated_at, "
        "(SELECT ara.run_id FROM agent_run_artifacts ara "
        " WHERE ara.artifact_id = ki.id AND ara.artifact_type = ki.item_type "
        " ORDER BY ara.created_at DESC LIMIT 1) AS producer_run_id "
        "FROM knowledge_items ki "
        f"WHERE ki.created_at > ? AND {_active} ORDER BY ki.rowid DESC LIMIT 100",
        (cutoff_24h,),
    ).fetchall()]
    recent_knowledge_total = int(get_db().execute(
        f"SELECT COUNT(*) FROM knowledge_items WHERE created_at > ? AND {_active}",
        (cutoff_24h,),
    ).fetchone()[0])
    recent_runs = repo._select_all(
        "agent_runs",
        where="agent_name = ? AND started_at > ?",
        params=("dreaming_agent", cutoff_24h),
        limit=5,
    )
    for run in recent_runs:
        run["artifacts"] = [dict(row) for row in get_db().execute(
            "SELECT artifact_type, artifact_id, created_at FROM agent_run_artifacts "
            "WHERE run_id = ? ORDER BY created_at",
            (run["id"],),
        ).fetchall()]
    try:
        from aletheia.learning.control import get_learning_job_health, job_health_view

        dreaming_job = job_health_view(get_learning_job_health("dreaming_daily"))
        dreaming_job.pop("last_error", None)
        dreaming_job.pop("last_failure_reason", None)
    except Exception:
        dreaming_job = None
        dreaming_job_availability = "unavailable"
    else:
        dreaming_job_availability = "available"
    return {
        "recent_research_notes": recent_research,
        "recent_strategy_hypotheses": recent_hypotheses,
        "deprecated_memories": deprecated_memories,
        "deprecated_memories_total": deprecated_memories_total,
        "deprecated_memories_truncated": len(deprecated_memories) < deprecated_memories_total,
        "recent_dreaming_logs": recent_dreaming_logs,
        "recent_dreaming_runs": recent_runs,
        "recent_daily_reviews": recent_daily_reviews,
        "recent_knowledge_items": recent_knowledge_items,
        "recent_knowledge_total": recent_knowledge_total,
        "recent_knowledge_returned": len(recent_knowledge_items),
        "recent_knowledge_truncated": len(recent_knowledge_items) < recent_knowledge_total,
        "dreaming_job": dreaming_job,
        "dreaming_job_availability": dreaming_job_availability,
    }


@router.get("/reviews/summary")
def get_reviews_summary():
    """Aggregated post-trade review summary for operator delegation.

    Returns recent closed positions, recent active post-trade reviews,
    recent reviewer runs, and the strategy IDs whose Positions are not yet
    covered by any active review (#269).
    """
    _ensure_db()
    import json
    from datetime import datetime, timezone

    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cutoff_15m = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()

    recent_closed_positions = repo._select_all(
        "positions",
        where="status = ? AND closed_at IS NOT NULL AND closed_at > ?",
        params=("closed", cutoff_24h),
        order="closed_at DESC",
        limit=20,
    )
    from aletheia.portfolio.pnl import position_realized_pnl_view

    recent_closed_positions = [position_realized_pnl_view(row) for row in recent_closed_positions]

    # Exclude archived/deprecated reviews: an owner who archives an invalid
    # review must be able to schedule a replacement (#269).
    recent_post_trade_reviews = repo._select_all(
        "knowledge_items",
        where="item_type = ? AND status NOT IN ('archived', 'deprecated') AND created_at > ?",
        params=("post_trade_review", cutoff_24h),
        limit=20,
    )
    recent_reviewer_runs = repo._select_all(
        "agent_runs",
        where="agent_name = ? AND started_at > ?",
        params=("post_trade_reviewer", cutoff_15m),
        limit=5,
    )

    # Position-level coverage: a strategy is covered only if its specific
    # Positions appear in an active review's position:{id} tags (#269).
    reviewed_position_ids: set[str] = set()
    for item in recent_post_trade_reviews:
        try:
            tags = json.loads(item.get("tags") or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(tags, list):
            continue
        reviewed_position_ids.update(str(tag).split(":", 1)[1] for tag in tags if str(tag).startswith("position:"))
    missing_review_strategy_ids = sorted(
        {
            str(position.get("strategy_id"))
            for position in recent_closed_positions
            if position.get("strategy_id") and str(position.get("id")) not in reviewed_position_ids
        }
    )

    return {
        "recent_closed_positions": recent_closed_positions,
        "recent_post_trade_reviews": recent_post_trade_reviews,
        "recent_post_trade_reviewer_runs": recent_reviewer_runs,
        "missing_review_strategy_ids": missing_review_strategy_ids,
    }


@router.get("/knowledge/{item_id}/content")
def get_knowledge_content(item_id: str):
    """Read file-backed Markdown, falling back to persisted inline content."""
    _ensure_db()
    from pathlib import Path

    items = repo._select_all("knowledge_items", where="id = ?", params=(item_id,), limit=1)
    if not items:
        raise HTTPException(404, "Knowledge item not found")
    item = items[0]
    inline_content = item.get("content") or ""
    file_path = item.get("file_path")
    if not file_path:
        return (
            {"id": item_id, "content": inline_content}
            if inline_content
            else {"id": item_id, "content": "", "error": "no_file_path"}
        )
    p = Path(file_path)
    if not p.exists():
        return (
            {"id": item_id, "content": inline_content}
            if inline_content
            else {"id": item_id, "content": "", "error": "file_not_found"}
        )
    try:
        content = p.read_text(encoding="utf-8")
    except Exception:
        return (
            {"id": item_id, "content": inline_content}
            if inline_content
            else {"id": item_id, "content": "", "error": "read_failed"}
        )
    return {"id": item_id, "content": content}


# ---------------------------------------------------------------------------
# Serenity Research Workbench endpoints
# ---------------------------------------------------------------------------


@router.get("/serenity/status")
def get_serenity_status():
    """Workbench status: engine state, current/last runs, artifact counts."""
    _ensure_db()
    active = repo.get_active_serenity_research_run()
    all_runs = repo.list_serenity_research_runs(limit=10)
    themes = repo.list_serenity_themes(status="active")

    last_attempted = all_runs[0] if all_runs else None
    last_successful = next((r for r in all_runs if r["status"] in ("completed", "completed_empty")), None)

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

    # If the pipeline has been disabled in settings, override the derived status
    # (which would otherwise be "error" from the last failed run) to "disabled".
    # An actively-running job is shown as "running" regardless — it needs to finish.
    try:
        from aletheia.config.loader import load_config as _load_cfg

        if not getattr(_load_cfg().serenity, "enabled", True) and engine_status != "running":
            engine_status = "disabled"
    except Exception:
        pass

    def _run_summary(r):
        if not r:
            return None
        return {
            "id": r["id"],
            "status": r["status"],
            "stage": r.get("stage"),
            "triggerType": r.get("trigger_type"),
            "mode": r.get("mode"),
            "startedAt": r.get("started_at"),
            "finishedAt": r.get("finished_at"),
            "lastProgressAt": r.get("updated_at"),
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
def create_serenity_run(body: dict):
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
def list_serenity_runs(
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
                "stage": r.get("stage"),
                "triggerType": r.get("trigger_type"),
                "mode": r.get("mode"),
                "startedAt": r.get("started_at"),
                "finishedAt": r.get("finished_at"),
                "lastProgressAt": r.get("updated_at"),
                "errorType": r.get("error_type"),
                "artifactCounts": json.loads(r.get("artifact_counts") or "{}"),
                "createdAt": r.get("created_at"),
            }
            for r in items
        ]
    }


@router.get("/serenity/runs/{run_id}")
def get_serenity_run_detail(run_id: str):
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
            "lastProgressAt": run.get("updated_at"),
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
def retry_serenity_run(run_id: str):
    _ensure_db()
    run = repo.get_serenity_research_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["status"] not in ("failed", "rate_limited"):
        raise HTTPException(400, "Only failed or rate_limited runs can be retried")

    retry_count = (run.get("retry_count") or 0) + 1
    new_run = SerenityResearchRun(
        trigger_type=run.get("trigger_type", "manual"),
        mode=run.get("mode", "standard"),
        status="queued",
        parent_run_id=run_id,
        profile_snapshot_id=run.get("profile_snapshot_id"),
        brief_snapshot=run.get("brief_snapshot"),
        focus_theme_id=run.get("focus_theme_id"),
        retry_count=retry_count,
        idempotency_key=f"serenity-retry:{run_id}:{retry_count}",
    )
    stored, deduplicated = repo.insert_serenity_research_retry(new_run)

    if not deduplicated:
        from aletheia.agents.research_worker import enqueue_research_run

        enqueue_research_run(stored["id"])

    return {
        "runId": stored["id"],
        "status": stored["status"],
        "parentRunId": run_id,
        "deduplicated": deduplicated,
    }


@router.post("/serenity/runs/{run_id}/cancel")
def cancel_serenity_run(run_id: str):
    _ensure_db()
    run = repo.get_serenity_research_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["status"] in ("cancel_requested", "cancelled"):
        return {"runId": run_id, "status": run["status"], "deduplicated": True}
    if run["status"] not in ("queued", "running"):
        raise HTTPException(400, "Only queued or running runs can be cancelled")

    now = datetime.now(timezone.utc).isoformat()
    status, changed = repo.request_serenity_research_run_cancel(run_id, now=now)
    if status not in ("cancel_requested", "cancelled"):
        raise HTTPException(409, "Run finished before cancellation could be requested")
    if changed:
        transitioned_run = repo.get_serenity_research_run(run_id) or run
        repo.insert_serenity_run_event(
            SerenityRunEvent(
                run_id=run_id,
                event_type=f"run.{status}",
                stage=transitioned_run.get("stage"),
                message=(
                    "Cancelled before worker start"
                    if status == "cancelled"
                    else "Cancellation requested by user"
                ),
            )
        )
    return {"runId": run_id, "status": status, "deduplicated": not changed}


@router.get("/serenity/profile")
def get_serenity_profile():
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
def patch_serenity_profile(body: dict):
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
def parse_serenity_brief(body: dict):
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
def patch_serenity_theme_status(theme_id: str, body: dict):
    _ensure_db()
    new_status = body.get("status")
    valid = {"active", "watching", "dismissed", "promoted", "expired"}
    if new_status not in valid:
        raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(sorted(valid))}")
    theme = repo.get_serenity_theme(theme_id)
    if not theme:
        raise HTTPException(404, "Theme not found")
    repo.update_serenity_theme(theme_id, status=new_status, updated_at=datetime.now(timezone.utc).isoformat())
    # Cascade dismiss/expire to child opportunities so thesis-invalidation exit fires
    # Fix #1518: Remove LIMIT — must update ALL active opportunities, not just newest 200
    if new_status in ("dismissed", "expired"):
        # Loop until all active opportunities are invalidated
        page_size = 200
        while True:
            active_opps = repo.list_serenity_opportunities(theme_id=theme_id, status="active", limit=page_size)
            if not active_opps:
                break
            for opp in active_opps:
                repo.update_serenity_opportunity(opp["id"], status=new_status)
            # If we got fewer than page_size, we're done
            if len(active_opps) < page_size:
                break
    return {"id": theme_id, "status": new_status}


@router.post("/serenity/themes/{theme_id}/feedback")
def post_serenity_theme_feedback(theme_id: str, body: dict):
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
def post_serenity_theme_deep_dive(theme_id: str, body: dict):
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
def promote_serenity_theme(theme_id: str):
    """Promote a Serenity theme's opportunities into the PI evaluation queue.

    Phase 3 (Option A, D5): Replaces the old StrategyRecord(state='draft') dead-record
    path. For each active opportunity under the theme:
      1. Set action_intent='pi_evaluate' on the opportunity
      2. Write a signal_pending PiDecision with signal_source='serenity' and
         opportunity_id=<serenity_opportunities.id>
      3. Wake the PI daemon so the new signals are evaluated promptly

    System Operator remains the sole trade-decision maker — these rows are inputs, not orders.
    """
    _ensure_db()
    theme = repo.get_serenity_theme(theme_id)
    if not theme:
        raise HTTPException(404, "Theme not found")
    if theme["status"] == "promoted":
        raise HTTPException(400, "Theme already promoted")

    # Research generation currently caps this far below 10k; use a deliberately
    # complete business working set rather than the old display-sized 200-row page.
    opps = repo.list_serenity_opportunities(
        theme_id=theme_id,
        status="active",
        limit=10_000,
    )
    if not opps:
        raise HTTPException(
            409,
            "Theme has no active opportunities to submit; it remains active and retryable.",
        )

    # The repository owns the all-or-nothing handoff. Theme state, opportunity
    # intent, and every durable pending carrier become visible together; a failed
    # insert propagates as an error and leaves the theme retryable.
    queued_ids = repo.enqueue_serenity_theme_for_pi(
        theme_id,
        [str(opp["id"]) for opp in opps],
    )

    try:
        from aletheia.agent_runtime.pi_daemon import notify_pi_daemon

        notify_pi_daemon(source="serenity_promote")
    except Exception:
        pass

    return {
        "theme_id": theme_id,
        "queued": len(queued_ids),
        "opportunity_ids": queued_ids,
        "status": "submitted_to_pi",
        "message": (
            f"Theme promoted: {len(queued_ids)} "
            f"opportunit{'y' if len(queued_ids) == 1 else 'ies'} enqueued for PI evaluation."
        ),
    }


@router.get("/serenity/claims")
def list_serenity_claims(
    run_id: str | None = Query(None),
    price_relevant: bool | None = Query(None),
    limit: int = Query(200, le=500),
):
    _ensure_db()
    items = repo.list_serenity_claims(run_id=run_id, price_relevant=price_relevant, limit=limit)
    return {"items": items}


@router.get("/serenity/themes/{theme_id}/claims")
def get_serenity_theme_claims(theme_id: str, limit: int = Query(200, le=500)):
    _ensure_db()
    theme = repo.get_serenity_theme(theme_id)
    if not theme:
        raise HTTPException(404, "Theme not found")
    claims = repo.list_serenity_claims_for_theme(theme_id, limit=limit)
    return {"items": claims, "count": len(claims)}


@router.get("/serenity/themes/{theme_id}/detail")
def get_serenity_theme_detail(theme_id: str):
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
def get_serenity_run_artifacts(run_id: str):
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

    danger_patterns = [
        "buy",
        "sell",
        "trade",
        "order",
        "execute",
        "ignore previous",
        "bypass",
        "unlock",
        "disable risk",
    ]
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
def get_family_scorecard():
    """Per-scanner_family performance statistics (win_rate, expectancy, alpha)."""
    _ensure_db()
    from aletheia.analytics.outcome_integrity import build_outcome_cohort_summary
    from aletheia.analytics.family_scorecard import compute_family_scorecard_with_epoch

    scorecard = compute_family_scorecard_with_epoch()
    return {**scorecard, **build_outcome_cohort_summary()}


@router.get("/analytics/shadow-trades")
def get_shadow_trades(
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
