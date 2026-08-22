"""Shared admission checks for prediction-market observation."""

from typing import Any


def prediction_observer_blocked_reason(config: Any) -> str | None:
    """Return the stable admission reason, or ``None`` when observation is allowed."""
    market_data = getattr(config, "market_data", None)
    if market_data is None:
        return "prediction_observer_config_unavailable"
    if not bool(getattr(market_data, "prediction_enabled", False)):
        return "prediction_observer_disabled"
    if not getattr(market_data, "observed_prediction_markets", None):
        return "prediction_observer_not_configured"
    return None
