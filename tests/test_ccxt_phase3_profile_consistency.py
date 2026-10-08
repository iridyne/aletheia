"""Phase 3: profile-derived CCXT identity is consistent across all three reads.

For every retained external execution mode, the same (exchange_name, sandbox,
listing_options) must be returned by _listing_view — the single source used by
capability reads, order-constraint reads, and execution.  This table-driven
test is the acceptance check required by #3699 Phase 3 / #3474 DoD.
"""

from __future__ import annotations

import pytest

# (execution_mode, expected_exchange_name, expected_sandbox, expected_extra_subset)
# expected_extra_subset: subset of listing_options that must be present (may have more).
PROFILE_IDENTITY_TABLE = [
    # OKX demo sandbox
    ("okx_demo", "okx", True, {}),
    # Bitget demo — the single UTA v3 profile
    ("bitget_demo", "bitget", True, {"uta": True}),
    # Binance testnet — the regression case: must use "binanceusdm", not "binance"
    ("binance_testnet", "binanceusdm", True, {}),
]


@pytest.fixture(autouse=True)
def _stub_config(monkeypatch):
    """Provide a minimal AletheiaConfig so _profile_spec() can resolve any mode."""
    from aletheia.config.schema import AletheiaConfig
    from extensions.exchanges import composition

    cfg = AletheiaConfig()
    monkeypatch.setattr(composition, "load_config", lambda: cfg)


@pytest.mark.parametrize("execution_mode,expected_name,expected_sandbox,expected_extra_subset",
                         PROFILE_IDENTITY_TABLE)
def test_listing_view_returns_profile_exchange_name(
    execution_mode, expected_name, expected_sandbox, expected_extra_subset,
):
    """_listing_view returns the profile's exact CCXT exchange name, not a coarse venue."""
    from extensions.exchanges.ccxt.order_rules import _listing_view

    sandbox, extra, exchange_name = _listing_view(execution_mode)
    assert exchange_name == expected_name, (
        f"{execution_mode}: expected exchange_name={expected_name!r}, got {exchange_name!r}"
    )
    assert sandbox is expected_sandbox, (
        f"{execution_mode}: expected sandbox={expected_sandbox}, got {sandbox}"
    )
    for key, val in expected_extra_subset.items():
        assert extra.get(key) == val, (
            f"{execution_mode}: expected extra[{key!r}]={val!r}, got {extra.get(key)!r}"
        )


def test_binance_testnet_uses_binanceusdm_not_binance():
    """Regression guard: binance_testnet must not resolve to ccxt.binance."""
    from extensions.exchanges.ccxt.order_rules import _listing_view

    _, _, exchange_name = _listing_view("binance_testnet")
    assert exchange_name == "binanceusdm"
    assert exchange_name != "binance"
