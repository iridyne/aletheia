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
from aletheia.risk.demo_entry_quality import EXCHANGE_DEMO_MODES


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
    assert EXCHANGE_DEMO_MODES is DEMO_EXECUTION_MODES
    assert DEMO_EXECUTION_MODES == {"binance_testnet", "okx_demo", "bitget_demo"}
    assert LIVE_EXECUTION_MODE_SET == {
        "binance_live_limited",
        "okx_live_limited",
        "bitget_live_limited",
    }
    assert EXECUTION_MODE_VENUES["binance_testnet"] == "binance"
    assert EXECUTION_MODE_VENUES["bitget_live_limited"] == "bitget"


def test_entry_permission_uses_registered_capability_identity(monkeypatch):
    from aletheia.execution import venue_capabilities

    assert venue_capabilities.entry_execution_minimum_permission("bitget_demo") == "Full"
    assert venue_capabilities.entry_execution_minimum_permission(" bitget_demo ") == "Full"
    assert venue_capabilities.entry_execution_minimum_permission("okx_demo") is None

    monkeypatch.setitem(venue_capabilities.ENTRY_EXECUTION_MINIMUM_PERMISSION, "okx_demo", "Full")
    assert venue_capabilities.entry_execution_minimum_permission("okx_demo") == "Full"


def test_reconciliation_covers_every_registered_account_through_one_path():
    from aletheia.config.exchanges import execution_mode_venue
    from aletheia.execution.reconciliation import _ACCOUNT_MODES
    from aletheia.uta.manager import UTA_EXECUTION_MODES

    assert set(_ACCOUNT_MODES) == {
        mode for mode in UTA_EXECUTION_MODES if execution_mode_venue(mode) is not None
    }
    assert {"bitget_demo", "okx_demo", "binance_testnet"} <= set(_ACCOUNT_MODES)


@pytest.mark.parametrize("mode", ["bitget_demo", "bitget_live_limited"])
def test_bitget_tpsl_recovery_uses_registered_account_environment(monkeypatch, mode):
    from extensions.exchanges import composition
    from extensions.exchanges.bitget import client
    from tests.specs.uta_private_history import _identity

    calls = []
    monkeypatch.setattr(client, "BitgetClient", lambda **kwargs: calls.append(kwargs))
    identity = _identity(mode)
    reader = composition.create_native_protection_reader(mode, expected_account=identity)
    assert reader.identity == identity
    assert calls == [{"mode": execution_mode_environment(mode)}]
    with pytest.raises(ValueError, match="native_protection_account_unsupported"):
        composition.create_native_protection_reader(
            mode, expected_account=identity.model_copy(update={"environment": "testnet"}),
        )
    assert len(calls) == 1
