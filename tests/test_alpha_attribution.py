from __future__ import annotations

from datetime import datetime, timedelta, timezone

from typer.testing import CliRunner


def _iso(days_ago: int = 0, hours_after: int = 0) -> str:
    anchor = datetime.fromisoformat("2026-07-12T07:10:01+00:00")
    return (anchor + timedelta(seconds=(30 - days_ago) * 10, hours=hours_after)).isoformat()


def _ms(iso_ts: str, hours_after: int = 0) -> int:
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00")) + timedelta(hours=hours_after)
    return int(dt.timestamp() * 1000)


def _insert_ohlcv(db, symbol: str, timeframe: str, entry_at: str, entry: float, exits: dict[int, float]) -> None:
    db.execute(
        "INSERT OR REPLACE INTO market_ohlcv "
        "(symbol, timeframe, ts, open, high, low, close, volume, provider) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'test')",
        (symbol, timeframe, _ms(entry_at), entry, entry, entry, entry),
    )
    for hours, close in exits.items():
        db.execute(
            "INSERT OR REPLACE INTO market_ohlcv "
            "(symbol, timeframe, ts, open, high, low, close, volume, provider) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'test')",
            (symbol, timeframe, _ms(entry_at, hours), close, close, close, close),
        )


def _insert_shadow(
    db,
    batch: str,
    opp: str,
    rank: int,
    pnl: float,
    selected: int = 0,
    *,
    shadow_pnl: float | None = None,
    policy_epoch: str = "epoch-test",
    hours_after: int = 0,
) -> None:
    entry = _iso(days_ago=1, hours_after=hours_after)
    outcome_key = f"opportunity:{opp}"
    db.execute(
        "INSERT INTO shadow_trades "
        "(id, batch_id, opportunity_id, symbol, venue, market_type, direction, "
        "scanner_family, trade_score, rank, entry_price, outcome_candidate_key, "
        "was_selected, policy_epoch_id, created_at, pnl_pct, net_pnl_pct, "
        "cost_model_version, resolved_at) "
        "VALUES (?, ?, ?, 'BTC/USDT', 'bitget', 'spot', 'long', 'momentum', ?, ?, "
        "100, ?, ?, ?, ?, ?, ?, 'candidate-net-v1', ?)",
        (
            f"sh-{batch}-{rank}-{opp}",
            batch,
            opp,
            100 - rank,
            rank,
            outcome_key,
            selected,
            policy_epoch,
            entry,
            pnl if shadow_pnl is None else shadow_pnl,
            pnl if shadow_pnl is None else shadow_pnl,
            _iso(),
        ),
    )
    db.execute(
        "INSERT INTO filter_candidate_outcomes "
        "(id, outcome_candidate_key, venue, symbol, market_type, direction, horizon, "
        "entry_price_usd, entry_ts, due_at, settled_at, net_return_pct, "
        "cost_model_version, status) "
        "VALUES (?, ?, 'bitget', 'BTC/USDT', 'spot', 'long', '24h', 100, ?, ?, ?, ?, "
        "'candidate-net-v1', 'settled')",
        (
            f"fco-{batch}-{rank}-{opp}",
            outcome_key,
            entry,
            _iso(hours_after=24),
            _iso(),
            pnl,
        ),
    )


def _insert_operator_candidate(
    db,
    idx: int,
    decision: str,
    pnl: float,
    *,
    behavior: str = "behavior-test",
    hours_after: int = 0,
) -> None:
    decision_id = f"operator-{decision}-{idx}"
    key = f"pi_decision:{decision_id}"
    entry = _iso(days_ago=1, hours_after=hours_after)
    db.execute(
        "INSERT INTO pi_decisions "
        "(id, created_at, symbol, direction, market_type, action, opportunity_id, "
        "signal_source, runtime_session_id, producer_commit, behavior_fingerprint) "
        "VALUES (?, ?, 'BTC/USDT', 'long', 'spot', ?, ?, 'market_observer', "
        "'session-test', 'commit-test', ?)",
        (
            decision_id,
            entry,
            "trade" if decision == "passed" else "skip",
            f"opp-operator-{decision}-{idx}",
            behavior,
        ),
    )
    db.execute(
        "INSERT INTO filter_decisions "
        "(id, dedup_key, candidate_key, candidate_kind, venue, symbol, market_type, "
        "direction, filter_name, stage, sequence_no, decision, reason_code, "
        "input_snapshot, parameter_snapshot, filter_version, code_commit, "
        "outcome_candidate_key, evaluated_at) "
        "VALUES (?, ?, ?, 'pi_decision', 'bitget', 'BTC/USDT', 'spot', 'long', "
        "'operator_gate', 'system_operator', 1, ?, ?, '{}', '{}', 'operator-v1', "
        "'commit-test', ?, ?)",
        (f"fd-operator-{decision}-{idx}", f"dedup-operator-{decision}-{idx}", key, decision, decision, key, entry),
    )
    db.execute(
        "INSERT INTO filter_candidate_outcomes "
        "(id, outcome_candidate_key, venue, symbol, market_type, direction, horizon, "
        "entry_price_usd, entry_ts, due_at, settled_at, net_return_pct, "
        "cost_model_version, status) "
        "VALUES (?, ?, 'bitget', 'BTC/USDT', 'spot', 'long', '24h', 100, ?, ?, ?, ?, "
        "'candidate-net-v1', 'settled')",
        (f"fco-operator-{decision}-{idx}", key, entry, _iso(hours_after=24), _iso(), pnl),
    )


