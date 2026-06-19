/**
 * TradeInsightsPanel — comprehensive trade analytics visualization.
 *
 * Uses Recharts for general-purpose charts (area, bar, scatter, radar).
 * lightweight-charts is reserved for financial OHLCV charts (PriceChart.tsx).
 *
 * Sections (top to bottom):
 *   1. Equity Curve — area chart with semi-transparent fill
 *   2. Drawdown Curve — area chart (negative, red fill)
 *   3. Profit Factor card + Long vs Short breakdown (2-col grid)
 *   4. PnL Distribution histogram
 *   5. Day-of-week + Hour heatmap bars (2-col grid)
 *   6. Regime Performance radar + Hold-time vs PnL scatter (2-col grid)
 *   7. Streak summary tiles
 */
import { useQuery } from '@tanstack/react-query'
import {
  AreaChart, Area, BarChart, Bar, ScatterChart, Scatter,
  RadarChart, Radar, PolarGrid, PolarAngleAxis,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  ReferenceLine, Cell,
} from 'recharts'
import { api } from '../api/client'
import type { TradeInsights } from '../api/client'
import { Card } from '../components/ui'

// ── Palette (matches CSS vars) ────────────────────────────────────────────────
const C = {
  green:   '#2ee6a8',
  red:     '#ff5d6c',
  accent:  '#22d3ee',
  yellow:  '#f5b942',
  purple:  '#a78bfa',
  surface: '#141a21',
  border:  '#232c36',
  text:    '#e6edf3',
  muted:   '#7a8a99',
}

// ── Shared Recharts props ─────────────────────────────────────────────────────
const axisStyle  = { fill: C.muted, fontSize: 10 }
const gridProps  = { stroke: C.border, strokeDasharray: '3 3' }
const tooltipStyle = {
  contentStyle: { background: '#1d2530', border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 11 },
  labelStyle: { color: C.text },
  itemStyle: { color: C.accent },
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div className="insights-section-label">{children}</div>
}

