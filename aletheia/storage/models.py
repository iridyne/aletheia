"""Pydantic models for storage entities."""

from datetime import datetime, timezone
from pydantic import BaseModel, Field
from uuid import uuid4


def new_id() -> str:
    return uuid4().hex[:12]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


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


class AgentRun(BaseModel):
    id: str = Field(default_factory=new_id)
    agent_name: str
    trigger_type: str = "scheduled"
    permission_mode: str = "Observe"
    started_at: str = Field(default_factory=_utcnow)
    ended_at: str | None = None
    status: str = "running"
    model_provider: str | None = None
    model_name: str | None = None
    tools_called: str = "[]"
    outputs_created: str = "[]"
    errors: str = "[]"
    cost_estimate: float | None = None


class Opportunity(BaseModel):
    id: str = Field(default_factory=new_id)
    symbol: str
    market: str
    direction_bias: str | None = None
    thesis: str | None = None
    confidence: str = "speculative"
    # Numeric calibration the LLM emits alongside the categorical `confidence`.
    # 0..100; None when the model didn't supply one — keeps older rows valid.
    confidence_score: int | None = None
    source_agent: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    status: str = "open"
    linked_strategy_id: str | None = None
    # Asset class: "crypto" | "equity" | "prediction". Default "crypto" keeps
    # older rows valid. paper_trader only acts on "crypto" opportunities.
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
    valid_until: str | None = None       # ISO timestamp — signal expiry
    entry_zone: str | None = None        # JSON: {"low": x, "high": y}
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
    settlement_price_source: str | None = None
    settlement_lag_sec: float | None = None
    cost_model_version: str | None = None
    btc_return_pct: float | None = None
    resolved_at: str | None = None


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
    entry_price_usd: float
    entry_spread_bps: float | None = None
    funding_rate_at_entry: float | None = None
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
    mfe_pct: float | None = None
    mae_pct: float | None = None
    settlement_price_source: str | None = None
    settlement_lag_sec: float | None = None
    cost_model_version: str | None = None
    status: str = "pending"


class TradeProposal(BaseModel):
    id: str = Field(default_factory=new_id)
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
    execution_mode: str = "paper_internal"
    agent_rationale: str | None = None
    # JSON snapshot captured at proposal/decision time for replay-stable paper
    # position attribution (BTC benchmark, regime, market context).
    attribution_snapshot: str | None = None
    status: str = "pending"
    # Provenance: "native" (Aletheia generated it) or "adopted" (synthetic chain the
    # reconciler built to bring a pre-existing/external exchange position under
    # management). See docs/DECISIONS.md (2026-06-12) / position_reconciler.
    source: str = "native"
    # Execution-level school discriminator (Phase 4): "short_term" (default) or
    # "serenity". Threaded from TradingDecision.signal_source so position_manager
    # can branch trailing-stop + max_hold behavior per school without touching the
    # risk engine. Default 'short_term' keeps every legacy proposal on the existing
    # path.
    trade_school: str = "short_term"
    created_at: str = Field(default_factory=_utcnow)
    # TaG hash chain (P0-1, 2026-06-29) — borrow from OpenAlice TradingGit.
    # commit_hash = SHA-256 of canonical JSON over immutable proposal fields
    # (everything except status + commit_hash itself) + parent_hash, sliced to
    # 16 hex chars (64-bit collision space). parent_hash links to the previous
    # row's commit_hash forming a global single chain. Both NULL on legacy rows
    # written before P0-1 landed. See docs/research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md.
    commit_hash: str | None = None
    parent_hash: str | None = None


class RiskDecision(BaseModel):
    id: str = Field(default_factory=new_id)
    proposal_id: str
    approved: bool = False
    risk_score: float = 0.0
    violations: str = "[]"
    allowed_execution_mode: str | None = None
    # The proposal's ORIGINAL execution_mode, preserved verbatim. Distinct from
    # allowed_execution_mode, which is downgraded to "paper_internal" on rejection
    # — that downgrade destroyed the only record of whether a REJECTED proposal
    # was a live order, making strategy_lifecycle's "live rejection = critical"
    # branch permanently dead (CR-RE-01). Keep both: allowed_* = what may execute,
    # proposal_execution_mode = what was requested.
    proposal_execution_mode: str | None = None
    message: str | None = None
    created_at: str = Field(default_factory=_utcnow)


