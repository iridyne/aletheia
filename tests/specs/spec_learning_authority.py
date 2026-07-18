"""Executable authority limits for factor and universe Learning paths."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.specs.spec_meta import SpecClaim, spec_claim


def _enable_legacy_paper_authority(monkeypatch, path_id: str) -> None:
    from aletheia.config.schema import AletheiaConfig
    import aletheia.learning.authority as authority

    cfg = AletheiaConfig()
    cfg.agent.autonomous_learning = True
    cfg.runtime.paused = False
    cfg.learning.paper_influence_enabled = True
    cfg.learning.paper_influence_paths = [path_id]
    monkeypatch.setattr(authority, "load_config", lambda: cfg)


def _insert_settled_outcome(candidate_key: str) -> str:
    """Insert one clean cost-aware outcome and return its exposure link key."""
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    base = candidate_key.replace(":", "_")
    db = get_db()
    was_in_tx = db.in_transaction
    db.execute(
        "INSERT INTO filter_candidate_outcomes ("
        "id, outcome_candidate_key, venue, symbol, market_type, direction, horizon, "
        "entry_price_usd, entry_ts, due_at, settled_at, net_return_pct, "
        "cost_model_version, status) VALUES (?, ?, 'test', 'BTC/USDT', 'perp', "
        "'long', '4h', 100, ?, ?, ?, 0.2, 'test-v1', 'settled')",
        (f"outcome-{base}", base, now, now, now),
    )
    if not was_in_tx:
        db.commit()
    return f"{base}:4h"


@spec_claim(SpecClaim(
    "LE-B05", "learning_evolution", "§7 stable assignment", "L2b",
    "Experiment assignment is deterministic from experiment id, salt, and causal candidate key.",
    status="implemented",
))
def test_stable_assignment_survives_retries():
    from aletheia.learning.experiments import stable_assignment
    from aletheia.learning.models import LearningExperiment

    experiment = LearningExperiment(
        experiment_id="exp-stable", hypothesis_id="h1", assignment_salt="private-random-salt",
        allocation_pct=0.25,
    )
    first = stable_assignment(experiment, "opportunity:123")
    assert first in {"control", "treatment"}
    assert {stable_assignment(experiment, "opportunity:123") for _ in range(10)} == {first}


@spec_claim(
    SpecClaim(
        "LE-B06", "learning_evolution", "§7 ranker before/after exposure", "L2b",
        "An authorized treatment delta records the exact before/after value and candidate identity; "
        "a control assignment keeps the baseline.", status="implemented",
    ),
    SpecClaim(
        "LE-C01", "learning_evolution", "§8 promotion evidence", "L2c",
        "No treatment authority exists without unique treatment/control exposures, minimum time, "
        "and real clean cost-aware outcome links.", status="implemented",
    ),
)
def test_authorized_numeric_treatment_records_before_after(monkeypatch):
    from aletheia.learning.experiments import apply_numeric_treatment, stable_assignment
    from aletheia.learning.ledger import get_record, insert_record
    from aletheia.learning.models import LearningContributionVerdict
    from aletheia.storage.sqlite import get_db

    common = dict(
        path_id="ranker_factor_filter",
        scope={"factor_key": "momentum:BTC/USDT:long:high:clean"},
        action={"type": "score_delta", "delta": -10.0},
        source_cohort="factor_feedback:v2", target_horizon="4h",
        baseline=50.0, treatment=40.0, consumer="paper_ranker",
        consumer_stage="score",
    )
    _enable_legacy_paper_authority(monkeypatch, common["path_id"])
    shadow = apply_numeric_treatment(candidate_key="candidate:seed", **common)
    db = get_db()
    was_in_tx = db.in_transaction
    db.execute(
        "UPDATE learning_experiments SET stage='paper_10', status='active', "
        "min_samples_per_arm=1 WHERE experiment_id=?",
        (shadow.experiment_id,),
    )
    if not was_in_tx:
        db.commit()
    experiment_row = get_record("learning_experiments", shadow.experiment_id)
    from aletheia.learning.models import LearningExperiment
    experiment = LearningExperiment(**experiment_row)
    candidate = next(
        f"candidate:{i}" for i in range(1000)
        if stable_assignment(experiment, f"candidate:{i}") == "treatment"
    )
    control_candidate = next(
        f"control:{i}" for i in range(1000)
        if stable_assignment(experiment, f"control:{i}") == "control"
    )
    apply_numeric_treatment(
        candidate_key=candidate,
        outcome_candidate_key=_insert_settled_outcome(candidate), **common,
    )
    apply_numeric_treatment(
        candidate_key=control_candidate,
        outcome_candidate_key=_insert_settled_outcome(control_candidate), **common,
    )
    now = datetime.now(timezone.utc)
    # P0-4: the verdict must carry the current cohort identity (epoch + stage) or it
    # cannot authorize. Read the epoch the exposures were stamped with.
    epoch = get_record("learning_exposures", shadow.exposure_id)["concurrent_policy_epoch"]
    insert_record("learning_contribution_verdicts", LearningContributionVerdict(
        experiment_id=shadow.experiment_id, hypothesis_id=shadow.hypothesis_id,
        window_start=(now - timedelta(days=14)).isoformat(), window_end=now.isoformat(),
        unique_treatment_n=1, unique_control_n=1, baseline=0.0, observed=0.2,
        delta=0.2, verdict="promote", reason="prospective net improvement",
        policy_epoch=epoch, stage="paper_10",
    ))
    applied_candidate = next(
        f"applied:{i}" for i in range(1000)
        if stable_assignment(experiment, f"applied:{i}") == "treatment"
    )
    applied = apply_numeric_treatment(candidate_key=applied_candidate, **common)

    assert applied.applied and applied.value == 40.0
    exposure = get_record("learning_exposures", applied.exposure_id)
    assert exposure["candidate_key"] == applied_candidate
    assert '"value": 50.0' in exposure["before_json"]
    assert '"value": 40.0' in exposure["after_json"]

    later_control = next(
        f"later-control:{i}" for i in range(1000)
        if stable_assignment(experiment, f"later-control:{i}") == "control"
    )
    control = apply_numeric_treatment(candidate_key=later_control, **common)
    assert not control.applied and control.value == 50.0


@spec_claim(
    SpecClaim(
        "LE-C02", "learning_evolution", "§8 continual refreeze", "L2c",
        "A newer negative prospective verdict immediately removes treatment authority.",
        status="implemented",
    ),
    SpecClaim(
        "LE-C03", "learning_evolution", "§8 static fallback", "L2c",
        "Refreeze restores the declared numeric baseline without deleting prior evidence.",
        status="implemented",
    ),
    SpecClaim(
        "LE-C12", "learning_evolution", "§9.8 universe rollback", "L2c",
        "A negative or invalid family-calibration holdout restores the neutral multiplier.",
        status="implemented",
    ),
)
def test_negative_verdict_refreezes_to_static_baseline(monkeypatch):
    from aletheia.learning.experiments import apply_numeric_treatment, stable_assignment
    from aletheia.learning.ledger import get_record, insert_record
    from aletheia.learning.models import LearningContributionVerdict, LearningExperiment
    from aletheia.storage.sqlite import get_db

    kwargs = dict(
        path_id="universe_family_calibration",
        scope={"family": "momentum", "direction": "long", "market_type": "perp"},
        action={"type": "opportunity_multiplier", "multiplier": 1.1},
        source_cohort="universe_forward_outcomes:clean_net:v2", target_horizon="4h",
        baseline=70.0, treatment=77.0, consumer="universe_allocator",
        consumer_stage="opportunity_score",
    )
    _enable_legacy_paper_authority(monkeypatch, kwargs["path_id"])
    created = apply_numeric_treatment(candidate_key="seed", **kwargs)
    db = get_db()
    was_in_tx = db.in_transaction
    db.execute(
        "UPDATE learning_experiments SET stage='paper_10', status='active', "
        "min_samples_per_arm=1 WHERE experiment_id=?",
        (created.experiment_id,),
    )
    if not was_in_tx:
        db.commit()
    experiment = LearningExperiment(**get_record("learning_experiments", created.experiment_id))
    treatment_candidate = next(
        f"treatment:{i}" for i in range(1000)
        if stable_assignment(experiment, f"treatment:{i}") == "treatment"
    )
    control_candidate = next(
        f"negative-control:{i}" for i in range(1000)
        if stable_assignment(experiment, f"negative-control:{i}") == "control"
    )
    apply_numeric_treatment(
        candidate_key=treatment_candidate,
        outcome_candidate_key=_insert_settled_outcome(treatment_candidate), **kwargs,
    )
    apply_numeric_treatment(
        candidate_key=control_candidate,
        outcome_candidate_key=_insert_settled_outcome(control_candidate), **kwargs,
    )
    now = datetime.now(timezone.utc)
    epoch = get_record("learning_exposures", created.exposure_id)["concurrent_policy_epoch"]
    positive = LearningContributionVerdict(
        experiment_id=experiment.experiment_id, hypothesis_id=experiment.hypothesis_id,
        window_start=(now - timedelta(days=14)).isoformat(), window_end=now.isoformat(),
        unique_treatment_n=1, unique_control_n=1, verdict="promote", reason="positive",
        created_at=now.isoformat(), policy_epoch=epoch, stage="paper_10",
    )
    insert_record("learning_contribution_verdicts", positive)
    authorized_candidate = next(
        f"authorized:{i}" for i in range(1000)
        if stable_assignment(experiment, f"authorized:{i}") == "treatment"
    )
    assert apply_numeric_treatment(candidate_key=authorized_candidate, **kwargs).value == 77.0

    negative = positive.model_copy(update={
        "verdict_id": "negative-verdict",
        "verdict": "refreeze", "reason": "holdout harmed",
        "created_at": (now + timedelta(seconds=1)).isoformat(),
    })
    insert_record("learning_contribution_verdicts", negative)
    refrozen_candidate = next(
        f"refrozen-treatment:{i}" for i in range(1000)
        if stable_assignment(experiment, f"refrozen-treatment:{i}") == "treatment"
    )
    refrozen = apply_numeric_treatment(candidate_key=refrozen_candidate, **kwargs)
    assert refrozen.value == 70.0
    assert not refrozen.applied
    assert get_record("learning_contribution_verdicts", positive.verdict_id) is not None


@spec_claim(SpecClaim(
    "LE-B13", "learning_evolution", "§9.3 factor feedback", "L2b",
    "Retrospective factor statistics are diagnostic only: exact clean evidence proposes a shadow "
    "experiment, while the ranker remains neutral until prospective authority exists.",
    status="implemented",
))
def test_factor_history_is_shadow_only_until_prospectively_authorized(monkeypatch):
    from aletheia.analytics import factor_feedback
    from tests.test_outcome_closure import _insert_entry_chain

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for i in range(12):
        position_id = f"position-{i}"
        opportunity_id, proposal_id, order_id = _insert_entry_chain(
            position_id=position_id,
            symbol="BTC/USDT",
            market_type="usdt_futures",
            direction="long",
            quantity=1.0,
            price=100.0,
            execution_mode="paper_internal",
        )
        rows.append({
            "id": position_id, "position_id": position_id,
            "opportunity_id": opportunity_id,
            "proposal_id": proposal_id,
            "order_id": order_id,
            "scanner_family": "momentum", "symbol": "BTC/USDT", "direction": "long",
            "confidence_score": 80, "late_entry_penalty": 0, "realized_pnl": -2.0,
            "market_type": "usdt_futures", "execution_mode": "paper_internal",
            "status": "closed", "entry_price": 100.0, "current_price": 98.0,
            "quantity": 1.0, "entry_fee": 0.0, "exit_fee": 0.0,
            "funding_cost": 0.0, "funding_source": "local_simulation",
            "entry_price_source": "local_fill", "entry_fill_verified": True,
            "closed_at": now,
            "close_reason": "stop_loss",
            "close_category": "strategy", "exit_price_source": "local_fill",
            "exit_fill_verified": True, "outcome_eligible": True,
            "outcome_exclusion_reason": "eligible",
            "outcome_semantics_version": "oi-v1",
        })
    monkeypatch.setattr(factor_feedback, "build_trade_attribution_rows", lambda limit=500: rows)
    signal = factor_feedback.lookup_factor_signal({
        "opportunity_id": "opp-1", "universe_member_id": "member-1",
        "scanner_family": "momentum", "symbol": "BTC/USDT", "direction": "long",
        "confidence_score": 80, "late_entry_penalty": 0,
    })

    assert signal["observed_status"] == "hard_blocked"
    assert signal["status"] == "watch"
    assert signal["authority_applied"] is False
    assert signal["experiment_id"]

    broad_only = factor_feedback.lookup_factor_signal({
        "opportunity_id": "opp-2", "scanner_family": "momentum",
        "symbol": "ETH/USDT", "direction": "long",
        "confidence_score": 80, "late_entry_penalty": 0,
    })
    assert broad_only["trade_count"] == 0
    assert broad_only["reason"] == "no_history"


@spec_claim(SpecClaim(
    "LE-B20", "learning_evolution", "§9.8 universe calibration", "L2b",
    "A retrospective family multiplier records a prospective shadow exposure but remains at the "
    "neutral 1.0 baseline until treatment/control evidence authorizes influence.",
    status="implemented",
))
def test_universe_calibration_is_neutral_shadow_before_authority(monkeypatch):
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.allocator import compute_total_score
    from aletheia.config.schema import UniverseConfig

    feat = {
        "feature_snapshot_id": "feature-1", "asset_id": "asset-1",
        "symbol": "BTC/USDT", "market_type": "perp", "tradability_score": 80.0,
        "quote_volume_24h": 50_000_000, "spread_bps": 5.0, "rsi_14": 55.0,
        "return_1h": 1.0, "return_4h": 2.0, "data_freshness_sec": 200,
    }
    scanner = {
        "opportunity_score": 70.0, "scanner_family": "momentum",
        "scanner_direction": "long",
    }
    neutral = compute_total_score(dict(feat), scanner, UniverseConfig(), calibration=None)
    proposed_feat = dict(feat)
    proposed = compute_total_score(
        proposed_feat, scanner, UniverseConfig(), calibration={"momentum:long:perp": 1.4},
    )

    assert proposed == neutral
    row = get_db().execute(
        "SELECT * FROM learning_exposures WHERE path_id='universe_family_calibration'"
    ).fetchone()
    assert row is not None
    assert row["assignment"] in {"control", "treatment"}
    assert '"proposed_multiplier": 1.1' in row["manifest_json"]

    from aletheia.universe import outcome_recorder
    captured = []
    monkeypatch.setattr(
        outcome_recorder.repo, "insert_forward_outcomes_batch",
        lambda outcomes: captured.extend(outcomes),
    )
    outcome_recorder.register_pending([{
        "id": "member-1", "snapshot_id": "snapshot-1", "symbol": "BTC/USDT",
        "scanner_family": "momentum", "direction": "long", "market_type": "perp",
        "entry_price_usd": 100.0, "learning_candidate_key": proposed_feat["learning_candidate_key"],
    }], entry_ts=datetime.now(timezone.utc).isoformat())
    assert {item.outcome_candidate_key for item in captured} == {
        f"{proposed_feat['learning_candidate_key']}:{horizon}" for horizon in ("15m", "1h", "4h", "24h")
    }


@spec_claim(SpecClaim(
    "LE-B22", "learning_evolution", "§9.3 factor identity", "L2b",
    "Producer, ranker, and Operator context use factor_key as the same stable identity field.",
    status="implemented",
))
def test_operator_factor_context_uses_factor_key(monkeypatch):
    from aletheia.agent_shell import trading_context
    from aletheia.analytics import factor_feedback

    monkeypatch.setattr(factor_feedback, "build_factor_feedback", lambda limit=200, min_trades=3: [{
        "factor_key": "momentum:BTC/USDT:long:high:clean", "group_type": "exact",
        "status": "blocked_candidate", "win_rate": 0.2, "expectancy": -1.0,
        "trade_count": 20,
    }])
    layer = trading_context._build_layer1_snapshot()
    assert layer["factor_feedback"][0]["factor_key"] == "momentum:BTC/USDT:long:high:clean"
    assert "key" not in layer["factor_feedback"][0]


@spec_claim(SpecClaim(
    "LE-B26", "learning_evolution", "§9.8 universe selection bias", "L2b",
    "Every pre-scored universe candidate (treatment or control, selected or dropped) gets a "
    "persisted exposure-aligned outcome, and complete candidate clusters are the statistical "
    "assignment units, so contribution compares full arms rather than survivors.",
    status="implemented",
))
def test_dropped_experiment_candidates_settle_into_both_arms():
    from aletheia.universe.service import _register_experiment_candidate_outcomes
    from aletheia.learning.contribution_runner import evaluate_experiment
    from aletheia.learning.models import (
        LearningExperiment,
        LearningExposure,
        LearningHypothesis,
    )
    from aletheia.learning.ledger import (
        insert_experiment,
        insert_exposure,
        insert_hypothesis,
    )
    from aletheia.storage.sqlite import get_db

    epoch, exp = "epoch-p03", "exp-p03"
    ck_t = "universe:featT:momentum:long:perp"
    ck_c = "universe:featC:momentum:long:perp"
    hypothesis = LearningHypothesis(
        hypothesis_id="h-p03",
        fingerprint="fp-p03",
        cohort_id="universe-full-arm",
        path_id="universe_family_calibration",
        overlap_group="universe",
        source_type="filter_candidate_outcomes:clean_net:v1",
        scope_json='{"family":"momentum","direction":"long","market_type":"perp"}',
        action_json='{"type":"opportunity_multiplier","multiplier":1.1}',
        consumer="universe_allocator",
        target_horizon="4h",
    )
    experiment = LearningExperiment(
        experiment_id=exp,
        hypothesis_id=hypothesis.hypothesis_id,
        assignment_salt="salt-p03",
        min_samples_per_arm=1,
        min_market_days=0,
        status="active",
        started_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )
    assert insert_hypothesis(hypothesis)
    assert insert_experiment(experiment)
    for ck, arm in ((ck_t, "treatment"), (ck_c, "control")):
        assert insert_exposure(LearningExposure(
            dedup_key=f"{exp}:{ck}:paper_10:{epoch}",
            experiment_id=exp, hypothesis_id="h-p03", assignment=arm,
            consumer="universe_allocator", consumer_stage="opportunity_score:paper_10",
            path_id="universe_family_calibration", overlap_group="universe",
            assignment_unit_key=ck,
            candidate_key=ck, outcome_candidate_key=f"{ck}:4h",
            outcome_source="fixed_horizon_candidate", concurrent_policy_epoch=epoch,
            experiment_stage="shadow",
            code_commit="test-commit",
        ))

    now = datetime.now(timezone.utc).isoformat()
    scored = [
        {"learning_candidate_key": ck_t, "symbol": "SOL/USDT", "venue": "okx",
         "market_type": "perp", "scanner_family": "momentum",
         "scanner_direction": "long", "last_price": 100.0,
         "allocation_status": "selected"},
        {"learning_candidate_key": ck_c, "symbol": "SOL/USDT", "venue": "okx",
         "market_type": "perp", "scanner_family": "momentum",
         "scanner_direction": "long", "last_price": 100.0,
         "allocation_status": "dropped"},
    ]

    class _MD:
        ccxt_exchange = "okx"

    class _Cfg:
        market_data = _MD()

    _register_experiment_candidate_outcomes(
        scored,
        _Cfg(),
        entry_ts=now,
        allocated=[scored[0]],
    )
    db = get_db()
    registered = db.execute(
        "SELECT outcome_candidate_key, horizon, allocation_selected "
        "FROM filter_candidate_outcomes "
        "ORDER BY outcome_candidate_key, horizon"
    ).fetchall()
    assert len(registered) == 8
    assert {row[0] for row in registered} == {ck_t, ck_c}
    assert {row[1] for row in registered} == {"15m", "1h", "4h", "24h"}
    assert {
        row[0]: bool(row[2]) for row in registered if row[1] == "4h"
    } == {ck_t: True, ck_c: False}

    was = db.in_transaction
    db.execute(
        "UPDATE filter_candidate_outcomes SET status='settled', net_return_pct=0.3, "
        "estimated_fee_pct=0.1, estimated_spread_slippage_pct=0.1, "
        "estimated_funding_pct=0.0, mae_pct=-0.1, cost_model_version='test-v1', settled_at=? "
        "WHERE outcome_candidate_key IN (?, ?) AND horizon='4h'",
        (now, ck_t, ck_c),
    )
    if not was:
        db.commit()

    verdict = evaluate_experiment(exp)[0]
    assert verdict.unique_treatment_n == 1
    assert verdict.unique_control_n == 1
    assert verdict.observed == 0.3
    assert verdict.baseline == 0.0


@spec_claim(SpecClaim(
    "LE-C15", "learning_evolution", "§8 verdict cohort identity", "L2c",
    "A promote verdict stamped with a different policy epoch cannot authorize the "
    "treatment: stale evidence never grants authority to a new cohort.",
    status="implemented",
))
def test_stale_epoch_verdict_does_not_authorize():
    from aletheia.learning.experiments import apply_numeric_treatment, stable_assignment
    from aletheia.learning.ledger import get_record, insert_record
    from aletheia.learning.models import LearningContributionVerdict, LearningExperiment
    from aletheia.storage.sqlite import get_db

    common = dict(
        path_id="ranker_factor_filter",
        scope={"factor_key": "momentum:ETH/USDT:long:high:clean"},
        action={"type": "score_delta", "delta": -10.0},
        source_cohort="factor_feedback:v2", target_horizon="4h",
        baseline=50.0, treatment=40.0, consumer="paper_ranker", consumer_stage="score",
    )
    shadow = apply_numeric_treatment(candidate_key="candidate:seed2", **common)
    db = get_db()
    was = db.in_transaction
    db.execute(
        "UPDATE learning_experiments SET stage='paper_10', status='active', "
        "min_samples_per_arm=1 WHERE experiment_id=?",
        (shadow.experiment_id,),
    )
    if not was:
        db.commit()
    experiment = LearningExperiment(**get_record("learning_experiments", shadow.experiment_id))
    t = next(f"t:{i}" for i in range(1000) if stable_assignment(experiment, f"t:{i}") == "treatment")
    c = next(f"c:{i}" for i in range(1000) if stable_assignment(experiment, f"c:{i}") == "control")
    apply_numeric_treatment(candidate_key=t, outcome_candidate_key=_insert_settled_outcome(t), **common)
    apply_numeric_treatment(candidate_key=c, outcome_candidate_key=_insert_settled_outcome(c), **common)

    now = datetime.now(timezone.utc)
    # A genuine "promote" verdict — but stamped with a STALE policy epoch (config/code
    # has since moved on). It must NOT authorize the current cohort.
    insert_record("learning_contribution_verdicts", LearningContributionVerdict(
        experiment_id=shadow.experiment_id, hypothesis_id=shadow.hypothesis_id,
        window_start=(now - timedelta(days=14)).isoformat(), window_end=now.isoformat(),
        unique_treatment_n=1, unique_control_n=1, verdict="promote", reason="stale",
        policy_epoch="STALE-EPOCH", stage="paper_10",
    ))
    applied_candidate = next(
        f"a:{i}" for i in range(1000)
        if stable_assignment(experiment, f"a:{i}") == "treatment"
    )
    decision = apply_numeric_treatment(candidate_key=applied_candidate, **common)
    assert not decision.applied
    assert decision.value == 50.0  # baseline — a stale-epoch verdict grants no authority


@spec_claim(SpecClaim(
    "LE-B27", "learning_evolution", "§ exposure fingerprint", "L2b",
    "The policy epoch stays stable across code-only rebuilds, rotates when universe "
    "behavior config changes, and each exposure is stamped with the running build.",
    status="implemented",
))
def test_policy_epoch_ignores_code_only_rebuild_and_rotates_on_config_change(monkeypatch):
    from aletheia.learning import experiments as exp
    import aletheia.agent_runtime.build_info as build_info
    from aletheia.config.loader import load_config
    from aletheia.learning.ledger import get_record

    base_cfg = load_config()
    monkeypatch.setattr(exp, "load_config", lambda: base_cfg)

    monkeypatch.setattr(build_info, "get_boot_commit", lambda: "commit-AAA")
    e1 = exp._policy_epoch("universe_family_calibration")
    assert e1 == exp._policy_epoch("universe_family_calibration")  # deterministic

    monkeypatch.setattr(build_info, "get_boot_commit", lambda: "commit-BBB")
    assert exp._policy_epoch("universe_family_calibration") == e1  # same effective policy

    changed = base_cfg.model_copy(update={
        "universe": base_cfg.universe.model_copy(
            update={"explore_ratio": base_cfg.universe.explore_ratio + 0.13})
    })
    monkeypatch.setattr(exp, "load_config", lambda: base_cfg)
    e_base = exp._policy_epoch("universe_family_calibration")
    monkeypatch.setattr(exp, "load_config", lambda: changed)
    assert exp._policy_epoch("universe_family_calibration") != e_base  # config change rotates it

    # Each exposure is stamped with the running build.
    monkeypatch.setattr(build_info, "get_boot_commit", lambda: "commit-XYZ")
    monkeypatch.setattr(exp, "load_config", lambda: base_cfg)
    d = exp.apply_numeric_treatment(
        path_id="ranker_factor_filter",
        scope={"factor_key": "momentum:BTC/USDT:long:high:clean"},
        action={"type": "score_delta", "delta": -10.0},
        source_cohort="factor_feedback:v2", target_horizon="4h",
        candidate_key="fp-candidate", baseline=50.0, treatment=40.0,
        consumer="paper_ranker", consumer_stage="score",
    )
    assert get_record("learning_exposures", d.exposure_id)["code_commit"] == "commit-XYZ"
