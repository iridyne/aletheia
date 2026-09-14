"""Read-only Telegram getMe transport with sanitized failures."""

from aletheia.net import httpx_client
from aletheia.config.secrets import get_secret


async def health():
    """Live reachability check for the configured Telegram bot token.

    Returns {reachable, username?, error?}. honest 'not_configured' when no token
    is set. Never includes the token in the response.
    """
    token = get_secret("telegram.bot_token")
    if not token:
        return {"reachable": False, "error": "not_configured"}
    try:
        async with httpx_client(timeout=10) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = resp.json()
        if data.get("ok"):
            return {"reachable": True, "username": data.get("result", {}).get("username")}
        return {"reachable": False, "error": "request_rejected"}
    except Exception as e:
        # Defense-in-depth: this path holds the bot token in the request URL.
        # Return only the exception class name, never the raw str(e) (which an
        # unexpected exception type could echo the token-bearing URL into).
        return {"reachable": False, "error": type(e).__name__}
