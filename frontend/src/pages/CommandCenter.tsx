import { startTransition, useDeferredValue, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { humanize, fmtDelta, fmtPct, fmtSymbol } from '../lib/format'
import { getOperatorDisplayStatus } from '../lib/systemState'
import { positionFreshness, positionKeys } from '../lib/queryKeys'
import { safeErrorText } from '../lib/mutationErrors'
import { connectEventStream } from '../lib/eventStream'
import PageHeader from '../components/PageHeader'
import { Badge, Button, Card, ListRowButton, StatusText, SymbolLink, TableWrap, useConfirm, useToast } from '../components/ui'
import type {
  OperatorCommandAudit,
  OperatorCommandPreview,
  OperatorCommandResponse,
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

const BRANCH_KIND_OPTIONS = [
  { value: 'command_session', label: 'Command' },
  { value: 'diagnostic_session', label: 'Diagnostic' },
  { value: 'dreaming_session', label: 'Dreaming' },
  { value: 'strategy_session', label: 'Strategy' },
  { value: 'review_session', label: 'Review' },
] as const

function severityTextClass(severity: string): string {
  switch (severity) {
    case 'high':
      return 'cc-severity-high'
    case 'medium':
      return 'cc-severity-medium'
    case 'low':
      return 'cc-severity-low'
    default:
      return 'cc-severity-low'
  }
}

function depthClass(depth: number): string {
  return `cc-depth-${Math.max(0, Math.min(depth, 6))}`
}

function sessionKindTone(kind: string | null | undefined): 'blue' | 'yellow' | 'green' | 'red' | 'muted' {
  switch (kind) {
    case 'dreaming_session':
      return 'blue'
    case 'review_session':
      return 'yellow'
    case 'strategy_session':
      return 'green'
    case 'diagnostic_session':
      return 'red'
    default:
      return 'muted'
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


// ─── Heartbeat 三宫格 ─────────────────────────────────────────────────────────

function usePrevNumber(val: number | null) {
  const ref = useRef<number | null>(null)
  const prev = ref.current
  ref.current = val
  return prev
}

function PnlFlash({ value }: { value: number | null }) {
  const prev = usePrevNumber(value)
  const [cls, setCls] = useState('')
  useEffect(() => {
    if (prev == null || value == null || value === prev) return
    const next = value > prev ? 'tick-flash-up' : 'tick-flash-down'
    setCls(next)
    const t = setTimeout(() => setCls(''), 650)
    return () => clearTimeout(t)
  }, [value, prev])
  if (value == null) return <span className="text-muted">—</span>
  return (
    <span className={`${value >= 0 ? 'pnl-positive' : 'pnl-negative'}${cls ? ` ${cls}` : ''}`}>
      {fmtDelta(value)}
    </span>
  )
}

function HeartbeatZone({
  operatorStatus,
}: {
  operatorStatus: import('../types').OperatorStatus | undefined
}) {
  const { data: portfolio } = useQuery({
    queryKey: ['portfolio'],
    queryFn: api.getPortfolio,
    refetchInterval: 10000,
  })
  const { data: positions = [] } = useQuery<any[]>({
    queryKey: positionKeys.all,
    queryFn: () => api.getPositions(),
    ...positionFreshness,
    refetchInterval: 10000,
    staleTime: 8000,
  })

  const p = portfolio
  const unrealNum: number | null = p?.unrealized_pnl == null ? null : Number(p.unrealized_pnl)
  const unrealBySymbol = p?.unrealized_by_symbol || {}
  const topSymbols = Object.entries(unrealBySymbol)
    .sort((a, b) => Math.abs(Number(b[1])) - Math.abs(Number(a[1])))
    .slice(0, 5)
  const positionsCount = Number(p?.positions_count ?? positions.length ?? 0)
  const hasPositions = Number.isFinite(positionsCount) && positionsCount > 0

  const opDisplay = getOperatorDisplayStatus(operatorStatus)
  // Busy = actively running ANY task (Working/Trading/Reviewing/Dreaming/Repairing),
  // not just mode==='Working'. Internal mode names are implementation labels; is_busy
  // is the honest "is the operator doing something right now" signal from the backend.
  const isWorking = !!operatorStatus?.is_busy
  const intent = operatorStatus?.current_intent || null
  const lastSeen = operatorStatus?.last_seen_at || null

  // gather thesis from positions (up to 3)
  const thesisItems = positions
    .filter((pos: any) => pos.thesis)
    .slice(0, 3)
    .map((pos: any) => ({
      symbol: pos.symbol,
      market_type: pos.market_type,
      thesis: pos.thesis as string,
    }))

  return (
    <div className="cc-heartbeat-zone">
      {/* ── 左格：System Operator 正在思考 ── */}
      <Card className="cc-heartbeat-card">
        <div className="cc-heartbeat-title">
          <span
            className={`status-light ${isWorking ? 'light-green heartbeat' : 'light-muted'}`}
          />
          <Badge tone={isWorking ? 'green' : 'neutral'}>
            {isWorking ? 'Thinking' : 'Idle'}
          </Badge>
        </div>
        {intent ? (
          <p className="cc-intent-text mono">{intent}</p>
        ) : (
          <p className="cc-intent-text text-muted text-sm">暂无进行中思考</p>
        )}
        {lastSeen && (
          <div className="cc-heartbeat-footer text-muted text-xs">
            {formatTimestamp(lastSeen)}
          </div>
        )}
      </Card>

      {/* ── 中格：当前持仓 PnL ── */}
      <Card className="cc-heartbeat-card">
        <div className="cc-heartbeat-title">
          <span className="text-sm text-muted">Unrealized PnL</span>
          <span className="cc-heartbeat-hero">
            <PnlFlash value={unrealNum} />
          </span>
        </div>
        {topSymbols.length > 0 ? (
          <table className="cc-pnl-table">
            <tbody>
              {topSymbols.map(([sym, pnl]) => {
                const pos = positions.find((p: any) => p.symbol === sym)
                const mt = pos?.market_type
                return (
                  <tr key={sym}>
                    <td><SymbolLink symbol={sym} marketType={mt} /></td>
                    <td className="text-right">
                      <PnlFlash value={Number(pnl)} />
                    </td>
                    <td className="text-right text-xs text-muted">
                      {p?.total_value_usdt
                        ? fmtPct((Math.abs(Number(pnl)) / Number(p.total_value_usdt)) * 100, { scale: 'percent' })
                        : ''}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        ) : hasPositions ? (
          <p className="text-muted text-sm">持仓 PnL 暂无分解</p>
        ) : (
          <p className="text-muted text-sm">无持仓</p>
        )}
      </Card>

      {/* ── 右格：持仓理由 ── */}
      <Card className="cc-heartbeat-card">
        <div className="cc-heartbeat-title">
          <span className="text-sm text-muted">持仓理由</span>
        </div>
        {thesisItems.length > 0 ? (
          <ul className="cc-thesis-list">
            {thesisItems.map(({ symbol, market_type, thesis }) => (
              <li key={symbol} className="cc-thesis-item">
                <SymbolLink symbol={symbol} marketType={market_type} className="text-xs cc-thesis-sym" />
                <span className="text-sm cc-thesis-body">{thesis}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted text-sm">无持仓理由</p>
        )}
      </Card>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────

export default function CommandCenter() {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [searchParams, setSearchParams] = useSearchParams()
  const [input, setInput] = useState('')
  const [selectedSessionId, setSelectedSessionId] = useState<string>('operator-main')
  const [branchTitle, setBranchTitle] = useState('')
  const [branchKind, setBranchKind] = useState<string>('command_session')
  const [historyFilter, setHistoryFilter] = useState('')
  const [inspectorFocus, setInspectorFocus] = useState<InspectorFocus>({ kind: 'session', id: 'operator-main' })
  const deferredPreviewInput = useDeferredValue(input)
  const deferredHistoryFilter = useDeferredValue(historyFilter)
  const composerRef = useRef<HTMLTextAreaElement>(null)
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
  const opDisplay = getOperatorDisplayStatus(operatorStatus)
  const intent = operatorStatus?.current_intent || null
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

  // Follow the Operator's active branch by exact id. The session list is one
  // page of the newest sessions, so it never decides which session is selected.
  useEffect(() => {
    if (!statusSelectedSessionId) return
    if (statusSelectedSessionId === selectedSessionId) return
    setSelectedSessionId(statusSelectedSessionId)
    setInspectorFocus({ kind: 'session', id: statusSelectedSessionId })
  }, [selectedSessionId, statusSelectedSessionId])

  useEffect(() => {
    if (!inspectorFocus && selectedSessionId) {
      setInspectorFocus({ kind: 'session', id: selectedSessionId })
    }
    // Focus composer when session branch switches
    composerRef.current?.focus()
  }, [inspectorFocus, selectedSessionId])

  const { data: sessionDetail, error: sessionDetailError } = useQuery<OperatorSessionDetail>({
    queryKey: ['operator-session', selectedSessionId],
    queryFn: () => api.getOperatorSession(selectedSessionId),
    enabled: !!selectedSessionId,
  })
  // A missing branch is shown as missing and never re-targeted. The main
  // session may legitimately not exist yet: the first command creates it.
  const mainSessionId = operatorStatus?.session_id || 'operator-main'
  const selectedSessionMissing =
    sessionDetailError instanceof ApiError && sessionDetailError.status === 404 && selectedSessionId !== mainSessionId

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
    const handleOperatorEvent = (event: MessageEvent<string>) => {
      if (!event.data || event.data.startsWith(':')) return
      try {
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
      } catch {
        // Ignore malformed chunks or SSE pings to prevent stream crash
      }
    }
    return connectEventStream('/api/operator/events/stream?limit=100', stream => {
      stream.addEventListener('operator_event', handleOperatorEvent as EventListener)
    })
  }, [qc])

  const { toast } = useToast()
  const commandMut = useMutation({
    // Snapshot of what was sent: the draft and selection may change while it is in flight.
    mutationFn: (sent: { input: string; sessionId: string; confirmedCommand?: string }) =>
      api.sendOperatorCommand(sent.input, { sessionId: sent.sessionId, confirmedCommand: sent.confirmedCommand }),
    onSuccess: (resp: OperatorCommandResponse, sent) => {
      if (resp.executed) {
        // Clear only the draft that was sent, never one typed meanwhile.
        setInput(current => (current === sent.input ? '' : current))
      } else {
        toast(resp.needs_confirmation ? 'warning' : 'error', `Not executed: ${safeErrorText(resp.response)}`)
      }
      qc.invalidateQueries({ queryKey: ['operator-status'] })
      qc.invalidateQueries({ queryKey: ['operator-sessions'] })
      qc.invalidateQueries({ queryKey: ['operator-session'] })
      qc.invalidateQueries({ queryKey: ['operator-recommendations'] })
      qc.invalidateQueries({ queryKey: ['operator-events'] })
    },
    // Send failure preserves the typed input (no setInput('')) so the operator can retry,
    // and surfaces the error via toast — silent loss of a typed command is the worst case.
    onError: (e: any) => toast('error', `Send failed: ${safeErrorText(e?.message || e?.error)}`),
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

  // #165 fix: pause/resume/wake used to swallow the backend's error responses
  // (LLM-unavailable → 409, kill-switch-active → 409, etc.) — the button
  // silently "succeeded" from the UI while the system stayed paused. Route
  // every failure through toast so the operator sees the real reason
  // (matches the pattern in App.tsx that already extracts extractResumeError).
  const pauseMut = useMutation({
    mutationFn: api.pauseOperator,
    onSuccess: async () => {
      await Promise.all([
        qc.refetchQueries({ queryKey: ['status'] }, { throwOnError: true }),
        qc.refetchQueries({ queryKey: ['operator-status'] }, { throwOnError: true }),
      ])
    },
    onError: (e: any) => toast('error', `Pause failed: ${safeErrorText(e?.message || e?.error)}`),
  })
  const resumeMut = useMutation({
    mutationFn: api.resumeOperator,
    onSuccess: async () => {
      await Promise.all([
        qc.refetchQueries({ queryKey: ['status'] }, { throwOnError: true }),
        qc.refetchQueries({ queryKey: ['operator-status'] }, { throwOnError: true }),
      ])
    },
    onError: (e: any) => toast('error', `Resume failed: ${safeErrorText(e?.message || e?.error)}`),
  })
  const wakeMut = useMutation({
    mutationFn: api.wakeOperator,
    onSuccess: async () => {
      await Promise.all([
        qc.refetchQueries({ queryKey: ['status'] }, { throwOnError: true }),
        qc.refetchQueries({ queryKey: ['operator-status'] }, { throwOnError: true }),
      ])
    },
    onError: (e: any) => toast('error', `Wake failed: ${safeErrorText(e?.message || e?.error)}`),
  })

  const activeTask = tasks.find(task => task.id === operatorStatus?.current_task_id) || null
  const operatorCommandPending = pauseMut.isPending || resumeMut.isPending || wakeMut.isPending
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
      title: humanize(event.event_type),
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
    { id: 'qa-learning', label: 'Learning Review', draft: 'summarize learning, strategy memory, and recent reviews' },
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
        title: humanize(selectedTask.task_type),
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
        title: humanize(selectedRecommendation.kind),
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
        title: humanize(selectedEvent.event_type),
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
            <Button className="btn-control" onClick={() => pauseMut.mutate()} disabled={operatorCommandPending}>Pause</Button>
            <Button variant="secondary" className="btn-control secondary" onClick={() => resumeMut.mutate()} disabled={operatorCommandPending}>Resume</Button>
            <Button variant="secondary" className="btn-control secondary" onClick={() => wakeMut.mutate()} disabled={operatorCommandPending}>Wake</Button>
          </div>
        }
      />

      <HeartbeatZone operatorStatus={operatorStatus} />

      <div className="command-center-layout">
        <div className="command-center-main">
        <Card className="command-center-shell">
          <div className="command-center-strip">
            <Badge tone={opDisplay.tone}>
              {opDisplay.state}
            </Badge>
            {intent && <span className="mono">{intent}</span>}
            <span className="mono sp-debug">mode: {opDisplay.debugMode}</span>
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
                // Rows written before `executed` existed only recorded `permitted`.
                const executed =
                  typeof content?.executed === 'boolean' ? content.executed : permitted
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
                          <Badge tone="blue">Trace</Badge>
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
                          <Badge tone={permitted === false ? 'red' : executed === false ? 'yellow' : 'green'}>
                            {permitted === false ? 'Blocked' : executed === false ? 'Not executed' : 'Executed'}
                          </Badge>
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
            {selectedSessionMissing && (
              <div className="command-preview-warning">
                Session {selectedSessionId} is not available. Commands are not sent to another session.
              </div>
            )}
            <textarea
              ref={composerRef}
              value={input}
              onChange={event => setInput(event.target.value)}
              placeholder="Ask the operator to explain state, inspect a run, review a proposal, pause background activity, or run an enabled slash command."
              rows={4}
            />
            {previewInput.length > 0 && (
              <div className="command-preview">
                <div className="command-preview-head">
                  <Badge tone={previewBadge(commandPreview).includes('green') ? 'green' : previewBadge(commandPreview).includes('red') ? 'red' : 'yellow'}>
                    {commandPreviewFetching && !commandPreview ? 'Checking…' : previewLabel(commandPreview)}
                  </Badge>
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
              <Button
                className="btn-control"
                disabled={commandMut.isPending || input.trim().length === 0 || selectedSessionMissing}
                onClick={async () => {
                  // The backend runs a confirmation-gated command only when
                  // confirmed_command names it; that field is the human
                  // confirmation and is sent only after this modal.
                  const gated = commandPreview?.command?.requires_confirmation
                    ? commandPreview.command.name
                    : undefined
                  if (gated) {
                    if (!(await confirm({
                      title: `Run ${gated}?`,
                      description: 'This command requires confirmation. Review the preview before sending.',
                      confirmLabel: 'Run command',
                      variant: 'warning',
                    }))) {
                      return
                    }
                  }
                  commandMut.mutate({ input, sessionId: selectedSessionId, confirmedCommand: gated })
                }}
              >
                {commandMut.isPending ? 'Sending…' : 'Send'}
              </Button>
            </div>
          </div>
        </Card>

          <Card>
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
                <Button
                  variant="secondary"
                  className="btn-control secondary"
                  disabled={createBranchMut.isPending || branchTitle.trim().length === 0}
                  onClick={() => createBranchMut.mutate()}
                >
                  {createBranchMut.isPending ? 'Creating…' : 'Create Branch'}
                </Button>
              </div>
              <div className="text-muted text-sm">Start a persistent side-task session under the currently selected session and make it the active operator branch.</div>
            </div>
            <div className="cc-list">
              {sessions.length === 0 && <div className="text-muted text-sm">No sessions yet.</div>}
              {sessionRows.map(({ session, depth }) => (
                <ListRowButton
                  key={session.id}
                  selected={selectedSessionId === session.id}
                  onClick={() => {
                    setSelectedSessionId(session.id)
                    setInspectorFocus({ kind: 'session', id: session.id })
                    if (statusSelectedSessionId !== session.id) {
                      activeBranchMut.mutate(session.id)
                    }
                  }}
                >
                  <span className={`cc-session-copy ${depthClass(depth)}`}>
                    <span>{sessionDisplayTitle(session)}</span>
                    <span className="cc-item-subtle">{session.id} · {session.message_count} msg</span>
                  </span>
                  <StatusText tone={sessionKindTone(session.kind)}>{sessionKindLabel(session.kind)}</StatusText>
                </ListRowButton>
              ))}
            </div>
          </Card>

          <Card>
            <div className="cc-card-header">
              <h3>Timeline</h3>
              <span className="mono text-muted">{filteredTimeline.length}/{timeline.length}</span>
            </div>
            <div className="cc-list">
              {timeline.length === 0 && <div className="text-muted text-sm">No operator timeline yet.</div>}
              {timeline.length > 0 && filteredTimeline.length === 0 && <div className="text-muted text-sm">No timeline items match the filter.</div>}
              {filteredTimeline.map(item => (
                <ListRowButton
                  key={item.id}
                  selected={inspectorFocus?.kind === 'event' && inspectorFocus.id === item.id}
                  onClick={() => setInspectorFocus({ kind: 'event', id: item.id })}
                >
                  <span className="cc-list-copy">
                    <span>{item.title}</span>
                    <span className="cc-item-subtle">{item.detail}</span>
                  </span>
                  <span className="mono">{formatTimestamp(item.ts)}</span>
                </ListRowButton>
              ))}
            </div>
          </Card>

          <Card>
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
                <ListRowButton
                  key={command.id}
                  className="cc-command-row"
                  selected={inspectorFocus?.kind === 'command' && inspectorFocus.id === command.id}
                  onClick={() => setInspectorFocus({ kind: 'command', id: command.id })}
                >
                  <div className="cc-command-main">
                    <span className="cc-command-name">{command.command}</span>
                    <StatusText tone={command.permitted ? 'green' : 'red'}>
                      {command.permitted ? 'allowed' : 'blocked'}
                    </StatusText>
                  </div>
                  <div className="cc-command-meta">
                    <span className="mono">{formatTimestamp(command.created_at)}</span>
                    {filterText.length > 0 && <span className="mono">{command.session_id}</span>}
                    {command.result_summary && <span>{command.result_summary}</span>}
                  </div>
                </ListRowButton>
              ))}
            </div>
          </Card>
        </div>

        <aside className="command-center-side">
          <Card>
            <h3>Quick Actions</h3>
            <div className="cc-action-grid">
              {quickActions.map(action => (
                <Button
                  variant="secondary"
                  key={action.id}
                  className="cc-action-btn"
                  onClick={() => setInput(action.draft)}
                >
                  {action.label}
                </Button>
              ))}
            </div>
          </Card>

          <Card>
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
                  <ListRowButton
                    key={extension.id}
                    onClick={() => setInput(draft)}
                  >
                    <span className="cc-list-copy">
                      <span>{extension.extension_name}</span>
                      <span className="cc-item-subtle">
                        {extension.slash_command} · {extension.target_command}
                      </span>
                      <span className="cc-item-subtle">{extension.description}</span>
                    </span>
                    <StatusText tone="green">enabled</StatusText>
                  </ListRowButton>
                )
              })}
            </div>
          </Card>

          <Card>
            <h3>Operator State</h3>
            <TableWrap>
              <table className="status-board">
                <tbody>
                  <tr><th>Status</th><td>{opDisplay.state}</td></tr>
                  {opDisplay.blockedDetail && (
                    <tr className="op-blocked-row">
                      <th>⚠ Reason</th>
                      <td className="op-blocked-detail">{opDisplay.blockedDetail}</td>
                    </tr>
                  )}
                  <tr><th>Intent</th><td>{operatorStatus?.current_intent || '—'}</td></tr>
                  <tr className="debug-row"><th>Mode</th><td className="muted">{opDisplay.debugMode}</td></tr>
                  <tr><th>Active Branch</th><td>{operatorStatus?.active_branch_title || operatorStatus?.active_branch_id || '—'}</td></tr>
                  <tr><th>Heartbeat</th><td>{formatTimestamp(operatorStatus?.last_seen_at)}</td></tr>
                  <tr><th>Error</th><td>{operatorStatus?.last_error || '—'}</td></tr>
                </tbody>
              </table>
            </TableWrap>
          </Card>

          <Card>
            <div className="cc-card-header">
              <h3>Recommendations</h3>
              <span className="mono text-muted">{filteredRecommendations.length}/{recommendations.length}</span>
            </div>
            <div className="cc-list">
              {recommendations.length === 0 && <div className="text-muted text-sm">No backend recommendations right now.</div>}
              {recommendations.length > 0 && filteredRecommendations.length === 0 && <div className="text-muted text-sm">No recommendations match the filter.</div>}
              {filteredRecommendations.map(item => (
                <ListRowButton
                  key={item.id}
                  selected={inspectorFocus?.kind === 'recommendation' && inspectorFocus.id === item.id}
                  onClick={() => setInspectorFocus({ kind: 'recommendation', id: item.id })}
                >
                  <span className="cc-list-copy">
                    <span>{item.summary}</span>
                    <span className="cc-item-subtle">{humanize(item.kind)}</span>
                  </span>
                  <span className={`cc-severity ${severityTextClass(item.severity)}`}>{humanize(item.severity)}</span>
                </ListRowButton>
              ))}
            </div>
          </Card>

          <Card>
            <div className="cc-card-header">
              <h3>Active Tasks</h3>
              <span className="mono text-muted">{filteredTasks.length}/{tasks.length}</span>
            </div>
            <div className="cc-list">
              {tasks.length === 0 && <div className="text-muted text-sm">No operator tasks yet.</div>}
              {tasks.length > 0 && filteredTasks.length === 0 && <div className="text-muted text-sm">No tasks match the filter.</div>}
              {filteredTasks.map(task => (
                <ListRowButton
                  key={task.id}
                  selected={inspectorFocus?.kind === 'task' && inspectorFocus.id === task.id ? true : activeTask?.id === task.id}
                  onClick={() => setInspectorFocus({ kind: 'task', id: task.id })}
                >
                  <span className="cc-list-copy">
                    <span>{humanize(task.task_type)}</span>
                    <span className="cc-item-subtle">{task.assigned_to} · {humanize(task.status)}</span>
                  </span>
                  <span className="mono">{humanize(task.priority)}</span>
                </ListRowButton>
              ))}
            </div>
          </Card>

          <Card>
            <div className="cc-card-header">
              <h3>Context Inspector</h3>
              <span className="mono text-muted">{sessionContext?.profile ? humanize(sessionContext.profile) : '—'}</span>
            </div>
            {!sessionContext && <div className="text-muted text-sm">Loading context snapshot…</div>}
            {sessionContext && (
              <div className="cc-inspector-panel">
                <div className="cc-inspector-head">
                  <div className="cc-inspector-title">{sessionContext.session_kind ? humanize(sessionContext.session_kind) : 'Command Session'}</div>
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
          </Card>

          <Card>
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
          </Card>
        </aside>
      </div>
    </div>
  )
}
