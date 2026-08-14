"""Focused regressions for decision-chain fixes (batch B4).

#12  non-integer LLM conviction is retryable model-output unavailability.
#152 attribution must not be severed by a conflicting same-symbol Layer-1 opp
     when a matching-direction ranker candidate exists.
#21/#102 serenity opportunities store the instrument under "market_type" — the
     decision path must read that key (not "market").
#9   _make_candidate must carry the opportunity's market_type into the candidate.
#102 (pending) promoted serenity pending-signals recover market_type from the
     source serenity_opportunity (pending_signals don't carry it).
"""

import json
import math
from types import SimpleNamespace

import pytest

from aletheia.agent_shell.trading_decision import (
    DecisionUnavailable,
    TradingDecision,
    _parse_decision,
    _attach_opportunity_context,
    _build_assessment_prompt,
)


def _seed_promoted_serenity_opportunity(
    *, opportunity_id: str, symbol: str, direction: str, market_type: str
) -> None:
    """Persist the source row required by a real Serenity pending carrier."""
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityOpportunity, SerenityTheme

    theme = SerenityTheme(
        name=f"Pending carrier for {opportunity_id}",
        status="promoted",
    )
    repo.insert_serenity_theme(theme)
    repo.insert_serenity_opportunity(SerenityOpportunity(
        id=opportunity_id,
        theme_id=theme.id,
        symbol=symbol,
        direction=direction,
        market_type=market_type,
        asset_class="crypto",
        status="active",
        action_intent="pi_evaluate",
    ))


def test_parse_decision_rejects_non_integer_conviction_without_terminalizing():
    base = {"action": "skip", "reasoning": ""}
    for invalid in ("high", "8.5", None, 101, -1):
        with pytest.raises(DecisionUnavailable, match="invalid_conviction"):
            _parse_decision({**base, "conviction": invalid})
    assert _parse_decision({**base, "conviction": 7}).conviction == 7
    assert _parse_decision({**base, "conviction": 8.0}).conviction == 8


def _complete_trade_payload(**updates):
    payload = {
        "action": "trade",
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "spot",
        "conviction": 90,
        "reasoning": "exact mark supports the planned entry",
        "bear_case": "momentum can reverse before follow-through",
        "stop_price": 98.0,
        "take_profit": 104.0,
        "parameter_rationale": {
            "entry_price": "use the fresh exact market mark at submission",
            "stop_loss": "invalidation below the observed support",
            "take_profit": "next resistance defines the target",
            "notional_usdt": "bounded paper sizing",
            "leverage": "spot uses no leverage",
        },
    }
    payload.update(updates)
    return payload


@pytest.mark.parametrize(
    ("updates", "missing"),
    [
        ({"market_type": None}, "market_type"),
        ({"market_type": "perp"}, "market_type"),
        ({"stop_price": None}, "stop_price"),
        ({"take_profit": 0.0}, "take_profit"),
    ],
)
def test_parse_trade_rejects_incomplete_operator_entry_plan(updates, missing):
    """A schema-shaped trade is not complete until its authored plan is usable."""
    with pytest.raises(DecisionUnavailable, match=missing):
        _parse_decision(_complete_trade_payload(**updates))


def test_structured_schema_requires_plan_only_for_trade():
    """The shared structured validator retries incomplete trades but accepts skips."""
    from aletheia.agent_shell.trading_decision import _DECISION_SCHEMA, _DummySchema
    from aletheia.agents.helpers import _validate_structured_payload

    schema = _DummySchema(_DECISION_SCHEMA)
    for field in ("market_type", "stop_price", "take_profit"):
        incomplete = _complete_trade_payload()
        incomplete.pop(field)
        with pytest.raises(ValueError, match="oneOf"):
            _validate_structured_payload(incomplete, schema, _DECISION_SCHEMA)

    invalid_market = _complete_trade_payload(market_type="perp")
    with pytest.raises(ValueError, match="outside enum"):
        _validate_structured_payload(invalid_market, schema, _DECISION_SCHEMA)

    skip = {
        "action": "skip",
        "conviction": 40,
        "reasoning": "candidate evidence is insufficient",
        "bear_case": "there is no complete entry thesis",
    }
    _validate_structured_payload(skip, schema, _DECISION_SCHEMA)
    for optional_rationale in ({}, {"entry_price": "unused on skip"}):
        _validate_structured_payload(
            {**skip, "parameter_rationale": optional_rationale},
            schema,
            _DECISION_SCHEMA,
        )


def test_structured_schema_accepts_null_skip_kind_only_as_optional_value():
    """Trade output may carry JSON null, but unknown non-empty labels stay invalid."""
    from aletheia.agent_shell.trading_decision import _DECISION_SCHEMA, _DummySchema
    from aletheia.agents.helpers import _validate_structured_payload

    schema = _DummySchema(_DECISION_SCHEMA)
    _validate_structured_payload(
        _complete_trade_payload(skip_kind=None), schema, _DECISION_SCHEMA
    )

    with pytest.raises(ValueError, match="outside enum"):
        _validate_structured_payload(
            _complete_trade_payload(skip_kind="not_a_skip_kind"),
            schema,
            _DECISION_SCHEMA,
        )


