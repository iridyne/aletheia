"""
WebSocket endpoint for real-time frontend push.

Two polling tiers run concurrently per connection:

  ALERT tier  (100 ms) — highest priority
    • In-memory infra events: warning + critical (no DB — must poll the deque directly)
    • Collector failures: collector_health_state rows with last_status IN
      ('failed', 'timed_out') — separate from the generic collector fingerprint
      so failures surface within 100 ms instead of waiting for the 500 ms cycle.
    Pushes  {"type": "alert",  "changed": [...]}

  REGULAR tier (500 ms) — all other DB-backed data categories
    Pushes  {"type": "update", "changed": [...]}

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

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
log = logging.getLogger(__name__)

# ── Regular-tier fingerprint queries (500 ms) ────────────────────────────────
# Each returns a single scalar that changes when the underlying data changes.
# try/except in _sync_fingerprints makes every query resilient to schema drift.
_REGULAR_QUERIES: dict[str, str] = {
    # Core trading
    "agent_runs":      "SELECT MAX(COALESCE(ended_at, started_at)) FROM agent_runs",
    "opportunities":   "SELECT MAX(created_at) FROM opportunities",
    "proposals":       "SELECT MAX(created_at) FROM trade_proposals",
    "pi_decisions":    "SELECT MAX(created_at) FROM pi_decisions",
    "positions":       "SELECT MAX(COALESCE(updated_at, created_at)) FROM positions",
    "fills":           "SELECT MAX(filled_at) FROM fills",
    "risk_decisions":  "SELECT MAX(created_at) FROM risk_decisions",
    # Market data
    "market_snapshots": "SELECT MAX(timestamp) FROM market_snapshots",
    "collector_health": "SELECT MAX(COALESCE(last_finished_at, last_attempt_at)) FROM collector_health_state",
    "macro_snaps":      "SELECT MAX(observed_at) FROM macro_snapshots",
    # Portfolio
    "portfolio_snaps":  "SELECT MAX(snapshot_at) FROM portfolio_snapshots",
    # Operator / runtime
    "heartbeats":      "SELECT MAX(last_seen_at) FROM agent_shell_heartbeats",
    "runtime_events":  "SELECT MAX(created_at) FROM runtime_events",
    # Research / signals
    "serenity_runs":   "SELECT MAX(COALESCE(finished_at, started_at)) FROM serenity_research_runs",
    "news_items":      "SELECT MAX(fetched_at) FROM news_items",
    "prediction_runs": "SELECT MAX(COALESCE(completed_at, started_at)) FROM prediction_observer_runs",
    # Universe
    "universe_snaps":  "SELECT MAX(created_at) FROM universe_snapshots",
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
    Returns the created_at of the most recent warning/critical infra event.
    Reads from the in-memory deque — no DB, no executor needed in principle,
    but we call it via run_in_executor anyway because emit_infra_event holds
    a threading.Lock that we don't want to starve the event loop with.
    """
    from aletheia.infra.events import list_infra_events
    events = list_infra_events(min_severity="warning", limit=1)
    return events[0]["created_at"] if events else ""


async def _alert_loop(ws: WebSocket, loop: asyncio.AbstractEventLoop) -> None:
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
            await ws.send_json({"type": "alert", "changed": changed})


async def _regular_loop(ws: WebSocket, loop: asyncio.AbstractEventLoop) -> None:
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
            await ws.send_json({"type": "update", "changed": changed})
            last = dict(current)


@router.websocket("/ws")
async def ws_realtime(ws: WebSocket) -> None:
    """
    Push-notification WebSocket.  Accepts any connection; pushes category
    names only (no data payloads).  The frontend fetches actual data via REST.
    """
    await ws.accept()
    loop = asyncio.get_event_loop()

    try:
        # ── Initial snapshot: tell the client to refresh all categories once ──
        current = await loop.run_in_executor(None, lambda: _run_fingerprints(_REGULAR_QUERIES))
        all_keys = list(current.keys()) + list(_ALERT_DB_QUERIES.keys()) + ["infra_events"]
        await ws.send_json({"type": "update", "changed": all_keys})

        # ── Run both tiers concurrently until the client disconnects ──────────
        await asyncio.gather(
            _alert_loop(ws, loop),
            _regular_loop(ws, loop),
        )

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug("ws_realtime closed: %s", exc)
        try:
            await ws.close()
        except Exception:
            pass
