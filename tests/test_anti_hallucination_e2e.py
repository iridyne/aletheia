"""E2E: anti-hallucination attribution chain (R0) — opportunity → context → decision.

Locks the R0 fix end-to-end (LEARNING_LOOP_AUDIT_2026-06-26 §R0): a seeded open
opportunity must surface into the System Operator's Layer-1 context WITH its id +
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
    from aletheia.storage.models import Opportunity, PiDecision
    from aletheia.storage.repositories import insert_opportunity, insert_pi_decision

    opp = Opportunity(
        symbol="BTC/USDT", market="spot", direction_bias="long",
        scanner_family="momentum", confidence="high", confidence_score=80,
        status="open", asset_class="crypto",
    )
    insert_opportunity(opp)
    insert_pi_decision(PiDecision(
        symbol=opp.symbol,
        direction=opp.direction_bias,
        market_type=opp.market,
        action="signal_pending",
        opportunity_id=opp.id,
        signal_source="market_observer",
    ))

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


def test_ranker_candidate_context_backfills_opportunity_id():
    """A PI pick from ranker_candidates must keep the source opportunity id."""
    from aletheia.agent_shell.trading_decision import TradingDecision, _attach_opportunity_context

    ctx = {
        "layer1": {"opportunities": []},
        "layer2": {
            "ranker_candidates": [
                {
                    "opportunity_id": "opp-ranker-1",
                    "symbol": "UNI/USDT",
                    "direction": "long",
                    "scanner_family": "momentum",
                    "strategy_id": "strat-ranker",
                    "signal_source": "market_observer",
                    "market_type": "usdt_futures",
                }
            ]
        },
        "layer3": {},
    }
    decision = TradingDecision(
        action="skip", symbol="UNI/USDT", direction="long",
        conviction=60, reasoning="ranker candidate looks best",
    )

    _attach_opportunity_context(decision, ctx)

    assert decision.opportunity_id == "opp-ranker-1"
    assert decision.scanner_family == "momentum"
    assert decision.strategy_id == "strat-ranker"
    assert decision.market_type == "usdt_futures"


def test_reviewed_opportunity_roundtrips_through_market_pending_signal(tmp_path, monkeypatch):
    """A consumed opportunity remains attributable through its signal_pending carrier.

    paper_trader intentionally marks the source row reviewed after enqueueing it, so
    Layer 1 and the ranker no longer expose that row.  The queued PiDecision must be
    sufficient for the System Operator to recover the original attribution fields.
    """
    setup_env(tmp_path, monkeypatch)

    from aletheia.storage.models import Opportunity, PiDecision
    from aletheia.storage.repositories import insert_opportunity, insert_pi_decision

    opp = Opportunity(
        symbol="AVAX/USDT",
        market="usdt_futures",
        direction_bias="short",
        scanner_family="short_bias",
        linked_strategy_id="strategy-short-bias",
        metadata='{"learning_candidate_key":"candidate-avax-short"}',
        confidence="high",
        confidence_score=82,
        status="reviewed",
        asset_class="crypto",
    )
    insert_opportunity(opp)
    insert_pi_decision(PiDecision(
        symbol=opp.symbol,
        direction=opp.direction_bias,
        market_type=opp.market,
        action="signal_pending",
        conviction=opp.confidence_score or 0,
        reasoning="queued by paper trader",
        opportunity_id=opp.id,
        signal_source="market_observer",
    ))
    competing = Opportunity(
        symbol=opp.symbol,
        market=opp.market,
        direction_bias=opp.direction_bias,
        scanner_family="momentum",
        confidence="high",
        confidence_score=90,
        status="open",
        asset_class="crypto",
    )
    insert_opportunity(competing)

    from aletheia.agent_shell.trading_context import build_enriched_trading_context
    from aletheia.agent_shell.trading_decision import TradingDecision, _attach_opportunity_context

    ctx = build_enriched_trading_context()
    assert all(row.get("id") != opp.id for row in ctx["layer1"]["opportunities"])
    assert any(row.get("id") == competing.id for row in ctx["layer1"]["opportunities"])
    pending = next(
        row for row in ctx["layer2"]["pending_signals"]
        if row.get("opportunity_id") == opp.id
    )
    assert pending["signal_source"] == "market_observer"

    decision = TradingDecision(
        action="trade",
        symbol=opp.symbol,
        direction=opp.direction_bias,
        conviction=75,
        reasoning="act on the queued short signal",
    )
    _attach_opportunity_context(decision, ctx)

    assert decision.opportunity_id == opp.id
    assert decision.signal_source == "market_observer"
    assert decision.scanner_family == "short_bias"
    assert decision.strategy_id == "strategy-short-bias"
    assert decision.learning_candidate_key == "candidate-avax-short"
    assert decision.market_type == "usdt_futures"


def test_unsupported_close_is_skip_and_does_not_bind_an_open_position():
    """The entry Operator cannot disguise an unimplemented close as a real action."""
    from aletheia.agent_shell.trading_decision import (
        _attach_opportunity_context,
        _parse_decision,
    )

    ctx = {
        "layer1": {
            "positions": [
                {
                    "symbol": "NEAR/USDT",
                    "direction": "short",
                    "opportunity_id": "opp-near-short",
                    "strategy_id": "strat-near",
                    "trade_school": "short_term",
                }
            ],
            "opportunities": [],
        },
        "layer2": {},
        "layer3": {},
    }
    decision = _parse_decision({
        "action": "close",
        "symbol": None,
        "direction": None,
        "conviction": 72,
        "reasoning": "NEAR/USDT short is underwater and thesis failed",
    })

    _attach_opportunity_context(decision, ctx)

    assert decision.action == "skip"
    assert "unsupported_action:close" in decision.reasoning
    assert decision.symbol is None
    assert decision.direction is None
    assert decision.opportunity_id is None
    assert decision.strategy_id is None


def test_entry_decision_does_not_guess_identity_from_open_positions():
    from aletheia.agent_shell.trading_decision import TradingDecision, _attach_opportunity_context

    ctx = {
        "layer1": {
            "positions": [
                {"symbol": "NEAR/USDT", "direction": "short", "opportunity_id": "opp-near"},
                {"symbol": "SOL/USDT", "direction": "long", "opportunity_id": "opp-sol"},
            ],
            "opportunities": [],
        },
        "layer2": {},
        "layer3": {},
    }
    decision = TradingDecision(
        action="trade", symbol=None, direction=None, conviction=70,
        reasoning="No durable pending entry identity was selected",
    )

    _attach_opportunity_context(decision, ctx)

    assert decision.opportunity_id is None
    assert decision.symbol is None


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
    """L-Bug-B: dreaming ps_lessons must reach Layer3 AND the PI prompt.
    Layer3 reads status='applied' (evidence-reviewed lessons). Proposed/accepted
    are earlier pipeline stages and are intentionally excluded from PI context."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.learning.lessons import LessonStore
    store = LessonStore()
    lesson = store.create(
        summary="Avoid chasing late breakouts in low-liquidity alts",
        source_type="post_trade_review", source_id="ptr-1",
        category="entry_timing", severity="warn",
    )
    # Advance lesson to applied status (the only status visible to Layer3)
    store.accept(lesson.lesson_id)
    store.mark_applied(lesson.lesson_id, policy_version_id="test-policy-1")
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


