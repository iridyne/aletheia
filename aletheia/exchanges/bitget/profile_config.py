"""Resolve the configured Bitget broker profile.

Single source of truth so every ``BitgetClient`` construction site reads the
same ``exchange.bitget_profile`` config key. Keeps classic_v2 / uta_v3
endpoint families consistent across execution, sync, and read-only paths.
"""

from aletheia.config.loader import load_config
from aletheia.config.schema import KNOWN_BITGET_PROFILES


# Re-exported from the config layer, which owns the validated identity. Kept as
# a name here so `exchanges/bitget/` has one import site rather than many.
_KNOWN_PROFILES = KNOWN_BITGET_PROFILES


def configured_bitget_profile() -> str:
    """Return the active Bitget profile (``"classic_v2"`` | ``"uta_v3"``).

    Falls back to the schema default ``"classic_v2"`` — the config field always
    resolves to a known value via ``ExchangeConfig`` validation, so callers never
    see an unknown profile leak into a constructed client.
    """
    return load_config().exchange.bitget_profile


def known_bitget_profiles() -> frozenset[str]:
    """Every recognized Bitget profile identity."""
    return _KNOWN_PROFILES