@pytest.mark.parametrize(
    ("updates", "repair_reason"),
    [
        ({"market_type": None}, "incomplete_operator_entry_plan:market_type"),
        ({"stop_price": None}, "incomplete_operator_entry_plan:stop_price"),
    ],
)
def test_incomplete_trade_uses_bounded_output_repair(
    monkeypatch, updates, repair_reason
):
    """Semantic plan failure takes the existing one-shot repair path, not Risk."""
    import asyncio
    import aletheia.agent_shell.trading_decision as td

    incomplete = _complete_trade_payload(**updates)
    repaired = _complete_trade_payload()
    repair_reasons: list[str] = []

    async def assess(_ctx):
        return incomplete

    async def repair(_ctx, prior, validation_reason):
        assert prior is incomplete
        repair_reasons.append(validation_reason)
        return repaired

    monkeypatch.setattr(td, "_assess_opportunity", assess)
    monkeypatch.setattr(td, "_reassess_direct_skip", repair)
    monkeypatch.setattr(td, "_proposal_bias_brake_reason", lambda _decision: None)
    monkeypatch.setattr(td, "_get_strategy_paper_trade_count", lambda _strategy_id: 0)
    monkeypatch.setattr(
        td,
        "_compute_position_size",
        lambda *_args, **_kwargs: 25.0,
    )

    decision = asyncio.run(td.evaluate_trading_opportunity({
        "layer2": {
            "entry_mark": {
                "status": "available",
                "symbol": "BTC/USDT",
                "market_type": "spot",
                "price": 100.0,
                "authority": "fresh_exact_market_mark",
            },
        },
    }))

    assert repair_reasons == [repair_reason]
    assert decision.stop_price == 98.0
    assert decision.take_profit == 104.0


def test_unrepaired_trade_plan_is_typed_entry_plan_unavailability(monkeypatch):
    """Two incomplete outputs stay retryable and are not mislabeled as a skip."""
    import asyncio
    import aletheia.agent_shell.trading_decision as td

    incomplete = _complete_trade_payload(stop_price=None)

    async def assess(_ctx):
        return incomplete

    async def repair(_ctx, _prior, _validation_reason):
        return incomplete

    monkeypatch.setattr(td, "_assess_opportunity", assess)
    monkeypatch.setattr(td, "_reassess_direct_skip", repair)

    with pytest.raises(
        DecisionUnavailable,
        match="operator_output_not_repaired:incomplete_operator_entry_plan:stop_price",
    ) as exc_info:
        asyncio.run(td.evaluate_trading_opportunity({}))

    assert exc_info.value.stage == "operator_entry_plan"


def test_complete_trade_without_authoritative_mark_is_retryable(monkeypatch):
    """The model cannot author absolute protection against an unknown entry."""
    import asyncio
    import aletheia.agent_shell.trading_decision as td

    async def assess(_ctx):
        return _complete_trade_payload()

    monkeypatch.setattr(td, "_assess_opportunity", assess)

    with pytest.raises(
        DecisionUnavailable,
        match="candidate_entry_mark_unavailable",
    ) as exc_info:
        asyncio.run(td.evaluate_trading_opportunity({"layer2": {}}))

    assert exc_info.value.stage == "operator_entry_plan"
    assert exc_info.value.filter_trace[-1]["filter_name"] == "operator_entry_mark"


def test_complete_trade_rejects_same_symbol_mark_from_wrong_venue(monkeypatch):
    import asyncio
    import aletheia.agent_shell.trading_decision as td

    async def assess(_ctx):
        return _complete_trade_payload(market_type="usdt_futures")

    monkeypatch.setattr(td, "_assess_opportunity", assess)
    context = {
        "layer2": {
            "pending_signals": [{
                "opportunity_id": "opp-exact-venue",
                "signal_source": "market_observer",
                "symbol": "BTC/USDT",
                "direction": "long",
                "market_type": "usdt_futures",
                "venue": "okx",
            }],
            "entry_mark": {
                "status": "available",
                "symbol": "BTC/USDT",
                "market_type": "usdt_futures",
                "venue": "binance",
                "price": 100.0,
                "authority": "fresh_exact_market_mark",
            },
        },
    }

    with pytest.raises(
        DecisionUnavailable,
        match="candidate_entry_mark_identity_mismatch",
    ) as exc_info:
        asyncio.run(td.evaluate_trading_opportunity(context))

    assert exc_info.value.stage == "operator_entry_plan"

    context["layer2"]["pending_signals"][0]["venue"] = None
    with pytest.raises(
        DecisionUnavailable,
        match="candidate_entry_mark_identity_unavailable",
    ):
        asyncio.run(td.evaluate_trading_opportunity(context))


