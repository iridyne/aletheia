import assert from 'node:assert/strict'
import test from 'node:test'

import { EventRouter } from '../src/event_router.js'
import { BackendClient } from '../src/backend_client.js'
import { OperatorLoop } from '../src/operator_loop.js'
import { RecommendationEngine } from '../src/recommendation_engine.js'
import { RepairEngine } from '../src/repair_engine.js'
import { SessionRuntime } from '../src/session_runtime.js'
import type {
  AgentRunSummary,
  BackendGatewayClient,
  GatewayHealthSnapshot,
  GatewayStatusSnapshot,
  OperatorArtifactCreate,
  OperatorCommandExecution,
  OperatorContextQuery,
  OperatorContextSnapshot,
  OperatorEventCreate,
  OperatorHeartbeat,
  OperatorMessageCreate,
  PersistedOperatorCommand,
  PersistedOperatorEvent,
  OperatorSessionDetail,
  OperatorSessionSummary,
  OperatorRecommendation,
  PersistedOperatorStatus,
  PersistedOperatorTask,
  OperatorSessionCreate,
  RiskSummary,
  PortfolioSnapshotRecord,
  PortfolioSummary,
  StrategySummary,
  OperatorTaskCreate,
  OperatorTaskUpdate,
} from '../src/schemas.js'
import { TaskRuntime } from '../src/task_runtime.js'

function makeRecommendation(overrides: Partial<OperatorRecommendation> = {}): OperatorRecommendation {
  return {
    id: 'health:market_data_stale',
    kind: 'health_attention_needed',
    severity: 'high',
    summary: 'Gateway health issue: market_data_stale:45m',
    taskType: 'diagnose_system_health',
    eventType: 'operator_degraded',
    metadata: {
      issue: 'market_data_stale:45m',
    },
    ...overrides,
  }
}

function makeSnapshot(
  overrides: {
    health?: Partial<GatewayHealthSnapshot>
    status?: Partial<GatewayStatusSnapshot>
  } = {},
): { health: GatewayHealthSnapshot; status: GatewayStatusSnapshot } {
  return {
    health: {
      status: 'healthy',
      issues: [],
      version: 'test',
      ...overrides.health,
    },
    status: {
      gateway_running: true,
      paused: false,
      permission_mode: 'ReadOnly',
      ...overrides.status,
    },
  }
}

function makeFakeBackend(snapshot: { health: GatewayHealthSnapshot; status: GatewayStatusSnapshot }) {
  const calls = {
    heartbeats: [] as OperatorHeartbeat[],
    createdTasks: [] as OperatorTaskCreate[],
    updatedTasks: [] as Array<{ taskId: string; payload: OperatorTaskUpdate }>,
    createdEvents: [] as OperatorEventCreate[],
    createdArtifacts: [] as OperatorArtifactCreate[],
    createdSessions: [] as OperatorSessionCreate[],
    createdMessages: [] as OperatorMessageCreate[],
    executedCommands: [] as Array<{ commandName: string; payload: OperatorCommandExecution }>,
    requestedContexts: [] as OperatorContextQuery[],
    compactions: [] as string[],
    startedAgents: [] as string[],
    gatewayStatusReads: 0,
    healthReads: 0,
    dreamingSummaryReads: 0,
    reviewSummaryReads: 0,
    strategySummaryReads: 0,
    riskSummaryReads: 0,
    portfolioSummaryReads: 0,
    portfolioHistoryReads: 0,
    agentRunReads: 0,
    sessionDetailReads: 0,
    commandReads: 0,
    taskReads: 0,
    taskReadRequests: [] as Array<{ status?: string; limit?: number }>,
  }

  const backend: BackendGatewayClient = {
    async getOperatorStatus() {
      return {
        agent_id: 'operator',
        status: 'offline',
        mode: 'Observing',
        permission_mode: 'ReadOnly',
        is_busy: false,
        current_task_id: null,
        current_intent: null,
        queue_size: 0,
        last_seen_at: null,
        last_success_at: null,
        last_error: null,
        model_provider: 'pi-shell',
        session_id: 'operator-main',
        stale: false,
        stale_after_sec: 45,
      } satisfies PersistedOperatorStatus
    },
    async getOperatorEvents() {
      return []
    },
    async getOperatorSession() {
      calls.sessionDetailReads += 1
      return {
        session: {
          id: 'operator-main',
          user_input: '[operator session]',
          intent: null,
          command: null,
          response: null,
          permitted: 1,
          duration_ms: 0,
          created_at: new Date().toISOString(),
          message_count: 0,
          command_count: 0,
        },
        messages: [],
        commands: [],
        artifacts: [],
      } satisfies OperatorSessionDetail
    },
    async getOperatorSessions() {
      return [
        {
          id: 'operator-main',
          user_input: '[operator session]',
          intent: null,
          command: null,
          response: null,
          permitted: 1,
          duration_ms: 0,
          created_at: new Date().toISOString(),
          message_count: 0,
          command_count: 0,
          kind: 'command_session',
          parent_session_id: null,
          title: 'Operator Main',
        },
      ] satisfies OperatorSessionSummary[]
    },
    async getOperatorCommands() {
      calls.commandReads += 1
      return [] satisfies PersistedOperatorCommand[]
    },
    async getOperatorTasks(status, limit) {
      calls.taskReads += 1
      calls.taskReadRequests.push({ status, limit })
      return [] satisfies PersistedOperatorTask[]
    },
    async createSession(payload) {
      calls.createdSessions.push(payload)
      return {
        id: payload.id,
        user_input: payload.user_input,
        intent: null,
        command: null,
        response: null,
        permitted: 1,
        duration_ms: null,
        created_at: new Date().toISOString(),
        message_count: 0,
        command_count: 0,
        kind: payload.kind,
        parent_session_id: payload.parent_session_id || null,
        title: payload.title || null,
      } satisfies OperatorSessionSummary
    },
    async createArtifact(payload) {
      calls.createdArtifacts.push(payload)
      return {
        id: `artifact-${calls.createdArtifacts.length}`,
        task_id: payload.task_id || null,
        session_id: payload.session_id || null,
        artifact_type: payload.artifact_type,
        title: payload.title,
        content: payload.content,
        created_at: new Date().toISOString(),
      }
    },
    async getDreamingSummary() {
      calls.dreamingSummaryReads += 1
      const today = new Date().toISOString().slice(0, 10)
      return {
        recent_research_notes: [],
        recent_strategy_hypotheses: [],
        deprecated_memories: [],
        recent_dreaming_logs: [{ id: 'dream-log-1' }],
        recent_dreaming_runs: [{ id: 'dream-run-1' }],
        recent_daily_reviews: [{ id: 'daily-review-today', created_at: `${today}T01:00:00Z` }],
      }
    },
    async getReviewSummary() {
      calls.reviewSummaryReads += 1
      return {
        recent_closed_positions: [],
        recent_post_trade_reviews: [],
        recent_post_trade_reviewer_runs: [],
        missing_review_strategy_ids: [],
      }
    },
    async getStrategySummary() {
      calls.strategySummaryReads += 1
      return {
        active_paper_strategies: [],
        under_review_strategies: [],
        paused_paper_strategies: [],
        live_candidate_strategies: [],
        recent_strategy_evaluations: [],
        needs_operator_evaluation_strategy_ids: [],
        recent_strategy_promotion_proposals: [],
        needs_operator_promotion_proposal_strategy_ids: [],
      } satisfies StrategySummary
    },
    async getRiskSummary() {
      calls.riskSummaryReads += 1
      return {
        recent_rejected_decisions: [],
        recent_risk_boundary_proposals: [],
        needs_operator_risk_boundary_proposals: [],
      } satisfies RiskSummary
    },
    async getPortfolioSummary() {
      calls.portfolioSummaryReads += 1
      return {
        total_value_usdt: 0,
        unrealized_pnl: 0,
        realized_pnl: 0,
        today_pnl: 0,
        positions_count: 0,
        exposure_by_symbol: {},
      } satisfies PortfolioSummary
    },
    async getPortfolioHistory() {
      calls.portfolioHistoryReads += 1
      return [] satisfies PortfolioSnapshotRecord[]
    },
    async getOperatorExtensions() {
      return []
    },
    async getAgentRuns() {
      calls.agentRunReads += 1
      return [
        {
          id: 'run-market-observer-fresh',
          agent_name: 'market_observer',
          trigger_type: 'scheduled',
          permission_mode: 'Observe',
          started_at: new Date().toISOString(),
          ended_at: new Date().toISOString(),
          status: 'completed',
          model_provider: null,
          model_name: null,
          tools_called: '[]',
          outputs_created: '[]',
          errors: '[]',
          cost_estimate: null,
        },
      ] satisfies AgentRunSummary[]
    },
    async getOperatorContext(payload) {
      calls.requestedContexts.push(payload)
      const profile =
        payload.profile ||
        (payload.session_id?.startsWith('strategy-')
          ? 'trading_context'
          : payload.session_id?.startsWith('review-')
            ? 'trading_context'
            : payload.session_id?.startsWith('dreaming-')
              ? 'knowledge_context'
              : payload.session_id?.startsWith('diagnostic-')
                ? 'repair_context'
                : 'operational_context')
      return {
        profile,
        session_id: payload.session_id || null,
        session_kind:
          profile === 'knowledge_context'
            ? 'dreaming_session'
            : profile === 'repair_context'
              ? 'diagnostic_session'
              : profile === 'trading_context'
                ? 'strategy_session'
                : 'command_session',
        sections:
          profile === 'knowledge_context'
            ? ['system', 'agents', 'knowledge', 'strategies', 'operator_state']
            : profile === 'repair_context'
              ? ['system', 'agents', 'trading', 'risk', 'errors', 'operator_state']
              : ['system', 'market', 'trading', 'risk', 'strategies', 'operator_state'],
        summary: `Snapshot for ${profile}.`,
        context: {
          system: { healthy: true, paused: false },
          operator_state: { current_mode: 'Observing' },
        },
      } satisfies OperatorContextSnapshot
    },
    async createMessage(payload) {
      calls.createdMessages.push(payload)
      return { id: `message-${calls.createdMessages.length}` }
    },
    async getGatewayStatus() {
      calls.gatewayStatusReads += 1
      return snapshot.status
    },
    async getHealth() {
      calls.healthReads += 1
      return snapshot.health
    },
    async postHeartbeat(heartbeat) {
      calls.heartbeats.push(heartbeat)
      return true
    },
    async createTask(payload) {
      calls.createdTasks.push(payload)
      return { id: `task-${calls.createdTasks.length}` }
    },
    async updateTask(taskId, payload) {
      calls.updatedTasks.push({ taskId, payload })
      return { id: taskId }
    },
    async createEvent(payload) {
      calls.createdEvents.push(payload)
      return { id: `event-${calls.createdEvents.length}` }
    },
    async executeOperatorCommand(commandName, payload) {
      calls.executedCommands.push({ commandName, payload })
      return {
        command: commandName,
        permitted: true,
        response: 'created: true',
        result: {
          created: true,
          title: String(payload.parameters?.title || ''),
        },
        action: null,
        risk_level: 'low_write',
        needs_confirmation: false,
        session_id: payload.session_id || 'operator-main',
      }
    },
    async compactOperatorMemory(date) {
      calls.compactions.push(date)
      return {
        date,
        path: `C:/tmp/daily_summary_${date.replaceAll('-', '')}.md`,
        artifact_id: `artifact-${calls.compactions.length}`,
        created: true,
      }
    },
    async startAgentRun(agentName) {
      calls.startedAgents.push(agentName)
      return {
        run_id: `run-${calls.startedAgents.length}`,
        status: 'completed',
        outputs: ['dreaming_log:abc123'],
        errors: [],
      }
    },
  }

  return { backend, calls }
}

function makeTask(
  overrides: Partial<PersistedOperatorTask> = {},
): PersistedOperatorTask {
  return {
    id: 'task-1',
    task_type: 'diagnose_system_health',
    status: 'running',
    priority: 'normal',
    created_by: 'operator_loop',
    assigned_to: 'operator',
    input_data: {},
    output_data: null,
    error: null,
    started_at: new Date().toISOString(),
    completed_at: null,
    created_at: new Date().toISOString(),
    ...overrides,
  }
}

function waitMs(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

test('backend client includes method and route in gateway request failures', async () => {
  const originalFetch = globalThis.fetch
  try {
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ detail: 'No route matches operator status' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch

    const client = new BackendClient('http://127.0.0.1:8787')

    await assert.rejects(
      () => client.getOperatorStatus(),
      /gateway_request_failed:404:GET \/api\/operator\/status:No route matches operator status/,
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('backend client can request bounded terminal task history for recovery deduplication', async () => {
  const originalFetch = globalThis.fetch
  let requestedUrl = ''
  try {
    globalThis.fetch = (async (url: RequestInfo | URL) => {
      requestedUrl = String(url)
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }) as typeof fetch

    const client = new BackendClient('http://127.0.0.1:8787')
    await client.getOperatorTasks(undefined, 500)
    assert.equal(requestedUrl, 'http://127.0.0.1:8787/api/operator/tasks?limit=500')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('operator recovery requests the complete queued backlog in addition to recent terminal history', async () => {
  const { backend, calls } = makeFakeBackend(makeSnapshot())
  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()

  assert.ok(calls.taskReadRequests.some(request => request.status === 'queued' && request.limit === 500))
  assert.ok(calls.taskReadRequests.some(request => request.status === undefined && request.limit === 500))
})

test('recommendation engine converts degraded health into actionable diagnostics', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot({
      health: {
        status: 'degraded',
        issues: ['market_data_stale:45m'],
      },
    }),
  )

  const healthRecommendation = recommendations.find(item => item.kind === 'health_attention_needed')
  assert.ok(healthRecommendation)
  assert.equal(healthRecommendation?.taskType, 'diagnose_system_health')
  assert.equal(healthRecommendation?.eventType, 'operator_degraded')
})

test('operator loop in shadow runtime emits runtime-aware heartbeat and suppresses side-effect tasks', async () => {
  const snapshot = makeSnapshot({
    health: {
      status: 'degraded',
      issues: ['market_data_stale:45m'],
    },
  })
  const { backend, calls } = makeFakeBackend(snapshot)
  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'PaperAuto',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
      runtimeId: 'runtime-shadow-1',
      runtimeMode: 'shadow',
      runtimeState: 'shadowing',
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.equal(calls.createdTasks.length, 0)
  assert.equal(calls.createdEvents.length, 0)
  assert.equal(calls.executedCommands.length, 0)
  assert.equal(calls.heartbeats.length, 1)
  assert.equal(calls.heartbeats[0].runtime_id, 'runtime-shadow-1')
  assert.equal(calls.heartbeats[0].runtime_mode, 'shadow')
  assert.equal(calls.heartbeats[0].runtime_state, 'shadowing')
  assert.equal(calls.gatewayStatusReads, 1)
  assert.equal(calls.healthReads, 1)
})

test('operator loop background heartbeat stays fresh while runOnce is still in flight', async () => {
  const { backend, calls } = makeFakeBackend(makeSnapshot())
  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 1,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: false,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  ;(loop as any).recovered = true
  let releaseRun: (() => void) | null = null
  let runCalls = 0
  ;(loop as any).runOnce = async () => {
    runCalls += 1
    await new Promise<void>(resolve => {
      releaseRun = resolve
    })
    return true
  }

  const started = loop.start()
  await waitMs(1200)

  assert.equal(runCalls, 1)
  assert.ok(
    calls.heartbeats.length >= 1,
    'heartbeat pump should persist operator state before a long runOnce finishes',
  )

  releaseRun?.()
  await started
  await loop.stop()
})

test('recommendation engine marks dreaming as due when no recent dreaming activity exists', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [],
      recent_dreaming_runs: [],
    },
  )

  const dreaming = recommendations.find(item => item.kind === 'dreaming_due')
  assert.ok(dreaming)
  assert.equal(dreaming?.taskType, 'run_dreaming')
})

test('recommendation engine marks post-trade review as due when closed strategies lack review coverage', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [{ id: 'dream-log-1' }],
      recent_dreaming_runs: [{ id: 'dream-run-1' }],
    },
    {
      recent_closed_positions: [{ id: 'pos-1', strategy_id: 'strat-1' }],
      recent_post_trade_reviews: [],
      recent_post_trade_reviewer_runs: [],
      missing_review_strategy_ids: ['strat-1'],
    },
  )

  const review = recommendations.find(item => item.kind === 'review_due')
  assert.ok(review)
  assert.equal(review?.taskType, 'generate_review')
})

test('recommendation engine surfaces failed and stuck operator tasks from backend truth', () => {
  const engine = new RecommendationEngine()
  const fortyFiveMinutesAgo = new Date(Date.now() - 45 * 60 * 1000).toISOString()

  const recommendations = engine.list(
    makeSnapshot(),
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    [
      makeTask({
        id: 'task-failed-1',
        task_type: 'generate_review',
        status: 'failed',
        error: 'review generation failed',
        completed_at: new Date().toISOString(),
      }),
      makeTask({
        id: 'task-stuck-1',
        task_type: 'run_dreaming',
        status: 'running',
        started_at: fortyFiveMinutesAgo,
        created_at: fortyFiveMinutesAgo,
      }),
    ],
  )

  const failed = recommendations.find(item => item.kind === 'failed_tasks_present')
  const stuck = recommendations.find(item => item.kind === 'stuck_tasks_present')
  assert.ok(failed)
  assert.match(failed?.summary || '', /failed operator task/i)
  assert.ok(stuck)
  assert.match(stuck?.summary || '', /appears stuck/i)
})

test('recommendation engine keeps historical task failures as audit history, not active supervision alerts', () => {
  const engine = new RecommendationEngine()
  const old = new Date(Date.now() - 31 * 60 * 1000).toISOString()

  const recommendations = engine.list(
    makeSnapshot(),
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    [
      makeTask({
        id: 'historical-failure',
        status: 'failed',
        error: 'transient gateway failure',
        completed_at: old,
        created_at: old,
      }),
    ],
  )

  assert.equal(recommendations.some(item => item.kind === 'failed_tasks_present'), false)
})

test('recommendation engine surfaces queued operator task backlog from backend truth', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot(),
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    [
      makeTask({
        id: 'task-queued-1',
        task_type: 'generate_review',
        status: 'queued',
      }),
      makeTask({
        id: 'task-queued-2',
        task_type: 'run_dreaming',
        status: 'queued',
      }),
    ],
  )

  const queued = recommendations.find(item => item.kind === 'queued_tasks_present')
  assert.ok(queued)
  assert.match(queued?.summary || '', /queued operator task/i)
})

test('recommendation engine escalates aged queued backlog into queue review recommendation', () => {
  const engine = new RecommendationEngine()
  const fortyMinutesAgo = new Date(Date.now() - 40 * 60 * 1000).toISOString()

  const recommendations = engine.list(
    makeSnapshot(),
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    [
      makeTask({
        id: 'task-queued-review-1',
        task_type: 'generate_review',
        status: 'queued',
        created_at: fortyMinutesAgo,
        priority: 'high',
      }),
    ],
  )

  const queueReview = recommendations.find(item => item.kind === 'queue_review_needed')
  assert.ok(queueReview)
  assert.match(queueReview?.summary || '', /queue review/i)
  assert.equal(queueReview?.severity, 'high')
  assert.equal(queueReview?.metadata?.review_stage, 'urgent')
  assert.equal(queueReview?.metadata?.suggested_review_cadence_sec, 60)
})

