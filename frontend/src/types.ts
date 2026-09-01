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
  take_profit: number | null
  risk_reward: number | null
  execution_mode: string
  entry_plan_id?: string | null
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
  proposal_execution_mode?: string | null
  decision_role?: string | null
  evaluated_quantity?: number | null
  proposal_commit_hash?: string | null
  risk_mode?: string | null
  policy_id?: string | null
  policy_revision?: number | null
  policy_hash?: string | null
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
  entry_plan_id?: string | null
  exchange_order_id: string | null
  status: string
  created_at: string
  filled_at: string | null
  error_reason: string | null
  position_id?: string | null
  close_reason?: string | null
  source?: string | null
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
  unrealized_pnl_source?: 'exchange_exact' | 'local_estimate' | 'ambiguous' | 'unavailable' | string
  realized_pnl: number | null
  realized_pnl_complete?: boolean
  realized_pnl_unavailable_reason?: string | null
  execution_mode: string
  strategy_id: string | null
  proposal_id?: string | null
  order_id?: string | null
  status: string
  stop_price: number | null
  take_profit_price: number | null
  close_reason: string | null
  exit_reasoning?: string | null
  source?: string  // "native" | "adopted"
  opened_at: string
  closed_at: string | null
  decision_reasoning?: string | null
  leverage?: number | null
  liquidation_price?: number | null
  entry_fee?: number | null
  exit_fee?: number | null
  /** Signed quote-currency funding: positive is a debit, negative a credit. */
  funding_cost?: number | null
  funding_source?: string | null
  funding_status?: 'complete' | 'not_applicable' | 'unavailable' | string
  slippage_cost?: number | null
}

export interface ThesisLineageRecord {
  lineage_id: string
  root_type: 'market_observer' | 'serenity' | 'unknown' | string
  root_id: string | null
  symbol: string | null
  market_type: string | null
  direction: string | null
  created_at: string | null
  universe_member_id: string | null
  universe_rank: number | null
  scanner_family: string | null
  tradability_score: number | null
  late_entry_penalty: number | null
  regime_tag: string | null
  observation_thesis: string | null
  observation_confidence: string | number | null
  key_observations: unknown[]
  decision_action: string | null
  decision_conviction: number | null
  decision_reasoning: string | null
  decision_signal_source?: string | null
  decision_signal_source_inferred_from?: string | null
  decision_opportunity_id_inferred_from?: string | null
  supporting_signals: unknown[]
  opposing_signals: unknown[]
  bear_case: string | null
  key_assumptions: unknown[]
  proposal_id: string | null
  proposal_status: string | null
  risk_approved: boolean | null
  risk_violations: unknown[]
  order_id: string | null
  position_id: string | null
  was_traded: boolean
  notional_usdt: number | null
  actual_pnl: number | null
  actual_pnl_pct: number | null
  close_reason: string | null
  shadow_batch_id: string | null
  shadow_rank: number | null
  shadow_pnl_pct: number | null
  counterfactual_avg_pnl_pct: number | null
  verdict: 'pending' | 'validated' | 'failed' | 'missed_alpha' | 'poor_expression' | 'insufficient_data' | string
  failure_bucket: string | null
  parse_warning?: string | null
  human_thesis?: string | null
  forward_return_pct?: number | null
  serenity?: Record<string, unknown> | null
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
  enabled: boolean
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
  execution_venue: string | null
  direction_affinity: string | null
  applicable_regimes: string | null
  created_at: string
  updated_at: string | null
}

export interface KnowledgeItem {
  id: string
  item_type: KnowledgeItemType | string
  title: string
  file_path?: string | null
  strategy_id: string | null
  confidence: number | string | null
  status: string
  tags: string | null
  created_at: string
  updated_at: string | null
  producer_run_id?: string | null
}

export type KnowledgeItemType =
  | 'calibration_report'
  | 'daily_review'
  | 'dreaming_log'
  | 'guard_candidate'
  | 'operator_assessment'
  | 'post_trade_review'
  | 'reasoning_feedback'
  | 'research_note'
  | 'strategy_memory'
  | 'system_action'
  | 'trade_case_study'
  | 'trading_guard'
  | 'trading_memo'

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
  realized_pnl: number | null
  positions_count: number
  execution_mode: string
  snapshot_at: string
}

