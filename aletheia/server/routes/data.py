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


@router.get("/trades")
async def get_trades():
    _ensure_db()
    return repo.list_orders()


@router.get("/positions")
async def get_positions():
    _ensure_db()
    return repo.list_positions()


@router.get("/strategies")
async def get_strategies():
    _ensure_db()
    return repo.list_strategies()


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
