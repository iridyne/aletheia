"""Read-only answers to "can an entry still get through, and is anything stuck?".

Fourteen days passed on the Fedora Bitget Demo runtime with 501 Entry Plans and no
fill before anyone knew, because no surface asked these questions: a state that
should have ended long ago, a gate that refuses every entry, retries with no pace,
and a funnel that produces nothing. Nothing here writes or places an order.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aletheia.execution.stale_order_cleanup import _STALE_ORDER_SOFT_HOURS, _age_hours
from aletheia.storage.order_states import NON_TERMINAL_ORDER_STATES
from aletheia.storage.sqlite import get_db
from aletheia.uta.identity import IdentityRelation, identity_relation
from aletheia.uta.models import UTAAccountIdentity

# Stale cleanup runs daily; an Order is overdue only after it has had its turn.
OVERDUE_HOURS = _STALE_ORDER_SOFT_HOURS + 24
RETRY_STORM_ERROR_ORDERS_PER_HOUR = 20
FUNNEL_WINDOW_DAYS = 7
FUNNEL_MIN_PLANS = 20


def overdue_orders() -> list[dict]:
    placeholders = ",".join("?" for _ in NON_TERMINAL_ORDER_STATES)
    rows = get_db().execute(
        f"SELECT id, status, execution_mode, created_at FROM orders WHERE status IN ({placeholders}) "
        "ORDER BY created_at ASC",
        NON_TERMINAL_ORDER_STATES,
    ).fetchall()
    overdue = []
    for row in rows:
        age = _age_hours(row["created_at"])
        if age is not None and age >= OVERDUE_HOURS:
            overdue.append({
                "id": row["id"], "status": row["status"],
                "execution_mode": row["execution_mode"], "age_hours": round(age, 1),
            })
    return overdue


def identity_blocked_modes() -> list[str]:
    """Modes where a non-terminal Order's stored account is not the configured one.

    That is the exact condition under which the entry gate refuses a new entry
    (§3.0b); it clears only when the Order is re-anchored or ended.
    """
    from aletheia.uta.manager import build_uta_registry

    configured = {i.execution_mode: i for i in build_uta_registry().configured_identities()}
    placeholders = ",".join("?" for _ in NON_TERMINAL_ORDER_STATES)
    blocked: set[str] = set()
    for row in get_db().execute(
        f"SELECT execution_mode, uta_account_identity_json FROM orders WHERE status IN ({placeholders})",
        NON_TERMINAL_ORDER_STATES,
    ).fetchall():
        current = configured.get(row["execution_mode"])
        if current is None:
            continue  # not configured is not "blocked"
        try:
            stored = UTAAccountIdentity.model_validate_json(row["uta_account_identity_json"] or "")
        except (ValueError, TypeError):
            blocked.add(row["execution_mode"])
            continue
        if identity_relation(stored, current) is not IdentityRelation.SAME:
            blocked.add(row["execution_mode"])
    return sorted(blocked)


def retry_storm_proposals() -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    rows = get_db().execute(
        "SELECT proposal_id, COUNT(*) AS n FROM orders WHERE status='error' AND created_at>=? "
        "AND proposal_id IS NOT NULL GROUP BY proposal_id HAVING n>? ORDER BY n DESC",
        (since, RETRY_STORM_ERROR_ORDERS_PER_HOUR),
    ).fetchall()
    return [{"proposal_id": r["proposal_id"], "error_orders_last_hour": r["n"]} for r in rows]


def entry_funnel() -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=FUNNEL_WINDOW_DAYS)).isoformat()
    row = get_db().execute(
        "SELECT COUNT(*) AS plans, "
        "SUM(CASE WHEN order_id IS NOT NULL OR status IN ('ordered','partially_filled','filled') THEN 1 ELSE 0 END) AS ordered, "
        "SUM(CASE WHEN status='filled' THEN 1 ELSE 0 END) AS filled "
        "FROM entry_plans WHERE created_at>=?",
        (since,),
    ).fetchone()
    return {
        "window_days": FUNNEL_WINDOW_DAYS,
        "plans": int(row["plans"] or 0),
        "ordered": int(row["ordered"] or 0),
        "filled": int(row["filled"] or 0),
    }


def funnel_is_dead(funnel: dict) -> bool:
    return funnel["plans"] >= FUNNEL_MIN_PLANS and funnel["filled"] == 0