test('recommendation engine marks queue review attention stage when backlog first crosses review threshold', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot(),
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    [
      makeTask({
        id: 'task-queued-attention-1',
        task_type: 'generate_review',
        status: 'queued',
      }),
      makeTask({
        id: 'task-queued-attention-2',
        task_type: 'generate_review',
        status: 'queued',
      }),
      makeTask({
        id: 'task-queued-attention-3',
        task_type: 'run_dreaming',
        status: 'queued',
      }),
    ],
  )

  const queueReview = recommendations.find(item => item.kind === 'queue_review_needed')
  assert.ok(queueReview)
  assert.equal(queueReview?.severity, 'medium')
  assert.equal(queueReview?.metadata?.review_stage, 'attention')
  assert.equal(queueReview?.metadata?.suggested_review_cadence_sec, 300)
})

test('recommendation engine marks market scan refresh as due when market observer activity is stale', () => {
  const engine = new RecommendationEngine()
  const fiftyMinutesAgo = new Date(Date.now() - 50 * 60 * 1000).toISOString()

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [{ id: 'dream-log-1' }],
      recent_dreaming_runs: [{ id: 'dream-run-1' }],
    },
    {
      recent_closed_positions: [],
      recent_post_trade_reviews: [],
      recent_post_trade_reviewer_runs: [],
      missing_review_strategy_ids: [],
    },
    {
      active_paper_strategies: [],
      under_review_strategies: [],
      paused_paper_strategies: [],
      live_candidate_strategies: [],
      recent_strategy_evaluations: [],
      needs_operator_evaluation_strategy_ids: [],
      recent_strategy_promotion_proposals: [],
      needs_operator_promotion_proposal_strategy_ids: [],
    },
    [
      {
        id: 'run-market-observer-stale',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: fiftyMinutesAgo,
        ended_at: fiftyMinutesAgo,
        status: 'completed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '[]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[],
  )

  const marketScan = recommendations.find(item => item.kind === 'market_scan_refresh_due')
  assert.ok(marketScan)
  assert.equal(marketScan?.taskType, 'run_market_scan')
  assert.equal(marketScan?.severity, 'medium')
})

test('recommendation engine marks portfolio snapshot refresh as due when open positions lack a fresh snapshot', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [{ id: 'dream-log-1' }],
      recent_dreaming_runs: [{ id: 'dream-run-1' }],
    },
    {
      recent_closed_positions: [],
      recent_post_trade_reviews: [],
      recent_post_trade_reviewer_runs: [],
      missing_review_strategy_ids: [],
    },
    {
      active_paper_strategies: [],
      under_review_strategies: [],
      paused_paper_strategies: [],
      live_candidate_strategies: [],
      recent_strategy_evaluations: [],
      needs_operator_evaluation_strategy_ids: [],
      recent_strategy_promotion_proposals: [],
      needs_operator_promotion_proposal_strategy_ids: [],
    },
    [
      {
        id: 'run-market-observer-fresh',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        status: 'completed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '[]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[],
    [],
    {
      recent_rejected_decisions: [],
      recent_risk_boundary_proposals: [],
      needs_operator_risk_boundary_proposals: [],
    },
    [],
    [],
    {
      total_value_usdt: 605,
      unrealized_pnl: 5,
      realized_pnl: 0,
      today_pnl: 5,
      positions_count: 1,
      exposure_by_symbol: { BTCUSDT: 605 },
    },
    [],
  )

  const portfolioRefresh = recommendations.find(item => item.kind === 'portfolio_snapshot_refresh_due')
  assert.ok(portfolioRefresh)
  assert.equal(portfolioRefresh?.taskType, 'run_portfolio_snapshot')
  assert.equal(portfolioRefresh?.metadata?.open_positions_count, 1)
})

test('recommendation engine marks failed agent rerun as due when one recent scheduled run failed without recovery', () => {
  const engine = new RecommendationEngine()
  const twentyMinutesAgo = new Date(Date.now() - 20 * 60 * 1000).toISOString()
  const fiveMinutesAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString()

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [{ id: 'dream-log-1' }],
      recent_dreaming_runs: [{ id: 'dream-run-1' }],
    },
    {
      recent_closed_positions: [],
      recent_post_trade_reviews: [],
      recent_post_trade_reviewer_runs: [],
      missing_review_strategy_ids: [],
    },
    {
      active_paper_strategies: [],
      under_review_strategies: [],
      paused_paper_strategies: [],
      live_candidate_strategies: [],
      recent_strategy_evaluations: [],
      needs_operator_evaluation_strategy_ids: [],
      recent_strategy_promotion_proposals: [],
      needs_operator_promotion_proposal_strategy_ids: [],
    },
    [
      {
        id: 'run-market-observer-fresh',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: fiveMinutesAgo,
        ended_at: fiveMinutesAgo,
        status: 'completed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '[]',
        cost_estimate: null,
      },
      {
        id: 'run-review-failed',
        agent_name: 'post_trade_reviewer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: twentyMinutesAgo,
        ended_at: twentyMinutesAgo,
        status: 'failed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '["timeout"]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[],
  )

  const rerun = recommendations.find(item => item.kind === 'failed_agent_rerun_due')
  assert.ok(rerun)
  assert.equal(rerun?.taskType, 'rerun_failed_agent_task')
  assert.equal(rerun?.metadata?.failed_run_id, 'run-review-failed')
  assert.equal(rerun?.metadata?.agent_name, 'post_trade_reviewer')
  assert.ok(Number(rerun?.metadata?.stale_minutes) >= 15)
})

test('recommendation engine marks daily review refresh as due when today has activity but no daily review yet', () => {
  const engine = new RecommendationEngine()
  const fiveMinutesAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString()
  const today = new Date().toISOString().slice(0, 10)

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [{ id: 'dream-log-1' }],
      recent_dreaming_runs: [{ id: 'dream-run-1' }],
      recent_daily_reviews: [],
    },
    {
      recent_closed_positions: [],
      recent_post_trade_reviews: [],
      recent_post_trade_reviewer_runs: [],
      missing_review_strategy_ids: [],
    },
    {
      active_paper_strategies: [],
      under_review_strategies: [],
      paused_paper_strategies: [],
      live_candidate_strategies: [],
      recent_strategy_evaluations: [],
      needs_operator_evaluation_strategy_ids: [],
      recent_strategy_promotion_proposals: [],
      needs_operator_promotion_proposal_strategy_ids: [],
    },
    [
      {
        id: 'run-market-observer-fresh',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: fiveMinutesAgo,
        ended_at: fiveMinutesAgo,
        status: 'completed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '[]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[],
  )

  const refresh = recommendations.find(item => item.kind === 'daily_review_refresh_due')
  assert.ok(refresh)
  assert.equal(refresh?.taskType, 'create_daily_review')
  assert.equal(refresh?.metadata?.target_date, today)
})

test('recommendation engine marks system health diagnostic report refresh as due when health is degraded', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot({
      health: {
        status: 'degraded',
        issues: ['market_data_stale:45m'],
      },
    }),
  )

  const refresh = recommendations.find(item => item.kind === 'system_health_report_refresh_due')
  assert.ok(refresh)
  assert.equal(refresh?.taskType, 'create_diagnostic_report')
  assert.equal(refresh?.metadata?.report_type, 'system_health')
  assert.equal(refresh?.metadata?.refresh_window_minutes, 60)
})

test('recommendation engine marks worker health review as due when worker health is degraded', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot({
      health: {
        status: 'degraded',
        issues: ['agent_failure_ratio:0.40>0.25'],
      },
    }),
  )

  const refresh = recommendations.find(item => item.kind === 'worker_health_review_due')
  assert.ok(refresh)
  assert.equal(refresh?.taskType, 'create_diagnostic_report')
  assert.equal(refresh?.metadata?.report_type, 'worker_health')
  assert.equal(refresh?.metadata?.refresh_window_minutes, 60)
})

test('recommendation engine marks model/runtime health review as due when llm health is degraded', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot({
      health: {
        status: 'degraded',
        issues: ['llm_unavailable:3'],
      },
    }),
  )

  const refresh = recommendations.find(item => item.kind === 'model_runtime_health_review_due')
  assert.ok(refresh)
  assert.equal(refresh?.taskType, 'create_diagnostic_report')
  assert.equal(refresh?.metadata?.report_type, 'model_runtime_health')
  assert.equal(refresh?.metadata?.refresh_window_minutes, 60)
})

test('recommendation engine marks data source health review as due when market data is stale', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot({
      health: {
        status: 'degraded',
        issues: ['market_data_stale:45m'],
      },
    }),
  )

  const refresh = recommendations.find(item => item.kind === 'data_source_health_review_due')
  assert.ok(refresh)
  assert.equal(refresh?.taskType, 'create_diagnostic_report')
  assert.equal(refresh?.metadata?.report_type, 'data_source_health')
  assert.equal(refresh?.metadata?.refresh_window_minutes, 60)
})

test('recommendation engine marks trade review report refresh as due when closed positions exist', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [{ id: 'dream-log-1' }],
      recent_dreaming_runs: [{ id: 'dream-run-1' }],
      recent_daily_reviews: [{ id: 'daily-review-today', created_at: `${new Date().toISOString().slice(0, 10)}T01:00:00Z` }],
    },
    {
      recent_closed_positions: [{ id: 'position-1', strategy_id: 'review-strategy-1' }],
      recent_post_trade_reviews: [],
      recent_post_trade_reviewer_runs: [],
      missing_review_strategy_ids: ['review-strategy-1'],
    },
    {
      active_paper_strategies: [],
      under_review_strategies: [],
      paused_paper_strategies: [],
      live_candidate_strategies: [],
      recent_strategy_evaluations: [],
      needs_operator_evaluation_strategy_ids: [],
      recent_strategy_promotion_proposals: [],
      needs_operator_promotion_proposal_strategy_ids: [],
    },
    [],
  )

  const refresh = recommendations.find(item => item.kind === 'trade_review_report_refresh_due')
  assert.ok(refresh)
  assert.equal(refresh?.taskType, 'create_review_report')
  assert.equal(refresh?.metadata?.report_type, 'trade_review')
  assert.equal(refresh?.metadata?.closed_position_count, 1)
})

test('recommendation engine marks agent performance report refresh as due when recent failures exist', () => {
  const engine = new RecommendationEngine()
  const twentyMinutesAgo = new Date(Date.now() - 20 * 60 * 1000).toISOString()

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [{ id: 'dream-log-1' }],
      recent_dreaming_runs: [{ id: 'dream-run-1' }],
      recent_daily_reviews: [{ id: 'daily-review-today', created_at: `${new Date().toISOString().slice(0, 10)}T01:00:00Z` }],
    },
    {
      recent_closed_positions: [],
      recent_post_trade_reviews: [],
      recent_post_trade_reviewer_runs: [],
      missing_review_strategy_ids: [],
    },
    {
      active_paper_strategies: [],
      under_review_strategies: [],
      paused_paper_strategies: [],
      live_candidate_strategies: [],
      recent_strategy_evaluations: [],
      needs_operator_evaluation_strategy_ids: [],
      recent_strategy_promotion_proposals: [],
      needs_operator_promotion_proposal_strategy_ids: [],
    },
    [
      {
        id: 'run-paper-failed',
        agent_name: 'paper_trader',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: twentyMinutesAgo,
        ended_at: twentyMinutesAgo,
        status: 'failed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '["timeout"]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[],
  )

  const refresh = recommendations.find(item => item.kind === 'agent_performance_report_refresh_due')
  assert.ok(refresh)
  assert.equal(refresh?.taskType, 'create_review_report')
  assert.equal(refresh?.metadata?.report_type, 'agent_performance')
  assert.equal(refresh?.metadata?.failed_run_count, 1)
})

test('recommendation engine marks risk review report refresh as due when recent rejections exist', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [{ id: 'dream-log-1' }],
      recent_dreaming_runs: [{ id: 'dream-run-1' }],
      recent_daily_reviews: [{ id: 'daily-review-today', created_at: `${new Date().toISOString().slice(0, 10)}T01:00:00Z` }],
    },
    {
      recent_closed_positions: [],
      recent_post_trade_reviews: [],
      recent_post_trade_reviewer_runs: [],
      missing_review_strategy_ids: [],
    },
    {
      active_paper_strategies: [],
      under_review_strategies: [],
      paused_paper_strategies: [],
      live_candidate_strategies: [],
      recent_strategy_evaluations: [],
      needs_operator_evaluation_strategy_ids: [],
      recent_strategy_promotion_proposals: [],
      needs_operator_promotion_proposal_strategy_ids: [],
    },
    [],
    [],
    {
      recent_rejected_decisions: [{ id: 'risk-1' }],
      recent_risk_boundary_proposals: [],
      needs_operator_risk_boundary_proposals: [],
    },
  )

  const refresh = recommendations.find(item => item.kind === 'risk_review_report_refresh_due')
  assert.ok(refresh)
  assert.equal(refresh?.taskType, 'create_review_report')
  assert.equal(refresh?.metadata?.report_type, 'risk_review')
  assert.equal(refresh?.metadata?.rejected_decision_count, 1)
})

test('recommendation engine marks prompt change as due when one agent keeps failing', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [{ id: 'dream-log-1' }],
      recent_dreaming_runs: [{ id: 'dream-run-1' }],
    },
    {
      recent_closed_positions: [],
      recent_post_trade_reviews: [],
      recent_post_trade_reviewer_runs: [],
      missing_review_strategy_ids: [],
    },
    {
      active_paper_strategies: [],
      under_review_strategies: [],
      paused_paper_strategies: [],
      live_candidate_strategies: [],
      recent_strategy_evaluations: [],
      needs_operator_evaluation_strategy_ids: [],
      recent_strategy_promotion_proposals: [],
      needs_operator_promotion_proposal_strategy_ids: [],
    },
    [
      {
        id: 'run-1',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        status: 'failed',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        errors: '["structured output parse failed"]',
      },
      {
        id: 'run-2',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        status: 'failed',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        errors: '["structured output parse failed"]',
      },
    ] satisfies AgentRunSummary[],
  )

  const promptProposal = recommendations.find(item => item.kind === 'agent_prompt_change_proposal_due')
  assert.ok(promptProposal)
  assert.equal(promptProposal?.taskType, 'create_repair_proposal')
  assert.equal(promptProposal?.metadata?.proposal_command, 'propose_prompt_change')
})

test('recommendation engine suppresses prompt change due when a recent proposal already exists', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [{ id: 'dream-log-1' }],
      recent_dreaming_runs: [{ id: 'dream-run-1' }],
    },
    {
      recent_closed_positions: [],
      recent_post_trade_reviews: [],
      recent_post_trade_reviewer_runs: [],
      missing_review_strategy_ids: [],
    },
    {
      active_paper_strategies: [],
      under_review_strategies: [],
      paused_paper_strategies: [],
      live_candidate_strategies: [],
      recent_strategy_evaluations: [],
      needs_operator_evaluation_strategy_ids: [],
      recent_strategy_promotion_proposals: [],
      needs_operator_promotion_proposal_strategy_ids: [],
    },
    [
      {
        id: 'run-1',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        status: 'failed',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        errors: '["structured output parse failed"]',
      },
      {
        id: 'run-2',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        status: 'failed',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        errors: '["structured output parse failed"]',
      },
    ] satisfies AgentRunSummary[],
    [
      {
        id: 'artifact-prompt-1',
        task_id: null,
        session_id: 'operator-main',
        artifact_type: 'prompt_change_proposal',
        title: 'Prompt change proposal: market_observer',
        content: {
          target: 'market_observer',
          requires_human_approval: true,
        },
        created_at: new Date().toISOString(),
      },
    ],
  )

  const promptProposal = recommendations.find(item => item.kind === 'agent_prompt_change_proposal_due')
  assert.equal(promptProposal, undefined)
})

test('recommendation engine marks schedule change as due when one agent keeps needing manual runs', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [{ id: 'dream-log-1' }],
      recent_dreaming_runs: [{ id: 'dream-run-1' }],
    },
    {
      recent_closed_positions: [],
      recent_post_trade_reviews: [],
      recent_post_trade_reviewer_runs: [],
      missing_review_strategy_ids: [],
    },
    {
      active_paper_strategies: [],
      under_review_strategies: [],
      paused_paper_strategies: [],
      live_candidate_strategies: [],
      recent_strategy_evaluations: [],
      needs_operator_evaluation_strategy_ids: [],
      recent_strategy_promotion_proposals: [],
      needs_operator_promotion_proposal_strategy_ids: [],
    },
    [
      {
        id: 'run-m1',
        agent_name: 'market_observer',
        trigger_type: 'manual',
        status: 'completed',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        errors: '[]',
      },
      {
        id: 'run-m2',
        agent_name: 'market_observer',
        trigger_type: 'manual',
        status: 'completed',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        errors: '[]',
      },
    ] satisfies AgentRunSummary[],
  )

  const scheduleProposal = recommendations.find(item => item.kind === 'agent_schedule_change_proposal_due')
  assert.ok(scheduleProposal)
  assert.equal(scheduleProposal?.taskType, 'create_repair_proposal')
  assert.equal(scheduleProposal?.metadata?.proposal_command, 'propose_schedule_change')
  assert.equal(scheduleProposal?.metadata?.requested_schedule, 'every 10 minutes')
})

test('recommendation engine suppresses schedule change due when a recent proposal already exists', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [{ id: 'dream-log-1' }],
      recent_dreaming_runs: [{ id: 'dream-run-1' }],
    },
    {
      recent_closed_positions: [],
      recent_post_trade_reviews: [],
      recent_post_trade_reviewer_runs: [],
      missing_review_strategy_ids: [],
    },
    {
      active_paper_strategies: [],
      under_review_strategies: [],
      paused_paper_strategies: [],
      live_candidate_strategies: [],
      recent_strategy_evaluations: [],
      needs_operator_evaluation_strategy_ids: [],
      recent_strategy_promotion_proposals: [],
      needs_operator_promotion_proposal_strategy_ids: [],
    },
    [
      {
        id: 'run-m1',
        agent_name: 'market_observer',
        trigger_type: 'manual',
        status: 'completed',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        errors: '[]',
      },
      {
        id: 'run-m2',
        agent_name: 'market_observer',
        trigger_type: 'manual',
        status: 'completed',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        errors: '[]',
      },
    ] satisfies AgentRunSummary[],
    [
      {
        id: 'artifact-schedule-1',
        task_id: null,
        session_id: 'operator-main',
        artifact_type: 'schedule_change_proposal',
        title: 'Schedule change proposal: market_observer',
        content: {
          agent_name: 'market_observer',
          requested_schedule: 'every 10 minutes',
          requires_human_approval: true,
        },
        created_at: new Date().toISOString(),
      },
    ],
  )

  const scheduleProposal = recommendations.find(item => item.kind === 'agent_schedule_change_proposal_due')
  assert.equal(scheduleProposal, undefined)
})

