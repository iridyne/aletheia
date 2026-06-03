"""Pydantic models for storage entities."""

from datetime import datetime
from pydantic import BaseModel, Field
from uuid import uuid4


def new_id() -> str:
    return uuid4().hex[:12]


class AuditEvent(BaseModel):
    id: str = Field(default_factory=new_id)
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
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
    permission_mode: str = "Research"
    started_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
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
    confidence: str = "medium_confidence"
    source_agent: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    status: str = "open"
    linked_strategy_id: str | None = None


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
    execution_mode: str = "paper_internal"
    agent_rationale: str | None = None
    status: str = "pending"
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class RiskDecision(BaseModel):
    id: str = Field(default_factory=new_id)
    proposal_id: str
    approved: bool = False
    risk_score: float = 0.0
    violations: str = "[]"
    allowed_execution_mode: str | None = None
    message: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


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
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    filled_at: str | None = None


class Fill(BaseModel):
    id: str = Field(default_factory=new_id)
    order_id: str
    fill_price: float
    fill_quantity: float
    fee: float = 0.0
    fee_currency: str = "USDT"
    filled_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


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
    status: str = "open"
    opened_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    closed_at: str | None = None


class StrategyRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    description: str | None = None
    state: str = "draft"
    parameters: str = "{}"
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class KnowledgeItem(BaseModel):
    id: str = Field(default_factory=new_id)
    item_type: str
    title: str
    file_path: str | None = None
    confidence: str = "medium_confidence"
    status: str = "active"
    tags: str = "[]"
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
