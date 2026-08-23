"""Market regime detection — determines Bull/Bear/Sideways/Volatile state."""

from aletheia.regime.detector import MarketRegime, detect_regime

__all__ = ["detect_regime", "MarketRegime"]
