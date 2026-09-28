"""
WebSocket endpoint for real-time frontend push.

Two process-wide polling tiers feed every connected dashboard:

  ALERT tier  (100 ms) — highest priority
    • In-memory infra events: warning + critical (no DB — must poll the deque directly)
    • Collector failures: collector_health_state rows with last_status IN
      ('failed', 'timed_out') — separate from the generic collector fingerprint
      so failures surface within 100 ms instead of waiting for the 500 ms cycle.
    Pushes  {"type": "alert",  "changed": [...]}

  REGULAR tier (500 ms) — all other DB-backed data categories
    Pushes  {"type": "update", "changed": [...]}

Fingerprint queries run once per backend process, not once per connection.
Frontend reacts to both by calling queryClient.invalidateQueries() for the
relevant React Query cache keys.  Actual data is still fetched via REST.

NOT covered by WS (in-memory or external-API — left on REST polling):
  /api/status, /api/health, /api/risk/kill-switch,
  /api/model/health, /api/uta/state,
  exchange connectivity tests, OAuth status flows
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

logger = logging.getLogger("aletheia.server.routes.ws")

router = APIRouter()
log = logging.getLogger(__name__)

# ── WebSocket admission ticket store ─────────────────────────────────────────
# Short-lived single-use tokens issued over authenticated HTTP so the browser
# WebSocket API (which cannot attach custom headers) can authenticate the upgrade.

_WS_TICKET_TTL = 30  # seconds
_ws_tickets: dict[str, float] = {}  # token -> expires_at (monotonic)


def _issue_ws_ticket() -> str:
    _cleanup_expired_tickets()
    token = secrets.token_urlsafe(32)
    _ws_tickets[token] = time.monotonic() + _WS_TICKET_TTL
    return token


def _consume_ws_ticket(token: str) -> bool:
    """Returns True and removes the ticket if valid and unexpired (single-use)."""
    expires = _ws_tickets.pop(token, None)
    return expires is not None and time.monotonic() <= expires


def _cleanup_expired_tickets() -> None:
    now = time.monotonic()
    for t in [t for t, exp in _ws_tickets.items() if exp <= now]:
        del _ws_tickets[t]


def _ws_remote_auth_required() -> bool | None:
    """Required/not required, or None when the admission policy is unavailable."""
    from aletheia.config.loader import load_config
    try:
        cfg = load_config(persist_migrations=False)
    except Exception as exc:
        logger.warning("ws: remote-auth config read unavailable (%s)", type(exc).__name__)
        return None
    tunnel_enabled = bool(getattr(getattr(cfg, "tunnel", None), "enabled", False))
    remote_control = getattr(cfg, "remote_control", None)
    return tunnel_enabled or bool(
        getattr(remote_control, "enabled", False)
        and getattr(remote_control, "require_token", False)
    )


_MAX_WS_CLIENTS = 50  # ponytail: global cap; raise if many simultaneous dashboard tabs needed

# ── Regular-tier fingerprint queries (500 ms) ────────────────────────────────
# Each returns a single scalar that changes when the underlying data changes.
# try/except in _sync_fingerprints makes every query resilient to schema drift.
_REGULAR_QUERIES: dict[str, str] = {
    # Core trading
    "agent_runs":      "SELECT MAX(COALESCE(ended_at, started_at)) FROM agent_runs",
    "opportunities":   "SELECT MAX(created_at) FROM opportunities",
    "proposals":       "SELECT MAX(created_at) FROM trade_proposals",
    "pi_decisions":    "SELECT MAX(created_at) FROM pi_decisions",
    "positions":       (
        "SELECT revision FROM position_realtime_revision WHERE singleton=1"
    ),
    "fills":           "SELECT MAX(filled_at) FROM fills",
    "risk_decisions":  "SELECT MAX(created_at) FROM risk_decisions",
    # Market data
    "market_snapshots": "SELECT MAX(snapshot_at) FROM market_snapshots",
    "collector_health": "SELECT MAX(COALESCE(last_finished_at, last_attempt_at)) FROM collector_health_state",
    "macro_snaps":      "SELECT MAX(observed_at) FROM macro_snapshots",
    # Portfolio
    "portfolio_snaps":  "SELECT MAX(snapshot_at) FROM portfolio_snapshots",
    # Operator / runtime
    "heartbeats":      "SELECT MAX(last_seen_at) FROM agent_shell_heartbeats",
    # Research / signals
    "serenity_runs":   "SELECT MAX(COALESCE(finished_at, started_at)) FROM serenity_research_runs",
    "news_items":      "SELECT MAX(fetched_at) FROM news_items",
    "prediction_runs": "SELECT MAX(COALESCE(completed_at, started_at)) FROM prediction_observer_runs",
    # Universe
    "universe_snaps":  "SELECT MAX(created_at) FROM universe_snapshots",
    "candidate_planning": "SELECT MAX(updated_at) FROM candidate_planning_generations",
    "entry_plans": "SELECT MAX(updated_at) FROM entry_plans",
}

# ── Alert-tier DB queries (100 ms) ───────────────────────────────────────────
# Only the subset of data where late delivery is genuinely harmful.
_ALERT_DB_QUERIES: dict[str, str] = {
    "collector_error": (
        "SELECT MAX(COALESCE(last_attempt_at, last_scheduled_at)) "
        "FROM collector_health_state "
        "WHERE last_status IN ('failed','timed_out','error')"
    ),
}

_REGULAR_INTERVAL = 0.5   # seconds
_ALERT_INTERVAL   = 0.1   # seconds


def _run_fingerprints(queries: dict[str, str]) -> dict[str, str]:
    """
    Thread-safe helper called via run_in_executor.
    Uses the thread-local SQLite connection from get_db().
    Returns empty string for any table that doesn't exist yet.
    """
    from aletheia.storage.sqlite import get_db
    db = get_db()
    result: dict[str, str] = {}
    for key, sql in queries.items():
        try:
            row = db.execute(sql).fetchone()
            result[key] = str(row[0]) if (row and row[0] is not None) else "0"
        except Exception:
            result[key] = "0"
    return result


def _get_latest_infra_ts() -> str:
    """
    Composite infra fingerprint for the alert tier.

    Returns ``"<latest warning/critical ts>|<outstanding count>|<resolution seq>"``.
    The latest warning/critical timestamp alone cannot see an info-severity
    resolution (e.g. ``exchange_connectivity_resumed``), which would otherwise
    leave clients showing a stale resolved incident; and a critical that has been
    evicted from the bounded deque is invisible in the raw list, so the curated
    outstanding count and the monotonic resolution counter are folded in too.

    Reads from the in-memory buffers — no DB, no executor needed in principle,
    but we call it via run_in_executor anyway because emit_infra_event holds
    a threading.Lock that we don't want to starve the event loop with.
    """
    from aletheia.infra.events import (
        list_infra_events,
        list_outstanding_infra_events,
        resolution_seq,
    )

    events = list_infra_events(min_severity="warning", limit=1)
    latest_warn_or_critical_ts = events[0]["created_at"] if events else ""
    outstanding_count = len(list_outstanding_infra_events())
    return f"{latest_warn_or_critical_ts}|{outstanding_count}|{resolution_seq()}"


class _RealtimeHub:
    """Share one fingerprint sampler across every connected dashboard."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.alert_task: asyncio.Task | None = None
        self.regular_task: asyncio.Task | None = None
        self.send_lock = asyncio.Lock()
        self.lifecycle_lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        async with self.lifecycle_lock:
            self.clients.add(ws)
            loop = asyncio.get_running_loop()
            if self.alert_task is None or self.alert_task.done():
                self.alert_task = asyncio.create_task(
                    _guarded_sampler_loop(self, loop, _alert_loop, "alert"),
                    name="ws-alert-fingerprints",
                )
            if self.regular_task is None or self.regular_task.done():
                self.regular_task = asyncio.create_task(
                    _guarded_sampler_loop(self, loop, _regular_loop, "regular"),
                    name="ws-regular-fingerprints",
                )

    async def remove(self, ws: WebSocket) -> None:
        async with self.lifecycle_lock:
            self.clients.discard(ws)
            if self.clients:
                return
            tasks = [
                task
                for task in (self.alert_task, self.regular_task)
                if task is not None
            ]
            self.alert_task = None
            self.regular_task = None
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def sampler_failed(self, tier: str) -> None:
        """Tear down admitted sockets when a sampler exits unexpectedly."""
        async with self.lifecycle_lock:
            current = asyncio.current_task()
            expected = self.alert_task if tier == "alert" else self.regular_task
            if expected is not current:
                return

            clients = tuple(self.clients)
            sibling = self.regular_task if tier == "alert" else self.alert_task
            self.clients.clear()
            self.alert_task = None
            self.regular_task = None
            if sibling is not None:
                sibling.cancel()

        if sibling is not None:
            await asyncio.gather(sibling, return_exceptions=True)
        results = await asyncio.gather(
            *(
                client.close(code=1012, reason="realtime sampler failed")
                for client in clients
            ),
            return_exceptions=True,
        )
        if any(isinstance(result, Exception) for result in results):
            log.debug("failed to close realtime client after sampler failure")

    async def broadcast(self, payload: dict) -> None:
        async with self.send_lock:
            disconnected: list[WebSocket] = []
            for client in tuple(self.clients):
                try:
                    await client.send_json(payload)
                except Exception:  # noqa: BLE001 - disconnect is handled by route
                    disconnected.append(client)
            for client in disconnected:
                self.clients.discard(client)


