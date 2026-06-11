"""Data API routes for dashboard consumption."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from aletheia.storage.sqlite import get_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo

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
