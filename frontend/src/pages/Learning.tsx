import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { KnowledgeItem, StrategyRecord } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import Tabs from '../components/Tabs'
import PnLChart from '../charts/PnLChart'
import PageHeader from '../components/PageHeader'
import { fmtNum, humanize, fmtTimestamp } from '../lib/format'

/**
 * Learning (WEBUI_PAGE_MAP.md — Dreaming + Knowledge Base + Reports MERGED).
 *
 * All three read knowledge_items, differing only in temporal framing:
 *   DREAMING — live "what was consolidated recently" (§23.5).
 *   KNOWLEDGE — searchable library, the §4 type taxonomy as sub-tabs (§23.6).
 *   REPORTS  — periodic daily/post-trade reviews + strategy performance metrics.
 *
 * CRITICAL honesty: the §4 knowledge-item taxonomy is preserved — each Knowledge sub-tab
 * queries its real item_type; no type laundering to fill a tab. Every empty/missing/
 * file-not-found state from the source pages is carried over verbatim.
 */

// ---- shared markdown content (read from /knowledge/{id}/content) ----
function MarkdownContent({ itemId }: { itemId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['knowledge-content', itemId],
    queryFn: () => api.getKnowledgeContent(itemId),
    enabled: !!itemId,
  })
  if (isLoading) return <p className="text-muted text-sm">Loading content...</p>
  if (!data || data.error) return <p className="text-muted text-sm">{data?.error === 'file_not_found' ? 'File not found on disk.' : 'No content available.'}</p>
  if (!data.content) return <p className="text-muted text-sm">Empty file.</p>
  return <div className="markdown-body" dangerouslySetInnerHTML={{ __html: renderMarkdown(data.content) }} />
}

// Minimal markdown→HTML (escapes first, then a small subset). Carried from KnowledgeBase.
function renderMarkdown(src: string): string {
  return src
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>')
    .replace(/^---$/gm, '<hr/>')
    .replace(/\n{2,}/g, '</p><p>')
    .replace(/^(?!<[hluop])(.+)$/gm, '<p>$1</p>')
}

export default function Learning() {
  return (
    <div>
      <PageHeader
        title="Learning"
        role="Evolve · the memory surface. Dreaming = what was just consolidated; Knowledge Base = the searchable library by type; Reports = periodic reviews and strategy performance."
      />
      <Tabs
        tabs={[
          { id: 'dreaming', label: 'Dreaming', render: () => <DreamingTab /> },
          { id: 'knowledge', label: 'Knowledge Base', render: () => <KnowledgeTab /> },
          { id: 'reports', label: 'Reports', render: () => <ReportsTab /> },
        ]}
      />
    </div>
  )
}

/* ============================ DREAMING ============================ */
function DreamingTab() {
  const { data: summary } = useQuery({ queryKey: ['dreaming-summary'], queryFn: api.getDreamingSummary })
  const { data: allLogs = [] } = useQuery({ queryKey: ['dreaming'], queryFn: api.getDreamingLogs })
  const [selectedItem, setSelectedItem] = useState<any>(null)

  const recentRuns = summary?.recent_dreaming_runs || []
  const recentResearch = summary?.recent_research_notes || []
  const recentHypotheses = summary?.recent_strategy_hypotheses || []
  const deprecatedMemories = summary?.deprecated_memories || []
  const recentLogs = summary?.recent_dreaming_logs || []

  return (
    <>
      {recentRuns.length > 0 && (
        <div className="card">
          <h3>Current Dreaming Task</h3>
          {recentRuns.map((r: any) => (
            <p key={r.id} className="text-sm">
              <StatusBadge status={r.status} />
              {' '}<span className="mono">{fmtTimestamp(r.started_at)}</span> — {r.agent_name}
            </p>
          ))}
        </div>
      )}

      <div className="card">
        <h3>Recent Consolidations (Dreaming Logs)</h3>
        {recentLogs.length === 0 && (allLogs as any[]).length === 0
          ? <p className="text-muted text-sm">No dreaming logs yet.</p>
          : <ItemList items={recentLogs.length > 0 ? recentLogs : (allLogs as any[]).slice(0, 10)} onSelect={setSelectedItem} />}
      </div>

      <div className="card">
        <h3>New Research Notes (24h)</h3>
        {recentResearch.length === 0
          ? <p className="text-muted text-sm">No new research notes in the last 24 hours.</p>
          : <ItemList items={recentResearch} onSelect={setSelectedItem} />}
      </div>

      <div className="card">
        <h3>New Strategy Hypotheses (24h)</h3>
        {recentHypotheses.length === 0
          ? <p className="text-muted text-sm">No new strategy hypotheses in the last 24 hours.</p>
          : <ItemList items={recentHypotheses} onSelect={setSelectedItem} />}
      </div>

      <div className="card">
        <h3>Deprecated Memories (7d)</h3>
        {deprecatedMemories.length === 0
          ? <p className="text-muted text-sm">No memories deprecated in the last 7 days.</p>
          : <ItemList items={deprecatedMemories} onSelect={setSelectedItem} />}
      </div>

      <DetailDrawer open={!!selectedItem} onClose={() => setSelectedItem(null)} title={selectedItem?.title || 'Knowledge Item'}>
        {selectedItem && (
          <>
            <DrawerSection label="Details">
              <DrawerField label="Type" value={humanize(selectedItem.item_type)} />
              <DrawerField label="Status" value={<StatusBadge status={selectedItem.status || 'active'} />} />
              <DrawerField label="Created" value={fmtTimestamp(selectedItem.created_at)} mono />
              <DrawerField label="Strategy" value={selectedItem.strategy_id} mono />
            </DrawerSection>
            <DrawerSection label="Content">
              <MarkdownContent itemId={selectedItem.id} />
            </DrawerSection>
          </>
        )}
      </DetailDrawer>
    </>
  )
}

