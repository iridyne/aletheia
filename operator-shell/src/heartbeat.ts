import type { OperatorHeartbeat, OperatorMode, OperatorSessionState, OperatorTaskState } from './schemas.js'

function nowIso(): string {
  return new Date().toISOString()
}

export function buildHeartbeat(input: {
  mode: OperatorMode
  permissionMode: string
  session: OperatorSessionState
  tasks: OperatorTaskState
  modelProvider: string
  lastError: string | null
  lastSuccessAt: string | null
}): OperatorHeartbeat {
  return {
    agent_id: 'operator',
    status: 'online',
    mode: input.mode,
    permission_mode: input.permissionMode,
    is_busy: input.tasks.currentTaskId !== null,
    current_task_id: input.tasks.currentTaskId,
    current_intent: input.session.currentIntent || 'monitoring system state',
    queue_size: input.tasks.queueSize,
    last_seen_at: nowIso(),
    last_success_at: input.lastSuccessAt,
    last_error: input.lastError,
    model_provider: input.modelProvider,
    session_id: input.session.id,
  }
}
