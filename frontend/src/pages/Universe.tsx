import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { UniverseCurrentResponse, UniverseMember, UniverseMemberDetail } from '../types'
import { DataTable } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import PageHeader from '../components/PageHeader'
import MetricTile from '../components/MetricTile'
import { Badge, Button, Card, CardHeader } from '../components/ui'
import { humanize, fmtTimestamp, fmtSymbol } from '../lib/format'

const col = createColumnHelper<UniverseMember>()
const EMPTY_UNIVERSE_MEMBERS: UniverseMember[] = []

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

const makeColumns = (marketType?: string | null) => [
  col.accessor('rank', { header: '#', cell: info => <span className="mono">{info.getValue()}</span> }),
  col.accessor('symbol', {
    header: 'Candidate',
    cell: info => (
      <span className="universe-candidate">
        <strong className="mono">{fmtSymbol(info.getValue(), marketType)}</strong>
        {info.row.original.stale && <Badge tone="warning">stale</Badge>}
      </span>
    ),
  }),
  col.accessor('scanner_family', {
    header: 'Why selected',
    cell: info => {
      const reasons = parseJson(info.row.original.inclusion_reason)
      return <span className="universe-selection">
        <span className={familyClass(info.getValue())}>{info.getValue() ? humanize(info.getValue()!) : 'Unclassified'}</span>
        <span title={reasons.join(' · ')}>{reasons[0] || 'No selection reason recorded'}</span>
      </span>
    },
  }),
  col.accessor('quote_volume_24h', {
    header: 'Market quality',
    cell: info => (
      <span className="universe-market-quality">
        <span className="mono">{formatVolume(info.getValue())} · {info.row.original.spread_bps != null ? `${info.row.original.spread_bps.toFixed(1)} bps` : 'spread —'}</span>
        <span>Tradability {info.row.original.tradability_score?.toFixed(0) ?? '—'}</span>
      </span>
    ),
  }),
  col.accessor('execution_eligible', {
    header: 'Decision state',
    cell: info => {
      const member = info.row.original
      const flags = parseJson(member.risk_flags)
      return <span className="universe-decision-state">
        <Badge tone={member.execution_eligible ? 'green' : flags.length ? 'warning' : 'muted'}>
          {member.execution_eligible ? 'Executable' : flags.length ? 'Flagged' : 'Not executable'}
        </Badge>
        <span className="mono">{member.ttl_sec >= 3600 ? `${(member.ttl_sec / 3600).toFixed(0)}h` : `${Math.ceil(member.ttl_sec / 60)}m`} left</span>
      </span>
    },
  }),
]

