"""Read-only health projection for the optional Telegram transport."""

from fastapi import APIRouter
from aletheia.telegram import transport_health

router = APIRouter(prefix="/api", tags=["telegram"])


@router.get("/telegram/health")
async def telegram_health():
    return await transport_health()
