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
}

export interface OperatorSessionState {
  id: string
  kind: OperatorSessionKind
  currentIntent: string | null
}

export interface OperatorTaskState {
  currentTaskId: string | null
  queueSize: number
}

export interface OperatorRecommendation {
  id: string
  type: string
  summary: string
}
