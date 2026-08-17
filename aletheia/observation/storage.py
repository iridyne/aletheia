"""SQLite storage for the narrow Observation Lab v1 POC."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from aletheia.config.loader import get_data_dir


SCHEMA_VERSION = 1


class ObservationError(RuntimeError):
    """Raised when the sidecar cannot preserve its frozen contract."""


def default_lab_path() -> Path:
    return get_data_dir() / "observation-lab" / "lab.db"


@contextmanager
def lab_db(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    target = Path(path or default_lab_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(target), timeout=5.0)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version not in (0, SCHEMA_VERSION):
            raise ObservationError(f"unsupported Observation Lab schema version: {version}")
        ensure_schema(db)
        if version == 0:
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.commit()
        yield db
    finally:
        db.close()


def ensure_schema(db: sqlite3.Connection) -> None:
    # Deliberately ranker-specific. v1 must not become a third generic experiment registry.
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS ranker_confidence_observations (
            observation_id TEXT PRIMARY KEY,
            source_experiment_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            started_at TEXT NOT NULL,
            filter_version TEXT NOT NULL,
            stage TEXT NOT NULL,
            scanner_family TEXT NOT NULL,
            direction TEXT NOT NULL,
            market_type TEXT NOT NULL,
            horizon TEXT NOT NULL,
            control_minimum TEXT NOT NULL,
            treatment_minimum TEXT NOT NULL,
            min_samples INTEGER NOT NULL,
            min_coverage REAL NOT NULL,
            min_edge_delta_pct REAL NOT NULL,
            adapter_version TEXT NOT NULL,
            evaluator_version TEXT NOT NULL,
            causal_identity_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'collecting',
            invalidation_reason TEXT,
            last_synced_at TEXT,
            last_evaluated_at TEXT,
            CHECK(status IN (
                'collecting','ready_for_review','completed_no_support','invalidated','closed'
            ))
        );

        CREATE TABLE IF NOT EXISTS ranker_confidence_units (
            observation_id TEXT NOT NULL,
            outcome_candidate_key TEXT NOT NULL,
            decision TEXT NOT NULL,
            first_evaluated_at TEXT NOT NULL,
            input_snapshot TEXT NOT NULL,
            parameter_snapshot TEXT NOT NULL,
            source_code_commit TEXT,
            outcome_status TEXT,
            net_return_pct REAL,
            PRIMARY KEY(observation_id, outcome_candidate_key),
            FOREIGN KEY(observation_id)
                REFERENCES ranker_confidence_observations(observation_id) ON DELETE RESTRICT,
            CHECK(decision IN ('passed','rejected'))
        );

        CREATE TABLE IF NOT EXISTS ranker_confidence_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            evaluation_fingerprint TEXT NOT NULL,
            disposition TEXT NOT NULL,
            control_candidate_count INTEGER NOT NULL,
            control_settled_n INTEGER NOT NULL,
            control_outcome_coverage REAL NOT NULL,
            control_raw_avg_net_pct REAL,
            treatment_candidate_count INTEGER NOT NULL,
            treatment_settled_n INTEGER NOT NULL,
            treatment_outcome_coverage REAL NOT NULL,
            treatment_raw_avg_net_pct REAL,
            treatment_shrunk_avg_net_pct REAL,
            edge_delta_pct REAL,
            invalid_replay_inputs INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL,
            UNIQUE(observation_id, evaluation_fingerprint),
            FOREIGN KEY(observation_id)
                REFERENCES ranker_confidence_observations(observation_id) ON DELETE RESTRICT
        );
        """
    )
    db.commit()