def _insert_trade_chain(db, idx: int, pnl_pct: float, scanner: str = "momentum", closed_at: str | None = None) -> None:
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    opp_id = f"opp-{idx}"
    prop_id = f"prop-{idx}"
    pos_id = f"pos-{idx}"
    closed = closed_at or _iso(days_ago=1)
    opened = (datetime.fromisoformat(closed) - timedelta(hours=1)).isoformat()
    _insert_closed_position_via_producer(
        position_id=pos_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        realized_pnl=pnl_pct,
        execution_mode="paper_internal",
        opened_at=opened,
        closed_at=closed,
        close_reason="signal_exit",
        proposal_id=prop_id,
        order_id=f"order-{idx}",
        opportunity_id=opp_id,
        scanner_family=scanner,
        confidence="high",
    )


def _insert_prospective_control_cohort(
    db,
    *,
    layer: str,
    prefix: str,
    selected_return: float,
    control_return: float,
) -> None:
    import json

    entry = _iso(days_ago=2)
    due_at = _iso(days_ago=2, hours_after=1)
    for i in range(5):
        for role, net_return in (
            ("selected", selected_return),
            ("control", control_return),
        ):
            symbol = f"{prefix}-{role}-{i}/USDT"
            outcome_key = f"{prefix}:{role}:{i}"
            db.execute(
                "INSERT INTO alpha_control_observations "
                "(id, layer, cohort_id, treatment_id, role, symbol, direction, horizon, "
                "entry_price, entry_ts, score, module_path, metadata, outcome_candidate_key) "
                "VALUES (?, ?, ?, ?, ?, ?, 'long', '1h', 100, ?, 50, ?, ?, ?)",
                (
                    f"aco-{prefix}-{role}-{i}",
                    layer,
                    f"cohort-{prefix}",
                    f"treatment-{prefix}",
                    role,
                    symbol,
                    entry,
                    f"aletheia/{layer}.py",
                    json.dumps({"due_at": due_at}),
                    outcome_key,
                ),
            )
            db.execute(
                "INSERT INTO filter_candidate_outcomes "
                "(id, outcome_candidate_key, venue, symbol, market_type, direction, "
                "horizon, entry_price_usd, entry_ts, due_at, settled_at, net_return_pct, status) "
                "VALUES (?, ?, 'okx', ?, 'spot', 'long', '1h', 100, ?, ?, ?, ?, 'settled')",
                (
                    f"fco-{prefix}-{role}-{i}",
                    outcome_key,
                    symbol,
                    entry,
                    due_at,
                    due_at,
                    net_return,
                ),
            )


def test_empty_alpha_audit_returns_five_insufficient_verdicts():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit

    report = build_alpha_attribution_audit(days=30)

    assert set(report["verdicts"]) == {"universe", "ranker", "system_operator", "learning", "serenity"}
    assert all(v["verdict"] == "insufficient_data" for v in report["verdicts"].values())
    assert report["main_bottleneck"]["layer"] == "insufficient_data"


def test_ranker_alpha_detects_top1_underperforming_shadow_baseline():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(5):
        batch = f"b{i}"
        _insert_shadow(db, batch, f"opp-r1-{i}", 1, -2.0, selected=1)
        _insert_shadow(db, batch, f"opp-r2-{i}", 2, 1.0)
        _insert_shadow(db, batch, f"opp-r3-{i}", 3, 2.0)
    db.commit()

    report = build_alpha_attribution_audit(days=30)
    ranker = report["verdicts"]["ranker"]

    assert ranker["verdict"] == "negative"
    assert ranker["sample_size"] == 5
    assert ranker["baseline"] > ranker["observed"]
    assert ranker["details"]["horizon"] == "24h"
    assert ranker["details"]["statistical_unit"] == "outcome_candidate_key"
    assert ranker["details"]["economic_unit"] == "net_return_pct"
    assert ranker["measurement_status"] == "directional_evidence_only"
    assert report["main_bottleneck"]["layer"] == "insufficient_data"


