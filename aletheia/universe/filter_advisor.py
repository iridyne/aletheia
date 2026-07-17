"""Filter Advisor — per-family filter spread tolerance and pressure diagnostics.

Computes two complementary signals:

1. Spread tolerance multipliers (from universe_forward_outcomes):
   For each scanner family, measures whether it retains positive net expectancy at
   higher entry spreads. A family that performs well even at elevated spreads can
   afford a looser spread filter threshold.

2. Filter pressure (from filter_candidate_outcomes with known scanner_family):
   For filter stages that run after scanner assignment (risk, PI), measures the
   miss rate — how often do rejected candidates have positive net returns?

These feed into apply_fine_tradability_filters() and the operator system assessment.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("aletheia.universe.filter_advisor")

# Minimum samples before adjusting tolerance away from neutral
_MIN_TOLERANCE_SAMPLES = 30
# Minimum samples for filter pressure summary entries
_MIN_PRESSURE_SAMPLES = 20
# Shrinkage denominator for empirical Bayes (same pattern as allocator)
_TOLERANCE_SHRINKAGE = 30
# Reference net return pct that earns the full ±max_step
_TOLERANCE_REF_NET_PCT = 0.8
# Never boost a family whose worst high-spread outcome was catastrophic
_TOLERANCE_HARM_FLOOR_PCT = -10.0
# Per-family calibration horizon (mirrors allocator._FAMILY_CALIBRATION_HORIZON)
_FAMILY_CALIBRATION_HORIZON = {
    "momentum": "4h",
    "mean_reversion": "1h",
    "volatility": "1h",
    "derivatives": "1h",
}
_DEFAULT_CALIBRATION_HORIZON = "1h"
# Threshold for flagging a family as "high pressure" in the pressure summary
_HIGH_PRESSURE_AVG_NET = 0.5


def _calibration_key(family: str, direction: str | None, market_type: str | None) -> str:
    return f"{family}:{direction or 'unknown'}:{market_type or 'spot'}"


def get_family_spread_tolerances(max_step: float = 0.20) -> dict[str, float]:
    """Compute per-family spread tolerance multipliers.

    A multiplier > 1.0 means the family retains positive net expectancy even at
    higher spreads — the fine tradability filter can use a looser spread threshold
    for candidates from this family.

    Returns {family:direction:market_type: spread_tolerance_multiplier}.
    Empty dict if data is unavailable.
    """
    from aletheia.storage import repositories as repo

    try:
        spread_rows = repo.family_spread_tolerance_stats()
        pressure_rows = repo.filter_pressure_stats()
    except Exception as exc:
        logger.warning("filter_advisor: data fetch failed: %s", exc)
        return {}

    # Index pressure data for secondary signal lookup
    pressure_index: dict[str, dict] = {}
    for row in pressure_rows:
        family = row.get("scanner_family")
        if not family:
            continue
        horizon = row.get("horizon")
        expected_horizon = _FAMILY_CALIBRATION_HORIZON.get(family, _DEFAULT_CALIBRATION_HORIZON)
        if horizon != expected_horizon:
            continue
        key = _calibration_key(family, row.get("direction"), row.get("market_type"))
        if row.get("n", 0) >= _MIN_PRESSURE_SAMPLES:
            pressure_index[key] = row

    tolerances: dict[str, float] = {}
    for row in spread_rows:
        family = row.get("scanner_family")
        if not family:
            continue
        horizon = row.get("horizon")
        expected_horizon = _FAMILY_CALIBRATION_HORIZON.get(family, _DEFAULT_CALIBRATION_HORIZON)
        if horizon != expected_horizon:
            continue

        n_high = row.get("n_high_spread", 0) or 0
        avg_net_high = row.get("avg_net_high_spread")
        if n_high < _MIN_TOLERANCE_SAMPLES or avg_net_high is None:
            continue

        # Shrink toward neutral for small samples
        shrunk = float(avg_net_high) * n_high / (n_high + _TOLERANCE_SHRINKAGE)
        adjustment = shrunk / _TOLERANCE_REF_NET_PCT * max_step
        adjustment = max(-max_step, min(max_step, adjustment))

        # Never boost if worst high-spread outcome was catastrophic
        min_net = row.get("min_net")
        if adjustment > 0 and min_net is not None and float(min_net) < _TOLERANCE_HARM_FLOOR_PCT:
            adjustment = 0.0

        # Secondary signal: if filter_pressure data shows this family's rejected
        # candidates are highly profitable, nudge the tolerance slightly higher
        direction = row.get("direction")
        market_type = row.get("market_type")
        key = _calibration_key(family, direction, market_type)
        pressure = pressure_index.get(key)
        if pressure and adjustment > 0:
            pressure_avg_net = pressure.get("avg_net") or 0
            if pressure_avg_net > _HIGH_PRESSURE_AVG_NET:
                # Add up to half of max_step extra based on pressure signal
                pressure_bonus = min(max_step * 0.5, pressure_avg_net / _TOLERANCE_REF_NET_PCT * max_step * 0.5)
                adjustment = min(max_step, adjustment + pressure_bonus)

        tolerances[key] = 1.0 + adjustment

    if tolerances:
        logger.info("Filter spread tolerances: %s", tolerances)
    return tolerances


def get_family_filter_pressure_summary() -> list[dict]:
    """Return high-pressure family summaries for logging and operator assessment.

    A high-pressure entry indicates a family whose blocked candidates (at risk/PI
    filter stages) show positive net returns — the filter is cutting alpha for
    this family.
    """
    from aletheia.storage import repositories as repo

    try:
        rows = repo.filter_pressure_stats()
    except Exception as exc:
        logger.warning("filter_advisor: pressure data fetch failed: %s", exc)
        return []

    summary = []
    for row in rows:
        family = row.get("scanner_family")
        if not family:
            continue
        horizon = row.get("horizon")
        expected_horizon = _FAMILY_CALIBRATION_HORIZON.get(family, _DEFAULT_CALIBRATION_HORIZON)
        if horizon != expected_horizon:
            continue
        n = row.get("n", 0) or 0
        avg_net = row.get("avg_net") or 0
        if n < _MIN_PRESSURE_SAMPLES or avg_net <= _HIGH_PRESSURE_AVG_NET:
            continue
        summary.append({
            "scanner_family": family,
            "direction": row.get("direction"),
            "market_type": row.get("market_type"),
            "horizon": horizon,
            "avg_net_missed": round(float(avg_net), 3),
            "miss_rate": round(float(row.get("miss_rate") or 0), 3),
            "n": n,
        })
    return summary
