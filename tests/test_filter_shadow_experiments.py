from __future__ import annotations

from datetime import datetime, timedelta, timezone


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
    from aletheia.analytics.filter_shadow_experiments import (
        register_replay_ready_experiments,
    )
    from aletheia.storage.sqlite import get_db

    started_at = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    replay = _ready_replay(filter_version="fixed-v1")

    first = register_replay_ready_experiments([replay], now=started_at)
    second = register_replay_ready_experiments([replay], now=started_at + timedelta(hours=1))
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
    from aletheia.analytics.filter_shadow_experiments import (
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


def test_shadow_experiment_invalidates_when_production_filter_version_changes():
    from aletheia.analytics.filter_shadow_experiments import (
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