def test_inverted_prompt_mark_geometry_uses_bounded_output_repair(monkeypatch):
    """A reversed authored stop is repaired before Proposal preparation."""
    import asyncio
    import aletheia.agent_shell.trading_decision as td

    inverted = _complete_trade_payload(stop_price=101.0)
    repaired = _complete_trade_payload(stop_price=98.0)
    repair_reasons: list[str] = []

    async def assess(_ctx):
        return inverted

    async def repair(_ctx, prior, validation_reason):
        assert prior is inverted
        repair_reasons.append(validation_reason)
        return repaired

    monkeypatch.setattr(td, "_assess_opportunity", assess)
    monkeypatch.setattr(td, "_reassess_direct_skip", repair)
    monkeypatch.setattr(td, "_proposal_bias_brake_reason", lambda _decision: None)
    monkeypatch.setattr(td, "_get_strategy_paper_trade_count", lambda _strategy_id: 0)
    monkeypatch.setattr(td, "_compute_position_size", lambda *_args, **_kwargs: 25.0)

    decision = asyncio.run(td.evaluate_trading_opportunity({
        "layer2": {
            "entry_mark": {
                "status": "available",
                "symbol": "BTC/USDT",
                "market_type": "spot",
                "price": 100.0,
                "authority": "fresh_exact_market_mark",
            },
        },
    }))

    assert repair_reasons == ["invalid_operator_entry_plan_geometry:stop_price"]
    assert decision.stop_price == 98.0
    assert [
        (item["filter_name"], item["decision"], item["reason_code"])
        for item in decision.filter_trace
        if item["filter_name"].startswith("operator_entry_plan")
    ] == [
        (
            "operator_entry_plan_output_repair",
            "error",
            "invalid_operator_entry_plan_geometry:stop_price",
        ),
        (
            "operator_entry_plan",
            "passed",
            "operator_entry_plan_geometry_valid",
        ),
    ]


def test_unrepaired_prompt_mark_geometry_is_typed_entry_plan_defer(monkeypatch):
    """A second inverted plan cannot escape the bounded correction lifecycle."""
    import asyncio
    import aletheia.agent_shell.trading_decision as td

    inverted = _complete_trade_payload(stop_price=101.0)

    async def assess(_ctx):
        return inverted

    async def repair(_ctx, _prior, _validation_reason):
        return inverted

    monkeypatch.setattr(td, "_assess_opportunity", assess)
    monkeypatch.setattr(td, "_reassess_direct_skip", repair)

    with pytest.raises(
        DecisionUnavailable,
        match=(
            "operator_output_not_repaired:"
            "invalid_operator_entry_plan_geometry:stop_price"
        ),
    ) as exc_info:
        asyncio.run(td.evaluate_trading_opportunity({
            "layer2": {
                "entry_mark": {
                    "status": "available",
                    "symbol": "BTC/USDT",
                    "market_type": "spot",
                    "price": 100.0,
                    "authority": "fresh_exact_market_mark",
                },
            },
        }))

    assert exc_info.value.stage == "operator_entry_plan"
    assert exc_info.value.filter_trace[-1]["decision"] == "rejected"


@pytest.mark.parametrize(
    "invalid_take_profit",
    [100.99, 110.01, 655.09],
    ids=["below-minimum", "above-maximum", "observed-outlier"],
)
def test_out_of_envelope_risk_reward_uses_bounded_output_repair(
    monkeypatch, invalid_take_profit
):
    """A plausible geometry still needs a bounded reward:risk magnitude."""
    import asyncio
    import aletheia.agent_shell.trading_decision as td

    invalid = _complete_trade_payload(
        stop_price=99.0,
        take_profit=invalid_take_profit,
    )
    repaired = _complete_trade_payload(stop_price=98.0, take_profit=104.0)
    repair_reasons: list[str] = []

    async def assess(_ctx):
        return invalid

    async def repair(_ctx, prior, validation_reason):
        assert prior is invalid
        repair_reasons.append(validation_reason)
        return repaired

    monkeypatch.setattr(td, "_assess_opportunity", assess)
    monkeypatch.setattr(td, "_reassess_direct_skip", repair)
    monkeypatch.setattr(td, "_proposal_bias_brake_reason", lambda _decision: None)
    monkeypatch.setattr(td, "_get_strategy_paper_trade_count", lambda _strategy_id: 0)
    monkeypatch.setattr(td, "_compute_position_size", lambda *_args, **_kwargs: 25.0)

    decision = asyncio.run(td.evaluate_trading_opportunity({
        "layer2": {
            "entry_mark": {
                "status": "available",
                "symbol": "BTC/USDT",
                "market_type": "spot",
                "price": 100.0,
                "authority": "fresh_exact_market_mark",
            },
        },
    }))

    assert repair_reasons == [
        "invalid_operator_entry_plan_risk_reward:outside_envelope"
    ]
    assert decision.stop_price == 98.0
    assert decision.take_profit == 104.0


