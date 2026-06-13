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
  error_reason: string | null
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
  source?: string  // "native" | "adopted"
  opened_at: string
  closed_at: string | null
}

export type AssetClass = 'crypto' | 'equity' | 'prediction'

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
  asset_class: AssetClass
  metadata: string | null
}

export interface PredictionMarket {
  id: string
  event_title: string
  yes_probability: number | null
  probability_change_24h: number | null
  volume_24h: number | null
  liquidity: number | null
  resolution_at: string | null
  category: string
  direction_bias: string | null
  confidence: string | null
  confidence_score: number | null
  status: string | null
  created_at: string | null
  market_id: string
  enable_orderbook: boolean
  source: string
}

export interface PredictionObserverRun {
  id: string
  started_at: string
  completed_at: string | null
  status: string
  markets_scanned: number
  opportunities_created: number
  errors: string[]
  categories: string[]
}

export interface PredictionStatus {
  last_run: PredictionObserverRun | null
  categories: string[]
  total_opportunities: number
  open_opportunities: number
  observer_interval_minutes: number
}

export interface PredictionEvent {
  id: string
  source: string
  external_event_id: string
  slug: string | null
  title: string
  description: string | null
  category: string | null
  status: string
  starts_at: string | null
  ends_at: string | null
  resolution_at: string | null
  liquidity: number | null
  volume_24h: number | null
  created_at: string
}

export interface PredictionMarketDetail {
  id: string
  event_id: string | null
  external_market_id: string
  question: string
  yes_probability: number | null
  no_probability: number | null
  probability_change_24h: number | null
  enable_orderbook: boolean | number
  liquidity: number | null
  volume_24h: number | null
  status: string
}

export interface ProbabilitySnapshot {
  ts: string
  yes_probability: number
  no_probability: number | null
  liquidity: number | null
}

export interface PredictionClaim {
  id: string
  market_id: string
  run_id: string | null
  claim_text: string
  claim_type: string
  direction: string | null
  confidence: number
  source: string
  created_at: string
}

