"""L5 back-linkage: policy_epoch rotation advisories from counterfactual evidence.

Purpose
-------
The counterfactual module is observe-only (contract invariants CF-I01, CF-I07).
This module produces *advisories* — never actions — indicating that recent
counterfactual evidence contains something an owner may wish to consider when
deciding whether to rotate the policy epoch.

Advisories are:

- **surfaced in reports** and returned from a pure function; they never write
  DB rows, never call the mutation entry points for policy epochs, and never
  reach any config mutation path;
- **suggestive, not prescriptive** — every advisory is stamped with
  ``observe_only=True`` and the recommendation text says so;
- **schemaful and versioned** — a fixed ``advisory_type`` and ``version``
  allow future consumers to filter safely.

Signals shipped
---------------
- ``calibration_no_positive_band`` (info) — the calibration report has bands
  but no crossover band. The bounded window carries no profitable expectancy;
  either regime shifted or the cost model drifted, both worth owner review.
- ``harm_signal`` (concern / strong_concern) — the current window's avg_net
  dropped materially versus the *same* policy_epoch's opening window. Uses
  epoch-scoped self-comparison to stay inside the CF-I04 partition and
  refuses to fire until the epoch is old enough for the two windows to be
  disjoint.

Deferred (needs owner input on threshold semantics)
---------------------------------------------------
- ``calibration_drift`` (crossover band moved ≥N against a reference epoch).
  See #497 Phase 3 follow-up.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from aletheia.counterfactual.trades import list_deduped_shadows


ADVISORY_TYPE = "policy_epoch_rotation"
ADVISORY_VERSION = "v1"

_VALID_SEVERITIES = ("info", "concern", "strong_concern")

# Harm-signal defaults. Chosen conservatively — the false-positive cost is
# owner attention, so the gate is set to catch clear drops without flagging
# ordinary week-to-week noise. See #497 Phase 3.5 for the justification.
_HARM_WINDOW_DAYS = 8
_HARM_CONCERN_PP = 0.3
_HARM_STRONG_PP = 0.7
_HARM_CONCERN_MIN_N = 50
_HARM_STRONG_MIN_N = 100


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
    """
    advisories: list[RotationAdvisory] = []
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = now_dt.isoformat(timespec="microseconds")

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

        epoch_id = calibration_report.get("policy_epoch_id")
        if epoch_id:
            try:
                harm = _harm_signal_from_epoch_windows(
                    policy_epoch_id=str(epoch_id),
                    now=now_dt,
                )
            except Exception:
                # Observe-only invariant: any failure to compute the signal is
                # a silent no-advisory. Never let advisory failures propagate
                # into report failures.
                harm = None
            if harm is not None:
                advisories.append(harm)

    return advisories


def _harm_signal_from_epoch_windows(
    *,
    policy_epoch_id: str,
    now: datetime,
    window_days: int = _HARM_WINDOW_DAYS,
    concern_pp: float = _HARM_CONCERN_PP,
    strong_concern_pp: float = _HARM_STRONG_PP,
    concern_min_n: int = _HARM_CONCERN_MIN_N,
    strong_concern_min_n: int = _HARM_STRONG_MIN_N,
) -> RotationAdvisory | None:
    """Emit a harm_signal advisory when current expectancy drops vs epoch opening.

    Reference baseline is the *same* policy_epoch's opening window; the module
    never compares across policy epochs (CF-I04 partitioning). If the epoch is
    younger than ``2 * window_days``, the baseline and current windows would
    overlap and the signal returns None instead of firing on noise.

    Returns None when:
    - the epoch has no shadow rows,
    - the epoch is too young to separate the two windows,
    - either window has fewer than concern_min_n samples,
    - the current avg_net has not dropped by at least concern_pp.

    Otherwise returns a RotationAdvisory with severity ``strong_concern`` when
    the drop is at least strong_concern_pp *and* both windows have at least
    strong_concern_min_n samples; ``concern`` in every other qualifying case.
    """
    epoch_start = _epoch_start(policy_epoch_id)
    if epoch_start is None:
        return None

    baseline_end = epoch_start + timedelta(days=window_days)
    current_start = now - timedelta(days=window_days)
    if current_start <= baseline_end:
        return None

    baseline_rows = list_deduped_shadows(
        since_iso=epoch_start.isoformat(),
        until_iso=baseline_end.isoformat(),
        policy_epoch_id=policy_epoch_id,
        columns=("net_pnl_pct",),
    )
    current_rows = list_deduped_shadows(
        since_iso=current_start.isoformat(),
        until_iso=now.isoformat(),
        policy_epoch_id=policy_epoch_id,
        columns=("net_pnl_pct",),
    )
    baseline_n = len(baseline_rows)
    current_n = len(current_rows)
    if baseline_n < concern_min_n or current_n < concern_min_n:
        return None

    baseline_avg = sum(float(r["net_pnl_pct"]) for r in baseline_rows) / baseline_n
    current_avg = sum(float(r["net_pnl_pct"]) for r in current_rows) / current_n
    delta_pp = round(current_avg - baseline_avg, 3)

    if delta_pp > -concern_pp:
        return None

    is_strong = (
        delta_pp <= -strong_concern_pp
        and baseline_n >= strong_concern_min_n
        and current_n >= strong_concern_min_n
    )
    severity = "strong_concern" if is_strong else "concern"

    return RotationAdvisory(
        advisory_type=ADVISORY_TYPE,
        version=ADVISORY_VERSION,
        produced_at=now.astimezone(timezone.utc).isoformat(timespec="microseconds"),
        policy_epoch_id=policy_epoch_id,
        severity=severity,
        signal="harm_signal",
        evidence={
            "reference": "epoch_opening_window",
            "window_days": window_days,
            "baseline_avg_net_pct": round(baseline_avg, 3),
            "baseline_n": baseline_n,
            "baseline_range": [
                epoch_start.isoformat(),
                baseline_end.isoformat(),
            ],
            "current_avg_net_pct": round(current_avg, 3),
            "current_n": current_n,
            "current_range": [
                current_start.isoformat(),
                now.astimezone(timezone.utc).isoformat(),
            ],
            "delta_pp": delta_pp,
            "concern_threshold_pp": -concern_pp,
            "strong_concern_threshold_pp": -strong_concern_pp,
        },
        recommendation=(
            f"Net expectancy dropped by {abs(delta_pp)}pp between the opening "
            f"{window_days}-day window and the most recent {window_days} days "
            f"within the same policy_epoch. Owner may wish to review whether "
            f"market regime shifted enough to warrant a fresh policy epoch."
        ),
        observe_only=True,
    )


def _epoch_start(policy_epoch_id: str) -> datetime | None:
    """Return the earliest shadow_trades.created_at for a given policy epoch.

    Used as the effective epoch-activation time for windowed comparison.
    Approximates true policy_epoch activation closely enough (the gap between
    activation and the first shadow row is bounded by one paper_trader tick).
    """
    from aletheia.storage.sqlite import get_db

    row = get_db().execute(
        "SELECT MIN(created_at) FROM shadow_trades WHERE policy_epoch_id = ?",
        (policy_epoch_id,),
    ).fetchone()
    raw = row[0] if row else None
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace(" ", "T"))
    except (ValueError, AttributeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


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
