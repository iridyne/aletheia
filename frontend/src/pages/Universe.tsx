import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { UniverseMember, UniverseCurrentResponse, UniverseMemberDetail, CalibrationResponse } from '../types'
import { DataTable } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import PageHeader from '../components/PageHeader'
import MetricTile from '../components/MetricTile'
import { Badge, Button, Card } from '../components/ui'
import { humanize, fmtTimestamp } from '../lib/format'

const col = createColumnHelper<UniverseMember>()

function parseJson(raw: string | null | undefined): any[] {
  if (!raw) return []
  try { return JSON.parse(raw) } catch { return [] }
}

function familyClass(family: string | null): string {
  return `universe-family universe-family-${family || 'unknown'}`
}

function formatVolume(v: number | null): string {
  if (v == null) return '—'
  if (v >= 1_000_000_000) return `${(v / 1_000_000_000).toFixed(1)}B`
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`
  return v.toFixed(0)
}

function timeAgo(isoStr: string | null | undefined): string {
  if (!isoStr) return '—'
  const ts = new Date(isoStr.endsWith('Z') || isoStr.includes('+') ? isoStr : isoStr + 'Z').getTime()
  const diff = Math.floor((Date.now() - ts) / 1000)
  if (diff < 60) return '<1m ago'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

const columns = [
  col.accessor('rank', { header: '#', cell: info => <span className="mono">{info.getValue()}</span> }),
  col.accessor('symbol', {
    header: 'Symbol',
    cell: info => (
      <span className="flex items-center gap-1">
        <span className="mono">{info.getValue()}</span>
        {info.row.original.stale && <Badge tone="warning">stale</Badge>}
      </span>
    ),
  }),
  col.accessor('scanner_family', {
    header: 'Family',
    cell: info => (
      <span className={familyClass(info.getValue())}>
        {info.getValue() ? humanize(info.getValue()!) : '—'}
      </span>
    ),
  }),
  col.accessor('total_score', {
    header: 'Score',
    cell: info => <span className="mono">{info.getValue()?.toFixed(1) ?? '—'}</span>,
  }),
  col.accessor('quote_volume_24h', {
    header: 'Volume 24h',
    cell: info => <span className="mono">{formatVolume(info.getValue())}</span>,
  }),
  col.accessor('spread_bps', {
    header: 'Spread',
    cell: info => <span className="mono">{info.getValue() != null ? `${info.getValue()!.toFixed(1)} bps` : '—'}</span>,
  }),
  col.accessor('tradability_score', {
    header: 'Tradability',
    cell: info => <span className="mono">{info.getValue()?.toFixed(0) ?? '—'}</span>,
  }),
  col.accessor('ttl_sec', {
    header: 'TTL',
    cell: info => {
      const sec = info.getValue()
      if (sec == null) return <span className="mono">—</span>
      return <span className="mono">{sec >= 3600 ? `${(sec / 3600).toFixed(0)}h` : `${(sec / 60).toFixed(0)}m`}</span>
    },
  }),
  col.accessor('risk_flags', {
    header: 'Flags',
    cell: info => {
      const flags = parseJson(info.getValue())
      if (!flags.length) return <span className="text-muted">—</span>
      return <span>{flags.map(f => humanize(String(f))).join(', ')}</span>
    },
  }),
  col.accessor('execution_eligible', {
    header: 'Eligible',
    cell: info => (
      <Badge tone={info.getValue() ? 'green' : 'muted'}>
        {info.getValue() ? 'Yes' : 'No'}
      </Badge>
    ),
  }),
]

export default function Universe() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [selected, setSelected] = useState<UniverseMember | null>(null)

  const { data } = useQuery<UniverseCurrentResponse>({
    queryKey: ['universe-current'],
    queryFn: () => api.get('/universe/current'),
    refetchInterval: 30000,
  })

  const { data: calibration } = useQuery<CalibrationResponse>({
    queryKey: ['universe-calibration'],
    queryFn: () => api.getUniverseCalibration(),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 10 * 60 * 1000,
  })

  const refreshMutation = useMutation({
    mutationFn: () => api.post('/universe/refresh'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['universe-current'] }),
  })

  const members = data?.members ?? []
  const snapshot = data?.snapshot
  const enabled = data?.enabled ?? false
  const isFallback = data?.fallback ?? true
  const isStale = data?.stale ?? false
  const staleCount = data?.stale_count ?? 0

  const summary = snapshot?.summary ? (() => {
    try { return JSON.parse(snapshot.summary) } catch { return {} }
  })() : {}

  const statusTone = !enabled ? 'muted' : isFallback ? 'negative' : isStale ? 'warning' : 'positive'
  const statusText = !enabled ? 'Off' : isFallback ? 'Fallback' : isStale ? 'Stale' : 'Active'

  return (
    <div>
      <PageHeader
        title="Universe Board"
        role={
          !enabled
            ? 'Disabled — using static fallback (BTC/ETH/SOL)'
            : isFallback
              ? 'No active snapshot — showing fallback pool (BTC/ETH/SOL)'
              : isStale
                ? `Stale data — ${staleCount}/${members.length} members expired, awaiting next refresh`
                : `Snapshot ${snapshot?.id?.slice(0, 8)} — ${members.length} candidates`
        }
      />

      {!enabled && (
        <Card className="universe-disabled-card">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <div>
              <h3>Universe generator is disabled</h3>
              <p className="hint">
                The board is showing the static fallback pool. Enable it in Settings → Risk, Symbols & Universe → Universe Board.
              </p>
            </div>
            <Button
              variant="secondary"
              onClick={() => navigate('/settings?tab=risk&section=universe')}
            >
              Open Universe setting
            </Button>
          </div>
        </Card>
      )}

      {/* Summary row */}
      <div className="account-snapshot">
        <MetricTile label="Candidates" value={members.length} />
        <MetricTile label="Scanned" value={summary.scanned ?? '—'} />
        <MetricTile label="Filtered" value={summary.filtered ?? '—'} />
        <MetricTile label="Status" value={statusText} tone={statusTone} />
        <MetricTile label="Refreshed" value={timeAgo(snapshot?.created_at)} />
      </div>

      {/* Family breakdown */}
      {members.length > 0 && !isFallback && (
        <div className="account-snapshot">
          {['momentum', 'mean_reversion', 'volatility', 'derivatives'].map(family => {
            const count = members.filter(m => m.scanner_family === family).length
            const top = members.find(m => m.scanner_family === family)
            return (
              <Card key={family} className="metric-tile">
                <div className={`metric-label ${familyClass(family)}`}>
                  {humanize(family)}
                </div>
                <div className="metric-value">{count} <span className="text-sm text-muted">assets</span></div>
                {top && (
                  <div className="metric-caveat">
                    Top: <span className="mono">{top.symbol}</span> ({top.total_score.toFixed(0)})
                  </div>
                )}
              </Card>
            )
          })}
        </div>
      )}

      {/* Scanner Calibration */}
      {(() => {
        const horizons = ['15m', '1h', '4h', '24h']
        const calMap: Record<string, Record<string, { n: number; wr: number }>> = {}
        for (const row of calibration?.by_family_horizon ?? []) {
          const fam = row.scanner_family ?? 'unknown'
          if (!calMap[fam]) calMap[fam] = {}
          if (row.n >= 10) {
            calMap[fam][row.horizon] = { n: row.n, wr: row.win_count / row.n * 100 }
          }
        }
        const calFamilies = Object.keys(calMap)
        if (!calFamilies.length) return null
        return (
          <Card className="mb-3">
            <div className="metric-label mb-2">Scanner Calibration <span className="text-muted">(win rate, n≥10)</span></div>
            <div className="cal-scroll">
              <table className="cal-table">
                <thead>
                  <tr>
                    <th className="cal-th cal-th-left">Family</th>
                    {horizons.map(h => (
                      <th key={h} className="cal-th cal-th-center">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {calFamilies.sort().map(fam => (
                    <tr key={fam}>
                      <td className="cal-td">
                        <span className={familyClass(fam)}>{humanize(fam)}</span>
                      </td>
                      {horizons.map(h => {
                        const cell = calMap[fam]?.[h]
                        if (!cell) return <td key={h} className="cal-td cal-td-center cal-td-muted">—</td>
                        const tone = cell.wr >= 52 ? 'pnl-positive' : cell.wr <= 48 ? 'pnl-negative' : ''
                        return (
                          <td key={h} className="cal-td cal-td-center">
                            <span className={`mono ${tone}`}>{cell.wr.toFixed(0)}%</span>
                            <span className="cal-n">n={cell.n}</span>
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )
      })()}

      {/* Actions */}
      {enabled && (
        <div className="mb-3">
          <div className="flex gap-2">
            <Button
              onClick={() => refreshMutation.mutate()}
              disabled={refreshMutation.isPending}
            >
              {refreshMutation.isPending ? 'Refreshing…' : 'Refresh Now'}
            </Button>
            <Button
              variant="secondary"
              onClick={() => navigate('/settings?tab=risk&section=universe')}
            >
              Universe Settings
            </Button>
          </div>
          {refreshMutation.isError && (
            <div className="settings-save-msg err">
              {(refreshMutation.error as Error).message}
            </div>
          )}
        </div>
      )}

      {/* Main table */}
      <DataTable
        columns={columns}
        data={members}
        onRowClick={setSelected}
        emptyMessage={enabled ? 'No universe data yet — trigger a refresh' : 'Universe generator disabled'}
      />

      {/* Detail drawer */}
      {selected && (
        <MemberDrawer member={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}

function MemberDrawer({ member, onClose }: { member: UniverseMember; onClose: () => void }) {
  const { data } = useQuery<UniverseMemberDetail>({
    queryKey: ['universe-member', member.id],
    queryFn: () => api.getUniverseMember(member.id),
  })

  const features = data?.features
  const opportunities = data?.opportunities ?? []

  return (
    <DetailDrawer open={true} title={`${member.symbol} — Rank #${member.rank}`} onClose={onClose}>
      <DrawerSection label="Scores">
        <DrawerField label="Total Score" value={member.total_score?.toFixed(1)} mono />
        <DrawerField label="Opportunity" value={member.opportunity_score?.toFixed(1) ?? '—'} mono />
        <DrawerField label="Tradability" value={member.tradability_score?.toFixed(0) ?? '—'} mono />
        <DrawerField label="Liquidity" value={member.liquidity_score?.toFixed(0) ?? '—'} mono />
        <DrawerField label="Est. Cost" value={member.estimated_cost?.toFixed(1) ?? '—'} mono />
        <DrawerField label="Crowding Risk" value={member.crowding_risk?.toFixed(1) ?? '—'} mono />
        <DrawerField label="Late Entry Penalty" value={member.late_entry_penalty?.toFixed(1) ?? '—'} mono />
      </DrawerSection>

      <DrawerSection label="Market Data">
        <DrawerField label="Volume 24h" value={formatVolume(member.quote_volume_24h)} mono />
        <DrawerField label="Spread" value={member.spread_bps != null ? `${member.spread_bps.toFixed(1)} bps` : '—'} mono />
      </DrawerSection>

      <DrawerSection label="Classification">
        <DrawerField label="Family" value={member.scanner_family ? humanize(member.scanner_family) : '—'} />
        <DrawerField label="TTL" value={`${member.ttl_sec}s`} mono />
        <DrawerField label="Expires" value={fmtTimestamp(member.expires_at)} mono />
        <DrawerField label="Eligible" value={member.execution_eligible ? 'Yes' : 'No'} />
      </DrawerSection>

      {features && (
        <DrawerSection label="Feature Snapshot">
          <DrawerField label="RSI (14)" value={features.rsi_14?.toFixed(1) ?? '—'} mono />
          <DrawerField label="ATR %" value={features.atr_pct?.toFixed(2) ?? '—'} mono />
          <DrawerField label="Return 1h" value={features.return_1h != null ? `${features.return_1h.toFixed(2)}%` : '—'} mono />
          <DrawerField label="Return 4h" value={features.return_4h != null ? `${features.return_4h.toFixed(2)}%` : '—'} mono />
          <DrawerField label="Return 24h" value={features.return_24h != null ? `${features.return_24h.toFixed(2)}%` : '—'} mono />
          <DrawerField label="Funding Rate" value={features.funding_rate != null ? `${(features.funding_rate * 100).toFixed(4)}%` : '—'} mono />
          <DrawerField label="Open Interest" value={features.open_interest != null ? formatVolume(features.open_interest) : '—'} mono />
          <DrawerField label="OI Delta 1h" value={features.oi_delta_1h != null ? formatVolume(features.oi_delta_1h) : '—'} mono />
          <DrawerField label="Data Age" value={features.data_freshness_sec != null ? `${features.data_freshness_sec}s` : '—'} mono />
        </DrawerSection>
      )}

      <DrawerSection label="Reasons">
        {parseJson(member.inclusion_reason).map((r: string, i: number) => (
          <div key={i} className="text-sm">• {r}</div>
        ))}
        {parseJson(member.inclusion_reason).length === 0 && (
          <div className="text-sm text-muted">No reasons recorded</div>
        )}
      </DrawerSection>

      <DrawerSection label="Risk Flags">
        {parseJson(member.risk_flags).map((f: string, i: number) => (
          <div key={i} className="text-sm universe-risk-flag">⚠ {humanize(String(f))}</div>
        ))}
        {parseJson(member.risk_flags).length === 0 && (
          <div className="text-sm text-muted">No risk flags</div>
        )}
      </DrawerSection>

      {opportunities.length > 0 && (
        <DrawerSection label="Linked Opportunities">
          {opportunities.map((opp) => (
            <div key={opp.id} className="flex justify-between text-sm">
              <span><span className="mono">{opp.symbol}</span> · {opp.direction_bias ? humanize(opp.direction_bias) : '—'}</span>
              <span className="text-muted">{humanize(opp.status)} · <span className="mono">{fmtTimestamp(opp.created_at)}</span></span>
            </div>
          ))}
        </DrawerSection>
      )}
    </DetailDrawer>
  )
}
