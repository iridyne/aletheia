import { useEffect, useRef, type MutableRefObject } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { parseAllIssues, sortedIssues } from './health-issues'

const POLL_MS = 5000

function browserPermission(): NotificationPermission | 'unsupported' {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported'
  return Notification.permission
}

function notifyBrowser(key: string, title: string, body: string, lastKeyRef: MutableRefObject<string>) {
  if (browserPermission() !== 'granted' || lastKeyRef.current === key) return
  lastKeyRef.current = key
  try {
    new Notification(title, { body, tag: key, silent: false })
  } catch { /* advisory only */ }
}

export default function BackendConnectionGuard() {
  const lastNotified = useRef('')
  const wasOffline = useRef(false)

  const { data, dataUpdatedAt, errorUpdatedAt } = useQuery({
    queryKey: ['backend-connection'],
    queryFn: api.getStatus,
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: true,
    retry: 0,
    staleTime: 0,
  })

  const disconnected = !!errorUpdatedAt && (!dataUpdatedAt || errorUpdatedAt > dataUpdatedAt)

  useEffect(() => {
    if (!dataUpdatedAt) return
    if (wasOffline.current) {
      notifyBrowser('aletheia-backend-restored', 'Aletheia backend restored',
        'Dashboard API connection is healthy again.', lastNotified)
    }
    wasOffline.current = false
  }, [dataUpdatedAt])

  useEffect(() => {
    if (!errorUpdatedAt) return
    wasOffline.current = true
  }, [errorUpdatedAt])

  const allIssues = sortedIssues(parseAllIssues(data?.health_issues ?? []))
  const hasSchedulerActual = typeof data?.scheduler_running === 'boolean'
  const schedulerBroken = hasSchedulerActual && !!data?.scheduler_enabled && !data?.scheduler_running

  useEffect(() => {
    if (disconnected) {
      notifyBrowser('aletheia-backend-disconnected', 'Aletheia backend unreachable',
        'The dashboard cannot reach /api/status. The watchdog may be restarting the gateway.', lastNotified)
    } else if (schedulerBroken) {
      notifyBrowser('aletheia-scheduler-not-running', 'Aletheia scheduler is not active',
        `Desired scheduler state is enabled, actual state is ${data?.scheduler_state || 'unknown'}.`, lastNotified)
    } else if (allIssues.some(i => i.key.startsWith('code_updated_pending_restart:'))) {
      const drift = allIssues.find(i => i.key.startsWith('code_updated_pending_restart:'))!
      notifyBrowser('aletheia-code-drift', 'Aletheia restart needed', `Code drift: ${drift.detail}`, lastNotified)
    }
  }, [disconnected, schedulerBroken, allIssues.map(i => i.key).join(',')])

  useEffect(() => {
    if (browserPermission() === 'default') {
      Notification.requestPermission().catch(() => {})
    }
  }, [])

  return null
}
