from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _ready_replay(*, filter_version: str, ttl_sec: int = 3600) -> dict:
    return {
        "read_only": True,
        "gate_only": True,
        "filter_name": "family_ttl",
        "filter_version": filter_version,
        "stage": "paper_ranker",
        "scanner_family": "momentum",
        "direction": "long",
        "market_type": "perp",
        "horizon": "1h",
        "current_ttl_sec": 1800,
        "candidate_cap_reached": False,
        "min_samples": 20,
        "alternatives": [
            {
                "ttl_sec": ttl_sec,
                "newly_admitted_candidates": 24,
                "settled_n": 24,
                "outcome_coverage": 1.0,
                "raw_avg_net_pct": 0.4,
                "shrunk_avg_net_pct": 0.2,
                "readiness": "ready_for_controlled_experiment",
            }
        ],
    }


def _record_ttl_candidate(
    *,
    key: str,
    evaluated_at: datetime,
    decision: str,
    net_return_pct: float,
    ttl_sec: int = 1800,
    lateness_sec: int = 600,
) -> str:
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    expires_at = (
        evaluated_at + timedelta(minutes=10)
        if decision == "passed"
        else evaluated_at - timedelta(seconds=lateness_sec)
    )
    record_filter_decision(
        candidate_key=key,
        candidate_kind="opportunity",
        filter_name="family_ttl",
        stage="paper_ranker",
        decision=decision,
        reason_code=(
            "universe_member_fresh"
            if decision == "passed"
            else "universe_member_expired"
        ),
        symbol="BTC/USDT",
        venue="okx",
        market_type="perp",
        direction="long",
        scanner_family="momentum",
        entry_price_usd=100.0,
        inputs={"expires_at": expires_at.isoformat(), "now": evaluated_at.isoformat()},
        parameters={"ttl_sec": ttl_sec},
        evaluated_at=evaluated_at.isoformat(),
    )
    db = get_db()
    db.execute(
        "UPDATE filter_candidate_outcomes SET status='settled', settled_at=?, "
        "net_return_pct=? WHERE outcome_candidate_key=? AND horizon='1h'",
        (evaluated_at.isoformat(), net_return_pct, key),
    )
    return db.execute(
        "SELECT filter_version FROM filter_decisions WHERE candidate_key=?",
        (key,),
    ).fetchone()[0]


def test_replay_ready_plan_is_registered_once_and_starts_prospectively():
    from aletheia.counterfactual.filter_experiments import (
        register_replay_ready_experiments,
    )
    from aletheia.storage.sqlite import get_db

    started_at = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    replay = _ready_replay(filter_version="fixed-v1")

    first = register_replay_ready_experiments([replay], now=started_at)
    second = register_replay_ready_experiments(
        [_ready_replay(filter_version="fixed-v1", ttl_sec=7200)],
        now=started_at + timedelta(hours=1),
    )
    rows = get_db().execute("SELECT * FROM filter_shadow_experiments").fetchall()

    assert first == {"registered": 1, "already_registered": 0, "skipped": 0}
    assert second == {"registered": 0, "already_registered": 1, "skipped": 0}
    assert len(rows) == 1
    assert rows[0]["started_at"] == started_at.isoformat(timespec="microseconds")
    assert rows[0]["status"] == "collecting"
    assert rows[0]["control_parameter"] == '{"ttl_sec":1800}'
    assert rows[0]["treatment_parameter"] == '{"ttl_sec":3600}'
    assert rows[0]["production_mutation_allowed"] == 0


