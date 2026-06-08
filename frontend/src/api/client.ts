const BASE = '/api'

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`)
  return res.json()
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return fetchJson<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined })
}

export interface KeyStatus { configured: boolean; last4: string | null }

export const api = {
  getHealth: () => fetchJson<{ status: string; issues: string[]; version: string }>('/health'),
  // Restart the gateway worker (warm) or full process (cold). Issued from the
  // topbar restart button when the health endpoint reports code_updated_pending_restart.
  restartGateway: () => post<{ mode: 'warm' | 'cold'; ok: boolean; message: string }>('/gateway/restart'),
  getStatus: () => fetchJson<Record<string, unknown>>('/status'),
  getSettings: () => fetchJson<Record<string, unknown>>('/settings'),

  pause: () => post('/gateway/pause'),
  resume: () => post('/gateway/resume'),

  setPermissionMode: (mode: string) => post('/settings/permission-mode', { mode }),
  setExchangeMode: (mode: string) => post('/settings/exchange-mode', { mode }),

  // Model provider config
  getModelConfig: () => fetchJson<{ default_provider: string; providers: Record<string, any> }>('/model'),
  getModelHealth: () => fetchJson<{ provider: string; available: boolean; model: string; error: string | null; models: string[] }[]>('/model/health'),
  setModelProvider: (body: {
    provider: string; base_url?: string; protocol?: string; model?: string
    enabled?: boolean; default?: boolean; api_key?: string; codex_model?: string
  }) => post('/settings/model', body),
  // Verify-before-commit: test a candidate provider config (key NOT persisted)
  testModelCandidate: (body: {
    provider: string; base_url?: string; protocol?: string; model?: string; api_key?: string
  }) => post<{ available: boolean; model: string; error: string | null }>('/model/test-candidate', body),

  // Codex subscription provider: web-triggered device-auth login + model listing
  codexLoginStart: () => post<{ state: string; verification_uri: string | null; user_code: string | null; error: string | null }>('/model/codex/login-start'),
  codexLoginStatus: () => fetchJson<{ state: string; verification_uri: string | null; user_code: string | null; error: string | null }>('/model/codex/login-status'),
  codexLoginCancel: () => post('/model/codex/login-cancel'),
  getCodexModels: () => fetchJson<{ slug: string; display_name: string }[]>('/model/codex/models'),

  // Bitget credentials + connection test (write-only secrets)
  getBitgetCredStatus: () => fetchJson<{ demo: any; live: any }>('/bitget/credentials-status'),
  setExchangeCredentials: (body: {
    profile: string; api_key?: string; api_secret?: string; passphrase?: string
  }) => post('/settings/exchange-credentials', body),
  testBitget: (mode?: string) =>
    post<{ connected: boolean; mode: string; error?: string }>('/bitget/test-connection', mode ? { mode } : {}),
  // Verify-before-commit: test candidate exchange creds (NOT persisted)
  testBitgetCandidate: (body: { profile: string; api_key: string; api_secret: string; passphrase: string }) =>
    post<{ connected: boolean; profile: string; error?: string }>('/bitget/test-candidate', body),

  // Telegram bot reachability (facet A — never returns the token)
  getTelegramHealth: () => fetchJson<{ reachable: boolean; username?: string; error?: string }>('/telegram/health'),

  // Live state
  liveUnlock: () => post('/settings/live-unlock', { confirm: true }),
  liveLock: () => post('/settings/live-lock'),

  // Risk: boundaries display + kill switch + editable config
  getRiskBoundaries: () => fetchJson<Record<string, any>>('/risk/boundaries'),
  getRiskStatus: () => fetchJson<Record<string, any>>('/risk/status'),
  getKillSwitch: () => fetchJson<{ active: boolean }>('/risk/kill-switch'),
  setKillSwitch: (active: boolean, reason = '') =>
    post<{ active: boolean }>('/risk/kill-switch', { active, reason }),
  updateRiskConfig: (body: Partial<{
    max_daily_loss_pct: number; max_single_trade_risk_pct: number
    max_total_live_exposure_pct: number; max_live_position_notional_usdt: number
    max_live_limited_cap_usdt: number; starting_capital_usdt: number
    max_demo_position_notional_usdt: number; max_demo_single_trade_risk_pct: number
    max_futures_leverage_live: number; max_futures_leverage_demo: number
    max_open_positions_live: number; max_open_positions_demo: number
    allow_short_demo: boolean; allow_short_live: boolean
  }>) => post<{ updated: Record<string, any>; risk: Record<string, any> }>('/settings/risk', body),
  updateTradableSymbols: (symbols: string[]) =>
    post<{ symbols: string[] }>('/settings/symbols', { symbols }),
  updateTradingConfig: (body: Partial<{
    default_stop_loss_pct: number; default_take_profit_pct: number
    max_position_hold_hours: number; min_confidence_to_trade: string
  }>) => post<{ updated: Record<string, any>; trading: Record<string, any> }>('/settings/trading', body),

  setAutonomousLearning: (enabled: boolean) =>
    post<{ old: boolean; new: boolean }>('/settings/autonomous-learning', { enabled }),
  setExchangeMarkets: (body: { spot: boolean; usdt_futures: boolean }) =>
    post<{ enabled_markets: Record<string, boolean> }>('/settings/exchange-markets', body),
  updateRuntimeConfig: (body: Partial<{ scheduler_enabled: boolean; git_enabled: boolean }>) =>
    post<{ updated: Record<string, any> }>('/settings/runtime', body),
  updateAppConfig: (body: Partial<{ auto_open_browser: boolean }>) =>
    post<{ updated: Record<string, any> }>('/settings/app', body),
  updateHealthConfig: (body: Partial<{
    enabled: boolean; alert_via_telegram: boolean; max_agent_failure_ratio: number
    llm_unavailable_alert_after: number; market_data_max_staleness_min: number
    supervisor_max_restarts: number; supervisor_restart_window_sec: number
    supervisor_backoff_cap_sec: number
  }>) => post<{ updated: Record<string, any> }>('/settings/health-config', body),
  updateRetentionConfig: (body: Partial<{
    agent_runs_days: number; tool_calls_days: number; run_steps_days: number
    market_snapshots_days: number; portfolio_snapshots_days: number; jsonl_max_mb: number
  }>) => post<{ updated: Record<string, any> }>('/settings/retention', body),
  updateTelegramConfig: (body: Partial<{ enabled: boolean; allowed_chat_ids: number[] }>) =>
    post<{ updated: Record<string, any> }>('/settings/telegram', body),
  updateUpdateConfig: (body: Partial<{ enabled: boolean; branch: string }>) =>
    post<{ enabled: boolean; branch: string }>('/settings/update-config', body),

  // Self-upgrade (on-demand code update)
  getUpdateStatus: () => fetchJson<{
    ok: boolean; reason?: string; has_update?: boolean; behind_count?: number
    current_commit?: string; remote_commit?: string; branch?: string
  }>('/update/status'),
  applyUpdate: () => post<{
    applied: boolean; reason?: string; old_commit?: string; new_commit?: string
    deps_reinstalled?: boolean; restart_required?: boolean; message?: string
  }>('/update/apply'),

  // Autostart + watchdog (Windows Task Scheduler; on-logon start + 5-min watchdog)
  getAutostartStatus: () => fetchJson<{
    supported: boolean; enabled: boolean | null; task_name?: string
    trigger?: string; interval?: string; reason?: string; detail?: string
  }>('/gateway/autostart'),
  setAutostart: (enabled: boolean) => post<{
    supported: boolean; enabled: boolean | null; ok?: boolean
    reason?: string; detail?: string
  }>('/gateway/autostart', { enabled }),

  // Data
  getAgentRuns: () => fetchJson<any[]>('/agent-runs'),
  getAgentRunToolCalls: (runId: string) => fetchJson<any[]>(`/agent-runs/${runId}/tool-calls`),
  startAgentRun: (agent_name: string) => post('/agent-runs/start', { agent_name }),
  getDreamingSummary: () => fetchJson<Record<string, any[]>>('/dreaming/summary'),
  getOpportunities: () => fetchJson<any[]>('/opportunities'),
  getProposals: () => fetchJson<any[]>('/proposals'),
  approveProposal: (id: string) => post(`/proposals/${id}/approve-paper`),
  rejectProposal: (id: string) => post(`/proposals/${id}/reject`),
  getProposalRiskDecision: (id: string) => fetchJson<{ proposal_id: string; decision: any }>(`/proposals/${id}/risk-decision`),
  getTrades: () => fetchJson<any[]>('/trades'),
  getPositions: () => fetchJson<any[]>('/positions'),
  getPortfolio: () => fetchJson<Record<string, any>>('/portfolio'),
  getStrategies: () => fetchJson<any[]>('/strategies'),
  promoteStrategy: (id: string, target_state: string) => post(`/strategies/${id}/promote`, { target_state }),
  retireStrategy: (id: string) => post(`/strategies/${id}/retire`),
  getKnowledge: (type = 'research_note') => fetchJson<any[]>(`/knowledge?type=${encodeURIComponent(type)}`),
  getStrategyMemory: () => fetchJson<any[]>('/knowledge/strategy-memory'),
  getDreamingLogs: () => fetchJson<any[]>('/knowledge/dreaming-logs'),
  getPostTradeReviews: () => fetchJson<any[]>('/knowledge?type=post_trade_review'),
  getKnowledgeContent: (id: string) => fetchJson<{ id: string; content: string; error?: string }>(`/knowledge/${id}/content`),
  getStrategyGates: (id: string) => fetchJson<Record<string, any>>(`/strategies/${id}/gates`),

  // Charts
  // `before` (unix seconds) requests the page of candles strictly older than that
  // time — used for scroll-back history paging. 0 = latest window.
  getOhlcv: (symbol: string, timeframe = '1h', limit = 100, before = 0, exchange = '', marketType = 'spot') => {
    const params = new URLSearchParams({
      symbol, timeframe, limit: String(limit),
      ...(before > 0 ? { before: String(before) } : {}),
      ...(exchange ? { exchange } : {}),
      ...(marketType !== 'spot' ? { market_type: marketType } : {}),
    })
    return fetchJson<any>(`/market/ohlcv?${params}`)
  },
  getPortfolioHistory: () => fetchJson<any[]>('/market/portfolio-history'),

  // Audit / Activity
  getAuditEvents: (limit = 50) => fetchJson<any[]>(`/audit/events?limit=${limit}`),

  // Market snapshots (watchlist)
  getMarketSnapshots: (symbol?: string, limit = 10) =>
    fetchJson<any[]>(`/market/snapshots${symbol ? `?symbol=${encodeURIComponent(symbol)}&limit=${limit}` : `?limit=${limit}`}`),

  // News Intelligence
  getNews: (limit = 50) => fetchJson<any[]>(`/news?limit=${limit}`),
  getNewsItem: (id: string) => fetchJson<any>(`/news/${id}`),
  analyzeNews: (id: string) => post<any>(`/news/${id}/analyze`),
  createNews: (body: { title: string; source?: string; summary?: string; symbols_mentioned?: string[]; sentiment?: string }) =>
    post<any>('/news', body),

  // Command Center
  sendCommand: (input: string, context?: Record<string, string>) =>
    post<any>('/command', { input, context }),
}
