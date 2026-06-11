import assert from 'node:assert/strict'
import test from 'node:test'

import { EventRouter } from '../src/event_router.js'
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
  OperatorSessionDetail,
  OperatorSessionSummary,
  OperatorRecommendation,
  PersistedOperatorStatus,
  PersistedOperatorTask,
  OperatorSessionCreate,
  RiskSummary,
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
    agentRunReads: 0,
    sessionDetailReads: 0,
    commandReads: 0,
    taskReads: 0,
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
    async getOperatorTasks() {
      calls.taskReads += 1
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

test('recommendation engine marks strategy evaluation as due when flagged strategies lack a fresh evaluation', () => {
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
      under_review_strategies: [{ id: 'strat-1', name: 'Momentum A' }],
      paused_paper_strategies: [],
      live_candidate_strategies: [],
      recent_strategy_evaluations: [],
      needs_operator_evaluation_strategy_ids: ['strat-1'],
      recent_strategy_promotion_proposals: [],
      needs_operator_promotion_proposal_strategy_ids: [],
    },
  )

  const strategyReview = recommendations.find(item => item.kind === 'strategy_review_due')
  assert.ok(strategyReview)
  assert.equal(strategyReview?.taskType, 'evaluate_strategy_state')
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
          violations: ['exceeds_demo_position_notional:300.0>200.0'],
        },
      ],
      recent_risk_boundary_proposals: [],
      needs_operator_risk_boundary_proposals: [
        {
          boundary_key: 'max_demo_position_notional_usdt',
          violation_kind: 'exceeds_demo_position_notional',
          rejection_count: 2,
          current_value: 200,
          suggested_value: 300,
          changes: {
            max_demo_position_notional_usdt: 300,
          },
          summary:
            'Recent risk rejections repeatedly hit the demo position-notional cap and should be proposed for human review.',
          sample_violations: ['exceeds_demo_position_notional:300.0>200.0'],
          decision_ids: ['risk-1', 'risk-2'],
        },
      ],
    },
  )

  const riskProposal = recommendations.find(item => item.kind === 'risk_boundary_change_proposal_due')
  assert.ok(riskProposal)
  assert.equal(riskProposal?.taskType, 'create_repair_proposal')
  assert.equal(riskProposal?.metadata?.proposal_command, 'propose_risk_boundary_change')
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
  assert.equal(calls.heartbeats[0].mode, 'Investigating')
  assert.equal(calls.heartbeats[0].current_task_id, 'task-1')
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
  backend.getAgentRuns = async () =>
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
    ] satisfies AgentRunSummary[]
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
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  assert.match(
    String(calls.updatedTasks[0].payload.output_data?.summary || ''),
    /Triggered manual market scan/i,
  )
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