test('recommendation engine surfaces risk boundary change proposals from backend truth', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [{ id: 'dream-log-1' }],
      recent_dreaming_runs: [{ id: 'dream-run-1' }],
    },
    {
      recent_closed_positions: [],
      recent_post_trade_reviews: [],
      recent_post_trade_reviewer_runs: [],
      missing_review_strategy_ids: [],
    },
    {
      active_paper_strategies: [],
      under_review_strategies: [],
      paused_paper_strategies: [],
      live_candidate_strategies: [],
      recent_strategy_evaluations: [],
      needs_operator_evaluation_strategy_ids: [],
      recent_strategy_promotion_proposals: [],
      needs_operator_promotion_proposal_strategy_ids: [],
    },
    [],
    [],
    {
      recent_rejected_decisions: [
        {
          id: 'risk-1',
          proposal_id: 'proposal-1',
          violations: ['exceeds_demo_position_margin:300.0>200.0'],
        },
      ],
      recent_risk_boundary_proposals: [],
      needs_operator_risk_boundary_proposals: [
        {
          boundary_key: 'max_demo_position_notional_usdt',
          violation_kind: 'exceeds_demo_position_margin',
          risk_mode: 'demo',
          policy_id: 'demo-policy',
          policy_revision: 2,
          policy_hash: 'demo-policy-hash',
          rejection_count: 2,
          current_value: 200,
          suggested_value: 300,
          changes: {
            max_demo_position_notional_usdt: 300,
          },
          summary:
            'Recent risk rejections repeatedly hit the demo position-margin cap and should be proposed for human review.',
          sample_violations: ['exceeds_demo_position_margin:300.0>200.0'],
          decision_ids: ['risk-1', 'risk-2'],
        },
      ],
    },
  )

  const riskProposal = recommendations.find(item => item.kind === 'risk_boundary_change_proposal_due')
  assert.ok(riskProposal)
  assert.equal(riskProposal?.taskType, 'create_repair_proposal')
  assert.equal(
    riskProposal?.id,
    'risk-boundary-proposal:demo:demo-policy-hash:max_demo_position_notional_usdt',
  )
  assert.equal(riskProposal?.metadata?.proposal_command, 'propose_risk_boundary_change')
  assert.equal(riskProposal?.metadata?.risk_mode, 'demo')
  assert.equal(riskProposal?.metadata?.policy_id, 'demo-policy')
  assert.equal(riskProposal?.metadata?.policy_revision, 2)
  assert.equal(riskProposal?.metadata?.policy_hash, 'demo-policy-hash')
  assert.deepEqual(riskProposal?.metadata?.changes, {
    max_demo_position_notional_usdt: 300,
  })
})

test('recommendation engine marks Command Center behavior change as due after repeated denied commands', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [{ id: 'dream-log-1' }],
      recent_dreaming_runs: [{ id: 'dream-run-1' }],
    },
    {
      recent_closed_positions: [],
      recent_post_trade_reviews: [],
      recent_post_trade_reviewer_runs: [],
      missing_review_strategy_ids: [],
    },
    {
      active_paper_strategies: [],
      under_review_strategies: [],
      paused_paper_strategies: [],
      live_candidate_strategies: [],
      recent_strategy_evaluations: [],
      needs_operator_evaluation_strategy_ids: [],
      recent_strategy_promotion_proposals: [],
      needs_operator_promotion_proposal_strategy_ids: [],
    },
    [],
    [],
    {
      recent_rejected_decisions: [],
      recent_risk_boundary_proposals: [],
      needs_operator_risk_boundary_proposals: [],
    },
    [
      {
        id: 'cmd-1',
        session_id: 'session-command-1',
        command: 'pause_agents',
        parameters: {},
        permitted: false,
        result_summary:
          "Command 'pause_agents' requires operator permission mode 'PaperAuto' but current mode is 'ReadOnly'.",
        created_at: new Date().toISOString(),
        session_kind: 'command_session',
        session_user_input: 'pause everything',
      },
      {
        id: 'cmd-2',
        session_id: 'session-command-2',
        command: 'pause_agents',
        parameters: {},
        permitted: false,
        result_summary:
          "Command 'pause_agents' requires operator permission mode 'PaperAuto' but current mode is 'ReadOnly'.",
        created_at: new Date().toISOString(),
        session_kind: 'command_session',
        session_user_input: 'pause agents now',
      },
    ] satisfies PersistedOperatorCommand[],
  )

  const behaviorProposal = recommendations.find(
    item => item.kind === 'command_center_behavior_change_proposal_due',
  )
  assert.ok(behaviorProposal)
  assert.equal(behaviorProposal?.taskType, 'create_repair_proposal')
  assert.equal(
    behaviorProposal?.metadata?.proposal_command,
    'propose_command_center_behavior_change',
  )
  assert.equal(behaviorProposal?.metadata?.target_command, 'pause_agents')
})

test('recommendation engine suppresses Command Center behavior change due when a recent proposal already exists', () => {
  const engine = new RecommendationEngine()

  const recommendations = engine.list(
    makeSnapshot(),
    {
      recent_research_notes: [],
      recent_strategy_hypotheses: [],
      deprecated_memories: [],
      recent_dreaming_logs: [{ id: 'dream-log-1' }],
      recent_dreaming_runs: [{ id: 'dream-run-1' }],
    },
    {
      recent_closed_positions: [],
      recent_post_trade_reviews: [],
      recent_post_trade_reviewer_runs: [],
      missing_review_strategy_ids: [],
    },
    {
      active_paper_strategies: [],
      under_review_strategies: [],
      paused_paper_strategies: [],
      live_candidate_strategies: [],
      recent_strategy_evaluations: [],
      needs_operator_evaluation_strategy_ids: [],
      recent_strategy_promotion_proposals: [],
      needs_operator_promotion_proposal_strategy_ids: [],
    },
    [],
    [
      {
        id: 'artifact-behavior-1',
        task_id: null,
        session_id: 'operator-main',
        artifact_type: 'command_center_behavior_proposal',
        title: 'Command Center behavior proposal: pause_agents',
        content: {
          target_command: 'pause_agents',
          requested_behavior: 'Add an inline permission gate.',
          requires_human_approval: true,
        },
        created_at: new Date().toISOString(),
      },
    ],
    {
      recent_rejected_decisions: [],
      recent_risk_boundary_proposals: [],
      needs_operator_risk_boundary_proposals: [],
    },
    [
      {
        id: 'cmd-1',
        session_id: 'session-command-1',
        command: 'pause_agents',
        parameters: {},
        permitted: false,
        result_summary:
          "Command 'pause_agents' requires operator permission mode 'PaperAuto' but current mode is 'ReadOnly'.",
        created_at: new Date().toISOString(),
        session_kind: 'command_session',
        session_user_input: 'pause everything',
      },
      {
        id: 'cmd-2',
        session_id: 'session-command-2',
        command: 'pause_agents',
        parameters: {},
        permitted: false,
        result_summary:
          "Command 'pause_agents' requires operator permission mode 'PaperAuto' but current mode is 'ReadOnly'.",
        created_at: new Date().toISOString(),
        session_kind: 'command_session',
        session_user_input: 'pause agents now',
      },
    ] satisfies PersistedOperatorCommand[],
  )

  const behaviorProposal = recommendations.find(
    item => item.kind === 'command_center_behavior_change_proposal_due',
  )
  assert.equal(behaviorProposal, undefined)
})

test('task runtime opens one diagnostic task and closes it when the issue clears', async () => {
  const { backend, calls } = makeFakeBackend(makeSnapshot())
  const runtime = new TaskRuntime(backend)
  const recommendation = makeRecommendation()

  await runtime.sync([recommendation])
  assert.deepEqual(runtime.snapshot(), { currentTaskId: 'task-1', queueSize: 1 })
  assert.equal(calls.createdTasks.length, 1)

  await runtime.sync([recommendation])
  assert.equal(calls.createdTasks.length, 1)

  await runtime.sync([])
  assert.deepEqual(runtime.snapshot(), { currentTaskId: null, queueSize: 0 })
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].taskId, 'task-1')
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
})

test('task runtime does not repeat a completed recommendation until its evidence changes, including after recovery', async () => {
  const { backend, calls } = makeFakeBackend(makeSnapshot())
  const recommendation = makeRecommendation({
    id: 'opportunity-quality-audit-due',
    kind: 'opportunity_quality_audit_due',
    taskType: 'pi_reasoning_session',
    metadata: {
      reason: 'high_rejection_rate',
      rejection_count: 5,
      goal: 'Audit the five current rejections.',
    },
  })
  const runtime = new TaskRuntime(backend)

  await runtime.sync([recommendation])
  await runtime.completeActiveTask({ summary: 'The diagnosis is complete.' })
  await runtime.sync([recommendation])

  assert.equal(calls.createdTasks.length, 1)
  assert.equal(typeof calls.createdTasks[0].input_data.recommendation_fingerprint, 'string')

  const recovered = new TaskRuntime(backend)
  recovered.restore([
    {
      id: 'completed-opportunity-audit',
      task_type: 'pi_reasoning_session',
      status: 'completed',
      priority: 'normal',
      created_by: 'operator_loop',
      assigned_to: 'operator',
      input_data: calls.createdTasks[0].input_data,
      output_data: { summary: 'The diagnosis is complete.' },
      error: null,
      started_at: '2026-07-14T00:00:00Z',
      completed_at: '2026-07-14T00:01:00Z',
      created_at: '2026-07-14T00:00:00Z',
    },
  ])
  await recovered.sync([recommendation])
  assert.equal(calls.createdTasks.length, 1)

  await recovered.sync([
    makeRecommendation({
      ...recommendation,
      metadata: {
        ...recommendation.metadata,
        rejection_count: 6,
      },
    }),
  ])
  assert.equal(calls.createdTasks.length, 2)
})

test('task runtime supersedes legacy unversioned opportunity-audit backlog after recovery', async () => {
  const { backend, calls } = makeFakeBackend(makeSnapshot())
  const recommendation = makeRecommendation({
    id: 'opportunity-quality-audit-due',
    kind: 'opportunity_quality_audit_due',
    taskType: 'pi_reasoning_session',
    metadata: {
      reason: 'high_rejection_rate',
      rejection_count: 6,
      rejected_decision_ids: ['risk-1', 'risk-2', 'risk-3', 'risk-4', 'risk-5', 'risk-6'],
      goal: 'Audit the current risk rejections once.',
    },
  })
  const legacyInput = {
    recommendation_id: 'opportunity-quality-audit-due',
    kind: 'opportunity_quality_audit_due',
    reason: 'high_rejection_rate',
    rejection_count: 6,
    goal: 'Audit the current risk rejections once.',
  }
  const runtime = new TaskRuntime(backend)
  runtime.restore([
    {
      id: 'legacy-running', task_type: 'pi_reasoning_session', status: 'running',
      priority: 'normal', created_by: 'operator_loop', assigned_to: 'operator',
      input_data: legacyInput, output_data: null, error: null,
      started_at: '2026-07-14T00:00:00Z', completed_at: null, created_at: '2026-07-14T00:00:00Z',
    },
    {
      id: 'legacy-queued', task_type: 'pi_reasoning_session', status: 'queued',
      priority: 'normal', created_by: 'operator_loop', assigned_to: 'operator',
      input_data: legacyInput, output_data: null, error: null,
      started_at: null, completed_at: null, created_at: '2026-07-14T00:01:00Z',
    },
  ])

  await runtime.sync([recommendation])

  assert.deepEqual(
    calls.updatedTasks.map(item => [item.taskId, item.payload.status]),
    [['legacy-running', 'completed'], ['legacy-queued', 'completed']],
  )
  assert.equal(calls.createdTasks.length, 1)
  assert.equal(calls.createdTasks[0].status, 'running')
  assert.equal(typeof calls.createdTasks[0].input_data.recommendation_fingerprint, 'string')
  assert.deepEqual(runtime.snapshot(), { currentTaskId: 'task-1', queueSize: 1 })
})

test('task runtime retires expired running tasks before recovery while retaining a fresh task', async () => {
  const { backend, calls } = makeFakeBackend(makeSnapshot())
  const runtime = new TaskRuntime(backend)
  const now = Date.now()
  const expiredAt = new Date(now - 31 * 60 * 1000).toISOString()
  const freshAt = new Date(now - 29 * 60 * 1000).toISOString()

  const recovered = await runtime.reconcileExpiredRunningTasks([
    makeTask({ id: 'expired-first', status: 'running', started_at: expiredAt, created_at: expiredAt }),
    makeTask({ id: 'fresh-running', status: 'running', started_at: freshAt, created_at: freshAt }),
    makeTask({ id: 'expired-second', status: 'running', started_at: expiredAt, created_at: expiredAt }),
  ])
  runtime.restore(recovered)

  assert.deepEqual(
    calls.updatedTasks.map(item => [item.taskId, item.payload.status, item.payload.error]),
    [
      ['expired-first', 'cancelled', 'operator_runtime_recovery_timeout'],
      ['expired-second', 'cancelled', 'operator_runtime_recovery_timeout'],
    ],
  )
  assert.equal(calls.updatedTasks[0].payload.output_data?.recovery_reason, 'running_task_expired')
  assert.deepEqual(runtime.snapshot(), { currentTaskId: 'fresh-running', queueSize: 1 })
})

test('task runtime creates a queued backlog and promotes the next queued task after the active one clears', async () => {
  const { backend, calls } = makeFakeBackend(makeSnapshot())
  const runtime = new TaskRuntime(backend)

  await runtime.sync([
    makeRecommendation(),
    makeRecommendation({
      id: 'dreaming-due',
      kind: 'dreaming_due',
      severity: 'medium',
      summary: 'No recent dreaming consolidation is visible.',
      taskType: 'run_dreaming',
    }),
  ])

  assert.equal(calls.createdTasks.length, 2)
  assert.equal(calls.createdTasks[0].status, 'running')
  assert.equal(calls.createdTasks[1].status, 'queued')
  assert.deepEqual(runtime.snapshot(), { currentTaskId: 'task-1', queueSize: 2 })

  await runtime.sync([])

  assert.equal(calls.updatedTasks[0].taskId, 'task-1')
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  assert.equal(calls.updatedTasks[1].taskId, 'task-2')
  assert.equal(calls.updatedTasks[1].payload.status, 'running')
  assert.deepEqual(runtime.snapshot(), { currentTaskId: 'task-2', queueSize: 1 })
})

test('task runtime promotes higher-priority queued work before older normal-priority backlog', async () => {
  const { backend, calls } = makeFakeBackend(makeSnapshot())
  const runtime = new TaskRuntime(backend)

  await runtime.sync([
    makeRecommendation(),
    makeRecommendation({
      id: 'review-due',
      kind: 'review_due',
      severity: 'medium',
      summary: 'A post-trade review is due.',
      taskType: 'generate_review',
    }),
    makeRecommendation({
      id: 'gateway-offline',
      kind: 'gateway_offline',
      severity: 'high',
      summary: 'Gateway reports itself as not running.',
      taskType: 'diagnose_gateway',
    }),
  ])

  assert.equal(calls.createdTasks.length, 3)
  assert.equal(calls.createdTasks[1].priority, 'normal')
  assert.equal(calls.createdTasks[2].priority, 'high')

  await runtime.sync([])

  assert.equal(calls.updatedTasks[1].taskId, 'task-3')
  assert.equal(calls.updatedTasks[1].payload.status, 'running')
  assert.deepEqual(runtime.snapshot(), { currentTaskId: 'task-3', queueSize: 2 })
})

test('operator loop emits one event, tracks one task, and returns to observing after recovery', async () => {
  const snapshot = makeSnapshot({
    health: {
      status: 'degraded',
      issues: ['market_data_stale:45m'],
    },
  })
  const { backend, calls } = makeFakeBackend(snapshot)
  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const first = await loop.runOnce()
  const second = await loop.runOnce()

  assert.equal(first, true)
  assert.equal(second, true)
  assert.equal(calls.createdEvents.length, 1)
  assert.equal(calls.createdTasks.length, 1)
  assert.equal(calls.heartbeats[0].mode, 'Working')
  assert.equal(calls.heartbeats[0].current_task_id, 'task-1')
  // is_busy contract: a non-null current task means the operator is actively busy.
  // The dashboard's "Thinking/Idle" badge keys on is_busy, so this mapping must hold.
  assert.equal(calls.heartbeats[0].is_busy, true)
  assert.match(calls.heartbeats[0].current_intent || '', /Gateway health issue/)

  snapshot.health = {
    status: 'healthy',
    issues: [],
    version: 'test',
  }

  const third = await loop.runOnce()

  assert.equal(third, true)
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  assert.equal(calls.heartbeats[2].mode, 'Observing')
  assert.equal(calls.heartbeats[2].current_task_id, null)
  // is_busy contract (idle side): task completed → no current task → not busy.
  assert.equal(calls.heartbeats[2].is_busy, false)
})

test('operator loop does not persist raw gateway 404 as the current intent', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getGatewayStatus = async () => {
    throw new Error('gateway_request_failed:404:GET /api/missing:Not Found')
  }
  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, false)
  assert.equal(calls.heartbeats.length, 1)
  assert.equal(calls.heartbeats[0].mode, 'Degraded')
  assert.equal(calls.heartbeats[0].last_error, 'gateway_request_failed:404:GET /api/missing:Not Found')
  assert.equal(calls.heartbeats[0].current_intent, 'gateway connectivity recovery pending')
})

test('operator loop ignores transient gateway errors when rebuilding recovery intent', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getOperatorStatus = async () =>
    ({
      agent_id: 'operator',
      status: 'stale',
      mode: 'Degraded',
      permission_mode: 'ReadOnly',
      is_busy: false,
      current_task_id: null,
      current_intent: 'gateway_request_failed:404',
      queue_size: 0,
      last_seen_at: new Date().toISOString(),
      last_success_at: new Date().toISOString(),
      last_error: 'gateway_request_failed:404',
      model_provider: 'pi-shell',
      session_id: 'operator-main',
      stale: true,
      stale_after_sec: 45,
    }) satisfies PersistedOperatorStatus
  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.equal(calls.heartbeats.length, 1)
  assert.equal(calls.heartbeats[0].mode, 'Observing')
  assert.equal(calls.heartbeats[0].current_intent, 'monitoring system state')
  assert.equal(calls.heartbeats[0].last_error, null)
})

test('operator loop treats lifecycle event write failure as non-fatal startup telemetry', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.createEvent = async payload => {
    if (payload.event_type === 'operator_started') {
      throw new Error('gateway_request_failed:500:POST /api/operator/internal/events:database is locked')
    }
    calls.createdEvents.push(payload)
    return { id: `event-${calls.createdEvents.length}` }
  }
  const previousExitCode = process.exitCode
  const previousConsoleError = console.error
  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  try {
    console.error = () => undefined
    await loop.start()
  } finally {
    console.error = previousConsoleError
    process.exitCode = previousExitCode
  }

  assert.equal(calls.heartbeats.length, 1)
  assert.equal(calls.heartbeats[0].last_error, null)
})

