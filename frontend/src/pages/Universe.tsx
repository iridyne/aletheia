import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { UniverseMember, UniverseCurrentResponse } from '../types'
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

const columns = [
  col.accessor('rank', { header: '#', cell: info => info.getValue() }),
  col.accessor('symbol', { header: 'Symbol' }),
  col.accessor('scanner_family', {
    header: 'Family',
    cell: info => (
      <span className={familyColor(info.getValue())}>
        {info.getValue() || '—'}
      </span>
    ),
  }),
  col.accessor('total_score', {
    header: 'Score',
    cell: info => info.getValue()?.toFixed(1) ?? '—',
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
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
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
        <DetailDrawer open={true} title={`${selected.symbol} — Rank #${selected.rank}`} onClose={() => setSelected(null)}>
          <DrawerSection label="Scores">
            <DrawerField label="Total Score" value={selected.total_score?.toFixed(1)} />
            <DrawerField label="Opportunity" value={selected.opportunity_score?.toFixed(1) ?? '—'} />
            <DrawerField label="Tradability" value={selected.tradability_score?.toFixed(0) ?? '—'} />
            <DrawerField label="Liquidity" value={selected.liquidity_score?.toFixed(0) ?? '—'} />
            <DrawerField label="Est. Cost" value={selected.estimated_cost?.toFixed(1) ?? '—'} />
            <DrawerField label="Crowding Risk" value={selected.crowding_risk?.toFixed(1) ?? '—'} />
            <DrawerField label="Late Entry Penalty" value={selected.late_entry_penalty?.toFixed(1) ?? '—'} />
          </DrawerSection>
          <DrawerSection label="Classification">
            <DrawerField label="Family" value={selected.scanner_family ?? '—'} />
            <DrawerField label="TTL" value={`${selected.ttl_sec}s`} />
            <DrawerField label="Expires" value={selected.expires_at?.slice(0, 16) ?? '—'} />
            <DrawerField label="Eligible" value={selected.execution_eligible ? 'Yes' : 'No'} />
          </DrawerSection>
          <DrawerSection label="Reasons">
            {parseJson(selected.inclusion_reason).map((r: string, i: number) => (
              <div key={i} className="text-sm text-zinc-300">• {r}</div>
            ))}
            {parseJson(selected.inclusion_reason).length === 0 && (
              <div className="text-sm text-zinc-500">No reasons recorded</div>
            )}
          </DrawerSection>
          <DrawerSection label="Risk Flags">
            {parseJson(selected.risk_flags).map((f: string, i: number) => (
              <div key={i} className="text-sm text-orange-400">⚠ {f}</div>
            ))}
            {parseJson(selected.risk_flags).length === 0 && (
              <div className="text-sm text-zinc-500">No risk flags</div>
            )}
          </DrawerSection>
        </DetailDrawer>
      )}
    </div>
  )
}