test('operator loop re-enters a recovered in-flight strategy evaluation task even when no new recommendation is present', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getOperatorStatus = async () => ({
    agent_id: 'operator',
    status: 'online',
    mode: 'Observing',
    permission_mode: 'ReadOnly',
    is_busy: true,
    current_task_id: 'task-recovered-strategy',
    current_intent: 'Recover strategy evaluation',
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
    if (status === 'running') {
      return [
        {
          id: 'task-recovered-strategy',
          task_type: 'evaluate_strategy_state',
          status: 'running',
          priority: 'high',
          created_by: 'operator_loop',
          assigned_to: 'operator',
          input_data: {
            strategy_ids: ['strat-8'],
            summary: 'Recover strategy state evaluation for strat-8.',
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
  backend.getStrategySummary = async () => ({
    active_paper_strategies: [],
    under_review_strategies: [],
    paused_paper_strategies: [],
    live_candidate_strategies: [],
    recent_strategy_evaluations: [],
    needs_operator_evaluation_strategy_ids: [],
    recent_strategy_promotion_proposals: [],
    needs_operator_promotion_proposal_strategy_ids: [],
  })
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Strategy Momentum Recovery remains under review until drawdown improves.',
      result: {
        strategy: {
          id: 'strat-8',
          name: 'Momentum Recovery',
          state: 'paper_running',
        },
        promotion_gates: {
          passed: false,
          failures: ['drawdown_too_high'],
        },
      },
      action: null,
      risk_level: 'read',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
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
  assert.equal(calls.createdTasks.length, 0)
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'get_strategy_lifecycle')
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].taskId, 'task-recovered-strategy')
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  assert.match(
    String(calls.updatedTasks[0].payload.output_data?.summary || ''),
    /Momentum Recovery remains under review/i,
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
    if (status === 'running') {
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
  assert.equal(calls.createdArtifacts.length, 1)
  assert.equal(calls.createdArtifacts[0].artifact_type, 'repair_decision')
  assert.equal(calls.createdSessions.length, 1)
  assert.equal(calls.createdSessions[0].kind, 'diagnostic_session')
  assert.equal(calls.requestedContexts.length, 1)
  assert.equal(calls.createdMessages[0].message_type, 'context')
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'create_diagnostic_todo')
  const eventTypes = calls.createdEvents.map(item => item.event_type)
  assert.ok(eventTypes.includes('repair_attempted'))
  assert.ok(eventTypes.includes('repair_executed'))
})

test('operator loop evaluates flagged strategies into a strategy session and artifact', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getStrategySummary = async () => ({
    active_paper_strategies: [],
    under_review_strategies: [{ id: 'strat-1', name: 'Momentum A', state: 'paper_running' }],
    paused_paper_strategies: [],
    live_candidate_strategies: [],
    recent_strategy_evaluations: [],
    needs_operator_evaluation_strategy_ids: ['strat-1'],
    recent_strategy_promotion_proposals: [],
    needs_operator_promotion_proposal_strategy_ids: [],
  })
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Strategy Momentum A is in state paper_running. Next allowed targets: paper_passed, retired, deprecated.',
      result: {
        message: 'Strategy Momentum A is in state paper_running. Next allowed targets: paper_passed, retired, deprecated.',
        strategy: {
          id: 'strat-1',
          name: 'Momentum A',
          state: 'paper_running',
          under_review: true,
          paper_trading_paused: false,
        },
        allowed_targets: ['paper_passed', 'retired', 'deprecated'],
        promotion_gates: { passed: false, failures: ['insufficient_paper_trades:0<30'] },
        live_limited_gates: null,
        strategy_count: 1,
      },
      action: null,
      risk_level: 'read',
      needs_confirmation: false,
      session_id: payload.session_id || 'operator-main',
    }
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

  assert.equal(ok, true)
  assert.equal(calls.createdTasks.length, 1)
  assert.equal(calls.createdTasks[0].task_type, 'evaluate_strategy_state')
  assert.equal(calls.createdSessions.length, 1)
  assert.equal(calls.createdSessions[0].kind, 'strategy_session')
  assert.equal(calls.createdMessages.length, 3)
  assert.equal(calls.requestedContexts.length, 1)
  assert.equal(calls.createdMessages[0].message_type, 'context')
  assert.equal((calls.createdMessages[0].content as Record<string, unknown>).profile, 'trading_context')
  assert.equal(calls.createdArtifacts.length, 1)
  assert.equal(calls.createdArtifacts[0].artifact_type, 'strategy_evaluation')
  assert.equal(calls.executedCommands.length, 1)
  assert.equal(calls.executedCommands[0].commandName, 'get_strategy_lifecycle')
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  const eventTypes = calls.createdEvents.map(item => item.event_type)
  assert.ok(eventTypes.includes('strategy_evaluated'))
})

test('operator loop materializes proposal-only strategy promotion decisions through typed proposal commands', async () => {
  const snapshot = makeSnapshot()
  const { backend, calls } = makeFakeBackend(snapshot)
  backend.getStrategySummary = async () => ({
    active_paper_strategies: [{ id: 'strat-2', name: 'Breakout B', state: 'paper_running' }],
    under_review_strategies: [],
    paused_paper_strategies: [],
    live_candidate_strategies: [],
    recent_strategy_evaluations: [
      {
        id: 'artifact-eval-1',
        task_id: 'task-eval-1',
        session_id: 'strategy-session-2',
        artifact_type: 'strategy_evaluation',
        title: 'Strategy Evaluation: Breakout B',
        created_at: new Date().toISOString(),
        strategy_id: 'strat-2',
        strategy_name: 'Breakout B',
        summary: 'Promotion gates passed for paper_running strategy.',
        evaluation: {
          strategy: { id: 'strat-2', name: 'Breakout B', state: 'paper_running' },
          promotion_gates: { passed: true, failures: [] },
        },
        content: {
          strategy_id: 'strat-2',
        },
      },
    ],
    needs_operator_evaluation_strategy_ids: [],
    recent_strategy_promotion_proposals: [],
    needs_operator_promotion_proposal_strategy_ids: ['strat-2'],
  })
  backend.executeOperatorCommand = async (commandName, payload) => {
    calls.executedCommands.push({ commandName, payload })
    return {
      command: commandName,
      permitted: true,
      response: 'Created a promotion proposal for Breakout B -> paper_passed.',
      result: {
        proposed: true,
        artifact_id: 'artifact-proposal-1',
        message: 'Created a promotion proposal for Breakout B -> paper_passed.',
        proposal: {
          strategy_id: 'strat-2',
          strategy_name: 'Breakout B',
          current_state: 'paper_running',
          target_state: 'paper_passed',
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
  assert.equal(calls.executedCommands[0].commandName, 'propose_strategy_promotion')
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
  const eventTypes = calls.createdEvents.map(item => item.event_type)
  assert.ok(eventTypes.includes('repair_attempted'))
  assert.ok(eventTypes.includes('proposal_created'))
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
        violations: ['exceeds_demo_position_notional:300.0>200.0'],
      },
      {
        id: 'risk-2',
        proposal_id: 'proposal-2',
        approved: false,
        violations: ['exceeds_demo_position_notional:300.0>200.0'],
      },
    ],
    recent_risk_boundary_proposals: [],
    needs_operator_risk_boundary_proposals: [
      {
        boundary_key: 'max_demo_position_notional_usdt',
        violation_kind: 'exceeds_demo_position_notional',
        rejection_count: 2,
        current_value: 200,
        suggested_value: 300,
        changes: {
          max_demo_position_notional_usdt: 300,
        },
        summary:
          'Recent risk rejections repeatedly hit the demo position-notional cap and should be proposed for human review.',
        sample_violations: ['exceeds_demo_position_notional:300.0>200.0'],
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
  assert.equal(calls.updatedTasks.length, 1)
  assert.equal(calls.updatedTasks[0].payload.status, 'completed')
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
