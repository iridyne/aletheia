from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ProposalRecord:
    direction: Literal["long", "short"]
    created_at: str   # ISO-8601 UTC
    symbol: str = "BTC/USDT"
    conviction: int = 60
    source: str = "native"


@dataclass
class FilterScenario:
    name: str
    description: str
    history: list[ProposalRecord]     # pre-existing proposals in the 24h window
    incoming: list[ProposalRecord]    # decisions to test
    expected_blocked: list[bool]      # per-incoming expected result (True = should be blocked)


ALL_SCENARIOS: list[FilterScenario] = [
    FilterScenario(
        name="cold_start",
        description=(
            "Only 3 long proposals in history — below min_sample=15. "
            "Brake must stay off regardless of direction share."
        ),
        history=[
            ProposalRecord(direction="long", created_at="2026-07-09T06:00:00+00:00"),
            ProposalRecord(direction="long", created_at="2026-07-09T07:00:00+00:00"),
            ProposalRecord(direction="long", created_at="2026-07-09T08:00:00+00:00"),
        ],
        incoming=[
            ProposalRecord(direction="long", created_at="2026-07-09T10:00:00+00:00", conviction=60),
        ],
        expected_blocked=[False],
    ),
    FilterScenario(
        name="bull_regime_below_threshold",
        description=(
            "14 long proposals in history (100% long concentration) but sample size is 14 < min_sample=15. "
            "Brake must stay off because min_sample is not met."
        ),
        history=[
            ProposalRecord(direction="long", created_at=f"2026-07-08T{h:02d}:00:00+00:00")
            for h in range(14)
        ],
        incoming=[
            ProposalRecord(direction="long", created_at="2026-07-09T10:00:00+00:00", conviction=60),
        ],
        expected_blocked=[False],
    ),
    FilterScenario(
        name="bull_regime_just_below_95",
        description=(
            "16 long + 1 short = 17 total proposals. Long share = 16/17 ≈ 94.1%, below max_share=0.95. "
            "Brake must stay off."
        ),
        history=[
            ProposalRecord(direction="long", created_at=f"2026-07-08T{h:02d}:00:00+00:00")
            for h in range(16)
        ] + [
            ProposalRecord(direction="short", created_at="2026-07-08T16:00:00+00:00"),
        ],
        incoming=[
            ProposalRecord(direction="long", created_at="2026-07-09T10:00:00+00:00", conviction=60),
        ],
        expected_blocked=[False],
    ),
    FilterScenario(
        name="signal_runaway_triggers_brake",
        description=(
            "19 long + 1 short = 20 total. Long share = 19/20 = 95.0%, exactly equal to max_share=0.95. "
            "Brake requires strictly > 0.95, so it must NOT fire at the boundary."
        ),
        history=[
            ProposalRecord(direction="long", created_at=f"2026-07-08T{h:02d}:00:00+00:00")
            for h in range(19)
        ] + [
            ProposalRecord(direction="short", created_at="2026-07-08T19:00:00+00:00"),
        ],
        incoming=[
            ProposalRecord(direction="long", created_at="2026-07-09T10:00:00+00:00", conviction=60),
        ],
        expected_blocked=[False],
    ),
    FilterScenario(
        name="signal_runaway_extreme",
        description=(
            "20 long proposals spread across the last 20 hours (base 2026-07-09T10:00:00+00:00 minus "
            "1..20 hours), 0 short. Long share = 100% > 95%. "
            "Genuine runaway — brake must fire and block the incoming long proposal."
        ),
        history=[
            # h=0..9  → 2026-07-09T09:00 down to 2026-07-09T00:00
            # h=10..19 → 2026-07-08T23:00 down to 2026-07-08T14:00
            # All are within the 24h window (cutoff 2026-07-08T10:00:00+00:00)
            ProposalRecord(
                direction="long",
                created_at=(
                    f"2026-07-09T{9 - h:02d}:00:00+00:00"
                    if h < 10
                    else f"2026-07-08T{33 - h:02d}:00:00+00:00"
                ),
            )
            for h in range(20)
        ],
        incoming=[
            ProposalRecord(direction="long", created_at="2026-07-09T10:00:00+00:00", conviction=60),
        ],
        expected_blocked=[True],
    ),
    FilterScenario(
        name="high_conviction_override",
        description=(
            "20 long proposals in history (100% long, share > 95%). Incoming long has conviction=75 "
            "which equals override_conviction=75. Override exempts it — brake must NOT fire."
        ),
        history=[
            ProposalRecord(direction="long", created_at=f"2026-07-08T{h:02d}:00:00+00:00")
            for h in range(20)
        ],
        incoming=[
            ProposalRecord(direction="long", created_at="2026-07-09T10:00:00+00:00", conviction=75),
        ],
        expected_blocked=[False],
    ),
    FilterScenario(
        name="mixed_regime",
        description=(
            "10 long + 10 short = 20 total (50/50 split). No directional concentration. "
            "Brake must stay off for an incoming long."
        ),
        history=[
            ProposalRecord(direction="long", created_at=f"2026-07-08T{h:02d}:00:00+00:00")
            for h in range(10)
        ] + [
            ProposalRecord(direction="short", created_at=f"2026-07-08T{h:02d}:30:00+00:00")
            for h in range(10)
        ],
        incoming=[
            ProposalRecord(direction="long", created_at="2026-07-09T10:00:00+00:00", conviction=60),
        ],
        expected_blocked=[False],
    ),
    FilterScenario(
        name="opposite_direction_exempt",
        description=(
            "20 long proposals in history (100% long, share > 95%). Incoming proposal is SHORT. "
            "Brake only fires when top_dir matches the incoming direction — short is exempt."
        ),
        history=[
            ProposalRecord(direction="long", created_at=f"2026-07-08T{h:02d}:00:00+00:00")
            for h in range(20)
        ],
        incoming=[
            ProposalRecord(direction="short", created_at="2026-07-09T10:00:00+00:00", conviction=60),
        ],
        expected_blocked=[False],
    ),
]
