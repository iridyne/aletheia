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
    from aletheia.learning import lessons

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





















def _insert_forward_outcome(db, *, oid: str, symbol: str, direction: str, scanner: str,
                            complete: bool = True) -> None:
    from datetime import datetime, timedelta, timezone
    _ts = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    db.execute(
        "INSERT INTO universe_forward_outcomes "
        "(id,outcome_candidate_key,member_id,snapshot_id,venue,symbol,scanner_family,direction,horizon,"
        "entry_price_usd,exit_price_usd,entry_ts,due_at,settled_at,directional_return_pct,"
        "settlement_price_source,settlement_lag_sec,"
        "estimated_fee_pct,estimated_spread_slippage_pct,estimated_funding_pct,"
        "net_return_pct,min_tradable_edge_pct,cost_model_version,status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            oid, f"candidate-{oid}" if complete else None, "member", "snapshot",
            "bitget" if complete else None, symbol,
            scanner, direction, "24h", 100.0, 101.2 if direction == "long" else 98.8,
            _ts, _ts, _ts,
            1.2, "horizon_ohlcv" if complete else None, 0 if complete else None,
            0.1, 0.1, 0.0, 1.0, 0.05,
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
    from extensions.research import lesson_reviewer
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
    _insert_forward_outcome(
        db, oid="malformed-edge", symbol="BTC/USDT", direction="long", scanner="momentum",
    )
    db.execute(
        "UPDATE universe_forward_outcomes SET min_tradable_edge_pct='malformed' "
        "WHERE id='malformed-edge'"
    )
    _insert_forward_outcome(
        db, oid="blob-edge", symbol="BTC/USDT", direction="long", scanner="momentum",
    )
    db.execute(
        "UPDATE universe_forward_outcomes SET min_tradable_edge_pct=? WHERE id='blob-edge'",
        (b"0.05",),
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
    from aletheia.analytics.outcome_semantics import assess_realized_position
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
