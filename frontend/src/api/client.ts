import type { InfraEvent, SystemStatus, ThesisLineageRecord } from '../types'

const BASE = '/api'

export interface LearningContributionProjection {
  schema_version: 'lco-v1'
  generated_at: string
  availability: 'available' | 'partial' | 'unavailable'
  unavailable_reason: string | null
  data_freshness: {
    last_evaluated_at: string | null
    age_sec: number | null
    stale_after_sec: number
    state: 'fresh' | 'stale' | 'not_available' | 'unavailable'
  }
  paths: Array<{
    path_id: string
    producer: string
    consumer: string
    consumer_wired: boolean
    settings_supported: boolean
    measurement_supported: boolean
    authority: string
    allowed_effects: string[]
    fallback: string
    outcome_source: string
  }>
  items: Array<{
    path_id: string
    hypothesis_id: string
    experiment_id: string
    stage: string
    experiment_status: string
    allocation_pct: number
    current_identity: {
      policy_epoch: string | null
      target_horizon: string
      outcome_contract_version: string
      metric_version: string
      behavior_contract_hash: string | null
      code_commit: string | null
      matches_running_cohort: boolean
      mismatch_reason: string | null
    }
    measurement: {
      state: 'not_measured' | 'insufficient_data' | 'neutral' | 'positive' | 'negative' | 'invalid'
      reason: string | null
      baseline: number | null
      observed: number | null
      delta: number | null
      confidence_low: number | null
      confidence_high: number | null
      evaluated_at: string | null
      window_start: string | null
      window_end: string | null
    }
    samples: {
      raw_exposures: number
      treatment_assignment_units: number
      control_assignment_units: number
      required_per_arm: number
      complete_assignment_units: number
      cluster_members: number
      selected_members: number
    }
    integrity: {
      source_code_commits: string[]
      source_code_commit_unknown_n: number
      code_commit_provenance_complete: boolean
      outcome_source_member_counts: Record<string, number>
      allocation_selection_source_member_counts: Record<string, number>
      matching_mirror_rows: number
      exclusion_reasons: Record<string, number>
      invalid_reasons: Record<string, number>
      conflict_count: number
      harm_breached: boolean
      breach_reasons: string[]
    }
    lifecycle: {
      recommended_action: string
      recommendation_reason: string | null
      authority_enabled: boolean
      authority_block_reason: string | null
      mutation_available: boolean
      mutation_unavailable_reason: string | null
    }
    latest_historical_measurement: {
      verdict_id: string
      state: string
      reason: string | null
      policy_epoch: string | null
      stage: string | null
      target_horizon: string | null
      outcome_contract_version: string | null
      metric_version: string | null
      behavior_contract_hash: string | null
      code_commit: string | null
      source_code_commits: string[]
      source_code_commit_unknown_n: number
      code_commit_provenance_complete: boolean
      current: boolean
      mismatch_reason: string | null
    } | null
  }>
  operations: {
    config: {
      contribution_evaluation_enabled: boolean
      autonomous_learning_enabled: boolean
      lifecycle_automation_enabled: boolean
      paper_influence_enabled: boolean
    }
    evaluator: {
      job_id: string
      current_status: string
      last_started_at: string | null
      last_succeeded_at: string | null
      next_due_at: string | null
      input_watermark: string | null
      backlog_count: number
      oldest_backlog_at: string | null
      consecutive_failures: number
      last_failed_at: string | null
      last_failure_code: string | null
      catchup_due: boolean
    }
  } | null
}

function cleanErrorText(value: unknown): string | null {
  if (value == null) return null
  const text = String(value).trim()
  return text ? text.replace(/_/g, ' ') : null
}

function formatErrorValue(value: unknown): string | null {
  if (value == null) return null
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return cleanErrorText(value)
  }
  if (Array.isArray(value)) {
    const items = value.map(formatErrorValue).filter(Boolean)
    return items.length ? items.join('; ') : null
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    const explicit = cleanErrorText(record.message)
      || cleanErrorText(record.reason)
      || cleanErrorText(record.error)
      || cleanErrorText(record.detail)
    const failures = formatErrorValue(record.failures)
      || formatErrorValue(record.errors)
      || formatErrorValue(record.gates)

    const parts: string[] = []
    if (explicit) parts.push(explicit)
    if (failures) parts.push(`Failures: ${failures}`)

    if (parts.length === 0) {
      for (const [key, item] of Object.entries(record).slice(0, 4)) {
        const formatted = formatErrorValue(item)
        if (formatted) parts.push(`${cleanErrorText(key)}: ${formatted}`)
      }
    }
    return parts.length ? parts.join(' - ') : null
  }
  return null
}

