"""
Placeholder: scenarios for the execution_eligible live-price gate.

The guard is implemented in:
  aletheia/universe/allocator.py  (line ~300)
  aletheia/universe/signal_emitter.py (line ~155)

It sets execution_eligible=False when market_snapshots has no row for the symbol
within the last 2 hours.

TODO: implement parametrized scenarios similar to direction_brake.py once
list_market_snapshots has a clean injectable interface.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Literal


@dataclass
class SnapshotRecord:
    symbol: str
    snapshot_at: str   # ISO-8601 UTC
    price: float = 1.0


@dataclass
class EligibilityScenario:
    name: str
    description: str
    snapshots: list[SnapshotRecord]
    symbol: str
    expected_eligible: bool


ALL_SCENARIOS: list[EligibilityScenario] = [
    # These are design-level stubs. Implement when the repository layer is mockable.
    EligibilityScenario(
        name="no_snapshot_ever",
        description="Symbol has never appeared in market_snapshots — should be ineligible",
        snapshots=[],
        symbol="NATGAS/USDT",
        expected_eligible=False,
    ),
    EligibilityScenario(
        name="stale_snapshot",
        description="Last snapshot is 3h ago — outside 2h freshness window, ineligible",
        snapshots=[SnapshotRecord("BTC/USDT", "2026-07-09T10:00:00+00:00", 60000.0)],
        symbol="BTC/USDT",
        expected_eligible=False,
    ),
    EligibilityScenario(
        name="fresh_snapshot",
        description="Snapshot within last 2h — eligible",
        snapshots=[SnapshotRecord("BTC/USDT", "2026-07-09T13:00:00+00:00", 62000.0)],
        symbol="BTC/USDT",
        expected_eligible=True,
    ),
]
