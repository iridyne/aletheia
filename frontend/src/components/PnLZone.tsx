import { useLayoutEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import MetricTile from './MetricTile'
import PnLChart from '../charts/PnLChart'
import { fmtNum, fmtSigned, fmtDelta, fmtSymbol, useDecimals } from '../lib/format'

// Palette for pie slices — cycles if more than 6 coins (rare)
const SLICE_COLORS = ['#00e5ff', '#7c3aed', '#f59e0b', '#10b981', '#f43f5e', '#3b82f6']

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
  equity_usdt: number
  available_usdt: number
  unrealized_pnl: number
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
  onClick?: () => void; tooltip: Record<string, number>
}) {
  const [show, setShow] = useState(false)
  const entries = Object.entries(tooltip).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
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
              <span className={`mono ${val > 0 ? 'pnl-positive' : val < 0 ? 'pnl-negative' : ''}`}>
                {val > 0 ? '+' : ''}{fmtNum(val)}
              </span>
            </div>
          ))}
        </div>
      )}
    </button>
  )
}

/** PnL / Account zone — hero account value (UTA unified > exchange > local notional) + context tiles + equity curve. */
export default function PnLZone() {
  const navigate = useNavigate()
  const { data: portfolio } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: utaState } = useQuery({ queryKey: ['utaState'], queryFn: api.getUtaState, refetchInterval: 30000 })
  const [hovered, setHovered] = useState(false)
  const [rangeDays, setRangeDays] = useState<number | undefined>(30)

  const exchangeMode = String((status as any)?.exchange_mode || 'demo')
  const isLocalPaper = (status as any)?.execution_target === 'local_paper'

  useDecimals()
  const toneOf = (n: number | null) => (n == null ? 'muted' : n > 0 ? 'positive' : n < 0 ? 'negative' : 'neutral')

  const p = portfolio as any
  const uta = utaState as any

  // UTA unified equity takes priority when available and has multiple accounts
  const utaEquity = uta?.sync_status === 'ok' && uta?.total_equity_usdt > 0 ? uta.total_equity_usdt : null
  const utaAccounts: UTAAccount[] = uta?.accounts ?? []

  const exchangeEquity = p?.exchange_equity_usdt
  const syncStatus: string = utaEquity != null ? (uta?.sync_status ?? 'ok') : (p?.exchange_sync_status ?? 'unconfigured')
  const totalRaw = utaEquity ?? exchangeEquity ?? p?.total_value_usdt
  const totalValue = fmtNum(totalRaw)
  const syncCaveat =
    isLocalPaper
      ? 'local paper · no exchange'
      : utaEquity != null
      ? utaAccounts.length > 1 ? `${utaAccounts.length} accounts · unified` : 'unified'
      : exchangeEquity != null
        ? syncStatus === 'stale' ? 'data stale' : 'live'
        : syncStatus === 'unconfigured' ? 'exchange not configured'
        : syncStatus === 'error' ? 'sync error'
        : 'exchange sync pending'

  const breakdown: AssetItem[] = p?.exchange_asset_breakdown ?? []

  const todayRaw = p?.today_pnl
  const todayNum = todayRaw == null ? null : Number(todayRaw)
  const dayPct = todayNum != null && totalRaw ? (todayNum / Number(totalRaw)) * 100 : null

  const unrealNum = p?.unrealized_pnl == null ? null : Number(p.unrealized_pnl)
  const realNum = p?.all_realized_pnl == null ? null : Number(p.all_realized_pnl)

  const exposure = (p?.exposure_by_symbol || {}) as Record<string, number>
  const exposureTotal = Object.values(exposure).reduce((a, b) => a + Number(b || 0), 0)
  const exposureParts = Object.entries(exposure)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .map(([sym, val]) => {
      const pct = exposureTotal > 0 ? Math.round((Number(val) / exposureTotal) * 100) : 0
      return `${sym.replace('USDT', '')} ${pct}%`
    })
  const positionsCount = Number(p?.positions_count ?? 0)

  const todayPnlBySymbol = (p?.today_pnl_by_symbol || {}) as Record<string, number>
  const unrealBySymbol = (p?.unrealized_by_symbol || {}) as Record<string, number>

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

        {/* Context tiles — hover shows per-symbol breakdown, click navigates */}
        <div className="pnl-tiles">
          <HoverTile
            label="Today PnL"
            value={todayNum == null ? 'not available' : fmtDelta(todayNum)}
            tone={toneOf(todayNum)}
            context={todayNum == null ? 'no data' : dayPct != null ? `${fmtDelta(dayPct)}% of managed` : undefined}
            onClick={() => navigate('/trades?tab=blotter')}
            tooltip={todayPnlBySymbol}
          />
          <HoverTile
            label="Unrealized"
            value={fmtDelta(unrealNum)}
            tone={toneOf(unrealNum)}
            onClick={() => navigate('/trades?tab=blotter')}
            tooltip={unrealBySymbol}
          />
          <MetricTile
            label="Realized"
            value={fmtDelta(realNum)}
            tone={toneOf(realNum)}
            context="all-time"
            onClick={() => navigate('/trades?tab=analytics')}
          />
          <MetricTile
            label="Exposure"
            value={positionsCount === 0 ? 'flat' : `${positionsCount} pos`}
            tone={positionsCount > 0 ? 'neutral' : 'muted'}
            context={exposureParts.length ? exposureParts.slice(0, 3).join(' · ') : 'no open exposure'}
            onClick={() => navigate('/trades?tab=blotter')}
          />
        </div>
      </div>

      {/* Cumulative PnL curve (realized; last point includes open unrealized) */}
      <div className="card pnl-curve">
        <div className="pnl-curve-header">
          <h3>Cumulative PnL</h3>
          <div className="time-range-selector">
            {([{ label: '7D', days: 7 }, { label: '30D', days: 30 }, { label: '90D', days: 90 }, { label: 'All', days: undefined }] as const).map(opt => (
              <button
                key={opt.label}
                className={`time-range-btn${rangeDays === opt.days ? ' active' : ''}`}
                onClick={() => setRangeDays(opt.days)}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
        <PnLChart days={rangeDays} />
      </div>
    </div>
  )
}