async def _guarded_sampler_loop(
    hub: _RealtimeHub,
    loop: asyncio.AbstractEventLoop,
    sampler,
    tier: str,
) -> None:
    """Keep sampler failures observable without treating deliberate cancel as one."""
    try:
        await sampler(hub, loop)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - the hub owns the failure boundary
        log.exception("websocket %s sampler failed; tearing down realtime clients", tier)
        await hub.sampler_failed(tier)


_hub = _RealtimeHub()


async def _alert_loop(hub: _RealtimeHub, loop: asyncio.AbstractEventLoop) -> None:
    """
    100 ms tier: infra events (in-memory) + collector failures (DB).
    Pushes {"type": "alert", "changed": [...]} immediately on new errors.
    """
    last_infra_ts = ""
    last_db: dict[str, str] = {}

    while True:
        await asyncio.sleep(_ALERT_INTERVAL)

        # 1. In-memory infra events (warning + critical)
        infra_ts = await loop.run_in_executor(None, _get_latest_infra_ts)
        infra_changed = infra_ts and infra_ts != last_infra_ts
        if infra_changed:
            last_infra_ts = infra_ts

        # 2. Collector failure rows in DB
        db_cur = await loop.run_in_executor(None, lambda: _run_fingerprints(_ALERT_DB_QUERIES))
        db_changed = [k for k in db_cur if db_cur[k] != last_db.get(k) and db_cur[k] != "0"]
        if db_changed:
            last_db = dict(db_cur)

        # Aggregate and push in one message if anything changed
        changed: list[str] = []
        if infra_changed:
            changed.append("infra_events")
        changed.extend(db_changed)
        if changed:
            await hub.broadcast({"type": "alert", "changed": changed})


