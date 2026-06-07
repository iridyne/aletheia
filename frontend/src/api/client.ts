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
  setAutonomousPaperTrading: (enabled: boolean) => post('/settings/autonomous-paper-trading', { enabled }),
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
  getOhlcv: (symbol: string, timeframe = '1h', limit = 100) =>
    fetchJson<any>(`/market/ohlcv?symbol=${symbol}&timeframe=${timeframe}&limit=${limit}`),
  getPortfolioHistory: () => fetchJson<any[]>('/market/portfolio-history'),

  // Audit / Activity
  getAuditEvents: (limit = 50) => fetchJson<any[]>(`/audit/events?limit=${limit}`),

  // Market snapshots (watchlist)
  getMarketSnapshots: (symbol?: string, limit = 10) =>
    fetchJson<any[]>(`/market/snapshots${symbol ? `?symbol=${symbol}&limit=${limit}` : `?limit=${limit}`}`),

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
