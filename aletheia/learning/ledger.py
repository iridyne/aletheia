"""Idempotent repositories for the additive Learning ledger."""

from __future__ import annotations

from pydantic import BaseModel

from aletheia.storage.sqlite import get_db


_TABLE_ID = {
    "learning_hypotheses": "hypothesis_id",
    "learning_experiments": "experiment_id",
    "learning_exposures": "exposure_id",
    "learning_contribution_verdicts": "verdict_id",
    "learning_lifecycle_events": "event_id",
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


def insert_lifecycle_event(record) -> bool:
    return insert_record("learning_lifecycle_events", record, ignore_duplicates=True)


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


def get_hypothesis_by_fingerprint(fingerprint: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM learning_hypotheses WHERE fingerprint=?", (fingerprint,)
    ).fetchone()
    return dict(row) if row else None


def get_open_experiment(hypothesis_id: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM learning_experiments WHERE hypothesis_id=? AND ended_at IS NULL "
        "ORDER BY created_at DESC LIMIT 1",
        (hypothesis_id,),
    ).fetchone()
    return dict(row) if row else None


def get_latest_verdict(experiment_id: str, *, authority_only: bool = False) -> dict | None:
    """Return the newest verdict, optionally restricted to authority decisions.

    Measurement verdicts intentionally use a separate vocabulary
    (``positive``/``neutral``/``negative``/``insufficient_data``/``invalid``).
    They are durable evidence, never a signal to change a consumer's authority.
    """
    clauses = ["experiment_id=?"]
    params: list[str] = [experiment_id]
    if authority_only:
        clauses.append("verdict IN ('promote', 'refreeze')")
    row = get_db().execute(
        "SELECT * FROM learning_contribution_verdicts WHERE " + " AND ".join(clauses) + " "
        "ORDER BY created_at DESC LIMIT 1",
        tuple(params),
    ).fetchone()
    return dict(row) if row else None


def get_latest_verdict_for_cohort(
    experiment_id: str, *, policy_epoch: str, stage: str,
) -> dict | None:
    """Latest verdict computed under the EXACT current cohort (P0-4).

    A verdict only grants authority if it was produced for this experiment under the
    same policy epoch and stage. A verdict stamped with a different (or NULL) epoch or
    stage is invisible here — so a stale positive verdict cannot authorize a treatment
    after config/code (epoch) or lifecycle stage changed.
    """
    row = get_db().execute(
        "SELECT * FROM learning_contribution_verdicts "
        "WHERE experiment_id=? AND policy_epoch=? AND stage=? "
        "AND verdict IN ('promote', 'refreeze') "
        "ORDER BY created_at DESC LIMIT 1",
        (experiment_id, policy_epoch, stage),
    ).fetchone()
    return dict(row) if row else None


def get_latest_measurement_verdict(experiment_id: str) -> dict | None:
    """Return the newest AT-01 measurement verdict, never a legacy authority row."""
    row = get_db().execute(
        "SELECT * FROM learning_contribution_verdicts WHERE experiment_id=? "
        "AND verdict IN ('positive', 'neutral', 'negative', 'insufficient_data', 'invalid') "
        "ORDER BY created_at DESC, verdict_id DESC LIMIT 1",
        (experiment_id,),
    ).fetchone()
    return dict(row) if row else None


def list_open_experiments(*, path_id: str | None = None) -> list[dict]:
    clauses = ["e.ended_at IS NULL"]
    params: list[str] = []
    if path_id is not None:
        clauses.append("h.path_id=?")
        params.append(path_id)
    rows = get_db().execute(
        "SELECT e.* FROM learning_experiments e JOIN learning_hypotheses h "
        "ON h.hypothesis_id=e.hypothesis_id WHERE " + " AND ".join(clauses) + " "
        "ORDER BY e.created_at, e.experiment_id",
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def get_lifecycle_event_by_dedup_key(dedup_key: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM learning_lifecycle_events WHERE dedup_key=?", (dedup_key,)
    ).fetchone()
    return dict(row) if row else None


def list_lifecycle_events(experiment_id: str, *, limit: int = 100) -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM learning_lifecycle_events WHERE experiment_id=? OR successor_experiment_id=? "
        "ORDER BY created_at DESC, event_id DESC LIMIT ?",
        (experiment_id, experiment_id, max(1, int(limit))),
    ).fetchall()
    return [dict(row) for row in rows]


def get_trial_admission_event(successor_experiment_id: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM learning_lifecycle_events "
        "WHERE successor_experiment_id=? AND action='admit_trial' "
        "ORDER BY created_at DESC, event_id DESC LIMIT 1",
        (successor_experiment_id,),
    ).fetchone()
    return dict(row) if row else None


def get_exposure_by_dedup_key(dedup_key: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM learning_exposures WHERE dedup_key=?", (dedup_key,)
    ).fetchone()
    return dict(row) if row else None


def linked_settled_exposure_counts(
    experiment_id: str, *, policy_epoch: str | None = None,
) -> dict[str, int]:
    """Count unique arms backed by real clean, cost-aware settled outcomes."""
    from aletheia.analytics.learning_window import clean_window_cutoff

    cutoff = clean_window_cutoff("1970-01-01T00:00:00+00:00")
    rows = get_db().execute(
        "WITH eligible_keys AS ("
        " SELECT outcome_candidate_key AS outcome_key FROM universe_forward_outcomes"
        " WHERE status='settled' AND entry_ts>=? AND net_return_pct IS NOT NULL"
        " AND cost_model_version IS NOT NULL AND outcome_candidate_key IS NOT NULL"
        " UNION "
        " SELECT outcome_candidate_key || ':' || horizon AS outcome_key"
        " FROM filter_candidate_outcomes"
        " WHERE status='settled' AND entry_ts>=? AND net_return_pct IS NOT NULL"
        " AND cost_model_version IS NOT NULL"
        ") "
        "SELECT e.assignment, COUNT(DISTINCT e.candidate_key) AS n "
        "FROM learning_exposures e JOIN eligible_keys k"
        " ON k.outcome_key=e.outcome_candidate_key "
        "WHERE e.experiment_id=? AND (? IS NULL OR e.concurrent_policy_epoch=?) "
        "GROUP BY e.assignment",
        (cutoff, cutoff, experiment_id, policy_epoch, policy_epoch),
    ).fetchall()
    counts = {"control": 0, "treatment": 0}
    counts.update({str(row[0]): int(row[1]) for row in rows})
    return counts


def get_open_policy_epoch(epoch_kind: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM learning_policy_epochs WHERE epoch_kind=? AND ended_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (epoch_kind,),
    ).fetchone()
    return dict(row) if row else None


def close_policy_epoch(epoch_id: str, ended_at: str) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    db.execute(
        "UPDATE learning_policy_epochs SET ended_at=? WHERE epoch_id=? AND ended_at IS NULL",
        (ended_at, epoch_id),
    )
    if not was_in_tx:
        db.commit()
