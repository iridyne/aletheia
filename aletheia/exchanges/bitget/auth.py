"""Bitget API authentication — HMAC-SHA256 request signing."""

import hmac
import hashlib
import base64
import time
from datetime import datetime, timezone


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
