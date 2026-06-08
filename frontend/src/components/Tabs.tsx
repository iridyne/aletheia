import { useState } from 'react'

/**
 * Minimal tab control (hand-rolled, no dependency). Used by the merged workbenches
 * (Agent Activity: Runs/Opportunities; Learning: Dreaming/KB/Reports). Honest by design:
 * a tab renders whatever its panel renders, including that panel's own empty/loading state.
 */
export interface TabDef {
  id: string
  label: string
  badge?: React.ReactNode
  render: () => React.ReactNode
}

export default function Tabs({ tabs, initial }: { tabs: TabDef[]; initial?: string }) {
  const [active, setActive] = useState(initial || tabs[0]?.id)
  const current = tabs.find(t => t.id === active) || tabs[0]
  return (
    <div>
      <div className="tab-bar" role="tablist">
        {tabs.map(t => (
          <button
            key={t.id}
            role="tab"
            aria-selected={t.id === active}
            className={`tab ${t.id === active ? 'tab-active' : ''}`}
            onClick={() => setActive(t.id)}
          >
            {t.label}
            {t.badge != null && <span className="tab-badge">{t.badge}</span>}
          </button>
        ))}
      </div>
      <div className="tab-panel">{current?.render()}</div>
    </div>
  )
}
