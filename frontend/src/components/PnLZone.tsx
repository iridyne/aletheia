import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import MetricTile from './MetricTile'
import type { OverviewBootstrap, OverviewUtaAccount } from '../api/boot'
import { fmtNum, fmtSigned, fmtCompactSigned, fmtSymbol } from '../lib/format'

// Legacy local-paper contract is surfaced by AccountSnapshot: execution_target === 'local_paper'.

const AccountEquityChart = lazy(() => import('../charts/AccountEquityChart'))
const PnLChart = lazy(() => import('../charts/PnLChart'))
// Bootstrap refresh replaces the old portfolio query cadence:
// const OVERVIEW_PNL_REFRESH_MS = 10_000
// refetchInterval: OVERVIEW_PNL_REFRESH_MS

function DeferredCurve({ days, kind }: { days?: number; kind: 'equity' | 'pnl' }) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const host = hostRef.current
    if (!host || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) {
        setVisible(true)
        observer.disconnect()
      }
    }, { rootMargin: '160px' })
    observer.observe(host)
    return () => observer.disconnect()
  }, [])
  return (
    <div ref={hostRef} style={{ minHeight: 220 }}>
      {visible
        ? <Suspense fallback={<div className="chart-placeholder" role="status">Chart loads…</div>}>
            {kind === 'equity' ? <AccountEquityChart days={days} /> : <PnLChart days={days} />}
          </Suspense>
        : <div className="chart-placeholder" role="status">Chart loads when visible.</div>}
    </div>
  )
}

// Palette for pie slices — cycles if more than 6 coins (rare)
const SLICE_COLORS = ['#00e5ff', '#7c3aed', '#f59e0b', '#10b981', '#f43f5e', '#3b82f6']
// The backend refreshes local mark prices on this cadence. Keep the Overview
// money panel aligned so an open page never freezes at its initial value.

interface AssetItem { coin: string; usdt_value: number; pct: number }

function AssetPie({ items }: { items: AssetItem[] }) {
  if (!items.length) return null
  const r = 28; const cx = 32; const cy = 32
  let angle = -Math.PI / 2  // start at top
  const slices = items.map((item, i) => {
    const sweep = (item.pct / 100) * 2 * Math.PI
    const x1 = cx + r * Math.cos(angle)
    const y1 = cy + r * Math.sin(angle)
    angle += sweep
    const x2 = cx + r * Math.cos(angle)
    const y2 = cy + r * Math.sin(angle)
    const large = sweep > Math.PI ? 1 : 0
    return (
      <path
        key={item.coin}
        d={`M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${large},1 ${x2},${y2} Z`}
        fill={SLICE_COLORS[i % SLICE_COLORS.length]}
        opacity={0.85}
      />
    )
  })
  return (
    <svg width="64" height="64" viewBox="0 0 64 64" style={{ flexShrink: 0 }}>
      {slices}
      {/* inner hole */}
      <circle cx={cx} cy={cy} r={14} fill="var(--surface, #0d1117)" />
    </svg>
  )
}

function AssetTooltip({ items }: { items: AssetItem[] }) {
  return (
    <div className="asset-tooltip">
      {items.map((item, i) => (
        <div key={item.coin} className="asset-tooltip-row">
          <span className="asset-dot" style={{ background: SLICE_COLORS[i % SLICE_COLORS.length] }} />
          <span className="asset-coin">{item.coin}</span>
          <span className="asset-value">{fmtNum(item.usdt_value)} USDT</span>
          <span className="asset-pct">{item.pct.toFixed(1)}%</span>
        </div>
      ))}
    </div>
  )
}

interface UTAAccount {
  broker_id: string
  display_name: string
  equity_usdt: number | null
  available_usdt: number | null
  unrealized_pnl: number | null
}

export function overviewTodayPnl(account?: OverviewBootstrap['account']): number | null {
  if (!account || account.today_realized_pnl_complete === false || account.today_realized_pnl == null) return null
  const value = Number(account.today_realized_pnl)
  return Number.isFinite(value) ? value : null
}

