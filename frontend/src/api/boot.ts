import { fetchJson } from './client'

export type BootstrapAvailability = 'available' | 'partial' | 'unavailable'

export interface OverviewUtaAccount {
  broker_id?: string
  display_name?: string
  equity_usdt?: number | null
  available_usdt?: number | null
  unrealized_pnl?: number | null
}

export interface OverviewAccount {
  total_value_usdt?: number | null
  account_equity_usdt?: number | null
  position_notional_usdt?: number | null
  today_pnl?: number | null
  today_realized_pnl?: number | null
  today_realized_pnl_complete?: boolean
  today_realized_pnl_unavailable_count?: number
  unrealized_pnl?: number | null
  realized_pnl?: number | null
  realized_pnl_complete?: boolean
  realized_pnl_unavailable_count?: number
  all_realized_pnl?: number | null
  all_realized_pnl_complete?: boolean
  all_realized_pnl_unavailable_count?: number
  all_historical_reconciled_pnl?: number | null
  all_historical_reconciled_count?: number
  all_historical_reconciliation_unavailable_count?: number
  positions_count?: number
  exposure_by_symbol?: Record<string, number>
  today_pnl_by_symbol?: Record<string, number | null>
  unrealized_by_symbol?: Record<string, number>
  exchange_asset_breakdown?: Array<{ coin: string; usdt_value: number; pct: number }>
  exchange_equity_usdt?: number | null
  exchange_state_age_sec?: number | null
  exchange_sync_status?: string | null
  exchange_mode?: string | null
  execution_mode?: string | null
  uta_state?: {
    sync_status?: string
    errors?: string[]
    total_equity_usdt?: number | null
    total_available_usdt?: number | null
    total_unrealized_pnl?: number | null
    age_seconds?: number | null
    fetched_at?: string | null
    accounts?: OverviewUtaAccount[]
  } | null
}

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
  exchange?: { status?: string; execution_exchange?: string | null; data_exchange?: string | null; exchange_diverged?: boolean; paper_execution_mode?: string | null }
  operator?: { status?: string; mode?: string; last_seen_at?: string | null; stale?: boolean; heartbeat_age_sec?: number }
  connectivity?: {
    state?: 'reachable' | 'unavailable' | 'recovering' | 'unknown' | string
    severity?: 'ok' | 'warning' | 'info' | string
    priority?: number
    message?: string
    action?: string | null
    observed_at?: string | null
    reason?: string
  }
  health?: { status?: string; issues?: string[]; availability?: string; reason?: string | null }
  runtime?: { version?: string; boot_commit?: string | null; current_commit?: string | null; mode?: string; update?: Record<string, unknown> }
  ticker?: Record<string, Record<string, unknown>>
}

export interface OverviewBootstrap {
  schema_version: 'ui-overview-v1'
  generated_at: string
  availability?: BootstrapAvailability
  reason?: string
  account?: OverviewAccount
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
