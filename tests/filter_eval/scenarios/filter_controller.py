"""
Scenarios for the Filter Feedback Controller (Phase 1, shadow).

The controller maps a per-scanner_family direction-adjusted forward-return
hit-rate to a verdict:

  hit_rate > 0.5 + HOLD_BAND  → loosen   (family's setups work; drop filters too tight)
  hit_rate < 0.5 - HOLD_BAND  → tighten  (setups underperform; filters too loose)
  within band                 → hold
  n < min_samples             → insufficient_data (no recommendation)

HOLD_BAND = 0.10, default min_samples = 20 (see filter_controller.py).

Each scenario supplies a fake `calibration_stats()` return (list of per-family
rows) and asserts the verdict emitted for a representative family.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ControllerScenario:
    name: str
    description: str
    # Rows shaped like repositories.calibration_stats() output.
    calibration_rows: list[dict]
    # family → expected verdict for that family's cells
    expected_verdict_by_family: dict[str, str]
    min_samples: int = 20


def _row(family: str, horizon: str, n: int, win: int) -> dict:
    return {"scanner_family": family, "horizon": horizon, "n": n, "win_count": win,
            "avg_return": 0.0, "min_return": -1.0, "max_return": 1.0}


# Per-family calibration horizon the controller expects (mirrors allocator).
# momentum→4h; everything else→1h.
ALL_SCENARIOS: list[ControllerScenario] = [
    ControllerScenario(
        name="high_hitrate_family_loosens",
        description="momentum 70% hit-rate over 40 settled 4h outcomes → loosen its drop filters.",
        calibration_rows=[_row("momentum", "4h", 40, 28)],  # 0.70
        expected_verdict_by_family={"momentum": "loosen"},
    ),
    ControllerScenario(
        name="low_hitrate_family_tightens",
        description="mean_reversion 30% hit-rate over 40 settled 1h outcomes → tighten.",
        calibration_rows=[_row("mean_reversion", "1h", 40, 12)],  # 0.30
        expected_verdict_by_family={"mean_reversion": "tighten"},
    ),
    ControllerScenario(
        name="balanced_family_holds",
        description="volatility 52% hit-rate over 50 outcomes is within the ±0.10 band → hold.",
        calibration_rows=[_row("volatility", "1h", 50, 26)],  # 0.52
        expected_verdict_by_family={"volatility": "hold"},
    ),
    ControllerScenario(
        name="thin_sample_insufficient",
        description="derivatives strong hit-rate but only 8 outcomes (< gate=20) → insufficient_data.",
        calibration_rows=[_row("derivatives", "1h", 8, 7)],  # 0.875 but n<20
        expected_verdict_by_family={"derivatives": "insufficient_data"},
    ),
    ControllerScenario(
        name="wrong_horizon_ignored",
        description="momentum only has a 1h row, but its calibration horizon is 4h → no family cell.",
        calibration_rows=[_row("momentum", "1h", 40, 28)],
        expected_verdict_by_family={},  # momentum should not appear
    ),
]
