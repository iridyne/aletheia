import { useState } from 'react'

/**
 * Minimal tab control (hand-rolled, no dependency). Used by every multi-tab page
 * (Agent Activity / Learning / Trades / Charts / Prediction Markets) — so the
 * tab look, ARIA, and badge behavior are uniform across the dashboard.
 *
 * Two modes:
 *   - **Uncontrolled** (default): pass `tabs` + optional `initial`; the component
 *     owns the active state.
 *   - **Controlled**: pass `value` + `onChange`; the parent owns the state.
 *     Use this when an external event (search, deep-link, sibling click) must
 *     switch tabs — e.g. Charts switches to the Chart tab when a watchlist row
 *     is clicked.
 *
 * Honest by design: a tab renders whatever its panel renders, including that
 * panel's own empty/loading state.
 */
export interface TabDef {
  id: string
  label: React.ReactNode
  badge?: React.ReactNode
  render: () => React.ReactNode
}

export interface TabsProps {
  tabs: TabDef[]
  /** Uncontrolled-mode initial tab id. Ignored if `value` is provided. */
  initial?: string
  /** Controlled-mode current tab id. Pair with `onChange`. */
  value?: string
  /** Controlled-mode change handler. Pair with `value`. */
  onChange?: (id: string) => void
}

export default function Tabs({ tabs, initial, value, onChange }: TabsProps) {
  const [internal, setInternal] = useState(initial || tabs[0]?.id)
  const isControlled = value !== undefined
  const active = isControlled ? value : internal
  const setActive = (id: string) => {
    if (!isControlled) setInternal(id)
    onChange?.(id)
  }
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
