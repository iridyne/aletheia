export interface TradeProposal {
  id: string
  strategy_id: string | null
  opportunity_id: string | null
  symbol: string
  market_type: string
  direction: string
  order_type: string
  quantity: number | null
  price: number | null
  leverage: number | null
  stop_price: number | null
  execution_mode: string
  agent_rationale: string | null
  status: string
  created_at: string
}

export interface RiskDecision {
  id: string
  proposal_id: string
  approved: boolean
  risk_score: number | null
  violations: string | null
  allowed_execution_mode: string | null
  message: string | null
  created_at: string
}

export interface Order {
  id: string
  proposal_id: string
  risk_decision_id: string | null
  symbol: string
  market_type: string
  direction: string
  order_type: string
  quantity: number
  price: number | null
  execution_mode: string
  exchange_order_id: string | null
  status: string
  created_at: string
  filled_at: string | null
}

export interface Fill {
  id: string
  order_id: string
  fill_price: number
  fill_quantity: number
  fee: number | null
  fee_currency: string | null
  filled_at: string
}

export interface Position {
  id: string
  symbol: string
  market_type: string
  direction: string
  quantity: number
  entry_price: number
  current_price: number | null
  unrealized_pnl: number | null
  realized_pnl: number | null
  execution_mode: string
  strategy_id: string | null
  status: string
  stop_price: number | null
  take_profit_price: number | null
  close_reason: string | null
  opened_at: string
  closed_at: string | null
}

export interface Opportunity {
  id: string
  symbol: string
  market: string
  direction_bias: string
  thesis: string | null
  confidence: string | null
  confidence_score: number | null
  source_agent: string | null
  created_at: string
  status: string
  linked_strategy_id: string | null
}

export interface StrategyRecord {
  id: string
  name: string
  description: string | null
  state: string
  parameters: string | null
  created_at: string
  updated_at: string | null
}

export interface KnowledgeItem {
  id: string
  item_type: string
  title: string
  file_path: string | null
  strategy_id: string | null
  confidence: number | null
  status: string
  tags: string | null
  created_at: string
  updated_at: string | null
}

export interface AgentRun {
  id: string
  agent_name: string
  trigger_type: string
  permission_mode: string | null
  started_at: string
  ended_at: string | null
  status: string
  model_provider: string | null
  model_name: string | null
  tools_called: string | null
  outputs_created: string | null
  errors: string | null
  cost_estimate: number | null
}

export interface MarketSnapshot {
  id: string
  symbol: string
  market_type: string
  price: number
  volume_24h: number | null
  change_pct_24h: number | null
  snapshot_at: string
}

export interface PortfolioSnapshot {
  id: string
  total_value_usdt: number
  unrealized_pnl: number
  realized_pnl: number
  positions_count: number
  execution_mode: string
  snapshot_at: string
}

export interface PortfolioSummary {
  total_value_usdt: number | null
  today_pnl: number | null
  unrealized_pnl: number | null
  realized_pnl: number | null
  exposure_by_symbol: Record<string, number> | null
}

export interface RiskStatus {
  kill_switch_active: boolean
  live_state: string
  permission_mode: string
  latest_risk_decision: RiskDecision | null
  status: string
  violations: string[]
}

export interface SystemStatus {
  gateway_running: boolean
  paused: boolean
  exchange: string
  exchange_mode: string
  live_state: string
  permission_mode: string
  scheduler_enabled: boolean
  version: string
}

export interface AuditEvent {
  id: string
  actor: string
  source: string
  event_type: string
  entity_type?: string
  entity_id?: string
  details?: string
  created_at: string
}
