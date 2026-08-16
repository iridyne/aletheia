import { useEffect, useRef, useState, type MutableRefObject } from 'react'
import { useQuery } from '@tanstack/react-query'
import { bootApi as api } from '../api/boot'

const POLL_MS = 10000
const FAILURES_BEFORE_ALERT = 3

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
  const [failureCount, setFailureCount] = useState(0)
  const [probeEnabled, setProbeEnabled] = useState(false)

  useEffect(() => {
    const timer = window.setTimeout(() => setProbeEnabled(true), 1000)
    return () => window.clearTimeout(timer)
  }, [])

  const { data, dataUpdatedAt, errorUpdatedAt } = useQuery({
    queryKey: ['backend-ready'],
    queryFn: api.getReady,
    enabled: probeEnabled,
    refetchInterval: POLL_MS,
    retry: 1,
    staleTime: 0,
    meta: { background: true },
  })

  const disconnected = failureCount >= FAILURES_BEFORE_ALERT && !!errorUpdatedAt && (!dataUpdatedAt || errorUpdatedAt > dataUpdatedAt)

  useEffect(() => {
    if (!dataUpdatedAt) return
    setFailureCount(0)
    if (wasOffline.current) {
      notifyBrowser('aletheia-backend-restored', 'Aletheia backend restored',
        'Dashboard API connection is healthy again.', lastNotified)
    }
    wasOffline.current = false
  }, [dataUpdatedAt])

  useEffect(() => {
    if (!errorUpdatedAt) return
    setFailureCount((count) => count + 1)
    wasOffline.current = true
  }, [errorUpdatedAt])

  useEffect(() => {
    if (disconnected) {
      notifyBrowser('aletheia-backend-disconnected', 'Aletheia backend unreachable',
        'The dashboard cannot reach /api/ready. Check gateway status or start it explicitly.', lastNotified)
    }
  }, [disconnected])

  useEffect(() => {
    if (browserPermission() === 'default') {
      Notification.requestPermission().catch(() => {})
    }
  }, [])

  return null
}