test('operator loop keeps heartbeat cadence every run but throttles supervision scans to 30 seconds', async () => {
  const originalNow = Date.now
  let now = Date.UTC(2026, 5, 11, 0, 0, 0)
  Date.now = () => now

  try {
    const snapshot = makeSnapshot()
    const { backend, calls } = makeFakeBackend(snapshot)
    const loop = new OperatorLoop(
      {
        gatewayBaseUrl: 'http://127.0.0.1:8787',
        heartbeatIntervalSec: 10,
        permissionMode: 'ReadOnly',
        mainSessionId: 'operator-main',
        modelProvider: 'pi-shell',
        once: true,
      },
      {
        backendClient: backend,
        sessionRuntime: new SessionRuntime('operator-main'),
        taskRuntime: new TaskRuntime(backend),
        eventRouter: new EventRouter(backend),
        repairEngine: new RepairEngine(),
        recommendationEngine: new RecommendationEngine(),
      },
    )

    await loop.runOnce()
    now += 10_000
    await loop.runOnce()
    now += 20_000
    await loop.runOnce()

    assert.equal(calls.gatewayStatusReads, 3)
    assert.equal(calls.healthReads, 3)
    assert.equal(calls.sessionDetailReads, 2)
    assert.equal(calls.commandReads, 2)
    assert.equal(calls.taskReads, 2)
    assert.equal(calls.heartbeats.length, 3)
  } finally {
    Date.now = originalNow
  }
})

test('operator loop throttles subsystem review scans to a 5 minute cadence', async () => {
  const originalNow = Date.now
  let now = Date.UTC(2026, 5, 11, 0, 0, 0)
  Date.now = () => now

  try {
    const snapshot = makeSnapshot()
    const { backend, calls } = makeFakeBackend(snapshot)
    const loop = new OperatorLoop(
      {
        gatewayBaseUrl: 'http://127.0.0.1:8787',
        heartbeatIntervalSec: 10,
        permissionMode: 'ReadOnly',
        mainSessionId: 'operator-main',
        modelProvider: 'pi-shell',
        once: true,
      },
      {
        backendClient: backend,
        sessionRuntime: new SessionRuntime('operator-main'),
        taskRuntime: new TaskRuntime(backend),
        eventRouter: new EventRouter(backend),
        repairEngine: new RepairEngine(),
        recommendationEngine: new RecommendationEngine(),
      },
    )

    await loop.runOnce()
    now += 10_000
    await loop.runOnce()
    now += 20_000
    await loop.runOnce()
    now += 270_000
    await loop.runOnce()

    assert.equal(calls.gatewayStatusReads, 4)
    assert.equal(calls.healthReads, 4)
    assert.equal(calls.dreamingSummaryReads, 2)
    assert.equal(calls.reviewSummaryReads, 2)
    assert.equal(calls.strategySummaryReads, 2)
    assert.equal(calls.riskSummaryReads, 2)
    assert.equal(calls.agentRunReads, 2)
  } finally {
    Date.now = originalNow
  }
})

test('operator loop restores session and active task without resuming into trading mode', async () => {
  const snapshot = makeSnapshot({
    health: {
      status: 'degraded',
      issues: ['market_data_stale:45m'],
    },
  })
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Trading',
    permission_mode: 'ReadOnly',
    is_busy: true,
    current_task_id: 'task-existing',
    current_intent: 'previous trading mode should not survive recovery',
    queue_size: 1,
    last_seen_at: new Date().toISOString(),
    last_success_at: new Date().toISOString(),
    last_error: 'previous operator crash',
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    stale: false,
    stale_after_sec: 45,
  })
  backend.getOperatorSession = async () => ({
    session: {
      id: 'operator-main',
      user_input: '[operator session]',
      intent: 'resume degraded operator context',
      command: null,
      response: 'Carry forward unresolved issues',
      permitted: 1,
      duration_ms: 0,
      created_at: new Date().toISOString(),
      message_count: 3,
      command_count: 1,
    },
    messages: [],
    commands: [],
    artifacts: [],
  })
  backend.getOperatorTasks = async () => [
    {
      id: 'task-existing',
      task_type: 'diagnose_system_health',
      status: 'running',
      priority: 'high',
      created_by: 'operator_loop',
      assigned_to: 'operator',
      input_data: {
        recommendation_id: 'health:market_data_stale',
        summary: 'Gateway health issue: market_data_stale:45m',
      },
      output_data: null,
      error: null,
      started_at: new Date().toISOString(),
      completed_at: null,
      created_at: new Date().toISOString(),
    },
  ]

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.equal(calls.heartbeats[0].current_task_id, 'task-existing')
  assert.notEqual(calls.heartbeats[0].mode, 'Trading')
  assert.match(calls.heartbeats[0].current_intent || '', /market_data_stale:45m/)
})

test('operator loop restores persisted active branch instead of guessing the newest branch', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getOperatorStatus = async () =>
    ({
      agent_id: 'operator',
      status: 'online',
      mode: 'Observing',
      permission_mode: 'ReadOnly',
      is_busy: false,
      current_task_id: null,
      current_intent: 'resume operator session',
      queue_size: 0,
      last_seen_at: new Date().toISOString(),
      last_success_at: new Date().toISOString(),
      last_error: null,
      model_provider: 'pi-shell',
      session_id: 'operator-main',
      stale: false,
      stale_after_sec: 45,
      active_branch_id: 'strategy-session-older',
      active_branch_kind: 'strategy_session',
      active_branch_title: 'Momentum A Review',
    } as PersistedOperatorStatus)
  backend.getOperatorSessions = async () => {
    const now = Date.now()
    return [
      {
        id: 'operator-main',
        user_input: '[operator session]',
        intent: null,
        command: null,
        response: null,
        permitted: 1,
        duration_ms: 0,
        created_at: new Date(now).toISOString(),
        message_count: 0,
        command_count: 0,
        kind: 'command_session',
        parent_session_id: null,
        title: 'Operator Main',
      },
      {
        id: 'strategy-session-newest',
        user_input: 'check strategy B',
        intent: null,
        command: null,
        response: 'newer branch summary',
        permitted: 1,
        duration_ms: 0,
        created_at: new Date(now).toISOString(),
        message_count: 1,
        command_count: 0,
        kind: 'strategy_session',
        parent_session_id: 'operator-main',
        title: 'Momentum B Review',
      },
      {
        id: 'strategy-session-older',
        user_input: 'check strategy A',
        intent: null,
        command: null,
        response: 'older branch summary',
        permitted: 1,
        duration_ms: 0,
        created_at: new Date(now - 60_000).toISOString(),
        message_count: 1,
        command_count: 0,
        kind: 'strategy_session',
        parent_session_id: 'operator-main',
        title: 'Momentum A Review',
      },
    ] satisfies OperatorSessionSummary[]
  }
  backend.getOperatorSession = async (sessionId: string) => {
    if (sessionId === 'strategy-session-newest') {
      return {
        session: {
          id: 'strategy-session-newest',
          user_input: 'check strategy B',
          intent: null,
          command: null,
          response: 'newer branch summary',
          permitted: 1,
          duration_ms: 0,
          created_at: new Date().toISOString(),
          message_count: 1,
          command_count: 0,
          kind: 'strategy_session',
          parent_session_id: 'operator-main',
          title: 'Momentum B Review',
        },
        messages: [],
        commands: [],
        artifacts: [],
      } satisfies OperatorSessionDetail
    }
    if (sessionId === 'strategy-session-older') {
      return {
        session: {
          id: 'strategy-session-older',
          user_input: 'check strategy A',
          intent: null,
          command: null,
          response: 'older branch summary',
          permitted: 1,
          duration_ms: 0,
          created_at: new Date(Date.now() - 60_000).toISOString(),
          message_count: 1,
          command_count: 0,
          kind: 'strategy_session',
          parent_session_id: 'operator-main',
          title: 'Momentum A Review',
        },
        messages: [],
        commands: [],
        artifacts: [],
      } satisfies OperatorSessionDetail
    }
    return {
      session: {
        id: 'operator-main',
        user_input: '[operator session]',
        intent: null,
        command: null,
        response: null,
        permitted: 1,
        duration_ms: 0,
        created_at: new Date().toISOString(),
        message_count: 0,
        command_count: 0,
        kind: 'command_session',
        parent_session_id: null,
        title: 'Operator Main',
      },
      messages: [],
      commands: [],
      artifacts: [],
    } satisfies OperatorSessionDetail
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.equal((calls.heartbeats[0] as any).active_branch_id, 'strategy-session-older')
  assert.equal((calls.heartbeats[0] as any).active_branch_kind, 'strategy_session')
  assert.equal((calls.heartbeats[0] as any).active_branch_title, 'Momentum A Review')
})

test('operator loop fetches authoritative active branch detail even when it falls outside the recent branch detail window', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const now = Date.now()
  const activeCreatedAt = new Date(now - 14 * 60_000).toISOString()
  backend.getOperatorStatus = async () =>
    ({
      agent_id: 'operator',
      status: 'online',
      mode: 'Observing',
      permission_mode: 'ReadOnly',
      is_busy: false,
      current_task_id: null,
      current_intent: 'resume operator session',
      queue_size: 0,
      last_seen_at: new Date(now).toISOString(),
      last_success_at: new Date(now).toISOString(),
      last_error: null,
      model_provider: 'pi-shell',
      session_id: 'operator-main',
      stale: false,
      stale_after_sec: 45,
      active_branch_id: 'strategy-session-active-old',
      active_branch_kind: 'strategy_session',
      active_branch_title: 'Legacy Active Branch',
    } as PersistedOperatorStatus)
  backend.getOperatorSessions = async () => {
    const sessions: OperatorSessionSummary[] = [
      {
        id: 'operator-main',
        user_input: '[operator session]',
        intent: null,
        command: null,
        response: null,
        permitted: 1,
        duration_ms: 0,
        created_at: new Date(now).toISOString(),
        message_count: 0,
        command_count: 0,
        kind: 'command_session',
        parent_session_id: null,
        title: 'Operator Main',
      },
    ]
    for (let index = 0; index < 13; index += 1) {
      sessions.push({
        id: `strategy-session-newer-${index}`,
        user_input: `review fresh branch ${index}`,
        intent: null,
        command: null,
        response: `recent branch ${index} summary`,
        permitted: 1,
        duration_ms: 0,
        created_at: new Date(now - index * 60_000).toISOString(),
        message_count: 1,
        command_count: 0,
        kind: 'strategy_session',
        parent_session_id: 'operator-main',
        title: `Fresh Branch ${index}`,
      })
    }
    sessions.push({
      id: 'strategy-session-active-old',
      user_input: 'Investigate the long-running promotion backlog branch.',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: activeCreatedAt,
      message_count: 2,
      command_count: 0,
      kind: 'strategy_session',
      parent_session_id: 'operator-main',
      title: 'Legacy Active Branch',
    })
    return sessions
  }
  backend.getOperatorSession = async (sessionId: string) => {
    if (sessionId === 'strategy-session-active-old') {
      return {
        session: {
          id: 'strategy-session-active-old',
          user_input: 'Investigate the long-running promotion backlog branch.',
          intent: null,
          command: null,
          response: null,
          permitted: 1,
          duration_ms: 0,
          created_at: activeCreatedAt,
          message_count: 2,
          command_count: 0,
          kind: 'strategy_session',
          parent_session_id: 'operator-main',
          title: 'Legacy Active Branch',
        },
        messages: [
          {
            id: 'message-active-old-1',
            session_id: 'strategy-session-active-old',
            role: 'assistant',
            message_type: 'result',
            related_event_id: null,
            content: {
              text: 'Legacy active branch still owns the pending follow-up queue for promotion blockers.',
            },
            created_at: activeCreatedAt,
          },
        ],
        commands: [],
        artifacts: [],
      } satisfies OperatorSessionDetail
    }
    if (sessionId.startsWith('strategy-session-newer-')) {
      return {
        session: {
          id: sessionId,
          user_input: `review ${sessionId}`,
          intent: null,
          command: null,
          response: `${sessionId} summary`,
          permitted: 1,
          duration_ms: 0,
          created_at: new Date(now).toISOString(),
          message_count: 1,
          command_count: 0,
          kind: 'strategy_session',
          parent_session_id: 'operator-main',
          title: sessionId,
        },
        messages: [],
        commands: [],
        artifacts: [],
      } satisfies OperatorSessionDetail
    }
    return {
      session: {
        id: 'operator-main',
        user_input: '[operator session]',
        intent: null,
        command: null,
        response: null,
        permitted: 1,
        duration_ms: 0,
        created_at: new Date(now).toISOString(),
        message_count: 0,
        command_count: 0,
        kind: 'command_session',
        parent_session_id: null,
        title: 'Operator Main',
      },
      messages: [],
      commands: [],
      artifacts: [],
    } satisfies OperatorSessionDetail
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.equal((calls.heartbeats[0] as any).active_branch_id, 'strategy-session-active-old')
  assert.match(
    calls.heartbeats[0].current_intent || '',
    /Legacy active branch still owns the pending follow-up queue/i,
  )
})

test('operator loop respects externally selected active branch before posting the next heartbeat', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  let statusReads = 0
  backend.getOperatorStatus = async () =>
    ({
      agent_id: 'operator',
      status: 'online',
      mode: 'Observing',
      permission_mode: 'ReadOnly',
      is_busy: false,
      current_task_id: null,
      current_intent: 'resume operator session',
      queue_size: 0,
      last_seen_at: new Date().toISOString(),
      last_success_at: new Date().toISOString(),
      last_error: null,
      model_provider: 'pi-shell',
      session_id: 'operator-main',
      stale: false,
      stale_after_sec: 45,
      active_branch_id: statusReads++ === 0 ? 'strategy-session-a' : 'strategy-session-b',
      active_branch_kind: 'strategy_session',
      active_branch_title: statusReads === 1 ? 'Momentum A Review' : 'Momentum B Review',
    } as PersistedOperatorStatus)
  backend.getOperatorSessions = async () => {
    const now = Date.now()
    return [
      {
        id: 'operator-main',
        user_input: '[operator session]',
        intent: null,
        command: null,
        response: null,
        permitted: 1,
        duration_ms: 0,
        created_at: new Date(now).toISOString(),
        message_count: 0,
        command_count: 0,
        kind: 'command_session',
        parent_session_id: null,
        title: 'Operator Main',
      },
      {
        id: 'strategy-session-a',
        user_input: 'review strategy A',
        intent: null,
        command: null,
        response: 'branch A summary',
        permitted: 1,
        duration_ms: 0,
        created_at: new Date(now - 60_000).toISOString(),
        message_count: 1,
        command_count: 0,
        kind: 'strategy_session',
        parent_session_id: 'operator-main',
        title: 'Momentum A Review',
      },
      {
        id: 'strategy-session-b',
        user_input: 'review strategy B',
        intent: null,
        command: null,
        response: 'branch B summary',
        permitted: 1,
        duration_ms: 0,
        created_at: new Date(now).toISOString(),
        message_count: 1,
        command_count: 0,
        kind: 'strategy_session',
        parent_session_id: 'operator-main',
        title: 'Momentum B Review',
      },
    ] satisfies OperatorSessionSummary[]
  }
  backend.getOperatorSession = async (sessionId: string) => {
    if (sessionId === 'strategy-session-a') {
      return {
        session: {
          id: 'strategy-session-a',
          user_input: 'review strategy A',
          intent: null,
          command: null,
          response: 'branch A summary',
          permitted: 1,
          duration_ms: 0,
          created_at: new Date(Date.now() - 60_000).toISOString(),
          message_count: 1,
          command_count: 0,
          kind: 'strategy_session',
          parent_session_id: 'operator-main',
          title: 'Momentum A Review',
        },
        messages: [],
        commands: [],
        artifacts: [],
      } satisfies OperatorSessionDetail
    }
    if (sessionId === 'strategy-session-b') {
      return {
        session: {
          id: 'strategy-session-b',
          user_input: 'review strategy B',
          intent: null,
          command: null,
          response: 'branch B summary',
          permitted: 1,
          duration_ms: 0,
          created_at: new Date().toISOString(),
          message_count: 1,
          command_count: 0,
          kind: 'strategy_session',
          parent_session_id: 'operator-main',
          title: 'Momentum B Review',
        },
        messages: [],
        commands: [],
        artifacts: [],
      } satisfies OperatorSessionDetail
    }
    return {
      session: {
        id: 'operator-main',
        user_input: '[operator session]',
        intent: null,
        command: null,
        response: null,
        permitted: 1,
        duration_ms: 0,
        created_at: new Date().toISOString(),
        message_count: 0,
        command_count: 0,
        kind: 'command_session',
        parent_session_id: null,
        title: 'Operator Main',
      },
      messages: [],
      commands: [],
      artifacts: [],
    } satisfies OperatorSessionDetail
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.equal((calls.heartbeats[0] as any).active_branch_id, 'strategy-session-b')
  assert.equal((calls.heartbeats[0] as any).active_branch_kind, 'strategy_session')
  assert.equal((calls.heartbeats[0] as any).active_branch_title, 'Momentum B Review')
})

test('operator loop keeps unresolved proposals visible after healthy restart recovery', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'ReadOnly',
    is_busy: false,
    current_task_id: null,
    current_intent: null,
    queue_size: 0,
    last_seen_at: new Date().toISOString(),
    last_success_at: new Date().toISOString(),
    last_error: null,
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    stale: false,
    stale_after_sec: 45,
  })
  backend.getOperatorSession = async () => ({
    session: {
      id: 'operator-main',
      user_input: '[operator session]',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: new Date().toISOString(),
      message_count: 3,
      command_count: 1,
    },
    messages: [],
    commands: [],
    artifacts: [
      {
        id: 'artifact-1',
        task_id: null,
        session_id: 'operator-main',
        artifact_type: 'strategy_promotion_proposal',
        title: 'Promote Momentum to live_candidate',
        content: { target_state: 'live_candidate' },
        created_at: new Date().toISOString(),
      },
    ],
  })

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.equal(calls.heartbeats[0].mode, 'Observing')
  assert.match(calls.heartbeats[0].current_intent || '', /unresolved proposal/i)
  assert.match(calls.heartbeats[0].current_intent || '', /Promote Momentum to live_candidate/)
})

test('operator loop reads compacted daily summary back into recovery intent', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString().slice(0, 10)
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'ReadOnly',
    is_busy: false,
    current_task_id: null,
    current_intent: null,
    queue_size: 0,
    last_seen_at: new Date().toISOString(),
    last_success_at: new Date().toISOString(),
    last_error: null,
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    stale: false,
    stale_after_sec: 45,
  })
  backend.getOperatorSession = async () => ({
    session: {
      id: 'operator-main',
      user_input: '[operator session]',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: new Date().toISOString(),
      message_count: 10,
      command_count: 4,
    },
    messages: [],
    commands: [],
    artifacts: [
      {
        id: 'artifact-summary-1',
        task_id: null,
        session_id: 'operator-main',
        artifact_type: 'operator_daily_summary',
        title: `Operator Daily Summary ${yesterday}`,
        content: {
          date: yesterday,
          path: `C:/tmp/daily_summary_${yesterday.replaceAll('-', '')}.md`,
          summary_excerpt:
            'Recent compacted memory: user said do not touch live trading; follow-up recommendation is to review promotion proposals.',
          sections: {
            major_user_instructions: ['Do not touch live trading.'],
            follow_up_recommendations: ['Review promotion proposals.'],
          },
        },
        created_at: new Date().toISOString(),
      },
    ],
  })

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.equal(calls.heartbeats[0].mode, 'Observing')
  assert.match(calls.heartbeats[0].current_intent || '', /do not touch live trading/i)
  assert.match(calls.heartbeats[0].current_intent || '', /review promotion proposals/i)
})

