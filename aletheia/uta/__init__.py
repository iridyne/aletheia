"""Complete private external-account contract for Aletheia."""

from aletheia.uta.contract import UTAAdapter, UTA_CONTRACT_VERSION
from aletheia.uta.ccxt_adapter import CCXTUTAAdapter
from aletheia.uta.errors import UTAError, UTAErrorCode
from aletheia.uta.registry import UTAAdapterConfig, UTARegistry

__all__ = [
    "UTAAdapter",
    "UTAAdapterConfig",
    "CCXTUTAAdapter",
    "UTAError",
    "UTAErrorCode",
    "UTARegistry",
    "UTA_CONTRACT_VERSION",
]
