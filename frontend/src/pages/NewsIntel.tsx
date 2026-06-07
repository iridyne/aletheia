import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'

type NewsItem = {
  id: string
  title: string
  source: string
  url?: string
  summary?: string
  symbols_mentioned: string[]
  event_type: string
  sentiment: string
  relevance_score: number
  published_at: string
  fetched_at: string
  status: string
}

const SENTIMENT_BADGE: Record<string, string> = {
  bullish: 'badge-green',
  bearish: 'badge-red',
  neutral: 'badge-blue',
  mixed: 'badge-yellow',
}

export default function NewsIntel() {
  const qc = useQueryClient()
  const { data: news = [] } = useQuery({ queryKey: ['news'], queryFn: () => api.getNews() })
  const [selected, setSelected] = useState<NewsItem | null>(null)
  const [analysis, setAnalysis] = useState<string | null>(null)
  const [filter, setFilter] = useState<string>('all')

  const analyzeMut = useMutation({
    mutationFn: (id: string) => api.analyzeNews(id),
    onSuccess: (data: any) => {
      setAnalysis(data.analysis)
      qc.invalidateQueries({ queryKey: ['news'] })
    },
  })

  const items = (news as NewsItem[]).filter(item => {
    if (filter === 'all') return true
    if (filter === 'new') return item.status === 'new'
    if (filter === 'reviewed') return item.status === 'reviewed'
    return true
  })

  function formatTime(ts: string): string {
    try {
      return new Date(ts).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    } catch { return ts }
  }

  return (
    <div>
      <h2 className="page-title">News Intelligence</h2>

      <div className="flex items-center gap-2 mb-3">
        {['all', 'new', 'reviewed'].map(f => (
          <button
            key={f}
            className={f === filter ? '' : 'secondary'}
            style={{ padding: '0.25rem 0.6rem', fontSize: '0.75rem' }}
            onClick={() => setFilter(f)}
          >
            {f}
          </button>
        ))}
        <span className="text-muted text-sm" style={{ marginLeft: '0.5rem' }}>
          {items.length} items
        </span>
      </div>

      {items.length === 0 ? (
        <div className="card">
          <p className="text-muted">No news items yet. News will appear here once the ingestion pipeline is configured or items are added manually via the API.</p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          {items.map(item => (
            <div
              key={item.id}
              className="card"
              style={{ cursor: 'pointer', padding: '0.8rem 1rem', marginBottom: 0 }}
              onClick={() => { setSelected(item); setAnalysis(null) }}
            >
              <div className="flex justify-between items-center">
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '0.85rem', fontWeight: 500, marginBottom: '0.2rem' }}>
                    {item.title}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-muted text-sm">{item.source}</span>
                    <span className="text-muted text-sm">{formatTime(item.published_at)}</span>
                    {item.symbols_mentioned?.map(s => (
                      <span key={s} className="badge badge-blue" style={{ fontSize: '0.6rem', padding: '0.05rem 0.3rem' }}>{s}</span>
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`badge ${SENTIMENT_BADGE[item.sentiment] || 'badge-blue'}`}>
                    {item.sentiment}
                  </span>
                  <span className={`badge ${item.status === 'new' ? 'badge-yellow' : 'badge-green'}`}>
                    {item.status}
                  </span>
                </div>
              </div>
              {item.summary && (
                <p className="text-muted text-sm mt-2" style={{ maxWidth: '80ch' }}>
                  {item.summary.length > 150 ? item.summary.slice(0, 150) + '...' : item.summary}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      <DetailDrawer open={!!selected} onClose={() => setSelected(null)} title="News Detail">
        {selected && (
          <>
            <DrawerSection label="Summary">
              <DrawerField label="Title" value={selected.title} />
              <DrawerField label="Source" value={selected.source} />
              <DrawerField label="Published" value={formatTime(selected.published_at)} mono />
              <DrawerField label="Event Type" value={selected.event_type} />
              <DrawerField label="Sentiment" value={<span className={`badge ${SENTIMENT_BADGE[selected.sentiment] || 'badge-blue'}`}>{selected.sentiment}</span>} />
              <DrawerField label="Relevance" value={`${(selected.relevance_score * 100).toFixed(0)}%`} mono />
              <DrawerField label="Status" value={selected.status} />
            </DrawerSection>

            {selected.symbols_mentioned?.length > 0 && (
              <DrawerSection label="Related Assets">
                <div className="flex gap-2 flex-wrap">
                  {selected.symbols_mentioned.map(s => (
                    <span key={s} className="badge badge-blue">{s}</span>
                  ))}
                </div>
              </DrawerSection>
            )}

            {selected.summary && (
              <DrawerSection label="Content">
                <p style={{ fontSize: '0.82rem', lineHeight: 1.6 }}>{selected.summary}</p>
              </DrawerSection>
            )}

            {selected.url && (
              <DrawerSection label="Source Link">
                <a href={selected.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: '0.8rem' }}>
                  {selected.url}
                </a>
              </DrawerSection>
            )}

            <DrawerSection label="AI Analysis">
              {analysis ? (
                <p style={{ fontSize: '0.82rem', lineHeight: 1.6 }}>{analysis}</p>
              ) : (
                <button
                  onClick={() => analyzeMut.mutate(selected.id)}
                  disabled={analyzeMut.isPending}
                  style={{ fontSize: '0.78rem' }}
                >
                  {analyzeMut.isPending ? 'Analyzing...' : 'Analyze Impact'}
                </button>
              )}
            </DrawerSection>
          </>
        )}
      </DetailDrawer>
    </div>
  )
}
