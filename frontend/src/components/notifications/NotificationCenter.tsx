import { useState } from 'react'
import { Bell } from 'lucide-react'
import { DetailDrawer } from '../DetailDrawer'
import { useNotifications } from './useNotifications'
import { fmtTimestamp } from '../../lib/format'

type Filter = 'all' | 'errors' | 'health'

function relTime(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60) return `${Math.round(diff)}s ago`
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`
  return fmtTimestamp(iso)
}

export function NotificationCenter() {
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState<Filter>('all')
  const { notifications, unreadCount, markAllRead, lastReadAt } = useNotifications()

  const handleOpen = () => {
    setOpen(true)
    markAllRead()
  }

  const visible = notifications.filter(n => {
    if (filter === 'errors') return n.severity === 'error'
    if (filter === 'health') return n.kind === 'health'
    return true
  })

  return (
    <>
      <button
        type="button"
        className="topbar-btn notification-bell-btn"
        onClick={handleOpen}
        aria-label={`Notifications: ${unreadCount} unread`}
        title={`${unreadCount} unread notification${unreadCount === 1 ? '' : 's'}`}
      >
        <Bell size={16} aria-hidden="true" />
        {unreadCount > 0 && (
          <span className="bell-badge mono" aria-hidden="true">{unreadCount > 9 ? '9+' : unreadCount}</span>
        )}
      </button>

      <DetailDrawer open={open} onClose={() => setOpen(false)} title="Notifications & Alerts">
        <div className="notification-filter-row">
          {(['all', 'errors', 'health'] as Filter[]).map(f => (
            <button
              key={f}
              type="button"
              className={`notification-filter-btn${filter === f ? ' active' : ''}`}
              onClick={() => setFilter(f)}
            >
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
        {visible.length === 0 ? (
          <p className="text-muted text-sm mt-3">No notifications</p>
        ) : (
          <ul className="notification-list">
            {visible.map(n => {
              const isUnread = new Date(n.created_at).getTime() > lastReadAt
              return (
                <li key={n.id} className={`notification-item ${n.severity}${isUnread ? ' unread' : ''}`}>
                  <span className={`notification-severity dot dot-${n.severity === 'error' ? 'red' : n.severity === 'warning' ? 'yellow' : 'muted'}`} aria-hidden="true" />
                  <div className="notification-body">
                    <span className="mono notification-code">{n.code}</span>
                    {n.detail && <span className="text-muted text-sm notification-detail">{n.detail}</span>}
                    <span className="text-muted text-sm notification-time">{relTime(n.created_at)}</span>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </DetailDrawer>
    </>
  )
}
