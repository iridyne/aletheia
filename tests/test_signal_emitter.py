"""Tests for #1217 — bias_threshold wired from config to signal_emitter.

Scenarios:
- bias_threshold=0.4 in config: signal with bias=0.35 rejected
- bias_threshold=0.3 (default): signal with bias=0.35 admitted → behavior unchanged
"""
from __future__ import annotations

from aletheia.universe.signal_emitter import _passes_regime_gate, _REGIME_BIAS_THRESHOLD


class _FakeRegime:
    def __init__(self, long_bias: float, short_bias: float = 0.5):
        self.long_bias = long_bias
        self.short_bias = short_bias
        self.state = "bull"


def test_strict_threshold_rejects_sub_threshold_signal():
    """With bias_threshold=0.4, a signal at long_bias=0.35 is rejected."""
    regime = _FakeRegime(long_bias=0.35)
    # score well below override threshold
    result = _passes_regime_gate("long", 0.5, regime, short_enabled=True, bias_threshold=0.4)
    assert result is False


def test_default_threshold_admits_signal():
    """With default threshold (0.30), long_bias=0.35 passes (unchanged behaviour)."""
    regime = _FakeRegime(long_bias=0.35)
    result = _passes_regime_gate("long", 0.5, regime, short_enabled=True, bias_threshold=_REGIME_BIAS_THRESHOLD)
    assert result is True


def test_default_constant_unchanged():
    """Module-level default is still 0.30 to preserve backward compatibility."""
    assert _REGIME_BIAS_THRESHOLD == 0.30


def test_short_disabled_always_rejects():
    """short_enabled=False is a hard gate regardless of threshold."""
    regime = _FakeRegime(long_bias=0.9, short_bias=0.9)
    result = _passes_regime_gate("short", 99.0, regime, short_enabled=False, bias_threshold=0.0)
    assert result is False
