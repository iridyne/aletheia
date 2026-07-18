import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'

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
const CHANGE_MAP: Record<string, readonly unknown[][]> = {
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
  positions: [
    ['positions'],
    ['portfolio'],
    ['equity-curve'],
  ],
  fills: [
    ['positions'],
    ['portfolio'],
    ['equity-curve'],
  ],
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

/**
 * Opens a WebSocket to /api/ws and invalidates React Query caches whenever
 * the backend pushes a change notification.  Auto-reconnects with exponential
 * backoff (3 s → 30 s).  Existing refetchIntervals continue to work as
 * fallback when the WebSocket is unavailable.
 *
 * Must be called inside a component that is a descendant of QueryClientProvider.
 */
export function useRealtimeUpdates(): void {
  const queryClient = useQueryClient()

  useEffect(() => {
    let retryDelay = WS_RETRY_BASE_MS
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    let ws: WebSocket | null = null
    let dead = false

    function connect() {
      if (dead) return
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      ws = new WebSocket(`${proto}//${window.location.host}/api/ws`)

      ws.onopen = () => {
        retryDelay = WS_RETRY_BASE_MS  // reset backoff on success
      }

      ws.onmessage = (e: MessageEvent) => {
        try {
          const msg = JSON.parse(e.data as string) as { type: string; changed: string[] }
          if ((msg.type !== 'update' && msg.type !== 'alert') || !Array.isArray(msg.changed)) return
          for (const key of msg.changed) {
            for (const queryKey of (CHANGE_MAP[key] ?? [])) {
              queryClient.invalidateQueries({ queryKey: queryKey as unknown[] })
            }
          }
        } catch {
          // ignore malformed frames
        }
      }

      ws.onclose = () => {
        ws = null
        if (!dead) {
          retryTimer = setTimeout(() => {
            retryDelay = Math.min(retryDelay * 1.5, WS_RETRY_MAX_MS)
            connect()
          }, retryDelay)
        }
      }

      ws.onerror = () => ws?.close()
    }

    connect()

    return () => {
      dead = true
      if (retryTimer) clearTimeout(retryTimer)
      ws?.close()
    }
  }, [queryClient])
}
