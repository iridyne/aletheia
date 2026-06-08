import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { fmtNum, fmtSigned, useDecimals } from '../lib/format'

/**
 * MarketTicker — Bloomberg-flavored top-bar ticker.
 *
 * Layout (left→right):
 *   local clock · UTC clock   |   crypto tokens (BTC/ETH/SOL — real)   |   indices (SPX/VIX — pending)
 *
 * Honesty:
 *   - Crypto = real Bitget snapshots from /api/market/snapshots (already in use elsewhere).
 *   - Indices = NOT WIRED. We render '—' + a "feed not wired" tooltip rather than fabricate.
 *     When the backend lands GET /api/market/indices (see WEBUI_BACKEND_HANDOFF.md §13),
 *     swap the placeholder block with a useQuery and the slots auto-fill.
 *
 * Refresh cadence:
 *   - Clock: 1s setInterval (display-only).
 *   - Crypto: react-query refetchInterval 15s (Bitget rate-limit friendly).
 */

const CRYPTO = [
  { sym: 'BTCUSDT', label: 'BTC' },
  { sym: 'ETHUSDT', label: 'ETH' },
  { sym: 'SOLUSDT', label: 'SOL' },
]

const INDICES = [
  { sym: 'SPX', label: 'SPX' },
  { sym: 'VIX', label: 'VIX' },
]

function pad2(n: number): string {
  return n < 10 ? '0' + n : String(n)
}

function fmtClock(d: Date, utc: boolean): string {
  const h = utc ? d.getUTCHours() : d.getHours()
  const m = utc ? d.getUTCMinutes() : d.getMinutes()
  const s = utc ? d.getUTCSeconds() : d.getSeconds()
  return `${pad2(h)}:${pad2(m)}:${pad2(s)}`
}

export default function MarketTicker() {
  useDecimals()
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])

  const { data: snapshots = [] } = useQuery({
    queryKey: ['market-snapshots-ticker'],
    queryFn: () => api.getMarketSnapshots(undefined, 20),
    refetchInterval: 15000,
  })

  // Take latest snapshot per symbol (api may return multiple rows newest-first).
  const snapMap: Record<string, any> = {}
  for (const s of snapshots as any[]) {
    if (!snapMap[s.symbol]) snapMap[s.symbol] = s
  }

  return (
    <div className="market-ticker" role="status" aria-label="Market ticker">
      {/* Clocks */}
      <span className="ticker-clock" title="Local time">
        <span className="ticker-clock-time">{fmtClock(now, false)}</span>
      </span>
      <span className="ticker-sep">·</span>
      <span className="ticker-clock" title="UTC time">
        <span className="ticker-clock-time">{fmtClock(now, true)}</span>
        <span className="ticker-clock-zone">UTC</span>
      </span>

      <span className="ticker-divider" aria-hidden="true" />

      {/* Crypto tokens — real data */}
      {CRYPTO.map(({ sym, label }) => {
        const snap = snapMap[sym]
        const price = snap?.price
        const pct = snap?.change_pct_24h
        const tone = pct == null ? 'muted' : pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat'
        return (
          <span key={sym} className="ticker-token" title={`${sym} · 24h change`}>
            <span className="ticker-label">{label}</span>
            <span className="ticker-price">{price != null ? fmtNum(price) : '—'}</span>
            <span className={`ticker-pct ticker-${tone}`}>
              {pct != null ? `${fmtSigned(pct)}%` : ''}
            </span>
          </span>
        )
      })}

      <span className="ticker-divider" aria-hidden="true" />

      {/* Indices — NOT WIRED. Placeholder slots until backend GET /api/market/indices ships. */}
      {INDICES.map(({ sym, label }) => (
        <span
          key={sym}
          className="ticker-token ticker-token-pending"
          title="Indices feed not wired — see WEBUI_BACKEND_HANDOFF.md §13"
        >
          <span className="ticker-label">{label}</span>
          <span className="ticker-price">—</span>
        </span>
      ))}
    </div>
  )
}
