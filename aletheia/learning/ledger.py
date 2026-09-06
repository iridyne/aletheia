"""Idempotent repositories for the additive Learning ledger."""

from __future__ import annotations

from pydantic import BaseModel

from aletheia.storage.repositories import (
    complete_outcome_economics_sql,
    insert_filter_candidate_outcomes_batch,
    transaction,
)
from aletheia.storage.sqlite import get_db, register_instant_us

_TABLE_ID = {
    "learning_hypotheses": "hypothesis_id",
    "learning_hypothesis_nominations": "nomination_id",
    "learning_experiments": "experiment_id",
    "learning_exposures": "exposure_id",
    "learning_contribution_verdicts": "verdict_id",
    "learning_inference_decisions": "experiment_id",
    "learning_lifecycle_events": "event_id",
    "learning_job_health": "job_id",
    "learning_lesson_reviewer_attempts": "attempt_id",
    "learning_policy_epochs": "epoch_id",
}


def _lab_authority() -> bool:
    """Return whether the canonical Lab owns Experiment behavior."""
    from aletheia.observation_lab.experiment_owner import (
        select_experiment_mutation_owner,
    )

    return select_experiment_mutation_owner().source == "lab"


def _require_legacy_experiment_write() -> None:
    from aletheia.observation_lab.experiment_owner import (
        ExperimentMutationAuthorityError,
        require_experiment_mutation_owner,
        select_experiment_mutation_owner,
    )

    owner = select_experiment_mutation_owner()
    if owner.source != "legacy":
        raise ExperimentMutationAuthorityError("legacy_experiment_owner_not_selected")
    require_experiment_mutation_owner("legacy", owner.epoch, db=get_db())


def insert_record(table: str, record: BaseModel, *, ignore_duplicates: bool = False) -> bool:
    if table not in _TABLE_ID:
        raise ValueError(f"unsupported Learning ledger table: {table}")
    protected = table in {
        "learning_experiments", "learning_exposures", "learning_contribution_verdicts",
        "learning_inference_decisions", "learning_lifecycle_events",
    }
    if protected:
        with transaction(immediate=True, savepoint=True):
            _require_legacy_experiment_write()
            return _insert_record(table, record, ignore_duplicates=ignore_duplicates)
    return _insert_record(table, record, ignore_duplicates=ignore_duplicates)


def _insert_record(table: str, record: BaseModel, *, ignore_duplicates: bool) -> bool:
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
    if _lab_authority() and table in {
        "learning_experiments", "learning_exposures", "learning_contribution_verdicts",
        "learning_inference_decisions", "learning_lifecycle_events",
    }:
        return None
    row = get_db().execute(f"SELECT * FROM {table} WHERE {id_column}=?", (record_id,)).fetchone()
    return dict(row) if row else None


def list_records(table: str, *, limit: int = 100) -> list[dict]:
    id_column = _TABLE_ID.get(table)
    if not id_column:
        raise ValueError(f"unsupported Learning ledger table: {table}")
    if _lab_authority() and table in {
        "learning_experiments", "learning_exposures", "learning_contribution_verdicts",
        "learning_inference_decisions", "learning_lifecycle_events",
    }:
        return []
    rows = get_db().execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
    return [dict(row) for row in rows]


def insert_hypothesis(record) -> bool:
    return insert_record("learning_hypotheses", record, ignore_duplicates=True)


def insert_nomination(record) -> bool:
    return insert_record(
        "learning_hypothesis_nominations", record, ignore_duplicates=True,
    )


def insert_experiment(record) -> bool:
    return insert_record("learning_experiments", record, ignore_duplicates=True)


