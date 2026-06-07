"""Codex device-auth login flow — web-triggered, in-memory, stores NO token.

The dashboard's "Connect Codex" button calls start_login(), which spawns
`codex login --device-auth` and parses the verification URL + user code from its
stdout to display in the web UI. The user authorizes in a browser; poll_status()
then asks the authoritative `codex login status` whether login completed. The real
OAuth + token storage is entirely owned by the codex CLI (~/.codex) — we keep only
transient session state in memory and never handle a token.

The device-auth stdout format is not documented, so parsing is defensive: if we
cannot extract a URL + code (or the process exits early) we fail with
`device_auth_parse_failed` and the UI tells the user to run `codex login` in a
terminal instead. Nothing here raises into the caller.
"""

import asyncio
import logging
import re
import shutil

from aletheia.files.jsonl import write_audit
from aletheia.gateway.pidfile import _no_window_kwargs

logger = logging.getLogger("aletheia.model_provider.codex_auth")

_COMMAND = "codex"
_PARSE_TIMEOUT = 20  # seconds to wait for URL+code before giving up
_STATUS_TIMEOUT = 10

_URL_RE = re.compile(r"https?://\S+")
# device/user codes appear after "code" keyword on the line
_CODE_RE = re.compile(r"(?i)(?:code|enter).*?\b([A-Z0-9]{4}-[A-Z0-9]{4}|[A-Z0-9]{8})\b")


class CodexLoginSession:
    def __init__(self):
        self.state = "idle"  # idle|awaiting_authorization|completed|failed|cancelled
        self.verification_uri: str | None = None
        self.user_code: str | None = None
        self.error: str | None = None
        self._proc = None

    def public(self) -> dict:
        return {
            "state": self.state,
            "verification_uri": self.verification_uri,
            "user_code": self.user_code,
            "error": self.error,
        }


_session = CodexLoginSession()
_lock = asyncio.Lock()


async def start_login() -> dict:
    """Spawn `codex login --device-auth` and parse URL+code. Never raises."""
    global _session
    async with _lock:
        if _session.state == "awaiting_authorization":
            return _session.public()  # idempotent

        _session = CodexLoginSession()
        if not shutil.which(_COMMAND):
            _session.state = "failed"
            _session.error = "not_installed"
            return _session.public()

        try:
            proc = await asyncio.create_subprocess_exec(
                _COMMAND, "login", "--device-auth",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **_no_window_kwargs(),
            )
        except Exception as e:
            _session.state = "failed"
            _session.error = f"spawn_failed:{e}"
            return _session.public()

        _session._proc = proc
        try:
            await asyncio.wait_for(_read_until_code(proc, _session), timeout=_PARSE_TIMEOUT)
        except asyncio.TimeoutError:
            pass

        if _session.verification_uri and _session.user_code:
            _session.state = "awaiting_authorization"
        else:
            _session.state = "failed"
            _session.error = "device_auth_parse_failed"
            try:
                proc.kill()
            except Exception:
                pass
        return _session.public()


async def _read_until_code(proc, session) -> None:
    """Read stdout lines until both a URL and a code are found, or EOF."""
    while True:
        line = await proc.stdout.readline()
        if not line:
            return  # EOF
        text = line.decode("utf-8", "replace")
        if session.verification_uri is None:
            m = _URL_RE.search(text)
            if m:
                session.verification_uri = m.group(0).rstrip(".,)")
        if session.user_code is None:
            m = _CODE_RE.search(text)
            if m:
                session.user_code = m.group(1)
        if session.verification_uri and session.user_code:
            return


async def poll_status() -> dict:
    """Ask `codex login status` whether login completed. Authoritative. Never raises."""
    if not shutil.which(_COMMAND):
        _session.state = "failed"
        _session.error = "not_installed"
        return _session.public()
    try:
        proc = await asyncio.create_subprocess_exec(
            _COMMAND, "login", "status",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **_no_window_kwargs(),
        )
        await asyncio.wait_for(proc.communicate(), timeout=_STATUS_TIMEOUT)
        rc = proc.returncode
    except Exception as e:
        logger.warning("codex login status failed: %s", e)
        return _session.public()

    if rc == 0:
        first_complete = _session.state != "completed"
        _session.state = "completed"
        _session.error = None
        if first_complete:
            write_audit({
                "actor": "human", "source": "dashboard",
                "event_type": "codex_login_completed", "entity_type": "model_provider",
                "entity_id": "codex_cli", "message": "Codex login completed",
            })
    return _session.public()


def cancel() -> dict:
    """Cancel an in-progress login. Never raises."""
    if _session._proc is not None:
        try:
            _session._proc.kill()
        except Exception:
            pass
    _session.state = "cancelled"
    write_audit({
        "actor": "human", "source": "dashboard",
        "event_type": "codex_login_cancelled", "entity_type": "model_provider",
        "entity_id": "codex_cli", "message": "Codex login cancelled",
    })
    return _session.public()
