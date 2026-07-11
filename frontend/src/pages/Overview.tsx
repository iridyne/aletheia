import { useEffect, useLayoutEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { GoLiveScorecard } from '../api/client'
import SetupBanner from '../components/SetupBanner'
import PnLZone from '../components/PnLZone'
import AgentFeed from '../components/AgentFeed'
import { Badge, Button, Card, CardHeader, StatusText } from '../components/ui'
import { formatViolation, riskStateLabel } from '../lib/riskViolations'
import { humanize, fmtTimestamp, fmtSymbol } from '../lib/format'

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
  const { data: riskStatus } = useQuery({ queryKey: ['risk-status'], queryFn: api.getRiskStatus, refetchInterval: 15000 })
  const { data: proposals = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })
  const { data: modelConfig } = useQuery({ queryKey: ['model'], queryFn: api.getModelConfig })
  const { data: modelHealth = [] } = useQuery({ queryKey: ['model-health'], queryFn: api.getModelHealth, refetchInterval: 5000 })
  const { data: collectorHealth } = useQuery({
    queryKey: ['collector-health'],
    queryFn: api.getCollectorHealth,
    refetchInterval: 30000,
  })
  const { data: dataCoverage } = useQuery({
    queryKey: ['market-data-coverage'],
    queryFn: api.getDataCoverage,
    refetchInterval: 30000,
  })
  const exchangeMode = String((status as any)?.exchange_mode || '')
  const usesExternalExchange = !!(status as any)?.uses_external_exchange
  // #171 fix: pick the exchange-test endpoint by the CONFIGURED exchange, not
  // always Bitget. Previously an OKX/Binance deployment saw a stale Bitget
  // probe (false-red if bitget creds absent, false-green if bitget was up
  // while the real exchange was down).
  const exchangeName = String(
    (status as any)?.execution_exchange || (status as any)?.exchange || 'bitget',
  ).toLowerCase()
  const testExchangeConnection =
    exchangeName === 'okx' ? api.testOkx :
    exchangeName === 'binance' ? api.testBinance :
    api.testBitget
  const { data: exchangeHealth } = useQuery({
    queryKey: ['exchange-test', exchangeName, exchangeMode],
    queryFn: () => testExchangeConnection(exchangeMode),
    refetchInterval: 15000,
    enabled: usesExternalExchange,
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

  if (!status) return <div className="skeleton"><div className="skeleton-bar" /><div className="skeleton-bar" /><div className="skeleton-bar" /></div>

  const s = status as any
  const running = !!s.gateway_running
  const paused = !!s.paused
  const killed = !!killSwitch?.active
  const pendingProposals = (proposals as any[]).filter(p => p.status === 'pending')

  // Risk
  const riskState = String(riskStatus?.status || 'not_available')
  const riskTone = riskState === 'blocked' ? 'red'
    : (riskState === 'warning' || riskState === 'not_available' || riskState === 'not_evaluated') ? 'yellow'
    : 'green'
  const violations = (riskStatus?.violations as string[]) || []
  const latestRiskDecision = (riskStatus as any)?.latest_risk_decision

  // System state
  const liveState = String(s.live_state || 'live_disabled')
  const defaultProvider = String(modelConfig?.default_provider || '')
  const dph = (modelHealth as any[]).find(h => h.provider === defaultProvider)

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
          <Card className="rail-card desk-panel">
            <CardHeader title="Approval Desk" />
            {pendingProposals.length > 0 ? (
              <>
                <Badge tone="yellow">{pendingProposals.length}</Badge>
                <ul className="approval-list">
                  {pendingProposals.slice(0, 4).map((p: any) => (
                    <li key={p.id}>
                      <span><span className="mono">{fmtSymbol(p.symbol, p.market_type)}</span> {humanize(p.direction)}</span>
                      <span className="ts mono">{fmtTimestamp(p.created_at)}</span>
                    </li>
                  ))}
                </ul>
                <Button className="btn-control mt-3" onClick={() => navigate('/proposals')}>Review →</Button>
              </>
            ) : (
              <p className="text-muted text-sm">No pending actions</p>
            )}
          </Card>

          {/* Risk state */}
          <Card className="rail-card risk-panel">
            <div className="risk-head">
              <h3>Risk State</h3>
              <div className="risk-head-badges">
                {killed && <StatusText tone="red">KILL SWITCH</StatusText>}
                <StatusText tone={riskTone}>{riskStateLabel(riskState)}</StatusText>
              </div>
            </div>
            {violations.length > 0 ? (
              <ul className="risk-violations">
                {violations.map((v, i) => {
                  const f = formatViolation(v)
                  return (
                    <li key={i} className="risk-violation">
                      <span className={`risk-dot risk-dot-${f.severity}`} />
                      <div className="risk-violation-body">
                        <span className="risk-violation-label">{f.label}</span>
                        {f.detail && <span className="risk-violation-detail">{f.detail}</span>}
                      </div>
                    </li>
                  )
                })}
              </ul>
            ) : (
              <div className="risk-clear">
                <span className="risk-dot risk-dot-ok" />
                <span className="text-muted text-sm">No active violations</span>
              </div>
            )}
            {latestRiskDecision && !latestRiskDecision.approved && (
              <p className="hint mb-0">
                Latest check rejected {fmtTimestamp(latestRiskDecision.created_at)}. Historical decisions remain available in Risk &amp; Controls.
              </p>
            )}
          </Card>

          {/* System / connections — tabular status board (old-school console feel) */}
          <Card className="rail-card system-panel">
            <div className="flex items-center justify-between mb-2">
              <h3>System</h3>
              <div className="flex gap-2">
                <Button className="btn-control" onClick={() => pauseMut.mutate()} disabled={paused || !running}>⏸</Button>
                <Button className="btn-control" variant="secondary" onClick={() => resumeMut.mutate()} disabled={!paused}>▶</Button>
              </div>
            </div>
            <table className="status-board">
              <tbody>
                <StatusRow label="Gateway" light={running ? 'green' : 'red'} value={running ? 'up' : 'down'} />
                {/* #118 fix: previously the Scheduler light was green whenever
                    !paused, ignoring `scheduler_running` and `scheduler_state`
                    from /status — so a scheduler that had actually stopped
                    (state != 'running') still read green. Prefer the real
                    running flag when the backend supplies it, falling back to
                    the paused heuristic only when it's absent. */}
                {(() => {
                  const hasReal = typeof s?.scheduler_running === 'boolean'
                  const isRunning = hasReal ? !!s.scheduler_running : !paused
                  const state = String(s?.scheduler_state || (paused ? 'paused' : (isRunning ? 'active' : 'stopped')))
                  const light = paused ? 'yellow' : (isRunning ? 'green' : 'red')
                  return <StatusRow label="Scheduler" light={light} value={state} />
                })()}
                <StatusRow
                  label="Exchange"
                  light={
                    !usesExternalExchange ? 'muted' :
                    s.exchange_diverged ? 'yellow' :
                    exchangeHealth?.connected ? 'green' : exchangeHealth ? 'red' : 'yellow'
                  }
                  value={
                    !usesExternalExchange ? 'Local paper · no exchange' :
                    s.exchange_diverged
                      ? `${String(s.execution_exchange)} exec · ${String(s.market_data_exchange)} data`
                      : `${String(s.execution_exchange || s.exchange)} · ${exchangeMode}`
                  }
                  title={s.exchange_diverged
                    ? 'Order execution and market analysis use different exchanges. This split was explicitly configured.'
                    : undefined}
                />
                <StatusRow
                  label="Model"
                  light={dph?.available ? 'green' : dph ? 'red' : 'yellow'}
                  value={defaultProvider || '—'}
                />
                <StatusRow
                  label="Collectors"
                  light={
                    !collectorHealth ? 'yellow' :
                    collectorHealth.status === 'healthy' ? 'green' :
                    collectorHealth.status === 'initializing' ? 'muted' :
                    collectorHealth.status === 'degraded' ? 'red' : 'yellow'
                  }
                  value={
                    !collectorHealth ? 'loading' :
                    collectorHealth.status === 'initializing' ? 'initializing' :
                    collectorHealth.status === 'degraded'
                      ? `${collectorHealth.degraded_count} degraded · ${collectorHealth.recorded_count}/${collectorHealth.domain_count} reporting`
                      : collectorHealth.status === 'attention'
                        ? `${collectorHealth.transient_problem_count} transient · ${collectorHealth.recorded_count}/${collectorHealth.domain_count} reporting`
                        : `${collectorHealth.recorded_count}/${collectorHealth.domain_count} reporting`
                  }
                />
                <StatusRow
                  label="Data coverage"
                  light={
                    !dataCoverage ? 'yellow' :
                    dataCoverage.status === 'empty' ? 'muted' :
                    dataCoverage.critical_problem_count > 0 ? 'yellow' : 'green'
                  }
                  value={
                    !dataCoverage ? 'loading' :
                    `${dataCoverage.fresh_count}/${dataCoverage.domain_count} current`
                  }
                  title={dataCoverage?.domains.map(domain => (
                    `${domain.label}: ${domain.state} · ${domain.symbol_count} symbols · ${domain.row_count} rows`
                  )).join('\n')}
                />
                {/* Live + Permission + Kill collapsed into one row to keep the System
                    card height short — open positions moved to the PnL Exposure tile. */}
                <StatusRow
                  label="Mode"
                  light={killed ? 'red' : liveState === 'live_disabled' ? 'muted' : 'yellow'}
                  value={`Live: ${humanize(liveState)} · ${humanize(String(s.permission_mode))}${killed ? ' · KILL' : ''}`}
                />
              </tbody>
            </table>
          </Card>

          {/* Go-Live Scorecard */}
          <GoLiveScoreCard />
        </div>
      </div>
    </div>
  )
}

