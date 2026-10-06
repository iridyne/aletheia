"""The derived minimum stop distance (#3777) is a single cost-derived authority.

Replaces the fixed `trading.vol_target_pct` floor, its published-sideways
doubling and the removed #3743 ATR draft policy. The floor's only two consumers
are the Compiler (which widens a stop up to it) and Risk (which rejects at
execution when a Plan's stop no longer clears it).
"""

from types import SimpleNamespace

import pytest

from aletheia.trading.entry_plan import (
    STOP_DISTANCE_BELOW_COST_FLOOR,
    min_stop_distance_pct,
    stop_distance_below_min_floor,
    stop_distance_pct,
)


def _cfg(fee=0.06, slippage_bps=5.0, cost_share: float | None = 0.10):
    return SimpleNamespace(trading=SimpleNamespace(
        execution_fee_pct=fee,
        slippage_bps=slippage_bps,
        entry_stop_cost_share=cost_share,
    ))


def test_stop_distance_pct_matches_doge_noise_stop():
    assert stop_distance_pct(0.06998, 0.07008) == pytest.approx(0.1429, rel=1e-3)


def test_floor_is_round_trip_cost_over_the_owner_share():
    # (2 x 0.06 + 2 x 5/100) / 0.10 == (0.12 + 0.10) / 0.10 == 2.2%
    assert min_stop_distance_pct(cfg=_cfg()) == pytest.approx(2.2)


def test_a_narrower_owner_share_widens_the_floor():
    assert min_stop_distance_pct(cfg=_cfg(cost_share=0.05)) == pytest.approx(4.4)


def test_measured_slippage_widens_the_floor():
    """The floor has exactly one cost authority: raising slippage_bps to the
    measured stop-exit slippage (mean 23 bps) raises it from 2.2% to 5.8%."""
    assert min_stop_distance_pct(cfg=_cfg(slippage_bps=23.0)) == pytest.approx(5.8)


def test_doge_noise_stop_is_below_the_cost_floor():
    token = stop_distance_below_min_floor(0.06998, 0.07008, cfg=_cfg())
    assert token is not None
    assert token.startswith(STOP_DISTANCE_BELOW_COST_FLOOR)
    assert "<2.2000" in token


def test_floor_is_not_demo_only():
    """The floor used to be Demo-scoped; it now has no execution-mode input at
    all, so an identical proposal is judged identically in every mode."""
    assert min_stop_distance_pct(cfg=_cfg()) is not None


def test_floor_never_loosens_without_a_usable_share():
    for broken in (
        _cfg(cost_share=0.0),
        _cfg(cost_share=-0.1),
        _cfg(cost_share=None),
        _cfg(cost_share=float("nan")),
        _cfg(fee=float("inf")),
        _cfg(slippage_bps=float("nan")),
    ):
        assert min_stop_distance_pct(cfg=broken) is None
    # ...and an unusable floor is not a violation, so nothing is rejected on it.
    assert stop_distance_below_min_floor(100.0, 99.99, cfg=_cfg(cost_share=0.0)) is None
    assert stop_distance_below_min_floor(100.0, 99.99, cfg=SimpleNamespace()) is None


def test_a_zero_fee_is_a_real_configuration_not_a_missing_one():
    """`fee=0` (maker rebate) still yields a floor from the slippage leg, because
    the alternative — treating 0 as unmeasured — would silently disable the floor
    on a venue that genuinely charges nothing."""
    assert min_stop_distance_pct(cfg=_cfg(fee=0.0)) == pytest.approx(1.0)


def test_a_stop_at_or_beyond_the_floor_is_accepted():
    assert stop_distance_below_min_floor(100.0, 97.8, cfg=_cfg()) is None
    assert stop_distance_below_min_floor(100.0, 97.0, cfg=_cfg()) is None