// ── 1. Equity Curve ───────────────────────────────────────────────────────────
function EquityCurve({ data }: { data: TradeInsights['equity_curve'] }) {
  const isPositive = (data[data.length - 1]?.cumulative_pnl ?? 0) >= 0
  const color = isPositive ? C.green : C.red
  return (
    <Card>
      <SectionLabel>Equity Curve (Cumulative PnL, USDT)</SectionLabel>
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="time" tick={axisStyle} interval="preserveStartEnd" />
          <YAxis tick={axisStyle} width={52} tickFormatter={v => v.toFixed(2)} />
          <Tooltip {...tooltipStyle} formatter={(v: any) => [`${Number(v).toFixed(4)} USDT`, 'Cumulative PnL']} />
          <ReferenceLine y={0} stroke={C.muted} strokeDasharray="4 2" />
          <Area
            type="monotone"
            dataKey="cumulative_pnl"
            stroke={color}
            strokeWidth={2}
            fill={color}
            fillOpacity={0.12}
            dot={false}
            activeDot={{ r: 4, fill: color }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </Card>
  )
}

// ── 2. Drawdown Curve ─────────────────────────────────────────────────────────
function DrawdownCurve({ data }: { data: TradeInsights['drawdown_curve'] }) {
  return (
    <Card>
      <SectionLabel>Drawdown (vs Peak Equity)</SectionLabel>
      <ResponsiveContainer width="100%" height={160}>
        <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="time" tick={axisStyle} interval="preserveStartEnd" />
          <YAxis tick={axisStyle} width={52} tickFormatter={v => v.toFixed(2)} />
          <Tooltip {...tooltipStyle} formatter={(v: any) => [`${Number(v).toFixed(4)} USDT`, 'Drawdown']} />
          <ReferenceLine y={0} stroke={C.muted} />
          <Area
            type="monotone"
            dataKey="drawdown"
            stroke={C.red}
            strokeWidth={1.5}
            fill={C.red}
            fillOpacity={0.15}
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </Card>
  )
}

// ── 3a. Profit Factor card ────────────────────────────────────────────────────
function ProfitFactorCard({ data }: { data: TradeInsights['profit_factor'] }) {
  const pf = data.profit_factor
  const pfColor = pf == null ? C.muted : pf >= 1.5 ? C.green : pf >= 1.0 ? C.yellow : C.red
  const barData = [
    { label: 'Avg Win', value: data.avg_win, color: C.green },
    { label: 'Avg Loss', value: Math.abs(data.avg_loss), color: C.red },
  ]
  return (
    <Card>
      <SectionLabel>Profit Factor</SectionLabel>
      <div className="insights-pf-grid">
        <div className="insights-pf-big" style={{ color: pfColor }}>
          {pf != null ? pf.toFixed(2) : '—'}
        </div>
        <div className="insights-pf-stats">
          <div><span className="insights-label">Gross Profit</span><span className="mono pos">{data.gross_profit.toFixed(4)}</span></div>
          <div><span className="insights-label">Gross Loss</span><span className="mono neg">-{data.gross_loss.toFixed(4)}</span></div>
          <div><span className="insights-label">Largest Win</span><span className="mono pos">{data.largest_win.toFixed(4)}</span></div>
          <div><span className="insights-label">Largest Loss</span><span className="mono neg">{data.largest_loss.toFixed(4)}</span></div>
          <div><span className="insights-label">Win / Loss</span><span className="mono">{data.win_count} / {data.loss_count}</span></div>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={80}>
        <BarChart data={barData} layout="vertical" margin={{ top: 0, right: 8, left: 8, bottom: 0 }}>
          <XAxis type="number" tick={axisStyle} tickFormatter={v => v.toFixed(3)} />
          <YAxis type="category" dataKey="label" tick={axisStyle} width={62} />
          <Tooltip {...tooltipStyle} formatter={(v: any) => [Number(v).toFixed(4), '']} />
          <Bar dataKey="value" radius={[0, 3, 3, 0]}>
            {barData.map((d, i) => <Cell key={i} fill={d.color} fillOpacity={0.85} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </Card>
  )
}

// ── 3b. Long vs Short ─────────────────────────────────────────────────────────
function DirectionBreakdown({ data }: { data: TradeInsights['by_direction'] }) {
  const rows = Object.entries(data).map(([dir, s]) => ({
    direction: dir,
    trade_count: s.trade_count,
    win_rate: s.win_rate,
    avg_pnl_pct: s.avg_pnl_pct,
    avg_hold_hours: s.avg_hold_hours,
  }))
  return (
    <Card>
      <SectionLabel>Long vs Short</SectionLabel>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="direction" tick={axisStyle} />
          <YAxis yAxisId="left" tick={axisStyle} width={36} />
          <YAxis yAxisId="right" orientation="right" tick={axisStyle} width={40} tickFormatter={v => `${v}%`} />
          <Tooltip {...tooltipStyle} />
          <Bar yAxisId="left" dataKey="trade_count" name="Trades" fill={C.accent} fillOpacity={0.7} radius={[3, 3, 0, 0]}>
            {rows.map((r, i) => <Cell key={i} fill={r.direction === 'long' ? C.accent : C.yellow} fillOpacity={0.8} />)}
          </Bar>
          <Bar yAxisId="right" dataKey="win_rate" name="Win %" fill={C.green} fillOpacity={0.4} radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
      <div className="insights-dir-table">
        {rows.map(r => (
          <div key={r.direction} className="insights-dir-row">
            <span className="insights-label" style={{ color: r.direction === 'long' ? C.accent : C.yellow }}>
              {r.direction.toUpperCase()}
            </span>
            <span className="mono">{r.trade_count} trades</span>
            <span className={`mono ${r.win_rate >= 50 ? 'pos' : 'neg'}`}>{r.win_rate.toFixed(1)}% wr</span>
            <span className={`mono ${r.avg_pnl_pct >= 0 ? 'pos' : 'neg'}`}>{r.avg_pnl_pct >= 0 ? '+' : ''}{r.avg_pnl_pct.toFixed(2)}% exp</span>
            <span className="mono text-muted">{r.avg_hold_hours.toFixed(1)}h hold</span>
          </div>
        ))}
      </div>
    </Card>
  )
}

// ── 4. PnL Distribution ───────────────────────────────────────────────────────
function PnlDistribution({ data }: { data: TradeInsights['pnl_distribution'] }) {
  return (
    <Card>
      <SectionLabel>PnL % Distribution</SectionLabel>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 24 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="bin" tick={{ ...axisStyle, fontSize: 9 }} angle={-45} textAnchor="end" interval={1} />
          <YAxis tick={axisStyle} width={32} />
          <Tooltip {...tooltipStyle} />
          <Bar dataKey="count" name="Trades" radius={[2, 2, 0, 0]}>
            {data.map((d, i) => {
              const isNeg = d.bin.startsWith('-') || d.bin === '<-10%'
              return <Cell key={i} fill={isNeg ? C.red : C.green} fillOpacity={0.75} />
            })}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </Card>
  )
}

// ── 5a. Day of Week ───────────────────────────────────────────────────────────
function DayOfWeekChart({ data }: { data: TradeInsights['by_day_of_week'] }) {
  return (
    <Card>
      <SectionLabel>PnL by Day of Week</SectionLabel>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="day" tick={axisStyle} />
          <YAxis tick={axisStyle} width={40} tickFormatter={v => `${v}%`} />
          <Tooltip {...tooltipStyle} formatter={(v: any) => [`${Number(v).toFixed(2)}%`, 'Avg PnL']} />
          <ReferenceLine y={0} stroke={C.muted} />
          <Bar dataKey="avg_pnl_pct" name="Avg PnL %" radius={[3, 3, 0, 0]}>
            {data.map((d, i) => <Cell key={i} fill={d.avg_pnl_pct >= 0 ? C.green : C.red} fillOpacity={0.8} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </Card>
  )
}

// ── 5b. Hour of Day ───────────────────────────────────────────────────────────
function HourOfDayChart({ data }: { data: TradeInsights['by_hour'] }) {
  const active = data.filter(d => d.trade_count > 0)
  return (
    <Card>
      <SectionLabel>Avg PnL by Hour (UTC)</SectionLabel>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid {...gridProps} />
          <XAxis dataKey="hour" tick={axisStyle} tickFormatter={v => `${v}h`} interval={3} />
          <YAxis tick={axisStyle} width={40} tickFormatter={v => `${v}%`} />
          <Tooltip {...tooltipStyle} formatter={(v: any, _n: any, p: any) => [`${Number(v).toFixed(2)}% (${p?.payload?.trade_count ?? ''} trades)`, 'Avg PnL']} />
          <ReferenceLine y={0} stroke={C.muted} />
          <Bar dataKey="avg_pnl_pct" name="Avg PnL %" radius={[2, 2, 0, 0]}>
            {data.map((d, i) => <Cell key={i} fill={d.avg_pnl_pct >= 0 ? C.green : C.red} fillOpacity={d.trade_count > 0 ? 0.8 : 0.2} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </Card>
  )
}

// ── 6a. Regime Radar ─────────────────────────────────────────────────────────
function RegimeRadar({ data }: { data: TradeInsights['by_regime'] }) {
  const radarData = data.map(d => ({
    subject: d.regime.replace('_', ' '),
    win_rate: d.win_rate,
    avg_pnl: Math.max(-30, Math.min(30, d.avg_pnl_pct * 10)), // scale for radar
    trade_count: Math.min(100, d.trade_count * 3),
  }))
  return (
    <Card>
      <SectionLabel>Regime Performance</SectionLabel>
      <ResponsiveContainer width="100%" height={220}>
        <RadarChart data={radarData} margin={{ top: 16, right: 32, left: 32, bottom: 8 }}>
          <PolarGrid stroke={C.border} />
          <PolarAngleAxis dataKey="subject" tick={{ fill: C.muted, fontSize: 11 }} />
          <Radar name="Win Rate" dataKey="win_rate" stroke={C.green} fill={C.green} fillOpacity={0.2} strokeWidth={1.5} />
          <Radar name="Avg PnL (scaled)" dataKey="avg_pnl" stroke={C.accent} fill={C.accent} fillOpacity={0.15} strokeWidth={1.5} />
          <Tooltip {...tooltipStyle} />
        </RadarChart>
      </ResponsiveContainer>
      <div className="insights-regime-table">
        {data.map(d => (
          <div key={d.regime} className="insights-dir-row">
            <span className="insights-label">{d.regime.replace('_', ' ')}</span>
            <span className="mono">{d.trade_count}x</span>
            <span className={`mono ${d.win_rate >= 50 ? 'pos' : 'neg'}`}>{d.win_rate.toFixed(0)}% wr</span>
            <span className={`mono ${d.avg_pnl_pct >= 0 ? 'pos' : 'neg'}`}>{d.avg_pnl_pct >= 0 ? '+' : ''}{d.avg_pnl_pct.toFixed(2)}%</span>
          </div>
        ))}
      </div>
    </Card>
  )
}

// ── 6b. Hold vs PnL Scatter ───────────────────────────────────────────────────
function HoldScatter({ data }: { data: TradeInsights['hold_vs_pnl'] }) {
  const longs = data.filter(d => d.direction === 'long')
  const shorts = data.filter(d => d.direction === 'short')
  return (
    <Card>
      <SectionLabel>Hold Time vs PnL %</SectionLabel>
      <ResponsiveContainer width="100%" height={220}>
        <ScatterChart margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
          <CartesianGrid {...gridProps} />
          <XAxis type="number" dataKey="hold_hours" name="Hold (h)" tick={axisStyle} unit="h" />
          <YAxis type="number" dataKey="pnl_pct" name="PnL %" tick={axisStyle} unit="%" width={40} />
          <ReferenceLine y={0} stroke={C.muted} strokeDasharray="4 2" />
          <Tooltip
            {...tooltipStyle}
            cursor={{ strokeDasharray: '3 3' }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const d = payload[0].payload
              return (
                <div style={tooltipStyle.contentStyle}>
                  <div style={{ color: C.text }}>{d.symbol}</div>
                  <div style={{ color: C.muted }}>Hold: {d.hold_hours}h · PnL: {d.pnl_pct}%</div>
                </div>
              )
            }}
          />
          <Scatter name="Long" data={longs} fill={C.accent} fillOpacity={0.7} r={4} />
          <Scatter name="Short" data={shorts} fill={C.yellow} fillOpacity={0.7} r={4} />
        </ScatterChart>
      </ResponsiveContainer>
      <div className="insights-scatter-legend">
        <span style={{ color: C.accent }}>● Long</span>
        <span style={{ color: C.yellow }}>● Short</span>
      </div>
    </Card>
  )
}

// ── 7. Streaks ────────────────────────────────────────────────────────────────
function StreakSummary({ data }: { data: TradeInsights['streaks'] }) {
  const curColor = data.current_streak_type === 'win' ? C.green : data.current_streak_type === 'loss' ? C.red : C.muted
  return (
    <Card>
      <SectionLabel>Consecutive Streaks</SectionLabel>
      <div className="insights-streak-grid">
        <div className="analytics-metric-tile">
          <div className="analytics-metric-label">Best Win Streak</div>
          <div className="analytics-metric-value mono pos">{data.max_consecutive_wins}</div>
        </div>
        <div className="analytics-metric-tile">
          <div className="analytics-metric-label">Worst Loss Streak</div>
          <div className="analytics-metric-value mono neg">{data.max_consecutive_losses}</div>
        </div>
        <div className="analytics-metric-tile">
          <div className="analytics-metric-label">Current Streak</div>
          <div className="analytics-metric-value mono" style={{ color: curColor }}>
            {data.current_streak} {data.current_streak_type !== 'none' ? data.current_streak_type : '—'}
          </div>
        </div>
        <div className="analytics-metric-tile">
          <div className="analytics-metric-label">W / L Total</div>
          <div className="analytics-metric-value mono">{data.total_wins} / {data.total_losses}</div>
        </div>
      </div>
    </Card>
  )
}

// ── Main Panel ────────────────────────────────────────────────────────────────
export default function TradeInsightsPanel() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['trade-insights'],
    queryFn: api.getTradeInsights,
    staleTime: 60_000,
  })

  if (isLoading) return <Card><p className="text-muted text-sm">Loading insights…</p></Card>
  if (error || !data) return <Card><p className="text-muted text-sm">Insights unavailable.</p></Card>

  const hasData = data.equity_curve.length > 0

  if (!hasData) {
    return <Card><p className="text-muted text-sm">No closed trades yet — insights will appear once positions are closed.</p></Card>
  }

  return (
    <div className="insights-panel">
      <EquityCurve data={data.equity_curve} />
      <DrawdownCurve data={data.drawdown_curve} />

      <div className="insights-2col">
        <ProfitFactorCard data={data.profit_factor} />
        <DirectionBreakdown data={data.by_direction} />
      </div>

      <PnlDistribution data={data.pnl_distribution} />

      <div className="insights-2col">
        <DayOfWeekChart data={data.by_day_of_week} />
        <HourOfDayChart data={data.by_hour} />
      </div>

      <div className="insights-2col">
        <RegimeRadar data={data.by_regime} />
        <HoldScatter data={data.hold_vs_pnl} />
      </div>

      <StreakSummary data={data.streaks} />
    </div>
  )
}