function BrokerBreakdown({ accounts, open }: { accounts: UTAAccount[]; open: boolean }) {
  if (!open || accounts.length <= 1) return null
  return (
    <div className="broker-breakdown">
      {accounts.map(a => (
        <div key={a.broker_id} className="broker-row">
          <span className="broker-name">{a.display_name}</span>
          <span className="broker-equity">{fmtNum(a.equity_usdt)} USDT</span>
        </div>
      ))}
    </div>
  )
}

type Tone = 'neutral' | 'positive' | 'negative' | 'muted'

const TOOLTIP_GAP = 6      // px gap between trigger and tooltip
const VIEWPORT_MARGIN = 8  // px min distance the tooltip keeps from any viewport edge

/**
 * Position a tooltip relative to its trigger with viewport boundary detection.
 * Prefers opening BELOW the trigger; flips above only when there isn't room
 * below but there is above. Horizontally clamps the box inside the viewport so
 * its content is never cut off. Uses position:fixed so it escapes any ancestor
 * `overflow:hidden` clipping (e.g. the sticky topbar / card).
 */
function useTooltipPosition(open: boolean) {
  // HTMLElement (not HTMLDivElement) so callers can attach to <button> too — we only
  // need getBoundingClientRect/offsetWidth/offsetHeight, available on any element.
  const triggerRef = useRef<HTMLElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)

  useLayoutEffect(() => {
    if (!open) { setPos(null); return }
    const trigger = triggerRef.current
    const tip = tooltipRef.current
    if (!trigger || !tip) return

    const t = trigger.getBoundingClientRect()
    const tw = tip.offsetWidth
    const th = tip.offsetHeight
    const vw = window.innerWidth
    const vh = window.innerHeight

    // Vertical: below by default, flip above only if it doesn't fit below but fits above.
    const spaceBelow = vh - t.bottom
    const fitsBelow = spaceBelow >= th + TOOLTIP_GAP + VIEWPORT_MARGIN
    const fitsAbove = t.top >= th + TOOLTIP_GAP + VIEWPORT_MARGIN
    const placeAbove = !fitsBelow && fitsAbove
    let top = placeAbove ? t.top - th - TOOLTIP_GAP : t.bottom + TOOLTIP_GAP
    // Last-resort clamp so a too-tall tooltip still stays on screen.
    top = Math.max(VIEWPORT_MARGIN, Math.min(top, vh - th - VIEWPORT_MARGIN))

    // Horizontal: align to trigger's left, then clamp both edges into the viewport.
    let left = t.left
    left = Math.min(left, vw - tw - VIEWPORT_MARGIN)
    left = Math.max(VIEWPORT_MARGIN, left)

    setPos({ top, left })
  }, [open])

  return { triggerRef, tooltipRef, pos }
}

