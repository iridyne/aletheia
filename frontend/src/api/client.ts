import type { InfraEvent, SystemStatus } from '../types'

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
  // EQ-R (2026-07-01): instrument classification for display + routing.
  // asset_class: "crypto" (default) | "equity" (US stock tokens from equity_chain lens)
  // market_type: "spot" | "swap" | "perp" — drives slash/no-slash display via fmtSymbol()
  asset_class?: string
  market_type?: string
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
  getReady: () => fetchJson<{ ok: boolean; version: string; pid: number; uptime_sec: number }>('/ready'),
  // Restart the gateway worker (warm) or full process (cold). Issued from the
  // topbar restart button when the health endpoint reports code_updated_pending_restart.
  restartGateway: () => post<{ mode: 'warm' | 'cold'; ok: boolean; message: string }>('/gateway/restart'),
  getStatus: () => fetchJson<SystemStatus>('/status'),
  getInfraEvents: () => fetchJson<{ events: InfraEvent[] }>('/infra/events?limit=12&min_severity=warning'),
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

  // Post-trade lessons (LessonStore). PI agent consumes them via trading_context.py:592
  // (wired in b9ad957). TTL fields: accepted lessons enter 24h dry-run then expire after 30d.
  getLessons: (limit = 50, status?: string) => fetchJson<Array<{
    lesson_id: string
    source_type?: string
    source_id?: string
    category?: string
    severity?: string
    summary?: string
    status?: string
    created_at?: string
    activated_at?: string
    dry_run_until?: string
    expires_at?: string
  }>>(`/persistence/lessons?limit=${limit}${status ? `&status=${encodeURIComponent(status)}` : ''}`),

  // Trading guards: accepted lessons promoted to active blocking/advisory constraints.
  getGuards: () => fetchJson<Array<{
    guard_id: string
    title: string
    description?: string
    status?: string
    scope?: string
    enforcement?: string
    severity?: string
    score_penalty?: number
    block_candidate?: boolean
    tags?: string[]
  }>>('/knowledge/guards'),
  getGuardStats: () => fetchJson<Array<{
    guard_id: string
    title: string
    fire_count: number
    loss_rate_when_fired: number
    avg_loss_pct: number
    estimated_savings: number
  }>>('/knowledge/guards/stats'),

  pause: () => post('/gateway/pause'),
  resume: () => post('/gateway/resume'),
  pauseOperator: () => post('/operator/pause'),
  resumeOperator: () => post('/operator/resume'),
  wakeOperator: () => post('/operator/wake'),

  setPermissionMode: (mode: string) => post('/settings/permission-mode', { mode }),
  setExchangeMode: (mode: string) => post('/settings/exchange-mode', { mode }),

  // Model provider config
  getModelConfig: () => fetchJson<{ default_provider: string; deep_provider: string | null; fast_provider: string | null; providers: Record<string, any> }>('/model'),
  getModelHealth: () => fetchJson<{ provider: string; available: boolean; model: string; error: string | null; models: string[] }[]>('/model/health'),
  pingModel: () => post<{ ok: boolean; latency_ms: number; model: string; response: string; error: string | null }>('/model/ping'),
  setModelProvider: (body: {
    provider: string; base_url?: string; protocol?: string; model?: string
    enabled?: boolean; default?: boolean; set_as_deep?: boolean; set_as_fast?: boolean
    api_key?: string; codex_model?: string; workspace_url?: string; auth_scheme?: string
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

  // OpenAI subscription OAuth (ChatGPT Plus/Pro)
  openaiOAuthStart: () => post<{ state: string; auth_url: string | null; error: string | null }>('/model/openai-oauth/start'),
  openaiOAuthStatus: () => fetchJson<{ state: string; auth_url: string | null; error: string | null }>('/model/openai-oauth/status'),
  openaiOAuthFinalize: () => post<{ persisted: boolean; reason?: string }>('/model/openai-oauth/finalize'),
  openaiOAuthCancel: () => post('/model/openai-oauth/cancel'),
  openaiOAuthDisconnect: () => post<{ disconnected: boolean }>('/model/openai-oauth/disconnect'),

  // Claude subscription OAuth (Claude Max/Pro)
  claudeOAuthStart: () => post<{ state: string; auth_url: string | null; error: string | null }>('/model/claude-oauth/start'),
  claudeOAuthComplete: (body: { code: string }) => post<{ state: string; error: string | null }>('/model/claude-oauth/complete', body),
  claudeOAuthStatus: () => fetchJson<{ state: string; auth_url: string | null; error: string | null }>('/model/claude-oauth/status'),
  claudeOAuthCancel: () => post('/model/claude-oauth/cancel'),
  claudeOAuthDisconnect: () => post<{ disconnected: boolean }>('/model/claude-oauth/disconnect'),

  // Exchange credentials — unified per-exchange APIs
  getBitgetCredStatus: () => fetchJson<{ demo: any; live: any }>('/bitget/credentials-status'),
  setBitgetCredentials: (body: {
    profile: string; api_key?: string; api_secret?: string; passphrase?: string
  }) => post('/settings/exchange-credentials', body),
  testBitget: (mode?: string) =>
    post<{ connected: boolean; mode?: string; error?: string }>('/bitget/test-connection', mode ? { mode } : {}),
  testBitgetCandidate: (body: { profile: string; api_key: string; api_secret: string; passphrase: string }) =>
    post<{ connected: boolean; profile?: string; error?: string }>('/bitget/test-candidate', body),

  getBinanceCredStatus: () => fetchJson<{ testnet: any; live: any }>('/binance/credentials-status'),
  setBinanceCredentials: (body: { profile: string; api_key?: string; api_secret?: string }) =>
    post('/settings/binance-credentials', body),
  testBinance: (mode?: string) =>
    post<{ connected: boolean; mode?: string; error?: string }>('/binance/test-connection', mode ? { mode } : {}),
  testBinanceCandidate: (body: { mode: string; api_key: string; api_secret: string }) =>
    post<{ connected: boolean; mode?: string; error?: string }>('/binance/test-candidate', body),

  getOkxCredStatus: () => fetchJson<{ demo: any; live: any }>('/okx/credentials-status'),
  setOkxCredentials: (body: { profile?: string; api_key?: string; api_secret?: string; passphrase?: string }) =>
    post('/settings/okx-credentials', body),
  testOkx: (mode?: string) =>
    post<{ connected: boolean; mode?: string; error?: string }>('/okx/test-connection', mode ? { mode } : {}),
  testOkxCandidate: (body: { mode?: string; api_key: string; api_secret: string; passphrase: string }) =>
    post<{ connected: boolean; mode?: string; error?: string }>('/okx/test-candidate', body),

  // Legacy aliases (keep for any remaining callers)
  setExchangeCredentials: (body: {
    profile: string; api_key?: string; api_secret?: string; passphrase?: string
  }) => post('/settings/exchange-credentials', body),
  getBinanceTestnetCredStatus: () => fetchJson<{ api_key: any; api_secret: any }>('/binance-testnet/credentials-status'),
  setBinanceTestnetCredentials: (body: { api_key?: string; api_secret?: string }) =>
    post('/settings/binance-testnet-credentials', body),
  testBinanceTestnet: () =>
    post<{ connected: boolean; positions_count?: number; error?: string }>('/binance-testnet/test-connection', {}),

  // Telegram bot reachability (facet A — never returns the token)
  getTelegramHealth: () => fetchJson<{ reachable: boolean; username?: string; error?: string }>('/telegram/health'),

  // Live state
  liveUnlock: () => post('/settings/live-unlock', { confirm: true }),
  liveLock: () => post('/settings/live-lock'),

  // Risk: boundaries display + kill switch + editable config
  getRiskBoundaries: () => fetchJson<Record<string, any>>('/risk/boundaries'),
  getRiskStatus: () => fetchJson<Record<string, any>>('/risk/status'),
  // 24h pipeline counts — used by Risk Active Risk panel to surface "the system is
  // actively guarding" (approved/rejected/top rejection reason) even in the healthy state.
  getPipelineFunnel: (hours = 24) => fetchJson<{
    window_hours: number
    risk_decisions: {
      approved: number; rejected: number
      approve_rate_pct: number | null
      top_rejection_reasons: { message: string; count: number }[]
    }
    fills: number
  }>(`/analytics/pipeline-funnel?hours=${hours}`),
  getLearningFunnel: () => fetchJson<{
    knowledge_items_by_type: Array<{ item_type: string; count: number }>
    pi_actual_visibility: { layer3_lesson_hits: number; layer3_guards_available: number; note: string }
    ps_lessons: { total: number; by_status: Array<{ status: string; count: number }>; latest_created_at: string | null }
    serenity_opportunities_active: number
  }>('/analytics/learning-funnel'),
  getKillSwitch: () => fetchJson<{ active: boolean }>('/risk/kill-switch'),
  // Releasing the brake requires confirm_release=true (backend returns 422 otherwise).
  setKillSwitch: (active: boolean, reason = '', confirmRelease = false) =>
    post<{ active: boolean }>('/risk/kill-switch', { active, reason, confirm_release: confirmRelease }),
  updateRiskConfig: (body: Partial<{
    max_daily_loss_pct: number; max_single_trade_risk_pct: number
    max_total_live_exposure_pct: number; max_live_position_notional_usdt: number
    max_live_limited_cap_usdt: number; starting_capital_usdt: number
    max_demo_position_notional_usdt: number; max_demo_single_trade_risk_pct: number
    max_futures_leverage_live: number; max_futures_leverage_demo: number
    max_open_positions_live: number; max_open_positions_demo: number
    allow_short_demo: boolean; allow_short_live: boolean
    kill_switch_enabled_live: boolean; kill_switch_enabled_demo: boolean
    correlation_guard_enabled: boolean; max_sector_concentration_pct: number; max_portfolio_beta: number
  }>) => post<{ updated: Record<string, any>; risk: Record<string, any> }>('/settings/risk', body),
  updateSymbolFilter: (body: { mode: string; symbols: string[] }) =>
    post<{ mode: string; symbols: string[] }>('/settings/symbols', body),
  updateEquities: (tickers: string[]) =>
    post<{ observed_equities: string[] }>('/settings/equities', { tickers }),
  updateTradingConfig: (body: Partial<{
    default_stop_loss_pct: number; default_take_profit_pct: number
    max_position_hold_hours: number; slippage_bps: number; execution_fee_pct: number
    min_confidence_to_trade: string; auto_adopt_enabled: boolean
    trail_enabled: boolean; trail_atr_mult: number; trail_pct: number; breakeven_trigger_pct: number
    exchange_tpsl_enabled: boolean
    default_notional_usdt: number; vol_sizing_enabled: boolean
    vol_target_risk_usdt: number; vol_target_pct: number; vol_kelly_cap_fraction: number
    persona_sizing_enabled: boolean; active_persona: string
    persona_learning_enabled: boolean; persona_learning_min_samples: number
    persona_progressive_unlock_enabled: boolean; persona_unlock_step_samples: number
    persona_pyramid_enabled: boolean; persona_pyramid_add_trigger_pct: number
    sweep_use_optuna: boolean; sweep_n_trials: number; sweep_optuna_timeout_sec: number; sweep_top_k: number
    market_aware_exits: Partial<{
      enabled: boolean; min_hold_hours_before_market_exit: number; max_signal_age_min: number
      urgent_exit_score: number; urgent_exit_max_pnl_pct: number
      tighten_stop_score: number; confidence_collapse_score: number
      tighten_atr_mult: number; tighten_pct: number
    }>
  }>) => post<{ updated: Record<string, any>; trading: Record<string, any> }>('/settings/trading', body),

  setAutonomousLearning: (enabled: boolean) =>
    post<{ old: boolean; new: boolean }>('/settings/autonomous-learning', { enabled }),
  setBypassDistressGate: (enabled: boolean) =>
    post<{ old: boolean; new: boolean }>('/settings/bypass-distress-gate', { enabled }),
  setExchangeMarkets: (body: { spot: boolean; usdt_futures: boolean; paper_execution_mode?: string }) =>
    post<Record<string, any>>('/settings/exchange-markets', body),
  setPrimaryExchange: (exchange_id: string) =>
    post<{ exchange_id: string; 'exchange.name': string; 'exchange.paper_execution_mode': string; 'market_data.ccxt_exchange': string; 'risk.allowed_exchange': string }>('/settings/primary-exchange', { exchange_id }),
  updateRuntimeConfig: (body: Partial<{
    scheduler_enabled: boolean
    git_enabled: boolean
    auto_reload: boolean
    autostart_on_start: boolean
    runtime_continuity_enabled: boolean
    runtime_continuity_lock_ttl_sec: number
    runtime_continuity_shadow_port_offset: number
  }>) =>
    post<{ updated: Record<string, any> }>('/settings/runtime', body),
  updateAppConfig: (body: Partial<{ auto_open_browser: boolean; equity_curve_mode: string }>) =>
    post<{ updated: Record<string, any> }>('/settings/app', body),
  updatePiDaemonConfig: (body: Partial<{ enabled: boolean; debounce_sec: number; cooldown_sec: number; heartbeat_sec: number }>) =>
    post<{ updated: Record<string, any> }>('/settings/pi_daemon', body),
  updateSerenityConfig: (body: Partial<{ auto_promote: boolean; min_score_for_pi: number }>) =>
    post<{ updated: Record<string, any> }>('/settings/serenity', body),
  updateOperatorConfig: (body: Partial<{ loop_enabled: boolean }>) =>
    post<{ updated: Record<string, any> }>('/settings/operator', body),
  updateGuardConfig: (body: Partial<{ enabled: boolean; enforce_in_paper_ranker: boolean; auto_promote_enabled: boolean; min_matching_trades: number; max_score_penalty: number }>) =>
    post<{ updated: Record<string, any> }>('/settings/guard', body),
  updateRegimeConfig: (body: Partial<{ enabled: boolean; short_enabled: boolean; bias_threshold: number; min_hold_periods: number }>) =>
    post<{ updated: Record<string, any> }>('/settings/regime', body),
  updateHealthConfig: (body: Partial<{
    enabled: boolean; alert_via_telegram: boolean; alert_via_windows_toast: boolean; max_agent_failure_ratio: number
    llm_unavailable_alert_after: number; market_data_max_staleness_min: number
    supervisor_max_restarts: number; supervisor_restart_window_sec: number
    supervisor_backoff_cap_sec: number
    self_heal_enabled: boolean; self_heal_min_interval_sec: number; self_heal_max_per_hour: number
    self_heal_code_drift: boolean; self_heal_market_data: boolean; self_heal_port_cleanup: boolean
    require_llm_at_startup: boolean; startup_gate_max_retries: number; startup_gate_retry_delay_sec: number
    auto_resume_enabled: boolean; auto_resume_interval_sec: number
  }>) => post<{ updated: Record<string, any> }>('/settings/health-config', body),
  updateRetentionConfig: (body: Partial<{
    agent_runs_days: number; tool_calls_days: number; run_steps_days: number
    market_snapshots_days: number; portfolio_snapshots_days: number
    opportunity_ttl_hours: number; jsonl_max_mb: number
  }>) => post<{ updated: Record<string, any> }>('/settings/retention', body),
  updateTelegramConfig: (body: Partial<{ enabled: boolean; allowed_chat_ids: number[]; notify_trade_fills: boolean; notify_daily_summary: boolean; daily_summary_hour_cst: number; dnd_enabled: boolean; dnd_start_hour: number; dnd_end_hour: number }>) =>
    post<{ updated: Record<string, any> }>('/settings/telegram', body),
  updateWebhookConfig: (body: Partial<{ enabled: boolean; url: string; secret: string; notify_trade_fills: boolean; notify_alerts: boolean; notify_daily_summary: boolean; timeout_sec: number }>) =>
    post<{ updated: Record<string, any> }>('/settings/webhook', body),
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

  // Autostart + watchdog (Windows Task Scheduler; on-logon start + 2-min watchdog)
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
  getProposal: (id: string) => fetchJson<any>(`/proposals/${id}`),
  approveProposal: (id: string) => post(`/proposals/${id}/approve-paper`),
  rejectProposal: (id: string) => post(`/proposals/${id}/reject`),
  getProposalRiskDecision: (id: string) => fetchJson<{ proposal_id: string; decision: any }>(`/proposals/${id}/risk-decision`),
  getTrades: () => fetchJson<any[]>('/trades'),
  exportTradesCsv: () => `${BASE}/trades/export?fmt=csv`,
  getPositions: () => fetchJson<any[]>('/positions'),
  getPositionsReconciliation: () => fetchJson<import('../types').PositionsReconciliation>('/positions/reconciliation'),
  getPortfolio: () => fetchJson<Record<string, any>>('/portfolio'),
  getUtaState: () => fetchJson<Record<string, any>>('/uta/state'),
  getStrategies: () => fetchJson<any[]>('/strategies'),
  promoteStrategy: (id: string, target_state: string) => post(`/strategies/${id}/promote`, { target_state }),
  retireStrategy: (id: string) => post(`/strategies/${id}/retire`),
  setStrategyVenue: (id: string, execution_venue: string | null) =>
    fetchJson<any>(`/strategies/${id}/venue`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ execution_venue }) }),
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
  togglePrediction: (enabled: boolean) => post<{ enabled: boolean }>('/prediction/toggle', { enabled }),

  // Serenity Lens endpoints (research-first investigation pipeline)
  getSerenityThemes: (status?: string) => fetchJson<SerenityTheme[]>(status ? `/serenity/themes?status=${status}` : '/serenity/themes'),
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
  getPortfolioHistory: (days?: number) => fetchJson<any[]>(`/market/portfolio-history${days ? `?days=${days}` : ''}`),

  // Capital events (deposit/withdraw tracking)
  getNetDeposits: () => fetchJson<{ net_deposits_usdt: number; first_event_at: string | null }>('/capital/net-deposits'),
  getCapitalEvents: () => fetchJson<any[]>('/capital/events'),
  createCapitalEvent: (body: { broker_id: string; event_type: string; amount_usdt: number; note?: string }) =>
    post<any>('/capital/events', body),

  // Audit / Activity
  getAuditEvents: (limit = 50) => fetchJson<any[]>(`/audit/events?limit=${limit}`),

  // Market snapshots (watchlist)
  getMarketSnapshots: (symbol?: string, limit = 10) =>
    fetchJson<any[]>(`/market/snapshots${symbol ? `?symbol=${encodeURIComponent(symbol)}&limit=${limit}` : `?limit=${limit}`}`),

  // Macro indicators (VIX, DXY, SPY, BTC dominance, risk regime)
  getMacro: () => fetchJson<{ available: boolean; data: any }>('/market/macro'),

  // News Intelligence
  getNews: (limit = 50) => fetchJson<any[]>(`/news?limit=${limit}`),
  getNewsItem: (id: string) => fetchJson<any>(`/news/${id}`),
  analyzeNews: (id: string) => post<any>(`/news/${id}/analyze`),
  createNews: (body: { title: string; source?: string; summary?: string; symbols_mentioned?: string[]; sentiment?: string }) =>
    post<any>('/news', body),
  updateNewsConfig: (body: any) => post<any>('/settings/news', body),

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
  getUniverseCalibration: () => fetchJson<import('../types').CalibrationResponse>('/universe/calibration'),
  updateUniverseConfig: (body: Partial<{
    enabled: boolean; refresh_interval_min: number; max_candidates: number;
    core_pool_size: number; min_volume_24h_usdt: number; max_spread_bps: number;
    min_depth_10bps_usd: number; explore_ratio: number;
    snapshot_ttl_enabled: boolean; snapshot_ttl_hours: number;
    blacklist: string[]; fallback_symbols: string[];
    market_type: string; family_quota: Record<string, number>;
    family_quota_by_regime: Record<string, Record<string, number>>;
  }>) => post<any>('/settings/universe', body),

  // Strategy Factory config
  updateStrategyFactoryConfig: (body: Partial<{
    backtest_gate_enabled: boolean; sweep_enabled: boolean; sweep_max_variants: number;
    event_driven_enabled: boolean; event_cooldown_minutes: number; event_min_score: number;
    walk_forward_enabled: boolean; walk_forward_folds: number; walk_forward_min_bars: number;
    high_trade_count_threshold: number; min_runtime_days_relaxed: number;
  }>) => post<any>('/settings/strategy_factory', body),
  // Market data config
  updateMarketDataConfig: (body: Partial<{
    ccxt_exchange: string; ccxt_fallback: string;
    ohlcv_cache_ttl_min: number; enable_yfinance_macro: boolean;
    observed_stocks: string[]; observed_stock_tokens: string[]; observed_xstocks: string[];
    observed_prediction_markets: string[];
    // Round 2 macro signals
    enable_long_short_ratio: boolean;
    enable_fear_greed: boolean;
    enable_coingecko_top: boolean;
    coingecko_top_n: number;
    derivatives_history_hours: number;
    enable_hyperliquid: boolean;
    enable_defillama: boolean;
  }>) => post<{ updated: Record<string, any>; market_data: Record<string, any> }>('/settings/market-data', body),

  // Historical data config
  updateHistoricalDataConfig: (body: Partial<{
    enabled: boolean; max_bars_per_pair: number; default_months: number;
    nightly_backfill_enabled: boolean; nightly_backfill_hour_cst: number;
    min_bars_for_backtest: number;
  }>) => post<any>('/settings/historical_data', body),

  // Event trigger config
  updateEventTriggerConfig: (body: Partial<{
    enabled: boolean; price_move_pct_threshold: number; volume_surge_multiplier: number;
    watch_universe_members: boolean; trigger_strategy_design: boolean;
    strategy_design_min_move_pct: number;
  }>) => post<any>('/settings/event_triggers', body),

  // Network / proxy config
  updateNetworkConfig: (body: Partial<{ proxy_url: string; no_proxy: string }>) =>
    post<{ updated: Record<string, any>; network: { proxy_url: string; no_proxy: string } }>('/settings/network', body),
  testNetworkProxy: () =>
    post<{ reachable: boolean; latency_ms: number; error: string | null }>('/settings/network/test'),

  // Trade Analytics panel
  getTradeStats: () => fetchJson<TradeStats>('/analytics/trade-stats'),
  getStrategyKpis: () => fetchJson<Record<string, { trade_count: number; win_rate: number; total_pnl: number }>>('/analytics/strategy-kpis'),
  getStrategyEvidence: () => fetchJson<Array<{
    strategy_id: string
    strategy_name: string
    state: string
    symbol: string
    timeframe: string
    direction_bias: string | null
    has_ruleset: boolean
    compile_status: string
    paper_summary: { trade_count: number; expectancy: number; win_rate: number; total_pnl: number; profit_factor: number } | null
    drift_summary: { drift_status: string; paper_trade_count: number; paper_expectancy: number; backtest_expectancy: number; paper_to_backtest_ratio: number | null } | null
    factor_summary: { total_factors: number; blocked_count: number; preferred_count: number } | null
    evidence_status: string
    recommended_action: string
    recommended_action_reasons: string[]
  }>>('/analytics/strategy-evidence'),
  getBacktestPaperDrift: () => fetchJson<Array<{
    strategy_id: string
    strategy_name: string
    state: string
    symbol: string
    timeframe: string
    backtest_trade_count: number
    backtest_win_rate: number
    backtest_profit_factor: number
    backtest_total_return_pct: number
    backtest_max_drawdown_pct: number
    paper_trade_count: number
    paper_expectancy: number
    paper_win_rate: number
    paper_profit_factor: number
    paper_total_pnl: number
    drift_expectancy: number | null
    paper_to_backtest_ratio: number | null
    drift_status: string
    drift_reasons: string[]
  }>>('/analytics/backtest-paper-drift'),
  getEquityCurve: (days?: number) => fetchJson<{
    points: { time: string; pnl: number; cumulative_pnl: number }[];
    current_unrealized: number;
    total_realized: number;
  }>(`/analytics/equity-curve${days ? `?days=${days}` : ''}`),
  getTradeInsights: () => fetchJson<TradeInsights>('/analytics/trade-insights'),
  getGoLiveScorecard: () => fetchJson<GoLiveScorecard>('/analytics/go-live-scorecard'),
  getCostSummary: () => fetchJson<CostSummary>('/analytics/cost-summary'),
  getConfidenceCalibration: () => fetchJson<CalibrationReport>('/analytics/confidence-calibration'),
  getReasoningFeedback: () => fetchJson<ReasoningFeedback>('/analytics/reasoning-feedback'),

  getBacktestRuns: (strategyId?: string) =>
    fetchJson<any[]>(`/analytics/backtest/runs${strategyId ? `?strategy_id=${strategyId}` : ''}`),
  getBacktestRunDetail: (runId: string) =>
    fetchJson<any>(`/analytics/backtest/runs/${runId}`),
  getBacktestCompare: (strategyId: string) =>
    fetchJson<any>(`/analytics/backtest/compare/${strategyId}`),
  getStrategyPerformance: () =>
    fetchJson<any>('/analytics/strategy-performance'),
  getSerenityPipeline: () =>
    fetchJson<any>('/serenity/pipeline'),

  // Generic helpers for pages that need simple get/post
  get: <T = any>(path: string) => fetchJson<T>(path),
  post: <T = any>(path: string, body?: unknown) => post<T>(path, body),
}

