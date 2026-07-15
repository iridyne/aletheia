import type {
  BackendGatewayClient,
  OperatorRecommendation,
  OperatorTaskState,
  PersistedOperatorTask,
} from './schemas.js'
import { createHash } from 'node:crypto'
import { isTaskNotFoundError, errorMessage } from './gateway_errors.js'

function stableJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableJson).join(',')}]`
  }
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    return `{${Object.keys(record).sort().map(key => `${JSON.stringify(key)}:${stableJson(record[key])}`).join(',')}}`
  }
  return JSON.stringify(value)
}

function recommendationFingerprint(recommendation: OperatorRecommendation): string {
  // A completed diagnostic is reusable only while the underlying evidence is identical.
  // Summary wording is deliberately excluded: text changes must not restart expensive work.
  const material = {
    id: recommendation.id,
    kind: recommendation.kind,
    task_type: recommendation.taskType || 'diagnose_system_health',
    metadata: recommendation.metadata || {},
  }
  return createHash('sha256').update(stableJson(material)).digest('hex')
}

export class TaskRuntime {
  private static readonly RUNNING_TASK_RECOVERY_TIMEOUT_MS = 30 * 60 * 1000

  private readonly terminalRecommendationFingerprints = new Set<string>()
  private queuedTasks: Array<{
    recommendationId: string | null
    taskId: string
    taskType: string
    assignedTo: string
    inputData: Record<string, unknown>
    priority: string
    createdAt: string | null
    restoredFromRecovery: boolean
  }> = []

  private activeTask:
    | {
        recommendationId: string | null
        taskId: string
        taskType: string
        assignedTo: string
        inputData: Record<string, unknown>
        priority: string
        createdAt: string | null
        restoredFromRecovery: boolean
      }
    | null = null

  private state: OperatorTaskState = {
    currentTaskId: null,
    queueSize: 0,
  }

  constructor(
    private readonly backendClient: Pick<BackendGatewayClient, 'createTask' | 'updateTask'>,
  ) {}

  // Returns the updated task, or null if the task no longer exists (404).
  // All other errors are re-thrown so the caller can handle them normally.
  private async safeUpdateTask(
    taskId: string,
    payload: Parameters<BackendGatewayClient['updateTask']>[1],
  ): Promise<{ id: string } | null> {
    try {
      return await this.backendClient.updateTask(taskId, payload)
    } catch (err) {
      if (isTaskNotFoundError(errorMessage(err))) {
        console.warn(`task_evicted:${taskId} — backend has no record; dropping from queue`)
        return null
      }
      throw err
    }
  }

  snapshot(): OperatorTaskState {
    return this.state
  }

  private toRuntimeTask(task: PersistedOperatorTask, restoredFromRecovery: boolean) {
    return {
      recommendationId:
        typeof task.input_data?.recommendation_id === 'string'
          ? task.input_data.recommendation_id
          : null,
      taskId: task.id,
      taskType: task.task_type,
      assignedTo: task.assigned_to,
      inputData: task.input_data || {},
      priority: task.priority,
      createdAt: task.created_at,
      restoredFromRecovery,
    }
  }

  private queueSortValue(priority: string): number {
    return priority === 'high' ? 0 : priority === 'normal' ? 1 : 2
  }

  private sortQueuedTasks(): void {
    this.queuedTasks.sort((left, right) => {
      const priorityDelta = this.queueSortValue(left.priority) - this.queueSortValue(right.priority)
      if (priorityDelta !== 0) {
        return priorityDelta
      }
      return Date.parse(left.createdAt || '') - Date.parse(right.createdAt || '')
    })
  }

  private syncState(): void {
    this.state = {
      currentTaskId: this.activeTask?.taskId || null,
      queueSize: (this.activeTask ? 1 : 0) + this.queuedTasks.length,
    }
  }

  private assignedToFor(taskType: string): string {
    return taskType === 'run_dreaming'
      ? 'dreaming_agent'
      : taskType === 'generate_review'
        ? 'post_trade_reviewer'
        : taskType === 'run_portfolio_snapshot'
          ? 'portfolio_agent'
        : taskType === 'run_strategy_design'
          ? 'strategy_designer'
          : taskType === 'run_risk_critique'
            ? 'risk_critic'
        : 'operator'
  }

  private terminalKey(recommendationId: string, fingerprint: string): string {
    return `${recommendationId}:${fingerprint}`
  }

  private fingerprintFromTask(task: { inputData: Record<string, unknown> }): string | null {
    const fingerprint = task.inputData.recommendation_fingerprint
    return typeof fingerprint === 'string' && fingerprint.length > 0 ? fingerprint : null
  }

  private rememberTerminalTask(task: { recommendationId: string | null; inputData: Record<string, unknown> }): void {
    if (!task.recommendationId) {
      return
    }
    const fingerprint = this.fingerprintFromTask(task)
    if (fingerprint) {
      this.terminalRecommendationFingerprints.add(this.terminalKey(task.recommendationId, fingerprint))
    }
  }

  private isTerminalRecommendation(recommendation: OperatorRecommendation): boolean {
    return this.terminalRecommendationFingerprints.has(
      this.terminalKey(recommendation.id, recommendationFingerprint(recommendation)),
    )
  }

  private isLegacyOpportunityQualityAudit(task: {
    recommendationId: string | null
    taskType: string
    inputData: Record<string, unknown>
  }): boolean {
    return task.recommendationId === 'opportunity-quality-audit-due'
      && task.taskType === 'pi_reasoning_session'
      && this.fingerprintFromTask(task) === null
  }

  private async supersedeLegacyOpportunityQualityBacklog(): Promise<void> {
    const output_data = {
      summary: 'Superseded legacy unversioned opportunity-quality diagnostic after fingerprint rollout.',
      superseded_by: 'recommendation_fingerprint:v1',
    }

    if (this.activeTask && this.isLegacyOpportunityQualityAudit(this.activeTask)) {
      const superseded = await this.safeUpdateTask(this.activeTask.taskId, {
        status: 'completed',
        output_data,
      })
      if (superseded !== null) {
        this.activeTask = null
      }
    }

    const retained = []
    for (const task of this.queuedTasks) {
      if (!this.isLegacyOpportunityQualityAudit(task)) {
        retained.push(task)
        continue
      }
      await this.safeUpdateTask(task.taskId, { status: 'completed', output_data })
    }
    this.queuedTasks = retained
    this.syncState()
  }

  private inputDataFor(recommendation: OperatorRecommendation): Record<string, unknown> {
    return {
      recommendation_id: recommendation.id,
      recommendation_fingerprint: recommendationFingerprint(recommendation),
      kind: recommendation.kind,
      summary: recommendation.summary,
      ...(recommendation.metadata || {}),
    }
  }

  private async createQueuedTask(recommendation: OperatorRecommendation): Promise<void> {
    const taskType = recommendation.taskType || 'diagnose_system_health'
    const assignedTo = this.assignedToFor(taskType)
    const inputData = this.inputDataFor(recommendation)
    const created = await this.backendClient.createTask({
      task_type: taskType,
      status: 'queued',
      priority: recommendation.severity === 'high' ? 'high' : 'normal',
      created_by: 'operator_loop',
      assigned_to: assignedTo,
      input_data: inputData,
    })
    this.queuedTasks.push({
      recommendationId: recommendation.id,
      taskId: created.id,
      taskType,
      assignedTo,
      inputData,
      priority: recommendation.severity === 'high' ? 'high' : 'normal',
      createdAt: null,
      restoredFromRecovery: false,
    })
    this.sortQueuedTasks()
  }

  private async promoteQueuedTask(): Promise<void> {
    this.sortQueuedTasks()
    const next = this.queuedTasks.shift()
    if (!next) {
      return
    }
    const updated = await this.safeUpdateTask(next.taskId, { status: 'running' })
    if (updated === null) {
      // Task evicted — try to promote the next one in queue
      if (this.queuedTasks.length > 0) {
        await this.promoteQueuedTask()
      }
      return
    }
    this.activeTask = {
      ...next,
      restoredFromRecovery: false,
    }
    this.syncState()
  }

  getActiveTask():
    | {
        recommendationId: string | null
        taskId: string
        taskType: string
        assignedTo: string
        inputData: Record<string, unknown>
        priority: string
        createdAt: string | null
        restoredFromRecovery: boolean
      }
    | null {
    return this.activeTask ? { ...this.activeTask } : null
  }

  getTaskForRecommendation(
    recommendationId: string,
  ):
    | {
        recommendationId: string | null
        taskId: string
        taskType: string
        assignedTo: string
        inputData: Record<string, unknown>
        priority: string
        createdAt: string | null
        restoredFromRecovery: boolean
        active: boolean
      }
    | null {
    if (this.activeTask?.recommendationId === recommendationId) {
      return {
        ...this.activeTask,
        active: true,
      }
    }

    const queuedTask = this.queuedTasks.find(task => task.recommendationId === recommendationId)
    if (!queuedTask) {
      return null
    }

    return {
      ...queuedTask,
      active: false,
    }
  }

  restore(tasks: PersistedOperatorTask[]): void {
    this.terminalRecommendationFingerprints.clear()
    for (const task of tasks) {
      if (task.status === 'completed' || task.status === 'failed') {
        this.rememberTerminalTask(this.toRuntimeTask(task, true))
      }
    }
    const active = tasks.find(task => task.status === 'running')
    const queued = tasks.filter(task => task.status === 'queued')
    if (!active) {
      this.activeTask = null
      this.queuedTasks = queued
        .map(task => this.toRuntimeTask(task, true))
      this.sortQueuedTasks()
      this.syncState()
      return
    }

    this.activeTask = this.toRuntimeTask(active, true)
    this.queuedTasks = queued
      .map(task => this.toRuntimeTask(task, true))
    this.sortQueuedTasks()
    this.syncState()
  }

  async reconcileExpiredRunningTasks(
    tasks: PersistedOperatorTask[],
    now = Date.now(),
  ): Promise<PersistedOperatorTask[]> {
    const retained: PersistedOperatorTask[] = []
    for (const task of tasks) {
      if (task.status !== 'running') {
        retained.push(task)
        continue
      }

      const startedAt = task.started_at || task.created_at
      const startedMs = Date.parse(startedAt || '')
      const expired =
        !Number.isNaN(startedMs) &&
        now - startedMs >= TaskRuntime.RUNNING_TASK_RECOVERY_TIMEOUT_MS
      if (!expired) {
        retained.push(task)
        continue
      }

      await this.safeUpdateTask(task.id, {
        status: 'cancelled',
        error: 'operator_runtime_recovery_timeout',
        output_data: {
          recovery_reason: 'running_task_expired',
          started_at: startedAt,
          timeout_minutes: TaskRuntime.RUNNING_TASK_RECOVERY_TIMEOUT_MS / (60 * 1000),
        },
      })
    }
    return retained
  }

  async completeActiveTask(output_data: Record<string, unknown>): Promise<void> {
    if (!this.activeTask) {
      return
    }
    const taskId = this.activeTask.taskId
    const completed = await this.safeUpdateTask(taskId, { status: 'completed', output_data })
    if (completed !== null) {
      this.rememberTerminalTask(this.activeTask)
    }
    this.activeTask = null
    this.syncState()
  }

  async completeTaskForRecommendation(
    recommendationId: string,
    output_data: Record<string, unknown>,
  ): Promise<boolean> {
    if (this.activeTask?.recommendationId === recommendationId) {
      await this.completeActiveTask(output_data)
      return true
    }

    const queuedIndex = this.queuedTasks.findIndex(task => task.recommendationId === recommendationId)
    if (queuedIndex === -1) {
      return false
    }

    const [task] = this.queuedTasks.splice(queuedIndex, 1)
    const completed = await this.safeUpdateTask(task.taskId, { status: 'completed', output_data })
    if (completed !== null) {
      this.rememberTerminalTask(task)
    }
    this.syncState()
    return true
  }

  async failActiveTask(error: string, output_data?: Record<string, unknown>): Promise<void> {
    if (!this.activeTask) {
      return
    }
    const taskId = this.activeTask.taskId
    const failed = await this.safeUpdateTask(taskId, { status: 'failed', error, output_data })
    if (failed !== null) {
      this.rememberTerminalTask(this.activeTask)
    }
    this.activeTask = null
    this.syncState()
  }

  async failTaskForRecommendation(
    recommendationId: string,
    error: string,
    output_data?: Record<string, unknown>,
  ): Promise<boolean> {
    if (this.activeTask?.recommendationId === recommendationId) {
      await this.failActiveTask(error, output_data)
      return true
    }

    const queuedIndex = this.queuedTasks.findIndex(task => task.recommendationId === recommendationId)
    if (queuedIndex === -1) {
      return false
    }

    const [task] = this.queuedTasks.splice(queuedIndex, 1)
    const failed = await this.safeUpdateTask(task.taskId, { status: 'failed', error, output_data })
    if (failed !== null) {
      this.rememberTerminalTask(task)
    }
    this.syncState()
    return true
  }

  async sync(recommendations: OperatorRecommendation[]): Promise<void> {
    // Versions before recommendation_fingerprint created an endless queued/running
    // opportunity-quality backlog. They cannot be attributed to a specific current
    // evidence set, so retire them once and let at most one v1 task represent now.
    await this.supersedeLegacyOpportunityQualityBacklog()
    const actionable = recommendations.filter(item => item.taskType)
    const unseenActionable = actionable.filter(item => !this.isTerminalRecommendation(item))
    const representedRecommendationIds = new Set(
      [this.activeTask?.recommendationId || null, ...this.queuedTasks.map(task => task.recommendationId)].filter(
        (value): value is string => typeof value === 'string' && value.length > 0,
      ),
    )

    if (this.activeTask?.restoredFromRecovery) {
      this.activeTask = {
        ...this.activeTask,
        restoredFromRecovery: false,
      }
      for (const recommendation of unseenActionable) {
        if (!representedRecommendationIds.has(recommendation.id)) {
          await this.createQueuedTask(recommendation)
          representedRecommendationIds.add(recommendation.id)
        }
      }
      this.syncState()
      return
    }

    const activeRecommendationStillActionable =
      this.activeTask?.recommendationId != null &&
      actionable.some(item => item.id === this.activeTask?.recommendationId)

    if (!activeRecommendationStillActionable && this.activeTask && actionable.length === 0) {
      await this.completeActiveTask({
        summary: 'Condition cleared; returning to passive observation.',
      })
      if (this.queuedTasks.length > 0) {
        await this.promoteQueuedTask()
      }
      return
    }

    if (!activeRecommendationStillActionable && this.activeTask && actionable.length > 0) {
      await this.completeActiveTask({
        summary: 'Superseded by a newer operator diagnostic.',
      })
    }

    if (!this.activeTask && this.queuedTasks.length > 0) {
      await this.promoteQueuedTask()
    }

    if (!this.activeTask && unseenActionable.length === 0) {
      this.syncState()
      return
    }

    if (!this.activeTask && unseenActionable.length > 0) {
      const first = unseenActionable[0]
      const taskType = first.taskType || 'diagnose_system_health'
      const assignedTo = this.assignedToFor(taskType)
      const inputData = this.inputDataFor(first)
      const created = await this.backendClient.createTask({
        task_type: taskType,
        status: 'running',
        priority: first.severity === 'high' ? 'high' : 'normal',
        created_by: 'operator_loop',
        assigned_to: assignedTo,
        input_data: inputData,
      })
      this.activeTask = {
        recommendationId: first.id,
        taskId: created.id,
        taskType,
        assignedTo,
        inputData,
        priority: first.severity === 'high' ? 'high' : 'normal',
        createdAt: null,
        restoredFromRecovery: false,
      }
      representedRecommendationIds.add(first.id)
    }

    for (const recommendation of unseenActionable) {
      if (recommendation.id === this.activeTask?.recommendationId) {
        continue
      }
      if (representedRecommendationIds.has(recommendation.id)) {
        continue
      }
      await this.createQueuedTask(recommendation)
      representedRecommendationIds.add(recommendation.id)
    }

    this.syncState()
  }
}