function HoverTile({ label, value, tone = 'neutral', context, onClick, tooltip }: {
  label: string; value: React.ReactNode; tone?: Tone; context?: React.ReactNode
  onClick?: () => void; tooltip: Record<string, number | null>
}) {
  const [show, setShow] = useState(false)
  const entries = Object.entries(tooltip).sort(
    (a, b) => Math.abs(b[1] ?? 0) - Math.abs(a[1] ?? 0),
  )
  const toneClass = tone === 'positive' ? 'pnl-positive' : tone === 'negative' ? 'pnl-negative' : tone === 'muted' ? 'text-muted' : ''
  const hasTooltip = entries.length > 0
  const { triggerRef, tooltipRef, pos } = useTooltipPosition(show && hasTooltip)
  const open = () => setShow(true)
  const close = () => setShow(false)
  return (
    // Real <button> so Tab/Enter/Space reach the tile, and focus also reveals the
    // tooltip (parity with hover) — WCAG 2.1.1 keyboard + 1.4.13 content-on-hover.
    // position:relative moved to .metric-tile.clickable in CSS (no inline style).
    <button
      ref={triggerRef as React.RefObject<HTMLButtonElement>}
      type="button"
      className="card metric-tile clickable"
      onClick={onClick}
      onMouseEnter={open}
      onMouseLeave={close}
      onFocus={open}
      onBlur={close}
      onKeyDown={(e) => { if (e.key === 'Escape') close() }}
    >
      <div className="metric-label">{label}</div>
      <div className={`metric-value ${toneClass}`}>{value}</div>
      {context && <div className="metric-context">{context}</div>}
      {show && hasTooltip && (
        <div
          ref={tooltipRef}
          className="tile-tooltip"
          // Render off-screen on the first measuring pass (pos === null) to avoid a flash
          // at the wrong spot, then snap to the computed viewport-clamped position.
          style={pos
            ? { top: pos.top, left: pos.left, visibility: 'visible' }
            : { top: 0, left: 0, visibility: 'hidden' }}
        >
          {entries.map(([sym, val]) => (
            <div key={sym} className="tile-tooltip-row">
              {/* #15 fix: fmtSymbol handles the spot/perp convention; the
                  tooltip payload here is symbol-keyed (no market_type at hand),
                  so it renders as spot which matches the prior behaviour but
                  routes through the shared helper so future market_type-aware
                  callers change one place. */}
              <span className="mono">{fmtSymbol(sym)}</span>
              <span className={`mono ${val != null && val > 0 ? 'pnl-positive' : val != null && val < 0 ? 'pnl-negative' : ''}`}>
                {val != null && val > 0 ? '+' : ''}{fmtNum(val)}
              </span>
            </div>
          ))}
        </div>
      )}
    </button>
  )
}

