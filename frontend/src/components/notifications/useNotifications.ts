import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import { StorageService } from '../../core/storage/storageService'

const LAST_READ_KEY = 'aletheia.notifications.lastReadAt'

export interface Notification {
  id: string
  kind: 'infra' | 'health'
  severity: 'error' | 'warning' | 'info'
  code: string
  detail?: string
  created_at: string
}

function healthSeverity(issue: string): 'error' | 'warning' | 'info' {
  if (issue.includes('unavailable') || issue.includes('error') || issue.includes('critical')) return 'error'
  if (issue.includes('warn') || issue.includes('stale') || issue.includes('partial')) return 'warning'
  return 'info'
}

export function useNotifications() {
  const [lastReadAt, setLastReadAt] = useState<number>(() => {
    const stored = StorageService.getString(LAST_READ_KEY)
    return stored ? new Date(stored).getTime() : 0
  })

  const infraQuery = useQuery({
    queryKey: ['infra-events'],
    queryFn: () => api.getInfraEvents(),
    refetchInterval: 15_000,
    meta: { background: true },
  })

  const healthQuery = useQuery({
    queryKey: ['health'],
    queryFn: () => api.getHealth(),
    refetchInterval: 10_000,
    meta: { background: true },
  })

  const notifications = useMemo<Notification[]>(() => {
    const items: Notification[] = []

    const infraEvents = ((infraQuery.data as { events?: unknown[] } | undefined)?.events ?? []) as Array<{ id?: string; created_at?: string; kind?: string; code?: string; detail?: string }>
    for (const e of infraEvents) {
      items.push({
        id: e.id ?? e.created_at ?? String(Math.random()),
        kind: 'infra',
        severity: (e.kind === 'error' || String(e.code).includes('unavailable')) ? 'error' : 'info',
        code: e.code ?? 'infra_event',
        detail: e.detail,
        created_at: e.created_at ?? new Date().toISOString(),
      })
    }

    const issues: string[] = (healthQuery.data?.issues ?? []).filter(
      (i: string) => !i.startsWith('code_updated_pending_restart:'),
    )
    for (const issue of issues) {
      items.push({
        id: `health-${issue}`,
        kind: 'health',
        severity: healthSeverity(issue),
        code: issue,
        created_at: new Date().toISOString(),
      })
    }

    return items.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
  }, [infraQuery.data, healthQuery.data])

  const unreadCount = useMemo(
    () => notifications.filter(n => new Date(n.created_at).getTime() > lastReadAt).length,
    [notifications, lastReadAt],
  )

  const markAllRead = () => {
    const now = new Date().toISOString()
    StorageService.setString(LAST_READ_KEY, now)
    setLastReadAt(Date.now())
  }

  return { notifications, unreadCount, markAllRead, lastReadAt }
}
