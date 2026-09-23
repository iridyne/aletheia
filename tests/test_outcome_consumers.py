"""Behavior consumers must not learn or act on administrative position closes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _external_account():
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)


def _unavailable_cohort():
    from aletheia.analytics.outcome_integrity import CompleteOutcomeCohort

    return CompleteOutcomeCohort(
        rows=(),
        assessments=(),
        source_raw_event_count=None,
        loaded_raw_event_count=0,
        cohort_complete=False,
        cohort_state="unavailable",
        ordered_source_digest=None,
        identity={
            "execution_mode": "bitget_demo",
            "runtime_session_id": None,
            "closed_after": None,
            "closed_before": None,
        },
    )


def _insert_closed(
    position_id: str,
    pnl: float | None,
    *,
    eligible: bool,
    strategy_id: str = "strategy-1",
    proposal_id: str | None = None,
) -> None:
    from aletheia.storage import repositories as repo
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    now = datetime.now(timezone.utc)
    if eligible:
        _insert_closed_position_via_producer(
            position_id=position_id, symbol="BTC/USDT", market_type="usdt_futures",
            direction="long", quantity=1.0, entry_price=100.0,
            execution_mode="bitget_demo", strategy_id=strategy_id,
            proposal_id=proposal_id, realized_pnl=pnl,
            closed_at=now.isoformat(),
        )
        return

    # Historical administrative records may carry numbers; consumers must exclude them.
    repo._insert("positions", {
        "id": position_id, "symbol": "BTC/USDT", "market_type": "usdt_futures",
        "direction": "long", "quantity": 1.0, "entry_price": 100.0,
        "execution_mode": "bitget_demo", "strategy_id": strategy_id,
        "proposal_id": proposal_id, "status": "closed", "realized_pnl": pnl,
        "opened_at": (now - timedelta(hours=2)).isoformat(), "closed_at": now.isoformat(),
        "close_reason": "reconciled_gone", "close_category": "administrative",
        "exit_price_source": "unavailable", "exit_fill_verified": False,
        "outcome_eligible": False, "outcome_exclusion_reason": "administrative_close",
        "outcome_semantics_version": "oi-v1",
    })


def test_trade_and_strategy_stats_ignore_administrative_rows():
    from aletheia.analytics.trade_stats import (
        build_strategy_kpis,
        compute_rolling_trade_stats,
    )

    _insert_closed("verified-win", 10.0, eligible=True)
    _insert_closed("verified-loss", -5.0, eligible=True)
    _insert_closed("administrative", -10_000.0, eligible=False)

    rolling = compute_rolling_trade_stats(days=7)
    assert rolling["total_trades"] == 2
    assert rolling["wins"] == 1
    assert rolling["losses"] == 1
    assert rolling["total_pnl"] == 5.0

    strategy = build_strategy_kpis()["strategy-1"]
    assert {key: strategy[key] for key in ("trade_count", "win_rate", "total_pnl")} == {
        "trade_count": 2, "win_rate": 50.0, "total_pnl": 5.0,
    }
    assert strategy["outcome_semantics_version"] == "oi-v2"


def test_trade_stats_resolves_eligible_outcomes_through_source_module(monkeypatch):
    """Regression: trade_stats must call eligible_strategy_outcomes through the
    outcome_integrity module, so a test patching the source is both honored and
    cleanly undone.

    A bare ``from outcome_integrity import eligible_strategy_outcomes`` binds a
    private copy in trade_stats. When another test (test_temporal) patched the
    source module and, in the same process, triggered the first lazy import of
    trade_stats, that copy captured the stub — which monkeypatch could not
    restore — and leaked into this file under xdist (TypeError: unexpected kwarg
    'closed_after').
    """
    import aletheia.analytics.outcome_integrity as oi
    import aletheia.analytics.trade_stats as ts

    # trade_stats must not hold its own copy of the name.
    assert not hasattr(ts, "eligible_strategy_outcomes")

    seen: dict = {}

    def _stub(**filters):
        seen.update(filters)
        return [{
            "symbol": "BTC/USDT", "realized_pnl": 3.0,
            "entry_price": 100.0, "quantity": 1.0,
            "opened_at": "2026-07-20T09:00:00+00:00",
            "closed_at": "2026-07-20T10:00:00+00:00",
        }]

    monkeypatch.setattr(oi, "eligible_strategy_outcomes", _stub)
    rolling = ts.compute_rolling_trade_stats(days=7)
    assert rolling["total_trades"] == 1          # patch on the source is honored
    assert "closed_after" in seen                # called with kwargs, via the module

    monkeypatch.undo()
    # After undo the live function is restored — no stale stub lingers.
    assert ts.outcome_integrity.eligible_strategy_outcomes is oi.eligible_strategy_outcomes


def test_family_scorecard_resolves_eligible_outcomes_through_source_module(monkeypatch, optional_evidence_jobs):
    """Regression (sibling of the trade_stats case): compute_family_scorecard is
    reached via build_trade_stats inside performance_report._collect, so the same
    report tests that patch outcome_integrity.eligible_strategy_outcomes used to
    leak a stub into family_scorecard's private copy — surfacing later as
    ``test_family_pi_context... trade_count == 2`` (the leaked 2-row stub) under
    xdist. Resolving through the module keeps the patch honored and undone.
    """
    import aletheia.analytics.outcome_integrity as oi
    import aletheia.analytics.family_scorecard as fsc

    # family_scorecard must not hold its own copy of the name.
    assert not hasattr(fsc, "eligible_strategy_outcomes")

    seen: dict = {}

    def _stub(**filters):
        seen["called"] = True
        return []

    monkeypatch.setattr(oi, "eligible_strategy_outcomes", _stub)
    fsc.compute_family_scorecard()
    assert seen.get("called")  # patch on the source is honored at call time

    monkeypatch.undo()
    assert fsc.outcome_integrity.eligible_strategy_outcomes is oi.eligible_strategy_outcomes




def test_family_pi_context_and_go_live_metrics_ignore_administrative_rows(optional_evidence_jobs):
    from aletheia.analytics.family_scorecard import compute_family_scorecard
    from aletheia.analytics.go_live_scorecard import _genuine_closed_pnls

    _insert_closed("verified", 7.0, eligible=True)
    _insert_closed("administrative", -500.0, eligible=False)

    families = compute_family_scorecard()
    assert len(families) == 1
    assert families[0]["trade_count"] == 1
    assert families[0]["win_rate"] == 100.0
    assert families[0]["outcome_semantics_version"] == "oi-v2"

    assert _genuine_closed_pnls() == [7.0]


def test_calibration_build_uses_only_verified_current_semantics_outcomes():
    from extensions.analytics.confidence_calibration import build_calibration_report
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    for index in range(5):
        proposal_id = f"proposal-{index}"
        _insert_closed(
            f"verified-{index}", 1.0, eligible=True, proposal_id=proposal_id,
        )
        db.execute(
            "INSERT INTO pi_decisions (id, created_at, action, conviction, proposal_id) "
            "VALUES (?, ?, 'trade', 70, ?)",
            (f"decision-{index}", now, proposal_id),
        )
    _insert_closed("administrative", -10_000.0, eligible=False, proposal_id="proposal-admin")
    db.execute(
        "INSERT INTO pi_decisions (id, created_at, action, conviction, proposal_id) "
        "VALUES ('decision-admin', ?, 'trade', 70, 'proposal-admin')",
        (now,),
    )
    db.commit()

    report = build_calibration_report()
    assert report is not None
    assert report["total_executed"] == 5
    assert report["overall_win_rate"] == 1.0
    assert report["outcome_semantics_version"] == "oi-v2"


def test_old_or_stale_calibration_report_is_rejected_before_pi_consumption():
    from extensions.analytics.confidence_calibration import get_latest_calibration_report
    from aletheia.storage.models import KnowledgeItem
    from aletheia.storage.repositories import insert_knowledge_item

    old = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_executed": 99,
        "ece": 0.01,
        "ece_grade": "excellent",
    }
    insert_knowledge_item(KnowledgeItem(
        item_type="calibration_report",
        title="legacy calibration",
        content=json.dumps(old),
    ))
    assert get_latest_calibration_report() is None


def test_current_fresh_calibration_report_is_accepted():
    from extensions.analytics.confidence_calibration import get_latest_calibration_report
    from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION
    from aletheia.analytics.learning_window import CLEAN_WINDOW_ANCHOR_ID
    from aletheia.storage.models import KnowledgeItem
    from aletheia.storage.repositories import insert_knowledge_item

    # #1939: a persisted calibration report is only returned when its
    # execution_mode matches the resolved selected mode.  Stamp the fixture
    # so it is accepted under the bitget_demo identity.
    current = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
        "clean_window_anchor_id": CLEAN_WINDOW_ANCHOR_ID,
        "total_executed": 5,
        "ece": 0.1,
        "ece_grade": "fair",
        "execution_mode": "bitget_demo",
        "execution_mode_status": "resolved",
    }
    insert_knowledge_item(KnowledgeItem(
        item_type="calibration_report",
        title="current calibration",
        content=json.dumps(current),
    ))
    assert get_latest_calibration_report(execution_mode="bitget_demo") == current


def test_old_memo_and_reasoning_feedback_are_rejected():
    from extensions.analytics.insight_synthesizer import get_latest_memo
    from extensions.analytics.reasoning_feedback import get_latest_reasoning_feedback
    from aletheia.storage.models import KnowledgeItem
    from aletheia.storage.repositories import insert_knowledge_item

    legacy = json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_size": 100,
    })
    insert_knowledge_item(KnowledgeItem(
        item_type="trading_memo", title="legacy memo", content=legacy,
        tags=json.dumps({"valid_until": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}),
    ))
    insert_knowledge_item(KnowledgeItem(
        item_type="reasoning_feedback", title="legacy feedback", content=legacy,
    ))
    assert get_latest_memo() is None
    assert get_latest_reasoning_feedback() is None


def test_memo_statistics_fail_closed_when_complete_cohort_is_unavailable(monkeypatch):
    from extensions.analytics import insight_synthesizer

    monkeypatch.setattr(
        insight_synthesizer,
        "read_complete_outcome_cohort",
        lambda **_: _unavailable_cohort(),
    )
    assert insight_synthesizer._gather_statistics(
        30, execution_mode="bitget_demo"
    ) is None


def test_reasoning_feedback_does_not_persist_an_unavailable_cohort(monkeypatch):
    from extensions.analytics import reasoning_feedback

    db = MagicMock()
    db.execute.return_value.fetchall.return_value = [{"proposal_id": "proposal-1"}]
    monkeypatch.setattr("aletheia.storage.sqlite.get_db", lambda: db)
    monkeypatch.setattr(
        reasoning_feedback,
        "read_complete_outcome_cohort",
        lambda **_: _unavailable_cohort(),
    )

    def unexpected_persist(_feedback):
        raise AssertionError("unavailable cohort must not be persisted")

    monkeypatch.setattr(reasoning_feedback, "_persist_feedback", unexpected_persist)

    assert reasoning_feedback.build_reasoning_feedback(
        execution_mode="bitget_demo"
    ) is None