def test_ranker_alpha_uses_canonical_fixed_horizon_outcome_not_shadow_lifecycle_pnl():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(5):
        batch = f"fixed{i}"
        _insert_shadow(db, batch, f"fixed-r1-{i}", 1, -2.0, selected=1, shadow_pnl=50.0)
        _insert_shadow(db, batch, f"fixed-r2-{i}", 2, 2.0, shadow_pnl=-50.0)
    db.commit()

    ranker = build_alpha_attribution_audit(days=30)["verdicts"]["ranker"]

    assert ranker["verdict"] == "negative"
    assert ranker["observed"] == -2.0
    assert ranker["baseline"] == 2.0
    assert ranker["details"]["source_table"] == "filter_candidate_outcomes"


def test_ranker_alpha_does_not_mix_policy_epochs():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(5):
        _insert_shadow(db, f"old-{i}", f"old-r1-{i}", 1, -2.0, policy_epoch="epoch-old")
        _insert_shadow(db, f"old-{i}", f"old-r2-{i}", 2, 2.0, policy_epoch="epoch-old")
    _insert_shadow(db, "new-0", "new-r1-0", 1, 3.0, policy_epoch="epoch-new", hours_after=1)
    _insert_shadow(db, "new-0", "new-r2-0", 2, -3.0, policy_epoch="epoch-new", hours_after=1)
    db.commit()

    ranker = build_alpha_attribution_audit(days=30)["verdicts"]["ranker"]

    assert ranker["verdict"] == "insufficient_data"
    assert ranker["details"]["policy_epoch_id"] == "epoch-new"


def test_system_operator_alpha_compares_fixed_horizon_passed_and_rejected_candidates():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(5):
        _insert_operator_candidate(db, i, "passed", -3.0)
        _insert_operator_candidate(db, i, "rejected", 2.0)
    db.commit()

    report = build_alpha_attribution_audit(days=30)
    operator = report["verdicts"]["system_operator"]

    assert operator["verdict"] == "negative"
    assert operator["sample_size"] == 5
    assert operator["details"]["rejected_sample_size"] == 5
    assert operator["details"]["horizon"] == "24h"
    assert operator["details"]["statistical_unit"] == "outcome_candidate_key"
    assert operator["details"]["economic_unit"] == "net_return_pct"
    assert operator["baseline"] > operator["observed"]
    assert operator["measurement_status"] == "directional_evidence_only"
    assert report["main_bottleneck"]["layer"] == "insufficient_data"


def test_system_operator_alpha_does_not_mix_behavior_fingerprints():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(5):
        _insert_operator_candidate(db, i, "passed", -3.0, behavior="behavior-old")
        _insert_operator_candidate(db, i, "rejected", 2.0, behavior="behavior-old")
    _insert_operator_candidate(db, 99, "passed", 3.0, behavior="behavior-new", hours_after=1)
    db.commit()

    operator = build_alpha_attribution_audit(days=30)["verdicts"]["system_operator"]

    assert operator["verdict"] == "insufficient_data"
    assert operator["details"]["behavior_fingerprint"] == "behavior-new"


def test_universe_alpha_uses_forward_outcomes_against_deterministic_baseline():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    entry = _iso(days_ago=2)
    for i in range(8):
        sym = f"BASE{i}/USDT"
        db.execute(
            "INSERT INTO universe_registry (id, venue, market_type, symbol, status, is_blacklisted) "
            "VALUES (?, 'bitget', 'spot', ?, 'active', 0)",
            (f"asset-base-{i}", sym),
        )
        _insert_ohlcv(db, sym, "1h", entry, 100.0, {1: 100.0})
    for i in range(5):
        db.execute(
            "INSERT INTO universe_forward_outcomes "
            "(id, member_id, snapshot_id, symbol, scanner_family, direction, horizon, "
            "entry_price_usd, entry_ts, due_at, settled_at, exit_price_usd, forward_return_pct, status) "
            "VALUES (?, ?, 'snap', ?, 'momentum', 'long', '1h', 100, ?, ?, ?, 102, 2.0, 'settled')",
            (f"ufo-{i}", f"member-{i}", f"SEL{i}/USDT", entry, _iso(days_ago=2, hours_after=1), _iso(days_ago=1)),
        )
    db.commit()

    report = build_alpha_attribution_audit(days=30)
    universe = report["verdicts"]["universe"]

    assert universe["verdict"] == "positive"
    assert universe["sample_size"] == 5
    assert universe["baseline"] == 0.0
    assert universe["observed"] == 2.0


