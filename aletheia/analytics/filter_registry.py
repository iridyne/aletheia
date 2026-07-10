"""Filter Registry — declarative inventory of every opportunity-screening filter.

The opportunity pipeline drops candidates at many stages (TTL expiry, ranker
gates, debate downgrade, direction brakes, risk rejection). Each drop is a
*filter* acting with a hand-set parameter. This registry is the single source of
truth for what those filters are, where their parameters live, which way they
move to loosen/tighten, and what realized-outcome signal can tell us whether the
filter is mis-calibrated.

It generalizes the two existing per-parameter registries in the codebase:
  - `operator_loop.PARAM_REGISTRY` / `ParamSpec` (bounded config-tuning of 3
    global params from aggregate account health), and
  - `allocator._FAMILY_TTL` + `get_family_multipliers` (per-scanner-family
    forward-outcome calibration of score multipliers).

Phase 1 is OBSERVE-ONLY: this module is pure data + helpers. It performs no DB
reads, no config writes, and no trading mutation. `filter_controller.py` consumes
it to compute shadow recommendations; a future phase may actuate through it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Direction a parameter moves to LOOSEN the filter (let more opportunities pass).
#   "up"   → increasing the value loosens (e.g. per-family TTL: longer = fewer expiries)
#   "down" → decreasing the value loosens (e.g. min confidence: lower = more pass)
#   "n/a"  → not a scalar knob / intentionally not tunable
LoosenDir = Literal["up", "down", "n/a"]

# Where the feedback/error signal for this filter comes from.
#   "forward_return" — universe_forward_outcomes, direction-adjusted (Phase-1 primary)
#   "closed_pnl"     — realized PnL from closed positions (family_scorecard)
#   "none"           — no measurable signal (e.g. drops before a row is persisted)
FeedbackSource = Literal["forward_return", "closed_pnl", "none"]


@dataclass(frozen=True)
class FilterSpec:
    """One screening filter and everything the controller needs to reason about it.

    `neutral`/`floor`/`ceiling` describe the tuning envelope (mirrors
    `PersonaParam(min, init, max)` and `ParamSpec.neutral/floor/ceiling`). They are
    advisory in Phase 1 (recommendations only) and become the hard bounds for
    bounded auto-tune in Phase 2.
    """

    name: str
    # Pipeline stage as it appears in the opportunity_flow ledger (final_stage), or
    # a synthetic stage label for filters that act before a ledger stage exists.
    stage: str
    # Exact drop-reason codes this filter emits into the ledger timeline. Used to
    # attribute dropped opportunities back to the filter that dropped them.
    drop_reason_codes: tuple[str, ...]
    # Dotted config path if config-driven (e.g. "trading.min_confidence_to_trade"),
    # else "" when the parameter is a hardcoded constant.
    config_path: str
    # "file:line" of the hardcoded constant when config_path is "" (for the Phase-2
    # lift-to-config work); "" when config-driven.
    hardcoded_at: str
    # Human-readable current value (string form so ladders/dicts render uniformly).
    current_value: str
    loosen_direction: LoosenDir
    # Tuning envelope (advisory in Phase 1). None when not a scalar knob.
    neutral: float | None
    floor: float | None
    ceiling: float | None
    feedback_source: FeedbackSource
    # True when the filter's outcomes can be keyed by scanner_family (the chosen
    # granularity). False → only a global signal is available.
    family_keyed: bool
    # False for intentional hard rules and change-controlled params — the controller
    # may still REPORT on them but must never recommend auto-actuation.
    auto_tunable: bool
    # True only after the pipeline persists this filter's own passed/dropped cohort.
    # Aggregate family returns are useful context, but cannot prove a filter is too
    # loose or too tight.
    feedback_ready: bool = False
    # Approximate share of drops attributable to this filter in the reference 72h
    # window (for impact ranking in the report). 0.0 when latent / not yet observed.
    drop_share_hint: float = 0.0
    note: str = ""


# ─── The inventory ───────────────────────────────────────────────────────────
# Ordered by observed drop-volume impact (largest first) so reports and future
# actuation address the dominant bottleneck before low-impact knobs.
# Every value below was verified against source on 2026-07-10.

FILTER_REGISTRY: tuple[FilterSpec, ...] = (
    FilterSpec(
        name="family_ttl",
        stage="ttl_expired",
        drop_reason_codes=("opportunity_valid_until_expired", "universe_member_expired"),
        config_path="",
        hardcoded_at="aletheia/universe/allocator.py:30 (_FAMILY_TTL)",
        current_value="momentum=1800s, mean_reversion=3600s, volatility=7200s, "
                      "derivatives=14400s, event=43200s, core=86400s",
        loosen_direction="up",  # longer TTL → fewer expiries → looser
        neutral=None, floor=None, ceiling=None,  # per-family; envelope set in Phase 2
        feedback_source="forward_return",
        family_keyed=True,
        auto_tunable=True,
        drop_share_hint=0.67,  # 121/181 observed drops
        note="Largest drop bucket. If a family's expired opps show positive "
             "direction-adjusted forward return, its TTL is too short.",
    ),
    FilterSpec(
        name="ranker_confidence_gate",
        stage="ranker_excluded",
        drop_reason_codes=("confidence_below",),
        config_path="trading.min_confidence_to_trade",
        hardcoded_at="",
        current_value="medium (ladder: speculative<medium<high); score floor <45 "
                      "at opportunity_flow.py inference only",
        loosen_direction="down",  # lower confidence bar → more pass
        neutral=None, floor=None, ceiling=None,  # categorical ladder
        feedback_source="forward_return",
        family_keyed=True,
        auto_tunable=True,
        drop_share_hint=0.20,  # part of 54/181 ranker_excluded
        note="Categorical ladder. Reuse operator_loop._CONFIDENCE_LADDER semantics.",
    ),
    FilterSpec(
        name="debate_downgrade",
        stage="debate_downgraded",
        drop_reason_codes=("Persisted debate trace.",),
        config_path="",
        hardcoded_at="aletheia/agent_shell/trading_decision.py:50-51 "
                     "(_MIN_CONVICTION_TO_TRADE=55, _DEBATE_RANGE=(40,80))",
        current_value="min_conviction=55, debate_range=(40,80)",
        loosen_direction="down",  # lower min conviction → fewer downgraded skips
        neutral=55.0, floor=45.0, ceiling=65.0,
        feedback_source="forward_return",
        family_keyed=True,
        auto_tunable=True,
        drop_share_hint=0.03,  # 5/181; but highest missed_alpha_suspect signal density
        note="missed_alpha_suspect concentrates here. Conviction cutoff is a config-"
             "liftable constant; the LLM debate itself stays out of scope.",
    ),
    FilterSpec(
        name="direction_bias_brake",
        stage="operator_skip",
        drop_reason_codes=("direction_overheated",),
        config_path="",
        hardcoded_at="aletheia/agent_shell/trading_decision.py:55-57 (_BIAS_BRAKE_*), "
                     "405-407 (_proposal_direction_overheated defaults)",
        current_value="bias_brake: min_proposals=20, max_dir_pct=0.80, bypass_conviction=85; "
                      "overheated: min_sample=15, max_share=0.95, override=75",
        loosen_direction="up",  # higher max_share → fewer brake trips
        neutral=0.90, floor=0.80, ceiling=0.98,
        feedback_source="forward_return",
        family_keyed=True,
        auto_tunable=True,
        drop_share_hint=0.0,  # latent in reference window
        note="Two brakes. Relaxed in a prior session; watch for SHORT suppression "
             "(see IB-24-11). Keyed by direction within family.",
    ),
    FilterSpec(
        name="ranker_ic_ramp",
        stage="ranker_score",
        drop_reason_codes=(),  # score penalty, not a hard drop reason
        config_path="",
        hardcoded_at="aletheia/agents/paper_trader_ranker.py:310-317 "
                     "(ramp /10.0, no_history -5, blocked_candidate -50)",
        current_value="ramp_trades=10, no_history_penalty=-5, blocked_penalty=-50",
        loosen_direction="down",  # smaller penalty / faster ramp → new factors surface
        neutral=10.0, floor=3.0, ceiling=20.0,
        feedback_source="closed_pnl",
        family_keyed=True,
        auto_tunable=True,
        drop_share_hint=0.0,
        note="Suppresses new factors until 10 trades. Reward via factor_feedback "
             "trade_count/win_rate per factor group.",
    ),
    FilterSpec(
        name="signal_batch_limit",
        stage="paper_trader_selection",
        drop_reason_codes=(),  # unselected candidates become shadow rows, not drops
        config_path="",
        hardcoded_at="aletheia/agents/paper_trader.py:137 (_select_signal_candidates limit=3)",
        current_value="3",
        loosen_direction="up",  # more signals per cycle
        neutral=3.0, floor=2.0, ceiling=5.0,
        feedback_source="closed_pnl",
        family_keyed=False,
        auto_tunable=True,
        drop_share_hint=0.0,
        note="Reward from shadow_trades rank>1 pnl: if rank-2/3 outperform rank-1, raise.",
    ),
    FilterSpec(
        name="signal_cooldown",
        stage="paper_trader_selection",
        drop_reason_codes=(),  # cooldown-skipped symbols are not logged today
        config_path="",
        hardcoded_at="aletheia/agents/paper_trader.py:37 (_RECENT_SIGNAL_COOLDOWN_MINUTES=120)",
        current_value="120 min",
        loosen_direction="down",  # shorter cooldown → more signals
        neutral=120.0, floor=30.0, ceiling=480.0,
        feedback_source="forward_return",
        family_keyed=False,
        auto_tunable=True,
        drop_share_hint=0.0,
        note="Observability gap: cooldown rejections not logged. Signal is indirect "
             "until a cooldown_rejected event is added (roadmap).",
    ),
    FilterSpec(
        name="observer_pre_filter",
        stage="pre_persistence",
        drop_reason_codes=(),  # rejected symbols never become opportunity rows
        config_path="",
        hardcoded_at="aletheia/agents/market_observer.py:178,187 "
                     "(RSI dev >12, ATR >0.8%)",
        current_value="rsi_deviation>12, atr_pct>0.8",
        loosen_direction="down",  # lower thresholds → more opportunities created
        neutral=12.0, floor=6.0, ceiling=20.0,
        feedback_source="none",
        family_keyed=False,
        auto_tunable=False,  # NOT observable until pre_filter_rejections log exists
        drop_share_hint=0.0,
        note="Drops before a row exists → no per-rejection signal. Do NOT auto-tune "
             "until the pre_filter_rejections table is added (roadmap).",
    ),
    FilterSpec(
        name="scanner_weights_quota",
        stage="ranker_score",
        drop_reason_codes=(),  # re-ranking, not a hard drop
        config_path="universe.scanner_weights / universe.family_quota / "
                    "universe.family_quota_by_regime / universe.scanner_regime_scoring",
        hardcoded_at="",
        current_value="scanner_weights={}, family_quota={momentum:.., ...} (see UniverseConfig)",
        loosen_direction="up",  # higher weight/quota → family surfaces more
        neutral=None, floor=None, ceiling=None,  # dict-valued
        feedback_source="closed_pnl",
        family_keyed=True,
        auto_tunable=True,
        drop_share_hint=0.0,
        note="Strongest existing signal (family_direction_edge + family_scorecard). "
             "Best Phase-2 first candidate. Overlaps get_family_multipliers.",
    ),
    FilterSpec(
        name="risk_family_live_eligibility",
        stage="risk_rejected",
        drop_reason_codes=("family_not_live_eligible",),
        config_path="risk.live_family_min_samples / risk.live_family_min_win_rate_pct",
        hardcoded_at="",
        current_value="min_samples=30, min_win_rate_pct=50.0",
        loosen_direction="down",  # lower bars → more families eligible
        neutral=30.0, floor=20.0, ceiling=50.0,
        feedback_source="closed_pnl",
        family_keyed=True,
        auto_tunable=False,  # CHANGE-CONTROLLED (risk/ engine) — human-gated only
        drop_share_hint=0.0,
        note="Change-controlled risk gate. Report only; never auto-actuate.",
    ),
)


# Intentional hard rules that the controller must never treat as tunable. Kept
# explicit so the report can show "excluded by design" rather than silently omit.
NON_TUNABLE_HARD_RULES: tuple[str, ...] = (
    "neutral_or_missing_direction",   # no directional bet possible — correct to drop
    "okx_demo_symbol_not_supported",  # venue capability constraint
    "equity_shadow_track",            # asset-class routing, not a quality filter
    "guard_blocked",                  # guard system owns its own feedback loop
)


def tunable_filters() -> tuple[FilterSpec, ...]:
    """Filters that may be configured for future bounded actuation."""
    return tuple(f for f in FILTER_REGISTRY if f.auto_tunable)


def report_only_filters() -> tuple[FilterSpec, ...]:
    """Filters surfaced in the report but never auto-actuated (hard rules / risk)."""
    return tuple(f for f in FILTER_REGISTRY if not f.auto_tunable)


def filters_by_stage(stage: str) -> tuple[FilterSpec, ...]:
    return tuple(f for f in FILTER_REGISTRY if f.stage == stage)


def filter_by_name(name: str) -> FilterSpec | None:
    for f in FILTER_REGISTRY:
        if f.name == name:
            return f
    return None
