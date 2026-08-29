"""Exchange-demo entry quality brake.

Live check #14 and local ``paper_internal`` stay unchanged. This module only
names violations; the Risk Engine remains the authoritative gate.

Default policy (demo, exchange-backed venues only):

- refuse new entries when published regime confidence is at or below 0.60
  (mean_reversion stays exempt from the confidence clause)
- refuse new entries — including mean_reversion — while the published regime
  is sideways
- refuse a family×direction with enough settled evidence and negative expectancy
- refuse a same-symbol reverse after a recent ``stop_loss`` on that symbol
- fail closed with ``demo_entry_quality_unavailable:<source>`` when an authority
  read fails; honestly empty evidence remains fail-open

Open positions are not closed. A zero/None threshold disables that clause.
"""

from __future__ import annotations

from aletheia.risk.policy import DEMO_EXECUTION_MODES

EXCHANGE_DEMO_MODES = frozenset({
    "bitget_demo",
    "okx_demo",
    "binance_testnet",
})

REGIME_CONFIDENCE_STANDDOWN = "regime_confidence_standdown"
REGIME_SIDEWAYS_STANDDOWN = "regime_sideways_standdown"
FAMILY_NOT_DEMO_ELIGIBLE = "family_not_demo_eligible"
REVERSE_AFTER_STOP = "reverse_after_stop"
DEMO_ENTRY_QUALITY_UNAVAILABLE = "demo_entry_quality_unavailable"

# Same-symbol reverse after a stop_loss: NEAR short stop then NEAR long next day.
REVERSE_AFTER_STOP_HOURS = 24

_RANK_SKIP_PREFIXES = (
    REGIME_CONFIDENCE_STANDDOWN,
    REGIME_SIDEWAYS_STANDDOWN,
    FAMILY_NOT_DEMO_ELIGIBLE,
    REVERSE_AFTER_STOP,
    DEMO_ENTRY_QUALITY_UNAVAILABLE,
)


def applies_to_execution_mode(execution_mode: str | None) -> bool:
    mode = str(execution_mode or "")
    return mode in EXCHANGE_DEMO_MODES and mode in DEMO_EXECUTION_MODES


def evaluate_demo_entry_quality(
    *,
    execution_mode: str | None,
    eligibility,
    scanner_family: str | None,
    direction: str | None,
    regime_state: str | None,
    regime_confidence: float | None,
    family_edge: dict | None,
    recent_stop_directions: set[str] | None = None,
) -> list[str]:
    """Return durable violation tokens. Empty means this brake does not fire."""
    if not applies_to_execution_mode(execution_mode) or eligibility is None:
        return []

    violations: list[str] = []

    # Mean-reversion may still fade a low-confidence trend, but sideways plus
    # mean-reversion was the loss cluster: noise stops inside the daily range.
    # Confidence standdown stays MR-exempt; sideways standdown applies to all
    # families, including mean_reversion.
    is_mean_reversion = str(scanner_family or "").lower() == "mean_reversion"

    try:
        min_conf = float(getattr(eligibility, "min_regime_confidence_to_enter", 0.0) or 0.0)
    except (TypeError, ValueError):
        min_conf = 0.0
    # Missing published regime is fail-open. The detector's implicit
    # MarketRegime() default (sideways / 0.5) is not a measured fact.
    published = regime_state is not None or regime_confidence is not None
    if published and min_conf > 0 and not is_mean_reversion:
        try:
            conf = float(regime_confidence) if regime_confidence is not None else 0.0
        except (TypeError, ValueError):
            conf = 0.0
        if conf <= min_conf:
            violations.append(
                f"{REGIME_CONFIDENCE_STANDDOWN}:conf={conf:.2f}<={min_conf:.2f}"
            )

    if published and getattr(eligibility, "sideways_standdown", False) is True:
        state = str(regime_state or "").lower()
        if state == "sideways":
            violations.append(f"{REGIME_SIDEWAYS_STANDDOWN}:state={state}")

    min_exp = getattr(eligibility, "family_min_expectancy", None)
    try:
        min_n = int(getattr(eligibility, "family_min_samples", 0) or 0)
    except (TypeError, ValueError):
        min_n = 0
    if min_exp is not None and min_n > 0 and family_edge:
        try:
            n = int(family_edge.get("n") or 0)
            raw_avg = family_edge.get("avg_return")
            avg = float(raw_avg) if raw_avg is not None else None
            floor = float(min_exp)
        except (TypeError, ValueError):
            n, avg, floor = 0, None, None
        if n >= min_n and avg is not None and floor is not None and avg < floor:
            family = scanner_family or "unknown"
            side = direction or "unknown"
            violations.append(
                f"{FAMILY_NOT_DEMO_ELIGIBLE}:{family}:{side}:n={n}:exp={avg}<{floor}"
            )

    side = str(direction or "").lower()
    stopped = {str(s or "").lower() for s in (recent_stop_directions or set())}
    opposite = "long" if side == "short" else ("short" if side == "long" else None)
    if opposite and opposite in stopped:
        violations.append(
            f"{REVERSE_AFTER_STOP}:{side}:after_{opposite}_stop"
        )
    return violations


