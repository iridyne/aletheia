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
  getHealth: () => fetchJson<{ status: string; version: string }>('/health'),
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

  // Live state
  liveUnlock: () => post('/settings/live-unlock', { confirm: true }),
  liveLock: () => post('/settings/live-lock'),

  // Risk: boundaries display + kill switch
  getRiskBoundaries: () => fetchJson<Record<string, any>>('/risk/boundaries'),
  getRiskStatus: () => fetchJson<Record<string, any>>('/risk/status'),
  getKillSwitch: () => fetchJson<{ active: boolean }>('/risk/kill-switch'),
  setKillSwitch: (active: boolean, reason = '') =>
    post<{ active: boolean }>('/risk/kill-switch', { active, reason }),

  // Self-upgrade (on-demand code update)
  getUpdateStatus: () => fetchJson<{
    ok: boolean; reason?: string; has_update?: boolean; behind_count?: number
    current_commit?: string; remote_commit?: string; branch?: string
  }>('/update/status'),
  applyUpdate: () => post<{
    applied: boolean; reason?: string; old_commit?: string; new_commit?: string
    deps_reinstalled?: boolean; restart_required?: boolean; message?: string
  }>('/update/apply'),

  // Data
  getAgentRuns: () => fetchJson<any[]>('/agent-runs'),
  startAgentRun: (agent_name: string) => post('/agent-runs/start', { agent_name }),
  getOpportunities: () => fetchJson<any[]>('/opportunities'),
  getProposals: () => fetchJson<any[]>('/proposals'),
  approveProposal: (id: string) => post(`/proposals/${id}/approve-paper`),
  rejectProposal: (id: string) => post(`/proposals/${id}/reject`),
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

  // Charts
  getOhlcv: (symbol: string, timeframe = '1h', limit = 100) =>
    fetchJson<any>(`/market/ohlcv?symbol=${symbol}&timeframe=${timeframe}&limit=${limit}`),
  getPortfolioHistory: () => fetchJson<any[]>('/market/portfolio-history'),
}
