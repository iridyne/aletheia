import type {
  AgentRunSummary,
  BackendGatewayClient,
  OperatorMode,
  OperatorRecommendation,
  OperatorSessionSummary,
  PersistedOperatorEvent,
  PersistedOperatorStatus,
  OperatorContextSnapshot,
  OperatorSessionDetail,
  OperatorSessionBranchState,
  OperatorSnapshot,
} from './schemas.js'
import { BackendClient } from './backend_client.js'
import type { OperatorShellConfig } from './config.js'
import { buildHeartbeat } from './heartbeat.js'
import { SessionRuntime } from './session_runtime.js'
import { TaskRuntime } from './task_runtime.js'
import { EventRouter } from './event_router.js'
import { RecommendationEngine } from './recommendation_engine.js'
import { RepairEngine } from './repair_engine.js'

interface OperatorLoopDeps {
  backendClient?: BackendGatewayClient
  sessionRuntime?: SessionRuntime
  taskRuntime?: TaskRuntime
  eventRouter?: EventRouter
  repairEngine?: RepairEngine
  recommendationEngine?: RecommendationEngine
}

export class OperatorLoop {
  private static readonly RECOVERY_INTENT_CHAR_BUDGET = 1200
  private static readonly RECOVERY_BRANCH_FETCH_LIMIT = 12
  private static readonly SUPERVISION_SCAN_INTERVAL_MS = 30_000
  private static readonly SUBSYSTEM_REVIEW_INTERVAL_MS = 5 * 60_000
  private static readonly OPERATOR_PERMISSION_ORDER: Record<string, number> = {
    ReadOnly: 0,
    Research: 1,
    PaperAuto: 2,
    AllowEditing: 3,
    Auto: 4,
  }
  private readonly backendClient: BackendGatewayClient
  private readonly sessionRuntime: SessionRuntime
  private readonly taskRuntime: TaskRuntime
  private readonly eventRouter: EventRouter
  private readonly repairEngine: RepairEngine
  private readonly recommendationEngine: RecommendationEngine
  private intervalHandle: NodeJS.Timeout | null = null
  private mode: OperatorMode = 'Observing'
  private lastError: string | null = null
  private lastSuccessAt: string | null = null
  private recovered = false
  private recoveryIntent: string | null = null
  private recoveryMode: OperatorMode | null = null
  private runParentSessionId: string | null = null
  private readonly compactedDates = new Set<string>()
  private readonly attemptedRepairDecisionIds = new Set<string>()
  private lastSupervisionScanAt: number | null = null
  private cachedSupervision: {
    sessionDetail: OperatorSessionDetail | null
    sessionSummaries: Awaited<ReturnType<BackendGatewayClient['getOperatorSessions']>>
    recentCommands: Awaited<ReturnType<BackendGatewayClient['getOperatorCommands']>>
    operatorTasks: Awaited<ReturnType<BackendGatewayClient['getOperatorTasks']>>
  } = {
    sessionDetail: null,
    sessionSummaries: [],
    recentCommands: [],
    operatorTasks: [],
  }
  private lastSubsystemReviewAt: number | null = null
  private cachedSubsystemReview: {
    dreamingSummary: Awaited<ReturnType<BackendGatewayClient['getDreamingSummary']>>
    reviewSummary: Awaited<ReturnType<BackendGatewayClient['getReviewSummary']>>
    strategySummary: Awaited<ReturnType<BackendGatewayClient['getStrategySummary']>>
    recentAgentRuns: Awaited<ReturnType<BackendGatewayClient['getAgentRuns']>>
    riskSummary: Awaited<ReturnType<BackendGatewayClient['getRiskSummary']>>
    portfolioSummary: Awaited<ReturnType<BackendGatewayClient['getPortfolioSummary']>>
    portfolioHistory: Awaited<ReturnType<BackendGatewayClient['getPortfolioHistory']>>
    operatorExtensions: Awaited<ReturnType<BackendGatewayClient['getOperatorExtensions']>>
  } | null = null

  constructor(private readonly config: OperatorShellConfig, deps: OperatorLoopDeps = {}) {
    this.backendClient = deps.backendClient ?? new BackendClient(config.gatewayBaseUrl)
    this.sessionRuntime = deps.sessionRuntime ?? new SessionRuntime(config.mainSessionId)
    this.taskRuntime = deps.taskRuntime ?? new TaskRuntime(this.backendClient)
    this.eventRouter = deps.eventRouter ?? new EventRouter(this.backendClient)
    this.repairEngine = deps.repairEngine ?? new RepairEngine()
    this.recommendationEngine = deps.recommendationEngine ?? new RecommendationEngine()
  }

  private isReviewTaskType(taskType: string | undefined): boolean {
    return (
      taskType === 'generate_review' ||
      taskType === 'run_portfolio_snapshot' ||
      taskType === 'run_risk_critique' ||
      taskType === 'create_daily_review' ||
      taskType === 'create_review_report'
    )
  }

  private isRepairTaskType(taskType: string | undefined): boolean {
    return taskType === 'create_repair_proposal'
  }

  private isTradingTaskType(taskType: string | undefined): boolean {
    return (
      taskType === 'evaluate_strategy_state' ||
      taskType === 'run_market_scan' ||
      taskType === 'rerun_failed_agent_task' ||
      taskType === 'run_strategy_design' ||
      taskType === 'set_symbol_observe_only' ||
      taskType === 'create_paper_experiment' ||
      taskType === 'pause_paper_strategy'
    )
  }

  private modeForTaskType(taskType: string | undefined): OperatorMode | null {
    if (taskType === 'run_dreaming') {
      return 'Dreaming'
    }
    if (this.isReviewTaskType(taskType)) {
      return 'Reviewing'
    }
    if (this.isRepairTaskType(taskType)) {
      return 'Repairing'
    }
    if (this.isTradingTaskType(taskType)) {
      return 'Trading'
    }
    if (taskType) {
      return 'Investigating'
    }
    return null
  }

  private settleModeAfterTaskRun(
    snapshot: OperatorSnapshot,
    task:
      | {
          taskType: string
          restoredFromRecovery: boolean
        }
      | null,
  ): void {
    if (!task || task.restoredFromRecovery) {
      this.mode = snapshot.status.paused ? 'Paused' : 'Observing'
      this.sessionRuntime.setCurrentIntent('monitoring system state')
      return
    }

    this.mode = this.modeForTaskType(task.taskType) || (snapshot.status.paused ? 'Paused' : 'Observing')
  }

  private deriveMode(snapshot: OperatorSnapshot, recommendations: OperatorRecommendation[]): OperatorMode {
    if (snapshot.status.paused) {
      return 'Paused'
    }
    for (const recommendation of recommendations) {
      const mode = this.modeForTaskType(recommendation.taskType)
      if (mode) {
        return mode
      }
    }
    if (recommendations.some(item => item.taskType)) {
      return 'Investigating'
    }
    if (recommendations.some(item => item.severity === 'high')) {
      return 'Degraded'
    }
    return 'Observing'
  }

  private branchKindLabel(kind: OperatorSessionBranchState['kind']): string {
    switch (kind) {
      case 'dreaming_session':
        return 'dreaming'
      case 'review_session':
        return 'review'
      case 'strategy_session':
        return 'strategy evaluation'
      case 'diagnostic_session':
        return 'diagnostic'
      default:
        return 'operator'
    }
  }

  private branchRecoveryLines(branches: OperatorSessionBranchState[]): string[] {
    const lines: string[] = []
    const seen = new Set<string>()
    const remember = (value: string | null | undefined) => {
      const normalized = value?.trim()
      if (!normalized) {
        return
      }
      const key = normalized.toLowerCase()
      if (seen.has(key)) {
        return
      }
      seen.add(key)
      lines.push(normalized)
    }

    for (const branch of branches) {
      const kind = this.branchKindLabel(branch.kind)
      const title = branch.title?.trim()
      const summary = branch.summary?.trim()
      if (title && summary) {
        remember(`Recovered ${kind} branch ${title}: ${summary}.`)
      } else if (summary) {
        remember(`Recovered ${kind} branch context: ${summary}.`)
      } else if (title) {
        remember(`Recovered ${kind} branch ${title}.`)
      }
    }

    return lines
  }