export function extractApiError(status: number, body: string): string {
  try {
    const obj = JSON.parse(body)
    const detail = obj.detail
    const formatted = formatErrorValue(detail)
    if (formatted) return formatted
  } catch { /* not JSON, fall through */ }
  if (body.length > 80) return `${status} error`
  return body || `${status} error`
}

// Hard cap on every API request. Prevents the top loading bar from staying
// stuck for minutes when the backend is slow to respond after a restart.
const _FETCH_TIMEOUT_MS = 15_000

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const timeoutCtrl = new AbortController()
  const timer = setTimeout(() => timeoutCtrl.abort(), _FETCH_TIMEOUT_MS)

  // Combine our timeout signal with any signal the caller already provided
  // (e.g. React Query's per-query cancellation signal threaded via init.signal).
  // Aborting either source aborts the request.
  const callerSignal = init?.signal
  if (callerSignal) {
    if (callerSignal.aborted) {
      clearTimeout(timer)
      timeoutCtrl.abort()
    } else {
      callerSignal.addEventListener('abort', () => timeoutCtrl.abort(), { once: true })
    }
  }

  try {
    const res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
      signal: timeoutCtrl.signal,
    })
    if (!res.ok) throw new Error(extractApiError(res.status, await res.text()))
    return res.json()
  } finally {
    clearTimeout(timer)
  }
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return fetchJson<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined })
}

