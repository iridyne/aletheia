export type OperatorMode =
  | 'Observing'
  | 'Investigating'
  | 'Repairing'
  | 'Trading'
  | 'Reviewing'
  | 'Dreaming'
  | 'Paused'
  | 'Degraded'

export type OperatorSessionKind =
  | 'command_session'
  | 'diagnostic_session'
  | 'dreaming_session'
  | 'strategy_session'
  | 'review_session'

export interface OperatorHeartbeat {
  agent_id: string
  status: 'online' | 'offline' | 'stale'
  mode: OperatorMode
  permission_mode: string
  is_busy: boolean
  current_task_id: string | null
  current_intent: string | null
  queue_size: number
  last_seen_at: string
  last_success_at: string | null
  last_error: string | null
  model_provider: string | null
  session_id: string
  active_branch_id?: string | null
  active_branch_kind?: OperatorSessionKind | null
  active_branch_title?: string | null
}

export interface OperatorSessionState {
  id: string
  kind: OperatorSessionKind
  currentIntent: string | null
}

export interface OperatorSessionBranchState {
  id: string
  kind: OperatorSessionKind
  title: string | null
  parent_session_id: string | null
  summary: string | null
  created_at?: string | null
}

export interface OperatorTaskState {
  currentTaskId: string | null
  queueSize: number
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

export interface PersistedOperatorMessage {
  id: string
  session_id: string
  role: string
  message_type: string
  content: Record<string, unknown> | string
  related_event_id: string | null
  created_at: string
}

export interface PersistedOperatorTask {
  id: string
  task_type: string
  status: string
  priority: string
  created_by: string
  assigned_to: string
  input_data: Record<string, unknown>
  output_data: Record<string, unknown> | null
  error: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
}

export interface PersistedOperatorEvent {
  id: string
  event_type: string
  severity: string
  source: string
  session_id: string | null
  task_id: string | null
  related_entity_type: string | null
  related_entity_id: string | null
  payload: Record<string, unknown>
  created_at: string
}

export interface PersistedOperatorArtifact {
  id: string
  task_id: string | null
  session_id: string | null
  artifact_type: string
  title: string
  content: Record<string, unknown> | string
  created_at: string
}

export interface PersistedOperatorCommand {
  id: string
  session_id: string
  command: string
  parameters: Record<string, unknown>
  permitted: boolean
  result_summary: string | null
  created_at: string
  session_kind?: string | null
  session_user_input?: string | null
  session_title?: string | null
}

export interface OperatorSessionDetail {
  session: OperatorSessionSummary
  messages: PersistedOperatorMessage[]
  commands: PersistedOperatorCommand[]
  artifacts: PersistedOperatorArtifact[]
}

export interface PersistedOperatorStatus extends OperatorHeartbeat {
  stale: boolean
  stale_after_sec: number
  heartbeat_age_sec?: number
}

export interface OperatorRecommendation {
  id: string
  kind: string
  severity: 'info' | 'medium' | 'high'
  summary: string
  taskType?: string
  eventType?: string
  metadata?: Record<string, unknown>
}

export interface GatewayStatusSnapshot {
  gateway_running: boolean
  paused: boolean
  permission_mode: string
  exchange?: string
  exchange_mode?: string
  live_state?: string
}

export interface GatewayHealthSnapshot {
  status: string
  issues: string[]
  version?: string
}

export interface OperatorSnapshot {
  status: GatewayStatusSnapshot
  health: GatewayHealthSnapshot
}

export interface OperatorTaskCreate {
  task_type: string
  status: string
  priority: string
  created_by: string
  assigned_to: string
  input_data?: Record<string, unknown>
  output_data?: Record<string, unknown>
  error?: string | null
}

export interface OperatorTaskUpdate {
  status?: string
  output_data?: Record<string, unknown>
  error?: string | null
}

export interface OperatorArtifactCreate {
  task_id?: string | null
  session_id?: string | null
  artifact_type: string
  title: string
  content: Record<string, unknown> | string
}

export interface OperatorSessionCreate {
  id: string
  user_input: string
  kind: string
  parent_session_id?: string | null
  title?: string | null
}

export interface OperatorMessageCreate {
  session_id: string
  role: string
  content: Record<string, unknown> | string
  message_type?: string
  related_event_id?: string | null
}

export interface OperatorEventCreate {
  event_type: string
  severity: string
  source: string
  session_id?: string | null
  task_id?: string | null
  related_entity_type?: string | null
  related_entity_id?: string | null
  payload?: Record<string, unknown>
}

export interface OperatorCompactionResult {
  date: string
  path: string
  artifact_id: string
  created: boolean
}

export type OperatorContextProfile =
  | 'minimal_context'
  | 'operational_context'
  | 'trading_context'
  | 'knowledge_context'
  | 'repair_context'

export interface OperatorContextQuery {
  session_id?: string | null
  profile?: OperatorContextProfile | null
  sections?: string[] | null
}

export interface OperatorContextSnapshot {
  profile: OperatorContextProfile
  session_id: string | null
  session_kind: OperatorSessionKind | 'command_session' | null
  sections: string[]
  summary: string
  context: Record<string, unknown>
}

export interface OperatorCommandExecution {
  session_id?: string | null
  user_input?: string | null
  parameters?: Record<string, unknown>
}

export interface OperatorCommandExecutionResult {
  command: string
  permitted: boolean
  response: string
  result: Record<string, unknown> | null
  action: Record<string, unknown> | null
  risk_level: string
  needs_confirmation: boolean
  session_id: string
}

export interface DreamingSummary {
  recent_research_notes: Array<Record<string, unknown>>
  recent_strategy_hypotheses: Array<Record<string, unknown>>
  deprecated_memories: Array<Record<string, unknown>>
  recent_dreaming_logs: Array<Record<string, unknown>>
  recent_dreaming_runs: Array<Record<string, unknown>>
  recent_daily_reviews?: Array<Record<string, unknown>>
}

export interface ReviewSummary {
  recent_closed_positions: Array<Record<string, unknown>>
  recent_post_trade_reviews: Array<Record<string, unknown>>
  recent_post_trade_reviewer_runs: Array<Record<string, unknown>>
  missing_review_strategy_ids: string[]
}

export interface StrategySummary {
  active_paper_strategies: Array<Record<string, unknown>>
  under_review_strategies: Array<Record<string, unknown>>
  paused_paper_strategies: Array<Record<string, unknown>>
  live_candidate_strategies: Array<Record<string, unknown>>
  recent_strategy_evaluations: Array<Record<string, unknown>>
  needs_operator_evaluation_strategy_ids: string[]
  recent_strategy_promotion_proposals: Array<Record<string, unknown>>
  needs_operator_promotion_proposal_strategy_ids: string[]
}

export interface RiskBoundaryProposalCandidate {
  boundary_key: string
  violation_kind: string
  rejection_count: number
  current_value: unknown
  suggested_value: unknown
  changes: Record<string, unknown>
  summary: string
  sample_violations: string[]
  decision_ids: string[]
  affected_symbols?: string[]
  execution_modes?: string[]
}

export interface RiskSummary {
  recent_rejected_decisions: Array<Record<string, unknown>>
  recent_risk_boundary_proposals: Array<Record<string, unknown>>
  needs_operator_risk_boundary_proposals: RiskBoundaryProposalCandidate[]
}

export interface AgentRunInvocationResult {
  run_id: string
  status: string
  outputs: string[]
  errors: string[]
}

export interface AgentRunSummary {
  id: string
  agent_name: string
  trigger_type: string
  permission_mode?: string | null
  started_at: string
  ended_at?: string | null
  status: string
  model_provider?: string | null
  model_name?: string | null
  tools_called?: string | null
  outputs_created?: string | null
  errors?: string | null
  cost_estimate?: number | null
}

export interface BackendGatewayClient {
  getGatewayStatus(): Promise<GatewayStatusSnapshot>
  getHealth(): Promise<GatewayHealthSnapshot>
  getOperatorStatus(): Promise<PersistedOperatorStatus>
  getOperatorEvents(limit?: number): Promise<PersistedOperatorEvent[]>
  getOperatorSessions(limit?: number): Promise<OperatorSessionSummary[]>
  getOperatorSession(sessionId: string): Promise<OperatorSessionDetail>
  getOperatorCommands(limit?: number): Promise<PersistedOperatorCommand[]>
  getOperatorTasks(status?: string): Promise<PersistedOperatorTask[]>
  getOperatorContext(payload: OperatorContextQuery): Promise<OperatorContextSnapshot>
  createSession(payload: OperatorSessionCreate): Promise<OperatorSessionSummary>
  createArtifact(payload: OperatorArtifactCreate): Promise<PersistedOperatorArtifact>
  getDreamingSummary(): Promise<DreamingSummary>
  getReviewSummary(): Promise<ReviewSummary>
  getStrategySummary(): Promise<StrategySummary>
  getRiskSummary(): Promise<RiskSummary>
  getAgentRuns(): Promise<AgentRunSummary[]>
  postHeartbeat(heartbeat: OperatorHeartbeat): Promise<boolean>
  createMessage(payload: OperatorMessageCreate): Promise<{ id: string }>
  createTask(payload: OperatorTaskCreate): Promise<{ id: string }>
  updateTask(taskId: string, payload: OperatorTaskUpdate): Promise<{ id: string }>
  createEvent(payload: OperatorEventCreate): Promise<{ id: string }>
  executeOperatorCommand(
    commandName: string,
    payload: OperatorCommandExecution,
  ): Promise<OperatorCommandExecutionResult>
  compactOperatorMemory(date: string): Promise<OperatorCompactionResult>
  startAgentRun(agentName: string): Promise<AgentRunInvocationResult>
}
