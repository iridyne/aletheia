"""Idempotent repositories for the additive Learning ledger."""

from __future__ import annotations

from pydantic import BaseModel

from aletheia.storage.repositories import transaction
from aletheia.storage.sqlite import get_db


_TABLE_ID = {
    "learning_hypotheses": "hypothesis_id",
    "learning_experiments": "experiment_id",
    "learning_exposures": "exposure_id",
    "learning_contribution_verdicts": "verdict_id",
    "learning_inference_decisions": "experiment_id",
    "learning_lifecycle_events": "event_id",
    "learning_job_health": "job_id",
    "learning_lesson_reviewer_attempts": "attempt_id",
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
    """Insert an exposure and activate its experiment in one transaction."""
    with transaction():
        inserted = insert_record("learning_exposures", record, ignore_duplicates=True)
        db = get_db()
        db.execute(
            "UPDATE learning_experiments SET "
            "status=CASE WHEN status='registered' THEN 'active' ELSE status END, "
            "started_at=(SELECT MIN(exposed_at) FROM learning_exposures "
            "WHERE experiment_id=?) "
            "WHERE experiment_id=? AND ended_at IS NULL "
            "AND EXISTS (SELECT 1 FROM learning_exposures WHERE experiment_id=?)",
            (
                record.experiment_id,
                record.experiment_id,
                record.experiment_id,
            ),
        )
    return inserted


def insert_lifecycle_event(record) -> bool:
    data = record.model_dump()
    with transaction(savepoint=True):
        db = get_db()
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        cursor = db.execute(
            f"INSERT OR IGNORE INTO learning_lifecycle_events ({columns}) "
            f"VALUES ({placeholders})",
            tuple(data.values()),
        )
        inserted = bool(cursor.rowcount)
        if inserted:
            db.execute(
                "INSERT INTO audit_events "
                "(id,timestamp,actor,source,event_type,entity_type,entity_id,message) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    f"learning-lifecycle:{record.event_id}",
                    record.created_at,
                    record.audit_actor,
                    record.audit_source,
                    "learning_lifecycle_transition",
                    "learning_experiment",
                    record.experiment_id,
                    record.reason,
                ),
            )
    return inserted


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


def latest_lesson_reviewer_attempt(input_watermark: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM learning_lesson_reviewer_attempts "
        "WHERE input_watermark=? ORDER BY started_at DESC, rowid DESC LIMIT 1",
        (input_watermark,),
    ).fetchone()
    return dict(row) if row else None


def running_lesson_reviewer_attempts() -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM learning_lesson_reviewer_attempts "
        "WHERE outcome='running' ORDER BY started_at"
    ).fetchall()
    return [dict(row) for row in rows]


def finish_lesson_reviewer_attempt(
    attempt_id: str,
    *,
    finished_at: str,
    outcome: str,
    error_class: str | None = None,
    error_message: str | None = None,
) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    cursor = db.execute(
        "UPDATE learning_lesson_reviewer_attempts SET "
        "finished_at=?, outcome=?, error_class=?, error_message=? "
        "WHERE attempt_id=? AND outcome='running'",
        (finished_at, outcome, error_class, error_message, attempt_id),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(f"lesson reviewer attempt is not running: {attempt_id}")
    if not was_in_tx:
        db.commit()


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


def get_latest_measurement_verdict(
    experiment_id: str,
    *,
    policy_epoch: str | None = None,
    stage: str | None = None,
    hypothesis_id: str | None = None,
    target_horizon: str | None = None,
    outcome_contract_version: str | None = None,
    metric_version: str | None = None,
    behavior_contract_hash: str | None = None,
    inference_role: str | None = None,
) -> dict | None:
    """Return the newest exact-cohort measurement, never a legacy authority row.

    "Newest" is evaluation order (``created_at``), not observation-window order.
    ``contribution_runner._window`` stamps the 1970-01-01 sentinel on a cohort with
    no surviving samples, and that sentinel sorts *after* every real window under
    ``window_end DESC`` — so window ordering silently returned a superseded row from
    the same cohort.  A cohort that empties out therefore resurrected its own last
    ``positive`` and the controller promoted on it.  Verdicts are immutable and
    evidence only accumulates within a cohort, so created_at ordering is the
    monotonic one; window_end remains the tiebreak for cohorts evaluated together.
    """
    clauses = [
        "experiment_id=?",
        "verdict IN ('positive', 'neutral', 'negative', 'insufficient_data', 'invalid')",
    ]
    params: list[str] = [experiment_id]
    for column, value in (
        ("policy_epoch", policy_epoch),
        ("stage", stage),
        ("hypothesis_id", hypothesis_id),
        ("target_horizon", target_horizon),
        ("outcome_contract_version", outcome_contract_version),
        ("metric_version", metric_version),
        ("behavior_contract_hash", behavior_contract_hash),
        ("inference_role", inference_role),
    ):
        if value is not None:
            clauses.append(f"{column}=?")
            params.append(value)
    row = get_db().execute(
        "SELECT * FROM learning_contribution_verdicts WHERE " + " AND ".join(clauses) + " "
        "ORDER BY datetime(created_at) DESC, datetime(window_end) DESC, verdict_id DESC LIMIT 1",
        tuple(params),
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


def list_unmirrored_lifecycle_events(
    *, limit: int = 100, event_ids: list[str] | None = None,
) -> list[dict]:
    clauses = ["audit_mirror_status IN ('pending', 'failed')"]
    params: list[object] = []
    if event_ids is not None:
        if not event_ids:
            return []
        clauses.append(f"event_id IN ({','.join('?' for _ in event_ids)})")
        params.extend(event_ids)
    params.append(min(max(int(limit), 1), 500))
    rows = get_db().execute(
        "SELECT * FROM learning_lifecycle_events "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY created_at, event_id LIMIT ?",
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def count_unmirrored_lifecycle_events() -> int:
    row = get_db().execute(
        "SELECT COUNT(*) FROM learning_lifecycle_events "
        "WHERE audit_mirror_status IN ('pending', 'failed')"
    ).fetchone()
    return int(row[0] or 0)


def update_lifecycle_audit_mirror(
    event_id: str,
    *,
    status: str,
    mirrored_at: str | None,
    failure_reason: str | None,
) -> None:
    db = get_db()
    was_in_tx = db.in_transaction
    where = "event_id=?"
    if status != "mirrored":
        where += " AND audit_mirror_status!='mirrored'"
    db.execute(
        "UPDATE learning_lifecycle_events SET audit_mirror_status=?, "
        f"audit_mirrored_at=?, audit_failure_reason=? WHERE {where}",
        (status, mirrored_at, failure_reason, event_id),
    )
    if not was_in_tx:
        db.commit()


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
