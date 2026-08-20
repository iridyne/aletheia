"""Capital events API: audited deposit/withdraw tracking and net-flow reporting."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from aletheia.storage import repositories as repo

router = APIRouter(prefix="/api/capital", tags=["capital"])

class CapitalEventRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    broker_id: str
    event_type: str  # "deposit" | "withdraw"
    amount_usdt: float = Field(gt=0)
    note: str = ""


@router.get("/net-deposits")
async def get_net_deposits():
    net = repo.get_net_deposits()
    first_at = repo.get_first_capital_event_time()
    return {"net_deposits_usdt": net, "first_event_at": first_at}


@router.get("/events")
async def list_events():
    return repo.list_capital_events()


@router.post("/events")
async def create_event(body: CapitalEventRequest):
    if body.event_type not in ("deposit", "withdraw"):
        raise HTTPException(400, "event_type must be 'deposit' or 'withdraw'")

    from aletheia.storage.models import CapitalEvent
    event = CapitalEvent(
        broker_id=body.broker_id,
        event_type=body.event_type,
        amount_usdt=body.amount_usdt,
        note=body.note or "manual",
    )
    repo.insert_capital_event(event)
    from aletheia.server.routes.data import invalidate_account_equity_history_cache
    invalidate_account_equity_history_cache()

    # #87 fix: capital deposits/withdrawals are trust-bearing account cash-flow
    # facts. The row was persisted but no audit event was written, so later
    # reconstruction of the owner's capital history had no hash-chain trace.
    # Emit an audit event now. Account-equity history consumes this event in a
    # separate adjusted series; verified trading PnL remains unchanged.
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human",
        "source": "capital_events_route",
        "event_type": f"capital_{body.event_type}",
        "entity_type": "capital_event",
        "entity_id": event.id,
        "after": {
            "broker_id": body.broker_id,
            "event_type": body.event_type,
            "amount_usdt": body.amount_usdt,
            "note": body.note or "manual",
        },
        "message": (
            f"Capital {body.event_type} recorded: {body.amount_usdt} USDT on "
            f"{body.broker_id} ({body.note or 'manual'})"
        ),
    })
    return {"id": event.id, "status": "created"}
