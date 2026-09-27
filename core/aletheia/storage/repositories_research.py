"""Research, Serenity, knowledge and agent-run persistence.

Shared helpers are imported inside functions to preserve both import orders
and the facade's transaction, connection and monkeypatch bindings.
"""

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import cast

from aletheia.storage.models import AgentRunArtifact, AgentRunStatus, PSLesson, SerenityTheme

__all__ = [
    "acknowledge_serenity_research_run_cancel",
    "acquire_serenity_lock",
    "begin_agent_run",
    "claim_serenity_research_run",
    "expire_stale_serenity_themes",
    "fail_unclaimed_serenity_research_run",
    "finish_agent_run",
    "finish_serenity_research_run_with_event",
    "get_active_serenity_research_run",
    "get_agent_run",
    "get_knowledge_item",
    "get_latest_successful_serenity_research_run",
    "get_serenity_opportunity",
    "get_serenity_profile_snapshot",
    "get_serenity_research_retry",
    "get_serenity_research_run",
    "get_serenity_research_run_by_idempotency_key",
    "get_serenity_theme",
    "get_serenity_user_profile",
    "insert_agent_run_artifacts",
    "insert_serenity_research_retry",
    "insert_serenity_theme_claims_bulk",
    "insert_serenity_theme_with_claim_links",
    "knowledge_hash_exists",
    "knowledge_title_exists_within",
    "list_active_serenity_research_runs",
    "list_agent_runs",
    "list_agent_runs_window",
    "list_knowledge_items",
    "list_latest_agent_runs",
    "list_lessons",
    "list_pending_trade_case_studies",
    "list_serenity_claims",
    "list_serenity_claims_for_theme",
    "list_serenity_evidence",
    "list_serenity_feedback_events",
    "list_serenity_opportunities",
    "list_serenity_post_mortems",
    "list_serenity_replay_snapshots",
    "list_serenity_research_runs",
    "list_serenity_risk_gates",
    "list_serenity_run_events",
    "list_serenity_themes",
    "mark_agent_run_stale",
    "pending_trade_case_studies_summary",
    "prune_agent_runs",
    "prune_controller_recommendations",
    "prune_run_steps",
    "prune_serenity_claims",
    "prune_tool_calls",
    "release_serenity_lock",
    "request_serenity_research_run_cancel",
    "transition_active_serenity_opportunities_for_theme",
    "transition_serenity_theme_status",
]


def list_lessons(status: str | None = None, limit: int = 100, *, oldest_first: bool = False) -> list[PSLesson]:
    """Read persisted rules independently of the optional lesson authoring surface."""
    from aletheia.storage.repositories import get_db

    db = get_db()
    direction = "ASC" if oldest_first else "DESC"
    if status:
        rows = db.execute(
            f"SELECT * FROM ps_lessons WHERE status = ? ORDER BY created_at {direction} LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = db.execute(
            f"SELECT * FROM ps_lessons ORDER BY created_at {direction} LIMIT ?", (limit,)
        ).fetchall()
    return [PSLesson(**dict(row)) for row in rows]


def insert_agent_run_artifacts(artifacts: list[AgentRunArtifact]) -> None:
    """Persist current-run artifact references without rewriting legacy outputs."""
    from aletheia.storage.repositories import _durable_agent_artifact_id, execute_many_write

    if not artifacts:
        return
    rows = [
        (
            artifact.run_id,
            artifact.artifact_type,
            _durable_agent_artifact_id(artifact.artifact_type, artifact.artifact_id),
        )
        for artifact in artifacts
    ]
    execute_many_write(
        "INSERT OR IGNORE INTO agent_run_artifacts "
        "(run_id, artifact_type, artifact_id, reference_version) VALUES (?, ?, ?, 'v2')",
        rows,
    )


