"""Pydantic models for storage entities."""

import json
import math
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator, model_validator
from uuid import uuid4
from aletheia.storage.time_utils import utcnow_iso as _utcnow


def new_id() -> str:
    return uuid4().hex[:12]




class StrategyParametersInvalid(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def decode_strategy_parameters(parameters: str | None) -> dict:
    if not isinstance(parameters, str) or not parameters.strip():
        raise StrategyParametersInvalid("parameters_missing")
    try:
        parsed = json.loads(parameters)
    except json.JSONDecodeError as exc:
        raise StrategyParametersInvalid("invalid_json") from exc
    if not isinstance(parsed, dict):
        raise StrategyParametersInvalid("not_json_object")
    return parsed


EXIT_INTENT_STOP_SOURCES = frozenset({"operator", "default"})


def exit_intent_violations(
    *,
    expected_horizon_hours: object = None,
    stop_source: object = None,
) -> list[str]:
    violations: list[str] = []
    if expected_horizon_hours is not None:
        try:
            horizon = float(expected_horizon_hours)
        except (TypeError, ValueError):
            horizon = math.nan
        if not math.isfinite(horizon) or horizon <= 0:
            violations.append("invalid_expected_horizon_hours")
    if stop_source is not None:
        if not isinstance(stop_source, str) or stop_source not in EXIT_INTENT_STOP_SOURCES:
            violations.append("invalid_stop_source")
    return violations


class _Provenanced(BaseModel):
    """Runtime Session provenance carried by every core trading-fact entity.

    All fields are expand-only and nullable: a row produced with no active runtime
    session (legacy data, out-of-band inserts) keeps them NULL rather than
    fabricating an identity. Stamped at insert time from the current session.
      runtime_session_id   — process lifecycle that produced the row
      producer_commit      — commit that process booted on
      behavior_fingerprint — fingerprint of the behaviour-affecting config
    """

    runtime_session_id: str | None = None
    producer_commit: str | None = None
    behavior_fingerprint: str | None = None


class RuntimeSession(BaseModel):
    """One Gateway PROCESS lifecycle. runtime_id names the running version;
    session_id names this specific boot (a restart is a new session under the same
    runtime_id). ended_at/shutdown_clean stay unset until a clean shutdown."""

    session_id: str = Field(default_factory=new_id)
    runtime_id: str = "runtime-local-current"
    boot_commit: str | None = None
    config_hash: str | None = None
    behavior_fingerprint: str | None = None
    schema_version: str | None = None
    active_scope_id: str | None = None
    execution_mode: str | None = None
    economics_tracing_enabled: bool | None = None
    started_at: str = Field(default_factory=_utcnow)
    ended_at: str | None = None
    shutdown_clean: int = 0


class AuditEvent(BaseModel):
    id: str = Field(default_factory=new_id)
    timestamp: str = Field(default_factory=_utcnow)
    actor: str
    source: str
    event_type: str
    entity_type: str | None = None
    entity_id: str | None = None
    before_state: str | None = None
    after_state: str | None = None
    message: str | None = None


AgentRunStatus = Literal[
    "queued",
    "running",
    "completed",
    "completed_with_errors",
    "failed",
    "timed_out",
    "stale",
    "cancelled",
    "rejected_queue_full",
    # Historical terminals with no writer left, kept legal so the CHECK migration
    # runs on real data while no transition can produce them. The read paths
    # still classify all three: run_view.FAILED_AGENT_RUN_STATUSES has "error",
    # run_view.SUCCESS_AGENT_RUN_STATUSES has "success", and
    # health._FAILED_STATUSES adds "cancel_requested".
    "error",
    "cancel_requested",
    "success",
]


class AgentRun(BaseModel):
    id: str = Field(default_factory=new_id)
    agent_name: str
    trigger_type: str = "scheduled"
    permission_mode: str = "Observe"
    started_at: str = Field(default_factory=_utcnow)
    ended_at: str | None = None
    status: AgentRunStatus = "running"
    model_provider: str | None = None
    model_name: str | None = None
    tools_called: str = "[]"
    outputs_created: str = "[]"
    errors: str = "[]"
    cost_estimate: float | None = None


class AgentArtifactRef(BaseModel):
    """A current-run reference to a durable record returned by an agent."""

    artifact_type: Literal[
        "opportunity",
        "filter_decision",
        "filter_decision_batch",
        "pi_decision",
        "portfolio_snapshot",
        "research_note",
        "serenity_evidence",
        "post_trade_review",
        "lesson",
        "dreaming_log",
        "daily_review",
        "policy_version",
        "draft_skill",
    ]
    artifact_id: str = Field(min_length=1, max_length=128)


class AgentRunArtifact(AgentArtifactRef):
    """Persisted AgentRun-to-artifact relation; never inferred from legacy output."""

    run_id: str


class Opportunity(_Provenanced):
    id: str = Field(default_factory=new_id)
    symbol: str
    market: str
    direction_bias: str | None = None
    thesis: str | None = None
    confidence: str = "speculative"
    # Source-relative 0..100 evidence score, never an implied probability.
    # Observer agents use model confidence; universe_scanner uses its composite
    # ranking score.  Consumers must preserve that source-specific distinction.
    # None keeps older rows without a numeric score valid.
    confidence_score: int | None = None
    source_agent: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    status: str = "open"
    # Historical rotation cursor from the retired ranker workset.
    last_ranked_at: str | None = None
    linked_strategy_id: str | None = None
    # Asset class: "crypto" | "equity" | "prediction". Default "crypto" keeps
    # older rows valid.
    asset_class: str = "crypto"
    # Sub-category within asset_class. For equity: "equity_daily" (yfinance daily
    # bars as primary signal). equity_observer always produces "equity_daily" with
    # optional xstock data in metadata.
    asset_subclass: str = ""
    # Canonical equity ticker for cross-path correlation (e.g. "AAPL").
    canonical_ticker: str = ""
    # JSON-encoded prediction/equity-specific metadata (event title, probabilities, etc.)
    metadata: str = "{}"
    execution_eligible: bool = True
    universe_snapshot_id: str | None = None
    universe_member_id: str | None = None
    scanner_family: str | None = None
    # Edge Lab: structured execution hints from LLM signal generation
    valid_until: str | None = None  # ISO timestamp — signal expiry
    entry_zone: str | None = None  # JSON: {"low": x, "high": y}
    stop_loss_hint: float | None = None
    tp_hint: float | None = None
    regime_tag: str | None = None
    key_observations: str = ""
    # Derivatives state at observation time (perp only; None for spot). Surfaced so a
    # perp opportunity exposes the funding/OI that justified it and the PI can weigh
    # funding drag when choosing spot vs perp. funding_rate is the per-interval rate
    # (fraction), open_interest in USD, oi_delta_1h the 1h % change.
    funding_rate: float | None = None
    open_interest: float | None = None
    oi_delta_1h: float | None = None
    # Fast Trigger facts. NULL keeps every non-trigger/legacy Opportunity valid.
    setup_id: str | None = None
    signal_mark: str | None = None
    trigger_behavior_version: str | None = None
    # Canonical generation JSON and its SHA-256.  NULL is retained
    # for legacy and observation-only rows; these three columns were written
    # by the now-retired execution-eligible generation entry path.
    generation_version: str | None = None
    generation_json: str | None = None
    generation_hash: str | None = None


class ShadowTrade(BaseModel):
    id: str = Field(default_factory=new_id)
    batch_id: str | None = None
    opportunity_id: str
    symbol: str
    venue: str | None = None
    market_type: str = "spot"
    direction: str
    scanner_family: str | None = None
    trade_score: float = -999.0
    rank: int = 0
    entry_price: float | None = None
    entry_spread_bps: float | None = None
    funding_rate_at_entry: float | None = None
    outcome_candidate_key: str | None = None
    regime_tag: str | None = None
    was_selected: bool = False
    created_at: str = Field(default_factory=_utcnow)
    exit_price: float | None = None
    pnl_pct: float | None = None
    gross_return_pct: float | None = None
    net_pnl_pct: float | None = None
    estimated_fee_pct: float | None = None
    estimated_spread_slippage_pct: float | None = None
    estimated_funding_pct: float | None = None
    min_tradable_edge_pct: float | None = None
    min_cost_covering_edge_pct: float | None = None
    settlement_price_source: str | None = None
    settlement_lag_sec: float | None = None
    cost_model_version: str | None = None
    btc_return_pct: float | None = None
    resolved_at: str | None = None
    policy_epoch_id: str | None = None
    learning_score_trace_json: str | None = None


class FilterDecision(BaseModel):
    """Append-only fact for one candidate crossing one pipeline gate."""

    id: str = Field(default_factory=new_id)
    dedup_key: str
    candidate_key: str
    candidate_kind: str
    parent_candidate_key: str | None = None
    run_id: str | None = None
    opportunity_id: str | None = None
    universe_snapshot_id: str | None = None
    universe_member_id: str | None = None
    proposal_id: str | None = None
    venue: str | None = None
    symbol: str
    market_type: str = "spot"
    direction: str | None = None
    scanner_family: str | None = None
    regime_tag: str | None = None
    filter_name: str
    stage: str
    sequence_no: int = 0
    decision: str
    reason_code: str
    reason_detail: str | None = None
    input_snapshot: str = "{}"
    parameter_snapshot: str = "{}"
    filter_version: str
    code_commit: str | None = None
    outcome_candidate_key: str | None = None
    evaluated_at: str = Field(default_factory=_utcnow)


class FilterDecisionBatch(BaseModel):
    """Compressed complete facts for one high-volume directionless gate."""

    id: str = Field(default_factory=new_id)
    dedup_key: str
    run_id: str
    candidate_kind: str
    filter_name: str
    stage: str
    candidate_count: int
    passed_count: int = 0
    rejected_count: int = 0
    not_applicable_count: int = 0
    error_count: int = 0
    candidate_facts: str = "[]"
    parameter_snapshot: str = "{}"
    filter_version: str
    code_commit: str | None = None
    evaluated_at: str = Field(default_factory=_utcnow)
    benchmark_window_id: str | None = None
    benchmark_batch_id: str | None = None


class FilterCandidateOutcome(BaseModel):
    """Fixed-horizon net result shared by all gates for one ex-ante candidate."""

    id: str = Field(default_factory=new_id)
    outcome_candidate_key: str
    venue: str
    symbol: str
    market_type: str = "spot"
    direction: str
    scanner_family: str | None = None
    regime_tag: str | None = None
    horizon: str
    allocation_selected: bool | None = None
    entry_price_usd: float
    entry_spread_bps: float | None = None
    funding_rate_at_entry: float | None = None
    funding_evidence_json: str | None = None
    entry_ts: str = Field(default_factory=_utcnow)
    due_at: str
    settled_at: str | None = None
    exit_price_usd: float | None = None
    gross_return_pct: float | None = None
    directional_return_pct: float | None = None
    estimated_fee_pct: float | None = None
    estimated_spread_slippage_pct: float | None = None
    estimated_funding_pct: float | None = None
    net_return_pct: float | None = None
    min_tradable_edge_pct: float | None = None
    min_cost_covering_edge_pct: float | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    settlement_price_source: str | None = None
    settlement_lag_sec: float | None = None
    cost_model_version: str | None = None
    # Fixed ex-ante basis for candidate-layer analysis. It is deliberately
    # nullable so legacy rows remain unavailable instead of inheriting a later
    # proposal's capital.
    analysis_notional_quote: float | None = Field(default=None, gt=0)
    producer_runtime_session_id: str | None = None
    producer_commit: str | None = None
    producer_behavior_fingerprint: str | None = None
    settlement_runtime_session_id: str | None = None
    settlement_producer_commit: str | None = None
    settlement_behavior_fingerprint: str | None = None
    status: str = "pending"
    rsi_1h_at_entry: float | None = None
    rsi_15m_at_entry: float | None = None
    return_24h_at_entry: float | None = None
    direction_exhaustion_signal: bool | None = None
    benchmark_window_id: str | None = None
    benchmark_batch_id: str | None = None
    benchmark_cohorts: str = "[]"
    benchmark_cost_snapshot: str | None = None
    benchmark_path_facts: str | None = None


class TradeProposal(_Provenanced):
    id: str = Field(default_factory=new_id)
    # Deprecated compatibility column. New Operator proposals leave this NULL;
    # historical rows may still carry it until the storage schema is retired.
    strategy_id: str | None = None
    opportunity_id: str | None = None
    symbol: str
    market_type: str
    direction: str
    order_type: str = "market"
    quantity: float | None = None
    price: float | None = None
    # Optional fields used by §21.3 risk checks (max_leverage, max_single_trade_risk).
    leverage: int | None = None
    stop_price: float | None = None
    # Entry discipline (2026-07-03, owner discussion #6): a System Operator entry
    # must commit to all four numbers BEFORE the order — entry (price, used as the
    # LIMIT price), stop (stop_price), take-profit, and the resulting reward:risk
    # ratio — so a trade is a planned bet, not a gambler's market-buy. take_profit
    # is the absolute TP price; risk_reward = |tp - entry| / |entry - stop|.
    # Both NULL on legacy market proposals (closes, reconciler-adopted chains).
    take_profit: float | None = None
    risk_reward: float | None = None
    # Exit-intent plan M2: the Operator's intended holding horizon (hours) and stop
    # provenance ("operator" if AI-authored the stop, "default" if config-filled).
    # Threaded to the position at open (gateway) so the executor's horizon time-stop
    # + must-answer wake and the default-provenance reback work. NULL on legacy rows.
    expected_horizon_hours: float | None = None
    stop_source: str | None = None
    execution_mode: str
    agent_rationale: str | None = None
    # JSON snapshot captured at proposal/decision time for replay-stable
    # position attribution (BTC benchmark, regime, market context).
    attribution_snapshot: str | None = None
    status: str = "pending"
    # Provenance: "native" (Aletheia generated it) or "adopted" (synthetic chain the
    # reconciler built to bring a pre-existing/external exchange position under
    # management). See docs/governance/decisions/DECISIONS.md (2026-06-12) /
    # position_reconciler.
    source: str = "native"
    # Execution-level school discriminator (Phase 4): "short_term" (default) or
    # "serenity". Threaded from TradingDecision.signal_source so position_manager
    # can branch trailing-stop + max_hold behavior per school without touching the
    # risk engine. Default 'short_term' keeps every legacy proposal on the existing
    # path.
    trade_school: str = "short_term"
    # Opaque provenance for live authorization at Proposal creation time.
    # Value is `unlock_evidence.evidence_binding` — a sha256 hash over the
    # (broker, execution_mode, account_environment, account_fingerprint,
    # config_revision, permission_status) tuple. Never a reusable secret.
    # NULL for every non-live-limited Proposal.
    live_authorization_ref: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    # TaG hash chain (P0-1, 2026-06-29) — borrow from OpenAlice TradingGit.
    # commit_hash = SHA-256 of canonical JSON over immutable proposal fields
    # (everything except status + commit_hash itself) + parent_hash, sliced to
    # 16 hex chars (64-bit collision space). parent_hash links to the previous
    # row's commit_hash forming a global single chain. Both NULL on legacy rows
    # written before P0-1 landed. See
    # docs/research/agents-learning/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md.
    commit_hash: str | None = None
    parent_hash: str | None = None
    # Exact conditional authority used by Fast Trigger. These fields are part of
    # immutable Proposal identity and therefore hash-covered when present.
    setup_id: str | None = None
    authorization_id: str | None = None
    # #2288 active Order Intent fields. NULL keeps historical/compat callers on
    # their existing semantics; non-NULL fields are hash-covered with Proposal.
    entry_plan_id: str | None = None
    entry_leg_index: int | None = Field(default=None, ge=0, le=1)
    order_tactic: str | None = None
    time_in_force: str | None = None
    post_only: bool | None = None
    bbo_reference: str | None = None
    max_slippage_bps: float | None = None
    worst_price: float | None = None


class RiskDecision(_Provenanced):
    id: str = Field(default_factory=new_id)
    proposal_id: str
    approved: bool = False
    risk_score: float = 0.0
    violations: str = "[]"
    allowed_execution_mode: str | None = None
    # Preserve the requested mode independently of any execution allowance.
    # A rejection has no allowed mode; the request remains historical evidence.
    proposal_execution_mode: str | None = None
    # ``preflight`` is a fresh, advisory check before venue quantity
    # normalization. ``authoritative`` is the one decision an Order may consume.
    # Rows written before this distinction remain explicitly ``legacy`` instead
    # of being retroactively promoted to authority.
    decision_role: str = "legacy"
    # Immutable evaluated snapshot identity.  The proposal hash continues to
    # identify the originally committed intent while evaluated_quantity records
    # the final size Risk actually assessed.
    evaluated_quantity: float | None = None
    proposal_commit_hash: str | None = None
    risk_mode: str | None = None
    policy_id: str | None = None
    policy_revision: int | None = None
    policy_hash: str | None = None
    execution_attempt_id: str | None = None
    execution_receipt_hash: str | None = None
    execution_receipt_json: str | None = None
    message: str | None = None
    created_at: str = Field(default_factory=_utcnow)


OrderStatus = Literal[
    "pending",
    "open",
    "submitted",
    "submission_unknown",
    "submission_rejected",
    "materialization_missing",
    "partial",
    "partially_filled",
    "filled",
    "overfilled",
    "cancelled",
    "error",
    # Historical terminals with no writer left. analytics/trade_stats.py
    # classifies all three when reading orders: completed_fill_statuses has
    # "executed", definitive_failure_statuses has "failed" and "rejected".
    # Legal values with no incoming transition, so stored rows stay readable
    # while new writes cannot produce them.
    "executed",
    "failed",
    "rejected",
]


class Order(_Provenanced):
    id: str = Field(default_factory=new_id)
    proposal_id: str | None = None
    risk_decision_id: str | None = None
    symbol: str
    market_type: str
    direction: str
    order_type: str
    quantity: float
    price: float | None = None
    # Pre-fill market/request anchor used only for paired execution-cost
    # accounting. It never changes the submitted price or broker behavior.
    reference_price: float | None = None
    reference_price_source: str | None = None
    execution_mode: str
    # Exact non-secret UTA identity captured before external submission; never backfilled from current config.
    uta_account_identity_json: str | None = None
    # Entry Plan cancel convergence bookkeeping: how many venue cancel submissions
    # have been made, and the earliest time the next one may run. NULL means no
    # retry is scheduled.
    entry_cancel_attempts: int | None = None
    entry_cancel_retry_after: str | None = None
    exchange_order_id: str | None = None
    # Stable exchange-side idempotency/recovery key.  This is persisted before
    # submission so a timeout that loses the venue order id can still be
    # reconciled through private order/fill history without resubmitting.
    client_order_id: str | None = None
    entry_plan_id: str | None = None
    order_tactic: str | None = None
    time_in_force: str | None = None
    post_only: bool | None = None
    requested_liquidity: str | None = None
    execution_attempt_id: str | None = None
    execution_receipt_hash: str | None = None
    paper_fill_model_version: str | None = None
    matching_stream_generation_id: str | None = None
    matching_subscription_generation_id: str | None = None
    monitoring_gap_started_at: str | None = None
    # Direct Position identity for close Orders and for entry Orders after local
    # materialization.  It removes the ambiguous proposal/symbol guesswork when
    # an averaging leg or delayed exchange fill is reconciled later.
    position_id: str | None = None
    # Close Orders persist the lifecycle reason before exchange submission so a
    # delayed private fill can close the Position with the original explanation.
    close_reason: str | None = None
    # Optional trade_decision_rationale.v1 JSON captured with a pending exchange
    # close claim. The Position publishes it only after verified settlement.
    close_reasoning: str | None = None
    status: OrderStatus = "pending"
    source: str = "native"  # "native" | "adopted" — see TradeProposal.source
    created_at: str = Field(default_factory=_utcnow)
    filled_at: str | None = None
    error_reason: str | None = None


class Fill(_Provenanced):
    id: str = Field(default_factory=new_id)
    order_id: str
    fill_price: float
    fill_quantity: float
    fee: float | None = None
    fee_currency: str = ""
    # Provenance of the fee figure: "estimated" (flat-rate taker fee computed by the
    # broker at fill time — paper + all exchange brokers do this) or "reconciled"
    # (reconciliation backfilled the exchange's real fillId fee). Defaults estimated
    # so an estimate is never silently mistaken for an actual exchange fee.
    fee_source: str = "estimated"
    # Venue/private-fill evidence only. NULL means unavailable, never inferred
    # from limit/post-only request intent.
    realized_liquidity: str | None = None
    simulated_liquidity: str | None = None
    execution_attempt_id: str | None = None
    execution_receipt_hash: str | None = None
    paper_fill_model_version: str | None = None
    fill_evidence_json: str | None = None
    fill_evidence_hash: str | None = None
    market_event_id: str | None = None
    filled_at: str = Field(default_factory=_utcnow)


PositionStatus = Literal["open", "closed"]


class Position(_Provenanced):
    id: str = Field(default_factory=new_id)
    symbol: str
    market_type: str
    direction: str
    quantity: float
    entry_price: float
    current_price: float | None = None
    unrealized_pnl: float | None = None
    # Provenance of the latest open-position mark.  "exchange_exact" when the
    # value was matched to this position's exact broker identity.  "unavailable"
    # when no exchange data is attributable — unrealized_pnl is then None.
    unrealized_pnl_source: str = "unavailable"
    # Open positions start at zero.  A closed futures position may remain NULL
    # when its real funding ledger is unavailable; NULL must never be coerced to
    # zero because that would fabricate net account economics.
    realized_pnl: float | None = 0.0
    execution_mode: str
    # Deprecated compatibility column; active Operator positions inherit NULL
    # because new proposals no longer carry Strategy ID.
    strategy_id: str | None = None
    # Futures: leverage used to open (None/1 = spot or 1x). liquidation_price is the
    # estimated isolated-margin liquidation level computed at open (None for 1x/spot).
    # Persisted so the account-leverage ratio check (#17), liquidation-buffer monitoring,
    # and the dashboard read a real per-position leverage instead of assuming 1x.
    leverage: int | None = None
    liquidation_price: float | None = None
    # Causal-chain back-links (trust §1.1): which proposal/order opened this position.
    # Nullable — older rows and positions opened before this field predate it.
    proposal_id: str | None = None
    order_id: str | None = None
    # Which opportunity this position traces back to. Powers the risk engine's
    # sector-concentration check (#16), which resolves scanner_family per open
    # position via its opportunity_id — without it that check is silenced.
    opportunity_id: str | None = None
    status: PositionStatus = "open"
    stop_price: float | None = None
    take_profit_price: float | None = None
    close_reason: str | None = None
    # JSON record written only after a close succeeds. It names the real actor
    # Current immutable ExitPlan projection.  The timestamp resolves against
    # position_exit_plan_versions; NULL means protection has not been activated.
    # The v9 storage migration adds this field without backfilling old rows.
    exit_plan_effective_at: str | None = None
    # and source (AI, automatic protection, manual, or reconciliation), avoiding
    # any retrospective invention of an AI rationale for a mechanical close.
    exit_reasoning: str | None = None
    # Outcome-integrity evidence is additive and fail-closed.  A closed row is a
    # local lifecycle fact, not automatically a genuine strategy outcome.  Later
    # close/reconciliation tasks stamp verified values; legacy and unstamped rows
    # remain explicitly ineligible without rewriting their original economics.
    close_category: str = "unknown"
    entry_price_source: str = "unavailable"
    entry_fill_verified: bool = False
    exit_price_source: str = "unavailable"
    exit_fill_verified: bool = False
    outcome_eligible: bool = False
    outcome_exclusion_reason: str = "unclassified"
    reconciliation_identity: str | None = None
    reconciliation_evidence: str | None = None
    outcome_semantics_version: str | None = None
    source: str = "native"  # "native" | "adopted" — see TradeProposal.source
    opened_at: str = Field(default_factory=_utcnow)
    closed_at: str | None = None
    # Edge Lab: BTC benchmark at entry/exit to separate alpha from market beta.
    btc_entry_price: float | None = None
    btc_exit_price: float | None = None
    # Regime tag at entry time: "risk_on" | "risk_off" | "unknown".
    regime_tag: str | None = None
    entry_market_summary: str | None = None
    exchange_tpsl_order_id: str | None = None
    # Exchange close order that has been accepted but not confirmed filled yet.
    # Position remains status="open" so risk/exposure accounting stays honest.
    pending_close_order_id: str | None = None
    pending_close_status: str | None = None
    pending_close_requested_at: str | None = None
    # Resting locally-watched limit exit and who authored it. exit_limit_source is
    # 'human' (dashboard manual close) or 'system_operator' (AI proactive exit); it
    # selects the honest close_reason when the level is crossed so a human's manual
    # limit is never attributed to AI exit discretion. NULL = no resting limit.
    exit_limit_price: float | None = None
    exit_limit_source: str | None = None
    decision_reasoning: str | None = None
    entry_fee: float | None = None
    exit_fee: float | None = None
    # Signed quote-currency funding paid over the holding period. Positive is a
    # cost and negative is a credit. Futures outcomes remain fail-closed while
    # this is unknown; spot closes stamp zero with source="not_applicable".
    funding_cost: float | None = None
    funding_source: str = "unavailable"
    slippage_cost: float | None = None
    # Execution-level school discriminator (Phase 4). Mirrors TradeProposal.trade_school
    # — defaults 'short_term' so legacy + adopted positions stay on the 72h cap + ATR
    # trailing path. 'serenity' positions skip ATR trailing and use
    # cfg.serenity.max_hold_days * 24 as their hold cap.
    trade_school: str = "short_term"
    # Outcome producer identity — stamped when the position is CLOSED, so a
    # restart between open and close is visible: runtime_session_id records who
    # OPENED it (Session A), closed_runtime_session_id who CLOSED it (Session B).
    # Never collapse the whole position onto the last run.
    closed_runtime_session_id: str | None = None
    closed_producer_commit: str | None = None
    closed_behavior_fingerprint: str | None = None
    # Exact persona/progressive-unlock manifest used for this entry.  It is
    # copied from the immutable Proposal rationale at materialization time so
    # a later rollout cannot claim an outcome that used another policy.
    sizing_manifest: str | None = None
    # Market-aware evaluation is advisory only.  Keep the legacy summary fields
    # and store exact venue/symbol/market + source provenance separately as JSON.
    last_market_signal_action: str | None = None
    last_market_signal_reason: str | None = None
    last_market_signal_at: str | None = None
    last_market_signal_evidence: str | None = None


class PositionDisappearanceCandidate(BaseModel):
    """Durable, account-scoped suspicion that an open position disappeared.

    This model stores confirmation state only.  It does not authorize closure;
    position_reconciler wiring is governed by OI-B01 in a later task.
    """

    id: str = Field(default_factory=new_id)
    position_id: str
    identity_key: str
    broker_id: str
    account_environment: str
    account_fingerprint: str
    execution_mode: str
    symbol: str
    market_type: str
    direction: str
    status: str = "pending"
    first_observed_at: str
    last_observed_at: str
    confirmation_count: int = 1
    direct_query_state: str = "not_attempted"
    semantics_version: str
    resolved_at: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


class PositionDisappearanceObservation(BaseModel):
    """Immutable, idempotent evidence attached to a disappearance candidate."""

    id: str = Field(default_factory=new_id)
    candidate_id: str
    observation_hash: str
    view_identity: str
    view_complete: bool
    position_absent: bool
    direct_query_state: str = "not_attempted"
    evidence_json: str = "{}"
    observed_at: str = Field(default_factory=_utcnow)


class ReconciliationIntegrityIncident(BaseModel):
    """Durable circuit-breaker state for systemic close/reopen churn."""

    id: str = Field(default_factory=new_id)
    incident_type: str = "close_reopen_churn"
    fingerprint: str
    status: str = "active"
    window_minutes: int = 30
    churn_count: int
    identities_json: str = "[]"
    reopen_sources_json: str = "{}"
    detected_at: str = Field(default_factory=_utcnow)
    last_detected_at: str = Field(default_factory=_utcnow)
    last_clean_observed_at: str | None = None
    clean_observation_count: int = 0
    resolution_reason: str | None = None
    resolved_by: str | None = None
    resolved_at: str | None = None
    semantics_version: str = "oi-v1"
    details_json: str = "{}"
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


class MarketRegimeRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    state: str = "sideways"
    confidence: float = 0.5
    long_bias: float = 0.5
    short_bias: float = 0.5
    signals: str = "{}"
    detected_at: str = Field(default_factory=_utcnow)


class StrategyRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    description: str | None = None
    state: str = "draft"
    # JSON blob with entry_rule/exit_rule/timeframe/direction_bias.
    # NOTE: entry_rule/exit_rule are ADVISORY (LLM-generated intent text),
    # They do not authorize orders or define executable trading rules.
    parameters: str = "{}"
    under_review: bool = False
    review_notes: str | None = None
    review_updated_at: str | None = None
    paper_trading_paused: bool = False
    paper_pause_reason: str | None = None
    paper_paused_at: str | None = None
    direction_affinity: str = "both"
    applicable_regimes: str = "all"
    # Which market a strategy is designed for: "spot" | "usdt_futures" | "both".
    # The paper ranker won't match a spot-only strategy to a perp opportunity (or
    # vice versa) now that the observer emits both (Round 2 perp enablement).
    market_type_affinity: str = "both"
    strategy_type: str = "scanner"
    execution_venue: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)

    @field_validator("parameters")
    @classmethod
    def _parameters_must_be_json_object(cls, value: str) -> str:
        decode_strategy_parameters(value)
        return value