def test_unrepaired_out_of_envelope_risk_reward_is_typed_defer(monkeypatch):
    """A second implausible plan cannot become a Proposal-ready decision."""
    import asyncio
    import aletheia.agent_shell.trading_decision as td

    invalid = _complete_trade_payload(stop_price=99.0, take_profit=655.09)

    async def assess(_ctx):
        return invalid

    async def repair(_ctx, _prior, validation_reason):
        assert validation_reason == (
            "invalid_operator_entry_plan_risk_reward:outside_envelope"
        )
        return invalid

    monkeypatch.setattr(td, "_assess_opportunity", assess)
    monkeypatch.setattr(td, "_reassess_direct_skip", repair)

    with pytest.raises(
        DecisionUnavailable,
        match=(
            "operator_output_not_repaired:"
            "invalid_operator_entry_plan_risk_reward:outside_envelope"
        ),
    ) as exc_info:
        asyncio.run(td.evaluate_trading_opportunity({
            "layer2": {
                "entry_mark": {
                    "status": "available",
                    "symbol": "BTC/USDT",
                    "market_type": "spot",
                    "price": 100.0,
                    "authority": "fresh_exact_market_mark",
                },
            },
        }))

    assert exc_info.value.stage == "operator_entry_plan"
    assert exc_info.value.filter_trace[-1]["decision"] == "rejected"


def test_operator_spot_short_repairs_then_defers_without_relabel(monkeypatch):
    """A spot carrier cannot silently become a futures short after inference."""
    import asyncio
    import aletheia.agent_shell.trading_decision as td

    spot_short = _complete_trade_payload(
        direction="short",
        market_type="spot",
        stop_price=102.0,
        take_profit=98.0,
    )
    repair_reasons: list[str] = []

    async def assess(_ctx):
        return spot_short

    async def repair(_ctx, _prior, validation_reason):
        repair_reasons.append(validation_reason)
        return spot_short

    monkeypatch.setattr(td, "_assess_opportunity", assess)
    monkeypatch.setattr(td, "_reassess_direct_skip", repair)

    with pytest.raises(
        DecisionUnavailable,
        match="unsupported_operator_instrument_direction:spot_short",
    ) as exc_info:
        asyncio.run(td.evaluate_trading_opportunity({
            "layer2": {
                "entry_mark": {
                    "status": "available",
                    "symbol": "BTC/USDT",
                    "market_type": "spot",
                    "price": 100.0,
                    "authority": "fresh_exact_market_mark",
                },
            },
        }))

    assert repair_reasons == [
        "unsupported_operator_instrument_direction:spot_short"
    ]
    assert exc_info.value.stage == "operator_entry_plan"
    assert exc_info.value.filter_trace[-1]["decision"] == "rejected"


def test_proposal_defense_rejects_parsed_operator_spot_short(tmp_path, monkeypatch):
    """Proposal preparation cannot bypass the exact-instrument planning seam."""
    import asyncio
    import aletheia.agent_shell.trading_decision as td
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    monkeypatch.setattr(td, "_proposal_direction_overheated", lambda *_args: None)
    decision = _parse_decision(_complete_trade_payload(
        direction="short",
        market_type="spot",
        stop_price=102.0,
        take_profit=98.0,
    ))

    with pytest.raises(
        DecisionUnavailable,
        match="unsupported_operator_instrument_direction:spot_short",
    ) as exc_info:
        asyncio.run(td.prepare_decision_as_proposal(decision, "paper_internal"))

    assert exc_info.value.stage == "operator_entry_plan"
    assert exc_info.value.filter_trace[-1]["reason_code"] == (
        "unsupported_operator_instrument_direction:spot_short"
    )


