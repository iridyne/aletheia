"""Spec: Serenity Decision Attribution (Phase 2 — signal_source discriminator)

Owner decision 2026-06-27 (Option A, PI-central): Serenity research is a
first-class input to the apex PI Agent, NOT an independent executor. When the PI
acts on a Serenity-sourced pick, the decision MUST carry a traceable source so the
attribution chain is complete on the signal side.

Why a discriminator is mandatory: both `opportunities.id` (market_observer) and
`serenity_opportunities.id` are `new_id()` = uuid4().hex[:12] — bare ids with no
type tag and no FK, so an id alone is cross-table ambiguous. A Serenity id stuffed
into `pi_decisions.opportunity_id` without a `signal_source` flag would silently
join against the WRONG table (`opportunities`). The fix is a decision-level
discriminator: `pi_decisions.signal_source ∈ {'market_observer','serenity'}`.

Scope boundary (deliberate): this discriminator lives ONLY on `pi_decisions`
(decision-level attribution). Proposal/position-level source-awareness is Phase 4's
`trade_school` field — a DIFFERENT concern (which school governs risk/exit), landing
in its own phase so `aletheia/risk/` (a change-controlled trust boundary) is not
touched here.

Contract: serenity_attribution (logical contract — registered in the conformance
matrix but intentionally NOT in REQUIRED_CONTRACTS, mirroring the behavioral
pipeline specs and the D2 serenity_evidence_quality contract).
"""

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "serenity_attribution"
_ROOT = Path(__file__).parent.parent.parent
_MODELS = _ROOT / "aletheia" / "storage" / "models.py"
_MIGRATIONS = _ROOT / "aletheia" / "storage" / "migrations.py"
_TRADING_CONTEXT = _ROOT / "aletheia" / "agent_shell" / "trading_context.py"
_TRADING_DECISION = _ROOT / "aletheia" / "agent_shell" / "trading_decision.py"
_REFLECTION = _ROOT / "aletheia" / "agent_shell" / "reflection.py"


# ---------------------------------------------------------------------------
# L2a — Structural
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SA-S01", _CONTRACT, "§3.2 PiDecision carries signal_source", "L2a",
        "PiDecision model has a signal_source field (default 'market_observer') so a "
        "bare opportunity_id can be resolved against the right table",
    ),
)
def test_pi_decision_has_signal_source():
    from aletheia.storage.models import PiDecision

    dec = PiDecision()
    assert hasattr(dec, "signal_source"), "PiDecision has no signal_source field"
    assert dec.signal_source == "market_observer", (
        f"default signal_source should be 'market_observer', got {dec.signal_source!r}"
    )


@spec_claim(
    SpecClaim(
        "SA-S02", _CONTRACT, "§3.2 TradingDecision carries + stamps signal_source", "L2a",
        "TradingDecision dataclass has signal_source and the attribution backfill "
        "stamps it 'serenity' for Serenity-sourced picks",
    ),
)
def test_trading_decision_has_signal_source_and_serenity_branch():
    src = _TRADING_DECISION.read_text(encoding="utf-8")
    assert "signal_source" in src, (
        "trading_decision.py never mentions signal_source — the discriminator is unwired"
    )
    assert '"serenity"' in src, (
        "trading_decision.py never stamps signal_source='serenity' — Serenity picks "
        "would be mis-attributed to market_observer"
    )


@spec_claim(
    SpecClaim(
        "SA-S03", _CONTRACT, "§3.2 additive migration for signal_source", "L2a",
        "migrations._ADDITIVE_COLUMNS adds signal_source to pi_decisions so existing "
        "app.db files gain the column (additive-only, not in CREATE TABLE)",
    ),
)
def test_migration_adds_signal_source_column():
    src = _MIGRATIONS.read_text(encoding="utf-8")
    assert '("signal_source", "pi_decisions"' in src, (
        "no additive ALTER for pi_decisions.signal_source — existing DBs would lack it"
    )


@spec_claim(
    SpecClaim(
        "SA-S04", _CONTRACT, "§3.2 Layer 3 surfaces the Serenity id", "L2a",
        "trading_context.py surfaces the Serenity opportunity id into the PI's Layer 3 "
        "context — the anchor the attribution backfill reads to set opportunity_id",
    ),
)
def test_layer3_surfaces_serenity_id():
    src = _TRADING_CONTEXT.read_text(encoding="utf-8")
    # Region-scoped: layer1's opportunities block ALSO carries "id": o.get("id"),
    # so a whole-file grep would false-positive. Anchor on the Serenity block and
    # require the id within it — otherwise the backfill has nothing to map onto.
    marker = "Serenity research opportunities"
    assert marker in src, "trading_context.py has no Serenity Layer 3 block"
    region = src[src.index(marker):]
    assert '"id": o.get("id")' in region, (
        "trading_context serenity block does not surface the opportunity id — the "
        "PI prompt would describe a pick the decision can never link back to"
    )


# ---------------------------------------------------------------------------
# L2b — Invariants (behavioral, pure attribution function)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SA-S05", _CONTRACT, "§3.2 pending_signals carry opportunity_id for direct attribution", "L2a",
        "trading_context pending_signals query selects opportunity_id + signal_source "
        "columns — the direct attribution path that bypasses layer3 limit=5 truncation",
    ),
)
def test_pending_signals_carry_opportunity_id():
    src = _TRADING_CONTEXT.read_text(encoding="utf-8")
    # Find the Phase 4b block and verify the SQL query includes opportunity_id
    phase4b_idx = src.index("Phase 4b")
    region = src[phase4b_idx:phase4b_idx + 600]
    assert "opportunity_id" in region, (
        "pending_signals query does not SELECT opportunity_id — serenity attribution "
        "relies on layer3 limit=5 which silently truncates promoted opps"
    )
    assert "signal_source" in region, (
        "pending_signals query does not SELECT signal_source — cannot distinguish "
        "serenity signals from market_observer in the direct match path"
    )