def test_shadow_evaluation_uses_only_future_first_exposures_and_never_mutates_config(monkeypatch):
    from aletheia.counterfactual.filter_experiments import (
        evaluate_active_shadow_experiments,
        register_replay_ready_experiments,
    )
    from aletheia.storage.sqlite import get_db

    start = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    version = _record_ttl_candidate(
        key="opportunity:historical-version-source",
        # This row exists before registration at the exact same timestamp. A
        # prospective experiment must exclude it with a strict time boundary.
        evaluated_at=start,
        decision="rejected",
        net_return_pct=9.0,
    )
    register_replay_ready_experiments([_ready_replay(filter_version=version)], now=start)

    for index in range(20):
        observed_at = start + timedelta(minutes=index + 1)
        _record_ttl_candidate(
            key=f"opportunity:future-control-{index}",
            evaluated_at=observed_at,
            decision="passed",
            net_return_pct=0.0,
        )
        _record_ttl_candidate(
            key=f"opportunity:future-treatment-{index}",
            evaluated_at=observed_at,
            decision="rejected",
            net_return_pct=0.4,
        )

    import aletheia.config.loader as loader

    save_calls = []
    monkeypatch.setattr(loader, "save_config", lambda *args, **kwargs: save_calls.append(1))
    summary = evaluate_active_shadow_experiments(now=start + timedelta(hours=2))
    experiment = get_db().execute("SELECT * FROM filter_shadow_experiments").fetchone()
    evaluation = get_db().execute("SELECT * FROM filter_shadow_evaluations").fetchone()

    assert summary == {"evaluated": 1, "ready_for_review": 1, "completed_no_support": 0, "invalidated": 0}
    assert experiment["status"] == "ready_for_review"
    assert evaluation["control_candidate_count"] == 20
    assert evaluation["control_settled_n"] == 20
    assert evaluation["treatment_candidate_count"] == 20
    assert evaluation["treatment_settled_n"] == 20
    assert evaluation["treatment_raw_avg_net_pct"] == 0.4
    assert evaluation["disposition"] == "ready_for_review"
    assert save_calls == []


def _ready_observer_replay(*, filter_version: str) -> dict:
    """A replay with three ready alternatives; the least-permissive is (10, 0.7)."""
    return {
        "read_only": True,
        "gate_only": True,
        "filter_name": "observer_pre_filter",
        "filter_version": filter_version,
        "stage": "observer_pre_filter",
        "scanner_family": "momentum",
        "direction": "long",
        "market_type": "perp",
        "horizon": "24h",
        "candidate_cap_reached": False,
        "min_samples": 200,
        "min_coverage": 0.80,
        "min_edge_delta_pct": 0.25,
        "control_parameter": {"rsi_deviation": 12.0, "atr_pct": 0.8},
        "alternatives": [
            {
                "rsi_deviation": 6.0,
                "atr_pct": 0.5,
                "newly_admitted_candidates": 250,
                "readiness": "ready_for_prospective_shadow",
            },
            {
                "rsi_deviation": 10.0,
                "atr_pct": 0.7,
                "newly_admitted_candidates": 210,
                "readiness": "ready_for_prospective_shadow",
            },
            {
                "rsi_deviation": 8.0,
                "atr_pct": 0.7,
                "newly_admitted_candidates": 220,
                "readiness": "no_support",
            },
        ],
    }


def test_observer_pre_filter_replay_registers_least_permissive_winner():
    from aletheia.counterfactual.filter_experiments import (
        register_replay_ready_experiments,
    )
    from aletheia.storage.sqlite import get_db

    started_at = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    replay = _ready_observer_replay(filter_version="obs-fixed-v1")

    summary = register_replay_ready_experiments([replay], now=started_at)
    rows = get_db().execute(
        "SELECT * FROM filter_shadow_experiments "
        "WHERE filter_name='observer_pre_filter'"
    ).fetchall()

    assert summary == {"registered": 1, "already_registered": 0, "skipped": 0}
    assert len(rows) == 1
    row = rows[0]
    # Least-permissive winner: largest rsi_deviation among ready alternatives,
    # tie-broken by largest atr_pct. Here (10.0, 0.7) beats (6.0, 0.5).
    assert row["treatment_parameter"] == '{"atr_pct":0.7,"rsi_deviation":10.0}'
    assert row["control_parameter"] == '{"atr_pct":0.8,"rsi_deviation":12.0}'
    assert row["production_mutation_allowed"] == 0
    # Observer replays declare their own stricter gates; the registration must
    # honor them rather than falling back to the family_ttl 0.05 / 0.70 pair.
    assert row["min_coverage"] == 0.80
    assert row["min_edge_delta_pct"] == 0.25
    assert row["min_samples"] == 200
    assert row["stage"] == "observer_pre_filter"
    assert row["horizon"] == "24h"


def test_observer_replay_without_ready_alternatives_is_skipped():
    from aletheia.counterfactual.filter_experiments import (
        register_replay_ready_experiments,
    )
    from aletheia.storage.sqlite import get_db

    started_at = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    replay = _ready_observer_replay(filter_version="obs-empty-v1")
    for alt in replay["alternatives"]:
        alt["readiness"] = "no_support"

    summary = register_replay_ready_experiments([replay], now=started_at)
    rows = get_db().execute(
        "SELECT COUNT(*) FROM filter_shadow_experiments"
    ).fetchone()

    assert summary == {"registered": 0, "already_registered": 0, "skipped": 1}
    assert rows[0] == 0


