"""Final consumer authority checks for the measured Learning loop."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests.specs.spec_meta import SpecClaim, spec_claim


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
                expires_at="2026-08-01T00:00:00+00:00",
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
        "Observer and Operator Learning bundles stay empty unless the exact paper path has "
        "prospective treatment authority; control retains the static prompt baseline and the "
        "manifest records the stable assignment and exposure identity.",
        status="implemented",
    )
)
def test_prompt_bundles_use_exact_paper_authority(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.learning.experiments import InfluenceDecision
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
    assert default_off["learning_manifest"]["authority"]["exposure_id"]
    from aletheia.analytics.filter_evidence import record_candidate_filter_trace
    from aletheia.learning.ledger import get_record, linked_settled_exposure_counts
    from aletheia.storage.sqlite import get_db
    assert record_candidate_filter_trace({
        "opportunity_id": "opp-authority",
        "symbol": "BTC/USDT",
        "market_type": "spot",
        "direction": "long",
        "entry_price_usd": 60_000.0,
        "filter_trace": [{
            "filter_name": "execution_eligibility",
            "stage": "paper_ranker",
            "decision": "passed",
            "reason_code": "eligible",
        }],
    }, raise_on_error=True) == 1
    operator_exposure = get_record(
        "learning_exposures",
        default_off["learning_manifest"]["authority"]["exposure_id"],
    )
    assert operator_exposure["outcome_candidate_key"] == "opportunity:opp-authority:4h"
    operator_epoch = get_db().execute(
        "SELECT effective_values_json FROM learning_policy_epochs WHERE epoch_id=?",
        (operator_exposure["concurrent_policy_epoch"],),
    ).fetchone()
    assert operator_epoch is not None
    operator_policy = json.loads(operator_epoch["effective_values_json"])
    # Superset check: skip_policy generations are backward-compatible supersets
    # (b1.4 → b1.5 Phase C defer semantics → b1.6 #321 material-dedup flags). Any
    # of these is a valid predecessor snapshot for a learning bundle; this test
    # locks authority attribution, not a specific policy revision.
    assert operator_policy["operator_decision_context"]["version"] in {
        "b1.4", "b1.5", "b1.6",
    }
    assert operator_policy["operator_decision_context"][
        "candidate_feature_refresh_wakes_operator"
    ] is True
    operator_outcome = get_db().execute(
        "SELECT id FROM filter_candidate_outcomes "
        "WHERE outcome_candidate_key='opportunity:opp-authority' AND horizon='4h'",
    ).fetchone()
    assert operator_outcome is not None
    get_db().execute(
        "UPDATE filter_candidate_outcomes SET status='settled', net_return_pct=0.2, "
        "cost_model_version='test-v1', settled_at=datetime('now') WHERE id=?",
        (operator_outcome["id"],),
    )
    get_db().commit()
    operator_counts = linked_settled_exposure_counts(
        operator_exposure["experiment_id"],
        policy_epoch=operator_exposure["concurrent_policy_epoch"],
    )
    assert operator_counts[operator_exposure["assignment"]] == 1

    observer_default = prompt_bundles.authorize_observer_learning_bundle(
        "learned observer memo", "learned symbol guard",
        candidate_key="observer_member:snapshot-1:member-1:spot",
    )
    assert observer_default[0] == ""
    assert observer_default[1]["components"] == {}
    assert observer_default[1]["authority"]["applied"] is False
    assert observer_default[1]["authority"]["exposure_id"]

    calls = []

    def control(**kwargs):
        calls.append(kwargs)
        return InfluenceDecision(
            value=0.0, applied=False, assignment="control", reason="baseline:authorized",
            hypothesis_id="hyp-operator", experiment_id="exp-operator",
            exposure_id="exposure-control",
        )

    monkeypatch.setattr("aletheia.learning.experiments.apply_boolean_treatment", control)
    controlled = prompt_bundles.authorize_operator_learning_context(_operator_prompt_context())
    assert controlled["layer2"]["reflection"] is None
    assert controlled["learning_manifest"]["authority"] == {
        "path_id": "operator_learning_bundle",
        "candidate_key": calls[0]["candidate_key"],
        "assignment": "control",
        "applied": False,
        "reason": "baseline:authorized",
        "hypothesis_id": "hyp-operator",
        "experiment_id": "exp-operator",
        "exposure_id": "exposure-control",
    }
    assert calls[0]["candidate_key"].startswith("operator_candidate:")
    assert calls[0]["outcome_candidate_key"] == "opportunity:opp-authority:4h"
    observer_control = prompt_bundles.authorize_observer_learning_bundle(
        "learned observer memo", "learned symbol guard",
        candidate_key="observer_member:snapshot-1:member-1:spot",
    )
    assert observer_control[0] == ""
    assert observer_control[1]["components"] == {}
    assert observer_control[1]["authority"]["assignment"] == "control"

    def treatment(**kwargs):
        return InfluenceDecision(
            value=1.0, applied=True, assignment="treatment", reason="authorized",
            hypothesis_id="hyp-operator", experiment_id="exp-operator",
            exposure_id="exposure-treatment",
        )

    monkeypatch.setattr("aletheia.learning.experiments.apply_boolean_treatment", treatment)
    treated = prompt_bundles.authorize_operator_learning_context(_operator_prompt_context())
    assert treated["layer2"]["reflection"] == "learned reflection"
    assert treated["learning_manifest"]["authority"]["assignment"] == "treatment"
    assert treated["learning_manifest"]["authority"]["exposure_id"] == "exposure-treatment"
    from aletheia.agent_shell.reflection import _build_pi_decision
    final = _build_pi_decision({
        "action": "skip", "symbol": "BTC/USDT", "direction": "long",
        "market_type": "spot", "opportunity_id": "opp-authority",
        "learning_manifest": treated["learning_manifest"],
    })
    assert json.loads(final.learning_manifest_json)["authority"]["exposure_id"] == "exposure-treatment"

    observer_treatment = prompt_bundles.authorize_observer_learning_bundle(
        "learned observer memo", "learned symbol guard",
        candidate_key="observer_member:snapshot-1:member-1:spot",
    )
    assert observer_treatment[0] == "learned observer memo\nlearned symbol guard"
    assert observer_treatment[1]["authority"]["path_id"] == "observer_advisory_bundle"

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
        "scanner_family": "momentum",
        "metadata": json.dumps({"learning_candidate_key": "member-1"}),
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
    assert all(c["outcome_candidate_key"] == "member-1:4h" for c in calls)
    assert {row["path_id"] for row in trace} == {"ranker_case_score", "ranker_memo_score"}

    from aletheia.learning.registry import get_learning_path
    assert len({
        get_learning_path("observer_advisory_bundle").overlap_group,
        get_learning_path("ranker_case_score").overlap_group,
        get_learning_path("operator_learning_bundle").overlap_group,
    }) == 3


def _insert_forward_outcome(db, *, oid: str, symbol: str, direction: str, scanner: str,
                            complete: bool = True) -> None:
    db.execute(
        "INSERT INTO universe_forward_outcomes "
        "(id,outcome_candidate_key,member_id,snapshot_id,symbol,scanner_family,direction,horizon,"
        "entry_price_usd,entry_ts,due_at,settled_at,net_return_pct,min_tradable_edge_pct,"
        "cost_model_version,status) VALUES (?,?,?,?,?,?,?,?,100,?,?,?,?,0.05,?,?)",
        (
            oid, f"candidate-{oid}" if complete else None, "member", "snapshot", symbol,
            scanner, direction, "24h", "2026-07-12T08:00:00+00:00",
            "2026-07-12T09:00:00+00:00", "2026-07-12T09:00:00+00:00", 1.0,
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