def test_universe_alpha_prefers_prospective_control_cohort():
    import json

    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    entry = _iso(days_ago=2)
    due_at = _iso(days_ago=2, hours_after=1)
    for i in range(5):
        sel = f"SELCAUSAL{i}/USDT"
        ctl = f"CTLCAUSAL{i}/USDT"
        _insert_ohlcv(db, sel, "1h", entry, 100.0, {1: 103.0})
        _insert_ohlcv(db, ctl, "1h", entry, 100.0, {1: 100.0})
        for role, sym in (("selected", sel), ("control", ctl)):
            outcome_key = f"control:{role}:{i}"
            db.execute(
                "INSERT INTO alpha_control_observations "
                "(id, layer, cohort_id, treatment_id, role, symbol, direction, horizon, "
                "entry_price, entry_ts, score, module_path, metadata, outcome_candidate_key) "
                "VALUES (?, 'universe', 'cohort-1', 'snap-1', ?, ?, 'long', '1h', 100, ?, 50, "
                "'aletheia/universe/allocator.py', ?, ?)",
                (f"aco-{role}-{i}", role, sym, entry, json.dumps({"due_at": due_at}), outcome_key),
            )
            db.execute(
                "INSERT INTO filter_candidate_outcomes "
                "(id, outcome_candidate_key, venue, symbol, market_type, direction, "
                "horizon, entry_price_usd, entry_ts, due_at, settled_at, net_return_pct, status) "
                "VALUES (?, ?, 'okx', ?, 'spot', 'long', '1h', 100, ?, ?, ?, ?, 'settled')",
                (
                    f"fco-{role}-{i}", outcome_key, sym, entry, due_at, due_at,
                    3.0 if role == "selected" else 0.0,
                ),
            )
    db.commit()

    report = build_alpha_attribution_audit(days=30)
    universe = report["verdicts"]["universe"]

    assert universe["verdict"] == "positive"
    assert universe["causal_grade"] == "weak_observational"
    assert universe["evidence_type"] == "prospective_control_cohort"
    assert universe["baseline"] == 0.0
    assert universe["observed"] == 3.0
    assert universe["measurement_status"] == "directional_evidence_only"
    assert report["main_bottleneck"]["layer"] == "insufficient_data"


def test_alpha_audit_projects_universe_twin_without_granting_authority():
    from aletheia.analytics.alpha_attribution import (
        build_alpha_attribution_audit,
        render_alpha_audit,
    )
    from aletheia.storage import repositories as repo
    from tests.test_universe_twin_experiment import _bundle

    bundle = _bundle("alpha-twin", captured_at=_iso(days_ago=1))
    repo.insert_universe_twin_shadow_bundle(
        bundle.batch,
        bundle.evidence,
        outcomes=bundle.outcomes,
        min_interval_sec=0,
    )

    report = build_alpha_attribution_audit(days=30)
    twin = report["universe_twin_evidence"]

    assert twin["policy_epoch_id"] == bundle.batch.policy_epoch_id
    assert twin["expected_cluster_n"] == 1
    assert twin["complete_cluster_n"] == 0
    assert twin["measurement_status"] == "not_measured"
    assert twin["contribution_verdict"] == "insufficient_data"
    assert twin["behavior_authority"] is False
    assert twin["promotion_eligible"] is False
    assert "fixed_horizon_outcomes_incomplete" in twin["blocking_reasons"]
    assert twin["audit_window_compatible"] is True
    assert report["main_bottleneck"]["layer"] == "insufficient_data"
    assert "Universe Twin evidence: not_measured" in render_alpha_audit(report)


def test_alpha_audit_twin_projection_fails_closed_without_storage():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute("DROP TABLE universe_twin_shadow_batches")
    db.commit()

    report = build_alpha_attribution_audit(days=30)

    assert set(report["verdicts"]) == {
        "universe", "ranker", "system_operator", "learning", "serenity",
    }
    twin = report["universe_twin_evidence"]
    assert twin["measurement_status"] == "not_measured"
    assert twin["contribution_verdict"] == "insufficient_data"
    assert "storage is unavailable" in twin["reason"]