test('operator loop folds compacted summary sections beyond summary excerpt into recovery intent', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString().slice(0, 10)
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'ReadOnly',
    is_busy: false,
    current_task_id: null,
    current_intent: null,
    queue_size: 0,
    last_seen_at: new Date().toISOString(),
    last_success_at: new Date().toISOString(),
    last_error: null,
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    stale: false,
    stale_after_sec: 45,
  })
  backend.getOperatorSession = async () => ({
    session: {
      id: 'operator-main',
      user_input: '[operator session]',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: new Date().toISOString(),
      message_count: 10,
      command_count: 4,
    },
    messages: [],
    commands: [],
    artifacts: [
      {
        id: 'artifact-summary-2',
        task_id: null,
        session_id: 'operator-main',
        artifact_type: 'operator_daily_summary',
        title: `Operator Daily Summary ${yesterday}`,
        content: {
          date: yesterday,
          path: `C:/tmp/daily_summary_${yesterday.replaceAll('-', '')}.md`,
          summary_excerpt: 'Recent compacted memory is available for operator recovery.',
          sections: {
            major_user_instructions: ['Do not widen live-risk permissions.'],
            important_repair_decisions: ['Escalate stale market data instead of auto-retrying forever.'],
            unresolved_issues: ['Follow up on repeated strategy promotion reviews.'],
            follow_up_recommendations: ['Review the promotion backlog before enabling new experiments.'],
          },
        },
        created_at: new Date().toISOString(),
      },
    ],
  })

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.equal(calls.heartbeats[0].mode, 'Observing')
  assert.match(calls.heartbeats[0].current_intent || '', /do not widen live-risk permissions/i)
  assert.match(calls.heartbeats[0].current_intent || '', /stale market data/i)
  assert.match(calls.heartbeats[0].current_intent || '', /promotion backlog/i)
})

test('operator loop restores child session branch context into recovery intent', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const createdAt = new Date().toISOString()
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'ReadOnly',
    is_busy: false,
    current_task_id: null,
    current_intent: null,
    queue_size: 0,
    last_seen_at: createdAt,
    last_success_at: createdAt,
    last_error: null,
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    stale: false,
    stale_after_sec: 45,
  })
  backend.getOperatorSessions = async () => [
    {
      id: 'operator-main',
      user_input: '[operator session]',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: createdAt,
      message_count: 0,
      command_count: 0,
      kind: 'command_session',
      parent_session_id: null,
      title: 'Operator Main',
    },
    {
      id: 'strategy-task-7',
      user_input: 'Evaluate strategy state for strat-7.',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: createdAt,
      message_count: 2,
      command_count: 1,
      kind: 'strategy_session',
      parent_session_id: 'operator-main',
      title: 'Strategy Evaluation',
    },
  ]
  backend.getOperatorSession = async sessionId => {
    if (sessionId === 'strategy-task-7') {
      return {
        session: {
          id: 'strategy-task-7',
          user_input: 'Evaluate strategy state for strat-7.',
          intent: null,
          command: null,
          response: null,
          permitted: 1,
          duration_ms: 0,
          created_at: createdAt,
          message_count: 2,
          command_count: 1,
          kind: 'strategy_session',
          parent_session_id: 'operator-main',
          title: 'Strategy Evaluation',
        },
        messages: [
          {
            id: 'message-branch-1',
            session_id: 'strategy-task-7',
            role: 'assistant',
            message_type: 'result',
            related_event_id: null,
            content: {
              text: 'Momentum A remains under review until the promotion gate failures are cleared.',
            },
            created_at: createdAt,
          },
        ],
        commands: [],
        artifacts: [
          {
            id: 'artifact-branch-1',
            task_id: 'task-7',
            session_id: 'strategy-task-7',
            artifact_type: 'strategy_evaluation',
            title: 'Strategy Evaluation: Momentum A',
            content: {
              strategy_id: 'strat-7',
              strategy_name: 'Momentum A',
              summary:
                'Momentum A remains under review until the promotion gate failures are cleared.',
            },
            created_at: createdAt,
          },
        ],
      } satisfies OperatorSessionDetail
    }

    return {
      session: {
        id: 'operator-main',
        user_input: '[operator session]',
        intent: null,
        command: null,
        response: null,
        permitted: 1,
        duration_ms: 0,
        created_at: createdAt,
        message_count: 1,
        command_count: 0,
      },
      messages: [],
      commands: [],
      artifacts: [],
    } satisfies OperatorSessionDetail
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.equal(calls.heartbeats[0].mode, 'Observing')
  assert.match(calls.heartbeats[0].current_intent || '', /strategy evaluation/i)
  assert.match(calls.heartbeats[0].current_intent || '', /Momentum A/i)
})

test('operator loop folds nested descendant branch context into recovery intent', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const createdAt = new Date().toISOString()
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'ReadOnly',
    is_busy: false,
    current_task_id: null,
    current_intent: null,
    queue_size: 0,
    last_seen_at: createdAt,
    last_success_at: createdAt,
    last_error: null,
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    stale: false,
    stale_after_sec: 45,
  })
  backend.getOperatorSessions = async () => [
    {
      id: 'operator-main',
      user_input: '[operator session]',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: createdAt,
      message_count: 0,
      command_count: 0,
      kind: 'command_session',
      parent_session_id: null,
      title: 'Operator Main',
    },
    {
      id: 'strategy-parent-1',
      user_input: 'Investigate Momentum root.',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: createdAt,
      message_count: 0,
      command_count: 0,
      kind: 'strategy_session',
      parent_session_id: 'operator-main',
      title: 'Momentum Root',
    },
    {
      id: 'review-child-1',
      user_input: 'Review nested drawdown branch.',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: createdAt,
      message_count: 1,
      command_count: 0,
      kind: 'review_session',
      parent_session_id: 'strategy-parent-1',
      title: 'Nested Drawdown Review',
    },
  ]
  backend.getOperatorSession = async sessionId => {
    if (sessionId === 'review-child-1') {
      return {
        session: {
          id: 'review-child-1',
          user_input: 'Review nested drawdown branch.',
          intent: null,
          command: null,
          response: null,
          permitted: 1,
          duration_ms: 0,
          created_at: createdAt,
          message_count: 1,
          command_count: 0,
          kind: 'review_session',
          parent_session_id: 'strategy-parent-1',
          title: 'Nested Drawdown Review',
        },
        messages: [
          {
            id: 'message-nested-1',
            session_id: 'review-child-1',
            role: 'assistant',
            message_type: 'result',
            related_event_id: null,
            content: {
              text: 'Nested drawdown review found persistent losses that still need follow-up.',
            },
            created_at: createdAt,
          },
        ],
        commands: [],
        artifacts: [],
      } satisfies OperatorSessionDetail
    }
    if (sessionId === 'strategy-parent-1') {
      return {
        session: {
          id: 'strategy-parent-1',
          user_input: 'Investigate Momentum root.',
          intent: null,
          command: null,
          response: null,
          permitted: 1,
          duration_ms: 0,
          created_at: createdAt,
          message_count: 0,
          command_count: 0,
          kind: 'strategy_session',
          parent_session_id: 'operator-main',
          title: 'Momentum Root',
        },
        messages: [],
        commands: [],
        artifacts: [],
      } satisfies OperatorSessionDetail
    }

    return {
      session: {
        id: 'operator-main',
        user_input: '[operator session]',
        intent: null,
        command: null,
        response: null,
        permitted: 1,
        duration_ms: 0,
        created_at: createdAt,
        message_count: 0,
        command_count: 0,
      },
      messages: [],
      commands: [],
      artifacts: [],
    } satisfies OperatorSessionDetail
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.match(calls.heartbeats[0].current_intent || '', /persistent losses/i)
  assert.match(calls.heartbeats[0].current_intent || '', /follow-up/i)
})

test('operator loop folds multiple recent child session branches into recovery intent', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const createdAt = new Date().toISOString()
  const olderAt = new Date(Date.now() - 60 * 60 * 1000).toISOString()
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'ReadOnly',
    is_busy: false,
    current_task_id: null,
    current_intent: null,
    queue_size: 0,
    last_seen_at: createdAt,
    last_success_at: createdAt,
    last_error: null,
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    stale: false,
    stale_after_sec: 45,
  })
  backend.getOperatorSessions = async () => [
    {
      id: 'operator-main',
      user_input: '[operator session]',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: createdAt,
      message_count: 0,
      command_count: 0,
      kind: 'command_session',
      parent_session_id: null,
      title: 'Operator Main',
    },
    {
      id: 'strategy-task-9',
      user_input: 'Evaluate strategy state for strat-9.',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: createdAt,
      message_count: 2,
      command_count: 1,
      kind: 'strategy_session',
      parent_session_id: 'operator-main',
      title: 'Strategy Evaluation',
    },
    {
      id: 'diagnostic-task-2',
      user_input: 'Diagnose gateway drift.',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: olderAt,
      message_count: 2,
      command_count: 1,
      kind: 'diagnostic_session',
      parent_session_id: 'operator-main',
      title: 'Repair Decision',
    },
  ]
  backend.getOperatorSession = async sessionId => {
    if (sessionId === 'strategy-task-9') {
      return {
        session: {
          id: 'strategy-task-9',
          user_input: 'Evaluate strategy state for strat-9.',
          intent: null,
          command: null,
          response: null,
          permitted: 1,
          duration_ms: 0,
          created_at: createdAt,
          message_count: 2,
          command_count: 1,
          kind: 'strategy_session',
          parent_session_id: 'operator-main',
          title: 'Strategy Evaluation',
        },
        messages: [],
        commands: [],
        artifacts: [
          {
            id: 'artifact-branch-9',
            task_id: 'task-9',
            session_id: 'strategy-task-9',
            artifact_type: 'strategy_evaluation',
            title: 'Strategy Evaluation: Momentum B',
            content: {
              summary: 'Momentum B still needs more paper evidence before promotion.',
            },
            created_at: createdAt,
          },
        ],
      } satisfies OperatorSessionDetail
    }
    if (sessionId === 'diagnostic-task-2') {
      return {
        session: {
          id: 'diagnostic-task-2',
          user_input: 'Diagnose gateway drift.',
          intent: null,
          command: null,
          response: null,
          permitted: 1,
          duration_ms: 0,
          created_at: olderAt,
          message_count: 2,
          command_count: 1,
          kind: 'diagnostic_session',
          parent_session_id: 'operator-main',
          title: 'Repair Decision',
        },
        messages: [
          {
            id: 'message-branch-2',
            session_id: 'diagnostic-task-2',
            role: 'assistant',
            message_type: 'result',
            related_event_id: null,
            content: {
              text: 'Gateway drift was already diagnosed; keep the restart todo visible.',
            },
            created_at: olderAt,
          },
        ],
        commands: [],
        artifacts: [],
      } satisfies OperatorSessionDetail
    }

    return {
      session: {
        id: 'operator-main',
        user_input: '[operator session]',
        intent: null,
        command: null,
        response: null,
        permitted: 1,
        duration_ms: 0,
        created_at: createdAt,
        message_count: 1,
        command_count: 0,
      },
      messages: [],
      commands: [],
      artifacts: [],
    } satisfies OperatorSessionDetail
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.match(calls.heartbeats[0].current_intent || '', /Momentum B/i)
  assert.match(calls.heartbeats[0].current_intent || '', /restart todo visible/i)
})

test('operator loop can fold more than two recent child session branches into recovery intent when budget allows', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const createdAt = new Date().toISOString()
  const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString()
  const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString()
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'ReadOnly',
    is_busy: false,
    current_task_id: null,
    current_intent: null,
    queue_size: 0,
    last_seen_at: createdAt,
    last_success_at: createdAt,
    last_error: null,
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    stale: false,
    stale_after_sec: 45,
  })
  backend.getOperatorSessions = async () => [
    {
      id: 'operator-main',
      user_input: '[operator session]',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: createdAt,
      message_count: 0,
      command_count: 0,
      kind: 'command_session',
      parent_session_id: null,
      title: 'Operator Main',
    },
    {
      id: 'strategy-task-a',
      user_input: 'Evaluate strategy state A.',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: createdAt,
      message_count: 1,
      command_count: 1,
      kind: 'strategy_session',
      parent_session_id: 'operator-main',
      title: 'Strategy A',
    },
    {
      id: 'diagnostic-task-b',
      user_input: 'Diagnose gateway drift B.',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: oneHourAgo,
      message_count: 1,
      command_count: 1,
      kind: 'diagnostic_session',
      parent_session_id: 'operator-main',
      title: 'Repair B',
    },
    {
      id: 'review-task-c',
      user_input: 'Generate review C.',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: twoHoursAgo,
      message_count: 1,
      command_count: 1,
      kind: 'review_session',
      parent_session_id: 'operator-main',
      title: 'Review C',
    },
  ]
  backend.getOperatorSession = async sessionId => {
    if (sessionId === 'strategy-task-a') {
      return {
        session: {
          id: 'strategy-task-a',
          user_input: 'Evaluate strategy state A.',
          intent: null,
          command: null,
          response: null,
          permitted: 1,
          duration_ms: 0,
          created_at: createdAt,
          message_count: 1,
          command_count: 1,
          kind: 'strategy_session',
          parent_session_id: 'operator-main',
          title: 'Strategy A',
        },
        messages: [],
        commands: [],
        artifacts: [
          {
            id: 'artifact-branch-a',
            task_id: 'task-a',
            session_id: 'strategy-task-a',
            artifact_type: 'strategy_evaluation',
            title: 'Strategy Evaluation: Alpha',
            content: {
              summary: 'Alpha is still under evaluation.',
            },
            created_at: createdAt,
          },
        ],
      } satisfies OperatorSessionDetail
    }
    if (sessionId === 'diagnostic-task-b') {
      return {
        session: {
          id: 'diagnostic-task-b',
          user_input: 'Diagnose gateway drift B.',
          intent: null,
          command: null,
          response: null,
          permitted: 1,
          duration_ms: 0,
          created_at: oneHourAgo,
          message_count: 1,
          command_count: 1,
          kind: 'diagnostic_session',
          parent_session_id: 'operator-main',
          title: 'Repair B',
        },
        messages: [
          {
            id: 'message-branch-b',
            session_id: 'diagnostic-task-b',
            role: 'assistant',
            message_type: 'result',
            related_event_id: null,
            content: {
              text: 'Gateway drift B already has a visible restart todo.',
            },
            created_at: oneHourAgo,
          },
        ],
        commands: [],
        artifacts: [],
      } satisfies OperatorSessionDetail
    }
    if (sessionId === 'review-task-c') {
      return {
        session: {
          id: 'review-task-c',
          user_input: 'Generate review C.',
          intent: null,
          command: null,
          response: null,
          permitted: 1,
          duration_ms: 0,
          created_at: twoHoursAgo,
          message_count: 1,
          command_count: 1,
          kind: 'review_session',
          parent_session_id: 'operator-main',
          title: 'Review C',
        },
        messages: [
          {
            id: 'message-branch-c',
            session_id: 'review-task-c',
            role: 'assistant',
            message_type: 'result',
            related_event_id: null,
            content: {
              text: 'Review C still needs one more pass for closed fills.',
            },
            created_at: twoHoursAgo,
          },
        ],
        commands: [],
        artifacts: [],
      } satisfies OperatorSessionDetail
    }

    return {
      session: {
        id: 'operator-main',
        user_input: '[operator session]',
        intent: null,
        command: null,
        response: null,
        permitted: 1,
        duration_ms: 0,
        created_at: createdAt,
        message_count: 0,
        command_count: 0,
      },
      messages: [],
      commands: [],
      artifacts: [],
    } satisfies OperatorSessionDetail
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.match(calls.heartbeats[0].current_intent || '', /Alpha is still under evaluation/i)
  assert.match(calls.heartbeats[0].current_intent || '', /restart todo/i)
  assert.match(calls.heartbeats[0].current_intent || '', /Review C still needs one more pass/i)
})

test('operator loop folds multiple recent compacted daily summaries into recovery intent', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000)
  const twoDaysAgo = new Date(Date.now() - 48 * 60 * 60 * 1000)
  const yesterdayDate = yesterday.toISOString().slice(0, 10)
  const twoDaysAgoDate = twoDaysAgo.toISOString().slice(0, 10)
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'ReadOnly',
    is_busy: false,
    current_task_id: null,
    current_intent: null,
    queue_size: 0,
    last_seen_at: new Date().toISOString(),
    last_success_at: new Date().toISOString(),
    last_error: null,
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    stale: false,
    stale_after_sec: 45,
  })
  backend.getOperatorSession = async () => ({
    session: {
      id: 'operator-main',
      user_input: '[operator session]',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: new Date().toISOString(),
      message_count: 10,
      command_count: 4,
    },
    messages: [],
    commands: [],
    artifacts: [
      {
        id: 'artifact-summary-latest',
        task_id: null,
        session_id: 'operator-main',
        artifact_type: 'operator_daily_summary',
        title: `Operator Daily Summary ${yesterdayDate}`,
        content: {
          date: yesterdayDate,
          path: `C:/tmp/daily_summary_${yesterdayDate.replaceAll('-', '')}.md`,
          summary_excerpt: 'Latest compacted memory: review the strategy promotion queue.',
          sections: {
            follow_up_recommendations: ['Review the strategy promotion queue.'],
          },
        },
        created_at: yesterday.toISOString(),
      },
      {
        id: 'artifact-summary-older',
        task_id: null,
        session_id: 'operator-main',
        artifact_type: 'operator_daily_summary',
        title: `Operator Daily Summary ${twoDaysAgoDate}`,
        content: {
          date: twoDaysAgoDate,
          path: `C:/tmp/daily_summary_${twoDaysAgoDate.replaceAll('-', '')}.md`,
          summary_excerpt: 'Older compacted memory: keep live-risk permissions unchanged.',
          sections: {
            major_user_instructions: ['Keep live-risk permissions unchanged.'],
          },
        },
        created_at: twoDaysAgo.toISOString(),
      },
    ],
  })

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.match(calls.heartbeats[0].current_intent || '', /strategy promotion queue/i)
  assert.match(calls.heartbeats[0].current_intent || '', /live-risk permissions unchanged/i)
})

