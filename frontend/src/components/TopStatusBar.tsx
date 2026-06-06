import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

export default function TopStatusBar({ onCommandOpen, onNavToggle }: { onCommandOpen?: () => void; onNavToggle?: () => void }) {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['status'], queryFn: api.getStatus, refetchInterval: 10000 })
  const { data: ks } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch, refetchInterval: 10000 })
  const { data: runs = [] } = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns, refetchInterval: 15000 })
  const pauseMut = useMutation({ mutationFn: api.pause, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const resumeMut = useMutation({ mutationFn: api.resume, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })

  if (!data) {
    return (
      <header className="top-status-bar">
        <div className="tsb-left">
          <span className="dot dot-muted" />
          <span className="tsb-label">connecting...</span>
        </div>
        <div className="tsb-right" />
      </header>
    )
  }

  const running = !!data.gateway_running
  const paused = !!data.paused
  const live = String(data.live_state || 'live_disabled')
  const mode = String(data.exchange_mode || '')
  const perm = String(data.permission_mode || '')
  const killed = !!ks?.active

  const latestRun = (runs as any[])[0]
  const lastActivity = latestRun?.started_at || latestRun?.created_at || null

  return (
    <header className="top-status-bar">
      <div className="tsb-left">
        {onNavToggle && <button className="nav-toggle" onClick={onNavToggle} title="Toggle navigation">≡</button>}
        <span className={`pill ${running ? (paused ? 'pill-yellow' : 'pill-green') : 'pill-red'}`}>
          <span className={`dot ${running ? (paused ? 'dot-yellow' : 'dot-green') : 'dot-red'}`} />
          {running ? (paused ? 'Paused' : 'Running') : 'Stopped'}
        </span>
        <span className="pill pill-muted">{mode || '—'}</span>
        <span className={`pill ${live === 'live_disabled' ? 'pill-muted' : 'pill-yellow'}`}>
          {live.replace('live_', '')}
        </span>
        <span className="pill pill-blue">{perm}</span>
        {killed && <span className="pill pill-red"><span className="dot dot-red" />kill switch</span>}
        {lastActivity && (
          <span className="tsb-heartbeat" title="Last agent activity">
            <span className="dot dot-green" />
            <span className="tsb-label">{formatTime(lastActivity)}</span>
          </span>
        )}
      </div>
      <div className="tsb-right">
        <button
          className="tsb-btn secondary"
          onClick={() => paused ? resumeMut.mutate() : pauseMut.mutate()}
          title={paused ? 'Resume agents' : 'Pause agents'}
        >
          {paused ? '▶ Resume' : '⏸ Pause'}
        </button>
        {onCommandOpen && (
          <button className="tsb-btn tsb-cmd-btn" onClick={onCommandOpen} title="Command Center (Ctrl+K)">
            ⌘K
          </button>
        )}
      </div>
    </header>
  )
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return '—'
  }
}