def test_layer2_keeps_unqueued_ranker_candidates_analysis_only(monkeypatch):
    """A locally eligible ranker row is not executable until it has a pending carrier."""
    from aletheia.agent_shell.trading_context import _build_layer2_short_term

    def fake_candidates(limit=30):
        return [
            {
                "opportunity_id": "opp-good",
                "symbol": "GOOD/USDT",
                "direction": "short",
                "market_type": "spot",
                "trade_score": 50,
                "skip_reason": None,
                "scanner_family": "short_bias",
            },
            {
                "opportunity_id": "opp-unsupported",
                "symbol": "BAD1/USDT",
                "direction": "long",
                "trade_score": -999,
                "skip_reason": "okx_demo_symbol_not_supported",
                "scanner_family": "event",
            },
            {
                "opportunity_id": "opp-guard",
                "symbol": "BAD2/USDT",
                "direction": "long",
                "trade_score": 45,
                "skip_reason": "guard_blocked:auto_momentum_long_bad2usdt",
                "scanner_family": "momentum",
            },
            {
                "opportunity_id": "opp-ttl",
                "symbol": "BAD3/USDT",
                "direction": "short",
                "trade_score": 41,
                "skip_reason": "ttl_expired",
                "scanner_family": "mean_reversion",
            },
        ]

    monkeypatch.setattr("aletheia.agents.paper_trader_ranker.build_paper_candidates", fake_candidates)

    layer2 = _build_layer2_short_term()

    assert layer2["executable_candidates"] == []
    assert [c["symbol"] for c in layer2["analysis_only_candidates"]] == ["GOOD/USDT"]
    assert [c["symbol"] for c in layer2["ranker_candidates"]] == ["GOOD/USDT"]
    summary = {row["reason"]: row["count"] for row in layer2["excluded_candidates_summary"]}
    assert summary["unsupported_demo_symbol"] == 1
    assert summary["guard_blocked"] == 1
    assert summary["ttl_expired"] == 1


