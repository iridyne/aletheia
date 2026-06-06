import { useInspector, type InspectorObject } from '../../context/InspectorContext'

type EntityCardProps = {
  type: InspectorObject['type']
  id: string
  title: string
  subtitle?: string
  status?: string
  meta?: string
  data: Record<string, any>
}

export default function EntityCard({ type, id, title, subtitle, status, meta, data }: EntityCardProps) {
  const { setObject } = useInspector()

  return (
    <div className="entity-card" onClick={() => setObject({ type, id, data })}>
      <div className="entity-card-header">
        <span className="entity-card-type">{type.replace(/_/g, ' ')}</span>
        {status && <span className={`badge ${statusClass(status)}`}>{status}</span>}
      </div>
      <div className="entity-card-title">{title}</div>
      {subtitle && <div className="entity-card-sub">{subtitle}</div>}
      {meta && <div className="entity-card-meta">{meta}</div>}
    </div>
  )
}

function statusClass(status: string): string {
  if (['completed', 'approved', 'running', 'open', 'passed'].includes(status)) return 'badge-green'
  if (['rejected', 'failed', 'blocked', 'stopped', 'error'].includes(status)) return 'badge-red'
  if (['pending', 'draft', 'warning', 'paused'].includes(status)) return 'badge-yellow'
  return 'badge-blue'
}
