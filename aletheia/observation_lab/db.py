"""Physical isolation for Observation Lab state (Issue #1072).

``~/.aletheia/observation-lab/lab.db`` is the Lab's own SQLite file. Two
consequences:

- Production migrations cannot silently rewrite a frozen unit — a schema
  change in ``app.db`` affects fresh Production reads, not the Lab's
  immutable snapshots.
- Reads FROM Production go through ``aletheia.storage.sqlite.get_db`` (or
  ``open_readonly_snapshot`` for physical RO paths); writes TO the Lab go
  through ``get_lab_db``. The two are never confused.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from aletheia.config.loader import get_data_dir

_local = threading.local()

_LAB_SCHEMA_SQL = (
    "CREATE TABLE IF NOT EXISTS filter_shadow_frozen_units ("
    "id TEXT PRIMARY KEY,"
    "experiment_id TEXT NOT NULL,"
    "outcome_candidate_key TEXT NOT NULL,"
    "decision TEXT NOT NULL CHECK (decision IN ('passed','rejected')),"
    "input_snapshot TEXT NOT NULL,"
    "parameter_snapshot TEXT NOT NULL,"
    "first_evaluated_at TEXT NOT NULL,"
    "source_runtime_commit TEXT,"
    "first_seen_at TEXT NOT NULL,"
    "UNIQUE (experiment_id, outcome_candidate_key)"
    ");"
    "CREATE INDEX IF NOT EXISTS idx_frozen_units_experiment "
    "ON filter_shadow_frozen_units(experiment_id, first_evaluated_at);"
)


def lab_db_path() -> Path:
    """Resolve the Lab DB location under the current data root."""
    root = get_data_dir() / "observation-lab"
    root.mkdir(parents=True, exist_ok=True)
    return root / "lab.db"


def get_lab_db() -> sqlite3.Connection:
    """Return a thread-local, path-aware connection to ``lab.db``.

    Path change (tests swap ``DATA_DIR``) closes the stale connection and
    reopens against the new path. Schema is materialized on every open — the
    ``IF NOT EXISTS`` guard keeps it idempotent.
    """
    target = lab_db_path()
    cached_path = getattr(_local, "path", None)
    cached_conn = getattr(_local, "conn", None)
    if cached_conn is not None and cached_path == target:
        return cached_conn
    if cached_conn is not None:
        try:
            cached_conn.close()
        except sqlite3.Error:
            pass
    conn = sqlite3.connect(
        str(target),
        isolation_level=None,  # autocommit — matches app.db policy
        check_same_thread=False,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.executescript(_LAB_SCHEMA_SQL)
    _local.conn = conn
    _local.path = target
    return conn


def close_lab_db() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        _local.conn = None
        _local.path = None


def reset_lab_db() -> None:
    """Test hook: drop cached connection so a fresh ``DATA_DIR`` is picked up."""
    close_lab_db()
