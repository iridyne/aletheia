import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { DetailDrawer, DrawerSection } from '../components/DetailDrawer'

export default function Dreaming() {
  const { data: summary } = useQuery({ queryKey: ['dreaming-summary'], queryFn: api.getDreamingSummary })
  const { data: allLogs = [] } = useQuery({ queryKey: ['dreaming'], queryFn: api.getDreamingLogs })
  const [selectedItem, setSelectedItem] = useState<any>(null)

  const recentRuns = summary?.recent_dreaming_runs || []
  const recentResearch = summary?.recent_research_notes || []
  const recentHypotheses = summary?.recent_strategy_hypotheses || []
  const deprecatedMemories = summary?.deprecated_memories || []
  const recentLogs = summary?.recent_dreaming_logs || []

  return (
    <div>
      <h2 className="page-title">Dreaming / Learning</h2>

      {recentRuns.length > 0 && (
        <div className="card">
          <h3>Current Dreaming Task</h3>
          {recentRuns.map((r: any) => (
            <p key={r.id} style={{ fontSize: '0.82rem' }}>
              <span className={`badge ${r.status === 'completed' ? 'badge-green' : r.status === 'running' ? 'badge-yellow' : 'badge-red'}`}>{r.status}</span>
              {' '}{r.started_at?.slice(0, 16)} — {r.agent_name}
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
              <p className="text-sm"><strong>Type:</strong> {selectedItem.item_type}</p>
              <p className="text-sm"><strong>Status:</strong> {selectedItem.status || 'active'}</p>
              <p className="text-sm"><strong>Created:</strong> {selectedItem.created_at?.slice(0, 16)}</p>
              {selectedItem.strategy_id && <p className="text-sm"><strong>Strategy:</strong> <span className="mono">{selectedItem.strategy_id}</span></p>}
            </DrawerSection>
            <DrawerSection label="Content">
              <MarkdownContent itemId={selectedItem.id} />
            </DrawerSection>
          </>
        )}
      </DetailDrawer>
    </div>
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
            <td>{item.item_type}</td>
            <td>{item.created_at?.slice(0, 16) || '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function MarkdownContent({ itemId }: { itemId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['knowledge-content', itemId],
    queryFn: () => api.getKnowledgeContent(itemId),
    enabled: !!itemId,
  })
  if (isLoading) return <p className="text-muted text-sm">Loading content...</p>
  if (!data || data.error) return <p className="text-muted text-sm">{data?.error === 'file_not_found' ? 'File not found on disk.' : 'No content available.'}</p>
  if (!data.content) return <p className="text-muted text-sm">Empty file.</p>
  return <div className="markdown-body"><pre style={{ whiteSpace: 'pre-wrap', fontSize: '0.8rem' }}>{data.content}</pre></div>
}
