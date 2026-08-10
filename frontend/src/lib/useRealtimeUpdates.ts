import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { positionKeys } from './queryKeys'

/**
 * Maps backend data-category keys (pushed via /api/ws) to React Query cache
 * key prefixes that should be invalidated.  Partial key matching is used so
 * parametric keys like ['ticker-snap', 'BTCUSDT'] are caught by ['ticker-snap'].
 *
 * Two message types arrive from the backend:
 *   "update" — regular 500 ms DB fingerprint changes
 *   "alert"  — highest-priority 100 ms push for infra errors + collector failures
 * Both are handled identically here: look up CHANGE_MAP and invalidate.
 *
 * NOT covered by WS (in-memory or external-API only — left on REST polling):
 *   status, health, kill-switch, model-health, uta/state,
 *   exchange connectivity tests, OAuth flows (codex-login-status, openai-oauth-status)
 */
type QueryKeyPrefix = readonly unknown[]

const POSITION_CHANGE_MAP = {
  positions: [positionKeys.all, ['portfolio'], ['equity-curve']],
  fills: [positionKeys.all, ['portfolio'], ['equity-curve']],
} satisfies Record<'positions' | 'fills', readonly QueryKeyPrefix[]>

const CHANGE_MAP: Record<string, readonly QueryKeyPrefix[]> = {
  // ── Alert tier (100 ms — highest priority) ───────────────────────────────
  infra_events: [
    ['infra-events'],
  ],
  collector_error: [
    ['collector-health'],
    ['market-data-coverage'],
  ],
  // ── Core trading ──────────────────────────────────────────────────────────
  agent_runs: [
    ['agent-runs'],
  ],
  opportunities: [
    ['opportunities'],
    ['opportunity-funnel'],
    // Blotter's Candidates panel + status token strip + Overview summary
    // strip all read the fast /current-snapshot endpoint. Without this,
    // WS-pushed opportunity changes would only reach them via the 30 s
    // refetch poll (2026-07-18).
    ['opportunity-funnel-snapshot'],
    ['pipeline-funnel-24h'],
  ],
  proposals: [
    ['proposals'],
  ],
  pi_decisions: [
    ['operator-decisions'],
    ['pipeline-funnel-24h'],
  ],
  ...POSITION_CHANGE_MAP,
  risk_decisions: [
    ['risk-status'],
    ['pipeline-funnel-24h'],
  ],

  // ── Market data ───────────────────────────────────────────────────────────
  market_snapshots: [
    ['ticker-snap'],        // matches all ['ticker-snap', sym] via prefix
    ['market-snapshots'],
    ['sparkline'],          // matches all ['sparkline', sym] via prefix
  ],
  collector_health: [
    ['collector-health'],
    ['market-data-coverage'],
  ],
  macro_snaps: [
    ['macro'],
  ],

  // ── Portfolio ─────────────────────────────────────────────────────────────
  portfolio_snaps: [
    ['portfolio'],
    ['account-equity-history'],  // matches all ['account-equity-history', days]
  ],

  // ── Operator / runtime ────────────────────────────────────────────────────
  heartbeats: [
    ['operator-status'],
  ],
  runtime_events: [
    ['operator-runtime'],
    ['operator-runtime-events'],
  ],

  // ── Research / signals ────────────────────────────────────────────────────
  serenity_runs: [
    ['serenity-status'],
    ['serenity-runs'],        // matches all ['serenity-runs', limit]
    ['serenity-run-detail'],  // matches all ['serenity-run-detail', runId]
  ],
  news_items: [
    ['news'],
  ],
  prediction_runs: [
    ['prediction-status'],
    ['prediction-markets'],
  ],

  // ── Universe ──────────────────────────────────────────────────────────────
  universe_snaps: [
    ['universe-current'],
    ['universe-calibration'],
  ],
}

const WS_RETRY_BASE_MS = 3_000
const WS_RETRY_MAX_MS = 30_000
const DIAGNOSTIC_COUNT_MAX = 999
const MAX_FRAME_BYTES = 64 * 1024
const MAX_CHANGED_CATEGORIES = 256

export interface RealtimeFrame {
  type: 'update' | 'alert'
  changed: string[]
}

