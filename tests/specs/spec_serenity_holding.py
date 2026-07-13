"""Spec: Serenity long-horizon holding (Phase 4 — execution-school discriminator)

Phase 4 of the Serenity execution path (docs/plans/serenity_execution_path.md §5).
Phase 2 stamped DECISION-level attribution via `pi_decisions.signal_source`.
Phase 4 introduces an EXECUTION-level discriminator: `TradeProposal.trade_school`
and `Position.trade_school` (both default 'short_term'). The trade_school controls
position-management behavior:

  - 'short_term' (default): unchanged behavior — 72h max_hold, ATR trailing,
    default stop pct (preserves the entire existing short-term loop)
  - 'serenity': long-horizon — no 72h max_hold (use cfg.serenity.max_hold_days
    instead, default 90d), trailing stop is no-op (intraday ATR signal is meaningless
    for industrial-chain timeframes), wider default stop (cfg.serenity.stop_loss_pct,
    default 8.0%)

This is strictly ADDITIVE: risk_engine + permission gating are untouched (#13 trust
boundary, not modified by Phase 4). The discriminator threads from signal_source on
TradingDecision → trade_school on TradeProposal → trade_school on Position, with
the per-school behavior delta applied only in position_manager._maybe_update_trailing_stop
and _decide_exit.

Contract: serenity_holding (logical contract — registered in the conformance matrix
but NOT in REQUIRED_CONTRACTS, mirroring the Phase 2/3 specs and other behavioral
pipeline contracts).
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "serenity_holding"
_ROOT = Path(__file__).parent.parent.parent
_MODELS = _ROOT / "aletheia" / "storage" / "models.py"
_MIGRATIONS = _ROOT / "aletheia" / "storage" / "migrations.py"
_SCHEMA = _ROOT / "aletheia" / "config" / "schema.py"
_TRADING_DECISION = _ROOT / "aletheia" / "agent_shell" / "trading_decision.py"
_PAPER_TOOLS = _ROOT / "aletheia" / "execution" / "paper_tools.py"
_GATEWAY = _ROOT / "aletheia" / "execution" / "gateway.py"
_PAPER_BROKER = _ROOT / "aletheia" / "execution" / "paper_broker.py"
_BITGET_BROKER = _ROOT / "aletheia" / "execution" / "bitget_broker.py"
_BINANCE_BROKER = _ROOT / "aletheia" / "execution" / "binance_broker.py"
_OKX_BROKER = _ROOT / "aletheia" / "execution" / "okx_broker.py"
_RECONCILER = _ROOT / "aletheia" / "portfolio" / "position_reconciler.py"
_POSITION_MANAGER = _ROOT / "aletheia" / "portfolio" / "position_manager.py"
_SETTINGS_ROUTES = _ROOT / "aletheia" / "server" / "routes" / "settings.py"
_SETTINGS_TSX = _ROOT / "frontend" / "src" / "pages" / "Settings.tsx"


# ---------------------------------------------------------------------------
# L2a — Structural
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SH-S01", _CONTRACT, "§models — trade_school on TradeProposal + Position", "L2a",
        "TradeProposal and Position both expose a trade_school: str field defaulting "
        "to 'short_term' so legacy records never accidentally adopt serenity behavior",
    ),
)
def test_trade_school_field_on_models():
    src = _MODELS.read_text(encoding="utf-8")
    # TradeProposal class block (base-class-agnostic: it may inherit a mixin)
    tp_start = src.index("class TradeProposal(")
    tp_end = src.index("\nclass ", tp_start + 1)
    tp_block = src[tp_start:tp_end]
    assert "trade_school" in tp_block, (
        "TradeProposal is missing trade_school — Phase 4 execution-level "
        "discriminator was never added"
    )
    assert 'trade_school: str = "short_term"' in tp_block or "trade_school: str = 'short_term'" in tp_block, (
        "TradeProposal.trade_school default is not 'short_term' — legacy proposals "
        "would adopt an undefined school"
    )
    # Position class block (base-class-agnostic: it may inherit a mixin)
    pos_start = src.index("class Position(")
    pos_end = src.index("\nclass ", pos_start + 1)
    pos_block = src[pos_start:pos_end]
    assert "trade_school" in pos_block, (
        "Position is missing trade_school — position_manager cannot branch on "
        "school without it"
    )
    assert 'trade_school: str = "short_term"' in pos_block or "trade_school: str = 'short_term'" in pos_block, (
        "Position.trade_school default is not 'short_term' — adopted/legacy "
        "positions would lose the protective default"
    )


@spec_claim(
    SpecClaim(
        "SH-S02", _CONTRACT, "§migrations — additive columns for trade_school", "L2a",
        "_ADDITIVE_COLUMNS registers trade_school on both trade_proposals and positions "
        "tables with NOT NULL DEFAULT 'short_term' so live SQLite files migrate cleanly",
    ),
)
def test_trade_school_migration_registered():
    src = _MIGRATIONS.read_text(encoding="utf-8")
    # Slice the _ADDITIVE_COLUMNS list. Naive `.index("]")` breaks because at least
    # one entry default value contains `'[]'` (serenity_themes.related_assets),
    # which terminates the slice prematurely. The actual list closes with `]` at
    # column 0, so anchor on the literal "\n]\n".
    start = src.index("_ADDITIVE_COLUMNS")
    tail = src[start:]
    end = tail.index("\n]\n")
    block = tail[:end + 2]
    assert '"trade_school", "trade_proposals"' in block, (
        "_ADDITIVE_COLUMNS is missing the trade_proposals.trade_school migration"
    )
    assert '"trade_school", "positions"' in block, (
        "_ADDITIVE_COLUMNS is missing the positions.trade_school migration"
    )
    # The DEFAULT must be 'short_term' (single-quoted inside the SQL DDL string).
    # Both rows must carry it — count matches ensure neither table is left without
    # a sane default.
    default_count = block.count("DEFAULT 'short_term'")
    assert default_count >= 2, (
        f"expected ≥2 'DEFAULT \\'short_term\\'' clauses in _ADDITIVE_COLUMNS, "
        f"got {default_count} — legacy rows would have NULL trade_school"
    )


@spec_claim(
    SpecClaim(
        "SH-S03", _CONTRACT, "§schema — SerenityConfig stop_loss_pct + max_hold_days", "L2a",
        "SerenityConfig exposes stop_loss_pct (default 8.0) and max_hold_days (default 90) "
        "so the long-horizon school has its own protective levels distinct from the "
        "short-term defaults",
    ),
)
def test_serenity_config_has_holding_fields():
    from aletheia.config.schema import SerenityConfig

    cfg = SerenityConfig()
    assert hasattr(cfg, "stop_loss_pct"), (
        "SerenityConfig is missing stop_loss_pct — Phase 4 cannot select a "
        "school-aware stop without it"
    )
    assert hasattr(cfg, "max_hold_days"), (
        "SerenityConfig is missing max_hold_days — _decide_exit cannot relax the "
        "72h cap for long-horizon holdings"
    )
    # Defaults must be sensible (wider than short-term)
    assert 0.1 < float(cfg.stop_loss_pct) < 50.0, (
        f"stop_loss_pct default out of band: {cfg.stop_loss_pct!r}"
    )
    assert 1 <= int(cfg.max_hold_days) <= 365, (
        f"max_hold_days default out of band: {cfg.max_hold_days!r}"
    )
    # Phase 4 picks 8.0 / 90 as the explicit defaults documented in the plan.
    assert float(cfg.stop_loss_pct) == 8.0, (
        f"stop_loss_pct default should be 8.0 (plan §5), got {cfg.stop_loss_pct!r}"
    )
    assert int(cfg.max_hold_days) == 90, (
        f"max_hold_days default should be 90 (plan §5), got {cfg.max_hold_days!r}"
    )


@spec_claim(
    SpecClaim(
        "SH-S04", _CONTRACT, "§trading_decision — derive trade_school from signal_source", "L2a",
        "submit_decision_as_proposal selects trade_school from decision.signal_source "
        "and picks the matching stop pct (cfg.serenity.stop_loss_pct for serenity, else "
        "cfg.trading.default_stop_loss_pct), then threads trade_school into create_paper_proposal",
    ),
)
def test_trading_decision_threads_trade_school():
    src = _TRADING_DECISION.read_text(encoding="utf-8")
    # Slice submit_decision_as_proposal body
    start = src.index("async def submit_decision_as_proposal(")
    tail = src[start:]
    # End at next top-level def or # ── marker
    end_def = tail.find("\nasync def ", 1)
    end_def2 = tail.find("\ndef ", 1)
    end_marker = tail.find("\n# ──", 1)
    candidates = [c for c in (end_def, end_def2, end_marker) if c > 0]
    end = min(candidates) if candidates else len(tail)
    body = tail[:end]
    assert "trade_school" in body, (
        "submit_decision_as_proposal does not reference trade_school — the "
        "execution-school discriminator never reaches the proposal layer"
    )
    assert "signal_source" in body, (
        "submit_decision_as_proposal does not consult decision.signal_source — "
        "the school cannot be derived without it"
    )
    assert "cfg.serenity.stop_loss_pct" in body or "serenity.stop_loss_pct" in body, (
        "submit_decision_as_proposal does not branch stop_pct on the serenity school "
        "— long-horizon holdings would inherit the tight short-term stop"
    )


@spec_claim(
    SpecClaim(
        "SH-S05", _CONTRACT, "§execution layer — trade_school threaded through proposal→position", "L2a",
        "paper_tools.create_paper_proposal + gateway.submit_proposal accept trade_school, "
        "and ALL 4 broker Position(...) constructors + position_reconciler propagate it",
    ),
)
def test_trade_school_threaded_through_execution_layer():
    # paper_tools.create_paper_proposal must accept + forward trade_school
    pt = _PAPER_TOOLS.read_text(encoding="utf-8")
    assert "trade_school" in pt, (
        "paper_tools.create_paper_proposal does not accept trade_school — the "
        "discriminator dies before reaching gateway.submit_proposal"
    )
    # gateway.submit_proposal must accept + thread it into TradeProposal(...)
    gw = _GATEWAY.read_text(encoding="utf-8")
    assert "trade_school" in gw, (
        "gateway does not reference trade_school — TradeProposal is constructed "
        "without the school field"
    )
    # gateway must also thread proposal.trade_school into downstream broker calls
    # so the Position carries it (mirror of opportunity_id=proposal.opportunity_id).
    assert "proposal.trade_school" in gw, (
        "gateway does not pass proposal.trade_school into broker calls — the "
        "school is lost before reaching the Position constructor"
    )
    # All four brokers + reconciler must set Position(..., trade_school=...).
    # The broker-specific propagation rule (memory aletheia-broker-stop-drop-bug.md)
    # says: when a field flows proposal→position, ALL FOUR brokers + reconciler
    # must be updated together — a paper_only fix would silently drop the school
    # on demo/live opens.
    for path in (_PAPER_BROKER, _BITGET_BROKER, _BINANCE_BROKER, _OKX_BROKER):
        src = path.read_text(encoding="utf-8")
        assert "trade_school" in src, (
            f"{path.name} does not propagate trade_school — Position rows opened by "
            f"this broker would silently default to 'short_term' (clone-divergence)"
        )
    rec = _RECONCILER.read_text(encoding="utf-8")
    assert "trade_school" in rec, (
        "position_reconciler does not stamp trade_school on adopted positions — "
        "external exchange positions adopted into management lose the field"
    )


@spec_claim(
    SpecClaim(
        "SH-S06", _CONTRACT, "§position_manager — school-aware exit + trailing-stop gate", "L2a",
        "_maybe_update_trailing_stop early-returns for serenity positions, and "
        "_decide_exit branches max_hold on the trade_school (serenity → cfg.serenity.max_hold_days * 24)",
    ),
)
def test_position_manager_branches_on_trade_school():
    src = _POSITION_MANAGER.read_text(encoding="utf-8")
    # Trailing stop gate
    ts_start = src.index("def _maybe_update_trailing_stop(")
    ts_tail = src[ts_start:]
    ts_end = ts_tail.index("\ndef ", 1)
    ts_body = ts_tail[:ts_end]
    assert "trade_school" in ts_body, (
        "_maybe_update_trailing_stop does not check trade_school — ATR trailing "
        "would fire on long-horizon serenity holdings (intraday noise)"
    )
    assert "serenity" in ts_body, (
        "_maybe_update_trailing_stop does not mention the serenity school — "
        "the gate is absent"
    )
    # Exit decision: must reference both trade_school AND cfg.serenity.max_hold_days
    de_start = src.index("def _decide_exit(")
    de_tail = src[de_start:]
    de_end = de_tail.index("\ndef ", 1)
    de_body = de_tail[:de_end]
    assert "trade_school" in de_body, (
        "_decide_exit does not branch on trade_school — serenity positions would "
        "be killed by the 72h short-term max_hold"
    )
    assert "max_hold_days" in de_body, (
        "_decide_exit does not consult cfg.serenity.max_hold_days — long-horizon "
        "holdings have no upper bound"
    )


@spec_claim(
    SpecClaim(
        "SH-S07", _CONTRACT, "§settings UI — Serenity stop_loss_pct + max_hold_days controls", "L2a",
        "POST /api/settings/serenity exposes stop_loss_pct + max_hold_days fields with "
        "bounds, AND frontend Settings.tsx Serenity section renders controls for both "
        "(config↔UI rule, CLAUDE.md)",
    ),
)
def test_settings_ui_exposes_holding_fields():
    routes_src = _SETTINGS_ROUTES.read_text(encoding="utf-8")
    assert "stop_loss_pct" in routes_src, (
        "settings route does not accept stop_loss_pct — UI cannot persist the "
        "serenity stop pct"
    )
    assert "max_hold_days" in routes_src, (
        "settings route does not accept max_hold_days — UI cannot persist the "
        "long-horizon hold cap"
    )
    # _SERENITY_BOUNDS must cover both fields
    bounds_start = routes_src.index("_SERENITY_BOUNDS")
    bounds_tail = routes_src[bounds_start:]
    bounds_end = bounds_tail.index("}")
    bounds_block = bounds_tail[:bounds_end + 1]
    assert "stop_loss_pct" in bounds_block, (
        "_SERENITY_BOUNDS missing stop_loss_pct — invalid input would write to config"
    )
    assert "max_hold_days" in bounds_block, (
        "_SERENITY_BOUNDS missing max_hold_days — invalid input would write to config"
    )
    # Frontend: Serenity section must render controls for both
    tsx_src = _SETTINGS_TSX.read_text(encoding="utf-8")
    # Anchor on the existing Serenity section title
    sect_start = tsx_src.index('title="Serenity Research"')
    sect_tail = tsx_src[sect_start:]
    sect_end_idx = sect_tail.find("</Section>")
    assert sect_end_idx > 0, "Serenity section in Settings.tsx is malformed"
    sect_block = sect_tail[:sect_end_idx]
    assert "stop_loss_pct" in sect_block, (
        "Settings.tsx Serenity section has no stop_loss_pct control — config↔UI "
        "rule violated (every config field must be reachable from UI)"
    )
    assert "max_hold_days" in sect_block, (
        "Settings.tsx Serenity section has no max_hold_days control — config↔UI "
        "rule violated"
    )


# ---------------------------------------------------------------------------
# L2b — Behavioral (pure-function unit tests on position_manager helpers)
# ---------------------------------------------------------------------------


def _seren_cfg():
    """Realistic in-memory config for the position-manager helpers."""
    from aletheia.config.loader import load_config
    return load_config()


def _make_position(*, trade_school: str, opened_hours_ago: float, direction: str = "long") -> dict:
    """Build a Position-shaped dict for direct _decide_exit / _maybe_update_trailing_stop calls.

    Uses ISO 8601 with explicit UTC offset — matches Position.opened_at format.
    """
    from datetime import datetime, timedelta, timezone
    opened = datetime.now(timezone.utc) - timedelta(hours=opened_hours_ago)
    return {
        "id": f"p-{trade_school}-{int(opened_hours_ago)}",
        "symbol": "NVDA",
        "direction": direction,
        "quantity": 1.0,
        "entry_price": 100.0,
        "current_price": 100.0,
        "stop_price": None,
        "take_profit_price": None,
        "trade_school": trade_school,
        "opened_at": opened.isoformat(),
        "execution_mode": "paper_internal",
    }


@spec_claim(
    SpecClaim(
        "SH-B01", _CONTRACT, "§serenity position not killed by 72h short-term cap", "L2b",
        "A serenity position opened 100h ago does NOT trigger max_hold exit because the "
        "long-horizon cap (cfg.serenity.max_hold_days * 24) is much larger than the "
        "short-term 72h",
    ),
)
def test_decide_exit_serenity_survives_short_term_cap():
    from aletheia.portfolio.position_manager import _decide_exit

    cfg = _seren_cfg()
    # The short-term cap (cfg.trading.max_position_hold_hours, default 72) would
    # fire here; the serenity school must instead use cfg.serenity.max_hold_days.
    pos = _make_position(trade_school="serenity", opened_hours_ago=100.0)
    decision = _decide_exit(pos, price=100.0, cfg=cfg)
    assert decision is None, (
        f"serenity position aged 100h was killed by short-term cap: {decision!r} — "
        f"the school branch in _decide_exit is missing or wrong"
    )


@spec_claim(
    SpecClaim(
        "SH-B02", _CONTRACT, "§serenity trailing stop is a no-op", "L2b",
        "_maybe_update_trailing_stop does NOT write a stop_price for a serenity position "
        "regardless of price action — intraday ATR trailing is meaningless for "
        "long-horizon holdings",
    ),
)
def test_trailing_stop_noop_for_serenity():
    from aletheia.portfolio.position_manager import _maybe_update_trailing_stop

    cfg = _seren_cfg()
    pos = _make_position(trade_school="serenity", opened_hours_ago=10.0)
    # Significant favorable move would normally raise a trailing stop. Capture
    # state before and confirm no in-memory mutation occurs (DB writes would be
    # caught by an autouse fixture; in-memory assertion is the lightweight proof).
    before = dict(pos)
    _maybe_update_trailing_stop(pos, price=120.0, cfg=cfg)
    assert pos.get("stop_price") == before.get("stop_price"), (
        f"serenity position acquired a trailing stop {pos.get('stop_price')!r} — "
        f"the school gate at the top of _maybe_update_trailing_stop is missing"
    )
    assert pos.get("trail_high_water") == before.get("trail_high_water"), (
        "serenity position acquired a trail_high_water marker — the trailing "
        "machinery is still running"
    )


@spec_claim(
    SpecClaim(
        "SH-B03", _CONTRACT, "§short-term regression — 72h cap still fires", "L2b",
        "A short_term position aged past cfg.trading.max_position_hold_hours still "
        "triggers max_hold exit — Phase 4 must not regress the existing short-term loop",
    ),
)
def test_decide_exit_short_term_still_capped():
    from aletheia.portfolio.position_manager import _decide_exit

    cfg = _seren_cfg()
    cap_h = float(cfg.trading.max_position_hold_hours or 72)
    pos = _make_position(trade_school="short_term", opened_hours_ago=cap_h + 2.0)
    decision = _decide_exit(pos, price=100.0, cfg=cfg)
    assert decision is not None, (
        "short_term position aged past the cap was NOT closed — Phase 4 regressed "
        "the existing short-term max_hold guard"
    )
    reason, _ = decision
    assert reason == "max_hold", (
        f"short_term cap fired the wrong reason: {reason!r} (expected 'max_hold')"
    )


# ---------------------------------------------------------------------------
# L2a — Thesis-invalidation exit
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SH-S08", _CONTRACT, "§thesis-invalidation exit for serenity positions", "L2a",
        "position_manager._decide_exit returns ('thesis_invalidated', price) when the "
        "underlying serenity_opportunity status != 'active' — research conclusion "
        "invalidation triggers position close",
    ),
)
def test_decide_exit_thesis_invalidated():
    src = _POSITION_MANAGER.read_text(encoding="utf-8")
    assert "thesis_invalidated" in src, (
        "position_manager has no thesis_invalidated exit — serenity positions stay "
        "open forever even after the research conclusion is dismissed/expired"
    )
    assert "get_serenity_opportunity" in src, (
        "position_manager does not look up the opportunity status — thesis "
        "invalidation check can't work without reading the opp record"
    )
    assert 'trade_school' in src and 'serenity' in src, (
        "thesis-invalidation check is not guarded by trade_school=='serenity' — "
        "it would fire for short_term positions that have no serenity context"
    )


# ---------------------------------------------------------------------------
# SH-S09 — Serenity position close writes PostMortem (feedback loop closure)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SH-S09", _CONTRACT, "§4.2 serenity close → auto PostMortem", "L2a",
        "close_position in pnl.py checks trade_school=='serenity' + opportunity_id "
        "and calls _close_serenity_feedback to write SerenityPostMortem and update "
        "the opportunity status — closing the attribution/learning feedback loop",
    ),
)
def test_close_position_writes_serenity_post_mortem():
    _PNL = _ROOT / "aletheia" / "portfolio" / "pnl.py"
    src = _PNL.read_text(encoding="utf-8")
    assert "_close_serenity_feedback" in src, (
        "pnl.py has no _close_serenity_feedback — serenity positions close without "
        "writing PostMortem, severing the learning feedback loop"
    )
    assert "SerenityPostMortem" in src, (
        "pnl.py never imports/uses SerenityPostMortem — cannot write feedback"
    )
    assert "traded_closed" in src, (
        "pnl.py does not update serenity_opportunities.status to 'traded_closed' — "
        "the opportunity lifecycle never closes after position exit"
    )


# ---------------------------------------------------------------------------
# SH-S10 — trade_attribution resolves serenity opportunity_id (fallback path)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SH-S10", _CONTRACT, "§4.2 trade_attribution serenity fallback", "L2a",
        "trade_attribution._build_row falls back to get_serenity_opportunity when "
        "the standard opportunities join returns None for serenity positions — "
        "preserving the full causal chain for serenity-sourced trades",
    ),
)
def test_trade_attribution_serenity_fallback():
    _ATTR = _ROOT / "aletheia" / "analytics" / "trade_attribution.py"
    src = _ATTR.read_text(encoding="utf-8")
    assert "get_serenity_opportunity" in src, (
        "trade_attribution never calls get_serenity_opportunity — serenity positions "
        "lose their causal chain (scanner_family=None, confidence=None)"
    )
    assert "trade_school" in src, (
        "trade_attribution does not check trade_school — cannot distinguish serenity "
        "positions for the fallback path"
    )