def test_ranker_confidence_replay_registers_and_rebuilds_prospectively(monkeypatch):
    from aletheia.counterfactual import filter_experiments
    from aletheia.storage.sqlite import get_db

    start = datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc)
    replay = {
        "filter_name": "ranker_confidence_gate",
        "filter_version": "ranker-fixed-v1",
        "stage": "paper_ranker",
        "scanner_family": "mean_reversion",
        "direction": "long",
        "market_type": "perp",
        "horizon": "4h",
        "current_minimum_confidence": "high",
        "candidate_cap_reached": False,
        "min_samples": 20,
        "alternatives": [
            {
                "minimum_confidence": "speculative",
                "readiness": "ready_for_controlled_experiment",
            },
            {
                "minimum_confidence": "medium",
                "readiness": "ready_for_controlled_experiment",
            },
        ],
    }
    assert filter_experiments.register_replay_ready_experiments(
        [replay], now=start
    )["registered"] == 1

    calls = []
    monkeypatch.setattr(
        filter_experiments,
        "build_ranker_confidence_replay",
        lambda **kwargs: calls.append(kwargs) or {
            "current_pass": {
                "candidate_count": 20, "settled_n": 20,
                "outcome_coverage": 1.0, "raw_avg_net_pct": 0.0,
            },
            "alternatives": [{
                "minimum_confidence": "medium",
                "candidate_count": 20,
                "settled_n": 20,
                "outcome_coverage": 1.0,
                "raw_avg_net_pct": 0.4,
                "shrunk_avg_net_pct": 0.2,
            }],
        },
    )
    summary = filter_experiments.evaluate_active_shadow_experiments(
        now=start + timedelta(hours=5)
    )
    experiment = get_db().execute(
        "SELECT * FROM filter_shadow_experiments"
    ).fetchone()

    assert summary["ready_for_review"] == 1
    assert experiment["production_mutation_allowed"] == 0
    assert experiment["control_parameter"] == '{"minimum_confidence":"high"}'
    assert experiment["treatment_parameter"] == '{"minimum_confidence":"medium"}'
    assert calls[0]["evaluated_after"] == start.isoformat(timespec="microseconds")


def test_shadow_experiment_invalidates_when_production_filter_version_changes():
    from aletheia.counterfactual.filter_experiments import (
        evaluate_active_shadow_experiments,
        register_replay_ready_experiments,
    )
    from aletheia.storage.sqlite import get_db

    start = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    version = _record_ttl_candidate(
        key="opportunity:version-source",
        evaluated_at=start - timedelta(minutes=1),
        decision="passed",
        net_return_pct=0.0,
    )
    register_replay_ready_experiments([_ready_replay(filter_version=version)], now=start)
    changed_version = _record_ttl_candidate(
        key="opportunity:new-version",
        evaluated_at=start + timedelta(minutes=1),
        decision="passed",
        net_return_pct=0.0,
        ttl_sec=1900,
    )
    assert changed_version != version

    summary = evaluate_active_shadow_experiments(now=start + timedelta(hours=1))
    experiment = get_db().execute("SELECT * FROM filter_shadow_experiments").fetchone()
    evaluation = get_db().execute("SELECT * FROM filter_shadow_evaluations").fetchone()

    assert summary["invalidated"] == 1
    assert experiment["status"] == "invalidated"
    assert experiment["invalidation_reason"] == "production_filter_version_changed"
    assert evaluation["disposition"] == "invalidated"


# ─── Observation Lab v1 — ranker_confidence_gate POC (Issue #1072) ────────────


def _ready_ranker_replay(
    *,
    filter_version: str = "ranker-fixed-v1",
    scanner_family: str = "mean_reversion",
    horizon: str = "4h",
    current: str = "high",
    treatment: str = "medium",
) -> dict:
    return {
        "read_only": True,
        "gate_only": True,
        "filter_name": "ranker_confidence_gate",
        "filter_version": filter_version,
        "stage": "paper_ranker",
        "scanner_family": scanner_family,
        "direction": "long",
        "market_type": "perp",
        "horizon": horizon,
        "current_minimum_confidence": current,
        "candidate_cap_reached": False,
        "min_samples": 20,
        "alternatives": [
            {
                "minimum_confidence": treatment,
                "readiness": "ready_for_controlled_experiment",
            },
        ],
    }


