"""Post-trade reviews may consume only centrally eligible closed outcomes."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage.models import Position
from tests.test_outcome_closure import _insert_entry_chain


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _seed_position(case: str) -> str:
    position_id = f"review-{case}"
    opportunity_id, proposal_id, order_id = _insert_entry_chain(
        position_id=position_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.2,
        price=100.0,
        execution_mode="bitget_demo",
        strategy_id="strategy-review",
    )
    common = dict(
        id=position_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.2,
        entry_price=100.0,
        current_price=105.0,
        realized_pnl=1.0,
        entry_fee=0.0,
        exit_fee=0.0,
        funding_cost=0.0,
        funding_source="broker_ledger",
        execution_mode="bitget_demo",
        strategy_id="strategy-review",
        proposal_id=proposal_id,
        order_id=order_id,
        opportunity_id=opportunity_id,
        status="closed",
        close_reason="take_profit",
        closed_at=datetime.now(timezone.utc).isoformat(),
        close_category="strategy",
        entry_price_source="broker_fill",
        entry_fill_verified=True,
        exit_price_source="broker_fill",
        exit_fill_verified=True,
        outcome_eligible=True,
        outcome_exclusion_reason="eligible",
        outcome_semantics_version="oi-v1",
    )
    if case == "open":
        common.update(
            status="open",
            closed_at=None,
            close_category="unknown",
            exit_price_source="unavailable",
            exit_fill_verified=False,
            outcome_eligible=False,
            outcome_exclusion_reason="unclassified",
            outcome_semantics_version=None,
        )
    elif case == "administrative":
        common.update(
            close_reason="reconciled_gone",
            close_category="administrative",
            outcome_eligible=False,
            outcome_exclusion_reason="administrative_close",
        )
    elif case == "unknown_funding":
        common.update(
            realized_pnl=None,
            funding_cost=None,
            funding_source="unavailable",
            outcome_eligible=False,
            outcome_exclusion_reason="incomplete_funding_economics",
        )
    repo.insert_position(Position(**common))
    return position_id


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["open", "administrative", "unknown_funding"])
async def test_reviewer_skips_non_eligible_positions_without_calling_llm(
    case, monkeypatch
):
    from aletheia.agents import post_trade_reviewer

    _seed_position(case)
    calls = []

    async def fake_llm(*args, **kwargs):
        calls.append((args, kwargs))
        return ("This must not be written for an ineligible outcome.", None)

    monkeypatch.setattr(post_trade_reviewer, "is_tool_permitted", lambda *_: True)
    monkeypatch.setattr(post_trade_reviewer, "llm_complete", fake_llm)
    monkeypatch.setattr(post_trade_reviewer, "_run_case_builder_safe", lambda *_: ([], []))

    result = await post_trade_reviewer.run_post_trade_reviewer(
        permission_mode="Observe"
    )

    assert result == {
        "outputs": ["post_trade_review_skipped:no_eligible_outcomes"],
        "errors": [],
    }
    assert calls == []
    assert repo.list_knowledge_items(item_type="post_trade_review") == []


@pytest.mark.anyio
async def test_reviewer_reviews_each_eligible_position_window_once(monkeypatch):
    from aletheia.agents import post_trade_reviewer

    position_id = _seed_position("eligible")
    calls = []

    async def fake_llm(*args, **kwargs):
        calls.append((args, kwargs))
        return (
            "The long thesis was confirmed by the verified close and positive net outcome; "
            "the same setup should be repeated only when the entry evidence is equally strong.",
            None,
        )

    monkeypatch.setattr(post_trade_reviewer, "is_tool_permitted", lambda *_: True)
    monkeypatch.setattr(post_trade_reviewer, "llm_complete", fake_llm)
    monkeypatch.setattr(post_trade_reviewer, "_run_case_builder_safe", lambda *_: ([], []))

    first = await post_trade_reviewer.run_post_trade_reviewer(
        permission_mode="Observe"
    )
    second = await post_trade_reviewer.run_post_trade_reviewer(
        permission_mode="Observe"
    )

    assert len(calls) == 1
    assert len(first["outputs"]) == 1
    assert second["outputs"] == [
        "post_trade_review_skipped:already_reviewed:strategy-review"
    ]
    [review] = repo.list_knowledge_items(item_type="post_trade_review")
    assert f"position:{position_id}" in review["tags"]