class Order(BaseModel):
    id: str = Field(default_factory=new_id)
    proposal_id: str | None = None
    risk_decision_id: str | None = None
    symbol: str
    market_type: str
    direction: str
    order_type: str
    quantity: float
    price: float | None = None
    execution_mode: str
    exchange_order_id: str | None = None
    status: str = "pending"
    source: str = "native"  # "native" | "adopted" — see TradeProposal.source
    created_at: str = Field(default_factory=_utcnow)
    filled_at: str | None = None
    error_reason: str | None = None


class Fill(BaseModel):
    id: str = Field(default_factory=new_id)
    order_id: str
    fill_price: float
    fill_quantity: float
    fee: float = 0.0
    fee_currency: str = "USDT"
    # Provenance of the fee figure: "estimated" (flat-rate taker fee computed by the
    # broker at fill time — paper + all exchange brokers do this) or "reconciled"
    # (reconciliation backfilled the exchange's real fillId fee). Defaults estimated
    # so an estimate is never silently mistaken for an actual exchange fee.
    fee_source: str = "estimated"
    filled_at: str = Field(default_factory=_utcnow)


class Position(BaseModel):
    id: str = Field(default_factory=new_id)
    symbol: str
    market_type: str
    direction: str
    quantity: float
    entry_price: float
    current_price: float | None = None
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    execution_mode: str
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
    status: str = "open"
    stop_price: float | None = None
    take_profit_price: float | None = None
    close_reason: str | None = None
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
    decision_reasoning: str | None = None
    entry_fee: float | None = None
    exit_fee: float | None = None
    slippage_cost: float | None = None
    # Execution-level school discriminator (Phase 4). Mirrors TradeProposal.trade_school
    # — defaults 'short_term' so legacy + adopted positions stay on the 72h cap + ATR
    # trailing path. 'serenity' positions skip ATR trailing and use
    # cfg.serenity.max_hold_days * 24 as their hold cap.
    trade_school: str = "short_term"


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
    # NOT parsed or executed by paper_trader. Trade direction derives from
    # opportunity.direction_bias, not from these rules.
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
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


class MarketSnapshot(BaseModel):
    id: str = Field(default_factory=new_id)
    symbol: str        # canonical: BTC/USDT
    market_type: str   # "spot" | "perp" | "futures"
    exchange: str = "" # source exchange: "binance", "bybit", "bitget", …
    price: float
    volume_24h: float | None = None
    change_pct_24h: float | None = None
    high_24h: float | None = None
    low_24h: float | None = None
    provider: str | None = None
    snapshot_at: str = Field(default_factory=_utcnow)


class PortfolioSnapshot(BaseModel):
    id: str = Field(default_factory=new_id)
    total_value_usdt: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    positions_count: int = 0
    execution_mode: str = "paper_internal"
    exchange_equity_usdt: float | None = None
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
    status: str = "unknown"      # pass | warn | fail
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
    opportunity_id: str
    outcome: str
    failure_bucket: str | None = None
    score_delta: int | None = None
    attribution: str | None = None
    summary: str
    created_by: str = "system"
    created_at: str = Field(default_factory=_utcnow)


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


class PSGatewayLock(BaseModel):
    lock_name: str
    owner_id: str
    lease_until: str
    heartbeat_at: str = Field(default_factory=_utcnow)


class PSReconciliationDiff(BaseModel):
    diff_type: str
    severity: str  # info | warning | critical
    local_ref: dict | None = None
    remote_ref: dict | None = None
    message: str
    recommended_action: str


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


class PSExecutionIntent(BaseModel):
    execution_intent_id: str = Field(default_factory=new_id)
    proposal_id: str
    mode: str  # paper | live
    status: str = "draft"  # draft | approved | submitted | cancelled | failed
    risk_check_json: str | None = None
    unlock_token_hash: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


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


