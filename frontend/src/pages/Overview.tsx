import { useEffect, useLayoutEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import SetupBanner from '../components/SetupBanner'
import PnLZone from '../components/PnLZone'
import AgentFeed from '../components/AgentFeed'

/**
 * Cockpit / Overview (OVERVIEW_REDESIGN.md Decision 3).
 *
 * Reading order: money first (PnL zone) -> what the AI is doing (agent feed, main width)
 * -> what needs YOU (right rail: pending proposals = the one human-in-loop control point,
 * risk state, system health). Bloomberg dense zoning + OpenAlice agent-narrative-primary.
 */
export default function Overview() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: killSwitch } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch })
  const { data: riskStatus } = useQuery({ queryKey: ['risk-status'], queryFn: api.getRiskStatus })
  const { data: proposals = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })
  const { data: modelConfig } = useQuery({ queryKey: ['model'], queryFn: api.getModelConfig })
  const { data: modelHealth = [] } = useQuery({ queryKey: ['model-health'], queryFn: api.getModelHealth, refetchInterval: 5000 })
  const exchangeMode = String((status as any)?.exchange_mode || '')
  const { data: exchangeHealth } = useQuery({
    queryKey: ['bitget-test', exchangeMode],
    queryFn: () => api.testBitget(exchangeMode),
    refetchInterval: 15000,
    enabled: !!exchangeMode,
  })
  const pauseMut = useMutation({ mutationFn: api.pause, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const resumeMut = useMutation({ mutationFn: api.resume, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })

  // The two columns must stay the same vertical height. The RIGHT rail drives this:
  // each rail card has its own max-height (overflow → internal scroll), so the rail's
  // total natural height is bounded. The LEFT (Agent Activity) is the passive follower —
  // we measure the rail with ResizeObserver and feed that height into AgentFeed as
  // maxHeight, so AgentFeed scrolls internally to match. No hard ceiling on the feed
  // itself in 2-col mode; the rail's per-card caps already bound it.
  // In single-col mode (<=1000px) the layout stacks, so we use SINGLE_COL_CAP as a
  // sane fallback to keep AgentFeed from becoming a 2000px scroll wall.
  const SINGLE_COL_CAP = 600
  const [railEl, setRailEl] = useState<HTMLDivElement | null>(null)
  const [railHeight, setRailHeight] = useState<number | null>(null)
  const [twoCol, setTwoCol] = useState<boolean>(() => typeof window !== 'undefined' ? window.innerWidth > 1000 : true)
  // Measure the rail and re-measure on layout changes. useLayoutEffect ensures we read
  // the post-paint height; ResizeObserver tracks subsequent rail-content changes.
  // Callback ref (setRailEl) instead of useRef so this effect re-runs once the rail
  // actually mounts (the rail is gated behind `if (!status) return Loading`).
  useLayoutEffect(() => {
    if (!railEl) return
    setRailHeight(Math.round(railEl.getBoundingClientRect().height))
    const ro = new ResizeObserver(entries => {
      for (const e of entries) setRailHeight(Math.round(e.contentRect.height))
    })
    ro.observe(railEl)
    return () => ro.disconnect()
  }, [railEl])
  useEffect(() => {
    const onResize = () => setTwoCol(window.innerWidth > 1000)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  const feedCap = twoCol
    ? (railHeight ?? SINGLE_COL_CAP)
    : SINGLE_COL_CAP

  if (!status) return <p>Loading...</p>

  const s = status as any
  const running = !!s.gateway_running
  const paused = !!s.paused
  const killed = !!killSwitch?.active
  const pendingProposals = (proposals as any[]).filter(p => p.status === 'pending')

  // Risk
  const riskState = String(riskStatus?.status || 'not_available')
  const riskBadge = riskState === 'blocked' ? 'badge-red'
    : (riskState === 'warning' || riskState === 'not_available' || riskState === 'not_evaluated') ? 'badge-yellow'
    : 'badge-green'
  const violations = (riskStatus?.violations as string[]) || []

  // System pills (honest)
  const liveState = String(s.live_state || 'live_disabled')
  const liveBadge = liveState === 'live_disabled' ? 'pill-muted' : 'pill-yellow'
  const defaultProvider = String(modelConfig?.default_provider || '')
  const dph = (modelHealth as any[]).find(h => h.provider === defaultProvider)
  const modelPill = dph?.available ? 'pill-green' : dph ? 'pill-red' : 'pill-yellow'
  const modelText = dph?.available ? 'model ✓' : dph ? 'model ✗' : 'model …'
  const exPill = exchangeHealth?.connected ? 'pill-green' : exchangeHealth ? 'pill-red' : 'pill-yellow'
  const exText = exchangeHealth?.connected ? `${exchangeMode} ✓` : exchangeHealth ? `${exchangeMode} ✗` : `${exchangeMode} …`

  return (
    <div>
      <h2 className="page-title">Overview</h2>
      <SetupBanner />

      {/* Anchor 1 — PnL / account zone (full width) */}
      <PnLZone />

      {/* Anchor 2 — agent feed (main) + right rail */}
      <div className="cockpit-main">
        <AgentFeed maxHeight={feedCap ?? undefined} />

        <div className="cockpit-rail" ref={setRailEl}>
          {/* Approval Desk — the one human-in-loop control point */}
          <div className="card rail-card desk-panel">
            <h3>Approval Desk</h3>
            {pendingProposals.length > 0 ? (
              <>
                <div className="flex items-center gap-2">
                  <span className="badge badge-yellow">{pendingProposals.length} pending</span>
                  <span className="text-muted text-sm">awaiting review</span>
                </div>
                <ul className="approval-list">
                  {pendingProposals.slice(0, 4).map((p: any) => (
                    <li key={p.id}>
                      <span><span className="mono">{p.symbol}</span> {p.direction}</span>
                      <span className="ts">{p.created_at?.slice(5, 16)}</span>
                    </li>
                  ))}
                </ul>
                <button className="btn-control mt-3" onClick={() => navigate('/proposals')}>Review →</button>
              </>
            ) : (
              <p className="text-muted text-sm">No pending actions</p>
            )}
          </div>

          {/* Risk state */}
          <div className="card rail-card risk-panel">
            <h3>Risk State</h3>
            <div className="flex items-center gap-2">
              <span className={`badge ${riskBadge}`}>{riskState}</span>
              {killed && <span className="badge badge-red">KILL SWITCH</span>}
            </div>
            {violations.length > 0
              ? <div className="cockpit-violations">{violations.slice(0, 3).join(', ')}{violations.length > 3 && ` +${violations.length - 3} more`}</div>
              : <p className="text-muted text-sm mt-2">No violations</p>}
          </div>

          {/* System / connections — tabular status board (old-school console feel) */}
          <div className="card rail-card system-panel">
            <div className="flex items-center justify-between mb-2">
              <h3>System</h3>
              <div className="flex gap-2">
                <button className="btn-control" onClick={() => pauseMut.mutate()} disabled={paused || !running}>⏸</button>
                <button className="btn-control secondary" onClick={() => resumeMut.mutate()} disabled={!paused}>▶</button>
              </div>
            </div>
            <table className="status-board">
              <tbody>
                <StatusRow label="Gateway" light={running ? 'green' : 'red'} value={running ? 'up' : 'down'} />
                <StatusRow label="Scheduler" light={paused ? 'yellow' : 'green'} value={paused ? 'paused' : 'active'} />
                <StatusRow
                  label="Exchange"
                  light={exchangeHealth?.connected ? 'green' : exchangeHealth ? 'red' : 'yellow'}
                  value={`${String(s.exchange)} · ${exText}`}
                />
                <StatusRow
                  label="Model"
                  light={dph?.available ? 'green' : dph ? 'red' : 'yellow'}
                  value={defaultProvider ? `${defaultProvider} · ${modelText}` : modelText}
                />
                {/* Live + Permission + Kill collapsed into one row to keep the System
                    card height short — open positions moved to the PnL Exposure tile. */}
                <StatusRow
                  label="Mode"
                  light={killed ? 'red' : liveState === 'live_disabled' ? 'muted' : 'yellow'}
                  value={`${liveState.replace('live_', 'live: ')} · ${String(s.permission_mode)}${killed ? ' · KILL' : ''}`}
                />
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}

type Light = 'green' | 'red' | 'yellow' | 'blue' | 'muted'

/** One row of the System status board: label · status light · value. */
function StatusRow({ label, light, value }: { label: string; light: Light; value: string }) {
  return (
    <tr className="status-row">
      <th>{label}</th>
      <td className="status-cell">
        <span className={`status-light light-${light}`} />
        <span className="status-value">{value}</span>
      </td>
    </tr>
  )
}
