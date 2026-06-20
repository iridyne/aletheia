"""Broker registry — maps broker_id to adapter class, with lazy auto-discovery."""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aletheia.portfolio.uta.broker_protocol import IBroker

logger = logging.getLogger("aletheia.uta.registry")

_BROKER_REGISTRY: dict[str, type] = {}


def register_broker(broker_id: str, adapter_class: type) -> None:
    _BROKER_REGISTRY[broker_id] = adapter_class


def create_broker(broker_id: str) -> "IBroker | None":
    cls = _BROKER_REGISTRY.get(broker_id)
    if cls is None:
        return None
    try:
        return cls()
    except Exception as e:
        logger.debug("create_broker(%s) failed: %s", broker_id, e)
        return None


def create_all_configured() -> "list[IBroker]":
    """Instantiate all brokers whose credentials are configured."""
    _ensure_registered()
    brokers = []
    for broker_id in _BROKER_REGISTRY:
        b = create_broker(broker_id)
        if b is not None:
            brokers.append(b)
    return brokers


def available_brokers() -> list[str]:
    _ensure_registered()
    return list(_BROKER_REGISTRY.keys())


_registered = False


def _ensure_registered() -> None:
    global _registered
    if _registered:
        return
    _registered = True
    try:
        from aletheia.portfolio.uta.bitget_adapter import BitgetDemoAdapter
        register_broker("bitget_demo", BitgetDemoAdapter)
    except Exception:
        pass
    try:
        from aletheia.portfolio.uta.binance_adapter import BinanceTestnetAdapter
        register_broker("binance_testnet", BinanceTestnetAdapter)
    except Exception:
        pass
    try:
        from aletheia.portfolio.uta.okx_adapter import OkxDemoAdapter
        register_broker("okx_demo", OkxDemoAdapter)
    except Exception:
        pass
