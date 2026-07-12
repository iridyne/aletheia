"""Cross-layer proof that one close meaning stays intact through every consumer."""

from __future__ import annotations


def test_full_reconcile_cycle_keeps_paper_internal_local_only(monkeypatch):
    from aletheia.config.loader import load_config, save_config
    from aletheia.portfolio import position_reconciler as reconciler
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    cfg = load_config()
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)
    repo.insert_position(Position(
        id="local-only", symbol="BTC/USDT", market_type="usdt_futures",
        direction="long", quantity=1.0, entry_price=100.0,
        execution_mode="paper_internal", status="open",
    ))
    monkeypatch.setattr(reconciler.exchange_sync, "get_unified_cached_state", lambda: {
        "positions": [], "accounts": [], "age_seconds": 1.0,
    })
    monkeypatch.setattr(reconciler.exchange_sync, "status_for", lambda *a, **k: "ok")

    result = reconciler.run_reconcile("demo")
    assert result["sync_status"] == "local_only"
    assert result["closed_orphans"] == []
    assert result["adopted"] == []
    assert result["disappearance"]["created"] == 0
    assert repo._select_one("positions", "local-only")["status"] == "open"


def test_verified_close_flows_once_to_cash_risk_reporting_and_learning(monkeypatch):
    from aletheia.analytics.outcome_integrity import (
        build_outcome_cohort_summary,
        eligible_strategy_outcomes,
    )
    from aletheia.analytics.trade_stats import build_trade_stats
    from aletheia.learning.outcomes import assess_realized_position
    from aletheia.portfolio import outcome_closure
    from aletheia.portfolio.account_loss import authoritative_account_realized_loss
    from aletheia.portfolio.paper_account import get_paper_account, record_position_opened
    from aletheia.server.routes.analytics import get_equity_curve
    from aletheia.storage import repositories as repo
    from tests.test_outcome_closure import _position, _verified_result

    position = _position()
    repo.insert_position(position)
    record_position_opened(position.id)
    before_cash = get_paper_account(event_limit=100)["cash_balance_usdt"]
    hooks: list[tuple[str, float, str]] = []
    monkeypatch.setattr(
        outcome_closure, "_run_outcome_hooks",
        lambda row, realized, reason: hooks.append((row["id"], realized, reason)),
    )

    evidence = _verified_result(position)
    closed = outcome_closure.close_position_verified(
        position.id, evidence, reason="take_profit",
        source="verified_exit_protocol",
    )
    assert closed["realized_pnl"] == 21.6
    stored = repo._select_one("positions", position.id)
    assert stored["exit_price_source"] == "local_fill"
    assert stored["outcome_eligible"] == 1
    assert get_paper_account(event_limit=100)["cash_balance_usdt"] == before_cash + 21.6

    account = authoritative_account_realized_loss("paper_internal")
    assert account.available is True
    assert account.realized_pnl_usdt == 21.6
    assert assess_realized_position(stored).eligible is True
    assert [row["id"] for row in eligible_strategy_outcomes()] == [position.id]

    cohort = build_outcome_cohort_summary()
    assert (cohort["raw_event_count"], cohort["eligible_outcome_count"], cohort["excluded_outcome_count"]) == (1, 1, 0)
    stats = build_trade_stats()
    assert stats["totals"]["trade_count"] == 1
    assert stats["totals"]["total_pnl"] == 21.6
    curve = get_equity_curve(execution_mode="paper_internal")
    assert curve["total_realized"] == 21.6
    assert len(curve["genuine_points"]) == 1
    assert len(curve["raw_recorded_points"]) == 1

    repeated = outcome_closure.close_position_verified(
        position.id, evidence, reason="take_profit",
    )
    assert repeated["already_closed"] is True
    assert len(hooks) == 1
    assert len([
        event for event in repo.list_paper_account_events(limit=100)
        if event["action"] == "position_closed" and event["position_id"] == position.id
    ]) == 1


