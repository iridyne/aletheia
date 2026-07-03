import { useEffect, useRef, useState } from 'react'
import { useQueries } from '@tanstack/react-query'
import { api } from '../api/client'
import { fmtNum, fmtSigned, fmtTimestamp, isoToEpochSeconds, useDecimals } from '../lib/format'

/**
 * MarketTicker — Bloomberg-flavored top-bar ticker.
 *
 * Layout (left→right):
 *   local clock · UTC clock   |   crypto tokens (BTC/ETH/SOL — real)   |   indices (SPX/VIX — pending)
 *
 * Honesty:
 *   - Crypto = real Bitget snapshots from /api/market/snapshots.
 *   - Indices = NOT WIRED. We render '—' + a "feed not wired" tooltip rather than fabricate.
 *
 * Why per-symbol queries (fixes the recurring "price shows a dash" bug):
 *   The old code fetched the latest 20 snapshots ACROSS ALL symbols and picked
 *   BTC/ETH/SOL out of that window. Once the scanner tracks >20 symbols, the
 *   majors routinely fall out of the newest-20 rows → the ticker got nothing →
 *   it rendered '—'. We now query each of the three symbols independently
 *   (limit=1), so a major is only ever blank if it has genuinely never been
 *   snapshotted. A present-but-old snapshot is shown dimmed + "(stale)" so a
 *   stalled feed is visibly distinct from a missing one.
 *
 * Refresh cadence:
 *   - Clock: 1s setInterval (display-only, also drives the staleness age).
 *   - Crypto: react-query refetchInterval 15s (Bitget rate-limit friendly).
 */

const CRYPTO = [
  { sym: 'BTC/USDT', label: 'BTC' },
  { sym: 'ETH/USDT', label: 'ETH' },
  { sym: 'SOL/USDT', label: 'SOL' },
]

// A snapshot older than this is treated as a stalled feed: keep showing the
// last price but dim it and surface the age, rather than implying it's live.
const STALE_SECONDS = 300

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

  // One query per symbol so majors never fall out of a shared "latest N" window.
  const results = useQueries({
    queries: CRYPTO.map(({ sym }) => ({
      queryKey: ['ticker-snap', sym],
      queryFn: () => api.getMarketSnapshots(sym, 1),
      refetchInterval: 15000,
    })),
  })

  const snaps = CRYPTO.map((_, i) => {
    const rows = (results[i]?.data as any[]) || []
    return rows[0] || null
  })

  const nowSec = Math.floor(now.getTime() / 1000)

  // Tick flash: compare current vs previous prices.
  const prevPrices = useRef<Record<string, number>>({})
  const [flashes, setFlashes] = useState<Record<string, 'up' | 'down'>>({})
  const priceSig = snaps.map((s) => s?.price ?? '').join(',')

  useEffect(() => {
    const next: Record<string, 'up' | 'down'> = {}
    CRYPTO.forEach(({ sym }, i) => {
      const price = snaps[i]?.price
      const prev = prevPrices.current[sym]
      if (price != null && prev != null && price !== prev) {
        next[sym] = price > prev ? 'up' : 'down'
      }
      if (price != null) prevPrices.current[sym] = price
    })
    if (Object.keys(next).length > 0) {
      setFlashes(next)
      const t = setTimeout(() => setFlashes({}), 650)
      return () => clearTimeout(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [priceSig])

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
      {CRYPTO.map(({ sym, label }, i) => {
        const snap = snaps[i]
        const price = snap?.price
        const pct = snap?.change_pct_24h
        const ageSec = snap ? isoToEpochSeconds(snap.snapshot_at) : null
        const stale = ageSec != null && nowSec - ageSec > STALE_SECONDS
        const tone = pct == null ? 'muted' : pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat'
        const title = snap
          ? `${sym} · 24h change · updated ${fmtTimestamp(snap.snapshot_at)}${stale ? ' (stale feed)' : ''}`
          : `${sym} · no snapshot feed yet`
        return (
          <span key={sym} className={`ticker-token${stale ? ' ticker-stale' : ''}`} title={title}>
            <span className="ticker-label">{label}</span>
            <span className={`ticker-price${flashes[sym] ? ` tick-flash-${flashes[sym]}` : ''}`}>{price != null ? fmtNum(price) : '—'}</span>
            <span className={`ticker-pct ticker-${tone}`}>
              {price != null && pct != null ? `${pct > 0 ? '▲' : pct < 0 ? '▼' : ''}${fmtSigned(pct)}%` : ''}
            </span>
            {stale && <span className="ticker-stale-dot" aria-hidden="true">◦</span>}
          </span>
        )
      })}

    </div>
  )
}