def test_prompt_prioritizes_executable_candidates_over_excluded_summary():
    """Unsupported symbols may appear in summary, but not in the main executable slots."""
    from aletheia.agent_shell.trading_decision import _build_assessment_prompt

    ctx = {
        "layer1": {},
        "layer2": {
            "executable_candidates": [
                {
                    "opportunity_id": "opp-good",
                    "symbol": "GOOD/USDT",
                    "direction": "short",
                    "trade_score": 50,
                    "scanner_family": "short_bias",
                }
            ],
            "excluded_candidates_summary": [
                {
                    "reason": "unsupported_demo_symbol",
                    "count": 6,
                    "examples": [
                        {"symbol": "BAD1/USDT", "direction": "long"},
                        {"symbol": "BAD2/USDT", "direction": "short"},
                    ],
                },
                {
                    "reason": "guard_blocked",
                    "count": 4,
                    "examples": [{"symbol": "BAD3/USDT", "direction": "long"}],
                },
            ],
        },
        "layer3": {},
    }

    prompt = _build_assessment_prompt(ctx)
    executable_section = prompt.split("## Executable Candidates", 1)[1].split(
        "## Excluded Candidate Summary", 1
    )[0]

    assert "GOOD/USDT short" in executable_section
    assert "BAD1/USDT" not in executable_section
    assert "BAD2/USDT" not in executable_section
    assert "unsupported_demo_symbol: 6" in prompt


def test_layer2_trade_analytics_collected(tmp_path, monkeypatch):
    """Trades/Positions analytics must enter System Operator Layer 2, not only the UI."""
    setup_env(tmp_path, monkeypatch)

    expected = {
        "outcome_status": "available",
        "outcome_semantics_version": "oi-v1",
        "totals": {"trade_count": 4, "win_rate": 50.0, "total_pnl": 12.5, "avg_hold_hours": 5.0},
        "by_source": [{"source_type": "opportunity", "trade_count": 3, "win_rate": 66.7, "total_pnl": 15.0}],
        "by_family": [{"family": "momentum", "trade_count": 2, "win_rate": 50.0, "total_pnl": 3.0}],
        "hold_distribution": [{"bucket": "1-4h", "count": 2, "pct": 50.0}],
    }
    monkeypatch.setattr("aletheia.analytics.trade_stats.build_trade_stats", lambda: expected)

    from aletheia.agent_shell.trading_context import _build_layer2_short_term
    layer2 = _build_layer2_short_term()

    assert layer2["trade_analytics"] == expected


