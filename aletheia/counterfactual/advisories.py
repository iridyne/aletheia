"""L5 back-linkage: policy_epoch rotation advisories from counterfactual evidence.

Purpose
-------
The counterfactual module is observe-only (contract invariants CF-I01, CF-I07).
This module produces *advisories* — never actions — indicating that recent
counterfactual evidence contains something an owner may wish to consider when
deciding whether to rotate the policy epoch.

Advisories are:

- **surfaced in reports** and returned from a pure function; they never write
  DB rows, never call ``ensure_skip_remediation_policy_epoch``, and never
  reach any config mutation path;
- **suggestive, not prescriptive** — every advisory is stamped with
  ``observe_only=True`` and the recommendation text says so;
- **schemaful and versioned** — a fixed ``advisory_type`` and ``version``
  allow future consumers to filter safely.

Current signals are deliberately conservative and few. The set is expected to
grow after owner calibration of what "significant" means for each signal
(#497 Phase 3 follow-up).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


ADVISORY_TYPE = "policy_epoch_rotation"
ADVISORY_VERSION = "v1"

_VALID_SEVERITIES = ("info", "concern", "strong_concern")


@dataclass(frozen=True)
class RotationAdvisory:
    """Advisory that the current policy epoch may warrant review for rotation.

    The advisory is a passive read of evidence. Any consumer that treats it as
    authorization to rotate is misusing this module.
    """

    advisory_type: str
    version: str
    produced_at: str
    policy_epoch_id: str | None
    severity: str
    signal: str
    evidence: dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""
    observe_only: bool = True


def build_rotation_advisories(
    *,
    calibration_report: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> list[RotationAdvisory]:
    """Produce zero or more rotation advisories from counterfactual evidence.

    Pure function. Reads the input report(s), emits advisories. No DB write,
    no config mutation, no scheduler side-effect.

    Currently emits:

    - ``calibration_no_positive_band``: the calibration report has bands but
      no trade_score band whose avg_net turned positive over the current
      window. This is one condition an owner might want to review the epoch
      over — a bounded window without a profitable band is compatible with
      either "regime shifted" or "cost model drifted", both of which are
      owner questions.

    Signals expected to be added after owner calibration (thresholds intentionally
    deferred): ``calibration_drift`` (crossover band moved by ≥N against a
    reference epoch), ``harm_signal`` (avg_net dropped ≥N vs a baseline
    window). Both require an owner-defined "N" and reference selection, so
    they are not shipped in the initial skeleton.
    """
    advisories: list[RotationAdvisory] = []
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    )

    if calibration_report and not calibration_report.get("errors"):
        crossover = calibration_report.get("crossover_band")
        bands = calibration_report.get("bands") or []
        if crossover is None and bands:
            advisories.append(
                RotationAdvisory(
                    advisory_type=ADVISORY_TYPE,
                    version=ADVISORY_VERSION,
                    produced_at=stamp,
                    policy_epoch_id=calibration_report.get("policy_epoch_id"),
                    severity="info",
                    signal="calibration_no_positive_band",
                    evidence={
                        "report": "counterfactual.calibration.build_calibration",
                        "window_days": calibration_report.get("window_days"),
                        "total_candidates": calibration_report.get(
                            "total_candidates"
                        ),
                        "bands_summary": [
                            {
                                "band": b.get("band"),
                                "avg_net_pct": b.get("avg_net_pct"),
                                "n": b.get("n"),
                            }
                            for b in bands
                        ],
                    },
                    recommendation=(
                        "No trade_score band in the current window had positive "
                        "average net return. Owner may wish to review whether "
                        "the policy epoch is still calibrated to a profitable "
                        "regime; a new policy epoch is a rotation *option* the "
                        "owner may take after separate review, not an action this "
                        "advisory takes."
                    ),
                    observe_only=True,
                )
            )

    return advisories


def render_advisories(advisories: list[RotationAdvisory]) -> str:
    """Human-readable rendering for CLI / report display."""
    if not advisories:
        return "No policy epoch rotation advisories from current counterfactual evidence."
    lines = [f"Policy epoch rotation advisories ({len(advisories)}):"]
    for adv in advisories:
        lines.append("")
        lines.append(f"  signal:         {adv.signal}")
        lines.append(f"  severity:       {adv.severity}")
        lines.append(f"  policy_epoch:   {adv.policy_epoch_id or 'unknown'}")
        lines.append(f"  produced_at:    {adv.produced_at}")
        lines.append(f"  observe_only:   {adv.observe_only}")
        lines.append(f"  recommendation: {adv.recommendation}")
    return "\n".join(lines)


def advisory_to_dict(advisory: RotationAdvisory) -> dict[str, Any]:
    """JSON-serializable projection for reports."""
    return asdict(advisory)