export interface PortfolioSummary {
  total_value_usdt: number | null
  today_pnl: number | null
  unrealized_pnl: number | null
  realized_pnl: number | null
  all_realized_pnl?: number | null
  today_realized_pnl?: number | null
  realized_pnl_complete?: boolean
  realized_pnl_unavailable_count?: number
  today_realized_pnl_complete?: boolean
  today_realized_pnl_unavailable_count?: number
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
  market_type: string | null
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
  paper_execution_mode: string
  execution_target: 'local_paper' | 'external_exchange'
  uses_external_exchange: boolean
  execution_exchange: string | null
  market_data_exchange: string
  exchange_alignment: 'local_paper' | 'aligned' | 'diverged'
  exchange_diverged: boolean
  live_state: string
  permission_mode: string
  scheduler_enabled: boolean
  scheduler_running: boolean
  scheduler_state: string
  scheduler_job_count: number
  scheduler_last_tick: string | null
  ohlcv_cache: {
    size: number
    max_entries: number
    hits: number
    misses: number
    expired: number
    evicted: number
  } | null
  startup_failure_policy: Array<{
    stage: string
    on_failure: 'fail_startup' | 'warn_continue' | 'pause_autonomy'
    readiness_effect: string
  }>
  model_provider: string
  external_connectivity: ExternalConnectivityStatus
  health_status: string
  health_issues: string[]
  infra_events?: InfraEvent[]
  llm_model_health?: Record<string, unknown>[]
  boot_commit: string | null
  current_commit: string | null
  pid: number
  uptime_sec: number
  version: string
}

export interface ExternalConnectivityStatus {
  state: 'reachable' | 'unavailable' | 'recovering' | 'unknown'
  severity: 'ok' | 'warning' | 'info'
  priority: 1 | 2 | 3 | 4
  message: string
  action: string | null
  observed_at: string | null
  outage_started_at?: number | null
  last_recovery_at?: number | null
}

export interface InfraEvent {
  created_at: string
  severity: 'info' | 'warning' | 'critical'
  source: string
  code: string
  message: string
  action?: string | null
  entity_id?: string | null
  metadata?: Record<string, unknown>
}

export interface CollectorHealthDomain {
  domain: string
  cadence_sec: number
  last_status: string
  display_status: string
  attributed_cause: string | null
  display_severity: 'warning' | 'info' | 'muted' | 'ok'
  age_sec: number | null
  overdue: boolean
  degraded: boolean
  consecutive_problems?: number
  last_error_code?: string | null
}

export interface CollectorInfrastructureIncident {
  code: 'external_connectivity_unavailable' | 'external_connectivity_recovering'
  status: 'active' | 'recovering'
  severity: 'warning' | 'info'
  priority: number
  message: string
  action: string
  detected_at: string | null
  observed_at: string | null
  affected_domains: string[]
}

export interface CollectorHealthReport {
  status: 'healthy' | 'attention' | 'degraded' | 'initializing'
  domain_count: number
  recorded_count: number
  completed_count: number
  degraded_count: number
  transient_problem_count: number
  infrastructure_incident: CollectorInfrastructureIncident | null
  domains: CollectorHealthDomain[]
}

export interface MarketDataCoverageDomain {
  domain: string
  label: string
  state: 'fresh' | 'stale' | 'empty' | 'invalid' | 'idle'
  critical: boolean
  row_count: number
  symbol_count: number
  source_counts: Record<string, number>
  newest_observed_at: string | null
  age_sec: number | null
  max_age_sec: number
}