class KnowledgeItem(BaseModel):
    id: str = Field(default_factory=new_id)
    item_type: str
    title: str
    file_path: str | None = None
    strategy_id: str | None = None
    confidence: str = "medium_confidence"
    status: str = "active"
    tags: str = "[]"
    content: str | None = None
    content_hash: str | None = None
    content_format: str | None = None
    content_sha256: str | None = None
    metadata_json: str | None = None
    symbol: str | None = None
    direction: str | None = None
    scanner_family: str | None = None
    outcome: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


class MarketSnapshot(BaseModel):
    id: str = Field(default_factory=new_id)
    symbol: str  # canonical: BTC/USDT
    market_type: str  # "spot" | "perp" | "futures"
    exchange: str = ""  # source exchange: "binance", "bybit", "bitget", …
    price: FiniteFloat = Field(gt=0)
    volume_24h: FiniteFloat | None = Field(default=None, ge=0)
    change_pct_24h: FiniteFloat | None = None
    high_24h: FiniteFloat | None = Field(default=None, gt=0)
    low_24h: FiniteFloat | None = Field(default=None, gt=0)
    provider: str | None = None
    snapshot_at: str = Field(default_factory=_utcnow)

    @field_validator(
        "price", "volume_24h", "change_pct_24h", "high_24h", "low_24h",
        mode="before",
    )
    @classmethod
    def _reject_boolean_numeric_facts(cls, value):
        if isinstance(value, bool):
            raise ValueError("market snapshot numeric facts must not be boolean")
        return value

    @model_validator(mode="after")
    def _high_low_are_ordered(self):
        if (
            self.high_24h is not None
            and self.low_24h is not None
            and self.high_24h < self.low_24h
        ):
            raise ValueError("market snapshot high_24h must be >= low_24h")
        return self


