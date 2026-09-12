"""Execution-mode identity is classified by one registry, never suffix guessing."""

import pytest

from aletheia.config.exchanges import (
    DEMO_EXECUTION_MODES,
    EXECUTION_MODE_ENVIRONMENTS,
    EXECUTION_MODE_VENUES,
    LIVE_EXECUTION_MODE_SET,
    execution_mode_environment,
    execution_mode_venue,
    is_live_execution_mode,
)


@pytest.mark.parametrize(
    ("mode", "environment"),
    [
        ("paper_internal", "local"),
        ("binance_testnet", "testnet"),
        ("okx_demo", "demo"),
        ("bitget_demo", "demo"),
        ("binance_live_limited", "live"),
        ("okx_live_limited", "live"),
        ("bitget_live_limited", "live"),
    ],
)
def test_registered_execution_mode_environment(mode, environment):
    assert execution_mode_environment(mode) == environment
    assert mode in EXECUTION_MODE_ENVIRONMENTS
    assert is_live_execution_mode(mode) is (environment == "live")


def test_unknown_execution_mode_is_unavailable():
    assert execution_mode_environment("venue_live_limited_typo") is None
    assert execution_mode_venue("venue_live_limited_typo") is None
    assert not is_live_execution_mode("venue_live_limited_typo")
    assert DEMO_EXECUTION_MODES == {"binance_testnet", "okx_demo", "bitget_demo"}
    assert LIVE_EXECUTION_MODE_SET == {
        "binance_live_limited",
        "okx_live_limited",
        "bitget_live_limited",
    }
    assert EXECUTION_MODE_VENUES["binance_testnet"] == "binance"
    assert EXECUTION_MODE_VENUES["bitget_live_limited"] == "bitget"
