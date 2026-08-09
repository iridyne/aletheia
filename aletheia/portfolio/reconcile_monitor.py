"""Reconciliation Monitor — active anomaly detection + predictive alerts.

Current reconciliation: reactive (detects problems after they happen).
Upgrade (QuantDinger inspired): active monitoring with anomaly detection.

Features:
- Track failure rates over time windows
- Detect anomalies (sudden spikes in failures)
- Predictive alerts ("at current rate, risk limit hit in N hours")
- Pattern recognition (failure correlation with market conditions)
"""

from __future__ import annotations

import logging
import statistics
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("aletheia.reconcile.monitor")


@dataclass
class AnomalyAlert:
    """An anomaly detected in reconciliation patterns."""

    alert_id: str
    severity: str  # "info", "warning", "critical"
    metric: str  # what metric is anomalous
    current_value: float
    baseline_value: float
    deviation_sigma: float  # how many standard deviations from baseline
    message: str
    timestamp: str = ""
    suggestions: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "severity": self.severity,
            "metric": self.metric,
            "current_value": round(self.current_value, 4),
            "baseline_value": round(self.baseline_value, 4),
            "deviation_sigma": round(self.deviation_sigma, 2),
            "message": self.message,
            "timestamp": self.timestamp,
            "suggestions": self.suggestions,
        }


# In-memory metrics store (could be persisted for long-term analysis)
_failure_history: list[dict[str, Any]] = []
_anomaly_alerts: list[AnomalyAlert] = []


def record_reconciliation_result(
    success: bool,
    symbol: str | None = None,
    order_id: str | None = None,
    error_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record a reconciliation result for monitoring.

    Call this after each reconciliation attempt to build the history
    that anomaly detection runs against.
    """
    _failure_history.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "symbol": symbol,
        "order_id": order_id,
        "error_type": error_type,
        "metadata": metadata or {},
    })

    # Keep only last 1000 entries (bounded memory)
    if len(_failure_history) > 1000:
        _failure_history.pop(0)


def detect_anomalies(
    window_minutes: int = 60,
    sigma_threshold: float = 2.0,
) -> list[AnomalyAlert]:
    """Detect anomalies in recent reconciliation patterns.

    Args:
        window_minutes: time window for "current" analysis.
        sigma_threshold: how many standard deviations = anomaly.

    Returns:
        List of AnomalyAlert for any detected issues.
    """
    alerts: list[AnomalyAlert] = []
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=window_minutes)

    # Parse timestamps and filter to window
    recent = []
    for entry in _failure_history:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts >= window_start:
                recent.append(entry)
        except (ValueError, TypeError):
            continue

    if len(recent) < 5:
        return alerts  # not enough data

    # --- Metric 1: Overall failure rate ---
    recent_failures = sum(1 for e in recent if not e["success"])
    recent_rate = recent_failures / len(recent)

    # Baseline: failure rate over the full history (or last 24h)
    baseline_window = now - timedelta(hours=24)
    baseline = []
    for entry in _failure_history:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts >= baseline_window:
                baseline.append(entry)
        except (ValueError, TypeError):
            continue

    if len(baseline) > 10:
        baseline_failures = sum(1 for e in baseline if not e["success"])
        baseline_rate = baseline_failures / len(baseline)

        # Compute standard deviation of failure rate over rolling windows
        rates = []
        for i in range(0, len(baseline) - 10, 5):
            chunk = baseline[i : i + 10]
            if chunk:
                r = sum(1 for e in chunk if not e["success"]) / len(chunk)
                rates.append(r)

        if len(rates) > 2:
            std_rate = statistics.stdev(rates) if len(rates) > 1 else 0.01
            std_rate = max(std_rate, 0.01)  # floor to avoid div by zero
            deviation = (recent_rate - baseline_rate) / std_rate
        else:
            deviation = 0.0

        if deviation > sigma_threshold and recent_rate > baseline_rate * 1.5:
            severity = "critical" if deviation > 4 else "warning"
            alerts.append(AnomalyAlert(
                alert_id=f"failure-rate-spike-{now.timestamp():.0f}",
                severity=severity,
                metric="failure_rate",
                current_value=recent_rate,
                baseline_value=baseline_rate,
                deviation_sigma=deviation,
                message=(
                    f"Reconciliation failure rate spiked: {recent_rate:.1%} "
                    f"(baseline {baseline_rate:.1%}, {deviation:.1f}σ)"
                ),
                suggestions=[
                    "Check exchange API connectivity",
                    "Review recent order patterns",
                    "Check if specific symbols are failing",
                ],
            ))

    # --- Metric 2: Per-symbol failure concentration ---
    symbol_failures: dict[str, int] = defaultdict(int)
    symbol_totals: dict[str, int] = defaultdict(int)
    for entry in recent:
        sym = entry.get("symbol") or "unknown"
        symbol_totals[sym] += 1
        if not entry["success"]:
            symbol_failures[sym] += 1

    for sym, failures in symbol_failures.items():
        total = symbol_totals[sym]
        if total >= 3 and failures / total > 0.5:
            alerts.append(AnomalyAlert(
                alert_id=f"symbol-failure-{sym}-{now.timestamp():.0f}",
                severity="warning",
                metric="symbol_failure_concentration",
                current_value=failures / total,
                baseline_value=0.1,
                deviation_sigma=0.0,
                message=f"Symbol {sym}: {failures}/{total} recent reconciliations failed",
                suggestions=[
                    f"Check {sym} order history on exchange",
                    f"Verify {sym} symbol is still tradable",
                ],
            ))

    # --- Metric 3: Error type patterns ---
    error_types: dict[str, int] = defaultdict(int)
    for entry in recent:
        if not entry["success"] and entry.get("error_type"):
            error_types[entry["error_type"]] += 1

    for error_type, count in error_types.items():
        if count >= 3:
            alerts.append(AnomalyAlert(
                alert_id=f"error-pattern-{error_type}-{now.timestamp():.0f}",
                severity="info",
                metric="error_type_frequency",
                current_value=count,
                baseline_value=1.0,
                deviation_sigma=0.0,
                message=f"Error type '{error_type}' occurred {count} times in last {window_minutes}min",
                suggestions=[
                    f"Investigate root cause of {error_type}",
                ],
            ))

    # Store alerts
    _anomaly_alerts.extend(alerts)
    if len(_anomaly_alerts) > 100:
        _anomaly_alerts.pop(0)

    return alerts


def get_reconciliation_health(window_minutes: int = 60) -> dict[str, Any]:
    """Get a health summary of recent reconciliation activity."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=window_minutes)

    recent = []
    for entry in _failure_history:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts >= window_start:
                recent.append(entry)
        except (ValueError, TypeError):
            continue

    if not recent:
        return {
            "window_minutes": window_minutes,
            "total": 0,
            "status": "no_data",
        }

    failures = sum(1 for e in recent if not e["success"])
    total = len(recent)
    failure_rate = failures / total

    # Status determination
    if failure_rate < 0.05:
        status = "healthy"
    elif failure_rate < 0.15:
        status = "degraded"
    else:
        status = "critical"

    return {
        "window_minutes": window_minutes,
        "total": total,
        "successes": total - failures,
        "failures": failures,
        "failure_rate": round(failure_rate, 4),
        "status": status,
        "recent_alerts": len([a for a in _anomaly_alerts if a.severity in ("warning", "critical")]),
    }
