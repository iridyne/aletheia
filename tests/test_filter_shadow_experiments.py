from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aletheia.analytics.learning_window import CLEAN_WINDOW_ANCHOR_ISO


def _anchor(**delta) -> datetime:
    """A start time inside the clean window.

    Shadow evaluation only counts exposures at or after the clean-window anchor,
    and that anchor moves whenever attribution integrity is re-anchored. A start
    written as an absolute literal silently falls out of the window at the next
    re-anchor: the cohort reads as empty and the verdict degrades to
    `insufficient_data`. Anchoring keeps each test's relative spacing while staying
    inside the window.
    """
    return datetime.fromisoformat(CLEAN_WINDOW_ANCHOR_ISO) + timedelta(**delta)


@pytest.fixture(autouse=True)
def lab_authority():
    from extensions.observation_lab.canonical import set_authority_source

    set_authority_source("lab", actor="test", reason="filter-shadow fixture")


def _evaluate_active(*, now) -> int:
    """Evaluate every active filter-shadow root through the live Lab evaluator."""
    from extensions.observation_lab.evaluator import evaluate_observation
    from extensions.observation_lab.filter_shadow import list_filter_shadow_observations

    roots = list_filter_shadow_observations(status="active", limit=1000) or []
    for root in roots:
        evaluate_observation(str(root["observation_id"]), int(root["generation"]), as_of_at=now)
    return len(roots)


def test_shared_first_exposure_orders_same_second_by_exact_instant():
    from aletheia.analytics.filter_evidence import FIRST_EXPOSURE_CTE_SQL
    from aletheia.storage.sqlite import get_db, register_instant_us

    db = get_db()
    register_instant_us(db)
    rows = [
        ("newer", "newer", "newer", "rejected", "2026-08-30T10:00:00.900000+00:00"),
        ("older", "older", "older", "passed", "2026-08-30T10:00:00.100000+00:00"),
    ]
    db.executemany(
        "INSERT INTO filter_decisions "
        "(id, dedup_key, candidate_key, candidate_kind, symbol, market_type, "
        "filter_name, stage, decision, reason_code, filter_version, "
        "outcome_candidate_key, evaluated_at) "
        "VALUES (?, ?, ?, 'opportunity', 'BTC/USDT', 'perp', "
        "'ranker_confidence_gate', 'paper_ranker', ?, 'test', 'v1', "
        "'shared-candidate', ?)",
        rows,
    )

    first = db.execute(
        FIRST_EXPOSURE_CTE_SQL
        + "SELECT decision, first_evaluated_at FROM ranked_filter_decisions "
        "WHERE exposure_rank = 1"
    ).fetchone()

    assert dict(first) == {
        "decision": "passed",
        "first_evaluated_at": "2026-08-30T10:00:00.100000+00:00",
    }


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
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION

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
        "UPDATE filter_candidate_outcomes SET status='settled', due_at=?, settled_at=?, "
        "directional_return_pct=?, exit_price_usd=?, estimated_fee_pct=0.1, "
        "estimated_spread_slippage_pct=0.1, estimated_funding_pct=0.0, "
        "net_return_pct=?, min_tradable_edge_pct=0.05, "
        "settlement_price_source='horizon_ohlcv', settlement_lag_sec=0, "
        "cost_model_version=? "
        "WHERE outcome_candidate_key=? AND horizon='1h'",
        (
            evaluated_at.isoformat(), evaluated_at.isoformat(), net_return_pct + 0.2,
            100.0 + net_return_pct + 0.2,
            net_return_pct, COST_MODEL_VERSION, key,
        ),
    )
    return db.execute(
        "SELECT filter_version FROM filter_decisions WHERE candidate_key=?",
        (key,),
    ).fetchone()[0]


def test_replay_ready_plan_is_registered_once_and_starts_prospectively():
    from extensions.counterfactual.filter_experiments import register_replay_ready_experiments
    from extensions.observation_lab.filter_shadow import list_filter_shadow_observations

    started_at = _anchor(minutes=1)
    first = register_replay_ready_experiments([_ready_replay(filter_version="fixed-v1")], now=started_at)
    second = register_replay_ready_experiments(
        [_ready_replay(filter_version="fixed-v1", ttl_sec=7200)], now=started_at + timedelta(hours=1),
    )
    rows = list_filter_shadow_observations()
    assert first == {"registered": 1, "already_registered": 0, "skipped": 0}
    assert second == {"registered": 0, "already_registered": 1, "skipped": 0}
    assert len(rows) == 1
    assert datetime.fromisoformat(rows[0]["started_at"]) == started_at
    assert rows[0]["control_parameter"] == {"ttl_sec": 1800}
    assert rows[0]["treatment_parameter"] == {"ttl_sec": 3600}