export function parseRealtimeFrame(raw: unknown): RealtimeFrame | null {
  if (typeof raw !== 'string' || raw.length > MAX_FRAME_BYTES) return null
  try {
    const message = JSON.parse(raw) as { type?: unknown; changed?: unknown } | null
    if (!message
      || (message.type !== 'update' && message.type !== 'alert')
      || !Array.isArray(message.changed)
      || message.changed.length > MAX_CHANGED_CATEGORIES
      || message.changed.some(key => typeof key !== 'string')) return null
    return { type: message.type, changed: message.changed }
  } catch {
    return null
  }
}

export function queryKeysForChanged(changed: readonly string[]): unknown[][] {
  const unique = new Map<string, unknown[]>()
  for (const category of new Set(changed)) {
    for (const queryKey of CHANGE_MAP[category] ?? []) {
      const key = [...queryKey]
      unique.set(JSON.stringify(key), key)
    }
  }
  return [...unique.values()]
}

export type RealtimeTransportState = 'connecting' | 'connected' | 'reconnecting' | 'polling_only' | 'disconnected'

export interface RealtimeStatus {
  state: RealtimeTransportState
  lastFrameAt: number | null
  malformedFrameCount: number
  unknownCategoryCount: number
}

/**
 * Opens a WebSocket to /api/ws and invalidates React Query caches whenever
 * the backend pushes a change notification.  Auto-reconnects with exponential
 * backoff (3 s → 30 s).  Existing refetchIntervals continue to work as
 * fallback when the WebSocket is unavailable.
 *
 * Must be called inside a component that is a descendant of QueryClientProvider.
 */
export function useRealtimeUpdates(): RealtimeStatus {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<RealtimeStatus>({
    state: 'connecting',
    lastFrameAt: null,
    malformedFrameCount: 0,
    unknownCategoryCount: 0,
  })

  useEffect(() => {
    let retryDelay = WS_RETRY_BASE_MS
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    let ws: WebSocket | null = null
    let dead = false

    const updateStatus = (update: Partial<RealtimeStatus> | ((current: RealtimeStatus) => RealtimeStatus)) => {
      if (!dead) setStatus(current => typeof update === 'function' ? update(current) : { ...current, ...update })
    }
    const boundedAdd = (count: number, increment = 1) => Math.min(count + increment, DIAGNOSTIC_COUNT_MAX)

    function scheduleReconnect() {
      if (dead || document.visibilityState === 'hidden') return
      updateStatus({ state: 'polling_only' })
      retryTimer = setTimeout(() => {
        retryTimer = null
        retryDelay = Math.min(retryDelay * 1.5, WS_RETRY_MAX_MS)
        connect(true)
      }, retryDelay)
    }

    function connect(reconnecting = false) {
      if (dead || document.visibilityState === 'hidden' || ws) return
      updateStatus({ state: reconnecting ? 'reconnecting' : 'connecting' })
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const socket = new WebSocket(`${proto}//${window.location.host}/api/ws`)
      ws = socket

      socket.onopen = () => {
        retryDelay = WS_RETRY_BASE_MS  // reset backoff on success
        updateStatus({ state: 'connected' })
      }

      socket.onmessage = (e: MessageEvent) => {
        const msg = parseRealtimeFrame(e.data)
        if (!msg) {
          updateStatus(current => ({
            ...current,
            malformedFrameCount: boundedAdd(current.malformedFrameCount),
          }))
          return
        }
        const unknownCount = msg.changed.filter(key => !(key in CHANGE_MAP)).length
        updateStatus(current => ({
          ...current,
          lastFrameAt: Date.now(),
          unknownCategoryCount: boundedAdd(current.unknownCategoryCount, unknownCount),
        }))
        for (const queryKey of queryKeysForChanged(msg.changed)) {
          queryClient.invalidateQueries({ queryKey })
        }
      }

      socket.onclose = () => {
        // A hidden→visible transition may already have installed a replacement.
        if (ws !== socket) return
        ws = null
        scheduleReconnect()
      }

      socket.onerror = () => socket.close()
    }

    function handleVisibilityChange() {
      if (document.visibilityState === 'hidden') {
        if (retryTimer) {
          clearTimeout(retryTimer)
          retryTimer = null
        }
        ws?.close()
        ws = null
        updateStatus({ state: 'disconnected' })
        return
      }
      retryDelay = WS_RETRY_BASE_MS
      connect()
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    connect()

    return () => {
      dead = true
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      if (retryTimer) clearTimeout(retryTimer)
      ws?.close()
    }
  }, [queryClient])

  return status
}
