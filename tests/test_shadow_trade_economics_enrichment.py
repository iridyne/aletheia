"""shadow_trades resolvers must populate cost-model fields — end-to-end.

Before this fix, resolve_shadow_trade_if_pending only wrote
{exit_price, pnl_pct, resolved_at}. Downstream consumers (reflection.py,
alpha_attribution, and the Learning verdict layer's assess_candidate_outcome
gate) require cost_model_version + net_return_pct to be present, so most
shadow rows were invisible to them.

The behavior covered here:
  1. resolve_shadow_trade_if_pending(economics=None) — legacy minimal write
     (unchanged: 3 fields).
  2. resolve_shadow_trade_if_pending(economics=dict) — enriched write
     populates cost_model_version + gross/net/fee/spread/funding/edge/settlement.
  3. _compute_shadow_economics computes a valid economics dict from a row.
"""

from __future__ import annotations

import uuid

import pytest

from aletheia.storage.repositories import (
    insert_shadow_trade,
    resolve_shadow_trade_if_pending,
)
from aletheia.storage.models import ShadowTrade
from aletheia.storage.sqlite import get_db
from aletheia.analytics.shadow_trades import _compute_shadow_economics


def _fresh_shadow(**overrides) -> ShadowTrade:
    base = dict(
        opportunity_id="opp-" + uuid.uuid4().hex[:8],
        symbol="BTC/USDT:USDT",
        market_type="perp",
        direction="long",
        scanner_family="momentum",
        trade_score=0.5,
        rank=1,
        entry_price=100.0,
        entry_spread_bps=1.5,
        funding_rate_at_entry=0.0001,
        was_selected=True,
    )
    base.update(overrides)
    return ShadowTrade(**base)


def test_minimal_resolve_preserves_legacy_shape():
    """resolve_shadow_trade_if_pending(economics=None) writes exactly 3 fields."""
    shadow = _fresh_shadow()
    insert_shadow_trade(shadow)

    ok = resolve_shadow_trade_if_pending(
        shadow.id,
        exit_price=110.0,
        pnl_pct=10.0,
        resolved_at="2026-07-19T12:00:00+00:00",
    )
    assert ok is True

    row = get_db().execute(
        "SELECT * FROM shadow_trades WHERE id = ?", (shadow.id,)
    ).fetchone()
    assert row is not None
    assert row["pnl_pct"] == 10.0
    assert row["resolved_at"] == "2026-07-19T12:00:00+00:00"
    # Cost fields must remain NULL — legacy contract preserved.
    assert row["cost_model_version"] is None
    assert row["net_pnl_pct"] is None
    assert row["gross_return_pct"] is None
    assert row["estimated_fee_pct"] is None


def test_enriched_resolve_populates_cost_model_fields():
    """resolve_shadow_trade_if_pending(economics=dict) writes cost fields."""
    shadow = _fresh_shadow()
    insert_shadow_trade(shadow)

    economics = _compute_shadow_economics(
        row=shadow.model_dump(),
        exit_price=110.0,
        hold_seconds=4 * 3600.0,
    )
    assert economics is not None, "economics must compute for a valid perp row"
    assert economics["cost_model_version"] == "v1"
    assert economics["gross_return_pct"] == pytest.approx(10.0, rel=1e-6)
    # For a long +10% gross before costs, net must be strictly less.
    assert economics["net_return_pct"] is not None
    assert economics["net_return_pct"] < economics["gross_return_pct"]

    ok = resolve_shadow_trade_if_pending(
        shadow.id,
        exit_price=110.0,
        pnl_pct=10.0,
        resolved_at="2026-07-19T12:00:00+00:00",
        economics=economics,
        settlement_price_source="position_close",
        settlement_lag_sec=4 * 3600.0,
    )
    assert ok is True

    row = get_db().execute(
        "SELECT * FROM shadow_trades WHERE id = ?", (shadow.id,)
    ).fetchone()
    assert row["cost_model_version"] == "v1"
    assert row["net_pnl_pct"] == pytest.approx(economics["net_return_pct"], rel=1e-6)
    assert row["gross_return_pct"] == pytest.approx(10.0, rel=1e-6)
    assert row["estimated_fee_pct"] is not None and row["estimated_fee_pct"] >= 0
    assert row["estimated_spread_slippage_pct"] is not None
    assert row["estimated_funding_pct"] is not None  # perp with funding present
    assert row["min_tradable_edge_pct"] is not None
    assert row["settlement_price_source"] == "position_close"
    assert row["settlement_lag_sec"] == 4 * 3600.0


def test_enriched_resolve_leaves_resolved_row_untouched():
    """The CAS `resolved_at IS NULL` guard still holds when economics are supplied."""
    shadow = _fresh_shadow()
    insert_shadow_trade(shadow)

    resolve_shadow_trade_if_pending(
        shadow.id,
        exit_price=110.0,
        pnl_pct=10.0,
        resolved_at="2026-07-19T12:00:00+00:00",
    )
    # Second call with economics: row is already resolved → no-op.
    economics = _compute_shadow_economics(
        row=shadow.model_dump(),
        exit_price=110.0,
        hold_seconds=4 * 3600.0,
    )
    ok = resolve_shadow_trade_if_pending(
        shadow.id,
        exit_price=999.0,
        pnl_pct=99.0,
        resolved_at="2100-01-01T00:00:00+00:00",
        economics=economics,
    )
    assert ok is False, "resolved_at IS NULL guard must reject the second write"

    row = get_db().execute(
        "SELECT * FROM shadow_trades WHERE id = ?", (shadow.id,)
    ).fetchone()
    assert row["pnl_pct"] == 10.0  # untouched
    assert row["cost_model_version"] is None  # still legacy shape


def test_economics_returns_none_for_missing_entry():
    """Bad rows (no entry_price / direction / etc.) must return None, not raise."""
    assert _compute_shadow_economics(
        row={"entry_price": None, "direction": "long", "market_type": "perp"},
        exit_price=100.0, hold_seconds=3600.0,
    ) is None
    assert _compute_shadow_economics(
        row={"entry_price": 100.0, "direction": None, "market_type": "perp"},
        exit_price=110.0, hold_seconds=3600.0,
    ) is None
    assert _compute_shadow_economics(
        row={"entry_price": -1.0, "direction": "long", "market_type": "perp"},
        exit_price=110.0, hold_seconds=3600.0,
    ) is None


def test_spot_row_has_zero_funding_and_valid_net():
    """Spot rows carry no funding leg; net must still compute."""
    shadow = _fresh_shadow(market_type="spot", funding_rate_at_entry=None)
    insert_shadow_trade(shadow)

    economics = _compute_shadow_economics(
        row=shadow.model_dump(), exit_price=110.0, hold_seconds=4 * 3600.0,
    )
    assert economics is not None
    assert economics["estimated_funding_pct"] == 0.0
    assert economics["net_return_pct"] is not None