def test_alpha_audit_twin_projection_never_promotes_complete_invalid_or_failed_evidence(
    monkeypatch,
):
    import aletheia.universe.twin_evaluation as twin_evaluation
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage import repositories as repo
    from tests.test_universe_twin_experiment import _bundle

    bundle = _bundle("alpha-twin-states", captured_at="2026-08-08T00:00:00+00:00")
    repo.insert_universe_twin_shadow_bundle(
        bundle.batch,
        bundle.evidence,
        outcomes=bundle.outcomes,
        min_interval_sec=0,
    )

    monkeypatch.setattr(
        twin_evaluation,
        "evaluate_twin_shadow_epoch",
        lambda _epoch: {
            "verdict": "not_available",
            "complete_cluster_n": 100,
            "expected_cluster_n": 100,
            "treatment_cluster_n": 50,
            "control_cluster_n": 50,
            "observed_cluster_utility_delta": 1.0,
        },
    )
    complete = build_alpha_attribution_audit(days=30)["universe_twin_evidence"]
    assert complete["measurement_status"] == "prospective_shadow_diagnostic"
    assert complete["causal_grade"] == "prospective_cluster_shadow_only"
    assert complete["contribution_verdict"] == "insufficient_data"

    monkeypatch.setattr(
        twin_evaluation,
        "evaluate_twin_shadow_epoch",
        lambda _epoch: {"verdict": "invalid", "complete_cluster_n": 0},
    )
    invalid = build_alpha_attribution_audit(days=30)["universe_twin_evidence"]
    assert invalid["measurement_status"] == "invalid_evidence"
    assert invalid["causal_grade"] == "insufficient"
    assert invalid["contribution_verdict"] == "insufficient_data"

    def fail(_epoch):
        raise RuntimeError("synthetic twin evaluation failure")

    monkeypatch.setattr(twin_evaluation, "evaluate_twin_shadow_epoch", fail)
    failed = build_alpha_attribution_audit(days=30)["universe_twin_evidence"]
    assert failed["measurement_status"] == "not_measured"
    assert failed["contribution_verdict"] == "insufficient_data"
    assert "RuntimeError" in failed["reason"]


def test_alpha_audit_does_not_truncate_twin_epoch_across_requested_window():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage import repositories as repo
    from tests.test_universe_twin_experiment import _bundle

    for run_id, captured_at in (
        ("alpha-twin-old", "2026-06-01T00:00:00+00:00"),
        ("alpha-twin-current", "2026-08-08T00:00:00+00:00"),
    ):
        bundle = _bundle(run_id, captured_at=captured_at)
        repo.insert_universe_twin_shadow_bundle(
            bundle.batch,
            bundle.evidence,
            outcomes=bundle.outcomes,
            min_interval_sec=0,
        )

    twin = build_alpha_attribution_audit(days=30)["universe_twin_evidence"]

    assert twin["measurement_status"] == "not_measured"
    assert twin["audit_window_compatible"] is False
    assert twin["window_start"] == "2026-06-01T00:00:00+00:00"
    assert "not truncated or pooled" in twin["reason"]


def test_prospective_universe_negative_remains_directional_without_authority_contract():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_prospective_control_cohort(
        db,
        layer="universe",
        prefix="universe-negative",
        selected_return=-3.0,
        control_return=0.0,
    )
    db.commit()

    report = build_alpha_attribution_audit(days=30)
    universe = report["verdicts"]["universe"]

    assert universe["verdict"] == "negative"
    assert universe["contribution_verdict"] == "insufficient_data"
    assert universe["measurement_status"] == "directional_evidence_only"
    assert report["main_bottleneck"]["layer"] == "insufficient_data"


def test_prospective_universe_and_ranker_negatives_remain_non_actionable():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_prospective_control_cohort(
        db,
        layer="universe",
        prefix="universe-authority",
        selected_return=-1.0,
        control_return=0.0,
    )
    for i in range(5):
        batch = f"weak-ranker-{i}"
        _insert_shadow(db, batch, f"weak-top1-{i}", 1, -10.0, selected=1)
        _insert_shadow(db, batch, f"weak-alt-{i}", 2, 1.0)
    db.commit()

    report = build_alpha_attribution_audit(days=30)

    assert report["verdicts"]["ranker"]["delta"] < report["verdicts"]["universe"]["delta"]
    assert report["verdicts"]["ranker"]["measurement_status"] == "directional_evidence_only"
    assert report["verdicts"]["universe"]["measurement_status"] == "directional_evidence_only"
    assert report["main_bottleneck"]["layer"] == "insufficient_data"