function put<T>(path: string, body?: unknown): Promise<T> {
  return fetchJson<T>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined })
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
  getInfraEvents: () => fetchJson<{ events: InfraEvent[] }>('/infra/events?limit=12&min_severity=info'),
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
  // #83 fix: promote/rollback/forced-drain now require an explicit confirm=true.
  // The Command Center already surfaces these as human-driven pivots, so we can
  // hardcode confirm=true here; a follow-up UI batch can lift it into a modal.
  promoteRuntime: (runtimeId?: string) =>
    post<any>('/operator/runtime/promote', {
      runtime_id: runtimeId,
      cause: 'command_center_promote',
      confirm: true,
    }),
  drainRuntime: (runtimeId?: string, force = true) =>
    post<any>('/operator/runtime/drain', {
      runtime_id: runtimeId,
      force,
      cause: 'command_center_drain',
      confirm: force,
    }),
  rollbackRuntime: () =>
    post<any>('/operator/runtime/rollback', {
      cause: 'command_center_rollback',
      confirm: true,
    }),
  getRuntimeEvents: (limit = 100) => fetchJson<any[]>(`/operator/runtime/events?limit=${limit}`),
  getSettings: () => fetchJson<Record<string, unknown> & {
    learning_path_options: Array<{
      path_id: string
      label: string
      supported: boolean
      status: 'supported' | 'consumer_unwired' | 'authority_unwired'
    }>
  }>('/settings'),

  // Post-trade lessons (LessonStore). System Operator consumes them via trading_context.py:592
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
  acceptLesson: (lessonId: string) => post<any>(`/persistence/lessons/${lessonId}/accept`),
  rejectLesson: (lessonId: string) => post<any>(`/persistence/lessons/${lessonId}/reject`),
  archiveLesson: (lessonId: string) => post<any>(`/persistence/lessons/${lessonId}/archive`),

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
  getGuardCandidates: () => fetchJson<Array<{
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
  }>>('/knowledge/guard-candidates'),
  promoteGuardCandidate: (guardId: string) => post<any>(`/knowledge/guard-candidates/${guardId}/promote`),
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
  getInferenceBroker: () => fetchJson<{
    running: boolean
    providers: Record<string, { policy: Record<string, number>; metrics: Record<string, number> }>
  }>('/model/broker'),
  pingModel: () => post<{ ok: boolean; latency_ms: number; model: string; response: string; error: string | null }>('/model/ping'),
  setModelProvider: (body: {
    provider: string; base_url?: string; protocol?: string; model?: string
    enabled?: boolean; default?: boolean; set_as_deep?: boolean; set_as_fast?: boolean
    api_key?: string; codex_model?: string; workspace_url?: string; auth_scheme?: string
    inference_max_concurrency?: number; inference_requests_per_minute?: number
    inference_tpm?: number; inference_queue_capacity?: number
    inference_reserved_high_priority_slots?: number; inference_timeout_sec?: number
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
  // Recent operator decisions — final-stage companion to the funnel.
  // Merges pi_decisions (trade/skip/watch/flip/scale_in) with rejected
  // trade_proposals (user vs auto reject distinguished via audit_events).
  //
  // Optional `outcome` / `symbol` filters are applied server-side AFTER the
  // full-window counts are computed — so counts and total_count never lie
  // about totals even when items is filtered. matched_count reports how many
  // rows matched the filter before truncation to limit.
  getOperatorDecisions: (
    hours = 24,
    limit = 50,
    opts: { outcome?: string | string[]; symbol?: string } = {},
  ) => {
    const params = new URLSearchParams({ hours: String(hours), limit: String(limit) })
    if (opts.outcome) {
      const value = Array.isArray(opts.outcome) ? opts.outcome.join(',') : opts.outcome
      if (value) params.set('outcome', value)
    }
    if (opts.symbol) params.set('symbol', opts.symbol)
    return fetchJson<{
      window_hours: number
      limit: number
      counts: {
        trade: number
        operator_skip: number
        operator_watch: number
        user_reject: number
        auto_reject: number
        reject_unknown: number
      }
      total_count: number
      matched_count: number
      items: Array<{
        opportunity_id: string | null
        proposal_id: string | null
        decision_id: string | null
        symbol: string | null
        direction: string | null
        market_type: string | null
        decided_at: string
        outcome: 'trade' | 'operator_skip' | 'operator_watch' | 'user_reject' | 'auto_reject' | 'reject_unknown'
        action: string
        conviction: number | null
        one_line_reason: string | null
        rejected_actor?: string | null
        rejected_source?: string | null
      }>
    }>(`/analytics/operator-decisions?${params.toString()}`)
  },

  // Full lifecycle trace for one opportunity — powers the decision drawer.
  getOpportunityTrace: (opportunityId: string) =>
    fetchJson<{
      opportunity_id: string
      found: boolean
      symbol?: string | null
      direction?: string | null
      market?: string | null
      scanner_family?: string | null
      created_at?: string | null
      final_state?: string | null
      final_stage?: string | null
      final_reason?: string | null
      timeline?: Array<{
        at: string
        stage?: string
        actor?: string
        summary?: string
        reason?: string
        details?: Record<string, unknown>
      }>
      counterfactual?: {
        verdict?: string | null
        shadow?: Record<string, unknown> | null
        forward?: Record<string, unknown> | null
      }
    }>(`/analytics/opportunity-trace/${encodeURIComponent(opportunityId)}`),

  // Market Observer → Operator funnel. Surfaces WHY opportunities don't reach
  // the operator (e.g. confidence gate, TTL expiry) — counterpart to the
  // Blotter (formerly Approval Desk). Full-window response; see the compact
  // /current-snapshot sibling for a faster subset when only `current.*` is
  // needed.
  getOpportunityFunnel: (hours = 24) => fetchJson<{
    window_hours: number
    emitted_count: number
    setup_count: number
    active_setup_count: number
    setup_identity_basis: 'symbol+market+direction+scanner_family'
    reviewed_count: number
    still_open_count: number
    queued_count: number
    expired_count: number
    terminal_breakdown: {
      snapshot_superseded: number
      ttl_expired: number
      other_expired: number
      operator_trade: number
      operator_skip: number
      operator_watch: number
      deterministic_review: number
      other_reviewed: number
    }
    proposed_count: number
    cohort_basis: 'opportunities_emitted_in_window'
    current: {
      state: 'position_open' | 'human_approval_required' | 'operator_deferred' |
        'operator_queued' | 'ranker_pending' | 'ranker_cooldown' |
        'ranker_backpressure' | 'ranker_batch_limit' | 'ranker_position_duplicate' |
        'ranker_carrier_active' | 'ranker_duplicate' | 'ranker_already_evaluated' | 'ranker_filter_wait' |
        'ranker_evidence_unavailable' | 'no_active_candidate' | 'no_candidates'
      headline: string
      primary_stage: string
      reason_code: string
      reason: string
      next_trigger: string
      as_of: string
      policy_epoch_id: string | null
      counts: {
        open_positions: number
        open_opportunities: number
        queued_candidates: number
        deferred_candidates: number
        pending_proposals: number
        discovered_generations: number
        unique_setups: number
        active_setups: number
        superseded_generations: number
        ttl_expired: number
        // Layer-1 observability: reason-class breakdown over LIVE defers
        // (defer_until > now), 12h defer_rate, oldest live-defer age in
        // seconds, and the (currently static) queue capacity from
        // paper_trader._PENDING_SIGNAL_QUEUE_CAPACITY.
        market_quiet_defers: number
        candidate_specific_defers: number
        defer_rate: number
        oldest_defer_age_seconds: number | null
        queue_capacity: number | null
      }
      ranker: {
        delivery_epoch: string
        carrier_epoch: string
        counts: {
          open: number
          not_evaluated: number
          cooldown: number
          backpressure: number
          batch_limit: number
          carrier_active: number
          other_wait: number
        }
        displayed_candidates: number
        undisplayed_candidates: number
        last_run: {
          run_id: string
          trigger_type: string
          started_at: string
          ended_at: string | null
          status: string
          outputs: unknown[]
          errors: unknown[]
        } | null
        wake: {
          scheduler_active: boolean
          pending: boolean
          pending_sources: string[]
          burst_count?: number
          last_requested_at?: string | null
          last_dispatched_at?: string | null
          last_completed_at?: string | null
          last_result?: Record<string, unknown> | null
          next_run_at: string | null
        }
      }
      candidates: Array<{
        opportunity_id: string | null
        symbol: string | null
        direction: string | null
        market_type: string | null
        scanner_family: string | null
        trade_score: number | null
        confidence_score: number | null
        status: 'queued' | 'deferred' | 'ranker_pending' | 'ranker_cooldown' |
          'ranker_backpressure' | 'ranker_batch_limit' | 'ranker_position_duplicate' |
          'ranker_carrier_active' | 'ranker_duplicate' | 'ranker_already_evaluated' | 'ranker_filter_wait' |
          'ranker_evidence_unavailable'
        stage: string
        reason_code: string | null
        reason: string
        next_trigger: string
        queued_at: string | null
        valid_until: string | null
        expires_in_sec: number | null
        defer_until: string | null
        latest_event_at: string | null
        policy_epoch_id: string | null
        blocking_filter: string | null
        passed_filters: string[]
        passed_filter_count: number | null
        latest_ranker_run_id: string | null
        retry_at: string | null
        retry_in_sec: number | null
        source: string | null
      }>
      pipeline: Array<{
        stage: string
        label: string
        state: 'active' | 'idle' | 'passed' | 'working' | 'waiting' |
          'action_required' | 'reached' | 'not_reached' | 'not_available' | 'position_open'
        count: number | null
      }>
    }
    snapshot: {
      executable_count: number
      excluded_by_reason: Array<{
        reason: string
        count: number
        examples: Array<{
          symbol?: string | null
          direction?: string | null
          trade_score?: number | null
          scanner_family?: string | null
          skip_reason?: string | null
          opportunity_id?: string | null
          valid_until?: string | null
          expired_seconds?: number | null
        }>
      }>
      main_reason: string | null
      below_floor: boolean | null
      floor: number | null
      snapshot_at: string | null
      checked_at: string | null
      freshness: 'fresh' | 'stale' | 'missing' | 'unavailable'
      age_sec: number | null
      stale_after_sec: number
    }
    config: { min_confidence_to_trade: string }
    family_ttl_sec: Record<string, number>
    universe_snapshot_age_sec: number | null
  }>(`/analytics/opportunity-funnel?hours=${hours}`),

  // Fast subset of getOpportunityFunnel — returns just `current` + config +
  // family_ttl_sec. Used by the Blotter's Candidates panel, status-token
  // strip, and Overview's shortcut strip. See
  // docs/design/frontend/blotter_design.md#performance-backlog for why this
  // split exists.
  getOpportunityFunnelCurrentSnapshot: (hours = 24) =>
    fetchJson<{
      window_hours: number
      // Same shape as getOpportunityFunnel().current — inlined for the
      // fast endpoint's compact response. Fields not enumerated here (the
      // rich candidate/ranker sub-shape) are inherited from the full type
      // via structural typing at call sites.
      current: Awaited<ReturnType<typeof api.getOpportunityFunnel>>['current']
      config: { min_confidence_to_trade: string }
      family_ttl_sec: Record<string, number>
    }>(`/analytics/opportunity-funnel/current-snapshot?hours=${hours}`),

  getLearningFunnel: () => fetchJson<{
    knowledge_items_by_type: Array<{ item_type: string; count: number }>
    pi_actual_visibility: { layer3_lesson_hits: number; layer3_guards_available: number; note: string }
    ps_lessons: { total: number; by_status: Array<{ status: string; count: number }>; latest_created_at: string | null }
    serenity_opportunities_active: number
    contradictions: { checked: boolean; count: number; items: any[]; note: string }
  }>('/analytics/learning-funnel'),
  getLearningContribution: () =>
    fetchJson<LearningContributionProjection>('/analytics/learning-contribution'),
  getKillSwitch: () => fetchJson<{ active: boolean }>('/risk/kill-switch'),
  // Releasing the brake requires confirm_release=true (backend returns 422 otherwise).
  setKillSwitch: (active: boolean, reason = '', confirmRelease = false) =>
    post<{ active: boolean }>('/risk/kill-switch', { active, reason, confirm_release: confirmRelease }),
  getRiskPolicies: () => fetchJson<Record<'demo' | 'live', any>>('/settings/risk-policies'),
  replaceRiskPolicy: (
    mode: 'demo' | 'live',
    policy: any,
    expectedRevision: number,
    reason: string,
  ) => put<Record<'demo' | 'live', any>>(`/settings/risk-policies/${mode}`, {
    policy,
    expected_revision: expectedRevision,
    confirm_target_replacement: true,
    reason,
  }),
  copyRiskPolicy: (
    source: 'demo' | 'live',
    target: 'demo' | 'live',
    sourceRevision: number,
    targetRevision: number,
    reason: string,
  ) => post<Record<'demo' | 'live', any>>('/settings/risk-policies/copy', {
    source,
    target,
    source_revision: sourceRevision,
    target_revision: targetRevision,
    confirm_target_replacement: true,
    reason,
  }),
  resetRiskPolicy: (
    mode: 'demo' | 'live',
    expectedRevision: number,
    reason: string,
  ) => post<Record<'demo' | 'live', any>>(`/settings/risk-policies/${mode}/reset-defaults`, {
    expected_revision: expectedRevision,
    confirm_target_replacement: true,
    reason,
  }),
  getRiskBrakes: () => fetchJson<{
    global_active: boolean; demo_active: boolean; live_active: boolean
  }>('/risk/brakes'),
  setRiskBrake: (
    scope: 'global' | 'demo' | 'live',
    active: boolean,
    reason = '',
    confirmRelease = false,
  ) => post(`/risk/brakes/${scope}`, {
    active,
    reason,
    confirm_release: confirmRelease,
  }),
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
    live_family_min_samples: number; live_family_min_win_rate_pct: number
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
    persona_auto_advance_enabled: boolean
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
  setExchangeMarkets: (body: { spot: boolean; usdt_futures: boolean; paper_execution_mode?: string; confirm_exchange_divergence?: boolean }) =>
    post<Record<string, any>>('/settings/exchange-markets', body),
  setPrimaryExchange: (exchange_id: string) =>
    post<{ exchange_id: string; 'exchange.name': string; 'exchange.paper_execution_mode': string; 'market_data.ccxt_exchange': string; 'risk.allowed_exchange': string }>('/settings/primary-exchange', { exchange_id }),
  updateRuntimeConfig: (body: Partial<{
    scheduler_enabled: boolean
    git_enabled: boolean
    auto_reload: boolean
    runtime_continuity_enabled: boolean
    runtime_continuity_lock_ttl_sec: number
    runtime_continuity_shadow_port_offset: number
  }>) =>
    post<{ updated: Record<string, any> }>('/settings/runtime', body),
  updateAppConfig: (body: Partial<{ auto_open_browser: boolean; equity_curve_mode: string }>) =>
    post<{ updated: Record<string, any> }>('/settings/app', body),
  updatePiDaemonConfig: (body: Partial<{ enabled: boolean; debounce_sec: number; cooldown_sec: number; heartbeat_sec: number }>) =>
    post<{ updated: Record<string, any> }>('/settings/pi_daemon', body),
  updateSerenityConfig: (body: Partial<{ enabled: boolean; auto_promote: boolean; min_score_for_pi: number; stop_loss_pct: number; max_hold_days: number }>) =>
    post<{ updated: Record<string, any> }>('/settings/serenity', body),
  updateControllerConfig: (body: Partial<{ shadow_enabled: boolean; observe_interval_hours: number; min_samples_gate: number; max_step: number }>) =>
    post<{ updated: Record<string, any> }>('/settings/controller', body),
  updateLearningConfig: (body: Partial<{
    contribution_evaluation_enabled: boolean; lifecycle_automation_enabled: boolean
    paper_influence_enabled: boolean; paper_influence_paths: string[]; auto_refreeze: boolean
    evaluation_interval_minutes: number; paper_active_allocation_pct: number
  }>) => post<{ updated: Record<string, any>; learning: Record<string, any> }>('/settings/learning', body),
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
    startup_behavior_gate_enabled: boolean; startup_gate_recheck_interval_sec: number
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

  // Data
  getAgentRuns: () => fetchJson<any[]>('/agent-runs'),
  getAgentRunToolCalls: (runId: string) => fetchJson<any[]>(`/agent-runs/${runId}/tool-calls`),
  // #72 fix: the per-step reasoning trace backend endpoint exists at
  // /api/agent-runs/{run_id}/steps (server/routes/data.py:236). The
  // client method was missing so AgentActivity was displaying the
  // "not yet available" placeholder. Frontend surface can now consume it.
  getAgentRunSteps: (runId: string) => fetchJson<any[]>(`/agent-runs/${runId}/steps`),
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
  // #95: removed dead setStrategyVenue() — it PATCHed /strategies/{id}/venue, a route
  // deleted with the rules engine (Position C); no backend handler, no TSX caller.
  getKnowledge: (type = 'research_note') => fetchJson<any[]>(`/knowledge?type=${encodeURIComponent(type)}`),
  getStrategyMemory: () => fetchJson<any[]>('/knowledge/strategy-memory'),
  getDreamingLogs: () => fetchJson<any[]>('/knowledge/dreaming-logs'),
  getPostTradeReviews: () => fetchJson<any[]>('/knowledge?type=post_trade_review'),
  getKnowledgeContent: (id: string) => fetchJson<{ id: string; content: string; error?: string }>(`/knowledge/${id}/content`),

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
  // #86 fix: backend is @router.patch("/preferences") (prediction.py:232),
  // client was POSTing → 405 Method Not Allowed on every save. Use PATCH.
  updatePredictionPreferences: (body: { categories?: string[] }) =>
    fetchJson<any>('/prediction/preferences', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
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
  getAccountEquityHistory: (days?: number) => fetchJson<AccountEquityHistory>(`/market/account-equity-history${days ? `?days=${days}` : ''}`),

  // Capital events (deposit/withdraw tracking)
  getNetDeposits: () => fetchJson<{ net_deposits_usdt: number; first_event_at: string | null }>('/capital/net-deposits'),
  getCapitalEvents: () => fetchJson<any[]>('/capital/events'),
  createCapitalEvent: (body: { broker_id: string; event_type: string; amount_usdt: number; note?: string }) =>
    post<any>('/capital/events', body),

  // Local paper account (available only in Paper internal / No exchange mode)
  getPaperAccount: () => fetchJson<any>('/settings/paper-account'),
  creditPaperAccount: (amount_usdt: number, note?: string) =>
    post<any>('/settings/paper-account/credit', { amount_usdt, note }),
  debitPaperAccount: (amount_usdt: number, note?: string) =>
    post<any>('/settings/paper-account/debit', { amount_usdt, note }),
  setPaperAccountBalance: (cash_balance_usdt: number, note?: string) =>
    post<any>('/settings/paper-account/set-balance', { cash_balance_usdt, note }),
  closeAllPaperPositions: () => post<any>('/settings/paper-account/close-all'),
  closePaperSpotPositions: () => post<any>('/settings/paper-account/close-spot'),

  // Audit / Activity
  getAuditEvents: (limit = 50) => fetchJson<any[]>(`/audit/events?limit=${limit}`),

  // Market snapshots (watchlist)
  getMarketSnapshots: (symbol?: string, limit = 10) =>
    fetchJson<any[]>(`/market/snapshots${symbol ? `?symbol=${encodeURIComponent(symbol)}&limit=${limit}` : `?limit=${limit}`}`),

  // Macro indicators (VIX, DXY, SPY, BTC dominance, risk regime)
  getMacro: () => fetchJson<{ available: boolean; data: any }>('/market/macro'),
  getCollectorHealth: () => fetchJson<import('../types').CollectorHealthReport>('/market/collector-health'),
  getDataCoverage: () => fetchJson<import('../types').MarketDataCoverageReport>('/market/data-coverage'),

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
    confirm_exchange_divergence: boolean;
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
    active_universe_backfill_enabled: boolean;
    active_universe_backfill_interval_minutes: number;
    active_universe_backfill_max_pairs_per_run: number;
  }>) => post<any>('/settings/historical_data', body),

  // Event trigger config
  updateEventTriggerConfig: (body: Partial<{
    enabled: boolean; price_move_pct_threshold: number; volume_surge_multiplier: number;
    watch_universe_members: boolean; trigger_strategy_design: boolean;
    strategy_design_min_move_pct: number;
  }>) => post<any>('/settings/event_triggers', body),

  // Network / proxy config
  updateNetworkConfig: (body: Partial<{ proxy_protocol: string; proxy_host: string; proxy_port: number; enforce: boolean; no_proxy: string; proxy_url: string }>) =>
    post<{ updated: Record<string, any>; network: Record<string, any> }>('/settings/network', body),
  testNetworkProxy: () =>
    post<{ reachable: boolean; latency_ms: number; error: string | null }>('/settings/network/test'),

  // Trade Analytics panel
  getTradeStats: () => fetchJson<TradeStats>('/analytics/trade-stats'),
  getStrategyKpis: () => fetchJson<OutcomeCohort & { strategies: Record<string, { trade_count: number; win_rate: number; total_pnl: number }> }>('/analytics/strategy-kpis'),
  getEquityCurve: (days?: number) => fetchJson<OutcomeCohort & {
    default_curve: 'genuine_verified';
    raw_curve_label: string;
    genuine_curve_label: string;
    raw_recorded_points: EquityPoint[];
    genuine_points: EquityPoint[];
    points: EquityPoint[];
    current_unrealized: number;
    current_mark_point: (EquityPoint & {
      closed_raw_pnl: number;
      current_unrealized: number;
    }) | null;
    total_realized: number | null;
    raw_total_recorded_realized: number | null;
  }>(`/analytics/equity-curve${days ? `?days=${days}` : ''}`),
  getTradeInsights: () => fetchJson<TradeInsights>('/analytics/trade-insights'),
  getThesisLineage: (params: {
    days?: number
    position_id?: string
    order_id?: string
    proposal_id?: string
    opportunity_id?: string
    limit?: number
  }) => {
    const q = new URLSearchParams()
    if (params.days != null) q.set('days', String(params.days))
    if (params.position_id) q.set('position_id', params.position_id)
    if (params.order_id) q.set('order_id', params.order_id)
    if (params.proposal_id) q.set('proposal_id', params.proposal_id)
    if (params.opportunity_id) q.set('opportunity_id', params.opportunity_id)
    if (params.limit != null) q.set('limit', String(params.limit))
    return fetchJson<{ records: ThesisLineageRecord[] }>(`/analytics/thesis-lineage?${q.toString()}`)
  },
  getGoLiveScorecard: () => fetchJson<GoLiveScorecard>('/analytics/go-live-scorecard'),
  getFilterController: () => fetchJson<{
    generated_at: string
    min_samples: number
    shadow_only: boolean
    signal_note?: string
    verdict_counts: Record<string, number>
    recommendations: Array<{
      filter_name: string; scanner_family: string; stage: string; current_value: string
      verdict: string; suggested_direction: string; error_signal: number | null
      n_samples: number; reasoning: string
    }>
    // Cells whose overrestrictive_suspect verdict has held across N+ consecutive
    // controller runs — the human-review signal. Not a Learning hypothesis.
    persistent_recommendations: Array<{
      filter_name: string; scanner_family: string
      direction: string | null; market_type: string | null
      horizon: string | null; filter_version: string | null
      consecutive_suspect_runs: number; runs_analyzed: number
      first_suspect_at: string; last_suspect_at: string
      latest_error_signal: number | null; latest_reasoning: string
      latest_run_id: string
    }>
    persistent_config: { min_consecutive_runs: number; max_runs_lookback: number }
    report_only_filters: Array<{ name: string; stage: string; reason: string }>
    tunable_filter_count: number
    total_filter_count: number
  }>('/analytics/filter-controller'),
  getCostSummary: () => fetchJson<CostSummary>('/analytics/cost-summary'),
  getConfidenceCalibration: () => fetchJson<CalibrationReport>('/analytics/confidence-calibration'),
  getReasoningFeedback: () => fetchJson<ReasoningFeedback>('/analytics/reasoning-feedback'),

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
  win_rate: number | null
  avg_hold_hours: number | null
  expectancy_pct: number | null
  total_pnl: number | null
}

