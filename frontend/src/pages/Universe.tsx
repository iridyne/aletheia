import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { UniverseMember, UniverseCurrentResponse, UniverseMemberDetail } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import PageHeader from '../components/PageHeader'

const col = createColumnHelper<UniverseMember>()

function parseJson(raw: string | null | undefined): any[] {
  if (!raw) return []
  try { return JSON.parse(raw) } catch { return [] }
}

function familyColor(family: string | null): string {
  switch (family) {
    case 'momentum': return 'text-blue-400'
    case 'mean_reversion': return 'text-purple-400'
    case 'volatility': return 'text-orange-400'
    case 'derivatives': return 'text-green-400'
    default: return 'text-zinc-400'
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
  col.accessor('rank', { header: '#', cell: info => info.getValue() }),
  col.accessor('symbol', { header: 'Symbol' }),
  col.accessor('scanner_family', {
    header: 'Family',
    cell: info => (
      <span className={familyColor(info.getValue())}>
        {info.getValue()?.replace('_', ' ') || '—'}
      </span>
    ),
  }),
  col.accessor('total_score', {
    header: 'Score',
    cell: info => info.getValue()?.toFixed(1) ?? '—',
  }),
  col.accessor('quote_volume_24h', {
    header: 'Volume 24h',
    cell: info => formatVolume(info.getValue()),
  }),
  col.accessor('spread_bps', {
    header: 'Spread',
    cell: info => info.getValue() != null ? `${info.getValue()!.toFixed(1)} bps` : '—',
  }),
  col.accessor('tradability_score', {
    header: 'Tradability',
    cell: info => info.getValue()?.toFixed(0) ?? '—',
  }),
  col.accessor('ttl_sec', {
    header: 'TTL',
    cell: info => {
      const sec = info.getValue()
      if (sec >= 3600) return `${(sec / 3600).toFixed(0)}h`
      return `${(sec / 60).toFixed(0)}m`
    },
  }),
  col.accessor('risk_flags', {
    header: 'Flags',
    cell: info => {
      const flags = parseJson(info.getValue())
      if (!flags.length) return '—'
      return flags.join(', ')
    },
  }),
  col.accessor('execution_eligible', {
    header: 'Eligible',
    cell: info => info.getValue() ? 'Yes' : 'No',
  }),
]

export default function Universe() {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<UniverseMember | null>(null)

  const { data, isLoading } = useQuery<UniverseCurrentResponse>({
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

  return (
    <div className="space-y-6">
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

      {/* Summary row */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <div className="bg-zinc-800 rounded-lg p-4">
          <div className="text-sm text-zinc-400">Candidates</div>
          <div className="text-2xl font-mono">{members.length}</div>
        </div>
        <div className="bg-zinc-800 rounded-lg p-4">
          <div className="text-sm text-zinc-400">Scanned</div>
          <div className="text-2xl font-mono">{summary.scanned ?? '—'}</div>
        </div>
        <div className="bg-zinc-800 rounded-lg p-4">
          <div className="text-sm text-zinc-400">Filtered</div>
          <div className="text-2xl font-mono">{summary.filtered ?? '—'}</div>
        </div>
        <div className="bg-zinc-800 rounded-lg p-4">
          <div className="text-sm text-zinc-400">Status</div>
          <div className="text-2xl font-mono">
            {!enabled ? 'OFF' : isFallback ? 'FALLBACK' : 'ACTIVE'}
          </div>
        </div>
        <div className="bg-zinc-800 rounded-lg p-4">
          <div className="text-sm text-zinc-400">Refreshed</div>
          <div className="text-2xl font-mono">{timeAgo(snapshot?.created_at)}</div>
        </div>
      </div>

      {/* Family breakdown */}
      {members.length > 0 && !isFallback && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {['momentum', 'mean_reversion', 'volatility', 'derivatives'].map(family => {
            const count = members.filter(m => m.scanner_family === family).length
            const top = members.find(m => m.scanner_family === family)
            return (
              <div key={family} className="bg-zinc-800 rounded-lg p-4">
                <div className={`text-sm ${familyColor(family)}`}>
                  {family.replace('_', ' ')}
                </div>
                <div className="text-lg font-mono">{count} assets</div>
                {top && (
                  <div className="text-xs text-zinc-500 mt-1">
                    Top: {top.symbol} ({top.total_score.toFixed(0)})
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Actions */}
      {enabled && (
        <div className="flex gap-2">
          <button
            onClick={() => refreshMutation.mutate()}
            disabled={refreshMutation.isPending}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded text-sm"
          >
            {refreshMutation.isPending ? 'Refreshing...' : 'Refresh Now'}
          </button>
          {refreshMutation.isError && (
            <span className="text-red-400 text-sm self-center">
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
        <DrawerField label="Total Score" value={member.total_score?.toFixed(1)} />
        <DrawerField label="Opportunity" value={member.opportunity_score?.toFixed(1) ?? '—'} />
        <DrawerField label="Tradability" value={member.tradability_score?.toFixed(0) ?? '—'} />
        <DrawerField label="Liquidity" value={member.liquidity_score?.toFixed(0) ?? '—'} />
        <DrawerField label="Est. Cost" value={member.estimated_cost?.toFixed(1) ?? '—'} />
        <DrawerField label="Crowding Risk" value={member.crowding_risk?.toFixed(1) ?? '—'} />
        <DrawerField label="Late Entry Penalty" value={member.late_entry_penalty?.toFixed(1) ?? '—'} />
      </DrawerSection>

      <DrawerSection label="Market Data">
        <DrawerField label="Volume 24h" value={formatVolume(member.quote_volume_24h)} />
        <DrawerField label="Spread" value={member.spread_bps != null ? `${member.spread_bps.toFixed(1)} bps` : '—'} />
      </DrawerSection>

      <DrawerSection label="Classification">
        <DrawerField label="Family" value={member.scanner_family?.replace('_', ' ') ?? '—'} />
        <DrawerField label="TTL" value={`${member.ttl_sec}s`} />
        <DrawerField label="Expires" value={member.expires_at?.slice(0, 16) ?? '—'} />
        <DrawerField label="Eligible" value={member.execution_eligible ? 'Yes' : 'No'} />
      </DrawerSection>

      {features && (
        <DrawerSection label="Feature Snapshot">
          <DrawerField label="RSI (14)" value={features.rsi_14?.toFixed(1) ?? '—'} />
          <DrawerField label="ATR %" value={features.atr_pct?.toFixed(2) ?? '—'} />
          <DrawerField label="Return 1h" value={features.return_1h != null ? `${features.return_1h.toFixed(2)}%` : '—'} />
          <DrawerField label="Return 4h" value={features.return_4h != null ? `${features.return_4h.toFixed(2)}%` : '—'} />
          <DrawerField label="Return 24h" value={features.return_24h != null ? `${features.return_24h.toFixed(2)}%` : '—'} />
          <DrawerField label="Funding Rate" value={features.funding_rate != null ? `${(features.funding_rate * 100).toFixed(4)}%` : '—'} />
          <DrawerField label="Open Interest" value={features.open_interest != null ? formatVolume(features.open_interest) : '—'} />
          <DrawerField label="OI Delta 1h" value={features.oi_delta_1h != null ? formatVolume(features.oi_delta_1h) : '—'} />
          <DrawerField label="Data Age" value={features.data_freshness_sec != null ? `${features.data_freshness_sec}s` : '—'} />
        </DrawerSection>
      )}

      <DrawerSection label="Reasons">
        {parseJson(member.inclusion_reason).map((r: string, i: number) => (
          <div key={i} className="text-sm text-zinc-300">• {r}</div>
        ))}
        {parseJson(member.inclusion_reason).length === 0 && (
          <div className="text-sm text-zinc-500">No reasons recorded</div>
        )}
      </DrawerSection>

      <DrawerSection label="Risk Flags">
        {parseJson(member.risk_flags).map((f: string, i: number) => (
          <div key={i} className="text-sm text-orange-400">⚠ {f}</div>
        ))}
        {parseJson(member.risk_flags).length === 0 && (
          <div className="text-sm text-zinc-500">No risk flags</div>
        )}
      </DrawerSection>

      {opportunities.length > 0 && (
        <DrawerSection label="Linked Opportunities">
          {opportunities.map((opp) => (
            <div key={opp.id} className="text-sm text-zinc-300" style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>{opp.symbol} · {opp.direction_bias || '—'}</span>
              <span className="text-zinc-500">{opp.status} · {opp.created_at?.slice(0, 16)}</span>
            </div>
          ))}
        </DrawerSection>
      )}
    </DetailDrawer>
  )
}
