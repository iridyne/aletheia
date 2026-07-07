import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'
import { Card, CardHeader, Button, StatusText, Meter } from '../components/ui'
import type { StatusTone } from '../components/ui'
import { fmtSigned, fmtNum, useDecimals, humanize, fmtSymbol } from '../lib/format'
import { formatViolation, riskStateLabel } from '../lib/riskViolations'
import { deriveMasterState } from '../lib/systemState'

/** Extract a human-readable error from a fetch/mutation failure. */
function errMsg(e: any): string {
  return String(e?.message || e?.error || 'request failed')
}

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
  // 24h pipeline counts — surfaces "the system IS guarding" in the healthy state.
  // Refetches every 60s; the underlying SQL is a single COUNT GROUP BY so it's cheap.
  const { data: funnel } = useQuery({ queryKey: ['pipeline-funnel-24h'], queryFn: () => api.getPipelineFunnel(24), refetchInterval: 60_000 })
  const exposureLoading = portfolioLoading || positionsLoading

  // Inline error state — the brakes-console levers are trust-critical, so a silent
  // mutation failure is the worst-case (operator THINKS they halted trading but didn't).
  // Errors stick under the failing lever and only clear when the action is retried/succeeds.
  // Project rule: error feedback below the button, block layout — never a toast that auto-dismisses.
  const [killErr, setKillErr] = useState('')
  const [schedErr, setSchedErr] = useState('')

  const engageKill = useMutation({
    mutationFn: () => api.setKillSwitch(true, 'manual_engage'),
    onSuccess: () => { setKillErr(''); qc.invalidateQueries({ queryKey: ['kill-switch'] }); qc.invalidateQueries({ queryKey: ['risk-status'] }) },
    onError: (e: any) => setKillErr(errMsg(e)),
  })
  const releaseKill = useMutation({
    // confirm_release=true — the onClick confirm() dialog is the second confirmation.
    mutationFn: () => api.setKillSwitch(false, 'manual_release', true),
    onSuccess: () => { setKillErr(''); qc.invalidateQueries({ queryKey: ['kill-switch'] }); qc.invalidateQueries({ queryKey: ['risk-status'] }) },
    onError: (e: any) => setKillErr(errMsg(e)),
  })
  const pauseMut = useMutation({
    mutationFn: api.pause,
    onSuccess: () => { setSchedErr(''); qc.invalidateQueries({ queryKey: ['status'] }) },
    onError: (e: any) => setSchedErr(errMsg(e)),
  })
  const resumeMut = useMutation({
    mutationFn: api.resume,
    onSuccess: () => { setSchedErr(''); qc.invalidateQueries({ queryKey: ['status'] }) },
    onError: (e: any) => setSchedErr(errMsg(e)),
  })

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
  // #47/#53 fix: previously `marketTypeBySymbol[p.symbol] = p.market_type`
  // — a same-symbol spot+perp pair overwrote each other, so the last row
  // won and the display could flip between spot slash and perp concat
  // rendering on every position update. Track BOTH; when a symbol has
  // more than one market_type prefer the derivative (perp/futures/swap)
  // for display consistency — the exposure_by_symbol key from the backend
  // is currently symbol-only (#52 deferred, coupled with market_type
  // migration), so this is a display-honesty best-effort until the
  // backend groups by (symbol, market_type).
  const marketTypesForSymbol: Record<string, Set<string>> = {}
  for (const p of positions as any[]) {
    if (!p?.symbol || !p?.market_type) continue
    ;(marketTypesForSymbol[p.symbol] ??= new Set()).add(p.market_type)
  }
  const _preferPerp = (types: Set<string>): string => {
    for (const derivative of ['perp', 'usdt_futures', 'futures', 'swap']) {
      if (types.has(derivative)) return derivative
    }
    return Array.from(types)[0] ?? ''
  }
  const marketTypeBySymbol: Record<string, string> = Object.fromEntries(
    Object.entries(marketTypesForSymbol).map(([sym, types]) => [sym, _preferPerp(types)])
  )
  // A quick lookup so the row can flag "spot+perp merged" visually.
  const hasMixedMarketType = (sym: string) => (marketTypesForSymbol[sym]?.size ?? 0) > 1
  const exposure = exposureRaw
    ? Object.entries(exposureRaw).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
    : []
  const exposureMax = exposure.reduce((m, [, v]) => Math.max(m, Math.abs(v)), 0) || 1
  const exposureTotal = exposure.reduce((sum, [, v]) => sum + v, 0)

  // ── Active Risk · headroom ───────────────────────────────────────────────
  // We surface 3 caps the operator cares about most: open-positions count,
  // largest single-position notional, today's loss vs daily-loss budget.
  // Caps come from `boundaries` (demo vs live picked by exchange_mode).
  const exchangeMode = String(s.exchange_mode || 'demo').toLowerCase()
  const isLive = exchangeMode === 'live' || exchangeMode === 'bitget_live_limited'
  const b = (boundaries || {}) as Record<string, any>
  const equity = Number((portfolio as any)?.exchange_equity_usdt) || Number((portfolio as any)?.total_value_usdt) || 0
  const positionsCount = Number((portfolio as any)?.positions_count) || (positions as any[]).length || 0
  const posCap = Number(isLive ? b.max_open_positions_live : b.max_open_positions_demo) || null
  const notionalCap = Number(isLive ? b.max_live_position_notional_usdt : b.max_demo_position_notional_usdt) || null
  const dailyLossPct = Number(b.max_daily_loss_pct) || null
  const dailyLossCapUsdt = (dailyLossPct != null && equity > 0) ? equity * dailyLossPct / 100 : null
  const todayPnl = Number((portfolio as any)?.today_pnl) || 0
  const todayLoss = todayPnl < 0 ? Math.abs(todayPnl) : 0
  // positions API has no notional field — derive from quantity × current_price, open only.
  const largestPos = (positions as any[]).reduce((m, p) => {
    if (p?.status && p.status !== 'open') return m
    const qty = Math.abs(Number(p?.quantity) || 0)
    const px  = Number(p?.current_price) || Number(p?.entry_price) || 0
    return Math.max(m, qty * px)
  }, 0)

  const headroomRows = [
    posCap != null && { label: 'Open positions', used: positionsCount, cap: posCap, fmtUsed: (v: number) => String(Math.round(v)), fmtCap: (v: number) => String(Math.round(v)) },
    notionalCap != null && { label: 'Largest position', used: largestPos, cap: notionalCap, fmtUsed: (v: number) => `$${v.toFixed(1)}`, fmtCap: (v: number) => `$${v.toFixed(0)}` },
    dailyLossCapUsdt != null && { label: 'Daily loss', used: todayLoss, cap: dailyLossCapUsdt, fmtUsed: (v: number) => `$${v.toFixed(1)}`, fmtCap: (v: number) => `$${v.toFixed(0)}` },
  ].filter(Boolean) as { label: string; used: number; cap: number; fmtUsed: (v: number) => string; fmtCap: (v: number) => string }[]

  const fnl = funnel?.risk_decisions
  const topRejection = fnl?.top_rejection_reasons?.[0]
  const latest = (riskStatus as any)?.latest_risk_decision

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
              {killErr && <div className="lever-err" role="alert">✗ {killErr}</div>}
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
              {schedErr && <div className="lever-err" role="alert">✗ {schedErr}</div>}
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
          ) : riskState === 'healthy' ? (
            <div className="risk-clear">
              <span className="risk-dot risk-dot-ok" />
              <span className="text-muted text-sm">No active violations — operating within all limits.</span>
            </div>
          ) : (
            // #172 fix: "within all limits" used to render whenever the
            // violation list was empty — including when riskState was
            // 'unknown' / missing / never-evaluated (the API returned no
            // status yet). That's the WORST case to render as green:
            // "no data" mislabels itself as "all clear". Only claim clear
            // when the backend actually says riskState === 'healthy'.
            <div className="risk-clear">
              <span className="risk-dot" style={{ background: 'var(--muted)' }} />
              <span className="text-muted text-sm">
                Risk state <span className="mono">{riskState}</span> — not confirming clear until the risk engine reports "healthy".
              </span>
            </div>
          )}

          {/* 24h ACTIVITY — proves the guard is alive even in the healthy state. */}
          {fnl && (
            <div className="risk-section">
              <div className="risk-section-label">24h Activity</div>
              <div className="risk-activity">
                <div className="risk-activity-row">
                  <span className="risk-activity-key">Risk checks</span>
                  <span className="risk-activity-val mono">
                    <span className="risk-good">{fnl.approved}</span>
                    <span className="risk-faint">/</span>
                    <span className={fnl.rejected > 0 ? 'risk-bad' : 'risk-faint'}>{fnl.rejected}</span>
                  </span>
                  <span className="risk-activity-meta">{fnl.approve_rate_pct != null ? `${Math.round(fnl.approve_rate_pct)}% pass` : '—'}</span>
                </div>
                {topRejection && (
                  <div className="risk-activity-row">
                    <span className="risk-activity-key">Top reject</span>
                    <span className="risk-activity-val" title={topRejection.message}>{shortReject(topRejection.message)}</span>
                    <span className="risk-activity-meta">{topRejection.count}×</span>
                  </div>
                )}
                {funnel?.fills != null && (
                  <div className="risk-activity-row">
                    <span className="risk-activity-key">Fills</span>
                    <span className="risk-activity-val mono">{funnel.fills}</span>
                    <span className="risk-activity-meta">orders executed</span>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* HEADROOM — how close are we to each cap. */}
          {headroomRows.length > 0 && (
            <div className="risk-section">
              <div className="risk-section-label">Headroom · {humanize(exchangeMode)}</div>
              <div className="headroom-list">
                {headroomRows.map(row => {
                  const pct = row.cap > 0 ? (row.used / row.cap) * 100 : 0
                  const tone = pct >= 100 ? 'crit' : pct >= 70 ? 'warn' : 'ok'
                  const w = Math.min(100, Math.max(2, pct))
                  return (
                    <div key={row.label} className="headroom-row">
                      <span className="headroom-label">{row.label}</span>
                      <span className="headroom-bar-track">
                        <Meter pct={w} className={`headroom-bar-fill ${tone}`} />
                      </span>
                      <span className="headroom-val mono">{row.fmtUsed(row.used)} <span className="risk-faint">/ {row.fmtCap(row.cap)}</span></span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* LATEST CHECK — when did the risk engine last decide anything. */}
          {latest && (
            <div className="risk-section">
              <div className="risk-section-label">Latest Check</div>
              <div className="latest-check">
                <span className="latest-check-time mono">{fmtRelative(latest.created_at)}</span>
                <StatusText tone={latest.approved ? 'green' : 'red'}>{latest.approved ? 'Approved' : 'Rejected'}</StatusText>
                {latest.risk_score != null && <span className="latest-check-meta">risk score {latest.risk_score}</span>}
              </div>
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
                    <span className="exposure-sym mono">
                      {fmtSymbol(sym, marketTypeBySymbol[sym])}
                      {/* #47/#53 fix: flag when this symbol has BOTH spot and
                          perp/futures locally. The backend exposure figure
                          groups them under one key (#52 deferred), so this
                          note tells the operator the number is not a pure-
                          market-type slice — no silent lie. */}
                      {hasMixedMarketType(sym) && (
                        <span className="text-muted text-sm" title="Both spot and perp open — exposure is combined">
                          {' '}(spot+perp)
                        </span>
                      )}
                    </span>
                    <span className="exposure-bar-track">
                      <Meter
                        pct={Math.max(2, (Math.abs(val) / exposureMax) * 100)}
                        className={`exposure-bar ${val >= 0 ? 'pos' : 'neg'}`}
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

/* deriveMasterState moved to lib/systemState.ts — shared with topbar StatusBar so
 * the same signals never produce two different colors on two surfaces (#2 unified). */

/** "Rejected: max_demo_positions_reached:6" → "max demo positions reached". */
function shortReject(msg: string): string {
  return (msg || '').replace(/^Rejected:\s*/, '').replace(/:[\d.>=,\s]+/g, ' ').replace(/_/g, ' ').trim().slice(0, 64)
}

/** UTC-safe ISO → "6h ago" etc. Mirrors lib/format normalizeIso behavior. */
function fmtRelative(iso?: string | null): string {
  if (!iso) return '—'
  const norm = iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`
  const t = new Date(norm).getTime()
  if (!Number.isFinite(t)) return '—'
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000))
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60); if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60); if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
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