def _durable_agent_artifact_id(artifact_type: str, artifact_id: str) -> str:
    """Promote a late-written pending PI artifact to its one durable final row."""
    from aletheia.storage.repositories import get_db

    if artifact_type != "pi_decision":
        return artifact_id
    rows = (
        get_db()
        .execute(
            "SELECT final.id FROM pi_decisions pending "
            "JOIN pi_decisions final ON final.opportunity_id=pending.opportunity_id "
            " AND COALESCE(final.signal_source, 'market_observer')="
            "     COALESCE(pending.signal_source, 'market_observer') "
            " AND final.action IN ('trade','scale_in','flip','skip','watch') "
            " AND (julianday(final.created_at)>julianday(pending.created_at) "
            "      OR (julianday(final.created_at)=julianday(pending.created_at) "
            "          AND final.rowid>pending.rowid)) "
            "WHERE pending.id=? AND pending.action='signal_pending' "
            "AND NOT EXISTS ("
            " SELECT 1 FROM pi_decisions newer_pending "
            " WHERE newer_pending.opportunity_id=pending.opportunity_id "
            " AND COALESCE(newer_pending.signal_source, 'market_observer')="
            "     COALESCE(pending.signal_source, 'market_observer') "
            " AND newer_pending.action='signal_pending' "
            " AND (julianday(newer_pending.created_at)>julianday(pending.created_at) "
            "      OR (julianday(newer_pending.created_at)=julianday(pending.created_at) "
            "          AND newer_pending.rowid>pending.rowid)) "
            " AND (julianday(newer_pending.created_at)<julianday(final.created_at) "
            "      OR (julianday(newer_pending.created_at)=julianday(final.created_at) "
            "          AND newer_pending.rowid<final.rowid))"
            ") ORDER BY julianday(final.created_at), final.rowid LIMIT 2",
            (artifact_id,),
        )
        .fetchall()
    )
    return str(rows[0][0]) if len(rows) == 1 else artifact_id


def begin_agent_run(
    run_id: str,
    *,
    permission_mode: str,
    model_provider: str | None,
) -> bool:
    from aletheia.storage.repositories import execute_write

    result = execute_write(
        "UPDATE agent_runs SET status='running', permission_mode=?, model_provider=? "
        "WHERE id=? AND status='queued'",
        (permission_mode, model_provider, run_id),
    )
    return result.rowcount == 1


def finish_agent_run(
    run_id: str,
    *,
    expected_status: AgentRunStatus,
    status: AgentRunStatus,
    ended_at: str,
    outputs_created: str,
    errors: str,
    tools_called: str,
    cost_estimate: float | None,
    model_name: str | None = None,
) -> bool:
    from aletheia.storage.repositories import (
        _AGENT_RUN_TERMINAL_STATUSES,
        _AGENT_RUN_TERMINAL_TRANSITIONS,
        execute_write,
    )

    if status not in _AGENT_RUN_TERMINAL_STATUSES:
        raise ValueError("invalid_agent_run_terminal_status")
    if status not in _AGENT_RUN_TERMINAL_TRANSITIONS.get(expected_status, ()):
        raise ValueError("invalid_agent_run_transition")
    result = execute_write(
        "UPDATE agent_runs SET status=?, ended_at=?, outputs_created=?, errors=?, "
        "tools_called=?, cost_estimate=?, model_name=COALESCE(?, model_name) "
        "WHERE id=? AND status=?",
        (
            status,
            ended_at,
            outputs_created,
            errors,
            tools_called,
            cost_estimate,
            model_name,
            run_id,
            expected_status,
        ),
    )
    return result.rowcount == 1


def mark_agent_run_stale(run_id: str, *, ended_at: str, errors: str) -> bool:
    from aletheia.storage.repositories import execute_write

    result = execute_write(
        "UPDATE agent_runs SET status='stale', ended_at=?, errors=? "
        "WHERE id=? AND status IN ('queued','running')",
        (ended_at, errors, run_id),
    )
    return result.rowcount == 1


def list_agent_runs(limit: int = 50, *, days: int | None = None) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    if days is None:
        return _select_all("agent_runs", limit=limit)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _select_all(
        "agent_runs",
        where="replace(started_at,' ','T') >= replace(?,' ','T')",
        params=(cutoff,),
        order="replace(started_at,' ','T') DESC",
        limit=limit,
    )


