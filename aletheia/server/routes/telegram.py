"""Telegram bot reachability health route.

Read-only: reports whether a configured bot token can reach the Telegram API
(via getMe) and the resolved bot username. The token itself is NEVER returned,
logged, or echoed. Setting the token stays CLI-only (`aletheia secrets set
telegram.bot_token`) — this route adds no write surface.
"""

import httpx
from fastapi import APIRouter

from aletheia.config.secrets import get_secret

router = APIRouter(prefix="/api", tags=["telegram"])


@router.get("/telegram/health")
async def telegram_health():
    """Live reachability check for the configured Telegram bot token.

    Returns {reachable, username?, error?}. honest 'not_configured' when no token
    is set. Never includes the token in the response.
    """
    token = get_secret("telegram.bot_token")
    if not token:
        return {"reachable": False, "error": "not_configured"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = resp.json()
        if data.get("ok"):
            return {"reachable": True, "username": data.get("result", {}).get("username")}
        return {"reachable": False, "error": data.get("description", "unknown")}
    except Exception as e:
        return {"reachable": False, "error": str(e)}