// ── Go-Live Scorecard Card ───────────────────────────────────────────────────
function GoLiveScoreCard() {
  const [expanded, setExpanded] = useState(false)
  const { data } = useQuery<GoLiveScorecard>({
    queryKey: ['go-live-scorecard'],
    queryFn: api.getGoLiveScorecard,
    staleTime: 60_000,
  })
  if (!data) return null

  const color = data.traffic_light === 'green' ? 'var(--green)'
    : data.traffic_light === 'yellow' ? 'var(--yellow)' : 'var(--red)'
  const label = data.traffic_light === 'green' ? 'READY'
    : data.traffic_light === 'yellow' ? 'CAUTION' : 'NOT READY'

  const circumference = 2 * Math.PI * 36
  const offset = circumference * (1 - data.composite_score / 100)

  return (
    <Card className="rail-card scorecard-panel">
      <div className="scorecard-header" onClick={() => setExpanded(!expanded)}>
        <svg width="56" height="56" viewBox="0 0 80 80">
          <circle cx="40" cy="40" r="36" fill="none" stroke="var(--border)" strokeWidth="6" />
          <circle cx="40" cy="40" r="36" fill="none" stroke={color} strokeWidth="6"
            strokeDasharray={circumference} strokeDashoffset={offset}
            strokeLinecap="round" transform="rotate(-90 40 40)" />
          <text x="40" y="44" textAnchor="middle" fill={color}
            fontSize="16" fontWeight="700" fontFamily="var(--font-mono)">
            {Math.round(data.composite_score)}
          </text>
        </svg>
        <div className="scorecard-title">
          <h3>Go-Live</h3>
          <StatusText tone={data.traffic_light === 'green' ? 'green' : data.traffic_light === 'yellow' ? 'yellow' : 'red'}>
            {label}
          </StatusText>
        </div>
        <span className="text-muted text-xs">{expanded ? '▲' : '▼'}</span>
      </div>
      {expanded && (
        <div className="scorecard-criteria">
          {data.criteria.map(c => {
            const barFill = { width: `${c.score}%`, background: c.status === 'pass' ? 'var(--green)' : c.status === 'warning' ? 'var(--yellow)' : 'var(--red)' }
            return (
              <div key={c.key} className="scorecard-row">
                <span className="scorecard-label">{c.label}</span>
                <div className="scorecard-bar-bg">
                  <div className="scorecard-bar-fill" style={barFill} />
                </div>
                <span className="scorecard-detail">{c.detail}</span>
              </div>
            )
          })}
          {data.blockers.length > 0 && (
            <div className="scorecard-blockers">
              <span className="text-xs text-error">Blockers:</span>
              {data.blockers.map((b, i) => <span key={i} className="text-xs text-error"> · {b}</span>)}
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

type Light = 'green' | 'red' | 'yellow' | 'blue' | 'muted'

/** One row of the System status board: label · status light · value. */
function StatusRow({
  label, light, value, title,
}: { label: string; light: Light; value: string; title?: string }) {
  return (
    <tr className="status-row">
      <th>{label}</th>
      <td className="status-cell">
        <span className={`status-light light-${light}`} />
        <span className="status-value" title={title}>{value}</span>
      </td>
    </tr>
  )
}