def test_two_measured_negative_layers_are_not_ranked_by_raw_delta():
    from aletheia.analytics.alpha_attribution import _main_bottleneck

    bottleneck = _main_bottleneck({
        "universe": {
            "name": "Universe Alpha", "delta": -1.0,
            "contribution_verdict": "negative", "measurement_status": "measured_contribution",
        },
        "serenity": {
            "name": "Serenity Alpha", "delta": -5.0,
            "contribution_verdict": "negative", "measurement_status": "measured_contribution",
        },
    })

    assert bottleneck["layer"] == "not_comparable"
    assert set(bottleneck["candidate_layers"]) == {"Universe Alpha", "Serenity Alpha"}
    assert bottleneck["suspected_modules"] == []


def test_learning_alpha_detects_same_pattern_improvement_after_lesson():
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    from aletheia.storage.sqlite import get_db

    db = get_db()
    event_at = _iso(days_ago=10)
    for i in range(5):
        _insert_trade_chain(db, 100 + i, pnl_pct=-2.0, closed_at=_iso(days_ago=15))
    for i in range(5):
        _insert_trade_chain(db, 200 + i, pnl_pct=2.0, closed_at=_iso(days_ago=5))
    db.execute(
        "INSERT INTO ps_lessons "
        "(lesson_id, source_type, source_id, category, severity, summary, evidence_json, status, created_at, activated_at) "
        "VALUES ('lesson-alpha', 'trade_case_study', 'case-1', 'risk', 'warn', 'momentum improved', "
        "?, 'accepted', ?, ?)",
        ('{"scanner_family":"momentum","direction":"long","symbol":"BTC/USDT"}', event_at, event_at),
    )
    db.commit()

    report = build_alpha_attribution_audit(days=30)
    learning = report["verdicts"]["learning"]

    assert learning["verdict"] == "positive"
    assert learning["sample_size"] == 5
    assert learning["baseline"] == -2.0
    assert learning["observed"] == 2.0


def test_learning_alpha_surfaces_current_paper_monitoring_without_authority(monkeypatch):
    import aletheia.analytics.alpha_attribution as alpha_attribution
    import aletheia.learning.read_models as learning_read_models
    from aletheia.storage.sqlite import get_db
    from tests.test_learning_verdict_runtime import _experiment, _exposure

    monkeypatch.setattr(
        alpha_attribution,
        "_utcnow",
        lambda: datetime(2026, 8, 9, 10, tzinfo=timezone.utc),
    )
    hypothesis, experiment = _experiment(min_samples=50, min_days=7)
    db = get_db()
    db.execute(
        "UPDATE learning_experiments SET created_at=?, started_at=? WHERE experiment_id=?",
        ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", experiment.experiment_id),
    )
    db.commit()
    _exposure(
        experiment,
        hypothesis,
        candidate="paper-monitoring-current",
        assignment="control",
        exposed_at="2026-08-09T09:00:00+00:00",
    )
    item = {
        "path_id": hypothesis.path_id,
        "experiment_id": experiment.experiment_id,
        "stage": "paper_10",
        "allocation_pct": 0.1,
        "current_identity": {"policy_epoch": "epoch-a", "matches_running_cohort": True},
        "measurement": {
            "state": "insufficient_data",
            "reason": "insufficient unique prospective treatment/control samples",
            "baseline": None,
            "observed": None,
            "delta": None,
            "authority_eligible": False,
            "window_start": "1970-01-01T00:00:00+00:00",
            "window_end": "1970-01-01T00:00:00+00:00",
        },
        "samples": {
            "raw_exposures": 21,
            "treatment_assignment_units": 0,
            "control_assignment_units": 0,
            "actual_intervention_treatment_units": 0,
        },
        "integrity": {
            "actual_intervention_state": "none",
            "exclusion_reasons": {"unsettled": 21},
        },
        "inference": {"status": "collecting"},
    }
    requested_limits = []
    monkeypatch.setattr(
        learning_read_models,
        "build_learning_contribution_projection",
        lambda *, limit: requested_limits.append(limit) or {
            "items": [
                {
                    "path_id": hypothesis.path_id,
                    "experiment_id": "newer-shadow-experiment",
                    "stage": "shadow",
                },
                item,
            ]
        },
    )

    learning = alpha_attribution.build_alpha_attribution_audit(days=30)["verdicts"]["learning"]

    assert learning["verdict"] == "insufficient_data"
    assert learning["contribution_verdict"] == "insufficient_data"
    assert learning["measurement_status"] == "not_measured"
    assert learning["evidence_type"] == "dedicated_learning_contribution_monitoring"
    assert learning["details"]["experiment_id"] == experiment.experiment_id
    assert learning["details"]["raw_exposures"] == 21
    assert learning["details"]["exclusion_reasons"] == {"unsettled": 21}
    assert learning["details"]["window_start"] == "1970-01-01T00:00:00+00:00"
    assert requested_limits == [100]
    rendered = alpha_attribution.render_alpha_audit(
        alpha_attribution.build_alpha_attribution_audit(days=30)
    )
    assert f"Learning experiment: id={experiment.experiment_id} stage=paper_10" in rendered
    assert 'exclusions={"unsettled": 21}' in rendered

    item["measurement"].update(
        state="positive",
        baseline=0.0,
        observed=1.0,
        delta=1.0,
        window_start="2026-08-09T09:00:00+00:00",
        window_end="2026-08-09T09:10:00+00:00",
    )
    item["samples"].update(treatment_assignment_units=50, control_assignment_units=50)
    monitoring_positive = alpha_attribution.build_alpha_attribution_audit(days=30)["verdicts"]["learning"]

    assert monitoring_positive["verdict"] == "positive"
    assert monitoring_positive["contribution_verdict"] == "insufficient_data"
    assert monitoring_positive["measurement_status"] == "directional_evidence_only"

    item["measurement"]["window_start"] = None
    missing_window = alpha_attribution.build_alpha_attribution_audit(days=30)["verdicts"]["learning"]
    assert missing_window["verdict"] == "insufficient_data"
    assert missing_window["details"]["audit_window_compatible"] is False

    def unavailable_projection(*, limit):
        raise RuntimeError("projection failed")

    monkeypatch.setattr(
        learning_read_models,
        "build_learning_contribution_projection",
        unavailable_projection,
    )
    unavailable = alpha_attribution.build_alpha_attribution_audit(days=30)["verdicts"]["learning"]

    assert unavailable["verdict"] == "insufficient_data"
    assert unavailable["evidence_type"] == "dedicated_learning_contribution_monitoring"
    assert "projection is unavailable" in unavailable["reason"]


