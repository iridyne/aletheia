const BASE = '/api'

function extractApiError(status: number, body: string): string {
  try {
    const obj = JSON.parse(body)
    const detail = obj.detail
    if (typeof detail === 'string') return detail
    if (detail && typeof detail === 'object') {
      if (detail.reason) return detail.reason.replace(/_/g, ' ')
      if (detail.message) return detail.message
    }
  } catch { /* not JSON, fall through */ }
  if (body.length > 80) return `${status} error`
  return body || `${status} error`
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) throw new Error(extractApiError(res.status, await res.text()))
  return res.json()
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return fetchJson<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined })
}

export interface KeyStatus { configured: boolean; last4: string | null }

export interface SerenityTheme {
  id: string
  name: string
  demand_wave: string | null
  status: string
  objective: string | null
  description: string | null
  strategy_notes: string | null
  tags: string[] | string
  created_at: string
  updated_at: string
}

export interface SerenityEvidence {
  id: string
  theme_id: string
  symbol?: string | null
  source: string
  source_ref?: string | null
  title: string
  summary: string | null
  tier: string
  supports: boolean
  evidence_type: string
  event_at: string
  created_at: string
  actor?: string | null
  confidence: string
  tags: string[] | string
}

export interface SerenityRiskGate {
  id: string
  opportunity_id: string
  gate_name: string
  status: string
  metric: string
  observed: string | number | null
  threshold: string | number | null
  message?: string | null
  checked_at: string
}

export interface SerenityOpportunity {
  id: string
  theme_id: string
  symbol: string
  direction: string
  thesis: string | null
  evidence_tier: string
  stage: string
  average_mode: string
  action_intent: string
  status: string
  risk_flags: string[] | string
  risk_metrics: Record<string, unknown> | string
  confidence_score: number | null
  created_at: string
  updated_at: string
  serenity_score?: {
    opportunity_id: string
    score: number
    bucket: string
    evidence_score: number
    stage_score: number
    average_score: number
    confidence_component: number
    stage_index: number
    evidence_count: number
    risk_fail_gates: number
    risk_warn_gates: number
    risk_flag_count: number
    average_engine?: {
      mode: string
      active: boolean
      dca_ladder?: Record<string, unknown>
      historical_mismatch?: Record<string, unknown>
      signals?: Record<string, unknown>
    }
    reasons: Record<string, unknown>
  }
  risk_gates?: SerenityRiskGate[]
}

export interface SerenityTimelinePoint {
  id: string
  as_of: string
  created_at: string
  event_type: string
  event_title: string
  stage: string
  score_total: number
  bucket?: string
  evidence_count?: number
  risk_fail_gates?: number
  risk_warn_gates?: number
}

export interface SerenityResearchCycle {
  opportunity: SerenityOpportunity & {
    serenity_score: SerenityOpportunity['serenity_score']
  }
  timeline: SerenityTimelinePoint[]
  risk_gates: SerenityRiskGate[]
  risk_gate_summary: Record<string, number>
  post_mortems: Record<string, unknown>[]
  post_mortem_summary: {
    count: number
    failure_bucket: Record<string, number>
  }
}

export interface SerenityThemeOverview {
  theme: SerenityTheme
  metrics: {
    opportunity_count: number
    evidence_count: number
    stage_counts: Record<string, number>
    average_mode_counts: Record<string, number>
    pipeline_order: string[]
  }
}