def list_agent_runs_window(*, hours: int) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return _select_all(
        "agent_runs",
        where=(
            "replace(COALESCE(NULLIF(ended_at,''),started_at),' ','T') "
            ">= replace(?,' ','T')"
        ),
        params=(cutoff,),
        order=(
            "replace(COALESCE(NULLIF(ended_at,''),started_at),' ','T') DESC, "
            "rowid DESC"
        ),
        limit=None,
    )


def list_latest_agent_runs() -> list[dict]:
    from aletheia.storage.repositories import get_db

    rows = get_db().execute(
        """
        WITH ranked AS (
            SELECT agent_runs.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY agent_name
                       ORDER BY replace(COALESCE(NULLIF(ended_at,''),started_at),' ','T') DESC,
                                rowid DESC
                   ) AS _rank
            FROM agent_runs
        )
        SELECT * FROM ranked WHERE _rank = 1
        ORDER BY replace(COALESCE(NULLIF(ended_at,''),started_at),' ','T') DESC, id DESC
        """
    ).fetchall()
    result = [dict(row) for row in rows]
    for row in result:
        row.pop("_rank", None)
    return result


def get_agent_run(run_id: str) -> dict | None:
    from aletheia.storage.repositories import _select_one

    return _select_one("agent_runs", run_id)


def get_knowledge_item(kid: str) -> dict | None:
    from aletheia.storage.repositories import _select_one

    return _select_one("knowledge_items", kid)


def list_knowledge_items(
    item_type: str | None = None, limit: int = 50, strategy_id: str | None = None, status: str = "active"
) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    conditions = []
    params_list: list = []
    if item_type:
        conditions.append("item_type = ?")
        params_list.append(item_type)
    if strategy_id is not None:
        conditions.append("strategy_id = ?")
        params_list.append(strategy_id)
    if status != "all":
        conditions.append("(status = ? OR status IS NULL)")
        params_list.append(status)
    where = " AND ".join(conditions) if conditions else None
    return _select_all("knowledge_items", where=where, params=tuple(params_list), limit=limit)


def list_pending_trade_case_studies(
    limit: int = 10,
    *,
    after_cursor: str | None = None,
    before_cursor: str | None = None,
) -> list[dict]:
    """Return oldest active trade cases without active lesson evidence."""
    from aletheia.storage.repositories import get_db


    bounded_limit = max(1, min(int(limit), 100))
    where = """
        ki.item_type = 'trade_case_study'
        AND (ki.status = 'active' OR ki.status IS NULL)
        AND NOT EXISTS (
            SELECT 1
            FROM ps_lessons AS pl
            WHERE pl.source_type = 'trade_case_study'
              AND (pl.status IS NULL OR pl.status IN ('proposed', 'accepted', 'applied'))
              AND (
                  pl.source_id = ki.id
                  OR instr(pl.evidence_json, '"' || ki.id || '"') > 0
              )
        )
    """
    params: list[str | int] = []
    if after_cursor:
        created_at, separator, case_id = str(after_cursor).partition("|")
        if separator and created_at and case_id:
            where += """
                AND (
                    ki.created_at > ?
                    OR (ki.created_at = ? AND ki.id > ?)
                )
            """
            params.extend((created_at, created_at, case_id))
    if before_cursor:
        created_at, separator, case_id = str(before_cursor).partition("|")
        if separator and created_at and case_id:
            where += """
                AND (
                    ki.created_at < ?
                    OR (ki.created_at = ? AND ki.id < ?)
                )
            """
            params.extend((created_at, created_at, case_id))
    rows = (
        get_db()
        .execute(
            f"""
        SELECT ki.*
        FROM knowledge_items AS ki
        WHERE {where}
        ORDER BY ki.created_at ASC, ki.id ASC
        LIMIT ?
        """,
            (*params, bounded_limit),
        )
        .fetchall()
    )
    return [dict(row) for row in rows]


