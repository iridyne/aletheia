import { useEffect, useRef, useState, type MutableRefObject } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

const POLL_MS = 5000
const OVERLAY_AFTER_MS = 30000

function browserPermission(): NotificationPermission | 'unsupported' {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported'
  return Notification.permission
}

function formatAge(ts: number | null): string {
  if (!ts) return 'never'
  const sec = Math.max(0, Math.floor((Date.now() - ts) / 1000))
  if (sec < 60) return `${sec}s ago`
  const min = Math.floor(sec / 60)
  return `${min}m ago`
}

function notifyBrowser(key: string, title: string, body: string, lastKeyRef: MutableRefObject<string>) {
  if (browserPermission() !== 'granted' || lastKeyRef.current === key) return
  lastKeyRef.current = key
  try {
    new Notification(title, { body, tag: key, silent: false })
  } catch { /* advisory only */ }
}

export default function BackendConnectionGuard() {
  const [failureCount, setFailureCount] = useState(0)
  const [lastOkAt, setLastOkAt] = useState<number | null>(null)
  const [offlineSince, setOfflineSince] = useState<number | null>(null)
  const [permission, setPermission] = useState(browserPermission())
  const lastNotified = useRef('')

  const { data, error, dataUpdatedAt, errorUpdatedAt, isFetching } = useQuery({
    queryKey: ['backend-connection'],
    queryFn: api.getStatus,
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: true,
    retry: 0,
    staleTime: 0,
  })

  useEffect(() => {
    if (!dataUpdatedAt) return
    const wasOffline = !!offlineSince
    const now = Date.now()
    setFailureCount(0)
    setLastOkAt(now)
    setOfflineSince(null)
    if (wasOffline) {
      notifyBrowser(
        'aletheia-backend-restored',
        'Aletheia backend restored',
        'Dashboard API connection is healthy again.',
        lastNotified,
      )
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataUpdatedAt])

  useEffect(() => {
    if (!errorUpdatedAt) return
    setFailureCount(n => n + 1)
    setOfflineSince(ts => ts ?? Date.now())
  }, [errorUpdatedAt])

  const driftIssue = data?.health_issues?.find(i => i.startsWith('code_updated_pending_restart:')) || ''
  const schedulerBroken = !!data?.scheduler_enabled && !data?.scheduler_running
  const disconnected = failureCount > 0
  const offlineForMs = offlineSince ? Date.now() - offlineSince : 0
  const showOverlay = disconnected && offlineForMs >= OVERLAY_AFTER_MS

  useEffect(() => {
    if (disconnected) {
      notifyBrowser(
        'aletheia-backend-disconnected',
        'Aletheia backend unreachable',
        'The dashboard cannot reach /api/status. The watchdog may be restarting the gateway.',
        lastNotified,
      )
    } else if (driftIssue) {
      notifyBrowser(
        'aletheia-code-drift',
        'Aletheia restart needed',
        driftIssue.replace('code_updated_pending_restart:', 'Code drift: '),
        lastNotified,
      )
    } else if (schedulerBroken) {
      notifyBrowser(
        'aletheia-scheduler-not-running',
        'Aletheia scheduler is not active',
        `Desired scheduler state is enabled, actual state is ${data?.scheduler_state || 'unknown'}.`,
        lastNotified,
      )
    }
  }, [disconnected, driftIssue, schedulerBroken, data?.scheduler_state])

  const requestNotifications = async () => {
    if (browserPermission() !== 'default') return
    try {
      const next = await Notification.requestPermission()
      setPermission(next)
    } catch {
      setPermission(browserPermission())
    }
  }

  const healthError = data?.health_issues?.find(i => i.startsWith('health_check_error:')) || ''
  const visible = disconnected || driftIssue || schedulerBroken || healthError
  if (!visible) return null

  const tone = disconnected || schedulerBroken || healthError ? 'red' : 'yellow'
  const title = disconnected
    ? 'Backend connection lost'
    : schedulerBroken
      ? 'Scheduler is not active'
      : healthError
        ? 'Health check failed'
        : 'Code update pending restart'
  const message = disconnected
    ? `Cannot reach the backend API. Last successful response: ${formatAge(lastOkAt)}. ${isFetching ? 'Retrying now…' : 'Retrying every 5s.'}`
    : schedulerBroken
      ? `Configured scheduler_enabled=true, but actual scheduler_state=${data?.scheduler_state || 'unknown'}.`
      : healthError
        ? healthError
        : driftIssue.replace('code_updated_pending_restart:', 'Gateway boot commit differs from disk HEAD: ')

  return (
    <>
      <div className={`backend-guard backend-guard-${tone}`} role="alert" aria-live="assertive">
        <div className="backend-guard-main">
          <span className={`dot dot-${tone}`} />
          <div>
            <strong>{title}</strong>
            <p>{message}</p>
            {error && disconnected && <p className="backend-guard-detail">{String((error as Error).message || error)}</p>}
          </div>
        </div>
        <div className="backend-guard-actions">
          {permission === 'default' && (
            <button className="topbar-btn secondary" onClick={requestNotifications}>Enable browser alerts</button>
          )}
          {permission === 'granted' && <span className="badge badge-blue">browser alerts on</span>}
        </div>
      </div>
      {showOverlay && (
        <div className="backend-guard-overlay" role="alertdialog" aria-live="assertive">
          <div className="backend-guard-panel">
            <span className="dot dot-red dot-lg" />
            <h2>Backend unreachable</h2>
            <p>The dashboard has been disconnected for {Math.floor(offlineForMs / 1000)}s.</p>
            <p>Keep this window open; it will reconnect automatically if the Windows watchdog restores the gateway.</p>
            <p className="backend-guard-detail">If it does not recover, run: <code>uv run aletheia doctor --fix</code></p>
          </div>
        </div>
      )}
    </>
  )
}
