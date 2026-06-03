import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export default function KnowledgeBase() {
  const { data = [] } = useQuery({ queryKey: ['knowledge'], queryFn: () => api.getKnowledge() })
  return (
    <div>
      <h2 className="page-title">Knowledge Base</h2>
      <div className="card">
        {(data as any[]).length === 0 ? <p style={{ color: 'var(--muted)' }}>No knowledge items yet.</p> : (
          <table>
            <thead><tr><th>Title</th><th>Type</th><th>Confidence</th><th>Created</th></tr></thead>
            <tbody>
              {(data as any[]).map((k: any) => (
                <tr key={k.id}><td>{k.title}</td><td>{k.item_type}</td><td>{k.confidence}</td><td>{k.created_at}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
