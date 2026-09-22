"""Funding-rate thresholds in the raw fractional units stored by collectors.

Collectors persist ``0.0001`` for ``0.01%``.  Keeping the conversion and named
thresholds here prevents prompts, scanner guards, and signal emission from
silently comparing a raw fraction with a human-readable percent value.
"""

from __future__ import annotations


def percent_to_rate(percent: float) -> float:
    """Convert a human percent value to the raw fractional funding rate."""
    return float(percent) / 100.0


CROWDED_FUNDING_RATE = percent_to_rate(0.01)
ELEVATED_FUNDING_RATE = percent_to_rate(0.005)
MILD_FUNDING_RATE = percent_to_rate(0.002)