def test_real_structured_validator_exhaustion_is_typed_and_content_free(
    tmp_path, monkeypatch
):
    """Production schema retries cannot collapse into an unclassified command error."""
    import asyncio
    from aletheia.agent_shell import trading_decision as td
    from aletheia.agents import helpers
    from aletheia.model_provider.base import ModelResponse
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    incomplete = _complete_trade_payload()
    incomplete.pop("market_type")
    response_text = json.dumps(incomplete)
    attempts = 0

    async def complete(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        return ModelResponse(
            content=response_text,
            raw={"usage": {"completion_tokens": 20}},
        )

    monkeypatch.setattr(helpers, "get_provider", lambda _name: object())
    monkeypatch.setattr(helpers, "complete_with_fallback", complete)
    monkeypatch.setattr(helpers, "_record_llm", lambda _success: None)
    monkeypatch.setattr(
        "aletheia.model_provider.router.get_provider_for_tier",
        lambda _tier: "synthetic-provider",
    )

    with pytest.raises(
        DecisionUnavailable,
        match="operator_structured_schema_invalid",
    ) as exc_info:
        asyncio.run(td._assess_opportunity({"layer1": {}, "layer2": {}, "layer3": {}}))

    assert attempts == 3
    assert exc_info.value.stage == "operator_output_validation"
    assert response_text not in exc_info.value.reason


def test_structured_empty_content_keeps_existing_daemon_retry_semantics(
    tmp_path, monkeypatch
):
    """A transient empty reasoning-model response is not a two-hour context defer."""
    import asyncio
    from aletheia.agent_shell import trading_decision as td
    from aletheia.agents import helpers
    from aletheia.model_provider.base import ModelResponse
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    attempts = 0

    async def complete(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        return ModelResponse(
            content="",
            raw={"usage": {"completion_tokens": 20}},
        )

    monkeypatch.setattr(helpers, "get_provider", lambda _name: object())
    monkeypatch.setattr(helpers, "complete_with_fallback", complete)
    monkeypatch.setattr(helpers, "_record_llm", lambda _success: None)
    monkeypatch.setattr(
        "aletheia.model_provider.router.get_provider_for_tier",
        lambda _tier: "synthetic-provider",
    )

    with pytest.raises(
        DecisionUnavailable,
        match="llm_assessment_unavailable",
    ) as exc_info:
        asyncio.run(td._assess_opportunity({"layer1": {}, "layer2": {}, "layer3": {}}))

    assert attempts == 3
    assert exc_info.value.stage == "operator_evaluation"


def test_candidate_entry_mark_is_exact_and_visible_to_operator(tmp_path, monkeypatch):
    """Planning uses the selected instrument's fresh venue-scoped authority."""
    import aletheia.agent_shell.trading_decision as td
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        "aletheia.data.price_feed.protective_price_venue",
        lambda _mode, *, configured_data_venue: configured_data_venue,
    )

    def exact_price(symbol, market_type, *, venue):
        calls.append((symbol, market_type, venue))
        return 200.0

    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        exact_price,
    )
    monkeypatch.setattr(
        td,
        "_latest_exact_entry_snapshot",
        lambda *_args: {
            "id": "snap-exact",
            "snapshot_at": "2026-08-07T00:00:00+00:00",
        },
    )
    ctx = {
        "layer1": {},
        "layer2": {
            "pending_signals": [{
                "symbol": "BTC/USDT",
                "direction": "long",
                "market_type": "usdt_futures",
                "venue": "binance",
                "opportunity_id": "opp-entry-plan",
            }],
        },
        "layer3": {},
    }

    assert td.attach_candidate_entry_mark(ctx, "paper_internal") is None

    mark = ctx["layer2"]["entry_mark"]
    assert calls == [("BTC/USDT", "usdt_futures", mark["venue"])]
    assert mark == {
        "status": "available",
        "symbol": "BTC/USDT",
        "market_type": "usdt_futures",
        "price": 200.0,
        "venue": mark["venue"],
        "authority": "fresh_exact_market_mark",
        "snapshot_id": "snap-exact",
        "snapshot_at": "2026-08-07T00:00:00+00:00",
    }
    prompt = td._build_assessment_prompt(ctx)
    assert (
        "Authoritative entry mark for planning: BTC/USDT [usdt_futures] "
        f"price=200.0 venue={mark['venue']} authority=fresh_exact_market_mark"
    ) in prompt
    from aletheia.agent_shell.trading_context import (
        operator_decision_context_fingerprint,
    )

    unavailable_ctx = {
        **ctx,
        "layer2": {
            **ctx["layer2"],
            "entry_mark": {"status": "unavailable"},
        },
    }
    assert operator_decision_context_fingerprint(ctx) != (
        operator_decision_context_fingerprint(unavailable_ctx)
    )

    def broken_price(*_args, **_kwargs):
        raise RuntimeError("simulated price source failure")

    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        broken_price,
    )
    failed_ctx = {
        "layer1": {},
        "layer2": {"pending_signals": list(ctx["layer2"]["pending_signals"])},
        "layer3": {},
    }
    assert td.attach_candidate_entry_mark(failed_ctx, "paper_internal") == (
        "candidate_entry_mark_unavailable"
    )
    assert failed_ctx["layer2"]["entry_mark"]["status"] == "unavailable"


def test_serenity_paper_candidate_uses_configured_exact_venue(monkeypatch):
    """Serenity has no Universe asset, so local Paper uses its configured data venue."""
    import aletheia.agent_shell.trading_decision as td

    monkeypatch.setattr(
        "aletheia.config.loader.load_config",
        lambda: SimpleNamespace(
            market_data=SimpleNamespace(ccxt_exchange="okx"),
        ),
    )
    calls: list[tuple[str, str, str]] = []

    def exact_price(symbol, market_type, *, venue):
        calls.append((symbol, market_type, venue))
        return 100.0

    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        exact_price,
    )
    monkeypatch.setattr(
        td,
        "_latest_exact_entry_snapshot",
        lambda *_args: {"id": "serenity-mark", "snapshot_at": "2026-08-15T00:00:00Z"},
    )
    candidate = {
        "opportunity_id": "serenity-exact-venue",
        "signal_source": "serenity",
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "spot",
    }
    ctx = {"layer2": {"pending_signals": [candidate]}}

    assert td.attach_candidate_entry_mark(ctx, "paper_internal") is None
    assert candidate["venue"] == "okx"
    assert calls == [("BTC/USDT", "spot", "okx")]
    assert ctx["layer2"]["entry_mark"]["venue"] == "okx"


