"""Bitget API authentication — HMAC-SHA256 request signing + candidate credential probe."""

import base64
import hashlib
import hmac
import time

from aletheia.net import httpx_client

BASE_URL = "https://api.bitget.com"
DEMO_BASE_URL = "https://api.bitget.com"  # Same URL, demo distinguished by header


def get_timestamp() -> str:
    """Get current UTC timestamp in milliseconds as string."""
    return str(int(time.time() * 1000))


def sign_request(
    timestamp: str,
    method: str,
    request_path: str,
    body: str,
    secret_key: str,
) -> str:
    """Sign a Bitget API request.

    Signature = base64(hmac_sha256(timestamp + method + requestPath + body, secretKey))
    """
    message = timestamp + method.upper() + request_path + body
    mac = hmac.new(
        secret_key.encode("utf-8"),
        msg=message.encode("utf-8"),
        digestmod=hashlib.sha256,
    )
    return base64.b64encode(mac.digest()).decode("utf-8")


def build_headers(
    api_key: str,
    secret_key: str,
    passphrase: str,
    method: str,
    request_path: str,
    body: str = "",
    demo: bool = False,
) -> dict[str, str]:
    """Build authenticated Bitget API headers."""
    timestamp = get_timestamp()
    signature = sign_request(timestamp, method, request_path, body, secret_key)
    headers = {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }
    if demo:
        headers["paptrading"] = "1"
    return headers

async def probe_candidate_credentials(
    api_key: str,
    api_secret: str,
    passphrase: str,
    profile: str,
) -> dict:
    """Verify-before-commit probe for CANDIDATE Bitget credentials.

    Signs one read-only account-info request with the supplied credentials and
    never stores, logs, or echoes them. Rejections return the stable
    secret-safe ``credential_probe_rejected`` code without upstream message
    text (a venue error can carry the signed request, i.e. the API key).
    """
    if profile not in ("demo", "live"):
        raise ValueError("profile must be 'demo' or 'live'")
    path = "/api/v2/spot/account/info"
    try:
        headers = build_headers(api_key, api_secret, passphrase, "GET", path, "", demo=profile == "demo")
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{BASE_URL}{path}", headers=headers)
            data = resp.json()
        if data.get("code") == "00000":
            return {"connected": True, "profile": profile}
        return {"connected": False, "profile": profile, "error": "credential_probe_rejected"}
    except Exception as e:
        err_msg = type(e).__name__
        if "timeout" in str(e).lower():
            err_msg = "connection_timeout"
        elif "connect" in str(e).lower():
            err_msg = "connection_failed"
        return {"connected": False, "profile": profile, "error": err_msg}
