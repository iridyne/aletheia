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
    base = {"action": "trade", "symbol": "BTC/USDT", "direction": "long", "reasoning": ""}
    for invalid in ("high", "8.5", None, 101, -1):
        with pytest.raises(DecisionUnavailable, match="invalid_conviction"):
            _parse_decision({**base, "conviction": invalid})
    assert _parse_decision({**base, "conviction": 7}).conviction == 7
    assert _parse_decision({**base, "conviction": 8.0}).conviction == 8


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


def test_spot_short_claim_survives_usdt_futures_coercion():
    """#175: spot-short signal_pending must not be rejected by pending_source_identity_mismatch.

    paper_trader emits a signal_pending with market_type='spot' for a spot short
    opportunity. prepare_decision_as_proposal coerces the decision's market_type to
    'usdt_futures' (trading_decision.py:718-721) and commands.py overwrites
    decision.market_type with the proposal's. _pending_claim_authority_failure then
    compared source.market='spot' vs pi_dec.market_type='usdt_futures' and raised
    pending_source_identity_mismatch, rolling back the claim and leaving the carrier
    stuck in retry.
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

    # Simulate the final PiDecision after spot→usdt_futures coercion.
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
