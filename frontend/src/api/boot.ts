import { fetchJson } from './client'

export type BootstrapAvailability = 'available' | 'unavailable'

export interface ShellBootstrap {
  schema_version: 'ui-shell-v1'
  generated_at: string
  freshness?: { state: string; max_age_sec: number }
  availability?: BootstrapAvailability
  reason?: string
  gateway?: { running?: boolean; paused?: boolean }
  scheduler?: { running?: boolean; state?: string; enabled?: boolean }
  live?: { state?: string; enabled?: boolean }
  permission?: { mode?: string }
  kill_switch?: { active?: boolean }
  risk?: { status?: string; violations?: string[]; kill_switch_active?: boolean }
  operator?: { status?: string; mode?: string; last_seen_at?: string | null; stale?: boolean; heartbeat_age_sec?: number }
  connectivity?: { state?: string; reason?: string }
  health?: { status?: string; issues?: string[]; availability?: string; reason?: string | null }
  runtime?: { version?: string; boot_commit?: string | null; current_commit?: string | null; mode?: string; update?: Record<string, unknown> }
  ticker?: Record<string, Record<string, unknown>>
}

export interface OverviewBootstrap {
  schema_version: 'ui-overview-v1'
  generated_at: string
  availability?: BootstrapAvailability
  reason?: string
  account?: Record<string, unknown>
  summary?: { pending_proposals?: number; open_opportunities?: number; runs_24h?: number }
  activity?: Array<Record<string, unknown>>
  setup_required?: boolean
  coverage?: Record<string, unknown>
  errors?: Record<string, string>
}

export const bootApi = {
  getShellBootstrap: ({ signal }: { signal?: AbortSignal } = {}) =>
    fetchJson<ShellBootstrap>('/ui/shell-bootstrap', { signal }),
  getOverviewBootstrap: ({ signal }: { signal?: AbortSignal } = {}) =>
    fetchJson<OverviewBootstrap>('/ui/overview-bootstrap', { signal }),
}
