"""UTA (Unified Trading Account) — multi-broker read aggregation layer."""

from aletheia.portfolio.uta.broker_protocol import (
    AccountInfo,
    BrokerPosition,
    IBroker,
)
from aletheia.portfolio.uta.registry import (
    available_brokers,
    create_broker,
    register_broker,
)
from aletheia.portfolio.uta.manager import get_unified_state, get_unified_state_sync, UnifiedState

__all__ = [
    "AccountInfo",
    "BrokerPosition",
    "IBroker",
    "UnifiedState",
    "available_brokers",
    "create_broker",
    "get_unified_state",
    "get_unified_state_sync",
    "register_broker",
]