def pending_trade_case_studies_summary() -> tuple[int, str | None]:
    """Return pending case count and oldest creation time for job health."""
    from aletheia.storage.repositories import get_db


    row = (
        get_db()
        .execute(
            """
        SELECT COUNT(*) AS pending_count, MIN(ki.created_at) AS oldest_created_at
        FROM knowledge_items AS ki
        WHERE ki.item_type = 'trade_case_study'
          AND (ki.status = 'active' OR ki.status IS NULL)
          AND NOT EXISTS (
              SELECT 1
              FROM ps_lessons AS pl
              WHERE pl.source_type = 'trade_case_study'
                AND (pl.status IS NULL OR pl.status IN ('proposed', 'accepted', 'applied'))
                AND (
                    pl.source_id = ki.id
                    OR instr(pl.evidence_json, '"' || ki.id || '"') > 0
                )
          )
        """
        )
        .fetchone()
    )
    return int(row["pending_count"] or 0), row["oldest_created_at"]


def knowledge_hash_exists(item_type: str, content_hash: str) -> bool:
    """Check if a knowledge item with this content hash already exists."""
    from aletheia.storage.repositories import _select_all

    rows = _select_all(
        "knowledge_items",
        where="item_type = ? AND content_hash = ?",
        params=(item_type, content_hash),
        limit=1,
    )
    return len(rows) > 0


