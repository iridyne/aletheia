import type {
  OperatorHeartbeat,
  OperatorMode,
  OperatorSessionBranchState,
  OperatorSessionState,
  OperatorTaskState,
} from './schemas.js'

function nowIso(): string {
  return new Date().toISOString()
}

export function buildHeartbeat(input: {
  mode: OperatorMode
  permissionMode: string
  session: OperatorSessionState
  tasks: OperatorTaskState
  activeBranch: OperatorSessionBranchState | null
  modelProvider: string
  lastError: string | null
  lastSuccessAt: string | null
  runtimeId?: string | null
  runtimeMode?: 'current' | 'shadow' | string | null
  runtimeState?: string | null
}): OperatorHeartbeat {
  return {
    agent_id: 'operator',
    status: 'online',
    runtime_id: input.runtimeId || 'runtime-local-current',
    runtime_mode: input.runtimeMode || 'current',
    runtime_state: input.runtimeState || (input.runtimeMode === 'shadow' ? 'shadowing' : 'current'),
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
    active_branch_id: input.activeBranch?.id || null,
    active_branch_kind: input.activeBranch?.kind || null,
    active_branch_title: input.activeBranch?.title || null,
  }
}