def test_shadow_evaluation_uses_only_future_first_exposures_and_never_mutates_config(monkeypatch):
    from extensions.counterfactual.filter_experiments import register_replay_ready_experiments
    from extensions.observation_lab.filter_shadow import list_filter_shadow_observations

    start = _anchor(minutes=1)
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
    _evaluate_active(now=start + timedelta(hours=2))
    experiment = list_filter_shadow_observations()[0]
    evaluation = experiment["latest_evaluation"]

    assert experiment["status"] == "ready_for_review"
    assert evaluation["control_candidate_count"] == 20
    assert evaluation["control_settled_n"] == 20
    assert evaluation["treatment_candidate_count"] == 20
    assert evaluation["treatment_settled_n"] == 20
    assert evaluation["treatment_raw_avg_net_pct"] == 0.4
    assert evaluation["disposition"] == "ready_for_review"
    assert save_calls == []


def test_lab_filter_shadow_captures_and_evaluates_canonical_exposures():
    from extensions.counterfactual.filter_experiments import (
        build_filter_shadow_experiment_report,
        register_replay_ready_experiments,
    )
    from extensions.observation_lab.canonical import set_authority_source
    from extensions.observation_lab.db import get_lab_db, reset_lab_db
    from extensions.observation_lab.evaluator import evaluate_observation
    from aletheia.storage.sqlite import get_db

    reset_lab_db()
    set_authority_source("lab", actor="owner", reason="filter-shadow-canonical-test")
    start = _anchor(minutes=1)
    version = _record_ttl_candidate(
        key="opportunity:lab-filter-history",
        evaluated_at=start,
        decision="rejected",
        net_return_pct=9.0,
    )
    assert register_replay_ready_experiments(
        [_ready_replay(filter_version=version)], now=start,
    ) == {"registered": 1, "already_registered": 0, "skipped": 0}
    # A later decision for the same candidate is still not a prospective first
    # exposure: the pre-registration row owns the immutable identity.
    _record_ttl_candidate(
        key="opportunity:lab-filter-history",
        evaluated_at=start + timedelta(minutes=1),
        decision="passed",
        net_return_pct=9.0,
    )
    _record_ttl_candidate(
        key="opportunity:lab-filter-no-trigger",
        evaluated_at=start + timedelta(minutes=1),
        decision="rejected",
        net_return_pct=0.0,
        lateness_sec=3_600,
    )
    from aletheia.analytics.filter_evidence import record_filter_decision

    record_filter_decision(
        candidate_key="opportunity:lab-filter-invalid",
        candidate_kind="opportunity",
        filter_name="family_ttl",
        stage="paper_ranker",
        decision="rejected",
        reason_code="malformed_snapshot",
        symbol="BTC/USDT",
        venue="okx",
        market_type="perp",
        direction="long",
        scanner_family="momentum",
        entry_price_usd=100.0,
        inputs={},
        parameters={"ttl_sec": 1_800},
        evaluated_at=(start + timedelta(minutes=1)).isoformat(),
    )

    for index in range(20):
        observed_at = start + timedelta(minutes=index + 1)
        _record_ttl_candidate(
            key=f"opportunity:lab-control-{index}",
            evaluated_at=observed_at,
            decision="passed",
            net_return_pct=0.0,
        )
        _record_ttl_candidate(
            key=f"opportunity:lab-treatment-{index}",
            evaluated_at=observed_at,
            decision="rejected",
            net_return_pct=0.4,
        )

    root = dict(get_lab_db().execute(
        "SELECT * FROM observation_windows WHERE kind='filter_shadow'"
    ).fetchone())
    evaluated = evaluate_observation(
        root["observation_id"], 1, as_of_at=start + timedelta(hours=2),
    )
    assert evaluated["available"] is True
    assert evaluated["verdict"] == "positive"
    assert evaluated["evaluations"][0]["disposition"] == "ready_for_review"
    assert evaluated["evaluations"][0]["no_trigger_unit_count"] == 1
    assert evaluated["evaluations"][0]["invalid_unit_count"] == 1
    repeated = evaluate_observation(
        root["observation_id"], 1, as_of_at=start + timedelta(hours=2),
    )
    assert repeated["evaluations"][0]["evaluation_id"] == evaluated["evaluations"][0]["evaluation_id"]
    assert get_lab_db().execute(
        "SELECT COUNT(*) FROM observation_exposures WHERE observation_id=?",
        (root["observation_id"],),
    ).fetchone()[0] == 40
    assert get_lab_db().execute(
        "SELECT COUNT(*) FROM observation_units WHERE observation_id=? AND admission='admitted'",
        (root["observation_id"],),
    ).fetchone()[0] == 40
    assert get_lab_db().execute(
        "SELECT COUNT(*) FROM observation_retention_claims "
        "WHERE observation_id=? AND status='active'",
        (root["observation_id"],),
    ).fetchone()[0] == 40
    assert get_db().execute(
        "SELECT COUNT(*) FROM filter_shadow_evaluations"
    ).fetchone()[0] == 0
    report = build_filter_shadow_experiment_report()
    assert report[0]["observation_id"] == root["observation_id"]
    assert report[0]["latest_evaluation"]["disposition"] == "ready_for_review"

    # The scheduled Lab pass must evaluate the canonical root too; a Lab
    # authority branch that merely reports "skipped" would leave the active
    # filter-shadow generation without a lifecycle/evidence advance.
    from extensions.observation_lab.runner import run_lab_windows

    runner_result = run_lab_windows()
    filter_window = runner_result["results"]["filter_shadow_evaluate"]
    assert filter_window["status"] == "canonical_lab_authority"
    assert filter_window["evaluation"]["evaluated"] == 1
    assert filter_window["evaluation"]["ready_for_review"] == 1


