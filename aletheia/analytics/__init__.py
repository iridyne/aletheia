"""Analytics layer — performance, attribution, streaks, and temporal analysis.

This package now exposes a small, stable public surface so callers (e.g. the
performance report) can import the key primitives from one place instead of
reaching into each submodule:

    from aletheia.analytics import (
        summarize, attribute, summarize_streaks, temporal_summary,
    )
"""

from aletheia.analytics.attribution import attribute
from aletheia.analytics.performance_summary import (
    calmar,
    consolidate_report,
    equity_curve,
    expectancy,
    max_drawdown,
    profit_factor,
    summarize,
    win_rate,
)
from aletheia.analytics.streaks import (
    current_streak,
    max_consecutive_losses,
    max_consecutive_wins,
    runs,
    summarize_streaks,
)
from aletheia.analytics.temporal import (
    bucket_by_hour,
    bucket_by_weekday,
    temporal_summary,
)

__all__ = [
    # performance_summary
    "equity_curve",
    "max_drawdown",
    "profit_factor",
    "win_rate",
    "expectancy",
    "calmar",
    "summarize",
    "consolidate_report",
    # attribution
    "attribute",
    # streaks
    "runs",
    "max_consecutive_wins",
    "max_consecutive_losses",
    "current_streak",
    "summarize_streaks",
    # temporal
    "bucket_by_hour",
    "bucket_by_weekday",
    "temporal_summary",
]