def _record_ranker_candidate(
    *,
    key: str,
    evaluated_at: datetime,
    decision: str,
    confidence: str,
    net_return_pct: float | None,
    horizon: str = "4h",
    scanner_family: str = "mean_reversion",
    minimum: str = "high",
    extra_params: dict | None = None,
) -> str:
    """Seed one ranker-confidence first-exposure row + optionally its outcome."""
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.sqlite import get_db

    params: dict = {"minimum": minimum}
    if extra_params:
        params.update(extra_params)
    record_filter_decision(
        candidate_key=key,
        candidate_kind="opportunity",
        filter_name="ranker_confidence_gate",
        stage="paper_ranker",
        decision=decision,
        reason_code=(
            "ranker_confidence_ok" if decision == "passed" else "ranker_confidence_low"
        ),
        symbol="BTC/USDT",
        venue="okx",
        market_type="perp",
        direction="long",
        scanner_family=scanner_family,
        entry_price_usd=100.0,
        inputs={"confidence": confidence},
        parameters=params,
        evaluated_at=evaluated_at.isoformat(),
    )
    db = get_db()
    if net_return_pct is not None:
        db.execute(
            "UPDATE filter_candidate_outcomes SET status='settled', settled_at=?, "
            "net_return_pct=? WHERE outcome_candidate_key=? AND horizon=?",
            (evaluated_at.isoformat(), net_return_pct, key, horizon),
        )
    return db.execute(
        "SELECT filter_version FROM filter_decisions WHERE candidate_key=?",
        (key,),
    ).fetchone()[0]


def _seed_ranker_cohort(
    start: datetime, *, count: int = 20, scanner_family: str = "mean_reversion"
) -> str:
    """Seed `count` passed + `count` rejected first-exposure ranker rows.

    Returns the deterministic ``filter_version`` derived from the shared
    ``{"minimum": "high"}`` parameter snapshot so callers can register a replay
    that lines up with the seeded rows on the legacy live-replay path too.
    """
    version = ""
    for i in range(count):
        when = start + timedelta(minutes=i + 1)
        version = _record_ranker_candidate(
            key=f"opportunity:{scanner_family}:passed-{i}",
            evaluated_at=when,
            decision="passed",
            confidence="high",
            net_return_pct=0.0,
            scanner_family=scanner_family,
        )
        _record_ranker_candidate(
            key=f"opportunity:{scanner_family}:rejected-{i}",
            evaluated_at=when,
            decision="rejected",
            confidence="medium",
            net_return_pct=0.4,
            scanner_family=scanner_family,
        )
    return version


def test_ranker_confidence_unrelated_filter_version_bump_does_not_invalidate():
    from aletheia.counterfactual.filter_experiments import (
        capture_frozen_units_for,
        evaluate_active_shadow_experiments,
        register_replay_ready_experiments,
    )
    from aletheia.observation_lab.units import count_frozen_units
    from aletheia.storage.sqlite import get_db

    start = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    register_replay_ready_experiments([_ready_ranker_replay()], now=start)
    _seed_ranker_cohort(start, count=20)

    experiment = get_db().execute(
        "SELECT * FROM filter_shadow_experiments"
    ).fetchone()
    captured = capture_frozen_units_for(dict(experiment))
    assert captured == 40  # 20 passed + 20 rejected

    # An unrelated master iteration bumps filter_version for a different scope
    # of the same filter (or same scope with an unrelated parameter tweak).
    # This is the exact 'high-freq master' condition Issue #1072 targets.
    for i in range(3):
        _record_ranker_candidate(
            key=f"opportunity:mean_reversion:unrelated-{i}",
            evaluated_at=start + timedelta(hours=1, minutes=i),
            decision="rejected",
            confidence="medium",
            net_return_pct=0.5,
            extra_params={"unrelated_bump": i + 1},  # → different filter_version
        )

    summary = evaluate_active_shadow_experiments(now=start + timedelta(hours=2))
    experiment_after = get_db().execute(
        "SELECT * FROM filter_shadow_experiments"
    ).fetchone()

    assert experiment_after["status"] != "invalidated"
    assert experiment_after["invalidation_reason"] is None
    assert summary["invalidated"] == 0
    # Frozen units survive the version bump — provenance shifts but identity holds.
    assert count_frozen_units(experiment_after["id"]) >= 40


