import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import PageHeader from '../components/PageHeader'
import { Badge, Button, Card } from '../components/ui'
import { humanize, fmtTimestamp } from '../lib/format'

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

const SENTIMENT_TONE: Record<string, 'green' | 'red' | 'blue' | 'yellow'> = {
  bullish: 'green',
  bearish: 'red',
  neutral: 'blue',
  mixed: 'yellow',
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

  return (
    <div>
      <PageHeader
        title="News Intelligence"
        role="Operate · out-of-MVP-spec surface (§17.3 lists news ingestion as future). No automated feed runs — items are entered manually via the API."
      />

      <div className="notice-banner">
        <span className="notice-icon">⚠</span>
        <span className="notice-text">
          <b>Manual entry only.</b> The automated news-ingestion pipeline is not configured (it is future scope per §17.3 / AGENTS.md). What you see here is whatever has been added by hand via <span className="mono">POST /api/news</span> — this is not a live market-news feed.
        </span>
      </div>

      <div className="flex items-center gap-2 mb-3">
        {['all', 'new', 'reviewed'].map(f => (
          <Button
            key={f}
            variant={f === filter ? 'primary' : 'secondary'}
            className="btn-sm"
            onClick={() => setFilter(f)}
          >
            {humanize(f)}
          </Button>
        ))}
        <span className="text-muted text-sm">
          {items.length} items
        </span>
      </div>

      {items.length === 0 ? (
        <Card>
          <p className="text-muted">No news items yet. News will appear here once the ingestion pipeline is configured or items are added manually via the API.</p>
        </Card>
      ) : (
        <div className="news-list">
          {items.map(item => (
            <Card
              key={item.id}
              className="news-card clickable"
              onClick={() => { setSelected(item); setAnalysis(null) }}
            >
              <div className="flex justify-between items-center gap-2">
                <div className="news-card-main">
                  <div className="news-title">{item.title}</div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-muted text-sm">{item.source}</span>
                    <span className="text-muted text-sm mono">{fmtTimestamp(item.published_at)}</span>
                    {item.symbols_mentioned?.map(s => (
                      <Badge key={s} tone="blue">{s}</Badge>
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone={SENTIMENT_TONE[item.sentiment] || 'blue'}>
                    {humanize(item.sentiment)}
                  </Badge>
                  <Badge tone={item.status === 'new' ? 'yellow' : 'muted'}>
                    {humanize(item.status)}
                  </Badge>
                </div>
              </div>
              {item.summary && (
                <p className="text-muted text-sm mt-2 news-summary">{item.summary}</p>
              )}
            </Card>
          ))}
        </div>
      )}

      <DetailDrawer open={!!selected} onClose={() => setSelected(null)} title="News Detail">
        {selected && (
          <>
            <DrawerSection label="Summary">
              <DrawerField label="Title" value={selected.title} />
              <DrawerField label="Source" value={selected.source} />
              <DrawerField label="Published" value={fmtTimestamp(selected.published_at)} mono />
              <DrawerField label="Event Type" value={humanize(selected.event_type)} />
              <DrawerField label="Sentiment" value={<Badge tone={SENTIMENT_TONE[selected.sentiment] || 'blue'}>{humanize(selected.sentiment)}</Badge>} />
              <DrawerField label="Relevance" value={`${(selected.relevance_score * 100).toFixed(0)}%`} mono />
              <DrawerField label="Status" value={<Badge tone={selected.status === 'new' ? 'yellow' : 'muted'}>{humanize(selected.status)}</Badge>} />
            </DrawerSection>

            {selected.symbols_mentioned?.length > 0 && (
              <DrawerSection label="Related Assets">
                <div className="flex gap-2 flex-wrap">
                  {selected.symbols_mentioned.map(s => (
                    <Badge key={s} tone="blue">{s}</Badge>
                  ))}
                </div>
              </DrawerSection>
            )}

            {selected.summary && (
              <DrawerSection label="Content">
                <p className="text-sm news-prose">{selected.summary}</p>
              </DrawerSection>
            )}

            {selected.url && (
              <DrawerSection label="Source Link">
                <a href={selected.url} target="_blank" rel="noopener noreferrer" className="text-sm">
                  {selected.url}
                </a>
              </DrawerSection>
            )}

            <DrawerSection label="AI Analysis">
              {analysis ? (
                <p className="text-sm news-prose">{analysis}</p>
              ) : (
                <Button
                  className="btn-sm"
                  onClick={() => analyzeMut.mutate(selected.id)}
                  disabled={analyzeMut.isPending}
                >
                  {analyzeMut.isPending ? 'Analyzing…' : 'Analyze Impact'}
                </Button>
              )}
            </DrawerSection>
          </>
        )}
      </DetailDrawer>
    </div>
  )
}
