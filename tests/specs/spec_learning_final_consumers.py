"""Final consumer authority checks for the measured Learning loop."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests.specs.spec_meta import SpecClaim, spec_claim


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