def test_ranker_confidence_adapter_identity_change_invalidates(monkeypatch):
    from aletheia.counterfactual import filter_experiments as fe
    from aletheia.storage.sqlite import get_db

    start = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    fe.register_replay_ready_experiments([_ready_ranker_replay()], now=start)
    _seed_ranker_cohort(start, count=5)  # small — semantics change, no need for gate
    experiment = get_db().execute("SELECT * FROM filter_shadow_experiments").fetchone()
    fe.capture_frozen_units_for(dict(experiment))

    # Owner review §6: adapter/evaluator semantics are versioned. A deliberate bump
    # invalidates the collecting cohort; append-only evaluation history is preserved.
    changed_identity = dict(fe._ADAPTER_CAUSAL_IDENTITY["ranker_confidence_gate"])
    changed_identity["adapter_version"] = "v2"
    monkeypatch.setitem(
        fe._ADAPTER_CAUSAL_IDENTITY, "ranker_confidence_gate", changed_identity
    )

    summary = fe.evaluate_active_shadow_experiments(now=start + timedelta(hours=1))
    experiment_after = get_db().execute(
        "SELECT * FROM filter_shadow_experiments"
    ).fetchone()
    evaluation = get_db().execute(
        "SELECT * FROM filter_shadow_evaluations ORDER BY created_at DESC LIMIT 1"
    ).fetchone()

    assert summary["invalidated"] == 1
    assert experiment_after["status"] == "invalidated"
    assert experiment_after["invalidation_reason"] == "adapter_causal_identity_changed"
    assert evaluation["disposition"] == "invalidated"


def test_ranker_confidence_evaluation_from_frozen_units_matches_live_replay():
    from aletheia.counterfactual.filter_experiments import (
        capture_frozen_units_for,
        evaluate_active_shadow_experiments,
        register_replay_ready_experiments,
    )
    from aletheia.storage.sqlite import get_db

    start = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    # Seed first so both live-replay (which filters by ``filter_version``) and
    # the frozen path see the exact same 25 passed + 25 rejected rows.
    version = _seed_ranker_cohort(start, count=25)
    register_replay_ready_experiments(
        [_ready_ranker_replay(filter_version=version)], now=start
    )

    # First evaluation: no frozen units yet, uses the live replay path.
    evaluate_active_shadow_experiments(now=start + timedelta(hours=1))
    live_eval = dict(get_db().execute(
        "SELECT * FROM filter_shadow_evaluations ORDER BY created_at DESC LIMIT 1"
    ).fetchone())

    # Freeze the exact same cohort, then re-evaluate through the frozen path.
    experiment = get_db().execute(
        "SELECT * FROM filter_shadow_experiments"
    ).fetchone()
    assert capture_frozen_units_for(dict(experiment)) == 50
    evaluate_active_shadow_experiments(now=start + timedelta(hours=2))
    frozen_eval = dict(get_db().execute(
        "SELECT * FROM filter_shadow_evaluations ORDER BY created_at DESC LIMIT 1"
    ).fetchone())

    # Owner review §9 Evidence Equivalence — same frozen inputs must produce
    # the same counts, coverage, effect, and disposition.
    for field in (
        "control_candidate_count", "control_settled_n",
        "control_outcome_coverage", "control_raw_avg_net_pct",
        "treatment_candidate_count", "treatment_settled_n",
        "treatment_outcome_coverage", "treatment_raw_avg_net_pct",
        "treatment_shrunk_avg_net_pct", "edge_delta_pct", "disposition",
    ):
        assert frozen_eval[field] == live_eval[field], (
            f"{field} diverged: frozen={frozen_eval[field]} live={live_eval[field]}"
        )


def test_two_ranker_experiments_collect_independently():
    from aletheia.counterfactual.filter_experiments import (
        capture_frozen_units_for,
        register_replay_ready_experiments,
    )
    from aletheia.observation_lab.units import list_frozen_units
    from aletheia.storage.sqlite import get_db

    start = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    register_replay_ready_experiments(
        [_ready_ranker_replay(scanner_family="mean_reversion")], now=start
    )
    register_replay_ready_experiments(
        [_ready_ranker_replay(scanner_family="momentum")], now=start
    )
    _seed_ranker_cohort(start, count=10, scanner_family="mean_reversion")
    _seed_ranker_cohort(start, count=7, scanner_family="momentum")

    rows = get_db().execute(
        "SELECT * FROM filter_shadow_experiments "
        "WHERE filter_name='ranker_confidence_gate' ORDER BY scanner_family"
    ).fetchall()
    assert len(rows) == 2
    a, b = dict(rows[0]), dict(rows[1])
    assert capture_frozen_units_for(a) == 20  # 10 pass + 10 rej
    assert capture_frozen_units_for(b) == 14  # 7 pass + 7 rej

    # Disjoint frozen sets — a candidate belongs to at most one Observation Lab
    # cohort because scanner_family scopes them apart.
    keys_a = {u["outcome_candidate_key"] for u in list_frozen_units(a["id"])}
    keys_b = {u["outcome_candidate_key"] for u in list_frozen_units(b["id"])}
    assert keys_a and keys_b
    assert keys_a.isdisjoint(keys_b)