@spec_claim(
    SpecClaim(
        "SA-I03", _CONTRACT, "§3.2 pending_signal direct match takes priority over layer3", "L2b",
        "When a serenity pending_signal matches the PI pick by symbol+direction, "
        "_attach_opportunity_context uses its opportunity_id directly — not the "
        "layer3 serenity_opportunities (which may be limit-truncated)",
    ),
)
def test_pending_signal_direct_match_bypasses_layer3():
    from aletheia.agent_shell.trading_decision import (
        TradingDecision,
        _attach_opportunity_context,
    )

    decision = TradingDecision(
        action="trade", symbol="BTC/USDT", direction="long", conviction=70, reasoning="x"
    )
    ctx = {
        "layer1": {"opportunities": []},
        "layer2": {
            "pending_signals": [
                {"symbol": "BTC/USDT", "direction": "long",
                 "opportunity_id": "ser-direct-111", "signal_source": "serenity",
                 "reasoning": "auto-promote", "at": "2026-07-01T12:00:00"}
            ]
        },
        "layer3": {
            "serenity_opportunities": []  # empty — simulates limit=5 truncation
        },
    }
    _attach_opportunity_context(decision, ctx)
    assert decision.opportunity_id == "ser-direct-111", (
        f"pending_signal direct match failed: {decision.opportunity_id!r}"
    )
    assert decision.signal_source == "serenity", (
        f"signal_source should be 'serenity', got {decision.signal_source!r}"
    )


@spec_claim(
    SpecClaim(
        "SA-I01", _CONTRACT, "§3.2 Serenity pick → traceable source", "L2b",
        "When a PI pick maps ONLY to a Serenity opportunity, the backfill sets "
        "opportunity_id = serenity id AND signal_source = 'serenity' (no silent None)",
    ),
)
def test_serenity_pick_backfills_id_and_source():
    from aletheia.agent_shell.trading_decision import (
        TradingDecision,
        _attach_opportunity_context,
    )

    decision = TradingDecision(
        action="trade", symbol="NVDA", direction="long", conviction=70, reasoning="x"
    )
    ctx = {
        "layer1": {"opportunities": []},  # no market_observer match
        "layer3": {
            "serenity_opportunities": [
                {"id": "ser-aaa111", "symbol": "NVDA", "direction": "long"}
            ]
        },
    }
    _attach_opportunity_context(decision, ctx)
    assert decision.opportunity_id == "ser-aaa111", (
        f"serenity id not backfilled: {decision.opportunity_id!r}"
    )
    assert decision.signal_source == "serenity", (
        f"signal_source should be 'serenity', got {decision.signal_source!r}"
    )


@spec_claim(
    SpecClaim(
        "SA-I02", _CONTRACT, "§3.2 market_observer takes precedence", "L2b",
        "When a pick matches BOTH a market_observer and a Serenity opportunity, the "
        "market_observer source wins (signal_source stays 'market_observer')",
    ),
)
def test_market_observer_precedence_over_serenity():
    from aletheia.agent_shell.trading_decision import (
        TradingDecision,
        _attach_opportunity_context,
    )

    decision = TradingDecision(
        action="trade", symbol="NVDA", direction="long", conviction=70, reasoning="x"
    )
    ctx = {
        "layer1": {
            "opportunities": [
                {"id": "mkt-bbb222", "symbol": "NVDA", "direction_bias": "long",
                 "linked_strategy_id": "strat-1", "scanner_family": "momentum"}
            ]
        },
        "layer3": {
            "serenity_opportunities": [
                {"id": "ser-aaa111", "symbol": "NVDA", "direction": "long"}
            ]
        },
    }
    _attach_opportunity_context(decision, ctx)
    assert decision.opportunity_id == "mkt-bbb222", (
        f"market opportunity should win, got {decision.opportunity_id!r}"
    )
    assert decision.signal_source == "market_observer", (
        f"signal_source should stay 'market_observer', got {decision.signal_source!r}"
    )
    assert decision.scanner_family == "momentum"  # market enrichment intact


# ---------------------------------------------------------------------------
# L2c — Invariant (signal_source survives the persistence round-trip)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SA-C01", _CONTRACT, "§3.2 signal_source persisted on pi_decisions", "L2c",
        "record_decision threads signal_source through to the persisted pi_decisions "
        "row — a Serenity-sourced decision reads back with signal_source='serenity'",
    ),
)
def test_signal_source_round_trips_through_record_decision():
    from aletheia.agent_shell.reflection import record_decision
    from aletheia.storage.repositories import list_pi_decisions

    record_decision(
        {
            "action": "trade",
            "symbol": "NVDA",
            "direction": "long",
            "conviction": 70,
            "reasoning": "serenity thesis",
            "proposal_id": None,
            "opportunity_id": "ser-aaa111",
            "signal_source": "serenity",
            "debate_ran": False,
        }
    )
    rows = list_pi_decisions(limit=5)
    match = [r for r in rows if r.get("opportunity_id") == "ser-aaa111"]
    assert match, "pi_decisions row was not persisted"
    assert match[0].get("signal_source") == "serenity", (
        f"signal_source not persisted: {match[0].get('signal_source')!r}"
    )
