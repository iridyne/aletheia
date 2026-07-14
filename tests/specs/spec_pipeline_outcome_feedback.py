"""Position -> Outcome -> Attribution/Feedback production contracts."""

from __future__ import annotations

import sqlite3

from tests.specs.spec_meta import SpecClaim, spec_claim


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.files.jsonl as jsonl
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq.reset_db()
    conn = sqlite3.connect(
        str(tmp_path / "app.db"),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    jsonl._last_audit_hash = None
    run_migrations()
    return conn


@spec_claim(
    SpecClaim(
        "PL-OF-01",
        "pipeline_outcome_feedback",
        "§1 local paper outcome handoff",
        "L2c",
        "A real native paper entry closed through the production router becomes one "
        "versioned eligible local-fill Outcome whose Proposal, Risk, Order, Position, "
        "Opportunity, and scanner identity reach Attribution and factor Feedback.",
        status="implemented",
    )
)
def test_native_paper_close_reaches_attribution_and_feedback(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.analytics.factor_feedback import build_factor_feedback
    from aletheia.analytics.trade_attribution import build_trade_attribution_rows
    from aletheia.execution.paper_broker import execute_paper_order
    from aletheia.learning.outcomes import assess_realized_position
    from aletheia.portfolio.position_manager import close_position_routed
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity, RiskDecision, TradeProposal

    opportunity = Opportunity(
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        thesis="paper outcome round trip",
        source_agent="market_observer",
        scanner_family="trend",
    )
    repo.insert_opportunity(opportunity)
    proposal = TradeProposal(
        opportunity_id=opportunity.id,
        symbol=opportunity.symbol,
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
        status="approved",
    )
    repo.insert_trade_proposal(proposal)
    decision = RiskDecision(
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode=proposal.execution_mode,
        proposal_execution_mode=proposal.execution_mode,
        decision_role="authoritative",
        evaluated_quantity=proposal.quantity,
        proposal_commit_hash=proposal.commit_hash,
    )
    repo.insert_risk_decision(decision)
    opened = execute_paper_order(
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        execution_mode=proposal.execution_mode,
        opportunity_id=opportunity.id,
    )

    close_position_routed(
        opened["position_id"],
        "take_profit",
        fallback_exit_price=110.0,
    )

    position = repo._select_one("positions", opened["position_id"])
    assert assess_realized_position(position).eligible is True
    assert position["close_category"] == "strategy"
    assert position["exit_price_source"] == "local_fill"
    assert position["exit_fill_verified"] == 1
    assert position["outcome_eligible"] == 1
    assert position["outcome_exclusion_reason"] == "eligible"
    attribution = build_trade_attribution_rows(limit=10)
    assert len(attribution) == 1
    row = attribution[0]
    assert row["position_id"] == opened["position_id"]
    assert row["proposal_id"] == proposal.id
    assert row["order_id"] == opened["order_id"]
    assert row["opportunity_id"] == opportunity.id
    assert row["scanner_family"] == "trend"
    assert build_factor_feedback(limit=10, min_trades=1)


@spec_claim(
    SpecClaim(
        "PL-OF-02",
        "pipeline_outcome_feedback",
        "§2 unverified exchange exclusion",
        "L2c",
        "An exchange Position closed without private fill evidence keeps an explicit current "
        "semantics exclusion and cannot trigger outcome-only shadow or serenity feedback.",
        status="implemented",
    )
)
def test_unverified_exchange_close_is_explicitly_excluded_from_feedback(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION
    from aletheia.portfolio import pnl
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=100.0,
        entry_fee=0.06,
        execution_mode="bitget_demo",
        opportunity_id="exchange-outcome-opportunity",
        trade_school="serenity",
    )
    repo.insert_position(position)
    feedback_calls: list[tuple] = []
    monkeypatch.setattr(
        pnl,
        "_close_serenity_feedback",
        lambda *args: feedback_calls.append(args),
    )

    pnl.close_position(position.id, exit_price=110.0, reason="take_profit")

    stored = repo._select_one("positions", position.id)
    assert feedback_calls == []
    assert stored["close_category"] == "strategy"
    assert stored["exit_fill_verified"] == 0
    assert stored["outcome_eligible"] == 0
    assert stored["outcome_exclusion_reason"] == "unverified_exit_fill"
    assert stored["outcome_semantics_version"] == OUTCOME_SEMANTICS_VERSION


@spec_claim(
    SpecClaim(
        "PL-OF-03",
        "pipeline_outcome_feedback",
        "§3 atomic local settlement",
        "L2b",
        "A local paper Position close and its paper-account cash settlement commit as one "
        "unit; if settlement fails, the Position remains open and no eligible Outcome "
        "escapes for Attribution or Feedback.",
        status="implemented",
    )
)
def test_paper_close_rolls_back_when_cash_settlement_fails(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.portfolio import paper_account
    from aletheia.portfolio.pnl import close_position
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=100.0,
        entry_fee=0.06,
        execution_mode="paper_internal",
    )
    repo.insert_position(position)
    paper_account.get_paper_account()
    monkeypatch.setattr(
        paper_account,
        "settle_position_closed",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("forced paper settlement failure")
        ),
    )

    import pytest

    with pytest.raises(RuntimeError, match="forced paper settlement failure"):
        close_position(position.id, exit_price=110.0, reason="take_profit")

    stored = repo._select_one("positions", position.id)
    assert stored["status"] == "open"
    assert stored["outcome_eligible"] == 0
    assert not repo.has_paper_account_position_event("position_closed", position.id)


@spec_claim(
    SpecClaim(
        "PL-OF-04",
        "pipeline_outcome_feedback",
        "§4 attribution gate",
        "L2b",
        "A locally verified close without the Proposal, entry Order, and Opportunity "
        "identity required by downstream Attribution remains explicitly ineligible.",
        status="implemented",
    )
)
def test_local_close_without_causal_identity_is_not_feedback_eligible(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes
    from aletheia.portfolio.pnl import close_position
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=100.0,
        entry_fee=0.06,
        execution_mode="paper_internal",
        entry_price_source="local_fill",
        entry_fill_verified=True,
    )
    repo.insert_position(position)

    result = close_position(position.id, exit_price=110.0, reason="take_profit")

    stored = repo._select_one("positions", position.id)
    assert result["outcome_eligible"] is False
    assert stored["outcome_eligible"] == 0
    assert stored["outcome_exclusion_reason"] == "missing_attribution"
    assert eligible_strategy_outcomes() == []
    assert repo.list_outcome_hook_jobs(position_id=position.id) == []


@spec_claim(
    SpecClaim(
        "PL-OF-05",
        "pipeline_outcome_feedback",
        "§5 complete futures economics",
        "L2b",
        "A futures close without verified funding economics may close its lifecycle but "
        "cannot become an eligible Outcome or feed factor learning.",
        status="implemented",
    )
)
def test_futures_close_without_funding_evidence_is_explicitly_ineligible(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.analytics.factor_feedback import build_factor_feedback
    from aletheia.execution.paper_broker import execute_paper_order
    from aletheia.learning.outcomes import assess_realized_position
    from aletheia.portfolio.pnl import close_position
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity, RiskDecision, TradeProposal

    opportunity = Opportunity(
        symbol="BTC/USDT",
        market="perp",
        direction_bias="long",
        thesis="funding evidence gate",
        source_agent="market_observer",
        scanner_family="trend",
    )
    repo.insert_opportunity(opportunity)
    proposal = TradeProposal(
        opportunity_id=opportunity.id,
        symbol=opportunity.symbol,
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
        status="approved",
    )
    repo.insert_trade_proposal(proposal)
    decision = RiskDecision(
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode=proposal.execution_mode,
        proposal_execution_mode=proposal.execution_mode,
        decision_role="authoritative",
        evaluated_quantity=proposal.quantity,
        proposal_commit_hash=proposal.commit_hash,
    )
    repo.insert_risk_decision(decision)
    opened = execute_paper_order(
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        execution_mode=proposal.execution_mode,
        opportunity_id=opportunity.id,
    )

    close_position(opened["position_id"], exit_price=110.0, reason="take_profit")

    stored = repo._select_one("positions", opened["position_id"])
    assessment = assess_realized_position(stored)
    assert assessment.eligible is False
    assert assessment.reason == "incomplete_funding_economics"
    assert stored["outcome_eligible"] == 0
    assert stored["outcome_exclusion_reason"] == "incomplete_funding_economics"
    assert build_factor_feedback(limit=10, min_trades=1) == []


@spec_claim(
    SpecClaim(
        "PL-OF-06",
        "pipeline_outcome_feedback",
        "§6 consumer-side economic verification",
        "L2b",
        "Outcome consumers independently reject a producer-marked result when the stored "
        "net PnL does not equal fill economics minus complete costs.",
        status="implemented",
    )
)
def test_outcome_consumer_rejects_inconsistent_net_economics():
    from aletheia.analytics.outcome_integrity import assess_position_closure

    assessment = assess_position_closure({
        "id": "position-economic-mismatch",
        "status": "closed",
        "symbol": "BTC/USDT",
        "market_type": "spot",
        "direction": "long",
        "quantity": 1.0,
        "entry_price": 100.0,
        "current_price": 110.0,
        "entry_fee": 0.1,
        "exit_fee": 0.1,
        "funding_cost": 0.0,
        "funding_source": "not_applicable",
        "realized_pnl": 10.0,
        "closed_at": "2026-07-13T00:00:00+00:00",
        "proposal_id": "proposal-1",
        "order_id": "order-1",
        "opportunity_id": "opportunity-1",
        "_attribution_complete": 1,
        "close_category": "strategy",
        "entry_price_source": "local_fill",
        "entry_fill_verified": True,
        "exit_price_source": "local_fill",
        "exit_fill_verified": True,
        "outcome_eligible": True,
        "outcome_exclusion_reason": "eligible",
        "outcome_semantics_version": "oi-v1",
    })

    assert assessment.eligible is False
    assert assessment.reason == "incomplete_realized_economics"


@spec_claim(
    SpecClaim(
        "PL-OF-07",
        "pipeline_outcome_feedback",
        "§7 verified entry evidence",
        "L2b",
        "A private exit fill cannot make an Outcome eligible when the opening Fill and "
        "entry fee were only estimated from an aggregate order response.",
        status="implemented",
    )
)
def test_verified_exit_cannot_mask_an_unverified_entry_fill():
    from aletheia.analytics.outcome_integrity import assess_position_closure

    assessment = assess_position_closure({
        "id": "position-unverified-entry",
        "status": "closed",
        "symbol": "BTC/USDT",
        "market_type": "spot",
        "direction": "long",
        "quantity": 1.0,
        "entry_price": 100.0,
        "current_price": 110.0,
        "entry_fee": 0.1,
        "exit_fee": 0.1,
        "funding_cost": 0.0,
        "funding_source": "not_applicable",
        "realized_pnl": 9.8,
        "closed_at": "2026-07-13T00:00:00+00:00",
        "proposal_id": "proposal-1",
        "order_id": "order-1",
        "opportunity_id": "opportunity-1",
        "_attribution_complete": 1,
        "close_category": "strategy",
        "entry_price_source": "unavailable",
        "entry_fill_verified": False,
        "exit_price_source": "broker_fill",
        "exit_fill_verified": True,
        "outcome_eligible": True,
        "outcome_exclusion_reason": "eligible",
        "outcome_semantics_version": "oi-v1",
    })

    assert assessment.eligible is False
    assert assessment.reason == "unverified_entry_fill"


@spec_claim(
    SpecClaim(
        "PL-OF-08",
        "pipeline_outcome_feedback",
        "§8 relational attribution identity",
        "L2c",
        "Non-empty causal IDs do not authorize feedback unless the Position's entry "
        "Order belongs to the same Proposal and Opportunity chain.",
        status="implemented",
    )
)
def test_mismatched_entry_order_identity_blocks_outcome_hooks(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.paper_broker import execute_paper_order
    from aletheia.portfolio.pnl import close_position
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity, Order, RiskDecision, TradeProposal

    opportunity = Opportunity(
        id="opportunity-outcome-primary",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        thesis="primary outcome chain",
        source_agent="market_observer",
        scanner_family="trend",
    )
    repo.insert_opportunity(opportunity)
    proposal = TradeProposal(
        id="proposal-outcome-primary",
        opportunity_id=opportunity.id,
        symbol=opportunity.symbol,
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
        status="approved",
    )
    repo.insert_trade_proposal(proposal)
    decision = RiskDecision(
        id="risk-outcome-primary",
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode=proposal.execution_mode,
        proposal_execution_mode=proposal.execution_mode,
        decision_role="authoritative",
        evaluated_quantity=proposal.quantity,
        proposal_commit_hash=proposal.commit_hash,
    )
    repo.insert_risk_decision(decision)
    opened = execute_paper_order(
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        execution_mode=proposal.execution_mode,
        opportunity_id=opportunity.id,
    )

    unrelated_opportunity = Opportunity(
        id="opportunity-outcome-unrelated",
        symbol="ETH/USDT",
        market="spot",
        direction_bias="long",
        thesis="unrelated chain",
        source_agent="market_observer",
        scanner_family="trend",
    )
    repo.insert_opportunity(unrelated_opportunity)
    unrelated_proposal = TradeProposal(
        id="proposal-outcome-unrelated",
        opportunity_id=unrelated_opportunity.id,
        symbol=unrelated_opportunity.symbol,
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=50.0,
        execution_mode="paper_internal",
        status="approved",
    )
    repo.insert_trade_proposal(unrelated_proposal)
    unrelated_decision = RiskDecision(
        id="risk-outcome-unrelated",
        proposal_id=unrelated_proposal.id,
        approved=True,
        allowed_execution_mode=unrelated_proposal.execution_mode,
        proposal_execution_mode=unrelated_proposal.execution_mode,
        decision_role="authoritative",
        evaluated_quantity=unrelated_proposal.quantity,
        proposal_commit_hash=unrelated_proposal.commit_hash,
    )
    repo.insert_risk_decision(unrelated_decision)
    unrelated_order = Order(
        id="order-outcome-unrelated",
        proposal_id=unrelated_proposal.id,
        risk_decision_id=unrelated_decision.id,
        symbol=unrelated_proposal.symbol,
        market_type=unrelated_proposal.market_type,
        direction=unrelated_proposal.direction,
        order_type=unrelated_proposal.order_type,
        quantity=unrelated_proposal.quantity,
        price=unrelated_proposal.price,
        execution_mode=unrelated_proposal.execution_mode,
        status="filled",
    )
    repo.insert_order(unrelated_order)
    repo.update_position(opened["position_id"], order_id=unrelated_order.id)

    result = close_position(
        opened["position_id"], exit_price=110.0, reason="take_profit"
    )

    stored = repo._select_one("positions", opened["position_id"])
    assert result["outcome_eligible"] is False
    assert stored["outcome_exclusion_reason"] == "missing_attribution"


@spec_claim(
    SpecClaim(
        "PL-OF-09",
        "pipeline_outcome_feedback",
        "§9 producer and reader eligibility parity",
        "L2c",
        "A close producer evaluates the complete prospective closed Position with the "
        "same clean-window and economic rules as the read cohort before persisting "
        "eligibility or publishing Outcome hooks.",
        status="implemented",
    )
)
def test_producer_rejects_pre_clean_close_before_persisting_or_running_hooks(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.analytics import shadow_trades
    from aletheia.analytics.outcome_integrity import (
        assess_reportable_outcome,
        raw_account_events,
    )
    from aletheia.portfolio.pnl import close_position
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    from tests.test_outcome_closure import _insert_entry_chain

    position_id = "position-pre-clean-producer"
    opportunity_id, proposal_id, order_id = _insert_entry_chain(
        position_id=position_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        price=100.0,
        execution_mode="bitget_demo",
        entry_fee=0.1,
    )
    repo.insert_position(Position(
        id=position_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="bitget_demo",
        entry_fee=0.1,
        entry_price_source="broker_fill",
        entry_fill_verified=True,
        funding_cost=0.0,
        funding_source="not_applicable",
        opportunity_id=opportunity_id,
        proposal_id=proposal_id,
        order_id=order_id,
    ))
    hook_calls = []
    monkeypatch.setattr(
        shadow_trades,
        "resolve_shadow_trades_for_position",
        lambda row: hook_calls.append(row["id"]),
    )

    result = close_position(
        position_id,
        exit_price=110.0,
        reason="take_profit",
        actual_exit_fee=0.1,
        closed_at="2026-07-10T00:00:00+00:00",
        verified_exit_evidence={
            "fill_ids": ["exit-fill-pre-clean"],
            "fill_quantity": 1.0,
        },
    )

    stored = repo._select_one("positions", position_id)
    [raw_event] = raw_account_events(execution_mode="bitget_demo")
    assessment = assess_reportable_outcome(raw_event)
    assert result["outcome_eligible"] is False
    assert result["outcome_exclusion_reason"] == "pre_clean_window"
    assert stored["outcome_eligible"] == 0
    assert stored["outcome_exclusion_reason"] == "pre_clean_window"
    assert assessment.eligible is False
    assert assessment.reason == "pre_clean_window"
    assert hook_calls == []


@spec_claim(
    SpecClaim(
        "PL-OF-10",
        "pipeline_outcome_feedback",
        "§10 atomic verified close claim",
        "L2c",
        "Concurrent verified close attempts claim the open Position with BEGIN IMMEDIATE "
        "and an in-transaction reread, producing one close, one audit, and one hook.",
        status="implemented",
    )
)
def test_concurrent_verified_close_claims_position_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    _setup_db(tmp_path, monkeypatch)
    from aletheia.portfolio import outcome_closure
    from aletheia.storage import repositories as repo
    from tests.test_outcome_closure import _position, _verified_result

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    evidence = _verified_result(position)
    pre_read_barrier = threading.Barrier(2)
    original_load_open = outcome_closure._load_open

    def synchronized_pre_read(position_id):
        row, already = original_load_open(position_id)
        if already is None:
            pre_read_barrier.wait(timeout=10)
        return row, already

    hook_calls = []
    monkeypatch.setattr(outcome_closure, "_load_open", synchronized_pre_read)
    monkeypatch.setattr(
        outcome_closure,
        "_run_outcome_hooks",
        lambda row, realized, reason: hook_calls.append(row["id"]),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                outcome_closure.close_position_verified,
                position.id,
                evidence,
                reason="take_profit",
            )
            for _ in range(2)
        ]
        results = [future.result(timeout=15) for future in futures]

    assert sorted(result["already_closed"] for result in results) == [False, True]
    assert hook_calls == [position.id]
    assert len(repo.list_outcome_hook_jobs(position_id=position.id)) == 1
    assert len([
        row for row in repo.list_audit_events(limit=20)
        if row["event_type"] == "position_verified_closed"
        and row["entity_id"] == position.id
    ]) == 1


@spec_claim(
    SpecClaim(
        "PL-OF-11",
        "pipeline_outcome_feedback",
        "§11 durable hook replay",
        "L2c",
        "An eligible close commits its hook job before delivery; a post-commit hook "
        "failure leaves a retryable row that a later worker completes exactly once.",
        status="implemented",
    )
)
def test_hook_failure_after_close_commit_is_durable_and_replayable(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.analytics import shadow_trades
    from aletheia.portfolio import outcome_closure, outcome_hooks
    from aletheia.storage import repositories as repo
    from tests.test_outcome_closure import _position, _verified_result

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    calls = []

    def flaky_shadow_hook(row):
        calls.append(row["id"])
        if len(calls) == 1:
            raise RuntimeError("forced post-commit hook failure")
        return 1

    monkeypatch.setattr(
        shadow_trades,
        "resolve_shadow_trades_for_position",
        flaky_shadow_hook,
    )

    closed = outcome_closure.close_position_verified(
        position.id,
        _verified_result(position),
        reason="take_profit",
    )

    [failed_job] = repo.list_outcome_hook_jobs(position_id=position.id)
    assert closed["closed"] is True
    assert repo._select_one("positions", position.id)["status"] == "closed"
    assert failed_job["status"] == "failed"
    assert failed_job["attempts"] == 1
    assert "forced post-commit hook failure" in failed_job["last_error"]

    replayed = outcome_hooks.process_outcome_hook_jobs(
        position_id=position.id,
        now="2100-01-01T00:00:00+00:00",
    )

    [completed_job] = repo.list_outcome_hook_jobs(position_id=position.id)
    assert replayed["completed"] == 1
    assert completed_job["status"] == "completed"
    assert completed_job["attempts"] == 2
    assert calls == [position.id, position.id]


@spec_claim(
    SpecClaim(
        "PL-OF-12",
        "pipeline_outcome_feedback",
        "§12 atomic close and hook intent",
        "L2c",
        "If local cash settlement rolls back an otherwise eligible close, its durable "
        "Outcome hook intent rolls back in the same transaction.",
        status="implemented",
    )
)
def test_hook_job_rolls_back_with_failed_eligible_paper_close(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    import pytest

    from aletheia.portfolio import paper_account
    from aletheia.portfolio.pnl import close_position
    from aletheia.storage import repositories as repo
    from tests.test_outcome_closure import _position

    position = _position(execution_mode="paper_internal")
    repo.insert_position(position)
    paper_account.record_position_opened(position.id)
    monkeypatch.setattr(
        paper_account,
        "settle_position_closed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("forced settlement rollback")
        ),
    )

    with pytest.raises(RuntimeError, match="forced settlement rollback"):
        close_position(position.id, exit_price=110.0, reason="take_profit")

    assert repo._select_one("positions", position.id)["status"] == "open"
    assert repo.list_outcome_hook_jobs(position_id=position.id) == []


@spec_claim(
    SpecClaim(
        "PL-OF-13",
        "pipeline_outcome_feedback",
        "§13 replay eligibility revalidation",
        "L2c",
        "A replay worker rereads the closed Position and reapplies the central Outcome "
        "assessment before invoking any hook, failing closed when authority changed.",
        status="implemented",
    )
)
def test_replay_rereads_closed_position_and_fails_closed(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.analytics import shadow_trades
    from aletheia.portfolio import outcome_closure, outcome_hooks
    from aletheia.storage import repositories as repo
    from tests.test_outcome_closure import _position, _verified_result

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    monkeypatch.setattr(outcome_closure, "_run_outcome_hooks", lambda *_args: None)
    outcome_closure.close_position_verified(position.id, _verified_result(position))
    repo.update_position(
        position.id,
        outcome_eligible=False,
        outcome_exclusion_reason="not_marked_eligible",
    )
    calls = []
    monkeypatch.setattr(
        shadow_trades,
        "resolve_shadow_trades_for_position",
        lambda row: calls.append(row["id"]),
    )

    replayed = outcome_hooks.process_outcome_hook_jobs(position_id=position.id)

    [job] = repo.list_outcome_hook_jobs(position_id=position.id)
    assert replayed["skipped"] == 1
    assert job["status"] == "skipped"
    assert job["last_error"] == "not_marked_eligible"
    assert calls == []


@spec_claim(
    SpecClaim(
        "PL-OF-14",
        "pipeline_outcome_feedback",
        "§14 concurrent hook replay claim",
        "L2c",
        "Concurrent replay workers atomically lease one pending hook job, so only one "
        "worker invokes the hook while the other observes no claimable work.",
        status="implemented",
    )
)
def test_concurrent_replay_workers_invoke_hook_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    _setup_db(tmp_path, monkeypatch)
    from aletheia.analytics import shadow_trades
    from aletheia.portfolio import outcome_closure, outcome_hooks
    from aletheia.storage import repositories as repo
    from tests.test_outcome_closure import _position, _verified_result

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    monkeypatch.setattr(outcome_closure, "_run_outcome_hooks", lambda *_args: None)
    outcome_closure.close_position_verified(position.id, _verified_result(position))

    entered = threading.Event()
    release = threading.Event()
    calls = []

    def blocking_hook(row):
        calls.append(row["id"])
        entered.set()
        assert release.wait(timeout=10)
        return 1

    monkeypatch.setattr(
        shadow_trades,
        "resolve_shadow_trades_for_position",
        blocking_hook,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            outcome_hooks.process_outcome_hook_jobs,
            position_id=position.id,
        )
        assert entered.wait(timeout=10)
        second = executor.submit(
            outcome_hooks.process_outcome_hook_jobs,
            position_id=position.id,
        )
        second_result = second.result(timeout=10)
        release.set()
        first_result = first.result(timeout=10)

    [job] = repo.list_outcome_hook_jobs(position_id=position.id)
    assert sorted([first_result["claimed"], second_result["claimed"]]) == [0, 1]
    assert calls == [position.id]
    assert job["status"] == "completed"
    assert job["attempts"] == 1


@spec_claim(
    SpecClaim(
        "PL-OF-15",
        "pipeline_outcome_feedback",
        "§15 Serenity replay idempotency",
        "L2c",
        "A crash after Serenity feedback commits but before its job completes can be "
        "replayed without creating a second post-mortem for the same Position.",
        status="implemented",
    )
)
def test_serenity_replay_after_completion_gap_is_durably_idempotent(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    import pytest

    from aletheia.portfolio import outcome_closure, outcome_hooks
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position, SerenityOpportunity, SerenityTheme
    from tests.test_outcome_closure import _insert_entry_chain, _verified_result

    theme = SerenityTheme(name="outbox idempotency", status="promoted")
    repo.insert_serenity_theme(theme)
    serenity = SerenityOpportunity(
        id="serenity-outbox-opportunity",
        theme_id=theme.id,
        symbol="BTC/USDT",
        direction="long",
        status="active",
    )
    repo.insert_serenity_opportunity(serenity)
    position_id = "position-serenity-outbox"
    opportunity_id, proposal_id, order_id = _insert_entry_chain(
        position_id=position_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=2.0,
        price=100.0,
        execution_mode="bitget_demo",
        entry_fee=0.2,
        opportunity_id=serenity.id,
    )
    position = Position(
        id=position_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=2.0,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="bitget_demo",
        entry_fee=0.2,
        entry_price_source="broker_fill",
        entry_fill_verified=True,
        funding_cost=0.0,
        funding_source="not_applicable",
        opportunity_id=opportunity_id,
        proposal_id=proposal_id,
        order_id=order_id,
        trade_school="serenity",
    )
    repo.insert_position(position)
    monkeypatch.setattr(outcome_closure, "_run_outcome_hooks", lambda *_args: None)
    outcome_closure.close_position_verified(position.id, _verified_result(position))

    original_complete = repo.mark_outcome_hook_job_completed
    monkeypatch.setattr(
        repo,
        "mark_outcome_hook_job_completed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SystemExit("simulated process crash after hook commit")
        ),
    )
    with pytest.raises(SystemExit, match="simulated process crash"):
        outcome_hooks.process_outcome_hook_jobs(
            position_id=position.id,
            hook_type=outcome_hooks.SERENITY_POST_MORTEM_HOOK,
        )

    [post_mortem] = repo.list_serenity_post_mortems(
        opportunity_id=serenity.id,
        limit=10,
    )
    serenity_jobs = repo.list_outcome_hook_jobs(
        position_id=position.id,
        hook_type=outcome_hooks.SERENITY_POST_MORTEM_HOOK,
    )
    assert len(repo.list_outcome_hook_jobs(position_id=position.id)) == 2
    assert post_mortem["position_id"] == position.id
    assert serenity_jobs[0]["status"] == "processing"

    monkeypatch.setattr(repo, "mark_outcome_hook_job_completed", original_complete)
    replayed = outcome_hooks.process_outcome_hook_jobs(
        position_id=position.id,
        hook_type=outcome_hooks.SERENITY_POST_MORTEM_HOOK,
        now="2100-01-01T00:00:00+00:00",
        stale_after_seconds=0,
    )

    post_mortems = repo.list_serenity_post_mortems(
        opportunity_id=serenity.id,
        limit=10,
    )
    [completed_job] = repo.list_outcome_hook_jobs(
        position_id=position.id,
        hook_type=outcome_hooks.SERENITY_POST_MORTEM_HOOK,
    )
    assert replayed["completed"] == 1
    assert len(post_mortems) == 1
    assert completed_job["status"] == "completed"
    assert completed_job["attempts"] == 2


@spec_claim(
    SpecClaim(
        "PL-OF-16",
        "pipeline_outcome_feedback",
        "§16 scheduled durable replay",
        "L2a",
        "The scheduler replays durable Outcome hook jobs every minute on the serial "
        "SQLite IO lane and keeps replay active during layered pause.",
        status="implemented",
    )
)
def test_scheduler_registers_outcome_hook_replay_as_essential_io_work():
    from pathlib import Path

    source = (
        Path(__file__).parent.parent.parent
        / "aletheia"
        / "agent_runtime"
        / "scheduler.py"
    ).read_text(encoding="utf-8")
    assert '"outcome_hook_replay"' in source.split("_ESSENTIAL_JOBS", 1)[1].split(
        "}", 1
    )[0]
    registration = source.split(
        '_guarded("outcome_hook_replay", _run_outcome_hook_replay)', 1
    )[1].split(")\n", 1)[0]
    assert "minutes=1" in registration
    assert 'executor="io"' in registration
    assert "max_instances=1" in registration


@spec_claim(
    SpecClaim(
        "PL-OF-17",
        "pipeline_outcome_feedback",
        "§17 partial shadow replay recovery",
        "L2c",
        "A shadow hook with a missing price or partial row failure stays retryable; "
        "a later replay resumes the unresolved rows without losing the batch anchor.",
        status="implemented",
    )
)
def test_partial_shadow_resolution_fails_job_then_resumes_remaining_rows(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.analytics import shadow_trades
    from aletheia.portfolio import outcome_closure, outcome_hooks
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import ShadowTrade
    from tests.test_outcome_closure import _position, _verified_result

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    batch_id = "partial-shadow-batch"
    counterfactual = ShadowTrade(
        id="shadow-counterfactual",
        batch_id=batch_id,
        opportunity_id="counterfactual-opportunity",
        symbol="ETH/USDT",
        market_type="usdt_futures",
        direction="long",
        entry_price=100.0,
        was_selected=False,
    )
    selected = ShadowTrade(
        id="shadow-selected",
        batch_id=batch_id,
        opportunity_id=position.opportunity_id,
        symbol=position.symbol,
        market_type=position.market_type,
        direction=position.direction,
        entry_price=position.entry_price,
        was_selected=True,
    )
    repo.insert_shadow_trade(counterfactual)
    repo.insert_shadow_trade(selected)
    monkeypatch.setattr(outcome_closure, "_run_outcome_hooks", lambda *_args: None)
    outcome_closure.close_position_verified(position.id, _verified_result(position))

    monkeypatch.setattr(
        shadow_trades,
        "_current_price",
        lambda symbol, *_args, **_kwargs: 110.0 if symbol == position.symbol else None,
    )
    first = outcome_hooks.process_outcome_hook_jobs(position_id=position.id)

    [failed_job] = repo.list_outcome_hook_jobs(position_id=position.id)
    first_rows = {row["id"]: row for row in repo.list_shadow_trades(batch_id=batch_id)}
    assert first["failed"] == 1
    assert failed_job["status"] == "failed"
    assert first_rows[selected.id]["resolved_at"] is not None
    assert first_rows[counterfactual.id]["resolved_at"] is None

    monkeypatch.setattr(
        shadow_trades,
        "_current_price",
        lambda _symbol, *_args, **_kwargs: 120.0,
    )
    replayed = outcome_hooks.process_outcome_hook_jobs(
        position_id=position.id,
        now="2100-01-01T00:00:00+00:00",
    )

    [completed_job] = repo.list_outcome_hook_jobs(position_id=position.id)
    final_rows = {row["id"]: row for row in repo.list_shadow_trades(batch_id=batch_id)}
    assert replayed["completed"] == 1
    assert completed_job["status"] == "completed"
    assert completed_job["attempts"] == 2
    assert final_rows[selected.id]["resolved_at"] == first_rows[selected.id]["resolved_at"]
    assert final_rows[counterfactual.id]["resolved_at"] is not None


@spec_claim(
    SpecClaim(
        "PL-OF-19",
        "pipeline_outcome_feedback",
        "§18 account-scoped funding provenance",
        "L2c",
        "An exchange-backed futures close cannot accept local-simulation funding; "
        "the lifecycle closes but no Outcome, Attribution, Feedback, or outbox job escapes.",
        status="implemented",
    )
)
def test_exchange_close_rejects_local_simulation_funding_provenance(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.analytics.factor_feedback import build_factor_feedback
    from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes
    from aletheia.analytics.trade_attribution import build_trade_attribution_rows
    from aletheia.portfolio import outcome_closure
    from aletheia.storage import repositories as repo
    from tests.test_outcome_closure import _position, _verified_result

    position = _position(execution_mode="bitget_demo").model_copy(
        update={"funding_source": "local_simulation"}
    )
    repo.insert_position(position)

    result = outcome_closure.close_position_verified(
        position.id,
        _verified_result(position),
        reason="take_profit",
    )

    stored = repo._select_one("positions", position.id)
    assert result["realized_pnl"] is None
    assert result["outcome_eligible"] is False
    assert stored["funding_cost"] is None
    assert stored["funding_source"] == "unavailable"
    assert stored["outcome_exclusion_reason"] == "incomplete_funding_economics"
    assert repo.list_outcome_hook_jobs(position_id=position.id) == []
    assert eligible_strategy_outcomes() == []
    assert build_trade_attribution_rows(limit=10) == []
    assert build_factor_feedback(limit=10, min_trades=1) == []


@spec_claim(
    SpecClaim(
        "PL-OF-20",
        "pipeline_outcome_feedback",
        "§19 local funding provenance",
        "L2c",
        "A local-paper futures close cannot accept broker-ledger funding; no net cash "
        "settlement, Outcome, Attribution, Feedback, or outbox job is produced.",
        status="implemented",
    )
)
def test_local_paper_close_rejects_broker_funding_provenance(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.analytics.factor_feedback import build_factor_feedback
    from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes
    from aletheia.analytics.trade_attribution import build_trade_attribution_rows
    from aletheia.portfolio import paper_account, pnl
    from aletheia.storage import repositories as repo
    from tests.test_outcome_closure import _position

    position = _position(execution_mode="paper_internal").model_copy(
        update={"funding_source": "broker_ledger"}
    )
    repo.insert_position(position)
    paper_account.record_position_opened(position.id)

    result = pnl.close_position(
        position.id,
        exit_price=110.0,
        reason="take_profit",
    )

    stored = repo._select_one("positions", position.id)
    assert result["realized_pnl"] is None
    assert result["outcome_eligible"] is False
    assert stored["funding_cost"] is None
    assert stored["funding_source"] == "unavailable"
    assert stored["outcome_exclusion_reason"] == "incomplete_funding_economics"
    assert repo.list_outcome_hook_jobs(position_id=position.id) == []
    assert eligible_strategy_outcomes() == []
    assert build_trade_attribution_rows(limit=10) == []
    assert build_factor_feedback(limit=10, min_trades=1) == []
    assert not any(
        event["action"] == "position_closed"
        for event in repo.list_paper_account_events(limit=20)
    )