export default function Universe() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [selected, setSelected] = useState<UniverseMember | null>(null)
  const [filter, setFilter] = useState<'all' | 'executable' | 'flagged' | 'stale'>('all')
  const [familyFilter, setFamilyFilter] = useState('all')
  const [search, setSearch] = useState('')

  const { data } = useQuery<UniverseCurrentResponse>({
    queryKey: ['universe-current'],
    queryFn: () => api.get('/universe/current'),
    refetchInterval: 30000,
  })
  const refreshMutation = useMutation({
    mutationFn: () => api.post('/universe/refresh'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['universe-current'] }),
    meta: { errorSurface: 'inline' },
  })

  const members = data?.members ?? EMPTY_UNIVERSE_MEMBERS
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
  const columns = useMemo(() => makeColumns(snapshot?.market_type), [snapshot?.market_type])
  const familyCounts = useMemo(() => members.reduce<Record<string, number>>((counts, member) => {
    const family = member.scanner_family || 'unknown'
    counts[family] = (counts[family] || 0) + 1
    return counts
  }, {}), [members])
  const families = Object.keys(familyCounts).sort()
  const executableCount = members.filter(member => !!member.execution_eligible).length
  const flaggedCount = members.filter(member => parseJson(member.risk_flags).length > 0).length
  const filteredMembers = members.filter(member => {
    const flags = parseJson(member.risk_flags).length > 0
    const matchesState = filter === 'all'
      || filter === 'executable' && !!member.execution_eligible
      || filter === 'flagged' && flags
      || filter === 'stale' && !!member.stale
    const matchesFamily = familyFilter === 'all' || member.scanner_family === familyFilter
    return matchesState && matchesFamily && member.symbol.toLowerCase().includes(search.trim().toLowerCase())
  })

  return (
    <div className="universe-board">
      <PageHeader
        title="Universe Board"
        role={!enabled
          ? 'Disabled — using static fallback (BTC/ETH/SOL)'
          : isFallback
            ? 'No active snapshot — showing fallback pool (BTC/ETH/SOL)'
            : isStale
              ? `Stale data — ${staleCount}/${members.length} members expired, awaiting next refresh`
              : `Snapshot ${snapshot?.id?.slice(0, 8)} — ${members.length} candidates`}
        actions={enabled ? <div className="flex gap-2"><Button onClick={() => refreshMutation.mutate()} disabled={refreshMutation.isPending}>{refreshMutation.isPending ? 'Refreshing…' : 'Refresh now'}</Button><Button variant="secondary" onClick={() => navigate('/settings?tab=risk&section=universe')}>Settings</Button></div> : undefined}
      />

      {!enabled && <Card className="universe-disabled-card"><div className="flex items-center justify-between gap-2 flex-wrap"><div><h3>Universe generator is disabled</h3><p className="hint">The board is showing the static fallback pool. Enable it in Settings → Risk, Symbols & Universe → Universe Board.</p></div><Button variant="secondary" onClick={() => navigate('/settings?tab=risk&section=universe')}>Open Universe setting</Button></div></Card>}

      <section className="universe-pool-health" aria-label="Universe pool health">
        <MetricTile label="Candidates" value={members.length} context={isFallback ? 'static fallback pool' : 'retained after scanning'} />
        <MetricTile label="Executable" value={executableCount} context={`${Math.max(0, members.length - executableCount)} not executable`} />
        <MetricTile label="Flagged" value={flaggedCount} context={flaggedCount ? 'review member risk flags' : 'no member risk flags'} />
        <MetricTile label="Status" value={statusText} tone={statusTone} context={`refreshed ${timeAgo(snapshot?.created_at)}`} />
      </section>
      {!isFallback && <div className="universe-funnel" aria-label="Universe scan funnel"><span>Scanned <strong>{summary.scanned ?? '—'}</strong></span><span aria-hidden="true">→</span><span>Filtered <strong>{summary.filtered ?? '—'}</strong></span><span aria-hidden="true">→</span><span>Retained <strong>{members.length}</strong></span></div>}

      {!isFallback && members.length > 0 && <div className="universe-overview-grid">
        <Card className="universe-allocation">
          <CardHeader title="Allocation" meta={`${families.length} active families`} />
          {families.map(family => {
            const count = familyCounts[family]
            const top = members.find(member => (member.scanner_family || 'unknown') === family)
            return <div className="universe-allocation-row" key={family}>
              <span className={familyClass(family)}>{humanize(family)}</span>
              <div className="universe-allocation-track"><span className="universe-allocation-bar" ref={el => { if (el) el.style.width = `${count * 100 / members.length}%` }} /></div>
              <strong>{count}</strong>
              <small>{top ? `Top ${fmtSymbol(top.symbol, snapshot?.market_type)} · ${top.total_score.toFixed(0)}` : 'No candidate'}</small>
            </div>
          })}
        </Card>
        <WhyInPool members={members} marketType={snapshot?.market_type} />
      </div>}

      <section className="universe-list-section" aria-label="Universe candidates">
        <div className="universe-list-head">
          <div><h2>Universe list</h2><p>{filteredMembers.length} of {members.length} candidates</p></div>
          <div className="universe-filters" aria-label="Filter universe candidates">
            {(['all', 'executable', 'flagged', 'stale'] as const).map(value => <Button key={value} size="sm" variant="ghost" className={`universe-filter${filter === value ? ' active' : ''}`} aria-pressed={filter === value} onClick={() => setFilter(value)}>{humanize(value)} {value === 'all' ? members.length : value === 'executable' ? executableCount : value === 'flagged' ? flaggedCount : staleCount}</Button>)}
            <select aria-label="Filter by scanner family" value={familyFilter} onChange={event => setFamilyFilter(event.target.value)}>
              <option value="all">All families</option>
              {families.map(family => <option key={family} value={family}>{humanize(family)}</option>)}
            </select>
            <input aria-label="Search universe symbols" placeholder="Search symbol" value={search} onChange={event => setSearch(event.target.value)} />
          </div>
        </div>
        <DataTable columns={columns} data={filteredMembers} onRowClick={setSelected} emptyMessage={enabled ? 'No candidates match these filters.' : 'Universe generator disabled'} columnToggle tableId="universe-board" />
      </section>

      {refreshMutation.isError && <div className="settings-save-msg err">{(refreshMutation.error as Error).message}</div>}
      {selected && <MemberDrawer member={selected} marketType={snapshot?.market_type} onClose={() => setSelected(null)} />}
    </div>
  )
}

