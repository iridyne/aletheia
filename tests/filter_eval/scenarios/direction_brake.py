from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal
from datetime import datetime, timedelta, timezone


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


def _t(hours_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


ALL_SCENARIOS = [
    FilterScenario(
        name="cold_start",
        description=(
            "Only 3 long proposals in history — below min_sample=15. "
            "Brake must stay off regardless of direction share."
        ),
        history=[
            ProposalRecord(direction="long", created_at=_t(3)),
            ProposalRecord(direction="long", created_at=_t(2)),
            ProposalRecord(direction="long", created_at=_t(1)),
        ],
        incoming=[
            ProposalRecord(direction="long", created_at=_t(0), conviction=60),
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
            ProposalRecord(direction="long", created_at=_t(14 - h))
            for h in range(14)
        ],
        incoming=[
            ProposalRecord(direction="long", created_at=_t(0), conviction=60),
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
            ProposalRecord(direction="long", created_at=_t(17 - h))
            for h in range(16)
        ] + [
            ProposalRecord(direction="short", created_at=_t(18)),
        ],
        incoming=[
            ProposalRecord(direction="long", created_at=_t(0), conviction=60),
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
            ProposalRecord(direction="long", created_at=_t(20 - h))
            for h in range(19)
        ] + [
            ProposalRecord(direction="short", created_at=_t(21)),
        ],
        incoming=[
            ProposalRecord(direction="long", created_at=_t(0), conviction=60),
        ],
        expected_blocked=[False],
    ),
    FilterScenario(
        name="signal_runaway_extreme",
        description=(
            "20 long proposals in the last 20 hours, 0 short. Long share = 100% > 95%. "
            "Genuine runaway — brake must fire and block the incoming long proposal."
        ),
        history=[
            ProposalRecord(
                direction="long",
                created_at=_t(h + 1),
            )
            for h in range(20)
        ],
        incoming=[
            ProposalRecord(direction="long", created_at=_t(0), conviction=60),
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
            ProposalRecord(direction="long", created_at=_t(20 - h))
            for h in range(20)
        ],
        incoming=[
            ProposalRecord(direction="long", created_at=_t(0), conviction=75),
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
            ProposalRecord(direction="long", created_at=_t(20 - h))
            for h in range(10)
        ] + [
            ProposalRecord(direction="short", created_at=_t(20 - h))
            for h in range(10)
        ],
        incoming=[
            ProposalRecord(direction="long", created_at=_t(0), conviction=60),
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
            ProposalRecord(direction="long", created_at=_t(20 - h))
            for h in range(20)
        ],
        incoming=[
            ProposalRecord(direction="short", created_at=_t(0), conviction=60),
        ],
        expected_blocked=[False],
    ),
]