export interface TradeStatsSource {
  source_type: 'ruleset' | 'opportunity'
  trade_count: number
  win_rate: number
  avg_hold_hours: number
  expectancy_pct: number
  total_pnl: number
}

export interface TradeStatsFamily {
  family: string
  trade_count: number
  win_rate: number
  avg_hold_hours: number
  expectancy_pct: number
}

export interface HoldBucket {
  bucket: string
  count: number
  pct: number
}

export interface TradeStats {
  by_source: TradeStatsSource[]
  by_family: TradeStatsFamily[]
  hold_distribution: HoldBucket[]
  totals: {
    trade_count: number
    win_rate: number
    avg_hold_hours: number
    median_hold_hours: number
    total_pnl: number
  }
}

// ── Trade Insights (comprehensive charts) ────────────────────────────────────

export interface EquityPoint {
  time: string
  pnl: number
  cumulative_pnl: number
}

export interface DrawdownPoint {
  time: string
  drawdown: number
}

export interface DirectionStats {
  trade_count: number
  win_rate: number
  total_pnl: number
  avg_pnl_pct: number
  avg_hold_hours: number
}

export interface ProfitFactorData {
  gross_profit: number
  gross_loss: number
  profit_factor: number | null
  avg_win: number
  avg_loss: number
  largest_win: number
  largest_loss: number
  win_count: number
  loss_count: number
}

