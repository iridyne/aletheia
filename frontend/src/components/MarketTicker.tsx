import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { bootApi } from '../api/boot'
import { fmtNum, fmtSigned, fmtTimestamp, isoToEpochSeconds } from '../lib/format'
import { useCellFlash } from '../lib/useCellFlash'

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

function CryptoTickerToken({ sym, label, snap, nowSec }: {
  sym: string; label: string; snap: any; nowSec: number
}) {
  const price: number | null = snap?.price ?? null
  const flash = useCellFlash(price)
  const pct: number | null = snap?.change_pct_24h ?? null
  const ageSec = snap ? isoToEpochSeconds(snap.snapshot_at) : null
  const stale = ageSec != null && nowSec - ageSec > STALE_SECONDS
  const tone = pct == null ? 'muted' : pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat'
  const title = snap
    ? `${sym} · 24h change · updated ${fmtTimestamp(snap.snapshot_at)}${stale ? ' (stale feed)' : ''}`
    : `${sym} · no snapshot feed yet`
  return (
    <span className={`ticker-token${stale ? ' ticker-stale' : ''}`} title={title}>
      <span className="ticker-label">{label}</span>
      <span className={`ticker-price ${flash}`}>{price != null ? fmtNum(price) : '—'}</span>
      <span className={`ticker-pct ticker-${tone}`}>
        {price != null && pct != null ? `${pct > 0 ? '▲' : pct < 0 ? '▼' : ''}${fmtSigned(pct)}%` : ''}
      </span>
      {stale && <span className="ticker-stale-dot" aria-hidden="true">◦</span>}
    </span>
  )
}

export default function MarketTicker() {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])

  const { data: shell } = useQuery({
    queryKey: ['ui-shell-bootstrap'],
    queryFn: ({ signal }) => bootApi.getShellBootstrap({ signal }),
    staleTime: 5_000,
    refetchInterval: 10_000,
    meta: { background: true },
  })

  const nowSec = Math.floor(now.getTime() / 1000)

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
      {CRYPTO.map(({ sym, label }) => (
        <CryptoTickerToken
          key={sym}
          sym={sym}
          label={label}
          snap={shell?.ticker?.[sym] ?? null}
          nowSec={nowSec}
        />
      ))}
    </div>
  )
}