class PortfolioSnapshot(BaseModel):
    id: str = Field(default_factory=new_id)
    total_value_usdt: float
    unrealized_pnl: float = 0.0
    realized_pnl: float | None = 0.0
    positions_count: int = 0
    execution_mode: str
    exchange_equity_usdt: float | None = None
    # A deliberately explicit account-balance reading.  `total_value_usdt` on
    # legacy local rows was position notional, so it cannot safely power an
    # account-equity curve.
    account_equity_usdt: float | None = None
    snapshot_at: str = Field(default_factory=_utcnow)


class SerenityTheme(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    demand_wave: str | None = None
    status: str = "active"
    objective: str | None = None
    description: str | None = None
    strategy_notes: str | None = None
    tags: str = "[]"
    run_id: str | None = None
    thesis: str | None = None
    confidence: float | None = None
    novelty_score: float | None = None
    actionability_score: float | None = None
    evidence_strength: float | None = None
    related_assets: str = "[]"
    related_topics: str = "[]"
    market_types: str = "[]"
    expires_at: str | None = None
    promoted_at: str | None = None
    dismissed_at: str | None = None
    metadata: str = "{}"
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


class SerenityEvidence(BaseModel):
    id: str = Field(default_factory=new_id)
    theme_id: str
    symbol: str | None = None
    source: str = "manual"
    source_ref: str | None = None
    title: str
    summary: str = ""
    tier: str = "speculative"  # generator's self-graded PROPOSAL (advisory only)
    supports: bool = True
    evidence_type: str = "thesis"
    event_at: str = Field(default_factory=_utcnow)
    created_at: str = Field(default_factory=_utcnow)
    actor: str | None = None
    confidence: str = "speculative"
    tags: str = "[]"
    run_id: str | None = None
    content_hash: str | None = None
    quality_score: float | None = None
    url: str | None = None
    published_at: str | None = None
    fetched_at: str | None = None
    metadata: str = "{}"
    # D2 (2026-06-27): authoritative tier assigned by the independent evaluator
    # (aletheia/trading/serenity_evidence.py). This is what gates scoring/promotion —
    # `tier` above is only the generator's proposal. NULL = legacy / unassessed.
    assessed_tier: str | None = None
    assessed_by: str | None = None
    # S1 (2026-06-28, AI Berkshire 4-lens integration): which research perspective
    # produced this evidence. Values: "technical" | "flow" | "sentiment" | "macro" | None.
    agent_lens: str | None = None


class SerenityOpportunity(BaseModel):
    id: str = Field(default_factory=new_id)
    theme_id: str
    symbol: str
    direction: str
    thesis: str | None = None
    evidence_tier: str = "speculative"  # generator's self-graded PROPOSAL (advisory only)
    stage: str = "research"
    average_mode: str = "none"
    action_intent: str = "watch"
    status: str = "active"
    risk_flags: str = "[]"
    risk_metrics: str = "{}"
    confidence_score: int | None = None
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)
    # D2 (2026-06-27): authoritative tier derived from supporting evidence by the
    # independent evaluator — gates scoring (score_serenity_opportunity). `evidence_tier`
    # is only the generator's self-grade. NULL = legacy / unassessed → scores as speculative.
    assessed_tier: str | None = None
    assessed_by: str | None = None
    # S4 (2026-06-28, AI Berkshire forced-verdict integration): machine-readable verdict
    # JSON blob — recommendation/bull_case/bear_case/key_assumption/invalidation.
    # NULL = legacy / pre-S4 run. Never self-grades scoring.
    verdict_json: str = "{}"
    # S2 (2026-06-28, AI Berkshire researchability): deterministic A/B/C grade
    # assigned by assess_researchability() at pipeline time. C caps score at ≤40.
    researchability_grade: str = "C"
    # EQ-R (2026-07-01, r-series/equity_chain adaptation): instrument classification.
    # asset_class: "crypto" (default) | "equity" (US stock token from equity_chain lens).
    # market_type: canonical InstrumentId market type — "spot" | "swap" | "perp" | "futures".
    #   Bitget r-series + ON-series are always "spot".  Future OKX SWAP → "swap".
    #   NULL on legacy rows means "crypto/spot" — treat as crypto for execution routing.
    # These two fields let trading_context and the PI skip equity observations for
    # order routing while still surfacing them as research context.
    asset_class: str = "crypto"
    market_type: str = "spot"