def test_lab_filter_shadow_registration_reports_idempotent_repeat():
    from extensions.counterfactual.filter_experiments import register_replay_ready_experiments
    from extensions.observation_lab.canonical import set_authority_source
    from extensions.observation_lab.db import reset_lab_db

    reset_lab_db()
    set_authority_source("lab", actor="owner", reason="filter-shadow-registration-test")
    start = _anchor(minutes=1)
    replay = _ready_replay(filter_version="lab-repeat-v1")

    assert register_replay_ready_experiments([replay], now=start) == {
        "registered": 1,
        "already_registered": 0,
        "skipped": 0,
    }
    assert register_replay_ready_experiments(
        [replay], now=start + timedelta(minutes=1),
    ) == {
        "registered": 0,
        "already_registered": 1,
        "skipped": 0,
    }


def test_lab_filter_shadow_respects_half_open_collect_interval():
    from extensions.observation_lab.canonical import set_authority_source
    from extensions.observation_lab.canonical import register_observation_spec
    from extensions.observation_lab.db import get_lab_db, reset_lab_db
    from extensions.observation_lab.filter_shadow import capture_filter_shadow_exposures

    reset_lab_db()
    set_authority_source("lab", actor="owner", reason="filter-shadow-half-open-test")
    start = _anchor(minutes=1)
    version = _record_ttl_candidate(
        key="opportunity:at-collect-until",
        evaluated_at=start + timedelta(hours=1),
        decision="passed",
        net_return_pct=0.0,
    )
    register_observation_spec(
        {
            "filter_name": "family_ttl",
            "filter_version": version,
            "stage": "paper_ranker",
            "scanner_family": "momentum",
            "direction": "long",
            "market_type": "perp",
            "horizon": "1h",
            "control": {"ttl_sec": 1800},
            "treatment": {"ttl_sec": 3600},
            "min_samples": 20,
        },
        actor="owner",
        started_at=start.isoformat(),
        collect_until=(start + timedelta(hours=1)).isoformat(),
        finalize_not_before=(start + timedelta(hours=1)).isoformat(),
    )
    root = dict(get_lab_db().execute(
        "SELECT * FROM observation_windows WHERE kind='filter_shadow'"
    ).fetchone())
    result = capture_filter_shadow_exposures(
        root["observation_id"], 1,
        as_of_at=datetime.fromisoformat(root["collect_until"]),
    )
    assert result["available"] is True
    assert result["units_inserted"] == 0
    assert get_lab_db().execute(
        "SELECT COUNT(*) FROM observation_units WHERE observation_id=?",
        (root["observation_id"],),
    ).fetchone()[0] == 0