export interface TradeStatsFamily {
  family: string
  trade_count: number
  win_rate: number | null
  avg_hold_hours: number | null
  expectancy_pct: number | null
}

export interface HoldBucket {
  bucket: string
  count: number
  pct: number
}

export interface OutcomeCohort {
  raw_event_count: number | null
  eligible_outcome_count: number | null
  excluded_outcome_count: number | null
  outcome_exclusion_reasons: Record<string, number>
  outcome_semantics_version: string
  cohort_state: 'available' | 'insufficient' | 'unavailable'
  raw_recorded_realized_pnl: number | null
  raw_economics_unavailable_count: number | null
  eligible_realized_pnl: number | null
  clean_window_anchor_id: string
  clean_window_anchor_iso: string
}

export interface TradeStats extends OutcomeCohort {
  by_source: TradeStatsSource[]
  by_family: TradeStatsFamily[]
  hold_distribution: HoldBucket[]
  totals: {
    trade_count: number
    win_rate: number | null
    avg_hold_hours: number | null
    median_hold_hours: number | null
    total_pnl: number | null
  }
}

// ── Trade Insights (comprehensive charts) ────────────────────────────────────

export interface EquityPoint {
  time: string
  pnl: number
  cumulative_pnl: number
}

export interface AccountEquityHistory {
  source: 'local_paper_ledger' | 'exchange_account_snapshot'
  state: 'available' | 'unavailable'
  points: { time: string; equity_usdt: number }[]
  adjusted_points: {
    time: string
    raw_equity_usdt: number
    cumulative_net_flow_usdt: number
    adjusted_equity_usdt: number
  }[]
  capital_flow_markers: {
    id: string
    time: string
    broker_id: string
    event_type: 'deposit' | 'withdraw'
    amount_usdt: number
    signed_amount_usdt: number
  }[]
  adjustment: {
    state: 'available' | 'unavailable'
    method: 'visible_window_net_flow_adjusted_usdt'
    scope: 'aggregate_tracked_account_equity'
    baseline_at: string | null
    note: string
  }
  as_of: string | null
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

export interface TradeInsights extends OutcomeCohort {
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

export interface CostSummary extends OutcomeCohort {
  total_entry_fees: number | null
  total_exit_fees: number | null
  total_fees: number | null
  total_slippage: number | null
  total_costs: number | null
  raw_recorded_costs: {
    total_entry_fees: number | null
    total_exit_fees: number | null
    total_fees: number | null
    total_slippage: number | null
    total_costs: number | null
  }
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
