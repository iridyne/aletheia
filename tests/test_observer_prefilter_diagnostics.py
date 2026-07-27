from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_observer_diagnostic_buckets_have_stable_boundaries():
    from aletheia.analytics.filter_evidence import (
        _observer_range_bucket,
        _observer_rule_cohort,
    )

    assert _observer_range_bucket(None, (6.0, 9.0, 12.0, 16.0)) == "unknown"
    assert _observer_range_bucket(6.0, (6.0, 9.0, 12.0, 16.0)) == "0_to_6"
    assert _observer_range_bucket(12.0, (6.0, 9.0, 12.0, 16.0)) == "9_to_12"
    assert _observer_range_bucket(12.01, (6.0, 9.0, 12.0, 16.0)) == "12_to_16"
    assert _observer_range_bucket(16.01, (6.0, 9.0, 12.0, 16.0)) == "gt_16"

    assert _observer_rule_cohort({}) == "neither"
    assert _observer_rule_cohort({"matched_rules": ["rsi_deviation"]}) == "rsi_only"
    assert _observer_rule_cohort({"matched_rules": ["atr_pct"]}) == "atr_only"
    assert _observer_rule_cohort(
        {"matched_rules": ["rsi_deviation", "atr_pct"]}
    ) == "rsi_and_atr"


def test_observer_diagnostics_use_first_exposure_and_are_read_only(
    tmp_path, monkeypatch,
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.analytics.filter_evidence import (
        observer_pre_filter_diagnostics,
        record_filter_decision,
    )
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc)
    common = {
        "candidate_kind": "observer_evaluation",
        "filter_name": "observer_pre_filter",
        "stage": "observer_pre_filter",
        "reason_code": "test",
        "symbol": "BTC/USDT",
        "market_type": "perp",
        "venue": "binance",
        "direction": "short",
        "scanner_family": "short_bias",
        "parameters": {"rsi_deviation": 12.0, "atr_pct": 0.8},
        "entry_price_usd": 100.0,
        "raise_on_error": True,
    }
    record_filter_decision(
        candidate_key="observer:first:rsi",
        decision="passed",
        inputs={
            "rsi": 64.0,
            "atr_pct": 0.5,
            "matched_rules": ["rsi_deviation"],
        },
        evaluated_at=(now - timedelta(hours=2)).isoformat(),
        **common,
    )
    # A later retry changes its decision, but cannot move the candidate into the
    # rejected cohort.
    record_filter_decision(
        candidate_key="observer:first:rsi",
        decision="rejected",
        inputs={"rsi": 55.0, "atr_pct": 0.5, "matched_rules": []},
        evaluated_at=(now - timedelta(hours=1)).isoformat(),
        **common,
    )
    record_filter_decision(
        candidate_key="observer:first:neither",
        decision="rejected",
        inputs={"rsi": 58.0, "atr_pct": 0.7, "matched_rules": []},
        evaluated_at=(now - timedelta(hours=1)).isoformat(),
        **common,
    )

    db = get_db()
    db.execute(
        "UPDATE filter_candidate_outcomes "
        "SET status='settled', net_return_pct = "
        "CASE WHEN outcome_candidate_key='observer:first:rsi' THEN -1.0 ELSE 2.0 END "
        "WHERE outcome_candidate_key IN ('observer:first:rsi','observer:first:neither')"
    )
    db.commit()
    before = db.total_changes

    report = observer_pre_filter_diagnostics()

    assert db.total_changes == before
    assert report["read_only"] is True
    assert report["assignment_policy"] == "first_exposure"
    assert report["row_count"] == 8  # two candidates × four horizons
    assert {
        (row["decision"], row["rule_cohort"])
        for row in report["cohorts"]
    } == {
        ("passed", "rsi_only"),
        ("rejected", "neither"),
    }
    rsi_24h = next(
        row for row in report["cohorts"]
        if row["rule_cohort"] == "rsi_only" and row["horizon"] == "24h"
    )
    calm_24h = next(
        row for row in report["cohorts"]
        if row["rule_cohort"] == "neither" and row["horizon"] == "24h"
    )
    assert rsi_24h["rsi_deviation_bucket"] == "12_to_16"
    assert rsi_24h["atr_pct_bucket"] == "0.4_to_0.6"
    assert rsi_24h["raw_avg_net_pct"] == -1.0
    assert calm_24h["rsi_deviation_bucket"] == "6_to_9"
    assert calm_24h["atr_pct_bucket"] == "0.6_to_0.8"
    assert calm_24h["raw_avg_net_pct"] == 2.0


