"""Final consumer authority checks for the measured Learning loop."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests.specs.spec_meta import SpecClaim, spec_claim


def _fresh_ranker_mark(_opp: dict) -> dict:
    return {
        "price": 100.0,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "venue": "bitget",
    }


def _operator_prompt_context() -> dict:
    return {
        "layer1": {"positions": [{"id": "position-1"}]},
        "layer2": {
            "reflection": "learned reflection",
            "trading_memo": {"summary": "learned memo"},
            "reasoning_feedback": "learned reasoning",
            "calibration_feedback": "learned calibration",
            "pending_signals": [{
                "opportunity_id": "opp-authority",
                "signal_source": "market_observer",
                "symbol": "BTC/USDT",
                "direction": "long",
                "market_type": "spot",
                "at": "2026-07-14T00:00:00+00:00",
            }],
            "pending_queue": {"read_status": "complete", "unresolved_total": 1},
        },
        "layer3": {
            "lessons": [{"content": "learned case"}],
            "ps_lessons": [{"summary": "learned structured lesson"}],
            "guard_candidates": [{"content": "learned guard"}],
        },
    }


@spec_claim(
    SpecClaim(
        "LE-C17", "learning_evolution", "§10 live diagnostic context", "L2c",
        "The active Operator PI session receives only reviewed, current Learning lessons as "
        "an advisory context, carries a manifest for audit, and does not enable a paper "
        "Learning treatment or trading authority.",
        status="implemented",
    )
)
def test_live_operator_diagnostic_learning_is_curated_and_advisory(monkeypatch):
    from aletheia.agent_shell import context

    monkeypatch.setattr(
        context,
        "LessonStore",
        lambda: SimpleNamespace(list_all=lambda **_: [
            SimpleNamespace(
                lesson_id="current-reviewed-lesson",
                category="quality",
                severity="warning",
                summary="Require stronger evidence before a parameter proposal.",
                created_at="2026-07-14T00:00:00+00:00",
                expires_at="2027-08-01T00:00:00+00:00",
            ),
            SimpleNamespace(
                lesson_id="expired-reviewed-lesson",
                category="quality",
                severity="warning",
                summary="This stale lesson must stay out of the prompt.",
                created_at="2026-06-01T00:00:00+00:00",
                expires_at="2026-06-02T00:00:00+00:00",
            ),
        ]),
    )

    diagnostic = context.build_app_context(["learning"])["learning"]
    assert diagnostic["advisory_only"] is True
    assert diagnostic["manifest"]["source"] == "applied_ps_lessons"
    assert diagnostic["manifest"]["lesson_ids"] == ["current-reviewed-lesson"]
    assert [row["lesson_id"] for row in diagnostic["lessons"]] == ["current-reviewed-lesson"]

    from aletheia.server.routes.operator import operator_llm_session
    import inspect
    source = inspect.getsource(operator_llm_session)
    assert '["system", "market", "agents", "trading", "learning"]' in source
    assert '"learning_manifest": learning_manifest' in source


@spec_claim(
    SpecClaim(
        "LE-C16", "learning_evolution", "§10 prompt authority", "L2c",
        "Measurement-unsupported Observer and Operator Learning bundles retain the static "
        "prompt baseline without creating a formal exposure or authority identity.",
        status="implemented",
    )
)
def test_unsupported_prompt_bundles_keep_static_baselines(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.learning import prompt_bundles

    cfg = AletheiaConfig()
    cfg.agent.autonomous_learning = True
    cfg.runtime.paused = False
    # Defaults are deliberately fail-closed: paper influence off + empty allowlist.
    monkeypatch.setattr("aletheia.learning.control.load_config", lambda: cfg)
    monkeypatch.setattr(
        "aletheia.learning.control.global_learning_refreeze_reason", lambda: None,
    )
    monkeypatch.setattr("aletheia.learning.authority.load_config", lambda: cfg)
    monkeypatch.setattr(
        "aletheia.learning.authority.global_learning_refreeze_reason", lambda: None,
    )
    monkeypatch.setattr("aletheia.learning.experiments.load_config", lambda: cfg)
    import aletheia.agent_shell.trading_context as trading_context
    monkeypatch.setattr(trading_context, "_build_layer1_snapshot", lambda: _operator_prompt_context()["layer1"])
    monkeypatch.setattr(trading_context, "_build_layer2_short_term", lambda: _operator_prompt_context()["layer2"])
    monkeypatch.setattr(trading_context, "_build_layer3_wisdom", lambda: _operator_prompt_context()["layer3"])

    default_off = trading_context.build_enriched_trading_context()
    assert default_off["layer2"]["reflection"] is None
    assert default_off["layer3"]["lessons"] == []
    assert default_off["learning_manifest"]["authority"]["applied"] is False
    assert default_off["learning_manifest"]["authority"]["reason"] == "measurement_unsupported"
    assert default_off["learning_manifest"]["authority"]["hypothesis_id"] is None
    assert default_off["learning_manifest"]["authority"]["experiment_id"] is None
    assert default_off["learning_manifest"]["authority"]["exposure_id"] is None

    observer_default = prompt_bundles.authorize_observer_learning_bundle(
        "learned observer memo", "learned symbol guard",
        candidate_key="observer_member:snapshot-1:member-1:spot",
    )
    assert observer_default[0] == ""
    assert observer_default[1]["components"] == {}
    assert observer_default[1]["authority"]["applied"] is False
    assert observer_default[1]["authority"]["reason"] == "measurement_unsupported"
    assert observer_default[1]["authority"]["hypothesis_id"] is None
    assert observer_default[1]["authority"]["experiment_id"] is None
    assert observer_default[1]["authority"]["exposure_id"] is None

    from aletheia.agents.market_observer import _observer_learning_candidate_key
    member_first = _observer_learning_candidate_key(
        run_id="run-1", venue="bitget", market_type="spot", symbol="BTC/USDT",
        universe_snapshot_id="snapshot-1", universe_member_id="member-1",
    )
    member_retry = _observer_learning_candidate_key(
        run_id="run-2", venue="bitget", market_type="spot", symbol="BTC/USDT",
        universe_snapshot_id="snapshot-1", universe_member_id="member-1",
    )
    assert member_retry == member_first


def test_operator_learning_authorization_occurs_after_single_candidate_scope(
    tmp_path, monkeypatch,
):
    import asyncio
    from datetime import datetime, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell import b2_evidence_policy, commands, trading_decision
    from aletheia.learning.experiments import InfluenceDecision

    pending = [
        {
            "opportunity_id": "opp-learning-a", "signal_source": "market_observer",
            "symbol": "AAA/USDT", "direction": "long", "market_type": "perp",
            "scanner_family": "momentum", "trade_score": 56.0, "asset_id": "asset-a",
        },
        {
            "opportunity_id": "opp-learning-b", "signal_source": "market_observer",
            "symbol": "BBB/USDT", "direction": "short", "market_type": "perp",
            "scanner_family": "derivatives", "trade_score": 55.0, "asset_id": "asset-b",
        },
    ]
    now = datetime.now(timezone.utc).isoformat()
    features = {
        f"binance|perp|{row['symbol']}": {
            "asset_id": row["asset_id"], "feature_snapshot_id": f"feature-{index}",
            "feature_ts": now, "data_freshness_sec": 15, "venue": "binance",
            "symbol": row["symbol"], "market_type": "perp", "price": 100.0,
            "rsi_14": 55.0, "return_1h": 0.2, "return_4h": 0.5,
        }
        for index, row in enumerate(pending)
    }
    context = {
        "layer1": {
            "data_health": {"ohlcv_ready": True}, "feature_index": features,
            "features": list(features.values()), "opportunities": [],
        },
        "layer2": {
            "reflection": "candidate-linked reflection",
            "trading_memo": {"summary": "candidate-linked memo"},
            "reasoning_feedback": None,
            "calibration_feedback": None,
            "pending_signals": pending,
            "executable_candidates": [dict(row) for row in pending],
            "pending_queue": {
                "read_status": "complete", "unresolved_total": 2,
                "visible_count": 2, "overflow_count": 0,
            },
        },
        "layer3": {"lessons": [], "ps_lessons": [], "guard_candidates": []},
    }
    calls = []

    def control(**kwargs):
        calls.append(kwargs)
        return InfluenceDecision(
            value=0.0, applied=False, assignment="control", reason="baseline:authorized",
            hypothesis_id="hyp-operator", experiment_id="exp-operator",
            exposure_id="exposure-candidate-a",
        )

    async def assess(ctx):
        candidate = ctx["layer2"]["pending_signals"][0]
        return {
            "action": "skip", "symbol": candidate["symbol"], "direction": candidate["direction"],
            "market_type": "perp", "conviction": 25, "reasoning": "neutral",
            "bear_case": "uncertain", "skip_kind": "insufficient_evidence",
            "adverse_reason_codes": [], "adverse_evidence": [],
        }

    monkeypatch.setattr(
        "aletheia.agent_shell.trading_context.build_enriched_trading_context",
        lambda **_kwargs: context,
    )
    monkeypatch.setattr(
        "aletheia.learning.experiments.apply_boolean_treatment", control,
    )
    monkeypatch.setattr(trading_decision, "_assess_opportunity", assess)
    monkeypatch.setattr(trading_decision, "_reassess_direct_skip", lambda *_args, **_kwargs: assess(_args[0]))
    monkeypatch.setattr(
        b2_evidence_policy, "attach_b2_candidate_evidence", lambda *_args, **_kwargs: None,
    )

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    assert result["action"] == "evaluation_deferred"
    assert result["opportunity_id"] == "opp-learning-a"
    assert len(calls) == 1
    assert calls[0]["candidate_key"].startswith("operator_candidate:")
    assert calls[0]["opportunity_id"] == "opp-learning-a"
    assert calls[0]["outcome_candidate_key"] == "opportunity:opp-learning-a:4h"


@spec_claim(
    SpecClaim(
        "LE-C09", "learning_evolution", "§9.5 separated consumer influence", "L2c",
        "Observer prompt influence and downstream ranker case/memo score influence use separate "
        "declared paths; ranker deltas pass through prospective authority and expose their ids.",
        status="implemented",
    )
)
def test_ranker_learning_deltas_are_refrozen_behind_separate_authority(monkeypatch):
    import aletheia.agents.paper_trader_ranker as ranker
    from aletheia.learning.experiments import InfluenceDecision
    from aletheia.regime.detector import MarketRegime

    monkeypatch.setattr(ranker, "get_current_regime", lambda: MarketRegime(state="bull", confidence=0.8))
    monkeypatch.setattr(ranker, "_ranker_entry_mark", _fresh_ranker_mark)
    monkeypatch.setattr(ranker, "_lesson_penalty", lambda *_: -12.0)
    monkeypatch.setattr(ranker, "_memo_scoring", lambda *_: 8.0)

    calls = []

    def refrozen(**kwargs):
        calls.append(kwargs)
        return InfluenceDecision(
            value=kwargs["baseline"], applied=False, assignment="control",
            reason="baseline_refreeze", hypothesis_id="h", experiment_id="e",
            exposure_id=f"x-{kwargs['path_id']}",
        )

    monkeypatch.setattr("aletheia.learning.experiments.apply_numeric_treatment", refrozen)
    opp = {
        "id": "opp-ranker", "symbol": "BTC/USDT", "confidence_score": 50,
        "scanner_family": "momentum", "market": "spot",
        "universe_snapshot_id": "snapshot-1", "universe_member_id": "member-1",
        "metadata": json.dumps({"learning_candidate_key": "unrelated-universe-key"}),
    }
    trace = []
    refrozen_score = ranker._compute_trade_score(
        opp, None, {"status": "watch"}, "medium", direction="long",
        learning_trace=trace,
    )

    monkeypatch.setattr(ranker, "_lesson_penalty", lambda *_: 0.0)
    monkeypatch.setattr(ranker, "_memo_scoring", lambda *_: 0.0)
    static_score = ranker._compute_trade_score(
        opp, None, {"status": "watch"}, "medium", direction="long",
    )
    assert refrozen_score == static_score
    assert {c["path_id"] for c in calls} == {"ranker_case_score", "ranker_memo_score"}
    assert {
        (c["path_id"], c["outcome_candidate_key"]) for c in calls
    } == {
        ("ranker_case_score", "ranker_member:snapshot-1:member-1:spot:4h"),
        ("ranker_memo_score", "unrelated-universe-key:4h"),
    }
    assert {row["path_id"] for row in trace} == {"ranker_case_score", "ranker_memo_score"}

    from aletheia.learning.registry import get_learning_path
    assert len({
        get_learning_path("observer_advisory_bundle").overlap_group,
        get_learning_path("ranker_case_score").overlap_group,
        get_learning_path("operator_learning_bundle").overlap_group,
    }) == 3


def test_ranker_case_shadow_uses_exact_candidate_identity_for_assignment(monkeypatch):
    import aletheia.agents.paper_trader_ranker as ranker
    from aletheia.learning.experiments import InfluenceDecision

    calls = []
    monkeypatch.setattr(ranker, "_ranker_entry_mark", _fresh_ranker_mark)

    def capture(**kwargs):
        calls.append(kwargs)
        return InfluenceDecision(
            value=kwargs["baseline"],
            applied=False,
            assignment="control",
            reason="baseline:control_assignment",
            hypothesis_id="hypothesis",
            experiment_id="experiment",
            exposure_id=f"exposure-{len(calls)}",
        )

    monkeypatch.setattr(
        "aletheia.learning.experiments.apply_numeric_treatment",
        capture,
    )
    trace = []
    for opportunity_id in ("opportunity-a", "opportunity-b"):
        value = ranker._authorized_ranker_delta(
            path_id="ranker_case_score",
            proposed_delta=0.0,
            opp={
                "id": opportunity_id,
                "symbol": "BTC/USDT",
                "market": "spot",
                "universe_snapshot_id": "snapshot-1",
                "universe_member_id": "member-1",
                "metadata": json.dumps({"learning_candidate_key": "unrelated-universe-key"}),
            },
            direction="long",
            scope={"symbol": "BTC/USDT", "direction": "long"},
            source_cohort="trade_case_study:v1",
            learning_trace=trace,
        )
        assert value == 0.0

    assert len(calls) == 2
    assert {(call["candidate_key"], call["assignment_key"]) for call in calls} == {
        (
            "ranker_member:snapshot-1:member-1:spot",
            "ranker_member:snapshot-1:member-1:spot",
        ),
    }
    assert {call["outcome_candidate_key"] for call in calls} == {
        "ranker_member:snapshot-1:member-1:spot:4h",
    }
    assert {row["assignment_unit_key"] for row in trace} == {
        "ranker_member:snapshot-1:member-1:spot",
    }


def test_ranker_case_without_exact_entry_mark_fails_closed(monkeypatch):
    import aletheia.agents.paper_trader_ranker as ranker

    calls = []
    monkeypatch.setattr(ranker, "_ranker_entry_mark", lambda _opp: None)
    monkeypatch.setattr(
        "aletheia.learning.experiments.apply_numeric_treatment",
        lambda **kwargs: calls.append(kwargs),
    )
    trace = []

    value = ranker._authorized_ranker_delta(
        path_id="ranker_case_score",
        proposed_delta=-7.0,
        opp={"id": "opportunity-no-mark", "symbol": "BTC/USDT"},
        direction="long",
        scope={"symbol": "BTC/USDT", "direction": "long"},
        source_cohort="trade_case_study:v1",
        learning_trace=trace,
    )

    assert value == 0.0
    assert calls == []
    assert trace[0]["reason"] == "ranker_entry_mark_unavailable"


def test_ranker_case_shadow_forces_nonzero_adapter_result_to_baseline(monkeypatch):
    import aletheia.agents.paper_trader_ranker as ranker
    from aletheia.learning.experiments import InfluenceDecision

    monkeypatch.setattr(ranker, "_ranker_entry_mark", _fresh_ranker_mark)
    monkeypatch.setattr(
        "aletheia.learning.experiments.apply_numeric_treatment",
        lambda **_: InfluenceDecision(
            7.0,
            True,
            "treatment",
            "unexpected_authorized_delta",
            "hypothesis",
            "experiment",
            "exposure",
        ),
    )
    trace = []
    value = ranker._authorized_ranker_delta(
        path_id="ranker_case_score",
        proposed_delta=-7.0,
        opp={
            "id": "opportunity-shadow",
            "symbol": "BTC/USDT",
            "metadata": json.dumps({"learning_candidate_key": "member-2"}),
        },
        direction="long",
        scope={"symbol": "BTC/USDT", "direction": "long"},
        source_cohort="trade_case_study:v1",
        learning_trace=trace,
    )

    assert value == 0.0
    assert trace[0]["actual_delta"] == 0.0
    assert trace[0]["reason"] == "shadow_zero_delta_enforced"


def test_ranker_case_valid_candidate_persists_one_shadow_exposure(monkeypatch):
    import aletheia.agents.paper_trader_ranker as ranker
    from aletheia.learning import experiments
    from aletheia.storage.sqlite import get_db
    monkeypatch.setattr(experiments, "_learning_gate_reason", lambda: None)
    monkeypatch.setattr(ranker, "_ranker_entry_mark", _fresh_ranker_mark)

    opp = {
        "id": "opportunity-carrier",
        "symbol": "BTC/USDT",
        "market": "spot",
        "universe_snapshot_id": "snapshot-1",
        "universe_member_id": "member-1",
        "metadata": json.dumps({"learning_candidate_key": "unrelated-universe-key"}),
    }
    kwargs = dict(
        path_id="ranker_case_score",
        proposed_delta=0.0,
        opp=opp,
        direction="long",
        scope={"symbol": "BTC/USDT", "direction": "long"},
        source_cohort="trade_case_study:v1",
        learning_trace=[],
        entry_price_usd=100.0,
    )
    first = ranker._authorized_ranker_delta(**kwargs)
    second = ranker._authorized_ranker_delta(**kwargs)

    assert first == second == 0.0
    assert second is not None
    ranker_key = "ranker_member:snapshot-1:member-1:spot"
    row = get_db().execute(
        "SELECT * FROM learning_exposures WHERE path_id=? AND candidate_key=?",
        ("ranker_case_score", ranker_key),
    ).fetchone()
    assert row["assignment_unit_key"] == ranker_key
    assert row["outcome_candidate_key"] == f"{ranker_key}:4h"
    assert row["experiment_stage"] == "shadow"
    assert json.loads(row["before_json"])["value"] == 0.0
    assert json.loads(row["after_json"])["value"] == 0.0
    assert json.loads(row["manifest_json"])["authority_applied"] is False
    assert get_db().execute(
        "SELECT COUNT(*) FROM learning_exposures WHERE path_id=? AND candidate_key=?",
        ("ranker_case_score", ranker_key),
    ).fetchone()[0] == 1

def test_ranker_case_pairs_outcome_with_exposure(monkeypatch):
    import aletheia.agents.paper_trader_ranker as ranker
    from aletheia.learning.experiments import InfluenceDecision

    events = []
    monkeypatch.setattr(ranker, "_ranker_entry_mark", _fresh_ranker_mark)

    def record_outcome(**kwargs):
        events.append(("outcome", kwargs["entry_ts"]))
        return 1

    def record_exposure(**kwargs):
        events.append(("exposure", kwargs["manifest"]["prospective_t0"]))
        return InfluenceDecision(
            0.0, False, "control", "baseline", "hypothesis", "experiment", "exposure"
        )

    monkeypatch.setattr(ranker, "_register_ranker_case_outcome", record_outcome)
    monkeypatch.setattr(
        "aletheia.learning.experiments.apply_numeric_treatment",
        record_exposure,
    )
    ranker._authorized_ranker_delta(
        path_id="ranker_case_score",
        proposed_delta=0.0,
        opp={
            "id": "opportunity-order",
            "symbol": "BTC/USDT",
            "market": "spot",
            "universe_snapshot_id": "snapshot-1",
            "universe_member_id": "member-order",
        },
        direction="long",
        scope={"symbol": "BTC/USDT", "direction": "long"},
        source_cohort="trade_case_study:v1",
        learning_trace=[],
        entry_price_usd=100.0,
    )

    assert [event[0] for event in events] == ["exposure", "outcome"]
    assert events[0][1] == events[1][1]


def test_ranker_case_rolls_back_exposure_when_outcome_seed_fails(monkeypatch):
    import aletheia.agents.paper_trader_ranker as ranker
    from aletheia.learning import experiments
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(experiments, "_learning_gate_reason", lambda: None)
    monkeypatch.setattr(ranker, "_ranker_entry_mark", _fresh_ranker_mark)

    def fail_seed(**_kwargs):
        raise RuntimeError("seed failure")

    monkeypatch.setattr(ranker, "_register_ranker_case_outcome", fail_seed)
    trace = []

    value = ranker._authorized_ranker_delta(
        path_id="ranker_case_score",
        proposed_delta=0.0,
        opp={
            "id": "opportunity-atomic",
            "symbol": "BTC/USDT",
            "market": "spot",
            "universe_snapshot_id": "snapshot-atomic",
            "universe_member_id": "member-atomic",
        },
        direction="long",
        scope={"symbol": "BTC/USDT", "direction": "long"},
        source_cohort="trade_case_study:v1",
        learning_trace=trace,
    )

    assert value == 0.0
    assert trace[0]["reason"] == "ranker_evidence_pair_unavailable"
    assert get_db().execute(
        "SELECT COUNT(*) FROM learning_exposures "
        "WHERE path_id='ranker_case_score' AND candidate_key=?",
        ("ranker_member:snapshot-atomic:member-atomic:spot",),
    ).fetchone()[0] == 0


def _insert_forward_outcome(db, *, oid: str, symbol: str, direction: str, scanner: str,
                            complete: bool = True) -> None:
    from datetime import datetime, timedelta, timezone
    _ts = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    db.execute(
        "INSERT INTO universe_forward_outcomes "
        "(id,outcome_candidate_key,member_id,snapshot_id,symbol,scanner_family,direction,horizon,"
        "entry_price_usd,entry_ts,due_at,settled_at,net_return_pct,min_tradable_edge_pct,"
        "cost_model_version,status) VALUES (?,?,?,?,?,?,?,?,100,?,?,?,?,0.05,?,?)",
        (
            oid, f"candidate-{oid}" if complete else None, "member", "snapshot", symbol,
            scanner, direction, "24h", _ts, _ts, _ts, 1.0,
            "v1" if complete else None, "settled",
        ),
    )


@spec_claim(
    SpecClaim(
        "LE-B03", "learning_evolution", "§5.1 clean reviewer evidence", "L2b",
        "Incomplete, identity-less, or non-cost-aware candidate outcomes cannot count toward lesson review.",
        status="implemented",
    ),
    SpecClaim(
        "LE-B04", "learning_evolution", "§5.2 exact-scope reviewer gate", "L2b",
        "A lesson is deferred when its exact populated scope misses the sample floor even if global history is large.",
        status="implemented",
    ),
)
async def test_lesson_reviewer_requires_clean_exact_scope(monkeypatch):
    from aletheia.agents import lesson_reviewer
    from aletheia.learning.lessons import LessonStore
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(25):
        _insert_forward_outcome(
            db, oid=f"global-{i}", symbol="ETH/USDT", direction="short", scanner="reversion",
        )
    for i in range(5):
        _insert_forward_outcome(
            db, oid=f"exact-{i}", symbol="BTC/USDT", direction="long", scanner="momentum",
        )
    for i in range(30):
        _insert_forward_outcome(
            db, oid=f"dirty-{i}", symbol="BTC/USDT", direction="long", scanner="momentum",
            complete=False,
        )
    db.commit()

    lesson = LessonStore().create(
        "BTC/USDT momentum longs should be preferred",
        source_type="trade_case_study", source_id="case-exact", category="entry_timing",
        severity="warning",
        evidence={"scanner_family": "momentum", "direction": "long", "symbol": "BTC/USDT"},
        recommendation={"action": "prefer", "symbol": "BTC/USDT", "direction": "long",
                        "scanner": "momentum"},
    )
    judge = AsyncMock(return_value=("accept", "broad history looks positive"))
    monkeypatch.setattr(lesson_reviewer, "_judge", judge)

    evidence = lesson_reviewer._fetch_evidence(lesson)
    assert evidence["aggregate"]["total"] == 30
    assert evidence["exact_scope"]["total"] == 5
    from aletheia.learning.outcomes import assess_realized_position
    assert assess_realized_position({
        "id": "synthetic", "closed_at": "2026-07-11T00:00:00+00:00",
        "close_reason": "reconciled_gone", "realized_pnl": 100,
        "entry_price": 100, "quantity": 1,
    }).eligible is False
    await lesson_reviewer.run_lesson_reviewer(run_id="test")
    assert LessonStore().get(lesson.lesson_id).status == "proposed"
    judge.assert_not_awaited()


@spec_claim(
    SpecClaim(
        "LE-B10", "learning_evolution", "§8.2 sizing exposure", "L2b",
        "Sizing records static baseline, learned proposal, actual notional, assignment, and exposure ids; "
        "a refrozen decision uses the static baseline.",
        status="implemented",
    )
)
def test_sizing_records_baseline_learned_and_actual_notional(monkeypatch):
    from aletheia.learning.experiments import InfluenceDecision
    from aletheia.trading import sizing_policy

    captured = {}

    def refrozen(**kwargs):
        captured.update(kwargs)
        return InfluenceDecision(
            value=kwargs["baseline"], applied=False, assignment="control",
            reason="baseline_refreeze", hypothesis_id="h-size", experiment_id="e-size",
            exposure_id="x-size",
        )

    monkeypatch.setattr("aletheia.learning.experiments.apply_numeric_treatment", refrozen)
    trace = sizing_policy.resolve_learned_notional(
        baseline_notional=25.0, learned_notional=31.0, symbol="BTC/USDT",
        direction="long", regime_bucket="bull", candidate_key="opportunity:opp-size",
        outcome_candidate_key="member-size:4h", opportunity_id="opp-size",
    )
    assert trace == {
        "baseline_notional": 25.0,
        "learned_notional": 31.0,
        "actual_notional": 25.0,
        "authority_applied": False,
        "assignment": "control",
        "reason": "baseline_refreeze",
        "hypothesis_id": "h-size",
        "experiment_id": "e-size",
        "exposure_id": "x-size",
    }
    assert captured["path_id"] == "persona_sizing"
    assert captured["manifest"]["baseline_notional"] == 25.0
    assert captured["manifest"]["learned_notional"] == 31.0

    monkeypatch.setattr(
        "aletheia.learning.experiments.apply_numeric_treatment",
        lambda **_: (_ for _ in ()).throw(RuntimeError("ledger unavailable")),
    )
    unavailable = sizing_policy.resolve_learned_notional(
        baseline_notional=25.0, learned_notional=31.0, symbol="BTC/USDT",
        direction="long", regime_bucket="bull", candidate_key="opportunity:opp-size-2",
        outcome_candidate_key="member-size-2:4h", opportunity_id="opp-size-2",
    )
    assert unavailable["actual_notional"] == 25.0
    assert unavailable["reason"] == "adapter_error"

    monkeypatch.setattr("aletheia.learning.experiments.apply_numeric_treatment", refrozen)
    from aletheia.config.schema import AletheiaConfig
    from aletheia.trading import persona, sizing
    import aletheia.config.loader as loader
    cfg = AletheiaConfig()
    cfg.trading.vol_sizing_enabled = False
    monkeypatch.setattr(loader, "load_config", lambda: cfg)

    def params(*_, apply_learning=True, **__):
        return {
            "notional_usdt": 31.0 if apply_learning else 25.0,
            "kelly_fraction": 0.25,
            "_regime": "bull",
        }

    monkeypatch.setattr(persona, "effective_sizing_params", params)
    integrated_trace = {}
    actual = sizing.vol_target_size(
        "BTC/USDT", conviction=70, direction="long", paper_trade_count=50,
        candidate_key="opportunity:opp-size-3",
        outcome_candidate_key="member-size-3:4h", opportunity_id="opp-size-3",
        sizing_trace=integrated_trace,
    )
    assert actual == 25.0
    assert integrated_trace["baseline_notional"] == 25.0
    assert integrated_trace["learned_notional"] == 31.0
    assert integrated_trace["actual_notional"] == 25.0


@spec_claim(
    SpecClaim(
        "LE-C05", "learning_evolution", "§2.3 immutable safety boundary", "L2c",
        "Final Learning consumer adapters have no imports from exchange, execution, live routes, or risk mutation modules.",
        status="implemented",
    ),
    SpecClaim(
        "LE-S02", "learning_evolution", "§2.3 package authority boundary", "L2a",
        "The Learning package imports no exchange client, execution gateway, or live-unlock route.",
        status="implemented",
    ),
)
def test_final_learning_consumers_do_not_gain_execution_or_risk_authority():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    paths = [
        root / "aletheia/learning/experiments.py",
        root / "aletheia/trading/sizing_policy.py",
        root / "aletheia/agents/lesson_reviewer.py",
    ]
    forbidden = (
        "aletheia.execution", "aletheia.exchanges", "aletheia.server.routes.live",
        "update_risk_config", "unlock_live", "execute_order",
    )
    for path in paths:
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path.name} gained forbidden authority via {token}"

    for path in (root / "aletheia/learning").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for token in ("aletheia.execution", "aletheia.exchanges", "aletheia.server.routes.live"):
            assert token not in source, f"{path.name} crosses the Learning authority boundary"

    import inspect
    from aletheia.agents import paper_trader_ranker
    ranker_learning_source = inspect.getsource(paper_trader_ranker._compute_trade_score)
    ranker_learning_source += inspect.getsource(paper_trader_ranker._authorized_ranker_delta)
    for token in forbidden:
        assert token not in ranker_learning_source