def insert_exposure(
    record,
    *,
    require_open_experiment_id: str | None = None,
    require_open_experiment_stage: str | None = None,
    outcome_seeds: list | None = None,
) -> bool | None:
    """Insert an exposure, its outcome identity, and activate the experiment atomically.

    Returns ``None`` when the optional lifecycle guard rejects the write, versus
    ``False`` for an accepted idempotent duplicate.

    When an admitted Paper identity was captured upstream, the optional guard
    makes lifecycle validation and insertion one atomic operation. A concurrent
    close/refreeze therefore cannot append evidence to the ended experiment.
    """
    with transaction(immediate=require_open_experiment_id is not None):
        _require_legacy_experiment_write()
        db = get_db()
        if require_open_experiment_id is not None:
            row = db.execute(
                "SELECT stage FROM learning_experiments "
                "WHERE experiment_id=? AND ended_at IS NULL",
                (require_open_experiment_id,),
            ).fetchone()
            if (
                require_open_experiment_id != record.experiment_id
                or row is None
                or (
                    require_open_experiment_stage is not None
                    and str(row["stage"]) != require_open_experiment_stage
                )
            ):
                return None
        inserted = insert_record("learning_exposures", record, ignore_duplicates=True)
        insert_filter_candidate_outcomes_batch(outcome_seeds or [])
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
        _require_legacy_experiment_write()
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


def get_nomination(nomination_id: str) -> dict | None:
    return get_record("learning_hypothesis_nominations", nomination_id)


def list_nominations(*, limit: int = 100) -> list[dict]:
    return list_records("learning_hypothesis_nominations", limit=limit)


def get_open_experiment(hypothesis_id: str) -> dict | None:
    if _lab_authority():
        return None
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
    if _lab_authority():
        return None
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
    if _lab_authority():
        return None
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
    if _lab_authority():
        return None
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
    db = get_db()
    register_instant_us(db)
    row = db.execute(
        "SELECT * FROM learning_contribution_verdicts WHERE " + " AND ".join(clauses) + " "
        "ORDER BY aletheia_instant_us(created_at) DESC, "
        "aletheia_instant_us(window_end) DESC, verdict_id DESC LIMIT 1",
        tuple(params),
    ).fetchone()
    return dict(row) if row else None


