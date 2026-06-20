import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'
import { ActionRow, Card, CardHeader, Button, StatusText, TableWrap } from '../components/ui'
import { fmtSigned, useDecimals, humanize } from '../lib/format'
import { formatViolation, riskStateLabel } from '../lib/riskViolations'

export default function RiskControls() {
  const qc = useQueryClient()
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: riskStatus } = useQuery({ queryKey: ['risk-status'], queryFn: api.getRiskStatus, refetchInterval: 15000 })
  const { data: boundaries } = useQuery({ queryKey: ['risk-boundaries'], queryFn: api.getRiskBoundaries })
  const { data: killSwitch, isLoading: ksLoading } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch })
  const { data: portfolio } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })

  const engageKill = useMutation({
    mutationFn: () => api.setKillSwitch(true, 'manual_engage'),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['kill-switch'] }); qc.invalidateQueries({ queryKey: ['risk-status'] }) },
  })
  const releaseKill = useMutation({
    mutationFn: () => api.setKillSwitch(false),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['kill-switch'] }); qc.invalidateQueries({ queryKey: ['risk-status'] }) },
  })
  const pauseMut = useMutation({ mutationFn: api.pause, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const resumeMut = useMutation({ mutationFn: api.resume, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })

  useDecimals() // re-render when the display-decimals setting changes
  const killed = !!killSwitch?.active
  const paused = !!(status as any)?.paused
  const violations = (riskStatus?.violations as string[]) || []
  const riskState = String(riskStatus?.status || 'unknown')
  const riskTone = riskState === 'blocked' ? 'red' : riskState === 'warning' ? 'yellow' : riskState === 'healthy' ? 'green' : 'blue'

  const exposure = (portfolio as any)?.exposure_by_symbol as Record<string, number> | undefined

  return (
    <div>
      <PageHeader
        title="Risk & Controls"
        role="Operate · the brakes console. Live risk state, the kill switch, gateway pause/resume, exposure, and the configured risk boundaries — the deep counterpart to the Cockpit's glanceable risk rail."
      />

      <div className="risk-grid">
        {/* Risk State */}
        <Card>
          <CardHeader title="Risk State" />
          <TableWrap>
          <table>
            <tbody>
              <tr><th>Status</th><td><StatusText tone={riskTone}>{riskStateLabel(riskState)}</StatusText></td></tr>
              <tr><th>Violations</th><td>
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
                ) : <span className="text-muted">none</span>}
              </td></tr>
              <tr><th>Permission Mode</th><td><StatusText tone="blue">{humanize(String((status as any)?.permission_mode || '—'))}</StatusText></td></tr>
              <tr><th>Live State</th><td><StatusText tone={(status as any)?.live_state === 'live_disabled' ? 'muted' : 'yellow'}>{humanize(String((status as any)?.live_state || '—'))}</StatusText></td></tr>
            </tbody>
          </table>
          </TableWrap>
        </Card>

        {/* Kill Switch */}
        <Card>
          <CardHeader
            title="Kill Switch"
            actions={ksLoading
              ? <span className="skeleton-bar-inline" />
              : <StatusText tone={killed ? 'red' : 'green'}>{killed ? 'ENGAGED' : 'INACTIVE'}</StatusText>
            }
          />
          <p className="text-sm text-muted mb-3">
            {ksLoading
              ? <span className="skeleton-bar" />
              : killed
                ? 'All trading is halted. No new proposals will be executed until released.'
                : 'Kill switch is inactive. The system can execute approved proposals.'}
          </p>
          <ActionRow align="start">
            {!ksLoading && !killed && (
              <Button
                variant="danger"
                onClick={() => { if (confirm('Engage kill switch? All trading will halt immediately.')) engageKill.mutate() }}
              >
                Engage Kill Switch
              </Button>
            )}
            {!ksLoading && killed && (
              <Button onClick={() => {
                if (!confirm('Release Kill Switch? Trading will resume immediately.')) return
                releaseKill.mutate()
              }}>
                Release Kill Switch
              </Button>
            )}
          </ActionRow>
        </Card>

        {/* Gateway Control */}
        <Card>
          <CardHeader title="Gateway Control" />
          <TableWrap>
          <table>
            <tbody>
              <tr><th>Gateway</th><td><StatusText tone={(status as any)?.gateway_running ? 'green' : 'red'}>{(status as any)?.gateway_running ? 'Running' : 'Stopped'}</StatusText></td></tr>
              <tr><th>Scheduler</th><td><StatusText tone={paused ? 'yellow' : 'green'}>{paused ? 'Paused' : 'Active'}</StatusText></td></tr>
            </tbody>
          </table>
          </TableWrap>
          <ActionRow>
            <Button variant="secondary" onClick={() => pauseMut.mutate()} disabled={paused}>Pause All Agents</Button>
            <Button onClick={() => resumeMut.mutate()} disabled={!paused}>Resume</Button>
          </ActionRow>
        </Card>

        {/* Exposure */}
        <Card>
          <CardHeader title="Exposure by Symbol" />
          {exposure && Object.keys(exposure).length > 0 ? (
            <TableWrap>
            <table>
              <thead><tr><th>Symbol</th><th>Exposure (USDT)</th></tr></thead>
              <tbody>
                {Object.entries(exposure).map(([sym, val]) => (
                  <tr key={sym}>
                    <td className="mono">{sym}</td>
                    <td className={`mono ${val >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
                      {fmtSigned(val)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </TableWrap>
          ) : (
            <p className="text-muted text-sm">No open exposure</p>
          )}
        </Card>
      </div>

      {/* Risk Boundaries (read-only) */}
      <Card>
        <CardHeader title="Risk Boundaries (configured limits)" />
        {boundaries ? (
          <div className="boundaries-grid">
            {Object.entries(boundaries as Record<string, any>).map(([key, val]) => (
              <div key={key} className="drawer-field">
                <span className="drawer-field-label">{humanize(key)}</span>
                <span className="drawer-field-value mono">{fmtBoundary(val)}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="skeleton"><div className="skeleton-bar" /><div className="skeleton-bar" /><div className="skeleton-bar" /></div>
        )}
      </Card>
    </div>
  )
}

/** Render a risk-boundary value: scalars as-is, objects as readable "key: value" pairs
 *  (not a raw JSON.stringify dump). */
function fmtBoundary(val: any): string {
  if (val == null) return '—'
  if (typeof val === 'object') {
    return Object.entries(val).map(([k, v]) => `${humanize(k)}: ${v}`).join(' · ')
  }
  return String(val)
}