def test_administrative_close_is_raw_only_and_risk_fails_closed_without_fake_loss(monkeypatch):
    from aletheia.analytics.outcome_integrity import build_outcome_cohort_summary
    from aletheia.analytics.trade_stats import build_trade_stats
    from aletheia.config.loader import load_config, save_config
    from aletheia.learning.outcomes import assess_realized_position
    from aletheia.portfolio import outcome_closure
    from aletheia.portfolio.account_loss import authoritative_account_realized_loss
    from aletheia.portfolio.paper_account import get_paper_account, record_position_opened
    from aletheia.risk.engine import evaluate_proposal
    from aletheia.risk.kill_switch import is_active
    from aletheia.server.routes.analytics import get_equity_curve
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal
    from tests.test_outcome_closure import _admin_identity, _position

    cfg = load_config()
    cfg.risk.kill_switch_enabled_demo = True
    save_config(cfg)
    position = _position()
    repo.insert_position(position)
    record_position_opened(position.id)
    before_cash = get_paper_account(event_limit=100)["cash_balance_usdt"]
    monkeypatch.setattr(
        outcome_closure, "_run_outcome_hooks",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("administrative close cannot trigger outcome hooks")
        ),
    )

    outcome_closure.close_position_administrative(
        position.id, reason="reconciled_gone",
        reconciliation_identity=_admin_identity(position),
        reconciliation_evidence={"candidate_id": "durable-candidate"},
        source="durable_disappearance",
    )
    stored = repo._select_one("positions", position.id)
    assert stored["realized_pnl"] is None
    assert stored["exit_fee"] is None
    assert stored["outcome_eligible"] == 0
    assert get_paper_account(event_limit=100)["cash_balance_usdt"] == before_cash
    assert not repo.has_paper_account_position_event("position_closed", position.id)
    assert assess_realized_position(stored).eligible is False

    cohort = build_outcome_cohort_summary()
    assert (cohort["raw_event_count"], cohort["eligible_outcome_count"], cohort["excluded_outcome_count"]) == (1, 0, 1)
    assert cohort["outcome_exclusion_reasons"] == {"administrative_close": 1}
    assert build_trade_stats()["totals"]["total_pnl"] is None
    curve = get_equity_curve(execution_mode="paper_internal")
    assert curve["genuine_points"] == []
    assert curve["raw_recorded_points"] == []
    assert curve["raw_economics_unavailable_count"] == 1

    account = authoritative_account_realized_loss("paper_internal")
    assert account.available is False
    assert account.reason == "administrative_close"
    proposal = TradeProposal(
        symbol="ETH/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.01, price=100.0,
    )
    repo.insert_trade_proposal(proposal)
    decision = evaluate_proposal(proposal)
    assert "account_loss_unavailable:administrative_close" in decision.violations
    assert not any(v.startswith("max_daily_loss_exceeded") for v in decision.violations)
    assert is_active() is False


def test_native_and_adopted_reopens_share_one_durable_learning_refreeze():
    from aletheia.learning.control import global_learning_refreeze_reason
    from aletheia.portfolio.position_reconciler import detect_reconcile_thrash
    from aletheia.portfolio.reconciliation_integrity import (
        get_active_reconciliation_integrity_incident,
        record_reconciliation_integrity_incident,
    )
    from tests.test_reconciliation_integrity_incident import _insert_churn_pair

    _insert_churn_pair(symbol="BTC/USDT", source="native", offset_minutes=8)
    _insert_churn_pair(symbol="ETH/USDT", source="adopted", offset_minutes=6)
    detection = detect_reconcile_thrash(window_minutes=30)
    assert detection["reopened_by_source"] == {"adopted": 1, "native": 1}
    incident, created = record_reconciliation_integrity_incident(detection)
    assert created is True
    assert get_active_reconciliation_integrity_incident()["id"] == incident["id"]
    assert global_learning_refreeze_reason() == (
        f"reconciliation_integrity_incident:{incident['id']}"
    )
