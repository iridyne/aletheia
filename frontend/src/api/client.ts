const BASE = '/api'

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`)
  return res.json()
}

export const api = {
  getHealth: () => fetchJson<{ status: string; version: string }>('/health'),
  getStatus: () => fetchJson<Record<string, unknown>>('/status'),
  getSettings: () => fetchJson<Record<string, unknown>>('/settings'),

  pause: () => fetchJson('/gateway/pause', { method: 'POST' }),
  resume: () => fetchJson('/gateway/resume', { method: 'POST' }),

  setPermissionMode: (mode: string) =>
    fetchJson('/settings/permission-mode', { method: 'POST', body: JSON.stringify({ mode }) }),
  setExchangeMode: (mode: string) =>
    fetchJson('/settings/exchange-mode', { method: 'POST', body: JSON.stringify({ mode }) }),

  getAgentRuns: () => fetchJson<unknown[]>('/agent-runs'),
  getOpportunities: () => fetchJson<unknown[]>('/opportunities'),
  getProposals: () => fetchJson<unknown[]>('/proposals'),
  getTrades: () => fetchJson<unknown[]>('/trades'),
  getPositions: () => fetchJson<unknown[]>('/positions'),
  getStrategies: () => fetchJson<unknown[]>('/strategies'),
  getKnowledge: (type?: string) => fetchJson<unknown[]>(`/knowledge/research-notes${type ? `?type=${type}` : ''}`),
  getDreamingLogs: () => fetchJson<unknown[]>('/knowledge/dreaming-logs'),
}