def test_exchange_backed_planning_and_submission_use_exact_venue(
    tmp_path, monkeypatch,
):
    """A newer same-instrument row from another venue cannot price either phase."""
    import asyncio
    import aletheia.agent_shell.trading_decision as td
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    calls: list[tuple[str, str, str]] = []

    def exact_price(symbol, market_type, *, venue):
        calls.append((symbol, market_type, venue))
        return 100.0

    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        exact_price,
    )
    monkeypatch.setattr(
        td,
        "_latest_exact_entry_snapshot",
        lambda symbol, market_type, venue: {
            "id": "okx-exact",
            "symbol": symbol,
            "market_type": market_type,
            "exchange": venue,
            "snapshot_at": "2026-08-15T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        "aletheia.trading.market_data.latest_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("venue-agnostic lookup must not be used")
        ),
    )
    monkeypatch.setattr("aletheia.trust.guard_chain.run_guards", lambda _ctx: [])
    monkeypatch.setattr(
        "aletheia.trust.guard_chain.summarize",
        lambda _verdicts: {"blocks": [], "details": []},
    )
    monkeypatch.setattr(td, "_proposal_direction_overheated", lambda *_args: None)
    monkeypatch.setattr(
        "aletheia.trading.entry_snapshot.capture_attribution_snapshot",
        lambda *_args, **_kwargs: {},
    )

    candidate = {
        "opportunity_id": "serenity-okx",
        "signal_source": "serenity",
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "spot",
        "venue": "okx",
    }
    ctx = {"layer2": {"pending_signals": [candidate]}}
    assert td.attach_candidate_entry_mark(ctx, "okx_demo") is None

    decision = TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="long",
        conviction=80,
        reasoning="exact exchange-backed mark",
        position_size_usdt=20.0,
        market_type="spot",
        venue="okx",
        signal_source="serenity",
    )
    result = asyncio.run(td.prepare_decision_as_proposal(decision, "okx_demo"))

    assert result["proposal"].price == 100.0
    assert calls == [
        ("BTC/USDT", "spot", "okx"),
        ("BTC/USDT", "spot", "okx"),
    ]


def test_assessment_prompt_handles_none_equity():
    """A None equity/available (paper account with an unsettled close) must render
    honestly, not crash the whole evaluation.

    Regression: get_paper_account() deliberately returns equity_usdt=None when a
    closed paper position still lacks settled economics (e.g. reconciled_gone
    administrative closes). The prompt builder formatted it with ``:.0f`` and
    raised ``unsupported format string passed to NoneType.__format__``, killing
    every pi_trading_decision daemon cycle (job_consecutive_failure alert)."""
    ctx = {
        "layer1": {
            "balance": {
                "equity_usdt": None,          # unsettled close → economics unknown
                "available_usdt": 0.0,
                "reserved_margin_usdt": 227.49,
                "source": "local_paper_ledger",
            },
        },
        "layer2": {},
        "layer3": {},
    }
    prompt = _build_assessment_prompt(ctx)          # must not raise
    assert "Equity: unknown" in prompt
    assert "Available: $0" in prompt

    # A fully-unavailable balance still degrades cleanly.
    ctx["layer1"]["balance"] = None
    assert "(unavailable)" in _build_assessment_prompt(ctx)

    # The healthy path (both numeric) is unchanged — byte-identical rendering.
    ctx["layer1"]["balance"] = {"equity_usdt": 1000.0, "available_usdt": 750.0}
    healthy = _build_assessment_prompt(ctx)
    assert "Equity: $1000 | Available: $750" in healthy


def test_submit_decision_reads_snapshot_for_final_market_type(tmp_path, monkeypatch):
    """A local-paper perp proposal uses only a fresh exact-venue perp mark."""
    import asyncio
    import aletheia.agent_shell.trading_decision as td
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import MarketSnapshot
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config

    venue = load_config().market_data.ccxt_exchange or "binance"
    repo.insert_market_snapshot(MarketSnapshot(
        symbol="BTC/USDT",
        market_type="perp",
        exchange=venue,
        price=200.0,
    ))
    captured: dict = {}

    def create(**kwargs):
        captured.update(kwargs)
        return {"status": "pending"}

    monkeypatch.setattr("aletheia.execution.paper_tools.create_paper_proposal", create)
    monkeypatch.setattr(
        "aletheia.trading.entry_snapshot.capture_attribution_snapshot_json",
        lambda _symbol: None,
    )
    monkeypatch.setattr("aletheia.trust.guard_chain.run_guards", lambda ctx: [])
    monkeypatch.setattr(
        "aletheia.trust.guard_chain.summarize",
        lambda verdicts: {"blocks": [], "details": []},
    )
    monkeypatch.setattr(td, "_proposal_direction_overheated", lambda *args: None)

    decision = TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="long",
        conviction=80,
        reasoning="perp setup",
        position_size_usdt=20.0,
        market_type="usdt_futures",
    )
    asyncio.run(td.submit_decision_as_proposal(decision, "paper_internal"))

    assert captured["price"] == 200.0
    assert captured["quantity"] == 0.1
    assert captured["order_type"] == "market"


