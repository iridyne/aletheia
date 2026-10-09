"""Risk reads an Entry Plan's family and one account-pool definition.

- #14/#14b/#15 resolve a canonical Plan Proposal's family from the durable Plan
  (universe Plan Proposals carry no opportunity_id by construction, check #1a).
- A stale venue-margin tier row leaves #21 unevaluated: DEFER, not REJECT.
- Entry sizing nets the same account pool #12/#17 use: each exchange demo mode
  is its own pool; different demo brokers are not summed.
"""
import json
from types import SimpleNamespace

import pytest

from aletheia.risk import engine
from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import PSLesson, TradeProposal
from aletheia.trading.entry_plan import entry_plan_payload
from tests.test_entry_plan_execution import _plan


@pytest.fixture(autouse=True)
def _pin_account_authority(monkeypatch):
    monkeypatch.setattr("aletheia.risk.engine.account_equity_for_execution", lambda *a, **k: 5000.0)
    monkeypatch.setattr("aletheia.risk.engine._account_equity", lambda *a, **k: 5000.0)
    monkeypatch.setattr("aletheia.risk.engine.account_available_for_execution", lambda *a, **k: 5000.0)


def _plan_proposal(monkeypatch, family="mean_reversion"):
    plan = _plan().model_copy(update={"candidate_family": family})
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(engine, "get_entry_plan", lambda _: {
        "plan_json": payload, "plan_hash": digest, "status": "active"})
    return TradeProposal(
        symbol=plan.symbol, market_type="usdt_futures", direction="long",
        execution_mode="bitget_demo", order_type="limit", price=100.0, quantity=1.0,
        entry_plan_id=plan.plan_id, entry_leg_index=0, opportunity_id=None,
    )


def test_universe_plan_proposal_family_comes_from_the_plan(monkeypatch):
    proposal = _plan_proposal(monkeypatch, family="mean_reversion")
    assert engine._resolve_proposal_family(proposal) == "mean_reversion"


def test_owner_block_scanner_lesson_blocks_the_plan_family(monkeypatch):
    run_migrations()
    lesson = PSLesson(
        source_type="owner", source_id="owner-rule", category="risk",
        severity="high", summary="block momentum", status="applied",
        recommendation_json='{"action":"block_scanner","scanner":"momentum"}',
    )
    repo._insert("ps_lessons", lesson.model_dump())
    proposal = _plan_proposal(monkeypatch, family="momentum")
    assert engine._check_lesson_constraints(proposal) == [
        f"lesson_block_scanner:{lesson.lesson_id}:momentum",
    ]


def test_stale_venue_margin_tiers_defer_instead_of_reject():
    result = engine._risk_result_from_row({
        "approved": False, "violations": json.dumps(["VENUE_MARGIN_TIERS_STALE"]),
    })
    assert result.disposition == "DEFER"
    # A malformed or absent tier row shares the UNAVAILABLE token: stays terminal.
    result = engine._risk_result_from_row({
        "approved": False, "violations": json.dumps(["VENUE_MARGIN_TIERS_UNAVAILABLE"]),
    })
    assert result.disposition == "REJECT"


def test_sizing_open_pool_counts_only_this_demo_account(monkeypatch):
    def position(pid, mode, symbol, notional):
        return {"id": pid, "execution_mode": mode, "symbol": symbol, "quantity": notional / 100.0,
                "entry_price": 100.0, "current_price": 100.0, "stop_price": 98.0,
                "market_type": "usdt_futures"}

    monkeypatch.setattr(engine, "_active_open_positions", lambda: [
        position("own", "bitget_demo", "ETH/USDT", 1000.0),
        position("other-broker", "okx_demo", "SOL/USDT", 80000.0),
    ])
    monkeypatch.setattr(engine, "_unresolved_entry_reservations", lambda: [])
    monkeypatch.setattr(engine, "_pre_order_entry_reservations", lambda **_: [])
    monkeypatch.setattr(engine, "_resolve_capital_base", lambda *a, **k: (5000.0, None))
    monkeypatch.setattr("aletheia.risk.policy.resolve_risk_policy", lambda *a, **k: SimpleNamespace(policy=None))
    state = engine.account_sizing_state("bitget_demo", "BTC/USDT", cfg=SimpleNamespace())
    assert state.open_pool_notional_usdt == pytest.approx(1000.0)


@pytest.mark.parametrize("held_symbol,expected_tier", [("ETH/USDT", 1), ("BTC/USDT", 2)])
def test_financing_tier_is_selected_from_this_symbol_position(monkeypatch, held_symbol, expected_tier):
    """Contract C step 4: a venue tier is a per-instrument position bracket.

    A 10k ETH position in the same account must not push a first BTC entry into
    BTC's second tier; a 10k BTC position must.
    """
    from aletheia.trading.entry_plan_compiler import compile_entry_plan
    from tests.helpers.venue_margin_fixture import seed_venue_margin_envelope
    from tests.test_entry_capture import capture_input

    inputs = capture_input(monkeypatch, "grounded-anchor-direct", "MAKER_PREFERRED")
    seed_venue_margin_envelope(tiers=((0.0, 25_000.0, 150.0, 0.004), (25_000.0, None, 10.0, 0.05)))
    monkeypatch.setattr(engine, "_active_open_positions", lambda: [{
        "id": "held", "execution_mode": "bitget_demo", "symbol": held_symbol, "quantity": 100.0,
        "entry_price": 100.0, "current_price": 100.0, "stop_price": 99.9, "market_type": "usdt_futures",
    }])
    plan = compile_entry_plan(*inputs)
    financing = plan.leverage_resolution
    assert financing is not None and financing.execution_notional_usdt is not None
    assert financing.tier == expected_tier
    held_same_symbol = 10_000.0 if held_symbol == "BTC/USDT" else 0.0
    assert financing.projected_position_notional_usdt == pytest.approx(
        held_same_symbol + financing.execution_notional_usdt
    )
