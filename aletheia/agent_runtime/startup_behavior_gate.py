"""Startup Behavior Gate — trust the process, not the uptime.

A gateway process can be "up" (port bound, dashboard reachable, data flowing) while
being in a state where it must NOT make new AI decisions or open new trades:
migrations half-applied, a corrupt DB, an execution mode that doesn't match the
active runtime scope, an incomplete exchange sync, an unexplained pending order/close
left over from a crash, an unresolved reconciliation integrity incident, a
misconfigured Learning influence surface, or a human pause / kill switch.

This gate makes the startup sequence honest:

    Gateway starts
    → API, Dashboard, essential data jobs available
    → AI decisions + new trading stay PAUSED
    → check DB, config, positions, orders, reconciliation, runtime status
    → checks pass  → resume AI decisions + paper trading
    → checks fail  → keep collecting data, but create no new trading behaviour

On failure the gate does NOT kill the gateway and does NOT hard-loop restarts.
It applies a LAYERED pause (essential jobs — market data, outcome settlement,
reconciliation — keep running; AI/trading jobs stop), records a DURABLE blocker
that survives restart, audits + notifies, surfaces the reason in Doctor/Health,
and periodically re-checks until the condition clears.

Design mirrors the existing LLM startup gate (agent_runtime/startup_gate.py) and
the exchange connectivity monitor (agent_runtime/connectivity.py): a pure diagnosis
function that never raises, plus an orchestration layer that owns the side effects.
The shared `runtime.paused` flag is coordinated via a `paused_by_gate` marker in the
durable blocker so a HUMAN pause is never auto-resumed by the gate (and vice versa).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("aletheia.startup_behavior_gate")

STATE_FILENAME = "startup_gate.json"

# Recognised paper execution modes. `paper_internal` is the declared active-scope
# mode; the others are supported demo adapters (see ACTIVE_RUNTIME_SCOPE.md). An
# execution mode outside this set is treated as config drift.
_KNOWN_EXECUTION_MODES = {"paper_internal", "bitget_demo", "okx_demo", "binance_testnet"}

_SCOPE_PATH = Path(__file__).resolve().parents[2] / "docs" / "product" / "ACTIVE_RUNTIME_SCOPE.md"

# Recheck loop task + persistent event loop captured at startup (mirrors the
# connectivity monitor's #145 handling so the resume task survives job loops).
_recheck_task = None  # asyncio.Task | concurrent.futures.Future | None
_uvicorn_loop: asyncio.AbstractEventLoop | None = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Durable blocker store — a small JSON file under data/gateway/ that survives
# restart. Read by the recheck loop, Doctor, and Health.
# --------------------------------------------------------------------------- #

def gate_state_path() -> Path:
    from aletheia.config.loader import get_data_dir

    return get_data_dir() / "gateway" / STATE_FILENAME


def read_gate_state() -> dict | None:
    p = gate_state_path()
    try:
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # a corrupt state file must not crash the gate
        logger.warning("startup gate: could not read durable state: %s", e)
        return None


def write_gate_state(state: dict) -> None:
    p = gate_state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(p)  # atomic on the same filesystem
    except Exception as e:
        logger.warning("startup gate: could not persist durable state: %s", e)


def clear_gate_state() -> None:
    p = gate_state_path()
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning("startup gate: could not clear durable state: %s", e)


def _gate_owns_pause() -> bool:
    state = read_gate_state()
    return bool(state and state.get("paused_by_gate"))


# --------------------------------------------------------------------------- #
# Individual checks. Each returns (ok: bool, detail: str) and NEVER raises — a
# probe that itself errors is reported as a blocking failure (fail-closed), since
# "we could not verify this" is not the same as "this is fine".
# --------------------------------------------------------------------------- #

def _check_migrations_applied(cfg) -> tuple[bool, str]:
    """Core tables created by run_migrations() must exist."""
    from aletheia.storage import sqlite as _sqlite

    required = {"ps_orders", "ps_positions", "ps_order_intents",
                "reconciliation_integrity_incidents"}
    try:
        rows = _sqlite.get_db().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r[0] for r in rows}
        missing = sorted(required - names)
        if missing:
            return False, f"missing tables: {', '.join(missing)}"
        return True, f"{len(names)} tables present"
    except Exception as e:
        return False, f"migration check failed: {e}"


def _check_sqlite_integrity(cfg) -> tuple[bool, str]:
    from aletheia.storage import sqlite as _sqlite

    try:
        row = _sqlite.get_db().execute("PRAGMA integrity_check").fetchone()
        result = (row[0] if row else "") or ""
        if result == "ok":
            return True, "ok"
        return False, f"integrity_check: {result}"
    except Exception as e:
        return False, f"integrity_check failed: {e}"


def _check_foreign_keys(cfg) -> tuple[bool, str]:
    from aletheia.storage import sqlite as _sqlite

    try:
        violations = _sqlite.get_db().execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            return False, f"{len(violations)} foreign-key violation(s)"
        return True, "no violations"
    except Exception as e:
        return False, f"foreign_key_check failed: {e}"


def _load_scope() -> dict | None:
    try:
        from aletheia.knowledge.frontmatter import parse_frontmatter

        metadata, _ = parse_frontmatter(_SCOPE_PATH)
        return metadata or None
    except Exception as e:
        logger.warning("startup gate: could not parse active runtime scope: %s", e)
        return None


def _check_active_runtime_scope(cfg) -> tuple[bool, str]:
    """The actual config must be consistent with the declared active runtime scope.

    We verify the scope document exists and is active, and that the configured
    paper execution mode is one the scope declares or an explicitly supported
    adapter — an unrecognised mode is config drift (a mismatch with the scope).
    Exchange-sync completeness for a demo adapter is checked separately.
    """
    scope = _load_scope()
    if scope is None:
        return False, "active runtime scope document missing/unparseable"
    if str(scope.get("status")) != "active":
        return False, f"active runtime scope status={scope.get('status')!r} (not active)"

    mode = (cfg.exchange.paper_execution_mode or "").strip()
    allowed = set(scope.get("execution_modes") or []) | _KNOWN_EXECUTION_MODES
    if mode not in allowed:
        return False, f"execution mode {mode!r} not in active scope {sorted(allowed)}"
    return True, f"config within scope {scope.get('scope_id')} (mode={mode})"


def _check_exchange_sync(cfg) -> tuple[bool, str]:
    """Exchange sync must be complete, OR we are explicitly running paper_internal."""
    try:
        from aletheia.portfolio.exchange_sync import active_broker_view_complete

        complete, reason = active_broker_view_complete(cfg)
        return bool(complete), reason
    except Exception as e:
        return False, f"exchange sync status check failed: {e}"


def _check_pending_orders(cfg, state_store) -> tuple[bool, str]:
    """No unexplained pending orders: no order intent stuck in created/submitted,
    and no order left in the reconciliation-anomalous 'unknown' status."""
    from aletheia.storage import sqlite as _sqlite

    try:
        unresolved = state_store.get_unresolved_intents()
        unknown = _sqlite.get_db().execute(
            "SELECT count(*) FROM ps_orders WHERE status='unknown'"
        ).fetchone()[0]
        problems = []
        if unresolved:
            problems.append(f"{len(unresolved)} unresolved order intent(s)")
        if unknown:
            problems.append(f"{unknown} order(s) in 'unknown' status")
        if problems:
            return False, "; ".join(problems)
        return True, "no pending orders"
    except Exception as e:
        return False, f"pending-order check failed: {e}"


def _check_pending_closes(cfg, state_store) -> tuple[bool, str]:
    """No unexplained pending closes: a position in 'unknown' status is one the
    reconciler could not confirm — a close left in limbo. Normal OPEN positions
    are expected during trading and do NOT block."""
    from aletheia.storage import sqlite as _sqlite

    try:
        unknown = _sqlite.get_db().execute(
            "SELECT count(*) FROM ps_positions WHERE status='unknown'"
        ).fetchone()[0]
        if unknown:
            return False, f"{unknown} position(s) in 'unknown' status (pending close)"
        return True, "no pending closes"
    except Exception as e:
        return False, f"pending-close check failed: {e}"


def _check_reconciliation_incident(cfg) -> tuple[bool, str]:
    try:
        from aletheia.portfolio.reconciliation_integrity import (
            get_active_reconciliation_integrity_incident,
        )

        incident = get_active_reconciliation_integrity_incident()
        if incident is not None:
            return False, (
                f"active reconciliation integrity incident {incident.get('id')} "
                f"(churn={incident.get('churn_count')})"
            )
        return True, "no active incident"
    except Exception as e:
        return False, f"reconciliation incident check failed: {e}"


def _check_learning_influence(cfg) -> tuple[bool, str]:
    """Learning influence / lifecycle automation / allowlist must be self-consistent.

    The active scope keeps behavioural influence disabled by default; enabling it
    is a deliberate override that is only valid with a non-empty allowlist and a
    bounded allocation. Contradictory settings (lifecycle automation on while
    influence is off, influence on with an empty allowlist, an allocation outside
    (0, 0.90]) indicate a misconfigured surface and must block the trading loop.
    """
    try:
        learning = cfg.learning
    except Exception as e:
        return False, f"learning config unavailable: {e}"

    if learning.lifecycle_automation_enabled and not learning.paper_influence_enabled:
        return False, "lifecycle_automation_enabled but paper_influence_enabled is off"
    if learning.paper_influence_enabled and not learning.paper_influence_paths:
        return False, "paper_influence_enabled but allowlist (paper_influence_paths) is empty"
    alloc = learning.paper_active_allocation_pct
    if not (0.0 < alloc <= 0.90):
        return False, f"paper_active_allocation_pct={alloc} outside (0, 0.90]"

    if not learning.paper_influence_enabled:
        return True, "influence disabled (scope default)"
    return True, f"influence enabled over {len(learning.paper_influence_paths)} path(s)"


# Ordered list of (name, callable) for the blocking system checks. `pending_orders`
# and `pending_closes` receive the state store; the rest receive only cfg.
def _run_checks(cfg, state_store) -> list[dict]:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "blocking": True})

    ok, detail = _check_migrations_applied(cfg); add("migrations_applied", ok, detail)
    ok, detail = _check_sqlite_integrity(cfg); add("sqlite_integrity", ok, detail)
    ok, detail = _check_foreign_keys(cfg); add("foreign_keys", ok, detail)
    ok, detail = _check_active_runtime_scope(cfg); add("active_runtime_scope", ok, detail)
    ok, detail = _check_exchange_sync(cfg); add("exchange_sync", ok, detail)
    ok, detail = _check_pending_orders(cfg, state_store); add("pending_orders", ok, detail)
    ok, detail = _check_pending_closes(cfg, state_store); add("pending_closes", ok, detail)
    ok, detail = _check_reconciliation_incident(cfg); add("reconciliation_incident", ok, detail)
    ok, detail = _check_learning_influence(cfg); add("learning_influence", ok, detail)
    return checks


def _user_block(cfg) -> str | None:
    """Return a reason string if the pause is HUMAN-owned (must not be auto-resumed).

    A kill switch is always human/system-owned. A `runtime.paused` flag with no
    gate-owned marker in the durable store is a human pause (the operator paused,
    or a different gate paused) — the startup gate must respect it and never clear
    it automatically.
    """
    if cfg.runtime.kill_switch_active:
        return "kill_switch_active"
    if cfg.runtime.paused and not _gate_owns_pause():
        return "manual_pause"
    return None


def evaluate_startup_gate(cfg=None, *, state_store=None) -> dict:
    """Pure diagnosis of whether the gateway may run its AI/trading loop.

    Never raises. Returns:
        {
          "passed": bool,                # all blocking system checks pass
          "checks": [{name, ok, detail, blocking}, ...],
          "blockers": [names of failing blocking checks],
          "user_blocked": bool,          # human pause / kill switch present
          "user_block_reason": str|None,
        }
    """
    if cfg is None:
        from aletheia.config.loader import load_config

        cfg = load_config()
    if state_store is None:
        from aletheia.gateway.state_store import GatewayStateStore

        state_store = GatewayStateStore()

    checks = _run_checks(cfg, state_store)
    blockers = [c["name"] for c in checks if c["blocking"] and not c["ok"]]
    user_block_reason = _user_block(cfg)
    return {
        "passed": len(blockers) == 0,
        "checks": checks,
        "blockers": blockers,
        "user_blocked": user_block_reason is not None,
        "user_block_reason": user_block_reason,
    }


def gate_allows_startup(cfg=None) -> bool:
    """Whether behaviour that must wait for the gate (e.g. the System Operator
    daemon) may start. True when the gate is disabled, or the checks pass and no
    human pause/kill switch is in effect."""
    if cfg is None:
        from aletheia.config.loader import load_config

        cfg = load_config()
    if not cfg.health.startup_behavior_gate_enabled:
        return True
    verdict = evaluate_startup_gate(cfg)
    return verdict["passed"] and not verdict["user_blocked"]


# --------------------------------------------------------------------------- #
# Side-effecting orchestration.
# --------------------------------------------------------------------------- #

def set_uvicorn_loop(loop) -> None:
    """Capture the persistent uvicorn loop so a resume triggered from the recheck
    loop can (re)start the System Operator daemon on a loop that survives."""
    global _uvicorn_loop
    _uvicorn_loop = loop


def _summary(verdict: dict) -> str:
    parts = [f"{c['name']}={c['detail']}" for c in verdict["checks"] if not c["ok"]]
    if verdict["user_block_reason"]:
        parts.append(f"user={verdict['user_block_reason']}")
    return "; ".join(parts) or "all checks clear"


def _audit(event_type: str, message: str) -> None:
    try:
        from aletheia.files.jsonl import write_audit

        write_audit({
            "actor": "system", "source": "startup_behavior_gate",
            "event_type": event_type, "entity_type": "gateway", "entity_id": "gateway",
            "message": message,
        })
    except Exception:
        pass


def _notify(title: str, message: str) -> None:
    try:
        from aletheia.notifications import notify_operator

        notify_operator(title, message, source="startup_behavior_gate",
                        event_type=None, audit=False, error_log=True)
    except Exception:
        pass


def _persist_paused(paused: bool) -> None:
    """Read-modify-write a FRESH on-disk config so we only flip `paused` and never
    clobber a concurrent settings edit (same rationale as the LLM startup gate)."""
    from aletheia.config.loader import load_config, save_config

    fresh = load_config()
    fresh.runtime.paused = paused
    save_config(fresh)


def _apply_block(verdict: dict, cfg) -> None:
    """Enter the layered pause + durable blocker state for a failing gate.

    Only a SYSTEM failure makes the pause gate-owned (auto-resumable). A pure
    human block (checks pass but paused/kill switch) is recorded for visibility
    but leaves the existing pause exactly as the human set it.
    """
    system_failed = not verdict["passed"]
    paused_by_gate = system_failed

    if system_failed:
        try:
            from aletheia.agent_runtime.scheduler import pause_scheduler

            pause_scheduler(layered=True)  # essential jobs keep running
        except Exception as e:
            logger.warning("startup gate: pause_scheduler failed: %s", e)
        _persist_paused(True)

    write_gate_state({
        "paused_by_gate": paused_by_gate,
        "blockers": verdict["blockers"],
        "user_block_reason": verdict["user_block_reason"],
        "checks": verdict["checks"],
        "updated_at": _utcnow_iso(),
    })

    summary = _summary(verdict)
    _audit("startup_behavior_gate_paused",
           f"Startup behaviour gate blocked (AI/trading paused, data continues): {summary}")
    logger.warning("Startup behaviour gate BLOCKED — AI/trading paused: %s", summary)
    _notify("Aletheia Startup Gate — trading paused",
            f"Gateway is collecting data but not trading. Blockers: {summary}")

    if paused_by_gate:
        _start_recheck_loop(cfg.health.startup_gate_recheck_interval_sec)


def _maybe_start_pi_daemon() -> None:
    """Best-effort (re)start of the System Operator daemon after a gate resume.

    Only meaningful when a persistent loop was captured (production). No-op in
    tests / when the daemon is disabled or already running."""
    try:
        from aletheia.config.loader import load_config

        cfg = load_config()
        if not cfg.pi_daemon.enabled:
            return
        from aletheia.agent_runtime.pi_daemon import is_daemon_running, start_pi_daemon

        if is_daemon_running():
            return
        loop = _uvicorn_loop
        if loop is None or not getattr(loop, "is_running", lambda: False)():
            return

        def _start():
            start_pi_daemon(
                loop=loop,
                debounce_sec=cfg.pi_daemon.debounce_sec,
                cooldown_sec=cfg.pi_daemon.cooldown_sec,
                heartbeat_sec=cfg.pi_daemon.heartbeat_sec,
            )

        loop.call_soon_threadsafe(_start)
    except Exception as e:
        logger.debug("startup gate: pi_daemon (re)start skipped: %s", e)


def _resume_from_gate(cause: str) -> None:
    _persist_paused(False)
    try:
        from aletheia.agent_runtime.scheduler import resume_scheduler

        resume_scheduler()
    except Exception as e:
        logger.warning("startup gate: resume_scheduler failed: %s", e)
    _audit("startup_behavior_gate_resumed",
           f"Startup behaviour gate cleared — AI/trading resumed ({cause})")
    logger.info("Startup behaviour gate CLEARED — AI/trading resumed (%s)", cause)
    _notify("Aletheia Startup Gate — trading resumed",
            "Startup checks passed; AI decisions and paper trading resumed.")
    _maybe_start_pi_daemon()


async def startup_behavior_gate(cfg=None) -> dict:
    """Lifespan entry point (run as a post-yield background task).

    Evaluates the gate and applies the verdict: on failure, layered pause +
    durable blocker + recheck loop; on pass, clear any gate-owned pause and
    resume. Never raises."""
    if cfg is None:
        from aletheia.config.loader import load_config

        cfg = load_config()
    if not cfg.health.startup_behavior_gate_enabled:
        return {"skipped": True}

    try:
        verdict = evaluate_startup_gate(cfg)
    except Exception as e:  # defensive: diagnosis is meant to be pure, but never crash startup
        logger.warning("startup behaviour gate evaluation errored: %s", e)
        return {"skipped": True, "error": str(e)}

    if verdict["passed"] and not verdict["user_blocked"]:
        if _gate_owns_pause():
            _resume_from_gate(cause="startup_checks_clear")
        clear_gate_state()
        _audit("startup_behavior_gate_passed", "Startup behaviour gate passed at boot")
        logger.info("Startup behaviour gate passed — AI/trading permitted")
        return verdict

    _apply_block(verdict, cfg)
    return verdict


async def recheck_once() -> bool:
    """Run one recheck tick. Returns True iff it resumed the trading loop.

    Only resumes a GATE-OWNED pause; a human pause / kill switch is left in place.
    """
    from aletheia.config.loader import load_config

    cfg = load_config()
    if not cfg.runtime.paused:
        # Already resumed by other means — nothing gate-owned to hold.
        if _gate_owns_pause():
            clear_gate_state()
        return False
    if not _gate_owns_pause():
        return False  # human-owned pause — never auto-resume

    verdict = evaluate_startup_gate(cfg)
    if verdict["passed"] and not verdict["user_blocked"]:
        _resume_from_gate(cause="recheck_clear")
        clear_gate_state()
        return True

    # Still blocked — refresh the durable blocker so surfaces stay current.
    write_gate_state({
        "paused_by_gate": True,
        "blockers": verdict["blockers"],
        "user_block_reason": verdict["user_block_reason"],
        "checks": verdict["checks"],
        "updated_at": _utcnow_iso(),
    })
    return False


async def _recheck_loop(interval_sec: int) -> None:
    while True:
        await asyncio.sleep(max(5, int(interval_sec)))
        try:
            if await recheck_once():
                return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("startup gate recheck tick failed: %s", e)


def _start_recheck_loop(interval_sec: int) -> None:
    """Schedule the recheck loop on a surviving event loop (mirrors connectivity #145)."""
    global _recheck_task
    if _recheck_task is not None and not _recheck_task.done():
        return  # already running
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    target = _uvicorn_loop if (_uvicorn_loop is not None and _uvicorn_loop is not running
                               and getattr(_uvicorn_loop, "is_running", lambda: False)()) else running
    if target is None:
        logger.error("startup gate: no event loop available to schedule recheck loop")
        return
    if target is running:
        _recheck_task = target.create_task(_recheck_loop(interval_sec), name="startup_gate_recheck")
    else:
        _recheck_task = asyncio.run_coroutine_threadsafe(_recheck_loop(interval_sec), target)


async def stop_startup_gate_recheck() -> None:
    """Cancel the recheck loop (lifespan shutdown)."""
    global _recheck_task, _uvicorn_loop
    task = _recheck_task
    _recheck_task = None
    _uvicorn_loop = None
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        if isinstance(task, asyncio.Future) and not isinstance(task, asyncio.Task):
            await asyncio.wrap_future(task)
        else:
            await task
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        pass
    except Exception as e:
        logger.debug("startup gate recheck shutdown: %s", e)
