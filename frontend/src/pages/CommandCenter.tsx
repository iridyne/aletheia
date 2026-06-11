import { startTransition, useDeferredValue, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'
import type {
  OperatorCommandAudit,
  OperatorContextSnapshot,
  OperatorEvent,
  OperatorExtension,
  OperatorRecommendation,
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

export default function CommandCenter() {
  const qc = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [input, setInput] = useState('')
  const [selectedSessionId, setSelectedSessionId] = useState<string>('operator-main')
  const [historyFilter, setHistoryFilter] = useState('')
  const [inspectorFocus, setInspectorFocus] = useState<InspectorFocus>({ kind: 'session', id: 'operator-main' })
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

  const activeTask = tasks.find(task => task.id === operatorStatus?.current_task_id) || null
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
              <div key={message.id} className={`command-msg ${message.role === 'assistant' ? 'assistant' : 'user'}`}>
                <div className="command-msg-meta">
                  <span>{message.role}</span>
                  <span className="mono">{formatTimestamp(message.created_at)}</span>
                </div>
                <pre className="command-msg-body">{messageText(message.content)}</pre>
              </div>
            ))}
          </div>

          <div className="command-composer">
            <textarea
              value={input}
              onChange={event => setInput(event.target.value)}
              placeholder="Ask the operator to explain state, inspect a run, review a proposal, pause background activity, or run an enabled slash command."
              rows={4}
            />
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
        </section>

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
              <h3>Sessions</h3>
              <span className="mono text-muted">{sessions.length}</span>
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