class SerenityRiskGate(BaseModel):
    id: str = Field(default_factory=new_id)
    opportunity_id: str
    gate_name: str
    status: str = "unknown"  # pass | warn | fail
    metric: str
    observed: str | float | int | None = None
    threshold: str | float | int | None = None
    message: str | None = None
    checked_at: str = Field(default_factory=_utcnow)


class SerenityReplaySnapshot(BaseModel):
    id: str = Field(default_factory=new_id)
    opportunity_id: str
    as_of: str
    evidence_count: int = 0
    score_total: int = 0
    stage: str = "research"
    event_type: str = "snapshot"
    event_title: str = "snapshot"
    payload: str = "{}"
    created_at: str = Field(default_factory=_utcnow)


class SerenityPostMortem(BaseModel):
    id: str = Field(default_factory=new_id)
    position_id: str | None = None
    opportunity_id: str
    outcome: str
    failure_bucket: str | None = None
    score_delta: int | None = None
    attribution: str | None = None
    summary: str
    created_by: str = "system"
    created_at: str = Field(default_factory=_utcnow)


class OutcomeHookJob(BaseModel):
    id: str = Field(default_factory=new_id)
    position_id: str
    hook_type: str
    status: str = "pending"
    attempts: int = 0
    next_attempt_at: str = Field(default_factory=_utcnow)
    locked_at: str | None = None
    claim_token: str | None = None
    last_error: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)
    completed_at: str | None = None


