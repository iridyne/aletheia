"""Application-layer dashboard authentication for the Aletheia front door.

Replaces the Caddy ``basicauth`` browser popup with a proper web login page.
The React SPA is NOT modified — authentication is enforced only at the HTTP
layer by the front-door middleware:

  * Unauthenticated HTML navigation  -> the login page is served (HTTP 200)
  * Unauthenticated /api/* request    -> HTTP 401 JSON
  * Authenticated request (valid session cookie) -> proxied/served as before

Credentials are a single SHARED team password (any username is accepted). The
bcrypt hash lives in ``dashboard_auth.json`` next to this file. This keeps the
dashboard shareable with a team while staying behind a login.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path

import bcrypt
from itsdangerous import BadSignature, URLSafeTimedSerializer

logger = logging.getLogger("aletheia.frontdoor.auth")

SESSION_COOKIE_NAME = "aletheia_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days when "remember me" is on
SESSION_MAX_AGE_SHORT = 60 * 60 * 24  # 1 day otherwise

_HERE = Path(__file__).resolve().parent
_AUTH_CONFIG_PATH = _HERE / "dashboard_auth.json"
_SECRET_PATH = Path(os.environ.get("ALETHEIA_DASHBOARD_AUTH_SECRET_PATH", "/home/ubuntu/aletheia/dashboard_auth_secret"))


def _load_secret() -> str:
    """Stable HMAC secret for session cookies; generated once and persisted."""
    if _SECRET_PATH.exists():
        return _SECRET_PATH.read_text().strip()
    secret = secrets.token_hex(32)
    _SECRET_PATH.write_text(secret)
    os.chmod(_SECRET_PATH, 0o600)
    return secret


_SERIALIZER = URLSafeTimedSerializer(_load_secret())


def _persist(hash_value: str) -> None:
    _AUTH_CONFIG_PATH.write_text(json.dumps({"password_hash": hash_value}, indent=2))
    os.chmod(_AUTH_CONFIG_PATH, 0o600)


def _resolve_password_hash() -> str:
    """Load the bcrypt hash, upgrade a plaintext ``password`` field if present,
    or generate a strong random default on first boot (logged for the operator)."""
    cfg: dict = {}
    if _AUTH_CONFIG_PATH.exists():
        try:
            cfg = json.loads(_AUTH_CONFIG_PATH.read_text())
        except Exception:
            cfg = {}
    if cfg.get("password_hash"):
        return cfg["password_hash"]
    if cfg.get("password"):
        h = bcrypt.hashpw(cfg["password"].encode("utf-8"), bcrypt.gensalt()).decode()
        _persist(h)
        logger.info("Upgraded plaintext dashboard password to a bcrypt hash.")
        return h
    # first boot: generate a strong shared password and log it for the operator
    pwd = secrets.token_urlsafe(12)
    h = bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode()
    _persist(h)
    logger.warning(
        "Generated default dashboard password: %s  (change via dashboard_auth.json)", pwd
    )
    return h


_PASSWORD_HASH = _resolve_password_hash()


def verify_credentials(username: str, password: str) -> bool:
    """Any non-empty username + the shared password is accepted (team-shareable)."""
    if not password:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), _PASSWORD_HASH.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_session_token(username: str) -> str:
    return _SERIALIZER.dumps({"u": username, "t": int(time.time())})


def verify_session_token(token: str | None) -> str | None:
    if not token:
        return None
    try:
        data = _SERIALIZER.loads(token, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None
    return data.get("u")


def load_login_page_html() -> str:
    p = _HERE / "dashboard_login.html"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return _FALLBACK_HTML


def _is_public_static(path: str) -> bool:
    """Static assets that are safe to serve even before authentication."""
    if path.startswith("/assets/"):
        return True
    return path in ("/favicon.svg", "/pwa-192.png", "/pwa-512.png")


_FALLBACK_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>ALETHEIA · Login</title></head>
<body style="background:#0a0d14;color:#eef2f8;font-family:system-ui,sans-serif;display:grid;place-items:center;height:100vh">
<form method="post" action="/api/auth/login" style="background:#141925;padding:32px;border-radius:12px">
<h2>ALETHEIA</h2>
<input name="username" placeholder="username" style="display:block;margin:8px 0;padding:10px"/>
<input name="password" type="password" placeholder="password" style="display:block;margin:8px 0;padding:10px"/>
<button style="padding:10px 20px">Login</button></form></body></html>"""
