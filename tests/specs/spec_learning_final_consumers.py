"""Final consumer authority checks for the measured Learning loop."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

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
def test_live_operator_diagnostic_learning_is_curated_and_advisory(monkeypatch, installed_learning_consumers):
    from aletheia.agent_shell import context
    from extensions.learning import lessons

    monkeypatch.setattr(
        lessons,
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

    import inspect

    from aletheia.server.routes.operator import operator_llm_session
    source = inspect.getsource(operator_llm_session)
    assert '["system", "market", "agents", "trading", "learning"]' in source
    assert '"learning_manifest": learning_manifest' in source





















def _source_trade(position_id: str, realized_pnl: float, *, eligible: bool = True) -> str:
    """A closed position and the trade_case_study that reviews it; returns the case id."""
    from datetime import timedelta

    from aletheia.storage import repositories as repo
    from extensions.knowledge.manager import create_trade_case_study
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    now = datetime.now(timezone.utc)
    if eligible:
        _insert_closed_position_via_producer(
            position_id=position_id, symbol="BTC/USDT", market_type="usdt_futures",
            direction="long", quantity=1.0, entry_price=100.0, realized_pnl=realized_pnl,
            execution_mode="bitget_demo", closed_at=now.isoformat(),
        )
    else:  # an administrative close carries a number but no clean outcome
        repo._insert("positions", {
            "id": position_id, "symbol": "BTC/USDT", "market_type": "usdt_futures",
            "direction": "long", "quantity": 1.0, "entry_price": 100.0,
            "execution_mode": "bitget_demo", "status": "closed", "realized_pnl": realized_pnl,
            "opened_at": (now - timedelta(hours=2)).isoformat(), "closed_at": now.isoformat(),
            "close_reason": "reconciled_gone", "close_category": "administrative",
            "exit_price_source": "unavailable", "exit_fill_verified": False,
            "outcome_eligible": False, "outcome_exclusion_reason": "administrative_close",
            "outcome_semantics_version": "oi-v1",
        })
    case = create_trade_case_study(
        f"Case: BTC/USDT long {position_id}",
        f"# Case {position_id}\n\nA closed BTC/USDT long reviewed for the lesson reviewer fixture. " * 2,
        metadata={"position_id": position_id, "symbol": "BTC/USDT", "direction": "long"},
    )
    assert case is not None
    return case.id


@spec_claim(
    SpecClaim(
        "LE-B03", "learning_evolution", "§5.1 clean reviewer evidence", "L2b",
        "Only a lesson's own source trades with a clean realized net outcome count toward lesson review.",
        status="implemented",
    ),
    SpecClaim(
        "LE-B04", "learning_evolution", "§5.2 exact-scope reviewer gate", "L2b",
        "A lesson is deferred when its own clean source trades miss the sample floor, "
        "even if other clean closes exist.",
        status="implemented",
    ),
)
async def test_lesson_reviewer_judges_a_lesson_from_its_source_trades(monkeypatch):
    from extensions.research import lesson_reviewer
    from extensions.learning.lessons import LessonStore

    store = LessonStore()
    judged = {}

    async def judge(lesson, evidence, run_id):
        judged[lesson.lesson_id] = lesson_reviewer._evidence_block(evidence)
        return "accept", "source trades back it", False

    monkeypatch.setattr(lesson_reviewer, "_judge", judge)
    supported = store.create(
        "BTC/USDT longs closed at a profit", source_type="trade_case_study", source_id="case",
        category="entry_timing", evidence={"supporting_source_ids": [
            _source_trade("win-1", 2.0), _source_trade("win-2", 1.0), _source_trade("loss-1", -1.0),
            _source_trade("admin-1", 50.0, eligible=False),
        ]},
    )
    thin = store.create(
        "Two trades are not a lesson yet", source_type="trade_case_study", source_id="case",
        category="entry_timing", evidence={"supporting_source_ids": [
            _source_trade("thin-1", 1.0), _source_trade("thin-2", 1.0),
        ]},
    )

    evidence = lesson_reviewer._fetch_evidence(supported)
    assert (evidence["total"], evidence["wins"]) == (3, 2)
    assert evidence["excluded"] == {"administrative_close": 1}

    await lesson_reviewer.run_lesson_reviewer(run_id="test")
    reviewed, deferred = store.get(supported.lesson_id), store.get(thin.lesson_id)
    assert reviewed is not None and reviewed.status == "applied"
    assert "total: 3, win_rate: 66.7%" in judged[supported.lesson_id]
    assert deferred is not None and deferred.status == "proposed"
    assert thin.lesson_id not in judged


@spec_claim(
    SpecClaim(
        "LE-B10", "learning_evolution", "§8.2 sizing exposure", "L2b",
        "Sizing records static baseline, learned proposal, actual notional, assignment, and exposure ids; "
        "a refrozen decision uses the static baseline.",
        status="implemented",
    )
)
def test_sizing_records_baseline_learned_and_actual_notional(monkeypatch):
    from extensions.learning.experiments import InfluenceDecision
    from aletheia.trading import sizing_policy

    captured = {}

    def refrozen(**kwargs):
        captured.update(kwargs)
        return InfluenceDecision(
            value=kwargs["baseline"], applied=False, assignment="control",
            reason="baseline_refreeze", hypothesis_id="h-size", experiment_id="e-size",
            exposure_id="x-size",
        )

    monkeypatch.setattr("extensions.learning.experiments.apply_numeric_treatment", refrozen)
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
        "extensions.learning.experiments.apply_numeric_treatment",
        lambda **_: (_ for _ in ()).throw(RuntimeError("ledger unavailable")),
    )
    unavailable = sizing_policy.resolve_learned_notional(
        baseline_notional=25.0, learned_notional=31.0, symbol="BTC/USDT",
        direction="long", regime_bucket="bull", candidate_key="opportunity:opp-size-2",
        outcome_candidate_key="member-size-2:4h", opportunity_id="opp-size-2",
    )
    assert unavailable["actual_notional"] == 25.0
    assert unavailable["reason"] == "adapter_error"

    monkeypatch.setattr("extensions.learning.experiments.apply_numeric_treatment", refrozen)
    import aletheia.config.loader as loader
    from aletheia.config.schema import AletheiaConfig
    from aletheia.trading import persona, sizing
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
