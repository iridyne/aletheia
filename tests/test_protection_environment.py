"""Protection dispatch follows registered environment, never venue spelling."""

from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("environment", ["demo", "testnet", "live", "local", None])
@pytest.mark.parametrize("enabled", [False, True])
def test_protection_dispatch_uses_registered_environment(monkeypatch, environment, enabled):
    from aletheia.config import exchanges
    from aletheia.config.schema import AletheiaConfig
    from aletheia.execution import gateway, stops

    cfg = AletheiaConfig()
    cfg.trading.exchange_tpsl_enabled = enabled
    monkeypatch.setattr("aletheia.config.loader.load_config", lambda: cfg)
    # Existing registered identity: changing its semantic fact must beat its name.
    monkeypatch.setitem(exchanges.EXECUTION_MODE_ENVIRONMENTS, "bitget_demo", environment)
    position = {"id": "position-1", "execution_mode": "bitget_demo", "stop_price": 95.0}
    monkeypatch.setattr("aletheia.storage.repositories._select_one", lambda *args: position)
    place = Mock(return_value={"sl": "protection-1"})
    replace = Mock(return_value=True)
    monkeypatch.setattr(gateway, "place_exchange_protection", place)
    monkeypatch.setattr(gateway, "replace_exchange_protective_stop", replace)

    allowed = enabled and environment == "demo"
    assert stops.place_exchange_protective_orders(position) == ({"sl": "protection-1"} if allowed else {})
    assert stops.replace_exchange_protective_stop(position["id"], 97.0) is allowed
    if allowed:
        place.assert_called_once_with(position["id"])
        replace.assert_called_once_with(position["id"], 97.0)
    else:
        place.assert_not_called()
        replace.assert_not_called()
