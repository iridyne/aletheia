"""Focused regression for #1939 — every profitability surface must scope to
one explicitly-identified `execution_mode`.

Seeds a profitable ``paper_internal`` close and a losing ``okx_demo`` close in
the same ledger, then asserts each producer, REST endpoint, and Learning
artifact reader returns cohort-isolated numbers (and never a mode-mixed
aggregate under a selected-mode identity).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    """File-backed SQLite so the same connection is visible from FastAPI
    threadpool workers (mirrors test_analytics_windowing_fixes._setup_env)."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    loader._config_cache = None

    sq.reset_db()
    sq._db_conn = None
    test_db = tmp_path / "test.db"
    conn = sqlite3.connect(
        str(test_db), check_same_thread=False, isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    sq._db_conn = conn
    monkeypatch.setattr(sq, "db_path", lambda: test_db)
    run_migrations()


def _seed_two_mode_ledger() -> None:
    """One profitable paper_internal close, one losing okx_demo close."""
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    # Close both within the rolling window (yesterday) but well after the
    # 2026-07-12 clean-window anchor.
    anchor = datetime.now(timezone.utc) - timedelta(hours=6)

    _insert_closed_position_via_producer(
        position_id="pos-paper",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        realized_pnl=100.0,          # profitable
        execution_mode="paper_internal",
        opened_at=(anchor - timedelta(hours=2)).isoformat(),
        closed_at=(anchor - timedelta(hours=1)).isoformat(),
        close_reason="signal_exit",
        proposal_id="prop-paper",
        order_id="order-paper",
        opportunity_id="opp-paper",
        scanner_family="momentum",
        confidence="high",
    )
    _insert_closed_position_via_producer(
        position_id="pos-okx-demo",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        realized_pnl=-10.0,          # losing
        execution_mode="okx_demo",
        opened_at=(anchor - timedelta(hours=4)).isoformat(),
        closed_at=(anchor - timedelta(hours=3)).isoformat(),
        close_reason="signal_exit",
        proposal_id="prop-okx",
        order_id="order-okx",
        opportunity_id="opp-okx",
        scanner_family="momentum",
        confidence="high",
    )


# ---------------------------------------------------------------------------
# Producer-level: each metric layer must accept and honor execution_mode.
# ---------------------------------------------------------------------------


def test_rolling_stats_and_trade_stats_isolate_by_execution_mode(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_two_mode_ledger()

    from aletheia.analytics.trade_stats import (
        build_strategy_kpis,
        build_trade_stats,
        compute_rolling_trade_stats,
    )

    paper_rolling = compute_rolling_trade_stats(
        days=30, execution_mode="paper_internal",
    )
    okx_rolling = compute_rolling_trade_stats(
        days=30, execution_mode="okx_demo",
    )
    mixed_rolling = compute_rolling_trade_stats(days=30)

    # Each cohort sees only its own close, never the other's.
    assert paper_rolling["total_trades"] == 1
    assert paper_rolling["total_pnl"] == 100.0
    assert paper_rolling["execution_mode"] == "paper_internal"
    assert paper_rolling["execution_mode_status"] == "resolved"

    assert okx_rolling["total_trades"] == 1
    assert okx_rolling["total_pnl"] == -10.0
    assert okx_rolling["execution_mode"] == "okx_demo"

    # And the mode-less (diagnostic) call must be explicitly labelled unavailable.
    assert mixed_rolling["execution_mode"] is None
    assert mixed_rolling["execution_mode_status"] == "unavailable"

    # build_trade_stats — same isolation rule end-to-end.
    paper_stats = build_trade_stats(execution_mode="paper_internal")
    okx_stats = build_trade_stats(execution_mode="okx_demo")
    assert paper_stats["totals"]["trade_count"] == 1
    assert okx_stats["totals"]["trade_count"] == 1
    assert paper_stats["execution_mode"] == "paper_internal"
    assert okx_stats["execution_mode"] == "okx_demo"
    assert paper_stats["totals"]["total_pnl"] != okx_stats["totals"]["total_pnl"]

    kpis = build_strategy_kpis(execution_mode="paper_internal")
    # Every KPI row is stamped with the same cohort identity.
    for v in kpis.values():
        assert v["execution_mode"] == "paper_internal"


def test_family_scorecard_isolates_by_execution_mode(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_two_mode_ledger()

    from aletheia.analytics.family_scorecard import compute_family_scorecard

    paper = compute_family_scorecard(execution_mode="paper_internal")
    okx = compute_family_scorecard(execution_mode="okx_demo")
    # Both share the same scanner_family; the numbers must differ because the
    # cohorts differ.
    paper_family = next(r for r in paper if r["family"] == "momentum")
    okx_family = next(r for r in okx if r["family"] == "momentum")
    assert paper_family["trade_count"] == 1
    assert okx_family["trade_count"] == 1
    assert paper_family["expectancy_pct"] != okx_family["expectancy_pct"]
    assert paper_family["execution_mode"] == "paper_internal"
    assert okx_family["execution_mode"] == "okx_demo"


def test_order_stats_isolates_by_execution_mode(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_two_mode_ledger()

    from aletheia.analytics.trade_stats import compute_order_stats

    paper = compute_order_stats(days=30, execution_mode="paper_internal")
    okx = compute_order_stats(days=30, execution_mode="okx_demo")
    mixed = compute_order_stats(days=30)

    assert paper["total"] == 2
    assert okx["total"] == 2
    assert mixed["total"] == 4
    assert paper["execution_mode"] == "paper_internal"
    assert okx["execution_mode"] == "okx_demo"
    assert mixed["execution_mode_status"] == "unavailable"


# ---------------------------------------------------------------------------
# REST-level: every profitability endpoint must resolve + return one cohort.
# ---------------------------------------------------------------------------


def test_rest_endpoints_fail_closed_without_resolved_mode(
    api_only_runtime, monkeypatch,
):
    """No config → analytics endpoints must return `unavailable`, not an
    all-mode aggregate under a selected-mode identity."""
    import aletheia.analytics.execution_mode as em
    monkeypatch.setattr(em, "resolve_selected_execution_mode", lambda: None)

    from fastapi.testclient import TestClient
    from aletheia.server.app import app

    with TestClient(app) as client:
        for path in (
            "/api/analytics/trade-stats",
            "/api/analytics/trade-insights",
            "/api/analytics/strategy-kpis",
            "/api/analytics/strategy-performance",
            "/api/analytics/cost-summary",
            "/api/analytics/confidence-calibration",
            "/api/analytics/reasoning-feedback",
        ):
            resp = client.get(path)
            assert resp.status_code == 200, path
            body = resp.json()
            assert body.get("execution_mode") is None, path
            assert body.get("execution_mode_status") == "unavailable", path
            # No cohort must appear under an unavailable identity.
            assert body.get("outcome_status") in (
                "unavailable", None,
            ) or body.get("status") == "unavailable", path


def test_rest_trade_stats_scoped_by_query(api_only_runtime, monkeypatch):
    _seed_two_mode_ledger()
    import aletheia.analytics.execution_mode as em
    # Ensure the resolver returns something so caller-side override still wins.
    monkeypatch.setattr(em, "resolve_selected_execution_mode", lambda: "paper_internal")

    from fastapi.testclient import TestClient
    from aletheia.server.app import app

    with TestClient(app) as client:
        paper = client.get(
            "/api/analytics/trade-stats?execution_mode=paper_internal",
        ).json()
        okx = client.get(
            "/api/analytics/trade-stats?execution_mode=okx_demo",
        ).json()

    assert paper["execution_mode"] == "paper_internal"
    assert okx["execution_mode"] == "okx_demo"
    assert paper["totals"]["total_pnl"] != okx["totals"]["total_pnl"]


# ---------------------------------------------------------------------------
# Learning-artifact readers must reject other-mode persisted artifacts.
# ---------------------------------------------------------------------------


def test_calibration_and_reasoning_readers_reject_other_mode_artifacts(
    tmp_path, monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    import json
    from aletheia.storage.repositories import insert_knowledge_item
    from aletheia.storage.models import KnowledgeItem
    from aletheia.analytics.learning_window import outcome_window_metadata

    # Persist ONE calibration report for okx_demo and none for paper_internal.
    okx_report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": 90,
        "total_executed": 10,
        "buckets": [],
        "ece": 0.1,
        "ece_grade": "good",
        "overall_win_rate": 0.6,
        "overconfident_buckets": [],
        "underconfident_buckets": [],
        "calibration_summary": "test",
        "execution_mode": "okx_demo",
        "execution_mode_status": "resolved",
        **outcome_window_metadata(),
    }
    insert_knowledge_item(KnowledgeItem(
        item_type="calibration_report",
        title="okx cal",
        content=json.dumps(okx_report),
    ))

    from aletheia.analytics.confidence_calibration import (
        get_latest_calibration_report,
    )

    # A reader asking for paper_internal must not return the okx report.
    assert get_latest_calibration_report(execution_mode="paper_internal") is None
    got = get_latest_calibration_report(execution_mode="okx_demo")
    assert got is not None
    assert got["execution_mode"] == "okx_demo"

    # Same rule for reasoning feedback.
    okx_fb = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": 30,
        "sample_proposals": 10,
        "sample_executed": 5,
        "approval_outcomes": [],
        "rejection_patterns": [],
        "recommended_avoid": [],
        "recommended_pursue": [],
        "high_conviction_win_rate": None,
        "low_conviction_win_rate": None,
        "execution_mode": "okx_demo",
        "execution_mode_status": "resolved",
        **outcome_window_metadata(),
    }
    insert_knowledge_item(KnowledgeItem(
        item_type="reasoning_feedback",
        title="okx fb",
        content=json.dumps(okx_fb),
    ))
    from aletheia.analytics.reasoning_feedback import get_latest_reasoning_feedback
    assert get_latest_reasoning_feedback(execution_mode="paper_internal") is None
    got_fb = get_latest_reasoning_feedback(execution_mode="okx_demo")
    assert got_fb is not None
    assert got_fb["execution_mode"] == "okx_demo"


def test_learning_builders_fail_closed_without_mode(monkeypatch):
    """`build_calibration_report`, `build_reasoning_feedback`, and
    `synthesize_trading_memo` must never write an all-mode artifact when the
    resolver has no answer (#1939)."""
    import aletheia.analytics.execution_mode as em
    monkeypatch.setattr(em, "resolve_selected_execution_mode", lambda: None)

    from aletheia.analytics.confidence_calibration import build_calibration_report
    from aletheia.analytics.reasoning_feedback import build_reasoning_feedback

    assert build_calibration_report() is None
    assert build_reasoning_feedback() is None


def test_learning_persistence_rate_limits_against_artifact_mode(monkeypatch):
    from aletheia.analytics import confidence_calibration as calibration
    from aletheia.analytics import reasoning_feedback as feedback

    generated_at = datetime.now(timezone.utc).isoformat()
    calibration_seen: dict = {}
    feedback_seen: dict = {}
    recent = {"generated_at": generated_at}

    monkeypatch.setattr(
        calibration,
        "get_latest_calibration_report",
        lambda **kwargs: calibration_seen.update(kwargs) or recent,
    )
    monkeypatch.setattr(
        feedback,
        "get_latest_reasoning_feedback",
        lambda **kwargs: feedback_seen.update(kwargs) or recent,
    )

    calibration._persist_report({"execution_mode": "okx_demo"})
    feedback._persist_feedback({"execution_mode": "okx_demo"})

    assert calibration_seen["execution_mode"] == "okx_demo"
    assert feedback_seen["execution_mode"] == "okx_demo"


def test_export_passes_mode_before_limit_to_repository(monkeypatch):
    import aletheia.server.routes.data as data

    seen: dict = {}

    def list_positions(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(data.repo, "list_positions", list_positions)
    response = data.export_trades(limit=1, execution_mode="okx_demo")

    assert seen == {"status": "closed", "execution_mode": "okx_demo", "limit": 1}
    assert response.headers["content-disposition"].endswith("trades_export_okx_demo.csv")


def test_system_operator_layer2_resolves_and_threads_one_mode(monkeypatch):
    import aletheia.agent_shell.trading_context as context
    import aletheia.analytics.confidence_calibration as calibration
    import aletheia.analytics.execution_mode as execution_mode
    import aletheia.analytics.insight_synthesizer as insight
    import aletheia.analytics.reasoning_feedback as feedback
    import aletheia.analytics.trade_stats as trade_stats
    import aletheia.agents.paper_trader_ranker as ranker
    import aletheia.agent_shell.reflection as reflection
    import aletheia.storage.repositories as repo

    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        execution_mode,
        "resolve_selected_execution_mode",
        lambda: calls.append(("resolve", None)) or "okx_demo",
    )
    monkeypatch.setattr(
        context,
        "eligible_strategy_outcomes",
        lambda **kwargs: calls.append(("outcomes", kwargs.get("execution_mode"))) or [],
    )
    monkeypatch.setattr(reflection, "get_last_reflection", lambda: None)
    monkeypatch.setattr(repo, "list_strategies", lambda **_kwargs: [])
    monkeypatch.setattr(
        trade_stats,
        "build_strategy_kpis",
        lambda **kwargs: calls.append(("kpis", kwargs.get("execution_mode"))) or {},
    )
    monkeypatch.setattr(
        trade_stats,
        "build_trade_stats",
        lambda **kwargs: calls.append(("trade_stats", kwargs.get("execution_mode"))) or {},
    )
    monkeypatch.setattr(
        insight,
        "get_latest_memo",
        lambda **kwargs: calls.append(("memo", kwargs.get("execution_mode"))) or None,
    )
    monkeypatch.setattr(
        feedback,
        "get_latest_reasoning_feedback",
        lambda **kwargs: calls.append(("feedback", kwargs.get("execution_mode"))) or None,
    )
    monkeypatch.setattr(
        calibration,
        "get_latest_calibration_report",
        lambda **kwargs: calls.append(("calibration", kwargs.get("execution_mode"))) or None,
    )
    monkeypatch.setattr(ranker, "build_paper_candidates", lambda **_kwargs: [])
    monkeypatch.setattr(context, "_build_pending_signal_queue", lambda: ([], {}))
    monkeypatch.setattr(context, "_record_candidate_supply_snapshot", lambda _layer: None)

    context._build_layer2_short_term()

    assert calls.count(("resolve", None)) == 1
    assert {
        name: mode for name, mode in calls if name != "resolve"
    } == {
        "outcomes": "okx_demo",
        "kpis": "okx_demo",
        "trade_stats": "okx_demo",
        "memo": "okx_demo",
        "feedback": "okx_demo",
        "calibration": "okx_demo",
    }