class SerenityResearchRun(BaseModel):
    id: str = Field(default_factory=new_id)
    trigger_type: str = "scheduled"
    mode: str = "standard"
    status: str = "queued"
    stage: str | None = None
    parent_run_id: str | None = None
    idempotency_key: str | None = None
    profile_snapshot_id: str | None = None
    brief_snapshot: str | None = None
    focus_theme_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    next_retry_at: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    error_stage: str | None = None
    retry_count: int = 0
    model_used: str | None = None
    token_usage: str = "{}"
    artifact_counts: str = '{"themes":0,"opportunities":0,"evidence":0,"claims":0}'
    metadata: str = "{}"
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


class SerenityRunEvent(BaseModel):
    id: str = Field(default_factory=new_id)
    run_id: str
    event_type: str
    stage: str | None = None
    message: str | None = None
    data: str = "{}"
    created_at: str = Field(default_factory=_utcnow)


class SerenityUserProfile(BaseModel):
    id: str = Field(default_factory=new_id)
    include_topics: str = "[]"
    exclude_topics: str = "[]"
    include_assets: str = "[]"
    exclude_assets: str = "[]"
    market_types: str = "[]"
    time_horizon: str | None = None
    risk_appetite: str | None = None
    raw_brief: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


class SerenityProfileSnapshot(BaseModel):
    id: str = Field(default_factory=new_id)
    profile_id: str | None = None
    include_topics: str = "[]"
    exclude_topics: str = "[]"
    include_assets: str = "[]"
    exclude_assets: str = "[]"
    market_types: str = "[]"
    time_horizon: str | None = None
    raw_brief: str | None = None
    created_at: str = Field(default_factory=_utcnow)


class SerenityFeedbackEvent(BaseModel):
    id: str = Field(default_factory=new_id)
    target_type: str
    target_id: str
    action: str
    value: str = "{}"
    created_at: str = Field(default_factory=_utcnow)


class SerenityClaim(BaseModel):
    id: str = Field(default_factory=new_id)
    run_id: str | None = None
    evidence_doc_id: str | None = None
    claim_text: str
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    claim_time: str | None = None
    confidence: float = 0.5
    price_relevant: bool = False
    evidence_hash: str | None = None
    support_level: str | None = None
    metadata: str = "{}"
    created_at: str = Field(default_factory=_utcnow)


# ====================================================================
# Persistence / Sync / Recovery Layer Models
# ====================================================================


class PSEvent(BaseModel):
    event_id: str = Field(default_factory=new_id)
    event_type: str
    aggregate_type: str
    aggregate_id: str
    actor_type: str
    actor_id: str
    run_id: str | None = None
    causation_id: str | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None
    payload_json: str = "{}"
    payload_hash: str = ""
    created_at: str = Field(default_factory=_utcnow)
    machine_id: str = ""
    schema_version: str = "v1"


class PSArtifactRef(BaseModel):
    path: str
    sha256: str
    content_type: str
    created_at: str = Field(default_factory=_utcnow)


class PSGatewaySession(BaseModel):
    gateway_session_id: str = Field(default_factory=new_id)
    gateway_id: str
    mode: str
    status: str
    started_at: str = Field(default_factory=_utcnow)
    ended_at: str | None = None
    heartbeat_at: str | None = None
    machine_id: str = ""
    recovery_report_id: str | None = None
    freeze_reason: str | None = None
    config_hash: str | None = None


