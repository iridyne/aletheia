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


def test_layer3_lessons_from_trade_case_study(tmp_path, monkeypatch):
    """L-Bug-A: Layer3 lessons must surface real trade_case_study items — the old dead
    filter ('lesson'/'accepted_lesson'/'trading_guard') matched 0 DB rows so lessons
    was permanently empty."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_trade_case_study
    create_trade_case_study(
        title="Case: BTC/USDT long (take_profit)",
        # >80 chars to clear the create_* quality gate (KL-1) — short bodies are dropped.
        content=(
            "Entry on momentum confluence (RSI 62, vol 2.1x, taker 1.3); exited +2.1% at "
            "take_profit after 4h hold. Pattern: confluence breakouts in a risk_on regime "
            "are worth repeating; avoid when funding is crowded."
        ),
        metadata={"symbol": "BTC/USDT"},
    )
    from aletheia.agent_shell.trading_context import _build_layer3_wisdom
    layer3 = _build_layer3_wisdom()
    assert layer3["lessons"], "Layer3 lessons must be non-empty once a trade_case_study exists"
    assert any(l["type"] == "trade_case_study" for l in layer3["lessons"])


def test_layer3_guards_from_guard_candidate(tmp_path, monkeypatch):
    """L-Bug-A': Layer3 guards must surface guard_candidate items — the old limit=30
    full-table scan got flooded by high-frequency system_action rows."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_guard_candidate
    create_guard_candidate(
        title="Auto guard candidate: ZEC/USDT volatility 4h loss",
        content="scanner_family=volatility picked ZEC short, 4h -3.2%. Candidate guard.",
        metadata={"symbol": "ZEC/USDT"},
    )
    from aletheia.agent_shell.trading_context import _build_layer3_wisdom
    layer3 = _build_layer3_wisdom()
    assert layer3["guard_candidates"], "Layer3 guards must surface guard_candidate items"


def test_ps_lessons_surface_to_pi_prompt(tmp_path, monkeypatch):
    """L-Bug-B: dreaming ps_lessons must reach Layer3 AND the PI prompt — 184+ proposed
    lessons previously had zero consumer path."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.learning.lessons import LessonStore
    LessonStore().create(
        summary="Avoid chasing late breakouts in low-liquidity alts",
        source_type="post_trade_review", source_id="ptr-1",
        category="entry_timing", severity="warn",
    )
    from aletheia.agent_shell.trading_context import _build_layer3_wisdom
    layer3 = _build_layer3_wisdom()
    assert layer3["ps_lessons"], "ps_lessons must surface into Layer3"

    from aletheia.agent_shell.trading_decision import _build_assessment_prompt
    prompt = _build_assessment_prompt({"layer1": {}, "layer2": {}, "layer3": layer3})
    assert "Recent Post-trade Lessons" in prompt
    assert "low-liquidity alts" in prompt


def test_a10_layer2_signals_injected_into_prompt():
    """A10: calibration / trading_memo / reasoning_feedback (computed in layer2) must be
    rendered into the PI prompt — they were silently dropped before this fix."""
    from aletheia.agent_shell.trading_decision import _build_assessment_prompt
    ctx = {
        "layer1": {},
        "layer2": {
            "trading_memo": {"rendered": "MEMO: short bias in risk_off regimes"},
            "calibration_feedback": "CAL: conviction 70-80 buckets → actual 45% win",
            "reasoning_feedback": "RF: momentum scanner 58% win over 30 trades",
        },
        "layer3": {},
    }
    prompt = _build_assessment_prompt(ctx)
    assert "MEMO: short bias" in prompt
    assert "CAL: conviction" in prompt
    assert "RF: momentum scanner" in prompt