def test_learning_alpha_consumes_exact_confirmatory_authority_verdict(monkeypatch):
    import aletheia.analytics.alpha_attribution as alpha_attribution
    from aletheia.learning.contribution_runner import evaluate_experiment
    from aletheia.learning.fixed_look import register_fixed_look_decision
    from tests.test_learning_verdict_runtime import (
        _experiment,
        _exposure,
        _iso as learning_iso,
        _outcome,
    )

    monkeypatch.setattr(
        alpha_attribution,
        "_utcnow",
        lambda: datetime(2026, 8, 9, 10, tzinfo=timezone.utc),
    )

    hypothesis, experiment = _experiment(min_samples=50, min_days=0)
    register_fixed_look_decision(
        experiment,
        hypothesis,
        policy_epoch="epoch-a",
        behavior_contract_hash="behavior-v1",
    )
    for index in range(50):
        for assignment, net, offset in (("treatment", 1.0, 0), ("control", 0.0, 1)):
            candidate = f"alpha-{assignment}-{index:02d}"
            _exposure(
                experiment,
                hypothesis,
                candidate=candidate,
                assignment=assignment,
                exposed_at=learning_iso(minutes=index * 2 + offset),
            )
            _outcome(
                candidate,
                net=net,
                entry_at=learning_iso(minutes=index * 2 + offset + 1),
            )
    evaluate_experiment(experiment.experiment_id)

    learning = alpha_attribution.build_alpha_attribution_audit(days=30)["verdicts"]["learning"]

    assert learning["verdict"] == "positive"
    assert learning["contribution_verdict"] == "positive"
    assert learning["measurement_status"] == "measured_contribution"
    assert learning["causal_grade"] == "strong_prospective_control"
    assert learning["evidence_type"] == "dedicated_learning_contribution_verdict"
    assert learning["details"]["path_id"] == "universe_family_calibration"
    assert learning["details"]["unique_treatment_n"] == 50
    assert learning["details"]["unique_control_n"] == 50