export const api = {
  getHealth: () => fetchJson<{ status: string; issues: string[]; version: string }>('/health'),
  // Restart the gateway worker (warm) or full process (cold). Issued from the
  // topbar restart button when the health endpoint reports code_updated_pending_restart.
  restartGateway: () => post<{ mode: 'warm' | 'cold'; ok: boolean; message: string }>('/gateway/restart'),
  getStatus: () => fetchJson<Record<string, unknown>>('/status'),
  getOperatorStatus: () => fetchJson<any>('/operator/status'),
  getRuntimeStatus: () => fetchJson<any>('/operator/runtime'),
  createRuntimeCandidate: (body: {
    code_version?: string
    config_version?: string
    schema_version?: string
    build_git_sha?: string
    release_bundle_hash?: string
    artifact_path?: string
    cause?: string
  }) => post<any>('/operator/runtime/candidate', body),
  startRuntimeShadow: (runtimeId?: string) =>
    post<any>('/operator/runtime/shadow', {
      runtime_id: runtimeId,
      cause: 'command_center_shadow',
    }),
  promoteRuntime: (runtimeId?: string) =>
    post<any>('/operator/runtime/promote', {
      runtime_id: runtimeId,
      cause: 'command_center_promote',
    }),
  drainRuntime: (runtimeId?: string, force = true) =>
    post<any>('/operator/runtime/drain', {
      runtime_id: runtimeId,
      force,
      cause: 'command_center_drain',
    }),
  rollbackRuntime: () =>
    post<any>('/operator/runtime/rollback', {
      cause: 'command_center_rollback',
    }),
  getRuntimeEvents: (limit = 100) => fetchJson<any[]>(`/operator/runtime/events?limit=${limit}`),
  getSettings: () => fetchJson<Record<string, unknown>>('/settings'),

  pause: () => post('/gateway/pause'),
  resume: () => post('/gateway/resume'),
  pauseOperator: () => post('/operator/pause'),
  resumeOperator: () => post('/operator/resume'),
  wakeOperator: () => post('/operator/wake'),

  setPermissionMode: (mode: string) => post('/settings/permission-mode', { mode }),
  setExchangeMode: (mode: string) => post('/settings/exchange-mode', { mode }),

  // Model provider config
  getModelConfig: () => fetchJson<{ default_provider: string; providers: Record<string, any> }>('/model'),
  getModelHealth: () => fetchJson<{ provider: string; available: boolean; model: string; error: string | null; models: string[] }[]>('/model/health'),
  setModelProvider: (body: {
    provider: string; base_url?: string; protocol?: string; model?: string
    enabled?: boolean; default?: boolean; api_key?: string; codex_model?: string
    workspace_url?: string; auth_scheme?: string
  }) => post('/settings/model', body),
  testModelCandidate: (body: {
    provider: string; base_url?: string; protocol?: string; model?: string
    api_key?: string; auth_scheme?: string; probe?: boolean
  }) => post<{ available: boolean; model: string; error: string | null; models?: string[] }>('/model/test-candidate', body),
  detectModels: (body: {
    provider: string; base_url?: string; api_key?: string; auth_scheme?: string
  }) => post<{ models: { slug: string; display_name: string }[] }>('/model/detect-models', body),

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
  updateRuntimeConfig: (body: Partial<{
    scheduler_enabled: boolean
    git_enabled: boolean
    runtime_continuity_enabled: boolean
    runtime_continuity_lock_ttl_sec: number
    runtime_continuity_shadow_port_offset: number
  }>) =>
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
  getPositionsReconciliation: () => fetchJson<import('../types').PositionsReconciliation>('/positions/reconciliation'),
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

  // Prediction Market Lens endpoints
  getPredictionOpportunities: () => fetchJson<import('../types').PredictionMarket[]>('/prediction/opportunities'),
  getPredictionStatus: () => fetchJson<import('../types').PredictionStatus>('/prediction/status'),
  getPredictionMarkets: () => fetchJson<import('../types').PredictionMarket[]>('/prediction/markets'),
  getPredictionRuns: () => fetchJson<import('../types').PredictionObserverRun[]>('/prediction/runs'),
  refreshPrediction: () => post('/prediction/refresh'),
  getPredictionEvents: (category?: string) => fetchJson<import('../types').PredictionEvent[]>(`/prediction/events${category ? `?category=${category}` : ''}`),
  getPredictionEventDetail: (id: string) => fetchJson<{ event: import('../types').PredictionEvent; markets: import('../types').PredictionMarketDetail[] }>(`/prediction/events/${id}`),
  getPredictionMarketDetail: (id: string) => fetchJson<import('../types').PredictionMarketDetail>(`/prediction/markets/${id}/detail`),
  getPredictionMarketHistory: (id: string) => fetchJson<{ source: string; points: import('../types').ProbabilitySnapshot[] }>(`/prediction/markets/${id}/history`),
  getPredictionMarketClaims: (id: string) => fetchJson<import('../types').PredictionClaim[]>(`/prediction/markets/${id}/claims`),
  getPredictionPreferences: () => fetchJson<import('../types').PredictionPreferences>('/prediction/preferences'),
  updatePredictionPreferences: (body: { categories?: string[] }) => post<any>('/prediction/preferences', body),

  // Serenity Lens endpoints (research-first investigation pipeline)
  getSerenityThemes: () => fetchJson<SerenityTheme[]>('/serenity/themes'),
  getSerenityTheme: (id: string) => fetchJson<SerenityTheme & { tags: string[] }>(`/serenity/themes/${id}`),
  getSerenityThemeOverview: (id: string) => fetchJson<SerenityThemeOverview>(`/serenity/themes/${id}/overview`),
  createSerenityTheme: (body: Partial<SerenityTheme> & { name: string }) =>
    post<{ id: string }>(`/serenity/themes`, body),
  getSerenityEvidence: (themeId?: string, limit = 100) =>
    fetchJson<SerenityEvidence[]>(`/serenity/evidence${themeId ? `?theme_id=${encodeURIComponent(themeId)}&limit=${limit}` : `?limit=${limit}`}`),
  createSerenityEvidence: (body: Partial<SerenityEvidence> & { theme_id: string; title: string }) =>
    post<{ id: string }>('/serenity/evidence', body as never),
  getSerenityOpportunities: (themeId?: string, status?: string, stage?: string, includeScores = true, limit = 100) => {
    const qs = new URLSearchParams()
    if (themeId) qs.set('theme_id', themeId)
    if (status) qs.set('status', status)
    if (stage) qs.set('stage', stage)
    qs.set('include_scores', String(includeScores))
    qs.set('limit', String(limit))
    return fetchJson<SerenityOpportunity[]>(`/serenity/opportunities?${qs.toString()}`)
  },
  createSerenityOpportunity: (body: Partial<SerenityOpportunity> & {
    theme_id: string
    symbol: string
    direction: string
  }) => post<{ id: string }>('/serenity/opportunities', body as never),
  getSerenityOpportunity: (id: string, includeScores = true) =>
    fetchJson<SerenityOpportunity>(`/serenity/opportunities/${id}?include_scores=${includeScores}`),
  scoreSerenityOpportunity: (id: string) =>
    fetchJson<Record<string, unknown>>(`/serenity/opportunities/${id}/score`),
  getSerenityResearchCycle: (id: string) => fetchJson<SerenityResearchCycle>(`/serenity/opportunities/${id}/research-cycle`),
  getSerenityOpportunityRiskGates: (id: string) => fetchJson<SerenityRiskGate[]>(`/serenity/opportunities/${id}/risk-gates`),
  createSerenityOpportunityRiskGate: (id: string, body: {
    gate_name: string
    status: string
    metric: string
    observed?: string | number | null
    threshold?: string | number | null
    message?: string | null
  }) => post<{ id: string }>(`/serenity/opportunities/${id}/risk-gates`, body),
  getSerenityReplay: (id: string) => fetchJson<Record<string, unknown>[]>(`/serenity/opportunities/${id}/replay`),
  createSerenityReplaySnapshot: (id: string, body: {
    as_of: string
    stage?: string
    event_type?: string
    event_title?: string
    payload?: Record<string, unknown>
  }) => post<{ id: string }>(`/serenity/opportunities/${id}/replay`, body),
  getSerenityPostMortems: (id: string) => fetchJson<Record<string, unknown>[]>(`/serenity/opportunities/${id}/post-mortems`),
  createSerenityPostMortem: (id: string, body: {
    outcome: string
    summary: string
    failure_bucket?: string | null
    score_delta?: number | null
    attribution?: string | null
    created_by?: string
  }) => post<{ id: string }>(`/serenity/opportunities/${id}/post-mortems`, body),

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
  sendOperatorCommand: (
    input: string,
    params?: { context?: Record<string, string>; sessionId?: string },
  ) =>
    post<any>('/operator/command', {
      input,
      context: params?.context,
      session_id: params?.sessionId,
    }),
  previewOperatorCommand: (body: { input: string; sessionId?: string }) =>
    post<any>('/operator/command-preview', {
      input: body.input,
      session_id: body.sessionId,
    }),
  setOperatorActiveBranch: (sessionId: string, selectedBy = 'command-center') =>
    post<any>('/operator/active-branch', {
      session_id: sessionId,
      selected_by: selectedBy,
    }),
  createOperatorBranch: (body: {
    title?: string
    kind?: string
    user_input?: string
    parent_session_id?: string
    activate?: boolean
    selected_by?: string
  }) => post<any>('/operator/branches', body),
  getOperatorSessions: () => fetchJson<any[]>('/operator/sessions'),
  getOperatorSession: (id: string) => fetchJson<any>(`/operator/sessions/${encodeURIComponent(id)}`),
  getOperatorTasks: () => fetchJson<any[]>('/operator/tasks'),
  getOperatorCommands: (params?: { sessionId?: string; query?: string; limit?: number }) => {
    const search = new URLSearchParams()
    if (params?.sessionId) search.set('session_id', params.sessionId)
    if (params?.query) search.set('query', params.query)
    if (params?.limit) search.set('limit', String(params.limit))
    const suffix = search.size > 0 ? `?${search.toString()}` : ''
    return fetchJson<any[]>(`/operator/commands${suffix}`)
  },
  getOperatorExtensions: (params?: { status?: string; limit?: number }) => {
    const search = new URLSearchParams()
    if (params?.status) search.set('status', params.status)
    if (params?.limit) search.set('limit', String(params.limit))
    const suffix = search.size > 0 ? `?${search.toString()}` : ''
    return fetchJson<any[]>(`/operator/extensions${suffix}`)
  },
  getOperatorContext: (body: { sessionId?: string; profile?: string; sections?: string[] }) =>
    post<any>('/operator/internal/context/query', {
      session_id: body.sessionId,
      profile: body.profile,
      sections: body.sections,
    }),
  getOperatorRecommendations: () => fetchJson<any[]>('/operator/recommendations'),
  getOperatorEvents: (params?: { sessionId?: string; taskId?: string; limit?: number }) => {
    const search = new URLSearchParams()
    if (params?.sessionId) search.set('session_id', params.sessionId)
    if (params?.taskId) search.set('task_id', params.taskId)
    if (params?.limit) search.set('limit', String(params.limit))
    const suffix = search.size > 0 ? `?${search.toString()}` : ''
    return fetchJson<any[]>(`/operator/events${suffix}`)
  },

  // --- Serenity Research Workbench ---
  getSerenityStatus: () => fetchJson<any>('/serenity/status'),
  createSerenityRun: (body: { mode?: string; triggerType?: string; brief?: string; saveToProfile?: boolean; idempotencyKey?: string }) =>
    post<any>('/serenity/runs', body),
  getSerenityRuns: (params?: { status?: string; limit?: number }) => {
    const search = new URLSearchParams()
    if (params?.status) search.set('status', params.status)
    if (params?.limit) search.set('limit', String(params.limit))
    const suffix = search.size > 0 ? `?${search.toString()}` : ''
    return fetchJson<any>(`/serenity/runs${suffix}`)
  },
  getSerenityRunDetail: (runId: string) => fetchJson<any>(`/serenity/runs/${runId}`),
  retrySerenityRun: (runId: string) => post<any>(`/serenity/runs/${runId}/retry`, {}),
  getSerenityProfile: () => fetchJson<any>('/serenity/profile'),
  updateSerenityProfile: (body: any) =>
    fetchJson<any>('/serenity/profile', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  parseSerenityBrief: (brief: string) => post<any>('/serenity/profile/parse', { brief }),
  updateSerenityThemeStatus: (themeId: string, status: string) =>
    fetchJson<any>(`/serenity/themes/${themeId}/status`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) }),
  createSerenityThemeFeedback: (themeId: string, action: string, value?: any) =>
    post<any>(`/serenity/themes/${themeId}/feedback`, { action, value }),
  createSerenityThemeDeepDive: (themeId: string, brief?: string) =>
    post<any>(`/serenity/themes/${themeId}/deep-dive`, { brief }),
  cancelSerenityRun: (runId: string) => post<any>(`/serenity/runs/${runId}/cancel`, {}),
  promoteSerenityTheme: (themeId: string) => post<any>(`/serenity/themes/${themeId}/promote`, {}),
  getSerenityThemeDetail: (themeId: string) => fetchJson<any>(`/serenity/themes/${themeId}/detail`),
  getSerenityThemeClaims: (themeId: string) => fetchJson<any>(`/serenity/themes/${themeId}/claims`),
  getSerenityRunArtifacts: (runId: string) => fetchJson<any>(`/serenity/runs/${runId}/artifacts`),
  getSereniyClaims: (params?: { run_id?: string; price_relevant?: boolean }) => {
    const qs = new URLSearchParams()
    if (params?.run_id) qs.set('run_id', params.run_id)
    if (params?.price_relevant !== undefined) qs.set('price_relevant', String(params.price_relevant))
    return fetchJson<any>(`/serenity/claims?${qs}`)
  },

  // Universe Generator endpoints
  getUniverseCurrent: () => fetchJson<import('../types').UniverseCurrentResponse>('/universe/current'),
  getUniverseSnapshots: () => fetchJson<import('../types').UniverseSnapshot[]>('/universe/snapshots'),
  getUniverseSnapshot: (id: string) => fetchJson<any>(`/universe/snapshots/${id}`),
  getUniverseMember: (id: string) => fetchJson<import('../types').UniverseMemberDetail>(`/universe/members/${id}`),
  refreshUniverse: () => post<{ snapshot_id: string; status: string }>('/universe/refresh'),
  getUniversePolicy: () => fetchJson<any>('/universe/policy'),
  getUniverseRegistryStats: () => fetchJson<{ total: number; active: number; blacklisted: number }>('/universe/registry/stats'),
  updateUniverseConfig: (body: Partial<{
    enabled: boolean; refresh_interval_min: number; max_candidates: number;
    core_pool_size: number; min_volume_24h_usdt: number; max_spread_bps: number;
    explore_ratio: number; blacklist: string[];
  }>) => post<any>('/settings/universe', body),

  // Generic helpers for pages that need simple get/post
  get: <T = any>(path: string) => fetchJson<T>(path),
  post: <T = any>(path: string, body?: unknown) => post<T>(path, body),
}