@pytest.mark.parametrize(
    ("requested_stop", "requested_take_profit"),
    [(0.0, -1.0), (math.nan, math.inf)],
)
def test_submit_decision_replaces_invalid_protection_with_defaults(
    tmp_path, monkeypatch, requested_stop, requested_take_profit
):
    """LLM sentinel values (0/negative) must not become a rejected entry plan."""
    import asyncio
    import aletheia.agent_shell.trading_decision as td
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import MarketSnapshot
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config

    venue = load_config().market_data.ccxt_exchange or "binance"
    repo.insert_market_snapshot(MarketSnapshot(
        symbol="BTC/USDT", market_type="perp", exchange=venue, price=200.0,
    ))
    captured: dict = {}
    monkeypatch.setattr(
        "aletheia.execution.paper_tools.create_paper_proposal",
        lambda **kwargs: captured.update(kwargs) or {"status": "pending"},
    )
    monkeypatch.setattr(
        "aletheia.trading.entry_snapshot.capture_attribution_snapshot_json",
        lambda _symbol: None,
    )
    monkeypatch.setattr("aletheia.trust.guard_chain.run_guards", lambda ctx: [])
    monkeypatch.setattr(
        "aletheia.trust.guard_chain.summarize",
        lambda verdicts: {"blocks": [], "details": []},
    )
    monkeypatch.setattr(td, "_proposal_direction_overheated", lambda *args: None)

    decision = TradingDecision(
        action="trade", symbol="BTC/USDT", direction="long", conviction=80,
        reasoning="zero means unspecified", position_size_usdt=20.0,
        market_type="usdt_futures", stop_price=requested_stop, take_profit=requested_take_profit,
    )
    asyncio.run(td.submit_decision_as_proposal(decision, "paper_internal"))

    assert captured["stop_price"] == 196.0
    assert captured["take_profit"] == 208.0
    assert captured["risk_reward"] == 2.0
    entry_parameters = json.loads(captured["agent_rationale"].split("\n---\n", 1)[1])["entry_parameters"]
    assert entry_parameters["stop_loss"]["value_source"] == "default_config"
    assert entry_parameters["take_profit"]["value_source"] == "default_config"


def test_parsed_operator_trade_never_defaults_invalid_authored_geometry(
    tmp_path, monkeypatch
):
    """A model trade keeps its own plan or defers; config cannot rewrite it."""
    import asyncio
    import aletheia.agent_shell.trading_decision as td
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import MarketSnapshot
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config

    venue = load_config().market_data.ccxt_exchange or "binance"
    repo.insert_market_snapshot(MarketSnapshot(
        symbol="BTC/USDT", market_type="perp", exchange=venue, price=200.0,
    ))
    monkeypatch.setattr("aletheia.trust.guard_chain.run_guards", lambda _ctx: [])
    monkeypatch.setattr(
        "aletheia.trust.guard_chain.summarize",
        lambda _verdicts: {"blocks": [], "details": []},
    )
    monkeypatch.setattr(td, "_proposal_direction_overheated", lambda *_args: None)

    decision = _parse_decision(_complete_trade_payload(
        market_type="usdt_futures",
        stop_price=210.0,
        take_profit=220.0,
    ))

    with pytest.raises(
        DecisionUnavailable,
        match="invalid_operator_entry_plan_geometry:stop_price",
    ):
        asyncio.run(td.prepare_decision_as_proposal(decision, "paper_internal"))

    assert decision.filter_trace[-1]["filter_name"] == "operator_entry_plan"
    assert decision.filter_trace[-1]["reason_code"] == (
        "invalid_operator_entry_plan_geometry:stop_price"
    )


