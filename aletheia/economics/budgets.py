"""Budget declaration writers + would-block observation calculator.

Phase 2B-1 (see docs/design/proposals/tokenomics_harness_phase_plan_2026-07-30.md
§4 Phase 2 and docs/research/tokenomics/phase2_design_decisions_2026-07-31.md).

This module ships:

- `add_budget()`, `list_budgets()`, `set_budget_enabled()` — CLI-fed
  writers matching the existing `add_pricing_snapshot` shape.
- `check_would_block()` — pure function computing which declared
  budgets a hypothetical additional cost would breach. No side
  effects. The hook that will call it lives in Phase 2B-2.

No behaviour change to any LLM caller until Phase 2B-2 wires
`check_would_block` into `complete_with_fallback` and gates it on
`EconomicsConfig.budgets_shadow_enabled`.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from aletheia.economics.schema import ensure_economics_schema


_VALID_SCOPES = ("system", "agent_run", "logical_agent", "provider", "provider_tier")
_VALID_WINDOWS = ("daily", "weekly", "session")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _budget_id(scope: str, subject: str | None, window: str) -> str:
    """Deterministic id keyed on the UNIQUE(scope, subject, window) triple."""
    payload = f"{scope}|{subject or ''}|{window}"
    return "budget_" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def add_budget(
    *,
    scope: str,
    subject: str | None,
    usd_cap: float,
    window: str,
    enabled: bool = True,
    source: str = "cli:budget-set",
) -> dict[str, Any]:
    """Declare a new budget policy or refresh the timestamp on an existing one.

    Idempotent on (scope, subject, window): calling with the same triple
    returns the existing row unchanged. To change `usd_cap` or `enabled`
    on an existing policy, use `set_budget_enabled` for the enable/disable
    axis; changing the cap requires deleting and re-adding (kept simple
    on purpose so budget history is auditable).

    `subject` may be None only when `scope == 'system'`.
    """
    scope = scope.strip()
    window = window.strip()
    if scope not in _VALID_SCOPES:
        raise ValueError(f"scope must be one of {_VALID_SCOPES}")
    if window not in _VALID_WINDOWS:
        raise ValueError(f"window must be one of {_VALID_WINDOWS}")
    if scope != "system" and not (subject and subject.strip()):
        raise ValueError(f"subject is required for scope={scope!r}")
    if scope == "system":
        subject = None
    else:
        subject = subject.strip() if subject else None
    try:
        cap = float(usd_cap)
    except (TypeError, ValueError) as exc:
        raise ValueError("usd_cap must be numeric") from exc
    if not (cap > 0):
        raise ValueError("usd_cap must be positive")

    budget_id = _budget_id(scope, subject, window)
    now = _utcnow()
    db = ensure_economics_schema()
    db.execute(
        """INSERT OR IGNORE INTO economics_budgets (
            id, scope, subject, usd_cap, window, enabled, source, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?)""",
        (budget_id, scope, subject, cap, window, 1 if enabled else 0, source, now, now),
    )
    row = db.execute(
        "SELECT * FROM economics_budgets WHERE id=?", (budget_id,)
    ).fetchone()
    return dict(row) if row else {}


def list_budgets(
    *, scope: str | None = None, enabled_only: bool = False
) -> list[dict[str, Any]]:
    """Return declared budget policies, optionally filtered by scope."""
    db = ensure_economics_schema()
    where: list[str] = []
    params: list[Any] = []
    if scope:
        if scope not in _VALID_SCOPES:
            raise ValueError(f"scope must be one of {_VALID_SCOPES}")
        where.append("scope=?")
        params.append(scope)
    if enabled_only:
        where.append("enabled=1")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.execute(
        f"SELECT * FROM economics_budgets {where_sql} "
        f"ORDER BY scope, subject IS NULL, subject, window",
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def remove_budget(*, budget_id: str) -> dict[str, Any] | None:
    """Hard-delete a budget policy.

    Returns the deleted row (as it was before deletion) or None if unknown.
    Budgets are policy, not accounting facts — deletion is safe. Historic
    would-block observations already in `runtime_cost_events.metadata_json`
    reference the policy_id in-place and remain intact; they are not
    rewritten when the policy row disappears.
    """
    db = ensure_economics_schema()
    row = db.execute(
        "SELECT * FROM economics_budgets WHERE id=?", (budget_id,)
    ).fetchone()
    if row is None:
        return None
    deleted = dict(row)
    db.execute("DELETE FROM economics_budgets WHERE id=?", (budget_id,))
    return deleted


def set_budget_enabled(*, budget_id: str, enabled: bool) -> dict[str, Any] | None:
    """Toggle a policy on or off. Returns the updated row, or None if unknown id."""
    db = ensure_economics_schema()
    now = _utcnow()
    cursor = db.execute(
        "UPDATE economics_budgets SET enabled=?, updated_at=? WHERE id=?",
        (1 if enabled else 0, now, budget_id),
    )
    if cursor.rowcount == 0:
        return None
    row = db.execute(
        "SELECT * FROM economics_budgets WHERE id=?", (budget_id,)
    ).fetchone()
    return dict(row) if row else None


# ── Observation calculator (pure) ────────────────────────────────────────────


def _window_start(window: str, now: datetime) -> datetime | None:
    """Resolve the inclusive start-of-window datetime for spend accumulation.

    Returns None for `session` when no runtime session is active — the
    caller treats that as "cannot compute" and skips the policy.
    """
    if window == "daily":
        return now - timedelta(days=1)
    if window == "weekly":
        return now - timedelta(days=7)
    if window == "session":
        try:
            from aletheia.agent_runtime.runtime_session import current_session
        except Exception:
            return None
        session = current_session()
        started = session.get("started_at") if session else None
        if not started:
            return None
        try:
            parsed = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _spent_in_window(
    db, *, scope: str, subject: str | None, window_start: datetime, now: datetime
) -> float:
    """Sum known cost usd for rows matching the scope filter within the window."""
    where = [
        "cost_status != 'unknown'",
        "estimated_cost_usd IS NOT NULL",
        "started_at >= ?",
        "started_at <= ?",
    ]
    params: list[Any] = [window_start.isoformat(), now.isoformat()]
    if scope == "system":
        pass  # no additional filter
    elif scope == "agent_run":
        where.append("run_id=?")
        params.append(subject)
    elif scope == "logical_agent":
        where.append(
            "(json_valid(metadata_json) AND "
            "json_extract(metadata_json,'$.logical_agent')=?)"
        )
        params.append(subject)
    elif scope == "provider":
        where.append("provider=?")
        params.append(subject)
    elif scope == "provider_tier":
        # provider_tier maps to a set of provider names via config; the
        # observation calculator receives the caller's resolved tier via
        # the `tier` kwarg on check_would_block and translates it here.
        where.append("provider=?")
        params.append(subject)
    row = db.execute(
        f"SELECT COALESCE(SUM(estimated_cost_usd),0.0) FROM runtime_cost_events "
        f"WHERE {' AND '.join(where)}",
        tuple(params),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def check_would_block(
    *,
    estimated_cost_usd: float,
    run_id: str | None = None,
    logical_agent: str | None = None,
    provider: str | None = None,
    tier: str | None = None,
    at: str | None = None,
) -> list[dict[str, Any]]:
    """Return would-block observations for the given hypothetical call.

    A pure read: no rows are written to any table. The caller (Phase 2B-2's
    hook inside `complete_with_fallback`) serialises the returned list
    into `runtime_cost_events.metadata_json.$.would_block` when the call
    completes.

    Callers should pass whichever identity fields are available:

    - `run_id`: enclosing AgentRun id.
    - `logical_agent`: daemon logical identity (e.g. "pi_trading_decision").
    - `provider`: exact provider name as it appears on ledger rows.
    - `tier`: "deep" / "fast" / "default"; resolved to a provider name
      via `router.get_provider_for_tier()` inside this function.

    Returns a list of dicts, one per breached budget:

        {
            "policy_id": "budget_...",
            "scope": "logical_agent",
            "subject": "pi_trading_decision",
            "window": "daily",
            "usd_cap": 10.0,
            "spent_before_call_usd": 9.8,
            "projected_after_call_usd": 10.05,
            "over_by_usd": 0.05,
        }

    Order is stable: sorted by policy_id.
    """
    try:
        cost = float(estimated_cost_usd)
    except (TypeError, ValueError):
        return []
    if not (cost > 0):
        return []

    now = (
        datetime.fromisoformat(str(at).replace("Z", "+00:00")).astimezone(timezone.utc)
        if at
        else datetime.now(timezone.utc)
    )

    resolved_tier_provider: str | None = None
    if tier:
        try:
            from aletheia.model_provider.router import get_provider_for_tier
            resolved_tier_provider = get_provider_for_tier(tier)
        except Exception:
            resolved_tier_provider = None

    db = ensure_economics_schema()
    budgets = list_budgets(enabled_only=True)
    observations: list[dict[str, Any]] = []
    for budget in budgets:
        scope = budget["scope"]
        subject = budget["subject"]
        window = budget["window"]

        applies = False
        matched_subject: str | None = subject
        if scope == "system":
            applies = True
            matched_subject = None
        elif scope == "agent_run" and run_id and subject == run_id:
            applies = True
        elif scope == "logical_agent" and logical_agent and subject == logical_agent:
            applies = True
        elif scope == "provider" and provider and subject == provider:
            applies = True
        elif scope == "provider_tier" and resolved_tier_provider:
            # provider_tier budgets carry the tier label in `subject`; resolve
            # against the caller's tier if provided.
            if subject == tier or subject == resolved_tier_provider:
                applies = True
                matched_subject = resolved_tier_provider

        if not applies:
            continue

        window_start = _window_start(window, now)
        if window_start is None:
            # `session` window with no active runtime session — cannot compute.
            continue

        spent = _spent_in_window(
            db,
            scope=scope,
            subject=matched_subject,
            window_start=window_start,
            now=now,
        )
        projected = spent + cost
        if projected > float(budget["usd_cap"]):
            observations.append(
                {
                    "policy_id": budget["id"],
                    "scope": scope,
                    "subject": matched_subject,
                    "window": window,
                    "usd_cap": float(budget["usd_cap"]),
                    "spent_before_call_usd": round(spent, 12),
                    "projected_after_call_usd": round(projected, 12),
                    "over_by_usd": round(projected - float(budget["usd_cap"]), 12),
                }
            )

    observations.sort(key=lambda item: item["policy_id"])
    return observations


def list_recent_would_block_events(
    *,
    days: int = 7,
    limit: int = 200,
    scope: str | None = None,
) -> list[dict[str, Any]]:
    """Query `runtime_cost_events` for rows whose metadata carries a `would_block` key.

    Returned rows include the base ledger fields plus a parsed `would_block`
    list. Used by the read-only Settings surface and by the
    `/api/economics/budgets/would-block` endpoint.

    `scope` filters to events where at least one observation targets the
    given scope. Filtering is done in Python (SQLite's JSON1 predicates
    against nested arrays require complex WHERE clauses; a small per-page
    filter here is simpler and correct).
    """
    ensure_economics_schema()
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=max(1, days))
    from aletheia.economics.schema import ensure_economics_schema as _ensure

    db = _ensure()
    rows = db.execute(
        """SELECT * FROM runtime_cost_events
           WHERE started_at >= ?
             AND json_valid(metadata_json)
             AND json_extract(metadata_json,'$.would_block') IS NOT NULL
           ORDER BY started_at DESC
           LIMIT ?""",
        (start.isoformat(), max(1, min(int(limit), 5_000))),
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        try:
            import json as _json

            metadata = _json.loads(entry.get("metadata_json") or "{}")
            observations = metadata.get("would_block") or []
        except Exception:
            observations = []
        if scope and not any(obs.get("scope") == scope for obs in observations):
            continue
        entry["would_block"] = observations
        results.append(entry)
    return results


def budgets_applicable_to(
    *,
    run_id: str | None = None,
    logical_agent: str | None = None,
    provider: str | None = None,
) -> Iterable[dict[str, Any]]:
    """Convenience view: which enabled budgets currently target this identity.

    Not consulted by the observation calculator; exposed for UI listing so
    the Settings tile can show "budgets that apply to this run" without
    duplicating the scope-matching logic.
    """
    for budget in list_budgets(enabled_only=True):
        scope = budget["scope"]
        subject = budget["subject"]
        if scope == "system":
            yield budget
        elif scope == "agent_run" and run_id and subject == run_id:
            yield budget
        elif scope == "logical_agent" and logical_agent and subject == logical_agent:
            yield budget
        elif scope == "provider" and provider and subject == provider:
            yield budget
