"""Data API routes for dashboard consumption."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.storage.sqlite import get_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo

router = APIRouter(prefix="/api", tags=["data"])


def _ensure_db():
    run_migrations()


@router.get("/agent-runs")
async def get_agent_runs():
    _ensure_db()
    return repo.list_agent_runs()


@router.get("/agent-runs/{run_id}")
async def get_agent_run(run_id: str):
    _ensure_db()
    return repo.get_agent_run(run_id)


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
    repo.update_trade_proposal(proposal_id, status="approved")
    write_audit({"actor": "human", "source": "dashboard", "event_type": "proposal_approved",
                 "entity_type": "trade_proposal", "entity_id": proposal_id,
                 "message": f"Proposal approved for paper execution"})
    # Execute through the gateway (risk-checked)
    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(
        symbol=p["symbol"], market_type=p["market_type"], direction=p["direction"],
        order_type=p.get("order_type", "market"), quantity=p.get("quantity") or 0.0,
        price=p.get("price") or 0.0, execution_mode="paper_internal",
        strategy_id=p.get("strategy_id"), agent_rationale=p.get("agent_rationale"),
    )
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


@router.get("/strategies")
async def get_strategies():
    _ensure_db()
    return repo.list_strategies()


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
async def get_market_snapshots(symbol: str | None = None, limit: int = 200):
    _ensure_db()
    return repo.list_market_snapshots(symbol=symbol, limit=limit)


@router.get("/market/ohlcv")
async def get_market_ohlcv(symbol: str = "BTCUSDT", timeframe: str = "1h", limit: int = 100):
    """Fetch OHLCV candles from Bitget for charting (public endpoint)."""
    from aletheia.config.loader import load_config
    from aletheia.exchanges.bitget.client import BitgetClient
    from aletheia.trading.instruments import BITGET_GRANULARITY
    cfg = load_config()
    client = BitgetClient(mode=cfg.exchange.mode)
    gran = BITGET_GRANULARITY.get(timeframe, timeframe)
    try:
        candles = await client.get_ohlcv(symbol, timeframe=gran, limit=limit, market_type="spot")
        # lightweight-charts wants ascending time in seconds
        rows = [
            {"time": int(c.timestamp / 1000), "open": c.open, "high": c.high,
             "low": c.low, "close": c.close, "volume": c.volume}
            for c in candles
        ]
        rows.sort(key=lambda r: r["time"])
        return rows
    except Exception as e:
        return {"error": str(e), "candles": []}


@router.get("/market/portfolio-history")
async def get_portfolio_history(limit: int = 100):
    _ensure_db()
    return repo.list_portfolio_snapshots(limit=limit)


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