export interface DayStats {
  day: string
  trade_count: number
  win_rate: number
  avg_pnl_pct: number
}

export interface HourStats {
  hour: number
  trade_count: number
  win_rate: number
  avg_pnl_pct: number
}

export interface PnlBin {
  bin: string
  count: number
}

export interface RegimeStats {
  regime: string
  trade_count: number
  win_rate: number
  avg_pnl_pct: number
}

export interface StreakData {
  max_consecutive_wins: number
  max_consecutive_losses: number
  current_streak: number
  current_streak_type: string
  total_wins: number
  total_losses: number
}

export interface HoldPnlPoint {
  hold_hours: number
  pnl_pct: number
  direction: string
  symbol: string
  market_type?: string
}

export interface HourDayCell {
  day: number
  hour: number
  avg_pnl_pct: number
  trade_count: number
}

export interface TradeFrequencyDay {
  date: string
  trades: { pnl_pct: number; direction: string }[]
}

export interface SymbolDirectionEntry {
  symbol: string
  direction: string
  win_rate: number
  avg_pnl_pct: number
  trade_count: number
}

export interface HoldDurationBucket {
  bucket: string
  trade_count: number
  win_rate: number
  avg_pnl_pct: number
  avg_win_pct: number
  avg_loss_pct: number
}

