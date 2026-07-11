"""Idempotent repositories for the additive Learning ledger."""

from __future__ import annotations

from pydantic import BaseModel

from aletheia.storage.sqlite import get_db


_TABLE_ID = {
    "learning_hypotheses": "hypothesis_id",
    "learning_experiments": "experiment_id",
    "learning_exposures": "exposure_id",
    "learning_contribution_verdicts": "verdict_id",
    "learning_job_health": "job_id",
    "learning_policy_epochs": "epoch_id",
}


def insert_record(table: str, record: BaseModel, *, ignore_duplicates: bool = False) -> bool:
    if table not in _TABLE_ID:
        raise ValueError(f"unsupported Learning ledger table: {table}")
    data = record.model_dump()
    db = get_db()
    was_in_tx = db.in_transaction
    verb = "INSERT OR IGNORE" if ignore_duplicates else "INSERT"
    columns = ", ".join(data)
    placeholders = ", ".join("?" for _ in data)
    cursor = db.execute(f"{verb} INTO {table} ({columns}) VALUES ({placeholders})", tuple(data.values()))
    if not was_in_tx:
        db.commit()
    return bool(cursor.rowcount)


def get_record(table: str, record_id: str) -> dict | None:
    id_column = _TABLE_ID.get(table)
    if not id_column:
        raise ValueError(f"unsupported Learning ledger table: {table}")
    row = get_db().execute(f"SELECT * FROM {table} WHERE {id_column}=?", (record_id,)).fetchone()
    return dict(row) if row else None


def list_records(table: str, *, limit: int = 100) -> list[dict]:
    id_column = _TABLE_ID.get(table)
    if not id_column:
        raise ValueError(f"unsupported Learning ledger table: {table}")
    rows = get_db().execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
    return [dict(row) for row in rows]


def insert_hypothesis(record) -> bool:
    return insert_record("learning_hypotheses", record, ignore_duplicates=True)


def insert_experiment(record) -> bool:
    return insert_record("learning_experiments", record, ignore_duplicates=True)


def insert_exposure(record) -> bool:
    return insert_record("learning_exposures", record, ignore_duplicates=True)


def upsert_job_health(record) -> None:
    """Persist the latest durable health snapshot for one Learning job."""
    data = record.model_dump()
    db = get_db()
    was_in_tx = db.in_transaction
    columns = ", ".join(data)
    placeholders = ", ".join("?" for _ in data)
    updates = ", ".join(f"{column}=excluded.{column}" for column in data if column != "job_id")
    db.execute(
        f"INSERT INTO learning_job_health ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(job_id) DO UPDATE SET {updates}",
        tuple(data.values()),
    )
    if not was_in_tx:
        db.commit()


def get_job_health(job_id: str) -> dict | None:
    return get_record("learning_job_health", job_id)