def test_open_readonly_snapshot_rejects_writes(tmp_path):
    """Owner review §5: production RO boundary must be physical, not conventional."""
    import sqlite3

    from aletheia.storage.sqlite import open_readonly_snapshot

    # Build a real file-backed DB with the frozen-units schema so the RO check
    # exercises the same table the Lab writes to. Tests otherwise run against
    # ``:memory:`` where a second connection can't see the schema.
    db_file = tmp_path / "readonly-snapshot.db"
    writer = sqlite3.connect(str(db_file))
    try:
        writer.execute(
            "CREATE TABLE filter_shadow_frozen_units ("
            "id TEXT PRIMARY KEY, experiment_id TEXT, outcome_candidate_key TEXT, "
            "decision TEXT, input_snapshot TEXT, parameter_snapshot TEXT, "
            "first_evaluated_at TEXT, first_seen_at TEXT)"
        )
        writer.commit()
    finally:
        writer.close()

    conn = open_readonly_snapshot(db_file)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO filter_shadow_frozen_units "
                "(id, experiment_id, outcome_candidate_key, decision, "
                "input_snapshot, parameter_snapshot, first_evaluated_at, first_seen_at) "
                "VALUES ('ro-x','ro-x','ro-x','passed','{}','{}','2026-08-17','2026-08-17')"
            )
    finally:
        conn.close()


def test_lab_db_is_physically_isolated_from_app_db(monkeypatch, tmp_path):
    """Lab writes must never land in ``app.db`` — physical file isolation.

    Issue #1072 owner review §5: reads from Production go through the RO
    boundary; the Lab's own writes go to a separate file so unrelated
    Production migrations cannot silently rewrite frozen units.
    """
    import aletheia.config.loader as loader
    from aletheia.observation_lab.db import lab_db_path, reset_lab_db
    from aletheia.observation_lab.units import count_frozen_units, insert_frozen_units

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    reset_lab_db()

    lab_path = lab_db_path()
    assert lab_path.is_absolute()
    assert lab_path.parent.name == "observation-lab"
    assert lab_path.name == "lab.db"
    # Sibling of app.db, not the same file.
    assert lab_path.parent != tmp_path
    assert lab_path != tmp_path / "app.db"

    inserted = insert_frozen_units(
        "obs-isolation",
        [{
            "id": "u1",
            "outcome_candidate_key": "k1",
            "decision": "passed",
            "input_snapshot": "{}",
            "parameter_snapshot": "{}",
            "first_evaluated_at": "2026-08-17T00:00:00+00:00",
            "source_runtime_commit": None,
            "first_seen_at": "2026-08-17T00:00:00+00:00",
        }],
    )
    assert inserted == 1
    assert count_frozen_units("obs-isolation") == 1
    assert lab_path.is_file()

    reset_lab_db()


def test_lab_db_open_readonly_snapshot_rejects_writes(monkeypatch, tmp_path):
    """Same physical-RO guarantee applies to lab.db."""
    import sqlite3

    import aletheia.config.loader as loader
    from aletheia.observation_lab.db import lab_db_path, reset_lab_db
    from aletheia.observation_lab.units import insert_frozen_units
    from aletheia.storage.sqlite import open_readonly_snapshot

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    reset_lab_db()
    # Force the schema to materialize before we open the RO handle.
    insert_frozen_units(
        "obs-ro",
        [{
            "id": "u-ro",
            "outcome_candidate_key": "k-ro",
            "decision": "passed",
            "input_snapshot": "{}",
            "parameter_snapshot": "{}",
            "first_evaluated_at": "2026-08-17T00:00:00+00:00",
            "source_runtime_commit": None,
            "first_seen_at": "2026-08-17T00:00:00+00:00",
        }],
    )

    conn = open_readonly_snapshot(lab_db_path())
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO filter_shadow_frozen_units "
                "(id, experiment_id, outcome_candidate_key, decision, "
                "input_snapshot, parameter_snapshot, first_evaluated_at, first_seen_at) "
                "VALUES ('bad','bad','bad','passed','{}','{}','2026-08-17','2026-08-17')"
            )
    finally:
        conn.close()
        reset_lab_db()
