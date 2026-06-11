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
