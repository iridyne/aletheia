import { lazy, Suspense, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { RealtimeStatus } from '../lib/useRealtimeUpdates'
import type { ShellBootstrap } from '../api/boot'

const StatusDiagnostics = lazy(() => import('./StatusDiagnostics'))
const MODES = ['Observe', 'Paper', 'Full'] as const

function tone(value: boolean | undefined, fallback = 'muted'): string {
  return value === undefined ? fallback : value ? 'green' : 'red'
}

export default function StatusCluster({
  data,
  realtime,
}: {
  data?: ShellBootstrap
  realtime: RealtimeStatus
}) {
  const qc = useQueryClient()
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false)
  const [error, setError] = useState('')
  const paused = !!data?.gateway?.paused
  const running = !!data?.gateway?.running
  const killed = !!data?.kill_switch?.active
  const permission = String(data?.permission?.mode || '')
  const realtimeTone = realtime.state === 'connected' ? 'green' : realtime.state === 'disconnected' ? 'muted' : 'yellow'
  const pause = useMutation({
    mutationFn: paused ? api.resume : api.pause,
    onSuccess: () => { setError(''); void qc.invalidateQueries({ queryKey: ['ui-shell-bootstrap'] }) },
    onError: (e: Error) => setError(e.message || 'request failed'),
  })
  const setMode = useMutation({
    mutationFn: (mode: string) => api.setPermissionMode(mode),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['ui-shell-bootstrap'] }),
  })
  const nextMode = () => {
    const index = MODES.indexOf(permission as typeof MODES[number])
    if (index < 0) return
    const next = MODES[(index + 1) % MODES.length]
    if (next === 'Full' && !window.confirm('Switch to Full permission mode? This enables live order execution.')) return
    setMode.mutate(next)
  }

  return (
    <div className="topbar-status" aria-label="System status">
      <span className="status-cluster-lights" aria-hidden="true">
        <span className={`dot dot-${tone(running, 'red')}`} />
        <span className={`dot dot-${data?.connectivity?.state === 'reachable' ? 'green' : data?.connectivity?.state === 'unavailable' ? 'red' : 'muted'}`} />
        <span className={`dot dot-${data?.live?.enabled ? 'yellow' : 'muted'}`} />
        <span className={`dot dot-${killed ? 'red' : 'muted'}`} />
      </span>
      <span className="status-cluster-state">{paused ? 'PAUSED' : killed ? 'BLOCKED' : running ? 'READY' : 'OFFLINE'}</span>
      <span className="realtime-state mono" aria-label={`Realtime transport: ${realtime.state}`}>
        <span className={`dot dot-${realtimeTone}`} />{realtime.state === 'connected' ? 'push' : realtime.state}
      </span>
      <button className="perm-mode-btn mono" onClick={nextMode} disabled={!permission || setMode.isPending} title="Cycle permission mode">
        {permission || '—'}
      </button>
      <button className="topbar-btn secondary" onClick={() => pause.mutate()} disabled={!running || pause.isPending} title={paused ? 'Resume agents' : 'Pause agents'}>
        {pause.isPending ? '…' : paused ? '▶' : '⏸'}
      </button>
      <button className="topbar-btn secondary" onClick={() => setDiagnosticsOpen(value => !value)} aria-expanded={diagnosticsOpen}>
        {diagnosticsOpen ? 'Hide' : 'Details'}
      </button>
      {error && <span className="topbar-err" title={error}>{error}</span>}
      {diagnosticsOpen && (
        <Suspense fallback={<span className="topbar-err">loading…</span>}>
          <StatusDiagnostics />
        </Suspense>
      )}
    </div>
  )
}
