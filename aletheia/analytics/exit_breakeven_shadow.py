"""Read-only reporting of archived Paper Internal exit-shadow outcomes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np

from aletheia.storage import repositories as repo


EXPERIMENT_VERSION = "exit-breakeven-shadow/v1"
TREATMENT_ID = "entry-lock-after-1.5pct/v1"
COST_MODEL_VERSION = "paper-fill-close-bar-v1"
_REPORT_BOOTSTRAP_SEED = 20260810
_REPORT_BOOTSTRAP_REPLICATES = 20_000
_REPORT_BOOTSTRAP_MAX_DRAWS = 2_000_000


def parse_utc_strict(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def build_exit_breakeven_shadow_report(
    *,
    closed_after: str | None = None,
) -> dict[str, Any]:
    """Return descriptive evidence only; no promotion or parameter mutation."""

    cutoff = None
    if closed_after is not None:
        cutoff = parse_utc_strict(closed_after)
        if cutoff is None:
            raise ValueError("closed_after must be an ISO-8601 timestamp with timezone")
    rows = repo.list_exit_breakeven_shadow_report_rows(
        closed_after=cutoff.isoformat() if cutoff is not None else None
    )
    rows.sort(key=lambda row: str(row["position_id"]))
    available = [
        row for row in rows
        if row["status"] in {"unchanged", "counterfactual_close"}
    ]
    unavailable = [row for row in rows if row["status"] == "unavailable"]
    unsettled = [row for row in rows if row["status"] is None]
    changed = [row for row in available if bool(row["treatment_changed"])]
    deltas = [float(row["incremental_net_pnl"]) for row in available]
    mean_ci_95 = None
    bootstrap_replicates = 0
    if len(deltas) >= 2:
        rng = np.random.default_rng(_REPORT_BOOTSTRAP_SEED)
        values = np.asarray(deltas, dtype=np.float64)
        size = len(deltas)
        bootstrap_replicates = min(
            _REPORT_BOOTSTRAP_REPLICATES,
            _REPORT_BOOTSTRAP_MAX_DRAWS // size,
        )
        # Keep each temporary index matrix bounded while NumPy performs the
        # expensive resampling outside the Python interpreter.
        chunk_replicates = max(1, min(bootstrap_replicates, 100_000 // size))
        means: list[float] = []
        for offset in range(0, bootstrap_replicates, chunk_replicates):
            batch_size = min(chunk_replicates, bootstrap_replicates - offset)
            indices = rng.integers(0, size, size=(batch_size, size))
            means.extend(values[indices].mean(axis=1).tolist())
        means.sort()
        last = len(means) - 1
        mean_ci_95 = [means[int(0.025 * last)], means[int(0.975 * last)]]
    by_close_reason = []
    for close_reason in sorted({str(row.get("close_reason") or "unknown") for row in rows}):
        reason_rows = [
            row for row in rows
            if str(row.get("close_reason") or "unknown") == close_reason
        ]
        reason_available = [
            row for row in reason_rows
            if row["status"] in {"unchanged", "counterfactual_close"}
        ]
        reason_deltas = [float(row["incremental_net_pnl"]) for row in reason_available]
        by_close_reason.append({
            "close_reason": close_reason,
            "outcome_count": len(reason_rows),
            "available_count": len(reason_available),
            "changed_count": sum(
                1 for row in reason_available if bool(row["treatment_changed"])
            ),
            "incremental_net_pnl_total": sum(reason_deltas) if reason_deltas else None,
            "incremental_net_pnl_mean": (
                sum(reason_deltas) / len(reason_deltas) if reason_deltas else None
            ),
        })
    return {
        "experiment_version": EXPERIMENT_VERSION,
        "treatment_id": TREATMENT_ID,
        "cost_model_version": COST_MODEL_VERSION,
        "behavior_authority": False,
        "outcome_count": len(rows),
        "available_count": len(available),
        "unavailable_count": len(unavailable),
        "unsettled_count": len(unsettled),
        "outcome_coverage": len(available) / len(rows) if rows else None,
        "changed_count": len(changed),
        "incremental_net_pnl_total": sum(deltas) if deltas else None,
        "incremental_net_pnl_mean": (sum(deltas) / len(deltas)) if deltas else None,
        "incremental_net_pnl_mean_ci_95": mean_ci_95,
        "bootstrap_method": "position_percentile_bootstrap/v1",
        "bootstrap_seed": _REPORT_BOOTSTRAP_SEED,
        "bootstrap_replicates": bootstrap_replicates,
        "bootstrap_max_draws": _REPORT_BOOTSTRAP_MAX_DRAWS,
        "positive_increment_count": sum(1 for value in deltas if value > 0),
        "close_reason_breakdown": by_close_reason,
        "close_reason_attribution_available": any(
            item["close_reason"] != "unknown" for item in by_close_reason
        ),
        "promotion_eligible": False,
    }
