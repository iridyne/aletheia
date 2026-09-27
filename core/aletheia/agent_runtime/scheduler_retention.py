"""Retention work for the scheduler's guarded cleanup job."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

__all__ = [
    "_cleanup_catchup_due",
    "_prune_agent_task_claims_tasks_db",
    "_prune_backups",
    "_prune_diagnostics",
    "_prune_news",
    "_prune_pre_migration_backup",
    "_prune_session_logs",
    "_prune_task_notes",
]


def _cleanup_catchup_due(*, now: datetime | None = None) -> bool:
    """Whether the most recent scheduled Shanghai cleanup lacks a success audit."""

    try:
        from aletheia.storage.sqlite import get_db

        row = get_db().execute(
            "SELECT timestamp FROM audit_events WHERE event_type='data_pruned' "
            "ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        if not row or not row[0]:
            return True
        last = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        current = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Shanghai"))
        most_recent_fire = current.replace(hour=3, minute=17, second=0, microsecond=0)
        if current < most_recent_fire:
            most_recent_fire -= timedelta(days=1)
        return last.astimezone(ZoneInfo("Asia/Shanghai")) < most_recent_fire
    except Exception as exc:
        logger.debug("cleanup catch-up probe unavailable: %s", exc)
        return False


def _prune_news(cfg) -> dict[str, int | str | None]:
    try:
        from extensions.data.news_collector import prune_old_news
        deleted = prune_old_news(cfg.news.retention_days)
        return {"deleted": deleted, "error": None}
    except Exception as e:
        return {"deleted": 0, "error": str(e)}


def _prune_session_logs(cfg) -> dict[str, int | str | None]:
    """Prune by the configured DB age, preserving the persistent main session."""
    try:
        from aletheia.agent_shell.file_artifacts import prune_session_logs_db_join
        deleted = prune_session_logs_db_join(
            cfg.retention.agent_shell_session_logs_days,
            extra_exempt_stems=(cfg.operator.main_session_id,),
        )
        return {"deleted": deleted, "error": None}
    except Exception as exc:
        return {"deleted": 0, "error": str(exc)}

def _prune_task_notes(cfg) -> dict[str, int | str | None]:
    """Task-notes filesystem prune (Agent 4) — joins agent_shell_tasks to
    unlink `.md` notes for finished tasks + orphan-sweeps stale files."""
    try:
        from aletheia.agent_shell.file_artifacts import prune_task_notes_db_join
        deleted = prune_task_notes_db_join(
            days_completed=int(getattr(cfg.retention, "agent_shell_task_notes_completed_days", 7)),
            days_failed=int(getattr(cfg.retention, "agent_shell_task_notes_failed_days", 30)),
        )
        return {"deleted": deleted, "error": None}
    except Exception as e:
        return {"deleted": 0, "error": str(e)}


def _prune_backups(keep_recent: int) -> int:
    try:
        from aletheia.maintenance.filesystem_retention import prune_backups
        return prune_backups(keep_recent=keep_recent)
    except Exception as exc:
        logger.warning("scheduler: backup prune unavailable (%s)", type(exc).__name__)
        return 0


def _prune_diagnostics(days: int) -> int:
    try:
        from aletheia.maintenance.filesystem_retention import prune_diagnostics_dir
        return prune_diagnostics_dir(days=days)
    except Exception as exc:
        logger.warning("scheduler: diagnostics prune unavailable (%s)", type(exc).__name__)
        return 0


def _prune_pre_migration_backup(days: int) -> int:
    try:
        from aletheia.maintenance.filesystem_retention import prune_pre_migration_backup
        return prune_pre_migration_backup(days=days)
    except Exception as exc:
        logger.warning("scheduler: pre-migration backup prune unavailable (%s)", type(exc).__name__)
        return 0


def _prune_agent_task_claims_tasks_db() -> int:
    """Best-effort DELETE of expired claims in the secondary tasks.db.

    That file lives at `${ALETHEIA_DATA_DIR:-~/.aletheia}/tasks.db` and is
    not managed by the app.db connection pool — the scheduler opens a
    fresh short-lived connection every cleanup cycle. Returns rowcount, or
    0 on any failure (missing file, unavailable table, locked DB).
    """
    try:
        import sqlite3
        from aletheia.config.loader import get_data_dir
        db_file = get_data_dir() / "tasks.db"
        if not db_file.exists():
            return 0
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(db_file), timeout=30, isolation_level=None)
        try:
            cur = conn.execute(
                "DELETE FROM agent_task_claims "
                "WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            return cur.rowcount or 0
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("scheduler: tasks.db claim prune unavailable (%s)", type(exc).__name__)
        return 0


# Import after defining exports so either module can be imported first.
from aletheia.agent_runtime.scheduler import logger