test('operator loop keeps longer recovery context within a bounded budget', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const now = new Date()
  const mainCreatedAt = now.toISOString()
  const sessions = Array.from({ length: 8 }, (_, index) => {
    const createdAt = new Date(now.getTime() - index * 60 * 60 * 1000).toISOString()
    return {
      id: `strategy-task-${index}`,
      user_input: `Evaluate strategy state ${index}.`,
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: createdAt,
      message_count: 1,
      command_count: 1,
      kind: 'strategy_session',
      parent_session_id: 'operator-main',
      title: `Strategy ${index}`,
    }
  })

  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'ReadOnly',
    is_busy: false,
    current_task_id: null,
    current_intent: null,
    queue_size: 0,
    last_seen_at: mainCreatedAt,
    last_success_at: mainCreatedAt,
    last_error: null,
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    stale: false,
    stale_after_sec: 45,
  })
  backend.getOperatorSessions = async () => [
    {
      id: 'operator-main',
      user_input: '[operator session]',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: mainCreatedAt,
      message_count: 0,
      command_count: 0,
      kind: 'command_session',
      parent_session_id: null,
      title: 'Operator Main',
    },
    ...sessions,
  ]
  backend.getOperatorSession = async sessionId => {
    if (sessionId === 'operator-main') {
      return {
        session: {
          id: 'operator-main',
          user_input: '[operator session]',
          intent: null,
          command: null,
          response: null,
          permitted: 1,
          duration_ms: 0,
          created_at: mainCreatedAt,
          message_count: 0,
          command_count: 0,
        },
        messages: [],
        commands: [],
        artifacts: Array.from({ length: 8 }, (_, index) => {
          const createdAt = new Date(now.getTime() - index * 24 * 60 * 60 * 1000).toISOString()
          const day = createdAt.slice(0, 10)
          return {
            id: `artifact-summary-${index}`,
            task_id: null,
            session_id: 'operator-main',
            artifact_type: 'operator_daily_summary',
            title: `Operator Daily Summary ${day}`,
            content: {
              date: day,
              path: `C:/tmp/daily_summary_${day.replaceAll('-', '')}.md`,
              summary_excerpt: `Summary ${index}: ${'x'.repeat(110)}`,
              sections: {
                follow_up_recommendations: [`Follow-up ${index}: ${'y'.repeat(90)}`],
              },
            },
            created_at: createdAt,
          }
        }),
      } satisfies OperatorSessionDetail
    }

    const session = sessions.find(item => item.id === sessionId)
    if (!session) {
      throw new Error(`unexpected session ${sessionId}`)
    }
    return {
      session,
      messages: [
        {
          id: `message-${sessionId}`,
          session_id: sessionId,
          role: 'assistant',
          message_type: 'result',
          related_event_id: null,
          content: {
            text: `${session.title} context: ${'z'.repeat(120)}`,
          },
          created_at: session.created_at,
        },
      ],
      commands: [],
      artifacts: [],
    } satisfies OperatorSessionDetail
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.ok((calls.heartbeats[0].current_intent || '').length <= 1200)
  assert.match(calls.heartbeats[0].current_intent || '', /Strategy 0 context/i)
})

test('operator loop resumes degraded when recent failed tasks still need review', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'ReadOnly',
    is_busy: false,
    current_task_id: null,
    current_intent: null,
    queue_size: 0,
    last_seen_at: new Date().toISOString(),
    last_success_at: new Date().toISOString(),
    last_error: null,
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    stale: false,
    stale_after_sec: 45,
  })
  backend.getOperatorTasks = async status => {
    if (status === 'failed') {
      return [
        {
          id: 'task-failed-1',
          task_type: 'diagnose_system_health',
          status: 'failed',
          priority: 'high',
          created_by: 'operator_loop',
          assigned_to: 'operator',
          input_data: {},
          output_data: null,
          error: 'market_data_stale:45m',
          started_at: new Date().toISOString(),
          completed_at: new Date().toISOString(),
          created_at: new Date().toISOString(),
        },
      ]
    }
    return []
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.equal(calls.heartbeats[0].mode, 'Degraded')
  assert.match(calls.heartbeats[0].current_intent || '', /market_data_stale:45m/)
})

test('operator loop folds recent operator error events into recovery intent even after last_error clears', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'ReadOnly',
    is_busy: false,
    current_task_id: null,
    current_intent: null,
    queue_size: 0,
    last_seen_at: new Date().toISOString(),
    last_success_at: new Date().toISOString(),
    last_error: null,
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    stale: false,
    stale_after_sec: 45,
  })
  ;(backend as any).getOperatorEvents = async () => [
    {
      id: 'event-repair-failed-1',
      event_type: 'repair_failed',
      severity: 'high',
      source: 'operator_loop',
      session_id: 'diagnostic-session-1',
      task_id: 'task-1',
      related_entity_type: 'repair',
      related_entity_id: 'repair-1',
      payload: {
        command: 'create_diagnostic_todo',
        response: 'repair attempt failed because market data is still stale',
      },
      created_at: new Date().toISOString(),
    },
  ]

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.equal(calls.heartbeats[0].mode, 'Degraded')
  assert.match(calls.heartbeats[0].current_intent || '', /recent operator error/i)
  assert.match(calls.heartbeats[0].current_intent || '', /market data is still stale/i)
})

test('operator loop falls back to latest heartbeat intent when restart has no fresher backlog context', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'ReadOnly',
    is_busy: false,
    current_task_id: null,
    current_intent: 'Continue reviewing repeated scheduler drift before proposing any cadence change.',
    queue_size: 0,
    last_seen_at: new Date().toISOString(),
    last_success_at: new Date().toISOString(),
    last_error: null,
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    stale: false,
    stale_after_sec: 45,
  })
  ;(backend as any).getOperatorEvents = async () => []

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.equal(calls.heartbeats[0].mode, 'Observing')
  assert.match(calls.heartbeats[0].current_intent || '', /scheduler drift/i)
  assert.match(calls.heartbeats[0].current_intent || '', /cadence change/i)
})

test('operator loop does not materialize proposal tasks when operator permission cannot execute them', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getAgentRuns = async () =>
    [
      {
        id: 'run-1',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        status: 'failed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '["structured output parse failed"]',
        cost_estimate: null,
      },
      {
        id: 'run-2',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        status: 'failed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '["structured output parse failed"]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[]

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.equal(calls.createdTasks.length, 0)
  assert.equal(calls.executedCommands.length, 0)
  assert.equal(calls.heartbeats[0].current_task_id, null)
})

test('operator loop refreshes stale market observer activity through typed run_market_scan', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const fiftyMinutesAgo = new Date(Date.now() - 50 * 60 * 1000).toISOString()
  const freshTimestamp = new Date().toISOString()
  // First getAgentRuns call (pre-command, from loadSubsystemReviewData): stale — triggers recommendation.
  // Second getAgentRuns call (post-command verification): fresh — proves condition resolved.
  let agentRunCallCount = 0
  backend.getAgentRuns = async () => {
    agentRunCallCount += 1
    if (agentRunCallCount === 1) {
      return [
        {
          id: 'run-market-observer-stale',
          agent_name: 'market_observer',
          trigger_type: 'scheduled',
          permission_mode: 'Observe',
          started_at: fiftyMinutesAgo,
          ended_at: fiftyMinutesAgo,
          status: 'completed',
          model_provider: null,
          model_name: null,
          tools_called: '[]',
          outputs_created: '[]',
          errors: '[]',
          cost_estimate: null,
        },
      ] satisfies AgentRunSummary[]
    }
    return [
      {
        id: 'run-market-observer-after-refresh',
        agent_name: 'market_observer',
        trigger_type: 'manual',
        permission_mode: 'Observe',
        started_at: freshTimestamp,
        ended_at: freshTimestamp,
        status: 'completed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '[]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[]
  }
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Triggered manual market scan. New run run-market-refresh-1 finished with status completed.',
      result: {
        message: 'Triggered manual market scan. New run run-market-refresh-1 finished with status completed.',
        run: {
          agent_name: 'market_observer',
          run_id: 'run-market-refresh-1',
          status: 'completed',
          errors: [],
          outputs: ['opportunity:btc'],
        },
      },
      action: null,
      risk_level: 'operational',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'PaperAuto',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.equal(calls.createdTasks.length, 1)
  assert.equal(calls.createdTasks[0].task_type, 'run_market_scan')
  assert.equal(calls.createdSessions.length, 1)
  assert.equal(calls.createdSessions[0].kind, 'command_session')
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'run_market_scan')
  assert.equal(calls.heartbeats[0].mode, 'Trading')
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  assert.match(
    String(calls.updatedTasks[0].payload.output_data?.summary || ''),
    /Triggered manual market scan/i,
  )
  assert.equal(calls.updatedTasks[0].payload.output_data?.verification, 'resolved')
  assert.equal(calls.updatedTasks[0].payload.output_data?.verified_run_id, 'run-market-observer-after-refresh')
  // Verification used a fresh read — exactly two getAgentRuns calls total.
  assert.equal(agentRunCallCount, 2)
  const refreshedEvent = calls.createdEvents.find(e => e.event_type === 'market_scan_refreshed')
  assert.ok(refreshedEvent, 'market_scan_refreshed event must be emitted on resolved path')
  assert.equal((refreshedEvent?.payload as Record<string, unknown>)?.verification, 'resolved')
})

test('operator loop marks market scan task as failed when command succeeds but Market Observer state remains stale after verification', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const fiftyMinutesAgo = new Date(Date.now() - 50 * 60 * 1000).toISOString()
  // Both pre-command and post-command getAgentRuns calls return the same stale run.
  backend.getAgentRuns = async () =>
    [
      {
        id: 'run-market-observer-stale-forever',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: fiftyMinutesAgo,
        ended_at: fiftyMinutesAgo,
        status: 'completed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '[]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[]
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Market scan triggered.',
      result: { run: { run_id: 'run-stale-check-1', status: 'completed' } },
      action: null,
      risk_level: 'operational',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'PaperAuto',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'run_market_scan')
  // Task must be failed, not completed — command success does not equal condition resolved.
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'failed')
  assert.equal(calls.updatedTasks[0].payload.output_data?.verification, 'unresolved')
  assert.match(String(calls.updatedTasks[0].payload.error || ''), /stale after verification/i)
  const failedEvent = calls.createdEvents.find(e => e.event_type === 'market_scan_refresh_failed')
  assert.ok(failedEvent, 'market_scan_refresh_failed event must be emitted on unresolved path')
  assert.equal((failedEvent?.payload as Record<string, unknown>)?.verification, 'unresolved')
  // market_scan_refreshed must NOT be emitted — condition was not verified.
  assert.equal(calls.createdEvents.some(e => e.event_type === 'market_scan_refreshed'), false)
})

test('operator loop marks market scan task as failed when post-command authoritative read is unavailable', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const fiftyMinutesAgo = new Date(Date.now() - 50 * 60 * 1000).toISOString()
  let agentRunCallCount = 0
  backend.getAgentRuns = async () => {
    agentRunCallCount += 1
    if (agentRunCallCount === 1) {
      return [
        {
          id: 'run-market-observer-stale-unavail',
          agent_name: 'market_observer',
          trigger_type: 'scheduled',
          permission_mode: 'Observe',
          started_at: fiftyMinutesAgo,
          ended_at: fiftyMinutesAgo,
          status: 'completed',
          model_provider: null,
          model_name: null,
          tools_called: '[]',
          outputs_created: '[]',
          errors: '[]',
          cost_estimate: null,
        },
      ] satisfies AgentRunSummary[]
    }
    // Second call (post-command verification) fails — authoritative state unavailable.
    throw new Error('gateway_timeout: agent-runs endpoint unreachable')
  }
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Market scan triggered.',
      result: { run: { run_id: 'run-unavail-check-1', status: 'completed' } },
      action: null,
      risk_level: 'operational',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'PaperAuto',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'run_market_scan')
  // Task must be failed — verification read was unavailable.
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'failed')
  assert.equal(calls.updatedTasks[0].payload.output_data?.verification, 'unavailable')
  assert.match(String(calls.updatedTasks[0].payload.error || ''), /could not be read for verification/i)
  const failedEvent = calls.createdEvents.find(e => e.event_type === 'market_scan_refresh_failed')
  assert.ok(failedEvent)
  assert.equal((failedEvent?.payload as Record<string, unknown>)?.verification, 'unavailable')
  // market_scan_refreshed must NOT be emitted — verification was not obtained.
  assert.equal(calls.createdEvents.some(e => e.event_type === 'market_scan_refreshed'), false)
})