export interface RMultipleBin {
  bin: string
  count: number
}

export interface ExitReasonEntry {
  reason: string
  trade_count: number
  win_rate: number
  avg_pnl_pct: number
  total_pnl: number
  pnl_contribution_pct: number
}

export interface TradeInsights {
  since: string         // ISO date, e.g. "2026-06-22" — all metrics cover closed_at >= since
  trade_count: number
  equity_curve: EquityPoint[]
  drawdown_curve: DrawdownPoint[]
  by_direction: Record<string, DirectionStats>
  profit_factor: ProfitFactorData
  by_day_of_week: DayStats[]
  by_hour: HourStats[]
  pnl_distribution: PnlBin[]
  by_regime: RegimeStats[]
  streaks: StreakData
  hold_vs_pnl: HoldPnlPoint[]
  hour_day_heatmap: HourDayCell[]
  trade_frequency: TradeFrequencyDay[]
  symbol_direction_matrix: SymbolDirectionEntry[]
  hold_duration_buckets: HoldDurationBucket[]
  r_multiple_distribution: RMultipleBin[]
  by_exit_reason: ExitReasonEntry[]
}

export interface GoLiveCriterion {
  key: string
  label: string
  score: number
  weight: number
  weighted_contribution: number
  detail: string
  status: 'pass' | 'warning' | 'fail'
}