def test_learning_alpha_rejects_authority_verdict_spanning_before_audit_window(monkeypatch):
    import aletheia.analytics.alpha_attribution as alpha_attribution
    from aletheia.learning.contribution_runner import evaluate_experiment
    from aletheia.learning.fixed_look import register_fixed_look_decision
    from tests.test_learning_verdict_runtime import (
        _experiment,
        _exposure,
        _iso as learning_iso,
        _outcome,
    )

    monkeypatch.setattr(
        alpha_attribution,
        "_utcnow",
        lambda: datetime(2026, 8, 9, 10, tzinfo=timezone.utc),
    )

    hypothesis, experiment = _experiment(min_samples=50, min_days=0)
    register_fixed_look_decision(
        experiment,
        hypothesis,
        policy_epoch="epoch-a",
        behavior_contract_hash="behavior-v1",
    )
    for index in range(50):
        for assignment, net, offset in (("treatment", 1.0, 0), ("control", 0.0, 1)):
            candidate = f"old-alpha-{assignment}-{index:02d}"
            exposed_at = (
                learning_iso(days=-1, minutes=offset)
                if index == 0
                else learning_iso(minutes=index * 2 + offset)
            )
            _exposure(
                experiment,
                hypothesis,
                candidate=candidate,
                assignment=assignment,
                exposed_at=exposed_at,
            )
            _outcome(candidate, net=net, entry_at=learning_iso(minutes=index * 2 + offset + 1))
    evaluate_experiment(experiment.experiment_id)

    learning = alpha_attribution.build_alpha_attribution_audit(days=27)["verdicts"]["learning"]

    assert learning["contribution_verdict"] == "insufficient_data"
    assert learning["measurement_status"] == "not_measured"


def test_weak_negative_keeps_diagnostics_without_prescriptive_rendering():
    from aletheia.analytics.alpha_attribution import (
        build_alpha_attribution_audit,
        render_alpha_audit,
    )
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(5):
        batch = f"module-b{i}"
        _insert_shadow(db, batch, f"opp-r1m-{i}", 1, -2.0, selected=1)
        _insert_shadow(db, batch, f"opp-r2m-{i}", 2, 2.0)
    db.commit()

    report = build_alpha_attribution_audit(days=30)
    ranker = report["verdicts"]["ranker"]

    assert ranker["verdict"] == "negative"
    assert "aletheia/agents/paper_trader_ranker.py" in ranker["suspected_modules"]
    assert report["main_bottleneck"]["layer"] == "insufficient_data"
    assert report["main_bottleneck"]["suspected_modules"] == []
    rendered = render_alpha_audit(report)
    assert "Suspect modules:" not in rendered
    assert "Inspect scoring weights" not in rendered
    assert "No actionable gradient is currently established" in rendered


def test_serenity_insert_records_prospective_alpha_controls():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityOpportunity
    from aletheia.storage.sqlite import get_db

    db = get_db()
    entry = _iso(days_ago=1)
    _insert_ohlcv(db, "AAPL", "1d", entry, 100.0, {24: 101.0})
    _insert_ohlcv(db, "MSFT", "1d", entry, 100.0, {24: 100.0})
    db.executemany(
        "INSERT INTO universe_registry "
        "(id, venue, market_type, symbol, status, is_blacklisted) "
        "VALUES (?, 'legacy_unknown', 'spot', ?, 'active', 0)",
        (("aapl-active", "AAPL"), ("msft-active", "MSFT")),
    )
    db.commit()

    opp = SerenityOpportunity(
        id="ser-alpha-1",
        theme_id="theme-alpha",
        symbol="AAPL",
        direction="long",
        asset_class="equity",
        created_at=entry,
    )
    repo.insert_serenity_opportunity(opp)

    rows = db.execute(
        "SELECT role, symbol, layer FROM alpha_control_observations "
        "WHERE layer = 'serenity' AND treatment_id = 'ser-alpha-1'"
    ).fetchall()
    assert any(r["role"] == "selected" and r["symbol"] == "AAPL" for r in rows)
    assert any(r["role"] == "control" and r["symbol"] == "MSFT" for r in rows)


def test_cli_and_api_surface_alpha_audit():
    from aletheia.main import app
    from aletheia.server.app import app as fastapi_app
    from aletheia.server.routes.analytics import get_alpha_attribution

    runner = CliRunner()
    result = runner.invoke(app, ["alpha", "audit", "--days", "7"])

    assert result.exit_code == 0
    assert "Alpha Attribution Audit" in result.output
    assert "System Operator:" in result.output
    assert "Universe Twin evidence:" in result.output

    assert any(route.path == "/api/analytics/alpha-attribution" for route in fastapi_app.routes)
    data = get_alpha_attribution(days=7)
    assert data["window_days"] == 7
    assert "universe_twin_evidence" in data
    assert set(data["verdicts"]) == {"universe", "ranker", "system_operator", "learning", "serenity"}
