"""Capital events API: deposit/withdraw tracking for equity curve baseline."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo

router = APIRouter(prefix="/api/capital", tags=["capital"])

_migrated = False


def _ensure_db():
    # #88: run migrations once per process, not on every request. run_migrations() does
    # a batch of DDL + column checks; calling it inside every async capital handler
    # added avoidable latency on a hot-ish path (migrations are idempotent + global).
    global _migrated
    if not _migrated:
        run_migrations()
        _migrated = True


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