export interface GoLiveScorecard {
  composite_score: number
  traffic_light: 'red' | 'yellow' | 'green'
  criteria: GoLiveCriterion[]
  blockers: string[]
  computed_at: string
}

export interface CostSummary {
  total_entry_fees: number
  total_exit_fees: number
  total_fees: number
  total_slippage: number
  total_costs: number
}

export interface CalibrationBucket {
  label: string
  conviction_range: string
  sample_size: number
  win_count: number
  actual_win_rate: number
  expected_win_rate: number
  calibration_error: number
  reliable: boolean
}

export interface CalibrationReport {
  generated_at?: string
  window_days?: number
  total_executed?: number
  buckets: CalibrationBucket[]
  ece: number | null
  ece_grade: string
  overall_win_rate?: number
  overconfident_buckets?: string[]
  underconfident_buckets?: string[]
  calibration_summary?: string
  status?: string
}

export interface ReasoningFeedback {
  generated_at?: string
  window_days?: number
  sample_proposals?: number
  sample_executed?: number
  approval_outcomes?: { pattern_label: string; win_rate: number; sample_size: number; avg_pnl: number; avg_conviction: number }[]
  rejection_patterns?: { pattern_label: string; rejection_rate: number; rejection_count: number }[]
  recommended_avoid?: string[]
  recommended_pursue?: string[]
  high_conviction_win_rate?: number | null
  low_conviction_win_rate?: number | null
  status?: string
}