test('market scan post-action verification performs a fresh getAgentRuns read not the pre-command snapshot', async () => {
  // The pre-command cached data is stale — it triggered the recommendation.
  // After the command, the verification must call getAgentRuns() directly, not reuse
  // the cached subsystem data. This test proves two separate reads happen.
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const fiftyMinutesAgo = new Date(Date.now() - 50 * 60 * 1000).toISOString()
  const freshTimestamp = new Date().toISOString()
  let agentRunCallCount = 0
  backend.getAgentRuns = async () => {
    agentRunCallCount += 1
    // Pre-command read: stale
    if (agentRunCallCount === 1) {
      return [
        {
          id: 'pre-command-stale-run',
          agent_name: 'market_observer',
          trigger_type: 'scheduled',
          permission_mode: 'Observe',
          started_at: fiftyMinutesAgo,
          ended_at: fiftyMinutesAgo,
          status: 'completed',
          model_provider: null,
          model_name: null,
          tools_called: '[]',
          outputs_created: '[]',
          errors: '[]',
          cost_estimate: null,
        },
      ] satisfies AgentRunSummary[]
    }
    // Post-command verification read: fresh
    return [
      {
        id: 'post-command-fresh-run',
        agent_name: 'market_observer',
        trigger_type: 'manual',
        permission_mode: 'Observe',
        started_at: freshTimestamp,
        ended_at: freshTimestamp,
        status: 'completed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '[]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[]
  }
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Market scan triggered.',
      result: null,
      action: null,
      risk_level: 'operational',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'PaperAuto',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.runOnce()

  // Two separate getAgentRuns reads must have occurred: one pre-command and one post-command.
  assert.equal(agentRunCallCount, 2, 'exactly two getAgentRuns reads: pre-command + post-command verification')
  // The post-command verification used the fresh run, not the stale pre-command snapshot.
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  assert.equal(calls.updatedTasks[0].payload.output_data?.verified_run_id, 'post-command-fresh-run')
})

test('operator loop parents market-scan task sessions under the persisted active branch', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const createdAt = new Date().toISOString()
  const fiftyMinutesAgo = new Date(Date.now() - 50 * 60 * 1000).toISOString()
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'PaperAuto',
    is_busy: false,
    current_task_id: null,
    current_intent: 'Continue investigating Momentum root.',
    queue_size: 0,
    last_seen_at: createdAt,
    last_success_at: createdAt,
    last_error: null,
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    active_branch_id: 'strategy-session-parent',
    active_branch_kind: 'strategy_session',
    active_branch_title: 'Momentum Root',
    stale: false,
    stale_after_sec: 45,
  })
  backend.getOperatorSessions = async () => [
    {
      id: 'operator-main',
      user_input: '[operator session]',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: createdAt,
      message_count: 0,
      command_count: 0,
      kind: 'command_session',
      parent_session_id: null,
      title: 'Operator Main',
    },
    {
      id: 'strategy-session-parent',
      user_input: 'Investigate Momentum root.',
      intent: null,
      command: null,
      response: 'Momentum root branch is still active.',
      permitted: 1,
      duration_ms: 0,
      created_at: createdAt,
      message_count: 2,
      command_count: 1,
      kind: 'strategy_session',
      parent_session_id: 'operator-main',
      title: 'Momentum Root',
    },
  ]
  backend.getAgentRuns = async () =>
    [
      {
        id: 'run-market-observer-stale-parented',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: fiftyMinutesAgo,
        ended_at: fiftyMinutesAgo,
        status: 'completed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '[]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[]
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Triggered manual market scan for active branch context.',
      result: {
        message: 'Triggered manual market scan for active branch context.',
        run: {
          agent_name: 'market_observer',
          run_id: 'run-market-refresh-parented-1',
          status: 'completed',
          errors: [],
          outputs: ['opportunity:eth'],
        },
      },
      action: null,
      risk_level: 'operational',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'PaperAuto',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.equal(calls.createdSessions.length, 1)
  assert.equal(calls.createdSessions[0].parent_session_id, 'strategy-session-parent')
})

test('operator loop marks a repeatedly rejected symbol observe-only through typed set_symbol_observe_only', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const fiveMinutesAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString()
  const today = new Date().toISOString().slice(0, 10)
  backend.getDreamingSummary = async () => ({
    recent_research_notes: [],
    recent_strategy_hypotheses: [],
    deprecated_memories: [],
    recent_dreaming_logs: [{ id: 'dream-log-fresh-observe-only' }],
    recent_dreaming_runs: [{ id: 'dream-run-fresh-observe-only' }],
    recent_daily_reviews: [{ id: 'daily-review-today', created_at: `${today}T01:00:00Z` }],
  })
  backend.getAgentRuns = async () =>
    [
      {
        id: 'run-market-observer-fresh-observe-only',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: fiveMinutesAgo,
        ended_at: fiveMinutesAgo,
        status: 'completed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '[]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[]
  backend.getRiskSummary = async () => ({
    recent_rejected_decisions: [
      {
        id: 'risk-btc-1',
        symbol: 'BTC/USDT',
        execution_mode: 'paper_internal',
      },
      {
        id: 'risk-btc-2',
        symbol: 'BTC/USDT',
        execution_mode: 'paper_internal',
      },
    ],
    recent_risk_boundary_proposals: [],
    needs_operator_risk_boundary_proposals: [],
    needs_operator_observe_only_symbols: [
      {
        symbol: 'BTC/USDT',
        rejection_count: 2,
        summary:
          'BTC/USDT triggered repeated paper-side risk rejections and should be left observe-only until setup quality improves.',
        decision_ids: ['risk-btc-1', 'risk-btc-2'],
        execution_modes: ['paper_internal'],
      },
    ],
  })
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'BTC/USDT is now observe-only.',
      result: {
        updated: true,
        symbol: 'BTC/USDT',
        message: 'BTC/USDT is now observe-only.',
        symbol_list: ['ETHUSDT', 'SOLUSDT'],
        observed_symbols: ['BTC/USDT', 'ETH/USDT'],
      },
      action: null,
      risk_level: 'operational',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'PaperAuto',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.ok(calls.createdTasks.some(task => task.task_type === 'set_symbol_observe_only'))
  assert.equal(calls.createdSessions.length, 1)
  assert.equal(calls.createdSessions[0].kind, 'command_session')
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'set_symbol_observe_only')
  assert.equal(calls.executedCommands[0].payload.parameters?.symbol, 'BTC/USDT')
  assert.equal(calls.heartbeats[0].mode, 'Trading')
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  assert.equal(calls.updatedTasks[0].payload.output_data?.symbol, 'BTC/USDT')
})

test('operator loop reruns a recent failed agent task through typed rerun_failed_agent_task', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const twentyMinutesAgo = new Date(Date.now() - 20 * 60 * 1000).toISOString()
  const fiveMinutesAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString()
  backend.getAgentRuns = async () =>
    [
      {
        id: 'run-market-observer-fresh',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: fiveMinutesAgo,
        ended_at: fiveMinutesAgo,
        status: 'completed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '[]',
        cost_estimate: null,
      },
      {
        id: 'run-review-failed',
        agent_name: 'post_trade_reviewer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: twentyMinutesAgo,
        ended_at: twentyMinutesAgo,
        status: 'failed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '["timeout"]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[]
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response:
        'Reran failed agent task for post_trade_reviewer. Original run run-review-failed, new run run-review-rerun-1 is completed.',
      result: {
        message:
          'Reran failed agent task for post_trade_reviewer. Original run run-review-failed, new run run-review-rerun-1 is completed.',
        rerun: {
          agent_name: 'post_trade_reviewer',
          original_run_id: 'run-review-failed',
          new_run_id: 'run-review-rerun-1',
          status: 'completed',
          errors: [],
          outputs: ['post_trade_review:abc123'],
        },
      },
      action: null,
      risk_level: 'operational',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'PaperAuto',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.ok(calls.createdTasks.some(task => task.task_type === 'rerun_failed_agent_task'))
  assert.equal(calls.createdSessions.length, 1)
  assert.equal(calls.createdSessions[0].kind, 'command_session')
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'rerun_failed_agent_task')
  assert.equal(calls.executedCommands[0].payload.parameters?.run_id, 'run-review-failed')
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  assert.equal(calls.updatedTasks[0].payload.output_data?.new_run_id, 'run-review-rerun-1')
  assert.match(
    String(calls.updatedTasks[0].payload.output_data?.summary || ''),
    /Reran failed agent task/i,
  )
})

test('operator loop refreshes the non-critical daily review report through typed create_daily_review', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const fiveMinutesAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString()
  const today = new Date().toISOString().slice(0, 10)
  backend.getDreamingSummary = async () => ({
    recent_research_notes: [],
    recent_strategy_hypotheses: [],
    deprecated_memories: [],
    recent_dreaming_logs: [{ id: 'dream-log-1' }],
    recent_dreaming_runs: [{ id: 'dream-run-1' }],
    recent_daily_reviews: [],
  })
  backend.getAgentRuns = async () =>
    [
      {
        id: 'run-market-observer-fresh',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: fiveMinutesAgo,
        ended_at: fiveMinutesAgo,
        status: 'completed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '[]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[]
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: `Created daily review Operator Daily Review ${today}.`,
      result: {
        created: true,
        id: 'daily-review-1',
        title: `Operator Daily Review ${today}`,
      },
      action: null,
      risk_level: 'low_risk_write',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'Research',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.equal(calls.createdTasks.length, 1)
  assert.equal(calls.createdTasks[0].task_type, 'create_daily_review')
  assert.equal(calls.createdSessions.length, 1)
  assert.equal(calls.createdSessions[0].kind, 'review_session')
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'create_daily_review')
  assert.equal(calls.executedCommands[0].payload.parameters?.title, `Operator Daily Review ${today}`)
  assert.match(String(calls.executedCommands[0].payload.parameters?.content || ''), /## System/i)
  assert.equal(calls.heartbeats[0].mode, 'Reviewing')
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  assert.equal(calls.updatedTasks[0].payload.output_data?.review_id, 'daily-review-1')
})

test('operator loop refreshes the non-critical agent performance report through typed create_review_report', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const twentyMinutesAgo = new Date(Date.now() - 20 * 60 * 1000).toISOString()
  backend.getDreamingSummary = async () => ({
    recent_research_notes: [],
    recent_strategy_hypotheses: [],
    deprecated_memories: [],
    recent_dreaming_logs: [{ id: 'dream-log-1' }],
    recent_dreaming_runs: [{ id: 'dream-run-1' }],
    recent_daily_reviews: [{ id: 'daily-review-today', created_at: `${new Date().toISOString().slice(0, 10)}T01:00:00Z` }],
  })
  backend.getAgentRuns = async () =>
    [
      {
        id: 'run-paper-failed',
        agent_name: 'paper_trader',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: twentyMinutesAgo,
        ended_at: twentyMinutesAgo,
        status: 'failed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '["timeout"]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[]
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Created agent performance review report.',
      result: {
        created: true,
        artifact_id: 'report-artifact-1',
        path: 'C:/tmp/agent-performance-report.md',
        report_type: 'agent_performance',
        title: String(payload.parameters?.title || ''),
      },
      action: null,
      risk_level: 'low_risk_write',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'Research',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.equal(calls.createdTasks.length, 1)
  assert.equal(calls.createdTasks[0].task_type, 'create_review_report')
  assert.equal(calls.createdSessions.length, 1)
  assert.equal(calls.createdSessions[0].kind, 'review_session')
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'create_review_report')
  assert.equal(calls.executedCommands[0].payload.parameters?.report_type, 'agent_performance')
  assert.match(String(calls.executedCommands[0].payload.parameters?.content || ''), /## Agent Activity/i)
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  assert.equal(calls.updatedTasks[0].payload.output_data?.artifact_id, 'report-artifact-1')
})

test('operator loop refreshes the system health diagnostic report through typed create_diagnostic_report', async () => {
  const snapshot = makeSnapshot({
    health: {
      status: 'degraded',
      issues: ['market_data_stale:45m'],
    },
  })
  const { backend, calls } = makeFakeBackend(snapshot)
  const restoredTaskRuntime = new TaskRuntime(backend)
  restoredTaskRuntime.restore([
    makeTask({
      id: 'task-diagnostic-report',
      task_type: 'create_diagnostic_report',
      status: 'running',
      input_data: {
        recommendation_id: 'system-health-report-refresh',
        kind: 'system_health_report_refresh_due',
        summary: 'Refresh system health diagnostic report.',
        report_type: 'system_health',
        title: 'System Health Diagnostic Report',
      },
    }),
  ])
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Created system health diagnostic report.',
      result: {
        created: true,
        artifact_id: 'diagnostic-artifact-1',
        path: 'C:/tmp/system-health-report.md',
        report_type: 'system_health',
        title: String(payload.parameters?.title || ''),
      },
      action: null,
      risk_level: 'low_risk_write',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'Research',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: restoredTaskRuntime,
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.ok(calls.createdTasks.some(task => task.task_type === 'diagnose_system_health'))
  assert.ok(calls.createdSessions.some(session => session.kind === 'diagnostic_session'))
  const diagnosticExecution = calls.executedCommands.find(
    command => command.commandName === 'create_diagnostic_report',
  )
  assert.ok(diagnosticExecution)
  assert.equal(diagnosticExecution?.payload.parameters?.report_type, 'system_health')
  assert.match(String(diagnosticExecution?.payload.parameters?.content || ''), /market_data_stale:45m/i)
  assert.ok(
    calls.updatedTasks.some(
      update =>
        update.payload.status === 'completed' &&
        update.payload.output_data?.artifact_id === 'diagnostic-artifact-1',
    ),
  )
})

test('operator loop refreshes the worker health diagnostic report through typed create_diagnostic_report', async () => {
  const snapshot = makeSnapshot({
    health: {
      status: 'degraded',
      issues: ['agent_failure_ratio:0.40>0.25'],
    },
  })
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getAgentRuns = async () => [
    {
      id: 'run-paper-failed',
      agent_name: 'paper_trader',
      trigger_type: 'scheduled',
      permission_mode: 'Observe',
      started_at: new Date(Date.now() - 20 * 60 * 1000).toISOString(),
      ended_at: new Date(Date.now() - 19 * 60 * 1000).toISOString(),
      status: 'failed',
      model_provider: 'pi-shell',
      model_name: 'test-model',
      tools_called: '[]',
      outputs_created: '[]',
      errors: '["timeout"]',
      cost_estimate: null,
    },
  ] satisfies AgentRunSummary[]
  const restoredTaskRuntime = new TaskRuntime(backend)
  restoredTaskRuntime.restore([
    makeTask({
      id: 'task-worker-diagnostic-report',
      task_type: 'create_diagnostic_report',
      status: 'running',
      input_data: {
        recommendation_id: 'worker-health-review',
        kind: 'worker_health_review_due',
        summary: 'Refresh worker health diagnostic report.',
        report_type: 'worker_health',
        title: 'Worker Health Diagnostic Report',
      },
    }),
  ])
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Created worker health diagnostic report.',
      result: {
        created: true,
        artifact_id: 'diagnostic-artifact-worker-1',
        path: 'C:/tmp/worker-health-report.md',
        report_type: 'worker_health',
        title: String(payload.parameters?.title || ''),
      },
      action: null,
      risk_level: 'low_risk_write',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'Research',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: restoredTaskRuntime,
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  const diagnosticExecution = calls.executedCommands.find(
    command => command.commandName === 'create_diagnostic_report',
  )
  assert.ok(diagnosticExecution)
  assert.equal(diagnosticExecution?.payload.parameters?.report_type, 'worker_health')
  assert.match(String(diagnosticExecution?.payload.parameters?.content || ''), /paper_trader/i)
  assert.match(String(diagnosticExecution?.payload.parameters?.content || ''), /timeout/i)
  assert.ok(
    calls.updatedTasks.some(
      update =>
        update.payload.status === 'completed' &&
        update.payload.output_data?.artifact_id === 'diagnostic-artifact-worker-1',
    ),
  )
})

test('operator loop re-enters a recovered in-flight repair proposal task from task payload when recommendation is gone', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'AllowEditing',
    is_busy: true,
    current_task_id: 'task-recovered-proposal',
    current_intent: 'Recover prompt proposal',
    queue_size: 1,
    last_seen_at: new Date().toISOString(),
    last_success_at: new Date().toISOString(),
    last_error: null,
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    stale: false,
    stale_after_sec: 45,
  })
  backend.getOperatorTasks = async status => {
    if (status === undefined || status === 'running') {
      return [
        {
          id: 'task-recovered-proposal',
          task_type: 'create_repair_proposal',
          status: 'running',
          priority: 'normal',
          created_by: 'operator_loop',
          assigned_to: 'operator',
          input_data: {
            recommendation_id: 'agent_prompt_change_proposal_due:market_observer',
            kind: 'agent_prompt_change_proposal_due',
            summary: 'market_observer keeps failing and needs a prompt/playbook proposal.',
            agent_name: 'market_observer',
            target: 'market_observer',
            error_samples: ['structured output parse failed', 'structured output parse failed again'],
          },
          output_data: null,
          error: null,
          started_at: new Date().toISOString(),
          completed_at: null,
          created_at: new Date().toISOString(),
        },
      ]
    }
    return []
  }
  backend.getAgentRuns = async () =>
    [
      {
        id: 'run-market-observer-fresh-recovery',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        status: 'completed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '[]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[]
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Created a prompt-change proposal for market_observer from recovered task context.',
      result: {
        proposed: true,
        artifact_id: 'artifact-proposal-recovered-1',
        message: 'Created a prompt-change proposal for market_observer from recovered task context.',
      },
      action: null,
      risk_level: 'proposal_only',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'AllowEditing',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const restored = await loop.runOnce()

  assert.equal(restored, true)
  assert.equal(calls.createdTasks.length, 0)
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'propose_prompt_change')
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].taskId, 'task-recovered-proposal')
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  const eventTypes = calls.createdEvents.map(item => item.event_type)
  assert.ok(eventTypes.includes('proposal_created'))
})

test('operator loop emits started and stopped lifecycle events', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.start()
  await loop.stop()

  const eventTypes = calls.createdEvents.map(item => item.event_type)
  assert.ok(eventTypes.includes('operator_started'))
  assert.ok(eventTypes.includes('operator_stopped'))
})

test('operator loop auto-compacts the previous UTC day once when missing', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString().slice(0, 10)
  backend.getOperatorSession = async () => ({
    session: {
      id: 'operator-main',
      user_input: '[operator session]',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: new Date().toISOString(),
      message_count: 0,
      command_count: 0,
    },
    messages: [],
    commands: [],
    artifacts: [],
  })

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const ok = await loop.runOnce()
  await loop.runOnce()

  assert.equal(ok, true)
  assert.deepEqual(calls.compactions, [yesterday])
})

test('operator loop does not re-compact a day that already has a summary artifact', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString().slice(0, 10)
  backend.getOperatorSession = async () => ({
    session: {
      id: 'operator-main',
      user_input: '[operator session]',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: new Date().toISOString(),
      message_count: 0,
      command_count: 0,
    },
    messages: [],
    commands: [],
    artifacts: [
      {
        id: 'artifact-summary-1',
        task_id: null,
        session_id: 'operator-main',
        artifact_type: 'operator_daily_summary',
        title: `Operator Daily Summary ${yesterday}`,
        content: { date: yesterday, path: `C:/tmp/daily_summary_${yesterday.replaceAll('-', '')}.md` },
        created_at: new Date().toISOString(),
      },
    ],
  })

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await loop.recoverState()
  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.deepEqual(calls.compactions, [])
})

test('operator loop delegates dreaming tasks to dreaming_agent and closes the task', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getDreamingSummary = async () => ({
    recent_research_notes: [],
    recent_strategy_hypotheses: [],
    deprecated_memories: [],
    recent_dreaming_logs: [],
    recent_dreaming_runs: [],
    recent_daily_reviews: [{ id: 'daily-review-today', created_at: `${new Date().toISOString().slice(0, 10)}T01:00:00Z` }],
  })

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.equal(calls.createdTasks.length, 1)
  assert.equal(calls.createdTasks[0].task_type, 'run_dreaming')
  assert.equal(calls.createdTasks[0].assigned_to, 'dreaming_agent')
  assert.deepEqual(calls.startedAgents, ['dreaming_agent'])
  assert.equal(calls.createdSessions.length, 1)
  assert.equal(calls.createdSessions[0].kind, 'dreaming_session')
  assert.equal(calls.createdSessions[0].parent_session_id, 'operator-main')
  assert.equal(calls.createdMessages.length, 3)
  assert.equal(calls.requestedContexts.length, 1)
  assert.equal(calls.createdMessages[0].message_type, 'context')
  const eventTypes = calls.createdEvents.map(item => item.event_type)
  assert.ok(eventTypes.includes('dreaming_started'))
  assert.ok(eventTypes.includes('dreaming_completed'))
  assert.equal(calls.heartbeats[0].mode, 'Dreaming')
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
})

test('operator loop delegates review tasks to post_trade_reviewer and closes the task', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getReviewSummary = async () => ({
    recent_closed_positions: [{ id: 'pos-1', strategy_id: 'strat-1' }],
    recent_post_trade_reviews: [],
    recent_post_trade_reviewer_runs: [],
    missing_review_strategy_ids: ['strat-1'],
  })

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'ReadOnly',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.equal(calls.createdTasks.length, 1)
  assert.equal(calls.createdTasks[0].task_type, 'generate_review')
  assert.equal(calls.createdTasks[0].assigned_to, 'post_trade_reviewer')
  assert.deepEqual(calls.startedAgents, ['post_trade_reviewer'])
  assert.equal(calls.createdSessions.length, 1)
  assert.equal(calls.createdSessions[0].kind, 'review_session')
  assert.equal(calls.createdSessions[0].parent_session_id, 'operator-main')
  assert.equal(calls.createdMessages.length, 3)
  assert.equal(calls.requestedContexts.length, 1)
  assert.equal(calls.createdMessages[0].message_type, 'context')
  const eventTypes = calls.createdEvents.map(item => item.event_type)
  assert.ok(eventTypes.includes('review_started'))
  assert.ok(eventTypes.includes('review_completed'))
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
})

test('operator loop delegates risk critique tasks to risk_critic and closes the task', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getDreamingSummary = async () => ({
    recent_research_notes: [],
    recent_strategy_hypotheses: [],
    deprecated_memories: [],
    recent_dreaming_logs: [{ id: 'dream-log-fresh-risk' }],
    recent_dreaming_runs: [{ id: 'dream-run-fresh-risk' }],
    recent_daily_reviews: [{ id: 'daily-review-today', created_at: `${new Date().toISOString().slice(0, 10)}T01:00:00Z` }],
  })
  backend.getRiskSummary = async () => ({
    recent_rejected_decisions: [{ id: 'risk-1', symbol: 'BTC/USDT', execution_mode: 'paper_internal' }],
    recent_risk_boundary_proposals: [],
    needs_operator_risk_boundary_proposals: [],
  })

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'Research',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.ok(calls.createdTasks.some(task => task.task_type === 'run_risk_critique'))
  assert.ok(calls.createdTasks.some(task => task.assigned_to === 'risk_critic'))
  assert.deepEqual(calls.startedAgents, ['risk_critic'])
  assert.equal(calls.createdSessions.length, 1)
  assert.equal(calls.createdSessions[0].kind, 'review_session')
  const eventTypes = calls.createdEvents.map(item => item.event_type)
  assert.ok(eventTypes.includes('risk_critique_started'))
  assert.ok(eventTypes.includes('risk_critique_completed'))
  assert.equal(calls.heartbeats[0].mode, 'Reviewing')
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
})

test('operator loop delegates portfolio snapshot refresh tasks to portfolio_agent and closes the task', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getPortfolioSummary = async () => ({
    total_value_usdt: 605,
    unrealized_pnl: 5,
    realized_pnl: 0,
    today_pnl: 5,
    positions_count: 1,
    exposure_by_symbol: { BTCUSDT: 605 },
  })
  backend.getPortfolioHistory = async () => []

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'Research',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.ok(calls.createdTasks.some(task => task.task_type === 'run_portfolio_snapshot'))
  assert.ok(calls.createdTasks.some(task => task.assigned_to === 'portfolio_agent'))
  assert.deepEqual(calls.startedAgents, ['portfolio_agent'])
  assert.equal(calls.createdSessions.length, 1)
  assert.equal(calls.createdSessions[0].kind, 'review_session')
  const eventTypes = calls.createdEvents.map(item => item.event_type)
  assert.ok(eventTypes.includes('portfolio_snapshot_started'))
  assert.ok(eventTypes.includes('portfolio_snapshot_completed'))
  assert.equal(calls.heartbeats[0].mode, 'Reviewing')
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
})

test('operator loop materializes and executes low-risk repair decisions through typed operator commands', async () => {
  const snapshot = makeSnapshot({
    health: {
      status: 'degraded',
      issues: ['market_data_stale:45m'],
    },
  })
  const { backend, calls } = makeFakeBackend(snapshot)

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'Research',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.ok(calls.createdArtifacts.some(item => item.artifact_type === 'repair_decision'))
  assert.ok(calls.createdSessions.some(item => item.kind === 'diagnostic_session'))
  assert.equal(calls.requestedContexts.length, 1)
  assert.equal(calls.createdMessages[0].message_type, 'context')
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'create_diagnostic_todo')
  const eventTypes = calls.createdEvents.map(item => item.event_type)
  assert.ok(eventTypes.includes('repair_attempted'))
  assert.ok(eventTypes.includes('repair_executed'))
  assert.ok(calls.createdTasks.some(task => task.task_type === 'diagnose_system_health'))
  assert.ok(calls.createdTasks.some(task => task.task_type === 'create_diagnostic_report'))
  assert.ok(calls.updatedTasks.some(item => item.payload.status === 'completed'))
})

