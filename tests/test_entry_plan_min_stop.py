"""Exchange-demo stop floor uses trading.vol_target_pct (existing ATR vol target)."""

from types import SimpleNamespace

import pytest

from aletheia.trading.entry_plan import (
    STOP_DISTANCE_BELOW_VOL_TARGET,
    min_stop_distance_pct,
    stop_distance_below_min_floor,
    stop_distance_pct,
)


def _cfg(vol_target_pct=1.5):
    return SimpleNamespace(trading=SimpleNamespace(vol_target_pct=vol_target_pct))


def test_stop_distance_pct_matches_doge_noise_stop():
    assert stop_distance_pct(0.06998, 0.07008) == pytest.approx(0.1429, rel=1e-3)


def test_doge_noise_stop_is_below_vol_target_floor():
    token = stop_distance_below_min_floor(
        0.06998, 0.07008, execution_mode="bitget_demo", cfg=_cfg()
    )
    assert token is not None
    assert token.startswith(STOP_DISTANCE_BELOW_VOL_TARGET)
    assert "<1.5000" in token


def test_floor_disabled_on_live_and_paper_internal():
    assert min_stop_distance_pct(execution_mode="bitget_live_limited", cfg=_cfg()) is None
    assert (
        stop_distance_below_min_floor(
            100.0, 99.86, execution_mode="paper_internal", cfg=_cfg()
        )
        is None
    )


def test_default_two_percent_stop_clears_demo_floor():
    assert (
        stop_distance_below_min_floor(
            100.0, 98.0, execution_mode="bitget_demo", cfg=_cfg()
        )
        is None
    )


def test_zero_vol_target_disables_floor():
    assert (
        stop_distance_below_min_floor(
            100.0, 99.9, execution_mode="bitget_demo", cfg=_cfg(0.0)
        )
        is None
    )
