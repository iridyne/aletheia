import { useLocation } from 'react-router-dom'
import { NAV } from '../App'

/**
 * PageHeader — title + a one-line role descriptor stating what this surface is for in the
 * framework. Directly addresses the "content / transparency insufficient" pain point: every
 * page now says, in plain words, where it sits in the Observe→Decide→Execute→Evolve→Operate
 * flow and the causal chain. Optional `actions` slot for page-level controls.
 *
 * Now also renders the breadcrumb here (was previously in the topbar). The topbar holds the
 * market ticker now; navigation context lives next to the page title where it belongs.
 */
export default function PageHeader({
  title, role, actions,
}: {
  title: string
  role?: string
  actions?: React.ReactNode
}) {
  const location = useLocation()
  const groupName = NAV.find(g => g.items.some(i => i.to === location.pathname))?.group

  return (
    <div className="page-header">
      <div className="page-header-text">
        {groupName && (
          <div className="page-crumb mono">
            <span className="crumb-group">{groupName}</span>
            <span className="crumb-sep">/</span>
            <span className="crumb-current">{title}</span>
          </div>
        )}
        <h2 className="page-title">{title}</h2>
        {role && <p className="page-role">{role}</p>}
      </div>
      {actions && <div className="page-header-actions">{actions}</div>}
    </div>
  )
}
