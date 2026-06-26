import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'
import { Card, CardHeader, Button, StatusText } from '../components/ui'
import type { StatusTone } from '../components/ui'
import { fmtSigned, fmtNum, useDecimals, humanize, fmtSymbol } from '../lib/format'
import { formatViolation, riskStateLabel } from '../lib/riskViolations'

/**
 * Risk & Controls — the brakes console (master-caution panel × Bloomberg risk monitor).
 *
 * Three honest zones instead of a flat card grid:
 *   1. Console band — a single derived master state (HALTED / BLOCKED / PAUSED /
 *      CAUTION / OPERATIONAL / OFFLINE) with the two big levers inline (kill switch,
 *      scheduler). The word is derived from real signals AND the components are shown
 *      beside it, so it summarizes without hiding anything.
 *   2. Live state — Active Risk (violations) + Live Exposure (per-symbol bars), side by
 *      side with `align-items: start` so a tall exposure list never stretches its peer.
 *   3. Configured Limits — read-only, grouped by concern, with live/demo limits paired
 *      on one row. Editing lives in Settings → Risk.
 */
export default function RiskControls() {
  const qc = useQueryClient()
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: riskStatus } = useQuery({ queryKey: ['risk-status'], queryFn: api.getRiskStatus, refetchInterval: 15000 })
  const { data: boundaries } = useQuery({ queryKey: ['risk-boundaries'], queryFn: api.getRiskBoundaries })
  const { data: killSwitch, isLoading: ksLoading } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch })
  const { data: portfolio, isLoading: portfolioLoading } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })
  // Open positions supply the market type per symbol so the exposure list can render the
  // spot/derivative convention (perp → no slash, spot → slash) via fmtSymbol. We MUST wait
  // for positions to load before rendering the exposure list — otherwise the dict is empty,
  // fmtSymbol can't tell perp from spot, and the rule says "never guess": it falls back to
  // the canonical (slashed) form, briefly displaying "ETH/USDT" for a USDT-perp until the
  // positions query settles. That violates the immutable owner rule (CLAUDE.md 2026-06-26).
  const { data: positions = [], isLoading: positionsLoading } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions })
  const exposureLoading = portfolioLoading || positionsLoading

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
  const s = (status ?? {}) as any
  const killed = !!killSwitch?.active
  const paused = !!s.paused
  const gatewayRunning = !!s.gateway_running
  const violations = (riskStatus?.violations as string[]) || []
  const riskState = String(riskStatus?.status || 'unknown')
  const riskTone: StatusTone = riskState === 'blocked' ? 'red' : riskState === 'warning' ? 'yellow' : riskState === 'healthy' ? 'green' : 'blue'

  const master = deriveMasterState({ gatewayRunning, killed, riskState, paused })

  // Exposure: largest magnitude first; bars scale to the biggest absolute exposure.
  const exposureRaw = (portfolio as any)?.exposure_by_symbol as Record<string, number> | undefined
  const marketTypeBySymbol: Record<string, string> = {}
  for (const p of positions as any[]) {
    if (p?.symbol && p?.market_type) marketTypeBySymbol[p.symbol] = p.market_type
  }
  const exposure = exposureRaw
    ? Object.entries(exposureRaw).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
    : []
  const exposureMax = exposure.reduce((m, [, v]) => Math.max(m, Math.abs(v)), 0) || 1
  const exposureTotal = exposure.reduce((sum, [, v]) => sum + v, 0)

  return (
    <div>
      <PageHeader
        title="Risk & Controls"
        role="Operate · the brakes console. Live risk state, the kill switch, gateway pause/resume, exposure, and the configured risk boundaries — the deep counterpart to the Cockpit's glanceable risk rail."
      />

      {/* Zone 1 — Console band: one master state + the two big levers */}
      <section className={`risk-console risk-console-${master.tone}`}>
        <div className="risk-console-top">
          <div className="risk-console-state">
            <span className="risk-console-dot" />
            <div className="risk-console-state-text">
              <div className="risk-console-word">{master.word}</div>
              <div className="risk-console-detail">{master.detail}</div>
            </div>
          </div>

          <div className="risk-console-levers">
            {/* Kill switch — the most destructive lever; keeps the confirm() friction. */}
            <div className="risk-lever">
              <div className="risk-lever-head">
                <span className="risk-lever-label">Kill Switch</span>
                {ksLoading
                  ? <span className="skeleton-bar-inline" />
                  : <StatusText tone={killed ? 'red' : 'green'}>{killed ? 'ENGAGED' : 'INACTIVE'}</StatusText>}
              </div>
              {!ksLoading && !killed && (
                <Button
                  variant="danger"
                  disabled={engageKill.isPending}
                  onClick={() => { if (confirm('Engage kill switch? All trading will halt immediately.')) engageKill.mutate() }}
                >
                  Engage Kill Switch
                </Button>
              )}
              {!ksLoading && killed && (
                <Button
                  disabled={releaseKill.isPending}
                  onClick={() => { if (confirm('Release Kill Switch? Trading will resume immediately.')) releaseKill.mutate() }}
                >
                  Release Kill Switch
                </Button>
              )}
            </div>

            {/* Scheduler — pause/resume all agents. */}
            <div className="risk-lever">
              <div className="risk-lever-head">
                <span className="risk-lever-label">Scheduler</span>
                <StatusText tone={paused ? 'yellow' : gatewayRunning ? 'green' : 'muted'}>
                  {paused ? 'PAUSED' : gatewayRunning ? 'ACTIVE' : 'OFFLINE'}
                </StatusText>
              </div>
              <div className="risk-lever-actions">
                <Button variant="secondary" onClick={() => pauseMut.mutate()} disabled={paused || !gatewayRunning || pauseMut.isPending}>Pause</Button>
                <Button onClick={() => resumeMut.mutate()} disabled={!paused || resumeMut.isPending}>Resume</Button>
              </div>
            </div>
          </div>
        </div>

        {/* Context strip — secondary state the operator may want but doesn't act on here. */}
        <div className="risk-console-meta">
          <span className="risk-console-meta-item">
            <span className="risk-console-meta-label">Permission</span>
            <span className="risk-console-meta-value">{humanize(String(s.permission_mode || '—'))}</span>
          </span>
          <span className="risk-console-meta-item">
            <span className="risk-console-meta-label">Live</span>
            <span className="risk-console-meta-value">{humanize(String(s.live_state || '—'))}</span>
          </span>
          <span className="risk-console-meta-item">
            <span className="risk-console-meta-label">Gateway</span>
            <span className="risk-console-meta-value">{gatewayRunning ? 'Running' : 'Stopped'}</span>
          </span>
        </div>
      </section>

      {/* Zone 2 — live state: violations + exposure (align-items:start, no stretch) */}
      <div className="risk-grid">
        <Card>
          <CardHeader title="Active Risk" actions={<StatusText tone={riskTone}>{riskStateLabel(riskState)}</StatusText>} />
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
              <span className="text-muted text-sm">No active violations — operating within all limits.</span>
            </div>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Live Exposure"
            meta={!exposureLoading && exposure.length > 0 ? `${exposure.length} symbol${exposure.length > 1 ? 's' : ''} open` : undefined}
          />
          {exposureLoading ? (
            <div className="skeleton"><div className="skeleton-bar" /><div className="skeleton-bar" /><div className="skeleton-bar" /></div>
          ) : exposure.length > 0 ? (
            <>
              <ul className="exposure-list">
                {exposure.map(([sym, val]) => (
                  <li key={sym} className="exposure-row">
                    <span className="exposure-sym mono">{fmtSymbol(sym, marketTypeBySymbol[sym])}</span>
                    <span className="exposure-bar-track">
                      <span
                        className={`exposure-bar ${val >= 0 ? 'pos' : 'neg'}`}
                        style={{ width: `${Math.max(2, (Math.abs(val) / exposureMax) * 100)}%` }}
                      />
                    </span>
                    <span className={`exposure-val mono ${val >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>{fmtSigned(val)}</span>
                  </li>
                ))}
              </ul>
              <div className="exposure-total">
                <span className="exposure-total-label">Total exposure</span>
                <span className="exposure-total-val mono">{fmtNum(exposureTotal)} USDT</span>
              </div>
            </>
          ) : (
            <p className="text-muted text-sm">No open exposure.</p>
          )}
        </Card>
      </div>

      {/* Zone 3 — Configured limits (read-only reference) */}
      <Card>
        <CardHeader title="Configured Limits" meta="Read-only — edit in Settings → Risk." />
        {boundaries ? (
          <LimitsView boundaries={boundaries as Record<string, any>} />
        ) : (
          <div className="skeleton"><div className="skeleton-bar" /><div className="skeleton-bar" /><div className="skeleton-bar" /></div>
        )}
      </Card>
    </div>
  )
}

interface MasterState { word: string; tone: StatusTone; detail: string }

/**
 * Derive the single console headline from real signals, in priority order of severity.
 * Never hides the underlying state — the kill-switch / scheduler / risk components are all
 * still rendered beside it in the band.
 */
function deriveMasterState(p: {
  gatewayRunning: boolean; killed: boolean; riskState: string; paused: boolean
}): MasterState {
  if (!p.gatewayRunning) return { word: 'OFFLINE', tone: 'muted', detail: 'Gateway is stopped — no agents are running.' }
  if (p.killed) return { word: 'HALTED', tone: 'red', detail: 'Kill switch engaged. All trading is stopped until released.' }
  if (p.riskState === 'blocked') return { word: 'BLOCKED', tone: 'red', detail: 'Risk engine is rejecting new orders. Clear the violations below.' }
  if (p.paused) return { word: 'PAUSED', tone: 'yellow', detail: 'Scheduler is paused. Agents are idle until resumed.' }
  if (p.riskState === 'warning') return { word: 'CAUTION', tone: 'yellow', detail: 'Running with active risk warnings — review the violations below.' }
  if (p.riskState === 'healthy') return { word: 'OPERATIONAL', tone: 'green', detail: 'Agents are running within all risk limits.' }
  return { word: 'STANDBY', tone: 'blue', detail: 'Risk state has not been evaluated yet.' }
}

// ── Configured limits — grouped by concern, live/demo paired ─────────────────
// Each row pulls one (single) or two (live/demo pair) keys out of the boundaries dict.
// Any key NOT claimed by a group falls through to an "Other" group so a schema addition
// is never silently dropped from the page.
type LimitRow = { label: string; pair: [string, string] } | { label: string; single: string }
interface LimitGroup { title: string; rows: LimitRow[] }

const LIMIT_GROUPS: LimitGroup[] = [
  {
    title: 'Position sizing',
    rows: [
      { label: 'Position notional cap', pair: ['max_live_position_notional_usdt', 'max_demo_position_notional_usdt'] },
      { label: 'Single-trade risk', pair: ['max_single_trade_risk_pct', 'max_demo_single_trade_risk_pct'] },
      { label: 'Max open positions', pair: ['max_open_positions_live', 'max_open_positions_demo'] },
    ],
  },
  {
    title: 'Leverage',
    rows: [
      { label: 'Futures leverage', pair: ['max_futures_leverage_live', 'max_futures_leverage_demo'] },
      { label: 'Major (BTC/ETH)', pair: ['max_leverage_major_live', 'max_leverage_major_demo'] },
      { label: 'Mid (SOL/BNB/XRP)', pair: ['max_leverage_mid_live', 'max_leverage_mid_demo'] },
      { label: 'Alt', pair: ['max_leverage_alt_live', 'max_leverage_alt_demo'] },
      { label: 'Account aggregate', pair: ['max_account_leverage_live', 'max_account_leverage_demo'] },
    ],
  },
  {
    title: 'Loss & margin',
    rows: [
      { label: 'Max daily loss', single: 'max_daily_loss_pct' },
      { label: 'Margin warning', single: 'futures_margin_warning_ratio' },
      { label: 'Force-close', single: 'futures_margin_force_close_ratio' },
      { label: 'Min liquidation buffer', single: 'min_liquidation_buffer_pct' },
    ],
  },
  {
    title: 'Exposure & capital',
    rows: [
      { label: 'Total live exposure', single: 'max_total_live_exposure_pct' },
      { label: 'Live limited cap', single: 'max_live_limited_cap_usdt' },
      { label: 'Starting capital', single: 'starting_capital_usdt' },
      { label: 'Sector concentration', single: 'max_sector_concentration_pct' },
      { label: 'Max portfolio beta', single: 'max_portfolio_beta' },
    ],
  },
  {
    title: 'Controls & flags',
    rows: [
      { label: 'Kill switch auto-trip', pair: ['kill_switch_enabled_live', 'kill_switch_enabled_demo'] },
      { label: 'Allow short', pair: ['allow_short_live', 'allow_short_demo'] },
      { label: 'Risk-check all orders', single: 'require_risk_check_for_all_orders' },
      { label: 'Correlation guard', single: 'correlation_guard_enabled' },
    ],
  },
]

function rowKeys(r: LimitRow): string[] {
  return 'pair' in r ? r.pair : [r.single]
}

function LimitsView({ boundaries }: { boundaries: Record<string, any> }) {
  const consumed = new Set<string>()
  const groups = LIMIT_GROUPS
    .map(g => {
      // Only keep rows whose keys actually exist in this config payload.
      const rows = g.rows.filter(r => rowKeys(r).some(k => boundaries[k] !== undefined))
      rows.forEach(r => rowKeys(r).forEach(k => consumed.add(k)))
      return { title: g.title, rows }
    })
    .filter(g => g.rows.length > 0)

  const others = Object.keys(boundaries).filter(k => !consumed.has(k))

  return (
    <div className="limits-groups">
      {groups.map(g => (
        <div key={g.title} className="limits-group">
          <div className="limits-group-title">{g.title}</div>
          {g.rows.map(r => (
            <div key={r.label} className="limit-row">
              <span className="limit-label">{r.label}</span>
              {'pair' in r ? (
                <span className="limit-pair">
                  <span className="limit-seg live">
                    <span className="limit-seg-tag">LIVE</span>
                    <span className="limit-seg-val">{fmtLimit(r.pair[0], boundaries[r.pair[0]])}</span>
                  </span>
                  <span className="limit-seg">
                    <span className="limit-seg-tag">DEMO</span>
                    <span className="limit-seg-val">{fmtLimit(r.pair[1], boundaries[r.pair[1]])}</span>
                  </span>
                </span>
              ) : (
                <span className="limit-single">{fmtLimit(r.single, boundaries[r.single])}</span>
              )}
            </div>
          ))}
        </div>
      ))}
      {others.length > 0 && (
        <div className="limits-group">
          <div className="limits-group-title">Other</div>
          {others.map(k => (
            <div key={k} className="limit-row">
              <span className="limit-label">{humanize(k)}</span>
              <span className="limit-single">{fmtLimit(k, boundaries[k])}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** Render a risk-limit value with a unit inferred from the key name. */
function fmtLimit(key: string, val: any): string {
  if (val == null) return '—'
  if (typeof val === 'boolean') return val ? 'ON' : 'OFF'
  if (typeof val === 'object') {
    return Object.entries(val).map(([k, v]) => `${humanize(k)}: ${v}`).join(' · ')
  }
  const n = Number(val)
  if (key.includes('leverage')) return `${val}×`
  if (key.endsWith('_pct')) return `${val}%`
  if (key.includes('ratio')) return Number.isFinite(n) ? `${Math.round(n * 100)}%` : String(val)
  if (key.includes('usdt') || key.includes('cap') || key.includes('capital') || key.includes('notional')) {
    return Number.isFinite(n) ? `$${n.toLocaleString()}` : String(val)
  }
  return String(val)
}
