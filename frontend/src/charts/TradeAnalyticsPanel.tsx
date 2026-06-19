/**
 * Trade Analytics Panel — visualizes closed-position statistics.
 *
 * Four sections:
 *   1. Totals row — trade count, win rate, avg/median hold time, total PnL
 *   2. By Strategy Source — RuleSet vs Opportunity (CSS bar chart)
 *   3. By Scanner Family — per-family breakdown (CSS bar chart)
 *   4. Hold Time Distribution — 6 duration buckets (CSS bar chart)
 *
 * Zero new dependencies — uses CSS bars instead of a chart library.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { TradeStatsSource, TradeStatsFamily, HoldBucket } from '../api/client'
import { Card } from '../components/ui'

function fmtHours(h: number): string {
  if (h < 1) return `${Math.round(h * 60)}m`
  if (h < 24) return `${h.toFixed(1)}h`
  return `${(h / 24).toFixed(1)}d`
}

function fmtPnl(v: number): string {
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}`
}

// ── MetricTile ────────────────────────────────────────────────────────────────

function MetricTile({ label, value, sub, tone }: {
  label: string
  value: string
  sub?: string
  tone?: 'pos' | 'neg' | 'neutral'
}) {
  const cls = tone === 'pos' ? 'pnl-positive' : tone === 'neg' ? 'pnl-negative' : ''
  return (
    <div className="analytics-metric-tile">
      <div className="analytics-metric-label">{label}</div>
      <div className={`analytics-metric-value mono ${cls}`}>{value}</div>
      {sub && <div className="analytics-metric-sub">{sub}</div>}
    </div>
  )
}

// ── BarRow ────────────────────────────────────────────────────────────────────

function BarRow({ label, barPct, color, stats }: {
  label: string
  barPct: number
  color: string
  stats: { label: string; value: string; tone?: 'pos' | 'neg' }[]
}) {
  return (
    <div className="analytics-bar-row">
      <div className="analytics-bar-label" title={label}>{label}</div>
      <div className="analytics-bar-wrap">
        <div className="analytics-bar" style={{ width: `${Math.max(barPct, 2)}%`, background: color }} />
      </div>
      <div className="analytics-bar-stats">
        {stats.map(s => (
          <span key={s.label} className={s.tone === 'pos' ? 'pos' : s.tone === 'neg' ? 'neg' : ''}>
            {s.value}
          </span>
        ))}
      </div>
    </div>
  )
}

// ── SectionLabel ──────────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div className="analytics-section-label">{children}</div>
}

// ── SourceSection ─────────────────────────────────────────────────────────────

function SourceSection({ rows }: { rows: TradeStatsSource[] }) {
  const maxCount = Math.max(...rows.map(r => r.trade_count), 1)
  const colors: Record<string, string> = {
    ruleset: 'var(--accent)',
    opportunity: 'var(--green)',
  }
  const labels: Record<string, string> = {
    ruleset: 'RuleSet',
    opportunity: 'Opportunity',
  }
  return (
    <>
      <SectionLabel>By Strategy Source</SectionLabel>
      {rows.map(r => (
        <BarRow
          key={r.source_type}
          label={labels[r.source_type] ?? r.source_type}
          barPct={(r.trade_count / maxCount) * 100}
          color={colors[r.source_type] ?? 'var(--muted)'}
          stats={[
            { label: 'n', value: `${r.trade_count}` },
            { label: 'wr', value: `${r.win_rate.toFixed(0)}%`, tone: r.win_rate >= 50 ? 'pos' : 'neg' },
            { label: 'hold', value: fmtHours(r.avg_hold_hours) },
            { label: 'exp', value: `${r.expectancy_pct >= 0 ? '+' : ''}${r.expectancy_pct.toFixed(2)}%`, tone: r.expectancy_pct >= 0 ? 'pos' : 'neg' },
          ]}
        />
      ))}
    </>
  )
}

// ── FamilySection ─────────────────────────────────────────────────────────────

const FAMILY_COLORS: Record<string, string> = {
  momentum:        '#4aa3ff',
  mean_reversion:  '#f5b942',
  volatility:      '#a78bfa',
  derivatives:     '#fb923c',
  core:            'var(--accent)',
  unknown:         'var(--faint)',
}

function FamilySection({ rows }: { rows: TradeStatsFamily[] }) {
  const maxCount = Math.max(...rows.map(r => r.trade_count), 1)
  return (
    <>
      <SectionLabel>By Scanner Family</SectionLabel>
      {rows.map(r => (
        <BarRow
          key={r.family}
          label={r.family}
          barPct={(r.trade_count / maxCount) * 100}
          color={FAMILY_COLORS[r.family] ?? 'var(--muted)'}
          stats={[
            { label: 'n', value: `${r.trade_count}` },
            { label: 'wr', value: `${r.win_rate.toFixed(0)}%`, tone: r.win_rate >= 50 ? 'pos' : 'neg' },
            { label: 'hold', value: fmtHours(r.avg_hold_hours) },
            { label: 'exp', value: `${r.expectancy_pct >= 0 ? '+' : ''}${r.expectancy_pct.toFixed(2)}%`, tone: r.expectancy_pct >= 0 ? 'pos' : 'neg' },
          ]}
        />
      ))}
    </>
  )
}

// ── HoldDistSection ───────────────────────────────────────────────────────────

// Color gradient: short hold = cyan, long hold = muted
const HOLD_COLORS = ['var(--accent)', '#0e9abf', '#0c7a96', 'var(--blue)', 'var(--yellow)', 'var(--muted)']

function HoldDistSection({ rows }: { rows: HoldBucket[] }) {
  const maxPct = Math.max(...rows.map(r => r.pct), 1)
  return (
    <>
      <SectionLabel>Hold Time Distribution</SectionLabel>
      {rows.map((r, i) => (
        <BarRow
          key={r.bucket}
          label={r.bucket}
          barPct={(r.pct / maxPct) * 100}
          color={HOLD_COLORS[i] ?? 'var(--muted)'}
          stats={[
            { label: 'n', value: `${r.count}` },
            { label: 'pct', value: `${r.pct.toFixed(0)}%` },
          ]}
        />
      ))}
    </>
  )
}

// ── Main Panel ────────────────────────────────────────────────────────────────

export default function TradeAnalyticsPanel() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['trade-stats'],
    queryFn: api.getTradeStats,
    staleTime: 60_000,
  })

  if (isLoading) {
    return <Card><p className="text-muted text-sm">Loading analytics…</p></Card>
  }

  if (error || !data) {
    return <Card><p className="text-muted text-sm">Analytics unavailable.</p></Card>
  }

  const t = data.totals
  const pnlTone = t.total_pnl >= 0 ? 'pos' : 'neg'

  return (
    <div>
      {/* Totals row */}
      <div className="analytics-grid">
        <MetricTile label="Closed Trades" value={`${t.trade_count}`} />
        <MetricTile
          label="Win Rate"
          value={`${t.win_rate.toFixed(1)}%`}
          tone={t.win_rate >= 50 ? 'pos' : 'neg'}
        />
        <MetricTile
          label="Total PnL"
          value={`${fmtPnl(t.total_pnl)} USDT`}
          tone={t.total_pnl >= 0 ? 'pos' : 'neg'}
        />
        <MetricTile
          label="Avg Hold Time"
          value={fmtHours(t.avg_hold_hours)}
          sub={`median ${fmtHours(t.median_hold_hours)}`}
        />
      </div>

      {/* Source breakdown */}
      <Card>
        <SourceSection rows={data.by_source} />
      </Card>

      {/* Family breakdown */}
      <Card>
        <FamilySection rows={data.by_family} />
      </Card>

      {/* Hold distribution */}
      <Card>
        <HoldDistSection rows={data.hold_distribution} />
      </Card>
    </div>
  )
}