/** PnL / Account zone — hero account value (UTA unified > exchange > local notional) + context tiles + equity curve. */
export default function PnLZone({ bootstrap, unavailable = false }: { bootstrap?: OverviewBootstrap; unavailable?: boolean }) {
  const navigate = useNavigate()
  const [hovered, setHovered] = useState(false)
  const [rangeDays, setRangeDays] = useState<number | undefined>(30)
  const [curveKind, setCurveKind] = useState<'equity' | 'pnl'>('equity')

  const portfolio = bootstrap?.account
  const accountState: 'loading' | 'unavailable' | 'ready' = unavailable
    ? 'unavailable'
    : !bootstrap
      ? 'loading'
    : bootstrap.errors?.portfolio || !portfolio
      ? 'unavailable'
      : 'ready'
  const exchangeMode = accountState === 'ready' ? String(portfolio?.exchange_mode || 'not available') : 'not available'
  const isLocalPaper = accountState === 'ready' && portfolio?.execution_mode === 'paper_internal'

  const toneOf = (n: number | null) => (n == null ? 'muted' : n > 0 ? 'positive' : n < 0 ? 'negative' : 'neutral')

  const p = accountState === 'ready' ? portfolio : undefined
  const uta = p?.uta_state

  // UTA unified equity takes priority when available and has multiple accounts
  const utaEquity = uta && uta.sync_status === 'ok' && typeof uta.total_equity_usdt === 'number' && uta.total_equity_usdt > 0
    ? uta.total_equity_usdt
    : null
  const utaAccounts: UTAAccount[] = (uta?.accounts ?? []).map((account: OverviewUtaAccount) => ({
    broker_id: String(account.broker_id || 'unknown'),
    display_name: String(account.display_name || account.broker_id || 'Broker'),
    equity_usdt: account.equity_usdt ?? null,
    available_usdt: account.available_usdt ?? null,
    unrealized_pnl: account.unrealized_pnl ?? null,
  }))

  const exchangeEquity = p?.exchange_equity_usdt ?? null
  const syncStatus: string = utaEquity != null ? (uta?.sync_status ?? 'ok') : (p?.exchange_sync_status ?? 'unconfigured')
  const totalRaw = utaEquity ?? exchangeEquity ?? p?.total_value_usdt ?? null
  const totalValue = accountState === 'loading' ? 'loading' : accountState === 'unavailable' ? 'not available' : fmtNum(totalRaw)
  const syncCaveat =
    accountState === 'loading'
      ? 'loading'
      : accountState === 'unavailable'
        ? 'not available'
        : isLocalPaper
      ? 'local paper · no exchange'
      : utaEquity != null
      ? utaAccounts.length > 1 ? `${utaAccounts.length} accounts · unified` : 'unified'
      : exchangeEquity != null
        ? syncStatus === 'stale' ? 'data stale' : 'live'
        : syncStatus === 'unconfigured' ? 'exchange not configured'
        : syncStatus === 'error' ? 'sync error'
        : 'exchange sync pending'

  const breakdown: AssetItem[] = p?.exchange_asset_breakdown ?? []

  const todayComplete = accountState === 'ready' && p?.today_realized_pnl_complete !== false
  const todayNum = accountState === 'ready' ? overviewTodayPnl(p) : null
  const dayPct = todayNum != null && totalRaw ? (todayNum / Number(totalRaw)) * 100 : null

  const unrealNum = p?.unrealized_pnl == null ? null : Number(p.unrealized_pnl)
  const realNum = p?.all_realized_pnl == null ? null : Number(p.all_realized_pnl)
  const realizedUnavailableCount = Number(p?.all_realized_pnl_unavailable_count ?? 0)
  const historicalNum = p?.all_historical_reconciled_pnl == null ? null : Number(p.all_historical_reconciled_pnl)
  const historicalCount = Number(p?.all_historical_reconciled_count ?? 0)
  const historicalUnavailable = Number(p?.all_historical_reconciliation_unavailable_count ?? 0)
  const realizedContext = accountState === 'loading'
    ? 'loading'
    : accountState === 'unavailable'
      ? 'not available'
      : realizedUnavailableCount > 0
    ? `partial · ${realizedUnavailableCount} close${realizedUnavailableCount === 1 ? '' : 's'} unverified`
    : realNum != null
      ? 'all-time'
      : historicalNum != null
        ? `${historicalCount} legacy closes reconciled: ${fmtSigned(historicalNum)}${historicalUnavailable ? ` · ${historicalUnavailable} still unavailable` : ''}`
        : 'realized economics unavailable'

  const exposure = (p?.exposure_by_symbol || {}) as Record<string, number>
  const exposureTotal = Object.values(exposure).reduce((a, b) => a + Number(b || 0), 0)
  const exposureParts = Object.entries(exposure)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .map(([sym, val]) => {
      const pct = exposureTotal > 0 ? Math.round((Number(val) / exposureTotal) * 100) : 0
      return `${sym.replace('USDT', '')} ${pct}%`
    })
  const positionsCount = p?.positions_count == null ? null : Number(p.positions_count)

  const todayPnlBySymbol = p?.today_pnl_by_symbol || {}
  const unrealBySymbol = p?.unrealized_by_symbol || {}
  const metricValue = (value: number | null) => accountState === 'loading' ? 'loading' : value == null ? 'not available' : fmtCompactSigned(value)

  return (
    <div className="pnl-zone">
      <div className="pnl-cluster">
        {/* Hero — account value + pie + hover shows broker breakdown */}
        <div
          className="card pnl-hero"
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
        >
          <div className="pnl-hero-inner">
            <div className="pnl-hero-text">
              <div className="metric-label">Account Value</div>
              <div className={`pnl-hero-value${totalRaw == null ? ' is-muted' : ''}`}>
                {totalValue} <span className="hero-unit">USDT</span>
              </div>
              <div className="metric-caveat">{exchangeMode} · {syncCaveat}</div>
            </div>
            {breakdown.length > 0 && (
              <div className="pnl-pie-wrap">
                <AssetPie items={breakdown} />
              </div>
            )}
          </div>
          {hovered && breakdown.length > 0 && <AssetTooltip items={breakdown} />}
          {hovered && utaAccounts.length > 1 && (
            <div className="broker-tooltip">
              {utaAccounts.map(a => (
                <div key={a.broker_id} className="broker-row">
                  <span className="broker-name">{a.display_name}</span>
                  <span className="broker-equity">{fmtNum(a.equity_usdt)} USDT</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Context tiles — hover shows per-symbol breakdown, click navigates.
            fmtCompactSigned enforces a fixed ~7-char budget: sub-1 values
            get 3 sig figs (or subscript notation past 3 leading zeros),
            4-digit-and-up integers switch to k/M/B suffix. Prevents the
            metric-value from overflowing a narrow 4-col tile at 800px
            viewports as the PnL magnitude grows. Full precision remains
            available in the /trades blotter drill-down. */}
        <div className="pnl-tiles">
          <HoverTile
            label="Today PnL"
            value={metricValue(todayNum)}
            tone={toneOf(todayNum)}
            context={accountState === 'loading'
              ? 'loading'
              : accountState === 'unavailable'
                ? 'not available'
                : todayNum == null
                  ? todayComplete ? 'no data' : 'funding or close economics unavailable'
              : !todayComplete
                ? dayPct != null ? `${fmtCompactSigned(dayPct)}% · some closes unverified` : 'some closes unverified'
                : dayPct != null ? `${fmtCompactSigned(dayPct)}% of managed` : undefined}
            onClick={() => navigate('/trades?tab=blotter')}
            tooltip={todayComplete ? todayPnlBySymbol : {}}
          />
          <HoverTile
            label="Unrealized"
            value={metricValue(unrealNum)}
            tone={toneOf(unrealNum)}
            onClick={() => navigate('/trades?tab=blotter')}
            tooltip={unrealBySymbol}
          />
          <MetricTile
            label="Realized"
            value={metricValue(realNum)}
            tone={toneOf(realNum)}
            context={realizedContext}
            onClick={() => navigate('/trades?tab=analytics')}
          />
          <MetricTile
            label="Exposure"
            value={accountState === 'loading' ? 'loading' : accountState === 'unavailable' ? 'not available' : positionsCount == null ? 'not available' : positionsCount === 0 ? 'flat' : `${positionsCount} pos`}
            tone={positionsCount != null && positionsCount > 0 ? 'neutral' : 'muted'}
            context={accountState === 'loading' ? 'loading' : accountState === 'unavailable' ? 'not available' : positionsCount == null ? 'not available' : exposureParts.length ? exposureParts.slice(0, 3).join(' · ') : 'no open exposure'}
            onClick={() => navigate('/trades?tab=blotter')}
          />
        </div>
      </div>

      {/* Account equity and verified PnL remain distinct projections with one shared window. */}
      <div className="card pnl-curve">
        <div className="pnl-curve-header">
          <div className="time-range-selector" aria-label="Curve type">
            {([
              { label: 'Account Equity', kind: 'equity' },
              { label: 'PnL Curves', kind: 'pnl' },
            ] as const).map(option => (
              <button
                type="button"
                key={option.kind}
                className={`time-range-btn${curveKind === option.kind ? ' active' : ''}`}
                aria-pressed={curveKind === option.kind}
                onClick={() => setCurveKind(option.kind)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="time-range-selector">
            {([{ label: '7D', days: 7 }, { label: '30D', days: 30 }, { label: '90D', days: 90 }, { label: 'All', days: undefined }] as const).map(opt => (
              <button
                type="button"
                key={opt.label}
                className={`time-range-btn${rangeDays === opt.days ? ' active' : ''}`}
                aria-pressed={rangeDays === opt.days}
                onClick={() => setRangeDays(opt.days)}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
        <DeferredCurve days={rangeDays} kind={curveKind} />
      </div>
    </div>
  )
}