def test_lab_filter_shadow_rejects_malformed_source_time():
    from extensions.observation_lab.canonical import register_observation_spec, set_authority_source
    from extensions.observation_lab.db import get_lab_db, reset_lab_db
    from extensions.observation_lab.filter_shadow import capture_filter_shadow_exposures
    from aletheia.storage.sqlite import get_db

    reset_lab_db()
    set_authority_source("lab", actor="owner", reason="filter-shadow-invalid-time-test")
    start = _anchor(minutes=1)
    version = _record_ttl_candidate(
        key="opportunity:malformed-source-time",
        evaluated_at=start + timedelta(minutes=1),
        decision="passed",
        net_return_pct=0.0,
    )
    get_db().execute(
        "UPDATE filter_decisions SET evaluated_at='not-a-time' WHERE candidate_key=?",
        ("opportunity:malformed-source-time",),
    )
    register_observation_spec(
        {
            "filter_name": "family_ttl",
            "filter_version": version,
            "stage": "paper_ranker",
            "scanner_family": "momentum",
            "direction": "long",
            "market_type": "perp",
            "horizon": "1h",
            "control": {"ttl_sec": 1800},
            "treatment": {"ttl_sec": 3600},
        },
        actor="owner",
        started_at=start.isoformat(),
    )
    root = dict(get_lab_db().execute(
        "SELECT * FROM observation_windows WHERE kind='filter_shadow'"
    ).fetchone())
    result = capture_filter_shadow_exposures(root["observation_id"], 1)
    assert result == {
        "available": False,
        "reason": "filter_shadow_source_time_unavailable",
        "observation_id": root["observation_id"],
        "generation": 1,
    }


def test_lab_filter_shadow_rejects_malformed_observation_start():
    from extensions.observation_lab import filter_shadow

    with pytest.raises(ValueError, match="observation_started_at_unavailable"):
        filter_shadow._source_rows(
            spec={
                "filter_name": "ranker_confidence_gate",
                "stage": "paper_ranker",
                "scanner_family": "momentum",
                "direction": "long",
                "market_type": "perp",
            },
            root={
                "started_at": "not-a-time",
                "collect_until": "2026-08-20T13:00:00+00:00",
            },
            as_of_at="2026-08-20T12:30:00+00:00",
        )


def test_lab_filter_shadow_rejects_empty_active_observation_interval():
    from extensions.observation_lab import filter_shadow

    with pytest.raises(ValueError, match="observation_interval_unavailable"):
        filter_shadow._source_rows(
            spec={
                "filter_name": "ranker_confidence_gate",
                "stage": "paper_ranker",
                "scanner_family": "momentum",
                "direction": "long",
                "market_type": "perp",
            },
            root={
                "status": "active",
                "started_at": "2026-08-20T12:00:00+00:00",
                "collect_until": "2026-08-20T12:00:00+00:00",
            },
            as_of_at="2026-08-20T12:30:00+00:00",
        )


