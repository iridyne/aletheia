import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { UniverseMember, UniverseCurrentResponse, UniverseMemberDetail } from '../types'
import { DataTable } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import PageHeader from '../components/PageHeader'
import MetricTile from '../components/MetricTile'
import { humanize, fmtTimestamp } from '../lib/format'

const col = createColumnHelper<UniverseMember>()

function parseJson(raw: string | null | undefined): any[] {
  if (!raw) return []
  try { return JSON.parse(raw) } catch { return [] }
}

/** Scanner-family accent — stays within the token palette (no off-palette purple/orange). */
function familyColor(family: string | null): string {
  switch (family) {
    case 'momentum': return 'var(--blue)'
    case 'mean_reversion': return 'var(--accent)'
    case 'volatility': return 'var(--yellow)'
    case 'derivatives': return 'var(--green)'
    default: return 'var(--muted)'
  }
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
  col.accessor('symbol', { header: 'Symbol', cell: info => <span className="mono">{info.getValue()}</span> }),
  col.accessor('scanner_family', {
    header: 'Family',
    cell: info => (
      <span style={{ color: familyColor(info.getValue()) }}>
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
      <span className={`badge ${info.getValue() ? 'badge-green' : 'badge-muted'}`}>
        {info.getValue() ? 'Yes' : 'No'}
      </span>
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

  const refreshMutation = useMutation({
    mutationFn: () => api.post('/universe/refresh'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['universe-current'] }),
  })

  const members = data?.members ?? []
  const snapshot = data?.snapshot
  const enabled = data?.enabled ?? false
  const isFallback = data?.fallback ?? true

  const summary = snapshot?.summary ? (() => {
    try { return JSON.parse(snapshot.summary) } catch { return {} }
  })() : {}

  const statusTone = !enabled ? 'muted' : isFallback ? 'negative' : 'positive'
  const statusText = !enabled ? 'Off' : isFallback ? 'Fallback' : 'Active'

  return (
    <div>
      <PageHeader
        title="Universe Board"
        role={
          !enabled
            ? 'Disabled — using static fallback (BTC/ETH/SOL)'
            : isFallback
              ? 'No active snapshot — showing fallback pool'
              : `Snapshot ${snapshot?.id?.slice(0, 8)} — ${members.length} candidates`
        }
      />

      {!enabled && (
        <div className="card" style={{ borderColor: 'var(--border-2)', marginBottom: '1rem' }}>
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <div>
              <h3 style={{ marginBottom: '0.25rem' }}>Universe generator is disabled</h3>
              <p className="hint" style={{ margin: 0 }}>
                The board is showing the static fallback pool. Enable it in Settings → Risk, Symbols & Universe → Universe Board.
              </p>
            </div>
            <button
              className="secondary"
              onClick={() => navigate('/settings?tab=risk&section=universe')}
            >
              Open Universe setting
            </button>
          </div>
        </div>
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
              <div key={family} className="card metric-tile">
                <div className="metric-label" style={{ color: familyColor(family) }}>
                  {humanize(family)}
                </div>
                <div className="metric-value">{count} <span className="text-sm text-muted">assets</span></div>
                {top && (
                  <div className="metric-caveat">
                    Top: <span className="mono">{top.symbol}</span> ({top.total_score.toFixed(0)})
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Actions */}
      {enabled && (
        <div className="flex gap-2 items-center mb-3">
          <button
            onClick={() => refreshMutation.mutate()}
            disabled={refreshMutation.isPending}
          >
            {refreshMutation.isPending ? 'Refreshing…' : 'Refresh Now'}
          </button>
          <button
            className="secondary"
            onClick={() => navigate('/settings?tab=risk&section=universe')}
          >
            Universe Settings
          </button>
          {refreshMutation.isError && (
            <span className="text-sm pnl-negative">
              {(refreshMutation.error as Error).message}
            </span>
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
          <div key={i} className="text-sm" style={{ color: 'var(--yellow)' }}>⚠ {humanize(String(f))}</div>
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