class PSReconciliationReport(BaseModel):
    reconciliation_report_id: str = Field(default_factory=new_id)
    gateway_session_id: str
    status: str  # ok | diff_found | frozen | failed
    local_state_hash: str | None = None
    remote_state_hash: str | None = None
    diffs_json: str = "[]"
    actions_json: str = "[]"
    artifact_path: str | None = None
    created_at: str = Field(default_factory=_utcnow)


class PSOrderIntent(BaseModel):
    intent_id: str = Field(default_factory=new_id)
    execution_intent_id: str | None = None
    proposal_id: str
    strategy_id: str
    mode: str  # paper | live
    venue: str
    symbol: str
    side: str  # buy | sell
    order_type: str
    quantity: str
    limit_price: str | None = None
    reduce_only: int = 0
    status: str = "created"  # created | submitted | cancelled | failed | completed
    client_order_id: str = ""
    idempotency_key: str = ""
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


class PSLesson(BaseModel):
    lesson_id: str = Field(default_factory=new_id)
    source_type: str
    source_id: str
    category: str
    severity: str
    summary: str
    evidence_json: str = "{}"
    recommendation_json: str | None = None
    status: str = "proposed"  # proposed | accepted | rejected | applied | archived
    artifact_path: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    applied_policy_version: str | None = None
    # Guard fields: set by accept(); protect against immediate full-force activation.
    # activated_at: when accepted; dry_run_until: advisory-only window (+24h);
    # expires_at: block silently ignored after this point (+30d).
    activated_at: str | None = None
    dry_run_until: str | None = None
    expires_at: str | None = None


class PSSkill(BaseModel):
    skill_id: str = Field(default_factory=new_id)
    name: str
    scope: str  # research | validation | review | execution_helper
    description: str
    inputs_json: str = "[]"
    outputs_json: str = "[]"
    risk_level: str = "low"
    allowed_layers_json: str = "[]"
    forbidden_layers_json: str = "[]"
    created_from_lessons_json: str | None = None
    source_lesson_id: str | None = None
    version: int = 1
    artifact_path: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    status: str = "draft"  # draft | active | deprecated


class PSPolicyVersion(BaseModel):
    policy_version_id: str = Field(default_factory=new_id)
    parent_policy_version_id: str | None = None
    policy_type: str
    policy_json: str = "{}"
    policy_hash: str = ""
    diff_json: str | None = None
    created_by: str = "system"
    created_at: str = Field(default_factory=_utcnow)
    status: str = "draft"  # draft | active | deprecated | rejected


# ====================================================================
# Universe Generator Models
# ====================================================================


class UniverseAsset(BaseModel):
    id: str = Field(default_factory=new_id)
    venue: str
    market_type: str
    symbol: str
    base_asset: str | None = None
    quote_asset: str | None = None
    status: str = "active"
    min_notional: float | None = None
    price_precision: int | None = None
    amount_precision: int | None = None
    first_seen_at: str = Field(default_factory=_utcnow)
    last_seen_at: str = Field(default_factory=_utcnow)
    is_blacklisted: bool = False
    blacklist_reason: str | None = None
    metadata: str = "{}"


class UniverseFeatureSnapshot(BaseModel):
    id: str = Field(default_factory=new_id)
    asset_id: str
    ts: str = Field(default_factory=_utcnow)
    quote_volume_24h: float | None = None
    volume_1h: float | None = None
    spread_bps: float | None = None
    depth_10bps_usd: float | None = None
    atr_pct: float | None = None
    rsi_14: float | None = None
    rsi_15m: float | None = None
    atr_15m_pct: float | None = None
    return_15m: float | None = None
    last_candle_range_atr_15m: float | None = None
    return_1h: float | None = None
    return_4h: float | None = None
    return_24h: float | None = None
    funding_rate: float | None = None
    open_interest: float | None = None
    oi_delta_1h: float | None = None
    long_short_ratio: float | None = None
    top_trader_position_ratio: float | None = None
    data_freshness_sec: int | None = None
    btc_beta: float | None = None
    taker_buy_ratio: float | None = None
    vol_ratio: float | None = None
    relative_strength_24h: float | None = None
    oi_price_divergence: int | None = None  # +1=hidden_accumulation, -1=weak_rally, 0=neutral
    rsi_4h: float | None = None
    atr_4h_pct: float | None = None
    bb_width_4h: float | None = None
    vol_ratio_4h: float | None = None
    bb_squeeze: bool | None = None
    technical_algorithm_version: str | None = None
    technical_snapshot_manifest: str = "{}"
    raw: str = "{}"


class UniverseScannerRun(BaseModel):
    id: str = Field(default_factory=new_id)
    started_at: str = Field(default_factory=_utcnow)
    completed_at: str | None = None
    status: str = "running"
    policy_id: str | None = None
    assets_scanned: int = 0
    assets_passed: int = 0
    snapshot_id: str | None = None
    errors: str = "[]"
    metrics: str = "{}"


class UniverseSnapshot(BaseModel):
    id: str = Field(default_factory=new_id)
    created_at: str = Field(default_factory=_utcnow)
    market_type: str = "crypto"
    source_kind: Literal["universe", "serenity"] = "universe"
    policy_id: str | None = None
    status: str = "active"
    expires_at: str | None = None
    summary: str = "{}"


class UniverseMember(BaseModel):
    """Durable Universe Candidate.

    The physical SQLite table remains ``universe_members`` for compatibility;
    semantically each row is a Candidate selected by Universe refresh, not an
    Opportunity.
    """

    id: str = Field(default_factory=new_id)
    snapshot_id: str
    asset_id: str
    # Additive exact-instrument fact for active Candidate planning. Legacy rows
    # remain NULL and are unavailable to the planner rather than venue-guessed.
    venue: str | None = None
    symbol: str
    market_type: str = "spot"
    rank: int
    total_score: float
    opportunity_score: float | None = None
    tradability_score: float | None = None
    liquidity_score: float | None = None
    estimated_cost: float | None = None
    crowding_risk: float | None = None
    late_entry_penalty: float | None = None
    scanner_family: str | None = None
    scanner_direction: str | None = None
    regime_tag: str | None = None
    # JSON dict {family: score} of all 5+ scanner families' scores for this member
    # (Phase 2A+.12). Enables ensemble/consensus analysis beyond the single winning
    # family; without persistence the run_all_scanners all_scores dict was discarded.
    all_scores: str = "{}"
    quote_volume_24h: float | None = None
    spread_bps: float | None = None
    ttl_sec: int = 3600
    expires_at: str = Field(default_factory=_utcnow)
    inclusion_reason: str = "[]"
    risk_flags: str = "[]"
    execution_eligible: bool = True
    entry_price_usd: float | None = None
    learning_candidate_key: str | None = None
    candidate_selection_policy: Literal[
        "legacy_total_score_v1", "candidate_selection_utility_v1", "serenity_research_v1"
    ] = "legacy_total_score_v1"
    candidate_selection_policy_epoch: str | None = None
    candidate_selection_utility_pct: float | None = None
    opportunity_mechanism: Literal[
        "continuation", "reversal", "positioning_forced_flow", "information_catalyst"
    ] | None = None
    opportunity_mechanism_version: str | None = None
    opportunity_source_component: str | None = None
    opportunity_source_cutoff_at: str | None = None
    opportunity_evidence_hash: str | None = Field(default=None, min_length=64, max_length=64)
    candidate_opportunity_context_version: Literal["candidate-opportunity-context/v1"] | None = None
    candidate_opportunity_context_json: str | None = None
    candidate_opportunity_context_hash: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def _validate_candidate_selection_evidence(self):
        from aletheia.universe.candidate_opportunity import CandidateOpportunityContext

        target = self.candidate_selection_policy in {"candidate_selection_utility_v1", "serenity_research_v1"}
        mechanism_evidence = (
            self.opportunity_mechanism_version,
            self.opportunity_source_component,
            self.opportunity_source_cutoff_at,
            self.opportunity_evidence_hash,
        )
        if target and (
            not str(self.candidate_selection_policy_epoch or "").strip()
            or (self.candidate_selection_utility_pct is not None
                and not math.isfinite(self.candidate_selection_utility_pct))
            or any(not str(value or "").strip() for value in mechanism_evidence)
        ):
            raise ValueError("target Candidate selection evidence is incomplete")
        if not target and (
            self.candidate_selection_policy_epoch is not None
            or self.candidate_selection_utility_pct is not None
            or any(value is not None for value in (
                self.opportunity_mechanism_version,
                self.opportunity_source_component,
                self.opportunity_evidence_hash,
            ))
        ):
            raise ValueError("legacy Candidate cannot carry target selection evidence")
        context_fields = (
            self.candidate_opportunity_context_version,
            self.candidate_opportunity_context_json,
            self.candidate_opportunity_context_hash,
        )
        if any(value is not None for value in context_fields):
            if any(value is None for value in context_fields):
                raise ValueError("Candidate Opportunity context is incomplete")
            try:
                context = CandidateOpportunityContext.model_validate_json(
                    self.candidate_opportunity_context_json
                )
            except ValueError as exc:
                raise ValueError("Candidate Opportunity context is invalid") from exc
            if (
                context.candidate_id != self.id
                or context.version != self.candidate_opportunity_context_version
                or context.context_hash != self.candidate_opportunity_context_hash
                or context.opportunity["primary_mechanism"] != self.opportunity_mechanism
                or context.candidate_selection["policy"] != self.candidate_selection_policy
                or context.candidate_selection.get("policy_epoch") != self.candidate_selection_policy_epoch
                or context.opportunity["direction"] != self.scanner_direction
                or (target and (
                    context.opportunity.get("economic_utility_pct") != self.candidate_selection_utility_pct
                    or context.opportunity["mechanism_version"] != self.opportunity_mechanism_version
                    or context.opportunity["source_component_id"] != self.opportunity_source_component
                ))
                or (
                    self.opportunity_source_cutoff_at is not None
                    and self.opportunity_source_cutoff_at != context.source["source_cutoff_at"]
                )
            ):
                raise ValueError("Candidate Opportunity context identity mismatch")
        elif target:
            raise ValueError("target Candidate Opportunity context is missing")
        elif self.opportunity_source_cutoff_at is not None:
            raise ValueError("Candidate source cutoff requires validated opportunity context")
        return self