def test_lab_observer_filter_shadow_preserves_union_and_companion_gates():
    from aletheia.analytics.filter_evidence import record_filter_decision
    from extensions.counterfactual.filter_experiments import register_replay_ready_experiments
    from extensions.observation_lab.canonical import set_authority_source
    from extensions.observation_lab.db import get_lab_db, reset_lab_db
    from extensions.observation_lab.evaluator import evaluate_observation
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION

    reset_lab_db()
    set_authority_source("lab", actor="owner", reason="observer-filter-shadow-canonical-test")
    start = _anchor(minutes=1)

    def add_candidate(key: str, when: datetime, decision: str, inputs: dict) -> str:
        record_filter_decision(
            candidate_key=key,
            candidate_kind="opportunity",
            filter_name="observer_pre_filter",
            stage="observer_pre_filter",
            decision=decision,
            reason_code="test",
            symbol=f"BTC/{key[-1]}/USDT",
            venue="okx",
            market_type="perp",
            direction="long",
            scanner_family="momentum",
            entry_price_usd=100.0,
            inputs=inputs,
            parameters={"rsi_deviation": 12.0, "atr_pct": 0.8},
            evaluated_at=when.isoformat(),
        )
        return get_db().execute(
            "SELECT filter_version FROM filter_decisions WHERE candidate_key=?",
            (key,),
        ).fetchone()[0]

    version = add_candidate(
        "opportunity:observer-history", start, "passed", {"rsi": 50.0, "atr_pct": 0.1},
    )
    replay = _ready_observer_replay(filter_version=version)
    replay["min_samples"] = 20
    assert register_replay_ready_experiments([replay], now=start)["registered"] == 1
    db = get_db()
    for index in range(20):
        when = start + timedelta(days=index, minutes=1)
        control_key = f"opportunity:observer-control-{index}"
        treatment_key = f"opportunity:observer-treatment-{index}"
        add_candidate(control_key, when, "passed", {"rsi": 50.0, "atr_pct": 0.1})
        add_candidate(treatment_key, when, "rejected", {"rsi": 61.0, "atr_pct": 0.1})
        for key, net in ((control_key, 0.0), (treatment_key, 0.8)):
                for horizon in ("1h", "4h", "24h"):
                    outcome = db.execute(
                    "SELECT due_at, entry_price_usd FROM filter_candidate_outcomes "
                        "WHERE outcome_candidate_key=? AND horizon=?",
                        (key, horizon),
                    ).fetchone()
                    due = datetime.fromisoformat(outcome[0])
                    db.execute(
                        "UPDATE filter_candidate_outcomes SET status='settled', settled_at=?, "
                    "directional_return_pct=?, exit_price_usd=?, estimated_fee_pct=0.1, "
                    "estimated_spread_slippage_pct=0.1, estimated_funding_pct=0.0, "
                    "net_return_pct=?, min_tradable_edge_pct=0.05, "
                    "settlement_price_source='horizon_ohlcv', settlement_lag_sec=0, "
                    "cost_model_version=? "
                        "WHERE outcome_candidate_key=? AND horizon=?",
                        (
                            (due + timedelta(minutes=1)).isoformat(), net + 0.2,
                            100.0 + net + 0.2, net, COST_MODEL_VERSION, key, horizon,
                        ),
                    )
    root = dict(get_lab_db().execute(
        "SELECT * FROM observation_windows WHERE kind='filter_shadow'"
    ).fetchone())
    evaluated = evaluate_observation(
        root["observation_id"], 1, as_of_at=start + timedelta(days=30),
    )
    evidence = evaluated["evaluations"][0]
    assert evaluated["verdict"] == "positive"
    assert evidence["newly_admitted"]["settled_n"] == 20
    assert evidence["treatment"]["settled_n"] == 40
    assert evidence["companion_horizons_ok"] is True
    assert evidence["bootstrap_delta_lcb_pct"] > 0


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
    from extensions.counterfactual.filter_experiments import (
        register_replay_ready_experiments,
    )
    from extensions.observation_lab.filter_shadow import list_filter_shadow_observations

    started_at = _anchor(minutes=1)
    replay = _ready_observer_replay(filter_version="obs-fixed-v1")

    summary = register_replay_ready_experiments([replay], now=started_at)
    rows = list_filter_shadow_observations()

    assert summary == {"registered": 1, "already_registered": 0, "skipped": 0}
    assert len(rows) == 1
    row = rows[0]
    # Least-permissive winner: largest rsi_deviation among ready alternatives,
    # tie-broken by largest atr_pct. Here (10.0, 0.7) beats (6.0, 0.5).
    assert row["treatment_parameter"] == {"atr_pct": 0.7, "rsi_deviation": 10.0}
    assert row["control_parameter"] == {"atr_pct": 0.8, "rsi_deviation": 12.0}
    # Observer replays declare their own stricter gates; the registration must
    # honor them rather than falling back to the family_ttl 0.05 / 0.70 pair.
    assert row["min_coverage"] == 0.80
    assert row["min_edge_delta_pct"] == 0.25
    assert row["min_samples"] == 200
    assert row["stage"] == "observer_pre_filter"
    assert row["horizon"] == "24h"


