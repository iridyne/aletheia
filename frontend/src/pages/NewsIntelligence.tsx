import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useInspector } from '../context/InspectorContext'

export default function NewsIntelligence() {
  const qc = useQueryClient()
  const { setObject } = useInspector()
  const [filter, setFilter] = useState<string>('all')
  const [showAdd, setShowAdd] = useState(false)
  const { data: news = [] } = useQuery({ queryKey: ['news'], queryFn: () => api.getNews(50), refetchInterval: 15000 })

  const archiveMut = useMutation({
    mutationFn: (id: string) => api.archiveNews(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['news'] }),
  })
  const researchMut = useMutation({
    mutationFn: (id: string) => api.createResearchNoteFromNews(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['news'] }),
  })

  const filtered = filter === 'all'
    ? (news as any[])
    : (news as any[]).filter(n => n.status === filter)

  return (
    <div>
      <h2 className="page-title">News Intelligence</h2>

      <div className="news-toolbar">
        <div className="news-filters">
          {['all', 'new', 'reviewed', 'linked', 'archived'].map(f => (
            <button key={f} className={`bt-filter ${filter === f ? 'bt-filter-active' : ''}`} onClick={() => setFilter(f)}>
              {f}
            </button>
          ))}
        </div>
        <button className="secondary" onClick={() => setShowAdd(!showAdd)}>
          {showAdd ? 'Cancel' : '+ Add News'}
        </button>
      </div>

      {showAdd && <AddNewsForm onDone={() => { setShowAdd(false); qc.invalidateQueries({ queryKey: ['news'] }) }} />}

      <div className="card">
        {filtered.length === 0 ? (
          <p style={{ color: 'var(--muted)' }}>No news items yet. Add news manually or wait for agent ingestion.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Headline</th>
                <th>Assets</th>
                <th>Type</th>
                <th>Sentiment</th>
                <th>Urgency</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((n: any) => (
                <tr key={n.id} onClick={() => setObject({ type: 'news', id: n.id, data: n })}>
                  <td style={{ maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={n.headline}>
                    {n.headline}
                  </td>
                  <td>{(n.related_assets || []).join(', ') || '—'}</td>
                  <td><span className="badge badge-blue">{n.event_type}</span></td>
                  <td><span className={`badge ${sentimentBadge(n.sentiment)}`}>{n.sentiment}</span></td>
                  <td><span className={`badge ${urgencyBadge(n.urgency)}`}>{n.urgency}</span></td>
                  <td><span className={`badge ${statusBadge(n.status)}`}>{n.status}</span></td>
                  <td onClick={e => e.stopPropagation()}>
                    <div style={{ display: 'flex', gap: '0.2rem' }}>
                      {n.status !== 'archived' && (
                        <>
                          <button className="secondary" style={{ fontSize: '0.68rem', padding: '0.2rem 0.4rem' }} onClick={() => researchMut.mutate(n.id)} title="Create research note">📝</button>
                          <button className="secondary" style={{ fontSize: '0.68rem', padding: '0.2rem 0.4rem' }} onClick={() => archiveMut.mutate(n.id)} title="Archive">📦</button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function AddNewsForm({ onDone }: { onDone: () => void }) {
  const [headline, setHeadline] = useState('')
  const [source, setSource] = useState('')
  const [summary, setSummary] = useState('')
  const [assets, setAssets] = useState('')
  const [eventType, setEventType] = useState('market')
  const [sentiment, setSentiment] = useState('neutral')
  const [urgency, setUrgency] = useState('low')

  const createMut = useMutation({
    mutationFn: () => api.createNews({
      headline,
      source,
      summary,
      related_assets: assets.split(',').map(a => a.trim()).filter(Boolean),
      event_type: eventType,
      sentiment,
      urgency,
    }),
    onSuccess: onDone,
  })

  return (
    <div className="card" style={{ marginBottom: '1rem' }}>
      <h3>Add News Item</h3>
      <div className="form-grid">
        <label>
          Headline
          <input value={headline} onChange={e => setHeadline(e.target.value)} placeholder="Breaking: ..." />
        </label>
        <label>
          Source
          <input value={source} onChange={e => setSource(e.target.value)} placeholder="Reuters, CoinDesk, ..." />
        </label>
        <label>
          Related Assets
          <input value={assets} onChange={e => setAssets(e.target.value)} placeholder="BTC, ETH, SOL" />
        </label>
        <label>
          Event Type
          <select value={eventType} onChange={e => setEventType(e.target.value)}>
            <option value="macro">Macro</option>
            <option value="exchange">Exchange</option>
            <option value="protocol">Protocol</option>
            <option value="regulatory">Regulatory</option>
            <option value="security">Security</option>
            <option value="market">Market</option>
            <option value="other">Other</option>
          </select>
        </label>
        <label>
          Sentiment
          <select value={sentiment} onChange={e => setSentiment(e.target.value)}>
            <option value="bullish">Bullish</option>
            <option value="bearish">Bearish</option>
            <option value="neutral">Neutral</option>
            <option value="mixed">Mixed</option>
            <option value="unknown">Unknown</option>
          </select>
        </label>
        <label>
          Urgency
          <select value={urgency} onChange={e => setUrgency(e.target.value)}>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </label>
      </div>
      <label style={{ display: 'block', marginTop: '0.5rem', fontSize: '0.75rem', color: 'var(--muted)' }}>
        Summary
        <textarea
          value={summary} onChange={e => setSummary(e.target.value)}
          rows={3} placeholder="Brief summary..."
          style={{ width: '100%', marginTop: '0.3rem', background: 'var(--bg)', color: 'var(--text)', border: '1px solid var(--border-2)', borderRadius: '5px', padding: '0.4rem', fontSize: '0.83rem', fontFamily: 'var(--sans)', resize: 'vertical' }}
        />
      </label>
      <div style={{ marginTop: '0.6rem' }}>
        <button onClick={() => createMut.mutate()} disabled={!headline.trim() || createMut.isPending}>
          Save News Item
        </button>
      </div>
    </div>
  )
}

function sentimentBadge(s: string): string {
  if (s === 'bullish') return 'badge-green'
  if (s === 'bearish') return 'badge-red'
  if (s === 'mixed') return 'badge-yellow'
  return 'badge-blue'
}

function urgencyBadge(u: string): string {
  if (u === 'high') return 'badge-red'
  if (u === 'medium') return 'badge-yellow'
  return 'badge-blue'
}

function statusBadge(s: string): string {
  if (s === 'new') return 'badge-green'
  if (s === 'linked') return 'badge-blue'
  if (s === 'archived') return 'badge-yellow'
  return 'badge-blue'
}