def list_open_experiments(*, path_id: str | None = None) -> list[dict]:
    if _lab_authority():
        return []
    clauses = ["e.ended_at IS NULL"]
    params: list[str] = []
    if path_id is not None:
        clauses.append("h.path_id=?")
        params.append(path_id)
    rows = get_db().execute(
        "SELECT e.*, ("
        "SELECT x.concurrent_policy_epoch FROM learning_exposures x "
        "WHERE x.experiment_id=e.experiment_id "
        "ORDER BY x.exposed_at DESC, x.rowid DESC LIMIT 1"
        ") AS concurrent_policy_epoch "
        "FROM learning_experiments e JOIN learning_hypotheses h "
        "ON h.hypothesis_id=e.hypothesis_id WHERE " + " AND ".join(clauses) + " "
        "ORDER BY e.created_at, e.experiment_id",
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def get_lifecycle_event_by_dedup_key(dedup_key: str) -> dict | None:
    if _lab_authority():
        return None
    row = get_db().execute(
        "SELECT * FROM learning_lifecycle_events WHERE dedup_key=?", (dedup_key,)
    ).fetchone()
    return dict(row) if row else None


def list_lifecycle_events(experiment_id: str, *, limit: int = 100) -> list[dict]:
    if _lab_authority():
        return []
    rows = get_db().execute(
        "SELECT * FROM learning_lifecycle_events WHERE experiment_id=? OR successor_experiment_id=? "
        "ORDER BY created_at DESC, event_id DESC LIMIT ?",
        (experiment_id, experiment_id, max(1, int(limit))),
    ).fetchall()
    return [dict(row) for row in rows]


def list_unmirrored_lifecycle_events(
    *, limit: int = 100, event_ids: list[str] | None = None,
) -> list[dict]:
    if _lab_authority():
        return []
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
    if _lab_authority():
        return 0
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
    if _lab_authority():
        return None
    row = get_db().execute(
        "SELECT * FROM learning_lifecycle_events "
        "WHERE successor_experiment_id=? AND action='admit_trial' "
        "ORDER BY created_at DESC, event_id DESC LIMIT 1",
        (successor_experiment_id,),
    ).fetchone()
    return dict(row) if row else None


def get_exposure(exposure_id: str) -> dict | None:
    if _lab_authority():
        return None
    row = get_db().execute(
        "SELECT * FROM learning_exposures WHERE exposure_id=?", (exposure_id,)
    ).fetchone()
    return dict(row) if row else None
def get_exposure_by_dedup_key(dedup_key: str) -> dict | None:
    if _lab_authority():
        return None
    row = get_db().execute(
        "SELECT * FROM learning_exposures WHERE dedup_key=?", (dedup_key,)
    ).fetchone()
    return dict(row) if row else None


def linked_settled_exposure_counts(
    experiment_id: str,
    *,
    cost_model_version: str,
    policy_epoch: str | None = None,
) -> dict[str, int]:
    """Count arms backed by prospective exact-version settled outcomes."""
    from datetime import datetime, timezone

    from aletheia.analytics.learning_window import clean_window_cutoff
    from aletheia.learning.outcomes import assess_candidate_outcome

    cutoff = clean_window_cutoff("1970-01-01T00:00:00+00:00")
    db = get_db()
    selected = (
        "o.*, e.assignment AS linked_assignment, "
        "e.candidate_key AS linked_candidate_key, "
        "e.exposed_at AS linked_exposed_at "
    )
    rows = db.execute(
        f"SELECT {selected}FROM universe_forward_outcomes o "
        "JOIN learning_exposures e "
        "ON e.outcome_candidate_key=o.outcome_candidate_key "
        "WHERE o.status='settled' AND o.entry_ts>=? "
        f"AND {complete_outcome_economics_sql('o')} "
        "AND o.cost_model_version=? AND e.experiment_id=? "
        "AND (? IS NULL OR e.concurrent_policy_epoch=?)",
        (cutoff, cost_model_version, experiment_id, policy_epoch, policy_epoch),
    ).fetchall()
    rows += db.execute(
        f"SELECT {selected}FROM filter_candidate_outcomes o "
        "JOIN learning_exposures e "
        "ON e.outcome_candidate_key=o.outcome_candidate_key || ':' || o.horizon "
        "WHERE o.status='settled' AND o.entry_ts>=? "
        f"AND {complete_outcome_economics_sql('o')} "
        "AND o.cost_model_version=? AND e.experiment_id=? "
        "AND (? IS NULL OR e.concurrent_policy_epoch=?)",
        (cutoff, cost_model_version, experiment_id, policy_epoch, policy_epoch),
    ).fetchall()

    as_of = datetime.now(timezone.utc)
    counts: dict[str, set[str]] = {}
    for raw in rows:
        outcome = dict(raw)
        try:
            exposed_at = datetime.fromisoformat(
                str(outcome["linked_exposed_at"]).replace("Z", "+00:00")
            )
            entry_at = datetime.fromisoformat(
                str(outcome["entry_ts"]).replace("Z", "+00:00")
            )
            if exposed_at.tzinfo is None:
                exposed_at = exposed_at.replace(tzinfo=timezone.utc)
            if entry_at.tzinfo is None:
                entry_at = entry_at.replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError):
            continue
        candidate_key = outcome.get("linked_candidate_key")
        if (
            candidate_key is None
            or entry_at.astimezone(timezone.utc) < exposed_at.astimezone(timezone.utc)
            or not assess_candidate_outcome(outcome, as_of_at=as_of).eligible
        ):
            continue
        counts.setdefault(str(outcome["linked_assignment"]), set()).add(
            str(candidate_key)
        )

    return {
        "control": len(counts.get("control", set())),
        "treatment": len(counts.get("treatment", set())),
    }


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
