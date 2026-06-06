import { NavLink, useLocation } from 'react-router-dom'
import { useState } from 'react'

type NavItem = { to: string; label: string; icon?: string }
type NavGroup = { group: string; items: NavItem[] }

const NAV: NavGroup[] = [
  {
    group: 'Operate',
    items: [
      { to: '/', label: 'Cockpit' },
      { to: '/command', label: 'Command Center' },
      { to: '/risk', label: 'Risk & Controls' },
    ],
  },
  {
    group: 'Research',
    items: [
      { to: '/charts', label: 'Markets' },
      { to: '/news', label: 'News Intelligence' },
      { to: '/opportunities', label: 'Opportunities' },
      { to: '/dreaming', label: 'Dreaming / Knowledge' },
    ],
  },
  {
    group: 'Trading',
    items: [
      { to: '/proposals', label: 'Proposals' },
      { to: '/portfolio', label: 'Portfolio' },
      { to: '/strategies', label: 'Strategy Lifecycle' },
    ],
  },
  {
    group: 'System',
    items: [
      { to: '/reports', label: 'Reports' },
      { to: '/settings', label: 'Settings' },
    ],
  },
]

export default function LeftNav() {
  const location = useLocation()
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const toggle = (g: string) => setCollapsed(c => ({ ...c, [g]: !c[g] }))

  return (
    <aside className="left-nav">
      <div className="brand">
        <span className="brand-mark">◆</span>
        <span className="brand-name">ALETHEIA</span>
        <span className="brand-sub">trading os</span>
      </div>
      <nav>
        {NAV.map(({ group, items }) => (
          <div key={group} className="nav-group">
            <button className="nav-group-label" onClick={() => toggle(group)}>
              <span>{group}</span>
              <span className={`chev ${collapsed[group] ? 'chev-closed' : ''}`}>▾</span>
            </button>
            {!collapsed[group] && items.map(n => (
              <NavLink key={n.to} to={n.to} end={n.to === '/'}>
                {n.label}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>
    </aside>
  )
}

export { NAV }