async def _regular_loop(hub: _RealtimeHub, loop: asyncio.AbstractEventLoop) -> None:
    """
    500 ms tier: all DB-backed data categories.
    Pushes {"type": "update", "changed": [...]} when fingerprints change.
    """
    last: dict[str, str] = {}
    while True:
        await asyncio.sleep(_REGULAR_INTERVAL)
        current = await loop.run_in_executor(None, lambda: _run_fingerprints(_REGULAR_QUERIES))
        changed = [k for k in current if current[k] != last.get(k)]
        if changed:
            await hub.broadcast({"type": "update", "changed": changed})
            last = dict(current)


@router.get("/ws/ticket")
async def issue_ws_ticket() -> dict:
    """Issue a short-lived single-use WebSocket connection ticket.

    Protected by the HTTP owner-auth middleware. Callers pass the returned
    ticket as ?ticket= on the /api/ws upgrade request, since the browser
    WebSocket API cannot attach X-Aletheia-Token headers.
    """
    return {"ticket": _issue_ws_ticket(), "ttl_seconds": _WS_TICKET_TTL}


@router.websocket("/ws")
async def ws_realtime(
    ws: WebSocket,
    ticket: str | None = Query(default=None),
) -> None:
    """
    Push-notification WebSocket.  Pushes category names only (no data payloads).
    Requires a valid ?ticket= when Remote Control owner auth is active.
    """
    auth_required = _ws_remote_auth_required()
    if auth_required is None:
        await ws.close(code=1013, reason="remote_auth_policy_unavailable")
        return
    if auth_required:
        if not ticket or not _consume_ws_ticket(ticket):
            await ws.close(code=4403)
            return
    if len(_hub.clients) >= _MAX_WS_CLIENTS:
        await ws.close(code=1013)
        return
    await ws.accept()
    loop = asyncio.get_event_loop()

    try:
        # ── Initial snapshot: tell the client to refresh all categories once ──
        current = await loop.run_in_executor(None, lambda: _run_fingerprints(_REGULAR_QUERIES))
        all_keys = list(current.keys()) + list(_ALERT_DB_QUERIES.keys()) + ["infra_events"]
        await ws.send_json({"type": "update", "changed": all_keys})
        await _hub.add(ws)
        while True:
            await ws.receive()

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug("ws_realtime closed: %s", exc)
        try:
            await ws.close()
        except Exception:
            logger.debug("ws: websocket close after an error failed", exc_info=True)
    finally:
        await _hub.remove(ws)
