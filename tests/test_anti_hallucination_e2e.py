"""E2E: anti-hallucination attribution chain (R0) — opportunity → context → decision.

Locks the R0 fix end-to-end (LEARNING_LOOP_AUDIT_2026-06-26 §R0): a seeded open
opportunity must surface into the PI Agent's Layer-1 context WITH its id +
scanner_family, and when the PI picks that symbol+direction,
_attach_opportunity_context backfills the real opportunity_id (+ strategy_id +
scanner_family) onto the decision — instead of the old hard-coded None that severed
the whole learning attribution chain (trade_case_study entry-context / guard
predicates / reasoning_feedback scanner all went 'unknown').

Behavioral (DB-backed real functions, no mock-call-verify) — complements the
structural AST guard in spec_learning_pipeline::LP-S05.
"""
import pytest

from tests.test_entry_logic import setup_env


def test_r0_opportunity_id_flows_opportunity_to_decision(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import insert_opportunity

    opp = Opportunity(
        symbol="BTC/USDT", market="spot", direction_bias="long",
        scanner_family="momentum", confidence="high", confidence_score=80,
        status="open", asset_class="crypto",
    )
    insert_opportunity(opp)

    from aletheia.agent_shell.trading_context import build_enriched_trading_context
    ctx = build_enriched_trading_context()
    opps = ctx["layer1"]["opportunities"]
    seeded = next((o for o in opps if o.get("symbol") == "BTC/USDT"), None)
    assert seeded is not None, "seeded opportunity must surface into Layer-1 context"
    assert seeded["id"] == opp.id, "R0: context must carry the opportunity id (not dropped)"
    assert seeded["scanner_family"] == "momentum"

    from aletheia.agent_shell.trading_decision import TradingDecision, _attach_opportunity_context
    decision = TradingDecision(
        action="trade", symbol="BTC/USDT", direction="long",
        conviction=70, reasoning="momentum breakout",
    )
    _attach_opportunity_context(decision, ctx)
    assert decision.opportunity_id == opp.id, (
        "R0: decision must backfill the source opportunity_id — the hard-coded None "
        "was the single root cause starving every downstream learning artifact"
    )
    assert decision.scanner_family == "momentum"
    # strategy_id is None here (opp has no linked strategy) but the path is wired.
    assert decision.strategy_id == opp.linked_strategy_id


def test_r0_no_matching_opportunity_leaves_attribution_none(tmp_path, monkeypatch):
    """Honest, not fabricated: a pick with no corresponding open opportunity (e.g. it
    came from raw features/signals) leaves opportunity_id/scanner_family None."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_shell.trading_context import build_enriched_trading_context
    from aletheia.agent_shell.trading_decision import TradingDecision, _attach_opportunity_context

    ctx = build_enriched_trading_context()
    decision = TradingDecision(
        action="trade", symbol="ETH/USDT", direction="short",
        conviction=60, reasoning="no backing opportunity",
    )
    _attach_opportunity_context(decision, ctx)
    assert decision.opportunity_id is None
    assert decision.scanner_family is None


def test_r0_direction_mismatch_does_not_misattribute(tmp_path, monkeypatch):
    """A long opportunity must NOT be attributed to a short decision on the same symbol."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import insert_opportunity

    insert_opportunity(Opportunity(
        symbol="SOL/USDT", market="spot", direction_bias="long",
        scanner_family="trend", status="open", asset_class="crypto",
    ))

    from aletheia.agent_shell.trading_context import build_enriched_trading_context
    from aletheia.agent_shell.trading_decision import TradingDecision, _attach_opportunity_context

    ctx = build_enriched_trading_context()
    decision = TradingDecision(
        action="trade", symbol="SOL/USDT", direction="short",
        conviction=65, reasoning="opposite direction",
    )
    _attach_opportunity_context(decision, ctx)
    assert decision.opportunity_id is None, "long opp must not attribute to a short decision"