Candidate = UniverseMember

class CandidatePlanningGeneration(_Provenanced):
    """Durable ownership/result state for one thesis-level planning generation."""

    id: str = Field(default_factory=new_id)
    planning_key: str = Field(min_length=64, max_length=64)
    planning_identity: str = Field(min_length=64, max_length=64)
    candidate_id: str
    last_candidate_id: str
    venue: str
    symbol: str
    market_type: Literal["spot", "perp"]
    candidate_family: str
    direction: Literal["long", "short"]
    opportunity_mechanism: Literal[
        "continuation", "reversal", "positioning_forced_flow", "information_catalyst"
    ] | None = None
    candidate_selection_policy: Literal[
        "legacy_total_score_v1", "candidate_selection_utility_v1", "serenity_research_v1"
    ] | None = None
    candidate_selection_policy_epoch: str | None = None
    mechanism_version: str | None = None
    is_current: bool = True
    status: Literal["pending", "planning", "planned", "no_plan", "unavailable", "superseded"] = "pending"
    result_kind: Literal["entry_plan", "no_plan", "unavailable", "stale_result"] | None = None
    owner_token: str | None = None
    claim_expires_at: str | None = None
    retry_not_before: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    result_json: str | None = None
    entry_plan_id: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)
    completed_at: str | None = None
    attention_status: Literal["active", "terminal"] = "active"
    attention_started_at: str
    last_requalified_at: str | None = None
    requalification_source_json: str | None = None
    soft_expires_at: str
    hard_expires_at: str
    attention_max_age_sec: int = Field(default=3600, ge=300, le=86_400)
    attention_policy_version: Literal["candidate-attention/v1"] = "candidate-attention/v1"
    attention_terminal_at: str | None = None
    attention_terminal_reason: str | None = None
    planner_input_json: str | None = None
    planner_input_hash: str | None = Field(default=None, min_length=64, max_length=64)
    validated_hypothesis_json: str | None = None
    cycle_number: int = Field(default=1, ge=1)
    previous_generation_id: str | None = None
    cycle_reason: str = "initial_candidate"
    requalification_candidate_id: str | None = None
    planning_started_at: str | None = None
    last_stage: str = "planning_queued"
    model_invoked: bool = False
    model_invoked_at: str | None = None
    compiler_invoked: bool = False
    compiler_invoked_at: str | None = None
    outcome_class: Literal[
        "entry_plan", "no_plan", "planning_blocked", "planning_failed",
        "planning_unavailable",
    ] | None = None
    outcome_reason: str | None = None
    entry_behavior_fingerprint: str | None = None
    schema_fingerprint: str | None = None

    @model_validator(mode="after")
    def _mechanism_identity_is_complete(self):
        fields = (
            self.opportunity_mechanism,
            self.candidate_selection_policy,
            self.mechanism_version,
        )
        if any(value is not None for value in (*fields, self.candidate_selection_policy_epoch)):
            if any(value is None or not str(value).strip() for value in fields):
                raise ValueError("mechanism planning identity is incomplete")
            if (
                self.candidate_selection_policy == "candidate_selection_utility_v1"
                and not str(self.candidate_selection_policy_epoch or "").strip()
            ):
                raise ValueError("target mechanism planning epoch is missing")
        return self


class EntryPlanRecord(_Provenanced):
    """Storage projection for immutable EntryPlan facts plus monotonic lifecycle."""

    id: str
    planning_generation_id: str
    candidate_id: str
    planning_key: str
    venue: str
    symbol: str
    market_type: Literal["spot", "perp"]
    direction: Literal["long", "short"]
    execution_mode: Literal["RESTING_MAKER", "TRIGGERED"]
    plan_json: str
    plan_hash: str = Field(min_length=64, max_length=64)
    status: Literal[
        "waiting",
        "active",
        "risk_reserved",
        "triggered",
        "ordered",
        "partially_filled",
        "filled",
        "invalidated",
        "expired",
        "cancelled",
        "failed",
    ] = "waiting"
    order_id: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)
    terminal_at: str | None = None
    terminal_reason: str | None = None
    monitoring_state: str | None = None
    monitoring_evidence_json: str | None = None
    monitoring_evidence_hash: str | None = None


class UniverseSetup(_Provenanced):
    """One exact family setup generation waiting for EntryEligibility."""

    id: str = Field(default_factory=new_id)
    snapshot_id: str
    member_id: str
    asset_id: str
    venue: str
    symbol: str
    market_type: str
    direction: Literal["long", "short"]
    rank: int = Field(ge=1)
    material_fingerprint: str
    trigger_spec_hash: str
    trigger_spec_version: str = "universe-entry-eligibility/v1"
    scanner_family: str = "momentum"
    motive_evidence: str = "{}"
    funding_rate: float | None = None
    funding_observed_at: str | None = None
    regime_tag: str | None = None
    breakout_level: float | None = Field(default=None, gt=0)
    atr: float | None = Field(default=None, gt=0)
    pullback_band: str | None = None
    entry_zone: str | None = None
    invalidation_price: float | None = Field(default=None, gt=0)
    state: Literal[
        "pending_authorization",
        "armed",
        "pullback_seen",
        "triggering",
        "executed",
        "refused",
        "expired",
        "invalidated",
    ] = "pending_authorization"
    opportunity_id: str | None = None
    proposal_id: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)
    valid_until: str


class UniverseForwardOutcome(BaseModel):
    id: str = Field(default_factory=new_id)
    outcome_candidate_key: str | None = None
    member_id: str
    snapshot_id: str
    symbol: str
    venue: str | None = None
    market_type: str = "spot"
    scanner_family: str | None = None
    # Trade direction the scanner family implied at selection ("long" | "short").
    # None for legacy rows / members with no inferable direction; direction-aware
    # win-rate calibration excludes those rows because their side is unknown.
    direction: str | None = None
    horizon: str  # "15m" | "1h" | "4h" | "24h"
    entry_price_usd: float
    entry_spread_bps: float | None = None
    funding_rate_at_entry: float | None = None
    entry_ts: str = Field(default_factory=_utcnow)
    due_at: str
    settled_at: str | None = None
    exit_price_usd: float | None = None
    # Legacy raw price move retained for compatibility. New consumers use the
    # explicit gross/directional/net fields below.
    forward_return_pct: float | None = None
    gross_return_pct: float | None = None
    directional_return_pct: float | None = None
    estimated_fee_pct: float | None = None
    estimated_spread_slippage_pct: float | None = None
    estimated_funding_pct: float | None = None
    net_return_pct: float | None = None
    min_tradable_edge_pct: float | None = None
    min_cost_covering_edge_pct: float | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    settlement_price_source: str | None = None
    settlement_lag_sec: float | None = None
    cost_model_version: str | None = None
    status: str = "pending"  # "pending" | "settled" | "failed"