def test_observer_replay_without_ready_alternatives_is_skipped():
    from extensions.counterfactual.filter_experiments import (
        register_replay_ready_experiments,
    )
    from extensions.observation_lab.filter_shadow import list_filter_shadow_observations

    started_at = _anchor(minutes=1)
    replay = _ready_observer_replay(filter_version="obs-empty-v1")
    for alt in replay["alternatives"]:
        alt["readiness"] = "no_support"

    summary = register_replay_ready_experiments([replay], now=started_at)
    rows = list_filter_shadow_observations()

    assert summary == {"registered": 0, "already_registered": 0, "skipped": 1}
    assert rows == []


def test_ranker_confidence_replay_selects_least_permissive_threshold():
    from extensions.counterfactual.filter_experiments import register_replay_ready_experiments
    from extensions.observation_lab.filter_shadow import list_filter_shadow_observations

    replay = _ready_ranker_replay()
    replay["alternatives"].append({"minimum_confidence": "speculative", "readiness": "ready_for_controlled_experiment"})
    register_replay_ready_experiments([replay])
    row = list_filter_shadow_observations()[0]
    assert row["control_parameter"] == {"minimum_confidence": "high"}
    assert row["treatment_parameter"] == {"minimum_confidence": "medium"}



def test_shadow_experiment_invalidates_when_production_filter_version_changes():
    from extensions.counterfactual.filter_experiments import register_replay_ready_experiments
    from extensions.observation_lab.filter_shadow import list_filter_shadow_observations

    start = _anchor(minutes=1)
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

    _evaluate_active(now=start + timedelta(hours=1))
    experiment = list_filter_shadow_observations()[0]
    evaluation = experiment["latest_evaluation"]

    assert experiment["status"] == "invalidated"
    assert evaluation["reason"] == "production_filter_version_changed"
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
            "net_return_pct=?, settlement_price_source='horizon_ohlcv', "
            "settlement_lag_sec=0 WHERE outcome_candidate_key=? AND horizon=?",
            (evaluated_at.isoformat(), net_return_pct, key, horizon),
        )
    return db.execute(
        "SELECT filter_version FROM filter_decisions WHERE candidate_key=?",
        (key,),
    ).fetchone()[0]


def test_frozen_units_use_exact_first_exposure_within_same_second():
    from extensions.counterfactual.filter_experiments import (
        register_replay_ready_experiments,
    )
    from extensions.observation_lab.filter_shadow import capture_filter_shadow_exposures
    from extensions.observation_lab.db import get_lab_db
    from aletheia.storage.sqlite import get_db

    # Registration must precede both exposures, and the two exposures must land in
    # the same second so this test still exercises sub-second first-exposure
    # ordering. Both are derived from the anchor so the pair moves together.
    started_at = _anchor(minutes=1)
    newer_at = _anchor(minutes=1, seconds=1, microseconds=900_000).isoformat()
    older_at = _anchor(minutes=1, seconds=1, microseconds=100_000).isoformat()
    register_replay_ready_experiments([_ready_ranker_replay()], now=started_at)
    db = get_db()
    rows = [
        (
            "frozen-newer", "frozen-newer", "frozen-newer", "rejected",
            newer_at, '{"minimum_confidence":"high"}',
        ),
        (
            "frozen-older", "frozen-older", "frozen-older", "passed",
            older_at, '{"minimum_confidence":"high"}',
        ),
    ]
    db.executemany(
        "INSERT INTO filter_decisions "
        "(id, dedup_key, candidate_key, candidate_kind, symbol, market_type, "
        "direction, scanner_family, filter_name, stage, decision, reason_code, "
        "filter_version, outcome_candidate_key, evaluated_at, parameter_snapshot) "
        "VALUES (?, ?, ?, 'opportunity', 'BTC/USDT', 'perp', 'long', "
        "'mean_reversion', 'ranker_confidence_gate', 'paper_ranker', ?, "
        "'test', 'v1', 'shared-frozen-candidate', ?, ?)",
        rows,
    )
    root = get_lab_db().execute("SELECT observation_id FROM observation_windows").fetchone()[0]
    assert capture_filter_shadow_exposures(root, 1)["units_inserted"] == 1
    units = [dict(row) for row in get_lab_db().execute(
        "SELECT assignment, exposed_at FROM observation_exposures WHERE observation_id=?", (root,),
    )]
    assert [(unit["assignment"], unit["exposed_at"]) for unit in units] == [
        ("control", older_at)
    ]


