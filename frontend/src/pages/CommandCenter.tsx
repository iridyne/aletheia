import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'
import type { AuditEvent, OperatorSessionDetail, OperatorSessionSummary, OperatorStatus, OperatorTask } from '../types'

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

export default function CommandCenter() {
  const qc = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [input, setInput] = useState('')
  const [selectedSessionId, setSelectedSessionId] = useState<string>('operator-main')

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
    refetchInterval: 5000,
  })
  const { data: sessions = [] } = useQuery<OperatorSessionSummary[]>({
    queryKey: ['operator-sessions'],
    queryFn: api.getOperatorSessions,
    refetchInterval: 5000,
  })
  const { data: tasks = [] } = useQuery<OperatorTask[]>({
    queryKey: ['operator-tasks'],
    queryFn: api.getOperatorTasks,
    refetchInterval: 5000,
  })
  const { data: auditEvents = [] } = useQuery<AuditEvent[]>({
    queryKey: ['audit-events', 'operator'],
    queryFn: () => api.getAuditEvents(20),
    refetchInterval: 10000,
  })

  useEffect(() => {
    if (sessions.length === 0) return
    if (!sessions.some(session => session.id === selectedSessionId)) {
      setSelectedSessionId(sessions[0].id)
    }
  }, [selectedSessionId, sessions])

  const { data: sessionDetail } = useQuery<OperatorSessionDetail>({
    queryKey: ['operator-session', selectedSessionId],
    queryFn: () => api.getOperatorSession(selectedSessionId),
    enabled: !!selectedSessionId && sessions.some(session => session.id === selectedSessionId),
    refetchInterval: 5000,
  })

  const commandMut = useMutation({
    mutationFn: () => api.sendOperatorCommand(input),
    onSuccess: () => {
      setInput('')
      qc.invalidateQueries({ queryKey: ['operator-status'] })
      qc.invalidateQueries({ queryKey: ['operator-sessions'] })
      qc.invalidateQueries({ queryKey: ['operator-session', 'operator-main'] })
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
  const guidance = (() => {
    if (!operatorStatus) return ['Waiting for operator status.']
    if (operatorStatus.status === 'offline') return ['Operator shell is offline. Check supervisor state or wake it.']
    if (operatorStatus.status === 'stale') return ['Heartbeat is stale. Inspect shell logs or restart the shell process.']
    if (operatorStatus.mode === 'Paused') return ['Operator is paused. Resume only when you want background monitoring active again.']
    if (activeTask) return [`Current focus: ${activeTask.task_type.replace(/_/g, ' ')}.`]
    return ['No active task. The operator is in observing mode.']
  })()

  const timeline = [
    ...tasks.slice(0, 8).map(task => ({
      id: `task-${task.id}`,
      ts: task.completed_at || task.started_at || task.created_at,
      title: `${task.task_type.replace(/_/g, ' ')} · ${task.status}`,
      kind: 'task',
    })),
    ...auditEvents.slice(0, 8).map(event => ({
      id: `evt-${event.id}`,
      ts: event.created_at,
      title: event.event_type.replace(/_/g, ' '),
      kind: 'event',
    })),
  ].sort((a, b) => new Date(toUtc(b.ts)).getTime() - new Date(toUtc(a.ts)).getTime())

  return (
    <div>
      <PageHeader
        title="Command Center"
        role="Persistent operator workspace for command, diagnosis, task visibility, and session inspection."
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
          </div>

          <div className="command-stream">
            {(sessionDetail?.messages || []).length === 0 && (
              <div className="command-empty">
                No conversation history yet. Use Ctrl+K to jump here or send a command below.
              </div>
            )}
            {(sessionDetail?.messages || []).map(message => (
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
              placeholder="Ask the operator to explain state, inspect a run, review a proposal, or pause background activity."
              rows={4}
            />
            <div className="command-composer-actions">
              <span className="text-muted text-sm">Long-form interaction lives here. Ctrl+K is now a launcher.</span>
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
            <h3>Operator State</h3>
            <table className="status-board">
              <tbody>
                <tr><th>Status</th><td>{operatorStatus?.status || 'offline'}</td></tr>
                <tr><th>Mode</th><td>{operatorStatus?.mode || 'Observing'}</td></tr>
                <tr><th>Intent</th><td>{operatorStatus?.current_intent || '—'}</td></tr>
                <tr><th>Heartbeat</th><td>{formatTimestamp(operatorStatus?.last_seen_at)}</td></tr>
                <tr><th>Error</th><td>{operatorStatus?.last_error || '—'}</td></tr>
              </tbody>
            </table>
          </div>

          <div className="card">
            <h3>Recommendations</h3>
            <ul className="cc-guidance">
              {guidance.map(item => <li key={item}>{item}</li>)}
            </ul>
          </div>

          <div className="card">
            <h3>Active Tasks</h3>
            <div className="cc-list">
              {tasks.length === 0 && <div className="text-muted text-sm">No operator tasks yet.</div>}
              {tasks.map(task => (
                <button key={task.id} className={`cc-list-item ${activeTask?.id === task.id ? 'selected' : ''}`}>
                  <span>{task.task_type.replace(/_/g, ' ')}</span>
                  <span className="mono">{task.status}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="card">
            <h3>Sessions</h3>
            <div className="cc-list">
              {sessions.length === 0 && <div className="text-muted text-sm">No sessions yet.</div>}
              {sessions.map(session => (
                <button
                  key={session.id}
                  className={`cc-list-item ${selectedSessionId === session.id ? 'selected' : ''}`}
                  onClick={() => setSelectedSessionId(session.id)}
                >
                  <span>{session.id}</span>
                  <span className="mono">{session.message_count} msg</span>
                </button>
              ))}
            </div>
          </div>

          <div className="card">
            <h3>Timeline</h3>
            <div className="cc-list">
              {timeline.length === 0 && <div className="text-muted text-sm">No operator timeline yet.</div>}
              {timeline.map(item => (
                <div key={item.id} className="cc-list-item static">
                  <span>{item.title}</span>
                  <span className="mono">{formatTimestamp(item.ts)}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <h3>Inspector</h3>
            <pre className="cc-inspector">
              {JSON.stringify(
                {
                  session: sessionDetail?.session || null,
                  activeTask,
                  lastCommand: sessionDetail?.commands?.slice(-1)[0] || null,
                },
                null,
                2,
              )}
            </pre>
          </div>
        </aside>
      </div>
    </div>
  )
}
