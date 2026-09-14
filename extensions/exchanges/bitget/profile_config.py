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


def profile_accounts(field: str, value: str) -> tuple[str, ...]:
    """The two account identities share the selected Bitget endpoint family."""
    if field != "bitget_profile" or value not in KNOWN_BITGET_PROFILES:
        raise ValueError("bitget_profile must be one of: classic_v2, uta_v3")
    return ("bitget_demo", "bitget_live_limited")


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


def configuration_diagnostic(cfg) -> tuple[str, bool, str]:
    """Validate bitget_profile is a known value and warn on Experimental uta_v3."""

    try:
        mode = cfg.exchange.paper_execution_mode or ""
        profile = (getattr(cfg.exchange, "bitget_profile", None) or "classic_v2").strip()
    except Exception as e:
        return "bitget_profile", False, f"bitget_profile check failed: {type(e).__name__}"
    known = KNOWN_BITGET_PROFILES
    if profile not in known:
        return "bitget_profile", False, f"bitget_profile={profile!r} is not a known profile {sorted(known)}"
    if "bitget" not in mode:
        return "bitget_profile", True, f"bitget_profile={profile!r} (bitget not active)"
    if profile == "uta_v3":
        return "bitget_profile", True, "bitget_profile=uta_v3 (Experimental — Demo qualification required before live use)"
    return "bitget_profile", True, f"bitget_profile={profile!r} (classic_v2, active)"