def knowledge_title_exists_within(item_type: str, title: str, hours: int = 6) -> bool:
    """Check if a knowledge item with same type+title was created within N hours."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    row = db.execute(
        "SELECT 1 FROM knowledge_items WHERE item_type = ? AND title = ? AND created_at > datetime('now', ?) LIMIT 1",
        (item_type, title, f"-{hours} hours"),
    ).fetchone()
    return row is not None


def insert_serenity_theme_with_claim_links(
    theme: SerenityTheme,
    links: list[tuple[str, str, float | None]],
) -> None:
    from aletheia.storage.repositories import (
        insert_serenity_theme,
        insert_serenity_theme_claims_bulk,
        transaction,
    )

    with transaction(savepoint=True):
        insert_serenity_theme(theme)
        insert_serenity_theme_claims_bulk(links)


def get_serenity_theme(theme_id: str) -> dict | None:
    from aletheia.storage.repositories import _select_one

    return _select_one("serenity_themes", theme_id)


def list_serenity_themes(status: str | None = "active", limit: int = 100) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    where: str | None = None
    params: list = []
    if status:
        where = "status = ?"
        params.append(status)
    return _select_all("serenity_themes", where=where, params=tuple(params), limit=limit)


def list_serenity_evidence(theme_id: str | None = None, limit: int = 100) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    where: str | None = None
    params: list = []
    if theme_id is not None:
        where = "theme_id = ?"
        params.append(theme_id)
    return _select_all("serenity_evidence", where=where, params=tuple(params), limit=limit)


def get_serenity_opportunity(opportunity_id: str) -> dict | None:
    from aletheia.storage.repositories import _select_one

    return _select_one("serenity_opportunities", opportunity_id)


def list_serenity_opportunities(
    theme_id: str | None = None,
    status: str | None = None,
    stage: str | None = None,
    limit: int = 100,
) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    conditions: list[str] = []
    params: list = []
    if theme_id is not None:
        conditions.append("theme_id = ?")
        params.append(theme_id)
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    if stage is not None:
        conditions.append("stage = ?")
        params.append(stage)
    where = " AND ".join(conditions) if conditions else None
    return _select_all("serenity_opportunities", where=where, params=tuple(params), limit=limit)


def transition_active_serenity_opportunities_for_theme(
    theme_id: str,
    *,
    status: str,
    updated_at: str,
) -> int:
    from aletheia.storage.repositories import execute_write

    result = execute_write(
        "UPDATE serenity_opportunities SET status=?, updated_at=? "
        "WHERE theme_id=? AND status='active'",
        (status, updated_at, theme_id),
    )
    return result.rowcount


def transition_serenity_theme_status(
    theme_id: str,
    *,
    status: str,
    updated_at: str,
) -> int | None:
    """Update one theme and atomically terminalize all active children."""
    from aletheia.storage.repositories import (
        execute_write,
        transaction,
        transition_active_serenity_opportunities_for_theme,
    )

    with transaction(immediate=True):
        theme = execute_write(
            "UPDATE serenity_themes SET status=?, updated_at=? WHERE id=?",
            (status, updated_at, theme_id),
        )
        if theme.rowcount != 1:
            return None
        if status not in {"dismissed", "expired"}:
            return 0
        return transition_active_serenity_opportunities_for_theme(
            theme_id,
            status=status,
            updated_at=updated_at,
        )


def list_serenity_risk_gates(opportunity_id: str, limit: int = 100) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    return _select_all("serenity_risk_gates", where="opportunity_id = ?", params=(opportunity_id,), limit=limit)


def list_serenity_replay_snapshots(opportunity_id: str, limit: int = 200) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    return _select_all(
        "serenity_replay_snapshots",
        where="opportunity_id = ?",
        params=(opportunity_id,),
        order="created_at DESC",
        limit=limit,
    )


def list_serenity_post_mortems(opportunity_id: str | None = None, limit: int = 100) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    conditions = ["invalidated_at IS NULL"]
    params: list = []
    if opportunity_id is not None:
        conditions.append("opportunity_id = ?")
        params.append(opportunity_id)
    return _select_all(
        "serenity_post_mortems",
        where=" AND ".join(conditions),
        params=tuple(params),
        limit=limit,
    )


def get_serenity_research_retry(parent_run_id: str, retry_count: int) -> dict | None:
    from aletheia.storage.repositories import get_db

    row = (
        get_db()
        .execute(
            "SELECT * FROM serenity_research_runs "
            "WHERE parent_run_id = ? AND retry_count = ? "
            "ORDER BY created_at, id LIMIT 1",
            (parent_run_id, retry_count),
        )
        .fetchone()
    )
    return dict(row) if row else None


def insert_serenity_research_retry(r) -> tuple[dict, bool]:
    """Insert one durable retry identity, returning the winner on a race."""
    from aletheia.storage.repositories import (
        get_serenity_research_retry,
        get_serenity_research_run,
        insert_serenity_research_run,
    )


    retry_key = getattr(r, "idempotency_key", None)
    if not r.parent_run_id or not isinstance(retry_key, str) or not retry_key.startswith("serenity-retry:"):
        raise ValueError("Serenity retry identity is required")
    try:
        insert_serenity_research_run(r)
    except sqlite3.IntegrityError:
        existing = get_serenity_research_retry(r.parent_run_id, r.retry_count)
        if existing is None:
            raise
        return existing, True
    inserted = get_serenity_research_run(r.id)
    if inserted is None:
        raise RuntimeError("inserted Serenity retry is unavailable")
    return inserted, False


def get_serenity_research_run(run_id: str) -> dict | None:
    from aletheia.storage.repositories import _select_one

    return _select_one("serenity_research_runs", run_id)


def list_serenity_research_runs(
    status: str | None = None,
    trigger_type: str | None = None,
    limit: int = 20,
) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    conditions: list[str] = []
    params: list = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if trigger_type:
        conditions.append("trigger_type = ?")
        params.append(trigger_type)
    where = " AND ".join(conditions) if conditions else None
    return _select_all(
        "serenity_research_runs", where=where, params=tuple(params), order="created_at DESC", limit=limit
    )


def claim_serenity_research_run(run_id: str, *, now: str) -> bool:
    """Claim one queued run without reviving a concurrently cancelled run."""
    from aletheia.storage.repositories import execute_write

    cursor = execute_write(
        "UPDATE serenity_research_runs SET status = 'running', started_at = ?, "
        "updated_at = ? WHERE id = ? AND status = 'queued'",
        (now, now, run_id),
    )
    return cursor.rowcount == 1


def fail_unclaimed_serenity_research_run(
    run_id: str,
    *,
    error_type: str,
    error_message: str | None,
    error_stage: str | None,
    now: str,
) -> bool:
    """Fail a queued run when worker startup crashes before the claim commits."""
    from aletheia.storage.repositories import execute_write

    cursor = execute_write(
        "UPDATE serenity_research_runs SET status = 'failed', error_type = ?, "
        "error_message = ?, error_stage = ?, finished_at = ?, updated_at = ? "
        "WHERE id = ? AND status = 'queued'",
        (error_type, error_message, error_stage, now, now, run_id),
    )
    return cursor.rowcount == 1


def request_serenity_research_run_cancel(run_id: str, *, now: str) -> tuple[str | None, bool]:
    """Cancel queued work or request cooperative cancellation of running work."""
    from aletheia.storage.repositories import execute_write, get_serenity_research_run

    queued = execute_write(
        "UPDATE serenity_research_runs SET status = 'cancelled', finished_at = ?, "
        "updated_at = ? WHERE id = ? AND status = 'queued'",
        (now, now, run_id),
    )
    if queued.rowcount == 1:
        return "cancelled", True
    running = execute_write(
        "UPDATE serenity_research_runs SET status = 'cancel_requested', updated_at = ? "
        "WHERE id = ? AND status = 'running'",
        (now, run_id),
    )
    if running.rowcount == 1:
        return "cancel_requested", True
    current = get_serenity_research_run(run_id)
    return (str(current["status"]) if current else None), False


def acknowledge_serenity_research_run_cancel(run_id: str, *, now: str) -> bool:
    """Write terminal cancellation only after the running worker has stopped."""
    from aletheia.storage.repositories import execute_write

    cursor = execute_write(
        "UPDATE serenity_research_runs SET status = 'cancelled', finished_at = ?, "
        "next_retry_at = NULL, updated_at = ? "
        "WHERE id = ? AND status = 'cancel_requested'",
        (now, now, run_id),
    )
    return cursor.rowcount == 1


def finish_serenity_research_run_with_event(run_id: str, status: str, event, **kwargs) -> bool:
    """Commit a terminal run transition and its terminal event atomically."""
    from aletheia.storage.repositories import execute_write, insert_serenity_run_event, transaction

    if status not in {"completed", "completed_empty", "failed", "rate_limited"}:
        raise ValueError(f"invalid Serenity terminal status: {status}")
    allowed = {
        "stage",
        "finished_at",
        "updated_at",
        "artifact_counts",
        "error_type",
        "error_message",
        "error_stage",
        "next_retry_at",
    }
    unknown = set(kwargs) - allowed
    if unknown:
        raise ValueError(f"invalid Serenity terminal fields: {sorted(unknown)}")
    values = {"status": status, **kwargs}
    sets = ", ".join(f"{key} = ?" for key in values)
    with transaction(savepoint=True):
        cursor = execute_write(
            f"UPDATE serenity_research_runs SET {sets} WHERE id = ? AND status = 'running'",
            [*values.values(), run_id],
        )
        if cursor.rowcount != 1:
            return False
        insert_serenity_run_event(event)
    return True


def get_active_serenity_research_run() -> dict | None:
    from aletheia.storage.repositories import list_active_serenity_research_runs

    rows = list_active_serenity_research_runs(limit=1)
    return rows[0] if rows else None


def list_active_serenity_research_runs(limit: int = 100) -> list[dict]:
    from aletheia.storage.repositories import get_db

    db = get_db()
    rows = db.execute(
        "SELECT * FROM serenity_research_runs "
        "WHERE status IN ('queued', 'running', 'cancel_requested', 'waiting_retry') "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_latest_successful_serenity_research_run(
    *, not_after: str | None = None,
) -> dict | None:
    from aletheia.storage.repositories import get_db

    db = get_db()
    time_limit = (
        "AND julianday(COALESCE(finished_at, created_at)) <= julianday(?) "
        if not_after is not None else ""
    )
    row = db.execute(
        "SELECT * FROM serenity_research_runs "
        "WHERE status IN ('completed', 'completed_empty') "
        f"{time_limit}"
        "ORDER BY COALESCE(finished_at, created_at) DESC LIMIT 1",
        (not_after,) if not_after is not None else (),
    ).fetchone()
    return dict(row) if row else None


def get_serenity_research_run_by_idempotency_key(key: str) -> dict | None:
    from aletheia.storage.repositories import get_db

    db = get_db()
    row = db.execute("SELECT * FROM serenity_research_runs WHERE idempotency_key = ?", (key,)).fetchone()
    return dict(row) if row else None


def list_serenity_run_events(run_id: str, limit: int = 200) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    return _select_all("serenity_run_events", where="run_id = ?", params=(run_id,), order="created_at ASC", limit=limit)


def get_serenity_user_profile() -> dict | None:
    from aletheia.storage.repositories import _select_all

    rows = _select_all("serenity_user_profiles", order="updated_at DESC", limit=1)
    return rows[0] if rows else None


def get_serenity_profile_snapshot(snapshot_id: str) -> dict | None:
    from aletheia.storage.repositories import _select_one

    return _select_one("serenity_profile_snapshots", snapshot_id)


def list_serenity_feedback_events(target_type: str, target_id: str, limit: int = 50) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    return _select_all(
        "serenity_feedback_events",
        where="target_type = ? AND target_id = ?",
        params=(target_type, target_id),
        limit=limit,
    )


def list_serenity_claims(run_id: str | None = None, price_relevant: bool | None = None, limit: int = 200) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    where_parts = []
    params: list = []
    if run_id:
        where_parts.append("run_id = ?")
        params.append(run_id)
    if price_relevant is not None:
        where_parts.append("price_relevant = ?")
        params.append(int(price_relevant))
    where = " AND ".join(where_parts) if where_parts else None
    return _select_all("serenity_claims", where=where, params=tuple(params), limit=limit)


def insert_serenity_theme_claims_bulk(links: list[tuple[str, str, float | None]]) -> None:
    from aletheia.storage.repositories import execute_write, transaction

    with transaction(savepoint=True):
        for theme_id, claim_id, relevance_score in links:
            execute_write(
                "INSERT OR IGNORE INTO serenity_theme_claims (theme_id, claim_id, relevance_score) VALUES (?, ?, ?)",
                (theme_id, claim_id, relevance_score),
            )


def list_serenity_claims_for_theme(theme_id: str, limit: int = 200) -> list[dict]:
    from aletheia.storage.repositories import get_db

    db = get_db()
    rows = db.execute(
        "SELECT c.*, tc.relevance_score FROM serenity_claims c "
        "JOIN serenity_theme_claims tc ON tc.claim_id = c.id "
        "WHERE tc.theme_id = ? ORDER BY c.created_at DESC LIMIT ?",
        (theme_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def acquire_serenity_lock(lock_key: str, owner_run_id: str, ttl_seconds: int = 600) -> bool:
    from aletheia.storage.repositories import execute_write

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()

    # Atomic acquire: purge expired, then INSERT OR IGNORE (PK = lock_key).
    # Two concurrent threads both DELETE expired + INSERT OR IGNORE: only one
    # INSERT succeeds (the other is a no-op due to PK constraint). No TOCTOU.
    execute_write(
        "DELETE FROM serenity_agent_locks WHERE lock_key = ? AND expires_at < ?",
        (lock_key, now.isoformat()),
    )
    inserted = execute_write(
        "INSERT OR IGNORE INTO serenity_agent_locks "
        "(lock_key, owner_run_id, acquired_at, expires_at) VALUES (?, ?, ?, ?)",
        (lock_key, owner_run_id, now.isoformat(), expires_at),
    )
    return inserted.rowcount > 0


def release_serenity_lock(lock_key: str, owner_run_id: str) -> bool:
    from aletheia.storage.repositories import execute_write

    result = execute_write(
        "DELETE FROM serenity_agent_locks WHERE lock_key = ? AND owner_run_id = ?",
        (lock_key, owner_run_id),
    )
    return result.rowcount > 0


def prune_agent_runs(days: int = 90) -> int:
    # agent_run_steps and agent_tool_calls have FK → agent_runs with no ON DELETE CASCADE.
    # Delete children first in the same transaction to avoid IntegrityError.
    from aletheia.storage.repositories import _record_retention_batch, execute_write, get_db, transaction

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    total = 0
    while True:
        run_ids = [
            row[0]
            for row in db.execute(
                "SELECT id FROM agent_runs WHERE started_at < ? ORDER BY started_at LIMIT 1000",
                (cutoff,),
            ).fetchall()
        ]
        if not run_ids:
            break
        placeholders = ",".join("?" * len(run_ids))
        started = time.monotonic()
        with transaction():
            execute_write(
                f"DELETE FROM agent_run_artifacts WHERE run_id IN ({placeholders})",
                run_ids,
            )
            execute_write(
                f"DELETE FROM agent_run_steps WHERE run_id IN ({placeholders})",
                run_ids,
            )
            execute_write(
                f"DELETE FROM agent_tool_calls WHERE run_id IN ({placeholders})",
                run_ids,
            )
            cur = execute_write(
                f"DELETE FROM agent_runs WHERE id IN ({placeholders})",
                run_ids,
            )
        deleted = max(0, cur.rowcount or 0)
        _record_retention_batch(
            "agent_runs",
            deleted,
            (time.monotonic() - started) * 1000,
        )
        total += deleted
        if len(run_ids) < 1000 or deleted == 0:
            break
    return total


def prune_run_steps(days: int = 14) -> int:
    """Prune telemetry-only agent_run_steps rows. Default lowered 30d→14d (Phase 3)."""
    from aletheia.storage.repositories import _delete_old_rows

    return _delete_old_rows("agent_run_steps", days)


def prune_tool_calls(days: int = 90) -> int:
    from aletheia.storage.repositories import _delete_old_rows

    return _delete_old_rows("agent_tool_calls", days)


def prune_serenity_claims(days: int = 90) -> int:
    # serenity_theme_claims has FK → serenity_claims with no ON DELETE CASCADE.
    from aletheia.storage.repositories import execute_write, get_db, transaction

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    claim_ids = [
        row[0] for row in db.execute("SELECT id FROM serenity_claims WHERE created_at < ?", (cutoff,)).fetchall()
    ]
    if not claim_ids:
        return 0
    placeholders = ",".join("?" * len(claim_ids))
    with transaction():
        execute_write(f"DELETE FROM serenity_theme_claims WHERE claim_id IN ({placeholders})", claim_ids)
        execute_write("DELETE FROM serenity_claims WHERE created_at < ?", (cutoff,))
    return len(claim_ids)


def _resolve_experiment_floor(value: str | None | object) -> str | None:
    from aletheia.storage.repositories import (
        _AUTO_EXPERIMENT_FLOOR,
        _canonical_lab_authority,
        min_open_experiment_exposure_at,
    )

    if value is _AUTO_EXPERIMENT_FLOOR:
        if _canonical_lab_authority():
            return None
        return min_open_experiment_exposure_at()
    return cast(str | None, value)


def prune_controller_recommendations(days: int = 30) -> int:
    """Prune controller_recommendations rows older than ``days`` (by created_at).

    Rows are append-only shadow-log advice — none of the entries are ever
    executed against production, so lossy pruning is safe.
    """
    from aletheia.storage.repositories import _delete_old_rows

    return _delete_old_rows("controller_recommendations", days)


def expire_stale_serenity_themes() -> int:
    """Soft-expire Serenity themes whose deadline has elapsed and cascade to children.

    Finds active/watching SerenityTheme rows where expires_at is in the past,
    transitions them to 'expired', and cascades the same status to their active
    SerenityOpportunity children so the pending-signal queue treats them as stale
    (#199). Writes are atomic; the transaction is not held across I/O.
    """
    from aletheia.storage.repositories import (
        get_db,
        transaction,
        transition_active_serenity_opportunities_for_theme,
        update_serenity_theme,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    with transaction(immediate=True):
        db = get_db()
        expired_themes = db.execute(
            "SELECT id FROM serenity_themes "
            "WHERE expires_at IS NOT NULL "
            "AND datetime(expires_at) <= datetime('now') "
            "AND status NOT IN ('dismissed', 'expired')",
        ).fetchall()
        if not expired_themes:
            return 0
        theme_ids = [row["id"] for row in expired_themes]
        for theme_id in theme_ids:
            update_serenity_theme(theme_id, status="expired", updated_at=now_iso)
            transition_active_serenity_opportunities_for_theme(
                theme_id,
                status="expired",
                updated_at=now_iso,
            )
        return len(theme_ids)
