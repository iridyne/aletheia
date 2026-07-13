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


def _report_only_gate(
    *,
    name: str,
    stage: str,
    reason_codes: tuple[str, ...],
    current_value: str,
    note: str,
    feedback_source: FeedbackSource = "none",
    feedback_ready: bool = False,
) -> FilterSpec:
    """Declare a persisted hard/capability gate that must never auto-actuate."""
    return FilterSpec(
        name=name,
        stage=stage,
        drop_reason_codes=reason_codes,
        config_path="",
        hardcoded_at="",
        current_value=current_value,
        loosen_direction="n/a",
        neutral=None,
        floor=None,
        ceiling=None,
        feedback_source=feedback_source,
        family_keyed=False,
        auto_tunable=False,
        feedback_ready=feedback_ready,
        note=note,
    )


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
        feedback_ready=True,
        drop_share_hint=0.67,  # 121/181 observed drops
        note="Largest historical drop bucket. Compare exact-version pass/reject "
             "net outcomes before considering a longer family TTL.",
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
        feedback_ready=True,
        drop_share_hint=0.20,  # part of 54/181 ranker_excluded
        note="Categorical ladder. Reuse operator_loop._CONFIDENCE_LADDER semantics.",
    ),
    _report_only_gate(
        name="execution_capability",
        stage="paper_ranker",
        reason_codes=("equity_shadow_track",),
        current_value="asset-class execution routing",
        note="Observation-only asset routing is a capability boundary, not an alpha knob.",
    ),
    _report_only_gate(
        name="execution_eligibility",
        stage="paper_ranker",
        reason_codes=("execution_eligible_false",),
        current_value="opportunity execution_eligible must be true",
        note="Upstream execution capability fact. Never auto-override from missed returns.",
        feedback_source="forward_return",
        feedback_ready=True,
    ),
    _report_only_gate(
        name="direction_present",
        stage="paper_ranker",
        reason_codes=("neutral_or_missing_direction",),
        current_value="long or short direction required",
        note="Directionless candidates cannot produce a causal net-alpha counterfactual.",
    ),
    _report_only_gate(
        name="universe_execution_eligibility",
        stage="paper_ranker",
        reason_codes=("universe_not_eligible",),
        current_value="Universe member execution_eligible must be true",
        note="Market-data/capability boundary inherited from the selected instrument.",
        feedback_source="forward_return",
        feedback_ready=True,
    ),
    _report_only_gate(
        name="venue_symbol_capability",
        stage="paper_ranker",
        reason_codes=("okx_demo_symbol_not_supported",),
        current_value="target venue must list the exact market instrument",
        note="Exchange capability is report-only and cannot be loosened by alpha evidence.",
        feedback_source="forward_return",
        feedback_ready=True,
    ),
    _report_only_gate(
        name="factor_hard_block",
        stage="paper_ranker",
        reason_codes=("factor_hard_blocked",),
        current_value="confirmed hard_blocked factor status",
        note="Factor governance owns this hard rule; unified outcomes are diagnostic only.",
        feedback_source="forward_return",
        feedback_ready=True,
    ),
    _report_only_gate(
        name="guard_candidate_block",
        stage="paper_ranker",
        reason_codes=("guard_blocked",),
        current_value="active block_candidate guard",
        note="Guard lifecycle owns promotion and rollback; filter evidence is report-only.",
        feedback_source="forward_return",
        feedback_ready=True,
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
        feedback_ready=True,
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
        feedback_ready=True,
        drop_share_hint=0.0,  # latent in reference window
        note="Two brakes. Relaxed in a prior session; watch for SHORT suppression "
             "(see IB-24-11). Keyed by direction within family.",
    ),
    FilterSpec(
        name="operator_conviction_gate",
        stage="operator_skip",
        drop_reason_codes=("conviction_below_minimum",),
        config_path="",
        hardcoded_at="aletheia/agent_shell/trading_decision.py "
                     "(_MIN_CONVICTION_TO_TRADE)",
        current_value="minimum_conviction=55",
        loosen_direction="down",
        neutral=55.0,
        floor=45.0,
        ceiling=65.0,
        feedback_source="forward_return",
        family_keyed=True,
        auto_tunable=True,
        feedback_ready=True,
        note="Final post-debate conviction gate. Observe-only until exact-version "
             "pass/reject net cohorts satisfy coverage and sample gates.",
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
        feedback_source="forward_return",
        family_keyed=False,
        auto_tunable=True,
        feedback_ready=True,
        drop_share_hint=0.0,
        note="Batch pass/reject facts now settle with fixed-horizon net economics; "
             "legacy shadow PnL remains explanatory only until its own migration.",
    ),
    _report_only_gate(
        name="operator_already_evaluated",
        stage="paper_trader_selection",
        reason_codes=("operator_already_evaluated",),
        current_value="one final System Operator decision per opportunity id",
        note="Identity/idempotency boundary; a final decision is never re-queued as pending.",
    ),
    _report_only_gate(
        name="signal_enqueue",
        stage="paper_trader_enqueue",
        reason_codes=("signal_enqueue_error", "opportunity_not_open"),
        current_value="signal_pending insert and opportunity queued transition are atomic",
        note="State-consistency evidence; selection is not reported as delivered until the "
             "queue row and source lifecycle transition commit together.",
    ),
    _report_only_gate(
        name="proposal_identity",
        stage="proposal_submission",
        reason_codes=("invalid_action", "missing_symbol", "missing_direction"),
        current_value="trade-like action requires symbol and direction",
        note="Carrier completeness boundary. Failures are durable on PiDecision and are "
             "never repaired by guessing identity after the fact.",
    ),
    _report_only_gate(
        name="proposal_submission",
        stage="proposal_submission",
        reason_codes=("submission_exception", "no_submission_result"),
        current_value="every trade intent has a durable submission status and reason",
        note="Submission exceptions persist only a stable reason code; provider detail is not stored.",
    ),
    _report_only_gate(
        name="duplicate_symbol",
        stage="paper_trader_selection",
        reason_codes=("duplicate_symbol",),
        current_value="one ranked thesis per symbol per cycle",
        note="In-cycle deduplication boundary; the next distinct symbol backfills the slot.",
        feedback_source="forward_return",
        feedback_ready=True,
    ),
    _report_only_gate(
        name="open_position_duplicate",
        stage="paper_trader_selection",
        reason_codes=("open_position_duplicate",),
        current_value="one open position per symbol",
        note="Portfolio-state conflict, not a quality threshold. Lower ranks backfill the slot.",
        feedback_source="forward_return",
        feedback_ready=True,
    ),
    FilterSpec(
        name="signal_cooldown",
        stage="paper_trader_selection",
        drop_reason_codes=(),  # cooldown-skipped symbols are not logged today
        config_path="",
        hardcoded_at="aletheia/agents/paper_trader.py (_RECENT_SIGNAL_COOLDOWN_MINUTES=30)",
        current_value="30 min",
        loosen_direction="down",  # shorter cooldown → more signals
        neutral=30.0, floor=30.0, ceiling=480.0,
        feedback_source="forward_return",
        family_keyed=False,
        auto_tunable=True,
        feedback_ready=True,
        drop_share_hint=0.0,
        note="Cooldown pass/reject is persisted before the batch cap so a cooling "
             "top-ranked candidate cannot hide a viable lower-ranked candidate.",
    ),
    _report_only_gate(
        name="opportunity_emission",
        stage="signal_emitter",
        reason_codes=(
            "invalid_total_score",
            "member_identity_missing",
            "score_below_minimum",
            "feature_missing",
            "direction_missing",
            "factor_blocked",
            "regime_filtered",
            "opportunity_insert_error",
        ),
        current_value="one terminal emission fact per selected universe member",
        note="Coverage and lifecycle-integrity evidence only. It does not create outcome "
             "rows, tune thresholds, or alter opportunity eligibility.",
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
        feedback_source="forward_return",
        family_keyed=False,
        auto_tunable=True,
        feedback_ready=True,
        drop_share_hint=0.0,
        note="Pass/reject facts are now persisted. Only rows carrying an ex-ante "
             "scanner direction receive net outcomes; directionless rows remain coverage-only.",
    ),
    FilterSpec(
        name="ticker_availability",
        stage="ticker_prefilter",
        drop_reason_codes=("ticker_unavailable", "asset_identity_missing"),
        config_path="market_data.ccxt_exchange / market_data.ccxt_fallback",
        hardcoded_at="",
        current_value="primary/fallback market-data venue capability",
        loosen_direction="n/a",
        neutral=None, floor=None, ceiling=None,
        feedback_source="none",
        family_keyed=False,
        auto_tunable=False,
        note="Directionless capability evidence. Missing venue data is observable but "
             "must never become an automatic threshold change.",
    ),
    FilterSpec(
        name="ticker_volume_prefilter",
        stage="ticker_prefilter",
        drop_reason_codes=("quote_volume_below_prefilter_minimum",),
        config_path="universe.min_volume_24h_usdt",
        hardcoded_at="",
        current_value="download prefilter at configured 24h quote-volume minimum",
        loosen_direction="down",
        neutral=None, floor=None, ceiling=None,
        feedback_source="none",
        family_keyed=False,
        auto_tunable=False,
        note="No ex-ante direction exists yet. Report supply coverage only; do not infer "
             "missed net alpha or auto-lower the liquidity floor.",
    ),
    FilterSpec(
        name="tradability_stablecoin",
        stage="tradability_filter",
        drop_reason_codes=("stablecoin_base_excluded",),
        config_path="",
        hardcoded_at="aletheia/universe/filters.py (_STABLECOIN_BASES)",
        current_value="USD stablecoin bases excluded; gold-backed RWA remains eligible",
        loosen_direction="n/a",
        neutral=None, floor=None, ceiling=None,
        feedback_source="none",
        family_keyed=False,
        auto_tunable=False,
        note="Asset-class classification rule, not an alpha threshold.",
    ),
    FilterSpec(
        name="tradability_blacklist",
        stage="tradability_filter",
        drop_reason_codes=("configured_blacklist",),
        config_path="universe.blacklist",
        hardcoded_at="",
        current_value="configured blacklist",
        loosen_direction="n/a",
        neutral=None, floor=None, ceiling=None,
        feedback_source="none",
        family_keyed=False,
        auto_tunable=False,
        note="Intentional operator exclusion. Never auto-remove an asset from the blacklist.",
    ),
    FilterSpec(
        name="tradability_volume",
        stage="tradability_filter",
        drop_reason_codes=("quote_volume_below_minimum",),
        config_path="universe.min_volume_24h_usdt",
        hardcoded_at="",
        current_value="configured 24h quote-volume minimum",
        loosen_direction="down",
        neutral=None, floor=None, ceiling=None,
        feedback_source="none",
        family_keyed=False,
        auto_tunable=False,
        note="Directionless execution-quality check. Keep report-only until a valid "
             "ex-ante strategy direction exists before this gate.",
    ),
    FilterSpec(
        name="tradability_spread",
        stage="tradability_filter",
        drop_reason_codes=("spread_above_rejection_limit",),
        config_path="universe.max_spread_bps",
        hardcoded_at="aletheia/universe/filters.py (rejection multiplier=2)",
        current_value="reject above 2x configured max_spread_bps",
        loosen_direction="up",
        neutral=None, floor=None, ceiling=None,
        feedback_source="none",
        family_keyed=False,
        auto_tunable=False,
        note="Execution-cost gate without an ex-ante direction. Never relax from gross "
             "price movement alone.",
    ),
    FilterSpec(
        name="tradability_freshness",
        stage="tradability_filter",
        drop_reason_codes=("market_data_stale",),
        config_path="",
        hardcoded_at="aletheia/universe/filters.py (_MAX_FRESHNESS_SEC)",
        current_value="7200 seconds",
        loosen_direction="up",
        neutral=7200.0, floor=None, ceiling=None,
        feedback_source="none",
        family_keyed=False,
        auto_tunable=False,
        note="Data-integrity gate. Stale quotes are report-only and never auto-approved.",
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
        feedback_source="forward_return",
        family_keyed=True,
        auto_tunable=True,
        feedback_ready=True,
        drop_share_hint=0.0,
        note="Selected members and deterministic same-pool controls now share exact "
             "net outcomes. Still observation-only because allocation is not randomized.",
    ),
    FilterSpec(
        name="risk_engine",
        stage="risk_rejected",
        drop_reason_codes=("risk_rejected",),
        config_path="risk.*",
        hardcoded_at="",
        current_value="authoritative RiskDecision checks (see RiskConfig)",
        loosen_direction="n/a",
        neutral=None,
        floor=None,
        ceiling=None,
        feedback_source="forward_return",
        family_keyed=True,
        auto_tunable=False,
        feedback_ready=True,
        note="RiskDecision remains authoritative. Unified evidence is report-only "
             "and must never loosen risk, live, credential or execution gates.",
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
