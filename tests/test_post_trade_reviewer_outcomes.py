"""Post-trade reviews may consume only centrally eligible closed outcomes."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage.models import Position
from tests.test_outcome_closure import _insert_entry_chain


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _seed_position(
    case: str,
    *,
    position_id: str | None = None,
    strategy_id: str = "strategy-review",
    closed_at: str | None = None,
) -> str:
    position_id = position_id or f"review-{case}"
    opportunity_id, proposal_id, order_id = _insert_entry_chain(
        position_id=position_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.2,
        price=100.0,
        execution_mode="bitget_demo",
        strategy_id=strategy_id,
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
        strategy_id=strategy_id,
        proposal_id=proposal_id,
        order_id=order_id,
        opportunity_id=opportunity_id,
        status="closed",
        close_reason="take_profit",
        closed_at=closed_at or datetime.now(timezone.utc).isoformat(),
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


@pytest.mark.anyio
async def test_reviewer_receives_complete_exact_proposal_decision_context(monkeypatch):
    from aletheia.agents import post_trade_reviewer
    from aletheia.storage.models import PiDecision
    from aletheia.storage.sqlite import get_db

    position_id = _seed_position("eligible", position_id="review-exact-proposal")
    position = repo.get_position(position_id)
    proposal_id = position["proposal_id"]
    long_reason = "complete reviewer reason " + ("z" * 900)
    get_db().execute(
        "UPDATE trade_proposals SET agent_rationale=? WHERE id=?",
        ("complete proposal rationale", proposal_id),
    )
    repo.insert_pi_decision(PiDecision(
        id="review-exact-pi",
        action="trade",
        proposal_id=proposal_id,
        opportunity_id=position["opportunity_id"],
        symbol=position["symbol"],
        direction=position["direction"],
        market_type=position["market_type"],
        reasoning=long_reason,
        bear_case="complete reviewer bear case",
        key_assumptions='["reviewer assumption"]',
        evidence_manifest_json='{"version":"evidence-v1","ids":["ev-review"]}',
        learning_manifest_json='{"version":"learning-v1","ids":["learn-review"]}',
    ))
    prompts = []

    async def fake_llm(*args, **kwargs):
        prompts.append(kwargs["user"])
        return ("Review based only on complete persisted context.", None)

    monkeypatch.setattr(post_trade_reviewer, "is_tool_permitted", lambda *_: True)
    monkeypatch.setattr(post_trade_reviewer, "llm_complete", fake_llm)
    monkeypatch.setattr(post_trade_reviewer, "_run_case_builder_safe", lambda *_: ([], []))

    await post_trade_reviewer.run_post_trade_reviewer(permission_mode="Observe")

    [prompt] = prompts
    assert long_reason in prompt
    assert "complete reviewer bear case" in prompt
    assert "reviewer assumption" in prompt
    assert "complete proposal rationale" in prompt
    assert "ev-review" in prompt
    assert "learn-review" in prompt


@pytest.mark.anyio
async def test_reviewer_budget_omits_whole_positions_without_truncating_selected(
    monkeypatch,
):
    from aletheia.agents import post_trade_reviewer
    from aletheia.storage.models import PiDecision

    oversized_id = _seed_position(
        "eligible", position_id="review-budget-oversized", strategy_id="strategy-budget"
    )
    selected_id = _seed_position(
        "eligible", position_id="review-budget-selected", strategy_id="strategy-budget"
    )
    oversized = repo.get_position(oversized_id)
    selected = repo.get_position(selected_id)
    complete_entry_summary = (
        "ENTRY-CONTEXT-START " + ("context " * 80) + "ENTRY-CONTEXT-END"
    )
    from aletheia.storage.sqlite import get_db
    get_db().execute(
        "UPDATE positions SET entry_market_summary=? WHERE id=?",
        (complete_entry_summary, selected_id),
    )
    repo.insert_pi_decision(PiDecision(
        id="review-budget-pi-oversized",
        action="trade",
        proposal_id=oversized["proposal_id"],
        opportunity_id=oversized["opportunity_id"],
        symbol=oversized["symbol"],
        direction=oversized["direction"],
        market_type=oversized["market_type"],
        reasoning="OVERSIZED-START " + ("x" * 5000) + " OVERSIZED-END",
    ))
    complete_selected_reason = (
        "SELECTED-COMPLETE-START " + ("y" * 300) + " SELECTED-COMPLETE-END"
    )
    repo.insert_pi_decision(PiDecision(
        id="review-budget-pi-selected",
        action="trade",
        proposal_id=selected["proposal_id"],
        opportunity_id=selected["opportunity_id"],
        symbol=selected["symbol"],
        direction=selected["direction"],
        market_type=selected["market_type"],
        reasoning=complete_selected_reason,
    ))
    monkeypatch.setattr(post_trade_reviewer, "_REVIEW_INPUT_BUDGET_CHARS", 2000)
    prompts = []

    async def fake_llm(*args, **kwargs):
        prompts.append(kwargs["user"])
        return (
            f"Bounded review {len(prompts)} based on complete selected records and "
            "their exact persisted decision evidence, without inventing omitted facts.",
            None,
        )

    monkeypatch.setattr(post_trade_reviewer, "is_tool_permitted", lambda *_: True)
    monkeypatch.setattr(post_trade_reviewer, "llm_complete", fake_llm)
    monkeypatch.setattr(post_trade_reviewer, "_run_case_builder_safe", lambda *_: ([], []))

    await post_trade_reviewer.run_post_trade_reviewer(permission_mode="Observe")

    [prompt] = prompts
    assert complete_selected_reason in prompt
    assert "SELECTED-COMPLETE-END" in prompt
    assert complete_entry_summary in prompt
    assert "ENTRY-CONTEXT-END" in prompt
    assert "OVERSIZED-START" not in prompt
    assert "OVERSIZED-END" not in prompt
    assert "Omitted complete position records: 1" in prompt
    assert len(prompt) <= post_trade_reviewer._REVIEW_INPUT_BUDGET_CHARS
    [first_review] = repo.list_knowledge_items(item_type="post_trade_review")
    assert f"position:{selected_id}" in first_review["tags"]
    assert f"position:{oversized_id}" not in first_review["tags"]

    monkeypatch.setattr(post_trade_reviewer, "_REVIEW_INPUT_BUDGET_CHARS", 10_000)
    await post_trade_reviewer.run_post_trade_reviewer(permission_mode="Observe")

    assert len(prompts) == 2
    assert "OVERSIZED-START" in prompts[1]
    assert "OVERSIZED-END" in prompts[1]
    reviews = repo.list_knowledge_items(item_type="post_trade_review")
    assert any(f"position:{oversized_id}" in review["tags"] for review in reviews)


@pytest.mark.anyio
async def test_reviewer_gives_each_strategy_a_fair_outcome_window(monkeypatch):
    from aletheia.agents import post_trade_reviewer

    now = datetime.now(timezone.utc).replace(microsecond=0)
    _seed_position(
        "eligible",
        position_id="review-quiet-strategy",
        strategy_id="strategy-quiet",
        closed_at=(now - timedelta(seconds=1)).isoformat(),
    )
    for index in range(200):
        _seed_position(
            "eligible",
            position_id=f"review-busy-strategy-{index:03d}",
            strategy_id="strategy-busy",
            closed_at=now.isoformat(),
        )

    reviewed_strategies = []

    async def fake_llm(*args, **kwargs):
        return ("Review based only on the supplied eligible outcomes.", None)

    def fake_create(*args, **kwargs):
        strategy_id = kwargs.get("strategy_id")
        reviewed_strategies.append(strategy_id)
        return SimpleNamespace(id=f"review-{strategy_id}")

    monkeypatch.setattr(post_trade_reviewer, "is_tool_permitted", lambda *_: True)
    monkeypatch.setattr(post_trade_reviewer, "llm_complete", fake_llm)
    monkeypatch.setattr(post_trade_reviewer, "create_post_trade_review", fake_create)
    monkeypatch.setattr(post_trade_reviewer, "_run_case_builder_safe", lambda *_: ([], []))

    result = await post_trade_reviewer.run_post_trade_reviewer(
        permission_mode="Observe"
    )

    assert set(reviewed_strategies) == {"strategy-busy", "strategy-quiet"}
    assert set(result["outputs"]) == {
        "post_trade_review:review-strategy-busy",
        "post_trade_review:review-strategy-quiet",
    }


@pytest.mark.anyio
async def test_reviewer_bounds_each_round_and_rotates_across_strategy_backlog(
    monkeypatch,
):
    """A real 45-strategy SQLite backlog must not fan out in one agent run."""
    from aletheia.agents import post_trade_reviewer

    for index in range(45):
        _seed_position(
            "eligible",
            position_id=f"review-rotation-{index:03d}",
            strategy_id=f"strategy-rotation-{index:03d}",
        )

    calls_by_round: list[list[str]] = []
    current_round: list[str] = []

    async def fake_llm(*args, **kwargs):
        strategy_id = kwargs["user"].split("Strategy: ", 1)[1].split(".", 1)[0]
        current_round.append(strategy_id)
        return ("Review based only on the supplied eligible outcomes.", None)

    def fake_create(*args, **kwargs):
        return SimpleNamespace(id=f"review-{kwargs.get('strategy_id')}")

    monkeypatch.setattr(post_trade_reviewer, "is_tool_permitted", lambda *_: True)
    monkeypatch.setattr(post_trade_reviewer, "llm_complete", fake_llm)
    monkeypatch.setattr(post_trade_reviewer, "create_post_trade_review", fake_create)
    monkeypatch.setattr(post_trade_reviewer, "_run_case_builder_safe", lambda *_: ([], []))

    for _ in range(3):
        current_round = []
        await post_trade_reviewer.run_post_trade_reviewer(permission_mode="Observe")
        calls_by_round.append(list(current_round))

    assert [len(round_calls) for round_calls in calls_by_round] == [20, 20, 20]
    assert len(set().union(*map(set, calls_by_round))) == 45


@pytest.mark.anyio
async def test_reviewer_high_cardinality_pool_bounds_eligibility_queries(monkeypatch):
    """The identity query may scan SQLite, but Python/eligibility fanout stays fixed."""
    from aletheia.agents import post_trade_reviewer
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    get_db().executemany(
        """
        INSERT INTO positions (
            id, symbol, market_type, direction, quantity, entry_price,
            execution_mode, strategy_id, status, closed_at, outcome_eligible
        ) VALUES (?, 'BTC/USDT', 'usdt_futures', 'long', 0.2, 100.0,
                  'bitget_demo', ?, 'closed', ?, 1)
        """,
        [
            (f"review-cardinality-{index:04d}", f"strategy-cardinality-{index:04d}", now)
            for index in range(1000)
        ],
    )

    assert len(
        post_trade_reviewer._closed_strategy_page(next_key=None, page_size=21)
    ) == 21

    real_eligible = post_trade_reviewer.eligible_strategy_outcomes
    checked_strategies: list[str | None] = []

    def counted_eligible(**kwargs):
        checked_strategies.append(kwargs.get("strategy_id"))
        return real_eligible(**kwargs)

    llm_calls = []

    async def fake_llm(*args, **kwargs):
        llm_calls.append((args, kwargs))
        return ("must not review ineligible rows", None)

    monkeypatch.setattr(post_trade_reviewer, "is_tool_permitted", lambda *_: True)
    monkeypatch.setattr(post_trade_reviewer, "eligible_strategy_outcomes", counted_eligible)
    monkeypatch.setattr(post_trade_reviewer, "llm_complete", fake_llm)
    monkeypatch.setattr(post_trade_reviewer, "_run_case_builder_safe", lambda *_: ([], []))

    await post_trade_reviewer.run_post_trade_reviewer(permission_mode="Observe")

    assert len(checked_strategies) == 20
    assert llm_calls == []


@pytest.mark.anyio
async def test_reviewer_reviewed_and_failed_items_do_not_block_later_strategies(
    monkeypatch,
):
    """Durable progress advances after skips and LLM failures, including restart."""
    from aletheia.agents import post_trade_reviewer
    from aletheia.storage.models import KnowledgeItem

    for index in range(25):
        strategy_id = f"strategy-progress-{index:03d}"
        _seed_position(
            "eligible",
            position_id=f"review-progress-{index:03d}",
            strategy_id=strategy_id,
        )
        if index < 10:
            [outcome] = post_trade_reviewer.eligible_strategy_outcomes(
                strategy_id=strategy_id,
                limit=200,
            )
            window_key = post_trade_reviewer._fills_window_key(
                strategy_id,
                [{"id": f"position:{outcome['id']}"}],
            )
            repo.insert_knowledge_item(
                KnowledgeItem(
                    id=f"existing-review-{index:03d}",
                    item_type="post_trade_review",
                    title="Existing review",
                    strategy_id=strategy_id,
                    tags=json.dumps([f"window:{window_key}"]),
                )
            )

    calls_by_round: list[list[str]] = []
    current_round: list[str] = []

    async def fake_llm(*args, **kwargs):
        strategy_id = kwargs["user"].split("Strategy: ", 1)[1].split(".", 1)[0]
        current_round.append(strategy_id)
        if strategy_id < "strategy-progress-020":
            return ("", f"temporary failure for {strategy_id}")
        return ("Review based only on the supplied eligible outcomes.", None)

    def fake_create(*args, **kwargs):
        return SimpleNamespace(id=f"review-{kwargs.get('strategy_id')}")

    monkeypatch.setattr(post_trade_reviewer, "is_tool_permitted", lambda *_: True)
    monkeypatch.setattr(post_trade_reviewer, "llm_complete", fake_llm)
    monkeypatch.setattr(post_trade_reviewer, "create_post_trade_review", fake_create)
    monkeypatch.setattr(post_trade_reviewer, "_run_case_builder_safe", lambda *_: ([], []))

    for _ in range(2):
        current_round = []
        await post_trade_reviewer.run_post_trade_reviewer(permission_mode="Observe")
        calls_by_round.append(list(current_round))

    # First page: 10 durable skips + 10 failed LLM attempts. A fresh round-robin
    # instance on the second call reads the persisted successor from settings.
    assert calls_by_round[0] == [
        f"strategy-progress-{index:03d}" for index in range(10, 20)
    ]
    assert set(calls_by_round[1]) >= {
        f"strategy-progress-{index:03d}" for index in range(20, 25)
    }