class UniversePolicy(BaseModel):
    id: str = Field(default_factory=new_id)
    created_at: str = Field(default_factory=_utcnow)
    created_by: str = "system"
    active: bool = False
    market_type: str = "crypto"
    family_quota: str = "{}"
    scoring_weights: str = "{}"
    filters: str = "{}"
    ttl_policy: str = "{}"
    explore_ratio: float = 0.2
    max_weight_step: float = 0.15
    change_reason: str | None = None
    previous_policy_id: str | None = None


class UniverseTwinShadowBatch(BaseModel):
    scanner_run_id: str
    authority_snapshot_id: str | None = None
    experiment_id: str | None = None
    record_version: str
    authority_behavior_version: str
    shadow_behavior_version: str
    allocation_replay_contract: str
    policy_epoch_id: str | None = None
    behavior_contract_hash: str | None = None
    assignment_unit_key: str | None = None
    assignment_contract_version: str | None = None
    assignment_arm: str | None = None
    assignment_propensity: float | None = None
    treatment_coefficient: float | None = None
    treatment_policy: str | None = None
    control_policy: str | None = None
    primary_horizon: str | None = None
    outcome_contract_version: str | None = None
    feature_source_fingerprint: str
    availability_profile_id: str | None = None
    metric_version: str
    allocator_random_state_json: str
    policy_json: str
    allocation_context_json: str
    candidate_count: int
    promotion_eligible: bool = False
    blocking_reasons_json: str = "[]"
    batch_hash: str
    captured_at: str = Field(default_factory=_utcnow)


class UniverseTwinShadowEvidence(BaseModel):
    id: str = Field(default_factory=new_id)
    scanner_run_id: str
    candidate_key: str
    candidate_index: int
    feature_snapshot_id: str | None = None
    asset_id: str | None = None
    symbol: str
    market_type: str
    experiment_id: str | None = None
    policy_epoch_id: str | None = None
    behavior_contract_hash: str | None = None
    availability_profile_id: str | None = None
    assignment_unit_key: str | None = None
    assignment_arm: str | None = None
    assignment_propensity: float | None = None
    treatment_coefficient: float | None = None
    primary_horizon: str | None = None
    outcome_contract_version: str | None = None
    metric_version: str | None = None
    outcome_candidate_key: str | None = None
    authority_total_score: float
    shadow_point_score: float | None = None
    shadow_pessimistic_score: float | None = None
    authority_rank: int | None = None
    shadow_point_rank: int | None = None
    shadow_pessimistic_rank: int | None = None
    fine_filter_passed: bool | None = None
    authority_selected: bool | None = None
    shadow_point_selected: bool | None = None
    shadow_pessimistic_selected: bool | None = None
    assigned_policy_selected: bool | None = None
    shadow_authority_applied: bool = False
    promotion_eligible: bool = False
    shadow_valid: bool = False
    compact_output_json: str
    input_json: str
    record_hash: str
    captured_at: str = Field(default_factory=_utcnow)


class UniverseRelativeRankShadowBatch(BaseModel):
    scanner_run_id: str
    authority_snapshot_id: str | None = None
    experiment_id: str
    policy_epoch_id: str
    behavior_contract_hash: str
    assignment_unit_key: str
    assignment_contract_version: str
    assignment_arm: str
    assignment_propensity: float
    treatment_coefficient: float
    treatment_policy: str
    control_policy: str
    primary_horizon: str
    outcome_contract_version: str
    metric_version: str
    feature_source_fingerprint: str
    record_version: str
    behavior_version: str
    min_candidate_count: int
    candidate_count: int
    eligible_count: int
    available: bool
    promotion_eligible: bool = False
    blocking_reasons_json: str = "[]"
    records_json: str
    batch_hash: str
    captured_at: str = Field(default_factory=_utcnow)


class PiDecision(_Provenanced):
    id: str = Field(default_factory=new_id)
    created_at: str = Field(default_factory=_utcnow)
    symbol: str | None = None
    direction: str | None = None
    # Self-contained instrument identity for pending and final Operator rows.
    # Legacy rows remain NULL and may still recover it from the source opportunity.
    market_type: str | None = None
    action: str = "skip"
    conviction: int = 0
    reasoning: str = ""
    proposal_id: str | None = None
    # Durable outcome of the proposal-submission attempt. Stable reason codes
    # explain NULL proposal_id without persisting provider exception text.
    proposal_status: str | None = None
    proposal_reason: str | None = None
    # A13 (LEARNING_LOOP_AUDIT / PL-PT-04): link a signal_pending decision back to
    # its source opportunity so the attribution chain is complete on the signal side
    # (mirrors the R0 opportunity_id fix on the proposal side).
    opportunity_id: str | None = None
    # Phase 2 (Serenity Option A): which table opportunity_id points at. Both
    # opportunities.id (market_observer) and serenity_opportunities.id are bare
    # uuid4().hex[:12] with no type tag, so the id alone is cross-table ambiguous.
    # 'market_observer' → opportunities.id; 'serenity' → serenity_opportunities.id.
    signal_source: str = "market_observer"
    debate_ran: bool = False
    # Debate trace fields are audit-only. NULL means no debate ran or legacy row.
    debate_initial_conviction: int | None = None
    debate_bull_confidence: int | None = None
    debate_bear_confidence: int | None = None
    debate_adjustment: int | None = None
    # P2 / S4 (2026-06-28, AI Berkshire forced-verdict): explicit counter-argument from PI.
    bear_case: str = ""
    # P1 (2026-06-28, AI Berkshire belief decomposition): JSON-encoded list of
    # critical assumptions this decision rests on. Empty JSON array = not provided.
    key_assumptions: str = "[]"
    # One bundle-level manifest for overlapping Learning prompt components.
    learning_manifest_json: str = "{}"
    # Compact source identities used for this decision. Never stores prompts or
    # raw external payloads.
    evidence_manifest_json: str = "{}"
    # Conditional-entry decisions do not point at an Opportunity because an
    # Opportunity does not exist until T0 materialization.
    setup_id: str | None = None


class OperatorEvaluationEvent(BaseModel):
    """Append-only observation outside the candidate-trading state machine."""

    id: str = Field(default_factory=new_id)
    dedup_key: str
    event_kind: str
    trigger: str
    pending_unresolved_total: int
    pending_visible_count: int
    pending_oldest_created_at: str | None = None
    queue_snapshot_json: str = "{}"
    data_health_json: str = "{}"
    policy_epoch_id: str | None = None
    created_at: str = Field(default_factory=_utcnow)


class CapitalEvent(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    id: str = Field(default_factory=new_id)
    broker_id: str
    event_type: str  # "deposit" | "withdraw"
    amount_usdt: float = Field(gt=0)
    equity_at_event: float | None = None
    detected_at: str = Field(default_factory=_utcnow)
    note: str = ""


class OnchainSnapshot(BaseModel):
    """Aggregated on-chain / DeFi metric snapshot (DefiLlama).

    metric_type: "stablecoin_mcap" | "chain_tvl" | "dex_volume"
    """

    id: str = Field(default_factory=new_id)
    source: str  # "defillama"
    metric_type: str  # metric category
    total_usd: float | None = None  # aggregated USD value
    change_24h_pct: float | None = None  # 24h % change (None when unavailable)
    top_entries: str = "[]"  # JSON: list of top-N entries
    raw_summary: str = "{}"  # JSON: key scalar metrics
    observed_at: str = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _finite_nonnegative_total(self):
        if self.total_usd is not None and (not math.isfinite(float(self.total_usd)) or float(self.total_usd) < 0):
            raise ValueError("onchain total_usd must be finite and non-negative")
        return self