function ItemList({ items, onSelect }: { items: any[]; onSelect: (item: any) => void }) {
  return (
    <table>
      <thead><tr><th>Title</th><th>Type</th><th>Created</th></tr></thead>
      <tbody>
        {items.map((item: any) => (
          <tr key={item.id} className="clickable" onClick={() => onSelect(item)}>
            <td>{item.title || '(untitled)'}</td>
            <td>{humanize(item.item_type)}</td>
            <td className="mono">{fmtTimestamp(item.created_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/* ============================ KNOWLEDGE BASE ============================ */
// §4 taxonomy preserved: each sub-tab queries its real item_type (no laundering).
const KB_TABS = [
  { key: 'research', label: 'Research Notes', fn: () => api.getKnowledge() },
  { key: 'strategy', label: 'Strategy Memory', fn: () => api.getStrategyMemory() },
  { key: 'reviews', label: 'Daily Reviews', fn: () => api.getKnowledge('daily_review') },
  { key: 'dreaming', label: 'Dreaming Logs', fn: () => api.getDreamingLogs() },
] as const

const kbCol = createColumnHelper<KnowledgeItem>()
const kbColumns = [
  kbCol.accessor('title', { header: 'Title' }),
  kbCol.accessor('confidence', { header: 'Confidence', cell: info => <span className="mono">{info.getValue() != null ? info.getValue() : '—'}</span> }),
  kbCol.accessor('status', { header: 'Status', cell: info => <StatusBadge status={info.getValue()} /> }),
  kbCol.accessor('created_at', { header: 'Created', cell: info => <span className="mono">{fmtTimestamp(info.getValue())}</span> }),
]

function KnowledgeTab() {
  const [tab, setTab] = useState<typeof KB_TABS[number]['key']>('research')
  const [selected, setSelected] = useState<KnowledgeItem | null>(null)
  const active = KB_TABS.find(t => t.key === tab)!
  const { data = [] } = useQuery({ queryKey: ['knowledge', tab], queryFn: active.fn })
  const rows = data as KnowledgeItem[]

  function parseTags(raw: string | null): string[] {
    if (!raw) return []
    try { return JSON.parse(raw) } catch { return [] }
  }

  return (
    <>
      <div className="flex gap-2 mb-3">
        {KB_TABS.map(t => (
          <button key={t.key} className={tab === t.key ? '' : 'secondary'} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </div>
      <div className="card">
        <DataTable data={rows} columns={kbColumns} onRowClick={setSelected} emptyMessage={`No ${active.label.toLowerCase()} yet.`} />
      </div>

      <DetailDrawer open={!!selected} onClose={() => setSelected(null)} title={selected?.title || 'Knowledge Item'}>
        {selected && (
          <>
            <DrawerSection label="Metadata">
              <DrawerField label="Type" value={humanize(selected.item_type)} />
              <DrawerField label="Confidence" value={selected.confidence} mono />
              <DrawerField label="Status" value={<StatusBadge status={selected.status} />} />
              <DrawerField label="Strategy ID" value={selected.strategy_id} mono />
              <DrawerField label="Created" value={fmtTimestamp(selected.created_at)} mono />
              <DrawerField label="Updated" value={fmtTimestamp(selected.updated_at)} mono />
            </DrawerSection>
            <DrawerSection label="Tags">
              {(() => {
                const tags = parseTags(selected.tags)
                return tags.length > 0
                  ? <div className="flex flex-wrap gap-1">{tags.map(t => <span key={t} className="badge badge-blue">{t}</span>)}</div>
                  : <span className="text-muted text-sm">No tags</span>
              })()}
            </DrawerSection>
            <DrawerSection label="Content">
              <MarkdownContent itemId={selected.id} />
            </DrawerSection>
          </>
        )}
      </DetailDrawer>
    </>
  )
}

/* ============================ REPORTS ============================ */
function ReportsTab() {
  const { data: dreaming = [] } = useQuery({ queryKey: ['dreaming'], queryFn: api.getDreamingLogs })
  const { data: reviews = [] } = useQuery({ queryKey: ['reviews'], queryFn: () => api.getKnowledge('daily_review') })
  const { data: postTradeReviews = [] } = useQuery({ queryKey: ['post-trade-reviews'], queryFn: api.getPostTradeReviews })
  const { data: strategies = [] } = useQuery({ queryKey: ['strategies'], queryFn: api.getStrategies })

  const activeStrategies = (strategies as StrategyRecord[]).filter(s => s.state !== 'draft' && s.state !== 'retired')

  return (
    <>
      <div className="card">
        <h3>Portfolio PnL</h3>
        <PnLChart />
      </div>
      {activeStrategies.length > 0 && (
        <div className="card">
          <h3>Strategy Metrics</h3>
          {activeStrategies.map(s => <StrategyMetrics key={s.id} strategy={s} />)}
        </div>
      )}
      <div className="card">
        <h3>Daily Reviews</h3>
        {(reviews as any[]).length === 0
          ? <p className="hint">No daily reviews yet — generated by the dreaming cycle.</p>
          : (reviews as any[]).slice(0, 10).map((r: any) => <p key={r.id} className="text-sm">{r.title} — <span className="mono text-muted">{fmtTimestamp(r.created_at)}</span></p>)}
      </div>
      <div className="card">
        <h3>Recent Consolidations</h3>
        {(dreaming as any[]).length === 0
          ? <p className="hint">No consolidations yet.</p>
          : (dreaming as any[]).slice(0, 10).map((d: any) => <p key={d.id} className="text-sm">{d.title} — <span className="mono text-muted">{fmtTimestamp(d.created_at)}</span></p>)}
      </div>
      <div className="card">
        <h3>Post-Trade Reviews</h3>
        {(postTradeReviews as any[]).length === 0
          ? <p className="hint">No post-trade reviews yet.</p>
          : (postTradeReviews as any[]).slice(0, 10).map((r: any) => <p key={r.id} className="text-sm">{r.title} — <span className="mono text-muted">{fmtTimestamp(r.created_at)}</span></p>)}
      </div>
    </>
  )
}

function StrategyMetrics({ strategy }: { strategy: StrategyRecord }) {
  const { data } = useQuery({
    queryKey: ['strategy-gates', strategy.id],
    queryFn: () => api.getStrategyGates(strategy.id),
    enabled: !!strategy.id,
  })
  const metrics = data?.metrics as Record<string, any> | undefined
  if (!metrics) return null
  return (
    <div className="strategy-metrics-row">
      <p className="strategy-metrics-name">{strategy.name} <StatusBadge status={strategy.state} /></p>
      <div className="strategy-metrics-grid">
        {Object.entries(metrics).filter(([, v]) => v != null && typeof v !== 'string').map(([k, v]) => (
          <div key={k} className="text-sm">
            <span className="text-muted">{humanize(k)}</span>
            <br /><span className="mono">{typeof v === 'number' ? (Number.isInteger(v) ? v : fmtNum(v)) : String(v)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