def _seed_ranker_cohort(
    start: datetime, *, count: int = 20, scanner_family: str = "mean_reversion"
) -> str:
    """Seed `count` passed + `count` rejected first-exposure ranker rows.

    Returns the deterministic ``filter_version`` derived from the shared
    ``{"minimum": "high"}`` parameter snapshot for canonical Lab registration.
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
    from extensions.counterfactual.filter_experiments import register_replay_ready_experiments
    from extensions.observation_lab.filter_shadow import capture_filter_shadow_exposures, list_filter_shadow_observations

    start = _anchor(minutes=1)
    register_replay_ready_experiments([_ready_ranker_replay()], now=start)
    _seed_ranker_cohort(start, count=20)

    experiment = list_filter_shadow_observations()[0]
    captured = capture_filter_shadow_exposures(experiment["observation_id"], 1)["exposures_inserted"]
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

    _evaluate_active(now=start + timedelta(hours=2))
    experiment_after = list_filter_shadow_observations()[0]

    assert experiment_after["status"] != "invalidated"
    # Frozen units survive the version bump — provenance shifts but identity holds.
    assert experiment_after["frozen_units"] == 43


def test_ranker_confidence_parameter_snapshot_change_invalidates_before_freeze():
    from extensions.counterfactual.filter_experiments import register_replay_ready_experiments
    from extensions.observation_lab.filter_shadow import capture_filter_shadow_exposures, list_filter_shadow_observations
    from extensions.observation_lab.db import get_lab_db

    start = _anchor(minutes=1)
    register_replay_ready_experiments([_ready_ranker_replay()], now=start)
    _record_ranker_candidate(
        key="opportunity:mean_reversion:compatible",
        evaluated_at=start + timedelta(minutes=1),
        decision="passed",
        confidence="high",
        net_return_pct=0.0,
        minimum="high",
    )
    _record_ranker_candidate(
        key="opportunity:mean_reversion:changed-minimum",
        evaluated_at=start + timedelta(minutes=2),
        decision="rejected",
        confidence="medium",
        net_return_pct=0.4,
        minimum="medium",
    )
    experiment = list_filter_shadow_observations()[0]

    # The incompatible row is not admitted to the immutable frozen cohort.
    capture = capture_filter_shadow_exposures(experiment["observation_id"], 1)
    assert capture["verdict"] == "invalid"
    assert get_lab_db().execute(
        "SELECT COUNT(*) FROM observation_exposures WHERE candidate_key=?",
        ("opportunity:mean_reversion:changed-minimum",),
    ).fetchone()[0] == 0

    after = list_filter_shadow_observations()[0]
    assert after["status"] == "invalidated"
    assert after["latest_evaluation"]["reason"] == "adapter_causal_input_changed"
    assert _evaluate_active(now=start + timedelta(hours=1)) == 0
    assert list_filter_shadow_observations()[0]["latest_evaluation"]["evaluation_id"] == after["latest_evaluation"]["evaluation_id"]


def test_ranker_confidence_malformed_parameter_snapshot_invalidates():
    from extensions.counterfactual import filter_experiments as fe
    from extensions.observation_lab.filter_shadow import list_filter_shadow_observations
    from aletheia.storage.sqlite import get_db

    start = _anchor(minutes=1)
    fe.register_replay_ready_experiments([_ready_ranker_replay()], now=start)
    _record_ranker_candidate(
        key="opportunity:mean_reversion:malformed-parameters",
        evaluated_at=start + timedelta(minutes=1),
        decision="rejected",
        confidence="medium",
        net_return_pct=0.4,
        minimum="high",
    )
    db = get_db()
    db.execute(
        "UPDATE filter_decisions SET parameter_snapshot=? WHERE candidate_key=?",
        ("not-json", "opportunity:mean_reversion:malformed-parameters"),
    )

    _evaluate_active(now=start + timedelta(hours=1))
    experiment = list_filter_shadow_observations()[0]
    assert experiment["status"] == "invalidated"
    assert experiment["latest_evaluation"]["reason"] == "adapter_causal_input_changed"


def test_ranker_confidence_adapter_identity_change_invalidates(monkeypatch):
    from extensions.counterfactual import filter_experiments as fe
    from extensions.observation_lab.filter_shadow import capture_filter_shadow_exposures, list_filter_shadow_observations

    start = _anchor(minutes=1)
    fe.register_replay_ready_experiments([_ready_ranker_replay()], now=start)
    _seed_ranker_cohort(start, count=5)  # small — semantics change, no need for gate
    experiment = list_filter_shadow_observations()[0]
    capture_filter_shadow_exposures(experiment["observation_id"], 1)

    # Owner review §6: adapter/evaluator semantics are versioned. A deliberate bump
    # invalidates the collecting cohort; append-only evaluation history is preserved.
    changed_identity = dict(fe._ADAPTER_CAUSAL_IDENTITY["ranker_confidence_gate"])
    changed_identity["adapter_version"] = "v2"
    monkeypatch.setitem(
        fe._ADAPTER_CAUSAL_IDENTITY, "ranker_confidence_gate", changed_identity
    )

    _evaluate_active(now=start + timedelta(hours=1))
    experiment_after = list_filter_shadow_observations()[0]
    evaluation = experiment_after["latest_evaluation"]

    assert experiment_after["status"] == "invalidated"
    assert evaluation["reason"] == "adapter_causal_identity_changed"
    assert evaluation["disposition"] == "invalidated"


def test_two_ranker_experiments_collect_independently():
    from extensions.counterfactual.filter_experiments import register_replay_ready_experiments
    from extensions.observation_lab.filter_shadow import capture_filter_shadow_exposures, list_filter_shadow_observations
    from extensions.observation_lab.db import get_lab_db

    start = _anchor(minutes=1)
    for family in ("mean_reversion", "momentum"):
        register_replay_ready_experiments([_ready_ranker_replay(scanner_family=family)], now=start)
    _seed_ranker_cohort(start, count=10, scanner_family="mean_reversion")
    _seed_ranker_cohort(start, count=7, scanner_family="momentum")
    keys = {}
    for root in list_filter_shadow_observations():
        capture_filter_shadow_exposures(root["observation_id"], 1)
        keys[root["scanner_family"]] = {row[0] for row in get_lab_db().execute(
            "SELECT outcome_candidate_key FROM observation_exposures WHERE observation_id=?", (root["observation_id"],),
        )}
    assert len(keys["mean_reversion"]) == 20
    assert len(keys["momentum"]) == 14
    assert keys["mean_reversion"].isdisjoint(keys["momentum"])



def test_controller_lab_pass_advances_canonical_observation():
    from extensions.analytics.filter_controller import run_controller_observe
    from extensions.counterfactual.filter_experiments import register_replay_ready_experiments
    from extensions.observation_lab.db import get_lab_db

    start = _anchor(minutes=1)
    version = _seed_ranker_cohort(start, count=20)
    register_replay_ready_experiments([_ready_ranker_replay(filter_version=version)], now=start)
    lab = get_lab_db()
    root = lab.execute("SELECT observation_id FROM observation_windows").fetchone()[0]
    assert lab.execute("SELECT COUNT(*) FROM observation_exposures").fetchone()[0] == 0
    result = run_controller_observe(min_samples=20)
    assert result["lab_windows"]["windows_errored"] == 0
    assert lab.execute(
        "SELECT COUNT(*) FROM observation_exposures WHERE observation_id=?", (root,),
    ).fetchone()[0] == 40
    assert lab.execute(
        "SELECT COUNT(*) FROM observation_evaluations WHERE observation_id=?", (root,),
    ).fetchone()[0] == 1


def test_controller_legacy_authority_refuses_without_recommendation_writes():
    from extensions.analytics.filter_controller import run_controller_observe
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute("UPDATE observation_authority_state SET authority_source='legacy'")
    with pytest.raises(RuntimeError, match="observation_lab_authority_required"):
        run_controller_observe(min_samples=20)
    assert db.execute("SELECT COUNT(*) FROM controller_recommendations").fetchone()[0] == 0


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
    from extensions.observation_lab.db import lab_db_path, reset_lab_db
    from extensions.observation_lab.units import count_frozen_units, insert_frozen_units

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
    from extensions.observation_lab.db import lab_db_path, reset_lab_db
    from extensions.observation_lab.units import insert_frozen_units
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