def test_controller_report_surfaces_observer_rule_diagnostics(monkeypatch):
    from aletheia.analytics import filter_controller as controller

    monkeypatch.setattr(controller, "compute_filter_feedback", lambda **_kwargs: [])
    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.filter_feedback_trajectory_summary",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.observer_pre_filter_diagnostics",
        lambda **_kwargs: {
            "row_count": 40,
            "cohort_count": 1,
            "cohorts": [{
                "scanner_family": "short_bias",
                "direction": "short",
                "market_type": "perp",
                "horizon": "24h",
                "decision": "rejected",
                "rule_cohort": "neither",
                "rsi_deviation_bucket": "6_to_9",
                "atr_pct_bucket": "0.6_to_0.8",
                "n_net": 20,
                "n_total": 20,
                "outcome_coverage": 1.0,
                "shrunk_avg_net_pct": 2.5,
            }],
        },
    )
    monkeypatch.setattr(
        "aletheia.analytics.filter_replay.build_bounded_filter_replays",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "aletheia.analytics.filter_shadow_experiments."
        "build_filter_shadow_experiment_report",
        lambda: [],
    )
    monkeypatch.setattr(controller, "compute_persistent_recommendations", lambda **_kwargs: [])

    report = controller.build_filter_controller_report(hours=120)
    rendered = controller.render_filter_controller_report(report)

    assert report["observer_pre_filter_diagnostics"]["row_count"] == 40
    assert "Observer 24h matched-rule cohorts" in rendered
    assert "short_bias short perp" in rendered
    assert "neither" in rendered
    assert "shrunk_net=2.5%" in rendered


def test_observer_replay_is_fixed_bounded_first_exposure_and_read_only(
    tmp_path, monkeypatch,
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.analytics.filter_replay import build_observer_pre_filter_replay
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc)
    common = {
        "candidate_kind": "observer_evaluation",
        "filter_name": "observer_pre_filter",
        "stage": "observer_pre_filter",
        "reason_code": "test",
        "symbol": "BTC/USDT",
        "market_type": "perp",
        "venue": "binance",
        "direction": "short",
        "scanner_family": "short_bias",
        "parameters": {"rsi_deviation": 12.0, "atr_pct": 0.8},
        "entry_price_usd": 100.0,
        "raise_on_error": True,
    }
    record_filter_decision(
        candidate_key="observer:replay:control",
        run_id="run-control",
        decision="passed",
        inputs={
            "rsi": 64.0,
            "atr_pct": 0.5,
            "matched_rules": ["rsi_deviation"],
        },
        evaluated_at=(now - timedelta(hours=3)).isoformat(),
        **common,
    )
    record_filter_decision(
        candidate_key="observer:replay:admit",
        run_id="run-treatment",
        decision="rejected",
        inputs={"rsi": 61.0, "atr_pct": 0.75, "matched_rules": []},
        evaluated_at=(now - timedelta(hours=2)).isoformat(),
        **common,
    )
    # Later exposure would pass, but the replay must retain the first rejection.
    record_filter_decision(
        candidate_key="observer:replay:admit",
        run_id="run-treatment-later",
        decision="passed",
        inputs={
            "rsi": 64.0,
            "atr_pct": 0.9,
            "matched_rules": ["rsi_deviation", "atr_pct"],
        },
        evaluated_at=(now - timedelta(hours=1)).isoformat(),
        **common,
    )
    # Exact-cell query must exclude directionless evidence.
    record_filter_decision(
        candidate_key="observer:replay:directionless",
        run_id="run-directionless",
        decision="rejected",
        inputs={"rsi": 60.0, "atr_pct": 0.7, "matched_rules": []},
        evaluated_at=(now - timedelta(hours=1)).isoformat(),
        **{**common, "direction": None},
    )

    db = get_db()
    version = db.execute(
        "SELECT filter_version FROM filter_decisions "
        "WHERE candidate_key='observer:replay:control'"
    ).fetchone()[0]
    db.execute(
        "UPDATE filter_candidate_outcomes "
        "SET status='settled', net_return_pct = "
        "CASE WHEN outcome_candidate_key='observer:replay:control' THEN -1.0 ELSE 2.0 END "
        "WHERE outcome_candidate_key IN "
        "('observer:replay:control','observer:replay:admit')"
    )
    db.commit()
    before = db.total_changes

    report = build_observer_pre_filter_replay(
        filter_version=version,
        scanner_family="short_bias",
        direction="short",
        market_type="perp",
        min_samples=1,
    )

    assert db.total_changes == before
    assert report["read_only"] is True
    assert report["assignment_policy"] == "first_exposure"
    assert report["min_samples"] == 200
    assert report["control_parameter"] == {
        "rsi_deviation": 12.0,
        "atr_pct": 0.8,
    }
    assert len(report["alternatives"]) == 15
    assert {
        (row["rsi_deviation"], row["atr_pct"])
        for row in report["alternatives"]
    } == {
        (rsi, atr)
        for rsi in (12.0, 10.0, 8.0, 6.0)
        for atr in (0.8, 0.7, 0.6, 0.5)
        if (rsi, atr) != (12.0, 0.8)
    }
    treatment = next(
        row for row in report["alternatives"]
        if row["rsi_deviation"] == 10.0 and row["atr_pct"] == 0.7
    )
    assert treatment["newly_admitted_candidates"] == 1
    assert treatment["horizon_metrics"]["24h"]["newly_admitted"]["settled_n"] == 1
    assert treatment["horizon_metrics"]["24h"]["newly_admitted"]["raw_avg_net_pct"] == 2.0
    assert treatment["readiness"] == "insufficient_data"


