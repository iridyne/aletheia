type EvidenceItem = {
  label: string
  value: string
  source?: string
}

export default function EvidenceStack({ items }: { items: EvidenceItem[] }) {
  if (!items.length) return null
  return (
    <div className="evidence-stack">
      {items.map((item, i) => (
        <div key={i} className="evidence-item">
          <span className="evidence-label">{item.label}</span>
          <span className="evidence-value">{item.value}</span>
          {item.source && <span className="evidence-source">{item.source}</span>}
        </div>
      ))}
    </div>
  )
}
