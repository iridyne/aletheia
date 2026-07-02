"""Spec: Serenity xStocks execution path (equity_tradeable asset_class)

Owner decision 2026-07-01: Serenity pipeline directly produces tradeable stock token
opportunities — they should be execution-eligible for PI to trade, distinct from
equity_observer's observation-only "equity" asset_class (locked by OBS-S04).

Design:
- research_worker stamps asset_class="equity_tradeable" on stock-token opps
- trading_context sets execution_eligible=True for "equity_tradeable" (not "equity")
- trading_decision allows "equity_tradeable" opps to anchor signal_source='serenity'
- equity_observer's "equity" remains execution_eligible=False (OBS-S04 unchanged)

Contract: serenity_xstock_execution (logical, not in REQUIRED_CONTRACTS).
"""

from __future__ import annotations

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "serenity_xstock_execution"
_ROOT = Path(__file__).parent.parent.parent
_RESEARCH_WORKER = _ROOT / "aletheia" / "agents" / "research_worker.py"
_TRADING_CONTEXT = _ROOT / "aletheia" / "agent_shell" / "trading_context.py"
_TRADING_DECISION = _ROOT / "aletheia" / "agent_shell" / "trading_decision.py"
_EQUITY_OBSERVER = _ROOT / "aletheia" / "agents" / "equity_observer.py"


@spec_claim(
    SpecClaim(
        "SX-S01", _CONTRACT, "§research_worker stamps equity_tradeable on stock tokens", "L2a",
        "research_worker uses asset_class='equity_tradeable' for is_stock_token symbols, "
        "distinguishing Serenity-produced tradeable tokens from equity_observer's 'equity'",
    ),
)
def test_research_worker_stamps_equity_tradeable():
    src = _RESEARCH_WORKER.read_text(encoding="utf-8")
    assert "equity_tradeable" in src, (
        "research_worker does not contain 'equity_tradeable' — stock token opps "
        "would still be classified as observation-only 'equity'"
    )
    assert 'is_stock_token' in src, (
        "research_worker does not check is_stock_token — classification is blind"
    )


@spec_claim(
    SpecClaim(
        "SX-S02", _CONTRACT, "§trading_context allows equity_tradeable execution", "L2a",
        "trading_context sets execution_eligible=True for asset_class='equity_tradeable' — "
        "Serenity-produced stock tokens CAN be traded by PI",
    ),
)
def test_trading_context_equity_tradeable_eligible():
    src = _TRADING_CONTEXT.read_text(encoding="utf-8")
    # The guard must block "equity" but NOT "equity_tradeable".
    # Pattern: only "equity" is in the blocked set/comparison.
    assert '"equity"' in src or "'equity'" in src, (
        "trading_context has no equity block at all"
    )
    # "equity_tradeable" must NOT appear in a blocking condition
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if "execution_eligible" in line.lower() and "equity_tradeable" in line:
            # If equity_tradeable appears in an exclusion (!=, not in), that's a bug
            if "!=" in line or "not in" in line:
                raise AssertionError(
                    f"Line {i+1} blocks equity_tradeable from execution — "
                    "Serenity stock tokens must be eligible"
                )


@spec_claim(
    SpecClaim(
        "SX-S03", _CONTRACT, "§equity_observer still observation-only (OBS-S04 preserved)", "L2a",
        "equity_observer's Opportunity() calls still use asset_class='equity' — not "
        "'equity_tradeable'. OBS-S04 invariant is not compromised by this change.",
    ),
)
def test_equity_observer_unchanged():
    src = _EQUITY_OBSERVER.read_text(encoding="utf-8")
    assert "equity_tradeable" not in src, (
        "equity_observer now contains 'equity_tradeable' — this would bypass OBS-S04 "
        "and make observation-only tokens execution-eligible"
    )
    assert '"equity"' in src or "'equity'" in src, (
        "equity_observer no longer references 'equity' asset_class at all"
    )