def test_stale_persistent_observer_version_cannot_trigger_replay(monkeypatch):
    from aletheia.analytics import filter_controller as controller

    monkeypatch.setattr(controller, "compute_filter_feedback", lambda **_kwargs: [])
    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.filter_feedback_trajectory_summary",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.observer_pre_filter_diagnostics",
        lambda **_kwargs: {"row_count": 0, "cohort_count": 0, "cohorts": []},
    )
    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.filter_version_status",
        lambda _name: {
            "filter_name": "observer_pre_filter",
            "current_filter_version": "current-v2",
            "versions": [],
        },
    )
    monkeypatch.setattr(
        "aletheia.analytics.filter_replay.build_bounded_filter_replays",
        lambda *_args, **_kwargs: [],
    )

    def forbidden_replay(**_kwargs):
        raise AssertionError("stale production version must not be replayed")

    monkeypatch.setattr(
        "aletheia.analytics.filter_replay.build_observer_pre_filter_replay",
        forbidden_replay,
    )
    monkeypatch.setattr(
        "aletheia.analytics.filter_shadow_experiments."
        "build_filter_shadow_experiment_report",
        lambda: [],
    )
    monkeypatch.setattr(
        controller,
        "compute_persistent_recommendations",
        lambda **_kwargs: [controller.PersistentRecommendation(
            filter_name="observer_pre_filter",
            scanner_family="short_bias",
            direction="short",
            market_type="perp",
            horizon="24h",
            filter_version="stale-v1",
            consecutive_suspect_runs=10,
            runs_analyzed=10,
            first_suspect_at="2026-07-20T00:00:00+00:00",
            last_suspect_at="2026-07-24T00:00:00+00:00",
            latest_error_signal=1.0,
            latest_reasoning="stale predecessor evidence",
            latest_run_id="run-stale",
        )],
    )

    report = controller.build_filter_controller_report(hours=120)
    rendered = controller.render_filter_controller_report(report)

    assert report["bounded_replays"] == []
    assert report["observer_pre_filter_version_status"][
        "current_filter_version"
    ] == "current-v2"
    assert report["stale_persistent_recommendations"][0][
        "replay_blocked_reason"
    ] == "production_filter_version_changed"
    assert "stale persistent suspects blocked: 1" in rendered