test('operator loop does not repeat a persisted repair attempt after restart recovery', async () => {
  const snapshot = makeSnapshot({
    health: {
      status: 'degraded',
      issues: ['market_data_stale:45m'],
    },
  })
  const firstRuntime = makeFakeBackend(snapshot)
  const firstLoop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'Research',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: firstRuntime.backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(firstRuntime.backend),
      eventRouter: new EventRouter(firstRuntime.backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  assert.equal(await firstLoop.runOnce(), true)
  assert.equal(firstRuntime.calls.executedCommands.length, 1)
  const firstAttempt = firstRuntime.calls.createdEvents.find(
    event => event.event_type === 'repair_attempted',
  )
  assert.ok(firstAttempt)
  const persistedAttempt = {
    id: 'event-repair-attempted-1',
    runtime_id: null,
    event_type: firstAttempt.event_type,
    severity: firstAttempt.severity,
    source: firstAttempt.source,
    session_id: firstAttempt.session_id || null,
    task_id: firstAttempt.task_id || null,
    related_entity_type: firstAttempt.related_entity_type || null,
    related_entity_id: firstAttempt.related_entity_id || null,
    payload: firstAttempt.payload || {},
    created_at: new Date().toISOString(),
  } satisfies PersistedOperatorEvent

  const restartedRuntime = makeFakeBackend(snapshot)
  const newerContextEvents = Array.from({ length: 20 }, (_, index) => ({
    id: `event-context-${index}`,
    runtime_id: null,
    event_type: 'operator_observed',
    severity: 'info',
    source: 'operator_loop',
    session_id: 'operator-main',
    task_id: null,
    related_entity_type: null,
    related_entity_id: null,
    payload: {},
    created_at: new Date(Date.now() - index * 1_000).toISOString(),
  } satisfies PersistedOperatorEvent))
  const persistedEvents = [...newerContextEvents, persistedAttempt]
  restartedRuntime.backend.getOperatorEvents = async (limit = 50) => persistedEvents.slice(0, limit)
  const restartedLoop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'Research',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: restartedRuntime.backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(restartedRuntime.backend),
      eventRouter: new EventRouter(restartedRuntime.backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  await restartedLoop.recoverState()
  assert.equal(await restartedLoop.runOnce(), true)

  assert.equal(restartedRuntime.calls.executedCommands.length, 0)
  assert.equal(restartedRuntime.calls.createdArtifacts.length, 0)
  assert.equal(restartedRuntime.calls.createdSessions.length, 0)
  assert.deepEqual(
    restartedRuntime.calls.createdEvents.filter(event => event.event_type.startsWith('repair_')),
    [],
  )
})

test('operator loop parents repair-decision sessions under the persisted active branch', async () => {
  const snapshot = makeSnapshot({
    health: {
      status: 'degraded',
      issues: ['market_data_stale:45m'],
    },
  })
  const { backend, calls } = makeFakeBackend(snapshot)
  const createdAt = new Date().toISOString()
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'Research',
    is_busy: false,
    current_task_id: null,
    current_intent: 'Keep working inside the nested diagnostic branch.',
    queue_size: 0,
    last_seen_at: createdAt,
    last_success_at: createdAt,
    last_error: null,
    model_provider: 'pi-shell',
    session_id: 'operator-main',
    active_branch_id: 'diagnostic-parent-1',
    active_branch_kind: 'diagnostic_session',
    active_branch_title: 'Nested Diagnostic Parent',
    stale: false,
    stale_after_sec: 45,
  })
  backend.getOperatorSessions = async () => [
    {
      id: 'operator-main',
      user_input: '[operator session]',
      intent: null,
      command: null,
      response: null,
      permitted: 1,
      duration_ms: 0,
      created_at: createdAt,
      message_count: 0,
      command_count: 0,
      kind: 'command_session',
      parent_session_id: null,
      title: 'Operator Main',
    },
    {
      id: 'diagnostic-parent-1',
      user_input: 'Inspect stale gateway branch.',
      intent: null,
      command: null,
      response: 'Nested diagnostic branch is still in progress.',
      permitted: 1,
      duration_ms: 0,
      created_at: createdAt,
      message_count: 1,
      command_count: 0,
      kind: 'diagnostic_session',
      parent_session_id: 'operator-main',
      title: 'Nested Diagnostic Parent',
    },
  ]

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'Research',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.equal(calls.createdSessions.length, 1)
  assert.equal(calls.createdSessions[0].parent_session_id, 'diagnostic-parent-1')
})



test('operator loop materializes proposal-only prompt change decisions through typed proposal commands', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getAgentRuns = async () =>
    [
      {
        id: 'run-1',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        status: 'failed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '["structured output parse failed"]',
        cost_estimate: null,
      },
      {
        id: 'run-2',
        agent_name: 'market_observer',
        trigger_type: 'scheduled',
        permission_mode: 'Observe',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        status: 'failed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '["structured output parse failed"]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[]
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Created a prompt-change proposal for market_observer.',
      result: {
        proposed: true,
        artifact_id: 'artifact-proposal-2',
        message: 'Created a prompt-change proposal for market_observer.',
        proposal: {
          target: 'market_observer',
          requires_human_approval: true,
        },
      },
      action: null,
      risk_level: 'proposal_only',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'AllowEditing',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.ok(calls.createdTasks.some(task => task.task_type === 'create_repair_proposal'))
  assert.equal(calls.createdArtifacts.length, 1)
  assert.equal(calls.createdArtifacts[0].artifact_type, 'repair_decision')
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'propose_prompt_change')
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  const eventTypes = calls.createdEvents.map(item => item.event_type)
  assert.ok(eventTypes.includes('repair_attempted'))
  assert.ok(eventTypes.includes('proposal_created'))
})

test('operator loop materializes proposal-only schedule change decisions through typed proposal commands', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getAgentRuns = async () =>
    [
      {
        id: 'run-m1',
        agent_name: 'market_observer',
        trigger_type: 'manual',
        permission_mode: 'Observe',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        status: 'completed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '[]',
        cost_estimate: null,
      },
      {
        id: 'run-m2',
        agent_name: 'market_observer',
        trigger_type: 'manual',
        permission_mode: 'Observe',
        started_at: new Date().toISOString(),
        ended_at: new Date().toISOString(),
        status: 'completed',
        model_provider: null,
        model_name: null,
        tools_called: '[]',
        outputs_created: '[]',
        errors: '[]',
        cost_estimate: null,
      },
    ] satisfies AgentRunSummary[]
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Created a schedule-change proposal for market_observer.',
      result: {
        proposed: true,
        artifact_id: 'artifact-proposal-3',
        message: 'Created a schedule-change proposal for market_observer.',
        proposal: {
          agent_name: 'market_observer',
          requested_schedule: 'every 10 minutes',
          requires_human_approval: true,
        },
      },
      action: null,
      risk_level: 'proposal_only',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'AllowEditing',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.ok(calls.createdTasks.some(task => task.task_type === 'create_repair_proposal'))
  assert.equal(calls.createdArtifacts.length, 1)
  assert.equal(calls.createdArtifacts[0].artifact_type, 'repair_decision')
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'propose_schedule_change')
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  const eventTypes = calls.createdEvents.map(item => item.event_type)
  assert.ok(eventTypes.includes('repair_attempted'))
  assert.ok(eventTypes.includes('proposal_created'))
})


test('operator loop materializes proposal-only risk boundary decisions through typed proposal commands', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getRiskSummary = async () => ({
    recent_rejected_decisions: [
      {
        id: 'risk-1',
        proposal_id: 'proposal-1',
        approved: false,
        violations: ['exceeds_demo_position_margin:300.0>200.0'],
      },
      {
        id: 'risk-2',
        proposal_id: 'proposal-2',
        approved: false,
        violations: ['exceeds_demo_position_margin:300.0>200.0'],
      },
    ],
    recent_risk_boundary_proposals: [],
    needs_operator_risk_boundary_proposals: [
      {
        boundary_key: 'max_demo_position_notional_usdt',
        violation_kind: 'exceeds_demo_position_margin',
        risk_mode: 'demo',
        policy_id: 'demo-policy',
        policy_revision: 2,
        policy_hash: 'demo-policy-hash',
        rejection_count: 2,
        current_value: 200,
        suggested_value: 300,
        changes: {
          max_demo_position_notional_usdt: 300,
        },
        summary:
          'Recent risk rejections repeatedly hit the demo position-margin cap and should be proposed for human review.',
        sample_violations: ['exceeds_demo_position_margin:300.0>200.0'],
        decision_ids: ['risk-1', 'risk-2'],
      },
    ],
  })
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Created a risk-boundary change proposal.',
      result: {
        proposed: true,
        artifact_id: 'artifact-proposal-4',
        message: 'Created a risk-boundary change proposal.',
        proposal: {
          changes: {
            max_demo_position_notional_usdt: 300,
          },
          requires_human_approval: true,
        },
      },
      action: null,
      risk_level: 'proposal_only',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'AllowEditing',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.ok(calls.createdTasks.some(task => task.task_type === 'create_repair_proposal'))
  assert.equal(calls.createdArtifacts.length, 1)
  assert.equal(calls.createdArtifacts[0].artifact_type, 'repair_decision')
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'propose_risk_boundary_change')
  assert.equal(calls.executedCommands[0].payload.parameters?.risk_mode, 'demo')
  assert.equal(calls.executedCommands[0].payload.parameters?.policy_id, 'demo-policy')
  assert.equal(calls.executedCommands[0].payload.parameters?.policy_revision, 2)
  assert.equal(calls.executedCommands[0].payload.parameters?.policy_hash, 'demo-policy-hash')
  assert.ok(calls.updatedTasks.some(item => item.payload.status === 'completed'))
  const eventTypes = calls.createdEvents.map(item => item.event_type)
  assert.ok(eventTypes.includes('repair_attempted'))
  assert.ok(eventTypes.includes('proposal_created'))
})

test('operator loop materializes proposal-only Command Center behavior decisions through typed proposal commands', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getOperatorCommands = async () =>
    [
      {
        id: 'cmd-1',
        session_id: 'session-command-1',
        command: 'pause_agents',
        parameters: {},
        permitted: false,
        result_summary:
          "Command 'pause_agents' requires operator permission mode 'PaperAuto' but current mode is 'ReadOnly'.",
        created_at: new Date().toISOString(),
        session_kind: 'command_session',
        session_user_input: 'pause everything',
      },
      {
        id: 'cmd-2',
        session_id: 'session-command-2',
        command: 'pause_agents',
        parameters: {},
        permitted: false,
        result_summary:
          "Command 'pause_agents' requires operator permission mode 'PaperAuto' but current mode is 'ReadOnly'.",
        created_at: new Date().toISOString(),
        session_kind: 'command_session',
        session_user_input: 'pause agents now',
      },
    ] satisfies PersistedOperatorCommand[]
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Created a Command Center behavior proposal.',
      result: {
        proposed: true,
        artifact_id: 'artifact-proposal-5',
        message: 'Created a Command Center behavior proposal.',
        proposal: {
          target_command: 'pause_agents',
          requires_human_approval: true,
        },
      },
      action: null,
      risk_level: 'proposal_only',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'AllowEditing',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.equal(calls.createdTasks.length, 1)
  assert.equal(calls.createdTasks[0].task_type, 'create_repair_proposal')
  assert.equal(calls.createdArtifacts.length, 1)
  assert.equal(calls.createdArtifacts[0].artifact_type, 'repair_decision')
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(
    calls.executedCommands[0].commandName,
    'propose_command_center_behavior_change',
  )
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  const eventTypes = calls.createdEvents.map(item => item.event_type)
  assert.ok(eventTypes.includes('repair_attempted'))
  assert.ok(eventTypes.includes('proposal_created'))
})

test('operator loop materializes extension enablement proposals after a passing sandbox test', async () => {
  const { backend, calls } = makeFakeBackend(makeSnapshot())

  ;(backend as any).getOperatorExtensions = async () => [
    {
      id: 'extension-1',
      session_id: 'operator-main',
      extension_name: 'Explain Proposal',
      slash_command: '/explain-proposal',
      description: 'Open a structured explanation flow for the latest proposal.',
      target_command: 'explain_proposal',
      manifest: {
        slash_command: '/explain-proposal',
        target_command: 'explain_proposal',
      },
      status: 'sandbox_passed',
      created_by: 'operator',
      proposal_artifact_id: null,
      latest_run_id: 'extension-run-1',
      enabled_at: null,
      enabled_by: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
  ]
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Created an extension enablement proposal.',
      result: {
        proposed: true,
        artifact_id: 'artifact-extension-proposal-1',
        message: 'Created an extension enablement proposal.',
        proposal: {
          extension_id: 'extension-1',
          requires_human_approval: true,
        },
      },
      action: null,
      risk_level: 'proposal_only',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
  }

  const loop = new OperatorLoop(
    {
      gatewayBaseUrl: 'http://127.0.0.1:8787',
      heartbeatIntervalSec: 10,
      permissionMode: 'AllowEditing',
      mainSessionId: 'operator-main',
      modelProvider: 'pi-shell',
      once: true,
    },
    {
      backendClient: backend,
      sessionRuntime: new SessionRuntime('operator-main'),
      taskRuntime: new TaskRuntime(backend),
      eventRouter: new EventRouter(backend),
      repairEngine: new RepairEngine(),
      recommendationEngine: new RecommendationEngine(),
    },
  )

  const ok = await loop.runOnce()

  assert.equal(ok, true)
  assert.equal(calls.createdTasks.length, 1)
  assert.equal(calls.createdTasks[0].task_type, 'create_repair_proposal')
  assert.equal(calls.createdArtifacts.length, 1)
  assert.equal(calls.createdArtifacts[0].artifact_type, 'repair_decision')
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'propose_extension_enablement')
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  const eventTypes = calls.createdEvents.map(item => item.event_type)
  assert.ok(eventTypes.includes('repair_attempted'))
  assert.ok(eventTypes.includes('proposal_created'))
})

test('pi runtime adapter returns structured result from llm-session endpoint', async () => {
  const { PiRuntimeAdapter } = await import('../src/pi_runtime_adapter.js')

  let capturedBody: unknown = null
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (url: RequestInfo | URL, init?: RequestInit) => {
    capturedBody = JSON.parse(init?.body as string)
    return new Response(JSON.stringify({
      status: 'completed',
      turns_used: 2,
      commands_executed: [{ command: 'get_system_status', permitted: true, response: 'all ok' }],
      final_answer: 'System is healthy. No issues detected.',
      learning_manifest: { manifest_version: 'operator_diagnostic_learning:v1', lesson_ids: ['lesson-1'] },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }

  try {
    const adapter = new PiRuntimeAdapter({} as any, 'http://localhost:8787')
    const result = await adapter.runSession({
      goal: 'Diagnose system health status',
      allowedCommands: ['get_system_status', 'get_agent_activity'],
      maxTurns: 5,
    })

    assert.equal(result.status, 'completed')
    assert.equal(result.turnsUsed, 2)
    assert.equal(result.commandsExecuted.length, 1)
    assert.equal(result.commandsExecuted[0].command, 'get_system_status')
    assert.equal(result.finalAnswer, 'System is healthy. No issues detected.')
    assert.deepEqual(result.learningManifest, {
      manifest_version: 'operator_diagnostic_learning:v1',
      lesson_ids: ['lesson-1'],
    })

    const body = capturedBody as Record<string, unknown>
    assert.equal(body.goal, 'Diagnose system health status')
    assert.equal(body.max_turns, 5)
    assert.deepEqual(body.allowed_commands, ['get_system_status', 'get_agent_activity'])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('pi runtime adapter handles fetch errors gracefully', async () => {
  const { PiRuntimeAdapter } = await import('../src/pi_runtime_adapter.js')

  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => {
    throw new Error('connection refused')
  }

  try {
    const adapter = new PiRuntimeAdapter({} as any, 'http://localhost:8787')
    const result = await adapter.runSession({ goal: 'test' })

    assert.equal(result.status, 'error')
    assert.equal(result.turnsUsed, 0)
    assert.equal(result.finalAnswer, null)
    assert.ok(result.error?.includes('connection refused'))
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('recommendation engine surfaces scheduler failure cluster when two or more distinct agents fail in recent window', () => {
  const engine = new RecommendationEngine()
  const now = new Date().toISOString()
  const recentAgentRuns: AgentRunSummary[] = [
    { id: 'r1', agent_name: 'market_observer', trigger_type: 'scheduled', started_at: now, ended_at: now, status: 'failed' },
    { id: 'r2', agent_name: 'paper_trader', trigger_type: 'scheduled', started_at: now, ended_at: now, status: 'error' },
  ]
  const recommendations = engine.list(
    makeSnapshot(),
    undefined, undefined, undefined,
    recentAgentRuns,
  )
  const cluster = recommendations.find(item => item.kind === 'scheduler_failure_cluster')
  assert.ok(cluster, 'scheduler_failure_cluster recommendation should be produced')
  assert.equal(cluster?.severity, 'high')
  assert.equal(cluster?.taskType, 'pi_reasoning_session')
  assert.equal(cluster?.metadata?.failing_agent_count, 2)
  assert.deepEqual(cluster?.metadata?.failing_agents, ['market_observer', 'paper_trader'])
})

test('recommendation engine does not surface scheduler failure cluster when only one agent fails', () => {
  const engine = new RecommendationEngine()
  const now = new Date().toISOString()
  const recentAgentRuns: AgentRunSummary[] = [
    { id: 'r1', agent_name: 'market_observer', trigger_type: 'scheduled', started_at: now, ended_at: now, status: 'failed' },
    { id: 'r2', agent_name: 'market_observer', trigger_type: 'scheduled', started_at: now, ended_at: now, status: 'failed' },
  ]
  const recommendations = engine.list(
    makeSnapshot(),
    undefined, undefined, undefined,
    recentAgentRuns,
  )
  assert.equal(
    recommendations.some(item => item.kind === 'scheduler_failure_cluster'),
    false,
    'should not fire when only one distinct agent fails',
  )
})

test('recommendation engine surfaces open position rate spike when current count exceeds 2x baseline average', () => {
  const engine = new RecommendationEngine()
  const base = new Date(Date.now() - 60 * 60 * 1000)
  const makeSnapshot_ = (minsAgo: number, count: number): PortfolioSnapshotRecord => ({
    id: `snap-${minsAgo}`,
    total_value_usdt: 1000,
    unrealized_pnl: 0,
    realized_pnl: 0,
    positions_count: count,
    execution_mode: 'paper',
    snapshot_at: new Date(base.getTime() - minsAgo * 60 * 1000).toISOString(),
  })
  // current = 10 positions; baseline avg = 3 (snapshots at 10/20/30 mins ago)
  const portfolioHistory = [
    makeSnapshot_(0, 10),  // most recent
    makeSnapshot_(10, 3),
    makeSnapshot_(20, 3),
    makeSnapshot_(30, 3),
  ]
  const recommendations = engine.list(
    makeSnapshot(),
    undefined, undefined, undefined, undefined, undefined, undefined, undefined, undefined,
    undefined, portfolioHistory,
  )
  const spike = recommendations.find(item => item.kind === 'open_position_rate_spike')
  assert.ok(spike, 'open_position_rate_spike recommendation should be produced')
  assert.equal(spike?.metadata?.current_count, 10)
  assert.equal(spike?.metadata?.baseline_avg, 3)
  assert.equal(spike?.taskType, 'pi_reasoning_session')
})

test('recommendation engine does not surface open position spike with insufficient portfolio history', () => {
  const engine = new RecommendationEngine()
  const now = new Date().toISOString()
  const portfolioHistory: PortfolioSnapshotRecord[] = [
    { id: 'snap-1', total_value_usdt: 1000, unrealized_pnl: 0, realized_pnl: 0, positions_count: 10, execution_mode: 'paper', snapshot_at: now },
    { id: 'snap-2', total_value_usdt: 1000, unrealized_pnl: 0, realized_pnl: 0, positions_count: 2, execution_mode: 'paper', snapshot_at: now },
  ]
  const recommendations = engine.list(
    makeSnapshot(),
    undefined, undefined, undefined, undefined, undefined, undefined, undefined, undefined,
    undefined, portfolioHistory,
  )
  assert.equal(
    recommendations.some(item => item.kind === 'open_position_rate_spike'),
    false,
    'should not fire with fewer than 4 snapshots (1 current + 3 baseline)',
  )
})
