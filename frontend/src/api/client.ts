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
  getModelHealth: () => fetchJson<{ provider: string; available: boolean; model: string; error: string | null }[]>('/model/health'),
  setModelProvider: (body: {
    provider: string; base_url?: string; protocol?: string; model?: string
    enabled?: boolean; default?: boolean; api_key?: string
  }) => post('/settings/model', body),

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
  getKnowledge: (type?: string) => fetchJson<any[]>(`/knowledge/research-notes${type ? `?type=${type}` : ''}`),
  getStrategyMemory: () => fetchJson<any[]>('/knowledge/strategy-memory'),
  getDreamingLogs: () => fetchJson<any[]>('/knowledge/dreaming-logs'),

  // Charts
  getOhlcv: (symbol: string, timeframe = '1h', limit = 100) =>
    fetchJson<any>(`/market/ohlcv?symbol=${symbol}&timeframe=${timeframe}&limit=${limit}`),
  getPortfolioHistory: () => fetchJson<any[]>('/market/portfolio-history'),
}
