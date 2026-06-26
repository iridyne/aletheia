"""Trading-session classifier — which global liquidity regime are we in right now?

Crypto trades 24/7, but its liquidity and realised volatility still follow the
TradFi session clock: the same institutional desks, the BTC↔equity correlation,
and US macro releases all cluster the biggest moves into the London/New-York
windows and leave the Asian and post-US hours thin and range-bound.

This module is a PURE, deterministic UTC-hour → session classifier. It has NO
side effects, reads no DB, and — critically — is NOT imported by the frozen PI
decision chain (`agent_shell/trading_context.py` / `trading_decision.py`). Until
those files wire it in (a Round-2 between-round change under EXPERIMENT_PROTOCOL),
this module changes nothing about what the AI sees or decides. It exists so that
the wiring is a one-line `import` the moment the freeze lifts, and so the session
boundaries can be unit-tested in isolation today.

Session bands (UTC, weekday):
    00:00–07:00  asian     — Tokyo/Singapore/HK. US & EU cash closed. Thinnest,
                             most mean-reverting; momentum breakouts often stall.
    07:00–13:00  european  — London. Volume picks up at the 07:00–08:00 open.
    13:00–16:00  overlap   — London afternoon + US morning. Peak liquidity, the
                             window that carries the largest directional moves.
    16:00–21:00  us        — New York. Highest realised vol; US macro (CPI/FOMC)
                             and the equity cash session land here.
    21:00–24:00  late_us   — US winding down, Asia not yet open. Thin, gappy.
    Sat / Sun    weekend   — Lowest liquidity all day; gap risk into Monday.

`us_equities_open` is the precise NYSE cash window (Mon–Fri 14:30–21:00 UTC),
matching the existing equity_observer scheduler gate. (NYSE shifts an hour earlier
under US DST; the one-hour drift is immaterial for a qualitative LLM hint and we
keep the codebase's fixed 14:30–21:00 convention for consistency.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone


@dataclass(frozen=True)
class SessionInfo:
    """A snapshot of the current global trading-session regime."""

    label: str                # machine key: asian|european|overlap|us|late_us|weekend
    name: str                 # human label for the prompt
    utc_hour: int             # 0-23
    weekday: int              # 0=Mon .. 6=Sun
    is_weekend: bool
    us_equities_open: bool    # NYSE cash session live (Mon-Fri 14:30-21:00 UTC)
    liquidity: str            # "high" | "medium" | "low"
    note: str                 # one-line characterization injected into the prompt


# NYSE cash session in UTC (standard-time convention; see module docstring).
_US_EQUITIES_OPEN = time(14, 30)
_US_EQUITIES_CLOSE = time(21, 0)


def classify_session(now_utc: datetime | None = None) -> SessionInfo:
    """Classify the current global trading session from the UTC wall clock.

    Deterministic and pure — pass an explicit ``now_utc`` to make it testable.
    A naive datetime is assumed to be UTC.
    """
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    hour = now.hour
    weekday = now.weekday()
    is_weekend = weekday >= 5  # Sat=5, Sun=6

    us_open = (
        not is_weekend
        and _US_EQUITIES_OPEN <= now.timetz().replace(tzinfo=None) < _US_EQUITIES_CLOSE
    )

    if is_weekend:
        return SessionInfo(
            label="weekend",
            name="Weekend (low-liquidity, gap risk)",
            utc_hour=hour,
            weekday=weekday,
            is_weekend=True,
            us_equities_open=False,
            liquidity="low",
            note=(
                "Weekend: TradFi closed, lowest liquidity of the week. Thin books "
                "amplify wicks; positions held into Monday carry gap risk. Favor "
                "smaller size and only high-conviction setups."
            ),
        )

    # Weekday hour bands (priority: overlap window wins over single sessions).
    if 13 <= hour < 16:
        label, name, liquidity = "overlap", "EU/US overlap (peak liquidity)", "high"
        note = (
            "London afternoon + US morning overlap — peak liquidity and the window "
            "that carries the largest directional moves. Breakouts have the best "
            "follow-through here; trend continuation is most reliable."
        )
    elif 16 <= hour < 21:
        label, name, liquidity = "us", "US (New York)", "high"
        note = (
            "US session: highest realised volatility, US macro and equity cash flow "
            "dominate. Strong directional follow-through but also sharp reversals "
            "around data releases."
        )
    elif 7 <= hour < 13:
        label, name, liquidity = "european", "European (London)", "medium"
        note = (
            "European session: liquidity building from the London open; decent "
            "follow-through but lighter than the US overlap. US cash not yet open."
        )
    elif 0 <= hour < 7:
        label, name, liquidity = "asian", "Asian (Tokyo/Singapore/HK)", "low"
        note = (
            "Asian session: US & European cash equities closed, thinner books and "
            "lower realised vol. Price tends to range / mean-revert; momentum "
            "breakouts often lack follow-through until London/US. Consider smaller "
            "size, a higher conviction bar, and caution around low-liquidity wicks."
        )
    else:  # 21 <= hour < 24
        label, name, liquidity = "late_us", "Late US → pre-Asia (thin)", "low"
        note = (
            "Late-US / pre-Asia gap: US desks winding down, Asia not yet open. Thin "
            "liquidity and choppy, low-conviction price action. Treat breakouts "
            "skeptically until Asian volume arrives."
        )

    return SessionInfo(
        label=label,
        name=name,
        utc_hour=hour,
        weekday=weekday,
        is_weekend=False,
        us_equities_open=us_open,
        liquidity=liquidity,
        note=note,
    )


def render_for_prompt(now_utc: datetime | None = None) -> str:
    """Render the session block for injection into the PI Agent assessment prompt.

    Kept in this (non-frozen) module so the Round-2 wiring into
    ``trading_decision._build_assessment_prompt`` is a single call:

        from aletheia.trading.market_session import render_for_prompt
        parts += ["## Trading Session", render_for_prompt(), ""]
    """
    s = classify_session(now_utc)
    eq = "open" if s.us_equities_open else "closed"
    return (
        f"  {s.name} | {s.utc_hour:02d}:00 UTC | liquidity={s.liquidity} | "
        f"US equities {eq}\n"
        f"  {s.note}"
    )
