import { useEffect, useRef, useState, type MutableRefObject } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { parseAllIssues, sortedIssues, type ParsedIssue } from './health-issues'
import type { InfraEvent } from '../types'

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

function formatEventAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime()
  const sec = Math.floor(ms / 1000)
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

function IssueList({ issues }: { issues: ParsedIssue[] }) {
  const MAX_INLINE = 3
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? issues : issues.slice(0, MAX_INLINE)
  const hidden = issues.length - MAX_INLINE
  return (
    <ul className="guard-issues">
      {visible.map(issue => (
        <li key={issue.key} className={`guard-issue guard-issue-${issue.severity}`}>
          <span className={`dot dot-${issue.severity} dot-sm`} />
          <span>{issue.label}{issue.detail ? <span className="guard-issue-detail"> — {issue.detail}</span> : null}</span>
        </li>
      ))}
      {!expanded && hidden > 0 && (
        <li className="guard-issue guard-issue-more">
          <button className="guard-more-btn" onClick={() => setExpanded(true)}>+{hidden} more</button>
        </li>
      )}
    </ul>
  )
}

function EventsSection({ events }: { events: InfraEvent[] }) {
  if (!events.length) return null
  return (
    <details className="guard-events">
      <summary className="guard-events-summary">{events.length} 条基础设施事件</summary>
      <ul className="guard-event-list">
        {events.map((e, i) => (
          <li key={i} className={`guard-event guard-event-${e.severity}`}>
            <span className="guard-event-code mono">{e.source}/{e.code}</span>
            <span className="guard-event-msg">{e.message}</span>
            <span className="guard-event-age text-muted">{formatEventAge(e.created_at)}</span>
          </li>
        ))}
      </ul>
    </details>
  )
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
      notifyBrowser('aletheia-backend-restored', 'Aletheia backend restored',
        'Dashboard API connection is healthy again.', lastNotified)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataUpdatedAt])

  useEffect(() => {
    if (!errorUpdatedAt) return
    setFailureCount(n => n + 1)
    setOfflineSince(ts => ts ?? Date.now())
  }, [errorUpdatedAt])

  const disconnected = failureCount > 0
  const offlineForMs = offlineSince ? Date.now() - offlineSince : 0
  const showOverlay = disconnected && offlineForMs >= OVERLAY_AFTER_MS

  // Parse all health issues
  const allIssues = sortedIssues(parseAllIssues(data?.health_issues ?? []))
  const hasSchedulerActual = typeof data?.scheduler_running === 'boolean'
  const schedulerBroken = hasSchedulerActual && !!data?.scheduler_enabled && !data?.scheduler_running
  const infraEvents = data?.infra_events ?? []

  // Add scheduler as a synthetic issue if not already captured
  if (schedulerBroken && !allIssues.some(i => i.key === 'scheduler_broken')) {
    allIssues.unshift({
      key: 'scheduler_broken',
      severity: 'red',
      label: 'Scheduler 未运行',
      detail: `configured=enabled, actual=${data?.scheduler_state || 'unknown'}`,
    })
  }

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

  const requestNotifications = async () => {
    if (browserPermission() !== 'default') return
    try {
      const next = await Notification.requestPermission()
      setPermission(next)
    } catch {
      setPermission(browserPermission())
    }
  }

  // Disconnected path (original behavior)
  if (disconnected) {
    return (
      <>
        <div className="backend-guard backend-guard-red" role="alert" aria-live="assertive">
          <div className="backend-guard-main">
            <span className="dot dot-red" />
            <div>
              <strong>Backend 连接中断</strong>
              <p>无法访问后端 API。上次成功响应：{formatAge(lastOkAt)}。{isFetching ? '正在重连…' : '每 5s 重试。'}</p>
              {error && <p className="backend-guard-detail">{String((error as Error).message || error)}</p>}
            </div>
          </div>
          <div className="backend-guard-actions">
            {permission === 'default' && (
              <button className="topbar-btn secondary" onClick={requestNotifications}>开启浏览器通知</button>
            )}
            {permission === 'granted' && <span className="badge badge-blue">浏览器通知已开启</span>}
          </div>
        </div>
        {showOverlay && (
          <div className="backend-guard-overlay" role="alertdialog" aria-live="assertive">
            <div className="backend-guard-panel">
              <span className="dot dot-red dot-lg" />
              <h2>Backend 不可达</h2>
              <p>Dashboard 已断开 {Math.floor(offlineForMs / 1000)}s。</p>
              <p>保持窗口打开，Windows 看门狗恢复后将自动重连。</p>
              <p className="backend-guard-detail">若未恢复，运行：<code>uv run aletheia doctor --fix</code></p>
            </div>
          </div>
        )}
      </>
    )
  }

  // Connected-but-degraded path: show all issues + infra events
  const hasIssues = allIssues.length > 0
  const hasEvents = infraEvents.length > 0
  if (!hasIssues && !hasEvents) return null

  const tone = allIssues.some(i => i.severity === 'red') ? 'red' : 'yellow'
  const headline = allIssues.length > 0 ? allIssues[0].label : '基础设施事件'

  return (
    <div className={`backend-guard backend-guard-${tone}`} role="alert" aria-live="assertive">
      <div className="backend-guard-main">
        <span className={`dot dot-${tone}`} />
        <div className="backend-guard-body">
          <strong>{headline}</strong>
          {allIssues.length > 1 && <IssueList issues={allIssues} />}
          {hasEvents && <EventsSection events={infraEvents as InfraEvent[]} />}
        </div>
      </div>
      <div className="backend-guard-actions">
        {permission === 'default' && (
          <button className="topbar-btn secondary" onClick={requestNotifications}>开启浏览器通知</button>
        )}
        {permission === 'granted' && <span className="badge badge-blue">浏览器通知已开启</span>}
      </div>
    </div>
  )
}