def collect_demo_entry_quality_violations(
    *,
    execution_mode: str | None,
    eligibility,
    scanner_family: str | None,
    direction: str | None,
    market_type: str | None = "spot",
    symbol: str | None = None,
) -> list[str]:
    """Load evidence and evaluate the demo entry-quality brake.

    Empty evidence remains a valid fail-open policy state. A read failure is a
    different state because Risk cannot prove that the brake was evaluated.
    """
    if not applies_to_execution_mode(execution_mode) or eligibility is None:
        return []
    unavailable: list[str] = []
    try:
        from aletheia.learning.demo_entry_quality import (
            ensure_demo_entry_quality_policy_epoch,
        )
        ensure_demo_entry_quality_policy_epoch()
    except Exception:
        unavailable.append(f"{DEMO_ENTRY_QUALITY_UNAVAILABLE}:policy_epoch")

    try:
        state, confidence = load_published_regime(fail_open=False)
    except Exception:
        state, confidence = None, None
        unavailable.append(f"{DEMO_ENTRY_QUALITY_UNAVAILABLE}:regime")

    edge = None
    if scanner_family and direction:
        try:
            from aletheia.risk.engine import _family_live_edge
            edge = _family_live_edge(
                scanner_family,
                direction,
                market_type or "spot",
                fail_open=False,
            )
        except Exception:
            unavailable.append(f"{DEMO_ENTRY_QUALITY_UNAVAILABLE}:family_edge")

    try:
        recent_stop_directions = load_recent_stop_directions(
            symbol, execution_mode, fail_open=False
        )
    except Exception:
        recent_stop_directions = set()
        unavailable.append(f"{DEMO_ENTRY_QUALITY_UNAVAILABLE}:recent_stops")

    return unavailable + evaluate_demo_entry_quality(
        execution_mode=execution_mode,
        eligibility=eligibility,
        scanner_family=scanner_family,
        direction=direction,
        regime_state=state,
        regime_confidence=confidence,
        family_edge=edge,
        recent_stop_directions=recent_stop_directions,
    )


def load_published_regime(*, fail_open: bool = True) -> tuple[str | None, float | None]:
    """Return (state, confidence) only when a regime has been published.

    ``get_current_regime()`` fabricates sideways/0.5 when nothing is published.
    Standing down on that default would block every unused test DB and every
    process that has not yet written a regime row.
    """
    try:
        from aletheia.regime import detector as regime_mod
        published = getattr(regime_mod, "_published_regime", None)
        if published is not None:
            from aletheia.data.freshness import assess_freshness

            verdict = assess_freshness(
                {"detected_at": getattr(published, "detected_at", None)},
                "regime",
                timestamp_field="detected_at",
            )
            if verdict.usable:
                return getattr(published, "state", None), getattr(published, "confidence", None)
        from aletheia.storage.repositories import get_latest_market_regime
        regime = get_latest_market_regime()
        if not regime or not regime.get("state"):
            return None, None
        regime = regime_mod.get_current_regime()
        return getattr(regime, "state", None), getattr(regime, "confidence", None)
    except Exception:
        if not fail_open:
            raise
        return None, None


def load_recent_stop_directions(
    symbol: str | None,
    execution_mode: str | None,
    *,
    fail_open: bool = True,
) -> set[str]:
    """Directions of ``stop_loss`` closes on this symbol inside the cooldown.

    Missing identity and empty query results remain valid empty evidence. Set
    ``fail_open=False`` for authoritative callers that must distinguish a read
    failure from an empty result.
    """
    if not symbol or not applies_to_execution_mode(execution_mode):
        return set()
    try:
        from datetime import datetime, timedelta, timezone

        from aletheia.data.symbols import to_canonical
        from aletheia.storage.repositories import list_closed_positions

        want = to_canonical(symbol)
        if not want:
            return set()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=REVERSE_AFTER_STOP_HOURS)
        ).isoformat()
        rows = list_closed_positions(
            since_iso=cutoff, execution_mode=execution_mode, limit=200
        )
        found: set[str] = set()
        for row in rows:
            if str(row.get("close_reason") or "").lower() != "stop_loss":
                continue
            closed_sym = to_canonical(str(row.get("symbol") or ""))
            if closed_sym != want:
                continue
            side = str(row.get("direction") or "").lower()
            if side in {"long", "short"}:
                found.add(side)
        return found
    except Exception:
        if not fail_open:
            raise
        return set()


def ranker_skip_reason(violations: list[str]) -> str | None:
    """One skip_reason for the ranker. Risk still records every violation."""
    if not violations:
        return None
    return violations[0]


def is_demo_entry_quality_skip(reason: str | None) -> bool:
    code = str(reason or "")
    return any(code == prefix or code.startswith(f"{prefix}:") for prefix in _RANK_SKIP_PREFIXES)
