"""Native reader profile construction, configuration and startup gates.

Private account request-family isolation and close rejection semantics belong to
the current UTA profiles (tests/uta/test_ccxt_adapter.py). Native exact-fill and
plan-history pagination is exercised by test_tpsl and test_bitget_uta_v3_switch,
not by tables belonging to the retired native trading implementation.
"""

import pytest

from extensions.exchanges.bitget.client import BitgetClient


# ─── Construction ────────────────────────────────────────────────────────────


def test_client_has_no_profile_choice():
    c = BitgetClient(mode="demo")
    assert not hasattr(c, "profile")


def test_client_rejects_profile_argument():
    with pytest.raises(TypeError):
        BitgetClient(mode="demo", profile="uta_v3")  # type: ignore[call-arg]


# ─── Config ──────────────────────────────────────────────────────────────────


def test_exchange_config_has_no_bitget_profile():
    from aletheia.config.schema import ExchangeConfig

    assert "bitget_profile" not in ExchangeConfig.model_fields


def test_bitget_modify_request_enriches_instrument_with_venue_quantity_step():
    """#3446: modify must use the same venue size grid as submit. A legal fractional
    quantity accepted by submit must not be rejected by modify as a contract-rounding
    error."""
    import ccxt
    from decimal import Decimal
    from types import SimpleNamespace
    from extensions.exchanges.bitget.profile import BitgetUTAV3Profile
    from aletheia.uta.models import InstrumentIdentity

    profile = BitgetUTAV3Profile(api_key="k", secret="s", passphrase="p")

    # HYPE example from #3439: contractSize=1, but the venue's real size grid is 0.01
    # (exposed as precision.amount by CCXT).
    market = {
        "id": "HYPEUSDT",
        "symbol": "HYPE/USDT:USDT",
        "contractSize": 1,
        "precision": {"amount": 0.01},
        "linear": True,
        "inverse": False,
    }
    exchange = SimpleNamespace(markets={"HYPE/USDT:USDT": market}, precisionMode=ccxt.TICK_SIZE)

    instrument = InstrumentIdentity(
        canonical_symbol="HYPE/USDT",
        venue_symbol="HYPE/USDT:USDT",
        market_type="usdt_futures",
        base_currency="HYPE",
        quote_currency="USDT",
        contract_size=Decimal("1"),
    )
    command = SimpleNamespace(
        target_venue_order_id="123",
        target_client_order_id=None,
        trigger_price=None,
        time_in_force=None,
        quantity=Decimal("2.5"),
        limit_price=None,
        instrument=instrument,
    )
    current = SimpleNamespace(
        instrument=instrument,
        order_type="limit",
        side="buy",
        price=Decimal("1.0"),
    )

    args = profile.modify_request(command, current, exchange=exchange)
    venue_quantity = args[4]

    assert venue_quantity == Decimal("2.5"), "modify_request must accept fractional qty (0.01 step)"


def test_bitget_modify_rejects_off_grid_quantity_when_step_is_known():
    """#3446 regression: off-grid quantities must still be rejected when the step
    is provided, not silently accepted due to the presence of quantity_precision."""
    import ccxt
    from decimal import Decimal
    from types import SimpleNamespace
    from extensions.exchanges.bitget.profile import BitgetUTAV3Profile
    from aletheia.uta.models import InstrumentIdentity
    from aletheia.uta.errors import UTAError

    profile = BitgetUTAV3Profile(api_key="k", secret="s", passphrase="p")

    market = {
        "id": "HYPEUSDT",
        "symbol": "HYPE/USDT:USDT",
        "contractSize": 1,
        "precision": {"amount": 0.01},
        "linear": True,
    }
    exchange = SimpleNamespace(markets={"HYPE/USDT:USDT": market}, precisionMode=ccxt.TICK_SIZE)

    instrument = InstrumentIdentity(
        canonical_symbol="HYPE/USDT",
        venue_symbol="HYPE/USDT:USDT",
        market_type="usdt_futures",
        base_currency="HYPE",
        quote_currency="USDT",
        contract_size=Decimal("1"),
    )
    command = SimpleNamespace(
        target_venue_order_id="123",
        target_client_order_id=None,
        trigger_price=None,
        time_in_force=None,
        quantity=Decimal("2.505"),  # off-grid for 0.01 step
        limit_price=None,
        instrument=instrument,
    )
    current = SimpleNamespace(
        instrument=instrument,
        order_type="limit",
        side="buy",
        price=Decimal("1.0"),
    )

    import pytest
    with pytest.raises(UTAError, match="quantity_not_exact_contract_multiple"):
        profile.modify_request(command, current, exchange=exchange)