def test_attribution_survives_conflicting_layer1_via_ranker():
    """#152: a conflicting-direction Layer-1 opp must not block the ranker fallback."""
    dec = TradingDecision(action="trade", symbol="BTC/USDT", direction="long",
                          conviction=60, reasoning="")
    ctx = {
        "layer1": {"opportunities": [
            {"symbol": "BTC/USDT", "direction_bias": "short", "id": "opp-short"},
        ]},
        "layer2": {
            "pending_signals": [
                {"symbol": "BTC/USDT", "direction": "long", "opportunity_id": "opp-long",
                 "market_type": "usdt_futures", "signal_source": "market_observer"},
            ],
            "ranker_candidates": [
                {"symbol": "BTC/USDT", "direction": "long", "opportunity_id": "opp-long",
                 "market_type": "usdt_futures"},
            ],
        },
    }
    _attach_opportunity_context(dec, ctx)
    assert dec.opportunity_id == "opp-long"          # not severed by the conflicting opp
    assert dec.signal_source == "market_observer"
    assert dec.market_type == "usdt_futures"


def test_serenity_opportunity_market_type_key():
    """#21/#102: serenity fallback must read market_type (not the non-existent 'market')."""
    _seed_promoted_serenity_opportunity(
        opportunity_id="ser-1",
        symbol="TAO/USDT",
        direction="long",
        market_type="usdt_futures",
    )
    dec = TradingDecision(action="trade", symbol="TAO/USDT", direction="long",
                          conviction=60, reasoning="")
    ctx = {
        "layer2": {"pending_signals": [
            {"symbol": "TAO/USDT", "direction": "long", "opportunity_id": "ser-1",
             "market_type": "usdt_futures", "signal_source": "serenity"},
        ]},
        "layer3": {"serenity_opportunities": [
            {"symbol": "TAO/USDT", "direction": "long", "id": "ser-1",
             "market_type": "usdt_futures", "execution_eligible": True,
             "asset_class": "crypto"},
        ]},
    }
    _attach_opportunity_context(dec, ctx)
    assert dec.opportunity_id == "ser-1"
    assert dec.signal_source == "serenity"
    assert dec.market_type == "usdt_futures"


def test_make_candidate_emits_market_type():
    """#9: _make_candidate must carry the opportunity's instrument type."""
    from aletheia.agents.paper_trader_ranker import _make_candidate

    cand = _make_candidate(
        {"id": "opp-1", "symbol": "BTC/USDT", "market": "usdt_futures"},
        direction="long",
    )
    assert cand.get("market_type") == "usdt_futures", (
        "candidate dropped market_type — trading_decision ranker branch reads "
        "c.get('market_type') and would get None (#9)"
    )
    # Legacy opps that already used the market_type key still work.
    cand2 = _make_candidate({"id": "o2", "symbol": "ETH/USDT", "market_type": "spot"})
    assert cand2.get("market_type") == "spot"


def test_serenity_pending_signal_carries_exact_market_type(monkeypatch):
    """A promoted Serenity carrier includes its executable instrument identity."""
    _seed_promoted_serenity_opportunity(
        opportunity_id="ser-pending",
        symbol="TAO/USDT",
        direction="long",
        market_type="usdt_futures",
    )
    dec = TradingDecision(action="trade", symbol="TAO/USDT", direction="long",
                          conviction=60, reasoning="")
    ctx = {
        "layer2": {"pending_signals": [
            {"symbol": "TAO/USDT", "direction": "long",
             "market_type": "usdt_futures", "opportunity_id": "ser-pending",
             "signal_source": "serenity"},
        ]},
    }
    _attach_opportunity_context(dec, ctx)
    assert dec.opportunity_id == "ser-pending"
    assert dec.signal_source == "serenity"
    assert dec.market_type == "usdt_futures", (
        "pending-signal branch dropped the carrier's exact market_type"
    )


def test_historical_spot_short_claim_survives_legacy_futures_coercion():
    """#175: preserve claim compatibility for already-coerced historical rows.

    New parsed Operator decisions fail closed before any spot-to-futures relabel.
    This reflection exception remains only so an already-persisted legacy claim can
    finish recovery instead of becoming permanently stuck.
    """
    from aletheia.storage.models import Opportunity, PiDecision
    from aletheia.storage.repositories import (
        insert_opportunity, insert_pi_decision,
        transition_open_opportunity_to_queued,
    )
    from aletheia.agent_shell.reflection import _pending_claim_authority_failure

    opp = Opportunity(
        id="opp-spot-short",
        symbol="ETH/USDT",
        market="spot",
        direction_bias="short",
        status="open",
        asset_class="crypto",
    )
    insert_opportunity(opp)
    assert transition_open_opportunity_to_queued(opp.id)

    pending = PiDecision(
        symbol="ETH/USDT",
        direction="short",
        market_type="spot",
        action="signal_pending",
        opportunity_id=opp.id,
        signal_source="market_observer",
    )
    insert_pi_decision(pending)

    # Simulate a historical final PiDecision written before the strict seam.
    final = PiDecision(
        symbol="ETH/USDT",
        direction="short",
        market_type="usdt_futures",
        action="trade",
        opportunity_id=opp.id,
        signal_source="market_observer",
    )

    result = _pending_claim_authority_failure(final)
    assert result is None, f"claim blocked with '{result}' — spot→usdt_futures coercion must be allowed"
