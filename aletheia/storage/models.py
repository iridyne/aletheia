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
    execution_mode: str = "paper_internal"
    agent_rationale: str | None = None
    status: str = "pending"
    # Provenance: "native" (Aletheia generated it) or "adopted" (synthetic chain the
    # reconciler built to bring a pre-existing/external exchange position under
    # management). See docs/DECISIONS.md (2026-06-12) / position_reconciler.
    source: str = "native"
    created_at: str = Field(default_factory=_utcnow)


class RiskDecision(BaseModel):
    id: str = Field(default_factory=new_id)
    proposal_id: str
    approved: bool = False
    risk_score: float = 0.0
    violations: str = "[]"
    allowed_execution_mode: str | None = None
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


class Fill(BaseModel):
    id: str = Field(default_factory=new_id)
    order_id: str
    fill_price: float
    fill_quantity: float
    fee: float = 0.0
    fee_currency: str = "USDT"
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
    # Causal-chain back-links (trust §1.1): which proposal/order opened this position.
    # Nullable — older rows and positions opened before this field predate it.
    proposal_id: str | None = None
    order_id: str | None = None
    status: str = "open"
    stop_price: float | None = None
    take_profit_price: float | None = None
    close_reason: str | None = None
    source: str = "native"  # "native" | "adopted" — see TradeProposal.source
    opened_at: str = Field(default_factory=_utcnow)
    closed_at: str | None = None


class StrategyRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    description: str | None = None
    state: str = "draft"
    parameters: str = "{}"
    under_review: bool = False
    review_notes: str | None = None
    review_updated_at: str | None = None
    paper_trading_paused: bool = False
    paper_pause_reason: str | None = None
    paper_paused_at: str | None = None
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
    tier: str = "speculative"
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


class SerenityOpportunity(BaseModel):
    id: str = Field(default_factory=new_id)
    theme_id: str
    symbol: str
    direction: str
    thesis: str | None = None
    evidence_tier: str = "speculative"
    stage: str = "research"
    average_mode: str = "none"
    action_intent: str = "watch"
    status: str = "active"
    risk_flags: str = "[]"
    risk_metrics: str = "{}"
    confidence_score: int | None = None
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


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