export interface PredictionPreferences {
  categories: string[]
  observer_interval_minutes: number
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

// One exchange position as reported by the live account view.
export interface ExchangePosition {
  symbol: string
  side: string
  quantity: number
  entry_price: number
  unrealized_pnl: number
  leverage?: number | null
  mark_price?: number
}

// One classified position from /api/positions/reconciliation.
export interface ReconcileItem {
  symbol: string
  direction: string
  classification: 'managed' | 'external' | 'orphaned'
  local: Position | null
  exchange: ExchangePosition | null
  qty_drift: number | null
}

export interface PositionsReconciliation {
  sync_status: 'ok' | 'stale' | 'unconfigured' | 'error'
  counts: { managed: number; external: number; orphaned: number }
  managed: ReconcileItem[]
  external: ReconcileItem[]
  orphaned: ReconcileItem[]
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

export interface OperatorStatus {
  agent_id: string
  status: string
  mode: string
  permission_mode: string
  is_busy: boolean
  current_task_id: string | null
  current_intent: string | null
  queue_size: number
  last_seen_at: string | null
  last_success_at: string | null
  last_error: string | null
  model_provider: string | null
  session_id: string
  active_branch_id?: string | null
  active_branch_kind?: string | null
  active_branch_title?: string | null
  stale: boolean
  stale_after_sec: number
  heartbeat_age_sec?: number
}

export interface OperatorTask {
  id: string
  task_type: string
  status: string
  priority: string
  created_by: string
  assigned_to: string
  input_data: Record<string, unknown> | unknown[]
  output_data: Record<string, unknown> | unknown[] | null
  error: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
}

export interface OperatorMessage {
  id: string
  session_id: string
  role: string
  message_type: string
  content: Record<string, unknown> | string
  related_event_id: string | null
  created_at: string
}

export interface OperatorCommandAudit {
  id: string
  session_id: string
  command: string
  parameters: Record<string, unknown>
  permitted: boolean
  result_summary: string | null
  created_at: string
}

export interface OperatorArtifact {
  id: string
  task_id: string | null
  session_id: string | null
  artifact_type: string
  title: string
  content: Record<string, unknown> | string
  created_at: string
}

export interface OperatorSessionSummary {
  id: string
  user_input: string
  kind?: string | null
  parent_session_id?: string | null
  title?: string | null
  intent: string | null
  command: string | null
  response: string | null
  permitted: number
  duration_ms: number | null
  created_at: string
  message_count: number
  command_count: number
}

export interface OperatorSessionDetail {
  session: OperatorSessionSummary
  messages: OperatorMessage[]
  commands: OperatorCommandAudit[]
  artifacts: OperatorArtifact[]
}

export interface OperatorContextSnapshot {
  profile: string
  session_id: string | null
  session_kind: string | null
  sections: string[]
  summary: string
  context: Record<string, unknown>
}

export interface OperatorCommandPreview {
  session_id: string
  input: string
  permission_scope: string
  permission_mode: string
  worker_permission_mode: string
  classification: string
  intent: string
  confidence: number | null
  parameters: Record<string, unknown>
  allowed: boolean
  denial_reason: string | null
  worker_allowed: boolean | null
  worker_denial_reason: string | null
  command: {
    name: string
    risk_class: string
    risk_level: string
    description: string
    worker_permission_required: string
    operator_permission_required: string
    requires_confirmation: boolean
    requires_proposal: boolean
    audit_event_type: string
    backend_handler: string
  } | null
  extension?: {
    slash_command: string
    status: string | null
    target_command: string | null
    extension_id: string | null
    description: string | null
  } | null
}

export interface OperatorCommandResponse {
  intent: string
  risk_level: string
  response: string
  action: Record<string, unknown> | null
  needs_confirmation: boolean
}

export interface OperatorEvent {
  id: string
  event_type: string
  severity: string
  source: string
  session_id: string | null
  task_id: string | null
  related_entity_type: string | null
  related_entity_id: string | null
  payload: Record<string, unknown> | unknown[]
  created_at: string
}

export interface OperatorRecommendation {
  id: string
  kind: string
  severity: string
  summary: string
  taskType?: string
  metadata?: Record<string, unknown>
  task_ids?: string[]
  count?: number
}

export interface OperatorExtension {
  id: string
  session_id: string | null
  extension_name: string
  slash_command: string
  description: string
  target_command: string
  manifest: Record<string, unknown>
  status: string
  created_by: string
  proposal_artifact_id: string | null
  latest_run_id: string | null
  enabled_at: string | null
  enabled_by: string | null
  created_at: string
  updated_at: string
}

export interface RuntimeRecord {
  runtime_id: string
  state: string
  code_version: string | null
  config_version: string | null
  schema_version: string | null
  build_git_sha: string | null
  release_bundle_hash: string | null
  artifact_path: string | null
  mode: string | null
  started_at: string | null
  promoted_at: string | null
  drained_at: string | null
  failed_at?: string | null
  rolled_back_at?: string | null
  last_error: string | null
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface RuntimeEvent {
  id: string
  runtime_id: string | null
  event_type: string
  phase: string
  severity: string
  actor: string
  cause: string | null
  kind: string
  status: string
  operation_id: string | null
  payload: Record<string, unknown>
  created_at: string
}

export interface RuntimeLock {
  lock_name: string
  runtime_id: string | null
  owner: string
  token: string
  acquired_at: string
  expires_at: string
  released_at: string | null
  metadata: Record<string, unknown>
}

export interface RuntimeStatus {
  runtime_id: string | null
  phase: string
  actor: string
  cause: string | null
  started_at: string | null
  expires_at: string | null
  lock_owner: string | null
  can_promote: boolean
  can_rollback: boolean
  can_drain: boolean
  activation: Record<string, unknown> | null
  activation_required: boolean
  enabled: boolean
  current: RuntimeRecord | null
  candidate: RuntimeRecord | null
  shadow: RuntimeRecord | null
  draining: RuntimeRecord | null
  locks: RuntimeLock[]
  events: RuntimeEvent[]
  ownership_summary: Record<string, number>
  metrics: {
    state_counts: Record<string, number>
    active_lock_count: number
    recent_event_count: number
    ownership_active_count: number
    failed_runtime_count: number
  }
  guidance: {
    action: string
    reason: string
    runtime_id: string | null
  }
}

// Universe Generator types

export interface UniverseMember {
  id: string
  snapshot_id: string
  asset_id: string
  symbol: string
  rank: number
  total_score: number
  opportunity_score: number | null
  tradability_score: number | null
  liquidity_score: number | null
  estimated_cost: number | null
  crowding_risk: number | null
  late_entry_penalty: number | null
  scanner_family: string | null
  quote_volume_24h: number | null
  spread_bps: number | null
  ttl_sec: number
  expires_at: string
  inclusion_reason: string
  risk_flags: string
  execution_eligible: number
}

export interface UniverseSnapshot {
  id: string
  created_at: string
  market_type: string
  policy_id: string | null
  status: string
  expires_at: string | null
  summary: string
}

export interface UniverseCurrentResponse {
  snapshot: UniverseSnapshot | null
  members: UniverseMember[]
  enabled: boolean
  fallback: boolean
}

export interface UniverseFeatureSnapshot {
  id: string
  asset_id: string
  ts: string
  quote_volume_24h: number | null
  volume_1h: number | null
  spread_bps: number | null
  depth_10bps_usd: number | null
  atr_pct: number | null
  rsi_14: number | null
  return_1h: number | null
  return_4h: number | null
  return_24h: number | null
  funding_rate: number | null
  open_interest: number | null
  oi_delta_1h: number | null
  data_freshness_sec: number | null
}

export interface UniverseMemberDetail {
  member: UniverseMember
  features: UniverseFeatureSnapshot | null
  opportunities: Array<{ id: string; symbol: string; status: string; direction_bias: string | null; created_at: string }>
}