export interface MarketDataCoverageReport {
  status: 'healthy' | 'attention' | 'empty'
  generated_at: string
  domain_count: number
  fresh_count: number
  problem_count: number
  critical_problem_count: number
  domains: MarketDataCoverageDomain[]
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
  trading_daemon?: {
    state: string
    running: boolean
    fallback_blocker: string | null
    last_error_summary: string | null
  }
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

export interface Candidate {
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
  stale?: boolean
  entry_plan_id?: string | null
  entry_plan_status?: string | null
  entry_plan_execution_mode?: 'RESTING_MAKER' | 'TRIGGERED' | null
  // Backward-compatible API fields for historical setup rows.
  setup_state?: string | null
  triggered_opportunity_id?: string | null
}

/** @deprecated Compatibility alias for the physical `universe_members` wire row. */
export type UniverseMember = Candidate

export interface EntryPlan {
  id: string
  planning_generation_id: string
  candidate_id: string
  planning_key: string
  version: 'entry-plan/v1'
  venue: string
  symbol: string
  market_type: 'spot' | 'perp'
  direction: 'long' | 'short'
  execution_mode: 'RESTING_MAKER' | 'TRIGGERED'
  status: 'waiting' | 'risk_reserved' | 'triggered' | 'ordered' | 'partially_filled' | 'filled' | 'invalidated' | 'expired' | 'cancelled' | 'failed'
  order_id: string | null
  plan_hash: string
  created_at: string
  updated_at: string
  terminal_at: string | null
  terminal_reason: string | null
  contract_valid: boolean
  lineage?: {
    order: { id: string; exchange_order_id: string | null; status: string; quantity: number; position_id: string | null } | null
    fills: { count: number; quantity: number; latest_at: string | null } | null
    position: { id: string; status: string; quantity: number; entry_price: number } | null
  }
  plan: {
    thesis: string
    evidence_refs?: string[]
    scenario: string
    entry_zone: [number, number]
    invalidation_price: number
    stop_price: number
    target_price: number
    expires_at: string
    limit_price?: number | null
    trigger_spec_id?: string | null
    trigger_spec_version?: string | null
    trigger_params?: Record<string, unknown>
    trigger_data_requirements?: string[]
    trigger_freshness_ms?: number | null
    expected_horizon_hours?: number
    order_tactic?: 'POST_ONLY_LIMIT' | 'OPPOSING_BBO_IOC'
    maker_required?: boolean
    time_in_force?: string
    price_rule?: string
    bbo_reference?: string | null
    max_wait_sec?: number
    partial_fill_policy?: string
    taker_fallback_allowed?: boolean
    max_slippage_bps?: number | null
    worst_price?: number | null
    max_notional?: number
    max_risk_pct?: number
    max_leverage?: number
    required_permission?: string
    execution_account_mode?: string
    risk_policy_id?: string
    risk_policy_revision?: number
  } | null
}

export interface CandidateAttention {
  id: string
  last_candidate_id: string
  venue: string
  symbol: string
  market_type: 'spot' | 'perp'
  candidate_family: string
  direction: 'long' | 'short'
  status: string
  attempt_count: number
  attention_status: 'active' | 'terminal'
  attention_started_at: string
  last_requalified_at: string | null
  soft_expires_at: string
  hard_expires_at: string
  attention_terminal_at: string | null
  attention_terminal_reason: string | null
  rank: number | null
  total_score: number | null
  entry_price_usd: number | null
  liquidity_score: number | null
  spread_bps: number | null
  inclusion_reason: string | null
  risk_flags: string | null
  execution_eligible: number | null
  candidate_expires_at: string | null
  active_entry_plan_id: string | null
  active_entry_plan_status: string | null
}

export interface CandidateWorkbenchRow {
  id: string
  snapshot_id: string
  asset_id: string
  venue: string
  symbol: string
  market_type: 'spot' | 'perp'
  rank: number
  total_score: number
  scanner_family: string | null
  scanner_direction: 'long' | 'short' | null
  inclusion_reason: string | null
  risk_flags: string | null
  execution_eligible: number
  expires_at: string
  planning_generation_id: string | null
  planning_status: string | null
  result_kind: string | null
  planning_result_json: string | null
  attempt_count: number | null
  attention_status: 'active' | 'terminal' | null
  soft_expires_at: string | null
  hard_expires_at: string | null
  attention_terminal_reason: string | null
  entry_plan_id: string | null
  entry_plan_status: string | null
  entry_plan_terminal_reason: string | null
}

export interface EntryWorkbenchResponse {
  generated_at: string
  availability: Record<string, { available: boolean; stale: boolean; reason?: string }>
  universe: {
    enabled: boolean | null
    status: 'active' | 'disabled' | 'stale_snapshot' | 'no_active_snapshot' | 'read_unavailable'
    snapshot: UniverseSnapshot | null
    fallback_instruments: string[]
  }
  summary: { active_entry_plans: number; needs_planning: number; expiring_soon: number } | null
  entry_plans: EntryPlan[] | null
  current_candidates: CandidateWorkbenchRow[] | null
  candidate_attention: CandidateAttention[] | null
}

export interface PlanningHistoryResponse {
  items: Array<CandidateAttention & {
    is_current: number
    entry_plan_id: string | null
    entry_plan_status: string | null
    execution_mode: string | null
    updated_at: string
  }>
  next_before: string | null
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
  members: Candidate[]
  enabled: boolean
  fallback: boolean
  stale: boolean
  stale_count: number
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

export interface CandidateDetail {
  member: Candidate
  attention: CandidateAttention | null
  features: UniverseFeatureSnapshot | null
  entry_plans: Array<{ id: string; status: string; execution_mode: string; created_at: string }>
  opportunities: Array<{ id: string; symbol: string; status: string; direction_bias: string | null; created_at: string }>
}

/** @deprecated Use CandidateDetail. */
export type UniverseMemberDetail = CandidateDetail

export interface CalibrationRow {
  scanner_family: string | null
  direction: 'long' | 'short'
  market_type: 'spot' | 'perp' | 'futures'
  horizon: string
  cost_model_version: string
  n: number
  win_count: number
  avg_return: number | null
  min_return: number | null
  max_return: number | null
}

export interface CalibrationFamilySummary {
  family: string
  direction: 'long' | 'short' | 'unknown'
  market_type: string
  total_outcomes: number
  hit_rate_pct: number
  avg_return_pct: number
}

export interface CalibrationResponse {
  cost_model_version: string
  by_cohort_horizon: CalibrationRow[]
  by_cohort: CalibrationFamilySummary[]
}
