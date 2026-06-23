"""Capital events API: deposit/withdraw tracking for equity curve baseline."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo

router = APIRouter(prefix="/api/capital", tags=["capital"])


def _ensure_db():
    run_migrations()


class CapitalEventRequest(BaseModel):
    broker_id: str
    event_type: str  # "deposit" | "withdraw"
    amount_usdt: float
    note: str = ""


@router.get("/net-deposits")
async def get_net_deposits():
    _ensure_db()
    net = repo.get_net_deposits()
    first_at = repo.get_first_capital_event_time()
    return {"net_deposits_usdt": net, "first_event_at": first_at}


@router.get("/events")
async def list_events():
    _ensure_db()
    return repo.list_capital_events()


@router.post("/events")
async def create_event(body: CapitalEventRequest):
    _ensure_db()
    if body.event_type not in ("deposit", "withdraw"):
        raise HTTPException(400, "event_type must be 'deposit' or 'withdraw'")
    if body.amount_usdt <= 0:
        raise HTTPException(400, "amount_usdt must be positive")

    from aletheia.storage.models import CapitalEvent
    event = CapitalEvent(
        broker_id=body.broker_id,
        event_type=body.event_type,
        amount_usdt=body.amount_usdt,
        note=body.note or "manual",
    )
    repo.insert_capital_event(event)
    return {"id": event.id, "status": "created"}