  private descendantSessionSummaries(sessionSummaries: OperatorSessionSummary[]): OperatorSessionSummary[] {
    const children = new Map<string, OperatorSessionSummary[]>()
    for (const session of sessionSummaries) {
      const parent = session.parent_session_id || ''
      if (!parent) {
        continue
      }
      const items = children.get(parent) || []
      items.push(session)
      children.set(parent, items)
    }

    const descendants: OperatorSessionSummary[] = []
    const seen = new Set<string>()
    const stack = [...(children.get(this.config.mainSessionId) || [])]
    while (stack.length > 0) {
      const session = stack.pop()
      if (!session || seen.has(session.id)) {
        continue
      }
      seen.add(session.id)
      descendants.push(session)
      stack.push(...(children.get(session.id) || []))
    }

    return descendants.sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))
  }

  private beginRunSessionParentScope(): void {
    this.runParentSessionId = this.sessionRuntime.getActiveBranch()?.id || this.config.mainSessionId
  }

  private endRunSessionParentScope(): void {
    this.runParentSessionId = null
  }

  private currentRunParentSessionId(): string {
    return this.runParentSessionId || this.config.mainSessionId
  }

  private compactedRecoveryLines(artifacts: OperatorSessionDetail['artifacts']): string[] {
    const summaries = artifacts
      .filter(
        artifact =>
          artifact.artifact_type === 'operator_daily_summary' &&
          artifact.content &&
          typeof artifact.content === 'object' &&
          !Array.isArray(artifact.content),
      )
      .sort((left, right) => {
        const leftContent = left.content as Record<string, unknown>
        const rightContent = right.content as Record<string, unknown>
        const leftDate =
          typeof leftContent.date === 'string' ? Date.parse(leftContent.date) : Date.parse(left.created_at)
        const rightDate =
          typeof rightContent.date === 'string' ? Date.parse(rightContent.date) : Date.parse(right.created_at)
        return (Number.isNaN(rightDate) ? 0 : rightDate) - (Number.isNaN(leftDate) ? 0 : leftDate)
      })
    if (summaries.length === 0) {
      return []
    }

    const lines: string[] = []
    const seen = new Set<string>()
    const remember = (value: string | null | undefined) => {
      const normalized = value?.trim()
      if (!normalized) {
        return
      }
      const key = normalized.toLowerCase()
      if (seen.has(key)) {
        return
      }
      seen.add(key)
      lines.push(normalized)
    }

    const sectionMappings: Array<[string, string]> = [
      ['major_user_instructions', 'Compacted user instruction'],
      ['important_repair_decisions', 'Compacted repair decision'],
      ['proposal_outcomes', 'Compacted proposal outcome'],
      ['strategy_state_changes', 'Compacted strategy-state change'],
      ['unresolved_issues', 'Compacted unresolved issue'],
      ['follow_up_recommendations', 'Compacted follow-up'],
    ]

    for (const artifact of summaries) {
      const content = artifact.content as Record<string, unknown>
      if (typeof content.summary_excerpt === 'string') {
        remember(content.summary_excerpt)
      }

      const sections =
        content.sections && typeof content.sections === 'object' && !Array.isArray(content.sections)
          ? (content.sections as Record<string, unknown>)
          : {}
      for (const [key, label] of sectionMappings) {
        const items = Array.isArray(sections[key])
          ? sections[key]
              .map(item => (typeof item === 'string' ? item.trim() : ''))
              .filter(item => item.length > 0)
          : []
        if (items[0]) {
          remember(`${label}: ${items[0]}`)
        }
      }
    }

    return lines
  }

  private withinRecoveryBudget(
    current: string[],
    candidate: string,
    budgetChars = OperatorLoop.RECOVERY_INTENT_CHAR_BUDGET,
  ): boolean {
    const joined = current.length === 0 ? candidate : `${current.join(' ')} ${candidate}`
    return joined.length <= budgetChars
  }

  private appendRecoveryLinesWithBudget(
    target: string[],
    candidates: string[],
    budgetChars = OperatorLoop.RECOVERY_INTENT_CHAR_BUDGET,
  ): void {
    for (const candidate of candidates) {
      if (!candidate.trim()) {
        continue
      }
      if (!this.withinRecoveryBudget(target, candidate, budgetChars)) {
        break
      }
      target.push(candidate)
    }
  }

  private shouldRunCadence(lastAt: number | null, intervalMs: number): boolean {
    return lastAt === null || Date.now() - lastAt >= intervalMs
  }

  private hasOperatorPermission(required: string): boolean {
    const current =
      OperatorLoop.OPERATOR_PERMISSION_ORDER[this.config.permissionMode] ?? -1
    const needed = OperatorLoop.OPERATOR_PERMISSION_ORDER[required] ?? Number.MAX_SAFE_INTEGER
    return current >= needed
  }

  private taskPermissionRequired(taskType: string): string | null {
    if (
      taskType === 'create_daily_review' ||
      taskType === 'create_review_report' ||
      taskType === 'create_diagnostic_report' ||
      taskType === 'run_portfolio_snapshot' ||
      taskType === 'run_risk_critique'
    ) {
      return 'Research'
    }
    if (taskType === 'create_repair_proposal') {
      return 'AllowEditing'
    }
    if (
      taskType === 'run_market_scan' ||
      taskType === 'rerun_failed_agent_task' ||
      taskType === 'run_strategy_design' ||
      taskType === 'set_symbol_observe_only' ||
      taskType === 'create_paper_experiment' ||
      taskType === 'pause_paper_strategy'
    ) {
      return 'PaperAuto'
    }
    return null
  }

  private runtimeId(): string {
    return this.config.runtimeId || 'runtime-local-current'
  }

  private runtimeMode(): 'current' | 'shadow' {
    return this.config.runtimeMode === 'shadow' ? 'shadow' : 'current'
  }

  private runtimeState(): string {
    return this.config.runtimeState || (this.runtimeMode() === 'shadow' ? 'shadowing' : 'current')
  }

  private isShadowRuntime(): boolean {
    return this.runtimeMode() === 'shadow'
  }

  private async runShadowOnce(): Promise<boolean> {
    const [status, health] = await Promise.all([
      this.backendClient.getGatewayStatus(),
      this.backendClient.getHealth(),
    ])
    this.mode = status.paused ? 'Paused' : health.status === 'healthy' ? 'Observing' : 'Degraded'
    const issueSummary = health.issues.length > 0 ? health.issues.join(', ') : health.status
    this.sessionRuntime.setCurrentIntent(`shadow validation: ${issueSummary}`)
    const heartbeat = buildHeartbeat({
      mode: this.mode,
      permissionMode: this.config.permissionMode,
      session: this.sessionRuntime.getMainSession(),
      tasks: { currentTaskId: null, queueSize: 0 },
      activeBranch: this.sessionRuntime.getActiveBranch(),
      modelProvider: this.config.modelProvider,
      lastError: this.lastError,
      lastSuccessAt: this.lastSuccessAt,
      runtimeId: this.runtimeId(),
      runtimeMode: this.runtimeMode(),
      runtimeState: this.runtimeState(),
    })
    await this.backendClient.postHeartbeat(heartbeat)
    this.lastSuccessAt = heartbeat.last_seen_at
    this.lastError = null
    return true
  }

  private buildDailyReviewContent(params: {
    snapshot: OperatorSnapshot
    recommendations: OperatorRecommendation[]
    reviewSummary: Awaited<ReturnType<BackendGatewayClient['getReviewSummary']>>
    strategySummary: Awaited<ReturnType<BackendGatewayClient['getStrategySummary']>>
    riskSummary: Awaited<ReturnType<BackendGatewayClient['getRiskSummary']>>
    recentAgentRuns: Awaited<ReturnType<BackendGatewayClient['getAgentRuns']>>
  }): string {
    const targetDate = new Date().toISOString().slice(0, 10)
    const runAgeMinutes = (run: Pick<AgentRunSummary, 'started_at' | 'ended_at'>): number | null => {
      const timestamp = run.ended_at || run.started_at
      if (!timestamp) {
        return null
      }
      const parsed = Date.parse(timestamp)
      if (Number.isNaN(parsed)) {
        return null
      }
      return (Date.now() - parsed) / 60_000
    }
    const topAttention = params.recommendations
      .filter(item => item.kind !== 'daily_review_refresh_due')
      .slice(0, 3)
      .map(item => `- [${item.severity}] ${item.summary}`)
    const latestMarketObserverRun = params.recentAgentRuns
      .filter(run => run.agent_name === 'market_observer')
      .slice()
      .sort((left, right) => {
        const leftTime = Date.parse(left.ended_at || left.started_at)
        const rightTime = Date.parse(right.ended_at || right.started_at)
        return (Number.isNaN(rightTime) ? 0 : rightTime) - (Number.isNaN(leftTime) ? 0 : leftTime)
      })[0]
    const marketObserverLine = latestMarketObserverRun
      ? `- Latest market observer run: ${latestMarketObserverRun.status} at ${latestMarketObserverRun.ended_at || latestMarketObserverRun.started_at}`
      : '- Latest market observer run: none recorded'
    const healthIssues =
      params.snapshot.health.issues.length > 0
        ? params.snapshot.health.issues.map(issue => `- ${issue}`)
        : ['- none']

    return [
      `# Operator Daily Review ${targetDate}`,
      '',
      '## System',
      `- Gateway running: ${params.snapshot.status.gateway_running ? 'yes' : 'no'}`,
      `- Gateway paused: ${params.snapshot.status.paused ? 'yes' : 'no'}`,
      `- Health status: ${params.snapshot.health.status}`,
      ...healthIssues,
      '',
      '## Market And Agent Activity',
      marketObserverLine,
      `- Recent agent runs (12h): ${params.recentAgentRuns.filter(run => {
        const ageMinutes = runAgeMinutes(run)
        return ageMinutes !== null && ageMinutes <= 12 * 60
      }).length}`,
      '',
      '## Review Coverage',
      `- Closed strategies missing post-trade review: ${params.reviewSummary.missing_review_strategy_ids.length}`,
      `- Recent closed positions (24h): ${params.reviewSummary.recent_closed_positions.length}`,
      '',
      '## Strategy Governance',
      `- Strategies needing operator evaluation: ${params.strategySummary.needs_operator_evaluation_strategy_ids.length}`,
      `- Strategies ready for promotion proposal: ${params.strategySummary.needs_operator_promotion_proposal_strategy_ids.length}`,
      '',
      '## Risk And Follow-Up',
      `- Risk boundary proposals due: ${params.riskSummary.needs_operator_risk_boundary_proposals.length}`,
      ...(topAttention.length > 0
        ? ['- Top operator attention:', ...topAttention]
        : ['- Top operator attention: none beyond routine monitoring']),
    ].join('\n')
  }

  private buildAgentPerformanceReportContent(params: {
    recentAgentRuns: Awaited<ReturnType<BackendGatewayClient['getAgentRuns']>>
  }): string {
    const cutoffMs = Date.now() - 12 * 60 * 60 * 1000
    const recentRuns = params.recentAgentRuns.filter(run => {
      const parsed = Date.parse(run.ended_at || run.started_at)
      return !Number.isNaN(parsed) && parsed >= cutoffMs
    })
    const byAgent = new Map<
      string,
      { total: number; failed: number; manual: number; latestStatus: string; latestAt: string }
    >()
    for (const run of recentRuns) {
      const agentName = String(run.agent_name || 'unknown')
      const current = byAgent.get(agentName) || {
        total: 0,
        failed: 0,
        manual: 0,
        latestStatus: String(run.status || 'unknown'),
        latestAt: String(run.ended_at || run.started_at || ''),
      }
      current.total += 1
      if (['failed', 'error', 'completed_with_errors'].includes(String(run.status || ''))) {
        current.failed += 1
      }
      if (String(run.trigger_type || '') === 'manual') {
        current.manual += 1
      }
      const currentLatest = Date.parse(current.latestAt)
      const nextLatest = Date.parse(run.ended_at || run.started_at || '')
      if ((Number.isNaN(currentLatest) ? 0 : currentLatest) <= (Number.isNaN(nextLatest) ? 0 : nextLatest)) {
        current.latestStatus = String(run.status || 'unknown')
        current.latestAt = String(run.ended_at || run.started_at || '')
      }
      byAgent.set(agentName, current)
    }
    const lines = [...byAgent.entries()]
      .sort((left, right) => right[1].total - left[1].total)
      .map(([agentName, stats]) =>
        `- ${agentName}: total=${stats.total}, failed=${stats.failed}, manual=${stats.manual}, latest=${stats.latestStatus} @ ${stats.latestAt}`,
      )

    return [
      `# Agent Performance Report ${new Date().toISOString().slice(0, 10)}`,
      '',
      '## Agent Activity',
      `- Recent agent runs (12h): ${recentRuns.length}`,
      `- Distinct agents active: ${byAgent.size}`,
      '',
      '## Per-Agent Summary',
      ...(lines.length > 0 ? lines : ['- No recent agent activity recorded.']),
    ].join('\n')
  }

  private buildRiskReviewReportContent(params: {
    riskSummary: Awaited<ReturnType<BackendGatewayClient['getRiskSummary']>>
  }): string {
    const rejected = params.riskSummary.recent_rejected_decisions || []
    const candidates = params.riskSummary.needs_operator_risk_boundary_proposals || []
    const candidateLines = candidates.slice(0, 5).map(candidate =>
      `- ${candidate.boundary_key}: ${candidate.summary}`,
    )

    return [
      `# Risk Review Report ${new Date().toISOString().slice(0, 10)}`,
      '',
      '## Rejections',
      `- Recent rejected decisions: ${rejected.length}`,
      '',
      '## Boundary Proposal Candidates',
      ...(candidateLines.length > 0 ? candidateLines : ['- No boundary proposal candidates right now.']),
    ].join('\n')
  }

  private buildTradeReviewReportContent(params: {
    reviewSummary: Awaited<ReturnType<BackendGatewayClient['getReviewSummary']>>
  }): string {
    const recentClosed = params.reviewSummary.recent_closed_positions || []
    const missing = params.reviewSummary.missing_review_strategy_ids || []

    return [
      `# Trade Review Report ${new Date().toISOString().slice(0, 10)}`,
      '',
      '## Closed Positions',
      `- Recent closed positions (24h): ${recentClosed.length}`,
      '',
      '## Review Coverage',
      `- Missing post-trade review strategy ids: ${missing.length}`,
      ...(missing.length > 0 ? missing.slice(0, 10).map(item => `- ${item}`) : ['- none']),
    ].join('\n')
  }

  private parseErrorList(raw: string | null | undefined): string[] {
    if (!raw) {
      return []
    }
    try {
      const parsed = JSON.parse(raw)
      return Array.isArray(parsed) ? parsed.map(item => String(item)) : [String(parsed)]
    } catch {
      return [String(raw)]
    }
  }

  private buildWorkerHealthDiagnosticContent(params: {
    snapshot: OperatorSnapshot
    recentAgentRuns: Awaited<ReturnType<BackendGatewayClient['getAgentRuns']>>
  }): string {
    const failedRuns = params.recentAgentRuns.filter(run =>
      ['failed', 'error', 'completed_with_errors'].includes(String(run.status || '')),
    )
    const lines = failedRuns.slice(0, 5).map(run => {
      const sampleError = this.parseErrorList(run.errors)[0] || 'no error sample recorded'
      return `- ${run.agent_name}: status=${run.status}, provider=${run.model_provider || 'unknown'}, error=${sampleError}`
    })

    return [
      `# Worker Health Diagnostic Report ${new Date().toISOString().slice(0, 10)}`,
      '',
      '## Worker Health Signals',
      ...(params.snapshot.health.issues.length > 0
        ? params.snapshot.health.issues.map(issue => `- ${issue}`)
        : ['- no worker health issues recorded']),
      '',
      '## Recent Failed Worker Runs',
      `- Failed/error runs in memory window: ${failedRuns.length}`,
      ...(lines.length > 0 ? lines : ['- no failed worker runs captured in the current memory window']),
    ].join('\n')
  }

  private buildModelRuntimeHealthDiagnosticContent(params: {
    snapshot: OperatorSnapshot
    recentAgentRuns: Awaited<ReturnType<BackendGatewayClient['getAgentRuns']>>
  }): string {
    const relevantIssues = params.snapshot.health.issues.filter(
      issue =>
        issue.startsWith('llm_unavailable:') ||
        issue.startsWith('code_updated_pending_restart:'),
    )
    const providers = [...new Set(
      params.recentAgentRuns
        .map(run => (typeof run.model_provider === 'string' ? run.model_provider.trim() : ''))
        .filter(Boolean),
    )]

    return [
      `# Model Runtime Health Diagnostic Report ${new Date().toISOString().slice(0, 10)}`,
      '',
      '## Runtime Signals',
      ...(relevantIssues.length > 0
        ? relevantIssues.map(issue => `- ${issue}`)
        : ['- no model/runtime issues recorded']),
      '',
      '## Recent Model Providers',
      ...(providers.length > 0 ? providers.map(provider => `- ${provider}`) : ['- no recent model provider usage recorded']),
    ].join('\n')
  }

  private buildDataSourceHealthDiagnosticContent(params: {
    snapshot: OperatorSnapshot
    recentAgentRuns: Awaited<ReturnType<BackendGatewayClient['getAgentRuns']>>
  }): string {
    const relevantIssues = params.snapshot.health.issues.filter(issue =>
      issue.startsWith('market_data_stale:'),
    )
    const marketObserverRuns = params.recentAgentRuns
      .filter(run => run.agent_name === 'market_observer')
      .sort(
        (left, right) =>
          Date.parse(right.ended_at || right.started_at) - Date.parse(left.ended_at || left.started_at),
      )
    const latestMarketObserverRun = marketObserverRuns[0] || null

    return [
      `# Data Source Health Diagnostic Report ${new Date().toISOString().slice(0, 10)}`,
      '',
      '## Data Source Signals',
      ...(relevantIssues.length > 0
        ? relevantIssues.map(issue => `- ${issue}`)
        : ['- no data-source health issues recorded']),
      '',
      '## Market Observer Coverage',
      latestMarketObserverRun
        ? `- Latest market_observer run: ${latestMarketObserverRun.status} @ ${latestMarketObserverRun.ended_at || latestMarketObserverRun.started_at}`
        : '- Latest market_observer run: none recorded',
    ].join('\n')
  }

  private buildSystemHealthDiagnosticContent(params: {
    snapshot: OperatorSnapshot
    recommendations: OperatorRecommendation[]
    recentAgentRuns: Awaited<ReturnType<BackendGatewayClient['getAgentRuns']>>
  }): string {
    const failedRuns = params.recentAgentRuns.filter(run =>
      ['failed', 'error', 'completed_with_errors'].includes(String(run.status || '')),
    )
    const attention = params.recommendations
      .filter(item => item.kind !== 'system_health_report_refresh_due')
      .slice(0, 5)
      .map(item => `- [${item.severity}] ${item.summary}`)
    return [
      `# System Health Diagnostic Report ${new Date().toISOString().slice(0, 10)}`,
      '',
      '## Gateway',
      `- Running: ${params.snapshot.status.gateway_running ? 'yes' : 'no'}`,
      `- Paused: ${params.snapshot.status.paused ? 'yes' : 'no'}`,
      `- Health status: ${params.snapshot.health.status}`,
      ...(params.snapshot.health.issues.length > 0
        ? params.snapshot.health.issues.map(issue => `- ${issue}`)
        : ['- no gateway health issues recorded']),
      '',
      '## Recent Agent Failures',
      `- Failed/error runs in memory window: ${failedRuns.length}`,
      '',
      '## Operator Attention',
      ...(attention.length > 0 ? attention : ['- no additional operator attention items']),
    ].join('\n')
  }

  private reviewReportTitle(reportType: string): string {
    switch (reportType) {
      case 'trade_review':
        return 'Trade Review Report'
      case 'risk_review':
        return 'Risk Review Report'
      case 'agent_performance':
        return 'Agent Performance Report'
      default:
        return 'Review Report'
    }
  }

  private diagnosticReportTitle(reportType: string): string {
    switch (reportType) {
      case 'worker_health':
        return 'Worker Health Diagnostic Report'
      case 'model_runtime_health':
        return 'Model Runtime Health Diagnostic Report'
      case 'data_source_health':
        return 'Data Source Health Diagnostic Report'
      case 'system_health':
        return 'System Health Diagnostic Report'
      default:
        return 'Diagnostic Report'
    }
  }

  private recommendationsAllowedForTaskSync(
    recommendations: OperatorRecommendation[],
  ): OperatorRecommendation[] {
    return recommendations.map(recommendation => {
      if (!recommendation.taskType) {
        return recommendation
      }
      const required = this.taskPermissionRequired(recommendation.taskType)
      if (!required || this.hasOperatorPermission(required)) {
        return recommendation
      }
      return {
        ...recommendation,
        taskType: undefined,
      }
    })
  }

  private async loadSupervisionData(): Promise<{
    sessionDetail: OperatorSessionDetail | null
    sessionSummaries: Awaited<ReturnType<BackendGatewayClient['getOperatorSessions']>>
    recentCommands: Awaited<ReturnType<BackendGatewayClient['getOperatorCommands']>>
    operatorTasks: Awaited<ReturnType<BackendGatewayClient['getOperatorTasks']>>
  }> {
    if (
      !this.shouldRunCadence(
        this.lastSupervisionScanAt,
        OperatorLoop.SUPERVISION_SCAN_INTERVAL_MS,
      )
    ) {
      return this.cachedSupervision
    }

    const [sessionDetail, sessionSummaries, recentCommands, operatorTasks] = await Promise.all([
      this.backendClient.getOperatorSession(this.config.mainSessionId).catch(() => null),
      this.backendClient.getOperatorSessions(100).catch(() => []),
      this.backendClient.getOperatorCommands(100),
      this.backendClient.getOperatorTasks().catch(() => []),
    ])

    this.cachedSupervision = {
      sessionDetail,
      sessionSummaries,
      recentCommands,
      operatorTasks,
    }
    this.lastSupervisionScanAt = Date.now()
    return this.cachedSupervision
  }

  private syncPersistedActiveBranch(
    operatorStatus: PersistedOperatorStatus,
    sessionSummaries: OperatorSessionSummary[],
  ): void {
    const persistedBranchId = operatorStatus.active_branch_id?.trim() || null
    if (!persistedBranchId) {
      this.sessionRuntime.clearActiveBranch()
      return
    }

    const branchSummary =
      sessionSummaries.find(session => session.id === persistedBranchId) || null
    this.sessionRuntime.selectBranch({
      id: persistedBranchId,
      kind:
        (branchSummary?.kind as OperatorSessionBranchState['kind'] | null) ||
        operatorStatus.active_branch_kind ||
        'command_session',
      title:
        branchSummary?.title ||
        operatorStatus.active_branch_title ||
        persistedBranchId,
      parent_session_id:
        branchSummary?.parent_session_id ||
        this.config.mainSessionId,
      summary:
        branchSummary?.response ||
        branchSummary?.intent ||
        branchSummary?.title ||
        branchSummary?.user_input ||
        operatorStatus.current_intent ||
        null,
      created_at: branchSummary?.created_at || null,
    })
  }

  private async loadSubsystemReviewData(): Promise<{
    dreamingSummary: Awaited<ReturnType<BackendGatewayClient['getDreamingSummary']>>
    reviewSummary: Awaited<ReturnType<BackendGatewayClient['getReviewSummary']>>
    strategySummary: Awaited<ReturnType<BackendGatewayClient['getStrategySummary']>>
    recentAgentRuns: Awaited<ReturnType<BackendGatewayClient['getAgentRuns']>>
    riskSummary: Awaited<ReturnType<BackendGatewayClient['getRiskSummary']>>
    portfolioSummary: Awaited<ReturnType<BackendGatewayClient['getPortfolioSummary']>>
    portfolioHistory: Awaited<ReturnType<BackendGatewayClient['getPortfolioHistory']>>
    operatorExtensions: Awaited<ReturnType<BackendGatewayClient['getOperatorExtensions']>>
  }> {
    if (
      this.cachedSubsystemReview &&
      !this.shouldRunCadence(
        this.lastSubsystemReviewAt,
        OperatorLoop.SUBSYSTEM_REVIEW_INTERVAL_MS,
      )
    ) {
      return this.cachedSubsystemReview
    }

    const [
      dreamingSummary,
      reviewSummary,
      strategySummary,
      recentAgentRuns,
      riskSummary,
      portfolioSummary,
      portfolioHistory,
      operatorExtensions,
    ] =
      await Promise.all([
        this.backendClient.getDreamingSummary(),
        this.backendClient.getReviewSummary(),
        this.backendClient.getStrategySummary(),
        this.backendClient.getAgentRuns(),
        this.backendClient.getRiskSummary(),
        this.backendClient.getPortfolioSummary(),
        this.backendClient.getPortfolioHistory(),
        this.backendClient.getOperatorExtensions('sandbox_passed'),
      ])

    this.cachedSubsystemReview = {
      dreamingSummary,
      reviewSummary,
      strategySummary,
      recentAgentRuns,
      riskSummary,
      portfolioSummary,
      portfolioHistory,
      operatorExtensions,
    }
    this.lastSubsystemReviewAt = Date.now()
    return this.cachedSubsystemReview
  }

  private async attachSessionContext(sessionId: string): Promise<OperatorContextSnapshot> {
    const snapshot = await this.backendClient.getOperatorContext({ session_id: sessionId })
    await this.backendClient.createMessage({
      session_id: sessionId,
      role: 'assistant',
      message_type: 'context',
      related_event_id: null,
      content: {
        text: snapshot.summary,
        profile: snapshot.profile,
        session_kind: snapshot.session_kind,
        sections: snapshot.sections,
        context: snapshot.context,
      },
    })
    return snapshot
  }

  private async maybeDelegateSpecialistTask(): Promise<boolean> {
    const activeTask = this.taskRuntime.getActiveTask()
    if (!activeTask) {
      return false
    }

    const delegation =
      activeTask.taskType === 'run_dreaming'
          ? {
              sessionKind: 'dreaming_session',
              sessionPrefix: 'dreaming',
              startEvent: 'dreaming_started',
              completedEvent: 'dreaming_completed',
              fallbackSummary: 'Dreaming run completed successfully.',
              warningSummary: 'Dreaming run completed with non-fatal warnings.',
              userInput: 'Run dreaming consolidation for operator memory.',
              title: 'Dreaming Delegation',
            } as const
          : activeTask.taskType === 'generate_review'
          ? {
              sessionKind: 'review_session',
              sessionPrefix: 'review',
              startEvent: 'review_started',
              completedEvent: 'review_completed',
              fallbackSummary: 'Post-trade review generation completed successfully.',
              warningSummary: 'Post-trade review generation completed with non-fatal warnings.',
              userInput: 'Generate post-trade review coverage for recently closed strategies.',
              title: 'Post-Trade Review Delegation',
            } as const
          : activeTask.taskType === 'run_portfolio_snapshot'
          ? {
              sessionKind: 'review_session',
              sessionPrefix: 'portfolio-snapshot',
              startEvent: 'portfolio_snapshot_started',
              completedEvent: 'portfolio_snapshot_completed',
              fallbackSummary: 'Portfolio snapshot refresh completed successfully.',
              warningSummary: 'Portfolio snapshot refresh completed with non-fatal warnings.',
              userInput: 'Refresh portfolio snapshot because open positions need a fresh operator view.',
              title: 'Portfolio Snapshot Delegation',
            } as const
          : activeTask.taskType === 'run_strategy_design'
          ? {
              sessionKind: 'strategy_session',
              sessionPrefix: 'strategy-design',
              startEvent: 'strategy_design_started',
              completedEvent: 'strategy_design_completed',
              fallbackSummary: 'Strategy design delegation completed successfully.',
              warningSummary: 'Strategy design delegation completed with non-fatal warnings.',
              userInput:
                'Delegate strategy design to create a fresh paper strategy from recent research inputs.',
              title: 'Strategy Design Delegation',
            } as const
          : activeTask.taskType === 'run_risk_critique'
          ? {
              sessionKind: 'review_session',
              sessionPrefix: 'risk-critique',
              startEvent: 'risk_critique_started',
              completedEvent: 'risk_critique_completed',
              fallbackSummary: 'Risk critique delegation completed successfully.',
              warningSummary: 'Risk critique delegation completed with non-fatal warnings.',
              userInput: 'Delegate risk critique for recent risk rejections and guardrail lessons.',
              title: 'Risk Critique Delegation',
            } as const
          : null

    if (!delegation) {
      return false
    }

    const sessionId = `${delegation.sessionPrefix}-${activeTask.taskId}`
    const parentSessionId = this.currentRunParentSessionId()
    await this.backendClient.createSession({
      id: sessionId,
      user_input: delegation.userInput,
      kind: delegation.sessionKind,
      parent_session_id: parentSessionId,
      title: delegation.title,
    })
    this.sessionRuntime.rememberBranch({
      id: sessionId,
      kind: delegation.sessionKind,
      title: delegation.title,
      parent_session_id: parentSessionId,
      summary: null,
    })
    await this.attachSessionContext(sessionId)
    await this.backendClient.createMessage({
      session_id: sessionId,
      role: 'assistant',
      message_type: 'delegation',
      related_event_id: null,
      content: {
        text: `Delegating ${activeTask.taskType} to ${activeTask.assignedTo}.`,
        task_id: activeTask.taskId,
        agent_name: activeTask.assignedTo,
      },
    })

    await this.backendClient.createEvent({
      event_type: delegation.startEvent,
      severity: 'info',
      source: 'operator_loop',
      session_id: this.config.mainSessionId,
      task_id: activeTask.taskId,
      related_entity_type: 'agent',
      related_entity_id: activeTask.assignedTo,
      payload: {
        agent_name: activeTask.assignedTo,
        task_type: activeTask.taskType,
      },
    })

    try {
      const result = await this.backendClient.startAgentRun(activeTask.assignedTo)
      const completed =
        result.status === 'completed' || result.status === 'completed_with_errors'
      const outputData = {
        agent_name: activeTask.assignedTo,
        run_id: result.run_id,
        run_status: result.status,
        outputs: result.outputs,
        errors: result.errors,
      }

      if (!completed) {
        const errorMessage =
          result.errors[0] ||
          `Delegated dreaming run ended with unexpected status ${result.status}.`
        await this.taskRuntime.failActiveTask(errorMessage, outputData)
        throw new Error(errorMessage)
      }

      await this.taskRuntime.completeActiveTask({
        ...outputData,
        summary:
          result.errors.length > 0
            ? delegation.warningSummary
            : delegation.fallbackSummary,
      })
      this.sessionRuntime.rememberBranch({
        id: sessionId,
        kind: delegation.sessionKind,
        title: delegation.title,
        parent_session_id: parentSessionId,
        summary:
          result.errors.length > 0
            ? delegation.warningSummary
            : delegation.fallbackSummary,
      })
      await this.backendClient.createMessage({
        session_id: sessionId,
        role: 'assistant',
        message_type: 'result',
        related_event_id: null,
        content: {
          text: result.errors.length > 0 ? delegation.warningSummary : delegation.fallbackSummary,
          run_id: result.run_id,
          outputs: result.outputs,
          errors: result.errors,
        },
      })
      await this.backendClient.createEvent({
        event_type: delegation.completedEvent,
        severity: result.errors.length > 0 ? 'medium' : 'info',
        source: 'operator_loop',
        session_id: this.config.mainSessionId,
        task_id: activeTask.taskId,
        related_entity_type: 'agent_run',
        related_entity_id: result.run_id,
        payload: outputData,
      })
      return true
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      if (this.taskRuntime.getActiveTask()?.taskId === activeTask.taskId) {
        await this.taskRuntime.failActiveTask(message, {
          agent_name: activeTask.assignedTo,
          task_type: activeTask.taskType,
        })
      }
      throw error
    }
  }

  private async maybeEvaluateStrategyTask(): Promise<boolean> {
    const activeTask = this.taskRuntime.getActiveTask()
    if (!activeTask || activeTask.taskType !== 'evaluate_strategy_state') {
      return false
    }

    const strategyIds = Array.isArray(activeTask.inputData.strategy_ids)
      ? activeTask.inputData.strategy_ids.filter((item): item is string => typeof item === 'string')
      : []
    const strategyId =
      strategyIds[0] ||
      (typeof activeTask.inputData.strategy_id === 'string' ? activeTask.inputData.strategy_id : null)
    if (!strategyId) {
      await this.taskRuntime.failActiveTask('No strategy_id was attached to the strategy evaluation task.')
      return true
    }

    const sessionId = `strategy-${activeTask.taskId}`
    const parentSessionId = this.currentRunParentSessionId()
    await this.backendClient.createSession({
      id: sessionId,
      user_input: `Evaluate strategy state for ${strategyId}.`,
      kind: 'strategy_session',
      parent_session_id: parentSessionId,
      title: 'Strategy Evaluation',
    })
    this.sessionRuntime.rememberBranch({
      id: sessionId,
      kind: 'strategy_session',
      title: 'Strategy Evaluation',
      parent_session_id: parentSessionId,
      summary: null,
    })
    await this.attachSessionContext(sessionId)
    await this.backendClient.createMessage({
      session_id: sessionId,
      role: 'assistant',
      message_type: 'evaluation',
      related_event_id: null,
      content: {
        text: `Evaluating strategy state for ${strategyId}.`,
        strategy_id: strategyId,
      },
    })

    try {
      const execution = await this.backendClient.executeOperatorCommand('get_strategy_lifecycle', {
        session_id: sessionId,
        user_input: `Evaluate strategy state for ${strategyId}.`,
        parameters: { strategy_id: strategyId },
      })
      const result = execution.result || {}
      const strategy =
        result && typeof result === 'object' && !Array.isArray(result) && typeof result.strategy === 'object'
          ? (result.strategy as Record<string, unknown>)
          : {}
      const strategyName = typeof strategy.name === 'string' ? strategy.name : strategyId
      const artifact = await this.backendClient.createArtifact({
        task_id: activeTask.taskId,
        session_id: sessionId,
        artifact_type: 'strategy_evaluation',
        title: `Strategy Evaluation: ${strategyName}`,
        content: {
          strategy_id: strategyId,
          strategy_name: strategyName,
          summary: execution.response,
          evaluation: result,
        },
      })
      await this.backendClient.createMessage({
        session_id: sessionId,
        role: 'assistant',
        message_type: 'result',
        related_event_id: null,
        content: {
          text: execution.response,
          strategy_id: strategyId,
          artifact_id: artifact.id,
          result,
        },
      })
      await this.backendClient.createEvent({
        event_type: 'strategy_evaluated',
        severity: 'info',
        source: 'operator_loop',
        session_id: sessionId,
        task_id: activeTask.taskId,
        related_entity_type: 'strategy',
        related_entity_id: strategyId,
        payload: {
          artifact_id: artifact.id,
          response: execution.response,
        },
      })
      await this.taskRuntime.completeActiveTask({
        strategy_id: strategyId,
        artifact_id: artifact.id,
        summary: execution.response,
      })
      this.sessionRuntime.rememberBranch({
        id: sessionId,
        kind: 'strategy_session',
        title: 'Strategy Evaluation',
        parent_session_id: parentSessionId,
        summary: execution.response,
      })
      return true
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      await this.taskRuntime.failActiveTask(message, { strategy_id: strategyId })
      throw error
    }
  }

  private async maybeRunOperationalControlTask(): Promise<boolean> {
    const activeTask = this.taskRuntime.getActiveTask()
    if (
      !activeTask ||
      (activeTask.taskType !== 'run_market_scan' &&
        activeTask.taskType !== 'rerun_failed_agent_task' &&
        activeTask.taskType !== 'set_symbol_observe_only' &&
        activeTask.taskType !== 'create_paper_experiment' &&
        activeTask.taskType !== 'pause_paper_strategy')
    ) {
      return false
    }

    if (activeTask.taskType === 'rerun_failed_agent_task') {
      const failedRunId =
        typeof activeTask.inputData.failed_run_id === 'string'
          ? activeTask.inputData.failed_run_id
          : typeof activeTask.inputData.run_id === 'string'
            ? activeTask.inputData.run_id
            : null
      const agentName =
        typeof activeTask.inputData.agent_name === 'string'
          ? activeTask.inputData.agent_name
          : null
      if (!failedRunId && !agentName) {
        await this.taskRuntime.failActiveTask(
          'No failed_run_id or agent_name was attached to the failed-task rerun request.',
        )
        return true
      }

      const sessionId = `failed-rerun-${activeTask.taskId}`
      const parentSessionId = this.currentRunParentSessionId()
      const rerunPrompt = failedRunId
        ? `Rerun failed agent task ${failedRunId}${agentName ? ` for ${agentName}` : ''}.`
        : `Rerun the latest failed agent task for ${agentName}.`
      await this.backendClient.createSession({
        id: sessionId,
        user_input: rerunPrompt,
        kind: 'command_session',
        parent_session_id: parentSessionId,
        title: 'Failed Agent Task Rerun',
      })
      this.sessionRuntime.rememberBranch({
        id: sessionId,
        kind: 'command_session',
        title: 'Failed Agent Task Rerun',
        parent_session_id: parentSessionId,
        summary: null,
      })
      await this.attachSessionContext(sessionId)
      await this.backendClient.createMessage({
        session_id: sessionId,
        role: 'assistant',
        message_type: 'command',
        related_event_id: null,
        content: {
          text: rerunPrompt,
          task_id: activeTask.taskId,
          failed_run_id: failedRunId,
          agent_name: agentName,
        },
      })

      const execution = await this.backendClient.executeOperatorCommand('rerun_failed_agent_task', {
        session_id: sessionId,
        user_input: rerunPrompt,
        parameters: failedRunId ? { run_id: failedRunId } : { agent_name: agentName },
      })

      await this.backendClient.createMessage({
        session_id: sessionId,
        role: 'assistant',
        message_type: 'response',
        related_event_id: null,
        content: {
          text: execution.response,
          command: 'rerun_failed_agent_task',
          permitted: execution.permitted,
          result: execution.result,
        },
      })

      if (!execution.permitted) {
        await this.backendClient.createEvent({
          event_type: 'failed_agent_rerun_failed',
          severity: 'high',
          source: 'operator_loop',
          session_id: sessionId,
          task_id: activeTask.taskId,
          related_entity_type: 'command',
          related_entity_id: 'rerun_failed_agent_task',
          payload: {
            response: execution.response,
            result: execution.result,
          },
        })
        await this.taskRuntime.failActiveTask(execution.response, {
          command: 'rerun_failed_agent_task',
        })
        return true
      }

      const rerun =
        execution.result &&
        typeof execution.result === 'object' &&
        !Array.isArray(execution.result) &&
        typeof execution.result.rerun === 'object' &&
        execution.result.rerun !== null
          ? (execution.result.rerun as Record<string, unknown>)
          : null

      await this.backendClient.createEvent({
        event_type: 'failed_agent_rerun_requested',
        severity: 'info',
        source: 'operator_loop',
        session_id: sessionId,
        task_id: activeTask.taskId,
        related_entity_type: 'command',
        related_entity_id: 'rerun_failed_agent_task',
        payload: {
          response: execution.response,
          result: execution.result,
        },
      })
      await this.taskRuntime.completeActiveTask({
        summary: execution.response,
        agent_name: typeof rerun?.agent_name === 'string' ? rerun.agent_name : agentName,
        original_run_id:
          typeof rerun?.original_run_id === 'string' ? rerun.original_run_id : failedRunId,
        new_run_id: typeof rerun?.new_run_id === 'string' ? rerun.new_run_id : null,
        rerun_status: typeof rerun?.status === 'string' ? rerun.status : null,
      })
      this.sessionRuntime.rememberBranch({
        id: sessionId,
        kind: 'command_session',
        title: 'Failed Agent Task Rerun',
        parent_session_id: parentSessionId,
        summary: execution.response,
      })
      return true
    }

    if (activeTask.taskType === 'set_symbol_observe_only') {
      const symbol =
        typeof activeTask.inputData.symbol === 'string' ? activeTask.inputData.symbol : null
      if (!symbol) {
        await this.taskRuntime.failActiveTask(
          'No symbol was attached to the observe-only request.',
        )
        return true
      }

      const sessionId = `observe-only-${activeTask.taskId}`
      const parentSessionId = this.currentRunParentSessionId()
      const prompt = `Mark ${symbol} observe-only after repeated paper-side risk rejections.`
      await this.backendClient.createSession({
        id: sessionId,
        user_input: prompt,
        kind: 'command_session',
        parent_session_id: parentSessionId,
        title: 'Observe-Only Symbol Update',
      })
      this.sessionRuntime.rememberBranch({
        id: sessionId,
        kind: 'command_session',
        title: 'Observe-Only Symbol Update',
        parent_session_id: parentSessionId,
        summary: null,
      })
      await this.attachSessionContext(sessionId)
      await this.backendClient.createMessage({
        session_id: sessionId,
        role: 'assistant',
        message_type: 'command',
        related_event_id: null,
        content: {
          text: prompt,
          task_id: activeTask.taskId,
          symbol,
        },
      })

      const execution = await this.backendClient.executeOperatorCommand('set_symbol_observe_only', {
        session_id: sessionId,
        user_input: prompt,
        parameters: {
          symbol,
        },
      })

      await this.backendClient.createMessage({
        session_id: sessionId,
        role: 'assistant',
        message_type: 'response',
        related_event_id: null,
        content: {
          text: execution.response,
          command: 'set_symbol_observe_only',
          permitted: execution.permitted,
          result: execution.result,
        },
      })

      if (!execution.permitted) {
        await this.backendClient.createEvent({
          event_type: 'observe_only_update_failed',
          severity: 'high',
          source: 'operator_loop',
          session_id: sessionId,
          task_id: activeTask.taskId,
          related_entity_type: 'command',
          related_entity_id: 'set_symbol_observe_only',
          payload: {
            symbol,
            response: execution.response,
            result: execution.result,
          },
        })
        await this.taskRuntime.failActiveTask(execution.response, {
          command: 'set_symbol_observe_only',
          symbol,
        })
        return true
      }

      const observeOnlyResult =
        execution.result &&
        typeof execution.result === 'object' &&
        !Array.isArray(execution.result)
          ? execution.result
          : null
      await this.backendClient.createEvent({
        event_type: 'symbol_set_observe_only',
        severity: 'info',
        source: 'operator_loop',
        session_id: sessionId,
        task_id: activeTask.taskId,
        related_entity_type: 'symbol',
        related_entity_id:
          typeof observeOnlyResult?.symbol === 'string' ? observeOnlyResult.symbol : symbol,
        payload: {
          response: execution.response,
          result: execution.result,
        },
      })
      await this.taskRuntime.completeActiveTask({
        summary: execution.response,
        symbol: typeof observeOnlyResult?.symbol === 'string' ? observeOnlyResult.symbol : symbol,
        updated: typeof observeOnlyResult?.updated === 'boolean' ? observeOnlyResult.updated : null,
        observed_symbols: Array.isArray(observeOnlyResult?.observed_symbols)
          ? observeOnlyResult.observed_symbols
          : null,
        tradable_symbols: Array.isArray(observeOnlyResult?.tradable_symbols)
          ? observeOnlyResult.tradable_symbols
          : null,
      })
      this.sessionRuntime.rememberBranch({
        id: sessionId,
        kind: 'command_session',
        title: 'Observe-Only Symbol Update',
        parent_session_id: parentSessionId,
        summary: execution.response,
      })
      return true
    }

    if (activeTask.taskType === 'create_paper_experiment') {
      const title =
        typeof activeTask.inputData.title === 'string' && activeTask.inputData.title.trim().length > 0
          ? activeTask.inputData.title.trim()
          : 'Paper Experiment'
      const content =
        typeof activeTask.inputData.content === 'string' && activeTask.inputData.content.trim().length > 0
          ? activeTask.inputData.content.trim()
          : ''
      const parameters =
        activeTask.inputData.parameters &&
        typeof activeTask.inputData.parameters === 'object' &&
        !Array.isArray(activeTask.inputData.parameters)
          ? (activeTask.inputData.parameters as Record<string, unknown>)
          : {}
      const sessionId = `paper-experiment-${activeTask.taskId}`
      const parentSessionId = this.currentRunParentSessionId()
      const symbol =
        typeof parameters.symbol === 'string' && parameters.symbol.trim().length > 0
          ? parameters.symbol.trim()
          : null
      const prompt = symbol
        ? `Create a new paper experiment for ${symbol}: ${title}.`
        : `Create a new paper experiment: ${title}.`
      await this.backendClient.createSession({
        id: sessionId,
        user_input: prompt,
        kind: 'command_session',
        parent_session_id: parentSessionId,
        title: 'Create Paper Experiment',
      })
      this.sessionRuntime.rememberBranch({
        id: sessionId,
        kind: 'command_session',
        title: 'Create Paper Experiment',
        parent_session_id: parentSessionId,
        summary: null,
      })
      await this.attachSessionContext(sessionId)
      await this.backendClient.createMessage({
        session_id: sessionId,
        role: 'assistant',
        message_type: 'command',
        related_event_id: null,
        content: {
          text: prompt,
          task_id: activeTask.taskId,
          title,
          content,
          parameters,
        },
      })

      const execution = await this.backendClient.executeOperatorCommand('create_paper_experiment', {
        session_id: sessionId,
        user_input: prompt,
        parameters: {
          title,
          content,
          parameters,
        },
      })

      await this.backendClient.createMessage({
        session_id: sessionId,
        role: 'assistant',
        message_type: 'response',
        related_event_id: null,
        content: {
          text: execution.response,
          command: 'create_paper_experiment',
          permitted: execution.permitted,
          result: execution.result,
        },
      })

      if (!execution.permitted) {
        await this.backendClient.createEvent({
          event_type: 'paper_experiment_creation_failed',
          severity: 'high',
          source: 'operator_loop',
          session_id: sessionId,
          task_id: activeTask.taskId,
          related_entity_type: 'command',
          related_entity_id: 'create_paper_experiment',
          payload: {
            response: execution.response,
            result: execution.result,
            title,
            parameters,
          },
        })
        await this.taskRuntime.failActiveTask(execution.response, {
          command: 'create_paper_experiment',
          title,
        })
        return true
      }

      const experimentResult =
        execution.result &&
        typeof execution.result === 'object' &&
        !Array.isArray(execution.result)
          ? execution.result
          : null
      const strategyId =
        typeof experimentResult?.id === 'string' ? experimentResult.id : null
      await this.backendClient.createEvent({
        event_type: 'paper_experiment_created',
        severity: 'info',
        source: 'operator_loop',
        session_id: sessionId,
        task_id: activeTask.taskId,
        related_entity_type: 'strategy',
        related_entity_id: strategyId || title,
        payload: {
          response: execution.response,
          result: execution.result,
        },
      })
      await this.taskRuntime.completeActiveTask({
        summary: execution.response,
        strategy_id: strategyId,
        title:
          typeof experimentResult?.title === 'string' ? experimentResult.title : title,
        state:
          typeof experimentResult?.state === 'string' ? experimentResult.state : null,
        strategy_memory_id:
          typeof experimentResult?.strategy_memory_id === 'string'
            ? experimentResult.strategy_memory_id
            : null,
        created: typeof experimentResult?.created === 'boolean' ? experimentResult.created : null,
      })
      this.sessionRuntime.rememberBranch({
        id: sessionId,
        kind: 'command_session',
        title: 'Create Paper Experiment',
        parent_session_id: parentSessionId,
        summary: execution.response,
      })
      return true
    }

    if (activeTask.taskType === 'pause_paper_strategy') {
      const strategyId =
        typeof activeTask.inputData.strategy_id === 'string'
          ? activeTask.inputData.strategy_id
          : null
      const strategyName =
        typeof activeTask.inputData.strategy_name === 'string'
          ? activeTask.inputData.strategy_name
          : null
      const reason =
        typeof activeTask.inputData.reason === 'string' && activeTask.inputData.reason.trim().length > 0
          ? activeTask.inputData.reason.trim()
          : 'strategy marked under review'
      if (!strategyId) {
        await this.taskRuntime.failActiveTask(
          'No strategy_id was attached to the paper-strategy pause request.',
        )
        return true
      }

      const sessionId = `paper-pause-${activeTask.taskId}`
      const parentSessionId = this.currentRunParentSessionId()
      const pausePrompt = `Pause paper trading for ${strategyName || strategyId} because it is under review.`
      await this.backendClient.createSession({
        id: sessionId,
        user_input: pausePrompt,
        kind: 'command_session',
        parent_session_id: parentSessionId,
        title: 'Pause Paper Strategy',
      })
      this.sessionRuntime.rememberBranch({
        id: sessionId,
        kind: 'command_session',
        title: 'Pause Paper Strategy',
        parent_session_id: parentSessionId,
        summary: null,
      })
      await this.attachSessionContext(sessionId)
      await this.backendClient.createMessage({
        session_id: sessionId,
        role: 'assistant',
        message_type: 'command',
        related_event_id: null,
        content: {
          text: pausePrompt,
          task_id: activeTask.taskId,
          strategy_id: strategyId,
          reason,
        },
      })

      const execution = await this.backendClient.executeOperatorCommand('pause_paper_strategy', {
        session_id: sessionId,
        user_input: pausePrompt,
        parameters: {
          strategy_id: strategyId,
          reason,
        },
      })

      await this.backendClient.createMessage({
        session_id: sessionId,
        role: 'assistant',
        message_type: 'response',
        related_event_id: null,
        content: {
          text: execution.response,
          command: 'pause_paper_strategy',
          permitted: execution.permitted,
          result: execution.result,
        },
      })

      if (!execution.permitted) {
        await this.backendClient.createEvent({
          event_type: 'paper_strategy_pause_failed',
          severity: 'high',
          source: 'operator_loop',
          session_id: sessionId,
          task_id: activeTask.taskId,
          related_entity_type: 'command',
          related_entity_id: 'pause_paper_strategy',
          payload: {
            strategy_id: strategyId,
            reason,
            response: execution.response,
            result: execution.result,
          },
        })
        await this.taskRuntime.failActiveTask(execution.response, {
          command: 'pause_paper_strategy',
          strategy_id: strategyId,
        })
        return true
      }

      const pauseResult =
        execution.result &&
        typeof execution.result === 'object' &&
        !Array.isArray(execution.result)
          ? execution.result
          : null
      const reviewedOpenOpportunityCount =
        typeof pauseResult?.reviewed_open_opportunity_count === 'number'
          ? pauseResult.reviewed_open_opportunity_count
          : null
      await this.backendClient.createEvent({
        event_type: 'paper_strategy_paused',
        severity: 'info',
        source: 'operator_loop',
        session_id: sessionId,
        task_id: activeTask.taskId,
        related_entity_type: 'strategy',
        related_entity_id: strategyId,
        payload: {
          reason,
          response: execution.response,
          result: execution.result,
        },
      })
      await this.taskRuntime.completeActiveTask({
        summary: execution.response,
        strategy_id:
          typeof pauseResult?.strategy_id === 'string' ? pauseResult.strategy_id : strategyId,
        paused: typeof pauseResult?.paused === 'boolean' ? pauseResult.paused : null,
        reviewed_open_opportunity_count: reviewedOpenOpportunityCount,
      })
      this.sessionRuntime.rememberBranch({
        id: sessionId,
        kind: 'command_session',
        title: 'Pause Paper Strategy',
        parent_session_id: parentSessionId,
        summary: execution.response,
      })
      return true
    }

    const sessionId = `market-scan-${activeTask.taskId}`
    const parentSessionId = this.currentRunParentSessionId()
    await this.backendClient.createSession({
      id: sessionId,
      user_input: 'Refresh market scan because recent market observer activity is stale.',
      kind: 'command_session',
      parent_session_id: parentSessionId,
      title: 'Market Scan Refresh',
    })
    this.sessionRuntime.rememberBranch({
      id: sessionId,
      kind: 'command_session',
      title: 'Market Scan Refresh',
      parent_session_id: parentSessionId,
      summary: null,
    })
    await this.attachSessionContext(sessionId)
    await this.backendClient.createMessage({
      session_id: sessionId,
      role: 'assistant',
      message_type: 'command',
      related_event_id: null,
      content: {
        text: 'Refreshing market scan because recent market observer activity is stale.',
        task_id: activeTask.taskId,
      },
    })

    const execution = await this.backendClient.executeOperatorCommand('run_market_scan', {
      session_id: sessionId,
      user_input: 'Refresh market scan because recent market observer activity is stale.',
      parameters: {},
    })

    await this.backendClient.createMessage({
      session_id: sessionId,
      role: 'assistant',
      message_type: 'response',
      related_event_id: null,
      content: {
        text: execution.response,
        command: 'run_market_scan',
        permitted: execution.permitted,
        result: execution.result,
      },
    })

    if (!execution.permitted) {
      await this.backendClient.createEvent({
        event_type: 'market_scan_refresh_failed',
        severity: 'high',
        source: 'operator_loop',
        session_id: sessionId,
        task_id: activeTask.taskId,
        related_entity_type: 'command',
        related_entity_id: 'run_market_scan',
        payload: {
          response: execution.response,
          result: execution.result,
        },
      })
      await this.taskRuntime.failActiveTask(execution.response, {
        command: 'run_market_scan',
      })
      return true
    }

    const run =
      execution.result &&
      typeof execution.result === 'object' &&
      !Array.isArray(execution.result) &&
      typeof execution.result.run === 'object' &&
      execution.result.run !== null
        ? (execution.result.run as Record<string, unknown>)
        : null

    await this.backendClient.createEvent({
      event_type: 'market_scan_refreshed',
      severity: 'info',
      source: 'operator_loop',
      session_id: sessionId,
      task_id: activeTask.taskId,
      related_entity_type: 'command',
      related_entity_id: 'run_market_scan',
      payload: {
        response: execution.response,
        result: execution.result,
      },
    })
    await this.taskRuntime.completeActiveTask({
      summary: execution.response,
      run_id: typeof run?.run_id === 'string' ? run.run_id : null,
      run_status: typeof run?.status === 'string' ? run.status : null,
    })
    this.sessionRuntime.rememberBranch({
      id: sessionId,
      kind: 'command_session',
      title: 'Market Scan Refresh',
      parent_session_id: parentSessionId,
      summary: execution.response,
    })
    return true
  }

  private async maybeRefreshReportTask(params: {
    snapshot: OperatorSnapshot
    recommendations: OperatorRecommendation[]
    reviewSummary: Awaited<ReturnType<BackendGatewayClient['getReviewSummary']>>
    strategySummary: Awaited<ReturnType<BackendGatewayClient['getStrategySummary']>>
    riskSummary: Awaited<ReturnType<BackendGatewayClient['getRiskSummary']>>
    recentAgentRuns: Awaited<ReturnType<BackendGatewayClient['getAgentRuns']>>
  }): Promise<boolean> {
    const activeTask = this.taskRuntime.getActiveTask()
    if (
      !activeTask ||
      !['create_daily_review', 'create_review_report', 'create_diagnostic_report'].includes(activeTask.taskType)
    ) {
      return false
    }

    if (activeTask.taskType === 'create_review_report') {
      const reportType =
        typeof activeTask.inputData.report_type === 'string'
          ? activeTask.inputData.report_type
          : 'agent_performance'
      const title =
        typeof activeTask.inputData.title === 'string' && activeTask.inputData.title.trim().length > 0
          ? activeTask.inputData.title.trim()
          : this.reviewReportTitle(reportType)
      const content =
        reportType === 'trade_review'
          ? this.buildTradeReviewReportContent({ reviewSummary: params.reviewSummary })
          : reportType === 'risk_review'
            ? this.buildRiskReviewReportContent({ riskSummary: params.riskSummary })
            : this.buildAgentPerformanceReportContent({ recentAgentRuns: params.recentAgentRuns })
      const sessionId = `review-report-${activeTask.taskId}`
      const parentSessionId = this.currentRunParentSessionId()
      await this.backendClient.createSession({
        id: sessionId,
        user_input: `Refresh ${reportType.replaceAll('_', ' ')} report.`,
        kind: 'review_session',
        parent_session_id: parentSessionId,
        title,
      })
      this.sessionRuntime.rememberBranch({
        id: sessionId,
        kind: 'review_session',
        title,
        parent_session_id: parentSessionId,
        summary: null,
      })
      await this.attachSessionContext(sessionId)
      await this.backendClient.createMessage({
        session_id: sessionId,
        role: 'assistant',
        message_type: 'review',
        related_event_id: null,
        content: {
          text: `Refreshing ${reportType.replaceAll('_', ' ')} report.`,
          task_id: activeTask.taskId,
          title,
          report_type: reportType,
        },
      })

      const execution = await this.backendClient.executeOperatorCommand('create_review_report', {
        session_id: sessionId,
        user_input: `Refresh ${reportType.replaceAll('_', ' ')} report.`,
        parameters: {
          report_type: reportType,
          title,
          content,
        },
      })

      await this.backendClient.createMessage({
        session_id: sessionId,
        role: 'assistant',
        message_type: 'result',
        related_event_id: null,
        content: {
          text: execution.response,
          command: 'create_review_report',
          permitted: execution.permitted,
          result: execution.result,
        },
      })

      if (!execution.permitted) {
        await this.backendClient.createEvent({
          event_type: 'review_report_refresh_failed',
          severity: 'high',
          source: 'operator_loop',
          session_id: sessionId,
          task_id: activeTask.taskId,
          related_entity_type: 'command',
          related_entity_id: 'create_review_report',
          payload: {
            report_type: reportType,
            response: execution.response,
            result: execution.result,
          },
        })
        await this.taskRuntime.failActiveTask(execution.response, {
          command: 'create_review_report',
          report_type: reportType,
        })
        return true
      }

      const result =
        execution.result && typeof execution.result === 'object' && !Array.isArray(execution.result)
          ? execution.result
          : null
      await this.backendClient.createEvent({
        event_type: 'review_report_refreshed',
        severity: 'info',
        source: 'operator_loop',
        session_id: sessionId,
        task_id: activeTask.taskId,
        related_entity_type: 'command',
        related_entity_id: 'create_review_report',
        payload: {
          report_type: reportType,
          response: execution.response,
          result: execution.result,
        },
      })
      await this.taskRuntime.completeActiveTask({
        summary: execution.response,
        artifact_id: typeof result?.artifact_id === 'string' ? result.artifact_id : null,
        report_path: typeof result?.path === 'string' ? result.path : null,
        report_type: reportType,
        title,
      })
      this.sessionRuntime.rememberBranch({
        id: sessionId,
        kind: 'review_session',
        title,
        parent_session_id: parentSessionId,
        summary: execution.response,
      })
      return true
    }

    if (activeTask.taskType === 'create_diagnostic_report') {
      const reportType =
        typeof activeTask.inputData.report_type === 'string'
          ? activeTask.inputData.report_type
          : 'system_health'
      const title =
        typeof activeTask.inputData.title === 'string' && activeTask.inputData.title.trim().length > 0
          ? activeTask.inputData.title.trim()
          : this.diagnosticReportTitle(reportType)
      const content =
        reportType === 'worker_health'
          ? this.buildWorkerHealthDiagnosticContent({
              snapshot: params.snapshot,
              recentAgentRuns: params.recentAgentRuns,
            })
          : reportType === 'model_runtime_health'
            ? this.buildModelRuntimeHealthDiagnosticContent({
                snapshot: params.snapshot,
                recentAgentRuns: params.recentAgentRuns,
              })
            : reportType === 'data_source_health'
              ? this.buildDataSourceHealthDiagnosticContent({
                  snapshot: params.snapshot,
                  recentAgentRuns: params.recentAgentRuns,
                })
              : this.buildSystemHealthDiagnosticContent({
                  snapshot: params.snapshot,
                  recommendations: params.recommendations,
                  recentAgentRuns: params.recentAgentRuns,
                })
      const sessionId = `diagnostic-report-${activeTask.taskId}`
      const parentSessionId = this.currentRunParentSessionId()
      await this.backendClient.createSession({
        id: sessionId,
        user_input: `Refresh ${reportType.replaceAll('_', ' ')} diagnostic report.`,
        kind: 'diagnostic_session',
        parent_session_id: parentSessionId,
        title,
      })
      this.sessionRuntime.rememberBranch({
        id: sessionId,
        kind: 'diagnostic_session',
        title,
        parent_session_id: parentSessionId,
        summary: null,
      })
      await this.attachSessionContext(sessionId)
      await this.backendClient.createMessage({
        session_id: sessionId,
        role: 'assistant',
        message_type: 'diagnostic',
        related_event_id: null,
        content: {
          text: `Refreshing ${reportType.replaceAll('_', ' ')} diagnostic report.`,
          task_id: activeTask.taskId,
          title,
          report_type: reportType,
        },
      })

      const execution = await this.backendClient.executeOperatorCommand('create_diagnostic_report', {
        session_id: sessionId,
        user_input: `Refresh ${reportType.replaceAll('_', ' ')} diagnostic report.`,
        parameters: {
          report_type: reportType,
          title,
          content,
        },
      })

      await this.backendClient.createMessage({
        session_id: sessionId,
        role: 'assistant',
        message_type: 'result',
        related_event_id: null,
        content: {
          text: execution.response,
          command: 'create_diagnostic_report',
          permitted: execution.permitted,
          result: execution.result,
        },
      })

      if (!execution.permitted) {
        await this.backendClient.createEvent({
          event_type: 'diagnostic_report_refresh_failed',
          severity: 'high',
          source: 'operator_loop',
          session_id: sessionId,
          task_id: activeTask.taskId,
          related_entity_type: 'command',
          related_entity_id: 'create_diagnostic_report',
          payload: {
            report_type: reportType,
            response: execution.response,
            result: execution.result,
          },
        })
        await this.taskRuntime.failActiveTask(execution.response, {
          command: 'create_diagnostic_report',
          report_type: reportType,
        })
        return true
      }

      const result =
        execution.result && typeof execution.result === 'object' && !Array.isArray(execution.result)
          ? execution.result
          : null
      await this.backendClient.createEvent({
        event_type: 'diagnostic_report_refreshed',
        severity: 'info',
        source: 'operator_loop',
        session_id: sessionId,
        task_id: activeTask.taskId,
        related_entity_type: 'command',
        related_entity_id: 'create_diagnostic_report',
        payload: {
          report_type: reportType,
          response: execution.response,
          result: execution.result,
        },
      })
      await this.taskRuntime.completeActiveTask({
        summary: execution.response,
        artifact_id: typeof result?.artifact_id === 'string' ? result.artifact_id : null,
        report_path: typeof result?.path === 'string' ? result.path : null,
        report_type: reportType,
        title,
      })
      this.sessionRuntime.rememberBranch({
        id: sessionId,
        kind: 'diagnostic_session',
        title,
        parent_session_id: parentSessionId,
        summary: execution.response,
      })
      return true
    }

    const targetDate =
      typeof activeTask.inputData.target_date === 'string'
        ? activeTask.inputData.target_date
        : new Date().toISOString().slice(0, 10)
    const title =
      typeof activeTask.inputData.title === 'string' && activeTask.inputData.title.trim().length > 0
        ? activeTask.inputData.title.trim()
        : `Operator Daily Review ${targetDate}`
    const content = this.buildDailyReviewContent(params)
    const sessionId = `daily-review-${activeTask.taskId}`
    const parentSessionId = this.currentRunParentSessionId()
    await this.backendClient.createSession({
      id: sessionId,
      user_input: `Refresh operator daily review for ${targetDate}.`,
      kind: 'review_session',
      parent_session_id: parentSessionId,
      title: 'Daily Review Refresh',
    })
    this.sessionRuntime.rememberBranch({
      id: sessionId,
      kind: 'review_session',
      title: 'Daily Review Refresh',
      parent_session_id: parentSessionId,
      summary: null,
    })
    await this.attachSessionContext(sessionId)
    await this.backendClient.createMessage({
      session_id: sessionId,
      role: 'assistant',
      message_type: 'review',
      related_event_id: null,
      content: {
        text: `Refreshing operator daily review for ${targetDate}.`,
        task_id: activeTask.taskId,
        title,
      },
    })

    const execution = await this.backendClient.executeOperatorCommand('create_daily_review', {
      session_id: sessionId,
      user_input: `Refresh operator daily review for ${targetDate}.`,
      parameters: {
        title,
        content,
      },
    })

    await this.backendClient.createMessage({
      session_id: sessionId,
      role: 'assistant',
      message_type: 'result',
      related_event_id: null,
      content: {
        text: execution.response,
        command: 'create_daily_review',
        permitted: execution.permitted,
        result: execution.result,
      },
    })

    if (!execution.permitted) {
      await this.backendClient.createEvent({
        event_type: 'daily_review_refresh_failed',
        severity: 'high',
        source: 'operator_loop',
        session_id: sessionId,
        task_id: activeTask.taskId,
        related_entity_type: 'command',
        related_entity_id: 'create_daily_review',
        payload: {
          response: execution.response,
          result: execution.result,
        },
      })
      await this.taskRuntime.failActiveTask(execution.response, {
        command: 'create_daily_review',
      })
      return true
    }

    const reviewId =
      execution.result &&
      typeof execution.result === 'object' &&
      !Array.isArray(execution.result) &&
      typeof execution.result.id === 'string'
        ? execution.result.id
        : null
    await this.backendClient.createEvent({
      event_type: 'daily_review_refreshed',
      severity: 'info',
      source: 'operator_loop',
      session_id: sessionId,
      task_id: activeTask.taskId,
      related_entity_type: 'command',
      related_entity_id: 'create_daily_review',
      payload: {
        response: execution.response,
        result: execution.result,
      },
    })
    await this.taskRuntime.completeActiveTask({
      summary: execution.response,
      review_id: reviewId,
      title,
      target_date: targetDate,
    })
    this.sessionRuntime.rememberBranch({
      id: sessionId,
      kind: 'review_session',
      title: 'Daily Review Refresh',
      parent_session_id: parentSessionId,
      summary: execution.response,
    })
    return true
  }

  private repairSessionId(decisionId: string): string {
    return `diagnostic-${decisionId.replace(/[^a-z0-9-]+/gi, '-')}`
  }

  private repairSessionShape(decision: NonNullable<ReturnType<RepairEngine['evaluate']>>): {
    kind: 'diagnostic_session' | 'strategy_session'
    title: string
  } {
    if (decision.commandName === 'pause_paper_strategy') {
      return {
        kind: 'strategy_session',
        title: 'Paper Strategy Pause Decision',
      }
    }
    if (decision.commandName === 'mark_strategy_under_review') {
      return {
        kind: 'strategy_session',
        title: 'Strategy Review Decision',
      }
    }
    if (decision.commandName === 'propose_strategy_promotion') {
      return {
        kind: 'strategy_session',
        title: 'Strategy Promotion Proposal',
      }
    }
    if (decision.commandName === 'propose_live_limited_enablement') {
      return {
        kind: 'strategy_session',
        title: 'Live-Limited Enablement Proposal',
      }
    }
    return {
      kind: 'diagnostic_session',
      title: 'Repair Decision',
    }
  }

  private recoveredRepairRecommendation(): OperatorRecommendation | null {
    const activeTask = this.taskRuntime.getActiveTask()
    if (!activeTask || activeTask.taskType !== 'create_repair_proposal') {
      return null
    }
    const kind = typeof activeTask.inputData.kind === 'string' ? activeTask.inputData.kind : null
    const summary =
      typeof activeTask.inputData.summary === 'string' ? activeTask.inputData.summary : null
    if (!kind || !summary) {
      return null
    }
    const metadata = { ...activeTask.inputData }
    delete metadata.recommendation_id
    delete metadata.kind
    delete metadata.summary
    return {
      id: activeTask.recommendationId || `recovered:${activeTask.taskId}`,
      kind,
      severity: 'medium',
      summary,
      taskType: 'create_repair_proposal',
      metadata,
    }
  }

  private async maybeResumeRecoveredRepairDecision(
    repairDecision: ReturnType<RepairEngine['evaluate']>,
  ): Promise<boolean> {
    const activeTask = this.taskRuntime.getActiveTask()
    if (!activeTask || activeTask.taskType !== 'create_repair_proposal') {
      return false
    }

    let decision = repairDecision
    if (
      decision &&
      activeTask.recommendationId &&
      decision.recommendationId &&
      decision.recommendationId !== activeTask.recommendationId
    ) {
      decision = null
    }

    if (!decision) {
      const recoveredRecommendation = this.recoveredRepairRecommendation()
      if (!recoveredRecommendation) {
        return false
      }
      decision = this.repairEngine.evaluate([recoveredRecommendation], this.config.permissionMode)
    }

    if (!decision) {
      return false
    }

    await this.maybeApplyRepairDecision(decision)
    return true
  }

  private async maybeApplyRepairDecision(decision: ReturnType<RepairEngine['evaluate']>): Promise<void> {
    if (!decision || this.attemptedRepairDecisionIds.has(decision.id)) {
      return
    }

    const activeTask = this.taskRuntime.getActiveTask()
    const repairTask =
      decision.recommendationId
        ? this.taskRuntime.getTaskForRecommendation(decision.recommendationId)
        : activeTask &&
            activeTask.taskType === 'create_repair_proposal'
          ? activeTask
          : null
    const repairTaskMatches = repairTask?.taskType === 'create_repair_proposal'
    if (decision.classification === 'proposal_only' && !repairTaskMatches) {
      return
    }

    const sessionId = this.repairSessionId(decision.id)
    const sessionShape = this.repairSessionShape(decision)
    const parentSessionId = this.currentRunParentSessionId()
    await this.backendClient.createSession({
      id: sessionId,
      user_input: decision.summary,
      kind: sessionShape.kind,
      parent_session_id: parentSessionId,
      title: sessionShape.title,
    })
    this.sessionRuntime.rememberBranch({
      id: sessionId,
      kind: sessionShape.kind,
      title: sessionShape.title,
      parent_session_id: parentSessionId,
      summary: decision.summary,
    })
    await this.attachSessionContext(sessionId)
    await this.backendClient.createArtifact({
      task_id: this.taskRuntime.snapshot().currentTaskId,
      session_id: sessionId,
      artifact_type: 'repair_decision',
      title: decision.title,
      content: {
        decision_id: decision.id,
        classification: decision.classification,
        recommendation_id: decision.recommendationId || null,
        summary: decision.summary,
        rationale: decision.rationale,
        command: decision.commandName || null,
        parameters: decision.parameters || {},
      },
    })
    await this.backendClient.createEvent({
      event_type: 'repair_attempted',
      severity: 'medium',
      source: 'operator_loop',
      session_id: sessionId,
      task_id: this.taskRuntime.snapshot().currentTaskId,
      related_entity_type: 'repair',
      related_entity_id: decision.id,
      payload: {
        recommendation_id: decision.recommendationId || null,
        classification: decision.classification,
        summary: decision.summary,
      },
    })

    if (decision.shouldExecute && decision.commandName) {
      const execution = await this.backendClient.executeOperatorCommand(decision.commandName, {
        session_id: sessionId,
        user_input: decision.summary,
        parameters: decision.parameters || {},
      })
      if (decision.classification === 'proposal_only' && execution.permitted) {
        const proposalArtifactId =
          execution.result &&
          typeof execution.result === 'object' &&
          !Array.isArray(execution.result) &&
          typeof execution.result.artifact_id === 'string'
            ? execution.result.artifact_id
            : null
        await this.backendClient.createEvent({
          event_type: 'proposal_created',
          severity: 'info',
          source: 'operator_loop',
          session_id: sessionId,
          task_id: this.taskRuntime.snapshot().currentTaskId,
          related_entity_type: 'proposal',
          related_entity_id: proposalArtifactId || decision.id,
          payload: {
            command: decision.commandName,
            artifact_id: proposalArtifactId,
            response: execution.response,
            result: execution.result,
          },
        })
        if (repairTaskMatches && decision.recommendationId) {
          await this.taskRuntime.completeTaskForRecommendation(decision.recommendationId, {
            summary: execution.response,
            proposal_artifact_id: proposalArtifactId,
            strategy_id:
              typeof decision.parameters?.strategy_id === 'string'
                ? decision.parameters.strategy_id
                : null,
          })
        }
        this.sessionRuntime.rememberBranch({
          id: sessionId,
          kind: sessionShape.kind,
          title: sessionShape.title,
          parent_session_id: parentSessionId,
          summary: execution.response,
        })
      } else {
        await this.backendClient.createEvent({
          event_type: execution.permitted ? 'repair_executed' : 'repair_failed',
          severity: execution.permitted ? 'info' : 'high',
          source: 'operator_loop',
          session_id: sessionId,
          task_id: this.taskRuntime.snapshot().currentTaskId,
          related_entity_type: 'repair',
          related_entity_id: decision.id,
          payload: {
            command: decision.commandName,
            permitted: execution.permitted,
            response: execution.response,
            result: execution.result,
          },
        })
        if (execution.permitted) {
          if (decision.recommendationId) {
            await this.taskRuntime.completeTaskForRecommendation(decision.recommendationId, {
              summary: execution.response,
              command: decision.commandName,
              strategy_id:
                typeof decision.parameters?.strategy_id === 'string'
                  ? decision.parameters.strategy_id
                  : null,
            })
          }
          this.sessionRuntime.rememberBranch({
            id: sessionId,
            kind: sessionShape.kind,
            title: sessionShape.title,
            parent_session_id: parentSessionId,
            summary: execution.response,
          })
        } else {
          if (decision.recommendationId) {
            await this.taskRuntime.failTaskForRecommendation(decision.recommendationId, execution.response, {
              command: decision.commandName,
              strategy_id:
                typeof decision.parameters?.strategy_id === 'string'
                  ? decision.parameters.strategy_id
                  : null,
            })
          }
        }
      }
    }

    this.attemptedRepairDecisionIds.add(decision.id)
  }

  private isRecent(timestamp: string | null | undefined, maxAgeHours = 24): boolean {
    if (!timestamp) {
      return false
    }
    const parsed = new Date(timestamp)
    if (Number.isNaN(parsed.getTime())) {
      return false
    }
    return Date.now() - parsed.getTime() <= maxAgeHours * 60 * 60 * 1000
  }

  private payloadText(payload: Record<string, unknown>, key: string): string | null {
    const value = payload[key]
    return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null
  }

  private eventNeedsRecoveryReview(event: PersistedOperatorEvent): boolean {
    const eventType = event.event_type.toLowerCase()
    const severity = event.severity.toLowerCase()
    if (severity === 'high') {
      return true
    }
    return (
      eventType.includes('failed') ||
      eventType.includes('stale') ||
      eventType.includes('degraded') ||
      eventType.includes('rejected')
    )
  }

  private eventRecoveryLines(events: PersistedOperatorEvent[]): string[] {
    const lines: string[] = []
    const seen = new Set<string>()
    const remember = (value: string | null | undefined) => {
      const normalized = value?.trim()
      if (!normalized) {
        return
      }
      const key = normalized.toLowerCase()
      if (seen.has(key)) {
        return
      }
      seen.add(key)
      lines.push(normalized)
    }

    for (const event of events) {
      if (!this.isRecent(event.created_at) || !this.eventNeedsRecoveryReview(event)) {
        continue
      }
      const payload = event.payload || {}
      const explicitDetail =
        this.payloadText(payload, 'response') ||
        this.payloadText(payload, 'summary') ||
        this.payloadText(payload, 'error') ||
        this.payloadText(payload, 'message') ||
        this.payloadText(payload, 'issue')
      if (explicitDetail) {
        remember(`Recent operator error still needs review: ${explicitDetail}.`)
        continue
      }

      switch (event.event_type) {
        case 'heartbeat_stale':
          remember('Recent operator error still needs review: operator heartbeat went stale.')
          break
        case 'operator_degraded':
          remember('Recent operator error still needs review: operator supervision entered degraded mode.')
          break
        case 'command_rejected':
          remember('Recent operator error still needs review: a recent operator command was rejected.')
          break
        case 'task_failed':
          remember('Recent operator error still needs review: a recent operator task failed.')
          break
        default:
          remember(`Recent operator error still needs review: ${event.event_type}.`)
          break
      }
    }

    return lines
  }

  private buildRecoveryContext(params: {
    lastError: string | null
    heartbeatIntent: string | null
    failedTasks: Awaited<ReturnType<BackendGatewayClient['getOperatorTasks']>>
    artifacts: OperatorSessionDetail['artifacts']
    branches: OperatorSessionBranchState[]
    recentEvents: PersistedOperatorEvent[]
  }): { intent: string | null; mode: OperatorMode | null } {
    const summaries: string[] = []
    const recentFailedTasks = params.failedTasks.filter(task =>
      this.isRecent(task.completed_at || task.created_at || null),
    )
    const unresolvedProposals = params.artifacts.filter(artifact =>
      artifact.artifact_type.toLowerCase().includes('proposal'),
    )
    const branchLines = this.branchRecoveryLines(params.branches)
    const recentErrorLines = this.eventRecoveryLines(params.recentEvents)
    const severeRecentErrorEvent = params.recentEvents.some(
      event =>
        this.isRecent(event.created_at) &&
        this.eventNeedsRecoveryReview(event) &&
        (event.severity.toLowerCase() === 'high' ||
          event.event_type.includes('failed') ||
          event.event_type.includes('stale') ||
          event.event_type.includes('degraded')),
    )

    if (recentFailedTasks.length > 0) {
      const latest = recentFailedTasks[0]
      const detail = latest.error || latest.task_type
      summaries.push(
        `${recentFailedTasks.length} recent failed operator task(s) still need review. Latest: ${detail}.`,
      )
    } else if (params.lastError) {
      summaries.push(`Last operator error still needs review: ${params.lastError}.`)
    }

    if (unresolvedProposals.length > 0) {
      summaries.push(
        `${unresolvedProposals.length} unresolved proposal artifact(s) waiting for review. Latest: ${unresolvedProposals[0].title}.`,
      )
    }
    this.appendRecoveryLinesWithBudget(summaries, recentErrorLines)
    this.appendRecoveryLinesWithBudget(summaries, branchLines)
    this.appendRecoveryLinesWithBudget(summaries, this.compactedRecoveryLines(params.artifacts))

    if (summaries.length === 0) {
      return {
        intent: params.heartbeatIntent?.trim() || null,
        mode: null,
      }
    }

    return {
      intent: summaries.join(' '),
      mode:
        recentFailedTasks.length > 0 || !!params.lastError || severeRecentErrorEvent
          ? 'Degraded'
          : 'Observing',
    }
  }

  private seedCompactedDates(artifacts: OperatorSessionDetail['artifacts']): void {
    for (const artifact of artifacts) {
      if (artifact.artifact_type !== 'operator_daily_summary') {
        continue
      }
      const content = artifact.content
      if (!content || typeof content !== 'object' || Array.isArray(content)) {
        continue
      }
      const date = typeof content.date === 'string' ? content.date : null
      if (date) {
        this.compactedDates.add(date)
      }
    }
  }

  private previousUtcDate(): string {
    return new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString().slice(0, 10)
  }

  private async maybeCompactPreviousDay(): Promise<void> {
    const targetDate = this.previousUtcDate()
    if (this.compactedDates.has(targetDate)) {
      return
    }
    const result = await this.backendClient.compactOperatorMemory(targetDate)
    if (result?.date) {
      this.compactedDates.add(result.date)
    }
  }

  async recoverState(): Promise<void> {
    if (this.recovered) {
      return
    }

    const [operatorStatus, sessionDetail, sessionSummaries, runningTasks, failedTasks, recentEvents] = await Promise.all([
      this.backendClient.getOperatorStatus(),
      this.backendClient.getOperatorSession(this.config.mainSessionId).catch(() => null),
      this.backendClient.getOperatorSessions(50).catch(() => []),
      this.backendClient.getOperatorTasks('running').catch(() => []),
      this.backendClient.getOperatorTasks('failed').catch(() => []),
      this.backendClient.getOperatorEvents(20).catch(() => []),
    ])

    const descendantSummaries = this.descendantSessionSummaries(sessionSummaries)
    const prioritizedChildSessionSummaries = (() => {
      const activeBranchId = operatorStatus.active_branch_id?.trim() || ''
      if (!activeBranchId) {
        return descendantSummaries.slice(0, OperatorLoop.RECOVERY_BRANCH_FETCH_LIMIT)
      }

      const activeBranchSummary = descendantSummaries.find(session => session.id === activeBranchId)
      if (!activeBranchSummary) {
        return descendantSummaries.slice(0, OperatorLoop.RECOVERY_BRANCH_FETCH_LIMIT)
      }

      return [
        activeBranchSummary,
        ...descendantSummaries.filter(session => session.id !== activeBranchId),
      ].slice(0, OperatorLoop.RECOVERY_BRANCH_FETCH_LIMIT)
    })()
    const childSessionDetails = (
      await Promise.all(
        prioritizedChildSessionSummaries.map(session =>
          this.backendClient.getOperatorSession(session.id).catch(() => null),
        ),
      )
    ).filter((detail): detail is OperatorSessionDetail => detail !== null)

    this.sessionRuntime.restore(
      sessionDetail,
      sessionSummaries,
      childSessionDetails,
      operatorStatus.active_branch_id
        ? {
            id: operatorStatus.active_branch_id,
            kind: operatorStatus.active_branch_kind || 'command_session',
            title: operatorStatus.active_branch_title || null,
            summary: operatorStatus.current_intent || null,
          }
        : null,
    )
    this.taskRuntime.restore(runningTasks)
    this.lastError = operatorStatus.last_error
    this.lastSuccessAt = operatorStatus.last_success_at
    this.seedCompactedDates(sessionDetail?.artifacts || [])
    const activeBranch = this.sessionRuntime.getActiveBranch()
    const orderedBranches = this.sessionRuntime.getBranches()
    const recoveryBranches =
      activeBranch === null
        ? orderedBranches
        : [activeBranch, ...orderedBranches.filter(branch => branch.id !== activeBranch.id)]
    const recovery = this.buildRecoveryContext({
      lastError: operatorStatus.last_error,
      heartbeatIntent: operatorStatus.current_intent,
      failedTasks,
      artifacts: sessionDetail?.artifacts || [],
      branches: recoveryBranches,
      recentEvents,
    })
    this.recoveryIntent = recovery.intent
    this.recoveryMode = recovery.mode
    if (this.recoveryIntent) {
      this.sessionRuntime.setCurrentIntent(this.recoveryIntent)
    }

    if (this.recoveryMode === 'Degraded' || operatorStatus.last_error || operatorStatus.status === 'stale') {
      this.mode = 'Degraded'
    } else if (operatorStatus.mode === 'Paused') {
      this.mode = 'Paused'
    } else {
      this.mode = 'Observing'
    }

    this.recovered = true
  }

  async runOnce(): Promise<boolean> {
    try {
      if (this.isShadowRuntime()) {
        return await this.runShadowOnce()
      }
      await this.maybeCompactPreviousDay()
      const [operatorStatus, status, health, supervision, subsystemReview] = await Promise.all([
        this.backendClient.getOperatorStatus(),
        this.backendClient.getGatewayStatus(),
        this.backendClient.getHealth(),
        this.loadSupervisionData(),
        this.loadSubsystemReviewData(),
      ])
      const {
        dreamingSummary,
        reviewSummary,
        strategySummary,
      recentAgentRuns,
      riskSummary,
      portfolioSummary,
      portfolioHistory,
      operatorExtensions,
      } =
        subsystemReview
      const { sessionDetail, sessionSummaries, recentCommands, operatorTasks } = supervision
      this.syncPersistedActiveBranch(operatorStatus, sessionSummaries)
      this.beginRunSessionParentScope()
      const snapshot: OperatorSnapshot = { status, health }
      const recommendations = this.recommendationEngine.list(
        snapshot,
        dreamingSummary,
        reviewSummary,
        strategySummary,
        recentAgentRuns,
        sessionDetail?.artifacts || [],
        riskSummary,
        recentCommands,
        operatorTasks,
        portfolioSummary,
        portfolioHistory,
        operatorExtensions,
      )
      const taskRecommendations = this.recommendationsAllowedForTaskSync(recommendations)
      const repairDecision = this.repairEngine.evaluate(recommendations, this.config.permissionMode)
      const derivedMode = this.deriveMode(snapshot, recommendations)
      const liveIntent = recommendations[0]?.summary || repairDecision?.rationale || null
      if (liveIntent) {
        this.mode = derivedMode
        this.sessionRuntime.setCurrentIntent(liveIntent)
      } else if (this.recoveryIntent) {
        this.mode =
          derivedMode === 'Paused'
            ? 'Paused'
            : this.recoveryMode === 'Degraded'
              ? 'Degraded'
              : derivedMode
        this.sessionRuntime.setCurrentIntent(this.recoveryIntent)
      } else {
        this.mode = derivedMode
        this.sessionRuntime.setCurrentIntent('monitoring system state')
      }
      await this.eventRouter.route(recommendations)
      await this.taskRuntime.sync(taskRecommendations)
      const resumedRecoveredRepair = await this.maybeResumeRecoveredRepairDecision(repairDecision)
      if (!resumedRecoveredRepair && !repairDecision) {
        this.attemptedRepairDecisionIds.clear()
      } else if (!resumedRecoveredRepair) {
        await this.maybeApplyRepairDecision(repairDecision)
      }
      const activeTaskBeforeEvaluation = this.taskRuntime.getActiveTask()
      const evaluatedStrategyTask = await this.maybeEvaluateStrategyTask()
      if (evaluatedStrategyTask && this.taskRuntime.snapshot().currentTaskId === null) {
        this.settleModeAfterTaskRun(snapshot, activeTaskBeforeEvaluation)
      }
      const activeTaskBeforeOperationalControl = this.taskRuntime.getActiveTask()
      const operationalTask = await this.maybeRunOperationalControlTask()
      if (operationalTask && this.taskRuntime.snapshot().currentTaskId === null) {
        this.settleModeAfterTaskRun(snapshot, activeTaskBeforeOperationalControl)
      }
      const activeTaskBeforeReportRefresh = this.taskRuntime.getActiveTask()
      const refreshedReportTask = await this.maybeRefreshReportTask({
        snapshot,
        recommendations,
        reviewSummary,
        strategySummary,
        riskSummary,
        recentAgentRuns,
      })
      if (refreshedReportTask && this.taskRuntime.snapshot().currentTaskId === null) {
        this.settleModeAfterTaskRun(snapshot, activeTaskBeforeReportRefresh)
      }
      const activeTaskBeforeDelegation = this.taskRuntime.getActiveTask()
      const delegatedTask = await this.maybeDelegateSpecialistTask()
      if (delegatedTask && this.taskRuntime.snapshot().currentTaskId === null) {
        this.settleModeAfterTaskRun(snapshot, activeTaskBeforeDelegation)
      }
      const heartbeat = buildHeartbeat({
        mode: this.mode,
        permissionMode: this.config.permissionMode,
        session: this.sessionRuntime.getMainSession(),
        tasks: this.taskRuntime.snapshot(),
        activeBranch: this.sessionRuntime.getActiveBranch(),
        modelProvider: this.config.modelProvider,
        lastError: this.lastError,
        lastSuccessAt: this.lastSuccessAt,
        runtimeId: this.runtimeId(),
        runtimeMode: this.runtimeMode(),
        runtimeState: this.runtimeState(),
      })
      await this.backendClient.postHeartbeat(heartbeat)
      this.lastSuccessAt = heartbeat.last_seen_at
      this.lastError = null
      return true
    } catch (error) {
      this.lastError = error instanceof Error ? error.message : String(error)
      this.mode = 'Degraded'
      this.sessionRuntime.setCurrentIntent(this.lastError)
      return false
    } finally {
      this.endRunSessionParentScope()
    }
  }

  async start(): Promise<void> {
    await this.recoverState()
    await this.backendClient.createEvent({
      event_type: 'operator_started',
      severity: 'info',
      source: 'operator_shell',
      session_id: this.config.mainSessionId,
      related_entity_type: 'operator',
      related_entity_id: 'operator',
      payload: {
        mode: this.mode,
        runtime_id: this.runtimeId(),
        runtime_mode: this.runtimeMode(),
        runtime_state: this.runtimeState(),
      },
    })

    if (this.config.once) {
      const ok = await this.runOnce()
      if (!ok) {
        process.exitCode = 1
      }
      return
    }

    await this.runOnce()
    this.intervalHandle = setInterval(() => {
      void this.runOnce()
    }, this.config.heartbeatIntervalSec * 1000)
  }

  async stop(): Promise<void> {
    if (this.intervalHandle) {
      clearInterval(this.intervalHandle)
      this.intervalHandle = null
    }
    await this.backendClient.createEvent({
      event_type: 'operator_stopped',
      severity: 'info',
      source: 'operator_shell',
      session_id: this.config.mainSessionId,
      related_entity_type: 'operator',
      related_entity_id: 'operator',
      payload: {
        mode: this.mode,
        runtime_id: this.runtimeId(),
        runtime_mode: this.runtimeMode(),
        runtime_state: this.runtimeState(),
      },
    })
  }
}
