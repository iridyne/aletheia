import { startTransition, useDeferredValue, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'
import type {
  OperatorCommandAudit,
  OperatorCommandPreview,
  OperatorContextSnapshot,
  OperatorEvent,
  OperatorExtension,
  OperatorRecommendation,
  RuntimeEvent,
  RuntimeRecord,
  RuntimeStatus,
  OperatorSessionDetail,
  OperatorSessionSummary,
  OperatorStatus,
  OperatorTask,
} from '../types'

type InspectorFocus =
  | { kind: 'session'; id: string }
  | { kind: 'task'; id: string }
  | { kind: 'recommendation'; id: string }
  | { kind: 'event'; id: string }
  | { kind: 'command'; id: string }
  | null

function toUtc(ts: string): string {
  return ts.endsWith('Z') || ts.includes('+') ? ts : `${ts}Z`
}

function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return '—'
  try {
    return new Date(toUtc(ts)).toLocaleString([], {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return ts
  }
}

function messageText(content: unknown): string {
  if (typeof content === 'string') {
    return content
  }
  if (content && typeof content === 'object' && 'text' in (content as Record<string, unknown>)) {
    const text = (content as Record<string, unknown>).text
    return typeof text === 'string' ? text : JSON.stringify(content, null, 2)
  }
  return JSON.stringify(content, null, 2)
}

function messageObject(content: unknown): Record<string, unknown> | null {
  return content && typeof content === 'object' && !Array.isArray(content)
    ? content as Record<string, unknown>
    : null
}

function formatActionSummary(action: unknown): string | null {
  if (!action || typeof action !== 'object' || Array.isArray(action)) {
    return null
  }
  const payload = action as Record<string, unknown>
  if (payload.type === 'state_change') {
    return `${String(payload.key || 'state')} -> ${String(payload.value)}`
  }
  return typeof payload.type === 'string' ? payload.type : 'structured action'
}

function sessionKindLabel(kind: string | null | undefined): string {
  switch (kind) {
    case 'dreaming_session':
      return 'Dreaming'
    case 'review_session':
      return 'Review'
    case 'strategy_session':
      return 'Strategy'
    case 'diagnostic_session':
      return 'Diagnostic'
    default:
      return 'Command'
  }
}

function sessionKindBadge(kind: string | null | undefined): string {
  switch (kind) {
    case 'dreaming_session':
      return 'badge-blue'
    case 'review_session':
      return 'badge-yellow'
    case 'strategy_session':
      return 'badge-green'
    case 'diagnostic_session':
      return 'badge-red'
    default:
      return 'badge-blue'
  }
}

const BRANCH_KIND_OPTIONS = [
  { value: 'command_session', label: 'Command' },
  { value: 'diagnostic_session', label: 'Diagnostic' },
  { value: 'dreaming_session', label: 'Dreaming' },
  { value: 'strategy_session', label: 'Strategy' },
  { value: 'review_session', label: 'Review' },
] as const

function severityBadge(severity: string): string {
  switch (severity) {
    case 'high':
    case 'warning':
      return 'badge-red'
    case 'medium':
      return 'badge-yellow'
    case 'info':
      return 'badge-blue'
    default:
      return 'badge-blue'
  }
}

function eventDetail(event: OperatorEvent): string {
  if (typeof event.payload === 'object' && event.payload && 'status' in (event.payload as Record<string, unknown>)) {
    return String((event.payload as Record<string, unknown>).status)
  }
  if (typeof event.payload === 'object' && event.payload && 'agent_name' in (event.payload as Record<string, unknown>)) {
    return String((event.payload as Record<string, unknown>).agent_name)
  }
  return event.source
}

function sessionDisplayTitle(session: OperatorSessionSummary): string {
  return session.title || session.id
}

function previewBadge(preview: OperatorCommandPreview | null | undefined): string {
  if (!preview) return 'badge-blue'
  if (preview.classification === 'query') return 'badge-blue'
  if (preview.allowed) return 'badge-green'
  if (preview.classification === 'typed_command') return 'badge-yellow'
  return 'badge-red'
}

function previewLabel(preview: OperatorCommandPreview | null | undefined): string {
  if (!preview) return 'Preview'
  switch (preview.classification) {
    case 'query':
      return 'Query'
    case 'typed_command':
      return preview.allowed ? 'Ready' : 'Blocked'
    case 'forbidden':
      return 'Forbidden'
    case 'extension_not_enabled':
      return 'Disabled'
    case 'extension_invalid_arguments':
      return 'Args'
    case 'extension_unknown':
      return 'Unknown'
    default:
      return 'Preview'
  }
}

function sortSessions(sessions: OperatorSessionSummary[]): OperatorSessionSummary[] {
  return [...sessions].sort((left, right) => {
    const leftTs = Date.parse(toUtc(left.created_at))
    const rightTs = Date.parse(toUtc(right.created_at))
    return rightTs - leftTs
  })
}

function buildSessionRows(sessions: OperatorSessionSummary[]): Array<{ session: OperatorSessionSummary; depth: number }> {
  const byId = new Set(sessions.map(session => session.id))
  const children = new Map<string, OperatorSessionSummary[]>()
  for (const session of sessions) {
    const parent = session.parent_session_id || ''
    if (!parent) continue
    const items = children.get(parent) || []
    items.push(session)
    children.set(parent, items)
  }

  const rows: Array<{ session: OperatorSessionSummary; depth: number }> = []
  const visit = (session: OperatorSessionSummary, depth: number) => {
    rows.push({ session, depth })
    const sessionChildren = sortSessions(children.get(session.id) || [])
    for (const child of sessionChildren) {
      visit(child, depth + 1)
    }
  }

  const roots = sortSessions(
    sessions.filter(session => !session.parent_session_id || !byId.has(session.parent_session_id)),
  )
  for (const root of roots) {
    visit(root, 0)
  }
  return rows
}

function mergeEvents(existing: OperatorEvent[], incoming: OperatorEvent): OperatorEvent[] {
  const deduped = [incoming, ...existing.filter(item => item.id !== incoming.id)]
  return deduped
    .sort((left, right) => Date.parse(toUtc(right.created_at)) - Date.parse(toUtc(left.created_at)))
    .slice(0, 100)
}

function runtimeBadge(state: string | null | undefined): string {
  switch (state) {
    case 'current':
    case 'ready_to_promote':
      return 'badge-green'
    case 'shadowing':
    case 'candidate':
    case 'draining':
      return 'badge-yellow'
    case 'failed':
    case 'rolled_back':
      return 'badge-red'
    default:
      return 'badge-blue'
  }
}

function RuntimeSlot({ title, record }: { title: string; record: RuntimeRecord | null | undefined }) {
  return (
    <div className="runtime-slot">
      <div className="runtime-slot-head">
        <span>{title}</span>
        <span className={`badge ${runtimeBadge(record?.state)}`}>{record?.state || 'not configured'}</span>
      </div>
      <div className="runtime-slot-id mono">{record?.runtime_id || '—'}</div>
      <table className="status-board compact">
        <tbody>
          <tr><th>Code</th><td>{record?.code_version || record?.build_git_sha || '—'}</td></tr>
          <tr><th>Schema</th><td>{record?.schema_version || '—'}</td></tr>
          <tr><th>Bundle</th><td>{record?.release_bundle_hash || '—'}</td></tr>
          <tr><th>Started</th><td>{formatTimestamp(record?.started_at)}</td></tr>
          <tr><th>Promoted</th><td>{formatTimestamp(record?.promoted_at)}</td></tr>
        </tbody>
      </table>
    </div>
  )
}

function RuntimeContinuityPanel({
  status,
  events,
  onCandidate,
  onShadow,
  onPromote,
  onDrain,
  onRollback,
  pendingAction,
}: {
  status: RuntimeStatus | undefined
  events: RuntimeEvent[]
  onCandidate: () => void
  onShadow: () => void
  onPromote: () => void
  onDrain: () => void
  onRollback: () => void
  pendingAction: string | null
}) {
  const candidateId = status?.candidate?.runtime_id
  const locks = status?.locks || []
  const ownershipSummary = status?.ownership_summary || {}
  const metrics = status?.metrics
  const guidance = status?.guidance
  return (
    <div className="runtime-continuity-panel">
      <div className="runtime-summary-strip">
        <span className={`badge ${status?.enabled ? 'badge-green' : 'badge-yellow'}`}>
          {status?.enabled ? 'enabled' : 'disabled'}
        </span>
        <span className="mono">phase: {status?.phase || 'not configured'}</span>
        <span className="mono">lock: {status?.lock_owner || 'none'}</span>
        {status?.activation_required && (
          <span className="badge badge-yellow">restart required</span>
        )}
        <span className="mono">current: {status?.runtime_id || '—'}</span>
      </div>

      <div className="runtime-grid">
        <RuntimeSlot title="Current" record={status?.current} />
        <RuntimeSlot title="Candidate" record={status?.candidate} />
        <RuntimeSlot title="Shadow" record={status?.shadow} />
        <RuntimeSlot title="Draining" record={status?.draining} />
      </div>

      <div className="runtime-action-row">
        <button className="btn-control" onClick={onCandidate} disabled={pendingAction !== null}>
          {pendingAction === 'candidate' ? 'Creating…' : 'Create Candidate'}
        </button>
        <button
          className="btn-control secondary"
          onClick={onShadow}
          disabled={!candidateId || pendingAction !== null}
        >
          {pendingAction === 'shadow' ? 'Validating…' : 'Start Shadow'}
        </button>
        <button
          className="btn-control secondary"
          onClick={onPromote}
          disabled={!status?.can_promote || pendingAction !== null}
        >
          {pendingAction === 'promote' ? 'Promoting…' : 'Promote'}
        </button>
        <button
          className="btn-control danger"
          onClick={onDrain}
          disabled={!status?.can_drain || pendingAction !== null}
        >
          {pendingAction === 'drain' ? 'Draining…' : 'Drain'}
        </button>
        <button
          className="btn-control danger"
          onClick={onRollback}
          disabled={!status?.can_rollback || pendingAction !== null}
        >
          {pendingAction === 'rollback' ? 'Rolling Back…' : 'Rollback'}
        </button>
      </div>

      <div className="runtime-ops-grid">
        <section className="runtime-ops-section">
          <div className="cc-card-header">
            <h3>Locks</h3>
            <span className="mono text-muted">{locks.length}</span>
          </div>
          {locks.length === 0 && <div className="text-muted text-sm">No active runtime lock.</div>}
          {locks.map(lock => (
            <div className="runtime-lock-row" key={lock.lock_name}>
              <span className="mono">{lock.lock_name}</span>
              <span>{lock.owner}</span>
              <span className="mono">{formatTimestamp(lock.expires_at)}</span>
            </div>
          ))}
        </section>

        <section className="runtime-ops-section">
          <div className="cc-card-header">
            <h3>Ownership</h3>
            <span className="mono text-muted">{Object.keys(ownershipSummary).length}</span>
          </div>
          {Object.keys(ownershipSummary).length === 0 && (
            <div className="text-muted text-sm">No runtime-owned operator task records yet.</div>
          )}
          {Object.entries(ownershipSummary).map(([key, count]) => (
            <div className="runtime-lock-row" key={key}>
              <span className="mono">{key}</span>
              <span>{count}</span>
            </div>
          ))}
        </section>

        <section className="runtime-ops-section">
          <div className="cc-card-header">
            <h3>Guidance</h3>
            <span className="mono text-muted">{guidance?.action || 'observe'}</span>
          </div>
          <div className="runtime-guidance">
            <span className={`badge ${runtimeBadge(guidance?.action)}`}>{guidance?.action || 'observe'}</span>
            <span>{guidance?.reason?.replace(/_/g, ' ') || 'No runtime action suggested.'}</span>
            {guidance?.runtime_id && <span className="mono">{guidance.runtime_id}</span>}
          </div>
        </section>

        <section className="runtime-ops-section">
          <div className="cc-card-header">
            <h3>Metrics</h3>
            <span className="mono text-muted">{metrics?.recent_event_count ?? 0} events</span>
          </div>
          <div className="runtime-metric-grid">
            <span className="mono">locks {metrics?.active_lock_count ?? 0}</span>
            <span className="mono">ownership {metrics?.ownership_active_count ?? 0}</span>
            <span className="mono">failed {metrics?.failed_runtime_count ?? 0}</span>
            {Object.entries(metrics?.state_counts || {}).map(([state, count]) => (
              <span className="mono" key={state}>{state} {count}</span>
            ))}
          </div>
        </section>
      </div>

      <section className="runtime-events-section">
        <div className="cc-card-header">
          <h3>Continuity Timeline</h3>
          <span className="mono text-muted">{events.length}</span>
        </div>
        <div className="runtime-event-list">
          {events.length === 0 && <div className="text-muted text-sm">No runtime continuity events yet.</div>}
          {events.map(event => (
            <div className="runtime-event-row" key={event.id}>
              <span className={`badge ${runtimeBadge(event.phase)}`}>{event.phase}</span>
              <span>{event.event_type.replace(/_/g, ' ')}</span>
              <span className="mono">{event.runtime_id || '—'}</span>
              <span className="mono">{formatTimestamp(event.created_at)}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}

export default function CommandCenter() {
  const qc = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [input, setInput] = useState('')
  const [selectedSessionId, setSelectedSessionId] = useState<string>('operator-main')
  const [branchTitle, setBranchTitle] = useState('')
  const [branchKind, setBranchKind] = useState<string>('command_session')
  const [historyFilter, setHistoryFilter] = useState('')
  const [activePane, setActivePane] = useState<'conversation' | 'runtime'>('conversation')
  const [inspectorFocus, setInspectorFocus] = useState<InspectorFocus>({ kind: 'session', id: 'operator-main' })
  const deferredPreviewInput = useDeferredValue(input)
  const deferredHistoryFilter = useDeferredValue(historyFilter)

  const draft = searchParams.get('draft') || ''

  useEffect(() => {
    if (draft) {
      setInput(draft)
      setSearchParams({}, { replace: true })
    }
  }, [draft, setSearchParams])

  const { data: operatorStatus } = useQuery<OperatorStatus>({
    queryKey: ['operator-status'],
    queryFn: api.getOperatorStatus,
    refetchInterval: 10000,
  })
  const { data: sessions = [] } = useQuery<OperatorSessionSummary[]>({
    queryKey: ['operator-sessions'],
    queryFn: api.getOperatorSessions,
  })
  const { data: tasks = [] } = useQuery<OperatorTask[]>({
    queryKey: ['operator-tasks'],
    queryFn: api.getOperatorTasks,
  })
  const { data: enabledExtensions = [] } = useQuery<OperatorExtension[]>({
    queryKey: ['operator-extensions', 'enabled'],
    queryFn: () => api.getOperatorExtensions({ status: 'enabled', limit: 50 }),
  })
  const { data: recommendations = [] } = useQuery<OperatorRecommendation[]>({
    queryKey: ['operator-recommendations'],
    queryFn: api.getOperatorRecommendations,
  })
  const { data: operatorEvents = [] } = useQuery<OperatorEvent[]>({
    queryKey: ['operator-events'],
    queryFn: () => api.getOperatorEvents({ limit: 100 }),
  })
  const { data: runtimeStatus } = useQuery<RuntimeStatus>({
    queryKey: ['operator-runtime'],
    queryFn: api.getRuntimeStatus,
    refetchInterval: 5000,
  })
  const { data: runtimeEvents = [] } = useQuery<RuntimeEvent[]>({
    queryKey: ['operator-runtime-events'],
    queryFn: () => api.getRuntimeEvents(100),
    refetchInterval: 5000,
  })
  const statusSelectedSessionId =
    operatorStatus?.active_branch_id || operatorStatus?.session_id || 'operator-main'

  useEffect(() => {
    if (sessions.length === 0) return
    if (!sessions.some(session => session.id === selectedSessionId)) {
      const fallback = sessions[0].id
      setSelectedSessionId(fallback)
      setInspectorFocus({ kind: 'session', id: fallback })
    }
  }, [selectedSessionId, sessions])

  useEffect(() => {
    if (!statusSelectedSessionId) return
    if (!sessions.some(session => session.id === statusSelectedSessionId)) return
    if (statusSelectedSessionId === selectedSessionId) return
    setSelectedSessionId(statusSelectedSessionId)
    setInspectorFocus({ kind: 'session', id: statusSelectedSessionId })
  }, [selectedSessionId, sessions, statusSelectedSessionId])

  useEffect(() => {
    if (!inspectorFocus && selectedSessionId) {
      setInspectorFocus({ kind: 'session', id: selectedSessionId })
    }
  }, [inspectorFocus, selectedSessionId])

  const { data: sessionDetail } = useQuery<OperatorSessionDetail>({
    queryKey: ['operator-session', selectedSessionId],
    queryFn: () => api.getOperatorSession(selectedSessionId),
    enabled: !!selectedSessionId && sessions.some(session => session.id === selectedSessionId),
  })

  const { data: selectedSessionEvents = [] } = useQuery<OperatorEvent[]>({
    queryKey: ['operator-events', 'session', selectedSessionId],
    queryFn: () => api.getOperatorEvents({ sessionId: selectedSessionId, limit: 50 }),
    enabled: !!selectedSessionId,
  })
  const { data: sessionContext } = useQuery<OperatorContextSnapshot>({
    queryKey: ['operator-context', selectedSessionId],
    queryFn: () => api.getOperatorContext({ sessionId: selectedSessionId }),
    enabled: !!selectedSessionId,
  })
  const previewInput = deferredPreviewInput.trim()
  const { data: commandPreview, isFetching: commandPreviewFetching } = useQuery<OperatorCommandPreview>({
    queryKey: ['operator-command-preview', selectedSessionId, previewInput],
    queryFn: () => api.previewOperatorCommand({ input: previewInput, sessionId: selectedSessionId }),
    enabled: previewInput.length > 0,
  })

  const focusedTaskId = inspectorFocus?.kind === 'task' ? inspectorFocus.id : null
  const { data: focusedTaskEvents = [] } = useQuery<OperatorEvent[]>({
    queryKey: ['operator-events', 'task', focusedTaskId],
    queryFn: () => api.getOperatorEvents({ taskId: focusedTaskId || '', limit: 50 }),
    enabled: !!focusedTaskId,
  })
  const { data: commandSearchResults = [] } = useQuery<OperatorCommandAudit[]>({
    queryKey: ['operator-commands', 'search', deferredHistoryFilter],
    queryFn: () => api.getOperatorCommands({ query: deferredHistoryFilter.trim(), limit: 50 }),
    enabled: deferredHistoryFilter.trim().length > 0,
  })

  useEffect(() => {
    const stream = new EventSource('/api/operator/events/stream?limit=100')

    const handleOperatorEvent = (event: MessageEvent<string>) => {
      const parsed = JSON.parse(event.data) as OperatorEvent
      startTransition(() => {
        qc.setQueryData<OperatorEvent[]>(['operator-events'], current =>
          mergeEvents(current || [], parsed),
        )
      })
      void qc.invalidateQueries({ queryKey: ['operator-status'] })
      void qc.invalidateQueries({ queryKey: ['operator-extensions'] })
      void qc.invalidateQueries({ queryKey: ['operator-sessions'] })
      void qc.invalidateQueries({ queryKey: ['operator-tasks'] })
      void qc.invalidateQueries({ queryKey: ['operator-recommendations'] })
      void qc.invalidateQueries({ queryKey: ['operator-commands'] })
      void qc.invalidateQueries({ queryKey: ['operator-session'] })
      void qc.invalidateQueries({ queryKey: ['operator-events', 'session'] })
      void qc.invalidateQueries({ queryKey: ['operator-events', 'task'] })
      void qc.invalidateQueries({ queryKey: ['operator-context'] })
    }

    stream.addEventListener('operator_event', handleOperatorEvent as EventListener)
    return () => {
      stream.removeEventListener('operator_event', handleOperatorEvent as EventListener)
      stream.close()
    }
  }, [qc])

  const commandMut = useMutation({
    mutationFn: () => api.sendOperatorCommand(input, { sessionId: selectedSessionId }),
    onSuccess: () => {
      setInput('')
      qc.invalidateQueries({ queryKey: ['operator-status'] })
      qc.invalidateQueries({ queryKey: ['operator-sessions'] })
      qc.invalidateQueries({ queryKey: ['operator-session'] })
      qc.invalidateQueries({ queryKey: ['operator-recommendations'] })
      qc.invalidateQueries({ queryKey: ['operator-events'] })
    },
  })
  const activeBranchMut = useMutation({
    mutationFn: (sessionId: string) => api.setOperatorActiveBranch(sessionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['operator-status'] })
      qc.invalidateQueries({ queryKey: ['operator-sessions'] })
      qc.invalidateQueries({ queryKey: ['operator-session'] })
      qc.invalidateQueries({ queryKey: ['operator-events'] })
      qc.invalidateQueries({ queryKey: ['operator-context'] })
    },
  })
  const createBranchMut = useMutation({
    mutationFn: () =>
      api.createOperatorBranch({
        title: branchTitle.trim(),
        kind: branchKind,
        user_input: branchTitle.trim(),
        parent_session_id: selectedSessionId || 'operator-main',
        activate: true,
        selected_by: 'command-center',
      }),
    onSuccess: session => {
      setBranchTitle('')
      setSelectedSessionId(session.id)
      setInspectorFocus({ kind: 'session', id: session.id })
      qc.invalidateQueries({ queryKey: ['operator-status'] })
      qc.invalidateQueries({ queryKey: ['operator-sessions'] })
      qc.invalidateQueries({ queryKey: ['operator-session'] })
      qc.invalidateQueries({ queryKey: ['operator-events'] })
      qc.invalidateQueries({ queryKey: ['operator-context'] })
    },
  })

  const pauseMut = useMutation({
    mutationFn: api.pauseOperator,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['status'] })
      qc.invalidateQueries({ queryKey: ['operator-status'] })
    },
  })
  const resumeMut = useMutation({
    mutationFn: api.resumeOperator,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['status'] })
      qc.invalidateQueries({ queryKey: ['operator-status'] })
    },
  })
  const wakeMut = useMutation({
    mutationFn: api.wakeOperator,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['status'] })
      qc.invalidateQueries({ queryKey: ['operator-status'] })
    },
  })
  const runtimeCandidateMut = useMutation({
    mutationFn: () => {
      const stamp = Date.now().toString(36)
      return api.createRuntimeCandidate({
        code_version: `dashboard-candidate-${stamp}`,
        config_version: 'dashboard',
        schema_version: 'sqlite-runtime-continuity-v1',
        build_git_sha: `dashboard-${stamp}`,
        release_bundle_hash: `dashboard-${stamp}`,
        cause: 'command_center_candidate',
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['operator-runtime'] })
      qc.invalidateQueries({ queryKey: ['operator-runtime-events'] })
    },
  })
  const runtimeShadowMut = useMutation({
    mutationFn: () => api.startRuntimeShadow(runtimeStatus?.candidate?.runtime_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['operator-runtime'] })
      qc.invalidateQueries({ queryKey: ['operator-runtime-events'] })
      qc.invalidateQueries({ queryKey: ['operator-status'] })
    },
  })
  const runtimePromoteMut = useMutation({
    mutationFn: () => api.promoteRuntime(runtimeStatus?.candidate?.runtime_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['operator-runtime'] })
      qc.invalidateQueries({ queryKey: ['operator-runtime-events'] })
      qc.invalidateQueries({ queryKey: ['operator-status'] })
    },
  })
  const runtimeDrainMut = useMutation({
    mutationFn: () => api.drainRuntime(runtimeStatus?.draining?.runtime_id, true),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['operator-runtime'] })
      qc.invalidateQueries({ queryKey: ['operator-runtime-events'] })
      qc.invalidateQueries({ queryKey: ['operator-status'] })
    },
  })
  const runtimeRollbackMut = useMutation({
    mutationFn: api.rollbackRuntime,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['operator-runtime'] })
      qc.invalidateQueries({ queryKey: ['operator-runtime-events'] })
      qc.invalidateQueries({ queryKey: ['operator-status'] })
    },
  })

  const activeTask = tasks.find(task => task.id === operatorStatus?.current_task_id) || null
  const pendingRuntimeAction =
    runtimeCandidateMut.isPending
      ? 'candidate'
      : runtimeShadowMut.isPending
        ? 'shadow'
      : runtimePromoteMut.isPending
        ? 'promote'
        : runtimeDrainMut.isPending
          ? 'drain'
          : runtimeRollbackMut.isPending
            ? 'rollback'
            : null
  const filterText = deferredHistoryFilter.trim().toLowerCase()
  const sessionMessages = sessionDetail?.messages || []
  const sessionCommands = sessionDetail?.commands || []
  const commandHistory = filterText.length === 0 ? sessionCommands : commandSearchResults
  const selectedSessionSummary = sessions.find(session => session.id === selectedSessionId) || null
  const selectedSessionChildren = selectedSessionSummary
    ? sessions.filter(session => session.parent_session_id === selectedSessionSummary.id)
    : []
  const sessionRows = buildSessionRows(sessions)

  const filteredMessages = filterText.length === 0
    ? sessionMessages
    : sessionMessages.filter(message => {
      const text = messageText(message.content).toLowerCase()
      return (
        message.role.toLowerCase().includes(filterText) ||
        message.message_type.toLowerCase().includes(filterText) ||
        text.includes(filterText)
      )
    })
  const filteredCommands = filterText.length === 0
    ? commandHistory
    : commandHistory.filter(command =>
      command.command.toLowerCase().includes(filterText) ||
      JSON.stringify(command.parameters).toLowerCase().includes(filterText) ||
      (command.result_summary || '').toLowerCase().includes(filterText) ||
      command.session_id.toLowerCase().includes(filterText),
    )
  const filteredRecommendations = filterText.length === 0
    ? recommendations
    : recommendations.filter(item =>
      item.kind.toLowerCase().includes(filterText) ||
      item.summary.toLowerCase().includes(filterText) ||
      item.severity.toLowerCase().includes(filterText),
    )
  const filteredTasks = filterText.length === 0
    ? tasks
    : tasks.filter(task =>
      task.task_type.toLowerCase().includes(filterText) ||
      task.status.toLowerCase().includes(filterText) ||
      task.assigned_to.toLowerCase().includes(filterText) ||
      (task.error || '').toLowerCase().includes(filterText),
    )
  const timelineSource = selectedSessionEvents.length > 0 ? selectedSessionEvents : operatorEvents
  const timeline = timelineSource
    .slice(0, 12)
    .map(event => ({
      id: event.id,
      ts: event.created_at,
      title: event.event_type.replace(/_/g, ' '),
      detail: eventDetail(event),
      event,
    }))
  const filteredTimeline = filterText.length === 0
    ? timeline
    : timeline.filter(item =>
      item.title.toLowerCase().includes(filterText) ||
      (item.detail || '').toLowerCase().includes(filterText),
    )
  const quickActions = [
    { id: 'qa-operator', label: 'Operator Heartbeat', draft: 'show the operator heartbeat' },
    { id: 'qa-system', label: 'System Status', draft: 'what is the system doing?' },
    { id: 'qa-reco', label: 'Show Recommendations', draft: 'what recommendations do you have?' },
    { id: 'qa-failed', label: 'Review Failures', draft: 'what failed?' },
    { id: 'qa-lifecycle', label: 'Strategy Lifecycle', draft: 'show the strategy lifecycle' },
  ]

  const selectedTask =
    inspectorFocus?.kind === 'task'
      ? tasks.find(task => task.id === inspectorFocus.id) || null
      : null
  const selectedRecommendation =
    inspectorFocus?.kind === 'recommendation'
      ? recommendations.find(item => item.id === inspectorFocus.id) || null
      : null
  const selectedCommand =
    inspectorFocus?.kind === 'command'
      ? commandHistory.find(command => command.id === inspectorFocus.id) || null
      : null
  const selectedEvent =
    inspectorFocus?.kind === 'event'
      ? [...selectedSessionEvents, ...focusedTaskEvents, ...operatorEvents].find(event => event.id === inspectorFocus.id) || null
      : null

  const inspectorModel = (() => {
    if (selectedTask) {
      return {
        title: selectedTask.task_type.replace(/_/g, ' '),
        subtitle: `Assigned to ${selectedTask.assigned_to}`,
        facts: [
          ['Status', selectedTask.status],
          ['Priority', selectedTask.priority],
          ['Created by', selectedTask.created_by],
          ['Started', formatTimestamp(selectedTask.started_at)],
          ['Completed', formatTimestamp(selectedTask.completed_at)],
          ['Error', selectedTask.error || '—'],
          ['Events', String(focusedTaskEvents.length)],
        ],
        raw: {
          task: selectedTask,
          task_events: focusedTaskEvents,
        },
      }
    }

    if (selectedRecommendation) {
      return {
        title: selectedRecommendation.kind.replace(/_/g, ' '),
        subtitle: selectedRecommendation.summary,
        facts: [
          ['Severity', selectedRecommendation.severity],
          ['Suggested task', selectedRecommendation.taskType || '—'],
          ['Recommendation id', selectedRecommendation.id],
        ],
        raw: selectedRecommendation,
      }
    }

    if (selectedCommand) {
      return {
        title: selectedCommand.command,
        subtitle: selectedCommand.result_summary || 'Command audit detail',
        facts: [
          ['Allowed', selectedCommand.permitted ? 'yes' : 'no'],
          ['Created', formatTimestamp(selectedCommand.created_at)],
          ['Session', selectedCommand.session_id],
        ],
        raw: selectedCommand,
      }
    }

    if (selectedEvent) {
      return {
        title: selectedEvent.event_type.replace(/_/g, ' '),
        subtitle: eventDetail(selectedEvent),
        facts: [
          ['Severity', selectedEvent.severity],
          ['Source', selectedEvent.source],
          ['Session', selectedEvent.session_id || '—'],
          ['Task', selectedEvent.task_id || '—'],
          ['Created', formatTimestamp(selectedEvent.created_at)],
        ],
        raw: selectedEvent,
      }
    }

    return {
      title: sessionDisplayTitle(selectedSessionSummary || { id: 'operator-main', user_input: '', created_at: '', intent: null, command: null, response: null, permitted: 1, duration_ms: null, message_count: 0, command_count: 0 }),
      subtitle: selectedSessionSummary ? sessionKindLabel(selectedSessionSummary.kind) : 'Command',
      facts: [
        ['Session id', selectedSessionSummary?.id || 'operator-main'],
        ['Kind', sessionKindLabel(selectedSessionSummary?.kind)],
        ['Parent', selectedSessionSummary?.parent_session_id || '—'],
        ['Messages', String(sessionMessages.length)],
        ['Commands', String(sessionCommands.length)],
        ['Artifacts', String(sessionDetail?.artifacts.length || 0)],
        ['Child sessions', String(selectedSessionChildren.length)],
      ],
      raw: {
        session: sessionDetail?.session || selectedSessionSummary,
        messages: sessionMessages.slice(-5),
        commands: sessionCommands.slice(-5),
        events: selectedSessionEvents.slice(0, 10),
      },
    }
  })()

  return (
    <div>
      <PageHeader
        title="Command Center"
        role="Persistent operator workspace for command, diagnosis, session branching, and evidence-backed inspection."
        actions={
          <div className="flex gap-2">
            <button className="btn-control" onClick={() => pauseMut.mutate()} disabled={pauseMut.isPending}>Pause</button>
            <button className="btn-control secondary" onClick={() => resumeMut.mutate()} disabled={resumeMut.isPending}>Resume</button>
            <button className="btn-control secondary" onClick={() => wakeMut.mutate()} disabled={wakeMut.isPending}>Wake</button>
          </div>
        }
      />

      <div className="command-center-layout">
        <div className="command-center-main">
        <section className="card command-center-shell">
          <div className="command-center-strip">
            <span className={`badge ${operatorStatus?.status === 'online' ? 'badge-green' : operatorStatus?.status === 'stale' ? 'badge-yellow' : 'badge-red'}`}>
              {operatorStatus?.status || 'offline'}
            </span>
            <span className="mono">mode: {operatorStatus?.mode || 'Observing'}</span>
            <span className="mono">perm: {operatorStatus?.permission_mode || 'ReadOnly'}</span>
            <span className="mono">session: {operatorStatus?.session_id || 'operator-main'}</span>
            <span className="mono">
              branch: {operatorStatus?.active_branch_title || operatorStatus?.active_branch_id || 'main'}
            </span>
          </div>

          <div className="command-center-tabs">
            <button
              className={activePane === 'conversation' ? 'active' : ''}
              onClick={() => setActivePane('conversation')}
            >
              Conversation
            </button>
            <button
              className={activePane === 'runtime' ? 'active' : ''}
              onClick={() => setActivePane('runtime')}
            >
              Runtime Continuity
            </button>
          </div>

          {activePane === 'conversation' ? (
            <>
          <div className="command-stream">
            {sessionMessages.length === 0 && (
              <div className="command-empty">
                No conversation history yet. Use Ctrl+K to jump here, send a command below, or invoke an enabled slash command.
              </div>
            )}
            {sessionMessages.length > 0 && filteredMessages.length === 0 && (
              <div className="command-empty compact">
                No session messages match the current filter.
              </div>
            )}
            {filteredMessages.map(message => (
              (() => {
                const content = messageObject(message.content)
                const commandName =
                  typeof content?.command === 'string'
                    ? content.command
                    : typeof content?.intent === 'string' && content.intent !== 'query'
                      ? content.intent
                      : null
                const permitted =
                  typeof content?.permitted === 'boolean' ? content.permitted : null
                const riskLevel =
                  typeof content?.risk_level === 'string' ? content.risk_level : null
                const needsConfirmation =
                  typeof content?.needs_confirmation === 'boolean'
                    ? content.needs_confirmation
                    : false
                const actionSummary = formatActionSummary(content?.action)
                const parameters =
                  content?.parameters && typeof content.parameters === 'object' && !Array.isArray(content.parameters)
                    ? content.parameters
                    : null
                const commandMeta =
                  content?.command_meta && typeof content.command_meta === 'object' && !Array.isArray(content.command_meta)
                    ? content.command_meta as Record<string, unknown>
                    : null
                const commandDescription =
                  typeof commandMeta?.description === 'string' ? commandMeta.description : null
                const showTraceCard = message.message_type === 'command' && !!commandName
                const showResultCard = message.message_type === 'response' && (!!commandName || permitted !== null || !!actionSummary)
                return (
                  <div key={message.id} className={`command-msg ${message.role === 'assistant' ? 'assistant' : 'user'}`}>
                    <div className="command-msg-meta">
                      <span>{message.role}</span>
                      <span className="mono">{formatTimestamp(message.created_at)}</span>
                    </div>
                    {showTraceCard && (
                      <div className="command-trace-card">
                        <div className="command-trace-head">
                          <span className="badge badge-blue">Trace</span>
                          <span className="mono">{commandName}</span>
                        </div>
                        {(parameters || commandDescription) && (
                          <div className="command-trace-summary">
                            {commandDescription || 'Typed command prepared for execution.'}
                          </div>
                        )}
                        {parameters && Object.keys(parameters).length > 0 && (
                          <pre className="command-preview-params">{JSON.stringify(parameters, null, 2)}</pre>
                        )}
                      </div>
                    )}
                    {showResultCard && (
                      <div className="command-result-card">
                        <div className="command-result-head">
                          <span className={`badge ${permitted === false ? 'badge-red' : 'badge-green'}`}>
                            {permitted === false ? 'Blocked' : 'Executed'}
                          </span>
                          <span className="mono">{commandName || 'response'}</span>
                          {riskLevel && <span className="mono">risk: {riskLevel}</span>}
                        </div>
                        <div className="command-result-facts">
                          {needsConfirmation && <span className="mono">confirmation</span>}
                          {actionSummary && <span className="mono">{actionSummary}</span>}
                          {typeof commandMeta?.operator_permission_required === 'string' && (
                            <span className="mono">needs {commandMeta.operator_permission_required}</span>
                          )}
                        </div>
                      </div>
                    )}
                    <pre className="command-msg-body">{messageText(message.content)}</pre>
                  </div>
                )
              })()
            ))}
          </div>

          <div className="command-composer">
            <textarea
              value={input}
              onChange={event => setInput(event.target.value)}
              placeholder="Ask the operator to explain state, inspect a run, review a proposal, pause background activity, or run an enabled slash command."
              rows={4}
            />
            {previewInput.length > 0 && (
              <div className="command-preview">
                <div className="command-preview-head">
                  <span className={`badge ${previewBadge(commandPreview)}`}>
                    {commandPreviewFetching && !commandPreview ? 'Checking…' : previewLabel(commandPreview)}
                  </span>
                  <span className="mono">
                    {commandPreview?.command?.name || commandPreview?.intent || 'preview'}
                  </span>
                  {commandPreview?.command && (
                    <span className="mono">risk: {commandPreview.command.risk_class}</span>
                  )}
                  {commandPreview?.extension?.slash_command && (
                    <span className="mono">{commandPreview.extension.slash_command}</span>
                  )}
                </div>
                {commandPreview && (
                  <>
                    <div className="command-preview-summary">
                      {commandPreview.command?.description
                        || commandPreview.extension?.description
                        || (commandPreview.classification === 'query'
                          ? 'The operator will answer directly without mapping this input to a typed command.'
                          : commandPreview.denial_reason
                            || 'No command preview is available for this input.')}
                    </div>
                    {commandPreview.command && (
                      <div className="command-preview-facts">
                        <span className="mono">operator {commandPreview.permission_mode}</span>
                        <span className="mono">needs {commandPreview.command.operator_permission_required}</span>
                        <span className="mono">worker {commandPreview.worker_permission_mode}</span>
                        <span className="mono">needs {commandPreview.command.worker_permission_required}</span>
                        {commandPreview.command.requires_confirmation && (
                          <span className="mono">confirmation</span>
                        )}
                        {commandPreview.command.requires_proposal && (
                          <span className="mono">proposal-only</span>
                        )}
                      </div>
                    )}
                    {!commandPreview.allowed && commandPreview.denial_reason && (
                      <div className="command-preview-warning">{commandPreview.denial_reason}</div>
                    )}
                    {commandPreview.worker_allowed === false && commandPreview.worker_denial_reason && (
                      <div className="command-preview-warning">{commandPreview.worker_denial_reason}</div>
                    )}
                    {Object.keys(commandPreview.parameters || {}).length > 0 && (
                      <pre className="command-preview-params">
                        {JSON.stringify(commandPreview.parameters, null, 2)}
                      </pre>
                    )}
                  </>
                )}
              </div>
            )}
            <div className="command-composer-actions">
              <span className="text-muted text-sm">Long-form interaction lives here. Ctrl+K is a launcher; child sessions carry specialist traces.</span>
              <button
                className="btn-control"
                disabled={commandMut.isPending || input.trim().length === 0}
                onClick={() => commandMut.mutate()}
              >
                {commandMut.isPending ? 'Sending…' : 'Send'}
              </button>
            </div>
          </div>
            </>
          ) : (
            <RuntimeContinuityPanel
              status={runtimeStatus}
              events={runtimeEvents}
              pendingAction={pendingRuntimeAction}
              onCandidate={() => runtimeCandidateMut.mutate()}
              onShadow={() => runtimeShadowMut.mutate()}
              onPromote={() => {
                if (window.confirm('Promote the ready shadow runtime to current?')) {
                  runtimePromoteMut.mutate()
                }
              }}
              onDrain={() => {
                if (window.confirm('Mark the previous runtime as drained/legacy?')) {
                  runtimeDrainMut.mutate()
                }
              }}
              onRollback={() => {
                if (window.confirm('Rollback to the latest pre-promote checkpoint?')) {
                  runtimeRollbackMut.mutate()
                }
              }}
            />
          )}
        </section>

          <div className="card">
            <div className="cc-card-header">
              <h3>Sessions</h3>
              <span className="mono text-muted">{sessions.length}</span>
            </div>
            <div className="cc-branch-form">
              <label className="cc-filter-label" htmlFor="branch-title">New branch</label>
              <input
                id="branch-title"
                className="cc-filter-input"
                value={branchTitle}
                onChange={event => setBranchTitle(event.target.value)}
                placeholder="Investigate stale market data"
              />
              <div className="cc-branch-actions">
                <select value={branchKind} onChange={event => setBranchKind(event.target.value)}>
                  {BRANCH_KIND_OPTIONS.map(option => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
                <button
                  className="btn-control secondary"
                  disabled={createBranchMut.isPending || branchTitle.trim().length === 0}
                  onClick={() => createBranchMut.mutate()}
                >
                  {createBranchMut.isPending ? 'Creating…' : 'Create Branch'}
                </button>
              </div>
              <div className="text-muted text-sm">Start a persistent side-task session under the currently selected session and make it the active operator branch.</div>
            </div>
            <div className="cc-list">
              {sessions.length === 0 && <div className="text-muted text-sm">No sessions yet.</div>}
              {sessionRows.map(({ session, depth }) => (
                <button
                  key={session.id}
                  className={`cc-list-item ${selectedSessionId === session.id ? 'selected' : ''}`}
                  onClick={() => {
                    setSelectedSessionId(session.id)
                    setInspectorFocus({ kind: 'session', id: session.id })
                    if (statusSelectedSessionId !== session.id) {
                      activeBranchMut.mutate(session.id)
                    }
                  }}
                >
                  <span className="cc-session-copy" style={{ paddingLeft: `${depth * 16}px` }}>
                    <span>{sessionDisplayTitle(session)}</span>
                    <span className="cc-item-subtle">{session.id} · {session.message_count} msg</span>
                  </span>
                  <span className={`badge ${sessionKindBadge(session.kind)}`}>{sessionKindLabel(session.kind)}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="cc-card-header">
              <h3>Timeline</h3>
              <span className="mono text-muted">{filteredTimeline.length}/{timeline.length}</span>
            </div>
            <div className="cc-list">
              {timeline.length === 0 && <div className="text-muted text-sm">No operator timeline yet.</div>}
              {timeline.length > 0 && filteredTimeline.length === 0 && <div className="text-muted text-sm">No timeline items match the filter.</div>}
              {filteredTimeline.map(item => (
                <button
                  key={item.id}
                  className={`cc-list-item ${inspectorFocus?.kind === 'event' && inspectorFocus.id === item.id ? 'selected' : ''}`}
                  onClick={() => setInspectorFocus({ kind: 'event', id: item.id })}
                >
                  <span className="cc-list-copy">
                    <span>{item.title}</span>
                    <span className="cc-item-subtle">{item.detail}</span>
                  </span>
                  <span className="mono">{formatTimestamp(item.ts)}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="cc-card-header">
              <h3>Command History</h3>
              <span className="mono text-muted">
                {filteredCommands.length}/{commandHistory.length}
              </span>
            </div>
            <label className="cc-filter-label" htmlFor="command-center-filter">Filter history</label>
            <input
              id="command-center-filter"
              className="cc-filter-input"
              value={historyFilter}
              onChange={event => setHistoryFilter(event.target.value)}
              placeholder="Filter messages, commands, tasks, timeline…"
            />
            <div className="cc-list">
              {filterText.length === 0 && sessionCommands.length === 0 && <div className="text-muted text-sm">No command audit records yet.</div>}
              {filterText.length > 0 && commandSearchResults.length === 0 && <div className="text-muted text-sm">No command records match the full operator history search.</div>}
              {commandHistory.length > 0 && filteredCommands.length === 0 && <div className="text-muted text-sm">No command records match the filter.</div>}
              {filteredCommands.map(command => (
                <button
                  key={command.id}
                  className={`cc-command-row ${inspectorFocus?.kind === 'command' && inspectorFocus.id === command.id ? 'selected' : ''}`}
                  onClick={() => setInspectorFocus({ kind: 'command', id: command.id })}
                >
                  <div className="cc-command-main">
                    <span className="cc-command-name">{command.command}</span>
                    <span className={`badge ${command.permitted ? 'badge-green' : 'badge-red'}`}>
                      {command.permitted ? 'allowed' : 'blocked'}
                    </span>
                  </div>
                  <div className="cc-command-meta">
                    <span className="mono">{formatTimestamp(command.created_at)}</span>
                    {filterText.length > 0 && <span className="mono">{command.session_id}</span>}
                    {command.result_summary && <span>{command.result_summary}</span>}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>

        <aside className="command-center-side">
          <div className="card">
            <h3>Quick Actions</h3>
            <div className="cc-action-grid">
              {quickActions.map(action => (
                <button
                  key={action.id}
                  className="cc-action-btn"
                  onClick={() => setInput(action.draft)}
                >
                  {action.label}
                </button>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="cc-card-header">
              <h3>Enabled Extensions</h3>
              <span className="mono text-muted">{enabledExtensions.length}</span>
            </div>
            <div className="cc-list">
              {enabledExtensions.length === 0 && (
                <div className="text-muted text-sm">No enabled slash extensions yet.</div>
              )}
              {enabledExtensions.map(extension => {
                const textParameter =
                  typeof extension.manifest?.['text_parameter'] === 'string'
                    ? String(extension.manifest['text_parameter'])
                    : ''
                const draft = textParameter ? `${extension.slash_command} ` : extension.slash_command
                return (
                  <button
                    key={extension.id}
                    className="cc-list-item"
                    onClick={() => setInput(draft)}
                  >
                    <span className="cc-list-copy">
                      <span>{extension.extension_name}</span>
                      <span className="cc-item-subtle">
                        {extension.slash_command} · {extension.target_command}
                      </span>
                      <span className="cc-item-subtle">{extension.description}</span>
                    </span>
                    <span className="badge badge-green">enabled</span>
                  </button>
                )
              })}
            </div>
          </div>

          <div className="card">
            <h3>Operator State</h3>
            <table className="status-board">
              <tbody>
                <tr><th>Status</th><td>{operatorStatus?.status || 'offline'}</td></tr>
                <tr><th>Mode</th><td>{operatorStatus?.mode || 'Observing'}</td></tr>
                <tr><th>Intent</th><td>{operatorStatus?.current_intent || '—'}</td></tr>
                <tr><th>Active Branch</th><td>{operatorStatus?.active_branch_title || operatorStatus?.active_branch_id || '—'}</td></tr>
                <tr><th>Heartbeat</th><td>{formatTimestamp(operatorStatus?.last_seen_at)}</td></tr>
                <tr><th>Error</th><td>{operatorStatus?.last_error || '—'}</td></tr>
              </tbody>
            </table>
          </div>

          <div className="card">
            <div className="cc-card-header">
              <h3>Recommendations</h3>
              <span className="mono text-muted">{filteredRecommendations.length}/{recommendations.length}</span>
            </div>
            <div className="cc-list">
              {recommendations.length === 0 && <div className="text-muted text-sm">No backend recommendations right now.</div>}
              {recommendations.length > 0 && filteredRecommendations.length === 0 && <div className="text-muted text-sm">No recommendations match the filter.</div>}
              {filteredRecommendations.map(item => (
                <button
                  key={item.id}
                  className={`cc-list-item ${inspectorFocus?.kind === 'recommendation' && inspectorFocus.id === item.id ? 'selected' : ''}`}
                  onClick={() => setInspectorFocus({ kind: 'recommendation', id: item.id })}
                >
                  <span className="cc-list-copy">
                    <span>{item.summary}</span>
                    <span className="cc-item-subtle">{item.kind.replace(/_/g, ' ')}</span>
                  </span>
                  <span className={`badge ${severityBadge(item.severity)}`}>{item.severity}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="cc-card-header">
              <h3>Active Tasks</h3>
              <span className="mono text-muted">{filteredTasks.length}/{tasks.length}</span>
            </div>
            <div className="cc-list">
              {tasks.length === 0 && <div className="text-muted text-sm">No operator tasks yet.</div>}
              {tasks.length > 0 && filteredTasks.length === 0 && <div className="text-muted text-sm">No tasks match the filter.</div>}
              {filteredTasks.map(task => (
                <button
                  key={task.id}
                  className={`cc-list-item ${inspectorFocus?.kind === 'task' && inspectorFocus.id === task.id ? 'selected' : activeTask?.id === task.id ? 'selected' : ''}`}
                  onClick={() => setInspectorFocus({ kind: 'task', id: task.id })}
                >
                  <span className="cc-list-copy">
                    <span>{task.task_type.replace(/_/g, ' ')}</span>
                    <span className="cc-item-subtle">{task.assigned_to} · {task.status}</span>
                  </span>
                  <span className="mono">{task.priority}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="cc-card-header">
              <h3>Context Inspector</h3>
              <span className="mono text-muted">{sessionContext?.profile?.replace(/_/g, ' ') || '—'}</span>
            </div>
            {!sessionContext && <div className="text-muted text-sm">Loading context snapshot…</div>}
            {sessionContext && (
              <div className="cc-inspector-panel">
                <div className="cc-inspector-head">
                  <div className="cc-inspector-title">{sessionContext.session_kind?.replace(/_/g, ' ') || 'command session'}</div>
                  <div className="cc-item-subtle">{sessionContext.summary}</div>
                </div>
                <div className="cc-inspector-facts">
                  <div className="cc-inspector-fact">
                    <span className="cc-inspector-label">Profile</span>
                    <span className="cc-inspector-value">{sessionContext.profile}</span>
                  </div>
                  <div className="cc-inspector-fact">
                    <span className="cc-inspector-label">Session</span>
                    <span className="cc-inspector-value">{sessionContext.session_id || '—'}</span>
                  </div>
                  <div className="cc-inspector-fact">
                    <span className="cc-inspector-label">Sections</span>
                    <span className="cc-inspector-value">{sessionContext.sections.join(', ')}</span>
                  </div>
                </div>
                <pre className="cc-inspector">
                  {JSON.stringify(sessionContext.context, null, 2)}
                </pre>
              </div>
            )}
          </div>

          <div className="card">
            <div className="cc-card-header">
              <h3>Inspector</h3>
              <span className="mono text-muted">{inspectorModel.subtitle}</span>
            </div>
            <div className="cc-inspector-panel">
              <div className="cc-inspector-head">
                <div className="cc-inspector-title">{inspectorModel.title}</div>
                <div className="cc-item-subtle">{inspectorModel.subtitle}</div>
              </div>
              <div className="cc-inspector-facts">
                {inspectorModel.facts.map(([label, value]) => (
                  <div key={label} className="cc-inspector-fact">
                    <span className="cc-inspector-label">{label}</span>
                    <span className="cc-inspector-value">{value}</span>
                  </div>
                ))}
              </div>
              <pre className="cc-inspector">
                {JSON.stringify(inspectorModel.raw, null, 2)}
              </pre>
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}