def test_trade_analytics_rendered_into_operator_prompt():
    """System Operator prompt must include Trades Analytics by source/family/hold time."""
    from aletheia.agent_shell.trading_decision import _build_assessment_prompt

    ctx = {
        "layer1": {},
        "layer2": {
            "trade_analytics": {
                "totals": {
                    "trade_count": 4,
                    "win_rate": 50.0,
                    "total_pnl": 12.5,
                    "avg_hold_hours": 5.0,
                    "median_hold_hours": 3.0,
                },
                "by_source": [
                    {"source_type": "opportunity", "trade_count": 3, "win_rate": 66.7, "total_pnl": 15.0},
                ],
                "by_family": [
                    {"family": "momentum", "trade_count": 2, "win_rate": 50.0, "total_pnl": 3.0},
                ],
                "hold_distribution": [
                    {"bucket": "1-4h", "count": 2, "pct": 50.0},
                ],
            },
        },
        "layer3": {},
    }
    prompt = _build_assessment_prompt(ctx)

    assert "Trades Analytics" in prompt
    assert "source opportunity" in prompt
    assert "family momentum" in prompt
    assert "hold 1-4h" in prompt


def test_data_health_signal_win_rate_excludes_unknown_direction(tmp_path, monkeypatch):
    """System Operator data health must not count unknown-direction outcomes as long.

    Historical NULL direction rows are not honest long or short evidence. They
    should not affect the signal win-rate that the operator sees in Layer 1.
    """
    setup_env(tmp_path, monkeypatch)

    from datetime import datetime, timezone
    from aletheia.storage.models import UniverseForwardOutcome
    from aletheia.storage import repositories as repo

    now = datetime.now(timezone.utc).isoformat()

    def _outcome(symbol: str, direction: str | None, ret: float) -> UniverseForwardOutcome:
        directional = ret if direction == "long" else -ret if direction == "short" else None
        return UniverseForwardOutcome(
            member_id=f"m_{symbol}_{direction}_{ret}",
            snapshot_id="snap_data_health",
            symbol=symbol,
            scanner_family="momentum",
            direction=direction,
            horizon="24h",
            entry_price_usd=100.0,
            entry_ts=now,
            due_at=now,
            settled_at=now,
            exit_price_usd=100.0 * (1 + ret / 100.0),
            forward_return_pct=ret,
            gross_return_pct=ret,
            directional_return_pct=directional,
            net_return_pct=(directional - 0.2) if directional is not None else None,
            min_tradable_edge_pct=0.05,
            cost_model_version="v1",
            market_type="spot",
            status="settled",
        )

    repo.insert_forward_outcomes_batch([
        _outcome("AAA/USDT", None, 9.0),
        _outcome("BBB/USDT", None, -9.0),
        _outcome("CCC/USDT", "long", 3.0),
    ])

    from aletheia.agent_shell.trading_context import _get_data_health_summary
    summary = _get_data_health_summary()

    assert summary["signal_win_rate_24h"] == 100.0, (
        f"unknown-direction rows leaked into data-health win rate: {summary}"
    )


def test_b1_ranker_penalty_ignores_post_trade_review(tmp_path, monkeypatch):
    """B1: the ranker's _lesson_penalty must NOT draw penalties from post_trade_review
    (the hallucination source) — it only retrieves trade_case_study now. A high_confidence
    review full of negative keywords for a symbol must produce NO penalty."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_post_trade_review
    # High-confidence review packed with the negative keywords _lesson_penalty matches.
    create_post_trade_review(
        title="Post-Trade Review",
        content=(
            "AVAX/USDT momentum trade: heavy loss, stopped out at drawdown, the entry "
            "failed badly and the position lost value across the whole 4h window. "
            "Execution quality was poor and the thesis failed to play out as expected."
        ),
        confidence="high_confidence",
    )
    from aletheia.knowledge.retrieval import retrieve_lessons
    # Robustness: prove the review IS retrievable (FTS indexed + high_confidence gate
    # passed) — so the 0 penalty below is SPECIFICALLY due to B1's item_type exclusion,
    # not an empty index.
    found = retrieve_lessons("AVAX/USDT long momentum", item_types=["post_trade_review"])
    assert found, "precondition: the seeded post_trade_review must be retrievable via FTS"

    from aletheia.agents.paper_trader_ranker import _lesson_penalty
    # B1: post_trade_review is excluded from the ranker's retrieval, so no penalty leaks.
    penalty = _lesson_penalty("AVAX/USDT", "long", "momentum")
    assert penalty == 0.0, (
        f"post_trade_review must not feed _lesson_penalty (got {penalty}) — B1 removed it "
        f"from retrieve_lessons; only trade_case_study should score the ranker"
    )