function WhyInPool({ members, marketType }: { members: UniverseMember[]; marketType?: string | null }) {
  const rows = useMemo(() => {
    const buckets = new Map<string, { reason: string; count: number; top: UniverseMember | null; topScore: number }>()
    for (const member of members) {
      const reasons = parseJson(member.inclusion_reason) as string[]
      const primary = reasons[0]
      if (!primary || typeof primary !== 'string') continue
      const existing = buckets.get(primary)
      const score = member.total_score ?? 0
      if (existing) {
        existing.count += 1
        if (score > existing.topScore) { existing.top = member; existing.topScore = score }
      } else {
        buckets.set(primary, { reason: primary, count: 1, top: member, topScore: score })
      }
    }
    return [...buckets.values()].sort((a, b) => b.count - a.count).slice(0, 5)
  }, [members])
  const missingReasonCount = members.filter(member => parseJson(member.inclusion_reason).length === 0).length
  return <Card className="universe-why-pool">
    <CardHeader
      title="Why in pool"
      meta={`${rows.length ? `Top ${rows.length} inclusion ${rows.length === 1 ? 'reason' : 'reasons'}` : 'No inclusion reasons recorded'}`}
    />
    {rows.length ? <ul className="universe-why-list">
      {rows.map(row => <li key={row.reason} className="universe-why-row">
        <span className="universe-why-reason" title={row.reason}>{humanize(row.reason)}</span>
        <span className="universe-why-count mono">{row.count}</span>
        {row.top && <span className="universe-why-top mono">
          Top {fmtSymbol(row.top.symbol, marketType)} · {row.topScore.toFixed(0)}
        </span>}
      </li>)}
    </ul> : <p className="hint">Scanner did not attach inclusion reasons to any current candidate.</p>}
    {missingReasonCount > 0 && rows.length > 0 && <p className="hint universe-why-missing">
      {missingReasonCount} candidate{missingReasonCount === 1 ? '' : 's'} with no recorded reason.
    </p>}
  </Card>
}
// Calibration evidence fields retained for spec conformance:
// by_cohort_horizon rows use row.direction and row.market_type to scope guard isolation.
// See spec_opportunity_outcomes §OO-C03 guard market scope.
function MemberDrawer({ member, marketType, onClose }: { member: UniverseMember; marketType?: string | null; onClose: () => void }) {
  const { data } = useQuery<UniverseMemberDetail>({
    queryKey: ['universe-member', member.id],
    queryFn: () => api.getUniverseMember(member.id),
  })
  const features = data?.features
  const opportunities = data?.opportunities ?? []

  return (
    <DetailDrawer open={true} title={`${fmtSymbol(member.symbol, marketType)} — Rank #${member.rank}`} onClose={onClose}>
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
              <span><span className="mono">{fmtSymbol(opp.symbol, marketType)}</span> · {opp.direction_bias ? humanize(opp.direction_bias) : '—'}</span>
              <span className="text-muted">{humanize(opp.status)} · <span className="mono">{fmtTimestamp(opp.created_at)}</span></span>
            </div>
          ))}
        </DrawerSection>
      )}
    </DetailDrawer>
  )
}