class PSOrder(BaseModel):
    order_id: str = Field(default_factory=new_id)
    intent_id: str
    mode: str
    venue: str
    venue_order_id: str | None = None
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: str
    price: str | None = None
    status: str = "submitted"
    submitted_at: str | None = None
    acknowledged_at: str | None = None
    updated_at: str = Field(default_factory=_utcnow)
    raw_json: str | None = None


class PSFill(BaseModel):
    fill_id: str = Field(default_factory=new_id)
    order_id: str | None = None
    mode: str
    venue: str
    venue_trade_id: str | None = None
    client_order_id: str | None = None
    symbol: str
    side: str
    quantity: str
    price: str
    fee: str | None = None
    fee_currency: str | None = None
    filled_at: str = Field(default_factory=_utcnow)
    raw_json: str | None = None


class PSPosition(BaseModel):
    position_id: str = Field(default_factory=new_id)
    mode: str
    strategy_id: str
    proposal_id: str | None = None
    venue: str
    symbol: str
    side: str  # long | short | flat
    quantity: str
    avg_entry_price: str | None = None
    realized_pnl: str | None = None
    unrealized_pnl: str | None = None
    status: str = "open"  # open | closed | orphan | unknown
    opened_at: str | None = None
    closed_at: str | None = None
    updated_at: str = Field(default_factory=_utcnow)
    raw_json: str | None = None


class PSCheckpoint(BaseModel):
    checkpoint_id: str = Field(default_factory=new_id)
    run_id: str
    step_name: str
    state_json: str = "{}"
    artifact_path: str | None = None
    created_at: str = Field(default_factory=_utcnow)


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
    return_1h: float | None = None
    return_4h: float | None = None
    return_24h: float | None = None
    funding_rate: float | None = None
    open_interest: float | None = None
    oi_delta_1h: float | None = None
    data_freshness_sec: int | None = None
    btc_beta: float | None = None
    taker_buy_ratio: float | None = None
    vol_ratio: float | None = None
    relative_strength_24h: float | None = None
    oi_price_divergence: int | None = None  # +1=hidden_accumulation, -1=weak_rally, 0=neutral
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
    policy_id: str | None = None
    status: str = "active"
    expires_at: str | None = None
    summary: str = "{}"


class UniverseMember(BaseModel):
    id: str = Field(default_factory=new_id)
    snapshot_id: str
    asset_id: str
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


class UniverseForwardOutcome(BaseModel):
    id: str = Field(default_factory=new_id)
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


class PiDecision(BaseModel):
    id: str = Field(default_factory=new_id)
    created_at: str = Field(default_factory=_utcnow)
    symbol: str | None = None
    direction: str | None = None
    action: str = "skip"
    conviction: int = 0
    reasoning: str = ""
    proposal_id: str | None = None
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


class CapitalEvent(BaseModel):
    id: str = Field(default_factory=new_id)
    broker_id: str
    event_type: str  # "deposit" | "withdraw"
    amount_usdt: float
    equity_at_event: float | None = None
    detected_at: str = Field(default_factory=_utcnow)
    note: str = ""


class PaperAccountEvent(BaseModel):
    """Immutable cash-account event for the local paper simulator only."""
    id: str = Field(default_factory=new_id)
    action: str
    amount_usdt: float
    cash_balance_before_usdt: float
    cash_balance_after_usdt: float
    actor: str = "system"
    source: str = "paper_account"
    position_id: str | None = None
    note: str = ""
    created_at: str = Field(default_factory=_utcnow)


class OnchainSnapshot(BaseModel):
    """Aggregated on-chain / DeFi metric snapshot (DefiLlama).

    metric_type: "stablecoin_mcap" | "chain_tvl" | "dex_volume"
    """
    id: str = Field(default_factory=new_id)
    source: str                          # "defillama"
    metric_type: str                     # metric category
    total_usd: float | None = None       # aggregated USD value
    change_24h_pct: float | None = None  # 24h % change (None when unavailable)
    top_entries: str = "[]"              # JSON: list of top-N entries
    raw_summary: str = "{}"             # JSON: key scalar metrics
    observed_at: str = Field(default_factory=_utcnow)
