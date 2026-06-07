import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { KnowledgeItem } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'

const TABS = [
  { key: 'research', label: 'Research Notes', fn: () => api.getKnowledge() },
  { key: 'strategy', label: 'Strategy Memory', fn: () => api.getStrategyMemory() },
  { key: 'reviews', label: 'Daily Reviews', fn: () => api.getKnowledge('daily_review') },
  { key: 'dreaming', label: 'Dreaming Logs', fn: () => api.getDreamingLogs() },
] as const

const col = createColumnHelper<KnowledgeItem>()

const columns = [
  col.accessor('title', { header: 'Title' }),
  col.accessor('confidence', {
    header: 'Confidence',
    cell: info => info.getValue() != null ? info.getValue() : '—',
  }),
  col.accessor('status', {
    header: 'Status',
    cell: info => <StatusBadge status={info.getValue()} />,
  }),
  col.accessor('created_at', {
    header: 'Created',
    cell: info => info.getValue()?.slice(0, 16) || '—',
  }),
]

function MarkdownContent({ itemId }: { itemId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['knowledge-content', itemId],
    queryFn: () => api.getKnowledgeContent(itemId),
    enabled: !!itemId,
  })

  if (isLoading) return <p className="text-muted text-sm">Loading content...</p>
  if (!data || data.error) return <p className="text-muted text-sm">{data?.error === 'file_not_found' ? 'File not found on disk.' : 'No content available.'}</p>
  if (!data.content) return <p className="text-muted text-sm">Empty file.</p>

  return (
    <div className="markdown-body" dangerouslySetInnerHTML={{ __html: renderMarkdown(data.content) }} />
  )
}

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
    .replace(/^(.+)$/gm, (line) => {
      if (/^<(h[1-3]|li|ul|hr|p)/.test(line)) return line
      return line
    })
    .replace(/^(?!<[hluop])(.+)$/gm, '<p>$1</p>')
}

export default function KnowledgeBase() {
  const [tab, setTab] = useState<typeof TABS[number]['key']>('research')
  const [selected, setSelected] = useState<KnowledgeItem | null>(null)
  const active = TABS.find(t => t.key === tab)!
  const { data = [] } = useQuery({ queryKey: ['knowledge', tab], queryFn: active.fn })
  const rows = data as KnowledgeItem[]

  function parseTags(raw: string | null): string[] {
    if (!raw) return []
    try { return JSON.parse(raw) } catch { return [] }
  }

  return (
    <div>
      <h2 className="page-title">Knowledge Base</h2>
      <div className="flex gap-2 mb-3">
        {TABS.map(t => (
          <button key={t.key} className={tab === t.key ? '' : 'secondary'} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </div>
      <div className="card">
        <DataTable
          data={rows}
          columns={columns}
          onRowClick={setSelected}
          emptyMessage={`No ${active.label.toLowerCase()} yet.`}
        />
      </div>

      <DetailDrawer open={!!selected} onClose={() => setSelected(null)} title={selected?.title || 'Knowledge Item'}>
        {selected && (
          <>
            <DrawerSection label="Metadata">
              <DrawerField label="Type" value={selected.item_type} />
              <DrawerField label="Confidence" value={selected.confidence} />
              <DrawerField label="Status" value={<StatusBadge status={selected.status} />} />
              <DrawerField label="Strategy ID" value={selected.strategy_id} mono />
              <DrawerField label="Created" value={selected.created_at?.slice(0, 16)} />
              <DrawerField label="Updated" value={selected.updated_at?.slice(0, 16)} />
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
    </div>
  )
}
